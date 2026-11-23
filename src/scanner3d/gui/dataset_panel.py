"""Panneau des datasets : liste avec vignettes, aperçu image par image, lancement de la construction 3D."""
from __future__ import annotations

from pathlib import Path

import cv2
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..dataset import Dataset, DatasetError, read_frame, thumbnail
from ..depth import colorize_depth, format_distance
from ..profiles import DEFAULT_QUALITY, PROFILES, QUALITIES
from ..reconstruction.pipeline import BuildOptions
from . import theme
from .widgets import ImageView, SegmentedControl, bgr_to_qimage, hline

THUMB_W, THUMB_H = 104, 59
MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
PROFILE_ACCENTS = {"objet_petit": theme.AMBER, "objet": theme.AMBER, "salle": theme.VIOLET}
FORMAT_LABELS = {"scanner3d": "Scanner 3D", "scan3d": "scan3d (importé)", "open3d": "Ancien format Open3D"}


def nice_date(ds: Dataset) -> str:
    d = ds.created
    return f"{d.day} {MONTHS[d.month - 1]} {d.year} · {d:%H:%M}"


def duration_label(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60} min {seconds % 60:02d} s" if seconds >= 60 else f"{seconds} s"


def size_label(size_mm: list[float] | tuple[float, ...], units: str) -> str:
    if units == "m" or max(size_mm, default=0) >= 2000:
        return " × ".join(f"{v / 1000:.2f}".replace(".", ",") for v in size_mm) + " m"
    return " × ".join(f"{v:.0f}" for v in size_mm) + " mm"


def rounded_pixmap(image: QImage, size: QSize, radius: float = 7) -> QPixmap:
    scaled = image.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                          Qt.TransformationMode.SmoothTransformation)
    pix = QPixmap(size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size.width(), size.height()), radius, radius)
    p.setClipPath(path)
    p.drawImage(QRectF(0, 0, size.width(), size.height()), scaled,
                QRectF((scaled.width() - size.width()) / 2, (scaled.height() - size.height()) / 2,
                       size.width(), size.height()))
    p.end()
    return pix


class DatasetItem(QWidget):
    """Ligne de la liste : vignette, date, type, nombre d'images, état du modèle."""

    def __init__(self, ds: Dataset, thumb: QImage | None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 10, 8)
        layout.setSpacing(10)
        pic = QLabel()
        pic.setFixedSize(THUMB_W, THUMB_H)
        if thumb is not None:
            pic.setPixmap(rounded_pixmap(thumb, QSize(THUMB_W, THUMB_H)))
        else:
            pic.setStyleSheet(f"background: {theme.BG}; border-radius: 7px;")
        layout.addWidget(pic)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(nice_date(ds))
        title.setStyleSheet("font-weight: 700;")
        profile = PROFILES.get(ds.profile_id)
        accent = PROFILE_ACCENTS.get(ds.profile_id, theme.MUTED)
        kind = QLabel(f"{profile.title if profile else ds.profile_id}  ·  {ds.frame_count()} images")
        kind.setStyleSheet(f"color: {accent}; font-size: 9pt; font-weight: 600;")
        info = ds.build_info()
        if info:
            state = QLabel(f"✓ Modèle · {size_label(info.get('size_mm', []), info.get('units', 'mm'))}")
            state.setStyleSheet(f"color: {theme.GREEN}; font-size: 9pt;")
        else:
            state = QLabel("Pas encore construit")
            state.setObjectName("Faint")
        for w in (title, kind, state):
            text.addWidget(w)
        layout.addLayout(text, 1)

    def set_selected(self, selected: bool) -> None:
        border = theme.AMBER if selected else theme.LINE
        bg = theme.SURFACE_3 if selected else theme.SURFACE_2
        self.setStyleSheet(f"QWidget#Card {{ background: {bg}; border: 1px solid {border}; border-radius: 10px; }}")


class DatasetPanel(QWidget):
    selection_changed = Signal(object)            # Dataset | None
    build_requested = Signal(object, object)      # Dataset, BuildOptions
    cancel_requested = Signal()
    show_model_requested = Signal(object)         # Dataset
    add_folder_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(360)
        self._datasets: list[Dataset] = []
        self._thumbs: dict[Path, QImage | None] = {}
        self._items: list[DatasetItem] = []
        self._current: Dataset | None = None
        self._frames: list = []
        self._building = False
        self._blocked: str | None = None
        self._preview_timer = QTimer(self, singleShot=True, interval=60)
        self._preview_timer.timeout.connect(self._load_preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)
        root.addLayout(self._header())
        self.list = QListWidget()
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.currentRowChanged.connect(self._on_row)
        root.addWidget(self.list, 1)
        self.empty = QLabel("Aucun dataset pour l'instant.\nLance un enregistrement, ou ajoute un dossier existant.")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty, 1)
        root.addWidget(hline())
        self.details = self._details()
        root.addWidget(self.details)
        self.set_datasets([])

    # -- construction de l'interface -----------------------------------------------------------
    def _header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        title = QLabel("Datasets")
        title.setObjectName("H2")
        self.count = QLabel("")
        self.count.setObjectName("Faint")
        refresh = QPushButton("↻")
        refresh.setObjectName("Ghost")
        refresh.setToolTip("Actualiser la liste")
        refresh.clicked.connect(self.refresh_requested)
        add = QPushButton("+ Dossier")
        add.setObjectName("Ghost")
        add.setToolTip("Ajouter un dossier contenant des datasets (ex. ~/Desktop/scan3d/scans)")
        add.clicked.connect(self.add_folder_requested)
        row.addWidget(title)
        row.addWidget(self.count)
        row.addStretch(1)
        row.addWidget(refresh)
        row.addWidget(add)
        return row

    def _details(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        top = QHBoxLayout()
        eyebrow = QLabel("APERÇU")
        eyebrow.setObjectName("Eyebrow")
        self.preview_mode = SegmentedControl([("couleur", "Couleur"), ("profondeur", "Profondeur")])
        self.preview_mode.changed.connect(lambda _k: self._preview_timer.start())
        top.addWidget(eyebrow)
        top.addStretch(1)
        top.addWidget(self.preview_mode)
        layout.addLayout(top)
        self.preview = ImageView("Sélectionne un dataset")
        self.preview.setFixedHeight(168)
        layout.addWidget(self.preview)
        scrub = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(lambda _v: self._preview_timer.start())
        self.frame_label = QLabel("—")
        self.frame_label.setObjectName("Mono")
        self.frame_label.setMinimumWidth(86)
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        scrub.addWidget(self.slider, 1)
        scrub.addWidget(self.frame_label)
        layout.addLayout(scrub)

        self.meta = QGridLayout()
        self.meta.setHorizontalSpacing(12)
        self.meta.setVerticalSpacing(3)
        self._meta_labels: dict[str, QLabel] = {}
        for row, key in enumerate(("Format", "Durée", "Profondeur", "Modèle")):
            k = QLabel(key.upper())
            k.setObjectName("Eyebrow")
            v = QLabel("—")
            v.setWordWrap(True)
            v.setStyleSheet(f"color: {theme.TEXT}; font-size: 9pt;")
            self.meta.addWidget(k, row, 0)
            self.meta.addWidget(v, row, 1)
            self._meta_labels[key] = v
        self.meta.setColumnStretch(1, 1)
        layout.addLayout(self.meta)

        opts = QHBoxLayout()
        self.profile_combo = QComboBox()
        for pid, profile in PROFILES.items():
            self.profile_combo.addItem(profile.title, pid)
        self.profile_combo.setToolTip("Réglages de reconstruction à utiliser")
        self.quality = SegmentedControl([(q.id, q.label) for q in QUALITIES.values()], DEFAULT_QUALITY)
        self.quality.setToolTip("Détaillé : voxel 25 % plus fin (plus de détails, un peu plus de bruit)")
        opts.addWidget(self.profile_combo, 1)
        opts.addWidget(self.quality, 1)
        layout.addLayout(opts)
        self.smoothing = QCheckBox("Lissage de surface")
        self.smoothing.setToolTip("Lissage de Taubin : gomme les marches de la grille de voxels sans réduire le volume")
        self.smoothing.setChecked(True)
        layout.addWidget(self.smoothing)

        self.build_button = QPushButton("Construire le modèle 3D")
        self.build_button.setObjectName("Primary")
        self.build_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.build_button.clicked.connect(self._on_build)
        layout.addWidget(self.build_button)
        self.progress_box = QWidget()
        pl = QVBoxLayout(self.progress_box)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(6)
        prow = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedHeight(10)
        cancel = QPushButton("Annuler")
        cancel.setObjectName("Danger")
        cancel.clicked.connect(self.cancel_requested)
        prow.addWidget(self.progress, 1)
        prow.addWidget(cancel)
        self.progress_text = QLabel("")
        self.progress_text.setObjectName("Muted")
        pl.addLayout(prow)
        pl.addWidget(self.progress_text)
        layout.addWidget(self.progress_box)
        self.progress_box.hide()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(64)
        self.log.setMaximumBlockCount(200)
        layout.addWidget(self.log)
        self.log.hide()

        actions = QHBoxLayout()
        self.view_button = QPushButton("Voir le modèle")
        self.view_button.clicked.connect(lambda: self._current and self.show_model_requested.emit(self._current))
        folder = QPushButton("Ouvrir le dossier")
        folder.setObjectName("Ghost")
        folder.clicked.connect(self._open_folder)
        self.folder_button = folder
        actions.addWidget(self.view_button)
        actions.addWidget(folder)
        actions.addStretch(1)
        layout.addLayout(actions)
        return box

    # -- données --------------------------------------------------------------------------------
    def set_datasets(self, datasets: list[Dataset], select: Path | None = None) -> None:
        keep = select or (self._current.path if self._current else None)
        self._datasets = datasets
        self.list.blockSignals(True)
        self.list.clear()
        self._items = []
        for ds in datasets:
            if ds.path not in self._thumbs:
                img = thumbnail(ds)
                self._thumbs[ds.path] = bgr_to_qimage(img) if img is not None else None
            widget = DatasetItem(ds, self._thumbs[ds.path])
            item = QListWidgetItem(self.list)
            item.setSizeHint(QSize(0, THUMB_H + 18))
            self.list.setItemWidget(item, widget)
            self._items.append(widget)
        self.list.blockSignals(False)
        self.count.setText(f"{len(datasets)}" if datasets else "")
        self.list.setVisible(bool(datasets))
        self.empty.setVisible(not datasets)
        row = next((i for i, d in enumerate(datasets) if d.path == keep), 0 if datasets else -1)
        self.list.setCurrentRow(row)
        self._on_row(row)

    def forget_thumbnail(self, path: Path) -> None:
        self._thumbs.pop(path, None)

    def current(self) -> Dataset | None:
        return self._current

    def _on_row(self, row: int) -> None:
        for i, item in enumerate(self._items):
            item.set_selected(i == row)
        self._current = self._datasets[row] if 0 <= row < len(self._datasets) else None
        ds = self._current
        self.details.setEnabled(ds is not None)
        if ds is None:
            self._frames = []
            self.preview.set_image(None)
            self.frame_label.setText("—")
            for label in self._meta_labels.values():
                label.setText("—")
            self.selection_changed.emit(None)
            return
        self._frames = ds.frames()
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(len(self._frames) - 1, 0))
        self.slider.setValue(len(self._frames) // 2)
        self.slider.blockSignals(False)
        idx = self.profile_combo.findData(ds.profile_id)
        self.profile_combo.setCurrentIndex(max(idx, 0))
        self._fill_meta(ds)
        self._update_buttons()
        self._load_preview()
        self.selection_changed.emit(ds)

    def _fill_meta(self, ds: Dataset) -> None:
        m = self._meta_labels
        m["Format"].setText(FORMAT_LABELS.get(ds.fmt, ds.fmt))
        duration = ds.meta.get("duration_s")
        dropped = ds.meta.get("dropped", 0)
        m["Durée"].setText((duration_label(duration) if duration else "—") +
                           (f"  ·  {dropped} perdues" if dropped else ""))
        unit = {10000.0: "0,1 mm", 1000.0: "1 mm"}.get(ds.depth_scale, f"1/{ds.depth_scale:g} m")
        far = f"  ·  coupée à {format_distance(ds.depth_max_m)}" if ds.depth_max_m else ""
        m["Profondeur"].setText(f"unité {unit}{far}")
        info = ds.build_info()
        if info:
            used = f"{info.get('frames_used')}/{info.get('frames_total')} images"
            dims = size_label(info.get("size_mm", []), info.get("units", "mm"))
            m["Modèle"].setText(f"{dims}  ·  {used}  ·  {info.get('duration_s', 0):.0f} s")
        else:
            m["Modèle"].setText("pas encore construit")

    def _load_preview(self) -> None:
        if not self._frames or self._current is None:
            return
        index = min(self.slider.value(), len(self._frames) - 1)
        self.frame_label.setText(f"{index + 1} / {len(self._frames)}")
        try:
            color, depth = read_frame(self._frames[index])
        except DatasetError as exc:
            self.preview.set_image(None)
            self.preview.set_placeholder(str(exc))
            return
        if self.preview_mode.value() == "profondeur":
            ds = self._current
            profile = PROFILES.get(ds.profile_id)
            far = ds.depth_max_m or (profile.depth_max_m if profile else 3.0)
            near = profile.depth_min_m if profile else 0.1
            image = colorize_depth(depth, ds.depth_scale, near, far)
        else:
            image = color
        h, w = image.shape[:2]
        small = cv2.resize(image, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        self.preview.set_image(bgr_to_qimage(small))

    # -- construction ---------------------------------------------------------------------------
    def _on_build(self) -> None:
        if self._current is None:
            return
        options = BuildOptions(self.profile_combo.currentData(), self.quality.value(), self.smoothing.isChecked())
        self.build_requested.emit(self._current, options)

    def options_for(self, ds: Dataset) -> BuildOptions:
        return BuildOptions(ds.profile_id, self.quality.value(), self.smoothing.isChecked())

    def set_building(self, building: bool) -> None:
        self._building = building
        self.progress_box.setVisible(building)
        if building:
            self.progress.setValue(0)
            self.progress_text.setText("Préparation...")
            self.log.clear()
            self.log.show()
        self._update_buttons()

    def set_progress(self, fraction: float, message: str) -> None:
        self.progress.setValue(int(fraction * 1000))
        self.progress_text.setText(f"{fraction * 100:.0f} %  ·  {message}")

    def append_log(self, message: str) -> None:
        self.log.show()
        self.log.appendPlainText(message)

    def set_build_blocked(self, reason: str | None) -> None:
        self._blocked = reason
        self._update_buttons()

    def _update_buttons(self) -> None:
        ds = self._current
        blocked = self._blocked
        self.build_button.setVisible(not self._building)
        self.build_button.setEnabled(ds is not None and not self._building and blocked is None)
        self.build_button.setToolTip(blocked or "")
        if ds is not None:
            self.build_button.setText("Reconstruire le modèle 3D" if ds.has_model else "Construire le modèle 3D")
        self.view_button.setEnabled(ds is not None and ds.has_model)

    def _open_folder(self) -> None:
        if self._current is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current.path)))
