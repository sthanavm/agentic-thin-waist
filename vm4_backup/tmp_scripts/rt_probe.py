#!/usr/bin/env python3
"""Does Resource Timing actually expose media segment sizes, per app?

The W3C spec zeroes encodedBodySize/decodedBodySize for CORS-cross-origin
responses and additionally gates transferSize behind the timing-allow check.
But MSE segments must be readable into an ArrayBuffer, so players fetch them
WITH CORS - and a CORS-successful response is not opaque. Whether the sizes are
therefore populated is a property of each CDN's headers, not something to be
reasoned about from the spec alone. So measure it.

Run inside the collector image, which already has Chrome + Xvfb:
  docker run --rm --entrypoint python3 \
    -v /tmp/rt_probe.py:/app/rt_probe.py:ro video-qoe-collector:latest \
    /app/rt_probe.py <app> <url> <seconds>
"""

import collections
import importlib.util
import json
import sys
import time

spec = importlib.util.spec_from_file_location("collect", "/app/collect.py")
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)

RT_JS = r"""
const out = {entries: [], nav_origin: location.origin};
let list = [];
try { list = performance.getEntriesByType('resource') || []; } catch (e) { out.error = String(e); }
for (const e of list) {
    // Media-ish only: XHR/fetch (MSE segments) or the video/audio element itself.
    const it = e.initiatorType || '';
    if (!(it === 'xmlhttprequest' || it === 'fetch' || it === 'video' || it === 'audio')) continue;
    let host = '';
    try { host = new URL(e.name).host; } catch (err) { host = '?'; }
    out.entries.push({
        host: host,
        initiator: it,
        transferSize: e.transferSize,
        encodedBodySize: e.encodedBodySize,
        decodedBodySize: e.decodedBodySize,
        // nextHopProtocol is zeroed/'' exactly when the timing-allow check fails,
        // so it doubles as a direct read of whether TAO was granted.
        nextHopProtocol: e.nextHopProtocol || '',
        duration: Math.round(e.duration),
        name_tail: String(e.name).slice(-60)
    });
}
return out;
"""


def main() -> int:
    app, url, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])
    collect.start_display(99)
    driver = collect.build_driver(mode="uc")
    try:
        driver.get(url)
        time.sleep(6)
        try:
            collect.start_playback(driver, app)
        except Exception as exc:  # noqa: BLE001 - a failed autoplay still fetches
            print("  (start_playback: %s)" % type(exc).__name__, flush=True)
        time.sleep(secs)
        data = driver.execute_script(RT_JS)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    ents = data.get("entries", [])
    print("APP %s  entries=%d  origin=%s" % (app, len(ents), data.get("nav_origin")))
    if not ents:
        print(
            "  NO media-ish resource entries (player may use a worker or opaque fetch)"
        )
        return 0
    by_host = collections.defaultdict(
        lambda: {"n": 0, "tsz": 0, "enc": 0, "dec": 0, "proto": 0, "bytes": 0}
    )
    for e in ents:
        h = by_host[e["host"]]
        h["n"] += 1
        h["tsz"] += 1 if (e["transferSize"] or 0) > 0 else 0
        h["enc"] += 1 if (e["encodedBodySize"] or 0) > 0 else 0
        h["dec"] += 1 if (e["decodedBodySize"] or 0) > 0 else 0
        h["proto"] += 1 if e["nextHopProtocol"] else 0
        h["bytes"] += e["encodedBodySize"] or 0
    print(
        "  %-42s %5s %7s %7s %7s %7s %10s"
        % ("host", "n", "tsz>0", "enc>0", "dec>0", "TAO", "enc bytes")
    )
    for host, h in sorted(by_host.items(), key=lambda kv: -kv[1]["n"])[:8]:
        print(
            "  %-42s %5d %7d %7d %7d %7d %10d"
            % (host[:42], h["n"], h["tsz"], h["enc"], h["dec"], h["proto"], h["bytes"])
        )
    tot = len(ents)
    print(
        "  TOTALS: enc>0 %d/%d (%.0f%%)  tsz>0 %d/%d  TAO %d/%d"
        % (
            sum(1 for e in ents if (e["encodedBodySize"] or 0) > 0),
            tot,
            100.0 * sum(1 for e in ents if (e["encodedBodySize"] or 0) > 0) / tot,
            sum(1 for e in ents if (e["transferSize"] or 0) > 0),
            tot,
            sum(1 for e in ents if e["nextHopProtocol"]),
            tot,
        )
    )
    big = [e for e in ents if (e["encodedBodySize"] or 0) > 50000]
    print("  segments >50KB with a readable size: %d" % len(big))
    for e in big[:3]:
        print(
            "     %s enc=%d  ...%s"
            % (e["host"][:30], e["encodedBodySize"], e["name_tail"][-34:])
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
