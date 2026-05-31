from __future__ import annotations

from typing import Any

from ..models import Finding


def detect(samples: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    findings += _chronic_jank(samples)
    findings += _disk_io_jank(samples)
    return findings


def _chronic_jank(samples: list[dict[str, Any]]) -> list[Finding]:
    # Only count ticks where the app was rendering (fps > 0)
    rendering = [s for s in samples if (s.get("fps") or 0) > 0 and s.get("jank_pct") is not None]
    if len(rendering) < 5:
        return []
    mean_jank = sum(s["jank_pct"] for s in rendering) / len(rendering)
    if mean_jank <= 10:
        return []
    severity = "critical" if mean_jank > 25 else "warning"
    bad = [s for s in rendering if s["jank_pct"] > 16]
    ts = [s["t"] for s in bad[:10] if s.get("t") is not None]
    return [Finding(
        severity=severity,
        category="jank",
        title="Chronic frame jank",
        description=f"Mean jank rate was {mean_jank:.0f}% across rendering ticks ({len(rendering)} ticks with FPS>0). {len(bad)} ticks exceeded 16ms/frame threshold.",
        recommendation="Use Android Studio System Trace (Perfetto) to identify which stage of the frame pipeline is slow: measure, layout, draw, or sync. Common culprits: overdraw, complex RecyclerView items, inflate on scroll.",
        timestamps=ts,
        peak_value=max(s["jank_pct"] for s in rendering),
    )]


def _disk_io_jank(samples: list[dict[str, Any]]) -> list[Finding]:
    have_disk = any(s.get("disk_write_b") is not None for s in samples)
    have_jank = any(s.get("jank_pct") is not None for s in samples)
    if not have_disk or not have_jank:
        return []

    hits: list[float] = []
    for i, s in enumerate(samples):
        if (s.get("disk_write_b") or 0) < 1_048_576:
            continue
        # Check jank in ±1 tick
        window = samples[max(0, i - 1):min(len(samples), i + 2)]
        if any((w.get("jank_pct") or 0) > 20 for w in window):
            if s.get("t") is not None:
                hits.append(s["t"])

    if not hits:
        return []
    return [Finding(
        severity="warning",
        category="jank",
        title="Disk writes coinciding with jank",
        description=f"Disk writes >1 MB occurred alongside jank >20% at {len(hits)} point(s). Writing large data synchronously blocks the frame pipeline.",
        recommendation="Enable StrictMode in debug builds to catch disk I/O on the main thread. Move large writes to Dispatchers.IO. Consider DataStore (Proto or Preferences) instead of SharedPreferences for async writes.",
        timestamps=hits[:10],
        peak_value=float(len(hits)),
    )]
