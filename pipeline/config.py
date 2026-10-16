from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Union
from urllib.parse import urlparse

from .svg import ICONS
from .tts import DEFAULT_DASHSCOPE_ENDPOINT


ALLOWED_POSES = {"stand", "look_down", "point", "arms_up", "think", "walk"}
ALLOWED_EXPRESSIONS = {"neutral", "smile", "confused", "surprised", "sad", "angry"}
ALLOWED_CAMERAS = {"static", "zoom_in", "zoom_out", "pan_left", "pan_right"}
ALLOWED_PROPS = {"arrow", "cross", "bubble", "node", "mirror", "arc", "star", "icon", "exchange"}
ALLOWED_PROP_PRESETS = {"none", "pop", "grow"}
PROP_ANIMATION_KEYS = {"preset", "start", "duration"}
ALLOWED_ANIMATION_PRESETS = {"none", "walk_in", "slide_in", "pop_in"}
SCENE_ANIMATION_KEYS = {"enabled", "blink", "blink_period", "oversample", "walk_cycle"}
CHARACTER_ANIMATION_KEYS = {"preset", "start", "duration", "from_x", "walk_speed", "walk_cycle", "blink"}
ALLOWED_PAPER_SHAPES = {
    "auto",
    "none",
    "circle_top_right",
    "circle_bottom_left",
    "band_bottom",
    "circle_top_left",
}
BACKDROP_KEYS = {"enabled", "paper_shape", "ground", "title_stroke", "character_shadows"}
ALLOWED_TTS_ENGINES = {"macos", "dashscope_qwen3"}
ALLOWED_QWEN_LANGUAGES = {
    "Auto",
    "Chinese",
    "English",
    "German",
    "Italian",
    "Portuguese",
    "Spanish",
    "Japanese",
    "Korean",
    "French",
    "Russian",
}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing configuration file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def resolve_project(path: Union[str, Path]) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    project_dir = Path(path).expanduser().resolve()
    project = load_json(project_dir / "project.json")
    storyboard = load_json(project_dir / "storyboard.json")
    validate(project, storyboard, project_dir)
    return project_dir, project, storyboard


def _resolution(value: Any, label: str, errors: list[str]) -> None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or not all(isinstance(item, int) and item > 0 for item in value)
    ):
        errors.append(f"{label} must be [positive_width, positive_height]")


def _animation(value: Any, label: str, errors: list[str], *, keys: set, allow_bool: bool) -> None:
    if value is None:
        return
    if isinstance(value, bool):
        if not allow_bool:
            errors.append(f"{label} must be an object")
        return
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    for key, item in value.items():
        if key not in keys:
            errors.append(f"{label}.{key} is not supported")
            continue
        if key == "preset":
            if item not in ALLOWED_ANIMATION_PRESETS:
                errors.append(f"{label}.preset is not supported: {item}")
        elif key == "oversample":
            if not isinstance(item, int) or not 1 <= item <= 6:
                errors.append(f"{label}.oversample must be an integer between 1 and 6")
        elif key in {"blink", "walk_cycle", "enabled"}:
            if not isinstance(item, bool):
                errors.append(f"{label}.{key} must be a boolean")
        elif key == "from_x":
            # Characters can enter from off-screen, so this one may be negative.
            if not isinstance(item, (int, float)):
                errors.append(f"{label}.from_x must be a number")
        elif not isinstance(item, (int, float)) or item < 0:
            errors.append(f"{label}.{key} must be zero or greater")


def _backdrop(value: Any, label: str, errors: list[str]) -> None:
    if value is None or isinstance(value, bool):
        return
    if not isinstance(value, dict):
        errors.append(f"{label} must be a boolean or an object")
        return
    for key, item in value.items():
        if key not in BACKDROP_KEYS:
            errors.append(f"{label}.{key} is not supported")
        elif key == "paper_shape":
            if item not in ALLOWED_PAPER_SHAPES:
                errors.append(f"{label}.paper_shape is not supported: {item}")
        elif not isinstance(item, bool):
            errors.append(f"{label}.{key} must be a boolean")


def validate(project: dict[str, Any], scenes: Any, project_dir: Path) -> None:
    errors: list[str] = []
    if not isinstance(project, dict):
        raise ValueError("project.json must contain an object")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("storyboard.json must contain a non-empty array")

    _resolution(project.get("resolution"), "project.resolution", errors)
    preview = project.get("preview", {})
    _resolution(preview.get("resolution"), "project.preview.resolution", errors)
    for key, value in (("fps", project.get("fps")), ("preview.fps", preview.get("fps"))):
        if not isinstance(value, int) or value <= 0:
            errors.append(f"{key} must be a positive integer")

    _backdrop(project.get("background_style"), "project.background_style", errors)

    voice = project.get("voice", {})
    if not isinstance(voice, dict):
        errors.append("project.voice must be an object")
    else:
        engine = voice.get("engine", "macos")
        if engine not in ALLOWED_TTS_ENGINES:
            errors.append(f"project.voice.engine is not supported: {engine}")
        elif engine == "macos":
            rate = voice.get("rate", 210)
            if not isinstance(rate, int) or rate <= 0:
                errors.append("project.voice.rate must be a positive integer")
        else:
            for key, default in (("model", "qwen3-tts-flash"), ("voice", "Cherry")):
                value = voice.get(key, default)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"project.voice.{key} must be a non-empty string")
            language = voice.get("language_type", "Chinese")
            if language not in ALLOWED_QWEN_LANGUAGES:
                errors.append(f"project.voice.language_type is not supported: {language}")
            api_key_env = voice.get("api_key_env", "DASHSCOPE_API_KEY")
            if not isinstance(api_key_env, str) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", api_key_env
            ):
                errors.append("project.voice.api_key_env must be an environment variable name")
            endpoint = voice.get("endpoint", DEFAULT_DASHSCOPE_ENDPOINT)
            try:
                parsed_endpoint = urlparse(endpoint) if isinstance(endpoint, str) else None
                valid_endpoint = bool(
                    parsed_endpoint
                    and parsed_endpoint.scheme in {"http", "https"}
                    and parsed_endpoint.netloc
                )
            except ValueError:
                valid_endpoint = False
            if not valid_endpoint:
                errors.append("project.voice.endpoint must be an HTTP(S) URL")
            timeout = voice.get("timeout", 90)
            if not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 300:
                errors.append("project.voice.timeout must be greater than 0 and at most 300")
            retries = voice.get("max_retries", 3)
            if not isinstance(retries, int) or not 1 <= retries <= 5:
                errors.append("project.voice.max_retries must be an integer from 1 to 5")

    seen: set[str] = set()
    for index, scene in enumerate(scenes):
        prefix = f"scene[{index}]"
        if not isinstance(scene, dict):
            errors.append(f"{prefix} must be an object")
            continue
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not re.fullmatch(r"[a-z0-9_-]+", scene_id):
            errors.append(f"{prefix}.id must match [a-z0-9_-]+")
        elif scene_id in seen:
            errors.append(f"Duplicate scene id: {scene_id}")
        else:
            seen.add(scene_id)

        narration = scene.get("narration", "")
        if not isinstance(narration, str):
            errors.append(f"{prefix}.narration must be a string")

        segments = scene.get("narration_segments")
        if segments is not None:
            if not isinstance(segments, list) or not segments:
                errors.append(f"{prefix}.narration_segments must be a non-empty array")
            else:
                for segment_index, segment in enumerate(segments):
                    segment_prefix = f"{prefix}.narration_segments[{segment_index}]"
                    if not isinstance(segment, dict):
                        errors.append(f"{segment_prefix} must be an object")
                        continue
                    text = segment.get("text")
                    if not isinstance(text, str) or not text.strip():
                        errors.append(f"{segment_prefix}.text must be a non-empty string")
                    caption = segment.get("caption")
                    if caption is not None and not isinstance(caption, str):
                        errors.append(f"{segment_prefix}.caption must be a string")
                    pause_after = segment.get("pause_after", 0)
                    if not isinstance(pause_after, (int, float)) or pause_after < 0:
                        errors.append(f"{segment_prefix}.pause_after must be zero or greater")
                    segment_audio = segment.get("audio_file")
                    if segment_audio and not (project_dir / segment_audio).exists():
                        errors.append(
                            f"{segment_prefix}.audio_file does not exist: {segment_audio}"
                        )
            if scene.get("audio_file"):
                errors.append(
                    f"{prefix} cannot combine scene.audio_file with narration_segments; "
                    "put audio_file on each segment instead"
                )

        if not narration and not segments and not scene.get("audio_file") and not scene.get("duration"):
            errors.append(f"{prefix} needs narration, narration_segments, audio_file, or duration")

        camera = scene.get("camera", "zoom_in")
        if camera not in ALLOWED_CAMERAS:
            errors.append(f"{prefix}.camera is not supported: {camera}")

        for char_index, character in enumerate(scene.get("characters", [])):
            if character.get("pose", "stand") not in ALLOWED_POSES:
                errors.append(f"{prefix}.characters[{char_index}].pose is unsupported")
            if character.get("expression", "neutral") not in ALLOWED_EXPRESSIONS:
                errors.append(f"{prefix}.characters[{char_index}].expression is unsupported")
            _animation(
                character.get("animation"),
                f"{prefix}.characters[{char_index}].animation",
                errors,
                keys=CHARACTER_ANIMATION_KEYS,
                allow_bool=True,
            )

        for prop_index, prop in enumerate(scene.get("props", [])):
            prop_prefix = f"{prefix}.props[{prop_index}]"
            if prop.get("type") not in ALLOWED_PROPS:
                errors.append(f"{prop_prefix}.type is unsupported: {prop.get('type')}")
            elif prop.get("type") == "icon" and prop.get("name") not in ICONS:
                errors.append(
                    f"{prop_prefix}.name is not a known icon: {prop.get('name')} "
                    f"(available: {', '.join(sorted(ICONS))})"
                )
            prop_animation = prop.get("animation")
            if prop_animation is not None:
                if not isinstance(prop_animation, dict):
                    errors.append(f"{prop_prefix}.animation must be an object")
                else:
                    for key, item in prop_animation.items():
                        if key not in PROP_ANIMATION_KEYS:
                            errors.append(f"{prop_prefix}.animation.{key} is not supported")
                        elif key == "preset" and item not in ALLOWED_PROP_PRESETS:
                            errors.append(f"{prop_prefix}.animation.preset is not supported: {item}")
                        elif key != "preset" and (not isinstance(item, (int, float)) or item < 0):
                            errors.append(f"{prop_prefix}.animation.{key} must be zero or greater")

        _backdrop(scene.get("background_style"), f"{prefix}.background_style", errors)
        paper_shape = scene.get("paper_shape")
        if paper_shape is not None and paper_shape not in ALLOWED_PAPER_SHAPES:
            errors.append(f"{prefix}.paper_shape is not supported: {paper_shape}")

        _animation(scene.get("animation"), f"{prefix}.animation", errors,
                   keys=SCENE_ANIMATION_KEYS, allow_bool=True)

        audio_file = scene.get("audio_file")
        if audio_file and not (project_dir / audio_file).exists():
            errors.append(f"{prefix}.audio_file does not exist: {audio_file}")

    if errors:
        formatted = "\n".join(f"- {item}" for item in errors)
        raise ValueError(f"Configuration validation failed:\n{formatted}")
