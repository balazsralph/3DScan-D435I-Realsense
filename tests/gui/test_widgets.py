"""Interface : composants Qt sans affichage (plateforme « offscreen »). La vue 3D VTK n'est pas testée ici."""
import gc
import os
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
import cv2  # noqa: E402,F401  (avant Qt : ses variables d'environnement sont neutralisées ci-dessous)

for _var in ("QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_FONTDIR"):
    os.environ.pop(_var, None)

from PySide6.QtWidgets import QApplication  # noqa: E402

from scanner3d.acquisition.camera import CameraInfo  # noqa: E402
from scanner3d.dataset import load_dataset  # noqa: E402
from scanner3d.gui import theme  # noqa: E402
from scanner3d.gui.dataset_panel import DatasetPanel, duration_label, nice_date, size_label  # noqa: E402
from scanner3d.gui.live_panel import LivePanel  # noqa: E402
from scanner3d.gui.start_screen import StartScreen  # noqa: E402
from scanner3d.gui.widgets import SegmentedControl, bgr_to_qimage  # noqa: E402
from scanner3d.gui.workers import LiveStats, Mailbox  # noqa: E402
from scanner3d.profiles import PROFILES  # noqa: E402
from tests.helpers.recordings import record  # noqa: E402


@pytest.fixture(scope="module")
def app() -> Iterator[QApplication]:
    instance = QApplication.instance() or QApplication([])
    theme.apply(instance)
    yield instance
    # détruire les fenêtres tant que l'application existe (sinon plantage à la sortie de Python)
    for widget in instance.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    gc.collect()
    instance.processEvents()


def test_labels() -> None:
    assert size_label([96.7, 92.2, 23.0], "mm") == "97 × 92 × 23 mm"
    assert size_label([3313.0, 3549.9, 2835.9], "m") == "3,31 × 3,55 × 2,84 m"
    assert duration_label(42) == "42 s" and duration_label(125) == "2 min 05 s"


def test_mailbox_keeps_only_latest() -> None:
    box = Mailbox()
    box.put(1)
    box.put(2)
    assert box.take(0.01) == 2 and box.take(0.01) is None
    box.close()
    assert box.closed


def test_mailbox_wakes_waiting_reader() -> None:
    box = Mailbox()
    threading.Timer(0.05, lambda: box.put("image")).start()
    assert box.take(2.0) == "image"


def test_bgr_to_qimage_swaps_channels(app: QApplication) -> None:
    img = np.zeros((4, 6, 3), np.uint8)
    img[..., 0] = 255  # bleu en BGR
    q = bgr_to_qimage(img)
    assert (q.width(), q.height()) == (6, 4) and q.pixelColor(0, 0).blue() == 255


def test_segmented_control(app: QApplication) -> None:
    seg = SegmentedControl([("a", "A"), ("b", "B")], "b")
    seen: list[str] = []
    seg.changed.connect(seen.append)
    assert seg.value() == "b"
    seg.set_value("a")
    assert seg.value() == "a"


def test_start_screen_emits_chosen_profile(app: QApplication) -> None:
    screen = StartScreen()
    chosen: list[str] = []
    screen.profile_chosen.connect(chosen.append)
    screen.size.set_value("objet_petit")
    screen.object_card.button.click()
    screen.room_card.button.click()
    assert chosen == ["objet_petit", "salle"]
    screen.set_cameras([CameraInfo("RealSense D435I", "123", "5.17", "2.1")])
    assert "USB 2.1" in screen.camera_chip.text()
    screen.set_cameras([])
    assert "Aucune" in screen.camera_chip.text()


def test_dataset_panel_lists_and_requests_build(app: QApplication, tmp_path: Path) -> None:
    datasets = [load_dataset(record(tmp_path, 4)), load_dataset(record(tmp_path, 3, "salle"))]
    panel = DatasetPanel()
    requests: list = []
    panel.build_requested.connect(lambda ds, opts: requests.append((ds, opts)))
    panel.set_datasets(datasets, select=datasets[1].path)
    assert panel.current() == datasets[1]
    assert panel.profile_combo.currentData() == "salle"
    panel.quality.set_value("fin")
    panel.build_button.click()
    ds, options = requests[0]
    assert ds == datasets[1] and options.profile_id == "salle" and options.quality == "fin"
    panel.set_build_blocked("Enregistrement en cours")
    assert not panel.build_button.isEnabled()
    panel.set_building(True)
    panel.set_progress(0.5, "Fusion")
    assert panel.progress.value() == 500 and "50 %" in panel.progress_text.text()
    panel.set_datasets([])
    assert panel.current() is None


def test_live_panel_profile_and_stats(app: QApplication) -> None:
    panel = LivePanel()
    panel.set_profile(PROFILES["salle"])
    assert panel.depth_slider.maximum() == 350
    panel.set_stats(LiveStats(29.9, 0.8, "ok", "Distance idéale", True, 120, 2, 65.0))
    assert panel.timer_tile._value.text() == "01:05"
    assert panel.frames_tile._value.text() == "120 (−2)"
    panel.set_recording(True)
    assert panel.record.isChecked() and not panel.depth_slider.isEnabled()


def test_nice_date_is_french(tmp_path: Path) -> None:
    ds = load_dataset(record(tmp_path, 1))
    ds = type(ds)(**{**ds.__dict__, "created": datetime(2026, 11, 23, 14, 5)})
    assert nice_date(ds) == "23 nov. 2026 · 14:05"
