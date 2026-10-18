#!/usr/bin/env python3
"""Generate a WAV for a bot peer's fake microphone.

Chrome's ``--use-file-for-fake-audio-capture`` wants a plain PCM WAV, which the
stdlib can write without ffmpeg (there is none on the hosts or in the image).

Why a tone rather than silence: Zoom applies voice activity detection and can
drop or suspend a track that carries nothing, so a silent file risks the audio
leg being torn down mid-call - the opposite of what adding audio is for. This
is a quiet, slowly-warbling tone: continuously "speaking" so the stream stays
up, low enough not to matter to anyone listening in.
"""

from __future__ import annotations

import argparse
import math
import struct
import wave


def write_tone(
    path: str,
    seconds: float = 10.0,
    rate: int = 48000,
    hz: float = 220.0,
    amplitude: float = 0.05,
) -> str:
    """Write a mono 16-bit PCM WAV that loops cleanly.

    The duration is rounded to a whole number of cycles so the end meets the
    start; Chrome loops the file, and a partial cycle would click every repeat.
    """
    cycles = max(1, round(seconds * hz))
    total = int(round(cycles * rate / hz))
    peak = int(amplitude * 32767)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for n in range(total):
            # Slow vibrato keeps the signal non-stationary, so VAD sees speech-
            # like variation instead of a constant sine it may treat as noise.
            wobble = 1.0 + 0.02 * math.sin(2 * math.pi * 0.7 * n / rate)
            frames += struct.pack(
                "<h", int(peak * math.sin(2 * math.pi * hz * wobble * n / rate))
            )
        w.writeframes(bytes(frames))
    return path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="/tmp/bot_audio.wav")
    p.add_argument("--seconds", type=float, default=10.0)
    p.add_argument("--hz", type=float, default=220.0)
    a = p.parse_args()
    path = write_tone(a.out, seconds=a.seconds, hz=a.hz)
    import os

    print(
        f"wrote {path} ({os.path.getsize(path) / 1e6:.2f} MB, ~{a.seconds:.0f}s loop)"
    )


if __name__ == "__main__":
    main()
