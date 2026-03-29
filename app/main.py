"""
CriaVideo — FastAPI entrypoint.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import get_settings
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown events."""
    # Ensure media directories exist
    Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
    for sub in ["scenes", "clips", "subtitles", "renders", "thumbnails", "voices"]:
        (Path(settings.media_dir) / sub).mkdir(exist_ok=True)

    start_scheduler()
    logger.info("CriaVideo started")
    yield
    stop_scheduler()
    logger.info("CriaVideo stopped")


app = FastAPI(
    title="CriaVideo",
    version="1.0.0",
    lifespan=lifespan,
)


def static_file_response(filename: str) -> FileResponse:
    return FileResponse(str(static_path / filename))

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        f"http://{settings.host}:{settings.port}",
        settings.site_url,
        "https://criavideo.pro",
        "https://www.criavideo.pro",
    ],
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

app.include_router(auth_router)
app.include_router(video_router)
app.include_router(social_router)
app.include_router(publish_router)
app.include_router(schedule_router)
app.include_router(voice_router)

# ── Serve rendered media files ──
media_path = Path(settings.media_dir)
if media_path.exists():
    app.mount("/video/media", StaticFiles(directory=str(media_path)), name="media")

# ── Serve static dashboard ──
static_path = Path(__file__).parent.parent / "static"
if static_path.exists():
    app.mount("/video/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/")
async def landing():
    """Public landing page — visible to Google verification."""
    return static_file_response("landing.html")


@app.get("/google3b8734f6a78a1e9f.html")
async def google_site_verification():
    """Google Search Console domain verification file."""
    return static_file_response("google3b8734f6a78a1e9f.html")


@app.get("/video")
async def dashboard():
    """Serve the web dashboard."""
    return static_file_response("index.html")


@app.get("/privacy")
@app.get("/video/privacy")
async def privacy_page():
    """Serve the privacy policy page."""
    return static_file_response("privacy.html")


@app.get("/terms")
@app.get("/video/terms")
async def terms_page():
    """Serve the terms of service page."""
    return static_file_response("terms.html")


@app.get("/video/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}
