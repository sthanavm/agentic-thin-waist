# Pramana — project memory

Read this first. Update it after every meaningful change so the next session
starts from what is already known instead of rediscovering it.

## What this is and who it's for

A QoE dataset for the CPUC broadband affordability work: how video streaming
actually behaves for a viewer on a constrained connection. Supervisor is Jaber;
the professor is Arpit Gupta.

The point is one measurement method that works the same way on any streaming
site, rather than a different scraper per site. Scope is four apps — YouTube,
Vimeo, Tubi and Twitch. YouTube and Vimeo are the ones being delivered first.
Conferencing (Zoom, Meet) belongs to Harika and Sthanav and is not part of this
work.

Five metrics per run: startup delay; buffer level and rebuffering; average
delivered bitrate split into video and audio; rendition switching with a
timestamp and a direction; and the frame rendering rate. Dropped frames are
kept as raw counts only, and throughput is deliberately not treated as a QoE
metric.

## How the VM and tools are used

Everything runs on the lab VM, never on a laptop. Connect with `ssh vm4`.

The experiment driver is `pramana_helpers.run_direct`, which shapes the link,
starts a packet capture, launches a real headed Chrome inside a network
namespace, samples the player once a second, and writes a run directory.

```bash
cd experiments/pramana
DUR=180 python run_matrix.py        # the Step A sweep
python make_release.py results_release <run_dir> ...   # charts, summaries, checks
```

`PRAMANA_COLLECTOR_SRC` points the container at a local `collect.py` so the
2 GB image does not have to be rebuilt to test a change.
`PRAMANA_DEFER_ANALYSIS=1` postpones plotting so a long sweep is not slowed by
it.

Keep an eye on disk. The VM is small and a sweep fills it quickly; back results
up and verify by checksum before deleting anything.

## Jaber's rules

1. Throughput must never exceed the shaped cap, peak or average.
2. Save a pcap for every run; never commit pcaps.
3. Separate download and upload plots. Video is download only.
4. Show buffer level.
5. Show resolution over time.
6. Flag NO_PLAYBACK and regenerate rather than quietly keeping a dead run.
7. With several apps, the cap is the shared total, not per app.
8. One pcap per app, split by host or IP; one plot per app, never combined.
9. Cross-traffic profiles are not implemented. Say so; do not fake one.
10. Use a high-bitrate source so quality differences are visible.
11. Run on the VM.
12. Everything reproducible: scripts, commands, versions.
13. Cross-validate an anomaly before calling it a finding.

Charts are line graphs, never filled, with a fixed 0 to 11 Mbps axis on
throughput and clipped to the playback window.

## What we tried, and what happened

**Startup delay.** Approach 1: take the first sample where the playback clock
moved. This gave about one second on every run regardless of bandwidth, which
was suspicious. Revision: it turned out to be an artefact — the method cannot
report anything below one sampling interval, so it was measuring the sampling
rate, not the site. We switched to the first frame actually presented for
composition, which moves the way you would expect: on Vimeo it went from about
twenty-one seconds at 1 Mbps down to about two seconds at 10 Mbps. The old
figure stayed flat across that whole range, which is how we knew.

**Bitrate reference.** Approach 1: compare the player's appended bytes against
the bytes in the packet capture. This failed, and chasing it cost a lot of
time. The capture only shows addresses, and the media connection often has no
name attached to it, so deciding which traffic was "the video" meant guessing.
Worse, one run produced a figure below the floor that is physically possible,
which proved the reference itself was incomplete rather than the metric being
wrong. Revision: ask Chrome directly, through its own network instrumentation,
which knows the hostname of every request it made. That agrees with the
player's appended bytes very closely. One detail matters: Chrome only reports a
request's final size when the response finishes, so a download still running
when the measurement ends is missed. Using the running byte count instead of
the completed one fixes it. The capture is still recorded, but as a
cross-check, never as the judge.

**Per-app pcap splitting.** Approach 1: we believed this was done, because the
pipeline printed a per-app split step. Revision: checking the actual files
showed it only split the byte accounting used for plots — there was still one
capture per run containing the page, DNS and every third-party host. We now
write a real per-app capture file containing only that app's packets.

**Window and viewport.** Approach 1: let the browser window be whatever it was.
Then YouTube at 3 Mbps gave 480p on one run and 720p on another, and we called
it run-to-run variation. Revision: fixing the window made all five trials
identical, and the spread in delivered bitrate fell sharply. Player size, not
chance, was driving which rendition the site served. Worth knowing: fixing the
window does not fully fix the video element's size, so it is a partial control
and the measured element size is recorded per run.

**Codec per run.** Approach 1: record the codec string the page declared when
it set up its buffer. Revision: that is only what the page said it *might*
send. YouTube changed rendition several times without ever announcing it
through the API we were watching, so the log stayed empty while the picture
visibly changed. We now read the codec out of the initialisation data the
player feeds its decoder, which is where the real answer lives and works on any
site. One limit: for VP9 inside WebM the container simply does not carry the
profile, so only the codec family is knowable there.

**The AV1 to VP9 claim — retracted.** We reported that YouTube switches from
AV1 to VP9 somewhere between 1.5 and 3 Mbps. Repeat runs did not reproduce it,
and the window work above explained why: at 3 Mbps you get one or the other
depending on how big the player is. It is not a bandwidth threshold and the
claim stays withdrawn.

**Tubi.** Approach 1: use the title that was hard-coded in the app list. No
video element ever appeared and we assumed a consent or bot wall. Revision: the
page was fine — the film had simply been removed from the catalogue, and the
site still returns a normal-looking page saying the content is unavailable. A
current title plays immediately. Tubi still does not give us startup or
rendering rate, because it swaps its video element and the frame callback does
not follow it across the swap.

**Twitch.** Delivered bitrate cannot be measured there at all. Twitch runs its
media handling inside a worker and hands the video element a ready-made stream,
so there is nothing for a byte hook to attach to. It is reported as unavailable
with that reason, never as zero. The other four metrics work.

**Measuring without disturbing.** We added Chrome's network instrumentation and
then found it had slowed the once-a-second sampling, occasionally badly. A
comparison against earlier runs confirmed our own change caused it, so we
collect that data less often now. Chasing the rest of it took three wrong
guesses, each ruled out by a test: it was not the collection cadence (the
stalls did not line up with it), not the cost of totting up the byte table
(caching that made it thirty times faster and changed nothing), and not the
number of resource entries (Vimeo has more of them and never stalls). What the
data actually showed is that every stall sat in the first two or three samples
of a run and never later. Part of it was ours — on starting up we were parsing
the whole backlog of events the page had generated while loading, about a
second and a quarter on YouTube — and that now happens before sampling begins.
The remainder is the page itself: YouTube is still busy when playback starts,
and on a two-core machine our once-a-second reading has to wait its turn.
At 1 Mbps it does not happen, because the page has finished loading long
before playback begins. The steady state is healthy everywhere, a little over
one second. We did not move the bar or delay the start to make this pass; it is
reported as a failure on the three runs where it crosses.

**The sampling cadence, and what it turned out to be.** The once-a-second
sampling was slipping on YouTube at 3 and 6 Mbps — gaps of two to three
seconds — and three guesses about why were all wrong, each killed by a
measurement rather than an argument. So we put timers on each phase of the
loop and let the numbers say it.

Two of the causes were ours and are fixed. The loop slept a flat one second
*after* doing its work, so the real period was work plus a second and every
millisecond of work pushed the whole series later; it now sleeps to the next
tick on a fixed clock. And every sample made two separate requests to the
browser, one for the page address and one for the statistics; the address now
comes back with the statistics, halving the requests that have to wait their
turn.

The third cause is not ours. Asking the page for its statistics is a
synchronous call, so when the browser's main thread is busy it simply waits —
and on this machine it waited a very long time. One sample waited past the
thirty-second limit and gave up entirely. The reason is that the machine is
shared: something else on it keeps about a hundred and sixty browser processes
alive, and while they are busy the run queue sits far above what two cores can
serve, with no idle time at all. Our own browser is a dozen processes of that.

The control proves the split. At 1 Mbps, where the load was lighter, the
cadence is now essentially perfect — one second, dead on. At 3 and 6 Mbps,
where the load was roughly twice as high, it still fails. We did not paper over
this by shortening the browser's timeout or by starting the measurement later;
both would make the check pass while hiding that the browser was starved.

It also matters beyond the cadence: under that load the runs themselves come
out wrong. The same 3 Mbps cell that gives 720p and no rebuffering on a quiet
machine gives 480p and several rebuffers on a busy one. So those re-runs were
treated as diagnostics and deliberately not folded into the results; the kept
results are the ones measured while the machine was quiet. Every sample now
records the machine's load alongside the measurement, so this confound is
visible in the data instead of invisible.

**The sampler, before and after.** The old sampler did its work and then slept
a flat second, so the time between readings was the work plus a second rather
than a second, and anything slow pushed the whole series later. It also asked
the browser two separate questions each time, one for the page address and one
for the statistics, and only the second was timed — which is why a forty-second
reading once looked like a five-second one. The new sampler sleeps to the next
tick on a fixed clock, asks one question instead of two, times every phase, and
puts a ten-second ceiling on a single reading so one stuck call cannot swallow
most of a run. On a quiet machine the result is a reading every 1.01 seconds,
which is as close to exact as this is going to get.

**The shared machine, and why it is recorded now.** The measurements sit on a
two-core box that we share. Another project keeps about a hundred and sixty
browser processes alive on it; when they are busy the queue of work waiting for
a core runs far past what two cores can serve, and our reading of the page —
which has to wait its turn — can stall for tens of seconds. With no collector
of ours running at all, the machine already sits at about three.

This is not only a nuisance for the sampling rate. It changes the result. The
same YouTube cell at 3 Mbps gives 720p with no rebuffering when the machine is
quiet, and 480p with several rebuffers when it is not. Treating those as the
same measurement would be wrong.

So a run now waits for the machine to settle before it starts, and will not
begin until the load has stayed low for three unbroken minutes. If it never
settles within twenty minutes the run goes ahead anyway and is labelled a
high-load run — kept, clearly marked, and never quietly mixed in with the rest.
Every run records what the load actually was while it ran, so this is visible
in the data rather than something a reader has to guess at.

**Reading the bitrate chart.** The per-interval line spikes well above the
link speed — over 40 Mbps on a 6 Mbps link — because a player fetches a whole
segment at once and hands it to the buffer in one go. That is not throughput
and should not be read as such. The chart now draws the reported average on
top as a flat labelled line, with the link cap alongside, so the number in the
summary is visible on the picture.

## Where things stand

YouTube and Vimeo produce all five metrics. Tubi produces three of five — no
startup, no rendering rate, for the element-swap reason above. Twitch produces
four of five, missing delivered bitrate by construction.

Latency and AQM sweeps have not been run; bandwidth is the only axis so far.
Cross-traffic profiles are still waiting on Jaber. Multi-app runs where several
apps share one cap have not been exercised.

Next: Step B, the latency and AQM sweeps, then bringing Tubi and Twitch into
the same release once their gaps are either fixed or documented as permanent.
