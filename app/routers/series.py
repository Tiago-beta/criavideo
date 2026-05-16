import asyncio
from datetime import datetime
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import engine, get_db
from app.models import (
    Base,
    VideoProject,
    VideoSeries,
    VideoSeriesChatMessage,
    VideoSeriesChatThread,
    VideoSeriesEpisode,
)
from app.services.series_planner import build_series_workspace_plan

router = APIRouter(prefix="/api/series", tags=["series"])

_SERIES_KIND_ALIASES = {
    "film": "film",
    "filme": "film",
    "series": "series",
    "serie": "series",
    "série": "series",
    "drama": "drama",
}
_SERIES_SCHEMA_READY = False
_SERIES_SCHEMA_LOCK = asyncio.Lock()


async def _ensure_series_workspace_schema() -> None:
    global _SERIES_SCHEMA_READY
    if _SERIES_SCHEMA_READY:
        return
    async with _SERIES_SCHEMA_LOCK:
        if _SERIES_SCHEMA_READY:
            return
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn,
                    tables=[
                        VideoSeries.__table__,
                        VideoSeriesEpisode.__table__,
                        VideoSeriesChatThread.__table__,
                        VideoSeriesChatMessage.__table__,
                    ],
                )
            )
        _SERIES_SCHEMA_READY = True


class CreateSeriesRequest(BaseModel):
    kind: str = "series"
    title: str = ""
    description: str = ""
    aspect_ratio: str = "16:9"
    language: str = "pt-BR"
    target_duration_seconds: float = 0
    episode_count: int = 0
    cover_image_path: str = ""
    default_settings: dict[str, Any] = Field(default_factory=dict)
    workspace_state: dict[str, Any] = Field(default_factory=dict)


class UpdateSeriesRequest(BaseModel):
    kind: str | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    aspect_ratio: str | None = None
    language: str | None = None
    target_duration_seconds: float | None = None
    cover_image_path: str | None = None
    default_settings: dict[str, Any] | None = None
    workspace_state: dict[str, Any] | None = None


class CreateSeriesEpisodeRequest(BaseModel):
    title: str = ""
    synopsis: str = ""
    script_text: str = ""
    season_number: int = 1
    episode_number: int = 0
    status: str = "draft"
    storyboard: list[dict[str, Any]] = Field(default_factory=list)
    timeline_data: dict[str, Any] = Field(default_factory=dict)
    selected_persona_ids: list[int] = Field(default_factory=list)
    video_project_id: int | None = None


class UpdateSeriesEpisodeRequest(BaseModel):
    title: str | None = None
    synopsis: str | None = None
    script_text: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    status: str | None = None
    storyboard: list[dict[str, Any]] | None = None
    timeline_data: dict[str, Any] | None = None
    selected_persona_ids: list[int] | None = None
    video_project_id: int | None = None


class ReorderSeriesEpisodesRequest(BaseModel):
    episode_ids: list[int] = Field(default_factory=list)
    season_number: int = 1


class CreateSeriesThreadRequest(BaseModel):
    title: str = "Novo bate-papo"


class CreateSeriesMessageRequest(BaseModel):
    content: str = ""
    role: str = "user"
    actions: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "completed"


class GenerateSeriesPlanRequest(BaseModel):
    message: str = ""
    tab: str = "projeto"


def _normalize_series_kind(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    return _SERIES_KIND_ALIASES.get(normalized, "series")


def _normalize_series_tab(tab: str) -> str:
    normalized = str(tab or "").strip().lower()
    if normalized in {"tela", "projeto", "project"}:
        return "projeto"
    if normalized == "roteiro":
        return "roteiro"
    if normalized == "personagens":
        return "personagens"
    if normalized == "storyboard":
        return "storyboard"
    if normalized in {"timeline", "linha-do-tempo", "linha do tempo"}:
        return "timeline"
    return "projeto"


def _normalize_positive_int(value: int, fallback: int = 1) -> int:
    try:
        parsed = int(value or 0)
    except Exception:
        parsed = 0
    return parsed if parsed > 0 else fallback


def _workspace_name_key(value: Any) -> str:
    lowered = str(value or "").strip().lower()
    if not lowered:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")


def _merge_named_workspace_items(
    existing_items: Any,
    incoming_items: Any,
    *,
    preserve_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    existing_list = [item for item in (existing_items if isinstance(existing_items, list) else []) if isinstance(item, dict)]
    incoming_list = [item for item in (incoming_items if isinstance(incoming_items, list) else []) if isinstance(item, dict)]

    existing_by_key: dict[str, dict[str, Any]] = {}
    for item in existing_list:
        key = _workspace_name_key(item.get("name")) or _workspace_name_key(item.get("id"))
        if key and key not in existing_by_key:
            existing_by_key[key] = item

    merged: list[dict[str, Any]] = []
    used_keys: set[str] = set()
    for index, item in enumerate(incoming_list, start=1):
        key = _workspace_name_key(item.get("name")) or _workspace_name_key(item.get("id")) or f"item-{index}"
        previous = existing_by_key.get(key)
        next_item = dict(previous or {})
        next_item.update(item)
        for field in preserve_fields:
            if previous and previous.get(field) and not next_item.get(field):
                next_item[field] = previous.get(field)
        next_item["id"] = str(next_item.get("id") or (previous or {}).get("id") or key)
        merged.append(next_item)
        used_keys.add(key)

    for item in existing_list:
        key = _workspace_name_key(item.get("name")) or _workspace_name_key(item.get("id"))
        if key and key not in used_keys:
            merged.append(item)
    return merged


def _build_episode_script_text(scenes: list[dict[str, Any]]) -> str:
    if not scenes:
        return "Nenhum roteiro gerado ainda para este episodio."
    blocks: list[str] = []
    for index, scene in enumerate(scenes, start=1):
        title = str(scene.get("title") or f"Cena {index}").strip() or f"Cena {index}"
        beat = str(scene.get("beat") or "").strip() or "Beat ainda nao definido."
        location = str(scene.get("location") or "").strip() or "Local a definir"
        characters = [str(item).strip() for item in (scene.get("characters") or []) if str(item).strip()]
        objects = [str(item).strip() for item in (scene.get("objects") or []) if str(item).strip()]
        duration_seconds = int(round(float(scene.get("duration_seconds") or 0))) if scene.get("duration_seconds") else 0
        block_lines = [
            f"Cena {index}: {title}",
            f"Local: {location}",
            f"Acao: {beat}",
        ]
        if duration_seconds > 0:
            block_lines.append(f"Duracao estimada: {duration_seconds}s")
        if characters:
            block_lines.append(f"Personagens: {', '.join(characters)}")
        if objects:
            block_lines.append(f"Objetos: {', '.join(objects)}")
        blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks)


async def _apply_generated_series_plan(
    *,
    series: VideoSeries,
    plan: dict[str, Any],
    active_tab: str,
    db: AsyncSession,
) -> list[VideoSeriesEpisode]:
    existing_workspace_state = series.workspace_state if isinstance(series.workspace_state, dict) else {}
    existing_storyboard = existing_workspace_state.get("storyboard") if isinstance(existing_workspace_state.get("storyboard"), dict) else {}

    series.title = str(plan.get("title") or series.title or "Nova serie").strip() or "Nova serie"
    series.description = str(plan.get("project_overview") or series.description or "").strip()
    series.aspect_ratio = str(plan.get("aspect_ratio") or series.aspect_ratio or "16:9").strip() or "16:9"
    series.language = str(plan.get("language") or series.language or "pt-BR").strip() or "pt-BR"
    series.target_duration_seconds = float(plan.get("target_duration_seconds") or series.target_duration_seconds or 0)

    project_plan = {
        "overview": str(plan.get("project_overview") or series.description or "").strip(),
        "story_treatment": str(plan.get("story_treatment") or plan.get("project_overview") or series.description or "").strip(),
        "requirements": [str(item).strip() for item in (plan.get("build_requirements") or []) if str(item).strip()],
        "last_generated_at": datetime.utcnow().isoformat(),
        "last_generated_brief": str(plan.get("source_message") or "").strip(),
    }

    workspace_state = dict(existing_workspace_state)
    workspace_state["active_tab"] = _normalize_series_tab(active_tab)
    workspace_state["project_plan"] = project_plan
    workspace_state["characters"] = _merge_named_workspace_items(
        existing_workspace_state.get("characters"),
        plan.get("characters"),
        preserve_fields=("persona_profile_id", "voice_profile_id", "image_url", "image_path", "prompt_text"),
    )
    workspace_state["storyboard"] = {
        "scenes": _merge_named_workspace_items(
            existing_storyboard.get("scenes"),
            plan.get("scenes"),
            preserve_fields=("image_url", "image_path", "upload_id", "file_name"),
        ),
        "objects": _merge_named_workspace_items(
            existing_storyboard.get("objects"),
            plan.get("objects"),
            preserve_fields=("image_url", "image_path", "upload_id", "file_name"),
        ),
    }
    series.workspace_state = workspace_state

    plan_episodes = [item for item in (plan.get("episodes") if isinstance(plan.get("episodes"), list) else []) if isinstance(item, dict)]
    desired_episode_count = _normalize_positive_int(len(plan_episodes), 1)
    if series.kind == "film":
        desired_episode_count = 1
        plan_episodes = plan_episodes[:1]
    series.episode_count = desired_episode_count

    existing_episodes = await _list_series_episodes(series.id, db)
    touched: list[VideoSeriesEpisode] = []
    for index in range(desired_episode_count):
        episode_plan = plan_episodes[index] if index < len(plan_episodes) else {}
        scenes = [item for item in (episode_plan.get("scenes") if isinstance(episode_plan.get("scenes"), list) else []) if isinstance(item, dict)]
        if index < len(existing_episodes):
            episode = existing_episodes[index]
        else:
            episode = VideoSeriesEpisode(
                series_id=series.id,
                season_number=1,
                episode_number=index + 1,
                title=f"Episodio {index + 1}",
            )
            db.add(episode)
        episode.season_number = 1
        episode.episode_number = int(episode_plan.get("episode_number") or (index + 1))
        episode.title = str(episode_plan.get("title") or episode.title or f"Episodio {index + 1}").strip() or f"Episodio {index + 1}"
        episode.synopsis = str(episode_plan.get("synopsis") or episode.synopsis or "").strip()
        episode.script_text = _build_episode_script_text(scenes)
        episode.storyboard = scenes
        episode.timeline_data = {
            "duration_seconds": int(round(float(episode_plan.get("duration_seconds") or 0))) if episode_plan.get("duration_seconds") else 0,
            "scene_count": len(scenes),
            "scene_titles": [str(scene.get("title") or "").strip() for scene in scenes if str(scene.get("title") or "").strip()],
        }
        if str(episode.status or "draft").strip().lower() in {"", "draft"}:
            episode.status = "planned"
        touched.append(episode)

    for extra in existing_episodes[desired_episode_count:]:
        await db.delete(extra)

    await db.commit()
    await db.refresh(series)
    return await _list_series_episodes(series.id, db)


def _serialize_series(series: VideoSeries) -> dict[str, Any]:
    return {
        "id": series.id,
        "kind": series.kind,
        "title": series.title,
        "description": series.description or "",
        "status": series.status,
        "aspect_ratio": series.aspect_ratio,
        "language": series.language,
        "target_duration_seconds": float(series.target_duration_seconds or 0),
        "episode_count": int(series.episode_count or 0),
        "cover_image_path": series.cover_image_path or "",
        "default_settings": series.default_settings or {},
        "workspace_state": series.workspace_state or {},
        "created_at": series.created_at.isoformat() if series.created_at else None,
        "updated_at": series.updated_at.isoformat() if series.updated_at else None,
    }


def _serialize_episode(episode: VideoSeriesEpisode) -> dict[str, Any]:
    return {
        "id": episode.id,
        "series_id": episode.series_id,
        "video_project_id": episode.video_project_id,
        "season_number": int(episode.season_number or 1),
        "episode_number": int(episode.episode_number or 1),
        "title": episode.title,
        "synopsis": episode.synopsis or "",
        "script_text": episode.script_text or "",
        "status": episode.status,
        "storyboard": episode.storyboard or [],
        "timeline_data": episode.timeline_data or {},
        "selected_persona_ids": episode.selected_persona_ids or [],
        "created_at": episode.created_at.isoformat() if episode.created_at else None,
        "updated_at": episode.updated_at.isoformat() if episode.updated_at else None,
    }


def _serialize_thread(thread: VideoSeriesChatThread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "series_id": thread.series_id,
        "title": thread.title,
        "is_default": bool(thread.is_default),
        "created_at": thread.created_at.isoformat() if thread.created_at else None,
        "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
    }


def _serialize_message(message: VideoSeriesChatMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "thread_id": message.thread_id,
        "role": message.role,
        "content": message.content or "",
        "actions": message.actions or [],
        "status": message.status,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


async def _get_owned_series(series_id: int, user_id: int, db: AsyncSession) -> VideoSeries:
    await _ensure_series_workspace_schema()
    series = await db.get(VideoSeries, series_id)
    if not series or int(series.user_id or 0) != int(user_id or 0):
        raise HTTPException(status_code=404, detail="Workspace de series nao encontrado.")
    return series


async def _get_owned_episode(series_id: int, episode_id: int, user_id: int, db: AsyncSession) -> tuple[VideoSeries, VideoSeriesEpisode]:
    series = await _get_owned_series(series_id, user_id, db)
    episode = await db.get(VideoSeriesEpisode, episode_id)
    if not episode or int(episode.series_id or 0) != int(series.id or 0):
        raise HTTPException(status_code=404, detail="Episodio nao encontrado.")
    return series, episode


async def _get_owned_thread(series_id: int, thread_id: int, user_id: int, db: AsyncSession) -> tuple[VideoSeries, VideoSeriesChatThread]:
    series = await _get_owned_series(series_id, user_id, db)
    thread = await db.get(VideoSeriesChatThread, thread_id)
    if not thread or int(thread.series_id or 0) != int(series.id or 0):
        raise HTTPException(status_code=404, detail="Conversa nao encontrada.")
    return series, thread


async def _list_series_episodes(series_id: int, db: AsyncSession) -> list[VideoSeriesEpisode]:
    result = await db.execute(
        select(VideoSeriesEpisode)
        .where(VideoSeriesEpisode.series_id == series_id)
        .order_by(VideoSeriesEpisode.season_number.asc(), VideoSeriesEpisode.episode_number.asc(), VideoSeriesEpisode.id.asc())
    )
    return list(result.scalars().all())


async def _list_series_threads(series_id: int, db: AsyncSession) -> list[VideoSeriesChatThread]:
    result = await db.execute(
        select(VideoSeriesChatThread)
        .where(VideoSeriesChatThread.series_id == series_id)
        .order_by(VideoSeriesChatThread.is_default.desc(), VideoSeriesChatThread.updated_at.desc(), VideoSeriesChatThread.id.desc())
    )
    return list(result.scalars().all())


async def _list_thread_messages(thread_id: int, db: AsyncSession) -> list[VideoSeriesChatMessage]:
    result = await db.execute(
        select(VideoSeriesChatMessage)
        .where(VideoSeriesChatMessage.thread_id == thread_id)
        .order_by(VideoSeriesChatMessage.created_at.asc(), VideoSeriesChatMessage.id.asc())
    )
    return list(result.scalars().all())


async def _touch_thread(thread: VideoSeriesChatThread, db: AsyncSession) -> None:
    thread.updated_at = datetime.utcnow()
    await db.flush()


async def _ensure_video_project_owned(project_id: int | None, user_id: int, db: AsyncSession) -> int | None:
    if not project_id:
        return None
    project = await db.get(VideoProject, project_id)
    if not project or int(project.user_id or 0) != int(user_id or 0):
        raise HTTPException(status_code=404, detail="Projeto de video nao encontrado para vinculo com episodio.")
    return int(project.id)


@router.post("", response_model=dict)
async def create_series_workspace(
    req: CreateSeriesRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_series_workspace_schema()
    kind = _normalize_series_kind(req.kind)
    requested_count = _normalize_positive_int(req.episode_count, 1)
    episode_count = 1 if kind == "film" else requested_count
    title = str(req.title or "").strip() or "Nova serie"

    series = VideoSeries(
        user_id=user["id"],
        kind=kind,
        title=title,
        description=req.description,
        aspect_ratio=req.aspect_ratio,
        language=req.language,
        target_duration_seconds=req.target_duration_seconds,
        episode_count=episode_count,
        cover_image_path=req.cover_image_path,
        default_settings=req.default_settings,
        workspace_state=req.workspace_state,
    )
    db.add(series)
    await db.flush()

    default_thread = VideoSeriesChatThread(
        series_id=series.id,
        title="Novo bate-papo",
        is_default=True,
    )
    db.add(default_thread)

    episode_rows: list[VideoSeriesEpisode] = []
    for index in range(episode_count):
        episode_number = index + 1
        if kind == "film":
            episode_title = title
        elif kind == "drama":
            episode_title = f"Capitulo {episode_number}"
        else:
            episode_title = f"Episodio {episode_number}"
        episode_rows.append(
            VideoSeriesEpisode(
                series_id=series.id,
                season_number=1,
                episode_number=episode_number,
                title=episode_title,
            )
        )
    db.add_all(episode_rows)

    await db.commit()
    await db.refresh(series)
    await db.refresh(default_thread)
    return {
        "series": _serialize_series(series),
        "episodes": [_serialize_episode(episode) for episode in episode_rows],
        "active_thread": _serialize_thread(default_thread),
    }


@router.get("", response_model=dict)
async def list_series_workspaces(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_series_workspace_schema()
    result = await db.execute(
        select(VideoSeries)
        .where(VideoSeries.user_id == user["id"])
        .order_by(VideoSeries.updated_at.desc(), VideoSeries.id.desc())
    )
    items = list(result.scalars().all())
    return {"items": [_serialize_series(item) for item in items]}


@router.get("/{series_id}", response_model=dict)
async def get_series_workspace(
    series_id: int,
    thread_id: int | None = Query(default=None),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    series = await _get_owned_series(series_id, user["id"], db)
    episodes = await _list_series_episodes(series.id, db)
    threads = await _list_series_threads(series.id, db)
    active_thread = None
    if threads:
        active_thread = next((thread for thread in threads if int(thread.id) == int(thread_id or 0)), None)
        if active_thread is None:
            active_thread = next((thread for thread in threads if thread.is_default), None) or threads[0]
    messages = await _list_thread_messages(active_thread.id, db) if active_thread else []

    return {
        "series": _serialize_series(series),
        "episodes": [_serialize_episode(episode) for episode in episodes],
        "threads": [_serialize_thread(thread) for thread in threads],
        "active_thread": _serialize_thread(active_thread) if active_thread else None,
        "messages": [_serialize_message(message) for message in messages],
    }


@router.patch("/{series_id}", response_model=dict)
async def update_series_workspace(
    series_id: int,
    req: UpdateSeriesRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    series = await _get_owned_series(series_id, user["id"], db)
    if req.kind is not None:
        series.kind = _normalize_series_kind(req.kind)
    if req.title is not None:
        series.title = str(req.title or "").strip() or series.title
    if req.description is not None:
        series.description = req.description
    if req.status is not None:
        series.status = str(req.status or "draft").strip() or "draft"
    if req.aspect_ratio is not None:
        series.aspect_ratio = req.aspect_ratio
    if req.language is not None:
        series.language = req.language
    if req.target_duration_seconds is not None:
        series.target_duration_seconds = req.target_duration_seconds
    if req.cover_image_path is not None:
        series.cover_image_path = req.cover_image_path
    if req.default_settings is not None:
        series.default_settings = req.default_settings
    if req.workspace_state is not None:
        series.workspace_state = req.workspace_state

    await db.commit()
    await db.refresh(series)
    return {"series": _serialize_series(series)}


@router.post("/{series_id}/plan", response_model=dict)
async def generate_series_workspace_plan(
    series_id: int,
    req: GenerateSeriesPlanRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    series = await _get_owned_series(series_id, user["id"], db)
    message = str(req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Envie uma descricao para estruturar o projeto.")

    active_tab = _normalize_series_tab(req.tab)
    plan = await build_series_workspace_plan(
        kind=_normalize_series_kind(series.kind),
        existing_title=str(series.title or "").strip(),
        message=message,
        language=str(series.language or "pt-BR").strip() or "pt-BR",
        target_tab=active_tab,
        existing_context=series.workspace_state if isinstance(series.workspace_state, dict) else {},
    )
    plan["source_message"] = message
    episodes = await _apply_generated_series_plan(series=series, plan=plan, active_tab=active_tab, db=db)
    return {
        "series": _serialize_series(series),
        "episodes": [_serialize_episode(item) for item in episodes],
        "assistant_message": str(plan.get("assistant_reply") or "").strip(),
    }


@router.post("/{series_id}/episodes", response_model=dict)
async def create_series_episode(
    series_id: int,
    req: CreateSeriesEpisodeRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    series = await _get_owned_series(series_id, user["id"], db)
    episodes = await _list_series_episodes(series.id, db)
    next_episode_number = len(episodes) + 1
    season_number = _normalize_positive_int(req.season_number, 1)
    episode_number = _normalize_positive_int(req.episode_number, next_episode_number)
    linked_project_id = await _ensure_video_project_owned(req.video_project_id, user["id"], db)
    title = str(req.title or "").strip() or f"Episodio {episode_number}"

    episode = VideoSeriesEpisode(
        series_id=series.id,
        video_project_id=linked_project_id,
        season_number=season_number,
        episode_number=episode_number,
        title=title,
        synopsis=req.synopsis,
        script_text=req.script_text,
        status=req.status,
        storyboard=req.storyboard,
        timeline_data=req.timeline_data,
        selected_persona_ids=req.selected_persona_ids,
    )
    db.add(episode)
    series.episode_count = len(episodes) + 1

    await db.commit()
    await db.refresh(episode)
    await db.refresh(series)
    return {
        "series": _serialize_series(series),
        "episode": _serialize_episode(episode),
    }


@router.patch("/{series_id}/episodes/{episode_id}", response_model=dict)
async def update_series_episode(
    series_id: int,
    episode_id: int,
    req: UpdateSeriesEpisodeRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _, episode = await _get_owned_episode(series_id, episode_id, user["id"], db)
    if req.title is not None:
        episode.title = str(req.title or "").strip() or episode.title
    if req.synopsis is not None:
        episode.synopsis = req.synopsis
    if req.script_text is not None:
        episode.script_text = req.script_text
    if req.season_number is not None:
        episode.season_number = _normalize_positive_int(req.season_number, episode.season_number)
    if req.episode_number is not None:
        episode.episode_number = _normalize_positive_int(req.episode_number, episode.episode_number)
    if req.status is not None:
        episode.status = str(req.status or episode.status).strip() or episode.status
    if req.storyboard is not None:
        episode.storyboard = req.storyboard
    if req.timeline_data is not None:
        episode.timeline_data = req.timeline_data
    if req.selected_persona_ids is not None:
        episode.selected_persona_ids = req.selected_persona_ids
    if req.video_project_id is not None:
        episode.video_project_id = await _ensure_video_project_owned(req.video_project_id, user["id"], db)

    await db.commit()
    await db.refresh(episode)
    return {"episode": _serialize_episode(episode)}


@router.post("/{series_id}/episodes/reorder", response_model=dict)
async def reorder_series_episodes(
    series_id: int,
    req: ReorderSeriesEpisodesRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    series = await _get_owned_series(series_id, user["id"], db)
    episodes = await _list_series_episodes(series.id, db)
    episode_map = {int(episode.id): episode for episode in episodes}
    ordered_ids = [episode_id for episode_id in req.episode_ids if int(episode_id or 0) in episode_map]
    remaining_ids = [episode.id for episode in episodes if int(episode.id) not in ordered_ids]
    final_ids = ordered_ids + remaining_ids
    season_number = _normalize_positive_int(req.season_number, 1)

    for index, episode_id in enumerate(final_ids, start=1):
        episode = episode_map[int(episode_id)]
        episode.season_number = season_number
        episode.episode_number = index

    series.episode_count = len(final_ids)
    await db.commit()

    refreshed = await _list_series_episodes(series.id, db)
    return {
        "series": _serialize_series(series),
        "episodes": [_serialize_episode(episode) for episode in refreshed],
    }


@router.get("/{series_id}/threads", response_model=dict)
async def list_series_threads(
    series_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    series = await _get_owned_series(series_id, user["id"], db)
    threads = await _list_series_threads(series.id, db)
    return {"items": [_serialize_thread(thread) for thread in threads]}


@router.post("/{series_id}/threads", response_model=dict)
async def create_series_thread(
    series_id: int,
    req: CreateSeriesThreadRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    series = await _get_owned_series(series_id, user["id"], db)
    thread = VideoSeriesChatThread(
        series_id=series.id,
        title=str(req.title or "").strip() or "Novo bate-papo",
        is_default=False,
    )
    db.add(thread)
    await db.commit()
    await db.refresh(thread)
    return {"thread": _serialize_thread(thread)}


@router.get("/{series_id}/threads/{thread_id}/messages", response_model=dict)
async def list_series_thread_messages(
    series_id: int,
    thread_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _, thread = await _get_owned_thread(series_id, thread_id, user["id"], db)
    messages = await _list_thread_messages(thread.id, db)
    return {
        "thread": _serialize_thread(thread),
        "messages": [_serialize_message(message) for message in messages],
    }


@router.post("/{series_id}/threads/{thread_id}/messages", response_model=dict)
async def create_series_thread_message(
    series_id: int,
    thread_id: int,
    req: CreateSeriesMessageRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _, thread = await _get_owned_thread(series_id, thread_id, user["id"], db)
    role = str(req.role or "user").strip().lower() or "user"
    if role not in {"user", "assistant", "system"}:
        role = "user"
    message = VideoSeriesChatMessage(
        thread_id=thread.id,
        role=role,
        content=str(req.content or "").strip(),
        actions=req.actions,
        status=str(req.status or "completed").strip() or "completed",
    )
    db.add(message)
    await db.flush()
    await _touch_thread(thread, db)
    await db.commit()
    await db.refresh(message)
    await db.refresh(thread)
    return {
        "thread": _serialize_thread(thread),
        "message": _serialize_message(message),
    }