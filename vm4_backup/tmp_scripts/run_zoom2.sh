#!/bin/bash
docker run --rm --privileged --dns 8.8.8.8 --shm-size=2g \
  -e ZOOM_URL="$1" -v /tmp/zoom_frames.py:/probe.py:ro -v /tmp/zoomshots:/out \
  --entrypoint python3 video-qoe-collector:latest /probe.py
