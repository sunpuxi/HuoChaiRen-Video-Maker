from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from pipeline.build import art_style_key
from pipeline.animate import (
    ANIMATION_VERSION,
    animation_signature,
    has_animated_content,
    prop_state,
    split_layers,
    strip_background,
)
from pipeline import handdraw
from pipeline.config import (
    ALLOWED_EXPRESSIONS,
    ALLOWED_POSES,
    load_env_file,
    resolve_project,
)
from pipeline.ink import chain, circle_points, ink, ink_loop, wobble
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
from pipeline.svg import ICONS, PAPER_SHAPES, PROP_RENDERERS, render_scene_svg
from pipeline.tts import (
    DEFAULT_MINIMAX_ENDPOINT,
    TTSError,
    minimax_payload,
    strip_control_markers,
    synthesize_dashscope_qwen3,
    synthesize_minimax,
    tts_cache_signature,
)


class FakeHTTPResponse:
    def __init__(self, content: bytes):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.content


class ConfigTests(unittest.TestCase):
    def _minimax_project(self, root: Path, voice: dict) -> None:
        (root / "project.json").write_text(
            json.dumps(
                {
                    "resolution": [1280, 720],
                    "fps": 30,
                    "preview": {"resolution": [640, 360], "fps": 12},
                    "voice": {"engine": "minimax", **voice},
                }
            ),
            encoding="utf-8",
        )
        (root / "storyboard.json").write_text(
            json.dumps([{"id": "s1", "narration": "测试。", "duration": 2, "camera": "static"}]),
            encoding="utf-8",
        )

    def test_minimax_voice_settings_are_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._minimax_project(
                root,
                {
                    "model": "speech-2.8-hd",
                    "voice_id": "Chinese (Mandarin)_Lyrical_Voice",
                    "speed": 0.95,
                    "vol": 1.0,
                    "pitch": 0,
                    "emotion": "calm",
                    "language_boost": "Chinese",
                    "text_normalization_mode": "quality",
                    "format": "mp3",
                    "sample_rate": 32000,
                    "api_key_env": "MINIMAX_API_KEY",
                    "timeout": 90,
                    "max_retries": 3,
                },
            )
            _, project, _ = resolve_project(root)
            self.assertEqual(project["voice"]["engine"], "minimax")

    def test_minimax_rejects_unknown_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._minimax_project(root, {"model": "speech-9-hd"})
            with self.assertRaisesRegex(ValueError, "voice.model is not supported"):
                resolve_project(root)

    def test_minimax_rejects_whisper_on_2_8_models(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._minimax_project(root, {"model": "speech-2.8-hd", "emotion": "whisper"})
            with self.assertRaisesRegex(ValueError, "whisper"):
                resolve_project(root)

    def test_minimax_rejects_out_of_range_settings(self) -> None:
        cases = {
            "speed": ({"speed": 3.0}, "voice.speed"),
            "vol": ({"vol": 0}, "voice.vol"),
            "pitch": ({"pitch": 20}, "voice.pitch"),
            "emotion": ({"emotion": "smug"}, "voice.emotion"),
            "format": ({"format": "ogg"}, "voice.format"),
            "sample_rate": ({"sample_rate": 48000}, "voice.sample_rate"),
            "channel": ({"channel": 3}, "voice.channel"),
            "bitrate": ({"bitrate": 96000}, "voice.bitrate"),
            "language_boost": ({"language_boost": "Klingon"}, "voice.language_boost"),
            "normalization": ({"text_normalization_mode": "fast"}, "text_normalization_mode"),
            "pronunciation": ({"pronunciation_dict": {"tone": [1, 2]}}, "pronunciation_dict"),
            "endpoint": ({"endpoint": "wss://api.minimax.cn/ws/v1/t2a_v2"}, "voice.endpoint"),
            "key_env": ({"api_key_env": "not a name"}, "voice.api_key_env"),
            "retries": ({"max_retries": 9}, "voice.max_retries"),
        }
        for label, (voice, message) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self._minimax_project(root, voice)
                with self.assertRaisesRegex(ValueError, message):
                    resolve_project(root)

    def test_env_file_fills_missing_variables(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            env_file = Path(temp) / ".env"
            env_file.write_text(
                "# comment\n"
                "MINIMAX_API_KEY=from-file\n"
                'QUOTED="quoted value"\n'
                "export EXPORTED=exported-value\n"
                "\n"
                "NOT_A_PAIR\n",
                encoding="utf-8",
            )
            self.assertEqual(load_env_file(env_file), 3)
            self.assertEqual(os.environ["MINIMAX_API_KEY"], "from-file")
            self.assertEqual(os.environ["QUOTED"], "quoted value")
            self.assertEqual(os.environ["EXPORTED"], "exported-value")
            self.assertNotIn("NOT_A_PAIR", os.environ)

    def test_env_file_never_overrides_the_real_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"MINIMAX_API_KEY": "from-shell"}, clear=True
        ):
            env_file = Path(temp) / ".env"
            env_file.write_text("MINIMAX_API_KEY=from-file\n", encoding="utf-8")
            self.assertEqual(load_env_file(env_file), 0)
            self.assertEqual(os.environ["MINIMAX_API_KEY"], "from-shell")

    def test_missing_env_file_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(load_env_file(Path(temp) / "nope.env"), 0)

    def test_valid_project_loads(self) -> None:
        root = Path(__file__).resolve().parents[1]
        project_dir, project, scenes = resolve_project(root / "projects" / "clothing")
        self.assertEqual(project_dir.name, "clothing")
        self.assertEqual(project["fps"], 30)
        self.assertEqual(len(scenes), 16)

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

    def test_minimax_decodes_hex_audio(self) -> None:
        audio = b"ID3-minimax-audio"
        api_response = json.dumps(
            {
                "data": {"audio": audio.hex(), "status": 2},
                "extra_info": {"audio_length": 1234},
                "trace_id": "trace-1",
                "base_resp": {"status_code": 0, "status_msg": "success"},
            }
        ).encode("utf-8")
        config = {
            "engine": "minimax",
            "model": "speech-2.8-hd",
            "voice_id": "male-qn-qingse",
            "max_retries": 1,
        }

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "voice.mp3"
            with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-secret"}), patch(
                "pipeline.tts.urlopen", return_value=FakeHTTPResponse(api_response)
            ) as mocked_open:
                synthesize_minimax("测试语音。", output, config)

            self.assertEqual(output.read_bytes(), audio)
            post_request = mocked_open.call_args_list[0].args[0]
            self.assertEqual(post_request.full_url, DEFAULT_MINIMAX_ENDPOINT)
            self.assertEqual(post_request.get_header("Authorization"), "Bearer test-secret")
            payload = json.loads(post_request.data.decode("utf-8"))
            self.assertEqual(payload["model"], "speech-2.8-hd")
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["output_format"], "hex")
            self.assertEqual(payload["voice_setting"]["voice_id"], "male-qn-qingse")

    def test_minimax_downloads_url_audio(self) -> None:
        audio = b"RIFF-minimax-audio"
        api_response = json.dumps(
            {
                "data": {"audio": "https://cdn.minimax.cn/generated.mp3"},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            }
        ).encode("utf-8")
        config = {"engine": "minimax", "max_retries": 1}

        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "voice.mp3"
            with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-secret"}), patch(
                "pipeline.tts.urlopen",
                side_effect=[FakeHTTPResponse(api_response), FakeHTTPResponse(audio)],
            ):
                synthesize_minimax("测试语音。", output, config)

            self.assertEqual(output.read_bytes(), audio)

    def test_minimax_requires_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(TTSError, "MINIMAX_API_KEY"):
                synthesize_minimax(
                    "测试语音。", Path(temp) / "voice.mp3", {"engine": "minimax"}
                )

    def test_minimax_rejects_oversized_text_before_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"MINIMAX_API_KEY": "test-secret"}
        ):
            with patch("pipeline.tts.urlopen") as mocked_open:
                with self.assertRaisesRegex(TTSError, "10000-character"):
                    synthesize_minimax(
                        "字" * 10_001, Path(temp) / "voice.mp3", {"engine": "minimax"}
                    )
            mocked_open.assert_not_called()

    def test_minimax_reports_business_error_without_retry(self) -> None:
        api_response = json.dumps(
            {
                "data": None,
                "trace_id": "trace-9",
                "base_resp": {"status_code": 1004, "status_msg": "authentication failed"},
            }
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"MINIMAX_API_KEY": "bad-key"}
        ):
            with patch(
                "pipeline.tts.urlopen", return_value=FakeHTTPResponse(api_response)
            ) as mocked_open:
                with self.assertRaisesRegex(TTSError, "1004.*authentication failed"):
                    synthesize_minimax(
                        "测试语音。",
                        Path(temp) / "voice.mp3",
                        {"engine": "minimax", "max_retries": 3},
                    )
            self.assertEqual(mocked_open.call_count, 1)

    def test_minimax_retries_rate_limit_then_succeeds(self) -> None:
        limited = json.dumps(
            {"base_resp": {"status_code": 1002, "status_msg": "rate limit"}}
        ).encode("utf-8")
        audio = b"ID3-late"
        success = json.dumps(
            {
                "data": {"audio": audio.hex()},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            }
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"MINIMAX_API_KEY": "test-secret"}
        ), patch("pipeline.tts.time.sleep") as mocked_sleep:
            output = Path(temp) / "voice.mp3"
            with patch(
                "pipeline.tts.urlopen",
                side_effect=[FakeHTTPResponse(limited), FakeHTTPResponse(success)],
            ) as mocked_open:
                synthesize_minimax("测试语音。", output, {"engine": "minimax", "max_retries": 3})

            self.assertEqual(output.read_bytes(), audio)
            self.assertEqual(mocked_open.call_count, 2)
            mocked_sleep.assert_called_once()

    def test_minimax_rejects_empty_audio(self) -> None:
        api_response = json.dumps(
            {
                "data": {"audio": ""},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            }
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"MINIMAX_API_KEY": "test-secret"}
        ):
            with patch("pipeline.tts.urlopen", return_value=FakeHTTPResponse(api_response)):
                with self.assertRaisesRegex(TTSError, "did not include audio data"):
                    synthesize_minimax(
                        "测试语音。", Path(temp) / "voice.mp3", {"engine": "minimax"}
                    )

    def test_minimax_payload_maps_storyboard_knobs(self) -> None:
        payload = minimax_payload(
            "你有没有想过(sighs)<#0.4#>一件很怪的事？",
            {
                "engine": "minimax",
                "model": "speech-2.6-turbo",
                "voice_id": "Chinese (Mandarin)_Lyrical_Voice",
                "speed": 0.95,
                "vol": 1.2,
                "pitch": -1,
                "emotion": "calm",
                "language_boost": "Chinese",
                "text_normalization_mode": "quality",
                "format": "wav",
                "sample_rate": 44100,
                "bitrate": 256000,
                "pronunciation_dict": {"tone": ["处理/(chu3)(li3)"]},
            },
        )
        self.assertEqual(payload["voice_setting"]["speed"], 0.95)
        self.assertEqual(payload["voice_setting"]["emotion"], "calm")
        self.assertEqual(payload["voice_setting"]["pitch"], -1)
        self.assertEqual(payload["language_boost"], "Chinese")
        self.assertEqual(payload["text_normalization_mode"], "quality")
        self.assertEqual(payload["audio_setting"]["sample_rate"], 44100)
        self.assertEqual(payload["audio_setting"]["format"], "wav")
        self.assertNotIn("bitrate", payload["audio_setting"])  # bitrate only applies to mp3
        self.assertEqual(payload["pronunciation_dict"]["tone"], ["处理/(chu3)(li3)"])

    def test_minimax_cache_signature_tracks_voice_settings(self) -> None:
        base = {"engine": "minimax", "model": "speech-2.8-hd", "voice_id": "v1", "speed": 1.0}
        signature = tts_cache_signature({**base, "api_key_env": "PRIVATE_KEY_NAME"})
        self.assertEqual(signature["voice_id"], "v1")
        self.assertNotIn("api_key_env", signature)
        self.assertNotEqual(signature, tts_cache_signature({**base, "speed": 0.9}))
        self.assertNotEqual(signature, tts_cache_signature({**base, "emotion": "calm"}))

    def test_strip_control_markers_leaves_plain_text_untouched(self) -> None:
        for text in ("你有没有想过一件很怪的事？", "钱，到底是什么？", "1000 年前的羊。"):
            self.assertEqual(strip_control_markers(text), text)

    def test_strip_control_markers_removes_speech_only_tokens(self) -> None:
        self.assertEqual(strip_control_markers("古人(sighs)拿羊换米。"), "古人拿羊换米。")
        self.assertEqual(strip_control_markers("你听<#0.4#>，钱在说话。"), "你听，钱在说话。")
        self.assertEqual(strip_control_markers("啊<#0.2#>（真的）？"), "啊（真的）？")
        self.assertEqual(strip_control_markers("(laughs) 从前的钱"), "从前的钱")


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


class BackdropTests(unittest.TestCase):
    """The optional backdrop must stay opt-in and never leak into plain frames."""

    SCENE = {
        "id": "backdrop",
        "title": "它变成了记号",
        "characters": [{"x": 470, "y": 255, "scale": 0.66, "pose": "point", "expression": "smile"}],
        "props": [{"type": "node", "x": 1020, "y": 250, "radius": 80, "text": "记号"}],
    }

    def _svg(self, *, project_extra=None, scene_extra=None, index=0):
        project = {"background": "#FFFDF7"}
        project.update(project_extra or {})
        scene = dict(self.SCENE)
        scene.update(scene_extra or {})
        return render_scene_svg(scene, project, index=index)

    def test_disabled_backdrop_leaves_the_frame_untouched(self) -> None:
        svg = self._svg()
        self.assertNotIn("opacity=\"0.13\"", svg)  # ground line
        self.assertNotIn("opacity=\"0.08\"", svg)  # character shadow
        self.assertNotIn("<circle cx=\"1220\"", svg)
        # The background rect must stay glued to <defs> so cache keys of
        # projects without a backdrop do not change.
        self.assertIn('<rect width="1280" height="720" fill="#FFFDF7"/>\n      <defs>', svg)

    def test_enabled_backdrop_adds_every_layer(self) -> None:
        svg = self._svg(project_extra={"background_style": {"enabled": True}})
        self.assertIn("opacity=\"0.13\"", svg)
        self.assertIn("opacity=\"0.08\"", svg)
        self.assertIn('<circle cx="1220" cy="110" r="260"', svg)  # index 0 placement
        self.assertIn("#F5B632", svg)  # accent title stroke on a light background

    def test_paper_shape_rotates_between_neighbouring_scenes(self) -> None:
        shapes = [
            self._svg(project_extra={"background_style": {"enabled": True}}, index=index)
            for index in range(len(PAPER_SHAPES))
        ]
        marks = ['<circle cx="1220" cy="110"', '<circle cx="60" cy="676"', 'Q640 546 1360 598', '<circle cx="80" cy="96"']
        for svg, mark in zip(shapes, marks):
            self.assertIn(mark, svg)
        self.assertEqual(len(set(shapes)), len(shapes))

    def test_title_stroke_falls_back_to_ink_on_saturated_backgrounds(self) -> None:
        svg = self._svg(
            project_extra={"background_style": {"enabled": True}},
            scene_extra={"background": "#F6B95C"},
        )
        self.assertNotIn("#F5B632", svg)

    def test_ground_line_clears_low_props(self) -> None:
        svg = self._svg(
            project_extra={"background_style": {"enabled": True}},
            scene_extra={"props": [{"type": "node", "x": 240, "y": 495, "radius": 76, "text": "态度"}]},
        )
        line = re.search(r'<path d="M96 (\d+) Q640', svg)
        self.assertIsNotNone(line)
        self.assertGreater(int(line.group(1)), 495 + 76)

    def test_single_layer_can_be_switched_off(self) -> None:
        svg = self._svg(
            project_extra={"background_style": {"enabled": True, "ground": False, "character_shadows": False}},
        )
        self.assertNotIn("opacity=\"0.13\"", svg)
        self.assertNotIn("opacity=\"0.08\"", svg)
        self.assertIn('<circle cx="1220" cy="110" r="260"', svg)

    def test_scene_override_wins_over_project(self) -> None:
        svg = self._svg(
            project_extra={"background_style": {"enabled": True}},
            scene_extra={"background_style": False},
        )
        self.assertNotIn("opacity=\"0.13\"", svg)

    def test_unsupported_paper_shape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project.json").write_text(
                json.dumps({"resolution": [1280, 720], "fps": 30, "preview": {"resolution": [640, 360], "fps": 12}}),
                encoding="utf-8",
            )
            (root / "storyboard.json").write_text(
                json.dumps([{"id": "bad", "title": "标题", "duration": 2, "paper_shape": "triangle"}]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "paper_shape"):
                resolve_project(root)


class AnimationTests(unittest.TestCase):
    """Per-frame animation must be opt-in, deterministic and layer-safe."""

    PROJECT = {"background": "#FFFDF7", "background_style": {"enabled": True}}
    # No backdrop: prop assertions then only see the props themselves.
    PLAIN = {"background": "#FFFDF7"}
    SCENE = {
        "id": "walk",
        "title": "走路",
        "narration": "旁白",
        "background": "#FFFDF7",
        "camera": "pan_right",
        "animation": {"enabled": True, "blink": True},
        "characters": [
            {
                "x": 640,
                "y": 250,
                "scale": 0.7,
                "pose": "stand",
                "expression": "smile",
                "shirt": "#6599D1",
                "animation": {"preset": "walk_in", "from_x": -200, "duration": 1.2},
            }
        ],
        "props": [{"type": "node", "x": 200, "y": 250, "text": "标签"}],
    }

    def _frame(self, t, scene=None):
        return render_scene_svg(scene or self.SCENE, self.PROJECT, index=0, time=t)

    def test_scene_without_animation_renders_the_old_way(self) -> None:
        plain = {key: value for key, value in self.SCENE.items() if key != "animation"}
        self.assertEqual(render_scene_svg(plain, self.PROJECT, index=0),
                         render_scene_svg(plain, self.PROJECT, index=0, time=0.0))

    def test_walk_in_moves_from_off_screen_to_the_target(self) -> None:
        start, middle, end = self._frame(0.0), self._frame(0.5), self._frame(1.4)
        self.assertIn("translate(-200", start)
        self.assertIn("translate(640", end)
        self.assertNotEqual(start, middle)

    def test_walk_cycle_swings_the_limbs(self) -> None:
        first, second = self._frame(1.4), self._frame(1.4 + 0.28)  # half a cycle apart
        self.assertNotEqual(first, second)
        self.assertNotIn('translate(-200', first)  # already arrived, still walking on the spot

    def test_frames_are_deterministic(self) -> None:
        self.assertEqual(self._frame(0.83), self._frame(0.83))

    def test_pop_in_grows_from_nothing(self) -> None:
        scene = copy.deepcopy(self.SCENE)
        scene["characters"][0]["animation"] = {"preset": "pop_in", "start": 0.2, "duration": 0.5}
        hidden = render_scene_svg(scene, self.PROJECT, index=0, time=0.1)
        shown = render_scene_svg(scene, self.PROJECT, index=0, time=1.2)
        self.assertIn("scale(0)", hidden)
        self.assertIn("scale(0.7)", shown)

    def test_blink_closes_the_eyes(self) -> None:
        open_frame = self._frame(1.0)
        blink_start = 3.1 * 2  # start of the third blink window
        closed_frame = self._frame(blink_start + 0.07, {**self.SCENE, "characters": [
            {**self.SCENE["characters"][0], "animation": {"preset": "none"}}]})
        self.assertIn('<circle cx="-23" cy="-10" r="7"', open_frame)
        self.assertIn('<ellipse cx="-23" cy="-10" rx="7"', closed_frame)

    def test_layers_keep_text_on_the_static_base(self) -> None:
        base, layer = split_layers(self.SCENE, self.PROJECT)
        base_svg = render_scene_svg(base, self.PROJECT, index=0)
        start = render_scene_svg(layer, self.PROJECT, index=0, time=0.0)
        later = render_scene_svg(layer, self.PROJECT, index=0, time=1.4)
        self.assertIn("走路", base_svg)          # title stays on the base layer
        self.assertIn("标签", base_svg)          # node label keeps its fonts
        self.assertNotIn("<circle cx=\"0\" cy=\"0\" r=\"86\"", base_svg)  # no character on the base
        self.assertNotIn("<text", start)         # animated layer can skip the font scan
        self.assertIn("translate(-200", start)
        self.assertNotEqual(start, later)        # the layer really animates

    def test_animation_layer_has_no_opaque_background(self) -> None:
        """The layer composites on top of the base, so it must stay transparent."""
        base, layer = split_layers(self.SCENE, self.PROJECT)
        base_svg = render_scene_svg(base, self.PROJECT, index=0)
        layer_svg = strip_background(render_scene_svg(layer, self.PROJECT, index=0, time=0.5))
        self.assertIn('<rect width="1280" height="720"', base_svg)      # base paints the scene
        self.assertNotIn('<rect width="1280" height="720"', layer_svg)  # layer must not,
        self.assertNotIn("<text", layer_svg)                            # or it hides the base
        self.assertEqual(strip_background("<svg></svg>"), "<svg></svg>")

    def test_props_animate_on_the_layer_not_the_base(self) -> None:
        scene = copy.deepcopy(self.SCENE)
        scene["props"] = [
            {"type": "icon", "name": "sheep", "x": 300, "y": 300,
             "animation": {"preset": "pop", "start": 0.2}},
            {"type": "icon", "name": "rice", "x": 900, "y": 300},
        ]
        base, layer = split_layers(scene, self.PROJECT)
        self.assertEqual([p["name"] for p in base["props"]], ["rice"])
        self.assertEqual([p["name"] for p in layer["props"]], ["sheep"])
        self.assertTrue(has_animated_content(scene))

    def test_prop_pop_starts_hidden_and_overshoots(self) -> None:
        prop = {"type": "icon", "name": "fish", "animation": {"preset": "pop", "start": 1.0, "duration": 0.5}}
        self.assertFalse(prop_state(prop, 0.5)["visible"])
        middle = prop_state(prop, 1.35)["scale"]
        self.assertGreater(middle, 1.0)          # ease-out-back overshoots
        self.assertAlmostEqual(prop_state(prop, 3.0)["scale"], 1.0, places=6)

    def test_prop_grow_shortens_the_arrow(self) -> None:
        scene = copy.deepcopy(self.SCENE)
        scene["characters"] = []
        scene["props"] = [{"type": "exchange", "x1": 400, "y1": 300, "x2": 800, "y2": 300,
                           "animation": {"preset": "grow", "start": 0.5, "duration": 0.5}}]

        def bar_end(time: float) -> float:
            svg = render_scene_svg(scene, self.PLAIN, index=0, time=time)
            return float(re.search(r'<path d="M[\d.]+ 300 L([\d.]+) 300"', svg).group(1))

        self.assertLess(bar_end(0.75), bar_end(2.0))   # still growing
        self.assertAlmostEqual(bar_end(2.0), 800 - 30.8, places=1)  # full length minus the head

    def test_exchange_draws_two_arrowheads_without_markers(self) -> None:
        scene = copy.deepcopy(self.SCENE)
        scene["characters"] = []
        scene["props"] = [{"type": "exchange", "x1": 400, "y1": 300, "x2": 800, "y2": 300}]
        svg = render_scene_svg(scene, self.PLAIN, index=0)
        self.assertEqual(svg.count("<path"), 3)   # bar + two heads
        self.assertNotIn("marker-end", svg)

    def test_icon_stroke_stays_constant_across_sizes(self) -> None:
        def width(size: float) -> float:
            scene = copy.deepcopy(self.SCENE)
            scene["characters"] = []
            scene["props"] = [{"type": "icon", "name": "sheep", "x": 640, "y": 300, "size": size}]
            svg = render_scene_svg(scene, self.PLAIN, index=0)
            return float(re.search(r'stroke-width="([0-9.]+)"', svg).group(1))
        # the pen width is divided by the scale, so it shrinks as the icon grows
        self.assertGreater(width(80), width(200))

    def test_every_registered_icon_renders(self) -> None:
        for name in ICONS:
            scene = copy.deepcopy(self.SCENE)
            scene["characters"] = []
            scene["props"] = [{"type": "icon", "name": name, "x": 640, "y": 300}]
            self.assertIn("<path", render_scene_svg(scene, self.PROJECT, index=0))

    def test_unknown_icon_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project.json").write_text(
                json.dumps({"resolution": [1280, 720], "fps": 30, "preview": {"resolution": [640, 360], "fps": 12}}),
                encoding="utf-8",
            )
            (root / "storyboard.json").write_text(
                json.dumps([{"id": "bad", "duration": 2, "props": [{"type": "icon", "name": "dragon"}]}]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "known icon"):
                resolve_project(root)

    def test_animated_scene_is_detected(self) -> None:
        self.assertTrue(has_animated_content(self.SCENE))
        self.assertFalse(has_animated_content({**self.SCENE, "animation": {"enabled": True, "blink": False},
                                               "characters": [{**self.SCENE["characters"][0],
                                                               "animation": {"preset": "none"}}]}))
        self.assertFalse(has_animated_content({key: value for key, value in self.SCENE.items()
                                               if key != "animation"}))

    def test_clip_signature_tracks_the_renderer_version(self) -> None:
        signature = animation_signature(self.SCENE)
        self.assertEqual(signature["version"], ANIMATION_VERSION)
        self.assertEqual(signature["characters"][0]["preset"], "walk_in")

    def test_invalid_animation_preset_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project.json").write_text(
                json.dumps({"resolution": [1280, 720], "fps": 30, "preview": {"resolution": [640, 360], "fps": 12}}),
                encoding="utf-8",
            )
            (root / "storyboard.json").write_text(
                json.dumps([{"id": "bad", "duration": 2, "animation": {"enabled": True},
                             "characters": [{"animation": {"preset": "teleport"}}]}]),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "preset"):
                resolve_project(root)


if __name__ == "__main__":
    unittest.main()



# --- hand-drawn art style ---------------------------------------------------

class InkEngineTests(unittest.TestCase):
    """The ink engine must be deterministic — every render cache depends on it."""

    def setUp(self) -> None:
        self.stroke = chain(((-40, 0), (-10, 30), (20, 40), (60, 10)))

    def test_ink_is_deterministic(self) -> None:
        self.assertEqual(ink(self.stroke, 12, "probe"), ink(self.stroke, 12, "probe"))

    def test_ink_varies_with_the_seed_name(self) -> None:
        self.assertNotEqual(ink(self.stroke, 12, "probe-a"), ink(self.stroke, 12, "probe-b"))

    def test_ink_never_emits_a_constant_stroke_width(self) -> None:
        # A single uniform width is exactly what makes vector art look machine-made.
        self.assertNotIn('stroke-width="12"', ink(self.stroke, 12, "probe"))

    def test_ink_loop_stays_hollow(self) -> None:
        contour = wobble(circle_points(0, 0, 50, 24), "ring", 1.0, closed=True)
        path = ink_loop(contour, 10, "ring")
        # Two offset rings under even-odd: one subpath each, interior unfilled.
        self.assertIn('fill-rule="evenodd"', path)
        self.assertEqual(path.count("M"), 2)

    def test_wobble_stays_within_the_requested_amplitude(self) -> None:
        moved = wobble(self.stroke, "amp", amp=2.0)
        self.assertEqual(len(moved), len(self.stroke))
        for (x0, y0), (x1, y1) in zip(self.stroke, moved):
            self.assertLessEqual(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, 2.05)


class ArtStyleTests(unittest.TestCase):
    """The default style must stay byte-identical; the new one is opt-in."""

    SCENE = {
        "id": "t",
        "narration": "x",
        "title": "标题",
        "camera": "zoom_in",
        "characters": [
            {"x": 400, "y": 220, "scale": 0.7, "pose": "point", "expression": "smile"},
            {"x": 880, "y": 240, "scale": 0.6, "pose": "think", "expression": "confused"},
        ],
        "props": [{"type": "icon", "name": "sheep", "x": 640, "y": 380}],
    }
    PROJECT = {"background": "#FFFDF7", "background_style": {"enabled": True}}

    def test_line_style_output_is_frozen(self) -> None:
        # A golden hash: an accidental change on the default drawing path would
        # silently invalidate every cached image and clip in existing projects.
        svg = render_scene_svg(self.SCENE, self.PROJECT, index=1)
        self.assertEqual(
            hashlib.sha256(svg.encode()).hexdigest(),
            "ced0f51fce9fe2678deb0e9c725b58a0922554870d9fd8c48126a9051051c4e2",
        )

    def test_style_resolution_order(self) -> None:
        self.assertEqual(handdraw.art_style_for({}, None), "line")
        self.assertEqual(handdraw.art_style_for({}, {"art_style": "hand"}), "hand")
        self.assertEqual(handdraw.art_style_for({"style": "line"}, {"art_style": "hand"}), "line")
        # an unknown style falls back rather than taking the render down
        self.assertEqual(handdraw.art_style_for({"style": "bogus"}, None),
                         handdraw.DEFAULT_STYLE)

    def test_hand_style_changes_the_scene(self) -> None:
        hand = dict(self.PROJECT, art_style="hand")
        self.assertNotEqual(render_scene_svg(self.SCENE, self.PROJECT, index=1),
                            render_scene_svg(self.SCENE, hand, index=1))

    def test_hand_style_draws_an_inked_figure(self) -> None:
        svg = render_scene_svg(self.SCENE, dict(self.PROJECT, art_style="hand"), index=1)
        self.assertIn('fill-rule="evenodd"', svg)
        self.assertGreater(len(svg), 20000)

    def test_clip_cache_records_non_default_styles(self) -> None:
        # Animated clips composite from a character-free base image, so without
        # this the clip cache serves a hand-drawn clip to a line-style project.
        scene = {"id": "a", "characters": [{"x": 1, "y": 1}]}
        self.assertIsNone(art_style_key(scene, {}))
        self.assertIsNone(art_style_key(scene, {"art_style": "line"}))
        self.assertEqual(art_style_key(scene, {"art_style": "hand"}), ["hand"])
        # per-character override still registers
        mixed = {"id": "a", "characters": [{"x": 1, "y": 1}, {"x": 2, "y": 2, "style": "hand"}]}
        self.assertEqual(art_style_key(mixed, {}), ["line", "hand"])

    def test_ground_line_follows_the_style_metrics(self) -> None:
        hand = render_scene_svg(self.SCENE, dict(self.PROJECT, art_style="hand"), index=1)
        line = handdraw.metrics_for("line")
        inked = handdraw.metrics_for("hand")
        self.assertNotEqual(line["foot"], inked["foot"])
        self.assertIn(f'cy="{220 + 0.7 * line["foot"]:g}"',
                      render_scene_svg(self.SCENE, self.PROJECT, index=1))
        self.assertIn(f'cy="{220 + 0.7 * inked["foot"]:g}"', hand)


class HandDrawnCharacterTests(unittest.TestCase):

    def test_every_pose_and_expression_renders(self) -> None:
        for pose in ALLOWED_POSES:
            for expression in ALLOWED_EXPRESSIONS:
                svg = handdraw.figure({"pose": pose, "expression": expression})
                self.assertTrue(svg.startswith("<"))
                self.assertGreater(len(svg), 4000)

    def test_every_style_variant_renders(self) -> None:
        for key, values in (("hair", handdraw.HAIR_STYLES),
                            ("brow", handdraw.BROW_STYLES),
                            ("nose", handdraw.NOSE_STYLES),
                            ("mouth", handdraw.MOUTH_STYLES),
                            ("outfit", handdraw.OUTFITS),
                            ("ears", handdraw.EARS)):
            for value in values:
                self.assertGreater(len(handdraw.figure({key: value})), 3000,
                                   f"{key}={value} produced a suspiciously small figure")

    def test_expression_drives_brow_and_mouth(self) -> None:
        self.assertEqual(handdraw.spec_of({"expression": "angry"})["brow"], "angry")
        self.assertEqual(handdraw.spec_of({"expression": "surprised"})["mouth"], "open")
        # an explicit field wins over the expression it would otherwise imply
        spec = handdraw.spec_of({"expression": "angry", "mouth": "grin"})
        self.assertEqual(spec["mouth"], "grin")
        self.assertEqual(spec["brow"], "angry")

    def test_unknown_values_fall_back_instead_of_raising(self) -> None:
        spec = handdraw.spec_of({"hair": "mohawk", "nose": "beak", "ears": "wings"})
        self.assertIn(spec["hair"], handdraw.HAIR_STYLES)
        self.assertIn(spec["nose"], handdraw.NOSE_STYLES)
        self.assertIn(spec["ears"], handdraw.EARS)

    def test_blink_closes_the_eye(self) -> None:
        opened = handdraw.head_parts(handdraw.spec_of({}), "b", 0.0)
        shut = handdraw.head_parts(handdraw.spec_of({}), "b", 1.0)
        self.assertNotEqual(opened, shut)
        self.assertIn('r="3.6"', opened)      # highlight only exists when open
        self.assertNotIn('r="3.6"', shut)

    def test_walk_cycle_moves_the_limbs(self) -> None:
        upright = handdraw.figure({}, pose="stand")
        mid_stride = handdraw.figure({}, pose="walk", walk_phase=1.2)
        self.assertNotEqual(upright, mid_stride)
        self.assertNotEqual(handdraw.walk_legs(1.2), handdraw.walk_legs(3.9))


class ArtStyleValidationTests(unittest.TestCase):

    def _resolve(self, project: dict, character: dict) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project.json").write_text(json.dumps({
                "resolution": [1280, 720],
                "fps": 30,
                "preview": {"resolution": [640, 360], "fps": 12},
                "voice": {"engine": "macos"},
                **project,
            }), encoding="utf-8")
            (root / "storyboard.json").write_text(json.dumps(
                [{"id": "s1", "narration": "测试。", "duration": 2, "characters": [character]}]
            ), encoding="utf-8")
            resolve_project(root)

    def test_rejects_unknown_project_art_style(self) -> None:
        with self.assertRaisesRegex(ValueError, "project.art_style"):
            self._resolve({"art_style": "painterly"}, {})

    def test_rejects_unknown_character_style_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "hair is not supported"):
            self._resolve({"art_style": "hand"}, {"hair": "mohawk"})
        with self.assertRaisesRegex(ValueError, "style is not supported"):
            self._resolve({"art_style": "hand"}, {"style": "sketch"})

    def test_accepts_a_hand_drawn_character(self) -> None:
        self._resolve({"art_style": "hand"}, {
            "hair": "afro", "nose": "potato", "outfit": "robe", "brow": "unibrow",
            "mouth": "grin", "ears": "big", "hair_colour": "#222222", "accent": "#E8623C",
        })


class HandDrawnPropTests(unittest.TestCase):
    """Props share the character's pen, so a scene never mixes two line styles."""

    def _prop(self, kind, **extra):
        return {"type": kind, "x": 300, "y": 200, "x1": 100, "y1": 100,
                "x2": 500, "y2": 220, **extra}

    def test_every_prop_type_renders(self) -> None:
        from pipeline import handprops
        self.assertEqual(set(handprops.PROP_RENDERERS), set(PROP_RENDERERS))
        for kind, renderer in handprops.PROP_RENDERERS.items():
            svg = renderer(self._prop(kind, name="sheep", text="你好"))
            self.assertTrue(svg.strip(), f"{kind} rendered nothing")

    def test_every_icon_renders(self) -> None:
        from pipeline import handprops
        self.assertEqual(set(handprops.ICON_RENDERERS), set(ICONS))
        for name in handprops.ICON_RENDERERS:
            self.assertGreater(len(handprops.icon(self._prop("icon", name=name))), 500)

    def test_props_use_the_requested_colour(self) -> None:
        # The arrow shaft silently kept the default ink colour while its head
        # was green, which reads as a black blob with a green tip.
        from pipeline import handprops
        for kind in ("arrow", "cross", "arc", "exchange", "star"):
            svg = handprops.PROP_RENDERERS[kind](
                self._prop(kind, width=15, _grow=1.0, color="#45A95A"))
            self.assertIn("#45A95A", svg, f"{kind} ignored its colour")
            self.assertNotIn("#141414", svg, f"{kind} fell back to the ink colour")

    def test_arrowheads_are_polygons_not_curves(self) -> None:
        # chain() bows a 3-point tuple into a quadratic, which silently turns
        # every arrowhead into a lens. Heads must be straight-edged.
        from pipeline import handprops
        for kind in ("arrow", "exchange"):
            svg = handprops.PROP_RENDERERS[kind](self._prop(kind, width=15, _grow=1.0))
            heads = [seg for seg in svg.split("<path") if 'fill="#45A95A"' in seg]
            self.assertTrue(heads, f"{kind} drew no filled head")
            for head in heads:
                body = head.split('d="', 1)[1].split('"', 1)[0]
                self.assertIn("L", body)
                self.assertNotIn("Q", body)

    def test_icons_keep_a_constant_on_screen_pen(self) -> None:
        # A large icon must not look bolder than a small one: the local width
        # is divided by the scale so the on-screen result is constant.
        from pipeline import handprops
        small_w, _ = handprops._icon_pen({"size": 100})
        large_w, _ = handprops._icon_pen({"size": 400})
        self.assertAlmostEqual(small_w * 1.0, large_w * 4.0, places=6)

    def test_hand_style_swaps_props_in_a_scene(self) -> None:
        scene = {"id": "s", "narration": "x", "camera": "static",
                 "characters": [{"x": 640, "y": 300}],
                 "props": [{"type": "icon", "name": "sheep", "x": 500, "y": 400},
                           {"type": "cross", "x": 800, "y": 400}]}
        base = {"background_style": {"enabled": False}}
        line = render_scene_svg(scene, base, index=0)
        hand = render_scene_svg(scene, dict(base, art_style="hand"), index=0)
        self.assertIn('Q', line)               # the line arrow uses markers/curves
        self.assertIn('fill-rule="evenodd"', hand)
        self.assertGreater(len(hand), len(line) * 3)

    def test_bubble_and_node_keep_their_text(self) -> None:
        from pipeline import handprops
        self.assertIn(">你好<", handprops.bubble(self._prop("bubble", text="你好")))
        self.assertIn(">身份<", handprops.node(self._prop("node")))


class InkChainTests(unittest.TestCase):

    def test_chain_accepts_lines_quads_and_cubics(self) -> None:
        self.assertEqual(len(chain(((0, 0), (10, 0)))), 2)
        self.assertGreater(len(chain(((0, 0), (5, -5), (10, 0)))), 2)
        self.assertGreater(len(chain(((0, 0), (3, -5), (7, -5), (10, 0)))), 2)

    def test_chain_rejects_a_polyline_of_the_wrong_length(self) -> None:
        with self.assertRaisesRegex(ValueError, "segments of 2, 3 or 4 points"):
            chain(((0, 0), (1, 1), (2, 2), (3, 3), (4, 4)))
