import unittest

from app.services.scene_generator import (
    _build_frame_edit_prompt,
    _sanitize_frame_edit_scene_context,
    build_similar_scene_continuity_prompt,
)


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

    def test_frame_edit_context_drops_person_identity_and_continuity_rules(self):
        context = _sanitize_frame_edit_scene_context(
            "A cena mostra uma mulher morena ajoelhada no piso branco, usando camiseta branca e shorts vermelhos. "
            "A luz natural entra pela janela e ilumina a parede clara e os paineis de madeira. "
            "CONTINUIDADE VISUAL OBRIGATORIA: preserve exatamente a mesma identidade visual da personagem. "
            "AJUSTE VISUAL SOLICITADO: trocar por uma mulher loira da imagem nova."
        )

        self.assertIn("luz natural entra pela janela", context)
        self.assertIn("parede clara", context)
        self.assertNotIn("mulher morena", context)
        self.assertNotIn("shorts vermelhos", context)
        self.assertNotIn("CONTINUIDADE VISUAL OBRIGATORIA", context)
        self.assertNotIn("AJUSTE VISUAL SOLICITADO", context)

    def test_frame_edit_prompt_makes_uploaded_reference_win_for_identity(self):
        prompt = _build_frame_edit_prompt(
            "Trocar a mulher morena do frame por essa loira da imagem nova",
            "A cena mostra uma mulher morena ajoelhada no piso branco. A luz natural entra pela janela e ilumina a parede clara.",
        )

        self.assertIn("as imagens adicionais vencem", prompt)
        self.assertIn("Nao reutilize rosto, cabelo, cor de pele, corpo ou roupa", prompt)
        self.assertIn("Contexto do ambiente a preservar", prompt)
        self.assertNotIn("mulher morena ajoelhada", prompt)


if __name__ == "__main__":
    unittest.main()