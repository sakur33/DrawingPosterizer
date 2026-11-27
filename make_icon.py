"""Genera assets/Posterizer.ico (Windows) y assets/Posterizer.icns (macOS) con Pillow.
Icono: un degradado de grises en 4 bandas tonales sobre fondo redondeado."""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ICO = os.path.join(HERE, "assets", "Posterizer.ico")
ICNS = os.path.join(HERE, "assets", "Posterizer.icns")
SIZES = [16, 24, 32, 48, 64, 128, 256]
TONES = [30, 95, 165, 235]


def render(size):
    scale = 4  # supermuestreo para bordes suaves
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([s * .04, s * .04, s * .96, s * .96],
                                           radius=int(s * .2), fill=255)
    bands = Image.new("RGBA", (s, s))
    d = ImageDraw.Draw(bands)
    bw = s / len(TONES)
    for i, t in enumerate(TONES):
        d.rectangle([i * bw, 0, (i + 1) * bw, s], fill=(t, t, t, 255))
    img.paste(bands, (0, 0), mask)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(ICO), exist_ok=True)
    render(256).save(ICO, format="ICO", sizes=[(n, n) for n in SIZES])
    print(f"[make_icon] Icono escrito en: {ICO}")
    render(1024).save(ICNS, format="ICNS")
    print(f"[make_icon] Icono escrito en: {ICNS}")
