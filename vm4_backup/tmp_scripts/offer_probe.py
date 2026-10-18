import json, sys, time, os
sys.path.insert(0, "/app")
from collect import build_driver, start_display
URL = sys.argv[1]
start_display("99"); os.environ["DISPLAY"] = ":99"
d = build_driver()
try:
    d.get(URL); time.sleep(14)
    o = d.execute_script("""
      const pc = window.pc; if (!pc) return {err:'no pc'};
      const l = pc.localDescription ? pc.localDescription.sdp : '';
      const r = pc.remoteDescription ? pc.remoteDescription.sdp : '';
      const cand = s => (s.match(/^a=candidate:.*$/gm)||[]);
      return {
        offer_candidates: cand(l), offer_mids: (l.match(/^a=mid:.*$/gm)||[]),
        offer_setup: (l.match(/^a=setup:.*$/gm)||[]),
        answer_candidates_count: cand(r).length,
        answer_setup: (r.match(/^a=setup:.*$/gm)||[]),
        answer_fingerprints: (r.match(/^a=fingerprint:.*$/gm)||[]).map(x=>x.split(' ')[0]),
        signalingState: pc.signalingState,
        iceGatheringState: pc.iceGatheringState,
        iceConnectionState: pc.iceConnectionState,
      };
    """)
    print("  signaling:", o.get("signalingState"), "| gathering:", o.get("iceGatheringState"),
          "| ice:", o.get("iceConnectionState"))
    print("  CHROME OFFER candidates:", len(o.get("offer_candidates") or []))
    for c in (o.get("offer_candidates") or [])[:6]: print("     ", c)
    print("  offer mid/setup:", o.get("offer_mids"), o.get("offer_setup"))
    print("  answer candidates seen by Chrome:", o.get("answer_candidates_count"))
    print("  answer setup:", o.get("answer_setup"), "| fingerprints:", o.get("answer_fingerprints"))
    for e in d.get_log("browser")[-10:]:
        m = str(e.get("message"))
        if "favicon" not in m: print("   console:", e.get("level"), m[:170])
finally:
    try: d.quit()
    except Exception: pass
