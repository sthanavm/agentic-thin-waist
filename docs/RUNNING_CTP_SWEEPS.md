# Running CTP cross-traffic sweeps

CTP ("cross-traffic profile") sweeps replay a recorded background-traffic
trace over the shaped link while an app runs, so the app is measured under
realistic contention instead of an idle link. This is supported today by
**`vimeo.py`** and **`teams.py`** via their shared `--ctp-*` flags
(`experiments/cpuc/ctp_plan()` in `conditions.py`). **`meet.py` does not
support CTP** — it has no `ctp` import or `--ctp-*` flags.

## Infrastructure prerequisites (read this first)

None of this is runnable from just a `git clone` of this repo. It assumes
you are executing **on the VM itself** (e.g. `docker-vm-10`), with specific
VM-local state already in place:

1. **The `substrate-worker` container must already be running**, not just
   built. These scripts never start it themselves — `docker exec` and `ip
   netns exec` calls throughout `netns.py` assume it's already up with
   `ns1`/`ns2` namespaces live. Check with:

   ```bash
   docker ps --format "{{.Names}}: {{.Status}}"
   ```

   If it's `Exited`, a plain restart is usually enough (it does not lose its
   namespace setup on a clean stop/start):

   ```bash
   docker start substrate-worker
   ```

   If it doesn't exist at all, it needs a full `docker run --privileged --pid
   host ...` recreation — see `netns.assert_ctp_mount()`'s own error message
   below for the exact flags this harness depends on (it will tell you this
   itself the first time a CTP run can't find its mount, but don't wait for
   that to discover the container isn't there).

2. **The CTP corpus, collector image, and any clip assets are VM-local
   files/images, not part of this repo.** `manifest.csv` + `download/*.pcap`
   (see below), `video-qoe-collector:latest` (built from
   `services/orchestration/scripts/selenium_video_qoe/`, see
   `RUNNING_VIDEO_CONFERENCING.md` for the build command and a seleniumbase
   version gotcha), and any bot-peer clip all have to already exist on the
   specific VM you're running on. A fresh checkout on a new machine has none
   of this — expect to either copy it over from a known-good VM or rebuild
   each piece from scratch before a first run can succeed.

## Prerequisites

1. **The CTP corpus**, on the host running the worker (default
   `/home/student/ctp_data`):
   - `manifest.csv` listing one `profile` column per usable trace.
   - `download/<profile>.pcap` for every name in the manifest.
   `cpuc/ctp.py::load_profiles()` reads the manifest and raises immediately if
   a named profile's pcap is missing, rather than silently skipping it.

2. **The worker container must have the corpus bind-mounted at `/ctpsrc`**
   (read-only), separately from any mount the app's own collector uses. This
   is checked up front by `netns.assert_ctp_mount()`; if it's missing you get:

   ```
   docker stop substrate-worker && docker rm substrate-worker
   docker run -d --name substrate-worker --privileged --pid host \
     -v /home/student/ctp_data/download:/ctpsrc:ro \
     <the rest of the container's existing mounts/env/image>
   ```

   This is a one-time container recreation, not something a flag fixes at run
   time — do it before starting a batch, not mid-run.

3. **The collector probe.** Both scripts call
   `drive_pramana.assert_collector_has_probe()` before starting, which checks
   the *mounted* `collect.py` source (not the baked image copy) for the
   streaming QoE probe. Set `PRAMANA_COLLECTOR_SRC` to the actual path of
   `collect.py` in your checkout if it isn't at the module's default
   (`/home/student/cpuc/services/orchestration/scripts/selenium_video_qoe/collect.py`)
   — this is the single most common setup mistake: a checkout at a different
   path (e.g. `/home/student/athw-ctp-test/...`) will fail this assertion
   with "collector source ... does not exist" until the env var is set.

## What a CTP run actually does

The replay goes through `netns.start_ctp_replay()`: `tcpreplay-edit` inside
the worker container, on the server side (`ns2`), replays the profile's
*download*-direction pcap looped for the run's duration, with its destination
PNAT-rewritten to `172.16.1.20`. That rewritten destination is what lets a
capture (and `link_interference`) separate CTP bytes from the app's own
traffic — if you're writing your own analysis, filter on `ip.dst==172.16.1.20`
for CTP demand.

The condition is fixed, not swept: `conditions.CTP_CONDITION` is always
10/10 Mb/s, 100 ms RTT, no induced loss. A CTP run only varies by *which*
profile is replayed, not by capacity/RTT/loss.

## Running it

Both scripts share the same flags:

```bash
python3 vimeo.py --ctp-only --ctp-count 10 --ctp-seed 999 \
  --out-root /home/student/cpuc_data/vimeo/ctp_runs \
  --csv /home/student/cpuc_data/vimeo/ctp_runs.csv \
  --marker /home/student/cpuc_data/vimeo/CTP_COMPLETE
```

```bash
python3 teams.py --ctp-only --ctp-count 5 --ctp-seed 42
```

- `--ctp-count N` — run N CTP profiles, each at `CTP_CONDITION`, one per run.
- `--ctp-seed S` — makes the random profile pick reproducible (same seed +
  same manifest → same N profiles, via `random.Random(seed).sample(...)`).
  Omit it and the pick is unseeded (not reproducible).
- `--ctp-dir DIR` — corpus root, if not the default `/home/student/ctp_data`.
- `--ctp-only` — skip the app's main rate/RTT matrix entirely and run *only*
  the CTP profiles. Without it, `--ctp-count` runs are appended on top of the
  main matrix.
- Without `--ctp-only`, you can still combine CTP runs with the main sweep in
  one invocation; `--ctp-count` runs are just added to the plan list.

**Running the same, specific profiles twice** (e.g. to reproduce a known
dataset without knowing the original seed): there's no `--ctp-profiles
<names>` flag today. Monkeypatch `ctp.random_profiles` before calling
`main()`:

```python
from cpuc import ctp as ctp_mod
PROFILES = ["cluster0_tree0_profile1307", "cluster2_tree3_profile7190", ...]
ctp_mod.random_profiles = lambda n, ctp_dir=None, seed=None: list(PROFILES)
sys.argv = ["vimeo.py", "--ctp-only", "--ctp-count", str(len(PROFILES)), ...]
import importlib; vimeo = importlib.import_module("cpuc.vimeo")
sys.exit(vimeo.main())
```

## Output

- `--csv` gets one row per CTP run, with `ctp_profile` naming which trace ran.
  `raw_path` points at the run directory `vimeo.py`/`teams.py` passed in —
  but note `AppRunResult.raw_path` actually prefers `record.get("run_dir")`
  from `pramana_helpers`, which can differ from `--out-root`'s own naming; if
  the two look inconsistent, check `pramana_helpers`'s own results directory
  (its default is `experiments/pramana/results/pramana_runs/`) rather than
  assuming the row's `raw_path` is where the data landed.
- Each run directory holds `capture.pcap` (the full link capture, app +
  CTP), `record.json` (the full run record — `network_stats`, `irtt`,
  `netvibe`, `player_qoe`, etc.), and `qoe/<app>_stats.jsonl` (one JSON line
  per QoE sample, with a `timestamp` epoch field you can align against the
  pcap).
- CTP-only runs produce `qoe_label: deferred` by default — no reference row
  exists for the CTP condition, so labelling against IQB thresholds doesn't
  apply the way it does for the main rate/RTT matrix.

## Probes without the app (IRTT / NetVibe only)

To measure the link under CTP pressure *without* driving any app, pass an
empty app string into `drive_pramana.drive("", ...)` directly —
`ExperimentConfig.__post_init__` strips the empty string, so `cfg.apps == []`
and `run_direct()` skips the browser/collector entirely while still shaping
the link, replaying the CTP profile, and running IRTT/NetVibe if enabled
(`PRAMANA_IRTT=1`, `PRAMANA_NETVIBE=1`). `vimeo.py`/`teams.py`'s own CLI has
no flag for this today — it means writing a small driver script that calls
`drive_pramana.drive()` directly with the fixed profile list, the same way
the CTP-only path above does.
