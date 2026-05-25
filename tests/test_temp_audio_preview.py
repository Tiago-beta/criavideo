import unittest
from unittest.mock import AsyncMock, patch

from app.routers.video import _generate_temp_preview_audio


class TestGenerateTempPreviewAudio(unittest.IsolatedAsyncioTestCase):
    async def test_suno_voice_prefix_uses_suno_narration_service(self):
        with patch(
            "app.services.suno_narration.generate_suno_narration",
            new=AsyncMock(return_value="media/audio/0/temp-suno.mp3"),
        ) as suno_mock, patch(
            "app.services.script_audio.generate_tts_audio",
            new=AsyncMock(return_value="media/audio/0/temp-tts.mp3"),
        ) as tts_mock:
            result = await _generate_temp_preview_audio(
                text="Depois da humilhacao, Lucas sentou em silencio.",
                voice="suno_narrator_male_dramatic",
                voice_type="builtin",
                tts_instructions="",
                pause_level="normal",
                tone="dramatico",
                output_filename="temp-preview-suno.mp3",
            )

        self.assertEqual(result, "media/audio/0/temp-suno.mp3")
        suno_mock.assert_awaited_once_with(
            text="Depois da humilhacao, Lucas sentou em silencio.",
            voice_preset="suno_narrator_male_dramatic",
            project_id=0,
            tone="dramatico",
            output_filename="temp-preview-suno.mp3",
        )
        tts_mock.assert_not_called()

    async def test_non_suno_voice_uses_standard_tts_service(self):
        with patch(
            "app.services.suno_narration.generate_suno_narration",
            new=AsyncMock(return_value="media/audio/0/temp-suno.mp3"),
        ) as suno_mock, patch(
            "app.services.script_audio.generate_tts_audio",
            new=AsyncMock(return_value="media/audio/0/temp-tts.mp3"),
        ) as tts_mock:
            result = await _generate_temp_preview_audio(
                text="Texto de narracao normal.",
                voice="UgBBYS2sOqTuMpoF3BR0",
                voice_type="elevenlabs",
                tts_instructions="fale com calma",
                pause_level="deep",
                tone="profundo",
                output_filename="temp-preview-tts.mp3",
            )

        self.assertEqual(result, "media/audio/0/temp-tts.mp3")
        tts_mock.assert_awaited_once_with(
            "Texto de narracao normal.",
            voice="UgBBYS2sOqTuMpoF3BR0",
            project_id=0,
            tts_instructions="fale com calma",
            voice_type="elevenlabs",
            pause_level="deep",
            tone="profundo",
            output_filename="temp-preview-tts.mp3",
        )
        suno_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()