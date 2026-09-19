#!/usr/bin/env python3
"""A bot peer for conferencing runs: joins a room and publishes a canned clip.

`needs_peer` apps (meet, zoom) produce no inbound media unless somebody else is
publishing video. With an empty room the only track is the local preview, which
measures this machine rather than the network, so `shared/qoe.py` refuses it.
This bot supplies the missing half.

It deliberately runs on normal Docker networking, NOT inside the shaped
namespace: its uplink must not be throttled by the same cap as the app under
test, or the measurement becomes "two endpoints sharing one bottleneck" instead
of "one downlink under test".

Progress is written to a status file so the launcher can wait for the join
instead of sleeping and hoping. Where admission is manual, the bot simply keeps
reporting `knocking` until it is let in or the deadline passes.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time

sys.path.insert(0, "/app")

from collect import (  # noqa: E402  - /app is only on the path at runtime
    GOOGLE_MEET_JOIN_JS,
    ensure_window_size,
    install_webrtc_hook,
    start_display,
)
from make_test_clip import write_clip  # noqa: E402

ROOM = os.environ.get("BOT_ROOM_URL", "")
NAME = os.environ.get("BOT_NAME", "pramana-bot")
CLIP = os.environ.get("BOT_CLIP", "/tmp/bot_clip.y4m")
STATUS = os.environ.get("BOT_STATUS", "/out/bot_status.json")
HOLD_S = float(os.environ.get("BOT_HOLD_SECONDS", "600"))
JOIN_DEADLINE_S = float(os.environ.get("BOT_JOIN_DEADLINE", "900"))
DISPLAY_NUM = os.environ.get("BOT_DISPLAY", "111")

_stop = False


def _sigterm(_sig, _frm):
    global _stop
    _stop = True


signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT, _sigterm)


def status(state: str, **extra) -> None:
    payload = {"state": state, "name": NAME, "ts": time.time(), **extra}
    tmp = f"{STATUS}.tmp"
    os.makedirs(os.path.dirname(STATUS) or ".", exist_ok=True)
    with open(tmp, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, STATUS)  # atomic: the reader never sees a half-written file
    print(f"[bot:{NAME}] {state} {extra if extra else ''}", flush=True)


STATE_JS = r"""
const t = (document.body.innerText || '').replace(/\s+/g, ' ');
return {
  waiting: /wait until a meeting host|asking to be let in/i.test(t),
  denied:  /denied|can't join|no one responded|removed you/i.test(t),
  snippet: t.slice(0, 120)
};
"""

OUTBOUND_JS = r"""
const done = arguments[arguments.length - 1];
(async () => {
  const pcs = Array.from(new Set(window.__netgentPeerConnections || []));
  let bytes = 0, frames = 0, w = 0, h = 0, n = 0;
  for (const pc of pcs) {
    const r = await pc.getStats();
    r.forEach(s => {
      if (s.type === 'outbound-rtp' && (s.kind || s.mediaType) === 'video') {
        n += 1;
        bytes += s.bytesSent || 0;
        frames += s.framesSent || 0;
        w = Math.max(w, s.frameWidth || 0);
        h = Math.max(h, s.frameHeight || 0);
      }
    });
  }
  done({streams: n, bytes_sent: bytes, frames_sent: frames, width: w, height: h});
})();
"""


def build_bot_driver(clip: str):
    """Chrome that presents `clip` as its webcam.

    The flags go to SeleniumBase comma-joined - space-joining silently collapses
    them into one unrecognised argument and getUserMedia then reports
    NotFoundError with no other clue.
    """
    from seleniumbase import Driver

    args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-blink-features=AutomationControlled",
        "--use-fake-ui-for-media-stream",
        "--use-fake-device-for-media-stream",
        f"--use-file-for-fake-video-capture={clip}",
        "--autoplay-policy=no-user-gesture-required",
        "--window-size=1920,1080",
        "--disable-background-networking",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    return Driver(
        browser="chrome",
        headed=True,
        uc=True,
        undetectable=True,
        use_auto_ext=False,
        chromium_arg=",".join(args),
    )


def main() -> int:
    if not ROOM:
        status("failed", reason="BOT_ROOM_URL not set")
        return 2
    status("starting", room=ROOM, clip=CLIP)

    if not os.path.exists(CLIP):
        write_clip(CLIP)
    status("clip_ready", bytes=os.path.getsize(CLIP))

    start_display(DISPLAY_NUM)
    os.environ["DISPLAY"] = f":{DISPLAY_NUM}"
    driver = build_bot_driver(CLIP)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(30)
    try:
        ensure_window_size(driver, f"bot:{NAME}")
        install_webrtc_hook(driver)
        try:
            driver.get(ROOM)
        except Exception as exc:  # noqa: BLE001 - room pages often never "finish"
            print(f"[bot:{NAME}] navigation warning: {type(exc).__name__}", flush=True)
        time.sleep(8)

        deadline = time.time() + JOIN_DEADLINE_S
        joined = False
        last_note = 0.0
        while time.time() < deadline and not _stop:
            try:
                driver.execute_script(GOOGLE_MEET_JOIN_JS, NAME)
            except Exception:  # noqa: BLE001 - the control may not exist yet
                pass
            try:
                st = driver.execute_script(STATE_JS)
                ob = driver.execute_async_script(OUTBOUND_JS)
            except Exception:  # noqa: BLE001
                st, ob = {}, {"bytes_sent": 0, "streams": 0}
            if st.get("denied"):
                status("failed", reason="admission denied", snippet=st.get("snippet"))
                return 3
            # Publishing is the only trustworthy "I am really in" signal: the
            # waiting room carries the same leave/chat chrome as the call.
            if ob.get("streams") and ob.get("bytes_sent", 0) > 0:
                joined = True
                status(
                    "joined",
                    publishing=f"{ob.get('width')}x{ob.get('height')}",
                    bytes_sent=ob.get("bytes_sent"),
                )
                break
            if time.time() - last_note > 15:
                last_note = time.time()
                status(
                    "knocking",
                    waiting=bool(st.get("waiting")),
                    hint="admit this participant in the meeting UI",
                )
            time.sleep(5)

        if not joined:
            status("failed", reason=f"not admitted within {JOIN_DEADLINE_S:.0f}s")
            return 4

        hold_until = time.time() + HOLD_S
        while time.time() < hold_until and not _stop:
            time.sleep(5)
            try:
                ob = driver.execute_async_script(OUTBOUND_JS)
                status(
                    "publishing",
                    bytes_sent=ob.get("bytes_sent"),
                    frames_sent=ob.get("frames_sent"),
                    size=f"{ob.get('width')}x{ob.get('height')}",
                )
            except Exception:  # noqa: BLE001 - keep holding even if a poll fails
                pass
        status("stopped")
        return 0
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
