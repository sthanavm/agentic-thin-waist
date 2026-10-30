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

---

# Per-app capture completeness — R4, pre-registered

Added after the release round was collected, and stated here **before** being
run against any release run, because it tests a property of the *tooling*
rather than a property of the network: whether the per-app split of a shared
capture actually contains the app's traffic. No existing band is changed and
no run is re-judged on anything but R4.

## Why it is needed

The concurrent 3 Mbps cell shipped with YouTube's download chart empty and its
per-app pcap at 4.7 MB against the 13.3 MB the page itself reported receiving.
Every other check on that run passed. The cause was that a capture names a
media endpoint only by the TLS SNI it sees in the clear, and YouTube's media
rides QUIC:

- both concurrent runs used the **same** server, 198.189.66.13
  (`rr2---sn-jxopj-n5oe.googlevideo.com`);
- in the 6 Mbps run Chrome also opened a throwaway TCP/443 connection to it
  (7,973 bytes, 2 SYNs) whose ClientHello leaked the name, so its 14.3 MB of
  QUIC media was attributed correctly;
- in the 3 Mbps run there were **no TCP packets to that address at all**, and
  no DNS answer for the name either, so the same 14.3 MB was bucketed as
  `other`.

Attribution was therefore correct only by luck. QUIC Initial packets are now
decrypted with the published fixed salt (RFC 9001 §5.2) and their ClientHello
SNI read, which is what a protocol analyser does and needs no cooperation from
the endpoint, so the name is available from the capture alone.

## The band

**R4 passes if, for each app, the media-host download bytes in that run's
capture divided by that app's `cdp_received_bytes` falls in [1.00, 1.25].**

Both sides count the same thing: media hosts only (the identical suffix list
drives the pcap tally and Chrome's own ledger), download only.

- **Floor 1.00 is physics.** Every byte Chrome counted crossed the wire inside
  this app's media flow, and the capture additionally sees per-packet framing
  that `encodedDataLength` never counts. Below 1.00 the capture is missing part
  of the flow, which is exactly the failure above — it would have scored 0.000.
- **Ceiling 1.25** allows that framing — 14 B Ethernet + 20 B IPv4 + 8 B UDP +
  about 26 B of QUIC header and AEAD tag over a ~1432 B payload, so ~4.8 % on
  a full-size frame — plus room for retransmission. Above it, bytes from some
  other flow have been folded in, which is the double-counting failure the
  attributor's disjoint-bucket rule exists to prevent.

Set from that arithmetic. It is not a fit: the ceiling is roughly four times
the expected overhead.

- A run that produces bytes but misses this band is **not** released as a
  verified per-app capture, and its pcap is not cited as that app's traffic.
- R4 is reported N/A, never PASS, for a run with no capture or no CDP ledger
  (the ledger only exists for apps with a media-host list).
- This is a different quantity from **T1**, which divides the *whole* per-app
  pcap by MSE appended bytes and so legitimately runs higher; both stand.

## Also reported from now on, not bands

Two figures are added alongside the existing ones. No check uses either, and
no existing derivation changed.

**The rate at which the link actually delivered**, over the seconds in which it
delivered anything — `throughput_mbps_not_a_qoe_metric.download_active`,
measured from the capture with the existing 0.05 Mbps idle floor. The window
mean is dragged down by a player idling on a full buffer: YouTube on a 10 Mbps
cap averages 0.87 Mbps across the window but **4.23 Mbps across the 37 seconds
it was transferring**. This is throughput, so it sits under the throughput key
and is not a QoE metric.

This figure is deliberately taken from the capture and **not** from MSE append
intervals. An append is instantaneous, so the intervals in which the byte
counter moved are not wire time; dividing by them gave Vimeo **24.5 Mbps on a
10 Mbps link**, which is an append rate bounded by nothing physical and would
have reproduced exactly the cap-violation misreading that the bitrate chart's
annotation exists to prevent.

**Delivered bitrate per second of media** —
`metrics.3_delivered_bitrate.video_mbps_per_media_second` — appended bytes over
the span of media those bytes cover, rather than over the wall-clock window.
`video_mbps` divides by the window, which equals the encoded bitrate only while
the buffer is in steady state. Across all 16 release runs the two agree within
10 %, which is the evidence that the reported figure is a sound reading of the
rendition's bitrate rather than an artefact of how long the player ran ahead.
