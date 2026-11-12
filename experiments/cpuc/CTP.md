# How CTP replay works, and what actually blocks Jaber's spec

Written to be read before talking to Sthanav. **Nothing here was run.** Every
path and line number is from the merged tree at `a9a0fb3`, and every figure
about vm4 was read off vm4 on 2026-11-11.

The short version: **the mechanism is complete and there is already a working
precedent for running it beside a measured application.**
`services/orchestration/scripts/run_direct_teams_only_experiment.py` on `main`
— Haarika's pattern — replays a CTP alongside a Teams run today, and it solves
the operational problems an earlier draft of this document listed as open. Two
blockers genuinely remain, and both are about data rather than code: **the
corpus on vm4 is one profile**, and **"cluster" has no referent in this
pipeline**.

---

## 1. The path that works: `run_direct_teams_only_experiment.py`

This is the file to copy, not `/replay`. It spins up its **own ephemeral
substrate worker** per run and drives `tcpreplay-edit` inside it directly
(`:230-257`):

```python
command = (
    f"ip netns exec ns2 tcpreplay-edit -i veth3 "
    f"--pnat={ctp_pnat} --loop=0 --duration={seconds} "
    f"/ctpsrc/{ctp_name}.pcap"
)
docker("exec", "--detach", container, "sh", "-c", command)
```

Five things that follow from it, each of which removes a problem:

| | how | why it matters |
|---|---|---|
| **Bypasses `/replay`** | calls `tcpreplay-edit` itself | `/replay` demands a matching `upload/<name>.pcap` and starts both directions; this run wants the downlink loaded only |
| **The empty `$CTP_DIR` stops mattering** | the profile directory is mounted `:/ctpsrc:ro` and the container's own `CTP_DIR=/ctp` points at a throwaway tempdir (`:453`, `:477`, `:487-490`) | nothing has to be staged into `/tmp/substrate-ctp` on the shared worker, and nothing on the host is written |
| **Loops for the whole window** | `--loop=0 --duration=max(240, duration+60)` (`:563`) | a 29.9 s profile against a 60 s measurement would otherwise leave the second half uncontended — a baseline wearing a cross-traffic label |
| **Never touches the shared worker** | `docker run --detach --rm --privileged --pid host --publish 127.0.0.1::8002`, torn down in `finally` along with the tempdir (`:460-495`, `:631-652`) | no replay can outlive the run and keep injecting into everyone's namespace, and no `DELETE /replay/{id}` to forget |
| **Records which profile ran** | `ctp.json` in the result dir, `ctp_file`/`ctp_direction`/`ctp_pnat` in `experiment.json`, and the name in the experiment id itself — `teams-only-{cap}mbps-{lat}ms-pfifo-{ctp_name}` (`:124-141`, `:579`) | the profile reaches the artefacts, so a dataset cannot have rows that differ by a variable nobody can see |

**`$CTP_DIR` on the shared worker is not hard to fill, as it turns out.**
`/tmp/substrate-ctp` is a **host bind mount at the identical path inside the
container** (`docker inspect substrate-worker`:
`bind|/tmp/substrate-ctp|/tmp/substrate-ctp|rw=true`, with
`CTP_DIR=/tmp/substrate-ctp`), writable from the host with `sudo`. So a
profile dropped at `/tmp/substrate-ctp/<name>.pcap` is visible to the running
worker immediately — no `docker cp`, no restart, no mount change, nothing
written inside the container's own filesystem. The Teams pattern's `/ctpsrc`
mount is still the cleaner route *for that script*, because its worker is
ephemeral and gets the corpus at launch; but anything driving the **shared**
worker has a supported place to put files. That is what makes the CPUC
integration in §6 cheap rather than structural.

Two more details worth copying rather than rediscovering:

- **Ordering.** The replay starts *after* the capture is running and *before*
  the application collector (`:547-583`), so the capture carries the cross
  traffic across the whole app window.
- **A failed replay is recorded, not fatal** (`:571-579`). `ctp_error` goes
  into `ctp.json` and the run continues, so a broken replay can never be
  mistaken for a clean baseline.

**`--pnat` keeps the capture splittable.** The default is
`169.231.0.0/16:172.16.1.20`, rewriting the trace's internal addresses onto an
address that is **not** the application client (`172.16.1.1`). Every demand and
`infra_share_pct` figure in this study depends on that separation; sending CTP
to the app's own address would make the capture unsplittable.

**It also sets `latency_location` explicitly** (`:132`), which is not optional
in effect: `apply_shaping` deletes netem on both interfaces and adds it only
where asked, so a `/shape` call that omits the field is shaped to capacity with
**zero** added latency and nothing says so.

---

## 2. Where the profiles live, and how they are named

Three directories matter. With the Teams pattern only the first and third do.

| Directory | Who writes it | Who reads it |
|---|---|---|
| `CTP_OUTPUT_DIR/users/...` | CTP Service `extract`/`transform` | the service itself |
| `$CTP_DIR/{download,upload}/<name>.pcap` | whoever stages the files | Substrate Worker `/replay` **only** |
| whatever you mount at `/ctpsrc` | you, read-only | `tcpreplay-edit` in the Teams pattern |

The name is built in two places and nothing in between adds a cluster:

```python
# services/ctp-service/app/operations/extract.py:188
ctp_id = f"ctp-{dataset_name}-{subnet_str.replace('/', '_')}-w{window_index:04d}"

# services/ctp-service/app/operations/transform.py:142
downlink_dir / f"{safe_id}_{throughput_threshold_mbps:.0f}mbps.pcap"
```

Read the one profile on vm4 straight off that:

```
ctp-  ctp_samples  -169.231.112.0_20-  w0001  _6mbps  .pcap
 │        │                  │           │      │
 │     dataset       subnet 169.231.112.0/20   transform threshold (Mb/s)
 │                                      window index 1 (0-based, 30 s each)
 prefix
```

Two more id shapes exist: `/merge` produces
`ctp-merged-{dataset}-{subnet}-{start}-{end}` (`merge.py:106`), and a
transformed CTP's **database id** is `ctp-transform-{safe_id}-{N}mbps`
(`transform.py:224`) while its **file** is `{safe_id}_{N}mbps.pcap`. The id and
the filename are not the same string — worth knowing before passing one where
the other belongs.

---

## 3. How one is selected

Automatic path, through orchestration:

```
intent → ParsedIntent.ctp_capacity_range → _select_ctp()
   → POST ctp-service:8001/ctps/select
       {"query": {"is_transformed": true,
                  "intensity_range_mbps": [lo, hi],
                  "intensity_direction": "download"},
        "limit": 10, "order_by": "intensity"}
   → first match wins  (orchestration_manager.py:179-190)
```

Three properties to know before designing a campaign on it:

- **It takes the first result**, ordered by intensity. It is not a random draw,
  so "one random CTP each" means the caller does the sampling and names the
  profile explicitly — which is exactly what the Teams script's `--ctp-name`
  expects.
- **No `ctp_capacity_range` means no cross traffic at all**
  (`orchestration_manager.py:135`). Deliberate: a silent 1–10 Mb/s default used
  to be synthesized, which put `172.16.1.20` packets into captures nobody had
  asked for.
- **Only `is_transformed: true` profiles are selectable.** A raw extracted CTP
  is never chosen.

---

## 4. The two blockers that remain

### 4.1 The corpus is one profile

```
services/substrate-worker/download/ctp-ctp_samples-169.231.112.0_20-w0001_6mbps.pcap
services/substrate-worker/upload/ctp-ctp_samples-169.231.112.0_20-w0001_6mbps.pcap
```

That pair is **committed to this repo** — it is not something copied onto the
VM, and a filesystem search of vm4 finds no other CTP-shaped pcap anywhere.
Measured directly (there is no `tshark` or `capinfos` on this host, worth
knowing before trusting any packet count from a script here):

| direction | packets | bytes | span | mean |
|---|---|---|---|---|
| download | 21,478 | 22.99 MB | 29.91 s | 6.15 Mb/s |
| upload | 31,087 | 23.13 MB | 29.94 s | 6.18 Mb/s |

The 30 s span is the pipeline's default window and the 6 Mb/s mean is the
transform threshold in the file's own name. A sample of ten from a corpus of
one is ten copies of the same trace.

### 4.2 "Cluster" has no referent

**There is no clustering step anywhere in the CTP service.** The corpus is
organised dataset → subnet (a prefix tree, `/32` leaves summed up to `/16`) →
window index, and queried by statistical descriptors — intensity, burstiness,
temporal correlation, structure. `grep -i cluster services/ctp-service/`
returns nothing.

Two things look like clusters and are not:

1. **`ReplayRequest.ctp_file`'s docstring** gives the example
   `'cluster26_tree10_profile424'` (`main.py:387`). That is a naming convention
   from another corpus; nothing here produces such a name, and the endpoint
   concatenates the string into a path without parsing it.
2. **`ctp_cluster` in the orchestration schema** (`schemas.py:65`) is accepted
   from the intent, carried onto the spec by `experiment_generator.py:88`, and
   **never read by the selection path**. `_select_ctp` takes
   `ctp_capacity_range` only. Setting `ctp_cluster` changes nothing about which
   profile is chosen; it is a label that travels with the record.

The nearest honest readings of "10 clusters" are **10 distinct source
subnets** (the tree already groups by prefix) or **10 non-overlapping
descriptor bands** — e.g. ten `intensity_range_mbps` windows. That is a
decision for Jaber and Sthanav, not one to guess at, and it is the first
question to put to them.

---

## 5. The command for Jaber's spec

**Spec:** 10 random clusters, one random CTP from each, names recorded, fixed
at 10 Mb/s down and up, 100 ms.

With a corpus in hand this is now a loop over the Teams script, not new
plumbing. Sketch to review, not to paste:

```bash
# 1. Draw the sample yourself and WRITE THE NAMES DOWN before running.
#    Selection does not randomise (§3), so the draw has to be explicit and
#    recorded, or the run is not reproducible.
python3 - <<'PY'
import json, random
random.seed(20261111)                        # recorded, so the draw repeats
clusters = json.load(open("clusters.json"))  # {cluster_id: [ctp_file, ...]}
picked = {c: random.choice(clusters[c]) for c in random.sample(sorted(clusters), 10)}
json.dump(picked, open("ctp_draw.json", "w"), indent=1)
PY

# 2. One run per drawn profile. The script brings up its own worker, mounts
#    the corpus read-only, loops the profile for the whole window, records the
#    name in ctp.json and in the experiment id, and tears everything down.
for ctp in $(python3 -c 'import json;print(" ".join(json.load(open("ctp_draw.json")).values()))'); do
  python3 services/orchestration/scripts/run_direct_teams_only_experiment.py \
      --capacities-mbps 10 \
      --latency-ms 100 \
      --duration-seconds 60 \
      --teams-url "<room url>" \
      --ctp-name "$ctp" \
      --ctp-host-dir /path/to/corpus/download \
      --result-subdir "ctp_sweep/$ctp" \
      --no-nest-mbps
done
```

Read before using it:

- **It is Teams, and the upload is symmetric.** The script sets
  `upload_mbps = capacity_mbps` (`:130`), which is what "10 Mb/s both
  directions" asks for — but it also means this is a *conferencing* run.
  A streaming equivalent does not exist; see below.
- **`--ctp-host-dir` is the directory holding `<name>.pcap`**, i.e. the
  `download/` side. The replay is download-only by design.
- **100 ms lands as one netem on `ns2:veth3`** and measures ~103 ms RTT, not
  2×100. The script's `latency_location: "upstream"` is what puts it there;
  the worker's label is a node position and `ns2:veth3` is the *download*
  path. `"both"` would apply the full figure to each interface and double the
  RTT rather than splitting it.
- **This produces the script's own artefacts, not CPUC rows.** No
  `cpuc_runs.csv`, no QoE label, no demand gate, no interference guard — see
  the next section.

---

## 6. What this still does not give us

**Nothing in `experiments/cpuc/` can replay a CTP.**
`grep -i ctp experiments/cpuc/*.py` returns one line, in `shaping.py`, saying
so. The CPUC drivers (`sweep.py`, `twitch.py`, `meet.py`, `vimeo.py`) talk to
the shared worker's `/shape` and nothing else, and every published number in
this study — the QoE label, the demand figure, `infra_share_pct`, the
background-traffic guard, the shaping check — comes from that path.

So a CTP campaign run through the Teams script today produces pcaps and its
own collector output, and none of the CPUC grading.

Carrying the replay into `runner.py` beside `apply_condition` turns out **not**
to be blocked on the worker. The obvious obstacle — the Teams pattern mounts
the corpus at container launch, and you cannot add a bind mount to a running
container — does not apply, because `/tmp/substrate-ctp` is already a writable
host bind mount at the same path inside the shared worker (§1). The replay
command itself is a `docker exec` into a container we are already talking to.

What is left is not plumbing:

- **The background-traffic guard would fail every CTP run.**
  `execute_app_run` treats `BackgroundContention` as fatal, and a CTP replay is
  background contention by construction. The guard has to learn the difference
  between a **declared** cross-traffic source (the CTP pnat address) and
  undeclared traffic — accounting and reporting the first, still failing on the
  second. Weaken that and the dataset loses the check that keeps it honest.
- **Demand would not say how much pressure was applied.** `infra_share_pct` is
  `infra / (app + infra)` and CTP bytes land in `other`, so a contended row
  would publish a demand figure indistinguishable from a baseline's. It needs
  `ctp_mb` / `ctp_mean_mbps` measured over the same sustained window, rather
  than the profile's nominal rate assumed.
- **Ordering.** `calibrate()` gates the run on iperf3 and ping over the shaped
  link, so the replay must start after it returns or the gate is applied to a
  contended probe.
- **The slug.** A CTP run has to be marked in the run id the way
  `drive_pramana` already slugs `_forcedq` and `_noav01`, or it can be averaged
  into a baseline.

## 7. What to ask Sthanav

1. What is a "cluster"? Source subnet, descriptor band, or something from a
   corpus this pipeline has not seen?
2. Where is the corpus? Only the repo-tracked profile is on vm4.
3. Conferencing or streaming for Jaber's 10 runs? The working script is Teams;
   a streaming equivalent means either the CPUC harness learns to replay, or
   the pattern is copied into a streaming script.
