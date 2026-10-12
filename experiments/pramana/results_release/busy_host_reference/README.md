# Busy-host reference runs — kept for comparison, not part of the result set

These are the earlier runs of the same twelve cells, collected while the host
was busy. They are kept so the effect of host load is visible rather than
asserted, and so the two 3 Mbps trial-1 runs can be compared directly.

**Only `summary.json` is kept here**, one file per run. The full data — samples,
charts and captures — is in the checksum-verified backup off the repo at
`~/SNL-Lab-Codebases/pramana-data/2026-10-12-pre-clean-rerun/`, verified
121 of 121 files by SHA-256 before anything was moved.

## How they differ from the result set

Each `summary.json` carries its own `run_metadata.host` block, so the load each
run actually saw is recorded rather than inferred. In short:

- these runs were collected before the 10 s script timeout and the final CDP
  drain were added, so a stalled page script could cost 30-40 s;
- the result set in `../youtube/` and `../vimeo/` was collected afterwards,
  with identical collector code across all twelve cells.

They are **not** mixed into the app x metric table. Nothing here was discarded
to make a check pass; the failures these runs show are reported in the main
README's history and in `../validation/THRESHOLDS.md`.

## A correction worth repeating

These were once described as being on a "busy host" whose load came from 159
Chrome processes. An audit showed those processes are all **zombies** — already
exited, holding no CPU and not contributing to load. The contention was real
but came from the collector's own Chrome and live work inside
`substrate-worker`. See the vm-4 process audit in `../validation/THRESHOLDS.md`.
