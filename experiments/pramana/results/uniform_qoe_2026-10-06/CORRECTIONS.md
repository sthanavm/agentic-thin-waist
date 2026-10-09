# Corrections to this directory's README, 2026-10-09

The README was written on 2026-10-08 from one trial per rung. A verification
pass the next day overturned several of its claims. Rather than quietly edit
the numbers, each change is recorded here with what was claimed, what is
measured now, and how it was measured. The README's own text is corrected in
place only where it stated something now known to be false.

Every claim below is labelled **measured** (it came out of a run or a
capture), **inferred** (it follows from measured things but was not itself
measured), or **unknown** (candidate explanations exist and none is proven).

## 1. The delivered-bitrate check does not fail

**Claimed:** "FAIL 6/6", ratio 1.29-2.24 against the pre-committed band
[1.00, 1.25], with the failure explained as "pcap-per-app includes the whole
page load" and, at 3/6/10 Mbps, a "QUIC/HTTP3 attribution gap".

**Measured now:** against bytes to and from the dominant media peer IP -- which
needs no hostname at all -- the ratio is **1.056-1.092 on all six rungs**,
inside the band. A seventh, independent run reproduces it at **1.119**. The
metric was never wrong; the reference was being computed from a hostname-string
filter that returned 0.00 whenever the media flow had no name.

Three of my own explanations for the original failure are **disproven**:

- *"It is a QUIC gap."* QUIC (UDP/443) carries the media on **all six** rungs,
  including the three that attributed correctly (12.17 / 25.47 / 25.03 /
  25.35 MB over UDP against ~4.8 MB over TCP; QUIC Initial packets 257 / 247 /
  213 / 244). QUIC cannot explain a difference between rungs that all use it.
  **Measured**, `diag_transport.py`.
- *"The bytes are missing."* They are not. The attributor's own log reads
  `adopted 1 unnamed flow(s), 22.54 MB, by connection affinity:
  198.189.66.13 -> youtube:prefix/24`. The bytes were attributed; they just
  carried no hostname for a name-based filter to match. **Measured.**
- *"No googlevideo DNS record appears in any capture, so this is a DNS
  visibility problem."* The count of zero was right but my parser was wrong --
  it read each answer record's **owner** name instead of the **queried** name,
  which a CNAME chain makes different. Fixed in `diag_attribution.py`. The
  count is still zero, which matters for a different reason (see below).

**Still unknown:** why host attribution named `googlevideo` at 0.5 / 1 / 1.5
Mbps and not at 3 / 6 / 10. What is now **measured** is the naming channel:
the media peer is named from **TLS SNI in a TCP ClientHello**, never from DNS
(0 DNS answers, 2 SNI entries; the media peer 198.189.66.13 resolves only via
SNI). So attribution works only when Chrome happened to open a TCP+TLS
connection to that host alongside QUIC. Whether that is what differs between
the rungs is a **candidate, not a finding** -- the test (ClientHellos to the
media peer, per rung) is in `diag_spread.py`.

## 2. Startup: the old ~1.0 s figure was an artifact

**Claimed:** startup reported without distinguishing which quantity it was.

**Measured now:** three different quantities existed under one name.

| quantity | YouTube 0.5 -> 10 Mbps | Vimeo 0.5 -> 10 Mbps |
|---|---|---|
| legacy `video_startup_time_ms` | 1037.7 -> 1380.4 ms | 1087.1 -> 1077.0 ms |
| `startup_delay_ms` (first presented frame) | 42,645 -> 3,347 ms | 21,231 -> 2,786 ms |
| `initial_buffering_ms` (buffer fill) | 6,126 -> 676 ms | 10,209 -> 603 ms |

The legacy figure sits in a 1,036-2,086 ms band across a 20x range of
bandwidth while the real startup moves by 7.6x. It is floored at one sampling
interval by construction: it is the timestamp of the second sample in the first
pair where `currentTime` advanced by more than 0.02 s, so at 1 Hz sampling it
can never report under ~1 s. **It does not measure startup.** The two figures
were never in conflict; they measure different things. **Measured.**

Exact start and end points, since "startup" is otherwise ambiguous:

- `startup_delay_ms` = `presentationTime` of the first
  `requestVideoFrameCallback` minus `S.t0`, where `S.t0` is
  `performance.now()` **at probe install** (document-start). It is therefore
  measured from probe install, not from `navigationStart`; the offset is
  `probe_t0_page_ms` (697 ms on the run below). An earlier version of this note
  treated it as navigationStart-relative, which understated it.
- `initial_buffering_ms` = total duration of `waiting` spans that occurred
  while `currentTime` was still 0.

Cross-checked against the capture on one run (YouTube 1 Mbps, trial 1):

| definition | value |
|---|---|
| probe install -> first presented frame | 19,779 ms |
| navigationStart -> first presented frame | 20,476 ms |
| first media-peer packet -> first presented frame | 14,368 ms |
| first media packet, at page time | +6,108 ms |

20,476 - 14,368 = 6,108, which is exactly the first media packet's page
offset. That internal consistency is what makes the clock mapping
trustworthy. The earlier cross-check was **invalid**: it took the first
media-peer packet anywhere in the capture, and capture starts 14.5 s before
navigation.

**Limit:** the twelve ladder runs have `probe_t0_page_ms = None` -- they
predate the page-clock fields -- so for those runs neither the navigationStart
conversion nor the pcap cross-check can be done at all. Both exist only for
runs made after 2026-10-09.

## 3. The rebuffer count was inflated by exactly one, everywhere

**Measured:** every run's first `waiting` span has `currentTime == 0`. That is
the player filling its buffer before playback starts, not an interruption of
playback, and the HTML spec defines `waiting` as "playback has stopped because
of a temporary lack of data". Counting it made every rebuffer figure in the
README one too high.

After the fix YouTube agrees with `sfn_player_state == 3` episodes **exactly
6/6** (1, 1, 0, 0, 0, 0), where before it was off by one on all six rungs.
The initial fill is now reported separately as `initial_buffering_ms`.

Vimeo's README rebuffer column (4, 1, 1, 1, 1, 4) should read **3, 0, 0, 0, 0,
3**.

## 4. Audio exceeds video at one rung only

**Measured**, all twelve rungs, from `SourceBuffer.appendBuffer` byte totals
split by MIME:

| app | rung | video bytes | audio bytes | audio/video | video codec | audio codec | itags |
|---|---|---|---|---|---|---|---|
| youtube | 0.5M | 1,505,016 | 2,661,648 | **1.769** | av01.0.00M.08 | opus | 394,250,251 |
| youtube | 1M | 5,500,173 | 3,433,384 | 0.624 | av01.0.04M.08 | opus | 394,251,395,396,397 |
| youtube | 1.5M | 9,874,338 | 3,433,384 | 0.348 | av01.0.04M.08 | opus | 397,251 |
| youtube | 3M | 17,686,076 | 3,433,384 | 0.194 | vp09.00.51.08 | opus | 247,251 |
| youtube | 6M | 17,686,076 | 3,433,384 | 0.194 | vp09.00.51.08 | opus | 247,251 |
| youtube | 10M | 17,686,076 | 3,433,384 | 0.194 | vp09.00.51.08 | opus | 247,251 |
| vimeo | 0.5M | 4,814,395 | 4,507,509 | 0.936 | av01.0.08M.08 | mp4a.40.2 | - |
| vimeo | 1M | 6,411,525 | 4,943,572 | 0.771 | av01.0.01M.08 | mp4a.40.2 | - |
| vimeo | 1.5M | 8,190,184 | 4,943,572 | 0.604 | av01.0.01M.08 | mp4a.40.2 | - |
| vimeo | 3M | 24,576,208 | 5,089,056 | 0.207 | av01.0.01M.08 | mp4a.40.2 | - |
| vimeo | 6M | 55,068,420 | 5,089,056 | 0.092 | av01.0.01M.08 | mp4a.40.2 | - |
| vimeo | 10M | 72,413,432 | 4,943,572 | 0.068 | av01.0.12M.08 | mp4a.40.2 | - |

Audio exceeds video **only at YouTube 0.5 Mbps**, where the 144p AV1 rendition
is smaller than the Opus track feeding it.

**Retracted:** "YouTube switches AV1 -> VP9 between 1.5 and 3 Mbps." Two
repeat trials at the same 3 Mbps cap produced 480p AV1 (9,874,338 B) and 720p
VP9 (17,686,076 B) respectively, with an **identical player box of 1331x749**
and zero switches within either run. The codec/resolution choice at 3 Mbps
varies between runs; it is not a bandwidth threshold. **Measured.**

## 5. What the recorded codec actually is

**Measured:** `mse_mime_switch_log` is `[]` on all twelve runs and
`mse_video_mime` stayed `av01.0.04M.08` through a run whose itags went
394 -> 395 -> 396 -> 397. YouTube declares one SourceBuffer MIME covering its
whole ladder and switches renditions inside it without calling `changeType`.

So the codec recorded per run is the **declared SourceBuffer MIME**, not the
codec of the rendition being decoded. It is present in every sample, but it
does not track rendition changes within a run. Getting the decoding
rendition's codec generically would need the init segment parsed; the itag
route is YouTube-specific.

## 6. Resource Timing on YouTube: the filter is not the cause

**Claimed:** "Probable cause is this probe's own filter -- an `initiatorType`
allowlist plus a 20 KB floor -- which is unconfirmed."

**Measured now** (YouTube 1 Mbps, trial 1), with the unfiltered totals the
probe did not previously report:

| quantity | value |
|---|---|
| MSE appended (video + audio) | 7,842,509 B |
| `rt_all_bytes` (unfiltered) | 6,987,336 B -> **0.891** of MSE |
| `rt_media_bytes` (filtered) | 6,912,813 B -> 0.881 of MSE |
| entries, all / media | 156 / 17 |
| dropped by initiator / by size floor | 11 / 128 entries |
| dropped bytes | 74,523 B = **1.07 %** of `rt_all_bytes` |
| `rt_buffer_full_events` | 0 |

The filter accounts for 1.07 of a 10.90 percentage-point shortfall, so it is
**disproven** as the cause. Buffer eviction was already ruled out and is
confirmed at 0 events against a 3000-entry buffer.

**Unknown:** the remaining ~10 %. The gap is bounded (769 KB - 1,563 KB over
the run) and does **not** grow, which rules out steady byte loss; but it also
does not close, sitting flat at 855,173 B for the final 80 s after both
counters stop advancing. At 50 s, 936,859 B had been appended with **zero**
media RT entries. One candidate consistent with both observations -- bytes
delivered on a streaming response that has not ended, since an RT entry only
appears at response end, and one such response still open at teardown would
leave its bytes permanently unaccounted -- is **untested**. Vimeo shows no
such shortfall (0.995-1.000), so this is YouTube-specific.

RT therefore stays corroboration on YouTube, not an independent validator.

## 7. Things the README asserted that remain unverified

- *"Treat the 10 Mbps Vimeo row as possibly VM-limited."* An A/B at that exact
  cell with the new instrumentation off and on gives resolution 720p both,
  legacy fps 23.80 / 23.86, buffer 24.10 / 24.34 s, host CPU peak 83.6 /
  83.3 %, collector peak 89.4 / 90.6 %. So **the instrumentation does not cause
  the degradation** (measured), and 1440x1080 *is* reached in 17 samples with
  the probe on, so 720p is the mode rather than a ceiling. Whether the host is
  the limiter is still **unknown** -- host CPU peaked near 84 % in both arms,
  which is suggestive and not conclusive.
- The player box is **not constant across runs** (1397x786, 1461x822,
  1461x822, 1331x749, 1331x749 across five runs). This is a confound for any
  cross-run comparison on sites that choose rendition by player size. It does
  not explain the 3 Mbps split above, where the box was identical.
