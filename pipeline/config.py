from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Union


ALLOWED_POSES = {"stand", "look_down", "point", "arms_up", "think", "walk"}
ALLOWED_EXPRESSIONS = {"neutral", "smile", "confused", "surprised", "sad", "angry"}
ALLOWED_CAMERAS = {"static", "zoom_in", "zoom_out", "pan_left", "pan_right"}
ALLOWED_PROPS = {"arrow", "cross", "bubble", "node", "mirror", "arc", "star"}


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

        for prop_index, prop in enumerate(scene.get("props", [])):
            if prop.get("type") not in ALLOWED_PROPS:
                errors.append(f"{prefix}.props[{prop_index}].type is unsupported")

        audio_file = scene.get("audio_file")
        if audio_file and not (project_dir / audio_file).exists():
            errors.append(f"{prefix}.audio_file does not exist: {audio_file}")

    if errors:
        formatted = "\n".join(f"- {item}" for item in errors)
        raise ValueError(f"Configuration validation failed:\n{formatted}")
