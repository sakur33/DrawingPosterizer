"""
Widget de recorte: muestra la imagen y un rectángulo editable (mover,
redimensionar por esquinas/bordes, o dibujar uno nuevo). Admite proporción fija.
Todas las coordenadas de selección están en píxeles de la IMAGEN.
"""
from PyQt5.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt5.QtGui import QPainter, QColor, QPen, QPixmap, QImage, QBrush
from PyQt5.QtWidgets import QWidget

HANDLE = 9      # radio de captura de un tirador, en píxeles de pantalla
MIN_SIZE = 16   # tamaño mínimo de selección, en píxeles de imagen


class CropWidget(QWidget):
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self._pix = None
        self._iw = self._ih = 0
        self.sel = QRectF()
        self.ratio = None          # ancho/alto, o None si es libre
        self._drag = None          # (modo, dato)
        self._press_sel = QRectF()
        self._press_pos = QPointF()

    # -- API pública --------------------------------------------------------
    def set_image(self, qimage: QImage):
        self._pix = QPixmap.fromImage(qimage)
        self._iw, self._ih = qimage.width(), qimage.height()
        self.select_all()

    def select_all(self):
        self.sel = QRectF(0, 0, self._iw, self._ih)
        if self.ratio:
            self.set_ratio(self.ratio)
        else:
            self._emit()

    def set_ratio(self, ratio):
        """Fija la proporción (ancho/alto) o None para libre. Reajusta el
        recuadro actual al mayor rectángulo con esa proporción dentro de él."""
        self.ratio = ratio
        if ratio and self._iw:
            r = self.sel
            w, h = r.width(), r.height()
            if w / h > ratio:
                w = h * ratio
            else:
                h = w / ratio
            c = r.center()
            self.sel = QRectF(c.x() - w / 2, c.y() - h / 2, w, h)
            self._clamp_move()
        self._emit()

    def selection(self):
        """(izq, arr, der, abajo) en enteros, dentro de la imagen."""
        r = self.sel
        l = max(0, round(r.left()))
        t = max(0, round(r.top()))
        rr = min(self._iw, round(r.right()))
        b = min(self._ih, round(r.bottom()))
        return l, t, rr, b

    # -- Geometría ----------------------------------------------------------
    def _layout(self):
        m = 14
        aw, ah = self.width() - 2 * m, self.height() - 2 * m
        s = min(aw / self._iw, ah / self._ih)
        w, h = self._iw * s, self._ih * s
        return s, (self.width() - w) / 2, (self.height() - h) / 2

    def _to_screen(self, p):
        s, ox, oy = self._layout()
        return QPointF(ox + p.x() * s, oy + p.y() * s)

    def _to_image(self, p):
        s, ox, oy = self._layout()
        x = min(max((p.x() - ox) / s, 0), self._iw)
        y = min(max((p.y() - oy) / s, 0), self._ih)
        return QPointF(x, y)

    def _screen_rect(self):
        a = self._to_screen(self.sel.topLeft())
        b = self._to_screen(self.sel.bottomRight())
        return QRectF(a, b)

    def _handles(self):
        r = self._screen_rect()
        cx, cy = r.center().x(), r.center().y()
        return {
            "tl": QPointF(r.left(), r.top()), "t": QPointF(cx, r.top()),
            "tr": QPointF(r.right(), r.top()), "r": QPointF(r.right(), cy),
            "br": QPointF(r.right(), r.bottom()), "b": QPointF(cx, r.bottom()),
            "bl": QPointF(r.left(), r.bottom()), "l": QPointF(r.left(), cy),
        }

    def _hit(self, pos):
        for name, pt in self._handles().items():
            if abs(pos.x() - pt.x()) <= HANDLE and abs(pos.y() - pt.y()) <= HANDLE:
                return name
        if self._screen_rect().contains(pos):
            return "move"
        return None

    # -- Dibujo -------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#1e1e1e"))
        if not self._pix:
            return
        s, ox, oy = self._layout()
        target = QRectF(ox, oy, self._iw * s, self._ih * s)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawPixmap(target, self._pix, QRectF(self._pix.rect()))

        sel = self._screen_rect()
        shade = QColor(0, 0, 0, 150)
        p.fillRect(QRectF(target.left(), target.top(), target.width(), sel.top() - target.top()), shade)
        p.fillRect(QRectF(target.left(), sel.bottom(), target.width(), target.bottom() - sel.bottom()), shade)
        p.fillRect(QRectF(target.left(), sel.top(), sel.left() - target.left(), sel.height()), shade)
        p.fillRect(QRectF(sel.right(), sel.top(), target.right() - sel.right(), sel.height()), shade)

        # Regla de tercios
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))
        for i in (1, 2):
            x = sel.left() + sel.width() * i / 3
            y = sel.top() + sel.height() * i / 3
            p.drawLine(QPointF(x, sel.top()), QPointF(x, sel.bottom()))
            p.drawLine(QPointF(sel.left(), y), QPointF(sel.right(), y))

        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(sel)

        p.setBrush(QBrush(QColor("#ffffff")))
        p.setPen(QPen(QColor("#2b6cb0"), 1))
        for pt in self._handles().values():
            p.drawRect(QRectF(pt.x() - 5, pt.y() - 5, 10, 10))

        # Dimensiones
        l, t, r, b = self.selection()
        label = f"{r - l} × {b - t} px"
        p.setPen(QColor("#ffffff"))
        p.fillRect(QRectF(sel.left(), sel.bottom() + 4, 8 + 7 * len(label), 20),
                   QColor(0, 0, 0, 170))
        p.drawText(QPointF(sel.left() + 4, sel.bottom() + 19), label)

    # -- Ratón --------------------------------------------------------------
    def mouseMoveEvent(self, e):
        if not self._pix:
            return
        if self._drag is None:
            cursors = {
                "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
                "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
                "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
                "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                "move": Qt.SizeAllCursor,
            }
            self.setCursor(cursors.get(self._hit(e.pos()), Qt.CrossCursor))
            return
        mode, anchor = self._drag
        pt = self._to_image(e.pos())
        if mode == "move":
            d = pt - self._to_image(self._press_pos)
            r = QRectF(self._press_sel)
            r.translate(d)
            self.sel = r
            self._clamp_move()
        elif mode == "corner":          # anchor: punto fijo opuesto
            self.sel = self._corner_rect(anchor, pt)
        else:                           # borde: anchor = nombre del tirador
            self.sel = self._edge_rect(anchor, pt)
        self._emit()

    def mousePressEvent(self, e):
        if not self._pix or e.button() != Qt.LeftButton:
            return
        self._press_pos = e.pos()
        self._press_sel = QRectF(self.sel)
        hit = self._hit(e.pos())
        r = self.sel
        if hit == "move":
            self._drag = ("move", None)
        elif hit in ("tl", "tr", "bl", "br"):
            ax = r.right() if "l" in hit else r.left()
            ay = r.bottom() if "t" in hit else r.top()
            self._drag = ("corner", QPointF(ax, ay))
        elif hit:
            self._drag = ("edge", hit)
        else:   # rectángulo nuevo desde el punto pulsado
            self._drag = ("corner", self._to_image(e.pos()))
            self.sel = QRectF(self._drag[1], self._drag[1])

    def mouseReleaseEvent(self, _):
        self._drag = None

    # -- Cálculo de rectángulos --------------------------------------------
    def _clamp_move(self):
        r = self.sel
        x = min(max(r.left(), 0), self._iw - r.width())
        y = min(max(r.top(), 0), self._ih - r.height())
        self.sel = QRectF(x, y, r.width(), r.height())

    def _corner_rect(self, a, pt):
        """Rectángulo entre el punto fijo `a` y el ratón `pt`."""
        sx = 1 if pt.x() >= a.x() else -1
        sy = 1 if pt.y() >= a.y() else -1
        w, h = abs(pt.x() - a.x()), abs(pt.y() - a.y())
        if self.ratio:
            if w / max(h, 1e-6) > self.ratio:
                h = w / self.ratio
            else:
                w = h * self.ratio
            room_w = (self._iw - a.x()) if sx > 0 else a.x()
            room_h = (self._ih - a.y()) if sy > 0 else a.y()
            k = min(1.0, room_w / max(w, 1e-6), room_h / max(h, 1e-6))
            w, h = w * k, h * k
        x0 = a.x() if sx > 0 else a.x() - w
        y0 = a.y() if sy > 0 else a.y() - h
        return QRectF(x0, y0, max(w, 1), max(h, 1))

    def _edge_rect(self, edge, pt):
        r = self._press_sel
        l, t, rr, b = r.left(), r.top(), r.right(), r.bottom()
        if edge == "l":
            l = min(pt.x(), rr - MIN_SIZE)
        elif edge == "r":
            rr = max(pt.x(), l + MIN_SIZE)
        elif edge == "t":
            t = min(pt.y(), b - MIN_SIZE)
        elif edge == "b":
            b = max(pt.y(), t + MIN_SIZE)
        if self.ratio:
            cx, cy = r.center().x(), r.center().y()
            if edge in ("l", "r"):
                w = rr - l
                h = w / self.ratio
                max_h = 2 * min(cy, self._ih - cy)
                if h > max_h:
                    h, w = max_h, max_h * self.ratio
                    l, rr = (rr - w, rr) if edge == "l" else (l, l + w)
                t, b = cy - h / 2, cy + h / 2
            else:
                h = b - t
                w = h * self.ratio
                max_w = 2 * min(cx, self._iw - cx)
                if w > max_w:
                    w, h = max_w, max_w / self.ratio
                    t, b = (b - h, b) if edge == "t" else (t, t + h)
                l, rr = cx - w / 2, cx + w / 2
        return QRectF(l, t, rr - l, b - t)

    def _emit(self):
        self.update()
        self.changed.emit()
