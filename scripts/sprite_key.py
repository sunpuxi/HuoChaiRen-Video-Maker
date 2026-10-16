#!/usr/bin/env python3
"""Key a green screen by RGB greenness, not by YUV distance.

ffmpeg's ``chromakey`` works in YUV, and against a *gradient* screen it has a
cliff: one similarity setting leaves the whole background, the next erases the
figure too. Greenness in RGB — ``G - max(R, B)`` — separates the two cleanly,
because a cartoon character simply has no strongly green pixels.

Reads and writes RGBA PNG via ``pnglib``; decoding the source JPEG is the only
job left to ffmpeg, which is what ``sprite_key.sh`` does.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pnglib import read_rgba, write_rgba  # noqa: E402

LOW = 26.0    # greenness at or below this is certainly the figure
HIGH = 48.0   # at or above this is certainly screen
DESPILL = 8   # how far the green channel may exceed max(R, B) on an edge


def key(src: str, dst: str) -> tuple[int, int, float]:
    width, height, px = read_rgba(Path(src))
    opaque = 0
    for i in range(0, len(px), 4):
        r, g, b = px[i], px[i + 1], px[i + 2]
        greenness = g - (r if r > b else b)
        if greenness >= HIGH:
            px[i + 3] = 0
            continue
        if greenness > LOW:
            # soft edge: fade out across the band instead of a hard cut
            px[i + 3] = int(255 * (HIGH - greenness) / (HIGH - LOW))
        else:
            opaque += 1
        ceiling = (r if r > b else b) + DESPILL
        if g > ceiling:
            px[i + 1] = ceiling
    write_rgba(Path(dst), width, height, bytes(px))
    return width, height, opaque / (width * height)


if __name__ == "__main__":
    w, h, ratio = key(sys.argv[1], sys.argv[2])
    print(f"{sys.argv[2]}: {w}x{h}  figure {ratio * 100:.1f}%")
