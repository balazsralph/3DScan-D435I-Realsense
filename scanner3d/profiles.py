"""Profils de scan : réglages caméra + reconstruction optimisés pour un type de sujet.

Trois profils : petit objet (≤ 10 cm), objet (10-60 cm) et salle entière.
Les valeurs du petit objet viennent de mesures réelles (projet scan3d) ; les autres
en sont dérivées à l'échelle (distances proportionnelles à la taille du voxel).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

PRESETS_DIR = Path(__file__).resolve().parent / "presets"
STREAM_WIDTH, STREAM_HEIGHT, STREAM_FPS = 848, 480, 30  # résolution recommandée par Intel pour le D435

Kind = Literal["object", "room"]


@dataclass(frozen=True)
class TrackingParams:
    """Suivi image -> modèle et garde-fous (au-delà, l'image est rejetée)."""

    odometry_method: str          # "PointToPlane" (relief) ou "Hybrid" (relief + couleur)
    depth_diff_m: float           # écart max entre profondeur mesurée et modèle pour un appariement
    min_fitness: float             # part min des pixels mesurés qui coïncident avec le modèle
    max_step_translation_m: float  # saut max entre deux images consécutives
    max_step_rotation_deg: float
    fragment_break_frames: int     # suivi perdu N images de suite : nouveau morceau
    min_fragment_frames: int       # morceaux plus courts ignorés


@dataclass(frozen=True)
class RegistrationParams:
    """Recalage des morceaux entre eux (RANSAC sur caractéristiques FPFH puis ICP)."""

    voxel_m: float
    ransac_iterations: int
    icp_distances_m: tuple[float, ...]
    eval_distance_m: float
    min_fitness: float
    max_rmse_m: float
    support_radius_m: float  # objet : support gardé autour de l'objet pour caler l'inclinaison


@dataclass(frozen=True)
class ObjectParams:
    """Isolement d'un objet posé sur un support plan."""

    plane_dist_threshold_m: float
    above_plane_min_m: float
    core_min_height_m: float
    base_grow_m: float
    local_plane_radius_m: float
    above_plane_max_m: float
    cluster_voxel_m: float
    cluster_eps_m: float
    object_merge_gap_m: float
    min_plane_inliers: int = 500
    cluster_min_points: int = 10
    min_component_ratio: float = 0.05
    object_min_size_ratio: float = 0.2
    plane_fit_samples: int = 200_000
    min_object_points: int = 200
    focus_max_cond: float = 50.0


@dataclass(frozen=True)
class RoomParams:
    """Nettoyage d'une scène de salle et mise à niveau du sol."""

    min_component_ratio: float = 0.002  # morceaux < 0,2 % des triangles : retirés (poussières flottantes)
    plane_voxel_m: float = 0.03
    plane_dist_m: float = 0.03
    max_floor_tilt_deg: float = 25.0
    min_floor_ratio: float = 0.03


@dataclass(frozen=True)
class ScanProfile:
    id: str
    title: str
    subtitle: str
    kind: Kind
    preset_file: str
    depth_scale: float            # valeur brute / depth_scale = mètres
    depth_min_m: float
    depth_max_m: float
    guide_near_m: float           # zone de distance conseillée pendant la capture
    guide_far_m: float
    voxel_mm: float
    live_voxel_factor: float      # aperçu en direct : voxel plus gros, plus rapide
    trunc_voxel_multiplier: float  # fusion finale : bande fine, préserve les parois minces
    block_count: int
    weight_threshold: float       # un voxel doit être vu par >= N images pour être gardé
    edge_jump_ratio: float        # pixels « volants » aux bords : saut de profondeur relatif max
    smoothing_iterations: int     # lissage de Taubin (préserve le volume)
    export_mm: bool
    spatial_filter: bool          # filtre spatial RealSense (lisse en préservant les arêtes)
    tracking: TrackingParams
    registration: RegistrationParams
    object: ObjectParams | None = None
    room: RoomParams | None = None
    tips: tuple[str, ...] = ()
    # Suivi : bande large. Mesuré sur orbite synthétique (600 images) : 4 voxels -> dérive 36 mm / 1,7° ;
    # 8 voxels -> 18 mm / 0,5°. Au-delà, plus de gain. La fusion finale garde `trunc_voxel_multiplier`.
    tracking_trunc_multiplier: float = 8.0

    @property
    def preset_path(self) -> Path:
        return PRESETS_DIR / self.preset_file

    @property
    def distance_label(self) -> str:
        return f"{_fmt_m(self.guide_near_m)} – {_fmt_m(self.guide_far_m)}"


@dataclass(frozen=True)
class Quality:
    """Finesse de la reconstruction (taille du voxel).

    Mesuré sur un scan réel de 1676 images : un voxel plus gros n'accélère pas (le suivi décroche
    plus souvent) et dégrade les cotes ; sauter des images double le mouvement entre deux images
    et fait décrocher le suivi. Toutes les images servent donc toujours.
    """

    id: str
    label: str
    voxel_factor: float


QUALITIES: dict[str, Quality] = {q.id: q for q in (
    Quality("standard", "Standard", 1.0),
    Quality("fin", "Détaillé", 0.75),
)}
DEFAULT_QUALITY = "standard"


def _fmt_m(value: float) -> str:
    return f"{value * 100:.0f} cm" if value < 1.0 else f"{value:.1f} m".replace(".", ",")


def object_params(scale: float) -> ObjectParams:
    """Paramètres d'isolement mesurés sur un objet de 8 cm, mis à l'échelle."""
    return ObjectParams(
        plane_dist_threshold_m=0.0015 * scale,
        above_plane_min_m=0.001 * scale,
        core_min_height_m=0.003 * scale,
        base_grow_m=0.0025 * scale,
        local_plane_radius_m=0.10 * scale,
        above_plane_max_m=min(0.15 * scale, 1.0),
        cluster_voxel_m=0.0015 * scale,
        cluster_eps_m=0.004 * scale,
        object_merge_gap_m=0.015 * scale,
    )


def object_registration(scale: float) -> RegistrationParams:
    return RegistrationParams(
        voxel_m=0.0015 * scale,
        ransac_iterations=1_000_000,
        icp_distances_m=tuple(d * scale for d in (0.006, 0.003, 0.0015)),
        eval_distance_m=0.0015 * scale,
        min_fitness=0.4,
        max_rmse_m=0.001 * scale,
        support_radius_m=0.08 * scale,
    )


OBJECT_TIPS = (
    "Pose l'objet sur une grande surface plane, rien d'autre autour.",
    "Lumière blanche diffuse ; surfaces brillantes : voile de talc ou spray matifiant.",
    "Tourne lentement autour (un tour en 40-60 s), puis un second tour plus haut.",
)

PROFILES: dict[str, ScanProfile] = {p.id: p for p in (
    ScanProfile(
        id="objet_petit", title="Petit objet", subtitle="jusqu'à 10 cm", kind="object",
        preset_file="objet_petit.json", depth_scale=10000.0, depth_min_m=0.115, depth_max_m=0.34,
        guide_near_m=0.15, guide_far_m=0.27, voxel_mm=0.8, live_voxel_factor=2.0,
        trunc_voxel_multiplier=4.0, block_count=60000, weight_threshold=3.0, edge_jump_ratio=0.03,
        smoothing_iterations=4, export_mm=True, spatial_filter=False,
        tracking=TrackingParams("PointToPlane", 0.02, 0.35, 0.010, 6.0, 5, 90),
        registration=object_registration(1.0), object=object_params(1.0), tips=OBJECT_TIPS,
    ),
    ScanProfile(
        id="objet", title="Objet", subtitle="10 à 60 cm", kind="object",
        preset_file="objet.json", depth_scale=10000.0, depth_min_m=0.2, depth_max_m=1.0,
        guide_near_m=0.30, guide_far_m=0.65, voxel_mm=2.0, live_voxel_factor=1.75,
        trunc_voxel_multiplier=4.0, block_count=50000, weight_threshold=3.0, edge_jump_ratio=0.03,
        smoothing_iterations=4, export_mm=True, spatial_filter=True,
        tracking=TrackingParams("PointToPlane", 0.04, 0.35, 0.02, 6.0, 5, 90),
        registration=object_registration(2.5), object=object_params(2.5), tips=OBJECT_TIPS,
    ),
    ScanProfile(
        id="salle", title="Salle entière", subtitle="pièce, atelier, intérieur", kind="room",
        preset_file="salle.json", depth_scale=1000.0, depth_min_m=0.3, depth_max_m=3.5,
        guide_near_m=0.6, guide_far_m=3.0, voxel_mm=8.0, live_voxel_factor=1.5,
        trunc_voxel_multiplier=6.0, block_count=50000, weight_threshold=3.0, edge_jump_ratio=0.04,
        smoothing_iterations=3, export_mm=False, spatial_filter=True,
        tracking=TrackingParams("PointToPlane", 0.07, 0.3, 0.06, 8.0, 8, 60),
        registration=RegistrationParams(
            voxel_m=0.04, ransac_iterations=1_000_000, icp_distances_m=(0.12, 0.06, 0.03),
            eval_distance_m=0.03, min_fitness=0.3, max_rmse_m=0.02, support_radius_m=0.0),
        room=RoomParams(),
        tips=(
            "Pars d'un coin, caméra à hauteur de poitrine, et longe les murs lentement.",
            "Garde toujours des meubles ou des murs texturés dans l'image (pas un mur blanc seul).",
            "Termine en revenant à ton point de départ : le recalage s'en sert.",
        ),
    ),
)}

DEFAULT_PROFILE = "objet"


def get_profile(profile_id: str) -> ScanProfile:
    try:
        return PROFILES[profile_id]
    except KeyError:
        raise ValueError(f"Profil inconnu : {profile_id!r} (attendu : {', '.join(PROFILES)})") from None


def build_profile(profile_id: str, quality_id: str = DEFAULT_QUALITY, depth_max_m: float | None = None) -> ScanProfile:
    """Profil ajusté pour une reconstruction : finesse du voxel et profondeur max éventuelle."""
    profile = get_profile(profile_id)
    if quality_id not in QUALITIES:
        raise ValueError(f"Qualité inconnue : {quality_id!r}")
    changes: dict[str, float] = {"voxel_mm": profile.voxel_mm * QUALITIES[quality_id].voxel_factor}
    if depth_max_m is not None:
        if not profile.depth_min_m < depth_max_m <= 10.0:
            raise ValueError(f"Profondeur max {depth_max_m} m hors plage ({profile.depth_min_m} - 10 m)")
        changes["depth_max_m"] = depth_max_m
    return replace(profile, **changes)
