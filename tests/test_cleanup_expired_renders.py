from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import app.scheduler as scheduler_module
from app.models import VideoProject, VideoRender, VideoStatus


class _FakeScalarResult:
    def __init__(self, items=None, first_value=None):
        self._items = list(items or [])
        self._first_value = first_value

    def scalars(self):
        return self

    def all(self):
        return list(self._items)

    def first(self):
        return self._first_value


class _FakeDb:
    def __init__(self, results):
        self._results = list(results)
        self.commit_calls = 0

    async def execute(self, *_args, **_kwargs):
        if not self._results:
            raise AssertionError("Unexpected execute call during cleanup_expired_renders")
        return self._results.pop(0)

    async def commit(self):
        self.commit_calls += 1


class _FakeSessionContext:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc, tb):
        return False


class TestCleanupExpiredRenders(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_deletes_tevoxi_render_files_for_legacy_media_paths(self):
        with TemporaryDirectory() as tmpdir:
            media_dir = Path(tmpdir)
            render_file = media_dir / "renders" / "42" / "video.mp4"
            thumb_file = media_dir / "thumbnails" / "42" / "thumb.jpg"
            audio_file = media_dir / "audio" / "42" / "track.mp3"

            render_file.parent.mkdir(parents=True, exist_ok=True)
            thumb_file.parent.mkdir(parents=True, exist_ok=True)
            audio_file.parent.mkdir(parents=True, exist_ok=True)

            render_file.write_bytes(b"video")
            thumb_file.write_bytes(b"thumb")
            audio_file.write_bytes(b"audio")

            project = VideoProject(
                id=42,
                user_id=7,
                track_id=0,
                title="Louvor",
                status=VideoStatus.COMPLETED,
                tags={"audio_source": "tevoxi"},
                track_artist="Tevoxi",
            )
            render = VideoRender(
                id=99,
                project_id=42,
                file_path="/video/media/renders/42/video.mp4",
                thumbnail_path="thumbnails/42/thumb.jpg",
                created_at=datetime.utcnow() - timedelta(hours=scheduler_module.RENDER_EXPIRY_HOURS + 1),
            )
            render.project = project

            fake_db = _FakeDb(
                [
                    _FakeScalarResult(items=[render]),
                    _FakeScalarResult(items=[project]),
                    _FakeScalarResult(first_value=None),
                ]
            )

            with patch.object(scheduler_module.settings, "media_dir", str(media_dir)):
                with patch.object(
                    scheduler_module,
                    "async_session",
                    return_value=_FakeSessionContext(fake_db),
                ):
                    await scheduler_module.cleanup_expired_renders()

            self.assertIsNone(render.file_path)
            self.assertIsNone(render.thumbnail_path)
            self.assertFalse(render_file.exists())
            self.assertFalse(thumb_file.exists())
            self.assertFalse(audio_file.exists())
            self.assertFalse((media_dir / "audio" / "42").exists())
            self.assertEqual(fake_db.commit_calls, 1)

    async def test_cleanup_keeps_db_path_when_delete_fails(self):
        with TemporaryDirectory() as tmpdir:
            media_dir = Path(tmpdir)
            render_file = media_dir / "renders" / "7" / "video.mp4"
            render_file.parent.mkdir(parents=True, exist_ok=True)
            render_file.write_bytes(b"video")

            project = VideoProject(
                id=7,
                user_id=3,
                track_id=0,
                title="Falha delete",
                status=VideoStatus.COMPLETED,
                tags={"audio_source": "tevoxi"},
            )
            render = VideoRender(
                id=11,
                project_id=7,
                file_path="renders/7/video.mp4",
                created_at=datetime.utcnow() - timedelta(hours=scheduler_module.RENDER_EXPIRY_HOURS + 1),
            )
            render.project = project

            fake_db = _FakeDb(
                [
                    _FakeScalarResult(items=[render]),
                    _FakeScalarResult(items=[project]),
                    _FakeScalarResult(first_value=render),
                ]
            )

            with patch.object(scheduler_module.settings, "media_dir", str(media_dir)):
                with patch.object(
                    scheduler_module,
                    "async_session",
                    return_value=_FakeSessionContext(fake_db),
                ):
                    with patch.object(scheduler_module.os, "remove", side_effect=OSError("locked")):
                        await scheduler_module.cleanup_expired_renders()

            self.assertEqual(render.file_path, "renders/7/video.mp4")
            self.assertTrue(render_file.exists())
            self.assertEqual(fake_db.commit_calls, 0)

    async def test_cleanup_removes_orphan_render_directories_for_already_expired_project(self):
        with TemporaryDirectory() as tmpdir:
            media_dir = Path(tmpdir)
            render_dir = media_dir / "renders" / "55"
            thumb_dir = media_dir / "thumbnails" / "55"
            audio_dir = media_dir / "audio" / "55"
            render_dir.mkdir(parents=True, exist_ok=True)
            thumb_dir.mkdir(parents=True, exist_ok=True)
            audio_dir.mkdir(parents=True, exist_ok=True)

            (render_dir / "orphan.mp4").write_bytes(b"video")
            (thumb_dir / "orphan.jpg").write_bytes(b"thumb")
            (audio_dir / "track.mp3").write_bytes(b"audio")

            project = VideoProject(
                id=55,
                user_id=9,
                track_id=0,
                title="Projeto expirado",
                status=VideoStatus.COMPLETED,
                tags={"audio_source": "tevoxi"},
            )

            fake_db = _FakeDb(
                [
                    _FakeScalarResult(items=[]),
                    _FakeScalarResult(items=[project]),
                    _FakeScalarResult(first_value=None),
                ]
            )

            with patch.object(scheduler_module.settings, "media_dir", str(media_dir)):
                with patch.object(
                    scheduler_module,
                    "async_session",
                    return_value=_FakeSessionContext(fake_db),
                ):
                    await scheduler_module.cleanup_expired_renders()

            self.assertFalse(render_dir.exists())
            self.assertFalse(thumb_dir.exists())
            self.assertFalse(audio_dir.exists())
            self.assertEqual(fake_db.commit_calls, 0)


if __name__ == "__main__":
    unittest.main()