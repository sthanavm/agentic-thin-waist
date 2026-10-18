import json, sys, os, time
sys.path.insert(0, "/app")
from collect import build_driver, start_display
start_display("99"); os.environ["DISPLAY"] = ":99"
d = build_driver()
try:
    d.get(sys.argv[1] if len(sys.argv)>1 else "about:blank")
    out = d.execute_async_script("""
      const done = arguments[arguments.length-1];
      const res = {};
      async function gather(label, cfg){
        const pc = new RTCPeerConnection(cfg);
        const cands = [];
        pc.onicecandidate = e => { if (e.candidate) cands.push(e.candidate.candidate); };
        pc.createDataChannel('x');
        await pc.setLocalDescription(await pc.createOffer());
        await new Promise(r => setTimeout(r, 6000));
        res[label] = {count: cands.length, state: pc.iceGatheringState,
                      types: [...new Set(cands.map(c => (c.match(/ typ (\\w+)/)||[])[1]))],
                      sample: cands.slice(0,3)};
        pc.close();
      }
      (async () => {
        await gather('no_ice_servers', {});
        await gather('with_stun', {iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
        done(res);
      })();
    """)
    for k, v in out.items():
        print("  %-16s candidates=%-3s gathering=%-10s types=%s" % (k, v["count"], v["state"], v["types"]))
        for c in v["sample"]: print("       ", c)
finally:
    try: d.quit()
    except Exception: pass
