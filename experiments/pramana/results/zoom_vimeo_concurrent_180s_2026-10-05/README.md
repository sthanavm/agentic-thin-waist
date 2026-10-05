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
| all six | `320x180@1458x820` | `remote-stream` | **0** |

Box area 1,195,560 px² against a 607,224 px² floor derived from the runs that
demonstrably measured the peer (35 % of 1756x988). Zero local samples in all six
cells: the self-view is never what was measured.

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
| 3 | 150 ms | Zoom | 180p | 2.94 | 41.47 % | 17 | VERIFIED |
| 3 | 150 ms | Vimeo | 240p | 22.65 | 7.60 % | 0 | VERIFIED |
| 3 | 50 ms | Zoom | 180p | 5.95 | 39.19 % | 17 | VERIFIED |
| 3 | 50 ms | Vimeo | 240p | 22.61 | 7.79 % | 0 | VERIFIED |
| 6 | 150 ms | Zoom | 180p | 8.51 | 51.82 % | 12 | REAL-CONDITION-FINDING |
| 6 | 150 ms | Vimeo | 720p | 18.80 | 6.83 % | 0 | REAL-CONDITION-FINDING |
| 6 | 50 ms | Zoom | 180p | 14.46 | 46.63 % | 2 | REAL-CONDITION-FINDING |
| 6 | 50 ms | Vimeo | 240p | 21.85 | 16.86 % | 0 | REAL-CONDITION-FINDING |
| 10 | 50 ms | Zoom | 180p | 15.66 | 35.46 % | 7 | REAL-CONDITION-FINDING |
| 10 | 50 ms | Vimeo | 1080p | 13.41 | 5.54 % | 0 | REAL-CONDITION-FINDING |
| 10 | 150 ms | Zoom | 180p | 17.27 | 40.60 % | 2 | REAL-CONDITION-FINDING |
| 10 | 150 ms | Vimeo | 1080p | 11.31 | 8.49 % | 0 | REAL-CONDITION-FINDING |

Zoom's frame rate rises monotonically with the cap: **2.94 → 5.95 → 8.51 →
14.46 → 15.66 → 17.27**.

### Why four cells are REAL-CONDITION-FINDING rather than VERIFIED

Two distinct caveats, both recorded per cell rather than applied as a blanket
label:

**Vimeo decode ceiling** (6/150, 10/50, 10/150). Vimeo held its rendition but
advanced only 85–141 s of content in ~180 s of wall clock. Hiding AV1 stopped
the collapse to 320x240 seen with AV1 enabled, but the host ceiling did not
disappear — it changed shape from a resolution collapse into a frame-rate and
playback-time collapse. Measured for contrast: Vimeo alone at 10 Mbps plays
1440x1080 AV1 at 24.0 fps with 0.103 % dropped, so the rendition is decodable
when nothing competes. **Do not read these Vimeo cells as network results.**

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
`640x360`. Crucially, in the three cells where host CPU peaked at only ~44 %
(3/150, 3/50, 6/150) Zoom still ran at 2.94/5.95/8.51 fps concurrent versus
16.58/20.62/20.39 solo — **that gap is link contention, not host CPU.**

## Reproducing

`run_sweep.py` drove the batches and `validate_sweep.py` produced the
classifications; both are in `experiments/pramana/`. Each `record.json` carries
the full config, `app_blocked_codecs`, `app_window_caps` and the measured-element
provenance. Captures are deliberately not committed; they stay on the
measurement host.
