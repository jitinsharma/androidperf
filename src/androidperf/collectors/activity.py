"""Foreground activity detection via `dumpsys activity activities`.

Used by the session to emit a `{type: screen}` event whenever the current
activity changes for the target package. A single fast shell call per tick.
"""

from __future__ import annotations

import re

from adbutils import AdbDevice

# Matches patterns like `com.example.app/.MainActivity` or
# `com.example.app/com.example.ui.HomeActivity`.
_ACTIVITY_RE = re.compile(r"([\w\.]+/[\w\.\$]+)")


def parse_resumed_activity(output: str, package: str) -> str | None:
    """Extract the currently resumed activity component for `package`, if any."""
    for line in output.splitlines():
        low = line.strip().lower()
        if "resumedactivity" not in low:
            continue
        for match in _ACTIVITY_RE.finditer(line):
            component = match.group(1)
            if component.startswith(package + "/"):
                return component
    return None


def current_activity(device: AdbDevice, package: str) -> str | None:
    out = device.shell("dumpsys activity activities")
    return parse_resumed_activity(out, package)


def class_short_name(resolved: str) -> str:
    """Reduce a resolved activity to just its class name.

    Examples:
        ``com.foo.bar/.a.b.MainActivity`` → ``MainActivity``
        ``com.foo.bar/com.foo.ui.HomeActivity`` → ``HomeActivity``
        ``a.b.c.SettingsActivity`` → ``SettingsActivity``
    """
    tail = resolved.split("/", 1)[1] if "/" in resolved else resolved
    tail = tail.lstrip(".")
    return tail.rsplit(".", 1)[-1]
