"""Export d'un modèle vers les formats des logiciels 3D (impression, CAO, Blender...).

Conventions :
- STL et PLY : unités du modèle (mm pour un objet, m pour une salle), z vers le haut, comme les
  trancheurs d'impression 3D et les logiciels de CAO.
- OBJ et GLB : mètres, y vers le haut (conventions de glTF et des importeurs de Blender, qui
  remettent automatiquement z vers le haut). Un objet de 97 mm arrive donc à la bonne taille.
Le GLB est écrit ici (glTF 2.0 binaire) : celui d'Open3D 0.20 perd les couleurs des sommets.
"""
from __future__ import annotations

import copy
import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import open3d as o3d

Z_UP_TO_Y_UP = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])  # (x, y, z) -> (x, z, -y)


class ExportError(RuntimeError):
    """Modèle illisible ou fichier impossible à écrire."""


@dataclass(frozen=True)
class ExportFormat:
    id: str
    label: str
    usage: str
    meters: bool       # True : converti en mètres ; False : unités du modèle
    y_up: bool
    colors: bool


FORMATS: dict[str, ExportFormat] = {f.id: f for f in (
    ExportFormat("stl", "STL", "impression 3D, CAO", meters=False, y_up=False, colors=False),
    ExportFormat("obj", "OBJ", "Blender, Maya, 3ds Max (couleurs)", meters=True, y_up=True, colors=True),
    ExportFormat("glb", "GLB (glTF)", "Blender, web, réalité augmentée (couleurs)", meters=True, y_up=True,
                 colors=True),
    ExportFormat("ply", "PLY", "MeshLab, CloudCompare (couleurs)", meters=False, y_up=False, colors=True),
)}


def prepare(mesh: o3d.geometry.TriangleMesh, fmt: ExportFormat, model_units: str) -> o3d.geometry.TriangleMesh:
    """Copie du maillage mise à l'échelle et orientée pour le format."""
    result = copy.deepcopy(mesh)
    if fmt.meters and model_units == "mm":
        result.scale(0.001, center=np.zeros(3))
    if fmt.y_up:
        result.rotate(Z_UP_TO_Y_UP, center=np.zeros(3))
    if not fmt.colors:
        result.vertex_colors = o3d.utility.Vector3dVector()
    result.compute_vertex_normals()
    return result


def _padded(data: bytes, pad: bytes) -> bytes:
    return data + pad * (-len(data) % 4)


def write_glb(path: Path, mesh: o3d.geometry.TriangleMesh) -> None:
    """glTF 2.0 binaire : positions, normales, couleurs des sommets (COLOR_0), triangles."""
    positions = np.asarray(mesh.vertices, dtype=np.float32)
    normals = np.asarray(mesh.vertex_normals, dtype=np.float32)
    indices = np.asarray(mesh.triangles, dtype=np.uint32).reshape(-1)
    has_colors = mesh.has_vertex_colors()
    colors = np.asarray(mesh.vertex_colors, dtype=np.float32) if has_colors else None
    blobs = [positions.tobytes(), normals.tobytes()] + ([colors.tobytes()] if has_colors else []) + [indices.tobytes()]
    views, offset, binary = [], 0, b""
    for i, blob in enumerate(blobs):
        target = 34963 if i == len(blobs) - 1 else 34962  # ELEMENT_ARRAY_BUFFER / ARRAY_BUFFER
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(blob), "target": target})
        binary += _padded(blob, b"\x00")
        offset = len(binary)
    n = len(positions)
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3",
         "min": positions.min(axis=0).tolist(), "max": positions.max(axis=0).tolist()},
        {"bufferView": 1, "componentType": 5126, "count": n, "type": "VEC3"},
    ]
    attributes = {"POSITION": 0, "NORMAL": 1}
    if has_colors:
        accessors.append({"bufferView": 2, "componentType": 5126, "count": n, "type": "VEC3"})
        attributes["COLOR_0"] = 2
    accessors.append({"bufferView": len(blobs) - 1, "componentType": 5125, "count": int(indices.size),
                      "type": "SCALAR"})
    document = {
        "asset": {"version": "2.0", "generator": "scanner3d"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "scan"}],
        "meshes": [{"name": "scan", "primitives": [
            {"attributes": attributes, "indices": len(accessors) - 1, "material": 0, "mode": 4}]}],
        "materials": [{"name": "scan", "doubleSided": True,
                       "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1], "metallicFactor": 0.0,
                                                "roughnessFactor": 0.85}}],
        "buffers": [{"byteLength": len(binary)}], "bufferViews": views, "accessors": accessors,
    }
    json_chunk = _padded(json.dumps(document, separators=(",", ":")).encode(), b" ")
    total = 12 + 8 + len(json_chunk) + 8 + len(binary)
    with path.open("wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(json_chunk), b"JSON") + json_chunk)
        f.write(struct.pack("<I4s", len(binary), b"BIN\x00") + binary)


def export_model(model_path: Path, model_units: str, dest: Path, fmt_id: str) -> Path:
    """Convertit `model_path` (PLY du scan) vers `dest` au format `fmt_id` ; renvoie le chemin écrit."""
    if fmt_id not in FORMATS:
        raise ExportError(f"Format inconnu : {fmt_id}")
    fmt = FORMATS[fmt_id]
    mesh = o3d.io.read_triangle_mesh(str(model_path))
    if len(mesh.triangles) == 0:
        raise ExportError(f"{model_path.name} : modèle vide ou illisible")
    dest = dest.with_suffix(f".{fmt.id}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    prepared = prepare(mesh, fmt, model_units)
    try:
        if fmt.id == "glb":
            write_glb(dest, prepared)
            return dest
        ok = o3d.io.write_triangle_mesh(str(dest), prepared, write_ascii=False, write_vertex_colors=fmt.colors)
    except OSError as exc:
        raise ExportError(f"Écriture impossible : {exc}") from exc
    if not ok:
        raise ExportError(f"Écriture impossible : {dest}")
    return dest
