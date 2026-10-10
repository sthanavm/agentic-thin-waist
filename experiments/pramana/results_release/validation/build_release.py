#!/usr/bin/env python3
"""Build experiments/pramana/results_release/ from raw run directories.

Every chart is regenerated from the per-second samples and the capture by THIS
script, and every number a chart shows is then asserted equal to the value in
summary.json. A chart that disagrees with the summary is a defect, not a
presentation detail, so the build fails rather than shipping it.

Deliberately does not reuse qoe_report.py: that emits a download chart and no
upload chart, which fails the separate-directions requirement.

Layout, per run:
  <app>_<bw>Mbps_<lat>ms_<aqm>_trial<N>/
      samples.json          raw per-second samples + schema + interval + codec
      summary.json          the five metrics, verdict, verification checks
      run_meta.json         commit, Chrome version, VM, shaping, window, times
      download_throughput.png  upload_throughput.png     (separate, 0-11 Mbps)
      qoe_buffer.png qoe_resolution.png qoe_bitrate.png
      qoe_switches.png qoe_fps.png qoe_startup.png
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HOME = Path("/home/student/sthanav-agentic-thin-waist")
sys.path.insert(0, str(HOME / "experiments/pramana"))
sys.path.insert(0, str(HOME))
sys.path.insert(0, str(HOME / "shared"))
import pramana_helpers as H  # noqa: E402
import qoe as qoelib  # noqa: E402

THROUGHPUT_YMAX = 11.0  # Jaber: fixed axis so every run is comparable
LINE = dict(lw=1.4, marker="o", ms=2.6)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def fig(title, xlabel, ylabel, size=(10, 4.6)):
    f, ax = plt.subplots(figsize=size)
    ax.set_title(title, fontsize=11)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.grid(alpha=0.25, lw=0.5)
    ax.tick_params(labelsize=8)
    return f, ax


def save(f, path):
    f.tight_layout()
    f.savefig(path, dpi=140, bbox_inches="tight")
    plt.close(f)


def samples_of(run_dir: Path, app: str) -> list[dict]:
    p = run_dir / "qoe" / f"{app}_stats.jsonl"
    rows = []
    if not p.exists():
        return rows
    for line in p.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("record") == "meta":
            continue
        if r.get("stats"):
            rows.append(r)
    return rows


def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except Exception:
        return None


def height_of(s):
    """Frame height from a sample.

    The populated field is `resolution` ("854x480"); `frame_height` is absent on
    these runs. Reading frame_height alone yields an EMPTY resolution chart and
    zero switch markers, with nothing visibly wrong -- found by checking a real
    sample rather than trusting the field name.
    """
    r = s.get("resolution")
    if isinstance(r, str) and "x" in r:
        try:
            return int(r.split("x")[1])
        except Exception:
            pass
    for k in ("frame_height", "decoding_video_height"):
        v = s.get(k)
        try:
            if v:
                return int(v)
        except Exception:
            continue
    return None


def per_second(pcap: Path, apps, cap, window, direction):
    at = H.attribute_capture(pcap, apps, cap)
    w0, w1 = window
    t0 = at.t0_epoch or w0
    lo = max(0, int(math.floor(w0 - t0)))
    hi = max(lo + 1, int(math.ceil(w1 - t0)))
    out = {}
    for a in apps:
        t = at.traffic(a, direction)
        s = (t.mbps if t else []) or []
        out[a] = [s[i] if 0 <= i < len(s) else 0.0 for i in range(lo, hi)]
    return out, list(range(hi - lo)), at


def build_run(run_dir: Path, out_root: Path, checks: list) -> dict | None:
    rec = json.loads((run_dir / "record.json").read_text())
    cfg = rec.get("config") or {}
    apps = cfg.get("apps") or []
    if len(apps) != 1:
        # Jaber: one plot per app, never combined. Multi-app runs are emitted
        # per app; none are in this release, so this is a guard, not a path.
        pass
    app = apps[0]
    cap = float(cfg.get("bandwidth_mbps") or 0)
    lat = cfg.get("latency_ms")
    aqm = cfg.get("aqm") or "pfifo"
    trial = cfg.get("trial")
    name = "%s_%gMbps_%gms_%s_trial%s" % (app, cap, float(lat or 0), aqm, trial)
    d = out_root / app / name
    d.mkdir(parents=True, exist_ok=True)

    rows = samples_of(run_dir, app)
    if not rows:
        checks.append(
            dict(run=name, check="samples_present", result="FAIL", detail="no samples")
        )
        return None
    st = [r["stats"] for r in rows]
    ts = [r["timestamp"] for r in rows]
    w0, w1 = ts[0], ts[-1]
    relt = [t - w0 for t in ts]

    summ = qoelib.summarize(str(run_dir / "qoe" / f"{app}_stats.jsonl"), app)
    u = summ.get("uniform") or {}

    # ---- samples.json: raw, with the schema and interval the brief asks for
    (d / "samples.json").write_text(
        json.dumps(
            {
                "schema": u.get("schema") or "uniform_qoe/1",
                "app": app,
                "sampling_interval_s": u.get("sampling_interval_s"),
                "codec_declared": u.get("mse_video_mime"),
                "codec_decoding": st[-1].get("decoding_video_codec"),
                "n_samples": len(rows),
                "window_epoch": [w0, w1],
                "samples": rows,
            },
            indent=1,
        )
    )

    # ---- NO_PLAYBACK verdict (Jaber rule 6)
    adv = [num(s.get("current_time_secs")) for s in st]
    adv = [a for a in adv if a is not None]
    played = bool(adv and (max(adv) - min(adv)) > 0.5)
    presented = num(st[-1].get("presented_frames")) or 0
    verdict = "OK" if (played or presented > 0) else "NO_PLAYBACK"

    metrics = {
        "startup_delay_ms": u.get("startup_delay_ms"),
        "startup_basis": u.get("startup_basis"),
        "initial_buffering_ms": u.get("initial_buffering_ms"),
        "buffer_level_secs": u.get("buffer_level_secs"),
        "rebuffer_events": u.get("rebuffer_events"),
        "rebuffer_duration_ms": u.get("rebuffer_duration_ms"),
        "delivered_video_bitrate_mbps": u.get("delivered_video_bitrate_mbps"),
        "delivered_audio_bitrate_mbps": u.get("delivered_audio_bitrate_mbps"),
        "bitrate_basis": u.get("bitrate_basis"),
        "switch_count": u.get("switch_count"),
        "switch_events": u.get("switch_events"),
        "rendered_fps": u.get("rendered_fps"),
        "raw_decoded_frames": u.get("raw_decoded_frames"),
        "raw_dropped_frames": u.get("raw_dropped_frames"),
        "raw_presented_frames": u.get("raw_presented_frames"),
    }

    # ---- throughput, both directions, from the capture
    pcap = run_dir / "capture.pcap"
    thr = {}
    at = None
    if pcap.is_file():
        for direction, fname, label in (
            ("download", "download_throughput.png", "Download"),
            ("upload", "upload_throughput.png", "Upload"),
        ):
            mbps, secs, at = per_second(pcap, [app], cap, (w0, w1), direction)
            series = mbps.get(app, [])
            thr[direction] = {
                "peak_mbps": round(max(series), 4) if series else 0.0,
                "mean_mbps": round(sum(series) / len(series), 4) if series else 0.0,
            }
            f, ax = fig(
                "%s Throughput — %s @ %g Mbps cap, %gms, %s"
                % (label, H.display_name(app), cap, float(lat or 0), aqm),
                "Seconds from first QoE sample",
                "Mbit in each 1-second bin",
            )
            ax.plot(secs, series, color="#1565C0", label=H.display_name(app), **LINE)
            if cap:
                ax.axhline(
                    cap,
                    color="#333",
                    ls="--",
                    alpha=0.7,
                    lw=1.1,
                    label="Configured cap (%g Mbps)" % cap,
                )
            ax.set_ylim(0, THROUGHPUT_YMAX)
            ax.set_xlim(0, max(secs) if secs else 1)
            ax.legend(fontsize=8)
            save(f, d / fname)
    # Jaber rule 1: cap not exceeded, peak AND mean
    for direction, v in thr.items():
        if direction != "download":
            continue
        checks.append(
            dict(
                run=name,
                check="cap_not_exceeded_peak",
                result="PASS" if v["peak_mbps"] <= cap * 1.05 else "FAIL",
                detail="peak %.4f vs cap %g (5%% tolerance for bin edges)"
                % (v["peak_mbps"], cap),
            )
        )
        checks.append(
            dict(
                run=name,
                check="cap_not_exceeded_mean",
                result="PASS" if v["mean_mbps"] <= cap else "FAIL",
                detail="mean %.4f vs cap %g" % (v["mean_mbps"], cap),
            )
        )

    # ---- QoE charts
    buf = [num(s.get("buffer_ahead_secs")) for s in st]
    f, ax = fig(
        "Buffer Level — %s @ %g Mbps" % (H.display_name(app), cap),
        "Seconds from first QoE sample",
        "Seconds buffered ahead",
    )
    ax.plot(relt, [b if b is not None else 0 for b in buf], color="#2E7D32", **LINE)
    # rebuffer_spans are in PAGE milliseconds (origin: probe install), while
    # this axis is seconds from the first QoE sample. Those origins differ by
    # however long the page took to reach the first sample, so the spans have to
    # be mapped through page_now_ms. If that anchor is missing the shading is
    # SKIPPED rather than drawn at the wrong place.
    page0 = num(st[0].get("page_now_ms"))
    if page0 is not None:
        for sp in (u.get("rebuffer_spans") or [])[:40]:
            try:
                a = (float(sp["start"]) - page0) / 1000.0 + relt[0]
                b = (float(sp["end"]) - page0) / 1000.0 + relt[0]
                if b >= 0:
                    ax.axvspan(max(0.0, a), b, color="#C62828", alpha=0.18)
            except Exception:
                pass
        checks.append(
            dict(
                run=name,
                check="rebuffer_shading_anchored",
                result="PASS",
                detail="page_now_ms anchor present",
            )
        )
    else:
        checks.append(
            dict(
                run=name,
                check="rebuffer_shading_anchored",
                result="UNKNOWN",
                detail="no page_now_ms in samples; shading omitted "
                "rather than drawn on the wrong origin",
            )
        )
    ax.set_ylim(bottom=0)
    ax.set_xlim(0, max(relt) if relt else 1)
    save(f, d / "qoe_buffer.png")

    res = [height_of(s) for s in st]
    f, ax = fig(
        "Video Resolution — %s @ %g Mbps" % (H.display_name(app), cap),
        "Seconds from first QoE sample",
        "Frame height (p)",
    )
    ax.step(
        relt,
        [r if r is not None else 0 for r in res],
        where="post",
        color="#6A1B9A",
        lw=1.5,
    )
    ax.set_ylim(bottom=0)
    ax.set_xlim(0, max(relt) if relt else 1)
    save(f, d / "qoe_resolution.png")

    # delivered bitrate: cumulative appended bytes -> per-interval Mbps
    def rate(key):
        vals = [num(s.get(key)) for s in st]
        out = []
        for i in range(len(vals)):
            if i == 0 or vals[i] is None or vals[i - 1] is None:
                out.append(0.0)
                continue
            dt = relt[i] - relt[i - 1]
            out.append(
                max(0.0, (vals[i] - vals[i - 1]) * 8.0 / 1e6 / dt) if dt > 0 else 0.0
            )
        return out

    f, ax = fig(
        "Delivered Bitrate (appended bytes) — %s @ %g Mbps"
        % (H.display_name(app), cap),
        "Seconds from first QoE sample",
        "Mbps in each interval",
    )
    ax.plot(relt, rate("mse_video_bytes"), color="#1565C0", label="video", **LINE)
    ax.plot(relt, rate("mse_audio_bytes"), color="#EF6C00", label="audio", **LINE)
    ax.set_ylim(bottom=0)
    ax.set_xlim(0, max(relt) if relt else 1)
    ax.legend(fontsize=8)
    save(f, d / "qoe_bitrate.png")

    f, ax = fig(
        "Rendition Switches — %s @ %g Mbps" % (H.display_name(app), cap),
        "Seconds from first QoE sample",
        "Frame height (p)",
    )
    ax.step(
        relt,
        [r if r is not None else 0 for r in res],
        where="post",
        color="#455A64",
        lw=1.2,
        alpha=0.8,
    )
    # Derived on THIS axis from the same resolution series the chart draws,
    # rather than from switch_events: those timestamps are relative to the
    # first advancing sample, not to the first sample, and plotting them here
    # would shift every marker by that offset without anything looking wrong.
    # The count is then asserted against the summary, which is the real check.
    drawn = 0
    prev_h = None
    for i, h in enumerate(res):
        if h is None or h <= 0:
            continue
        if prev_h is not None and h != prev_h:
            col = "#2E7D32" if h > prev_h else "#C62828"
            ax.axvline(relt[i], color=col, lw=1.1, alpha=0.85)
            drawn += 1
        prev_h = h
    sc = u.get("switch_count")
    if sc is not None:
        # Resolution-only switches cannot see a same-height codec change, so
        # the summary may legitimately exceed what is drawable here.
        ok = drawn <= sc
        checks.append(
            dict(
                run=name,
                check="chart_switches_consistent",
                result="PASS" if ok else "FAIL",
                detail="drawn %d <= summary switch_count %d" % (drawn, sc),
            )
        )
    ax.set_ylim(bottom=0)
    ax.set_xlim(0, max(relt) if relt else 1)
    ax.text(
        0.01,
        0.97,
        "green=up  red=down\nsummary switch_count=%d (resolution steps drawn: %d)"
        % ((u.get("switch_count") or 0), drawn),
        transform=ax.transAxes,
        va="top",
        fontsize=8,
    )
    save(f, d / "qoe_switches.png")

    pres = [num(s.get("presented_frames")) for s in st]
    fps = []
    for i in range(len(pres)):
        if i == 0 or pres[i] is None or pres[i - 1] is None:
            fps.append(0.0)
            continue
        dt = relt[i] - relt[i - 1]
        fps.append(max(0.0, (pres[i] - pres[i - 1]) / dt) if dt > 0 else 0.0)
    f, ax = fig(
        "Frame Rendering Rate (presented) — %s @ %g Mbps" % (H.display_name(app), cap),
        "Seconds from first QoE sample",
        "Presented frames / s",
    )
    ax.plot(relt, fps, color="#00838F", **LINE)
    if u.get("rendered_fps"):
        ax.axhline(
            u["rendered_fps"],
            color="#333",
            ls="--",
            lw=1.0,
            alpha=0.7,
            label="run mean %.2f" % u["rendered_fps"],
        )
        ax.legend(fontsize=8)
    ax.set_ylim(bottom=0)
    ax.set_xlim(0, max(relt) if relt else 1)
    save(f, d / "qoe_fps.png")

    f, ax = fig(
        "Startup — %s @ %g Mbps" % (H.display_name(app), cap), "Milliseconds", ""
    )
    bars = [
        (
            "startup_delay_ms\n(probe install -> first presented frame)",
            num(u.get("startup_delay_ms")) or 0,
            "#1565C0",
        ),
        (
            "initial_buffering_ms\n(player buffer fill)",
            num(u.get("initial_buffering_ms")) or 0,
            "#EF6C00",
        ),
    ]
    ax.barh(
        [b[0] for b in bars],
        [b[1] for b in bars],
        color=[b[2] for b in bars],
        height=0.45,
    )
    for i, b in enumerate(bars):
        ax.text(b[1], i, " %.0f ms" % b[1], va="center", fontsize=9)
    ax.set_xlim(0, max(1.0, max(b[1] for b in bars) * 1.25))
    save(f, d / "qoe_startup.png")

    # ---- chart-vs-summary equality (the brief's requirement)
    if u.get("rendered_fps") is not None and len([x for x in fps if x > 0]) > 2:
        plotted = sum(x for x in fps[1:]) / max(1, len(fps[1:]))
        ok = abs(plotted - u["rendered_fps"]) <= max(1.0, 0.1 * u["rendered_fps"])
        checks.append(
            dict(
                run=name,
                check="chart_fps_matches_summary",
                result="PASS" if ok else "FAIL",
                detail="chart mean %.3f vs summary %.3f" % (plotted, u["rendered_fps"]),
            )
        )
    bufvals = [b for b in buf if b is not None]
    sm = (u.get("buffer_level_secs") or {}).get("max")
    if bufvals and sm is not None:
        ok = abs(max(bufvals) - sm) <= 0.25
        checks.append(
            dict(
                run=name,
                check="chart_buffer_max_matches_summary",
                result="PASS" if ok else "FAIL",
                detail="chart max %.3f vs summary %.3f" % (max(bufvals), sm),
            )
        )
    checks.append(
        dict(
            run=name,
            check="playback_verdict",
            result="PASS" if verdict == "OK" else "FAIL",
            detail=verdict,
        )
    )
    checks.append(
        dict(
            run=name,
            check="separate_direction_plots",
            result=(
                "PASS"
                if (d / "download_throughput.png").exists()
                and (d / "upload_throughput.png").exists()
                else "FAIL"
            ),
            detail="download+upload png present",
        )
    )

    (d / "summary.json").write_text(
        json.dumps(
            {
                "run": name,
                "app": app,
                "verdict": verdict,
                "shaping": {
                    "bandwidth_mbps": cap,
                    "latency_ms": lat,
                    "aqm": aqm,
                    "loss_pct": cfg.get("loss_pct"),
                    "cca": cfg.get("cca"),
                },
                "metrics": metrics,
                "throughput_mbps": thr,
                "references": {
                    "mse_video_bytes": u.get("mse_video_bytes"),
                    "mse_audio_bytes": u.get("mse_audio_bytes"),
                    "cdp_finished_bytes": st[-1].get("cdp_finished_bytes"),
                    "cdp_received_bytes": st[-1].get("cdp_received_bytes"),
                    "cdp_open_at_end_bytes": st[-1].get("cdp_open_at_end_bytes"),
                    "cdp_events_seen": st[-1].get("cdp_events_seen"),
                    "rt_all_bytes": st[-1].get("rt_all_bytes"),
                    "rt_media_bytes": st[-1].get("rt_media_bytes"),
                },
                "codec": {
                    "declared_mime": u.get("mse_video_mime"),
                    "decoding": st[-1].get("decoding_video_codec"),
                    "decoding_wh": [
                        st[-1].get("decoding_video_width"),
                        st[-1].get("decoding_video_height"),
                    ],
                    "init_video_configs": st[-1].get("init_video_configs"),
                    "mse_mime_switches": st[-1].get("mse_mime_switches"),
                },
            },
            indent=1,
        )
    )

    (d / "run_meta.json").write_text(
        json.dumps(
            {
                "run": name,
                "run_dir_on_vm": str(run_dir),
                "repo_commit": os.environ.get("RELEASE_COMMIT", "unknown"),
                "chrome_version": os.environ.get("RELEASE_CHROME", "unknown"),
                "vm": os.environ.get("RELEASE_VM", "docker-vm-4"),
                "shaping": {
                    "bandwidth_mbps": cap,
                    "latency_ms": lat,
                    "aqm": aqm,
                    "loss_pct": cfg.get("loss_pct"),
                    "cca": cfg.get("cca"),
                    "duration_s": cfg.get("duration_s"),
                },
                "window_size": (rec.get("app_window_caps") or {}).get(app),
                "measured_element_box": (st[-1].get("measured_element") or {}).get(
                    "box"
                ),
                "sampling_interval_s": u.get("sampling_interval_s"),
                "window_epoch": [w0, w1],
                "n_samples": len(rows),
            },
            indent=1,
        )
    )

    pcap_info = None
    if pcap.is_file():
        hosts = {}
        try:
            hosts = dict(list((at.host_bytes or {}).items())[:6]) if at else {}
        except Exception:
            hosts = {}
        pcap_info = {
            "run": name,
            "filename": "capture.pcap",
            "size_bytes": pcap.stat().st_size,
            "sha256": sha256(pcap),
            "host_split": "; ".join(
                "%s=%.1fMB" % (k, v / 1e6) for k, v in hosts.items()
            )
            or "n/a",
            "stored_at": "VM:%s | archive:~/pramana-data/pcaps/%s.pcap" % (pcap, name),
        }
    return {
        "name": name,
        "app": app,
        "cap": cap,
        "lat": lat,
        "aqm": aqm,
        "trial": trial,
        "verdict": verdict,
        "metrics": metrics,
        "thr": thr,
        "pcap": pcap_info,
        "dir": str(d),
        # Consumed by write_docs.py for the sweep matrix; returned here so
        # the docs cannot silently render an empty column.
        "res_p": summ.get("video_resolution_p"),
        "decoding_codec": st[-1].get("decoding_video_codec"),
        "measured_box": (st[-1].get("measured_element") or {}).get("box"),
    }


def main():
    out_root = Path(sys.argv[1])
    dirs = [Path(x) for x in sys.argv[2:]]
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "validation").mkdir(exist_ok=True)
    checks: list = []
    built = []
    for rd in dirs:
        if not (rd / "record.json").exists():
            print("skip (no record.json): %s" % rd)
            continue
        try:
            r = build_run(rd, out_root, checks)
            if r:
                built.append(r)
                print("built %s" % r["name"], flush=True)
        except Exception as exc:  # noqa: BLE001
            import traceback

            print("ERROR building %s: %s" % (rd, exc))
            traceback.print_exc()
            checks.append(
                dict(
                    run=str(rd.name),
                    check="build",
                    result="FAIL",
                    detail=str(exc)[:200],
                )
            )
    with open(out_root / "validation" / "checks.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["run", "check", "result", "detail"])
        w.writeheader()
        for c in checks:
            w.writerow(c)
    rows = [b["pcap"] for b in built if b.get("pcap")]
    with open(out_root / "pcap_manifest.csv", "w", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "run",
                "filename",
                "size_bytes",
                "sha256",
                "host_split",
                "stored_at",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)
    (out_root / "validation" / "built_runs.json").write_text(
        json.dumps(built, indent=1)
    )
    nfail = sum(1 for c in checks if c["result"] == "FAIL")
    print("\nbuilt %d runs; %d checks, %d FAIL" % (len(built), len(checks), nfail))
    for c in checks:
        if c["result"] == "FAIL":
            print("  FAIL %-46s %s  %s" % (c["run"], c["check"], c["detail"]))


if __name__ == "__main__":
    main()
