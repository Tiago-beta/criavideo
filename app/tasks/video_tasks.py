"""
Video Tasks — Async background tasks for the full video generation pipeline.
"""
import os
import asyncio
import logging
import shutil
import httpx
from pathlib import Path
from app.config import get_settings
from app.database import async_session
from app.models import VideoProject, VideoScene, VideoRender, VideoStatus

logger = logging.getLogger(__name__)
settings = get_settings()


def _safe_error_message(err, fallback: str) -> str:
    """Return a readable error message even when upstream exceptions stringify to '{}' or empty."""
    try:
        raw = str(err or "").strip()
    except Exception:
        raw = ""

    if raw and raw not in {"{}", "[]", "None", "null", "[object Object]"}:
        return raw

    err_name = type(err).__name__ if err is not None else "ErroDesconhecido"
    return f"{fallback} ({err_name})."


def _aspect_to_resolution(aspect_ratio: str) -> tuple[int, int]:
    if aspect_ratio == "9:16":
        return 1080, 1920
    if aspect_ratio == "1:1":
        return 1080, 1080
    return 1920, 1080


async def _normalize_video_aspect(input_path: str, aspect_ratio: str, output_path: str) -> str:
    """Force output video to requested aspect ratio using scale+crop."""
    tw, th = _aspect_to_resolution(aspect_ratio)
    vf = f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th},setsar=1"

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if proc.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Aspect normalization failed for {input_path}")
    return output_path


async def _trim_video_duration(input_path: str, duration_seconds: float, output_path: str) -> str:
    """Trim video duration while keeping the original audio track if present."""
    target = max(1.0, float(duration_seconds or 0))
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", f"{target:.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-map", "0:v:0",
        "-map", "0:a?",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if proc.returncode != 0 or not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"Video trim failed for {input_path}")
    return output_path


async def _video_has_audio_stream(video_path: str) -> bool:
    if not video_path or not os.path.exists(video_path):
        return False

    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return False

    return "audio" in (stdout or b"").decode(errors="ignore").lower()


def _find_custom_background_music(project_id: int) -> str:
    """Return custom uploaded background music path if present."""
    audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
    if not audio_dir.exists():
        return ""
    for ext in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm"):
        candidate = audio_dir / f"custom_background_music{ext}"
        if candidate.exists():
            return str(candidate)
    return ""


def _find_custom_video(project_id: int) -> str:
    """Return custom uploaded video path if present."""
    vid_dir = Path(settings.media_dir) / "videos" / str(project_id)
    if not vid_dir.exists():
        return ""
    for ext in (".mp4", ".mov", ".avi", ".webm", ".mkv"):
        candidate = vid_dir / f"user_video{ext}"
        if candidate.exists():
            return str(candidate)
    return ""


def _build_tevoxi_auth_headers(audio_url: str) -> dict:
    """Build auth headers for Tevoxi private audio URLs when possible."""
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
_GROK_IDENTITY_HINT_MARKERS = (
    "grok identity lock",
    "close-up identity lock",
    "trava de identidade grok",
    "trava de close-up",
)


def _is_face_identity_reference(reference_mode: str) -> bool:
    return str(reference_mode or "").strip().lower() in {"face_identity_only", "face_only", "persona_face"}


def _ensure_reference_image_instruction(prompt: str, reference_mode: str = "") -> str:
    """Guarantee that prompt text explicitly anchors generation to user reference image."""
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        return base_prompt

    lowered = base_prompt.lower()
    if _is_face_identity_reference(reference_mode):
        if "modo rosto da persona" in lowered or "somente identidade facial" in lowered:
            return base_prompt
        reference_rule = (
            "MODO ROSTO DA PERSONA (OBRIGATORIO): use a imagem de referencia somente para preservar identidade facial. "
            "Preserve geometria do rosto, olhos, nariz, labios, mandibula, tom de pele, idade aparente e linha/cor do cabelo. "
            "Nao preserve roupas, fundo, objetos, pose, enquadramento, iluminacao, paleta de cores ou ambiente da foto. "
            "Crie roupa, cenario, acao, composicao e clima visual novos de acordo com o prompt atual."
        )
        return f"{base_prompt}\n\n{reference_rule}"

    if any(marker in lowered for marker in _REFERENCE_IMAGE_HINT_MARKERS):
        return base_prompt

    reference_rule = (
        "REGRA OBRIGATORIA DE IMAGEM DE REFERENCIA: use a imagem enviada como ancora visual principal. "
        "Mantenha a mesma identidade do sujeito, tracos de rosto, cabelo, paleta de cores e estilo visual geral da referencia."
    )
    return f"{base_prompt}\n\n{reference_rule}"


def _ensure_grok_identity_lock(prompt: str, reference_mode: str = "") -> str:
    """Add a strict identity lock optimized for Grok close-up fidelity."""
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        return base_prompt

    lowered = base_prompt.lower()
    if _is_face_identity_reference(reference_mode):
        if "trava facial grok" in lowered:
            return base_prompt
        identity_lock = (
            "TRAVA FACIAL GROK (OBRIGATORIA): a imagem de referencia define apenas o rosto e a identidade humana. "
            "Mantenha o mesmo rosto em close-up sem face swap, sem morphing e sem trocar protagonista. "
            "Nao copie roupa, fundo, pose, objetos, iluminacao, cores ou composicao da foto de referencia. "
            "O figurino, o ambiente, a acao e a atmosfera devem ser recriados livremente a partir do texto do prompt."
        )
        return f"{base_prompt}\n\n{identity_lock}"

    if any(marker in lowered for marker in _GROK_IDENTITY_HINT_MARKERS):
        return base_prompt

    identity_lock = (
        "TRAVA DE IDENTIDADE GROK (OBRIGATORIA): use a imagem de referencia como identidade exata do protagonista. "
        "Preserve a mesma geometria facial, olhos, nariz, labios, mandibula, tom de pele, linha e cor do cabelo e idade aparente. "
        "TRAVA DE CLOSE-UP: em planos fechados, mantenha exatamente o mesmo rosto, sem face swap, sem novo protagonista e sem morphing relevante. "
        "Camera, iluminacao, acao e ambiente podem mudar, mas a identidade deve permanecer identica a referencia."
    )
    return f"{base_prompt}\n\n{identity_lock}"


def _ensure_seedance_audio_instruction(prompt: str) -> str:
    """Bias Seedance toward native diegetic SFX instead of soundtrack music."""
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        return base_prompt

    lowered = base_prompt.lower()
    audio_markers = (
        "audio:",
        "sound effect",
        "diegetic",
        "som ambiente",
        "efeitos sonoros",
        "no soundtrack",
        "sem trilha musical",
    )
    if any(marker in lowered for marker in audio_markers):
        return base_prompt

    audio_rule = (
        "AUDIO (OBRIGATORIO): incluir efeitos sonoros diegeticos realistas sincronizados com as acoes da cena "
        "(motor, pneu, vento, rua, passos, tecido, etc. quando fizer sentido). "
        "Sem trilha musical de fundo, sem canto e sem narracao."
    )
    return f"{base_prompt}\n\n{audio_rule}"


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
    return normalized if normalized in _INTERACTION_PERSONAS else ""


def _build_interaction_persona_instruction(interaction_persona: str) -> str:
    persona = _normalize_interaction_persona(interaction_persona)
    if persona == "homem":
        return (
            "PERSONA DE INTERACAO: inclua um homem em cena interagindo com o ambiente e a emocao do trecho "
            "(por exemplo, orando, cantando, caminhando, contemplando), sem perder o sentido da letra."
        )
    if persona == "mulher":
        return (
            "PERSONA DE INTERACAO: inclua uma mulher em cena interagindo com o ambiente e a emocao do trecho "
            "(por exemplo, orando, cantando, caminhando, contemplando), sem perder o sentido da letra."
        )
    if persona == "crianca":
        return (
            "PERSONA DE INTERACAO: inclua uma crianca em cena interagindo com o ambiente e a emocao do trecho, "
            "com linguagem visual sensivel e respeitosa."
        )
    if persona == "familia":
        return (
            "PERSONA DE INTERACAO: inclua uma familia (duas ou mais pessoas) interagindo de forma natural com a cena "
            "e com a emocao do trecho."
        )
    if persona == "desenho":
        return (
            "PERSONA DE INTERACAO: inclua um personagem em estilo desenho/animacao (cartoon, 3D, anime, etc.) "
            "interagindo de forma natural com a cena e com a emocao do trecho."
        )
    if persona == "personalizado":
        return (
            "PERSONA DE INTERACAO: inclua a persona personalizada definida pelo usuario, mantendo os tracos, "
            "estilo e identidade visual da referencia enviada."
        )
    if persona == "natureza":
        return (
            "PERSONA DE INTERACAO: priorize natureza viva e inclua obrigatoriamente pelo menos um elemento visual "
            "de conexao (animal, flor, ave, borboleta ou outro ser vivo natural) em destaque, coerente com o trecho."
        )
    return ""


def _inject_interaction_persona_instruction(prompt: str, interaction_persona: str) -> str:
    base_prompt = (prompt or "").strip()
    if not base_prompt:
        return base_prompt
    if "PERSONA DE INTERACAO:" in base_prompt:
        return base_prompt

    persona_instruction = _build_interaction_persona_instruction(interaction_persona)
    if not persona_instruction:
        return base_prompt
    return f"{base_prompt} {persona_instruction}"


def _build_transcribed_realistic_prompt(transcribed_text: str, interaction_persona: str = "") -> str:
    """Build a visual prompt grounded on the exact transcribed segment."""
    excerpt = " ".join((transcribed_text or "").split())[:420]
    excerpt_lower = excerpt.lower()
    has_field_imagery = any(
        kw in excerpt_lower
        for kw in ("trigo", "wheat", "campo", "pasto", "pastagem", "fazenda", "meadow", "farm")
    )
    anti_repeat = (
        " Evite cliches repetidos como campo de trigo, roupas totalmente brancas e poses padrao "
        "quando isso nao estiver claramente no trecho cantado."
        if not has_field_imagery
        else ""
    )
    persona_instruction = _build_interaction_persona_instruction(interaction_persona)
    persona_suffix = f" {persona_instruction}" if persona_instruction else ""

    if not excerpt:
        return (
            "Crie uma cena realista cinematografica inspirada no trecho cantado. "
            "Baseie a composicao apenas no trecho atual, sem reaproveitar elementos de outros versos."
            " Evite cliches repetidos como campo de trigo e roupas totalmente brancas "
            "quando isso nao estiver no trecho cantado."
            f"{persona_suffix}"
        )

    return (
        f'Trecho transcrito da musica: "{excerpt}". '
        "Crie uma cena realista cinematografica baseada somente nessas palavras e na emocao desse trecho, "
        "sem usar ideias de outros versos da musica. "
        "Nao force personagem humano quando o trecho nao pedir isso; priorize os simbolos e a acao citados no trecho. "
        "sem texto na tela e sem sobreposicoes de legenda no proprio frame."
        f"{anti_repeat}"
        f"{persona_suffix}"
    )


def _build_dialogue_visual_lock(dialogue_turns: list[dict], interaction_persona: str = "", target_duration: float = 8.0) -> str:
    valid_lines: list[str] = []
    for idx, item in enumerate(dialogue_turns or []):
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker") or "Personagem").strip() or "Personagem"
        text = " ".join(str(item.get("text") or "").split()).strip()
        if not text:
            continue
        try:
            start = float(item.get("start", 0) or 0)
        except Exception:
            start = 0.0
        try:
            end = float(item.get("end", start + 1.2) or (start + 1.2))
        except Exception:
            end = start + 1.2
        valid_lines.append(f"- {start:.2f}s ate {max(end, start + 0.2):.2f}s | {speaker}: \"{text[:180]}\"")
        if idx >= 7:
            break

    if not valid_lines:
        return ""

    persona_instruction = _build_interaction_persona_instruction(interaction_persona)
    persona_suffix = f" {persona_instruction}" if persona_instruction else ""
    timeline = "\n".join(valid_lines)

    return (
        "DIALOGUE SYNC LOCK (OBRIGATORIO): o video deve seguir esta linha de dialogo e manter sincronia visual aproximada "
        "de labios/gestos com as falas na ordem e no tempo indicados. "
        f"Duracao-alvo: {max(1.0, float(target_duration or 8.0)):.2f}s. "
        "Mantenha os mesmos personagens durante todo o clipe, alternando foco de camera por falante sem trocar identidade. "
        "Evite cenas que contradigam o texto falado.\n"
        "TIMELINE DE FALAS:\n"
        f"{timeline}"
        f"{persona_suffix}"
    )


async def _run_custom_video_pipeline(db, project, project_id: int):
    """Pipeline for user-uploaded video: overlay subtitles + optional narration."""
    from app.services.video_composer import compose_overlay_video
    from app.services.video_composer import _get_duration as get_duration

    video_path = _find_custom_video(project_id)
    if not video_path:
        raise FileNotFoundError("Video do usuario nao encontrado.")

    video_duration = get_duration(video_path)
    if video_duration <= 0:
        video_duration = 60.0
    project.track_duration = round(video_duration)

    narration_path = ""
    has_script = (project.lyrics_text or "").strip()

    # Step 1: Generate TTS narration if user provided a script
    if has_script and project.audio_path and os.path.exists(project.audio_path):
        # Audio already generated by the router
        narration_path = project.audio_path
        logger.info(f"Custom video: using pre-generated narration {narration_path}")
    elif has_script:
        project.status = VideoStatus.GENERATING_SCENES
        project.progress = 10
        await db.commit()
        logger.info(f"Custom video: script provided but no narration audio, subtitles only")

    # Step 2: Transcribe narration or video audio for accurate subtitles
    project.status = VideoStatus.GENERATING_SCENES
    project.progress = 30
    await db.commit()

    transcribed_words = []
    transcribed_text = ""
    # First try: transcribe narration audio if available
    if narration_path and os.path.exists(narration_path):
        try:
            from app.services.transcriber import transcribe_audio
            import asyncio
            lyrics_hint = (project.lyrics_text or "").strip()
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: transcribe_audio(narration_path, prompt=lyrics_hint)
            )
            transcribed_words = result.get("words", [])
            transcribed_text = (result.get("text", "") or "").strip()
            if not project.lyrics_text and transcribed_text:
                project.lyrics_text = transcribed_text
                await db.commit()
            logger.info(f"Custom video: narration transcription: {len(transcribed_words)} words")
        except Exception as e:
            logger.warning(f"Custom video: narration transcription failed: {e}")
    # Second: if no narration and no script, transcribe video's own audio for auto-subtitles
    elif not has_script:
        try:
            from app.services.transcriber import transcribe_audio
            import asyncio
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: transcribe_audio(video_path)
            )
            transcribed_words = result.get("words", [])
            transcribed_text = (result.get("text", "") or "").strip()
            if transcribed_text:
                project.lyrics_text = transcribed_text
                has_script = True
                await db.commit()
            logger.info(f"Custom video: video audio transcription: {len(transcribed_words)} words, text={len(transcribed_text)} chars")
        except Exception as e:
            logger.warning(f"Custom video: video audio transcription failed: {e}")

    # Step 3: Generate subtitles
    subtitle_path = ""
    enable_subtitles = getattr(project, "enable_subtitles", True)
    if enable_subtitles is None:
        enable_subtitles = True

    if enable_subtitles and (transcribed_words or has_script):
        from app.services.subtitle_generator import generate_ass_subtitles, generate_ass_from_text

        subtitle_dir = Path(settings.media_dir) / "subtitles" / str(project_id)
        subtitle_dir.mkdir(parents=True, exist_ok=True)
        subtitle_path = str(subtitle_dir / "karaoke.ass")

        is_karaoke_project = getattr(project, "is_karaoke", False) or False
        narration_mode = not is_karaoke_project

        if transcribed_words:
            generate_ass_subtitles(
                lyrics_words=transcribed_words,
                aspect_ratio=project.aspect_ratio,
                output_path=subtitle_path,
                narration_mode=narration_mode,
            )
        elif has_script:
            duration = get_duration(narration_path) if narration_path else video_duration
            generate_ass_from_text(
                lyrics_text=project.lyrics_text,
                duration=duration,
                aspect_ratio=project.aspect_ratio,
                output_path=subtitle_path,
                narration_mode=narration_mode,
            )
        logger.info(f"Custom video: subtitles generated at {subtitle_path}")

    # Step 4: Compose overlay video
    project.status = VideoStatus.RENDERING
    project.progress = 60
    await db.commit()

    import asyncio
    render_result = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: compose_overlay_video(
            project_id=project_id,
            video_path=video_path,
            subtitle_path=subtitle_path,
            narration_path=narration_path,
            aspect_ratio=project.aspect_ratio,
        ),
    )

    await db.rollback()
    project = await db.get(VideoProject, project_id)

    project.progress = 90
    await db.commit()

    # Step 5: Generate thumbnail (skip if user uploaded custom thumbnail)
    thumb_dir = Path(settings.media_dir) / "thumbnails" / str(project_id)
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = str(thumb_dir / "thumbnail.jpg")

    custom_thumb = next(thumb_dir.glob("custom_thumbnail.*"), None)
    if custom_thumb:
        shutil.copy2(str(custom_thumb), thumb_path)
        logger.info(f"Using custom thumbnail for project {project_id}")
    else:
        from app.services.thumbnail_generator import generate_thumbnail_from_frame
        try:
            generate_thumbnail_from_frame(
                video_path=render_result["file_path"],
                title=project.track_title or project.title,
                artist=project.track_artist or "",
                output_path=thumb_path,
            )
        except Exception as e:
            logger.warning(f"Custom video thumbnail failed: {e}")
            thumb_path = ""

    # Save render
    render = VideoRender(
        project_id=project_id,
        format=project.aspect_ratio,
        file_path=render_result["file_path"],
        file_size=render_result["file_size"],
        thumbnail_path=thumb_path,
        duration=render_result["duration"],
    )
    db.add(render)

    project.status = VideoStatus.COMPLETED
    project.progress = 100
    await db.commit()
    logger.info(f"Custom video pipeline complete for project {project_id}")


async def download_audio_if_url(audio_path: str, project_id: int) -> str:
    """If audio_path is a URL, download it locally and return the local path."""
    if not audio_path or not audio_path.startswith(("http://", "https://")):
        return audio_path

    audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
    audio_dir.mkdir(parents=True, exist_ok=True)
    local_path = str(audio_dir / "track.mp3")

    if os.path.exists(local_path):
        return local_path

    logger.info(f"Downloading audio from {audio_path}")
    headers = _build_tevoxi_auth_headers(audio_path)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(audio_path, headers=headers or None)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)

    logger.info(f"Audio downloaded: {local_path} ({os.path.getsize(local_path)} bytes)")
    return local_path


async def run_video_pipeline(project_id: int, pipeline_options: dict | None = None):
    """Full pipeline: scenes → subtitles → render → thumbnail.
    Runs as a background task.
    """
    async with async_session() as db:
        try:
            project = await db.get(VideoProject, project_id)
            if not project:
                return

            pipeline_cfg = pipeline_options if isinstance(pipeline_options, dict) else {}
            subtitle_settings = pipeline_cfg.get("subtitle_settings", {})
            if not isinstance(subtitle_settings, dict):
                subtitle_settings = {}
            enable_audio_spectrum = bool(pipeline_cfg.get("enable_audio_spectrum", False))

            # ── Step 0: Download audio if URL ──
            from app.services.video_composer import compose_video
            from app.services.video_composer import _get_duration as get_audio_duration

            # ── Custom Video Pipeline (user uploaded video → subtitles overlay) ──
            use_custom_video = getattr(project, "use_custom_video", False) or False
            if use_custom_video:
                await _run_custom_video_pipeline(db, project, project_id)
                return

            audio_path = await download_audio_if_url(project.audio_path, project_id) if project.audio_path else ""
            use_custom_images = getattr(project, "use_custom_images", False) or False
            image_display_seconds = float(getattr(project, "image_display_seconds", 0) or 0)
            zoom_images = bool(getattr(project, "zoom_images", True))
            is_music_only_mode = use_custom_images and not (project.lyrics_text or "").strip()

            if (not audio_path or not os.path.exists(audio_path)) and is_music_only_mode:
                # Photo-only mode without uploaded music: generate instrumental soundtrack automatically.
                img_dir = Path(settings.media_dir) / "images" / str(project_id)
                user_images_count = len([p for p in img_dir.glob("user_*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}])
                base_seconds = image_display_seconds if image_display_seconds > 0 else 12.0
                target_dur = max(30.0, min(240.0, float(max(user_images_count, 1) * base_seconds)))

                text_hint = f"{project.style_prompt or ''} {project.title or ''}".lower()
                bgm_mood = "inspiracional"
                if any(w in text_hint for w in ["terror", "horror", "misterio", "dark", "suspense"]):
                    bgm_mood = "misterioso"
                elif any(w in text_hint for w in ["urgente", "alerta", "crise", "perigo"]):
                    bgm_mood = "urgente"
                elif any(w in text_hint for w in ["motivac", "superac", "força", "poder"]):
                    bgm_mood = "motivacional"
                elif any(w in text_hint for w in ["reflex", "calma", "paz", "tranquil"]):
                    bgm_mood = "reflexivo"
                elif any(w in text_hint for w in ["drama", "triste", "emocio"]):
                    bgm_mood = "dramatico"

                music_dir = Path(settings.media_dir) / "audio" / str(project_id)
                music_dir.mkdir(parents=True, exist_ok=True)
                main_music_path = str(music_dir / "music_only_main.mp3")

                try:
                    from app.services.suno_music import generate_suno_music

                    topic_hint = project.title or project.style_prompt or ""
                    generated = await generate_suno_music(main_music_path, target_dur, bgm_mood, topic_hint)
                    if generated and os.path.exists(generated):
                        audio_path = generated
                        logger.info(f"Music-only mode: Suno main audio generated for project {project_id}: {generated}")
                except Exception as e:
                    logger.warning(f"Music-only mode: Suno main audio failed for project {project_id}: {e}")

                if not audio_path or not os.path.exists(audio_path):
                    try:
                        from app.services.script_audio import generate_background_music

                        generated = await asyncio.get_event_loop().run_in_executor(
                            None, generate_background_music, main_music_path, target_dur, bgm_mood
                        )
                        if generated and os.path.exists(generated):
                            audio_path = generated
                            logger.info(f"Music-only mode: FFmpeg fallback main audio generated for project {project_id}: {generated}")
                    except Exception as e:
                        logger.warning(f"Music-only mode: FFmpeg fallback main audio failed for project {project_id}: {e}")

                if audio_path and os.path.exists(audio_path):
                    project.audio_path = audio_path
                    real_dur = get_audio_duration(audio_path)
                    project.track_duration = round(real_dur) if real_dur > 0 else round(target_dur)
                    await db.commit()

            if not audio_path or not os.path.exists(audio_path):
                raise FileNotFoundError(f"Audio file not found: {project.audio_path}")

            audio_basename = os.path.basename(audio_path).lower()
            is_music_only_mode = (
                is_music_only_mode
                or audio_basename.startswith("custom_background_music")
                or audio_basename.startswith("music_only_main")
                or "instrumental_no_vocals" in audio_basename
            )

            # ── Step 0b: Transcribe audio with Whisper for accurate karaoke ──
            transcribed_words = []
            if not is_music_only_mode:
                try:
                    from app.services.transcriber import transcribe_audio
                    lyrics_hint = (project.lyrics_text or "").strip()
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: transcribe_audio(audio_path, prompt=lyrics_hint)
                    )
                    transcribed_words = result.get("words", [])
                    # Use transcribed text if we had no lyrics
                    if not project.lyrics_text and result.get("text"):
                        project.lyrics_text = result["text"]
                        await db.commit()
                    logger.info(f"Whisper transcription: {len(transcribed_words)} words")
                except Exception as e:
                    logger.warning(f"Whisper transcription failed, will use text fallback: {e}")
            else:
                logger.info(f"Music-only mode for project {project_id}: skipping Whisper transcription")

            # ── Early: Start Suno background music generation (runs in parallel with scenes) ──
            suno_music_task = None
            bgm_mood = "inspiracional"
            no_bgm = getattr(project, 'no_background_music', False) or False
            custom_bgm_path = "" if no_bgm else _find_custom_background_music(project_id)
            is_suno_narration = audio_path and "suno_narration" in os.path.basename(audio_path)
            if no_bgm or is_suno_narration:
                logger.info(f"Background music DISABLED for project {project_id}" + (" (Suno narration includes BGM)" if is_suno_narration else ""))
            elif custom_bgm_path:
                logger.info(f"Using custom uploaded background music for project {project_id}: {custom_bgm_path}")
            if not no_bgm and not is_suno_narration and not custom_bgm_path and audio_path and os.path.basename(audio_path) == "narration.mp3":
                try:
                    from app.services.suno_music import generate_suno_music
                    from app.services.video_composer import _get_duration as get_audio_duration

                    music_dir = Path(settings.media_dir) / "audio" / str(project_id)
                    music_dir.mkdir(parents=True, exist_ok=True)
                    music_path = str(music_dir / "background_music.mp3")

                    audio_dur = get_audio_duration(audio_path)
                    if audio_dur > 0:
                        # Detect mood from project data
                        text_hint = f"{project.style_prompt or ''} {project.title or ''}".lower()
                        if any(w in text_hint for w in ["terror", "horror", "misterio", "dark", "suspense"]):
                            bgm_mood = "misterioso"
                        elif any(w in text_hint for w in ["urgente", "alerta", "crise", "perigo"]):
                            bgm_mood = "urgente"
                        elif any(w in text_hint for w in ["motivac", "superac", "força", "poder"]):
                            bgm_mood = "motivacional"
                        elif any(w in text_hint for w in ["reflex", "calma", "paz", "tranquil"]):
                            bgm_mood = "reflexivo"
                        elif any(w in text_hint for w in ["drama", "triste", "emocio"]):
                            bgm_mood = "dramatico"

                        topic_hint = project.title or project.style_prompt or ""
                        suno_music_task = asyncio.create_task(
                            generate_suno_music(music_path, audio_dur, bgm_mood, topic_hint)
                        )
                        logger.info(f"Suno music generation started in background (mood={bgm_mood})")
                except Exception as e:
                    logger.warning(f"Failed to start Suno music task: {e}")

            # ── Step 1: Generate scenes (images) ──
            project.status = VideoStatus.GENERATING_SCENES
            project.progress = 5
            await db.commit()

            style_prompt = project.style_prompt or ""
            is_black_screen = "tela_preta" in style_prompt.lower()
            is_karaoke = getattr(project, "is_karaoke", False) or False
            project_tags = project.tags if isinstance(project.tags, dict) else {}
            force_karaoke_two_line = bool(project_tags.get("force_karaoke_two_line", False))

            # Map style tags to rich English descriptions for better image generation
            _STYLE_DESCRIPTIONS = {
                "cinematic": "cinematic, dramatic lighting, movie-like atmosphere, depth of field",
                "minimalista": "minimalist, clean, simple geometric shapes, subtle colors, elegant",
                "colorido": "colorful, vibrant, bright saturated colors, dynamic composition",
                "dark": "dark, moody, deep shadows, mysterious atmosphere, dramatic contrast",
                "neon": "neon lights, glowing colors, cyberpunk aesthetic, electric atmosphere",
                "vintage": "vintage, retro, film grain, warm nostalgic tones, 1970s",
                "futurista": "futuristic, sci-fi, high-tech, sleek modern design, holographic",
                "natureza": "beautiful nature landscape, lush green forest, mountains, rivers, flowers, golden sunlight, scenic",
                "urbano": "urban cityscape, city skyline at night, street lights, metropolitan architecture",
                "editorial": "editorial, fashion, high contrast, stylish composition, magazine quality",
                "anime": "anime style, Japanese animation, vibrant cel-shaded, detailed illustration",
                "aquarela": "watercolor painting, soft brushstrokes, artistic, pastel colors, dreamy",
                "desenho": "colorful hand-drawn illustration, vibrant sketch art, detailed pencil and watercolor drawing, rich saturated colors, artistic brushstrokes, illustrated storybook style, cartoon illustration with vivid palette",
            }

            def _expand_style_prompt(raw: str) -> str:
                """Expand style tags into rich English descriptions."""
                if not raw:
                    return "cinematic, vibrant colors, dynamic lighting"
                parts = [t.strip().lower() for t in raw.split(",")]
                expanded = []
                for p in parts:
                    expanded.append(_STYLE_DESCRIPTIONS.get(p, p))
                return ", ".join(expanded) if expanded else raw

            if is_karaoke and not use_custom_images:
                # Karaoke mode: generate a single background image for the entire video
                from app.services.scene_generator import generate_scene_image
                img_dir = Path(settings.media_dir) / "images" / str(project_id)
                img_dir.mkdir(parents=True, exist_ok=True)
                output_path = str(img_dir / "scene_000.png")

                visual_prompt = _expand_style_prompt(style_prompt)
                karaoke_prompt = f"{visual_prompt}. Abstract musical background, no text, no people, atmospheric, suitable for karaoke lyrics overlay"

                if is_black_screen:
                    from PIL import Image
                    ar = project.aspect_ratio or "16:9"
                    bw, bh = (1080, 1920) if ar == "9:16" else (1080, 1080) if ar == "1:1" else (1920, 1080)
                    Image.new("RGB", (bw, bh), (0, 0, 0)).save(output_path)
                else:
                    try:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None, generate_scene_image, karaoke_prompt,
                            project.aspect_ratio or "16:9", output_path,
                        )
                    except Exception as e:
                        logger.warning(f"Karaoke image generation failed, using black screen: {e}")
                        from PIL import Image
                        ar = project.aspect_ratio or "16:9"
                        bw, bh = (1080, 1920) if ar == "9:16" else (1080, 1080) if ar == "1:1" else (1920, 1080)
                        Image.new("RGB", (bw, bh), (0, 0, 0)).save(output_path)

                dur = project.track_duration or 180
                scenes = [{
                    "scene_index": 0,
                    "start_time": 0,
                    "end_time": dur,
                    "visual_prompt": karaoke_prompt,
                    "image_path": output_path,
                    "lyrics_segment": "",
                    "is_chorus": False,
                }]
                project.progress = 40
                await db.commit()
                logger.info(f"Karaoke mode: single background image for {dur:.0f}s")

            elif use_custom_images:
                # User uploaded their own photos — use them directly, skip AI generation
                img_dir = Path(settings.media_dir) / "images" / str(project_id)
                user_images = sorted(
                    [str(p) for p in img_dir.glob("user_*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
                )
                if not user_images:
                    raise RuntimeError("Nenhuma foto encontrada. Envie pelo menos uma foto.")

                dur = project.track_duration or 180
                # Distribute images evenly across audio duration, cycling if needed
                if image_display_seconds > 0:
                    per_image = max(image_display_seconds, 1.0)
                    desired_dur = per_image * len(user_images)
                    # Trim audio to match user's desired total duration
                    if dur > desired_dur + 2.0:
                        trimmed_path = str(Path(audio_path).parent / "trimmed_audio.mp3")
                        try:
                            import subprocess
                            trim_cmd = [
                                "ffmpeg", "-y", "-i", audio_path,
                                "-t", f"{desired_dur:.2f}",
                                "-af", f"afade=t=out:st={max(desired_dur - 3, 0):.2f}:d=3",
                                "-c:a", "libmp3lame", "-b:a", "192k", trimmed_path,
                            ]
                            subprocess.run(trim_cmd, capture_output=True, text=True, timeout=120)
                            if os.path.exists(trimmed_path) and os.path.getsize(trimmed_path) > 0:
                                audio_path = trimmed_path
                                project.audio_path = audio_path
                                project.track_duration = round(desired_dur)
                                await db.commit()
                                logger.info(f"Audio trimmed to {desired_dur:.1f}s to match user image_display_seconds")
                        except Exception as e:
                            logger.warning(f"Audio trim failed, using original: {e}")
                    dur = desired_dur
                else:
                    per_image = max(dur / len(user_images), 5.0) if len(user_images) <= 20 else 5.0
                scenes = []
                t = 0.0
                idx = 0
                while t < dur:
                    image_path = user_images[idx % len(user_images)]
                    end_t = min(t + per_image, dur)
                    if dur - end_t < 2.0:
                        end_t = dur
                    scenes.append({
                        "scene_index": len(scenes),
                        "start_time": t,
                        "end_time": end_t,
                        "visual_prompt": "user uploaded photo",
                        "image_path": image_path,
                        "lyrics_segment": "",
                        "is_chorus": False,
                        "is_user_uploaded": True,
                    })
                    t = end_t
                    idx += 1
                project.progress = 40
                await db.commit()
                logger.info(f"Custom images mode: {len(user_images)} photos, {len(scenes)} scene slots, {dur:.0f}s total")

            elif is_black_screen:
                # Black screen mode — no image generation, create a single black frame
                from PIL import Image
                if project.aspect_ratio == "9:16":
                    bw, bh = 1080, 1920
                elif project.aspect_ratio == "1:1":
                    bw, bh = 1080, 1080
                else:
                    bw, bh = 1920, 1080
                black_dir = Path(settings.media_dir) / "images" / str(project_id)
                black_dir.mkdir(parents=True, exist_ok=True)
                black_path = str(black_dir / "black.png")
                Image.new("RGB", (bw, bh), (0, 0, 0)).save(black_path)
                dur = project.track_duration or 180
                scenes = [{"scene_index": 0, "start_time": 0, "end_time": dur,
                           "visual_prompt": "black screen", "image_path": black_path,
                           "lyrics_segment": "", "is_chorus": False}]
                project.progress = 40
                await db.commit()
                logger.info(f"Black screen mode: single black frame for {dur:.0f}s")
            else:
                from app.services.scene_generator import generate_all_scenes

                async def _scene_progress(done, total):
                    # Map scene progress to 5-40% range
                    pct = 5 + int((done / total) * 35)
                    project.progress = min(pct, 40)
                    await db.commit()

                scenes = await generate_all_scenes(
                    project_id=project_id,
                    lyrics_text=project.lyrics_text or "",
                    lyrics_words=project.lyrics_words or [],
                    duration=project.track_duration or 180,
                    aspect_ratio=project.aspect_ratio,
                    style_hint=_expand_style_prompt(style_prompt),
                    user_id=project.user_id,
                    on_progress=_scene_progress,
                )

            # Hard guard: very short AI videos should use a single image scene.
            rendered_duration = float(project.track_duration or 0) or float(get_audio_duration(audio_path) or 0)
            if (
                rendered_duration > 0
                and rendered_duration <= 12.0
                and not use_custom_images
                and not is_black_screen
                and not is_karaoke
                and isinstance(scenes, list)
                and len(scenes) > 1
            ):
                first_scene = scenes[0] if isinstance(scenes[0], dict) else {}
                scenes = [{
                    **first_scene,
                    "scene_index": 0,
                    "start_time": 0,
                    "end_time": rendered_duration,
                }]
                logger.info(
                    f"Short AI video guard active for project {project_id}: duration={rendered_duration:.2f}s, forced 1 scene"
                )

            # Save scenes to DB
            for s in scenes:
                scene = VideoScene(
                    project_id=project_id,
                    scene_index=s.get("scene_index", 0),
                    scene_type="image",
                    prompt=s.get("visual_prompt", ""),
                    image_path=s.get("image_path"),
                    start_time=s.get("start_time", 0),
                    end_time=s.get("end_time", 0),
                    lyrics_segment=s.get("lyrics_segment", ""),
                    is_user_uploaded=s.get("is_user_uploaded", False),
                )
                db.add(scene)

            project.progress = 40
            await db.commit()

            project.progress = 60
            await db.commit()

            # ── Step 2: Generate karaoke subtitles (skip for tela_preta or disabled) ──
            subtitle_path = ""
            enable_subtitles = getattr(project, "enable_subtitles", True)
            if enable_subtitles is None:
                enable_subtitles = True
            # Narration mode: non-karaoke, non-music projects show only current spoken line
            is_narration_subtitle = (not is_karaoke and not is_music_only_mode and not force_karaoke_two_line)
            if not is_black_screen and enable_subtitles:
                from app.services.subtitle_generator import generate_ass_subtitles, generate_ass_from_text

                subtitle_dir = Path(settings.media_dir) / "subtitles" / str(project_id)
                subtitle_dir.mkdir(parents=True, exist_ok=True)
                subtitle_path = str(subtitle_dir / "karaoke.ass")

                if transcribed_words:
                    # Best: Whisper word-level timestamps → accurate karaoke
                    generate_ass_subtitles(
                        lyrics_words=transcribed_words,
                        aspect_ratio=project.aspect_ratio,
                        output_path=subtitle_path,
                        narration_mode=is_narration_subtitle,
                        style_settings=subtitle_settings,
                    )
                elif project.lyrics_words:
                    generate_ass_subtitles(
                        lyrics_words=project.lyrics_words,
                        aspect_ratio=project.aspect_ratio,
                        output_path=subtitle_path,
                        narration_mode=is_narration_subtitle,
                        style_settings=subtitle_settings,
                    )
                elif project.lyrics_text:
                    generate_ass_from_text(
                        lyrics_text=project.lyrics_text,
                        duration=project.track_duration or 180,
                        aspect_ratio=project.aspect_ratio,
                        output_path=subtitle_path,
                        narration_mode=is_narration_subtitle,
                        style_settings=subtitle_settings,
                    )
                else:
                    subtitle_path = ""
            else:
                logger.info(f"Skipping subtitle generation (tela_preta={is_black_screen}, enable_subtitles={enable_subtitles})")

            project.progress = 70
            await db.commit()

            # ── Step 3: Get background music (Suno task started earlier) ──
            background_music_path = "" if is_music_only_mode else (custom_bgm_path or "")
            if is_suno_narration:
                # Suno narration already includes background music — skip all BGM
                background_music_path = ""
                logger.info(f"Suno narration detected — skipping separate BGM for project {project_id}")
            elif suno_music_task is not None:
                try:
                    background_music_path = await suno_music_task
                    logger.info(f"Suno background music result: {background_music_path}")
                except Exception as e:
                    logger.warning(f"Suno music await failed: {e}")

            # Fallback to FFmpeg synthesis if Suno didn't produce music
            if not no_bgm and not custom_bgm_path and not background_music_path and audio_path and os.path.basename(audio_path) == "narration.mp3":
                try:
                    from app.services.script_audio import generate_background_music
                    from app.services.video_composer import _get_duration as get_audio_duration

                    music_dir = Path(settings.media_dir) / "audio" / str(project_id)
                    music_dir.mkdir(parents=True, exist_ok=True)
                    music_path = str(music_dir / "background_music.mp3")

                    audio_dur = get_audio_duration(audio_path)
                    if audio_dur > 0:
                        background_music_path = await asyncio.get_event_loop().run_in_executor(
                            None, generate_background_music, music_path, audio_dur, bgm_mood
                        )
                        logger.info(f"FFmpeg fallback music ready: {background_music_path}")
                except Exception as e:
                    logger.warning(f"FFmpeg fallback music also failed: {e}")

            # ── Step 4: Compose video with FFmpeg ──
            project.status = VideoStatus.RENDERING
            project.progress = 75
            await db.commit()

            if not audio_path or not os.path.exists(audio_path):
                raise FileNotFoundError(f"Audio file not found: {project.audio_path}")

            # Run FFmpeg in thread pool to avoid blocking event loop and DB timeout
            render_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: compose_video(
                    project_id=project_id,
                    scenes=scenes,
                    audio_path=audio_path,
                    subtitle_path=subtitle_path,
                    aspect_ratio=project.aspect_ratio,
                    background_music_path=background_music_path,
                    enable_zoom=zoom_images,
                    enable_audio_spectrum=enable_audio_spectrum,
                ),
            )

            # Refresh DB session after long render to avoid stale connections
            await db.rollback()
            project = await db.get(VideoProject, project_id)

            project.progress = 90
            await db.commit()

            # ── Step 5: Generate thumbnail (skip if user uploaded custom) ──
            thumb_dir = Path(settings.media_dir) / "thumbnails" / str(project_id)
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = str(thumb_dir / "thumbnail.jpg")

            custom_thumb = next(thumb_dir.glob("custom_thumbnail.*"), None)
            if custom_thumb:
                shutil.copy2(str(custom_thumb), thumb_path)
                logger.info(f"Using custom thumbnail for project {project_id}")
            else:
                from app.services.thumbnail_generator import generate_thumbnail
                try:
                    generate_thumbnail(
                        title=project.track_title or project.title,
                        artist=project.track_artist or "",
                        output_path=thumb_path,
                    )
                except Exception as e:
                    logger.warning(f"Thumbnail generation failed, using frame fallback: {e}")
                    from app.services.thumbnail_generator import generate_thumbnail_from_frame
                    generate_thumbnail_from_frame(
                        video_path=render_result["file_path"],
                        title=project.track_title or project.title,
                        artist=project.track_artist or "",
                        output_path=thumb_path,
                    )

            # ── Save render to DB ──
            render = VideoRender(
                project_id=project_id,
                format=project.aspect_ratio,
                file_path=render_result["file_path"],
                file_size=render_result["file_size"],
                thumbnail_path=thumb_path,
                duration=render_result["duration"],
            )
            db.add(render)

            project.status = VideoStatus.COMPLETED
            project.progress = 100
            await db.commit()

            logger.info(f"Video pipeline complete for project {project_id}")

        except Exception as e:
            logger.error(f"Video pipeline failed for project {project_id}: {e}", exc_info=True)
            project = await db.get(VideoProject, project_id)
            if project:
                project.status = VideoStatus.FAILED
                project.error_message = _safe_error_message(
                    e,
                    "Falha ao processar o vídeo",
                )[:1000]
                await db.commit()


async def run_video_format_copy_pipeline(project_id: int, source_video_path: str):
    """Create a new project render by reformatting an existing rendered video."""
    async with async_session() as db:
        project = None
        try:
            project = await db.get(VideoProject, project_id)
            if not project:
                return

            project.status = VideoStatus.RENDERING
            project.progress = 20
            project.error_message = None
            await db.commit()

            from app.services.video_composer import reformat_video

            render_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: reformat_video(
                    project_id=project_id,
                    source_video_path=source_video_path,
                    aspect_ratio=project.aspect_ratio,
                ),
            )

            await db.rollback()
            project = await db.get(VideoProject, project_id)
            if not project:
                return

            project.progress = 90
            await db.commit()

            from app.services.thumbnail_generator import generate_thumbnail_from_frame

            thumb_dir = Path(settings.media_dir) / "thumbnails" / str(project_id)
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = str(thumb_dir / "thumbnail.jpg")

            generate_thumbnail_from_frame(
                video_path=render_result["file_path"],
                title=project.track_title or project.title,
                artist=project.track_artist or "",
                output_path=thumb_path,
            )

            render = VideoRender(
                project_id=project_id,
                format=project.aspect_ratio,
                file_path=render_result["file_path"],
                file_size=render_result["file_size"],
                thumbnail_path=thumb_path,
                duration=render_result["duration"],
            )
            db.add(render)

            project.status = VideoStatus.COMPLETED
            project.progress = 100
            await db.commit()

            logger.info(f"Format-copy pipeline complete for project {project_id}")

        except Exception as e:
            logger.error(f"Format-copy pipeline failed for project {project_id}: {e}", exc_info=True)
            if project is None:
                project = await db.get(VideoProject, project_id)
            if project:
                project.status = VideoStatus.FAILED
                project.error_message = _safe_error_message(
                    e,
                    "Falha ao copiar o formato do vídeo",
                )[:1000]
                await db.commit()


async def _combine_realistic_audio(
    video_path: str,
    narration_path: str,
    music_path: str,
    output_path: str,
    video_duration: float,
):
    """Combine narration and/or background music with a silent video using FFmpeg."""
    import subprocess

    inputs = ["-i", video_path]
    filter_parts = []
    audio_index = 1

    if narration_path and os.path.exists(narration_path):
        inputs.extend(["-i", narration_path])
        filter_parts.append(f"[{audio_index}:a]aresample=44100,volume=1.0[narr]")
        audio_index += 1

    if music_path and os.path.exists(music_path):
        inputs.extend(["-stream_loop", "-1", "-i", music_path])
        # Music volume: lower if narration is present
        music_vol = "0.15" if narration_path and os.path.exists(narration_path) else "0.5"
        fade_start = max(0, video_duration - 3)
        filter_parts.append(
            f"[{audio_index}:a]aresample=44100,volume={music_vol},"
            f"afade=t=out:st={fade_start}:d=3[music]"
        )
        audio_index += 1

    if not filter_parts:
        return  # Nothing to combine

    # Build the amix filter
    has_narr = narration_path and os.path.exists(narration_path)
    has_music = music_path and os.path.exists(music_path)

    if has_narr and has_music:
        filter_complex = ";".join(filter_parts) + ";[narr][music]amix=inputs=2:duration=first:normalize=0[aout]"
    elif has_narr:
        filter_complex = filter_parts[0].replace("[narr]", "[aout]")
    else:
        filter_complex = filter_parts[0].replace("[music]", "[aout]")

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "0:v:0",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-t", str(video_duration),
        "-shortest",
        output_path,
    ]

    logger.info(f"Combining audio with video: {' '.join(cmd[:10])}...")
    proc = await asyncio.get_event_loop().run_in_executor(
        None,
        lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=120),
    )
    if proc.returncode != 0:
        logger.error(f"FFmpeg combine failed: {proc.stderr[:500]}")
        raise RuntimeError(f"FFmpeg audio merge failed: {proc.stderr[:200]}")


def _resolve_wan_effective_duration(duration: int) -> int:
    """Normalize WAN duration to 8-second blocks (8..56)."""
    raw = max(1, int(duration or 0))
    if raw <= 8:
        return 8
    capped = min(raw, 56)
    return max(8, (capped // 8) * 8)


async def _extract_audio_from_video_track(video_path: str, audio_output_path: str) -> str:
    """Extract first audio stream from a video file into an AAC container."""
    if not video_path or not os.path.exists(video_path):
        raise RuntimeError("Video de origem para extracao de audio nao encontrado")

    audio_dir = os.path.dirname(audio_output_path)
    if audio_dir:
        os.makedirs(audio_dir, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-map",
        "0:a:0",
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        audio_output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        details = (stderr or b"").decode(errors="ignore")[-600:]
        raise RuntimeError(f"Falha ao extrair audio do Grok: {details}")

    if not os.path.exists(audio_output_path) or os.path.getsize(audio_output_path) <= 0:
        raise RuntimeError("Audio extraido do Grok ficou vazio")

    return audio_output_path


async def _concatenate_audio_tracks(audio_paths: list[str], output_path: str) -> str:
    """Concatenate multiple audio tracks into one AAC file."""
    valid_paths = [p for p in (audio_paths or []) if p and os.path.exists(p)]
    if not valid_paths:
        raise RuntimeError("Nenhum segmento de audio foi gerado para concatenacao")

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    if len(valid_paths) == 1:
        shutil.copy2(valid_paths[0], output_path)
        return output_path

    list_path = output_path + ".txt"
    try:
        with open(list_path, "w", encoding="utf-8") as handle:
            for path in valid_paths:
                normalized = path.replace("\\", "/").replace("'", "'\\''")
                handle.write(f"file '{normalized}'\\n")

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_path,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            output_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            details = (stderr or b"").decode(errors="ignore")[-600:]
            raise RuntimeError(f"Falha ao concatenar audio do Grok: {details}")
    finally:
        try:
            if os.path.exists(list_path):
                os.remove(list_path)
        except Exception:
            pass

    if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        raise RuntimeError("Audio final concatenado do Grok ficou vazio")

    return output_path


async def run_realistic_video_pipeline(project_id: int):
    """Pipeline for realistic video generation via the configured realistic engine.
    Much simpler than the standard pipeline: optimize prompt → generate → thumbnail → done.
    """
    async with async_session() as db:
        try:
            project = await db.get(VideoProject, project_id)
            if not project:
                return

            from app.services.seedance_video import optimize_prompt_for_seedance, generate_realistic_video, sanitize_prompt_for_retry
            from app.services.video_composer import _get_duration as get_duration

            # Determine engine from audio_path field (used to store engine choice)
            engine = (project.audio_path or "").strip()
            if engine not in ("seedance", "minimax", "wan2", "grok"):
                engine = "wan2"
            engine_labels = {"minimax": "MiniMax Hailuo", "wan2": "Wan 2.7", "seedance": "Seedance 2.0", "grok": "Cria 3.0 speed"}
            engine_label = engine_labels.get(engine, "Wan 2.7")
            logger.info(f"Realistic video pipeline for project {project_id} using engine: {engine}")

            # ── Step 1: Optimize prompt via GPT ──
            project.status = VideoStatus.GENERATING_SCENES
            project.progress = 5
            await db.commit()

            user_prompt = (project.lyrics_text or "").strip()
            if not user_prompt:
                raise ValueError("Nenhuma descricao fornecida para o video realista.")

            transcribed_text_for_subtitles = ""
            transcribed_words_for_subtitles = []

            # ── Step 0.5: Transcribe audio clip for context-aware prompt ──
            tags_data_early = project.tags if isinstance(project.tags, dict) else {}
            external_audio_url_early = (tags_data_early.get("audio_url") or "").strip()
            segment_transcription_hint = str(tags_data_early.get("segment_transcription", "") or "").strip()
            interaction_persona = _normalize_interaction_persona(tags_data_early.get("interaction_persona", ""))
            reference_source_early = str(tags_data_early.get("reference_source", "") or "").strip().lower()
            reference_mode = str(tags_data_early.get("reference_mode", "") or "").strip().lower()
            if reference_source_early == "persona" and not reference_mode:
                reference_mode = "face_identity_only"
            clip_start_early = float(tags_data_early.get("clip_start", 0))
            clip_dur_early = float(tags_data_early.get("clip_duration", 0))
            if external_audio_url_early:
                try:
                    audio_dir_early = Path(settings.media_dir) / "audio" / str(project_id)
                    audio_dir_early.mkdir(parents=True, exist_ok=True)
                    ext_audio = await download_audio_if_url(external_audio_url_early, project_id)
                    if ext_audio and os.path.exists(ext_audio):
                        # Extract the clip segment for transcription
                        clip_path = str(audio_dir_early / "clip_for_transcription.mp3")
                        trim_args = ["ffmpeg", "-y", "-i", ext_audio]
                        if clip_start_early > 0:
                            trim_args += ["-ss", str(clip_start_early)]
                        if clip_dur_early > 0:
                            trim_args += ["-t", str(clip_dur_early)]
                        trim_args += ["-c:a", "libmp3lame", "-q:a", "2", clip_path]
                        proc = await asyncio.create_subprocess_exec(
                            *trim_args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                        )
                        await proc.wait()
                        if os.path.exists(clip_path) and os.path.getsize(clip_path) > 0:
                            from app.services.transcriber import transcribe_audio
                            lyrics_hint = tags_data_early.get("lyrics", "")
                            result = await asyncio.get_event_loop().run_in_executor(
                                None, lambda: transcribe_audio(clip_path, prompt=lyrics_hint)
                            )
                            transcribed_text = (result.get("text", "") or "").strip()
                            words_raw = result.get("words", []) if isinstance(result, dict) else []
                            if isinstance(words_raw, list):
                                transcribed_words_for_subtitles = [w for w in words_raw if isinstance(w, dict) and w.get("word")]
                            if transcribed_text:
                                transcribed_text_for_subtitles = transcribed_text

                                # Persist transcript for subtitle fallback/inspection.
                                if getattr(project, "enable_subtitles", False):
                                    project.lyrics_text = transcribed_text
                                    if transcribed_words_for_subtitles:
                                        project.lyrics_words = transcribed_words_for_subtitles
                                    await db.commit()

                            if transcribed_text:
                                user_prompt = _build_transcribed_realistic_prompt(transcribed_text, interaction_persona)
                                logger.info(f"Realistic video: transcribed clip text ({len(transcribed_text)} chars): {transcribed_text[:200]}")
                            elif segment_transcription_hint:
                                user_prompt = _build_transcribed_realistic_prompt(segment_transcription_hint, interaction_persona)
                                logger.info("Realistic video: using stored segment transcription fallback")
                            elif lyrics_hint:
                                user_prompt = _build_transcribed_realistic_prompt(lyrics_hint, interaction_persona)
                                logger.info("Realistic video: transcription empty, using lyrics hint for prompt")
                            else:
                                user_prompt = _build_transcribed_realistic_prompt("", interaction_persona)
                                logger.info("Realistic video: no transcription available, using generic clip prompt")
                        else:
                            if segment_transcription_hint:
                                user_prompt = _build_transcribed_realistic_prompt(segment_transcription_hint, interaction_persona)
                            elif tags_data_early.get("lyrics"):
                                user_prompt = _build_transcribed_realistic_prompt(str(tags_data_early.get("lyrics", "")), interaction_persona)
                            else:
                                user_prompt = _build_transcribed_realistic_prompt("", interaction_persona)
                            logger.warning("Realistic video: clip extraction failed, using lyric-based fallback prompt")
                except Exception as e:
                    if segment_transcription_hint:
                        user_prompt = _build_transcribed_realistic_prompt(segment_transcription_hint, interaction_persona)
                    elif tags_data_early.get("lyrics"):
                        user_prompt = _build_transcribed_realistic_prompt(str(tags_data_early.get("lyrics", "")), interaction_persona)
                    else:
                        user_prompt = _build_transcribed_realistic_prompt("", interaction_persona)
                    logger.warning(f"Realistic video: clip transcription failed: {e}, using lyric-based fallback prompt")
            elif segment_transcription_hint:
                user_prompt = _build_transcribed_realistic_prompt(segment_transcription_hint, interaction_persona)
                logger.info("Realistic video: no external audio URL, using stored segment transcription")

            user_prompt = _inject_interaction_persona_instruction(user_prompt, interaction_persona)

            # Check for reference image (stored in style_prompt as file path)
            image_path = None
            if project.style_prompt and os.path.exists(project.style_prompt):
                image_path = project.style_prompt
                logger.info(f"Realistic video with reference image: {image_path}")
            elif project.style_prompt:
                logger.warning("Realistic video reference image path missing on disk: %s", project.style_prompt)
            has_reference_image = bool(image_path)

            if engine == "grok" and reference_source_early == "persona" and not has_reference_image:
                raise RuntimeError(
                    "Imagem de referencia da persona nao encontrada. Reabra as personas selecionadas e gere novamente."
                )

            if has_reference_image:
                user_prompt = _ensure_reference_image_instruction(user_prompt, reference_mode=reference_mode)
                if engine == "grok":
                    user_prompt = _ensure_grok_identity_lock(user_prompt, reference_mode=reference_mode)
                logger.info("Realistic video: reference-image rule injected into prompt")

            duration = int(project.track_duration or 7)
            if engine == "grok":
                duration = max(1, min(duration, 60))
            elif engine == "wan2":
                duration = _resolve_wan_effective_duration(duration)
            else:
                duration = max(1, min(duration, 10))

            # Only optimize prompt if it wasn't already optimized by the AI suggest
            tags_data = project.tags if isinstance(project.tags, dict) else {}
            prompt_optimized = tags_data.get("prompt_optimized", False)
            realistic_style = tags_data.get("realistic_style", "")
            add_music = bool(tags_data.get("add_music", False))
            provider_generate_audio_requested = bool(tags_data.get("provider_generate_audio", False))
            seedance_native_audio_only = bool(tags_data.get("seedance_native_audio_only", False))
            external_audio_url_for_prompt = str(tags_data.get("audio_url", "") or "").strip()
            grok_text_only = engine == "grok" and bool(
                tags_data.get("grok_text_only") or tags_data.get("disable_persona_reference")
            )
            dialogue_enabled = bool(tags_data.get("dialogue_enabled", False))
            dialogue_characters = tags_data.get("dialogue_characters", []) if isinstance(tags_data.get("dialogue_characters", []), list) else []
            dialogue_voice_profile_ids = tags_data.get("dialogue_voice_profile_ids", []) if isinstance(tags_data.get("dialogue_voice_profile_ids", []), list) else []
            dialogue_tone = str(tags_data.get("dialogue_tone", "informativo") or "informativo").strip() or "informativo"
            dialogue_duration = float(tags_data.get("dialogue_duration", 0) or 0)
            # Wan 2.7 now runs video-only (no legacy Grok shadow audio).
            shadow_audio_from_grok = False
            shadow_grok_retry_limit = max(0, int(tags_data.get("wan_shadow_grok_retry_limit", 2) or 2))
            wan_effective_duration = _resolve_wan_effective_duration(duration) if engine == "wan2" else duration
            grok_shadow_prompt = ""
            grok_shadow_video_path = ""
            grok_shadow_audio_path = ""
            grok_shadow_duration = 0.0

            if engine == "wan2":
                tags_data["wan_shadow_grok_audio"] = False
                tags_data["wan_effective_duration"] = wan_effective_duration
                project.tags = tags_data
                await db.commit()

            prebuilt_dialogue_result = None
            prebuilt_narration_path = ""
            prebuilt_music_path = ""
            seedance_expect_native_audio = (
                engine == "seedance"
                and provider_generate_audio_requested
                and seedance_native_audio_only
                and not external_audio_url_for_prompt
                and not dialogue_enabled
            )

            if dialogue_enabled and not shadow_audio_from_grok:
                project.progress = 7
                await db.commit()
                try:
                    from app.services.dialogue_audio import generate_dialogue_audio_bundle

                    target_dialogue_duration = dialogue_duration if dialogue_duration > 0 else float(duration)
                    prebuilt_dialogue_result = await generate_dialogue_audio_bundle(
                        db=db,
                        user_id=int(project.user_id),
                        project_id=project_id,
                        prompt_text=user_prompt,
                        target_duration=target_dialogue_duration,
                        characters=dialogue_characters,
                        voice_profile_ids=dialogue_voice_profile_ids,
                        tone=dialogue_tone,
                        interaction_persona=interaction_persona,
                        realistic_style=realistic_style,
                        add_music=add_music,
                    )

                    prebuilt_narration_path = (prebuilt_dialogue_result.get("audio_path") or "").strip()
                    prebuilt_music_path = (prebuilt_dialogue_result.get("music_path") or "").strip() if add_music else ""
                    dialogue_script = (prebuilt_dialogue_result.get("script") or "").strip()
                    dialogue_turns = prebuilt_dialogue_result.get("turns") or []

                    if dialogue_script:
                        tags_data["dialogue_script"] = dialogue_script
                        if not transcribed_text_for_subtitles:
                            transcribed_text_for_subtitles = dialogue_script
                    if isinstance(dialogue_turns, list) and dialogue_turns:
                        tags_data["dialogue_turns"] = dialogue_turns
                        dialogue_lock = _build_dialogue_visual_lock(
                            dialogue_turns=dialogue_turns,
                            interaction_persona=interaction_persona,
                            target_duration=target_dialogue_duration,
                        )
                        if dialogue_lock:
                            user_prompt = f"{user_prompt}\n\n{dialogue_lock}"

                    project.tags = tags_data
                    await db.commit()
                    logger.info("Dialogue pre-generated before realistic render for project %s", project_id)
                except Exception as e:
                    logger.warning(f"Dialogue pre-generation failed for project {project_id}: {e}")
                    dialogue_enabled = False
                    tags_data["dialogue_enabled"] = False
                    project.tags = tags_data
                    await db.commit()

            if seedance_expect_native_audio:
                user_prompt = _ensure_seedance_audio_instruction(user_prompt)

            # If user selected a style, prepend it as context for the optimizer
            if realistic_style and not prompt_optimized:
                style_labels = {
                    "cinematic": "estilo cinematografico epico",
                    "commercial": "estilo comercial/produto premium",
                    "meme": "estilo meme viral engracado",
                    "anime": "estilo anime japones",
                    "drama": "estilo drama emotivo",
                    "vfx": "estilo efeitos visuais/surrealista",
                }
                style_hint = style_labels.get(realistic_style, realistic_style)
                user_prompt = f"{user_prompt}. Estilo: {style_hint}"

            if prompt_optimized:
                optimized_prompt = user_prompt
                logger.info(f"Realistic prompt already optimized, using as-is: {optimized_prompt[:200]}...")
            elif dialogue_enabled and engine in ("wan2", "minimax"):
                # Keep explicit dialogue timeline cues for Wan/MiniMax instead of Seedance-style rewrite.
                optimized_prompt = user_prompt
                logger.info("Dialogue mode active: using direct prompt for %s to preserve sync timeline", engine)
            elif engine == "grok":
                from app.services.grok_video import optimize_prompt_for_grok
                optimized_prompt = await optimize_prompt_for_grok(
                    user_description=user_prompt,
                    duration=duration,
                    has_reference_image=has_reference_image,
                    tone=realistic_style,
                    reference_mode=reference_mode,
                )
                logger.info(f"Grok prompt optimized: {optimized_prompt[:200]}...")
            else:
                seedance_optimizer_temperature = 0.2 if engine == "wan2" else None
                optimized_prompt = await optimize_prompt_for_seedance(
                    user_description=user_prompt,
                    duration=duration,
                    has_reference_image=has_reference_image,
                    temperature=seedance_optimizer_temperature,
                )
                logger.info(f"Seedance prompt optimized: {optimized_prompt[:200]}...")

            if has_reference_image:
                optimized_prompt = _ensure_reference_image_instruction(optimized_prompt, reference_mode=reference_mode)
                if engine == "grok":
                    optimized_prompt = _ensure_grok_identity_lock(optimized_prompt, reference_mode=reference_mode)
            if seedance_expect_native_audio:
                optimized_prompt = _ensure_seedance_audio_instruction(optimized_prompt)

            if shadow_audio_from_grok:
                try:
                    from app.services.grok_video import optimize_prompt_for_grok

                    grok_shadow_prompt = await optimize_prompt_for_grok(
                        user_description=user_prompt,
                        duration=wan_effective_duration,
                        has_reference_image=has_reference_image,
                        tone=realistic_style,
                        reference_mode=reference_mode,
                    )
                except Exception as e:
                    logger.warning("Grok shadow prompt optimization failed, using base prompt: %s", e)
                    grok_shadow_prompt = user_prompt

                if has_reference_image:
                    grok_shadow_prompt = _ensure_reference_image_instruction(grok_shadow_prompt, reference_mode=reference_mode)
                    grok_shadow_prompt = _ensure_grok_identity_lock(grok_shadow_prompt, reference_mode=reference_mode)

            project.progress = 10
            await db.commit()

            # ── Step 2: Generate video ──
            project.status = VideoStatus.RENDERING
            project.progress = 15
            await db.commit()

            render_dir = Path(settings.media_dir) / "renders" / str(project_id)
            render_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(render_dir / "realistic_video.mp4")
            if shadow_audio_from_grok:
                output_path = str(render_dir / "realistic_video_wan.mp4")
                grok_shadow_video_path = str(render_dir / "realistic_video_grok_shadow.mp4")

            aspect_ratio = project.aspect_ratio or "16:9"
            generate_audio = bool(tags_data.get("provider_generate_audio", not getattr(project, "no_background_music", False)))
            if engine == "seedance" and not generate_audio:
                generate_audio = True
            scene_reference_path = image_path
            grok_direct_reference_path = image_path if (image_path and os.path.exists(image_path)) else ""

            reference_source = str(tags_data.get("reference_source", "") or "").strip().lower()
            raw_persona_ids = tags_data.get("persona_profile_ids", []) if isinstance(tags_data.get("persona_profile_ids", []), list) else []
            has_persona_selection = False
            for raw_pid in raw_persona_ids:
                try:
                    if int(raw_pid) > 0:
                        has_persona_selection = True
                        break
                except Exception:
                    continue
            enable_grok_persona_anchor = (
                engine == "grok"
                and reference_source == "persona"
                and has_reference_image
                and has_persona_selection
            )
            grok_persona_anchor_path = ""
            grok_persona_anchor_provider = ""
            grok_persona_anchor_retry_count = 0
            grok_persona_anchor_fallback = False

            async def _on_progress(pct, msg):
                nonlocal project
                try:
                    project.progress = pct
                    await db.commit()
                except Exception:
                    pass

            # Build a scene-locked reference frame with Nano Banana for engines other than Grok.
            # Grok max-fidelity mode uses the original persona image directly.
            if has_reference_image and image_path and engine != "grok":
                from app.services.scene_generator import build_single_scene_anchor_prompt, generate_scene_image

                try:
                    await _on_progress(16, "Montando cena base com Nano Banana...")
                    scene_ref_dir = render_dir / "scene_ref"
                    scene_ref_dir.mkdir(parents=True, exist_ok=True)
                    scene_reference_path = str(scene_ref_dir / "persona_scene.png")
                    nano_source_prompt = (optimized_prompt or user_prompt or "").strip()
                    nano_prompt = await build_single_scene_anchor_prompt(
                        source_prompt=nano_source_prompt,
                        duration_seconds=duration,
                    )
                    nano_prompt = (nano_prompt or nano_source_prompt)[:1400]
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None,
                        generate_scene_image,
                        nano_prompt,
                        aspect_ratio,
                        scene_reference_path,
                        True,
                        image_path,
                        "",
                        None,
                        reference_mode,
                    )
                    if os.path.exists(scene_reference_path):
                        logger.info(
                            "Realistic video: Nano Banana scene anchor created from persona image (%s) [source=%s chars, anchor=%s chars]",
                            scene_reference_path,
                            len(nano_source_prompt),
                            len(nano_prompt),
                        )
                    else:
                        scene_reference_path = image_path
                        logger.warning("Realistic video: Nano Banana scene anchor missing; falling back to original persona image")
                except Exception as e:
                    scene_reference_path = image_path
                    logger.warning("Realistic video: Nano Banana scene anchor failed; using original persona image: %s", e)
            elif enable_grok_persona_anchor:
                from app.services.scene_generator import generate_scene_image

                anchor_source_path = grok_direct_reference_path or scene_reference_path or image_path
                if not anchor_source_path or not os.path.exists(anchor_source_path):
                    grok_persona_anchor_fallback = True
                    logger.warning(
                        "Grok persona anchor skipped: no valid source image for project %s",
                        project_id,
                    )
                else:
                    await _on_progress(16, "Montando imagem-base com personas para o Cria 3.0...")
                    anchor_dir = render_dir / "grok_persona_anchor"
                    anchor_dir.mkdir(parents=True, exist_ok=True)
                    candidate_anchor_path = str(anchor_dir / "reference.png")
                    anchor_prompt = (optimized_prompt or user_prompt)[:800]
                    max_attempts = 2  # 1 retry before falling back
                    loop = asyncio.get_event_loop()

                    for attempt in range(1, max_attempts + 1):
                        try:
                            if os.path.exists(candidate_anchor_path):
                                os.remove(candidate_anchor_path)

                            anchor_meta: dict = {}
                            await loop.run_in_executor(
                                None,
                                generate_scene_image,
                                anchor_prompt,
                                aspect_ratio,
                                candidate_anchor_path,
                                True,
                                anchor_source_path,
                                "openai",
                                anchor_meta,
                                reference_mode,
                            )

                            if os.path.exists(candidate_anchor_path) and os.path.getsize(candidate_anchor_path) > 0:
                                grok_persona_anchor_path = candidate_anchor_path
                                grok_persona_anchor_provider = str(anchor_meta.get("provider") or "unknown")
                                grok_persona_anchor_retry_count = attempt - 1
                                grok_persona_anchor_fallback = False
                                grok_direct_reference_path = candidate_anchor_path
                                scene_reference_path = candidate_anchor_path
                                logger.info(
                                    "Grok persona anchor ready for project %s (provider=%s retries=%s path=%s)",
                                    project_id,
                                    grok_persona_anchor_provider,
                                    grok_persona_anchor_retry_count,
                                    candidate_anchor_path,
                                )
                                break

                            raise RuntimeError("Imagem-base gerada vazia")
                        except Exception as e:
                            grok_persona_anchor_retry_count = attempt
                            if attempt >= max_attempts:
                                grok_persona_anchor_fallback = True
                                logger.warning(
                                    "Grok persona anchor failed after %s attempts for project %s: %s",
                                    max_attempts,
                                    project_id,
                                    e,
                                )
                                break

                            logger.warning(
                                "Grok persona anchor attempt %s/%s failed for project %s: %s",
                                attempt,
                                max_attempts,
                                project_id,
                                e,
                            )
                            await _on_progress(17, f"Retentando imagem-base de personas ({attempt + 1}/{max_attempts})...")

                    if grok_persona_anchor_fallback:
                        logger.warning(
                            "Grok persona anchor fallback enabled for project %s; using original reference image.",
                            project_id,
                        )
            elif engine == "grok" and grok_direct_reference_path:
                logger.info(
                    "Realistic video: Grok max-fidelity enabled, using direct persona reference image (%s)",
                    grok_direct_reference_path,
                )

            if engine == "grok":
                grok_base_image_path = grok_direct_reference_path or scene_reference_path
                if duration > 15:
                    # — Grok multi-clip: chain multiple 15s clips for longer videos —
                    from app.services.multi_clip import generate_multi_clip_video
                    logger.info(f"Grok multi-clip mode: {duration}s total")
                    output_path = str(render_dir / "realistic_video.mp4")
                    await generate_multi_clip_video(
                        project_id=project_id,
                        optimized_prompt=optimized_prompt,
                        total_duration=duration,
                        aspect_ratio=aspect_ratio,
                        image_path=grok_base_image_path,
                        render_dir=render_dir,
                        reuse_base_reference_for_all_clips=bool(grok_persona_anchor_path),
                        on_progress=_on_progress,
                        reference_mode=reference_mode,
                    )
                else:
                    # — Grok single clip (<=15s): generate image first, then video —
                    from app.services.grok_video import generate_video_clip, generate_video_from_prompt
                    from app.services.scene_generator import generate_scene_image

                    if grok_text_only:
                        await _on_progress(18, "Iniciando geracao Grok apenas por prompt...")
                        await generate_video_from_prompt(
                            prompt=optimized_prompt,
                            output_path=output_path,
                            duration=duration,
                            aspect_ratio=aspect_ratio,
                            on_progress=_on_progress,
                        )
                    else:
                        await _on_progress(16, "Gerando imagem de referencia...")
                        grok_image_path = grok_base_image_path
                        if not grok_image_path:
                            img_dir = render_dir / "grok_ref"
                            img_dir.mkdir(parents=True, exist_ok=True)
                            grok_image_path = str(img_dir / "reference.png")
                            img_prompt = optimized_prompt[:500]
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(
                                None, generate_scene_image, img_prompt, aspect_ratio, grok_image_path, True
                            )
                            logger.info(f"Grok reference image generated: {grok_image_path}")
                        else:
                            logger.info(f"Grok using direct reference image: {grok_image_path}")

                        await _on_progress(18, "Iniciando geracao de video Grok...")
                        await generate_video_clip(
                            image_path=grok_image_path,
                            prompt=optimized_prompt,
                            output_path=output_path,
                            duration=duration,
                            aspect_ratio=aspect_ratio,
                            on_progress=_on_progress,
                            reference_mode=reference_mode,
                        )

                if enable_grok_persona_anchor:
                    tags_data["grok_persona_anchor_enabled"] = True
                    tags_data["grok_persona_anchor_path"] = grok_persona_anchor_path
                    tags_data["grok_persona_anchor_provider"] = grok_persona_anchor_provider
                    tags_data["grok_persona_anchor_retry_count"] = grok_persona_anchor_retry_count
                    tags_data["grok_persona_anchor_fallback"] = bool(grok_persona_anchor_fallback or not grok_persona_anchor_path)
                    project.tags = tags_data
                    await db.commit()

            elif engine == "minimax":
                # ── MiniMax Hailuo ──
                from app.services.minimax_video import generate_minimax_video
                await generate_minimax_video(
                    prompt=optimized_prompt,
                    duration=duration,
                    aspect_ratio=aspect_ratio,
                    output_path=output_path,
                    image_path=scene_reference_path,
                    on_progress=_on_progress,
                )
            elif engine == "wan2":
                # ── Wan 2.7 via Atlas Cloud ──
                from app.services.runpod_video import generate_wan_video
                from app.services.multi_clip import concatenate_clips, extract_last_frame

                wan_segment_duration = 8
                wan_segment_count = max(1, -(-wan_effective_duration // wan_segment_duration))

                async def _generate_wan_sequence() -> None:
                    nonlocal output_path
                    if wan_segment_count <= 1:
                        await generate_wan_video(
                            prompt=optimized_prompt,
                            duration=wan_segment_duration,
                            aspect_ratio=aspect_ratio,
                            output_path=output_path,
                            image_path=scene_reference_path,
                            on_progress=_on_progress,
                        )
                        return

                    local_ref = scene_reference_path
                    wan_clip_paths: list[str] = []
                    for idx in range(wan_segment_count):
                        clip_path = str(render_dir / f"realistic_video_wan_clip_{idx:02d}.mp4")
                        await generate_wan_video(
                            prompt=optimized_prompt,
                            duration=wan_segment_duration,
                            aspect_ratio=aspect_ratio,
                            output_path=clip_path,
                            image_path=local_ref,
                            on_progress=None,
                        )

                        if not os.path.exists(clip_path) or os.path.getsize(clip_path) <= 0:
                            raise RuntimeError(f"Wan clip {idx + 1}/{wan_segment_count} ficou vazio")

                        wan_clip_paths.append(clip_path)

                        if idx < wan_segment_count - 1:
                            next_ref = str(render_dir / f"realistic_video_wan_ref_{idx:02d}.png")
                            await extract_last_frame(clip_path, next_ref)
                            local_ref = next_ref

                        await _on_progress(18 + int(52 * (idx + 1) / wan_segment_count), f"Gerando WAN clip {idx + 1}/{wan_segment_count}...")

                    await concatenate_clips(wan_clip_paths, output_path)

                if shadow_audio_from_grok:
                    from app.services.grok_video import generate_video_clip
                    from app.services.scene_generator import generate_scene_image

                    await _on_progress(18, "Gerando WAN 2.7 e Grok em paralelo...")
                    grok_image_path = grok_direct_reference_path or scene_reference_path

                    if not grok_image_path:
                        grok_ref_dir = render_dir / "grok_shadow_ref"
                        grok_ref_dir.mkdir(parents=True, exist_ok=True)
                        grok_image_path = str(grok_ref_dir / "reference.png")
                        grok_img_prompt = (grok_shadow_prompt or optimized_prompt)[:500]
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(
                            None,
                            generate_scene_image,
                            grok_img_prompt,
                            aspect_ratio,
                            grok_image_path,
                            True,
                        )

                    if not grok_image_path or not os.path.exists(grok_image_path):
                        raise RuntimeError("Nao foi possivel preparar imagem de referencia para o Grok auxiliar")

                    async def _generate_grok_shadow_clip_with_retry(
                        reference_image_path: str,
                        clip_video_path: str,
                        clip_audio_path: str,
                        clip_index: int,
                        total_clips: int,
                    ) -> None:
                        last_error = None
                        max_attempts = shadow_grok_retry_limit + 1
                        for attempt in range(max_attempts):
                            try:
                                if os.path.exists(clip_video_path):
                                    os.remove(clip_video_path)
                                if os.path.exists(clip_audio_path):
                                    os.remove(clip_audio_path)

                                await generate_video_clip(
                                    image_path=reference_image_path,
                                    prompt=grok_shadow_prompt or optimized_prompt,
                                    output_path=clip_video_path,
                                    duration=wan_segment_duration,
                                    aspect_ratio=aspect_ratio,
                                    on_progress=None,
                                    reference_mode=reference_mode,
                                )

                                await _extract_audio_from_video_track(clip_video_path, clip_audio_path)

                                if (
                                    os.path.exists(clip_video_path)
                                    and os.path.getsize(clip_video_path) > 0
                                    and os.path.exists(clip_audio_path)
                                    and os.path.getsize(clip_audio_path) > 0
                                ):
                                    return
                                raise RuntimeError("Grok auxiliar retornou arquivo vazio")
                            except Exception as exc:
                                last_error = exc
                                if attempt >= max_attempts - 1:
                                    break
                                wait_seconds = min(15, 4 * (attempt + 1))
                                logger.warning(
                                    "Grok auxiliar falhou no projeto %s (clip %s/%s, tentativa %s/%s): %s",
                                    project_id,
                                    clip_index,
                                    total_clips,
                                    attempt + 1,
                                    max_attempts,
                                    exc,
                                )
                                await _on_progress(22, f"Retentando Grok auxiliar ({attempt + 2}/{max_attempts})...")
                                await asyncio.sleep(wait_seconds)

                        raise RuntimeError(
                            f"Grok auxiliar falhou no clip {clip_index}/{total_clips} apos {max_attempts} tentativas: {last_error}"
                        )

                    async def _generate_grok_shadow_sequence() -> None:
                        nonlocal grok_shadow_video_path, grok_shadow_audio_path

                        if wan_segment_count <= 1:
                            await _generate_grok_shadow_clip_with_retry(
                                reference_image_path=grok_image_path,
                                clip_video_path=grok_shadow_video_path,
                                clip_audio_path=str(render_dir / "realistic_video_grok_shadow_audio.m4a"),
                                clip_index=1,
                                total_clips=1,
                            )
                            grok_shadow_audio_path = str(render_dir / "realistic_video_grok_shadow_audio.m4a")
                            return

                        local_ref = grok_image_path
                        grok_clip_paths: list[str] = []
                        grok_audio_paths: list[str] = []

                        for idx in range(wan_segment_count):
                            clip_video_path = str(render_dir / f"realistic_video_grok_shadow_clip_{idx:02d}.mp4")
                            clip_audio_path = str(render_dir / f"realistic_video_grok_shadow_clip_{idx:02d}.m4a")

                            await _generate_grok_shadow_clip_with_retry(
                                reference_image_path=local_ref,
                                clip_video_path=clip_video_path,
                                clip_audio_path=clip_audio_path,
                                clip_index=idx + 1,
                                total_clips=wan_segment_count,
                            )

                            grok_clip_paths.append(clip_video_path)
                            grok_audio_paths.append(clip_audio_path)

                            if idx < wan_segment_count - 1:
                                next_ref = str(render_dir / f"realistic_video_grok_shadow_ref_{idx:02d}.png")
                                await extract_last_frame(clip_video_path, next_ref)
                                local_ref = next_ref

                            await _on_progress(
                                22 + int(48 * (idx + 1) / wan_segment_count),
                                f"Gerando Grok auxiliar clip {idx + 1}/{wan_segment_count}...",
                            )

                        await concatenate_clips(grok_clip_paths, grok_shadow_video_path)
                        grok_shadow_audio_path = await _concatenate_audio_tracks(
                            grok_audio_paths,
                            str(render_dir / "realistic_video_grok_shadow_audio.m4a"),
                        )

                    await asyncio.gather(
                        _generate_wan_sequence(),
                        _generate_grok_shadow_sequence(),
                    )
                else:
                    await _generate_wan_sequence()
            else:
                # ── Seedance 2.0 (with auto-retry on content filter) ──
                final_prompt = optimized_prompt
                max_retries = 2

                async def _generate_seedance_clip_with_retry(
                    clip_output_path: str,
                    clip_duration: int,
                    clip_image_path: str | None,
                    clip_progress,
                ) -> None:
                    nonlocal final_prompt
                    prompt_for_attempt = final_prompt
                    for attempt in range(max_retries + 1):
                        try:
                            await generate_realistic_video(
                                prompt=prompt_for_attempt,
                                duration=clip_duration,
                                aspect_ratio=aspect_ratio,
                                output_path=clip_output_path,
                                generate_audio=generate_audio,
                                image_path=clip_image_path,
                                on_progress=clip_progress,
                            )
                            final_prompt = prompt_for_attempt
                            return
                        except RuntimeError as e:
                            error_msg = str(e)
                            if ("flagged as sensitive" in error_msg or "E005" in error_msg) and attempt < max_retries:
                                logger.warning(
                                    "Seedance content filter triggered (attempt %d/%d), sanitizing prompt...",
                                    attempt + 1,
                                    max_retries + 1,
                                )
                                project.progress = 10
                                await db.commit()
                                prompt_for_attempt = await sanitize_prompt_for_retry(prompt_for_attempt)
                                final_prompt = prompt_for_attempt
                                logger.info("Retrying Seedance with sanitized prompt (%d chars)", len(prompt_for_attempt))
                                project.progress = 15
                                await db.commit()
                                continue
                            raise
                await _generate_seedance_clip_with_retry(
                    clip_output_path=output_path,
                    clip_duration=duration,
                    clip_image_path=scene_reference_path,
                    clip_progress=_on_progress,
                )

            await db.rollback()
            project = await db.get(VideoProject, project_id)

            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                raise RuntimeError(f"{engine_label} nao gerou o video ou o arquivo esta vazio.")
            if shadow_audio_from_grok and (not os.path.exists(grok_shadow_video_path) or os.path.getsize(grok_shadow_video_path) == 0):
                raise RuntimeError("Grok auxiliar nao gerou video com audio para compor o resultado final.")

            # Force final frame geometry to the requested ratio (some providers may ignore aspect settings)
            try:
                normalized_path = str(render_dir / "realistic_video_aspect.mp4")
                output_path = await _normalize_video_aspect(output_path, aspect_ratio, normalized_path)
                logger.info(f"Realistic video aspect normalized to {aspect_ratio}: {output_path}")
            except Exception as e:
                logger.warning(f"Aspect normalization skipped, using provider output: {e}")

            if shadow_audio_from_grok:
                try:
                    grok_normalized_path = str(render_dir / "realistic_video_grok_shadow_aspect.mp4")
                    grok_shadow_video_path = await _normalize_video_aspect(
                        grok_shadow_video_path,
                        aspect_ratio,
                        grok_normalized_path,
                    )
                    logger.info(
                        "Grok shadow video aspect normalized to %s: %s",
                        aspect_ratio,
                        grok_shadow_video_path,
                    )
                except Exception as e:
                    logger.warning(f"Grok shadow aspect normalization skipped: {e}")

            file_size = os.path.getsize(output_path)
            video_duration = get_duration(output_path) if os.path.exists(output_path) else float(duration)
            if video_duration <= 0:
                video_duration = float(duration)

            project.progress = 80
            await db.commit()

            # ── Step 3: Generate audio (narration + music) for engines without native audio ──
            tags = project.tags if isinstance(project.tags, dict) else {}
            add_music = tags.get("add_music", False)
            add_narration = tags.get("add_narration", False)
            narration_voice = tags.get("narration_voice", "onyx")
            narration_text = (project.description or "").strip() if add_narration else ""
            dialogue_enabled = bool(tags.get("dialogue_enabled", False))
            dialogue_characters = tags.get("dialogue_characters", []) if isinstance(tags.get("dialogue_characters", []), list) else []
            dialogue_voice_profile_ids = tags.get("dialogue_voice_profile_ids", []) if isinstance(tags.get("dialogue_voice_profile_ids", []), list) else []
            dialogue_tone = str(tags.get("dialogue_tone", "informativo") or "informativo").strip() or "informativo"
            dialogue_duration = float(tags.get("dialogue_duration", 0) or 0)

            final_video_path = output_path
            has_audio = False
            provider_has_audio = await _video_has_audio_stream(output_path)
            provider_generate_audio = bool(tags.get("provider_generate_audio", False))
            seedance_missing_audio = engine == "seedance" and provider_generate_audio and not provider_has_audio
            if seedance_missing_audio:
                logger.warning(
                    "Seedance returned video without native audio for project %s.",
                    project_id,
                )

            if shadow_audio_from_grok:
                audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
                audio_dir.mkdir(parents=True, exist_ok=True)

                project.progress = 84
                await db.commit()

                if not (grok_shadow_audio_path and os.path.exists(grok_shadow_audio_path) and os.path.getsize(grok_shadow_audio_path) > 0):
                    grok_shadow_audio_path = await _extract_audio_from_video_track(
                        grok_shadow_video_path,
                        str(audio_dir / "grok_shadow_audio.m4a"),
                    )

                project.progress = 88
                await db.commit()

                combined_path = str(render_dir / "realistic_video_final.mp4")
                await _combine_realistic_audio(
                    video_path=output_path,
                    narration_path=grok_shadow_audio_path,
                    music_path="",
                    output_path=combined_path,
                    video_duration=video_duration,
                )

                if not os.path.exists(combined_path) or os.path.getsize(combined_path) <= 0:
                    raise RuntimeError("Falha ao sincronizar audio do Grok com o video do WAN")

                final_video_path = combined_path
                file_size = os.path.getsize(combined_path)
                has_audio = True

                if os.path.exists(grok_shadow_video_path):
                    try:
                        grok_shadow_duration = get_duration(grok_shadow_video_path)
                    except Exception:
                        grok_shadow_duration = 0.0

                tags["wan_shadow_grok_audio_used"] = True
                tags["wan_shadow_grok_video_path"] = grok_shadow_video_path
                tags["wan_shadow_grok_audio_path"] = grok_shadow_audio_path
                tags["wan_shadow_grok_video_duration"] = grok_shadow_duration
                tags["wan_shadow_grok_audio_duration"] = get_duration(grok_shadow_audio_path)
                project.tags = tags
                await db.commit()

                logger.info(
                    "WAN dual mode applied for project %s: wan_video=%s grok_audio=%s",
                    project_id,
                    output_path,
                    grok_shadow_audio_path,
                )

            elif engine in ("minimax", "wan2", "grok", "seedance") and (add_narration or add_music or dialogue_enabled or seedance_missing_audio):
                audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
                audio_dir.mkdir(parents=True, exist_ok=True)

                narration_path = prebuilt_narration_path or ""
                music_path = (prebuilt_music_path or "") if add_music else ""

                if seedance_missing_audio and not (add_narration or add_music or dialogue_enabled):
                    tags["seedance_audio_fallback"] = "native_missing_no_overlay"
                    project.tags = tags
                    await db.commit()

                # Dialogue mode: generate multi-speaker spoken track (+ optional separate BGM)
                if dialogue_enabled:
                    if narration_path and os.path.exists(narration_path):
                        if music_path and not os.path.exists(music_path):
                            music_path = ""
                        logger.info("Using pre-generated dialogue audio bundle for realistic project %s", project_id)
                    else:
                        project.progress = 82
                        await db.commit()
                        try:
                            from app.services.dialogue_audio import generate_dialogue_audio_bundle

                            dialogue_result = await generate_dialogue_audio_bundle(
                                db=db,
                                user_id=int(project.user_id),
                                project_id=project_id,
                                prompt_text=user_prompt,
                                target_duration=(dialogue_duration if dialogue_duration > 0 else video_duration),
                                characters=dialogue_characters,
                                voice_profile_ids=dialogue_voice_profile_ids,
                                tone=dialogue_tone,
                                interaction_persona=interaction_persona,
                                realistic_style=realistic_style,
                                add_music=bool(add_music),
                            )
                            narration_path = (dialogue_result.get("audio_path") or "").strip()
                            if add_music:
                                music_path = (dialogue_result.get("music_path") or "").strip()

                            dialogue_script = (dialogue_result.get("script") or "").strip()
                            dialogue_turns = dialogue_result.get("turns") or []
                            if dialogue_script:
                                tags["dialogue_script"] = dialogue_script
                                if not transcribed_text_for_subtitles:
                                    transcribed_text_for_subtitles = dialogue_script
                            if isinstance(dialogue_turns, list) and dialogue_turns:
                                tags["dialogue_turns"] = dialogue_turns
                            project.tags = tags
                            await db.commit()
                            logger.info("Dialogue audio generated for realistic project %s", project_id)
                        except Exception as e:
                            logger.warning(f"Dialogue generation failed, falling back to regular narration/music: {e}")
                            dialogue_enabled = False

                # Generate narration via TTS
                if not dialogue_enabled and add_narration and narration_text:
                    project.progress = 82
                    await db.commit()
                    logger.info(f"Generating narration for realistic video {project_id}: voice={narration_voice}")
                    try:
                        from app.services.script_audio import generate_tts_audio
                        narration_path = await generate_tts_audio(
                            text=narration_text,
                            voice=narration_voice,
                            project_id=project_id,
                            voice_type="builtin",
                            pause_level="normal",
                            tone="informativo",
                        )
                        logger.info(f"Narration generated: {narration_path}")
                    except Exception as e:
                        logger.warning(f"Narration generation failed: {e}")
                        narration_path = ""

                # Use external audio URL (from Tevoxi) or generate background music
                external_audio_url = tags.get("audio_url", "")
                if add_music and external_audio_url and not dialogue_enabled:
                    project.progress = 85
                    await db.commit()
                    logger.info(f"Downloading external audio for realistic video {project_id}: {external_audio_url[:100]}")
                    try:
                        ext_audio_path = await download_audio_if_url(external_audio_url, project_id)
                        if ext_audio_path and os.path.exists(ext_audio_path):
                            # Trim to clip section if specified
                            clip_start = float(tags.get("clip_start", 0))
                            clip_dur = float(tags.get("clip_duration", 0))
                            if clip_start > 0 or clip_dur > 0:
                                trimmed_path = str(audio_dir / "external_clip.mp3")
                                trim_args = ["ffmpeg", "-y", "-i", ext_audio_path]
                                if clip_start > 0:
                                    trim_args += ["-ss", str(clip_start)]
                                if clip_dur > 0:
                                    trim_args += ["-t", str(clip_dur)]
                                trim_args += ["-c:a", "libmp3lame", "-q:a", "2", trimmed_path]
                                proc = await asyncio.create_subprocess_exec(
                                    *trim_args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
                                )
                                await proc.wait()
                                if os.path.exists(trimmed_path) and os.path.getsize(trimmed_path) > 0:
                                    ext_audio_path = trimmed_path
                                    logger.info(f"Audio trimmed: start={clip_start}s, dur={clip_dur}s")
                            music_path = ext_audio_path
                            logger.info(f"External audio downloaded: {music_path}")
                        else:
                            logger.warning("External audio download returned empty, falling back to generation")
                    except Exception as e:
                        logger.warning(f"External audio download failed: {e}")

                # Fallback: generate background music via Tevoxi if no external audio
                if add_music and not music_path:
                    project.progress = 85
                    await db.commit()
                    logger.info(f"Generating background music for realistic video {project_id}")
                    try:
                        if dialogue_enabled:
                            from app.services.suno_music import generate_suno_music

                            music_path = await generate_suno_music(
                                output_path=str(audio_dir / "dialogue_music_fallback.mp3"),
                                duration=max(video_duration + 2.0, 8.0),
                                mood="informativo",
                                topic=user_prompt[:120],
                            )
                        else:
                            from app.services.tevoxi_music import generate_music_from_theme

                            music_theme = user_prompt[:200]
                            music_result = await generate_music_from_theme(
                                theme=music_theme,
                                project_id=project_id,
                                duration=max(int(video_duration) + 5, 30),
                                manual_settings={
                                    "music_mode": "instrumental",
                                    "music_duration": max(int(video_duration) + 5, 30),
                                },
                            )
                            music_path = music_result.get("audio_path", "")
                        logger.info(f"Background music generated: {music_path}")
                    except Exception as e:
                        logger.warning(f"Music generation failed, trying FFmpeg fallback: {e}")
                        try:
                            from app.services.script_audio import generate_background_music
                            music_path = str(audio_dir / "bgm_fallback.mp3")
                            generate_background_music(music_path, video_duration + 2)
                        except Exception as e2:
                            logger.warning(f"FFmpeg music fallback also failed: {e2}")
                            music_path = ""

                # Combine audio tracks with video using FFmpeg
                project.progress = 88
                await db.commit()

                if narration_path or music_path:
                    combined_path = str(render_dir / "realistic_video_final.mp4")
                    try:
                        await _combine_realistic_audio(
                            video_path=output_path,
                            narration_path=narration_path,
                            music_path=music_path,
                            output_path=combined_path,
                            video_duration=video_duration,
                        )
                        if os.path.exists(combined_path) and os.path.getsize(combined_path) > 0:
                            final_video_path = combined_path
                            file_size = os.path.getsize(combined_path)
                            has_audio = True
                            logger.info(f"Audio combined into video: {combined_path}")
                        else:
                            logger.warning("Combined video is empty, using original")
                    except Exception as e:
                        logger.warning(f"Audio combination failed, using video without audio: {e}")

            project.progress = 90
            await db.commit()

            # ── Step 3.5: Burn subtitles if enabled ──
            if getattr(project, "enable_subtitles", False):
                try:
                    from app.services.subtitle_generator import generate_ass_from_text, generate_ass_subtitles

                    subtitle_dir = Path(settings.media_dir) / "subtitles" / str(project_id)
                    subtitle_dir.mkdir(parents=True, exist_ok=True)
                    subtitle_path_r = str(subtitle_dir / "realistic.ass")

                    subtitle_settings = tags.get("subtitle_settings", {}) if isinstance(tags, dict) else {}
                    words_for_subs = transcribed_words_for_subtitles or (project.lyrics_words or [])
                    has_external_audio = bool((tags.get("audio_url", "") or "").strip())
                    if has_external_audio:
                        text_for_subs = (transcribed_text_for_subtitles or "").strip()
                    else:
                        text_for_subs = (
                            transcribed_text_for_subtitles
                            or str(tags.get("dialogue_script") or "")
                            or project.lyrics_text
                            or ""
                        ).strip()

                    if words_for_subs:
                        generate_ass_subtitles(
                            lyrics_words=words_for_subs,
                            aspect_ratio=aspect_ratio,
                            output_path=subtitle_path_r,
                            narration_mode=True,
                            style_settings=subtitle_settings,
                        )
                    elif text_for_subs:
                        generate_ass_from_text(
                            lyrics_text=text_for_subs,
                            duration=video_duration,
                            aspect_ratio=aspect_ratio,
                            output_path=subtitle_path_r,
                            narration_mode=True,
                            style_settings=subtitle_settings,
                        )
                    else:
                        subtitle_path_r = ""
                        if has_external_audio:
                            logger.info(
                                "Skipping realistic subtitles for project %d: no transcription text was available for external audio",
                                project_id,
                            )

                    if subtitle_path_r and os.path.exists(subtitle_path_r):
                        burned_path = str(render_dir / "realistic_video_subs.mp4")
                        sub_cmd = [
                            "ffmpeg", "-y", "-i", final_video_path,
                            "-vf", f"ass='{subtitle_path_r}'",
                            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                            "-c:a", "copy", burned_path,
                        ]
                        proc = await asyncio.create_subprocess_exec(
                            *sub_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                        )
                        _, stderr = await proc.communicate()
                        if proc.returncode == 0 and os.path.exists(burned_path) and os.path.getsize(burned_path) > 0:
                            final_video_path = burned_path
                            file_size = os.path.getsize(burned_path)
                            logger.info(f"Subtitles burned into realistic video {project_id}")
                        else:
                            logger.warning(f"Subtitle burn failed (rc={proc.returncode}): {stderr.decode()[:500] if stderr else ''}")
                except Exception as e:
                    logger.warning(f"Subtitle generation/burn failed for realistic video {project_id}: {e}")

            # ── Step 4: Generate thumbnail from video frame ──
            thumb_dir = Path(settings.media_dir) / "thumbnails" / str(project_id)
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = str(thumb_dir / "thumbnail.jpg")

            from app.services.thumbnail_generator import generate_thumbnail_from_frame
            try:
                generate_thumbnail_from_frame(
                    video_path=final_video_path,
                    title=project.title or "Video Realista",
                    artist=engine_label,
                    output_path=thumb_path,
                )
            except Exception as e:
                logger.warning(f"Realistic video thumbnail failed: {e}")
                thumb_path = ""

            project.progress = 95
            await db.commit()

            # ── Step 5: Save render to DB ──
            render = VideoRender(
                project_id=project_id,
                format=aspect_ratio,
                file_path=final_video_path,
                file_size=file_size,
                thumbnail_path=thumb_path,
                duration=video_duration,
            )
            db.add(render)

            project.status = VideoStatus.COMPLETED
            project.progress = 100
            project.track_duration = video_duration
            await db.commit()

            logger.info(f"Realistic video pipeline complete for project {project_id} ({video_duration:.1f}s)")

        except Exception as e:
            logger.error(f"Realistic video pipeline failed for project {project_id}: {e}", exc_info=True)
            project = await db.get(VideoProject, project_id)
            if project:
                project.status = VideoStatus.FAILED
                project.error_message = _safe_error_message(
                    e,
                    "Falha ao gerar o vídeo realista",
                )[:1000]
                await db.commit()
