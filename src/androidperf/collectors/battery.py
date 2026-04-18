"""Battery state via `dumpsys battery`.

Captures level, temperature (tenths of degC in the raw output, converted to
float degC), voltage (mV → V), and a coarse charging state. Works on any
Android version since `dumpsys battery` output is stable.
"""

from __future__ import annotations

import re

from adbutils import AdbDevice

_LEVEL_RE = re.compile(r"^\s*level:\s*(\d+)", re.MULTILINE)
_TEMP_RE = re.compile(r"^\s*temperature:\s*(-?\d+)", re.MULTILINE)
_VOLT_RE = re.compile(r"^\s*voltage:\s*(\d+)", re.MULTILINE)
_STATUS_RE = re.compile(r"^\s*status:\s*(\d+)", re.MULTILINE)

# From android.os.BatteryManager constants
_STATUS_NAMES = {1: "unknown", 2: "charging", 3: "discharging", 4: "not_charging", 5: "full"}


def parse_battery(output: str) -> dict[str, float]:
    result: dict[str, float] = {}
    if m := _LEVEL_RE.search(output):
        result["battery_level_pct"] = float(m.group(1))
    if m := _TEMP_RE.search(output):
        # dumpsys reports deci-degrees; convert to degC.
        result["battery_temp_c"] = float(m.group(1)) / 10.0
    if m := _VOLT_RE.search(output):
        # dumpsys reports mV; convert to V.
        result["battery_voltage_v"] = float(m.group(1)) / 1000.0
    if m := _STATUS_RE.search(output):
        status_code = int(m.group(1))
        # Stored as numeric for easy plotting; the UI/report map it to a name.
        result["battery_status"] = float(status_code)
    return result


def battery_status_name(status_code: float | None) -> str:
    if status_code is None:
        return "—"
    return _STATUS_NAMES.get(int(status_code), f"code_{int(status_code)}")


def sample(device: AdbDevice, **_: object) -> dict[str, float]:
    out = device.shell("dumpsys battery")
    return parse_battery(out)
