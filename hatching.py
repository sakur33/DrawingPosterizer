"""
Sombreado cruzado (crosshatching) en capas anidadas. Sin interfaz.

Cada nivel nuevo añade UNA capa de trazos, en otra dirección, dentro de la zona
más oscura que la anterior; los trazos de las capas previas no cambian. Las
zonas se calculan por cuantiles de la propia foto (cada capa cubre un % fijo de
la imagen), así que son las mismas en 2, 3, 4… niveles.

Los trazos se generan como GEOMETRÍA (polilíneas), no como píxeles, de modo que
la misma geometría sirve para:
Opcionalmente cada capa añade también el CONTORNO de su zona (el borde entre la sombra
y la luz), que da la silueta de los rasgos: ojos, nariz, boca, cabeza.

  - HatchImage.render()  -> imagen PIL en gris a cualquier resolución
  - HatchImage.to_svg()  -> SVG vectorial
  - el PDF/impresión (se dibujan las polilíneas con QPainter, ver app.py)
"""
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HATCH_MAX_LEVELS = 10                 # n niveles = n-1 capas de trazos
# Una entrada por capa. Cada capa tiene su zona, dirección y separación FIJAS, así que
# al subir de nivel solo se añade una capa nueva y las anteriores no cambian. Cuanto más
# profunda la capa, más juntas van sus líneas (y algo más finas) para que cada nivel nuevo
# oscurezca de verdad.
COVER = [78, 60, 44, 31, 22, 15, 10, 6, 3]           # % más oscuro de la imagen que cubre
ANGLES = [45, 135, 12, 102, 28, 118, 75, 160, 58]    # dirección de los trazos (grados)
SPACING = [16, 15, 14, 13, 12, 10.5, 9, 8, 7]        # separación entre trazos (px de análisis)
THICKNESS = [2.0, 2.0, 1.9, 1.8, 1.7, 1.5, 1.4, 1.3, 1.2]   # grosor (px de análisis)
WOBBLE = 2.2                          # cuánto serpentea el trazo (px de análisis)
INK = 30                              # tono de la tinta (0 = negro)
ANALYSIS_SIDE = 1080                  # lado mayor de referencia: todas las medidas de arriba
                                      # están dadas para este tamaño y se escalan con él
STEP = 4.0                            # paso de muestreo a lo largo del trazo
MIN_LEN = 12.0                        # trazos más cortos se descartan
CLEAN = 9                             # tamaño del filtro que limpia las zonas
OUTLINE_THICK = 2.6                   # grosor del contorno (px de análisis)
OUTLINE_SMOOTH = 2.0                  # suavizado de la zona antes de sacar el contorno
OUTLINE_MIN_LEN = 40.0                # contornos más cortos se descartan (px de análisis)


def _noise_field(seed, scale, aw, ah):
    """Campo aleatorio suave en [-1, 1], definido en coordenadas continuas (sin
    saltos entre celdas: interpolación bilineal con suavizado)."""
    rng = np.random.default_rng(seed)
    gw, gh = int(aw // scale) + 3, int(ah // scale) + 3
    g = rng.standard_normal((gh, gw)).astype(np.float32)
    g /= np.abs(g).max()

    def at(x, y):
        fx = np.clip(x / scale, 0, gw - 1.001)
        fy = np.clip(y / scale, 0, gh - 1.001)
        x0, y0 = fx.astype(int), fy.astype(int)
        tx, ty = fx - x0, fy - y0
        tx, ty = tx * tx * (3 - 2 * tx), ty * ty * (3 - 2 * ty)     # smoothstep
        a = g[y0, x0] * (1 - tx) + g[y0, x0 + 1] * tx
        b = g[y0 + 1, x0] * (1 - tx) + g[y0 + 1, x0 + 1] * tx
        return a * (1 - ty) + b * ty
    return at


def _clean_region(mask, u):
    """Quita motas y rellena huecos pequeños para que no queden trazos en fragmentos."""
    size = max(3, round(CLEAN * u) // 2 * 2 + 1)         # impar
    m = Image.fromarray((mask * 255).astype("uint8"))
    m = m.filter(ImageFilter.MinFilter(size)).filter(ImageFilter.MaxFilter(size))
    m = m.filter(ImageFilter.MaxFilter(size)).filter(ImageFilter.MinFilter(size))
    return np.array(m) > 127


# Marching squares: pares de bordes (T=arriba, R=derecha, B=abajo, L=izquierda) que cruza
# el contorno en cada caso de las 4 esquinas (a=sup.izq, b=sup.der, c=inf.der, d=inf.izq).
_EDGES = {1: [("L", "B")], 2: [("B", "R")], 3: [("L", "R")], 4: [("T", "R")],
          6: [("T", "B")], 7: [("T", "L")], 8: [("T", "L")], 9: [("T", "B")],
          11: [("T", "R")], 12: [("L", "R")], 13: [("B", "R")], 14: [("L", "B")]}


def _trace_contours(f, level=0.5):
    """Contornos de nivel `level` del campo `f` como polilíneas (lista de arrays (n,2) x,y)."""
    h, w = f.shape
    a, b, c, d = f[:-1, :-1], f[:-1, 1:], f[1:, 1:], f[1:, :-1]
    idx = ((a > level) * 8 + (b > level) * 4 + (c > level) * 2 + (d > level) * 1)
    ys, xs = np.nonzero((idx > 0) & (idx < 15))
    adj = defaultdict(list)
    for y, x in zip(ys.tolist(), xs.tolist()):
        case = int(idx[y, x])
        if case in (5, 10):                      # punto de silla: decide el centro
            hi = (a[y, x] + b[y, x] + c[y, x] + d[y, x]) / 4 > level
            if (case == 5) == hi:
                pairs = [("T", "L"), ("B", "R")]
            else:
                pairs = [("T", "R"), ("L", "B")]
        else:
            pairs = _EDGES[case]
        k = y * w + x
        ids = {"T": 2 * k, "B": 2 * (k + w), "L": 2 * k + 1, "R": 2 * (k + 1) + 1}
        for e1, e2 in pairs:
            i1, i2 = ids[e1], ids[e2]
            adj[i1].append(i2)
            adj[i2].append(i1)

    def point(eid):
        k, vert = eid >> 1, eid & 1
        y, x = divmod(k, w)
        if vert:                                  # arista vertical (y,x)-(y+1,x)
            return (x, y + (level - f[y, x]) / (f[y + 1, x] - f[y, x]))
        return (x + (level - f[y, x]) / (f[y, x + 1] - f[y, x]), y)

    visited, chains = set(), []
    ends = [k for k, v in adj.items() if len(v) == 1]        # contornos abiertos primero
    for start in ends + list(adj):
        if start in visited:
            continue
        chain, cur = [start], start
        visited.add(start)
        while True:
            nxt = [n for n in adj[cur] if n not in visited]
            if not nxt:
                break
            cur = nxt[0]
            chain.append(cur)
            visited.add(cur)
        if len(chain) > 2 and chain[0] in adj[chain[-1]]:    # contorno cerrado
            chain.append(chain[0])
        chains.append(np.array([point(e) for e in chain], dtype=np.float32))
    return chains


def _outline_strokes(region, u):
    """Contorno de una zona como trazos (pts, grosor), ya suavizado y sin fragmentos."""
    m = Image.fromarray((region * 255).astype("uint8"))
    f = np.array(m.filter(ImageFilter.GaussianBlur(max(1.0, OUTLINE_SMOOTH * u)))
                 ).astype(np.float32) / 255.0
    strokes = []
    for pts in _trace_contours(f):
        if len(pts) < 4:
            continue
        length = float(np.hypot(*np.diff(pts, axis=0).T).sum())
        if length < OUTLINE_MIN_LEN * u:
            continue
        pts = pts[::2] if len(pts) > 8 else pts              # menos puntos
        if len(pts) > 4:                                      # suaviza sin mover los extremos
            sm = pts.copy()
            sm[1:-1] = (pts[:-2] + pts[1:-1] * 2 + pts[2:]) / 4
            pts = sm
        strokes.append((pts, OUTLINE_THICK * u))
    return strokes


def _layer_strokes(region, angle, spacing, thick, seed, aw, ah, u_scale):
    """Polilíneas de una capa: líneas paralelas recortadas a `region`.
    `u_scale` = lado de análisis / ANALYSIS_SIDE (escala todas las medidas)."""
    step, min_len = STEP * u_scale, MIN_LEN * u_scale
    spacing = spacing * u_scale
    t = np.deg2rad(angle)
    nx, ny = np.cos(t), np.sin(t)              # normal (varía entre líneas)
    dx, dy = -np.sin(t), np.cos(t)             # dirección de la línea
    corners = np.array([(0, 0), (aw, 0), (0, ah), (aw, ah)], dtype=np.float32)
    us = corners[:, 0] * nx + corners[:, 1] * ny
    vs = corners[:, 0] * dx + corners[:, 1] * dy
    ks = np.arange(int(np.floor(us.min() / spacing)), int(np.ceil(us.max() / spacing)) + 1)
    v = np.arange(vs.min(), vs.max() + step, step, dtype=np.float32)
    wob = _noise_field(seed, 60 * u_scale, aw, ah)
    thk = _noise_field(seed + 100, 40 * u_scale, aw, ah)
    min_pts = max(2, int(min_len / step))
    strokes = []
    for k in ks:
        u0 = (k + 0.5) * spacing
        x0, y0 = u0 * nx + v * dx, u0 * ny + v * dy
        cx, cy = np.clip(x0, 0, aw - 1), np.clip(y0, 0, ah - 1)
        off = wob(cx, cy) * WOBBLE * u_scale                       # desplazamiento lateral
        x, y = x0 + off * nx, y0 + off * ny
        inside = (x >= 0) & (x < aw) & (y >= 0) & (y < ah)
        ok = np.zeros(v.shape, bool)
        ok[inside] = region[y[inside].astype(int), x[inside].astype(int)]
        # tramos continuos de True
        edges = np.diff(np.concatenate(([0], ok.astype(np.int8), [0])))
        for a, b in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
            if b - a < min_pts:
                continue
            pts = np.stack([x[a:b], y[a:b]], axis=1)
            mid = pts[len(pts) // 2]
            w = thick * u_scale * (1 + 0.3 * float(thk(np.array(mid[0]), np.array(mid[1]))))
            strokes.append((pts.astype(np.float32), w))
    return strokes


class HatchImage:
    """Resultado del sombreado cruzado: geometría + tamaño de salida en píxeles."""

    def __init__(self, width, height, aw, ah, layers, outlines=None):
        self.width, self.height = width, height        # tamaño de salida (px)
        self.aw, self.ah = aw, ah                      # espacio de los trazos
        self.layers = layers                           # lista de capas; cada una, lista de (pts, grosor)
        # contorno de cada capa (misma longitud que `layers`; vacío si no se pidieron)
        self.outlines = outlines or [[] for _ in layers]

    @property
    def size(self):
        return (self.width, self.height)

    def render(self):
        """Imagen PIL 'L' del tamaño de salida (trazos suavizados por supermuestreo)."""
        longest = max(self.width, self.height)
        ss = 3 if longest <= 1200 else 2 if longest <= 3000 else 1
        s = self.width / self.aw * ss
        img = Image.new("L", (self.width * ss, self.height * ss), 255)
        d = ImageDraw.Draw(img)
        for layer, outline in zip(self.layers, self.outlines):
            for pts, w in layer + outline:
                d.line([tuple(p) for p in (pts * s).tolist()], fill=INK,
                       width=max(1, round(w * s)), joint="curve")
        if ss > 1:
            img = img.resize((self.width, self.height), Image.LANCZOS)
        return img

    def to_svg(self, size_mm=None):
        """SVG vectorial. Con `size_mm` = (ancho, alto) queda a esa medida real."""
        dims = (f'width="{size_mm[0]:.2f}mm" height="{size_mm[1]:.2f}mm" ' if size_mm
                else f'width="{self.aw}" height="{self.ah}" ')
        out = ['<?xml version="1.0" encoding="UTF-8"?>',
               f'<svg xmlns="http://www.w3.org/2000/svg" {dims}'
               f'viewBox="0 0 {self.aw} {self.ah}">',
               f'<rect width="{self.aw}" height="{self.ah}" fill="#ffffff"/>',
               f'<g fill="none" stroke="#{INK:02x}{INK:02x}{INK:02x}" '
               'stroke-linecap="round" stroke-linejoin="round">']
        for i, (layer, outline) in enumerate(zip(self.layers, self.outlines), 1):
            for name, strokes in ((f"capa-{i}", layer), (f"contorno-{i}", outline)):
                if not strokes:
                    continue
                out.append(f'<g id="{name}">')
                for pts, w in strokes:
                    p = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts.tolist())
                    out.append(f'<polyline stroke-width="{w:.2f}" points="{p}"/>')
                out.append("</g>")
        out.append("</g></svg>")
        return "\n".join(out)


def hatch(arr, levels, outlines=False):
    """Array en gris (0-255, ya con autocontraste) -> HatchImage de `levels` niveles.
    Con `outlines` cada capa añade además el contorno de su zona.
    Los trazos se calculan a min(1080, lado mayor) px: una vista previa pequeña es
    ligera y se ve igual que la exportación final."""
    if not 2 <= levels <= HATCH_MAX_LEVELS:
        raise ValueError(f"El sombreado cruzado admite de 2 a {HATCH_MAX_LEVELS} niveles.")
    h, w = arr.shape
    side = min(ANALYSIS_SIDE, max(h, w))
    u = side / ANALYSIS_SIDE
    k = side / max(h, w)
    aw, ah = max(1, round(w * k)), max(1, round(h * k))
    small = Image.fromarray(np.clip(arr, 0, 255).astype("uint8")).resize((aw, ah), Image.LANCZOS)
    gray = np.array(small.filter(ImageFilter.GaussianBlur(8 * u))).astype(np.float32)
    layers, contours = [], []
    for i in range(levels - 1):
        region = _clean_region(gray < np.percentile(gray, COVER[i]), u)
        layers.append(_layer_strokes(region, ANGLES[i], SPACING[i], THICKNESS[i], i, aw, ah, u))
        contours.append(_outline_strokes(region, u) if outlines else [])
    return HatchImage(w, h, aw, ah, layers, contours)
