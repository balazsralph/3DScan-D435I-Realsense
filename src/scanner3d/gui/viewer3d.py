"""Vue 3D (VTK via PyVista) : nuage de points en direct ou modèle reconstruit, plusieurs rendus."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyvista as pv
from PySide6.QtWidgets import QVBoxLayout, QWidget
from pyvistaqt import QtInteractor

from . import theme

RENDER_MODES = (("couleur", "Couleur"), ("relief", "Relief"), ("fil", "Maillage"))
RELIEF_COLOR = "#D7DCE3"
CAMERA_TO_VIEW = np.array([1.0, -1.0, -1.0], dtype=np.float32)  # repère caméra (y bas, z devant) -> vue


class Viewer3D(QWidget):
    """Affiche un nuage de points (mis à jour en continu) ou un maillage chargé depuis un PLY."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.plotter = QtInteractor(self, auto_update=False)
        layout.addWidget(self.plotter.interactor)
        self.plotter.set_background(theme.BG, top="#1B2330")
        self.plotter.enable_anti_aliasing("ssaa")
        self.plotter.add_axes(color=theme.MUTED, line_width=2, labels_off=False)
        self._mode = "couleur"
        self._mesh: pv.PolyData | None = None
        self._mesh_units = "mm"
        self._cloud: pv.PolyData | None = None
        self._cloud_actor = None
        self._show_bounds = False
        self._message_actor = None
        self._point_px = 4.0 * max(self.devicePixelRatioF(), 1.0)  # écrans HiDPI : points lisibles
        self.content = "vide"  # "vide", "nuage" ou "modele"
        self.show_message("Aperçu 3D\nLe nuage en direct ou le modèle construit s'affichera ici.")

    # -- messages -----------------------------------------------------------------------------
    def show_message(self, text: str) -> None:
        self._clear_message()
        self._message_actor = self.plotter.add_text(text, position="upper_left", font_size=10,
                                                    color=theme.MUTED, name="message")
        self.plotter.render()

    def _clear_message(self) -> None:
        if self._message_actor is not None:
            self.plotter.remove_actor(self._message_actor, render=False)
            self._message_actor = None

    # -- nuage de points ----------------------------------------------------------------------
    def show_points(self, points: np.ndarray, colors: np.ndarray, camera_frame: bool = True,
                    reset_camera: bool = False) -> None:
        """Remplace le nuage affiché. `camera_frame` : points dans le repère de la caméra RealSense."""
        if len(points) == 0:
            return
        if self.content != "nuage":
            self._enter_cloud_mode()
        cloud = pv.PolyData(points * CAMERA_TO_VIEW if camera_frame else points)
        cloud.point_data["rgb"] = colors
        first = self._cloud is None
        if first:
            self._cloud = cloud
            self._cloud_actor = self.plotter.add_mesh(cloud, scalars="rgb", rgb=True, point_size=self._point_px,
                                                      render_points_as_spheres=True, lighting=False,
                                                      name="cloud", show_scalar_bar=False)
        else:
            self._cloud.shallow_copy(cloud)
        if first or reset_camera:
            self._camera_from_sensor(points)
        self.plotter.render()

    def _camera_from_sensor(self, points: np.ndarray) -> None:
        """Vue placée comme la caméra RealSense, en léger recul."""
        depth = float(np.median(points[:, 2])) if len(points) else 1.0
        self.plotter.camera_position = [(0.0, 0.0, depth * 0.35), (0.0, 0.0, -depth), (0.0, 1.0, 0.0)]
        self.plotter.reset_camera_clipping_range()

    def _enter_cloud_mode(self) -> None:
        self.plotter.clear_actors()
        self._message_actor = None
        self._mesh = None
        self._cloud = None
        self.plotter.remove_bounds_axes()
        self.content = "nuage"

    def clear_cloud(self) -> None:
        if self.content == "nuage":
            self.plotter.clear_actors()
            self._cloud = None
            self.content = "vide"

    # -- modèle -------------------------------------------------------------------------------
    def show_model(self, path: Path, units: str = "mm") -> tuple[int, int]:
        """Charge et affiche un maillage ; renvoie (sommets, triangles)."""
        mesh = pv.read(str(path))
        if not isinstance(mesh, pv.PolyData) or mesh.n_points == 0:
            raise ValueError(f"{path.name} : maillage vide ou illisible")
        self.plotter.clear_actors()
        self._message_actor = None
        self._cloud = None
        self._mesh = mesh.compute_normals(auto_orient_normals=False, split_vertices=False) \
            if "Normals" not in mesh.point_data else mesh
        self._mesh_units = units
        self.content = "modele"
        self._draw_mesh()
        self.plotter.view_isometric()
        self.plotter.camera.up = (0.0, 0.0, 1.0)
        self.plotter.reset_camera()
        self.plotter.camera.zoom(1.15)
        self.plotter.render()
        return mesh.n_points, mesh.n_cells

    def _draw_mesh(self) -> None:
        if self._mesh is None:
            return
        self.plotter.remove_actor("model", render=False)
        has_rgb = "RGB" in self._mesh.point_data or "RGBA" in self._mesh.point_data
        common = {"name": "model", "smooth_shading": True, "show_scalar_bar": False}
        if self._mode == "couleur" and has_rgb:
            scalars = "RGB" if "RGB" in self._mesh.point_data else "RGBA"
            self.plotter.add_mesh(self._mesh, scalars=scalars, rgb=True, ambient=0.45, diffuse=0.75, specular=0.1,
                                  **common)
        elif self._mode == "fil":
            self.plotter.add_mesh(self._mesh, color="#2A3442", show_edges=True, edge_color=theme.CYAN,
                                  line_width=0.6, **common)
        else:
            self.plotter.add_mesh(self._mesh, color=RELIEF_COLOR, specular=0.35, specular_power=18,
                                  ambient=0.12, diffuse=0.85, **common)
        self._update_bounds()

    def _update_bounds(self) -> None:
        self.plotter.remove_bounds_axes()
        if self._show_bounds and self._mesh is not None:
            unit = "mm" if self._mesh_units == "mm" else "m"
            self.plotter.show_bounds(mesh=self._mesh, grid="back", location="outer", color=theme.MUTED,
                                     xtitle=f"X ({unit})", ytitle=f"Y ({unit})", ztitle=f"Z ({unit})",
                                     font_size=9, fmt="%.0f" if unit == "mm" else "%.2f", render=False)

    # -- réglages -----------------------------------------------------------------------------
    def set_render_mode(self, mode: str) -> None:
        self._mode = mode
        if self.content == "modele":
            self._draw_mesh()
            self.plotter.render()

    def set_bounds_visible(self, visible: bool) -> None:
        self._show_bounds = visible
        if self.content == "modele":
            self._update_bounds()
            self.plotter.render()

    def reset_view(self) -> None:
        if self.content == "modele":
            self.plotter.view_isometric()
            self.plotter.camera.up = (0.0, 0.0, 1.0)
            self.plotter.reset_camera()
        elif self._cloud is not None:
            self._camera_from_sensor(np.asarray(self._cloud.points) * CAMERA_TO_VIEW)
        self.plotter.render()

    def view_top(self) -> None:
        if self.content == "modele":
            self.plotter.view_xy()
            self.plotter.render()

    def screenshot(self, path: Path) -> None:
        self.plotter.screenshot(str(path))

    def close_viewer(self) -> None:
        self.plotter.close()
