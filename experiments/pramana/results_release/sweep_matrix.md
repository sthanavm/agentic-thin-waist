# Sweep matrix

Every cell actually run for this release, with trial count and status.
Status is derived from `validation/checks.csv`, not written by hand.
`VERIFIED*` means every check passed but at least one was UNKNOWN
(recorded as such rather than silently treated as a pass).

All cells: solo app, 0 % loss, cubic, 180 s, one app per run.

| app | cap (Mbps) | latency (ms) | AQM | trials | statuses | resolution(s) | startup (ms) |
|---|---|---|---|---|---|---|---|
| tubi | 3 | 50 | pfifo | 1 | VERIFIED | 480p | n/a |
| tubi | 6 | 50 | pfifo | 1 | VERIFIED | 480p | n/a |
| tubi | 10 | 50 | pfifo | 1 | VERIFIED | 480p | n/a |
| twitch | 3 | 50 | pfifo | 1 | FAILED | 720p | 16434 |
| twitch | 6 | 50 | pfifo | 1 | FAILED | 720p | 13287 |
| twitch | 10 | 50 | pfifo | 1 | FAILED | 720p | 12683 |
| vimeo | 0.5 | 50 | pfifo | 1 | VERIFIED | 240p | 21504 |
| vimeo | 1 | 50 | pfifo | 1 | VERIFIED | 240p | 10508 |
| vimeo | 1.5 | 50 | pfifo | 1 | VERIFIED | 240p | 8095 |
| vimeo | 3 | 50 | pfifo | 1 | VERIFIED | 540p | 4084 |
| vimeo | 6 | 50 | pfifo | 1 | VERIFIED | 720p | 2854 |
| vimeo | 10 | 50 | pfifo | 1 | VERIFIED | 720p | 2049 |
| youtube | 0.5 | 50 | pfifo | 1 | VERIFIED | 144p | 40507 |
| youtube | 1 | 50 | pfifo | 1 | VERIFIED | 360p | 20521 |
| youtube | 1.5 | 50 | pfifo | 1 | FAILED | 480p | 14261 |
| youtube | 3 | 50 | pfifo | 6 | FAILED | 480p, 720p | 7528-8165 |
| youtube | 6 | 50 | pfifo | 1 | FAILED | 720p | 6335 |
| youtube | 10 | 50 | pfifo | 1 | FAILED | 720p | 8127 |

## Not run, and why

| combination | why |
|---|---|
| Multi-app / concurrent cells (Step 3b) | gated on the YouTube byte reference passing for a proven reason; see README |
| Cross-traffic profiles | not implemented — still waiting on Jaber for the pointer. Nothing is faked and no profile is claimed |
| AQM other than pfifo | not swept in this release; the AQM axis was held fixed so bandwidth and app are the only variables |
| Latency other than 50 ms | held fixed for the same reason |
