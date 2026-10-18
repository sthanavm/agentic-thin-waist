#!/usr/bin/env bash
set -euo pipefail
#
# Shaping sanity check — proves tc/htb+IFB actually caps throughput.
# Runs inside a Docker container on the lab VM.
#
# Usage:  /shaping_sanity_check.sh <rate_mbps> <host_ip>
#   rate_mbps  — bandwidth cap to test (e.g. 3, 6, 10)
#   host_ip    — IP of the iperf3 server (the Docker bridge gateway)

RATE_MBPS="${1:?Usage: shaping_sanity_check.sh <rate_mbps> <host_ip>}"
HOST_IP="${2:?Usage: shaping_sanity_check.sh <rate_mbps> <host_ip>}"
TOLERANCE="${3:-10}"   # percent above cap that still counts as PASS

IFACE=eth0
IFB=ifb0
RATE_KBIT=$((RATE_MBPS * 1000))
BURST_K=$((RATE_KBIT / 8))
[ "$BURST_K" -lt 15 ] && BURST_K=15
DURATION=30

echo "================================================================"
echo "  Shaping sanity check: ${RATE_MBPS} Mbps cap"
echo "================================================================"
echo "  Interface:  $IFACE -> $IFB"
echo "  HTB rate:   ${RATE_KBIT} kbit"
echo "  HTB burst:  ${BURST_K}k"
echo "  Duration:   ${DURATION}s"
echo "  Tolerance:  ${TOLERANCE}%"
echo ""

# Install iperf3 if missing
if ! command -v iperf3 &>/dev/null; then
  echo "  Installing iperf3..."
  apt-get update -qq > /dev/null 2>&1
  apt-get install -y -qq iperf3 > /dev/null 2>&1
fi

# Set up IFB shaping
modprobe ifb 2>/dev/null || true
ip link add "$IFB" type ifb 2>/dev/null || true
ip link set "$IFB" up
tc qdisc add dev "$IFACE" handle ffff: ingress 2>/dev/null || true
tc filter add dev "$IFACE" parent ffff: protocol all u32 match u32 0 0 \
  action mirred egress redirect dev "$IFB"
tc qdisc add dev "$IFB" root handle 1: htb default 10
tc class add dev "$IFB" parent 1: classid 1:10 htb \
  rate "${RATE_KBIT}kbit" burst "${BURST_K}k"
tc qdisc add dev "$IFB" parent 1:10 handle 10: pfifo limit 50

echo "  HTB class configured:"
tc -d class show dev "$IFB" | grep "class htb 1:10"
echo ""

# Snapshot tc counters before
B0=$(tc -s class show dev "$IFB" | grep -A2 "class htb 1:10" | grep Sent | awk '{print $2}')

echo "  Running iperf3 -R for ${DURATION}s..."
echo ""
iperf3 -c "$HOST_IP" -R -t "$DURATION" -i 5 --forceflush
echo ""

# Snapshot tc counters after
B1=$(tc -s class show dev "$IFB" | grep -A2 "class htb 1:10" | grep Sent | awk '{print $2}')
DROPS=$(tc -s class show dev "$IFB" | grep -A3 "class htb 1:10" | grep Sent | awk '{print $7}' | tr -d ',)')
OVERLIMITS=$(tc -s class show dev "$IFB" | grep -A3 "class htb 1:10" | grep Sent | awk '{print $9}')

echo "  tc counters (ifb0 HTB class 1:10):"
tc -s class show dev "$IFB" | grep -A5 "class htb 1:10"
echo ""

# Compute throughput from tc counters (the definitive post-shaping measurement)
python3 -c "
cap = ${RATE_MBPS}
tol = ${TOLERANCE}
b0, b1, dt = ${B0}, ${B1}, ${DURATION}
delta = b1 - b0
mbps = delta * 8 / dt / 1_000_000
limit = cap * (1 + tol/100)

print(f'  Post-shaping throughput (from tc counters):')
print(f'    Bytes through HTB:  {delta:,}')
print(f'    Duration:           {dt}s')
print(f'    Throughput:         {mbps:.2f} Mbps')
print(f'    Cap:                {cap} Mbps')
print(f'    Ceiling ({tol}%):      {limit:.1f} Mbps')
print(f'    Drops:              ${DROPS}')
print(f'    Overlimits:         ${OVERLIMITS}')
print()

if mbps <= limit:
    pct = (mbps / cap - 1) * 100
    sign = '+' if pct >= 0 else ''
    print(f'  ✅  PASS — {mbps:.2f} Mbps ({sign}{pct:.1f}% of {cap} Mbps cap)')
else:
    over = (mbps / cap - 1) * 100
    print(f'  ❌  FAIL — {mbps:.2f} Mbps is {over:.1f}% above the {cap} Mbps cap')
    exit(1)
"
