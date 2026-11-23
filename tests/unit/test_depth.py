import numpy as np
import pytest

from scanner3d.depth import (
    NO_DEPTH_BGR,
    OUT_OF_RANGE_BGR,
    backproject,
    center_distance_m,
    clean_depth,
    colorize_depth,
    depth_overlay,
    distance_hint,
    format_distance,
    valid_ratio,
)

SCALE = 10000.0  # 0,1 mm


def flat(value_m: float, shape: tuple[int, int] = (48, 64)) -> np.ndarray:
    return np.full(shape, int(value_m * SCALE), dtype=np.uint16)


def test_clean_depth_removes_out_of_range() -> None:
    depth = flat(0.30)
    depth[0, 0] = int(0.05 * SCALE)   # trop près
    depth[0, 1] = int(0.90 * SCALE)   # trop loin
    cleaned = clean_depth(depth, SCALE, 0.10, 0.50, edge_jump_ratio=0.0)
    assert cleaned[0, 0] == 0 and cleaned[0, 1] == 0
    assert cleaned[10, 10] == depth[10, 10]


def test_clean_depth_removes_flying_pixels_at_edges() -> None:
    depth = flat(0.30)
    depth[:, 32:] = int(0.60 * SCALE)          # fond derrière un bord vertical
    depth[20, 31] = int(0.45 * SCALE)          # pixel « volant » à mi-chemin
    cleaned = clean_depth(depth, SCALE, 0.10, 1.0, edge_jump_ratio=0.03)
    assert cleaned[20, 31] == 0
    assert cleaned[20, 10] == depth[20, 10] and cleaned[20, 50] == depth[20, 50]  # surfaces intactes


def test_clean_depth_keeps_pixels_next_to_holes() -> None:
    depth = flat(0.30)
    depth[20, 20] = 0
    cleaned = clean_depth(depth, SCALE, 0.10, 1.0, edge_jump_ratio=0.03)
    assert cleaned[20, 21] == depth[20, 21] and cleaned[19, 20] == depth[19, 20]


def test_clean_depth_keeps_gentle_slopes() -> None:
    rows = np.linspace(0.30, 0.33, 48)[:, None]  # surface inclinée : 0,6 mm par ligne
    depth = np.repeat(rows, 64, axis=1)
    raw = (depth * SCALE).astype(np.uint16)
    assert valid_ratio(clean_depth(raw, SCALE, 0.10, 1.0, edge_jump_ratio=0.03)) == 1.0


def test_clean_depth_rejects_bad_dtype() -> None:
    with pytest.raises(ValueError):
        clean_depth(np.zeros((4, 4), np.float32), SCALE, 0.1, 1.0, 0.03)


def test_colorize_marks_missing_and_cut_depth() -> None:
    depth = flat(0.30)
    depth[0, 0] = 0
    depth[0, 1] = int(0.90 * SCALE)
    colored = colorize_depth(depth, SCALE, 0.10, 0.50)
    assert tuple(colored[0, 0]) == NO_DEPTH_BGR
    assert tuple(colored[0, 1]) == OUT_OF_RANGE_BGR
    assert colored.shape == (48, 64, 3) and colored.dtype == np.uint8


def test_overlay_only_tints_measured_range() -> None:
    color = np.full((48, 64, 3), 100, np.uint8)
    depth = flat(0.30)
    depth[:, :10] = 0
    out = depth_overlay(color, depth, SCALE, 0.10, 0.50)
    assert np.array_equal(out[:, :10], color[:, :10])
    assert not np.array_equal(out[:, 20:], color[:, 20:])


def test_backproject_center_pixel_lies_on_optical_axis() -> None:
    depth = np.zeros((48, 64), np.uint16)
    depth[24, 32] = int(0.5 * SCALE)
    color = np.zeros((48, 64, 3), np.uint8)
    color[24, 32] = (10, 20, 30)  # BGR
    points, colors = backproject(depth, color, fx=60.0, fy=60.0, ppx=32.0, ppy=24.0, depth_scale=SCALE, step=1)
    assert points.shape == (1, 3)
    assert points[0] == pytest.approx([0.0, 0.0, 0.5])
    assert tuple(colors[0]) == (30, 20, 10)  # RGB


def test_backproject_off_axis_geometry() -> None:
    depth = np.zeros((48, 64), np.uint16)
    depth[24, 62] = int(1.0 * SCALE)
    points, _ = backproject(depth, np.zeros((48, 64, 3), np.uint8), 60.0, 60.0, 32.0, 24.0, SCALE, step=1)
    assert points[0] == pytest.approx([0.5, 0.0, 1.0])


def test_center_distance() -> None:
    assert center_distance_m(flat(0.20, (480, 848)), SCALE) == pytest.approx(0.20)
    assert center_distance_m(np.zeros((480, 848), np.uint16), SCALE) is None


@pytest.mark.parametrize(("distance", "level"), [(None, "none"), (0.10, "near"), (0.40, "ok"), (2.0, "far")])
def test_distance_hint_levels(distance: float | None, level: str) -> None:
    assert distance_hint(distance, 0.30, 0.65).level == level


def test_distance_formatting_is_french() -> None:
    assert format_distance(0.42) == "42 cm"
    assert format_distance(1.354) == "1,35 m"
    assert "4,48 m" in distance_hint(4.48, 0.3, 0.65).text
