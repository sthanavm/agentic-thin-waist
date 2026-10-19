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
| 1 | Throughput never above the cap, peak and average | **met** | Checks **J1** (peak ≤ cap × 1.05) and **J2** (mean ≤ cap) on every run. Measured peaks sit just under the cap, e.g. 2.96–3.01 on a 3 Mbps cap and 5.92–5.98 on 6 Mbps. |
| 2 | One pcap per run, split per app | **met** | **J3**. `pcap_manifest.csv` lists every capture with size and SHA-256; the 3-app run yields three per-app pcaps from one capture. |
| 3 | Separate download and upload plots | **met** | **J4**. `download_throughput.png` and `upload_throughput.png` in every run folder. |
| 4 | Buffer level and resolution shown over time | **met** | `buffer.png` and `resolution.png` per run, drawn from the per-second samples. |
| 5 | NO_PLAYBACK flagged | **partly met** | The verdict exists and is asserted by **J6**; `shared/qoe.py` emits `no_playback` when the clock never advances. **Not automated:** a NO_PLAYBACK run is not re-run by itself. Auto-rerun was judged not cheap — it needs the batch runner to re-enter the load gate and renumber the trial — so it was not implemented. No release run has this verdict. |
| 6 | Multi-app cap is shared, not per app | **met** | The shared-cap check sums every app on a common 1 s grid **before** comparing to the cap, so neither app can hide behind the other. `summary.json` carries `link_shared` with the summed peak and mean and the app list. |
| 7 | Line graphs, fixed 0–11 Mbps axis, clipped to the playback window | **met** | **J5**. Verified by reading charts: throughput axis fixed 0–11 regardless of data, x-axis clipped to the sampled window, one app per plot. |
| 8 | High-bitrate source | **not met for YouTube; met for Vimeo** | Vimeo reaches **1080p at 4.27 Mbps** on a 10 Mbps cap. YouTube never exceeds **~0.70 Mbps at 720p with 0 switches**, at 3, 6 *and* 10 Mbps — the delivered bitrate does not rise with the cap. That is the clip (`dQw4w9WgXcQ`), not the player or the link. **A higher-bitrate YouTube source is needed before YouTube's bitrate numbers mean much.** |
| 9 | Runs on the lab VM | **met** | All release runs on vm-4; host recorded in every `summary.json` under `run_metadata`. |
| 10 | Reproducible | **met** | `run_stepb.py`, `run_cpuscale.py`, `run_matrix.py` collect; `make_release.py` rebuilds every chart and summary from the samples and asserts the charts against `summary.json`. Two builds from committed code are byte-identical. |
| 11 | Five uniform metrics | **met** | Startup delay, buffer level + rebuffering, delivered bitrate split video/audio, rendition switching with timestamp and direction, frame rendering rate. Produced on every release run of YouTube and Vimeo. |
| 12 | Dropped frames raw only | **met, with a note** | The raw `dropped_video_frames` count is carried and is **not** an input to any of the five metrics. A convenience `dropped_pct` is also computed from it; no threshold or verdict uses either. |
| 13 | Throughput is not a QoE metric | **met** | Stored under the literal key `throughput_mbps_not_a_qoe_metric`, used only for the cap check and its own two charts. |
| 14 | YouTube stats-for-nerds as ground truth | **met** | Check **B4** compares our buffer level against YouTube's own figure (MAE ≤ 1.0 s, r ≥ 0.90). Kept honest by `sfn_merged_into_generic = false` in every sample, so the reference never flows into the channel it validates. `N/A` on Vimeo, which publishes no such figure. |
| — | Cross-traffic profiles (CTP) | **out of scope** | Not part of this round, by agreement. |
