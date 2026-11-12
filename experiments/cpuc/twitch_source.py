#!/usr/bin/env python3
"""What ladder is Twitch actually offering for a channel, right now?

WHY THIS IS A GATE AND NOT A NOTE. The sweep's whole subject is ADAPTATION:
which rendition the player picks as capacity falls. That only means anything
if the player has renditions to pick between. Twitch transcodes are not
guaranteed for a non-partner channel -- they are allocated best-effort, and
when they are not allocated the channel is served SOURCE ONLY: one variant,
usually 1080p60, with nothing below it. On a source-only channel a capacity
sweep measures buffering, not adaptation, and every row below the source
bitrate would say "it broke" rather than "it stepped down".

`conditions.TWITCH_PROBED_LADDERS_P` records a probe from 2026-11-07 and the
comment there says to re-probe before a campaign for exactly this reason. This
module is that re-probe, run for real rather than trusted.

WHERE THE LADDER COMES FROM. Twitch's player fetches an HLS master playlist
from usher.ttvnw.net, and that playlist lists every variant the player may
choose, with its RESOLUTION and FRAME-RATE. Getting it needs a signed playback
access token from Twitch's public GraphQL endpoint, with the web player's own
public client id -- the same two requests the player itself makes. Nothing is
scraped and nothing is logged in to.

The output is deliberately shaped like the YouTube `renditions.json` sidecar,
so `sweep.load_renditions` and `sweep.assert_source_high_enough` apply to
Twitch with no new code and the same 1080p source requirement is enforced by
the same function.

Probed from the HOST, not from ns1: this is a question about the channel, not
about the shaped link, and sending it through the bottleneck would both
disturb a measurement and risk the probe itself timing out at 0.5 Mb/s.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Optional

# The Twitch web player's own public client id. Not a secret and not ours: it
# is the value the player sends from any browser, and the playback token
# endpoint rejects requests without it.
WEB_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
GQL_URL = "https://gql.twitch.tv/gql"
USHER = "https://usher.ttvnw.net/api/channel/hls/{channel}.m3u8"

# Twitch's name for the untranscoded original. A ladder containing only this
# is the source-only case this module exists to catch.
SOURCE_NAMES = ("chunked", "source")


class ProbeFailed(RuntimeError):
    """The ladder could not be read, so nothing is known about the source."""


class SourceOnly(RuntimeError):
    """The channel is being served without transcodes."""


def _curl(args: list[str], timeout: int = 30) -> str:
    out = subprocess.run(
        ["curl", "-sS", "--max-time", str(timeout), *args],
        capture_output=True,
        text=True,
        timeout=timeout + 15,
    )
    if out.returncode != 0:
        raise ProbeFailed(f"curl failed: {out.stderr.strip()[:200]}")
    return out.stdout


def playback_token(channel: str) -> tuple[str, str]:
    """(signature, token) for `channel`, from Twitch's public GQL endpoint."""
    query = {
        "operationName": "PlaybackAccessToken",
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": (
                    "0828119ded1c13477966434e15800ff57ddacf13ba1911c129dc2200705b0712"
                ),
            }
        },
        "variables": {
            "isLive": True,
            "login": channel,
            "isVod": False,
            "vodID": "",
            "playerType": "embed",
        },
    }
    body = _curl(
        [
            "-H",
            f"Client-ID: {WEB_CLIENT_ID}",
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(query),
            GQL_URL,
        ]
    )
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProbeFailed(f"GQL returned non-JSON: {body[:200]}") from exc
    tok = ((data.get("data") or {}).get("streamPlaybackAccessToken")) or {}
    sig, value = tok.get("signature"), tok.get("value")
    if not sig or not value:
        raise ProbeFailed(
            f"no playback token for {channel!r}; the channel may be offline "
            f"or the response shape changed: {body[:300]}"
        )
    return sig, value


def master_playlist(channel: str) -> str:
    sig, token = playback_token(channel)
    url = USHER.format(channel=channel)
    text = _curl(
        [
            "-G",
            "--data-urlencode",
            f"sig={sig}",
            "--data-urlencode",
            f"token={token}",
            "--data-urlencode",
            "allow_source=true",
            "--data-urlencode",
            "allow_audio_only=true",
            "--data-urlencode",
            "fast_bread=true",
            url,
        ]
    )
    if "#EXTM3U" not in text:
        raise ProbeFailed(f"usher did not return a playlist: {text[:300]}")
    return text


def parse_ladder(playlist: str) -> list[dict]:
    """Every video variant the player may choose, from the master playlist.

    Audio-only variants are dropped: they carry no RESOLUTION and are not a
    rendition a video sweep can land on.
    """
    out: list[dict] = []
    media_name: Optional[str] = None
    for line in playlist.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MEDIA:"):
            m = re.search(r'NAME="([^"]+)"', line)
            media_name = m.group(1) if m else None
            continue
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        res = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
        vid = re.search(r'VIDEO="([^"]+)"', line)
        bw = re.search(r"BANDWIDTH=(\d+)", line)
        fps = re.search(r"FRAME-RATE=([\d.]+)", line)
        if not res:
            media_name = None
            continue
        out.append(
            {
                "group": vid.group(1) if vid else "",
                "name": media_name or (vid.group(1) if vid else ""),
                "width": int(res.group(1)),
                "height": int(res.group(2)),
                "bandwidth_bps": int(bw.group(1)) if bw else None,
                "fps": float(fps.group(1)) if fps else None,
            }
        )
        media_name = None
    return out


def is_source(variant: dict) -> bool:
    return variant.get("group", "").lower() in SOURCE_NAMES


def probe(channel: str) -> dict:
    """The sidecar. Same keys `sweep.load_renditions` reads, plus the ladder."""
    playlist = master_playlist(channel)
    ladder = parse_ladder(playlist)
    if not ladder:
        raise ProbeFailed(
            f"no video variants in {channel}'s master playlist; the channel is "
            f"offline or serving audio only"
        )
    heights = sorted({v["height"] for v in ladder}, reverse=True)
    transcodes = [v for v in ladder if not is_source(v)]
    return {
        "url": f"https://www.twitch.tv/{channel}",
        "channel": channel,
        "probed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": True,
        # The keys sweep.load_renditions / assert_source_high_enough read.
        "rendition_heights": heights,
        "max_rendition_p": heights[0],
        "has_1080p_plus": heights[0] >= 1080,
        # Twitch-specific, and the reason this module exists.
        "variant_count": len(ladder),
        "transcode_count": len(transcodes),
        "source_only": not transcodes,
        "ladder": ladder,
    }


def assert_transcodes_present(info: dict, min_transcodes: int = 2) -> None:
    """Refuse a sweep on a channel with no ladder to adapt along.

    `min_transcodes` is 2, not 1: a single transcode plus the source gives the
    player two choices in total, which cannot show a stepped descent across
    eight capacities spanning 0.5 to 100 Mb/s. The number is the floor at
    which the sweep still measures adaptation rather than a single switch.
    """
    if info.get("source_only"):
        raise SourceOnly(
            f"{info.get('channel')!r} is being served SOURCE ONLY "
            f"({info.get('max_rendition_p')}p, {info.get('variant_count')} "
            f"variant) -- Twitch has allocated no transcodes for it. A capacity "
            f"sweep here would measure buffering, not adaptation."
        )
    n = int(info.get("transcode_count") or 0)
    if n < min_transcodes:
        raise SourceOnly(
            f"{info.get('channel')!r} offers only {n} transcode(s) besides the "
            f"source; {min_transcodes} are needed for a descent to be visible "
            f"across the sweep's eight capacities. Ladder: "
            f"{[v['height'] for v in info.get('ladder') or []]}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", default="")
    ap.add_argument("--out", default="")
    ap.add_argument(
        "--require-transcodes",
        type=int,
        default=2,
        help="minimum transcodes besides the source (default 2)",
    )
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from cpuc import conditions as C  # noqa: PLC0415

    channel = args.channel or C.TWITCH_PINNED_CHANNEL
    info = probe(channel)
    print(
        f"{channel}: {info['variant_count']} variants, "
        f"{info['transcode_count']} transcodes, max {info['max_rendition_p']}p"
    )
    for v in info["ladder"]:
        print(
            f"  {v['height']:>4}p{v['fps'] or '':<5} {v['width']}x{v['height']} "
            f"{(v['bandwidth_bps'] or 0)/1e6:6.2f} Mb/s  group={v['group']}"
            f"{'  (SOURCE)' if is_source(v) else ''}"
        )
    recorded = C.TWITCH_PROBED_LADDERS_P.get(channel)
    if recorded is not None and recorded != info["max_rendition_p"]:
        print(
            f"  ! conditions.TWITCH_PROBED_LADDERS_P records {recorded}p for "
            f"{channel}; the live ladder tops out at {info['max_rendition_p']}p"
        )
    assert_transcodes_present(info, args.require_transcodes)
    print("source gate: OK")
    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w") as fh:
            json.dump(info, fh, indent=1)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
