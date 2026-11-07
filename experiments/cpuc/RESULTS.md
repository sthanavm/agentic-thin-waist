# CPUC bandwidth-threshold results

Running log of what we have measured against IQB 2025 Fig 10. Updated as
things land — not regenerated, so the history of what changed stays readable.

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
one. Twitch needs its pinned channel changed first — `relaxbeats` tops out at
720p and would abort at the source gate; `lofigirl` offers a full ladder to
1920x1080.

---

## Google Meet — conferencing (PR #20)

**Run:** 6 capacities (100, 25, 6, 3, 1, 0.5 Mb/s symmetric) × 3 latencies.
**20 of 24 runs collected** — the meeting ended during the 100 ms batch and the
drop guard stopped rather than burning runs.

**Headline:** Meet held 720p at 24 fps with freezes under 1% down to **3 Mb/s
symmetric, 8x below IQB's 25 Mb/s conferencing minimum**. It breaks between 3
and 1 Mb/s: at 1 Mb/s, 360p at 20 fps with 18% of the session frozen; at
0.5 Mb/s no remote video arrives at all.

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
demand gate 13/13 PASS, 5 skipped for no remote media.

**Assumed, not verified:** the source clip was 720p, not 1080p, so these runs
do not test whether Meet would hold 1080p. Received resolution never exceeded
720p even on the reference link, so the clip bounded resolution equally at
every point.

**Caveats that must travel with the table:** 6 Mb/s at 50 ms was measured twice
and the runs disagree (360p/acceptable, then 720p/high); the table keeps the
later one and it is the one number here not to lean on. The 1 and 0.5 Mb/s
points at 100 ms are **confounded** — the peer stopped publishing — and are
marked as such in the table rather than only in prose; the same two capacities
at 50 ms failed with the peer verified still publishing, and those are genuine.

**Open:** the six low-capacity rows behind "no remote media" come from runs
whose collector exited non-zero, which the new validation now treats as
unusable. "No remote video arrived" needs re-deriving from the capture rather
than inferred from the collector having died.

---

## Microsoft Teams — conferencing (PR #22, Sthanav)

Not our study. 21 conditions, 1 rep, in
`experiments/cpuc/results/teams_*`. See #22 for the data and the write-up.

One row worth naming because it looks like an error and is not: **Teams was
`high` at 4 Mb/s and `acceptable` at 8 Mb/s at 20 ms** — better quality on the
slower link. Resolution is 540p in both, so the difference is in fps and
freezes, not rendition. Same n=1 caveat as everything above.

---

## What would change these conclusions

- More than one run per point. Every headline above rests on single runs.
- The 720p ceiling being explained. If it is a session property rather than a
  link property, the streaming numbers describe YouTube-at-720p, not YouTube.
- Twitch and Vimeo. One streaming app is not the plan's three, and YouTube's
  ABR behaviour is not obviously general.
