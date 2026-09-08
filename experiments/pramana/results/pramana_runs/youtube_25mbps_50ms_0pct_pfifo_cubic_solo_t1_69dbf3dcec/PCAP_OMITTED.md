# Capture omitted from this copy

This run is a **reference example** shipped in the repository. Its `record.json`
says `pcap_saved: true` because the run really did save a capture on the VM that
produced it — captures are 15-80 MB each and do not belong in a git repo.

The record, plots and collector log here are the unmodified originals.
`validate_runs.py` reports the missing capture as a `PCAP_OMITTED` warning
rather than a failure, so the omission is visible rather than hidden.
