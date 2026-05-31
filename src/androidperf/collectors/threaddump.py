"""Periodic ART thread dumps via SIGQUIT (kill -3).

Sending SIGQUIT to the app PID causes ART to write all thread stack traces
to logcat under the `art` tag. We read them back with `logcat -d -s art`
and parse thread name, state, and per-thread stack frames.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from typing import Any

from adbutils import AdbDevice

# "thread-name" prio=N tid=N State
_THREAD_HDR = re.compile(
    r'^"([^"]+)"\s+prio=\d+\s+tid=(\d+)\s+([\w][\w ]*\w|[\w]+)\s*$'
)
# Linux TID in the metadata block
_SYTID_RE = re.compile(r'sysTid=(\d+)')
# Stack frame: "  at fully.Qualified.method(File.java:N)"
_FRAME_RE = re.compile(r'^\s+at\s+([\w.$]+)\.([\w$<>]+)\(([^)]*)\)')

# Framework prefixes — suppress from "interesting" frame list
_SKIP_PREFIXES = (
    "java.", "javax.", "sun.", "com.sun.",
    "dalvik.", "libcore.", "org.apache.harmony.",
    "android.os.", "android.app.", "android.view.",
    "android.content.", "android.graphics.",
    "com.android.internal.",
    "kotlin.jvm.", "kotlin.coroutines.jvm.",
)


def _is_interesting(class_name: str) -> bool:
    return not any(class_name.startswith(p) for p in _SKIP_PREFIXES)


def _parse_thread_dump(raw: str) -> list[dict[str, Any]]:
    """Parse ART thread dump lines into a list of thread dicts."""
    threads: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in raw.splitlines():
        m = _THREAD_HDR.match(line)
        if m:
            if current is not None:
                threads.append(current)
            current = {
                "name": m.group(1),
                "tid": int(m.group(2)),
                "state": m.group(3).strip(),
                "linux_tid": None,
                "frames": [],
                "interesting_frames": [],
            }
            continue

        if current is None:
            continue

        # Linux TID from metadata line
        if current["linux_tid"] is None:
            sm = _SYTID_RE.search(line)
            if sm:
                current["linux_tid"] = int(sm.group(1))

        fm = _FRAME_RE.match(line)
        if fm:
            class_name = fm.group(1)
            method = fm.group(2)
            location = fm.group(3)
            frame = f"{class_name}.{method}({location})"
            if len(current["frames"]) < 8:
                current["frames"].append(frame)
            if _is_interesting(class_name) and len(current["interesting_frames"]) < 4:
                current["interesting_frames"].append(frame)

    if current is not None:
        threads.append(current)

    return threads


def capture(device: AdbDevice, pid: int, package: str) -> list[dict[str, Any]]:
    """Send SIGQUIT and return parsed thread list. Takes ~2s due to wait."""
    device.shell(f"kill -3 {pid}")
    time.sleep(1.5)
    raw = device.shell("logcat -d -s art -t 1000 -v raw")
    return _parse_thread_dump(raw)


class ThreadDumpCoordinator:
    """Accepts dump requests, executes them serially in a background thread."""

    def __init__(self, device: AdbDevice, pid: int, package: str) -> None:
        self._device = device
        self._pid = pid
        self._package = package
        self._dumps: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=6)
        self._thread = threading.Thread(target=self._worker, daemon=True,
                                        name="threaddump-coordinator")
        self._thread.start()

    def request(
        self,
        trigger: str,
        activity: str | None,
        fragment: str | None,
        t: float,
    ) -> None:
        try:
            self._queue.put_nowait({
                "trigger": trigger,
                "activity": activity,
                "fragment": fragment,
                "t": t,
            })
        except queue.Full:
            pass  # Drop if backlog is full — always safe to miss a dump

    def finish(self, timeout: float = 10.0) -> None:
        """Drain queue and stop. Blocks up to `timeout` seconds."""
        self._queue.put(None)  # Sentinel — worker exits after this
        self._thread.join(timeout=timeout)

    def get_dumps(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._dumps)

    def _worker(self) -> None:
        while True:
            ctx = self._queue.get()
            if ctx is None:
                break
            try:
                threads = capture(self._device, self._pid, self._package)
            except Exception:
                threads = []
            with self._lock:
                self._dumps.append({
                    "t": ctx["t"],
                    "trigger": ctx["trigger"],
                    "activity": ctx["activity"],
                    "fragment": ctx["fragment"],
                    "threads": threads,
                })
