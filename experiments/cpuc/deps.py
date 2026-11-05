"""Where the harness finds `pramana_helpers`, and why it is not just an import.

The background-traffic guard calls `pramana_helpers.link_interference`, the
per-bin interference judgement. That function is on branch
`jenil/pramana/collector-driver-prefetch` (e0a6b01) and NOT on main.

Merging that branch into this checkout was tried and abandoned: it conflicts
with `jenil/pramana/notebook-code-only` in 10 hunks / 591 lines of
`pramana_helpers.py` alone, plus `collect.py` and `shared/apps.py`. Those are
two divergent lines of measurement code, and hand-resolving them to obtain one
function would risk changing how QoE or attribution is computed -- which is the
one thing this study cannot afford to do quietly.

So the dependency is resolved by PATH instead: point at a tree that already
has the function. `driver-work` is a linked worktree of this repo checked out
at e0a6b01, so it is the same code the branch would have brought in, with no
merge and nothing resolved by hand.

Override with CPUC_PRAMANA_PATH when that branch lands on main, at which point
this module can go away.
"""

from __future__ import annotations

import os
import sys

DEFAULT_PRAMANA_PATHS = (
    os.environ.get("CPUC_PRAMANA_PATH", ""),
    "/Users/jenilprajapati/SNL-Lab-Codebases/driver-work/experiments/pramana",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pramana"
    ),
)

REQUIRED_ATTRS = ("link_interference", "attribute_capture")


def pramana_path() -> str:
    """First candidate directory that actually provides what the guard needs."""
    for cand in DEFAULT_PRAMANA_PATHS:
        if not cand:
            continue
        helpers = os.path.join(cand, "pramana_helpers.py")
        if not os.path.isfile(helpers):
            continue
        try:
            with open(helpers) as fh:
                src = fh.read()
        except OSError:
            continue
        if all(f"def {a}" in src for a in REQUIRED_ATTRS):
            return cand
    raise RuntimeError(
        "no pramana_helpers.py providing "
        + ", ".join(REQUIRED_ATTRS)
        + " was found. Set CPUC_PRAMANA_PATH to a checkout of "
        "jenil/pramana/collector-driver-prefetch (e0a6b01) or later. Tried: "
        + ", ".join(c for c in DEFAULT_PRAMANA_PATHS if c)
    )


def install() -> str:
    """Put that directory on sys.path. Idempotent; returns the path used."""
    path = pramana_path()
    if path not in sys.path:
        sys.path.insert(0, path)
    return path
