# Streaming QoE results — YouTube and Vimeo (Step A)

One extraction method and one schema on both apps, with no per-site
APIs. Solo runs carry one app each. The concurrent cell runs both
apps at once over a single shared cap; each app still gets its own
folder, its own charts and its own per-app pcap, drawn only from the
traffic attributed to it, and the two apps' IP sets are disjoint.
Every chart is drawn from the per-second samples by `make_release.py`,
and the numbers it shows are asserted against `summary.json` during the
build, so a chart that disagreed fails the build rather than shipping.

The bands and the procedure below were fixed and committed **before**
any of these runs were started, so they could not be chosen to fit the
results. No band was widened and no run was excluded afterwards.

## What VERIFIED means here

A metric is **VERIFIED for an app** only if, on **every** run of that
app, it was produced (non-null) **and every pre-registered check that
applies to it passed**.

- **FAILED** — produced, but at least one applicable check failed on at
  least one run.
- **UNKNOWN** — not produced on at least one run, so no check could run.

The previous release had no such definition and reported delivered
bitrate as "VERIFIED 11/11" while 4 of those 11 runs failed the
pre-registered bitrate band — "11/11" counted only production. Under
the definition above that cannot happen: a band failure forces FAILED
for the whole app.

## Run matrix

| | |
|---|---|
| apps | twitch, vimeo, youtube |
| bandwidth | 1, 3, 6, 10 Mbps |
| latency | 20, 50, 150 ms (baseline 50 ms) |
| AQM / CCA / loss | pfifo / cubic / 0 % |
| duration | 180 s |
| solo cells | 8 (one run per app x bandwidth) |
| concurrent cells | 3 (both apps sharing one cap) |
| run folders | **19** from **11** capture(s) |

The AQM sweep is a later pass and is not in this branch.

### Step B, first pass: the latency sweep

Solo runs only, 6 Mbps, at 20, 50 and 150 ms, two trials per cell.
The plan and every band were pre-registered in
`validation/THRESHOLDS.md` and committed **before** any of it was
collected. The 2.0 s cadence bar was not changed.

**The 50 ms cells replace the earlier Step A 6 Mbps / 50 ms solo
runs.** They measure the same cell; the Step B pair is newer and was
collected in the same session as the rest of the sweep, so the sweep
is internally consistent. The Step A originals are not deleted - they
are archived off-repo with their checksums, and their numbers remain
in the earlier published release.

**Only trial 1 of each cell is carried in this repo.** Trial 2 is
archived, and its numbers are in `sweep_matrix.csv`, which marks every
row with whether the folder is in the repo or archived. Charts and
`samples.json` are kept in full for the runs that are here.

Shaping was checked twice: the substrate reported the latency it
applied (within 10 % on all 12 runs, the pre-registered L1 band), and
independently the round-trip times measured from the captures sit at
23, 53 and 153 ms for the three settings - the configured delay plus
about 3 ms of real path. The second check matters because the first is
the shaper reporting on itself.

### The concurrent cell: what is and is not synchronised

A barrier holds both apps until each has a ready player, so
neither spends its window waiting for the other to load. It does
**not** give them an identical sampling window: after the barrier
each app independently waits for media-ready, triggers play and
drains its own CDP backlog, and the player can still reload under
contention. Measured on this cell:

| app | samples | window | first sample vs earliest |
|---|---|---|---|
| vimeo | 180 | 179.0 s | +0.0 s |
| youtube | 180 | 179.0 s | +1.6 s |
| vimeo | 180 | 179.0 s | +7317.1 s |
| youtube | 180 | 179.0 s | +7317.4 s |
| vimeo | 180 | 179.0 s | +8963.7 s |
| youtube | 180 | 179.0 s | +8964.1 s |
| twitch | 180 | 179.0 s | +8964.1 s |

Each app's own metrics are computed over its own window. The
shared-cap check does not assume equal windows: it sums the apps
on a common 1 s grid. Compare the two apps to each other only
with that difference in mind.

## Status: app x metric

| metric | Twitch | Vimeo | Youtube |
|---|---|---|---|
| 1 startup delay | **VERIFIED**<br><sub>1/1 run(s), all checks passed</sub> | **VERIFIED**<br><sub>9/9 run(s), all checks passed</sub> | **VERIFIED**<br><sub>9/9 run(s), all checks passed</sub> |
| 2 buffer rebuffering | **VERIFIED**<br><sub>1/1 run(s), all checks passed (B4: no reference)</sub> | **VERIFIED**<br><sub>9/9 run(s), all checks passed (B4: no reference)</sub> | **VERIFIED**<br><sub>9/9 run(s), all checks passed</sub> |
| 3 delivered bitrate | **UNKNOWN**<br><sub>produced on 0 of 1 run(s)</sub> | **VERIFIED**<br><sub>9/9 run(s), all checks passed</sub> | **VERIFIED**<br><sub>9/9 run(s), all checks passed</sub> |
| 4 switching | **VERIFIED**<br><sub>1/1 run(s), all checks passed</sub> | **VERIFIED**<br><sub>9/9 run(s), all checks passed</sub> | **VERIFIED**<br><sub>9/9 run(s), all checks passed</sub> |
| 5 frame rate | **VERIFIED**<br><sub>1/1 run(s), all checks passed</sub> | **VERIFIED**<br><sub>9/9 run(s), all checks passed</sub> | **VERIFIED**<br><sub>9/9 run(s), all checks passed</sub> |

Dropped frames are carried as raw counts only; throughput is not a QoE
metric and appears only in its own two charts and the cap check.

## Checks

| | count |
|---|---|
| PASS | 423 |
| **FAIL** | **2** |
| no reference (N/A) | 12 |

### Every failing check

| run | check | detail |
|---|---|---|
| twitch_6Mbps_50ms_pfifo_with_youtube+vimeo_trial531 | **R2** | video=None audio=None |
| twitch_6Mbps_50ms_pfifo_with_youtube+vimeo_trial531 | **R3** | mse_video_bytes=0.0 |

## Per-run results

| run | verdict | res | startup (ms) | rebuffers | video Mbps | audio Mbps | switches | fps |
|---|---|---|---|---|---|---|---|---|
| twitch_6Mbps_50ms_pfifo_with_youtube+vimeo_trial531 | OK | 720p | 8738.0 | 0 | None | None | 1 | 29.5 |
| vimeo_1Mbps_50ms_pfifo_trial611 | OK | 240p | 10528.0 | 0 | 0.2861 | 0.2091 | 2 | 24.01 |
| vimeo_3Mbps_50ms_pfifo_with_youtube_trial521 | OK | 360p | 6744.0 | 0 | 1.0222 | 0.196 | 4 | 23.99 |
| vimeo_3Mbps_50ms_pfifo_trial621 | OK | 540p | 4040.0 | 0 | 1.1016 | 0.196 | 3 | 23.99 |
| vimeo_6Mbps_50ms_pfifo_with_youtube_trial401 | OK | 720p | 3803.0 | 0 | 1.9889 | 0.1895 | 4 | 23.99 |
| vimeo_6Mbps_50ms_pfifo_with_youtube+twitch_trial531 | OK | 240p | 4450.0 | 0 | 0.395 | 0.196 | 5 | 23.99 |
| vimeo_6Mbps_50ms_pfifo_trial641 | OK | 720p | 2585.0 | 0 | 2.2958 | 0.196 | 5 | 24.0 |
| vimeo_6Mbps_20ms_pfifo_trial661 | OK | 720p | 2225.0 | 0 | 2.3382 | 0.196 | 5 | 24.01 |
| vimeo_6Mbps_150ms_pfifo_trial681 | OK | 720p | 3813.0 | 0 | 2.7937 | 0.2026 | 3 | 24.01 |
| vimeo_10Mbps_50ms_pfifo_trial541 | OK | 1080p | 2055.0 | 0 | 4.2685 | 0.2026 | 5 | 24.0 |
| youtube_1Mbps_50ms_pfifo_trial601 | OK | 360p | 18857.0 | 1 | 0.2111 | 0.1333 | 4 | 25.01 |
| youtube_3Mbps_50ms_pfifo_with_vimeo_trial521 | OK | 480p | 11620.0 | 0 | 0.3058 | 0.1027 | 0 | 25.0 |
| youtube_3Mbps_50ms_pfifo_trial551 | OK | 720p | 7610.0 | 0 | 0.615 | 0.1173 | 0 | 25.0 |
| youtube_6Mbps_50ms_pfifo_with_vimeo_trial401 | OK | 480p | 5408.0 | 0 | 0.3607 | 0.1244 | 0 | 25.01 |
| youtube_6Mbps_50ms_pfifo_with_vimeo+twitch_trial531 | OK | 480p | 11677.0 | 0 | 0.3607 | 0.1244 | 0 | 25.0 |
| youtube_6Mbps_50ms_pfifo_trial631 | OK | 720p | 4512.0 | 0 | 0.6559 | 0.1244 | 0 | 25.0 |
| youtube_6Mbps_20ms_pfifo_trial651 | OK | 720p | 4047.0 | 0 | 0.6751 | 0.132 | 0 | 24.98 |
| youtube_6Mbps_150ms_pfifo_trial671 | OK | 720p | 4618.0 | 0 | 0.6168 | 0.1173 | 0 | 25.0 |
| youtube_10Mbps_50ms_pfifo_trial541 | OK | 720p | 3179.0 | 0 | 0.6945 | 0.132 | 0 | 24.99 |

## The checks every run had to pass

| id | what it checks | bar |
|---|---|---|
| S1,S2 | startup produced, positive, inside the session | 0 < startup < duration |
| S3 | the three startup quantities add up on one clock | residual <= 50 ms |
| B1 | sampling cadence | every gap <= 2x the 1.0 s interval |
| B2,B3 | buffer level physical; rebuffer count produced | buffer >= 0 |
| B4 | buffer agrees with YouTube's own figure (YouTube only) | MAE <= 1.0 s, r >= 0.90 |
| R1 | delivered bytes vs Chrome's Network domain | cdp_received / appended in [1.00, 1.15] |
| R2,R3 | video and audio reported separately, video non-zero | both non-null |
| W1,W2 | switches timestamped with direction; chart agrees with count | drawn <= switch_count |
| F1,F2,F3 | rendered rate positive, <= decoded, chart equals summary | <= decoded x 1.05 |
| J1,J2 | throughput within the shaped cap, peak and mean | peak <= cap x 1.05; mean <= cap |
| | on a concurrent cell both apps are summed on a 1 s grid first, so neither can hide behind the other | same bars, applied to the sum |
| J3,J4 | a pcap per run and per app; download and upload plotted separately | files present |
| J5,J6,J7 | fixed 0-11 Mbps line charts clipped to playback; playback verdict; chart numbers equal summary | — |

Each run's `summary.json` carries its own `checks` block with the
measured value for every one of them.

## Run folder layout

```
<app>/<app>_<bw>Mbps_<lat>ms_<aqm>_trial<N>/              solo
<app>/<app>_<bw>Mbps_<lat>ms_<aqm>_with_<other>_trial<N>/   concurrent
  samples.json   raw per-second samples, schema and interval
  summary.json   the five metrics, run metadata, byte references, checks
  download_throughput.png   upload_throughput.png   (separate, 0-11 Mbps)
  buffer.png  resolution.png  bitrate.png  switches.png  fps.png  startup.png
```

## pcaps

Not committed. Each run has a full `capture.pcap` and a per-app pcap
containing only packets to or from IPs attributed to that app; both are
listed with size and SHA-256 in `pcap_manifest.csv`.

## Reproducing

```bash
cd experiments/pramana
DUR=180 python run_matrix.py                 # the solo cells
DUR=180 python run_concurrent.py             # the concurrent cell
python make_release.py results_release <run_dir> [<run_dir> ...]
```

Repo commit, Chrome version, VM name and the shaping parameters for each
run are in that run's `summary.json` under `run_metadata`.

Built from commit `d1f5bf1`, Chrome `Google Chrome 154.0.8037.57`, on `docker-vm-4 8vCPU 7.9GB Ubuntu-24.04.4`.

The collector deployed on the VM when these runs were collected is
byte-identical (sha256) to this commit's
`selenium_video_qoe/collect.py`, so the stamp above is checked, not
assumed. One caveat: that file was last written partway through the
collection session, after the three vimeo solo runs and before the
youtube solo runs and the concurrent cell. Every run in this release
carries the same per-sample instrumentation (`loop_ms`, `host_load1`),
so none predates the instrumented collector, but whether the three
vimeo solo runs used a byte-identical file is **unknown**.
