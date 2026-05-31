from __future__ import annotations

from typing import Any

from .models import Finding
from .detectors import memory, cpu, jank, threads, network

MIN_SAMPLES = 5
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def analyze(
    samples: list[dict[str, Any]],
    events: list[dict[str, Any]],
    meta: dict[str, Any],
    gc_events: list[dict[str, Any]] | None = None,
) -> list[Finding]:
    if len(samples) < MIN_SAMPLES:
        return []
    gc = gc_events or []
    findings: list[Finding] = (
        memory.detect(samples, events, gc)
        + cpu.detect(samples, events)
        + jank.detect(samples, events)
        + threads.detect(samples, events)
        + network.detect(samples, events)
    )
    findings.sort(key=lambda f: _SEVERITY_ORDER[f.severity])
    return findings
