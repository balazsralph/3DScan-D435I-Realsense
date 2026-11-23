"""Datasets RGB-D : lecture des trois formats connus et création des nouveaux.

Formats reconnus :
- scanner3d : `session.json` + `couleur/` + `profondeur/` (créé par cette application) ;
- scan3d    : `intrinseques.json` + `couleur/` + `profondeur/` (projet ~/Desktop/scan3d) ;
- open3d    : `camera_intrinsic.json` + `color/` + `depth/` (anciens scripts de ce dépôt,
              profondeur en 0,1 mm comme le preset MediumDensityPreset-custom.json).
La reconstruction est toujours écrite dans le sous-dossier `reconstruction/`, pour ne jamais
écraser les fichiers produits par les autres outils.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

SESSION_FILE = "session.json"
SCAN3D_FILE = "intrinseques.json"
LEGACY_FILE = "camera_intrinsic.json"
THUMBNAIL_FILE = "thumbnail.jpg"
RECONSTRUCTION_DIR = "reconstruction"
BUILD_FILE = "build.json"
MODEL_FILE = "modele.ply"
LEGACY_DEPTH_SCALE = 10000.0
FORMAT_VERSION = 2

FramePair = tuple[Path, Path]  # (couleur, profondeur)


class DatasetError(RuntimeError):
    """Dossier illisible ou qui n'est pas un dataset RGB-D."""


@dataclass(frozen=True)
class Intrinsics:
    width: int
    height: int
    fx: float
    fy: float
    ppx: float
    ppy: float

    def matrix(self) -> np.ndarray:
        return np.array([[self.fx, 0.0, self.ppx], [0.0, self.fy, self.ppy], [0.0, 0.0, 1.0]])


@dataclass(frozen=True)
class Dataset:
    path: Path
    fmt: str
    profile_id: str
    intrinsics: Intrinsics
    depth_scale: float
    created: datetime
    color_dir: Path
    depth_dir: Path
    depth_max_m: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def reconstruction_dir(self) -> Path:
        return self.path / RECONSTRUCTION_DIR

    @property
    def model_path(self) -> Path:
        return self.reconstruction_dir / MODEL_FILE

    @property
    def has_model(self) -> bool:
        return self.model_path.is_file()

    def frames(self) -> list[FramePair]:
        """Paires (couleur, profondeur) triées ; une image sans sa jumelle est ignorée."""
        colors = {p.stem: p for p in _scan_files(self.color_dir, (".jpg", ".jpeg", ".png"))}
        return [(colors[d.stem], d) for d in sorted(_scan_files(self.depth_dir, (".png",)))
                if d.stem in colors]

    def frame_count(self) -> int:
        return int(self.meta.get("frames") or len(self.frames()))

    def build_info(self) -> dict[str, Any] | None:
        path = self.reconstruction_dir / BUILD_FILE
        try:
            return json.loads(path.read_text()) if path.is_file() else None
        except (OSError, json.JSONDecodeError):
            return None


def _scan_files(folder: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not folder.is_dir():
        return []
    with os.scandir(folder) as entries:
        return [Path(e.path) for e in entries if e.is_file() and e.name.lower().endswith(suffixes)]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"{path} illisible : {exc}") from exc
    if not isinstance(data, dict):
        raise DatasetError(f"{path} : contenu inattendu")
    return data


def _created(path: Path, meta: dict[str, Any]) -> datetime:
    stamp = meta.get("created")
    if isinstance(stamp, str):
        try:
            return datetime.fromisoformat(stamp)
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def _load_scanner3d(path: Path) -> Dataset:
    meta = _read_json(path / SESSION_FILE)
    try:
        intr = Intrinsics(**meta["intrinsics"])
        return Dataset(path, "scanner3d", str(meta["profile"]), intr, float(meta["depth_scale"]),
                       _created(path, meta), path / "couleur", path / "profondeur",
                       meta.get("depth_max_m"), meta)
    except (KeyError, TypeError, ValueError) as exc:
        raise DatasetError(f"{path / SESSION_FILE} incomplet : {exc}") from exc


def _load_scan3d(path: Path) -> Dataset:
    meta = _read_json(path / SCAN3D_FILE)
    try:
        intr = Intrinsics(int(meta["width"]), int(meta["height"]), float(meta["fx"]), float(meta["fy"]),
                          float(meta["ppx"]), float(meta["ppy"]))
        return Dataset(path, "scan3d", "objet_petit", intr, float(meta["depth_scale"]), _created(path, {}),
                       path / "couleur", path / "profondeur", meta.get("depth_max_m"), {})
    except (KeyError, TypeError, ValueError) as exc:
        raise DatasetError(f"{path / SCAN3D_FILE} incomplet : {exc}") from exc


def _load_legacy(path: Path) -> Dataset:
    meta = _read_json(path / LEGACY_FILE)
    try:
        m = [float(v) for v in meta["intrinsic_matrix"]]  # ordre colonne par colonne (Open3D)
        intr = Intrinsics(int(meta["width"]), int(meta["height"]), m[0], m[4], m[6], m[7])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise DatasetError(f"{path / LEGACY_FILE} incomplet : {exc}") from exc
    return Dataset(path, "open3d", "objet", intr, LEGACY_DEPTH_SCALE, _created(path, {}),
                   path / "color", path / "depth", None, {})


_LOADERS = ((SESSION_FILE, _load_scanner3d), (SCAN3D_FILE, _load_scan3d), (LEGACY_FILE, _load_legacy))


def is_dataset(path: Path) -> bool:
    return path.is_dir() and any((path / marker).is_file() for marker, _ in _LOADERS)


def load_dataset(path: Path) -> Dataset:
    for marker, loader in _LOADERS:
        if (path / marker).is_file():
            return loader(path)
    raise DatasetError(f"{path} n'est pas un dataset RGB-D (aucun fichier d'intrinsèques).")


def discover(roots: list[Path]) -> tuple[list[Dataset], list[str]]:
    """Datasets trouvés dans `roots` (chaque racine peut être un dataset ou en contenir).

    Renvoie (datasets du plus récent au plus ancien, messages d'erreur des dossiers illisibles).
    """
    found: dict[Path, Dataset] = {}
    errors: list[str] = []
    for root in roots:
        candidates = [root] if is_dataset(root) else (
            sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else [])
        for candidate in candidates:
            if not is_dataset(candidate) or candidate.resolve() in found:
                continue
            try:
                found[candidate.resolve()] = load_dataset(candidate)
            except DatasetError as exc:
                errors.append(str(exc))
    return sorted(found.values(), key=lambda d: d.created, reverse=True), errors


def new_dataset_dir(root: Path, profile_id: str) -> Path:
    """Nouveau dossier daté ; suffixe -2, -3... si deux sessions démarrent dans la même seconde."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    root.mkdir(parents=True, exist_ok=True)
    for n in range(1, 100):
        path = root / (f"{stamp}_{profile_id}" + (f"-{n}" if n > 1 else ""))
        try:
            path.mkdir()
        except FileExistsError:
            continue
        (path / "couleur").mkdir()
        (path / "profondeur").mkdir()
        return path
    raise OSError(f"Impossible de créer un dossier de session dans {root}")


@dataclass(frozen=True)
class SessionInfo:
    profile: str
    intrinsics: Intrinsics
    depth_scale: float
    depth_min_m: float
    depth_max_m: float
    frames: int
    dropped: int
    duration_s: float
    created: str
    camera: dict[str, str] = field(default_factory=dict)


def write_session(path: Path, info: SessionInfo) -> None:
    data = {"format": FORMAT_VERSION, **asdict(info)}
    tmp = path / (SESSION_FILE + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(path / SESSION_FILE)


def write_thumbnail(dataset_dir: Path, color_bgr: np.ndarray, width: int = 320) -> None:
    h, w = color_bgr.shape[:2]
    thumb = cv2.resize(color_bgr, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(dataset_dir / THUMBNAIL_FILE), thumb, [cv2.IMWRITE_JPEG_QUALITY, 88])


def read_frame(pair: FramePair) -> tuple[np.ndarray, np.ndarray]:
    """Lit une paire : (couleur BGR uint8, profondeur uint16)."""
    color_path, depth_path = pair
    color = cv2.imread(str(color_path), cv2.IMREAD_COLOR)
    depth = cv2.imread(str(depth_path), cv2.IMREAD_UNCHANGED)
    if color is None or depth is None:
        raise DatasetError(f"Image illisible : {color_path.name} / {depth_path.name}")
    if depth.dtype != np.uint16:
        raise DatasetError(f"{depth_path} : profondeur attendue en 16 bits, reçu {depth.dtype}")
    return color, depth


def thumbnail(dataset: Dataset, width: int = 320) -> np.ndarray | None:
    """Vignette BGR : celle enregistrée, sinon l'image du milieu de la capture."""
    saved = dataset.path / THUMBNAIL_FILE
    if saved.is_file() and (img := cv2.imread(str(saved), cv2.IMREAD_COLOR)) is not None:
        return img
    frames = dataset.frames()
    if not frames:
        return None
    img = cv2.imread(str(frames[len(frames) // 2][0]), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    return cv2.resize(img, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)


def folder_size_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total
