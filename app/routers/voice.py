"""
Voice Router — Manage voice profiles: built-in voices, custom voice cloning via OpenAI,
record/upload samples, set default voice.
"""
import os
import logging
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel
from typing import Optional
import openai

from app.auth import get_current_user
from app.database import get_db
from app.models import VoiceProfile
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])
settings = get_settings()
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)

# Built-in voices available
BUILTIN_VOICES = [
    {"id": "onyx", "name": "Masculina Grave", "gender": "male", "model": "tts-1-hd"},
    {"id": "echo", "name": "Masculina Suave", "gender": "male", "model": "tts-1-hd"},
    {"id": "ash", "name": "Masculina Natural", "gender": "male", "model": "tts-1-hd"},
    {"id": "nova", "name": "Feminina Clara", "gender": "female", "model": "tts-1-hd"},
    {"id": "shimmer", "name": "Feminina Suave", "gender": "female", "model": "tts-1-hd"},
    {"id": "coral", "name": "Feminina Natural", "gender": "female", "model": "tts-1-hd"},
    {"id": "alloy", "name": "Neutra", "gender": "neutral", "model": "tts-1-hd"},
    {"id": "fable", "name": "Narrativa", "gender": "neutral", "model": "tts-1-hd"},
    {"id": "sage", "name": "Calma e Clara", "gender": "neutral", "model": "tts-1-hd"},
]

# Portuguese consent phrase required by OpenAI Custom Voices API
CONSENT_PHRASE_PT = (
    "Eu sou o proprietário desta voz e autorizo o OpenAI "
    "a usá-la para criar um modelo de voz sintética."
)


class CreateVoiceProfileRequest(BaseModel):
    name: str
    builtin_voice: str
    tts_instructions: str = ""
    is_default: bool = False


class UpdateVoiceProfileRequest(BaseModel):
    name: Optional[str] = None
    tts_instructions: Optional[str] = None
    is_default: Optional[bool] = None


@router.get("/builtin")
async def list_builtin_voices():
    """List all available built-in OpenAI voices."""
    return BUILTIN_VOICES


@router.get("/profiles")
async def list_voice_profiles(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all voice profiles for the current user."""
    result = await db.execute(
        select(VoiceProfile)
        .where(VoiceProfile.user_id == user["id"])
        .order_by(VoiceProfile.is_default.desc(), VoiceProfile.created_at.desc())
    )
    profiles = result.scalars().all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "voice_type": p.voice_type,
            "builtin_voice": p.builtin_voice,
            "has_custom_voice": bool(p.openai_voice_id),
            "has_sample": bool(p.sample_path),
            "sample_url": f"/video/media/voices/{user['id']}/{p.id}/sample.webm" if p.sample_path else None,
            "tts_instructions": p.tts_instructions or "",
            "is_default": p.is_default,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in profiles
    ]


@router.post("/profiles")
async def create_voice_profile(
    req: CreateVoiceProfileRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new voice profile (from built-in voice)."""
    valid_ids = [v["id"] for v in BUILTIN_VOICES]
    if req.builtin_voice not in valid_ids:
        raise HTTPException(status_code=400, detail=f"Voz invalida. Opcoes: {valid_ids}")

    if req.is_default:
        await db.execute(
            update(VoiceProfile)
            .where(VoiceProfile.user_id == user["id"])
            .values(is_default=False)
        )

    profile = VoiceProfile(
        user_id=user["id"],
        name=req.name,
        voice_type="builtin",
        builtin_voice=req.builtin_voice,
        tts_instructions=req.tts_instructions,
        is_default=req.is_default,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return {"id": profile.id, "name": profile.name, "voice_type": profile.voice_type}


@router.put("/profiles/{profile_id}")
async def update_voice_profile(
    profile_id: int,
    req: UpdateVoiceProfileRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a voice profile."""
    profile = await db.get(VoiceProfile, profile_id)
    if not profile or profile.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Perfil nao encontrado")

    if req.name is not None:
        profile.name = req.name
    if req.tts_instructions is not None:
        profile.tts_instructions = req.tts_instructions
    if req.is_default is not None:
        if req.is_default:
            await db.execute(
                update(VoiceProfile)
                .where(VoiceProfile.user_id == user["id"])
                .values(is_default=False)
            )
        profile.is_default = req.is_default

    await db.commit()
    return {"ok": True}


@router.delete("/profiles/{profile_id}")
async def delete_voice_profile(
    profile_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a voice profile."""
    profile = await db.get(VoiceProfile, profile_id)
    if not profile or profile.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Perfil nao encontrado")

    # Clean up files
    voice_dir = Path(settings.media_dir) / "voices" / str(user["id"]) / str(profile_id)
    if voice_dir.exists():
        import shutil
        shutil.rmtree(voice_dir, ignore_errors=True)

    await db.delete(profile)
    await db.commit()
    return {"deleted": True}


@router.post("/profiles/{profile_id}/set-default")
async def set_default_voice(
    profile_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Set a voice profile as the user's default."""
    profile = await db.get(VoiceProfile, profile_id)
    if not profile or profile.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Perfil nao encontrado")

    await db.execute(
        update(VoiceProfile)
        .where(VoiceProfile.user_id == user["id"])
        .values(is_default=False)
    )
    profile.is_default = True
    await db.commit()
    return {"ok": True, "default_id": profile_id}


@router.get("/default")
async def get_default_voice(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the user's default voice profile."""
    result = await db.execute(
        select(VoiceProfile)
        .where(VoiceProfile.user_id == user["id"], VoiceProfile.is_default == True)
    )
    profile = result.scalar_one_or_none()
    if not profile:
        return {"id": None, "builtin_voice": "onyx", "voice_type": "builtin", "name": "Padrao"}
    return {
        "id": profile.id,
        "name": profile.name,
        "voice_type": profile.voice_type,
        "builtin_voice": profile.builtin_voice,
        "has_custom_voice": bool(profile.openai_voice_id),
        "tts_instructions": profile.tts_instructions or "",
    }


@router.post("/profiles/{profile_id}/upload-sample")
async def upload_voice_sample(
    profile_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a voice sample for a profile (for custom voice cloning)."""
    profile = await db.get(VoiceProfile, profile_id)
    if not profile or profile.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Perfil nao encontrado")

    allowed = {"audio/webm", "audio/wav", "audio/mpeg", "audio/mp4", "audio/ogg", "audio/flac",
               "audio/x-wav", "audio/mp3", "video/webm"}
    if file.content_type and file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Formato de audio nao suportado")

    voice_dir = Path(settings.media_dir) / "voices" / str(user["id"]) / str(profile_id)
    voice_dir.mkdir(parents=True, exist_ok=True)

    ext = ".webm"
    if file.content_type:
        ext_map = {"audio/wav": ".wav", "audio/x-wav": ".wav", "audio/mpeg": ".mp3",
                   "audio/mp3": ".mp3", "audio/mp4": ".m4a", "audio/ogg": ".ogg",
                   "audio/flac": ".flac", "audio/webm": ".webm", "video/webm": ".webm"}
        ext = ext_map.get(file.content_type, ".webm")

    sample_path = str(voice_dir / f"sample{ext}")
    content = await file.read()

    # Limit to 10MB
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo muito grande (max 10MB)")

    with open(sample_path, "wb") as f:
        f.write(content)

    profile.sample_path = sample_path
    profile.voice_type = "custom"
    await db.commit()

    logger.info(f"Voice sample uploaded for profile {profile_id}: {sample_path}")
    return {"ok": True, "sample_path": sample_path}


@router.post("/profiles/{profile_id}/create-custom-voice")
async def create_custom_voice(
    profile_id: int,
    consent_file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a custom OpenAI voice from the sample + consent recording.

    Requires: 1) Voice sample already uploaded via /upload-sample
              2) Consent recording (reading the required phrase)
    """
    profile = await db.get(VoiceProfile, profile_id)
    if not profile or profile.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Perfil nao encontrado")
    if not profile.sample_path or not os.path.exists(profile.sample_path):
        raise HTTPException(status_code=400, detail="Envie primeiro uma amostra de voz")

    voice_dir = Path(settings.media_dir) / "voices" / str(user["id"]) / str(profile_id)
    voice_dir.mkdir(parents=True, exist_ok=True)
    consent_path = str(voice_dir / "consent.webm")

    content = await consent_file.read()
    with open(consent_path, "wb") as f:
        f.write(content)

    try:
        # Step 1: Upload consent recording
        import httpx
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
        }

        async with httpx.AsyncClient(timeout=60) as client:
            with open(consent_path, "rb") as cf:
                consent_resp = await client.post(
                    "https://api.openai.com/v1/audio/voice_consents",
                    headers=headers,
                    files={"recording": ("consent.webm", cf, "audio/webm")},
                    data={"name": f"criavideo_user{user['id']}_p{profile_id}", "language": "pt"},
                )

            if consent_resp.status_code not in (200, 201):
                logger.warning(f"Custom voice consent failed: {consent_resp.text}")
                raise HTTPException(status_code=400,
                    detail="Nao foi possivel criar a voz customizada. "
                           "Este recurso pode nao estar disponivel na sua conta OpenAI.")

            consent_id = consent_resp.json().get("id")

            # Step 2: Create voice from sample + consent
            with open(profile.sample_path, "rb") as sf:
                voice_resp = await client.post(
                    "https://api.openai.com/v1/audio/voices",
                    headers=headers,
                    files={"audio_sample": ("sample.webm", sf, "audio/webm")},
                    data={"name": f"criavideo_{user['id']}_{profile_id}", "consent": consent_id},
                )

            if voice_resp.status_code not in (200, 201):
                logger.warning(f"Custom voice creation failed: {voice_resp.text}")
                raise HTTPException(status_code=400,
                    detail="Falha ao criar voz. Verifique a qualidade do audio.")

            voice_id = voice_resp.json().get("voice_id") or voice_resp.json().get("id")
            profile.openai_voice_id = voice_id
            profile.voice_type = "custom"
            await db.commit()

            logger.info(f"Custom voice created: {voice_id} for profile {profile_id}")
            return {"ok": True, "voice_id": voice_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Custom voice creation error: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao criar voz: {e}")


@router.post("/profiles/{profile_id}/preview")
async def preview_voice(
    profile_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a short preview audio with the voice profile."""
    profile = await db.get(VoiceProfile, profile_id)
    if not profile or profile.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Perfil nao encontrado")

    preview_text = "Ola! Esta e uma previa de como minha voz vai soar nos seus videos. Espero que goste!"

    voice_dir = Path(settings.media_dir) / "voices" / str(user["id"]) / str(profile_id)
    voice_dir.mkdir(parents=True, exist_ok=True)
    preview_path = str(voice_dir / "preview.mp3")

    try:
        voice_param = profile.builtin_voice or "onyx"
        if profile.openai_voice_id:
            voice_param = profile.openai_voice_id

        tts_kwargs = {
            "model": "gpt-4o-mini-tts",
            "voice": voice_param,
            "input": preview_text,
            "response_format": "mp3",
        }
        if profile.tts_instructions:
            tts_kwargs["instructions"] = profile.tts_instructions

        response = await _openai.audio.speech.create(**tts_kwargs)
        response.stream_to_file(preview_path)

        return {
            "ok": True,
            "preview_url": f"/video/media/voices/{user['id']}/{profile_id}/preview.mp3",
        }
    except Exception as e:
        logger.error(f"Voice preview failed: {e}")
        raise HTTPException(status_code=500, detail=f"Erro ao gerar preview: {e}")


@router.get("/consent-phrase")
async def get_consent_phrase():
    """Return the consent phrase that must be read for custom voice creation."""
    return {"phrase": CONSENT_PHRASE_PT, "language": "pt"}
