# Running video conferencing experiments (Meet, Teams)

Conferencing runs (`meet.py`, `teams.py`) are the bandwidth-sweep harness for
the two IQB-conferencing apps. Unlike streaming (`vimeo.py`), a conferencing
run needs a second participant: an empty room produces only the local
preview track, which `shared/qoe.py` refuses to measure since it says
nothing about the network. A **bot peer** supplies the missing remote
video — it joins the room and publishes a canned clip over normal (unshaped)
Docker networking, so its own uplink is never the bottleneck under test.

## Infrastructure prerequisites (read this first)

None of this is runnable from just a `git clone` of this repo — it assumes
you are executing **on the VM itself**, with VM-local state already in
place, and a **human actively present during the run**, not just at the
start:

1. **The `substrate-worker` container must already be running** (not just
   built) before any of these scripts will do anything — see
   `RUNNING_CTP_SWEEPS.md`'s infrastructure section for the check/restart
   commands. This applies here too even though conferencing runs don't touch
   CTP; the shaping and the whole `ns1`/`ns2` setup live in that same
   container.

2. **The collector image must exist**, built from
   `services/orchestration/scripts/selenium_video_qoe/`:

   ```bash
   docker build -t video-qoe-collector:latest \
     services/orchestration/scripts/selenium_video_qoe/
   ```

   Watch the `seleniumbase` version this resolves to. The Dockerfile's
   `requirements.txt` only pins `seleniumbase>=4.42.5,<5.0.0` — an unpinned
   range — and `seleniumbase==4.56.5` is known to silently break the
   in-page QoE probe (no startup-delay/rebuffer data at all, with no error).
   Only `4.54.12` is validated. If you need a deterministic build, pin it
   explicitly before building (`seleniumbase==4.54.12` in that
   `requirements.txt`) and revert the pin afterward; verify what actually
   landed with:

   ```bash
   docker run --rm --entrypoint python3 video-qoe-collector:latest \
     -c "import seleniumbase; print(seleniumbase.__version__)"
   ```

3. **A human has to open the meeting AND stay reachable during the batch,**
   not just click "join" once at the start:
   - `bot_peer.py`'s own docstring says "where admission is manual, the bot
     simply keeps reporting `knocking` until it is let in or the deadline
     passes." If the room/org requires manual admission of guests, **a human
     needs to admit the bot every single run**, within `JOIN_DEADLINE_S`
     (default 150 s, `CPUC_MEET_JOIN_DEADLINE_S` / `CPUC_TEAMS_JOIN_DEADLINE_S`
     to change it) — this is not automatable from this repo alone, and
     nothing in `meet.py`/`teams.py` warns you it's waiting on a human.
   - Separately, for Meet specifically, the harness's comments describe the
     bot as "already persistent in the room" by the time a batch starts
     (`peer_wait_s=30` assumes this), which implies some bot instance is
     expected to be pre-joined and kept alive across the whole batch — but
     the actual launch mechanics for that persistent instance live in
     `pramana_helpers.py` and were not traced for this doc. Don't assume
     `python3 meet.py` alone starts and keeps a bot in the room between
     runs; if runs start failing to detect a publishing peer, this is the
     first thing to check.

## Prerequisites

1. **A clip for the bot to publish.** A synthetic/low-res clip caps the
   measured resolution for a reason that has nothing to do with the link,
   which is exactly what both scripts check for before the first run — so
   use real, high-resolution video. `/home/student/pramana-assets/
   vimeo_911411289_1440p24_10s.y4m` is a 10 s, 1920x1440, 24 fps clip sourced
   directly from the same Vimeo video (911411289) and rendition the Vimeo CTP
   runs themselves played, pulled via `yt-dlp` + `ffmpeg` and converted to
   raw Y4M (`ffmpeg -i <source> -t 10 -an -pix_fmt yuv420p <out>.y4m`). Point
   both scripts at it:

   ```bash
   export CPUC_MEET_CLIP=/home/student/pramana-assets/vimeo_911411289_1440p24_10s.y4m
   export CPUC_TEAMS_CLIP=/home/student/pramana-assets/vimeo_911411289_1440p24_10s.y4m
   ```

   `CPUC_MEET_MIN_CLIP_H` / `CPUC_TEAMS_MIN_CLIP_H` (default 720) is the floor
   both scripts check the clip against before the first run — this clip
   clears it easily at 1440.

2. **A room URL:**
   - **Meet**: defaults to a fixed room
     (`https://meet.google.com/kiu-amox-fbz`, override with
     `CPUC_MEET_ROOM`). It must be a **Calendar-scheduled meeting, not an
     instant one** — an instant meeting ends the moment everyone leaves, and
     the bot leaves between runs, which killed a room mid-batch in practice.
   - **Teams**: has **no default** — `CPUC_TEAMS_ROOM` must be set, or
     `teams.py` refuses to start with "point it at a persistent (Calendar,
     ...) meeting". Same reasoning as Meet.

3. **The collector probe.** Same as the CTP doc: both call
   `drive_pramana.assert_collector_has_probe()` up front, checking the
   *mounted* `collect.py` for the streaming/WebRTC probe markers. Set
   `PRAMANA_COLLECTOR_SRC` if your checkout isn't at the module default path.

## Running it

```bash
python3 meet.py --rtt 20
python3 meet.py --rtt 50 --rates 25,8,6,4
python3 teams.py
CPUC_TEAMS_ROOM="https://teams.microsoft.com/l/meetup-join/..." python3 teams.py --rtts 50,100
```

Common flags (both scripts):
- `--limit N` — stop after N plans (0 = all).
- `--rates ...` — comma-separated capacities, replacing the default sweep.
- `--skip-reference` — don't re-run the no-loss reference corner; labels
  resolve against a reference row already in the CSV.
- `--ndt-only` — run only the NDT half (no meeting needed at all, so this is
  the thing to run after an M-Lab rate limit clears without spending any
  meeting time).
- `--app-only` — run only the conferencing-call half, skipping NDT.
- `--out-root`, `--csv`, `--marker` — defaults are
  `/home/student/cpuc_data/<app>/{runs,cpuc_runs.csv,COMPLETE}`.

Meet-specific:
- `--extra` — add 12.5 and 0.5 Mb/s to the sweep.
- `--rtt MS` — a single RTT for the whole batch (default 20 ms; IQB's
  conferencing corners are 50 ms minimum / 20 ms high, with 100 ms as a third
  point). Unlike Teams, Meet runs one RTT per invocation, not a comma list.

Teams-specific (it ported Meet's harness and extended it to the full Stage
1+2 design):
- `--rtts ...` — comma-separated RTTs, default `20,50,100` (plural, unlike
  Meet's single `--rtt`).
- `--condition-range LO-HI` — 1-indexed inclusive slice of the unique
  condition list, for splitting a batch across a time-limited meeting. Index
  order puts all three per-RTT references first, so `1-11` always covers
  every reference and a later `12-21` can resolve labels against the CSV
  without re-running the first half.
- `--ctp-count` / `--ctp-seed` / `--ctp-only` / `--ctp-dir` — see
  `RUNNING_CTP_SWEEPS.md`.

## The bot peer, in practice

Both drivers pass `cfg.peers = {app: {"join_url": ROOM, "y4m": CLIP, "name":
BOT_NAME, "join_deadline_s": JOIN_DEADLINE_S}}` into `pramana_helpers.
run_direct()`, which is what actually launches/manages the bot container —
the exact launch mechanics live in `pramana_helpers.py`, not in `meet.py`/
`teams.py` themselves.

The two differ in how long a run waits for the bot to be confirmed
publishing (`peer_wait_s`):
- **Meet: 30 s.** Meet's bot is treated as already-persistent in the room, so
  this is mostly a formality. A separate freshness check,
  `peer_is_publishing()`, reads a status file (default
  `/home/student/pramana-assets/peer-meet/meet_status.json`, override with
  `CPUC_MEET_PEER_STATUS`, fresh within 180 s) during the batch as a health
  signal distinguishing "meeting dropped" from "collector died" when a run
  comes back empty.
- **Teams: 120 s.** Teams' bot joins fresh every single run (it is not
  persistent) and needs the real knock/admit cycle to complete — measured
  live: 30 s made every run fail with "bot peer never confirmed publishing"
  even when the bot was actually in the meeting and auto-admitted, because
  the timeout fired before the status file caught up.

If you see that failure on Teams, it is very likely `peer_wait_s` being too
short for your meeting's actual admit latency, not a broken bot.

## Why Meet's remote-tile gate is disabled

Both scripts set `PRAMANA_REMOTE_TILE_MIN_AREA=0`. The collector's normal
remote-tile gate checks DOM fields (`measured_box_area`,
`measured_is_local`, `video_remote_decoded_count`) to prove it measured the
remote peer's tile and not the local self-view — but Meet's `getStats` path
emits none of those three fields at all, so the gate cannot pass regardless
of call health. It's replaced by `assert_remote_inbound_media`, which checks
remote inbound video arrived via `inbound-rtp` stats directly (local video
appears in `outbound-rtp`, so this path is unambiguous by construction).

## Output

Same shape as streaming: one CSV row per run, with
`run_dir`/`record.json`/`qoe/<app>_stats.jsonl` holding the full per-run
data. `qoe_label` resolves against `qoe_labels.label_conferencing()` (checks
resolution + fps, not just resolution like streaming) once a reference row
exists for that RTT.
