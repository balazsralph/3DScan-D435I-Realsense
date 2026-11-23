"""Panneau d'aperçu 3D : source (direct / modèle), mode de rendu, vues, capture d'écran."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme
from .viewer3d import RENDER_MODES, Viewer3D
from .widgets import SegmentedControl


class ViewerPanel(QWidget):
    source_changed = Signal(str)  # "direct" ou "modele"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        eyebrow = QLabel("APERÇU 3D")
        eyebrow.setObjectName("Eyebrow")
        self.title = QLabel("Nuage en direct")
        self.title.setObjectName("H2")
        titles.addWidget(eyebrow)
        titles.addWidget(self.title)
        head.addLayout(titles)
        head.addStretch(1)
        self.source = SegmentedControl([("direct", "Direct"), ("modele", "Modèle")])
        self.source.changed.connect(self.source_changed)
        head.addWidget(self.source)
        root.addLayout(head)

        frame = QWidget()
        frame.setStyleSheet(f"background: {theme.BG}; border-radius: {theme.RADIUS_M}px;")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        self.viewer = Viewer3D()
        fl.addWidget(self.viewer)
        root.addWidget(frame, 1)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.render_mode = SegmentedControl(list(RENDER_MODES))
        self.render_mode.changed.connect(self.viewer.set_render_mode)
        tools.addWidget(self.render_mode)
        self.bounds = QCheckBox("Cotes")
        self.bounds.setToolTip("Affiche les dimensions du modèle sur ses axes")
        self.bounds.toggled.connect(self.viewer.set_bounds_visible)
        tools.addWidget(self.bounds)
        tools.addStretch(1)
        for label, tip, slot in (("Iso", "Vue isométrique", self.viewer.reset_view),
                                 ("Dessus", "Vue de dessus", self.viewer.view_top),
                                 ("Capture", "Enregistrer une image de la vue 3D", self._screenshot)):
            button = QPushButton(label)
            button.setObjectName("Ghost")
            button.setToolTip(tip)
            button.clicked.connect(slot)
            tools.addWidget(button)
        root.addLayout(tools)

        self.info = QLabel("")
        self.info.setObjectName("Faint")
        root.addWidget(self.info)
        self._model_dir: Path | None = None

    def set_title(self, text: str, info: str = "") -> None:
        self.title.setText(text)
        self.info.setText(info)

    def set_source(self, source: str) -> None:
        self.source.set_value(source)

    def set_model_dir(self, folder: Path | None) -> None:
        self._model_dir = folder

    def _screenshot(self) -> None:
        default = (self._model_dir or Path.home()) / f"apercu_{datetime.now():%Y%m%d_%H%M%S}.png"
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer la vue 3D", str(default), "Images PNG (*.png)")
        if path:
            self.viewer.screenshot(Path(path))
