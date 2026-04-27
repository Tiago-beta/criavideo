"""Background tasks and helpers for the Similar Video workflow."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import mimetypes
import os
import uuid
from pathlib import Path

import openai
from sqlalchemy import delete, select

from app.config import get_settings
from app.database import async_session
from app.models import VideoProject, VideoRender, VideoScene, VideoStatus
from app.services.baixatudo_client import BaixaTudoClient, BaixaTudoError
from app.services.grok_video import generate_video_clip
from app.services.minimax_video import generate_minimax_video
from app.services.multi_clip import concatenate_clips
from app.services.runpod_video import generate_wan_video
from app.services.scene_generator import generate_scene_image
from app.services.seedance_video import generate_realistic_video
from app.services.thumbnail_generator import generate_thumbnail_from_frame
from app.services.video_composer import _get_duration as get_duration


logger = logging.getLogger(__name__)
settings = get_settings()


def _safe_tags_dict(raw: object) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _safe_error_message(err: Exception, fallback: str) -> str:
    try:
        raw = str(err or "").strip()
    except Exception:
        raw = ""
    if raw and raw not in {"{}", "[]", "None", "null", "[object Object]"}:
        return raw
    return fallback


async def _ffprobe_duration(video_path: str) -> float:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError("ffprobe failed to read video duration")
    try:
        value = float((stdout or b"").decode().strip())
    except Exception as exc:
        raise RuntimeError("Could not parse video duration") from exc
    if value <= 0:
        raise RuntimeError("Video duration is zero")
    return value


async def _extract_frame(video_path: str, timestamp_seconds: float, output_path: str) -> None:
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-ss",
        f"{max(0.0, float(timestamp_seconds)):.3f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        output_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not out.exists() or out.stat().st_size <= 0:
        details = (stderr or b"").decode(errors="ignore")[-500:]
        raise RuntimeError(f"Frame extraction failed: {details}")


def _image_file_to_data_url(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    raw = Path(path).read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{encoded}"


async def _analyze_frame_prompt(
    client: openai.AsyncOpenAI,
    frame_path: str,
    start_time: float,
    end_time: float,
    duration_seconds: float,
) -> str:
    image_data_url = _image_file_to_data_url(frame_path)
    model_name = (settings.similar_analysis_model or "gpt-4o").strip() or "gpt-4o"

    try:
        resp = await client.chat.completions.create(
            model=model_name,
            temperature=0.2,
            max_tokens=450,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Voce analisa frames de video e escreve prompts cinematograficos em portugues do Brasil. "
                        "Retorne JSON com chave 'scene_prompt'."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Analise este frame e crie um prompt para recriar a cena em video com riqueza de detalhes, "
                                "incluindo enquadramento, ambiente, luz, cores, textura e movimento de camera. "
                                "A cena representa o trecho de "
                                f"{start_time:.1f}s ate {end_time:.1f}s de um video de {duration_seconds:.1f}s. "
                                "Escreva em pt-BR e sem marcadores."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": image_data_url},
                        },
                    ],
                },
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        data = json.loads(raw) if raw else {}
        prompt = str(data.get("scene_prompt") or "").strip()
        if prompt:
            return prompt[:1600]
    except Exception as exc:
        logger.warning("Frame analysis fallback activated: %s", exc)

    return (
        "Cena cinematografica ultra detalhada com composicao fiel ao frame de referencia, "
        "movimento de camera suave, iluminacao natural e continuidade visual consistente."
    )


def _build_temporal_prompt(scenes: list[dict]) -> str:
    chunks: list[str] = []
    for scene in scenes:
        start = float(scene.get("start_time", 0) or 0)
        end = float(scene.get("end_time", start) or start)
        prompt = str(scene.get("prompt") or "").strip()
        if not prompt:
            continue
        chunks.append(f"{start:.1f}s - {end:.1f}s\n{prompt}")
    return "\n\n".join(chunks).strip()


def _scene_duration(scene: VideoScene) -> int:
    start = float(scene.start_time or 0)
    end = float(scene.end_time or start)
    raw = max(0.0, end - start)
    floor = max(1, int(settings.similar_scene_min_seconds or 5))
    ceil = max(floor, int(settings.similar_scene_max_seconds or 15))
    if raw <= 0:
        return floor
    return max(floor, min(ceil, int(round(raw))))


def _normalize_engine(value: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"grok", "wan2", "minimax", "seedance"}:
        return raw
    if "seedance" in raw:
        return "seedance"
    if "mini" in raw:
        return "minimax"
    if "wan" in raw or "ultra" in raw:
        return "wan2"
    return "grok"


def _engine_duration(engine: str, duration: int) -> int:
    safe = max(1, int(duration or 5))
    if engine == "grok":
        return max(1, min(15, safe))
    if engine == "wan2":
        allowed = (5, 10, 15)
        if safe in allowed:
            return safe
        return min(allowed, key=lambda candidate: (abs(candidate - safe), candidate))
    if engine in {"minimax", "seedance"}:
        return max(5, min(10, safe))
    return max(5, min(15, safe))


async def _ensure_scene_image(scene: VideoScene, aspect_ratio: str, target_dir: Path) -> str:
    if scene.image_path and os.path.exists(scene.image_path):
        return str(scene.image_path)

    target_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(target_dir / f"similar_scene_{int(scene.scene_index or 0):03d}.png")
    prompt = (scene.prompt or "").strip() or "Cena cinematografica detalhada."

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        generate_scene_image,
        prompt[:1200],
        aspect_ratio,
        out_path,
    )

    if not os.path.exists(out_path) or os.path.getsize(out_path) <= 0:
        raise RuntimeError("Falha ao gerar imagem da cena")

    scene.image_path = out_path
    return out_path


async def _generate_clip_for_scene(
    scene: VideoScene,
    *,
    engine: str,
    aspect_ratio: str,
    clip_dir: Path,
    image_dir: Path,
) -> str:
    normalized_engine = _normalize_engine(engine)
    clip_duration = _engine_duration(normalized_engine, _scene_duration(scene))
    prompt = (scene.prompt or "").strip() or "Cena cinematografica detalhada."

    clip_dir.mkdir(parents=True, exist_ok=True)
    image_path = await _ensure_scene_image(scene, aspect_ratio, image_dir)
    output_path = str(clip_dir / f"similar_scene_{int(scene.scene_index or 0):03d}.mp4")

    if normalized_engine == "grok":
        await generate_video_clip(
            image_path=image_path,
            prompt=prompt,
            output_path=output_path,
            duration=clip_duration,
            aspect_ratio=aspect_ratio,
            on_progress=None,
            reference_mode="",
        )
    elif normalized_engine == "minimax":
        await generate_minimax_video(
            prompt=prompt,
            duration=clip_duration,
            aspect_ratio=aspect_ratio,
            output_path=output_path,
            image_path=image_path,
            on_progress=None,
        )
    elif normalized_engine == "wan2":
        await generate_wan_video(
            prompt=prompt,
            duration=clip_duration,
            aspect_ratio=aspect_ratio,
            output_path=output_path,
            image_path=image_path,
            generate_audio=True,
            on_progress=None,
        )
    else:
        await generate_realistic_video(
            prompt=prompt,
            duration=clip_duration,
            aspect_ratio=aspect_ratio,
            output_path=output_path,
            generate_audio=True,
            image_path=image_path,
            on_progress=None,
        )

    if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
        raise RuntimeError("Falha ao gerar clip da cena")

    scene.clip_path = output_path
    scene.scene_type = "video_clip"
    scene.end_time = float(scene.start_time or 0) + float(clip_duration)
    return output_path


def _is_similar_project(project: VideoProject) -> bool:
    tags = _safe_tags_dict(project.tags)
    return str(tags.get("type") or "").strip().lower() == "similar"


async def run_similar_reference_analysis(project_id: int, source_url: str) -> None:
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        if not project:
            return

        tags = _safe_tags_dict(project.tags)
        tags.update(
            {
                "type": "similar",
                "similar_stage": "downloading_reference",
                "similar_source_url": source_url,
            }
        )
        project.tags = tags
        project.status = VideoStatus.GENERATING_SCENES
        project.progress = 2
        project.error_message = None
        await db.commit()

        try:
            work_dir = Path(settings.media_dir) / "similar" / str(project_id)
            frames_dir = work_dir / "frames"
            work_dir.mkdir(parents=True, exist_ok=True)
            frames_dir.mkdir(parents=True, exist_ok=True)

            client = BaixaTudoClient(
                base_url=settings.baixatudo_api_url,
                api_key=settings.baixatudo_api_key,
                timeout_seconds=settings.baixatudo_timeout_seconds,
                poll_interval_seconds=settings.baixatudo_poll_interval_seconds,
                max_wait_seconds=settings.baixatudo_max_wait_seconds,
            )

            download_result = await client.download_video(
                source_url=source_url,
                output_path=str(work_dir / "reference_video.mp4"),
                formato="video_melhor",
            )

            tags = _safe_tags_dict(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": "analyzing_reference",
                    "similar_download_task_id": download_result.task_id,
                    "similar_source_url": download_result.source_url,
                    "similar_normalized_url": download_result.normalized_url,
                    "similar_local_video_path": download_result.output_path,
                }
            )
            project.tags = tags
            project.progress = 15
            await db.commit()

            duration_seconds = await _ffprobe_duration(download_result.output_path)
            scene_seconds = max(
                int(settings.similar_scene_default_seconds or 5),
                int(settings.similar_scene_min_seconds or 5),
            )
            scene_seconds = min(scene_seconds, int(settings.similar_scene_max_seconds or 15))
            scene_count = max(1, int(math.ceil(duration_seconds / scene_seconds)))
            scene_count = min(scene_count, 120)

            openai_client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            scene_payloads: list[dict] = []

            for idx in range(scene_count):
                start = float(idx * scene_seconds)
                if start >= duration_seconds:
                    break
                end = min(duration_seconds, start + scene_seconds)
                midpoint = min(duration_seconds - 0.05, start + ((end - start) / 2.0))
                if midpoint < 0:
                    midpoint = 0

                frame_path = str(frames_dir / f"frame_{idx:03d}.jpg")
                await _extract_frame(download_result.output_path, midpoint, frame_path)
                prompt = await _analyze_frame_prompt(
                    client=openai_client,
                    frame_path=frame_path,
                    start_time=start,
                    end_time=end,
                    duration_seconds=duration_seconds,
                )

                scene_payloads.append(
                    {
                        "scene_index": idx,
                        "start_time": start,
                        "end_time": end,
                        "prompt": prompt,
                    }
                )

                progress = 20 + int(55 * ((idx + 1) / max(scene_count, 1)))
                project.progress = min(80, progress)
                await db.commit()

            if not scene_payloads:
                raise RuntimeError("Nenhuma cena foi extraida do video de referencia")

            await db.execute(delete(VideoScene).where(VideoScene.project_id == project_id))

            for payload in scene_payloads:
                db.add(
                    VideoScene(
                        project_id=project_id,
                        scene_index=int(payload["scene_index"]),
                        scene_type="image",
                        prompt=str(payload["prompt"]),
                        image_path="",
                        clip_path="",
                        start_time=float(payload["start_time"]),
                        end_time=float(payload["end_time"]),
                        lyrics_segment="",
                        is_user_uploaded=False,
                    )
                )

            tags = _safe_tags_dict(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": "analysis_ready",
                    "similar_scene_seconds": scene_seconds,
                    "similar_scene_count": len(scene_payloads),
                    "similar_total_duration": duration_seconds,
                }
            )
            project.tags = tags
            project.track_duration = float(duration_seconds)
            project.lyrics_text = _build_temporal_prompt(scene_payloads)
            project.status = VideoStatus.PENDING
            project.progress = 0
            project.error_message = None
            await db.commit()

        except Exception as exc:
            logger.error("Similar analysis failed for project %s: %s", project_id, exc, exc_info=True)
            project = await db.get(VideoProject, project_id)
            if not project:
                return
            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "analysis_failed"})
            project.tags = tags
            project.status = VideoStatus.FAILED
            project.error_message = _safe_error_message(exc, "Falha ao analisar o video de referencia")[:1000]
            await db.commit()


async def run_similar_generate_previews(project_id: int, engine: str, aspect_ratio: str) -> None:
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        if not project:
            return

        if not _is_similar_project(project):
            project.status = VideoStatus.FAILED
            project.error_message = "Projeto nao esta no modo Semelhante"
            await db.commit()
            return

        try:
            tags = _safe_tags_dict(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": "generating_previews",
                    "similar_engine": _normalize_engine(engine),
                    "similar_aspect_ratio": aspect_ratio,
                }
            )
            project.tags = tags
            project.status = VideoStatus.GENERATING_CLIPS
            project.progress = 5
            project.error_message = None
            await db.commit()

            result = await db.execute(
                select(VideoScene)
                .where(VideoScene.project_id == project_id)
                .order_by(VideoScene.scene_index.asc())
            )
            scenes = result.scalars().all()
            if not scenes:
                raise RuntimeError("Nenhuma cena encontrada para gerar previews")

            clip_dir = Path(settings.media_dir) / "clips" / str(project_id)
            image_dir = Path(settings.media_dir) / "images" / str(project_id)

            for idx, scene in enumerate(scenes):
                await _generate_clip_for_scene(
                    scene,
                    engine=engine,
                    aspect_ratio=aspect_ratio,
                    clip_dir=clip_dir,
                    image_dir=image_dir,
                )
                project.progress = 10 + int(80 * ((idx + 1) / max(len(scenes), 1)))
                await db.commit()

            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "preview_ready"})
            project.tags = tags
            project.status = VideoStatus.PENDING
            project.progress = 0
            await db.commit()

        except Exception as exc:
            logger.error("Similar preview generation failed for project %s: %s", project_id, exc, exc_info=True)
            project = await db.get(VideoProject, project_id)
            if not project:
                return
            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "preview_failed"})
            project.tags = tags
            project.status = VideoStatus.FAILED
            project.error_message = _safe_error_message(exc, "Falha ao gerar previews das cenas")[:1000]
            await db.commit()


async def run_similar_regenerate_scene(project_id: int, scene_id: int, engine: str, aspect_ratio: str) -> None:
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        if not project:
            return

        if not _is_similar_project(project):
            project.status = VideoStatus.FAILED
            project.error_message = "Projeto nao esta no modo Semelhante"
            await db.commit()
            return

        try:
            scene = await db.get(VideoScene, scene_id)
            if not scene or scene.project_id != project_id:
                raise RuntimeError("Cena nao encontrada para regeneracao")

            tags = _safe_tags_dict(project.tags)
            tags.update(
                {
                    "type": "similar",
                    "similar_stage": "regenerating_scene",
                    "similar_regenerating_scene_id": scene_id,
                    "similar_engine": _normalize_engine(engine),
                    "similar_aspect_ratio": aspect_ratio,
                }
            )
            project.tags = tags
            project.status = VideoStatus.GENERATING_CLIPS
            project.progress = 20
            await db.commit()

            clip_dir = Path(settings.media_dir) / "clips" / str(project_id)
            image_dir = Path(settings.media_dir) / "images" / str(project_id)

            await _generate_clip_for_scene(
                scene,
                engine=engine,
                aspect_ratio=aspect_ratio,
                clip_dir=clip_dir,
                image_dir=image_dir,
            )

            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "preview_ready"})
            project.tags = tags
            project.status = VideoStatus.PENDING
            project.progress = 0
            project.error_message = None
            await db.commit()

        except Exception as exc:
            logger.error("Similar scene regeneration failed for project %s scene %s: %s", project_id, scene_id, exc, exc_info=True)
            project = await db.get(VideoProject, project_id)
            if not project:
                return
            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "regenerate_failed"})
            project.tags = tags
            project.status = VideoStatus.FAILED
            project.error_message = _safe_error_message(exc, "Falha ao regenerar a cena")[:1000]
            await db.commit()


async def run_similar_merge(project_id: int, aspect_ratio: str, scene_ids: list[int] | None = None) -> None:
    async with async_session() as db:
        project = await db.get(VideoProject, project_id)
        if not project:
            return

        if not _is_similar_project(project):
            project.status = VideoStatus.FAILED
            project.error_message = "Projeto nao esta no modo Semelhante"
            await db.commit()
            return

        try:
            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "merging_scenes", "similar_aspect_ratio": aspect_ratio})
            project.tags = tags
            project.status = VideoStatus.RENDERING
            project.progress = 10
            project.error_message = None
            await db.commit()

            result = await db.execute(
                select(VideoScene)
                .where(VideoScene.project_id == project_id)
                .order_by(VideoScene.scene_index.asc())
            )
            scenes = result.scalars().all()
            if scene_ids:
                selected: set[int] = set()
                for raw_id in scene_ids:
                    try:
                        parsed_id = int(raw_id)
                    except Exception:
                        continue
                    if parsed_id > 0:
                        selected.add(parsed_id)
                scenes = [scene for scene in scenes if int(scene.id or 0) in selected]

            clip_paths = [str(scene.clip_path) for scene in scenes if scene.clip_path and os.path.exists(scene.clip_path)]
            if not clip_paths:
                raise RuntimeError("Nenhum clip pronto para unir")

            render_dir = Path(settings.media_dir) / "renders" / str(project_id)
            render_dir.mkdir(parents=True, exist_ok=True)
            output_path = str(render_dir / f"video_{aspect_ratio.replace(':', 'x')}_similar.mp4")

            await concatenate_clips(clip_paths, output_path)

            if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
                raise RuntimeError("Falha ao unir os clips")

            duration = float(get_duration(output_path) or 0)
            file_size = int(os.path.getsize(output_path))

            thumb_dir = Path(settings.media_dir) / "thumbnails" / str(project_id)
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = str(thumb_dir / "thumbnail.jpg")
            try:
                generate_thumbnail_from_frame(
                    video_path=output_path,
                    title=project.title or "Video Semelhante",
                    artist="Semelhante",
                    output_path=thumb_path,
                )
            except Exception as thumb_exc:
                logger.warning("Similar merge thumbnail failed for project %s: %s", project_id, thumb_exc)
                thumb_path = ""

            db.add(
                VideoRender(
                    project_id=project_id,
                    format=aspect_ratio,
                    file_path=output_path,
                    file_size=file_size,
                    thumbnail_path=thumb_path,
                    duration=duration,
                )
            )

            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "merged"})
            project.tags = tags
            project.status = VideoStatus.COMPLETED
            project.progress = 100
            project.aspect_ratio = aspect_ratio
            project.track_duration = duration or float(project.track_duration or 0)
            await db.commit()

        except Exception as exc:
            logger.error("Similar merge failed for project %s: %s", project_id, exc, exc_info=True)
            project = await db.get(VideoProject, project_id)
            if not project:
                return
            tags = _safe_tags_dict(project.tags)
            tags.update({"type": "similar", "similar_stage": "merge_failed"})
            project.tags = tags
            project.status = VideoStatus.FAILED
            project.error_message = _safe_error_message(exc, "Falha ao unir as cenas")[:1000]
            await db.commit()
