# Zoom + Vimeo concurrent, 180 s per cell — 2026-10-04

Zoom and Vimeo sharing one shaped link. The cap applies to **total** traffic, not
per app. pfifo / cubic / 0 % loss, 180 s per cell, one trial each.

Vimeo ran with **AV1 hidden from the page** (`app_block_codecs={"vimeo":["av01"]}`,
recorded in each `record.json` as `app_blocked_codecs`, and in the run slug as
`_nocvimeoav01`). These cells must never be pooled with AV1-enabled Vimeo runs:
bitrate-per-resolution differs, so both the ladder and the offered load differ.

## Read this before using the numbers

**The Vimeo column is valid. The Zoom column is not a measurement of the call.**

Zoom's web client renders the same streams into several `<video>` elements at
once. Every sample's census is stored (`video_element_count`,
`video_decoded_count`, `video_elements` as `stream@box`), and it shows the
collector measured a **207x117 thumbnail** in all four cells rather than the
main view:

| cell | measured | main view present | Zoom verdict |
|---|---|---|---|
| 6 Mbps / 150 ms | `320x180@207x117` | yes, `320x180@1458x820`, ranked below the self-view | not measured |
| 10 Mbps / 50 ms | `1280x720@207x117` | yes, ranked below the self-view | not measured |
| 3 Mbps / 150 ms | `320x180@207x117` | yes, ranked below the self-view | not measured |
| 3 Mbps / 50 ms  | `1280x720@207x117` | **no composited main view existed** | not measured |

Two distinct causes:

1. Cells 1–3 ran with a `pickVideo()` that ranked candidates by *intrinsic*
   resolution. The collector joins with its camera on (a fake device at
   1280x720), so its own self-view thumbnail outranked the remote speaker's
   downscaled main view — the local preview is exactly what a conferencing
   measurement must never report. Fixed mid-sweep to rank by **rendered box**.
2. Cell 4 ran with that fix, and the fix behaved correctly: it picked the
   largest rendered element. But by then no composited main view existed at all,
   so the largest rendered element was still the 207x117 self-view. Its
   `dropped_frame_pct` of exactly 0.0 % across 180 s of a contended 3 Mbps link
   is the signature of a local source, not a delivered stream.

The Zoom runs that *did* measure the remote peer correctly (`1280x720@1756x988`)
were taken when no human participant was in the meeting. That is the variable
that changed here, and it is the configuration to reproduce. An earlier guess in
this session that extra participant cameras were responsible was wrong: the four
decoded elements are duplicate renderings of the same streams, not four cameras.

## What was verified

Per cell, all three layers agree and the run-level invariants hold:

- `record.json` vs the per-second JSONL vs the capture, re-attributed from
  scratch: **pcap-to-record drift 0.000 MB on all eight app-sides**.
- Shared cap on **combined** traffic, summed on a 1 s grid (summing per-app
  peaks is wrong — they land at different instants): 6.00/6, 10.00/10, 3.01/3,
  3.02/3 Mbps.
- The capture window covers the whole call in every cell.
- Zoom's `mean_bitrate_mbps` is `null` throughout — the Zoom web client exposes
  no bitrate, and none was invented.
- Vimeo: exactly 1 element, 1 decoded, codec `avc1.640020` in every sample.

## Results

| bw | lat | app | res | fps | dropped | rebuffers | status |
|---|---|---|---|---|---|---|---|
| 6 | 150 ms | Vimeo | 720p | 21.11 | 5.704 % | 0 | VERIFIED |
| 6 | 150 ms | Zoom | — | — | — | — | NOT MEASURED |
| 10 | 50 ms | Vimeo | 1080p | 12.76 | 4.343 % | 0 | VERIFIED, see note |
| 10 | 50 ms | Zoom | — | — | — | — | NOT MEASURED |
| 3 | 150 ms | Vimeo | 540p | 23.17 | 5.693 % | 0 | VERIFIED |
| 3 | 150 ms | Zoom | — | — | — | — | NOT MEASURED |
| 3 | 50 ms | Vimeo | 540p | 23.80 | 0.969 % | 0 | VERIFIED |
| 3 | 50 ms | Zoom | — | — | — | — | NOT MEASURED |

Not completed: **6 Mbps / 50 ms** and **10 Mbps / 150 ms**. The free-account
meeting reached its 40-minute limit; the peer's own interface counters flipped
it to `stalled` and the cell in flight was discarded rather than kept as a run
against a dead meeting.

### Note on the 10 Mbps Vimeo cell

Vimeo held `1440x1080` for all 142 samples — hiding AV1 did prevent the collapse
to 320x240 seen with AV1 enabled. But it advanced only **95.54 s of content in
~142 s** at **12.76 fps**: the host decode ceiling did not disappear, it changed
shape from a resolution collapse into a frame-rate and playback-time collapse.
This is a property of a 2-vCPU host decoding 1440p H.264 while Zoom decodes
alongside it, not of the 10 Mbps link. **Do not read this cell as a network
result.** Measured for contrast: Vimeo alone at 10 Mbps plays 1440x1080 AV1 at
24.0 fps with 0.103 % dropped, so the rendition is decodable when nothing
competes with it.

## Vimeo ladder behaviour (the usable finding)

With AV1 hidden the ladder stays free and tracks the cap, which is what the
experiment is for:

| cell | ladder |
|---|---|
| 3 Mbps / 50 ms | `720x540` ×109, `320x240` ×46, `480x360` ×18 |
| 3 Mbps / 150 ms | `720x540` ×63, `480x360` ×48, `320x240` ×47 |
| 6 Mbps / 150 ms | `960x720` ×77, `480x360` ×72 |
| 10 Mbps / 50 ms | `1440x1080` ×142 |

Zero rebuffers in all four cells.

## Reproducing

`run_zv2.py` in this folder is the exact harness used. Each cell's `record.json`
carries the full config, `app_blocked_codecs`, and `app_window_caps`. Captures
are deliberately not committed; they stay on the measurement host.
