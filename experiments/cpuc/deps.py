"""Where the harness finds `pramana_helpers`, and why it is not just an import.

The background-traffic guard wants `pramana_helpers.link_interference`, the
per-bin interference judgement. That function is on branch
`jenil/pramana/collector-driver-prefetch` (e0a6b01) and NOT on main, and a
repo-wide search (every branch, all 673 commits, this fork and upstream) found
it has never been pushed anywhere reachable -- only referenced, never defined.
It lives only on Jenil's own machine per the path below.

DECISION (Sthanav, pending Jenil sharing that file): `link_interference` is
dropped from REQUIRED_ATTRS so collection is not blocked on it. Confirmed by
reading guard.py and pramana_helpers.py that nothing in the actually-used run
path (`teams.py`'s drive_teams/apply_condition) calls `link_interference`
directly -- only `attribute_capture`, which IS present. The cost: `teams.py`
does not call the background-traffic guard at all (see its own note at the
call site), so a run genuinely contaminated by Chrome's own background
traffic would not be caught. Re-add `link_interference` to REQUIRED_ATTRS and
wire the guard back into teams.py's run_app_point once that file is in hand.

Override with CPUC_PRAMANA_PATH if/when that branch lands somewhere reachable.
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

REQUIRED_ATTRS = ("attribute_capture",)


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
