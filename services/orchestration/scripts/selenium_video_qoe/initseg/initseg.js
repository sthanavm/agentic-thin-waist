// Codec of the rendition actually being decoded, read from the init segment
// the page appended to its SourceBuffer.
//
// Why this exists: the MIME string passed to addSourceBuffer() is the codec the
// page DECLARED it might send, not the one it is sending. Measured on YouTube,
// one declared `av01.0.04M.08` stayed constant while the rendition went
// 394 -> 395 -> 396 -> 397, and `changeType` was never called, so the switch
// log was empty. The init segment is the only generic place the real answer
// appears, because the decoder itself is configured from it.
//
// Deliberately NOT itag-based: itags are a YouTube URL convention and say
// nothing about any other site. This reads the container, which both sites
// must emit correctly for their own decoder to work.
//
// Handles the two containers that occur in practice: ISO-BMFF (.mp4, used by
// YouTube for AV1 and by Vimeo for everything) and WebM/EBML (.webm, used by
// YouTube for VP9 and Opus).

function u8(buf) {
    return buf instanceof Uint8Array ? buf : new Uint8Array(
        buf.buffer ? buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) : buf);
}
function be(b, o, n) { let v = 0; for (let i = 0; i < n; i++) v = v * 256 + b[o + i]; return v; }
function fourcc(b, o) { return String.fromCharCode(b[o], b[o + 1], b[o + 2], b[o + 3]); }
function hex2(n) { return n.toString(16).padStart(2, '0'); }

// ── ISO-BMFF ────────────────────────────────────────────────────────────────
// Walk boxes, descending only into containers, and collect every sample entry
// in stsd. Sizes are validated so a truncated or non-MP4 buffer returns null
// instead of reading past the end.
const MP4_CONTAINERS = new Set(['moov', 'trak', 'mdia', 'minf', 'stbl', 'mvex', 'edts']);

function mp4Boxes(b, start, end, cb) {
    let o = start;
    while (o + 8 <= end) {
        let size = be(b, o, 4);
        const type = fourcc(b, o + 4);
        let hdr = 8;
        if (size === 1) {               // 64-bit extended size
            if (o + 16 > end) return;
            size = be(b, o + 8, 8);
            hdr = 16;
        } else if (size === 0) {
            size = end - o;             // box runs to end of buffer
        }
        if (size < hdr || o + size > end) return;   // malformed: stop, don't guess
        cb(type, o + hdr, o + size);
        o += size;
    }
}

function av1cCodec(b, s, e) {
    // AV1 codec config record, AV1-ISOBMFF spec: marker/version, then
    // seq_profile(3) seq_level_idx_0(5), then seq_tier(1) high_bitdepth(1)
    // twelve_bit(1) monochrome(1) ...
    if (e - s < 4) return null;
    const profile = (b[s + 1] >> 5) & 0x07;
    const level = b[s + 1] & 0x1f;
    const tier = (b[s + 2] >> 7) & 0x01;
    const high = (b[s + 2] >> 6) & 0x01;
    const twelve = (b[s + 2] >> 5) & 0x01;
    const depth = twelve ? 12 : (high ? 10 : 8);
    return 'av01.' + profile + '.' + String(level).padStart(2, '0') +
        (tier ? 'H' : 'M') + '.' + String(depth).padStart(2, '0');
}

function avccCodec(b, s, e) {
    // AVCDecoderConfigurationRecord: version, profile_idc, profile_compat,
    // level_idc
    if (e - s < 4) return null;
    return 'avc1.' + hex2(b[s + 1]) + hex2(b[s + 2]) + hex2(b[s + 3]);
}

function hvccCodec(b, s, e) {
    if (e - s < 13) return null;
    const gpcf = b[s + 1];
    const profileSpace = (gpcf >> 6) & 0x03;
    const tierFlag = (gpcf >> 5) & 0x01;
    const profileIdc = gpcf & 0x1f;
    const compat = be(b, s + 2, 4);
    const levelIdc = b[s + 12];
    const sp = ['', 'A', 'B', 'C'][profileSpace];
    return 'hvc1.' + sp + profileIdc + '.' +
        compat.toString(16).toUpperCase() + '.' + (tierFlag ? 'H' : 'L') + levelIdc;
}

function vpccCodec(b, s, e, fcc) {
    // VPCodecConfigurationRecord: profile, level, bitDepth<<4|chroma<<1|range
    if (e - s < 6) return null;
    const profile = b[s + 4];
    const level = b[s + 5];
    const depth = (e - s >= 7) ? (b[s + 6] >> 4) : 8;
    return fcc + '.' + String(profile).padStart(2, '0') + '.' +
        String(level).padStart(2, '0') + '.' + String(depth).padStart(2, '0');
}

function esdsCodec(b, s, e) {
    // Find the DecoderConfigDescriptor (tag 0x04) and read objectTypeIndication,
    // then the AudioSpecificConfig's first 5 bits for the AAC object type.
    for (let o = s; o + 2 < e; o++) {
        if (b[o] !== 0x04) continue;
        let p = o + 1;
        while (p < e && (b[p] & 0x80)) p++;      // skip expandable length
        p++;
        if (p >= e) break;
        const oti = b[p];
        if (oti !== 0x40) return 'mp4a.' + hex2(oti);
        // walk to DecoderSpecificInfo (tag 0x05)
        for (let q = p; q + 2 < e; q++) {
            if (b[q] !== 0x05) continue;
            let r = q + 1;
            while (r < e && (b[r] & 0x80)) r++;
            r++;
            if (r >= e) break;
            const audioObjectType = (b[r] >> 3) & 0x1f;
            return 'mp4a.40.' + audioObjectType;
        }
        return 'mp4a.40';
    }
    return null;
}

function parseMp4(b) {
    const out = [];
    const visit = (s, e) => {
        mp4Boxes(b, s, e, (type, cs, ce) => {
            if (MP4_CONTAINERS.has(type)) { visit(cs, ce); return; }
            if (type !== 'stsd') return;
            // stsd: version/flags(4) entry_count(4) then sample entries
            if (ce - cs < 8) return;
            mp4Boxes(b, cs + 8, ce, (fcc, es, ee) => {
                const entry = { sample_entry: fcc, codec: null, width: null, height: null };
                // VisualSampleEntry puts width/height at +24/+26 after the
                // 8-byte box header we already skipped (6 reserved + 2 index +
                // 16 pre_defined/reserved).
                if (ee - es >= 78) {
                    const w = be(b, es + 24, 2), h = be(b, es + 26, 2);
                    if (w && h) { entry.width = w; entry.height = h; }
                }
                // Child boxes do NOT start at the sample entry's payload. A
                // VisualSampleEntry carries 78 bytes of fixed fields after the
                // box header: 8 for the SampleEntry base (6 reserved + 2
                // data_reference_index), then 16 pre_defined/reserved, 4 of
                // width+height, 8 of resolutions, 4 reserved, 2 frame_count,
                // 32 compressorname, 2 depth, 2 pre_defined. An
                // AudioSampleEntry carries 28. Verified against ffmpeg-written
                // files: av1C and avcC both begin at payload offset 78.
                // Scanning from the wrong offset finds no config box at all
                // and silently reports no codec.
                const scan = (off) => {
                    let got = null;
                    if (es + off >= ee) return null;
                    mp4Boxes(b, es + off, ee, (cfg, xs, xe) => {
                        if (cfg === 'av1C') got = av1cCodec(b, xs, xe);
                        else if (cfg === 'avcC') got = avccCodec(b, xs, xe);
                        else if (cfg === 'hvcC') got = hvccCodec(b, xs, xe);
                        else if (cfg === 'vpcC') got = vpccCodec(b, xs, xe,
                            fcc === 'vp08' ? 'vp08' : 'vp09');
                        else if (cfg === 'esds') got = esdsCodec(b, xs, xe);
                        else if (cfg === 'dOps') got = 'opus';
                    });
                    return got;
                };
                entry.codec = scan(78) || scan(28) || scan(0);
                if (!entry.codec) {
                    // Containers that need no config box to be identified.
                    if (fcc === 'Opus') entry.codec = 'opus';
                    else if (fcc === 'fLaC') entry.codec = 'flac';
                    else if (fcc === 'ac-3') entry.codec = 'ac-3';
                }
                out.push(entry);
            });
        });
    };
    visit(0, b.length);
    return out;
}

// ── WebM / EBML ─────────────────────────────────────────────────────────────
// Only the path Tracks > TrackEntry > {CodecID, CodecPrivate, PixelWidth,
// PixelHeight} is needed, so this descends into exactly those master elements.
const EBML_DESCEND = new Set([0x18538067, 0x1654AE6B, 0xAE, 0xE0, 0xE1]);  // Segment, Tracks, TrackEntry, Video, Audio

function ebmlNum(b, o, end, isId) {
    if (o >= end) return null;
    const first = b[o];
    let len = 1, mask = 0x80;
    while (len <= 8 && !(first & mask)) { mask >>= 1; len++; }
    if (len > 8 || o + len > end) return null;
    let v = isId ? first : (first & (mask - 1));
    for (let i = 1; i < len; i++) v = v * 256 + b[o + i];
    return { value: v, len: len };
}

function parseWebm(b) {
    const tracks = [];
    let cur = null;
    const walk = (s, e, depth) => {
        if (depth > 6) return;
        let o = s;
        while (o < e) {
            const id = ebmlNum(b, o, e, true);
            if (!id) return;
            const sz = ebmlNum(b, o + id.len, e, false);
            if (!sz) return;
            let cs = o + id.len + sz.len;
            let ce = cs + sz.value;
            const unknownSize = sz.value >= Math.pow(2, 7 * sz.len) - 1;
            if (unknownSize || ce > e) ce = e;     // streaming: size may be unknown
            if (id.value === 0xAE) {               // TrackEntry
                cur = { codec_id: null, codec: null, width: null, height: null, private_len: 0 };
                tracks.push(cur);
                walk(cs, ce, depth + 1);
            } else if (EBML_DESCEND.has(id.value)) {
                walk(cs, ce, depth + 1);
            } else if (cur) {
                if (id.value === 0x86) {           // CodecID
                    cur.codec_id = String.fromCharCode.apply(null, b.subarray(cs, ce)).replace(/\0+$/, '');
                } else if (id.value === 0x63A2) {  // CodecPrivate
                    cur.private_len = ce - cs;
                    cur._priv = [cs, ce];
                } else if (id.value === 0xB0) {    // PixelWidth
                    cur.width = be(b, cs, ce - cs);
                } else if (id.value === 0xBA) {    // PixelHeight
                    cur.height = be(b, cs, ce - cs);
                }
            }
            o = ce;
            if (ce <= cs) return;                  // no forward progress: bail
        }
    };
    walk(0, b.length, 0);
    for (const t of tracks) {
        const id = t.codec_id || '';
        if (id === 'V_VP9') t.codec = vp9FromPrivate(b, t) || 'vp09';
        else if (id === 'V_VP8') t.codec = 'vp08';
        else if (id === 'V_AV1') t.codec = av1FromPrivate(b, t) || 'av01';
        else if (id.startsWith('V_MPEG4/ISO/AVC')) t.codec = 'avc1';
        else if (id === 'A_OPUS') t.codec = 'opus';
        else if (id === 'A_VORBIS') t.codec = 'vorbis';
        else if (id.startsWith('A_AAC')) t.codec = 'mp4a.40.2';
        else t.codec = id || null;
        delete t._priv;
    }
    return tracks;
}

function av1FromPrivate(b, t) {
    if (!t._priv) return null;
    return av1cCodec(b, t._priv[0], t._priv[1]);
}
function vp9FromPrivate(b, t) {
    // WebM carries VP9 config as CodecPrivate ID/len/value triplets, not vpcC.
    if (!t._priv) return null;
    const [s, e] = t._priv;
    let profile = null, level = null, depth = null;
    for (let o = s; o + 2 <= e;) {
        const id = b[o], len = b[o + 1];
        if (o + 2 + len > e) break;
        const v = be(b, o + 2, len);
        if (id === 1) profile = v;
        else if (id === 2) level = v;
        else if (id === 3) depth = v;
        o += 2 + len;
    }
    if (profile === null) return null;
    return 'vp09.' + String(profile).padStart(2, '0') + '.' +
        String(level === null ? 0 : level).padStart(2, '0') + '.' +
        String(depth === null ? 8 : depth).padStart(2, '0');
}

// ── entry point ─────────────────────────────────────────────────────────────
function parseInitSegment(buf) {
    const b = u8(buf);
    if (b.length < 8) return null;
    // EBML magic
    if (b[0] === 0x1A && b[1] === 0x45 && b[2] === 0xDF && b[3] === 0xA3) {
        const tr = parseWebm(b);
        return tr.length ? { container: 'webm', tracks: tr } : null;
    }
    const mp4 = parseMp4(b);
    return mp4.length ? { container: 'iso-bmff', tracks: mp4 } : null;
}

if (typeof module !== 'undefined') module.exports = { parseInitSegment };
