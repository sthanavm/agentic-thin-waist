| down Mb/s | RTT set | RTT measured | NDT down (median of n) | IQB (download) | IQB (all legs) | source offers | resolution | rebuffers | startup | demand Mb/s | infra share | QoE (n=3) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 100 *(ref)* | 20 ms | 22.6 ms | 92.63 (91.57–94.09) | meets high | fails minimum (upload) | up to 1080p | 1080p | 0 | 3.81–4.17 s | 5.11–5.56 | 2% | high |
| 25 | 20 ms | 22.7 ms | 23.30 (22.73–23.33) | fails minimum | fails minimum (download) | up to 1080p | 1080p | 0 | 3.96–4.18 s | 5.22–5.49 | 2% | high |
| 12.5 | 20 ms | 22.8 ms | 11.72 (11.64–11.73) | fails minimum | fails minimum (download) | up to 1080p | 1080p | 0 | 4.48–4.80 s | 4.79–5.05 | 2% | high |
| 6.25 | 20 ms | 23.1 ms | 5.87 (5.84–5.89) | fails minimum | fails minimum (download) | up to 1080p | 1080p | 1–2 | 6.16–6.73 s | 2.68–2.76 | 4% | acceptable |
| 3 | 20 ms | 23.1 ms | 2.85 (2.84–2.85) | fails minimum | fails minimum (download) | up to 1080p | 480p | 0–1 | 10.84–12.24 s | 1.57–1.75 | 5% | unusable ⚠2/3 |
| 1.5 | 20 ms | 23.1 ms | 1.43 (1.43–1.43) | fails minimum | fails minimum (download) | up to 1080p | 144p | 1 | 20.13–20.54 s | 1.04–1.07 | 7% | unusable ⚠3/3 |
| 1 | 20 ms | 23.0 ms | 0.96 (0.96–0.96) | fails minimum | fails minimum (download) | up to 1080p | 144p | 0 | 30.06–31.25 s | 0.76–0.80 | 10% | unusable ⚠3/3 |
| 0.5 | 20 ms | 23.0 ms | 0.48 (0.48–0.48) | fails minimum | fails minimum (download) | up to 1080p | 144p | 1–2 | 55.13–70.29 s | 0.40–0.46 | 18% | unusable ⚠3/3 |

every cell is **n=3** unless it says otherwise: the centre is the median of the repetitions and the bracket or range is their full spread, min to max. A single number means the repetitions agreed exactly.

**demand** is the mean over the sustained playback window (first app byte to last), per run, averaged over the repetitions; **infra share** is the rest of the shaped link over the same window.

⚠n/m means the background-traffic guard failed for n of the m repetitions at that point: Chrome's own non-media traffic took more than 10% of the cap in more bins than the budget allows, inside the app's measured window. Those runs are kept and flagged, not discarded.

**resolution** is the modal sample height, which is what every table in this study publishes (shared/qoe.py:514). Where the repetitions were not steady the point carries `reps_steady`, `reps_ramping_up`, `reps_downshifting` and a per-run `resolution_timeline` in the CSVs, because a modal cell alone reads as a session that sat at that rendition.
