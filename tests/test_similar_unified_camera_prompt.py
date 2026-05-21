import unittest
from types import SimpleNamespace

from app.routers.video import _build_similar_unified_prompt_fallback, _is_similar_unified_prompt_valid


class TestSimilarUnifiedCameraPrompt(unittest.TestCase):
    def test_unified_fallback_respects_fixed_camera_mode(self):
        project = SimpleNamespace(title="Video semelhante", aspect_ratio="9:16")
        scenes = [
            {
                "prompt": "Baby sits on the floor facing a cat in a living room, static camera on tripod, no camera movement.",
                "start_time": 0.0,
                "end_time": 10.0,
                "lyrics_segment": "",
            }
        ]
        tags_data = {
            "similar_context_summary": "Indoor living room scene with parents in the background and a locked-off static camera.",
            "similar_camera_mode": "fixed",
            "similar_camera_label": "camera fixa/travada",
            "similar_camera_guidance": "Camera fixa/travada: manter o enquadramento principal parado, com a acao acontecendo dentro do quadro, sem pan, tilt, travelling, orbita ou zoom inventado.",
        }

        prompt = _build_similar_unified_prompt_fallback(project, scenes, tags_data)

        self.assertIn("Video cinematografico ultra-realista locked-off", prompt)
        self.assertIn("A cena se desenrola em um unico plano continuo:", prompt)
        self.assertIn("Comportamento de camera: enquadramento fixo", prompt)
        self.assertNotIn("handheld micro-shakes", prompt)
        self.assertTrue(_is_similar_unified_prompt_valid(prompt))

    def test_unified_fallback_preserves_all_scene_prompts_in_order(self):
        project = SimpleNamespace(title="Historia semelhante", aspect_ratio="16:9")
        scenes = [
            {
                "prompt": "Uma mulher abre a porta da cozinha e entra com pressa, segurando uma sacola vermelha.",
                "start_time": 0.0,
                "end_time": 3.0,
                "lyrics_segment": "",
            },
            {
                "prompt": "Ela atravessa o corredor, olha para a janela e percebe algo estranho do lado de fora.",
                "start_time": 3.0,
                "end_time": 6.0,
                "lyrics_segment": "",
            },
            {
                "prompt": "A mulher corre para a sala, abraca a crianca e fecha a cortina enquanto o vento aumenta.",
                "start_time": 6.0,
                "end_time": 9.0,
                "lyrics_segment": "",
            },
        ]

        prompt = _build_similar_unified_prompt_fallback(project, scenes, {})

        first_idx = prompt.find("uma mulher abre a porta da cozinha")
        second_idx = prompt.find("ela atravessa o corredor")
        third_idx = prompt.find("a mulher corre para a sala")

        self.assertGreaterEqual(first_idx, 0)
        self.assertGreaterEqual(second_idx, 0)
        self.assertGreaterEqual(third_idx, 0)
        self.assertLess(first_idx, second_idx)
        self.assertLess(second_idx, third_idx)
        self.assertTrue(_is_similar_unified_prompt_valid(prompt))


if __name__ == "__main__":
    unittest.main()