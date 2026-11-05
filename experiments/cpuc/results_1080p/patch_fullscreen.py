"""Patch the CPUC job's OWN copy of collect.py to support a fullscreen player.

Not the repo and not the shared worker: /home/student/cpuc/... is the copy this
job mounts over the collector image. A .orig-prefs backup is kept beside it.
"""

import ast
import pathlib
import sys

TARGET = (
    "/home/student/cpuc/services/orchestration/scripts/selenium_video_qoe/collect.py"
)

HELPER = '''FULLSCREEN_ARM_JS = """
document.addEventListener('click', function h() {
    document.removeEventListener('click', h);
    const p = document.querySelector('#movie_player') || document.documentElement;
    const fn = p.requestFullscreen || p.webkitRequestFullscreen;
    if (fn) { try { fn.call(p); } catch (e) {} }
}, {once: true});
return true;
"""

RESUME_JS = """
const v = document.querySelector('video');
if (v && v.paused) { v.muted = true; v.play().catch(e => {}); }
return v ? !v.paused : false;
"""


def go_fullscreen(driver, app, attempts=4, settle_s=1.5):
    """Put the PLAYER into fullscreen, so the video element is the whole screen.

    The window is already 1920x1080, but YouTube's watch layout spends that on
    a sidebar plus browser chrome and leaves the player about 1317x988. An
    adaptive player sizes its rendition to the element it draws into, so that
    box - not the shaped link - is what holds the ladder at 720p. Fullscreen
    makes the element the screen: exactly 1920x1080.

    requestFullscreen() needs a user activation and execute_script does not
    carry one, so this goes through the input domain, which produces trusted
    events: first YouTube's own 'f' hotkey, then a real click into a one-shot
    handler that calls requestFullscreen itself. The element box is read back
    after every attempt, because a refused fullscreen request fails silently
    and would otherwise be indistinguishable from success.

    Returns the measured geometry; the caller decides whether to accept it.
    """
    from selenium.webdriver.common.action_chains import ActionChains

    geom = {}
    for i in range(1, attempts + 1):
        try:
            v = driver.find_element("css selector", "video")
            if i <= 2:
                ActionChains(driver).move_to_element(v).send_keys("f").perform()
            else:
                driver.execute_script(FULLSCREEN_ARM_JS)
                ActionChains(driver).move_to_element(v).click().perform()
                # A click on the YouTube player toggles pause, so undo it.
                driver.execute_script(RESUME_JS)
        except Exception as exc:  # noqa: BLE001 - never fail a run over this
            print(f"[{app}] fullscreen attempt {i} raised: {exc}", flush=True)
        time.sleep(settle_s)
        try:
            geom = driver.execute_script(GEOMETRY_JS) or {}
        except Exception:  # noqa: BLE001 - page may not be ready to run JS
            geom = {}
        pw, ph = geom.get("player_w") or 0, geom.get("player_h") or 0
        if ph >= 1000:
            geom.update(fullscreen=True, fs_attempts=i)
            print(f"[{app}] fullscreen: player element {pw}x{ph}", flush=True)
            return geom
        print(
            f"[{app}] player element {pw}x{ph}, want >=1920x1080 - "
            f"retrying ({i}/{attempts})",
            flush=True,
        )
    geom.update(fullscreen=False, fs_attempts=attempts)
    print(
        f"[{app}] WARNING: player element stuck at "
        f"{geom.get('player_w')}x{geom.get('player_h')} - "
        f"the rendition will be viewport-capped",
        flush=True,
    )
    return geom


'''

ANCHOR_A = 'MEDIA_STATE_JS = """\n'

OLD_B = """        _ws = job.get("window_size")
        if _ws:
            try:"""

NEW_B = """        _ws = job.get("window_size")
        want_fullscreen = str(_ws or "").strip().lower() in ("fullscreen", "fs")
        if want_fullscreen:
            # Keep the window at the default 1920x1080; the player is taken
            # fullscreen after playback starts, once the element exists.
            _ws = None
            print(f"[{app}] viewport: fullscreen requested")
        if _ws:
            try:"""

OLD_C = """                print(f"[{app}] synchronized play result: {play_result}")"""

NEW_C = """                print(f"[{app}] synchronized play result: {play_result}")
                if want_fullscreen:
                    # After play, not before: the element has to exist and be
                    # rendering before the player will accept fullscreen, and
                    # the ladder is re-evaluated once the box changes.
                    window_geometry = go_fullscreen(driver, app)"""


def main() -> int:
    p = pathlib.Path(TARGET)
    t = p.read_text()
    if "def go_fullscreen(" in t:
        print("already patched; nothing to do")
        return 0
    for name, needle in (("A", ANCHOR_A), ("B", OLD_B), ("C", OLD_C)):
        if t.count(needle) != 1:
            print(f"anchor {name} matched {t.count(needle)} times, expected 1")
            return 1
    t = t.replace(ANCHOR_A, HELPER + ANCHOR_A, 1)
    t = t.replace(OLD_B, NEW_B, 1)
    t = t.replace(OLD_C, NEW_C, 1)
    ast.parse(t)  # refuse to write a file that will not import
    p.write_text(t)
    print("patched: fullscreen helper + window_size=fullscreen + post-play trigger")
    return 0


if __name__ == "__main__":
    sys.exit(main())
