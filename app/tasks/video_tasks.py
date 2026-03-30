"""
Video Tasks — Async background tasks for the full video generation pipeline.
"""
import os
import asyncio
import logging
import httpx
from pathlib import Path
from app.config import get_settings
from app.database import async_session
from app.models import VideoProject, VideoScene, VideoRender, VideoStatus

logger = logging.getLogger(__name__)
settings = get_settings()


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
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        resp = await client.get(audio_path)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)

    logger.info(f"Audio downloaded: {local_path} ({os.path.getsize(local_path)} bytes)")
    return local_path


async def run_video_pipeline(project_id: int):
    """Full pipeline: scenes → subtitles → render → thumbnail.
    Runs as a background task.
    """
    async with async_session() as db:
        try:
            project = await db.get(VideoProject, project_id)
            if not project:
                return

            # ── Step 0: Download audio if URL ──
            from app.services.video_composer import compose_video

            audio_path = await download_audio_if_url(project.audio_path, project_id)
            if not audio_path or not os.path.exists(audio_path):
                raise FileNotFoundError(f"Audio file not found: {project.audio_path}")
            audio_basename = os.path.basename(audio_path).lower()
            is_music_only_mode = audio_basename.startswith("custom_background_music")

            # ── Step 0b: Transcribe audio with Whisper for accurate karaoke ──
            transcribed_words = []
            if not is_music_only_mode:
                try:
                    from app.services.transcriber import transcribe_audio
                    import asyncio
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, transcribe_audio, audio_path
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
            custom_bgm_path = _find_custom_background_music(project_id)
            if custom_bgm_path:
                logger.info(f"Using custom uploaded background music for project {project_id}: {custom_bgm_path}")
            if not custom_bgm_path and audio_path and os.path.basename(audio_path) == "narration.mp3":
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
            use_custom_images = getattr(project, "use_custom_images", False) or False

            if use_custom_images:
                # User uploaded their own photos — use them directly, skip AI generation
                img_dir = Path(settings.media_dir) / "images" / str(project_id)
                user_images = sorted(
                    [str(p) for p in img_dir.glob("user_*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}]
                )
                if not user_images:
                    raise RuntimeError("Nenhuma foto encontrada. Envie pelo menos uma foto.")

                dur = project.track_duration or 180
                # Distribute images evenly across audio duration, cycling if needed
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
                    style_hint=style_prompt,
                    on_progress=_scene_progress,
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
                    )
                elif project.lyrics_words:
                    generate_ass_subtitles(
                        lyrics_words=project.lyrics_words,
                        aspect_ratio=project.aspect_ratio,
                        output_path=subtitle_path,
                    )
                elif project.lyrics_text:
                    generate_ass_from_text(
                        lyrics_text=project.lyrics_text,
                        duration=project.track_duration or 180,
                        aspect_ratio=project.aspect_ratio,
                        output_path=subtitle_path,
                    )
                else:
                    subtitle_path = ""
            else:
                logger.info(f"Skipping subtitle generation (tela_preta={is_black_screen}, enable_subtitles={enable_subtitles})")

            project.progress = 70
            await db.commit()

            # ── Step 3: Get background music (Suno task started earlier) ──
            background_music_path = "" if is_music_only_mode else (custom_bgm_path or "")
            if suno_music_task is not None:
                try:
                    background_music_path = await suno_music_task
                    logger.info(f"Suno background music result: {background_music_path}")
                except Exception as e:
                    logger.warning(f"Suno music await failed: {e}")

            # Fallback to FFmpeg synthesis if Suno didn't produce music
            if not custom_bgm_path and not background_music_path and audio_path and os.path.basename(audio_path) == "narration.mp3":
                try:
                    from app.services.script_audio import generate_background_music
                    from app.services.video_composer import _get_duration as get_audio_duration

                    music_dir = Path(settings.media_dir) / "audio" / str(project_id)
                    music_dir.mkdir(parents=True, exist_ok=True)
                    music_path = str(music_dir / "background_music.mp3")

                    audio_dur = get_audio_duration(audio_path)
                    if audio_dur > 0:
                        import asyncio
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
            import asyncio
            render_result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: compose_video(
                    project_id=project_id,
                    scenes=scenes,
                    audio_path=audio_path,
                    subtitle_path=subtitle_path,
                    aspect_ratio=project.aspect_ratio,
                    background_music_path=background_music_path,
                ),
            )

            # Refresh DB session after long render to avoid stale connections
            await db.rollback()
            project = await db.get(VideoProject, project_id)

            project.progress = 90
            await db.commit()

            # ── Step 5: Generate thumbnail ──
            from app.services.thumbnail_generator import generate_thumbnail

            thumb_dir = Path(settings.media_dir) / "thumbnails" / str(project_id)
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = str(thumb_dir / "thumbnail.jpg")

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
                project.error_message = str(e)[:1000]
                await db.commit()
