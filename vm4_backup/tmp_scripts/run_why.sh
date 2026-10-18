#!/bin/bash
# Four experiments x 3 trials, one at a time, each behind the host-load gate.
# Writes /tmp/why_DONE at the end so progress can be checked without holding
# an ssh session open.
cd ~/sthanav-agentic-thin-waist/experiments/pramana || exit 1
rm -f /tmp/why_DONE
for T in 1 2 3; do
  for E in yt_first vm_first vimeo_cpu yt_480p; do
    case $E in
      yt_first)  TR=$((200 + T)) ;;
      vm_first)  TR=$((210 + T)) ;;
      vimeo_cpu) TR=$((220 + T)) ;;
      yt_480p)   TR=$((230 + T)) ;;
    esac
    echo "===== WHY $E trial $T TRIAL=$TR start $(date -u +%FT%TZ) load1=$(cut -d' ' -f1 /proc/loadavg) ====="
    BW=6 DUR=180 TRIAL=$TR python3 run_freeze_why.py "$E" 2>&1
    echo "===== WHY $E trial $T done $(date -u +%FT%TZ) ====="
  done
done
echo "ALL_WHY_DONE"
{ date -u +%FT%TZ; grep -c "WHY .* trial .* done" /tmp/why.log; } > /tmp/why_DONE
