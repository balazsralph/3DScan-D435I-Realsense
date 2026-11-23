"""Fusion de deux passages synthétiques autour de la même boîte (hauteurs et départs différents)."""
import math
from pathlib import Path

import numpy as np
import open3d as o3d
import pytest

from scanner3d.dataset import load_dataset
from scanner3d.reconstruction.merge import check_compatible, merge_datasets, poisson_depth
from scanner3d.reconstruction.pipeline import BuildOptions
from tests.helpers.synthetic_scene import BOX_MM, look_at, orbit_poses, render, write_dataset

pytestmark = pytest.mark.gpu
TOLERANCE_MM = 9.0  # mesuré : 156,9 x 106,6 x 82,6 mm pour une boîte de 150 x 100 x 80


def orbit(count: int, radius: float, height: float, start: float) -> list[np.ndarray]:
    target = np.array([0.0, 0.0, BOX_MM[2] / 2000])
    return [look_at(np.array([radius * math.cos(2 * math.pi * i / count + start),
                              radius * math.sin(2 * math.pi * i / count + start), height]), target)
            for i in range(count)]


@pytest.fixture(scope="module")
def two_scans(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    root = tmp_path_factory.mktemp("fusion")
    high = write_dataset(root / "haut", render(orbit_poses(450, 0.45, 0.32, np.array([0.0, 0.0, 0.04]))))
    low = write_dataset(root / "bas", render(orbit(450, 0.42, 0.22, 1.3), seed=1))
    return root, high, low


def test_merge_two_passes(two_scans: tuple[Path, Path, Path]) -> None:
    root, high, low = two_scans
    messages: list[str] = []
    progress: list[float] = []
    result = merge_datasets([load_dataset(high), load_dataset(low)], BuildOptions("objet"), root,
                            lambda f, _m: progress.append(f), None, messages.append)
    assert result.merged == ("haut", "bas") and result.rejected == ()
    length, width, height = sorted(result.size_mm[:2], reverse=True) + [result.size_mm[2]]
    assert length == pytest.approx(BOX_MM[0], abs=TOLERANCE_MM)
    assert width == pytest.approx(BOX_MM[1], abs=TOLERANCE_MM)
    assert height == pytest.approx(BOX_MM[2], abs=TOLERANCE_MM)
    assert progress == sorted(progress) and progress[-1] == pytest.approx(1.0)
    assert any("coïncide" in m for m in messages)

    fusion = load_dataset(result.path)
    assert fusion.fmt == "fusion" and fusion.has_model and fusion.frames() == []
    assert fusion.meta["merged"] == ["haut", "bas"] and len(fusion.meta["sources"]) == 2
    assert fusion.build_info()["units"] == "mm"
    mesh = o3d.io.read_triangle_mesh(str(result.model_path))
    assert len(mesh.triangles) > 5000 and mesh.has_vertex_colors()


def test_merge_rejects_incompatible_kinds(two_scans: tuple[Path, Path, Path], tmp_path: Path) -> None:
    _root, high, _low = two_scans
    room = write_dataset(tmp_path / "salle", render(orbit_poses(3, 0.45, 0.32, np.zeros(3))), profile="salle")
    with pytest.raises(ValueError):
        check_compatible([load_dataset(high), load_dataset(room)])
    with pytest.raises(ValueError):
        check_compatible([load_dataset(high)])


@pytest.mark.parametrize(("extent", "voxel", "depth"), [(0.1, 0.0008, 8), (0.15, 0.002, 8), (4.0, 0.008, 10),
                                                        (50.0, 0.001, 11), (0.001, 0.002, 6)])
def test_poisson_depth(extent: float, voxel: float, depth: int) -> None:
    assert poisson_depth(extent, voxel) == depth
