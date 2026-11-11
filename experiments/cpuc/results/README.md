# YouTube streaming against IQB 2025 — bandwidth × latency

**This README covers the `streaming_*` files only.** The folder holds three
studies: `streaming_*` (YouTube, #21), `meet_*` (Google Meet, #20) and
`teams_*` (Microsoft Teams, #22, not ours, not merged yet). For Meet and Teams read
`../RESULTS.md`, which is the index for all three and carries the corrections
log. The `*_manifest.lat.*` and `*_manifest.lowend.*` files record sha256 on vm4
and after transfer under the names the files had **on vm4**, before the
per-study prefix was added — `cpuc_runs.csv` there is this folder's
`streaming_cpuc_runs.csv`, and the hashes still match. The
`*_manifest.charts.txt` files use this folder's own names and can be checked
in place with `shasum -a 256 -c`.

Does IQB 2025's **25 Mb/s** streaming download minimum (Fig 10) describe what
streaming actually needs? This set answers it across three configured
latencies, with a speed test run on the same shaped link at every point so the
measured QoE can be set against the verdict a speed test would have produced.

## Read the caveats first

- **IQB's streaming latency minimum is 100 ms, and our 100 ms rows measure
  102.5–103.2 ms.** netem adds its delay on top of the real path, so the
  100 ms setpoint lands 2.5–3.2 ms *over* IQB's threshold. That is the only
  reason the 100 Mb/s reference row fails on the latency leg. It is a
  measurement artifact, not a finding — **do not quote the latency leg**. The
  argument here rests on the **download** leg, which fails at every capacity
  at or below 25 Mb/s on its own.
- **5 of 24 speed tests did not complete**, all at 50 ms and 6.25 Mb/s or
  below: M-Lab rate-limited us (`Too many periodic requests`). Those rows show
  `—` for both NDT columns and `no ndt` for the IQB verdict rather than
  borrowing a number from another latency. The **app** runs at those points
  are valid and complete — only the speed test is missing.
- **Mean demand is over the sustained playback window, not the whole
  capture.** It runs from the first app byte to the last, per run, with GGC
  media attribution corrected (#25) — an earlier version of this note
  described a whole-capture mean diluted by a 6–72 s idle tail, which stopped
  being true when the window was introduced and the figures were re-derived.
  Each sidecar entry carries its own `basis`, `window_s`, `window_start_s` and
  `youtube_active_mbps` (the rate while the player was actually pulling), so
  the basis of every number can be checked against the number. Still not
  evidence that latency changed demand: each point is one run.
- **19 of 24 app runs carry the `suspect` flag** (⚠ in the table) for Chrome's
  own background fetches inside the measured window. That makes those rows
  conservative — the app had *less* of the link than the capacity suggests,
  not more.

## Coverage

24 app runs and 24 speed tests: 8 capacities × 3 latencies, no induced loss.
All 24 app runs are valid. 19 of 24 speed tests are valid.

| | 20 ms | 50 ms | 100 ms |
|---|---|---|---|
| configured | 20 ms | 50 ms | 100 ms |
| measured (`ndt_minrtt`) | 22.7–23.3 ms | 52.5–52.7 ms | 102.5–103.2 ms |
| IQB streaming latency | below both values | below the 100 ms minimum | **over** the 100 ms minimum |

IQB 2025 video streaming: download 25 min / 50–100 high, upload 10/10,
latency 100 min / 50 high, loss 1% / 0.1%.

## Result

See `streaming_iqb_vs_qoe_table.md` for all 24 rows, each carrying the configured RTT and
the measured RTT.

**720p at every capacity from 6.25 Mb/s up, at all three latencies**, with no
rebuffering, against a source that offered 2160p the whole time. A speed test
calls all of those links failing.

| capacity | 20 ms | 50 ms | 100 ms |
|---|---|---|---|
| 100 Mb/s *(ref)* | 720p | 720p | 720p |
| 25 | 720p **high** | 720p **high** | 720p **high** |
| 12.5 | 720p **high** | 720p **high** | 720p **high** |
| 6.25 | 720p **high** | 720p **high** | 720p **high** |
| 3 | 720p acceptable | 720p acceptable | 480p acceptable |
| 1.5 | 240p unusable | 240p unusable | 240p unusable |
| 1 | 240p unusable | 240p unusable | 360p unusable |
| 0.5 | 144p unusable | 144p unusable | 144p unusable |

**Latency barely moves the quality.** The breakpoint sits between 3 and
6.25 Mb/s at all three latencies. The only movement is at 3 Mb/s, where 100 ms
costs one rendition step (720p → 480p) at the same QoE grade. What latency
moves is **startup delay**: 2.5 s at 100 Mb/s against 39–47 s at 0.5 Mb/s.

**The disagreement is the finding.** At 6.25, 12.5 and 25 Mb/s — at every
latency — a speed test says the link fails IQB's minimum while the player
delivers 720p with zero rebuffering. That is 9 of 24 points where the two
verdicts are opposite, ringed in `streaming_contrast_iqb_vs_qoe.png`.

**The 720p ceiling is not the link.** It holds at 100 Mb/s too. The
supplementary 1080p-viewport set (`../results_1080p/`) establishes that it is
not the viewport either: with a 1920x1080 element YouTube reports
`1280x720@25 / 1920x1080@25` and serves 720p anyway, dropping 12–16% of frames
decoding AV1 in software. The constraint is host decode, not bandwidth.

## Charts

Every chart marks IQB's 25 Mb/s minimum and carries latency as a series, with
both the configured and the measured RTT in the legend or panel title.

| chart | what it shows |
|---|---|
| `streaming_contrast_iqb_vs_qoe.png` | IQB's verdict against measured QoE, one panel per latency. The ringed points are where they disagree. |
| `streaming_demand_vs_capacity.png` | what YouTube used against what the link offered, with the window and the n=1 caveat on the chart itself |
| `streaming_source_vs_delivered.png` | the 2160p ladder the source offered against the rendition the player chose, per latency |
| `streaming_startup_vs_bandwidth.png` | startup delay, which is where latency and capacity actually show up |
| `streaming_rebuffer_vs_bandwidth.png` | rebuffer ratio — zero everywhere above 1 Mb/s |
| `streaming_qoe_vs_bandwidth.png` | delivered resolution against capacity |

## Files

| file | what it is |
|---|---|
| `streaming_cpuc_runs.csv` | one row per run, all three latencies, 53 rows |
| `streaming_iqb_vs_qoe_table.md` / `.csv` | the joined app + speed-test table, 24 points |
| `youtube_demand_bylat.json` | per-app demand keyed `capacity@rtt`, with the window, its basis, and the active rate |
| `youtube_demand.json` | the original rate-only sidecar, kept because the 20 ms entries name the specific runs they came from |
| `renditions.json` | the source ladder, probed once |
| `lat100.log.txt` / `lat50.log.txt` | the two batch logs |
| `manifest.lat.remote.txt` / `.local.txt` | sha256 on vm4 and after transfer; 7 files, all matched |
| `COMPLETE.lat50` | end-of-batch marker, including the 5 rate-limited speed tests |
