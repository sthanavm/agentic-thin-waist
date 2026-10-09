#!/usr/bin/env python3
"""Item 2: cross-check startup_delay_ms against the capture, on one clock.

The earlier attempt was invalid: it took "first media byte" as the first packet
to the media peer anywhere in the capture, but capture starts ~40 s BEFORE
navigation, so that packet predated the page. This version maps page time to
wall time explicitly and only counts packets after navigationStart.

page t=0 (navigationStart) in wall time = sample.timestamp - page_now_ms/1000,
which nav_start_page_ms == 0 confirms is the performance-timeline origin. The
mapping is computed from every sample and its spread is reported, so a bad
mapping shows up rather than hiding.
"""

import collections
import json
import pathlib
import statistics
import sys

HOME = pathlib.Path.home() / "SNL-Lab-Codebases/sthanav-agentic-thin-waist"
sys.path.insert(0, str(HOME / "experiments/pramana"))
sys.path.insert(0, str(HOME))
import pramana_helpers as H  # noqa: E402


def ip4(data, linktype):
    off = 14 if linktype == 1 else (16 if linktype == 113 else 0)
    if len(data) < off + 20:
        return None
    if linktype == 1:
        et = int.from_bytes(data[12:14], "big")
        if et == 0x8100:
            off += 4
            et = int.from_bytes(data[16:18], "big")
        if et != 0x0800:
            return None
    if data[off] >> 4 != 4:
        return None
    ihl = (data[off] & 0xF) * 4
    proto = data[off + 9]
    src = ".".join(str(b) for b in data[off + 12 : off + 16])
    dst = ".".join(str(b) for b in data[off + 16 : off + 20])
    l4 = off + ihl
    if len(data) < l4 + 4:
        return None
    sport = int.from_bytes(data[l4 : l4 + 2], "big")
    dport = int.from_bytes(data[l4 + 2 : l4 + 4], "big")
    return proto, src, dst, sport, dport


def samples(path):
    out = []
    for line in pathlib.Path(path).read_text(errors="replace").splitlines():
        if not line.strip() or '"meta"' in line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("stats"):
            out.append(r)
    return out


def main(jsonl, pcap):
    rows = samples(jsonl)
    st_last = rows[-1]["stats"]

    # ---- page clock -> wall clock, with its own spread as a sanity check ----
    offs = [
        r["timestamp"] - (r["stats"].get("page_now_ms") or 0) / 1000.0
        for r in rows
        if r["stats"].get("page_now_ms")
    ]
    nav_wall = statistics.median(offs)
    spread = max(offs) - min(offs)
    print("page->wall mapping from %d samples" % len(offs))
    print(
        "  navigationStart wall = %.3f   spread across samples = %.3f s"
        % (nav_wall, spread)
    )
    if spread > 2.0:
        print("  !! spread too large to trust this mapping")

    fp_ms = st_last.get("first_presentation_ms")
    if fp_ms is None:
        print("no first_presentation_ms; cannot cross-check")
        return
    # first_presentation_ms is presentationTime - S.t0, and S.t0 is
    # performance.now() at PROBE INSTALL, not navigationStart. The probe runs
    # at document-start but that is still some milliseconds in (697 ms on the
    # run this was built against), so the offset has to be added back before
    # the figure can be placed on the page timeline. Omitting it understates
    # every startup figure by probe_t0_page_ms. It cancels in the DIFFERENCE
    # between two startup definitions, which is why an internal-consistency
    # check does not catch it.
    probe_t0 = st_last.get("probe_t0_page_ms") or 0
    fp_from_nav_ms = fp_ms + probe_t0
    frame_wall = nav_wall + fp_from_nav_ms / 1000.0

    # ---- find the media peer and its first packet after navigationStart ----
    peer_bytes = collections.Counter()
    pkts = []
    t0 = t1 = None
    for ts, orig_len, linktype, data in H.iter_capture(str(pcap)):
        t0 = ts if t0 is None else t0
        t1 = ts
        r = ip4(data, linktype)
        if not r:
            continue
        proto, src, dst, sport, dport = r
        peer = dst if sport > dport else src
        peer_bytes[peer] += orig_len
        pkts.append((ts, peer, orig_len))
    media_ip, media_bytes = peer_bytes.most_common(1)[0]

    print("capture spans wall %.3f .. %.3f  (%.1f s)" % (t0, t1, t1 - t0))
    print("  navigationStart is %+.1f s into the capture" % (nav_wall - t0))
    print("  media peer %s carried %.2f MB" % (media_ip, media_bytes / 1e6))

    after = [p for p in pkts if p[1] == media_ip and p[0] >= nav_wall]
    before = [p for p in pkts if p[1] == media_ip and p[0] < nav_wall]
    print(
        "  media-peer packets BEFORE navigationStart: %d (%.2f MB) <- what invalidated the earlier check"
        % (len(before), sum(p[2] for p in before) / 1e6)
    )
    if not after:
        print("  no media-peer packet after navigationStart; cannot cross-check")
        return
    first_media_wall = after[0][0]

    print()
    print("STARTUP, four definitions, one clock")
    print(
        "  probe install (document-start) -> first frame        : %8.0f ms  (= startup_delay_ms as stored)"
        % fp_ms
    )
    print("     probe installed at page t = %+.0f ms" % probe_t0)
    print(
        "  navigationStart            -> first presented frame : %8.0f ms"
        % fp_from_nav_ms
    )
    print(
        "  first media-peer packet    -> first presented frame : %8.0f ms  (pcap cross-check)"
        % ((frame_wall - first_media_wall) * 1000.0)
    )
    print(
        "     first media packet at page t = %+.0f ms"
        % ((first_media_wall - nav_wall) * 1000.0)
    )
    # initial_buffering_ms is derived, not a per-sample field: sum the waiting
    # spans that occurred before any media time had elapsed.
    spans = st_last.get("waiting_spans") or []
    ib = sum(
        float(w.get("dur_ms") or 0)
        for w in spans
        if isinstance(w, dict) and float(w.get("ct") or 0) <= 0
    )
    print("  player initial buffer fill (initial_buffering_ms)   : %8.0f ms" % ib)
    rec = pathlib.Path(jsonl).with_name(
        pathlib.Path(jsonl).name.replace(".jsonl", "_record.json")
    )
    if rec.exists():
        pq = json.loads(rec.read_text()).get("player_qoe") or {}
        for app_name, q in pq.items():
            if isinstance(q, dict) and q.get("startup_ms") is not None:
                print(
                    "  legacy startup_ms (counter-advance artifact)        : %8s ms"
                    " <- floored at one sampling interval by construction"
                    % q.get("startup_ms")
                )
    dcl = st_last.get("dom_content_loaded_ms")
    print("  (context) DOMContentLoaded at page t                : %8s ms" % dcl)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
