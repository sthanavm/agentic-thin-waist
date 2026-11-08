| capacity (sym) | RTT set | RTT measured | NDT down | NDT up | IQB verdict | resolution | fps | freezes | Meet demand | infra share | QoE |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 Mb/s *(ref)* | 20 ms | 22.6 ms | 92.60 | 93.12 | meets minimum only (download) | 720p | 24.0 | 0 (0.00%) | 3.74 | 12% | high |
| 100 Mb/s *(ref)* | 50 ms | 52.7 ms | 92.94 | 92.78 | fails minimum (latency) | 720p | 23.9 | 0 (0.00%) | 2.18 | 23% | high |
| 100 Mb/s *(ref)* | 100 ms | 102.7 ms | 89.67 | 89.03 | fails minimum (latency) | 720p | 23.8 | 1 (0.34%) | 1.92 | 20% | high |
| 25 Mb/s | 20 ms | 22.6 ms | 23.28 | 23.54 | fails minimum (upload) | 720p | 23.9 | 0 (0.00%) | 2.19 | 23% | high ⚠ |
| 25 Mb/s | 50 ms | 52.6 ms | 22.92 | 23.31 | fails minimum (latency) | 720p | 24.0 | 1 (0.34%) | 2.16 | 23% | high ⚠ |
| 25 Mb/s | 100 ms | 102.6 ms | 22.39 | 22.44 | fails minimum (latency) | 720p | 24.0 | 1 (0.34%) | 2.07 | 19% | high |
| 6 Mb/s | 20 ms | 23.2 ms | 5.66 | 5.72 | fails minimum (download) | 720p | 23.0 | 0 (0.00%) | 1.80 | 24% | high ⚠ |
| 6 Mb/s | 50 ms | 52.9 ms | 5.60 | 5.67 | fails minimum (download) | 720p | 24.0 | 1 (0.38%) | 1.85 | 24% | high ⚠ |
| 6 Mb/s | 100 ms | 102.7 ms | 5.55 | 5.52 | fails minimum (download) | 720p | 23.9 | 0 (0.00%) | 1.84 | 20% | high ⚠ |
| 3 Mb/s | 20 ms | 22.9 ms | 2.85 | 2.86 | fails minimum (download) | 720p | 22.7 | 2 (0.70%) | 1.60 | 21% | high ⚠ |
| 3 Mb/s | 50 ms | 52.9 ms | 2.82 | 2.84 | fails minimum (download) | 720p | 22.1 | 1 (4.77%) | 1.54 | 25% | acceptable ⚠ |
| 3 Mb/s | 100 ms | 103.1 ms | 2.80 | 2.79 | fails minimum (download) | 720p | 19.6 | 0 (0.00%) | 1.50 | 26% | acceptable ⚠ |
| 1 Mb/s | 20 ms | 23.0 ms | 0.96 | 0.96 | fails minimum (download) | 360p | 19.9 | 8 (17.95%) | 0.51 | 38% | unusable ⚠ |
| 1 Mb/s | 50 ms | 53.0 ms | 0.95 | 0.95 | fails minimum (download) | — | — | — | — | 54% | video arrived, unmeasured |
| 1 Mb/s | 100 ms | 103.2 ms | 0.94 | 0.95 | fails minimum (download) | — | — | — | — | — | invalid run (no packets) |
| 0.5 Mb/s | 20 ms | 22.9 ms | 0.48 | 0.48 | fails minimum (download) | — | — | — | — | 43% | video arrived, unmeasured |
| 0.5 Mb/s | 50 ms | 53.3 ms | 0.48 | 0.48 | fails minimum (download) | — | — | — | — | 56% | video arrived, unmeasured |
| 0.5 Mb/s | 100 ms | 103.2 ms | 0.48 | 0.48 | fails minimum (download) | — | — | — | — | — | invalid run (no packets) |

*(ref)* is the reference run, graded against itself: its resolution and fps are 100% of their own, but the freeze test is absolute, so the label is earned rather than granted.

**video arrived, unmeasured** means remote video was arriving and the collector failed to sample it, so resolution, fps and freezes are unknown at those points -- not zero. Read off the captures: 2.2-3.2 MB of inbound media from Meet's own endpoints (1,671-2,442 full-size packets), in both attempts at every one of those capacities, against controls of 17,588 such packets on the known-720p reference run and 4,943 on the 360p run at 1 Mb/s. **Correction, 2026-11-08:** these cells previously read *no remote media* and were quoted as *no remote video arrived at all*. That was wrong. It was inferred from the collector's exit status, which cannot tell a dead collector from a silent peer.

**invalid run (no packets)** is not a result. Those captures contain no inbound packets of any size, so nothing about the link was measured. They were previously marked *confounded* -- the persistent peer had stopped publishing -- which still reads as a result with a caveat. Zero packets is not a measurement.

**Meet demand** is the mean over the sustained call window (first app byte to last), not over the whole capture, and it is withheld wherever quality is unmeasured because the demand gate has no rendition to check it against. **infra share** is the rest of the shaped link over the same window.
