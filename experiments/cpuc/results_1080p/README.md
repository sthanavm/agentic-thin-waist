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

## Caveat

The viewport was raised, but resolution stayed flat — so this set bounds the
cause (not the viewport, not the link) without isolating the decoder. Forcing
a non-AV1 codec would test the decode hypothesis directly; that is a separate
run and is not claimed here.

Two of the three runs (12.5 and 6.25 Mb/s) carry the background-traffic
`suspect` flag for Chrome's own fetches inside the measured window, exactly as
in the main sweep. That makes those rows conservative, not optimistic.

## Files

| file | what it is |
|---|---|
| `supp1080_runs.csv` | one row per run, with the measured element box, the player's serving/optimal pair, codec and dropped-frame counts |
| `run.log` | the batch log |
| `COMPLETE` | end-of-batch marker with the delivered resolution per point |
| `manifest.remote.txt` / `manifest.local.txt` | sha256 of every file on vm4 and after transfer; 3 files, 3 matched, 0 missing, 0 mismatched |
