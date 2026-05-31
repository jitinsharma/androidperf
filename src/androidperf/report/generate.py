"""Render a samples.json into a self-contained HTML report.

All charts are Plotly figures embedded inline in a single HTML file — no
external network calls needed to view the report.
"""

from __future__ import annotations

import json
from hashlib import md5
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from jinja2 import Environment, FileSystemLoader, select_autoescape

import dataclasses

from ..collectors.activity import class_short_name
from ..insights import analyze as analyze_insights
from ..summary import build_cards

# Curated dark-theme-friendly palette. Names get assigned colors by stable
# hash so the same fragment always renders the same color across runs.
_PALETTE = [
    "#60a5fa", "#34d399", "#fbbf24", "#f472b6", "#a78bfa",
    "#fb923c", "#22d3ee", "#84cc16", "#f87171", "#c084fc",
]


def _color_for(name: str) -> str:
    h = int(md5(name.encode("utf-8")).hexdigest()[:8], 16)
    return _PALETTE[h % len(_PALETTE)]

_TEMPLATE_DIR = Path(__file__).parent
_TEMPLATE_NAME = "template.html.j2"


def _layout(yaxis_title: str) -> dict[str, Any]:
    return {
        "template": "plotly_dark",
        "paper_bgcolor": "#141823",
        "plot_bgcolor": "#141823",
        "font": {"family": "-apple-system, Segoe UI, Roboto, sans-serif", "size": 12, "color": "#e6e9ef"},
        # Bottom margin holds the x-axis title + horizontal legend below
        # the plot. Screen-context now lives in the top-of-page swim-lane
        # and the unified hover, so we no longer reserve top space for it.
        "margin": {"l": 50, "r": 20, "t": 30, "b": 90},
        "height": 360,
        "hovermode": "x unified",
        "xaxis": {"title": "elapsed (s)", "gridcolor": "#1d2230"},
        "yaxis": {"title": yaxis_title, "gridcolor": "#1d2230", "rangemode": "tozero"},
        "showlegend": True,
        "legend": {"orientation": "h", "y": -0.28, "x": 0, "yanchor": "top"},
    }


def _segments(events: list[dict[str, Any]], kind: str, end_t: float) -> list[dict[str, Any]]:
    """Turn point-in-time events into [start, end, name] segments.

    Each event becomes a segment that starts at its `t` and ends when the
    next same-kind event begins (or `end_t` for the last one).
    """
    filtered = [e for e in events if e.get("type") == kind and e.get("t") is not None]
    filtered.sort(key=lambda e: float(e["t"]))
    segs: list[dict[str, Any]] = []
    for i, ev in enumerate(filtered):
        start = float(ev["t"])
        end = float(filtered[i + 1]["t"]) if i + 1 < len(filtered) else end_t
        if end <= start:
            continue
        full = ev.get("name", "")
        short = ev.get("short_name") or class_short_name(full) or full
        segs.append({"start": start, "end": end, "name": full, "short": short})
    return segs


def _series_for_samples(df: pd.DataFrame, segments: list[dict[str, Any]]) -> list[str]:
    """For each sample timestamp in `df`, find the segment that contains it."""
    if not segments or "t" not in df.columns:
        return ["—"] * len(df)
    out: list[str] = []
    for t in df["t"].fillna(0.0):
        match = next((s["short"] for s in segments if s["start"] <= t < s["end"]), "—")
        out.append(match)
    return out


def _timeline_figure(events: list[dict[str, Any]], duration: float) -> go.Figure | None:
    """Compact swim-lane: activity row on top, fragment row below, colored by
    name. Same x-axis scale as the metric charts so transitions line up."""
    activity_segs = _segments(events, "screen", duration)
    fragment_segs = _segments(events, "fragment", duration)
    if not activity_segs and not fragment_segs:
        return None

    fig = go.Figure()
    rows = []
    if activity_segs:
        rows.append(("Activity", activity_segs))
    if fragment_segs:
        rows.append(("Fragment", fragment_segs))

    for label, segs in rows:
        for seg in segs:
            fig.add_trace(go.Bar(
                x=[seg["end"] - seg["start"]],
                y=[label],
                base=[seg["start"]],
                orientation="h",
                marker={"color": _color_for(seg["short"]), "line": {"width": 0}},
                hovertemplate=(
                    f"<b>{seg['short']}</b><br>"
                    f"{seg['name']}<br>"
                    f"{seg['start']:.1f}s → {seg['end']:.1f}s"
                    "<extra></extra>"
                ),
                text=seg["short"],
                textposition="inside",
                insidetextanchor="start",
                textfont={"size": 11, "color": "#0b0d12"},
                showlegend=False,
                cliponaxis=False,
            ))

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#141823",
        plot_bgcolor="#141823",
        font={"family": "-apple-system, Segoe UI, Roboto, sans-serif", "size": 12, "color": "#e6e9ef"},
        margin={"l": 80, "r": 20, "t": 20, "b": 40},
        height=120 if len(rows) == 2 else 90,
        barmode="overlay",
        bargap=0.4,
        xaxis={"title": "elapsed (s)", "gridcolor": "#1d2230", "range": [0, duration]},
        yaxis={"gridcolor": "#1d2230", "categoryorder": "array", "categoryarray": [r[0] for r in rows][::-1]},
        showlegend=False,
        hovermode="closest",
    )
    return fig


def _add_context_hover(fig: go.Figure, df: pd.DataFrame, events: list[dict[str, Any]], duration: float) -> None:
    """Add an invisible trace whose tooltip carries activity + fragment so
    `hovermode='x unified'` shows the screen context next to metric values."""
    if "t" not in df.columns or df.empty:
        return
    activity = _series_for_samples(df, _segments(events, "screen", duration))
    fragment = _series_for_samples(df, _segments(events, "fragment", duration))
    # Plotly drops traces with all-None y values from unified hover, so we
    # plant the trace at y=0 with a fully transparent line. It still doesn't
    # render visually but it shows up in the x-unified tooltip.
    fig.add_trace(go.Scatter(
        x=df["t"],
        y=[0] * len(df),
        mode="lines",
        line={"color": "rgba(0,0,0,0)", "width": 0},
        customdata=list(zip(activity, fragment, strict=False)),
        name="screen",
        hovertemplate="activity: %{customdata[0]}<br>fragment: %{customdata[1]}<extra></extra>",
        showlegend=False,
    ))


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
    # Android UI rendering is demand-driven: the app submits frames only when
    # something changes on screen (animation tick, scroll, invalidation). This
    # trace is "frames submitted per second per tick" — useful as activity
    # context for the jank trace, not as a smoothness metric on its own.
    if "fps" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["t"], y=df["fps"], name="Frames/sec",
            mode="lines", line={"color": "#fcd34d", "width": 2},
        ))
    if "jank_pct" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["t"], y=df["jank_pct"], name="Jank %",
            mode="lines", yaxis="y2", line={"color": "#f87171", "dash": "dot"},
        ))
    layout = _layout("Frames/sec")
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
    gc_events = payload.get("gc_events", [])
    meta = payload.get("meta", {})

    findings = analyze_insights(samples=samples, events=events, meta=meta, gc_events=gc_events)
    findings_dicts = [dataclasses.asdict(f) for f in findings]

    df = pd.DataFrame(samples)
    if "t" not in df.columns:
        df["t"] = pd.Series(dtype=float)

    duration = float(df["t"].max()) if not df["t"].empty else 0.0

    charts: list[dict[str, Any]] = []
    timeline = _timeline_figure(events, duration)
    if timeline is not None:
        charts.append({"title": "Screen timeline", "fig": timeline})
    charts.extend([
        {"title": "CPU", "fig": _cpu_figure(df)},
        {"title": "Memory", "fig": _memory_figure(df)},
        {"title": "Network", "fig": _network_figure(df)},
        {"title": "Render activity (frames/sec) & jank", "fig": _fps_figure(df)},
        {"title": "Battery", "fig": _battery_figure(df)},
        {"title": "Thermal", "fig": _thermal_figure(df)},
    ])

    # Tooltip enrichment: fold the active activity/fragment at each timestamp
    # into every metric chart's unified hover. The swim-lane on top carries
    # the at-a-glance view; this is the precise per-tick lookup.
    for chart in charts:
        if chart["title"] == "Screen timeline":
            continue
        _add_context_hover(chart["fig"], df, events, duration)

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
        meta=meta,
        charts=rendered_charts,
        summary_cards=build_cards(df),
        events=events,
        findings=findings_dicts,
    )

    output_html = Path(output_html)
    output_html.write_text(out)
    return output_html
