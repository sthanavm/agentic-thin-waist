FROM --platform=$BUILDPLATFORM golang:1.25-bookworm AS netvibe-builder
# NetVibe in bench mode: the production probe server with a SQLite sink and no
# signaling, Postgres, ClickHouse or device login, plus the sensor that answers
# its probes. Serves POST /netvibe.
#
# Pinned by COMMIT, not by tag: there are no release artifacts, and the probe's
# own send timing and record schema ARE the measurement, so a moving source
# would silently change the numbers. Bump this deliberately and re-check the
# bench SQLite schema in summarize_netvibe().
ARG NETVIBE_COMMIT=e9e93bec4eb523f0db7815ea31104db6e7934b1b
ARG NETVIBE_REPO=https://github.com/SNL-UCSB/netvibe.git
WORKDIR /src
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
# A private repo needs a credential at build time; the build fails loudly here
# rather than producing an image whose /netvibe answers 422 for ever.
RUN git clone --filter=blob:none "$NETVIBE_REPO" . \
    && git checkout "$NETVIBE_COMMIT" \
    && CGO_ENABLED=0 go build -o /out/netvibe-bench ./backend/cmd/bench \
    && CGO_ENABLED=0 go build -o /out/netvibe-cli ./cli/cmd/netvibe-cli \
    && CGO_ENABLED=0 go build -o /out/netvibe-fleet ./cli/cmd/netvibe-fleet

FROM --platform=$BUILDPLATFORM golang:1.24-bookworm AS ndt-builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

ENV GOARCH=amd64
ENV GOOS=linux
ENV CGO_ENABLED=0

RUN git clone https://github.com/m-lab/ndt7-client-go.git \
    && cd ndt7-client-go \
    && git checkout v0.9.1 \
    && go build -v -o ./ndt7-client ./cmd/ndt7-client \
    && test -x ./ndt7-client \
    && install -m 0755 ./ndt7-client /usr/local/bin/ndt7-client

FROM ghcr.io/browserless/chromium:latest

USER root

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV DEBIAN_FRONTEND=noninteractive
ENV UV_PROJECT_ENVIRONMENT=/opt/substrate-worker-venv

# irtt (isochronous UDP round-trip probe, serves POST /irtt) is version-pinned
# deliberately: the probe's own send timing IS the measurement, so a bump that
# changed its scheduler or its JSON schema would silently change the numbers.
#
# 0.9.1-1 is what this base image's suite (Ubuntu 26.04 "resolute", universe)
# carries. Note that the base is `browserless/chromium:latest`, which is NOT
# pinned and has already moved 24.04 -> 26.04, so this pin is only as stable as
# that tag. When the base moves again the build FAILS here with "Version ... was
# not found" rather than silently installing a different probe, which is the
# intended behaviour: re-pin deliberately and re-check the JSON schema.
# Verified: 0.9.1 emits json format version 1, the same as 0.9.0 on the host.
RUN apt-get update && apt-get install -y \
    python3 python-is-python3 \
    tshark iputils-ping iproute2 \
    net-tools iperf3 \
    curl wget iptables byobu nano \
    speedtest-cli tcpreplay \
    python3-flask python3-requests \
    irtt=0.9.1-1 \
    sudo util-linux kmod \
    gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# The bench-mode binaries, where the /netvibe handler looks for them
# (NETVIBE_BIN_DIR, default /opt/netvibe/bin).
COPY --from=netvibe-builder /out/netvibe-bench /out/netvibe-cli /out/netvibe-fleet /opt/netvibe/bin/
ENV NETVIBE_BIN_DIR=/opt/netvibe/bin

WORKDIR /app

# Build the CCA preload shim. Lets per-flow setsockopt(TCP_CONGESTION)
# bypass the kernel's "tcp_allowed_congestion_control" gate that
# otherwise rejects non-default CCAs in non-init net namespaces. See
# src/cca_preload/cca_preload.c for the full rationale.
COPY services/substrate-worker/src/cca_preload/cca_preload.c /tmp/cca_preload.c
RUN gcc -O2 -fPIC -shared -o /usr/local/lib/cca_preload.so /tmp/cca_preload.c -ldl \
    && rm /tmp/cca_preload.c

COPY --from=ndt-builder /usr/local/bin/ndt7-client /usr/local/bin/ndt-client

RUN cd /usr/src/app \
    && npm install --no-save playwright-core@1.58.0 \
    && node ./node_modules/playwright-core/cli.js install chromium

COPY services/substrate-worker/pyproject.toml ./pyproject.toml
RUN uv sync --python /usr/bin/python3 --no-install-project

COPY services/substrate-worker/ .
COPY shared/ /shared/

RUN chmod +x src/browser/wrap-playwright-chrome.sh src/substrate/setup.sh src/substrate/setup_local.sh src/substrate/setup_aws.sh \
    && ./src/browser/wrap-playwright-chrome.sh

ENV PATH="/opt/substrate-worker-venv/bin:$PATH"
ENV PYTHONPATH="/app/src:/shared:/shared/clients/netgent/src:${PYTHONPATH:-}"
ENV HOST=0.0.0.0
ENV PORT=3000
ENV BROWSERLESS_WS_ENDPOINT=ws://127.0.0.1:3000/chromium/playwright
ENV SUBSTRATE_BROWSER_NAMESPACE=ns1
ENV SUBSTRATE_SHELL_NAMESPACE=ns1

EXPOSE 3000
EXPOSE 8002

ENTRYPOINT []
CMD ["bash", "-lc", "if [ \"${CONNECTIVITY_BACKEND:-local_docker}\" = \"aws\" ]; then SETUP_SCRIPT=./src/substrate/setup_aws.sh; MAIN_MODULE=substrate.main_aws; else SETUP_SCRIPT=./src/substrate/setup_local.sh; MAIN_MODULE=substrate.main_local; fi; echo \"Starting substrate profile: ${CONNECTIVITY_BACKEND:-local_docker} setup=${SETUP_SCRIPT} module=${MAIN_MODULE}\"; \"$SETUP_SCRIPT\" && (cd /usr/src/app && ./scripts/start.sh) & browserless_pid=$! && sleep 2 && kill -0 \"$browserless_pid\" && exec uv run --no-sync python -m \"$MAIN_MODULE\""]
