#!/bin/bash
docker run --rm --privileged --dns 8.8.8.8 \
  -v /tmp/probe_conf.py:/probe.py:ro --entrypoint python3 video-qoe-collector:latest /probe.py
