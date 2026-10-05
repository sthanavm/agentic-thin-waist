# Zoom + Vimeo concurrent, 180 s per cell — 2026-10-05

Zoom and Vimeo sharing one shaped link. The cap applies to **total** traffic, not
per app. pfifo / cubic / 0 % loss, 180 s per cell, one trial each, collected
across six free-Zoom meeting windows with a human participant present
(camera off, mic muted).

Vimeo ran with **AV1 hidden from the page** (`app_blocked_codecs`
= `{"vimeo": ["av01"]}`, also in the run slug as `_nocvimeoav01`). These cells
must never be pooled with AV1-enabled Vimeo runs: bitrate-per-resolution
differs, so both the ladder and the offered load differ.

**This supersedes `zoom_vimeo_concurrent_180s_2026-10-04`,** where the Zoom
column measured the collector's own 207x117 self-view and was not a measurement
of the call.

## The measurement is now provable, not assumed

Every sample records the origin of every `<video>` on the page, classified by
`getUserMedia` track identity rather than by size, and each `record.json`
carries the reduction under `player_qoe.zoom.measured_element`:

| cell | dominant element | origin | local samples |
|---|---|---|---|
| all six | `320x180@1458x820` | `remote-stream` | 0-8 per cell (19 total) |

Box area 1,195,560 px² against a 607,224 px² floor derived from the runs that
demonstrably measured the peer (35 % of 1756x988). Across the six cells, 19 of 712 samples (2.7%) measured the collector's own
self-view at join; the rest measured the remote tile. See Known limitations.

## Verified per cell

- `record.json` vs the per-second JSONL vs the capture re-attributed from
  scratch: **pcap-to-record drift 0.000 MB on all twelve app-sides**.
- Shared cap on **combined** traffic, summed on a 1 s grid (summing per-app
  peaks is wrong — they land at different instants): **3.01/3, 3.01/3, 6.00/6,
  6.01/6, 10.00/10, 10.00/10**.
- The capture window covers the whole call in every cell.
- Zoom's `mean_bitrate_mbps` is `null` throughout — the web client exposes no
  bitrate and none was invented.
- Sample counts in `record.json` match the JSONL exactly.

## Results

| bw | lat | app | res | fps | dropped | rebuffers | status |
|---|---|---|---|---|---|---|---|
| 3 | 150 ms | Zoom | 180p | 2.94 | 41.47 % | 17 | REAL-CONDITION |
| 3 | 150 ms | Vimeo | 240p | 22.65 | 7.60 % | 0 | REAL-CONDITION |
| 3 | 50 ms | Zoom | 180p | 5.95 | 39.19 % | 17 | REAL-CONDITION |
| 3 | 50 ms | Vimeo | 240p | 22.61 | 7.79 % | 0 | REAL-CONDITION |
| 6 | 150 ms | Zoom | 180p | 8.51 | 51.82 % | 12 | REAL-CONDITION |
| 6 | 150 ms | Vimeo | 720p | 18.80 | 6.83 % | 0 | REAL-CONDITION |
| 6 | 50 ms | Zoom | 180p | 14.46 | 46.63 % | 2 | REAL-CONDITION |
| 6 | 50 ms | Vimeo | 240p | 21.85 | 16.86 % | 0 | REAL-CONDITION |
| 10 | 50 ms | Zoom | 180p | 15.66 | 35.46 % | 7 | REAL-CONDITION |
| 10 | 50 ms | Vimeo | 1080p | 13.41 | 5.54 % | 0 | REAL-CONDITION |
| 10 | 150 ms | Zoom | 180p | 17.27 | 40.60 % | 2 | REAL-CONDITION |
| 10 | 150 ms | Vimeo | 1080p | 11.31 | 8.49 % | 0 | REAL-CONDITION |

Zoom's frame rate rises with the cap across these six cells: **2.94 → 5.95 →
8.51 → 14.46 → 15.66 → 17.27**. With one trial per cell this is an ordering,
not a fitted relationship.

### Decoded size distribution of the measured tile

The `res` column above is the MODAL decoded height. The distribution behind
it, from the stored samples:

| cell | decoded sizes | label |
|---|---|---|
| 6 Mbps / 150 ms | `320x180` 98 % | 180p |
| 10 Mbps / 50 ms | `320x180` 87 %, `640x360` 12 % | 180p |
| 3 Mbps / 150 ms | `320x180` 92 %, `1280x720` 6 %, `640x360` 1 % | 180p |
| 3 Mbps / 50 ms | `320x180` 90 %, `640x360` 5 %, `1280x720` 4 % | 180p |
| 6 Mbps / 50 ms | `320x180` 72 %, `640x360` 25 %, `1280x720` 2 % | 180p |
| 10 Mbps / 150 ms | `320x180` 98 %, `1280x720` 1 % | 180p |

The rendered box was `1458x820` in every sample of every cell. An earlier
report quoted a `640x360` tile for the 10 Mbps / 50 ms cell: that came from
the final sample of the run, while the cell spent 87 % of its samples at
`320x180`. The label is correct; the earlier quote was not representative.

### Why every cell is REAL-CONDITION-FINDING rather than VERIFIED

Three distinct caveats, each recorded per cell rather than applied as a
blanket label:

**Vimeo decode ceiling** (6/150, 10/50, 10/150). Vimeo held its rendition but
advanced only 85–141 s of content in ~180 s of wall clock. Hiding AV1 stopped
the collapse to 320x240 seen with AV1 enabled, but the host ceiling did not
disappear — it changed shape from a resolution collapse into a frame-rate and
playback-time collapse. Measured for contrast: Vimeo alone at 10 Mbps plays
1440x1080 AV1 at 24.0 fps with 0.103 % dropped, so the rendition is decodable
when nothing competes. **Do not read these Vimeo cells as network results.**

**Self-view at join** (five of six cells, 1-8 samples each, 0.8-6.3 %). A few
samples land on the collector's own camera before the main view is laid out.
They are included in the metrics.

**Host CPU saturation** (6/50 at 117.8 % peak, 10/150 at 86.4 %). Per-process
CPU is in `cpu_per_process.jsonl` per cell, attributed from `/proc` — container
from cgroup, app from `DISPLAY`. The remaining four cells peaked at 43.8–45.5 %,
so host CPU does **not** explain their Zoom degradation.

A known gap: `DISPLAY` identifies only the parent Chrome processes, so renderer
and GPU children fall into `collector_chrome_unknown` (peak 146.6 %). The
per-app `collector_chrome_zoom` / `collector_chrome_vimeo` figures (~1.6 % mean)
therefore **undercount** each collector's real decode cost. `peer_chrome`
(65–77 % mean of one core, encoding 720p continuously) is reliable.

## The headline finding

Comparing identical regimes against the solo sweep
(`zoom_solo_180s_2026-10-05`):

| regime | Zoom fps, concurrent → solo | Zoom dropped, concurrent → solo |
|---|---|---|
| 3 Mbps / 150 ms | 2.94 → 16.58 | 41.5 % → 21.4 % |
| 3 Mbps / 50 ms | 5.95 → 20.62 | 39.2 % → 15.3 % |
| 6 Mbps / 150 ms | 8.51 → 20.39 | 51.8 % → 17.4 % |
| 6 Mbps / 50 ms | 14.46 → 21.21 | 46.6 % → 17.1 % |
| 10 Mbps / 50 ms | 15.66 → 20.46 | 35.5 % → 18.5 % |
| 10 Mbps / 150 ms | 17.27 → 22.54 | 40.6 % → 22.2 % |

Sharing the link with Vimeo costs Zoom 25–85 % of its frame rate and roughly
doubles its dropped frames, and its tile stays at `320x180` where solo reaches
`640x360`. In the three cells where host CPU peaked at only ~44 % (3/150, 3/50, 6/150)
Zoom still ran at 2.94/5.95/8.51 fps concurrent versus 16.58/20.62/20.39
solo. Host CPU saturation does not account for that gap, which is
consistent with contention for the shared link — but with n=1 per cell and
the two suites recorded in different meetings, this is an indication rather
than a measured decomposition. Separating link from host would need the
two varied independently.

## Reproducing

`run_sweep.py` drove the batches and `validate_sweep.py` produced the
classifications; both are in `experiments/pramana/`. Each `record.json` carries
the full config, `app_blocked_codecs`, `app_window_caps` and the measured-element
provenance. Captures are deliberately not committed; they stay on the
measurement host.

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
