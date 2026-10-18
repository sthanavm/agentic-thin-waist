#!/usr/bin/env python3
"""Four conditions that could each disprove a reason for the YouTube freeze.

Measured so far: in the concurrent 6 Mbps cell the YouTube page's own 200 ms
timer stops for about 16 s, three runs out of three, while Vimeo's never stops
for more than 1 s on the same host in the same window. That says the page's
main thread halts. It does not say why. These conditions separate the
candidates, and each can come out either way:

  solo          YouTube alone. If it freezes here, nothing about sharing the
                host matters and every other condition is beside the point.
  cpuload       YouTube alone plus two CPU burners, i.e. contention WITHOUT a
                second browser. Freezes -> plain CPU starvation is enough.
                Does not freeze -> contention alone is not the cause.
  twitch        YouTube beside a different second app. Freezes -> nothing to do
                with Vimeo. Does not freeze -> something about that pairing.
  pinned        YouTube and Vimeo with one core each, so neither can take the
                other's. Freezes -> not core contention. Does not -> it is.

Every condition runs the in-page log and a host-side CPU probe, so a freeze can
be checked against whether the renderer was getting any CPU at the time.
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
os.environ["PRAMANA_INPAGE_LOG"] = "1"

BW = float(os.environ.get("BW", "6"))
DUR = int(os.environ.get("DUR", "180"))

CONDITIONS = {
    "solo": {"apps": ["youtube"], "burners": 0, "pin": False},
    "cpuload": {"apps": ["youtube"], "burners": 2, "pin": False},
    "twitch": {"apps": ["youtube", "twitch"], "burners": 0, "pin": False},
    "pinned": {"apps": ["youtube", "vimeo"], "burners": 0, "pin": True},
}

BURNER = (
    "import time\n"
    "end = time.time() + %d\n"
    "x = 0\n"
    "while time.time() < end:\n"
    "    x += 1\n"
)

# Pins each browser to one core by the X display it was launched on, which is
# how the collector separates the two apps.
PINNER = r"""
import os, subprocess, sys, time
end = time.time() + float(sys.argv[1])
seen = {}
order = []
while time.time() < end:
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or pid in seen:
            continue
        try:
            cmd = open('/proc/%s/cmdline' % pid, 'rb').read().replace(b'\0', b' ').decode('utf-8', 'replace')
        except Exception:
            continue
        if 'chrome' not in cmd.lower():
            continue
        disp = None
        for tok in cmd.split():
            if tok.startswith('--display='):
                disp = tok.split('=', 1)[1]
        if disp is None:
            continue
        if disp not in order:
            order.append(disp)
        cpu = order.index(disp) % os.cpu_count()
        try:
            subprocess.run(['taskset', '-a', '-p', '-c', str(cpu), pid],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            seen[pid] = cpu
        except Exception:
            pass
    time.sleep(0.5)
print('pinned %d process(es): %s' % (len(seen), sorted(set(seen.values()))))
"""


def start_burners(n, secs):
    procs = []
    for _ in range(n):
        procs.append(
            subprocess.Popen(
                [sys.executable, "-c", BURNER % secs],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        )
    return procs


def main():
    cond = sys.argv[1]
    if cond not in CONDITIONS:
        raise SystemExit("conditions: %s" % ", ".join(CONDITIONS))
    spec = CONDITIONS[cond]
    trial = int(os.environ.get("TRIAL", "1"))

    tag = "freeze_%s_%gM_t%d" % (cond, BW, trial)
    gate, waited, l1, chrome = M.wait_for_quiet_host(tag)
    print(
        "=== %s === gate=%s load1=%s waited=%ss" % (tag, gate, l1, waited), flush=True
    )

    window = DUR + 180  # the run plus its setup and teardown
    probe_out = "/tmp/cpu_probe_%s_t%d.jsonl" % (cond, trial)
    env = dict(os.environ, PROBE_DURATION_S=str(window), PROBE_INTERVAL_S="0.2")
    probe = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "cpu_probe.py"), probe_out],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    burners = start_burners(spec["burners"], window) if spec["burners"] else []
    pinner = None
    if spec["pin"]:
        pinner = subprocess.Popen(
            [sys.executable, "-c", PINNER, str(window)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    if burners:
        print("started %d CPU burner(s)" % len(burners), flush=True)

    rec = None
    try:
        cfg = H.ExperimentConfig(
            apps=spec["apps"],
            bandwidth_mbps=BW,
            latency_ms=50,
            loss_pct=0.0,
            aqm="pfifo",
            cca="cubic",
            duration_s=DUR,
            trial=trial,
        )
        rec = H.run_direct(cfg)
    except Exception:
        import traceback

        traceback.print_exc()
    finally:
        for b in burners:
            try:
                b.send_signal(signal.SIGTERM)
            except Exception:
                pass
        for p in (probe, pinner):
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass
        if pinner is not None:
            try:
                print(pinner.communicate(timeout=10)[0], flush=True)
            except Exception:
                pass

    print("PROBE %s" % probe_out, flush=True)
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
                            "status": q.get("status"),
                            "startup_delay_ms": u.get("startup_delay_ms"),
                            "rebuffer_events": u.get("rebuffer_events"),
                            "rendered_fps": u.get("rendered_fps"),
                        }
                    ),
                ),
                flush=True,
            )
    print("FREEZE_COND_DONE %s" % cond, flush=True)


if __name__ == "__main__":
    main()
