import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles


RUNTIME_HOST = str(os.getenv("CRIAVIDEO_DESKTOP_RUNTIME_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
RUNTIME_PORT = int(os.getenv("CRIAVIDEO_DESKTOP_RUNTIME_PORT", "3232") or 3232)
SITE_URL = str(os.getenv("CRIAVIDEO_DESKTOP_RUNTIME_SITE_URL", "https://criavideo.pro") or "https://criavideo.pro").strip().rstrip("/")
DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
STATIC_DIR = Path(os.getenv("CRIAVIDEO_DESKTOP_RUNTIME_STATIC_DIR") or DEFAULT_STATIC_DIR).resolve()

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


async def _proxy_request(request: Request) -> Response:
    client = app.state.http_client
    content = await request.body()
    forwarded_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS and key.lower() != "host"
    }
    upstream = await client.request(
        method=request.method,
        url=_build_upstream_url(request),
        content=content,
        headers=forwarded_headers,
    )
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
    return JSONResponse({
        "status": "ok",
        "mode": "local-proxy",
        "site_url": SITE_URL,
        "static_dir": str(STATIC_DIR),
    })


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_api(path: str, request: Request) -> Response:
    return await _proxy_request(request)


@app.api_route("/video/media/{path:path}", methods=["GET", "HEAD", "OPTIONS"])
async def proxy_media(path: str, request: Request) -> Response:
    return await _proxy_request(request)


if __name__ == "__main__":
    uvicorn.run(app, host=RUNTIME_HOST, port=RUNTIME_PORT, log_level="info")