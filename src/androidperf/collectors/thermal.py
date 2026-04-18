"""Thermal state via `dumpsys thermalservice`.

Captures:
- overall thermal status (0..6, maps to android.os.PowerManager thermal levels)
- representative temperatures: skin, cpu, battery when present

Format varies across Android versions. We handle both the "Current temperatures"
line-style and the older "Cached temperatures: Temperature{mValue=..., mName=...}"
style.
"""

from __future__ import annotations

import re

from adbutils import AdbDevice

_STATUS_RE = re.compile(r"^(?:Current )?[Tt]hermal [Ss]tatus:\s*(\d+)", re.MULTILINE)
_TEMP_ENTRY_RE = re.compile(
    r"Temperature\{[^}]*mValue=([-\d.]+)[^}]*mName=([A-Za-z0-9_\-]+)[^}]*\}"
)

# PowerManager.THERMAL_STATUS_* names, useful for display.
_STATUS_NAMES = {
    0: "none",
    1: "light",
    2: "moderate",
    3: "severe",
    4: "critical",
    5: "emergency",
    6: "shutdown",
}

# Which well-known names we persist as columns. `thermal_*` prefix avoids
# colliding with the battery collector's own `battery_temp_c`.
_NAME_TO_FIELD = {
    "skin": "thermal_skin_c",
    "cpu": "thermal_cpu_c",
    "battery": "thermal_battery_c",
    "gpu": "thermal_gpu_c",
    "usb_port": "thermal_usb_c",
}


def parse_thermal(output: str) -> dict[str, float]:
    result: dict[str, float] = {}
    if m := _STATUS_RE.search(output):
        result["thermal_status"] = float(m.group(1))

    # Collect the first reading per name — older devices list multiple entries.
    seen: set[str] = set()
    for match in _TEMP_ENTRY_RE.finditer(output):
        value_str, name = match.group(1), match.group(2).lower()
        if name in seen:
            continue
        field = _NAME_TO_FIELD.get(name)
        if not field:
            continue
        try:
            result[field] = float(value_str)
            seen.add(name)
        except ValueError:
            continue
    return result


def thermal_status_name(status_code: float | None) -> str:
    if status_code is None:
        return "—"
    return _STATUS_NAMES.get(int(status_code), f"code_{int(status_code)}")


def sample(device: AdbDevice, **_: object) -> dict[str, float]:
    out = device.shell("dumpsys thermalservice")
    return parse_thermal(out)
