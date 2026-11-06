"""Shared controller utilities for the standalone OsmAnd map widgets."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Protocol, Sequence

from PySide6.QtCore import QObject, QPointF, QTimer
from PySide6.QtGui import QPainter

from maps.errors import TileLoadingError
from maps.map_sources import MapBackendMetadata, MapSourceSpec
from maps.tile_backend import OsmAndRasterBackend

from .input_handler import InputHandler
from .map_renderer import CityAnnotation, MapRenderer
from .tile_collector import collect_tiles, request_tiles
from .tile_manager import TileManager
from .viewport import compute_view_state


class SupportsMapViewport(Protocol):
    """Minimal interface the rendering controller expects from the widget."""

    def update(self) -> None:
        ...

    def width(self) -> int:
        ...

    def height(self) -> int:
        ...

    def setCursor(self, cursor) -> None:
        ...

    def unsetCursor(self) -> None:
        ...

    def setMouseTracking(self, enabled: bool) -> None:
        ...

    def setMinimumSize(self, width: int, height: int) -> None:
        ...

    def devicePixelRatioF(self) -> float:
        ...


class MapWidgetBase(Protocol):
    """Structural typing hook used by ``main.py`` for widget factories."""

    @property
    def zoom(self) -> float:
        ...

    def width(self) -> int:
        ...

    def height(self) -> int:
        ...

    def set_zoom(self, zoom: float) -> None:
        ...

    def reset_view(self) -> None:
        ...

    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        ...

    def center_lonlat(self) -> tuple[float, float]:
        ...

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        ...

    def setFocus(self) -> None:
        ...

    def shutdown(self) -> None:
        ...

    def map_backend_metadata(self) -> MapBackendMetadata:
        ...

    def set_city_annotations(self, cities: Sequence[CityAnnotation]) -> None:
        ...

    def city_at(self, position: QPointF) -> str | None:
        ...

    def event_target(self) -> QObject:
        ...


class MapWidgetController:
    """Encapsulate rendering, tile management, and input handling logic."""

    TILE_SIZE = 256

    def __init__(
        self,
        widget: SupportsMapViewport,
        *,
        map_source: MapSourceSpec | None = None,
        tile_root: Path | str = "",
        style_path: Path | str = "",
    ) -> None:
        del tile_root, style_path

        self._widget = widget
        self._view_listeners: list[Callable[[float, float, float], None]] = []
        self._pan_listeners: list[Callable[[QPointF], None]] = []
        self._pan_finished_listeners: list[Callable[[], None]] = []

        package_root = Path(__file__).resolve().parent.parent
        requested_source = self._resolve_source_spec(package_root, map_source=map_source)

        self._tile_backend = OsmAndRasterBackend(requested_source)
        self._tile_manager = TileManager(
            self._tile_backend,
            cache_limit=256,
            worker_count=TileManager.DEFAULT_WORKER_COUNT,
            backend_factory=lambda: OsmAndRasterBackend(requested_source),
            parent=self._widget,
        )
        self._backend_metadata = self._tile_manager.metadata
        self._renderer = MapRenderer(
            tile_manager=self._tile_manager,
            tile_size=self.TILE_SIZE,
        )
        self._renderer.set_cities([])
        self._input_handler = InputHandler(
            min_zoom=self._backend_metadata.min_zoom,
            max_zoom=self._backend_metadata.max_zoom,
            parent=self._widget,
        )

        self._tile_manager.tile_loaded.connect(self._handle_tile_loaded)
        self._tile_manager.tile_missing.connect(self._handle_tile_missing)
        self._tile_manager.tile_removed.connect(self._handle_tile_removed)

        self._input_handler.pan_requested.connect(self._on_pan_requested)
        self._input_handler.pan_requested.connect(self._notify_pan_delta)
        self._input_handler.pan_finished.connect(self._notify_pan_finished)
        self._input_handler.zoom_requested.connect(self._on_zoom_requested)
        self._input_handler.cursor_changed.connect(self._widget.setCursor)
        self._input_handler.cursor_reset.connect(self._widget.unsetCursor)

        # Timer to debounce tile requests during resize
        self._resize_timer = QTimer(self._widget)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)
        self._resize_timer.timeout.connect(self._request_initial_tiles)

        self._center_x = 0.5
        self._center_y = 0.5
        self._min_zoom = float(self._backend_metadata.min_zoom)
        self._max_zoom = float(self._backend_metadata.max_zoom)
        self._default_zoom = min(max(2.0, self._min_zoom), self._max_zoom)
        self._zoom = self._default_zoom
        self._device_scale = 1.0
        self._cities: list[CityAnnotation] = []
        self._tile_manager.set_device_scale(self._device_pixel_ratio())

        # Pre-load initial visible tiles to avoid blank screen on first render.
        # This is especially important on Linux where the first paint event
        # may occur before any tiles have been requested.
        self._request_initial_tiles()

    @property
    def zoom(self) -> float:
        return self._zoom

    def map_backend_metadata(self) -> MapBackendMetadata:
        return self._backend_metadata

    def set_zoom(self, zoom: float) -> None:
        zoom = max(self._min_zoom, min(self._max_zoom, zoom))
        if zoom == self._zoom:
            return
        self._zoom = zoom
        self._request_repaint()
        self._notify_view_changed()

    def reset_view(self) -> None:
        self._center_x = 0.5
        self._center_y = 0.5
        self.set_zoom(self._default_zoom)
        self._request_repaint()
        self._notify_view_changed()

    def handle_resize(self) -> None:
        """Schedule a tile re-request after widget resize.

        This ensures tiles are requested for the correct viewport size
        when the widget is first shown or resized.
        """
        self._resize_timer.start()

    def pan_by_pixels(self, delta_x: float, delta_y: float) -> None:
        world_size = self._world_size()
        self._center_x -= float(delta_x) / world_size
        self._center_y -= float(delta_y) / world_size
        self._wrap_center()
        self._request_repaint()
        self._notify_view_changed()

    def center_lonlat(self) -> tuple[float, float]:
        return self._normalized_to_lonlat(self._center_x, self._center_y)

    def shutdown(self) -> None:
        self._tile_manager.shutdown()

    def render(self, painter: QPainter) -> None:
        current_scale = self._device_pixel_ratio()
        if abs(current_scale - self._device_scale) > 1e-6:
            self._device_scale = current_scale
            self._tile_manager.set_device_scale(current_scale)

        painter.setRenderHint(QPainter.Antialiasing, True)
        self._renderer.render(
            painter,
            center_x=self._center_x,
            center_y=self._center_y,
            zoom=self._zoom,
            width=self._widget.width(),
            height=self._widget.height(),
        )

    def set_cities(self, cities: Sequence[CityAnnotation]) -> None:
        new_cities = list(cities)
        if new_cities == self._cities:
            return
        self._cities = new_cities
        self._renderer.set_cities(self._cities)
        self._request_repaint()

    def city_at(self, position: QPointF) -> str | None:
        return self._renderer.city_at(position)

    def handle_mouse_press(self, event) -> None:
        self._input_handler.handle_mouse_press(event)

    def handle_mouse_move(self, event) -> None:
        self._input_handler.handle_mouse_move(event)

    def handle_mouse_release(self, event) -> None:
        self._input_handler.handle_mouse_release(event)

    def handle_wheel_event(self, event) -> None:
        self._input_handler.handle_wheel_event(event, self._zoom)

    def add_view_listener(self, callback: Callable[[float, float, float], None]) -> None:
        if callback not in self._view_listeners:
            self._view_listeners.append(callback)

    def add_pan_listener(self, callback: Callable[[QPointF], None]) -> None:
        if callback not in self._pan_listeners:
            self._pan_listeners.append(callback)

    def add_pan_finished_listener(self, callback: Callable[[], None]) -> None:
        if callback not in self._pan_finished_listeners:
            self._pan_finished_listeners.append(callback)

    def project_lonlat(self, lon: float, lat: float) -> QPointF | None:
        world_position = self._lonlat_to_world(lon, lat)
        if world_position is None:
            return None

        world_x, world_y = world_position
        world_size = self._world_size()

        center_px = self._center_x * world_size
        center_py = self._center_y * world_size

        delta_x = world_x - center_px
        if delta_x > world_size / 2.0:
            world_x -= world_size
        elif delta_x < -world_size / 2.0:
            world_x += world_size

        top_left_x = center_px - self._widget.width() / 2.0
        top_left_y = center_py - self._widget.height() / 2.0

        screen_x = world_x - top_left_x
        screen_y = world_y - top_left_y
        return QPointF(screen_x, screen_y)

    def center_on(self, lon: float, lat: float) -> None:
        world_position = self._lonlat_to_world(lon, lat)
        if world_position is None:
            return
        world_x, world_y = world_position
        world_size = self._world_size()
        self._center_x = (world_x / world_size) % 1.0
        self._center_y = world_y / world_size
        self._wrap_center()
        self._request_repaint()
        self._notify_view_changed()

    def focus_on(self, lon: float, lat: float, zoom_delta: float = 1.0) -> None:
        self.center_on(lon, lat)
        if zoom_delta:
            self.set_zoom(self._zoom + zoom_delta)

    def view_state(self) -> tuple[float, float, float]:
        return self._center_x, self._center_y, self._zoom

    def _request_repaint(self) -> None:
        full_update = getattr(self._widget, "request_full_update", None)
        if callable(full_update):
            full_update()
            return
        self._widget.update()

    def _on_pan_requested(self, delta: QPointF) -> None:
        self.pan_by_pixels(delta.x(), delta.y())

    def _notify_pan_delta(self, delta: QPointF) -> None:
        for callback in list(self._pan_listeners):
            try:
                callback(delta)
            except Exception:
                continue

    def _notify_pan_finished(self) -> None:
        for callback in list(self._pan_finished_listeners):
            try:
                callback()
            except Exception:
                continue

    def _on_zoom_requested(self, new_zoom: float, anchor: QPointF) -> None:
        world_size = self._world_size()
        center_px = self._center_x * world_size
        center_py = self._center_y * world_size
        view_top_left_x = center_px - self._widget.width() / 2.0
        view_top_left_y = center_py - self._widget.height() / 2.0

        mouse_world_x = (view_top_left_x + anchor.x()) / world_size
        mouse_world_y = (view_top_left_y + anchor.y()) / world_size

        self._zoom = max(self._min_zoom, min(self._max_zoom, new_zoom))
        new_world_size = self._world_size()
        new_center_px = mouse_world_x * new_world_size - anchor.x() + self._widget.width() / 2.0
        new_center_py = mouse_world_y * new_world_size - anchor.y() + self._widget.height() / 2.0

        self._center_x = new_center_px / new_world_size
        self._center_y = new_center_py / new_world_size
        self._wrap_center()
        self._request_repaint()
        self._notify_view_changed()

    def _handle_tile_loaded(self, tile_key: tuple[int, int, int]) -> None:
        self._renderer.invalidate_tile(tile_key)
        self._request_repaint()

    def _handle_tile_missing(self, tile_key: tuple[int, int, int]) -> None:
        self._renderer.invalidate_tile(tile_key)
        self._request_repaint()

    def _handle_tile_removed(self, tile_key: tuple[int, int, int]) -> None:
        self._renderer.invalidate_tile(tile_key)
        self._request_repaint()

    def _world_size(self) -> float:
        return float(self.TILE_SIZE * (2 ** self._zoom))

    def _wrap_center(self) -> None:
        self._center_x %= 1.0

        world_size = self._world_size()
        viewport_height = max(1, self._widget.height())
        half_view_ratio = viewport_height / (2.0 * world_size)

        if half_view_ratio >= 0.5:
            self._center_y = 0.5
            return

        min_center = half_view_ratio
        max_center = 1.0 - half_view_ratio
        self._center_y = min(max(self._center_y, min_center), max_center)

    def _notify_view_changed(self) -> None:
        for callback in list(self._view_listeners):
            try:
                callback(self._center_x, self._center_y, self._zoom)
            except Exception:
                continue

    def _resolve_source_spec(
        self,
        package_root: Path,
        *,
        map_source: MapSourceSpec | None,
    ) -> MapSourceSpec:
        if map_source is None:
            return MapSourceSpec.osmand_default(package_root).resolved(package_root)

        resolved = map_source.resolved(package_root)
        if resolved.kind != "osmand_obf":
            raise TileLoadingError("This standalone preview only supports OsmAnd OBF sources")
        return resolved

    def _device_pixel_ratio(self) -> float:
        ratio_getter = getattr(self._widget, "devicePixelRatioF", None)
        if callable(ratio_getter):
            try:
                return max(1.0, float(ratio_getter()))
            except Exception:
                return 1.0
        return 1.0

    def _request_initial_tiles(self) -> None:
        """Request tiles for the initial viewport before the first paint.

        This ensures tiles start loading immediately on widget creation,
        preventing a blank screen on Linux where paint events may be delayed.
        """
        # Use default widget size if not yet sized - this is critical for
        # Linux where the widget may not have a proper size during construction.
        width = max(640, self._widget.width())
        height = max(480, self._widget.height())

        fetch_max_zoom = self._tile_manager.metadata.fetch_max_zoom
        if fetch_max_zoom is None:
            fetch_max_zoom = max(0, int(self._tile_manager.metadata.max_zoom))

        view_state = compute_view_state(
            self._center_x,
            self._center_y,
            self._zoom,
            width,
            height,
            self.TILE_SIZE,
            max_tile_zoom_level=fetch_max_zoom,
        )
        _, tiles_to_request = collect_tiles(view_state, self._tile_manager)
        request_tiles(tiles_to_request, self._tile_manager)

    def _lonlat_to_world(self, lon: float, lat: float) -> tuple[float, float] | None:
        try:
            lon = float(lon)
            lat = max(min(float(lat), 85.05112878), -85.05112878)
        except (TypeError, ValueError):
            return None

        world_size = self._world_size()
        x = (lon + 180.0) / 360.0 * world_size
        sin_lat = math.sin(math.radians(lat))
        y = (0.5 - math.log((1 + sin_lat) / (1 - sin_lat)) / (4 * math.pi)) * world_size
        return x, y

    @staticmethod
    def _normalized_to_lonlat(center_x: float, center_y: float) -> tuple[float, float]:
        wrapped_x = float(center_x) % 1.0
        clamped_y = min(max(float(center_y), 0.0), 1.0)
        lon = wrapped_x * 360.0 - 180.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * clamped_y))))
        return lon, lat


__all__ = ["MapWidgetBase", "MapWidgetController", "SupportsMapViewport"]
