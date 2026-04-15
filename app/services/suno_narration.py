"""
Suno Narration Generator — Generates spoken narration with background music via Suno API.

Unlike suno_music.py (instrumental only), this generates voice narration combined with
background music in a single audio track. When used, separate BGM generation is skipped.
"""
import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SUNO_BASE_URL = "https://api.sunoapi.org/api/v1"

# Voice style presets: each combines vocal description + background music atmosphere
VOICE_PRESETS = {
    "suno_narrator_male_deep": {
        "style": (
            "deep male spoken narration voice, warm baritone, calm and confident, "
            "slow deliberate pacing, soft ambient piano background, gentle atmospheric pads, "
            "no singing, spoken word only, podcast narrator style, Brazilian Portuguese, clear articulation"
        ),
        "gender": "m",
        "label": "Narrador Profundo",
    },
    "suno_narrator_male_dramatic": {
        "style": (
            "dramatic male spoken narrator, intense emotional delivery, suspenseful tone, "
            "cinematic orchestral background, tension strings, dark ambient undertones, "
            "no singing, spoken word narration, Brazilian Portuguese, clear articulation"
        ),
        "gender": "m",
        "label": "Narrador Dramatico",
    },
    "suno_narrator_female_soft": {
        "style": (
            "soft female spoken narration voice, warm and soothing, gentle pacing, "
            "ambient piano and strings background, calm atmosphere, no singing, "
            "audiobook narrator style, Brazilian Portuguese, clear articulation"
        ),
        "gender": "f",
        "label": "Narradora Suave",
    },
    "suno_narrator_female_energetic": {
        "style": (
            "energetic female spoken narrator, enthusiastic delivery, upbeat corporate background, "
            "light percussion, motivational tone, no singing, spoken word, "
            "Brazilian Portuguese, clear articulation"
        ),
        "gender": "f",
        "label": "Narradora Energetica",
    },
}

NEGATIVE_TAGS = (
    "singing, melodic vocals, chorus, harmony, pop vocals, autotune, "
    "falsetto, vibrato, humming, rapping, beatboxing"
)

# Approximate speech rate: ~2.5 words/sec for narration
WORDS_PER_SECOND = 2.5
# Max duration per Suno generation (V4_5ALL handles up to ~4 min reliably)
MAX_SEGMENT_WORDS = 600  # ~4 min of speech


def _format_narration_text(text: str) -> str:
    """Format script text for Suno narration prompt field.
    
    Adds [spoken] markers at paragraph boundaries and [pause] at ellipses
    to guide Suno's speech synthesis.
    """
    # Clean up text
    text = text.strip()
    
    # Replace ellipses with pause markers
    text = re.sub(r'\.{3,}', ' [pause] ', text)
    
    # Split into paragraphs and add spoken markers
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    
    if len(paragraphs) <= 1:
        return f"[spoken]\n{text}"
    
    formatted = []
    for p in paragraphs:
        formatted.append(f"[spoken]\n{p}")
    
    return "\n\n".join(formatted)


def _split_text_for_segments(text: str) -> list[str]:
    """Split long text into segments suitable for individual Suno generations.
    
    Each segment targets ~MAX_SEGMENT_WORDS words to stay within Suno's duration limits.
    """
    words = text.split()
    if len(words) <= MAX_SEGMENT_WORDS:
        return [text]
    
    # Split at paragraph boundaries when possible
    paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
    
    segments = []
    current_segment = []
    current_words = 0
    
    for para in paragraphs:
        para_words = len(para.split())
        if current_words + para_words > MAX_SEGMENT_WORDS and current_segment:
            segments.append('\n'.join(current_segment))
            current_segment = [para]
            current_words = para_words
        else:
            current_segment.append(para)
            current_words += para_words
    
    if current_segment:
        segments.append('\n'.join(current_segment))
    
    return segments


async def generate_suno_narration(
    text: str,
    voice_preset: str,
    project_id: int,
    tone: str = "",
) -> str:
    """Generate spoken narration with background music using Suno API.
    
    Args:
        text: The script/narration text
        voice_preset: Key from VOICE_PRESETS (e.g., "suno_narrator_male_deep")
        project_id: Project ID for file organization
        tone: Optional tone hint (e.g., "dramatico", "motivacional")
    
    Returns:
        Path to the generated audio file, or empty string on failure.
    """
    api_key = settings.suno_api_key
    if not api_key:
        logger.warning("SUNO_API_KEY not configured, cannot generate Suno narration")
        return ""
    
    preset = VOICE_PRESETS.get(voice_preset)
    if not preset:
        logger.warning(f"Unknown Suno voice preset: {voice_preset}")
        return ""
    
    # Output directory
    audio_dir = Path(settings.media_dir) / "audio" / str(project_id)
    audio_dir.mkdir(parents=True, exist_ok=True)
    
    # Split text into segments if too long
    segments = _split_text_for_segments(text)
    
    if len(segments) == 1:
        # Single generation
        output_path = str(audio_dir / "suno_narration.mp3")
        result = await _generate_single_narration(
            text=segments[0],
            preset=preset,
            tone=tone,
            output_path=output_path,
        )
        return result
    else:
        # Multiple segments: generate each, then concatenate
        logger.info(f"Suno narration: splitting into {len(segments)} segments for project {project_id}")
        segment_paths = []
        
        for i, segment_text in enumerate(segments):
            seg_path = str(audio_dir / f"suno_narration_seg{i:02d}.mp3")
            result = await _generate_single_narration(
                text=segment_text,
                preset=preset,
                tone=tone,
                output_path=seg_path,
            )
            if not result:
                logger.warning(f"Suno narration segment {i} failed, aborting")
                return ""
            segment_paths.append(result)
        
        # Concatenate segments with FFmpeg
        output_path = str(audio_dir / "suno_narration.mp3")
        success = await _concatenate_segments(segment_paths, output_path)
        if not success:
            # Fallback: return first segment
            logger.warning("Concatenation failed, using first segment only")
            return segment_paths[0] if segment_paths else ""
        
        return output_path


async def _generate_single_narration(
    text: str,
    preset: dict,
    tone: str,
    output_path: str,
) -> str:
    """Generate a single Suno narration segment.
    
    Returns output_path on success, empty string on failure.
    """
    # Build style with tone adjustment
    style = preset["style"]
    if tone:
        tone_hints = {
            "dramatico": ", dramatic emotional intensity, deep gravitas",
            "motivacional": ", inspiring motivational energy, uplifting",
            "misterioso": ", mysterious whispered undertone, suspenseful",
            "urgente": ", urgent fast-paced delivery, tense",
            "reflexivo": ", calm reflective contemplation, meditative",
            "descontraido": ", casual relaxed delivery, conversational",
            "inspirador": ", uplifting inspirational warmth, hopeful",
            "profundo": ", deep philosophical gravitas, thoughtful",
        }
        hint = tone_hints.get(tone.lower(), "")
        if hint and len(style) + len(hint) <= 1000:
            style += hint
    
    # Format text for narration
    formatted_text = _format_narration_text(text)
    
    headers = {
        "Authorization": f"Bearer {settings.suno_api_key}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "customMode": True,
        "instrumental": False,
        "model": "V4_5ALL",
        "prompt": formatted_text,
        "style": style,
        "negativeTags": NEGATIVE_TAGS,
        "vocalGender": preset["gender"],
        "title": "Narration",
        "callBackUrl": f"{settings.site_url}/api/suno-callback/narration",
    }
    
    logger.info(
        f"Suno narration: requesting (preset={preset['label']}, "
        f"text_len={len(text)}, style_len={len(style)})"
    )
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Start generation
            resp = await client.post(
                f"{SUNO_BASE_URL}/generate",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            resp_data = resp.json()
            logger.info(f"Suno narration: generate response code={resp_data.get('code')}")
            
            # Extract task ID
            inner = resp_data.get("data") or resp_data
            if isinstance(inner, dict):
                task_id = inner.get("taskId") or inner.get("task_id")
            else:
                task_id = None
            
            if not task_id:
                logger.warning(f"Suno narration: no taskId in response: {resp_data}")
                return ""
            
            logger.info(f"Suno narration: generation started, taskId={task_id}")
            
            # Step 2: Poll for completion (max 10 min for longer narrations)
            audio_url = await _poll_suno_task(client, headers, task_id, max_wait=600)
            if not audio_url:
                return ""
            
            # Step 3: Download the audio
            logger.info(f"Suno narration: downloading from {audio_url[:80]}...")
            dl_resp = await client.get(audio_url, timeout=120, follow_redirects=True)
            dl_resp.raise_for_status()
            
            with open(output_path, "wb") as f:
                f.write(dl_resp.content)
            
            file_size = Path(output_path).stat().st_size
            logger.info(f"Suno narration: saved to {output_path} ({file_size} bytes)")
            return output_path
    
    except Exception as e:
        logger.warning(f"Suno narration generation failed: {e}")
        return ""


async def _poll_suno_task(
    client: httpx.AsyncClient,
    headers: dict,
    task_id: str,
    max_wait: int = 600,
) -> str:
    """Poll Suno API for task completion. Returns audio_url or empty string.
    
    Replicates the polling logic from suno_music.py for consistency.
    """
    elapsed = 0
    interval = 5
    
    while elapsed < max_wait:
        await asyncio.sleep(interval)
        elapsed += interval
        
        try:
            resp = await client.get(
                f"{SUNO_BASE_URL}/generate/record-info",
                params={"taskId": task_id},
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
            result = resp.json()
            
            inner = result.get("data") or {}
            if not isinstance(inner, dict):
                inner = {}
            status = inner.get("status", "")
            
            if status == "SUCCESS":
                # Get audio URL from response
                response_obj = inner.get("response") or {}
                tracks = response_obj.get("sunoData", [])
                if isinstance(tracks, list) and tracks:
                    audio_url = tracks[0].get("audioUrl") or tracks[0].get("audio_url") or ""
                    if audio_url:
                        duration = tracks[0].get("duration")
                        logger.info(f"Suno narration: completed after {elapsed}s, duration={duration}s")
                        return audio_url
                
                # Try legacy format
                legacy_tracks = inner.get("data", [])
                if isinstance(legacy_tracks, list) and legacy_tracks:
                    audio_url = legacy_tracks[0].get("audio_url") or legacy_tracks[0].get("audioUrl") or ""
                    if audio_url:
                        logger.info(f"Suno narration: completed (legacy) after {elapsed}s")
                        return audio_url
                
                logger.warning("Suno narration: SUCCESS but no audio URL in response")
                return ""
            
            elif status == "FAILED":
                err = inner.get("errorMessage") or inner.get("errorCode") or "unknown"
                logger.warning(f"Suno narration: task failed after {elapsed}s, error: {err}")
                return ""
            
            # Still pending
            if elapsed % 30 == 0:
                logger.info(f"Suno narration: still generating... ({elapsed}s), status={status}")
        
        except Exception as e:
            logger.warning(f"Suno narration poll error: {e}")
    
    logger.warning(f"Suno narration: timed out after {max_wait}s")
    return ""


async def _concatenate_segments(segment_paths: list[str], output_path: str) -> bool:
    """Concatenate multiple audio segments using FFmpeg."""
    if len(segment_paths) == 1:
        import shutil
        shutil.copy2(segment_paths[0], output_path)
        return True
    
    try:
        # Create concat list file
        list_path = output_path + ".concat.txt"
        with open(list_path, "w") as f:
            for sp in segment_paths:
                # FFmpeg concat requires escaped paths
                escaped = sp.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")
        
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output_path,
        ]
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, timeout=120),
        )
        
        # Clean up concat list
        try:
            os.remove(list_path)
        except OSError:
            pass
        
        if result.returncode != 0:
            logger.warning(f"FFmpeg concat failed: {result.stderr.decode()[:200]}")
            return False
        
        logger.info(f"Suno narration: concatenated {len(segment_paths)} segments → {output_path}")
        return True
    
    except Exception as e:
        logger.warning(f"Segment concatenation failed: {e}")
        return False
