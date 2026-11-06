"""Background tile loading and caching infrastructure."""

from __future__ import annotations

import heapq
import itertools
import logging
import time
from collections import OrderedDict
from typing import Callable, Iterable

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from maps.errors import TileLoadingError
from maps.map_sources import MapBackendMetadata
from maps.tile_backend import TileBackend, TilePayload

LOGGER = logging.getLogger(__name__)

TileKey = tuple[int, int, int]
TilePriority = tuple[float, ...]


class _TileLoaderWorker(QObject):
    """Own one backend and its helper process in a dedicated QThread."""

    process_requested = Signal(object)
    shutdown_requested = Signal()
    clear_requested = Signal()
    device_scale_requested = Signal(float)
    tile_ready = Signal(int, object, int, object)
    tile_error = Signal(int, object, int, str)

    def __init__(self, worker_id: int, tile_backend: TileBackend) -> None:
        super().__init__()
        self._worker_id = worker_id
        self._backend = tile_backend
        self.process_requested.connect(self.process_tile)
        self.shutdown_requested.connect(self.shutdown)
        self.clear_requested.connect(self.clear_backend_cache)
        self.device_scale_requested.connect(self.set_backend_device_scale)

    def clear_backend_cache(self) -> None:
        """Clear backend-owned caches in this worker's thread."""

        self._backend.clear_cache()

    def set_backend_device_scale(self, scale: float) -> None:
        """Update backend rendering scale in this worker's thread."""

        self._backend.set_device_scale(scale)

    def process_tile(self, job: object) -> None:
        """Load one tile; this method runs only in the worker thread."""

        tile_key, generation = job
        z, x, y = tile_key
        try:
            tile = self._backend.load_tile(z, x, y)
        except TileLoadingError as exc:
            self.tile_error.emit(self._worker_id, tile_key, generation, str(exc))
        except Exception as exc:  # Keep one unexpected backend failure from killing the pool.
            LOGGER.exception("Unexpected error in tile worker %s", self._worker_id)
            self.tile_error.emit(
                self._worker_id,
                tile_key,
                generation,
                f"{type(exc).__name__}: {exc}",
            )
        else:
            if tile is None:
                self.tile_error.emit(self._worker_id, tile_key, generation, "")
            else:
                self.tile_ready.emit(self._worker_id, tile_key, generation, tile)

    def shutdown(self) -> None:
        """Release this worker's helper process in its owning thread."""

        try:
            self._backend.shutdown()
        finally:
            QThread.currentThread().quit()


class TileManager(QObject):
    """Manage tile caching and a priority-driven pool of raster workers.

    Each worker owns a separate backend instance and therefore a separate
    persistent helper process. Requests are dispatched to idle workers in
    priority order. Queued requests can be reprioritized as the viewport moves.
    """

    tile_loaded = Signal(tuple)
    tile_missing = Signal(tuple)
    tile_removed = Signal(tuple)
    tiles_changed = Signal()

    _MISSING_RETRY_COOLDOWN_S = 1.5
    DEFAULT_WORKER_COUNT = 4

    def __init__(
        self,
        tile_backend: TileBackend,
        *,
        cache_limit: int = 256,
        worker_count: int = 1,
        backend_factory: Callable[[], TileBackend] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache_limit = cache_limit
        self._tile_cache: OrderedDict[TileKey, TilePayload] = OrderedDict()
        self._pending_generations: dict[TileKey, int] = {}
        self._missing_tiles: dict[TileKey, float] = {}

        self._metadata = tile_backend.probe()
        # probe() may have created a QProcess in the GUI thread. Close it
        # there; the same backend object is then first used in worker 0.
        tile_backend.shutdown()

        worker_count = max(1, int(worker_count))
        if worker_count > 1 and backend_factory is None:
            raise ValueError("backend_factory is required when worker_count > 1")

        backends = [tile_backend]
        if backend_factory is not None:
            backends.extend(backend_factory() for _ in range(worker_count - 1))

        self._workers: list[_TileLoaderWorker] = []
        self._worker_threads: list[QThread] = []
        self._worker_busy = [False] * worker_count

        for worker_id, backend in enumerate(backends):
            thread = QThread(self)
            thread.setObjectName(f"TileLoaderThread-{worker_id}")
            worker = _TileLoaderWorker(worker_id, backend)
            worker.moveToThread(thread)
            worker.tile_ready.connect(self._on_worker_tile_ready)
            worker.tile_error.connect(self._on_worker_tile_error)
            thread.finished.connect(worker.deleteLater)
            self._workers.append(worker)
            self._worker_threads.append(thread)
            thread.start()

        self._request_queue: list[
            tuple[TilePriority, int, TileKey, int, int]
        ] = []
        self._queue_tokens: dict[TileKey, int] = {}
        self._sequence = itertools.count()
        self._next_queue_token = itertools.count(1)
        self._generation = 0
        self._processing_queue = False
        self._is_shutdown = False

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        """Stop workers and release each helper in its owning thread."""

        if self._is_shutdown:
            return
        self._is_shutdown = True
        self._request_queue.clear()
        self._queue_tokens.clear()
        self._pending_generations.clear()

        # The queued shutdown slot runs after any current blocking tile call.
        for worker in self._workers:
            worker.shutdown_requested.emit()

        for thread in self._worker_threads:
            if not thread.wait(35000):
                LOGGER.error("Tile worker %s did not stop cleanly", thread.objectName())
                thread.terminate()
                thread.wait(1000)

    # ------------------------------------------------------------------
    def get_tile(self, tile_key: TileKey) -> TilePayload | None:
        """Return a cached tile, updating the LRU ordering when found."""

        tile = self._tile_cache.get(tile_key)
        if tile is not None:
            self._tile_cache.move_to_end(tile_key)
        return tile

    # ------------------------------------------------------------------
    def ensure_tile(
        self,
        tile_key: TileKey,
        *,
        priority: TilePriority | None = None,
    ) -> None:
        """Schedule a tile, or improve its priority if it is still queued."""

        if self._is_shutdown:
            return

        if self._pending_generations.get(tile_key) == self._generation:
            if tile_key in self._queue_tokens and priority is not None:
                self._enqueue(tile_key, self._generation, priority)
            return

        failed_at = self._missing_tiles.get(tile_key)
        if failed_at is not None:
            if (time.monotonic() - failed_at) < self._MISSING_RETRY_COOLDOWN_S:
                return
            self._missing_tiles.pop(tile_key, None)

        self._pending_generations[tile_key] = self._generation
        self._enqueue(tile_key, self._generation, priority)
        self._schedule_queue_processing()

    # ------------------------------------------------------------------
    def reprioritize(self, priorities: dict[TileKey, TilePriority]) -> None:
        """Keep queued work aligned with the latest viewport.

        In-flight jobs are allowed to finish, but queued tiles that have left
        the requested viewport are dropped and current queued tiles receive
        their latest centre-first priority.
        """

        if self._is_shutdown:
            return

        for tile_key in tuple(self._queue_tokens):
            if tile_key not in priorities:
                self._queue_tokens.pop(tile_key, None)
                if self._pending_generations.get(tile_key) == self._generation:
                    self._pending_generations.pop(tile_key, None)

        for tile_key, priority in priorities.items():
            if (
                self._pending_generations.get(tile_key) == self._generation
                and tile_key in self._queue_tokens
            ):
                self._enqueue(tile_key, self._generation, priority)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def _enqueue(
        self,
        tile_key: TileKey,
        generation: int,
        priority: TilePriority | None,
    ) -> None:
        sequence = next(self._sequence)
        normalized_priority = (
            tuple(float(value) for value in priority)
            if priority is not None
            else (float("inf"),)
        )
        token = next(self._next_queue_token)
        self._queue_tokens[tile_key] = token
        heapq.heappush(
            self._request_queue,
            (normalized_priority, sequence, tile_key, generation, token),
        )

    # ------------------------------------------------------------------
    def _schedule_queue_processing(self) -> None:
        if self._processing_queue or self._is_shutdown:
            return
        self._processing_queue = True
        QTimer.singleShot(0, self._process_queue)

    # ------------------------------------------------------------------
    def _process_queue(self) -> None:
        """Fill all idle workers from the current priority queue."""

        self._processing_queue = False
        if self._is_shutdown:
            return

        while self._request_queue:
            try:
                worker_id = self._worker_busy.index(False)
            except ValueError:
                return

            job = self._take_next_request()
            if job is None:
                break

            tile_key, generation = job
            self._worker_busy[worker_id] = True
            self._workers[worker_id].process_requested.emit((tile_key, generation))

        # Stale heap entries may have been discarded while looking for work.
        if self._request_queue and any(not busy for busy in self._worker_busy):
            self._schedule_queue_processing()

    # ------------------------------------------------------------------
    def _take_next_request(self) -> tuple[TileKey, int] | None:
        while self._request_queue:
            _, _, tile_key, generation, token = heapq.heappop(self._request_queue)
            if self._queue_tokens.get(tile_key) != token:
                continue
            if self._pending_generations.get(tile_key) != generation:
                self._queue_tokens.pop(tile_key, None)
                continue
            self._queue_tokens.pop(tile_key, None)
            return tile_key, generation
        return None

    # ------------------------------------------------------------------
    def _on_worker_tile_ready(
        self,
        worker_id: int,
        tile_key: TileKey,
        generation: int,
        tile: TilePayload,
    ) -> None:
        self._worker_finished(worker_id)
        if self._pending_generations.get(tile_key) == generation:
            self._handle_tile_loaded(tile_key, tile)
        self._schedule_queue_processing()

    # ------------------------------------------------------------------
    def _on_worker_tile_error(
        self,
        worker_id: int,
        tile_key: TileKey,
        generation: int,
        error_msg: str,
    ) -> None:
        self._worker_finished(worker_id)
        if self._pending_generations.get(tile_key) == generation:
            z, x, y = tile_key
            if error_msg:
                LOGGER.warning(
                    "Tile %s/%s/%s could not be loaded: %s", z, x, y, error_msg
                )
            self._handle_tile_missing(tile_key)
        self._schedule_queue_processing()

    # ------------------------------------------------------------------
    def _worker_finished(self, worker_id: int) -> None:
        if 0 <= worker_id < len(self._worker_busy):
            self._worker_busy[worker_id] = False

    # ------------------------------------------------------------------
    def is_tile_missing(self, tile_key: TileKey) -> bool:
        """Return True when a tile previously failed within the retry window."""

        failed_at = self._missing_tiles.get(tile_key)
        if failed_at is None:
            return False
        if (time.monotonic() - failed_at) < self._MISSING_RETRY_COOLDOWN_S:
            return True
        self._missing_tiles.pop(tile_key, None)
        return False

    # ------------------------------------------------------------------
    def pending_tiles(self) -> Iterable[TileKey]:
        """Expose queued and in-flight requests for diagnostics/testing."""

        return set(self._pending_generations)

    # ------------------------------------------------------------------
    @property
    def metadata(self) -> MapBackendMetadata:
        return self._metadata

    # ------------------------------------------------------------------
    def set_device_scale(self, scale: float) -> None:
        """Update all backends and invalidate tiles rendered at the old scale."""

        for worker in self._workers:
            worker.device_scale_requested.emit(float(scale))
        self.clear()

    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Invalidate cached and queued work without accepting stale results."""

        self._generation += 1
        self._tile_cache.clear()
        self._pending_generations.clear()
        self._missing_tiles.clear()
        self._queue_tokens.clear()
        self._request_queue.clear()
        for worker in self._workers:
            worker.clear_requested.emit()
        self.tiles_changed.emit()

    # ------------------------------------------------------------------
    def _handle_tile_loaded(self, key: TileKey, tile: TilePayload) -> None:
        self._pending_generations.pop(key, None)
        self._missing_tiles.pop(key, None)
        self._tile_cache[key] = tile
        self._tile_cache.move_to_end(key)
        self.tile_loaded.emit(key)

        while len(self._tile_cache) > self._cache_limit:
            evicted_key, _ = self._tile_cache.popitem(last=False)
            self.tile_removed.emit(evicted_key)

        self.tiles_changed.emit()

    # ------------------------------------------------------------------
    def _handle_tile_missing(self, key: TileKey) -> None:
        self._pending_generations.pop(key, None)
        self._missing_tiles[key] = time.monotonic()
        if key in self._tile_cache:
            del self._tile_cache[key]
            self.tile_removed.emit(key)
        self.tile_missing.emit(key)
        self.tiles_changed.emit()


__all__ = ["TileManager"]
