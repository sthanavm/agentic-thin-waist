# CPUC bandwidth-threshold results

Running log of what we have measured against IQB 2025 Fig 10. Updated as
things land — not regenerated, so the history of what changed stays readable.
Work this study found and did not do is in [FOLLOWUPS.md](FOLLOWUPS.md).

**Every point is n=1 except Twitch, which is n=3.** One run per (app,
capacity, latency) everywhere else; where two runs exist for such a cell it is
because the first failed and the table keeps the valid one. The Twitch sweep
below runs three repetitions per capacity and publishes the spread beside the
median, so one study now says how repeatable these numbers are. It is the
answer to the first item under "What would change these conclusions", and the
non-monotonic rows named below are what n=1 costs.

Common to every study: netem adds its delay on top of the real path to
M-Lab, so a 20 ms setpoint measures 22.6–23.3 ms, 50 ms measures 52.5–53.3 ms
and 100 ms measures 102.5–103.2 ms. The 50 ms rows therefore sit **over** IQB's
50 ms conferencing minimum, and the 100 ms rows **over** its 100 ms streaming
minimum, by 2.5–3.3 ms. Every table carries both the configured and the
measured RTT for this reason.

Also common to every study, and verified against the live qdiscs on vm4 for
this PR: **the configured latency is applied once, on one interface, in one
direction.** A single `netem delay <figure>ms` sits on `ns2:veth3` — whose
egress is the *download* path, counted through the netem itself — and
`ns1:veth1` is `noqueue`, so no delay is added to uploads. There is no `ifb0`
on the box and no half-per-direction split: 1 ms configured measures 1.13 ms
RTT, not 2 ms. `shaping.py`'s docstring claimed otherwise until this PR and is
corrected there, with the `/shape` `latency_location` mapping that produces
it.

---

## YouTube — streaming (PR #21)

**Run:** 8 capacities (100, 25, 12.5, 6.25, 3, 1.5, 1, 0.5 Mb/s) × 3 latencies
(20, 50, 100 ms), app + ndt7 at every point. 24 app points, all collected.

**Headline:** YouTube held 720p with zero rebuffering down to **6.25 Mb/s, 4x
below IQB's 25 Mb/s streaming minimum**, at all three latencies. Quality breaks
between 3 and 1.5 Mb/s: 3 Mb/s is acceptable (720p, startup 7.6–8.2 s), 1.5 Mb/s
is unusable (240p, startup 14–15 s). A speed test calls the link failing at
every capacity below 25 Mb/s while the video is still good.

**Verified:** shaping confirmed under cap on all 24 published rows
(`shaper_verified_not_over_cap`); demand gate 24/24 PASS against what the
delivered resolution actually costs; source ladder probed at 2160p so the
resolution result is not bounded by the source; chart hashes in
`results/streaming_manifest.charts.txt`.

**Assumed, not verified:** the source ladder was probed once and assumed stable
across the sweep.

**The 720p ceiling is now explained — it is the player.** Four runs on one
300 Mb/s / 1 ms link (`results_1080p/yt1080_link_runs.csv`, this PR) separate
the candidates: on auto the player serves 720p while reporting 1080p as its
own optimal, drawing 2.56–2.68 Mb/s of a 300 Mb/s link (0.9%) on a link that
peaked at 223 Mb/s when asked; pinning the rendition on the same URL and link
delivers 1920x1080 for 60 of 60 samples with **zero** dropped frames and the
fastest startup of the four. So it is not the link, not the video, not our
collection, and not the decoder. See `results_1080p/README.md`.

**Non-monotonic row, stated plainly:** at 100 ms, **1 Mb/s delivered 360p while
1.5 Mb/s delivered 240p** — less bandwidth, higher resolution. Both are labelled
unusable, and the 1 Mb/s run also took one rebuffer against the 1.5 Mb/s run's
zero, so the labels are ordered even though the resolutions are not. With n=1
this is within what ABR run-to-run variance can produce; it is not evidence
that lower capacity helps.

**Open:** Vimeo is not swept, so the plan's three streaming apps are two.
Twitch is swept in this PR — the section below. It was unblocked — the pinned channel is `lofigirl` as of this PR,
in **both** registries (`shared/apps.py`, which is what the collector opens,
and `APP_REGISTRY["twitch"]`), probed live at a full ladder to 1920x1080 where
`relaxbeats` topped out at 720p and would have aborted at the source gate. Its bitrate is **not**
comparable to YouTube's (near-static animation encodes 1080p far cheaper), so a
Twitch sweep compares the *shape* of degradation, and demand only within the
app. Neither channel is a Twitch partner, so re-probe the ladder before the
campaign: a source-only stream has one rendition and the sweep would measure
buffering rather than adaptation.

---

## Twitch — streaming (this PR)

**Run:** the same 8 capacities as YouTube (100, 25, 12.5, 6.25, 3, 1.5, 1,
0.5 Mb/s) at 20 ms, **3 repetitions each**, channel pinned to `lofigirl`,
upload fixed at 10 Mb/s. 24 app runs + 24 ndt7 runs, all 48 valid, none
invalid, ~2 h wall clock. Tables and charts in `results/twitch_*`; the
per-repetition rows are in `twitch_runs_table.csv`, the reduced table in
`twitch_iqb_vs_qoe_table.{csv,md}`.

**Source gate passed before the sweep started.** Re-probed live: 5 variants,
**4 transcodes**, 1080p24 source at 6.92 Mb/s plus 720p30 (2.37), 480p30
(1.43), 360p30 (0.63) and 160p30 (0.23). A source-only ladder would have
aborted the sweep, because it would have measured buffering rather than
adaptation. Recorded in `twitch_renditions.json`.

**Headline:** Twitch held **1080p with zero rebuffering and sub-5 s startup
down to 12.5 Mb/s in all three repetitions — half IQB's 25 Mb/s minimum** —
while a speed test on the same link calls it failing. It breaks one step
lower: at 6.25 Mb/s the player keeps asking for 1080p against a 6.9 Mb/s
source, rebuffers 1–2 times and starts in 6.2–6.7 s (`acceptable`); at 3 Mb/s
and below every repetition is `unusable`, on startup rather than on rendition.

**The repetitions agree.** Across all 8 capacities the three reps agreed on
the QoE label and on the delivered resolution, every time. The spread that
exists is in startup (±0.4 s at the top, ±15 s at 0.5 Mb/s) and in rebuffer
counts at the two breaking points. `twitch_rep_spread.png` draws every run
rather than a mean.

**Twitch is more demanding than YouTube, and the comparison is about shape.**
YouTube held its (720p) ceiling down to 6.25 Mb/s; Twitch holds 1080p to
12.5 and breaks at 6.25. The bitrates are not comparable — lofigirl is
near-static animation and YouTube's player would not leave 720p at any
capacity — so what transfers is the shape: both players hold their chosen
rendition far below IQB's minimum and then fail on **startup**, not on
resolution.

**Verified:** 48/48 runs valid; shaping confirmed under cap on every published
row; source ladder probed live and re-checked against
`conditions.TWITCH_PROBED_LADDERS_P`; all 24 NDT runs completed on the first
attempt with no M-Lab rate limiting (starts spaced ≥300 s); demand gate run
over **both** published tables, 18/24 and 6/8 PASS with the two flagged points
explained below; chart hashes in `twitch_manifest.files.txt`, transfer hashes
in `twitch_manifest.{remote,local}.txt`.

**Demand gate: 2 of 8 points flagged, and the reason is in the data.** At
1.5 Mb/s and 1 Mb/s the measured demand (1.06 and 0.78 Mb/s) sits above the
gate's 0.60 Mb/s ceiling for 144p. Neither figure is wrong. `resolution_p` is
the **modal** sample height (`shared/qoe.py:514`), and those sessions were not
at one rendition: at 1.5 Mb/s the player sat at 144p for 36 of 60 samples and
then climbed through 360p to 480p, **still rising when the window closed**.
The demand mean covers the whole session including the larger later segments.
The gate's own note anticipated the opposite case — a session that downshifts
carrying its earlier, larger segments — and the direction here is the reverse.
Every row now publishes `resolution_modal_share`, `resolution_final_p` and a
per-run `resolution_timeline`, so a steady point and a ramp can be told apart.

**Contamination, stated rather than hidden:** the background-traffic guard
failed on **11 of 24** runs, all at 3 Mb/s and below — Chrome's own non-media
traffic takes more than 10% of the cap in more bins than the budget allows
once the cap is small enough. Those runs are **flagged, not discarded**
(`⚠n/m` in the table, `reps_suspect` in the CSV). Over the whole window the
infra share is 5% at 3 Mb/s rising to 18% at 0.5 Mb/s. The headline rests on
12.5 Mb/s and above, where no repetition was flagged and infra share is 2%.

**What this does not say:** one latency (20 ms), one channel, one hour of one
day. `lofigirl` is not a Twitch partner, so the ladder can lose its transcodes
at any time — the sweep re-probes and aborts rather than silently measuring a
source-only stream, but a future run may abort for that reason.

---

## Google Meet — conferencing (PR #20)

**Run:** 6 capacities (100, 25, 6, 3, 1, 0.5 Mb/s symmetric) × 3 latencies =
**18 cells**, 30 app runs (some cells retried). Of the 18: **13 have measured
quality**, 3 carried remote video the collector failed to sample, and 2
captured no packets at all. The planned 12.5 Mb/s point was never run.

*(This replaces "20 of 24 runs collected". 24 was never the size of this
matrix — the Meet plan is 6 capacities, not the streaming study's 8 — and
neither number could be reproduced from the data. The counts above come
straight from `meet_cpuc_runs.csv` and the demand gate's own 18-row total.)*

**Headline:** Meet held 720p at 24 fps with freezes under 1% down to **3 Mb/s
symmetric, 8x below IQB's 25 Mb/s conferencing minimum**. It breaks between 3
and 1 Mb/s: at 1 Mb/s, 360p at 20 fps with 18% of the session frozen. 1 Mb/s is
the lowest capacity with a quality verdict; below it quality is unmeasured.

**Correction, 2026-11-08 — "at 0.5 Mb/s no remote video arrives at all" was
wrong.** It is deleted, not reworded. Remote video *was* arriving: re-derived
from the captures, 2.2–3.2 MB of inbound media (1,671–2,442 full-size packets)
came from Meet's own endpoints in **both** attempts at 1 Mb/s @ 50 ms,
0.5 Mb/s @ 20 ms and 0.5 Mb/s @ 50 ms, against controls of 17,588 such packets
on the known-720p reference run and 4,943 on the 360p run at 1 Mb/s. What
actually happened is that the collector produced no samples, so **quality at
those three points is unmeasured, not absent**. The original claim was inferred
from the collector having exited non-zero, which cannot tell a dead collector
from a silent peer — the fourth instance of that class this week, and the first
one to reach a published finding. The two 100 ms points previously called
*confounded* are **invalid runs**: their captures contain no inbound packets of
any size, and zero packets is not a measurement.

**What survives unchanged:** the 3 Mb/s headline, the 8x factor, the 1 Mb/s
breakpoint and every row with a quality verdict. All 13 measured rows are
untouched, the demand gate still passes 13/13, and `meet_finding.json` is
byte-identical to the one published with #20. The correction removes a claim
about the bottom two capacities; it does not move the finding.

**The asset-cost finding.** Meet's own page assets — `fonts.gstatic.com`
(48.4%), `www.google.com` (16.9%), `www.gstatic.com` (14.3%) — cost a
near-constant **4.2–6.7 MB per run regardless of capacity**. Because the cost is
fixed and the link is not, the share grows as the link shrinks:
`infra_share_pct` is **12–26% at 3 Mb/s and above, but 38–56% at 1 and
0.5 Mb/s**. At the capacities where the call fails, the page is competing with
the call for a comparable slice. This is a property of loading Meet cold, so a
warm browser profile would remove most of it; it is not a property of
conferencing at low bandwidth, and the low-capacity rows should be read with
that in mind.

**Verified:** `adopt_unnamed_ips` is applied and working (95.4–99.5%
attribution per run); the unnamed `142.251.x.119` talker is `www.google.com`,
read from the captures' own DNS answers and confirmed by SNI in a QUIC-off run;
demand gate 13/13 PASS, 5 skipped (3 unmeasured, 2 invalid runs — the gate
checks a demand figure against the rendition it bought, and those rows have no
rendition, which is also why their demand column is blank).

**Assumed, not verified:** the source clip was 720p, not 1080p, so these runs
do not test whether Meet would hold 1080p. Received resolution never exceeded
720p even on the reference link, so the clip bounded resolution equally at
every point.

**Caveats that must travel with the table:** 6 Mb/s at 50 ms was measured twice
and the runs disagree (360p/acceptable, then 720p/high); the table keeps the
later one and it is the one number here not to lean on. The three
`video arrived, unmeasured` rows are holes in the curve, not low points on it —
reading them as quality failures is exactly the mistake corrected above.

**Open:** what Meet actually delivered below 1 Mb/s is still unknown, and
answering it needs the collector fixed and those three points re-run, not more
analysis of these captures. The captures show video arriving; they cannot show
what it looked like.

---

## Microsoft Teams — conferencing (PR #22, Sthanav)

Not our study. 21 conditions, 1 rep, in
`experiments/cpuc/results/teams_*`. See #22 for the data and the write-up.

One row worth naming because it looks like an error and is not: **Teams was
`high` at 4 Mb/s and `acceptable` at 8 Mb/s at 20 ms** — better quality on the
slower link. Resolution is 540p in both, so the difference is in fps and
freezes, not rendition. Same n=1 caveat as everything above.

---

## Corrections

Kept here rather than edited away, because a corrected claim that leaves no
trace is indistinguishable from one that was never made.

- **2026-11-08 — Meet, "at 0.5 Mb/s no remote video arrives at all."** Wrong,
  and published in #20, its table, and here. Video was arriving; the collector
  produced no samples. Replaced by "quality unmeasured" on three rows and
  "invalid run" on two. Derivation moved from the collector's exit status to
  the capture. No measured row and no headline changed. Details in the Meet
  section above.
- **2026-11-08 — `media_bytes` reported `{'meet': 0}` for every Meet run,
  including the known-720p control.** The figure matches a remote *host name*
  against a suffix list, and WebRTC media endpoints are chosen by ICE and never
  named in DNS, so there was never anything to match. It was a silent zero, not
  a shortfall. Conferencing apps now get no entry at all rather than a zero,
  and the one consumer (`make_release.py`'s R4) says which side is missing. No
  published number used it.
- **2026-11-08 — the first Twitch repin changed the wrong registry.** The
  commit moved `APP_REGISTRY["twitch"]["url"]` and wrote a `conditions.py`
  comment saying the channel was pinned, but a real-Chrome run opens
  `shared/apps.py`'s `default_url` via `build_collector_jobs`, which still
  said `relaxbeats` — so the sweep would have aborted at the source gate
  while the comment insisted it would not. Caught in review, not by me. Both
  registries now carry the pin, the probed ladders are recorded as data in
  `conditions.py`, and seven tests pin the two registries, the comment and
  the source gate together. Second time a comment has asserted something the
  code did not do; the GGC double-r regex was the first.
- **2026-11-08 — Tubi was pinned to two different films.** `shared/apps.py`
  named `568500/forrest-gump` and `APP_REGISTRY` named
  `593796/another-cinderella-story`, so which movie a run measured depended on
  which code path drove it. **No Tubi run was ever collected** — 7 dataset
  indexes on vm4, 284 runs, zero Tubi; no Tubi row in any CSV; the release
  matrix is YouTube and Vimeo only — so no result mixed them. Probed live:
  **both titles are now dead** (`"video_resources":[]`, a "Remind Me" button,
  no `contentUrl`, against a control that has all three). Both registries now
  name one title, verified playable, with its expiry date recorded.
- **2026-11-08 — two docstrings described code that does not exist.** Found by
  scanning every comment and docstring in `experiments/` for names the code
  never defines. `runner.py` credited the work to `execute_run` (the real ones
  are `execute_app_run` / `execute_ndt_run` / `execute_condition_rep`), and
  `generate_run_plots` claimed "no per-app throughput variants exist" directly
  above the loop that writes `throughput_<dir>_<app>.png`. Third and fourth
  instances of the class review caught twice.
- **2026-11-11 — `startup_s` was blank in all three published 1080p
  supplementary rows.** `supp1080.py` read `player_qoe[app]["startup_delay_ms"]`,
  a key that does not exist at that level — it lives under the `uniform` block,
  which is where `qoe_labels` reads it. The lookup returned `None` every time
  and the CSV column came out empty, indistinguishable from "not measured".
  It **was** measured: 3.07 / 2.16 / 3.65 s, sitting in each record all along.
  Both readers now use the labeller's own definition, and the three published
  rows were backfilled from their own records rather than re-run (originals
  kept as `.pre-startup-backfill`). No label changed. Fifth instance of the
  silent-zero/silent-absence class.
- **2026-11-11 — `shaping.py` described a latency split this harness has
  never done.** Its docstring said netem delay is "split HALF PER DIRECTION"
  with ingress redirected to an `ifb0`, which describes the borrowed reference
  implementation. The live layout on vm4 refutes it: one netem on `ns2:veth3`
  carrying the whole figure, `ns1:veth1` `noqueue`, no `ifb0` on the box, and
  1 ms configured measuring 1.13 ms RTT. It also contradicted
  `conditions.latency_for_platform()` in the same package, so a reader who
  found `shaping.py` first would have concluded the opposite of the truth. The
  docstring now states the measured behaviour, including that the worker's
  `latency_location` labels are node positions rather than traffic directions
  (`"upstream"` → `ns2:veth3` → the *download* path) and that `"both"` doubles
  the RTT rather than splitting it. **No published number changes** — all 785
  archived runs were taken this way and every table already carried the
  measured RTT. Fifth instance of the comment-asserts-what-the-code-doesn't-do
  class.
- **2026-11-08 — two of the five Meet charts on `main` could not be
  reproduced by `main`'s own code.** They were generated before #20's and
  #21's `plots.py` were merged. Regenerated and re-hashed in
  `meet_manifest.charts.txt`. The streaming charts all reproduced exactly.

## What would change these conclusions

- More than one run per point. **Twitch now has three**, and the three agreed
  on every label and every rendition, which is evidence that the single-run
  numbers are not noise — but it is evidence from one app at one latency. The
  YouTube and Meet headlines still rest on single runs.
- ~~The 720p ceiling being explained.~~ Explained in this PR: it is the
  player, not the link, the video, our collection or the decoder. The
  streaming numbers do describe **YouTube-at-720p**, by the player's choice,
  on a link with 100× the headroom it used.
- Vimeo. Two streaming apps are not the plan's three, and the two we have
  disagree about where quality breaks (YouTube 6.25 Mb/s, Twitch 12.5), which
  is a reason to want the third rather than a reason to doubt either.
- A second latency for Twitch. Everything above is 20 ms.
