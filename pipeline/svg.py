from __future__ import annotations

from html import escape
from typing import Any


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


def _face(expression: str) -> str:
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


def character_svg(character: dict[str, Any]) -> str:
    x = _num(character.get("x"), 640)
    y = _num(character.get("y"), 220)
    scale = _num(character.get("scale"), 0.7)
    rotation = _num(character.get("rotation"), 0)
    shirt = escape(str(character.get("shirt", "#6599D1")), quote=True)
    skin = escape(str(character.get("skin", "#F3C48D")), quote=True)
    pose = str(character.get("pose", "stand"))
    expression = str(character.get("expression", "neutral"))
    clothing = str(character.get("clothing", "shirt"))

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
        {_face(expression)}
        {torso}
        {_limbs(pose)}
      </g>'''


def _arrow(prop: dict[str, Any]) -> str:
    x1, y1 = _num(prop.get("x1"), 300), _num(prop.get("y1"), 350)
    x2, y2 = _num(prop.get("x2"), 800), _num(prop.get("y2"), 350)
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


PROP_RENDERERS = {
    "arrow": _arrow,
    "cross": _cross,
    "bubble": _bubble,
    "node": _node,
    "mirror": _mirror,
    "arc": _arc,
    "star": _star,
}


def render_scene_svg(scene: dict[str, Any], project: dict[str, Any]) -> str:
    background = escape(str(scene.get("background", project.get("background", "#FFFDF7"))), quote=True)
    title = _text(scene.get("title", ""))
    title_size = int(_num(scene.get("title_size"), 58))
    caption = _text(scene.get("caption", scene.get("narration", "")))

    connections = []
    for connection in scene.get("connections", []):
        connections.append(_arrow(connection))
    props = [PROP_RENDERERS[item["type"]](item) for item in scene.get("props", [])]
    characters = [character_svg(item) for item in scene.get("characters", [])]

    caption_svg = ""
    if caption:
        caption_svg = f'''
          <rect x="175" y="626" width="930" height="62" rx="20" fill="#171717" opacity="0.93"/>
          <text x="640" y="667" text-anchor="middle" class="caption">{caption}</text>'''

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
      <rect width="1280" height="720" fill="{background}"/>
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
      </defs>
      <text x="640" y="92" text-anchor="middle" class="title">{title}</text>
      {''.join(connections)}
      {''.join(props)}
      {''.join(characters)}
      {caption_svg}
    </svg>'''
