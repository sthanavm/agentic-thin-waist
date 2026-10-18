import json, sys, time, os
sys.path.insert(0, "/app")
from collect import build_driver, start_display
URL = sys.argv[1]
start_display("99"); os.environ["DISPLAY"] = ":99"
d = build_driver()
try:
    d.get(URL)
    time.sleep(16)
    tr = d.execute_script("return window.__trace || []")
    print("  --- page trace ---")
    for e in tr:
        extra = {k: v for k, v in e.items() if k not in ("k", "t")}
        print("    %-24s %s" % (e["k"], json.dumps(extra) if extra else ""))
    out = d.execute_async_script("""
      const done = arguments[arguments.length-1];
      const v = document.querySelector('video'); const pc = window.pc;
      if (!pc) return done({err:'no pc'});
      pc.getStats().then(rs => {
        const o = {ice: pc.iceConnectionState, conn: pc.connectionState,
                   w: v.videoWidth, h: v.videoHeight, rs: v.readyState,
                   paused: v.paused, ct: v.currentTime, pairs: 0, succeeded: 0, inbound: null};
        rs.forEach(r => {
          if (r.type==='candidate-pair'){ o.pairs++; if(r.state==='succeeded') o.succeeded++; }
          if (r.type==='inbound-rtp' && r.kind==='video')
            o.inbound={bytes:r.bytesReceived, packets:r.packetsReceived,
                       framesDecoded:r.framesDecoded, framesReceived:r.framesReceived,
                       frameWidth:r.frameWidth, frameHeight:r.frameHeight};
        });
        done(o);
      });
    """)
    print("  --- browser state ---")
    print("   ", json.dumps(out))
finally:
    try: d.quit()
    except Exception: pass
