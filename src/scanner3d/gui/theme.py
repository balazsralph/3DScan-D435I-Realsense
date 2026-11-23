"""Identité visuelle : « instrument de mesure » — graphite profond, ambre laser, cyan profondeur."""
from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# Couleurs (rôle sémantique, pas décoratif)
BG = "#0B0E12"          # fond de l'application
SURFACE = "#12161C"     # panneaux
SURFACE_2 = "#181E26"   # cartes, champs
SURFACE_3 = "#202833"   # survol
LINE = "#263040"        # filets
TEXT = "#E8ECF1"
MUTED = "#8B97A8"
FAINT = "#5A6676"
AMBER = "#F5A524"       # action principale, laser
AMBER_DIM = "#3A2A0E"
CYAN = "#3DC7E8"        # profondeur, 3D
CYAN_DIM = "#0E2E38"
GREEN = "#3DDC97"       # bon, prêt
GREEN_DIM = "#0F3326"
RED = "#FF5A5F"         # enregistrement, erreur
RED_DIM = "#3A1416"
VIOLET = "#A78BFA"      # salle

DISPLAY_FAMILIES = ("Cantarell", "Noto Sans", "Sans Serif")
BODY_FAMILIES = ("Noto Sans", "Cantarell", "Sans Serif")
MONO_FAMILIES = ("Source Code Pro", "Noto Sans Mono", "Monospace")

RADIUS_S, RADIUS_M, RADIUS_L = 6, 10, 16


def _first_available(families: tuple[str, ...]) -> str:
    available = set(QFontDatabase.families())
    return next((f for f in families if f in available), families[-1])


class Fonts:
    display = "Sans Serif"
    body = "Sans Serif"
    mono = "Monospace"

    @classmethod
    def load(cls) -> None:
        cls.display = _first_available(DISPLAY_FAMILIES)
        cls.body = _first_available(BODY_FAMILIES)
        cls.mono = _first_available(MONO_FAMILIES)


def font(kind: str = "body", size: float = 10.0, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    family = {"display": Fonts.display, "mono": Fonts.mono}.get(kind, Fonts.body)
    f = QFont(family)
    f.setPointSizeF(size)
    f.setWeight(weight)
    if kind == "mono":
        f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def qcolor(hex_color: str, alpha: int = 255) -> QColor:
    c = QColor(hex_color)
    c.setAlpha(alpha)
    return c


def stylesheet() -> str:
    return f"""
* {{ font-family: "{Fonts.body}"; color: {TEXT}; }}
QMainWindow, QWidget#Root {{ background: {BG}; }}
QWidget#Panel {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RADIUS_L}px; }}
QWidget#Card {{ background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: {RADIUS_M}px; }}
QLabel {{ background: transparent; }}
QLabel#H1 {{ font-family: "{Fonts.display}"; font-size: 26pt; font-weight: 800; letter-spacing: -0.5px; }}
QLabel#H2 {{ font-family: "{Fonts.display}"; font-size: 13pt; font-weight: 700; }}
QLabel#Eyebrow {{ color: {MUTED}; font-size: 8pt; font-weight: 700; letter-spacing: 1.6px; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Faint {{ color: {FAINT}; font-size: 9pt; }}
QLabel#Mono {{ font-family: "{Fonts.mono}"; }}
QLabel#Stat {{ font-family: "{Fonts.mono}"; font-size: 15pt; font-weight: 600; }}

QPushButton {{
    background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: {RADIUS_S}px;
    padding: 7px 14px; font-weight: 600;
}}
QPushButton:hover {{ background: {SURFACE_3}; border-color: #34425A; }}
QPushButton:pressed {{ background: {LINE}; }}
QPushButton:focus {{ border-color: {CYAN}; }}
QPushButton:disabled {{ color: {FAINT}; background: {SURFACE}; border-color: {SURFACE_3}; }}
QPushButton#Primary {{ background: {AMBER}; color: #1A1204; border: none; padding: 10px 18px; font-size: 10.5pt; }}
QPushButton#Primary:hover {{ background: #FFB840; }}
QPushButton#Primary:pressed {{ background: #D98E12; }}
QPushButton#Primary:disabled {{ background: {AMBER_DIM}; color: #7A6440; }}
QPushButton#Ghost {{ background: transparent; border: 1px solid transparent; color: {MUTED}; padding: 6px 10px; }}
QPushButton#Ghost:hover {{ color: {TEXT}; background: {SURFACE_2}; border-color: {LINE}; }}
QPushButton#Danger {{ background: transparent; border: 1px solid {RED_DIM}; color: {RED}; }}
QPushButton#Danger:hover {{ background: {RED_DIM}; }}

QToolButton#Segment {{
    background: transparent; border: none; border-radius: {RADIUS_S - 2}px; padding: 5px 12px;
    color: {MUTED}; font-weight: 600;
}}
QToolButton#Segment:hover {{ color: {TEXT}; }}
QToolButton#Segment:checked {{ background: {SURFACE_3}; color: {TEXT}; }}
QWidget#SegmentBar {{ background: {BG}; border: 1px solid {LINE}; border-radius: {RADIUS_S}px; }}

QListWidget {{ background: transparent; border: none; outline: none; }}
QListWidget::item {{ border: none; padding: 0px; margin: 0px 0px 6px 0px; }}
QListWidget::item:selected {{ background: transparent; }}

QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {CYAN}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {CYAN}; }}

QProgressBar {{
    background: {BG}; border: 1px solid {LINE}; border-radius: 5px; height: 10px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 4px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {AMBER}, stop:1 #FFCF6B);
}}

QCheckBox {{ spacing: 8px; color: {MUTED}; }}
QCheckBox:hover {{ color: {TEXT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {LINE}; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {AMBER}; border-color: {AMBER}; }}

QComboBox {{
    background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: {RADIUS_S}px; padding: 5px 10px;
}}
QComboBox:hover {{ border-color: #34425A; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_2}; border: 1px solid {LINE}; selection-background-color: {SURFACE_3};
}}
QDoubleSpinBox {{
    background: {SURFACE_2}; border: 1px solid {LINE}; border-radius: {RADIUS_S}px; padding: 4px 8px;
    font-family: "{Fonts.mono}";
}}

QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #34425A; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {SURFACE_3}; color: {TEXT}; border: 1px solid {LINE}; padding: 6px; border-radius: 6px; }}
QPlainTextEdit {{
    background: {BG}; border: 1px solid {LINE}; border-radius: {RADIUS_S}px; color: {MUTED};
    font-family: "{Fonts.mono}"; font-size: 8.5pt; padding: 4px;
}}
QMessageBox {{ background: {SURFACE}; }}
"""


def apply(app: QApplication) -> None:
    Fonts.load()
    app.setStyle("Fusion")
    palette = QPalette()
    for role, value in ((QPalette.ColorRole.Window, BG), (QPalette.ColorRole.Base, SURFACE_2),
                        (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.WindowText, TEXT),
                        (QPalette.ColorRole.Button, SURFACE_2), (QPalette.ColorRole.ButtonText, TEXT),
                        (QPalette.ColorRole.Highlight, AMBER), (QPalette.ColorRole.HighlightedText, "#1A1204"),
                        (QPalette.ColorRole.ToolTipBase, SURFACE_3), (QPalette.ColorRole.ToolTipText, TEXT)):
        palette.setColor(role, QColor(value))
    app.setPalette(palette)
    app.setFont(font("body", 10))
    app.setStyleSheet(stylesheet())
