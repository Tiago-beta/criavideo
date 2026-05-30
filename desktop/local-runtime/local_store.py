from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import VideoProject, VideoRender, VideoStatus


LOCAL_USER_ID = 1
LOCAL_PROJECT_ID_BASE = 900_000_000_000
LOCAL_RENDER_ID_BASE = 1


def resolve_default_runtime_data_dir() -> Path:
    local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data) / "CriaVideo Desktop" / "runtime-data"
    if os.name == "nt":
        return Path.home() / "AppData" / "Local" / "CriaVideo Desktop" / "runtime-data"
    return Path.home() / ".criavideo-desktop" / "runtime-data"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utcnow().isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _serialize_jsonable(value: Any, fallback: Any) -> Any:
    candidate = value if isinstance(value, type(fallback)) else fallback
    try:
        return json.loads(json.dumps(candidate))
    except Exception:
        return fallback


def _common_path_within(child: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath([str(child), str(parent)]) == str(parent)
    except ValueError:
        return False


class LocalProjectStore:
    def __init__(self, data_dir: Path, media_root: Path):
        self.data_dir = Path(data_dir).resolve()
        self.media_root = Path(media_root).resolve()
        self.state_path = self.data_dir / "projects-state.json"
        self._lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_root.mkdir(parents=True, exist_ok=True)

    def _default_state(self) -> dict[str, Any]:
        return {
            "next_project_id": LOCAL_PROJECT_ID_BASE,
            "next_render_id": LOCAL_RENDER_ID_BASE,
            "projects": {},
        }

    def _load_state_unlocked(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._default_state()
        try:
            with open(self.state_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return self._default_state()

        if not isinstance(payload, dict):
            return self._default_state()

        next_project_id = int(payload.get("next_project_id") or LOCAL_PROJECT_ID_BASE)
        next_render_id = int(payload.get("next_render_id") or LOCAL_RENDER_ID_BASE)
        projects = payload.get("projects") if isinstance(payload.get("projects"), dict) else {}
        return {
            "next_project_id": max(LOCAL_PROJECT_ID_BASE, next_project_id),
            "next_render_id": max(LOCAL_RENDER_ID_BASE, next_render_id),
            "projects": projects,
        }

    def _save_state_unlocked(self, state: dict[str, Any]) -> None:
        temp_path = self.state_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=True, indent=2)
        temp_path.replace(self.state_path)

    def _next_project_id_unlocked(self, state: dict[str, Any]) -> int:
        next_project_id = int(state.get("next_project_id") or LOCAL_PROJECT_ID_BASE)
        state["next_project_id"] = next_project_id + 1
        return next_project_id

    def _next_render_id_unlocked(self, state: dict[str, Any]) -> int:
        next_render_id = int(state.get("next_render_id") or LOCAL_RENDER_ID_BASE)
        state["next_render_id"] = next_render_id + 1
        return next_render_id

    def build_media_url(self, path: str | Path | None) -> str | None:
        if not path:
            return None
        candidate = Path(path).resolve()
        if not candidate.exists() or not _common_path_within(candidate, self.media_root):
            return None
        rel = candidate.relative_to(self.media_root).as_posix().lstrip("/")
        return f"/video/media/{rel}"

    def resolve_media_path(self, relative_path: str | None) -> Path | None:
        raw = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
        if not raw:
            return None
        candidate = (self.media_root / raw).resolve()
        if not _common_path_within(candidate, self.media_root):
            return None
        return candidate

    def adopt_file(self, source_path: Path, relative_dir: str, prefix: str) -> Path:
        source = Path(source_path).resolve()
        if not source.exists():
            raise FileNotFoundError(str(source))
        target_dir = (self.media_root / relative_dir).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{prefix}_{uuid.uuid4().hex[:10]}{source.suffix.lower() or '.bin'}"
        shutil.copy2(source, target_path)
        return target_path

    def assign_missing_ids(self, objects: list[Any]) -> None:
        with self._lock:
            state = self._load_state_unlocked()
            dirty = False
            for obj in objects:
                if isinstance(obj, VideoProject) and not getattr(obj, "id", None):
                    obj.id = self._next_project_id_unlocked(state)
                    dirty = True
                elif isinstance(obj, VideoRender) and not getattr(obj, "id", None):
                    obj.id = self._next_render_id_unlocked(state)
                    dirty = True
            if dirty:
                self._save_state_unlocked(state)

    def persist_orm_objects(self, objects: list[Any]) -> list[int]:
        persisted_project_ids: list[int] = []
        with self._lock:
            state = self._load_state_unlocked()
            records = state.setdefault("projects", {})
            now_iso = _iso_now()

            for project in [obj for obj in objects if isinstance(obj, VideoProject)]:
                project_id = int(getattr(project, "id", 0) or 0)
                if project_id <= 0:
                    project_id = self._next_project_id_unlocked(state)
                    project.id = project_id
                key = str(project_id)
                current = records.get(key) if isinstance(records.get(key), dict) else {}
                created_at = (
                    getattr(project, "created_at", None)
                    and getattr(project, "created_at").isoformat()
                ) or current.get("created_at") or now_iso
                tags = getattr(project, "tags", current.get("tags", {}))
                record = {
                    "id": project_id,
                    "user_id": int(getattr(project, "user_id", LOCAL_USER_ID) or LOCAL_USER_ID),
                    "track_id": int(getattr(project, "track_id", 0) or 0),
                    "title": str(getattr(project, "title", "Projeto local") or "Projeto local"),
                    "description": str(getattr(project, "description", "") or ""),
                    "tags": _serialize_jsonable(tags, {} if isinstance(tags, dict) else []),
                    "style_prompt": str(getattr(project, "style_prompt", "") or ""),
                    "aspect_ratio": str(getattr(project, "aspect_ratio", "16:9") or "16:9"),
                    "status": getattr(getattr(project, "status", VideoStatus.COMPLETED), "value", str(getattr(project, "status", "completed") or "completed")),
                    "progress": int(getattr(project, "progress", 100) or 100),
                    "track_title": str(getattr(project, "track_title", "") or ""),
                    "track_artist": str(getattr(project, "track_artist", "") or ""),
                    "track_duration": float(getattr(project, "track_duration", 0) or 0),
                    "lyrics_text": str(getattr(project, "lyrics_text", "") or ""),
                    "lyrics_words": _serialize_jsonable(getattr(project, "lyrics_words", current.get("lyrics_words", [])), []),
                    "audio_path": str(getattr(project, "audio_path", "") or ""),
                    "use_custom_images": bool(getattr(project, "use_custom_images", False)),
                    "use_custom_video": bool(getattr(project, "use_custom_video", True)),
                    "enable_subtitles": bool(getattr(project, "enable_subtitles", True)),
                    "zoom_images": bool(getattr(project, "zoom_images", True)),
                    "image_display_seconds": float(getattr(project, "image_display_seconds", 0) or 0),
                    "no_background_music": bool(getattr(project, "no_background_music", False)),
                    "is_karaoke": bool(getattr(project, "is_karaoke", False)),
                    "is_realistic": bool(getattr(project, "is_realistic", False)),
                    "error_message": str(getattr(project, "error_message", "") or ""),
                    "created_at": created_at,
                    "updated_at": now_iso,
                    "renders": list(current.get("renders") or []),
                    "scenes": list(current.get("scenes") or []),
                    "source_layers": list(current.get("source_layers") or []),
                }
                records[key] = record
                persisted_project_ids.append(project_id)

            for render in [obj for obj in objects if isinstance(obj, VideoRender)]:
                project_id = int(getattr(render, "project_id", 0) or 0)
                if project_id <= 0:
                    continue
                key = str(project_id)
                if key not in records:
                    records[key] = {
                        "id": project_id,
                        "user_id": LOCAL_USER_ID,
                        "track_id": 0,
                        "title": f"Projeto {project_id}",
                        "description": "",
                        "tags": {"type": "editor", "local_runtime": True},
                        "style_prompt": "",
                        "aspect_ratio": str(getattr(render, "format", "16:9") or "16:9"),
                        "status": VideoStatus.COMPLETED.value,
                        "progress": 100,
                        "track_title": "",
                        "track_artist": "",
                        "track_duration": float(getattr(render, "duration", 0) or 0),
                        "lyrics_text": "",
                        "lyrics_words": [],
                        "audio_path": "",
                        "use_custom_images": False,
                        "use_custom_video": True,
                        "enable_subtitles": True,
                        "zoom_images": True,
                        "image_display_seconds": 0,
                        "no_background_music": False,
                        "is_karaoke": False,
                        "is_realistic": False,
                        "error_message": "",
                        "created_at": now_iso,
                        "updated_at": now_iso,
                        "renders": [],
                        "scenes": [],
                        "source_layers": [],
                    }

                render_id = int(getattr(render, "id", 0) or 0)
                if render_id <= 0:
                    render_id = self._next_render_id_unlocked(state)
                    render.id = render_id

                record = records[key]
                created_at = (
                    getattr(render, "created_at", None)
                    and getattr(render, "created_at").isoformat()
                ) or now_iso
                render_payload = {
                    "id": render_id,
                    "project_id": project_id,
                    "format": str(getattr(render, "format", record.get("aspect_ratio", "16:9")) or record.get("aspect_ratio", "16:9")),
                    "file_path": str(getattr(render, "file_path", "") or ""),
                    "file_size": int(getattr(render, "file_size", 0) or 0),
                    "thumbnail_path": str(getattr(render, "thumbnail_path", "") or ""),
                    "duration": float(getattr(render, "duration", 0) or 0),
                    "created_at": created_at,
                }
                existing = [item for item in list(record.get("renders") or []) if int(item.get("id") or 0) != render_id]
                existing.insert(0, render_payload)
                existing.sort(
                    key=lambda item: _parse_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )
                record["renders"] = existing
                record["updated_at"] = now_iso
                if not record.get("track_duration"):
                    record["track_duration"] = float(render_payload.get("duration") or 0)

            self._save_state_unlocked(state)
        return persisted_project_ids

    def _copy_record(self, record: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(record))

    def is_local_project(self, project_id: int) -> bool:
        with self._lock:
            state = self._load_state_unlocked()
            return str(int(project_id or 0)) in state.get("projects", {})

    def attach_source_layers(self, project_id: int, layers: list[dict[str, Any]]) -> None:
        safe_project_id = int(project_id or 0)
        if safe_project_id <= 0:
            return
        cleaned_layers: list[dict[str, Any]] = []
        for layer in list(layers or []):
            path = str(layer.get("path") or "").strip()
            if not path:
                continue
            cleaned_layers.append(
                {
                    "path": path,
                    "kind": str(layer.get("kind") or "").strip(),
                    "name": str(layer.get("name") or "").strip(),
                    "duration": float(layer.get("duration") or 0),
                }
            )
        with self._lock:
            state = self._load_state_unlocked()
            record = state.get("projects", {}).get(str(safe_project_id))
            if not isinstance(record, dict):
                return
            record["source_layers"] = cleaned_layers
            record["updated_at"] = _iso_now()
            self._save_state_unlocked(state)

    def list_project_summaries(self) -> list[dict[str, Any]]:
        with self._lock:
            state = self._load_state_unlocked()
            records = [self._copy_record(item) for item in state.get("projects", {}).values() if isinstance(item, dict)]

        def _latest_render(record: dict[str, Any]) -> dict[str, Any] | None:
            renders = list(record.get("renders") or [])
            if not renders:
                return None
            renders.sort(
                key=lambda item: _parse_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            return renders[0]

        payload = []
        for record in records:
            latest_render = _latest_render(record)
            payload.append(
                {
                    "id": int(record.get("id") or 0),
                    "title": str(record.get("title") or "Projeto local"),
                    "track_title": str(record.get("track_title") or ""),
                    "track_artist": str(record.get("track_artist") or ""),
                    "status": str(record.get("status") or VideoStatus.COMPLETED.value),
                    "progress": int(record.get("progress") or 100),
                    "aspect_ratio": str(record.get("aspect_ratio") or "16:9"),
                    "error_message": str(record.get("error_message") or ""),
                    "created_at": record.get("created_at"),
                    "render_created_at": latest_render.get("created_at") if latest_render else None,
                    "video_expired": False,
                    "lyrics_text": str(record.get("lyrics_text") or ""),
                    "style_prompt": str(record.get("style_prompt") or ""),
                    "thumbnail_url": self.build_media_url(latest_render.get("thumbnail_path")) if latest_render else None,
                    "duration": float((latest_render or {}).get("duration") or record.get("track_duration") or 0),
                    "workflow_type": "editor",
                    "workflow_stage": "",
                    "is_editor_project": True,
                    "hide_from_create_list": False,
                    "is_local_project": True,
                }
            )

        payload.sort(
            key=lambda item: _parse_datetime(item.get("render_created_at") or item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        return payload

    def get_project_record(self, project_id: int) -> dict[str, Any] | None:
        safe_project_id = int(project_id or 0)
        with self._lock:
            state = self._load_state_unlocked()
            record = state.get("projects", {}).get(str(safe_project_id))
            if not isinstance(record, dict):
                return None
            return self._copy_record(record)

    def get_project_detail(self, project_id: int) -> dict[str, Any] | None:
        record = self.get_project_record(project_id)
        if not record:
            return None
        tags = record.get("tags") if isinstance(record.get("tags"), dict) else {}
        preview_video_path = str(tags.get("editor_preview_video_path") or "").strip()
        preview_video_url = self.build_media_url(preview_video_path) if preview_video_path else None
        renders_payload = []
        for render in list(record.get("renders") or []):
            renders_payload.append(
                {
                    "id": int(render.get("id") or 0),
                    "format": str(render.get("format") or record.get("aspect_ratio") or "16:9"),
                    "file_path": str(render.get("file_path") or ""),
                    "file_size": int(render.get("file_size") or 0) or None,
                    "thumbnail_path": str(render.get("thumbnail_path") or ""),
                    "duration": float(render.get("duration") or 0) or None,
                    "created_at": render.get("created_at"),
                    "video_url": self.build_media_url(render.get("file_path")),
                    "thumbnail_url": self.build_media_url(render.get("thumbnail_path")),
                }
            )
        return {
            "id": int(record.get("id") or 0),
            "title": str(record.get("title") or "Projeto local"),
            "description": str(record.get("description") or ""),
            "lyrics_text": str(record.get("lyrics_text") or ""),
            "tags": tags,
            "preview_video_url": preview_video_url,
            "source_image_urls": [],
            "status": str(record.get("status") or VideoStatus.COMPLETED.value),
            "progress": int(record.get("progress") or 100),
            "aspect_ratio": str(record.get("aspect_ratio") or "16:9"),
            "track_title": str(record.get("track_title") or ""),
            "track_artist": str(record.get("track_artist") or ""),
            "track_duration": float(record.get("track_duration") or 0) or None,
            "error_message": str(record.get("error_message") or ""),
            "created_at": record.get("created_at"),
            "scenes": list(record.get("scenes") or []),
            "renders": renders_payload,
            "is_local_project": True,
        }

    def rename_project(self, project_id: int, title: str) -> dict[str, Any] | None:
        safe_project_id = int(project_id or 0)
        new_title = str(title or "").strip()
        if not new_title:
            return None
        with self._lock:
            state = self._load_state_unlocked()
            record = state.get("projects", {}).get(str(safe_project_id))
            if not isinstance(record, dict):
                return None
            record["title"] = new_title[:500]
            record["updated_at"] = _iso_now()
            self._save_state_unlocked(state)
            return {"id": safe_project_id, "title": record["title"]}

    def delete_project(self, project_id: int) -> bool:
        safe_project_id = int(project_id or 0)
        files_to_delete: list[Path] = []
        directories_to_delete: list[Path] = []

        with self._lock:
            state = self._load_state_unlocked()
            record = state.get("projects", {}).pop(str(safe_project_id), None)
            if not isinstance(record, dict):
                return False
            self._save_state_unlocked(state)

        for render in list(record.get("renders") or []):
            for key in ("file_path", "thumbnail_path"):
                raw_path = str(render.get(key) or "").strip()
                if raw_path:
                    files_to_delete.append(Path(raw_path))

        tags = record.get("tags") if isinstance(record.get("tags"), dict) else {}
        preview_video_path = str(tags.get("editor_preview_video_path") or "").strip()
        if preview_video_path:
            files_to_delete.append(Path(preview_video_path))

        for layer in list(record.get("source_layers") or []):
            raw_path = str(layer.get("path") or "").strip()
            if raw_path:
                files_to_delete.append(Path(raw_path))

        directories_to_delete.extend(
            [
                self.media_root / str(safe_project_id),
                self.media_root / "images" / str(safe_project_id),
            ]
        )

        for file_path in files_to_delete:
            candidate = Path(file_path).resolve()
            if candidate.exists() and _common_path_within(candidate, self.media_root):
                candidate.unlink(missing_ok=True)

        for directory in directories_to_delete:
            candidate = Path(directory).resolve()
            if candidate.exists() and _common_path_within(candidate, self.media_root):
                shutil.rmtree(candidate, ignore_errors=True)

        return True

    def build_project_objects(self, project_id: int) -> tuple[VideoProject, VideoRender]:
        record = self.get_project_record(project_id)
        if not record:
            raise KeyError(project_id)

        project = VideoProject(
            user_id=int(record.get("user_id") or LOCAL_USER_ID),
            track_id=int(record.get("track_id") or 0),
            title=str(record.get("title") or "Projeto local"),
            description=str(record.get("description") or ""),
            tags=record.get("tags") if isinstance(record.get("tags"), (dict, list)) else {},
            style_prompt=str(record.get("style_prompt") or ""),
            aspect_ratio=str(record.get("aspect_ratio") or "16:9"),
            status=VideoStatus(str(record.get("status") or VideoStatus.COMPLETED.value)),
            progress=int(record.get("progress") or 100),
            track_title=str(record.get("track_title") or ""),
            track_artist=str(record.get("track_artist") or ""),
            track_duration=float(record.get("track_duration") or 0) or None,
            lyrics_text=str(record.get("lyrics_text") or ""),
            lyrics_words=record.get("lyrics_words") if isinstance(record.get("lyrics_words"), list) else [],
            audio_path=str(record.get("audio_path") or ""),
            use_custom_images=bool(record.get("use_custom_images")),
            use_custom_video=bool(record.get("use_custom_video", True)),
            enable_subtitles=bool(record.get("enable_subtitles", True)),
            zoom_images=bool(record.get("zoom_images", True)),
            image_display_seconds=float(record.get("image_display_seconds") or 0),
            no_background_music=bool(record.get("no_background_music")),
            is_karaoke=bool(record.get("is_karaoke")),
            is_realistic=bool(record.get("is_realistic")),
        )
        project.id = int(record.get("id") or 0)
        project.error_message = str(record.get("error_message") or "")
        project.created_at = _parse_datetime(record.get("created_at"))
        project.updated_at = _parse_datetime(record.get("updated_at"))

        renders = list(record.get("renders") or [])
        renders.sort(
            key=lambda item: _parse_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        render_record = next((item for item in renders if str(item.get("file_path") or "").strip()), None)
        if not render_record:
            raise KeyError(project_id)

        render = VideoRender(
            project_id=project.id,
            format=str(render_record.get("format") or project.aspect_ratio or "16:9"),
            file_path=str(render_record.get("file_path") or ""),
            file_size=int(render_record.get("file_size") or 0) or None,
            thumbnail_path=str(render_record.get("thumbnail_path") or ""),
            duration=float(render_record.get("duration") or 0) or None,
        )
        render.id = int(render_record.get("id") or 0)
        render.created_at = _parse_datetime(render_record.get("created_at"))
        return project, render


class LocalAsyncSession:
    def __init__(self, store: LocalProjectStore):
        self.store = store
        self._added: list[Any] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def add(self, obj: Any) -> None:
        self._added.append(obj)

    async def flush(self) -> None:
        self.store.assign_missing_ids(self._added)

    async def commit(self) -> None:
        self.store.persist_orm_objects(self._added)
        self._added = []

    async def refresh(self, obj: Any) -> Any:
        return obj


class LocalAsyncSessionFactory:
    def __init__(self, store: LocalProjectStore):
        self.store = store

    def __call__(self) -> LocalAsyncSession:
        return LocalAsyncSession(self.store)