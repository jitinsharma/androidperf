"""Bridges run_session's on_sample callback to an asyncio queue for WebSocket streaming."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from ..device import DeviceError, pick_device
from ..session import run_session


class RecordingSession:
    """Runs run_session in a background thread and exposes samples via an asyncio queue."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event = threading.Event()
        self.active = False
        self.run_dir: Path | None = None
        self.error: str | None = None

    def start(
        self,
        *,
        serial: str | None,
        package: str,
        interval: float,
        duration: float | None,
        output_dir: Path,
        launch: bool,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._loop = loop
        self._stop_event.clear()
        self.active = True
        self.error = None
        self.run_dir = None
        self._thread = threading.Thread(target=self._run, kwargs={
            "serial": serial,
            "package": package,
            "interval": interval,
            "duration": duration,
            "output_dir": output_dir,
            "launch": launch,
        }, daemon=True)
        self._thread.start()

    def _run(self, *, serial, package, interval, duration, output_dir, launch) -> None:
        try:
            device, info = pick_device(serial)
        except DeviceError as exc:
            self._push({"type": "error", "message": str(exc)})
            self.active = False
            return

        self._push({"type": "started", "device": info.label, "package": package})

        try:
            run_dir = run_session(
                device=device,
                device_info=info,
                package=package,
                interval=interval,
                duration=duration,
                output_dir=output_dir,
                launch=launch,
                on_sample=self._on_sample,
                stop_event=self._stop_event,
            )
            self.run_dir = run_dir
            self._push({"type": "done", "run_id": run_dir.name})
        except Exception as exc:
            self._push({"type": "error", "message": str(exc)})
        finally:
            self.active = False

    def _on_sample(self, sample: dict[str, Any]) -> None:
        self._push({"type": "sample", "data": sample})

    def _push(self, msg: dict[str, Any]) -> None:
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(
                self._queue.put(json.dumps(msg)), self._loop
            )

    async def next_message(self, timeout: float = 30.0) -> str | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        except TimeoutError:
            return None

    def stop(self) -> None:
        self._stop_event.set()
