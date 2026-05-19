import unittest

from app.tasks.similar_tasks import (
    _build_similar_scene_ranges,
    _engine_duration,
    _normalize_engine,
    _resolve_similar_reference_end_frame_timestamp,
    _resolve_similar_reference_frame_timestamp,
    _suggest_engine_for_detected_mode,
)


class TestSimilarShotRanges(unittest.TestCase):
    def test_detected_cuts_are_coalesced_into_fixed_time_windows(self):
        ranges = _build_similar_scene_ranges(
            12.0,
            [1.0, 2.0, 3.0, 4.0],
            target_chunk_seconds=5.0,
            min_seconds=0.85,
        )

        self.assertEqual(ranges, [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0), (6.0, 8.0), (8.0, 10.0), (10.0, 12.0)])

    def test_long_stable_shot_uses_fixed_two_second_windows(self):
        ranges = _build_similar_scene_ranges(
            12.0,
            [],
            target_chunk_seconds=3.0,
            min_seconds=0.85,
        )

        self.assertEqual(ranges, [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0), (6.0, 8.0), (8.0, 10.0), (10.0, 12.0)])

    def test_short_video_splits_into_two_second_windows_even_with_tiny_cuts(self):
        ranges = _build_similar_scene_ranges(
            4.0,
            [0.2, 1.0, 1.25, 3.0],
            target_chunk_seconds=5.0,
            min_seconds=0.85,
        )

        self.assertEqual(ranges, [(0.0, 2.0), (2.0, 4.0)])

    def test_vidu_engine_defaults_to_pro_31_for_similar(self):
        self.assertEqual(_normalize_engine("Vidu Q3 Pro Starter"), "viduq3")
        self.assertEqual(_engine_duration("viduq3", 20), 16)
        self.assertEqual(_suggest_engine_for_detected_mode("realistic"), "viduq3")

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