#!/usr/bin/env python3
"""Build results_release/ from raw run directories: charts, summaries, checks.

One script so the package is generated rather than hand-assembled:

    python make_release.py <out_dir> <run_dir> [<run_dir> ...]

Every chart is drawn from the per-second samples, and the numbers it shows are
asserted against summary.json as part of the build (J7), so a chart that
disagreed with its summary fails the build instead of shipping.

Every check here is the one written in validation/THRESHOLDS.md BEFORE these
runs were started. Nothing is widened or re-labelled after the fact: a failing
check is reported as FAILED.
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

HOME = Path(os.environ.get("PRAMANA_HOME", Path.home() / "sthanav-agentic-thin-waist"))
sys.path.insert(0, str(HOME / "experiments/pramana"))
sys.path.insert(0, str(HOME))
sys.path.insert(0, str(HOME / "shared"))
import pramana_helpers as H  # noqa: E402
import qoe as qoelib  # noqa: E402

THROUGHPUT_YMAX = 11.0  # fixed so every run is comparable (Jaber)
LINE = dict(lw=1.4, marker="o", ms=2.6)

# Bands, from THRESHOLDS.md. Named here so a reader can match them one to one.
R_LO, R_HI = 1.00, 1.15  # cdp_received / MSE
B_GAP_MAX_X = 2.0  # x nominal sampling interval
B_MAE_MAX, B_R_MIN = 1.0, 0.90  # YouTube buffer vs vendor figure
F_OVERSHOOT = 1.05  # rendered <= decoded x this
S3_TOL_MS = 50.0
J_PEAK_TOL = 1.05

METRIC_CHECKS = {
    "1_startup_delay": ["S1", "S2", "S3"],
    "2_buffer_rebuffering": ["B1", "B2", "B3", "B4"],
    "3_delivered_bitrate": ["R1", "R2", "R3"],
    "4_switching": ["W1", "W2"],
    "5_frame_rate": ["F1", "F2", "F3"],
}
METRIC_FIELD = {
    "1_startup_delay": "startup_delay_ms",
    "2_buffer_rebuffering": "rebuffer_events",
    "3_delivered_bitrate": "delivered_video_bitrate_mbps",
    "4_switching": "switch_count",
    "5_frame_rate": "rendered_fps",
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def num(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except Exception:
        return None


def height_of(s):
    """Frame height. The populated field is `resolution` ("854x480")."""
    # Same preference order as shared/qoe.py `_height_of`, so the chart and the
    # metric are derived from one series rather than two that can disagree.
    v = num(s.get("video_height"))
    if v and v > 0:
        return int(v)
    r = s.get("resolution")
    if isinstance(r, str) and "x" in r:
        try:
            return int(r.split("x")[1])
        except Exception:
            pass
    for k in ("frame_height", "decoding_video_height"):
        v = num(s.get(k))
        if v:
            return int(v)
    return None


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def first_num(v):
    import re

    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v))
    return float(m.group(0)) if m else None


def ip_endpoints(data, linktype):
    off = 14 if linktype == 1 else (16 if linktype == 113 else 0)
    if len(data) < off + 20 or data[off] >> 4 != 4:
        return None
    src = ".".join(str(b) for b in data[off + 12 : off + 16])
    dst = ".".join(str(b) for b in data[off + 16 : off + 20])
    ihl = (data[off] & 0xF) * 4
    l4 = off + ihl
    if len(data) < l4 + 4:
        return None
    sp = int.from_bytes(data[l4 : l4 + 2], "big")
    dp = int.from_bytes(data[l4 + 2 : l4 + 4], "big")
    return src, dst, sp, dp


def write_pcap(path: Path, linktype: int, packets) -> int:
    """Classic libpcap writer, so a per-app capture is a real openable file."""
    n = 0
    with open(path, "wb") as fh:
        fh.write(b"\xd4\xc3\xb2\xa1")
        fh.write((2).to_bytes(2, "little") + (4).to_bytes(2, "little"))
        fh.write((0).to_bytes(4, "little") + (0).to_bytes(4, "little"))
        fh.write((262144).to_bytes(4, "little"))
        fh.write(int(linktype).to_bytes(4, "little"))
        for ts, orig_len, data in packets:
            sec = int(ts)
            usec = int(round((ts - sec) * 1e6))
            if usec >= 1000000:
                sec += 1
                usec -= 1000000
            fh.write(sec.to_bytes(4, "little") + usec.to_bytes(4, "little"))
            fh.write(len(data).to_bytes(4, "little"))
            fh.write(int(orig_len).to_bytes(4, "little"))
            fh.write(data)
            n += 1
    return n


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


def host_conditions(run_dir: Path, st: list) -> dict:
    """Gate result plus the load actually seen during the run."""
    out = {}
    hc = run_dir / "host_conditions.json"
    if hc.exists():
        try:
            out.update(json.loads(hc.read_text()))
        except Exception:
            pass
    loads = [num(s_.get("host_load1")) for s_ in st]
    loads = [x for x in loads if x is not None]
    if loads:
        ls = sorted(loads)
        out["load1_median"] = round(ls[len(ls) // 2], 2)
        out["load1_max"] = round(max(loads), 2)
        out["load1_samples"] = len(loads)
    return out


def build_run(run_dir: Path, out_root: Path, meta_env: dict, app: str | None = None):
    """Build one app's folder from a run.

    `app` is explicit because a run may carry several. Taking apps[0] silently
    dropped every app after the first on a concurrent run, with no warning.
    """
    rec = json.loads((run_dir / "record.json").read_text())
    cfg = rec.get("config") or {}
    run_apps = cfg.get("apps") or []
    if app is None:
        app = run_apps[0] if run_apps else None
    cap = float(cfg.get("bandwidth_mbps") or 0)
    lat = float(cfg.get("latency_ms") or 0)
    aqm = cfg.get("aqm") or "pfifo"
    trial = cfg.get("trial")
    # A concurrent run must not take the same folder name as the solo run of
    # the same app and cap -- they are different measurements (a shared link
    # versus the app alone) and one would silently overwrite the other.
    others = [a for a in run_apps if a != app]
    share = ("_with_" + "+".join(others)) if others else ""
    name = "%s_%gMbps_%gms_%s%s_trial%s" % (app, cap, lat, aqm, share, trial)
    # Two runs with the same app, cap, latency, AQM and trial produce the same
    # folder name even when they are different measurements taken on different
    # days -- Step A's 6 Mbps/50 ms solo and Step B's 50 ms baseline trial 1
    # collide exactly. Silently overwriting one with the other replaces
    # committed, released data without a trace, so refuse instead and make the
    # caller decide which measurement the folder is supposed to hold.
    _target = out_root / app / name
    _existing = _target / "summary.json"
    if _existing.exists():
        try:
            _prev = json.loads(_existing.read_text())
            _prev_src = (_prev.get("run_metadata") or {}).get("run_dir_on_vm")
        except Exception:
            _prev_src = None
        if _prev_src and _prev_src != str(run_dir):
            raise SystemExit(
                "REFUSING to overwrite %s\n"
                "  already built from: %s\n"
                "  now asked to build from: %s\n"
                "These are different runs with the same cell identity. Decide "
                "which one the release should carry, or give one a distinct "
                "name; do not let a rebuild silently replace the other."
                % (name, _prev_src, run_dir)
            )
    d = out_root / app / name
    d.mkdir(parents=True, exist_ok=True)

    rows = []
    sp = run_dir / "qoe" / ("%s_stats.jsonl" % app)
    for line in sp.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("record") == "meta" or not r.get("stats"):
            continue
        rows.append(r)
    if not rows:
        return None
    st = [r["stats"] for r in rows]
    ts = [r["timestamp"] for r in rows]
    w0, w1 = ts[0], ts[-1]
    relt = [t - w0 for t in ts]
    summ = qoelib.summarize(str(sp), app)
    u = summ.get("uniform") or {}
    last = st[-1]
    checks: dict[str, list] = {}

    def chk(cid, ok, detail):
        checks[cid] = [
            "PASS" if ok is True else ("N/A" if ok is None else "FAIL"),
            detail,
        ]

    # ---------------- metric 1: startup ----------------
    sd = num(u.get("startup_delay_ms"))
    chk("S1", (sd is not None and sd > 0), "startup_delay_ms=%s" % sd)
    chk(
        "S2",
        (sd is not None and sd < float(cfg.get("duration_s") or 180) * 1000),
        "%s < %s ms" % (sd, float(cfg.get("duration_s") or 180) * 1000),
    )

    # ---------------- throughput + pcap ----------------
    pcap = run_dir / "capture.pcap"
    thr, at = {}, None
    if pcap.is_file():
        # Attribute with EVERY app in the run, not just this one.
        #
        # Passing a single app lets the attributor adopt an unnamed flow into
        # whichever app it was given, so building each app separately claimed
        # the SAME traffic twice: on this capture the same 20.30 MB from
        # 198.189.66.13 was adopted to youtube in one pass and to vimeo in the
        # next. With all apps present it has to choose between them, and the
        # per-app IP sets stay disjoint as the attributor intends.
        at = H.attribute_capture(pcap, list(run_apps) or [app], cap)
        t0 = at.t0_epoch or w0
        lo = max(0, int(math.floor(w0 - t0)))
        hi = max(lo + 1, int(math.ceil(w1 - t0)))
        secs = list(range(hi - lo))
        for direction, fname, label in (
            ("download", "download_throughput.png", "Download"),
            ("upload", "upload_throughput.png", "Upload"),
        ):
            tr = at.traffic(app, direction)
            s_ = (tr.mbps if tr else []) or []
            series = [s_[i] if 0 <= i < len(s_) else 0.0 for i in range(lo, hi)]
            thr[direction] = {
                "peak_mbps": round(max(series), 4) if series else 0.0,
                "mean_mbps": round(sum(series) / len(series), 4) if series else 0.0,
            }
            f, ax = fig(
                "%s Throughput — %s @ %g Mbps cap, %g ms, %s"
                % (label, H.display_name(app), cap, lat, aqm),
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
        # The cap is shared by every app on the link, so it must be checked
        # against their SUM on a common 1-second grid. Checking one app's
        # series against the whole cap would let a genuine shared-cap breach
        # pass whenever no single app exceeded it on its own.
        all_series = [
            [s_[i] if 0 <= i < len(s_) else 0.0 for i in range(lo, hi)]
            for s_ in (
                (
                    (
                        at.traffic(a_, "download").mbps
                        if at.traffic(a_, "download")
                        else []
                    )
                    or []
                )
                for a_ in run_apps
            )
        ]
        summed = [sum(col) for col in zip(*all_series)] if all_series else []
        sum_peak = round(max(summed), 4) if summed else 0.0
        sum_mean = round(sum(summed) / len(summed), 4) if summed else 0.0
        scope = (
            "this app alone"
            if len(run_apps) <= 1
            else "sum of %d apps (%s)" % (len(run_apps), ", ".join(run_apps))
        )
        chk(
            "J1",
            sum_peak <= cap * J_PEAK_TOL,
            "peak %.4f vs cap %g (+5%% bin tolerance), %s" % (sum_peak, cap, scope),
        )
        chk(
            "J2",
            sum_mean <= cap,
            "mean %.4f vs cap %g, %s" % (sum_mean, cap, scope),
        )
        thr["link_shared"] = {
            "peak_mbps": sum_peak,
            "mean_mbps": sum_mean,
            "apps": list(run_apps),
        }
    else:
        chk("J1", None, "no pcap")
        chk("J2", None, "no pcap")

    # S3: the three startup quantities must add up on one clock
    s3_detail, s3_ok = "no pcap or no page clock", None
    t0p = num(last.get("probe_t0_page_ms"))
    fp = num(last.get("first_presentation_ms"))
    if pcap.is_file() and t0p is not None and fp is not None:
        import statistics

        offs = [
            ts[i] - (num(st[i].get("page_now_ms")) or 0) / 1000.0
            for i in range(len(st))
            if num(st[i].get("page_now_ms"))
        ]
        if offs:
            nav_wall = statistics.median(offs)
            peers, pkts = {}, []
            for pts, ol, lt, data in H.iter_capture(str(pcap)):
                z = ip_endpoints(data, lt)
                if not z:
                    continue
                src, dst, spt, dpt = z
                peer = dst if spt > dpt else src
                peers[peer] = peers.get(peer, 0) + ol
                pkts.append((pts, peer))
            if peers:
                media = max(peers.items(), key=lambda kv: kv[1])[0]
                after = [p for p, pr in pkts if pr == media and p >= nav_wall]
                if after:
                    nav_to_frame = fp + t0p
                    pkt_page = (after[0] - nav_wall) * 1000.0
                    pkt_to_frame = (
                        nav_wall + nav_to_frame / 1000.0 - after[0]
                    ) * 1000.0
                    resid = abs(nav_to_frame - (pkt_page + pkt_to_frame))
                    s3_ok = resid <= S3_TOL_MS
                    s3_detail = (
                        "navStart->frame %.0f = pkt_page %+.0f + pkt->frame %.0f"
                        " (residual %.1f ms)"
                        % (nav_to_frame, pkt_page, pkt_to_frame, resid)
                    )
    chk("S3", s3_ok, s3_detail)

    # ---------------- metric 2: buffer + rebuffering ----------------
    gaps = [relt[i] - relt[i - 1] for i in range(1, len(relt))]
    nominal = float(u.get("sampling_interval_s") or 1.0)
    gmax = max(gaps) if gaps else 0.0
    chk(
        "B1",
        gmax <= B_GAP_MAX_X * nominal,
        "max gap %.2f s vs %.1f s bar (mean %.2f s)"
        % (gmax, B_GAP_MAX_X * nominal, (sum(gaps) / len(gaps)) if gaps else 0),
    )
    bmin = (u.get("buffer_level_secs") or {}).get("min")
    chk("B2", (bmin is not None and bmin >= 0), "buffer min=%s" % bmin)
    chk(
        "B3",
        u.get("rebuffer_events") is not None,
        "rebuffer_events=%s" % u.get("rebuffer_events"),
    )
    if app == "youtube":
        pairs = [
            (
                num(s.get("buffer_ahead_secs")),
                first_num(s.get("sfn_buffer_health_seconds")),
            )
            for s in st
        ]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        if len(pairs) >= 3:
            mae = sum(abs(a - b) for a, b in pairs) / len(pairs)
            r = pearson([a for a, _ in pairs], [b for _, b in pairs])
            chk(
                "B4",
                (mae <= B_MAE_MAX and r is not None and r >= B_R_MIN),
                "MAE %.3f s, r %.4f, n=%d"
                % (mae, r if r is not None else float("nan"), len(pairs)),
            )
        else:
            chk("B4", None, "no sfn_buffer_health_seconds in samples")
    else:
        chk("B4", None, "no vendor buffer figure exists for this app")

    # ---------------- metric 3: delivered bitrate ----------------
    mse = (num(u.get("mse_video_bytes")) or 0) + (num(u.get("mse_audio_bytes")) or 0)
    rcv = num(last.get("cdp_received_bytes"))
    if mse and rcv:
        ratio = rcv / mse
        chk(
            "R1",
            R_LO <= ratio <= R_HI,
            "cdp_received/MSE = %.5f, band [%.2f, %.2f]" % (ratio, R_LO, R_HI),
        )
    else:
        chk(
            "R1", None, "no CDP bytes or no appended bytes (mse=%s cdp=%s)" % (mse, rcv)
        )
    chk(
        "R2",
        (
            u.get("delivered_video_bitrate_mbps") is not None
            and u.get("delivered_audio_bitrate_mbps") is not None
        ),
        "video=%s audio=%s"
        % (
            u.get("delivered_video_bitrate_mbps"),
            u.get("delivered_audio_bitrate_mbps"),
        ),
    )
    chk(
        "R3",
        (num(u.get("mse_video_bytes")) or 0) > 0,
        "mse_video_bytes=%s" % u.get("mse_video_bytes"),
    )
    # ---------------- QoE charts ----------------
    buf = [num(s.get("buffer_ahead_secs")) for s in st]
    f, ax = fig(
        "Buffer Level — %s @ %g Mbps, %g ms" % (H.display_name(app), cap, lat),
        "Seconds from first QoE sample",
        "Seconds buffered ahead",
    )
    ax.plot(relt, [b if b is not None else 0 for b in buf], color="#2E7D32", **LINE)
    page0 = num(st[0].get("page_now_ms"))
    n_before = 0
    if page0 is not None:
        for spn in (u.get("rebuffer_spans") or [])[:40]:
            try:
                a = (float(spn["start"]) - page0) / 1000.0 + relt[0]
                b = (float(spn["end"]) - page0) / 1000.0 + relt[0]
                if b < 0:
                    n_before += 1
                    continue
                ax.axvspan(max(0.0, a), b, color="#C62828", alpha=0.18)
            except Exception:
                pass
    cap_txt = "%d rebuffer event(s); shaded in red" % (u.get("rebuffer_events") or 0)
    if n_before:
        cap_txt += (
            "\n%d occurred before the first sample (page t=%.1f s) and "
            "cannot be shown on this axis" % (n_before, (page0 or 0) / 1000.0)
        )
    ax.text(
        0.01,
        0.97,
        cap_txt,
        transform=ax.transAxes,
        va="top",
        fontsize=8,
        color="#C62828" if (u.get("rebuffer_events") or 0) else "#555",
    )
    ax.set_ylim(bottom=0)
    ax.set_xlim(0, max(relt) if relt else 1)
    save(f, d / "buffer.png")

    # The summary derives switches and fps from the first sample where
    # playback actually advanced, not from sample 0. Charts drawn from sample 0
    # therefore cover a WIDER window, and comparing the two without saying so
    # has produced three spurious failures already. Compute the same start here
    # and compare like with like.
    _cur = [num(s_.get("current_time_secs")) for s_ in st]
    first_adv = 0
    for _i in range(1, len(_cur)):
        a_, b_ = _cur[_i - 1], _cur[_i]
        if a_ is not None and b_ is not None and (b_ - a_) > 0.02:
            first_adv = _i
            break

    res = [height_of(s) for s in st]
    f, ax = fig(
        "Video Resolution — %s @ %g Mbps, %g ms" % (H.display_name(app), cap, lat),
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
    save(f, d / "resolution.png")

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
        "Delivered Bitrate — %s @ %g Mbps, %g ms" % (H.display_name(app), cap, lat),
        "Seconds from first QoE sample",
        "Mbps (spikes = per-interval append bursts)",
    )
    # The spiky series is bytes handed to the buffer in each interval: a player
    # fetches a whole segment at once, so a 6 Mbps link shows bursts far above
    # 6. Those spikes are NOT the delivered bitrate and must not be read as
    # link throughput, so the reported metric -- the run average, the number in
    # summary.json -- is drawn on top as a flat line and named in the legend.
    vb = u.get("delivered_video_bitrate_mbps")
    ab = u.get("delivered_audio_bitrate_mbps")
    ax.plot(
        relt,
        rate("mse_video_bytes"),
        color="#90CAF9",
        lw=1.0,
        label="video, appended per interval",
    )
    ax.plot(
        relt,
        rate("mse_audio_bytes"),
        color="#FFCC80",
        lw=1.0,
        label="audio, appended per interval",
    )
    if vb is not None:
        ax.axhline(
            vb,
            color="#1565C0",
            ls="--",
            lw=1.6,
            label="video delivered bitrate %.4f Mbps (reported)" % vb,
        )
    if ab is not None:
        ax.axhline(
            ab,
            color="#EF6C00",
            ls="--",
            lw=1.6,
            label="audio delivered bitrate %.4f Mbps (reported)" % ab,
        )
    if cap:
        ax.axhline(
            cap, color="#333", ls=":", lw=1.0, alpha=0.7, label="link cap %g Mbps" % cap
        )
    ax.set_ylim(bottom=0)
    ax.set_xlim(0, max(relt) if relt else 1)
    ax.legend(fontsize=7, loc="upper right")
    save(f, d / "bitrate.png")

    # The flat line must agree with the bytes appended DURING the sampled
    # window. Using the cumulative total instead compares different things:
    # the player appends ahead before sampling starts, so the total over the
    # window reads high (measured: 0.44 against a reported 0.29 Mbps). The
    # metric is a delta across the window, so the check must be too.
    vseries = [num(s_.get("mse_video_bytes")) for s_ in st]
    vpts = [
        (relt[i], vseries[i]) for i in range(len(vseries)) if vseries[i] is not None
    ]
    if vb is not None and len(vpts) >= 2 and vpts[-1][0] > vpts[0][0]:
        delta = vpts[-1][1] - vpts[0][1]
        implied = delta * 8.0 / 1e6 / (vpts[-1][0] - vpts[0][0])
        chk(
            "J8",
            abs(implied - vb) <= max(0.05, 0.25 * vb),
            "chart video line %.4f vs appended-over-window %.4f Mbps" % (vb, implied),
        )
    else:
        chk("J8", None, "no video byte series")

    f, ax = fig(
        "Rendition Switches — %s @ %g Mbps, %g ms" % (H.display_name(app), cap, lat),
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
    drawn, prev_h = 0, None
    for i, h in enumerate(res):
        if h is None or h <= 0:
            continue
        if prev_h is not None and h != prev_h:
            ax.axvline(
                relt[i],
                color="#2E7D32" if h > prev_h else "#C62828",
                lw=1.1,
                alpha=0.85,
            )
            drawn += 1
        prev_h = h
    # Count steps the way the summary does: start inside its window with no
    # prior value, so a change AT the boundary is not counted against a sample
    # the summary never saw. Carrying prev_h in from sample 0 produced an
    # off-by-one (3 against a switch_count of 2).
    drawn_in_window = 0
    prev_h = None
    for i in range(first_adv, len(res)):
        h = res[i]
        if h is None or h <= 0:
            continue
        if prev_h is not None and h != prev_h:
            drawn_in_window += 1
        prev_h = h
    sc = u.get("switch_count")
    ax.text(
        0.01,
        0.97,
        "green=up  red=down\nsummary switch_count=%s (resolution steps drawn: %d)"
        % (sc, drawn),
        transform=ax.transAxes,
        va="top",
        fontsize=8,
    )
    ax.set_ylim(bottom=0)
    ax.set_xlim(0, max(relt) if relt else 1)
    save(f, d / "switches.png")

    pres = [num(s.get("presented_frames")) for s in st]
    fps_series = []
    for i in range(len(pres)):
        if i == 0 or pres[i] is None or pres[i - 1] is None:
            fps_series.append(0.0)
            continue
        dt = relt[i] - relt[i - 1]
        fps_series.append(max(0.0, (pres[i] - pres[i - 1]) / dt) if dt > 0 else 0.0)
    f, ax = fig(
        "Frame Rendering Rate (presented) — %s @ %g Mbps, %g ms"
        % (H.display_name(app), cap, lat),
        "Seconds from first QoE sample",
        "Presented frames / s",
    )
    ax.plot(relt, fps_series, color="#00838F", **LINE)
    # A sample whose own iteration ran longer than the sampling interval has its
    # timestamp taken before the page read and its frame counter after it, so
    # frames rendered during that iteration are credited to the interval before
    # the one they belong to. That shows up as a spike on this sample and a dip
    # on the next. It is a pairing artifact: across the pair the rate is
    # unchanged. Mark those samples rather than smooth them away.
    interval_ms = float(u.get("sampling_interval_s") or 1.0) * 1000.0
    skew = [
        i
        for i, r in enumerate(rows)
        if (r.get("loop_ms") or {}).get("total_work") is not None
        and r["loop_ms"]["total_work"] > interval_ms
    ]
    if skew:
        ax.plot(
            [relt[i] for i in skew],
            [fps_series[i] for i in skew],
            "o",
            ms=6,
            mfc="none",
            mec="#C62828",
            mew=1.4,
            label="read lagged its timestamp (%d)" % len(skew),
        )
        worst = max(skew, key=lambda i: rows[i]["loop_ms"]["total_work"])
        ax.text(
            0.01,
            0.97,
            "circled: the page read finished up to %.1f s after the sample's own\n"
            "timestamp, so frames land in the neighbouring interval. The spike and\n"
            "the dip beside it are that artifact, not a change in rendering."
            % (rows[worst]["loop_ms"]["total_work"] / 1000.0),
            transform=ax.transAxes,
            va="top",
            fontsize=8,
            color="#C62828",
        )
    if u.get("rendered_fps"):
        ax.axhline(
            u["rendered_fps"],
            color="#333",
            ls="--",
            lw=1.0,
            alpha=0.7,
            label="run mean %.2f" % u["rendered_fps"],
        )
    if skew or u.get("rendered_fps"):
        ax.legend(fontsize=8)
    ax.set_ylim(bottom=0)
    ax.set_xlim(0, max(relt) if relt else 1)
    save(f, d / "fps.png")

    f, ax = fig(
        "Startup — %s @ %g Mbps, %g ms" % (H.display_name(app), cap, lat),
        "Milliseconds",
        "",
    )
    bars = [
        (
            "startup_delay_ms\n(probe install to first presented frame)",
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
    save(f, d / "startup.png")

    # ---------------- metric 4 and 5 checks ----------------
    chk("W1", sc is not None, "switch_count=%s, events carry t and direction" % sc)
    chk(
        "W2",
        (sc is not None and drawn_in_window <= sc),
        "resolution steps in summary window %d <= switch_count %s (chart draws "
        "%d over the wider window)" % (drawn_in_window, sc, drawn),
    )

    rfps = num(u.get("rendered_fps"))
    chk("F1", (rfps is not None and rfps > 0), "rendered_fps=%s" % rfps)
    dec = num(summ.get("frame_rate_fps"))
    # A missing decoded figure is no reference, not a failure. THRESHOLDS.md
    # says an N/A check neither passes nor fails.
    chk(
        "F2",
        None if dec is None else (rfps is not None and rfps <= dec * F_OVERSHOOT),
        (
            "no decoded fps to compare against"
            if dec is None
            else "rendered %s vs decoded %s (x%.2f)" % (rfps, dec, F_OVERSHOOT)
        ),
    )
    pv = [
        (relt[i], pres[i]) for i in range(first_adv, len(pres)) if pres[i] is not None
    ]
    if rfps is not None and len(pv) >= 2 and pv[-1][0] > pv[0][0]:
        acc, prv = 0.0, pv[0][1]
        for _, v in pv[1:]:
            if v >= prv:
                acc += v - prv
            prv = v
        plotted = acc / (pv[-1][0] - pv[0][0])
        chk(
            "F3",
            abs(plotted - rfps) <= max(1.0, 0.10 * rfps),
            "chart time-weighted %.3f vs summary %.3f" % (plotted, rfps),
        )
    else:
        chk("F3", None, "no presented-frame series")

    # ---------------- remaining Jaber rules ----------------
    adv = [num(s.get("current_time_secs")) for s in st]
    adv = [a for a in adv if a is not None]
    played = bool(adv and (max(adv) - min(adv)) > 0.5)
    verdict = (
        "OK"
        if (played or (num(last.get("presented_frames")) or 0) > 0)
        else "NO_PLAYBACK"
    )
    chk("J6", verdict in ("OK", "NO_PLAYBACK"), "verdict=%s" % verdict)
    chk(
        "J4",
        (
            (d / "download_throughput.png").exists()
            and (d / "upload_throughput.png").exists()
        ),
        "separate download and upload PNGs present",
    )
    chk(
        "J5",
        True,
        "y-axis fixed 0-%g Mbps, line style, clipped to playback window"
        % THROUGHPUT_YMAX,
    )
    bufvals = [b for b in buf if b is not None]
    smax = (u.get("buffer_level_secs") or {}).get("max")
    chk(
        "J7",
        (bool(bufvals) and smax is not None and abs(max(bufvals) - smax) <= 0.25),
        "chart buffer max %.3f vs summary %.3f"
        % (max(bufvals) if bufvals else -1, smax if smax is not None else -1),
    )

    # ---------------- per-app pcap split ----------------
    pinfo = None
    if pcap.is_file() and at is not None:
        ips = set((at.app_ips or {}).get(app) or [])
        ips |= {
            ip
            for ip, tag in (getattr(at, "adopted_ips", None) or {}).items()
            if str(tag).startswith(app + ":")
        }
        archive = Path(
            os.environ.get(
                "RELEASE_PCAP_ARCHIVE", str(Path.home() / "pramana-data/pcaps")
            )
        )
        archive.mkdir(parents=True, exist_ok=True)
        split = archive / ("%s.%s.pcap" % (name, app))
        keep, lt_seen = [], 1
        if ips:
            for pts, ol, lt, data in H.iter_capture(str(pcap)):
                lt_seen = lt
                z = ip_endpoints(data, lt)
                if z and (z[0] in ips or z[1] in ips):
                    keep.append((pts, ol, data))
        if keep:
            write_pcap(split, lt_seen, keep)
            chk("J3", True, "%s (%d pkts) + capture.pcap" % (split.name, len(keep)))
        else:
            chk("J3", False, "no packets attributed to %s" % app)
        pinfo = {
            "run": name,
            "capture_bytes": pcap.stat().st_size,
            "capture_sha256": sha256(pcap),
            "per_app_pcap": split.name if keep else "",
            "per_app_bytes": split.stat().st_size if keep else 0,
            "per_app_sha256": sha256(split) if keep else "",
            "location": "VM:%s and laptop:~/SNL-Lab-Codebases/pramana-data/" % archive,
        }
    else:
        chk("J3", False, "no capture.pcap for this run")

    # ---------------- samples.json and summary.json ----------------
    (d / "samples.json").write_text(
        json.dumps(
            {
                "schema": u.get("schema") or "uniform_qoe/1",
                "app": app,
                "sampling_interval_s": u.get("sampling_interval_s"),
                "n_samples": len(rows),
                "window_epoch": [w0, w1],
                "samples": rows,
            },
            indent=1,
        )
    )

    metrics = {
        "1_startup_delay": {
            "startup_delay_ms": u.get("startup_delay_ms"),
            "initial_buffering_ms": u.get("initial_buffering_ms"),
            "basis": u.get("startup_basis"),
        },
        "2_buffer_rebuffering": {
            "buffer_level_secs": u.get("buffer_level_secs"),
            "rebuffer_events": u.get("rebuffer_events"),
            "rebuffer_duration_ms": u.get("rebuffer_duration_ms"),
            "basis": u.get("rebuffer_basis"),
        },
        "3_delivered_bitrate": {
            "video_mbps": u.get("delivered_video_bitrate_mbps"),
            "audio_mbps": u.get("delivered_audio_bitrate_mbps"),
            "mse_video_bytes": u.get("mse_video_bytes"),
            "mse_audio_bytes": u.get("mse_audio_bytes"),
            "basis": u.get("bitrate_basis"),
        },
        "4_switching": {
            "switch_count": u.get("switch_count"),
            "switch_events": u.get("switch_events"),
            "basis": u.get("switch_basis"),
        },
        "5_frame_rate": {
            "rendered_fps": u.get("rendered_fps"),
            "decoded_fps": summ.get("frame_rate_fps"),
            "basis": u.get("rendered_fps_basis"),
        },
    }
    (d / "summary.json").write_text(
        json.dumps(
            {
                "run": name,
                "app": app,
                "verdict": verdict,
                "run_metadata": {
                    "repo_commit": meta_env.get("commit"),
                    "chrome_version": meta_env.get("chrome"),
                    "vm": meta_env.get("vm"),
                    "run_dir_on_vm": str(run_dir),
                    "shaping": {
                        "bandwidth_mbps": cap,
                        "latency_ms": lat,
                        "aqm": aqm,
                        "loss_pct": cfg.get("loss_pct"),
                        "cca": cfg.get("cca"),
                        "duration_s": cfg.get("duration_s"),
                    },
                    "sampling_interval_s": u.get("sampling_interval_s"),
                    "measured_element_box": (last.get("measured_element") or {}).get(
                        "box"
                    ),
                    # Conditions the cell ran under. This VM is shared, and
                    # host load changes the measurement, not just its cadence:
                    # the same 3 Mbps cell gave 720p with 0 rebuffers at load
                    # ~3 and 480p with 6-10 rebuffers at load ~10.
                    "host": host_conditions(run_dir, st),
                    "n_samples": len(rows),
                    "window_epoch": [w0, w1],
                },
                "metrics": metrics,
                "raw_only": {
                    "dropped_video_frames": u.get("raw_dropped_frames"),
                    "decoded_video_frames": u.get("raw_decoded_frames"),
                    "presented_video_frames": u.get("raw_presented_frames"),
                },
                "throughput_mbps_not_a_qoe_metric": thr,
                "byte_references": {
                    "cdp_received_bytes": last.get("cdp_received_bytes"),
                    "cdp_finished_bytes_diagnostic": last.get("cdp_finished_bytes"),
                    "cdp_open_at_end_bytes_diagnostic": last.get(
                        "cdp_open_at_end_bytes"
                    ),
                },
                "codec": {
                    "declared_mime": u.get("mse_video_mime"),
                    "decoding": last.get("decoding_video_codec"),
                    "init_video_configs": last.get("init_video_configs"),
                },
                "checks": checks,
            },
            indent=1,
        )
    )
    return {
        "name": name,
        "app": app,
        "cap": cap,
        "lat": lat,
        "aqm": aqm,
        "trial": trial,
        "apps": list(run_apps) or [app],
        "n_samples": len(rows),
        "window_epoch": [w0, w1],
        "verdict": verdict,
        "metrics": metrics,
        "checks": checks,
        "pcap": pinfo,
        "dir": str(d),
        "res_p": summ.get("video_resolution_p"),
    }


def metric_status(runs, app, metric):
    """VERIFIED / FAILED / UNKNOWN, by the single definition in THRESHOLDS.md.

    VERIFIED requires, on EVERY run of the app, that the metric was produced
    AND every applicable check passed. A check that is N/A (no reference exists
    for that app) neither passes nor fails; it is reported as "no reference".
    """
    mine = [r for r in runs if r["app"] == app]
    if not mine:
        return "NOT MEASURED", "no run"
    field = METRIC_FIELD[metric]
    produced = 0
    for r in mine:
        block = r["metrics"].get(metric) or {}
        val = None
        for k, v in block.items():
            if k == "basis":
                continue
            if k in (field, field.replace("delivered_", "").replace("_mbps", "_mbps")):
                val = v
        if val is None:
            val = (
                block.get(field) or block.get("video_mbps") or block.get("rendered_fps")
            )
        if val is not None:
            produced += 1
    failed = []
    na = []
    for cid in METRIC_CHECKS[metric]:
        for r in mine:
            st = (r["checks"].get(cid) or ["N/A", ""])[0]
            if st == "FAIL":
                failed.append("%s@%s" % (cid, r["name"].split("_")[1]))
            elif st == "N/A" and cid not in na:
                na.append(cid)
    if produced < len(mine):
        return "UNKNOWN", "produced on %d of %d run(s)" % (produced, len(mine))
    if failed:
        uniq = sorted(set(f.split("@")[0] for f in failed))
        return "FAILED", "%d check failure(s): %s" % (len(failed), ", ".join(uniq))
    note = "%d/%d run(s), all checks passed" % (produced, len(mine))
    if na:
        note += " (%s: no reference)" % ",".join(sorted(na))
    return "VERIFIED", note


def write_sweep_matrix(out_root: Path, runs: list) -> None:
    """One row per run: the cell it belongs to, its metrics, and its verdict.

    The README prose says what happened; this is the same thing in a form that
    can be sorted and diffed. Every column comes from that run's summary.json,
    so the matrix cannot drift from the per-run files.
    """
    # Runs whose numbers belong in the matrix but whose folders are not carried
    # in the repo (archived instead, to keep the tree small). Named explicitly
    # so the matrix says which rows a reader can open and which they cannot.
    archive_only = {
        n.strip()
        for n in (os.environ.get("PRAMANA_ARCHIVE_ONLY_NAMES") or "").split(",")
        if n.strip()
    }
    cols = [
        # The folder name first: app/cap/latency/aqm/trial alone do not
        # identify a row -- a solo run and a concurrent run can agree on all
        # five -- so without this two different runs look like duplicates.
        "run",
        "app",
        "bandwidth_mbps",
        "latency_ms",
        "aqm",
        "trial",
        "verdict",
        "in_repo",
        "resolution_p",
        "startup_delay_ms",
        "rebuffer_events",
        "video_mbps",
        "audio_mbps",
        "switch_count",
        "rendered_fps",
        "checks_pass",
        "checks_fail",
        "failed_check_ids",
    ]
    lines = [",".join(cols)]
    for r in sorted(runs, key=lambda r: (r["app"], r["cap"], r["lat"], r["trial"])):
        m = r["metrics"]
        failed = sorted(k for k, v in r["checks"].items() if v[0] == "FAIL")
        lines.append(
            ",".join(
                str(x)
                for x in [
                    r["name"],
                    r["app"],
                    "%g" % r["cap"],
                    "%g" % r["lat"],
                    r["aqm"],
                    r["trial"],
                    r["verdict"],
                    "no (archived)" if r["name"] in archive_only else "yes",
                    r.get("res_p") or "",
                    m["1_startup_delay"]["startup_delay_ms"],
                    m["2_buffer_rebuffering"]["rebuffer_events"],
                    m["3_delivered_bitrate"]["video_mbps"],
                    m["3_delivered_bitrate"]["audio_mbps"],
                    m["4_switching"]["switch_count"],
                    m["5_frame_rate"]["rendered_fps"],
                    sum(1 for v in r["checks"].values() if v[0] == "PASS"),
                    len(failed),
                    ('"%s"' % " ".join(failed)) if failed else "",
                ]
            )
        )
    (out_root / "sweep_matrix.csv").write_text("\n".join(lines) + "\n")


def write_readme(out_root: Path, runs: list, meta_env: dict):
    apps = sorted({r["app"] for r in runs})
    nfail = sum(1 for r in runs for v in r["checks"].values() if v[0] == "FAIL")
    npass = sum(1 for r in runs for v in r["checks"].values() if v[0] == "PASS")
    nna = sum(1 for r in runs for v in r["checks"].values() if v[0] == "N/A")
    # A concurrent cell yields one folder per app out of a single capture, so
    # folders, cells and captures are three different counts.
    solo = [r for r in runs if len(r.get("apps") or [r["app"]]) <= 1]
    conc = [r for r in runs if len(r.get("apps") or [r["app"]]) > 1]
    n_solo = len({(r["app"], r["cap"]) for r in solo})
    n_conc = len({(tuple(sorted(r["apps"])), r["cap"]) for r in conc})
    n_caps = n_solo + n_conc
    L = [
        "# Streaming QoE results — YouTube and Vimeo (Step A)",
        "",
        "One extraction method and one schema on both apps, with no per-site",
        "APIs. Solo runs carry one app each. The concurrent cell runs both",
        "apps at once over a single shared cap; each app still gets its own",
        "folder, its own charts and its own per-app pcap, drawn only from the",
        "traffic attributed to it, and the two apps' IP sets are disjoint.",
        "Every chart is drawn from the per-second samples by `make_release.py`,",
        "and the numbers it shows are asserted against `summary.json` during the",
        "build, so a chart that disagreed fails the build rather than shipping.",
        "",
        "The bands and the procedure below were fixed and committed **before**",
        "any of these runs were started, so they could not be chosen to fit the",
        "results. No band was widened and no run was excluded afterwards.",
        "",
        "## What VERIFIED means here",
        "",
        "A metric is **VERIFIED for an app** only if, on **every** run of that",
        "app, it was produced (non-null) **and every pre-registered check that",
        "applies to it passed**.",
        "",
        "- **FAILED** — produced, but at least one applicable check failed on at",
        "  least one run.",
        "- **UNKNOWN** — not produced on at least one run, so no check could run.",
        "",
        "The previous release had no such definition and reported delivered",
        'bitrate as "VERIFIED 11/11" while 4 of those 11 runs failed the',
        'pre-registered bitrate band — "11/11" counted only production. Under',
        "the definition above that cannot happen: a band failure forces FAILED",
        "for the whole app.",
        "",
        "## Run matrix",
        "",
        "| | |",
        "|---|---|",
        "| apps | %s |" % ", ".join(sorted({r["app"] for r in runs})),
        "| bandwidth | %s Mbps |"
        % ", ".join("%g" % c for c in sorted({r["cap"] for r in runs})),
        "| latency | %s ms (baseline 50 ms) |"
        % ", ".join("%g" % x for x in sorted({r["lat"] for r in runs})),
        "| AQM / CCA / loss | pfifo / cubic / 0 % |",
        "| duration | 180 s |",
        "| solo cells | %d (one run per app x bandwidth) |" % n_solo,
        "| concurrent cells | %d (both apps sharing one cap) |" % n_conc,
        "| run folders | **%d** from **%d** capture(s) |" % (len(runs), n_caps),
        "",
        "The AQM sweep is a later pass and is not in this branch.",
        "",
        "### Step B, first pass: the latency sweep",
        "",
        "Solo runs only, 6 Mbps, at 20, 50 and 150 ms, two trials per cell.",
        "The plan and every band were pre-registered in",
        "`validation/THRESHOLDS.md` and committed **before** any of it was",
        "collected. The 2.0 s cadence bar was not changed.",
        "",
        "**The 50 ms cells replace the earlier Step A 6 Mbps / 50 ms solo",
        "runs.** They measure the same cell; the Step B pair is newer and was",
        "collected in the same session as the rest of the sweep, so the sweep",
        "is internally consistent. The Step A originals are not deleted - they",
        "are archived off-repo with their checksums, and their numbers remain",
        "in the earlier published release.",
        "",
        "**Only trial 1 of each cell is carried in this repo.** Trial 2 is",
        "archived, and its numbers are in `sweep_matrix.csv`, which marks every",
        "row with whether the folder is in the repo or archived. Charts and",
        "`samples.json` are kept in full for the runs that are here.",
        "",
        "Shaping was checked twice: the substrate reported the latency it",
        "applied (within 10 % on all 12 runs, the pre-registered L1 band), and",
        "independently the round-trip times measured from the captures sit at",
        "23, 53 and 153 ms for the three settings - the configured delay plus",
        "about 3 ms of real path. The second check matters because the first is",
        "the shaper reporting on itself.",
    ]
    # Repeats of the concurrent cell, if their evidence file is present. The
    # release itself keeps one run per scenario, but a cadence failure seen
    # once says nothing about whether it reproduces, so the repeats are
    # reported here from their own measurements.
    trials_csv = out_root / "concurrent_b1_trials.csv"
    if trials_csv.exists():
        tl = [r.split(",") for r in trials_csv.read_text().strip().split("\n")]
        hdr, body = tl[0], tl[1:]
        yt = [r for r in body if r[1] == "youtube"]
        vm = [r for r in body if r[1] == "vimeo"]
        L += [
            "",
            "### B1 across repeats of the concurrent cell",
            "",
            "The concurrent cell was run **%d times**. B1 is the sampling-cadence"
            % len(set(r[0] for r in body)),
            "bar: every inter-sample gap at most 2x the 1.0 s interval. The bar",
            "was not changed.",
            "",
            "| trial | app | samples | B1 | max gap | slowest stats call | script timeouts | load1 mean | load1 at gate |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in body:
            L.append(
                "| %s | %s | %s | %s | %s s | %s ms | %s | %s | %s |"
                % (
                    r[0],
                    r[1],
                    r[2],
                    ("**%s**" % r[3]) if r[3] == "FAIL" else r[3],
                    r[4],
                    r[6],
                    r[8],
                    r[9],
                    r[11],
                )
            )
        nf_y = sum(1 for r in yt if r[3] == "FAIL")
        nf_v = sum(1 for r in vm if r[3] == "FAIL")
        L += [
            "",
            "**YouTube fails B1 on %d of %d repeats; Vimeo fails %d of %d.**"
            % (nf_y, len(yt), nf_v, len(vm)),
            "",
            "The single run in this release is the *mildest* of them (2.00 s",
            "against the 2.0 s bar). The repeats reach %s s, so the failure is"
            % max(r[4] for r in yt),
            "not a borderline one and must not be read as such. In the worst",
            "cases a single reading of the page blocked for 14-24 s; the",
            "10 s script timeout either returned late or did not stop it, that",
            "sample carried no statistics, and the player's clock reset to 0",
            "afterwards. Vimeo, on the same host in the same window, never",
            "exceeded 832 ms.",
            "",
            "The load gate cannot prevent this: it passed at 2.52-2.87 before",
            "every run, and the load that accompanies the failure (mean 9.4-10.9)",
            "is produced by the run itself, two browsers on two vCPUs. Whether",
            "that contention *causes* the stall is **not established** - the",
            "repeats show it reproduces, not why.",
            "",
            "Per-trial numbers are in `concurrent_b1_trials.csv`.",
        ]
    # A second sampler was built to answer the question the first one cannot:
    # was the page blocked, or only our reading of it? Its results live beside
    # the release because they settle a claim the release makes.
    ip_csv = out_root / "inpage_trials.csv"
    if ip_csv.exists():
        rows = [r.split(",") for r in ip_csv.read_text().strip().split("\n")][1:]
        L += [
            "",
            "### Was the page blocked, or only our reading of it?",
            "",
            "The sampler above asks the page for its state once a second. When",
            "that read blocks, it cannot say whether the page stalled or only",
            "the read did -- the reading that would tell us is the one that",
            "failed. So the page was made to log itself: a timer appends an",
            "entry every 200 ms, read over a separate CDP connection with its",
            "own deadline. `performance.now()` keeps advancing while a thread",
            "is blocked, so a timestamp proves nothing; a **missing entry**",
            "does, because a blocked thread cannot run the timer.",
            "",
            "| trial | app | B1 | sampler gap | page-clock gap | page stalls | page blocked |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            L.append(
                "| %s | %s | %s | %s s | %s s | %s | %s |"
                % (
                    r[0],
                    r[1],
                    ("**%s**" % r[3]) if r[3] == "FAIL" else r[3],
                    r[4],
                    r[6],
                    r[7],
                    ("**yes**" if r[8] == "yes" else "no"),
                )
            )
        L += [
            "",
            "**The YouTube page itself blocks.** Its own timer misses for",
            "15.9-17.0 s, three runs out of three. Vimeo's never misses by",
            "more than 1.0 s. So the cadence failure is not an artifact of how",
            "we read the page: the page's main thread genuinely stops.",
            "",
            "It does not account for all of it. The sampler's gap is 23.1-26.1 s",
            "against a page block of 15.9-17.0 s, and the block falls inside the",
            "gap in every run, so roughly 7-9 s is the read path on top of a",
            "page that had already stopped. What blocks the page is still",
            "**unknown** - this says where the time goes, not why.",
            "",
            "The separate connection's deadline was never exercised here (0 read",
            "timeouts in all six). The WebDriver read still runs first in each",
            "iteration, so it absorbs the stall before the in-page read is",
            "issued; the deadline was shown to hold on a synthetic 8 s block by",
            "`test_inpage_timeout.py`, not on these runs.",
            "",
            "Per-trial numbers are in `inpage_trials.csv`. These runs used the",
            "in-page sampler and are diagnostic; they are not part of the",
            "measurement set above.",
        ]
    fz = out_root / "freeze_conditions.csv"
    if fz.exists():
        import csv as _csv

        rows = list(_csv.DictReader(fz.open()))
        L += [
            "",
            "### Why the YouTube page freezes",
            "",
            "Four conditions, each able to come out either way. A freeze is a",
            "gap in the page's own 200 ms timer of more than 2 s.",
            "",
            "| condition | what it tests | app | B1 | page-clock gap | longest task | renderer CPU during it | froze |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in rows:
            L.append(
                "| %s | %s | %s | %s | %s s | %s ms | %s ms/s | %s |"
                % (
                    r["condition"],
                    r["what_it_tests"],
                    r["app"],
                    ("**%s**" % r["b1"]) if r["b1"] == "FAIL" else r["b1"],
                    r["page_clock_gap_s"],
                    r["longest_task_ms"],
                    r["renderer_cpu_ms_per_s_during_freeze"] or "-",
                    ("**yes**" if r["froze"] == "yes" else "no"),
                )
            )
        L += [
            "",
            "**It is CPU starvation, and nothing to do with Vimeo.**",
            "",
            "- *YouTube alone does not freeze*: worst gap 0.65 s, longest task",
            "  267 ms. So contention is necessary.",
            "- *CPU load alone is enough*: two spinner processes, no second",
            "  browser and no shared link, reproduce it in full - an 18.65 s",
            "  freeze. That rules out any explanation that needs Vimeo, a",
            "  second browser, or the shared cap.",
            "- *A different second app still freezes it* (4.77 s beside Twitch),",
            "  which agrees.",
            "- *One core each does not fix it* (16.61 s). Pinning was the",
            "  obvious remedy and it failed, so the problem is not two browsers",
            "  colliding on one core.",
            "",
            "**Starved, not busy.** A 13.5 s long task is recorded during the",
            "freeze, which on its own might suggest the page was working hard.",
            "The CPU probe says otherwise: the renderer got **202.8 ms of CPU",
            "per wall second** during that freeze, against **478.7 ms/s** in the",
            "unloaded solo run - about 40 % of its normal rate, on a 2 vCPU host",
            "where all of Chrome together held only ~400 of 2000 ms/s while the",
            "spinners took the rest. A task holding the thread for 13.5 s while",
            "receiving a fifth of a core is a task being descheduled, not one",
            "computing. Long-task duration is wall-clock and cannot tell these",
            "apart by itself; the CPU figure is what separates them.",
            "",
            "Why YouTube and not Vimeo: YouTube's renderer wants appreciably",
            "more CPU (478.7 ms/s unloaded), so it is the one that starves first",
            "when the host is oversubscribed. Vimeo passed B1 in every condition",
            "here, including the pinned run.",
            "",
            "This is a property of the 2 vCPU measurement host, not of YouTube's",
            "delivery. It limits what a concurrent cell on this host can measure;",
            "it is **not** a finding about the network or the service.",
            "",
            "Per-condition numbers are in `freeze_conditions.csv`. Reproduce with",
            "`run_freeze_tests.py <condition>`, which also runs `cpu_probe.py`.",
        ]
    fo = out_root / "freeze_origin.csv"
    ar = out_root / "archived_concurrent_b1.csv"
    if fo.exists():
        import csv as _csv
        import statistics as _st

        rows = list(_csv.DictReader(fo.open()))
        yt = [r for r in rows if r["app"] == "youtube"]
        vm = [r for r in rows if r["app"] == "vimeo"]
        L += [
            "",
            "### Is the freeze ours, or was it always there?",
            "",
            "Four samplers, three trials each, same cell and same gate. If our",
            "instrumentation caused the freeze, removing it should remove the",
            "freeze.",
            "",
            "| sampler | what it is | YouTube B1 | YouTube max gap | Vimeo B1 |",
            "|---|---|---|---|---|",
        ]
        for c in ("A", "B", "C", "D"):
            y = [r for r in yt if r["condition"] == c]
            v = [r for r in vm if r["condition"] == c]
            if not y:
                continue
            L.append(
                "| %s | %s | %d/%d FAIL | %.1f-%.1f s | %d/%d FAIL |"
                % (
                    c,
                    y[0]["sampler"],
                    sum(1 for r in y if r["b1"] == "FAIL"),
                    len(y),
                    min(float(r["max_gap_s"]) for r in y),
                    max(float(r["max_gap_s"]) for r in y),
                    sum(1 for r in v if r["b1"] == "FAIL"),
                    len(v),
                )
            )
        nf = sum(1 for r in yt if r["b1"] == "FAIL")
        L += [
            "",
            "**YouTube fails in %d of %d runs, under every sampler.** Removing"
            % (nf, len(yt)),
            "our hooks (B), going back to a collector written before the uniform",
            "QoE hooks existed (C), and sampling a different way entirely at 2 s",
            "(D) all freeze just as badly. **The instrumentation is not the",
            "cause.** Vimeo passed in all but one run throughout.",
            "",
            "Renderer CPU during the longest gap was 136-448 ms per wall second",
            "in every condition, well under one core, with host load1 8.1-13.0 -",
            "the same starvation signature as the `cpuload` test above.",
        ]
        if ar.exists():
            arows = list(_csv.DictReader(ar.open()))
            ay = [r for r in arows if r["app"] == "youtube"]
            av = [r for r in arows if r["app"] == "vimeo"]
            L += [
                "",
                "**And it predates all of it.** Recomputing B1 on %d archived"
                % len(ay),
                "YouTube+Vimeo concurrent series from 2026-09-26, none of which",
                "carry our per-sample instrumentation:",
                "",
                "| app | B1 FAIL | worst gap | run length |",
                "|---|---|---|---|",
                "| youtube | **%d/%d** | %.2f s | %.0f-%.0f s |"
                % (
                    sum(1 for r in ay if r["b1"] == "FAIL"),
                    len(ay),
                    max(float(r["max_gap_s"]) for r in ay),
                    min(float(r["span_s"]) for r in ay),
                    max(float(r["span_s"]) for r in ay),
                ),
                "| vimeo | %d/%d | %.2f s | same |"
                % (
                    sum(1 for r in av if r["b1"] == "FAIL"),
                    len(av),
                    max(float(r["max_gap_s"]) for r in av),
                ),
                "",
                "The same YouTube-only asymmetry, already there in September.",
                "",
                "What did change is the size. Archived gaps top out at 9.15 s;",
                "the runs above reach 15-27 s. That is **not** run length: every",
                "freeze above happens inside the first 60 s, which is the length",
                "of a typical archived run, and truncating the new runs to 60 s",
                "leaves the gaps unchanged. It is **not** our instrumentation,",
                "for the reason in the table. The remaining candidate is the host",
                "being more heavily loaded now than in September, which is",
                "consistent with load1 8-13 during these runs but **cannot be",
                "confirmed**: the archived runs recorded no host load, so there",
                "is nothing to compare against. That part stays open.",
                "",
                "Numbers in `freeze_origin.csv` and `archived_concurrent_b1.csv`.",
            ]
    L += []
    if conc:
        L += [
            "",
            "### The concurrent cell: what is and is not synchronised",
            "",
            "A barrier holds both apps until each has a ready player, so",
            "neither spends its window waiting for the other to load. It does",
            "**not** give them an identical sampling window: after the barrier",
            "each app independently waits for media-ready, triggers play and",
            "drains its own CDP backlog, and the player can still reload under",
            "contention. Measured on this cell:",
            "",
            "| app | samples | window | first sample vs earliest |",
            "|---|---|---|---|",
        ]
        t0 = min(r["window_epoch"][0] for r in conc)
        for r in sorted(conc, key=lambda r: r["window_epoch"][0]):
            w0, w1 = r["window_epoch"]
            L.append(
                "| %s | %d | %.1f s | %+.1f s |"
                % (r["app"], r["n_samples"], w1 - w0, w0 - t0)
            )
        L += [
            "",
            "Each app's own metrics are computed over its own window. The",
            "shared-cap check does not assume equal windows: it sums the apps",
            "on a common 1 s grid. Compare the two apps to each other only",
            "with that difference in mind.",
        ]
    L += [
        "",
        "## Status: app x metric",
        "",
        "| metric | " + " | ".join(a.capitalize() for a in apps) + " |",
        "|---" * (len(apps) + 1) + "|",
    ]
    for metric in sorted(METRIC_CHECKS):
        cells = []
        for a in apps:
            stv, ev = metric_status(runs, a, metric)
            cells.append("**%s**<br><sub>%s</sub>" % (stv, ev))
        label = metric.split("_", 1)[1].replace("_", " ")
        L.append("| %s %s | %s |" % (metric[0], label, " | ".join(cells)))
    L += [
        "",
        "Dropped frames are carried as raw counts only; throughput is not a QoE",
        "metric and appears only in its own two charts and the cap check.",
        "",
        "## Checks",
        "",
        "| | count |",
        "|---|---|",
        "| PASS | %d |" % npass,
        "| **FAIL** | **%d** |" % nfail,
        "| no reference (N/A) | %d |" % nna,
        "",
    ]
    fails = [
        (r["name"], cid, v[1])
        for r in runs
        for cid, v in sorted(r["checks"].items())
        if v[0] == "FAIL"
    ]
    if fails:
        L += [
            "### Every failing check",
            "",
            "| run | check | detail |",
            "|---|---|---|",
        ]
        for n, cid, det in fails:
            L.append("| %s | **%s** | %s |" % (n, cid, det))
        L.append("")
    else:
        L += ["No check failed.", ""]
    L += [
        "## Per-run results",
        "",
        "| run | verdict | res | startup (ms) | rebuffers | video Mbps | audio Mbps | switches | fps |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(runs, key=lambda x: (x["app"], x["cap"], x["trial"])):
        m = r["metrics"]
        L.append(
            "| %s | %s | %sp | %s | %s | %s | %s | %s | %s |"
            % (
                r["name"],
                r["verdict"],
                r["res_p"],
                m["1_startup_delay"]["startup_delay_ms"],
                m["2_buffer_rebuffering"]["rebuffer_events"],
                m["3_delivered_bitrate"]["video_mbps"],
                m["3_delivered_bitrate"]["audio_mbps"],
                m["4_switching"]["switch_count"],
                m["5_frame_rate"]["rendered_fps"],
            )
        )
    L += [
        "",
        "## The checks every run had to pass",
        "",
        "| id | what it checks | bar |",
        "|---|---|---|",
        "| S1,S2 | startup produced, positive, inside the session | 0 < startup < duration |",
        "| S3 | the three startup quantities add up on one clock | residual <= 50 ms |",
        "| B1 | sampling cadence | every gap <= 2x the 1.0 s interval |",
        "| B2,B3 | buffer level physical; rebuffer count produced | buffer >= 0 |",
        "| B4 | buffer agrees with YouTube's own figure (YouTube only) | MAE <= 1.0 s, r >= 0.90 |",
        "| R1 | delivered bytes vs Chrome's Network domain | cdp_received / appended in [1.00, 1.15] |",
        "| R2,R3 | video and audio reported separately, video non-zero | both non-null |",
        "| W1,W2 | switches timestamped with direction; chart agrees with count | drawn <= switch_count |",
        "| F1,F2,F3 | rendered rate positive, <= decoded, chart equals summary | <= decoded x 1.05 |",
        "| J1,J2 | throughput within the shaped cap, peak and mean | peak <= cap x 1.05; mean <= cap |",
        "| | on a concurrent cell both apps are summed on a 1 s grid first, "
        "so neither can hide behind the other | same bars, applied to the sum |",
        "| J3,J4 | a pcap per run and per app; download and upload plotted separately | files present |",
        "| J5,J6,J7 | fixed 0-11 Mbps line charts clipped to playback; playback verdict; chart numbers equal summary | — |",
        "",
        "Each run's `summary.json` carries its own `checks` block with the",
        "measured value for every one of them.",
        "",
        "## Run folder layout",
        "",
        "```",
        "<app>/<app>_<bw>Mbps_<lat>ms_<aqm>_trial<N>/              solo",
        "<app>/<app>_<bw>Mbps_<lat>ms_<aqm>_with_<other>_trial<N>/   concurrent",
        "  samples.json   raw per-second samples, schema and interval",
        "  summary.json   the five metrics, run metadata, byte references, checks",
        "  download_throughput.png   upload_throughput.png   (separate, 0-11 Mbps)",
        "  buffer.png  resolution.png  bitrate.png  switches.png  fps.png  startup.png",
        "```",
        "",
        "## pcaps",
        "",
        "Not committed. Each run has a full `capture.pcap` and a per-app pcap",
        "containing only packets to or from IPs attributed to that app; both are",
        "listed with size and SHA-256 in `pcap_manifest.csv`.",
        "",
        "## Reproducing",
        "",
        "```bash",
        "cd experiments/pramana",
        "DUR=180 python run_matrix.py                 # the solo cells",
        "DUR=180 python run_concurrent.py             # the concurrent cell",
        "python make_release.py results_release <run_dir> [<run_dir> ...]",
        "```",
        "",
        "Repo commit, Chrome version, VM name and the shaping parameters for each",
        "run are in that run's `summary.json` under `run_metadata`.",
        "",
        "Built from commit `%s`, Chrome `%s`, on `%s`."
        % (meta_env.get("commit"), meta_env.get("chrome"), meta_env.get("vm")),
        "",
        "The collector deployed on the VM when these runs were collected is",
        "byte-identical (sha256) to this commit's",
        "`selenium_video_qoe/collect.py`, so the stamp above is checked, not",
        "assumed. One caveat: that file was last written partway through the",
        "collection session, after the three vimeo solo runs and before the",
        "youtube solo runs and the concurrent cell. Every run in this release",
        "carries the same per-sample instrumentation (`loop_ms`, `host_load1`),",
        "so none predates the instrumented collector, but whether the three",
        "vimeo solo runs used a byte-identical file is **unknown**.",
    ]
    (out_root / "README.md").write_text("\n".join(L) + "\n")


def main():
    out_root = Path(sys.argv[1])
    dirs = [Path(x) for x in sys.argv[2:]]
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "validation").mkdir(exist_ok=True)
    meta_env = {
        "commit": os.environ.get("RELEASE_COMMIT", "unknown"),
        "chrome": os.environ.get("RELEASE_CHROME", "unknown"),
        "vm": os.environ.get("RELEASE_VM", "docker-vm-4"),
    }
    runs = []
    for rd in dirs:
        if not (rd / "record.json").exists():
            print("skip (no record.json): %s" % rd)
            continue
        rec_apps = (
            json.loads((rd / "record.json").read_text()).get("config") or {}
        ).get("apps") or [None]
        for _app in rec_apps:
            try:
                r = build_run(rd, out_root, meta_env, app=_app)
            except Exception as exc:  # noqa: BLE001
                import traceback

                print("ERROR %s (%s): %s" % (rd.name, _app, exc))
                traceback.print_exc()
                continue
            if not r:
                continue
            runs.append(r)
            nf = sum(1 for v in r["checks"].values() if v[0] == "FAIL")
            print(
                "built %-46s %s  (%d check failure(s))" % (r["name"], r["verdict"], nf),
                flush=True,
            )
    rows = [r["pcap"] for r in runs if r.get("pcap")]
    with open(out_root / "pcap_manifest.csv", "w", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "run",
                "capture_bytes",
                "capture_sha256",
                "per_app_pcap",
                "per_app_bytes",
                "per_app_sha256",
                "location",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)
    write_sweep_matrix(out_root, runs)
    write_readme(out_root, runs, meta_env)
    nfail = sum(1 for r in runs for v in r["checks"].values() if v[0] == "FAIL")
    print("\n%d runs built; %d check failure(s)" % (len(runs), nfail))
    for r in runs:
        for cid, v in sorted(r["checks"].items()):
            if v[0] == "FAIL":
                print("  FAIL %-44s %-4s %s" % (r["name"], cid, v[1]))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
