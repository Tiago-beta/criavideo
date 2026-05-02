import unittest

from app.tasks.similar_tasks import _build_similar_scene_ranges


class TestSimilarShotRanges(unittest.TestCase):
    def test_detected_cuts_create_micro_scenes(self):
        ranges = _build_similar_scene_ranges(
            5.0,
            [1.0, 2.0, 3.0, 4.0],
            target_chunk_seconds=5.0,
            min_seconds=0.85,
        )

        self.assertEqual(ranges, [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0), (4.0, 5.0)])

    def test_long_stable_shot_is_split_by_target_chunk(self):
        ranges = _build_similar_scene_ranges(
            12.0,
            [],
            target_chunk_seconds=5.0,
            min_seconds=0.85,
        )

        self.assertEqual(ranges, [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)])

    def test_tiny_cuts_are_merged_into_neighbors(self):
        ranges = _build_similar_scene_ranges(
            4.0,
            [0.2, 1.0, 1.25, 3.0],
            target_chunk_seconds=5.0,
            min_seconds=0.85,
        )

        self.assertEqual(ranges, [(0.0, 1.0), (1.0, 3.0), (3.0, 4.0)])


if __name__ == "__main__":
    unittest.main()