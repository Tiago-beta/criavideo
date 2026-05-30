import asyncio
import logging
import os
import shutil
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app.database as app_database
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.routers import editor as editor_router

from local_store import LOCAL_USER_ID, LocalAsyncSession, LocalAsyncSessionFactory, LocalProjectStore, resolve_default_runtime_data_dir


RUNTIME_HOST = str(os.getenv("CRIAVIDEO_DESKTOP_RUNTIME_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
RUNTIME_PORT = int(os.getenv("CRIAVIDEO_DESKTOP_RUNTIME_PORT", "3232") or 3232)
SITE_URL = str(os.getenv("CRIAVIDEO_DESKTOP_RUNTIME_SITE_URL", "https://criavideo.pro") or "https://criavideo.pro").strip().rstrip("/")
DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
STATIC_DIR = Path(os.getenv("CRIAVIDEO_DESKTOP_RUNTIME_STATIC_DIR") or DEFAULT_STATIC_DIR).resolve()
DEFAULT_DATA_DIR = resolve_default_runtime_data_dir()
RUNTIME_DATA_DIR = Path(os.getenv("CRIAVIDEO_DESKTOP_RUNTIME_DATA_DIR") or DEFAULT_DATA_DIR).resolve()
LOCAL_MEDIA_DIR = (RUNTIME_DATA_DIR / "media").resolve()

logger = logging.getLogger(__name__)

LOCAL_PROJECT_STORE = LocalProjectStore(RUNTIME_DATA_DIR, LOCAL_MEDIA_DIR)
LOCAL_ASYNC_SESSION_FACTORY = LocalAsyncSessionFactory(LOCAL_PROJECT_STORE)
editor_router.settings.media_dir = str(LOCAL_MEDIA_DIR)
app_database.async_session = LOCAL_ASYNC_SESSION_FACTORY

if not STATIC_DIR.exists():
    raise RuntimeError(f"Static directory not found for desktop runtime: {STATIC_DIR}")


def _apply_html_no_cache_headers(response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def _static_file_response(filename: str) -> FileResponse:
    response = FileResponse(str(STATIC_DIR / filename))
    if filename.lower().endswith(".html"):
        _apply_html_no_cache_headers(response)
    return response


class DesktopStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if str(path or "").lower().endswith(".html"):
            _apply_html_no_cache_headers(response)
        return response


_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(120.0, connect=20.0))
    try:
        yield
    finally:
        client = getattr(app.state, "http_client", None)
        if client is not None:
            await client.aclose()


app = FastAPI(title="CriaVideo Desktop Runtime", version="0.1.0", lifespan=lifespan)
app.mount("/video/static", DesktopStaticFiles(directory=str(STATIC_DIR)), name="static")


def _build_upstream_url(request: Request) -> str:
    base = SITE_URL
    query = str(request.url.query or "").strip()
    if query:
        return f"{base}{request.url.path}?{query}"
    return f"{base}{request.url.path}"


def _build_forwarded_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS and key.lower() != "host"
    }


async def _request_upstream(request: Request) -> httpx.Response:
    client = app.state.http_client
    return await client.request(
        method=request.method,
        url=_build_upstream_url(request),
        content=await request.body(),
        headers=_build_forwarded_headers(request),
    )


async def _request_upstream_json(request: Request):
    upstream = await _request_upstream(request)
    if upstream.status_code >= 400:
        try:
            detail = upstream.json()
        except Exception:
            detail = upstream.text or "Falha ao acessar a API remota"
        raise HTTPException(upstream.status_code, detail)
    try:
        return upstream.json()
    except Exception as exc:
        raise HTTPException(502, f"Resposta JSON inválida do servidor remoto: {exc}")


def _local_user() -> dict[str, int]:
    return {"id": LOCAL_USER_ID}


def _local_db() -> LocalAsyncSession:
    return LocalAsyncSession(LOCAL_PROJECT_STORE)


def _build_local_projects_response(remote_projects, local_projects):
    merged: dict[int, dict] = {}
    for item in list(remote_projects or []):
        try:
            merged[int(item.get("id") or 0)] = item
        except Exception:
            continue
    for item in list(local_projects or []):
        try:
            merged[int(item.get("id") or 0)] = item
        except Exception:
            continue

    def _sort_key(item: dict):
        raw = str(item.get("render_created_at") or item.get("created_at") or "").strip()
        return raw

    return sorted(merged.values(), key=_sort_key, reverse=True)


def _local_media_response(path: str) -> FileResponse | None:
    local_path = LOCAL_PROJECT_STORE.resolve_media_path(path)
    if not local_path or not local_path.exists() or not local_path.is_file():
        return None
    return FileResponse(str(local_path))


def _load_local_project_or_none(project_id: int):
    if not LOCAL_PROJECT_STORE.is_local_project(project_id):
        return None, None
    try:
        return LOCAL_PROJECT_STORE.build_project_objects(project_id)
    except KeyError:
        raise HTTPException(404, "Projeto local não encontrado")


def _attach_local_source_layers(payload: dict) -> dict:
    project_id = int(payload.get("project_id") or 0)
    layers = payload.get("layers") if isinstance(payload.get("layers"), list) else []
    if project_id > 0 and layers:
        LOCAL_PROJECT_STORE.attach_source_layers(project_id, layers)
    return payload


async def _generate_local_tevoxi_music(req: editor_router.GenerateTevoxiMusicRequest) -> dict:
    project, _ = _load_local_project_or_none(int(req.project_id or 0))
    if not project:
        raise HTTPException(404, "Projeto local não encontrado")

    mood = editor_router._normalize_editor_tevoxi_mood(req.mood)
    mood_settings = editor_router._EDITOR_TEVOXI_MOOD_SETTINGS.get(mood, editor_router._EDITOR_TEVOXI_MOOD_SETTINGS["calmo"])
    characteristics = str(req.characteristics or "").strip()

    theme_parts: list[str] = []
    project_title = (project.title or project.track_title or "").strip()
    if project_title:
        theme_parts.append(f"Tema do vídeo: {project_title[:120]}")
    theme_parts.append(f"Direção sonora: {mood_settings['theme_hint']}")
    if characteristics:
        theme_parts.append(f"Características desejadas: {characteristics[:220]}")
    if mood == "drama" and not characteristics:
        theme_parts.append("Deixe mais agressivo e épico, com sensação de urgência cinematográfica.")
    theme_parts.append("Trilha instrumental para fundo de narração, sem voz cantada.")
    theme = " | ".join(theme_parts)

    requested_duration = float(req.duration_seconds or 0.0)
    fallback_duration = float(project.track_duration or 0.0)
    target_duration = int(round(requested_duration if requested_duration > 0 else fallback_duration))
    target_duration = max(30, min(240, target_duration or 60))

    result = await editor_router.generate_music_from_theme(
        theme=theme,
        project_id=int(project.id or 0),
        duration=target_duration,
        language="pt-BR",
        user_id=int(project.user_id or LOCAL_USER_ID),
        manual_settings={
            "music_mode": "instrumental",
            "music_genre": mood_settings["genre"],
            "music_vocalist": "",
            "music_mood": mood_settings["api_mood"],
            "music_duration": target_duration,
            "music_language": "pt-BR",
        },
    )

    raw_audio_path = Path(str(result.get("audio_path") or "").strip()).resolve()
    if not raw_audio_path.exists() or raw_audio_path.stat().st_size <= 0:
        raise HTTPException(500, "Tevoxi retornou sem arquivo de áudio válido")

    adopted_path = raw_audio_path
    if not LOCAL_PROJECT_STORE.build_media_url(adopted_path):
        adopted_path = LOCAL_PROJECT_STORE.adopt_file(
            raw_audio_path,
            f"editor_uploads/{LOCAL_USER_ID}/generated/music",
            "tevoxi_music",
        )

    media_url = LOCAL_PROJECT_STORE.build_media_url(adopted_path)
    if not media_url:
        raise HTTPException(500, "Falha ao mapear mídia local de áudio gerada")

    return {
        "path": str(adopted_path),
        "media_url": media_url,
        "title": str(result.get("title") or "Áudio IA Tevoxi"),
        "duration": float(result.get("duration") or target_duration),
        "mood": mood,
        "source": "tevoxi",
    }


def _build_local_library_layer(project_id: int) -> dict:
    project, render = _load_local_project_or_none(project_id)
    if not project or not render:
        raise HTTPException(404, "Projeto local não encontrado")

    src_video = Path(str(render.file_path or "").strip()).resolve()
    if not src_video.exists() or src_video.stat().st_size <= 0:
        raise HTTPException(400, "Arquivo do vídeo local não foi encontrado")

    dest = LOCAL_PROJECT_STORE.adopt_file(
        src_video,
        f"editor_uploads/{LOCAL_USER_ID}/layers/videos",
        f"layer_video_local_{int(project.id or 0)}",
    )
    duration, _ = editor_router._probe_video_metadata(str(dest))
    width, height = editor_router._probe_media_dimensions(str(dest))
    has_audio = editor_router._probe_has_audio_stream(str(dest))
    title = (project.title or project.track_title or f"Projeto {project.id}").strip()

    return {
        "path": str(dest),
        "media_url": LOCAL_PROJECT_STORE.build_media_url(dest),
        "duration": duration,
        "width": width,
        "height": height,
        "has_audio": has_audio,
        "name": title[:120] if title else "Vídeo local",
    }


async def _transcribe_local_project(project_id: int) -> dict:
    project, render = _load_local_project_or_none(project_id)
    if not project or not render:
        raise HTTPException(404, "Projeto local não encontrado")

    src_video = str(render.file_path or "").strip()
    if not src_video or not os.path.exists(src_video):
        raise HTTPException(400, "Arquivo de vídeo não encontrado")

    tmp_audio = editor_router._extract_editor_audio(int(project.id or 0), src_video, "transcribe")
    try:
        from app.services.transcriber import transcribe_audio

        result = await asyncio.to_thread(transcribe_audio, tmp_audio, "pt")
        return {"text": result.get("text", ""), "words": result.get("words", [])}
    finally:
        if os.path.exists(tmp_audio):
            os.remove(tmp_audio)


async def _analyze_local_smart_cuts(req: editor_router.SmartCutsRequest) -> dict:
    project, render = _load_local_project_or_none(int(req.project_id or 0))
    if not project or not render:
        raise HTTPException(404, "Projeto local não encontrado")

    src_video = str(render.file_path or "").strip()
    if not src_video or not os.path.exists(src_video):
        raise HTTPException(400, "Arquivo de vídeo não encontrado")

    duration, _ = editor_router._probe_video_metadata(src_video)
    tmp_audio = editor_router._extract_editor_audio(int(project.id or 0), src_video, "smartcuts")
    try:
        from app.services.transcriber import transcribe_audio

        transcription = await asyncio.to_thread(transcribe_audio, tmp_audio, "pt")
        cuts = await editor_router._analyze_smart_cuts_with_ai(transcription, duration)
        return {
            "text": transcription.get("text", ""),
            "words": transcription.get("words", []),
            "cuts": cuts,
        }
    finally:
        if os.path.exists(tmp_audio):
            os.remove(tmp_audio)


def _start_local_export_job(req: editor_router.ExportRequest, background_tasks: BackgroundTasks) -> dict:
    project, render = _load_local_project_or_none(int(req.project_id or 0))
    if not project or not render:
        raise HTTPException(404, "Projeto local não encontrado")

    job_id = uuid.uuid4().hex[:12]
    editor_router._export_jobs[job_id] = {
        "status": "processing",
        "progress": 0,
        "message": "Iniciando exportacao local...",
        "error": None,
        "output_url": None,
        "output_urls": [],
        "export_kind": editor_router._normalize_editor_export_kind(req.export_kind),
        "local": True,
    }

    main_loop = asyncio.get_running_loop()
    background_tasks.add_task(
        editor_router._run_export,
        job_id,
        project,
        render,
        req,
        LOCAL_USER_ID,
        main_loop,
    )
    return {"job_id": job_id}


async def _proxy_request(request: Request) -> Response:
    upstream = await _request_upstream(request)
    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )


@app.get("/")
async def root() -> FileResponse:
    return _static_file_response("landing.html")


@app.get("/video")
async def video_dashboard() -> FileResponse:
    return _static_file_response("index.html")


@app.get("/privacy")
@app.get("/video/privacy")
async def privacy_page() -> FileResponse:
    return _static_file_response("privacy.html")


@app.get("/account-deletion")
@app.get("/video/account-deletion")
async def account_deletion_page() -> FileResponse:
    return _static_file_response("account-deletion.html")


@app.get("/terms")
@app.get("/video/terms")
async def terms_page() -> FileResponse:
    return _static_file_response("terms.html")


@app.get("/video/health")
async def health() -> JSONResponse:
    local_projects = LOCAL_PROJECT_STORE.list_project_summaries()
    return JSONResponse({
        "status": "ok",
        "mode": "local-proxy",
        "site_url": SITE_URL,
        "static_dir": str(STATIC_DIR),
        "data_dir": str(RUNTIME_DATA_DIR),
        "local_media_dir": str(LOCAL_MEDIA_DIR),
        "local_project_count": len(local_projects),
    })


@app.get("/api/video/projects")
async def list_video_projects(request: Request) -> JSONResponse:
    local_projects = LOCAL_PROJECT_STORE.list_project_summaries()
    try:
        remote_projects = await _request_upstream_json(request)
    except HTTPException as exc:
        if local_projects:
            logger.warning("[desktop-runtime] falling back to local projects list: %s", exc.detail)
            return JSONResponse(local_projects)
        raise

    if not isinstance(remote_projects, list):
        return JSONResponse(local_projects)
    return JSONResponse(_build_local_projects_response(remote_projects, local_projects))


@app.get("/api/video/projects/{project_id}")
async def get_video_project(project_id: int, request: Request):
    if LOCAL_PROJECT_STORE.is_local_project(project_id):
        detail = LOCAL_PROJECT_STORE.get_project_detail(project_id)
        if not detail:
            raise HTTPException(404, "Projeto local não encontrado")
        return JSONResponse(detail)
    return await _proxy_request(request)


@app.patch("/api/video/projects/{project_id}/title")
async def rename_video_project(project_id: int, request: Request):
    if not LOCAL_PROJECT_STORE.is_local_project(project_id):
        return await _proxy_request(request)

    payload = await request.json()
    title = str((payload or {}).get("title") or "").strip()
    if not title:
        raise HTTPException(400, "Título não pode ficar vazio")
    if len(title) > 500:
        raise HTTPException(400, "Título muito longo (máximo 500 caracteres)")

    renamed = LOCAL_PROJECT_STORE.rename_project(project_id, title)
    if not renamed:
        raise HTTPException(404, "Projeto local não encontrado")
    return JSONResponse(renamed)


@app.delete("/api/video/projects/{project_id}")
async def delete_video_project(project_id: int, request: Request):
    if not LOCAL_PROJECT_STORE.is_local_project(project_id):
        return await _proxy_request(request)
    deleted = LOCAL_PROJECT_STORE.delete_project(project_id)
    if not deleted:
        raise HTTPException(404, "Projeto local não encontrado")
    return JSONResponse({"deleted": True})


@app.post("/api/video/editor/upload-video")
async def upload_video_local(file: UploadFile = File(...)):
    payload = await editor_router.upload_video(file=file, user=_local_user(), db=_local_db())
    return JSONResponse(payload)


@app.post("/api/video/editor/upload-video-url")
async def upload_video_url_local(req: editor_router.EditorImportVideoUrlRequest):
    payload = await editor_router.upload_video_url(req=req, user=_local_user(), db=_local_db())
    return JSONResponse(payload)


@app.post("/api/video/editor/upload-image-sequence")
async def upload_image_sequence_local(images: list[UploadFile] = File(...)):
    payload = await editor_router.upload_image_sequence(images=images, user=_local_user(), db=_local_db())
    return JSONResponse(_attach_local_source_layers(payload))


@app.post("/api/video/editor/upload-audio-project")
async def upload_audio_project_local(file: UploadFile = File(...), aspect_ratio: str = Form("9:16")):
    payload = await editor_router.upload_audio_project(file=file, aspect_ratio=aspect_ratio, user=_local_user(), db=_local_db())
    return JSONResponse(_attach_local_source_layers(payload))


@app.post("/api/video/editor/upload-media-sequence")
async def upload_media_sequence_local(files: list[UploadFile] = File(...)):
    payload = await editor_router.upload_media_sequence(files=files, user=_local_user(), db=_local_db())
    return JSONResponse(_attach_local_source_layers(payload))


@app.post("/api/video/editor/upload-layer-image")
async def upload_layer_image_local(file: UploadFile = File(...)):
    return JSONResponse(await editor_router.upload_layer_image(file=file, user=_local_user()))


@app.post("/api/video/editor/upload-layer-video")
async def upload_layer_video_local(file: UploadFile = File(...)):
    return JSONResponse(await editor_router.upload_layer_video(file=file, user=_local_user()))


@app.post("/api/video/editor/upload-layer-audio")
async def upload_layer_audio_local(file: UploadFile = File(...)):
    return JSONResponse(await editor_router.upload_layer_audio(file=file, user=_local_user()))


@app.post("/api/video/editor/upload-music")
async def upload_music_local(file: UploadFile = File(...)):
    return JSONResponse(await editor_router.upload_music(file=file, user=_local_user()))


@app.post("/api/video/editor/upload-video-audio")
async def upload_video_audio_local(file: UploadFile = File(...)):
    return JSONResponse(await editor_router.upload_video_audio(file=file, user=_local_user()))


@app.post("/api/video/editor/upload-video-audio-url")
async def upload_video_audio_url_local(req: editor_router.EditorImportVideoUrlRequest):
    return JSONResponse(await editor_router.upload_video_audio_url(req=req, user=_local_user()))


@app.post("/api/video/editor/generate-tevoxi-music")
async def generate_tevoxi_music_local(request: Request, req: editor_router.GenerateTevoxiMusicRequest):
    if not LOCAL_PROJECT_STORE.is_local_project(int(req.project_id or 0)):
        return await _proxy_request(request)
    return JSONResponse(await _generate_local_tevoxi_music(req))


@app.post("/api/video/editor/add-layer-video-from-library")
async def add_layer_video_from_library_local(request: Request, req: editor_router.AddLayerVideoFromLibraryRequest):
    if not LOCAL_PROJECT_STORE.is_local_project(int(req.project_id or 0)):
        return await _proxy_request(request)
    return JSONResponse(_build_local_library_layer(int(req.project_id or 0)))


@app.post("/api/video/editor/transcribe/{project_id}")
async def transcribe_local_project(project_id: int, request: Request):
    if not LOCAL_PROJECT_STORE.is_local_project(project_id):
        return await _proxy_request(request)
    return JSONResponse(await _transcribe_local_project(project_id))


@app.post("/api/video/editor/smart-cuts")
async def smart_cuts_local(request: Request, req: editor_router.SmartCutsRequest):
    if not LOCAL_PROJECT_STORE.is_local_project(int(req.project_id or 0)):
        return await _proxy_request(request)
    return JSONResponse(await _analyze_local_smart_cuts(req))


@app.post("/api/video/editor/export")
async def export_local(request: Request, req: editor_router.ExportRequest, background_tasks: BackgroundTasks):
    if not LOCAL_PROJECT_STORE.is_local_project(int(req.project_id or 0)):
        return await _proxy_request(request)
    return JSONResponse(_start_local_export_job(req, background_tasks))


@app.get("/api/video/editor/export/{job_id}/status")
async def export_local_status(job_id: str, request: Request):
    local_job = editor_router._export_jobs.get(job_id)
    if local_job is not None:
        return JSONResponse(local_job)
    return await _proxy_request(request)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_api(path: str, request: Request) -> Response:
    return await _proxy_request(request)


@app.api_route("/video/media/{path:path}", methods=["GET", "HEAD", "OPTIONS"])
async def proxy_media(path: str, request: Request) -> Response:
    local_response = _local_media_response(path)
    if local_response is not None:
        return local_response
    return await _proxy_request(request)


if __name__ == "__main__":
    uvicorn.run(app, host=RUNTIME_HOST, port=RUNTIME_PORT, log_level="info")