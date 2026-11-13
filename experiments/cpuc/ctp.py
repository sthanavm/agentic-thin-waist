"""Picking CTP profiles from the corpus manifest.

Kept separate from conditions.py on purpose: that module is declarative and
I/O-free by design (its own docstring says so), and choosing profiles means
reading manifest.csv off disk. This module does the reading; conditions.py's
`ctp_plan()` just turns a plain list of names into RunPlans.
"""

from __future__ import annotations

import csv
import os
import random
from pathlib import Path

DEFAULT_CTP_DIR = os.environ.get("CPUC_CTP_DIR", "/home/student/ctp_data")


def load_profiles(ctp_dir: str = DEFAULT_CTP_DIR) -> list[str]:
    """Every profile name in manifest.csv, verified to exist as a download pcap.

    Reads the name from the manifest rather than listing download/ directly,
    so a profile missing from the manifest (and whatever provenance it
    records) is never silently picked.
    """
    manifest = Path(ctp_dir) / "manifest.csv"
    if not manifest.is_file():
        raise RuntimeError(f"no manifest.csv at {manifest}")
    download_dir = Path(ctp_dir) / "download"
    out: list[str] = []
    with open(manifest, newline="") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("profile") or "").strip()
            if not name:
                continue
            if not (download_dir / f"{name}.pcap").is_file():
                raise RuntimeError(
                    f"manifest lists profile {name!r} but "
                    f"{download_dir / f'{name}.pcap'} does not exist"
                )
            out.append(name)
    if not out:
        raise RuntimeError(f"manifest at {manifest} named no usable profiles")
    return out


def random_profiles(
    n: int, ctp_dir: str = DEFAULT_CTP_DIR, seed: int | None = None
) -> list[str]:
    """`n` distinct profile names, randomly chosen (optionally reproducibly).

    Raises rather than silently truncating if the corpus has fewer than `n`
    profiles -- running 7 CTPs when 10 were asked for should be a decision,
    not something the CSV reveals after the fact.
    """
    profiles = load_profiles(ctp_dir)
    if n > len(profiles):
        raise RuntimeError(
            f"asked for {n} CTP profiles but the corpus at {ctp_dir!r} only "
            f"has {len(profiles)}"
        )
    rng = random.Random(seed)
    return rng.sample(profiles, n)
