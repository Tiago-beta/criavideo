import unittest

from app.routers.video import (
    _build_similar_optimized_unified_prompt_fallback,
    _extract_similar_optimized_scene_specs,
)


class TestSimilarUnifiedPromptOptimization(unittest.TestCase):
    def test_fallback_builds_scene_script_with_selected_timing(self):
        base_prompt = (
            "Ultra-realistic cinematic handheld video set in a rustic bakery street. Natural environmental audio with footsteps, tools, and oven heat. "
            "Warm morning light crosses the door while the main baker keeps the same face, wardrobe, and energy throughout the whole scene."
        )

        prompt = _build_similar_optimized_unified_prompt_fallback(base_prompt, "9:16", 30, 5)

        self.assertIn("GANCHO CENTRAL", prompt)
        self.assertIn("FORMATO: 9:16", prompt)
        self.assertIn("DURACAO TOTAL: 30s", prompt)
        self.assertIn("TEMPO POR CENA: 5s", prompt)
        self.assertIn("TOTAL DE CENAS: 6", prompt)
        self.assertIn("CENA 1 (0s-5s)", prompt)
        self.assertIn("CENA 6 (25s-30s)", prompt)
        self.assertIn("Prompt completo:", prompt)
        self.assertIn("scroll-stopping", prompt.lower())

    def test_extract_scene_specs_from_optimized_prompt(self):
        base_prompt = (
            "Ultra-realistic cinematic handheld video set in a rustic bakery street. Natural environmental audio with footsteps, tools, and oven heat. "
            "Warm morning light crosses the door while the main baker keeps the same face, wardrobe, and energy throughout the whole scene."
        )

        prompt = _build_similar_optimized_unified_prompt_fallback(base_prompt, "9:16", 30, 5)
        scene_specs = _extract_similar_optimized_scene_specs(prompt, 30, 5)

        self.assertEqual(len(scene_specs), 6)
        self.assertEqual(scene_specs[0]["scene_index"], 0)
        self.assertEqual(scene_specs[0]["start_time"], 0.0)
        self.assertEqual(scene_specs[0]["end_time"], 5.0)
        self.assertIn("scene 1 of 6", scene_specs[0]["prompt"].lower())
        self.assertEqual(scene_specs[-1]["scene_index"], 5)
        self.assertEqual(scene_specs[-1]["start_time"], 25.0)
        self.assertEqual(scene_specs[-1]["end_time"], 30.0)
        self.assertIn("scene 6 of 6", scene_specs[-1]["prompt"].lower())


if __name__ == "__main__":
    unittest.main()