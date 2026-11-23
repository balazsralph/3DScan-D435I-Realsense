import json
from collections import namedtuple
from pathlib import Path

import cv2
import numpy as np
import pytest

from scanner3d.acquisition import recorder as recorder_module
from scanner3d.acquisition.recorder import Recorder, RecorderError
from scanner3d.dataset import DatasetError, discover, is_dataset, load_dataset, read_frame, thumbnail
from scanner3d.profiles import PROFILES
from tests.helpers.recordings import INTR, frame, record


def test_recorder_roundtrip(tmp_path: Path) -> None:
    path = record(tmp_path, 5)
    ds = load_dataset(path)
    assert ds.fmt == "scanner3d" and ds.profile_id == "objet"
    assert ds.intrinsics == INTR and ds.depth_scale == 10000.0 and ds.depth_max_m == 0.8
    assert ds.frame_count() == 5 and len(ds.frames()) == 5
    color, depth = read_frame(ds.frames()[2])
    assert depth.dtype == np.uint16 and int(depth[0, 0]) == 3002
    assert color.shape == (48, 64, 3)
    assert (path / "thumbnail.jpg").is_file() and thumbnail(ds) is not None
    assert not ds.has_model and ds.build_info() is None


def test_recorder_without_frames_leaves_nothing(tmp_path: Path) -> None:
    rec = Recorder(tmp_path, PROFILES["salle"], INTR, 3.0)
    result = rec.finish()
    assert result.path is None and not any(tmp_path.iterdir())


def test_recorder_refuses_full_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(recorder_module.shutil, "disk_usage", lambda _p: usage(10, 9, 1))
    with pytest.raises(RecorderError):
        Recorder(tmp_path, PROFILES["objet"], INTR, 0.8)


def test_frames_ignore_orphans(tmp_path: Path) -> None:
    path = record(tmp_path, 3)
    (path / "profondeur" / "000001.png").unlink()
    assert [d.stem for _, d in load_dataset(path).frames()] == ["000000", "000002"]


def test_scan3d_format(tmp_path: Path) -> None:
    session = tmp_path / "2026-11-18_00-26-01"
    (session / "couleur").mkdir(parents=True)
    (session / "profondeur").mkdir()
    (session / "intrinseques.json").write_text(json.dumps({
        "width": 848, "height": 480, "fx": 604.2, "fy": 604.1, "ppx": 422.1, "ppy": 234.6,
        "depth_scale": 10000.0, "depth_min_m": 0.115, "depth_max_m": 0.34}))
    cv2.imwrite(str(session / "couleur" / "000000.jpg"), frame()[0])
    cv2.imwrite(str(session / "profondeur" / "000000.png"), frame()[1])
    ds = load_dataset(session)
    assert ds.fmt == "scan3d" and ds.profile_id == "objet_petit" and ds.depth_max_m == 0.34
    assert ds.intrinsics.fx == pytest.approx(604.2) and len(ds.frames()) == 1


def test_legacy_open3d_format_reads_column_major_matrix(tmp_path: Path) -> None:
    (tmp_path / "color").mkdir()
    (tmp_path / "depth").mkdir()
    (tmp_path / "camera_intrinsic.json").write_text(json.dumps({
        "width": 848, "height": 480, "intrinsic_matrix": [604.28, 0, 0, 0, 604.23, 0, 422.10, 234.67, 1]}))
    ds = load_dataset(tmp_path)
    assert ds.fmt == "open3d" and ds.depth_scale == 10000.0
    assert (ds.intrinsics.fx, ds.intrinsics.fy, ds.intrinsics.ppx, ds.intrinsics.ppy) == (604.28, 604.23, 422.10,
                                                                                           234.67)


def test_discover_sorts_newest_first_and_reports_broken(tmp_path: Path) -> None:
    old, new = record(tmp_path, 1), record(tmp_path, 1, "salle")
    session = json.loads((old / "session.json").read_text())
    session["created"] = "2020-01-01T10:00:00"
    (old / "session.json").write_text(json.dumps(session))
    broken = tmp_path / "casse"
    broken.mkdir()
    (broken / "session.json").write_text("{pas du json")
    (tmp_path / "pas_un_dataset").mkdir()
    found, errors = discover([tmp_path, tmp_path / "absent"])
    assert [d.path for d in found][0] == new
    assert {d.path for d in found} == {old, new}
    assert len(errors) == 1 and "casse" in errors[0]
    # une racine qui est elle-même un dataset
    assert [d.path for d in discover([new])[0]] == [new]


def test_new_dataset_dirs_never_collide(tmp_path: Path) -> None:
    first, second = record(tmp_path, 1), record(tmp_path, 1)  # même seconde, même profil
    assert first != second and first.is_dir() and second.is_dir()


def test_load_dataset_rejects_plain_folder(tmp_path: Path) -> None:
    assert not is_dataset(tmp_path)
    with pytest.raises(DatasetError):
        load_dataset(tmp_path)


def test_incomplete_session_is_reported(tmp_path: Path) -> None:
    (tmp_path / "session.json").write_text(json.dumps({"profile": "objet"}))
    with pytest.raises(DatasetError):
        load_dataset(tmp_path)
