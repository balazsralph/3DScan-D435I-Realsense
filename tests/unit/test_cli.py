from pathlib import Path

import pytest

from scanner3d.cli import main, parse_args
from scanner3d.paths import ENV_DATASETS, default_datasets_root


def test_build_subcommand_options() -> None:
    args = parse_args(["build", "scans/x", "--profil", "salle", "--qualite", "fin", "--profondeur-max", "2.5"])
    assert args.command == "build" and args.dataset == Path("scans/x")
    assert (args.profil, args.qualite, args.profondeur_max) == ("salle", "fin", 2.5)
    assert not args.sans_lissage and not args.sans_correction_derive


def test_gui_is_default_command() -> None:
    args = parse_args(["--mode", "objet_petit"])
    assert args.command is None and args.mode == "objet_petit"


def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--mode", "garage"])


def test_build_reports_missing_dataset(tmp_path: Path) -> None:
    assert main(["build", str(tmp_path / "absent")]) == 1


def test_datasets_root_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ENV_DATASETS, str(tmp_path))
    assert default_datasets_root() == tmp_path


def test_datasets_root_inside_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_DATASETS, raising=False)
    root = default_datasets_root()
    assert root.name == "datasets" and (root.parent / "pyproject.toml").is_file()
