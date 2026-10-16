"""Hand-drawn ink engine for the vector pipeline.

What makes vector art read as "computer art" is the uniform stroke: one width,
no drift, perfect round caps. A real pen line breathes and wanders. Every
stroke produced here is therefore not a stroke at all — it is a filled
outline whose centreline drifts sideways and whose width varies along the
path, with the ends tapered like a pen lifting off the paper.

The noise is seeded from a stable string (``zlib.crc32``, not ``hash()``, which
is randomised per process). That matters for two reasons:

* a given scene renders byte-identically every time, so the three content-hash
  caches in ``build.py`` keep working;
* a character's look is reproducible across machines and across re-renders,
  which is what makes an art asset reviewable in the first place.
"""
from __future__ import annotations

import math
import zlib
from typing import Iterable, Sequence

Point = tuple[float, float]

__all__ = [
    "Pen",
    "seed_of",
    "cubic",
    "quad",
    "chain",
    "ellipse_points",
    "circle_points",
    "normals_of",
    "wobble",
    "poly_path",
    "fill_shape",
    "ink",
    "ink_loop",
]


def seed_of(name: str) -> int:
    """Stable across processes, unlike ``hash()``."""
    return zlib.crc32(name.encode("utf-8")) & 0xFFFFFFFF


class Pen:
    """Smooth pseudo-noise: a few sine harmonics with a seeded phase."""

    def __init__(self, seed: int, harmonics: int = 3) -> None:
        state = seed or 1
        parts = []
        for _ in range(harmonics):
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            k = 1 + (state >> 16) % 3
            phase = ((state >> 5) % 10000) / 10000.0 * math.tau
            amp = 0.55 + ((state >> 11) % 1000) / 1000.0 * 0.45
            parts.append((k, phase, amp))
        self.parts = parts
        self.total = sum(amp for _, _, amp in parts)

    def loop(self, t: float) -> float:
        """Periodic on [0, 1) — for closed contours that must not show a seam."""
        return sum(a * math.sin(math.tau * k * t + p) for k, p, a in self.parts) / self.total

    def drift(self, t: float) -> float:
        """Non-repeating wander along an open stroke."""
        return sum(a * math.sin(math.tau * k * t * 0.73 + p) for k, p, a in self.parts) / self.total


# --- geometry ---------------------------------------------------------------

def cubic(p0: Point, p1: Point, p2: Point, p3: Point, steps: int = 24) -> list[Point]:
    out = []
    for i in range(steps + 1):
        t = i / steps
        m = 1 - t
        out.append((
            m ** 3 * p0[0] + 3 * m * m * t * p1[0] + 3 * m * t * t * p2[0] + t ** 3 * p3[0],
            m ** 3 * p0[1] + 3 * m * m * t * p1[1] + 3 * m * t * t * p2[1] + t ** 3 * p3[1],
        ))
    return out


def quad(p0: Point, p1: Point, p2: Point, steps: int = 20) -> list[Point]:
    out = []
    for i in range(steps + 1):
        t = i / steps
        m = 1 - t
        out.append((
            m * m * p0[0] + 2 * m * t * p1[0] + t * t * p2[0],
            m * m * p0[1] + 2 * m * t * p1[1] + t * t * p2[1],
        ))
    return out


def chain(*segments: Sequence[Point]) -> list[Point]:
    """Join control tuples into one polyline: 2 = line, 3 = quadratic, 4 = cubic."""
    pts: list[Point] = []
    for seg in segments:
        if len(seg) == 2:
            piece = [tuple(seg[0]), tuple(seg[1])]
        elif len(seg) == 3:
            piece = quad(*seg)
        elif len(seg) == 4:
            piece = cubic(*seg)
        else:
            raise ValueError(
                f"chain() needs segments of 2, 3 or 4 points, got {len(seg)}: {seg!r}"
            )
        pts.extend(piece if not pts else piece[1:])
    return pts


def ellipse_points(cx: float, cy: float, rx: float, ry: float, steps: int = 40) -> list[Point]:
    return [(cx + rx * math.cos(math.tau * i / steps), cy + ry * math.sin(math.tau * i / steps))
            for i in range(steps + 1)]


def circle_points(cx: float, cy: float, r: float, steps: int = 34) -> list[Point]:
    return ellipse_points(cx, cy, r, r, steps)


def normals_of(pts: Sequence[Point], closed: bool) -> list[Point]:
    n = len(pts)
    out = []
    for i in range(n):
        if closed:
            a, b = pts[(i - 1) % n], pts[(i + 1) % n]
        elif i == 0:
            a, b = pts[0], pts[1]
        elif i == n - 1:
            a, b = pts[-2], pts[-1]
        else:
            a, b = pts[i - 1], pts[i + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        out.append((-dy / length, dx / length))
    return out


def wobble(pts: Sequence[Point], name: str, amp: float = 1.8, closed: bool = False) -> list[Point]:
    """Displace a polyline sideways with seeded noise."""
    pen = Pen(seed_of(name + ":wobble"))
    normals = normals_of(pts, closed)
    n = len(pts)
    out = []
    for i, (x, y) in enumerate(pts):
        t = i / n if closed else i / (n - 1)
        offset = (pen.loop(t) if closed else pen.drift(t)) * amp
        out.append((x + normals[i][0] * offset, y + normals[i][1] * offset))
    return out


# --- output -----------------------------------------------------------------

def poly_path(pts: Iterable[Point]) -> str:
    pts = list(pts)
    d = [f"M{pts[0][0]:.1f} {pts[0][1]:.1f}"]
    d += [f"L{p[0]:.1f} {p[1]:.1f}" for p in pts[1:]]
    d.append("Z")
    return " ".join(d)


def fill_shape(pts: Sequence[Point], colour: str) -> str:
    return f'<path d="{poly_path(pts)}" fill="{colour}"/>'


def ink(pts: Sequence[Point], width: float, name: str, *,
        taper: float = 0.30, jitter: float = 0.30, colour: str = "#141414") -> str:
    """An open press stroke: filled ribbon, breathing width, tapered ends."""
    pen = Pen(seed_of(name + ":width"))
    n = len(pts)
    normals = normals_of(pts, False)
    half = []
    for i in range(n):
        t = i / (n - 1)
        envelope = 1.0 if taper <= 0 else max(0.10, min(1.0, t / taper, (1 - t) / taper)) ** 0.55
        half.append(max(0.55, width / 2 * envelope * (1.0 + jitter * pen.drift(t * 1.7))))
    left = [(pts[i][0] + normals[i][0] * half[i], pts[i][1] + normals[i][1] * half[i]) for i in range(n)]
    right = [(pts[i][0] - normals[i][0] * half[i], pts[i][1] - normals[i][1] * half[i]) for i in range(n)]

    def f(p: Point) -> str:
        return f"{p[0]:.1f} {p[1]:.1f}"

    d = [f"M{f(left[0])}"]
    d += [f"L{f(p)}" for p in left[1:]]
    d.append(f"A{half[-1]:.1f} {half[-1]:.1f} 0 0 1 {f(right[-1])}")
    d += [f"L{f(p)}" for p in reversed(right[:-1])]
    d.append(f"A{half[0]:.1f} {half[0]:.1f} 0 0 1 {f(left[0])}")
    d.append("Z")
    return f'<path d="{" ".join(d)}" fill="{colour}"/>'


def ink_loop(pts: Sequence[Point], width: float, name: str, *,
             jitter: float = 0.24, colour: str = "#141414") -> str:
    """A closed contour stroke drawn as an annulus (even-odd rule).

    Offsetting a *closed* contour and walking each side round the loop would
    close two full outlines and flood the interior. Two offset rings with
    ``fill-rule="evenodd"`` leave the middle empty and the band inked.
    """
    pen = Pen(seed_of(name + ":loop"))
    n = len(pts)
    normals = normals_of(pts, True)
    half = [width / 2 * (1.0 + jitter * pen.loop(i / n)) for i in range(n)]
    left = [(pts[i][0] + normals[i][0] * half[i], pts[i][1] + normals[i][1] * half[i]) for i in range(n)]
    right = [(pts[i][0] - normals[i][0] * half[i], pts[i][1] - normals[i][1] * half[i]) for i in range(n)]
    return (f'<path d="{poly_path(left)} {poly_path(right)}" fill="{colour}" '
            f'fill-rule="evenodd"/>')
