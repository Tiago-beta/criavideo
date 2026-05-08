import unittest
from types import SimpleNamespace

from app.routers.video import _build_similar_unified_prompt_fallback


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

        self.assertIn("Ultra-realistic cinematic locked-off video", prompt)
        self.assertIn("locked-off static framing", prompt)
        self.assertNotIn("handheld micro-shakes", prompt)


if __name__ == "__main__":
    unittest.main()