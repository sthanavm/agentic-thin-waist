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

**The machine was never clogged with Chrome.** We spent a while blaming a
hundred and sixty stray browser processes on the shared machine for the
sampling stalls, and planned to clean them up. An audit showed there was
nothing to clean: every one of them had already exited and was simply waiting
for its parent to collect it. Processes in that state hold no processor time,
no memory and no file handles, and they do not count towards the machine's
load; trying to kill one is refused, because it is already dead. An earlier
note of ours put seventy-five percent of the processor on them, which was
wrong — that figure came from adding up each one's lifetime average at the
moment it died, not anything it was using. The congestion we measured was real,
but it came from our own browser and from live work by the other project on the
machine. Nothing was changed, so later runs are not on a "cleaned" machine, and
we do not describe them that way.

**What actually fixed the sampling.** Once the sampler itself was corrected —
a fixed clock instead of sleeping a flat second after the work, one question to
the browser per reading instead of two, a ceiling on a single reading, and a
final collection of byte counts so a segment arriving in the last second is not
missed — the readings came back at almost exactly one second. Eleven of the
twelve cells now hold the cadence. The one that does not misses by a hundredth
of a second, on YouTube at 3 Mbps, and is reported as a failure rather than
rounded into a pass.

**Nothing was lost in the gaps.** We checked whether the stalls had swallowed
any rebuffering or quality switches. They had not. Rebuffering is recorded from
the page's own event log, which runs from the moment the page loads and does
not depend on our reading rhythm, so stalls that happened inside a gap were
still counted. No quality change straddled a gap either. One run looked like it
might have lost a switch, but it had no gaps at all — the difference came from
two renditions sharing the same picture height, which a height-based count
cannot see by design.

**The last stubborn hundredth of a second.** One cell kept missing the
once-a-second cadence by a whisker — two and a bit seconds against a two second
limit. Looking at where the gap actually fell, it was five seconds into the run,
right in YouTube's noisy start-up, and the reading before it had taken about one
and a quarter seconds.

The cause turned out to be our own scheduler, not the page. When a reading
overran its slot, the scheduler waited for the *next* slot instead of taking the
reading straight away. So a reading that ran a quarter of a second long did not
cost a quarter of a second — it cost a whole extra slot, and one reading was
never taken at all. Any overrun at all, however small, produced a gap of
exactly two slots.

Now, when a reading overruns, the next one is taken immediately and the rhythm
picks up from there. A slow reading costs only the time it actually took, and
no reading is skipped. Nothing changes when readings are quick, which is almost
always: the typical one takes about sixty milliseconds.

Six fresh runs of the troublesome cell, each waiting for the machine to settle
first, all held the cadence. Three of them had readings that overran — about
1.3 to 1.5 seconds — and under the old scheduler each of those would have
become a two-second gap and failed. We did not touch the two-second limit.

Vimeo was left alone deliberately: its readings never once overran a slot (the
slowest was under half a second), so the change cannot have altered anything
there, and re-running it would have added nothing.

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
Cross-traffic profiles are still waiting on Jaber.

One multi-app run now exists: YouTube and Vimeo together behind a single
6 Mbps cap. It is one trial, not three, so nothing about it is settled.

Next: three trials per cell, then Step B, the latency and AQM sweeps, then
bringing Tubi and Twitch into the same release once their gaps are either
fixed or documented as permanent.

## Running two apps on one cap

The cap is the total for the link, not an allowance per app, so the check that
matters sums both apps on a common one-second grid before comparing against
the cap. Checking each app on its own would pass whenever neither exceeded the
cap alone, which is exactly the case the shared cap is meant to catch. Summed,
the two apps peaked at 5.9954 of 6 Mbps.

Two things broke the first time and are worth remembering.

The folder name left out the other app, so a concurrent run and a solo run at
the same app and speed produced the same directory and one quietly overwrote
the other. Concurrent folders now carry the other app's name.

Attribution was asked about one app at a time. With a single name to work
with, the attributor handed the same unnamed flow — one address, twenty
megabytes — to whichever app was being built at that moment, so the same bytes
were counted twice, once for each app. It is now given the full list of apps
in the run, and the two apps' addresses no longer overlap at all.

A barrier holds both apps until each has a player ready, so neither spends its
window waiting for the other to load. It does **not** give them the same
window. YouTube's first reading came 18.9 seconds after Vimeo's, leaving it
161 seconds against Vimeo's 179. Each app's numbers are worked out over its
own window, and the cap check does not assume the windows match. The earlier
claim in the code that both started "at the same instant" was simply wrong and
has been removed.

## The two-second gap on the concurrent YouTube run

One reading in that run sat exactly two seconds after the one before it, which
fails the two-second bar. We did not move the bar. The run records how long
each iteration spent working, so the cause is readable rather than guessed.

The gap is not the scheduler. Of the 2000 milliseconds, 1998 were spent inside
the iteration itself and only 2 were left waiting, so the loop slept for
practically no time at all. Almost all of that work — 1967 milliseconds — was a
single call into the page to read the player's statistics. The loop took the
next reading the moment it was free, which is what it is supposed to do.

At the time we thought the following explained it. The repeats below show
it does not explain the worst cases, so treat this paragraph as describing the
first run only.

What makes that call slow looked like a combination, not one thing. YouTube's statistics
call is heavier than Vimeo's in every run we have: typically 37 to 57
milliseconds against Vimeo's 28 to 44, and with a far longer tail. Host load
stretches that tail. During the concurrent run the one-minute load averaged
9.4 and peaked at 11.1, because two browsers were sharing two processors, and
the slow call landed at 10.8. Vimeo, on the same machine in the same window,
never passed 496 milliseconds.

The load gate cannot prevent this. It checks the machine is quiet *before* a
run starts, and it was — 2.84. The load that caused the problem is made by the
run itself.

## What three repeats then showed, and what it corrected

The cell was run three more times, one at a time, each behind the same gate.
The result changes the picture above, so read this part rather than that one.

YouTube failed the cadence bar in **all four** runs. Vimeo passed in all four.
The first run, the one kept in the release, turned out to be the mildest of
them at exactly two seconds. The repeats reached fourteen, twenty and
twenty-four seconds. So the failure is not the borderline thing a single run
made it look like, and it should never be described that way.

The shape of it is also different from what the first run suggested. In the
repeats the stall lands in the first two or three readings rather than in the
middle, and it is not a gradually stretched call. A single reading of the page
blocks for fourteen to twenty-four seconds. The ten-second limit we put on
those calls did not contain it: twice it returned late, and once a reading
finished successfully after fourteen seconds without the limit firing at all.
The reading that stalls comes back with no statistics in it, and straight
afterwards the player's clock is back at zero, which is the same restart we
saw in the first run without understanding it.

Vimeo on the same machine in the same seconds never passed 832 milliseconds,
so this is specific to the YouTube path and not simply the machine being busy.

The gate cannot help here. It passed at between 2.5 and 2.9 every time, and
the load that accompanies the failure — around ten — is made by the run
itself, two browsers sharing two processors.

What is settled: it reproduces, every time, on YouTube only. Why it happens
was answered afterwards, and the answer is below.

## Why the YouTube page freezes: it is starved, not busy

Four conditions were run, each able to come out the other way.

YouTube on its own does not freeze. Its worst gap was two thirds of a second
and its longest task a quarter of a second, so there is nothing wrong with the
page by itself; something has to be taking the machine away from it.

YouTube with two plain spinner processes alongside — no second browser, no
second video, no shared link — froze for eighteen and a half seconds. That one
result rules out every explanation that needs Vimeo, or a second browser, or
the two of them sharing a cap. Ordinary competition for the processor is
enough on its own.

Beside a different app, Twitch, it froze again, which agrees. And giving each
browser a processor of its own did not help: it still froze for sixteen
seconds. That was the obvious fix and it did not work, so the problem is not
two browsers colliding on one core.

The interesting part is what the page was doing while frozen. A thirteen and a
half second task is recorded, which reads at first like the page working very
hard. The processor figures say the opposite. During that freeze the renderer
received about two hundred milliseconds of processor time per second of wall
clock, against four hundred and eighty in the quiet solo run — roughly two
fifths of its usual share — while everything Chrome was running together held
about a fifth of the machine and the spinners took the rest. A task that holds
the thread for thirteen seconds while getting a fifth of a processor is a task
being pushed aside, not one computing. A long task is measured in wall clock,
so by itself it cannot tell those apart; the processor figure is what does.

Why YouTube and not Vimeo: YouTube's renderer simply wants more processor —
four hundred and eighty against Vimeo's five hundred when each is comfortable,
but YouTube is the one that collapses when the machine is oversubscribed.
Vimeo passed the cadence bar in every one of these conditions.

This is a limit of a two-processor measurement host, not something about how
YouTube delivers video. It bounds what a concurrent cell can measure here. It
should not be written up as a network result, and the release says so.

## Is the freeze ours? No, and it was there before us

The obvious worry about all of the above is that we caused it — that hooking
the page is what makes the page stop. So four samplers were run against the
same cell, three trials each: the current extractor; the current one with our
hooks, the byte ledger and the in-page log all switched off; a collector taken
from before the uniform hooks were written at all; and Jaber's approach, a
plain read of the video element every two seconds.

YouTube failed the cadence bar in all twelve. Not one sampler avoided it, and
the old collector froze as badly as the new one. Vimeo passed in eleven of the
twelve. So the instrumentation is not the cause, and we can stop suspecting it.

Older runs settle the rest. Sixty-three YouTube and Vimeo concurrent series
recorded on the twenty-sixth of September — months before any of this
instrumentation existed — show the same one-sided pattern: YouTube missed the
bar in seventeen of sixty-three, Vimeo in one. The asymmetry was always there.
Nobody had looked.

One thing did change: the size of it. The September gaps stop at about nine
seconds and the recent ones reach fifteen to twenty-seven. That is not the
runs being longer, because every freeze we measured happens inside the first
minute and cutting the new runs to a minute changes nothing. It is not the
instrumentation either, for the reason above. The likely remainder is that the
machine is busier now than it was in September, which fits the load figures we
see, but the old runs did not record load, so there is nothing to check it
against. That piece is unresolved and should be left that way rather than
guessed at.

## Why YouTube and not Vimeo: none of the obvious answers survived

The earlier line in this file — that YouTube's renderer simply wants more
processor — was wrong, and the numbers that were supposed to support it do not.
Measured on their own, the two are the same: about four hundred and eighty
milliseconds of processor per second for YouTube, about five hundred for Vimeo.
That is no difference at all, and it should never have been written as one.

So six things were tried, each able to come out the other way.

**Start order.** YouTube froze whether it went first with a minute's head start
or came in a minute late, three trials each way. Order is not it. The timing
moves, though: going first, the freezes arrive late in the run; coming in
second, they arrive within the first few seconds.

**Vimeo under the same load.** This is the one that matters. The two spinner
processes that freeze YouTube for eighteen seconds were pointed at Vimeo alone.
Vimeo did not freeze — worst gap about a second, three trials out of three.
Identical starvation, opposite outcome. Whatever this is, it is not simply a
busy machine, and the earlier conclusion that contention alone explains it was
too broad: contention is enough to break YouTube and not enough to break Vimeo.

**The decoder.** Neither app gets hardware decoding here; the browser reports
nothing as power-efficient, for H.264, VP9 or AV1 alike. Both decode in
software. It cannot be the thing that separates them.

**The rest of the machine.** Every process was watched, not only Chrome. During
a YouTube freeze the busiest thing on the box is dockerd, around seven to nine
hundred milliseconds per second, with fifteen to eighteen thousand context
switches a second and a dozen to twenty processes wanting to run on two
processors. But the same is true in the same seconds while Vimeo, in the same
run, keeps perfect time. The host looks identical in both cases, so none of it
discriminates.

**Ads.** Ads did run during the freezes in one set of runs. They did not run at
all in the others, which froze harder. Not necessary.

**Resolution.** Pinning YouTube to 480p did not stop it either, still three
failures out of three, the worst seventeen seconds.

So none of the six explains it. What the data does show is one real difference
between the two players: YouTube's page runs very long single tasks — up to ten
seconds — attributed to its own top-level script, while Vimeo's player never
exceeds about nine hundred milliseconds. Under contention a long task stretches
and the page stops answering; a short one does not. That is where the asymmetry
lives, and naming the function inside it is the next piece of work. The
browser's attribution says only "self", which identifies the origin and not the
code, so anything more specific would need a real trace.

Practically: this is a property of this two-processor host and of how YouTube's
player is built, not a network result, and the concurrent cell should not be
read as one.

## Inside the long task: three different things, not one

A sampling profile was taken of the browser's own script engine during the
concurrent cell, three trials, so the multi-second tasks could be looked into
rather than guessed about. An early single run suggested a clean answer —
ninety-two per cent script, nearly all of it YouTube's Kevlar interface code.
Three trials do not support that as *the* answer. They show three different
pictures:

One freeze was script, overwhelmingly: eighty-eight per cent, and the names are
all from YouTube's own interface framework — anonymous Kevlar functions, a
mutation-observer callback, property initialisation, template stamping
finishing. That is the page rebuilding parts of its own interface.

Another was almost entirely *not* script: eighty-six per cent sat in what the
profiler calls "program", which is time inside the engine that is not running
any JavaScript function, with eleven per cent garbage collection and only three
per cent in actual script.

The third produced no samples at all inside the freeze. The profiler is part of
the same thread; if it never got to run, the thread was not running either.
That one is the page being denied the processor, not the page being busy.

So the honest summary is that the freeze is not one mechanism. Sometimes the
page is doing heavy interface work, sometimes it is doing non-script work
inside the engine, and sometimes it is simply not scheduled. Garbage collection
was never more than about a tenth. Vimeo, profiled in the same runs, shows
nothing comparable: its worst windows are idle, a little garbage collection, or
a few hundred milliseconds of its own player code.

What the profile cannot see is worth stating: anything outside the script
engine — layout, painting, video decoding — does not appear as a named
function. It lands in "program" or leaves no sample. So this separates script
from garbage collection from neither; it does not separate layout from decode.

## How little it takes

Graded load settles the last question. One competing processor-bound process is
already enough to break YouTube: three trials with a single spinner, all three
failed, the worst gap thirty seconds. Two spinners are no worse in kind —
again three failures out of three, twenty-five to twenty-eight seconds.

Set that against Vimeo, which survived *two* spinners untouched. One spinner
breaks YouTube; two do not break Vimeo. On a two-processor machine that is a
very small margin, and it is the clearest statement of the asymmetry we have.

## Step B, first pass: the latency sweep

The plan and the bands were written into validation/THRESHOLDS.md and committed
before a single run started, so nothing in them could have been chosen to fit a
result. Twelve runs: YouTube and Vimeo on their own, six megabits, twenty,
fifty and a hundred and fifty milliseconds, two trials each. Fifty is the
baseline carried over from Step A so those cells line up with what is already
committed, and the queue stayed pfifo throughout — the queueing sweep is a
separate pass and was not started.

All twelve passed every check, including the cadence bar, which was not
changed. The host was quiet for all of them; the gate let each one through at a
load between two and a half and three and a third.

The shaping was real, and checked two ways rather than one. The shaper's own
report said it applied what it was asked for in all twelve. Independently, the
round trips measured out of the packet captures sit at a floor of twenty-three,
fifty-three and a hundred and fifty-three milliseconds for the three settings —
the configured delay plus about three milliseconds of real path. The second
check matters because the first is the shaper marking its own homework.

What the sweep shows, with two trials and therefore no claim of significance:
startup gets slower with latency on both players, Vimeo from about 2.5 to about
4 seconds, YouTube from about 4.5 to about 5.3. Nothing rebuffered anywhere.
Vimeo held 720p and its delivered video bitrate barely moved, around 2.3
megabits at every latency. YouTube dropped from 720p to 480p at a hundred and
fifty milliseconds, and its delivered video bitrate fell with it, from about
0.6 to 0.33. So on this evidence latency costs YouTube picture quality and
costs Vimeo only startup time.

## A name collision that would have replaced committed data

Building the release with Step A and Step B together produced twenty builds but
only eighteen folders. The reason is that a folder is named after the app, the
cap, the latency, the queue and the trial number, and Step A's six-megabit
fifty-millisecond solo run and Step B's fifty-millisecond baseline trial one
agree on every one of those. They are different runs from different days, and
the second was quietly overwriting the first — replacing data that is already
committed and listed by checksum.

The builder now refuses instead. It compares the run it is about to write
against the run already in that folder and stops with both paths named, so
whoever is building has to decide which measurement the folder is meant to
hold. Nothing was pushed in this state.
