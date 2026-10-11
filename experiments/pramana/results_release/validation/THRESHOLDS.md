# Pre-registered checks — YouTube and Vimeo, Step A

Written and committed **before any of the runs it governs were started**.
Nothing in this file may be edited once the first run completes. If a check
fails, it is reported as FAILED; no band is widened, no reference is swapped,
and no run is excluded after the fact.

## What "VERIFIED" means — one definition, used everywhere

A metric is **VERIFIED for an app** only if, on **every** run of that app in
this release, BOTH hold:

1. the metric was **produced** (non-null), and
2. **every pre-registered check that applies to it passed**.

- **FAILED** — the metric was produced, but at least one applicable check
  failed on at least one run.
- **UNKNOWN** — the metric was not produced on at least one run, so no check
  could be evaluated.

This exists because the previous release labelled delivered bitrate
"VERIFIED 11/11" while 4 of those 11 runs failed the pre-registered bitrate
band. Under that wording "11/11" counted only *production*, silently ignoring
the checks. Condition 2 above makes that impossible: a band failure forces
FAILED for the whole app, not a footnote.

## Run matrix

| | |
|---|---|
| apps | YouTube, Vimeo — **one app per run**, never combined |
| bandwidth | 1, 3, 6 Mbps |
| latency | **50 ms**, the baseline used for every solo run in this project so far |
| AQM / CCA / loss | pfifo / cubic / 0 % |
| duration | 180 s |
| trials | 2 per cell |
| total | **12 runs**, executed one at a time, never in parallel |

Latency and AQM sweeps are Step B and are deliberately out of scope here, so
bandwidth and app are the only variables.

## Checks, by metric

`MSE` below means `mse_video_bytes + mse_audio_bytes` for the run.

### 1. Startup delay

| id | check | bar |
|---|---|---|
| S1 | produced and strictly positive | `0 < startup_delay_ms` |
| S2 | within the session | `startup_delay_ms < duration_s x 1000` |
| S3 | pcap cross-check is arithmetically consistent | `(navigationStart -> first frame) - (first media packet -> first frame)` equals the first media packet's page offset to within **50 ms** |

S3 is a self-consistency identity, not a tolerance on a measurement: the three
quantities are derived from one clock mapping and must add up. A mismatch means
the mapping is wrong.

### 2. Buffer level and rebuffering

| id | check | bar |
|---|---|---|
| B1 | buffer sampled at least once per second | **every** actual inter-sample gap <= **2.0 s**, i.e. 2x the 1.0 s nominal interval. A gap longer than that means a whole sample period was missed |
| B2 | buffer level is physical | `buffer_level_secs.min >= 0` |
| B3 | rebuffer count is produced | `rebuffer_events` is not null |
| B4 | **YouTube only** — agrees with the vendor's own figure | mean absolute error vs `sfn_buffer_health_seconds` <= **1.0 s** and Pearson r >= **0.90** |

B4 has no Vimeo equivalent because Vimeo publishes no vendor buffer figure.
Its absence is recorded as "no reference", never as a pass.

### 3. Delivered bitrate (video/audio split)

The reference is **Chrome's own Network domain**, because it names the host
rather than guessing which peer IP carried the media.

| id | check | bar |
|---|---|---|
| R1 | `cdp_received_bytes / MSE` | **[1.00, 1.15]** |
| R2 | video and audio are reported separately and both are non-null | — |
| R3 | video bytes are non-zero | `mse_video_bytes > 0` |

Floor 1.00: the network cannot deliver fewer body bytes than the player
appended. Ceiling 1.15: response headers plus any non-media request to the same
media host; header overhead on multi-hundred-KB segments is small.

**Why received-bytes and not finished-bytes.** `Network.loadingFinished` fires
only when a response completes, so a request still in flight when the run ends
contributes nothing to it. That was measured in the previous release: at low
bandwidth `cdp_finished/MSE` fell to 0.8943 while `cdp_received/MSE` was 1.0010,
and `finished + open` differed from `received` by 365 bytes in 9.4 MB. This is
prior knowledge being used to choose the right instrument **before** collecting
new data, not a band moved after seeing it. `cdp_finished_bytes` and
`cdp_open_at_end_bytes` are still recorded, as diagnostics only.

**pcap is reported, never a judge.** The same release measured `pcap/MSE` at
**0.9166** on one run — below 1.0, which a complete reference cannot produce,
because a single dominant peer misses media delivered over more than one IP.

### 4. Rendition switching

| id | check | bar |
|---|---|---|
| W1 | `switch_count` produced, with a timestamp and direction per event | — |
| W2 | the switches drawn on the chart do not exceed the summary's count | `drawn <= switch_count` (resolution steps cannot see a same-height codec change) |

### 5. Frame rendering rate

| id | check | bar |
|---|---|---|
| F1 | produced and positive | `rendered_fps > 0` |
| F2 | cannot exceed decoding | `rendered_fps <= decoded_fps x 1.05` |
| F3 | the chart's time-weighted rate equals the summary | within **10 %** or 1.0 fps, whichever is larger |

A presented-frame counter that never advanced must report `null`, never 0.0.

## Jaber's per-run rules, checked mechanically

| id | check | bar |
|---|---|---|
| J1 | download throughput peak within the cap | `peak <= cap x 1.05` (5 % for 1-second bin edges) |
| J2 | download throughput mean within the cap | `mean <= cap`, no tolerance |
| J3 | a pcap exists for the run, and a per-app pcap is written | both present, SHA-256 recorded |
| J4 | download and upload are separate plots | both PNGs present |
| J5 | throughput y-axis fixed 0-11 Mbps, line (not filled), clipped to the playback window | — |
| J6 | playback verdict recorded | `OK` or `NO_PLAYBACK`; a NO_PLAYBACK run is reported, not silently dropped |
| J7 | chart numbers equal `summary.json` | buffer max and fps compared exactly |

## Failure procedure

A failing check is reported as FAILED in the README table. If a failure is
traced to a defect, the fix is made and **that cell is re-run**; the failure and
the re-run are both recorded. Re-running is allowed; re-labelling is not.

---

# Addendum: pre-run host-load gate

Written and committed **before the runs it governs were started**.

## Why

This VM is shared and has 2 vCPUs. Another workload on it (`substrate-worker`)
keeps about 160 Chrome processes alive, and when they are busy the run queue
goes far above what two cores can serve. Measured consequences:

- The sampler's page-script call is synchronous, so it waits on the renderer.
  One call exceeded Selenium's timeout and cost **39.1 s**, destroying roughly
  forty samples.
- The runs themselves degrade. The same YouTube 3 Mbps cell gave **720p with 0
  rebuffers** at load ~3 and **480p with 6-10 rebuffers** at load ~10.

So host load is not a nuisance to be averaged away; it changes the measurement.
It is now gated before a run and recorded with every run.

## The gate

Before each cell:

1. Sample `/proc/loadavg` 1-minute load every **15 s**.
2. Start the run once load1 has been **≤ 4.0 for 3 consecutive minutes**
   (12 consecutive samples).
3. If that has not happened within **20 minutes**, start the run anyway and
   mark it `host_load_gate: "timeout"` — a **high-load run**, kept and
   labelled, never silently discarded.

**Why 4.0.** The box has 2 cores. The collector needs roughly one core for
Chrome plus a little for the sampler, so for page-script calls to return
promptly the total runnable count wants to be near or below the core count
plus our own demand. The measured idle baseline with no collector running is
**load1 ≈ 3.0**, entirely from the other workload, so 4.0 is reachable at idle
while still excluding the 5.5-13.4 range in which the failures occurred. The
threshold is chosen from the machine's capacity and its measured idle floor,
not from the results of any run judged against it.

## What is recorded, for every run

In the run metadata, so a reader can see the conditions rather than infer them:

| field | meaning |
|---|---|
| `host_load_gate` | `passed` or `timeout` |
| `host_load_gate_wait_s` | how long the gate waited before starting |
| `load1_at_start` | 1-minute load immediately before the run |
| `load1_median`, `load1_max` | across the run's own samples |
| `chrome_procs_at_start` | count of `chrome` **and** `chrome-headless` processes, since the other workload's are named the latter |

A run with `host_load_gate: "timeout"` is reported as a high-load run in the
results table and is **not** compared against gated runs without saying so.

## What this gate does NOT do

It does not make a failing check pass. B1 and every other check are evaluated
exactly as written above, on gated and high-load runs alike.
