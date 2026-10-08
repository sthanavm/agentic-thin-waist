#!/usr/bin/env python3
"""Play one or more video URLs in real (headed, Xvfb-rendered) Chrome and
sample QoE.

PROVENANCE / SCOPE
------------------
The browser-driving mechanics here are from PR #167 (SNL-UCSB/agentic-thin-waist,
branch `dual`) and are reused as-is: they are the hard-won part. Changes made on
top are deliberately minimal and are limited to making the driver registry-driven
rather than hardcoded to two app names:

  * jobs carry a `kind` ("html5_video" | "webrtc") from shared/apps.py, and the
    WebRTC branch routes on that instead of `app == "google_meet"`, so any
    conferencing app in the registry uses the same path;
  * every sample records its `kind` and `app`, so the summarizer knows how to
    reduce the file without guessing from the URL;
  * a failing job aborts the sampling barrier instead of leaving its siblings to
    block for the full timeout.

This file deliberately does NOT define what a QoE metric means. It records raw
per-second player samples. All derivation (startup, rebuffers, resolution, fps,
dropped frames, bitrate) lives in shared/qoe.py against the QoEMetrics
definition in shared/models/README.md.

Headless CDP-over-Playwright works fine for YouTube but Vimeo's MSE ("blob:"
src) pipeline never advances through that path. The proven fix (see PR #6 in
netgent-dev) is SeleniumBase + undetected-chromedriver driving a real,
non-headless google-chrome-stable rendered onto an Xvfb virtual display with
a window manager (fluxbox). This script reproduces that mechanism.

Running two apps as two separate *containers* that share one network
namespace (Docker's `--network container:X`) was tried first and rejected:
even with distinct X displays, the two undetected-chromedriver sessions
cross-attached to each other's Chrome (confirmed empirically - QoE samples
from one container's URL showed up in the other's output file). Sharing a
network namespace also shares the whole loopback port space, and chromedriver
port allocation across that boundary isn't reliable. Running multiple jobs as
sibling *threads* within one process/container - the normal, well-supported
way to drive several Selenium sessions concurrently - avoids that class of
bug entirely, and matches how PR #6's own reference actually did it (one
process, DISPLAY=:99 and :100 for the two apps).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Runs in the page. Detects YouTube vs. a generic <video> element (Vimeo
# falls into the generic branch - that's exactly what worked in PR #6).
MEDIA_HOOK_JS = r"""
// Codec detection. A <video> does not expose its codec: src is usually a blob
// backed by MediaSource, and videoTracks is empty in Chrome. The mime type is
// only ever stated when the page creates its source buffers, so record it
// there. Installed before page scripts, exactly like the peer-connection hook.
(() => {
    const key = '__netgentCodecs';
    if (Array.isArray(window[key])) return;
    Object.defineProperty(window, key, {
        value: [], configurable: false, enumerable: false, writable: false
    });
    const MS = window.MediaSource || window.WebKitMediaSource;
    if (!MS || !MS.prototype || MS.prototype.__netgentWrapped) return;
    const native = MS.prototype.addSourceBuffer;
    MS.prototype.addSourceBuffer = function (mime) {
        try {
            if (mime && window[key].indexOf(mime) === -1) window[key].push(mime);
        } catch (e) { /* never break playback to observe it */ }
        return native.apply(this, arguments);
    };
    MS.prototype.__netgentWrapped = true;
})();

// Which MediaStreamTracks came from the LOCAL camera. This is the only
// deterministic way to tell a conferencing self-view from a remote participant:
// both are MediaStream-backed <video> elements with real intrinsic sizes, and
// size alone is actively misleading - Zoom renders the local fake device at a
// full 1280x720 inside a 207x117 thumbnail while the remote speaker occupies the
// main view at whatever resolution Zoom chose to send. Ranking by size therefore
// reported the collector's own camera as the call's video quality.
// getUserMedia is the single place a page can acquire local capture, so wrapping
// it before any page script runs yields an exact set of local track ids.
(() => {
    const key = '__netgentLocalTrackIds';
    if (window[key]) return;
    Object.defineProperty(window, key, {
        value: [], configurable: false, enumerable: false, writable: false
    });
    Object.defineProperty(window, '__netgentLocalTrackLabels', {
        value: [], configurable: false, enumerable: false, writable: false
    });
    const remember = (stream) => {
        try {
            stream.getTracks().forEach((t) => {
                if (t && t.id && window[key].indexOf(t.id) === -1) {
                    window[key].push(t.id);
                }
                // The label survives a clone or canvas re-wrap; the id does not.
                const lab = (t.label || '').trim();
                if (lab && window.__netgentLocalTrackLabels.indexOf(lab) === -1) {
                    window.__netgentLocalTrackLabels.push(lab);
                }
            });
        } catch (e) { /* never break capture to observe it */ }
        return stream;
    };
    const md = navigator.mediaDevices;
    if (md && typeof md.getUserMedia === 'function') {
        const native = md.getUserMedia.bind(md);
        md.getUserMedia = function (c) {
            return native(c).then(remember);
        };
    }
    // Legacy entry point, still used by some builds.
    const legacy = navigator.getUserMedia || navigator.webkitGetUserMedia;
    if (typeof legacy === 'function') {
        const nativeLegacy = legacy.bind(navigator);
        const wrapped = function (c, ok, err) {
            return nativeLegacy(c, (s) => ok(remember(s)), err);
        };
        try { navigator.getUserMedia = wrapped; } catch (e) { /* read-only */ }
        try { navigator.webkitGetUserMedia = wrapped; } catch (e) { /* ditto */ }
    }
})();
"""


# Uniform streaming-QoE probe. One mechanism for every site, no per-site APIs.
# Installed on the same pre-document channel as the codec hook, so it observes a
# player's very first append and very first presented frame.
#
# Each piece exists because a target metric cannot be measured without it:
#   * appended bytes PER SourceBuffer, keyed by mime - the only uniform source of
#     delivered bitrate. Measured on this host: YouTube's audio buffer carried
#     26% of all appended bytes, so a single total would overstate video bitrate
#     by about a third. appendBuffer's argument size is observable, and audio and
#     video are always separate SourceBuffers.
#   * requestVideoFrameCallback - presentedFrames counts frames actually
#     submitted for composition, which is the rendering rate being asked for;
#     getVideoPlaybackQuality().totalVideoFrames counts DECODED frames, a
#     different quantity. Its first callback also timestamps the first frame a
#     viewer could see, which is the honest end of startup delay.
#   * the media event log - `waiting` is spec-defined as "playback has stopped
#     because of a temporary lack of data", i.e. a rebuffer, closed by `playing`.
#     Inferring stalls from a progress counter cannot distinguish that from a
#     paused tab or a counter reset.
#   * changeType/addSourceBuffer mime history - a codec or rendition switch
#     without creating a new SourceBuffer shows up nowhere else.
#   * worker detection - a player may run MediaSource inside a Worker and hand
#     the element a MediaSourceHandle via srcObject. Measured: Twitch does
#     exactly this (0 main-world MediaSource constructions, 2 Workers,
#     video.srcObject set), so these hooks are structurally blind there and the
#     record must say so instead of reporting a silent zero.
STREAM_PROBE_JS = r"""
(() => {
    const K = '__netgentProbe';
    if (window[K]) return;
    Object.defineProperty(window, K, {
        value: {
            buffers: [], appends: 0, bytes_total: 0,
            ms_constructions: 0, workers: [],
            events: {}, event_log: [], waiting_spans: [], _waiting_at: null,
            rvfc: {presented: 0, first_presentation_ms: null, first_media_time: null,
                   last_media_time: null, width: 0, height: 0, installed: false,
                   processing_total: 0, processing_n: 0},
            t0: (performance && performance.now) ? performance.now() : 0
        },
        configurable: false, enumerable: false, writable: false
    });
    const S = window[K];
    const now = () => ((performance && performance.now) ? performance.now() : 0);

    // ---- MSE: bytes per SourceBuffer, keyed by the mime it was created with --
    const MS = window.MediaSource || window.WebKitMediaSource;
    const findRec = (sb) => {
        for (const r of S.buffers) { if (r.sb === sb) { return r; } }
        return null;
    };
    if (MS && MS.prototype && !MS.prototype.__netgentProbeWrapped) {
        const nativeAdd = MS.prototype.addSourceBuffer;
        MS.prototype.addSourceBuffer = function (mime) {
            const sb = nativeAdd.apply(this, arguments);
            try {
                S.buffers.push({
                    sb: sb, mime: String(mime || ''), bytes: 0, appends: 0,
                    mime_history: [{t: now() - S.t0, mime: String(mime || '')}]
                });
            } catch (e) { /* observation must never break playback */ }
            return sb;
        };
        MS.prototype.__netgentProbeWrapped = true;
    }
    const SBp = window.SourceBuffer && window.SourceBuffer.prototype;
    if (SBp && SBp.appendBuffer && !SBp.__netgentProbeWrapped) {
        const nativeAppend = SBp.appendBuffer;
        SBp.appendBuffer = function (data) {
            try {
                const n = (data && (data.byteLength !== undefined
                    ? data.byteLength : (data.length || 0))) || 0;
                const r = findRec(this);
                if (r) { r.bytes += n; r.appends += 1; }
                S.appends += 1; S.bytes_total += n;
            } catch (e) { /* ditto */ }
            return nativeAppend.apply(this, arguments);
        };
        if (SBp.changeType) {
            const nativeChange = SBp.changeType;
            SBp.changeType = function (mime) {
                try {
                    const r = findRec(this);
                    if (r) {
                        r.mime = String(mime || '');
                        r.mime_history.push({t: now() - S.t0, mime: String(mime || '')});
                    }
                } catch (e) { /* ditto */ }
                return nativeChange.apply(this, arguments);
            };
        }
        SBp.__netgentProbeWrapped = true;
    }
    // Count main-world MediaSource constructions, to tell "no MSE" apart from
    // "MSE, but inside a Worker where this hook cannot reach".
    if (MS) {
        try {
            const P = new Proxy(MS, {
                construct(t, a) { S.ms_constructions += 1; return new t(...a); }
            });
            window.MediaSource = P;
        } catch (e) { /* a non-configurable global is fine; count stays 0 */ }
    }
    if (window.Worker) {
        try {
            const NW = window.Worker;
            const W = function (url, opts) {
                try { S.workers.push(String(url).slice(0, 120)); } catch (e) {}
                return new NW(url, opts);
            };
            W.prototype = NW.prototype;
            window.Worker = W;
        } catch (e) { /* ditto */ }
    }

    // ---- media events + rVFC, attached to each <video> as it appears ---------
    const EV = ['waiting', 'playing', 'stalled', 'seeking', 'seeked', 'ratechange',
                'loadeddata', 'canplay', 'play', 'pause', 'suspend', 'emptied',
                'error', 'ended', 'loadedmetadata'];
    const attach = (v) => {
        if (!v || v.__netgentProbeAttached) { return; }
        v.__netgentProbeAttached = true;
        for (const name of EV) {
            v.addEventListener(name, () => {
                try {
                    S.events[name] = (S.events[name] || 0) + 1;
                    const t = now() - S.t0;
                    if (S.event_log.length < 400) {
                        S.event_log.push({t: Math.round(t), e: name,
                                          ct: v.currentTime});
                    }
                    // A rebuffer is the span from `waiting` to the next
                    // `playing`, which is what the spec defines those events to
                    // mean. Seeking also fires `waiting`, so the span records
                    // whether a seek was in flight and the reducer can exclude it.
                    if (name === 'waiting') {
                        S._waiting_at = {t: t, ct: v.currentTime, seeking: !!v.seeking};
                    } else if (name === 'playing' && S._waiting_at) {
                        S.waiting_spans.push({
                            start: Math.round(S._waiting_at.t),
                            end: Math.round(t),
                            dur_ms: Math.round(t - S._waiting_at.t),
                            ct: S._waiting_at.ct,
                            during_seek: S._waiting_at.seeking
                        });
                        S._waiting_at = null;
                    }
                } catch (e) { /* never throw from a listener */ }
            }, {passive: true});
        }
        if (typeof v.requestVideoFrameCallback === 'function' && !S.rvfc.installed) {
            S.rvfc.installed = true;
            const step = (nowTs, md) => {
                try {
                    S.rvfc.presented = md.presentedFrames || (S.rvfc.presented + 1);
                    if (S.rvfc.first_presentation_ms === null) {
                        S.rvfc.first_presentation_ms = Math.round(
                            (md.presentationTime || nowTs) - S.t0);
                        S.rvfc.first_media_time = md.mediaTime;
                    }
                    S.rvfc.last_media_time = md.mediaTime;
                    S.rvfc.width = md.width || S.rvfc.width;
                    S.rvfc.height = md.height || S.rvfc.height;
                    if (typeof md.processingDuration === 'number') {
                        S.rvfc.processing_total += md.processingDuration;
                        S.rvfc.processing_n += 1;
                    }
                } catch (e) { /* ditto */ }
                try { v.requestVideoFrameCallback(step); } catch (e) {}
            };
            try { v.requestVideoFrameCallback(step); } catch (e) {}
        }
    };
    const scan = () => {
        try { document.querySelectorAll('video').forEach(attach); } catch (e) {}
    };
    scan();
    try {
        new MutationObserver(scan).observe(document.documentElement || document,
            {childList: true, subtree: true});
    } catch (e) { /* fall back to the periodic scan */ }
    setInterval(scan, 1000);
})();
"""


# Runs in the page, once per sample. ONE generic path does the real work on any
# <video> element - no app detection, no per-app branches. YouTube's
# getStatsForNerds() is layered on top as a bonus when it happens to exist;
# nothing downstream requires it. Output carries both the standardized
# video/playback/frames blocks and the original flat keys, because shared/qoe.py
# and ~400 stored runs read the flat names and must keep working unchanged.
STATS_JS = r"""
const host = location.hostname || '';

function pickVideo() {
    // Conferencing pages carry several <video> elements for the same streams:
    // 0x0 placeholders, filmstrip thumbnails, the local self-view and the main
    // speaker view. Picking among them has broken this measurement three times,
    // so the rules are stated in priority order and each one names its evidence.
    //
    //   1. NEVER measure a local self-view. videoOrigin() classifies by
    //      getUserMedia track identity, not by size: Zoom renders the local fake
    //      device at a full 1280x720 inside a 207x117 thumbnail, so any
    //      size-based rule picks the collector's own camera. One sweep reported
    //      1280x720@207x117 - its own fake camera - in all 168 samples of a
    //      cell, with 0.0% dropped frames because nothing was crossing a
    //      network at all.
    //   2. Prefer the LARGEST RENDERED BOX among remote streams. The box is
    //      what a viewer actually sees; intrinsic size is not, because Zoom
    //      downscales a remote tile while leaving its element large.
    //   3. STICK to that choice while it stays valid, so a tile re-render
    //      cannot move the measurement mid-call and reset the frame counter
    //      (it did, in three of six runs of one earlier sweep).
    //   4. Require the element to be laid out at all: a <video> can report
    //      1280x720 while occupying a 0x0 box, and Chrome never composites
    //      such an element, so getVideoPlaybackQuality() counts every frame as
    //      dropped. That produced 99.971% dropped on two otherwise clean runs.
    const vids = Array.from(document.querySelectorAll('video'));
    const boxOf = (v) => {
        try {
            const r = v.getBoundingClientRect();
            return Math.max(0, r.width) * Math.max(0, r.height);
        } catch (e) { return 0; }
    };
    const decoded = vids.filter(v => v.videoWidth > 0 && v.videoHeight > 0);
    const rendered = decoded.filter(v => boxOf(v) > 0);
    // Remote-only candidates, best first. `local === null` means the element has
    // no MediaStream (a file/MSE player such as Vimeo), which is not a self-view
    // and must stay eligible - otherwise this change would break every
    // non-conferencing app.
    const remote = rendered.filter(v => videoOrigin(v).local !== true);
    const pool = remote.length ? remote : (rendered.length ? rendered : decoded);
    const better = (a, b) => {
        const ba = boxOf(a), bb = boxOf(b);
        if (bb !== ba) { return bb > ba; }
        return b.videoWidth * b.videoHeight > a.videoWidth * a.videoHeight;
    };
    const pinned = window.__netgentPinnedVideo;
    // The pin must not outlive its own correctness. Drop it if it became a
    // self-view or stopped being composited, and upgrade it if a clearly larger
    // remote view appears (twice the area), which means the layout changed.
    if (pinned && pool.indexOf(pinned) !== -1) {
        let upgrade = null;
        for (const v of pool) {
            if (v !== pinned && boxOf(v) > boxOf(pinned) * 2) {
                if (!upgrade || better(upgrade, v)) { upgrade = v; }
            }
        }
        if (!upgrade) { return pinned; }
        try { window.__netgentPinnedVideo = upgrade; } catch (e) { /* advisory */ }
        return upgrade;
    }
    if (!pool.length) { return vids[0] || null; }
    const best = pool.reduce((a, b) => (better(a, b) ? b : a));
    if (boxOf(best) > 0) {
        try { window.__netgentPinnedVideo = best; } catch (e) { /* pin is advisory */ }
    }
    return best;
}

// Where a <video>'s pixels come from: the local camera, a remote peer, or an
// element with no MediaStream at all (a file/MSE player).
function videoOrigin(v) {
    const out = { local: null, labels: [], track_ids: [], kind: 'unknown' };
    try {
        const so = v && v.srcObject;
        if (!so || typeof so.getTracks !== 'function') {
            out.kind = 'no-stream';
            return out;
        }
        const tracks = so.getTracks() || [];
        out.track_ids = tracks.map((t) => (t && t.id) || '');
        out.labels = tracks.map((t) => (t && t.label) || '');
        const localIds = window.__netgentLocalTrackIds || [];
        const localLabels = window.__netgentLocalTrackLabels || [];
        // A stream carrying a locally-captured track is a self-view. Match on id
        // OR label: the id is exact but is lost when the page re-wraps the track,
        // and the label survives that. Measured on this host - Zoom's self-view
        // renders a track labelled 'fake_device_0' whose id is absent from the
        // captured set, so id-only matching called the collector's own camera a
        // remote stream in 24 samples of one 12-cell sweep.
        const byId = out.track_ids.some((id) => id && localIds.indexOf(id) !== -1);
        const byLabel = out.labels.some(
            (l) => l && (localLabels.indexOf(l) !== -1 || /fake_device/i.test(l))
        );
        out.local = byId || byLabel;
        out.matched_on = byId ? 'track_id' : (byLabel ? 'track_label' : null);
        out.kind = out.local ? 'local-capture' : 'remote-stream';
    } catch (e) { /* classification is observability only */ }
    return out;
}

function detectCodec(video) {
    try {
        const recorded = window.__netgentCodecs;
        if (Array.isArray(recorded) && recorded.length) {
            const v = recorded.filter(m => /video\//i.test(m));
            return (v.length ? v : recorded).join(', ');
        }
        const tracks = video && video.videoTracks;
        if (tracks && tracks.length) {
            for (let i = 0; i < tracks.length; i++) {
                if (tracks[i] && tracks[i].configuration
                    && tracks[i].configuration.codec) {
                    return tracks[i].configuration.codec;
                }
            }
        }
        // A plain progressive file states its type on the element.
        if (video && video.currentSrc && /\.(mp4|webm|ogg)(\?|$)/i.test(video.currentSrc)) {
            return video.currentSrc.replace(/^.*\.([a-z0-9]+)(\?.*)?$/i, '$1');
        }
    } catch (e) { /* observability only */ }
    return null;
}

// The whole measurement. Everything below this line is generic DOM/HTMLMediaElement.
function genericVideoStats(video) {
    const out = {};
    if (!video) { return out; }
    out.video_width = video.videoWidth;
    out.video_height = video.videoHeight;
    out.resolution = video.videoWidth + 'x' + video.videoHeight;
    out.playback_rate = video.playbackRate;
    out.paused = video.paused;
    out.muted = video.muted;
    out.volume = video.volume;
    out.current_time_secs = video.currentTime;
    out.ready_state = video.readyState;
    out.network_state = video.networkState;
    out.ended = video.ended;
    const d = video.duration;
    if (typeof d === 'number' && isFinite(d) && d > 0) { out.duration_secs = d; }
    if (video.buffered && video.buffered.length) {
        const end = video.buffered.end(video.buffered.length - 1);
        out.buffer_ahead_secs = Math.max(0, end - video.currentTime);
        out.buffered_start_secs = video.buffered.start(0);
        out.buffered_end_secs = end;
        out.buffered_ranges = video.buffered.length;
    }
    if (typeof video.getVideoPlaybackQuality === 'function') {
        const q = video.getVideoPlaybackQuality();
        out.dropped_video_frames = q.droppedVideoFrames;
        out.total_video_frames = q.totalVideoFrames;
    }
    out.codec = detectCodec(video);
    // Viewport + rendered player size. An adaptive player picks its rendition
    // to fit the element it is drawn into, so without these a run capped by a
    // small window is indistinguishable from one capped by the shaped link.
    try {
        out.window_outer_w = window.outerWidth;
        out.window_outer_h = window.outerHeight;
        out.window_inner_w = window.innerWidth;
        out.window_inner_h = window.innerHeight;
        const r = video.getBoundingClientRect();
        out.player_elem_w = Math.round(r.width);
        out.player_elem_h = Math.round(r.height);
    } catch (e) { /* geometry is observability only - never fail a sample */ }
    // Census of every <video> on the page, not just the measured one, with each
    // element's ORIGIN. Size alone cannot distinguish a self-view from a remote
    // tile, so without the origin "we measured the peer" is an assumption; with
    // it the record proves which element was measured and why.
    try {
        const all = Array.from(document.querySelectorAll('video'));
        const box = (v) => {
            try {
                const b = v.getBoundingClientRect();
                return {
                    w: Math.round(Math.max(0, b.width)),
                    h: Math.round(Math.max(0, b.height))
                };
            } catch (e) { return { w: 0, h: 0 }; }
        };
        const dec = all.filter(v => v.videoWidth > 0 && v.videoHeight > 0);
        out.video_element_count = all.length;
        out.video_decoded_count = dec.length;
        out.video_rendered_count = dec.filter(v => box(v).w * box(v).h > 0).length;
        out.video_remote_decoded_count =
            dec.filter(v => videoOrigin(v).local !== true).length;
        out.video_local_decoded_count =
            dec.filter(v => videoOrigin(v).local === true).length;
        out.video_elements = all.slice(0, 10).map((v) => {
            const b = box(v);
            const o = videoOrigin(v);
            return {
                stream: v.videoWidth + 'x' + v.videoHeight,
                box: b.w + 'x' + b.h,
                origin: o.kind,
                local: o.local,
                label: (o.labels[0] || '').slice(0, 40),
                measured: v === video
            };
        });
        // The measured element, called out explicitly so a reader never has to
        // infer it from the census.
        const mo = videoOrigin(video);
        const mb = box(video);
        out.measured_element = {
            stream: video.videoWidth + 'x' + video.videoHeight,
            box: mb.w + 'x' + mb.h,
            box_area: mb.w * mb.h,
            origin: mo.kind,
            is_local: mo.local,
            label: (mo.labels[0] || '').slice(0, 40)
        };
        out.measured_is_local = mo.local;
        out.measured_origin = mo.kind;
        out.measured_box_area = mb.w * mb.h;
    } catch (e) { /* census is observability only - never fail a sample */ }
    // ---- uniform streaming-QoE probe readout --------------------------------
    // Flat keys only, so every existing consumer and the ~400 stored runs keep
    // working. Null means "not measurable here", never zero.
    try {
        const S = window.__netgentProbe;
        if (S) {
            let vb = 0, ab = 0, va = 0, aa = 0, vmime = null, amime = null;
            let switches = [];
            for (const r of S.buffers) {
                const m = String(r.mime || '');
                if (m.indexOf('video/') === 0) {
                    vb += r.bytes; va += r.appends; vmime = vmime || m;
                } else if (m.indexOf('audio/') === 0) {
                    ab += r.bytes; aa += r.appends; amime = amime || m;
                }
                if (r.mime_history && r.mime_history.length > 1) {
                    switches = switches.concat(r.mime_history.slice(1));
                }
            }
            // Audio and video are separate SourceBuffers; a combined total
            // overstates video bitrate (measured: 26% audio on YouTube).
            out.mse_video_bytes = vb;
            out.mse_audio_bytes = ab;
            out.mse_video_appends = va;
            out.mse_audio_appends = aa;
            out.mse_video_mime = vmime;
            out.mse_audio_mime = amime;
            out.mse_sourcebuffers = S.buffers.length;
            out.mse_mime_switches = switches.length;
            out.mse_mime_switch_log = switches.slice(0, 12);
            out.mse_appends_total = S.appends;

            // presentedFrames is frames submitted for composition - the
            // rendering rate. totalVideoFrames above counts DECODED frames.
            out.presented_frames = S.rvfc.installed ? S.rvfc.presented : null;
            out.rvfc_supported = !!S.rvfc.installed;
            out.first_presentation_ms = S.rvfc.first_presentation_ms;
            out.first_presented_media_time = S.rvfc.first_media_time;
            out.rvfc_frame_w = S.rvfc.width || null;
            out.rvfc_frame_h = S.rvfc.height || null;
            out.mean_processing_duration_ms = S.rvfc.processing_n
                ? (1000.0 * S.rvfc.processing_total / S.rvfc.processing_n) : null;

            // `waiting` -> `playing` spans are the spec's own definition of a
            // stall, independent of any progress-counter heuristic.
            out.media_event_counts = S.events;
            out.waiting_events = S.events['waiting'] || 0;
            out.waiting_spans = S.waiting_spans.slice(0, 40);
            out.waiting_total_ms = S.waiting_spans.reduce(
                (a, w) => a + (w.during_seek ? 0 : w.dur_ms), 0);
            out.seeking_events = S.events['seeking'] || 0;
            out.ratechange_events = S.events['ratechange'] || 0;
            out.stalled_events = S.events['stalled'] || 0;

            // Why a byte channel may be structurally unavailable here.
            out.main_world_mediasource_constructions = S.ms_constructions;
            out.worker_count = S.workers.length;
            out.uses_worker_media = !!(video && video.srcObject
                && S.ms_constructions === 0 && S.workers.length > 0);
        }
    } catch (e) { /* probe readout is observability only - never fail a sample */ }

    // ---- Resource Timing: an independent delivered-bytes channel -------------
    // Spec-zeroed for CORS-cross-origin responses, so eligibility is a property
    // of each CDN's headers. nextHopProtocol is empty exactly when the
    // timing-allow check fails, which makes it a free per-host eligibility read.
    try {
        let list = [];
        try { list = performance.getEntriesByType('resource') || []; } catch (e) { list = []; }
        let n = 0, bytes = 0, tao = 0, zero = 0;
        for (const e of list) {
            const it = e.initiatorType || '';
            if (!(it === 'xmlhttprequest' || it === 'fetch' || it === 'video'
                  || it === 'audio')) { continue; }
            const enc = e.encodedBodySize || 0;
            if (enc < 20000 && (e.transferSize || 0) < 20000) { continue; }
            n += 1; bytes += enc;
            if (e.nextHopProtocol) { tao += 1; }
            if (enc === 0) { zero += 1; }
        }
        out.rt_media_entries = n;
        out.rt_media_bytes = bytes;
        out.rt_tao_entries = tao;
        out.rt_size_zeroed_entries = zero;
        out.rt_sizes_usable = n > 0 && bytes > 0;
    } catch (e) { /* ditto */ }

    // Any countdown the conferencing UI shows about the meeting ending. A free
    // Zoom meeting dies at 40 minutes, and a wall-clock timer for that can only
    // ever be an estimate because the meeting starts before anything here can
    // observe it. If the page states the remaining time, that is ground truth
    // and worth capturing; it is NOT assumed to exist, so the field is simply
    // absent when no such text is present.
    try {
        const re = /(\d+)\s*minutes?\s*(left|remaining)|meeting\s+will\s+end|will\s+end\s+in|time\s+is\s+almost\s+up|ending\s+soon/i;
        const hits = [];
        const walk = document.body ? document.body.querySelectorAll('*') : [];
        for (const el of walk) {
            if (el.children && el.children.length) { continue; }  // leaf text only
            const t = (el.innerText || el.textContent || '').trim();
            if (t && t.length < 200 && re.test(t)) {
                if (hits.indexOf(t) === -1) { hits.push(t); }
                if (hits.length >= 3) { break; }
            }
        }
        if (hits.length) { out.meeting_notice = hits; }
    } catch (e) { /* notice scan is observability only */ }
    return out;
}

// Optional, additive. Present only on YouTube, and never required: if any of it
// throws or is missing, the generic numbers above still stand on their own.
function addYouTubeBonus(out) {
    const player = document.getElementById('movie_player')
        || document.querySelector('.html5-video-player');
    if (!player) { return; }
    // stats-for-nerds is YouTube's own instrumentation and is the GROUND TRUTH
    // this tool is validated against. It must therefore never flow into the
    // generic channel, or the validation would be comparing a measurement with
    // itself. Every key is namespaced `sfn_` and the un-namespaced merge is
    // opt-in via window.__netgentMergeVendorStats (off by default), where it
    // previously dropped all 33 keys straight into the flat namespace.
    try {
        if (typeof player.getStatsForNerds === 'function') {
            const nerds = player.getStatsForNerds();
            const merge = !!window.__netgentMergeVendorStats;
            for (const k in nerds) {
                out['sfn_' + k] = nerds[k];
                if (merge && !(k in out)) { out[k] = nerds[k]; }
            }
            out.sfn_available = true;
            out.sfn_merged_into_generic = merge;
        }
    } catch (e) { out.stats_for_nerds_error = String(e); }
    try {
        if (typeof player.getVideoData === 'function') {
            const vd = player.getVideoData();
            out.sfn_video_id = vd && vd.video_id;
            out.sfn_title = vd && vd.title;
        }
        if (typeof player.getDuration === 'function') {
            const dur = player.getDuration();
            // Kept out of duration_secs: the element's own duration is the
            // generic signal, and a vendor override would make YouTube's
            // generic channel differ in kind from every other app's.
            if (dur > 0) { out.sfn_duration_secs = dur; }
        }
        if (typeof player.getVideoLoadedFraction === 'function') {
            out.sfn_loaded_fraction = player.getVideoLoadedFraction();
        }
        if (typeof player.getPlayerState === 'function') {
            // IFrame-API state codes: 3 == BUFFERING, 1 == PLAYING. An
            // independent reference for rebuffering, and the only one available
            // for metrics stats-for-nerds does not cover.
            out.sfn_player_state = player.getPlayerState();
        }
    } catch (e) { out.player_data_error = String(e); }
}

function standardize(out) {
    // The documented, app-independent shape. The flat keys above stay exactly
    // as they were so shared/qoe.py and every stored run keep reading them.
    const num = v => (typeof v === 'number' && isFinite(v)) ? v : null;
    let bitrate = null;
    const nerdsBw = out.bandwidth_kbps;      // YouTube states this as a string
    if (typeof nerdsBw === 'string') {
        const m = nerdsBw.match(/([\d.]+)/);
        if (m) { bitrate = parseFloat(m[1]) / 1000; }
    }
    out.video = {
        width: num(out.video_width),
        height: num(out.video_height),
        fps: num(out.frames_per_second),
        bitrate: bitrate,                     // Mbps, null unless the app states it
        codec: out.codec || null,
    };
    out.playback = {
        current_time: num(out.current_time_secs),
        buffer_ahead: num(out.buffer_ahead_secs),
        rebuffering: (out.player_state === 3)
            || (out.paused === false && out.ready_state != null && out.ready_state < 3),
    };
    out.frames = {
        total: num(out.total_video_frames),
        dropped: num(out.dropped_video_frames),
    };
    return out;
}

const video = pickVideo();
const platform = host.indexOf('youtube.com') !== -1 || host.indexOf('youtu.be') !== -1
    ? 'youtube'
    : (host.indexOf('meet.google.com') !== -1 ? 'google_meet' : 'generic');
if (!video && platform !== 'youtube') {
    return {platform: platform, error: 'no_video'};
}
const out = genericVideoStats(video);
out.platform = platform;
if (platform === 'youtube') { addYouTubeBonus(out); }
if (!video && !out.video_width) { out.error = 'no_video'; }
return standardize(out);
"""


# Installed before any Google Meet page JavaScript runs. Keeping references to
# the real RTCPeerConnection objects lets the collector query receiver-side
# inbound RTP statistics instead of mistaking Meet's local preview <video> for
# remote-call QoE.
WEBRTC_HOOK_JS = r"""
(() => {
    const key = '__netgentPeerConnections';
    if (!Array.isArray(window[key])) {
        Object.defineProperty(window, key, {
            value: [], configurable: false, enumerable: false, writable: false
        });
    }

    function wrap(name) {
        const Native = window[name];
        if (!Native || Native.__netgentWrapped) return;
        const Wrapped = new Proxy(Native, {
            construct(target, args, newTarget) {
                const pc = Reflect.construct(target, args, newTarget);
                window[key].push(pc);
                return pc;
            }
        });
        Object.defineProperty(Wrapped, '__netgentWrapped', {value: true});
        window[name] = Wrapped;
    }

    wrap('RTCPeerConnection');
    wrap('webkitRTCPeerConnection');
})();
"""

# Selenium's execute_async_script supplies the final argument as a completion
# callback. Aggregate every active inbound video RTP stream: a Meet call may
# change SSRCs or receive more than one remote tile during a run.
GOOGLE_MEET_STATS_JS = r"""
const done = arguments[arguments.length - 1];
(async () => {
    const pcs = Array.from(new Set(window.__netgentPeerConnections || []));
    const inbound = [];

    for (let pcIndex = 0; pcIndex < pcs.length; pcIndex += 1) {
        const pc = pcs[pcIndex];
        const report = await pc.getStats();
        const codecs = {};
        report.forEach(stat => {
            if (stat.type === 'codec') codecs[stat.id] = stat.mimeType || null;
        });
        report.forEach(stat => {
            const mediaKind = stat.kind || stat.mediaType;
            if (stat.type !== 'inbound-rtp' || mediaKind !== 'video' || stat.isRemote) {
                return;
            }
            inbound.push({
                peer_connection_index: pcIndex,
                id: stat.id,
                ssrc: stat.ssrc ?? null,
                codec: codecs[stat.codecId] || null,
                bytes_received: stat.bytesReceived || 0,
                header_bytes_received: stat.headerBytesReceived || 0,
                packets_received: stat.packetsReceived || 0,
                packets_lost: stat.packetsLost || 0,
                packets_discarded: stat.packetsDiscarded || 0,
                jitter_seconds: stat.jitter || 0,
                frames_received: stat.framesReceived || 0,
                frames_decoded: stat.framesDecoded || 0,
                frames_rendered: stat.framesRendered || 0,
                frames_dropped: stat.framesDropped || 0,
                key_frames_decoded: stat.keyFramesDecoded || 0,
                frames_per_second: stat.framesPerSecond || 0,
                frame_width: stat.frameWidth || 0,
                frame_height: stat.frameHeight || 0,
                total_decode_time_seconds: stat.totalDecodeTime || 0,
                jitter_buffer_delay_seconds: stat.jitterBufferDelay || 0,
                jitter_buffer_emitted_count: stat.jitterBufferEmittedCount || 0,
                freeze_count: stat.freezeCount || 0,
                total_freezes_duration_seconds: stat.totalFreezesDuration || 0,
                pause_count: stat.pauseCount || 0,
                total_pauses_duration_seconds: stat.totalPausesDuration || 0,
                nack_count: stat.nackCount || 0,
                pli_count: stat.pliCount || 0,
                fir_count: stat.firCount || 0,
            });
        });
    }

    const active = inbound.filter(
        stream => stream.bytes_received > 0 || stream.frames_decoded > 0
    );
    const primary = active.reduce(
        (best, stream) => !best || stream.bytes_received > best.bytes_received
            ? stream : best,
        null,
    );
    const sum = field => active.reduce((total, stream) => total + (stream[field] || 0), 0);

    done({
        platform: 'google_meet',
        source: 'webrtc_inbound_rtp',
        peer_connection_count: pcs.length,
        inbound_video_stream_count: active.length,
        bytes_received: sum('bytes_received'),
        header_bytes_received: sum('header_bytes_received'),
        packets_received: sum('packets_received'),
        packets_lost: sum('packets_lost'),
        packets_discarded: sum('packets_discarded'),
        frames_received: sum('frames_received'),
        frames_decoded: sum('frames_decoded'),
        frames_rendered: sum('frames_rendered'),
        frames_dropped: sum('frames_dropped'),
        key_frames_decoded: sum('key_frames_decoded'),
        frames_per_second: sum('frames_per_second'),
        total_decode_time_seconds: sum('total_decode_time_seconds'),
        jitter_buffer_delay_seconds: sum('jitter_buffer_delay_seconds'),
        jitter_buffer_emitted_count: sum('jitter_buffer_emitted_count'),
        freeze_count: sum('freeze_count'),
        total_freezes_duration_seconds: sum('total_freezes_duration_seconds'),
        pause_count: sum('pause_count'),
        total_pauses_duration_seconds: sum('total_pauses_duration_seconds'),
        nack_count: sum('nack_count'),
        pli_count: sum('pli_count'),
        fir_count: sum('fir_count'),
        jitter_seconds: active.reduce(
            (maximum, stream) => Math.max(maximum, stream.jitter_seconds || 0), 0
        ),
        frame_width: primary ? primary.frame_width : 0,
        frame_height: primary ? primary.frame_height : 0,
        resolution: primary ? `${primary.frame_width}x${primary.frame_height}` : '0x0',
        codecs: Array.from(new Set(active.map(stream => stream.codec).filter(Boolean))),
        inbound_streams: active,
    });
})().catch(error => done({
    platform: 'google_meet',
    source: 'webrtc_inbound_rtp',
    error: String(error),
}));
"""

GOOGLE_MEET_JOIN_JS = r"""
const guestName = arguments[0];

// Guest rooms show a name field before "Ask to join". Use the native setter
// so Meet's React handlers observe the value instead of only the DOM changing.
const nameInput = Array.from(document.querySelectorAll('input')).find(input => {
    const label = `${input.placeholder || ''} ${input.getAttribute('aria-label') || ''}`
        .trim().toLowerCase();
    return label.includes('your name') || label === 'name';
});
if (nameInput && guestName && nameInput.value !== guestName) {
    const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
    ).set;
    setter.call(nameInput, guestName);
    nameInput.dispatchEvent(new Event('input', {bubbles: true}));
    nameInput.dispatchEvent(new Event('change', {bubbles: true}));
    return {
        clicked: false,
        guest_name_entered: true,
        state: 'waiting_for_ui_update',
    };
}

const buttons = Array.from(document.querySelectorAll('button'));
for (const button of buttons) {
    const label = `${button.innerText || ''} ${button.getAttribute('aria-label') || ''}`
        .trim().toLowerCase();
    if (label.includes('join now') || label.includes('ask to join')) {
        button.click();
        return {clicked: true, label, guest_name_entered: Boolean(nameInput)};
    }
}
return {clicked: false};
"""


ZOOM_JOIN_JS = r"""
// Zoom's web client guest flow: type a display name, then press Join. Reached
// via /wc/<id>/join?pwd=... - the /j/ invite page offers "Join from browser"
// but silently swallows the click under automation, so runs must not use it.
const guestName = arguments[0];
const out = {typed: false, clicked: null, in_call: false, started_video: false,
             joined_audio: null, unmuted: false};

// Already inside? The meeting toolbar only exists in-call.
out.in_call = !!document.querySelector(
    '[aria-label*="Leave" i],[aria-label*="participant" i],[class*="footer-button"]'
) && !/let them know you.?re here|waiting for the host/i.test(document.body.innerText || '');

const inputs = Array.from(document.querySelectorAll('input'));
const nameInput = inputs.find(i => {
    const l = `${i.placeholder || ''} ${i.getAttribute('aria-label') || ''} ${i.id || ''}`
        .toLowerCase();
    return (l.includes('name') || l.includes('input-for-name')) && !l.includes('pass');
});
if (nameInput && guestName && nameInput.value !== guestName) {
    const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, 'value'
    ).set;
    setter.call(nameInput, guestName);
    nameInput.dispatchEvent(new Event('input', {bubbles: true}));
    nameInput.dispatchEvent(new Event('change', {bubbles: true}));
    out.typed = true;
}
for (const b of document.querySelectorAll('button,[role="button"]')) {
    const l = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
    if (l === 'join' || l === 'join meeting' || l === 'join audio by computer') {
        b.click(); out.clicked = l; break;
    }
}

// Connect audio. Zoom puts a fresh web-client participant behind an audio
// prompt ("Join Audio" -> "Join with Computer Audio"); until it is taken the
// participant is only half-joined, which can hold back the media negotiation.
// Harika's zoom-data-collection workflow does this same pair of clicks before
// touching video, so it happens here first too.
for (const b of document.querySelectorAll('button,[role="button"],a')) {
    const l = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
    if (l === 'join audio' || l === 'join audio by computer'
        || l === 'join with computer audio' || l.includes('computer audio')) {
        b.click(); out.joined_audio = l; break;
    }
}
// Unmute if the mic came up muted - a muted track still negotiates, but Zoom
// treats a silent participant differently and may suspend the stream.
for (const b of document.querySelectorAll('button,[role="button"]')) {
    const l = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
    if (l === 'unmute' || l === 'unmute my audio' || l.startsWith('unmute')) {
        b.click(); out.unmuted = true; break;
    }
}

// Publish video. Zoom can join with the camera off, and then the fake device
// never reaches the other side - the peer sees an avatar tile, no <video> is
// created, and downstream that reads as "nobody is publishing". The control is
// a toggle: "start video" means the camera is currently OFF.
for (const b of document.querySelectorAll('button,[role="button"]')) {
    const l = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
    if (l === 'start video' || l === 'start my video' || l.includes('start video')) {
        b.click(); out.started_video = true; break;
    }
}
return out;
"""


ZOOM_READY_JS = r"""
// Remote camera video is a MediaStream-backed <video> with real intrinsic
// dimensions. The sharer placeholders are 0x0, so size is what separates them.
// The three not-ready cases are reported apart: a waiting room, being in the
// call with nobody publishing, and not having joined are different problems,
// and collapsing them into one reason made the previous run ambiguous.
const _t = (document.body.innerText || '').replace(/\s+/g, ' ');
const _waiting = /let them know you.?re here|waiting for the host/i.test(_t);
const _inCall = !!document.querySelector(
    '[aria-label*="Leave" i],[aria-label*="participant" i],[class*="footer-button"]'
);
const live = Array.from(document.querySelectorAll('video'))
    .filter(v => v.videoWidth > 0 && v.videoHeight > 0);
if (!live.length) {
    return {ok: false, waiting: _waiting, in_call: _inCall && !_waiting,
            reason: _waiting ? 'waiting_room'
                  : (_inCall ? 'in_call_no_video' : 'not_joined')};
}
const v = live[0];
let frames = null;
if (typeof v.getVideoPlaybackQuality === 'function') {
    frames = v.getVideoPlaybackQuality().totalVideoFrames;
}
return {ok: true, w: v.videoWidth, h: v.videoHeight, frames: frames,
        paused: v.paused};
"""


def wait_for_zoom_join(driver, app, timeout_s=900.0, guest_name="pramana"):
    """Click through Zoom's guest join, then wait for remote video to decode.

    Admission may be manual, so this keeps trying rather than failing fast. The
    ready signal is advancing decoded frames - Zoom's currentTime never moves,
    so a clock test would wait forever on a healthy call.
    """
    deadline = time.time() + timeout_s
    last_frames = None
    last_note = 0.0
    while time.time() < deadline:
        try:
            r = driver.execute_script(ZOOM_JOIN_JS, guest_name)
            if r.get("clicked"):
                print(
                    f"[{app}] clicked Zoom join control: {r.get('clicked')}", flush=True
                )
            if r.get("joined_audio"):
                print(
                    f"[{app}] connected audio (clicked '{r.get('joined_audio')}')",
                    flush=True,
                )
            if r.get("started_video"):
                # Worth logging: without this the browser sits in the call with
                # its camera off and never publishes the fake device to the peer.
                print(f"[{app}] turned the camera on (clicked Start Video)", flush=True)
        except Exception as exc:  # noqa: BLE001 - control may not exist yet
            print(
                f"[{app}] zoom join control not ready: {type(exc).__name__}", flush=True
            )
        try:
            st = driver.execute_script(ZOOM_READY_JS)
        except Exception:  # noqa: BLE001
            st = {"ok": False, "reason": "script_error"}
        if st.get("ok"):
            f = st.get("frames")
            if last_frames is not None and f is not None and f > last_frames:
                print(
                    f"[{app}] remote video decoding: {st['w']}x{st['h']}, "
                    f"frames {last_frames} -> {f}",
                    flush=True,
                )
                return st
            last_frames = f
        elif time.time() - last_note > 20:
            last_note = time.time()
            why = {
                "waiting_room": "in Zoom's waiting room - admit this participant",
                "in_call_no_video": "in the call, but nobody is publishing video",
                "not_joined": "not in the call yet (join control not taken)",
            }.get(st.get("reason"), st.get("reason"))
            print(f"[{app}] {why}", flush=True)
        time.sleep(3)
    print(
        f"[{app}] WARNING: no advancing remote video within {timeout_s:.0f}s",
        flush=True,
    )
    return {"ok": False, "reason": "timeout"}


def start_display(dnum: str) -> None:
    """Start Xvfb + fluxbox on display :dnum, matching netgent-dev's start.sh."""
    resolution = os.environ.get("RESOLUTION", "1920x1080x24")
    subprocess.Popen(["Xvfb", f":{dnum}", "-screen", "0", resolution])
    time.sleep(2)
    subprocess.Popen(
        ["fluxbox"],
        env={**os.environ, "DISPLAY": f":{dnum}"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1)


def build_plain_driver(args: list[str], user_data_dir: str | None = None):
    """Stock webdriver Chrome, for targets that need working WebRTC.

    Measured on this image: undetected-chromedriver gathers ZERO ICE candidates
    - iceGatheringState reaches "complete" with an empty list, so no candidate
    pair can form and no RTP ever flows. The same flags under a stock driver
    gather 9 candidates on the same host. Consumer sites still need UC mode to
    get past bot walls, so the choice is per-app (AppSpec.driver_mode) rather
    than global.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    for a in args:
        opts.add_argument(a)
    if user_data_dir:
        opts.add_argument(f"--user-data-dir={user_data_dir}")
    return webdriver.Chrome(options=opts)


def build_driver(user_data_dir: str | None = None, mode: str = "uc"):
    from seleniumbase import Driver

    args = [
        "--force-device-scale-factor=1",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        # Chrome withholds host ICE candidates from a page that has not been
        # granted media access, so a receive-only page gathers zero candidates,
        # reports gathering 'complete', forms no candidate pairs and never
        # receives a byte. A page served over plain HTTP cannot earn the
        # permission either - navigator.mediaDevices only exists in a secure
        # context. This states the policy directly instead, which is what a
        # measurement host on a closed link wants anyway.
        "--force-webrtc-ip-handling-policy=default",
        "--window-size=1920,1080",
        "--start-maximized",
        "--disable-gpu",
        # Without this, autoplay=1 in the URL is silently ignored by Chrome's
        # autoplay policy and the player sits paused at t=0 forever (found via
        # live debugging - not in the netgent-dev reference recipe).
        "--autoplay-policy=no-user-gesture-required",
        # ── keep the shaped link for the app under test ──────────────────────
        # Measured on our own captures: Chrome's own background fetches took a
        # median 65% of the shaped download (up to ~50% of a 10 Mbps link),
        # competing directly with the video and confounding every per-app QoE
        # number. Two sources, both of which the page never requested:
        #   optimizationguide-pa.googleapis.com  - ML model download (HTTPS)
        #   edgedl.me.gvt1.com                   - component updater (HTTP:80,
        #                                          hardcoded IP, no DNS/SNI)
        # These flags stop them at the source; the host-resolver rules below are
        # only a safety net. Nothing here touches playback or how QoE is read.
        "--disable-features=OptimizationHints,OptimizationGuideModelDownloading,"
        "OptimizationGuideModelPushNotifications,OptimizationTargetPrediction",
        "--disable-component-update",
        "--disable-background-networking",
        "--disable-domain-reliability",
        "--no-first-run",
        "--no-default-browser-check",
        # Safety net, pinned to the two offending hostnames ONLY. Deliberately
        # not a wildcard on gvt1.com: r*.sn-*.gvt1.com serves YouTube media and
        # must keep resolving normally.
        "--host-resolver-rules=MAP optimizationguide-pa.googleapis.com 127.0.0.1,"
        "MAP edgedl.me.gvt1.com 127.0.0.1,"
        "MAP update.googleapis.com 127.0.0.1",
    ]
    chromium_arg = ",".join(args)
    kwargs: dict[str, Any] = dict(
        uc=True,
        headed=True,
        browser="chrome",
        chromium_arg=chromium_arg,
        use_auto_ext=False,
        undetectable=True,
    )
    binary_location = os.environ.get("CHROME_BINARY")
    if binary_location:
        kwargs["binary_location"] = binary_location
    if user_data_dir:
        kwargs["user_data_dir"] = user_data_dir
    if mode == "plain":
        return build_plain_driver(args, user_data_dir=user_data_dir)
    return Driver(**kwargs)


def install_webrtc_hook(driver: Any) -> None:
    """Install the peer-connection hook before Meet loads any page scripts."""
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": WEBRTC_HOOK_JS},
    )


# Makes a codec family look unsupported so an adaptive player never selects it.
# Needed because a player's ladder is bounded by what it believes it can decode,
# not by the element it draws into: with a 945x709 element and 10Mbps spare,
# Vimeo still chose 1440x1080 AV1 (83/117 samples) and then could not decode it
# in real time on a 2-vCPU host, collapsing to 320x240. Vimeo's own
# `quality=720p` parameter is not an answer either - it FORCES one rendition and
# switches ABR off (960x720 for 116/116 samples, zero switches), deleting the
# adaptive behaviour under test. Removing the undecodable codec family instead
# leaves ABR free and resolution network-responsive. All three decision points a
# player may consult are covered.
CODEC_BLOCK_JS_TMPL = r"""
(() => {
    const PATTERNS = __PATTERNS__;
    const blocked = (s) => {
        try {
            const t = String(s || '').toLowerCase();
            return PATTERNS.some(p => t.indexOf(p) !== -1);
        } catch (e) { return false; }
    };
    try {
        const MS = window.MediaSource || window.WebKitMediaSource;
        if (MS && MS.isTypeSupported) {
            const nativeIs = MS.isTypeSupported.bind(MS);
            MS.isTypeSupported = (t) => (blocked(t) ? false : nativeIs(t));
        }
    } catch (e) { /* leave support reporting alone if it cannot be wrapped */ }
    try {
        const proto = window.HTMLMediaElement && window.HTMLMediaElement.prototype;
        if (proto && proto.canPlayType) {
            const nativeCan = proto.canPlayType;
            proto.canPlayType = function (t) {
                return blocked(t) ? '' : nativeCan.call(this, t);
            };
        }
    } catch (e) { /* ditto */ }
    try {
        const mc = navigator.mediaCapabilities;
        if (mc && mc.decodingInfo) {
            const nativeInfo = mc.decodingInfo.bind(mc);
            mc.decodingInfo = (cfg) => {
                try {
                    if (cfg && cfg.video && blocked(cfg.video.contentType)) {
                        return Promise.resolve({
                            supported: false, smooth: false, powerEfficient: false
                        });
                    }
                } catch (e) { /* fall through to native */ }
                return nativeInfo(cfg);
            };
        }
    } catch (e) { /* ditto */ }
})();
"""


def install_codec_block(driver: Any, patterns: list[str]) -> None:
    """Hide `patterns` codecs from the page, before any page script runs."""
    if not patterns:
        return
    src = CODEC_BLOCK_JS_TMPL.replace("__PATTERNS__", json.dumps(patterns))
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": src})
        print(f"[collector] codecs hidden from page: {patterns}", flush=True)
    except Exception as exc:  # noqa: BLE001 - never fail a run over this
        print(f"[collector] codec block unavailable: {type(exc).__name__}", flush=True)


def install_vendor_merge_flag(driver: Any, merge: bool) -> None:
    """Opt in to merging a vendor's own stats into the generic namespace.

    Off for validation runs: stats-for-nerds is the reference the generic channel
    is measured against, so letting it flow into the generic fields would make
    the comparison circular.
    """
    src = "window.__netgentMergeVendorStats = %s;" % ("true" if merge else "false")
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": src})
    except Exception as exc:  # noqa: BLE001 - never fail a run over a flag
        print(f"[collector] vendor-merge flag unavailable: {type(exc).__name__}")


def install_media_hook(driver: Any) -> None:
    """Record source-buffer mime types so the generic path can name the codec.

    Must run before the page creates its MediaSource, so it goes in on the same
    new-document channel as the peer-connection hook. Best effort: a browser
    that refuses the CDP call still yields every other generic metric.
    """
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": MEDIA_HOOK_JS},
        )
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": STREAM_PROBE_JS},
        )
    except Exception as exc:  # noqa: BLE001 - codec is a bonus, never a gate
        print(f"[collector] codec hook unavailable: {type(exc).__name__}", flush=True)


def get_google_meet_stats(driver: Any) -> dict[str, Any]:
    stats = driver.execute_async_script(GOOGLE_MEET_STATS_JS)
    if not isinstance(stats, dict):
        raise RuntimeError(f"Google Meet returned invalid WebRTC stats: {stats!r}")
    if stats.get("error"):
        raise RuntimeError(f"Google Meet WebRTC stats failed: {stats['error']}")
    return stats


def wait_for_google_meet_inbound(
    driver: Any,
    timeout_seconds: float,
    guest_name: str,
) -> dict[str, Any]:
    """Join Meet and require a genuinely advancing remote inbound video RTP stream."""
    deadline = time.monotonic() + timeout_seconds
    previous: dict[str, Any] | None = None
    latest: dict[str, Any] = {}

    while time.monotonic() < deadline:
        try:
            join_result = driver.execute_script(GOOGLE_MEET_JOIN_JS, guest_name)
            if join_result and join_result.get("clicked"):
                print(f"[google_meet] clicked Meet join control: {join_result}")
        except Exception as exc:  # noqa: BLE001 - retry while UI settles
            print(f"[google_meet] join control not ready: {exc}")

        try:
            latest = get_google_meet_stats(driver)
        except Exception as exc:  # noqa: BLE001 - retry while Meet initializes
            print(f"[google_meet] waiting for WebRTC stats: {exc}")
            time.sleep(1)
            continue

        if previous is not None and latest.get("inbound_video_stream_count", 0) > 0:
            bytes_advanced = latest.get("bytes_received", 0) > previous.get(
                "bytes_received", 0
            )
            frames_advanced = latest.get("frames_decoded", 0) > previous.get(
                "frames_decoded", 0
            )
            if bytes_advanced and frames_advanced:
                print(
                    "[google_meet] confirmed advancing remote inbound video: "
                    f"bytes={latest['bytes_received']} "
                    f"frames={latest['frames_decoded']}"
                )
                return latest
        previous = latest
        time.sleep(1)

    raise RuntimeError(
        "Google Meet never produced an advancing inbound video RTP stream. "
        "Confirm the profile is logged in, the browser joined the room, and "
        "another participant is publishing video. Local preview video is not accepted. "
        f"Last WebRTC stats: {latest}"
    )


def add_google_meet_interval_metrics(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    elapsed_seconds: float | None,
) -> None:
    """Add rates/deltas derived from cumulative WebRTC counters."""
    if previous is None or elapsed_seconds is None or elapsed_seconds <= 0:
        return

    def delta(field: str) -> float:
        return max(0.0, float(current.get(field, 0)) - float(previous.get(field, 0)))

    byte_delta = delta("bytes_received")
    received_delta = delta("packets_received")
    lost_delta = delta("packets_lost")
    decoded_delta = delta("frames_decoded")
    dropped_delta = delta("frames_dropped")
    current["inbound_bitrate_mbps"] = byte_delta * 8 / elapsed_seconds / 1_000_000
    current["packets_received_delta"] = int(received_delta)
    current["packets_lost_delta"] = int(lost_delta)
    packet_total = received_delta + lost_delta
    current["packet_loss_percent"] = (
        100 * lost_delta / packet_total if packet_total else 0.0
    )
    current["frames_decoded_delta"] = int(decoded_delta)
    current["frames_dropped_delta"] = int(dropped_delta)
    frame_total = decoded_delta + dropped_delta
    current["frame_drop_percent"] = (
        100 * dropped_delta / frame_total if frame_total else 0.0
    )
    current["freeze_count_delta"] = int(delta("freeze_count"))
    current["freeze_duration_delta_seconds"] = delta("total_freezes_duration_seconds")


FORCE_MAX_QUALITY_JS = """
const p = document.getElementById('movie_player')
       || document.querySelector('.html5-video-player');
if (!p) { return {ok: false, reason: 'no_player_element'}; }
const out = {ok: true, levels: null, range_applied: false, single_applied: false};
try { out.levels = p.getAvailableQualityLevels ? p.getAvailableQualityLevels() : null; }
catch (e) { out.levels_error = String(e); }
try {
    if (p.setPlaybackQualityRange) {
        p.setPlaybackQualityRange(arguments[0], arguments[0]);
        out.range_applied = true;
    }
} catch (e) { out.range_error = String(e); }
if (!out.range_applied) {
    try {
        if (p.setPlaybackQuality) { p.setPlaybackQuality(arguments[0]); out.single_applied = true; }
    } catch (e) { out.single_error = String(e); }
}
try { out.quality_after = p.getPlaybackQuality ? p.getPlaybackQuality() : null; }
catch (e) { out.quality_after_error = String(e); }
return out;
"""


def force_youtube_max_quality(
    driver, app: str, level: str = "hd2160"
) -> dict[str, Any]:
    """Ask YouTube's player to pin the highest rendition instead of ABR auto.

    `setPlaybackQualityRange` is undocumented and silently ignores levels the
    source does not carry, so this is best-effort by construction: it reports
    what it managed to do and never raises into the run. The available-level
    list is captured too — without it a 480p result is ambiguous between "ABR
    chose low" and "the source has nothing higher".
    """
    try:
        result = driver.execute_script(FORCE_MAX_QUALITY_JS, level)
    except Exception as exc:  # the page may not expose the player API at all
        result = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    print(f"[{app}] force_max_quality({level}) -> {result}")
    return result if isinstance(result, dict) else {"ok": False, "reason": "bad_result"}


GEOMETRY_JS = """
const v = document.querySelector('video');
const r = v ? v.getBoundingClientRect() : null;
return {
    outer_w: window.outerWidth, outer_h: window.outerHeight,
    inner_w: window.innerWidth, inner_h: window.innerHeight,
    screen_w: screen.width, screen_h: screen.height,
    player_w: r ? Math.round(r.width) : null,
    player_h: r ? Math.round(r.height) : null,
};
"""

_WANT_W, _WANT_H = 1920, 1080


def ensure_window_size(
    driver, app, want_w=_WANT_W, want_h=_WANT_H, attempts=5, settle_s=0.8
):
    """Set the window rect and confirm it actually took effect, retrying if not.

    Setting it once is not enough. fluxbox manages the window asynchronously and
    can re-apply its own geometry after `set_window_rect` has already returned,
    silently reverting it. That race is invisible without a read-back, and when
    it bites an adaptive player sizes its rendition to the small element - which
    is how concurrent runs produced 480p on a 50 Mbps link with 48 Mbps spare.

    Returns the measured geometry so the run records the viewport it really had.
    """
    geom = {}
    for i in range(1, attempts + 1):
        try:
            driver.set_window_rect(x=0, y=0, width=want_w, height=want_h)
        except Exception as exc:  # noqa: BLE001 - never fail a run over geometry
            print(f"[{app}] warning: set_window_rect raised: {exc}")
        time.sleep(settle_s)
        try:
            geom = driver.execute_script(GEOMETRY_JS) or {}
        except Exception:  # noqa: BLE001 - page may not be ready to run JS yet
            geom = {}
        ow, oh = geom.get("outer_w") or 0, geom.get("outer_h") or 0
        if ow >= want_w * 0.95 and oh >= want_h * 0.90:
            geom.update(ok=True, attempts=i, wanted=f"{want_w}x{want_h}")
            print(f"[{app}] window {ow}x{oh} confirmed after {i} attempt(s)")
            return geom
        print(
            f"[{app}] window is {ow}x{oh}, want {want_w}x{want_h} - retrying ({i}/{attempts})"
        )
    geom.update(ok=False, attempts=attempts, wanted=f"{want_w}x{want_h}")
    print(
        f"[{app}] WARNING: window stuck at {geom.get('outer_w')}x{geom.get('outer_h')} "
        f"after {attempts} attempts - rendition may be viewport-capped"
    )
    return geom


MEDIA_STATE_JS = """
const v = document.querySelector('video');
if (!v) { return {ok: false, reason: 'no_video'}; }
let bstart = null, bend = null, nranges = 0;
try {
    nranges = v.buffered ? v.buffered.length : 0;
    if (nranges) { bstart = v.buffered.start(0); bend = v.buffered.end(nranges - 1); }
} catch (e) { /* buffered can throw before init */ }
return {
    ok: true,
    ready_state: v.readyState,
    current_time: v.currentTime,
    paused: v.paused,
    buffered_start: bstart,
    buffered_end: bend,
    buffered_ranges: nranges,
    network_state: v.networkState,
};
"""

PLAY_JS = """
const v = document.querySelector('video');
if (!v) { return {started: false, reason: 'no_video'}; }
v.muted = true;
v.play().catch(e => {});
return {started: true, paused: v.paused, ready_state: v.readyState,
        current_time: v.currentTime};
"""

# HTMLMediaElement.readyState: 3 == HAVE_FUTURE_DATA (enough to start playing).
_READY_ENOUGH = 3


def wait_for_media_ready(driver, app, timeout_s=25.0, poll_s=0.3):
    """Block until the <video> actually has data at the current position.

    The collector used to fire play() the instant the synchronized barrier
    released, regardless of the element's state. YouTube is normally at
    readyState 4 by then, but Vimeo's MSE buffer often is not: under contention
    its load is delayed, play() lands on a readyState-1 element, and the element
    then sits unpaused with a frozen buffer and a clock that never advances --
    observed as a false "no_playback" for the whole run.

    Returns the last observed state dict; `ready` says whether it got there.
    """
    deadline = time.monotonic() + timeout_s
    state = {}
    while time.monotonic() < deadline:
        try:
            state = driver.execute_script(MEDIA_STATE_JS) or {}
        except Exception as exc:  # page still settling; keep polling
            state = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        if state.get("ok") and (state.get("ready_state") or 0) >= _READY_ENOUGH:
            state["ready"] = True
            waited = timeout_s - (deadline - time.monotonic())
            print(
                f"[{app}] media ready after {waited:.1f}s "
                f"(readyState={state.get('ready_state')}, "
                f"buffered={state.get('buffered_start')}..{state.get('buffered_end')})"
            )
            return state
        time.sleep(poll_s)
    state["ready"] = False
    # Loud, not silent: a genuinely broken load must be visible in the log.
    print(
        f"[{app}] WARNING: media not ready after {timeout_s:.0f}s "
        f"(readyState={state.get('ready_state')}, "
        f"buffered={state.get('buffered_start')}..{state.get('buffered_end')}, "
        f"networkState={state.get('network_state')}) - playing anyway"
    )
    return state


def start_playback(driver, app, attempts=4, settle_s=1.0):
    """Fire play() and confirm the clock actually moves; retry if it does not.

    One unverified play() was the other half of the bug: if the first call did
    not take, nothing ever tried again.
    """
    last = {}
    for i in range(1, attempts + 1):
        try:
            last = driver.execute_script(PLAY_JS) or {}
        except Exception as exc:
            last = {"started": False, "reason": f"{type(exc).__name__}: {exc}"}
        before = last.get("current_time")
        time.sleep(settle_s)
        try:
            now = driver.execute_script(MEDIA_STATE_JS) or {}
        except Exception:
            now = {}
        advanced = (
            now.get("current_time") is not None
            and before is not None
            and now["current_time"] > before
        )
        if advanced:
            print(
                f"[{app}] playback confirmed on attempt {i} "
                f"(clock {before:.2f} -> {now['current_time']:.2f})"
            )
            last["attempts"] = i
            last["confirmed"] = True
            return last
        print(
            f"[{app}] play attempt {i}/{attempts} did not advance the clock "
            f"(readyState={now.get('ready_state')}, paused={now.get('paused')})"
        )
    last["attempts"] = attempts
    last["confirmed"] = False
    return last


# Floor for "this is the remote peer's main tile", derived from the runs that
# demonstrably measured the peer: a 1756x988 main view, 1,734,928 px^2. The same
# view in a different Zoom layout measured 1458x820 (1,195,560), while the local
# self-view thumbnail that silently replaced it was 207x117 (24,219). A 35% floor
# therefore accepts both real layouts and rejects the thumbnail by nearly two
# orders of magnitude.
REMOTE_TILE_REFERENCE_AREA = 1756 * 988
REMOTE_TILE_MIN_AREA = int(
    os.environ.get(
        "PRAMANA_REMOTE_TILE_MIN_AREA", REMOTE_TILE_REFERENCE_AREA * 35 // 100
    )
)
# How long the page gets to lay the tile out before the gate bites. Joining,
# the gallery settling and the first remote frame all happen inside this.
REMOTE_TILE_GRACE_S = float(os.environ.get("PRAMANA_REMOTE_TILE_GRACE_S", "20"))


class RemoteTileMissing(RuntimeError):
    """The measured element is not the remote peer's main tile."""


def _assert_remote_tile(
    app: str, job: dict[str, Any], samples: list[dict], started_at: float
) -> None:
    """Fail a conferencing cell early when it is measuring the wrong element.

    Replaces an earlier check that required exactly one <video> on the page.
    That was never satisfiable: Zoom always exposes several elements for the same
    streams, so the check could only ever have failed. What actually matters is
    narrower and checkable - the element being measured must be a REMOTE stream
    (never the collector's own self-view) and must be laid out at main-view size.

    Raised rather than logged: a cell that is measuring the local camera cannot
    be salvaged afterwards, and a 180s run plus teardown is most of a free
    meeting's remaining window.
    """
    if not job.get("require_remote_tile"):
        return
    if time.time() - started_at < REMOTE_TILE_GRACE_S:
        return
    stats = next((s["stats"] for s in reversed(samples) if s.get("stats")), None)
    if not stats:
        raise RemoteTileMissing(f"{app}: no player samples within grace period")
    me = stats.get("measured_element") or {}
    if stats.get("measured_is_local") is True:
        raise RemoteTileMissing(
            f"{app}: measuring the LOCAL self-view "
            f"({me.get('stream')}@{me.get('box')}, label={me.get('label')!r}); "
            "the remote peer's tile is not being measured"
        )
    area = stats.get("measured_box_area") or 0
    if area < REMOTE_TILE_MIN_AREA:
        raise RemoteTileMissing(
            f"{app}: measured tile too small to be the main view "
            f"({me.get('stream')}@{me.get('box')}, area {area} < "
            f"{REMOTE_TILE_MIN_AREA}); remote tiles decoded="
            f"{stats.get('video_remote_decoded_count')}"
        )


def run_job(
    job: dict[str, Any],
    launch_lock: threading.Lock,
    sampling_barrier: threading.Barrier,
) -> None:
    app = job["app"]
    # Registry-driven routing (shared/apps.py). Defaults keep older JOBS payloads
    # that only carried an app name working unchanged.
    kind = str(job.get("kind") or ("webrtc" if app == "google_meet" else "html5_video"))
    is_webrtc = kind == "webrtc"
    video_url = job["url"]
    display_num = str(job["display_num"])
    out_path = Path(job["out_path"])
    duration_seconds = float(job["duration_seconds"])
    sample_interval_seconds = float(job.get("sample_interval_seconds", 1.0))
    user_data_dir = job.get("user_data_dir")
    guest_name = str(job.get("guest_name", "NetGent QoE Collector"))
    meet_join_timeout_seconds = float(job.get("join_timeout_seconds", 180))
    barrier_timeout_seconds = float(job.get("barrier_timeout_seconds", 360))
    # Opt-in: pin YouTube's rendition instead of letting ABR pick. Off by
    # default, so every existing payload behaves exactly as before.
    media_ready_timeout_seconds = float(job.get("media_ready_timeout_seconds", 25))
    force_max_quality = bool(job.get("force_max_quality", False))
    force_quality_level = str(job.get("force_quality_level", "hd2160"))
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[{app}] starting Xvfb+fluxbox on :{display_num}")
    start_display(display_num)

    # os.environ and pyautogui's X11 backend are process-global, so only one
    # job may be mid-launch (setting DISPLAY, then constructing the Driver
    # that reads it) at a time. Each job's own Xvfb/display is independent -
    # this lock only serializes the brief "point DISPLAY here and launch
    # Chrome against it" window, not the whole job.
    with launch_lock:
        os.environ["DISPLAY"] = f":{display_num}"
        try:
            import Xlib.display
            import pyautogui

            pyautogui._pyautogui_x11._display = Xlib.display.Display(
                os.environ["DISPLAY"]
            )
        except Exception as exc:  # noqa: BLE001 - best-effort, not critical
            print(f"[{app}] warning: could not wire Xlib display for pyautogui: {exc}")

        print(
            f"[{app}] launching undetected-chromedriver Chrome (headed, DISPLAY=:{display_num})"
        )
        driver_mode = str(job.get("driver_mode") or "uc")
        if driver_mode != "uc":
            print(f"[{app}] using the {driver_mode} chrome driver", flush=True)
        driver = build_driver(user_data_dir=user_data_dir, mode=driver_mode)
        # Chrome/SeleniumBase silently ignores --window-size under Xvfb+fluxbox:
        # measured here, the window came up 945x1040 despite the flag asking for
        # 1920x1080. That left YouTube a 922x519 player, for which 480p is the
        # *correct* ABR choice - so the rendition was capped by our viewport, not
        # by the shaped link. Setting the rect after launch makes the window the
        # size we actually asked for.
        install_media_hook(driver)
        install_vendor_merge_flag(driver, bool(job.get("merge_vendor_stats", False)))
        install_codec_block(driver, list(job.get("block_codecs") or []))
        # A per-app viewport cap bounds the top of an adaptive ladder: the
        # player sizes its rendition to the element, so a smaller window keeps
        # it off renditions this host cannot decode in real time, without
        # disabling ABR the way a forced-quality parameter would.
        want_w, want_h = _WANT_W, _WANT_H
        _ws = job.get("window_size")
        if _ws:
            try:
                _w, _h = str(_ws).lower().split("x")
                want_w, want_h = int(_w), int(_h)
                print(f"[{app}] viewport capped to {want_w}x{want_h}")
            except Exception:  # noqa: BLE001 - a bad cap must not fail a run
                print(f"[{app}] warning: bad window_size {_ws!r}, using default")
        window_geometry = ensure_window_size(driver, app, want_w=want_w, want_h=want_h)

    samples: list[dict] = []
    play_trigger_ts: float | None = None
    meet_ready_stats: dict[str, Any] | None = None
    meet_ready_timestamp: float | None = None
    force_quality_result: dict[str, Any] | None = None
    try:
        if is_webrtc:
            install_webrtc_hook(driver)
            driver.set_script_timeout(15)
        print(f"[{app}] navigating to {video_url}")
        driver.set_page_load_timeout(45)
        try:
            driver.get(video_url)
        except Exception as exc:  # streaming pages may never signal full load
            print(
                f"[{app}] navigation did not finish cleanly; "
                f"sampling the loaded page anyway: {type(exc).__name__}: {exc}"
            )

        if job.get("join_flow") == "zoom":
            print(f"[{app}] joining Zoom web client and waiting for remote video")
            zoom_ready = wait_for_zoom_join(
                driver,
                app,
                timeout_s=meet_join_timeout_seconds,
                guest_name=guest_name,
            )
            print(f"[{app}] zoom join result: {zoom_ready}", flush=True)
        if is_webrtc:
            print(f"[{app}] joining room and waiting for remote inbound WebRTC video")
            meet_ready_stats = wait_for_google_meet_inbound(
                driver,
                meet_join_timeout_seconds,
                guest_name,
            )
            meet_ready_timestamp = time.time()

        # Pin the rendition after the player exists but before the synchronized
        # start, so the whole sampled window runs at the forced quality.
        if force_max_quality and not is_webrtc and app == "youtube":
            force_quality_result = force_youtube_max_quality(
                driver, app, force_quality_level
            )

        print(f"[{app}] ready; waiting for all applications before sampling")
        sampling_barrier.wait(timeout=barrier_timeout_seconds)
        print(f"[{app}] all applications ready; starting synchronized sampling")
        # Clock for the remote-tile gate: the page gets REMOTE_TILE_GRACE_S from
        # here to lay out the peer's main view before a cell is refused.
        sampling_started_at = time.time()
        if not is_webrtc:
            try:
                # Wait for the element to actually hold data before asking it to
                # play, then verify the clock moves and retry if it does not.
                # Cheap for YouTube (already ready -> first poll returns), and
                # the difference between playing and a silent 0-frame run for
                # Vimeo under contention.
                ready_state_at_play = wait_for_media_ready(
                    driver, app, timeout_s=media_ready_timeout_seconds
                )
                play_result = start_playback(driver, app)
                play_result["ready_wait"] = ready_state_at_play
                play_trigger_ts = time.time()
                print(f"[{app}] synchronized play result: {play_result}")
            except Exception as exc:  # sampling records whether playback recovers
                print(
                    f"[{app}] synchronized play nudge failed: "
                    f"{type(exc).__name__}: {exc}"
                )
        # Written as the first line of the file so shared/qoe.py can measure
        # startup as (first advancing frame - play trigger) rather than from the
        # first sample, which understates it.
        with open(out_path, "a", buffering=1) as meta_f:
            meta_f.write(
                json.dumps(
                    {
                        "record": "meta",
                        "app": app,
                        "kind": kind,
                        "url": video_url,
                        "play_trigger_ts": play_trigger_ts,
                        "force_max_quality": force_max_quality,
                        "force_quality_level": (
                            force_quality_level if force_max_quality else None
                        ),
                        "force_quality_result": force_quality_result,
                        "window_geometry": window_geometry,
                    }
                )
                + "\n"
            )
        deadline = time.monotonic() + duration_seconds
        previous_meet_stats = meet_ready_stats
        previous_meet_timestamp = meet_ready_timestamp
        with open(out_path, "a", buffering=1) as f:
            while time.monotonic() < deadline:
                sample = {"timestamp": time.time(), "app": app, "kind": kind}
                try:
                    sample["url"] = driver.current_url
                except Exception as exc:  # noqa: BLE001
                    sample["url"] = None
                    sample["url_error"] = str(exc)
                try:
                    if is_webrtc:
                        stats = get_google_meet_stats(driver)
                        elapsed = (
                            sample["timestamp"] - previous_meet_timestamp
                            if previous_meet_timestamp is not None
                            else None
                        )
                        add_google_meet_interval_metrics(
                            stats,
                            previous_meet_stats,
                            elapsed,
                        )
                        sample["stats"] = stats
                        previous_meet_stats = stats
                        previous_meet_timestamp = sample["timestamp"]
                    else:
                        sample["stats"] = driver.execute_script(STATS_JS)
                        # Recorded per sample, not just per run: a buffer series
                        # is uninterpretable without knowing how often it was
                        # read, and the interval is what makes two apps'
                        # series comparable.
                        if isinstance(sample["stats"], dict):
                            sample["stats"][
                                "sampling_interval_s"
                            ] = sample_interval_seconds
                except Exception as exc:  # noqa: BLE001
                    sample["stats"] = None
                    sample["sample_error"] = str(exc)

                f.write(json.dumps(sample) + "\n")
                samples.append(sample)
                _assert_remote_tile(app, job, samples, sampling_started_at)
                time.sleep(sample_interval_seconds)
    finally:
        try:
            driver.quit()
        except Exception as exc:  # noqa: BLE001
            print(f"[{app}] warning: driver.quit() raised: {exc}")

    stats_samples = [s["stats"] for s in samples if s.get("stats")]
    if is_webrtc:
        times = [
            stats["frames_decoded"]
            for stats in stats_samples
            if stats.get("frames_decoded") is not None
        ]
    else:
        times = [
            stats["current_time_secs"]
            for stats in stats_samples
            if stats.get("current_time_secs") is not None
        ]
    resolutions = [
        stats["resolution"]
        for stats in stats_samples
        if stats.get("resolution") and stats["resolution"] != "0x0"
    ]
    advanced = bool(times) and max(times) > times[0]
    final_resolution = resolutions[-1] if resolutions else None
    progress_label = "frames_decoded" if is_webrtc else "current_time_secs"
    print(
        f"[{app}] summary: {len(samples)} samples written to {out_path} | "
        f"{progress_label} range=[{min(times) if times else None}, "
        f"{max(times) if times else None}] | advanced={advanced} | "
        f"final_resolution={final_resolution}"
    )


def main() -> int:
    jobs_raw = os.environ.get("JOBS")
    if jobs_raw:
        jobs = json.loads(jobs_raw)
    else:
        # Single-job mode, kept for standalone smoke testing.
        jobs = [
            {
                "app": os.environ.get("APP", "video"),
                "url": os.environ["VIDEO_URL"],
                "display_num": os.environ.get("DISPLAY_NUM", "99"),
                "out_path": os.environ.get("OUT_PATH", "/out/qoe.jsonl"),
                "duration_seconds": os.environ.get("DURATION_SECONDS", "15"),
                "sample_interval_seconds": os.environ.get(
                    "SAMPLE_INTERVAL_SECONDS", "1.0"
                ),
            }
        ]

    launch_lock = threading.Lock()
    sampling_barrier = threading.Barrier(len(jobs))
    errors: list[tuple[str, Exception]] = []

    def run_job_checked(job: dict[str, Any]) -> None:
        try:
            run_job(job, launch_lock, sampling_barrier)
        except Exception as exc:  # propagate thread failures through process exit
            errors.append((job["app"], exc))
            # Release any sibling already blocked on the barrier: without this
            # they wait out the full barrier timeout for a job that is gone.
            sampling_barrier.abort()
            print(
                f"[{job['app']}] collector failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    threads = [
        threading.Thread(target=run_job_checked, args=(job,), name=job["app"])
        for job in jobs
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
