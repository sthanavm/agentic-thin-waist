# How CTP replay works, and what it would take to run Jaber's spec

Written to be read before talking to Sthanav. **Nothing here was run.** Every
path and line number is from the merged tree at `a9a0fb3`, and every figure
about vm4 was read off vm4 on 2026-11-11.

The short version: the mechanism is complete and the worker on vm4 can replay
today. The *corpus* is not there. There is exactly **one** CTP profile on the
whole box, it carries no cluster id because nothing in this pipeline assigns
one, and it is not in the directory the running worker reads. Jaber's "10
random clusters, one random CTP each" cannot be satisfied as things stand, and
the gap is data, not code.

---

## 1. Where the profiles live

Three different directories matter and they are easy to confuse.

| Directory | Who writes it | Who reads it |
|---|---|---|
| `CTP_OUTPUT_DIR/users/...` | CTP Service `extract`/`transform` | the service itself |
| `$CTP_DIR/download/<name>.pcap`, `$CTP_DIR/upload/<name>.pcap` | whoever stages the files | **Substrate Worker `/replay`** |
| `services/substrate-worker/{download,upload}/` | the git checkout | nothing at runtime |

`/replay` builds its two paths by string concatenation and nothing else
(`services/substrate-worker/src/substrate/main.py:1475`):

```python
download_path = f"{CTP_DIR}/download/{cfg.ctp_file}.pcap"
upload_path   = f"{CTP_DIR}/upload/{cfg.ctp_file}.pcap"
```

so a profile is replayable **only** if both files exist under `$CTP_DIR` with
the same base name. `CTP_DIR` defaults to `/home/netreplica/config/ctp` in the
worker source and is set to `/tmp/substrate-ctp` by compose
(`docker-compose.yml:151`).

**On vm4 right now:**

```
$ curl -s localhost:8002/health
  "ctp_dir": "/tmp/substrate-ctp",  "tcpreplay_available": true,  "status": "ok"

$ ls /tmp/substrate-ctp
  (empty)
```

The one profile pair that exists on the box is the one **committed to the
repo**, not something copied onto the VM:

```
services/substrate-worker/download/ctp-ctp_samples-169.231.112.0_20-w0001_6mbps.pcap
services/substrate-worker/upload/ctp-ctp_samples-169.231.112.0_20-w0001_6mbps.pcap
```

Measured directly (no `tshark` or `capinfos` on this host — both absent, which
is worth knowing before anyone trusts a packet count from a script here):

| direction | packets | bytes | span | mean |
|---|---|---|---|---|
| download | 21,478 | 22.99 MB | 29.91 s | 6.15 Mb/s |
| upload | 31,087 | 23.13 MB | 29.94 s | 6.18 Mb/s |

The 30 s span is the pipeline's default window and the 6 Mb/s mean is the
transform threshold in its own file name. Both directions are present, so this
profile *would* replay through `/replay` as soon as it is staged under
`$CTP_DIR`.

---

## 2. How they are named — and why there is no cluster

The name is built in two places and nothing in between adds a cluster.

```python
# services/ctp-service/app/operations/extract.py:188
ctp_id = f"ctp-{dataset_name}-{subnet_str.replace('/', '_')}-w{window_index:04d}"

# services/ctp-service/app/operations/transform.py:142
downlink_dir / f"{safe_id}_{throughput_threshold_mbps:.0f}mbps.pcap"
```

Read the profile on vm4 straight off that:

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
the filename are not the same string — a script that passes a `ctp_id`
straight to `/replay` as `ctp_file` will get a 400.

**There is no clustering step anywhere in the CTP service.** The corpus is
organised by dataset → subnet (a prefix tree, `/32` leaves summed up to `/16`)
→ window index, and queried by statistical descriptors (intensity, burstiness,
temporal correlation, structure). `grep -i cluster services/ctp-service/`
returns nothing.

Two things look like clusters and are not:

1. **`ReplayRequest.ctp_file`'s docstring** gives the example
   `'cluster26_tree10_profile424'` (`main.py:387`). That is a naming
   convention from another corpus. Nothing in this repo produces such a name,
   and `/replay` does not parse the string — it concatenates it into a path.
2. **`ctp_cluster` in the orchestration schema** (`schemas.py:65`) is accepted
   from the intent, carried onto the spec by `experiment_generator.py:88`, and
   then **never read by the selection path**. `_select_ctp`
   (`orchestration_manager.py:125`) takes `ctp_capacity_range` only and
   queries the corpus on `{is_transformed: true, intensity_range_mbps,
   intensity_direction: "download"}`. Setting `ctp_cluster` changes nothing
   about which profile is chosen. It is a label that travels with the record.

So "10 random clusters" has no referent today. The nearest honest reading of
the intent is **10 distinct source subnets** (the tree already groups by
prefix) or **10 descriptor bands** (10 non-overlapping `intensity_range_mbps`
windows). That is a decision for Jaber and Sthanav, not one to guess at — and
it is the first question to put to them.

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
   → _ctp_pointer_for_worker(ctp) → worker /ctp/fetch → worker /replay
```

Three properties worth stating plainly before anyone designs a campaign on it:

- **It takes the first result**, ordered by intensity. It is not a random
  draw. Jaber's "one random CTP each" needs the caller to do the sampling and
  name the profile explicitly.
- **No `ctp_capacity_range` means no cross traffic at all.** `_select_ctp`
  short-circuits to `None` and `/replay` is skipped
  (`orchestration_manager.py:135`). This is deliberate: a silent 1–10 Mb/s
  default used to be synthesized, which put `172.16.1.20` packets into every
  capture nobody had asked for.
- **Only `is_transformed: true` profiles are selectable.** A raw extracted CTP
  is never chosen; something must run `/transform` at a target capacity first.

Manual path: skip selection entirely and name the file. That is what
`run_direct_google_meet_only_experiment.py` does, and it is the only script in
the tree that has actually replayed a CTP beside a measured application run.

---

## 4. How it is replayed beside an app run

`POST /replay` starts **two** `tcpreplay-edit` processes, detached, and returns
a `replay_id` (`main.py:1486-1520`):

```
ip netns exec ns2 tcpreplay-edit -i veth3 --pnat=<rules> [--duration=N] <download pcap>
ip netns exec ns1 tcpreplay-edit -i veth1 --pnat=<rules> [--duration=N] <upload pcap>
```

Download is injected from **ns2 on veth3**, upload from **ns1 on veth1**. That
matches what the qdisc counters say about this topology independently: during
a bulk download 149,310 packets crossed the netem on `ns2:veth3` against 3,334
during a bulk upload. So replayed cross traffic crosses the same shaped
`veth2`/`veth4` HTB and the same single netem the application does — it is
genuinely contending, not running alongside the bottleneck.

`--pnat` rewrites the capture's internal addresses onto our topology. The
working default in the direct script is:

```
--ctp-pnat 169.231.0.0/16:172.16.1.20
```

**Keep the CTP's target address different from the application's.** The
application client is `172.16.1.1`; sending cross traffic to the same address
makes the capture unsplittable, and every demand and `infra_share_pct` figure
in this study depends on splitting it. `172.16.1.20` is the address the
existing script uses and the one to keep.

Two further details from the direct script that `/replay` does not give you
(`run_direct_google_meet_only_experiment.py:187-213`):

- It replays the **download side only**, by calling `tcpreplay-edit` itself
  rather than `/replay`, because `/replay` insists on a matching
  `upload/<name>.pcap` and starts both directions.
- It passes **`--loop=0`**, so a 30 s profile repeats for the whole window.
  Without it a 60 s measurement spends its second half uncontended — and the
  QoE numbers from that half would be a baseline wearing a cross-traffic
  label. `/replay` does **not** pass `--loop`; with the profile on vm4 at
  29.91 s, a `/replay`-driven run longer than 30 s is half uncontended.

**Nothing in `experiments/cpuc/` can replay a CTP.** `grep -i ctp
experiments/cpuc/*.py` returns one line, in `shaping.py`, saying so. The CPUC
drivers (`sweep.py`, `twitch.py`, `meet.py`, `vimeo.py`) talk to the worker's
`/shape` and nothing else. Running Jaber's spec through the CPUC harness means
adding a replay call to `runner.py` beside `apply_condition`, or driving it
from the orchestration script instead and giving up the CPUC labelling.

---

## 5. The command for Jaber's spec

**Spec:** 10 random clusters, one random CTP from each, names recorded, fixed
at 10 Mb/s down and up, 100 ms.

It cannot run as written today, for two reasons that are worth separating:

1. **No clusters exist** (§2). Until "cluster" is given a referent, there is
   nothing to sample 10 of.
2. **One profile exists** (§1). Even with the definition settled, a sample of
   10 from a corpus of 1 is 10 copies of the same trace.

What the command *would* be, once a corpus is extracted and staged — this is a
sketch to review with Sthanav, not something to paste:

```bash
# 0. Stage the corpus where the running worker reads it. CTP_DIR on vm4 is
#    /tmp/substrate-ctp and is empty; both directions are required.
#    (The existing worker is shared — do not restart it to change CTP_DIR.)
sudo mkdir -p /tmp/substrate-ctp/{download,upload}
sudo cp <corpus>/download/*.pcap /tmp/substrate-ctp/download/
sudo cp <corpus>/upload/*.pcap   /tmp/substrate-ctp/upload/

# 1. Draw the sample yourself and WRITE THE NAMES DOWN before running.
#    Selection does not randomise (§3), so the draw has to be explicit and
#    recorded, or the run is not reproducible.
python3 - <<'PY'
import json, random
random.seed(20261111)                      # recorded, so the draw repeats
clusters = json.load(open("clusters.json"))  # {cluster_id: [ctp_file, ...]}
picked = {c: random.choice(clusters[c]) for c in random.sample(sorted(clusters), 10)}
json.dump(picked, open("ctp_draw.json", "w"), indent=1)
print(json.dumps(picked, indent=1))
PY

# 2. One run per drawn profile: shape 10/10 at 100 ms, start the replay,
#    run the app, stop the replay.
for ctp in $(python3 -c 'import json;print(" ".join(json.load(open("ctp_draw.json")).values()))'); do
  # ShapeRequest field names are exact, and latency_location is NOT optional
  # in effect: omit it and apply_shaping() deletes netem and adds none, so the
  # run is shaped to 10/10 with zero added latency and nothing says so.
  curl -s -X POST localhost:8002/shape -H 'content-type: application/json' \
       -d '{"downstream_iface": "veth2", "upstream_iface": "veth4",
            "download_mbps": 10, "upload_mbps": 10,
            "latency_ms": 100, "latency_location": "upstream",
            "qdisc": "pfifo", "buffer_packets": 1000, "verify": true}'

  rid=$(curl -s -X POST localhost:8002/replay -H 'content-type: application/json' \
        -d "{\"ctp_file\": \"$ctp\",
             \"pnat\": \"169.231.0.0/16:172.16.1.20,128.111.0.0/16:172.16.1.20\",
             \"duration_seconds\": 90}" | python3 -c 'import json,sys;print(json.load(sys.stdin)["replay_id"])')

  # ... the app run goes here ...

  curl -s -X DELETE "localhost:8002/replay/$rid"
done
```

Read before using it:

- **`latency_ms: 100` is one-way-on-one-interface**, i.e. the whole 100 ms
  lands as a single netem delay on `ns2:veth3` and measures ~103 ms RTT, not
  2×100. `latency_location: "upstream"` is what puts it there — the worker's
  label is a node position, and `ns2:veth3` is the *download* path. `"both"`
  applies the full 100 ms to each interface and doubles the RTT; it does not
  split it. See the corrected docstring in `shaping.py`.
- **`/replay` does not loop.** With a 30 s profile and a 90 s window, two
  thirds of the run is uncontended. Either pass a `duration_seconds` no longer
  than the profile, or call `tcpreplay-edit --loop=0` directly the way the
  direct script does.
- **Nothing above records the draw into the run record.** The profile name has
  to reach `record.json`, or the dataset will have ten rows that differ by a
  variable nobody can see — which is exactly the failure this study keeps
  finding in other shapes.
- **The worker is shared.** A replay started and not deleted keeps injecting
  into everyone's namespace. Every `POST /replay` needs its matching
  `DELETE /replay/{id}`, including on the error path.

## 6. What to ask Sthanav

1. What is a "cluster"? Source subnet, descriptor band, or something from a
   corpus this pipeline has not seen?
2. Where is the corpus Harika copied? Only the repo-tracked profile is on vm4,
   and `$CTP_DIR` is empty.
3. Should the CPUC harness learn to replay, or should these runs go through
   the orchestration path and lose the CPUC QoE labelling?
