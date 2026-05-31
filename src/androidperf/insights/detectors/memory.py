from __future__ import annotations

from typing import Any

from ..models import Finding


def _linreg_slope(xs: list[float], ys: list[float]) -> float:
    """Least-squares slope without numpy."""
    n = len(xs)
    if n < 2:
        return 0.0
    sx = sum(xs)
    sy = sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys))
    sxx = sum(x * x for x in xs)
    denom = n * sxx - sx * sx
    return (n * sxy - sx * sy) / denom if denom else 0.0


def detect(samples: list[dict[str, Any]], events: list[dict[str, Any]], gc_events: list[dict[str, Any]] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    findings += _heap_growth(samples)
    findings += _gc_correlated_jank(samples, gc_events or [])
    findings += _gc_pressure(gc_events or [])
    findings += _activity_leak(samples, events)
    findings += _view_count(samples)
    return findings


def _heap_growth(samples: list[dict[str, Any]]) -> list[Finding]:
    pts = [(s["t"], s["java_kb"]) for s in samples if s.get("java_kb") is not None and s.get("t") is not None]
    if len(pts) < 5:
        return []
    ts, ys = zip(*pts)
    slope = _linreg_slope(list(ts), list(ys))
    if slope < 500:
        return []
    y0_mb = ys[0] / 1024
    yn_mb = ys[-1] / 1024
    severity = "critical" if slope > 2000 else "warning"
    return [Finding(
        severity=severity,
        category="memory",
        title="Rapid Java heap growth",
        description=f"Java heap grew from {y0_mb:.0f} MB → {yn_mb:.0f} MB at {slope:.0f} KB/s over the session.",
        recommendation="Capture a heap dump in Android Studio (Memory Profiler → Dump Java Heap) and look for retained object chains. Common causes: static collections holding Activity/Context refs, unclosed streams, or unbounded caches.",
        timestamps=[pts[0][0], pts[-1][0]],
        peak_value=max(ys),
    )]


def _gc_correlated_jank(samples: list[dict[str, Any]], gc_events: list[dict[str, Any]]) -> list[Finding]:
    if not samples:
        return []

    have_jank = any(s.get("jank_pct") is not None for s in samples)
    if not have_jank:
        return []

    use_logcat = bool(gc_events)
    correlations: list[tuple[float, str, float]] = []  # (t, reason, jank_pct)

    if use_logcat:
        gc_times = [(e["t"], e.get("reason", "Unknown")) for e in gc_events if e.get("t") is not None]
        interval = samples[1]["t"] - samples[0]["t"] if len(samples) > 1 else 1.0
        window = max(2 * interval, 2.0)
        for gc_t, reason in gc_times:
            nearby = [s for s in samples if abs(s.get("t", -999) - gc_t) <= window]
            for s in nearby:
                jp = s.get("jank_pct", 0) or 0
                if jp > 20:
                    correlations.append((gc_t, reason, jp))
                    break
    else:
        # Heuristic: ≥10% java_kb drop = likely GC
        java = [(i, s.get("t", 0), s.get("java_kb"), s.get("jank_pct", 0) or 0)
                for i, s in enumerate(samples) if s.get("java_kb") is not None]
        for idx, (i, t, kb, _) in enumerate(java):
            if i < 5:
                continue
            window_vals = [v for _, _, v, _ in java[max(0, idx - 5):idx] if v is not None]
            if not window_vals:
                continue
            recent_max = max(window_vals)
            if kb < 0.9 * recent_max:
                nearby = java[max(0, idx - 2):min(len(java), idx + 3)]
                for _, nt, _, jp in nearby:
                    if jp > 20:
                        correlations.append((t, "Inferred", jp))
                        break

    if len(correlations) < 3:
        return []

    ts = sorted({c[0] for c in correlations})[:10]
    reasons = {c[1] for c in correlations}
    reason_str = ", ".join(sorted(reasons - {"Inferred"})) or "inferred from heap drops"
    peak_jank = max(c[2] for c in correlations)

    return [Finding(
        severity="warning",
        category="memory",
        title="GC pauses correlated with jank",
        description=f"GC events ({reason_str}) coincided with jank >20% at {len(correlations)} points. Peak jank during GC: {peak_jank:.0f}%.",
        recommendation="Reduce short-lived object allocation in hot paths (RecyclerView binders, onDraw, animation callbacks). Use object pools for frequently allocated types. Avoid boxing in loops.",
        timestamps=ts,
        peak_value=peak_jank,
    )]


def _gc_pressure(gc_events: list[dict[str, Any]]) -> list[Finding]:
    if not gc_events:
        return []
    findings: list[Finding] = []

    alloc_gcs = [e for e in gc_events if e.get("reason") == "Alloc"]
    if len(alloc_gcs) > 5:
        freed_kb = sum(e.get("freed_kb", 0) for e in alloc_gcs)
        findings.append(Finding(
            severity="warning",
            category="memory",
            title="Allocation-pressure GCs",
            description=f"{len(alloc_gcs)} Alloc-triggered GCs ran during the session (heap full), freeing {freed_kb // 1024:.0f} MB total. The app is repeatedly exhausting the Java heap.",
            recommendation="Profile allocations with Android Studio Allocation Tracker. Common hot spots: Bitmap.createBitmap() in loops, String concatenation, frequent list copies. Consider increasing heap size only as a last resort.",
            timestamps=[e["t"] for e in alloc_gcs[:10] if e.get("t") is not None],
            peak_value=float(len(alloc_gcs)),
        ))

    explicit_gcs = [e for e in gc_events if e.get("reason") == "Explicit"]
    if explicit_gcs:
        findings.append(Finding(
            severity="info",
            category="memory",
            title="Unnecessary System.gc() calls detected",
            description=f"{len(explicit_gcs)} explicit GC(s) triggered — likely via System.gc() or Runtime.gc(). These are hints ART may ignore and add unnecessary pause risk.",
            recommendation="Remove System.gc() calls. They're vestigial from Dalvik and ART handles GC scheduling better without hints.",
            timestamps=[e["t"] for e in explicit_gcs[:10] if e.get("t") is not None],
        ))

    native_gcs = [e for e in gc_events if e.get("reason") == "NativeAlloc"]
    if len(native_gcs) > 3:
        freed_kb = sum(e.get("freed_kb", 0) for e in native_gcs)
        findings.append(Finding(
            severity="warning",
            category="memory",
            title="Native memory pressure (Bitmap / NIO buffers)",
            description=f"{len(native_gcs)} NativeAlloc-triggered GCs ran, freeing {freed_kb // 1024:.0f} MB. ART is reclaiming Java objects to relieve native heap pressure — typically Bitmaps or direct ByteBuffers.",
            recommendation="Recycle Bitmaps explicitly (bitmap.recycle()) when done. Use inBitmap reuse in BitmapFactory.Options. For direct ByteBuffers, release them promptly rather than relying on GC.",
            timestamps=[e["t"] for e in native_gcs[:10] if e.get("t") is not None],
        ))

    return findings


def _activity_leak(samples: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[Finding]:
    acts = [(s.get("t", 0), s.get("obj_activities")) for s in samples if s.get("obj_activities") is not None]
    if not acts:
        return []
    screen_events = [e for e in events if e.get("type") == "screen"]
    if not screen_events:
        return []

    max_count = max(v for _, v in acts)
    if max_count <= 2:
        return []

    # Check if count monotonically increases after first screen event
    first_screen_t = screen_events[0]["t"]
    after = [(t, v) for t, v in acts if t >= first_screen_t]
    if len(after) < 3:
        return []
    ever_decreases = any(after[i][1] > after[i + 1][1] for i in range(len(after) - 1))
    if ever_decreases:
        return []

    ts = [t for t, _ in after[:10]]
    return [Finding(
        severity="warning",
        category="memory",
        title="Possible Activity leak",
        description=f"Activity object count rose to {max_count:.0f} and never decreased during navigation. Activities are not being destroyed, suggesting a leak.",
        recommendation="Check for: static references to Activity, anonymous inner classes with implicit outer refs (use WeakReference), unregistered BroadcastReceivers/listeners. Use LeakCanary to pinpoint the retention path.",
        timestamps=ts,
        peak_value=max_count,
    )]


def _view_count(samples: list[dict[str, Any]]) -> list[Finding]:
    views = [(s.get("t", 0), s.get("obj_views")) for s in samples if s.get("obj_views") is not None]
    if not views:
        return []
    peak = max(v for _, v in views)
    if peak <= 500:
        return []
    severity = "critical" if peak > 1000 else "warning"
    ts = [t for t, v in views if v >= 500][:10]
    return [Finding(
        severity=severity,
        category="memory",
        title="High view count in hierarchy",
        description=f"View object count peaked at {peak:.0f}. Excessive views increase measure/layout/draw time per frame.",
        recommendation="Flatten your view hierarchy with ConstraintLayout. Use merge tags where possible. For list-heavy screens, migrate to Jetpack Compose LazyColumn which only creates views for visible items.",
        timestamps=ts,
        peak_value=peak,
    )]
