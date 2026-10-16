from __future__ import annotations

import math
from html import escape
from typing import Any, Optional

from . import handdraw, handprops
from .animate import character_state, prop_state, scene_animation


VIEW_WIDTH = 1280
VIEW_HEIGHT = 720
INK = "#171717"


def _text(value: Any) -> str:
    return escape(str(value))


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --- optional minimal backdrop ---------------------------------------------
# Four layers, each at most a few SVG nodes, sized so a frame never carries more
# than four extra marks: one tone-on-tone paper shape, a ground line, a shadow
# under every character and a short stroke under the title. Placement rotates
# per scene so neighbouring shots never repeat the same backdrop.
ACCENT = "#F5B632"
PAPER_SHAPES = ("circle_top_right", "circle_bottom_left", "band_bottom", "circle_top_left")
BACKDROP_DEFAULTS = {
    "enabled": False,
    "paper_shape": "auto",
    "ground": True,
    "title_stroke": True,
    "character_shadows": True,
}


def _mix(first: str, second: str, ratio: float) -> str:
    """Blend two #RRGGBB colours; used for tone-on-tone shapes."""
    try:
        left = tuple(int(first[index:index + 2], 16) for index in (1, 3, 5))
        right = tuple(int(second[index:index + 2], 16) for index in (1, 3, 5))
    except ValueError:
        return first
    mixed = tuple(round(left[i] + (right[i] - left[i]) * ratio) for i in range(3))
    return "#%02X%02X%02X" % mixed


def _luminance(color: str) -> float:
    try:
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    except ValueError:
        return 1.0
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _paper_shape(kind: str, background: str, art_style: str = "line") -> str:
    tint = _mix(background, INK, 0.065)
    if art_style == "hand":
        return handprops.paper_shape(kind, tint)
    if kind == "circle_top_right":
        return f'<circle cx="1220" cy="110" r="260" fill="{tint}"/>'
    if kind == "circle_bottom_left":
        return f'<circle cx="60" cy="676" r="225" fill="{tint}"/>'
    if kind == "band_bottom":
        # A gently curved floor reads as hand drawn; a straight edge looks printed.
        return f'<path d="M-80 720 L-80 598 Q640 546 1360 598 L1360 720 Z" fill="{tint}"/>'
    return f'<circle cx="80" cy="96" r="235" fill="{tint}"/>'


def _title_stroke(title: str, background: str, art_style: str = "line") -> str:
    width = min(300, 46 * len(title) + 40)
    # Accent yellow disappears on saturated backgrounds, so fall back to ink.
    colour = ACCENT if _contrast(ACCENT, background) >= 1.55 else _mix(background, INK, 0.30)
    if art_style == "hand":
        return handprops.title_stroke(640 - width / 2, 640 + width / 2, 126, colour)
    return (
        f'<path d="M{640 - width / 2:g} 126 Q640 116 {640 + width / 2:g} 126" fill="none" '
        f'stroke="{colour}" stroke-width="14" stroke-linecap="round"/>'
    )


def _prop_bottom(prop: dict[str, Any]) -> float:
    """Lowest edge of a prop, used to keep the ground line clear of shapes."""
    kind = str(prop.get("type", ""))
    y = _num(prop.get("y"), 0)
    if kind == "node":
        return y + _num(prop.get("radius"), 76)
    if kind == "mirror":
        return y + _num(prop.get("height"), 360) / 2
    if kind == "cross":
        return y + _num(prop.get("size"), 180) / 2
    if kind == "star":
        return y + 43 * _num(prop.get("scale"), 1)
    if kind == "bubble":
        return y + _num(prop.get("height"), 130) / 2 + 45
    if kind == "arrow":
        return max(_num(prop.get("y1"), 0), _num(prop.get("y2"), 0))
    return y


def _figure_metrics(item: dict[str, Any], project: dict[str, Any]) -> dict[str, float]:
    """How far below a character's anchor its feet and shadow sit.

    The hand-drawn figures have different proportions from the line figures, so
    they need their own foot offset rather than a shared magic number.
    """
    return handdraw.metrics_for(handdraw.art_style_for(item, project))


def _ground_line(scene: dict[str, Any], project: dict[str, Any],
                 art_style: str = "line") -> str:
    feet = [
        _num(item.get("y"), 220)
        + _num(item.get("scale"), 0.7) * _figure_metrics(item, project)["foot"]
        for item in scene.get("characters", [])
    ]
    props = [_prop_bottom(item) for item in scene.get("props", [])]
    lowest = max([value + 22 for value in feet] + [value + 14 for value in props], default=596)
    y = int(max(560, min(608, lowest)))
    if art_style == "hand":
        return f'<g opacity="0.13">{handprops.ground_line(96, 1184, y, INK)}</g>'
    return (
        f'<path d="M96 {y} Q640 {y - 12} 1184 {y}" fill="none" stroke="{INK}" '
        f'stroke-width="10" stroke-linecap="round" opacity="0.13"/>'
    )


def _character_shadows(scene: dict[str, Any], project: dict[str, Any]) -> str:
    parts = []
    for item in scene.get("characters", []):
        x, y = _num(item.get("x"), 640), _num(item.get("y"), 220)
        scale = _num(item.get("scale"), 0.7)
        metrics = _figure_metrics(item, project)
        parts.append(
            f'<ellipse cx="{x:g}" cy="{y + scale * metrics["foot"]:g}" '
            f'rx="{scale * metrics["shadow_rx"]:g}" '
            f'ry="{scale * metrics["shadow_ry"]:g}" fill="{INK}" opacity="0.08"/>'
        )
    return "".join(parts)


def backdrop_settings(project: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
    """Merge project level defaults with a per scene override."""
    settings = dict(BACKDROP_DEFAULTS)
    for source in (project.get("background_style"), scene.get("background_style")):
        if isinstance(source, bool):
            settings["enabled"] = source
        elif isinstance(source, dict):
            settings.update({key: value for key, value in source.items() if key in settings})
    if scene.get("paper_shape"):
        settings["paper_shape"] = str(scene["paper_shape"])
    return settings


def render_backdrop(scene: dict[str, Any], project: dict[str, Any], index: int = 0) -> str:
    settings = backdrop_settings(project, scene)
    if not settings["enabled"]:
        return ""
    background = escape(str(scene.get("background", project.get("background", "#FFFDF7"))), quote=True)
    art_style = handdraw.art_style_for({}, project)
    shape = str(settings["paper_shape"])
    if shape == "auto":
        shape = PAPER_SHAPES[index % len(PAPER_SHAPES)]
    layers = []
    if shape in PAPER_SHAPES:
        layers.append(_paper_shape(shape, background, art_style))
    if settings["character_shadows"]:
        layers.append(_character_shadows(scene, project))
    if settings["ground"]:
        layers.append(_ground_line(scene, project, art_style))
    title = str(scene.get("title", "")).strip()
    if settings["title_stroke"] and title:
        layers.append(_title_stroke(title, background, art_style))
    return "".join(layers)


def _face(expression: str, blink: float = 0.0) -> str:
    if blink > 0:
        radius = max(0.0, 7 * (1 - blink))
        eyes = (f'<ellipse cx="-23" cy="-10" rx="7" ry="{radius:.2f}"/>'
                f'<ellipse cx="23" cy="-10" rx="7" ry="{radius:.2f}"/>')
    else:
        eyes = '<circle cx="-23" cy="-10" r="7"/><circle cx="23" cy="-10" r="7"/>'
    mouths = {
        "neutral": '<path d="M-19 27 Q0 35 19 27" fill="none" class="thin"/>',
        "smile": '<path d="M-24 20 Q0 51 24 20" fill="none" class="thin"/>',
        "confused": '<path d="M-22 30 Q0 14 22 30" fill="none" class="thin"/><path d="M-40 -35 Q-21 -49 -5 -36" fill="none" class="thin"/>',
        "surprised": '<circle cx="0" cy="27" r="13" fill="none" class="thin"/>',
        "sad": '<path d="M-24 39 Q0 10 24 39" fill="none" class="thin"/>',
        "angry": '<path d="M-40 -34 L-8 -22 M40 -34 L8 -22 M-22 36 Q0 18 22 36" fill="none" class="thin"/>',
    }
    return f'<g fill="{INK}">{eyes}{mouths.get(expression, mouths["neutral"])}</g>'


def _limbs(pose: str) -> str:
    poses = {
        "stand": '''
          <path d="M-43 178 L-104 272 M43 178 L104 272" fill="none" class="ink"/>
          <path d="M-24 326 L-68 438 M24 326 L72 438" fill="none" class="ink"/>''',
        "look_down": '''
          <path d="M-42 178 L-88 245 L-55 280 M42 178 L82 234 L48 270" fill="none" class="ink"/>
          <path d="M-24 326 L-55 430 L-105 462 M24 326 L70 420 L118 430" fill="none" class="ink"/>''',
        "point": '''
          <path d="M-43 178 L-112 225 L-168 188 M43 178 L115 132 L190 112" fill="none" class="ink"/>
          <path d="M-24 326 L-62 438 M24 326 L72 438" fill="none" class="ink"/>''',
        "arms_up": '''
          <path d="M-43 178 L-120 104 L-162 38 M43 178 L118 104 L160 38" fill="none" class="ink"/>
          <path d="M-24 326 L-68 438 M24 326 L72 438" fill="none" class="ink"/>''',
        "think": '''
          <path d="M-43 178 L-95 245 L-52 274 M43 178 L102 102 L74 31" fill="none" class="ink"/>
          <circle cx="68" cy="15" r="13" fill="#F3C48D" class="thin"/>
          <path d="M-24 326 L-68 438 M24 326 L72 438" fill="none" class="ink"/>''',
        "walk": '''
          <path d="M-43 178 L-121 232 M43 178 L126 220" fill="none" class="ink"/>
          <path d="M-24 326 L-112 414 M24 326 L129 392" fill="none" class="ink"/>''',
    }
    return poses.get(pose, poses["stand"])


def _walk_limbs(phase: float) -> str:
    """Legs and arms mid-stride; two steps per full cycle."""
    swing = 48 * math.sin(phase)
    lift_left = max(0.0, -30 * math.cos(phase))
    lift_right = max(0.0, 30 * math.cos(phase))
    return (
        f'<path d="M-43 178 L{-104 - 0.6 * swing:g} 272 M43 178 L{104 + 0.6 * swing:g} 272" fill="none" class="ink"/>'
        f'<path d="M-24 326 L{-68 + swing:g} {438 - lift_left:g} M24 326 L{72 - swing:g} {438 - lift_right:g}" fill="none" class="ink"/>'
    )


def character_svg(
    character: dict[str, Any],
    time: Optional[float] = None,
    settings: Optional[dict[str, Any]] = None,
    order: int = 0,
    art_style: str = "line",
) -> str:
    if art_style == "hand":
        return handdraw.character_svg(character, time, settings, order)
    x = _num(character.get("x"), 640)
    y = _num(character.get("y"), 220)
    scale = _num(character.get("scale"), 0.7)
    rotation = _num(character.get("rotation"), 0)
    shirt = escape(str(character.get("shirt", "#6599D1")), quote=True)
    skin = escape(str(character.get("skin", "#F3C48D")), quote=True)
    pose = str(character.get("pose", "stand"))
    expression = str(character.get("expression", "neutral"))
    clothing = str(character.get("clothing", "shirt"))
    blink = 0.0
    walk_phase: Optional[float] = None

    if time is not None and settings is not None:
        state = character_state(character, time, settings, order)
        x, y, scale = state["x"], state["y"] + state["bob"], state["scale"]
        pose = state["pose"]
        blink = state["blink"]
        if state["walking"]:
            walk_phase = state["walk_phase"]

    torso = (
        '<path d="M-60 92 Q0 65 60 92 L82 302 Q0 340 -82 302 Z" '
        f'fill="{shirt}" class="ink"/>'
    )
    if clothing == "hide":
        torso = (
            '<path d="M-60 92 Q0 65 60 92 L82 300 L54 286 L30 318 L5 294 '
            f'L-23 320 L-49 291 L-82 302 Z" fill="{shirt}" class="ink"/>'
        )

    return f'''
      <g transform="translate({x:g} {y:g}) rotate({rotation:g}) scale({scale:g})">
        <circle cx="0" cy="0" r="86" fill="{skin}" class="ink"/>
        {_face(expression, blink)}
        {torso}
        {_walk_limbs(walk_phase) if walk_phase is not None else _limbs(pose)}
      </g>'''


def _arrow(prop: dict[str, Any]) -> str:
    x1, y1 = _num(prop.get("x1"), 300), _num(prop.get("y1"), 350)
    x2, y2 = _num(prop.get("x2"), 800), _num(prop.get("y2"), 350)
    grow = _num(prop.get("_grow"), 1.0)
    x2, y2 = x1 + (x2 - x1) * grow, y1 + (y2 - y1) * grow
    color = escape(str(prop.get("color", "#45A95A")), quote=True)
    width = _num(prop.get("width"), 18)
    return f'''
      <defs><marker id="arrowhead" markerWidth="10" markerHeight="10" refX="7" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L8,3 z" fill="{color}"/></marker></defs>
      <path d="M{x1:g} {y1:g} L{x2:g} {y2:g}" fill="none" stroke="{color}" stroke-width="{width:g}" stroke-linecap="round" marker-end="url(#arrowhead)"/>'''


def _cross(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 850), _num(prop.get("y"), 300)
    size = _num(prop.get("size"), 180)
    color = escape(str(prop.get("color", "#D9433F")), quote=True)
    width = _num(prop.get("width"), 38)
    return f'''
      <g filter="url(#shadow)">
        <path d="M{x-size/2:g} {y-size/2:g} L{x+size/2:g} {y+size/2:g}" stroke="{color}" stroke-width="{width:g}" stroke-linecap="round"/>
        <path d="M{x+size/2:g} {y-size/2:g} L{x-size/2:g} {y+size/2:g}" stroke="{color}" stroke-width="{width:g}" stroke-linecap="round"/>
      </g>'''


def _bubble(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 640), _num(prop.get("y"), 250)
    width, height = _num(prop.get("width"), 250), _num(prop.get("height"), 130)
    fill = escape(str(prop.get("fill", "#E9F4DD")), quote=True)
    label = _text(prop.get("text", "…"))
    return f'''
      <g transform="translate({x:g} {y:g})">
        <path d="M{-width/2:g} 0 Q{-width/2:g} {-height/2:g} 0 {-height/2:g} Q{width/2:g} {-height/2:g} {width/2:g} 0 Q{width/2:g} {height/2:g} 28 {height/2:g} L-30 {height/2+45:g} L-18 {height/2-2:g} Q{-width/2:g} {height/2-8:g} {-width/2:g} 0Z" fill="{fill}" class="ink"/>
        <text x="0" y="12" text-anchor="middle" class="label">{label}</text>
      </g>'''


def _node(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 250), _num(prop.get("y"), 300)
    radius = _num(prop.get("radius"), 76)
    label = _text(prop.get("text", "身份"))
    fill = escape(str(prop.get("fill", "#FFFDF7")), quote=True)
    return f'''
      <g>
        <circle cx="{x:g}" cy="{y:g}" r="{radius:g}" fill="{fill}" class="thin"/>
        <text x="{x:g}" y="{y+12:g}" text-anchor="middle" class="small-label">{label}</text>
      </g>'''


def _mirror(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 880), _num(prop.get("y"), 350)
    width, height = _num(prop.get("width"), 250), _num(prop.get("height"), 360)
    shirt = escape(str(prop.get("shirt", "#F0A454")), quote=True)
    return f'''
      <g transform="translate({x:g} {y:g})">
        <rect x="{-width/2:g}" y="{-height/2:g}" width="{width:g}" height="{height:g}" rx="{width/2:g}" fill="#D9F1F4" class="ink"/>
        <path d="M{-width/2+18:g} {height/2-54:g} Q0 {height/2-15:g} {width/2-18:g} {height/2-54:g}" fill="none" stroke="#87C8CE" stroke-width="13"/>
        <g transform="translate(0 -66) scale(.46)">
          <circle cx="0" cy="0" r="86" fill="#F3C48D" class="ink"/>
          {_face("smile")}
          <path d="M-60 92 Q0 65 60 92 L82 302 Q0 340 -82 302 Z" fill="{shirt}" class="ink"/>
        </g>
      </g>'''


def _arc(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 150), _num(prop.get("y"), 120)
    width = _num(prop.get("width"), 180)
    color = escape(str(prop.get("color", "#F5B632")), quote=True)
    return f'<path d="M{x-width/2:g} {y:g} Q{x:g} {y-55:g} {x+width/2:g} {y:g}" fill="none" stroke="{color}" stroke-width="18" stroke-linecap="round"/>'


def _star(prop: dict[str, Any]) -> str:
    x, y = _num(prop.get("x"), 160), _num(prop.get("y"), 190)
    scale = _num(prop.get("scale"), 1)
    color = escape(str(prop.get("color", "#EAF6FF")), quote=True)
    return f'<path transform="translate({x:g} {y:g}) scale({scale:g})" d="M0-42 L12-13 L43-13 L18 6 L27 37 L0 19 L-27 37 L-18 6 L-43-13 L-12-13Z" fill="{color}"/>'


# --- minimal hand-drawn object icons ---------------------------------------
# Every icon lives inside a 0..100 box centred on the origin and is drawn with
# the same ink weight as the characters, so an icon reads as part of the same
# drawing instead of a pasted sticker. Text-free by construction: icons must be
# rasterisable on the transparent animation layer without a font scan.
ICON_BOX = 100.0
# On-screen stroke weights: icons take the same pen as the characters
# (a character is drawn at ~0.65 scale with a 12px pen, i.e. ~8px on screen),
# and the widths are divided by the icon scale so bigger icons do not get
# heavier lines.
_ICON_INK = 8.0
_ICON_THIN = 5.0
_LINE = 'fill="none" stroke="{color}" stroke-width="{ink}" stroke-linecap="round" stroke-linejoin="round"'
_THIN_LINE = 'fill="none" stroke="{color}" stroke-width="{thin}" stroke-linecap="round" stroke-linejoin="round"'
_SHAPE = 'stroke="{color}" stroke-width="{ink}" stroke-linecap="round" stroke-linejoin="round"'

ICONS: dict[str, dict[str, str]] = {
    "sheep": {
        "fill": "#F6F3EC",
        "body": (
            # woolly body
            f'<path d="M-28 14 Q-42 12 -39 -1 Q-46 -15 -28 -19 Q-24 -32 -6 -29 '
            f'Q9 -38 21 -26 Q38 -24 35 -8 Q45 4 28 14 Z" {_SHAPE}/>'
            # head, horn, ear
            f'<circle cx="-41" cy="-13" r="14" {_SHAPE}/>'
            f'<path d="M-48 -25 Q-62 -34 -55 -47" {_LINE}/>'
            f'<path d="M-33 -24 Q-27 -34 -17 -35" {_LINE}/>'
            # legs
            f'<path d="M-22 14 L-22 44 M-6 14 L-6 44 M12 14 L12 44 M26 14 L26 44" {_LINE}/>'
            f'<circle cx="-45" cy="-14" r="2.6" fill="#171717"/>'
        ),
    },
    "rice": {
        "fill": "#FFFFFF",
        "body": (
            # heaped rice, a warmer tone than the bowl so it reads as a mound
            '<path d="M-31 2 Q0 -42 31 2 Z" fill="#F7E7C8" stroke="{color}" '
            'stroke-width="{ink}" stroke-linecap="round" stroke-linejoin="round"/>'
            # bowl
            '<path d="M-42 2 L42 2 Q36 45 0 45 Q-36 45 -42 2 Z" fill="#FFFFFF" '
            'stroke="{color}" stroke-width="{ink}" stroke-linecap="round" stroke-linejoin="round"/>'
            # grains
            '<circle cx="-11" cy="-13" r="3.2" fill="#FFFFFF"/>'
            '<circle cx="2" cy="-20" r="3.2" fill="#FFFFFF"/>'
            '<circle cx="14" cy="-11" r="3.2" fill="#FFFFFF"/>'
        ),
    },
    "fish": {
        "fill": "#BBDCEC",
        "body": (
            f'<path d="M-34 0 Q-6 -30 24 -16 Q38 -8 44 0 Q38 8 24 16 Q-6 30 -34 0 Z" {_SHAPE}/>'
            f'<path d="M-34 0 L-58 -20 L-58 20 Z" {_SHAPE}/>'
            f'<path d="M4 -14 Q10 0 4 14" {_THIN_LINE}/>'
            '<circle cx="22" cy="-5" r="3.4" fill="#171717"/>'
        ),
    },
    "shoes": {
        "fill": "#E7DACB",
        "body": (
            # upper: heel -> ankle opening -> toe
            f'<path d="M-42 20 L-36 -8 Q-34 -18 -22 -18 Q-10 -18 -4 -6 L10 12 '
            f'Q20 19 44 20 Z" {_SHAPE}/>'
            # sole
            f'<path d="M-46 20 L46 20 Q48 32 36 32 L-36 32 Q-48 32 -46 20 Z" {_SHAPE}/>'
            # lace
            f'<path d="M-28 -16 L-14 -2 M-18 -18 L-6 -6" {_THIN_LINE}/>'
        ),
    },
}


def _icon(prop: dict[str, Any]) -> str:
    """A flat object icon; ``size`` is the box width in scene units."""
    icon = ICONS.get(str(prop.get("name", "")))
    if icon is None:
        return ""
    x, y = _num(prop.get("x"), 640), _num(prop.get("y"), 300)
    scale = _num(prop.get("size"), 130) / ICON_BOX * _num(prop.get("scale"), 1)
    rotation = _num(prop.get("rotation"), 0)
    fill = escape(str(prop.get("color", icon.get("fill", "#FFFFFF"))), quote=True)
    body = icon["body"].format(
        color=INK,
        ink=f"{_ICON_INK / scale:.3f}",
        thin=f"{_ICON_THIN / scale:.3f}",
    )
    return (
        f'<g transform="translate({x:g} {y:g}) rotate({rotation:g}) scale({scale:g})">'
        f'<g fill="{fill}">{body}</g></g>'
    )


def _exchange(prop: dict[str, Any]) -> str:
    """Double-headed arrow: trade and reciprocity, never a one-way flow."""
    x1, y1 = _num(prop.get("x1"), 430), _num(prop.get("y1"), 300)
    x2, y2 = _num(prop.get("x2"), 850), _num(prop.get("y2"), 300)
    color = escape(str(prop.get("color", "#45A95A")), quote=True)
    width = _num(prop.get("width"), 14)
    grow = _num(prop.get("_grow"), 1.0)
    x2, y2 = x1 + (x2 - x1) * grow, y1 + (y2 - y1) * grow
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    head = min(length / 2.6, width * 2.2)
    half = width * 0.9

    def tip(px: float, py: float, sign: float) -> str:
        base_x, base_y = px - sign * head * ux, py - sign * head * uy
        nx, ny = -uy * half, ux * half
        return (
            f'<path d="M{px:g} {py:g} L{base_x + nx:g} {base_y + ny:g} '
            f'L{base_x - nx:g} {base_y - ny:g} Z" fill="{color}"/>'
        )

    return (
        f'<path d="M{x1 + head * ux:g} {y1 + head * uy:g} L{x2 - head * ux:g} {y2 - head * uy:g}" '
        f'fill="none" stroke="{color}" stroke-width="{width:g}" stroke-linecap="round"/>'
        + tip(x1, y1, 1.0)
        + tip(x2, y2, -1.0)
    )


def render_prop(prop: dict[str, Any], project: dict[str, Any]) -> str:
    """Draw a prop with the pen that matches the project's art style."""
    if handdraw.art_style_for({}, project) == "hand":
        renderer = handprops.PROP_RENDERERS.get(str(prop.get("type", "")))
        if renderer is not None:
            return renderer(prop)
    return PROP_RENDERERS[prop["type"]](prop)


PROP_RENDERERS = {
    "arrow": _arrow,
    "cross": _cross,
    "bubble": _bubble,
    "node": _node,
    "mirror": _mirror,
    "arc": _arc,
    "star": _star,
    "icon": _icon,
    "exchange": _exchange,
}


def render_scene_svg(
    scene: dict[str, Any],
    project: dict[str, Any],
    index: int = 0,
    time: Optional[float] = None,
) -> str:
    background = escape(str(scene.get("background", project.get("background", "#FFFDF7"))), quote=True)
    title = _text(scene.get("title", ""))
    title_size = int(_num(scene.get("title_size"), 58))
    caption = _text(scene.get("caption", scene.get("narration", "")))
    backdrop = render_backdrop(scene, project, index)
    backdrop_svg = f"\n      {backdrop}" if backdrop else ""
    animation = scene_animation(scene) if time is not None else None
    # Skipping an empty title keeps the animated layer free of text, which is
    # what lets it rasterise without touching the system fonts.
    title_svg = f'\n      <text x="640" y="92" text-anchor="middle" class="title">{title}</text>' if title else ""

    connections = []
    for connection in scene.get("connections", []):
        connections.append(_arrow(connection))
    props = []
    for item in scene.get("props", []):
        render_item = item
        if time is not None:
            state = prop_state(item, time)
            if not state["visible"]:
                continue
            if state["scale"] != 1.0 or state["grow"] < 1.0:
                render_item = {**item, "_grow": state["grow"]}
                if state["scale"] != 1.0:
                    render_item["scale"] = _num(item.get("scale"), 1.0) * state["scale"]
        props.append(render_prop(render_item, project))
    characters = [
        character_svg(item, time, animation, order,
                      art_style=handdraw.art_style_for(item, project))
        for order, item in enumerate(scene.get("characters", []))
    ]

    caption_svg = ""
    if caption:
        caption_svg = f'''
          <rect x="175" y="626" width="930" height="62" rx="20" fill="#171717" opacity="0.93"/>
          <text x="640" y="667" text-anchor="middle" class="caption">{caption}</text>'''

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
      <rect width="1280" height="720" fill="{background}"/>{backdrop_svg}
      <defs>
        <filter id="shadow" x="-30%" y="-30%" width="160%" height="160%">
          <feDropShadow dx="0" dy="7" stdDeviation="0" flood-color="#151515" flood-opacity="0.14"/>
        </filter>
        <style>
          .ink {{ stroke:{INK}; stroke-width:12; stroke-linecap:round; stroke-linejoin:round; }}
          .thin {{ stroke:{INK}; stroke-width:8; stroke-linecap:round; stroke-linejoin:round; }}
          .title {{ font-family:'PingFang SC','Hiragino Sans GB','Noto Sans CJK SC',sans-serif; font-size:{title_size}px; font-weight:900; fill:{INK}; }}
          .label {{ font-family:'PingFang SC','Hiragino Sans GB','Noto Sans CJK SC',sans-serif; font-size:34px; font-weight:800; fill:{INK}; }}
          .small-label {{ font-family:'PingFang SC','Hiragino Sans GB','Noto Sans CJK SC',sans-serif; font-size:29px; font-weight:800; fill:{INK}; }}
          .caption {{ font-family:'PingFang SC','Hiragino Sans GB','Noto Sans CJK SC',sans-serif; font-size:31px; font-weight:700; fill:white; }}
        </style>
      </defs>{title_svg}
      {''.join(connections)}
      {''.join(props)}
      {''.join(characters)}
      {caption_svg}
    </svg>'''
