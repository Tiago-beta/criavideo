import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app.tasks.similar_tasks as similar_tasks
from app.routers.video import (
    _extract_similar_reference_frame_map,
    _extract_similar_reference_end_frame_map,
    _extract_similar_reference_text_detected_map,
    _extract_similar_reference_text_excerpt_map,
    _ensure_similar_unified_boundary_frame_paths,
    _extract_similar_unified_boundary_frame_paths,
    _promote_similar_scene_reference_frame,
    _serialize_project_scene,
)
from app.tasks.similar_tasks import (
    _compose_similar_unified_scene_reference_paths,
    _resolve_similar_scene_boundary_reference_paths,
)


class TestSimilarUnifiedReferenceFrames(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.start_path = str(root / "start.jpg")
        self.upload_path = str(root / "upload.jpg")
        self.fallback_path = str(root / "fallback.jpg")
        self.end_path = str(root / "end.jpg")

        for path in (self.start_path, self.upload_path, self.fallback_path, self.end_path):
            Path(path).write_bytes(b"test")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_extract_boundary_paths_ignores_missing_files(self):
        start_path, end_path = _extract_similar_unified_boundary_frame_paths(
            {
                "similar_unified_start_frame_path": self.start_path,
                "similar_unified_end_frame_path": self.end_path,
            }
        )

        self.assertEqual(start_path, self.start_path)
        self.assertEqual(end_path, self.end_path)

        missing_start, missing_end = _extract_similar_unified_boundary_frame_paths(
            {
                "similar_unified_start_frame_path": self.start_path,
                "similar_unified_end_frame_path": str(Path(self.temp_dir.name) / "missing.jpg"),
            }
        )

        self.assertEqual(missing_start, self.start_path)
        self.assertEqual(missing_end, "")

    def test_boundary_frames_wrap_uploaded_and_fallback_references(self):
        ordered_paths = _compose_similar_unified_scene_reference_paths(
            [self.start_path, self.end_path],
            [self.upload_path],
            [self.fallback_path, self.end_path],
        )

        self.assertEqual(
            ordered_paths,
            [self.start_path, self.upload_path, self.fallback_path, self.end_path],
        )

    def test_extract_end_frame_map_ignores_missing_files(self):
        end_frame_map = _extract_similar_reference_end_frame_map(
            {
                "similar_reference_end_frames": {
                    "0": self.end_path,
                    "1": str(Path(self.temp_dir.name) / "missing.jpg"),
                }
            }
        )

        self.assertEqual(end_frame_map, {"0": self.end_path})

    def test_serialize_project_scene_exposes_boundary_frame_urls(self):
        scene = SimpleNamespace(
            id=10,
            scene_index=0,
            scene_type="image",
            prompt="Prompt",
            image_path="",
            clip_path="",
            start_time=0.0,
            end_time=5.0,
            lyrics_segment="",
            is_user_uploaded=False,
        )

        with patch("app.routers.video._to_media_url", side_effect=lambda path: f"/media/{Path(path).name}" if path else None):
            payload = _serialize_project_scene(
                scene,
                {"0": self.start_path},
                {"0": self.end_path},
                {},
                {},
                {},
                {"0": 5.0},
            )

        self.assertEqual(payload["reference_frame_start_path"], self.start_path)
        self.assertEqual(payload["reference_frame_end_path"], self.end_path)
        self.assertEqual(payload["reference_frame_urls"], ["/media/start.jpg", "/media/end.jpg"])

    def test_promote_scene_reference_replaces_base_and_previous_boundary(self):
        promoted_path = str(Path(self.temp_dir.name) / "scene-1-clean.jpg")
        Path(promoted_path).write_bytes(b"clean")

        tags = {
            "similar_reference_frames": {
                "0": self.start_path,
                "1": self.fallback_path,
            },
            "similar_reference_end_frames": {
                "0": self.end_path,
            },
            "similar_reference_frame_text_detected": {
                "1": True,
            },
            "similar_reference_frame_text_excerpt": {
                "1": "DIY Double C Shelf - Easy Build",
            },
        }
        scene = SimpleNamespace(scene_index=1, image_path=promoted_path)

        _promote_similar_scene_reference_frame(tags, scene)

        self.assertEqual(_extract_similar_reference_frame_map(tags)["1"], promoted_path)
        self.assertEqual(_extract_similar_reference_end_frame_map(tags)["0"], promoted_path)
        self.assertNotIn("1", _extract_similar_reference_text_detected_map(tags))
        self.assertNotIn("1", _extract_similar_reference_text_excerpt_map(tags))


class TestSimilarUnifiedBoundaryFallbacks(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.start_path = str(root / "scene-0.jpg")
        self.end_path = str(root / "scene-1.jpg")
        self.video_path = str(root / "source.mp4")
        Path(self.start_path).write_bytes(b"start")
        Path(self.end_path).write_bytes(b"end")
        Path(self.video_path).write_bytes(b"video")
        self.scenes = [
            SimpleNamespace(scene_index=0, start_time=0.0, end_time=2.6),
            SimpleNamespace(scene_index=1, start_time=2.6, end_time=5.2),
        ]

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_unified_boundary_falls_back_when_terminal_extraction_fails(self):
        with patch(
            "app.routers.video._extract_similar_unified_video_frame",
            new=AsyncMock(side_effect=RuntimeError("ffmpeg failed")),
        ):
            start_path, end_path = await _ensure_similar_unified_boundary_frame_paths(
                99,
                self.scenes,
                {
                    "similar_local_video_path": self.video_path,
                    "similar_reference_frames": {
                        "0": self.start_path,
                        "1": self.end_path,
                    },
                },
            )

        self.assertEqual(start_path, self.start_path)
        self.assertEqual(end_path, self.end_path)


class TestSimilarSceneBoundaryReferences(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.frame_paths = [
            str(root / "scene-0.jpg"),
            str(root / "scene-1.jpg"),
            str(root / "scene-2.jpg"),
        ]
        self.video_path = str(root / "source.mp4")
        for path in self.frame_paths:
            Path(path).write_bytes(b"frame")
        Path(self.video_path).write_bytes(b"video")
        self.scenes = [
            SimpleNamespace(scene_index=0, start_time=0.0, end_time=2.6),
            SimpleNamespace(scene_index=1, start_time=2.6, end_time=5.2),
            SimpleNamespace(scene_index=2, start_time=5.2, end_time=7.8),
        ]

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_scene_boundary_uses_next_scene_start_frame(self):
        ordered_paths = await _resolve_similar_scene_boundary_reference_paths(
            77,
            self.scenes[0],
            self.scenes,
            {"similar_local_video_path": self.video_path},
            {
                "0": self.frame_paths[0],
                "1": self.frame_paths[1],
                "2": self.frame_paths[2],
            },
        )

        self.assertEqual(ordered_paths, [self.frame_paths[0], self.frame_paths[1]])

    async def test_scene_boundary_prefers_explicit_end_frame_from_tags(self):
        explicit_end_path = str(Path(self.temp_dir.name) / "scene-0-end.jpg")
        Path(explicit_end_path).write_bytes(b"end-frame")

        ordered_paths = await _resolve_similar_scene_boundary_reference_paths(
            77,
            self.scenes[0],
            self.scenes,
            {
                "similar_local_video_path": self.video_path,
                "similar_reference_end_frames": {"0": explicit_end_path},
            },
            {
                "0": self.frame_paths[0],
                "1": self.frame_paths[1],
                "2": self.frame_paths[2],
            },
        )

        self.assertEqual(ordered_paths, [self.frame_paths[0], explicit_end_path])

    async def test_last_scene_boundary_extracts_terminal_frame(self):
        async def fake_extract_frame(_video_path, _timestamp_seconds, output_path):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_bytes(b"terminal")

        with patch.object(similar_tasks.settings, "media_dir", self.temp_dir.name), patch(
            "app.tasks.similar_tasks._extract_frame",
            new=AsyncMock(side_effect=fake_extract_frame),
        ):
            ordered_paths = await _resolve_similar_scene_boundary_reference_paths(
                77,
                self.scenes[2],
                self.scenes,
                {
                    "similar_local_video_path": self.video_path,
                    "similar_total_duration": 7.8,
                },
                {
                    "0": self.frame_paths[0],
                    "1": self.frame_paths[1],
                    "2": self.frame_paths[2],
                },
            )

        self.assertEqual(ordered_paths[0], self.frame_paths[2])
        self.assertEqual(Path(ordered_paths[1]).name, "similar_scene_002_end_frame.jpg")


if __name__ == "__main__":
    unittest.main()