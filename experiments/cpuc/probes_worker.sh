#!/usr/bin/env bash
# Build and run the renamed substrate worker that probes_only.py runs against,
# on this machine or on another server over SSH.
#
#   probes_worker.sh [--host H] [--name N] [--port P] [--image I] <command> [options]
#
#   check                      read-only: can this host build and run a worker?
#   build --netvibe <src>      build the image (src: git URL or NetVibe clone)
#   up    --ctp-dir <dir>      start the worker with <dir>/*.pcap at /ctpsrc
#   status                     show the worker's state and health
#   down                       stop and remove the worker and its network
#
# --host is an SSH destination (an alias from ~/.ssh/config, or user@host).
# Without it, everything runs here. With it, --netvibe and --ctp-dir name
# paths on THIS machine, and the script copies what the server needs.
# See experiments/cpuc/PROBES_WORKER.md.
#
# Safety: only ever creates, stops or removes its own labelled container,
# network and builder. It never touches other containers (e.g. the shared
# substrate-worker), never restarts Docker and never prunes.
set -euo pipefail

HOST=${PROBES_WORKER_HOST:-}
NAME=${PROBES_WORKER_NAME:-jenil-fork-worker}
PORT=${PROBES_WORKER_PORT:-18202}
IMAGE=${PROBES_WORKER_IMAGE:-jenil-fork-substrate-worker:netvibe}
NETWORK=${PROBES_WORKER_NETWORK:-}
REMOTE_DIR=${PROBES_WORKER_REMOTE_DIR:-}
MIN_FREE_GB=${PROBES_WORKER_MIN_FREE_GB:-25}
LABEL=probes-worker
IRTT_PIN="irtt=0.9.0-2ubuntu0.24.04.3"

# Globals so the EXIT trap still sees them after the function that set them returns.
work=""
gd=""
builder=""

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }
usage() { sed -n '2,21p' "$SELF_FILE" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

SELF_FILE=${BASH_SOURCE[0]:-}
REPO_ROOT=""
if [ -f "$SELF_FILE" ]; then
  REPO_ROOT=$(cd "$(dirname "$SELF_FILE")/../.." && pwd)
fi

# ---------------------------------------------------------------- arguments

ARGS_GLOBAL=()
while [ $# -gt 0 ]; do
  case $1 in
    --host) HOST=${2:?--host needs a value}; shift 2 ;;
    --name) NAME=${2:?--name needs a value}; ARGS_GLOBAL+=(--name "$NAME"); shift 2 ;;
    --port) PORT=${2:?--port needs a value}; ARGS_GLOBAL+=(--port "$PORT"); shift 2 ;;
    --image) IMAGE=${2:?--image needs a value}; ARGS_GLOBAL+=(--image "$IMAGE"); shift 2 ;;
    --network) NETWORK=${2:?--network needs a value}; ARGS_GLOBAL+=(--network "$NETWORK"); shift 2 ;;
    -h|--help) usage 0 ;;
    -*) die "unknown option: $1 (global options go before the command)" ;;
    *) break ;;
  esac
done
CMD=${1:-}
[ -n "$CMD" ] || usage 1
shift

# jenil-fork-worker -> jenil-fork-net, as the workers were first set up by hand.
NETWORK=${NETWORK:-${NAME%-worker}-net}
REMOTE_DIR=${REMOTE_DIR:-probes-worker}  # per server: repo/ and netvibe.git/; <name>/ctp per worker

case $NAME in
  substrate-worker|*/*|"") die "refusing container name '$NAME'" ;;
esac
[[ $PORT =~ ^[0-9]+$ ]] || die "--port must be a number"
[ "$PORT" != 8002 ] || die "port 8002 is the shared substrate-worker's; pick another"

# ------------------------------------------------------------ local helpers

has() { command -v "$1" >/dev/null 2>&1; }
tmo() { if has timeout; then timeout "$@"; else shift; "$@"; fi; }

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

free_port_from() {
  local p=$1
  while port_busy "$p"; do p=$((p + 1)); [ "$p" -lt $(($1 + 50)) ] || return 1; done
  echo "$p"
}

netvibe_commit() {
  sed -n 's/^ARG NETVIBE_COMMIT=//p' "$REPO_ROOT/services/substrate-worker/Dockerfile" | head -1
}

netvibe_unreachable() {
  cat >&2 <<EOF
error: cannot reach NetVibe at: $1

The worker image builds NetVibe from source, and the NetVibe repo
(SNL-UCSB/netvibe) is private. Do one of:
  * get read access to SNL-UCSB/netvibe, so that 'git ls-remote $1'
    works for you here (an SSH key or credential helper), or
  * get a copy of someone's NetVibe clone (it must contain commit $2) and
    pass its path: --netvibe /path/to/netvibe
Nothing was built.
EOF
  exit 2
}

# Make a bare mirror of NetVibe at $2 from $1 (URL or clone), using this
# machine's own git access, and check it has the pinned commit.
mirror_netvibe() {
  local src=$1 dest=$2 commit
  commit=$(netvibe_commit)
  [ -n "$commit" ] || die "could not read NETVIBE_COMMIT from the Dockerfile"
  case $src in
    /*|./*|../*|~*)
      [ -d "$src" ] || die "--netvibe $src: no such directory. Pass a NetVibe clone or its git URL." ;;
  esac
  say "fetching NetVibe from $src"
  if [ -d "$src" ]; then
    git clone --quiet --bare "$src" "$dest" 2>/dev/null || netvibe_unreachable "$src" "$commit"
  else
    export GIT_TERMINAL_PROMPT=0 GIT_SSH_COMMAND="ssh -o BatchMode=yes"
    tmo 120 git ls-remote "$src" >/dev/null 2>&1 || netvibe_unreachable "$src" "$commit"
    git clone --quiet --bare "$src" "$dest" 2>/dev/null || netvibe_unreachable "$src" "$commit"
  fi
  git -C "$dest" cat-file -e "$commit^{commit}" 2>/dev/null \
    || die "NetVibe at $src does not contain commit $commit (the Dockerfile's pin). Fetch it into that clone and retry."
  say "NetVibe source OK, has $commit"
}

check_repo() {
  [ -n "$REPO_ROOT" ] || die "build must run from a checkout of the repo"
  local df=$REPO_ROOT/services/substrate-worker/Dockerfile
  [ -f "$df" ] || die "no Dockerfile at $df"
  grep -q "$IRTT_PIN" "$df" \
    || die "Dockerfile does not pin $IRTT_PIN; the image will not build on noble. Check out a branch with the irtt pin (#30)."
  grep -qF 'ip netns exec {cfg.server_namespace} {bench_bin}' \
    "$REPO_ROOT/services/substrate-worker/src/substrate/probes.py" \
    || die "probes.py lacks the NetVibe ns2 fix; NetVibe would record nothing. Check out the probe-only branch (#31)."
}

docker_free_gb() {
  local root
  root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
  df -Pk "$root" 2>/dev/null | awk 'NR==2 {print int($4/1048576)}' \
    || df -Pk / | awk 'NR==2 {print int($4/1048576)}'
}

ours() {  # is the existing container/network $2 (kind $1) one this script made?
  [ "$(docker "$1" inspect -f "{{index .Config.Labels \"$LABEL\"}}" "$2" 2>/dev/null)" = 1 ] 2>/dev/null \
    || [ "$(docker "$1" inspect -f "{{index .Labels \"$LABEL\"}}" "$2" 2>/dev/null)" = 1 ]
}

# ------------------------------------------------------- commands (on host)

cmd_check() {
  local ok=1 v
  say "host $(hostname), user $(id -un)"
  if has docker && docker info >/dev/null 2>&1; then
    say "docker: $(docker version -f '{{.Server.Version}}' 2>/dev/null) reachable without sudo"
  else
    echo "  FAIL docker not reachable for $(id -un) (not installed, or not in the docker group)"; ok=""
  fi
  if docker buildx version >/dev/null 2>&1; then say "buildx: $(docker buildx version | awk '{print $2}')"
  else echo "  FAIL docker buildx missing (needed to build)"; ok=""; fi
  for v in git curl; do has $v && say "$v: present" || { echo "  FAIL $v missing"; ok=""; }; done
  if has git && [ -x "$(git --exec-path)/git-daemon" ]; then say "git daemon: present"
  else echo "  FAIL git daemon missing (needed to build; Debian/Ubuntu package git-daemon-run or git)"; ok=""; fi
  if docker info >/dev/null 2>&1; then
    v=$(docker_free_gb)
    if [ "${v:-0}" -ge "$MIN_FREE_GB" ]; then say "disk: ${v} GB free for Docker"
    else echo "  WARN only ${v} GB free for Docker; a build needs about ${MIN_FREE_GB} GB (running an already-built image does not)"; fi
    docker image inspect "$IMAGE" >/dev/null 2>&1 && say "image $IMAGE: present" || say "image $IMAGE: not built yet"
    if docker container inspect "$NAME" >/dev/null 2>&1; then
      ours container "$NAME" && say "container $NAME: exists (ours)" \
        || { echo "  FAIL a container named $NAME exists and was not made by this script"; ok=""; }
    else say "container $NAME: free"; fi
    docker container inspect substrate-worker >/dev/null 2>&1 \
      && say "note: a shared substrate-worker runs here; this script leaves it alone"
  fi
  if port_busy "$PORT"; then echo "  FAIL port $PORT is in use"; ok=""; else say "port $PORT: free"; fi
  [ -n "$ok" ] && say "OK: this host can run a probes worker" || die "this host is not ready (see FAIL lines)"
}

cmd_build() {
  local src=""
  while [ $# -gt 0 ]; do
    case $1 in
      --netvibe) src=${2:-}; shift 2 ;;
      *) die "unknown option for build: $1" ;;
    esac
  done
  [ -n "$src" ] || die "build needs --netvibe <git url or path to a NetVibe clone>"
  has docker || die "docker not found"
  docker buildx version >/dev/null 2>&1 || die "docker buildx not available"
  check_repo

  # The separate builder keeps its own copy of every layer, exports the image,
  # and Docker loads a third copy: far more than the ~7 GB image itself. On
  # vm4 a build with 13 GB free ran out of space during export.
  local free
  free=$(docker_free_gb)
  [ "${free:-0}" -ge "$MIN_FREE_GB" ] || die "only ${free} GB free for Docker; the build needs about ${MIN_FREE_GB} GB. Free space first (e.g. 'docker rmi' your own unused images), or set PROBES_WORKER_MIN_FREE_GB if you know it needs less."
  say "${free} GB free for Docker"

  work=$(mktemp -d "${TMPDIR:-/tmp}/probes-worker.XXXXXX")
  builder=$NAME-builder
  cleanup() {
    if [ -n "$gd" ]; then kill "$gd" 2>/dev/null || true; wait "$gd" 2>/dev/null || true; fi
    if [ -n "$builder" ] && docker buildx rm "$builder" >/dev/null 2>&1; then say "builder $builder removed"; fi
    if [ -n "$work" ]; then rm -rf "$work"; fi
  }
  trap cleanup EXIT

  mirror_netvibe "$src" "$work/netvibe.git"

  # Serve the mirror to the build on loopback only: nothing secret enters the build.
  has git && [ -x "$(git --exec-path)/git-daemon" ] || die "'git daemon' is not available on this host"
  local gport url ok=""
  gport=$(free_port_from "${PROBES_WORKER_GIT_PORT:-9418}") || die "no free loopback port near 9418 for git daemon"
  git daemon --export-all --base-path="$work" --listen=127.0.0.1 --port="$gport" --reuseaddr &
  gd=$!
  url=git://127.0.0.1:$gport/netvibe.git
  for _ in $(seq 1 40); do
    git ls-remote "$url" >/dev/null 2>&1 && { ok=1; break; }
    sleep 0.5
  done
  [ -n "$ok" ] || die "git daemon did not come up on 127.0.0.1:$gport"

  # A separate BuildKit with its own storage: the default builder on vm4 fails
  # on a damaged containerd snapshot, and the fixes there (restart, prune)
  # would disturb other users. Harmless on hosts without that problem.
  docker buildx rm "$builder" >/dev/null 2>&1 || true
  say "creating builder $builder"
  docker buildx create --name "$builder" --driver docker-container \
    --driver-opt network=host \
    --buildkitd-flags '--allow-insecure-entitlement network.host' >/dev/null
  say "building $IMAGE (several minutes)"
  docker buildx build --builder "$builder" --progress=plain \
    --allow network.host --network=host --load \
    --build-arg "NETVIBE_REPO=$url" \
    -f "$REPO_ROOT/services/substrate-worker/Dockerfile" -t "$IMAGE" "$REPO_ROOT"
  say "built $IMAGE: $(docker image inspect -f '{{.Id}}' "$IMAGE")"
}

cmd_up() {
  local ctp=""
  while [ $# -gt 0 ]; do
    case $1 in
      --ctp-dir) ctp=${2:-}; shift 2 ;;
      *) die "unknown option for up: $1" ;;
    esac
  done
  [ -n "$ctp" ] || die "up needs --ctp-dir <directory holding <profile>.pcap files>"
  [ -d "$ctp" ] || die "--ctp-dir $ctp is not a directory"
  ctp=$(cd "$ctp" && pwd)
  ls "$ctp"/*.pcap >/dev/null 2>&1 || die "no .pcap files in $ctp"
  docker image inspect "$IMAGE" >/dev/null 2>&1 || die "image $IMAGE not found on $(hostname); build it first"
  docker container inspect "$NAME" >/dev/null 2>&1 && die "$NAME already exists on $(hostname); run 'down' first"
  port_busy "$PORT" && die "port $PORT on 127.0.0.1 is already in use on $(hostname)"

  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    ours network "$NETWORK" || die "network $NETWORK exists and was not made by this script; pass --network"
  else
    docker network create --label "$LABEL=1" "$NETWORK" >/dev/null
  fi
  say "starting $NAME on $(hostname) (127.0.0.1:$PORT -> 8002, /ctpsrc <- $ctp)"
  docker run -d --name "$NAME" --network "$NETWORK" --privileged \
    --label "$LABEL=1" \
    -p "127.0.0.1:$PORT:8002" \
    -v "$ctp:/ctpsrc:ro" \
    -e CAPTURE_DIR=/tmp/cap -e CTP_DIR=/tmp/ctp \
    "$IMAGE" >/dev/null
  local up=""
  for _ in $(seq 1 90); do
    curl -s -m 2 -o /dev/null "http://127.0.0.1:$PORT/health" && { up=1; break; }
    sleep 2
  done
  [ -n "$up" ] || die "$NAME did not answer /health within 3 minutes; see: docker logs $NAME"
  say "$NAME healthy; $(docker exec "$NAME" sh -c 'ls /ctpsrc/*.pcap | wc -l') profiles visible at /ctpsrc"
  cat <<EOF

On $(hostname), run probes_only.py from the repo root with:
  export CPUC_WORKER_CONTAINER=$NAME
  export PRAMANA_SUBSTRATE_URL=http://127.0.0.1:$PORT
  python3 experiments/cpuc/probes_only.py --mode irtt --ctp-profile <name> --ctp-dir $ctp
EOF
}

cmd_status() {
  if ! docker container inspect "$NAME" >/dev/null 2>&1; then
    say "$NAME: not present on $(hostname)"; return 0
  fi
  ours container "$NAME" || die "$NAME exists on $(hostname) but was not made by this script"
  say "$NAME on $(hostname): $(docker inspect -f '{{.State.Status}} since {{.State.StartedAt}}, image {{.Config.Image}}' "$NAME")"
  say "health: $(curl -s -m 3 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" || echo unreachable) on 127.0.0.1:$PORT"
}

cmd_down() {
  if docker container inspect "$NAME" >/dev/null 2>&1; then
    ours container "$NAME" || die "$NAME exists on $(hostname) but was not made by this script; leaving it alone"
    docker stop -t 20 "$NAME" >/dev/null
    docker rm "$NAME" >/dev/null
    say "$NAME removed"
  else
    say "$NAME: not present"
  fi
  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    if ours network "$NETWORK" || [ "$NETWORK" = jenil-fork-net ]; then
      docker network rm "$NETWORK" >/dev/null && say "$NETWORK removed"
    else
      say "network $NETWORK was not made by this script; leaving it"
    fi
  fi
}

# ----------------------------------------------------- remote (over SSH)

rq() { printf '%q ' "$@"; }

remote_script() {  # run this script on $HOST from stdin, nothing copied
  ssh -o BatchMode=yes "$HOST" "bash -s -- $(rq "${ARGS_GLOBAL[@]+"${ARGS_GLOBAL[@]}"}" "$@")" < "$SELF_FILE"
}

remote_home() { ssh -o BatchMode=yes "$HOST" 'printf %s "$HOME"'; }

remote_build() {
  local src="" rhome stage
  while [ $# -gt 0 ]; do
    case $1 in
      --netvibe) src=${2:-}; shift 2 ;;
      *) die "unknown option for build: $1" ;;
    esac
  done
  [ -n "$src" ] || die "build needs --netvibe <git url or path to a NetVibe clone on this machine>"
  check_repo
  has rsync || die "rsync is needed on this machine"
  ssh -o BatchMode=yes "$HOST" 'command -v rsync >/dev/null' || die "rsync is not installed on $HOST"
  rhome=$(remote_home)
  stage=$(mktemp -d "${TMPDIR:-/tmp}/probes-worker.XXXXXX")
  work=$stage
  trap '[ -n "$work" ] && rm -rf "$work"' EXIT

  # NetVibe is fetched HERE, with this machine's access, so the server never
  # needs credentials for the private repo.
  mirror_netvibe "$src" "$stage/netvibe.git"
  say "copying the repo (code only, no results) and NetVibe to $HOST:~/$REMOTE_DIR"
  ssh -o BatchMode=yes "$HOST" "mkdir -p $(rq "$REMOTE_DIR")"
  (cd "$REPO_ROOT" && git ls-files -z | grep -zvE '^experiments/[^/]+/results' \
    | rsync -a --from0 --files-from=- ./ "$HOST:$REMOTE_DIR/repo/")
  rsync -a --delete "$stage/netvibe.git/" "$HOST:$REMOTE_DIR/netvibe.git/"
  ssh -o BatchMode=yes "$HOST" \
    "cd $(rq "$rhome/$REMOTE_DIR/repo") && bash experiments/cpuc/probes_worker.sh $(rq "${ARGS_GLOBAL[@]+"${ARGS_GLOBAL[@]}"}") build --netvibe $(rq "$rhome/$REMOTE_DIR/netvibe.git")"
}

remote_up() {
  local ctp="" rhome
  while [ $# -gt 0 ]; do
    case $1 in
      --ctp-dir) ctp=${2:-}; shift 2 ;;
      *) die "unknown option for up: $1" ;;
    esac
  done
  [ -n "$ctp" ] || die "up needs --ctp-dir <directory of <profile>.pcap files on this machine>"
  [ -d "$ctp" ] || die "--ctp-dir $ctp is not a directory on this machine"
  ls "$ctp"/*.pcap >/dev/null 2>&1 || die "no .pcap files in $ctp"
  has rsync || die "rsync is needed on this machine"
  rhome=$(remote_home)
  say "copying CTP profiles to $HOST:~/$REMOTE_DIR/$NAME/ctp"
  ssh -o BatchMode=yes "$HOST" "mkdir -p $(rq "$REMOTE_DIR/$NAME/ctp")"
  rsync -a --delete --include='*.pcap' --exclude='*' "$ctp/" "$HOST:$REMOTE_DIR/$NAME/ctp/"
  remote_script up --ctp-dir "$rhome/$REMOTE_DIR/$NAME/ctp"
}

# ----------------------------------------------------------------- dispatch

if [ -n "$HOST" ]; then
  [ -f "$SELF_FILE" ] || die "--host needs the script run from a file"
  case $CMD in
    build) remote_build "$@" ;;
    up) remote_up "$@" ;;
    check|status|down) remote_script "$CMD" "$@" ;;
    *) die "unknown command: $CMD" ;;
  esac
else
  case $CMD in
    check) cmd_check "$@" ;;
    build) cmd_build "$@" ;;
    up) cmd_up "$@" ;;
    status) cmd_status ;;
    down) cmd_down ;;
    *) die "unknown command: $CMD (check | build | up | status | down)" ;;
  esac
fi
