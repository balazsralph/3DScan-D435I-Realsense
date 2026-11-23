"""Caméra Intel RealSense D435/D435i : preset du profil, filtres, profondeur alignée sur la couleur."""
from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import dataclass

import numpy as np
import pyrealsense2 as rs

from .dataset import Intrinsics
from .profiles import STREAM_FPS, STREAM_HEIGHT, STREAM_WIDTH, ScanProfile

log = logging.getLogger(__name__)
ADVANCED_MODE_WAIT_S = 15.0
RESET_WAIT_S = 12.0
BUSY_WAIT_S = 1.5
OPEN_ATTEMPTS = 3
READ_TIMEOUT_MS = 5000


class CameraError(RuntimeError):
    """Problème de caméra, avec un message lisible par l'utilisateur."""


@dataclass(frozen=True)
class CameraInfo:
    name: str
    serial: str
    firmware: str
    usb: str

    @property
    def is_usb3(self) -> bool:
        return self.usb.startswith("3")

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "serial": self.serial, "firmware": self.firmware, "usb": self.usb}


@dataclass(frozen=True)
class RgbdFrame:
    color: np.ndarray   # BGR uint8 (H, W, 3)
    depth: np.ndarray   # uint16 (H, W), alignée sur la couleur, 0 = pas de mesure
    timestamp_ms: float
    number: int


def _info(device: rs.device, key: rs.camera_info, default: str = "?") -> str:
    return device.get_info(key) if device.supports(key) else default


def describe(device: rs.device) -> CameraInfo:
    return CameraInfo(_info(device, rs.camera_info.name), _info(device, rs.camera_info.serial_number),
                      _info(device, rs.camera_info.firmware_version),
                      _info(device, rs.camera_info.usb_type_descriptor))


def detect() -> list[CameraInfo]:
    """Caméras RealSense branchées (liste vide si aucune ou si le pilote est indisponible)."""
    try:
        return [describe(d) for d in rs.context().query_devices()]
    except RuntimeError as exc:
        log.warning("Détection RealSense impossible : %s", exc)
        return []


def _find_device(serial: str | None = None) -> rs.device:
    devices = list(rs.context().query_devices())
    if not devices:
        raise CameraError("Aucune caméra RealSense détectée. Branche-la sur un port USB 3 (bleu).")
    for device in devices:
        if serial is None or _info(device, rs.camera_info.serial_number) == serial:
            return device
    raise CameraError(f"Caméra n° {serial} introuvable.")


def _ensure_advanced_mode(device: rs.device) -> rs.device:
    """Active le mode avancé (nécessaire aux presets JSON) ; la caméra redémarre alors."""
    advanced = rs.rs400_advanced_mode(device)
    if advanced.is_enabled():
        return device
    serial = _info(device, rs.camera_info.serial_number)
    log.info("Activation du mode avancé de la caméra (redémarrage de quelques secondes)...")
    advanced.toggle_advanced_mode(True)
    deadline = time.monotonic() + ADVANCED_MODE_WAIT_S
    while time.monotonic() < deadline:
        time.sleep(1.0)
        try:
            device = _find_device(serial)
            if rs.rs400_advanced_mode(device).is_enabled():
                return device
        except (CameraError, RuntimeError):
            continue
    raise CameraError("La caméra n'est pas revenue après l'activation du mode avancé : débranche-la et rebranche-la.")


class RealSenseCamera:
    """Ouvre la caméra avec le preset du profil ; `read()` renvoie couleur + profondeur alignée."""

    def __init__(self, profile: ScanProfile, serial: str | None = None) -> None:
        self.profile = profile
        self._serial = serial
        self._pipeline: rs.pipeline | None = None
        self._align = rs.align(rs.stream.color)
        self._filters: list[rs.filter] = []
        self.info: CameraInfo | None = None
        self.intrinsics: Intrinsics | None = None
        self.depth_scale = profile.depth_scale

    def open(self) -> None:
        if not self.profile.preset_path.is_file():
            raise CameraError(f"Preset introuvable : {self.profile.preset_path}")
        last_error: RuntimeError | None = None
        for attempt in range(OPEN_ATTEMPTS):
            try:
                self._start()
                return
            except CameraError:
                raise
            except RuntimeError as exc:  # souvent « Device or resource busy » juste après un autre programme
                last_error = exc
                self.close()
                if attempt == 0:
                    log.warning("Caméra occupée (%s) : nouvel essai dans un instant.", exc)
                    time.sleep(BUSY_WAIT_S)
                elif attempt < OPEN_ATTEMPTS - 1:
                    log.warning("Caméra toujours indisponible : réinitialisation matérielle.")
                    self._hardware_reset()
        raise CameraError(f"Caméra inutilisable ({last_error}). realsense-viewer ou un autre programme "
                          "l'utilise-t-il encore ?") from last_error

    def _start(self) -> None:
        device = _ensure_advanced_mode(_find_device(self._serial))
        rs.rs400_advanced_mode(device).load_json(self.profile.preset_path.read_text())
        depth_sensor = device.first_depth_sensor()
        depth_sensor.set_option(rs.option.depth_units, 1.0 / self.profile.depth_scale)
        self.info = describe(device)
        pipeline, cfg = rs.pipeline(), rs.config()
        cfg.enable_device(self.info.serial)
        cfg.enable_stream(rs.stream.depth, STREAM_WIDTH, STREAM_HEIGHT, rs.format.z16, STREAM_FPS)
        cfg.enable_stream(rs.stream.color, STREAM_WIDTH, STREAM_HEIGHT, rs.format.bgr8, STREAM_FPS)
        profile = pipeline.start(cfg)
        scale = profile.get_device().first_depth_sensor().get_depth_scale()
        if abs(scale * self.profile.depth_scale - 1.0) > 1e-3:
            pipeline.stop()
            raise CameraError(f"Unité de profondeur non appliquée ({scale} m au lieu de "
                              f"{1 / self.profile.depth_scale} m).")
        intr = profile.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
        self.intrinsics = Intrinsics(intr.width, intr.height, intr.fx, intr.fy, intr.ppx, intr.ppy)
        self._filters = self._make_filters()
        self._pipeline = pipeline
        if not self.info.is_usb3:
            log.warning("Caméra branchée en USB %s : utilise un port USB 3 pour un flux fiable.", self.info.usb)

    def _make_filters(self) -> list[rs.filter]:
        """Filtre spatial dans le domaine des disparités (ordre recommandé par Intel), sans bouchage de trous."""
        if not self.profile.spatial_filter:
            return []
        spatial = rs.spatial_filter()
        spatial.set_option(rs.option.filter_magnitude, 2)
        spatial.set_option(rs.option.filter_smooth_alpha, 0.5)
        spatial.set_option(rs.option.filter_smooth_delta, 20)
        spatial.set_option(rs.option.holes_fill, 0)  # ne jamais inventer de profondeur
        return [rs.disparity_transform(True), spatial, rs.disparity_transform(False)]

    def _hardware_reset(self) -> None:
        """Redémarre la caméra et attend qu'elle réapparaisse sur le bus USB."""
        self.close()
        try:
            _find_device(self._serial).hardware_reset()
        except (CameraError, RuntimeError) as exc:
            log.warning("Réinitialisation impossible : %s", exc)
            return
        time.sleep(1.0)  # le temps qu'elle disparaisse
        deadline = time.monotonic() + RESET_WAIT_S
        while time.monotonic() < deadline:
            if self._is_present():
                time.sleep(1.0)  # réapparue : on lui laisse finir son initialisation
                return
            time.sleep(0.5)

    def _is_present(self) -> bool:
        serials = [_info(d, rs.camera_info.serial_number) for d in rs.context().query_devices()]
        return bool(serials) if self._serial is None else self._serial in serials

    def read(self, timeout_ms: int = READ_TIMEOUT_MS) -> RgbdFrame | None:
        """Image suivante, ou None si la paire couleur/profondeur est incomplète."""
        if self._pipeline is None:
            raise CameraError("Caméra non ouverte.")
        try:
            frames = self._pipeline.wait_for_frames(timeout_ms)
            for block in self._filters:
                frames = block.process(frames).as_frameset()
            aligned = self._align.process(frames)
        except RuntimeError as exc:
            raise CameraError(f"Flux de la caméra interrompu ({exc}). Câble débranché ?") from exc
        depth, color = aligned.get_depth_frame(), aligned.get_color_frame()
        if not depth or not color:
            return None
        return RgbdFrame(np.asanyarray(color.get_data()).copy(), np.asanyarray(depth.get_data()).copy(),
                         frames.get_timestamp(), color.get_frame_number())

    def close(self) -> None:
        if self._pipeline is not None:
            with contextlib.suppress(RuntimeError):
                self._pipeline.stop()
            self._pipeline = None

    def __enter__(self) -> RealSenseCamera:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
