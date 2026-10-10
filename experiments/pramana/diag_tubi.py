#!/usr/bin/env python3
"""Why does Tubi produce no <video>? Observe, do not assume.

Zero Tubi measurements exist and the earlier note said only "never produced a
<video> element in this environment". This records what the page actually is at
that moment -- status, title, visible text, every <video>, iframes, and a
screenshot -- so the blocker is identified from evidence rather than guessed
from a list of plausible causes (consent gate, login, ads, geo, bot wall).

Runs INSIDE the collector image so the browser, flags and network namespace are
the same ones a real run uses. A diagnosis from a different browser profile
would not transfer.
"""
import json
import os
import sys
import time

sys.path.insert(0, "/app")

URL = os.environ.get(
    "TUBI_URL", "https://tubitv.com/movies/593796/another-cinderella-story"
)
OUT = os.environ.get("TUBI_OUT", "/out")

DIAG_JS = r"""
const out = {};
try {
    out.url = location.href;
    out.title = document.title || '';
    out.ready = document.readyState;
    const vids = Array.from(document.querySelectorAll('video'));
    out.video_count = vids.length;
    out.videos = vids.slice(0, 6).map((v) => {
        const r = v.getBoundingClientRect();
        return {
            src: String(v.currentSrc || v.src || '').slice(0, 140),
            readyState: v.readyState, networkState: v.networkState,
            paused: v.paused, ct: v.currentTime, dur: v.duration,
            w: v.videoWidth, h: v.videoHeight,
            box: Math.round(r.width) + 'x' + Math.round(r.height),
            err: v.error ? (v.error.code + ':' + (v.error.message || '')) : null,
            srcObject: !!v.srcObject
        };
    });
    out.iframe_count = document.querySelectorAll('iframe').length;
    out.iframe_srcs = Array.from(document.querySelectorAll('iframe'))
        .slice(0, 6).map((f) => String(f.src || '').slice(0, 120));
    // MediaSource / Worker evidence: tells "no player" apart from
    // "player, but its MSE lives in a Worker" (the Twitch shape).
    out.ms_constructions = window.__netgentProbe
        ? window.__netgentProbe.ms_constructions : null;
    out.worker_count = window.__netgentProbe
        ? window.__netgentProbe.workers : null;
    // Gate detection, by the words the page shows rather than by selector
    // guesses that rot. Reported as matches, not as a conclusion.
    const body = (document.body ? (document.body.innerText || '') : '');
    out.body_len = body.length;
    out.body_head = body.slice(0, 1200);
    const pats = {
        consent: /accept all cookies|cookie|consent|privacy preferences|manage choices/i,
        signin: /sign in|log in|create account|register|continue with/i,
        age: /are you (over|18)|birth ?date|confirm your age|age verif/i,
        geo: /not available in your (region|country|location)|unavailable in your/i,
        bot: /unusual traffic|are you a (human|robot)|verify you are|captcha|access denied|blocked/i,
        notfound: /page not found|no longer available|404|we can't find/i,
        play_overlay: /play|watch now|start watching|resume/i,
        ad: /advertisement|ad will end|your video will (resume|begin)|sponsored/i,
    };
    out.text_matches = {};
    for (const k in pats) {
        const m = body.match(pats[k]);
        out.text_matches[k] = m ? m[0].slice(0, 60) : null;
    }
    // Buttons a human would press, so a click-to-play requirement is visible.
    out.buttons = Array.from(document.querySelectorAll(
        'button,[role=button],a.btn,[data-testid*=play],[class*=play]'))
        .slice(0, 14).map((b) => ({
            text: String(b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 48),
            cls: String(b.className || '').slice(0, 60)
        })).filter((x) => x.text);
} catch (e) { out.diag_error = String(e); }
return out;
"""


def main():
    import collect

    os.makedirs(OUT, exist_ok=True)
    dnum = os.environ.get("DISPLAY_NUM", "111")
    collect.start_display(dnum)
    os.environ["DISPLAY"] = f":{dnum}"
    driver = collect.build_driver(mode="uc")
    report = {"url_requested": URL, "stages": []}
    try:
        collect.install_media_hook(driver)
        try:
            collect.install_stream_probe(driver, True, "tubi")
        except Exception as exc:  # noqa: BLE001
            report["probe_error"] = str(exc)
        driver.get(URL)
        for wait_s in (5, 15, 30, 50):
            time.sleep(
                wait_s if not report["stages"] else wait_s - report["stages"][-1]["t"]
            )
            try:
                d = driver.execute_script(DIAG_JS)
            except Exception as exc:  # noqa: BLE001
                d = {"exec_error": str(exc)}
            d["t"] = wait_s
            report["stages"].append(d)
            try:
                driver.save_screenshot(f"{OUT}/tubi_t{wait_s}s.png")
            except Exception:
                pass
            print(
                f"[t={wait_s}s] videos={d.get('video_count')} "
                f"title={str(d.get('title'))[:60]!r} "
                f"matches={ {k: v for k, v in (d.get('text_matches') or {}).items() if v} }",
                flush=True,
            )
        # Page source, trimmed: enough to see whether a player container exists
        try:
            src = driver.page_source
            report["page_source_len"] = len(src)
            with open(f"{OUT}/tubi_page.html", "w") as fh:
                fh.write(src)
        except Exception as exc:  # noqa: BLE001
            report["page_source_error"] = str(exc)
        try:
            report["browser_logs"] = [
                {"level": e.get("level"), "msg": str(e.get("message"))[:200]}
                for e in (driver.get_log("browser") or [])[-25:]
            ]
        except Exception as exc:  # noqa: BLE001
            report["browser_log_error"] = str(exc)
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    with open(f"{OUT}/tubi_diag.json", "w") as fh:
        json.dump(report, fh, indent=1)
    print("WROTE " + OUT + "/tubi_diag.json")


if __name__ == "__main__":
    main()
