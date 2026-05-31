"""Trigger am dumpheap on a running app and pull the hprof file.

Only works on debuggable builds (debug APKs, emulators, rooted devices).
Returns False gracefully for release apps — the caller should treat that
as a normal skip, not an error.
"""

from __future__ import annotations

import time
from pathlib import Path

from adbutils import AdbDevice


def capture(device: AdbDevice, package: str, out_path: Path, timeout: int = 30) -> bool:
    """Dump the heap of *package* to *out_path* on the local machine.

    Returns True on success, False if the dump is not permitted or fails.
    Never raises.
    """
    remote = f"/data/local/tmp/androidperf_{package}.hprof"
    try:
        result = device.shell(f"am dumpheap {package} {remote} 2>&1")
        lower = (result or "").lower()
        if any(kw in lower for kw in ("error", "not allowed", "failed", "permission denied")):
            return False

        # am dumpheap returns immediately; the VM writes the file asynchronously.
        # Poll until file size stabilises across two consecutive reads.
        deadline = time.monotonic() + timeout
        prev_size = -1
        while time.monotonic() < deadline:
            size_str = device.shell(f"stat -c %s {remote} 2>/dev/null").strip()
            try:
                size = int(size_str)
            except ValueError:
                size = 0
            if size > 0 and size == prev_size:
                break
            prev_size = size
            time.sleep(1.0)

        if prev_size <= 0:
            return False

        device.sync.pull(remote, str(out_path))
        return out_path.exists() and out_path.stat().st_size > 0

    except Exception:  # noqa: BLE001
        return False
    finally:
        try:
            device.shell(f"rm -f {remote}")
        except Exception:  # noqa: BLE001
            pass
