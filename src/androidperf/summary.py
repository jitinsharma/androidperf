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
    # FPS / jank / frame-time averages only make sense over samples where
    # the app actually rendered. Idle ticks report fps=0 (no frames drawn),
    # and including them drags the mean toward zero — a 60 fps app looks
    # like 3 fps if it sat idle 95% of the session.
    # Smoothness metrics: jank % and p95 frame time, computed only over
    # ticks where the app actually rendered (idle ticks have no frames so
    # the device reports nonsense values for those windows).
    if "fps" in df.columns and df["fps"].max() > 0:
        active = df[df["fps"] > 0]
        if "jank_pct" in active.columns:
            cards.append({
                "label": "Avg jank",
                "value": f"{_fmt_mean(active['jank_pct'])} %",
                "sub": f"max {active['jank_pct'].max():.1f}%",
            })
        if "p95_ms" in active.columns and not active["p95_ms"].dropna().empty:
            cards.append({
                "label": "Frame time",
                "value": f"{active['p95_ms'].mean():.0f} ms",
                "sub": "p95, while rendering",
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
