from __future__ import annotations

from typing import Any

from ..models import Finding


def detect(samples: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    findings += _chatty_network(samples)
    findings += _large_payloads(samples)
    return findings


def _chatty_network(samples: list[dict[str, Any]]) -> list[Finding]:
    net_samples = [s for s in samples if s.get("rx_b") is not None or s.get("tx_b") is not None]
    if len(net_samples) < 5:
        return []
    active = [s for s in net_samples if (s.get("rx_b") or 0) > 10_000 or (s.get("tx_b") or 0) > 10_000]
    fraction = len(active) / len(net_samples)
    if fraction <= 0.80:
        return []
    total_rx_kb = sum((s.get("rx_b") or 0) for s in active) / 1024
    ts = [s["t"] for s in active[:10] if s.get("t") is not None]
    return [Finding(
        severity="warning",
        category="network",
        title="Chatty network — active in most ticks",
        description=f"Network transfers >10 KB occurred in {fraction * 100:.0f}% of sample ticks, receiving {total_rx_kb:.0f} KB total. Continuous network activity drains battery and keeps the radio awake.",
        recommendation="Batch network requests. Use HTTP/2 multiplexing. Implement request coalescing for repeated similar queries. Check for polling patterns and replace with push notifications or WebSocket where appropriate.",
        timestamps=ts,
        peak_value=fraction,
    )]


def _large_payloads(samples: list[dict[str, Any]]) -> list[Finding]:
    large = [s for s in samples if (s.get("rx_b") or 0) > 1_048_576]
    if not large:
        return []
    peak_kb = max((s.get("rx_b") or 0) for s in large) / 1024
    ts = sorted(s["t"] for s in large if s.get("t") is not None)[:10]
    return [Finding(
        severity="warning",
        category="network",
        title="Large network payload(s) received",
        description=f"{len(large)} sample tick(s) received >1 MB in a single interval. Peak: {peak_kb:.0f} KB. Large payloads increase parse time and memory pressure.",
        recommendation="Use pagination or cursor-based APIs instead of loading all data at once. Enable gzip compression on your server (most OkHttp clients handle this automatically). Use progressive image loading (Glide/Coil with thumbnail()). For JSON, consider Protocol Buffers for 3-5× size reduction.",
        timestamps=ts,
        peak_value=peak_kb,
    )]
