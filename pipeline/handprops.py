"""Hand-drawn versions of every prop, matching the ``pipeline.ink`` pen.

Props are drawn by the same engine as the characters, so a scene no longer
mixes a machine-perfect arrow with a hand-drawn person. Two rules from the
original module still hold and are load-bearing:

* **no text on the animation layer** — icons, arrows, crosses and stars are
  rasterised per frame without a system font scan, which is what keeps
  per-frame rendering affordable. Only the bubble and node carry labels, and
  those two already live on the static base layer.
* **constant on-screen pen weight** — an icon drawn at a larger scale gets
  proportionally thinner local widths, so a big sheep does not look bolder
  than a small one.
"""
from __future__ import annotations

import math
from html import escape
from typing import Any, Callable

from .ink import (
    chain,
    circle_points,
    ellipse_points,
    fill_shape,
    ink,
    ink_loop,
    poly_path,
    wobble,
)

ICON_BOX = 100.0
ICON_INK = 8.0
ICON_THIN = 5.0

INK = "#141414"


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _colour(value: Any, default: str) -> str:
    return escape(str(value), quote=True) if value is not None else default


def _seed(prop: dict[str, Any], name: str) -> str:
    """Stable per-prop seed: the same prop in the same place always draws alike."""
    x = _num(prop.get("x", prop.get("x1")), 0.0)
    y = _num(prop.get("y", prop.get("y1")), 0.0)
    return f"prop:{name}:{x:.0f}:{y:.0f}"


def _wobbled(pts, prop, name, amp=1.6, closed=False):
    return wobble(pts, _seed(prop, name), amp, closed)


def _inked(pts, width, prop, name, **kw):
    return ink(_wobbled(pts, prop, name), width, _seed(prop, name), **kw)


def _polygon(points, prop, name, amp=1.4):
    """Straight-edged shape. ``chain()`` would bow a 3-point input into a curve,
    which silently turns every arrowhead into a lens."""
    return _wobbled([tuple(p) for p in points], prop, name, amp, True)


# --- arrow ------------------------------------------------------------------

def arrow(prop: dict[str, Any]) -> str:
    x1, y1 = _num(prop.get("x1"), 300), _num(prop.get("y1"), 350)
    x2, y2 = _num(prop.get("x2"), 800), _num(prop.get("y2"), 350)
    grow = _num(prop.get("_grow"), 1.0)
    x2, y2 = x1 + (x2 - x1) * grow, y1 + (y2 - y1) * grow
    colour = _colour(prop.get("color"), "#45A95A")
    width = _num(prop.get("width"), 18)
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    head = min(length * 0.30, width * 2.6)
    half = width * 1.15
    # stop the shaft short so the hand-drawn head can sit on top of it
    sx, sy = x2 - ux * head * 0.75, y2 - uy * head * 0.75
    tip = chain(((x1, y1), (x1 + dx * 0.34, y1 + dy * 0.34),
                 (x1 + dx * 0.67, y1 + dy * 0.67), (sx, sy)))
    bx, by = x2 - ux * head, y2 - uy * head
    wing = ((bx - uy * half, by + ux * half),
            (bx + uy * half, by - ux * half),
            (x2, y2))
    return (_inked(tip, width, prop, "arrow:shaft", taper=0.16, colour=colour)
            + fill_shape(_polygon(wing, prop, "arrow:head"), colour))


# --- cross ------------------------------------------------------------------

def cross(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 850), _num(prop.get("y"), 300)
    size = _num(prop.get("size"), 180) / 2
    colour = _colour(prop.get("color"), "#D9433F")
    width = _num(prop.get("width"), 38)
    a = _inked(chain(((x - size, y - size), (x - size * 0.3, y - size * 0.3),
                      (x + size * 0.3, y + size * 0.3), (x + size, y + size))),
               width, prop, "cross:a", taper=0.20, colour=colour)
    b = _inked(chain(((x + size, y - size), (x + size * 0.3, y - size * 0.3),
                      (x - size * 0.3, y + size * 0.3), (x - size, y + size))),
               width, prop, "cross:b", taper=0.20, colour=colour)
    return f'<g filter="url(#shadow)">{a}{b}</g>'


# --- arc / star -------------------------------------------------------------

def arc(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 150), _num(prop.get("y"), 120)
    width = _num(prop.get("width"), 180)
    colour = _colour(prop.get("color"), "#F5B632")
    return _inked(chain(((x - width / 2, y), (x, y - 62), (x + width / 2, y))),
                  18, prop, "arc", taper=0.28, colour=colour)


def star(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 160), _num(prop.get("y"), 190)
    scale = _num(prop.get("scale"), 1)
    colour = _colour(prop.get("color"), "#EAF6FF")
    spikes = [(0, -42), (12, -13), (43, -13), (18, 6), (27, 37),
              (0, 19), (-27, 37), (-18, 6), (-43, -13), (-12, -13)]
    pts = wobble([(x + px * scale, y + py * scale) for px, py in spikes],
                 _seed(prop, "star"), 1.8 * scale, closed=True)
    return fill_shape(pts, colour)


# --- bubble / node (static layer: these carry text) -------------------------

def bubble(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 640), _num(prop.get("y"), 250)
    w, h = _num(prop.get("width"), 250) / 2, _num(prop.get("height"), 130) / 2
    fill = _colour(prop.get("fill"), "#E9F4DD")
    label = escape(str(prop.get("text", "…")))
    body = chain(
        ((-w, 0), (-w, -h), (-w * 0.4, -h * 1.12), (0, -h * 1.12)),
        ((0, -h * 1.12), (w * 0.4, -h * 1.12), (w, -h), (w, 0)),
        ((w, 0), (w, h * 0.42), (30, h), (14, h)),
        ((14, h), (-2, h), (-20, h * 1.5), (-30, h * 2.1)),
        ((-30, h * 2.1), (-26, h * 1.3), (-w * 0.55, h * 0.62), (-w, h * 0.42)),
        ((-w, h * 0.42), (-w, h * 0.2), (-w, h * 0.1), (-w, 0)),
    )
    pts = wobble(body, _seed(prop, "bubble"), 2.4, closed=True)
    return (f'<g transform="translate({x:g} {y:g})">'
            f'<path d="{poly_path(pts)}" fill="{fill}"/>'
            f'{ink_loop(pts, 10, _seed(prop, "bubble"))}'
            f'<text x="0" y="12" text-anchor="middle" class="label">{label}</text>'
            f'</g>')


def node(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 250), _num(prop.get("y"), 300)
    radius = _num(prop.get("radius"), 76)
    label = escape(str(prop.get("text", "身份")))
    fill = _colour(prop.get("fill"), "#FFFDF7")
    pts = wobble(circle_points(x, y, radius, 44), _seed(prop, "node"), 2.2, closed=True)
    return (f'<path d="{poly_path(pts)}" fill="{fill}"/>'
            f'{ink_loop(pts, 9, _seed(prop, "node"))}'
            f'<text x="{x:g}" y="{y + 12:g}" text-anchor="middle" '
            f'class="small-label">{label}</text>')


def mirror(prop: dict[str, Any]) -> str:
    """A mirror showing the reflection: the figure inside is the same pen."""
    from . import handdraw

    x, y = _num(prop.get("x"), 880), _num(prop.get("y"), 350)
    w, h = _num(prop.get("width"), 250) / 2, _num(prop.get("height"), 360) / 2
    shirt = _colour(prop.get("shirt"), "#F0A454")
    frame = chain(
        ((-w, -h + w * 0.6), (-w, -h * 0.4), (-w * 0.55, -h), (0, -h)),
        ((0, -h), (w * 0.55, -h), (w, -h * 0.4), (w, -h + w * 0.6)),
        ((w, -h + w * 0.6), (w, h * 0.5), (w * 0.5, h), (0, h)),
        ((0, h), (-w * 0.5, h), (-w, h * 0.5), (-w, -h + w * 0.6)),
    )
    pts = wobble(frame, _seed(prop, "mirror"), 2.0, closed=True)
    glass = (f'<path d="{poly_path(pts)}" fill="#D9F1F4"/>'
             f'{ink_loop(pts, 11, _seed(prop, "mirror"))}')
    water = _inked(chain(((-w + 18, h - 46), (0, h - 8), (w - 18, h - 46))),
                   13, prop, "mirror:water", taper=0.30, colour="#87C8CE")
    bust = handdraw.figure({"expression": "smile", "shirt": shirt, "outfit": "none",
                            "hair": "combover", "nose": "hook"},
                           pose="stand", order=90)
    return (f'<g transform="translate({x:g} {y:g})">{glass}{water}'
            f'<g transform="translate(0 -6) scale(0.30)">{bust}</g></g>')


# --- exchange ---------------------------------------------------------------

def exchange(prop: dict[str, Any]) -> str:
    x1, y1 = _num(prop.get("x1"), 430), _num(prop.get("y1"), 300)
    x2, y2 = _num(prop.get("x2"), 850), _num(prop.get("y2"), 300)
    colour = _colour(prop.get("color"), "#45A95A")
    width = _num(prop.get("width"), 14)
    grow = _num(prop.get("_grow"), 1.0)
    x2, y2 = x1 + (x2 - x1) * grow, y1 + (y2 - y1) * grow
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    head = min(length / 2.6, width * 2.2)
    half = width * 0.95

    def tip(px: float, py: float, sign: float) -> str:
        bx, by = px - sign * head * ux, py - sign * head * uy
        wing = ((bx - uy * half, by + ux * half),
                (bx + uy * half, by - ux * half),
                (px, py))
        return fill_shape(_polygon(wing, prop, f"exchange:{px:.0f}", 1.2), colour)

    shaft = _inked(chain(((x1 + head * ux, y1 + head * uy),
                          ((x1 + x2) / 2, (y1 + y2) / 2),
                          (x2 - head * ux, y2 - head * uy))),
                   width, prop, "exchange:shaft", taper=0.18, colour=colour)
    return shaft + tip(x1, y1, 1.0) + tip(x2, y2, -1.0)


# --- icons ------------------------------------------------------------------
# Geometry stays inside the 0..100 box so ``size`` keeps its old meaning.

def _ibox(prop: dict[str, Any]) -> tuple[float, str]:
    """Local pen widths that stay constant on screen, and a seed prefix."""
    scale = _num(prop.get("size"), 130) / ICON_BOX * _num(prop.get("scale"), 1)
    return scale, _seed(prop, "icon:" + str(prop.get("name", "")))


def _icon_pen(prop: dict[str, Any]) -> tuple[float, float]:
    scale, _ = _ibox(prop)
    return ICON_INK / scale, ICON_THIN / scale


def icon_sheep(prop: dict[str, Any]) -> str:
    ink_w, thin_w = _icon_pen(prop)
    prefix = _seed(prop, "icon:sheep")
    body = chain(
        ((-28, 14), (-44, 10), (-42, -6), (-34, -20)),
        ((-34, -20), (-16, -34), (6, -30), (14, -18)),
        ((14, -18), (32, -30), (44, -14), (36, 2)),
        ((36, 2), (26, 16), (4, 18), (-12, 18)),
        ((-12, 18), (-20, 18), (-26, 16), (-28, 14)),
    )
    bw = wobble(body, prefix + ":body", 1.8, closed=True)
    head = wobble(ellipse_points(-44, -12, 15, 14), prefix + ":head", 1.5, closed=True)
    out = [f'<path d="{poly_path(bw)}" fill="#F6F3EC"/>', ink_loop(bw, ink_w * 1.25, prefix + ":body"),
           f'<path d="{poly_path(head)}" fill="#F6F3EC"/>', ink_loop(head, ink_w, prefix + ":head")]
    out.append(ink(wobble(chain(((-52, -22), (-64, -32), (-58, -46))), prefix + ":horn", 1.2),
                   thin_w * 1.6, prefix + ":horn", taper=0.30))
    out.append(ink(wobble(chain(((-34, -24), (-28, -34), (-18, -36))), prefix + ":ear", 1.2),
                   thin_w * 1.6, prefix + ":ear", taper=0.30))
    for i, lx in enumerate((-22, -6, 12, 26)):
        out.append(ink(wobble(chain(((lx, 16), (lx - 1, 30), (lx, 44))), f"{prefix}:leg{i}", 1.0),
                       thin_w * 2.0, f"{prefix}:leg{i}", taper=0.18))
    out.append(f'<circle cx="-48" cy="-13" r="2.8" fill="{INK}"/>')
    return "".join(out)


def icon_rice(prop: dict[str, Any]) -> str:
    ink_w, _ = _icon_pen(prop)
    prefix = _seed(prop, "icon:rice")
    mound = wobble(chain(((-31, 3), (0, -46), (31, 3))), prefix + ":mound", 1.8, closed=True)
    bowl = wobble(chain(((-44, 0), (-42, 22), (-22, 40), (0, 42)),
                        ((0, 42), (22, 40), (42, 22), (44, 0)),
                        ((44, 0), (14, 6), (-14, 6), (-44, 0))),
                  prefix + ":bowl", 1.8, closed=True)
    out = [f'<path d="{poly_path(mound)}" fill="#F7E7C8"/>', ink_loop(mound, ink_w * 1.2, prefix + ":mound"),
           f'<path d="{poly_path(bowl)}" fill="#FFFFFF"/>', ink_loop(bowl, ink_w * 1.2, prefix + ":bowl")]
    for i, (gx, gy) in enumerate(((-11, -14), (2, -21), (14, -12))):
        out.append(f'<ellipse cx="{gx}" cy="{gy}" rx="3.4" ry="3.0" fill="#FFFFFF"/>')
    return "".join(out)


def icon_fish(prop: dict[str, Any]) -> str:
    ink_w, thin_w = _icon_pen(prop)
    prefix = _seed(prop, "icon:fish")
    body = wobble(chain(((-34, 0), (-14, -24), (10, -22), (24, -14)),
                        ((24, -14), (36, -10), (44, -3), (46, 0)),
                        ((46, 0), (36, 8), (22, 16), (8, 18)),
                        ((8, 18), (-14, 20), (-30, 10), (-34, 0))),
                  prefix + ":body", 1.7, closed=True)
    tail = wobble(chain(((-32, 0), (-46, -18), (-60, -22), (-58, -4)),
                        ((-58, -4), (-56, 10), (-46, 20), (-32, 0))),
                  prefix + ":tail", 1.5, closed=True)
    out = [f'<path d="{poly_path(tail)}" fill="#BBDCEC"/>', ink_loop(tail, ink_w * 1.1, prefix + ":tail"),
           f'<path d="{poly_path(body)}" fill="#BBDCEC"/>', ink_loop(body, ink_w * 1.2, prefix + ":body")]
    out.append(ink(wobble(chain(((4, -14), (10, 0), (4, 14))), prefix + ":gill", 1.1),
                   thin_w * 1.6, prefix + ":gill", taper=0.28))
    out.append(f'<circle cx="22" cy="-5" r="3.6" fill="{INK}"/>')
    return "".join(out)


def icon_shoes(prop: dict[str, Any]) -> str:
    ink_w, thin_w = _icon_pen(prop)
    prefix = _seed(prop, "icon:shoes")
    upper = wobble(chain(((-42, 20), (-38, -6), (-32, -18), (-20, -18)),
                         ((-20, -18), (-8, -18), (-2, -6), (8, 10)),
                         ((8, 10), (16, 18), (32, 20), (44, 20))),
                  prefix + ":upper", 1.7, closed=True)
    sole = wobble(chain(((-46, 20), (-46, 28), (-38, 34), (-24, 34)),
                        ((-24, 34), (0, 35), (24, 34), (38, 34)),
                        ((38, 34), (46, 28), (46, 24), (46, 20)),
                        ((46, 20), (0, 21), (-46, 21), (-46, 20))),
                  prefix + ":sole", 1.7, closed=True)
    out = [f'<path d="{poly_path(upper)}" fill="#E7DACB"/>', ink_loop(upper, ink_w * 1.2, prefix + ":upper"),
           f'<path d="{poly_path(sole)}" fill="#E7DACB"/>', ink_loop(sole, ink_w * 1.2, prefix + ":sole")]
    out.append(ink(wobble(chain(((-28, -16), (-16, -4))), prefix + ":lace0", 1.0),
                   thin_w * 1.6, prefix + ":lace0", taper=0.28))
    out.append(ink(wobble(chain(((-18, -18), (-6, -6))), prefix + ":lace1", 1.0),
                   thin_w * 1.6, prefix + ":lace1", taper=0.28))
    return "".join(out)


ICON_RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "sheep": icon_sheep,
    "rice": icon_rice,
    "fish": icon_fish,
    "shoes": icon_shoes,
}


def icon(prop: dict[str, Any]) -> str:
    renderer = ICON_RENDERERS.get(str(prop.get("name", "")))
    if renderer is None:
        return ""
    x, y = _num(prop.get("x"), 640), _num(prop.get("y"), 300)
    scale, _ = _ibox(prop)
    rotation = _num(prop.get("rotation"), 0)
    return (f'<g transform="translate({x:g} {y:g}) rotate({rotation:g}) scale({scale:g})">'
            f'{renderer(prop)}</g>')


# --- backdrop layers --------------------------------------------------------

def ground_line(x0: float, x1: float, y: float, colour: str) -> str:
    prop = {"x": x0, "y": y}
    return _inked(chain(((x0, y), ((x0 + x1) / 2, y - 12), (x1, y))),
                  10, prop, "ground", taper=0.12, colour=colour, jitter=0.22)


def title_stroke(x0: float, x1: float, y: float, colour: str) -> str:
    prop = {"x": x0, "y": y}
    return _inked(chain(((x0, y), ((x0 + x1) / 2, y - 10), (x1, y))),
                  14, prop, "titlestroke", taper=0.22, colour=colour)


def paper_shape(kind: str, tint: str) -> str:
    """The same four tone-on-tone placements, with a hand-cut edge."""
    prop = {"x": 0, "y": 0}
    if kind == "circle_top_right":
        pts = ellipse_points(1220, 110, 260, 260, 52)
    elif kind == "circle_bottom_left":
        pts = ellipse_points(60, 676, 225, 225, 52)
    elif kind == "circle_top_left":
        pts = ellipse_points(80, 96, 235, 235, 52)
    else:
        pts = ([(-80.0, 720.0), (-80.0, 598.0)]
               + chain(((-80, 598), (640, 546), (1360, 598)))
               + [(1360.0, 720.0)])
    return fill_shape(wobble(pts, _seed(prop, "paper:" + kind), 5.0, closed=True), tint)


PROP_RENDERERS = {
    "arrow": arrow,
    "cross": cross,
    "bubble": bubble,
    "node": node,
    "mirror": mirror,
    "arc": arc,
    "star": star,
    "icon": icon,
    "exchange": exchange,
}
