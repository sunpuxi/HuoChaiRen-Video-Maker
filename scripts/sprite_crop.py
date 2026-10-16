#!/usr/bin/env python3
"""Trim a keyed sprite to its opaque bounds. Standard library only."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pnglib import alpha_bbox, crop, read_rgba, write_rgba  # noqa: E402


def main(src: str, dst: str) -> None:
    width, height, pixels = read_rgba(Path(src))
    box = alpha_bbox(pixels, width, height)
    if box is None:
        raise SystemExit(f"{src}: fully transparent")
    x0, y0, x1, y1 = box
    cw, ch = x1 - x0 + 1, y1 - y0 + 1
    write_rgba(Path(dst), cw, ch, crop(pixels, width, x0, y0, cw, ch))
    print(f"{dst}: {cw}x{ch} (was {width}x{height})")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
