"""Isolement d'un objet posé sur son support, mise à plat et cotes (repris de scan3d, paramétré)."""
from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from dataclasses import dataclass

import cv2
import numpy as np
import open3d as o3d
import open3d.core as o3c

from ..profiles import ObjectParams

RANSAC_N, RANSAC_ITERATIONS = 3, 2000


class ExtractionError(RuntimeError):
    """Le sujet n'a pas pu être isolé de la scène."""


@dataclass(frozen=True)
class ScannedObject:
    mesh: o3d.geometry.TriangleMesh  # mètres ; support à z=0, objet vers +z, centré en x/y
    length_m: float
    width_m: float
    height_m: float
    transform: np.ndarray            # repère de la scène -> repère posé à plat


def signed_distance(points: np.ndarray, plane: np.ndarray) -> np.ndarray:
    return points @ plane[:3] + plane[3]


def refit_plane(points: np.ndarray, plane: np.ndarray, p: ObjectParams) -> np.ndarray:
    """Plan des moindres carrés sur les points proches de `plane`, orienté comme lui."""
    inliers = points[np.abs(signed_distance(points, plane)) < p.plane_dist_threshold_m]
    if len(inliers) < p.min_plane_inliers:
        return plane
    centroid = inliers.mean(axis=0)
    normal = np.linalg.svd(inliers - centroid, full_matrices=False)[2][-1]
    refined = np.append(normal, -normal @ centroid)
    return refined if refined[:3] @ plane[:3] > 0 else -refined


def fit_support_plane(points: np.ndarray, camera_center: np.ndarray, p: ObjectParams) -> np.ndarray:
    """Plan dominant (le support) affiné par moindres carrés, caméra du côté positif."""
    rng = np.random.default_rng(0)
    sample = points[rng.choice(len(points), min(len(points), p.plane_fit_samples), replace=False)]
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(sample))
    rough, _ = pcd.segment_plane(p.plane_dist_threshold_m, RANSAC_N, RANSAC_ITERATIONS)
    plane = refit_plane(points, np.asarray(rough, dtype=np.float64) / np.linalg.norm(rough[:3]), p)
    return plane if signed_distance(camera_center[None, :], plane)[0] > 0 else -plane


def distances_to(points: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """Distance de chaque point au point de `reference` le plus proche."""
    if len(points) == 0 or len(reference) == 0:
        return np.full(len(points), np.inf)
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    target = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(reference))
    return np.asarray(cloud.compute_point_cloud_distance(target))


def voxel_groups(points: np.ndarray, voxel: float) -> tuple[np.ndarray, np.ndarray]:
    """Centres des voxels occupés et indice du voxel de chaque point."""
    keys = np.floor(points / voxel).astype(np.int64)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    return (unique + 0.5) * voxel, inverse.ravel()


def distance_to_ray(points: np.ndarray, origin: np.ndarray, direction: np.ndarray) -> np.ndarray:
    rel = points - origin
    return np.linalg.norm(rel - np.outer(rel @ direction, direction), axis=1)


def cluster_labels(centers: np.ndarray, eps: float, min_points: int) -> np.ndarray:
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(centers))
    return np.asarray(pcd.cluster_dbscan(eps, min_points))


def focus_point(poses: Sequence[np.ndarray], max_cond: float) -> np.ndarray | None:
    """Point le plus proche de tous les axes de visée : ce que la caméra a filmé en tournant autour."""
    a, b = np.zeros((3, 3)), np.zeros(3)
    for pose in poses:
        direction, origin = pose[:3, 2], pose[:3, 3]
        projector = np.eye(3) - np.outer(direction, direction)
        a += projector
        b += projector @ origin
    return None if np.linalg.cond(a) > max_cond else np.linalg.solve(a, b)


def distance_to_target(points: np.ndarray, poses: Sequence[np.ndarray], p: ObjectParams) -> np.ndarray:
    """Distance au point visé ; si la caméra n'a pas tourné, distance à l'axe de la 1re image."""
    target = focus_point(poses, p.focus_max_cond)
    if target is not None:
        return np.linalg.norm(points - target, axis=1)
    return distance_to_ray(points, poses[0][:3, 3], poses[0][:3, 2])


def select_object(points: np.ndarray, poses: Sequence[np.ndarray], p: ObjectParams) -> np.ndarray:
    """Masque de l'objet visé pendant le scan, morceaux voisins (trous, nervures) compris."""
    centers, inverse = voxel_groups(points, p.cluster_voxel_m)
    fine = cluster_labels(centers, p.cluster_eps_m, p.cluster_min_points)
    if fine.size == 0 or fine.max() < 0:
        raise ExtractionError("Aucun objet trouvé au-dessus du support.")
    sizes = np.bincount(fine[fine >= 0])
    big = np.nonzero(sizes >= p.object_min_size_ratio * sizes.max())[0]
    centroids = np.array([centers[fine == k].mean(axis=0) for k in big])
    seed = fine == big[np.argmin(distance_to_target(centroids, poses, p))]
    coarse = cluster_labels(centers, p.object_merge_gap_m, p.cluster_min_points)
    seed_labels = coarse[seed][coarse[seed] >= 0]
    if seed_labels.size == 0:
        return seed[inverse]
    return (coarse == np.bincount(seed_labels).argmax())[inverse]


def keep_vertices(mesh: o3d.geometry.TriangleMesh, keep: np.ndarray) -> o3d.geometry.TriangleMesh:
    result = copy.deepcopy(mesh)
    result.remove_vertices_by_mask(~keep)
    return result


def keep_main_components(mesh: o3d.geometry.TriangleMesh, min_ratio: float) -> o3d.geometry.TriangleMesh:
    """Retire les morceaux plus petits que `min_ratio` fois le plus gros."""
    if len(mesh.triangles) == 0:
        return mesh
    clusters, counts, _ = mesh.cluster_connected_triangles()
    clusters, counts = np.asarray(clusters), np.asarray(counts)
    result = copy.deepcopy(mesh)
    result.remove_triangles_by_mask(counts[clusters] < min_ratio * counts.max())
    result.remove_unreferenced_vertices()
    return result


def plane_basis(normal: np.ndarray) -> np.ndarray:
    """Matrice de rotation dont la 3e ligne est la normale du support."""
    helper = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    x = helper - (helper @ normal) * normal
    x /= np.linalg.norm(x)
    return np.stack([x, np.cross(normal, x), normal])


def support_transform(points: np.ndarray, plane: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Transformation qui pose le support en z=0 et aligne l'emprise de l'objet sur x/y."""
    rot = plane_basis(plane[:3])
    origin = -plane[3] * plane[:3]
    flat = (points - origin) @ rot.T
    angle = math.radians(cv2.minAreaRect(flat[:, :2].astype(np.float32))[2])
    rz = np.array([[math.cos(-angle), -math.sin(-angle), 0], [math.sin(-angle), math.cos(-angle), 0], [0, 0, 1]])
    xy = flat @ rz.T
    if np.ptp(xy[:, 1]) > np.ptp(xy[:, 0]):
        rz = np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]]) @ rz
        xy = flat @ rz.T
    center = np.array([(xy[:, 0].max() + xy[:, 0].min()) / 2, (xy[:, 1].max() + xy[:, 1].min()) / 2, 0.0])
    transform = np.eye(4)
    transform[:3, :3] = rz @ rot
    transform[:3, 3] = -(rz @ rot) @ origin - center
    return transform, float(np.ptp(xy[:, 0])), float(np.ptp(xy[:, 1]))


def object_vertex_mask(vertices: np.ndarray, poses: Sequence[np.ndarray],
                       p: ObjectParams) -> tuple[np.ndarray, np.ndarray]:
    """Zone de l'objet (pied compris, à découper ensuite par le plan) et plan du support autour de lui.

    Le cœur (au-dessus de la petite « jupe » de fusion au pied de l'objet) sert à isoler l'objet ;
    les sommets bas ne sont ajoutés que près du cœur, pour ne pas embarquer le sol qui ondule.
    """
    if len(vertices) < p.min_object_points:
        raise ExtractionError("Scène vide : rien n'a été reconstruit.")
    plane = fit_support_plane(vertices, poses[0][:3, 3], p)
    dist = signed_distance(vertices, plane)
    candidate = np.nonzero((dist > p.core_min_height_m) & (dist < p.above_plane_max_m))[0]
    if candidate.size < p.min_object_points:
        raise ExtractionError("Rien de significatif au-dessus du support : l'objet a-t-il été filmé ?")
    core = np.zeros(len(vertices), dtype=bool)
    core[candidate[select_object(vertices[candidate], poses, p)]] = True
    near = np.linalg.norm(vertices - vertices[core].mean(axis=0), axis=1) < p.local_plane_radius_m
    plane = refit_plane(vertices[near], plane, p)
    dist = signed_distance(vertices, plane)
    core &= dist > p.core_min_height_m
    if not core.any():
        raise ExtractionError("L'objet se confond avec le support.")
    low = np.nonzero(dist <= p.core_min_height_m)[0]
    region = core.copy()
    region[low[distances_to(vertices[low], vertices[core]) < p.base_grow_m]] = True
    return region, plane


def clip_above_support(mesh: o3d.geometry.TriangleMesh, plane: np.ndarray,
                       p: ObjectParams) -> o3d.geometry.TriangleMesh:
    """Coupe nette le long du plan (les triangles sont tranchés, pas supprimés : pas de dents de scie)."""
    normal = plane[:3]
    point = -plane[3] * normal + p.above_plane_min_m * normal
    clipped = o3d.t.geometry.TriangleMesh.from_legacy(mesh).clip_plane(o3c.Tensor(point), o3c.Tensor(normal))
    return clipped.to_legacy()


def extract_object(scene: o3d.geometry.TriangleMesh, poses: Sequence[np.ndarray], p: ObjectParams) -> ScannedObject:
    """Isole l'objet visé pendant le scan, le pose à plat et mesure ses cotes."""
    region, plane = object_vertex_mask(np.asarray(scene.vertices), poses, p)
    obj = keep_main_components(clip_above_support(keep_vertices(scene, region), plane, p), p.min_component_ratio)
    points = np.asarray(obj.vertices)
    above_skirt = points[signed_distance(points, plane) > p.core_min_height_m]
    if len(above_skirt) < 3:
        raise ExtractionError("Objet trop petit ou trop plat pour être isolé.")
    transform, length, width = support_transform(above_skirt, plane)
    aligned = copy.deepcopy(obj).transform(transform)
    height = float(np.asarray(aligned.vertices)[:, 2].max())
    return ScannedObject(aligned, length, width, height, transform)
