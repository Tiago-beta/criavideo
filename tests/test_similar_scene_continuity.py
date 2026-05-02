import unittest

from app.services.scene_generator import build_similar_scene_continuity_prompt


class TestSimilarSceneContinuity(unittest.TestCase):
    def test_first_scene_keeps_original_prompt(self):
        prompt = "Mesa de madeira com caixa de ovos azul em close cinematografico."

        generated = build_similar_scene_continuity_prompt(
            prompt,
            anchor_prompt="Primeira cena com caixa de ovos azul e cimento cinza.",
            current_scene_index=0,
            anchor_scene_index=0,
        )

        self.assertEqual(generated, prompt)
        self.assertNotIn("CONTINUIDADE VISUAL OBRIGATORIA", generated)

    def test_later_scene_inherits_scene_one_visual_language(self):
        prompt = "A mao continua mexendo o cimento na casca enquanto a camera se aproxima."
        anchor_prompt = (
            "Close-up de uma caixa de ovos azul sobre mesa de madeira clara, "
            "com cimento cinza dentro das cascas e luz suave de fim de tarde."
        )

        generated = build_similar_scene_continuity_prompt(
            prompt,
            anchor_prompt=anchor_prompt,
            current_scene_index=2,
            anchor_scene_index=0,
        )

        self.assertIn(prompt, generated)
        self.assertIn("CONTINUIDADE VISUAL OBRIGATORIA", generated)
        self.assertIn("mesma paleta de cores", generated)
        self.assertIn("caixa de ovos azul", generated)
        self.assertIn("cimento cinza", generated)


if __name__ == "__main__":
    unittest.main()