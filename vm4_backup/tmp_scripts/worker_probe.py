#!/usr/bin/env python3
"""Does Twitch create Workers, and is MediaSource used inside them?

If the player appends media from a DedicatedWorker, the worker has its own
MediaSource/SourceBuffer globals and its own performance timeline, so a
main-world hook and main-thread Resource Timing both see nothing even while the
video plays. That is a mechanism claim, so test it: count Worker constructions
and record their script URLs.
"""

import importlib.util, sys, time

spec = importlib.util.spec_from_file_location("collect", "/app/collect.py")
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)

HOOK = r"""
(() => {
    const K = '__netgentWorkers';
    if (window[K]) return;
    Object.defineProperty(window, K, {value: [], configurable: false,
        enumerable: false, writable: false});
    const NW = window.Worker;
    if (!NW) return;
    window.Worker = function (url, opts) {
        try { window[K].push(String(url).slice(0, 120)); } catch (e) {}
        return new NW(url, opts);
    };
    window.Worker.prototype = NW.prototype;
    // Also note whether the page's MediaSource was ever constructed at all.
    const MS = window.MediaSource;
    if (MS) {
        let n = 0;
        const P = new Proxy(MS, {construct(t, a) { n++; window.__netgentMsCount = n;
            return new t(...a); }});
        try { window.MediaSource = P; } catch (e) {}
    }
})();
"""
REPORT = r"""
const v = document.querySelector('video');
let q = null; try { q = v && v.getVideoPlaybackQuality(); } catch (e) {}
return {
  workers: window.__netgentWorkers || [],
  main_mediasource_constructions: window.__netgentMsCount || 0,
  played: v ? {t: v.currentTime, frames: q ? q.totalVideoFrames : null,
               res: v.videoWidth + 'x' + v.videoHeight,
               src: String(v.src || '').slice(0, 40),
               srcObject: !!v.srcObject} : null
};
"""

app, url, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])
collect.start_display(99)
d = collect.build_driver(mode="uc")
try:
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": HOOK})
    d.get(url)
    time.sleep(6)
    try:
        collect.start_playback(d, app)
    except Exception:
        pass
    time.sleep(secs)
    r = d.execute_script(REPORT)
finally:
    try:
        d.quit()
    except Exception:
        pass
print("APP", app)
print("  played:", r.get("played"))
print(
    "  main-world MediaSource constructions:", r.get("main_mediasource_constructions")
)
ws = r.get("workers") or []
print("  Worker constructions: %d" % len(ws))
for w in ws[:6]:
    print("     ", w)
