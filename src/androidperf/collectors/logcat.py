"""Background logcat tail for ART GC events."""

from __future__ import annotations

import re
import subprocess
import threading
import time
from typing import Any

# ART GC log pattern examples:
# I art     : Background sticky concurrent mark sweep GC freed 54321(5 MB) AllocSpace objects, ...
# I art     : Explicit concurrent mark sweep GC freed 12345(2 MB) AllocSpace objects, ...
# The reason is the first capitalised word(s) after the GC type description.
_GC_RE = re.compile(
    r"art\s*:\s+"
    r"(?P<type>\S.*?)\s+GC\s+freed\s+"
    r"(?P<count>\d+)\((?P<freed>[^\)]+)\)",
    re.IGNORECASE,
)

# Freed size: "5 MB", "512 KB", "1234 B"
_SIZE_RE = re.compile(r"([\d.]+)\s*(MB|KB|B)", re.IGNORECASE)

_REASONS = {"Explicit", "Alloc", "Background", "NativeAlloc", "CollectorTransition",
            "HomogeneousSpaceCompact", "DisableMovingGc", "HeapTrim"}


def _parse_freed_kb(freed_str: str) -> float:
    m = _SIZE_RE.search(freed_str)
    if not m:
        return 0.0
    val = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "MB":
        return val * 1024
    if unit == "KB":
        return val
    return val / 1024


def _extract_reason(gc_type: str) -> str:
    """Pull the GC reason from the type string (first token that matches known reasons)."""
    for token in gc_type.split():
        if token in _REASONS:
            return token
    # Fallback: first capitalised token
    for token in gc_type.split():
        if token[0].isupper():
            return token
    return "Unknown"


class LogcatCollector:
    """Tails logcat ART tag in a background thread for the duration of a session."""

    def __init__(self, serial: str | None, started_mono: float) -> None:
        self._serial = serial
        self._started_mono = started_mono
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="logcat-gc")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc:
            try:
                self._proc.kill()
            except Exception:
                pass

    def gc_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events)

    def _run(self) -> None:
        cmd = ["adb"]
        if self._serial:
            cmd += ["-s", self._serial]
        cmd += ["logcat", "-s", "art", "-v", "raw"]

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            return

        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            if self._stop.is_set():
                break
            m = _GC_RE.search(line)
            if not m:
                continue
            elapsed = time.monotonic() - self._started_mono
            reason = _extract_reason(m.group("type"))
            freed_kb = _parse_freed_kb(m.group("freed"))
            with self._lock:
                self._events.append({
                    "t": round(elapsed, 3),
                    "type": "gc",
                    "reason": reason,
                    "freed_kb": freed_kb,
                    "count": int(m.group("count")),
                })
