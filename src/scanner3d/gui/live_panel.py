"""Panneau caméra en direct : image avec repères de cadrage, guidage de distance, enregistrement."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from ..depth import format_distance
from ..profiles import ScanProfile
from ..reconstruction.coverage import GOOD, MISSING, PARTIAL, WEAK, CoverageMap
from . import theme
from .widgets import Chip, ImageView, RecordButton, SegmentedControl, StatTile
from .workers import LiveStats

HINT_COLORS = {"ok": theme.GREEN, "near": theme.RED, "far": theme.RED, "none": theme.AMBER}
COVERAGE_COLORS = {GOOD: theme.GREEN, PARTIAL: theme.AMBER}


class LiveView(ImageView):
    """Image de la caméra + réticule, pastille de distance et voyant REC dessinés par Qt (accents compris)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Ouverture de la caméra...", parent)
        self.stats: LiveStats | None = None
        self.tracking: tuple[str, str] | None = None  # (texte, niveau)
        self.coverage: CoverageMap | None = None
        self.show_coverage = True

    def paint_overlay(self, p: QPainter, r: QRectF) -> None:
        c = r.center()
        s = self.stats
        accent = QColor(HINT_COLORS.get(s.hint_level, theme.MUTED)) if s else QColor(theme.MUTED)
        # réticule et cercle de cadrage
        p.setPen(QPen(theme.qcolor("#FFFFFF", 170), 1.5))
        p.drawLine(QPointF(c.x() - 14, c.y()), QPointF(c.x() - 5, c.y()))
        p.drawLine(QPointF(c.x() + 5, c.y()), QPointF(c.x() + 14, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 14), QPointF(c.x(), c.y() - 5))
        p.drawLine(QPointF(c.x(), c.y() + 5), QPointF(c.x(), c.y() + 14))
        p.setPen(QPen(theme.qcolor(accent.name(), 150), 1.2, Qt.PenStyle.DashLine))
        radius = r.height() * 0.28
        p.drawEllipse(c, radius, radius)
        self._corner_marks(p, r.adjusted(14, 14, -14, -14))
        if s is None:
            return
        covering = self.show_coverage and s.recording and self.coverage is not None
        if covering:
            self._paint_coverage(p, r, self.coverage)
        self._pill(p, QPointF(r.left() + 14, r.top() + 14), s.hint_text, accent)
        if s.recording:
            elapsed = int(s.elapsed_s)
            text = f"REC  {elapsed // 60:02d}:{elapsed % 60:02d}  ·  {s.frames} img"
            self._pill(p, QPointF(r.right() - 14, r.top() + 14), text, QColor(theme.RED), align_right=True)
        if covering:
            self._coverage_legend(p, QPointF(r.right() - 14, r.top() + 50), self.coverage)
        if self.tracking:
            text, level = self.tracking
            color = QColor({"ok": theme.GREEN, "warn": theme.AMBER, "err": theme.RED}.get(level, theme.CYAN))
            self._pill(p, QPointF(r.left() + 14, r.bottom() - 44), text, color)
        footer = f"{s.fps:4.1f} img/s   ·   profondeur mesurée {s.valid * 100:3.0f} %"
        p.setFont(theme.font("mono", 8.5))
        p.setPen(theme.qcolor("#FFFFFF", 190))
        p.drawText(QRectF(r.left() + 16, r.bottom() - 26, r.width() - 32, 18),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, footer)

    @staticmethod
    def _paint_coverage(p: QPainter, r: QRectF, coverage: CoverageMap) -> None:
        """Grille de marqueurs : triangles rouges = à scanner, points ambre = partiel, points verts = bien vu."""
        rows, cols = coverage.grid.shape
        cw, ch = r.width() / cols, r.height() / rows
        size = max(3.0, min(cw, ch) * 0.32)
        p.setPen(Qt.PenStyle.NoPen)
        for level, alpha, scale in ((GOOD, 120, 0.30), (PARTIAL, 210, 0.45)):
            p.setBrush(theme.qcolor(COVERAGE_COLORS[level], alpha))
            for row, col in zip(*np.nonzero(coverage.grid == level), strict=True):
                center = QPointF(r.left() + (col + 0.5) * cw, r.top() + (row + 0.5) * ch)
                p.drawEllipse(center, size * scale * 2, size * scale * 2)
        p.setPen(QPen(theme.qcolor("#1A0507", 200), 1))
        p.setBrush(theme.qcolor(theme.RED, 235))
        for row, col in zip(*np.nonzero((coverage.grid == WEAK) | (coverage.grid == MISSING)), strict=True):
            x, y = r.left() + (col + 0.5) * cw, r.top() + (row + 0.5) * ch
            p.drawPolygon(QPolygonF([QPointF(x, y - size), QPointF(x + size * 0.9, y + size * 0.6),
                                     QPointF(x - size * 0.9, y + size * 0.6)]))

    @staticmethod
    def _coverage_legend(p: QPainter, anchor: QPointF, coverage: CoverageMap) -> None:
        f = theme.font("body", 9, QFont.Weight.DemiBold)
        p.setFont(f)
        metrics = QFontMetrics(f)
        items = [(theme.RED, "triangle", f"à scanner {coverage.to_scan_ratio * 100:.0f} %"),
                 (COVERAGE_COLORS[PARTIAL], "dot", "partiel"),
                 (COVERAGE_COLORS[GOOD], "dot", f"bien vu {coverage.good_ratio * 100:.0f} %")]
        width = sum(metrics.horizontalAdvance(text) + 30 for *_, text in items) + 14
        rect = QRectF(anchor.x() - width, anchor.y(), width, 28)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        p.fillPath(path, theme.qcolor("#0B0E12", 205))
        x = rect.left() + 14
        for color, shape, text in items:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            cy = rect.center().y()
            if shape == "triangle":
                p.drawPolygon(QPolygonF([QPointF(x + 5, cy - 5), QPointF(x + 10, cy + 4), QPointF(x, cy + 4)]))
            else:
                p.drawEllipse(QPointF(x + 5, cy), 4, 4)
            p.setPen(theme.qcolor(theme.TEXT))
            p.drawText(QRectF(x + 16, rect.top(), metrics.horizontalAdvance(text) + 4, rect.height()),
                       Qt.AlignmentFlag.AlignVCenter, text)
            x += metrics.horizontalAdvance(text) + 30
        p.setBrush(Qt.BrushStyle.NoBrush)

    @staticmethod
    def _corner_marks(p: QPainter, r: QRectF, length: float = 18) -> None:
        p.setPen(QPen(theme.qcolor("#FFFFFF", 120), 2))
        for corner, dx, dy in ((r.topLeft(), 1, 1), (r.topRight(), -1, 1), (r.bottomLeft(), 1, -1),
                               (r.bottomRight(), -1, -1)):
            p.drawLine(corner, QPointF(corner.x() + dx * length, corner.y()))
            p.drawLine(corner, QPointF(corner.x(), corner.y() + dy * length))

    @staticmethod
    def _pill(p: QPainter, anchor: QPointF, text: str, color: QColor, align_right: bool = False) -> None:
        f = theme.font("body", 9.5, QFont.Weight.DemiBold)
        p.setFont(f)
        w = QFontMetrics(f).horizontalAdvance(text) + 34
        rect = QRectF(anchor.x() - w if align_right else anchor.x(), anchor.y(), w, 28)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        p.fillPath(path, theme.qcolor("#0B0E12", 205))
        p.setPen(QPen(theme.qcolor(color.name(), 140), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(rect.left() + 14, rect.center().y()), 4, 4)
        p.setPen(theme.qcolor(theme.TEXT))
        p.drawText(rect.adjusted(24, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, text)


class DepthView(ImageView):
    """Profondeur colorée avec sa légende (proche -> loin) et la plage gardée."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self.near_m, self.far_m = 0.2, 1.0

    def set_range(self, near_m: float, far_m: float) -> None:
        self.near_m, self.far_m = near_m, far_m
        self.update()

    def paint_overlay(self, p: QPainter, r: QRectF) -> None:
        bar = QRectF(r.left() + 16, r.bottom() - 34, min(260.0, r.width() * 0.4), 8)
        back = QPainterPath()
        back.addRoundedRect(bar.adjusted(-10, -24, 10, 10), 10, 10)
        p.fillPath(back, theme.qcolor("#0B0E12", 200))
        gradient = QLinearGradient(bar.topLeft(), bar.topRight())
        for stop, color in ((0.0, "#7A0403"), (0.25, "#F66B19"), (0.5, "#A2FC3C"), (0.75, "#28BCEB"),
                            (1.0, "#30123B")):
            gradient.setColorAt(stop, QColor(color))
        path = QPainterPath()
        path.addRoundedRect(bar, 4, 4)
        p.fillPath(path, gradient)
        p.setFont(theme.font("mono", 8))
        p.setPen(theme.qcolor(theme.TEXT, 220))
        p.drawText(QRectF(bar.left(), bar.top() - 20, bar.width(), 16), Qt.AlignmentFlag.AlignLeft,
                   format_distance(self.near_m))
        p.drawText(QRectF(bar.left(), bar.top() - 20, bar.width(), 16), Qt.AlignmentFlag.AlignRight,
                   format_distance(self.far_m))
        p.drawText(QRectF(bar.left(), bar.top() - 20, bar.width(), 16), Qt.AlignmentFlag.AlignHCenter,
                   "profondeur")


class LivePanel(QWidget):
    record_toggled = Signal(bool)
    view_mode_changed = Signal(str)
    depth_max_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._profile: ScanProfile | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        eyebrow = QLabel("CAMÉRA EN DIRECT")
        eyebrow.setObjectName("Eyebrow")
        self.title = QLabel("—")
        self.title.setObjectName("H2")
        titles.addWidget(eyebrow)
        titles.addWidget(self.title)
        head.addLayout(titles)
        head.addStretch(1)
        self.camera_chip = Chip("Connexion...", "info")
        head.addWidget(self.camera_chip)
        root.addLayout(head)

        self.view = LiveView()
        root.addWidget(self.view, 3)
        self.depth_view = DepthView()
        root.addWidget(self.depth_view, 2)

        bar = QHBoxLayout()
        bar.setSpacing(18)
        self.record = RecordButton()
        self.record.toggled.connect(self.record_toggled)
        bar.addWidget(self.record)
        self.timer_tile = StatTile("Durée", "00:00")
        self.frames_tile = StatTile("Images", "0")
        bar.addWidget(self.timer_tile)
        bar.addWidget(self.frames_tile)
        bar.addStretch(1)
        controls = QVBoxLayout()
        controls.setSpacing(6)
        self.view_mode = SegmentedControl([("mixte", "Couleur + profondeur"), ("couleur", "Couleur seule")])
        self.view_mode.changed.connect(self.view_mode_changed)
        controls.addWidget(self.view_mode)
        depth_row = QHBoxLayout()
        self.depth_label = QLabel("Fond coupé à —")
        self.depth_label.setObjectName("Muted")
        self.depth_slider = QSlider(Qt.Orientation.Horizontal)
        self.depth_slider.setToolTip("Profondeur maximale gardée : coupe le fond (utile avec un plateau tournant)")
        self.depth_slider.valueChanged.connect(self._on_depth)
        depth_row.addWidget(self.depth_label)
        depth_row.addWidget(self.depth_slider, 1)
        controls.addLayout(depth_row)
        bar.addLayout(controls, 2)

        options = QHBoxLayout()
        options.setSpacing(14)
        self.coverage_box = QCheckBox("Couverture en direct")
        self.coverage_box.setChecked(True)
        self.coverage_box.setToolTip("Pendant l'enregistrement : triangles rouges = à scanner, "
                                     "points verts = bien vu.\nLe nuage 3D est alors coloré de la même façon.")
        self.coverage_box.toggled.connect(self._on_coverage_toggled)
        self.auto_build = QCheckBox("Construire à la fin")
        self.auto_build.setChecked(True)
        options.addWidget(self.coverage_box)
        options.addWidget(self.auto_build)
        options.addStretch(1)
        controls.addLayout(options)
        self.tip = QLabel("")
        self.tip.setObjectName("Faint")
        self.tip.setWordWrap(True)
        root.addLayout(bar)
        root.addWidget(self.tip)

    def set_profile(self, profile: ScanProfile) -> None:
        self._profile = profile
        self.title.setText(f"{profile.title}  ·  {profile.subtitle}")
        lo, hi = int(profile.depth_min_m * 100) + 5, int(profile.depth_max_m * 100)
        self.depth_slider.blockSignals(True)
        self.depth_slider.setRange(lo, hi)
        self.depth_slider.setValue(hi)
        self.depth_slider.blockSignals(False)
        self._update_depth_label(hi)
        self.tip.setText("Conseil : " + "  ·  ".join(profile.tips[:2]))
        self.view.stats = None
        self.view.tracking = None
        self.view.set_image(None)
        self.view.set_placeholder("Ouverture de la caméra...")
        self.depth_view.set_image(None)
        self.depth_view.set_range(profile.depth_min_m, profile.depth_max_m)

    def _on_depth(self, value_cm: int) -> None:
        self._update_depth_label(value_cm)
        self.depth_max_changed.emit(value_cm / 100.0)

    def _update_depth_label(self, value_cm: int) -> None:
        self.depth_label.setText(f"Fond coupé à {format_distance(value_cm / 100)}")
        if self._profile is not None:
            self.depth_view.set_range(self._profile.depth_min_m, value_cm / 100)

    def show_frames(self, main: QImage, depth: QImage) -> None:
        self.view.set_image(main)
        self.depth_view.set_image(depth)

    def clear_frames(self, message: str) -> None:
        self.view.stats = None
        self.view.set_image(None)
        self.view.set_placeholder(message)
        self.depth_view.set_image(None)

    def set_stats(self, stats: LiveStats) -> None:
        self.view.stats = stats
        elapsed = int(stats.elapsed_s)
        self.timer_tile.set_value(f"{elapsed // 60:02d}:{elapsed % 60:02d}", theme.RED if stats.recording else None)
        dropped = f" (−{stats.dropped})" if stats.dropped else ""
        self.frames_tile.set_value(f"{stats.frames}{dropped}")

    def _on_coverage_toggled(self, on: bool) -> None:
        self.view.show_coverage = on
        self.view.update()

    def set_coverage(self, coverage: CoverageMap | None) -> None:
        self.view.coverage = coverage
        self.view.update()

    def set_recording(self, recording: bool) -> None:
        self.record.blockSignals(True)
        self.record.setChecked(recording)
        self.record.blockSignals(False)
        self.depth_slider.setEnabled(not recording)
        if not recording:
            self.view.tracking = None
            self.view.coverage = None
