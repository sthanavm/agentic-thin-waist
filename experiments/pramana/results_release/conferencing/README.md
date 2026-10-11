# Conferencing runs (Meet, Zoom) — manual admission

Included for coverage. These are **not** comparable row-for-row with the
video apps, and not with each other either.

| | Meet | Zoom |
|---|---|---|
| path | WebRTC (`getStats()`) | reports as `html5_video` |
| delivered bitrate | inbound-RTP, a different layer from appended bytes | **none available** |
| frame rate | decoded | decoded |
| presented-frame rate | not available (no MediaSource, no rVFC counter) | not available |
| startup | time to first decoded frame | **only the legacy artifact** — see below |

**Admission is manual**: a human admitted the bot into each meeting, so
these runs were not unattended and the join instant was not under
experiment control.

**Zoom has no trustworthy startup figure here.** The only one its path
produces is `video_startup_time_ms`, the timestamp of the first sample
where a counter advanced. At a 1 s cadence that is floored at about one
second by construction: measured across 12 ladder runs it stayed in a
1036-2086 ms band while real startup moved by 7.6x. It is reported as
`null` with that reason rather than printed as a startup number.

| run | app | cap | latency | res | res changes | rebuffers | bitrate (Mbps) | decoded fps | dropped frames |
|---|---|---|---|---|---|---|---|---|---|
| meet_3Mbps_50ms_pfifo_977bfe4d | meet | 3 | 50 ms | 360p | 0 | 2 | 0.3973 (inbound RTP) | 22.46 | 3 |
| meet_3Mbps_50ms_pfifo_fe8c6a87 | meet | 3 | 50 ms | 360p | 0 | 6 | 0.3903 (inbound RTP) | 21.38 | 2 |
| meet_3Mbps_150ms_pfifo_f555e67b | meet | 3 | 150 ms | 360p | 0 | 3 | 0.3928 (inbound RTP) | 21.53 | 20 |
| meet_6Mbps_50ms_pfifo_5fa80074 | meet | 6 | 50 ms | 360p | 1 | 4 | 0.3854 (inbound RTP) | 21.35 | 5 |
| meet_6Mbps_150ms_pfifo_cfb33f74 | meet | 6 | 150 ms | 360p | 0 | 6 | 0.3873 (inbound RTP) | 21.42 | 15 |
| meet_10Mbps_50ms_pfifo_03dbee00 | meet | 10 | 50 ms | 360p | 0 | 5 | 0.3824 (inbound RTP) | 21.07 | 8 |
| meet_10Mbps_50ms_pfifo_05afa404 | meet | 10 | 50 ms | 360p | 0 | 2 | 0.3462 (inbound RTP) | 21.45 | 27 |
| meet_10Mbps_50ms_pfifo_eefe603a | meet | 10 | 50 ms | 540p | 0 | 3 | 1.1275 (inbound RTP) | 45.49 | 274 |
| zoom_3Mbps_50ms_pfifo_80f23577 | zoom | 3 | 50 ms | 720p | 0 | 0 | **none** | 13.05 | 137 |
| zoom_3Mbps_50ms_pfifo_ba3a5f74 | zoom | 3 | 50 ms | 720p | 0 | 0 | **none** | 19.98 | 0 |
| zoom_3Mbps_50ms_pfifo_d6843875 | zoom | 3 | 50 ms | 360p | 2 | 3 | **none** | 20.62 | 539 |
| zoom_3Mbps_150ms_pfifo_00b4d726 | zoom | 3 | 150 ms | 360p | 10 | 6 | **none** | 16.58 | 661 |
| zoom_3Mbps_150ms_pfifo_541f8abf | zoom | 3 | 150 ms | 720p | 0 | 0 | **none** | 16.64 | 194 |
| zoom_3Mbps_150ms_pfifo_8eb93027 | zoom | 3 | 150 ms | 720p | 0 | 0 | **none** | 19.67 | 3483 |
| zoom_3Mbps_150ms_pfifo_a15e72bd | zoom | 3 | 150 ms | 720p | 0 | 0 | **none** | 20.0 | 4 |
| zoom_6Mbps_50ms_pfifo_5cc750cc | zoom | 6 | 50 ms | 720p | 0 | 1 | **none** | 5.1 | 93 |
| zoom_6Mbps_50ms_pfifo_7280b208 | zoom | 6 | 50 ms | 720p | 0 | 0 | **none** | 19.98 | 220 |
| zoom_6Mbps_50ms_pfifo_d574e51f | zoom | 6 | 50 ms | 360p | 1 | 1 | **none** | 21.21 | 654 |
| zoom_6Mbps_150ms_pfifo_ba38d176 | zoom | 6 | 150 ms | 720p | 0 | 0 | **none** | 19.98 | 217 |
| zoom_6Mbps_150ms_pfifo_ee5ecc16 | zoom | 6 | 150 ms | 360p | 4 | 3 | **none** | 20.39 | 612 |
| zoom_10Mbps_50ms_pfifo_a13d5114 | zoom | 10 | 50 ms | 270p | 6 | 0 | **none** | 9.86 | 38 |
| zoom_10Mbps_50ms_pfifo_afc36dd2 | zoom | 10 | 50 ms | 720p | 0 | 0 | **none** | 19.96 | 231 |
| zoom_10Mbps_50ms_pfifo_d6a00dd5 | zoom | 10 | 50 ms | 360p | 1 | 0 | **none** | 20.46 | 716 |
| zoom_10Mbps_50ms_pfifo_e65a42bb | zoom | 10 | 50 ms | 270p | 6 | 0 | **none** | 13.43 | 48 |
| zoom_10Mbps_50ms_pfifo_f0b931a0 | zoom | 10 | 50 ms | 720p | 2 | 0 | **none** | None | 1 |
| zoom_10Mbps_150ms_pfifo_a39f2fce | zoom | 10 | 150 ms | 720p | 0 | 0 | **none** | 19.98 | 21 |
| zoom_10Mbps_150ms_pfifo_b6c74806 | zoom | 10 | 150 ms | 360p | 1 | 0 | **none** | 22.54 | 903 |

Charts present per run where the series existed: `conf_bitrate.png`,
`conf_resolution.png`, `conf_buffer.png`.
