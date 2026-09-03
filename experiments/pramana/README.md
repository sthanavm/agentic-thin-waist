# Pramana — CPUC broadband QoE experiment runner

Drives real applications in **real Chrome** over a **shaped bottleneck** and
produces per-app, player-side QoE (resolution, startup time, rebuffers, dropped
frames) alongside per-app network throughput.

Everything here is native to this repo. Clone it, bring the stack up, build one
image, open the notebook — no sibling checkouts, no environment variables to
point at other repositories.

> ### Two things that are not optional
>
> **1. Runs must be generated on the Linux VM, not on your laptop.** The
> collector joins the substrate worker's `ns1` network namespace, which exists
> only where the stack runs. Off-host you get network numbers from an *unshaped*
> link and no player QoE at all. Edit and read results anywhere; *generate* them
> on the VM. See [Running remotely](#running-remotely-and-what-breaks).
>
> **2. You must build `video-qoe-collector:latest` (step 2).** It is not
> optional and not pulled automatically — **without it the notebook measures no
> player QoE whatsoever**: every app reports `status: skipped` and you are left
> with throughput only.

---

## What a run actually does

```
run_experiment(cfg, mode="direct")
  ├─ substrate-worker :8002   shape the link once, capture once on veth2
  ├─ video-qoe-collector      REAL headed Chrome, one container, one thread per app
  │     └─ nsenter --net=<ns1>   ← runs INSIDE the shaped namespace, so the
  │                                traffic is genuinely rate-limited and captured
  │     └─ writes qoe/<app>_stats.jsonl   (raw per-second player samples)
  ├─ shared/qoe.py            samples → QoEMetrics (the definitions live there)
  ├─ telemetry-service :8004  per-app qoe_metrics
  └─ record.json + per-app plots
```

The notebook is a **client**. It contains no browser or Selenium code: the
browser driving is `services/orchestration/scripts/selenium_video_qoe/collect.py`,
and the metric definitions are `shared/apps.py` + `shared/qoe.py`.

---

## Prerequisites

- A **Linux VM** with Docker. The collector must join the substrate worker's
  `ns1` network namespace, which only exists where the stack runs — so the
  notebook has to run on the VM for player QoE (see *Running remotely* below).
- Docker with enough disk: the collector image is ~1.9 GB and each 60 s run
  writes a 40–70 MB pcap. Budget a few GB.
- Python 3.12 (3.10+ works).
- Outbound internet from the VM — the browser really loads YouTube, Vimeo, etc.

---

## 1. Bring the stack up

```bash
git clone https://github.com/sthanavm/agentic-thin-waist.git
cd agentic-thin-waist
cp .env.example .env

# DIRECT mode needs the substrate worker and telemetry service.
docker compose up -d postgres telemetry-service substrate-worker

# Confirm both are healthy:
curl -s localhost:8002/health   # substrate worker
curl -s localhost:8004/health   # telemetry
```

`substrate-worker` runs privileged (it needs `tc`/`netem` and `tshark`) and
creates the `ns1` / `ns2` namespaces on startup.

## 2. Build the QoE collector image (MANDATORY, once)

```bash
docker build -t video-qoe-collector:latest \
  services/orchestration/scripts/selenium_video_qoe/
```

**Do not skip this.** The image is never pulled for you, and nothing else
provides player-side QoE. Without it every browser app is reported as
`skipped` and the notebook produces throughput only — no resolution, no
startup time, no rebuffers.

This installs real `google-chrome-stable` plus Xvfb, fluxbox, SeleniumBase and
undetected-chromedriver. Takes a few minutes and ~1.9 GB. Override the name with
`PRAMANA_COLLECTOR_IMAGE` if you tag it differently.

**Budget ~4 GB of free disk for the build**, not 2 GB: while it runs, the host
holds the old image, the compressed layers and the extracted snapshot at once.
A build that dies with `no space left on device` while exporting the Chrome
layer leaves a broken, unusable image — remove it and retry with more room:

```bash
docker rmi -f video-qoe-collector:latest   # only if a build failed midway
docker builder prune -af
```

Verify the image really carries the collector before running anything:

```bash
docker run --rm --entrypoint sh video-qoe-collector:latest \
  -c 'google-chrome --version && python3 -c "import seleniumbase, selenium"'
```

## 3. Install the notebook dependencies

```bash
pip install -r experiments/pramana/requirements.txt
```

## 4. Open the notebook

```bash
jupyter notebook --no-browser --port 8888
```

From your laptop, tunnel the Jupyter port and open the printed URL:

```bash
ssh -p <port> -L 8888:localhost:8888 <user>@<vm-host>
```

Open `experiments/pramana/pramana_demo.ipynb` and run top to bottom.

---

## Running one experiment

The notebook's config cell is the only thing you normally edit:

```python
cfg = ExperimentConfig(
    apps=["youtube"],        # one app = solo; several = concurrent on one link
    bandwidth_mbps=10,
    latency_ms=50,
    aqm="pfifo",
    cca="cubic",
    duration_s=60,           # keep >= 60s so metrics reflect steady state
)
record = run_experiment(cfg, mode="direct")
ph.show_player_qoe(record)
```

Supported apps come from `shared/apps.py`: `youtube`, `vimeo`, `twitch`, `tubi`,
`roku`, `puffer` (HTML5 video), `zoom`, `meet` (WebRTC), `wget` (shell, no player
QoE). Adding one is a registry entry, not new code.

**Conferencing apps need a peer.** Zoom and Meet produce no inbound media unless
someone else is publishing video. Without a configured peer they are reported as
`skipped` with a reason and the rest of the run still succeeds — they are never
measured against their own local camera preview. See `REALQOE.md` at the repo
root for peer-supply options.

**Live apps** (`twitch`, `puffer`) have no finite duration, so
`video_duration_secs` and `delivered_fraction_of_video` are `null` by
construction. Supply a channel that is actually streaming via
`app_urls={"twitch": "https://www.twitch.tv/<channel>"}`.

---

## Forced quality (opt-in, YouTube only)

By default YouTube picks its own rendition with adaptive bitrate, and it is
conservative: measured here, it sits at **480p at 6, 10 and 50 Mbps alike** and
never asks for more than ~7.7 Mbps even on a 50 Mbps link. To measure what the
*link* can carry rather than what ABR chooses, pin the rendition:

```python
cfg = ExperimentConfig(
    apps=["youtube"],
    bandwidth_mbps=50, latency_ms=50,
    duration_s=120,
    force_max_quality=True,        # default False — nothing else changes
)
```

`force_max_quality` is **off by default**; leaving it unset reproduces the
original behaviour exactly. When on, the collector calls YouTube's
`setPlaybackQualityRange('hd2160','hd2160')` (falling back to
`setPlaybackQuality`) after the player loads and before sampling starts. The API
is undocumented and silently ignores levels a source does not carry, so the call
is best-effort: it never raises, and it records what it managed to do —
including `getAvailableQualityLevels()` — in `collector.log` and in the meta
line of `qoe/<app>_stats.jsonl`. That list is what tells you whether a low
result means "ABR chose low" or "the source has nothing higher".

Forced runs are self-identifying: `record.json` carries `force_max_quality:
true`, and the run directory gets a **`_forcedq`** slug suffix so forced and
auto runs can never be pooled or compared by accident.

Pinning quality does not make resolution climb with bandwidth — it pins 2160p at
every tier. What changes with bandwidth is whether that resolution can be
*delivered*: in the reference runs the watched fraction goes 12% → 33% → 78% →
84% across 6 → 10 → 25 → 50 Mbps, while startup falls from 17.7 s to 1.1 s.

`force_max_quality` requires the collector image to have been built from the
current `collect.py`. If you built it earlier, rebuild it (step 2) — otherwise
the flag is recorded but does nothing.

---

## Validating output

Every run should be checked before it is used as a result. The validator is
**read-only** — it never edits a run:

```bash
python3 experiments/pramana/validate_runs.py            # defaults to results/pramana_runs
python3 experiments/pramana/validate_runs.py <dir> -v   # another tree, verbose
```

It prints one row per run (`PASS` / `FAIL:<rules>` / `WARN:<rules>`), then the
offending value against the expected bound for every failure. Exit status is 1
if anything failed, so it can gate a pipeline.

Hard rules (a `FAIL` is a contradiction inside the run): shaping under cap,
label-vs-player honesty, played-but-watched-zero, no-playback logic, bitrate
sanity, artifacts present, capture duration. Soft rules (`WARN`): resolution
falling as bandwidth rises, record bytes diverging from the pcap, duplicate
configs, and an intentionally omitted capture.

**Repairing an old dataset.** Records written before the flag fix carry
`per_app_stats[app].player_qoe_available: false` even when real player metrics
exist. `backfill_player_flag.py` rewrites that one field to agree with the
payload and touches nothing else:

```bash
python3 experiments/pramana/backfill_player_flag.py            # dry run
python3 experiments/pramana/backfill_player_flag.py --index --apply
```

---

## Reference runs shipped in this repo

`results/pramana_runs/` contains a small set of **reference example runs** — the
newest run per configuration that passes every hard validator rule, plus the
forced-quality sweep. They exist so you can see the output format and run the
validator before generating anything yourself.

They ship **without their captures**. A pcap is 65–300 MB and GitHub rejects any
file over 100 MB, so each shipped run carries a `PCAP_OMITTED.md` marker and the
validator reports a `PCAP_OMITTED` *warning* rather than a failure. The
`record.json` still says `pcap_saved: true`, because the run genuinely did save
one on the VM — the marker explains the absence instead of falsifying the
record. Everything else in those directories is the unmodified original.

Your own runs land in the same tree and are gitignored.

---

## Where results land

```
experiments/pramana/results/pramana_runs/<slug>_<id>/
├── record.json               config + player_qoe + per_app_stats + network stats
├── capture.pcap              raw capture at the bottleneck (always saved)
├── qoe/<app>_stats.jsonl     raw per-second player samples
├── throughput_<app>.png      one per app (per direction for conferencing)
├── qoe_summary_<app>.png     throughput + buffer + resolution panels
└── collector.log             browser driver output
```

`record.json` also carries `force_max_quality` (true/false), so a run always says
whether YouTube's rendition was pinned or left to ABR.

Plus `results/pramana_runs/dataset_index.jsonl` — every run, one JSON per line.
It is written on the VM as runs accumulate and is not shipped in the repo.

Your runs are **gitignored** (captures are large, and they are evidence, not
source); the handful of committed reference runs described above are the only
exception. Override the location with `PRAMANA_RESULTS_DIR=/mnt/big-disk` if the
VM's root volume is tight — each 60 s run costs 40–70 MB, and a 120 s forced-4K
run costs 100–300 MB.

### Pulling results to your laptop

```bash
# QoE data + plots, without the large pcaps
rsync -az --exclude='*.pcap' -e 'ssh -p <port>' \
  '<user>@<vm-host>:~/agentic-thin-waist/experiments/pramana/results/pramana_runs/' \
  ./pramana_results/

# a flat table across every run pulled
python3 experiments/pramana/build_summary.py ./pramana_results
```

---

## Configuration (all environment-driven)

| Variable | Default | Purpose |
|---|---|---|
| `PRAMANA_SUBSTRATE_URL` | `http://localhost:8002` | substrate worker |
| `PRAMANA_TELEMETRY_URL` | `http://localhost:8004` | telemetry service |
| `PRAMANA_NETGENT_URL` | `http://localhost:8003` | NetGent (optional) |
| `PRAMANA_ORCH_URL` | `http://localhost:8005` | orchestrator (INTENT mode only) |
| `PRAMANA_COLLECTOR_IMAGE` | `video-qoe-collector:latest` | collector image tag |
| `PRAMANA_SUBSTRATE_CONTAINER` | `substrate-worker` | container to resolve `ns1` from |
| `PRAMANA_DOCKER` | `docker` | docker binary |
| `PRAMANA_RESULTS_DIR` | `experiments/pramana/results` | where runs are written |
| `PRAMANA_COLLECT` | `1` | set `0` to skip the browser collector |
| `PRAMANA_COLLECTOR_SRC` | unset | dev override: bind-mount a host `collect.py` into the image instead of rebuilding it |

No code edits are needed to run against a different host — only these.

`PRAMANA_COLLECTOR_SRC` exists for iterating on the collector when the host has
no room to rebuild a ~2 GB image. It is a development shortcut, not the
supported path: build the image (step 2) so behaviour is baked in.

Per-experiment settings live on `ExperimentConfig` rather than the environment —
notably `force_max_quality` (default `False`) and `force_quality_level` (default
`hd2160`); see [Forced quality](#forced-quality-opt-in-youtube-only).

---

## Running remotely (and what breaks)

You can run the notebook on your laptop with the services tunnelled:

```bash
ssh -p <port> <user>@<vm-host> -L 8002:localhost:8002 -L 8004:localhost:8004
```

The **network** side works fully: shaping, capture and pcap download are all
HTTP. **Player QoE does not.** The collector is launched via `docker run` on
whichever machine runs the notebook, and it must join the substrate worker's
`ns1` namespace — which exists only on the VM. Off-host, browser apps report no
player QoE rather than silently producing numbers from an unshaped link.

Run the notebook on the VM (Option A above) for real QoE.

---

## Reading the output honestly

- **`status: ok`** — real player metrics were measured.
- **`status: no_data`, `reason: no_playback`** — the page loaded but the media
  clock never advanced. This is a **failure to deliver video**, not a slow
  stream, and the per-app verdict becomes `failed_to_deliver` even when the app
  moved megabytes.
- **`status: skipped`** — not measurable as configured (e.g. a conferencing app
  with no peer). Never a fabricated number.
- **`null` is an answer, not a gap.** `mean_bitrate_mbps` is null for HTML5 apps
  because neither YouTube field is a media bitrate: `bandwidth_kbps` is a
  *connection-speed estimate* and `network_activity_bytes` is an instantaneous
  gauge, not a counter. `derivation.bitrate_basis` records why in every record.
- **`rebuffer_events` counts frozen playback**, not network idle time. A player
  that has buffered 60 s and gone quiet is healthy, not stalled.

The per-app support matrix — which fields are real, derived, null, or need a peer
— is in `REALQOE.md` at the repo root.

---

## Troubleshooting

**`could not resolve the ns1 namespace via container 'substrate-worker'`**
The substrate worker isn't running, or is named differently. Check
`docker ps | grep substrate`, and set `PRAMANA_SUBSTRATE_CONTAINER` if needed.

**Collector exits non-zero / no samples**
Read `collector.log` in the run folder. Common causes: no outbound internet from
`ns1`, or the app URL doesn't autoplay. Chrome runs with
`--autoplay-policy=no-user-gesture-required`, but some pages still need a real
title URL rather than a homepage (Twitch and Tubi especially).

**`ImportError: could not import shared.apps / shared.qoe`**
`pramana_helpers.py` was moved out of `experiments/pramana/`. It resolves the
repo root from its own location; put it back or run from the repo root.

**Shaping verification failed**
The measured average exceeded the cap by more than `SHAPING_TOLERANCE`. The run's
pcap, record and plots are still saved — it raises *after* writing them, so the
evidence is never lost. Re-render later with `ph.replot_run(<dir>)`.

**Disk fills up**
Each run keeps its pcap. Prune old runs, or set `PRAMANA_RESULTS_DIR` to a
larger volume.
