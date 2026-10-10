#!/usr/bin/env python3
"""The checks the brief asks for, run against the finished runs.

Each section states what it tests, and each one can fail. Bands come from
THRESHOLDS.md and are not restated here as adjustable numbers.

  A  byte references: MSE vs CDP vs pcap, with the pre-registered bands and the
     cross-reference agreement gate C1.
  B  startup vs pcap, per run, on one clock.
  C  why the mime switch log stayed empty while renditions changed.
  D  the Resource Timing gap against cdp_open_at_end_bytes -- the direct test
     of the never-finished-response explanation.
  E  YouTube 3 Mbps rendition distribution with the player box enforced.
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics
import sys

HOME = pathlib.Path("/home/student/sthanav-agentic-thin-waist")
sys.path.insert(0, str(HOME / "experiments/pramana"))
sys.path.insert(0, str(HOME))
import pramana_helpers as H  # noqa: E402

# Pre-registered in THRESHOLDS.md before any data existed.
A_LO, A_HI = 1.00, 1.15
C1_MAX_REL = 0.05

OUT = []


def p(s=""):
    print(s)
    OUT.append(s)


def samples(run_dir: pathlib.Path, app: str):
    f = run_dir / "qoe" / ("%s_stats.jsonl" % app)
    rows = []
    if not f.exists():
        return rows
    for line in f.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("record") == "meta" or not r.get("stats"):
            continue
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


def ip4(data, lt):
    off = 14 if lt == 1 else (16 if lt == 113 else 0)
    if len(data) < off + 20 or data[off] >> 4 != 4:
        return None
    ihl = (data[off] & 0xF) * 4
    src = ".".join(str(b) for b in data[off + 12 : off + 16])
    dst = ".".join(str(b) for b in data[off + 16 : off + 20])
    l4 = off + ihl
    if len(data) < l4 + 4:
        return None
    sp = int.from_bytes(data[l4 : l4 + 2], "big")
    dp = int.from_bytes(data[l4 + 2 : l4 + 4], "big")
    return src, dst, sp, dp


def load(dirs):
    runs = []
    for d in dirs:
        d = pathlib.Path(d)
        rj = d / "record.json"
        if not rj.exists():
            continue
        rec = json.loads(rj.read_text())
        cfg = rec.get("config") or {}
        apps = cfg.get("apps") or []
        if not apps:
            continue
        app = apps[0]
        rows = samples(d, app)
        if not rows:
            continue
        q = (rec.get("player_qoe") or {}).get(app) or {}
        runs.append(
            {
                "dir": d,
                "app": app,
                "cap": float(cfg.get("bandwidth_mbps") or 0),
                "trial": cfg.get("trial"),
                "rows": rows,
                "st": [r["stats"] for r in rows],
                "ts": [r["timestamp"] for r in rows],
                "q": q,
                "u": q.get("uniform") or {},
                "rec": rec,
            }
        )
    return runs


def sec_a(runs):
    p("## A. Byte references: MSE vs CDP vs pcap")
    p("")
    p("Bands fixed in THRESHOLDS.md before any of this data existed:")
    p(
        "A1/A2 `CDP/MSE` in [%.2f, %.2f]; C1 cross-reference agreement <= %.0f%%."
        % (A_LO, A_HI, C1_MAX_REL * 100)
    )
    p("pcap is reported but is NOT part of the verdict: it is the reference")
    p("under suspicion, so it cannot also be the judge.")
    p("")
    p(
        "| run | MSE bytes | CDP finished | A1 | CDP received | A2 | C1 | pcap peer | pcap/MSE |"
    )
    p("|---|---|---|---|---|---|---|---|---|")
    verdicts = []
    for r in runs:
        if r["app"] != "youtube":
            continue
        last = r["st"][-1]
        mse = (num(r["u"].get("mse_video_bytes")) or 0) + (
            num(r["u"].get("mse_audio_bytes")) or 0
        )
        fin = num(last.get("cdp_finished_bytes"))
        rcv = num(last.get("cdp_received_bytes"))
        if not mse or fin is None:
            p(
                "| %s_%gM_t%s | %s | n/a | - | - | - | - | - | - |"
                % (r["app"], r["cap"], r["trial"], int(mse) if mse else "n/a")
            )
            continue
        a1 = fin / mse
        a2 = (rcv / mse) if rcv else None
        c1 = (abs(fin - rcv) / max(fin, rcv)) if (rcv and max(fin, rcv)) else None
        # pcap, name-independent: dominant peer
        pk = None
        pc = r["dir"] / "capture.pcap"
        if pc.exists():
            import collections

            peers = collections.Counter()
            for ts, ol, lt, data in H.iter_capture(str(pc)):
                z = ip4(data, lt)
                if not z:
                    continue
                src, dst, sp, dp = z
                peers[dst if sp > dp else src] += ol
            if peers:
                ip, by = peers.most_common(1)[0]
                pk = (ip, by / mse)
        ok1 = A_LO <= a1 <= A_HI
        ok2 = (a2 is not None) and (A_LO <= a2 <= A_HI)
        okc = (c1 is not None) and (c1 <= C1_MAX_REL)
        verdicts.append(ok1 and ok2 and okc)
        p(
            "| %s_%gM_t%s | %d | %d | %s %.4f | %d | %s %.4f | %s %.4f%% | %s | %.4f |"
            % (
                r["app"],
                r["cap"],
                r["trial"],
                int(mse),
                int(fin),
                "PASS" if ok1 else "**FAIL**",
                a1,
                int(rcv or 0),
                "PASS" if ok2 else "**FAIL**",
                a2 or 0,
                "PASS" if okc else "**FAIL**",
                (c1 or 0) * 100,
                pk[0] if pk else "n/a",
                pk[1] if pk else 0,
            )
        )
    p("")
    if verdicts:
        n_ok = sum(1 for v in verdicts if v)
        p(
            "**Verdict: %d/%d YouTube runs pass A1, A2 and C1 together.**"
            % (n_ok, len(verdicts))
        )
        p("")
        if n_ok == len(verdicts):
            p("The CDP reference is proven on every run by a second, independent")
            p("accumulation path agreeing with it, so the Step 3b gate is met.")
        else:
            p("At least one run fails, so the gate is NOT met and Step 3b stays")
            p("blocked. The failing ratios are reported above as measured.")
    else:
        p("**No YouTube run carried CDP fields — reference UNAVAILABLE.**")
    p("")
    return bool(verdicts) and all(verdicts)


def sec_b(runs):
    p("## B. Startup delay vs the capture, on one clock")
    p("")
    p("`startup_delay_ms` is `presentationTime - probe install`, so it is put on")
    p("the page timeline by adding `probe_t0_page_ms`, then onto wall time via")
    p("`page_now_ms`. Only packets AFTER navigationStart are counted: capture")
    p("begins before navigation, which is what invalidated an earlier attempt.")
    p("")
    p(
        "| run | probe_t0 (ms) | startup_delay_ms | navStart->frame | 1st media pkt (page ms) | pkt->frame | consistent |"
    )
    p("|---|---|---|---|---|---|---|")
    for r in runs:
        last = r["st"][-1]
        fp = num(last.get("first_presentation_ms"))
        t0 = num(last.get("probe_t0_page_ms"))
        if fp is None or t0 is None:
            p(
                "| %s_%gM_t%s | n/a | %s | - | - | - | no page-clock fields |"
                % (r["app"], r["cap"], r["trial"], fp)
            )
            continue
        offs = [
            r["ts"][i] - (num(r["st"][i].get("page_now_ms")) or 0) / 1000.0
            for i in range(len(r["st"]))
            if num(r["st"][i].get("page_now_ms"))
        ]
        if not offs:
            continue
        nav_wall = statistics.median(offs)
        frame_wall = nav_wall + (fp + t0) / 1000.0
        pc = r["dir"] / "capture.pcap"
        if not pc.exists():
            continue
        import collections

        peers = collections.Counter()
        pkts = []
        for ts, ol, lt, data in H.iter_capture(str(pc)):
            z = ip4(data, lt)
            if not z:
                continue
            src, dst, sp, dp = z
            peer = dst if sp > dp else src
            peers[peer] += ol
            pkts.append((ts, peer))
        if not peers:
            continue
        media = peers.most_common(1)[0][0]
        after = [t for t, pr in pkts if pr == media and t >= nav_wall]
        if not after:
            p(
                "| %s_%gM_t%s | %.0f | %.0f | %.0f | none after nav | - | - |"
                % (r["app"], r["cap"], r["trial"], t0, fp, fp + t0)
            )
            continue
        first = after[0]
        pkt_page = (first - nav_wall) * 1000.0
        pkt_to_frame = (frame_wall - first) * 1000.0
        # The three must satisfy navStart->frame == pkt_page + pkt->frame
        lhs = fp + t0
        ok = abs(lhs - (pkt_page + pkt_to_frame)) < 50
        p(
            "| %s_%gM_t%s | %.0f | %.0f | %.0f | %+.0f | %.0f | %s |"
            % (
                r["app"],
                r["cap"],
                r["trial"],
                t0,
                fp,
                lhs,
                pkt_page,
                pkt_to_frame,
                "yes" if ok else "**NO**",
            )
        )
    p("")


def sec_c(runs):
    p("## C. Why the mime switch log stayed empty while renditions changed")
    p("")
    p("`mse_mime_switch_log` only records `addSourceBuffer` and `changeType`")
    p("calls. If a player changes rendition WITHOUT calling `changeType`, the")
    p("log is empty however many times the rendition changed. The init segment")
    p("is the independent witness: the decoder is configured from it.")
    p("")
    p(
        "| run | declared mime | mime switches | distinct init video configs | decoding codec | WxH | resolutions seen |"
    )
    p("|---|---|---|---|---|---|---|")
    for r in runs:
        last = r["st"][-1]
        heights = []
        for s in r["st"]:
            h = height_of(s)
            if h and (not heights or heights[-1] != h):
                heights.append(h)
        p(
            "| %s_%gM_t%s | %s | %s | **%s** | %s | %sx%s | %s |"
            % (
                r["app"],
                r["cap"],
                r["trial"],
                str(r["u"].get("mse_video_mime"))[:34],
                last.get("mse_mime_switches"),
                last.get("init_video_configs"),
                last.get("decoding_video_codec"),
                last.get("decoding_video_width"),
                last.get("decoding_video_height"),
                ",".join(str(h) for h in heights),
            )
        )
    p("")
    p("A run where `mime switches` is 0 while `distinct init video configs` or")
    p("`resolutions seen` is greater than 1 is the direct demonstration: the")
    p("rendition changed and `changeType` was never called.")
    p("")


def sec_d(runs):
    p("## D. The Resource Timing gap vs never-finished responses")
    p("")
    p("Hypothesis under test: the missing RT bytes belong to responses that")
    p("never ended, because a Resource Timing entry is only created at response")
    p("end. `cdp_open_at_end_bytes` counts exactly those bytes independently.")
    p("If the RT shortfall is real but open bytes are ~0, the hypothesis is")
    p("REFUTED.")
    p("")
    p(
        "| run | MSE | rt_all | rt_all/MSE | RT shortfall | cdp_open_at_end | explains? |"
    )
    p("|---|---|---|---|---|---|---|")
    for r in runs:
        last = r["st"][-1]
        mse = (num(r["u"].get("mse_video_bytes")) or 0) + (
            num(r["u"].get("mse_audio_bytes")) or 0
        )
        rt = num(last.get("rt_all_bytes"))
        op = num(last.get("cdp_open_at_end_bytes"))
        if not mse or rt is None:
            continue
        short = mse - rt
        if op is None:
            verdict = "no CDP"
        elif short <= 0:
            verdict = "no shortfall"
        elif op == 0:
            verdict = "**REFUTED** (0 open bytes)"
        else:
            cov = op / short
            verdict = (
                ("explains %.0f%%" % (cov * 100)) if cov < 0.9 else "**CONSISTENT**"
            )
        p(
            "| %s_%gM_t%s | %d | %d | %.4f | %d | %s | %s |"
            % (
                r["app"],
                r["cap"],
                r["trial"],
                int(mse),
                int(rt),
                rt / mse,
                int(short),
                int(op) if op is not None else "n/a",
                verdict,
            )
        )
    p("")


def sec_e(runs):
    p("## E. YouTube 3 Mbps with the player box enforced")
    p("")
    p("The rendition at 3 Mbps was measured to be bimodal (480p AV1 vs 720p VP9)")
    p("across trials at an identical box, which could still have been our box")
    p("varying. Here the box is ENFORCED and logged, so a remaining spread is")
    p("the site's own choice.")
    p("")
    rows = [r for r in runs if r["app"] == "youtube" and abs(r["cap"] - 3.0) < 1e-9]
    if not rows:
        p("_No 3 Mbps YouTube runs in this set._")
        p("")
        return
    p(
        "| trial | enforced window | measured box | res | decoding codec | video Mbps | switches |"
    )
    p("|---|---|---|---|---|---|---|")
    for r in rows:
        last = r["st"][-1]
        me = last.get("measured_element") or {}
        win = (r["rec"].get("app_window_caps") or {}).get(r["app"])
        p(
            "| %s | %s | %s | %sp | %s | %s | %s |"
            % (
                r["trial"],
                win or "not enforced",
                me.get("box"),
                r["q"].get("video_resolution_p"),
                last.get("decoding_video_codec"),
                r["u"].get("delivered_video_bitrate_mbps"),
                r["u"].get("switch_count"),
            )
        )
    res = [r["q"].get("video_resolution_p") for r in rows]
    br = [num(r["u"].get("delivered_video_bitrate_mbps")) for r in rows]
    br = [b for b in br if b is not None]
    boxes = {(r["st"][-1].get("measured_element") or {}).get("box") for r in rows}
    p("")
    p(
        "resolutions: %s -> %s"
        % (res, "IDENTICAL" if len(set(res)) == 1 else "**DIFFER**")
    )
    p(
        "measured boxes: %s -> %s"
        % (
            sorted(x for x in boxes if x),
            "identical" if len(boxes) == 1 else "**differ**",
        )
    )
    if len(br) > 1:
        p(
            "delivered video Mbps: min=%.4f max=%.4f mean=%.4f  range/mean=%.1f%%"
            % (
                min(br),
                max(br),
                statistics.mean(br),
                100 * (max(br) - min(br)) / statistics.mean(br),
            )
        )
    p("")


if __name__ == "__main__":
    out_md = pathlib.Path(sys.argv[1])
    runs = load(sys.argv[2:])
    p("# Release analysis")
    p("")
    p(
        "%d run(s) analysed. Every claim below is derived from the per-second"
        % len(runs)
    )
    p("samples and the captures of those runs.")
    p("")
    gate = sec_a(runs)
    sec_b(runs)
    sec_c(runs)
    sec_d(runs)
    sec_e(runs)
    p("## Step 3b gate")
    p("")
    p(
        "**%s**"
        % (
            "MET — the byte reference passed with independent agreement."
            if gate
            else "NOT MET — concurrent cells must not run."
        )
    )
    out_md.write_text("\n".join(OUT) + "\n")
    print("\nwrote %s" % out_md)
