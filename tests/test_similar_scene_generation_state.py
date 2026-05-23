import unittest

from app.tasks.similar_tasks import (
    _extract_similar_active_scene_generation_ids,
    _set_similar_active_scene_generation_ids,
)


class TestSimilarSceneGenerationState(unittest.TestCase):
    def test_extract_active_scene_generation_ids_keeps_unique_positive_ids(self):
        tags = {
            "similar_active_scene_generation_ids": [12, "18", 12, 0, "x"],
            "similar_regenerating_scene_id": 25,
        }

        self.assertEqual(_extract_similar_active_scene_generation_ids(tags), [12, 18, 25])

    def test_set_active_scene_generation_ids_dedupes_and_clears_empty(self):
        tags = {"similar_active_scene_generation_ids": [7]}

        normalized = _set_similar_active_scene_generation_ids(tags, [7, "11", 7, 0, None])
        self.assertEqual(normalized, [7, 11])
        self.assertEqual(tags["similar_active_scene_generation_ids"], [7, 11])

        normalized = _set_similar_active_scene_generation_ids(tags, [])
        self.assertEqual(normalized, [])
        self.assertNotIn("similar_active_scene_generation_ids", tags)


if __name__ == "__main__":
    unittest.main()