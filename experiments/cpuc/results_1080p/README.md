# Supplementary set — 1080p viewport, ABR on

**This is not part of the main 720p sweep. A different viewport is a different
experiment, so these rows live in their own directory and their own CSV and are
never merged with it.** The collector also slugs a changed viewport
(`_vcapyoutubefullscreen`), so the two cannot mix even by accident.

## The note this set exists to supply

The main sweep ran with the browser window at 1920x1080 on a 1920x1080 virtual
screen — the collector's default. That is **not** the box the player draws
into. YouTube's watch layout spends the window on a sidebar and browser chrome
and leaves the video element at **1331x749** (measured on the main sweep's
25 Mb/s run: `window_outer` 1919x1079, `window_inner` 1911x988, `player_elem`
**1331x749**). An adaptive player picks its rendition to fit the element, and
for a 1331x749 box **720p is the correct choice**. So the main sweep's flat
720p line is a statement about that viewport as much as about the link.

This set puts the player itself into fullscreen, so the element becomes the
whole screen — exactly **1920x1080** — and leaves ABR free. It answers one
question: at the same capacities, what does the same link deliver when the
viewport is not the binding constraint?

No NDT runs here: the speed tests at these capacities are already in the main
sweep and the link is unchanged.

## Result

| down Mb/s | element box | player serving | player's own "optimal" | codec | dropped frames | rebuffers | delivered |
|---|---|---|---|---|---|---|---|
| 25 | 1920x1080 | 1280x720@25 | **1920x1080@25** | AV1 | 260/1611 (16.1%) | 0 | **720p** |
| 12.5 | 1920x1080 | 1280x720@25 | **1920x1080@25** | AV1 | 233/1650 (14.1%) | 0 | **720p** |
| 6.25 | 1920x1080 | 1280x720@25 | **1920x1080@25** | AV1 | 209/1771 (11.8%) | 0 | **720p** |

All three runs valid; the element box was verified at 1920x1080 in every row
before the result was accepted. 720p in 60 of 60 samples at every point, with
no rebuffering anywhere.

## Charts

| chart | what it shows |
|---|---|
| `viewport_vs_rendition.png` | the element the player was given (1920x1080) against the rendition it served (720p), with the main sweep's 749px element and its identical 720p on the same axes. The box grew 44% in height and the rendition did not move. |
| `optimal_vs_served.png` | the player's own "optimal" for this viewport (1080p) against what it served (720p), with the gap shaded. Optimal tracks the viewport, so the player noticed the bigger element and declined to use it. |
| `dropped_frames.png` | the decode evidence: 11.8-16.1% of frames dropped at 720p. Dropped frames rise with capacity, because more headroom means a higher-bitrate 720p stream and more decode work per frame -- not less. |

A resolution-vs-bandwidth line is deliberately not among them: all three points
delivered 720p, so that chart would be a flat line carrying no information.
Each chart marks IQB's 25 Mb/s minimum.

## What this shows

**The 720p ceiling is not the viewport, and it is not the link.** With a
1920x1080 element and 25 Mb/s of capacity, YouTube's own stats-for-nerds
reports `1280x720@25 / 1920x1080@25` — it knows the optimal rendition for this
viewport is 1920x1080, and serves 720p anyway.

The reason is in the same telemetry: the stream is **AV1**, decoded in software
on this VM, and **12–16% of frames are dropped at 720p**. YouTube's ABR backs
off resolution when the client cannot decode what it is given, so the binding
constraint here is **host decode capability**, not bandwidth. Mean demand was
2.5–2.6 Mb/s at all three capacities — about a tenth of IQB's 25 Mb/s minimum.

This strengthens, rather than weakens, the main sweep's finding: the gap
between what the link offers and what the player takes is a player-side
decision, and a speed test measuring the link cannot see it.

## Correction (2026-11-07): the decode explanation does not survive

The reading above — that AV1 software decode is what binds — is **contradicted
by a direct test on the same host.** One Vimeo run at 100 Mb/s / 20 ms served
**1920x1440 in 85 of 90 samples**, in **AV1**
(`av01.0.01M.08.0.111.01.01.01.0`), with 1.38 s startup and no rebuffering.

So this VM decodes AV1 well past 1080p, and "the host cannot decode what it is
given" cannot be the reason YouTube stopped at 720p here. The dropped-frame
figures below are real and still reported, but they do not carry the causal
claim that was attached to them.

**What survives, and it is the part the main sweep needs:** the ceiling is not
the link and not the viewport, and YouTube's own telemetry reports
`1280x720@25 / 1920x1080@25` — the player knew 1080p was optimal for this
element and served 720p anyway. That remains a player-side decision. What it is
*not* is explained, and this set no longer claims to explain it.

## The link test (2026-11-11): the cause is the player

The caveat below asked for two runs this set did not have — a link with real
headroom, and a non-AV1 codec. Both are now done, on Jaber's exact condition:
**300 Mb/s both directions, 1 ms base latency, nothing else shaped.** The
shaper was verified before the runs rather than assumed: iperf3 measured
280.3 / 286.6 Mb/s with **zero retransmits** and ping 1.13 ms.

Four runs on that one link, each removing one candidate cause. Files:
`yt1080_link_runs.csv`, `yt1080_link.log.txt`, `yt1080_link_COMPLETE.json`.

| | what it removes | element | serving / player's own optimal | codec | delivered | dropped frames | startup | rebuffers |
|---|---|---|---|---|---|---|---|---|
| **A** | nothing — the spec verbatim | 1387x780 | 720p / **720p** | AV1 | 720p, 60/60 | 17/1546 (1.1%) | 2.75 s | 0 |
| **B** | the viewport | 1920x1080 | 720p / **1080p** | AV1 | 720p, 60/60 | 240/1590 (15.09%) | 2.70 s | 0 |
| **C** | the player's ABR | 1920x1080 | 1080p / 1080p | AV1 | **1080p, 60/60** | **0/1568 (0.0%)** | **2.16 s** | 0 |
| **D** | the AV1 decode path | 1920x1080 | 720p / **1080p** | VP9 | 720p, 60/60 | 228/1585 (14.38%) | 2.22 s | 0 |

All four valid, 61 samples each, the element box verified before the result
was accepted, and **none flagged** by the background-traffic guard.

### At 300 Mb/s and 1 ms, YouTube still does not reach 1080p on auto

**It is the player.**

- **Not the link.** Mean link throughput was **2.56–2.68 Mb/s on a 300 Mb/s
  link — 0.9% of capacity** — in the three auto runs. And the capacity was
  genuinely available: at 100 ms resolution the download peaked at
  **223 Mb/s** (the 1 s series in the CSV averages that away to 27.8–43.8).
  The player could take the link when it wanted it. It did not want it.
- **Not the video.** C served 1920x1080 for 60 of 60 samples from the same URL
  on the same link. The ladder the player itself reported:
  `hd2160, hd1440, hd1080, hd720, large, medium, small, tiny`.
- **Not our QoE collection.** C's 1080p was measured and reported by the same
  code path that reports 720p in B and D.
- **Not the decoder.** C decoded **1080p AV1 with zero dropped frames** and
  the fastest startup of the four.

In B the player reported `1280x720@25 / 1920x1080@25` on a 300 Mb/s link: it
knew 1080p was optimal for the element, had the bandwidth, had the renditions,
could decode them, and served 720p anyway.

### The dropped-frame figures track the upscale, not the rendition

The withdrawn decode explanation rested on the dropped-frame counts. Those
counts follow the **upscale ratio**: 720p into a 780px box → 1.1%; 720p into a
1080p box → 15.09%; 1080p into a 1080p box → **0.0%**. Blocking AV1 (D) moved
the codec to VP9 and left the drop rate at 14.38%, so it is not AV1-specific
either. The correction below is now positively refuted rather than merely
unclaimed. The upscale correlation is reported **as observed**; no mechanism
is claimed for it.

### The packet trace matches the shape Jaber described

Measured on YouTube's own attributed bytes at 100 ms bins
(`yt1080_link_bursts.json`, with a 3×3 sensitivity grid over the two free
parameters in `yt1080_link_bursts_grid_{A,B,C}.json`):

- **One large initial burst filling the buffer:** 6.66 MB (A), 6.89 (B),
  10.58 (C) against a later-burst mean of 1.11 / 1.21 / 1.95 MB — about 6×.
- **Then smaller bursts every 5–6 s:** median gap between burst starts
  **5.45 s (A), 5.7 s (B), 6.0 s (C)**, inside the 5–6 s described.

Honest qualifier: the period depends on what counts as one burst. Those
figures are at a ≥2 s quiet separation; at 0.5–1 s the same traffic resolves
into sub-bursts 3.8–4.7 s apart. The 5–6 s reading is the one that corresponds
to the description, and it holds across all three traces.

### Caveat on this set

n=1 per variant. C is positive evidence that needs no repetition; B's 720p
reproduces both the 510-run archive and the three supplementary rows above, so
the auto-720p result does not rest on this one run.

## Caveat

The viewport was raised, but resolution stayed flat — so this set bounds the
cause (not the viewport, not the link) without isolating the decoder. Forcing
a non-AV1 codec would test the decode hypothesis directly; that is a separate
run and is not claimed here. **Both are now done — see "The link test"
above.**

Two of the three runs (12.5 and 6.25 Mb/s) carry the background-traffic
`suspect` flag for Chrome's own fetches inside the measured window, exactly as
in the main sweep. That makes those rows conservative, not optimistic.

## Files

| file | what it is |
|---|---|
| `supp1080_runs.csv` | one row per run, with the measured element box, the player's serving/optimal pair, codec and dropped-frame counts |
| `run.log` | the batch log |
| `COMPLETE` | end-of-batch marker with the delivered resolution per point |
| `manifest.remote.txt` / `manifest.local.txt` | sha256 of this set's files on vm4 and after transfer; 4 files, 4 matched, 0 missing, 0 mismatched |
| `yt1080_link_runs.csv` | the four link-test runs, one row each |
| `yt1080_link_bursts.json` | burst structure at 1 s bins, per variant |
| `yt1080_link_bursts_grid_{A,B,C}.json` | the same at 100 ms bins, over a 3×3 grid of burst-definition thresholds |
| `yt1080_link.log.txt` / `yt1080_link_COMPLETE.json` | the batch log and the end-of-batch marker |
| `yt1080_link_manifest.{remote,local,files}.txt` | sha256 on vm4, after transfer, and under the names used here |
| `supp1080_runs.csv.pre-startup-backfill` | the published rows as they stood before `startup_s` was backfilled (see the correction below) |

## Correction (2026-11-11): `startup_s` was blank in all three rows

`supp1080.py` read startup from `player_qoe[app]["startup_delay_ms"]`. That key
does not exist at that level — it lives under the `uniform` block, which is
where `qoe_labels.extract_streaming_metrics` reads it. The lookup returned
`None` on every run and the CSV column came out **empty**, indistinguishable
from "not measured".

It was measured all along: **3.07 s (25 Mb/s), 2.16 s (12.5), 3.65 s (6.25)**,
sitting in each run's own `record.json`. Both this driver and the link-test
driver now use the labeller's definition, and the three rows were backfilled
from their own records rather than re-run — `backfill_startup.py`, which never
overwrites a row that already carries a figure and keeps the original as
`supp1080_runs.csv.pre-startup-backfill`. The committed file before the
backfill is byte-identical to that copy, so the diff is exactly three added
fields per row — `startup_s`, `startup_basis` and `rebuffer_ratio` (blank for
the same reason) — and nothing else. **No label changed**; this set's labels
never used startup.
