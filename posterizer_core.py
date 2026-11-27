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
from PIL import Image, ImageDraw, ImageFilter, ImageOps

import hatching

try:  # HEIC/HEIF (fotos de iPhone). Opcional.
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

MODES = {"cuantiles": "c", "lineal": "l", "capas": "s", "trazos": "h"}

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


def adaptive_thresholds(arr, count=NESTED_MAX_LEVELS - 1):
    """Umbrales (de claro a oscuro) repartidos según DÓNDE HAY DETALLE: cada tono de la
    imagen pesa por el contraste local que tiene (gradiente), así que se agrupan donde
    la foto tiene textura (p. ej. el pelo en una foto oscura) y no en zonas lisas.
    Son fijos para la imagen: no dependen del nº de niveles, así que siguen anidados."""
    gy, gx = np.gradient(arr.astype(np.float32))
    mag = np.hypot(gx, gy).ravel()
    bins = np.clip(arr.ravel().astype(int), 0, 255)
    mass = np.bincount(bins, weights=mag, minlength=256) + 1e-6
    cum = np.cumsum(mass[::-1])                 # índice 0 = tono 255
    cum /= cum[-1]
    out, last = [], 256
    for i in range(count):
        t = 255 - int(np.searchsorted(cum, (i + 1) / (count + 1)))
        t = min(t, last - 4)                    # estrictamente decreciente
        out.append(max(t, 1))
        last = out[-1]
    return out


def nested_cuts(arr, levels, adaptive=False):
    """Umbrales de gris de las `levels`-1 sombras aditivas del modo capas (fijos, o
    adaptados al detalle de la foto). Los usa también el sombreado cruzado."""
    thr = adaptive_thresholds(arr) if adaptive else NESTED_THRESHOLDS
    return thr[:levels - 1]


def hatch(arr, levels, outlines=False, adaptive=False, total=None):
    """Sombreado cruzado (HatchImage) con las zonas de las sombras aditivas de 'capas'.
    `total` = niveles de la tirada: las láminas de una misma tirada son aditivas."""
    return hatching.hatch(arr, levels, nested_cuts(arr, levels, adaptive), outlines, total)


def quantize_nested(arr, levels, only_new=False, adaptive=False):
    """Array en gris -> imagen PIL 'L' con sombras anidadas (modo 'capas').
    Con `only_new` solo se dibuja la sombra añadida en este nivel (la zona más oscura
    que la del nivel anterior), con su mismo tono: así se ve qué cambia al subir."""
    if not 2 <= levels <= NESTED_MAX_LEVELS:
        raise ValueError(f"El modo capas admite de 2 a {NESTED_MAX_LEVELS} niveles.")
    out = np.full(arr.shape, 255, dtype=np.uint8)
    thr = adaptive_thresholds(arr) if adaptive else NESTED_THRESHOLDS
    steps = list(zip(thr[:levels - 1], NESTED_TONES))
    if only_new:
        steps = steps[-1:]
    for t, tone in steps:
        out[arr < t] = tone
    return Image.fromarray(out, mode="L")


def add_tone_guide(img, level, total):
    """Añade encima de `img` una tira con el rango completo de tonos del modo 'capas'
    (papel + `total`-1 sombras) y marca en rojo el que se pinta en el nivel `level`.
    Devuelve una imagen RGB más alta que la original."""
    tones = nested_tones(total)
    w, h = img.size
    bar = max(28, round(w * 0.14))
    out = Image.new("RGB", (w, h + bar), (255, 255, 255))
    out.paste(img.convert("RGB"), (0, bar))
    d = ImageDraw.Draw(out)
    pad = max(2, bar // 9)
    cw = (w - 2 * pad) / len(tones)
    top, bot = pad, bar - 2 * pad - pad
    for k, t in enumerate(tones):
        x0 = pad + k * cw
        box = [x0 + pad // 2, top, x0 + cw - pad, bot]
        d.rectangle(box, fill=(t, t, t), outline=(120, 120, 120))
        if k == level - 1:
            d.rectangle([box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad],
                        outline=(230, 40, 40), width=max(2, pad))
            cx = (box[0] + box[2]) / 2
            d.polygon([(cx - pad * 1.3, bot + pad * 1.6), (cx + pad * 1.3, bot + pad * 1.6),
                       (cx, bar - 1)], fill=(230, 40, 40))
    return out


def quantize(arr, levels, mode="cuantiles", outlines=False, only_new=False, adaptive=False,
             guide=None):
    """Array en gris -> imagen PIL 'L' con exactamente `levels` tonos.
    Modo 'capas' con `only_new` y `guide` (nº total de niveles): la imagen sale en RGB con
    una tira de tonos encima que marca el que toca pintar. En 'trazos', `guide` es el total
    de la tirada, para que sus láminas sean aditivas."""
    if levels < 2:
        raise ValueError("El número de niveles debe ser al menos 2.")
    if mode == "capas":
        img = quantize_nested(arr, levels, only_new, adaptive)
        if only_new and guide:
            img = add_tone_guide(img, levels, max(guide, levels))
        return img
    if mode == "trazos":
        return hatch(arr, levels, outlines, adaptive, guide).render()
    if mode == "cuantiles":
        pct = np.linspace(0, 100, levels + 1)[1:-1]
        thresholds = np.percentile(arr, pct)
    else:
        thresholds = np.linspace(0, 255, levels + 1)[1:-1]
    values = np.linspace(0, 255, levels).round().astype(np.uint8)
    out = values[np.digitize(arr, bins=thresholds)]
    return Image.fromarray(out, mode="L")


def output_name(base, levels, blur, mode, upscale, ext, only_new=False):
    """Mismo esquema que el script original, más el factor de ampliación."""
    name = f"{base}_n{levels:02d}_b{blur:.1f}_m{MODES[mode]}"
    if only_new and mode == "capas":
        name += "_nuevo"
    if upscale > 1:
        name += f"_x{upscale:g}"
    return name + "." + ext.lstrip(".")


def save_image(img, path, fmt, dpi=None, size_mm=None):
    """Guarda la imagen. Con `dpi` queda registrada la medida física al imprimir.
    Un HatchImage (sombreado cruzado) también puede guardarse como SVG vectorial."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if isinstance(img, hatching.HatchImage):
        if fmt.lower() == "svg":
            with open(path, "w", encoding="utf-8") as f:
                f.write(img.to_svg(size_mm))
            return
        img = img.render()
    extra = {"dpi": (dpi, dpi)} if dpi else {}
    if fmt.lower() in ("jpg", "jpeg"):
        img.save(path, quality=95, **extra)
    else:
        img.save(path, **extra)
