import json
import unittest
from types import SimpleNamespace

from app.routers import publish as publish_router


class _FakeCompletions:
    def __init__(self, payloads):
        self._payloads = list(payloads)

    async def create(self, **kwargs):
        if not self._payloads:
            raise AssertionError("Unexpected extra OpenAI call")
        payload = self._payloads.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
                )
            ]
        )


class _FakeOpenAI:
    def __init__(self, payloads):
        self.chat = SimpleNamespace(completions=_FakeCompletions(payloads))


class _FakeDB:
    def __init__(self, render, project):
        self.render = render
        self.project = project

    async def get(self, model, item_id):
        if model is publish_router.VideoRender and item_id == self.render.id:
            return self.render
        if model is publish_router.VideoProject and item_id == self.project.id:
            return self.project
        return None


class TestPublishAISuggest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._original_openai = publish_router._openai

    def tearDown(self):
        publish_router._openai = self._original_openai

    def _build_render(self):
        return SimpleNamespace(
            id=42,
            project_id=7,
            file_path="missing-render.mp4",
            duration=0,
        )

    def _build_project(self):
        return SimpleNamespace(
            id=7,
            user_id=1,
            title="Gratidão no entardecer",
            track_title="Gratidão no entardecer",
            track_artist="Olevita",
            style_prompt="",
            lyrics_text="Obrigado meu Deus, por tanta bênção, por me guiar com a tua mão.",
            description=(
                "Vídeo musical sobre gratidão, pôr do sol, serenidade e beleza natural. "
                "A mensagem principal fala sobre fé, calma e contemplação da natureza."
            ),
            tags=["gratidão", "beleza natural", "pôr do sol", "serenidade", "música brasileira"],
        )

    async def test_strips_generic_hashtags_and_meta_title_terms(self):
        publish_router._openai = _FakeOpenAI([
            {
                "keywords": ["gratidão", "beleza natural", "pôr do sol"],
                "angle": "mensagem de fé e serenidade",
                "audience": "quem gosta de música com mensagem positiva",
                "emotion": "serenidade",
                "element": "pôr do sol no campo",
                "titles": ["Gratidão: contexto e descrição do vídeo"],
                "selected_title": "Gratidão: contexto e descrição do vídeo",
                "description": (
                    "Gratidão no entardecer revela uma mensagem de fé, serenidade e beleza natural. "
                    "A letra convida o público a contemplar o pôr do sol, agradecer pelas bênçãos e sentir calma em cada verso. "
                    "Assista até o final para ouvir a mensagem completa e compartilhar essa atmosfera de paz."
                ),
                "hashtags": "#Gratidão #BelezaNatural\n#IdeiaGenial #VideoViral",
                "tags": ["gratidão", "beleza natural", "pôr do sol", "serenidade"],
                "thumbnail_hook": "GRATIDÃO REAL",
                "thumbnail_prompt": "close no entardecer",
            },
            {
                "chosen_title": "Gratidão: contexto e descrição do vídeo",
                "description": (
                    "Gratidão no entardecer revela uma mensagem de fé, serenidade e beleza natural. "
                    "A letra convida o público a contemplar o pôr do sol, agradecer pelas bênçãos e sentir calma em cada verso. "
                    "Assista até o final para ouvir a mensagem completa e compartilhar essa atmosfera de paz."
                ),
                "hashtags": "#Gratidão #BelezaNatural\n#IdeiaGenial #VideoViral",
                "tags": ["gratidão", "beleza natural", "pôr do sol", "serenidade"],
                "thumbnail_hook": "GRATIDÃO REAL",
                "thumbnail_prompt": "close no entardecer",
            },
        ])
        db = _FakeDB(self._build_render(), self._build_project())

        result = await publish_router.ai_suggest(
            publish_router.AISuggestRequest(render_id=42),
            user={"id": 1},
            db=db,
        )

        self.assertNotIn("contexto", result["title"].lower())
        self.assertNotIn("descrição", result["title"].lower())
        self.assertNotIn("#IdeiaGenial", result["hashtags"])
        self.assertNotIn("#VideoViral", result["hashtags"])
        self.assertIn("#Gratidão", result["hashtags"])
        self.assertIn("#BelezaNatural", result["hashtags"])

    async def test_does_not_inject_generic_hashtags_when_ai_returns_none(self):
        publish_router._openai = _FakeOpenAI([
            {
                "keywords": ["gratidão", "serenidade", "pôr do sol"],
                "angle": "canção contemplativa",
                "audience": "quem busca música calma",
                "emotion": "calma",
                "element": "natureza ao entardecer",
                "titles": ["Gratidão no entardecer"],
                "selected_title": "Gratidão no entardecer",
                "description": (
                    "Gratidão no entardecer traz uma canção contemplativa sobre fé, calma e natureza. "
                    "A música acompanha imagens do pôr do sol e reforça uma mensagem de serenidade em cada verso. "
                    "Ouça até o fim para sentir a atmosfera completa dessa paisagem musical."
                ),
                "hashtags": "",
                "tags": ["gratidão", "beleza natural", "pôr do sol", "serenidade", "música brasileira"],
                "thumbnail_hook": "PAZ NO ENTARDECER",
                "thumbnail_prompt": "campo ao pôr do sol",
            },
            {
                "chosen_title": "Gratidão no entardecer",
                "description": (
                    "Gratidão no entardecer traz uma canção contemplativa sobre fé, calma e natureza. "
                    "A música acompanha imagens do pôr do sol e reforça uma mensagem de serenidade em cada verso. "
                    "Ouça até o fim para sentir a atmosfera completa dessa paisagem musical."
                ),
                "hashtags": "",
                "tags": ["gratidão", "beleza natural", "pôr do sol", "serenidade", "música brasileira"],
                "thumbnail_hook": "PAZ NO ENTARDECER",
                "thumbnail_prompt": "campo ao pôr do sol",
            },
        ])
        db = _FakeDB(self._build_render(), self._build_project())

        result = await publish_router.ai_suggest(
            publish_router.AISuggestRequest(render_id=42),
            user={"id": 1},
            db=db,
        )

        self.assertNotIn("#IdeiaGenial", result["hashtags"])
        self.assertNotIn("#VideoViral", result["hashtags"])
        self.assertNotIn("#DicaCriativa", result["hashtags"])
        self.assertNotIn("#VejaIsso", result["hashtags"])
        self.assertIn("#Gratidão", result["hashtags"])
        self.assertIn("#Serenidade", result["hashtags"])


if __name__ == "__main__":
    unittest.main()