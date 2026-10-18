#!/usr/bin/env python3
"""Re-run the cells that failed B1, plus a control that passed.

3 Mbps and 6 Mbps are the cells where the sampling cadence broke; 1 Mbps is the
control, because it passed before and must still pass after the change.
"""
import os
import sys

sys.path.insert(0, "/tmp")
os.environ["PRAMANA_CKPT"] = "/tmp/b1_done.json"
import run_matrix as M  # noqa: E402

if __name__ == "__main__":
    for bw, trials in ((3, (1, 2)), (6, (1, 2)), (1, (1,))):
        for t in trials:
            M.run_cell("youtube", bw, t)
    print("B1_RERUN_DONE", flush=True)
