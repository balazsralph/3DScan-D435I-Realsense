"""Threads de travail : caméra (aperçu + enregistrement), reconstruction en direct, construction du modèle.

Les images passent au thread de l'interface par « dernière valeur » : si l'interface n'a pas fini
d'afficher l'image précédente, la nouvelle remplace l'ancienne au lieu de s'empiler.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal

from ..acquisition.camera import CameraError, RealSenseCamera
from ..acquisition.recorder import Recorder, RecorderError, RecordingResult
from ..dataset import Dataset, DatasetError
from ..depth import backproject, center_distance_m, colorize_depth, depth_overlay, distance_hint, valid_ratio
from ..profiles import ScanProfile
from ..reconstruction.extraction import ExtractionError
from ..reconstruction.merge import merge_datasets
from ..reconstruction.pipeline import BuildOptions, build_dataset
from ..reconstruction.slam import Cancelled, DenseSlam, pick_device, prepare_frame

log = logging.getLogger(__name__)
CLOUD_EVERY_S = 0.12      # nuage de l'image courante : ~8 mises à jour par seconde
LIVE_CLOUD_EVERY_S = 0.8  # modèle en cours : extraction plus coûteuse
COVERAGE_EVERY_S = 0.15   # marqueurs de couverture sur l'image : ~6 par seconde
STATS_EVERY_S = 0.5


@dataclass(frozen=True)
class LiveStats:
    fps: float
    valid: float
    hint_level: str
    hint_text: str
    recording: bool
    frames: int
    dropped: int
    elapsed_s: float


class Mailbox:
    """Boîte à une place : on garde toujours la dernière valeur déposée."""

    def __init__(self) -> None:
        self._value: object | None = None
        self._cond = threading.Condition()
        self.closed = False

    def put(self, value: object) -> None:
        with self._cond:
            self._value = value
            self._cond.notify()

    def take(self, timeout: float = 0.5) -> object | None:
        with self._cond:
            if self._value is None and not self.closed:
                self._cond.wait(timeout)
            value, self._value = self._value, None
            return value

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()


class CameraWorker(QThread):
    opened = Signal(object, object)          # CameraInfo, Intrinsics
    frame_ready = Signal()
    cloud_ready = Signal(object, object)     # points (repère caméra), couleurs
    stats = Signal(object)                   # LiveStats
    recording_started = Signal(str)
    recording_finished = Signal(object)      # RecordingResult
    failed = Signal(str)

    def __init__(self, profile: ScanProfile, datasets_root: Path) -> None:
        super().__init__()
        self.profile = profile
        self.datasets_root = datasets_root
        self.depth_max_m = profile.depth_max_m
        self.view_mode = "mixte"          # image principale : "mixte" (couleur + profondeur) ou "couleur"
        self.send_cloud = True
        self.live_sink: Mailbox | None = None
        self.camera: RealSenseCamera | None = None
        self._stop = threading.Event()
        self._want_record = threading.Event()
        self._recorder: Recorder | None = None
        self._lock = threading.Lock()
        self._latest: tuple[np.ndarray, np.ndarray] | None = None
        self._frame_pending = False
        self._cloud_pending = False

    # -- commandes depuis l'interface (thread-safe) --------------------------------------------
    def start_recording(self) -> None:
        self._want_record.set()

    def stop_recording(self) -> None:
        self._want_record.clear()

    def stop(self) -> None:
        self._stop.set()
        self._want_record.clear()

    def take_frame(self) -> tuple[np.ndarray, np.ndarray] | None:
        """(image principale BGR, profondeur colorée BGR) les plus récentes."""
        with self._lock:
            latest, self._latest = self._latest, None
            self._frame_pending = False
            return latest

    def cloud_consumed(self) -> None:
        self._cloud_pending = False

    # -- boucle ---------------------------------------------------------------------------------
    def run(self) -> None:
        self.camera = RealSenseCamera(self.profile)
        try:
            self.camera.open()
        except (CameraError, RuntimeError) as exc:
            self.failed.emit(str(exc))
            return
        self.opened.emit(self.camera.info, self.camera.intrinsics)
        try:
            self._loop()
        except (CameraError, RecorderError, OSError) as exc:
            self.failed.emit(str(exc))
        finally:
            self._finish_recording()
            self.camera.close()

    def _loop(self) -> None:
        assert self.camera is not None and self.camera.intrinsics is not None
        intr, p = self.camera.intrinsics, self.profile
        last_cloud = last_stats = time.monotonic()
        count, t_fps, fps = 0, time.monotonic(), 0.0
        while not self._stop.is_set():
            frame = self.camera.read()
            if frame is None:
                continue
            self._sync_recording()
            if self._recorder is not None:
                self._recorder.add(frame.color, frame.depth)
                if self.live_sink is not None:
                    self.live_sink.put((frame.color, frame.depth))
            self._publish_frame(frame.color, frame.depth)
            now = time.monotonic()
            count += 1
            if now - t_fps >= 1.0:
                fps, count, t_fps = count / (now - t_fps), 0, now
            if (self.send_cloud and not self._cloud_pending and now - last_cloud >= CLOUD_EVERY_S
                    and (self._recorder is None or self.live_sink is None)):
                last_cloud = now
                shown = np.where(frame.depth <= self.depth_max_m * p.depth_scale, frame.depth, 0).astype(np.uint16)
                points, colors = backproject(shown, frame.color, intr.fx, intr.fy, intr.ppx, intr.ppy,
                                             p.depth_scale, step=2)
                self._cloud_pending = True
                self.cloud_ready.emit(points, colors)
            if now - last_stats >= STATS_EVERY_S:
                last_stats = now
                self.stats.emit(self._make_stats(frame.depth, fps))

    def _make_stats(self, depth: np.ndarray, fps: float) -> LiveStats:
        p = self.profile
        hint = distance_hint(center_distance_m(depth, p.depth_scale), p.guide_near_m, p.guide_far_m)
        rec = self._recorder
        return LiveStats(fps, valid_ratio(depth), hint.level, hint.text, rec is not None,
                         rec.written if rec else 0, rec.dropped if rec else 0, rec.elapsed_s if rec else 0.0)

    def _publish_frame(self, color: np.ndarray, depth: np.ndarray) -> None:
        if self._frame_pending:
            return
        p = self.profile
        if self.view_mode == "couleur":
            shown = color
        else:
            shown = depth_overlay(color, depth, p.depth_scale, p.depth_min_m, self.depth_max_m)
        colored = colorize_depth(depth, p.depth_scale, p.depth_min_m, self.depth_max_m)
        with self._lock:
            self._latest = (shown, colored)
            self._frame_pending = True
        self.frame_ready.emit()

    def _sync_recording(self) -> None:
        if self._want_record.is_set() and self._recorder is None:
            assert self.camera is not None and self.camera.intrinsics is not None
            info = self.camera.info.as_dict() if self.camera.info else {}
            self._recorder = Recorder(self.datasets_root, self.profile, self.camera.intrinsics, self.depth_max_m,
                                      info)
            self.recording_started.emit(str(self._recorder.path))
        elif not self._want_record.is_set() and self._recorder is not None:
            self._finish_recording()

    def _finish_recording(self) -> None:
        if self._recorder is None:
            return
        recorder, self._recorder = self._recorder, None
        result: RecordingResult = recorder.finish()
        self.recording_finished.emit(result)


class LiveReconWorker(QThread):
    """Reconstruit en direct (GPU) pendant l'enregistrement : modèle qui se remplit et couverture."""

    cloud_ready = Signal(object, object)     # points (repère 1re image), couleurs
    coverage_ready = Signal(object, float)   # grille de couverture (ou None si suivi perdu), part bien couverte
    status = Signal(bool, float, int, int)   # suivi ok, fitness, images intégrées, décrochages
    failed = Signal(str)

    def __init__(self, profile: ScanProfile, intrinsics: object, depth_max_m: float) -> None:
        super().__init__()
        self.profile, self.intrinsics, self.depth_max_m = profile, intrinsics, depth_max_m
        self.mailbox = Mailbox()
        self.show_coverage = True          # nuage coloré par couverture plutôt qu'en couleurs réelles
        self._cloud_pending = False
        self._coverage_pending = False

    def cloud_consumed(self) -> None:
        self._cloud_pending = False

    def coverage_consumed(self) -> None:
        self._coverage_pending = False

    def stop(self) -> None:
        self.mailbox.close()

    def run(self) -> None:
        p = self.profile
        voxel = p.voxel_mm * p.live_voxel_factor
        try:
            slam = DenseSlam(self.intrinsics, p, voxel, p.depth_scale, self.depth_max_m, pick_device())
        except RuntimeError as exc:
            self.failed.emit(f"Aperçu en direct indisponible : {exc}")
            return
        index, losses, last_cloud, last_coverage = 0, 0, 0.0, 0.0
        good_ratio = 0.0
        while not self.mailbox.closed:
            item = self.mailbox.take()
            if item is None:
                continue
            color, raw_depth = item
            try:
                color_rgb, depth = prepare_frame(color, raw_depth, p, p.depth_scale, self.depth_max_m)
                status = slam.process(color_rgb, depth, index)
                now = time.monotonic()
                if not self._coverage_pending and now - last_coverage >= COVERAGE_EVERY_S:
                    last_coverage = now
                    self._coverage_pending = True
                    coverage = slam.coverage_map(depth) if status.accepted else None
                    self.coverage_ready.emit(coverage, good_ratio)
                if not self._cloud_pending and now - last_cloud >= LIVE_CLOUD_EVERY_S:
                    last_cloud = now
                    self._cloud_pending = True
                    if self.show_coverage:
                        points, colors, good_ratio = slam.extract_coverage_points(300_000)
                    else:
                        points, colors = slam.extract_points(300_000)
                    self.cloud_ready.emit(points, colors)
            except RuntimeError as exc:
                self.failed.emit(f"Aperçu en direct interrompu : {exc}")
                return
            index += 1
            if status.lost_streak == p.tracking.fragment_break_frames:
                losses += 1
            self.status.emit(status.accepted, status.fitness, len(slam.indices), losses)


class BuildWorker(QThread):
    progress = Signal(float, str)
    preview = Signal(object, object)
    message = Signal(str)
    succeeded = Signal(object)    # BuildResult
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, dataset: Dataset, options: BuildOptions) -> None:
        super().__init__()
        self.dataset, self.options = dataset, options
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            result = build_dataset(self.dataset, self.options, self.progress.emit,
                                   lambda pts, cols: self.preview.emit(pts, cols), self._cancel, self.message.emit)
        except Cancelled:
            self.cancelled.emit()
        except (DatasetError, ExtractionError, ValueError, OSError, RuntimeError) as exc:
            log.exception("Échec de la construction")
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)


class MergeWorker(QThread):
    """Fusionne plusieurs datasets (construit d'abord les modèles manquants)."""

    progress = Signal(float, str)
    message = Signal(str)
    succeeded = Signal(object)    # MergeResult
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, datasets: list[Dataset], options: BuildOptions, root: Path) -> None:
        super().__init__()
        self.datasets, self.options, self.root = datasets, options, root
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            result = merge_datasets(self.datasets, self.options, self.root, self.progress.emit, self._cancel,
                                    self.message.emit)
        except Cancelled:
            self.cancelled.emit()
        except (DatasetError, ExtractionError, ValueError, OSError, RuntimeError) as exc:
            log.exception("Échec de la fusion")
            self.failed.emit(str(exc))
        else:
            self.succeeded.emit(result)
