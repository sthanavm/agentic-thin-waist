#!/usr/bin/env bash
# Pull a finished CPUC batch to the laptop and verify it byte for byte.
#
# sha256 is computed on the VM BEFORE transfer and re-computed locally after,
# then compared. A plain rsync would tell us it copied something; this tells us
# it copied the same thing. Never deletes on either side.
set -euo pipefail

VM="${VM:-vm4}"
REMOTE_DIR="${REMOTE_DIR:-/home/student/cpuc_data}"
LOCAL_DIR="${LOCAL_DIR:-/Users/jenilprajapati/SNL-Lab-Codebases/cpuc-data}"

mkdir -p "$LOCAL_DIR"

echo "== manifest on the VM =="
ssh "$VM" "cd '$REMOTE_DIR' && find . -type f \( -name '*.csv' -o -name '*.json' -o -name '*.pcap' -o -name '*.jsonl' -o -name '*.log' -o -name 'COMPLETE*' \) -print0 | sort -z | xargs -0 sha256sum" \
  > "$LOCAL_DIR/manifest.remote.txt"
echo "   $(wc -l < "$LOCAL_DIR/manifest.remote.txt") files"

echo "== transfer (no delete) =="
rsync -a --info=stats2 "$VM:$REMOTE_DIR/" "$LOCAL_DIR/" --exclude 'manifest.*.txt' | tail -4

echo "== verify locally =="
( cd "$LOCAL_DIR" && awk '{print $2}' manifest.remote.txt | tr -d '\r' \
    | while IFS= read -r f; do [ -f "$f" ] && shasum -a 256 "$f"; done ) \
  > "$LOCAL_DIR/manifest.local.txt"

python3 - "$LOCAL_DIR" <<'PYEOF'
import sys, pathlib
d = pathlib.Path(sys.argv[1])
def load(p):
    out = {}
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        h, _, f = line.partition(" ")
        out[f.strip().lstrip("*").lstrip("./")] = h
    return out
r, l = load(d / "manifest.remote.txt"), load(d / "manifest.local.txt")
missing = sorted(set(r) - set(l))
bad = sorted(f for f in set(r) & set(l) if r[f] != l[f])
print(f"   remote files : {len(r)}")
print(f"   verified     : {len(set(r) & set(l)) - len(bad)}")
print(f"   missing      : {len(missing)}")
print(f"   MISMATCHED   : {len(bad)}")
for f in (missing + bad)[:10]:
    print("     !", f)
raise SystemExit(1 if (missing or bad) else 0)
PYEOF
echo "== archive verified =="
