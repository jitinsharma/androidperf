from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["critical", "warning", "info"]
Category = Literal["memory", "cpu", "jank", "network", "threads", "battery"]


@dataclass
class Finding:
    severity: Severity
    category: Category
    title: str
    description: str
    recommendation: str
    timestamps: list[float] = field(default_factory=list)
    peak_value: float | None = None
