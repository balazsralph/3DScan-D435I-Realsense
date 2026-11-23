"""Fenêtre principale : accueil (choix du mode) puis espace de travail datasets | caméra | 3D."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import open3d.core as o3c
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..build import BuildOptions, BuildResult
from ..camera import detect
from ..dataset import Dataset, discover
from ..profiles import PROFILES, ScanProfile
from ..recorder import RecordingResult
from ..slam import pick_device
from .dataset_panel import DatasetPanel, nice_date, size_label
from .live_panel import LivePanel
from .start_screen import StartScreen
from .viewer_panel import ViewerPanel
from .widgets import Chip, bgr_to_qimage
from .workers import BuildWorker, CameraWorker, LiveReconWorker, LiveStats

log = logging.getLogger(__name__)
STOP_WAIT_MS = 4000
CLOSE_WAIT_MS = 20_000  # à la fermeture : laisser l'écrivain vider sa file d'images sur le disque


class MainWindow(QMainWindow):
    def __init__(self, repo_root: Path, initial_profile: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Scanner 3D · RealSense D435i")
        self.resize(1680, 940)
        self.setMinimumSize(1200, 720)
        self.settings = QSettings("scanner3d", "scanner3d")
        self.repo_root = repo_root
        self.datasets_root = repo_root / "datasets"
        self.profile: ScanProfile | None = None
        self.camera_worker: CameraWorker | None = None
        self.live_worker: LiveReconWorker | None = None
        self.build_worker: BuildWorker | None = None
        self.intrinsics: object | None = None
        self.has_cuda = pick_device().get_type() == o3c.Device.DeviceType.CUDA
        self._first_live_cloud = True
        self._first_preview = True

        self.stack = QStackedWidget()
        self.start = StartScreen()
        self.start.profile_chosen.connect(self.open_profile)
        self.start.browse_only.connect(self.browse_only)
        self.start.refresh_camera.connect(lambda: self.start.set_cameras(detect()))
        self.stack.addWidget(self.start)
        self.stack.addWidget(self._workspace())
        self.setCentralWidget(self.stack)
        self.start.set_cameras(detect())
        self._restore_settings()
        space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        space.activated.connect(self._space_pressed)
        if initial_profile:
            self.open_profile(initial_profile)

    # -- construction -------------------------------------------------------------------------
    def _workspace(self) -> QWidget:
        page = QWidget()
        page.setObjectName("Root")
        root = QVBoxLayout(page)
        root.setContentsMargins(14, 10, 14, 14)
        root.setSpacing(10)
        top = QHBoxLayout()
        back = QPushButton("←  Modes")
        back.setObjectName("Ghost")
        back.clicked.connect(self.back_to_start)
        brand = QLabel("SCANNER 3D")
        brand.setObjectName("Eyebrow")
        self.mode_chip = Chip()
        reconnect = QPushButton("Reconnecter la caméra")
        reconnect.setObjectName("Ghost")
        reconnect.clicked.connect(self._start_camera)
        self.reconnect_button = reconnect
        gpu = Chip("GPU · CUDA", "ok") if self.has_cuda else Chip("CPU · pas de CUDA (plus lent)", "warn")
        for w in (back, brand, self.mode_chip):
            top.addWidget(w)
        top.addStretch(1)
        top.addWidget(reconnect)
        top.addWidget(gpu)
        root.addLayout(top)

        self.datasets = DatasetPanel()
        self.live = LivePanel()
        self.viewer_panel = ViewerPanel()
        self.viewer = self.viewer_panel.viewer
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        for w in (self.datasets, self.live, self.viewer_panel):
            splitter.addWidget(w)
        splitter.setSizes([360, 660, 660])
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        root.addWidget(splitter, 1)

        self.datasets.selection_changed.connect(self._on_selection)
        self.datasets.build_requested.connect(self.start_build)
        self.datasets.cancel_requested.connect(self._cancel_build)
        self.datasets.show_model_requested.connect(self.show_model)
        self.datasets.add_folder_requested.connect(self._add_folder)
        self.datasets.refresh_requested.connect(self.refresh_datasets)
        self.live.record_toggled.connect(self.toggle_record)
        self.live.view_mode_changed.connect(self._set_view_mode)
        self.live.depth_max_changed.connect(self._set_depth_max)
        self.viewer_panel.source_changed.connect(self._on_source)
        return page

    # -- paramètres ---------------------------------------------------------------------------
    def _extra_roots(self) -> list[Path]:
        value = self.settings.value("extra_roots", [])
        items = [value] if isinstance(value, str) else list(value or [])
        return [Path(p) for p in items if p]

    def _restore_settings(self) -> None:
        self.datasets.quality.set_value(str(self.settings.value("quality", "standard")))
        self.datasets.smoothing.setChecked(self.settings.value("smoothing", True, type=bool))
        self.live.auto_build.setChecked(self.settings.value("auto_build", True, type=bool))

    def _save_settings(self) -> None:
        self.settings.setValue("quality", self.datasets.quality.value())
        self.settings.setValue("smoothing", self.datasets.smoothing.isChecked())
        self.settings.setValue("auto_build", self.live.auto_build.isChecked())

    # -- modes ---------------------------------------------------------------------------------
    def open_profile(self, profile_id: str) -> None:
        self.profile = PROFILES[profile_id]
        self.mode_chip.set(f"{self.profile.title} · {self.profile.subtitle}", "warn" if self.profile.kind == "object"
                           else "info")
        self.live.set_profile(self.profile)
        self.live.setEnabled(True)
        self.reconnect_button.setVisible(True)
        self.stack.setCurrentIndex(1)
        self.viewer_panel.set_source("direct")
        self._on_source("direct")
        self.refresh_datasets()
        self._start_camera()

    def browse_only(self) -> None:
        self._stop_camera()
        self.profile = None
        self.mode_chip.set("Datasets seulement", "neutral")
        self.live.setEnabled(False)
        self.live.clear_frames("Caméra non utilisée.\nReviens aux modes pour scanner.")
        self.live.camera_chip.set("Caméra inactive", "neutral")
        self.reconnect_button.setVisible(False)
        self.stack.setCurrentIndex(1)
        self.refresh_datasets()
        self.viewer_panel.set_source("modele")
        self._on_source("modele")

    def back_to_start(self) -> None:
        if self.camera_worker is not None:
            self.toggle_record(False)
        self._stop_camera()
        self.start.set_cameras(detect())
        self.stack.setCurrentIndex(0)

    # -- caméra --------------------------------------------------------------------------------
    def _start_camera(self) -> None:
        if self.profile is None:
            return
        self._stop_camera()
        self.live.camera_chip.set("Connexion...", "info")
        self.live.record.setEnabled(False)
        worker = CameraWorker(self.profile, self.datasets_root)
        worker.view_mode = self.live.view_mode.value()
        worker.depth_max_m = self.live.depth_slider.value() / 100.0
        worker.opened.connect(self._on_camera_opened)
        worker.failed.connect(self._on_camera_failed)
        worker.frame_ready.connect(self._on_frame)
        worker.cloud_ready.connect(self._on_camera_cloud)
        worker.stats.connect(self._on_stats)
        worker.recording_started.connect(self._on_recording_started)
        worker.recording_finished.connect(self._on_recording_finished)
        self.camera_worker = worker
        worker.start()

    def _stop_camera(self, wait_ms: int = CLOSE_WAIT_MS) -> None:
        self._stop_live()
        worker, self.camera_worker = self.camera_worker, None
        if worker is not None:
            worker.stop()
            if not worker.wait(wait_ms):
                log.warning("Le thread caméra ne s'est pas arrêté à temps.")

    def _on_camera_opened(self, info: object, intrinsics: object) -> None:
        self.intrinsics = intrinsics
        level = "ok" if getattr(info, "is_usb3", True) else "warn"
        self.live.camera_chip.set(f"{info.name} · USB {info.usb}", level)
        self.live.record.setEnabled(True)

    def _on_camera_failed(self, message: str) -> None:
        if self.sender() is not self.camera_worker:
            return
        self.live.camera_chip.set("Caméra indisponible", "err")
        self.live.record.setEnabled(False)
        self.live.set_recording(False)
        self.live.clear_frames(f"{message}\n\nBranche la caméra puis clique sur « Reconnecter la caméra ».\n"
                                       "Les datasets restent consultables et constructibles.")
        self._stop_live()

    def _on_frame(self) -> None:
        worker = self.camera_worker
        if worker is None or self.sender() is not worker:
            return
        data = worker.take_frame()
        if data is not None:
            self.live.show_frames(bgr_to_qimage(data[0]), bgr_to_qimage(data[1]))

    def _on_camera_cloud(self, points: np.ndarray, colors: np.ndarray) -> None:
        worker = self.camera_worker
        if worker is None or self.sender() is not worker:
            return
        if self.viewer_panel.source.value() == "direct" and self.build_worker is None:
            self.viewer.show_points(points, colors)
        worker.cloud_consumed()

    def _on_stats(self, stats: LiveStats) -> None:
        self.live.set_stats(stats)
        self.live.view.update()

    def _set_view_mode(self, mode: str) -> None:
        if self.camera_worker is not None:
            self.camera_worker.view_mode = mode

    def _set_depth_max(self, value_m: float) -> None:
        if self.camera_worker is not None:
            self.camera_worker.depth_max_m = value_m

    # -- enregistrement ------------------------------------------------------------------------
    def _space_pressed(self) -> None:
        if self.stack.currentIndex() == 1 and self.live.record.isEnabled():
            self.live.record.click()

    def toggle_record(self, on: bool) -> None:
        worker = self.camera_worker
        if worker is None:
            self.live.set_recording(False)
            return
        if on:
            if self.build_worker is not None:
                self.live.set_recording(False)
                QMessageBox.information(self, "Construction en cours",
                                        "Attends la fin de la construction (ou annule-la) avant d'enregistrer : "
                                        "les deux utilisent la carte graphique.")
                return
            self._start_live(worker)
            worker.start_recording()
        else:
            worker.stop_recording()
            self._stop_live()

    def _start_live(self, worker: CameraWorker) -> None:
        if not self.has_cuda or self.profile is None or self.intrinsics is None:
            return
        live = LiveReconWorker(self.profile, self.intrinsics, worker.depth_max_m)
        live.cloud_ready.connect(self._on_live_cloud)
        live.status.connect(self._on_live_status)
        live.failed.connect(lambda msg: self.datasets.append_log(msg))
        self.live_worker = live
        self._first_live_cloud = True
        worker.live_sink = live.mailbox
        live.start()
        self.viewer_panel.set_source("direct")
        self.viewer_panel.set_title("Reconstruction en direct", "Le modèle se remplit pendant que tu scannes.")

    def _stop_live(self) -> None:
        live, self.live_worker = self.live_worker, None
        if self.camera_worker is not None:
            self.camera_worker.live_sink = None
        if live is not None:
            live.stop()
            live.wait(STOP_WAIT_MS)

    def _on_live_cloud(self, points: np.ndarray, colors: np.ndarray) -> None:
        live = self.live_worker
        if live is None or self.sender() is not live:
            return
        if self.viewer_panel.source.value() == "direct":
            self.viewer.show_points(points, colors, reset_camera=self._first_live_cloud)
            self._first_live_cloud = False
            self.viewer_panel.info.setText(f"{len(points):,} points".replace(",", " "))
        live.cloud_consumed()

    def _on_live_status(self, ok: bool, fitness: float, frames: int, losses: int) -> None:
        if ok:
            self.live.view.tracking = (f"Suivi OK · {frames} images intégrées", "ok")
        else:
            self.live.view.tracking = ("Suivi perdu · ralentis et reviens vers une zone déjà vue", "err")

    def _on_recording_started(self, path: str) -> None:
        self.live.set_recording(True)
        self.datasets.set_build_blocked("Enregistrement en cours")

    def _on_recording_finished(self, result: RecordingResult) -> None:
        self.live.set_recording(False)
        self.datasets.set_build_blocked(None)
        if result.path is None:
            return
        self.refresh_datasets(select=result.path)
        message = f"Enregistré : {result.frames} images en {result.duration_s:.0f} s"
        if result.dropped:
            message += f" ({result.dropped} perdues : disque trop lent ?)"
        self.datasets.append_log(message)
        ds = self.datasets.current()
        if ds is not None and ds.path == result.path and self.live.auto_build.isChecked():
            self.start_build(ds, self.datasets.options_for(ds))

    # -- datasets ------------------------------------------------------------------------------
    def refresh_datasets(self, select: Path | None = None) -> None:
        roots = [self.datasets_root, self.repo_root / "dataset", *self._extra_roots()]
        found, errors = discover([r for r in roots if r.exists()])
        if select is not None:
            self.datasets.forget_thumbnail(select)
        self.datasets.set_datasets(found, select)
        for error in errors:
            log.warning(error)

    def _add_folder(self) -> None:
        start = str(Path.home() / "Desktop")
        folder = QFileDialog.getExistingDirectory(self, "Dossier de datasets (ou un dataset)", start)
        if not folder:
            return
        roots = [str(p) for p in self._extra_roots()]
        if folder not in roots:
            roots.append(folder)
            self.settings.setValue("extra_roots", roots)
        self.refresh_datasets()

    def _on_selection(self, ds: Dataset | None) -> None:
        if self.viewer_panel.source.value() == "modele" and self.build_worker is None:
            self._show_selected_model()

    def _on_source(self, source: str) -> None:
        if source == "modele":
            if self.build_worker is None:
                self._show_selected_model()
        else:
            self.viewer.clear_cloud()
            self._first_live_cloud = True
            live = self.camera_worker is not None
            self.viewer_panel.set_title("Nuage en direct" if live else "Direct",
                                        "Nuage de l'image courante, coupé à la profondeur choisie." if live else "")
            if not live:
                self.viewer.show_message("Aucune caméra active.")

    def _show_selected_model(self) -> None:
        ds = self.datasets.current()
        if ds is None:
            self.viewer.show_message("Sélectionne un dataset à gauche.")
            self.viewer_panel.set_title("Modèle")
        elif ds.has_model:
            self.show_model(ds)
        else:
            self.viewer.show_message("Ce dataset n'a pas encore de modèle.\nClique sur « Construire le modèle 3D ».")
            self.viewer_panel.set_title("Modèle", nice_date(ds))

    def show_model(self, ds: Dataset) -> None:
        info = ds.build_info() or {}
        units = info.get("units", "mm")
        self.viewer_panel.set_source("modele")
        try:
            vertices, triangles = self.viewer.show_model(ds.model_path, units)
        except (ValueError, OSError) as exc:
            self.viewer.show_message(f"Modèle illisible : {exc}")
            return
        self.viewer_panel.set_model_dir(ds.reconstruction_dir)
        dims = size_label(info.get("size_mm", []), units) if info.get("size_mm") else ""
        detail = f"{triangles:,} triangles".replace(",", " ")
        if info:
            used = f"{info.get('frames_used')}/{info.get('frames_total')} images"
            detail += f"  ·  {used}  ·  voxel {info.get('voxel_mm')} mm"
        self.viewer_panel.set_title(f"Modèle  ·  {dims}" if dims else "Modèle", detail)

    # -- construction --------------------------------------------------------------------------
    def start_build(self, ds: Dataset, options: BuildOptions) -> None:
        if self.build_worker is not None:
            return
        if self.camera_worker is not None and self.live.record.isChecked():
            QMessageBox.information(self, "Enregistrement en cours", "Termine l'enregistrement avant de construire.")
            return
        worker = BuildWorker(ds, options)
        worker.progress.connect(self.datasets.set_progress)
        worker.message.connect(self.datasets.append_log)
        worker.preview.connect(self._on_build_preview)
        worker.succeeded.connect(self._on_build_ok)
        worker.failed.connect(self._on_build_failed)
        worker.cancelled.connect(self._on_build_cancelled)
        worker.finished.connect(self._on_build_thread_done)
        self.build_worker = worker
        self._first_preview = True
        self._save_settings()
        self.datasets.set_building(True)
        self.live.record.setEnabled(False)
        self.viewer_panel.set_source("modele")
        self.viewer.show_message("Construction en cours...\nLe modèle apparaîtra ici au fil du suivi.")
        self.viewer_panel.set_title("Construction en cours", nice_date(ds))
        worker.start()

    def _cancel_build(self) -> None:
        if self.build_worker is not None:
            self.build_worker.cancel()
            self.datasets.set_progress(0, "Annulation...")

    def _on_build_preview(self, points: np.ndarray, colors: np.ndarray) -> None:
        if self.viewer_panel.source.value() == "modele":
            self.viewer.show_points(points, colors, reset_camera=self._first_preview)
            self._first_preview = False

    def _on_build_ok(self, result: BuildResult) -> None:
        sizes = " × ".join(f"{s:g}" for s in result.size_mm)
        used = f"{result.frames_used}/{result.frames_total} images"
        parts = f"{result.fragments_merged}/{result.fragments} morceau(x)"
        self.datasets.append_log(f"Modèle prêt : {sizes} mm · {used} · {parts} · {result.duration_s:.0f} s")
        for warning in result.warnings:
            self.datasets.append_log(f"Attention : {warning}")
        ds = self.build_worker.dataset if self.build_worker else None
        self.refresh_datasets(select=ds.path if ds else None)
        current = self.datasets.current()
        if current is not None and current.has_model:
            self.show_model(current)

    def _on_build_failed(self, message: str) -> None:
        self.datasets.append_log(f"Échec : {message}")
        self.viewer.show_message(f"La construction a échoué :\n{message}")
        QMessageBox.warning(self, "Construction impossible", message)

    def _on_build_cancelled(self) -> None:
        self.datasets.append_log("Construction annulée.")
        self.viewer.show_message("Construction annulée.")

    def _on_build_thread_done(self) -> None:
        self.build_worker = None
        self.datasets.set_building(False)
        self.live.record.setEnabled(self.camera_worker is not None and self.intrinsics is not None)

    # -- fermeture -----------------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:
        if self.build_worker is not None:
            answer = QMessageBox.question(self, "Construction en cours", "Annuler la construction et quitter ?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.build_worker.cancel()
            self.build_worker.wait(30_000)
        self._save_settings()
        if self.camera_worker is not None:
            self.camera_worker.stop_recording()
        self._stop_camera()
        self.viewer.close_viewer()
        event.accept()
