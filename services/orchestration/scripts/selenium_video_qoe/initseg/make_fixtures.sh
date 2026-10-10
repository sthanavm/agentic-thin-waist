#!/usr/bin/env bash
# Real init segments for initseg_test.js, so the parser is validated against
# bytes a muxer actually wrote rather than bytes this repo invented.
# ffprobe's reading of the same files is the independent ground truth.
set -euo pipefail
out="${1:-./seg}"
mkdir -p "$out"; cd "$out"
V="testsrc=size=320x240:rate=24"
ffmpeg -hide_banner -loglevel error -f lavfi -i "$V" -t 1 -c:v libaom-av1 -cpu-used 8 -y av01_plain.mp4
# +dash writes an EMPTY av1C (size 8). Kept deliberately: the parser must
# report no codec for it rather than fabricate one.
ffmpeg -hide_banner -loglevel error -f lavfi -i "$V" -t 1 -c:v libaom-av1 -cpu-used 8 \
    -movflags +dash+frag_keyframe+empty_moov -f mp4 -y av01.mp4
ffmpeg -hide_banner -loglevel error -f lavfi -i "$V" -t 1 -c:v libaom-av1 -cpu-used 8 -y av01.webm
ffmpeg -hide_banner -loglevel error -f lavfi -i "$V" -t 1 -c:v libx264 -preset ultrafast \
    -movflags +dash+frag_keyframe+empty_moov -f mp4 -y avc1.mp4
ffmpeg -hide_banner -loglevel error -f lavfi -i "$V" -t 1 -c:v libvpx-vp9 -speed 8 -y vp09.webm
ffmpeg -hide_banner -loglevel error -f lavfi -i sine=frequency=440 -t 1 -c:a aac \
    -movflags +dash+frag_keyframe+empty_moov -f mp4 -y mp4a.mp4
ffmpeg -hide_banner -loglevel error -f lavfi -i sine=frequency=440 -t 1 -c:a libopus -y opus.webm
echo "fixtures in $out"
