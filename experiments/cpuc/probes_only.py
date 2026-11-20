"""Probe-only CTP runs: IRTT and NetVibe on the shaped link, no application.

Same shaping, capture and CTP replay as the Vimeo CTP runs (PR #29), with the
app replaced by the two link probes. `vimeo.py --ctp-only` still plays Vimeo;
this plays nothing, so whatever the probes measure is the link plus the CTP.

What is reused rather than rewritten:
  * conditions.CTP_CONDITION and ctp_plan()  -- 10/10 Mb/s, 100 ms, pfifo, 0 loss
  * ctp.random_profiles() / load_profiles()  -- profile selection from manifest.csv
  * netns.assert_ctp_mount(), start_ctp_replay() -- tcpreplay-edit in ns2:veth3,
    PNAT 169.231.0.0/16 -> 172.16.1.20, looped
  * pramana_helpers                          -- /shape, captures, the worker's
    /irtt and /netvibe endpoints, summarize_irtt/summarize_netvibe, and
    attribute_capture with extra_probe_endpoint_ips

What is added, and why:
  * an explicit wait for the measurement window between starting and stopping
    the probes. run_direct() has none: with no app it would stop the probes
    seconds after starting them.
  * the bench SQLite is copied out of the worker before summarize_netvibe()
    reads it; through the worker endpoint its path is inside the container.
  * a per-port pass over the pcap. attribute_capture() keys probes by IP, and
    IRTT and NetVibe share 172.16.2.1, so they are told apart by port here.
  * IRTT p95 and per-packet RTTs, from irtt's own round_trips.

Never drives the shared worker: the target container and URL come from
CPUC_WORKER_CONTAINER / PRAMANA_SUBSTRATE_URL, the run refuses to start if
either points at the default `substrate-worker` / :8002, and the host-nsenter
probe fallbacks are forced off.

Usage (on the VM, against a renamed worker that has the corpus at /ctpsrc):

    CPUC_WORKER_CONTAINER=jenil-fork-worker \\
    PRAMANA_SUBSTRATE_URL=http://127.0.0.1:18202 \\
    python3 experiments/cpuc/probes_only.py --mode both --ctp-profile NAME \\
        --ctp-dir /path/to/download --reps 1
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import os
import statistics
import struct
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ── environment, before pramana_helpers / netns read it at import ────────────
WORKER = os.environ.get("CPUC_WORKER_CONTAINER", "")
SUBSTRATE_URL = os.environ.get("PRAMANA_SUBSTRATE_URL", "")
if WORKER in ("", "substrate-worker") or SUBSTRATE_URL in (
    "",
    "http://localhost:8002",
    "http://127.0.0.1:8002",
):
    sys.exit(
        "probes_only refuses to drive the shared worker: set "
        "CPUC_WORKER_CONTAINER and PRAMANA_SUBSTRATE_URL to a renamed worker"
    )
for _k in (
    "PRAMANA_SUBSTRATE_CONTAINER",
    "PRAMANA_IRTT_CONTAINER",
    "PRAMANA_NETVIBE_CONTAINER",
):
    os.environ[_k] = WORKER
os.environ["PRAMANA_IRTT_HOST_FALLBACK"] = "0"
os.environ["PRAMANA_NETVIBE_HOST_FALLBACK"] = "0"
os.environ.setdefault("PRAMANA_COLLECT", "0")

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # experiments/
sys.path.insert(0, str(_HERE.parent / "pramana"))
sys.path.insert(0, str(_HERE.parent.parent))  # repo root, for shared/

from cpuc import ctp as ctp_mod  # noqa: E402
from cpuc import netns  # noqa: E402
from cpuc.conditions import CTP_CONDITION  # noqa: E402
import pramana_helpers as H  # noqa: E402

PROBE_ADDR = "172.16.2.1"
IRTT_PORT = 2112
NETVIBE_PORTS = (9999, 10443)
MODES = ("both", "irtt", "netvibe")
RESULTS_DIR = _HERE / "results"
CSV_COLUMNS = [
    "slug",
    "timestamp_utc",
    "mode",
    "ctp_profile",
    "rep",
    "condition",
    "rate_down_mbps",
    "rate_up_mbps",
    "rtt_ms",
    "qdisc",
    "buffer_packets",
    "duration_s",
    "irtt_rtt_min_ms",
    "irtt_rtt_mean_ms",
    "irtt_rtt_median_ms",
    "irtt_rtt_p95_ms",
    "irtt_rtt_max_ms",
    "irtt_loss_pct",
    "irtt_packets_sent",
    "irtt_packets_received",
    "netvibe_rtt_mean_ms",
    "netvibe_rtt_median_ms",
    "netvibe_rtt_p95_ms",
    "netvibe_queue_down_mean_ms",
    "netvibe_queue_down_p95_ms",
    "netvibe_queue_up_mean_ms",
    "netvibe_queue_up_p95_ms",
    "netvibe_probes_ok",
    "netvibe_probes_total",
    "netvibe_sessions",
    "netvibe_sensors",
    "ctp_mbps_window_mean",
    "ctp_mbps_window_p95_1s",
    "ctp_bytes_window",
    "irtt_bytes",
    "netvibe_bytes",
    "other_bytes",
    "probe_path_ok",
    "ok",
    "error",
    "pcap",
]


# ── small helpers ────────────────────────────────────────────────────────────
def _pct(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 4)


def _r(x: Optional[float], n: int = 4) -> Optional[float]:
    return None if x is None else round(x, n)


def _docker_cp(src: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(
        ["docker", "cp", f"{WORKER}:{src}", str(dest)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return out.returncode == 0 and dest.exists()


def irtt_packets(stopped: Optional[dict[str, Any]]) -> list[dict[str, float]]:
    """Per-packet (t_s since first send, rtt_ms) from irtt's own round_trips."""
    rts = (((stopped or {}).get("result") or {}).get("round_trips")) or []
    out: list[dict[str, float]] = []
    t0 = None
    for rt in rts:
        if rt.get("lost") not in (False, None, "false"):
            continue
        rtt_ns = ((rt.get("delay") or {}).get("rtt")) or None
        send = ((rt.get("timestamps") or {}).get("client") or {}).get("send") or {}
        wall = send.get("wall")
        if rtt_ns is None or wall is None:
            continue
        t0 = wall if t0 is None else t0
        out.append({"t_s": (wall - t0) / 1e9, "rtt_ms": rtt_ns / 1e6})
    return out


def netvibe_packets(sqlite_path: Optional[Path]) -> list[dict[str, float]]:
    """Per-probe (t_s, rtt_ms) from bench's records: t4-t1 minus the sensor's
    own hold time (t3-t2), the same round trip summarize_netvibe reports."""
    import sqlite3

    if not sqlite_path or not sqlite_path.exists():
        return []
    con = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select t1_ns, t2_us, t3_us, t4_ns from probe_records "
            "where status = 'ok' order by t1_ns"
        ).fetchall()
    finally:
        con.close()
    out = []
    t0 = rows[0][0] if rows else 0
    for t1, t2, t3, t4 in rows:
        if None in (t1, t2, t3, t4):
            continue
        out.append(
            {"t_s": (t1 - t0) / 1e9, "rtt_ms": ((t4 - t1) - (t3 - t2) * 1000) / 1e6}
        )
    return out


def classify_pcap(pcap: Path, ctp_ip: str = netns.CTP_PNAT_IP) -> dict[str, Any]:
    """Bytes per second by class: ctp (to/from the PNAT address), irtt and
    netvibe (172.16.2.1, told apart by port), other. Uses pramana_helpers'
    streaming reader so pcap and pcapng are both handled."""
    per_s: dict[str, dict[int, int]] = {
        k: {} for k in ("ctp", "irtt", "netvibe", "other")
    }
    totals = {k: 0 for k in per_s}
    t0 = None
    ctp, probe = ipaddress.ip_address(ctp_ip), ipaddress.ip_address(PROBE_ADDR)
    for ts, orig_len, linktype, data in H.iter_capture(str(pcap)):
        off = {1: 14, 101: 0, 113: 16, 228: 0}.get(linktype)
        if off is None or len(data) < off + 20:
            continue
        if linktype == 1:
            et = struct.unpack("!H", data[12:14])[0]
            while et == 0x8100 and len(data) >= off + 4:
                off += 4
                et = struct.unpack("!H", data[off - 2 : off])[0]
            if et != 0x0800:
                continue
        ip = data[off:]
        if ip[0] >> 4 != 4:
            continue
        ihl = (ip[0] & 0x0F) * 4
        proto = ip[9]
        src, dst = ipaddress.ip_address(ip[12:16]), ipaddress.ip_address(ip[16:20])
        sport = dport = None
        if proto in (6, 17) and len(ip) >= ihl + 4:
            sport, dport = struct.unpack("!HH", ip[ihl : ihl + 4])
        if ctp in (src, dst):
            cls = "ctp"
        elif probe in (src, dst) and IRTT_PORT in (sport, dport):
            cls = "irtt"
        elif probe in (src, dst) and (sport in NETVIBE_PORTS or dport in NETVIBE_PORTS):
            cls = "netvibe"
        else:
            cls = "other"
        t0 = ts if t0 is None else t0
        sec = int(ts - t0)
        per_s[cls][sec] = per_s[cls].get(sec, 0) + orig_len
        totals[cls] += orig_len
    return {"t0_epoch": t0, "per_s_bytes": per_s, "totals_bytes": totals}


def _window_rate(
    per_s: dict[int, int], t_lo: float, t_hi: float
) -> tuple[float, float, int]:
    secs = range(int(t_lo), int(t_hi))
    vals = [per_s.get(s, 0) * 8 / 1e6 for s in secs]
    if not vals:
        return 0.0, 0.0, 0
    return (
        round(statistics.fmean(vals), 3),
        _pct(vals, 0.95) or 0.0,
        sum(per_s.get(s, 0) for s in secs),
    )


# ── one run ──────────────────────────────────────────────────────────────────
def run_one(
    mode: str,
    ctp_profile: str,
    ctp_dir: str,
    rep: int,
    duration_s: int,
    out_root: Path,
) -> dict[str, Any]:
    assert mode in MODES, mode
    cond = CTP_CONDITION
    tag = ctp_profile or "noctp"
    slug = f"probes_{mode}_{cond.slug()}_{tag}_rep{rep}_{uuid.uuid4().hex[:6]}"
    run_dir = out_root / slug
    run_dir.mkdir(parents=True, exist_ok=True)
    rec: dict[str, Any] = {
        "slug": slug,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "ctp_profile": ctp_profile,
        "rep": rep,
        "condition": cond.slug(),
        "rate_down_mbps": cond.rate_down_mbps,
        "rate_up_mbps": cond.rate_up_mbps,
        "rtt_ms": cond.rtt_ms,
        "qdisc": cond.qdisc,
        "duration_s": duration_s,
        "worker_container": WORKER,
        "errors": [],
    }
    print(f"▶ PROBE-ONLY run: {slug}")
    cfg = H.ExperimentConfig(
        apps=[],
        bandwidth_mbps=cond.rate_down_mbps,
        upload_mbps=cond.rate_up_mbps,
        latency_ms=cond.latency_for_platform(),
        loss_pct=cond.loss_pct,
        aqm=cond.qdisc,
        cca="cubic",
        duration_s=duration_s,
        trial=rep,
        extra_probe_endpoint_ips=(
            {"ctp": {netns.CTP_PNAT_IP}} if ctp_profile else None
        ),
    )
    cfg.slug = slug
    rec["buffer_packets"] = cfg.buffer_packets

    # Shaping exactly as a single-app run_direct does it (verify on).
    print("  • shaping ...")
    rec["shape"] = H.substrate_shape(cfg, verify=True)
    rec["congestion"] = H.substrate_congestion(cfg)

    cap_duration = duration_s + H.CAPTURE_OVERHEAD_S
    cap = H.substrate_capture_start(slug, duration_s=cap_duration)
    up_cap_id = None
    if mode in ("both", "netvibe"):
        up_cap_id = H.substrate_capture_start_on(
            H.UPSTREAM_IFACE, f"{slug}_upstream", duration_s=cap_duration
        )["capture_id"]

    # CTP after shaping and capture, so the loop covers the whole window (his
    # margin of +30 s over the window is kept).
    if ctp_profile:
        print(f"  • CTP replay {ctp_profile} ...")
        rec["ctp_replay"] = netns.start_ctp_replay(
            ctp_profile, ctp_dir, seconds=duration_s + 30, container=WORKER
        )

    irtt_s = netvibe_s = None
    if mode in ("both", "irtt"):
        irtt_s = H.substrate_irtt_start(duration_s)
        if not irtt_s:
            rec["errors"].append("IRTT did not start on the worker endpoint")
    if mode in ("both", "netvibe"):
        netvibe_s = H.substrate_netvibe_start(run_dir, duration_s)
        if not netvibe_s:
            rec["errors"].append("NetVibe did not start on the worker endpoint")
    window_start = time.time()
    print(f"  • measuring {duration_s}s ...")
    time.sleep(duration_s)
    window_end = time.time()
    rec["window_epoch"] = [round(window_start, 3), round(window_end, 3)]

    # Stop in run_direct's order: NetVibe, then IRTT.
    nv_sqlite = None
    if netvibe_s:
        stopped = H.substrate_netvibe_stop(netvibe_s) or {}
        rec["netvibe_stop"] = {k: v for k, v in stopped.items() if k != "request"}
        if stopped.get("out_sqlite"):
            nv_sqlite = run_dir / "netvibe" / "bench.sqlite"
            if _docker_cp(stopped["out_sqlite"], nv_sqlite):
                stopped["out_sqlite"] = str(nv_sqlite)
            else:
                rec["errors"].append("could not copy bench.sqlite out of the worker")
        rec["netvibe"] = H.summarize_netvibe(stopped)
        if (rec["netvibe"] or {}).get("status") != "ok":
            rec["errors"].append(f"NetVibe: {(rec['netvibe'] or {}).get('error')}")
    irtt_raw = None
    if irtt_s:
        irtt_raw = H.substrate_irtt_stop(irtt_s) or {}
        (run_dir / "irtt_result.json").write_text(json.dumps(irtt_raw))
        rec["irtt"] = H.summarize_irtt(irtt_raw)
        if (rec["irtt"] or {}).get("status") != "ok":
            rec["errors"].append(f"IRTT: {(rec['irtt'] or {}).get('error')}")

    # Captures.
    pcap = run_dir / "capture.pcap"
    H.substrate_capture_wait(cap["capture_id"], timeout_s=cap_duration + 60)
    got = H.substrate_capture_download(cap["capture_id"], pcap)
    if got:
        H.substrate_capture_delete(cap["capture_id"])
    else:
        rec["errors"].append(f"pcap download failed (capture {cap['capture_id']} kept)")
    up_pcap = None
    if up_cap_id:
        up_pcap = run_dir / f"capture_{H.UPSTREAM_IFACE}.pcap"
        H.substrate_capture_wait(up_cap_id, timeout_s=cap_duration + 60)
        if H.substrate_capture_download(up_cap_id, up_pcap):
            H.substrate_capture_delete(up_cap_id)
        else:
            up_pcap = None

    # Separation: his attribution (CTP via extra_probe_endpoint_ips, probes via
    # their own reports), plus the per-port split it cannot do on one IP.
    probe_eps = H.merge_endpoint_ips(
        H.irtt_probe_endpoints(rec.get("irtt")),
        H.netvibe_probe_endpoints(rec.get("netvibe")),
        cfg.extra_probe_endpoint_ips,
    )
    if got:
        attr = H.attribute_capture(
            pcap,
            [],
            cfg.bandwidth_mbps,
            client_ips=H.client_ips_for_capture(),
            probe_endpoint_ips=probe_eps,
        )
        rec["attribution_bucket_mb"] = dict(attr.bucket_mb)
        probe_ips = sorted(
            {ip for ips in probe_eps.values() for ip in ips} - {netns.CTP_PNAT_IP}
        )
        if probe_ips and up_pcap:
            ppath = H.check_probe_path(
                {H.DOWNSTREAM_IFACE: pcap, H.UPSTREAM_IFACE: up_pcap},
                probe_ips,
                enforce=False,
            )
            rec["probe_path"] = (
                ppath
                if isinstance(ppath, dict)
                else getattr(ppath, "__dict__", str(ppath))
            )
        cls = classify_pcap(pcap)
        lo = window_start - (cls["t0_epoch"] or window_start)
        hi = window_end - (cls["t0_epoch"] or window_start)
        mean, p95, nbytes = _window_rate(cls["per_s_bytes"]["ctp"], lo, hi)
        rec["ctp_observed"] = {
            "mbps_window_mean": mean,
            "mbps_window_p95_1s": p95,
            "bytes_window": nbytes,
        }
        rec["bytes_by_class"] = cls["totals_bytes"]
        (run_dir / "bytes_per_s.json").write_text(
            json.dumps(
                {
                    "t0_epoch": cls["t0_epoch"],
                    "window_rel_s": [lo, hi],
                    **cls["per_s_bytes"],
                }
            )
        )

    # Per-packet series and the row.
    ip = irtt_packets(irtt_raw)
    nvp = netvibe_packets(nv_sqlite)
    (run_dir / "rtt_series.json").write_text(json.dumps({"irtt": ip, "netvibe": nvp}))
    rec["pcap"] = str(pcap) if got else ""
    rec["ok"] = not rec["errors"]
    (run_dir / "record.json").write_text(json.dumps(rec, indent=2, default=str))
    row = csv_row(rec, ip, nvp)
    print(
        f"  {'✓' if rec['ok'] else '✗'} {slug}: irtt p95={row['irtt_rtt_p95_ms']} ms  "
        f"netvibe p95={row['netvibe_rtt_p95_ms']} ms  ctp={row['ctp_mbps_window_mean']} Mb/s  "
        f"ok={row['ok']}" + (f"  error: {row['error']}" if row["error"] else "")
    )
    return row


def csv_row(rec: dict[str, Any], ip: list[dict], nvp: list[dict]) -> dict[str, Any]:
    irtt = rec.get("irtt") or {}
    irt = irtt.get("round_trip_ms") or {}
    pk = irtt.get("packets") or {}
    nv = rec.get("netvibe") or {}
    nrt = nv.get("round_trip_ms") or {}
    q = nv.get("queueing_ms") or {}
    dn, up = q.get("downstream_ns2_to_ns1") or {}, q.get("upstream_ns1_to_ns2") or {}
    rc = nv.get("records") or {}
    ns = (rec.get("netvibe_stop") or {}).get("records") or {}
    co = rec.get("ctp_observed") or {}
    bb = rec.get("bytes_by_class") or {}
    irtt_rtts = [p["rtt_ms"] for p in ip]
    nv_rtts = [p["rtt_ms"] for p in nvp]
    pp = rec.get("probe_path")
    return {
        "slug": rec["slug"],
        "timestamp_utc": rec["timestamp_utc"],
        "mode": rec["mode"],
        "ctp_profile": rec["ctp_profile"] or "none",
        "rep": rec["rep"],
        "condition": rec["condition"],
        "rate_down_mbps": rec["rate_down_mbps"],
        "rate_up_mbps": rec["rate_up_mbps"],
        "rtt_ms": rec["rtt_ms"],
        "qdisc": rec["qdisc"],
        "buffer_packets": rec.get("buffer_packets"),
        "duration_s": rec["duration_s"],
        "irtt_rtt_min_ms": irt.get("min"),
        "irtt_rtt_mean_ms": irt.get("mean"),
        "irtt_rtt_median_ms": irt.get("median"),
        "irtt_rtt_p95_ms": _pct(irtt_rtts, 0.95),
        "irtt_rtt_max_ms": irt.get("max"),
        "irtt_loss_pct": pk.get("loss_percent"),
        "irtt_packets_sent": pk.get("sent"),
        "irtt_packets_received": pk.get("received"),
        "netvibe_rtt_mean_ms": nrt.get("mean"),
        "netvibe_rtt_median_ms": nrt.get("median"),
        "netvibe_rtt_p95_ms": (
            nrt.get("p95") if nrt.get("p95") is not None else _pct(nv_rtts, 0.95)
        ),
        "netvibe_queue_down_mean_ms": dn.get("mean"),
        "netvibe_queue_down_p95_ms": dn.get("p95"),
        "netvibe_queue_up_mean_ms": up.get("mean"),
        "netvibe_queue_up_p95_ms": up.get("p95"),
        "netvibe_probes_ok": rc.get("ok"),
        "netvibe_probes_total": rc.get("total"),
        "netvibe_sessions": ns.get("probe_sessions"),
        "netvibe_sensors": ns.get("sensors"),
        "ctp_mbps_window_mean": co.get("mbps_window_mean"),
        "ctp_mbps_window_p95_1s": co.get("mbps_window_p95_1s"),
        "ctp_bytes_window": co.get("bytes_window"),
        "irtt_bytes": bb.get("irtt"),
        "netvibe_bytes": bb.get("netvibe"),
        "other_bytes": bb.get("other"),
        "probe_path_ok": (pp or {}).get("ok") if isinstance(pp, dict) else None,
        "ok": rec["ok"],
        "error": "; ".join(rec["errors"]),
        "pcap": rec["pcap"],
    }


def append_csv(path: Path, row: dict[str, Any]) -> None:
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=MODES, default="both")
    ap.add_argument(
        "--ctp-profile", default="", help="one profile name, or empty for no CTP"
    )
    ap.add_argument(
        "--ctp-count", type=int, default=0, help="N random profiles from manifest.csv"
    )
    ap.add_argument("--ctp-seed", type=int, default=None)
    ap.add_argument("--ctp-dir", default=ctp_mod.DEFAULT_CTP_DIR)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument(
        "--rep-start",
        type=int,
        default=1,
        help="rep number of the first run, for interleaving one rep per call",
    )
    ap.add_argument("--duration", type=int, default=60)
    ap.add_argument("--gap", type=int, default=20, help="idle seconds between runs")
    ap.add_argument("--out-root", default=str(RESULTS_DIR / "probes_runs"))
    ap.add_argument("--csv", default=str(RESULTS_DIR / "probes_ctp_runs.csv"))
    args = ap.parse_args()

    if args.ctp_count:
        # Same corpus layout as ctp.load_profiles: <dir>/manifest.csv + <dir>/download/.
        profiles = ctp_mod.random_profiles(
            args.ctp_count, args.ctp_dir, seed=args.ctp_seed
        )
        ctp_dir = str(Path(args.ctp_dir) / "download")
    else:
        profiles = [args.ctp_profile]
        ctp_dir = args.ctp_dir
    if any(profiles):
        netns.assert_ctp_mount(WORKER)
    out_root = Path(args.out_root)
    first = True
    for prof in profiles:
        for rep in range(args.rep_start, args.rep_start + args.reps):
            if not first:
                time.sleep(args.gap)
            first = False
            row = run_one(args.mode, prof, ctp_dir, rep, args.duration, out_root)
            append_csv(Path(args.csv), row)


if __name__ == "__main__":
    main()
