"""Salle entière : retrait des poussières flottantes et mise à niveau du sol (z = 0, z vers le haut)."""
from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import open3d as o3d

from .profiles import RoomParams

MAX_PLANES = 6


@dataclass(frozen=True)
class ScannedRoom:
    mesh: o3d.geometry.TriangleMesh  # mètres, sol à z = 0 si trouvé, z vers le haut
    size_m: tuple[float, float, float]
    floor_found: bool
    transform: np.ndarray


def remove_floaters(mesh: o3d.geometry.TriangleMesh, min_ratio: float) -> o3d.geometry.TriangleMesh:
    """Retire les morceaux de moins de `min_ratio` des triangles (bruit isolé dans l'air)."""
    if len(mesh.triangles) == 0:
        return mesh
    clusters, counts, _ = mesh.cluster_connected_triangles()
    clusters, counts = np.asarray(clusters), np.asarray(counts)
    result = copy.deepcopy(mesh)
    result.remove_triangles_by_mask(counts[clusters] < min_ratio * len(clusters))
    result.remove_unreferenced_vertices()
    return result


def up_from_poses(poses: Sequence[np.ndarray]) -> np.ndarray:
    """Haut estimé : moyenne des « haut » de la caméra (-y image), tenue à peu près droite."""
    up = -np.mean([pose[:3, 1] for pose in poses], axis=0)
    norm = np.linalg.norm(up)
    return up / norm if norm > 1e-6 else np.array([0.0, -1.0, 0.0])


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Rotation qui amène le vecteur unitaire `a` sur `b` (formule de Rodrigues)."""
    v, c = np.cross(a, b), float(a @ b)
    if c < -1.0 + 1e-9:  # vecteurs opposés : demi-tour autour d'un axe perpendiculaire
        axis = np.cross(a, [1.0, 0.0, 0.0] if abs(a[0]) < 0.9 else [0.0, 1.0, 0.0])
        axis /= np.linalg.norm(axis)
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx / (1.0 + c)


def find_floor(points: np.ndarray, up: np.ndarray, p: RoomParams) -> np.ndarray | None:
    """Plan horizontal le plus bas et assez grand (le sol), normale orientée vers le haut."""
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points)).voxel_down_sample(p.plane_voxel_m)
    total = len(pcd.points)
    if total < 100:
        return None
    cos_max = math.cos(math.radians(p.max_floor_tilt_deg))
    best: tuple[float, np.ndarray] | None = None
    for _ in range(MAX_PLANES):
        if len(pcd.points) < 0.5 * p.min_floor_ratio * total or len(pcd.points) < 50:
            break
        plane, inliers = pcd.segment_plane(p.plane_dist_m, 3, 1000)
        plane = np.asarray(plane, dtype=np.float64) / np.linalg.norm(plane[:3])
        if plane[:3] @ up < 0:
            plane = -plane
        if len(inliers) >= p.min_floor_ratio * total and plane[:3] @ up >= cos_max:
            height = float(-plane[3])  # hauteur du plan le long de sa normale (≈ haut)
            if best is None or height < best[0]:
                best = (height, plane)
        pcd = pcd.select_by_index(inliers, invert=True)
    return None if best is None else best[1]


def level_room(mesh: o3d.geometry.TriangleMesh, poses: Sequence[np.ndarray], p: RoomParams) -> ScannedRoom:
    """Nettoie la scène et la pose sur son sol (z vers le haut)."""
    cleaned = remove_floaters(mesh, p.min_component_ratio)
    points = np.asarray(cleaned.vertices)
    if len(points) == 0:
        return ScannedRoom(cleaned, (0.0, 0.0, 0.0), False, np.eye(4))
    up = up_from_poses(poses)
    floor = find_floor(points, up, p)
    normal = floor[:3] if floor is not None else up
    transform = np.eye(4)
    transform[:3, :3] = rotation_between(normal, np.array([0.0, 0.0, 1.0]))
    rotated = points @ transform[:3, :3].T
    z0 = -floor[3] if floor is not None else float(rotated[:, 2].min())
    center = (rotated.max(axis=0) + rotated.min(axis=0)) / 2
    transform[:3, 3] = [-center[0], -center[1], -z0]
    leveled = copy.deepcopy(cleaned).transform(transform)
    extent = np.ptp(np.asarray(leveled.vertices), axis=0)
    return ScannedRoom(leveled, (float(extent[0]), float(extent[1]), float(extent[2])), floor is not None, transform)
