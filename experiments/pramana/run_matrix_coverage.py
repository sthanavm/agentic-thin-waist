#!/usr/bin/env python3
"""Batch D: a minimal ladder for Tubi and Twitch, for app coverage.

Both had only one cell in the main matrix. Two more rungs each gives a low and
a high point so the release can say something about how each behaves under
constraint rather than reporting a single sample.
"""
import os
import sys

sys.path.insert(0, "/tmp")
os.environ.setdefault("DUR", "180")
import run_matrix as M  # noqa: E402

if __name__ == "__main__":
    for app in ("tubi", "twitch"):
        for bw in (3, 10):
            M.run(app, bw, 10)
    print("MATRIX_D_DONE", flush=True)
