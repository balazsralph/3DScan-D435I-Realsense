"""Recalage des morceaux de scan entre eux : RANSAC sur caractéristiques FPFH puis ICP de plus en plus fin.

Objet : le recalage se fait sur la forme de l'objet seul (le support plat, sans relief, tromperait
RANSAC), l'ICP fin garde un peu de support pour caler l'inclinaison.
Salle : toute la scène sert au recalage.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
import open3d as o3d

from ..profiles import RegistrationParams, ScanProfile
from .extraction import ExtractionError, object_vertex_mask, signed_distance
from .slam import Fragment

log = logging.getLogger(__name__)
reg = o3d.pipelines.registration
MeshOf = Callable[[Fragment], o3d.geometry.TriangleMesh]
LogFn = Callable[[str], None]


@dataclass(frozen=True)
class FragmentClouds:
    feature_cloud: o3d.geometry.PointCloud  # caractéristiques, contrôle du recalage
    icp_cloud: o3d.geometry.PointCloud      # recalage fin


@dataclass(frozen=True)
class Registration:
    transform: np.ndarray
    fitness: float
    rmse_m: float

    def is_reliable(self, r: RegistrationParams) -> bool:
        return self.fitness >= r.min_fitness and self.rmse_m <= r.max_rmse_m


def prepared(points: np.ndarray, voxel: float) -> o3d.geometry.PointCloud:
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points)).voxel_down_sample(voxel)
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 4, max_nn=30))
    return pcd


def object_clouds(mesh: o3d.geometry.TriangleMesh, poses: Sequence[np.ndarray],
                  profile: ScanProfile) -> FragmentClouds:
    if profile.object is None:
        raise ValueError(f"Le profil {profile.id} n'a pas de paramètres d'objet.")
    p, r = profile.object, profile.registration
    vertices = np.asarray(mesh.vertices)
    region, plane = object_vertex_mask(vertices, poses, p)
    dist = signed_distance(vertices, plane)
    obj = vertices[region & (dist > p.above_plane_min_m)]
    near = np.linalg.norm(vertices - obj.mean(axis=0), axis=1) < r.support_radius_m
    support = vertices[near & (np.abs(dist) < p.plane_dist_threshold_m)]
    return FragmentClouds(prepared(obj, r.voxel_m), prepared(np.vstack([obj, support]), r.voxel_m))


def scene_clouds(mesh: o3d.geometry.TriangleMesh, _poses: Sequence[np.ndarray],
                 profile: ScanProfile) -> FragmentClouds:
    cloud = prepared(np.asarray(mesh.vertices), profile.registration.voxel_m)
    if len(cloud.points) < 100:
        raise ExtractionError("Morceau presque vide.")
    return FragmentClouds(cloud, cloud)


def fragment_clouds(mesh: o3d.geometry.TriangleMesh, poses: Sequence[np.ndarray],
                    profile: ScanProfile) -> FragmentClouds:
    return (object_clouds if profile.kind == "object" else scene_clouds)(mesh, poses, profile)


def fpfh(pcd: o3d.geometry.PointCloud, voxel: float) -> reg.Feature:
    return reg.compute_fpfh_feature(pcd, o3d.geometry.KDTreeSearchParamHybrid(radius=voxel * 10, max_nn=100))


def register(source: FragmentClouds, target: FragmentClouds, r: RegistrationParams) -> Registration:
    o3d.utility.random.seed(0)
    distance = r.voxel_m * 2
    coarse = reg.registration_ransac_based_on_feature_matching(
        source.feature_cloud, target.feature_cloud, fpfh(source.feature_cloud, r.voxel_m),
        fpfh(target.feature_cloud, r.voxel_m), True, distance, reg.TransformationEstimationPointToPoint(False), 3,
        [reg.CorrespondenceCheckerBasedOnEdgeLength(0.9), reg.CorrespondenceCheckerBasedOnDistance(distance)],
        reg.RANSACConvergenceCriteria(r.ransac_iterations, 0.9999))
    transform = coarse.transformation
    for max_dist in r.icp_distances_m:
        transform = reg.registration_icp(source.icp_cloud, target.icp_cloud, max_dist, transform,
                                         reg.TransformationEstimationPointToPlane(),
                                         reg.ICPConvergenceCriteria(max_iteration=60)).transformation
    check = reg.evaluate_registration(source.feature_cloud, target.feature_cloud, r.eval_distance_m, transform)
    return Registration(np.asarray(transform), check.fitness, check.inlier_rmse)


def _transformed(clouds: FragmentClouds, transform: np.ndarray) -> FragmentClouds:
    return FragmentClouds(o3d.geometry.PointCloud(clouds.feature_cloud).transform(transform),
                          o3d.geometry.PointCloud(clouds.icp_cloud).transform(transform))


def _combined(a: FragmentClouds, b: FragmentClouds, voxel: float) -> FragmentClouds:
    def merge(x: o3d.geometry.PointCloud, y: o3d.geometry.PointCloud) -> o3d.geometry.PointCloud:
        return prepared(np.vstack([np.asarray(x.points), np.asarray(y.points)]), voxel)
    return FragmentClouds(merge(a.feature_cloud, b.feature_cloud), merge(a.icp_cloud, b.icp_cloud))


def align_fragments(fragments: Sequence[Fragment], mesh_of: MeshOf, profile: ScanProfile,
                    log_fn: LogFn | None = None) -> list[tuple[Fragment, np.ndarray]]:
    """Recale chaque morceau sur l'ensemble déjà accepté ; renvoie (morceau, transformation).

    Le premier morceau (le plus long) sert de référence. Un morceau qui ne coïncide pas assez
    est ignoré plutôt que de fausser le modèle.
    """
    say = log_fn or log.info
    reference = fragments[0]
    try:
        target = fragment_clouds(mesh_of(reference), reference.poses, profile)
    except ExtractionError as exc:
        say(f"Morceau de référence inexploitable pour le recalage ({exc}) : seul lui est gardé.")
        return [(reference, np.eye(4))]
    accepted = [(reference, np.eye(4))]
    r = profile.registration
    for frag in fragments[1:]:
        try:
            clouds = fragment_clouds(mesh_of(frag), frag.poses, profile)
        except ExtractionError:
            say(f"Morceau de {len(frag.indices)} images : sujet introuvable, ignoré.")
            continue
        result = register(clouds, target, r)
        ok = result.is_reliable(r)
        say(f"Morceau de {len(frag.indices)} images : {result.fitness * 100:.0f} % coïncide, "
            f"écart {result.rmse_m * 1000:.2f} mm → {'fusionné' if ok else 'ignoré'}")
        if ok:
            accepted.append((frag, result.transform))
            target = _combined(target, _transformed(clouds, result.transform), r.voxel_m)
    return accepted


# -- correction de la dérive le long d'un morceau --------------------------------------------------
MAX_CHUNK_CORRECTION_DEG = 8.0


def split_into_chunks(fragment: Fragment, chunk_frames: int) -> list[Fragment]:
    """Découpe un morceau en tronçons consécutifs (le dernier, s'il est trop court, rejoint le précédent)."""
    n = len(fragment.indices)
    bounds = list(range(0, n, chunk_frames))
    if len(bounds) > 1 and n - bounds[-1] < chunk_frames // 2:
        bounds.pop()
    ends = [*bounds[1:], n]
    return [Fragment(fragment.indices[a:b], fragment.poses[a:b]) for a, b in zip(bounds, ends, strict=True)]


def refine_icp(source: FragmentClouds, target: FragmentClouds, r: RegistrationParams) -> Registration:
    """ICP de plus en plus fin à partir de la pose suivie (pas de recherche globale : la dérive est petite)."""
    transform = np.eye(4)
    for max_dist in r.icp_distances_m:
        transform = reg.registration_icp(source.icp_cloud, target.icp_cloud, max_dist, transform,
                                         reg.TransformationEstimationPointToPlane(),
                                         reg.ICPConvergenceCriteria(max_iteration=60)).transformation
    check = reg.evaluate_registration(source.feature_cloud, target.feature_cloud, r.eval_distance_m, transform)
    return Registration(np.asarray(transform), check.fitness, check.inlier_rmse)


def is_small_correction(transform: np.ndarray, r: RegistrationParams) -> bool:
    translation = float(np.linalg.norm(transform[:3, 3]))
    angle = np.degrees(np.arccos(np.clip((np.trace(transform[:3, :3]) - 1.0) / 2.0, -1.0, 1.0)))
    return translation <= 2 * r.icp_distances_m[0] and angle <= MAX_CHUNK_CORRECTION_DEG


def correct_drift(chunks: Sequence[Fragment], mesh_of: MeshOf, profile: ScanProfile,
                  log_fn: LogFn | None = None) -> list[np.ndarray]:
    """Recale chaque tronçon sur l'union des précédents ; renvoie une correction par tronçon.

    Le suivi image par image dérive lentement (~1 % du trajet) ; en fin de tour, la caméra revoit
    le début du scan avec un décalage qui dédouble les surfaces. Recaler chaque tronçon sur tout
    ce qui précède referme la boucle. Une correction douteuse est ignorée (identité) : les images
    restent alors à la pose donnée par le suivi, rien n'est perdu.
    """
    say = log_fn or log.info
    corrections = [np.eye(4) for _ in chunks]
    if len(chunks) < 2:
        return corrections
    r = profile.registration
    try:
        target = fragment_clouds(mesh_of(chunks[0]), chunks[0].poses, profile)
    except ExtractionError:
        return corrections
    applied = 0
    for k, chunk in enumerate(chunks[1:], start=1):
        try:
            clouds = fragment_clouds(mesh_of(chunk), chunk.poses, profile)
        except ExtractionError:
            continue
        result = refine_icp(clouds, target, r)
        if result.is_reliable(r) and is_small_correction(result.transform, r):
            corrections[k] = result.transform
            applied += 1
            clouds = _transformed(clouds, result.transform)
        target = _combined(target, clouds, r.voxel_m)
    say(f"Correction de dérive : {applied}/{len(chunks) - 1} tronçons recalés sur le début du scan.")
    return corrections
