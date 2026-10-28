#!/usr/bin/env python3
"""Generate a Y4M clip for a bot peer's fake camera.

Chrome's `--use-file-for-fake-video-capture` wants raw Y4M. What matters for a
QoE measurement is that the clip *compresses like real video*: an easy synthetic
pattern (a bar sliding over a gradient) encodes to a fraction of a real camera's
bitrate, so every downstream number - inbound bitrate, bytes on the wire, the
throughput plots - comes out unrealistically low.

So the frame carries three things a camera also has:
  * static spatial texture (fine detail that survives intra-coding),
  * a scene that translates slowly (motion the encoder must actually track),
  * per-frame grain (high-frequency noise that defeats trivial temporal reuse).

Pure noise would be the opposite error - incompressible, and worse than any real
camera - so the grain is masked to the low bits and laid over structure.
"""

from __future__ import annotations

import argparse
import os
import random


def _texture_plane(w: int, h: int, seed: int) -> bytearray:
    """Static scene: smooth regions, hard edges, and fine detail."""
    rnd = random.Random(seed)
    plane = bytearray(w * h)
    # a handful of soft "objects" plus a textured background
    blobs = [
        (
            rnd.randrange(w),
            rnd.randrange(h),
            rnd.randrange(40, 130),
            rnd.randrange(60, 210),
        )
        for _ in range(9)
    ]
    for y in range(h):
        base = 40 + (y * 90 // max(h - 1, 1))
        row = y * w
        for x in range(w):
            v = base + ((x * 13 + y * 7) % 37)  # fine cross-hatch detail
            for bx, by, br, bv in blobs:
                if (x - bx) * (x - bx) + (y - by) * (y - by) < br * br:
                    v = (v + bv) // 2
                    break
            plane[row + x] = v & 0xFF
    return plane


def _grain_plane(n: int, seed: int, mask: int = 0x1F) -> bytes:
    """Low-amplitude per-pixel noise: camera grain, not static."""
    rnd = random.Random(seed)
    raw = rnd.randbytes(n)
    # big-int AND keeps this at C speed instead of a per-byte Python loop
    m = int.from_bytes(bytes([mask]) * n, "big")
    return (int.from_bytes(raw, "big") & m).to_bytes(n, "big")


def write_clip(
    path: str,
    width: int = 640,
    height: int = 480,
    frames: int = 150,
    fps: int = 30,
    seed: int = 1234,
) -> str:
    n = width * height
    scene = bytes(_texture_plane(width, height, seed))
    grains = [
        _grain_plane(n, seed + i, 0x1F) for i in range(12)
    ]  # cycled, not per-frame
    half = (width // 2) * (height // 2)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(f"YUV4MPEG2 W{width} H{height} F{fps}:1 Ip A1:1 C420mpeg2\n".encode())
        for i in range(frames):
            # translate the scene: real motion for the encoder to track
            shift = ((i * 3) % width) + ((i * width) % n)
            rolled = scene[shift:] + scene[:shift]
            grain = grains[i % len(grains)]
            luma = (
                int.from_bytes(rolled, "big") ^ int.from_bytes(grain, "big")
            ).to_bytes(n, "big")
            fh.write(b"FRAME\n")
            fh.write(luma)
            # slowly drifting chroma keeps colour from being a constant plane
            fh.write(bytes([120 + (i * 2) % 24]) * half)
            fh.write(bytes([136 - (i * 3) % 24]) * half)
    return path


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", default="/tmp/bot_clip.y4m")
    p.add_argument("--width", type=int, default=640)
    p.add_argument("--height", type=int, default=480)
    p.add_argument("--frames", type=int, default=150)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--seed", type=int, default=1234)
    a = p.parse_args()
    path = write_clip(a.out, a.width, a.height, a.frames, a.fps, a.seed)
    size = os.path.getsize(path)
    print(
        f"[clip] {path}  {size / 1e6:.1f} MB  {a.width}x{a.height} "
        f"{a.frames} frames @{a.fps}fps ({a.frames / a.fps:.1f}s loop)"
    )


if __name__ == "__main__":
    main()
