"""Typer CLI entry point for androidperf."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .device import DeviceError, list_devices, list_packages, pick_device

app = typer.Typer(
    name="androidperf",
    help="Record Android app performance over ADB and render an HTML report.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True, style="red")


def _fail(message: str) -> None:
    err_console.print(message)
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"androidperf {__version__}")


@app.command()
def devices() -> None:
    """List connected ADB devices."""
    pairs = list_devices()
    if not pairs:
        _fail("No devices detected. Plug in a device or start an emulator.")
    table = Table(title="ADB devices")
    table.add_column("Serial", style="cyan")
    table.add_column("Manufacturer")
    table.add_column("Model")
    table.add_column("SDK", justify="right")
    for _, info in pairs:
        table.add_row(info.serial, info.manufacturer, info.model, str(info.sdk))
    console.print(table)


@app.command()
def packages(
    filter: str | None = typer.Option(None, "--filter", "-f", help="Substring filter (case-insensitive)."),
    serial: str | None = typer.Option(None, "--serial", "-s", help="Device serial."),
    limit: int = typer.Option(0, "--limit", "-n", help="Max packages to print (0 = all)."),
) -> None:
    """List installed packages on the selected device."""
    try:
        device, info = pick_device(serial)
    except DeviceError as exc:
        _fail(str(exc))

    names = list_packages(device, filter)
    if limit > 0:
        names = names[:limit]

    console.print(f"[dim]{info.label} — {len(names)} packages[/dim]")
    for name in names:
        console.print(name)


@app.command()
def record(
    package: str | None = typer.Option(None, "--package", "-p", help="Target package name. If omitted, prompts interactively."),
    interval: float = typer.Option(1.0, "--interval", "-i", min=0.1, help="Polling interval in seconds."),
    duration: float | None = typer.Option(None, "--duration", "-d", help="Optional max duration in seconds. Ctrl+C always stops early."),
    output_dir: Path = typer.Option(Path("./runs"), "--output-dir", "-o", help="Directory for run artifacts."),
    serial: str | None = typer.Option(None, "--serial", "-s", help="Device serial."),
    no_launch: bool = typer.Option(False, "--no-launch", help="Attach to the already-running process instead of launching."),
) -> None:
    """Launch an app and record CPU / RAM / network / FPS until stopped."""
    try:
        device, info = pick_device(serial)
    except DeviceError as exc:
        _fail(str(exc))

    if package is None:
        package = _prompt_package(device)

    # Session orchestration lives in session.py; import locally to keep `devices`
    # and `packages` commands fast to start.
    from .session import run_session

    try:
        run_dir = run_session(
            device=device,
            device_info=info,
            package=package,
            interval=interval,
            duration=duration,
            output_dir=output_dir,
            launch=not no_launch,
        )
    except DeviceError as exc:
        _fail(str(exc))

    console.print()
    console.print(f"[green]✓[/green] Run written to [bold]{run_dir}[/bold]")
    console.print(f"  [dim]samples[/dim]  {run_dir / 'samples.json'}")
    console.print(f"  [dim]report [/dim]  {run_dir / 'report.html'}")


@app.command()
def report(
    samples_json: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    output: Path | None = typer.Option(None, "--output", "-o", help="HTML output path. Defaults to sibling report.html."),
) -> None:
    """Regenerate the HTML report from an existing samples.json."""
    from .report.generate import generate_report

    out = output or samples_json.with_name("report.html")
    generate_report(samples_json, out)
    console.print(f"[green]Wrote[/green] {out}")


def _prompt_package(device) -> str:  # noqa: ANN001 - AdbDevice has no public type alias
    names = list_packages(device)
    if not names:
        _fail("No packages returned by `pm list packages`.")
    console.print(f"[dim]{len(names)} packages on device[/dim]")
    console.print("Type a filter substring (or blank to list first 30):")
    needle = typer.prompt("Filter", default="", show_default=False).strip().lower()
    shown = [n for n in names if needle in n.lower()] if needle else names[:30]
    if not shown:
        _fail(f"No packages match {needle!r}.")
    for i, name in enumerate(shown, start=1):
        console.print(f"  [cyan]{i:>3}[/cyan]  {name}")
    idx = typer.prompt("Select #", type=int)
    if not 1 <= idx <= len(shown):
        _fail(f"Index {idx} out of range.")
    return shown[idx - 1]


if __name__ == "__main__":
    app()
