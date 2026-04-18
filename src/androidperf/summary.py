"""Shared summary-card computation.

Both the HTML report and the terminal end-of-session panel read from this so
the numbers (and units) stay consistent.
"""

from __future__ import annotations

import pandas as pd


def fmt_bytes_from_kb(kb: float) -> str:
    """Auto-scale a byte total given in KB → KB / MB / GB as appropriate."""
    if kb >= 1024 * 1024:
        return f"{kb / (1024 * 1024):.2f} GB"
    if kb >= 1024:
        return f"{kb / 1024:.1f} MB"
    return f"{kb:.1f} KB"


def _fmt_mean(series: pd.Series, precision: int = 1) -> str:
    if series.empty:
        return "—"
    return f"{series.mean():.{precision}f}"


def build_cards(df: pd.DataFrame) -> list[dict[str, str]]:
    """Summary stats derived from a sample DataFrame. Stable key shape:
    ``{"label": ..., "value": ..., "sub": ...}``.
    """
    cards: list[dict[str, str]] = []
    if "cpu_pct" in df.columns:
        cards.append({
            "label": "Avg CPU",
            "value": f"{_fmt_mean(df['cpu_pct'])} %",
            "sub": f"max {df['cpu_pct'].max():.1f}%",
        })
    if "pss_kb" in df.columns:
        cards.append({
            "label": "Avg PSS",
            "value": f"{df['pss_kb'].mean() / 1024:.1f} MB",
            "sub": f"peak {df['pss_kb'].max() / 1024:.1f} MB",
        })
    if "fps" in df.columns and df["fps"].max() > 0:
        sub = ""
        if "p95_ms" in df.columns:
            sub = f"p95 frame {df['p95_ms'].mean():.0f} ms"
        cards.append({"label": "Avg FPS", "value": _fmt_mean(df["fps"]), "sub": sub})
    if "jank_pct" in df.columns:
        cards.append({
            "label": "Avg jank",
            "value": f"{_fmt_mean(df['jank_pct'])} %",
            "sub": f"max {df['jank_pct'].max():.1f}%",
        })
    if "rx_b" in df.columns:
        total_rx_kb = df["rx_b"].sum() / 1024.0
        total_tx_kb = df.get("tx_b", pd.Series([0])).sum() / 1024.0
        cards.append({
            "label": "Network rx/tx",
            "value": f"{fmt_bytes_from_kb(total_rx_kb)} / {fmt_bytes_from_kb(total_tx_kb)}",
            "sub": "summed across session",
        })
    if "battery_level_pct" in df.columns and not df["battery_level_pct"].empty:
        start = df["battery_level_pct"].iloc[0]
        end = df["battery_level_pct"].iloc[-1]
        delta = end - start
        cards.append({
            "label": "Battery",
            "value": f"{end:.0f}%",
            "sub": f"Δ {delta:+.0f}% over session",
        })
    if "thermal_skin_c" in df.columns and not df["thermal_skin_c"].empty:
        cards.append({
            "label": "Skin temp",
            "value": f"{df['thermal_skin_c'].mean():.1f} °C",
            "sub": f"max {df['thermal_skin_c'].max():.1f} °C",
        })
    return cards
