# Probe-only worker

`probes_only.py` runs IRTT and NetVibe against its own renamed copy of the
substrate worker, never the shared `substrate-worker`. `probes_worker.sh` builds
that copy and starts it as `jenil-fork-worker`: network `jenil-fork-net`, port
`127.0.0.1:18202 -> 8002`, CTP profiles mounted read-only at `/ctpsrc`.

## 1. Build the image

From a checkout of this branch (it has the irtt pin and the NetVibe fix):

```bash
experiments/cpuc/probes_worker.sh build --netvibe <source>
```

`<source>` is either:

- a git URL you can already read on this host, for example
  `git@github.com:SNL-UCSB/netvibe.git`. The NetVibe repo is private, so this
  needs your own access.
- a path to a NetVibe clone on this host. If you don't have repo access, ask
  for a copy of someone's clone; it must contain the commit pinned in the
  Dockerfile (`NETVIBE_COMMIT`).

If the source can't be read, the script stops before building and says why.

The build takes several minutes. It runs in its own temporary buildx builder,
because the default Docker builder on vm4 fails on a damaged snapshot. The
builder is removed when the script exits, whether the build succeeds or not.
NetVibe is mirrored on the host and served to the build over loopback, so no
credentials go into the image or its build args.

## 2. Start the worker

```bash
experiments/cpuc/probes_worker.sh up --ctp-dir /path/to/profiles
```

`--ctp-dir` is a directory of `<profile>.pcap` files, for example #29's
`experiments/cpuc/results/vimeo_ctp_profiles_used`. The script waits until
`/health` answers, then prints the exports `probes_only.py` needs.

## 3. Run

```bash
export CPUC_WORKER_CONTAINER=jenil-fork-worker
export PRAMANA_SUBSTRATE_URL=http://127.0.0.1:18202
python3 experiments/cpuc/probes_only.py --mode irtt \
    --ctp-profile cluster68_tree2_profile6239 \
    --ctp-dir /path/to/profiles
```

`--mode` is `irtt`, `netvibe` or `both`. Leave out `--ctp-profile` for a
no-CTP baseline. `probes_only.py` refuses to run against `substrate-worker`.

## 4. Stop

```bash
experiments/cpuc/probes_worker.sh down
```

This removes `jenil-fork-worker` and `jenil-fork-net`. It doesn't touch the
image, any other container, or the Docker daemon.
