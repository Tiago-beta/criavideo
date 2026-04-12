"""
Tevoxi Music Integration — Generate music via Levita's create-music API.
"""
import asyncio
import logging
import os
from pathlib import Path

import httpx
import openai

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)


async def expand_theme_to_music_prompt(theme: str) -> dict:
    """Use GPT-4o-mini to expand a simple theme into Suno-compatible music parameters."""
    try:
        resp = await _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Voce e um especialista em producao musical. "
                        "Dado um tema, gere parametros para criar uma musica. "
                        "Responda APENAS um JSON valido com: "
                        '{"prompt": "descricao da musica em ingles", '
                        '"genres": ["genre1", "genre2"], '
                        '"mood": "mood", '
                        '"vocalist": "m" ou "f" ou "duet", '
                        '"mode": "generate", '
                        '"title_suggestion": "titulo em portugues"}'
                    ),
                },
                {"role": "user", "content": f"Tema: {theme}"},
            ],
            temperature=0.7,
            max_tokens=300,
            response_format={"type": "json_object"},
        )
        import json
        data = json.loads(resp.choices[0].message.content)
        return {
            "prompt": data.get("prompt", theme),
            "genres": data.get("genres", ["pop"]),
            "mood": data.get("mood", ""),
            "vocalist": data.get("vocalist", "m"),
            "mode": data.get("mode", "generate"),
            "title_suggestion": data.get("title_suggestion", theme),
        }
    except Exception as e:
        logger.warning("Failed to expand theme to music prompt: %s", e)
        return {
            "prompt": theme,
            "genres": ["pop"],
            "mood": "",
            "vocalist": "m",
            "mode": "generate",
            "title_suggestion": theme,
        }


async def generate_music_from_theme(
    theme: str,
    project_id: int,
    duration: int = 120,
    language: str = "pt-BR",
    manual_settings: dict | None = None,
) -> dict:
    """Generate music via Tevoxi API and download the audio file.

    Returns: {"audio_path": str, "title": str, "lyrics": str, "duration": float}
    """
    api_url = settings.tevoxi_api_url.rstrip("/")
    api_token = settings.tevoxi_api_token

    # Generate JWT on-the-fly if jwt_secret is configured
    if not api_token and settings.tevoxi_jwt_secret:
        from jose import jwt as jose_jwt
        import time
        payload = {
            "id": settings.tevoxi_jwt_user_id,
            "email": settings.tevoxi_jwt_email,
            "role": "admin",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        }
        api_token = jose_jwt.encode(payload, settings.tevoxi_jwt_secret, algorithm="HS256")

    if not api_token:
        raise RuntimeError("TEVOXI_API_TOKEN ou TEVOXI_JWT_SECRET nao configurado.")

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }

    if manual_settings:
        # Use manual music settings from the user
        music_mode = manual_settings.get("music_mode", "generate")
        vocalist = manual_settings.get("music_vocalist", "female")
        if music_mode == "instrumental":
            vocalist = ""
            music_mode = "instrumental"

        payload = {
            "prompt": theme,
            "mode": music_mode,
            "genres": [manual_settings.get("music_genre", "pop")],
            "vocalist": vocalist,
            "language": manual_settings.get("music_language", language),
            "mood": manual_settings.get("music_mood", ""),
            "duration": manual_settings.get("music_duration") or min(max(duration, 30), 240),
        }
        custom_lyrics = manual_settings.get("music_lyrics", "")
        if music_mode == "lyrics" and custom_lyrics:
            payload["customLyrics"] = custom_lyrics
    else:
        # Auto mode: use AI to expand theme into music parameters
        params = await expand_theme_to_music_prompt(theme)
        payload = {
            "prompt": params["prompt"],
            "mode": params["mode"],
            "genres": params["genres"],
            "vocalist": params["vocalist"],
            "language": language,
            "mood": params["mood"],
            "duration": min(max(duration, 30), 240),
        }

    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Start generation
        resp = await client.post(f"{api_url}/api/create-music", json=payload, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"Tevoxi generation failed: {resp.status_code} {resp.text[:200]}")

        data = resp.json()
        job_id = data.get("id")
        if not job_id:
            raise RuntimeError(f"Tevoxi did not return job ID: {data}")

        logger.info("Tevoxi music generation started: job_id=%s for project %d", job_id, project_id)

        # 2. Poll for completion (max 5 min)
        for _ in range(60):
            await asyncio.sleep(5)
            status_resp = await client.get(
                f"{api_url}/api/create-music/status/{job_id}",
                headers=headers,
            )
            if status_resp.status_code != 200:
                continue

            status_data = status_resp.json()
            status = status_data.get("status", "")

            if status == "completed":
                title = status_data.get("title", params.get("title_suggestion", theme))
                lyrics = status_data.get("lyrics", "")
                music_duration = status_data.get("duration", duration)

                # 3. Download audio
                audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
                audio_dir.mkdir(parents=True, exist_ok=True)
                audio_path = audio_dir / "tevoxi_music.mp3"

                audio_resp = await client.get(
                    f"{api_url}/api/create-music/audio/{job_id}",
                    headers=headers,
                )
                if audio_resp.status_code != 200:
                    raise RuntimeError(f"Failed to download Tevoxi audio: {audio_resp.status_code}")

                with open(audio_path, "wb") as f:
                    f.write(audio_resp.content)

                logger.info("Tevoxi music downloaded: %s (%d bytes)", audio_path, len(audio_resp.content))

                return {
                    "audio_path": str(audio_path),
                    "title": title,
                    "lyrics": lyrics,
                    "duration": music_duration,
                }

            elif status == "failed":
                error = status_data.get("message", "Unknown error")
                raise RuntimeError(f"Tevoxi generation failed: {error}")

        raise RuntimeError("Tevoxi generation timed out after 5 minutes")
