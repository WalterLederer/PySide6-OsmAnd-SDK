"""Standalone entry point for the PySide6/OsmAnd preview application."""

from __future__ import annotations

if __package__ in {None, ""}:  # pragma: no cover - direct script bootstrap
    import sys
    from pathlib import Path

    _SRC_ROOT = Path(__file__).resolve().parents[1]
    if str(_SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(_SRC_ROOT))

import argparse
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QEvent, QObject, QLocale, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QOffscreenSurface, QOpenGLContext
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from maps.errors import TileLoadingError
from maps.map_sources import (
    MapBackendMetadata,
    MapSourceSpec,
    has_usable_osmand_default,
    has_usable_osmand_native_widget,
)
from maps.map_widget import MapGLWidget, MapWidget, NativeOsmAndWidget
from maps.map_widget._map_widget_base import MapWidgetBase
from maps.map_widget.native_osmand_widget import probe_native_widget_runtime
from maps.osmand_search import OsmAndSearchService, SearchSuggestion


@dataclass(frozen=True)
class PreviewLaunchConfig:
    """Describe the backend setup requested for the standalone preview."""

    map_source: MapSourceSpec
    widget_class: type[MapWidgetBase]
    native_widget_class: type[MapWidgetBase] | None
    startup_message: str


class SearchWorker(QObject):
    """Run blocking offline searches off the UI thread and keep only the latest request."""

    resultsReady = Signal(int, object)
    errorReady = Signal(int, str)

    def __init__(self, service: OsmAndSearchService) -> None:
        super().__init__()
        self._service = service
        self._lock = threading.Lock()
        self._wake_event = threading.Event()
        self._stopped = False
        self._pending_request: tuple[int, str, str, int, bool] | None = None
        self._thread = threading.Thread(target=self._run, name="OsmAndSearchWorker", daemon=True)
        self._thread.start()

    def submit(
        self,
        request_id: int,
        query: str,
        *,
        locale: str,
        limit: int,
        include_poi_fallback: bool,
    ) -> None:
        with self._lock:
            self._pending_request = (request_id, query, locale, limit, include_poi_fallback)
            self._wake_event.set()
        self._service.abort()

    def shutdown(self) -> None:
        with self._lock:
            self._stopped = True
            self._pending_request = None
            self._wake_event.set()
        self._service.abort()
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            print(
                "[maps.main] SearchWorker: worker thread did not stop within timeout;"
                " skipping service shutdown to avoid use-after-close",
                flush=True,
            )
            return
        self._service.shutdown()

    def _run(self) -> None:
        while True:
            self._wake_event.wait()
            with self._lock:
                if self._stopped:
                    return
                request = self._pending_request
                self._pending_request = None
                self._wake_event.clear()

            if request is None:
                continue

            request_id, query, locale, limit, include_poi_fallback = request
            try:
                results = self._service.search(
                    query,
                    limit=limit,
                    locale=locale,
                    include_poi_fallback=include_poi_fallback,
                )
            except Exception as exc:
                with self._lock:
                    if self._stopped:
                        return
                self.errorReady.emit(request_id, str(exc))
                continue

            with self._lock:
                if self._stopped:
                    return
            self.resultsReady.emit(request_id, results)


def check_opengl_support() -> bool:
    """Return ``True`` when the system can create a basic OpenGL context."""

    try:
        surface = QOffscreenSurface()
        surface.create()
        if not surface.isValid():
            return False

        context = QOpenGLContext()
        if not context.create():
            return False

        if not context.makeCurrent(surface):
            return False

        context.doneCurrent()
        return True
    except Exception:
        return False


def choose_native_widget_class(
    package_root: Path,
    *,
    use_opengl: bool,
    prefer_native_widget: bool = True,
) -> tuple[type[MapWidgetBase] | None, str]:
    if not use_opengl:
        return None, "OpenGL support unavailable. Falling back to the helper-backed CPU renderer."

    if not prefer_native_widget:
        return None, "OpenGL support detected. Using the helper-backed Python OBF renderer."

    if not has_usable_osmand_native_widget(package_root):
        return None, "OpenGL support detected. Native widget unavailable, using the Python OBF renderer."

    is_available, reason = probe_native_widget_runtime(package_root)
    if is_available:
        return NativeOsmAndWidget, "OpenGL support detected. Using the native OsmAnd widget when available."

    detail = f" Native widget disabled: {reason}." if reason else ""
    return None, f"OpenGL support detected.{detail} Using the Python OBF renderer."


def prepare_qt_runtime_for_backend(backend: str) -> None:
    """Adjust Qt startup on Linux before ``QApplication`` is constructed.

    The native OsmAnd widget depends on GLEW, which expects a GLX-backed
    desktop OpenGL context on Linux. Qt will otherwise often prefer a Wayland
    or EGL path that leaves the native widget stuck on the clear-color frame.
    """

    normalized_backend = backend.strip().lower()
    if sys.platform != "linux" or normalized_backend == "python":
        return

    if not os.environ.get("QT_QPA_PLATFORM"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    if os.environ.get("QT_QPA_PLATFORM") == "xcb":
        os.environ.setdefault("QT_OPENGL", "desktop")
        os.environ.setdefault("QT_XCB_GL_INTEGRATION", "xcb_glx")
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True)


def build_argument_parser() -> argparse.ArgumentParser:
    """Return the CLI parser used by the standalone preview entry point."""

    parser = argparse.ArgumentParser(description="Preview OsmAnd OBF rendering backends")
    parser.add_argument(
        "--backend",
        choices=("auto", "native", "python"),
        default="auto",
        help="Select the startup renderer explicitly instead of auto-detecting it.",
    )
    parser.add_argument(
        "--center",
        nargs=2,
        metavar=("LON", "LAT"),
        type=float,
        help="Center the initial view on the provided longitude/latitude pair.",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        help="Set the initial zoom level after the window has been created.",
    )
    parser.add_argument(
        "--screenshot",
        type=Path,
        help="Save a screenshot after startup and exit once the image is written.",
    )
    parser.add_argument(
        "--capture-delay-ms",
        type=int,
        default=1500,
        help="How long to wait before taking --screenshot (default: 1500).",
    )
    return parser


def choose_launch_configuration(
    package_root: Path,
    *,
    use_opengl: bool,
    backend: str,
) -> PreviewLaunchConfig:
    """Resolve the startup backend requested on the command line."""

    widget_cls: type[MapWidgetBase] = MapGLWidget if use_opengl else MapWidget
    normalized_backend = backend.strip().lower()
    default_source = MapSourceSpec.osmand_default(package_root)

    if normalized_backend == "auto":
        native_widget_cls, startup_message = choose_native_widget_class(
            package_root,
            use_opengl=use_opengl,
        )
        if native_widget_cls is None and not has_usable_osmand_default(package_root):
            raise TileLoadingError(
                "Neither the native widget nor the helper-backed Python renderer is available",
            )
        return PreviewLaunchConfig(
            map_source=default_source,
            widget_class=widget_cls,
            native_widget_class=native_widget_cls,
            startup_message=startup_message,
        )

    if normalized_backend == "native":
        if not use_opengl:
            raise TileLoadingError("OpenGL support is unavailable, so the native OsmAnd widget can not be forced")
        if not has_usable_osmand_native_widget(package_root):
            raise TileLoadingError("The native OsmAnd widget library is not available")
        is_available, reason = probe_native_widget_runtime(package_root)
        if not is_available:
            detail = f": {reason}" if reason else ""
            raise TileLoadingError(f"The native OsmAnd widget failed its runtime probe{detail}")
        return PreviewLaunchConfig(
            map_source=default_source,
            widget_class=widget_cls,
            native_widget_class=NativeOsmAndWidget,
            startup_message="OpenGL support detected. Forcing the native OsmAnd widget.",
        )

    if normalized_backend == "python":
        if not has_usable_osmand_default(package_root):
            raise TileLoadingError(
                "The OsmAnd helper backend is unavailable, so the Python OBF renderer can not be forced",
            )
        renderer_label = "GPU accelerated" if use_opengl else "CPU"
        return PreviewLaunchConfig(
            map_source=default_source,
            widget_class=widget_cls,
            native_widget_class=None,
            startup_message=f"Forcing the {renderer_label} Python OBF renderer.",
        )

    raise ValueError(f"unsupported backend mode: {backend}")


def _backend_kind_for_widget(
    map_widget: MapWidgetBase,
    *,
    map_source: MapSourceSpec,
) -> str:
    del map_source
    if isinstance(map_widget, NativeOsmAndWidget):
        return "osmand_native"
    return "osmand_python"


def _confirmed_gl_state(
    map_widget: MapWidgetBase,
    *,
    backend_kind: str,
) -> str:
    if backend_kind == "osmand_native":
        return "true"
    if isinstance(map_widget, MapGLWidget):
        return "true"
    if isinstance(map_widget, MapWidget):
        return "false"
    return "unknown"


def format_map_runtime_diagnostics(
    map_widget: MapWidgetBase,
    *,
    map_source: MapSourceSpec,
) -> str:
    """Return a one-line runtime summary that proves whether GL is active."""

    backend_kind = _backend_kind_for_widget(map_widget, map_source=map_source)
    metadata = map_widget.map_backend_metadata()
    event_target = map_widget.event_target()
    event_target_name = getattr(event_target, "objectName", lambda: "")()
    if not event_target_name:
        event_target_name = type(event_target).__name__
    native_library_path = getattr(map_widget, "loaded_library_path", lambda: None)()
    native_library_suffix = ""
    if native_library_path:
        native_library_suffix = f" native_library={native_library_path}"

    return (
        "[maps.main] "
        f"backend={backend_kind} "
        f"confirmed_gl={_confirmed_gl_state(map_widget, backend_kind=backend_kind)} "
        f"widget={type(map_widget).__name__} "
        f"event_target={event_target_name} "
        f"source={map_source.kind} "
        f"tile_kind={metadata.tile_kind} "
        f"tile_scheme={metadata.tile_scheme}"
        f"{native_library_suffix}"
    )


def format_status_message(
    *,
    backend_label: str,
    requested_source: MapSourceSpec,
    metadata: MapBackendMetadata,
    zoom: float,
    longitude: float,
    latitude: float,
) -> str:
    """Summarize the current map state for the status bar."""

    del metadata
    source_path = Path(requested_source.data_path).name
    return (
        f"{backend_label} | Zoom {zoom:.2f} | Center {latitude:.4f}, {longitude:.4f}"
        f" | Source {source_path}"
    )


class MainWindow(QMainWindow):
    """Primary application window that hosts an interactive map widget."""

    PAN_FRACTION = 0.18
    SEARCH_RESULT_LIMIT = 5
    SEARCH_DEBOUNCE_MS = 80
    SEARCH_FOCUS_ZOOM = 8.0

    def __init__(
        self,
        *,
        map_source: MapSourceSpec | None = None,
        widget_class: type[MapWidgetBase] | None = None,
        native_widget_class: type[MapWidgetBase] | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("OsmAnd Preview")
        self.resize(1280, 860)

        self._package_root = Path(__file__).resolve().parent
        self._widget_cls: type[MapWidgetBase] = widget_class or MapWidget
        self._native_widget_cls = native_widget_class
        self._runtime_diagnostics = ""
        self._map_source = (map_source or MapSourceSpec.osmand_default(self._package_root)).resolved(
            self._package_root,
        )
        self._search_worker: SearchWorker | None = None
        self._search_results_data: list[SearchSuggestion] = []
        self._search_cache: dict[str, list[SearchSuggestion]] = {}
        self._latest_search_request_id = 0

        self._create_search_ui()
        self._reset_search_service(self._map_source)

        self._map_widget: MapWidgetBase | None = None
        initial_widget = self._create_map_widget(map_source=self._map_source)
        self._set_central_map(initial_widget)

        self._create_actions()
        self._create_menus()
        self.statusBar().showMessage("Ready")
        self._refresh_window_chrome()
        self._announce_backend_state()

    def _create_actions(self) -> None:
        self._action_zoom_in = QAction("Zoom In", self)
        self._action_zoom_in.setShortcuts([QKeySequence("+"), QKeySequence("=")])
        self._action_zoom_in.triggered.connect(self._zoom_in)

        self._action_zoom_out = QAction("Zoom Out", self)
        self._action_zoom_out.setShortcuts([QKeySequence("-"), QKeySequence("_")])
        self._action_zoom_out.triggered.connect(self._zoom_out)

        self._action_reset_view = QAction("Reset View", self)
        self._action_reset_view.setShortcuts([QKeySequence("Home"), QKeySequence("R")])
        self._action_reset_view.triggered.connect(self._reset_view)

        self._action_pan_left = QAction("Pan Left", self)
        self._action_pan_left.setShortcuts([QKeySequence("Left"), QKeySequence("A")])
        self._action_pan_left.triggered.connect(lambda: self._pan_by_fraction(-self.PAN_FRACTION, 0.0))

        self._action_pan_right = QAction("Pan Right", self)
        self._action_pan_right.setShortcuts([QKeySequence("Right"), QKeySequence("D")])
        self._action_pan_right.triggered.connect(lambda: self._pan_by_fraction(self.PAN_FRACTION, 0.0))

        self._action_pan_up = QAction("Pan Up", self)
        self._action_pan_up.setShortcuts([QKeySequence("Up"), QKeySequence("W")])
        self._action_pan_up.triggered.connect(lambda: self._pan_by_fraction(0.0, -self.PAN_FRACTION))

        self._action_pan_down = QAction("Pan Down", self)
        self._action_pan_down.setShortcuts([QKeySequence("Down"), QKeySequence("S")])
        self._action_pan_down.triggered.connect(lambda: self._pan_by_fraction(0.0, self.PAN_FRACTION))

        self._action_open_map_source = QAction("Select OBF Source...", self)
        self._action_open_map_source.triggered.connect(self._open_map_source)

    def _create_menus(self) -> None:
        menu_bar = self.menuBar()

        view_menu = menu_bar.addMenu("View")
        view_menu.addAction(self._action_zoom_in)
        view_menu.addAction(self._action_zoom_out)
        view_menu.addAction(self._action_reset_view)

        navigate_menu = menu_bar.addMenu("Navigate")
        navigate_menu.addAction(self._action_pan_left)
        navigate_menu.addAction(self._action_pan_right)
        navigate_menu.addAction(self._action_pan_up)
        navigate_menu.addAction(self._action_pan_down)

        file_menu = menu_bar.addMenu("File")
        file_menu.addAction(self._action_open_map_source)

    def _create_search_ui(self) -> None:
        self._central_container = QWidget(self)
        self._central_layout = QVBoxLayout(self._central_container)
        self._central_layout.setContentsMargins(8, 8, 8, 8)
        self._central_layout.setSpacing(6)

        self._search_input = QLineEdit(self._central_container)
        self._search_input.setClearButtonEnabled(True)
        self._search_input.setPlaceholderText("Search place names, for example 北京 or Beijing")
        self._search_input.installEventFilter(self)
        self._search_input.textEdited.connect(self._schedule_search)

        self._search_results = QListWidget(self._central_container)
        self._search_results.setMaximumHeight(180)
        self._search_results.setAlternatingRowColors(True)
        self._search_results.setVisible(False)
        self._search_results.itemActivated.connect(self._activate_search_item)
        self._search_results.itemClicked.connect(self._activate_search_item)

        self._map_host = QWidget(self._central_container)
        self._map_layout = QVBoxLayout(self._map_host)
        self._map_layout.setContentsMargins(0, 0, 0, 0)
        self._map_layout.setSpacing(0)

        self._central_layout.addWidget(self._search_input)
        self._central_layout.addWidget(self._search_results)
        self._central_layout.addWidget(self._map_host, 1)
        self.setCentralWidget(self._central_container)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(self.SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._perform_search)

    def _create_map_widget(self, *, map_source: MapSourceSpec) -> MapWidgetBase:
        if self._native_widget_cls is not None:
            try:
                return self._native_widget_cls(map_source=map_source)
            except Exception as exc:  # pragma: no cover - best effort error reporting
                print(
                    f"[main] NativeOsmAndWidget failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                self.statusBar().showMessage(
                    f"Native OsmAnd widget unavailable, falling back to the Python renderer: {exc}",
                    8000,
                )

        try:
            return self._widget_cls(map_source=map_source)
        except TileLoadingError:
            raise
        except Exception as exc:  # pragma: no cover - best effort error reporting
            if self._widget_cls is MapWidget:
                raise

            QMessageBox.warning(
                self,
                "GPU Acceleration Disabled",
                "The OpenGL based map view failed to initialize.\n"
                "The application will continue with the CPU renderer instead.\n\n"
                f"Details: {exc}",
            )
            self._widget_cls = MapWidget
            return MapWidget(map_source=map_source)

    def _zoom_in(self) -> None:
        self._map_widget.set_zoom(self._map_widget.zoom * 1.5)

    def _zoom_out(self) -> None:
        self._map_widget.set_zoom(self._map_widget.zoom / 1.5)

    def _reset_view(self) -> None:
        self._map_widget.reset_view()

    def _pan_by_fraction(self, fraction_x: float, fraction_y: float) -> None:
        self._map_widget.pan_by_pixels(
            self._map_widget.width() * fraction_x,
            self._map_widget.height() * fraction_y,
        )

    def _open_map_source(self) -> None:
        current_path = str(self._map_source.data_path)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select OBF Source",
            current_path,
            "OBF Files (*.obf);;All Files (*)",
        )
        if not path:
            return

        default_source = MapSourceSpec.osmand_default(self._package_root).resolved(self._package_root)
        new_source = MapSourceSpec(
            kind="osmand_obf",
            data_path=path,
            resources_root=default_source.resources_root,
            style_path=default_source.style_path,
        ).resolved(self._package_root)
        try:
            widget = self._create_map_widget(map_source=new_source)
        except TileLoadingError as exc:
            QMessageBox.critical(self, "Error", f"Unable to open the OBF source:\n{exc}")
            return

        self._map_source = new_source
        self._reset_search_service(new_source)
        self._clear_search_results()
        self._search_input.clear()
        self._set_central_map(widget)
        self._announce_backend_state()

    def _active_backend_label(self) -> str:
        if isinstance(self._map_widget, NativeOsmAndWidget):
            return "Native OsmAnd Widget"
        return "Python OBF Raster"

    def _refresh_window_chrome(self) -> None:
        self._update_window_title()
        self._update_status_bar()

    def _update_window_title(self) -> None:
        self.setWindowTitle(
            f"OsmAnd Preview - {self._active_backend_label()} - Zoom {self._map_widget.zoom:.2f}",
        )

    def _update_status_bar(self) -> None:
        longitude, latitude = self._map_widget.center_lonlat()
        status_text = format_status_message(
            backend_label=self._active_backend_label(),
            requested_source=self._map_source,
            metadata=self._map_widget.map_backend_metadata(),
            zoom=self._map_widget.zoom,
            longitude=longitude,
            latitude=latitude,
        )
        self.statusBar().showMessage(status_text)

    def _announce_backend_state(self) -> None:
        self._refresh_window_chrome()
        self._emit_runtime_diagnostics()

    def _emit_runtime_diagnostics(self) -> None:
        self._runtime_diagnostics = format_map_runtime_diagnostics(
            self._map_widget,
            map_source=self._map_source,
        )
        print(self._runtime_diagnostics, flush=True)

    def runtime_diagnostics(self) -> str:
        """Return the latest runtime diagnostics emitted by the preview window."""

        return self._runtime_diagnostics

    def apply_initial_view(
        self,
        *,
        center: tuple[float, float] | None = None,
        zoom: float | None = None,
    ) -> None:
        """Apply optional startup view overrides for debugging."""

        if center is not None:
            self._map_widget.center_on(center[0], center[1])
        if zoom is not None:
            self._map_widget.set_zoom(float(zoom))
        self._refresh_window_chrome()

    def capture_screenshot(self, destination: Path) -> bool:
        """Save a screenshot of the currently rendered map."""

        output_path = destination.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(self._map_widget, MapGLWidget):
            pixmap = self._map_widget.grabFramebuffer()
        else:
            pixmap = self._map_widget.grab()

        if pixmap.isNull():
            return False

        return pixmap.save(str(output_path))
        
    def _handle_view_changed(self, center_x: float, center_y: float, zoom: float) -> None:
        del center_x, center_y, zoom
        self._refresh_window_chrome()

    def _set_central_map(self, widget: MapWidgetBase) -> None:
        old = getattr(self, "_map_widget", None)
        if old is not None:
            if hasattr(old, "viewChanged"):
                try:
                    old.viewChanged.disconnect(self._handle_view_changed)  # type: ignore[attr-defined]
                except (RuntimeError, TypeError):
                    pass
            self._map_layout.removeWidget(old)  # type: ignore[arg-type]
            if hasattr(old, "shutdown"):
                old.shutdown()  # type: ignore[call-arg]
            old.setParent(None)
            old.deleteLater()

        self._map_widget = widget
        self._map_layout.addWidget(self._map_widget)
        if hasattr(self._map_widget, "viewChanged"):
            self._map_widget.viewChanged.connect(self._handle_view_changed)  # type: ignore[attr-defined]
        self._map_widget.setFocus()
        self._refresh_window_chrome()

    def _reset_search_service(self, map_source: MapSourceSpec) -> None:
        if self._search_worker is not None:
            self._search_worker.shutdown()
            self._search_worker = None
        self._search_cache.clear()
        self._latest_search_request_id = 0

        try:
            worker = SearchWorker(OsmAndSearchService(map_source))
        except Exception as exc:
            self._search_input.setEnabled(False)
            self._search_input.setPlaceholderText("Search unavailable")
            self.statusBar().showMessage(f"Search unavailable: {exc}", 8000)
            return

        worker.resultsReady.connect(self._handle_search_results)
        worker.errorReady.connect(self._handle_search_error)
        self._search_worker = worker
        self._search_input.setEnabled(True)
        self._search_input.setPlaceholderText("Search place names, for example 北京 or Beijing")

    def _should_search_query(self, query: str) -> bool:
        trimmed = query.strip()
        if not trimmed:
            return False
        if any(ord(character) > 127 for character in trimmed):
            return len(trimmed) >= 1
        return len(trimmed) >= 2

    def _preview_cached_suggestions(self, query: str) -> list[SearchSuggestion]:
        trimmed = query.strip()
        if not trimmed:
            return []

        if trimmed in self._search_cache:
            return self._search_cache[trimmed][: self.SEARCH_RESULT_LIMIT]

        best_prefix = ""
        for cached_query in self._search_cache:
            if trimmed.startswith(cached_query) and len(cached_query) > len(best_prefix):
                best_prefix = cached_query

        if not best_prefix:
            return []

        lowered_query = trimmed.casefold()
        filtered = [
            suggestion
            for suggestion in self._search_cache[best_prefix]
            if lowered_query in f"{suggestion.display_name} {suggestion.secondary_text}".casefold()
        ]
        return filtered[: self.SEARCH_RESULT_LIMIT]

    def _schedule_search(self, text: str) -> None:
        trimmed = text.strip()
        if not self._should_search_query(trimmed) or self._search_worker is None:
            self._search_timer.stop()
            self._clear_search_results()
            return
        preview = self._preview_cached_suggestions(trimmed)
        if preview:
            self._apply_search_suggestions(preview)
        self._search_timer.start()

    def _perform_search(self) -> None:
        query = self._search_input.text().strip()
        if not self._should_search_query(query) or self._search_worker is None:
            self._clear_search_results()
            return

        locale = QLocale.system().name().split("_", 1)[0].lower()
        self._latest_search_request_id += 1
        self._search_worker.submit(
            self._latest_search_request_id,
            query,
            locale=locale,
            limit=self.SEARCH_RESULT_LIMIT,
            include_poi_fallback=True,
        )

    def _handle_search_results(self, request_id: int, suggestions: list[SearchSuggestion]) -> None:
        if request_id != self._latest_search_request_id:
            return
        query = self._search_input.text().strip()
        if not self._should_search_query(query):
            self._clear_search_results()
            return

        self._search_cache[query] = suggestions[: self.SEARCH_RESULT_LIMIT]
        if len(self._search_cache) > 32:
            oldest_key = next(iter(self._search_cache))
            self._search_cache.pop(oldest_key, None)
        self._apply_search_suggestions(suggestions)

    def _handle_search_error(self, request_id: int, message: str) -> None:
        if request_id != self._latest_search_request_id:
            return
        self._clear_search_results()
        self.statusBar().showMessage(f"Search failed: {message}", 5000)

    def _apply_search_suggestions(self, suggestions: list[SearchSuggestion]) -> None:
        self._search_results.clear()
        self._search_results_data = suggestions[: self.SEARCH_RESULT_LIMIT]
        for index, suggestion in enumerate(self._search_results_data):
            item = QListWidgetItem(suggestion.display_name)
            if suggestion.secondary_text:
                item.setText(f"{suggestion.display_name} - {suggestion.secondary_text}")
                item.setToolTip(suggestion.secondary_text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self._search_results.addItem(item)

        has_results = bool(self._search_results_data)
        self._search_results.setVisible(has_results)
        if has_results:
            self._search_results.setCurrentRow(0)

    def _clear_search_results(self) -> None:
        self._search_results_data = []
        self._search_results.clear()
        self._search_results.setVisible(False)

    def _activate_search_item(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if index is None:
            return
        self._activate_search_index(int(index))

    def _activate_search_index(self, index: int) -> None:
        if index < 0 or index >= len(self._search_results_data):
            return

        suggestion = self._search_results_data[index]
        self._search_input.setText(suggestion.display_name)
        self._clear_search_results()
        self._map_widget.center_on(suggestion.longitude, suggestion.latitude)
        if self._map_widget.zoom < self.SEARCH_FOCUS_ZOOM:
            self._map_widget.set_zoom(self.SEARCH_FOCUS_ZOOM)
        self._map_widget.setFocus()
        self._refresh_window_chrome()

    def _move_search_selection(self, step: int) -> None:
        if not self._search_results_data:
            return

        current_row = self._search_results.currentRow()
        if current_row < 0:
            current_row = 0
        next_row = (current_row + step) % len(self._search_results_data)
        self._search_results.setCurrentRow(next_row)
        self._search_results.scrollToItem(self._search_results.item(next_row))
        self._search_results.setVisible(True)

    def _activate_current_search_selection(self) -> None:
        if not self._search_results_data:
            self._perform_search()
        if not self._search_results_data:
            return

        current_row = self._search_results.currentRow()
        if current_row < 0:
            current_row = 0
        self._activate_search_index(current_row)

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # type: ignore[override]
        if watched is self._search_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._move_search_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                self._move_search_selection(-1)
                return True
            if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                self._activate_current_search_selection()
                return True
            if key == Qt.Key.Key_Escape:
                self._clear_search_results()
                return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        if self._search_worker is not None:
            self._search_worker.shutdown()
            self._search_worker = None
        if self._map_widget is not None and hasattr(self._map_widget, "shutdown"):
            self._map_widget.shutdown()
        super().closeEvent(event)


def _schedule_screenshot_capture(
    app: QApplication,
    window: MainWindow,
    screenshot_path: Path,
    *,
    capture_delay_ms: int,
) -> None:
    """Capture the rendered map before shutting down its renderer."""

    delay_ms = max(0, int(capture_delay_ms))

    def _capture_and_exit() -> None:
        if window.capture_screenshot(screenshot_path):
            print(f"[maps.main] screenshot={screenshot_path.resolve()}", flush=True)
            exit_code = 0
        else:
            print(
                f"[maps.main] failed to save screenshot to {screenshot_path.resolve()}",
                file=sys.stderr,
                flush=True,
            )
            exit_code = 1

        map_widget = window._map_widget
        if hasattr(map_widget, "shutdown"):
            map_widget.shutdown()

        app.exit(exit_code)

    QTimer.singleShot(delay_ms, _capture_and_exit)
    

def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv if argv is not None else sys.argv[1:])
    parsed_args = build_argument_parser().parse_args(arguments)
    prepare_qt_runtime_for_backend(parsed_args.backend)
    app = QApplication([Path(__file__).name, *arguments])

    package_root = Path(__file__).resolve().parent
    use_opengl = check_opengl_support()
    launch_config = choose_launch_configuration(
        package_root,
        use_opengl=use_opengl,
        backend=parsed_args.backend,
    )
    print(launch_config.startup_message, flush=True)

    try:
        window = MainWindow(
            map_source=launch_config.map_source,
            widget_class=launch_config.widget_class,
            native_widget_class=launch_config.native_widget_class,
        )
    except TileLoadingError as exc:
        QMessageBox.critical(None, "Error", f"Failed to initialize map:\n{exc}")
        return 1

    if parsed_args.center is not None or parsed_args.zoom is not None:
        center = tuple(parsed_args.center) if parsed_args.center is not None else None
        window.apply_initial_view(center=center, zoom=parsed_args.zoom)

    window.show()
    if parsed_args.screenshot is not None:
        _schedule_screenshot_capture(
            app,
            window,
            parsed_args.screenshot,
            capture_delay_ms=parsed_args.capture_delay_ms,
        )
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
