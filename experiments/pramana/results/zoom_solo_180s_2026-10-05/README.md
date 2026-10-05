# Zoom solo sweep, 180 s per cell — 2026-10-05

Zoom alone on a shaped link, one bot peer publishing 720p and a human
participant present (camera off, mic muted). pfifo / cubic / 0 % loss, 180 s per
cell, one trial each, collected across free-Zoom meeting windows.

**This supersedes `zoom_solo_180s_2026-09-28`.** In that sweep the measured
`<video>` varied across runs — `(0,0)`, `(207,117)`, `(254,143)`, `(700,394)`,
`(1756,988)` — so its dropped-frame column was not comparable from cell to cell,
and one run showed the full hidden-element artefact at 99.971 % dropped. Those
values were a property of element selection, not of the network.

## The measurement is now provable, not assumed

Every sample records the origin of every `<video>` on the page, classified by
`getUserMedia` track identity rather than by size:

| cell | dominant element | origin | local samples |
|---|---|---|---|
| all six | `640x360@1458x820` | `remote-stream` | 0-4 per cell (5 total) |

Across the six cells, 5 of 932 samples (0.5%) measured the collector's own
self-view at join; the rest measured the remote tile. The failure mode that
invalidated the previous sweep cannot recur silently, because `record.json`
now states which element was measured and how it was classified.

## Verified per cell

- `record.json` vs the per-second JSONL vs the capture re-attributed from
  scratch: **pcap-to-record drift 0.000 MB on all six cells**.
- Cap held: **3.00/3, 3.01/3, 6.00/6, 6.00/6, 10.00/10, 10.00/10**.
- The capture window covers the whole call in every cell.
- `mean_bitrate_mbps` is `null` throughout — the Zoom web client exposes no
  bitrate and none was invented.
- Sample counts in `record.json` match the JSONL exactly.

## Results

| bw | lat | res | fps | dropped | rebuffers | status |
|---|---|---|---|---|---|---|
| 3 | 150 ms | 360p | 16.58 | 21.45 % | 6 | REAL-CONDITION |
| 3 | 50 ms | 360p | 20.62 | 15.29 % | 3 | VERIFIED |
| 6 | 150 ms | 360p | 20.39 | 17.40 % | 3 | REAL-CONDITION |
| 6 | 50 ms | 360p | 21.21 | 17.10 % | 1 | VERIFIED |
| 10 | 50 ms | 360p | 20.46 | 18.52 % | 0 | REAL-CONDITION |
| 10 | 150 ms | 360p | 22.54 | 22.21 % | 0 | REAL-CONDITION |

Four cells are REAL-CONDITION-FINDING and two VERIFIED. The caveats are host
CPU (10/50 at 85.7 %, 10/150 at 117.8 % of total capacity) and a few join-time
self-view samples (6/150: 1 of 158; 3/150: 4 of 154). No cell failed a check.

## Does the dropped-frame column behave consistently now?

**Yes.** It sits in a narrow **15.29–22.21 %** band across the whole grid — a
7-point spread — against the previous sweep's 0.0 % to 99.971 %. Every cell
measured the same element type at the same size (`640x360@1458x820`), which is
what makes the column comparable at all.

The residual ~18 % does not fall as the cap rises; if anything it rises
slightly (15.29 % at 3 Mbps/50 ms to 22.21 % at 10 Mbps/150 ms) while frame
rate also rises. That pattern is more consistent with decode cost on a
loaded host than with loss or starvation on the link, but with n=1 per cell
it is not established.

### Decoded size distribution of the measured tile

| cell | decoded sizes | label |
|---|---|---|
| 3 Mbps / 150 ms | `640x360` 50 %, `320x180` 46 %, `1280x720` 2 % | 360p |
| 3 Mbps / 50 ms | `640x360` 80 %, `320x180` 20 % | 360p |
| 6 Mbps / 150 ms | `640x360` 71 %, `320x180` 27 % | 360p |
| 6 Mbps / 50 ms | `640x360` 80 %, `320x180` 19 % | 360p |
| 10 Mbps / 50 ms | `640x360` 81 %, `320x180` 18 % | 360p |
| 10 Mbps / 150 ms | `640x360` 84 %, `320x180` 15 % | 360p |

Rendered box `1458x820` throughout. `3M_150ms` is a near-tie, so its 360p
label is the weakest of the six.

## What the grid shows

Zoom solo reaches ~20–22 fps at `640x360` across almost the entire grid. The one
clearly degraded cell is **3 Mbps and 150 ms together** (16.58 fps, 6 rebuffers,
and the only cell whose tile spent most of its samples at `320x180`). At 3 Mbps
with 50 ms latency Zoom achieves 20.62 fps — better than 10 Mbps/50 ms — so the
degradation is the *combination* of a low cap and high latency, not the cap
alone. An earlier reading of this data as "bandwidth-limited below 6 Mbps"
confounded the two variables and is wrong.

Rebuffers are the cleanest monotonic signal: 6, 3, 3, 1, 0, 0 as conditions
improve.

## Reproducing

`run_sweep.py` drove the batches and `validate_sweep.py` produced the
classifications; both are in `experiments/pramana/`. Per-process CPU for each
cell is in `cpu_per_process.jsonl`. Captures are deliberately not committed.

## Known limitations

- **One trial per cell.** Every number here is n=1. No variance estimate exists,
  so differences between adjacent regimes should not be treated as significant
  on their own.
- **Suites A and B ran in different meetings.** The 12 cells span six separate
  free-Zoom meeting windows, so a Zoom-side difference between A and B carries
  between-meeting variation (server assignment, routing, time of day) on top of
  the condition being varied.
- **Every Vimeo cell is AV1-blocked.** Vimeo ran with `av01` hidden from the
  page, so it served H.264. These cells are not comparable with AV1-enabled
  Vimeo runs: bitrate-per-resolution differs, so both the ladder and the offered
  load differ.
- **Three Vimeo cells are VM-limited by a decode ceiling:** `6M_150ms`,
  `10M_50ms`, `10M_150ms`. Vimeo held its rendition but advanced only 141 s,
  100 s and 85 s of content respectively in ~180 s of wall clock. That is a
  2-vCPU host decoding H.264 at 1080p+, not the shaped link.
- **The bot peer's Chrome consumed 65-77% of one core (peak 87.5%)** encoding
  720p continuously, on a 2-vCPU host. It is part of every cell's load.
- **Zoom's bitrate is `null` in every cell.** The Zoom web client exposes no
  bitrate, so no throughput-per-resolution figure exists on the player side;
  only the capture gives bytes.
- **The collector stays bidirectional.** It joins with its camera on and
  publishes a fake device, so it both encodes and decodes. Turning the camera
  off would reduce host load but changes the condition, so it was not done.
- **Per-app CPU in these cells undercounts the collectors.** The sampler
  attributed only the parent Chrome process, leaving renderer and GPU children
  - which do the decoding - in an unattributed bucket (peak 146.6% of a core).
  `peer_chrome` is reliable; `collector_chrome_zoom` / `collector_chrome_vimeo`
  are not. The sampler has since been fixed and verified on a live Vimeo run
  (113.1% attributed to the collector, no unknown bucket), but these cells
  cannot be re-attributed: only the bucketed aggregates were stored, not raw
  per-pid ticks.
- **The Zoom resolution label is the modal decoded height,** not a mean or a
  maximum. Mode share is 73-98% in 11 of 12 cells, but `zoom_3M_150ms` is a
  near-tie (`640x360` 50% / `320x180` 46%, mode share 51%) where one number
  hides half the call. Per-cell distributions are given above; `resolutions_observed`
  and `res_timeline` in each `record.json` carry the full series.
- **24 of 1638 Zoom samples (1.5%) measured the collector's own self-view,**
  concentrated at join before the main view laid out, in 7 of the 12 cells (max
  8 samples, `zoom+vimeo_3M_150ms`). They are included in the metrics. An
  earlier version of this README claimed zero such samples; that was a
  false negative from matching local tracks by id alone, since Zoom re-wraps the
  local camera and the clone carries a new id. Matching also on the track label
  (`fake_device_0`) exposed them.
