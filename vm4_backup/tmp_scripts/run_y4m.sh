#!/bin/bash
docker run --rm --privileged --dns 8.8.8.8 \
  -v /tmp/test_y4m2.py:/probe.py:ro -v /tmp/mk_y4m.py:/mk_y4m.py:ro \
  --entrypoint python3 video-qoe-collector:latest /probe.py
