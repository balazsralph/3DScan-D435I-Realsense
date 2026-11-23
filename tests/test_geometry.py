import math

import numpy as np
import open3d as o3d
import pytest

from scanner3d.extraction import focus_point, plane_basis, signed_distance, support_transform
from scanner3d.profiles import RoomParams
from scanner3d.room import level_room, remove_floaters, rotation_between, up_from_poses
from scanner3d.slam import relative_fitness, step_is_plausible
from tests.synthetic import look_at, orbit_poses


def pose(translation: float = 0.0, rotation_deg: float = 0.0) -> np.ndarray:
    c, s = math.cos(math.radians(rotation_deg)), math.sin(math.radians(rotation_deg))
    m = np.eye(4)
    m[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    m[0, 3] = translation
    return m


@pytest.mark.parametrize(("translation", "rotation", "expected"), [
    (0.0, 0.0, True), (0.005, 3.0, True), (0.02, 0.0, False), (0.0, 10.0, False)])
def test_step_is_plausible(translation: float, rotation: float, expected: bool) -> None:
    assert step_is_plausible(pose(translation, rotation), 0.010, 6.0) is expected


def test_step_threshold_grows_after_rejected_frames() -> None:
    assert step_is_plausible(pose(0.02), 0.010, 6.0, frames_elapsed=3) is True
    assert step_is_plausible(pose(0.02), 0.010, 6.0, frames_elapsed=1) is False


def test_relative_fitness_accounts_for_cut_background() -> None:
    """Fond coupé : 22 % de l'image coïncide sur 24 % mesurés = suivi excellent (92 %)."""
    assert relative_fitness(0.22, 0.24) == pytest.approx(0.9167, abs=1e-3)
    assert relative_fitness(0.5, 0.4) == 1.0
    assert relative_fitness(0.1, 0.0) == 0.0


@pytest.mark.parametrize("vector", [[1, 0, 0], [0, -1, 0], [0.3, 0.4, -0.866], [0, 0, -1], [0, 0, 1]])
def test_rotation_between_maps_vector_onto_z(vector: list[float]) -> None:
    a = np.asarray(vector, float) / np.linalg.norm(vector)
    rot = rotation_between(a, np.array([0.0, 0.0, 1.0]))
    assert rot @ a == pytest.approx([0.0, 0.0, 1.0], abs=1e-9)
    assert rot @ rot.T == pytest.approx(np.eye(3), abs=1e-9)
    assert np.linalg.det(rot) == pytest.approx(1.0)


def test_up_from_poses_follows_camera_holding() -> None:
    poses = orbit_poses(12, 2.0, 1.5, np.array([0.0, 0.0, 1.5]))  # caméra horizontale, monde z vers le haut
    assert up_from_poses(poses) == pytest.approx([0.0, 0.0, 1.0], abs=1e-6)


def test_focus_point_finds_orbit_center() -> None:
    target = np.array([0.1, -0.05, 0.02])
    assert focus_point(orbit_poses(24, 0.4, 0.3, target), 50.0) == pytest.approx(target, abs=1e-6)
    straight = [look_at(np.array([0.0, 0.0, 0.5 + i * 0.01]), np.zeros(3)) for i in range(5)]
    assert focus_point(straight, 50.0) is None  # caméra qui ne tourne pas : point visé indéterminé


def test_support_transform_lays_object_flat_and_measures_footprint() -> None:
    rng = np.random.default_rng(0)
    local = rng.uniform([-0.03, -0.01, 0.0], [0.03, 0.01, 0.02], (2000, 3))  # 60 x 20 x 20 mm
    yaw = math.radians(30)
    rz = np.array([[math.cos(yaw), -math.sin(yaw), 0], [math.sin(yaw), math.cos(yaw), 0], [0, 0, 1]])
    tilt = rotation_between(np.array([0.0, 0.0, 1.0]), np.array([0.0, -0.6, -0.8]))  # support incliné
    points = local @ rz.T @ tilt.T
    normal = tilt @ np.array([0.0, 0.0, 1.0])
    plane = np.append(normal, 0.0)
    transform, length, width = support_transform(points, plane)
    flat = points @ transform[:3, :3].T + transform[:3, 3]
    assert length == pytest.approx(0.06, abs=1e-3) and width == pytest.approx(0.02, abs=1e-3)
    assert flat[:, 2].min() == pytest.approx(0.0, abs=1e-3)
    assert signed_distance(points, plane).min() >= -1e-9
    assert plane_basis(normal)[2] == pytest.approx(normal)


def room_mesh() -> o3d.geometry.TriangleMesh:
    floor = o3d.geometry.TriangleMesh.create_box(4.0, 3.0, 0.02).translate((-2.0, -1.5, -0.02))
    wall = o3d.geometry.TriangleMesh.create_box(4.0, 0.02, 2.5).translate((-2.0, 1.5, 0.0))
    side = o3d.geometry.TriangleMesh.create_box(0.02, 3.0, 2.5).translate((2.0, -1.5, 0.0))
    mesh = floor + wall + side
    return mesh.subdivide_midpoint(5)


def test_level_room_puts_floor_at_zero() -> None:
    world = room_mesh()
    poses_world = orbit_poses(16, 1.0, 1.4, np.array([0.0, 0.0, 1.4]))
    # scène exprimée dans le repère de la 1re caméra, comme en sortie du SLAM
    to_first = np.linalg.inv(poses_world[0])
    scene = o3d.geometry.TriangleMesh(world).transform(to_first)
    poses = [to_first @ p for p in poses_world]
    room = level_room(scene, poses, RoomParams())
    vertices = np.asarray(room.mesh.vertices)
    assert room.floor_found
    assert vertices[:, 2].min() == pytest.approx(-0.02, abs=0.03)
    assert room.size_m[2] == pytest.approx(2.52, abs=0.05)
    assert sorted(room.size_m[:2]) == pytest.approx([3.02, 4.02], abs=0.05)


def test_remove_floaters_drops_small_islands() -> None:
    big = o3d.geometry.TriangleMesh.create_box(1, 1, 1).subdivide_midpoint(4)
    speck = o3d.geometry.TriangleMesh.create_tetrahedron(0.01).translate((3, 3, 3))
    cleaned = remove_floaters(big + speck, 0.01)
    assert len(cleaned.triangles) == len(big.triangles)
