#!/usr/bin/env python3
"""Prove the in-page log and its hard read deadline behave under a real block.

Run this before trusting any measurement the sampler makes. It blocks a page's
main thread for a known number of seconds and checks three things that could
each come out the other way:

  1. a read issued while the thread is blocked returns after ITS OWN deadline,
     not after the block ends -- this is the property the WebDriver read lacks,
     and the whole reason for a separate connection;
  2. once the thread is free again, the page's own log shows a gap of about the
     block length, so a blocked page is actually detectable;
  3. with no block, reads are prompt and the log has no gap, so the detector is
     not simply calling everything a stall.

Exits non-zero if any check fails. No experiment data is touched.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(HERE)),
        "services/orchestration/scripts/selenium_video_qoe",
    ),
)
sys.path.insert(0, "/opt/collector")  # where the image keeps it
import inpage_log as IL  # noqa: E402

BLOCK_S = float(os.environ.get("BLOCK_S", "8"))
READ_DEADLINE_S = float(os.environ.get("READ_DEADLINE_S", "2"))
PORT = int(os.environ.get("CDP_PORT", "9333"))


def chrome_binary():
    for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        p = shutil.which(c)
        if p:
            return p
    raise SystemExit("no chrome binary found")


def wait_for_devtools(port, timeout_s=30):
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:%d/json/version" % port, timeout=1
            ) as fh:
                return json.loads(fh.read().decode())
        except Exception:
            time.sleep(0.3)
    raise SystemExit("devtools did not come up on port %d" % port)


def main():
    failures = []
    udd = tempfile.mkdtemp(prefix="inpage-test-")
    proc = subprocess.Popen(
        [
            chrome_binary(),
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--remote-debugging-port=%d" % PORT,
            "--user-data-dir=%s" % udd,
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ver = wait_for_devtools(PORT)
        print("chrome up: %s" % ver.get("Browser"))
        ws = IL.page_ws_url("127.0.0.1:%d" % PORT)
        ch = IL.CdpChannel(ws).connect()

        # Install the page's own recorder and let it tick a while.
        ch.evaluate(IL.INPAGE_LOGGER_JS, 5.0)
        time.sleep(2.0)

        # --- check 3 (control): no block -> prompt read, no stall ------------
        t0 = time.monotonic()
        base = ch.read(READ_DEADLINE_S)
        dt = time.monotonic() - t0
        rep = IL.stall_report(base["events"])
        last_t = rep["last_tick_t"]
        print(
            "control : read %.3fs, %d ticks, max gap %.1f ms, stalls %d"
            % (dt, rep["ticks"], rep["max_gap_ms"], len(rep["stalls"]))
        )
        if dt > READ_DEADLINE_S:
            failures.append(
                "control read took %.2fs, over its %.2fs deadline"
                % (dt, READ_DEADLINE_S)
            )
        if rep["ticks"] < 5:
            failures.append(
                "control produced only %d ticks; the page recorder is not running"
                % rep["ticks"]
            )
        if rep["stalls"]:
            failures.append(
                "control reported %d stall(s) with nothing blocking"
                % len(rep["stalls"])
            )

        # --- check 1: a read during a real block returns on ITS deadline -----
        # Fire-and-forget, so the call that starts the block does not block us.
        ch.evaluate(
            "setTimeout(function(){var e=Date.now()+%d;while(Date.now()<e){}},0); 1"
            % int(BLOCK_S * 1000),
            5.0,
        )
        time.sleep(0.4)  # let the block actually start
        t0 = time.monotonic()
        timed_out = False
        try:
            ch.read(READ_DEADLINE_S)
        except IL.CdpTimeout:
            timed_out = True
        dt = time.monotonic() - t0
        print(
            "blocked : read returned after %.3fs (deadline %.1fs, block %.1fs) timed_out=%s"
            % (dt, READ_DEADLINE_S, BLOCK_S, timed_out)
        )
        if not timed_out:
            failures.append("read during a %.1fs block did not time out" % BLOCK_S)
        if dt > READ_DEADLINE_S + 1.0:
            failures.append(
                "read took %.2fs, i.e. it waited on the block (%.1fs) rather than its own "
                "%.1fs deadline" % (dt, BLOCK_S, READ_DEADLINE_S)
            )

        # --- check 2: the block is visible in the page's own clock -----------
        time.sleep(BLOCK_S + 1.0)  # wait the block out
        ch.connect()
        after = ch.read(5.0)
        # Carry the last tick seen before the block, or the gap that spans the
        # read boundary -- the one the block actually produced -- is invisible.
        rep2 = IL.stall_report(after["events"], prev_t=last_t)
        print(
            "after   : %d ticks, max gap %.1f ms, stalls %d  %s"
            % (
                rep2["ticks"],
                rep2["max_gap_ms"],
                len(rep2["stalls"]),
                [g["gap_ms"] for g in rep2["stalls"]][:3],
            )
        )
        lo, hi = BLOCK_S * 1000 * 0.7, BLOCK_S * 1000 * 1.4
        if not any(lo <= g["gap_ms"] <= hi for g in rep2["stalls"]):
            failures.append(
                "no gap near %.0f ms in the page clock; a blocked page would not be detected "
                "(saw %s)" % (BLOCK_S * 1000, [g["gap_ms"] for g in rep2["stalls"]][:5])
            )
        print("channel : timeouts=%d reconnects=%d" % (ch.timeouts, ch.reconnects))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(udd, ignore_errors=True)

    print()
    if failures:
        print("FAIL (%d)" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("PASS: deadline holds under a real block, and the block is detectable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
