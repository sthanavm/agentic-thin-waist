# Real player-side QoE — per-app support matrix

What the platform can honestly measure from each application's **own player**,
as opposed to inferring it from the network. Produced by `shared/qoe.py` from the
per-sample JSONL a collector writes; driven by the registry in `shared/apps.py`.

The governing rule is **honest nulls**: where a signal is genuinely absent, the
field is `null`. It is never filled with a stand-in from a different quantity.

---

## How it runs

```
pramana_helpers.run_experiment(cfg, mode="direct")     ← notebook client
   ├─ substrate worker :8002   shape once → capture once on veth2
   ├─ video-qoe-collector      REAL headed Chrome, one container, one thread per app
   │     └─ nsenter --net=/proc/<substrate-pid>/root/run/netns/ns1
   │           → traffic is genuinely shaped and captured
   │     └─ writes {app}_stats.jsonl  (raw per-second player samples)
   ├─ shared/qoe.py            samples → QoEMetrics  ← the definitions live here
   ├─ Telemetry :8004          per-app qoe_metrics
   └─ record.json + per-app plots
```

**The driver.** `services/orchestration/scripts/selenium_video_qoe/collect.py`
(from PR #167, branch `dual` — unmerged, which is why it is absent from `HEAD`).
It is a *full browser driver*, not a stats logger: it starts Xvfb + fluxbox per
app, builds real headed `google-chrome-stable` through SeleniumBase +
undetected-chromedriver, navigates, nudges `video.play()` muted, synchronizes all
apps on a `threading.Barrier`, and samples once per second. No wrapper was
needed. Its browser-driving is reused as-is; its metric logic is not used at all.

Changes made to it are limited to making it registry-driven:

- jobs carry `kind` from `shared/apps.py`; the WebRTC branch routes on that
  instead of `app == "google_meet"`, so any conferencing app takes the same path;
- each sample records `app` and `kind`, and a leading `{"record": "meta"}` line
  carries `play_trigger_ts` so startup time is measured from the real play
  trigger rather than from the first sample (which understates it);
- a failing job calls `sampling_barrier.abort()` so siblings fail fast instead of
  blocking for the full barrier timeout.

**One mechanism fix was required.** PR #167 joins ns1 via
`/proc/<ns1-anchor-pid>/ns/net`, reading the anchor PID from
`/var/run/substrate/ns1.pid`. That is only correct when the substrate worker runs
with `--pid host`. On this deployment it does **not** (`PidMode` is empty), so
that PID is container-local: on the host, `/proc/62/ns/net` resolves to the
**root** namespace (`net:[4026531840]`), and the collector would have run
unshaped with none of its traffic in the pcap — the exact failure the PR warns
about. `ns1` is a *named* namespace, so `ns1_netns_path()` resolves it through
the substrate container's own mount instead:

```
/proc/$(docker inspect -f '{{.State.Pid}}' substrate-worker)/root/run/netns/ns1
```

Verified: a container entered that way holds `veth1 172.16.1.1/30` (the shaped
ns1 client interface, inode `net:[4026532936]`) and reaches the internet, versus
the unshaped `172.17.0.2` without it.

**Layout.** Everything lives in this repo. The definitions are `shared/apps.py` and `shared/qoe.py`; the client library and notebook are
`experiments/pramana/`, which imports them as a normal same-repo package (`from shared import apps, qoe`, repo root resolved from `__file__`). The
browser driver is `services/orchestration/scripts/selenium_video_qoe/`. A fresh clone needs no sibling checkout and no environment variable; see
`experiments/pramana/README.md` for setup.

## HTML5 video apps

| Field | youtube | vimeo | tubi | twitch | roku | puffer |
|---|---|---|---|---|---|---|
| `video_resolution_p` | **real** | **real** | **real** | **real** | **real** | **real** |
| `resolutions_observed` / timeline | **real** | **real** | **real** | **real** | **real** | **real** |
| `video_startup_time_ms` | **real** | **real** | ad-skewed | **real** | **real** | **real** |
| `rebuffer_events` / `rebuffer_duration_ms` | **derived** | **derived** | **derived** | **derived** | **derived** | **derived** |
| `dropped_frame_pct` | **real** | **real** | **real** | **real** | **real** | **real** |
| `frame_rate_fps` | **derived** | **derived** | **derived** | **derived** | **derived** | **derived** |
| `mean_buffer_ahead_secs` + timeline | **real** | **real** | **real** | **real** | **real** | **real** |
| `mean_bitrate_mbps` | `null`¹ | `null` | `null` | `null` | `null` | `null` |
| `bitrate_changes` | `null` | `null` | `null` | `null` | `null` | `null` |
| `connection_speed_estimate_mbps` | **real**¹ | `null` | `null` | `null` | `null` | `null` |
| `video_duration_secs` | **real** | **real** | **real** | `null` (live) | **real** | `null` (live) |
| `delivered_fraction_of_video` | **real** | **real** | **real** | `null` (live) | **real** | `null` (live) |

**real** = read directly from the player. **derived** = computed from real player
signals (see below). `null` = genuinely unavailable.

¹ **YouTube bitrate — why it is null even though YouTube reports numbers.**
`getStatsForNerds()` exposes two byte-ish fields, and neither is a media bitrate:

- `bandwidth_kbps` is the player's estimate of *available connection speed*. The
  captured samples show `76764 Kbps` while the link was shaped to 3 Mbps.
  Reporting it as bitrate (as `scripts/analyze_stats.py` does) overstates it by
  more than an order of magnitude. It is surfaced as
  `connection_speed_estimate_mbps` and **never** as bitrate.
- `network_activity_bytes` is an instantaneous *recent activity gauge*, not a
  cumulative counter — observed values bounce (`35 KB, 0, 0, 0, 71 KB, 47 KB…`).
  Differencing it as if it accumulated yields **0.0039 Mbps for a 480p stream**.
  This was caught in a live run and fixed: `_derive_bitrate()` now requires a
  genuinely non-decreasing counter, so this path returns `null` with
  `bitrate_basis = "unavailable (player reports an activity gauge, not a counter)"`.

Per-app *network throughput* is measured separately from the PCAP and is also not
substituted here — it carries audio, container and protocol overhead, so it is a
different quantity from media bitrate.

### How the derived fields are derived

- **`rebuffer_events` / `rebuffer_duration_ms`** — a rebuffer is a span where
  `current_time_secs` is **not advancing** while the player is **not paused** and
  **not ended**, corroborated by `buffer_ahead_secs ≤ 0.5 s` or YouTube's own
  `player_state == 3` (BUFFERING). Spans are merged and counted.

  This replaces the old network proxy ("throughput below 5% of cap"), which was
  wrong in both directions: a player that has buffered 60 s of video and gone
  idle is healthy, not stalled; and a player can freeze while the network is
  still busy. In the verified sample below, the network was idle for ~50 of 83
  seconds while the buffer held 30–60 s and playback never froze — **0 rebuffers**.
- **`frame_rate_fps`** — `total_video_frames` delta over the active playback
  window (`derivation.fps_basis == "total_video_frames_delta"`).
- **`video_startup_time_ms`** — from the collector's play-trigger timestamp to the
  first sample whose media clock advances. Without a play-trigger stamp it is
  measured from the first sample and flagged
  (`derivation.startup_basis == "first_sample"`), which slightly *understates*
  startup. Collectors should stamp the trigger.

### App-specific caveats

- **tubi** — ad-supported. Pre-roll ads play before the title, so the opening
  samples describe the **ad**, not the content: startup and early resolution are
  ad metrics.
- **twitch** — live. No finite duration and no seek, so `video_duration_secs` and
  `delivered_fraction_of_video` are `null` by construction; startup is
  time-to-first-frame. Supply a channel that is actually streaming via
  `app_urls`; the bare `twitch.tv` home page has no `<video>` and reports
  `no_playback`.
- **puffer** — live research player; may require accepting participation terms
  before playback begins.
- **roku / vimeo** — generic `<video>`, no vendor stats API: bitrate `null`.

---

## WebRTC apps (meet, zoom)

| Field | source |
|---|---|
| `mean_bitrate_mbps` / max / min | inbound-rtp bitrate (**real**) |
| `packet_loss_pct` | inbound-rtp packets lost (**real**) |
| `dropped_frame_pct` | `framesDropped / framesDecoded` (**real**) |
| `rebuffer_events` / `rebuffer_duration_ms` | WebRTC freeze count / freeze duration (**real**) |
| `mean_jitter_secs` | inbound-rtp jitter (**real**) |
| `video_resolution_p` | inbound-rtp frame height (**real**) |
| `video_startup_time_ms` | `null` — no equivalent in a call |

**Both require a remote peer.** With nobody else publishing video, the only
track is the **local camera preview**, which measures the local machine and not
the network. `shared/qoe.py` rejects that case: if no sample shows non-zero
inbound bitrate it returns `status: "no_remote_media (no peer publishing video)"`
with `player_qoe_available: false`. Fabricated conferencing numbers are never
emitted.

### Supplying a peer (options, in preference order)

1. **Bot peer, same host** — a second headless Chrome joining the same room with
   `--use-fake-device-for-media-stream` plus
   `--use-file-for-fake-video-capture=/path/loop.y4m`, publishing a looped Y4M
   clip. Deterministic content, no external dependency, and the bot must sit
   **outside** the shaped namespace so its uplink is not throttled too.
2. **Standing test room** — a long-lived meeting with a permanently joined
   participant; simplest to operate, but the room must outlive the experiment.
3. **Two collectors, one room** — both inside the run; measures a real
   two-party call, but both endpoints share the shaped bottleneck, so the
   measurement is of the pair, not of one downlink.

Without one of these configured, a `needs_peer` app is marked
`status: "skipped"` with a reason and **the rest of the run still succeeds**.

**Zoom additionally:** web-client availability varies by meeting settings and
often requires a signed-in Chrome profile; some meetings refuse the web client
entirely. Join failure is reported as `skipped` with the reason — it never hangs.

---

## Shell apps (wget)

No browser and no player, therefore no player QoE — by construction, not by
limitation. `player_qoe_available` is `false` and the record carries a transfer
summary (`bytes`, `seconds`, `completed`, `mean_throughput_mbps`) instead. No
player fields are invented.

---

## Verification performed

### Against real captured samples (summarizer only)

| sample file | result |
|---|---|
| `youtube_qoe_stats_vm.jsonl` | 480p (360/480/720 observed), 25.03 fps, startup 2039 ms, 0 rebuffers, 0.434% dropped, buffer 48.4 s |
| `twitch_stats.jsonl` | 1080p, 56.29 fps, 7.588% dropped, correctly `is_live` with duration + delivered-fraction `null` |
| `youtube_1mbps_stats.jsonl`, `youtube_stats.jsonl` | `no_playback` — every sample `current_time = 0`, paused, `player_state = -1` |

### End-to-end on the lab VM (real Chrome, shaped link)

**Run A — `youtube` solo, 10 Mbps / 50 ms / 60 s**

```
status ok      resolution 480p     startup 3289 ms    fps 24.95
rebuffers 0 (0 ms)                 dropped 0.254%     buffer 48.8 s
watched 56.1 s of 213 s            bitrate null       delivered_fraction 0.263
```
Telemetry row carries 26 populated `qoe_metrics` fields.

**Run B — `youtube` + `vimeo` concurrent, 3 Mbps / 100 ms, plus `meet` with no peer**

| app | player QoE | network |
|---|---|---|
| youtube | ok — 480p, startup 1033 ms, 0 rebuffers, 0.92% dropped, 24.95 fps, buffer 47.4 s, watched 58.7 s | 1.14 Mbps avg → `served` |
| vimeo | **`no_playback`** — media clock never left 0 across 58 samples | 0.40 Mbps avg, 5.5 MB transferred → **`failed_to_deliver`** |
| meet | `skipped` — needs a remote peer publishing video | no traffic |

Cross-attach check (the failure mode PR #167 documents): each output file
referenced only its own app — `youtube_stats.jsonl` saw only `www.youtube.com`
(59 samples), `vimeo_stats.jsonl` only `player.vimeo.com` (59). No leakage.

**The Vimeo result is the point of this work.** It moved 5.5 MB and held a 69%
delivered fraction, so the network-side view scored it as a served, merely
slow app. The player says playback never started at all. Only the player-side
measurement distinguishes "the tier degrades quality" from "the tier fails to
deliver video" — and the per-app verdict now takes the player's answer, marking
it `failed_to_deliver`.

### Acceptance checks

| # | check | result |
|---|---|---|
| 1 | youtube solo: real resolution, startup > 0, int rebuffers, dropped% in [0,100], telemetry populated | PASS |
| 2 | vimeo: real resolution/buffer derived; bitrate real-or-null, never fabricated | PASS (bitrate null, documented) |
| 3 | concurrent: both get real QoE; each file only its own app | PASS |
| 4 | meet with no peer: `skipped(reason)`, run still succeeds for co-scheduled apps | PASS |
| 5 | wget: no player QoE, transfer summary, nothing fabricated | PASS |
| 6 | pcap non-empty; network throughput plots/metrics unchanged | PASS (44.2 MB, shaping +2.9%) |
| 7 | no `--network container:X`; `nsenter` ns1 join intact | PASS (only a docstring mention of why it was rejected) |
| 8 | `load_dataset()` shows the real-QoE columns | PASS |

Two bugs were found and fixed by running this for real rather than reasoning
about it: the bitrate gauge above, and a crash where an app with no attributed
traffic (a skipped `meet`) formatted `None` into a plot label and took the whole
record down *after* the experiment had succeeded. Plot generation is now wrapped
so a rendering failure can never cost the pcap or the record, and
`rebuild_dataset_index()` re-derives the append-only index from the per-run
records after any such correction.
