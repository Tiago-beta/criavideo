import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.tasks.similar_tasks import _analyze_frame_prompt, _extract_scene_prompt_from_content


def _fake_openai_response(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]
    )


class TestSimilarFramePrompt(unittest.IsolatedAsyncioTestCase):
    def test_extract_scene_prompt_from_plain_text(self):
        prompt = _extract_scene_prompt_from_content(
            "Uma mulher de vestido vermelho caminha por uma avenida ensolarada, com camera seguindo por tras em plano medio."
        )

        self.assertIn("mulher", prompt)
        self.assertIn("avenida", prompt)
        self.assertNotIn("scene_prompt:", prompt.lower())

    async def test_analyze_frame_prompt_accepts_plain_text_response(self):
        create_mock = AsyncMock(
            return_value=_fake_openai_response(
                "Uma mulher de vestido vermelho caminha por uma avenida ensolarada, com camera seguindo por tras em plano medio."
            )
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock)))

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(b"fake-image")
            tmp_path = tmp.name

        try:
            prompt = await _analyze_frame_prompt(client, tmp_path, 0.0, 3.9, 19.0)
        finally:
            os.unlink(tmp_path)

        self.assertIn("mulher", prompt)
        self.assertIn("avenida", prompt)
        self.assertEqual(create_mock.await_count, 1)

    async def test_analyze_frame_prompt_retries_after_structured_failure(self):
        create_mock = AsyncMock(
            side_effect=[
                RuntimeError("structured output unsupported"),
                _fake_openai_response(
                    "Um homem mistura cimento dentro de cascas de ovo em close sobre uma mesa, com luz natural e camera proxima das maos."
                ),
            ]
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock)))

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(b"fake-image")
            tmp_path = tmp.name

        try:
            prompt = await _analyze_frame_prompt(client, tmp_path, 0.0, 3.9, 19.0)
        finally:
            os.unlink(tmp_path)

        self.assertIn("cimento", prompt)
        self.assertIn("cascas de ovo", prompt)
        self.assertEqual(create_mock.await_count, 2)

    async def test_analyze_frame_prompt_uses_google_after_openai_quota_error(self):
        class FakeQuotaError(RuntimeError):
            status_code = 429

        create_mock = AsyncMock(
            side_effect=FakeQuotaError("Error code: 429 - {'error': {'code': 'insufficient_quota'}}")
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock)))

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(b"fake-image")
            tmp_path = tmp.name

        try:
            with patch(
                "app.tasks.similar_tasks._request_scene_prompt_from_google",
                new=AsyncMock(
                    return_value="Uma artesa quebra cascas de ovo e mistura o po em uma tigela sobre bancada rustica, em close com luz lateral suave."
                ),
            ) as google_mock:
                prompt = await _analyze_frame_prompt(client, tmp_path, 0.0, 3.9, 19.0)
        finally:
            os.unlink(tmp_path)

        self.assertIn("cascas de ovo", prompt)
        self.assertEqual(create_mock.await_count, 1)
        self.assertEqual(google_mock.await_count, 1)


if __name__ == "__main__":
    unittest.main()