"""Démarrage de l'interface Qt."""
from __future__ import annotations

import os
import sys
from pathlib import Path


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


def run(datasets_root: Path, mode: str | None = None) -> int:
    prepare_environment()
    import open3d as o3d
    from PySide6.QtWidgets import QApplication

    from . import theme
    from .main_window import MainWindow

    o3d.utility.set_verbosity_level(o3d.utility.VerbosityLevel.Error)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Scanner 3D")
    app.setOrganizationName("scanner3d")
    theme.apply(app)
    window = MainWindow(datasets_root, mode)
    window.show()
    return app.exec()
