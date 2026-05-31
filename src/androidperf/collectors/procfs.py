"""Thread count, context switches, disk I/O, and thread names from /proc/<pid>."""

from __future__ import annotations

import re

from adbutils import AdbDevice

_STATUS_RE = re.compile(r"^(\w+):\s+(.+)$", re.MULTILINE)
_THREADS_RE = re.compile(r"^Threads:\s+(\d+)", re.MULTILINE)
_CSW_VOL_RE = re.compile(r"^voluntary_ctxt_switches:\s+(\d+)", re.MULTILINE)
_CSW_NONVOL_RE = re.compile(r"^nonvoluntary_ctxt_switches:\s+(\d+)", re.MULTILINE)
_IO_RE = re.compile(r"^(\w+):\s+(\d+)", re.MULTILINE)

# Thread name lines from /proc/<pid>/task/*/status look like:
# Name:	OkHttp Dispatch
_TASK_NAME_RE = re.compile(r"^Name:\s+(.+)$", re.MULTILINE)


def sample(device: AdbDevice, *, pid: int, **_: object) -> dict[str, object]:
    """Return thread count, cumulative context switches and disk I/O bytes."""
    out = device.shell(f"cat /proc/{pid}/status 2>/dev/null; echo ---; cat /proc/{pid}/io 2>/dev/null")
    parts = out.split("---", 1)
    status_text = parts[0]
    io_text = parts[1] if len(parts) > 1 else ""

    result: dict[str, object] = {}

    m = _THREADS_RE.search(status_text)
    if m:
        result["threads"] = int(m.group(1))

    m = _CSW_VOL_RE.search(status_text)
    if m:
        result["csw_vol_total"] = int(m.group(1))

    m = _CSW_NONVOL_RE.search(status_text)
    if m:
        result["csw_nonvol_total"] = int(m.group(1))

    for match in _IO_RE.finditer(io_text):
        key, val = match.group(1), match.group(2)
        if key == "read_bytes":
            result["disk_read_total_b"] = int(val)
        elif key == "write_bytes":
            result["disk_write_total_b"] = int(val)

    return result


def sample_thread_names(device: AdbDevice, *, pid: int, **_: object) -> list[str]:
    """Return a deduplicated list of thread names for the process."""
    out = device.shell(f"cat /proc/{pid}/task/*/status 2>/dev/null | grep '^Name:'")
    names: list[str] = []
    seen: set[str] = set()
    for match in _TASK_NAME_RE.finditer(out):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names
