# Jaber's rules — evidence per rule

Every row is backed by a check that runs on every release run and fails the
build if it does not hold, or by a named file you can open. Where a rule is
only partly met, the gap is stated rather than rounded up.

Host for every release run: **vm-4, 8 vCPU, 7.9 GB RAM, Ubuntu 24.04.4
(kernel 6.8.0-147), Chrome 154.0.8037.57** inside the collector image
(Debian 13). Runs collected at 2 and 4 vCPU were a CPU diagnostic and are
**not** in the release.

| # | Rule | Status | Evidence |
|---|---|---|---|
| 1 | Throughput never above the cap, peak and average | **met** | Checks **J1** (peak ≤ cap × 1.05) and **J2** (mean ≤ cap) on every run. On a concurrent cell both apps are summed on a common 1 s grid *before* the comparison, so neither can hide behind the other. Measured peaks sit just under the cap: 2.998 on a 3 Mbps cap, 5.974 on 6 Mbps. |
| 2 | One pcap per run, split per app | **met** | **J3** plus the new **R4**. `pcap_manifest.csv` lists every capture with size and SHA-256. The split is by remote endpoint, and endpoints are now named from QUIC Initial packets as well as from DNS and TCP SNI — see "The attribution fix" below, which is why this row previously held for one concurrent cell and not the other. |
| 3 | Separate download and upload plots | **met** | **J4**. `download_throughput.png` and `upload_throughput.png` in every run folder. |
| 4 | Buffer level and resolution shown over time | **met** | `buffer.png` and `resolution.png` per run, drawn from the per-second samples. A stall that ended before the first sample has no position on an axis that starts at the first sample, so it is hatched at the left edge and counted in the caption rather than left looking as though it never happened. |
| 5 | NO_PLAYBACK flagged | **partly met** | The verdict exists and is asserted by **J6**; `shared/qoe.py` emits `no_playback` when the clock never advances. **Not automated:** a NO_PLAYBACK run is not re-run by itself. Auto-rerun was judged not cheap — it needs the batch runner to re-enter the load gate and renumber the trial — so it was not implemented. No release run has this verdict. |
| 6 | Multi-app cap is shared, not per app | **met** | The shared-cap check sums every app on a common 1 s grid before comparing to the cap. `summary.json` carries `link_shared` with the summed peak and mean and the app list. |
| 7 | Line graphs, fixed 0–11 Mbps axis, clipped to the playback window | **met** | **J5**. The throughput axis is fixed 0–11 regardless of data, the x-axis is clipped to the sampled window, one app per plot. |
| 8 | High-bitrate source | **not met for YouTube; met for Vimeo** — *and the reason is now pinned* | Vimeo reaches **1080p at 4.27 Mbps** on a 10 Mbps cap. YouTube never exceeds **~0.69 Mbps at 720p with 0 switches**, at 3, 6 *and* 10 Mbps. What is new is that this is **not** a pipeline limit: on the same runs the link delivered **2.44, 2.65 and 4.23 Mbps** respectively during the seconds it was transferring (`download_active`). The link and the collector can carry more; the clip (`dQw4w9WgXcQ`) has no higher rendition to give. **A higher-bitrate YouTube source is still needed before YouTube's bitrate numbers mean much**, but the shortfall is the source, not the measurement. |
| 9 | Runs on the lab VM | **met** | All release runs on vm-4; host recorded in every `summary.json` under `run_metadata`. |
| 10 | Reproducible | **met** | `run_stepb.py`, `run_cpuscale.py`, `run_matrix.py` collect; `make_release.py` rebuilds every chart and summary from the samples and asserts the charts against `summary.json`. Two builds from committed code are byte-identical — verified again after this round of changes, across every chart, summary, sample file and per-app pcap SHA-256. |
| 11 | Five uniform metrics | **met** | Startup delay, buffer level + rebuffering, delivered bitrate split video/audio, rendition switching with timestamp and direction, frame rendering rate. Produced on every release run of YouTube and Vimeo. How startup is defined, and the three things about it that have been misread, are written out in `README.md`. |
| 12 | Dropped frames raw only | **met, with a note** | The raw `dropped_video_frames` count is carried and is **not** an input to any of the five metrics. A convenience `dropped_pct` is also computed from it; no threshold or verdict uses either. |
| 13 | Throughput is not a QoE metric | **met** | Stored under the literal key `throughput_mbps_not_a_qoe_metric`, used only for the cap check and its own two charts. The rate the link achieved while actually delivering (`download_active`) is new in this round and lives under that same key for the same reason. |
| 14 | YouTube stats-for-nerds as ground truth | **met** | Check **B4** compares our buffer level against YouTube's own figure (MAE ≤ 1.0 s, r ≥ 0.90). Kept honest by `sfn_merged_into_generic = false` in every sample, so the reference never flows into the channel it validates. `N/A` on Vimeo, which publishes no such figure. |
| — | Cross-traffic profiles (CTP) | **out of scope** | Not part of this round, by agreement. |

## The attribution fix behind rule 2

The concurrent 3 Mbps cell shipped with YouTube's download chart empty and its
per-app pcap at 4.7 MB against the 13.3 MB the page itself reported receiving,
while every check on that run passed.

A capture names a media endpoint only by the TLS SNI it sees in the clear, and
YouTube's media rides QUIC. Both concurrent runs used the **same** server,
`198.189.66.13` (`rr2---sn-jxopj-n5oe.googlevideo.com`):

- in the **6 Mbps** run Chrome also opened a throwaway TCP/443 connection to it
  (7,973 bytes, 2 SYNs) whose ClientHello leaked the name, so its 14.3 MB of
  QUIC media was attributed correctly;
- in the **3 Mbps** run there were **no TCP packets to that address at all**,
  and no DNS answer for the name either, so the same 14.3 MB was bucketed as
  `other`.

Attribution was therefore correct only by luck. QUIC Initial packets are now
decrypted using the fixed, published salt of RFC 9001 §5.2 — no cooperation
from the endpoint is needed, and it is what any protocol analyser does — and
their ClientHello SNI read. On the 3 Mbps capture that lifts the share of bytes
mapped to a named app from **0.694 to 0.972**, and YouTube's download chart from
a 0.0004 Mbps mean to a 0.434 Mbps mean with a 2.998 Mbps peak.

**R4** is the check that would have caught it, and is pre-registered in
`validation/THRESHOLDS.md`: for each app, media-host download bytes in the
capture divided by that app's `cdp_received_bytes` must fall in
**[1.00, 1.25]**. Both sides count media hosts only, download only, using the
same suffix list. The floor is physics — the capture cannot carry less than
Chrome counted, and additionally sees per-packet framing Chrome never counts.
The broken run scored **0.000**; every run now measures **1.033–1.060**.

## Open, and not claimed as met

- The committed artifacts in this folder predate the attribution fix for the
  concurrent 3 Mbps cell. They are regenerated by re-running
  `make_release.py`; until that has run on the host that holds every source
  capture, the numbers in this folder for that one cell are the old ones.
- `pcap_manifest.csv` lists `vimeo_solo_10Mbps_50ms_pfifo.vimeo.pcap`
  (111,155,237 bytes) with its SHA-256, but that file is not in `pcaps/`. The
  rebuild reproduces it with exactly the listed SHA-256, so it is a packaging
  gap and not a missing measurement; whether a 111 MB pcap belongs in the repo
  is a separate decision.
