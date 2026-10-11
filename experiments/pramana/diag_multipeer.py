#!/usr/bin/env python3
"""Why two YouTube trials scored BELOW 1.00 against media-peer bytes.

A ratio under 1.00 says the wire carried fewer bytes than the player appended,
which cannot happen if the reference is complete. So the reference -- bytes
to/from the single dominant peer IP -- is the thing at fault. This reports the
top peers by bytes and the cumulative ratio as each is added, which shows
directly whether the media arrived over more than one peer.
"""
import json
import pathlib
import sys

HOME = pathlib.Path.home() / "sthanav-agentic-thin-waist"
sys.path.insert(0, str(HOME / "experiments/pramana"))
sys.path.insert(0, str(HOME))
import diag_attribution as DA  # noqa: E402

rows = json.loads(pathlib.Path("/tmp/spread_rows.json").read_text())
dirs = {}
for line in pathlib.Path("/tmp/it36b.log").read_text(errors="replace").splitlines():
    if line.startswith("DIR "):
        _, tag, d = line.split(None, 2)
        dirs[tag] = d.strip()

for r in rows:
    if not str(r["app"]).startswith("youtube"):
        continue
    d = dirs.get(r["tag"])
    if not d:
        continue
    mse = (r["mse_v"] or 0) + (r["mse_a"] or 0)
    total, peers, pproto, dns = DA.analyse(pathlib.Path(d) / "capture.pcap")
    print(
        "%-24s MSE=%d  single-peer ratio=%s"
        % (r["tag"], mse, r.get("ratio_peer_over_mse"))
    )
    cum = 0
    for i, (ip, b) in enumerate(peers.most_common(5)):
        cum += b
        pr = pproto.get(ip, {})
        print(
            "   %d. %-16s %8.2f MB  cum=%8.2f MB  cum/MSE=%6.4f   UDP=%6.2f TCP=%6.2f"
            % (
                i + 1,
                ip,
                b / 1e6,
                cum / 1e6,
                cum / mse if mse else 0,
                pr.get(17, 0) / 1e6,
                pr.get(6, 0) / 1e6,
            )
        )
    print(
        "   whole capture = %.2f MB  -> all/MSE=%.4f"
        % (total / 1e6, total / mse if mse else 0)
    )
    print()
