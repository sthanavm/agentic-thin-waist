#!/usr/bin/env python3
"""Run Zoom cells in batches that fit inside a free Zoom meeting's 40 minutes.

Why this exists rather than looping run_direct: a free meeting gives one hard
40-minute budget, and three things previously wasted it.

  * The capture split and plots took 465-574s per cell on this 2-vCPU host and
    ran between cells, inside the window, for work that needs no meeting. They
    are deferred here (PRAMANA_DEFER_ANALYSIS=1) and finished afterwards with
    replot_run(), which measured 2-3s on the same data.
  * A cell measuring the wrong <video> consumed a full 180s before anyone knew.
    The collector now aborts such a cell within ~20s, and this runner records it
    as incomplete rather than keeping it.
  * A cell was started with no time left to finish it, and another ran against a
    meeting that had already ended. Both are now refused up front.

Every decision is printed, so the log is the audit trail.
"""

import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(
    0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana")
)
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))

os.environ.setdefault("PRAMANA_CAPTURE_OVERHEAD_S", "120")
os.environ.setdefault("PRAMANA_PLOT_YMAX", "11")
os.environ["PRAMANA_DEFER_ANALYSIS"] = "1"

import pramana_helpers as H  # noqa: E402

MEETING_LIMIT_S = float(os.environ.get("MEETING_LIMIT_S", 40 * 60))
# Refuse a cell unless this much is left: the call itself, a setup allowance, and
# a 3-minute reserve. Setup measured 68.9-101.5s across the last sweep, so the
# allowance is the observed maximum rounded up, not an average.
SETUP_ALLOWANCE_S = float(os.environ.get("SETUP_ALLOWANCE_S", "110"))
RESERVE_S = float(os.environ.get("RESERVE_S", "180"))
TEARDOWN_ALLOWANCE_S = float(os.environ.get("TEARDOWN_ALLOWANCE_S", "60"))
CLIP = os.path.expanduser("~/pramana-assets/bbb_720p24_10s.y4m")
SAMPLER = "/tmp/cpu_sampler.py"
QOE_KEYS = (
    "status",
    "video_resolution_p",
    "frame_rate_fps",
    "dropped_frame_pct",
    "rebuffer_events",
    "watched_seconds",
    "total_samples",
    "mean_bitrate_mbps",
)


def say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def peer_state() -> str:
    """Last status word the peer printed; 'stalled' means its tx bytes died."""
    try:
        r = subprocess.run(
            ["docker", "logs", "--tail", "4", "pramana-peer-zoom"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        for line in reversed((r.stdout + r.stderr).splitlines()):
            if "[bot:" in line:
                parts = line.split("]", 1)[1].split()
                if parts:
                    return parts[0]
    except Exception:
        pass
    return "unknown"


def samples_of(run_dir: str, app: str) -> list[dict]:
    p = pathlib.Path(run_dir) / "qoe" / f"{app}_stats.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip() or '"meta"' in line:
            continue
        try:
            st = (json.loads(line) or {}).get("stats") or {}
        except Exception:
            continue
        if st:
            out.append(st)
    return out


def tile_report(run_dir: str, app: str) -> dict:
    """Was the REMOTE peer's main tile measured, and did its frames advance?

    The old expiry check asked "is any Zoom tile live", which the collector's own
    local self-view satisfies forever - a dead meeting still passed it. Both
    halves here are required: the element must be remote and main-view sized, and
    its decoded-frame counter must actually move.
    """
    st = samples_of(run_dir, app)
    if not st:
        return {"ok": False, "why": "no samples"}
    local = sum(1 for s in st if s.get("measured_is_local") is True)
    areas = [s.get("measured_box_area") or 0 for s in st]
    me = next(
        (s.get("measured_element") for s in reversed(st) if s.get("measured_element")),
        {},
    )
    frames = [
        s.get("total_video_frames")
        for s in st
        if s.get("total_video_frames") is not None
    ]
    advanced = (max(frames) - min(frames)) if len(frames) > 1 else 0
    floor = int(1756 * 988 * 0.35)
    ok = local == 0 and max(areas or [0]) >= floor and advanced > 0
    why = []
    if local:
        why.append(f"local self-view in {local}/{len(st)} samples")
    if max(areas or [0]) < floor:
        why.append(f"tile too small (max area {max(areas or [0])} < {floor})")
    if advanced <= 0:
        why.append("decoded-frame counter never advanced")
    return {
        "ok": ok,
        "why": "; ".join(why) or "remote main tile, frames advancing",
        "measured": f"{me.get('stream')}@{me.get('box')}" if me else None,
        "origin": me.get("origin") if me else None,
        "frames_advanced": advanced,
        "local_samples": local,
        "samples": len(st),
    }


def cpu_digest(path: pathlib.Path) -> dict:
    """Mean/peak CPU per bucket, ignoring the idle infrastructure containers."""
    if not path.exists():
        return {}
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("record") == "meta" or "cpu_pct" not in r:
            continue
        rows.append(r)
    if not rows:
        return {}
    keep = ("peer_chrome", "collector_chrome_zoom", "collector_chrome_vimeo")
    out = {}
    for k in keep:
        vals = [r["cpu_pct"].get(k, 0.0) for r in rows]
        if any(vals):
            out[k] = {
                "mean_pct_of_core": round(sum(vals) / len(vals), 1),
                "peak_pct_of_core": round(max(vals), 1),
            }
    tot = [r.get("cpu_pct_total_of_host", 0.0) for r in rows]
    out["host_total_pct"] = {
        "mean": round(sum(tot) / len(tot), 1),
        "peak": round(max(tot), 1),
    }
    out["samples"] = len(rows)
    return out


def run_cell(apps, bw, lat, dur, room, ckpt_path, state):
    tag = f"{'+'.join(apps)}_{bw}M_{lat}ms"
    kw = {}
    if "zoom" in apps:
        kw["app_urls"] = {"zoom": room}
        kw["peers"] = {"zoom": {"join_url": room, "name": "pramana-peer", "y4m": CLIP}}
        kw["peer_wait_s"] = 300
    if "vimeo" in apps:
        kw["app_block_codecs"] = {"vimeo": ["av01"]}
    cfg = H.ExperimentConfig(
        apps=list(apps),
        bandwidth_mbps=bw,
        upload_mbps=bw,
        latency_ms=lat,
        loss_pct=0,
        aqm="pfifo",
        buffer_packets=1000,
        cca="cubic",
        duration_s=dur,
        trial=1,
        tag=f"sweep_{tag}",
        **kw,
    )
    # CPU sampler spans setup + call so the decode load during the call is
    # attributable to a process rather than inferred from QoE numbers.
    cpu_path = pathlib.Path(f"/tmp/cpu_{tag}_{int(time.time())}.jsonl")
    dmap = {"99": apps[0], "100": apps[1]} if len(apps) > 1 else {"99": apps[0]}
    sampler = None
    try:
        sampler = subprocess.Popen(
            [
                "sudo",
                "-n",
                "python3",
                SAMPLER,
                str(cpu_path),
                str(dur + SETUP_ALLOWANCE_S + 60),
                "5",
                json.dumps(dmap),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        say(f"  ! cpu sampler not started ({type(exc).__name__}); continuing")
    try:
        rec = H.run_direct(cfg)
    except Exception as exc:
        say(f"CELL_FAILED {tag} {type(exc).__name__}: {str(exc)[:200]}")
        state["incomplete"].append(
            {"cell": tag, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}
        )
        return None
    finally:
        if sampler:
            try:
                sampler.wait(timeout=90)
            except Exception:
                sampler.kill()
    rd = (rec.get("artifacts") or {}).get("dir") or ""
    pq = rec.get("player_qoe") or {}
    out = {a: {k: (pq.get(a) or {}).get(k) for k in QOE_KEYS} for a in apps}
    say(f"RESULT {tag} {json.dumps(out)}")
    cpu = cpu_digest(cpu_path)
    if cpu:
        say(f"CPU {tag} {json.dumps(cpu)}")
        try:
            (pathlib.Path(rd) / "cpu_per_process.jsonl").write_bytes(
                cpu_path.read_bytes()
            )
        except Exception:
            pass
    tiles = {}
    if "zoom" in apps:
        tiles = tile_report(rd, "zoom")
        say(f"TILE {tag} {json.dumps(tiles)}")
    say(f"RUNDIR {tag} {rd}")
    entry = {
        "cell": tag,
        "bw": bw,
        "lat": lat,
        "apps": list(apps),
        "run_dir": rd,
        "qoe": out,
        "cpu": cpu,
        "tile": tiles,
    }
    if "zoom" in apps and not tiles.get("ok"):
        say(f"CELL_INVALID {tag}: {tiles.get('why')} - discarding")
        try:
            subprocess.run(["rm", "-rf", rd], timeout=60)
        except Exception:
            pass
        entry["discarded"] = True
        state["incomplete"].append({"cell": tag, "reason": tiles.get("why")})
    else:
        state["done"].append(entry)
    ckpt_path.write_text(json.dumps(state, indent=2))
    return entry


def main() -> int:
    room_raw = sys.argv[1]
    start_epoch = float(sys.argv[2])
    cells = json.loads(sys.argv[3])  # [[apps, bw, lat], ...]
    dur = int(os.environ.get("DUR", "180"))
    ckpt_path = pathlib.Path(os.environ.get("CKPT", "/tmp/sweep_checkpoint.json"))

    try:
        from shared import apps as A

        room = A.normalize_join_url("zoom", room_raw)
    except Exception:
        room = room_raw
    if "/j/" in room:
        say(
            "REFUSING: join URL did not normalise to /wc/ form; peer would sit knocking"
        )
        return 2
    say(f"room normalised: {room}")

    state = {"done": [], "incomplete": [], "remaining": cells}
    if ckpt_path.exists():
        try:
            prev = json.loads(ckpt_path.read_text())
            state["done"] = prev.get("done", [])
            state["incomplete"] = prev.get("incomplete", [])
            done_tags = {d["cell"] for d in state["done"]}
            cells = [
                c
                for c in cells
                if f"{'+'.join(c[0])}_{c[1]}M_{c[2]}ms" not in done_tags
            ]
            if done_tags:
                say(f"resuming; already done: {sorted(done_tags)}")
        except Exception:
            pass

    for idx, (apps, bw, lat) in enumerate(cells):
        tag = f"{'+'.join(apps)}_{bw}M_{lat}ms"
        elapsed = time.time() - start_epoch
        remaining = MEETING_LIMIT_S - elapsed
        need = dur + SETUP_ALLOWANCE_S + TEARDOWN_ALLOWANCE_S + RESERVE_S
        say(
            f"--- next {tag} ({'+'.join(apps)}): {remaining:.0f}s left, need {need:.0f}s"
        )
        if remaining < need:
            say(f"STOP_WINDOW {tag} does not fit ({remaining:.0f}s < {need:.0f}s)")
            state["remaining"] = [list(c) for c in cells[idx:]]
            ckpt_path.write_text(json.dumps(state, indent=2))
            break
        ps = peer_state()
        if "zoom" in apps and ps != "publishing":
            say(f"STOP_PEER peer is '{ps}', not publishing - meeting likely ended")
            state["remaining"] = [list(c) for c in cells[idx:]]
            ckpt_path.write_text(json.dumps(state, indent=2))
            break
        say(f"=== RUN {'+'.join(apps)} bw={bw}Mbps lat={lat}ms (peer={ps}) ===")
        run_cell(apps, bw, lat, dur, room, ckpt_path, state)
    else:
        state["remaining"] = []
        ckpt_path.write_text(json.dumps(state, indent=2))

    say(
        f"CHECKPOINT done={[d['cell'] for d in state['done']]} "
        f"incomplete={[i['cell'] for i in state['incomplete']]} "
        f"remaining={[f'{chr(43).join(c[0])}_{c[1]}M_{c[2]}ms' for c in state.get('remaining', [])]}"
    )
    say("SWEEP_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
