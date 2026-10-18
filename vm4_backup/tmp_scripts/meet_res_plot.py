import json, pathlib, collections
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RUNS = [
    ("meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_eefe603aa4", "NEW run (kon-xobx-fgb) - 2 inbound streams"),
    ("meet_10mbps_50ms_0pct_pfifo_cubic_solo_t1_05afa404ea", "PREVIOUS run (akr-bpcc-qdj) - 1 inbound stream"),
]
base = pathlib.Path.home()/"sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
fig, axes = plt.subplots(len(RUNS), 1, figsize=(13, 7), sharex=True)
for ax, (run, title) in zip(axes, RUNS):
    d = base/run
    rows = [json.loads(l) for l in open(d/"qoe"/"meet_stats.jsonl") if l.strip()]
    rows = [r for r in rows if r.get("record") != "meta"]
    t0 = rows[0]["timestamp"]
    per = collections.defaultdict(list)
    for r in rows:
        st = r.get("stats", r)
        for s in (st.get("inbound_streams") or []):
            h = s.get("frame_height")
            if h:
                per[s["id"]].append((r["timestamp"] - t0, h))
    colors = ["#2E7D32", "#C62828", "#1565C0"]
    for i, (sid, pts) in enumerate(sorted(per.items())):
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        heights = sorted(set(ys))
        lab = f"{sid[:14]}  ({'/'.join(str(h)+'p' for h in heights)})"
        ax.step(xs, ys, where="post", linewidth=1.6, color=colors[i % 3], label=lab)
        ax.scatter(xs, ys, s=14, color=colors[i % 3], zorder=3)
    ax.set_ylabel("resolution (p)")
    ax.set_title(title, fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, 640)
    ax.legend(loc="upper right", fontsize=8)
axes[-1].set_xlabel("Time since first in-call sample (s)")
fig.suptitle("Google Meet - decoded resolution per inbound video stream - 10 Mbps / 50ms / pfifo", fontsize=12)
fig.tight_layout(rect=(0, 0, 1, 0.96))
out = base/RUNS[0][0]/"resolution_meet_streams.png"
fig.savefig(out, dpi=150)
print("wrote", out)
for run, _ in RUNS:
    d = base/run
    rows = [json.loads(l) for l in open(d/"qoe"/"meet_stats.jsonl") if l.strip()]
    rows = [r for r in rows if r.get("record") != "meta"]
    per = collections.defaultdict(collections.Counter)
    for r in rows:
        for s in ((r.get("stats", r)).get("inbound_streams") or []):
            per[s["id"]]["%sx%s" % (s.get("frame_width"), s.get("frame_height"))] += 1
    print("\n %s" % run[-10:])
    for sid, c in per.items():
        print("   %s : %s" % (sid[:16], dict(c)))
