"""
Qt + asyncio integration. We run an asyncio loop on a worker thread and
marshal results back to the GUI thread via Qt signals. PySide6 has
QtAsyncio in newer versions, but for portability we use a small custom
bridge that works on PySide6 6.5+.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Coroutine

from PySide6.QtCore import QObject, Signal


class AsyncRunner(QObject):
    """Owns a background asyncio event loop; `run()` schedules a coroutine
    on it and emits `finished`/`failed` back on the Qt thread that owns this
    object (Signal emission across threads is queued by Qt automatically)."""

    finished = Signal(object, object)  # (token, result)
    failed = Signal(object, Exception)  # (token, exception)
    progress = Signal(object, str, str, object)  # (token, stage, detail, progress_pct)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        ready = threading.Event()

        def _run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            ready.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run_loop, name="AsyncRunner", daemon=True)
        self._thread.start()
        ready.wait()

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._loop = None

    def status_callback(self, token: Any) -> Callable[[str, str, "int | None"], None]:
        """Returns a plain-function status_fn (matching SessionEngine's
        StatusFn signature) that marshals onto this runner's owning thread
        via the `progress` signal."""
        def _fn(stage: str, detail: str, pct: int | None = None) -> None:
            self.progress.emit(token, stage, detail, pct)
        return _fn

    def run(self, token: Any, coro: Coroutine) -> None:
        if self._loop is None:
            self.start()

        async def _wrapped():
            try:
                result = await coro
            except Exception as e:  # noqa: BLE001 — surfaced to the GUI, not swallowed
                self.failed.emit(token, e)
            else:
                self.finished.emit(token, result)

        asyncio.run_coroutine_threadsafe(_wrapped(), self._loop)
