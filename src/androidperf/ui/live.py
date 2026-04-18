"""Rich live-updating panel shown during a recording session."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rich.align import Align
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..collectors.battery import battery_status_name
from ..collectors.thermal import thermal_status_name

_MAX_SPARK = 120
_SPARK_CHARS = "▁▂▃▄▅▆▇█"
_PANEL_HEIGHT = 11  # rows, including top/bottom border + title line


def _sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo = min(values)
    hi = max(values)
    if hi == lo:
        return _SPARK_CHARS[0] * len(values)
    span = hi - lo
    step = len(_SPARK_CHARS) - 1
    return "".join(_SPARK_CHARS[int((v - lo) / span * step)] for v in values)


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _fmt_kb(kb: float) -> str:
    if kb > 1024:
        return f"{kb / 1024:.1f} MB"
    return f"{kb:.0f} KB"


@dataclass
class _Series:
    cpu: list[float] = field(default_factory=list)
    pss: list[float] = field(default_factory=list)
    rx: list[float] = field(default_factory=list)
    tx: list[float] = field(default_factory=list)
    fps: list[float] = field(default_factory=list)

    def push(self, sample: dict[str, float]) -> None:
        for attr, key in (
            ("cpu", "cpu_pct"),
            ("pss", "pss_kb"),
            ("rx", "rx_b"),
            ("tx", "tx_b"),
            ("fps", "fps"),
        ):
            value = sample.get(key)
            if value is None:
                continue
            bucket: list[float] = getattr(self, attr)
            bucket.append(float(value))
            if len(bucket) > _MAX_SPARK:
                del bucket[0]


class LiveDashboard:
    """Context manager that renders a 4-panel dashboard while samples stream in."""

    def __init__(self, *, package: str, device_label: str) -> None:
        self._package = package
        self._device = device_label
        self._series = _Series()
        self._latest: dict[str, Any] = {}
        self._tick = 0
        self._elapsed = 0.0
        self._current_screen: str | None = None
        self._live: Live | None = None

    def __enter__(self) -> LiveDashboard:
        self._live = Live(
            self._render(),
            refresh_per_second=8,
            transient=False,
            screen=False,
        )
        self._live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._live is not None:
            self._live.__exit__(exc_type, exc, tb)
            self._live = None

    def update(
        self,
        *,
        sample: dict[str, Any],
        tick: int,
        elapsed_s: float,
        current_screen: str | None = None,
    ) -> None:
        self._latest = sample
        self._tick = tick
        self._elapsed = elapsed_s
        self._current_screen = current_screen
        self._series.push(sample)
        if self._live is not None:
            self._live.update(self._render())

    # ------------------------------------------------------------------

    def _render(self) -> Group:
        # Use a Table.grid + Group rather than rich.layout.Layout: Layout
        # expands to fill the terminal height and leaves dead space below
        # the footer. A Group is naturally sized to its content.
        body = Table.grid(expand=True)
        for _ in range(4):
            body.add_column(ratio=1)
        body.add_row(
            self._cpu_panel(),
            self._mem_panel(),
            self._net_panel(),
            self._fps_panel(),
        )
        return Group(
            self._header(),
            self._status_row(),
            body,
            self._footer(),
        )

    def _header(self) -> Panel:
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")
        mins, secs = divmod(int(self._elapsed), 60)
        table.add_row(
            Text.assemble(
                ("androidperf ", "bold cyan"),
                ("· ", "dim"),
                (self._package, "bold"),
                ("  ", ""),
                (self._device, "dim"),
            ),
            Text.assemble(
                ("samples ", "dim"),
                (f"{self._tick}", "bold"),
                ("   elapsed ", "dim"),
                (f"{mins:02d}:{secs:02d}", "bold"),
            ),
        )
        return Panel(table, border_style="cyan")

    def _footer(self) -> Align:
        return Align.center(Text("Ctrl+C to stop", style="dim"))

    def _status_row(self) -> Panel:
        # screen
        screen = self._current_screen
        screen_text = (screen.split("/", 1)[1].lstrip(".") if "/" in screen else screen) if screen else "—"

        # battery
        lvl = self._latest.get("battery_level_pct")
        btemp = self._latest.get("battery_temp_c")
        bstatus = battery_status_name(self._latest.get("battery_status"))
        battery_text = Text.assemble(
            (f"{lvl:.0f}%" if lvl is not None else "—", "bold"),
            ("  ", ""),
            (bstatus, "dim"),
            ("  ", ""),
            (f"{btemp:.1f}°C" if btemp is not None else "", "dim"),
        )

        # thermal
        tstatus = thermal_status_name(self._latest.get("thermal_status"))
        skin = self._latest.get("thermal_skin_c")
        cpu_c = self._latest.get("thermal_cpu_c")
        thermal_parts: list[tuple[str, str]] = [(tstatus, "bold")]
        if skin is not None:
            thermal_parts.append(("  skin ", "dim"))
            thermal_parts.append((f"{skin:.1f}°C", ""))
        if cpu_c is not None:
            thermal_parts.append(("  cpu ", "dim"))
            thermal_parts.append((f"{cpu_c:.1f}°C", ""))
        thermal_text = Text.assemble(*thermal_parts)

        table = Table.grid(expand=True)
        table.add_column(justify="left", ratio=2)
        table.add_column(justify="left", ratio=1)
        table.add_column(justify="left", ratio=1)
        table.add_row(
            Text.assemble(("screen ", "dim"), (screen_text, "bold white")),
            Text.assemble(("battery ", "dim"), battery_text),
            Text.assemble(("thermal ", "dim"), thermal_text),
        )
        return Panel(table, border_style="grey37")

    def _cpu_panel(self) -> Panel:
        value = self._latest.get("cpu_pct")
        big = Text(f"{value:5.1f} %" if value is not None else "  —", style="bold green")
        spark = Text(_sparkline(self._series.cpu), style="green")
        body = Align.center(
            Group(
                Align.center(big),
                Text(""),
                Align.center(spark),
                Text(""),
                Align.center(Text("per-process CPU", style="dim")),
            ),
            vertical="middle",
        )
        return Panel(body, title="[bold]CPU[/bold]", border_style="green", height=_PANEL_HEIGHT)

    def _mem_panel(self) -> Panel:
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")
        rows = [
            ("Total PSS", _fmt_kb(self._latest["pss_kb"])) if "pss_kb" in self._latest else ("Total PSS", "—"),
            ("Java", _fmt_kb(self._latest["java_kb"])) if "java_kb" in self._latest else ("Java", "—"),
            ("Native", _fmt_kb(self._latest["native_kb"])) if "native_kb" in self._latest else ("Native", "—"),
            ("Graphics", _fmt_kb(self._latest["gfx_kb"])) if "gfx_kb" in self._latest else ("Graphics", "—"),
        ]
        for label, value in rows:
            table.add_row(Text(label, style="dim"), Text(value, style="bold"))
        spark = Text(_sparkline(self._series.pss), style="magenta")
        body = Align.center(Group(table, Text(""), Align.center(spark)), vertical="middle")
        return Panel(body, title="[bold]Memory[/bold]", border_style="magenta", height=_PANEL_HEIGHT)

    def _net_panel(self) -> Panel:
        rx = self._latest.get("rx_b", 0.0)
        tx = self._latest.get("tx_b", 0.0)
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")
        table.add_row(Text("↓ rx", style="dim"), Text(_fmt_bytes(rx) + "/s", style="bold blue"))
        table.add_row(Text("↑ tx", style="dim"), Text(_fmt_bytes(tx) + "/s", style="bold blue"))
        rx_line = Text(_sparkline(self._series.rx), style="blue")
        tx_line = Text(_sparkline(self._series.tx), style="blue")
        body = Align.center(
            Group(table, Text(""), Align.center(rx_line), Align.center(tx_line)),
            vertical="middle",
        )
        return Panel(body, title="[bold]Network[/bold]", border_style="blue", height=_PANEL_HEIGHT)

    def _fps_panel(self) -> Panel:
        fps_val = self._latest.get("fps")
        jank = self._latest.get("jank_pct")
        p95 = self._latest.get("p95_ms")
        table = Table.grid(expand=True)
        table.add_column(justify="left")
        table.add_column(justify="right")
        table.add_row(
            Text("fps", style="dim"),
            Text(f"{fps_val:5.1f}" if fps_val is not None else "—", style="bold yellow"),
        )
        table.add_row(
            Text("jank %", style="dim"),
            Text(f"{jank:.1f}" if jank is not None else "—", style="bold"),
        )
        table.add_row(
            Text("p95 ms", style="dim"),
            Text(f"{p95:.0f}" if p95 is not None else "—", style="bold"),
        )
        spark = Text(_sparkline(self._series.fps), style="yellow")
        body = Align.center(Group(table, Text(""), Align.center(spark)), vertical="middle")
        return Panel(body, title="[bold]FPS[/bold]", border_style="yellow", height=_PANEL_HEIGHT)
