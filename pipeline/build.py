from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import PIPELINE_VERSION
from .config import resolve_project
from .media import (
    align_duration_to_frames,
    burn_ass_subtitles,
    burn_subtitle_overlays,
    concatenate,
    compose_pcm_audio,
    contact_sheet,
    convert_audio,
    duration,
    ffmpeg_filter_available,
    loudness,
    rasterize_svg,
    render_scene_clip,
    require_commands,
    run,
    validate_video,
)
from .subtitles import render_subtitle_svg, write_ass, write_srt
from .svg import render_scene_svg
from .tts import synthesize_dashscope_qwen3, tts_cache_signature


@dataclass
class CacheStats:
    audio_hits: int = 0
    audio_misses: int = 0
    image_hits: int = 0
    image_misses: int = 0
    clip_hits: int = 0
    clip_misses: int = 0
    subtitle_hits: int = 0
    subtitle_misses: int = 0


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:20]


def _ensure_audio(
    scene: dict[str, Any],
    project: dict[str, Any],
    project_dir: Path,
    cache_dir: Path,
    stats: CacheStats,
    force: bool,
) -> Path:
    tts = project.get("voice", {})
    source_path: Optional[Path] = None
    if scene.get("audio_file"):
        source_path = (project_dir / scene["audio_file"]).resolve()
        signature = {
            "kind": "source",
            "file": _file_digest(source_path),
            "pipeline": PIPELINE_VERSION,
        }
    else:
        signature = {
            "kind": "tts",
            "text": scene.get("narration", ""),
            "settings": tts_cache_signature(tts),
            "pipeline": PIPELINE_VERSION,
        }

    output = cache_dir / "audio" / f"{_digest(signature)}.wav"
    if output.exists() and not force:
        try:
            if duration(output) > 0 and loudness(output)["max_volume"] > -60:
                stats.audio_hits += 1
                return output
        except RuntimeError:
            pass

    stats.audio_misses += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    if source_path:
        convert_audio(source_path, output)
    else:
        text = scene.get("narration", "").strip()
        if not text:
            raise RuntimeError(f"Scene {scene['id']} has no narration for TTS")
        with tempfile.TemporaryDirectory(prefix="stickman-tts-") as temp_dir:
            engine = tts.get("engine", "macos")
            if engine == "dashscope_qwen3":
                raw = Path(temp_dir) / "voice.wav"
                synthesize_dashscope_qwen3(text, raw, tts)
            else:
                raw = Path(temp_dir) / "voice.aiff"
                run(
                    [
                        "say",
                        "-v",
                        str(tts.get("name", "Tingting")),
                        "-r",
                        str(int(tts.get("rate", 210))),
                        "-o",
                        str(raw),
                        text,
                    ]
                )
            try:
                if duration(raw) <= 0 or loudness(raw)["max_volume"] < -60:
                    raise RuntimeError("generated audio is empty")
            except RuntimeError as exc:
                if engine == "macos":
                    raise RuntimeError(
                        "macOS speech synthesis produced an empty track. Run outside a restricted "
                        "sandbox or provide scene.audio_file."
                    ) from exc
                raise RuntimeError("DashScope Qwen3-TTS produced an invalid audio track") from exc
            convert_audio(raw, output)

    levels = loudness(output)
    if levels["max_volume"] < -60:
        output.unlink(missing_ok=True)
        raise RuntimeError(f"Audio source for {scene['id']} appears silent")
    return output


def _ensure_segmented_audio(
    scene: dict[str, Any],
    project: dict[str, Any],
    project_dir: Path,
    cache_dir: Path,
    stats: CacheStats,
    force: bool,
) -> tuple[Path, float, list[dict[str, Any]]]:
    parts: list[tuple[Path, float]] = []
    cues: list[dict[str, Any]] = []
    clock = 0.0
    for index, segment in enumerate(scene["narration_segments"]):
        segment_scene = {
            "id": f"{scene['id']} segment {index + 1}",
            "narration": segment["text"],
        }
        if segment.get("audio_file"):
            segment_scene["audio_file"] = segment["audio_file"]
        audio = _ensure_audio(
            segment_scene,
            project,
            project_dir,
            cache_dir,
            stats,
            force,
        )
        spoken_duration = duration(audio)
        pause_after = float(segment.get("pause_after", 0))
        cue_end = clock + spoken_duration + pause_after
        cues.append(
            {
                "text": segment.get("caption", segment["text"]),
                "start": clock,
                "end": cue_end,
                "audio_duration": spoken_duration,
            }
        )
        parts.append((audio, pause_after))
        clock = cue_end

    signature = {
        "kind": "segmented",
        "parts": [
            {"audio": _file_digest(path), "pause_after": round(pause, 4)}
            for path, pause in parts
        ],
        "pipeline": PIPELINE_VERSION,
    }
    output = cache_dir / "audio" / f"{_digest(signature)}.wav"
    if force or not output.exists():
        compose_pcm_audio(parts, output)
    total_duration = duration(output)
    if cues:
        cues[-1]["end"] = min(cues[-1]["end"], total_duration)
    return output, total_duration, cues


def _muted_segment_cues(scene: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    cues: list[dict[str, Any]] = []
    clock = 0.0
    for segment in scene.get("narration_segments", []):
        spoken_duration = max(0.8, len(segment["text"].strip()) / 5.0)
        pause_after = float(segment.get("pause_after", 0))
        end = clock + spoken_duration + pause_after
        cues.append(
            {
                "text": segment.get("caption", segment["text"]),
                "start": clock,
                "end": end,
                "audio_duration": spoken_duration,
            }
        )
        clock = end
    return clock, cues


def _copy_if_changed(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and _file_digest(source) == _file_digest(destination):
        return
    shutil.copy2(source, destination)


def build_project(
    project_path: str,
    *,
    quality: str,
    muted: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    project_dir, project, scenes = resolve_project(project_path)
    needs_tts = not muted and any(
        any(not segment.get("audio_file") for segment in scene.get("narration_segments", []))
        if scene.get("narration_segments")
        else bool(scene.get("narration", "").strip()) and not scene.get("audio_file")
        for scene in scenes
    )
    engine = project.get("voice", {}).get("engine", "macos")
    needs_macos_tts = needs_tts and engine == "macos"
    require_commands(["node", "ffmpeg", "ffprobe"] + (["say"] if needs_macos_tts else []))
    renderer = root / "scripts" / "render_svg.cjs"
    if not (root / "node_modules" / "@resvg" / "resvg-js").exists():
        raise RuntimeError(f"Renderer dependency is missing. Run `npm install` in {root}")

    if quality not in {"preview", "final"}:
        raise ValueError("quality must be preview or final")

    if quality == "preview":
        render_config = project["preview"]
        width, height = render_config["resolution"]
        fps = render_config["fps"]
    else:
        width, height = project["resolution"]
        fps = project["fps"]
    oversample = int(project.get("motion_oversample", 4))
    if oversample < 1 or oversample > 6:
        raise ValueError("project.motion_oversample must be between 1 and 6")

    cache_dir = root / ".cache"
    build_dir = root / "builds" / project_dir.name / quality
    scene_dir = build_dir / "scenes"
    scene_dir.mkdir(parents=True, exist_ok=True)
    output_name = project.get("output_name", project_dir.name)
    final_video = build_dir / f"{output_name}_{quality}.mp4"
    clean_video = build_dir / f"{output_name}_{quality}_clean.mp4"
    srt_path = build_dir / "subtitles.srt"
    ass_path = build_dir / "subtitles.ass"
    subtitle_config = project.get("subtitles", {})
    subtitles_enabled = bool(subtitle_config.get("enabled", True))
    fonts = [(project_dir / item).resolve() for item in project.get("font_files", [])]
    for font in fonts:
        if not font.exists():
            raise RuntimeError(f"Configured font does not exist: {font}")

    stats = CacheStats()
    clips: list[Path] = []
    timeline: list[dict[str, Any]] = []
    subtitle_cues: list[dict[str, Any]] = []
    clock = 0.0

    for scene in scenes:
        scene_id = scene["id"]
        audio: Optional[Path] = None
        audio_duration = 0.0
        local_cues: list[dict[str, Any]] = []
        estimated_segment_duration = 0.0
        if not muted:
            if scene.get("narration_segments"):
                audio, audio_duration, local_cues = _ensure_segmented_audio(
                    scene, project, project_dir, cache_dir, stats, force
                )
            elif scene.get("audio_file") or scene.get("narration", "").strip():
                audio = _ensure_audio(scene, project, project_dir, cache_dir, stats, force)
                audio_duration = duration(audio)
        elif scene.get("narration_segments"):
            estimated_segment_duration, local_cues = _muted_segment_cues(scene)

        fallback_duration = max(
            float(scene.get("duration", 0)),
            float(scene.get("min_duration", project.get("minimum_scene_duration", 2.5))),
            estimated_segment_duration,
            len(scene.get("narration", "")) / 5.0 if muted and not local_cues else 0,
        )
        clip_duration = max(
            fallback_duration,
            audio_duration + float(scene.get("tail_pad", project.get("tail_pad", 0.35))),
        )
        # Snap to the frame grid before it reaches the cache key, the encoder
        # and the subtitle clock, so every scene stays on the same timeline.
        clip_duration = align_duration_to_frames(clip_duration, fps)

        if not local_cues:
            caption = str(scene.get("caption", scene.get("narration", ""))).strip()
            if caption:
                caption_duration = audio_duration if audio_duration else clip_duration
                local_cues = [{"text": caption, "start": 0.0, "end": caption_duration}]

        rendered_scene = scene
        if subtitles_enabled and local_cues:
            rendered_scene = dict(scene)
            rendered_scene["caption"] = ""

        svg = render_scene_svg(rendered_scene, project)
        svg_path = scene_dir / f"{scene_id}.svg"
        svg_path.write_text(svg, encoding="utf-8")
        image_signature = {
            "svg": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
            "width": raster_width,
            "height": raster_height,
            "fonts": [_file_digest(item) for item in fonts],
            "renderer": "resvg-2.6.2",
        }
        image_key = _digest(image_signature)
        cached_image = cache_dir / "images" / f"{image_key}.png"
        if cached_image.exists() and not force:
            stats.image_hits += 1
        else:
            stats.image_misses += 1
            rasterize_svg(
                renderer,
                svg_path,
                cached_image,
                raster_width,
                raster_height,
                fonts,
            )
        image_path = scene_dir / f"{scene_id}.png"
        _copy_if_changed(cached_image, image_path)

        clip_signature = {
            "image": image_key,
            "audio": _file_digest(audio) if audio else "muted",
            "duration": round(clip_duration, 4),
            "camera": scene.get("camera", "zoom_in"),
            "width": width,
            "height": height,
            "fps": fps,
            "quality": quality,
            "oversample": oversample,
            "pipeline": PIPELINE_VERSION,
        }
        clip_key = _digest(clip_signature)
        cached_clip = cache_dir / "clips" / f"{clip_key}.mp4"
        if cached_clip.exists() and not force:
            stats.clip_hits += 1
        else:
            stats.clip_misses += 1
            render_scene_clip(
                cached_image,
                audio,
                cached_clip,
                width=width,
                height=height,
                fps=fps,
                clip_duration=clip_duration,
                camera=scene.get("camera", "zoom_in"),
                quality=quality,
            )
        clip_path = scene_dir / f"{scene_id}.mp4"
        _copy_if_changed(cached_clip, clip_path)
        clips.append(clip_path)
        # Video duration is quantized to whole frames. Use the encoded clip's
        # real duration so later scene subtitles start on the actual concat boundary.
        actual_clip_duration = duration(clip_path)
        scene_subtitles = []
        for cue in local_cues:
            cue_start = min(actual_clip_duration, max(0.0, float(cue["start"])))
            cue_end = min(
                actual_clip_duration,
                max(cue_start + 0.1, float(cue["end"])),
            )
            global_cue = {
                "scene_id": scene_id,
                "text": cue["text"],
                "start": round(clock + cue_start, 3),
                "end": round(clock + cue_end, 3),
            }
            subtitle_cues.append(global_cue)
            scene_subtitles.append(
                {
                    "text": cue["text"],
                    "start": round(cue_start, 3),
                    "end": round(cue_end, 3),
                }
            )
        timeline.append(
            {
                "id": scene_id,
                "start": round(clock, 3),
                "duration": round(actual_clip_duration, 3),
                "audio_duration": round(audio_duration, 3),
                "camera": scene.get("camera", "zoom_in"),
                "subtitles": scene_subtitles,
            }
        )
        clock += actual_clip_duration

    concatenate(clips, clean_video, build_dir / "concat.txt")
    write_srt(subtitle_cues, srt_path)
    write_ass(
        subtitle_cues,
        ass_path,
        width=width,
        height=height,
        style=subtitle_config,
    )
    subtitle_burner = "none"
    subtitle_overlays: list[tuple[Path, float, float]] = []
    if subtitles_enabled and subtitle_cues:
        subtitle_burner = "ass" if ffmpeg_filter_available("ass") else "svg-overlay"
        if subtitle_burner == "svg-overlay":
            overlay_dir = build_dir / "subtitle_overlays"
            overlay_dir.mkdir(parents=True, exist_ok=True)
            for index, cue in enumerate(subtitle_cues, start=1):
                overlay_svg = render_subtitle_svg(
                    str(cue["text"]),
                    width=width,
                    height=height,
                    style=subtitle_config,
                )
                svg_path = overlay_dir / f"cue_{index:03d}.svg"
                svg_path.write_text(overlay_svg, encoding="utf-8")
                overlay_signature = {
                    "svg": hashlib.sha256(overlay_svg.encode("utf-8")).hexdigest(),
                    "width": width,
                    "height": height,
                    "fonts": [_file_digest(item) for item in fonts],
                    "renderer": "resvg-2.6.2-transparent",
                    "pipeline": PIPELINE_VERSION,
                }
                cached_overlay = cache_dir / "subtitles" / f"{_digest(overlay_signature)}.png"
                if force or not cached_overlay.exists():
                    rasterize_svg(
                        renderer,
                        svg_path,
                        cached_overlay,
                        width,
                        height,
                        fonts,
                        transparent=True,
                    )
                overlay_path = overlay_dir / f"cue_{index:03d}.png"
                _copy_if_changed(cached_overlay, overlay_path)
                subtitle_overlays.append(
                    (overlay_path, float(cue["start"]), float(cue["end"]))
                )
        subtitle_signature = {
            "video": _file_digest(clean_video),
            "subtitles": _file_digest(ass_path),
            "burner": subtitle_burner,
            "quality": quality,
            "pipeline": PIPELINE_VERSION,
        }
        cached_subtitled = cache_dir / "subtitled" / f"{_digest(subtitle_signature)}.mp4"
        if cached_subtitled.exists() and not force:
            stats.subtitle_hits += 1
        else:
            stats.subtitle_misses += 1
            if subtitle_burner == "ass":
                burn_ass_subtitles(clean_video, ass_path, cached_subtitled, quality=quality)
            else:
                burn_subtitle_overlays(
                    clean_video,
                    subtitle_overlays,
                    cached_subtitled,
                    quality=quality,
                )
        _copy_if_changed(cached_subtitled, final_video)
    else:
        _copy_if_changed(clean_video, final_video)
    validation = validate_video(final_video, width, height, fps, muted)
    sheet = build_dir / "contact_sheet.png"
    contact_sheet(
        final_video,
        sheet,
        [item["start"] + item["duration"] / 2 for item in timeline],
    )

    report = {
        "pipeline_version": PIPELINE_VERSION,
        "project": project.get("title", project_dir.name),
        "quality": quality,
        "muted": muted,
        "motion_oversample": oversample,
        "output": str(final_video),
        "clean_output": str(clean_video),
        "subtitles": {
            "enabled": subtitles_enabled,
            "srt": str(srt_path),
            "ass": str(ass_path),
            "cue_count": len(subtitle_cues),
            "burner": subtitle_burner,
        },
        "contact_sheet": str(sheet),
        "timeline": timeline,
        "validation": validation,
        "cache": stats.__dict__,
    }
    (build_dir / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (build_dir / "build_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report
