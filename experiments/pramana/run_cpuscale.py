#!/usr/bin/env python3
"""Does more CPU fix the concurrent YouTube freeze?

Pre-registered in validation/CPU_SCALING.md before any of this was collected.
Concurrent YouTube + Vimeo at a shared 6 Mbps cap, with our collector container
limited to 8, 4 or 2 CPUs. The 2-CPU case is a same-host control: it should
reproduce the old freeze, and if it does not, that is a finding about the host
rather than a confirmation of the old one.

The limit applies to our collector container only -- never the shared worker,
never the host.
"""

import json
import os
import subprocess
import sys

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

BW = float(os.environ.get("BW", "6"))
DUR = int(os.environ.get("DUR", "180"))
MIN_FREE_GB = float(os.environ.get("PRAMANA_MIN_FREE_GB", "2"))
APPS = (os.environ.get("APPS") or "youtube,vimeo").split(",")


def free_gb(path="/"):
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize / 1e9


def c1_watch(want_cpus, out_path):
    """Record the container's CPU quota WHILE it runs.

    The collector container is started with --rm, so inspecting after the run
    finds nothing and C1 can never be answered. This polls until the container
    appears, records its quota once, and stops.
    """
    import threading
    import time as _t

    def _loop():
        end = _t.time() + 300
        while _t.time() < end:
            try:
                ids = subprocess.run(
                    [
                        "docker",
                        "ps",
                        "--filter",
                        "ancestor=video-qoe-collector",
                        "--format",
                        "{{.ID}}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                ).stdout.split()
                if ids:
                    nano = subprocess.run(
                        ["docker", "inspect", "-f", "{{.HostConfig.NanoCpus}}", ids[0]],
                        capture_output=True,
                        text=True,
                        timeout=20,
                    ).stdout.strip()
                    got = float(nano) / 1e9 if nano and nano != "0" else None
                    if want_cpus is None:
                        msg = "C1 PASS: no limit requested, container quota=%s" % (
                            ("%g" % got) if got else "none"
                        )
                    elif got is None:
                        msg = (
                            "C1 FAIL: requested %g CPU, container has NO quota"
                            % want_cpus
                        )
                    else:
                        msg = "C1 %s: requested %g CPU, container quota %g" % (
                            "PASS" if abs(got - want_cpus) < 0.01 else "FAIL",
                            want_cpus,
                            got,
                        )
                    open(out_path, "w").write(msg + "\n")
                    return
            except Exception:
                pass
            _t.sleep(2)
        open(out_path, "w").write("C1 UNKNOWN: collector container never seen\n")

    th = threading.Thread(target=_loop, daemon=True)
    th.start()
    return th


def c1_check(want_cpus):
    """Band C1: the quota we asked for is the quota the container got.

    A limit that silently failed to apply would make a full-speed run look
    like evidence about a small one, so this is checked rather than assumed.
    """
    try:
        out = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "ancestor=video-qoe-collector",
                "--format",
                "{{.ID}}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.split()
        if not out:
            return "C1 UNKNOWN: no collector container found to inspect"
        cid = out[0]
        nano = subprocess.run(
            ["docker", "inspect", "-f", "{{.HostConfig.NanoCpus}}", cid],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        got = float(nano) / 1e9 if nano and nano != "0" else None
        if want_cpus is None:
            return "C1 PASS: no limit requested, container quota=%s" % (got or "none")
        if got is None:
            return "C1 FAIL: requested %g CPU, container has NO quota" % want_cpus
        ok = abs(got - want_cpus) < 0.01
        return "C1 %s: requested %g CPU, container quota %g" % (
            "PASS" if ok else "FAIL",
            want_cpus,
            got,
        )
    except Exception as exc:  # noqa: BLE001
        return "C1 UNKNOWN: %s" % exc


def main():
    label = sys.argv[1]  # e.g. "8vcpu"
    cpus = None if len(sys.argv) < 3 or sys.argv[2] == "none" else float(sys.argv[2])
    trial = int(os.environ.get("TRIAL", "1"))

    gb = free_gb()
    if gb < MIN_FREE_GB:
        print("ABORT: %.2f GB free, need %.1f" % (gb, MIN_FREE_GB), flush=True)
        raise SystemExit(3)
    print("disk check: %.2f GB free" % gb, flush=True)

    os.environ["PRAMANA_INPAGE_LOG"] = "1"
    if cpus is None:
        os.environ.pop("PRAMANA_COLLECTOR_CPUS", None)
    else:
        os.environ["PRAMANA_COLLECTOR_CPUS"] = str(cpus)

    tag = "cpuscale_%s_%gM_t%d" % (label, BW, trial)
    gate, waited, l1, chrome = M.wait_for_quiet_host(tag)
    print(
        "=== %s === gate=%s load1=%s waited=%.0fs" % (tag, gate, l1, waited), flush=True
    )

    c1_path = "/tmp/c1_%s_t%d.txt" % (label, trial)
    c1_watch(cpus, c1_path)

    hp = "/tmp/host_probe_%s_t%d.jsonl" % (label, trial)
    try:
        os.remove(hp)
    except OSError:
        pass
    probe = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "host_probe.py"), hp],
        env=dict(os.environ, PROBE_DURATION_S=str(DUR + 240), PROBE_INTERVAL_S="0.5"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    rec = None
    try:
        rec = H.run_direct(
            H.ExperimentConfig(
                apps=APPS,
                bandwidth_mbps=BW,
                latency_ms=50,
                loss_pct=0.0,
                aqm="pfifo",
                cca="cubic",
                duration_s=DUR,
                trial=trial,
            )
        )
    except Exception:
        import traceback

        traceback.print_exc()
    finally:
        try:
            probe.terminate()
        except Exception:
            pass

    try:
        print(open(c1_path).read().strip(), flush=True)
    except Exception:
        print(c1_check(cpus), flush=True)
    if isinstance(rec, dict):
        for app in APPS:
            q = (rec.get("player_qoe") or {}).get(app) or {}
            u = q.get("uniform") or {}
            print(
                "ROW %s %s"
                % (
                    app,
                    json.dumps(
                        {
                            "cond": label,
                            "trial": trial,
                            "status": q.get("status"),
                            "res_p": q.get("video_resolution_p"),
                            "startup_delay_ms": u.get("startup_delay_ms"),
                            "rebuffer_events": u.get("rebuffer_events"),
                            "video_mbps": u.get("delivered_video_bitrate_mbps"),
                            "audio_mbps": u.get("delivered_audio_bitrate_mbps"),
                            "switch_count": u.get("switch_count"),
                            "rendered_fps": u.get("rendered_fps"),
                        }
                    ),
                ),
                flush=True,
            )
    print("HOSTPROBE %s" % hp, flush=True)
    print("disk after: %.2f GB free" % free_gb(), flush=True)
    print("CPUSCALE_DONE %s t%d" % (label, trial), flush=True)


if __name__ == "__main__":
    main()
