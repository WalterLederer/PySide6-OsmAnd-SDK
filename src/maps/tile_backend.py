"""Backend abstractions for helper-backed OsmAnd raster tiles."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, TypeAlias

from PySide6.QtCore import QProcess, QProcessEnvironment, QStandardPaths
from PySide6.QtGui import QImage

from maps.errors import TileAccessError, TileLoadingError
from maps.map_sources import MapBackendMetadata, MapSourceSpec, is_git_lfs_pointer

LOGGER = logging.getLogger(__name__)

if sys.platform == "win32":
    DEFAULT_QT_ROOT = Path(r"C:\Qt\6.10.1\mingw_64")
    DEFAULT_MINGW_ROOT = Path(r"C:\Qt\Tools\mingw1310_64")
else:
    DEFAULT_QT_ROOT = Path("/usr")
    DEFAULT_MINGW_ROOT = Path()
ENV_QT_ROOT = "IPHOTO_OSMAND_QT_ROOT"
ENV_MINGW_ROOT = "IPHOTO_OSMAND_MINGW_ROOT"
DEFAULT_HELPER_INIT_TIMEOUT_MS = 30000
DEFAULT_HELPER_RENDER_TIMEOUT_MS = 30000


class TileBackendUnavailableError(TileLoadingError):
    """Raised when a tile backend cannot currently serve requests."""


class TileRenderError(TileLoadingError):
    """Raised when a raster tile could not be rendered correctly."""


@dataclass(frozen=True)
class RasterTile:
    """Represent a rendered raster tile returned by the OBF backend."""

    image: QImage
    device_scale: float = 1.0


TilePayload: TypeAlias = RasterTile


class TileBackend(Protocol):
    """Protocol shared by raster tile backends."""

    metadata: MapBackendMetadata

    def probe(self) -> MapBackendMetadata:
        """Validate the backend and return its runtime metadata."""

    def load_tile(self, z: int, x: int, y: int) -> Optional[TilePayload]:
        """Return the requested tile payload or ``None`` when unavailable."""

    def clear_cache(self) -> None:
        """Drop any in-memory or on-disk tile caches maintained by the backend."""

    def shutdown(self) -> None:
        """Release long-lived resources such as helper processes."""

    def set_device_scale(self, scale: float) -> None:
        """Update the desired raster device scale for future requests."""


class OsmAndRasterBackend:
    """Load map tiles through an external OsmAnd-compatible helper."""

    CACHE_SCHEMA_VERSION = "2"
    DEFAULT_METADATA = MapBackendMetadata(
        min_zoom=2.0,
        max_zoom=19.0,
        provides_place_labels=True,
        tile_kind="raster",
        tile_scheme="xyz",
        fetch_max_zoom=19,
    )

    def __init__(self, source: MapSourceSpec) -> None:
        if source.kind != "osmand_obf":
            raise ValueError("OsmAndRasterBackend requires an osmand_obf source")
        self._source = source
        self.metadata = self.DEFAULT_METADATA
        self._device_scale = 1.0
        self._process: QProcess | None = None
        self._cache_root: Path | None = None
        self._next_request_id = 1

    def probe(self) -> MapBackendMetadata:
        self._validate_paths()
        self._ensure_process()
        return self.metadata

    def load_tile(self, z: int, x: int, y: int) -> Optional[RasterTile]:
        self._validate_paths()
        cache_path = self._cache_file_path(z, x, y)
        if cache_path.exists():
            return self._load_cached_tile(cache_path)

        for attempt in range(2):
            try:
                self._render_tile_to_cache(z, x, y, cache_path)
                break
            except TileBackendUnavailableError:
                self._remove_partial_cache_file(cache_path)
                if attempt >= 1:
                    raise
                LOGGER.warning(
                    "OsmAnd helper became unavailable while rendering %s/%s/%s; restarting and retrying once",
                    z,
                    x,
                    y,
                )
                self.shutdown()

        return self._load_cached_tile(cache_path)

    def clear_cache(self) -> None:
        return None

    def shutdown(self) -> None:
        if self._process is None:
            return

        try:
            self._communicate(self._process, {"command": "shutdown"}, timeout_ms=1000)
        except TileLoadingError:
            pass

        self._process.kill()
        self._process.waitForFinished(1000)
        self._process = None

    def set_device_scale(self, scale: float) -> None:
        self._device_scale = max(1.0, float(scale))

    def _ensure_process(self) -> QProcess:
        if self._process is not None and self._process.state() == QProcess.ProcessState.Running:
            return self._process

        command = self._source.helper_command
        if not command:
            raise TileBackendUnavailableError(
                "OsmAnd helper command not configured. Set IPHOTO_OSMAND_RENDER_HELPER.",
            )

        process = QProcess()
        process.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedErrorChannel)
        process.setProgram(command[0])
        process.setArguments(list(command[1:]))
        process.setProcessEnvironment(_helper_process_environment(Path(command[0])))
        process.start()
        if not process.waitForStarted(5000):
            stderr = bytes(process.readAllStandardError()).decode("utf8", errors="replace").strip()
            detail = f": {stderr}" if stderr else ""
            raise TileBackendUnavailableError(
                f"Unable to start OsmAnd helper '{command[0]}'{detail}",
            )

        response = self._communicate(
            process,
            {
                "command": "init",
                "obf_path": str(self._source.data_path),
                "resources_root": str(self._source.resources_root),
                "style_path": str(self._source.style_path),
                "night_mode": False,
            },
            timeout_ms=DEFAULT_HELPER_INIT_TIMEOUT_MS,
        )
        if response.get("status") != "ok":
            process.kill()
            process.waitForFinished(1000)
            message = str(response.get("message", "failed to initialise OsmAnd helper"))
            raise TileBackendUnavailableError(message)

        self.metadata = MapBackendMetadata(
            min_zoom=float(response.get("min_zoom", self.DEFAULT_METADATA.min_zoom)),
            max_zoom=float(response.get("max_zoom", self.DEFAULT_METADATA.max_zoom)),
            provides_place_labels=bool(
                response.get(
                    "provides_place_labels",
                    self.DEFAULT_METADATA.provides_place_labels,
                ),
            ),
            tile_kind="raster",
            tile_scheme="xyz",
            fetch_max_zoom=max(
                0,
                int(float(response.get("max_zoom", self.DEFAULT_METADATA.max_zoom))),
            ),
        )
        self._process = process
        return process

    def _communicate(
        self,
        process: QProcess,
        payload: dict[str, object],
        *,
        timeout_ms: int = 5000,
    ) -> dict[str, object]:
        message = json.dumps(payload, ensure_ascii=True) + "\n"
        process.write(message.encode("utf8"))
        if not process.waitForBytesWritten(timeout_ms):
            self._discard_process(process)
            raise TileBackendUnavailableError("Timed out while writing to the OsmAnd helper")

        if not process.canReadLine() and not process.waitForReadyRead(timeout_ms):
            self._discard_process(process)
            raise TileBackendUnavailableError("Timed out while waiting for the OsmAnd helper")

        while not process.canReadLine():
            if not process.waitForReadyRead(timeout_ms):
                self._discard_process(process)
                raise TileBackendUnavailableError("Timed out while reading from the OsmAnd helper")

        raw_line = bytes(process.readLine()).decode("utf8", errors="replace").strip()
        if not raw_line:
            self._discard_process(process)
            raise TileBackendUnavailableError("OsmAnd helper returned an empty response")

        try:
            response = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            self._discard_process(process)
            raise TileBackendUnavailableError(
                f"OsmAnd helper returned invalid JSON: {raw_line!r}",
            ) from exc

        if not isinstance(response, dict):
            self._discard_process(process)
            raise TileBackendUnavailableError("OsmAnd helper returned a non-object JSON response")

        return response

    def _read_exactly(self, process: QProcess, size: int, timeout_ms: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = bytes(process.read(size - len(data)))
            if chunk:
                data.extend(chunk)
                continue
            if process.state() != QProcess.ProcessState.Running:
                self._discard_process(process)
                raise TileBackendUnavailableError("OsmAnd helper exited during a binary response")
            if not process.waitForReadyRead(timeout_ms):
                self._discard_process(process)
                raise TileBackendUnavailableError("Timed out while reading binary data from the OsmAnd helper")
        return bytes(data)

    def _communicate_render(
        self, process: QProcess, payload: dict[str, object], *, request_id: int, timeout_ms: int
    ) -> QImage:
        message = json.dumps(payload, ensure_ascii=True) + "\\n"
        process.write(message.encode("utf8"))
        if not process.waitForBytesWritten(timeout_ms):
            self._discard_process(process)
            raise TileBackendUnavailableError("Timed out while writing a render request to the OsmAnd helper")
        header = self._read_exactly(process, 40, timeout_ms)
        try:
            magic, version, header_size, status, response_id, width, height, stride, pixel_format, payload_size = struct.unpack(
                "<4sHHIIIIIIQ", header
            )
        except struct.error as exc:
            self._discard_process(process)
            raise TileBackendUnavailableError("OsmAnd helper returned an invalid binary header") from exc
        if magic != b"OSMR" or version != 1 or header_size != 40:
            self._discard_process(process)
            raise TileBackendUnavailableError("OsmAnd helper returned an unsupported binary protocol header")
        if response_id != request_id:
            self._discard_process(process)
            raise TileBackendUnavailableError(
                f"OsmAnd helper response ID mismatch: expected {request_id}, received {response_id}"
            )
        if payload_size > 512 * 1024 * 1024:
            self._discard_process(process)
            raise TileBackendUnavailableError("OsmAnd helper response exceeds the 512 MiB safety limit")
        binary_payload = self._read_exactly(process, int(payload_size), timeout_ms)
        if status != 0:
            detail = binary_payload.decode("utf8", errors="replace").strip()
            raise TileRenderError(detail or f"OsmAnd render failed with status {status}")
        if pixel_format != 1 or width <= 0 or height <= 0 or stride < width * 4:
            self._discard_process(process)
            raise TileBackendUnavailableError("OsmAnd helper returned invalid RGBA8 dimensions or stride")
        if payload_size != stride * height:
            self._discard_process(process)
            raise TileBackendUnavailableError("OsmAnd helper returned an inconsistent RGBA8 payload length")
        image = QImage(
            bytearray(binary_payload), int(width), int(height), int(stride), QImage.Format.Format_RGBA8888
        ).copy()
        if image.isNull():
            raise TileRenderError("Unable to create a QImage from the helper's RGBA8 response")
        return image

    def _validate_paths(self) -> None:
        data_path = Path(self._source.data_path)
        if not data_path.exists():
            raise TileAccessError(f"OBF file '{data_path}' does not exist")
        if is_git_lfs_pointer(data_path):
            raise TileAccessError(
                f"OBF file '{data_path}' is a Git LFS pointer. Run 'git lfs pull' to download the real map data."
            )
        resources_root = Path(self._source.resources_root or "")
        if not resources_root.exists():
            raise TileAccessError(f"OsmAnd resources directory '{resources_root}' does not exist")
        style_path = Path(self._source.style_path or "")
        if not style_path.exists():
            raise TileAccessError(f"OsmAnd rendering style '{style_path}' does not exist")

    def _render_tile_to_cache(self, z: int, x: int, y: int, cache_path: Path) -> None:
        process = self._ensure_process()
        request_id = self._next_request_id
        self._next_request_id = (self._next_request_id + 1) & 0xFFFFFFFF
        if self._next_request_id == 0:
            self._next_request_id = 1
        request = {
            "command": "render",
            "request_id": request_id,
            "z": int(z),
            "x": int(x),
            "y": int(y),
            "device_scale": float(self._device_scale),
        }
        image = self._communicate_render(
            process, request, request_id=request_id, timeout_ms=DEFAULT_HELPER_RENDER_TIMEOUT_MS
        )
        if not image.save(str(cache_path), "PNG"):
            raise TileRenderError(f"Unable to save RGBA tile to cache file '{cache_path}'")

    def _discard_process(self, process: QProcess) -> None:
        if self._process is process:
            self._process = None
        if process.state() != QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(1000)

    def _remove_partial_cache_file(self, cache_path: Path) -> None:
        try:
            cache_path.unlink(missing_ok=True)
        except OSError:
            LOGGER.debug("Failed to remove partial cache file '%s'", cache_path, exc_info=True)

    def _cache_file_path(self, z: int, x: int, y: int) -> Path:
        cache_root = self._cache_directory()
        scale_tag = f"{self._device_scale:.2f}".replace(".", "_")
        cache_path = cache_root / scale_tag / str(z) / str(x)
        cache_path.mkdir(parents=True, exist_ok=True)
        return cache_path / f"{y}.png"

    def _cache_directory(self) -> Path:
        if self._cache_root is None:
            base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)
            if not base:
                base = str(Path(tempfile.gettempdir()) / "pyside6-osmand-core")
            fingerprint = hashlib.sha256(
                "|".join(
                    (
                        self.CACHE_SCHEMA_VERSION,
                        str(self._source.data_path),
                        str(self._source.style_path),
                        str(self._source.resources_root),
                        "|".join(self._source.helper_command or ()),
                    ),
                ).encode("utf8"),
            ).hexdigest()[:16]
            self._cache_root = Path(base) / "maps" / "obf" / fingerprint
        return self._cache_root

    def _load_cached_tile(self, path: Path) -> RasterTile:
        image = QImage(str(path))
        if image.isNull():
            raise TileRenderError(f"Unable to decode raster tile '{path}'")
        return RasterTile(image=image, device_scale=self._device_scale)


def _helper_process_environment(helper_executable: Path) -> QProcessEnvironment:
    """Return a process environment that can load the helper's native libraries."""

    env = QProcessEnvironment.systemEnvironment()
    runtime_paths = _helper_runtime_paths(helper_executable)

    if os.name == "nt":
        # On Windows, DLLs are resolved via PATH.
        path_entries = _existing_path_entries(env)
        prepended_entries = [str(path) for path in runtime_paths]

        merged_path: list[str] = []
        seen: set[str] = set()
        for entry in (*prepended_entries, *path_entries):
            normalized = entry.strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            merged_path.append(normalized)

        env.insert("PATH", os.pathsep.join(merged_path))
    else:
        # On Linux/macOS, shared libraries are resolved via LD_LIBRARY_PATH /
        # DYLD_LIBRARY_PATH.  Also prepend the helper's directory to PATH so
        # the helper executable itself is found.
        path_entries = _existing_path_entries(env)
        lib_dirs = [str(p) for p in runtime_paths if p.is_dir()]
        merged_path = [str(p) for p in runtime_paths] + list(path_entries)
        env.insert("PATH", os.pathsep.join(merged_path))

        ld_key = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
        existing_ld = env.value(ld_key, "")
        existing_dirs = [d for d in existing_ld.split(os.pathsep) if d]
        merged_ld = lib_dirs + existing_dirs
        env.insert(ld_key, os.pathsep.join(merged_ld))

    return env


def _helper_runtime_paths(helper_executable: Path) -> tuple[Path, ...]:
    """Return directories that let the native helper resolve Qt and runtime libraries."""

    candidates: list[Path] = [helper_executable.resolve().parent]

    for root in (
        os.environ.get(ENV_QT_ROOT),
        os.environ.get("QT_ROOT"),
        os.environ.get("QTDIR"),
        str(DEFAULT_QT_ROOT),
    ):
        candidates.extend(_runtime_bin_candidates(root))

    if os.name == "nt":
        for root in (
            os.environ.get(ENV_MINGW_ROOT),
            os.environ.get("MINGW_ROOT"),
            str(DEFAULT_MINGW_ROOT),
        ):
            candidates.extend(_runtime_bin_candidates(root))

    # On Linux, also add PySide6's lib directory for shared library resolution
    if sys.platform == "linux":
        try:
            import PySide6
            pyside_lib = Path(PySide6.__file__).resolve().parent / "Qt" / "lib"
            if pyside_lib.is_dir():
                candidates.append(pyside_lib)
            pyside_parent = Path(PySide6.__file__).resolve().parent
            candidates.append(pyside_parent)
        except ImportError:
            pass

    existing: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        existing.append(resolved)

    return tuple(existing)


def _runtime_bin_candidates(root: str | None) -> tuple[Path, ...]:
    """Expand a Qt/MinGW root into candidate runtime directories."""

    if not root:
        return ()

    path = Path(root)
    if path.name.lower() == "bin":
        return (path,)
    return (path / "bin", path)


def _existing_path_entries(env: QProcessEnvironment) -> tuple[str, ...]:
    """Return the current process PATH split into individual entries."""

    path_value = env.value("PATH", "")
    if not path_value:
        return ()
    return tuple(entry for entry in path_value.split(os.pathsep) if entry)


__all__ = [
    "OsmAndRasterBackend",
    "RasterTile",
    "TileBackend",
    "TileBackendUnavailableError",
    "TilePayload",
    "TileRenderError",
]
