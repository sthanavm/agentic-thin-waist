# Streaming QoE results — YouTube and Vimeo (Step A)

One extraction method and one schema on both apps, with no per-site
APIs. Every run is a single app; no run and no chart mixes the two.
Every chart is drawn from the per-second samples by `make_release.py`,
and the numbers it shows are asserted against `summary.json` during the
build, so a chart that disagreed fails the build rather than shipping.

Bands and procedure were committed in
`validation/THRESHOLDS.md` **before any of these runs started**.

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
| apps | YouTube, Vimeo (one app per run) |
| bandwidth | 1, 3, 6 Mbps |
| latency | 50 ms (project baseline for solo runs) |
| AQM / CCA / loss | pfifo / cubic / 0 % |
| duration | 180 s |
| trials | 2 per cell — **12 runs total** |

Latency and AQM sweeps are Step B and are not in this branch.

## Status: app x metric

| metric | Vimeo | Youtube |
|---|---|---|
| 1 startup delay | **VERIFIED**<br><sub>6/6 run(s), all checks passed</sub> | **VERIFIED**<br><sub>6/6 run(s), all checks passed</sub> |
| 2 buffer rebuffering | **VERIFIED**<br><sub>6/6 run(s), all checks passed (B4: no reference)</sub> | **VERIFIED**<br><sub>6/6 run(s), all checks passed</sub> |
| 3 delivered bitrate | **VERIFIED**<br><sub>6/6 run(s), all checks passed</sub> | **VERIFIED**<br><sub>6/6 run(s), all checks passed</sub> |
| 4 switching | **VERIFIED**<br><sub>6/6 run(s), all checks passed</sub> | **VERIFIED**<br><sub>6/6 run(s), all checks passed</sub> |
| 5 frame rate | **VERIFIED**<br><sub>6/6 run(s), all checks passed</sub> | **VERIFIED**<br><sub>6/6 run(s), all checks passed</sub> |

Dropped frames are carried as raw counts only; throughput is not a QoE
metric and appears only in its own two charts and the cap check.

## Checks

| | count |
|---|---|
| PASS | 270 |
| **FAIL** | **0** |
| no reference (N/A) | 6 |

No check failed.

## Per-run results

| run | verdict | res | startup (ms) | rebuffers | video Mbps | audio Mbps | switches | fps |
|---|---|---|---|---|---|---|---|---|
| vimeo_1Mbps_50ms_pfifo_trial1 | OK | 240p | 10458.0 | 0 | 0.2958 | 0.2156 | 2 | 23.97 |
| vimeo_1Mbps_50ms_pfifo_trial2 | OK | 240p | 11160.0 | 0 | 0.2325 | 0.196 | 3 | 23.97 |
| vimeo_3Mbps_50ms_pfifo_trial1 | OK | 540p | 4294.0 | 1 | 1.1049 | 0.2156 | 4 | 23.88 |
| vimeo_3Mbps_50ms_pfifo_trial2 | OK | 540p | 4512.0 | 0 | 1.0476 | 0.2026 | 4 | 23.96 |
| vimeo_6Mbps_50ms_pfifo_trial1 | OK | 720p | 2772.0 | 0 | 2.3043 | 0.196 | 4 | 23.93 |
| vimeo_6Mbps_50ms_pfifo_trial2 | OK | 720p | 2870.0 | 0 | 2.2959 | 0.196 | 4 | 23.89 |
| youtube_1Mbps_50ms_pfifo_trial1 | OK | 720p | 20701.0 | 0 | 0.5475 | 0.1253 | 2 | 25.0 |
| youtube_1Mbps_50ms_pfifo_trial2 | OK | 360p | 18113.0 | 0 | 0.184 | 0.1257 | 3 | 25.0 |
| youtube_3Mbps_50ms_pfifo_trial1 | OK | 720p | 7800.0 | 0 | 0.5977 | 0.1179 | 0 | 25.0 |
| youtube_3Mbps_50ms_pfifo_trial2 | OK | 480p | 7718.0 | 0 | 0.2876 | 0.1027 | 0 | 24.99 |
| youtube_6Mbps_50ms_pfifo_trial1 | OK | 480p | 5274.0 | 0 | 0.3168 | 0.1103 | 0 | 24.99 |
| youtube_6Mbps_50ms_pfifo_trial2 | OK | 720p | 5349.0 | 0 | 0.5504 | 0.1027 | 0 | 24.95 |

## Run folder layout

```
<app>/<app>_<bw>Mbps_<lat>ms_<aqm>_trial<N>/
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
DUR=180 python run_matrix.py                 # the 12 runs in the table
python make_release.py results_release <run_dir> [<run_dir> ...]
```

Repo commit, Chrome version, VM name and the shaping parameters for each
run are in that run's `summary.json` under `run_metadata`.

Built from commit `536adf0`, Chrome `Google Chrome 154.0.8037.57`, on `docker-vm-4`.
