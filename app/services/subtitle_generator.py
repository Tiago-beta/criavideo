"""
Subtitle Generator — Creates ASS (Advanced SubStation Alpha) karaoke subtitles
from word-level timestamps (Whisper output stored in Levita's tracks table).
"""
import re
import logging

logger = logging.getLogger(__name__)

ASS_HEADER = r"""[Script Info]
Title: CriaVideo Karaoke
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: None
PlayResX: 1920
PlayResY: {play_res_y}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial,{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _format_ass_time(seconds: float) -> str:
    """Convert seconds to ASS time format H:MM:SS.cc"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _build_karaoke_line(words: list[dict]) -> str:
    """Build a karaoke line with \\k tags for word-by-word highlighting.
    Each word dict: {"word": "text", "start": 1.23, "end": 1.56}
    """
    parts = []
    for w in words:
        duration_cs = max(1, int((w["end"] - w["start"]) * 100))
        text = w["word"].strip()
        if text:
            parts.append(f"{{\\k{duration_cs}}}{text}")
    return " ".join(parts) if parts else ""


def group_words_into_lines(words: list[dict], max_words_per_line: int = 8, max_gap: float = 1.5) -> list[list[dict]]:
    """Group words into subtitle lines based on word count and timing gaps."""
    if not words:
        return []

    lines = []
    current_line = []

    for w in words:
        word_text = w.get("word", "").strip()
        if not word_text:
            continue

        if current_line and (
            len(current_line) >= max_words_per_line
            or (w["start"] - current_line[-1]["end"]) > max_gap
        ):
            lines.append(current_line)
            current_line = []

        current_line.append(w)

    if current_line:
        lines.append(current_line)

    return lines


def generate_ass_subtitles(
    lyrics_words: list[dict],
    aspect_ratio: str = "16:9",
    output_path: str = "karaoke.ass",
) -> str:
    """Generate a complete ASS subtitle file with karaoke highlighting.

    lyrics_words: list of {"word": str, "start": float, "end": float}
    """
    if aspect_ratio == "9:16":
        play_res_y = 1920
        font_size = 50
        margin_v = 200
    else:
        play_res_y = 1080
        font_size = 60
        margin_v = 80

    header = ASS_HEADER.format(
        play_res_y=play_res_y,
        font_size=font_size,
        margin_v=margin_v,
    )

    lines = group_words_into_lines(lyrics_words)
    events = []

    for line_words in lines:
        if not line_words:
            continue
        start = line_words[0]["start"]
        end = line_words[-1]["end"] + 0.3  # small buffer after last word
        karaoke_text = _build_karaoke_line(line_words)
        start_str = _format_ass_time(start)
        end_str = _format_ass_time(end)
        events.append(
            f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{karaoke_text}"
        )

    ass_content = header + "\n".join(events) + "\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    logger.info(f"ASS subtitles saved: {output_path} ({len(events)} lines)")
    return output_path


def generate_ass_from_text(
    lyrics_text: str,
    duration: float,
    aspect_ratio: str = "16:9",
    output_path: str = "karaoke.ass",
) -> str:
    """Generate ASS subtitles from plain text lyrics (no word timestamps).
    Distributes lines evenly across the song duration with highlight effect.
    """
    if aspect_ratio == "9:16":
        play_res_y = 1920
        font_size = 50
        margin_v = 200
    else:
        play_res_y = 1080
        font_size = 60
        margin_v = 80

    header = ASS_HEADER.format(
        play_res_y=play_res_y,
        font_size=font_size,
        margin_v=margin_v,
    )

    # Strip structural markers like [Refrão], [Verso 1], [Ponte], etc.
    cleaned = re.sub(r'\[.*?\]', '', lyrics_text)
    # Split lyrics into non-empty lines
    raw_lines = [l.strip() for l in cleaned.strip().split("\n") if l.strip()]
    if not raw_lines:
        return ""

    # Distribute lines evenly across duration (with small gaps)
    time_per_line = duration / len(raw_lines)
    events = []

    for i, line in enumerate(raw_lines):
        start = i * time_per_line
        end = start + time_per_line - 0.1
        # Karaoke-style: whole line highlights word by word
        words = line.split()
        if words:
            word_dur_cs = max(1, int((time_per_line / len(words)) * 100))
            karaoke_parts = " ".join(f"{{\\k{word_dur_cs}}}{w}" for w in words)
        else:
            karaoke_parts = line
        start_str = _format_ass_time(start)
        end_str = _format_ass_time(end)
        events.append(
            f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{karaoke_parts}"
        )

    ass_content = header + "\n".join(events) + "\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(ass_content)

    logger.info(f"ASS text subtitles saved: {output_path} ({len(events)} lines)")
    return output_path
