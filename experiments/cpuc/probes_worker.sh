#!/usr/bin/env bash
# Build and start the renamed substrate worker that probes_only.py runs against.
#
#   probes_worker.sh build --netvibe <url-or-path>
#   probes_worker.sh up    --ctp-dir <dir of <profile>.pcap files>
#   probes_worker.sh down
#
# See experiments/cpuc/PROBES_WORKER.md. This only ever creates, starts, stops
# or removes its own container, network and builder (names below). It never
# touches the shared substrate-worker, never restarts Docker and never prunes.
set -euo pipefail

CONTAINER=jenil-fork-worker
NETWORK=jenil-fork-net
HOST_PORT=18202
IMAGE=${PROBES_WORKER_IMAGE:-jenil-fork-substrate-worker:netvibe}
BUILDER=probes-worker-builder
DAEMON_PORT=${PROBES_WORKER_GIT_PORT:-9418}
work=""
mirror=""
gd=""

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
DOCKERFILE=$REPO_ROOT/services/substrate-worker/Dockerfile
IRTT_PIN="irtt=0.9.0-2ubuntu0.24.04.3"

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }

usage() {
  sed -n '2,7p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

netvibe_unreachable() {
  cat >&2 <<EOF
error: cannot reach NetVibe at: $1

The worker image builds NetVibe from source, and the NetVibe repo
(SNL-UCSB/netvibe) is private. Do one of:
  * get read access to SNL-UCSB/netvibe and make sure plain 'git ls-remote $1'
    works for you on this host (an SSH key or credential helper), or
  * copy someone's NetVibe clone (it must contain commit $2) to this host and
    pass its path: --netvibe /path/to/netvibe
Nothing was built.
EOF
  exit 2
}

cmd_build() {
  local src=""
  while [ $# -gt 0 ]; do
    case $1 in
      --netvibe) src=${2:-}; shift 2 ;;
      -h|--help) usage ;;
      *) die "unknown option for build: $1" ;;
    esac
  done
  [ -n "$src" ] || die "build needs --netvibe <git url or path to a NetVibe clone>"
  command -v docker >/dev/null || die "docker not found"
  docker buildx version >/dev/null 2>&1 || die "docker buildx not available"
  [ -f "$DOCKERFILE" ] || die "no Dockerfile at $DOCKERFILE"
  grep -q "$IRTT_PIN" "$DOCKERFILE" || die "Dockerfile does not pin $IRTT_PIN; the image will not build on noble. Check out a branch that has the irtt pin (#30)."
  grep -qF 'ip netns exec {cfg.server_namespace} {bench_bin}' "$REPO_ROOT/services/substrate-worker/src/substrate/probes.py" \
    || die "probes.py lacks the NetVibe ns2 fix; NetVibe would record nothing. Check out the probe-only branch (#31)."

  # The separate builder keeps its own copy of every layer, exports the image,
  # and Docker loads a third copy: far more than the ~7 GB image itself. On
  # vm4 a build with 13 GB free ran out of space during export.
  local root free min=${PROBES_WORKER_MIN_FREE_GB:-25}
  root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
  free=$(df -Pk "$root" 2>/dev/null | awk 'NR==2 {print int($4/1048576)}')
  free=${free:-$(df -Pk / | awk 'NR==2 {print int($4/1048576)}')}
  [ "$free" -ge "$min" ] || die "only ${free} GB free for Docker ($root); the build needs about ${min} GB. Free space first (e.g. remove your own unused images with 'docker rmi <name>'), or set PROBES_WORKER_MIN_FREE_GB if you know it needs less."
  say "${free} GB free for Docker"

  local commit
  commit=$(sed -n 's/^ARG NETVIBE_COMMIT=//p' "$DOCKERFILE" | head -1)
  [ -n "$commit" ] || die "could not read NETVIBE_COMMIT from the Dockerfile"

  # Mirror NetVibe on the host with the host's own git credentials, then serve
  # the mirror to the build on loopback only. Nothing secret enters the build.
  # globals, not locals: the EXIT trap runs after this function returns
  work=$(mktemp -d "${TMPDIR:-/tmp}/probes-worker.XXXXXX")
  mirror=$work/netvibe.git
  cleanup() {
    [ -n "$gd" ] && kill "$gd" 2>/dev/null && wait "$gd" 2>/dev/null || true
    docker buildx rm "$BUILDER" >/dev/null 2>&1 && say "builder $BUILDER removed" || true
    [ -n "$work" ] && rm -rf "$work"
  }
  trap cleanup EXIT

  say "fetching NetVibe from $src"
  case $src in
    /*|./*|../*|~*)
      [ -d "$src" ] || die "--netvibe $src: no such directory. Pass a NetVibe clone on this host, or its git URL." ;;
  esac
  if [ -d "$src" ]; then
    git clone --quiet --bare "$src" "$mirror" 2>/dev/null || netvibe_unreachable "$src" "$commit"
  else
    GIT_TERMINAL_PROMPT=0 timeout 120 git ls-remote "$src" >/dev/null 2>&1 || netvibe_unreachable "$src" "$commit"
    GIT_TERMINAL_PROMPT=0 git clone --quiet --bare "$src" "$mirror" || netvibe_unreachable "$src" "$commit"
  fi
  git -C "$mirror" cat-file -e "$commit^{commit}" 2>/dev/null \
    || die "NetVibe at $src does not contain commit $commit (the Dockerfile's pin). Fetch it into that clone and retry."
  say "NetVibe source OK, has $commit"

  if (exec 3<>"/dev/tcp/127.0.0.1/$DAEMON_PORT") 2>/dev/null; then
    die "port $DAEMON_PORT on 127.0.0.1 is in use; set PROBES_WORKER_GIT_PORT to a free port"
  fi
  git daemon --export-all --base-path="$work" --listen=127.0.0.1 --port="$DAEMON_PORT" --reuseaddr &
  gd=$!
  local url=git://127.0.0.1:$DAEMON_PORT/netvibe.git ok=""
  for _ in $(seq 1 40); do
    git ls-remote "$url" >/dev/null 2>&1 && { ok=1; break; }
    sleep 0.5
  done
  [ -n "$ok" ] || die "git daemon did not come up on 127.0.0.1:$DAEMON_PORT ('git daemon' may need the git-daemon package)"

  # Separate BuildKit with its own storage: the daemon's default builder on vm4
  # fails on a damaged containerd snapshot, and the fixes there (restart/prune)
  # would disturb other users.
  docker buildx rm "$BUILDER" >/dev/null 2>&1 || true
  say "creating builder $BUILDER"
  docker buildx create --name "$BUILDER" --driver docker-container \
    --driver-opt network=host \
    --buildkitd-flags '--allow-insecure-entitlement network.host' >/dev/null
  say "building $IMAGE (several minutes)"
  docker buildx build --builder "$BUILDER" --progress=plain \
    --allow network.host --network=host --load \
    --build-arg "NETVIBE_REPO=$url" \
    -f "$DOCKERFILE" -t "$IMAGE" "$REPO_ROOT"
  say "built $IMAGE: $(docker image inspect -f '{{.Id}}' "$IMAGE")"
}

cmd_up() {
  local ctp=""
  while [ $# -gt 0 ]; do
    case $1 in
      --ctp-dir) ctp=${2:-}; shift 2 ;;
      -h|--help) usage ;;
      *) die "unknown option for up: $1" ;;
    esac
  done
  [ -n "$ctp" ] || die "up needs --ctp-dir <directory holding <profile>.pcap files>"
  [ -d "$ctp" ] || die "--ctp-dir $ctp is not a directory"
  ctp=$(cd "$ctp" && pwd)
  ls "$ctp"/*.pcap >/dev/null 2>&1 || die "no .pcap files in $ctp"
  docker image inspect "$IMAGE" >/dev/null 2>&1 || die "image $IMAGE not found; run: $0 build --netvibe <source>"
  if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    die "$CONTAINER already exists; run '$0 down' first"
  fi
  if (exec 3<>"/dev/tcp/127.0.0.1/$HOST_PORT") 2>/dev/null; then
    die "port $HOST_PORT on 127.0.0.1 is already in use"
  fi

  docker network inspect "$NETWORK" >/dev/null 2>&1 || docker network create "$NETWORK" >/dev/null
  say "starting $CONTAINER (127.0.0.1:$HOST_PORT -> 8002, /ctpsrc <- $ctp)"
  docker run -d --name "$CONTAINER" --network "$NETWORK" --privileged \
    -p "127.0.0.1:$HOST_PORT:8002" \
    -v "$ctp:/ctpsrc:ro" \
    -e CAPTURE_DIR=/tmp/cap -e CTP_DIR=/tmp/ctp \
    "$IMAGE" >/dev/null
  local up=""
  for _ in $(seq 1 90); do
    curl -s -m 2 -o /dev/null "http://127.0.0.1:$HOST_PORT/health" && { up=1; break; }
    sleep 2
  done
  [ -n "$up" ] || die "$CONTAINER did not answer /health within 3 minutes; see: docker logs $CONTAINER"
  say "$CONTAINER healthy; $(docker exec "$CONTAINER" sh -c 'ls /ctpsrc/*.pcap | wc -l') profiles visible at /ctpsrc"
  cat <<EOF

Run probes_only.py from the repo root with:
  export CPUC_WORKER_CONTAINER=$CONTAINER
  export PRAMANA_SUBSTRATE_URL=http://127.0.0.1:$HOST_PORT
  python3 experiments/cpuc/probes_only.py --mode irtt --ctp-profile <name> --ctp-dir $ctp
EOF
}

cmd_down() {
  if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
    docker stop -t 20 "$CONTAINER" >/dev/null
    docker rm "$CONTAINER" >/dev/null
    say "$CONTAINER removed"
  fi
  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    docker network rm "$NETWORK" >/dev/null && say "$NETWORK removed"
  fi
}

case ${1:-} in
  build) shift; cmd_build "$@" ;;
  up) shift; cmd_up "$@" ;;
  down) shift; cmd_down ;;
  -h|--help|"") usage ;;
  *) die "unknown command: $1 (build | up | down)" ;;
esac
