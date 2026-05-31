from __future__ import annotations

from collections import Counter
from typing import Any

from ..models import Finding

_LIBRARY_PREFIXES: list[tuple[str, str]] = [
    ("OkHttp", "OkHttp"),
    ("RxComputationTh", "RxJava Computation"),
    ("RxIoScheduler", "RxJava IO"),
    ("RxNewThreadSche", "RxJava NewThread"),
    ("glide-", "Glide"),
    ("arch_disk_io_", "Room/DataStore"),
    ("pool-", "ThreadPoolExecutor"),
    ("AsyncTask", "AsyncTask"),
    ("Retrofit", "Retrofit"),
    ("firebase-", "Firebase"),
    ("ExoPlayer", "ExoPlayer"),
    ("WorkManager", "WorkManager"),
    ("Kotlin Coroutin", "Kotlin Coroutines"),
]


def _library_for(name: str) -> str:
    for prefix, label in _LIBRARY_PREFIXES:
        if name.startswith(prefix):
            return label
    return name[:20]


def _top_thread_libraries(name_lists: list[list[str]], top_n: int = 3) -> str:
    all_names: list[str] = []
    for nl in name_lists:
        all_names.extend(nl)
    libs = [_library_for(n) for n in all_names]
    counts = Counter(libs).most_common(top_n)
    return ", ".join(f"{lib} ({cnt})" for lib, cnt in counts)


def _activity_thread_events(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find ticks where thread names grew significantly, with activity/fragment context."""
    events: list[dict[str, Any]] = []
    prev_names: set[str] = set()
    for s in samples:
        curr_names = set(s.get("thread_names") or [])
        new_names = curr_names - prev_names
        if len(new_names) >= 2:
            events.append({
                "t": s.get("t", 0),
                "new_names": list(new_names),
                "activity": s.get("activity"),
                "fragment": s.get("fragment"),
            })
        prev_names = curr_names
    return events


def detect(samples: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    findings += _thread_leak(samples)
    findings += _thread_storm(samples)
    return findings


def _thread_leak(samples: list[dict[str, Any]]) -> list[Finding]:
    thread_samples = [(s.get("t", 0), s.get("threads")) for s in samples if s.get("threads") is not None]
    if len(thread_samples) < 3:
        return []

    first_count = thread_samples[0][1]
    last_count = thread_samples[-1][1]
    growth = last_count - first_count
    if growth <= 5:
        return []

    severity = "critical" if growth > 20 else "warning"
    ts = [t for t, _ in thread_samples[:10]]

    # Prefer activity-correlated attribution via thread_names over simple library heuristic
    activity_events = _activity_thread_events(samples)
    attribution = ""
    if activity_events:
        biggest = max(activity_events, key=lambda e: len(e["new_names"]))
        context_parts = []
        if biggest.get("fragment"):
            context_parts.append(biggest["fragment"].split(" / ")[-1])
        elif biggest.get("activity"):
            context_parts.append(biggest["activity"].split(".")[-1])
        context = " → ".join(context_parts) if context_parts else "session"
        lib_counts = Counter(_library_for(n) for n in biggest["new_names"]).most_common(3)
        lib_str = ", ".join(f"{lib} ({cnt})" for lib, cnt in lib_counts)
        attribution = f" +{len(biggest['new_names'])} threads during [{context}]: {lib_str}."
    elif any(s.get("thread_names") for s in samples):
        name_lists = [s["thread_names"] for s in samples if s.get("thread_names")]
        top = _top_thread_libraries(name_lists)
        if top:
            attribution = f" Dominant threads: {top}."

    description = f"Thread count grew {first_count:.0f} → {last_count:.0f} (+{growth:.0f})."
    if attribution:
        description += attribution

    return [Finding(
        severity=severity,
        category="threads",
        title="Thread count growth (possible leak)",
        description=description,
        recommendation="Audit ExecutorService and thread pool creation — ensure they are singletons (not created per-request). Use structured concurrency (coroutineScope, lifecycle-aware scopes). Check that background workers are tied to a lifecycle and cancelled on destroy.",
        timestamps=ts,
        peak_value=float(last_count),
    )]


def _thread_storm(samples: list[dict[str, Any]]) -> list[Finding]:
    high = [s for s in samples if (s.get("threads") or 0) > 100]
    if not high:
        return []

    peak = max(s["threads"] for s in high)
    ts = [s["t"] for s in high[:10] if s.get("t") is not None]

    # Activity context from the first storm sample
    first_storm = high[0]
    context_parts = []
    if first_storm.get("fragment"):
        context_parts.append(first_storm["fragment"].split(" / ")[-1])
    elif first_storm.get("activity"):
        context_parts.append(first_storm["activity"].split(".")[-1])
    context_str = ""
    if context_parts:
        context_str = f" During: [{' → '.join(context_parts)}]."

    name_lists = [s.get("thread_names") for s in high if s.get("thread_names")]
    lib_str = ""
    if name_lists:
        top = _top_thread_libraries(name_lists)
        if top:
            lib_str = f" Top threads: {top}."

    return [Finding(
        severity="warning",
        category="threads",
        title="Thread storm (>100 concurrent threads)",
        description=f"Thread count exceeded 100 (peak: {peak:.0f}).{context_str}{lib_str} Excessive threads increase context-switch overhead and memory footprint.",
        recommendation="Consolidate thread pools. OkHttp and Retrofit share a dispatcher — set a max on OkHttpClient.dispatcher().maxRequestsPerHost. Replace unbounded newCachedThreadPool() with a fixed-size pool or coroutine dispatcher.",
        timestamps=ts,
        peak_value=float(peak),
    )]
