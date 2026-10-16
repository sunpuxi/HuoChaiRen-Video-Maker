#!/usr/bin/env python3
"""Split a keyed multi-figure sheet into one PNG per figure.

The point of generating every pose in a single image is that the model keeps
the character consistent by itself. That only pays off if the figures can be
separated afterwards, so this finds the empty columns between them rather than
assuming an even grid — the model never spaces them evenly.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pnglib import alpha_bbox, crop, read_rgba, write_rgba  # noqa: E402

MIN_GAP = 6      # columns of transparency that count as a separator
MIN_WIDTH = 20   # ignore slivers from stray speckles


def figure_spans(pixels: bytearray, width: int, height: int) -> list[tuple[int, int]]:
    filled = [False] * width
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            if pixels[row + x * 4 + 3] > 8:
                filled[x] = True
    spans: list[tuple[int, int]] = []
    start = None
    gap = 0
    for x, on in enumerate(filled):
        if on:
            if start is None:
                start = x
            gap = 0
        elif start is not None:
            gap += 1
            if gap >= MIN_GAP:
                if x - gap - start + 1 >= MIN_WIDTH:
                    spans.append((start, x - gap))
                start = None
    if start is not None and width - start >= MIN_WIDTH:
        spans.append((start, width - 1))
    return spans


def main(src: str, out_dir: str, prefix: str) -> None:
    width, height, pixels = read_rgba(Path(src))
    spans = figure_spans(pixels, width, height)
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    for i, (x0, x1) in enumerate(spans, start=1):
        box = alpha_bbox(pixels, width, height, x0, x1 + 1)
        if box is None:
            continue
        bx0, by0, bx1, by1 = box
        cw, ch = bx1 - bx0 + 1, by1 - by0 + 1
        path = target / f"{prefix}{i}.png"
        write_rgba(path, cw, ch, crop(pixels, width, bx0, by0, cw, ch))
        print(f"{path.name}: {cw}x{ch}")
    print(f"{len(spans)} 个图形")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "fig")
