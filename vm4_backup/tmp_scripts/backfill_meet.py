"""Rewrite the Meet run's record.json from its own raw samples.

Only the fields the fixed derivation actually changes are touched; the capture,
the network stats and every other block stay exactly as the run produced them.
"""
import json, glob, os, shutil, sys
sys.path.insert(0, "/home/student/sthanav-agentic-thin-waist")
sys.path.insert(0, "/home/student/sthanav-agentic-thin-waist/experiments/pramana")
from shared import qoe as Q
import pramana_helpers as ph

R = "/home/student/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs"
targets = [os.path.dirname(p) for p in glob.glob(R + "/*/record.json")
           if (json.load(open(p)).get("config") or {}).get("tag") == "meet_e2e"]
targets = [d for d in targets if os.path.exists(os.path.join(d, "qoe", "meet_stats.jsonl"))]
print(f"  meet runs with samples: {len(targets)}")

for d in sorted(targets, key=os.path.getmtime):
    rp = os.path.join(d, "record.json")
    rec = json.load(open(rp))
    f = os.path.join(d, "qoe", "meet_stats.jsonl")
    rows = [json.loads(l) for l in open(f) if l.strip()]
    meta = [r for r in rows if r.get("record") == "meta"]
    if not [r for r in rows if r.get("record") != "meta"]:
        print(f"  {os.path.basename(d)[-10:]}: no samples, skipped"); continue
    ptt = meta[0].get("play_trigger_ts") if meta else None
    before = (rec.get("player_qoe") or {}).get("meet") or {}
    after = Q.summarize(f, "meet", play_trigger_ts=ptt)
    if not after.get("player_qoe_available"):
        print(f"  {os.path.basename(d)[-10:]}: still not available, leaving as-is"); continue

    shutil.copy(rp, rp + ".pre_qoefix.bak")
    rec.setdefault("player_qoe", {})["meet"] = after
    st = (rec.get("per_app_stats") or {}).get("meet")
    if st is not None:
        net_verdict = "served" if (st.get("download") or {}).get(
            "classification") == "served" else (st.get("download") or {}).get(
            "classification", "no_traffic")
        st["player_qoe_available"] = bool(after.get("player_qoe_available"))
        st["player_qoe_status"] = after.get("status")
        st["classification"] = ph.app_verdict(net_verdict, after)
    pa = (rec.get("per_app") or {}).get("meet")
    if pa is not None:
        pa["player_qoe_available"] = bool(after.get("player_qoe_available"))
        if st is not None:
            pa["traffic_verdict"] = st["classification"]
    rec.setdefault("backfill", []).append({
        "what": "player_qoe.meet re-derived with the fixed WebRTC field mapping",
        "was": {"status": before.get("status"), "reason": before.get("reason")},
    })
    json.dump(rec, open(rp, "w"), indent=2)
    print(f"\n  === {os.path.basename(d)} ===")
    print(f"    backup: record.json.pre_qoefix.bak")
    print(f"    status      {before.get('status')} / {before.get('reason')}  ->  {after.get('status')}")
    print(f"    available   {before.get('player_qoe_available')}  ->  {after.get('player_qoe_available')}")
    print(f"    classification -> {st['classification'] if st else 'n/a'}")
    for k in ("mean_bitrate_mbps","max_bitrate_mbps","min_bitrate_mbps","video_resolution_p",
              "resolutions_observed","dropped_frame_pct","packet_loss_pct","mean_jitter_secs",
              "rebuffer_events","rebuffer_duration_ms","stall_duration_ms","total_samples","is_live",
              "max_bitrate_is_startup_artifact"):
        print(f"      {k:<32} {after.get(k)}")
    print(f"      derivation.bitrate_caveat present: {'bitrate_caveat' in (after.get('derivation') or {})}")
print("\nBACKFILL_DONE")
