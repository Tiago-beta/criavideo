"""
Multi-clip video generation for Grok.
Generates videos longer than 15s by chaining multiple Grok clips (max 15s each).
Extracts the last frame of each clip as reference for the next, maintaining visual continuity.
"""
import os
import json
import asyncio
import logging
import subprocess
from pathlib import Path

import openai
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_SCENE_SPLIT_SYSTEM = """Voce e um planejador de cenas para video cinematografico.

Dada a descricao do video e o numero de segmentos, divida em descricoes de cena sequenciais.
Cada cena deve:
1. Ser visualmente distinta, mas manter continuidade narrativa e visual com a cena anterior
2. Incluir movimentos de camera, iluminacao e acoes especificas
3. SEMPRE descrever os MESMOS personagens com tracos fisicos IDENTICOS (cabelo, roupa, biotipo, idade, genero) em TODAS as cenas
4. Reutilizar os mesmos detalhes de local/ambiente quando fizer sentido para continuidade
5. Ser escrita em portugues do Brasil (pt-BR)
6. Incluir nota de continuidade para cenas 2+ (ex.: "Continuacao direta da cena anterior...")
7. Incluir duas linhas de continuidade em toda cena:
    - CHARACTER_LOCK: identidade completa do elenco (nome, idade, cabelo, roupa, biotipo) copiada literalmente em todas as cenas
    - WORLD_LOCK: local, horario, clima e humor copiados literalmente em todas as cenas
8. Ser concisa e viva (ate 200 palavras por cena)

CRITICO: As cenas formam uma historia continua. Toda cena deve manter os mesmos protagonistas, mesmas cores de figurino e mesma dinamica de relacao, sem drift de personagens.

Saida SOMENTE em JSON array de strings, uma por cena. Sem markdown e sem explicacao.
Exemplo para 3 cenas: ["descricao da cena 1", "descricao da cena 2", "descricao da cena 3"]"""


async def generate_scene_prompts(
    base_prompt: str,
    num_segments: int,
    duration_per_segment: int = 15,
) -> list[str]:
    """Split a video prompt into N sequential scene prompts using GPT-4o."""
    client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

    user_msg = (
        f"Video total: {num_segments * duration_per_segment}s dividido em {num_segments} segmentos "
        f"de {duration_per_segment}s cada.\n\n"
        f"Descricao base:\n{base_prompt}\n\n"
        "Nao substitua nem altere os personagens principais entre as cenas. "
        "Mantenha uma unica historia coerente e continuidade de contexto do inicio ao fim.\n\n"
        "Todas as descricoes e falas devem estar em portugues do Brasil (pt-BR).\n\n"
        f"Gere {num_segments} descricoes de cena em JSON array."
    )

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _SCENE_SPLIT_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.45,
            max_tokens=2000,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()

        scenes = json.loads(raw)
        if not isinstance(scenes, list) or len(scenes) != num_segments:
            logger.warning(f"Scene split returned {len(scenes) if isinstance(scenes, list) else 'non-list'}, expected {num_segments}. Using fallback.")
            return [base_prompt] * num_segments
        logger.info(f"Scene prompts generated: {num_segments} segments")
        return scenes
    except Exception as e:
        logger.warning(f"Scene prompt split failed: {e}. Using base prompt for all segments.")
        return [base_prompt] * num_segments


async def extract_last_frame(video_path: str, output_path: str) -> str:
    """Extract the last frame of a video as PNG using FFmpeg."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Get duration first
    probe_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    try:
        dur = float(stdout.decode().strip())
    except (ValueError, AttributeError):
        dur = 10.0

    # Seek to near the end and grab one frame
    seek_time = max(0, dur - 0.1)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(seek_time),
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        output_path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"Failed to extract last frame: {stderr.decode()[:300]}")

    logger.info(f"Extracted last frame from {video_path} -> {output_path}")
    return output_path


async def concatenate_clips(clip_paths: list[str], output_path: str, crossfade_dur: float = 0.5) -> str:
    """Concatenate video clips while preserving scene audio."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if len(clip_paths) == 1:
        import shutil
        shutil.copy2(clip_paths[0], output_path)
        return output_path

    inputs = []
    for path in clip_paths:
        inputs.extend(["-i", path])

    n = len(clip_paths)
    filter_parts: list[str] = []

    clip_durations = [await _get_clip_duration(path) for path in clip_paths]

    audio_labels: list[str] = []
    for i, path in enumerate(clip_paths):
        normalized_label = f"[a{i}]"
        if await _clip_has_audio(path):
            filter_parts.append(
                f"[{i}:a]aresample=async=1:first_pts=0,aformat=sample_rates=44100:channel_layouts=stereo{normalized_label}"
            )
        else:
            duration = max(0.1, float(clip_durations[i] or 0.1))
            filter_parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=44100,atrim=duration={duration:.2f}{normalized_label}"
            )
        audio_labels.append(normalized_label)

    if crossfade_dur <= 0:
        video_labels = "".join(f"[{i}:v]" for i in range(n))
        filter_parts.append(f"{video_labels}concat=n={n}:v=1:a=0[outv]")
        filter_parts.append(f"{''.join(audio_labels)}concat=n={n}:v=0:a=1[outa]")
    elif n == 2:
        video_filter = (
            f"[0:v][1:v]xfade=transition=fade:duration={crossfade_dur}:offset=OFFSET0[outv]"
        )
        # Get duration of first clip to compute offset
        offset0 = max(0, clip_durations[0] - crossfade_dur)
        filter_parts.append(video_filter.replace("OFFSET0", f"{offset0:.2f}"))
        filter_parts.append(f"{audio_labels[0]}{audio_labels[1]}acrossfade=d={crossfade_dur}[outa]")
    elif n <= 5:
        # Chain xfade for 3-5 clips
        offsets = []
        cumulative = 0.0
        for i in range(n - 1):
            dur_i = clip_durations[i]
            cumulative += dur_i - (crossfade_dur if i > 0 else 0)
            offsets.append(cumulative - crossfade_dur)

        prev_video = "[0:v]"
        for i in range(1, n):
            next_label = "[outv]" if i == n - 1 else f"[v{i}]"
            filter_parts.append(
                f"{prev_video}[{i}:v]xfade=transition=fade:duration={crossfade_dur}:offset={offsets[i-1]:.2f}{next_label}"
            )
            prev_video = next_label if i < n - 1 else ""

        prev_audio = audio_labels[0]
        for i in range(1, n):
            next_label = "[outa]" if i == n - 1 else f"[ax{i}]"
            filter_parts.append(
                f"{prev_audio}{audio_labels[i]}acrossfade=d={crossfade_dur}{next_label}"
            )
            prev_audio = next_label if i < n - 1 else ""
    else:
        # Fallback: simple concat without crossfade for many clips
        concat_file = output_path + ".txt"
        with open(concat_file, "w") as f:
            for path in clip_paths:
                f.write(f"file '{path}'\n")
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_file, "-c", "copy", output_path,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        os.remove(concat_file)
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg concat failed: {stderr.decode()[:300]}")
        return output_path

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264", "-crf", "22", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-pix_fmt", "yuv420p",
        output_path,
    ]

    logger.info(
        "Concatenating %s clips with %s...",
        n,
        "hard cuts" if crossfade_dur <= 0 else f"crossfade {crossfade_dur:.2f}s",
    )
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg crossfade concat failed: {stderr.decode()[:300]}")

    logger.info(f"Clips concatenated: {output_path}")
    return output_path


async def _clip_has_audio(path: str) -> bool:
    """Return True when the clip has at least one audio stream."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    return proc.returncode == 0 and bool((stdout or b"").decode().strip())


async def _get_clip_duration(path: str) -> float:
    """Get video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except (ValueError, AttributeError):
        return 15.0


async def generate_multi_clip_video(
    project_id: int,
    optimized_prompt: str,
    total_duration: int,
    aspect_ratio: str,
    image_path: str | None,
    render_dir,
    reuse_base_reference_for_all_clips: bool = False,
    on_progress=None,
    reference_mode: str = "",
) -> str:
    """Generate a long Grok video by chaining multiple 15s clips.

    1. Split prompt into N scene prompts (with character consistency)
    2. Generate reference images for ALL scenes upfront
    3. For each clip: generate video using its pre-generated reference image
    4. Concatenate all clips with crossfade
    5. Return final video path
    """
    from app.services.grok_video import generate_video_clip, optimize_prompt_for_grok
    from app.services.scene_generator import generate_scene_image

    render_dir = Path(render_dir)
    clips_dir = render_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    # Calculate segments
    max_per_clip = 15
    num_segments = -(-total_duration // max_per_clip)  # ceil division
    last_clip_dur = total_duration - (num_segments - 1) * max_per_clip
    if last_clip_dur < 3:
        num_segments -= 1
        last_clip_dur += max_per_clip

    logger.info(f"Multi-clip: {total_duration}s -> {num_segments} segments (last={last_clip_dur}s)")
    base_reference_image = image_path if (image_path and os.path.exists(image_path)) else ""

    if on_progress:
        await on_progress(16, f"Planejando {num_segments} cenas...")

    # Step 1: Generate scene prompts
    if reuse_base_reference_for_all_clips:
        # In strict persona-anchor mode, avoid scene rewrites to keep the same composition.
        scene_prompts = [optimized_prompt] * num_segments
        logger.info(
            "Multi-clip strict reference mode enabled: reusing base prompt for all %s segments",
            num_segments,
        )
    else:
        scene_prompts = await generate_scene_prompts(
            base_prompt=optimized_prompt,
            num_segments=num_segments,
            duration_per_segment=max_per_clip,
        )

    # Optimize each scene prompt for Grok
    optimized_scenes = []
    face_identity_only = str(reference_mode or "").strip().lower() in {"face_identity_only", "face_only", "persona_face"}
    if face_identity_only:
        continuity_lock = (
            "\n\nTRAVA DE CONTINUIDADE FACIAL: mantenha os mesmos personagens principais apenas pela identidade do rosto, "
            "idade aparente, tom de pele e cabelo. Nao introduza novo protagonista."
            "\nTRAVA DE VARIEDADE: roupas, cenario, luz, pose, paleta e objetos devem variar conforme cada cena e nao devem copiar a foto da persona."
            "\nTRAVA DE CLOSE-UP: mantenha exatamente a mesma identidade facial em planos fechados, sem face swap e sem morphing facial."
        )
    else:
        continuity_lock = (
            "\n\nTRAVA DE CONTINUIDADE: mantenha EXATAMENTE os mesmos personagens principais, tracos faciais, "
            "cores de figurino, biotipo, idade e contexto relacional em todas as cenas. Nao introduza novo protagonista."
            "\nTRAVA DE CLOSE-UP: mantenha exatamente a mesma identidade facial em planos fechados, sem face swap e sem morphing facial."
        )
    for i, sp in enumerate(scene_prompts):
        dur = last_clip_dur if i == num_segments - 1 else max_per_clip
        opt = await optimize_prompt_for_grok(
            user_description=f"{sp}{continuity_lock}",
            duration=dur,
            has_reference_image=bool(base_reference_image),
            reference_mode=reference_mode,
        )
        optimized_scenes.append(opt)
        logger.info(f"Scene {i+1}/{num_segments} prompt optimized ({len(opt)} chars)")

    if on_progress:
        await on_progress(20, "Gerando imagens de referencia para todas as cenas...")

    # Step 2: Generate reference images for ALL scenes upfront
    # This ensures character/setting consistency across the entire video
    img_dir = clips_dir / "ref_images"
    img_dir.mkdir(parents=True, exist_ok=True)

    ref_images = []
    for i in range(num_segments):
        if base_reference_image and (reuse_base_reference_for_all_clips or i == 0):
            # Reuse a stable persona anchor when requested to avoid identity drift.
            ref_images.append(base_reference_image)
            logger.info(
                "Reference image %s/%s reusing base scene anchor (reuse_all=%s): %s",
                i + 1,
                num_segments,
                reuse_base_reference_for_all_clips,
                base_reference_image,
            )

            if on_progress:
                pct = 20 + int(10 * (i + 1) / num_segments)
                await on_progress(pct, f"Imagem de referencia {i+1}/{num_segments} preparada...")
        else:
            ref_path = str(img_dir / f"reference_{i}.png")
            img_prompt = optimized_scenes[i][:500]
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                generate_scene_image,
                img_prompt,
                aspect_ratio,
                ref_path,
                True,
                base_reference_image,
                "",
                None,
                reference_mode,
            )
            ref_images.append(ref_path)
            logger.info(f"Reference image {i+1}/{num_segments} generated: {ref_path}")

            if on_progress:
                pct = 20 + int(10 * (i + 1) / num_segments)
                await on_progress(pct, f"Imagem de referencia {i+1}/{num_segments} gerada...")

    # Step 3: Generate clips sequentially using pre-generated reference images
    clip_paths = []

    for i in range(num_segments):
        clip_dur = last_clip_dur if i == num_segments - 1 else max_per_clip
        clip_path = str(clips_dir / f"clip_{i:02d}.mp4")

        pct_base = 30 + int(50 * i / num_segments)
        pct_end = 30 + int(50 * (i + 1) / num_segments)

        if on_progress:
            await on_progress(pct_base, f"Gerando clip {i+1}/{num_segments} ({clip_dur}s)...")

        async def _clip_progress(pct, msg):
            # Map clip's 0-100 to our segment range
            mapped = pct_base + int((pct_end - pct_base) * pct / 100)
            if on_progress:
                await on_progress(mapped, f"Clip {i+1}/{num_segments}: {msg}")

        await generate_video_clip(
            image_path=ref_images[i],
            prompt=optimized_scenes[i],
            output_path=clip_path,
            duration=clip_dur,
            aspect_ratio=aspect_ratio,
            on_progress=_clip_progress,
            reference_mode=reference_mode,
        )

        clip_paths.append(clip_path)
        logger.info(f"Clip {i+1}/{num_segments} generated: {clip_path}")

    # Step 4: Concatenate clips
    if on_progress:
        await on_progress(82, "Juntando clips...")

    output_path = str(render_dir / "realistic_video.mp4")
    await concatenate_clips(clip_paths, output_path)

    logger.info(f"Multi-clip video complete: {output_path} ({total_duration}s, {num_segments} clips)")
    return output_path
