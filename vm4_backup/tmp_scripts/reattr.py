import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana"))
import pramana_helpers as H
base = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
run, apps, cap = sys.argv[1], sys.argv[2].split(","), float(sys.argv[3])
d = base/run
at = H.attribute_capture(d/"capture.pcap", apps, cap)
print(f"  method={at.method}  total={at.total_mb} MB  attributed={at.attributed_fraction:.3f}")
print(f"  buckets: {at.bucket_mb}")
print(f"  adopted: {at.adopted_ips}")
for a in apps:
    for dirn in ("download","upload"):
        t = at.traffic(a, dirn)
        if t: print("   %-8s %-9s total=%7.3f MB  stall_s=%5.1f  delivered=%.3f  avg=%.3f peak=%.3f  class=%s"
                    % (a, dirn, t.total_mb, t.stall_seconds, t.delivered_fraction, t.avg_throughput_mbps, t.peak_throughput_mbps, t.classification))
