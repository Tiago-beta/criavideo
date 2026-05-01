import unittest

from app.services.realistic_cover_guidance import (
    apply_cover_guidance,
    build_cover_optimizer_tone,
    decide_cover_guidance,
)


class TestRealisticCoverGuidance(unittest.TestCase):
    def test_defaults_to_photorealistic_without_explicit_stylization(self):
        decision = decide_cover_guidance(prompt="Uma cantora em palco com luz cinematografica")

        self.assertEqual(decision.visual_mode, "photorealistic")
        self.assertEqual(build_cover_optimizer_tone("cinematic", decision.visual_mode), "photorealistic live-action, highly realistic skin, hair, lighting, anatomy, cinematic")

    def test_explicit_stylized_markers_unlock_stylized_mode(self):
        decision = decide_cover_guidance(
            prompt="Uma heroina anime cantando sob chuva neon",
            cover_custom_prompt="quero visual de ilustracao japonesa",
        )

        self.assertEqual(decision.visual_mode, "stylized")
        self.assertIn("anime", decision.stylized_markers)

    def test_human_cover_in_music_flow_adds_identity_performance_lock(self):
        decision = decide_cover_guidance(
            cover_persona="human singer",
            cover_source="tevoxi",
            tevoxi_has_official_cover_reference=True,
            has_reference_image=True,
            image_is_cover_anchor=True,
            music_driven=True,
            cover_context="Cantora com cabelo ruivo e jaqueta de couro preta.",
        )

        guided = apply_cover_guidance("Close-up no refrão da musica.", decision)

        self.assertIn("official Tevoxi cover reference", guided)
        self.assertIn("PERFORMANCE LOCK", guided)
        self.assertIn("COVER CONTEXT LOCK", guided)

    def test_nature_cover_avoids_forcing_human_singer(self):
        decision = decide_cover_guidance(
            cover_persona="nature",
            has_reference_image=True,
            image_is_cover_anchor=True,
        )

        guided = apply_cover_guidance("Cena do amanhecer sobre montanhas.", decision)

        self.assertIn("NATURE COVER LOCK", guided)
        self.assertIn("Do not insert a human singer by default", guided)


if __name__ == "__main__":
    unittest.main()