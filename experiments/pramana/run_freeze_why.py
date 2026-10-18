#!/usr/bin/env python3
"""Why YouTube and not Vimeo? Four experiments that can each say "neither".

The earlier reading -- that YouTube simply wants more CPU -- does not survive
its own numbers: solo renderer CPU is about 479 ms/s for YouTube and 501 ms/s
for Vimeo, which is no difference at all. So the asymmetry needs a real cause,
and these are the candidates that can be separated by experiment:

  yt_first   YouTube alone for 60 s, then Vimeo joins. If YouTube freezes only
             when it is the one still settling, order matters, not identity.
  vm_first   The mirror image. If YouTube still freezes while Vimeo had the
             head start, order does not explain it.
  vimeo_cpu  Vimeo alone with the same two spinners that froze YouTube. If
             Vimeo freezes too, nothing about YouTube is special and the
             concurrent asymmetry must come from the pairing, not the app.
  yt_480p    YouTube pinned to 480p. If the freeze goes with the resolution,
             decode cost is implicated; the in-page log also records whether
             an ad was on screen during the window.

Every run carries the in-page log (page clock, long tasks, ad flag, decoder
capability) and the whole-host probe (every process's CPU, context switches,
memory), so a freeze can be read against what the machine was doing.
"""

import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, HOME)
import run_matrix as M  # noqa: E402
import pramana_helpers as H  # noqa: E402

os.environ.setdefault(
    "PRAMANA_COLLECTOR_SRC",
    os.path.join(HOME, "services/orchestration/scripts/selenium_video_qoe/collect.py"),
)
os.environ.setdefault("PRAMANA_DEFER_ANALYSIS", "1")

# A run that starts with a nearly full disk does not fail honestly: the
# collector cannot unpack its driver, Chrome never launches, and the run
# "completes" with an empty qoe/ and null metrics. That happened to a whole
# 12-run batch. Refuse to start instead.
MIN_FREE_GB = float(os.environ.get("PRAMANA_MIN_FREE_GB", "2"))


def free_gb(path="/"):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e9


def require_space(stage):
    gb = free_gb()
    if gb < MIN_FREE_GB:
        print(
            "ABORT %s: only %.2f GB free, need %.1f GB. Nothing was run."
            % (stage, gb, MIN_FREE_GB),
            flush=True,
        )
        raise SystemExit(3)
    print("disk check %s: %.2f GB free" % (stage, gb), flush=True)


def drop_our_capture(slug):
    """Remove this run's staging capture; the run dir keeps its own copy."""
    f = "/tmp/substrate-capture/%s.pcap" % slug
    if not os.path.exists(f):
        return
    try:
        subprocess.run(
            ["sudo", "rm", "-f", f],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("cleaned staging capture %s" % os.path.basename(f), flush=True)
    except Exception:
        pass


BW = float(os.environ.get("BW", "6"))
DUR = int(os.environ.get("DUR", "180"))

EXPERIMENTS = {
    "yt_first": {
        "apps": ["youtube", "vimeo"],
        "delay": {"vimeo": 60},
        "burners": 0,
        "q": None,
    },
    "vm_first": {
        "apps": ["youtube", "vimeo"],
        "delay": {"youtube": 60},
        "burners": 0,
        "q": None,
    },
    "vimeo_cpu": {"apps": ["vimeo"], "delay": {}, "burners": 2, "q": None},
    "yt_480p": {"apps": ["youtube", "vimeo"], "delay": {}, "burners": 0, "q": "large"},
    # Graded load: how much starvation does it actually take? Vimeo survives
    # two spinners, so the interesting question is where YouTube's threshold is.
    "yt_cpu1": {"apps": ["youtube"], "delay": {}, "burners": 1, "q": None},
    "yt_cpu2": {"apps": ["youtube"], "delay": {}, "burners": 2, "q": None},
    # The concurrent cell with the V8 profiler on, to see inside the long task.
    "trace": {"apps": ["youtube", "vimeo"], "delay": {}, "burners": 0, "q": None},
}

BURNER = "import time\nend=time.time()+%d\nx=0\nwhile time.time()<end:\n    x+=1\n"


def main():
    name = sys.argv[1]
    if name not in EXPERIMENTS:
        raise SystemExit("experiments: %s" % ", ".join(EXPERIMENTS))
    spec = EXPERIMENTS[name]
    trial = int(os.environ.get("TRIAL", "1"))

    os.environ["PRAMANA_INPAGE_LOG"] = "1"
    if name == "trace":
        os.environ["PRAMANA_TRACE"] = "1"
    os.environ["PRAMANA_START_DELAY_S"] = json.dumps(spec["delay"])

    tag = "why_%s_%gM_t%d" % (name, BW, trial)
    require_space("before " + tag)
    gate, waited, l1, chrome = M.wait_for_quiet_host(tag)
    print(
        "=== %s === gate=%s load1=%s waited=%.0fs" % (tag, gate, l1, waited), flush=True
    )

    window = DUR + 240
    hp = "/tmp/host_probe_%s_t%d.jsonl" % (name, trial)
    for f in (hp,):
        try:
            os.remove(f)
        except OSError:
            pass
    env = dict(os.environ, PROBE_DURATION_S=str(window), PROBE_INTERVAL_S="0.5")
    probe = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "host_probe.py"), hp],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    burners = [
        subprocess.Popen(
            [sys.executable, "-c", BURNER % window],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(spec["burners"])
    ]
    if burners:
        print("started %d CPU burner(s)" % len(burners), flush=True)

    rec = None
    try:
        kw = dict(
            apps=spec["apps"],
            bandwidth_mbps=BW,
            latency_ms=50,
            loss_pct=0.0,
            aqm="pfifo",
            cca="cubic",
            duration_s=DUR,
            trial=trial,
        )
        if spec["q"]:
            kw["force_max_quality"] = True
            kw["force_quality_level"] = spec["q"]
        rec = H.run_direct(H.ExperimentConfig(**kw))
    except Exception:
        import traceback

        traceback.print_exc()
    finally:
        for b in burners:
            try:
                b.send_signal(signal.SIGTERM)
            except Exception:
                pass
        try:
            probe.terminate()
        except Exception:
            pass

    # Clean up after ourselves so a long batch does not fill the disk the way
    # the first attempt did.
    try:
        slug = "%s_%gmbps_50ms_0pct_pfifo_cubic_%s_t%d" % (
            "+".join(spec["apps"]),
            BW,
            "concurrent2" if len(spec["apps"]) > 1 else "solo",
            trial,
        )
        drop_our_capture(slug)
        drop_our_capture(slug + "_forcedq")
    except Exception:
        pass
    print("disk after %s: %.2f GB free" % (tag, free_gb()), flush=True)
    print("HOSTPROBE %s" % hp, flush=True)
    if isinstance(rec, dict):
        for app in spec["apps"]:
            q = (rec.get("player_qoe") or {}).get(app) or {}
            u = q.get("uniform") or {}
            print(
                "ROW %s %s"
                % (
                    app,
                    json.dumps(
                        {
                            "res_p": q.get("video_resolution_p"),
                            "startup_delay_ms": u.get("startup_delay_ms"),
                            "rebuffer_events": u.get("rebuffer_events"),
                            "rendered_fps": u.get("rendered_fps"),
                        }
                    ),
                ),
                flush=True,
            )
    print("WHY_DONE %s %d" % (name, trial), flush=True)


if __name__ == "__main__":
    main()
