"""
Lógica de posterizado (sin interfaz).

Pipeline para cada exportación:
  recorte -> escala de grises -> ampliación (Lanczos) -> desenfoque
  -> autocontraste -> cuantización a N niveles

La ampliación se hace ANTES de cuantizar a propósito: así los bordes entre
masas tonales salen suaves y limpios en vez de escalonados.
"""
import os

import numpy as np
from PIL import Image, ImageFilter, ImageOps

try:  # HEIC/HEIF (fotos de iPhone). Opcional.
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

MODES = {"cuantiles": "c", "lineal": "l", "capas": "s"}

# Modo "capas" (sombras anidadas). Blanco = papel vacío; solo se dibujan
# sombras. Cada profundidad tiene un umbral y un tono FIJOS, así que subir de
# nivel nunca cambia lo ya existente: solo añade una sombra más oscura dentro
# de la anterior. Con n niveles hay n-1 sombras.
NESTED_MAX_LEVELS = 10
NESTED_THRESHOLDS = [190 - 20 * i for i in range(NESTED_MAX_LEVELS - 1)]
NESTED_TONES = [222 - 26 * i for i in range(NESTED_MAX_LEVELS - 1)]
MAX_SIDE = 12000  # límite de seguridad para el lado mayor tras ampliar


def load_image(path):
    """Abre una imagen respetando la orientación EXIF (fotos de móvil)."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def clamp_factor(size, factor):
    """Reduce el factor de ampliación si el resultado superaría MAX_SIDE."""
    longest = max(size)
    if longest * factor > MAX_SIDE:
        return max(1.0, MAX_SIDE / longest)
    return factor


def upscale_gray(img, upscale=1.0):
    """Imagen -> PIL 'L' ampliada con Lanczos (sin desenfoque ni contraste)."""
    gray = img.convert("L")
    upscale = clamp_factor(gray.size, upscale)
    if upscale > 1.0:
        new = (round(gray.width * upscale), round(gray.height * upscale))
        gray = gray.resize(new, Image.LANCZOS)
    return gray


def blur_contrast(gray, radius):
    """PIL 'L' -> array float32 desenfocado y con autocontraste."""
    if radius > 0:
        gray = gray.filter(ImageFilter.GaussianBlur(radius=radius))
    gray = ImageOps.autocontrast(gray, cutoff=1)
    return np.array(gray, dtype=np.float32)


def level_blurs(levels, blur_max, blur_min=None):
    """Desenfoque por nivel. Sin `blur_min` todos usan `blur_max`.
    Progresivo: menos niveles -> más desenfoque (`blur_max`); más niveles ->
    menos (`blur_min`), repartido linealmente por posición entre los niveles
    elegidos."""
    levels = sorted(levels)
    if blur_min is None or len(levels) < 2:
        return {n: blur_max for n in levels}
    k = len(levels) - 1
    return {n: blur_max + (blur_min - blur_max) * i / k
            for i, n in enumerate(levels)}


def prepare_gray(img, blur=2.0, upscale=1.0):
    """Imagen -> array float32 en gris, ampliado, suavizado y con autocontraste.

    `blur` está expresado en píxeles de la imagen ORIGINAL (recortada); se
    multiplica por el factor de ampliación para que el aspecto sea el mismo.
    """
    gray = upscale_gray(img, upscale)
    return blur_contrast(gray, blur * clamp_factor(img.size, upscale))


def nested_tones(levels):
    """Tonos (del papel a la sombra más oscura) del modo 'capas'."""
    return [255] + NESTED_TONES[:levels - 1]


def quantize_nested(arr, levels):
    """Array en gris -> imagen PIL 'L' con sombras anidadas (modo 'capas')."""
    if not 2 <= levels <= NESTED_MAX_LEVELS:
        raise ValueError(f"El modo capas admite de 2 a {NESTED_MAX_LEVELS} niveles.")
    out = np.full(arr.shape, 255, dtype=np.uint8)
    for t, tone in zip(NESTED_THRESHOLDS[:levels - 1], NESTED_TONES):
        out[arr < t] = tone
    return Image.fromarray(out, mode="L")


def quantize(arr, levels, mode="cuantiles"):
    """Array en gris -> imagen PIL 'L' con exactamente `levels` tonos."""
    if levels < 2:
        raise ValueError("El número de niveles debe ser al menos 2.")
    if mode == "capas":
        return quantize_nested(arr, levels)
    if mode == "cuantiles":
        pct = np.linspace(0, 100, levels + 1)[1:-1]
        thresholds = np.percentile(arr, pct)
    else:
        thresholds = np.linspace(0, 255, levels + 1)[1:-1]
    values = np.linspace(0, 255, levels).round().astype(np.uint8)
    out = values[np.digitize(arr, bins=thresholds)]
    return Image.fromarray(out, mode="L")


def output_name(base, levels, blur, mode, upscale, ext):
    """Mismo esquema que el script original, más el factor de ampliación."""
    name = f"{base}_n{levels:02d}_b{blur:.1f}_m{MODES[mode]}"
    if upscale > 1:
        name += f"_x{upscale:g}"
    return name + "." + ext.lstrip(".")


def save_image(img, path, fmt, dpi=None):
    """Guarda la imagen. Con `dpi` queda registrada la medida física al imprimir."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    extra = {"dpi": (dpi, dpi)} if dpi else {}
    if fmt.lower() in ("jpg", "jpeg"):
        img.save(path, quality=95, **extra)
    else:
        img.save(path, **extra)
