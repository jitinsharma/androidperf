"""Memory usage via `dumpsys meminfo <package>`.

We read the `App Summary` block which provides a stable, documented set of
buckets (Java Heap, Native Heap, Graphics, TOTAL PSS). Older Android versions
may not include every row; unknown rows are simply absent from the result.
"""

from __future__ import annotations

import re

from adbutils import AdbDevice

# Match "<label>:   <number>" at the start of a line. The App Summary block
# sometimes has trailing RSS columns on the same row, so we don't anchor to
# end-of-line.
_ROW_RE = re.compile(r"^\s*([A-Za-z][\w /\.]+?):\s+([\d,]+)")

_FIELDS = {
    "Java Heap": "java_kb",
    "Native Heap": "native_kb",
    "Code": "code_kb",
    "Stack": "stack_kb",
    "Graphics": "gfx_kb",
    "Private Other": "private_other_kb",
    "System": "system_kb",
    "TOTAL PSS": "pss_kb",
    "TOTAL": "pss_kb",  # very old Android shortcut
}


def parse_meminfo(output: str) -> dict[str, float]:
    """Pull numeric rows out of the `App Summary` section."""
    lines = output.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if "App Summary" in ln)
    except StopIteration:
        return {}

    result: dict[str, float] = {}
    for line in lines[start:]:
        match = _ROW_RE.match(line)
        if not match:
            continue
        label, num = match.group(1).strip(), match.group(2)
        key = _FIELDS.get(label)
        if not key:
            continue
        try:
            result[key] = float(num.replace(",", ""))
        except ValueError:
            continue
        if label.startswith("TOTAL"):
            break
    return result


def sample(device: AdbDevice, *, package: str, **_: object) -> dict[str, float]:
    # `-s` (short) skips the per-allocation breakdown that makes the default
    # dump take 3-6 s on apps with large heaps; the `App Summary` block we
    # parse below is still present.
    out = device.shell(f"dumpsys meminfo -s {package}")
    return parse_meminfo(out)


# Objects block has labels both at line-start and mid-line, e.g.:
#   AppContexts:   4           Activities:   3
_OBJ_RE = re.compile(r"\b(Activities|Views|AppContexts):\s+(\d+)")


def parse_meminfo_objects(output: str) -> dict[str, float]:
    """Extract object counts from the Objects section of dumpsys meminfo."""
    result: dict[str, float] = {}
    for m in _OBJ_RE.finditer(output):
        label, val = m.group(1), m.group(2)
        if label == "Activities":
            result["obj_activities"] = float(val)
        elif label == "Views":
            result["obj_views"] = float(val)
        elif label == "AppContexts":
            result["obj_app_contexts"] = float(val)
    return result


def sample_objects(device: AdbDevice, *, package: str, **_: object) -> dict[str, float]:
    # No -s flag so the Objects block is present; grep keeps it fast.
    out = device.shell(f"dumpsys meminfo {package} | grep -A10 '^ Objects'")
    return parse_meminfo_objects(out)
