# Key findings

Every claim is labelled **measured**, **inferred** or **unknown**, with the
source of the number. Failures and reversals come first.

## 1. The Step 3b gate is NOT met — concurrent cells were not run

**Measured.** The byte-reference bands were fixed in `THRESHOLDS.md` before any
of this data existed. Across 11 YouTube runs, **7 pass all three checks and 4
fail**:

| run | CDP finished / MSE (A1) | CDP received / MSE (A2) | agreement (C1) |
|---|---|---|---|
| 0.5 Mbps | **0.9652 FAIL** | 1.0020 PASS | 3.67 % PASS |
| 1 Mbps | **0.8943 FAIL** | 1.0010 PASS | **10.67 % FAIL** |
| 1.5 Mbps | **0.9731 FAIL** | 1.0347 PASS | **5.95 % FAIL** |
| 3 Mbps (t10) | **0.9644 FAIL** | 1.0005 PASS | 3.61 % PASS |
| 3 Mbps (t21-t25) | 1.0008 PASS ×5 | 1.0007 PASS ×5 | 0.004 % PASS ×5 |
| 6 Mbps | 1.0005 PASS | 1.0005 PASS | 0.003 % PASS |
| 10 Mbps | 1.0006 PASS | 1.0005 PASS | 0.004 % PASS |

The band was **not** moved and no reference was swapped after the fact.

**Cause, measured:** the failures are bandwidth-ordered because
`Network.loadingFinished` only fires when a response completes, and at low
bandwidth requests are still in flight when the run ends. On the 1 Mbps run,
`finished + open = 9,449,868` against `received = 9,449,503` — a 365-byte
difference in 9.4 MB, so the entire 10.67 % disagreement is accounted for by
`cdp_open_at_end_bytes = 1,008,277`.

**What this does NOT license:** declaring received-bytes "the" reference now.
Choosing the reference that passes after seeing the data is exactly what the
pre-registration forbids. The honest position is that a future pre-registration
should define the reference as received-bytes, citing this evidence.

**Also measured:** the pcap reference remains unreliable. `pcap/MSE` reads
**0.9166** at 6 Mbps and 0.9907 at 3 Mbps — below 1.0, which is impossible for
a complete reference, because a single dominant peer does not capture a media
flow delivered over more than one IP.

## 2. The Resource Timing shortfall is explained

**Measured, and the test could have refuted it.** A Resource Timing entry is
only created when a response ends, so bytes on a response that never ends
should be missing from RT and present in `cdp_open_at_end_bytes`. If the
shortfall were real while open bytes were ~0, the explanation would be dead.

| run | RT shortfall vs MSE | `cdp_open_at_end_bytes` | verdict |
|---|---|---|---|
| YouTube 1 Mbps | 869,988 | 1,008,277 | open bytes **cover** it |
| YouTube 0.5 Mbps | 159,142 | 187,514 | open bytes **cover** it |

Earlier explanations for the same gap were **disproven**: it is not CORS
zeroing (`rt_size_zeroed_entries` = 0), not buffer eviction
(`rt_buffer_full_events` = 0 against a 3000-entry buffer), and not this probe's
own filter (which accounts for 74,523 B — **1.07 %** — of a 10.90 % gap).

## 3. Why the rendition-switch log was empty

**Measured.** `mse_mime_switch_log` records only `addSourceBuffer` and
`changeType`. On YouTube at 1 Mbps the declared MIME never changed and
`mse_mime_switches` was **0**, while the init segments the player appended show
the decoder being reconfigured four times:

| page time | init-segment codec | resolution |
|---|---|---|
| 19,833 ms | `av01.0.04M.08` | 854×480 |
| 50,634 ms | `av01.0.00M.08` | 426×240 |
| 55,713 ms | `av01.0.01M.08` | 640×360 |
| 115,183 ms | `av01.0.04M.08` | 854×480 |

The resolution series independently shows 480 → 240 → 360 → 480. YouTube
changes rendition **without ever calling `changeType`**, so the log was
watching an API that is never invoked. The init segment is the generic witness,
because the decoder is configured from it.

## 4. Rendition at 3 Mbps is driven by player size — a reversal

**Measured, and it corrects an earlier claim of mine.** I previously reported
the 3 Mbps rendition as bimodal "run-to-run variance at an identical box". With
the browser window enforced at 1280×720, five trials give:

| measured element box | resolution | codec | delivered video Mbps |
|---|---|---|---|
| 867×488 (4 trials) | 480p | `av01.0.04M.08` | 0.299 – 0.311 |
| 812×457 (1 trial) | 480p | `av01.0.04M.08` | 0.318 |

**Resolution identical 5/5**, and the bitrate spread falls from **35.6 % to
6.0 %**. The unconstrained cell, at box 1331×749, served 720p VP9 instead. So
player box is the dominant lever, not chance — my earlier framing was
incomplete.

Two consequences:
- The claim "YouTube switches AV1 → VP9 between 1.5 and 3 Mbps" **stays
  retracted**, and now has a mechanism: at 3 Mbps you get AV1 480p or VP9 720p
  depending on player size, not on bandwidth.
- **Enforcing the window does not fully pin the element box** — one trial
  measured 812×457 under the same 1280×720 window. Window control is partial.

## 5. Vimeo at 10 Mbps is decode-limited, not network-limited

**Measured, reproduced on two independent runs, and refutable.** If the higher
rendition had been reached with few drops, the host-limit reading would be
dead.

| rendition | frame drop rate, run A | run B |
|---|---|---|
| 1440×1080 | **13.43 %** | **13.71 %** |
| 960×720 | 2.24 % | 2.67 % |

Buffer stayed full throughout the 1440×1080 samples (mean 23.1–23.4 s, min
15.4 s) and delivered bitrate was 30.9 % of the 10 Mbps cap. Bytes arrived;
frames were dropped anyway; the player then abandoned the rendition. A network
limit requires delivery near the cap and a draining buffer — neither held.

## 6. Tubi produced no player because the title had been withdrawn

**Measured, with a disproving test run.** The hard-coded title returned HTTP
200 with the correct page `<title>`, but the body read **CONTENT UNAVAILABLE**,
the primary action was "Remind Me", and **zero** `<video>` elements and zero
MediaSource constructions were ever created. None of the suspected causes were
present: no consent gate, login wall, age gate, geo block, bot wall or ad text.

On a title that is in the catalogue, a `<video>` appears within 5 s and
playback advances 0 → 10.07 → 25.50 → 45.83 s at `readyState` 4. Control held:
the retired title reports CONTENT UNAVAILABLE and four current titles do not.

Tubi still lacks two metrics — see the app × metric table. `startup_delay_ms`
and `rendered_fps` are **null**, because `requestVideoFrameCallback` never
fired even though `totalVideoFrames` climbed 39 → 4335. Tubi swaps its video
element and the callback does not follow. **Unknown** whether re-attaching the
callback on element replacement would recover them; not implemented.

## 7. Two fabricated zeros were removed

**Measured.** `rendered_fps` was computing `(0-0)/elapsed = 0.0` and reporting
it with a real-looking basis whenever the presented-frame counter never
advanced — stating that a video rendered no frames when 4,335 were decoded.
It now returns `null` with the reason. This is the same defect class as the
Twitch `delivered_video_bitrate_mbps = 0.0` fixed earlier; Twitch now correctly
reports `null`, because its MSE runs inside Workers and is structurally
unreachable.

Separately, a counter **reset** mid-run (YouTube 1.5 Mbps: decoded frames ran
997 → 16 when the player swapped elements) made the rate negative. Positive
deltas are now summed across resets, which recovered a correct 24.21 fps for
that run.

## 8. The instrument disturbed the measurement, and was fixed

**Measured, and it implicates my own change rather than exonerating it.**
Draining Chrome's CDP performance log every second is a synchronous WebDriver
round-trip over thousands of events, and it stalled the sample loop:

| | runs | median max-gap | worst gap | intervals > 2 s |
|---|---|---|---|---|
| with per-second draining | 17 | 2.35 s | **17.39 s** | 22 / 2765 = **0.80 %** |
| without | 161 | 1.31 s | 4.19 s | 29 / 12317 = 0.24 % |

Draining is now throttled to every 5th sample. **The data in this release was
collected with per-second draining**, so 12 of its 23 runs carry at least one
interval longer than 2× the nominal 1 s cadence; each is listed in
`validation/VERIFICATION_REPORT.md`. The event-based metrics (startup,
rebuffering, switches, byte totals) are unaffected — only the sampled series
have holes.

## 9. What events a chart cannot show

**Measured.** The probe is installed at document-start, but the per-second
series only begins after the synchronized play trigger, and YouTube autoplays
before that. On the 1 Mbps run `DOMContentLoaded` was 41.1 s, the first
presented frame 20.5 s, and the first QoE sample 52.4 s — so a real 18.1 s
rebuffer at `currentTime` 11.9 s fell entirely to the left of every chart.
`qoe_buffer.png` carries a caption saying so. See `MEASUREMENT_WINDOW.md` for
which metrics are evented and which are sampled.
