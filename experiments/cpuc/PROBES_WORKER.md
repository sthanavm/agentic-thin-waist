# Probe-only worker

`probes_only.py` runs IRTT and NetVibe against its own renamed copy of the
substrate worker, never the shared `substrate-worker`. `probes_worker.sh`
builds that copy and runs it, on this machine or on any server you can SSH to.

```
experiments/cpuc/probes_worker.sh [--host H] [--name N] [--port P] [--image I] <command>
```

| option | default | meaning |
|---|---|---|
| `--host` | this machine | SSH destination: an alias from `~/.ssh/config`, or `user@host` |
| `--name` | `jenil-fork-worker` | container name; the network is the same name with `-worker` swapped for `-net` |
| `--port` | `18202` | host port, bound to 127.0.0.1, mapped to the worker's 8002 |
| `--image` | `jenil-fork-substrate-worker:netvibe` | image tag to build or run |

Use a different `--name` and `--port` for each worker that shares a host.

## Commands

**`check`** is read-only. It reports whether the host can build and run a
worker: Docker access without sudo, buildx, git and git daemon, curl, free
disk, and whether the name and port are free.

**`build --netvibe <src>`** builds the image. `<src>` is either a NetVibe git
URL you can read, such as `git@github.com:SNL-UCSB/netvibe.git`, or a path to
a NetVibe clone. The NetVibe repo is private. Without access, ask someone for
a copy of their clone; it must contain the commit pinned in the Dockerfile
(`NETVIBE_COMMIT`). If the source can't be read, the script stops before
building and says why.

The build takes several minutes and needs about 25 GB free for Docker. It
runs in its own temporary buildx builder, which is removed afterwards; vm4's
default builder fails on a damaged snapshot. NetVibe is served to the build
over loopback, so no credentials go into the image.

**`up --ctp-dir <dir>`** starts the worker with `<dir>/*.pcap` mounted
read-only at `/ctpsrc`, waits for `/health`, and prints the exports
`probes_only.py` needs.

**`status`** shows the worker's state and health. **`down`** removes the
worker and its network.

## With `--host`

`--netvibe` and `--ctp-dir` are paths on **your** machine. The script copies
what the server needs into `~/probes-worker/` there:

- `build` fetches NetVibe on your machine with your own access, then copies
  the repo's code (no results) to `repo/` and the NetVibe mirror to
  `netvibe.git/`. The server never needs access to the private repo.
- `up` copies the profile pcaps to `<name>/ctp/`.
- `check`, `status` and `down` copy nothing.

The server needs Docker (usable without sudo), buildx, git with
`git daemon`, curl and rsync. The script installs nothing; run `check` first.

## Example: two workers on one server

```bash
W=experiments/cpuc/probes_worker.sh
$W --host snl6 check
$W --host snl6 build --netvibe git@github.com:SNL-UCSB/netvibe.git
$W --host snl6 --name probes-a-worker --port 18203 up --ctp-dir experiments/cpuc/results/vimeo_ctp_profiles_used
$W --host snl6 --name probes-b-worker --port 18204 up --ctp-dir experiments/cpuc/results/vimeo_ctp_profiles_used
$W --host snl6 --name probes-a-worker --port 18203 status
```

Then on the server, from `~/probes-worker/repo`:

```bash
export CPUC_WORKER_CONTAINER=probes-a-worker
export PRAMANA_SUBSTRATE_URL=http://127.0.0.1:18203
python3 experiments/cpuc/probes_only.py --mode irtt \
    --ctp-profile cluster68_tree2_profile6239 --ctp-dir ~/probes-worker/probes-a-worker/ctp
```

`--mode` is `irtt`, `netvibe` or `both`. Leave out `--ctp-profile` for a
no-CTP baseline.

## Safety

The script labels everything it creates. `down` and `status` refuse to touch a
container or network without that label, so the shared `substrate-worker`
and other people's containers are safe even if you pass their name. It
never restarts Docker and never prunes.
