# Pramana — CPUC broadband QoE experiment runner

Drives real applications in **real Chrome** over a **shaped bottleneck** and
produces per-app, player-side QoE (resolution, startup time, rebuffers, dropped
frames) alongside per-app network throughput.

Everything here is native to this repo. Clone it, bring the stack up, build one
image, open the notebook — no sibling checkouts, no environment variables to
point at other repositories.

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

## 2. Build the QoE collector image (once)

```bash
docker build -t video-qoe-collector:latest \
  services/orchestration/scripts/selenium_video_qoe/
```

This installs real `google-chrome-stable` plus Xvfb, fluxbox, SeleniumBase and
undetected-chromedriver. Takes a few minutes and ~1.9 GB. Override the name with
`PRAMANA_COLLECTOR_IMAGE` if you tag it differently.

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

Plus `results/pramana_runs/dataset_index.jsonl` — every run, one JSON per line.

This whole tree is **gitignored**. Captures are large and are evidence, not source.
Override the location with `PRAMANA_RESULTS_DIR=/mnt/big-disk` if the VM's root
volume is tight.

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

No code edits are needed to run against a different host — only these.

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
