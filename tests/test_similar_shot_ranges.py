import unittest

from app.tasks.similar_tasks import (
    _build_similar_scene_ranges,
    _resolve_similar_reference_end_frame_timestamp,
    _resolve_similar_reference_frame_timestamp,
)


class TestSimilarShotRanges(unittest.TestCase):
    def test_detected_cuts_are_coalesced_into_fixed_time_windows(self):
        ranges = _build_similar_scene_ranges(
            12.0,
            [1.0, 2.0, 3.0, 4.0],
            target_chunk_seconds=5.0,
            min_seconds=0.85,
        )

        self.assertEqual(ranges, [(0.0, 5.0), (5.0, 10.0), (10.0, 12.0)])

    def test_long_stable_shot_uses_minimum_five_second_windows(self):
        ranges = _build_similar_scene_ranges(
            12.0,
            [],
            target_chunk_seconds=3.0,
            min_seconds=0.85,
        )

        self.assertEqual(ranges, [(0.0, 5.0), (5.0, 10.0), (10.0, 12.0)])

    def test_short_video_stays_in_one_window_even_with_tiny_cuts(self):
        ranges = _build_similar_scene_ranges(
            4.0,
            [0.2, 1.0, 1.25, 3.0],
            target_chunk_seconds=5.0,
            min_seconds=0.85,
        )

        self.assertEqual(ranges, [(0.0, 4.0)])

    def test_reference_frame_timestamp_uses_scene_start(self):
        self.assertEqual(_resolve_similar_reference_frame_timestamp(0.0, 12.0), 0.0)
        self.assertEqual(_resolve_similar_reference_frame_timestamp(3.0, 12.0), 3.0)
        self.assertEqual(_resolve_similar_reference_frame_timestamp(-2.0, 12.0), 0.0)

    def test_reference_end_frame_timestamp_uses_scene_end_minus_epsilon(self):
        self.assertEqual(_resolve_similar_reference_end_frame_timestamp(0.0, 5.0, 12.0), 4.95)
        self.assertEqual(_resolve_similar_reference_end_frame_timestamp(5.0, 10.0, 12.0), 9.95)
        self.assertEqual(_resolve_similar_reference_end_frame_timestamp(10.0, 12.0, 12.0), 11.95)


if __name__ == "__main__":
    unittest.main()