from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = ROOT / "static" / "icons"


def blend_hex(start: str, end: str, ratio: float) -> tuple[int, int, int, int]:
    start = start.lstrip("#")
    end = end.lstrip("#")
    start_rgb = tuple(int(start[index:index + 2], 16) for index in range(0, 6, 2))
    end_rgb = tuple(int(end[index:index + 2], 16) for index in range(0, 6, 2))
    mixed = tuple(int(start_rgb[i] + (end_rgb[i] - start_rgb[i]) * ratio) for i in range(3))
    return mixed + (255,)


def make_icon(size: int) -> Image.Image:
    background = "#081A2F"
    glow = "#17395F"
    gold_start = "#FFD45E"
    gold_end = "#F6A52F"
    highlight = "#FFE189"

    image = Image.new("RGBA", (size, size), background)

    glow_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    glow_draw.ellipse(
        (
            int(size * 0.07),
            int(size * 0.02),
            int(size * 0.92),
            int(size * 0.82),
        ),
        fill=glow,
    )
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=max(6, size // 18)))
    image.alpha_composite(glow_layer)

    ring_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ring_draw = ImageDraw.Draw(ring_layer)
    ring_box = (
        int(size * 0.25),
        int(size * 0.25),
        int(size * 0.75),
        int(size * 0.75),
    )
    ring_width = max(8, size // 18)
    ring_steps = max(24, size // 10)
    for step in range(ring_steps):
        ratio = step / max(1, ring_steps - 1)
        color = blend_hex(gold_start, gold_end, ratio)
        inset = int(step * (ring_width / ring_steps))
        ring_draw.ellipse(
            (
                ring_box[0] + inset,
                ring_box[1] + inset,
                ring_box[2] - inset,
                ring_box[3] - inset,
            ),
            outline=color,
            width=max(1, ring_width // 3),
        )
    ring_layer = ring_layer.filter(ImageFilter.GaussianBlur(radius=max(1, size // 256)))
    image.alpha_composite(ring_layer)

    play_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    play_draw = ImageDraw.Draw(play_layer)
    play_points = [
        (int(size * 0.455), int(size * 0.40)),
        (int(size * 0.655), int(size * 0.50)),
        (int(size * 0.455), int(size * 0.60)),
    ]
    play_draw.polygon(play_points, fill=blend_hex(highlight, gold_end, 0.35))
    image.alpha_composite(play_layer)

    dot_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    dot_draw = ImageDraw.Draw(dot_layer)
    dot_radius = max(5, size // 26)
    dot_center = (int(size * 0.684), int(size * 0.344))
    dot_draw.ellipse(
        (
            dot_center[0] - dot_radius,
            dot_center[1] - dot_radius,
            dot_center[0] + dot_radius,
            dot_center[1] + dot_radius,
        ),
        fill=(255, 225, 137, 235),
    )
    dot_layer = dot_layer.filter(ImageFilter.GaussianBlur(radius=max(1, size // 120)))
    image.alpha_composite(dot_layer)

    return image


def main() -> None:
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    icon_512 = make_icon(512)
    icon_192 = make_icon(192)
    favicon_32 = make_icon(32)
    upload_120 = make_icon(120)

    icon_512.save(ICONS_DIR / "icon-512.png")
    icon_192.save(ICONS_DIR / "icon-192.png")
    favicon_32.save(ICONS_DIR / "favicon-32.png")
    upload_120.save(ICONS_DIR / "logo-upload-120.png")
    favicon_32.save(ICONS_DIR / "favicon.ico", sizes=[(32, 32), (16, 16)])

    print("Generated icons:")
    for name in ["icon-512.png", "icon-192.png", "favicon-32.png", "favicon.ico", "logo-upload-120.png"]:
        print(ICONS_DIR / name)


if __name__ == "__main__":
    main()