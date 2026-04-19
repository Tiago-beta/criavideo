"""
Seedance Video — Uses Replicate API to call ByteDance Seedance 2.0
for realistic AI video generation (text-to-video).
"""
import os
import time
import logging
import asyncio
import httpx
import openai
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

REPLICATE_API_URL = "https://api.replicate.com/v1/predictions"
SEEDANCE_MODEL_VERSION = "bytedance/seedance-2.0"
SEEDANCE_RATE_LIMIT_MSG = (
    "Seedance 2.0 esta com alta demanda no momento (429). "
    "Tente novamente em alguns segundos ou use MiniMax/Wan 2.2."
)


def _retry_delay_from_header(retry_after: str | None, default_seconds: int = 5) -> int:
    if not retry_after:
        return default_seconds
    try:
        return max(1, min(int(float(retry_after)), 90))
    except Exception:
        return default_seconds

# Curated Seedance prompt engineering system prompt
_SEEDANCE_SYSTEM_PROMPT = """You are an expert prompt engineer for Seedance 2.0, ByteDance's state-of-the-art AI video generation model.

Your job: convert the user's video description (usually in Portuguese) into an optimized English prompt for Seedance 2.0.

RULES:
1. Output ONLY the final English prompt. No explanations, no markdown.
2. The video is {duration} seconds long. Structure shots within that time.
3. CONTENT SAFETY (CRITICAL): Seedance has a strict content filter. You MUST:
   - NEVER use explicit religious references (God, Jesus, church, prayer, psalm, Bible, faith, worship, angel, demon, etc.)
   - NEVER use political, violent, sexual, or controversial terms.
   - Convert religious/spiritual themes into VISUAL METAPHORS: e.g. "shepherd on a hill at golden hour", "peaceful valley with sheep", "sunrise over mountains", "person walking a serene path through nature".
   - Focus on NATURE, LANDSCAPES, EMOTIONS, and HUMAN MOMENTS — not abstract concepts.
   - If the user's theme is spiritual, translate it into a beautiful cinematic nature/human scene without any religious words.
4. Use this proven Seedance prompt structure:

   Style: [Visual style], [Aesthetic], [Mood], [Technical look].
   Duration: {duration}s.
   [00:00-XX:XX] Shot 1: [Shot Name].
   Scene: [Visual description with specific details].
   Action: [Movement, interaction, emotion].
   Camera: [Camera movement - push in, pan, dolly, crane, tracking, slow motion, etc].
   Lighting: [Specific lighting - golden hour, neon, dramatic shadows, etc].

5. Include SPECIFIC visual details:
   - Camera movements: push in, pull back, pan left/right, dolly, crane up/down, tracking shot, slow motion, whip pan, rack focus
   - Lighting: golden hour, blue hour, neon, candlelight, dramatic rim light, volumetric fog, lens flare, cinematic shadows
   - Texture/mood: film grain, depth of field, bokeh, desaturated, vibrant, moody, ethereal
   - Physics: rain, smoke, particles, fabric movement, hair physics, water ripples

6. For short durations (5-7s), use 1-2 shots max. For 10s, use 2-3 shots.
7. Be VERY specific about what appears in frame — Seedance excels with concrete visual descriptions.
8. Do NOT include dialogue or subtitle cues — focus on visuals, motion, and atmosphere.
9. If the user mentions a product, brand, or specific object, describe it precisely in the scene.
10. Preserve the user's creative intent while enhancing with cinematic details.

EXAMPLES OF GREAT SEEDANCE PROMPTS:

Example 1 (7s cinematic):
Style: IMAX Cinematic, Golden Hour, Epic Scale, Warm Tones.
Duration: 7s.
[00:00-00:04] Shot 1: The Reveal.
Scene: A cozy wooden cabin nestled in a dense snowy forest at night. Warm amber light glows from frosted windows. Fresh snow covers the ground and pine branches.
Camera: Slow push in through the trees, branches gently parting.
Lighting: Warm interior glow contrasting with cool blue moonlit snow. Gentle snowfall catches the light.
[00:04-00:07] Shot 2: The Detail.
Scene: Close-up of snowflakes landing on a windowsill, the warm interior visible through glass.
Camera: Slow dolly right along the window.
Lighting: Bokeh from interior lights, crystalline snow detail.

Example 2 (5s commercial):
Style: Premium Commercial, Clean, High-End Product Photography in Motion.
Duration: 5s.
[00:00-00:05] Shot 1: Hero Product Reveal.
Scene: A sleek glass perfume bottle rotates slowly on a reflective black surface. Golden liquid catches light. Delicate mist particles float around it.
Camera: Smooth 180-degree orbit around the bottle, slight low angle.
Lighting: Key light from above creating dramatic reflections and caustics on the glass. Subtle rim light separating product from dark background.

Example 3 (10s social media):
Style: Hyperrealistic CG, Comedic Surrealism, Viral Meme Aesthetic, 8K.
Duration: 10s.
[00:00-00:04] Shot 1: The Setup.
Scene: A bustling city street with modern skyscrapers. Normal daily life — people walking, traffic flowing.
Camera: Handheld street-level perspective, slight shake for authenticity.
[00:04-00:07] Shot 2: The Reveal.
Scene: Camera tilts up to reveal a giant orange tabby cat the size of a building, lounging between two skyscrapers, lazily swatting at a helicopter.
Camera: Dramatic tilt up, wide angle lens distortion.
[00:07-00:10] Shot 3: The Punchline.
Scene: The giant cat yawns enormously, causing papers and hats to blow away from pedestrians below.
Camera: Ground-level looking up at the massive yawning cat face.
"""


async def optimize_prompt_for_seedance(
    user_description: str,
    duration: int = 7,
    tone: str | None = None,
) -> str:
    """Convert user's description (Portuguese) into an optimized English Seedance 2.0 prompt."""
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    system = _SEEDANCE_SYSTEM_PROMPT.replace("{duration}", str(duration))
    user_msg = user_description
    if tone:
        user_msg += f"\n\nTom/estilo desejado: {tone}"

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
            max_tokens=800,
        )
        optimized = resp.choices[0].message.content.strip()
        logger.info(f"Seedance prompt optimized: {len(optimized)} chars")
        return optimized
    except Exception as e:
        logger.warning(f"Prompt optimization failed, using original: {e}")
        return user_description


_SANITIZE_PROMPT = """You are a content-safety filter for AI video generation prompts.

The following prompt was REJECTED by the AI video model's content filter (error E005: flagged as sensitive).
Your job: rewrite it to convey the SAME visual scene but remove ALL potentially sensitive words/themes.

RULES:
1. Remove ALL religious terms (God, Lord, Jesus, faith, prayer, church, psalm, worship, angel, shepherd in religious context, etc.)
2. Remove ALL political, violent, sexual, or controversial references.
3. Keep the VISUAL ESSENCE: landscapes, nature, people, lighting, camera movements.
4. Replace abstract/spiritual concepts with concrete visual descriptions.
5. Output ONLY the rewritten prompt. No explanations.

Example:
- "A shepherd guided by divine light walks through a valley of faith" →
  "A man with a wooden staff walks through a lush green valley at golden hour, warm sunlight streaming through mountain peaks"
"""


async def sanitize_prompt_for_retry(rejected_prompt: str) -> str:
    """Rewrite a prompt that was flagged by Seedance's content filter."""
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _SANITIZE_PROMPT},
                {"role": "user", "content": rejected_prompt},
            ],
            temperature=0.5,
            max_tokens=800,
        )
        sanitized = resp.choices[0].message.content.strip()
        logger.info(f"Prompt sanitized for retry: {len(sanitized)} chars")
        return sanitized
    except Exception as e:
        logger.warning(f"Prompt sanitization failed: {e}")
        raise RuntimeError("Nao foi possivel reformular o prompt para evitar o filtro de conteudo.")


async def generate_realistic_video(
    prompt: str,
    duration: int = 7,
    aspect_ratio: str = "16:9",
    output_path: str = "",
    seed: int | None = None,
    resolution: str = "720p",
    generate_audio: bool = True,
    image_path: str | None = None,
    timeout_seconds: int = 600,
    on_progress=None,
) -> str:
    """Generate a realistic video using Seedance 2.0 via Replicate API.

    Returns the local path to the downloaded MP4 video.
    """
    token = settings.replicate_api_token
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN not configured")

    duration = max(1, min(duration, 10))

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "wait",
    }

    input_data = {
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
        "generate_audio": generate_audio,
    }
    if seed is not None:
        input_data["seed"] = seed

    # Add reference image as base64 data URI if provided
    if image_path and os.path.exists(image_path):
        import base64
        import mimetypes
        mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        with open(image_path, "rb") as img_f:
            b64 = base64.b64encode(img_f.read()).decode("utf-8")
        input_data["image"] = f"data:{mime_type};base64,{b64}"
        logger.info(f"Seedance image-to-video: attached {image_path} as base64 ({len(b64)} chars)")

    payload = {
        "version": SEEDANCE_MODEL_VERSION,
        "input": input_data,
    }

    # Step 1: Create prediction
    prediction = None
    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(5):
            try:
                resp = await client.post(
                    "https://api.replicate.com/v1/models/bytedance/seedance-2.0/predictions",
                    headers=headers,
                    json={"input": input_data},
                )
            except httpx.RequestError as e:
                if attempt >= 4:
                    raise RuntimeError(f"Falha de conexao ao iniciar Seedance: {e}")
                wait_s = min(20, 2 ** attempt)
                logger.warning(
                    "Seedance request error on create (attempt %d/5): %s. Retrying in %ds",
                    attempt + 1,
                    e,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code == 429:
                if attempt >= 4:
                    raise RuntimeError(SEEDANCE_RATE_LIMIT_MSG)
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(30, 2 ** (attempt + 2)))
                logger.warning(
                    "Seedance rate-limited on create (attempt %d/5). Retrying in %ds",
                    attempt + 1,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as e:
                body = (e.response.text or "").strip()[:300] if e.response is not None else ""
                msg = f"Erro ao iniciar Seedance (HTTP {e.response.status_code if e.response is not None else '??'})"
                if body:
                    msg += f": {body}"
                raise RuntimeError(msg)

            prediction = resp.json()
            break

    if not prediction:
        raise RuntimeError("Nao foi possivel iniciar a geracao no Seedance.")

    prediction_id = prediction["id"]
    status = prediction.get("status", "starting")
    logger.info(f"Seedance prediction created: {prediction_id} (status={status})")

    if on_progress:
        await on_progress(20, "Gerando video realista com Seedance 2.0...")

    # Step 2: Poll for completion
    poll_url = prediction.get("urls", {}).get("get", f"https://api.replicate.com/v1/predictions/{prediction_id}")
    poll_headers = {"Authorization": f"Bearer {token}"}

    start_time = time.time()
    last_progress = 20
    async with httpx.AsyncClient(timeout=60) as client:
        while (time.time() - start_time) < timeout_seconds:
            try:
                resp = await client.get(poll_url, headers=poll_headers)
            except httpx.RequestError as e:
                logger.warning("Seedance poll request error: %s", e)
                await asyncio.sleep(5)
                continue

            if resp.status_code == 429:
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=6)
                logger.warning("Seedance rate-limited on poll. Retrying in %ds", wait_s)
                await asyncio.sleep(wait_s)
                continue

            resp.raise_for_status()
            data = resp.json()

            status = data.get("status", "")
            if status == "succeeded":
                output = data.get("output")
                if not output:
                    raise RuntimeError("Seedance returned empty output")
                # output is a URL to the video file
                video_url = output if isinstance(output, str) else str(output)
                break
            elif status in ("failed", "canceled"):
                error = data.get("error", "Unknown error")
                raise RuntimeError(f"Seedance generation failed: {error}")

            # Update progress based on elapsed time
            elapsed = time.time() - start_time
            progress = min(75, 20 + int((elapsed / timeout_seconds) * 55))
            if progress > last_progress and on_progress:
                last_progress = progress
                await on_progress(progress, "Gerando video realista com Seedance 2.0...")

            await asyncio.sleep(5)
        else:
            raise TimeoutError(f"Seedance generation timed out after {timeout_seconds}s")

    if on_progress:
        await on_progress(80, "Baixando video gerado...")

    # Step 3: Download the video
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        downloaded = False
        for attempt in range(4):
            try:
                resp = await client.get(video_url)
            except httpx.RequestError as e:
                if attempt >= 3:
                    raise RuntimeError(f"Falha ao baixar video gerado: {e}")
                wait_s = min(12, 2 ** (attempt + 1))
                logger.warning(
                    "Seedance download request error (attempt %d/4): %s. Retrying in %ds",
                    attempt + 1,
                    e,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code == 429:
                if attempt >= 3:
                    raise RuntimeError(SEEDANCE_RATE_LIMIT_MSG)
                wait_s = _retry_delay_from_header(resp.headers.get("Retry-After"), default_seconds=min(20, 2 ** (attempt + 2)))
                logger.warning(
                    "Seedance rate-limited on download (attempt %d/4). Retrying in %ds",
                    attempt + 1,
                    wait_s,
                )
                await asyncio.sleep(wait_s)
                continue

            resp.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(resp.content)
            downloaded = True
            break

        if not downloaded:
            raise RuntimeError("Nao foi possivel baixar o video do Seedance.")

    file_size = os.path.getsize(output_path)
    logger.info(f"Seedance video downloaded: {output_path} ({file_size} bytes)")

    return output_path
