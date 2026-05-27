import unittest

from app.routers.video import _build_similar_optimized_unified_prompt_fallback


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


if __name__ == "__main__":
    unittest.main()