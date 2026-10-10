#!/usr/bin/env bash
# Archive the release runs' pcaps off the run tree and verify by checksum.
#
# pcaps are NOT committed (they are large and the brief forbids it), so the
# manifest in the release is only trustworthy if the files it names actually
# exist somewhere with the stated hash. This copies them to the VM archive and
# verifies each copy, then prints a manifest the laptop side can re-check.
set -uo pipefail
MANIFEST="${1:?usage: archive_pcaps.sh <pcap_manifest.csv> <built_runs.json> <archive_dir>}"
BUILT="${2:?}"
ARCHIVE="${3:?}"
mkdir -p "$ARCHIVE"
fail=0
python3 - "$MANIFEST" "$BUILT" "$ARCHIVE" <<'PY'
import csv, hashlib, json, pathlib, shutil, sys
man, built, archive = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

runs = {b["name"]: b for b in json.loads(pathlib.Path(built).read_text())}
rows = list(csv.DictReader(open(man)))
bad = 0
for r in rows:
    name = r["run"]
    src = pathlib.Path(runs[name]["dir"]).parent  # unused; resolve from VM dir
    vm_dir = json.loads(pathlib.Path(built).read_text())
    srcp = None
    for b in vm_dir:
        if b["name"] == name:
            # run_meta.json records where the raw run lives
            meta = pathlib.Path(b["dir"]) / "run_meta.json"
            if meta.exists():
                srcp = pathlib.Path(json.loads(meta.read_text())["run_dir_on_vm"]) / "capture.pcap"
            break
    if srcp is None or not srcp.exists():
        print("  MISSING source pcap for %s (%s)" % (name, srcp))
        bad += 1
        continue
    dst = archive / ("%s.pcap" % name)
    if not dst.exists() or dst.stat().st_size != srcp.stat().st_size:
        shutil.copy2(srcp, dst)
    got = sha256(dst)
    ok = got == r["sha256"]
    print("  %-52s %9.1f MB  %s" % (name, dst.stat().st_size / 1e6,
                                    "OK" if ok else "CHECKSUM MISMATCH"))
    if not ok:
        bad += 1
print("archived %d pcaps, %d problem(s)" % (len(rows), bad))
sys.exit(1 if bad else 0)
PY
