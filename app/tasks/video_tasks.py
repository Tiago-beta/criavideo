"""
Video Tasks — Async background tasks for the full video generation pipeline.
"""
import os
import logging
import httpx
from pathlib import Path
from app.config import get_settings
from app.database import async_session
from app.models import VideoProject, VideoScene, VideoRender, VideoStatus

logger = logging.getLogger(__name__)
settings = get_settings()


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

            # ── Step 1: Generate scenes (images) ──
            project.status = VideoStatus.GENERATING_SCENES
            project.progress = 5
            await db.commit()

            from app.services.scene_generator import generate_all_scenes

            scenes = await generate_all_scenes(
                project_id=project_id,
                lyrics_text=project.lyrics_text or "",
                lyrics_words=project.lyrics_words or [],
                duration=project.track_duration or 180,
                aspect_ratio=project.aspect_ratio,
                style_hint=project.style_prompt,
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
                )
                db.add(scene)

            project.progress = 40
            await db.commit()

            # ── Step 2: Generate Grok video clips for chorus highlights ──
            project.status = VideoStatus.GENERATING_CLIPS
            project.progress = 45
            await db.commit()

            from app.services.grok_video import generate_video_clip

            clips_dir = Path(settings.media_dir) / "clips" / str(project_id)
            clips_dir.mkdir(parents=True, exist_ok=True)

            for s in scenes:
                if not s.get("is_chorus") or not s.get("image_path"):
                    continue
                try:
                    clip_path = str(clips_dir / f"clip_{s['scene_index']:03d}.mp4")
                    await generate_video_clip(
                        image_path=s["image_path"],
                        prompt=s.get("visual_prompt", "Slow cinematic motion"),
                        output_path=clip_path,
                        duration=min(int(s.get("end_time", 0) - s.get("start_time", 0)), 10),
                    )
                    s["clip_path"] = clip_path
                    s["scene_type"] = "video_clip"
                except Exception as e:
                    logger.warning(f"Grok clip generation failed for scene {s.get('scene_index')}: {e}")

            project.progress = 60
            await db.commit()

            # ── Step 3: Generate karaoke subtitles ──
            from app.services.subtitle_generator import generate_ass_subtitles, generate_ass_from_text

            subtitle_dir = Path(settings.media_dir) / "subtitles" / str(project_id)
            subtitle_dir.mkdir(parents=True, exist_ok=True)
            subtitle_path = str(subtitle_dir / "karaoke.ass")

            if project.lyrics_words:
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

            project.progress = 70
            await db.commit()

            # ── Step 4: Compose video with FFmpeg ──
            project.status = VideoStatus.RENDERING
            project.progress = 75
            await db.commit()

            from app.services.video_composer import compose_video

            audio_path = await download_audio_if_url(project.audio_path, project_id)
            if not audio_path or not os.path.exists(audio_path):
                raise FileNotFoundError(f"Audio file not found: {project.audio_path}")

            render_result = compose_video(
                project_id=project_id,
                scenes=scenes,
                audio_path=audio_path,
                subtitle_path=subtitle_path,
                aspect_ratio=project.aspect_ratio,
            )

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
