"""Bout en bout : scène synthétique de dimensions connues -> dataset -> suivi, fusion, isolement, export."""
import json
import threading
from pathlib import Path

import numpy as np
import open3d as o3d
import pytest

from scanner3d.build import BuildOptions, build_dataset
from scanner3d.dataset import load_dataset
from scanner3d.slam import Cancelled
from tests.synthetic import BOX_MM, orbit_poses, render, write_dataset

pytestmark = pytest.mark.gpu
TOLERANCE_MM = 8.0   # mesuré : 155,3 x 106,1 x 80,8 mm (poses exactes : 152,1 x 102,2 x 80,1)
FRAMES = 600         # ~5 mm par image, comme un scan à la main (un tour en 20 s)


@pytest.fixture(scope="module")
def box_dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    target = np.array([0.0, 0.0, BOX_MM[2] / 2000])
    frames = render(orbit_poses(FRAMES, 0.45, 0.30, target))
    return write_dataset(tmp_path_factory.mktemp("scan") / "boite", frames)


def test_build_recovers_box_dimensions(box_dataset: Path) -> None:
    messages: list[str] = []
    progress: list[float] = []
    previews: list[int] = []
    result = build_dataset(load_dataset(box_dataset), BuildOptions("objet"), lambda f, _m: progress.append(f),
                           lambda pts, _c: previews.append(len(pts)), None, messages.append)

    length, width, height = result.size_mm
    assert length == pytest.approx(BOX_MM[0], abs=TOLERANCE_MM)
    assert width == pytest.approx(BOX_MM[1], abs=TOLERANCE_MM)
    assert height == pytest.approx(BOX_MM[2], abs=TOLERANCE_MM)
    assert result.frames_used >= 0.9 * result.frames_total
    assert progress == sorted(progress) and progress[-1] == pytest.approx(1.0)
    assert previews and all(n > 0 for n in previews)

    mesh = o3d.io.read_triangle_mesh(str(result.model_path))
    assert len(mesh.triangles) > 1000 and mesh.has_vertex_colors()
    vertices = np.asarray(mesh.vertices)  # export en mm, support à z = 0 (base coupée juste au-dessus)
    assert np.ptp(vertices[:, :2], axis=0) == pytest.approx(result.size_mm[:2], abs=1.0)
    assert vertices[:, 2].max() == pytest.approx(result.size_mm[2], abs=1.0)
    assert 0.0 < vertices[:, 2].min() < 5.0
    assert result.stl_path.is_file() and result.scene_path is not None and result.scene_path.is_file()

    info = json.loads((box_dataset / "reconstruction" / "build.json").read_text())
    assert info["units"] == "mm" and info["frames_total"] == FRAMES
    ds = load_dataset(box_dataset)
    assert ds.has_model and ds.build_info()["size_mm"] == list(result.size_mm)


def test_build_can_be_cancelled(box_dataset: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        build_dataset(load_dataset(box_dataset), BuildOptions("objet"), cancel=cancel)
