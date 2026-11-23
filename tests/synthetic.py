"""Scène synthétique (table + boîte de dimensions connues) rendue en images RGB-D par lancer de rayons."""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import open3d as o3d
import open3d.core as o3c

from scanner3d.dataset import Intrinsics, SessionInfo, write_session

INTR = Intrinsics(424, 240, 302.0, 302.0, 212.0, 120.0)
BOX_MM = (150.0, 100.0, 80.0)
BOX_YAW_DEG = 25.0
TABLE_BGR = (150, 180, 200)
BOX_BGR = (40, 40, 200)


def look_at(position: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Pose caméra -> monde (convention RealSense/OpenCV : x droite, y bas, z devant)."""
    forward = (target - position) / np.linalg.norm(target - position)
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    pose = np.eye(4)
    pose[:3, :3] = np.column_stack([right, down, forward])
    pose[:3, 3] = position
    return pose


def orbit_poses(count: int, radius: float, height: float, target: np.ndarray) -> list[np.ndarray]:
    poses = []
    for i in range(count):
        a = 2 * math.pi * i / count
        poses.append(look_at(np.array([radius * math.cos(a), radius * math.sin(a), height]), target))
    return poses


# Objets posés plus loin sur la table (rayon, hauteur, largeur en m) : comme dans une vraie pièce, ils
# donnent du relief au suivi. Une boîte seule sur un plan est un cas dégénéré pour le suivi point-à-plan.
CLUTTER = ((0.30, 0.12, 0.05), (0.32, 0.06, 0.08), (0.28, 0.20, 0.04), (0.33, 0.09, 0.06), (0.30, 0.15, 0.03))


def scene_meshes(clutter: bool = True) -> tuple[o3d.geometry.TriangleMesh, o3d.geometry.TriangleMesh]:
    """(table et objets alentour, boîte à scanner)."""
    table = o3d.geometry.TriangleMesh.create_box(1.2, 1.2, 0.02).translate((-0.6, -0.6, -0.02))
    for i, (radius, height, width) in enumerate(CLUTTER if clutter else ()):
        a = 2 * math.pi * i / len(CLUTTER) + 0.3
        table += o3d.geometry.TriangleMesh.create_box(width, width * 1.5, height).translate(
            (radius * math.cos(a) - width / 2, radius * math.sin(a) - width * 0.75, 0.0))
    bx, by, bz = (v / 1000 for v in BOX_MM)
    box = o3d.geometry.TriangleMesh.create_box(bx, by, bz).translate((-bx / 2, -by / 2, 0.0))
    yaw = math.radians(BOX_YAW_DEG)
    box.rotate(o3d.geometry.get_rotation_matrix_from_xyz((0.0, 0.0, yaw)), center=(0.0, 0.0, 0.0))
    return table, box


def render(poses: list[np.ndarray], intr: Intrinsics = INTR, depth_scale: float = 10000.0,
           noise_m: float = 0.0003, seed: int = 0) -> list[tuple[np.ndarray, np.ndarray]]:
    """Images (couleur BGR, profondeur uint16) vues depuis chaque pose."""
    scene = o3d.t.geometry.RaycastingScene()
    table, box = scene_meshes()
    ids = [scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(m)) for m in (table, box)]
    # Open3D lance ses rayons par le centre des pixels (u + 0,5) alors que la rétroprojection de la
    # profondeur (RealSense, Open3D) utilise (u, v) : on décale le point principal d'un demi-pixel.
    k = intr.matrix()
    k[0, 2] -= 0.5
    k[1, 2] -= 0.5
    k = o3c.Tensor(k, o3c.float64)
    rng = np.random.default_rng(seed)
    frames = []
    for pose in poses:
        extrinsic = o3c.Tensor(np.linalg.inv(pose), o3c.float64)
        rays = scene.create_rays_pinhole(k, extrinsic, intr.width, intr.height)
        hit = scene.cast_rays(rays)
        t = hit["t_hit"].numpy()
        r = rays.numpy()
        points = r[..., :3] + r[..., 3:] * np.where(np.isfinite(t), t, 0.0)[..., None]
        hit_mask = np.isfinite(t)
        points = np.where(hit_mask[..., None], points, 0.0)
        cam = (points - pose[:3, 3]) @ pose[:3, :3]  # repère caméra
        z = np.where(hit_mask, cam[..., 2], 0.0)
        z = np.where(z > 0, z + rng.normal(0.0, noise_m, z.shape), 0.0)
        depth = np.clip(z * depth_scale, 0, 65535).astype(np.uint16)
        geometry = hit["geometry_ids"].numpy()
        color = np.zeros((intr.height, intr.width, 3), np.uint8)
        color[geometry == ids[0]] = TABLE_BGR
        color[geometry == ids[1]] = BOX_BGR
        frames.append((color, depth))
    return frames


def write_dataset(path: Path, frames: list[tuple[np.ndarray, np.ndarray]], profile: str = "objet",
                  depth_max_m: float = 1.0) -> Path:
    (path / "couleur").mkdir(parents=True)
    (path / "profondeur").mkdir()
    for i, (color, depth) in enumerate(frames):
        cv2.imwrite(str(path / "couleur" / f"{i:06d}.jpg"), color, [cv2.IMWRITE_JPEG_QUALITY, 95])
        cv2.imwrite(str(path / "profondeur" / f"{i:06d}.png"), depth)
    write_session(path, SessionInfo(profile=profile, intrinsics=INTR, depth_scale=10000.0, depth_min_m=0.2,
                                    depth_max_m=depth_max_m, frames=len(frames), dropped=0, duration_s=0.0,
                                    created=datetime.now().isoformat(timespec="seconds")))
    return path
