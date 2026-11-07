| capacity (sym) | RTT set | RTT measured | NDT down | NDT up | IQB verdict | resolution | fps | freezes | Meet demand | QoE |
|---|---|---|---|---|---|---|---|---|---|---|
| 100 Mb/s *(ref)* | 20 ms | 22.6 ms | 92.60 | 93.12 | meets minimum only (download) | 720p | 24.0 | 0 (0.00%) | 3.69 | high |
| 100 Mb/s *(ref)* | 50 ms | 52.7 ms | 92.94 | 92.78 | fails minimum (latency) | 720p | 23.9 | 0 (0.00%) | 1.45 | high |
| 100 Mb/s *(ref)* | 100 ms | 102.7 ms | 89.67 | 89.03 | fails minimum (latency) | 720p | 23.8 | 1 (0.34%) | 1.90 | high |
| 25 Mb/s | 20 ms | 22.6 ms | 23.28 | 23.54 | fails minimum (upload) | 720p | 23.9 | 0 (0.00%) | 2.15 | high ⚠ |
| 25 Mb/s | 50 ms | 52.6 ms | 22.92 | 23.31 | fails minimum (latency) | 720p | 24.0 | 1 (0.34%) | 2.13 | high ⚠ |
| 25 Mb/s | 100 ms | 102.6 ms | 22.39 | 22.44 | fails minimum (latency) | 720p | 24.0 | 1 (0.34%) | 2.04 | high |
| 6 Mb/s | 20 ms | 23.2 ms | 5.66 | 5.72 | fails minimum (download) | 720p | 23.0 | 0 (0.00%) | 1.78 | high ⚠ |
| 6 Mb/s | 50 ms | 52.9 ms | 5.60 | 5.67 | fails minimum (download) | 720p | 24.0 | 1 (0.38%) | 1.83 | high ⚠ |
| 6 Mb/s | 100 ms | 102.7 ms | 5.55 | 5.52 | fails minimum (download) | 720p | 23.9 | 0 (0.00%) | 1.82 | high ⚠ |
| 3 Mb/s | 20 ms | 22.9 ms | 2.85 | 2.86 | fails minimum (download) | 720p | 22.7 | 2 (0.70%) | 1.56 | high ⚠ |
| 3 Mb/s | 50 ms | 52.9 ms | 2.82 | 2.84 | fails minimum (download) | 720p | 22.1 | 1 (4.77%) | 1.51 | acceptable ⚠ |
| 3 Mb/s | 100 ms | 103.1 ms | 2.80 | 2.79 | fails minimum (download) | 720p | 19.6 | 0 (0.00%) | 1.46 | acceptable ⚠ |
| 1 Mb/s | 20 ms | 23.0 ms | 0.96 | 0.96 | fails minimum (download) | 360p | 19.9 | 8 (17.95%) | 0.50 | unusable ⚠ |
| 1 Mb/s | 50 ms | 53.0 ms | 0.95 | 0.95 | fails minimum (download) | — | — | — | — | no remote media |
| 1 Mb/s | 100 ms | 103.2 ms | 0.94 | 0.95 | fails minimum (download) | — | — | — | — | no remote media (confounded) |
| 0.5 Mb/s | 20 ms | 22.9 ms | 0.48 | 0.48 | fails minimum (download) | — | — | — | — | no remote media |
| 0.5 Mb/s | 50 ms | 53.3 ms | 0.48 | 0.48 | fails minimum (download) | — | — | — | — | no remote media |
| 0.5 Mb/s | 100 ms | 103.2 ms | 0.48 | 0.48 | fails minimum (download) | — | — | — | — | no remote media (confounded) |

*(ref)* is the reference run, graded against itself: its resolution and fps are 100% of their own, but the freeze test is absolute, so the label is earned rather than granted.

**no remote media (confounded)** is NOT evidence about the link: the persistent peer stopped publishing during that batch, so an absence of remote video cannot be told apart from the meeting having ended. The same two capacities at 50 ms failed while the peer was verified still publishing, and those rows are plain **no remote media**.
