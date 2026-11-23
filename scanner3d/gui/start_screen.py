"""Écran d'accueil : choix du type de scan (objet ou salle entière)."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..camera import CameraInfo
from ..profiles import PROFILES, ScanProfile
from . import theme
from .widgets import Chip, SegmentedControl, paint_object_icon, paint_room_icon

IconPainter = Callable[[QPainter, QRectF, QColor], None]


class ModeCard(QWidget):
    """Grande carte cliquable présentant un mode de scan."""

    chosen = Signal(str)

    def __init__(self, accent: str, icon: IconPainter, title: str, pitch: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accent, self._icon, self._hover = accent, icon, False
        self.setMinimumSize(380, 430)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 150, 28, 24)
        layout.setSpacing(10)
        heading = QLabel(title)
        heading.setStyleSheet(f"font-family: '{theme.Fonts.display}'; font-size: 22pt; font-weight: 800;")
        sub = QLabel(pitch)
        sub.setObjectName("Muted")
        sub.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(sub)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        layout.addLayout(self.body)
        layout.addStretch(1)
        self.specs = QGridLayout()
        self.specs.setHorizontalSpacing(16)
        self.specs.setVerticalSpacing(6)
        layout.addLayout(self.specs)
        self.button = QPushButton("Commencer  →")
        self.button.setObjectName("Primary")
        self.button.setCursor(Qt.CursorShape.PointingHandCursor)
        if accent != theme.AMBER:
            self.button.setStyleSheet(f"QPushButton#Primary {{ background: {accent}; color: #120B24; }}"
                                      f"QPushButton#Primary:hover {{ background: #C4B0FF; }}")
        layout.addSpacing(8)
        layout.addWidget(self.button)

    def set_specs(self, rows: list[tuple[str, str]]) -> None:
        while self.specs.count():
            item = self.specs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for row, (label, value) in enumerate(rows):
            key = QLabel(label.upper())
            key.setObjectName("Eyebrow")
            val = QLabel(value)
            val.setObjectName("Mono")
            self.specs.addWidget(key, row, 0)
            self.specs.addWidget(val, row, 1, alignment=Qt.AlignmentFlag.AlignRight)

    def enterEvent(self, event: object) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event: object) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event: object) -> None:
        self.button.click()

    def paintEvent(self, _event: object) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, theme.RADIUS_L, theme.RADIUS_L)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, theme.qcolor(self._accent, 38 if self._hover else 22))
        gradient.setColorAt(0.45, theme.qcolor(theme.SURFACE))
        gradient.setColorAt(1.0, theme.qcolor(theme.SURFACE))
        p.fillPath(path, gradient)
        p.setPen(QPen(theme.qcolor(self._accent, 200) if self._hover else theme.qcolor(theme.LINE), 1.5))
        p.drawPath(path)
        self._icon(p, QRectF(rect.x() + 10, rect.y() + 14, 150, 130), QColor(self._accent))


class StartScreen(QWidget):
    profile_chosen = Signal(str)
    browse_only = Signal()
    refresh_camera = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Root")
        root = QVBoxLayout(self)
        root.setContentsMargins(48, 32, 48, 32)
        root.setSpacing(0)

        top = QHBoxLayout()
        brand = QLabel("SCANNER 3D  ·  INTEL REALSENSE D435i")
        brand.setObjectName("Eyebrow")
        top.addWidget(brand)
        top.addStretch(1)
        self.camera_chip = Chip()
        refresh = QPushButton("Détecter")
        refresh.setObjectName("Ghost")
        refresh.clicked.connect(self.refresh_camera)
        top.addWidget(self.camera_chip)
        top.addWidget(refresh)
        root.addLayout(top)
        root.addStretch(1)

        title = QLabel("Que veux-tu scanner ?")
        title.setObjectName("H1")
        subtitle = QLabel("Chaque mode règle la caméra (preset, portée, filtres) et la reconstruction "
                          "(finesse, suivi, nettoyage) pour son sujet.")
        subtitle.setObjectName("Muted")
        root.addWidget(title, alignment=Qt.AlignmentFlag.AlignHCenter)
        root.addSpacing(6)
        root.addWidget(subtitle, alignment=Qt.AlignmentFlag.AlignHCenter)
        root.addSpacing(32)

        cards = QHBoxLayout()
        cards.setSpacing(24)
        cards.addStretch(1)
        self.object_card = ModeCard(theme.AMBER, paint_object_icon, "Objet",
                                    "Tourne autour d'un objet posé sur une surface plane : il est isolé, posé "
                                    "à plat et coté en millimètres.")
        self.size = SegmentedControl([("objet_petit", "Petit  ≤ 10 cm"), ("objet", "Moyen  10 – 60 cm")], "objet")
        self.size.changed.connect(lambda _k: self._update_object_specs())
        self.object_card.body.addSpacing(6)
        self.object_card.body.addWidget(self.size)
        self.object_card.button.clicked.connect(lambda: self.profile_chosen.emit(self.size.value()))
        self.room_card = ModeCard(theme.VIOLET, paint_room_icon, "Salle entière",
                                  "Parcours une pièce caméra en main : la scène est nettoyée et posée sur "
                                  "son sol, en mètres.")
        self.room_card.button.clicked.connect(lambda: self.profile_chosen.emit("salle"))
        self.room_card.set_specs(self._specs(PROFILES["salle"]))
        self._update_object_specs()
        cards.addWidget(self.object_card, 1)
        cards.addWidget(self.room_card, 1)
        cards.addStretch(1)
        root.addLayout(cards)
        root.addSpacing(24)

        browse = QPushButton("Parcourir les datasets sans caméra")
        browse.setObjectName("Ghost")
        browse.setCursor(Qt.CursorShape.PointingHandCursor)
        browse.clicked.connect(self.browse_only)
        root.addWidget(browse, alignment=Qt.AlignmentFlag.AlignHCenter)
        root.addStretch(2)

    @staticmethod
    def _specs(profile: ScanProfile) -> list[tuple[str, str]]:
        voxel = f"{profile.voxel_mm:g} mm" if profile.voxel_mm < 10 else f"{profile.voxel_mm / 10:g} cm"
        return [("Distance idéale", profile.distance_label), ("Finesse", voxel),
                ("Export", "PLY + STL · mm" if profile.export_mm else "PLY + STL · m")]

    def _update_object_specs(self) -> None:
        self.object_card.set_specs(self._specs(PROFILES[self.size.value()]))

    def set_cameras(self, cameras: list[CameraInfo]) -> None:
        if not cameras:
            self.camera_chip.set("Aucune caméra détectée", "err")
            return
        cam = cameras[0]
        level = "ok" if cam.is_usb3 else "warn"
        self.camera_chip.set(f"{cam.name} · USB {cam.usb} · n° {cam.serial}", level)
