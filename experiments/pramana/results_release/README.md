# Uniform streaming-QoE results — release package

One extraction method and one output schema applied to every app, with no
per-site APIs. Every number here came from a run on the lab VM; every chart
was regenerated from the raw per-second samples by `build_release.py`, and
each chart's values are asserted equal to `summary.json` as part of the
build. A chart that disagreed would fail the build rather than ship.

**Read `validation/VERIFICATION_REPORT.md` before trusting any row.**

## Status at a glance

| | count |
|---|---|
| runs in this release | 23 |
| checks run | 227 |
| checks FAILED | **12** |
| checks UNKNOWN | 0 |

## App x metric

`VERIFIED` = produced and checked on every run of that app. `VERIFIED*` =
produced on some runs only. `UNKNOWN` = the channel exists but returned
nothing. `NOT MEASURED` = no run.

| metric | tubi | twitch | vimeo | youtube |
|---|---|---|---|---|
| 1 Startup delay | UNKNOWN<br><sub>3 run(s), metric null in all</sub> | VERIFIED<br><sub>3/3 run(s)</sub> | VERIFIED<br><sub>6/6 run(s)</sub> | VERIFIED<br><sub>11/11 run(s)</sub> |
| 2 Buffer level + rebuffering | VERIFIED<br><sub>3/3 run(s)</sub> | VERIFIED<br><sub>3/3 run(s)</sub> | VERIFIED<br><sub>6/6 run(s)</sub> | VERIFIED<br><sub>11/11 run(s)</sub> |
| 3 Delivered bitrate (video/audio split) | VERIFIED<br><sub>3/3 run(s)</sub> | UNKNOWN<br><sub>3 run(s), metric null in all</sub> | VERIFIED<br><sub>6/6 run(s)</sub> | VERIFIED<br><sub>11/11 run(s)</sub> |
| 4 Rendition switching (timestamped, with direction) | VERIFIED<br><sub>3/3 run(s)</sub> | VERIFIED<br><sub>3/3 run(s)</sub> | VERIFIED<br><sub>6/6 run(s)</sub> | VERIFIED<br><sub>11/11 run(s)</sub> |
| 5 Frame rendering rate (presented) | UNKNOWN<br><sub>3 run(s), metric null in all</sub> | VERIFIED<br><sub>3/3 run(s)</sub> | VERIFIED<br><sub>6/6 run(s)</sub> | VERIFIED<br><sub>11/11 run(s)</sub> |

Dropped frames are carried as raw counts only (`raw_dropped_frames`), and
throughput is NOT treated as a QoE metric — both per the brief.

## How to read a run folder

```
<app>/<app>_<bw>Mbps_<lat>ms_<aqm>_trial<N>/
  samples.json              raw per-second samples, schema, interval, codec
  summary.json              the five metrics, verdict, byte references
  run_meta.json             commit, Chrome version, VM, shaping, window
  download_throughput.png   separate from upload, fixed 0-11 Mbps axis
  upload_throughput.png
  qoe_buffer.png            buffer level, rebuffer spans shaded
  qoe_resolution.png        resolution over time
  qoe_bitrate.png           delivered video and audio bitrate
  qoe_switches.png          rendition switches, green=up red=down
  qoe_fps.png               presented frames per second
  qoe_startup.png           startup delay vs initial buffer fill
```

Throughput charts are line graphs (not filled), share a fixed 0-11 Mbps
y-axis so runs are comparable, and are clipped to the playback window.

## Known limitations

- **Cross-traffic profiles are not implemented.** Still waiting on Jaber
  for the pointer. No profile is simulated and none is claimed.
- **2 vCPU VM.** Vimeo at 10 Mbps is decode-limited on this host, measured:
  at 1440x1080 the frame-drop rate is 13.4-13.7 % against 2.2-2.7 % at
  960x720 while the buffer stayed full. That is a host result, not a
  network one, and is labelled as such.
- **Twitch delivered bitrate is unavailable by construction**: it builds no
  main-world MediaSource, running MSE inside Workers and feeding the element
  a MediaSourceHandle, so appended-byte hooks cannot reach it. Reported as
  null with that reason rather than as 0.
- **VP9-in-WebM reports codec family only.** Verified on a real file: WebM
  carries no CodecPrivate for VP9, so the profile lives in the bitstream and
  no init-segment parser can recover it.
- **Tubi titles rotate.** A retired title still returns HTTP 200 with the
  right page title but shows CONTENT UNAVAILABLE and creates no video
  element. The URL in `shared/apps.py` carries instructions for re-pointing.
- **pcaps are not in git.** See `pcap_manifest.csv` for sizes, SHA-256 and
  where each one lives.

## Reproducing

```bash
# on the lab VM, with the stack up (substrate worker + telemetry healthy)
cd experiments/pramana
PRAMANA_COLLECTOR_SRC=$PWD/../../services/orchestration/scripts/\
selenium_video_qoe/collect.py PRAMANA_DEFER_ANALYSIS=1 \
  python run_matrix.py all          # the sweep in sweep_matrix.md

# then rebuild this package (charts + summaries + manifest) from raw runs
python build_release.py <out_dir> <run_dir> [<run_dir> ...]
python write_docs.py <out_dir>
```

Versions, VM name and the exact shaping parameters for each run are in that
run's `run_meta.json`.
