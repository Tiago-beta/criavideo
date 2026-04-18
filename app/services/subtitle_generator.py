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
Style: Karaoke,{font_name},{font_size},{primary_color},{secondary_color},{outline_color},{back_color},{bold},{italic},0,0,100,100,0,0,{border_style},{outline_width},{shadow},2,40,40,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _css_color_to_ass(color: str | None, default_hex: str, default_alpha: int = 0) -> str:
    """Convert CSS-like color formats (#RRGGBB / rgba) to ASS format (&HAABBGGRR)."""
    if not color:
        color = default_hex

    value = str(color).strip()
    alpha = int(_clamp(default_alpha, 0, 255))

    # #RGB / #RRGGBB / #RRGGBBAA
    if value.startswith("#"):
        raw = value[1:]
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        if len(raw) == 6:
            try:
                r = int(raw[0:2], 16)
                g = int(raw[2:4], 16)
                b = int(raw[4:6], 16)
                return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"
            except ValueError:
                pass
        if len(raw) == 8:
            try:
                r = int(raw[0:2], 16)
                g = int(raw[2:4], 16)
                b = int(raw[4:6], 16)
                css_alpha = int(raw[6:8], 16) / 255.0
                ass_alpha = int(round((1.0 - css_alpha) * 255))
                ass_alpha = int(_clamp(ass_alpha, 0, 255))
                return f"&H{ass_alpha:02X}{b:02X}{g:02X}{r:02X}"
            except ValueError:
                pass

    # rgb(...) / rgba(...)
    m = re.match(r"rgba?\(([^)]+)\)", value, flags=re.IGNORECASE)
    if m:
        parts = [p.strip() for p in m.group(1).split(",")]
        if len(parts) >= 3:
            try:
                r = int(float(parts[0]))
                g = int(float(parts[1]))
                b = int(float(parts[2]))
                r = int(_clamp(r, 0, 255))
                g = int(_clamp(g, 0, 255))
                b = int(_clamp(b, 0, 255))

                if len(parts) >= 4:
                    a_val = parts[3]
                    if a_val.endswith("%"):
                        css_alpha = float(a_val[:-1]) / 100.0
                    else:
                        css_alpha = float(a_val)
                    css_alpha = _clamp(css_alpha, 0.0, 1.0)
                    alpha = int(round((1.0 - css_alpha) * 255))
                    alpha = int(_clamp(alpha, 0, 255))

                return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"
            except ValueError:
                pass

    # Fallback
    return _css_color_to_ass(default_hex, default_hex, default_alpha)


def _normalize_ass_style(aspect_ratio: str, style_settings: dict | None = None) -> dict:
    if aspect_ratio == "9:16":
        play_res_y = 1920
        font_size_default = 50
        y_default = 82
    else:
        play_res_y = 1080
        font_size_default = 60
        y_default = 88

    cfg = style_settings if isinstance(style_settings, dict) else {}

    y_percent = float(cfg.get("y", y_default))
    y_percent = _clamp(y_percent, 5, 95)
    margin_v = int(round(((100.0 - y_percent) / 100.0) * play_res_y))
    margin_v = int(_clamp(margin_v, 20, play_res_y - 20))

    font_size = int(_clamp(float(cfg.get("font_size", font_size_default)), 14, 160))

    font_family = str(cfg.get("font_family") or "Arial").strip()
    font_name = font_family.split(",")[0].strip().strip('"').strip("'") or "Arial"

    primary_color = _css_color_to_ass(cfg.get("font_color"), "#FFFF00", 0)
    secondary_color = _css_color_to_ass("#FFFFFF", "#FFFFFF", 0)
    outline_color = _css_color_to_ass(cfg.get("outline_color"), "#000000", 0)

    bg_raw = cfg.get("bg_color")
    has_bg = bool(str(bg_raw or "").strip())
    if has_bg:
        back_color = _css_color_to_ass(str(bg_raw), "#000000", 128)
        border_style = 3
        outline_width = 1
    else:
        back_color = _css_color_to_ass("rgba(0,0,0,0.5)", "#000000", 128)
        border_style = 1
        outline_width = int(_clamp(float(3), 0, 8))

    bold = -1 if bool(cfg.get("bold", True)) else 0
    italic = -1 if bool(cfg.get("italic", False)) else 0

    return {
        "play_res_y": play_res_y,
        "font_name": font_name,
        "font_size": font_size,
        "margin_v": margin_v,
        "primary_color": primary_color,
        "secondary_color": secondary_color,
        "outline_color": outline_color,
        "back_color": back_color,
        "bold": bold,
        "italic": italic,
        "border_style": border_style,
        "outline_width": outline_width,
        "shadow": 1,
    }


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
        text = w["word"].strip().upper()
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
    narration_mode: bool = False,
    style_settings: dict | None = None,
) -> str:
    """Generate a complete ASS subtitle file with karaoke highlighting.
    Always shows 2 lines: the current line with karaoke effect
    and the next line in dim color so the user can read ahead.

    If narration_mode=True, shows only the current line (no next-line preview)
    and displays the full line at once instead of word-by-word karaoke.

    lyrics_words: list of {"word": str, "start": float, "end": float}
    """
    style_cfg = _normalize_ass_style(aspect_ratio, style_settings)
    header = ASS_HEADER.format(**style_cfg)

    lines = group_words_into_lines(lyrics_words)
    events = []

    # Dim color for the "next line" preview (light gray)
    next_line_color = r"{\1c&H00AAAAAA&\2c&H00AAAAAA&}"

    for i, line_words in enumerate(lines):
        if not line_words:
            continue
        start = max(0.0, line_words[0]["start"] - 0.3)  # appear slightly early
        end = line_words[-1]["end"] + 0.5  # buffer after last word

        # Clip end so it doesn't overlap with next line's start (avoids duplicate text)
        if i + 1 < len(lines) and lines[i + 1]:
            next_start = max(0.0, lines[i + 1][0]["start"] - 0.3)
            end = min(end, next_start)

        if narration_mode:
            # Narration: show full line at once, yellow highlighted, no word-by-word karaoke
            line_text = " ".join(w["word"].strip().upper() for w in line_words if w.get("word", "").strip())
            karaoke_text = line_text
        else:
            karaoke_text = _build_karaoke_line(line_words)

        # Add next line preview if available (only for karaoke/music mode)
        if not narration_mode and i + 1 < len(lines):
            next_words = lines[i + 1]
            next_text = " ".join(w["word"].strip().upper() for w in next_words if w.get("word", "").strip())
            if next_text:
                karaoke_text += r"\N" + next_line_color + next_text

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
    narration_mode: bool = False,
    style_settings: dict | None = None,
) -> str:
    """Generate ASS subtitles from plain text lyrics (no word timestamps).
    Distributes lines evenly across the song duration with highlight effect.
    Always shows 2 lines: current highlighted + next line preview.
    If narration_mode=True, shows only the current spoken line.
    """
    style_cfg = _normalize_ass_style(aspect_ratio, style_settings)
    header = ASS_HEADER.format(**style_cfg)

    # Strip structural markers like [Refrão], [Verso 1], [Ponte], etc.
    cleaned = re.sub(r'\[.*?\]', '', lyrics_text)
    # Split lyrics into non-empty lines
    raw_lines = [l.strip() for l in cleaned.strip().split("\n") if l.strip()]
    if not raw_lines:
        return ""

    # Dim color for the "next line" preview
    next_line_color = r"{\1c&H00AAAAAA&\2c&H00AAAAAA&}"

    # Distribute lines evenly across duration (with small gaps)
    time_per_line = duration / len(raw_lines)
    events = []

    for i, line in enumerate(raw_lines):
        start = i * time_per_line
        # End exactly when next line starts (no overlap)
        end = (i + 1) * time_per_line if i + 1 < len(raw_lines) else start + time_per_line - 0.1

        if narration_mode:
            # Narration: show full line at once, no word-by-word karaoke
            karaoke_parts = line.upper()
        else:
            # Karaoke-style: whole line highlights word by word
            words = line.upper().split()
            if words:
                word_dur_cs = max(1, int((time_per_line / len(words)) * 100))
                karaoke_parts = " ".join(f"{{\\k{word_dur_cs}}}{w}" for w in words)
            else:
                karaoke_parts = line.upper()

        # Add next line preview if available (only for karaoke/music mode)
        if not narration_mode and i + 1 < len(raw_lines):
            next_text = raw_lines[i + 1].upper()
            if next_text:
                karaoke_parts += r"\N" + next_line_color + next_text

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
