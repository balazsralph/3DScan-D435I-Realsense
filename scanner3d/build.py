"""Construction du modèle 3D d'un dataset : suivi, recalage, fusion, isolement, lissage, export.

Utilisable sans interface : `python -m scanner3d.build DOSSIER [--qualite fin]`.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import open3d as o3d

from .dataset import BUILD_FILE, MODEL_FILE, Dataset, DatasetError, load_dataset
from .extraction import ExtractionError, extract_object
from .profiles import DEFAULT_QUALITY, QUALITIES, ScanProfile, build_profile
from .registration import align_fragments, correct_drift, split_into_chunks
from .room import level_room
from .slam import Cancelled, Fragment, FrameLoader, integrate_frames, pick_device, track_fragments

log = logging.getLogger(__name__)
StageFn = Callable[[float, str], None]
PreviewFn = Callable[[np.ndarray, np.ndarray], None]

# Part de la barre de progression occupée par chaque étape
STAGES = {"suivi": (0.0, 0.62), "recalage": (0.62, 0.72), "fusion": (0.72, 0.92), "finition": (0.92, 1.0)}
MIN_USED_RATIO = 0.5
CHUNK_FRAMES = 200  # tronçons recalés entre eux pour corriger la dérive (~7 s de scan)


@dataclass(frozen=True)
class BuildOptions:
    profile_id: str
    quality: str = DEFAULT_QUALITY
    smoothing: bool = True
    depth_max_m: float | None = None
    drift_correction: bool = True


@dataclass(frozen=True)
class BuildResult:
    model_path: Path
    stl_path: Path
    scene_path: Path | None
    size_mm: tuple[float, float, float]
    frames_used: int
    frames_total: int
    fragments: int
    fragments_merged: int
    triangles: int
    duration_s: float
    device: str
    voxel_mm: float
    timings_s: dict[str, float] = field(default_factory=dict)
    warnings: tuple[str, ...] = field(default_factory=tuple)


class Progress:
    """Convertit l'avancement d'une étape en avancement global."""

    def __init__(self, callback: Callable[[float, str], None] | None) -> None:
        self._callback = callback

    def stage(self, name: str) -> StageFn:
        start, end = STAGES[name]

        def report(fraction: float, message: str) -> None:
            if self._callback:
                self._callback(start + (end - start) * min(max(fraction, 0.0), 1.0), message)
        return report


def smooth(mesh: o3d.geometry.TriangleMesh, iterations: int) -> o3d.geometry.TriangleMesh:
    """Lissage de Taubin : gomme les marches de la grille de voxels sans faire fondre le volume."""
    result = mesh.filter_smooth_taubin(number_of_iterations=iterations) if iterations > 0 else copy.deepcopy(mesh)
    if mesh.has_vertex_colors() and not result.has_vertex_colors():
        result.vertex_colors = mesh.vertex_colors
    result.compute_vertex_normals()
    return result


def scaled(mesh: o3d.geometry.TriangleMesh, factor: float) -> o3d.geometry.TriangleMesh:
    result = copy.deepcopy(mesh)
    if factor != 1.0:
        result.scale(factor, center=np.zeros(3))
    result.compute_vertex_normals()
    result.compute_triangle_normals()
    return result


def export_meshes(mesh: o3d.geometry.TriangleMesh, out_dir: Path, units_mm: bool) -> tuple[Path, Path]:
    """`modele.ply` (couleurs) et `modele.stl` (géométrie), en mm pour un objet, en m pour une salle."""
    out_dir.mkdir(parents=True, exist_ok=True)
    final = scaled(mesh, 1000.0 if units_mm else 1.0)
    stl_mesh = copy.deepcopy(final)
    stl_mesh.vertex_colors = o3d.utility.Vector3dVector()
    ply, stl = out_dir / MODEL_FILE, out_dir / "modele.stl"
    if not (o3d.io.write_triangle_mesh(str(ply), final) and o3d.io.write_triangle_mesh(str(stl), stl_mesh)):
        raise OSError(f"Écriture impossible dans {out_dir}")
    return ply, stl


def _usable_fragments(fragments: list[Fragment], profile: ScanProfile) -> list[Fragment]:
    ordered = sorted(fragments, key=lambda f: len(f.indices), reverse=True)
    return [f for f in ordered if len(f.indices) >= profile.tracking.min_fragment_frames] or ordered[:1]


def build_dataset(dataset: Dataset, options: BuildOptions, progress: Callable[[float, str], None] | None = None,
                  preview: PreviewFn | None = None, cancel: threading.Event | None = None,
                  log_fn: Callable[[str], None] | None = None) -> BuildResult:
    """Reconstruit le dataset et écrit le résultat dans `<dataset>/reconstruction/`."""
    say = log_fn or log.info
    t0 = time.monotonic()
    recorded_max = dataset.depth_max_m if options.profile_id == dataset.profile_id else None
    profile = build_profile(options.profile_id, options.quality, options.depth_max_m or recorded_max)
    frames = dataset.frames()
    if len(frames) < 10:
        raise DatasetError(f"Pas assez d'images dans {dataset.name} ({len(frames)}).")
    device = pick_device()
    depth_max = profile.depth_max_m
    loader = FrameLoader(frames, profile, dataset.depth_scale, depth_max)
    steps = Progress(progress)
    warnings: list[str] = []
    say(f"{len(frames)} images · {profile.title} · voxel {profile.voxel_mm:.2f} mm · {device}")

    timings: dict[str, float] = {}
    clock = time.monotonic()

    def lap(name: str) -> None:
        nonlocal clock
        timings[name] = round(time.monotonic() - clock, 1)
        clock = time.monotonic()

    fragments = track_fragments(loader, dataset.intrinsics, profile, profile.voxel_mm, device,
                                steps.stage("suivi"), preview, cancel)
    if not fragments:
        raise ExtractionError("Le suivi de la caméra a échoué sur toutes les images.")
    lap("suivi")
    usable = _usable_fragments(fragments, profile)
    if len(fragments) > 1:
        say(f"Scan coupé en {len(fragments)} morceaux ({len(usable)} exploitables) : recalage...")

    def mesh_of(frag: Fragment) -> o3d.geometry.TriangleMesh:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        return integrate_frames(loader, frag.indices, frag.poses, dataset.intrinsics, profile, profile.voxel_mm,
                                device)

    steps.stage("recalage")(0.0, "Recalage des morceaux...")
    accepted = align_fragments(usable, mesh_of, profile, say) if len(usable) > 1 else [(usable[0], np.eye(4))]
    # tronçons dans le repère commun, puis correction de la dérive le long du scan
    chunks = [Fragment(c.indices, tuple(t @ pose for pose in c.poses))
              for frag, t in accepted for c in split_into_chunks(frag, CHUNK_FRAMES)]
    steps.stage("recalage")(0.5, "Correction de la dérive...")
    corrections = correct_drift(chunks, mesh_of, profile, say) if options.drift_correction else [np.eye(4)] * len(
        chunks)
    lap("recalage")
    indices = [i for c in chunks for i in c.indices]
    poses = [t @ pose for c, t in zip(chunks, corrections, strict=True) for pose in c.poses]
    if len(indices) < MIN_USED_RATIO * len(frames):
        warnings.append(f"Seules {len(indices)} images sur {len(frames)} ont servi : refais le scan plus "
                        "lentement, en gardant le sujet dans l'image.")

    scene = integrate_frames(loader, indices, poses, dataset.intrinsics, profile, profile.voxel_mm, device,
                             steps.stage("fusion"), cancel)
    if len(scene.triangles) == 0:
        raise ExtractionError("La fusion n'a produit aucune surface (sujet trop loin ou hors plage ?).")

    lap("fusion")
    finish = steps.stage("finition")
    finish(0.1, "Isolement du sujet...")
    out_dir = dataset.reconstruction_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    scene_path: Path | None = None
    if profile.kind == "object" and profile.object is not None:
        scene_path = out_dir / "scene.ply"
        o3d.io.write_triangle_mesh(str(scene_path), scaled(scene, 1000.0))
        obj = extract_object(scene, poses, profile.object)
        mesh, size = obj.mesh, (obj.length_m, obj.width_m, obj.height_m)
    else:
        room = level_room(scene, poses, profile.room) if profile.room else None
        if room is None:
            raise ValueError(f"Profil {profile.id} sans paramètres de salle.")
        if not room.floor_found:
            warnings.append("Sol non trouvé : la scène est orientée d'après la tenue de la caméra.")
        mesh, size = room.mesh, room.size_m
    finish(0.6, "Lissage et export...")
    if options.smoothing:
        mesh = smooth(mesh, profile.smoothing_iterations)
    ply, stl = export_meshes(mesh, out_dir, profile.export_mm)
    lap("finition")
    result = BuildResult(
        model_path=ply, stl_path=stl, scene_path=scene_path, size_mm=tuple(round(s * 1000, 1) for s in size),
        frames_used=len(indices), frames_total=len(frames), fragments=len(fragments),
        fragments_merged=len(accepted), triangles=len(mesh.triangles), duration_s=round(time.monotonic() - t0, 1),
        device=str(device), voxel_mm=round(profile.voxel_mm, 3), timings_s=timings, warnings=tuple(warnings))
    _write_build_info(out_dir, result, options)
    finish(1.0, "Modèle prêt")
    for warning in warnings:
        say(f"Attention : {warning}")
    return result


def _write_build_info(out_dir: Path, result: BuildResult, options: BuildOptions) -> None:
    data = {k: (str(v) if isinstance(v, Path) else v) for k, v in asdict(result).items()}
    data.update({"options": asdict(options), "built": datetime.now().isoformat(timespec="seconds"),
                 "units": "mm" if build_profile(options.profile_id).export_mm else "m"})
    (out_dir / BUILD_FILE).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Construit le modèle 3D d'un dataset RGB-D.")
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--profil", help="objet_petit, objet ou salle (par défaut : celui du dataset)")
    parser.add_argument("--qualite", default=DEFAULT_QUALITY, choices=tuple(QUALITIES))
    parser.add_argument("--sans-lissage", action="store_true")
    parser.add_argument("--profondeur-max", type=float, metavar="M",
                        help="coupe le fond au-delà de M mètres (ex. 0.45 pour un plateau tournant)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    try:
        dataset = load_dataset(args.dataset)
        options = BuildOptions(args.profil or dataset.profile_id, args.qualite, not args.sans_lissage,
                               args.profondeur_max)
        last = [-1]

        def progress(fraction: float, message: str) -> None:
            if int(fraction * 20) != last[0]:
                last[0] = int(fraction * 20)
                log.info("[%3d %%] %s", fraction * 100, message)
        result = build_dataset(dataset, options, progress)
    except (DatasetError, ExtractionError, ValueError, OSError) as exc:
        log.error("Erreur : %s", exc)
        return 1
    except KeyboardInterrupt:
        return 130
    log.info("Modèle : %s (%d triangles, %s mm) en %.0f s", result.model_path, result.triangles,
             " x ".join(f"{s:g}" for s in result.size_mm), result.duration_s)
    log.info("Durées : %s", ", ".join(f"{k} {v:.0f} s" for k, v in result.timings_s.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
