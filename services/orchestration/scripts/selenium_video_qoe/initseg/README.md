# Per-rendition codec from the init segment

## Why

The MIME string given to `addSourceBuffer()` is what the page said it *might*
send, not what it is sending. Measured on YouTube: one declared
`av01.0.04M.08` stayed constant while the rendition went 394 → 395 → 396 → 397,
and `changeType()` was never called, so `mse_mime_switch_log` was empty. The
declared MIME therefore cannot answer "which codec is decoding right now".

The init segment can, because the decoder itself is configured from it, and
every site must emit it correctly for its own playback to work. That makes this
generic: no itags, no per-site URL parsing.

## What it reads

| container | path | result |
|---|---|---|
| ISO-BMFF | `moov/trak/mdia/minf/stbl/stsd` sample entry → `av1C`, `avcC`, `hvcC`, `vpcC`, `esds` | full codec string |
| ISO-BMFF | sample entry fourcc `Opus`, `fLaC`, `ac-3` | family (no config box needed) |
| WebM/EBML | `Tracks/TrackEntry` → `CodecID` + `CodecPrivate` | full string for `V_AV1`; family for others |

Width and height come from the `VisualSampleEntry` fixed fields (ISO-BMFF) or
`PixelWidth`/`PixelHeight` (WebM).

## Validation

`initseg_test.js` runs against init segments produced by ffmpeg
(`make_fixtures.sh`), with ffprobe's reading of the same file as the
independent ground truth. 9/9 pass:

| fixture | parser | ffprobe | agree |
|---|---|---|---|
| `av01_plain.mp4` | `av01.1.00M.08` | av1, **High** (= profile 1) | yes |
| `av01.webm` | `av01.1.00M.08` | av1, High | yes — and identical to the MP4 path |
| `avc1.mp4` | `avc1.f4000d` | h264, **High 4:4:4 Predictive**, level **13** (0xf4=244, 0x0d=13) | yes |
| `mp4a.mp4` | `mp4a.40.2` | aac, **LC** (AOT 2) | yes |
| `vp09.webm` | `vp09` | vp9, Profile 1 | **family only** — see below |
| `opus.webm` | `opus` | opus | yes |

Plus three negative tests: an **empty** `av1C` (size 8, which ffmpeg writes
under `+dash`) must yield `null` rather than a fabricated codec; a truncated
buffer must yield no codec; garbage must not throw.

## Known limitation, measured not assumed

**VP9 in WebM gives the family only, never the profile.** Verified on the
fixture: the file contains `CodecID = V_VP9` and **no `CodecPrivate` element at
all** (searched for EBML id `0x63A2`; absent). VP9's profile lives in the
bitstream's uncompressed frame header, not in the container, so it is not
recoverable from an init segment by any parser. AV1 in WebM *does* carry its
config in `CodecPrivate`, which is why that case is complete.

This matters for YouTube specifically, whose VP9 renditions arrive as
`video/webm`: those will report `vp09` with no profile digits.

## Not yet validated

The parser has **not** been run against a real YouTube or Vimeo init segment —
that needs the measurement VM, which was unreachable when this landed. The
`hvcC` and ISO-BMFF `vpcC` paths have no fixture at all and are therefore
**unvalidated code**, not measured behaviour.
