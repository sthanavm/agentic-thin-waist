import sys, collections
from scapy.all import PcapReader, IP, IPv6

pcap, sess_start, sess_end = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
first = last = None
src_bytes = collections.Counter()
pkts = []
n = 0
with PcapReader(pcap) as rd:
    for p in rd:
        n += 1
        t = float(p.time)
        if first is None: first = t
        last = t
        ip = p.getlayer(IP) or p.getlayer(IPv6)
        if ip is None:
            pkts.append((t, None, None, len(p))); continue
        src_bytes[ip.src] += len(p)
        pkts.append((t, ip.src, ip.dst, len(p)))
print(f"packets {n}   capture epoch {first:.3f} .. {last:.3f}  ({last-first:.1f}s)")
print(f"session  epoch {sess_start:.3f} .. {sess_end:.3f}")
print(f"OFFSET: session t=0 is capture t={sess_start-first:+.1f}s;  capture t=0 is session t={first-sess_start:+.1f}s")
print("\ntop talkers by bytes sent:")
for ips, b in src_bytes.most_common(6):
    print(f"   {ips:42s} {b/1e6:8.2f} MB")
# local = the private/namespace address that sends the most
def is_local(a):
    return a and (a.startswith("10.") or a.startswith("192.168.") or a.startswith("172.1") or a.startswith("172.2") or a.startswith("172.3"))
local = next((a for a, _ in src_bytes.most_common() if is_local(a)), None)
print(f"\nlocal (ns1) address detected: {local}")

up = collections.Counter(); down = collections.Counter()
for t, s, d, ln in pkts:
    sec = int(t - first)
    if s == local: up[sec] += ln
    elif d == local: down[sec] += ln
cap_off = sess_start - first
print("\n  cap_t  sess_t     UP bytes    DOWN bytes")
for sec in range(0, int(last - first) + 1):
    st = sec - cap_off
    if sec >= 40:
        print("  %5d  %6.1f  %11d  %12d" % (sec, st, up.get(sec, 0), down.get(sec, 0)))
print("\n=== TOTALS ===")
print(f"  whole capture:  up {sum(up.values())/1e6:.3f} MB   down {sum(down.values())/1e6:.3f} MB")
def window(lo, hi, rel="cap"):
    o = 0 if rel == "cap" else cap_off
    u = sum(v for k, v in up.items() if lo + o <= k <= hi + o)
    d = sum(v for k, v in down.items() if lo + o <= k <= hi + o)
    nz = sum(1 for k in range(int(lo + o), int(hi + o) + 1) if up.get(k, 0) > 0)
    tot = int(hi - lo) + 1
    print(f"  {rel} t={lo}-{hi}s: up {u:,} B ({u*8/1e6/tot:.4f} Mbps avg)  down {d:,} B  |  seconds with UP>0: {nz}/{tot}")
window(45, 60, "cap"); window(45, 60, "sess")
