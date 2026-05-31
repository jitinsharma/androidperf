from __future__ import annotations

from collections import Counter
from typing import Any

from ..models import Finding

# Known library prefixes in thread names (Android truncates to 15 chars)
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
    if not name_lists:
        return ""
    all_names: list[str] = []
    for nl in name_lists:
        all_names.extend(nl)
    libs = [_library_for(n) for n in all_names]
    counts = Counter(libs).most_common(top_n)
    return ", ".join(f"{lib} ({cnt})" for lib, cnt in counts)


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

    name_lists = [s.get("thread_names") for s in samples if s.get("thread_names")]
    library_str = ""
    if name_lists:
        top = _top_thread_libraries(name_lists)
        if top:
            library_str = f" Dominant threads: {top}."

    return [Finding(
        severity=severity,
        category="threads",
        title="Thread count growth (possible leak)",
        description=f"Thread count grew from {first_count:.0f} → {last_count:.0f} (+{growth:.0f}) over the session.{library_str}",
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

    name_lists = [s.get("thread_names") for s in high if s.get("thread_names")]
    library_str = ""
    if name_lists:
        top = _top_thread_libraries(name_lists)
        if top:
            library_str = f" Top thread sources: {top}."

    return [Finding(
        severity="warning",
        category="threads",
        title="Thread storm (>100 concurrent threads)",
        description=f"Thread count exceeded 100 (peak: {peak:.0f}).{library_str} Excessive threads increase context-switch overhead and memory footprint.",
        recommendation="Consolidate thread pools. OkHttp and Retrofit share a dispatcher — set a max on OkHttpClient.dispatcher().maxRequestsPerHost. Replace unbounded newCachedThreadPool() with a fixed-size pool or coroutine dispatcher.",
        timestamps=ts,
        peak_value=float(peak),
    )]
