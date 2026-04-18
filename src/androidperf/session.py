"""Recording session: polling loop, signal handling, JSON writer."""

from __future__ import annotations

import json
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adbutils import AdbDevice
from rich.console import Console

from .collectors import activity, battery, cpu, fps, memory, network, thermal
from .device import DeviceError, DeviceInfo, get_pid, get_uid, launch_app
from .report.generate import generate_report
from .ui.live import LiveDashboard
from .ui.summary import render_summary


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _timestamped_dir(root: Path, package: str) -> Path:
    slug = package.replace(".", "_")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = root / f"{stamp}-{slug}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def run_session(
    *,
    device: AdbDevice,
    device_info: DeviceInfo,
    package: str,
    interval: float,
    duration: float | None,
    output_dir: Path,
    launch: bool,
) -> Path:
    """Drive the polling loop and write samples.json + report.html. Returns run dir."""
    if device_info.sdk and device_info.sdk < 24:
        raise DeviceError(
            f"Device SDK {device_info.sdk} is too old; gfxinfo framestats requires SDK >= 24."
        )

    if launch:
        pid, uid = launch_app(device, package)
    else:
        pid = get_pid(device, package)
        if pid is None:
            raise DeviceError(f"Package {package} is not running; pass without --no-launch to launch it.")
        uid_val = get_uid(device, pid)
        if uid_val is None:
            raise DeviceError(f"Could not read uid for pid {pid}.")
        uid = uid_val

    fps.reset(device, package)

    stop = threading.Event()

    def _on_sigint(signum: int, frame: object) -> None:  # noqa: ARG001
        stop.set()

    previous_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _on_sigint)

    started_at = _utcnow_iso()
    started_mono = time.monotonic()
    last_reset_mono = started_mono

    samples: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    prev_rx = prev_tx = 0.0
    have_prev_net = False
    last_activity: str | None = None

    run_dir = _timestamped_dir(output_dir, package)

    try:
        with LiveDashboard(package=package, device_label=device_info.label) as ui:
            tick_index = 0
            next_tick = started_mono
            while not stop.is_set():
                now_mono = time.monotonic()
                if duration is not None and now_mono - started_mono >= duration:
                    break
                if now_mono < next_tick:
                    # Sleep in short slices so Ctrl+C responds quickly.
                    stop.wait(timeout=min(0.1, next_tick - now_mono))
                    continue

                sample: dict[str, float] = {"t": round(now_mono - started_mono, 3)}

                # Each collector is wrapped so one failing metric doesn't abort the session.
                try:
                    sample.update(cpu.sample(device, pid=pid))
                except Exception as exc:  # noqa: BLE001
                    sample["_cpu_error"] = repr(exc)
                try:
                    sample.update(memory.sample(device, package=package))
                except Exception as exc:  # noqa: BLE001
                    sample["_mem_error"] = repr(exc)
                try:
                    net = network.sample(device, uid=uid)
                    rx = net.get("rx_total_b", 0.0)
                    tx = net.get("tx_total_b", 0.0)
                    if have_prev_net:
                        sample["rx_b"] = max(0.0, rx - prev_rx)
                        sample["tx_b"] = max(0.0, tx - prev_tx)
                    else:
                        sample["rx_b"] = 0.0
                        sample["tx_b"] = 0.0
                        have_prev_net = True
                    prev_rx, prev_tx = rx, tx
                except Exception as exc:  # noqa: BLE001
                    sample["_net_error"] = repr(exc)
                try:
                    elapsed_window = max(time.monotonic() - last_reset_mono, 1e-6)
                    sample.update(
                        fps.sample(
                            device,
                            package=package,
                            elapsed_since_reset_s=elapsed_window,
                        )
                    )
                    last_reset_mono = time.monotonic()
                except Exception as exc:  # noqa: BLE001
                    sample["_fps_error"] = repr(exc)
                try:
                    sample.update(battery.sample(device))
                except Exception as exc:  # noqa: BLE001
                    sample["_battery_error"] = repr(exc)
                try:
                    sample.update(thermal.sample(device))
                except Exception as exc:  # noqa: BLE001
                    sample["_thermal_error"] = repr(exc)

                # Activity transition → event. Cheap to check each tick.
                try:
                    current = activity.current_activity(device, package)
                    if current and current != last_activity:
                        events.append({
                            "t": sample["t"],
                            "type": "screen",
                            "name": current,
                            "short_name": activity.class_short_name(current),
                        })
                        last_activity = current
                    sample["activity"] = last_activity  # type: ignore[assignment]
                except Exception as exc:  # noqa: BLE001
                    sample["_activity_error"] = repr(exc)

                samples.append(sample)
                tick_index += 1
                ui.update(
                    sample=sample,
                    tick=tick_index,
                    elapsed_s=sample["t"],
                    current_screen=last_activity,
                )

                # Target cadence — don't drift if a sample took too long.
                next_tick = max(next_tick + interval, time.monotonic())
    finally:
        signal.signal(signal.SIGINT, previous_sigint)

    # Persist first — anything below (summary panel, HTML render) is best-effort
    # post-processing. A failure there must not lose samples that were captured.
    ended_at = _utcnow_iso()
    payload = {
        "meta": {
            "device": {
                "serial": device_info.serial,
                "model": device_info.model,
                "manufacturer": device_info.manufacturer,
                "sdk": device_info.sdk,
            },
            "package": package,
            "pid": pid,
            "uid": uid,
            "started_at": started_at,
            "ended_at": ended_at,
            "interval_s": interval,
            "sample_count": len(samples),
            "event_count": len(events),
        },
        "samples": samples,
        "events": events,
    }

    json_path = run_dir / "samples.json"
    _atomic_write_json(json_path, payload)

    Console().print(render_summary(samples))

    html_path = run_dir / "report.html"
    generate_report(json_path, html_path)

    return run_dir
