"""Couverture du scan : ce que le modèle a déjà bien vu, et ce qui reste à scanner.

Chaque voxel de la TSDF compte ses observations (une par image intégrée). Depuis la pose courante,
on lit ce compte sur une grille de points posée sur l'image caméra :
- MISSING : la caméra voit une surface que le modèle n'a pas encore ;
- WEAK / PARTIAL : surface vue peu de fois, à repasser ;
- GOOD : surface vue assez longtemps (≈ 1,5 s à 30 img/s), bruit moyenné.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

NONE, MISSING, WEAK, PARTIAL, GOOD = 0, 1, 2, 3, 4
WEAK_MAX = 12       # moins de 12 observations : faible
GOOD_MIN = 45       # au moins 45 observations : bien couvert
CELL_PX = 16        # un point de la grille tous les 16 pixels
MIN_FILL = 0.3      # part de pixels mesurés pour qu'une case compte comme « vue par la caméra »

LEVEL_RGB = {
    MISSING: (255, 90, 95),
    WEAK: (255, 90, 95),
    PARTIAL: (245, 165, 36),
    GOOD: (61, 220, 151),
}


@dataclass(frozen=True)
class CoverageMap:
    grid: np.ndarray        # (lignes, colonnes) uint8, niveaux NONE..GOOD
    good_ratio: float       # part des cases avec surface qui sont bien couvertes

    @property
    def to_scan_ratio(self) -> float:
        surface = self.grid != NONE
        return float(np.count_nonzero(self.grid[surface] <= WEAK)) / max(int(np.count_nonzero(surface)), 1)


def grid_shape(width: int, height: int, cell_px: int = CELL_PX) -> tuple[int, int]:
    return max(height // cell_px, 1), max(width // cell_px, 1)


def frame_valid_grid(depth: np.ndarray, rows: int, cols: int, min_fill: float = MIN_FILL) -> np.ndarray:
    """Cases de la grille où la caméra mesure assez de profondeur."""
    h, w = depth.shape
    ch, cw = h // rows, w // cols
    valid = (depth[:rows * ch, :cols * cw] > 0).reshape(rows, ch, cols, cw)
    return valid.mean(axis=(1, 3)) >= min_fill


def classify_cells(model_weight: np.ndarray, model_depth: np.ndarray, frame_valid: np.ndarray) -> np.ndarray:
    """Niveau de chaque case à partir du poids TSDF raycasté et des mesures de l'image courante."""
    surface = (model_depth > 0) & (model_weight >= 1.0)
    grid = np.full(model_weight.shape, NONE, np.uint8)
    grid[frame_valid & ~surface] = MISSING
    grid[surface & (model_weight < WEAK_MAX)] = WEAK
    grid[surface & (model_weight >= WEAK_MAX) & (model_weight < GOOD_MIN)] = PARTIAL
    grid[surface & (model_weight >= GOOD_MIN)] = GOOD
    return grid


def coverage_from(grid: np.ndarray) -> CoverageMap:
    surface = grid != NONE
    good = np.count_nonzero(grid == GOOD) / max(int(np.count_nonzero(surface)), 1)
    return CoverageMap(grid, float(good))


def position_keys(points: np.ndarray, quantum: float) -> np.ndarray:
    """Clé entière par position (les extractions successives donnent exactement les mêmes points)."""
    q = np.round(points / quantum).astype(np.int64) + (1 << 20)
    return (q[:, 0] << 42) | (q[:, 1] << 21) | q[:, 2]


def point_levels(all_points: np.ndarray, partial_points: np.ndarray, good_points: np.ndarray,
                 quantum: float) -> np.ndarray:
    """Niveau (WEAK, PARTIAL, GOOD) de chaque point de `all_points`.

    `partial_points` et `good_points` sont les surfaces extraites avec des seuils d'observations plus
    élevés : ce sont des sous-ensembles exacts de `all_points`.
    """
    keys = position_keys(all_points, quantum)
    levels = np.full(len(all_points), WEAK, np.uint8)
    if len(partial_points):
        levels[np.isin(keys, position_keys(partial_points, quantum))] = PARTIAL
    if len(good_points):
        levels[np.isin(keys, position_keys(good_points, quantum))] = GOOD
    return levels


def level_colors(levels: np.ndarray) -> np.ndarray:
    palette = np.zeros((GOOD + 1, 3), np.uint8)
    for level, rgb in LEVEL_RGB.items():
        palette[level] = rgb
    return palette[levels]
