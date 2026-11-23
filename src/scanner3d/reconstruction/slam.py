"""Suivi image -> modèle (SLAM dense Open3D) et fusion TSDF, sur GPU si disponible.

Si le suivi décroche, le scan est coupé en morceaux plutôt que de risquer un mauvais
raccrochage (qui dédoublerait le sujet). Les morceaux sont recalés ensuite (registration.py).
Repris et généralisé du projet scan3d (garde-fous mesurés sur de vrais scans).
"""
from __future__ import annotations

import math
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import cv2
import numpy as np
import open3d as o3d
import open3d.core as o3c

from ..dataset import FramePair, Intrinsics, read_frame
from ..depth import clean_depth
from ..profiles import ScanProfile

ProgressFn = Callable[[float, str], None]
PreviewFn = Callable[[np.ndarray, np.ndarray], None]


class Cancelled(Exception):  # noqa: N818 (pas une erreur : demande de l'utilisateur)
    """Reconstruction annulée par l'utilisateur."""


@dataclass(frozen=True)
class Fragment:
    indices: tuple[int, ...]        # indices (dans la liste d'images) des images intégrées
    poses: tuple[np.ndarray, ...]   # pose caméra -> monde, repère de la 1re image du morceau


@dataclass(frozen=True)
class TrackStatus:
    accepted: bool
    fitness: float
    lost_streak: int


def pick_device() -> o3c.Device:
    return o3c.Device("CUDA:0") if o3c.cuda.is_available() else o3c.Device("CPU:0")


def step_is_plausible(delta: np.ndarray, max_translation_m: float, max_rotation_deg: float,
                      frames_elapsed: int = 1) -> bool:
    """Rejette les sauts de pose impossibles depuis la dernière image acceptée.

    Le seuil grandit avec le nombre d'images écoulées : après des rejets, la caméra a eu
    le temps de bouger davantage (sinon un seul rejet entraînerait tous les suivants).
    """
    translation = float(np.linalg.norm(delta[:3, 3]))
    cos_angle = np.clip((np.trace(delta[:3, :3]) - 1.0) / 2.0, -1.0, 1.0)
    rotation_deg = math.degrees(math.acos(cos_angle))
    return (translation <= max_translation_m * frames_elapsed
            and rotation_deg <= max_rotation_deg * frames_elapsed)


def relative_fitness(fitness: float, valid_ratio: float) -> float:
    """Part des pixels mesurés qui coïncident avec le modèle (le fitness d'Open3D est rapporté à l'image)."""
    return min(fitness / valid_ratio, 1.0) if valid_ratio > 1e-3 else 0.0


def intrinsic_tensor(intr: Intrinsics) -> o3c.Tensor:
    return o3c.Tensor(intr.matrix(), o3c.float64)


def prepare_frame(color_bgr: np.ndarray, raw_depth: np.ndarray, profile: ScanProfile, depth_scale: float,
                  depth_max_m: float) -> tuple[np.ndarray, np.ndarray]:
    """(couleur RGB contiguë, profondeur nettoyée) prêtes pour Open3D."""
    depth = clean_depth(raw_depth, depth_scale, profile.depth_min_m, depth_max_m, profile.edge_jump_ratio)
    return np.ascontiguousarray(cv2.cvtColor(color_bgr, cv2.COLOR_BGR2RGB)), depth


def load_into(frame: o3d.t.pipelines.slam.Frame, color_rgb: np.ndarray, depth: np.ndarray,
              device: o3c.Device) -> None:
    frame.set_data_from_image("depth", o3d.t.geometry.Image(o3c.Tensor(depth)).to(device))
    frame.set_data_from_image("color", o3d.t.geometry.Image(o3c.Tensor(color_rgb)).to(device))


def new_model(voxel_mm: float, profile: ScanProfile, device: o3c.Device) -> o3d.t.pipelines.slam.Model:
    return o3d.t.pipelines.slam.Model(voxel_mm / 1000.0, 16, profile.block_count, o3c.Tensor(np.eye(4)), device)


class DenseSlam:
    """Suivi d'un morceau de scan avec garde-fous ; une instance = un morceau.

    Prédiction à vitesse constante (comme KinectFusion) : le modèle est raycasté là où la caméra
    devrait se trouver à l'image suivante. L'odométrie projective d'Open3D ne rattrape qu'environ
    la moitié du mouvement le long d'une surface plane (table, sol) ; partir de la pose prédite
    ne lui laisse à corriger que l'accélération (mesuré sur scène synthétique : voir tests).
    """

    def __init__(self, intr: Intrinsics, profile: ScanProfile, voxel_mm: float, depth_scale: float,
                 depth_max_m: float, device: o3c.Device) -> None:
        self._profile, self._device = profile, device
        self._depth_scale, self._depth_max = depth_scale, depth_max_m
        self.voxel_mm = voxel_mm
        self._pose = np.eye(4)            # pose de la dernière image acceptée
        self._velocity = np.eye(4)        # mouvement entre les deux dernières images acceptées
        self._raycast_pose = np.eye(4)    # pose d'où le modèle a été raycasté (prédiction)
        self._model = new_model(voxel_mm, profile, device)
        k = intrinsic_tensor(intr)
        self._input = o3d.t.pipelines.slam.Frame(intr.height, intr.width, k, device)
        self._raycast = o3d.t.pipelines.slam.Frame(intr.height, intr.width, k, device)
        self._method = getattr(o3d.t.pipelines.odometry.Method, profile.tracking.odometry_method)
        self._raycast_color = self._method == o3d.t.pipelines.odometry.Method.Hybrid
        self.indices: list[int] = []
        self.poses: list[np.ndarray] = []
        self.lost_streak = 0

    def _track(self, valid_ratio: float) -> tuple[np.ndarray | None, float]:
        """Nouvelle pose de l'image courante (ou None si rejetée) et part des pixels mesurés qui coïncident.

        Open3D calcule le « fitness » sur toute l'image : avec le fond coupé (24 % de pixels mesurés),
        il plafonne à 0,24 même quand le suivi est parfait. On le rapporte donc aux pixels mesurés.
        """
        t = self._profile.tracking
        try:
            result = self._model.track_frame_to_model(self._input, self._raycast, self._depth_scale,
                                                      self._depth_max, t.depth_diff_m, self._method)
        except RuntimeError:  # système singulier : pas assez de correspondances dans cette image
            return None, 0.0
        fitness = relative_fitness(float(result.fitness), valid_ratio)
        pose = self._raycast_pose @ result.transformation.cpu().numpy()
        step = np.linalg.inv(self._pose) @ pose
        plausible = step_is_plausible(step, t.max_step_translation_m, t.max_step_rotation_deg, self.lost_streak + 1)
        if fitness < t.min_fitness or not plausible:
            return None, fitness
        return pose, fitness

    def process(self, color_rgb: np.ndarray, depth: np.ndarray, index: int) -> TrackStatus:
        """Suit puis intègre une image (déjà préparée). Une image rejetée n'est pas intégrée."""
        load_into(self._input, color_rgb, depth, self._device)
        fitness = 1.0
        if self.poses:
            pose, fitness = self._track(np.count_nonzero(depth) / depth.size)
            if pose is None:
                self.lost_streak += 1
                return TrackStatus(False, fitness, self.lost_streak)
            # après des images rejetées, la vitesse n'est plus fiable : on repart sans prédiction
            self._velocity = np.linalg.inv(self._pose) @ pose if self.lost_streak == 0 else np.eye(4)
            self._pose = pose
        self.lost_streak = 0
        trunc = self._profile.tracking_trunc_multiplier
        frame_id = len(self.poses)
        self._model.update_frame_pose(frame_id, o3c.Tensor(self._pose))
        self._model.integrate(self._input, self._depth_scale, self._depth_max, trunc)
        self._raycast_pose = self._pose @ self._velocity
        self._model.update_frame_pose(frame_id, o3c.Tensor(self._raycast_pose))
        self._model.synthesize_model_frame(self._raycast, self._depth_scale, self._profile.depth_min_m,
                                           self._depth_max, trunc, self._raycast_color)
        self.indices.append(index)
        self.poses.append(self._pose.copy())
        return TrackStatus(True, fitness, 0)

    def fragment(self) -> Fragment:
        return Fragment(tuple(self.indices), tuple(self.poses))

    def current_pose(self) -> np.ndarray:
        return self._pose.copy()

    def extract_points(self, max_points: int = 400_000) -> tuple[np.ndarray, np.ndarray]:
        """Surface reconstruite jusqu'ici : (points Nx3 float32, couleurs Nx3 uint8)."""
        pcd = self._model.extract_pointcloud(min(self._profile.weight_threshold, 2.0))
        if pcd.is_empty():
            return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint8)
        count = pcd.point.positions.shape[0]
        if count > max_points:
            pcd = pcd.voxel_down_sample(self.voxel_mm / 1000.0 * math.ceil((count / max_points) ** 0.5 * 1.2))
        points = pcd.point.positions.cpu().numpy().astype(np.float32)
        if "colors" in pcd.point:
            colors = np.clip(pcd.point.colors.cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
        else:
            colors = np.full((len(points), 3), 200, np.uint8)
        return points, colors


class FrameLoader:
    """Lit et prépare les images d'un dataset."""

    def __init__(self, frames: Sequence[FramePair], profile: ScanProfile, depth_scale: float,
                 depth_max_m: float) -> None:
        self.frames = frames
        self.profile, self.depth_scale, self.depth_max_m = profile, depth_scale, depth_max_m

    def __len__(self) -> int:
        return len(self.frames)

    def load(self, index: int) -> tuple[np.ndarray, np.ndarray]:
        color, raw = read_frame(self.frames[index])
        return prepare_frame(color, raw, self.profile, self.depth_scale, self.depth_max_m)


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def track_fragments(loader: FrameLoader, intr: Intrinsics, profile: ScanProfile, voxel_mm: float,
                    device: o3c.Device, progress: ProgressFn | None = None, preview: PreviewFn | None = None,
                    cancel: threading.Event | None = None, preview_every: int = 60) -> list[Fragment]:
    """Suit toutes les images ; coupe en un nouveau morceau quand le suivi est perdu trop longtemps."""
    fragments: list[Fragment] = []
    slam: DenseSlam | None = None
    breaks = 0
    total = len(loader)
    for i in range(total):
        _check(cancel)
        slam = slam or DenseSlam(intr, profile, voxel_mm, loader.depth_scale, loader.depth_max_m, device)
        status = slam.process(*loader.load(i), i)
        if not status.accepted and slam.lost_streak >= profile.tracking.fragment_break_frames:
            if slam.indices:
                fragments.append(slam.fragment())
            slam = None  # libère la mémoire du GPU avant le morceau suivant
            breaks += 1
        if progress and (i % 10 == 0 or i == total - 1):
            progress((i + 1) / total, f"Suivi de la caméra : image {i + 1}/{total}"
                                      + (f" · {breaks} décrochage(s)" if breaks else ""))
        if preview and slam is not None and i % preview_every == preview_every - 1:
            preview(*slam.extract_points(250_000))
    if slam is not None and slam.indices:
        fragments.append(slam.fragment())
    return fragments


def integrate_frames(loader: FrameLoader, indices: Sequence[int], poses: Sequence[np.ndarray], intr: Intrinsics,
                     profile: ScanProfile, voxel_mm: float, device: o3c.Device,
                     progress: ProgressFn | None = None,
                     cancel: threading.Event | None = None) -> o3d.geometry.TriangleMesh:
    """Fusionne des images dont les poses sont connues (sans refaire le suivi)."""
    model = new_model(voxel_mm, profile, device)
    frame = o3d.t.pipelines.slam.Frame(intr.height, intr.width, intrinsic_tensor(intr), device)
    total = len(indices)
    for n, (index, pose) in enumerate(zip(indices, poses, strict=True)):
        _check(cancel)
        load_into(frame, *loader.load(index), device)
        model.update_frame_pose(n, o3c.Tensor(pose))
        model.integrate(frame, loader.depth_scale, loader.depth_max_m, profile.trunc_voxel_multiplier)
        if progress and (n % 20 == 0 or n == total - 1):
            progress((n + 1) / total, f"Fusion TSDF : image {n + 1}/{total}")
    return model.extract_trianglemesh(profile.weight_threshold).to_legacy()
