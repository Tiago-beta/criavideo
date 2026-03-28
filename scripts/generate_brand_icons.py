"""
Generate CriaVideo brand icons — gold ring + play symbol on dark background.
Renders at 4x supersampling then downscales for clean anti-aliasing.
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = ROOT / "static" / "icons"
EXPORT_DIR = ROOT.parent / "CriaVideo-icones"

SCALE = 4  # supersampling factor


def hex_to_rgba(h: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


def make_icon(target_size: int) -> Image.Image:
    """Render icon at SCALE× then downscale for crisp result."""
    s = target_size * SCALE
    cx, cy = s / 2, s / 2

    bg_color = hex_to_rgba("#081A2F")
    ring_color = hex_to_rgba("#F6A52F")
    ring_highlight = hex_to_rgba("#FFCD57")
    play_color = hex_to_rgba("#FFBF40")
    dot_color = hex_to_rgba("#FFE08C", 220)
    glow_color = hex_to_rgba("#1A3D64")

    img = Image.new("RGBA", (s, s), bg_color)

    # --- Centered radial glow ---
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    glow_r = s * 0.38
    gd.ellipse(
        (cx - glow_r, cy - glow_r, cx + glow_r, cy + glow_r),
        fill=glow_color,
    )
    glow = glow.filter(ImageFilter.GaussianBlur(radius=s * 0.18))
    img.alpha_composite(glow)

    # --- Gold ring (outer circle - inner circle) ---
    ring_layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    rd = ImageDraw.Draw(ring_layer)
    ring_outer_r = s * 0.36
    ring_width = s * 0.055
    ring_inner_r = ring_outer_r - ring_width

    # Outer edge (highlight)
    rd.ellipse(
        (cx - ring_outer_r, cy - ring_outer_r, cx + ring_outer_r, cy + ring_outer_r),
        fill=ring_highlight,
    )
    # Inner gold
    rd.ellipse(
        (cx - ring_outer_r + ring_width * 0.3,
         cy - ring_outer_r + ring_width * 0.3,
         cx + ring_outer_r - ring_width * 0.3,
         cy + ring_outer_r - ring_width * 0.3),
        fill=ring_color,
    )
    # Punch out center
    rd.ellipse(
        (cx - ring_inner_r, cy - ring_inner_r, cx + ring_inner_r, cy + ring_inner_r),
        fill=(0, 0, 0, 0),
    )
    img.alpha_composite(ring_layer)

    # --- Play triangle (centered with optical offset) ---
    play_layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    pd = ImageDraw.Draw(play_layer)
    tri_h = s * 0.22
    tri_w = tri_h * 0.9
    # Shift right slightly for optical centering of a triangle
    offset_x = tri_w * 0.12
    tri_cx = cx + offset_x
    tri_cy = cy
    play_points = [
        (tri_cx - tri_w * 0.42, tri_cy - tri_h / 2),
        (tri_cx + tri_w * 0.58, tri_cy),
        (tri_cx - tri_w * 0.42, tri_cy + tri_h / 2),
    ]
    pd.polygon(play_points, fill=play_color)
    img.alpha_composite(play_layer)

    # --- Small accent dot (top-right of ring) ---
    dot_layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dd = ImageDraw.Draw(dot_layer)
    dot_angle = math.radians(-40)
    dot_dist = ring_outer_r - ring_width / 2
    dot_x = cx + dot_dist * math.cos(dot_angle)
    dot_y = cy + dot_dist * math.sin(dot_angle)
    dot_r = s * 0.028
    dd.ellipse(
        (dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r),
        fill=dot_color,
    )
    dot_layer = dot_layer.filter(ImageFilter.GaussianBlur(radius=max(1, s * 0.004)))
    img.alpha_composite(dot_layer)

    # --- Downscale with high-quality resampling ---
    return img.resize((target_size, target_size), Image.LANCZOS)


def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    sizes = {
        "icon-512.png": 512,
        "icon-192.png": 192,
        "favicon-32.png": 32,
        "logo-upload-120.png": 120,
    }

    icons = {}
    for name, sz in sizes.items():
        icons[name] = make_icon(sz)
        icons[name].save(ICONS_DIR / name)
        icons[name].save(EXPORT_DIR / name)

    # Favicon ICO (multi-size)
    icons["favicon-32.png"].save(
        ICONS_DIR / "favicon.ico", sizes=[(32, 32), (16, 16)]
    )
    icons["favicon-32.png"].save(
        EXPORT_DIR / "favicon.ico", sizes=[(32, 32), (16, 16)]
    )

    print("Generated icons in:")
    print(f"  {ICONS_DIR}")
    print(f"  {EXPORT_DIR}")
    for name in sizes:
        print(f"  - {name}")
    print("  - favicon.ico")


if __name__ == "__main__":
    main()