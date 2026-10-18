import json, sys, time, os
sys.path.insert(0, "/app")
from collect import build_driver, start_display
URL = sys.argv[1]
start_display("99"); os.environ["DISPLAY"] = ":99"
d = build_driver()
try:
    d.get(URL); time.sleep(12)
    out = d.execute_async_script("""
      const done = arguments[arguments.length-1];
      const pc = window.pc;
      if (!pc) return done({err:'no pc'});
      pc.getStats().then(rs => {
        const o = {ice: pc.iceConnectionState, conn: pc.connectionState, pairs: [], inbound: null};
        rs.forEach(r => {
          if (r.type==='candidate-pair') o.pairs.push({state:r.state, nominated:r.nominated,
              sent:r.bytesSent, recv:r.bytesReceived});
          if (r.type==='inbound-rtp' && r.kind==='video')
              o.inbound = {bytes:r.bytesReceived, packets:r.packetsReceived,
                           frames:r.framesDecoded, dropped:r.framesDropped, codec:r.codecId};
        });
        done(o);
      });
    """)
    print("  ice:", out.get("ice"), "| conn:", out.get("conn"))
    print("  inbound-rtp:", json.dumps(out.get("inbound")))
    succ = [p for p in (out.get("pairs") or []) if p.get("state")=="succeeded"]
    print("  candidate pairs: %d total, %d succeeded" % (len(out.get("pairs") or []), len(succ)))
    for p in succ[:2]: print("     ", json.dumps(p))
finally:
    try: d.quit()
    except Exception: pass
