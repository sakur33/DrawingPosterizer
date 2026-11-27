"""Genera las imagenes del README (docs/*.png) a partir de una escena propia.

La escena es un bodegon de cubo, cilindro y esfera con luz y sombras suaves, renderizado aqui mismo
(sin fotos de terceros). Cada imagen muestra un metodo de la app con los mismos
ajustes que trae por defecto. Uso:  python docs/make_docs.py
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import hatching
import posterizer_core as core

W, H = 800, 600            # tamaño de cada panel
SS = 2                     # supermuestreo del render de la escena


# --------------------------------------------------------------- escena
def _rot_y(v, ang):
    """Gira los vectores `v` (..., 3) un angulo `ang` alrededor del eje Y."""
    c, s = np.cos(ang), np.sin(ang)
    x, z = v[..., 0] * c + v[..., 2] * s, -v[..., 0] * s + v[..., 2] * c
    return np.stack([x, v[..., 1], z], -1)


class Sphere:
    def __init__(self, c, r):
        self.c, self.r = np.array(c, float), r

    def hit(self, o, d):
        oc = o - self.c
        b = (oc * d).sum(-1)
        disc = b * b - ((oc * oc).sum(-1) - self.r ** 2)
        t = -b - np.sqrt(np.maximum(disc, 0))
        return np.where((disc > 0) & (t > 1e-3), t, np.inf)

    def normal(self, p):
        return (p - self.c) / self.r


class Box:
    """Caja con giro alrededor de Y (tamaño `h` = semiejes)."""

    def __init__(self, c, h, ang):
        self.c, self.h, self.ang = np.array(c, float), np.array(h, float), np.radians(ang)

    def hit(self, o, d):
        lo = _rot_y(o - self.c, self.ang)
        ld = _rot_y(d, self.ang)
        with np.errstate(divide="ignore", invalid="ignore"):
            t1, t2 = (-self.h - lo) / ld, (self.h - lo) / ld
        tn = np.nanmax(np.minimum(t1, t2), -1)
        tf = np.nanmin(np.maximum(t1, t2), -1)
        return np.where((tf > tn) & (tn > 1e-3), tn, np.inf)

    def normal(self, p):
        lp = _rot_y(p - self.c, self.ang) / self.h
        ax = np.argmax(np.abs(lp), -1)
        n = np.zeros_like(lp)
        idx = np.indices(ax.shape)
        n[(*idx, ax)] = np.sign(lp[(*idx, ax)])
        return _rot_y(n, -self.ang)


class Cylinder:
    """Cilindro vertical de radio `r` y alto `h`."""

    def __init__(self, c, r, h):
        self.c, self.r, self.h = np.array(c, float), r, h

    def hit(self, o, d):
        oc = o - self.c
        a = d[..., 0] ** 2 + d[..., 2] ** 2
        b = oc[..., 0] * d[..., 0] + oc[..., 2] * d[..., 2]
        cc = oc[..., 0] ** 2 + oc[..., 2] ** 2 - self.r ** 2
        disc = b * b - a * cc
        with np.errstate(divide="ignore", invalid="ignore"):
            ts = (-b - np.sqrt(np.maximum(disc, 0))) / a
            yh = oc[..., 1] + ts * d[..., 1]
            side = np.where((disc > 0) & (ts > 1e-3) & (np.abs(yh) <= self.h / 2), ts, np.inf)
            best = side
            for sgn in (1, -1):                                  # tapas
                tc = (sgn * self.h / 2 - oc[..., 1]) / d[..., 1]
                x, z = oc[..., 0] + tc * d[..., 0], oc[..., 2] + tc * d[..., 2]
                ok = (tc > 1e-3) & (x * x + z * z <= self.r ** 2)
                best = np.minimum(best, np.where(ok, tc, np.inf))
        return best

    def normal(self, p):
        q = p - self.c
        n = np.zeros_like(q)
        cap = np.abs(q[..., 1]) >= self.h / 2 - 1e-3
        n[..., 1] = np.where(cap, np.sign(q[..., 1]), 0)
        n[..., 0] = np.where(cap, 0, q[..., 0] / self.r)
        n[..., 2] = np.where(cap, 0, q[..., 2] / self.r)
        return n


def render_scene(w, h, seed=3):
    """Bodegon de cubo, cilindro y esfera con una luz de area arriba a la izquierda."""
    rng = np.random.default_rng(seed)
    objs = [Box((-2.0, 0.75, 0.7), (0.75, 0.75, 0.75), 28),
            Cylinder((0.2, 1.0, 0.6), 0.72, 2.0),
            Sphere((2.2, 0.8, -0.4), 0.8),
            Box((0.1, 0.25, -1.5), (0.55, 0.25, 0.4), -18)]
    o = np.array([0.0, 4.2, -8.0])
    fwd = np.array([0.0, 0.45, 0.0]) - o
    fwd /= np.linalg.norm(fwd)
    right = np.cross([0, 1, 0], fwd)
    right /= np.linalg.norm(right)
    up = np.cross(fwd, right)
    xs, ys = np.meshgrid(np.linspace(-1, 1, w), np.linspace(1, -1, h))
    sc = 0.36
    d = fwd + (xs * sc * (w / h))[..., None] * right + (ys * sc)[..., None] * up
    d /= np.linalg.norm(d, axis=-1, keepdims=True)

    t_plane = np.where(d[..., 1] < -1e-6, -o[1] / d[..., 1], np.inf)
    best, which = t_plane.copy(), np.full(t_plane.shape, -1)
    for i, ob in enumerate(objs):
        t = ob.hit(o, d)
        m = t < best
        best, which = np.where(m, t, best), np.where(m, i, which)
    hit = np.isfinite(best)
    p = o + d * np.where(hit, best, 0)[..., None]
    n = np.zeros_like(p)
    n[..., 1] = 1.0
    for i, ob in enumerate(objs):
        m = which == i
        if m.any():
            n[m] = ob.normal(p[m])
    # sombras suaves: varios rayos hacia puntos de una luz de area
    light = np.array([-0.62, 1.0, -0.55])
    light /= np.linalg.norm(light)
    lit = np.zeros(best.shape)
    k = 24
    for _ in range(k):
        jit = light + rng.normal(0, 0.05, 3)
        jit /= np.linalg.norm(jit)
        jd = np.broadcast_to(jit, p.shape)
        blocked = np.zeros(best.shape, bool)
        for ob in objs:
            blocked |= np.isfinite(ob.hit(p + n * 2e-3, jd))
        lit += ~blocked
    lit /= k
    diff = np.clip((n * light).sum(-1), 0, 1) * lit
    far = np.clip(1.25 - np.hypot(p[..., 0] + 1.2, p[..., 2] - 0.2) / 7.0, 0.22, 1.0)
    shade = np.where(which < 0, (0.06 + 0.95 * diff) * far, 0.05 + 1.0 * diff)
    shade += np.where(which >= 0, 0.06 * np.clip(-n[..., 1], 0, 1) * far, 0)   # rebote suave
    shade = np.where(hit, shade, 0.10)
    img = np.clip(shade, 0, 1) ** (1 / 2.2)
    return Image.fromarray((np.clip(img, 0, 1) * 255).astype("uint8"), "L")


# ---------------------------------------------------------------- paneles
def font(size):
    for f in ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def sheet(panels, cols, title=None):
    """Cuadricula de (etiqueta, imagen) con una etiqueta bajo cada panel."""
    pad, lab, gap = 14, 42, 10
    w, h = panels[0][1].size
    rows = (len(panels) + cols - 1) // cols
    top = 0
    h = max(im.size[1] for _, im in panels)
    mode = "RGB" if any(im.mode == "RGB" for _, im in panels) else "L"   # la guía lleva rojo
    out = Image.new(mode, (cols * w + (cols + 1) * gap, rows * (h + lab) + (rows + 1) * gap),
                    (255, 255, 255) if mode == "RGB" else 255)
    d = ImageDraw.Draw(out)
    f = font(26)
    for i, (name, im) in enumerate(panels):
        r, c = divmod(i, cols)
        x, y = gap + c * (w + gap), gap + r * (h + lab + gap)
        out.paste(im.convert(mode), (x, y))
        d.rectangle([x, y, x + w - 1, y + im.size[1] - 1], outline=190)
        d.text((x + w // 2, y + h + lab // 2), name, fill=(40,) * 3 if mode == "RGB" else 40,
               font=f, anchor="mm")
    return out


def main():
    scene = render_scene(W * SS, H * SS).resize((W, H), Image.LANCZOS)
    scene.save(os.path.join(HERE, "original.png"), optimize=True)
    gray = core.upscale_gray(scene.convert("RGB"), 1.0)

    def arrays(levels):
        blurs = core.level_blurs(levels, 2.0, 0.5)          # desenfoque progresivo (por defecto)
        return {n: core.blur_contrast(gray, blurs[n]) for n in levels}

    # Una lámina por tono, con la tira de tonos encima (opción por defecto del modo capas)
    sheets = 6
    arr = arrays([sheets + 1])[sheets + 1]
    panels = [(f"Sheet {n - 1} of {sheets}",
               core.quantize(arr, n, "capas", only_new=True, guide=sheets + 1))
              for n in range(2, sheets + 2)]
    sheet(panels, 3).save(os.path.join(HERE, "capas_laminas.png"), optimize=True)
    print("ok capas_laminas")

    # Sombreado cruzado: 8 láminas aditivas (cada una suma una capa a la anterior); se
    # muestran la 2, 4, 6 y 8. Todas parten de la misma imagen, con el desenfoque mínimo.
    total = 8
    arr = core.blur_contrast(gray, 0.5)
    panels = [(f"Sheet {n} of {total}",
               core.hatch(arr, n + 1, outlines=True, total=total + 1).render())
              for n in (2, 4, 6, 8)]
    sheet(panels, 2).save(os.path.join(HERE, "trazos.png"), optimize=True)
    print("ok trazos")

    jobs = {
        "capas": ([2, 4, 6, 8], lambda a, n: core.quantize(a, n, "capas")),
        "cuantiles": ([3, 4, 5, 6], lambda a, n: core.quantize(a, n, "cuantiles")),
        "lineal": ([3, 4, 5, 6], lambda a, n: core.quantize(a, n, "lineal")),
    }
    for name, (levels, fn) in jobs.items():
        arrs = arrays(levels)
        panels = [(f"{n} levels", fn(arrs[n], n)) for n in levels]
        sheet(panels, 2).save(os.path.join(HERE, f"{name}.png"), optimize=True)
        print("ok", name)


if __name__ == "__main__":
    main()
