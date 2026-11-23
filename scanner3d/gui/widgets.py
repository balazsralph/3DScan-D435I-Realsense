"""Composants d'interface réutilisables : contrôle segmenté, pastilles, bouton d'enregistrement, vue d'image."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme

CHIP_STYLES = {
    "ok": (theme.GREEN, theme.GREEN_DIM),
    "warn": (theme.AMBER, theme.AMBER_DIM),
    "err": (theme.RED, theme.RED_DIM),
    "info": (theme.CYAN, theme.CYAN_DIM),
    "neutral": (theme.MUTED, theme.SURFACE_2),
}


def bgr_to_qimage(image: np.ndarray) -> QImage:
    """Image OpenCV BGR (ou niveaux de gris) -> QImage indépendante du tableau numpy."""
    if image.ndim == 2:
        h, w = image.shape
        return QImage(np.ascontiguousarray(image).data, w, h, w, QImage.Format.Format_Grayscale8).copy()
    h, w, _ = image.shape
    rgb = np.ascontiguousarray(image[:, :, ::-1])
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class SegmentedControl(QWidget):
    """Choix exclusif compact (ex. Rapide / Équilibré / Précis)."""

    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], current: str | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._buttons: dict[str, QToolButton] = {}
        for key, label in options:
            button = QToolButton(self)
            button.setObjectName("Segment")
            button.setText(label)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(button)
            self._buttons[key] = button
            layout.addWidget(button)
        self.set_value(current or options[0][0])

    def value(self) -> str:
        return next(k for k, b in self._buttons.items() if b.isChecked())

    def set_value(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)


class Chip(QLabel):
    """Pastille d'état colorée selon son niveau (ok, warn, err, info, neutral)."""

    def __init__(self, text: str = "", level: str = "neutral", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.set(text, level)

    def set(self, text: str, level: str = "neutral") -> None:
        fg, bg = CHIP_STYLES.get(level, CHIP_STYLES["neutral"])
        self.setText(f"●  {text}" if level != "neutral" else text)
        self.setStyleSheet(f"QLabel {{ color: {fg}; background: {bg}; border-radius: 11px; padding: 3px 10px;"
                           f" font-weight: 600; font-size: 9pt; }}")
        self.setVisible(bool(text))


class StatTile(QWidget):
    """Valeur chiffrée avec son libellé (télémétrie)."""

    def __init__(self, label: str, value: str = "—", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self._label = QLabel(label.upper())
        self._label.setObjectName("Eyebrow")
        self._value = QLabel(value)
        self._value.setObjectName("Stat")
        layout.addWidget(self._label)
        layout.addWidget(self._value)

    def set_value(self, value: str, color: str | None = None) -> None:
        self._value.setText(value)
        self._value.setStyleSheet(f"color: {color};" if color else "")


class RecordButton(QAbstractButton):
    """Gros bouton rond : point ambre au repos, carré rouge pulsant pendant l'enregistrement."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(64, 64)
        self.setToolTip("Lancer / terminer l'enregistrement (Espace)")
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.toggled.connect(lambda on: self._timer.start(40) if on else self._timer.stop())

    def sizeHint(self) -> QSize:
        return QSize(64, 64)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.08) % (2 * math.pi)
        self.update()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        enabled = self.isEnabled()
        color = QColor(theme.RED if self.isChecked() else theme.AMBER)
        if not enabled:
            color = QColor(theme.FAINT)
        if self.isChecked():
            glow = theme.qcolor(theme.RED, int(60 + 50 * math.sin(self._phase)))
            p.setPen(QPen(glow, 6))
            p.drawEllipse(rect.adjusted(-1, -1, 1, 1))
        ring = QPen(color, 3 if self.underMouse() else 2)
        p.setPen(ring)
        p.setBrush(theme.qcolor(theme.SURFACE_2))
        p.drawEllipse(rect)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        center = rect.center()
        if self.isChecked():
            side = rect.width() * 0.32
            p.drawRoundedRect(QRectF(center.x() - side / 2, center.y() - side / 2, side, side), 4, 4)
        else:
            radius = rect.width() * (0.26 if self.underMouse() else 0.23)
            p.drawEllipse(center, radius, radius)


class ImageView(QWidget):
    """Affiche une image en conservant ses proportions, coins arrondis, texte d'attente si vide."""

    def __init__(self, placeholder: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._placeholder = placeholder
        self.setMinimumSize(160, 90)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_image(self, image: QImage | None) -> None:
        self._image = image
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        if self._image is None:
            self.update()

    def image_rect(self) -> QRectF:
        if self._image is None or self._image.isNull():
            return QRectF(self.rect())
        area = QRectF(self.rect())
        scale = min(area.width() / self._image.width(), area.height() / self._image.height())
        w, h = self._image.width() * scale, self._image.height() * scale
        return QRectF(area.x() + (area.width() - w) / 2, area.y() + (area.height() - h) / 2, w, h)

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        target = self.image_rect()
        path = QPainterPath()
        path.addRoundedRect(target, theme.RADIUS_M, theme.RADIUS_M)
        if self._image is None or self._image.isNull():
            p.fillPath(path, theme.qcolor(theme.BG))
            pen = QPen(theme.qcolor(theme.LINE), 1, Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawPath(path)
            p.setPen(theme.qcolor(theme.MUTED))
            p.setFont(theme.font("body", 10.5))
            p.drawText(target, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._placeholder)
            return
        p.setClipPath(path)
        p.drawImage(target, self._image)
        self.paint_overlay(p, target)

    def paint_overlay(self, painter: QPainter, target: QRectF) -> None:
        """À surcharger : dessin par-dessus l'image (réticule, repères...)."""


def paint_object_icon(p: QPainter, rect: QRectF, color: QColor) -> None:
    """Cube isométrique entouré d'une orbite (scan autour d'un objet)."""
    c, s = rect.center(), min(rect.width(), rect.height()) * 0.22
    top = [QPointF(c.x(), c.y() - s), QPointF(c.x() + s, c.y() - s / 2), QPointF(c.x(), c.y()),
           QPointF(c.x() - s, c.y() - s / 2)]
    p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(theme.qcolor(color.name(), 40))
    p.drawPolygon(top)
    p.setBrush(theme.qcolor(color.name(), 20))
    p.drawPolygon([top[3], top[2], QPointF(c.x(), c.y() + s), QPointF(c.x() - s, c.y() + s / 2)])
    p.setBrush(theme.qcolor(color.name(), 70))
    p.drawPolygon([top[2], top[1], QPointF(c.x() + s, c.y() + s / 2), QPointF(c.x(), c.y() + s)])
    p.setBrush(Qt.BrushStyle.NoBrush)
    orbit = QRectF(c.x() - s * 2.0, c.y() - s * 0.55, s * 4.0, s * 1.6)
    p.setPen(QPen(color, 1.5, Qt.PenStyle.DashLine))
    p.drawArc(orbit, 200 * 16, 140 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(orbit.center().x() + orbit.width() / 2 * math.cos(math.radians(-20)),
                          orbit.center().y() - orbit.height() / 2 * math.sin(math.radians(-20))), 3.5, 3.5)


def paint_room_icon(p: QPainter, rect: QRectF, color: QColor) -> None:
    """Pièce en perspective (sol, deux murs) avec la caméra au centre."""
    c, s = rect.center(), min(rect.width(), rect.height()) * 0.30
    p.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    back_left, back_right = QPointF(c.x() - s * 0.55, c.y() - s * 0.75), QPointF(c.x() + s * 0.55, c.y() - s * 0.75)
    floor_left, floor_right = QPointF(c.x() - s * 0.55, c.y() + s * 0.25), QPointF(c.x() + s * 0.55, c.y() + s * 0.25)
    p.setBrush(theme.qcolor(color.name(), 30))
    p.drawPolygon([back_left, back_right, floor_right, floor_left])
    p.setBrush(theme.qcolor(color.name(), 55))
    p.drawPolygon([floor_left, floor_right, QPointF(c.x() + s * 1.2, c.y() + s), QPointF(c.x() - s * 1.2, c.y() + s)])
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(back_left, QPointF(c.x() - s * 1.2, c.y() - s * 1.2))
    p.drawLine(back_right, QPointF(c.x() + s * 1.2, c.y() - s * 1.2))
    p.setPen(QPen(color, 1.5, Qt.PenStyle.DashLine))
    for angle in (-35, 0, 35):
        rad = math.radians(angle - 90)
        p.drawLine(QPointF(c.x(), c.y() + s * 0.55),
                   QPointF(c.x() + s * 0.9 * math.cos(rad), c.y() + s * 0.55 + s * 0.9 * math.sin(rad)))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(c.x(), c.y() + s * 0.55), 4, 4)


def hline() -> QWidget:
    line = QWidget()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {theme.LINE};")
    return line
