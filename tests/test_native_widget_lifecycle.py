from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMetaObject, Qt, Signal
from PySide6.QtGui import QWindow
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QWidget

from maps.errors import TileLoadingError
from maps.map_sources import MapSourceSpec
from maps.map_widget import native_osmand_widget as native


class BridgeLibrary:
    def __init__(self):
        self.eager_calls = 0
        self.deferred_calls = 0
        self.initialize_calls = 0
        self.cleanup_calls = 0
        self.resources_succeed = True
        self.presented = False
        self.zoom = 2.0
        self.center = (0.0, 0.0)

    def osmand_create_map_widget(self, *args):
        self.eager_calls += 1
        self.initialize_calls += 1
        return 1

    def osmand_create_map_widget_deferred(self, *args):
        self.deferred_calls += 1
        return 1

    def osmand_widget_initialize_resources(self, *args):
        self.initialize_calls += 1
        return int(self.resources_succeed)

    def osmand_widget_has_presented_frame(self, pointer):
        return int(self.presented)

    def osmand_widget_cleanup(self, pointer):
        self.cleanup_calls += 1

    def osmand_widget_get_min_zoom(self, pointer):
        return 2.0

    def osmand_widget_get_max_zoom(self, pointer):
        return 19.0

    def osmand_widget_get_zoom(self, pointer):
        return self.zoom

    def osmand_widget_set_zoom(self, pointer, zoom):
        self.zoom = zoom

    def osmand_widget_get_center_lonlat(self, pointer, lon, lat):
        lon._obj.value, lat._obj.value = self.center

    def osmand_widget_set_center_lonlat(self, pointer, lon, lat):
        self.center = (lon, lat)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def runtime(qapp, monkeypatch, tmp_path):
    class Surface(QWidget):
        frameSwapped = Signal()

    state = SimpleNamespace(library=BridgeLibrary(), surface=Surface(), wrapped_types=[])
    state.source = MapSourceSpec(
        kind="osmand_obf", data_path=tmp_path / "world.obf",
        resources_root=tmp_path, style_path=tmp_path / "style.xml",
    )
    monkeypatch.setattr(native, "resolve_osmand_native_widget_library", lambda root: tmp_path / "native.dll")
    monkeypatch.setattr(native, "_load_bridge", lambda path: SimpleNamespace(library=state.library))
    monkeypatch.setattr(native.shiboken6, "getCppPointer", lambda widget: (1,))

    def wrap(pointer, cls):
        state.wrapped_types.append(cls)
        return state.surface

    monkeypatch.setattr(native.shiboken6, "wrapInstance", wrap)
    state.widgets = []

    def create(**kwargs):
        widget = native.NativeOsmAndWidget(map_source=state.source, **kwargs)
        state.widgets.append(widget)
        return widget

    state.create = create
    yield state
    for widget in state.widgets:
        widget.shutdown()
        widget.close()
    qapp.processEvents()


@pytest.mark.parametrize("preview", [False, True])
def test_existing_callers_initialize_eagerly(runtime, preview):
    if preview:
        from maps.main import MainWindow

        widget = MainWindow._create_map_widget(
            SimpleNamespace(_native_widget_cls=native.NativeOsmAndWidget), map_source=runtime.source,
        )
        runtime.widgets.append(widget)
    else:
        widget = runtime.create()
    assert runtime.library.eager_calls == 1
    assert runtime.library.deferred_calls == 0
    widget.start_deferred_content()
    assert runtime.library.initialize_calls == 1


def test_deferred_resources_and_first_frame_are_separate(runtime):
    widget = runtime.create(defer_initialization=True)
    spy = QSignalSpy(widget.firstFramePresented)
    widget.prepare_surface()
    assert runtime.library.initialize_calls == 0
    assert runtime.library.deferred_calls == 1
    runtime.surface.frameSwapped.emit()
    widget._poll_view_state()
    assert spy.count() == 0
    widget.start_deferred_content()
    widget.start_deferred_content()
    assert runtime.library.initialize_calls == 1
    runtime.surface.frameSwapped.emit()
    assert spy.count() == 0  # New ABI reports actual map rendering separately.
    runtime.library.presented = True
    widget._poll_view_state()
    widget._poll_view_state()
    assert spy.count() == 1


def test_deferred_failure_propagates_without_reporting_first_frame(runtime):
    runtime.library.resources_succeed = False
    widget = runtime.create(defer_initialization=True)
    spy = QSignalSpy(widget.firstFramePresented)
    with pytest.raises(TileLoadingError, match="resources"):
        widget.start_deferred_content()
    widget._poll_view_state()
    assert spy.count() == 0
    runtime.library.resources_succeed = True
    widget.start_deferred_content()
    assert runtime.library.initialize_calls == 2


@pytest.mark.parametrize("window_surface", [False, True])
def test_old_abi_keeps_eager_creation_and_uses_real_event_target(runtime, monkeypatch, window_surface):
    for name in ("osmand_create_map_widget_deferred", "osmand_widget_initialize_resources", "osmand_widget_has_presented_frame", "osmand_widget_cleanup"):
        monkeypatch.setattr(runtime.library, name, None)
    target = runtime.surface
    if window_surface:
        class WindowSurface(QWindow):
            frameSwapped = Signal()

        target = WindowSurface()
        monkeypatch.setattr(runtime.library, "osmand_widget_get_event_target", lambda pointer: 2, raising=False)

        def wrap(pointer, cls):
            runtime.wrapped_types.append(cls)
            return target if int(pointer) == 2 else runtime.surface

        monkeypatch.setattr(native.shiboken6, "wrapInstance", wrap)
    widget = runtime.create(defer_initialization=True)
    spy = QSignalSpy(widget.firstFramePresented)
    assert runtime.library.eager_calls == 1
    assert runtime.wrapped_types == ([QWidget, QWindow] if window_surface else [QWidget])
    assert widget.event_target() is target
    assert QMetaObject.invokeMethod(target, "frameSwapped", Qt.ConnectionType.DirectConnection)
    assert spy.count() == 1
    widget.shutdown()
    QMetaObject.invokeMethod(target, "frameSwapped", Qt.ConnectionType.DirectConnection)
    assert spy.count() == 1
    if window_surface:
        target.destroy()


def test_deferred_abi_without_frame_query_waits_for_initialized_swap(runtime, monkeypatch):
    monkeypatch.setattr(runtime.library, "osmand_widget_has_presented_frame", None)
    widget = runtime.create(defer_initialization=True)
    spy = QSignalSpy(widget.firstFramePresented)
    runtime.surface.frameSwapped.emit()
    assert spy.count() == 0
    widget.start_deferred_content()
    runtime.surface.frameSwapped.emit()
    assert spy.count() == 1


def test_hide_show_reuses_surface_and_shutdown_is_terminal(runtime, qapp):
    widget = runtime.create(defer_initialization=True)
    widget.show()
    widget.start_deferred_content()
    widget.center_on(11.576124, 48.137154)
    widget.set_zoom(8.0)
    for _ in range(20):
        widget.hide()
        qapp.processEvents()
        assert not widget._state_timer.isActive()
        widget.show()
        qapp.processEvents()
        assert widget._state_timer.isActive()
        assert widget._native_widget is runtime.surface
        assert runtime.surface.updatesEnabled()
    assert runtime.library.deferred_calls == 1
    assert runtime.library.initialize_calls == 1
    assert runtime.library.cleanup_calls == 0
    assert widget.center_lonlat() == (11.576124, 48.137154)
    widget.shutdown()
    widget.shutdown()
    widget.hide()
    widget.show()
    widget.start_deferred_content()
    widget.center_on(0, 0)
    assert not widget._state_timer.isActive()
    assert runtime.library.center == (11.576124, 48.137154)
    assert runtime.library.cleanup_calls == 1


def test_shutdown_before_start_cancels_deferred_resources(runtime):
    widget = runtime.create(defer_initialization=True)
    widget.shutdown()
    widget.start_deferred_content()
    assert runtime.library.initialize_calls == 0
    assert runtime.library.cleanup_calls == 1
