"""Petits datasets enregistrés avec le vrai Recorder (images synthétiques 64 x 48)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from scanner3d.acquisition.recorder import Recorder
from scanner3d.dataset import Intrinsics
from scanner3d.profiles import PROFILES

INTR = Intrinsics(64, 48, 60.0, 61.0, 32.0, 24.0)


def frame(value: int = 3000) -> tuple[np.ndarray, np.ndarray]:
    color = np.full((48, 64, 3), 120, np.uint8)
    depth = np.full((48, 64), value, np.uint16)
    return color, depth


def record(root: Path, count: int, profile_id: str = "objet") -> Path:
    rec = Recorder(root, PROFILES[profile_id], INTR, 0.8, {"serial": "123"})
    for i in range(count):
        rec.add(*frame(3000 + i))
    result = rec.finish()
    assert result.frames == count
    return result.path
