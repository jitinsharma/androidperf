"""End-of-session summary panel for the terminal."""

from __future__ import annotations

from typing import Any

import pandas as pd
from rich.align import Align
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ..summary import build_cards

_CARDS_PER_ROW = 4


def render_summary(samples: list[dict[str, Any]]) -> RenderableType:
    """Render the same summary cards that appear in the HTML report."""
    df = pd.DataFrame(samples)
    cards = build_cards(df)
    if not cards:
        return Panel(Text("(no samples captured)", style="dim"), title="Session summary")

    grid = Table.grid(expand=True, padding=(0, 2))
    for _ in range(_CARDS_PER_ROW):
        grid.add_column(ratio=1)

    # Pack cards into rows of _CARDS_PER_ROW; pad the last row with blanks so
    # column widths stay uniform.
    for start in range(0, len(cards), _CARDS_PER_ROW):
        chunk = cards[start : start + _CARDS_PER_ROW]
        cells: list[RenderableType] = [_card_cell(c) for c in chunk]
        while len(cells) < _CARDS_PER_ROW:
            cells.append(Text(""))
        grid.add_row(*cells)

    return Panel(grid, title="[bold]Session summary[/bold]", border_style="cyan", padding=(1, 1))


def _card_cell(card: dict[str, str]) -> RenderableType:
    return Align.center(
        Group(
            Text(card["label"], style="dim"),
            Text(card["value"], style="bold cyan"),
            Text(card.get("sub", ""), style="dim"),
        )
    )
