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
| all six | `640x360@1458x820` | `remote-stream` | **0** |

Zero local samples in all six cells, so the collector's own camera is never what
was measured — the failure mode that invalidated the previous sweep cannot
recur silently, because `record.json` now states which element was measured.

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
| 3 | 150 ms | 360p | 16.58 | 21.45 % | 6 | VERIFIED |
| 3 | 50 ms | 360p | 20.62 | 15.29 % | 3 | VERIFIED |
| 6 | 150 ms | 360p | 20.39 | 17.40 % | 3 | VERIFIED |
| 6 | 50 ms | 360p | 21.21 | 17.10 % | 1 | VERIFIED |
| 10 | 50 ms | 360p | 20.46 | 18.52 % | 0 | REAL-CONDITION-FINDING |
| 10 | 150 ms | 360p | 22.54 | 22.21 % | 0 | REAL-CONDITION-FINDING |

The two REAL-CONDITION-FINDING cells are host CPU, not a failed check: peaks of
85.7 % and 117.8 % of total capacity. The four VERIFIED cells peaked at
77.1–84.9 %.

## Does the dropped-frame column behave consistently now?

**Yes.** It sits in a narrow **15.29–22.21 %** band across the whole grid — a
7-point spread — against the previous sweep's 0.0 % to 99.971 %. Every cell
measured the same element type at the same size (`640x360@1458x820`), which is
what makes the column comparable at all.

The residual ~18 % is not bandwidth-driven. It does not fall as the cap rises;
if anything it rises slightly (15.29 % at 3 Mbps/50 ms to 22.21 % at
10 Mbps/150 ms) while frame rate also rises, which is the signature of decode
cost on a saturated host rather than loss or starvation on the link.

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
