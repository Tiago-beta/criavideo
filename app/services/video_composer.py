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
    background_music_path: str = "",
    enable_zoom: bool = True,
    enable_audio_spectrum: bool = False,
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
    elif aspect_ratio == "1:1":
        width, height = 1080, 1080
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

        # For long videos: cycle scenes so each image shows ~12s instead of stretching
        if total_scene_dur > 0 and audio_duration / total_scene_dur > 2.0:
            target_per_scene = 12.0  # seconds per scene appearance
            needed_total = audio_duration
            cycle_scenes = []
            t = 0.0
            idx = 0
            while t < needed_total:
                src = valid_scenes[idx % len(valid_scenes)]
                dur = min(target_per_scene, needed_total - t)
                if dur < 2.0:
                    break
                cycle_scenes.append({**src, "duration": dur})
                t += dur
                idx += 1
            valid_scenes = cycle_scenes
            logger.info(f"Long video: cycled {len(valid_scenes)} scene slots ({audio_duration:.0f}s, "
                         f"{len(scenes)} unique images)")
        elif abs(total_scene_dur - audio_duration) > 2.0:
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
        frames = max(int(dur * 30), 1)

        # Image: Ken Burns zoom/pan — single frame input, zoompan d controls duration
        input_args.extend(["-i", sc["image_path"]])

        # For long videos (>10min), skip 2x upscale to keep render feasible
        zoom_scale = 1 if audio_duration > 600 else 2

        # Ken Burns zoom/pan effect (optional)
        if enable_zoom:
            effect = i % 2
            if effect == 0:  # Suave zoom in: 1.0 -> 1.06
                filters.append(
                    f"[{input_idx}:v]scale={width*zoom_scale}:{height*zoom_scale},"
                    f"zoompan=z='1.0+0.06*(on/{frames})':"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d={frames}:s={width}x{height}:fps=30,"
                    f"format=yuv420p,setsar=1,setpts=PTS-STARTPTS[v{i}]"
                )
            else:  # Suave zoom out: 1.06 -> 1.0
                filters.append(
                    f"[{input_idx}:v]scale={width*zoom_scale}:{height*zoom_scale},"
                    f"zoompan=z='1.06-0.06*(on/{frames})':"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d={frames}:s={width}x{height}:fps=30,"
                    f"format=yuv420p,setsar=1,setpts=PTS-STARTPTS[v{i}]"
                )
        else:
            filters.append(
                f"[{input_idx}:v]scale={width}:{height},"
                f"zoompan=z='1.0':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={frames}:s={width}x{height}:fps=30,"
                f"format=yuv420p,setsar=1,setpts=PTS-STARTPTS[v{i}]"
            )

        concat_inputs.append(f"[v{i}]")
        input_idx += 1

    # Audio input
    audio_idx = input_idx
    input_args.extend(["-i", audio_path])
    input_idx += 1

    # Background music input (optional — loop seamlessly for long videos)
    music_idx = None
    if background_music_path and os.path.exists(background_music_path):
        music_idx = input_idx
        input_args.extend(["-stream_loop", "-1", "-i", background_music_path])
        input_idx += 1

    # Concat all scenes
    filter_str = ";\n".join(filters)
    concat = "".join(concat_inputs) + f"concat=n={len(valid_scenes)}:v=1:a=0[slideshow]"
    filter_complex = f"{filter_str};\n{concat}"

    video_output = "[slideshow]"

    if enable_audio_spectrum:
        spectrum_height = max(120, int(height * 0.18))
        spectrum_y = max(height - spectrum_height - 28, 0)
        filter_complex += (
            f";\n[{audio_idx}:a]aformat=channel_layouts=mono,"
            f"showwaves=s={width}x{spectrum_height}:mode=cline:colors=0xF6A52F,format=rgba[spectrum];"
            f"{video_output}[spectrum]overlay=0:{spectrum_y}:shortest=1[with_spectrum]"
        )
        video_output = "[with_spectrum]"
        logger.info("Audio spectrum overlay enabled")

    # Add subtitle burn-in if available
    if subtitle_path and os.path.exists(subtitle_path):
        sub_path_escaped = subtitle_path.replace("\\", "/").replace(":", "\\:")
        filter_complex += f";\n{video_output}ass='{sub_path_escaped}'[final]"
        video_output = "[final]"

    # Mix narration with background music if provided
    if music_idx is not None:
        fade_start = max(audio_duration - 4, 0)
        filter_complex += (
            f";\n[{audio_idx}:a]volume=1.0[narration];"
            f"[{music_idx}:a]volume=0.18,afade=t=out:st={fade_start}:d=4[bgm];"
            f"[narration][bgm]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[audioout]"
        )
        audio_output = "[audioout]"
        logger.info(f"Audio mixing: narration[{audio_idx}] + bgm[{music_idx}] vol=0.18 normalize=0")
    else:
        audio_output = f"{audio_idx}:a"

    # Use faster encoding preset for long videos to keep render time reasonable
    encode_preset = "ultrafast" if audio_duration > 900 else ("fast" if audio_duration > 600 else "medium")

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", video_output,
        "-map", audio_output,
        "-c:v", "libx264",
        "-preset", encode_preset,
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        output_path
    ]

    logger.info(f"Running FFmpeg compose for project {project_id} (preset={encode_preset})...")
    logger.info(f"FFmpeg filter_complex: {filter_complex[:500]}...")
    # Timeout scales with video duration: min 30 min, max 4 hours
    ffmpeg_timeout = max(1800, min(int(audio_duration * 4), 14400))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=ffmpeg_timeout)

    if result.returncode != 0:
        # Extract actual error lines from stderr (skip progress lines)
        err_lines = [l for l in result.stderr.split('\n') if l.strip() and 'size=' not in l and 'speed=' not in l]
        err_msg = '\n'.join(err_lines[-20:]) if err_lines else result.stderr[-2000:]
        logger.error(f"FFmpeg error:\n{err_msg}")
        logger.warning("Trying safe fallback renderer (all images, no zoom)")
        try:
            _render_static_fallback(
                valid_scenes=valid_scenes,
                audio_path=audio_path,
                output_path=output_path,
                width=width,
                height=height,
                duration=audio_duration,
                subtitle_path=subtitle_path,
                background_music_path=background_music_path,
            )
            file_size = os.path.getsize(output_path)
            duration = _get_duration(output_path)
            logger.info(f"Fallback video rendered: {output_path} ({file_size / 1024 / 1024:.1f} MB, {duration:.1f}s)")
            return {"file_path": output_path, "duration": duration, "file_size": file_size}
        except Exception as fallback_error:
            logger.error(f"Fallback renderer also failed: {fallback_error}")
            raise RuntimeError(f"FFmpeg failed: {err_msg[-500:]}")

    file_size = os.path.getsize(output_path)
    duration = _get_duration(output_path)

    logger.info(f"Video rendered: {output_path} ({file_size / 1024 / 1024:.1f} MB, {duration:.1f}s)")
    return {"file_path": output_path, "duration": duration, "file_size": file_size}


def reformat_video(
    project_id: int,
    source_video_path: str,
    aspect_ratio: str = "16:9",
    output_dir: str = "",
) -> dict:
    """Create a format-converted copy from an already rendered video.

    This keeps the same visual content and audio, only adapting framing/resolution
    to the requested aspect ratio for cross-platform posting.
    """
    if not source_video_path or not os.path.exists(source_video_path):
        raise FileNotFoundError(f"Source video not found: {source_video_path}")

    if not output_dir:
        output_dir = os.path.join(settings.media_dir, "renders", str(project_id))
    os.makedirs(output_dir, exist_ok=True)

    if aspect_ratio == "9:16":
        width, height = 1080, 1920
    elif aspect_ratio == "1:1":
        width, height = 1080, 1080
    else:
        width, height = 1920, 1080

    output_path = os.path.join(output_dir, f"video_{aspect_ratio.replace(':', 'x')}.mp4")
    filter_v = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )

    src_duration = max(_get_duration(source_video_path), 1.0)
    timeout = max(600, min(int(src_duration * 4), 7200))

    cmd = [
        "ffmpeg", "-y",
        "-i", source_video_path,
        "-vf", filter_v,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info(
        f"Reformatting video for project {project_id}: {source_video_path} -> {aspect_ratio}"
    )
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        err_lines = [l for l in result.stderr.split('\n') if l.strip() and 'size=' not in l and 'speed=' not in l]
        err_msg = '\n'.join(err_lines[-20:]) if err_lines else result.stderr[-2000:]
        raise RuntimeError(f"FFmpeg reformat failed: {err_msg[-500:]}")

    file_size = os.path.getsize(output_path)
    duration = _get_duration(output_path)
    logger.info(f"Reformatted video: {output_path} ({file_size / 1024 / 1024:.1f} MB, {duration:.1f}s)")
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


def _render_static_fallback(
    valid_scenes: list[dict],
    audio_path: str,
    output_path: str,
    width: int,
    height: int,
    duration: float,
    subtitle_path: str = "",
    background_music_path: str = "",
    **_kwargs,
) -> None:
    """Safe fallback renderer: static slideshow (all images) + audio, no zoompan."""
    if duration <= 0:
        duration = max(_get_duration(audio_path), 5.0)

    input_args = []
    filters = []
    concat_inputs = []

    for i, sc in enumerate(valid_scenes):
        dur = sc.get("duration", 10.0)
        frames = max(int(dur * 30), 1)
        input_args.extend(["-i", sc["image_path"]])
        filters.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps=30,format=yuv420p,setsar=1,"
            f"trim=duration={dur:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        concat_inputs.append(f"[v{i}]")

    audio_idx = len(valid_scenes)
    input_args.extend(["-i", audio_path])

    music_idx = None
    if background_music_path and os.path.exists(background_music_path):
        music_idx = audio_idx + 1
        input_args.extend(["-stream_loop", "-1", "-i", background_music_path])

    filter_str = ";\n".join(filters)
    concat = "".join(concat_inputs) + f"concat=n={len(valid_scenes)}:v=1:a=0[slideshow]"
    filter_complex = f"{filter_str};\n{concat}"

    video_output = "[slideshow]"
    if subtitle_path and os.path.exists(subtitle_path):
        sub_path_escaped = subtitle_path.replace("\\", "/").replace(":", "\\:")
        filter_complex += f";[slideshow]ass='{sub_path_escaped}'[final]"
        video_output = "[final]"

    if music_idx is not None:
        fade_start = max(duration - 4, 0)
        filter_complex += (
            f";[{audio_idx}:a]volume=1.0[narration];"
            f"[{music_idx}:a]volume=0.18,afade=t=out:st={fade_start}:d=4[bgm];"
            f"[narration][bgm]amix=inputs=2:duration=first:dropout_transition=3:normalize=0[audioout]"
        )
        audio_output = "[audioout]"
    else:
        audio_output = f"{audio_idx}:a"

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", filter_complex,
        "-map", video_output,
        "-map", audio_output,
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info(f"Fallback renderer: {len(valid_scenes)} images, {duration:.1f}s")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        err_lines = [l for l in result.stderr.split('\n') if l.strip() and 'size=' not in l and 'speed=' not in l]
        err_msg = '\n'.join(err_lines[-20:]) if err_lines else result.stderr[-2000:]
        raise RuntimeError(f"Fallback FFmpeg failed: {err_msg[-500:]}")


def compose_overlay_video(
    project_id: int,
    video_path: str,
    subtitle_path: str = "",
    narration_path: str = "",
    aspect_ratio: str = "16:9",
    output_dir: str = "",
) -> dict:
    """Overlay subtitles and optional narration on a user-uploaded video.

    Returns: {"file_path": str, "duration": float, "file_size": int}
    """
    if not video_path or not os.path.exists(video_path):
        raise FileNotFoundError(f"Source video not found: {video_path}")

    if not output_dir:
        output_dir = os.path.join(settings.media_dir, "renders", str(project_id))
    os.makedirs(output_dir, exist_ok=True)

    if aspect_ratio == "9:16":
        width, height = 1080, 1920
    elif aspect_ratio == "1:1":
        width, height = 1080, 1080
    else:
        width, height = 1920, 1080

    output_path = os.path.join(output_dir, f"video_{aspect_ratio.replace(':', 'x')}.mp4")
    video_duration = max(_get_duration(video_path), 1.0)

    # Build filter_complex
    input_args = ["-i", video_path]
    input_idx = 1  # 0 = video

    # Scale and crop to target aspect ratio
    vf = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},format=yuv420p,setsar=1[scaled]"
    )

    # Subtitle burn-in
    if subtitle_path and os.path.exists(subtitle_path):
        sub_path_escaped = subtitle_path.replace("\\", "/").replace(":", "\\:")
        vf += f";\n[scaled]ass='{sub_path_escaped}'[vout]"
        video_output = "[vout]"
    else:
        video_output = "[scaled]"

    # Audio handling: mix narration with original video audio
    if narration_path and os.path.exists(narration_path):
        input_args.extend(["-i", narration_path])
        narr_idx = input_idx
        input_idx += 1
        # Lower original audio volume and mix with narration
        fade_start = max(video_duration - 4, 0)
        vf += (
            f";\n[0:a]volume=0.15[origaudio];"
            f"[{narr_idx}:a]volume=1.0[narration];"
            f"[narration][origaudio]amix=inputs=2:duration=first:dropout_transition=3:normalize=0,"
            f"afade=t=out:st={fade_start}:d=4[audioout]"
        )
        audio_output = "[audioout]"
    else:
        # Keep original audio as-is
        audio_output = "0:a"

    timeout = max(600, min(int(video_duration * 4), 7200))

    cmd = [
        "ffmpeg", "-y",
        *input_args,
        "-filter_complex", vf,
        "-map", video_output,
        "-map", audio_output,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]

    logger.info(f"Composing overlay video for project {project_id} ({video_duration:.1f}s)...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    if result.returncode != 0:
        err_lines = [l for l in result.stderr.split('\n') if l.strip() and 'size=' not in l and 'speed=' not in l]
        err_msg = '\n'.join(err_lines[-20:]) if err_lines else result.stderr[-2000:]
        logger.error(f"Overlay FFmpeg error:\n{err_msg}")
        raise RuntimeError(f"FFmpeg overlay failed: {err_msg[-500:]}")

    file_size = os.path.getsize(output_path)
    duration = _get_duration(output_path)

    logger.info(f"Overlay video rendered: {output_path} ({file_size / 1024 / 1024:.1f} MB, {duration:.1f}s)")
    return {"file_path": output_path, "duration": duration, "file_size": file_size}
