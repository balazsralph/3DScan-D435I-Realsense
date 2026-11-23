"""Lance l'interface : `python -m scanner3d [--mode objet|objet_petit|salle]`."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def prepare_environment() -> None:
    """Réglages à faire avant d'importer Qt.

    - Wayland : VTK (vue 3D) a besoin d'une fenêtre X11 ; on passe par XWayland si disponible.
    - OpenCV (paquet non « headless ») impose ses propres greffons Qt, incompatibles avec PySide6.
    """
    if os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    import cv2  # noqa: F401  (importé ici pour neutraliser ses variables d'environnement Qt)

    for var in ("QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_FONTDIR"):
        if "cv2" in os.environ.get(var, ""):
            del os.environ[var]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scanner 3D pour Intel RealSense D435i.")
    parser.add_argument("--mode", choices=("objet_petit", "objet", "salle"),
                        help="ouvre directement ce mode (sinon : écran d'accueil)")
    parser.add_argument("--debug", action="store_true", help="journal détaillé")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s : %(message)s", datefmt="%H:%M:%S")
    prepare_environment()
    import open3d as o3d
    from PySide6.QtWidgets import QApplication

    from .gui import theme
    from .gui.main_window import MainWindow

    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Scanner 3D")
    app.setOrganizationName("scanner3d")
    theme.apply(app)
    window = MainWindow(REPO_ROOT, args.mode)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
