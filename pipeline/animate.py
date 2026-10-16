"""Procedural, time-based animation for stickman scenes.

The renderer still draws one SVG per frame; this module only decides *where*
every animated part is at time ``t``. Everything here is a pure function of the
scene description and the timestamp, so the same scene always renders the same
frames and the clip cache stays meaningful.
"""
from __future__ import annotations

import math
from typing import Any

# Props without any text can be rasterised per frame without loading fonts.
# node/bubble carry labels, so they stay on the static layer.
PROP_PRESETS = ("none", "pop", "grow")
PROP_DEFAULTS: dict[str, Any] = {"preset": "none", "start": 0.0, "duration": 0.45}
ENTER_PRESETS = ("walk_in", "slide_in", "pop_in")

# Bump whenever the frame generation changes shape (layer split, easing, limb
# maths). It is part of the clip cache key, so stale clips cannot be reused.
ANIMATION_VERSION = 4

BACKGROUND_RECT = '<rect width="1280" height="720"'


def strip_background(svg: str) -> str:
    """Drop the opaque background rect so a layer composites with alpha.

    The animated layer is drawn on top of the static base, so it must stay
    transparent; otherwise it hides the title, the backdrop and every text prop
    that lives on the base layer.
    """
    start = svg.find(BACKGROUND_RECT)
    if start < 0:
        return svg
    end = svg.find("/>", start)
    if end < 0:
        return svg
    return svg[:start] + svg[end + 2:]

SCENE_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "blink": True,
    "blink_period": 3.1,
    "oversample": 2,
    "walk_cycle": True,
}

CHARACTER_DEFAULTS: dict[str, Any] = {
    "preset": "none",
    "start": 0.0,
    "duration": 1.1,
    "from_x": -180.0,
    "walk_speed": 1.7,
    "walk_cycle": None,
    "blink": None,
}


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def ease_out_cubic(progress: float) -> float:
    return 1 - (1 - progress) ** 3


def ease_out_back(progress: float) -> float:
    """Overshoot then settle; used by the pop entrance."""
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (progress - 1) ** 3 + c1 * (progress - 1) ** 2


def progress(t: float, start: float, duration: float) -> float:
    if duration <= 0:
        return 1.0
    return min(1.0, max(0.0, (t - start) / duration))


def blink_amount(t: float, period: float = 3.1, closed: float = 0.14) -> float:
    """0 = eyes open, 1 = eyes shut."""
    if period <= 0:
        return 0.0
    phase = t % period
    if phase >= closed:
        return 0.0
    return math.sin(math.pi * phase / closed)


def scene_animation(scene: dict[str, Any]) -> dict[str, Any] | None:
    """Resolved scene animation settings, or None when the scene stays still."""
    raw = scene.get("animation")
    if raw is None or raw is False:
        return None
    if raw is True:
        raw = {}
    if not isinstance(raw, dict):
        return None
    settings = dict(SCENE_DEFAULTS)
    settings.update({key: value for key, value in raw.items() if key in SCENE_DEFAULTS})
    return settings if settings["enabled"] else None


def character_animation(character: dict[str, Any]) -> dict[str, Any]:
    raw = character.get("animation")
    if raw is True:
        raw = {}
    settings = dict(CHARACTER_DEFAULTS)
    if isinstance(raw, dict):
        settings.update({key: value for key, value in raw.items() if key in CHARACTER_DEFAULTS})
    if settings["preset"] not in ENTER_PRESETS:
        settings["preset"] = "none"
    return settings


def prop_animation(prop: dict[str, Any]) -> dict[str, Any]:
    raw = prop.get("animation")
    settings = dict(PROP_DEFAULTS)
    if isinstance(raw, dict):
        settings.update({key: value for key, value in raw.items() if key in PROP_DEFAULTS})
    if settings["preset"] not in PROP_PRESETS:
        settings["preset"] = "none"
    return settings


def prop_is_animated(prop: dict[str, Any]) -> bool:
    return prop_animation(prop)["preset"] != "none"


def prop_state(prop: dict[str, Any], t: float) -> dict[str, Any]:
    """Visibility, scale and grow progress of one prop at time ``t``."""
    settings = prop_animation(prop)
    preset = settings["preset"]
    if preset == "none":
        return {"visible": True, "scale": 1.0, "grow": 1.0}
    advanced = progress(t, settings["start"], settings["duration"])
    if preset == "pop":
        return {"visible": advanced > 0, "scale": ease_out_back(advanced) if advanced > 0 else 0.0,
                "grow": advanced}
    return {"visible": advanced > 0, "scale": 1.0, "grow": ease_out_cubic(advanced)}


def has_animated_content(scene: dict[str, Any]) -> bool:
    """True when this scene needs one render per frame."""
    settings = scene_animation(scene)
    if settings is None:
        return False
    if settings["blink"]:
        return True
    if any(prop_is_animated(item) for item in scene.get("props", [])):
        return True
    return any(
        character_animation(item)["preset"] != "none"
        for item in scene.get("characters", [])
    )


def animation_signature(scene: dict[str, Any]) -> dict[str, Any]:
    """Cache-key material for the animated render path."""
    return {
        "version": ANIMATION_VERSION,
        "scene": scene_animation(scene),
        "characters": [character_animation(item) for item in scene.get("characters", [])],
        "props": [prop_animation(item) for item in scene.get("props", [])],
    }


def split_layers(scene: dict[str, Any], project: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a scene into a static base layer and a transparent animated layer.

    Text stays on the base layer: the animated layer is rasterised without a
    system font scan, which is what makes per-frame rendering affordable.
    """
    shared = {key: value for key, value in scene.items() if key not in ("characters", "props")}
    animated_props = [item for item in scene.get("props", []) if prop_is_animated(item)]

    base = dict(shared)
    base["background_style"] = _backdrop(scene, project, characters=False)
    base["props"] = [item for item in scene.get("props", []) if not prop_is_animated(item)]
    base["characters"] = []

    layer = dict(shared)
    layer.update({"title": "", "caption": "", "narration": "", "connections": []})
    layer["background_style"] = _backdrop(scene, project, characters=True)
    layer["props"] = animated_props
    layer["characters"] = scene.get("characters", [])
    return base, layer


def _backdrop(scene: dict[str, Any], project: dict[str, Any], *, characters: bool) -> dict[str, Any]:
    """Keep the paper shape/ground on the base layer and shadows on the characters."""
    source = scene.get("background_style")
    if source is None:
        source = project.get("background_style")
    settings: dict[str, Any] = dict(source) if isinstance(source, dict) else {"enabled": bool(source)}
    if not isinstance(source, (dict, bool)) or source is False:
        settings = {"enabled": False}
    settings.setdefault("enabled", True)
    if characters:
        return {**settings, "paper_shape": "none", "ground": False, "title_stroke": False,
                "character_shadows": settings.get("character_shadows", True)}
    return {**settings, "character_shadows": False}


def character_state(
    character: dict[str, Any],
    t: float,
    settings: dict[str, Any],
    order: int = 0,
) -> dict[str, Any]:
    """Geometry and expression state for one character at time ``t``."""
    base_x = _num(character.get("x"), 640)
    base_y = _num(character.get("y"), 220)
    scale = _num(character.get("scale"), 0.7)
    animation = character_animation(character)
    preset = animation["preset"]

    state = {
        "x": base_x,
        "y": base_y,
        "scale": scale,
        "pose": str(character.get("pose", "stand")),
        "walking": False,
        "walk_phase": 0.0,
        "bob": 0.0,
        "blink": 0.0,
    }

    if preset in ("walk_in", "slide_in"):
        moved = ease_out_cubic(progress(t, animation["start"], animation["duration"]))
        from_x = _num(animation["from_x"], -180.0)
        state["x"] = from_x + (base_x - from_x) * moved
        arriving = progress(t, animation["start"], animation["duration"]) < 1.0
        keep_walking = animation["walk_cycle"]
        if keep_walking is None:
            keep_walking = settings["walk_cycle"]
        state["walking"] = preset == "walk_in" and (arriving or bool(keep_walking))
    elif preset == "pop_in":
        popped = progress(t, animation["start"], animation["duration"])
        state["scale"] = scale * ease_out_back(popped) if popped > 0 else 0.0

    if state["walking"]:
        speed = _num(animation["walk_speed"], 1.7)
        state["walk_phase"] = 2 * math.pi * speed * t
        # Two steps per cycle: the body rises when a foot plants.
        state["bob"] = -4.5 * abs(math.sin(state["walk_phase"]))
        state["pose"] = "walk"

    blink_enabled = animation["blink"]
    if blink_enabled is None:
        blink_enabled = settings["blink"]
    if blink_enabled:
        # Stagger the phase per character so a crowd does not blink in unison.
        state["blink"] = blink_amount(t + order * 0.37, _num(settings["blink_period"], 3.1))

    return state
