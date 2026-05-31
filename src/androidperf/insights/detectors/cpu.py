from __future__ import annotations

from typing import Any

from ..models import Finding


def detect(samples: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    findings += _sustained_high_cpu(samples)
    findings += _thermal_throttle_jank(samples)
    findings += _network_driven_cpu(samples)
    return findings


def _sustained_high_cpu(samples: list[dict[str, Any]]) -> list[Finding]:
    cpu_samples = [s for s in samples if s.get("cpu_pct") is not None]
    if not cpu_samples:
        return []
    high = [s for s in cpu_samples if s["cpu_pct"] > 70]
    fraction = len(high) / len(cpu_samples)
    if fraction <= 0.30:
        return []
    severity = "critical" if fraction > 0.60 else "warning"
    mean_cpu = sum(s["cpu_pct"] for s in cpu_samples) / len(cpu_samples)
    ts = [s["t"] for s in high[:10] if s.get("t") is not None]
    return [Finding(
        severity=severity,
        category="cpu",
        title="Sustained high CPU usage",
        description=f"CPU exceeded 70% in {fraction * 100:.0f}% of samples (mean {mean_cpu:.0f}%). Sustained load drains battery and can cause thermal throttling.",
        recommendation="Profile with Android Studio CPU Profiler (method trace or sampled). Look for work on the main thread that should be offloaded: JSON parsing, image decoding, database queries. Use Dispatchers.Default/IO in coroutines.",
        timestamps=ts,
        peak_value=max(s["cpu_pct"] for s in high),
    )]


def _thermal_throttle_jank(samples: list[dict[str, Any]]) -> list[Finding]:
    hits = [
        s for s in samples
        if (s.get("thermal_status") or 0) >= 2 and (s.get("jank_pct") or 0) > 15
    ]
    if not hits:
        return []
    severity = "critical" if len(hits) >= 5 else "warning"
    ts = [s["t"] for s in hits[:10] if s.get("t") is not None]
    peak_status = max(s.get("thermal_status", 0) for s in hits)
    return [Finding(
        severity=severity,
        category="cpu",
        title="Thermal throttling causing jank",
        description=f"Device was thermally throttled (status ≥2) during {len(hits)} jank-heavy ticks. The CPU/GPU clock was reduced by the system to cool down, causing frame drops.",
        recommendation="Reduce sustained CPU/GPU load: lower animation complexity, batch background work, avoid continuous sensor polling. Test on a cool device to distinguish thermal from algorithmic performance issues.",
        timestamps=ts,
        peak_value=float(peak_status),
    )]


def _network_driven_cpu(samples: list[dict[str, Any]]) -> list[Finding]:
    have_rx = any(s.get("rx_b") is not None for s in samples)
    have_cpu = any(s.get("cpu_pct") is not None for s in samples)
    if not have_rx or not have_cpu:
        return []

    cpu_vals = [s.get("cpu_pct", 0) or 0 for s in samples]
    mean_cpu = sum(cpu_vals) / len(cpu_vals) if cpu_vals else 0.0
    threshold = 1.5 * mean_cpu

    correlated: list[float] = []
    for i, s in enumerate(samples):
        if (s.get("rx_b") or 0) < 50_000:
            continue
        # Check cpu_pct in next 2 ticks
        for j in range(i + 1, min(i + 3, len(samples))):
            if (samples[j].get("cpu_pct") or 0) > threshold:
                if s.get("t") is not None:
                    correlated.append(s["t"])
                break

    if len(correlated) < 3:
        return []
    return [Finding(
        severity="warning",
        category="cpu",
        title="Network I/O driving CPU spikes",
        description=f"Large network reads (>50 KB) were followed by CPU spikes (>{threshold:.0f}%) in {len(correlated)} cases. Parsing or processing responses on the main thread is likely.",
        recommendation="Ensure network response parsing runs on Dispatchers.IO, not Dispatchers.Main. Use streaming parsers (Moshi streaming, OkHttp ResponseBody.source()) for large payloads instead of reading the full body into memory.",
        timestamps=correlated[:10],
        peak_value=float(len(correlated)),
    )]
