#!/usr/bin/env python3
"""Measure RTT from a run's pcap, to check the configured latency is real.

L1 as pre-registered only asks the substrate whether it applied what it was
asked for -- that is the shaper reporting on itself. This is the independent
check: the time between a TCP SYN and its SYN-ACK is a round trip over the
shaped path, measured from the packets rather than from the configuration.

What it does NOT assume is how netem's one-way delay maps onto RTT. The number
is reported as measured, and the scaling across 20/50/150 ms is what shows the
shaping is real.
"""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist/experiments/pramana"))
sys.path.insert(0, os.path.expanduser("~/sthanav-agentic-thin-waist"))
import pramana_helpers as H  # noqa: E402
import statistics


def parse(data, lt):
    off = 14 if lt == 1 else (16 if lt == 113 else 0)
    if len(data) < off + 20 or data[off] >> 4 != 4:
        return None
    ihl = (data[off] & 0xF) * 4
    if data[off + 9] != 6:  # TCP only
        return None
    l4 = off + ihl
    if len(data) < l4 + 14:
        return None
    src = ".".join(str(b) for b in data[off + 12 : off + 16])
    dst = ".".join(str(b) for b in data[off + 16 : off + 20])
    sport = int.from_bytes(data[l4 : l4 + 2], "big")
    dport = int.from_bytes(data[l4 + 2 : l4 + 4], "big")
    seq = int.from_bytes(data[l4 + 4 : l4 + 8], "big")
    ack = int.from_bytes(data[l4 + 8 : l4 + 12], "big")
    flags = data[l4 + 13]
    return src, dst, sport, dport, seq, ack, flags


def rtts(pcap):
    syns = {}
    out = []
    for ts, _ol, lt, data in H.iter_capture(str(pcap)):
        r = parse(data, lt)
        if not r:
            continue
        src, dst, sp, dp, seq, ack, fl = r
        syn, a = bool(fl & 0x02), bool(fl & 0x10)
        if syn and not a:
            syns[(src, dst, sp, dp, seq)] = ts
        elif syn and a:
            k = (dst, src, dp, sp, ack - 1)
            if k in syns:
                out.append((ts - syns.pop(k)) * 1000.0)
    return out


if __name__ == "__main__":
    print("%-52s %6s %9s %9s %9s" % ("run", "n", "median", "p10", "p90"))
    for d in sys.argv[1:]:
        p = os.path.join(d, "capture.pcap")
        if not os.path.exists(p):
            print("%-52s  (no capture)" % os.path.basename(d)[:52])
            continue
        v = sorted(rtts(p))
        if not v:
            print("%-52s %6d  no TCP handshakes" % (os.path.basename(d)[:52], 0))
            continue
        print("%-52s %6d %8.1fms %8.1fms %8.1fms" % (
            os.path.basename(d)[:52], len(v), statistics.median(v),
            v[int(0.1 * len(v))], v[int(0.9 * (len(v) - 1))]))
