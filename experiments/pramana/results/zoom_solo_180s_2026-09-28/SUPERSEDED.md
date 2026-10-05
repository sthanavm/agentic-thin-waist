# SUPERSEDED — do not use the Zoom numbers in this folder

Replaced by `../zoom_solo_180s_2026-10-05`. Kept for provenance, not for use.

The Zoom QoE in these runs was measured on an element that varied from run to
run, so the columns are not comparable with each other or with the new sweep.

## Why this sweep reads 720p where the new one reads 360p

From the stored samples of both sweeps:

| sweep | decoded size | rendered box | element origin | label |
|---|---|---|---|---|
| 2026-09-28 (this one) | `1280x720` | **`207x117`** (most runs) | not recorded | not recorded |
| 2026-10-05 (new) | `640x360` 71-84 % | `1458x820` | `remote-stream` | `remote video` |

Per-run geometry here, from `zoom_stats.jsonl`:

| run | label | decoded | box |
|---|---|---|---|
| `…_a39f2fce09` (10M/150ms) | 720 | `1280x720` | `1756x988` |
| `…_afc36dd2b8` (10M/50ms) | 720 | `1280x720` | `207x117` |
| `…_a15e72bd94` (3M/150ms) | 720 | `1280x720` | `207x117` |
| `…_80f23577c7` (3M/50ms) | 720 | `1280x720` | `207x117` + `254x143` |
| `…_ba3a5f74ab` (3M/50ms) | 720 | `1280x720` | `700x394` |
| `…_ba38d17656` (6M/150ms) | 720 | `1280x720` | `207x117` |
| `…_5cc750cc44` (6M/50ms) | 720 | `1280x720` | `207x117` + `254x143` |
| `…_7280b20869` (6M/50ms) | 720 | `1280x720` | `207x117` |
| `…_8eb9302760` (3M/150ms) | 720 | `1280x720` | **`0x0`** (158/159 samples) |
| four others | None | — | `(None,None)` — no data |

## What the evidence proves

- **These runs measured a different element than the new sweep.** The box was
  `207x117`, `254x143`, `700x394`, `0x0` or `1756x988`; the new sweep measured
  `1458x820` in every sample of every cell. A `207x117` box is ~1.4 % of the
  area of the `1458x820` main view.
- **The 720p label is therefore not Zoom's delivered resolution for the call**
  in the way the new sweep's 360p is. It is the decoded size of whatever element
  was selected, and selection was unstable across runs.
- **One run (`…_8eb9302760`) measured an uncomposited element** (`0x0` box for
  158 of 159 samples) and reported 99.971 % dropped frames — a measurement
  artefact, not a network condition.
- **The dropped-frame column here spans 0.0 % to 99.971 %** against the new
  sweep's 15.29-22.21 %, and that spread tracks element geometry rather than the
  shaped condition.

## What the evidence cannot prove

**It cannot establish that these runs measured the collector's own self-view.**
Origin tagging did not exist in September: neither the `getUserMedia` track-id
hook nor the track-label capture was present, so no sample here records whether
its element was local or remote.

The geometry is consistent with a self-view — in the October runs, an element
with exactly this signature (`1280x720` decoded in a `207x117` box) carries the
track label `fake_device_0`, which is Chrome's synthetic camera, i.e. the
collector's own. But consistency is not identification: Zoom could in principle
place a remote tile in a thumbnail of that size, and in fact the October samples
contain `320x180@207x117` and `640x360@207x117` elements labelled
`remote video`, proving a `207x117` box is not exclusively local.

So the honest statement is: **the measured element differed, the labels are not
comparable, and whether these specific runs read the local camera is not
recoverable from the data they stored.** That is why they are superseded rather
than reinterpreted.
