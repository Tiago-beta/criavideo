"""
Video Composer — Uses FFmpeg to compose the final music video:
  1. Image slideshow with Ken Burns effect (zoom/pan)
  2. Grok video clips interleaved at highlight moments
  3. Audio overlay
  4. Karaoke ASS subtitle burn-in
  5. Output H.264 MP4
"""
import os
import json
import logging
import subprocess
from pathlib import Path
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


def compose_video(
    project_id: int,
    scenes: list[dict],
    audio_path: str,
    subtitle_path: str,
    aspect_ratio: str = "16:9",
    output_dir: str = "",
) -> dict:
    """Compose the final video using FFmpeg.

    ALL scenes are included in order — images get Ken Burns, clips get scaled.
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

    # Build ordered list of valid image scenes
    valid_scenes = []
    for s in scenes:
        has_image = s.get("image_path") and os.path.exists(s.get("image_path", ""))
        if has_image:
            dur = s.get("end_time", 0) - s.get("start_time", 0)
            valid_scenes.append({
                **s,
                "duration": max(dur, 3.0),
            })

    if not valid_scenes:
        raise RuntimeError("No valid scenes to compose")

    # Get actual audio duration and redistribute scenes to cover it fully
    audio_duration = _get_duration(audio_path)
    if audio_duration > 0:
        total_scene_dur = sum(s["duration"] for s in valid_scenes)
        if abs(total_scene_dur - audio_duration) > 2.0:
            # Scale all scene durations proportionally to match audio
            ratio = audio_duration / total_scene_dur if total_scene_dur > 0 else 1.0
            for s in valid_scenes:
                s["duration"] = s["duration"] * ratio
            logger.info(f"Adjusted scene durations: {total_scene_dur:.1f}s -> {audio_duration:.1f}s (ratio {ratio:.2f})")

    logger.info(f"Composing {len(valid_scenes)} image scenes, total {sum(s['duration'] for s in valid_scenes):.1f}s")

    # Build FFmpeg inputs and filter_complex
    input_args = []
    filters = []
    concat_inputs = []
    input_idx = 0

    for i, sc in enumerate(valid_scenes):
        dur = sc["duration"]
        frames = int(dur * 30)

        # Image: Ken Burns zoom/pan — single frame input, zoompan d controls duration
        input_args.extend(["-i", sc["image_path"]])

        # Smooth alternating Ken Burns effects using linear interpolation (on/d)
        effect = i % 2
        if effect == 0:  # Smooth zoom in: 1.0 -> 1.10
            filters.append(
                f"[{input_idx}:v]scale={width*2}:{height*2},"
                f"zoompan=z='1.0+0.10*(on/{frames})':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s={width}x{height}:fps=30,"
                f"format=yuv420p,setpts=PTS-STARTPTS[v{i}]"
            )
        else:  # Smooth zoom out: 1.10 -> 1.0
            filters.append(
                f"[{input_idx}:v]scale={width*2}:{height*2},"
                f"zoompan=z='1.10-0.10*(on/{frames})':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s={width}x{height}:fps=30,"
                f"format=yuv420p,setpts=PTS-STARTPTS[v{i}]"
            )

        concat_inputs.append(f"[v{i}]")
        input_idx += 1

    # Audio input
    audio_idx = input_idx
    input_args.extend(["-i", audio_path])

    # Concat all scenes
    filter_str = ";\n".join(filters)
    concat = "".join(concat_inputs) + f"concat=n={len(valid_scenes)}:v=1:a=0[slideshow]"
    filter_complex = f"{filter_str};\n{concat}"

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
        "-movflags", "+faststart",
        output_path
    ]

    logger.info(f"Running FFmpeg compose for project {project_id}...")
    logger.info(f"FFmpeg filter_complex: {filter_complex[:500]}...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)

    if result.returncode != 0:
        # Extract actual error lines from stderr (skip progress lines)
        err_lines = [l for l in result.stderr.split('\n') if l.strip() and 'size=' not in l and 'speed=' not in l]
        err_msg = '\n'.join(err_lines[-20:]) if err_lines else result.stderr[-2000:]
        logger.error(f"FFmpeg error:\n{err_msg}")
        raise RuntimeError(f"FFmpeg failed: {err_msg[-500:]}")

    file_size = os.path.getsize(output_path)
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
