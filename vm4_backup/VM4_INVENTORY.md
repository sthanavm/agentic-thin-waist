# vm-4 inventory — 2026-10-17T23:10:30Z

Collected for the migration backup. Sizes as reported on the VM.

## Host
docker-vm-4
 23:10:30 up 113 days, 4:12, 9 users, load average: 2.32, 2.67, 2.95
2
               total        used        free      shared  buff/cache   available
Mem:            5898        3100         402          68        2767        2798
/dev/sda1        29G   26G  3.0G  90% /

## ~/sthanav-agentic-thin-waist git state
branch: pramana-self-contained-notebook
HEAD: 5d493b2 experiments/pramana: bring the QoE notebook into this repo as a self-contained deliverable
--- uncommitted (tracked) ---
 M experiments/pramana/pramana_demo.ipynb
 M experiments/pramana/pramana_helpers.py
 M services/orchestration/scripts/selenium_video_qoe/Dockerfile
 M services/orchestration/scripts/selenium_video_qoe/collect.py
 M shared/apps.py
 M shared/qoe.py
--- untracked (top level, excluding results) ---
?? experiments/pramana/backfill_player_flag.py
?? experiments/pramana/cpu_probe.py
?? experiments/pramana/diag_attribution.py
?? experiments/pramana/host_probe.py
?? experiments/pramana/make_release.py
?? experiments/pramana/pramana_generic_check.ipynb
?? experiments/pramana/pramana_generic_check_out.ipynb
?? experiments/pramana/pramana_matrix_executed.ipynb
?? experiments/pramana/pramana_matrix_run.ipynb
?? experiments/pramana/pramana_meet_executed.ipynb
?? experiments/pramana/pramana_meet_long.ipynb
?? experiments/pramana/pramana_meet_long_executed.ipynb
?? experiments/pramana/pramana_meet_run.ipynb
?? experiments/pramana/pramana_newformat_executed.ipynb
?? experiments/pramana/pramana_newformat_verify.ipynb
?? experiments/pramana/pramana_racefix2_executed.ipynb
?? experiments/pramana/pramana_racefix_executed.ipynb
?? experiments/pramana/pramana_racefix_run.ipynb
?? experiments/pramana/pramana_tubi3_retry.ipynb
?? experiments/pramana/pramana_tubi3_retry_executed.ipynb
?? experiments/pramana/pramana_tubi_combos_executed.ipynb
?? experiments/pramana/pramana_tubi_combos_run.ipynb
?? experiments/pramana/pramana_tubi_twitch_executed.ipynb
?? experiments/pramana/pramana_tubi_twitch_run.ipynb
?? experiments/pramana/pramana_winfix_executed.ipynb
?? experiments/pramana/pramana_winfix_run.ipynb
?? experiments/pramana/pramana_zoom_executed.ipynb
?? experiments/pramana/pramana_zoom_run.ipynb
?? experiments/pramana/qoe_report.py
?? experiments/pramana/run_captest.py
--- stashes ---
--- unpushed commits per branch ---

## Branches and upstreams
main -> origin/main
pramana-self-contained-notebook -> jenil/pramana-self-contained-notebook

## Untracked file count (all)
54

## Sizes: what is big
2.7G	/home/student/sthanav-agentic-thin-waist
747M	/home/student/pramana-data
107M	/tmp/vm4_export
3.0G	/tmp/substrate-capture

## pcaps on the VM
run-dir captures: 69 files, 2.3G
pramana-data pcaps: 22 files, 747M
vm4_export: 1093 files

## Docker / compose
/home/student/sthanav-agentic-thin-waist/docker-compose.aws.yml
/home/student/sthanav-agentic-thin-waist/docker-compose.cloud.yml
/home/student/sthanav-agentic-thin-waist/docker-compose.public.yml
/home/student/sthanav-agentic-thin-waist/docker-compose.test.yml
/home/student/sthanav-agentic-thin-waist/docker-compose.yml
containers:
orchestration Up 7 weeks (healthy)
netgent-worker Up 7 weeks (unhealthy)
netgent-service Up 7 weeks (healthy)
experiment-api Up 3 months (healthy)
substrate-worker Up 7 weeks (healthy)
telemetry-service Up 3 months (healthy)
ctp-service Up 3 months (healthy)
agentic-thin-waist-postgres-1 Up 3 months (healthy)

## .env / secrets present?
  no .env at repo root

## Is the VM branch pushed?
branch: pramana-self-contained-notebook  upstream: jenil/pramana-self-contained-notebook
unpushed commits: 0
behind: 0

## Other local branches without upstream

## ~/pramana exists?
  no ~/pramana (repo is ~/sthanav-agentic-thin-waist)

## Scripts in ~ and /tmp that are ours
/home/student/analyze_qtrace.py
/home/student/pramana_helpers.py
/home/student/run_yt_solo.sh
/home/student/run_yt_zoom.sh
/home/student/shaping_sanity_check.sh
/tmp/agree.py
/tmp/analyze_release.py
/tmp/archive_pcaps.sh
/tmp/attr_fix.py
/tmp/b1_analyze.py
/tmp/b1_report.py
/tmp/b1_trials.py
/tmp/backfill.py
/tmp/backfill_meet.py
/tmp/bins.py
/tmp/bitrate_probe.py
/tmp/bot_peer_new.py
/tmp/bot_peer_test.py
/tmp/bot_peer_test2.py
/tmp/bot_peer_test3.py
/tmp/browser_probe.py
/tmp/browser_probe2.py
/tmp/build_conferencing.py
/tmp/build_meet_nb.py
/tmp/build_release.py

## shaping settings currently applied
qdisc noqueue 0: dev lo root refcnt 2 
qdisc htb 1: dev eth0 root refcnt 65 r2q 100 default 0x10 direct_packets_stat 0 direct_qlen 1000
qdisc pfifo 10: dev eth0 parent 1:10 limit 1000p
qdisc htb 1: dev veth2 root refcnt 65 r2q 100 default 0x10 direct_packets_stat 0 direct_qlen 1000
qdisc pfifo 10: dev veth2 parent 1:10 limit 1000p
qdisc htb 1: dev veth4 root refcnt 65 r2q 100 default 0x10 direct_packets_stat 0 direct_qlen 1000
