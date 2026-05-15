import tempfile
import unittest
from pathlib import Path

from app.routers.video import _extract_similar_unified_boundary_frame_paths
from app.tasks.similar_tasks import _compose_similar_unified_scene_reference_paths


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


if __name__ == "__main__":
    unittest.main()