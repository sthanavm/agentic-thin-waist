| down Mb/s | RTT set | RTT measured | NDT down | IQB (download) | IQB (all legs) | source offers | resolution chosen | rebuffers | startup | demand Mb/s | infra share | QoE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 *(ref)* | 20 ms | 22.7 ms | 91.28 | meets high | fails minimum (upload) | up to 2160p | 720p | 0 | 2.49 s | 2.27 | 10% | high |
| 25 | 20 ms | 22.8 ms | 23.21 | fails minimum | fails minimum (download) | up to 2160p | 720p | 0 | 3.01 s | 2.35 | 10% | high |
| 12.5 | 20 ms | 22.7 ms | 11.71 | fails minimum | fails minimum (download) | up to 2160p | 720p | 0 | 2.24 s | 4.50 | 10% | high ⚠ |
| 6.25 | 20 ms | 23.2 ms | 5.84 | fails minimum | fails minimum (download) | up to 2160p | 720p | 0 | 4.07 s | 3.65 | 11% | high ⚠ |
| 3 | 20 ms | 23.1 ms | 2.85 | fails minimum | fails minimum (download) | up to 2160p | 720p | 0 | 7.60 s | 2.14 | 10% | acceptable ⚠ |
| 1.5 | 20 ms | 23.3 ms | 1.43 | fails minimum | fails minimum (download) | up to 2160p | 240p | 0 | 14.00 s | 1.18 | 14% | unusable ⚠ |
| 1 | 20 ms | 22.9 ms | 0.96 | fails minimum | fails minimum (download) | up to 2160p | 240p | 1 | 21.47 s | 0.86 | 10% | unusable ⚠ |
| 0.5 | 20 ms | 22.9 ms | 0.48 | fails minimum | fails minimum (download) | up to 2160p | 144p | 1 | 38.90 s | 0.40 | 18% | unusable ⚠ |
| 100 *(ref)* | 50 ms | 52.7 ms | 90.82 | meets high | fails minimum (upload) | up to 2160p | 720p | 0 | 2.46 s | 2.40 | 10% | high |
| 25 | 50 ms | 52.5 ms | 22.99 | fails minimum | fails minimum (download) | up to 2160p | 720p | 0 | 3.49 s | 2.43 | 7% | high ⚠ |
| 12.5 | 50 ms | 52.7 ms | 11.62 | fails minimum | fails minimum (download) | up to 2160p | 720p | 0 | 2.47 s | 2.35 | 10% | high ⚠ |
| 6.25 | 50 ms | — | — | no ndt | no ndt (download missing) | up to 2160p | 720p | 0 | 4.05 s | 2.19 | 10% | high ⚠ |
| 3 | 50 ms | — | — | no ndt | no ndt (download missing) | up to 2160p | 720p | 0 | 8.16 s | 2.09 | 9% | acceptable ⚠ |
| 1.5 | 50 ms | — | — | no ndt | no ndt (download missing) | up to 2160p | 240p | 0 | 14.69 s | 1.16 | 11% | unusable ⚠ |
| 1 | 50 ms | — | — | no ndt | no ndt (download missing) | up to 2160p | 240p | 0 | 19.95 s | 0.91 | 5% | unusable ⚠ |
| 0.5 | 50 ms | — | — | no ndt | no ndt (download missing) | up to 2160p | 144p | 1 | 40.83 s | 0.44 | 11% | unusable ⚠ |
| 100 *(ref)* | 100 ms | 102.7 ms | 89.83 | meets high | fails minimum (latency) | up to 2160p | 720p | 0 | 2.78 s | 2.25 | 10% | high |
| 25 | 100 ms | 102.5 ms | 22.37 | fails minimum | fails minimum (download) | up to 2160p | 720p | 0 | 2.80 s | 2.40 | 7% | high ⚠ |
| 12.5 | 100 ms | 102.8 ms | 11.43 | fails minimum | fails minimum (download) | up to 2160p | 720p | 0 | 3.80 s | 2.32 | 8% | high ⚠ |
| 6.25 | 100 ms | 103.2 ms | 5.79 | fails minimum | fails minimum (download) | up to 2160p | 720p | 0 | 4.70 s | 2.23 | 8% | high ⚠ |
| 3 | 100 ms | 103.0 ms | 2.80 | fails minimum | fails minimum (download) | up to 2160p | 480p | 0 | 7.80 s | 1.49 | 10% | acceptable ⚠ |
| 1.5 | 100 ms | 103.1 ms | 1.41 | fails minimum | fails minimum (download) | up to 2160p | 240p | 0 | 15.35 s | 1.12 | 11% | unusable ⚠ |
| 1 | 100 ms | 103.0 ms | 0.94 | fails minimum | fails minimum (download) | up to 2160p | 360p | 1 | 22.24 s | 0.87 | 10% | unusable ⚠ |
| 0.5 | 100 ms | 102.9 ms | 0.48 | fails minimum | fails minimum (download) | up to 2160p | 144p | 0 | 47.32 s | 0.39 | 19% | unusable ⚠ |

*(ref)* is the reference run, graded against itself: its resolution is 100% of its own, and the rebuffer and startup tests are absolute, so the label is earned rather than granted.

⚠ the background-traffic guard failed for that run; see the contamination note.
