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

    async def download_video(self, source_url: str, output_path: str, formato: str = "video_melhor") -> BaixaTudoDownloadResult:
        await self.ensure_ready()
        started = await self.start_download(source_url, formato=formato)
        task_id = str(started.get("task_id", "")).strip()
        if not task_id:
            raise BaixaTudoError("Baixa Tudo nao retornou task_id")

        done_state = await self.wait_until_completed(task_id)
        final_path = await self.fetch_file(task_id, output_path)

        file_name = str(done_state.get("nome_arquivo") or Path(final_path).name).strip() or Path(final_path).name
        normalized_url = str(started.get("url_final") or source_url).strip()

        return BaixaTudoDownloadResult(
            task_id=task_id,
            output_path=final_path,
            file_name=file_name,
            source_url=str(source_url or "").strip(),
            normalized_url=normalized_url,
        )
