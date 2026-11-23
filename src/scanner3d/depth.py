"""Traitements d'images de profondeur : nettoyage, coloration, nuage de points, aide au cadrage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

CENTER_HALF = 20                      # demi-côté (px) de la zone centrale pour mesurer la distance
_BIG = np.float32(1e9)               # remplace les trous pour que l'érosion les ignore
_KERNEL = np.ones((3, 3), np.uint8)

HintLevel = Literal["ok", "near", "far", "none"]


@dataclass(frozen=True)
class DistanceHint:
    level: HintLevel
    text: str
    distance_m: float | None


def clean_depth(raw: np.ndarray, depth_scale: float, min_m: float, max_m: float,
                edge_jump_ratio: float) -> np.ndarray:
    """Profondeur brute (uint16) -> profondeur fiable (uint16, 0 = pas de mesure).

    - hors de la plage [min_m, max_m] : retiré ;
    - pixels « volants » sur les bords des objets (à mi-chemin entre l'avant-plan et le fond,
      typiques des caméras stéréo) : retirés quand la profondeur varie de plus de
      `edge_jump_ratio` (relatif) dans leur voisinage 3x3. Les trous voisins sont ignorés,
      pour ne pas grignoter les surfaces au bord d'une zone non mesurée.
    """
    if raw.dtype != np.uint16 or raw.ndim != 2:
        raise ValueError(f"profondeur attendue en uint16 2D, reçu {raw.dtype} {raw.shape}")
    depth = raw.astype(np.float32)
    valid = (depth >= min_m * depth_scale) & (depth <= max_m * depth_scale)
    depth = np.where(valid, depth, np.float32(0))
    if edge_jump_ratio > 0:
        local_max = cv2.dilate(depth, _KERNEL)
        local_min = cv2.erode(np.where(depth > 0, depth, _BIG), _KERNEL)
        valid &= (local_max - local_min) <= edge_jump_ratio * depth
    return np.where(valid, raw, np.uint16(0)).astype(np.uint16)


OUT_OF_RANGE_BGR = (52, 46, 42)   # mesuré mais hors de la plage gardée (coupé)
NO_DEPTH_BGR = (18, 14, 11)       # pas de mesure


def colorize_depth(raw: np.ndarray, depth_scale: float, min_m: float, max_m: float) -> np.ndarray:
    """Image BGR (palette Turbo : proche = rouge, loin = bleu). Hors plage : gris sombre ; sans mesure : noir."""
    meters = raw.astype(np.float32) / depth_scale
    norm = np.clip((max_m - meters) / max(max_m - min_m, 1e-6), 0.0, 1.0)
    colored = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    colored[(meters < min_m) | (meters > max_m)] = OUT_OF_RANGE_BGR
    colored[raw == 0] = NO_DEPTH_BGR
    return colored


def format_distance(meters: float) -> str:
    """Distance lisible à la française : « 42 cm », « 1,35 m »."""
    return f"{meters * 100:.0f} cm" if meters < 1 else f"{meters:.2f} m".replace(".", ",")


def depth_overlay(color: np.ndarray, raw: np.ndarray, depth_scale: float, min_m: float, max_m: float,
                  alpha: float = 0.45) -> np.ndarray:
    """Couleur avec la profondeur mesurée (dans la plage utile) superposée en transparence."""
    colored = colorize_depth(raw, depth_scale, min_m, max_m)
    inside = (raw >= min_m * depth_scale) & (raw <= max_m * depth_scale)
    out = color.copy()
    out[inside] = cv2.addWeighted(color, 1.0 - alpha, colored, alpha, 0)[inside]
    return out


def backproject(raw: np.ndarray, color_bgr: np.ndarray, fx: float, fy: float, ppx: float, ppy: float,
                depth_scale: float, step: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Nuage de points (repère caméra : x droite, y bas, z devant) d'une image sous-échantillonnée.

    Renvoie (points Nx3 float32 en mètres, couleurs Nx3 uint8 RGB).
    """
    depth = raw[::step, ::step].astype(np.float32) / depth_scale
    rows, cols = np.nonzero(depth > 0)
    z = depth[rows, cols]
    u, v = cols * step, rows * step
    points = np.stack([(u - ppx) * z / fx, (v - ppy) * z / fy, z], axis=1).astype(np.float32)
    colors = color_bgr[v, u][:, ::-1].copy()
    return points, colors


def center_distance_m(raw: np.ndarray, depth_scale: float) -> float | None:
    """Distance médiane (m) mesurée au centre de l'image, None si rien n'est mesuré."""
    h, w = raw.shape
    patch = raw[h // 2 - CENTER_HALF:h // 2 + CENTER_HALF, w // 2 - CENTER_HALF:w // 2 + CENTER_HALF]
    valid = patch[patch > 0]
    return float(np.median(valid)) / depth_scale if valid.size else None


def distance_hint(distance_m: float | None, near_m: float, far_m: float) -> DistanceHint:
    if distance_m is None:
        return DistanceHint("none", "Vise le sujet : rien n'est mesuré au centre", None)
    cm = format_distance(distance_m)
    if distance_m < near_m:
        return DistanceHint("near", f"Trop près · recule ({cm})", distance_m)
    if distance_m > far_m:
        return DistanceHint("far", f"Trop loin · rapproche-toi ({cm})", distance_m)
    return DistanceHint("ok", f"Distance idéale ({cm})", distance_m)


def valid_ratio(raw: np.ndarray) -> float:
    """Part des pixels où la caméra mesure une profondeur."""
    return float(np.count_nonzero(raw)) / raw.size if raw.size else 0.0
