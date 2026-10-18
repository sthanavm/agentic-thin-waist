#!/bin/bash
docker run --rm --privileged --dns 8.8.8.8 \
  -v /tmp/tubi_two.py:/probe.py:ro -v /tmp/probe_out:/out \
  --entrypoint python3 video-qoe-collector:latest /probe.py
