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
| apps | vimeo, youtube |
| bandwidth | 1, 3, 6 Mbps |
| latency | 50 ms (project baseline) |
| AQM / CCA / loss | pfifo / cubic / 0 % |
| duration | 180 s |
| solo cells | 6 (one run per app x bandwidth) |
| concurrent cells | 1 (both apps sharing one cap) |
| run folders | **8** from **7** capture(s) |

Latency and AQM sweeps are Step B and are not in this branch.

### B1 across repeats of the concurrent cell

The concurrent cell was run **4 times**. B1 is the sampling-cadence
bar: every inter-sample gap at most 2x the 1.0 s interval. The bar
was not changed.

| trial | app | samples | B1 | max gap | slowest stats call | script timeouts | load1 mean | load1 at gate |
|---|---|---|---|---|---|---|---|---|
| t1 | youtube | 160 | **FAIL** | 2.000 s | 1967.3 ms | 0 | 9.37 | 2.84 |
| t1 | vimeo | 180 | PASS | 1.059 s | 495.4 ms | 0 | 9.09 | 2.84 |
| t2 | youtube | 152 | **FAIL** | 14.048 s | 14023.6 ms | 0 | 10.75 | 2.87 |
| t2 | vimeo | 180 | PASS | 1.113 s | 491.1 ms | 0 | 10.50 | 2.87 |
| t3 | youtube | 156 | **FAIL** | 20.001 s | 3841.1 ms | 1 | 10.32 | 2.84 |
| t3 | vimeo | 180 | PASS | 1.043 s | 831.7 ms | 0 | 10.20 | 2.84 |
| t4 | youtube | 152 | **FAIL** | 24.462 s | 2653.7 ms | 1 | 10.88 | 2.52 |
| t4 | vimeo | 180 | PASS | 1.044 s | 543.5 ms | 0 | 10.60 | 2.52 |

**YouTube fails B1 on 4 of 4 repeats; Vimeo fails 0 of 4.**

The single run in this release is the *mildest* of them (2.00 s
against the 2.0 s bar). The repeats reach 24.462 s, so the failure is
not a borderline one and must not be read as such. In the worst
cases a single reading of the page blocked for 14-24 s; the
10 s script timeout either returned late or did not stop it, that
sample carried no statistics, and the player's clock reset to 0
afterwards. Vimeo, on the same host in the same window, never
exceeded 832 ms.

The load gate cannot prevent this: it passed at 2.52-2.87 before
every run, and the load that accompanies the failure (mean 9.4-10.9)
is produced by the run itself, two browsers on two vCPUs. Whether
that contention *causes* the stall is **not established** - the
repeats show it reproduces, not why.

Per-trial numbers are in `concurrent_b1_trials.csv`.

### Was the page blocked, or only our reading of it?

The sampler above asks the page for its state once a second. When
that read blocks, it cannot say whether the page stalled or only
the read did -- the reading that would tell us is the one that
failed. So the page was made to log itself: a timer appends an
entry every 200 ms, read over a separate CDP connection with its
own deadline. `performance.now()` keeps advancing while a thread
is blocked, so a timestamp proves nothing; a **missing entry**
does, because a blocked thread cannot run the timer.

| trial | app | B1 | sampler gap | page-clock gap | page stalls | page blocked |
|---|---|---|---|---|---|---|
| t5 | youtube | **FAIL** | 26.121 s | 16.730 s | 13 | **yes** |
| t5 | vimeo | PASS | 1.074 s | 0.667 s | 1 | no |
| t6 | youtube | **FAIL** | 23.062 s | 15.861 s | 15 | **yes** |
| t6 | vimeo | PASS | 1.051 s | 0.682 s | 1 | no |
| t7 | youtube | **FAIL** | 25.640 s | 16.961 s | 21 | **yes** |
| t7 | vimeo | PASS | 1.370 s | 0.993 s | 1 | no |

**The YouTube page itself blocks.** Its own timer misses for
15.9-17.0 s, three runs out of three. Vimeo's never misses by
more than 1.0 s. So the cadence failure is not an artifact of how
we read the page: the page's main thread genuinely stops.

It does not account for all of it. The sampler's gap is 23.1-26.1 s
against a page block of 15.9-17.0 s, and the block falls inside the
gap in every run, so roughly 7-9 s is the read path on top of a
page that had already stopped. What blocks the page is still
**unknown** - this says where the time goes, not why.

The separate connection's deadline was never exercised here (0 read
timeouts in all six). The WebDriver read still runs first in each
iteration, so it absorbs the stall before the in-page read is
issued; the deadline was shown to hold on a synthetic 8 s block by
`test_inpage_timeout.py`, not on these runs.

Per-trial numbers are in `inpage_trials.csv`. These runs used the
in-page sampler and are diagnostic; they are not part of the
measurement set above.

### Why the YouTube page freezes

Four conditions, each able to come out either way. A freeze is a
gap in the page's own 200 ms timer of more than 2 s.

| condition | what it tests | app | B1 | page-clock gap | longest task | renderer CPU during it | froze |
|---|---|---|---|---|---|---|---|
| solo | youtube alone | youtube | PASS | 0.645 s | 267 ms | 478.7 ms/s | no |
| cpuload | youtube + 2 CPU burners (no 2nd browser) | youtube | **FAIL** | 18.654 s | 13548 ms | 202.8 ms/s | **yes** |
| twitch | youtube + twitch | twitch | **FAIL** | 1.637 s | 1375 ms | 415.1 ms/s | no |
| twitch | youtube + twitch | youtube | **FAIL** | 4.773 s | 3824 ms | 356.2 ms/s | **yes** |
| pinned | youtube + vimeo (one core each) | vimeo | PASS | 0.758 s | 646 ms | 500.8 ms/s | no |
| pinned | youtube + vimeo (one core each) | youtube | **FAIL** | 16.612 s | 14494 ms | 381.6 ms/s | **yes** |

**It is CPU starvation, and nothing to do with Vimeo.**

- *YouTube alone does not freeze*: worst gap 0.65 s, longest task
  267 ms. So contention is necessary.
- *CPU load alone is enough*: two spinner processes, no second
  browser and no shared link, reproduce it in full - an 18.65 s
  freeze. That rules out any explanation that needs Vimeo, a
  second browser, or the shared cap.
- *A different second app still freezes it* (4.77 s beside Twitch),
  which agrees.
- *One core each does not fix it* (16.61 s). Pinning was the
  obvious remedy and it failed, so the problem is not two browsers
  colliding on one core.

**Starved, not busy.** A 13.5 s long task is recorded during the
freeze, which on its own might suggest the page was working hard.
The CPU probe says otherwise: the renderer got **202.8 ms of CPU
per wall second** during that freeze, against **478.7 ms/s** in the
unloaded solo run - about 40 % of its normal rate, on a 2 vCPU host
where all of Chrome together held only ~400 of 2000 ms/s while the
spinners took the rest. A task holding the thread for 13.5 s while
receiving a fifth of a core is a task being descheduled, not one
computing. Long-task duration is wall-clock and cannot tell these
apart by itself; the CPU figure is what separates them.

Why YouTube and not Vimeo: YouTube's renderer wants appreciably
more CPU (478.7 ms/s unloaded), so it is the one that starves first
when the host is oversubscribed. Vimeo passed B1 in every condition
here, including the pinned run.

This is a property of the 2 vCPU measurement host, not of YouTube's
delivery. It limits what a concurrent cell on this host can measure;
it is **not** a finding about the network or the service.

Per-condition numbers are in `freeze_conditions.csv`. Reproduce with
`run_freeze_tests.py <condition>`, which also runs `cpu_probe.py`.

### Is the freeze ours, or was it always there?

Four samplers, three trials each, same cell and same gate. If our
instrumentation caused the freeze, removing it should remove the
freeze.

| sampler | what it is | YouTube B1 | YouTube max gap | Vimeo B1 |
|---|---|---|---|---|
| A | current full extractor (control) | 3/3 FAIL | 19.2-27.1 s | 0/3 FAIL |
| B | uniform hooks + CDP drain + in-page log OFF | 3/3 FAIL | 15.2-25.7 s | 0/3 FAIL |
| C | collector at 4ada635 (before uniform QoE hooks) | 3/3 FAIL | 18.9-26.6 s | 1/3 FAIL |
| D | Jaber approach: plain video + stats-for-nerds at 2 s | 3/3 FAIL | 19.6-22.3 s | 0/3 FAIL |

**YouTube fails in 12 of 12 runs, under every sampler.** Removing
our hooks (B), going back to a collector written before the uniform
QoE hooks existed (C), and sampling a different way entirely at 2 s
(D) all freeze just as badly. **The instrumentation is not the
cause.** Vimeo passed in all but one run throughout.

Renderer CPU during the longest gap was 136-448 ms per wall second
in every condition, well under one core, with host load1 8.1-13.0 -
the same starvation signature as the `cpuload` test above.

**And it predates all of it.** Recomputing B1 on 63 archived
YouTube+Vimeo concurrent series from 2026-09-26, none of which
carry our per-sample instrumentation:

| app | B1 FAIL | worst gap | run length |
|---|---|---|---|
| youtube | **17/63** | 9.15 s | 34-120 s |
| vimeo | 1/63 | 2.54 s | same |

The same YouTube-only asymmetry, already there in September.

What did change is the size. Archived gaps top out at 9.15 s;
the runs above reach 15-27 s. That is **not** run length: every
freeze above happens inside the first 60 s, which is the length
of a typical archived run, and truncating the new runs to 60 s
leaves the gaps unchanged. It is **not** our instrumentation,
for the reason in the table. The remaining candidate is the host
being more heavily loaded now than in September, which is
consistent with load1 8-13 during these runs but **cannot be
confirmed**: the archived runs recorded no host load, so there
is nothing to compare against. That part stays open.

Numbers in `freeze_origin.csv` and `archived_concurrent_b1.csv`.

### The concurrent cell: what is and is not synchronised

A barrier holds both apps until each has a ready player, so
neither spends its window waiting for the other to load. It does
**not** give them an identical sampling window: after the barrier
each app independently waits for media-ready, triggers play and
drains its own CDP backlog, and the player can still reload under
contention. Measured on this cell:

| app | samples | window | first sample vs earliest |
|---|---|---|---|
| vimeo | 180 | 178.8 s | +0.0 s |
| youtube | 160 | 161.1 s | +18.9 s |

Each app's own metrics are computed over its own window. The
shared-cap check does not assume equal windows: it sums the apps
on a common 1 s grid. Compare the two apps to each other only
with that difference in mind.

## Status: app x metric

| metric | Vimeo | Youtube |
|---|---|---|
| 1 startup delay | **VERIFIED**<br><sub>4/4 run(s), all checks passed</sub> | **VERIFIED**<br><sub>4/4 run(s), all checks passed</sub> |
| 2 buffer rebuffering | **VERIFIED**<br><sub>4/4 run(s), all checks passed (B4: no reference)</sub> | **FAILED**<br><sub>1 check failure(s): B1</sub> |
| 3 delivered bitrate | **VERIFIED**<br><sub>4/4 run(s), all checks passed</sub> | **VERIFIED**<br><sub>4/4 run(s), all checks passed</sub> |
| 4 switching | **VERIFIED**<br><sub>4/4 run(s), all checks passed</sub> | **VERIFIED**<br><sub>4/4 run(s), all checks passed</sub> |
| 5 frame rate | **VERIFIED**<br><sub>4/4 run(s), all checks passed</sub> | **VERIFIED**<br><sub>4/4 run(s), all checks passed</sub> |

Dropped frames are carried as raw counts only; throughput is not a QoE
metric and appears only in its own two charts and the cap check.

## Checks

| | count |
|---|---|
| PASS | 179 |
| **FAIL** | **1** |
| no reference (N/A) | 4 |

### Every failing check

| run | check | detail |
|---|---|---|
| youtube_6Mbps_50ms_pfifo_with_vimeo_trial1 | **B1** | max gap 2.00 s vs 2.0 s bar (mean 1.01 s) |

## Per-run results

| run | verdict | res | startup (ms) | rebuffers | video Mbps | audio Mbps | switches | fps |
|---|---|---|---|---|---|---|---|---|
| vimeo_1Mbps_50ms_pfifo_trial1 | OK | 240p | 10458.0 | 0 | 0.2958 | 0.2156 | 2 | 23.97 |
| vimeo_3Mbps_50ms_pfifo_trial1 | OK | 540p | 4294.0 | 1 | 1.1049 | 0.2156 | 4 | 23.88 |
| vimeo_6Mbps_50ms_pfifo_trial1 | OK | 720p | 2772.0 | 0 | 2.3043 | 0.196 | 4 | 23.93 |
| vimeo_6Mbps_50ms_pfifo_with_youtube_trial1 | OK | 540p | 7009.0 | 1 | 1.3596 | 0.196 | 5 | 22.64 |
| youtube_1Mbps_50ms_pfifo_trial1 | OK | 720p | 20701.0 | 0 | 0.5475 | 0.1253 | 2 | 25.0 |
| youtube_3Mbps_50ms_pfifo_trial1 | OK | 720p | 7800.0 | 0 | 0.5977 | 0.1179 | 0 | 25.0 |
| youtube_6Mbps_50ms_pfifo_with_vimeo_trial1 | OK | 480p | 6484.0 | 3 | 0.3785 | 0.1308 | 1 | 24.81 |
| youtube_6Mbps_50ms_pfifo_trial1 | OK | 480p | 5274.0 | 0 | 0.3168 | 0.1103 | 0 | 24.99 |

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

Built from commit `f62f493`, Chrome `Google Chrome 154.0.8037.57`, on `docker-vm-4`.

The collector deployed on the VM when these runs were collected is
byte-identical (sha256) to this commit's
`selenium_video_qoe/collect.py`, so the stamp above is checked, not
assumed. One caveat: that file was last written partway through the
collection session, after the three vimeo solo runs and before the
youtube solo runs and the concurrent cell. Every run in this release
carries the same per-sample instrumentation (`loop_ms`, `host_load1`),
so none predates the instrumented collector, but whether the three
vimeo solo runs used a byte-identical file is **unknown**.
