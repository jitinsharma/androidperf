"""FPS and jank via `dumpsys gfxinfo <package>`.

Strategy:
- reset gfxinfo at session start and after every sample
- on each tick, parse the summary header of a fresh `dumpsys gfxinfo`
- derive fps as `frames / elapsed_seconds_since_last_reset`
"""

from __future__ import annotations

import contextlib
import re

from adbutils import AdbDevice

_PATTERNS = {
    "frames_total": re.compile(r"Total frames rendered:\s*(\d+)"),
    "jank_frames": re.compile(r"Janky frames:\s*(\d+)\s*\(([\d.]+)%\)"),
    "p50_ms": re.compile(r"50th percentile:\s*(\d+)ms"),
    "p90_ms": re.compile(r"90th percentile:\s*(\d+)ms"),
    "p95_ms": re.compile(r"95th percentile:\s*(\d+)ms"),
    "p99_ms": re.compile(r"99th percentile:\s*(\d+)ms"),
}


def parse_gfxinfo(output: str) -> dict[str, float]:
    """Extract the summary numbers. `jank_pct` is returned separately from count."""
    result: dict[str, float] = {}
    for key, pattern in _PATTERNS.items():
        match = pattern.search(output)
        if not match:
            continue
        if key == "jank_frames":
            result["jank_frames"] = float(match.group(1))
            with contextlib.suppress(ValueError):
                result["jank_pct"] = float(match.group(2))
        else:
            with contextlib.suppress(ValueError):
                result[key] = float(match.group(1))
    return result


def reset(device: AdbDevice, package: str) -> None:
    device.shell(f"dumpsys gfxinfo {package} reset")


def sample(
    device: AdbDevice,
    *,
    package: str,
    elapsed_since_reset_s: float,
    **_: object,
) -> dict[str, float]:
    """Sample gfxinfo and reset for the next window. FPS = frames / elapsed."""
    out = device.shell(f"dumpsys gfxinfo {package}")
    parsed = parse_gfxinfo(out)
    reset(device, package)
    frames = parsed.get("frames_total", 0.0)
    if elapsed_since_reset_s > 0 and frames > 0:
        parsed["fps"] = frames / elapsed_since_reset_s
    else:
        parsed["fps"] = 0.0
    return parsed
