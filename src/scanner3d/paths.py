"""Emplacements par défaut des données."""
from __future__ import annotations

import os
from pathlib import Path

ENV_DATASETS = "SCANNER3D_DATASETS"


def default_datasets_root() -> Path:
    """Dossier des datasets : $SCANNER3D_DATASETS, sinon `datasets/` du dépôt cloné, sinon ~/Scanner3D/datasets."""
    if env := os.environ.get(ENV_DATASETS):
        return Path(env).expanduser()
    checkout = Path(__file__).resolve().parents[2]
    if (checkout / "pyproject.toml").is_file():
        return checkout / "datasets"
    return Path.home() / "Scanner3D" / "datasets"
