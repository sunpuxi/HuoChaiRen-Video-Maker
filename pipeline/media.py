from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Optional, Sequence


class CommandError(RuntimeError):
    pass


def require_commands(commands: Sequence[str]) -> None:
    missing = [item for item in commands if shutil.which(item) is None]
    if missing:
        raise RuntimeError(f"Missing required commands: {', '.join(missing)}")


def run(command: Sequence[str], *, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        rendered = " ".join(str(item) for item in command)
        detail = result.stderr.strip() or result.stdout.strip()
        raise CommandError(f"Command failed ({result.returncode}): {rendered}\n{detail}")
    return result


def probe(path: Path) -> dict[str, Any]:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def duration(path: Path) -> float:
    data = probe(path)
    raw = data.get("format", {}).get("duration")
    if raw is None:
        for stream in data.get("streams", []):
            if stream.get("duration") is not None:
                raw = stream["duration"]
                break
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Could not determine media duration: {path}") from exc
    if value <= 0:
        raise RuntimeError(f"Media duration is zero: {path}")
    return value


def loudness(path: Path) -> dict[str, float]:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    text = f"{result.stdout}\n{result.stderr}"
    found: dict[str, float] = {}
    for key in ("mean_volume", "max_volume"):
        match = re.search(rf"{key}:\s*(-?[0-9.]+)\s*dB", text)
        if match:
            found[key] = float(match.group(1))
    if "max_volume" not in found:
        raise RuntimeError(f"Could not measure audio loudness: {path}")
    return found


def convert_audio(source: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ar",
            "48000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )


def compose_pcm_audio(parts: Sequence[tuple[Path, float]], output: Path) -> None:
    """Join normalized PCM WAV files and append per-part silence without re-encoding."""
    if not parts:
        raise ValueError("audio composition requires at least one part")
    output.parent.mkdir(parents=True, exist_ok=True)
    expected: Optional[tuple[int, int, int, str, str]] = None
    with wave.open(str(output), "wb") as writer:
        for path, pause_after in parts:
            with wave.open(str(path), "rb") as reader:
                params = (
                    reader.getnchannels(),
                    reader.getsampwidth(),
                    reader.getframerate(),
                    reader.getcomptype(),
                    reader.getcompname(),
                )
                if expected is None:
                    expected = params
                    writer.setnchannels(params[0])
                    writer.setsampwidth(params[1])
                    writer.setframerate(params[2])
                    writer.setcomptype(params[3], params[4])
                elif params != expected:
                    raise RuntimeError(f"Audio parameters do not match while composing {path}")
                writer.writeframes(reader.readframes(reader.getnframes()))
                silence_frames = round(max(0.0, pause_after) * params[2])
                if silence_frames:
                    writer.writeframes(b"\x00" * silence_frames * params[0] * params[1])


def burn_ass_subtitles(
    source: Path,
    subtitles: Path,
    output: Path,
    *,
    quality: str,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    preset = "veryfast" if quality == "preview" else "medium"
    crf = "27" if quality == "preview" else "18"
    escaped = str(subtitles.resolve()).replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"ass=filename='{escaped}'",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            crf,
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )


def ffmpeg_filter_available(name: str) -> bool:
    result = run(["ffmpeg", "-hide_banner", "-filters"])
    return any(line.split()[1:2] == [name] for line in result.stdout.splitlines())


def burn_subtitle_overlays(
    source: Path,
    overlays: Sequence[tuple[Path, float, float]],
    output: Path,
    *,
    quality: str,
) -> None:
    if not overlays:
        raise ValueError("subtitle overlay burn requires at least one overlay")
    output.parent.mkdir(parents=True, exist_ok=True)
    preset = "veryfast" if quality == "preview" else "medium"
    crf = "27" if quality == "preview" else "18"
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    for image, _, _ in overlays:
        command += ["-loop", "1", "-framerate", "1", "-i", str(image)]

    filters: list[str] = []
    previous = "0:v"
    for index, (_, start, end) in enumerate(overlays, start=1):
        output_label = f"sub{index}"
        filters.append(
            f"[{previous}][{index}:v]overlay=0:0:shortest=1:"
            f"enable='between(t,{start:.3f},{end:.3f})'[{output_label}]"
        )
        previous = output_label

    command += [
        "-filter_complex",
        ";".join(filters),
        "-map",
        f"[{previous}]",
        "-map",
        "0:a:0",
        "-t",
        f"{duration(source):.3f}",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        crf,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output),
    ]
    run(command)


def rasterize_svg(
    renderer_script: Path,
    svg_path: Path,
    png_path: Path,
    width: int,
    height: int,
    fonts: Sequence[Path],
    transparent: bool = False,
) -> None:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "node",
            str(renderer_script),
            str(svg_path),
            str(png_path),
            str(width),
            str(height),
            *(["--transparent"] if transparent else []),
            *(str(item) for item in fonts),
        ]
    )


def align_duration_to_frames(clip_duration: float, fps: int) -> float:
    """Snap a clip duration onto the encoder's frame grid.

    A clip can only contain whole frames, so a duration that falls between two
    frame boundaries makes the encoded video shorter than its container
    duration. That deficit accumulates across scenes and drags the average
    frame rate of the concatenated output below the target, which short videos
    notice most. Rounding up costs less than one frame of silence and never
    truncates narration.
    """
    if fps <= 0:
        raise ValueError("fps must be positive")
    frames = max(1, math.ceil(clip_duration * fps - 1e-6))
    return frames / fps


def _video_filter(camera: str, width: int, height: int, fps: int, clip_duration: float) -> str:
    frames = max(1, round(clip_duration * fps))
    fade = (
        f"fade=t=in:st=0:d=0.12,"
        f"fade=t=out:st={max(0, clip_duration - 0.12):.3f}:d=0.12,"
        "format=yuv420p"
    )
    if camera == "static":
        # A truly static scene should not pass through zoompan: even a nominal
        # 1.0 zoom can introduce rounding changes on thin ink strokes.
        return f"scale={width}:{height}:flags=lanczos,{fade}"

    denominator = max(1, frames - 1)
    progress = f"min(1,on/{denominator})"
    ease = f"({progress})*({progress})*(3-2*({progress}))"
    center_x = "trunc(((iw-iw/zoom)/2)/2)*2"
    center_y = "trunc(((ih-ih/zoom)/2)/2)*2"

    if camera == "zoom_out":
        zoom = f"1.045-0.045*({ease})"
        x = center_x
    elif camera == "pan_left":
        zoom = "1.06"
        x = f"trunc(((iw-iw/zoom)*(1-({ease})))/2)*2"
    elif camera == "pan_right":
        zoom = "1.06"
        x = f"trunc(((iw-iw/zoom)*({ease}))/2)*2"
    else:
        zoom = f"1+0.045*({ease})"
        x = center_x
    return (
        f"zoompan=z='{zoom}':x='{x}':y='{center_y}':"
        f"d=1:s={width}x{height}:fps={fps},"
        f"{fade}"
    )


def render_scene_clip(
    image: Path,
    audio: Optional[Path],
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    clip_duration: float,
    camera: str,
    quality: str,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    clip_duration = align_duration_to_frames(clip_duration, fps)
    video_filter = _video_filter(camera, width, height, fps, clip_duration)
    preset = "veryfast" if quality == "preview" else "medium"
    crf = "27" if quality == "preview" else "18"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(image),
    ]
    if audio:
        command += ["-i", str(audio)]
    else:
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono"]
    command += [
        "-filter_complex",
        f"[0:v]{video_filter}[v];[1:a]aresample=48000,apad=pad_dur={clip_duration:.3f}[a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        f"{clip_duration:.3f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        crf,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(output),
    ]
    run(command)


def concatenate(clips: Sequence[Path], output: Path, manifest: Path) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "\n".join(f"file '{item.resolve()}'" for item in clips) + "\n",
        encoding="utf-8",
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )


def rasterize_svg_batch(
    renderer_script: Path,
    items: Sequence[tuple[Path, Path]],
    width: int,
    height: int,
    *,
    transparent: bool = False,
    fonts: Sequence[Path] = (),
) -> None:
    """Rasterise a whole frame sequence in one process.

    Starting a renderer per frame would reload the system fonts every time,
    which costs more than the drawing itself.
    """
    if not items:
        return
    batch_script = renderer_script.with_name("render_svg_batch.cjs")
    with tempfile.TemporaryDirectory(prefix="stickman-batch-") as temp:
        manifest = Path(temp) / "manifest.json"
        manifest.write_text(
            json.dumps(
                [
                    {
                        "svg": str(svg_path),
                        "png": str(png_path),
                        "width": width,
                        "height": height,
                        "transparent": transparent,
                        "fonts": [str(item) for item in fonts],
                    }
                    for svg_path, png_path in items
                ]
            ),
            encoding="utf-8",
        )
        run(["node", str(batch_script), str(manifest)])


def render_animated_clip(
    base_image: Path,
    frame_pattern: Path,
    audio: Optional[Path],
    output: Path,
    *,
    width: int,
    height: int,
    fps: int,
    clip_duration: float,
    camera: str,
    quality: str,
) -> None:
    """Composite a static base layer with one transparent frame per tick."""
    output.parent.mkdir(parents=True, exist_ok=True)
    clip_duration = align_duration_to_frames(clip_duration, fps)
    video_filter = _video_filter(camera, width, height, fps, clip_duration)
    preset = "veryfast" if quality == "preview" else "medium"
    crf = "27" if quality == "preview" else "18"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-i",
        str(base_image),
        "-framerate",
        str(fps),
        "-i",
        str(frame_pattern),
    ]
    if audio:
        command += ["-i", str(audio)]
    else:
        command += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono"]
    command += [
        "-filter_complex",
        f"[0:v][1:v]overlay=0:0:format=auto[composited];"
        f"[composited]{video_filter}[v];"
        f"[2:a]aresample=48000,apad=pad_dur={clip_duration:.3f}[a]",
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-t",
        f"{clip_duration:.3f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        crf,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(output),
    ]
    run(command)


def contact_sheet(video: Path, output: Path, timestamps: Sequence[float]) -> None:
    if not timestamps:
        raise ValueError("contact sheet requires at least one timestamp")
    columns = min(3, len(timestamps))
    rows = math.ceil(len(timestamps) / columns)
    frame_count = columns * rows
    with tempfile.TemporaryDirectory(prefix="stickman-contact-") as temp:
        temp_dir = Path(temp)
        for index, timestamp in enumerate(timestamps):
            frame = temp_dir / f"frame_{index:03d}.png"
            run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=480:270:force_original_aspect_ratio=decrease,pad=480:270:(ow-iw)/2:(oh-ih)/2:white",
                    str(frame),
                ]
            )
        for index in range(len(timestamps), frame_count):
            frame = temp_dir / f"frame_{index:03d}.png"
            run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=white:s=480x270",
                    "-frames:v",
                    "1",
                    str(frame),
                ]
            )
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-framerate",
                "1",
                "-start_number",
                "0",
                "-i",
                str(temp_dir / "frame_%03d.png"),
                "-vf",
                f"tile={columns}x{rows}:nb_frames={frame_count}:padding=8:margin=8:color=white",
                "-frames:v",
                "1",
                str(output),
            ]
        )


def frame_rate_tolerance(output_duration: float, fps: int) -> float:
    """Allowed deviation between measured and target average frame rate.

    Frame counts are whole numbers while scene durations are not, so a timeline
    can legitimately sit up to one frame away from the nominal cadence. On a
    short video that shows up as a large *average* deviation. Scale the
    allowance with the timeline so a sub-frame rounding artefact is not
    reported as a broken encode, while a wrong cadence still fails loudly.
    """
    return max(0.1, fps * 0.005, 1.0 / max(output_duration, 1e-3))


def validate_video(path: Path, width: int, height: int, fps: int, muted: bool) -> dict[str, Any]:
    data = probe(path)
    streams = data.get("streams", [])
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not video:
        raise RuntimeError("Output has no video stream")
    if int(video.get("width", 0)) != width or int(video.get("height", 0)) != height:
        raise RuntimeError(
            f"Unexpected output size {video.get('width')}x{video.get('height')}; expected {width}x{height}"
        )
    # Concat-copy can advertise a high codec time-base-derived r_frame_rate
    # even though the actual presentation cadence is correct. avg_frame_rate
    # reflects the rendered timeline and is the meaningful QA signal here.
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate", "0/1")
    numerator, denominator = (int(item) for item in rate.split("/"))
    actual_fps = numerator / denominator if denominator else 0
    output_duration = duration(path)
    # AAC/video concat boundaries can shift the average by a fraction of a
    # frame per second even when every source clip is rendered at the target.
    tolerance = frame_rate_tolerance(output_duration, fps)
    if abs(actual_fps - fps) > tolerance:
        raise RuntimeError(f"Unexpected frame rate {actual_fps}; expected {fps}")
    if not audio:
        raise RuntimeError("Output has no audio stream")
    audio_levels = None if muted else loudness(path)
    if audio_levels and audio_levels["max_volume"] < -60:
        raise RuntimeError(f"Output audio appears silent: {audio_levels['max_volume']} dB")
    return {
        "duration": output_duration,
        "width": width,
        "height": height,
        "fps": actual_fps,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "audio_levels": audio_levels,
        "size_bytes": path.stat().st_size,
    }
