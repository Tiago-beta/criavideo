import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.routers.video import _extract_explicit_scene_dialogue
from app.tasks.similar_tasks import (
    _analyze_frame_prompt,
    _extract_scene_analysis_from_content,
    _build_scene_analysis_instruction,
    _build_similar_general_prompt_fallback,
    _build_similar_scene_generation_context,
    _build_similar_scene_speech_lock,
    _extract_scene_prompt_from_content,
    _extract_scene_transcript_excerpt,
    _infer_similar_camera_profile,
)


def _fake_openai_response(content):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
            )
        ]
    )


class TestSimilarFramePrompt(unittest.IsolatedAsyncioTestCase):
    def test_extract_explicit_scene_dialogue_prefers_dialogue_timing_quotes(self):
        dialogue = _extract_explicit_scene_dialogue(
            """
            0.0s - 3.0s
            Mulher apresenta o produto em close.

            Dialogue timing:
            0.0s - 1.5s | Speaker: Mulher
            \"Chegou o painel que eu estava esperando.\"
            1.5s - 3.0s | Speaker: Mulher
            \"Agora vou renovar minha casa sem bagunca.\"
            """
        )

        self.assertIn("Chegou o painel", dialogue)
        self.assertIn("renovar minha casa", dialogue)

    def test_extract_explicit_scene_dialogue_accepts_braces(self):
        dialogue = _extract_explicit_scene_dialogue(
            "Mulher instala o painel com luz natural entrando pela janela.\n\nFala em PT-BR: {Estou muito feliz, agora vou renovar minha sala.}"
        )

        self.assertEqual(dialogue, "Estou muito feliz, agora vou renovar minha sala.")

    def test_build_similar_scene_speech_lock_keeps_exact_phrase(self):
        prompt = _build_similar_scene_speech_lock(
            "Mulher instala o painel ripado com luz natural entrando pela janela.",
            "Chegou o painel que eu estava esperando.",
        )

        self.assertIn("FALA OBRIGATORIA EM PT-BR", prompt)
        self.assertIn('"Chegou o painel que eu estava esperando."', prompt)

    def test_build_scene_analysis_instruction_includes_video_context(self):
        instruction = _build_scene_analysis_instruction(
            0.0,
            3.9,
            19.0,
            global_context="O vídeo mostra um leão adulto descansando enquanto um filhote se aproxima de forma curiosa.",
            spoken_context="Sem fala, apenas sons naturais da savana.",
        )

        self.assertIn("português do Brasil", instruction)
        self.assertIn("acentuação", instruction)
        self.assertIn("Contexto geral do vídeo", instruction)
        self.assertIn("Audio de referencia", instruction)
        self.assertIn("câmera parece fixa/travada", instruction)
        self.assertIn("som ambiente natural e diegetico", instruction)
        self.assertNotIn("fala principal entre aspas", instruction)

    def test_build_scene_analysis_instruction_uses_two_frame_timeline_and_no_text_rule(self):
        instruction = _build_scene_analysis_instruction(
            0.0,
            5.0,
            19.0,
            frame_count=2,
        )

        self.assertIn("frames inicial e final", instruction)
        self.assertIn("frame inicial", instruction)
        self.assertIn("nada escrito na tela", instruction)

    def test_infer_similar_camera_profile_detects_fixed_camera(self):
        profile = _infer_similar_camera_profile(
            [
                {
                    "prompt": "Bebê sentado de frente para um gato em sala interna, com câmera fixa em tripé e ação acontecendo dentro do quadro.",
                    "spoken_context": "Sem fala.",
                }
            ],
            "Casal observa ao fundo no sofá; a câmera permanece parada durante todo o clipe.",
        )

        self.assertEqual(profile["mode"], "fixed")
        self.assertIn("Camera fixa/travada", profile["guidance_pt"])

    def test_general_prompt_fallback_keeps_fixed_camera_rule(self):
        prompt = _build_similar_general_prompt_fallback(
            scene_payloads=[
                {
                    "prompt": "Bebê encara o gato sentado no mesmo ponto da sala, com câmera fixa e sem mudança de posição.",
                    "spoken_context": "",
                }
            ],
            context_summary="Vídeo interno curto com câmera fixa/travada observando a interação do bebê com o gato.",
            transcript_text="",
            duration_seconds=10.0,
            camera_mode="fixed",
            camera_guidance_pt="Camera fixa/travada: manter o enquadramento principal parado, com a acao acontecendo dentro do quadro, sem pan, tilt, travelling, orbita ou zoom inventado.",
        )

        self.assertIn("Camera fixa/travada", prompt)
        self.assertIn("Som ambiente natural e diegetico", prompt)
        self.assertIn("Nunca gerar fala", prompt)
        self.assertNotIn("camera entrando suavemente na acao", prompt)

    def test_build_scene_generation_context_sanitizes_prompt_to_natural_audio(self):
        scene = SimpleNamespace(
            id=7,
            scene_index=0,
            prompt=(
                "Mulher instala o painel ripado com as duas maos em close na parede iluminada.\n\n"
                "SEM TEXTO NA TELA (OBRIGATORIO): remova qualquer texto, legenda, logotipo, marca d'agua, letreiro, rotulo ou palavra visivel.\n\n"
                "FALA OBRIGATORIA EM PT-BR: use exatamente esta fala no audio da cena, sem resumir: \"Chegou o painel que eu estava esperando.\""
            ),
            lyrics_segment="Chegou o painel que eu estava esperando.",
            image_path="",
        )

        prompt, reference_image_path = _build_similar_scene_generation_context(scene)

        self.assertEqual(reference_image_path, "")
        self.assertIn("AUDIO NATURAL (OBRIGATORIO)", prompt)
        self.assertIn("Som ambiente natural e diegetico", prompt)
        self.assertIn("sem qualquer texto visivel", prompt)
        self.assertNotIn("SEM TEXTO NA TELA (OBRIGATORIO)", prompt)
        self.assertNotIn("FALA OBRIGATORIA EM PT-BR", prompt)
        self.assertNotIn("Chegou o painel que eu estava esperando", prompt)

    def test_extract_scene_analysis_from_content_keeps_visible_text_metadata(self):
        analysis = _extract_scene_analysis_from_content(
            '{"scene_prompt": "Mulher monta uma estante de madeira em bancada clara.", '
            '"visible_text_detected": true, '
            '"visible_text_excerpt": "DIY Double C Shelf - Easy Build"}'
        )

        self.assertEqual(analysis["scene_prompt"], "Mulher monta uma estante de madeira em bancada clara.")
        self.assertTrue(analysis["visible_text_detected"])
        self.assertEqual(analysis["visible_text_excerpt"], "DIY Double C Shelf - Easy Build")

    def test_extract_scene_transcript_excerpt_uses_scene_window(self):
        excerpt = _extract_scene_transcript_excerpt(
            [
                {"word": "o", "start": 0.0, "end": 0.2},
                {"word": "leão", "start": 0.2, "end": 0.4},
                {"word": "descansa", "start": 0.4, "end": 0.8},
                {"word": "enquanto", "start": 2.5, "end": 2.9},
                {"word": "o", "start": 2.9, "end": 3.0},
                {"word": "filhote", "start": 3.0, "end": 3.4},
                {"word": "chega", "start": 3.4, "end": 3.7},
            ],
            2.6,
            3.8,
        )

        self.assertIn("filhote", excerpt)
        self.assertIn("chega", excerpt)
        self.assertNotIn("leão descansa", excerpt)

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

    async def test_analyze_frame_prompt_sends_two_reference_images_when_available(self):
        create_mock = AsyncMock(
            return_value=_fake_openai_response(
                "Uma mulher monta uma estante de madeira sobre bancada escura, indo da primeira peca ao encaixe final em cinco segundos."
            )
        )
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create_mock)))

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_start, tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp_end:
            tmp_start.write(b"fake-image")
            tmp_end.write(b"fake-image")
            tmp_start_path = tmp_start.name
            tmp_end_path = tmp_end.name

        try:
            await _analyze_frame_prompt(client, [tmp_start_path, tmp_end_path], 0.0, 5.0, 19.0)
        finally:
            os.unlink(tmp_start_path)
            os.unlink(tmp_end_path)

        user_content = create_mock.await_args.kwargs["messages"][1]["content"]
        image_parts = [part for part in user_content if part.get("type") == "image_url"]
        self.assertEqual(len(image_parts), 2)

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

    async def test_analyze_frame_prompt_includes_detected_text_to_remove(self):
        create_mock = AsyncMock(
            return_value=_fake_openai_response(
                '{"scene_prompt": "Mulher monta uma estante de madeira sobre bancada escura, mantendo o mesmo enquadramento do tutorial.", '
                '"visible_text_detected": true, '
                '"visible_text_excerpt": "DIY Double C Shelf - Easy Build"}'
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

        self.assertIn("DIY Double C Shelf - Easy Build", prompt)
        self.assertIn("SEM TEXTO NA TELA", prompt)

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