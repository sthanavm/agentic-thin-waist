# Repeat trials, 2026-10-09 — what holds up and what was luck

Everything in `README.md` was one trial per cell. This is 3 trials each on
YouTube 1 and 3 Mbps and Vimeo 3 and 6 Mbps, plus the Vimeo 10 Mbps A/B pair,
same conditions (solo, 50 ms, pfifo/cubic, 0 % loss, 180 s).

All figures re-derived from the saved per-second samples in a fresh process, so
the corrected rebuffer metric applies. The batch's own printed rows do **not**
have it: that process imported `qoe.py` before the fix landed and kept using
the pre-fix module for its whole run. Raw per-run values are in
`repeat_trials.json`.

## Spread, by cell

cv = coefficient of variation (sd/mean). "range/mean" is (max-min)/mean.

| cell | n | startup_delay_ms | video Mbps | rendered fps | rebuffers | resolution |
|---|---|---|---|---|---|---|
| vimeo 3 Mbps | 3 | 4260-4572, cv 3.6 % | 1.024-1.084, cv 3.2 % | cv 0.2 % | 0, 0, 0 | 540p all |
| vimeo 6 Mbps | 3 | 2531-3094, cv 10.3 % | 2.291-2.333, cv 1.0 % | cv 0.3 % | 0, 0, 0 | 720p all |
| youtube 1 Mbps | 3 | 19315-20731, cv 3.6 % | 0.178-0.218, cv 10.1 % | cv 0.4 % | 2, 1, 1 | 360p all |
| youtube 3 Mbps | 3 | 7448-8057, cv 4.3 % | **0.290-0.596, cv 35.6 %** | cv 0.1 % | 0, 0, 0 | **480p, 720p, 720p** |

## What holds up

- **Rendered fps.** cv 0.1-0.4 % in every cell. The tightest quantity measured.
- **Resolution mode**, except YouTube 3 Mbps: identical across all 3 trials in
  the other three cells.
- **Rebuffer count after the fix.** 0 in all three trials at Vimeo 3 and 6 Mbps
  and YouTube 3 Mbps. The pre-fix figures of 1 per run at these cells were the
  initial buffer fill being miscounted.
- **Vimeo's delivered bitrate.** cv 1.0-3.2 %, and it tracks the cap
  (1.06 Mbps mean at 3, 2.31 at 6).
- **Startup ordering.** Monotone with bandwidth in every cell, cv 3.6-10.3 %.
- **Vimeo's pcap agreement.** 1.078-1.090 across all 7 Vimeo runs, cv 0.1-0.6 %.
- **Vimeo's Resource Timing agreement.** 1.0018-1.0033 on all 7 runs.

## What was luck

- **YouTube 3 Mbps resolution and codec.** Two trials served 720p VP9
  (17,686,076 B appended, byte-identical) and one served 480p AV1
  (9,874,338 B), with an **identical 1331x749 player box** and zero switches
  inside any of the three runs. The README's 3 Mbps row, and the claim that
  YouTube transitions AV1 -> VP9 between 1.5 and 3 Mbps, are single-trial
  artefacts of which rendition that one run happened to get.
- **YouTube's delivered bitrate at 3 Mbps.** 0.290 vs 0.592 vs 0.596 Mbps --
  a 2x range, entirely explained by the rendition split. Conditional on
  rendition it is tight: the two 720p runs differ by 0.7 %.
- **The 6/6 pass on delivered bitrate vs pcap.** See below. It does not hold.
- **`initial_buffering_ms`.** cv 31-51 % on three of four cells. Reported, not
  relied on.

## The delivered-bitrate check does NOT hold up (reversal)

`CORRECTIONS.md` §1 reported this check passing 6/6 at 1.056-1.092 against
bytes to and from the dominant media peer IP. Across 13 runs it passes 11 and
**fails low on 2**:

| run | peer/MSE | |
|---|---|---|
| youtube_1M_t3 | **0.9855** | below the 1.00 floor |
| youtube_3M_t1 | **0.8583** | below the 1.00 floor |
| youtube_1M_t1, 1M_t2, 3M_t2, 3M_t3 | 1.119, 1.126, 1.070, 1.071 | pass |
| vimeo, all 7 | 1.078-1.090 | pass |
| ladder, all 6 rungs (re-verified) | 1.056-1.120 | pass |

A ratio below 1.00 says the wire carried fewer bytes than the player appended,
which is impossible. So those two are **reference failures, not metric
failures**, and the measured cause is that YouTube delivered the media over
**two** peer IPs in both:

| run | dominant peer | second media peer | both / MSE |
|---|---|---|---|
| youtube_1M_t3 | 198.189.66.13, 8.69 MB | 74.125.157.105, 1.26 MB UDP | 1.128 |
| youtube_3M_t1 | 198.189.66.13, 11.42 MB | 74.125.157.105, 2.90 MB UDP | 1.076 |

The attributor's own log already identified both for the second run: "adopted
2 unnamed flow(s), 14.32 MB" = 11.42 + 2.90.

**The reference has not been fixed, deliberately.** Adding "the next
UDP-dominant peer" would put these two in band but pushes `youtube_1M_t1` to
1.279, outside it. No peer-selection rule tested so far passes all 13 runs, so
the honest position is that **a reliable pcap reference for YouTube's media
bytes does not exist yet**. Vimeo needs no such rule: one CDN peer carries
everything, which is why its 7/7 agreement is the stronger evidence that the
metric itself is sound.

## Why host attribution worked on some rungs and not others — answered

`CORRECTIONS.md` §1 left this unknown. Measured over 13 runs:

| | ClientHellos to media peer | named | hostattr |
|---|---|---|---|
| youtube_1M_t1 | 1 | yes, via SNI | 8.97 MB |
| youtube 1M t2/t3, 3M t1/t2/t3 | 0 | **no** | 0.00-1.26 MB |
| vimeo, all 7 | 1 each | yes, via SNI | 31-76 MB |

The media peer is named from **TLS SNI in a TCP ClientHello and never from
DNS** -- zero googlevideo DNS answers in any capture, on any rung. So host
attribution of a media flow works only when Chrome happened to open a TCP+TLS
connection to that host alongside QUIC. YouTube did in 1 of 6 runs; Vimeo in
7 of 7. The original 0.5/1/1.5-pass vs 3/6/10-fail split was this, not
bandwidth.

## Resource Timing on YouTube is run-dependent, not a constant shortfall

| run | rt_all / MSE | entries (all) |
|---|---|---|
| youtube_1M_t1 | 0.891 | 156 |
| youtube_1M_t2 | **1.011** | 59 |
| youtube_1M_t3 | 0.880 | 60 |
| youtube_3M_t1 | **0.625** | 56 |
| youtube_3M_t2 | 0.813 | 40 |
| youtube_3M_t3 | 0.805 | 125 |
| vimeo, all 7 | 1.0018-1.0033 | 129-143 |

One YouTube run has no shortfall at all and another is short by 37 %, so this
is not a fixed property of the site. `rt_buffer_full_events` is 0 everywhere,
and the probe's filter accounts for ~1 % (`CORRECTIONS.md` §6). Two runs with
**byte-identical** appends (3M t2 and t3, 21,119,460 B each) produced 40 and
125 RT entries. Cause **unknown**.
