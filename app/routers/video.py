"""
Video Router — Endpoints for creating video projects, generating scenes/renders.
"""
import json
import logging
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field
from typing import Optional
import openai
from app.auth import get_current_user
from app.database import get_db
from app.models import VideoProject, VideoScene, VideoRender, VideoStatus
from app.config import get_settings
from app.services.persona_registry import (
    build_persona_reference_montage,
    resolve_persona_reference_image,
    resolve_persona_reference_images,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/video", tags=["video"])
settings = get_settings()
_openai = openai.AsyncOpenAI(api_key=settings.openai_api_key)

# Voice demo config: name, label, demo phrase
VOICE_DEMOS = {
    "onyx":    {"name": "Lucas",   "label": "Masculina Grave",   "text": "Oi, eu sou o Lucas! Com minha voz grave e marcante, vou dar presença aos seus vídeos. Me escolha!"},
    "echo":    {"name": "Rafael",  "label": "Masculina Suave",   "text": "Olá, sou o Rafael! Minha voz suave e envolvente vai conquistar seu público. Me escolha!"},
    "ash":     {"name": "Pedro",   "label": "Masculina Natural", "text": "E aí, sou o Pedro! Com minha voz natural e autêntica, seus vídeos vão ficar incríveis. Me escolha!"},
    "nova":    {"name": "Clara",   "label": "Feminina Clara",    "text": "Oi, eu sou a Clara! Minha voz clara e vibrante vai dar vida aos seus vídeos. Me escolha!"},
    "shimmer": {"name": "Sofia",   "label": "Feminina Suave",    "text": "Olá, sou a Sofia! Com minha voz suave e delicada, vou encantar quem assistir. Me escolha!"},
    "coral":   {"name": "Beatriz", "label": "Feminina Natural",  "text": "Oi, eu sou a Beatriz! Minha voz natural e expressiva é perfeita para seus vídeos. Me escolha!"},
    "alloy":   {"name": "Alex",    "label": "Neutra",            "text": "Olá, sou Alex! Minha voz versátil se adapta a qualquer tipo de conteúdo. Me escolha!"},
    "fable":   {"name": "Mateus",  "label": "Narrativa",         "text": "Olá, sou o Mateus! Minha voz narrativa vai transformar seus vídeos em histórias inesquecíveis. Me escolha!"},
    "sage":    {"name": "Luna",    "label": "Calma e Clara",     "text": "Oi, eu sou a Luna! Com minha voz calma e clara, vou transmitir tranquilidade nos seus vídeos. Me escolha!"},
}

VOICE_DEMO_DIR = os.path.join(settings.media_dir, "voice_demos")
os.makedirs(VOICE_DEMO_DIR, exist_ok=True)

TEMP_UPLOAD_DIR = Path(settings.media_dir) / "temp_uploads"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".webm", ".mkv"}
KARAOKE_PROGRESS_TTL_MINUTES = 120
_karaoke_progress_store: dict[str, dict] = {}
_REFERENCE_IMAGE_HINT_MARKERS = (
    "reference image",
    "uploaded image",
    "user-provided image",
    "first frame",
    "imagem de referencia",
    "regra obrigatoria de imagem de referencia",
    "foto enviada",
)
_INTERACTION_PERSONAS = {"homem", "mulher", "crianca", "familia", "natureza", "desenho", "personalizado"}
_NON_VISUAL_BRIEFING_MARKERS = (
    "regra obrigatoria",
    "imagem de referencia",
    "inclua um homem em cena",
    "inclua uma mulher em cena",
    "inclua uma crianca em cena",
    "inclua uma familia",
    "inclua obrigatoriamente",
    "persona personalizada",
    "priorize natureza viva",
    "elemento visual de conexao",
)
_SCENE_RANGE_ONLY_RE = re.compile(r"^(?P<start>\d+\.\d)s\s*-\s*(?P<end>\d+\.\d)s$")
_DIALOGUE_TIMING_LINE_RE = re.compile(
    r"^(?P<start>\d+\.\d)s\s*-\s*(?P<end>\d+\.\d)s\s*\|\s*Speaker:\s*(?P<speaker>.+)$"
)


def _ensure_reference_image_instruction(prompt: str) -> str:
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        return base_prompt

    lowered = base_prompt.lower()
    if any(marker in lowered for marker in _REFERENCE_IMAGE_HINT_MARKERS):
        return base_prompt

    reference_rule = (
        "REGRA OBRIGATORIA DE IMAGEM DE REFERENCIA: use a imagem enviada como ancora visual principal. "
        "Mantenha a mesma identidade do sujeito, tracos de rosto, cabelo, paleta de cores e estilo visual geral da referencia."
    )
    return f"{base_prompt}\n\n{reference_rule}"


def _normalize_interaction_persona(value: str) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "criança": "crianca",
        "crianca": "crianca",
        "família": "familia",
        "familia": "familia",
        "personalizada": "personalizado",
        "custom": "personalizado",
    }
    normalized = mapping.get(raw, raw)
    if normalized in _INTERACTION_PERSONAS:
        return normalized
    return "natureza"


def _build_interaction_persona_instruction(interaction_persona: str) -> str:
    persona = _normalize_interaction_persona(interaction_persona)
    if persona == "homem":
        return (
            "Inclua um homem em cena interagindo com o ambiente e com a emocao do tema, "
            "mantendo coerencia narrativa e visual cinematografica."
        )
    if persona == "mulher":
        return (
            "Inclua uma mulher em cena interagindo com o ambiente e com a emocao do tema, "
            "mantendo coerencia narrativa e visual cinematografica."
        )
    if persona == "crianca":
        return (
            "Inclua uma crianca em cena interagindo com o ambiente e com a emocao do tema, "
            "com linguagem visual sensivel e respeitosa."
        )
    if persona == "familia":
        return (
            "Inclua uma familia (duas ou mais pessoas) interagindo de forma natural com o ambiente "
            "e com a emocao do tema."
        )
    if persona == "desenho":
        return (
            "Inclua obrigatoriamente um personagem em estilo desenho/animacao (cartoon, 3D, anime, etc.) "
            "interagindo com o ambiente e com a emocao do tema, com consistencia visual cinematografica."
        )
    if persona == "personalizado":
        return (
            "Inclua obrigatoriamente a persona personalizada definida pelo usuário, mantendo os traços, estilo "
            "e identidade visual descritos na referencia."
        )
    return (
        "Priorize natureza viva e inclua obrigatoriamente pelo menos um elemento visual de conexão "
        "(animal, flor, ave, borboleta ou outro ser vivo natural) em destaque e coerente com o tema."
    )


def _inject_interaction_persona_instruction(prompt: str, interaction_persona: str) -> str:
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        return base_prompt

    instruction = _build_interaction_persona_instruction(interaction_persona)
    if not instruction:
        return base_prompt

    if instruction.lower() in base_prompt.lower():
        return base_prompt

    return f"{base_prompt}\n\n{instruction}"


def _strip_non_visual_briefing_directives(briefing: str) -> str:
    raw = (briefing or "").strip()
    if not raw:
        return ""

    cleaned_lines: list[str] = []
    for line in raw.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        lowered = re.sub(r"\s+", " ", candidate.lower())
        if any(marker in lowered for marker in _NON_VISUAL_BRIEFING_MARKERS):
            continue
        cleaned_lines.append(candidate)

    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()
    if cleaned:
        return cleaned
    return "Create a realistic cinematic sequence with clear visual continuity and emotional progression."


def _build_continuous_time_ranges(total_duration: int, block_count: int) -> list[tuple[float, float]]:
    total_tenths = max(1, int(round(max(0.1, float(total_duration or 1)) * 10)))
    count = max(1, int(block_count or 1))

    ranges: list[tuple[float, float]] = []
    prev_end = 0
    for idx in range(count):
        start = prev_end
        end = (total_tenths * (idx + 1)) // count
        if idx == count - 1:
            end = total_tenths
        if end <= start:
            end = min(total_tenths, start + 1)
        ranges.append((start / 10.0, end / 10.0))
        prev_end = end

    if ranges:
        ranges[-1] = (ranges[-1][0], total_tenths / 10.0)
    return ranges


def _build_temporal_prompt_fallback(briefing: str, duration: int) -> str:
    cleaned_briefing = _strip_non_visual_briefing_directives(briefing)
    scene_count = 4 if duration <= 6 else 5 if duration <= 12 else 6
    scene_ranges = _build_continuous_time_ranges(duration, scene_count)
    dialogue_count = 1 if duration <= 6 else 2
    dialogue_ranges = _build_continuous_time_ranges(duration, dialogue_count)

    visual_seed = re.sub(r"\s+", " ", cleaned_briefing).strip()
    visual_seed = visual_seed[:240] if visual_seed else "a grounded cinematic scene"

    scene_templates = [
        "Explosive opening frame built around {seed}, with strong subject focus, cinematic depth, and dynamic environmental motion.",
        "Camera shifts aggressively to reveal character emotion, layered foreground movement, and intensified dramatic atmosphere.",
        "A tighter framing captures critical action details, reactive body language, and escalating visual tension in real time.",
        "Sustained confrontation beat with continuous motion, expressive reactions, and coherent scene geography.",
        "Final push-in emphasizes the emotional peak, preserving continuity in identity, lighting, and environment.",
        "Closing beat that keeps all elements in motion and lands the sequence with high cinematic impact.",
    ]

    lines: list[str] = []
    for idx, (start, end) in enumerate(scene_ranges):
        template = scene_templates[min(idx, len(scene_templates) - 1)]
        description = template.format(seed=visual_seed)
        lines.append(f"{start:.1f}s - {end:.1f}s")
        lines.append(description)
        if idx < len(scene_ranges) - 1:
            lines.append("")

    lines.append("")
    lines.append("Dialogue timing:")

    fallback_dialogues = [
        '"Voce mentiu para mim esse tempo todo, e agora tudo veio a tona!"',
        '"Acabou. Nao tem mais volta depois de tudo que eu descobri."',
        '"A cena explode em emocao enquanto os personagens reagem em choque."',
    ]

    for idx, (start, end) in enumerate(dialogue_ranges):
        speaker = "Strawberry woman" if idx % 2 == 0 else "Avocado man"
        speech = fallback_dialogues[min(idx, len(fallback_dialogues) - 1)]
        lines.append(f"{start:.1f}s - {end:.1f}s | Speaker: {speaker}")
        lines.append(speech)
        if idx < len(dialogue_ranges) - 1:
            lines.append("")

    return "\n".join(lines).strip()


def _is_temporal_prompt_format_valid(prompt_text: str, total_duration: int) -> bool:
    raw = (prompt_text or "").strip()
    if not raw:
        return False
    if "```" in raw:
        return False
    if raw.startswith("{") or raw.startswith("["):
        return False

    lines = [line.rstrip() for line in raw.splitlines()]
    dialogue_idx = -1
    for idx, line in enumerate(lines):
        if line.strip().lower() == "dialogue timing:":
            dialogue_idx = idx
            break

    if dialogue_idx <= 0:
        return False

    scene_lines = [line.strip() for line in lines[:dialogue_idx] if line.strip()]
    if not scene_lines:
        return False

    scene_ranges: list[tuple[float, float]] = []
    i = 0
    while i < len(scene_lines):
        range_match = _SCENE_RANGE_ONLY_RE.match(scene_lines[i])
        if not range_match:
            return False

        start = float(range_match.group("start"))
        end = float(range_match.group("end"))
        if end <= start:
            return False

        scene_ranges.append((start, end))
        i += 1

        if i >= len(scene_lines):
            return False

        has_description = False
        while i < len(scene_lines) and not _SCENE_RANGE_ONLY_RE.match(scene_lines[i]):
            if scene_lines[i].strip():
                has_description = True
            i += 1

        if not has_description:
            return False

    if abs(scene_ranges[0][0] - 0.0) > 0.11:
        return False

    for idx in range(1, len(scene_ranges)):
        if abs(scene_ranges[idx][0] - scene_ranges[idx - 1][1]) > 0.11:
            return False

    expected_end = round(float(total_duration or 1), 1)
    if abs(scene_ranges[-1][1] - expected_end) > 0.11:
        return False

    dialogue_lines = [line.strip() for line in lines[dialogue_idx + 1 :] if line.strip()]
    if not dialogue_lines:
        return False

    dialogue_blocks = 0
    j = 0
    while j < len(dialogue_lines):
        timing_match = _DIALOGUE_TIMING_LINE_RE.match(dialogue_lines[j])
        if not timing_match:
            return False

        start = float(timing_match.group("start"))
        end = float(timing_match.group("end"))
        if end <= start:
            return False

        j += 1
        if j >= len(dialogue_lines):
            return False

        speech_line = dialogue_lines[j]
        if not (speech_line.startswith('"') and speech_line.endswith('"') and len(speech_line) >= 3):
            return False

        dialogue_blocks += 1
        j += 1

    return dialogue_blocks >= 1


async def _generate_temporal_realistic_prompt(optimized_prompt: str, duration: int) -> str:
    cleaned_briefing = _strip_non_visual_briefing_directives(optimized_prompt)

    scene_count = 4 if duration <= 6 else 5 if duration <= 12 else 6 if duration <= 20 else 7
    dialogue_count = 1 if duration <= 6 else 2 if duration <= 16 else 3
    scene_ranges = _build_continuous_time_ranges(duration, scene_count)
    dialogue_ranges = _build_continuous_time_ranges(duration, dialogue_count)

    scene_ranges_text = "\n".join(f"{start:.1f}s - {end:.1f}s" for start, end in scene_ranges)
    dialogue_ranges_text = "\n".join(f"{start:.1f}s - {end:.1f}s" for start, end in dialogue_ranges)

    system_prompt = (
        "You convert realistic video briefs into strict temporal prompt blocks. "
        "Return plain text only, never markdown, never JSON. "
        "Scene descriptions must be in English. "
        "Dialogue must be in PT-BR."
    )
    user_prompt = (
        f"Total duration: {duration:.1f}s\n\n"
        "Visual briefing:\n"
        f"{cleaned_briefing}\n\n"
        "Output rules (mandatory):\n"
        "1) For each scene block, write exactly:\n"
        "   X.Xs - Y.Ys\n"
        "   <One detailed English description for this interval>\n"
        "2) Use continuous timeline from 0.0s to total duration with no gaps.\n"
        "3) After scene blocks, write exactly:\n"
        "   Dialogue timing:\n"
        "4) Then write at least one dialogue block exactly as:\n"
        "   X.Xs - Y.Ys | Speaker: Name\n"
        "   \"<spoken line in PT-BR>\"\n"
        "5) No markdown and no JSON.\n\n"
        "Use exactly these scene ranges:\n"
        f"{scene_ranges_text}\n\n"
        "Use dialogue inside these ranges:\n"
        f"{dialogue_ranges_text}"
    )

    try:
        resp = await _openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.35,
            max_tokens=1400,
        )
        candidate = (resp.choices[0].message.content or "").strip()
        if _is_temporal_prompt_format_valid(candidate, duration):
            return candidate
        logger.warning("Temporal prompt returned with invalid format; using fallback template")
    except Exception as e:
        logger.warning("Temporal prompt generation failed: %s", e)

    return _build_temporal_prompt_fallback(cleaned_briefing, duration)


def _cleanup_karaoke_progress_store() -> None:
    if not _karaoke_progress_store:
        return
    cutoff = datetime.utcnow() - timedelta(minutes=KARAOKE_PROGRESS_TTL_MINUTES)
    stale_keys = [
        op_id
        for op_id, state in _karaoke_progress_store.items()
        if datetime.fromisoformat(state.get("updated_at", "1970-01-01T00:00:00")) < cutoff
    ]
    for key in stale_keys:
        _karaoke_progress_store.pop(key, None)


def _set_karaoke_progress(
    operation_id: str,
    user_id: int,
    progress: int,
    message: str,
    *,
    status: str = "running",
    stage: str = "removing_vocals",
    error: str = "",
) -> None:
    if not operation_id:
        return
    _cleanup_karaoke_progress_store()
    now_iso = datetime.utcnow().isoformat()
    _karaoke_progress_store[operation_id] = {
        "operation_id": operation_id,
        "user_id": int(user_id),
        "status": status,
        "stage": stage,
        "progress": max(0, min(100, int(progress))),
        "message": message,
        "error": error,
        "updated_at": now_iso,
    }


@router.get("/karaoke-progress/{operation_id}")
async def get_karaoke_progress(operation_id: str, user: dict = Depends(get_current_user)):
    _cleanup_karaoke_progress_store()
    state = _karaoke_progress_store.get(operation_id)
    if not state or int(state.get("user_id", 0)) != int(user["id"]):
        return {
            "operation_id": operation_id,
            "status": "pending",
            "stage": "removing_vocals",
            "progress": 0,
            "message": "Aguardando inicio da remocao de voz...",
            "error": "",
            "updated_at": datetime.utcnow().isoformat(),
        }

    return {
        "operation_id": state.get("operation_id"),
        "status": state.get("status", "running"),
        "stage": state.get("stage", "removing_vocals"),
        "progress": state.get("progress", 0),
        "message": state.get("message", ""),
        "error": state.get("error", ""),
        "updated_at": state.get("updated_at", datetime.utcnow().isoformat()),
    }


def _temp_user_dir(user_id: int) -> Path:
    path = TEMP_UPLOAD_DIR / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_temp_file(user_id: int, upload_id: str, allowed_exts: set[str]) -> Path | None:
    # upload_id format: <uuid><ext>
    if not upload_id or "/" in upload_id or "\\" in upload_id:
        return None
    ext = Path(upload_id).suffix.lower()
    if ext not in allowed_exts:
        return None
    candidate = _temp_user_dir(user_id) / upload_id
    return candidate if candidate.exists() else None


def _build_tevoxi_auth_headers(audio_url: str) -> dict:
    url = (audio_url or "").strip()
    if "/api/create-music/audio/" not in url:
        return {}

    token = (getattr(settings, "tevoxi_api_token", "") or "").strip()
    if not token and getattr(settings, "tevoxi_jwt_secret", ""):
        try:
            import time
            from jose import jwt as jose_jwt

            payload = {
                "id": settings.tevoxi_jwt_user_id,
                "email": settings.tevoxi_jwt_email,
                "role": "admin",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            }
            token = jose_jwt.encode(payload, settings.tevoxi_jwt_secret, algorithm="HS256")
        except Exception as e:
            logger.warning(f"Failed to create Tevoxi JWT for audio download: {e}")
            token = ""

    return {"Authorization": f"Bearer {token}"} if token else {}


async def _download_external_audio_to_path(audio_url: str, output_path: Path) -> None:
    import httpx

    output_path.parent.mkdir(parents=True, exist_ok=True)
    headers = _build_tevoxi_auth_headers(audio_url)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        response = await client.get(audio_url, headers=headers or None)
        response.raise_for_status()
        content = response.content

    if not content:
        raise RuntimeError("empty_audio_response")

    with open(output_path, "wb") as f:
        f.write(content)


def _trim_audio_clip(input_path: str, output_path: str, clip_start: float, clip_duration: float) -> None:
    trim_cmd = ["ffmpeg", "-y"]
    if clip_start > 0:
        trim_cmd += ["-ss", f"{clip_start:.3f}"]
    trim_cmd += ["-i", input_path]
    if clip_duration > 0:
        trim_cmd += ["-t", f"{clip_duration:.3f}"]
    trim_cmd += ["-vn", "-c:a", "libmp3lame", "-b:a", "192k", output_path]

    result = subprocess.run(trim_cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        err_lines = [l for l in (result.stderr or "").split("\n") if l.strip()]
        err_msg = "\n".join(err_lines[-8:]) if err_lines else "ffmpeg trim failed"
        raise RuntimeError(err_msg)
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError("trim_output_missing")


@router.post("/upload-temp-image")
async def upload_temp_image(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo de imagem inválido")
    ext = Path(file.filename).suffix.lower()
    if ext not in IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="Formato de imagem não suportado")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagem excede 10MB")

    upload_id = f"{uuid.uuid4().hex}{ext}"
    target = _temp_user_dir(user["id"]) / upload_id
    with open(target, "wb") as f:
        f.write(content)
    return {"upload_id": upload_id, "size": len(content)}


@router.post("/upload-temp-audio")
async def upload_temp_audio(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo de áudio inválido")
    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIO_EXTS:
        raise HTTPException(status_code=400, detail="Formato de áudio não suportado")

    content = await file.read()
    if len(content) > 80 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Áudio excede 80MB")

    upload_id = f"{uuid.uuid4().hex}{ext}"
    target = _temp_user_dir(user["id"]) / upload_id
    with open(target, "wb") as f:
        f.write(content)
    return {"upload_id": upload_id, "size": len(content)}


@router.post("/upload-temp-video")
async def upload_temp_video(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo de vídeo inválido")
    ext = Path(file.filename).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise HTTPException(status_code=400, detail="Formato de vídeo não suportado. Use MP4, MOV, AVI ou WEBM.")

    content = await file.read()
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Vídeo excede 500MB")

    upload_id = f"{uuid.uuid4().hex}{ext}"
    target = _temp_user_dir(user["id"]) / upload_id
    with open(target, "wb") as f:
        f.write(content)
    return {"upload_id": upload_id, "size": len(content)}


@router.post("/upload-temp-chunk/start")
async def upload_temp_chunk_start(
    request: Request,
    user: dict = Depends(get_current_user),
):
    payload = await request.json()
    filename = str(payload.get("filename", "")).strip()
    kind = str(payload.get("kind", "image")).strip().lower() or "image"
    size = int(payload.get("size", 0) or 0)

    if not filename:
        raise HTTPException(status_code=400, detail="Nome de arquivo inválido")

    ext = Path(filename).suffix.lower()
    if kind == "audio":
        allowed = AUDIO_EXTS
        max_size = 80 * 1024 * 1024
    else:
        allowed = IMAGE_EXTS
        max_size = 10 * 1024 * 1024

    if ext not in allowed:
        raise HTTPException(status_code=400, detail="Formato de arquivo não suportado")
    if size <= 0 or size > max_size:
        raise HTTPException(status_code=400, detail="Tamanho de arquivo inválido")

    user_dir = _temp_user_dir(user["id"])
    session_id = uuid.uuid4().hex
    part_path = user_dir / f"{session_id}.part"
    meta_path = user_dir / f"{session_id}.json"

    with open(part_path, "wb") as f:
        f.truncate(size)

    meta = {
        "filename": filename,
        "ext": ext,
        "kind": kind,
        "size": size,
        "received": 0,
    }
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return {"session_id": session_id, "chunk_size": 512 * 1024}


@router.post("/upload-temp-chunk/{session_id}")
async def upload_temp_chunk(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    user_dir = _temp_user_dir(user["id"])
    meta_path = user_dir / f"{session_id}.json"
    part_path = user_dir / f"{session_id}.part"
    if not meta_path.exists() or not part_path.exists():
        raise HTTPException(status_code=404, detail="Sessão de upload não encontrada")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Metadados de upload inválidos")

    try:
        offset = int(request.headers.get("x-upload-offset", "0"))
    except Exception:
        raise HTTPException(status_code=400, detail="Offset inválido")

    received = int(meta.get("received", 0))
    if offset != received:
        return {"received": received, "size": int(meta.get("size", 0)), "mismatch": True}

    chunk = await file.read()
    if not chunk:
        raise HTTPException(status_code=400, detail="Chunk vazio")

    size = int(meta.get("size", 0))
    new_received = received + len(chunk)
    if new_received > size:
        raise HTTPException(status_code=400, detail="Chunk excede tamanho total")

    with open(part_path, "r+b") as f:
        f.seek(offset)
        f.write(chunk)

    meta["received"] = new_received
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    return {"received": new_received, "size": size, "done": new_received >= size}


@router.post("/upload-temp-chunk/{session_id}/finish")
async def upload_temp_chunk_finish(
    session_id: str,
    user: dict = Depends(get_current_user),
):
    user_dir = _temp_user_dir(user["id"])
    meta_path = user_dir / f"{session_id}.json"
    part_path = user_dir / f"{session_id}.part"
    if not meta_path.exists() or not part_path.exists():
        raise HTTPException(status_code=404, detail="Sessão de upload não encontrada")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Metadados de upload inválidos")

    size = int(meta.get("size", 0))
    received = int(meta.get("received", 0))
    if received < size:
        raise HTTPException(status_code=400, detail="Upload incompleto")

    ext = str(meta.get("ext", "")).lower()
    upload_id = f"{uuid.uuid4().hex}{ext}"
    target = user_dir / upload_id
    os.replace(part_path, target)
    try:
        meta_path.unlink(missing_ok=True)
    except Exception:
        pass

    return {"upload_id": upload_id, "size": size, "kind": meta.get("kind", "image")}


@router.get("/voice-demo/{voice_id}")
async def get_voice_demo(voice_id: str):
    """Return a cached TTS demo for the given voice. Generates on first request."""
    if voice_id not in VOICE_DEMOS:
        raise HTTPException(404, "Voice not found")

    cache_path = os.path.join(VOICE_DEMO_DIR, f"{voice_id}.mp3")
    if not os.path.exists(cache_path):
        demo = VOICE_DEMOS[voice_id]
        resp = await _openai.audio.speech.create(
            model="tts-1",
            voice=voice_id,
            input=demo["text"],
            response_format="mp3",
        )
        Path(cache_path).write_bytes(resp.content)

    return FileResponse(cache_path, media_type="audio/mpeg")


def _to_media_url(path: str | None) -> str | None:
    """Convert absolute file path to web-accessible URL."""
    if not path:
        return None
    media_prefix = settings.media_dir.rstrip("/")
    if path.startswith(media_prefix):
        return "/video/media" + path[len(media_prefix):]
    return None


class CreateProjectRequest(BaseModel):
    track_id: int = 0
    title: str = ""
    description: str = ""
    tags: list[str] = []
    style_prompt: str = ""
    aspect_ratio: str = "16:9"
    # Track data from Levita
    track_title: str = ""
    track_artist: str = ""
    track_duration: float = 0
    lyrics_text: str = ""
    lyrics_words: list[dict] = []
    audio_path: str = ""


class QuickCreateRequest(BaseModel):
    """Request from Levita's "Criar Vídeo" button — minimal data, AI fills the rest."""
    song_title: str = ""
    song_artist: str = ""
    audio_url: str
    lyrics: str = ""
    duration: float = 0
    aspect_ratio: str = "16:9"
    style_prompt: str = ""     # optional: user-chosen style override
    description: str = ""      # optional: user-provided description/topic


class CopyFormatRequest(BaseModel):
    aspect_ratio: str = "9:16"


class RenameProjectRequest(BaseModel):
    title: str


class ProjectResponse(BaseModel):
    id: int
    status: str
    progress: int
    title: str
    track_title: str | None = None
    track_artist: str | None = None
    aspect_ratio: str
    error_message: str | None = None
    created_at: str


@router.post("/projects", response_model=dict)
async def create_project(
    req: CreateProjectRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new video project from a Levita track."""
    project = VideoProject(
        user_id=user["id"],
        track_id=req.track_id,
        title=req.title or req.track_title or "Untitled Video",
        description=req.description,
        tags=req.tags,
        style_prompt=req.style_prompt,
        aspect_ratio=req.aspect_ratio,
        track_title=req.track_title,
        track_artist=req.track_artist,
        track_duration=req.track_duration,
        lyrics_text=req.lyrics_text,
        lyrics_words=req.lyrics_words,
        audio_path=req.audio_path,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return {"id": project.id, "status": project.status.value}


@router.get("/projects")
async def list_projects(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all video projects for the current user."""
    result = await db.execute(
        select(VideoProject)
        .options(selectinload(VideoProject.renders))
        .where(VideoProject.user_id == user["id"])
        .order_by(VideoProject.created_at.desc())
    )
    projects = result.scalars().all()

    def _ordered_renders(renders: list[VideoRender]) -> list[VideoRender]:
        return sorted(
            renders or [],
            key=lambda r: (r.created_at or datetime.min, r.id or 0),
            reverse=True,
        )

    payload = []
    for p in projects:
        ordered = _ordered_renders(list(p.renders or []))
        latest_any = ordered[0] if ordered else None
        latest_active = next((r for r in ordered if r.file_path), None)
        display_render = latest_active or latest_any

        payload.append(
            {
                "id": p.id,
                "title": p.title,
                "track_title": p.track_title,
                "track_artist": p.track_artist,
                "status": p.status.value,
                "progress": p.progress,
                "aspect_ratio": p.aspect_ratio,
                "error_message": p.error_message,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "render_created_at": display_render.created_at.isoformat() if display_render and display_render.created_at else None,
                "video_expired": bool(ordered) and latest_active is None,
                "lyrics_text": p.lyrics_text or "",
                "style_prompt": p.style_prompt or "",
                "thumbnail_url": _to_media_url(display_render.thumbnail_path) if display_render else None,
            }
        )

    return payload


@router.get("/projects/{project_id}")
async def get_project(
    project_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get project details with scenes and renders."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    result_scenes = await db.execute(
        select(VideoScene).where(VideoScene.project_id == project_id).order_by(VideoScene.scene_index)
    )
    scenes = result_scenes.scalars().all()

    result_renders = await db.execute(
        select(VideoRender)
        .where(VideoRender.project_id == project_id)
        .order_by(VideoRender.created_at.desc(), VideoRender.id.desc())
    )
    renders = result_renders.scalars().all()

    return {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "tags": project.tags,
        "status": project.status.value,
        "progress": project.progress,
        "aspect_ratio": project.aspect_ratio,
        "track_title": project.track_title,
        "track_artist": project.track_artist,
        "track_duration": project.track_duration,
        "error_message": project.error_message,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "scenes": [
            {
                "id": s.id,
                "scene_index": s.scene_index,
                "scene_type": s.scene_type,
                "prompt": s.prompt,
                "image_path": s.image_path,
                "start_time": s.start_time,
                "end_time": s.end_time,
                "lyrics_segment": s.lyrics_segment,
            }
            for s in scenes
        ],
        "renders": [
            {
                "id": r.id,
                "format": r.format,
                "file_path": r.file_path,
                "file_size": r.file_size,
                "thumbnail_path": r.thumbnail_path,
                "duration": r.duration,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "video_url": _to_media_url(r.file_path),
                "thumbnail_url": _to_media_url(r.thumbnail_path),
            }
            for r in renders
        ],
    }


@router.patch("/projects/{project_id}/title")
async def rename_project(
    project_id: int,
    req: RenameProjectRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Rename a video project title."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    new_title = (req.title or "").strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Título não pode ficar vazio")
    if len(new_title) > 500:
        raise HTTPException(status_code=400, detail="Título muito longo (máximo 500 caracteres)")

    project.title = new_title
    await db.commit()
    await db.refresh(project)
    return {"id": project.id, "title": project.title}


@router.post("/projects/{project_id}/thumbnail")
async def update_project_thumbnail(
    project_id: int,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload/replace thumbnail for a completed project."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    # Validate image type
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Formato inválido. Envie JPG, PNG ou WebP.")
    if file.size and file.size > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagem muito grande (máximo 10MB)")

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}.get(file.content_type, ".jpg")
    thumb_dir = Path("thumbnails") / str(project_id)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = thumb_dir / f"thumbnail{ext}"

    # Remove old thumbnails with different extensions
    for old in thumb_dir.glob("thumbnail.*"):
        old.unlink(missing_ok=True)

    data = await file.read()
    thumb_path.write_bytes(data)

    # Update render record
    result = await db.execute(
        select(VideoRender).where(VideoRender.project_id == project_id)
    )
    render = result.scalars().first()
    if render:
        render.thumbnail_path = str(thumb_path)
        await db.commit()

    return {"thumbnail_path": str(thumb_path)}


@router.post("/projects/{project_id}/images")
async def upload_project_images(
    project_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload one or more custom images to an existing project."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    form = await request.form()
    try:
        raw_images = form.getlist("images")
    except Exception:
        raw_images = []

    uploads = [item for item in raw_images if getattr(item, "filename", "")]
    if not uploads:
        raise HTTPException(status_code=400, detail="Nenhuma imagem enviada")

    img_dir = Path(settings.media_dir) / "images" / str(project.id)
    img_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(img_dir.glob("user_*.*"))
    next_idx = len(existing)
    max_total = 20
    remaining_slots = max_total - next_idx
    if remaining_slots <= 0:
        raise HTTPException(status_code=400, detail="Limite de 20 imagens por projeto atingido")

    saved_files: list[str] = []
    for image in uploads[:remaining_slots]:
        filename = str(getattr(image, "filename", "") or "").strip()
        if not filename:
            continue

        ext = Path(filename).suffix.lower()
        if ext not in IMAGE_EXTS:
            raise HTTPException(status_code=400, detail=f"Formato não suportado para {filename}. Use JPG, PNG ou WebP.")

        content = await image.read()
        if not content:
            continue
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"Imagem {filename} excede 10MB")

        target_name = f"user_{next_idx:03d}{ext}"
        target_path = img_dir / target_name
        with open(target_path, "wb") as f:
            f.write(content)

        saved_files.append(target_name)
        next_idx += 1

    if not saved_files:
        raise HTTPException(status_code=400, detail="Nenhuma imagem válida enviada")

    if not bool(getattr(project, "use_custom_video", False)):
        project.use_custom_images = True
    await db.commit()

    return {
        "project_id": project.id,
        "saved_count": len(saved_files),
        "images": saved_files,
        "total_images": next_idx,
    }


@router.post("/projects/{project_id}/generate")
async def generate_video(
    project_id: int,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start the full video generation pipeline (async background task)."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.status not in (VideoStatus.PENDING, VideoStatus.FAILED):
        raise HTTPException(status_code=400, detail=f"Project is already {project.status.value}")

    # If audio is missing but we have the script, regenerate TTS
    if (not project.audio_path or not os.path.exists(project.audio_path)) and project.lyrics_text:
        from app.services.script_audio import generate_tts_audio
        try:
            voice = "onyx"
            tts_instructions = ""
            voice_type = "builtin"

            # Check if user has a default voice profile
            from app.models import VoiceProfile
            from sqlalchemy import select
            result = await db.execute(
                select(VoiceProfile).where(
                    VoiceProfile.user_id == user["id"],
                    VoiceProfile.is_default == True
                )
            )
            default_profile = result.scalar_one_or_none()
            if default_profile:
                profile_voice_type = str(default_profile.voice_type or "builtin").strip().lower()
                if profile_voice_type == "elevenlabs" and default_profile.openai_voice_id:
                    voice = default_profile.openai_voice_id
                    voice_type = "elevenlabs"
                elif profile_voice_type == "custom" and default_profile.openai_voice_id:
                    voice = default_profile.openai_voice_id
                    voice_type = "custom"
                elif default_profile.builtin_voice:
                    voice = default_profile.builtin_voice
                    voice_type = "builtin"
                tts_instructions = default_profile.tts_instructions or ""

            audio_path = await generate_tts_audio(
                text=project.lyrics_text,
                voice=voice,
                project_id=project.id,
                tts_instructions=tts_instructions,
                voice_type=voice_type,
            )
            project.audio_path = audio_path
            word_count = len(project.lyrics_text.split())
            project.track_duration = round(word_count / 2.5)
        except Exception as e:
            project.status = VideoStatus.FAILED
            project.error_message = f"Erro ao gerar áudio: {e}"
            await db.commit()
            raise HTTPException(status_code=500, detail=f"Erro ao gerar áudio: {e}")

    project.status = VideoStatus.GENERATING_SCENES
    project.progress = 0
    project.error_message = None
    await db.commit()

    from app.tasks.video_tasks import run_video_pipeline
    background_tasks.add_task(run_video_pipeline, project_id)

    return {"status": "started", "project_id": project_id}


@router.post("/projects/{project_id}/copy-format")
async def copy_project_with_format(
    project_id: int,
    req: CopyFormatRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create an exact copy of a completed project and re-render in another aspect ratio."""
    if req.aspect_ratio not in {"16:9", "9:16", "1:1"}:
        raise HTTPException(status_code=400, detail="Formato inválido. Use 16:9, 9:16 ou 1:1")

    source = await db.get(VideoProject, project_id)
    if not source or source.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")
    if source.status != VideoStatus.COMPLETED:
        raise HTTPException(status_code=400, detail="Somente projetos concluidos podem ser copiados")

    source_render_res = await db.execute(
        select(VideoRender)
        .where(VideoRender.project_id == source.id)
        .order_by(VideoRender.created_at.desc())
    )
    source_render = source_render_res.scalars().first()
    if not source_render or not source_render.file_path:
        raise HTTPException(status_code=400, detail="Projeto origem sem vídeo renderizado")
    if not os.path.exists(source_render.file_path):
        raise HTTPException(status_code=400, detail="Arquivo do vídeo origem não foi encontrado")

    title = (source.title or source.track_title or "Video").strip()
    new_title = f"{title} [{req.aspect_ratio}]"

    project = VideoProject(
        user_id=source.user_id,
        track_id=source.track_id,
        title=new_title,
        description=source.description or "",
        tags=source.tags or [],
        style_prompt=source.style_prompt or "",
        aspect_ratio=req.aspect_ratio,
        track_title=source.track_title or "",
        track_artist=source.track_artist or "",
        track_duration=source.track_duration or 0,
        lyrics_text=source.lyrics_text or "",
        lyrics_words=source.lyrics_words or [],
        audio_path=source.audio_path or "",
        use_custom_images=bool(getattr(source, "use_custom_images", False)),
        enable_subtitles=bool(getattr(source, "enable_subtitles", True)),
        zoom_images=bool(getattr(source, "zoom_images", True)),
        image_display_seconds=float(getattr(source, "image_display_seconds", 0) or 0),
        status=VideoStatus.RENDERING,
        progress=10,
        error_message=None,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    from app.tasks.video_tasks import run_video_format_copy_pipeline
    background_tasks.add_task(run_video_format_copy_pipeline, project.id, source_render.file_path)

    return {
        "id": project.id,
        "status": "started",
        "source_project_id": source.id,
        "aspect_ratio": project.aspect_ratio,
    }


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a video project and all associated files."""
    project = await db.get(VideoProject, project_id)
    if not project or project.user_id != user["id"]:
        raise HTTPException(status_code=404, detail="Project not found")

    # Clean up files
    import shutil
    from pathlib import Path
    for dir_name in ["images", "clips", "renders", "subtitles"]:
        dir_path = Path(settings.media_dir) / dir_name / str(project_id)
        if dir_path.exists():
            shutil.rmtree(dir_path, ignore_errors=True)

    await db.delete(project)
    await db.commit()
    return {"deleted": True}


@router.post("/quick-create")
async def quick_create(
    req: QuickCreateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """One-click video creation: AI generates title/description/style, creates project, starts pipeline."""
    # Detect gospel/worship genre from lyrics and title
    _text_lower = f"{req.song_title or ''} {req.song_artist or ''} {(req.lyrics or '')[:500]}".lower()
    _is_gospel = any(w in _text_lower for w in [
        "gospel", "worship", "louvor", "adoração", "adoracao", "deus", "senhor",
        "jesus", "cristo", "espírito", "espirito", "santo", "glória", "gloria",
        "redenção", "redencao", "fé", "oração", "oracao", "salvação", "salvacao",
        "graça", "graca", "igreja", "aleluia", "hallelujah", "amém", "amen",
    ])

    _gospel_style_instruction = """
IMPORTANT: This is a GOSPEL/WORSHIP song. The style_prompt MUST reflect spiritual, uplifting imagery:
- Use nature landscapes: mountains, valleys, rivers, sunrise, sunset, golden light, green pastures, calm waters, starry sky, fields of wheat, olive trees, gentle rain, waterfalls, meadows, oceans
- Use warm, golden, celestial lighting — NOT dark, horror, or scary imagery
- Do NOT mention birds, doves, or animals in the style_prompt — focus on landscapes and light
- NEVER use dark/horror/scary/gothic themes for gospel music""" if _is_gospel else ""

    # Ask AI to generate creative metadata from song info
    ai_prompt = f"""Você é um produtor criativo de vídeos musicais.
Com base nos dados desta música, gere metadados criativos para um videoclipe.

Título da música: {req.song_title or 'Desconhecido'}
Artista: {req.song_artist or 'Desconhecido'}
Duração: {req.duration:.0f} segundos
Trecho da letra:
{(req.lyrics or 'Sem letra disponível')[:800]}
{_gospel_style_instruction}

Responda SOMENTE um JSON com:
- "title": título curto e criativo para o projeto de vídeo (máx 60 chars, em português)
- "description": descrição envolvente para redes sociais (máx 200 chars, em português)
- "style_prompt": prompt em INGLÊS descrevendo o estilo visual ideal (cores, cenário, mood, iluminação — máx 120 chars)
- "tags": lista de 3-5 tags relevantes em português

JSON apenas, sem markdown."""

    title = req.song_title or "Meu Vídeo"
    description = req.description or ""
    style_prompt = req.style_prompt or "cinematic, vibrant colors, dynamic lighting"
    tags = []

    # If user provided style_prompt, skip AI generation for style
    if req.style_prompt:
        # Still generate title/description/tags via AI if description not provided
        if not req.description:
            try:
                resp = await _openai.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": ai_prompt}],
                    temperature=0.8,
                    max_tokens=300,
                )
                data = json.loads(resp.choices[0].message.content)
                title = data.get("title", title)
                description = data.get("description", description)
                tags = data.get("tags", tags)
            except Exception as e:
                logger.warning("AI metadata generation failed, using defaults: %s", e)
    else:
        try:
            resp = await _openai.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": ai_prompt}],
                temperature=0.8,
                max_tokens=300,
            )
            data = json.loads(resp.choices[0].message.content)
            title = data.get("title", title)
            description = data.get("description", description)
            style_prompt = data.get("style_prompt", style_prompt)
            tags = data.get("tags", tags)
        except Exception as e:
            logger.warning("AI metadata generation failed, using defaults: %s", e)

    # ── Credit check: deduct based on song duration ──
    # Skip for Levita users (credits handled by Levita backend)
    if user.get("source") != "levita":
        from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
        import math
        est_minutes = max(1, math.ceil((req.duration or 60) / 60))
        credits_needed = est_minutes * CREDITS_PER_MINUTE
        await deduct_credits(db, user["id"], credits_needed)

    project = VideoProject(
        user_id=user["id"],
        track_id=0,
        title=title,
        description=description,
        tags=tags,
        style_prompt=style_prompt,
        aspect_ratio=req.aspect_ratio,
        track_title=req.song_title or "",
        track_artist=req.song_artist or "",
        track_duration=req.duration,
        lyrics_text=req.lyrics or "",
        lyrics_words=[],
        audio_path=req.audio_url,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # Auto-start generation
    project.status = VideoStatus.GENERATING_SCENES
    project.progress = 0
    await db.commit()

    from app.tasks.video_tasks import run_video_pipeline
    background_tasks.add_task(run_video_pipeline, project.id)

    return {
        "id": project.id,
        "title": title,
        "description": description,
        "style_prompt": style_prompt,
        "tags": tags,
        "status": "generating_scenes",
    }


# ── Script & Audio Generation ──────────────────────────────────


class FixTextRequest(BaseModel):
    text: str


class GenerateScriptRequest(BaseModel):
    topic: str
    tone: str = "informativo"
    duration_seconds: int = 60
    custom_image_ids: list[str] = Field(default_factory=list)


class GenerateTTSRequest(BaseModel):
    script: str
    voice: str = ""
    voice_profile_id: int = 0
    voice_type: str = ""  # "suno" for Suno AI narration, empty for auto-detect
    title: str = ""
    aspect_ratio: str = "16:9"
    style_prompt: str = ""
    pause_level: str = "normal"
    tone: str = "informativo"
    enable_subtitles: bool = True
    zoom_images: bool = True
    image_display_seconds: float = 0
    no_background_music: bool = False
    use_custom_audio: bool = False
    audio_is_music: bool = False
    remove_vocals: bool = False
    subtitle_position_y: int = 80
    enable_audio_spectrum: bool = False
    use_tevoxi_audio: bool = False
    tevoxi_audio_url: str = ""
    tevoxi_lyrics: str = ""
    tevoxi_clip_start: float = 0
    tevoxi_clip_duration: float = 0


class TranscribeTevoxiClipRequest(BaseModel):
    audio_url: str
    clip_start: float = 0
    clip_duration: float = 10
    lyrics_hint: str = ""


@router.post("/transcribe-tevoxi-clip")
async def transcribe_tevoxi_clip_endpoint(
    req: TranscribeTevoxiClipRequest,
    user: dict = Depends(get_current_user),
):
    audio_url = (req.audio_url or "").strip()
    if not audio_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="URL de áudio inválida.")

    clip_start = max(0.0, float(req.clip_start or 0))
    clip_duration = max(0.0, float(req.clip_duration or 0))
    if clip_duration <= 0:
        raise HTTPException(status_code=400, detail="Selecione um trecho com duração maior que zero.")

    clip_duration = min(45.0, clip_duration)
    transcribe_dir = Path(settings.media_dir) / "temp_transcribe" / str(user["id"])
    transcribe_dir.mkdir(parents=True, exist_ok=True)
    transcribe_id = uuid.uuid4().hex
    source_path = transcribe_dir / f"{transcribe_id}_source.mp3"
    clip_path = transcribe_dir / f"{transcribe_id}_clip.mp3"

    try:
        await _download_external_audio_to_path(audio_url, source_path)
        _trim_audio_clip(str(source_path), str(clip_path), clip_start, clip_duration)

        from app.services.transcriber import transcribe_audio
        import asyncio

        lyrics_hint = (req.lyrics_hint or "").strip()
        if len(lyrics_hint) > 5000:
            lyrics_hint = lyrics_hint[:5000]

        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: transcribe_audio(str(clip_path), prompt=lyrics_hint),
        )
        text = ""
        words = []
        if isinstance(result, dict):
            text = str(result.get("text", "") or "").strip()
            words = result.get("words", []) if isinstance(result.get("words", []), list) else []

        return {
            "text": text,
            "duration": clip_duration,
            "words_count": len(words),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Tevoxi clip transcription failed: {e}")
        raise HTTPException(status_code=502, detail="Não foi possível transcrever o trecho agora.")
    finally:
        for path in (clip_path, source_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass


@router.post("/fix-text")
async def fix_text_endpoint(
    req: FixTextRequest,
    user: dict = Depends(get_current_user),
):
    """Fix spelling, grammar and punctuation errors in user text using GPT."""
    import openai
    from app.config import get_settings
    settings = get_settings()
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": (
                "Você é um corretor ortográfico e gramatical de português brasileiro. "
                "Corrija APENAS erros de ortografia, acentuação, pontuação e gramática no texto. "
                "NÃO altere o significado, o estilo, o tom ou a estrutura do texto. "
                "NÃO remova nem adicione frases. NÃO reescreva o texto. "
                "Mantenha exatamente as reticencias (...), quebras de linha e formatacao original. "
                "Retorne SOMENTE o texto corrigido, sem explicacoes."
            )},
            {"role": "user", "content": req.text},
        ],
        temperature=0.1,
        max_tokens=min(len(req.text) * 2, 16000),
    )
    corrected = resp.choices[0].message.content.strip()

    # Count approximate changes
    original_words = req.text.split()
    corrected_words = corrected.split()
    changes = sum(1 for a, b in zip(original_words, corrected_words) if a != b)
    changes += abs(len(original_words) - len(corrected_words))

    return {"text": corrected, "changes": changes}


@router.post("/generate-script")
async def generate_script_endpoint(
    req: GenerateScriptRequest,
    user: dict = Depends(get_current_user),
):
    """Generate a video narration script using AI."""
    from app.services.script_audio import generate_script

    image_paths: list[str] = []
    for upload_id in (req.custom_image_ids or [])[:8]:
        resolved = _resolve_temp_file(user["id"], str(upload_id).strip(), IMAGE_EXTS)
        if resolved:
            image_paths.append(str(resolved))

    result = await generate_script(
        topic=req.topic,
        tone=req.tone,
        duration_seconds=req.duration_seconds,
        image_paths=image_paths,
    )

    if image_paths:
        result["image_context_used"] = True
        result["image_count_used"] = len(image_paths)

    return result


@router.post("/generate-audio")
async def generate_audio_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate TTS audio from script, create project, and start video pipeline."""
    from app.services.script_audio import generate_tts_audio
    from app.models import VoiceProfile

    # Accept both JSON and multipart/form-data (with optional background_music upload)
    content_type = request.headers.get("content-type", "")
    bgm_upload: UploadFile | None = None
    custom_audio_upload: UploadFile | None = None
    custom_image_uploads: list[UploadFile] = []
    custom_image_ids: list[str] = []
    background_music_id: str = ""
    custom_audio_id: str = ""
    custom_video_id: str = ""
    custom_thumbnail_id: str = ""
    karaoke_operation_id: str = ""
    if "multipart/form-data" in content_type:
        form = await request.form()
        enable_sub_raw = str(form.get("enable_subtitles", "true")).lower()
        zoom_raw = str(form.get("zoom_images", "true")).lower()
        image_seconds_raw = form.get("image_display_seconds", 0)
        no_bgm_raw = str(form.get("no_background_music", "false")).lower()
        use_custom_audio_raw = str(form.get("use_custom_audio", "false")).lower()
        audio_is_music_raw = str(form.get("audio_is_music", "false")).lower()
        remove_vocals_raw = str(form.get("remove_vocals", "false")).lower()
        subtitle_position_raw = form.get("subtitle_position_y", 80)
        enable_audio_spectrum_raw = str(form.get("enable_audio_spectrum", "false")).lower()
        use_tevoxi_audio_raw = str(form.get("use_tevoxi_audio", "false")).lower()
        tevoxi_audio_url_raw = str(form.get("tevoxi_audio_url", "")).strip()
        tevoxi_lyrics_raw = str(form.get("tevoxi_lyrics", ""))
        tevoxi_clip_start_raw = form.get("tevoxi_clip_start", 0)
        tevoxi_clip_duration_raw = form.get("tevoxi_clip_duration", 0)
        req = GenerateTTSRequest(
            script=str(form.get("script", "")),
            voice=str(form.get("voice", "")),
            voice_profile_id=int(form.get("voice_profile_id", 0) or 0),
            voice_type=str(form.get("voice_type", "")),
            title=str(form.get("title", "")),
            aspect_ratio=str(form.get("aspect_ratio", "16:9")),
            style_prompt=str(form.get("style_prompt", "")),
            pause_level=str(form.get("pause_level", "normal")),
            tone=str(form.get("tone", "informativo")),
            enable_subtitles=enable_sub_raw not in ("false", "0", "no"),
            zoom_images=zoom_raw not in ("false", "0", "no"),
            image_display_seconds=float(image_seconds_raw or 0),
            no_background_music=no_bgm_raw in ("true", "1", "yes"),
            use_custom_audio=use_custom_audio_raw in ("true", "1", "yes"),
            audio_is_music=audio_is_music_raw in ("true", "1", "yes"),
            remove_vocals=remove_vocals_raw in ("true", "1", "yes"),
            subtitle_position_y=int(subtitle_position_raw or 80),
            enable_audio_spectrum=enable_audio_spectrum_raw in ("true", "1", "yes"),
            use_tevoxi_audio=use_tevoxi_audio_raw in ("true", "1", "yes"),
            tevoxi_audio_url=tevoxi_audio_url_raw,
            tevoxi_lyrics=tevoxi_lyrics_raw,
            tevoxi_clip_start=float(tevoxi_clip_start_raw or 0),
            tevoxi_clip_duration=float(tevoxi_clip_duration_raw or 0),
        )
        raw_upload = form.get("background_music")
        if isinstance(raw_upload, UploadFile) and raw_upload.filename:
            bgm_upload = raw_upload
        elif getattr(raw_upload, "filename", ""):
            bgm_upload = raw_upload

        raw_main_audio = form.get("custom_audio")
        if isinstance(raw_main_audio, UploadFile) and raw_main_audio.filename:
            custom_audio_upload = raw_main_audio
        elif getattr(raw_main_audio, "filename", ""):
            custom_audio_upload = raw_main_audio

        # Collect custom image uploads (multiple files under "custom_images")
        try:
            uploaded_images = form.getlist("custom_images")
        except Exception:
            uploaded_images = []
        for value in uploaded_images:
            if isinstance(value, UploadFile) and value.filename:
                custom_image_uploads.append(value)
            elif getattr(value, "filename", ""):
                custom_image_uploads.append(value)
        try:
            custom_image_ids = [str(v).strip() for v in form.getlist("custom_image_ids") if str(v).strip()]
        except Exception:
            custom_image_ids = []
        background_music_id = str(form.get("background_music_id", "")).strip()
        custom_audio_id = str(form.get("custom_audio_id", "")).strip()
        custom_video_id = str(form.get("custom_video_id", "")).strip()
        custom_thumbnail_id = str(form.get("custom_thumbnail_id", "")).strip()
        karaoke_operation_id = str(form.get("karaoke_operation_id", "")).strip()
    else:
        payload = await request.json()
        karaoke_operation_id = str(payload.get("karaoke_operation_id", "")).strip()
        req = GenerateTTSRequest(**payload)

    if karaoke_operation_id:
        _set_karaoke_progress(
            karaoke_operation_id,
            user["id"],
            5,
            "Preparando remocao de voz no Levita...",
            status="running",
            stage="removing_vocals",
        )

    script_text = (req.script or "").strip()
    has_uploaded_custom_audio = bool(custom_audio_id) or bool(custom_audio_upload and custom_audio_upload.filename)
    use_tevoxi_audio = bool(req.use_tevoxi_audio and (req.tevoxi_audio_url or "").strip())

    if req.use_tevoxi_audio and not use_tevoxi_audio:
        raise HTTPException(status_code=400, detail="Modo Tevoxi ativo, mas URL do áudio não foi enviada.")

    if req.use_custom_audio and use_tevoxi_audio:
        raise HTTPException(status_code=400, detail="Escolha apenas uma fonte de áudio principal (Tevoxi ou áudio enviado).")

    if req.use_custom_audio and not has_uploaded_custom_audio:
        raise HTTPException(status_code=400, detail="Usar meu áudio está ativo, mas nenhum arquivo foi enviado.")

    if req.use_custom_audio and req.audio_is_music:
        req.remove_vocals = True
        req.enable_subtitles = True

    if use_tevoxi_audio:
        req.no_background_music = True
        req.enable_subtitles = True

    if not script_text and not custom_image_uploads and not custom_image_ids and not has_uploaded_custom_audio and not use_tevoxi_audio:
        raise HTTPException(status_code=400, detail="Sem narração, envie fotos ou áudio para criar um vídeo personalizado.")

    # ── Credit check: estimate duration → deduct credits ──
    from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
    import math
    if has_uploaded_custom_audio and custom_audio_id:
        from app.services.video_composer import _get_duration as get_audio_duration

        src_audio = _resolve_temp_file(user["id"], custom_audio_id, AUDIO_EXTS)
        audio_seconds = get_audio_duration(str(src_audio)) if src_audio else 0
        est_minutes = max(1, math.ceil(audio_seconds / 60)) if audio_seconds > 0 else 1
    elif use_tevoxi_audio:
        clip_duration = max(0.0, float(req.tevoxi_clip_duration or 0))
        audio_seconds = clip_duration if clip_duration > 0 else 60.0
        est_minutes = max(1, math.ceil(audio_seconds / 60))
    elif script_text:
        word_count = len(script_text.split())
        est_minutes = max(1, math.ceil(word_count / 150))  # ~150 words/min narration
    else:
        est_minutes = 1  # photo-only / audio-only fallback: minimum 1 min
    credits_needed = est_minutes * CREDITS_PER_MINUTE
    await deduct_credits(db, user["id"], credits_needed)

    # Resolve voice from profile or direct parameter
    voice = req.voice or "onyx"
    tts_instructions = ""
    voice_type = req.voice_type or "builtin"
    is_suno_narration = voice_type == "suno" or (req.voice or "").startswith("suno_narrator_")
    if is_suno_narration:
        voice_type = "suno"
        voice = req.voice  # e.g. "suno_narrator_male_deep"
    elif req.voice_profile_id:
        profile = await db.get(VoiceProfile, req.voice_profile_id)
        if profile and profile.user_id == user["id"]:
            profile_voice_type = str(profile.voice_type or "builtin").strip().lower()
            if profile_voice_type == "elevenlabs" and profile.openai_voice_id:
                voice = profile.openai_voice_id
                voice_type = "elevenlabs"
            elif profile_voice_type == "custom" and profile.openai_voice_id:
                voice = profile.openai_voice_id
                voice_type = "custom"
            elif profile.builtin_voice:
                voice = profile.builtin_voice
                voice_type = "builtin"
            tts_instructions = profile.tts_instructions or ""
    elif not req.voice:
        # Try user's default voice profile
        from sqlalchemy import select
        result = await db.execute(
            select(VoiceProfile).where(
                VoiceProfile.user_id == user["id"],
                VoiceProfile.is_default == True
            )
        )
        default_profile = result.scalar_one_or_none()
        if default_profile:
            profile_voice_type = str(default_profile.voice_type or "builtin").strip().lower()
            if profile_voice_type == "elevenlabs" and default_profile.openai_voice_id:
                voice = default_profile.openai_voice_id
                voice_type = "elevenlabs"
            elif profile_voice_type == "custom" and default_profile.openai_voice_id:
                voice = default_profile.openai_voice_id
                voice_type = "custom"
            elif default_profile.builtin_voice:
                voice = default_profile.builtin_voice
                voice_type = "builtin"
            tts_instructions = default_profile.tts_instructions or ""

    # Create project first to get an ID for the audio path
    has_custom_images = len(custom_image_uploads) > 0 or len(custom_image_ids) > 0
    has_custom_audio = req.use_custom_audio and has_uploaded_custom_audio
    has_custom_video = bool(custom_video_id)
    has_tevoxi_audio = use_tevoxi_audio
    image_display_seconds = req.image_display_seconds if req.image_display_seconds and req.image_display_seconds > 0 else 0
    project = VideoProject(
        user_id=user["id"],
        track_id=0,
        title=req.title or "Vídeo com IA",
        description="",
        tags=[],
        style_prompt=req.style_prompt or "cinematic, vibrant colors, dynamic lighting",
        aspect_ratio=req.aspect_ratio,
        track_title=req.title or ("Vídeo enviado" if has_custom_video else "Áudio Tevoxi" if has_tevoxi_audio else "Áudio enviado" if has_custom_audio else "Narração IA"),
        track_artist="Tevoxi" if has_tevoxi_audio else ("Usuário" if (has_custom_audio or has_custom_video) else "CriaVideo AI"),
        track_duration=0,
        lyrics_text=req.script,
        lyrics_words=[],
        audio_path="",
        use_custom_images=has_custom_images and not has_custom_video,
        use_custom_video=has_custom_video,
        enable_subtitles=req.enable_subtitles,
        zoom_images=req.zoom_images,
        image_display_seconds=image_display_seconds,
        no_background_music=(req.no_background_music or has_custom_audio or has_custom_video or has_tevoxi_audio),
        is_karaoke=(req.use_custom_audio and req.audio_is_music and req.remove_vocals),
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # Save custom images uploaded by user (max 20, max 10MB each)
    if custom_image_uploads or custom_image_ids:
        img_dir = Path(settings.media_dir) / "images" / str(project.id)
        img_dir.mkdir(parents=True, exist_ok=True)
        idx = 0

        for upload_id in custom_image_ids[:20]:
            try:
                src = _resolve_temp_file(user["id"], upload_id, IMAGE_EXTS)
                if not src:
                    logger.warning(f"Invalid temp image ID for project {project.id}: {upload_id}")
                    continue
                ext = src.suffix.lower()
                target = img_dir / f"user_{idx:03d}{ext}"
                shutil.copy2(src, target)
                idx += 1
            except Exception as e:
                logger.warning(f"Failed to move temp image {upload_id} for project {project.id}: {e}")

        allowed_ext = IMAGE_EXTS
        for img_upload in custom_image_uploads[: max(20 - idx, 0)]:
            try:
                ext = Path(img_upload.filename).suffix.lower()
                if ext not in allowed_ext:
                    ext = ".jpg"
                target = img_dir / f"user_{idx:03d}{ext}"
                content = await img_upload.read()
                if len(content) > 10 * 1024 * 1024:
                    logger.warning(f"Skipping image {img_upload.filename}: exceeds 10MB")
                    continue
                with open(target, "wb") as f:
                    f.write(content)
                logger.info(f"Saved custom image {idx} for project {project.id}: {target}")
                idx += 1
            except Exception as e:
                logger.warning(f"Failed to save custom image {idx} for project {project.id}: {e}")

    # Save custom video uploaded by user
    custom_video_path = ""
    if has_custom_video and custom_video_id:
        try:
            src = _resolve_temp_file(user["id"], custom_video_id, VIDEO_EXTS)
            if not src:
                raise HTTPException(status_code=400, detail="Vídeo enviado não foi encontrado.")
            vid_dir = Path(settings.media_dir) / "videos" / str(project.id)
            vid_dir.mkdir(parents=True, exist_ok=True)
            ext = src.suffix.lower() if src.suffix else ".mp4"
            target = vid_dir / f"user_video{ext}"
            shutil.copy2(src, target)
            custom_video_path = str(target)

            from app.services.video_composer import _get_duration as get_video_duration
            vid_dur = get_video_duration(custom_video_path)
            if vid_dur > 0:
                project.track_duration = round(vid_dur)
            logger.info(f"Custom video saved for project {project.id}: {custom_video_path} ({vid_dur:.1f}s)")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Failed to save custom video for project {project.id}: {e}")
            raise HTTPException(status_code=400, detail=f"Falha ao processar video enviado: {e}")

    # Save custom thumbnail uploaded by user
    if custom_thumbnail_id:
        try:
            src = _resolve_temp_file(user["id"], custom_thumbnail_id, IMAGE_EXTS)
            if src:
                thumb_dir = Path(settings.media_dir) / "thumbnails" / str(project.id)
                thumb_dir.mkdir(parents=True, exist_ok=True)
                ext = src.suffix.lower() if src.suffix else ".jpg"
                target = thumb_dir / f"custom_thumbnail{ext}"
                shutil.copy2(src, target)
                logger.info(f"Custom thumbnail saved for project {project.id}: {target}")
        except Exception as e:
            logger.warning(f"Failed to save custom thumbnail for project {project.id}: {e}")

    # Save optional custom background music. The pipeline will prioritize this file over Suno.
    custom_bgm_path = ""
    if bgm_upload and bgm_upload.filename:
        try:
            ext = Path(bgm_upload.filename).suffix.lower()
            if ext not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm"}:
                ext = ".mp3"
            music_dir = Path(settings.media_dir) / "audio" / str(project.id)
            music_dir.mkdir(parents=True, exist_ok=True)
            target = music_dir / f"custom_background_music{ext}"
            with open(target, "wb") as f:
                f.write(await bgm_upload.read())
            custom_bgm_path = str(target)
            logger.info(f"Custom background music uploaded for project {project.id}: {target}")
        except Exception as e:
            logger.warning(f"Failed to save custom background music for project {project.id}: {e}")
    elif background_music_id:
        try:
            src = _resolve_temp_file(user["id"], background_music_id, AUDIO_EXTS)
            if src:
                ext = src.suffix.lower()
                music_dir = Path(settings.media_dir) / "audio" / str(project.id)
                music_dir.mkdir(parents=True, exist_ok=True)
                target = music_dir / f"custom_background_music{ext}"
                shutil.copy2(src, target)
                custom_bgm_path = str(target)
                logger.info(f"Custom temp background music moved for project {project.id}: {target}")
        except Exception as e:
            logger.warning(f"Failed to move custom temp background music for project {project.id}: {e}")

    # Save optional custom main audio. If present, this becomes the primary video track.
    custom_main_audio_path = ""
    tevoxi_main_audio_path = ""
    if has_custom_audio:
        try:
            audio_dir = Path(settings.media_dir) / "audio" / str(project.id)
            audio_dir.mkdir(parents=True, exist_ok=True)

            source_path = None
            ext = ".mp3"

            if custom_audio_id:
                source_path = _resolve_temp_file(user["id"], custom_audio_id, AUDIO_EXTS)
                if not source_path:
                    raise HTTPException(status_code=400, detail="Áudio enviado não foi encontrado.")
                ext = source_path.suffix.lower() if source_path.suffix else ".mp3"

            target = audio_dir / f"user_main_audio{ext}"

            if source_path:
                shutil.copy2(source_path, target)
            elif custom_audio_upload and custom_audio_upload.filename:
                ext = Path(custom_audio_upload.filename).suffix.lower()
                if ext not in AUDIO_EXTS:
                    ext = ".mp3"
                target = audio_dir / f"user_main_audio{ext}"
                with open(target, "wb") as f:
                    f.write(await custom_audio_upload.read())
            else:
                raise HTTPException(status_code=400, detail="Áudio principal não enviado.")

            custom_main_audio_path = str(target)
            project.audio_path = custom_main_audio_path

            from app.services.video_composer import _get_duration as get_audio_duration

            audio_dur = get_audio_duration(custom_main_audio_path)
            project.track_duration = round(audio_dur) if audio_dur > 0 else 0
            logger.info(f"Custom main audio saved for project {project.id}: {custom_main_audio_path}")

            # For karaoke/music mode, transcribe original audio before optional vocal removal.
            if req.audio_is_music:
                try:
                    from app.services.transcriber import transcribe_audio
                    import asyncio

                    # Pass user-provided lyrics as prompt to guide Whisper accuracy
                    lyrics_hint = (project.lyrics_text or "").strip()
                    transcribed = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: transcribe_audio(custom_main_audio_path, prompt=lyrics_hint),
                    )
                    words = transcribed.get("words", []) if isinstance(transcribed, dict) else []
                    text = (transcribed.get("text", "") if isinstance(transcribed, dict) else "").strip()
                    if words:
                        project.lyrics_words = words
                    if text and not (project.lyrics_text or "").strip():
                        project.lyrics_text = text
                    logger.info(f"Karaoke transcription ready for project {project.id}: {len(words)} words")
                except Exception as e:
                    logger.warning(f"Failed to transcribe custom music for project {project.id}: {e}")

                if req.remove_vocals:
                    from app.services.audio_tools import remove_vocals_track

                    levita_auth_token = ""
                    auth_header = str(request.headers.get("authorization") or "").strip()
                    if auth_header.lower().startswith("bearer "):
                        levita_auth_token = auth_header.split(" ", 1)[1].strip()
                    if not levita_auth_token:
                        levita_auth_token = str(request.cookies.get("token") or "").strip()
                    if not levita_auth_token:
                        levita_auth_token = str(settings.levita_api_token or "").strip()

                    async def _karaoke_progress_callback(progress: int, message: str):
                        if not karaoke_operation_id:
                            return
                        _set_karaoke_progress(
                            karaoke_operation_id,
                            user["id"],
                            progress,
                            message,
                            status="running",
                            stage="removing_vocals",
                        )

                    try:
                        instrumental_path = await remove_vocals_track(
                            custom_main_audio_path,
                            project.id,
                            auth_token=levita_auth_token,
                            allow_ffmpeg_fallback=False,
                            progress_callback=_karaoke_progress_callback,
                        )
                    except Exception as sep_err:
                        logger.warning(f"Karaoke vocal removal failed on Olevita for project {project.id}: {sep_err}")
                        if karaoke_operation_id:
                            _set_karaoke_progress(
                                karaoke_operation_id,
                                user["id"],
                                100,
                                "Falha ao remover voz no Levita.",
                                status="failed",
                                stage="removing_vocals",
                                error=str(sep_err),
                            )
                        raise HTTPException(
                            status_code=502,
                            detail="Não foi possível concluir a remoção de voz agora. Tente novamente em alguns minutos.",
                        )

                    if not instrumental_path or not os.path.exists(instrumental_path):
                        if karaoke_operation_id:
                            _set_karaoke_progress(
                                karaoke_operation_id,
                                user["id"],
                                100,
                                "Não foi possível baixar o áudio sem voz.",
                                status="failed",
                                stage="removing_vocals",
                                error="instrumental_output_missing",
                            )
                        raise HTTPException(status_code=500, detail="Não foi possível remover a voz do áudio.")

                    if karaoke_operation_id:
                        _set_karaoke_progress(
                            karaoke_operation_id,
                            user["id"],
                            100,
                            "Voz removida com sucesso.",
                            status="completed",
                            stage="removing_vocals",
                        )

                    project.audio_path = instrumental_path
                    logger.info(f"Karaoke instrumental created for project {project.id}: {instrumental_path}")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"Failed to save custom main audio for project {project.id}: {e}")
            raise HTTPException(status_code=400, detail=f"Falha ao processar áudio enviado: {e}")

    if use_tevoxi_audio:
        try:
            audio_dir = Path(settings.media_dir) / "audio" / str(project.id)
            audio_dir.mkdir(parents=True, exist_ok=True)

            tevoxi_source_path = audio_dir / "tevoxi_source.mp3"
            await _download_external_audio_to_path((req.tevoxi_audio_url or "").strip(), tevoxi_source_path)

            clip_start = max(0.0, float(req.tevoxi_clip_start or 0))
            clip_duration = max(0.0, float(req.tevoxi_clip_duration or 0))
            if clip_start > 0 or clip_duration > 0:
                clipped_path = audio_dir / "tevoxi_main_audio.mp3"
                _trim_audio_clip(str(tevoxi_source_path), str(clipped_path), clip_start, clip_duration)
                tevoxi_main_audio_path = str(clipped_path)
            else:
                tevoxi_main_audio_path = str(tevoxi_source_path)

            project.audio_path = tevoxi_main_audio_path

            from app.services.video_composer import _get_duration as get_audio_duration

            tevoxi_duration = get_audio_duration(tevoxi_main_audio_path)
            project.track_duration = round(tevoxi_duration) if tevoxi_duration > 0 else 0

            if not script_text and (req.tevoxi_lyrics or "").strip():
                project.lyrics_text = (req.tevoxi_lyrics or "").strip()

            logger.info(f"Tevoxi main audio ready for project {project.id}: {tevoxi_main_audio_path}")
        except Exception as e:
            logger.warning(f"Failed to prepare Tevoxi main audio for project {project.id}: {e}")
            raise HTTPException(status_code=400, detail=f"Falha ao processar áudio do Tevoxi: {e}")

    try:
        primary_main_audio_path = custom_main_audio_path or tevoxi_main_audio_path

        if primary_main_audio_path:
            project.audio_path = primary_main_audio_path
            if project.track_duration <= 0:
                from app.services.video_composer import _get_duration as get_audio_duration

                custom_duration = get_audio_duration(project.audio_path)
                project.track_duration = round(custom_duration) if custom_duration > 0 else 60
        elif script_text:
            if is_suno_narration:
                from app.services.suno_narration import generate_suno_narration
                audio_path = await generate_suno_narration(
                    text=req.script,
                    voice_preset=voice,
                    project_id=project.id,
                    tone=req.tone,
                )
                if not audio_path:
                    raise Exception("Falha ao gerar narração Suno AI. Tente novamente.")
                project.audio_path = audio_path
                # Suno narration includes background music — skip separate BGM
                project.no_background_music = True
            else:
                audio_path = await generate_tts_audio(
                    text=req.script,
                    voice=voice,
                    project_id=project.id,
                    tts_instructions=tts_instructions,
                    voice_type=voice_type,
                    pause_level=req.pause_level,
                    tone=req.tone,
                )
                project.audio_path = audio_path

            # Estimate duration from word count (~2.5 words/sec for TTS)
            word_count = len(req.script.split())
            project.track_duration = round(word_count / 2.5)
        else:
            if custom_bgm_path:
                from app.services.video_composer import _get_duration as get_audio_duration

                project.audio_path = custom_bgm_path
                bgm_duration = get_audio_duration(custom_bgm_path)
                project.track_duration = round(bgm_duration) if bgm_duration > 0 else 60
            else:
                # No narration + no uploaded music: pipeline will generate instrumental music automatically.
                project.audio_path = ""
                project.track_duration = 0
            if not has_custom_video:
                project.enable_subtitles = False

        project.status = VideoStatus.GENERATING_SCENES
        project.progress = 0
        await db.commit()

        subtitle_y = int(req.subtitle_position_y or 80)
        if subtitle_y not in (20, 50, 80):
            subtitle_y = 80

        pipeline_options = {
            "subtitle_settings": {"y": subtitle_y},
            "enable_audio_spectrum": bool(use_tevoxi_audio and req.enable_audio_spectrum),
        }

        from app.tasks.video_tasks import run_video_pipeline
        background_tasks.add_task(run_video_pipeline, project.id, pipeline_options)

        return {
            "id": project.id,
            "title": project.title,
            "status": "generating_scenes",
            "estimated_duration": project.track_duration,
        }
    except HTTPException:
        project.status = VideoStatus.FAILED
        project.error_message = "Configuração inválida para geração de áudio"
        await db.commit()
        raise
    except Exception as e:
        project.status = VideoStatus.FAILED
        project.error_message = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Erro ao gerar áudio: {e}")


# ── Realistic Video (Seedance 2.0) ──────────────────────────────


class GenerateRealisticPromptRequest(BaseModel):
    topic: str
    style: str = "cinematic"
    engine: str = "wan2"
    duration: int = 10
    interaction_persona: str = "natureza"
    has_reference_image: bool = False


@router.post("/generate-realistic-prompt")
async def generate_realistic_prompt_endpoint(
    req: GenerateRealisticPromptRequest,
    user: dict = Depends(get_current_user),
):
    """Generate an optimized realistic-video prompt from a simple topic/theme."""
    topic = (req.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Descreva o tema do vídeo.")
    if len(topic) > 2000:
        raise HTTPException(status_code=400, detail="Tema muito longo (máximo 2000 caracteres).")

    engine = req.engine if req.engine in ("seedance", "minimax", "wan2", "grok") else "wan2"
    max_dur = 60 if engine == "grok" else 10
    duration = max(1, min(int(req.duration or 10), max_dur))
    interaction_persona = _normalize_interaction_persona(req.interaction_persona)
    prompt_for_optimizer = _ensure_reference_image_instruction(topic) if req.has_reference_image else topic
    prompt_for_optimizer = _inject_interaction_persona_instruction(prompt_for_optimizer, interaction_persona)

    if engine == "grok":
        from app.services.grok_video import optimize_prompt_for_grok

        optimized = await optimize_prompt_for_grok(
            user_description=prompt_for_optimizer,
            duration=duration,
            has_reference_image=req.has_reference_image,
            tone=req.style,
        )
    else:
        from app.services.seedance_video import optimize_prompt_for_seedance

        optimized = await optimize_prompt_for_seedance(
            user_description=prompt_for_optimizer,
            duration=duration,
            tone=req.style,
            has_reference_image=req.has_reference_image,
        )

    temporal_prompt = await _generate_temporal_realistic_prompt(
        optimized_prompt=optimized,
        duration=duration,
    )

    final_prompt = _inject_interaction_persona_instruction(temporal_prompt, interaction_persona)
    if req.has_reference_image:
        final_prompt = _ensure_reference_image_instruction(final_prompt)

    return {"prompt": final_prompt}


class GenerateRealisticRequest(BaseModel):
    prompt: str
    duration: int = 7
    aspect_ratio: str = "16:9"
    generate_audio: bool = True
    add_music: bool = True
    add_narration: bool = False
    narration_text: str = ""
    narration_voice: str = "onyx"
    title: str = ""
    image_upload_id: str = ""
    image_upload_ids: list[str] = Field(default_factory=list)
    engine: str = "wan2"  # "seedance", "minimax", "wan2" or "grok"
    audio_url: str = ""       # External audio URL (e.g. from Tevoxi)
    lyrics: str = ""          # Lyrics/transcription for the audio clip
    clip_start: float = 0     # Start time in seconds for audio clip
    clip_duration: float = 0  # Duration of the audio clip (0 = full)
    prompt_optimized: bool = False
    realistic_style: str = ""
    interaction_persona: str = "natureza"
    persona_profile_id: int = 0
    persona_profile_ids: list[int] = Field(default_factory=list)
    dialogue_enabled: bool = False
    dialogue_characters: list[str] = Field(default_factory=list)
    dialogue_voice_profile_ids: list[int] = Field(default_factory=list)
    dialogue_tone: str = "informativo"
    dialogue_duration: int = 0


@router.post("/generate-realistic")
async def generate_realistic_endpoint(
    req: GenerateRealisticRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate a realistic AI video using the available realistic engines."""
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Descreva a cena que você quer ver no vídeo.")
    if len(prompt) > 5000:
        raise HTTPException(status_code=400, detail="Descrição muito longa (máximo 5000 caracteres).")

    engine = req.engine if req.engine in ("seedance", "minimax", "wan2", "grok") else "wan2"
    max_dur = 60 if engine == "grok" else 10
    duration = max(1, min(req.duration, max_dur))

    if req.aspect_ratio not in {"16:9", "9:16", "1:1"}:
        raise HTTPException(status_code=400, detail="Formato inválido. Use 16:9, 9:16 ou 1:1.")

    interaction_persona = _normalize_interaction_persona(req.interaction_persona)
    selected_persona_profile_id = int(req.persona_profile_id or 0)
    selected_persona_profile_ids: list[int] = []
    for raw_pid in (req.persona_profile_ids or []):
        try:
            parsed_pid = int(raw_pid)
        except Exception:
            continue
        if parsed_pid > 0 and parsed_pid not in selected_persona_profile_ids:
            selected_persona_profile_ids.append(parsed_pid)

    # Backward compatibility for older clients that send only persona_profile_id.
    if selected_persona_profile_id and selected_persona_profile_id not in selected_persona_profile_ids:
        selected_persona_profile_ids.insert(0, selected_persona_profile_id)

    dialogue_enabled = bool(req.dialogue_enabled)
    dialogue_characters: list[str] = []
    for raw_name in (req.dialogue_characters or []):
        cleaned = str(raw_name or "").strip()
        if cleaned and cleaned not in dialogue_characters:
            dialogue_characters.append(cleaned[:40])
    dialogue_characters = dialogue_characters[:4]

    dialogue_voice_profile_ids: list[int] = []
    for raw_voice_id in (req.dialogue_voice_profile_ids or []):
        try:
            parsed_voice_id = int(raw_voice_id)
        except Exception:
            continue
        if parsed_voice_id > 0 and parsed_voice_id not in dialogue_voice_profile_ids:
            dialogue_voice_profile_ids.append(parsed_voice_id)
    dialogue_voice_profile_ids = dialogue_voice_profile_ids[:4]

    dialogue_tone = (req.dialogue_tone or "informativo").strip()[:40] or "informativo"
    dialogue_duration = max(1, min(duration, int(req.dialogue_duration or duration))) if dialogue_enabled else 0

    upload_ids: list[str] = []
    for upload_id in (req.image_upload_ids or []):
        cleaned = str(upload_id or "").strip()
        if cleaned and cleaned not in upload_ids:
            upload_ids.append(cleaned)
    if req.image_upload_id and req.image_upload_id not in upload_ids:
        upload_ids.insert(0, req.image_upload_id)
    upload_ids = upload_ids[:6]

    # Resolve reference image with precedence: uploaded images > selected personas > default persona
    image_path_str = ""
    reference_count = 0
    resolved_personas = []
    persona_dialogue_voice_profile_ids: list[int] = []
    persona_dialogue_characters: list[str] = []
    if upload_ids:
        upload_image_paths: list[str] = []
        for upload_id in upload_ids:
            resolved = _resolve_temp_file(user["id"], upload_id, IMAGE_EXTS)
            if not resolved:
                raise HTTPException(status_code=400, detail="Uma das imagens de referência não foi encontrada. Envie novamente.")
            upload_image_paths.append(str(resolved))

        image_path_str = build_persona_reference_montage(
            user_id=user["id"],
            image_paths=upload_image_paths,
            prefix="upload_refs",
        )
        reference_count = len(upload_image_paths)
    else:
        try:
            resolved_personas, persona_image_paths = await resolve_persona_reference_images(
                db=db,
                user_id=user["id"],
                persona_type=interaction_persona,
                persona_profile_ids=selected_persona_profile_ids,
                ensure_default=False,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        if not persona_image_paths:
            try:
                resolved_persona, persona_image_path = await resolve_persona_reference_image(
                    db=db,
                    user_id=user["id"],
                    persona_type=interaction_persona,
                    persona_profile_id=selected_persona_profile_id,
                    ensure_default=False,
                )
                if resolved_persona and persona_image_path:
                    resolved_personas = [resolved_persona]
                    persona_image_paths = [persona_image_path]
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc))

        if not persona_image_paths:
            raise HTTPException(status_code=400, detail="Crie uma ou mais personas de interação antes de gerar o vídeo realista.")

        reference_count = len(persona_image_paths)
        image_path_str = build_persona_reference_montage(
            user_id=user["id"],
            image_paths=persona_image_paths,
            prefix="persona_refs",
        )

        for profile in resolved_personas:
            persona_name = str(getattr(profile, "name", "") or "").strip()
            if persona_name and persona_name not in persona_dialogue_characters:
                persona_dialogue_characters.append(persona_name[:40])

            attrs = getattr(profile, "attributes", {}) if isinstance(getattr(profile, "attributes", {}), dict) else {}
            try:
                voice_pid = int(attrs.get("voice_profile_id") or 0)
            except Exception:
                voice_pid = 0
            if voice_pid > 0 and voice_pid not in persona_dialogue_voice_profile_ids:
                persona_dialogue_voice_profile_ids.append(voice_pid)

        selected_persona_profile_ids = [int(profile.id) for profile in resolved_personas]
        selected_persona_profile_id = selected_persona_profile_ids[0] if selected_persona_profile_ids else 0

    auto_dialogue_requested = bool(req.add_narration) and not bool(str(req.narration_text or "").strip())
    if auto_dialogue_requested and not dialogue_enabled:
        dialogue_enabled = True

    if dialogue_enabled:
        if not dialogue_characters and persona_dialogue_characters:
            dialogue_characters = persona_dialogue_characters[:4]
        if not dialogue_characters:
            dialogue_characters = ["Personagem"]

        if not dialogue_voice_profile_ids and persona_dialogue_voice_profile_ids:
            dialogue_voice_profile_ids = persona_dialogue_voice_profile_ids[:4]

    dialogue_duration = max(1, min(duration, int(req.dialogue_duration or duration))) if dialogue_enabled else 0

    has_reference_image = bool(image_path_str)
    if not has_reference_image:
        raise HTTPException(status_code=400, detail="Vídeo realista exige imagem de referência.")

    prompt = _ensure_reference_image_instruction(prompt)
    if reference_count > 1:
        prompt = (
            f"{prompt}\n\n"
            "MULTI-PERSONA REFERENCE RULE: Use all uploaded reference identities together in the same scene. "
            "Preserve each face identity and visual traits without merging faces into one person."
        )

    # Credit check — multi-clip costs more (1 credit per 15s segment)
    from app.routers.credits import CREDITS_PER_MINUTE, deduct_credits
    num_clips = -(-duration // 15) if engine == "grok" and duration > 15 else 1
    credits_needed = CREDITS_PER_MINUTE * num_clips
    await deduct_credits(db, user["id"], credits_needed)

    # Use custom title if provided
    project_title = (req.title or "").strip()
    if not project_title:
        project_title = prompt[:100]

    engine_labels = {"minimax": "MiniMax Hailuo", "wan2": "Ultra High 2.2", "seedance": "Seedance 2.0", "grok": "Cria 3.0 speed"}
    engine_label = engine_labels.get(engine, "Ultra High 2.2")

    # Narration config stored in tags JSON
    narration_text = (req.narration_text or "").strip() if req.add_narration and not dialogue_enabled else ""
    narration_voice = req.narration_voice or "onyx"
    speech_mode = "none"
    if dialogue_enabled:
        speech_mode = "dialogue_auto"
    elif req.add_narration and bool(narration_text):
        speech_mode = "narration_manual"

    external_audio_url = (req.audio_url or "").strip()
    external_lyrics = (req.lyrics or "").strip()
    tags_data = {
        "type": "realista",
        "engine": engine,
        "has_reference_image": has_reference_image,
        "reference_source": "upload" if upload_ids else "persona",
        "reference_count": max(1, reference_count),
        "add_music": req.add_music or bool(external_audio_url),
        "add_narration": req.add_narration and bool(narration_text) and not dialogue_enabled,
        "speech_mode": speech_mode,
        "speech_auto_requested": auto_dialogue_requested,
        "narration_voice": narration_voice,
        "prompt_optimized": bool(req.prompt_optimized),
        "realistic_style": (req.realistic_style or "").strip(),
        "interaction_persona": interaction_persona,
        "persona_profile_id": 0 if upload_ids else selected_persona_profile_id,
        "persona_profile_ids": [] if upload_ids else selected_persona_profile_ids,
        "dialogue_enabled": dialogue_enabled,
        "dialogue_characters": dialogue_characters,
        "dialogue_voice_profile_ids": dialogue_voice_profile_ids,
        "dialogue_tone": dialogue_tone,
        "dialogue_duration": dialogue_duration,
    }
    if external_audio_url:
        tags_data["audio_url"] = external_audio_url
        tags_data["clip_start"] = req.clip_start
        tags_data["clip_duration"] = req.clip_duration
    if external_lyrics:
        tags_data["lyrics"] = external_lyrics

    project = VideoProject(
        user_id=user["id"],
        track_id=0,
        title=project_title,
        description=narration_text,
        tags=tags_data,
        style_prompt=image_path_str,
        aspect_ratio=req.aspect_ratio,
        track_title=project_title,
        track_artist=engine_label,
        track_duration=float(duration),
        lyrics_text=prompt,
        lyrics_words=[],
        audio_path=engine,
        is_realistic=True,
        no_background_music=not req.add_music,
        enable_subtitles=False,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    project.status = VideoStatus.GENERATING_SCENES
    project.progress = 0
    await db.commit()

    from app.tasks.video_tasks import run_realistic_video_pipeline
    background_tasks.add_task(run_realistic_video_pipeline, project.id)

    return {
        "id": project.id,
        "title": project.title,
        "status": "generating_scenes",
        "duration": duration,
    }

