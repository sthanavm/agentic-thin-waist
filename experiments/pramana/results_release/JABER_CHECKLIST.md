# Requirements checklist

Every requirement I could find, with its status and the evidence for it.
Statuses are **met**, **partly met**, **not met**. Where a requirement is not
met, that is stated plainly rather than softened.

## Sources I actually had

| source | available | used |
|---|---|---|
| The 13 rules, graph format and five metrics as written in the task brief | yes | the authoritative list below |
| Jaber's reference code, `SNL-UCSB/netgent` branch `jaber_QoE`, `src/netgent/browser/stats_logger.py` | yes — read at commit `c4891e6bbd`, 213 lines | compared field by field below |
| Repo docs (`REALQOE.md`, `experiments/pramana/README.md`, `docs/`) | yes | build and run procedure |
| **Meeting transcripts / notes files / links from Jaber** | **NO — none present in this repo or available to me** | **nothing inferred from them.** The rule list below is taken from the brief as given; I have not guessed at any requirement that may have been stated only verbally |

## The 13 rules

| # | rule | status | evidence |
|---|---|---|---|
| 1 | Throughput never exceeds the shaped cap (peak AND average) | **met** | checked per run in `validation/VERIFICATION_REPORT.md` as `jaber1_cap_peak` and `jaber1_cap_mean`; peak allows 5 % for 1-second bin edges, mean allows none |
| 2 | Save a pcap for every run | **met** | one `capture.pcap` per run; `pcap_manifest.csv` lists size, SHA-256 and location. Not committed, per rule below |
| 3 | Separate download and upload plots; video = download only | **met** | `download_throughput.png` and `upload_throughput.png` per run. Note: the pre-existing `qoe_report.py` emitted **only** a download chart, so the release builder was written to produce both |
| 4 | Show buffer level | **met** | `qoe_buffer.png`, from `buffered.end - currentTime` sampled every 1.0 s, with rebuffer spans shaded |
| 5 | Show resolution over time | **met** | `qoe_resolution.png`, step plot of `frame_height` |
| 6 | Flag NO_PLAYBACK and regenerate if playback never started | **partly met** | the flag is computed and written to `summary.json` as `verdict`, and checked as `jaber6_playback_flagged`. Automatic re-running on NO_PLAYBACK is **not** wired in: a flagged run is reported, not silently replaced |
| 7 | Multi-app cap = total shared cap, not per app | **not exercised** | no multi-app run is in this release (Step 3b is gated). The shaping applies one cap to the shared link, and an earlier validator bug that summed per-app peaks was found and fixed, but no concurrent cell is included here to demonstrate it |
| 8 | One pcap per app (split by remote host/IP); one plot per app, never combined | **met** | **Checked rather than assumed, and it was NOT met at first**: the pipeline wrote one `capture.pcap` per *run* and split only the byte accounting, so a solo run's pcap still contained the page, DNS and every third-party host. `build_release.py` now writes a real per-app pcap (`<run>.<app>.pcap`) containing only packets to/from the IPs attributed to that app, using the same attribution the throughput charts use. The writer was round-trip tested — timestamps, lengths, payload and linktype all preserved. Each one's packet count, byte total, attributed-IP count and SHA-256 are in `pcap_manifest.csv`. Charts are one app per file. Not demonstrated on a multi-app run, since none is included |
| 9 | Cross-traffic profiles: not implemented, waiting on Jaber's pointer; state it, do not fake it | **met (as a disclosure)** | stated in `README.md` under Known limitations and in `sweep_matrix.md` under "Not run, and why". No profile is simulated and none is claimed |
| 10 | High-bitrate source video so quality differences are visible | **met** | YouTube and Vimeo clips exercise 144p-1440p; Vimeo reaches 1440x1080 and 2.9 Mbps delivered, YouTube 720p. Tubi is a feature film. Resolution and delivered bitrate both vary across the ladder, which is the observable the rule asks for |
| 11 | Run everything on the lab VM, not the laptop | **met** | every run executed on `docker-vm-4`; `run_meta.json` records the VM name and the on-VM run directory. The only laptop-side work was git and the offline parser fixtures |
| 12 | Everything reproducible: scripts, notebook, commands, versions | **met** | `run_matrix.py` is the sweep; `validation/build_release.py`, `write_docs.py`, `analyze_release.py`, `verify_release.py` rebuild the package; commands are in `README.md`; commit, Chrome version and shaping are in each `run_meta.json` |
| 13 | Cross-validate any anomaly before calling it a finding | **met** | the Vimeo 10 Mbps anomaly was cross-validated on two independent runs before being called decode-limited; the YouTube 3 Mbps rendition split was re-run with the box enforced; a previously claimed AV1→VP9 transition was **retracted** when repeats refuted it |

## Graph format

| requirement | status | evidence |
|---|---|---|
| Line graphs, not filled | **met** | `ax.plot` / `ax.step`; no `fill_between` anywhere in the release builder |
| Fixed y-axis 0-11 Mbps on throughput plots | **met** | `THROUGHPUT_YMAX = 11.0` applied to both direction charts |
| Window-clipped to the playback/call period | **met** | x-axis spans first to last QoE sample; throughput bins outside that window are excluded |
| Six single-metric charts per run | **met, with eight** | buffer, resolution, bitrate, switches, fps, startup — plus download and upload throughput as separate files |

## The five QoE metrics

| metric | status | how it is obtained (same code on every site) |
|---|---|---|
| 1 Startup delay | **met** | first `requestVideoFrameCallback` `presentationTime`, plus `probe_t0_page_ms` to place it on the page timeline. Reported separately from `initial_buffering_ms` |
| 2 Buffer level + rebuffering | **met** | `buffered.end - currentTime` at 1.0 s; rebuffers from `waiting`→`playing` media events, with the initial buffer fill excluded (it is startup, not an interruption) |
| 3 Average delivered bitrate, video/audio split | **met except Twitch** | `SourceBuffer.appendBuffer` byte totals keyed by MIME. Twitch runs MSE inside Workers and feeds the element a `MediaSourceHandle`, so this is structurally unreachable there and reports `null` with that reason, never 0 |
| 4 Bitrate/rendition switching, timestamped with direction | **met** | resolution transitions with `up`/`down`/`codec_change`, plus an init-segment-derived config count that does not depend on the player calling `changeType` |
| 5 Frame rendering rate | **met** | `presentedFrames` from `requestVideoFrameCallback` — frames actually composited, not merely decoded |
| Dropped frames: raw data only | **met** | carried as `raw_dropped_frames`; never folded into a score |
| Throughput excluded from QoE | **met** | throughput appears only in its own two charts and in the cap check, never in the metrics block |

## Against Jaber's own reference code

Read from `SNL-UCSB/netgent@jaber_QoE:src/netgent/browser/stats_logger.py`.
His logger samples `video_width/height`, `resolution`, `playback_rate`,
`paused`, `muted`, `volume`, `current_time_secs`, `buffer_ahead_secs`,
`dropped_video_frames`, `total_video_frames`, `corrupted_video_frames`, plus
YouTube's full `getStatsForNerds()` and a Twitch branch.

| of the five metrics | in Jaber's logger | here |
|---|---|---|
| Buffer level | **yes** — `buffered.end - currentTime`, identical definition | same |
| Startup delay | no — no first-frame timestamp is captured | `requestVideoFrameCallback` `presentationTime` |
| Rebuffering | no — no media-event listener; only YouTube's `player_state` | `waiting`→`playing` spans, any site |
| Delivered bitrate, video/audio split | no — appended bytes are not observed | `appendBuffer` totals per SourceBuffer MIME |
| Switching with direction | no — resolution is sampled but no switch event or direction is derived | timestamped events with direction |
| Frame rendering rate | **decoded only** — `totalVideoFrames` | `presentedFrames` (composited) and decoded kept separately |

Two further differences worth stating, both measured rather than assumed:

- His logger branches on hostname (`youtube.com`, `twitch.tv`, else fallback).
  The brief asked for one method that works on any site, so the channel here is
  site-independent and YouTube's stats-for-nerds is kept in `sfn_*` keys used
  **only** to validate, never to produce a reported number.
- His logger takes `document.querySelector('video')`, the first element in the
  document. That was measured to pick the wrong element in this harness — the
  collector's own camera self-view in conferencing runs, and uncomposited 0x0
  elements elsewhere — so element choice here ranks by rendered box area and
  excludes local captures.

None of this is a criticism of a reference logger that was built for a
different purpose; it is recorded so the difference in numbers between the two
is explainable rather than mysterious.
