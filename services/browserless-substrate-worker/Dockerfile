# Pinned, not :latest. Browserless moved its base from Ubuntu 24.04 to 26.04 in
# v2.57.0 (2026-09-12), and the Playwright this image pins cannot install a
# browser there. Measured in the amd64 image, which is the platform the worker
# actually runs on:
#
#   :latest  (ubuntu 26.04) -> playwright-core@1.58.0 `install chromium` fails:
#       "ERROR: Playwright does not support chromium on ubuntu26.04-x64",
#       and it first REMOVES browserless's own bundled chromium, so the image
#       would be left with no browser at all.
#   v2.56.7  (ubuntu 24.04) -> same command succeeds (Chromium 145.0.7632.6).
#
# v2.56.7 is the newest browserless tag still built on Ubuntu 24.04. Pinned by
# digest as well as tag: the digest is the multi-arch index, so this stays
# amd64+arm64 while being immutable. Bump only together with the
# playwright-core version below, and only after checking the new base's
# Playwright support.
FROM ghcr.io/browserless/chromium:v2.56.7@sha256:b1ba7b054af2891a8199f884d4bd249cf8c3bd2fa8a97b339077e40f92803ba8

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iproute2 \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY services/browserless-substrate-worker/wrap-playwright-chrome.sh /usr/local/bin/wrap-playwright-chrome.sh

RUN chmod +x /usr/local/bin/wrap-playwright-chrome.sh \
    && /usr/local/bin/wrap-playwright-chrome.sh

ENV HOST=0.0.0.0
ENV PORT=3000
ENV SUBSTRATE_BROWSER_NAMESPACE=ns1
