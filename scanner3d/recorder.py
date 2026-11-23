"""Enregistrement d'un dataset : écriture des images dans un thread, finalisation de la session."""
from __future__ import annotations

import logging
import queue
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .dataset import Intrinsics, SessionInfo, new_dataset_dir, write_session, write_thumbnail
from .profiles import ScanProfile

log = logging.getLogger(__name__)
JPEG_QUALITY = 95
QUEUE_SIZE = 240          # ~8 s d'avance à 30 img/s si le disque ralentit
MIN_FREE_BYTES = 2 * 1024 ** 3


class RecorderError(RuntimeError):
    """Enregistrement impossible (disque plein, dossier inaccessible...)."""


class FrameWriter:
    """Écrit les paires couleur/profondeur sur disque sans ralentir l'aperçu."""

    def __init__(self, dataset_dir: Path) -> None:
        self._dir = dataset_dir
        self._queue: queue.Queue[tuple[int, np.ndarray, np.ndarray] | None] = queue.Queue(QUEUE_SIZE)
        self._next_index = 0
        self._lock = threading.Lock()
        self.written = 0
        self.dropped = 0
        self.failed = 0
        self._thread = threading.Thread(target=self._run, name="frame-writer", daemon=True)
        self._thread.start()

    def put(self, color: np.ndarray, depth: np.ndarray) -> bool:
        """Met une paire en file ; False si la file est pleine (image perdue, comptée)."""
        try:
            self._queue.put_nowait((self._next_index, color, depth))
        except queue.Full:
            self.dropped += 1
            return False
        self._next_index += 1
        return True

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join()

    def _run(self) -> None:
        while (item := self._queue.get()) is not None:
            index, color, depth = item
            name = f"{index:06d}"
            ok_color = cv2.imwrite(str(self._dir / "couleur" / f"{name}.jpg"), color,
                                   [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            ok_depth = cv2.imwrite(str(self._dir / "profondeur" / f"{name}.png"), depth)
            with self._lock:
                if ok_color and ok_depth:
                    self.written += 1
                else:
                    self.failed += 1


@dataclass(frozen=True)
class RecordingResult:
    path: Path | None   # None si aucune image n'a été enregistrée (dossier supprimé)
    frames: int
    dropped: int
    failed: int
    duration_s: float


class Recorder:
    """Une session d'enregistrement : dossier, écrivain, métadonnées."""

    def __init__(self, root: Path, profile: ScanProfile, intrinsics: Intrinsics, depth_max_m: float,
                 camera: dict[str, str] | None = None) -> None:
        root.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(root).free
        if free < MIN_FREE_BYTES:
            raise RecorderError(f"Espace disque insuffisant ({free / 1024 ** 3:.1f} Go libres, 2 Go minimum).")
        self.profile = profile
        self.path = new_dataset_dir(root, profile.id)
        self._intrinsics = intrinsics
        self._depth_max_m = depth_max_m
        self._camera = camera or {}
        self._writer = FrameWriter(self.path)
        self._started = time.monotonic()
        self._created = datetime.now().isoformat(timespec="seconds")
        self._thumbnail: np.ndarray | None = None

    def add(self, color: np.ndarray, depth: np.ndarray) -> bool:
        if self._thumbnail is None or self._writer.written == 30:  # vignette après la mise au point
            self._thumbnail = color
        return self._writer.put(color, depth)

    @property
    def written(self) -> int:
        return self._writer.written

    @property
    def dropped(self) -> int:
        return self._writer.dropped

    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self._started

    def finish(self) -> RecordingResult:
        duration = self.elapsed_s
        self._writer.close()
        w = self._writer
        if w.failed:
            log.warning("%d images n'ont pas pu être écrites (disque plein ?).", w.failed)
        if w.written == 0:
            shutil.rmtree(self.path, ignore_errors=True)
            return RecordingResult(None, 0, w.dropped, w.failed, duration)
        write_session(self.path, SessionInfo(
            profile=self.profile.id, intrinsics=self._intrinsics, depth_scale=self.profile.depth_scale,
            depth_min_m=self.profile.depth_min_m, depth_max_m=self._depth_max_m, frames=w.written,
            dropped=w.dropped, duration_s=round(duration, 2), created=self._created, camera=self._camera))
        if self._thumbnail is not None:
            write_thumbnail(self.path, self._thumbnail)
        log.info("%d images enregistrées dans %s (%d perdues).", w.written, self.path, w.dropped)
        return RecordingResult(self.path, w.written, w.dropped, w.failed, duration)
