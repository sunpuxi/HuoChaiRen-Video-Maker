from __future__ import annotations

import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from pipeline.config import resolve_project
from pipeline.media import (
    _video_filter,
    align_duration_to_frames,
    compose_pcm_audio,
    concatenate,
    duration,
    frame_rate_tolerance,
    probe,
    render_scene_clip,
    run,
    validate_video,
)
from pipeline.subtitles import write_ass, write_srt
from pipeline.tts import TTSError, synthesize_dashscope_qwen3, tts_cache_signature


class FakeHTTPResponse:
    def __init__(self, content: bytes):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.content
from pipeline.svg import render_scene_svg


class ConfigTests(unittest.TestCase):
    def test_valid_project_loads(self) -> None:
        root = Path(__file__).resolve().parents[1]
        project_dir, project, scenes = resolve_project(root / "projects" / "clothing")
        self.assertEqual(project_dir.name, "clothing")
        self.assertEqual(project["fps"], 30)
        self.assertEqual(len(scenes), 5)

    def test_unsafe_scene_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project.json").write_text(
                json.dumps(
                    {
                        "resolution": [1280, 720],
                        "fps": 30,
                        "preview": {"resolution": [640, 360], "fps": 12},
                    }
                ),
                encoding="utf-8",
            )
            (root / "storyboard.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "../outside",
                            "narration": "test",
                            "duration": 2,
                            "camera": "static",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "must match"):
                resolve_project(root)

    def test_segmented_narration_loads_without_scene_narration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project.json").write_text(
                json.dumps(
                    {
                        "resolution": [1280, 720],
                        "fps": 30,
                        "preview": {"resolution": [640, 360], "fps": 12},
                    }
                ),
                encoding="utf-8",
            )
            (root / "storyboard.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "segmented",
                            "narration_segments": [
                                {"text": "第一句。", "pause_after": 0.1},
                                {"text": "第二句。", "caption": "第二句字幕。"},
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            _, _, scenes = resolve_project(root)
            self.assertEqual(len(scenes[0]["narration_segments"]), 2)

    def test_scene_audio_and_segments_are_rejected_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "voice.wav").write_bytes(b"placeholder")
            (root / "project.json").write_text(
                json.dumps(
                    {
                        "resolution": [1280, 720],
                        "fps": 30,
                        "preview": {"resolution": [640, 360], "fps": 12},
                    }
                ),
                encoding="utf-8",
            )
            (root / "storyboard.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "ambiguous",
                            "audio_file": "voice.wav",
                            "narration_segments": [{"text": "一句话。"}],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "cannot combine"):
                resolve_project(root)

    def test_dashscope_voice_configuration_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project.json").write_text(
                json.dumps(
                    {
                        "resolution": [1280, 720],
                        "fps": 30,
                        "preview": {"resolution": [640, 360], "fps": 12},
                        "voice": {
                            "engine": "dashscope_qwen3",
                            "model": "qwen3-tts-flash",
                            "voice": "Elias",
                            "language_type": "Chinese",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "storyboard.json").write_text(
                json.dumps([{"id": "api", "narration": "测试语音。"}]),
                encoding="utf-8",
            )
            _, project, _ = resolve_project(root)
            self.assertEqual(project["voice"]["engine"], "dashscope_qwen3")

            project["voice"]["language_type"] = "Unsupported"
            (root / "project.json").write_text(json.dumps(project), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "language_type"):
                resolve_project(root)


class SvgTests(unittest.TestCase):
    def test_scene_renders_expected_components(self) -> None:
        svg = render_scene_svg(
            {
                "title": "测试标题",
                "caption": "测试字幕",
                "characters": [
                    {
                        "pose": "point",
                        "expression": "smile",
                        "shirt": "#123456",
                    }
                ],
                "props": [{"type": "cross", "x": 900, "y": 300}],
            },
            {"background": "#FFFFFF"},
        )
        self.assertIn("测试标题", svg)
        self.assertIn("测试字幕", svg)
        self.assertIn("#123456", svg)
        self.assertIn("#D9433F", svg)
        self.assertTrue(svg.startswith("<svg"))


class MediaTests(unittest.TestCase):
    def test_all_camera_presets_build_filters(self) -> None:
        static = _video_filter("static", 640, 360, 12, 3.0)
        self.assertNotIn("zoompan", static)
        self.assertIn("flags=lanczos", static)

        for camera in ("zoom_in", "zoom_out", "pan_left", "pan_right"):
            rendered = _video_filter(camera, 640, 360, 12, 3.0)
            self.assertIn("zoompan", rendered)
            self.assertIn("s=640x360", rendered)
            self.assertIn("fps=12", rendered)
            self.assertIn("d=1", rendered)
            self.assertIn("trunc", rendered)

    def test_pcm_audio_composition_adds_pause(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first.wav"
            second = root / "second.wav"
            output = root / "joined.wav"
            for path in (first, second):
                with wave.open(str(path), "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(48000)
                    handle.writeframes(b"\x01\x00" * 4800)
            compose_pcm_audio([(first, 0.05), (second, 0)], output)
            self.assertAlmostEqual(duration(output), 0.25, places=3)


class SubtitleTests(unittest.TestCase):
    def test_writes_srt_and_ass(self) -> None:
        cues = [
            {"text": "第一句。", "start": 0.0, "end": 1.234},
            {"text": "第二句。", "start": 1.234, "end": 2.5},
        ]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            srt = root / "subtitles.srt"
            ass = root / "subtitles.ass"
            write_srt(cues, srt)
            write_ass(cues, ass, width=640, height=360)
            self.assertIn("00:00:01,234", srt.read_text(encoding="utf-8"))
            ass_text = ass.read_text(encoding="utf-8")
            self.assertIn("PlayResX: 640", ass_text)
            self.assertIn("Dialogue: 0,0:00:00.00,0:00:01.23", ass_text)


class TTSTests(unittest.TestCase):
    def test_dashscope_downloads_generated_audio(self) -> None:
        api_response = json.dumps(
            {
                "request_id": "request-1",
                "output": {
                    "audio": {
                        "url": "https://example.aliyuncs.com/generated.wav",
                        "data": "",
                    }
                },
            }
        ).encode("utf-8")
        audio = b"RIFF-test-audio"
        config = {
            "engine": "dashscope_qwen3",
            "model": "qwen3-tts-flash",
            "voice": "Elias",
            "language_type": "Chinese",
            "max_retries": 1,
        }

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "voice.wav"
            with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-secret"}), patch(
                "pipeline.tts.urlopen",
                side_effect=[FakeHTTPResponse(api_response), FakeHTTPResponse(audio)],
            ) as mocked_open:
                synthesize_dashscope_qwen3("测试语音。", output, config)

            self.assertEqual(output.read_bytes(), audio)
            post_request = mocked_open.call_args_list[0].args[0]
            payload = json.loads(post_request.data.decode("utf-8"))
            self.assertEqual(payload["model"], "qwen3-tts-flash")
            self.assertEqual(payload["input"]["voice"], "Elias")
            self.assertEqual(payload["input"]["language_type"], "Chinese")

    def test_dashscope_requires_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(TTSError, "DASHSCOPE_API_KEY"):
                synthesize_dashscope_qwen3(
                    "测试语音。",
                    Path(temp) / "voice.wav",
                    {"engine": "dashscope_qwen3"},
                )

    def test_dashscope_rejects_oversized_text_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"DASHSCOPE_API_KEY": "test-secret"}
        ):
            with self.assertRaisesRegex(TTSError, "600-character"):
                synthesize_dashscope_qwen3(
                    "字" * 601,
                    Path(temp) / "voice.wav",
                    {"engine": "dashscope_qwen3"},
                )

    def test_tts_cache_signature_does_not_contain_credentials(self) -> None:
        signature = tts_cache_signature(
            {
                "engine": "dashscope_qwen3",
                "model": "qwen3-tts-flash",
                "voice": "Elias",
                "api_key_env": "PRIVATE_KEY_NAME",
            }
        )
        self.assertEqual(signature["voice"], "Elias")
        self.assertNotIn("api_key_env", signature)


class VideoTimelineTests(unittest.TestCase):
    """Short outputs used to be rejected for a sub-frame timing artefact."""

    WIDTH = 320
    HEIGHT = 180
    FPS = 12

    @staticmethod
    def _still(path: Path) -> Path:
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
                "color=c=white:s=320x180",
                "-frames:v",
                "1",
                str(path),
            ]
        )
        return path

    @staticmethod
    def _frames(path: Path) -> int:
        stream = next(
            item for item in probe(path)["streams"] if item.get("codec_type") == "video"
        )
        return int(stream["nb_frames"])

    def test_align_duration_snaps_up_to_frame_grid(self) -> None:
        self.assertAlmostEqual(align_duration_to_frames(2.87, 12), 35 / 12, places=6)
        self.assertAlmostEqual(align_duration_to_frames(2.9, 12), 35 / 12, places=6)
        # Already aligned durations must stay untouched to keep caches warm.
        self.assertAlmostEqual(align_duration_to_frames(4.5, 12), 4.5, places=6)
        self.assertAlmostEqual(align_duration_to_frames(3.0, 30), 3.0, places=6)
        # A clip always keeps at least one frame.
        self.assertAlmostEqual(align_duration_to_frames(0.0, 12), 1 / 12, places=6)
        snapped = align_duration_to_frames(2.87, 12)
        self.assertAlmostEqual(align_duration_to_frames(snapped, 12), snapped, places=6)
        with self.assertRaisesRegex(ValueError, "fps"):
            align_duration_to_frames(2.0, 0)

    def test_rendered_clip_lands_exactly_on_the_frame_grid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            clip = root / "clip.mp4"
            render_scene_clip(
                self._still(root / "still.png"),
                None,
                clip,
                width=self.WIDTH,
                height=self.HEIGHT,
                fps=self.FPS,
                clip_duration=2.87,
                camera="zoom_in",
                quality="preview",
            )
            stream = next(
                item
                for item in probe(clip)["streams"]
                if item.get("codec_type") == "video"
            )
            frames = int(stream["nb_frames"])
            self.assertEqual(frames, 35)
            # Container duration matches the frame count, so nothing is lost.
            self.assertAlmostEqual(duration(clip), frames / self.FPS, places=3)

    def test_frame_rate_tolerance_covers_a_lost_frame(self) -> None:
        # Real failure: a 5.83 s preview encoded at 12 fps whose average came
        # out at 414/35 = 11.829 fps, one whole frame short of the timeline.
        measured = 414 / 35
        self.assertGreaterEqual(12 - measured, 0.1)
        self.assertGreaterEqual(frame_rate_tolerance(5.833333, 12), 12 - measured)
        # Long renders keep the tighter relative allowance ...
        self.assertEqual(frame_rate_tolerance(20.353, 12), 0.1)
        self.assertEqual(frame_rate_tolerance(19.395, 30), 0.15)
        # ... and a genuinely wrong cadence is still rejected.
        self.assertGreater(abs(24 - 12), frame_rate_tolerance(5.833333, 12))
        self.assertGreater(abs(30 - 12), frame_rate_tolerance(5.833333, 12))

    def test_short_video_validates_on_the_target_cadence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            still = self._still(root / "still.png")
            clips = []
            for index, clip_duration in enumerate((2.870, 2.917)):
                clip = root / f"clip_{index}.mp4"
                render_scene_clip(
                    still,
                    None,
                    clip,
                    width=self.WIDTH,
                    height=self.HEIGHT,
                    fps=self.FPS,
                    clip_duration=clip_duration,
                    camera="zoom_in",
                    quality="preview",
                )
                clips.append(clip)
            rendered_frames = sum(self._frames(clip) for clip in clips)
            output = root / "joined.mp4"
            concatenate(clips, output, root / "concat.txt")
            report = validate_video(output, self.WIDTH, self.HEIGHT, self.FPS, muted=True)
            joined_frames = self._frames(output)
            # Force the allowance to an impossible value: the very same file
            # must then be rejected, proving the gate really consults it.
            with patch("pipeline.media.frame_rate_tolerance", return_value=-1.0):
                with self.assertRaisesRegex(RuntimeError, "Unexpected frame rate"):
                    validate_video(output, self.WIDTH, self.HEIGHT, self.FPS, muted=True)
        # Nothing is dropped at the scene seam ...
        self.assertEqual(joined_frames, rendered_frames)
        # ... and a short two-scene video keeps the requested cadence instead of
        # drifting a whole frame behind (this pair averaged 11.83 fps before).
        self.assertLess(abs(report["fps"] - self.FPS), 0.25 / report["duration"])


if __name__ == "__main__":
    unittest.main()
