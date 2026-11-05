"""Capture the launched browser's REAL command line, for the suppression gate.

`collect.py` does not record it anywhere, and the guard must not be satisfied
by the flag list we intended to pass -- Chrome silently ignores a misspelled
flag, so intent and effect can differ and only the effect matters.

The collector container is started with `--pid host`
(pramana_helpers.py:1229-1230), so every Chrome process it launches is visible
in the host's /proc. This watches for one during the app window and keeps its
`/proc/<pid>/cmdline` verbatim.

Runs on the VM, beside the run itself; /proc is read-only and nothing here
signals or modifies any process.
"""

from __future__ import annotations

import glob
import os
import threading
import time
from typing import Optional

# A Chrome we care about is one the collector launched: it is the browser
# binary AND it carries the automation flag the collector always passes.
BROWSER_HINTS = ("google-chrome", "chrome", "chromium")
MUST_CONTAIN = "--remote-debugging"


def _read_cmdline(pid: str) -> Optional[str]:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except (OSError, PermissionError):
        return None
    if not raw:
        return None
    return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()


def find_browser_cmdline() -> Optional[str]:
    """The longest matching Chrome command line currently running.

    Longest wins because Chrome spawns helper processes (renderer, gpu, zygote)
    whose command lines are short and carry none of the suppression flags; the
    browser process is the one with the full flag set.
    """
    best: Optional[str] = None
    for path in glob.glob("/proc/[0-9]*"):
        pid = os.path.basename(path)
        cmd = _read_cmdline(pid)
        if not cmd:
            continue
        low = cmd.lower()
        if not any(h in low for h in BROWSER_HINTS):
            continue
        if MUST_CONTAIN not in cmd:
            continue
        if best is None or len(cmd) > len(best):
            best = cmd
    return best


class CmdlineWatcher:
    """Polls for the browser command line in the background during a run.

    Captures the first full match and keeps the longest seen, then stops. If it
    never sees one, `cmdline` stays None and the suppression assertion fails --
    which is the correct outcome, not a reason to assume the flags were there.
    """

    def __init__(self, poll_interval_s: float = 0.5, timeout_s: float = 180.0):
        self.poll_interval_s = poll_interval_s
        self.timeout_s = timeout_s
        self.cmdline: Optional[str] = None
        self.samples = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _loop(self) -> None:
        deadline = time.monotonic() + self.timeout_s
        while not self._stop.is_set() and time.monotonic() < deadline:
            found = find_browser_cmdline()
            if found:
                self.samples += 1
                if self.cmdline is None or len(found) > len(self.cmdline):
                    self.cmdline = found
            self._stop.wait(self.poll_interval_s)

    def __enter__(self) -> "CmdlineWatcher":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
