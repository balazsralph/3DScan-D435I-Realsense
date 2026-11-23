import numpy as np
import pytest

from scanner3d.reconstruction.coverage import (
    GOOD,
    GOOD_MIN,
    MISSING,
    NONE,
    PARTIAL,
    WEAK,
    WEAK_MAX,
    classify_cells,
    coverage_from,
    frame_valid_grid,
    grid_shape,
    level_colors,
    point_levels,
)


def test_grid_shape_for_realsense_stream() -> None:
    assert grid_shape(848, 480) == (30, 53)


def test_frame_valid_grid_needs_enough_measured_pixels() -> None:
    depth = np.zeros((32, 64), np.uint16)
    depth[:16, :16] = 1000          # case (0, 0) entièrement mesurée
    depth[16:, 16:20] = 1000        # case (1, 1) : 25 % seulement
    grid = frame_valid_grid(depth, 2, 4)
    assert grid.tolist() == [[True, False, False, False], [False, False, False, False]]


def test_classify_cells_levels() -> None:
    weight = np.array([[0.0, 3.0, WEAK_MAX + 1.0, GOOD_MIN + 5.0, 0.0]])
    model_depth = np.array([[0.0, 0.4, 0.4, 0.4, 0.0]])
    frame_valid = np.array([[True, True, True, True, False]])
    assert classify_cells(weight, model_depth, frame_valid).tolist() == [[MISSING, WEAK, PARTIAL, GOOD, NONE]]


def test_coverage_ratios() -> None:
    cov = coverage_from(np.array([[NONE, MISSING, WEAK, PARTIAL, GOOD, GOOD]], np.uint8))
    assert cov.good_ratio == pytest.approx(2 / 5)
    assert cov.to_scan_ratio == pytest.approx(2 / 5)


def test_point_levels_match_nested_extractions() -> None:
    rng = np.random.default_rng(0)
    everything = rng.uniform(-0.1, 0.1, (500, 3)).astype(np.float32)
    partial, good = everything[100:], everything[300:]   # extractions à seuils croissants : sous-ensembles
    levels = point_levels(everything, partial, good, quantum=1e-5)
    assert (levels[:100] == WEAK).all() and (levels[100:300] == PARTIAL).all() and (levels[300:] == GOOD).all()
    colors = level_colors(levels)
    assert colors.shape == (500, 3) and tuple(colors[0]) != tuple(colors[-1])


def test_point_levels_with_empty_subsets() -> None:
    pts = np.zeros((3, 3), np.float32)
    assert (point_levels(pts, np.zeros((0, 3)), np.zeros((0, 3)), 1e-5) == WEAK).all()
