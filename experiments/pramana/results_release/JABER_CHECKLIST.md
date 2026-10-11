# Jaber's rules — status for this release

Scope: YouTube and Vimeo, Step A. Statuses are **met**, **partly met**,
**not met**, **not exercised**. Where a rule is not met, it says so plainly.

Evidence lives in each run's `summary.json` under `checks`, and every failing
check is listed in `README.md`.

| # | rule | status | evidence |
|---|---|---|---|
| 1 | Throughput never exceeds the shaped cap (peak and average) | **met** | checks `J1` (peak, with a 5 % allowance for 1-second bin edges) and `J2` (mean, no allowance) on every run |
| 2 | Save a pcap for every run; never push them | **met** | one `capture.pcap` per run, archived off the repo; `pcap_manifest.csv` carries size and SHA-256. No pcap is in the branch |
| 3 | Separate download and upload plots; video is download only | **met** | `download_throughput.png` and `upload_throughput.png` per run, check `J4`. Upload shows only ACK-level traffic, which is what "video is download only" should look like |
| 4 | Show buffer level | **met** | `buffer.png`, from `buffered.end - currentTime` sampled once a second, with rebuffer spans shaded |
| 5 | Show resolution over time | **met** | `resolution.png`, a step plot |
| 6 | Flag NO_PLAYBACK and regenerate | **partly met** | the verdict is computed, written to `summary.json` and checked (`J6`). Automatic re-running is **not** wired in — a flagged run is reported, not silently replaced |
| 7 | With several apps the cap is the shared total | **not exercised** | every run here is a single app, so there is nothing to share. Not demonstrated |
| 8 | One pcap per app, split by host/IP; one plot per app | **met** | a real per-app pcap is written per run (check `J3`), containing only packets to or from IPs attributed to that app. Charts are one app per file; no chart mixes YouTube and Vimeo |
| 9 | Cross-traffic profiles — state that they are not implemented | **met (as a disclosure)** | stated in `README.md` and in `experiments/pramana/CLAUDE.md`. Nothing is simulated and no profile is claimed |
| 10 | High-bitrate source so quality differences are visible | **met** | both ladders move with bandwidth — resolution and delivered bitrate both change across 1, 3 and 6 Mbps, which is the observable the rule asks for |
| 11 | Run everything on the lab VM | **met** | every run executed on `docker-vm-4`; each `summary.json` records the VM name and the on-VM run directory |
| 12 | Everything reproducible | **met** | `run_matrix.py` is the sweep, `make_release.py` rebuilds the package, commands are in `README.md`, and commit, Chrome version and shaping parameters are in each run's `run_metadata` |
| 13 | Cross-validate an anomaly before calling it a finding | **met** | two trials per cell. Earlier anomalies were cross-validated before being reported, and one claim (AV1 to VP9 between 1.5 and 3 Mbps) was **retracted** when repeats refuted it |

## Graph format

| requirement | status |
|---|---|
| Line graphs, not filled | **met** — `plot`/`step` only, no `fill_between` anywhere in `make_release.py` |
| Fixed 0 to 11 Mbps on throughput | **met** — one constant applied to both direction charts |
| Clipped to the playback window | **met** — x-axis spans first to last QoE sample |
| Single-metric charts per run | **met** — eight: two throughput plus buffer, resolution, bitrate, switches, fps, startup |

## The five metrics

| metric | how it is obtained, the same way on both apps |
|---|---|
| 1 Startup delay | first `requestVideoFrameCallback` presentation time, placed on the page timeline; reported separately from the initial buffer fill |
| 2 Buffer level and rebuffering | `buffered.end - currentTime` once a second; rebuffers from `waiting` to `playing` media events, with the initial fill excluded because that is startup, not an interruption |
| 3 Delivered bitrate, video and audio split | `SourceBuffer.appendBuffer` byte totals keyed by MIME type |
| 4 Rendition switching | resolution transitions with direction, plus a decoder-configuration count taken from init segments that does not depend on the site announcing the change |
| 5 Frame rendering rate | `presentedFrames` — frames actually composited, not merely decoded. A counter that never advanced reports null, never 0 |

Dropped frames are raw counts only. Throughput appears only in its own two
charts and the cap check, never inside a metric.

## Known gaps

- Rule 6's automatic re-run is not implemented.
- Rule 7 is untested because no multi-app run is included.
- Cross-traffic profiles remain unimplemented, pending Jaber's pointer.
- Latency and AQM are fixed at 50 ms and pfifo; sweeping them is Step B.
