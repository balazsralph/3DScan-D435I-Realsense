"""Ligne de commande.

  scanner3d                       interface graphique (écran d'accueil)
  scanner3d --mode salle          interface, directement dans un mode
  scanner3d build DOSSIER [...]   construit le modèle 3D d'un dataset, sans interface
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .paths import default_datasets_root
from .profiles import DEFAULT_QUALITY, PROFILES, QUALITIES

log = logging.getLogger("scanner3d")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scanner3d", description="Scanner 3D pour Intel RealSense D435i.")
    parser.add_argument("--mode", choices=tuple(PROFILES), help="ouvre directement ce mode (sinon : accueil)")
    parser.add_argument("--datasets", type=Path, default=None,
                        help=f"dossier des datasets (défaut : {default_datasets_root()})")
    parser.add_argument("--debug", action="store_true", help="journal détaillé")
    sub = parser.add_subparsers(dest="command")
    build = sub.add_parser("build", help="construit le modèle 3D d'un dataset, sans interface")
    build.add_argument("dataset", type=Path)
    build.add_argument("--profil", choices=tuple(PROFILES), help="par défaut : celui du dataset")
    build.add_argument("--qualite", default=DEFAULT_QUALITY, choices=tuple(QUALITIES))
    build.add_argument("--sans-lissage", action="store_true")
    build.add_argument("--sans-correction-derive", action="store_true")
    build.add_argument("--profondeur-max", type=float, metavar="M",
                       help="coupe le fond au-delà de M mètres (ex. 0.45 pour un plateau tournant)")
    return parser.parse_args(argv)


def run_build(args: argparse.Namespace) -> int:
    import open3d as o3d

    from .dataset import DatasetError, load_dataset
    from .reconstruction.extraction import ExtractionError
    from .reconstruction.pipeline import BuildOptions, build_dataset

    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    last = -1

    def progress(fraction: float, message: str) -> None:
        nonlocal last
        if int(fraction * 20) != last:
            last = int(fraction * 20)
            log.info("[%3d %%] %s", fraction * 100, message)

    try:
        dataset = load_dataset(args.dataset)
        options = BuildOptions(args.profil or dataset.profile_id, args.qualite, not args.sans_lissage,
                               args.profondeur_max, not args.sans_correction_derive)
        result = build_dataset(dataset, options, progress)
    except (DatasetError, ExtractionError, ValueError, OSError) as exc:
        log.error("Erreur : %s", exc)
        return 1
    except KeyboardInterrupt:
        return 130
    log.info("Modèle : %s (%d triangles, %s mm) en %.0f s", result.model_path, result.triangles,
             " x ".join(f"{s:g}" for s in result.size_mm), result.duration_s)
    log.info("Durées : %s", ", ".join(f"{k} {v:.0f} s" for k, v in result.timings_s.items()))
    for warning in result.warnings:
        log.warning("Attention : %s", warning)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(message)s" if args.command == "build" else
                        "%(asctime)s %(levelname)s %(name)s : %(message)s", datefmt="%H:%M:%S")
    if args.command == "build":
        return run_build(args)
    from .gui.app import run

    return run(args.datasets or default_datasets_root(), args.mode)
