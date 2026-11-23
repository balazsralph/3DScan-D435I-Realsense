import json
import struct
from pathlib import Path

import numpy as np
import open3d as o3d
import pytest

from scanner3d.export import FORMATS, ExportError, export_model


@pytest.fixture()
def model(tmp_path: Path) -> Path:
    """Boîte colorée de 60 x 40 x 20 mm, posée sur z = 0, comme un modèle d'objet (en mm)."""
    mesh = o3d.geometry.TriangleMesh.create_box(60.0, 40.0, 20.0).subdivide_midpoint(1)
    mesh.vertex_colors = o3d.utility.Vector3dVector(np.tile([0.8, 0.1, 0.1], (len(mesh.vertices), 1)))
    mesh.compute_vertex_normals()
    path = tmp_path / "modele.ply"
    o3d.io.write_triangle_mesh(str(path), mesh)
    return path


def extent(mesh: o3d.geometry.TriangleMesh) -> np.ndarray:
    return np.ptp(np.asarray(mesh.vertices), axis=0)


@pytest.mark.parametrize("fmt", ["stl", "ply"])
def test_print_formats_keep_mm_and_z_up(model: Path, tmp_path: Path, fmt: str) -> None:
    out = export_model(model, "mm", tmp_path / "sortie", fmt)
    assert out.suffix == f".{fmt}"
    mesh = o3d.io.read_triangle_mesh(str(out))
    assert extent(mesh) == pytest.approx([60.0, 40.0, 20.0], abs=1e-3)
    assert mesh.has_vertex_colors() == FORMATS[fmt].colors


def test_obj_is_in_meters_y_up_with_colors(model: Path, tmp_path: Path) -> None:
    out = export_model(model, "mm", tmp_path / "blender", "obj")
    mesh = o3d.io.read_triangle_mesh(str(out))
    assert extent(mesh) == pytest.approx([0.060, 0.020, 0.040], abs=1e-6)  # hauteur sur y
    assert mesh.has_vertex_colors()


def test_glb_is_valid_gltf_with_vertex_colors(model: Path, tmp_path: Path) -> None:
    out = export_model(model, "mm", tmp_path / "scan", "glb")
    raw = out.read_bytes()
    magic, version, total = struct.unpack("<4sII", raw[:12])
    json_len, json_type = struct.unpack("<I4s", raw[12:20])
    doc = json.loads(raw[20:20 + json_len])
    bin_len, bin_type = struct.unpack("<I4s", raw[20 + json_len:28 + json_len])
    assert (magic, version, total, json_type, bin_type) == (b"glTF", 2, len(raw), b"JSON", b"BIN\x00")
    assert json_len % 4 == 0 and bin_len % 4 == 0 and bin_len == doc["buffers"][0]["byteLength"]
    attributes = doc["meshes"][0]["primitives"][0]["attributes"]
    assert set(attributes) == {"POSITION", "NORMAL", "COLOR_0"}
    position = doc["accessors"][attributes["POSITION"]]
    assert np.subtract(position["max"], position["min"]) == pytest.approx([0.060, 0.020, 0.040], abs=1e-6)
    for view in doc["bufferViews"]:
        assert view["byteOffset"] % 4 == 0 and view["byteOffset"] + view["byteLength"] <= bin_len


def test_room_models_are_not_rescaled(model: Path, tmp_path: Path) -> None:
    out = export_model(model, "m", tmp_path / "salle", "obj")
    assert extent(o3d.io.read_triangle_mesh(str(out))) == pytest.approx([60.0, 20.0, 40.0], abs=1e-3)


def test_errors(model: Path, tmp_path: Path) -> None:
    with pytest.raises(ExportError):
        export_model(model, "mm", tmp_path / "x", "fbx")
    empty = tmp_path / "vide.ply"
    o3d.io.write_triangle_mesh(str(empty), o3d.geometry.TriangleMesh())
    with pytest.raises(ExportError):
        export_model(empty, "mm", tmp_path / "x", "stl")
