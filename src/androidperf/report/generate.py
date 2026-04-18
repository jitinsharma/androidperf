"""Render a samples.json into a self-contained HTML report.

All charts are Plotly figures embedded inline in a single HTML file — no
external network calls needed to view the report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..collectors.activity import class_short_name
from ..summary import build_cards

_TEMPLATE_DIR = Path(__file__).parent
_TEMPLATE_NAME = "template.html.j2"


def _layout(yaxis_title: str) -> dict[str, Any]:
    return {
        "template": "plotly_dark",
        "paper_bgcolor": "#141823",
        "plot_bgcolor": "#141823",
        "font": {"family": "-apple-system, Segoe UI, Roboto, sans-serif", "size": 12, "color": "#e6e9ef"},
        # Top margin holds up to 3 stacked rows of event labels; bottom
        # margin holds the x-axis title + horizontal legend below plot.
        "margin": {"l": 50, "r": 20, "t": 80, "b": 90},
        "height": 360,
        "hovermode": "x unified",
        "xaxis": {"title": "elapsed (s)", "gridcolor": "#1d2230"},
        "yaxis": {"title": yaxis_title, "gridcolor": "#1d2230", "rangemode": "tozero"},
        "showlegend": True,
        "legend": {"orientation": "h", "y": -0.28, "x": 0, "yanchor": "top"},
    }


_MAX_LABEL_ROWS = 3
_ROW_Y_BASE = 1.05
_ROW_Y_STEP = 0.08


def _label_for(ev: dict[str, Any]) -> str:
    """Prefer an already-short label; otherwise derive it from the full name.

    Re-deriving means older samples.json files (with long dotted short_names)
    still render cleanly when re-rendered via `androidperf report`.
    """
    raw = ev.get("short_name") or ev.get("name", "")
    return class_short_name(raw) if raw else ""


def _pack_rows(events: list[dict[str, Any]], duration: float) -> list[tuple[dict[str, Any], int]]:
    """Assign each event a row index so labels don't horizontally overlap.

    Uses a rough 'per-character x-extent' heuristic — we don't know the
    plot's pixel width at render time, so duration scales the gap. Events
    that can't fit in the first `_MAX_LABEL_ROWS` pile onto the last row.
    """
    per_char_x = max(duration * 0.012, 0.4) if duration > 0 else 1.0
    rows_right: list[float] = []
    placed: list[tuple[dict[str, Any], int]] = []
    for ev in events:
        if ev.get("type") != "screen" or ev.get("t") is None:
            continue
        t = float(ev["t"])
        extent = t + len(_label_for(ev)) * per_char_x
        row: int | None = None
        for i, right in enumerate(rows_right):
            if t > right:
                row = i
                break
        if row is None:
            if len(rows_right) < _MAX_LABEL_ROWS:
                row = len(rows_right)
                rows_right.append(extent)
            else:
                row = _MAX_LABEL_ROWS - 1
                rows_right[row] = extent
        else:
            rows_right[row] = extent
        placed.append((ev, row))
    return placed


def _apply_event_shapes(fig: go.Figure, events: list[dict[str, Any]], duration: float) -> None:
    """Draw a vertical dashed line + (row-stacked) label for each screen event."""
    if not events:
        return
    shapes = []
    annotations = []
    for ev, row in _pack_rows(events, duration):
        t = ev["t"]
        shapes.append({
            "type": "line",
            "xref": "x",
            "yref": "paper",
            "x0": t,
            "x1": t,
            "y0": 0,
            "y1": 1,
            "line": {"color": "#8a94a6", "width": 1, "dash": "dot"},
        })
        annotations.append({
            "x": t,
            "y": _ROW_Y_BASE + row * _ROW_Y_STEP,
            "xref": "x",
            "yref": "paper",
            "text": _label_for(ev),
            "showarrow": False,
            "font": {"size": 11, "color": "#a3adc2"},
            "xanchor": "left",
            "yanchor": "middle",
            "hovertext": ev.get("name", ""),
        })
    if shapes:
        fig.update_layout(shapes=shapes, annotations=annotations)


def _cpu_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df["t"], y=df.get("cpu_pct"), name="CPU %", mode="lines", line={"color": "#6ee7b7", "width": 2}))
    fig.update_layout(**_layout("% CPU"))
    return fig


def _memory_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    traces = (
        ("pss_kb", "Total PSS"),
        ("java_kb", "Java"),
        ("native_kb", "Native"),
        ("gfx_kb", "Graphics"),
    )
    for key, label in traces:
        if key in df.columns:
            fig.add_trace(
                go.Scatter(x=df["t"], y=df[key] / 1024.0, name=label, mode="lines")
            )
    fig.update_layout(**_layout("MB"))
    return fig


def _network_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if "rx_b" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["t"], y=df["rx_b"] / 1024.0, name="rx (KB/tick)",
            mode="lines", line={"color": "#60a5fa"},
        ))
    if "tx_b" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["t"], y=df["tx_b"] / 1024.0, name="tx (KB/tick)",
            mode="lines", line={"color": "#f87171"},
        ))
    fig.update_layout(**_layout("KB per sample"))
    return fig


def _fps_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if "fps" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["t"], y=df["fps"], name="FPS",
            mode="lines", line={"color": "#fcd34d", "width": 2},
        ))
    if "jank_pct" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["t"], y=df["jank_pct"], name="Jank %",
            mode="lines", yaxis="y2", line={"color": "#f87171", "dash": "dot"},
        ))
    layout = _layout("FPS")
    layout["yaxis2"] = {
        "title": "Jank %",
        "overlaying": "y",
        "side": "right",
        "gridcolor": "#1d2230",
        "rangemode": "tozero",
    }
    fig.update_layout(**layout)
    return fig


def _battery_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    if "battery_level_pct" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["t"], y=df["battery_level_pct"], name="Level %",
            mode="lines", line={"color": "#34d399", "width": 2},
        ))
    if "battery_temp_c" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["t"], y=df["battery_temp_c"], name="Temp °C",
            mode="lines", yaxis="y2", line={"color": "#fb923c", "dash": "dot"},
        ))
    layout = _layout("Level %")
    layout["yaxis"]["rangemode"] = "normal"
    layout["yaxis2"] = {
        "title": "°C",
        "overlaying": "y",
        "side": "right",
        "gridcolor": "#1d2230",
    }
    fig.update_layout(**layout)
    return fig


def _thermal_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    traces = (
        ("thermal_skin_c", "Skin", "#f472b6"),
        ("thermal_cpu_c", "CPU", "#f87171"),
        ("thermal_gpu_c", "GPU", "#c084fc"),
        ("thermal_battery_c", "Battery", "#34d399"),
    )
    for key, label, color in traces:
        if key in df.columns:
            fig.add_trace(go.Scatter(
                x=df["t"], y=df[key], name=label,
                mode="lines", line={"color": color, "width": 1.5},
            ))
    if "thermal_status" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["t"], y=df["thermal_status"], name="Status",
            mode="lines", yaxis="y2", line={"color": "#8a94a6", "dash": "dot"},
        ))
    layout = _layout("°C")
    layout["yaxis"]["rangemode"] = "normal"
    layout["yaxis2"] = {
        "title": "Status",
        "overlaying": "y",
        "side": "right",
        "gridcolor": "#1d2230",
        "range": [0, 6],
    }
    fig.update_layout(**layout)
    return fig




def generate_report(samples_json: Path, output_html: Path) -> Path:
    """Build a self-contained HTML report from a samples.json file."""
    payload = json.loads(Path(samples_json).read_text())
    samples = payload.get("samples", [])
    events = payload.get("events", [])
    df = pd.DataFrame(samples)
    if "t" not in df.columns:
        df["t"] = pd.Series(dtype=float)

    charts = [
        {"title": "CPU", "fig": _cpu_figure(df)},
        {"title": "Memory", "fig": _memory_figure(df)},
        {"title": "Network", "fig": _network_figure(df)},
        {"title": "FPS / jank", "fig": _fps_figure(df)},
        {"title": "Battery", "fig": _battery_figure(df)},
        {"title": "Thermal", "fig": _thermal_figure(df)},
    ]

    # Overlay screen-transition markers on every chart for correlation.
    duration = float(df["t"].max()) if not df["t"].empty else 0.0
    for chart in charts:
        _apply_event_shapes(chart["fig"], events, duration)

    rendered_charts: list[dict[str, str]] = []
    for i, chart in enumerate(charts):
        # Only the first figure bundles the plotly.js library inline; the rest
        # reuse the already-loaded global so the file size doesn't explode.
        include_js: str | bool = "inline" if i == 0 else False
        html = chart["fig"].to_html(
            include_plotlyjs=include_js,
            full_html=False,
            config={"displaylogo": False, "responsive": True},
        )
        rendered_charts.append({"title": chart["title"], "html": html})

    env = Environment(
        loader=FileSystemLoader(_TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template(_TEMPLATE_NAME)
    out = template.render(
        meta=payload.get("meta", {}),
        charts=rendered_charts,
        summary_cards=build_cards(df),
        events=events,
    )

    output_html = Path(output_html)
    output_html.write_text(out)
    return output_html
