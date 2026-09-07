# Capture omitted from this copy

This run is a **reference example** shipped in the repository. Its
`record.json` says `pcap_saved: true` because the run really did save a
capture on the VM that produced it — captures are 65-300 MB each and cannot
live in a git repo (GitHub rejects any file over 100 MB).

The record, plots and per-second player samples in this directory are the
originals, unmodified. `validate_runs.py` reports the missing capture as a
`PCAP_OMITTED` warning rather than a failure, so the omission is visible
rather than hidden.

To validate against full evidence, re-run the validator on the VM where the
capture still lives.
