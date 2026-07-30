"""
Qt + asyncio integration. We run an asyncio loop on a worker thread and
marshal results back to the GUI thread via Qt signals. PySide6 has
QtAsyncio in newer versions, but for portability we use a small custom
bridge that works on PySide6 6.5+.

Restored to match the shipped app almost exactly (this class's bytecode
decompiled cleanly enough to read the real design directly) — the version
previously here was a from-scratch guess (token-keyed signals, a separate
`progress` signal owned by AsyncRunner) written when this repo had no
decompiled source to check against. The real design is simpler: there is
only ever one recording at a time, so no token is needed, and per-stage
status doesn't go through AsyncRunner at all — MainWindow owns its own
`status_signal` and passes a closure over it directly as SessionEngine's
`status_fn` (see main_window.py).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Coroutine

from PySide6.QtCore import QObject, Signal

log = logging.getLogger("app.async_runner")


class AsyncRunner(QObject):
    """Runs coroutines on a background thread with its own asyncio loop.
    Emits `finished(result)` or `failed(exception_str)` on the GUI thread
    when the coroutine completes."""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def stop(self) -> None:
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3)

    def submit(self, coro: Coroutine) -> None:
        if self._loop is None:
            raise RuntimeError("AsyncRunner not started")

        def _on_done(f: "asyncio.Future") -> None:
            try:
                result = f.result()
            except Exception as e:  # noqa: BLE001 — surfaced to the GUI, not swallowed
                log.exception("Async task failed: %s", e)
                self.failed.emit(f"{type(e).__name__}: {e}")
            else:
                self.finished.emit(result)

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        future.add_done_callback(_on_done)
