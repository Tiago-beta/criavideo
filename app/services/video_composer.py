"""
Video Composer — Uses FFmpeg to compose the final music video:
  1. Image slideshow with Ken Burns effect (zoom/pan)
  2. Optional Grok video clips at highlight moments
  3. Audio overlay
  4. Karaoke ASS subtitle burn-in
  5. Output H.264 MP4
"""
import os
import json
import logging
import subprocess
import shlex
from pathlib import Path
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def _build_ken_burns_filter(scene_count: int, scene_durations: list[float], width: int, height: int) -> str:
    """Build FFmpeg filter_complex for Ken Burns (zoom/pan) effect on images."""
    filters = []
    concat_inputs = []

    for i in range(scene_count):
        dur = scene_durations[i]
        frames = int(dur * 30)  # 30fps

        # Alternate between zoom-in, zoom-out, pan-left, pan-right
        effect = i % 4
        if effect == 0:  # Zoom in
            zoom_expr = f"min(zoom+0.0008,1.3)"
            x_expr = f"iw/2-(iw/zoom/2)"
            y_expr = f"ih/2-(ih/zoom/2)"
        elif effect == 1:  # Zoom out
            zoom_expr = f"if(eq(on,1),1.3,max(zoom-0.0008,1.0))"
            x_expr = f"iw/2-(iw/zoom/2)"
            y_expr = f"ih/2-(ih/zoom/2)"
        elif effect == 2:  # Pan right
            zoom_expr = "1.1"
            x_expr = f"if(eq(on,1),0,min(x+2,iw-iw/zoom))"
            y_expr = f"ih/2-(ih/zoom/2)"
        else:  # Pan left
            zoom_expr = "1.1"
            x_expr = f"if(eq(on,1),iw-iw/zoom,max(x-2,0))"
            y_expr = f"ih/2-(ih/zoom/2)"

        filters.append(
            f"[{i}:v]scale=8000:-1,zoompan=z='{zoom_expr}':"
            f"x='{x_expr}':y='{y_expr}':"
            f"d={frames}:s={width}x{height}:fps=30,"
            f"setpts=PTS-STARTPTS[v{i}]"
        )
        concat_inputs.append(f"[v{i}]")

    filter_str = ";\n".join(filters)
    concat = "".join(concat_inputs) + f"concat=n={scene_count}:v=1:a=0[slideshow]"
    return f"{filter_str};\n{concat}"


def compose_video(
    project_id: int,
    scenes: list[dict],
    audio_path: str,
    subtitle_path: str,
    aspect_ratio: str = "16:9",
    output_dir: str = "",
) -> dict:
    """Compose the final video using FFmpeg.

    scenes: list of {"image_path": str, "clip_path": str|None, "start_time": float, "end_time": float, "scene_type": str}
    Returns: {"file_path": str, "duration": float, "file_size": int}
    """
    if not output_dir:
        output_dir = os.path.join(settings.media_dir, "renders", str(project_id))
    os.makedirs(output_dir, exist_ok=True)

    if aspect_ratio == "9:16":
        width, height = 1080, 1920
    else:
        width, height = 1920, 1080

    output_path = os.path.join(output_dir, f"video_{aspect_ratio.replace(':', 'x')}.mp4")

    # Separate image-only scenes from video clip scenes
    image_scenes = []
    clip_inserts = []

    for s in scenes:
        if s.get("scene_type") == "video_clip" and s.get("clip_path") and os.path.exists(s["clip_path"]):
            clip_inserts.append(s)
        elif s.get("image_path") and os.path.exists(s["image_path"]):
            image_scenes.append(s)

    if not image_scenes and not clip_inserts:
        raise RuntimeError("No valid scenes to compose")

    # Calculate durations for each image scene
    scene_durations = []
    for s in image_scenes:
        dur = s.get("end_time", 0) - s.get("start_time", 0)
        scene_durations.append(max(dur, 3.0))

    # Build FFmpeg command
    input_args = []
    for s in image_scenes:
        input_args.extend(["-loop", "1", "-t", str(scene_durations[len(input_args) // 4]), "-i", s["image_path"]])

    # Audio input (last input)
    audio_idx = len(image_scenes)
    input_args.extend(["-i", audio_path])

    # Build filter complex
    filter_complex = _build_ken_burns_filter(len(image_scenes), scene_durations, width, height)

    # Add subtitle burn-in if available
    if subtitle_path and os.path.exists(subtitle_path):
        sub_path_escaped = subtitle_path.replace("\\", "/").replace(":", "\\:")
        filter_complex += f";\n[slideshow]ass='{sub_path_escaped}'[final]"
        video_output = "[final]"
    else:
        video_output = "[slideshow]"

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", video_output,
        "-map", f"{audio_idx}:a",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        output_path
    ]

    logger.info(f"Running FFmpeg: {' '.join(cmd[:10])}...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

    if result.returncode != 0:
        logger.error(f"FFmpeg error: {result.stderr[-1000:]}")
        raise RuntimeError(f"FFmpeg failed: {result.stderr[-500:]}")

    file_size = os.path.getsize(output_path)

    # Get duration via ffprobe
    duration = _get_duration(output_path)

    logger.info(f"Video rendered: {output_path} ({file_size / 1024 / 1024:.1f} MB, {duration:.1f}s)")
    return {"file_path": output_path, "duration": duration, "file_size": file_size}


def _get_duration(file_path: str) -> float:
    """Get video duration using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", file_path],
            capture_output=True, text=True, timeout=30
        )
        info = json.loads(result.stdout)
        return float(info["format"]["duration"])
    except Exception:
        return 0.0
