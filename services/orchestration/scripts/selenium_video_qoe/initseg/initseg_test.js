// Validation against REAL init segments produced by ffmpeg, with ffprobe's
// reading as the independent ground truth.
const fs = require('fs');
const { parseInitSegment } = require('./initseg.js');
const dir = process.argv[2];
const cases = [
    // Profile 1 / level 0 / 8-bit, cross-checked against ffprobe's "av1, High"
    // (AV1 profile 1 is High) on the same file.
    ['av01_plain.mp4', 'iso-bmff', /^av01\.1\.00M\.08$/, 320, 240],
    ['avc1.mp4', 'iso-bmff', /^avc1\./, 320, 240],
    ['mp4a.mp4', 'iso-bmff', /^mp4a\.40\./, null, null],
    // WebM carries the AV1 config in CodecPrivate, so the full string is
    // recoverable there -- but NOT for VP9, which ships no CodecPrivate at all
    // (verified on this fixture), so only the family is knowable from the
    // container. The regex below is deliberately loose for that reason.
    ['av01.webm', 'webm', /^av01\.1\.00M\.08$/, 320, 240],
    ['vp09.webm', 'webm', /^vp09$/, 320, 240],
    ['opus.webm', 'webm', /^opus$/, null, null],
];
let pass = 0, fail = 0;
for (const [f, container, re, w, h] of cases) {
    const p = dir + '/' + f;
    if (!fs.existsSync(p)) { console.log('  SKIP (absent) ' + f); continue; }
    const r = parseInitSegment(fs.readFileSync(p));
    if (!r) { console.log('  FAIL ' + f + ': parser returned null'); fail++; continue; }
    const t = r.tracks.find(x => x.codec) || r.tracks[0];
    const okC = r.container === container;
    const okCodec = t && re.test(String(t.codec));
    const okDim = (w === null) || (t && t.width === w && t.height === h);
    const ok = okC && okCodec && okDim;
    console.log('  ' + (ok ? 'PASS' : 'FAIL') + ' ' + f.padEnd(11) +
        ' container=' + r.container.padEnd(9) +
        ' codec=' + String(t && t.codec).padEnd(18) +
        ' dims=' + (t && t.width ? t.width + 'x' + t.height : '-') +
        (ok ? '' : '   <-- expected ' + container + ' / ' + re + ' / ' + w + 'x' + h));
    ok ? pass++ : fail++;
}
// An EMPTY av1C box (size 8, no config bytes) must yield NO codec rather than
// a fabricated one. ffmpeg writes exactly this when muxing fragmented MP4 with
// +dash, which is how the case was found.
{
    const r = parseInitSegment(fs.readFileSync(dir + '/av01.mp4'));
    const t = (r && r.tracks || []).find(x => x.sample_entry === 'av01');
    const ok = !!t && t.codec === null;
    console.log('  ' + (ok ? 'PASS' : 'FAIL') +
        ' empty av1C yields codec=null, not a guess (got ' +
        JSON.stringify(t && t.codec) + ')');
    ok ? pass++ : fail++;
}

// A truncated buffer must return null, not a wrong answer.
const trunc = fs.readFileSync(dir + '/av01.mp4').subarray(0, 40);
const tr = parseInitSegment(trunc);
const truncOk = (tr === null) || !(tr.tracks || []).some(t => t.codec);
console.log('  ' + (truncOk ? 'PASS' : 'FAIL') + ' truncated buffer yields no codec (got ' +
    JSON.stringify(tr) + ')');
truncOk ? pass++ : fail++;
// Garbage must not throw.
try {
    parseInitSegment(Buffer.from([1,2,3,4,5,6,7,8,9,10,11,12]));
    console.log('  PASS garbage input does not throw');
    pass++;
} catch (e) { console.log('  FAIL garbage input threw: ' + e.message); fail++; }
console.log('\n  ' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
