"""Where the harness finds `pramana_helpers`, and why it still checks.

`link_interference` and `attribute_capture` landed on main in #23, so the
in-repo `experiments/pramana` is now the tree that provides them and the
dependency is an ordinary sibling import. The absolute path this module used
to carry -- a worktree under /Users/jenilprajapati -- is gone with it; it
resolved only on one laptop and silently fell through to a stale tree
anywhere else.

What stays is the assertion. A run that imports a tree WITHOUT those
functions must fail loudly rather than proceed with a different definition of
interference than the one the study's numbers were computed under, so the
check is on the file's contents, not on the import succeeding. CPUC_PRAMANA_PATH
still overrides, for pointing a run at a specific checkout deliberately.
"""

from __future__ import annotations

import os
import sys

REQUIRED_ATTRS = ("link_interference", "attribute_capture")


def pramana_path() -> str:
    """The in-repo pramana tree, or an explicit override. Verified, not assumed."""
    cand = os.environ.get("CPUC_PRAMANA_PATH") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pramana"
    )
    helpers = os.path.join(cand, "pramana_helpers.py")
    if not os.path.isfile(helpers):
        raise RuntimeError(
            f"no pramana_helpers.py at {helpers!r}. The in-repo tree is the "
            f"default since #23 landed on main; set CPUC_PRAMANA_PATH to point "
            f"at another checkout."
        )
    with open(helpers) as fh:
        src = fh.read()
    missing = [a for a in REQUIRED_ATTRS if f"def {a}" not in src]
    if missing:
        raise RuntimeError(
            f"{helpers!r} does not provide {', '.join(missing)}. This tree "
            f"predates #23, so its interference judgement is not the one these "
            f"results were computed under -- refusing to run against it."
        )
    return cand


def install() -> str:
    """Put that directory on sys.path. Idempotent; returns the path used."""
    path = pramana_path()
    if path not in sys.path:
        sys.path.insert(0, path)
    return path
