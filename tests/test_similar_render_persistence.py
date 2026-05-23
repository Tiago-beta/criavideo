import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("GOOGLE_AI_API_KEY", "test-key")

import app.tasks.similar_tasks as similar_tasks
from app.models import VideoProject, VideoRender
from app.tasks.similar_tasks import (
    _persist_similar_preview_render,
    _persist_similar_project_render,
)


class _FakeScalarResult:
    def __init__(self, first_value=None):
        self._first_value = first_value

    def scalars(self):
        return self

    def first(self):
        return self._first_value


class _FakeDb:
    def __init__(self, results=None):
        self._results = list(results or [])
        self.added = []

    async def execute(self, *_args, **_kwargs):
        if self._results:
            return self._results.pop(0)
        return _FakeScalarResult()

    def add(self, item):
        self.added.append(item)


class TestSimilarRenderPersistence(unittest.IsolatedAsyncioTestCase):
    async def test_persist_project_render_copies_video_into_renders_folder(self):
        with TemporaryDirectory() as tmpdir:
            media_dir = Path(tmpdir)
            source_path = media_dir / "clips" / "12" / "similar_unified.mp4"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_bytes(b"video-bytes")

            fake_db = _FakeDb([_FakeScalarResult(first_value=None)])
            project = VideoProject(id=12, user_id=7, track_id=0, title="Video Semelhante")

            def fake_thumbnail(*, output_path, **_kwargs):
                target = Path(output_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"thumb")

            with patch.object(similar_tasks.settings, "media_dir", str(media_dir)), patch(
                "app.tasks.similar_tasks.generate_thumbnail_from_frame",
                side_effect=fake_thumbnail,
            ), patch("app.tasks.similar_tasks.get_duration", return_value=7.5):
                output_path, duration = await _persist_similar_project_render(
                    fake_db,
                    project,
                    source_video_path=str(source_path),
                    aspect_ratio="16:9",
                    kind="unified",
                )

            stored_path = Path(output_path)
            self.assertTrue(stored_path.exists())
            self.assertEqual(stored_path.read_bytes(), b"video-bytes")
            self.assertEqual(stored_path.name, "video_16x9_similar_unified.mp4")
            self.assertEqual(duration, 7.5)
            self.assertEqual(len(fake_db.added), 1)

            render = fake_db.added[0]
            self.assertEqual(render.project_id, 12)
            self.assertEqual(render.format, "16:9")
            self.assertEqual(render.file_path, output_path)
            self.assertEqual(render.file_size, len(b"video-bytes"))
            self.assertEqual(render.duration, 7.5)
            self.assertTrue(str(render.thumbnail_path).endswith("thumbnail_similar_unified.jpg"))

    async def test_persist_preview_render_updates_existing_row(self):
        with TemporaryDirectory() as tmpdir:
            media_dir = Path(tmpdir)
            clip_a = media_dir / "clips" / "44" / "scene_000.mp4"
            clip_b = media_dir / "clips" / "44" / "scene_001.mp4"
            clip_a.parent.mkdir(parents=True, exist_ok=True)
            clip_a.write_bytes(b"clip-a")
            clip_b.write_bytes(b"clip-b")

            existing_render_path = media_dir / "renders" / "44" / "video_16x9_similar_preview.mp4"
            existing_render = VideoRender(project_id=44, file_path=str(existing_render_path), format="16:9")
            fake_db = _FakeDb([_FakeScalarResult(first_value=existing_render)])
            project = VideoProject(id=44, user_id=5, track_id=0, title="Preview semelhante")
            scenes = [
                SimpleNamespace(clip_path=str(clip_a)),
                SimpleNamespace(clip_path=str(clip_b)),
            ]

            async def fake_concatenate(_paths, output_path, crossfade_dur=0.5):
                target = Path(output_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"preview-video")

            def fake_thumbnail(*, output_path, **_kwargs):
                target = Path(output_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"thumb")

            with patch.object(similar_tasks.settings, "media_dir", str(media_dir)), patch(
                "app.tasks.similar_tasks.concatenate_clips",
                new=AsyncMock(side_effect=fake_concatenate),
            ), patch(
                "app.tasks.similar_tasks.generate_thumbnail_from_frame",
                side_effect=fake_thumbnail,
            ), patch("app.tasks.similar_tasks.get_duration", return_value=11.2):
                output_path, duration = await _persist_similar_preview_render(
                    fake_db,
                    project,
                    scenes,
                    "16:9",
                    {"similar_scene_strategy": "story"},
                )

            self.assertEqual(output_path, str(existing_render_path))
            self.assertEqual(duration, 11.2)
            self.assertEqual(len(fake_db.added), 0)
            self.assertEqual(existing_render.file_path, str(existing_render_path))
            self.assertEqual(existing_render.file_size, len(b"preview-video"))
            self.assertEqual(existing_render.duration, 11.2)
            self.assertTrue(Path(output_path).exists())


if __name__ == "__main__":
    unittest.main()