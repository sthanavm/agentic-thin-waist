#!/usr/bin/env python3
"""Two candidate bitrate channels, measured side by side, per app.

Channel A - MSE: hook SourceBuffer.appendBuffer and total byteLength PER
SourceBuffer, keyed by the mime passed to addSourceBuffer/changeType. Audio and
video are separate SourceBuffers, so a single total would silently add the audio
track to the "video bitrate".

Channel B - Resource Timing: encodedBodySize per media fetch. The spec zeroes
this for CORS-cross-origin responses, so whether it works is a property of each
CDN's headers and has to be measured, not assumed.

Playback state is reported alongside, because "no segments seen" means nothing
unless the video actually played.
"""

import importlib.util
import json
import sys
import time

spec = importlib.util.spec_from_file_location("collect", "/app/collect.py")
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)

# Installed before any page script, on the same channel as the existing codec
# hook, so it sees the player's very first appends.
MSE_BYTES_HOOK = r"""
(() => {
    const KEY = '__netgentMseStats';
    if (window[KEY]) return;
    Object.defineProperty(window, KEY, {
        value: {buffers: [], appends: 0, bytes_total: 0},
        configurable: false, enumerable: false, writable: false
    });
    const S = window.__netgentMseStats;
    const MS = window.MediaSource || window.WebKitMediaSource;
    if (!MS || !MS.prototype) return;

    const findRec = (sb) => {
        for (const r of S.buffers) { if (r.sb === sb) return r; }
        return null;
    };

    const nativeAdd = MS.prototype.addSourceBuffer;
    MS.prototype.addSourceBuffer = function (mime) {
        const sb = nativeAdd.apply(this, arguments);
        try {
            S.buffers.push({sb: sb, mime: String(mime || ''), bytes: 0, appends: 0,
                            mime_history: [String(mime || '')]});
        } catch (e) { /* observation must never break playback */ }
        return sb;
    };

    const SB = window.SourceBuffer && window.SourceBuffer.prototype;
    if (SB && SB.appendBuffer) {
        const nativeAppend = SB.appendBuffer;
        SB.appendBuffer = function (data) {
            try {
                const n = (data && (data.byteLength !== undefined
                    ? data.byteLength : (data.length || 0))) || 0;
                const r = findRec(this);
                if (r) { r.bytes += n; r.appends += 1; }
                S.appends += 1; S.bytes_total += n;
            } catch (e) { /* ditto */ }
            return nativeAppend.apply(this, arguments);
        };
    }
    // changeType is how a player switches codec/mime mid-stream without a new
    // SourceBuffer, so it is the switch signal - record it as history.
    if (SB && SB.changeType) {
        const nativeChange = SB.changeType;
        SB.changeType = function (mime) {
            try {
                const r = findRec(this);
                if (r) { r.mime = String(mime || ''); r.mime_history.push(String(mime || '')); }
            } catch (e) { /* ditto */ }
            return nativeChange.apply(this, arguments);
        };
    }
})();
"""

REPORT_JS = r"""
const S = window.__netgentMseStats || null;
const out = {mse: null, rt: [], play: null};
if (S) {
    out.mse = {
        appends: S.appends, bytes_total: S.bytes_total,
        buffers: S.buffers.map(r => ({
            mime: r.mime, mime_history: r.mime_history,
            bytes: r.bytes, appends: r.appends
        }))
    };
}
const v = document.querySelector('video');
if (v) {
    let q = null;
    try { q = v.getVideoPlaybackQuality(); } catch (e) { q = null; }
    out.play = {
        currentTime: v.currentTime, readyState: v.readyState, paused: v.paused,
        res: v.videoWidth + 'x' + v.videoHeight,
        total_frames: q ? q.totalVideoFrames : null,
        buffered_end: (v.buffered && v.buffered.length)
            ? v.buffered.end(v.buffered.length - 1) : null
    };
}
let list = [];
try { list = performance.getEntriesByType('resource') || []; } catch (e) {}
for (const e of list) {
    const it = e.initiatorType || '';
    if (!(it === 'xmlhttprequest' || it === 'fetch' || it === 'video' || it === 'audio')) continue;
    if ((e.encodedBodySize || 0) < 20000 && (e.transferSize || 0) < 20000) continue;
    let host = ''; try { host = new URL(e.name).host; } catch (err) { host = '?'; }
    out.rt.push({host: host, enc: e.encodedBodySize, tsz: e.transferSize,
                 tao: !!e.nextHopProtocol});
}
return out;
"""


def main() -> int:
    app, url, secs = sys.argv[1], sys.argv[2], float(sys.argv[3])
    collect.start_display(99)
    driver = collect.build_driver(mode="uc")
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": MSE_BYTES_HOOK}
        )
        driver.get(url)
        time.sleep(6)
        try:
            collect.start_playback(driver, app)
        except Exception as exc:  # noqa: BLE001
            print("  (start_playback: %s)" % type(exc).__name__, flush=True)
        time.sleep(secs)
        d = driver.execute_script(REPORT_JS)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print("APP %s" % app)
    p = d.get("play")
    if p:
        print(
            "  PLAYBACK currentTime=%.1fs readyState=%s paused=%s res=%s frames=%s buffered_end=%s"
            % (
                p["currentTime"],
                p["readyState"],
                p["paused"],
                p["res"],
                p["total_frames"],
                p["buffered_end"],
            )
        )
        played = (p["currentTime"] or 0) > 1 and (p["total_frames"] or 0) > 10
        print(
            "  DID IT PLAY: %s"
            % ("YES" if played else "NO - absence of segments proves nothing")
        )
    else:
        print("  PLAYBACK no <video> element found")
    m = d.get("mse")
    if m:
        print(
            "  MSE appends=%d total=%.2f MB across %d SourceBuffer(s)"
            % (m["appends"], m["bytes_total"] / 1e6, len(m["buffers"]))
        )
        for b in m["buffers"]:
            kind = (
                "video"
                if "video/" in b["mime"]
                else ("audio" if "audio/" in b["mime"] else "?")
            )
            print(
                "     [%s] %-46s %8.2f MB  appends=%d  mimes=%d"
                % (
                    kind,
                    b["mime"][:46],
                    b["bytes"] / 1e6,
                    b["appends"],
                    len(b["mime_history"]),
                )
            )
    else:
        print("  MSE hook saw nothing (no MediaSource used, or fetched in a worker)")
    rt = d.get("rt") or []
    tot = sum((e["enc"] or 0) for e in rt)
    hosts = {}
    for e in rt:
        hosts.setdefault(e["host"], [0, 0])
        hosts[e["host"]][0] += 1
        hosts[e["host"]][1] += e["enc"] or 0
    print("  RT media-sized entries=%d total=%.2f MB" % (len(rt), tot / 1e6))
    for h, (n, b) in sorted(hosts.items(), key=lambda kv: -kv[1][1])[:4]:
        print("     %-44s n=%-4d %8.2f MB" % (h[:44], n, b / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
