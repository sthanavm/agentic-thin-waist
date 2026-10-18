#!/bin/bash
docker run --rm --privileged --dns 8.8.8.8 --shm-size=2g \
  -e MEET_ROOM=https://meet.google.com/kmr-oamc-jam \
  -v /tmp/bot_peer_test3.py:/probe.py:ro -v /tmp/mk_y4m2.py:/mk_y4m2.py:ro \
  --entrypoint python3 video-qoe-collector:latest /probe.py
