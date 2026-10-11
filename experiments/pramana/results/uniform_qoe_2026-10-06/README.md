# Uniform streaming-QoE tool — validation, 2026-10-06/08

> **Read `CORRECTIONS.md` first.** A verification pass on 2026-10-09 overturned
> several claims below: the delivered-bitrate check does **not** fail (it was a
> broken reference, now 1.056-1.092 and passing 6/6), every rebuffer count here
> is **one too high**, the "AV1 -> VP9 between 1.5 and 3 Mbps" transition is
> **not reproducible**, and the Resource Timing shortfall is **not** caused by
> this probe's filter. Numbers in this file that `CORRECTIONS.md` supersedes are
> marked inline.

One extraction method and one output schema for any browser-based streaming
site, with no per-site APIs. Validated against YouTube (stats-for-nerds as
ground truth), then applied to Vimeo.

Thresholds were fixed and committed in `THRESHOLDS.md` (commit `cf92e27`)
**before any of this data existed**. They have not been changed since.

Solo runs, 50 ms, pfifo / cubic / 0 % loss, 180 s, one trial per rung.
Ladder 0.5 / 1 / 1.5 / 3 / 6 / 10 Mbps. `merge_vendor_stats` off throughout, so
stats-for-nerds stays in `sfn_*` and never reaches the channel it validates.

## Metric × app availability (all five)

| Metric | YouTube | Vimeo | Tubi | Twitch |
|---|---|---|---|---|
| 1 Startup delay | ✅ rVFC first presentationTime | ✅ | ✖ no player reached | ✅ |
| 2 Buffer level (≥1/s) + rebuffering | ✅ `buffered` + `waiting`→`playing` | ✅ | ✖ | ✅ |
| 3 Delivered bitrate (video, audio split) | ✅ MSE appended bytes | ✅ | ✖ | ✖ worker-side MSE |
| 4 Bitrate/rendition switching, timestamped + direction | ✅ | ✅ | ✖ | ✅ (height only) |
| 5 Frame rendering rate | ✅ rVFC presentedFrames | ✅ | ✖ | ✅ |

**5/5 on YouTube and Vimeo.** Twitch gives 4/5: it constructs no main-world
MediaSource, spawns two Workers and feeds the element a MediaSourceHandle via
`srcObject`, so byte-level hooks are structurally blind. `uses_worker_media`
records this and bitrate reports `null` with that reason rather than 0. Tubi
never produced a `<video>` element in this environment, so nothing is claimed
about it either way.

## Ground-truth agreement — YouTube

Generic and `sfn_*` are produced by the *same* `STATS_JS` execution, so the two
readings share one timestamp and no interpolation is needed.

| Metric | Reference | Result | Verdict |
|---|---|---|---|
| Buffer level | `sfn_buffer_health_seconds` | MAE **0.170–0.264 s**, r **0.9997–1.0000** | **PASS** 6/6 |
| Decoded frames | `sfn_dims_and_frames` | match **0.976–1.000**, MAE **0.06–0.47** frames | **PASS** 6/6 |
| Rebuffer events | `sfn_player_state == 3` | ours 1–2 vs 0–1, \|Δ\| ≤ 1 | **PASS** 6/6 |
| Switch events | `sfn_codecs` itag changes | **exact match 6/6**, incl. 394→395→396→397 at 1 Mbps | **PASS** 6/6 |
| Delivered bitrate | per-app pcap bytes | ratio 1.29–2.24 vs bound [1.00, 1.25] | ~~FAIL 6/6~~ **superseded — PASS 6/6 at 1.056–1.092 against media-peer bytes, see CORRECTIONS.md §1** |
| Startup delay | pcap first byte → first presented frame | reported only (weak reference) | n/a |
| Rendered fps | none exists | internal check: rendered ≤ decoded×1.05 | **PASS** 6/6 |

### Why the bitrate check failed, and what it means

> **Superseded by `CORRECTIONS.md` §1.** The first point below is right: the
> reference was at fault, not the metric. Points 2 and 3 are wrong. The media
> flow at 3/6/10 Mbps was attributed and its bytes were present; it simply
> carried no hostname for a name-based filter to match. QUIC is **not** the
> explanation -- it carries the media on all six rungs, including the three
> that attributed correctly.

The failure is in the **reference**, not the metric:

1. **pcap-per-app includes the whole page load.** `www.youtube.com` alone is
   4.2 MB per run, plus gstatic / ytimg / google.com. Restricting pcap to the
   media host gives **1.167 / 1.120 / 1.083** at 0.5 / 1 / 1.5 Mbps — inside the
   pre-committed band.
2. **That fix cannot be completed at 3 / 6 / 10 Mbps**: `googlevideo.com` does
   not appear in host attribution for those runs at all (~23 MB unattributed,
   top host `www.youtube.com`). Consistent with a QUIC/HTTP3 attribution gap,
   **not confirmed**.
3. **Vimeo passes the same check 6/6** (below), which is the strongest evidence
   the metric itself is right.

### Resource Timing as a second byte channel

Measured, not assumed. The W3C spec zeroes `encodedBodySize`/`decodedBodySize`
for CORS-cross-origin responses and gates `transferSize` behind the timing-allow
check; these CDNs grant both, while 89 non-CORS entries on the same YouTube page
read 0.

| app | RT ÷ MSE appended |
|---|---|
| Vimeo | **0.995–1.000** (near-exact) |
| YouTube | **0.750–0.988** (undercounts) |

YouTube's shortfall is **not** CORS zeroing (`rt_size_zeroed_entries` = 0 on
every rung) and **not** buffer eviction (10–18 entries against a 250-entry
default). ~~Probable cause is this probe's own filter — an `initiatorType`
allowlist plus a 20 KB floor.~~ **That is now disproven**: measured unfiltered,
the filter accounts for 74,523 B — 1.07 % — of a 10.90 % shortfall, and the
buffer never overflowed (0 events against 3000 entries). The remaining ~10 % is
**unknown**; see `CORRECTIONS.md` §6. RT is therefore reported as corroboration,
not as an independent validator, on YouTube.

## Preliminary Vimeo results

Reference is media-host pcap bytes plus RT; there is no stats-for-nerds.

Rebuffer counts in this table are **one too high** — the initial buffer fill
was miscounted as a rebuffer. Corrected column: 3, 0, 0, 0, 0, 3. Startup here
is `startup_delay_ms` (probe install → first presented frame), not the legacy
~1.0 s figure. See `CORRECTIONS.md` §2 and §3.

| rung | res | startup | rebuf (ms) | video Mbps | audio Mbps | switches | rendered fps | buffer mean | pcap÷MSE | RT÷MSE | verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.5 | 240p | 21.2 s | 4 (12564) | 0.214 | 0.195 | 1 | 22.97 | 7.6 s | 1.115 | 0.997 | PASS |
| 1 | 240p | 11.1 s | 1 (4504) | 0.286 | 0.209 | 2 | 23.94 | 23.4 s | 1.176 | 0.995 | PASS |
| 1.5 | 360p | 7.6 s | 1 (2714) | 0.363 | 0.202 | 2 | 23.90 | 24.4 s | 1.160 | 0.996 | PASS |
| 3 | 540p | 4.2 s | 1 (1166) | 1.098 | 0.209 | 3 | 23.78 | 23.9 s | 1.109 | 0.998 | PASS |
| 6 | 720p | 2.8 s | 1 (603) | 2.474 | 0.222 | 5 | 23.81 | 23.1 s | 1.093 | 0.999 | PASS |
| 10 | 720p | 7.2 s | 4 (6025) | 2.997 | 0.189 | 4 | 23.28 | 24.2 s | 1.093 | 1.000 | PASS |

Vimeo's delivered bitrate **scales with the cap** (0.21 → 3.00 Mbps) and its
ladder climbs 240 → 360 → 540 → 720p, where YouTube saturates at 720p by 3 Mbps.
Vimeo also holds a much shallower buffer (7.6–24 s against YouTube's 60–69 s),
which is why it rebuffers more at the bottom rung.

The 10 Mbps row is the one anomaly: startup rises to 7.2 s and rebuffers return
(4), against 2.8 s and 1 at 6 Mbps. Consistent with the AV1 1440p decode ceiling
seen in earlier work on this 2-vCPU host, so **treat the 10 Mbps Vimeo row as
possibly VM-limited** rather than as a network result. Not separately confirmed
in this sweep.

## Resolution vs bitrate — distinct, with evidence from this ladder

YouTube held **720p at 3, 6 and 10 Mbps** while appending **identical bytes**
(17,686,076 at all three rungs) — but this rests on one trial per rung, and a
repeat at 3 Mbps served **480p AV1** instead with an identical player box, so
the rendition at a given cap varies between runs (`CORRECTIONS.md` §4). Vimeo held **720p at 6 and 10 Mbps** with
delivered video bitrate **2.474 vs 2.997 Mbps**. Same resolution, different
bitrate — they are not interchangeable, and both are reported separately.

## Known limitations

- **One trial per rung.** No variance estimate. Repeat trials run on
  2026-10-09 show this matters: at YouTube 3 Mbps two trials served different
  renditions (480p AV1 vs 720p VP9) and delivered bitrates differing 2x, with
  the player box identical. Any single-trial statement about a rung's
  rendition, codec or delivered bitrate may be luck.
- **Delivered bitrate saturates when the asset finishes downloading.** YouTube's
  appended bytes are identical at 3/6/10 Mbps because the whole clip was
  fetched, so the figure is bytes ÷ measurement window, not the encoded
  rendition bitrate. Dividing by *appended media seconds* would give the
  rendition figure; not implemented, flagged as a refinement.
- **YouTube bitrate is unvalidated against pcap at 3/6/10 Mbps** (host
  attribution gap above).
- **Twitch bitrate is unavailable by construction** (worker-side MSE).
- **Tubi produced no player** in this environment; its channels are untested.
- **Startup delay has no strong reference** and is reported, not thresholded.
- **Rendered fps has no external reference at all** — stats-for-nerds counts
  decoded frames only.
- **2 vCPU host.** The 10 Mbps Vimeo row is the visible candidate for a host
  limit rather than a link effect.
- **Mid-run re-shaping is confirmed feasible** (`POST /shape` re-applies tc on
  the live interface, driven 10→1→10→3 and read back each time) but the
  10→1→10 step profile was **not run** in this sweep.

## Files

`<app>/<rung>mbps/` holds `record.json`, the per-second `*_stats.jsonl`,
`collector.log` and 5 charts per run (per-app throughput for both directions,
the merged view, and a QoE summary carrying buffer and resolution over time).
Captures are not committed. `agreement_youtube.json` holds the raw per-rung
agreement output.
