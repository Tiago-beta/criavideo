import unittest

from app.services.thumbnail_generator import _build_thumbnail_prompt


class TestThumbnailGeneratorPrompt(unittest.TestCase):
    def test_prompt_bans_generic_icons_and_arrows(self):
        prompt = _build_thumbnail_prompt(
            title_text="Hipnose para dormir rapido",
            description_text="Video para relaxar e dormir melhor.",
            hook_text="DORMIR RAPIDO",
            mood="sono profundo",
            style_hint="cinematico",
            strategy_prompt="",
            has_reference_image=False,
        )

        self.assertIn("NUNCA usar setas, circulos, selos, stickers, emojis", prompt)
        self.assertIn("a curiosidade visual deve nascer da cena", prompt)

    def test_sleep_prompt_requests_serene_positive_human_image(self):
        prompt = _build_thumbnail_prompt(
            title_text="Hipnose dormir profundo",
            description_text="Ajuda a relaxar antes de dormir.",
            hook_text="SONO PROFUNDO",
            mood="relaxamento e calma",
            style_hint="",
            strategy_prompt="",
            has_reference_image=False,
        )

        self.assertIn("expressao serena, calma e confiavel", prompt)
        self.assertIn("transmitindo alivio, seguranca e bem-estar", prompt)

    def test_general_prompt_requests_positive_human_image(self):
        prompt = _build_thumbnail_prompt(
            title_text="Motivacao para vencer",
            description_text="Mensagem inspiradora para comecar o dia.",
            hook_text="FORCA TOTAL",
            mood="esperanca",
            style_hint="",
            strategy_prompt="",
            has_reference_image=False,
        )

        self.assertIn("expressao positiva, confiante ou acolhedora", prompt)
        self.assertIn("transmitindo algo bom e verdadeiro", prompt)


if __name__ == "__main__":
    unittest.main()