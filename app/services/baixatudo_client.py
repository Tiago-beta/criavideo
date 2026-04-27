"""Client for Baixa Tudo integration API used by similar-video workflows."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx


logger = logging.getLogger(__name__)


@dataclass
class BaixaTudoDownloadResult:
    task_id: str
    output_path: str
    file_name: str
    source_url: str
    normalized_url: str


class BaixaTudoError(RuntimeError):
    """Raised when Baixa Tudo returns an operational error."""


class BaixaTudoClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: int = 120,
        poll_interval_seconds: float = 2.5,
        max_wait_seconds: int = 900,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = max(10, int(timeout_seconds or 120))
        self.poll_interval_seconds = max(0.8, float(poll_interval_seconds or 2.5))
        self.max_wait_seconds = max(60, int(max_wait_seconds or 900))

        if not self.base_url:
            raise BaixaTudoError("Baixa Tudo URL nao configurada")
        if not self.api_key:
            raise BaixaTudoError("Baixa Tudo API key nao configurada")

    def _endpoint(self, path: str) -> str:
        return urljoin(self.base_url + "/", str(path or "").lstrip("/"))

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    @staticmethod
    def _extract_error(payload: object) -> str:
        if isinstance(payload, dict):
            for key in ("detail", "erro", "error", "message", "mensagem"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return str(payload)
        return str(payload)

    async def _request_json(self, method: str, path: str, *, json_body: dict | None = None) -> dict:
        url = self._endpoint(path)
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.request(
                method=method.upper(),
                url=url,
                headers=self._headers(),
                json=json_body,
            )
        if response.status_code >= 400:
            try:
                payload = response.json()
            except Exception:
                payload = {"detail": response.text[:400]}
            raise BaixaTudoError(
                f"Baixa Tudo ({response.status_code}) em {path}: {self._extract_error(payload)}"
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise BaixaTudoError(f"Resposta invalida do Baixa Tudo em {path}") from exc

        if not isinstance(payload, dict):
            raise BaixaTudoError(f"Payload inesperado do Baixa Tudo em {path}")

        return payload

    async def ensure_ready(self) -> dict:
        return await self._request_json("GET", "/api/integracao")

    async def start_download(self, source_url: str, formato: str = "video_melhor") -> dict:
        payload = {
            "url": str(source_url or "").strip(),
            "formato": str(formato or "video_melhor").strip(),
            "legendas": False,
            "thumbnail": False,
        }
        if not payload["url"].startswith(("http://", "https://")):
            raise BaixaTudoError("URL de video invalida")
        return await self._request_json("POST", "/api/integracao/download", json_body=payload)

    async def get_download_status(self, task_id: str) -> dict:
        return await self._request_json("GET", f"/api/integracao/download/{task_id}")

    async def wait_until_completed(self, task_id: str) -> dict:
        waited = 0.0
        last_progress = ""
        while waited <= float(self.max_wait_seconds):
            state = await self.get_download_status(task_id)
            status = str(state.get("status", "")).strip().lower()
            progress = state.get("progresso", state.get("progress", ""))
            if progress != "" and progress != last_progress:
                last_progress = str(progress)
                logger.info("Baixa Tudo task %s progress: %s", task_id, progress)

            if status == "completed":
                return state
            if status in {"error", "failed"}:
                raise BaixaTudoError(self._extract_error(state) or "Falha no download do Baixa Tudo")

            await asyncio.sleep(self.poll_interval_seconds)
            waited += self.poll_interval_seconds

        raise BaixaTudoError("Tempo limite excedido aguardando download do Baixa Tudo")

    async def fetch_file(self, task_id: str, output_path: str) -> str:
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        url = self._endpoint(f"/api/integracao/download/{task_id}/file")

        async with httpx.AsyncClient(timeout=self.timeout_seconds * 4, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=self._headers()) as response:
                if response.status_code >= 400:
                    try:
                        payload = await response.aread()
                        text = payload.decode(errors="ignore")
                    except Exception:
                        text = ""
                    raise BaixaTudoError(
                        f"Baixa Tudo ({response.status_code}) ao baixar arquivo: {text[:400]}"
                    )
                with open(target, "wb") as fp:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            fp.write(chunk)

        if not target.exists() or target.stat().st_size <= 0:
            raise BaixaTudoError("Arquivo baixado do Baixa Tudo veio vazio")

        return str(target)

    @staticmethod
    def _is_retryable_missing_final_file_error(err: Exception) -> bool:
        text = str(err or "").lower()
        markers = (
            "nao foi possivel localizar o arquivo final",
            "não foi possível localizar o arquivo final",
            "arquivo final",
            "arquivo nao encontrado",
            "arquivo não encontrado",
            "404",
            "not found",
        )
        return any(marker in text for marker in markers)

    def _download_with_ytdlp_sync(self, source_url: str, output_path: str) -> tuple[str, str, str]:
        try:
            import yt_dlp
        except Exception as exc:
            raise BaixaTudoError("Fallback yt-dlp indisponivel no servidor") from exc

        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        outtmpl = str(target.with_suffix(".%(ext)s"))

        normalized_url = str(source_url or "").strip()
        title = ""

        ydl_opts = {
            "format": "bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "retries": 3,
            "fragment_retries": 3,
            "http_headers": {
                "User-Agent": "Mozilla/5.0",
            },
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(source_url, download=True)
            if isinstance(info, dict):
                normalized_url = str(
                    info.get("webpage_url") or info.get("original_url") or info.get("url") or normalized_url
                ).strip()
                title = str(info.get("title") or "").strip()

                try:
                    prepared = Path(ydl.prepare_filename(info))
                except Exception:
                    prepared = None
            else:
                prepared = None

        candidates: list[Path] = []
        candidates.append(target)
        candidates.append(target.with_suffix(".mp4"))

        if prepared is not None:
            candidates.append(prepared)
            if prepared.suffix.lower() != ".mp4":
                candidates.append(prepared.with_suffix(".mp4"))

        try:
            stem_candidates = sorted(
                target.parent.glob(f"{target.stem}.*"),
                key=lambda item: item.stat().st_size if item.exists() else 0,
                reverse=True,
            )
            candidates.extend(stem_candidates)
        except Exception:
            pass

        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            try:
                if candidate.exists() and candidate.stat().st_size > 0:
                    file_name = candidate.name
                    if title and not file_name.strip():
                        file_name = f"{title}.mp4"
                    return str(candidate), file_name, normalized_url
            except Exception:
                continue

        raise BaixaTudoError("Fallback yt-dlp nao gerou arquivo de video valido")

    async def download_video(self, source_url: str, output_path: str, formato: str = "video_melhor") -> BaixaTudoDownloadResult:
        await self.ensure_ready()
        last_error: Exception | None = None

        for task_attempt in range(1, 4):
            started = await self.start_download(source_url, formato=formato)
            task_id = str(started.get("task_id", "")).strip()
            if not task_id:
                raise BaixaTudoError("Baixa Tudo nao retornou task_id")

            try:
                done_state = await self.wait_until_completed(task_id)
            except Exception as exc:
                last_error = exc
                if self._is_retryable_missing_final_file_error(exc) and task_attempt < 3:
                    logger.warning(
                        "Baixa Tudo task %s terminou sem arquivo final (tentativa %s/3). Reenfileirando download...",
                        task_id,
                        task_attempt,
                    )
                    await asyncio.sleep(min(1.2 + task_attempt, 4.0))
                    continue
                raise

            final_path = ""
            last_fetch_error: Exception | None = None

            for fetch_attempt in range(1, 8):
                try:
                    final_path = await self.fetch_file(task_id, output_path)
                    break
                except Exception as exc:
                    last_fetch_error = exc
                    if not self._is_retryable_missing_final_file_error(exc):
                        raise

                    state = await self.get_download_status(task_id)
                    status = str(state.get("status", "")).strip().lower()
                    if status in {"error", "failed"}:
                        raise BaixaTudoError(self._extract_error(state) or "Falha no download do Baixa Tudo")
                    done_state = state

                    if fetch_attempt >= 7:
                        break
                    await asyncio.sleep(min(1.5 + (fetch_attempt * 0.5), 4.0))

            if final_path:
                file_name = str(done_state.get("nome_arquivo") or Path(final_path).name).strip() or Path(final_path).name
                normalized_url = str(started.get("url_final") or source_url).strip()

                return BaixaTudoDownloadResult(
                    task_id=task_id,
                    output_path=final_path,
                    file_name=file_name,
                    source_url=str(source_url or "").strip(),
                    normalized_url=normalized_url,
                )

            if last_fetch_error is not None:
                last_error = last_fetch_error
            if task_attempt < 3:
                logger.warning(
                    "Baixa Tudo task %s nao entregou arquivo final apos retries locais (tentativa %s/3). Reenfileirando...",
                    task_id,
                    task_attempt,
                )
                await asyncio.sleep(min(1.2 + task_attempt, 4.0))
                continue

        if last_error is not None and self._is_retryable_missing_final_file_error(last_error):
            logger.warning("Baixa Tudo falhou com arquivo final ausente. Tentando fallback com yt-dlp...")
            try:
                loop = asyncio.get_event_loop()
                fallback_path, fallback_name, fallback_url = await loop.run_in_executor(
                    None,
                    self._download_with_ytdlp_sync,
                    source_url,
                    output_path,
                )
                return BaixaTudoDownloadResult(
                    task_id="fallback:ytdlp",
                    output_path=fallback_path,
                    file_name=fallback_name,
                    source_url=str(source_url or "").strip(),
                    normalized_url=str(fallback_url or source_url).strip(),
                )
            except Exception as fallback_exc:
                raise BaixaTudoError(f"{last_error} | Fallback yt-dlp falhou: {fallback_exc}")

        if last_error is not None:
            raise BaixaTudoError(str(last_error))
        raise BaixaTudoError("Download terminou, mas nao foi possivel baixar o arquivo final")
