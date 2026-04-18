"""Device detection, package listing, and app launch helpers built on adbutils."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from adbutils import AdbDevice, adb


@dataclass(frozen=True)
class DeviceInfo:
    serial: str
    model: str
    manufacturer: str
    sdk: int

    @property
    def label(self) -> str:
        return f"{self.manufacturer} {self.model} (sdk {self.sdk})"


class DeviceError(RuntimeError):
    """Raised when ADB interaction fails in a way the CLI should surface."""


def list_devices() -> list[tuple[AdbDevice, DeviceInfo]]:
    """Return connected devices paired with cached metadata."""
    pairs: list[tuple[AdbDevice, DeviceInfo]] = []
    for dev in adb.device_list():
        try:
            sdk = int(dev.getprop("ro.build.version.sdk") or "0")
        except ValueError:
            sdk = 0
        info = DeviceInfo(
            serial=dev.serial,
            model=dev.getprop("ro.product.model") or "unknown",
            manufacturer=dev.getprop("ro.product.manufacturer") or "unknown",
            sdk=sdk,
        )
        pairs.append((dev, info))
    return pairs


def pick_device(serial: str | None = None) -> tuple[AdbDevice, DeviceInfo]:
    """Return a single device. Fails loudly when the selection is ambiguous."""
    pairs = list_devices()
    if not pairs:
        raise DeviceError("No ADB devices detected. Plug in a device or start an emulator.")
    if serial:
        for dev, info in pairs:
            if dev.serial == serial:
                return dev, info
        raise DeviceError(f"No device matching serial {serial!r}.")
    if len(pairs) > 1:
        serials = ", ".join(d.serial for d, _ in pairs)
        raise DeviceError(
            f"Multiple devices connected ({serials}). Pass --serial to disambiguate."
        )
    return pairs[0]


def list_packages(device: AdbDevice, filter_substr: str | None = None) -> list[str]:
    """Return installed package names, optionally filtered by substring match."""
    out = device.shell("pm list packages")
    names = sorted(
        line.removeprefix("package:").strip()
        for line in out.splitlines()
        if line.startswith("package:")
    )
    if filter_substr:
        needle = filter_substr.lower()
        names = [n for n in names if needle in n.lower()]
    return names


_LAUNCHER_RE = re.compile(r"name=([\w\.$/]+)")


def resolve_main_activity(device: AdbDevice, package: str) -> str:
    """Resolve the package's MAIN/LAUNCHER activity into a `pkg/.Activity` component name."""
    out = device.shell(
        f"cmd package resolve-activity --brief {package}"
    ).strip()
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    for line in lines:
        if "/" in line and not line.startswith("priority"):
            return line
    out = device.shell(
        "cmd package query-activities --brief "
        f"-a android.intent.action.MAIN -c android.intent.category.LAUNCHER {package}"
    )
    for line in out.splitlines():
        match = _LAUNCHER_RE.search(line)
        if match:
            return match.group(1)
    raise DeviceError(
        f"Could not resolve launcher activity for {package}. "
        "Is the package installed and does it declare MAIN/LAUNCHER?"
    )


def get_pid(device: AdbDevice, package: str) -> int | None:
    """Return the pid of a running process for `package`, or None if not running."""
    out = device.shell(f"pidof {package}").strip()
    if not out:
        return None
    try:
        return int(out.split()[0])
    except ValueError:
        return None


def get_uid(device: AdbDevice, pid: int) -> int | None:
    """Read /proc/<pid>/status and return the effective UID, or None if unreadable."""
    out = device.shell(f"cat /proc/{pid}/status")
    for line in out.splitlines():
        if line.startswith("Uid:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    return None
    return None


def launch_app(
    device: AdbDevice,
    package: str,
    *,
    wait_seconds: float = 10.0,
    poll_interval: float = 0.25,
) -> tuple[int, int]:
    """Start the app's launcher activity and return (pid, uid) once it's running."""
    component = resolve_main_activity(device, package)
    device.shell(f"am start -W -n {component}")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        pid = get_pid(device, package)
        if pid is not None:
            uid = get_uid(device, pid)
            if uid is None:
                raise DeviceError(f"Process {package} (pid {pid}) has no readable uid.")
            return pid, uid
        time.sleep(poll_interval)
    raise DeviceError(f"Timed out waiting for {package} to start (waited {wait_seconds:.0f}s).")
