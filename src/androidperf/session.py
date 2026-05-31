"""Recording session: polling loop, signal handling, JSON writer."""

from __future__ import annotations

import json
import signal
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adbutils import AdbDevice
from rich.console import Console

from .collectors import activity, battery, cpu, fps, fragments, heapdump, hprof_parse, memory, network, procfs, thermal
from .collectors.logcat import LogcatCollector
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
    on_sample: Callable[[dict[str, Any]], None] | None = None,
    on_status: Callable[[str], None] | None = None,
    stop_event: threading.Event | None = None,
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

    stop = stop_event if stop_event is not None else threading.Event()
    started_mono = time.monotonic()
    logcat = LogcatCollector(serial=device_info.serial, started_mono=started_mono)

    def _on_sigint(signum: int, frame: object) -> None:  # noqa: ARG001
        stop.set()

    _is_main_thread = threading.current_thread() is threading.main_thread()
    previous_sigint = signal.getsignal(signal.SIGINT)
    if _is_main_thread:
        signal.signal(signal.SIGINT, _on_sigint)

    started_at = _utcnow_iso()

    samples: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    prev_rx = prev_tx = 0.0
    have_prev_net = False
    prev_csw_vol = prev_csw_nonvol = prev_disk_read = prev_disk_write = 0.0
    have_prev_procfs = False
    last_activity: str | None = None
    last_fragment: str | None = None

    run_dir = _timestamped_dir(output_dir, package)
    logcat.start()

    # Collectors are all IO-bound on `adb shell`. Running them on a thread
    # pool makes the per-tick cost ~max(individual) instead of sum(individual);
    # on a busy app this is the difference between 1 s and 4 s per tick.
    try:
        with (
            ThreadPoolExecutor(max_workers=10, thread_name_prefix="collector") as pool,
            LiveDashboard(package=package, device_label=device_info.label) as ui,
        ):
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

                fut_cpu = pool.submit(cpu.sample, device, pid=pid)
                fut_mem = pool.submit(memory.sample, device, package=package)
                fut_net = pool.submit(network.sample, device, uid=uid)
                fut_fps = pool.submit(fps.sample, device, package=package)
                fut_batt = pool.submit(battery.sample, device)
                fut_therm = pool.submit(thermal.sample, device)
                fut_act = pool.submit(activity.current_activity, device, package)
                # Fragment dump needs the resumed activity component. Use the
                # last-known activity to keep this query parallel; on the very
                # first tick we have nothing, so it returns None.
                fut_frag = pool.submit(fragments.current_fragment, device, last_activity)
                fut_procfs = pool.submit(procfs.sample, device, pid=pid)
                fut_obj = pool.submit(memory.sample_objects, device, package=package)
                fut_tnames = pool.submit(procfs.sample_thread_names, device, pid=pid)

                # Each collector is wrapped so one failing metric doesn't abort the session.
                try:
                    sample.update(fut_cpu.result())
                except Exception as exc:  # noqa: BLE001
                    sample["_cpu_error"] = repr(exc)
                try:
                    sample.update(fut_mem.result())
                except Exception as exc:  # noqa: BLE001
                    sample["_mem_error"] = repr(exc)
                try:
                    net = fut_net.result()
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
                    sample.update(fut_fps.result())
                except Exception as exc:  # noqa: BLE001
                    sample["_fps_error"] = repr(exc)
                try:
                    sample.update(fut_batt.result())
                except Exception as exc:  # noqa: BLE001
                    sample["_battery_error"] = repr(exc)
                try:
                    sample.update(fut_therm.result())
                except Exception as exc:  # noqa: BLE001
                    sample["_thermal_error"] = repr(exc)

                # Activity transition → event.
                try:
                    current = fut_act.result()
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

                # Fragment transition → event. Best-effort: silent on apps
                # that don't use FragmentManager.
                try:
                    current_frag = fut_frag.result()
                    if current_frag and current_frag != last_fragment:
                        events.append({
                            "t": sample["t"],
                            "type": "fragment",
                            "name": current_frag,
                            "short_name": current_frag.split(" / ")[-1],
                        })
                        last_fragment = current_frag
                    sample["fragment"] = last_fragment  # type: ignore[assignment]
                except Exception as exc:  # noqa: BLE001
                    sample["_fragment_error"] = repr(exc)

                # procfs: threads, context switches, disk I/O (delta)
                try:
                    pfs = fut_procfs.result()
                    if pfs.get("threads") is not None:
                        sample["threads"] = pfs["threads"]
                    if have_prev_procfs:
                        sample["csw_vol"] = max(0, pfs.get("csw_vol_total", 0) - prev_csw_vol)
                        sample["csw_nonvol"] = max(0, pfs.get("csw_nonvol_total", 0) - prev_csw_nonvol)
                        sample["disk_read_b"] = max(0, pfs.get("disk_read_total_b", 0) - prev_disk_read)
                        sample["disk_write_b"] = max(0, pfs.get("disk_write_total_b", 0) - prev_disk_write)
                    else:
                        have_prev_procfs = True
                    prev_csw_vol = pfs.get("csw_vol_total", prev_csw_vol)
                    prev_csw_nonvol = pfs.get("csw_nonvol_total", prev_csw_nonvol)
                    prev_disk_read = pfs.get("disk_read_total_b", prev_disk_read)
                    prev_disk_write = pfs.get("disk_write_total_b", prev_disk_write)
                except Exception as exc:  # noqa: BLE001
                    sample["_procfs_error"] = repr(exc)

                try:
                    sample.update(fut_obj.result())
                except Exception as exc:  # noqa: BLE001
                    sample["_obj_error"] = repr(exc)

                try:
                    sample["thread_names"] = fut_tnames.result()
                except Exception as exc:  # noqa: BLE001
                    sample["_tnames_error"] = repr(exc)

                samples.append(sample)
                if on_sample is not None:
                    on_sample(sample)
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
        if _is_main_thread:
            signal.signal(signal.SIGINT, previous_sigint)
        logcat.stop()

    gc_events = logcat.gc_events()

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
            "gc_event_count": len(gc_events),
        },
        "samples": samples,
        "events": events,
        "gc_events": gc_events,
    }

    json_path = run_dir / "samples.json"
    _atomic_write_json(json_path, payload)

    con = Console()

    # Heap dump: best-effort, debug builds and emulators only.
    heap_histogram: list[dict] = []
    hprof_path = run_dir / "heap.hprof"
    if on_status:
        on_status("Capturing heap dump…")
    con.print("[dim]Capturing heap dump (debug builds only)…[/dim]")
    try:
        if heapdump.capture(device, package, hprof_path):
            heap_histogram = hprof_parse.parse_histogram(hprof_path)
            _atomic_write_json(run_dir / "heap_histogram.json", {"histogram": heap_histogram})
            con.print(f"[green]✓ Heap dump:[/green] {len(heap_histogram)} classes → heap_histogram.json")
        else:
            con.print("[dim]  Heap dump skipped (app not debuggable or not supported)[/dim]")
    except Exception as exc:  # noqa: BLE001
        con.print(f"[dim]  Heap dump failed: {exc}[/dim]")

    con.print(render_summary(samples))

    html_path = run_dir / "report.html"
    generate_report(json_path, html_path, heap_histogram=heap_histogram)

    return run_dir
