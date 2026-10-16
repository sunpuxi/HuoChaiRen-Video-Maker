from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from pipeline.animate import (
    ANIMATION_VERSION,
    animation_signature,
    has_animated_content,
    split_layers,
)
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
from pipeline.svg import PAPER_SHAPES, render_scene_svg
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


class ConfigTests(unittest.TestCase):
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
