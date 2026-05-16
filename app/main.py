"""
CriaVideo — FastAPI entrypoint.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings
from app.scheduler import start_scheduler, stop_scheduler
from app.tasks.video_tasks import fail_interrupted_video_projects_on_startup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()

ANDROID_APP_PACKAGE_NAME = "pro.criavideo.app"
ANDROID_UPLOAD_KEY_SHA256 = "90:95:28:75:5C:1C:A5:9A:53:BF:68:CD:2E:BA:58:13:AB:0B:40:B9:0C:68:A8:76:FA:6E:19:D1:70:B3:29:99"
ANDROID_PLAY_SIGNING_SHA256 = "C9:93:9F:09:86:1D:9B:65:5A:78:E7:03:F7:F0:B8:13:DA:EE:8B:F5:8F:BF:FA:D1:35:A9:2F:CE:CE:E9:80:E0"
ANDROID_ASSETLINKS_STATEMENTS = [
    {
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": ANDROID_APP_PACKAGE_NAME,
            "sha256_cert_fingerprints": [
                ANDROID_UPLOAD_KEY_SHA256,
                ANDROID_PLAY_SIGNING_SHA256,
            ],
        },
    }
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    # Ensure media directories exist
    Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
    for sub in ["scenes", "clips", "subtitles", "renders", "thumbnails", "voices"]:
        (Path(settings.media_dir) / sub).mkdir(exist_ok=True)

    start_scheduler()
    try:
        recovered_count = await fail_interrupted_video_projects_on_startup()
        if recovered_count:
            logger.warning("Recovered %s interrupted video projects on startup", recovered_count)
    except Exception:
        logger.exception("Failed to recover interrupted video projects on startup")
    logger.info("CriaVideo started")
    yield
    stop_scheduler()
    logger.info("CriaVideo stopped")


app = FastAPI(
    title="CriaVideo",
    version="1.0.0",
    lifespan=lifespan,
)


def _apply_html_no_cache_headers(response) -> None:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"


def static_file_response(filename: str) -> FileResponse:
    response = FileResponse(str(static_path / filename))
    if filename.lower().endswith(".html"):
        _apply_html_no_cache_headers(response)
    return response


class DashboardStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if str(path or "").lower().endswith(".html"):
            _apply_html_no_cache_headers(response)
        return response

def _build_cors_origins() -> list[str]:
    base_origins = [
        "http://localhost:3000",
        "http://localhost:3001",
        f"http://{settings.host}:{settings.port}",
        settings.site_url,
        "https://criavideo.pro",
        "https://www.criavideo.pro",
        "https://staging.criavideo.pro",
        "https://tevoxi.com",
        "https://www.tevoxi.com",
        "https://staging.tevoxi.com",
        "https://levita.pro",
        "https://www.levita.pro",
        "https://staging.levita.pro",
    ]
    extra_origins = [origin.strip() for origin in str(settings.cors_origins or "").split(",") if origin.strip()]
    seen: set[str] = set()
    origins: list[str] = []
    for origin in [*base_origins, *extra_origins]:
        normalized = str(origin or "").strip().rstrip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            origins.append(normalized)
    return origins


# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=_build_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──
from app.routers.video import router as video_router
from app.routers.auth import router as auth_router
from app.routers.social import router as social_router
from app.routers.publish import router as publish_router
from app.routers.schedule import router as schedule_router
from app.routers.voice import router as voice_router
from app.routers.credits import router as credits_router
from app.routers.automation import router as automation_router
from app.routers.risc import router as risc_router
from app.routers.editor import router as editor_router
from app.routers.persona import router as persona_router
from app.routers.analyze import router as analyze_router
from app.routers.admin import router as admin_router
from app.routers.series import router as series_router

app.include_router(auth_router)
app.include_router(video_router)
app.include_router(social_router)
app.include_router(publish_router)
app.include_router(schedule_router)
app.include_router(voice_router)
app.include_router(credits_router)
app.include_router(automation_router)
app.include_router(risc_router)
app.include_router(editor_router)
app.include_router(persona_router)
app.include_router(analyze_router)
app.include_router(admin_router)
app.include_router(series_router)

# ── Serve rendered media files ──
media_path = Path(settings.media_dir)
if media_path.exists():
    app.mount("/video/media", StaticFiles(directory=str(media_path)), name="media")

# ── Serve static dashboard ──
static_path = Path(__file__).parent.parent / "static"
if static_path.exists():
    app.mount("/video/static", DashboardStaticFiles(directory=str(static_path)), name="static")


@app.get("/")
async def landing():
    """Public landing page — visible to Google verification."""
    return static_file_response("landing.html")


@app.get("/google3b8734f6a78a1e9f.html")
async def google_site_verification():
    """Google Search Console domain verification file."""
    return static_file_response("google3b8734f6a78a1e9f.html")


@app.get("/.well-known/assetlinks.json", include_in_schema=False)
async def assetlinks_json():
    """Serve Digital Asset Links for the Android Trusted Web Activity."""
    return JSONResponse(ANDROID_ASSETLINKS_STATEMENTS)


@app.get("/video")
async def dashboard():
    """Serve the web dashboard."""
    return static_file_response("index.html")


@app.get("/privacy")
@app.get("/video/privacy")
async def privacy_page():
    """Serve the privacy policy page."""
    return static_file_response("privacy.html")


@app.get("/account-deletion")
@app.get("/video/account-deletion")
async def account_deletion_page():
    """Serve the account deletion instructions page."""
    return static_file_response("account-deletion.html")


@app.get("/terms")
@app.get("/video/terms")
async def terms_page():
    """Serve the terms of service page."""
    return static_file_response("terms.html")


@app.get("/video/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
