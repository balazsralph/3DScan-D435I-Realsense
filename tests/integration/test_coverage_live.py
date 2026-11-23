"""Couverture en direct sur scène synthétique : ce qui est vu longtemps devient « bien vu », le reste est signalé."""
import numpy as np
import open3d as o3d
import pytest

from scanner3d.profiles import build_profile
from scanner3d.reconstruction.coverage import GOOD, MISSING, WEAK, level_colors
from scanner3d.reconstruction.slam import DenseSlam, pick_device, prepare_frame
from tests.helpers.synthetic_scene import INTR, orbit_poses, render

pytestmark = pytest.mark.gpu


@pytest.fixture(scope="module")
def slam_and_frames():
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    poses = orbit_poses(600, 0.45, 0.30, np.array([0.0, 0.0, 0.04]))
    frames = render(poses[:100])
    profile = build_profile("objet")
    slam = DenseSlam(INTR, profile, 3.5, 10000.0, 1.0, pick_device())
    prepared = [prepare_frame(c, d, profile, 10000.0, 1.0) for c, d in frames]
    slam.process(*prepared[0], 0)
    first = slam.coverage_map(prepared[0][1])  # une seule image intégrée
    for i, (color, depth) in enumerate(prepared[1:], start=1):
        slam.process(color, depth, i)
    return slam, poses, profile, prepared, first


def test_coverage_grows_with_observations(slam_and_frames) -> None:
    slam, _poses, _profile, prepared, first = slam_and_frames
    after = slam.coverage_map(prepared[-1][1])
    assert first.good_ratio == 0.0 and np.count_nonzero(first.grid == WEAK) > 0
    assert after.good_ratio > 0.6


def test_unseen_side_is_flagged(slam_and_frames) -> None:
    slam, poses, profile, _prepared, _first = slam_and_frames
    color, depth = prepare_frame(*render([poses[400]])[0], profile, 10000.0, 1.0)
    slam._pose = np.linalg.inv(poses[0]) @ poses[400]  # caméra placée de l'autre côté, pose exacte
    seen = slam.coverage_map(depth)
    assert np.count_nonzero(seen.grid == MISSING) > 50
    assert seen.to_scan_ratio > 0.2


def test_coverage_points_are_colored_by_level(slam_and_frames) -> None:
    slam, *_ = slam_and_frames
    points, colors, good_ratio = slam.extract_coverage_points(50_000)
    assert len(points) == len(colors) > 1000 and len(points) <= 50_000
    assert 0.0 < good_ratio <= 1.0
    assert len({tuple(c) for c in colors[:: max(1, len(colors) // 2000)]}) >= 2
    assert tuple(level_colors(np.array([GOOD], np.uint8))[0]) in {tuple(c) for c in colors}
