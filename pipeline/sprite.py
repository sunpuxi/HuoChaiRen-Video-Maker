"""Sprite characters: a raster figure placed by the same anchors as the vector ones.

The art is produced offline (an image model, then a chroma key) and dropped into
``<project>/sprites/``. At render time a sprite is just an ``<image>`` element
inside the scene SVG, so rasterising, concatenation, subtitle timing and all
three content-hash caches keep working untouched.

The figure is normalised to ``SPRITE_FIGURE_HEIGHT`` scene units with its feet
on the same line the vector figures use, so a storyboard authored for the line
style keeps its composition when it switches to sprites.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

from .animate import character_state

# The line figure's local span runs from the top of the head (-86) to the feet
# (438), so a figure is 524 units tall and its feet sit 438 below the anchor.
# Matching both numbers keeps an existing storyboard's composition exactly.
SPRITE_FIGURE_HEIGHT = 524.0
SPRITE_FOOT_OFFSET = 438.0

# Resolution order for a character's sprite. A project only needs to provide the
# combinations its storyboard actually uses; anything missing falls back.
FALLBACKS = ("{pose}_{expression}", "{pose}", "{expression}", "neutral", "default")

_CACHE: dict[str, tuple[str, int, int]] = {}
_HEIGHTS: dict[str, dict[str, float]] = {}


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def sprite_dir(project: Optional[dict[str, Any]]) -> Optional[Path]:
    """Where a project keeps its sprites, or ``None`` when it has no project dir."""
    if not project:
        return None
    base = project.get("_dir")
    if not base:
        return None
    return Path(base) / str(project.get("sprites", "sprites"))


def _atlas(base: Path) -> dict[str, float]:
    """Optional per-sprite height overrides from ``sprites/atlas.json``.

    A sprite is normalised by its opaque bounding box. That is right for a
    standing figure, but a pose that throws the arms above the head makes the
    box taller than the body, so the figure would come out smaller than its
    neighbours. The atlas lets a project pin the height of those few sprites.
    """
    key = str(base)
    if key not in _HEIGHTS:
        overrides: dict[str, float] = {}
        manifest = base / "atlas.json"
        if manifest.is_file():
            import json
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            for name, entry in (data.get("sprites") or {}).items():
                if isinstance(entry, dict) and isinstance(entry.get("height"), (int, float)):
                    overrides[name] = float(entry["height"])
        _HEIGHTS[key] = overrides
    return _HEIGHTS[key]


def _load(path: Path) -> tuple[str, int, int]:
    """Base64 payload plus pixel size, cached for the whole run."""
    key = str(path)
    if key not in _CACHE:
        raw = path.read_bytes()
        width, height = _png_size(raw)
        _CACHE[key] = (base64.b64encode(raw).decode("ascii"), width, height)
    return _CACHE[key]


def _png_size(raw: bytes) -> tuple[int, int]:
    """Read width/height straight out of the IHDR chunk — no image library."""
    if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("sprite is not a PNG")
    return (int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big"))


def find_sprite(project: Optional[dict[str, Any]], character: dict[str, Any]) -> Optional[Path]:
    base = sprite_dir(project)
    if base is None or not base.is_dir():
        return None
    pose = str(character.get("pose", "stand"))
    expression = str(character.get("expression", "neutral"))
    for pattern in FALLBACKS:
        candidate = base / f"{pattern.format(pose=pose, expression=expression)}.png"
        if candidate.is_file():
            return candidate
    return None


def missing_sprite_names(project: Optional[dict[str, Any]],
                         characters: list[dict[str, Any]]) -> list[str]:
    """Which characters have no usable sprite — surfaced before a long render."""
    return [f"{c.get('pose', 'stand')}/{c.get('expression', 'neutral')}"
            for c in characters if find_sprite(project, c) is None]


def character_svg(character: dict[str, Any], time: Optional[float] = None,
                  settings: Optional[dict[str, Any]] = None, order: int = 0,
                  project: Optional[dict[str, Any]] = None) -> str:
    """Drop-in replacement for ``svg.character_svg`` in the sprite style."""
    path = find_sprite(project, character)
    if path is None:
        return ""
    payload, px_w, px_h = _load(path)
    x = _num(character.get("x"), 640)
    y = _num(character.get("y"), 220)
    scale = _num(character.get("scale"), 0.7)
    rotation = _num(character.get("rotation"), 0)

    if time is not None and settings is not None:
        state = character_state(character, time, settings, order)
        x, y, scale = state["x"], state["y"] + state["bob"], state["scale"]

    base = sprite_dir(project)
    height = _atlas(base).get(path.stem, SPRITE_FIGURE_HEIGHT) if base else SPRITE_FIGURE_HEIGHT
    width = height * (px_w / px_h) if px_h else height
    top = y + scale * (SPRITE_FOOT_OFFSET - height)
    inner = (f'<image x="{x - scale * width / 2:g}" y="{top:g}" '
             f'width="{scale * width:g}" height="{scale * height:g}" '
             f'preserveAspectRatio="xMidYMid meet" '
             f'href="data:image/png;base64,{payload}"/>')
    if rotation:
        return f'<g transform="rotate({rotation:g} {x:g} {y:g})">{inner}</g>'
    return inner
