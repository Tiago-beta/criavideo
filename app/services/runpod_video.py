"""
RunPod Video — Uses RunPod Public Endpoints to call Wan 2.2
for realistic AI video generation (text-to-video and image-to-video).

Endpoints:
  - T2V: wan-2-2-t2v-720  ($0.30/5s, $0.48/8s)
  - I2V: wan-2-2-i2v-720  ($0.30/5s)
"""
import os
import time
import base64
import mimetypes
import logging
import asyncio
import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

RUNPOD_BASE_URL = "https://api.runpod.ai/v2"
WAN_T2V_ENDPOINT = "wan-2-2-t2v-720"
WAN_I2V_ENDPOINT = "wan-2-2-i2v-720"

# Aspect ratio mapping: our format → Wan 2.2 size string
_ASPECT_TO_SIZE = {
    "16:9": "1280*720",
    "9:16": "720*1280",
    "1:1": "720*720",
}


async def generate_wan_video(
    prompt: str,
    duration: int = 5,
    aspect_ratio: str = "16:9",
    output_path: str = "",
    image_path: str | None = None,
    timeout_seconds: int = 900,
    on_progress=None,
) -> str:
    """Generate a realistic video using Wan 2.2 via RunPod Public Endpoints.

    If image_path is provided, uses image-to-video (I2V).
    Otherwise, uses text-to-video (T2V).

    Returns the local path to the downloaded MP4 video.
    """
    api_key = settings.runpod_api_key
    if not api_key:
        raise RuntimeError("RUNPOD_API_KEY not configured")

    # Wan 2.2 supports 5s or 8s
    if duration <= 6:
        wan_duration = 5
    else:
        wan_duration = 8

    size = _ASPECT_TO_SIZE.get(aspect_ratio, "1280*720")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Choose endpoint based on whether we have a reference image
    use_i2v = image_path and os.path.exists(image_path)
    endpoint = WAN_I2V_ENDPOINT if use_i2v else WAN_T2V_ENDPOINT
    wan_temperature = 0.2

    input_data = {
        "prompt": prompt,
        "duration": wan_duration,
        "size": size,
        "num_inference_steps": 30,
        "guidance_scale": 5,
        "flow_shift": 5,
        "enable_prompt_optimization": False,
        "temperature": wan_temperature,
    }

    # Add reference image for I2V
    if use_i2v:
        mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        with open(image_path, "rb") as img_f:
            b64 = base64.b64encode(img_f.read()).decode("utf-8")
        input_data["image"] = f"data:{mime_type};base64,{b64}"

    # ─────── STEP 1: Submit async job ────────
    endpoint_url = f"{RUNPOD_BASE_URL}/{endpoint}"

    async with httpx.AsyncClient(timeout=120) as client:
        submit_payload = {"input": input_data}
        resp = await client.post(
            f"{endpoint_url}/run",
            headers=headers,
            json=submit_payload,
        )

        if resp.status_code in (400, 422):
            details = (resp.text or "")[:300]
            lowered = details.lower()
            if "temperature" in lowered:
                logger.warning(
                    "Wan endpoint rejected temperature control; retrying without temperature. details=%s",
                    details,
                )
                fallback_input = dict(input_data)
                fallback_input.pop("temperature", None)
                resp = await client.post(
                    f"{endpoint_url}/run",
                    headers=headers,
                    json={"input": fallback_input},
                )

        resp.raise_for_status()
        job = resp.json()

    job_id = job["id"]
    status = job.get("status", "IN_QUEUE")
    mode = "I2V" if use_i2v else "T2V"
    logger.info(f"Wan 2.2 {mode} job submitted: {job_id} (status={status})")

    if on_progress:
        await on_progress(20, "Gerando video realista com Wan 2.2...")

    # ─────── STEP 2: Poll for completion ────────
    start_time = time.time()
    last_progress = 20

    async with httpx.AsyncClient(timeout=60) as client:
        while (time.time() - start_time) < timeout_seconds:
            resp = await client.get(
                f"{endpoint_url}/status/{job_id}",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status", "")
            if status == "COMPLETED":
                output = data.get("output", {})
                # RunPod Wan 2.2 returns video URL in output.result
                video_url = output.get("result") or output.get("video_url") or ""
                if not video_url:
                    raise RuntimeError(f"Wan 2.2 returned empty output: {output}")
                break
            elif status in ("FAILED", "CANCELLED"):
                error = data.get("error", data.get("output", "Unknown error"))
                raise RuntimeError(f"Wan 2.2 generation failed: {error}")

            # Update progress based on elapsed time
            elapsed = time.time() - start_time
            progress = min(75, 20 + int((elapsed / timeout_seconds) * 55))
            if progress > last_progress and on_progress:
                last_progress = progress
                await on_progress(progress, "Gerando video realista com Wan 2.2...")

            await asyncio.sleep(8)
        else:
            raise TimeoutError(f"Wan 2.2 generation timed out after {timeout_seconds}s")

    if on_progress:
        await on_progress(80, "Baixando video gerado...")

    # ─────── STEP 3: Download the video ────────
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        resp = await client.get(video_url)
        resp.raise_for_status()
        with open(output_path, "wb") as f:
            f.write(resp.content)

    file_size = os.path.getsize(output_path)
    logger.info(f"Wan 2.2 video downloaded: {output_path} ({file_size} bytes)")
    return output_path
