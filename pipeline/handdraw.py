"""Parameterised hand-drawn characters.

One drawing engine, many characters. A character is a flat dict of parameters —
hair, brow, nose, mouth, outfit, colours — layered on geometry every figure
shares. That matters beyond looks: the parts are drawn by separate functions
(limbs / torso / head), which is what phase two of the art plan needs in order
to drive them independently.

This module is opt-in. ``svg.character_svg`` keeps the original line figure as
the default, so existing storyboards and every cached render are untouched.
"""
from __future__ import annotations

import math
from typing import Any, Optional, Sequence

from .animate import character_state
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

# --- shared geometry (origin = head centre, feet near y=385) ----------------
HW, HH = 130.0, 86.0
NECK_TOP, NECK_BOT = 84.0, 142.0
TORSO_TOP, TORSO_BOT = 112.0, 288.0
LEG_HIP_Y = 268.0
LEG_FOOT_Y = 366.0

INK = "#141414"
SKIN = "#FBF7EF"
FUR_DARK = "#8A5C31"

STYLES = ("line", "hand", "sprite")
DEFAULT_STYLE = "line"

HAIR_STYLES = ("spikes", "bowl", "ring", "flat", "afro", "combover", "none")
BROW_STYLES = ("worried", "angry", "raised", "flat", "unibrow")
NOSE_STYLES = ("hook", "potato", "pointy", "tiny")
MOUTH_STYLES = ("flat", "grin", "squiggle", "open", "frown")
OUTFITS = ("fur", "shirt", "tie", "overalls", "robe", "none")
EARS = ("normal", "big", "none")

# Expression drives brow and mouth unless the character overrides them.
EXPRESSION_MAP = {
    "neutral": ("flat", "flat"),
    "smile": ("raised", "grin"),
    "confused": ("worried", "squiggle"),
    "surprised": ("raised", "open"),
    "sad": ("worried", "frown"),
    "angry": ("angry", "frown"),
    "worried": ("worried", "frown"),
}

DEFAULTS: dict[str, Any] = {
    "skin": SKIN,
    "hair_colour": INK,
    "shirt": "#EFE8DA",
    "accent": "#E8623C",
    "hair": "spikes",
    "brow": None,
    "nose": "hook",
    "mouth": None,
    "outfit": "fur",
    "ears": "normal",
}

# Per-style metrics the backdrop needs: where the feet land, and how wide the
# contact shadow should be. Keeps the ground line under the figure without
# forcing the hand-drawn proportions to imitate the line figure's numbers.
METRICS: dict[str, dict[str, float]] = {
    "line": {"foot": 452.0, "shadow_rx": 96.0, "shadow_ry": 15.0},
    "hand": {"foot": 399.0, "shadow_rx": 118.0, "shadow_ry": 17.0},
    # sprite figures normalise to the line figure's footing, so they reuse it
    "sprite": {"foot": 452.0, "shadow_rx": 104.0, "shadow_ry": 16.0},
}


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def art_style_for(character: dict[str, Any], project: Optional[dict[str, Any]]) -> str:
    """Character override, then project default, then the original line figure."""
    for source in (character.get("style"), (project or {}).get("art_style")):
        if source in STYLES:
            return str(source)
    return DEFAULT_STYLE


def metrics_for(style: str) -> dict[str, float]:
    return METRICS.get(style, METRICS[DEFAULT_STYLE])


def spec_of(character: dict[str, Any]) -> dict[str, Any]:
    """Resolve a character dict into a complete drawing spec."""
    spec = dict(DEFAULTS)
    for key in DEFAULTS:
        value = character.get(key)
        if value is not None:
            spec[key] = value
    expression = str(character.get("expression", "neutral"))
    default_brow, default_mouth = EXPRESSION_MAP.get(expression, EXPRESSION_MAP["neutral"])
    if spec["brow"] is None:
        spec["brow"] = default_brow
    if spec["mouth"] is None:
        spec["mouth"] = default_mouth
    if spec["hair"] not in HAIR_STYLES:
        spec["hair"] = DEFAULTS["hair"]
    if spec["brow"] not in BROW_STYLES:
        spec["brow"] = default_brow
    if spec["nose"] not in NOSE_STYLES:
        spec["nose"] = DEFAULTS["nose"]
    if spec["mouth"] not in MOUTH_STYLES:
        spec["mouth"] = default_mouth
    if spec["outfit"] not in OUTFITS:
        spec["outfit"] = DEFAULTS["outfit"]
    if spec["ears"] not in EARS:
        spec["ears"] = DEFAULTS["ears"]
    return spec


# --- poses ------------------------------------------------------------------
# An arm is (shoulder, elbow, hand); a leg is (hip, knee, ankle). Pose names
# match the existing storyboard vocabulary so a scene can switch style freely.

ARM_LEFT: dict[str, tuple[tuple[float, float], ...]] = {
    "stand": ((-76, 152), (-106, 212), (-114, 264)),
    "look_down": ((-76, 152), (-100, 214), (-106, 270)),
    "point": ((-76, 152), (-106, 212), (-114, 264)),
    "arms_up": ((-76, 152), (-126, 96), (-148, 34)),
    "think": ((-76, 152), (-106, 212), (-114, 264)),
    "walk": ((-76, 152), (-106, 212), (-114, 264)),
}
ARM_RIGHT: dict[str, tuple[tuple[float, float], ...]] = {
    "stand": ((76, 152), (106, 212), (114, 264)),
    "look_down": ((76, 152), (100, 214), (106, 270)),
    "point": ((76, 152), (136, 168), (204, 156)),
    "arms_up": ((76, 152), (126, 96), (148, 34)),
    "think": ((76, 152), (114, 118), (58, 62)),
    "walk": ((76, 152), (106, 212), (114, 264)),
}
LEGS_STAND: list[tuple[tuple[float, float], ...]] = [
    ((-40, LEG_HIP_Y), (-58, 322), (-70, LEG_FOOT_Y)),
    ((42, LEG_HIP_Y), (60, 322), (74, LEG_FOOT_Y)),
]


def walk_arms(phase: float) -> list[tuple[tuple[float, float], ...]]:
    """Counter-swinging arms; two steps per cycle."""
    out = []
    for side in (-1, 1):
        swing = math.sin(phase) * side
        shoulder = (76.0 * side, 152.0)
        hand = (114.0 * side - 36.0 * swing, 264.0 - 20.0 * abs(swing))
        elbow = ((shoulder[0] + hand[0]) / 2 + 16.0 * side, (shoulder[1] + hand[1]) / 2)
        out.append((shoulder, elbow, hand))
    return out


def walk_legs(phase: float) -> list[tuple[tuple[float, float], ...]]:
    """Legs mid-stride, with a knee lift on the forward leg."""
    out = []
    for side in (-1, 1):
        swing = math.sin(phase) * side
        lift = max(0.0, math.cos(phase) * side) * 32.0
        hip = (40.0 * side, LEG_HIP_Y)
        ankle = (70.0 * side + 44.0 * swing, LEG_FOOT_Y - lift)
        knee = ((hip[0] + ankle[0]) / 2 + 8.0 * side, (hip[1] + ankle[1]) / 2 - 6.0)
        out.append((hip, knee, ankle))
    return out


# --- head -------------------------------------------------------------------

def head_contour() -> list[tuple[float, float]]:
    """A bean: domed crown, wide at the cheekbones, tapering to a rounded chin."""
    return chain(
        ((0, -HH), (72, -HH), (108, -HH * 0.64), (HW, -HH * 0.10)),
        ((HW, -HH * 0.10), (HW, HH * 0.52), (90, HH * 1.00), (40, HH * 1.14)),
        ((40, HH * 1.14), (20, HH * 1.19), (-20, HH * 1.19), (-40, HH * 1.14)),
        ((-40, HH * 1.14), (-90, HH * 1.00), (-HW, HH * 0.52), (-HW, -HH * 0.10)),
        ((-HW, -HH * 0.10), (-108, -HH * 0.64), (-72, -HH), (0, -HH)),
    )


def _hair(spec: dict[str, Any], prefix: str) -> str:
    style = spec["hair"]
    colour = spec["hair_colour"]
    if style == "spikes":
        return "".join(
            ink(wobble(chain(seg), f"{prefix}:hair{i}", 2.0), 9, f"{prefix}:hair{i}",
                taper=0.32, colour=colour)
            for i, seg in enumerate([
                ((-46, -80), (-62, -118), (-72, -140), (-78, -156)),
                ((-12, -86), (-20, -132), (-24, -156), (-26, -176)),
                ((22, -84), (34, -126), (42, -148), (48, -166)),
                ((52, -74), (72, -104), (84, -120), (92, -130)),
            ]))
    if style == "bowl":
        pts = wobble(chain(
            ((-110, -10), (-114, -72), (-64, -100), (0, -100)),
            ((0, -100), (64, -100), (114, -72), (110, -10)),
            ((110, -10), (100, -46), (64, -58), (34, -52)),
            ((34, -52), (12, -48), (-14, -48), (-36, -52)),
            ((-36, -52), (-70, -58), (-102, -44), (-110, -10)),
        ), f"{prefix}:hair", 1.5, closed=True)
        return fill_shape(pts, colour)
    if style in ("ring", "combover"):
        out = []
        for side in (-1, 1):
            pts = wobble(chain(
                ((side * 104, -12), (side * 108, 30), (side * 90, 66), (side * 58, 80)),
                ((side * 58, 80), (side * 72, 46), (side * 76, 16), (side * 72, -12)),
            ), f"{prefix}:side{side}", 1.4, closed=True)
            out.append(fill_shape(pts, colour))
        if style == "combover":
            pts = wobble(chain(
                ((-86, -50), (-58, -80), (-6, -86), (30, -70)),
                ((30, -70), (56, -58), (70, -42), (74, -26)),
                ((74, -26), (62, -46), (40, -58), (12, -60)),
                ((12, -60), (-22, -62), (-58, -52), (-86, -42)),
            ), f"{prefix}:comb", 1.6, closed=True)
            out.append(fill_shape(pts, colour))
        else:
            for i, seg in enumerate([
                ((-34, -80), (-24, -94), (-10, -98), (2, -92)),
                ((4, -88), (14, -98), (26, -98), (34, -90)),
            ]):
                out.append(ink(wobble(chain(seg), f"{prefix}:wisp{i}", 1.2), 12,
                               f"{prefix}:wisp{i}", taper=0.30, colour=colour))
        return "".join(out)
    if style == "flat":
        pts = wobble(chain(
            ((-104, -18), (-104, -54), (-60, -76), (0, -76)),
            ((0, -76), (60, -76), (104, -54), (104, -18)),
            ((104, -18), (80, -40), (40, -50), (0, -50)),
            ((0, -50), (-40, -50), (-80, -40), (-104, -18)),
        ), f"{prefix}:hair", 1.4, closed=True)
        return fill_shape(pts, colour)
    if style == "afro":
        pts = wobble(ellipse_points(0, -104, 112, 78), f"{prefix}:hair", 9.0, closed=True)
        return fill_shape(pts, colour)
    return ""


def _brows(spec: dict[str, Any], prefix: str) -> str:
    style = spec["brow"]
    colour = spec["hair_colour"]
    if style == "unibrow":
        seg = ((-114, -62), (-38, -74), (38, -74), (114, -62))
        return ink(wobble(chain(seg), prefix + ":brow", 1.4), 17,
                   prefix + ":brow", taper=0.30, colour=colour)
    out = []
    for side in (-1, 1):
        if style == "angry":
            seg = ((side * 116, -76), (side * 94, -82), (side * 62, -62), (side * 32, -46))
        elif style == "raised":
            seg = ((side * 112, -82), (side * 90, -94), (side * 62, -92), (side * 34, -84))
        elif style == "flat":
            seg = ((side * 112, -66), (side * 92, -70), (side * 62, -70), (side * 36, -66))
        else:  # worried — the inner ends lift
            seg = ((side * 116, -48), (side * 96, -58), (side * 64, -74), (side * 32, -76))
        out.append(ink(wobble(chain(seg), f"{prefix}:brow{side}", 1.3), 17,
                       f"{prefix}:brow{side}", taper=0.38, colour=colour))
    return "".join(out)


def _eyes(spec: dict[str, Any], prefix: str, blink: float) -> str:
    out = []
    for side, dx in ((-1, -63), (1, 63)):
        ry = 39.0 * max(0.10, 1.0 - blink)
        eye = wobble(ellipse_points(dx, -6, 34, ry), f"{prefix}:eye{side}", 1.5, closed=True)
        out.append(f'<path d="{poly_path(eye)}" fill="#FFFFFF"/>')
        if blink < 0.72:
            out.append(f'<ellipse cx="{dx - side * 8:g}" cy="-3" rx="12" ry="13" fill="{INK}"/>')
            out.append(f'<circle cx="{dx - side * 8 - 4:g}" cy="-9" r="3.6" fill="#FFFFFF"/>')
        out.append(ink_loop(eye, 8, f"{prefix}:eye{side}"))
    return "".join(out)


def _nose(spec: dict[str, Any], prefix: str) -> str:
    style = spec["nose"]
    skin = spec["skin"]
    if style == "potato":
        pts = wobble(ellipse_points(0, 32, 34, 28), f"{prefix}:nose", 1.6, closed=True)
        return (fill_shape(pts, skin) + ink_loop(pts, 9, f"{prefix}:nose")
                + f'<circle cx="-13" cy="38" r="5" fill="{INK}"/>'
                + f'<circle cx="13" cy="38" r="5" fill="{INK}"/>')
    if style == "pointy":
        pts = wobble(chain(((-24, 14), (8, 62), (28, 6))), f"{prefix}:nose", 1.4, closed=True)
        return fill_shape(pts, skin) + ink_loop(pts, 9, f"{prefix}:nose")
    if style == "tiny":
        seg = ((-8, 30), (0, 40), (9, 32))
        return ink(wobble(chain(seg), prefix + ":nose", 1.0), 8,
                   prefix + ":nose", taper=0.30)
    pts = chain(((-30, 16), (-40, 40), (-18, 54), (0, 52)),
                ((0, 52), (18, 50), (26, 32), (16, 18)))
    nw = wobble(pts, prefix + ":nose", 1.2)
    return (f'<path d="{poly_path(nw)}" fill="{skin}"/>'
            + ink(nw, 8, prefix + ":nose", taper=0.20))


def _mouth(spec: dict[str, Any], prefix: str) -> str:
    style = spec["mouth"]
    skin = spec["skin"]
    if style == "open":
        m = wobble(chain(((-26, 66), (-14, 56), (14, 56), (26, 66)),
                         ((26, 66), (16, 90), (-16, 90), (-26, 66))),
                   prefix + ":mouth", 1.4, closed=True)
        return f'<path d="{poly_path(m)}" fill="{INK}"/>' + ink_loop(m, 8, prefix + ":mouth")
    if style == "grin":
        m = wobble(chain(((-36, 64), (-17, 72), (17, 72), (36, 64)),
                         ((36, 64), (18, 94), (-18, 94), (-36, 64))),
                   prefix + ":mouth", 1.4, closed=True)
        return f'<path d="{poly_path(m)}" fill="{skin}"/>' + ink_loop(m, 9, prefix + ":mouth")
    if style == "flat":
        seg = ((-28, 76), (-9, 72), (9, 78), (28, 74))
    elif style == "frown":
        seg = ((-26, 84), (-12, 72), (12, 72), (26, 84))
    else:  # squiggle
        seg = ((-30, 82), (-15, 68), (3, 84), (18, 68))
    return ink(wobble(chain(seg), prefix + ":mouth", 1.3), 9,
               prefix + ":mouth", taper=0.26)


def _ears(spec: dict[str, Any], prefix: str) -> str:
    if spec["ears"] == "none":
        return ""
    out = []
    grow = 1.9 if spec["ears"] == "big" else 1.0
    for side in (-1, 1):
        pts = wobble(ellipse_points(side * (HW - 4), 4, 16 * grow, 26 * grow),
                     f"{prefix}:ear{side}", 1.2, closed=True)
        out.append(f'<path d="{poly_path(pts)}" fill="{spec["skin"]}"/>')
        out.append(ink_loop(pts, 8, f"{prefix}:ear{side}"))
    return "".join(out)


def head_parts(spec: dict[str, Any], prefix: str, blink: float = 0.0) -> str:
    """Head, hair and face — everything above the neck."""
    hc = wobble(head_contour(), prefix + ":head", 1.7, closed=True)
    return "".join([
        _ears(spec, prefix),
        fill_shape(hc, spec["skin"]),
        ink_loop(hc, 10, prefix + ":head"),
        _hair(spec, prefix),
        _brows(spec, prefix),
        ink(wobble(chain(((-104, 16), (-92, 40), (-74, 50))), prefix + ":cheek", 1.2),
            6, prefix + ":cheek", taper=0.50),
        _eyes(spec, prefix, blink),
        _nose(spec, prefix),
        _mouth(spec, prefix),
    ])


# --- torso ------------------------------------------------------------------

def _body_shape() -> str:
    top, bot = TORSO_TOP, TORSO_BOT
    pts = wobble(chain(
        ((-86, top + 6), (0, top - 22), (86, top + 6), (92, top + 60)),
        ((92, top + 60), (96, bot - 8), (50, bot + 18), (0, bot + 20)),
        ((0, bot + 20), (-50, bot + 18), (-96, bot - 8), (-92, top + 60)),
        ((-92, top + 60), (-90, top + 30), (-90, top + 14), (-86, top + 6)),
    ), "body", 1.5, closed=True)
    return fill_shape(pts, SKIN)


def _outfit_fur(spec: dict[str, Any], prefix: str) -> str:
    top, bot = TORSO_TOP, TORSO_BOT
    tunic = chain(
        ((-92, top + 16), (-90, bot - 22), (-72, bot - 32), (-62, bot - 10)),
        ((-62, bot - 10), (-48, bot + 6), (-38, bot - 18), (-24, bot + 2)),
        ((-24, bot + 2), (-8, bot + 16), (6, bot - 10), (20, bot + 8)),
        ((20, bot + 8), (34, bot - 12), (48, bot + 6), (60, bot - 20)),
        ((60, bot - 20), (74, bot - 6), (88, bot - 24), (92, top + 16)),
        ((92, top + 16), (46, top + 44), (0, top + 66), (-46, top + 44)),
        ((-46, top + 44), (-72, top + 30), (-88, top + 24), (-92, top + 16)),
    )
    tw = wobble(tunic, prefix + ":tunic", 1.6, closed=True)
    out = [fill_shape(tw, spec["shirt"]), ink_loop(tw, 9, prefix + ":tunic")]
    for i, (sx, sy) in enumerate([(-58, 158), (10, 176), (56, 156), (-30, 216), (36, 222), (-4, 132)]):
        out.append(fill_shape(wobble(ellipse_points(sx, sy, 13, 10), f"{prefix}:spot{i}", 1.2,
                                     closed=True), FUR_DARK))
    out.append(ink(wobble(chain(((-90, 238), (-30, 232), (30, 234), (90, 228))),
                          prefix + ":belt", 1.3), 13, prefix + ":belt", taper=0.14))
    for i, (tx, ty) in enumerate([(-34, 246), (30, 248)]):
        tooth = wobble(chain(((tx - 11, ty), (tx + 11, ty), (tx + 3, ty + 32), (tx, ty + 36)),
                             ((tx, ty + 36), (tx - 6, ty + 28), (tx - 11, ty), (tx - 11, ty))),
                       f"{prefix}:tooth{i}", 1.1, closed=True)
        out.append(fill_shape(tooth, SKIN) + ink_loop(tooth, 7, f"{prefix}:tooth{i}"))
    return "".join(out)


def _outfit_shirt(spec: dict[str, Any], prefix: str) -> str:
    top, bot = TORSO_TOP, TORSO_BOT
    pts = wobble(chain(
        ((-90, top + 14), (-88, bot - 12), (-50, bot + 6), (0, bot + 12)),
        ((0, bot + 12), (50, bot + 6), (88, bot - 12), (90, top + 14)),
        ((90, top + 14), (46, top + 40), (0, top + 56), (-46, top + 40)),
        ((-46, top + 40), (-72, top + 28), (-86, top + 22), (-90, top + 14)),
    ), prefix + ":shirt", 1.5, closed=True)
    return fill_shape(pts, spec["shirt"]) + ink_loop(pts, 9, prefix + ":shirt")


def _outfit_tie(spec: dict[str, Any], prefix: str) -> str:
    return (_outfit_shirt(spec, prefix)
            + f'<path d="M-11 {TORSO_TOP + 34:g} L11 {TORSO_TOP + 34:g} '
              f'L17 {TORSO_TOP + 58:g} L-17 {TORSO_TOP + 58:g} Z" fill="{spec["accent"]}"/>'
            + f'<path d="M0 {TORSO_TOP + 58:g} L18 {TORSO_TOP + 116:g} '
              f'L0 {TORSO_TOP + 148:g} L-18 {TORSO_TOP + 116:g} Z" fill="{spec["accent"]}"/>')


def _outfit_overalls(spec: dict[str, Any], prefix: str) -> str:
    return (_outfit_shirt(spec, prefix)
            + f'<path d="M-34 {TORSO_TOP + 10:g} L-42 {TORSO_BOT - 30:g} '
              f'L42 {TORSO_BOT - 30:g} L34 {TORSO_TOP + 10:g}" fill="none" '
              f'stroke="#4A6E8A" stroke-width="30" stroke-linejoin="round"/>')


def _outfit_robe(spec: dict[str, Any], prefix: str) -> str:
    top = TORSO_TOP
    pts = wobble(chain(
        ((-84, top + 2), (-100, 356), (-40, 368), (0, 368)),
        ((0, 368), (40, 368), (100, 356), (84, top + 2)),
        ((84, top + 2), (46, top + 30), (0, top + 46), (-46, top + 30)),
        ((-46, top + 30), (-72, top + 20), (-82, top + 10), (-84, top + 2)),
    ), prefix + ":robe", 1.7, closed=True)
    return (fill_shape(pts, spec["shirt"]) + ink_loop(pts, 9, prefix + ":robe")
            + ink(wobble(chain(((-92, 216), (0, 210), (92, 214))), prefix + ":sash", 1.4),
                  20, prefix + ":sash", taper=0.12, colour=spec["accent"]))


OUTFIT_RENDERERS = {
    "fur": _outfit_fur,
    "shirt": _outfit_shirt,
    "tie": _outfit_tie,
    "overalls": _outfit_overalls,
    "robe": _outfit_robe,
}


def torso_parts(spec: dict[str, Any], prefix: str) -> str:
    """Neck, torso and clothing."""
    neck = ink(wobble(chain(((0, NECK_TOP), (0, 112), (0, 128), (0, NECK_BOT))),
                      prefix + ":neck", 1.0), 15, prefix + ":neck", taper=0.14,
               colour=spec["skin"])
    renderer = OUTFIT_RENDERERS.get(spec["outfit"])
    outfit = "" if renderer is None else renderer(spec, prefix)
    return neck + _body_shape() + outfit


# --- limbs ------------------------------------------------------------------

def _limb(seg: Sequence[tuple[float, float]], prefix: str, width: float = 10) -> str:
    return ink(wobble(chain(seg), prefix, 1.7), width, prefix, taper=0.13)


def _hand(x: float, y: float, prefix: str, r: float = 15) -> str:
    return fill_shape(wobble(circle_points(x, y, r), prefix, 1.2, closed=True), INK)


def _foot(x: float, y: float, direction: float, prefix: str) -> str:
    d = direction
    pts = chain(
        ((x, y - 13), (x + d * 16, y - 15), (x + d * 44, y - 10), (x + d * 48, y + 5)),
        ((x + d * 48, y + 5), (x + d * 50, y + 17), (x + d * 34, y + 19), (x + d * 20, y + 19)),
        ((x + d * 20, y + 19), (x - d * 6, y + 19), (x - d * 24, y + 17), (x - d * 24, y + 5)),
        ((x - d * 24, y + 5), (x - d * 24, y - 10), (x - d * 12, y - 13), (x, y - 13)),
    )
    fw = wobble(pts, prefix, 1.3, closed=True)
    return f'<path d="{poly_path(fw)}" fill="{SKIN}"/>' + ink_loop(fw, 9, prefix)


def _limbs_for(pose: str, walk_phase: Optional[float]):
    if walk_phase is not None:
        return walk_arms(walk_phase), walk_legs(walk_phase)
    arms = [ARM_LEFT.get(pose, ARM_LEFT["stand"]), ARM_RIGHT.get(pose, ARM_RIGHT["stand"])]
    return arms, LEGS_STAND


def body_parts(spec: dict[str, Any], prefix: str, pose: str,
               walk_phase: Optional[float]) -> str:
    """Far limbs, then torso, then near limbs — the order that reads correctly."""
    arms, legs = _limbs_for(pose, walk_phase)
    return "".join([
        _limb(legs[0], prefix + ":leg0", 11),
        _foot(legs[0][2][0], legs[0][2][1], -1, prefix + ":foot0"),
        _limb(arms[1], prefix + ":arm1", 10),
        _hand(arms[1][2][0], arms[1][2][1], prefix + ":hand1"),
        torso_parts(spec, prefix),
        _limb(legs[1], prefix + ":leg1", 11),
        _foot(legs[1][2][0], legs[1][2][1], 1, prefix + ":foot1"),
        _limb(arms[0], prefix + ":arm0", 10),
        _hand(arms[0][2][0], arms[0][2][1], prefix + ":hand0"),
    ])


def figure(character: dict[str, Any], *, pose: str = "stand",
           walk_phase: Optional[float] = None, blink: float = 0.0, order: int = 0) -> str:
    """One complete figure in local coordinates (head centre at 0,0)."""
    spec = spec_of(character)
    prefix = f"hand{order}:{pose}"
    return body_parts(spec, prefix, pose, walk_phase) + head_parts(spec, prefix, blink)


def character_svg(character: dict[str, Any], time: Optional[float] = None,
                  settings: Optional[dict[str, Any]] = None, order: int = 0) -> str:
    """Drop-in replacement for ``svg.character_svg`` in the hand-drawn style."""
    x = _num(character.get("x"), 640)
    y = _num(character.get("y"), 220)
    scale = _num(character.get("scale"), 0.7)
    rotation = _num(character.get("rotation"), 0)
    pose = str(character.get("pose", "stand"))
    blink = 0.0
    walk_phase: Optional[float] = None

    if time is not None and settings is not None:
        state = character_state(character, time, settings, order)
        x, y, scale = state["x"], state["y"] + state["bob"], state["scale"]
        pose = state["pose"]
        blink = state["blink"]
        if state["walking"]:
            walk_phase = state["walk_phase"]

    inner = figure(character, pose=pose, walk_phase=walk_phase, blink=blink, order=order)
    return (f'<g transform="translate({x:g} {y:g}) rotate({rotation:g}) scale({scale:g})">'
            f'{inner}</g>')
