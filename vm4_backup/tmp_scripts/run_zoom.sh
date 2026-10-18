#!/bin/bash
docker run --rm --privileged --dns 8.8.8.8 --shm-size=2g \
  -e ZOOM_URL="$1" -e ZOOM_NAME="${2:-pramana-probe}" \
  -v /tmp/zoom_tabs.py:/probe.py:ro \
  --entrypoint python3 video-qoe-collector:latest /probe.py
