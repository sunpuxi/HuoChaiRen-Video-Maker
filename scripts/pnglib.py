"""Minimal RGBA PNG reader/writer on the standard library alone.

The sprite tooling must run anywhere the renderer does, so it does not pull in
Pillow or numpy. Only 8-bit RGBA is supported, which is what the keyer emits.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _chunks(raw: bytes):
    pos = 8
    while pos < len(raw):
        (length,) = struct.unpack(">I", raw[pos:pos + 4])
        yield raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + length]
        pos += 12 + length


def read_rgba(path: Path) -> tuple[int, int, bytearray]:
    raw = Path(path).read_bytes()
    width = height = None
    idat = b""
    for kind, data in _chunks(raw):
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", data[:10])
            if depth != 8 or colour != 6:
                raise SystemExit(f"{path}: expected 8-bit RGBA, got depth={depth} colour={colour}")
        elif kind == b"IDAT":
            idat += data
    if width is None:
        raise SystemExit(f"{path}: no IHDR")
    return width, height, _unfilter(width, height, zlib.decompress(idat))


def _unfilter(width: int, height: int, data: bytes) -> bytearray:
    stride = width * 4
    out = bytearray(stride * height)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ftype = data[pos]
        pos += 1
        line = bytearray(data[pos:pos + stride])
        pos += stride
        if ftype:
            for i in range(stride):
                a = line[i - 4] if i >= 4 else 0
                b = prev[i]
                c = prev[i - 4] if i >= 4 else 0
                if ftype == 1:
                    line[i] = (line[i] + a) & 0xFF
                elif ftype == 2:
                    line[i] = (line[i] + b) & 0xFF
                elif ftype == 3:
                    line[i] = (line[i] + (a + b) // 2) & 0xFF
                else:
                    p = a + b - c
                    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                    pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    line[i] = (line[i] + pred) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


def write_rgba(path: Path, width: int, height: int, pixels: bytes) -> None:
    stride = width * 4
    raw = b"".join(b"\x00" + pixels[y * stride:(y + 1) * stride] for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    Path(path).write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b""))


def crop(pixels: bytearray, width: int, x0: int, y0: int, cw: int, ch: int) -> bytes:
    out = bytearray(cw * ch * 4)
    for y in range(ch):
        src = (y0 + y) * width * 4 + x0 * 4
        out[y * cw * 4:(y + 1) * cw * 4] = pixels[src:src + cw * 4]
    return bytes(out)


def alpha_bbox(pixels: bytearray, width: int, height: int,
               x0: int = 0, x1: int | None = None) -> tuple[int, int, int, int] | None:
    x1 = width if x1 is None else x1
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        row = y * width * 4
        for x in range(x0, x1):
            if pixels[row + x * 4 + 3] > 8:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return None
    return min_x, min_y, max_x, max_y
