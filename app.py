"""
Posterizer - Interfaz gráfica (PyQt5).
Asistente en 4 pasos:
  1) Cargar imagen   2) Recortar   3) Elegir niveles (con vista previa)
  4) Salida: carpeta de imágenes y/o PDF (guardar o imprimir)
"""
import math
import os
import sys

from PIL import Image
from PyQt5.QtCore import Qt, QPointF, QThread, QTimer, QUrl, QSettings, QRectF, pyqtSignal
from PyQt5.QtGui import (QColor, QDesktopServices, QFont, QIcon, QImage, QPainter, QPen,
                         QPixmap, QPolygonF)
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

import hatching
import posterizer_core as core
from cropwidget import CropWidget


# ---------------------------------------------------------------------------
# Recursos y configuración
# ---------------------------------------------------------------------------
def resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


APP_ICON = resource_path(os.path.join("assets", "Posterizer.ico"))
DONATE_URL = "https://ko-fi.com/free_software_solutions"
NAG_EVERY = 10   # mostrar el recordatorio cada N exportaciones acumuladas

PREVIEW_SIDE = 700      # lado mayor de la imagen usada en la vista previa
CARD_SIDE = 230         # tamaño de cada tarjeta de vista previa
LEVEL_CHOICES = list(range(2, 11))
OTHER_LEVELS = {3, 4, 5, 6}    # niveles por defecto de Cuantiles / Lineal
CAPAS_LEVELS = {2, 4, 6, 8}    # niveles por defecto (método «Sombras en capas»)
DEFAULT_LEVELS = CAPAS_LEVELS
TRAZOS_LEVELS = {2, 4, 6, 8}   # niveles por defecto del sombreado cruzado
LEVEL_DEFAULTS = {"capas": CAPAS_LEVELS, "trazos": TRAZOS_LEVELS, "otros": OTHER_LEVELS}
NESTED_SHEETS_MAX = core.NESTED_MAX_LEVELS - 1   # láminas = sombras = niveles - 1
NESTED_SHEETS_DEFAULT = 6
MAX_LEVELS = {"trazos": hatching.HATCH_MAX_LEVELS}   # tope de niveles por método


def level_group(mode):
    """Cuantiles y Lineal comparten los niveles que el usuario tenía marcados."""
    return mode if mode in ("capas", "trazos") else "otros"
RATIOS = [
    ("Libre", None), ("Como la original", "orig"), ("1:1", 1.0),
    ("4:3", 4 / 3), ("3:4", 3 / 4), ("3:2", 3 / 2), ("2:3", 2 / 3),
    ("16:9", 16 / 9), ("9:16", 9 / 16),
    ("A4 vertical", 1 / math.sqrt(2)), ("A4 horizontal", math.sqrt(2)),
]


def pil_to_qimage(img):
    """PIL (L o RGB) -> QImage independiente del buffer original."""
    if img.mode == "L":
        return QImage(img.tobytes(), img.width, img.height, img.width,
                      QImage.Format_Grayscale8).copy()
    img = img.convert("RGB")
    return QImage(img.tobytes(), img.width, img.height, img.width * 3,
                  QImage.Format_RGB888).copy()


# ---------------------------------------------------------------------------
# Recordatorio de donación (nagware honesto: no bloquea nada)
# ---------------------------------------------------------------------------
class NagDialog(QDialog):
    def __init__(self, total, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recordatorio")
        self.setModal(False)
        self.setFixedWidth(420)
        lay = QVBoxLayout(self)
        title = QLabel(f"Llevas {total} exportaciones.")
        title.setStyleSheet("font-size:16px;font-weight:700;")
        lay.addWidget(title)
        body = QLabel(
            "Otras apps te habrían pedido la licencia unas "
            f"{total} veces.\n\nNosotros, una. Esta. Y se cierra sola.\n\n"
            "Posterizer seguirá siendo gratis, dones o no.")
        body.setWordWrap(True)
        lay.addWidget(body)
        row = QHBoxLayout()
        yes = QPushButton("Vale, invito al café")
        yes.clicked.connect(lambda: (QDesktopServices.openUrl(QUrl(DONATE_URL)),
                                     self.accept()))
        no = QPushButton("Ahora no, gracias")
        no.clicked.connect(self.accept)
        row.addWidget(yes)
        row.addWidget(no)
        lay.addLayout(row)


# ---------------------------------------------------------------------------
# Hilo de exportación
# ---------------------------------------------------------------------------
class ExportWorker(QThread):
    progress = pyqtSignal(int, int)
    log = pyqtSignal(str)
    finished_all = pyqtSignal(list, int, int)   # resultados, ok, fallos

    def __init__(self, img, levels, blurs, mode, upscale, folder, fmt, base,
                 include_gray, size_mm=None, outlines=False, only_new=False,
                 adaptive=False):
        super().__init__()
        self.img, self.levels, self.blurs = img, sorted(levels), blurs
        self.mode, self.upscale = mode, upscale
        self.folder, self.fmt, self.base = folder, fmt, base
        self.include_gray = include_gray
        self.size_mm = size_mm      # (ancho, alto) en mm de cada imagen, o None
        self.outlines = outlines    # sombreado cruzado: dibujar también los contornos
        self.only_new = only_new    # capas: cada nivel dibuja solo lo que añade
        self.adaptive = adaptive    # capas: umbrales según dónde hay detalle

    def run(self):
        results, ok, fail = [], 0, 0
        total = len(self.levels) + (1 if self.include_gray else 0)
        done = 0
        eff = core.clamp_factor(self.img.size, self.upscale)
        if eff < self.upscale:
            self.log.emit(f"Ampliación limitada a x{eff:.2f} "
                          f"(máx. {core.MAX_SIDE} px de lado).")
        try:
            gray = core.upscale_gray(self.img, self.upscale)
        except Exception as e:
            self.log.emit(f"✗ Error preparando la imagen: {e}")
            self.finished_all.emit([], 0, total)
            return

        def emit_one(caption, image, name, fmt=None):
            nonlocal ok, fail, done
            fmt = fmt or self.fmt
            try:
                if self.folder:
                    dpi = (image.width / (self.size_mm[0] / 25.4)
                           if self.size_mm else None)
                    core.save_image(image, os.path.join(self.folder, name), fmt, dpi,
                                    self.size_mm)
                results.append((caption, image))
                ok += 1
                self.log.emit(f"✓ {name}" if self.folder else f"✓ {caption}")
            except Exception as e:
                fail += 1
                self.log.emit(f"✗ {name}: {e}")
            done += 1
            self.progress.emit(done, total)

        # Orden de dibujo: de menos a más niveles; el original, al final.
        for n in self.levels:
            try:
                arr = core.blur_contrast(gray, self.blurs[n] * eff)
                img = (core.hatch(arr, n, self.outlines, self.adaptive, max(self.levels)) if self.mode == "trazos"
                       else core.quantize(arr, n, self.mode, only_new=self.only_new,
                                          adaptive=self.adaptive, guide=max(self.levels)))
            except Exception as e:
                fail += 1
                done += 1
                self.log.emit(f"✗ {n} niveles: {e}")
                self.progress.emit(done, total)
                continue
            name = core.output_name(self.base, n, self.blurs[n], self.mode,
                                    self.upscale, self.fmt, self.only_new)
            cap = (f"Lámina {n - 1} de {len(self.levels)}"
                   if self.mode == "trazos" or (self.mode == "capas" and self.only_new)
                   else f"{n} niveles")
            emit_one(cap, img, name)
        if self.include_gray:
            ofmt = "png" if self.fmt == "svg" else self.fmt   # el original no es vectorial
            emit_one("Original en grises", gray,
                     f"{self.base}_original"
                     + (f"_x{self.upscale:g}" if self.upscale > 1 else "")
                     + "." + ofmt, ofmt)
        self.finished_all.emit(results, ok, fail)


# ---------------------------------------------------------------------------
# PDF / impresión
# ---------------------------------------------------------------------------
def make_printer(landscape, paper):
    pr = QPrinter(QPrinter.HighResolution)
    pr.setPaperSize(QPrinter.A4 if paper == "A4" else QPrinter.Letter)
    pr.setOrientation(QPrinter.Landscape if landscape else QPrinter.Portrait)
    return pr


INSTRUCTIONS = [
    ("title", "Sombras en capas"),
    ("text", "Cómo usar estas hojas para entender las sombras sin oscurecerlas de más."),
    ("step", "1.  El blanco es el papel: déjalo vacío. Solo se dibujan sombras."),
    ("step", "2.  Empieza por la hoja con menos niveles: dibuja esa mancha de sombra "
             "con el gris indicado. Es tu base."),
    ("step", "3.  En la hoja siguiente, las formas y el gris de las anteriores son los "
             "mismos. No los oscurezcas: añade solo, dentro de ellos, las sombras nuevas, "
             "más oscuras."),
    ("step", "4.  Repite hasta el último nivel. Así cada sombra se queda con su tono y "
             "ves cuántos escalones de oscuridad hay."),
    ("head", "Tonos de cada nivel (de papel a sombra más oscura)"),
]


INSTRUCTIONS_GUIDE = [
    ("title", "Sombras en capas"),
    ("text", "Cómo usar estas hojas: una hoja por tono, sin oscurecer de más."),
    ("step", "1.  El blanco es el papel: déjalo vacío. Solo se dibujan sombras."),
    ("step", "2.  Encima de cada hoja hay una tira con todos los tonos, de papel a la "
             "sombra más oscura. El tono marcado en rojo es el que toca pintar en esa hoja."),
    ("step", "3.  Pinta con ese tono todas las zonas que ves coloreadas en la hoja. "
             "Incluye las que luego serán más oscuras: las sombras son aditivas."),
    ("step", "4.  Pasa a la hoja siguiente y repite con el tono marcado. Pinta siempre "
             "encima de lo anterior: cada zona acaba con el tono de la última hoja en que "
             "aparece."),
]


INSTRUCTIONS_HATCH = [
    ("title", "Sombreado cruzado"),
    ("text", "Cómo usar estas hojas para construir la sombra con trazos."),
    ("step", "1.  El blanco es el papel: déjalo vacío. Solo se dibujan trazos."),
    ("step", "2.  Empieza por la hoja 1: rellena con líneas paralelas, en la dirección que "
             "ves, las zonas con trazos. Es tu primera capa."),
    ("step", "3.  Cada hoja siguiente repite exactamente los trazos de la anterior y suma una "
             "capa: cruza con una dirección distinta solo dentro de las zonas más oscuras "
             "(las mismas sombras que en «Sombras en capas»). No cambies lo ya dibujado."),
    ("step", "4.  Sigue hasta la última hoja. Cuantas más capas se cruzan en un "
             "sitio, más oscuro queda."),
]


def paint_hatch(painter, hatch_img, target):
    """Dibuja el sombreado cruzado como vectores (trazos reales) dentro de `target`."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.fillRect(target, Qt.white)
    k = target.width() / hatch_img.aw
    pen = QPen(QColor(hatching.INK, hatching.INK, hatching.INK))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    for layer, outline in zip(hatch_img.layers, hatch_img.outlines):
        for pts, w in layer + outline:
            pen.setWidthF(w * k)
            painter.setPen(pen)
            painter.drawPolyline(QPolygonF(
                [QPointF(target.x() + x * k, target.y() + y * k) for x, y in pts.tolist()]))
    painter.restore()


def paint_instructions(painter, printer, levels, kind="capas"):
    """Primera página: cómo usar el método. En «capas» añade los tonos de cada nivel."""
    mm = printer.resolution() / 25.4
    page = printer.pageRect()
    margin = 15 * mm
    width = page.width() - 2 * margin
    y = margin
    flags = Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop
    texts = {"capas": INSTRUCTIONS, "capas_guia": INSTRUCTIONS_GUIDE}.get(
        kind, INSTRUCTIONS_HATCH)
    for item_kind, text in texts:
        f = QFont()
        f.setPointSizeF({"title": 20, "head": 12}.get(item_kind, 11))
        f.setBold(item_kind in ("title", "head"))
        painter.setFont(f)
        if item_kind == "head":
            y += 6 * mm
        rect = painter.boundingRect(QRectF(margin, y, width, page.height()), flags, text)
        painter.drawText(rect, flags, text)
        y = rect.bottom() + (3 if item_kind in ("step", "text") else 2) * mm
    if kind != "capas":     # trazos y capas con guía: la tira ya va en cada hoja
        return
    f = QFont()
    f.setPointSizeF(10)
    painter.setFont(f)
    sw, sh = 14 * mm, 9 * mm
    for n in sorted(levels):
        painter.setPen(Qt.black)
        painter.drawText(QRectF(margin, y, 26 * mm, sh), Qt.AlignVCenter, f"{n} niveles")
        for i, tone in enumerate(core.nested_tones(n)):
            painter.setBrush(QColor(tone, tone, tone))
            painter.setPen(QColor("#888"))
            painter.drawRect(QRectF(margin + 28 * mm + i * (sw + 1 * mm), y, sw, sh))
        y += sh + 2 * mm
    painter.setBrush(Qt.NoBrush)
    painter.setPen(Qt.black)


def paint_pages(printer, results, subtitle, layout, instruction_levels=None,
                size_mm=None, instruction_kind="capas"):
    """Dibuja los resultados en `printer`. layout: 'pages' o 'sheet'.
    Con `instruction_levels` (lista de niveles) añade antes la hoja de instrucciones.
    Con `size_mm` = (ancho, alto) cada imagen se dibuja a esa medida real; si no
    cabe en la hoja se reduce. Devuelve True si hubo que reducirla."""
    painter = QPainter(printer)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    res = printer.resolution()
    mm = res / 25.4
    page = printer.pageRect()
    margin = 10 * mm
    font = QFont()
    font.setPointSizeF(11 if layout == "pages" else 9)
    painter.setFont(font)
    cap_h = 8 * mm
    gap = 5 * mm
    shrunk = False

    def draw_cell(box, caption, image):
        vec = isinstance(image, hatching.HatchImage)     # trazos: se dibujan como vectores
        qi = None if vec else pil_to_qimage(image)
        avail = QRectF(box.x(), box.y(), box.width(), box.height() - cap_h)
        s = min(avail.width() / image.width, avail.height() / image.height)
        w, h = image.width * s, image.height * s
        target = QRectF(avail.x() + (avail.width() - w) / 2, avail.y(), w, h)
        if vec:
            paint_hatch(painter, image, target)
        else:
            painter.drawImage(target, qi)
        painter.drawRect(target)
        painter.drawText(QRectF(box.x(), target.bottom() + 1 * mm, box.width(), cap_h),
                         Qt.AlignHCenter | Qt.AlignTop, caption)

    area = QRectF(margin, margin, page.width() - 2 * margin,
                  page.height() - 2 * margin - 6 * mm)
    foot = QRectF(margin, page.height() - margin - 4 * mm, area.width(), 5 * mm)
    small = QFont(font)
    small.setPointSizeF(7)

    def footer():
        painter.save()
        painter.setFont(small)
        painter.setPen(Qt.gray)
        painter.drawText(foot, Qt.AlignRight | Qt.AlignVCenter, subtitle)
        painter.restore()

    if instruction_levels:
        paint_instructions(painter, printer, instruction_levels, instruction_kind)
        printer.newPage()

    if size_mm:
        iw, ih = size_mm[0] * mm, size_mm[1] * mm
        k = min(1.0, area.width() / iw, (area.height() - cap_h) / ih)
        if k < 1.0:
            iw, ih, shrunk = iw * k, ih * k, True
        cw, ch = iw, ih + cap_h
        if layout == "pages":
            for i, (cap, img) in enumerate(results):
                if i:
                    printer.newPage()
                draw_cell(QRectF(area.x() + (area.width() - cw) / 2, area.y(), cw, ch),
                          cap, img)
                footer()
        else:
            cols = max(1, int((area.width() + gap) // (cw + gap)))
            rows = max(1, int((area.height() + gap) // (ch + gap)))
            x0 = area.x() + (area.width() - (cols * cw + (cols - 1) * gap)) / 2
            for i, (cap, img) in enumerate(results):
                pos = i % (cols * rows)
                if i and pos == 0:
                    footer()
                    printer.newPage()
                r, c = divmod(pos, cols)
                draw_cell(QRectF(x0 + c * (cw + gap), area.y() + r * (ch + gap), cw, ch),
                          cap, img)
            footer()
    elif layout == "pages":
        for i, (cap, img) in enumerate(results):
            if i:
                printer.newPage()
            draw_cell(area, cap, img)
            footer()
    else:
        n = len(results)
        cols = math.ceil(math.sqrt(n * area.width() / area.height()))
        cols = max(1, min(cols, n))
        rows = math.ceil(n / cols)
        cw = (area.width() - gap * (cols - 1)) / cols
        ch = (area.height() - gap * (rows - 1)) / rows
        for i, (cap, img) in enumerate(results):
            r, c = divmod(i, cols)
            draw_cell(QRectF(area.x() + c * (cw + gap), area.y() + r * (ch + gap), cw, ch),
                      cap, img)
        footer()
    painter.end()
    return shrunk


PAPER_MM = {"A4": (210.0, 297.0), "Carta": (215.9, 279.4)}


def sheet_capacity(page_w, page_h, img_w, img_h):
    """Cuántas imágenes de img_w x img_h mm caben en una hoja (mismos márgenes
    que paint_pages)."""
    aw, ah = page_w - 20, page_h - 26
    cols = int((aw + 5) // (img_w + 5))
    rows = int((ah + 5) // (img_h + 8 + 5))
    return max(0, cols) * max(0, rows)


# ---------------------------------------------------------------------------
# Panel informativo: explica el método elegido
# ---------------------------------------------------------------------------
class LadderDiagram(QWidget):
    """Tonos de 2, 4 y 6 niveles del modo capas, uno bajo otro, para ver que
    cada nivel conserva los tonos del anterior."""
    ROWS = (2, 4, 6)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(len(self.ROWS) * 26 + 6)
        self.setFixedWidth(300)

    def paintEvent(self, _):
        p = QPainter(self)
        sw = 34
        for r, n in enumerate(self.ROWS):
            y = 3 + r * 26
            p.setPen(Qt.black)
            p.drawText(0, y, 70, 22, Qt.AlignVCenter, f"{n} niveles")
            for i, tone in enumerate(core.nested_tones(n)):
                x = 76 + i * (sw + 2)
                p.setBrush(QColor(tone, tone, tone))
                p.setPen(QColor("#999"))
                p.drawRect(x, y, sw, 22)
        p.end()


INFO_TEXTS = {
    "capas": (
        "<b>Sombras en capas: dibuja sin oscurecer lo que ya hiciste</b>"
        "<ul style='margin:3px 0 3px -18px'>"
        "<li><b>El blanco es el papel:</b> se deja vacío. Solo se dibujan sombras.</li>"
        "<li><b>Nivel 2:</b> una sola mancha de sombra, en gris claro. Es tu base.</li>"
        "<li><b>Cada nivel más</b> conserva las formas y el gris de los anteriores y "
        "añade sombras más oscuras <i>dentro</i> de ellas.</li></ul>"
        "<b>Uso:</b> imprime el PDF, dibuja el nivel más bajo y ve oscureciendo encima "
        "para los siguientes, sin borrar nada.<br>"
        "<span style='color:#777'>Con pocos niveles la sombra más oscura es gris, no negro.</span>"),
    "trazos": (
        "<b>Sombreado cruzado: la sombra se construye con trazos</b>"
        "<ul style='margin:3px 0 3px -18px'>"
        "<li><b>El blanco es el papel:</b> se deja vacío. Solo se dibujan líneas.</li>"
        "<li><b>Lámina 1:</b> una sola dirección de líneas paralelas, donde hay sombra, "
        "y las líneas principales de los contornos (silueta, ojos, boca…).</li>"
        "<li><b>Cada lámina más</b> añade una dirección nueva que cruza las anteriores, "
        "solo dentro de las sombras aditivas del modo capas: las mismas zonas.</li></ul>"
        "<b>Eliges cuántas láminas quieres</b> y se generan todas, de forma aditiva: cada "
        "una repite los trazos de la anterior y suma una capa. Con pocas láminas las "
        "líneas empiezan juntas; con muchas, más separadas y se van cerrando capa a capa "
        "para que quepan todas.<br>"
        "<b>Uso:</b> dibuja la lámina 1 y ve cruzando encima con cada lámina siguiente.<br>"
        "<span style='color:#777'>En «Salida» puedes guardar SVG vectorial, y el PDF "
        "lleva los trazos como vectores: nítidos a cualquier tamaño.</span>"),
    "cuantiles": (
        "<b>Cuantiles:</b> cada nivel cubre un área parecida de la imagen. "
        "Cada versión se calcula por separado, así que los tonos cambian entre 3, 4, 5… niveles."),
    "lineal": (
        "<b>Lineal:</b> corta la escala de grises en tramos iguales, de negro a blanco. "
        "Cada versión se calcula por separado, así que los tonos cambian entre 3, 4, 5… niveles."),
}


class InfoWidget(QFrame):
    """Panel plegable «¿Cómo funciona?» que se adapta al método elegido."""

    def __init__(self):
        super().__init__()
        self.setObjectName("info")
        self.setStyleSheet("#info{background:#f3f7fc;border:1px solid #c9d9ee;"
                           "border-radius:8px;}"
                           "#info QLabel{color:#1f2933;}")
        lay = QVBoxLayout(self)
        self.toggle = QPushButton()
        self.toggle.setCheckable(True)
        self.toggle.setFlat(True)
        self.toggle.setStyleSheet("text-align:left;font-weight:700;color:#2b6cb0;")
        self.toggle.toggled.connect(self._toggled)
        lay.addWidget(self.toggle)
        self.body = QWidget()
        bl = QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setTextFormat(Qt.RichText)
        self.text.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        row.addWidget(self.text, 1)
        self.side = QWidget()
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(0, 0, 0, 0)
        self.diagram = LadderDiagram()
        sl.addWidget(self.diagram)
        self.caption = QLabel("Los tonos de 2 y 4 niveles " + chr(10) + "se repiten idénticos en el 6.")
        self.caption.setStyleSheet("color:#777;font-size:11px;")
        sl.addWidget(self.caption)
        sl.addStretch()
        row.addWidget(self.side)
        bl.addLayout(row)
        lay.addWidget(self.body)
        self.toggle.setChecked(True)
        self._toggled(True)

    def _toggled(self, on):
        self.body.setVisible(on)
        self.toggle.setText("ℹ  ¿Cómo funciona?  " + ("▾" if on else "▸"))

    def set_mode(self, mode):
        self.text.setText(INFO_TEXTS.get(mode, ""))
        capas = mode == "capas"
        self.side.setVisible(capas)
        if capas:               # al elegir el método nuevo, se despliega
            self.toggle.setChecked(True)


# ---------------------------------------------------------------------------
# Ventana principal
# ---------------------------------------------------------------------------
STEPS = ["1 · Imagen", "2 · Recorte", "3 · Niveles", "4 · Salida"]


class PosterizerApp(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("Posterizer", "App")
        self.total_exports = int(self.settings.value("total_exports", 0))
        self.worker = None
        self._levels_by_mode = {}     # niveles marcados que se recuerdan por método
        self._cur_mode = "capas"
        self.src_path = None
        self.image = None        # PIL RGB completa (con rotaciones aplicadas)
        self.cropped = None      # PIL RGB recortada
        self.preview_src = None  # versión reducida para vista previa
        self.preview_scale = 1.0
        self._pending = None     # acción tras exportar: ('files',) / ('print',)

        self.setWindowTitle("Posterizer — gratis para siempre")
        if os.path.exists(APP_ICON):
            self.setWindowIcon(QIcon(APP_ICON))
        self.setMinimumSize(900, 640)
        self.setAcceptDrops(True)

        self.timer = QTimer(self, singleShot=True, interval=180)
        self.timer.timeout.connect(self._refresh_previews)
        self._build_ui()
        self._go(0)

    # -- Construcción -------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)

        self.step_labels = []
        bar = QHBoxLayout()
        for s in STEPS:
            lb = QLabel(s)
            self.step_labels.append(lb)
            bar.addWidget(lb)
        bar.addStretch()
        root.addLayout(bar)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_load())
        self.stack.addWidget(self._page_crop())
        self.stack.addWidget(self._page_levels())
        self.stack.addWidget(self._page_output())
        root.addWidget(self.stack, 1)

        nav = QHBoxLayout()
        tagline = QLabel("Gratis para siempre. Sin cuenta, sin trucos.")
        tagline.setStyleSheet("color:#777;font-size:12px;")
        nav.addWidget(tagline)
        donate = QPushButton("☕ Donar un café")
        donate.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DONATE_URL)))
        nav.addWidget(donate)
        nav.addStretch()
        self.btn_back = QPushButton("◀ Atrás")
        self.btn_back.clicked.connect(lambda: self._go(self.stack.currentIndex() - 1))
        self.btn_next = QPushButton("Siguiente ▶")
        self.btn_next.clicked.connect(lambda: self._go(self.stack.currentIndex() + 1))
        self.btn_reset = QPushButton("⟲ Reiniciar")
        self.btn_reset.setToolTip("Descarta la foto actual y vuelve al paso 1 para cargar otra.")
        self.btn_reset.clicked.connect(self._reset)
        nav.addWidget(self.btn_reset)
        nav.addWidget(self.btn_back)
        nav.addWidget(self.btn_next)
        root.addLayout(nav)

    # Página 1 ---------------------------------------------------------------
    def _page_load(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addStretch()
        self.drop = QLabel("Arrastra una foto aquí\no pulsa «Abrir imagen…»")
        self.drop.setAlignment(Qt.AlignCenter)
        self.drop.setMinimumHeight(220)
        self.drop.setStyleSheet(
            "border:2px dashed #999;border-radius:12px;color:#666;font-size:16px;")
        lay.addWidget(self.drop)
        btn = QPushButton("Abrir imagen…")
        btn.clicked.connect(self._pick_image)
        lay.addWidget(btn, 0, Qt.AlignHCenter)
        self.load_info = QLabel("")
        self.load_info.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.load_info)
        lay.addStretch()
        return w

    # Página 2 ---------------------------------------------------------------
    def _page_crop(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        tools = QHBoxLayout()
        tools.addWidget(QLabel("Proporción:"))
        self.ratio_combo = QComboBox()
        for name, _ in RATIOS:
            self.ratio_combo.addItem(name)
        self.ratio_combo.currentIndexChanged.connect(self._ratio_changed)
        tools.addWidget(self.ratio_combo)
        for text, deg in (("⟲ Girar", 90), ("⟳ Girar", -90)):
            b = QPushButton(text)
            b.clicked.connect(lambda _, d=deg: self._rotate(d))
            tools.addWidget(b)
        reset = QPushButton("Seleccionar todo")
        reset.clicked.connect(lambda: self.crop.select_all())
        tools.addWidget(reset)
        tools.addStretch()
        lay.addLayout(tools)
        self.crop = CropWidget()
        lay.addWidget(self.crop, 1)
        hint = QLabel("Arrastra para dibujar el recorte; mueve el recuadro o tira de "
                      "sus esquinas y bordes.")
        hint.setStyleSheet("color:#777;")
        lay.addWidget(hint)
        return w

    # Página 3 ---------------------------------------------------------------
    def _page_levels(self):
        w = QWidget()
        lay = QHBoxLayout(w)

        side = QVBoxLayout()
        self.sheets_box = QGroupBox("Láminas a generar")
        sg = QVBoxLayout(self.sheets_box)
        self.sheets = QSpinBox()
        self.sheets.setRange(1, NESTED_SHEETS_MAX)
        self.sheets.setValue(NESTED_SHEETS_DEFAULT)
        self.sheets.setToolTip("Sombras en capas: nº de tonos a pintar. Se genera una lámina "
                               "por tono, de la más clara a la más oscura.")
        self.sheets.valueChanged.connect(self._params_changed)
        sg.addWidget(self.sheets)
        self.sheets_hint = QLabel()
        self.sheets_hint.setWordWrap(True)
        sg.addWidget(self.sheets_hint)
        side.addWidget(self.sheets_box)
        self.levels_box = box = QGroupBox("Niveles a generar")
        g = QGridLayout(box)
        self.level_checks = {}
        for i, n in enumerate(LEVEL_CHOICES):
            cb = QCheckBox(str(n))
            cb.setChecked(n in DEFAULT_LEVELS)
            cb.toggled.connect(self._params_changed)
            self.level_checks[n] = cb
            g.addWidget(cb, i // 3, i % 3)
        side.addWidget(box)

        opt = QGroupBox("Ajustes")
        og = QGridLayout(opt)
        self.blur_title = QLabel("Suavizado (desenfoque):")
        og.addWidget(self.blur_title, 0, 0, 1, 2)
        self.blur = QSlider(Qt.Horizontal)
        self.blur.setRange(0, 200)      # décimas de píxel
        self.blur.setValue(20)
        self.blur_lbl = QLabel("2.0")
        self.blur.valueChanged.connect(
            lambda v: (self.blur_lbl.setText(f"{v / 10:.1f}"), self._params_changed()))
        og.addWidget(self.blur, 1, 0)
        og.addWidget(self.blur_lbl, 1, 1)
        og.addWidget(QLabel("Método:"), 2, 0, 1, 2)
        self.mode = QComboBox()
        self.mode.addItem("Sombras en capas (blanco = papel)", "capas")
        self.mode.addItem("Sombreado cruzado (trazos)", "trazos")
        self.mode.addItem("Cuantiles (masas equilibradas)", "cuantiles")
        self.mode.addItem("Lineal (cortes iguales)", "lineal")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        og.addWidget(self.mode, 3, 0, 1, 2)
        og.addWidget(QLabel("Ampliar (para dibujar grande):"), 4, 0, 1, 2)
        self.upscale = QComboBox()
        for f in (1, 1.5, 2, 3, 4):
            self.upscale.addItem(f"x{f:g}", f)
        self.upscale.currentIndexChanged.connect(self._update_size_info)
        og.addWidget(self.upscale, 5, 0, 1, 2)
        self.px_info = QLabel("")
        self.px_info.setStyleSheet("color:#777;")
        self.px_info.setWordWrap(True)
        og.addWidget(self.px_info, 6, 0, 1, 2)
        self.include_gray = QCheckBox("Incluir original en grises")
        self.include_gray.setChecked(True)
        og.addWidget(self.include_gray, 7, 0, 1, 2)
        self.include_gray.toggled.connect(self._params_changed)
        self.prog = QCheckBox("Desenfoque progresivo")
        self.prog.setToolTip("Menos niveles = más desenfoque; más niveles = menos.")
        og.addWidget(self.prog, 8, 0, 1, 2)
        self.blur_min_title = QLabel("Desenfoque mínimo (más niveles):")
        og.addWidget(self.blur_min_title, 9, 0, 1, 2)
        self.blur_min = QSlider(Qt.Horizontal)
        self.blur_min.setRange(0, 200)
        self.blur_min.setValue(5)
        self.blur_min_lbl = QLabel("0.5")
        self.blur_min.valueChanged.connect(
            lambda v: (self.blur_min_lbl.setText(f"{v / 10:.1f}"), self._params_changed()))
        og.addWidget(self.blur_min, 10, 0)
        og.addWidget(self.blur_min_lbl, 10, 1)
        self.prog.setChecked(True)
        self.prog.toggled.connect(self._prog_toggled)
        self._prog_toggled(True)
        self.outline_chk = QCheckBox("Dibujar contornos de las formas")
        self.outline_chk.setChecked(True)
        self.outline_chk.setToolTip("Sombreado cruzado: dibuja los bordes reales de la foto "
                                    "(silueta, ojos, boca…); cada nivel añade líneas más finas.")
        self.outline_chk.toggled.connect(self._params_changed)
        og.addWidget(self.outline_chk, 11, 0, 1, 2)
        self.outline_chk.setVisible(False)
        self.new_chk = QCheckBox("Una hoja por tono, con guía de tonos")
        self.new_chk.setChecked(True)
        self.new_chk.setToolTip("Sombras en capas: cada nivel muestra solo el tono que toca pintar "
                                "ahora (las sombras son aditivas) y, encima, la tira con todos los "
                                "tonos con el actual marcado en rojo.")
        self.new_chk.toggled.connect(self._update_level_ui)
        self.new_chk.toggled.connect(self._params_changed)
        og.addWidget(self.new_chk, 12, 0, 1, 2)
        self.new_chk.setVisible(False)
        self.adapt_chk = QCheckBox("Niveles adaptados al detalle de la foto")
        self.adapt_chk.setToolTip("Sombras en capas y sombreado cruzado: reparte los umbrales donde la foto tiene más "
                                  "detalle (p. ej. el pelo en una foto oscura) en vez de usar "
                                  "tonos fijos.")
        self.adapt_chk.toggled.connect(self._params_changed)
        og.addWidget(self.adapt_chk, 13, 0, 1, 2)
        self.adapt_chk.setVisible(False)
        side.addWidget(opt)
        self._update_level_ui()
        side.addStretch()
        side_w = QWidget()
        side_w.setLayout(side)
        side_w.setFixedWidth(300)
        lay.addWidget(side_w)

        self.cards_host = QWidget()
        self.cards_grid = QGridLayout(self.cards_host)
        self.cards_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.cards_host)
        right = QVBoxLayout()
        self.info = InfoWidget()
        self.info.set_mode(self.mode.currentData())
        right.addWidget(self.info)
        right.addWidget(scroll, 1)
        lay.addLayout(right, 1)
        return w

    # Página 4 ---------------------------------------------------------------
    def _page_output(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        self.chk_files = QCheckBox("Guardar también cada imagen por separado en una carpeta")
        self.chk_files.setChecked(False)
        lay.addWidget(self.chk_files)
        self.files_box = QWidget()          # detalles: solo si se guardan imágenes
        g = QGridLayout(self.files_box)
        g.setContentsMargins(0, 0, 0, 0)
        self.out_dir = QLineEdit()
        g.addWidget(QLabel("Carpeta:"), 0, 0)
        g.addWidget(self.out_dir, 0, 1)
        b = QPushButton("Examinar...")
        b.clicked.connect(self._pick_out_dir)
        g.addWidget(b, 0, 2)
        g.addWidget(QLabel("Formato:"), 1, 0)
        self.fmt = QComboBox()
        self.fmt.addItems(["png", "jpg"])
        g.addWidget(self.fmt, 1, 1)
        lay.addWidget(self.files_box)
        self.chk_files.toggled.connect(self.files_box.setVisible)
        self.files_box.setVisible(self.chk_files.isChecked())

        size = QGroupBox("Tamaño de cada imagen")
        sg = QGridLayout(size)
        self.size_mode = QComboBox()
        self.size_mode.addItem("Automático (ajustar a la hoja)", "auto")
        self.size_mode.addItem("Medida exacta", "fixed")
        sg.addWidget(self.size_mode, 0, 0)
        self.size_box = QWidget()           # detalles: solo con «Medida exacta»
        sb = QHBoxLayout(self.size_box)
        sb.setContentsMargins(0, 0, 0, 0)
        self.size_dim = QComboBox()
        self.size_dim.addItems(["Ancho", "Alto"])
        self.size_val = QDoubleSpinBox()
        self.size_val.setDecimals(1)
        self.size_unit = QComboBox()
        self.size_unit.addItem("cm", 10.0)
        self.size_unit.addItem("mm", 1.0)
        self._unit_factor = 10.0
        self._set_size_range()
        self.size_val.setValue(10.0)
        self.size_info = QLabel("")
        self.size_info.setStyleSheet("color:#555;")
        for wd in (self.size_dim, self.size_val, self.size_unit, self.size_info):
            sb.addWidget(wd)
        sb.addStretch()
        sg.addWidget(self.size_box, 1, 0)
        lay.addWidget(size)
        self.size_mode.currentIndexChanged.connect(self._size_changed)
        self.size_dim.currentIndexChanged.connect(self._size_changed)
        self.size_val.valueChanged.connect(self._size_changed)
        self.size_unit.currentIndexChanged.connect(self._unit_changed)
        self.size_box.setVisible(False)

        pdf = QGroupBox("PDF e impresión")
        pg = QGridLayout(pdf)
        self.chk_pdf = QCheckBox("Guardar un PDF con todas las imágenes")
        self.chk_pdf.setChecked(True)
        pg.addWidget(self.chk_pdf, 0, 0, 1, 3)
        self.pdf_file_box = QWidget()       # ruta del PDF: solo si se guarda PDF
        pf = QHBoxLayout(self.pdf_file_box)
        pf.setContentsMargins(0, 0, 0, 0)
        self.pdf_path = QLineEdit()
        pf.addWidget(QLabel("Archivo PDF:"))
        pf.addWidget(self.pdf_path, 1)
        bp = QPushButton("Examinar...")
        bp.clicked.connect(self._pick_pdf)
        pf.addWidget(bp)
        pg.addWidget(self.pdf_file_box, 1, 0, 1, 3)
        self.chk_pdf.toggled.connect(self.pdf_file_box.setVisible)
        self.pdf_file_box.setVisible(self.chk_pdf.isChecked())
        pg.addWidget(QLabel("Papel:"), 2, 0)
        self.paper = QComboBox()
        self.paper.addItems(["A4", "Carta"])
        pg.addWidget(self.paper, 2, 1)
        pg.addWidget(QLabel("Disposición:"), 3, 0)
        self.pdf_layout = QComboBox()
        self.pdf_layout.addItem("Todas las imágenes en la misma hoja", "sheet")
        self.pdf_layout.addItem("Una imagen por página", "pages")
        pg.addWidget(self.pdf_layout, 3, 1)
        self.chk_instr = QCheckBox("Imprimir las instrucciones (primera hoja)")
        self.chk_instr.setChecked(True)
        self.chk_instr.setToolTip("Añade una hoja que explica cómo usar este método.")
        pg.addWidget(self.chk_instr, 4, 0, 1, 3)
        lay.addWidget(pdf)

        row = QHBoxLayout()
        self.btn_export = QPushButton("Generar")
        self.btn_export.clicked.connect(lambda: self._start_export("files"))
        self.btn_print = QPushButton("🖨 Imprimir…")
        self.btn_print.clicked.connect(lambda: self._start_export("print"))
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_print)
        lay.addLayout(row)

        self.progress = QProgressBar()
        lay.addWidget(self.progress)
        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setPlaceholderText("Registro...")
        lay.addWidget(self.console, 1)
        return w

    # -- Navegación -----------------------------------------------------------
    def _go(self, i):
        i = max(0, min(i, len(STEPS) - 1))
        if i >= 1 and self.image is None:
            i = 0
        if i == 2 and self.stack.currentIndex() != 2:
            if not self._take_crop():
                return
        if i == 3 and self.cropped is None:
            return
        self.stack.setCurrentIndex(i)
        for k, lb in enumerate(self.step_labels):
            lb.setStyleSheet("font-weight:700;color:#2b6cb0;padding:2px 10px;"
                             if k == i else "color:#999;padding:2px 10px;")
        self.btn_back.setEnabled(i > 0)
        self.btn_reset.setVisible(i > 0)
        self.btn_next.setVisible(i < len(STEPS) - 1)
        self.btn_next.setEnabled(self.image is not None)
        if i == 2:
            self._params_changed()
        if i == 3:
            self._default_outputs()
            self._size_changed()
            self._refresh_formats()
            self.chk_instr.setVisible(self.mode.currentData() in ("capas", "trazos"))

    def _reset(self):
        """Vuelve al paso 1: descarta la foto y sus salidas (los ajustes se conservan)."""
        if self.worker is not None and self.worker.isRunning():
            return
        self.src_path = self.image = self.cropped = self.preview_src = None
        self.preview_scale = 1.0
        self.load_info.clear()
        self.out_dir.clear()
        self.pdf_path.clear()
        self.console.clear()
        self.progress.setValue(0)
        self.ratio_combo.setCurrentIndex(0)
        self._go(0)

    def _take_crop(self):
        l, t, r, b = self.crop.selection()
        if r - l < 8 or b - t < 8:
            QMessageBox.warning(self, "Recorte", "El recorte es demasiado pequeño.")
            return False
        self.cropped = self.image.crop((l, t, r, b))
        longest = max(self.cropped.size)
        self.preview_scale = min(1.0, PREVIEW_SIDE / longest)
        if self.preview_scale < 1.0:
            sz = (max(1, round(self.cropped.width * self.preview_scale)),
                  max(1, round(self.cropped.height * self.preview_scale)))
            self.preview_src = self.cropped.resize(sz, Image.LANCZOS)
        else:
            self.preview_src = self.cropped
        self._update_size_info()
        return True

    # -- Cargar imagen ----------------------------------------------------------
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls and self.stack.currentIndex() == 0:
            self._load(urls[0].toLocalFile())

    def _pick_image(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Selecciona una imagen", self.settings.value("in_dir", ""),
            "Imágenes (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp *.heic *.heif);;Todos (*)")
        if f:
            self._load(f)

    def _load(self, path):
        try:
            img = core.load_image(path)
        except Exception as e:
            QMessageBox.warning(self, "No se pudo abrir", str(e))
            return
        self.src_path = path
        self.image = img
        self.cropped = None
        self.out_dir.clear()
        self.pdf_path.clear()
        self.settings.setValue("in_dir", os.path.dirname(path))
        self.load_info.setText(f"{os.path.basename(path)} — {img.width}×{img.height} px")
        self.crop.set_image(pil_to_qimage(img))
        self._go(1)

    # -- Recorte ------------------------------------------------------------------
    def _ratio_changed(self, idx):
        val = RATIOS[idx][1]
        if val == "orig":
            val = self.image.width / self.image.height if self.image else None
        self.crop.set_ratio(val)

    def _rotate(self, deg):
        if self.image is None:
            return
        self.image = self.image.rotate(deg, expand=True)
        self.crop.set_image(pil_to_qimage(self.image))
        self._ratio_changed(self.ratio_combo.currentIndex())

    # -- Vista previa -------------------------------------------------------------
    def _params_changed(self, *_):
        self.timer.start()

    def _refresh_formats(self):
        """SVG vectorial solo está disponible con el sombreado cruzado."""
        want = ["png", "jpg"] + (["svg"] if self.mode.currentData() == "trazos" else [])
        if [self.fmt.itemText(i) for i in range(self.fmt.count())] != want:
            cur = self.fmt.currentText()
            self.fmt.clear()
            self.fmt.addItems(want)
            if cur in want:
                self.fmt.setCurrentText(cur)

    def _set_size_range(self):
        """Rango y paso del campo de medida según la unidad (cm o mm)."""
        cm = self.size_unit.currentData() == 10.0
        self.size_val.setRange(1.0 if cm else 10.0, 200.0 if cm else 2000.0)
        self.size_val.setSingleStep(0.5 if cm else 5.0)

    def _unit_changed(self, *_):
        old = self._unit_factor
        self._unit_factor = self.size_unit.currentData()
        mm_val = self.size_val.value() * old          # conserva la misma medida real
        self.size_val.blockSignals(True)
        self._set_size_range()
        self.size_val.setValue(mm_val / self._unit_factor)
        self.size_val.blockSignals(False)
        self._size_changed()

    def _size_mm(self):
        """(ancho, alto) en mm de cada imagen, o None si es automático."""
        if self.size_mode.currentData() != "fixed" or self.cropped is None:
            return None
        v = self.size_val.value() * self.size_unit.currentData()
        aspect = self.cropped.width / self.cropped.height
        return (v, v / aspect) if self.size_dim.currentIndex() == 0 else (v * aspect, v)

    def _size_changed(self, *_):
        self.size_box.setVisible(self.size_mode.currentData() == "fixed")
        sz = self._size_mm()
        if sz:
            f = self.size_unit.currentData()
            unit = self.size_unit.currentText()
            self.size_info.setText(f"→ {sz[0] / f:.1f} × {sz[1] / f:.1f} {unit}")

    def _mode_changed(self, *_):
        mode = self.mode.currentData()
        self._levels_by_mode[level_group(self._cur_mode)] = set(self._checked_levels())
        self._cur_mode = mode
        self.info.set_mode(mode)
        self.outline_chk.setVisible(mode == "trazos")
        self._update_level_ui()
        top = MAX_LEVELS.get(mode, LEVEL_CHOICES[-1])
        wanted = self._levels_by_mode.get(level_group(mode), LEVEL_DEFAULTS[level_group(mode)])
        for n, cb in self.level_checks.items():
            cb.blockSignals(True)
            cb.setChecked(n in wanted and n <= top)
            cb.setEnabled(n <= top)         # cada método tiene su tope de niveles
            cb.blockSignals(False)
        self._params_changed()

    def _prog_toggled(self, on):
        self.blur_title.setText("Desenfoque máximo (menos niveles):" if on
                                else "Suavizado (desenfoque):")
        for wd in (self.blur_min_title, self.blur_min, self.blur_min_lbl):
            wd.setVisible(on)
        self._params_changed()

    def _blurs(self):
        """{nivel: desenfoque}: fijo, o progresivo si está activado."""
        mx = self.blur.value() / 10
        mn = None
        if self.prog.isChecked():
            mn = self.blur_min.value() / 10
            mx, mn = max(mx, mn), min(mx, mn)
        blurs = core.level_blurs(self._selected_levels(), mx, mn)
        if self.mode.currentData() == "trazos" and blurs:
            # láminas aditivas: todas parten de la misma imagen, así que las zonas no cambian
            blurs = dict.fromkeys(blurs, blurs[max(blurs)])
        return blurs

    def _checked_levels(self):
        return [n for n, cb in self.level_checks.items() if cb.isChecked()]

    def _sheet_mode(self):
        """Capas con una lámina por tono, o trazos (siempre aditivos): se pide un solo
        número y se generan todas las láminas."""
        mode = self.mode.currentData()
        return mode == "trazos" or (mode == "capas" and self.new_chk.isChecked())

    def _selected_levels(self):
        if self._sheet_mode():      # k láminas = k sombras/capas = niveles 2..k+1
            return list(range(2, self.sheets.value() + 2))
        return self._checked_levels()

    def _update_level_ui(self, *_):
        capas = self.mode.currentData() == "capas"
        self.new_chk.setVisible(capas)
        self.adapt_chk.setVisible(capas or self.mode.currentData() == "trazos")
        self.sheets_hint.setText(
            "Cada lámina repite los trazos de la anterior y suma una capa nueva."
            if self.mode.currentData() == "trazos"
            else "Una lámina por tono, de claro a oscuro.")
        self.sheets_box.setVisible(self._sheet_mode())
        self.levels_box.setVisible(not self._sheet_mode())

    def _update_size_info(self, *_):
        if self.cropped is None:
            return
        f = core.clamp_factor(self.cropped.size, self.upscale.currentData())
        w, h = round(self.cropped.width * f), round(self.cropped.height * f)
        txt = f"Salida: {w}×{h} px"
        if f < self.upscale.currentData():
            txt += " (limitado por tamaño máximo)"
        self.px_info.setText(txt)

    def _refresh_previews(self):
        if self.preview_src is None or self.stack.currentIndex() != 2:
            return
        while self.cards_grid.count():
            item = self.cards_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        mode = self.mode.currentData()
        blurs = self._blurs()
        gray = core.upscale_gray(self.preview_src, 1.0)
        items = []
        for n in self._selected_levels():
            arr = core.blur_contrast(gray, blurs[n] * self.preview_scale)
            cap = f"Lámina {n - 1} de {self.sheets.value()}" if self._sheet_mode() else f"{n} niveles"
            if self.prog.isChecked():
                cap += f" · desenf. {blurs[n]:.1f}"
            items.append((cap, core.quantize(arr, n, mode, self.outline_chk.isChecked(),
                                                self.new_chk.isChecked(),
                                                self.adapt_chk.isChecked(),
                                                max(self._selected_levels()))))
        if self.include_gray.isChecked():   # el original va siempre al final
            items.append(("Original en grises", gray))
        if not items:
            lb = QLabel("Marca al menos un nivel.")
            self.cards_grid.addWidget(lb, 0, 0)
            return
        cols = max(1, (self.width() - 380) // (CARD_SIDE + 16))
        for i, (cap, img) in enumerate(items):
            card = QFrame()
            cl = QVBoxLayout(card)
            pic = QLabel()
            pm = QPixmap.fromImage(pil_to_qimage(img)).scaled(
                CARD_SIDE, CARD_SIDE, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pic.setPixmap(pm)
            pic.setAlignment(Qt.AlignCenter)
            cl.addWidget(pic)
            t = QLabel(cap)
            t.setAlignment(Qt.AlignCenter)
            t.setStyleSheet("font-weight:600;")
            cl.addWidget(t)
            self.cards_grid.addWidget(card, i // cols, i % cols)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.stack.currentIndex() == 2:
            self._params_changed()

    # -- Salida -------------------------------------------------------------------
    def _base(self):
        return os.path.splitext(os.path.basename(self.src_path))[0]

    def _default_outputs(self):
        if not self.out_dir.text().strip():
            self.out_dir.setText(os.path.join(os.path.dirname(self.src_path),
                                              self._base() + "_posterizado"))
        if not self.pdf_path.text().strip():
            self.pdf_path.setText(os.path.join(os.path.dirname(self.src_path),
                                               self._base() + "_posterizado.pdf"))

    def _pick_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Carpeta de salida")
        if d:
            self.out_dir.setText(d)

    def _pick_pdf(self):
        f, _ = QFileDialog.getSaveFileName(self, "Guardar PDF", self.pdf_path.text(),
                                           "PDF (*.pdf)")
        if f:
            self.pdf_path.setText(f)
            self.chk_pdf.setChecked(True)

    def _start_export(self, action):
        levels = self._selected_levels()
        if not levels and not self.include_gray.isChecked():
            QMessageBox.warning(self, "Niveles", "Elige al menos un nivel (paso 3).")
            return
        folder = None
        if action == "files":
            want_files, want_pdf = self.chk_files.isChecked(), self.chk_pdf.isChecked()
            if not (want_files or want_pdf):
                QMessageBox.warning(self, "Salida", "Marca guardar imágenes y/o PDF.")
                return
            if want_files:
                folder = self.out_dir.text().strip()
                if not folder:
                    QMessageBox.warning(self, "Salida", "Indica la carpeta.")
                    return
            if want_pdf and not self.pdf_path.text().strip():
                QMessageBox.warning(self, "Salida", "Indica el archivo PDF.")
                return
        self._pending = action
        self.console.clear()
        self.progress.setValue(0)
        self.setEnabled(False)
        self.worker = ExportWorker(
            self.cropped, levels, self._blurs(), self.mode.currentData(),
            self.upscale.currentData(), folder, self.fmt.currentText(), self._base(),
            self.include_gray.isChecked(), self._size_mm(), self.outline_chk.isChecked(),
            self.new_chk.isChecked(), self.adapt_chk.isChecked())
        self.worker.progress.connect(
            lambda d, t: (self.progress.setMaximum(t), self.progress.setValue(d)))
        self.worker.log.connect(self.console.append)
        self.worker.finished_all.connect(self._export_done)
        self.worker.start()

    def _export_done(self, results, ok, fail):
        self.setEnabled(True)
        action, self._pending = self._pending, None
        if not results:
            QMessageBox.warning(self, "Error", "No se generó ninguna imagen.")
            return
        landscape = results[0][1].width > results[0][1].height
        paper = "A4" if self.paper.currentIndex() == 0 else "Carta"
        size = self._size_mm()
        w, h = results[0][1].size
        if size:    # la guía de tonos hace la imagen más alta: la foto conserva su ancho
            size = (size[0], size[0] * h / w)
        subtitle = f"Posterizer · {self._base()} · {w}×{h} px"

        msg = f"{ok} correctas, {fail} fallidas."
        make_pdf = action == "print" or self.chk_pdf.isChecked()
        layout = self.pdf_layout.currentData() if len(results) > 1 else "pages"
        if size and layout == "sheet" and len(results) > 1:
            # con medida exacta, la orientación en que quepan más imágenes por hoja
            pw, ph = PAPER_MM[paper]
            port = sheet_capacity(pw, ph, *size)
            land = sheet_capacity(ph, pw, *size)
            if land != port:
                landscape = land > port
        instr = (self._selected_levels()
                 if self.mode.currentData() in ("capas", "trazos")
                 and self.chk_instr.isChecked()
                 else None)
        kind = self.mode.currentData()
        if kind == "capas" and self.new_chk.isChecked():
            kind = "capas_guia"
        shrunk = False
        pdf_file = None
        try:
            if not make_pdf:
                pass
            elif action == "print":
                pr = make_printer(landscape, paper)
                if QPrintDialog(pr, self).exec_() != QDialog.Accepted:
                    return
                shrunk = paint_pages(pr, results, subtitle, layout, instr, size, kind)
                self.console.append("✓ Enviado a la impresora.")
            else:
                path = self.pdf_path.text().strip()
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                pr = make_printer(landscape, paper)
                pr.setOutputFormat(QPrinter.PdfFormat)
                pr.setOutputFileName(path)
                shrunk = paint_pages(pr, results, subtitle, layout, instr, size, kind)
                pdf_file = path
                self.console.append(f"✓ PDF: {path}")
            if size and shrunk:
                self.console.append("⚠ La medida elegida no cabe en la hoja: "
                                    "se redujo para que quepa.")
        except Exception as e:
            self.console.append(f"✗ PDF/impresión: {e}")
            fail += 1
        self.console.append(f"\nTerminado: {msg}")

        folder = self.out_dir.text().strip()
        self._after_success(ok)
        images_dir = folder if (action == "files" and self.chk_files.isChecked()
                                and os.path.isdir(folder)) else None
        self._show_done(msg, images_dir, pdf_file)

    def _show_done(self, msg, images_dir, pdf_file):
        """Aviso final con atajos para abrir el PDF generado o la carpeta de salida."""
        pdf_file = pdf_file if pdf_file and os.path.isfile(pdf_file) else None
        folder = images_dir or (os.path.dirname(pdf_file) if pdf_file else None)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Hecho")
        box.setText(msg)
        b_pdf = box.addButton("Abrir PDF", QMessageBox.AcceptRole) if pdf_file else None
        b_dir = box.addButton("Abrir carpeta", QMessageBox.AcceptRole) if folder else None
        box.addButton("Cerrar", QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if b_pdf is not None and clicked is b_pdf:
            QDesktopServices.openUrl(QUrl.fromLocalFile(pdf_file))
        elif b_dir is not None and clicked is b_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _after_success(self, ok):
        prev = self.total_exports
        if ok > 0:
            self.total_exports += 1
            self.settings.setValue("total_exports", self.total_exports)
            if prev // NAG_EVERY != self.total_exports // NAG_EVERY:
                QTimer.singleShot(300, lambda: NagDialog(self.total_exports, self).show())


def main():
    app = QApplication(sys.argv)
    if os.path.exists(APP_ICON):
        app.setWindowIcon(QIcon(APP_ICON))
    win = PosterizerApp()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
