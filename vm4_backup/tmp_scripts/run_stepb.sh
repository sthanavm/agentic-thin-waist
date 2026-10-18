#!/bin/bash
# Step B first pass: 2 apps x 3 latencies x 2 trials = 12 solo runs.
# Pre-registered in validation/THRESHOLDS.md, committed before this ran.
cd ~/sthanav-agentic-thin-waist/experiments/pramana || exit 1
R=~/sthanav-agentic-thin-waist/experiments/pramana/results/pramana_runs
rm -f /tmp/stepb_DONE
first=1
for T in 1 2; do
  for LAT in 20 50 150; do
    for APP in youtube vimeo; do
      echo "===== STEPB $APP ${LAT}ms trial $T start $(date -u +%FT%TZ) free=$(df -m / | tail -1 | awk '{print $4}')MB load1=$(cut -d' ' -f1 /proc/loadavg) ====="
      BW=6 DUR=180 TRIAL=$T python3 run_stepb.py "$APP" "$LAT" 2>&1
      rc=$?
      if [ $rc -eq 3 ]; then
        echo "BATCH STOPPED: disk guard at $APP ${LAT}ms t$T"
        { date -u +%FT%TZ; echo STOPPED_DISK; } > /tmp/stepb_DONE; exit 3
      fi
      echo "===== STEPB $APP ${LAT}ms trial $T done rc=$rc $(date -u +%FT%TZ) ====="
      if [ $first -eq 1 ]; then
        first=0
        D=$(ls -dt $R/${APP}_6mbps_${LAT}ms_*solo_t${T}_* 2>/dev/null | head -1)
        n=$(ls -1 "$D/qoe" 2>/dev/null | wc -l)
        nulls=$(grep -c '"rendered_fps": null' /tmp/stepb.log 2>/dev/null)
        echo "HEALTHCHECK dir=$(basename ${D:-none}) qoe_files=$n null_fps=$nulls"
        if [ "$n" -lt 1 ] || [ "$nulls" -gt 0 ]; then
          echo "BATCH STOPPED: first run produced no usable data"
          { date -u +%FT%TZ; echo STOPPED_UNHEALTHY; } > /tmp/stepb_DONE; exit 4
        fi
        echo "HEALTHCHECK passed; continuing"
      fi
    done
  done
done
echo "ALL_STEPB_DONE"
{ date -u +%FT%TZ; grep -c "STEPB .* trial .* done" /tmp/stepb.log; } > /tmp/stepb_DONE
