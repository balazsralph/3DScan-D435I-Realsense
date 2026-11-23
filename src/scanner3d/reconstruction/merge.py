"""Fusion de plusieurs datasets en un seul modèle.

Cas d'usage : scanner un objet, le retourner et scanner son dessous ; compléter une salle en
plusieurs passages. Chaque dataset est d'abord reconstruit seul (objet isolé, ou salle nettoyée),
puis les modèles sont recalés sur le premier (RANSAC sur caractéristiques FPFH, puis ICP). Une
surface de Poisson unique et étanche est enfin calculée sur l'ensemble des points orientés.
Un dataset qui ne coïncide pas assez est écarté plutôt que de fausser le modèle.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import numpy as np
import open3d as o3d

from ..dataset import (
    BUILD_FILE,
    FUSION_FILE,
    Dataset,
    DatasetError,
    load_dataset,
    new_dataset_dir,
    thumbnail,
    write_thumbnail,
)
from ..profiles import PROFILES, build_profile
from .extraction import keep_main_components
from .pipeline import BuildOptions, build_dataset, export_meshes
from .registration import FragmentClouds, prepared, register
from .slam import Cancelled

log = logging.getLogger(__name__)
ProgressFn = Callable[[float, str], None]
STAGES = {"modeles": (0.0, 0.55), "recalage": (0.55, 0.75), "surface": (0.75, 1.0)}
MERGE_MIN_FITNESS = 0.25        # part du modèle ajouté qui doit retrouver le modèle de référence
MERGE_RMSE_FACTOR = 1.5         # tolérance d'écart par rapport au recalage des morceaux d'un scan
DENSITY_QUANTILE = 0.04         # Poisson : sommets les moins soutenus par des mesures, retirés
POISSON_DEPTH_RANGE = (6, 11)


@dataclass(frozen=True)
class MergeResult:
    path: Path
    model_path: Path
    size_mm: tuple[float, float, float]
    merged: tuple[str, ...]
    rejected: tuple[str, ...]
    triangles: int
    duration_s: float


def check_compatible(datasets: Sequence[Dataset]) -> str:
    """Type commun (« object » ou « room ») des datasets, ou ValueError."""
    if len(datasets) < 2:
        raise ValueError("Sélectionne au moins deux datasets à fusionner.")
    kinds = {PROFILES[d.profile_id].kind if d.profile_id in PROFILES else "?" for d in datasets}
    if len(kinds) != 1 or "?" in kinds:
        raise ValueError("Impossible de fusionner des objets et des salles ensemble.")
    return kinds.pop()


def model_cloud(dataset: Dataset) -> o3d.geometry.PointCloud:
    """Sommets du modèle (mètres) avec normales et couleurs."""
    info = dataset.build_info() or {}
    mesh = o3d.io.read_triangle_mesh(str(dataset.model_path))
    if len(mesh.vertices) == 0:
        raise DatasetError(f"{dataset.name} : modèle vide ou illisible")
    if info.get("units", "mm") == "mm":
        mesh.scale(0.001, center=np.zeros(3))
    mesh.compute_vertex_normals()
    cloud = o3d.geometry.PointCloud(mesh.vertices)
    cloud.normals = mesh.vertex_normals
    if mesh.has_vertex_colors():
        cloud.colors = mesh.vertex_colors
    return cloud


def poisson_depth(extent_m: float, voxel_m: float) -> int:
    """Profondeur de l'octree de Poisson : cellules un peu plus fines que le voxel du scan."""
    depth = math.ceil(math.log2(max(extent_m, voxel_m) / voxel_m)) + 1
    return int(min(max(depth, POISSON_DEPTH_RANGE[0]), POISSON_DEPTH_RANGE[1]))


def poisson_surface(cloud: o3d.geometry.PointCloud, voxel_m: float, is_object: bool) -> o3d.geometry.TriangleMesh:
    """Surface étanche passant par les points orientés ; les zones inventées (peu soutenues) sont retirées."""
    extent = float(np.max(cloud.get_max_bound() - cloud.get_min_bound()))
    mesh, densities = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(
        cloud, depth=poisson_depth(extent, voxel_m), scale=1.1)
    densities = np.asarray(densities)
    mesh.remove_vertices_by_mask(densities < np.quantile(densities, DENSITY_QUANTILE))
    box = cloud.get_axis_aligned_bounding_box()
    margin = np.full(3, 2 * voxel_m)
    mesh = mesh.crop(o3d.geometry.AxisAlignedBoundingBox(box.min_bound - margin, box.max_bound + margin))
    if is_object:
        mesh = keep_main_components(mesh, 0.05)
    mesh.compute_vertex_normals()
    return mesh


def _stage(progress: ProgressFn | None, name: str) -> ProgressFn:
    start, end = STAGES[name]

    def report(fraction: float, message: str) -> None:
        if progress:
            progress(start + (end - start) * min(max(fraction, 0.0), 1.0), message)
    return report


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def ensure_models(datasets: Sequence[Dataset], options: BuildOptions, progress: ProgressFn,
                  cancel: threading.Event | None, say: Callable[[str], None]) -> list[Dataset]:
    """Construit les modèles manquants ; renvoie les datasets rechargés."""
    ready = []
    for i, ds in enumerate(datasets):
        _check(cancel)
        if not ds.has_model:
            if ds.fmt == "fusion":
                raise DatasetError(f"{ds.name} : fusion sans modèle (refais-la d'abord).")
            say(f"Construction du modèle de {ds.name}...")

            def sub(fraction: float, message: str, i: int = i, name: str = ds.name) -> None:
                progress((i + fraction) / len(datasets), f"{name} · {message}")
            build_dataset(ds, replace(options, profile_id=ds.profile_id), sub, None, cancel, say)
            ds = load_dataset(ds.path)
        ready.append(ds)
        progress((i + 1) / len(datasets), f"Modèle {i + 1}/{len(datasets)} prêt")
    return ready


def align_models(clouds: Sequence[o3d.geometry.PointCloud], names: Sequence[str], profile_id: str,
                 progress: ProgressFn, cancel: threading.Event | None,
                 say: Callable[[str], None]) -> list[np.ndarray | None]:
    """Transformation de chaque modèle vers le premier (None si écarté)."""
    r = build_profile(profile_id).registration
    voxel = r.voxel_m

    def clouds_of(cloud: o3d.geometry.PointCloud) -> FragmentClouds:
        pts = prepared(np.asarray(cloud.points), voxel)
        return FragmentClouds(pts, pts)

    target = clouds_of(clouds[0])
    transforms: list[np.ndarray | None] = [np.eye(4)]
    for i, (cloud, name) in enumerate(zip(clouds[1:], names[1:], strict=True), start=1):
        _check(cancel)
        progress((i - 1) / max(len(clouds) - 1, 1), f"Recalage de {name}...")
        result = register(clouds_of(cloud), target, r)
        ok = result.fitness >= MERGE_MIN_FITNESS and result.rmse_m <= r.max_rmse_m * MERGE_RMSE_FACTOR
        say(f"{name} : {result.fitness * 100:.0f} % coïncide, écart {result.rmse_m * 1000:.2f} mm → "
            f"{'fusionné' if ok else 'écarté'}")
        transforms.append(result.transform if ok else None)
        if ok:
            moved = clouds_of(o3d.geometry.PointCloud(cloud).transform(result.transform))
            merged = np.vstack([np.asarray(target.feature_cloud.points), np.asarray(moved.feature_cloud.points)])
            target = clouds_of(o3d.geometry.PointCloud(o3d.utility.Vector3dVector(merged)))
    progress(1.0, "Recalage terminé")
    return transforms


def merge_datasets(datasets: Sequence[Dataset], options: BuildOptions, root: Path,
                   progress: ProgressFn | None = None, cancel: threading.Event | None = None,
                   log_fn: Callable[[str], None] | None = None) -> MergeResult:
    """Fusionne les datasets dans un nouveau dossier `<date>_fusion` de `root`."""
    say = log_fn or log.info
    t0 = time.monotonic()
    kind = check_compatible(datasets)
    ready = ensure_models(datasets, options, _stage(progress, "modeles"), cancel, say)
    reference = ready[0]
    profile = build_profile(reference.profile_id)
    names = [d.name for d in ready]
    clouds = [model_cloud(d) for d in ready]
    transforms = align_models(clouds, names, reference.profile_id, _stage(progress, "recalage"), cancel, say)
    kept = [(c, t) for c, t in zip(clouds, transforms, strict=True) if t is not None]
    if len(kept) < 2:
        raise ValueError("Aucun autre dataset ne coïncide avec le premier : vérifie qu'ils montrent le même sujet.")

    surface = _stage(progress, "surface")
    surface(0.1, "Surface de Poisson...")
    combined = o3d.geometry.PointCloud()
    for cloud, transform in kept:
        combined += o3d.geometry.PointCloud(cloud).transform(transform)
    voxel_m = profile.voxel_mm / 1000.0
    combined = combined.voxel_down_sample(voxel_m)
    combined.normalize_normals()
    _check(cancel)
    mesh = poisson_surface(combined, voxel_m, kind == "object")
    if len(mesh.triangles) == 0:
        raise ValueError("La fusion n'a produit aucune surface.")

    surface(0.8, "Export...")
    out = new_dataset_dir(root, "fusion", with_frames=False)
    merged_names = tuple(n for n, t in zip(names, transforms, strict=True) if t is not None)
    rejected = tuple(n for n, t in zip(names, transforms, strict=True) if t is None)
    ply, _stl = export_meshes(mesh, out / "reconstruction", profile.export_mm)
    size = tuple(round(float(v) * 1000, 1) for v in np.ptp(np.asarray(mesh.vertices), axis=0))
    duration = round(time.monotonic() - t0, 1)
    created = datetime.now().isoformat(timespec="seconds")
    (out / FUSION_FILE).write_text(json.dumps({
        "format": 1, "profile": reference.profile_id, "kind": kind, "created": created,
        "sources": [str(d.path) for d in ready], "merged": list(merged_names), "rejected": list(rejected),
    }, indent=2, ensure_ascii=False))
    (out / "reconstruction" / BUILD_FILE).write_text(json.dumps({
        "size_mm": list(size), "triangles": len(mesh.triangles), "duration_s": duration,
        "voxel_mm": round(profile.voxel_mm, 3), "units": "mm" if profile.export_mm else "m",
        "frames_used": sum(d.frame_count() for d in ready), "frames_total": sum(d.frame_count() for d in ready),
        "merged": list(merged_names), "rejected": list(rejected), "built": created,
    }, indent=2, ensure_ascii=False))
    if (thumb := thumbnail(reference)) is not None:
        write_thumbnail(out, thumb)
    surface(1.0, "Fusion prête")
    return MergeResult(out, ply, size, merged_names, rejected, len(mesh.triangles), duration)
