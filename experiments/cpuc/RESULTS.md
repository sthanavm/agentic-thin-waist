# CPUC bandwidth-threshold results

Running log of what we have measured against IQB 2025 Fig 10. Updated as
things land — not regenerated, so the history of what changed stays readable.
Work this study found and did not do is in [FOLLOWUPS.md](FOLLOWUPS.md).

**Every point in every study is n=1.** One run per (app, capacity, latency).
Where two runs exist for a cell it is because the first failed, and the table
keeps the valid one. Nothing here has a confidence interval, and the
non-monotonic rows named below are the visible cost of that.

Common to all three studies: netem adds its delay on top of the real path to
M-Lab, so a 20 ms setpoint measures 22.6–23.3 ms, 50 ms measures 52.5–53.3 ms
and 100 ms measures 102.5–103.2 ms. The 50 ms rows therefore sit **over** IQB's
50 ms conferencing minimum, and the 100 ms rows **over** its 100 ms streaming
minimum, by 2.5–3.3 ms. Every table carries both the configured and the
measured RTT for this reason.

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
across the sweep. The 720p ceiling on a 100 Mb/s link is unexplained — it is
not the viewport (tested) and not host decode (a Vimeo run served 1920x1440 in
AV1 on the same host), so it is a property of this YouTube session we have not
pinned down.

**Non-monotonic row, stated plainly:** at 100 ms, **1 Mb/s delivered 360p while
1.5 Mb/s delivered 240p** — less bandwidth, higher resolution. Both are labelled
unusable, and the 1 Mb/s run also took one rebuffer against the 1.5 Mb/s run's
zero, so the labels are ordered even though the resolutions are not. With n=1
this is within what ABR run-to-run variance can produce; it is not evidence
that lower capacity helps.

**Open:** Twitch and Vimeo are not run, so the plan's three streaming apps are
one. Twitch is now unblocked — the pinned channel is `lofigirl` as of this PR,
in **both** registries (`shared/apps.py`, which is what the collector opens,
and `APP_REGISTRY["twitch"]`), probed live at a full ladder to 1920x1080 where
`relaxbeats` topped out at 720p and would have aborted at the source gate. Its bitrate is **not**
comparable to YouTube's (near-static animation encodes 1080p far cheaper), so a
Twitch sweep compares the *shape* of degradation, and demand only within the
app. Neither channel is a Twitch partner, so re-probe the ladder before the
campaign: a source-only stream has one rendition and the sweep would measure
buffering rather than adaptation.

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
- **2026-11-08 — two of the five Meet charts on `main` could not be
  reproduced by `main`'s own code.** They were generated before #20's and
  #21's `plots.py` were merged. Regenerated and re-hashed in
  `meet_manifest.charts.txt`. The streaming charts all reproduced exactly.

## What would change these conclusions

- More than one run per point. Every headline above rests on single runs.
- The 720p ceiling being explained. If it is a session property rather than a
  link property, the streaming numbers describe YouTube-at-720p, not YouTube.
- Twitch and Vimeo. One streaming app is not the plan's three, and YouTube's
  ABR behaviour is not obviously general.
