# Step B, first pass: latency sweep — pre-registered plan and bands

Written and committed **before any Step B data was collected**, so nothing
below can have been chosen to fit a result. If a check fails it is reported as
a failure; no band here is moved afterwards, and no run is dropped after the
fact.

## What is being swept

YouTube and Vimeo, **solo only** — one app per run. No concurrent cells in this
pass, because the concurrent cell is not measurable on this host: a single
competing CPU-bound process is enough to stall YouTube's page for 5-30 s, and
that is a property of the 2-vCPU measurement host rather than of the network.
That work is parked behind a separate pre-registered plan on a larger host.

| | |
|---|---|
| apps | youtube, vimeo (solo, one app per run) |
| bandwidth | 6 Mbps |
| **latency swept** | **20 ms, 50 ms, 150 ms** (one-way, applied upstream) |
| **baseline latency** | **50 ms** — the Step A value, carried forward unchanged, so the 50 ms cells are directly comparable with the committed Step A runs |
| **AQM** | **pfifo** throughout, unchanged from Step A. The AQM sweep is a later pass and is not started here. |
| CCA / loss | cubic / 0 % |
| duration | 180 s |
| trials | 2 per cell |
| cells | 2 apps x 3 latencies = 6 cells, **12 runs** |
| sampling interval | 1.0 s |

One run at a time, each behind the host-load gate (load1 <= 4.0 held for 180 s)
and a 2 GB free-space guard.

## Pass bands

Every Step A check applies unchanged. They are restated here so this note is
self-contained, and **the 2.0 s B1 bar is not changed**:

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
| J3,J4 | a pcap per run and per app; download and upload plotted separately | files present |
| J5,J6,J7 | fixed 0-11 Mbps charts clipped to playback; playback verdict; chart numbers equal summary | — |

### One band added for this pass

**L1 — the shaped latency was actually applied.** The substrate reports the
latency it set and verifies it; a run whose applied latency differs from the
requested value by more than **10 %** is reported as a failed cell, not quietly
analysed. Without this, a sweep that silently failed to re-shape would look
like "latency has no effect".

## What counts as a result, decided now

- A metric is **VERIFIED for an app at a latency** only if, on **every** run of
  that cell, it was produced and every applicable check passed. One failure
  makes the whole cell FAILED. This is the Step A definition, unchanged.
- **Direction is not pre-judged.** Startup delay and rebuffering are expected
  to worsen with latency and delivered bitrate to fall, but no band encodes
  that, and a flat or reversed trend is reported as measured.
- **Two trials give no variance estimate.** A difference between latencies is
  reported as observed, not as significant, and the 20 ms vs 150 ms comparison
  rests on 2 runs per cell.
- Mixed results within a cell are reported as mixed. The better trial is not
  selected.

## Not in this pass

The AQM sweep, any concurrent cell, and any change to the committed Step A
results or to the 2.0 s B1 bar.

---

# Release round on the 8-vCPU host — pre-registered

Written and committed **before** any run in this round. Every band below
already existed and is restated unchanged; nothing here is new except the
Twitch pcap band, which is stated for the first time and fixed now.
**The 2.0 s B1 bar is not changed.**

## What is collected

Every release cell comes from vm-4 at **8 vCPU**. Solo YouTube and Vimeo at
1, 3, 6 and 10 Mbps (50 ms); 6 Mbps at 20 ms and 150 ms; plus the concurrent
3 and 6 Mbps cells and the 3-app run already collected on this host. Two
trials per new cell, one run at a time, load gate and 2 GB disk guard,
health check after each batch's first run, completion marker on the VM.

Runs at 2 and 4 vCPU are **excluded from the release**; they were a CPU
diagnostic and are not comparable.

## Bands

All existing bands apply unchanged: S1-S3, B1-B4, R1-R3, W1-W2, F1-F3,
J1-J7, L1, and the shared-cap check summed on a 1 s grid.

### New: T1 — Twitch delivered bitrate against the capture

Twitch has no stats-for-nerds, so if the worker-side hook starts producing
bytes, the only independent reference is the packet capture. Vimeo is the
model: on this pipeline Vimeo's media-host pcap bytes divided by its MSE
appended bytes has run **1.09 to 1.18**.

**T1 passes if Twitch's per-app pcap bytes divided by its MSE appended bytes
falls in [1.00, 1.35].** The floor is physics: the capture cannot carry less
than the player appended. The ceiling is Vimeo's observed worst (1.18) plus
headroom for Twitch carrying more non-media traffic on the same hosts.

A Twitch run that produces bytes but misses this band is **not** released as
verified bitrate. R2 and R3 are **not** weakened: if the hook yields nothing,
Twitch ships as a labelled 4/5 limit with no Twitch folders in the release.

## Decided in advance

- A failing run is kept out of the release and recorded in
  `sweep_matrix.csv` with its failure, not silently dropped.
- A root cause is only named if a test that could have disproved it was run.
- Mixed results are reported as mixed; the better trial is never selected.
- Cross-traffic profiles (CTP) are **out of scope** for this round.
