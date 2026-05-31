"""Tests for the insights engine and procfs parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from androidperf.collectors.procfs import sample as procfs_sample
from androidperf.collectors.logcat import _parse_freed_kb, _extract_reason, _GC_RE
from androidperf.collectors.memory import parse_meminfo_objects
from androidperf.insights import analyze
from androidperf.insights.detectors import memory, cpu, jank, threads, network

FIXTURES = Path(__file__).parent / "fixtures"


# ─────────────────────────────────────────────────────────────────────────────
# procfs parser
# ─────────────────────────────────────────────────────────────────────────────

def _fake_procfs_output() -> str:
    status = (FIXTURES / "proc_status.txt").read_text()
    io = (FIXTURES / "proc_io.txt").read_text()
    return status + "---\n" + io


def test_procfs_threads():
    from androidperf.collectors import procfs
    import re
    out = _fake_procfs_output()
    m = procfs._THREADS_RE.search(out)
    assert m and int(m.group(1)) == 42


def test_procfs_csw():
    from androidperf.collectors import procfs
    out = _fake_procfs_output()
    m = procfs._CSW_VOL_RE.search(out)
    assert m and int(m.group(1)) == 15234
    m = procfs._CSW_NONVOL_RE.search(out)
    assert m and int(m.group(1)) == 3421


def test_procfs_disk():
    from androidperf.collectors import procfs
    out = _fake_procfs_output()
    # Use _IO_RE to verify disk parsing
    for match in procfs._IO_RE.finditer(out.split("---")[1]):
        if match.group(1) == "read_bytes":
            assert int(match.group(2)) == 67108864
        if match.group(1) == "write_bytes":
            assert int(match.group(2)) == 33554432


# ─────────────────────────────────────────────────────────────────────────────
# meminfo objects parser
# ─────────────────────────────────────────────────────────────────────────────

_OBJECTS_BLOCK = """
 Objects
               Views:      312         ViewRootImpl:        1
         AppContexts:        4           Activities:        3
              Assets:        4        AssetManagers:        4
"""

def test_parse_meminfo_objects():
    result = parse_meminfo_objects(_OBJECTS_BLOCK)
    assert result["obj_activities"] == 3.0
    assert result["obj_views"] == 312.0
    assert result["obj_app_contexts"] == 4.0


def test_parse_meminfo_objects_empty():
    assert parse_meminfo_objects("no objects here") == {}


# ─────────────────────────────────────────────────────────────────────────────
# logcat GC parser
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line,expected_reason,min_freed_kb", [
    ("I art     : Background sticky concurrent mark sweep GC freed 54321(5 MB) AllocSpace objects, 0(0 B) LOS objects, 40% free, 18MB/31MB, paused 1.234ms total 45.678ms", "Background", 5000),
    ("I art     : Explicit concurrent mark sweep GC freed 12345(2 MB) AllocSpace objects", "Explicit", 2000),
    ("I art     : Alloc concurrent mark sweep GC freed 1024(512 KB) AllocSpace objects", "Alloc", 500),
    ("I art     : NativeAlloc concurrent mark sweep GC freed 100(1 MB) AllocSpace objects", "NativeAlloc", 1000),
])
def test_gc_line_parsing(line, expected_reason, min_freed_kb):
    m = _GC_RE.search(line)
    assert m is not None, f"No match for: {line}"
    reason = _extract_reason(m.group("type"))
    assert reason == expected_reason
    freed = _parse_freed_kb(m.group("freed"))
    assert freed >= min_freed_kb


def test_parse_freed_kb_units():
    assert _parse_freed_kb("5 MB") == pytest.approx(5 * 1024)
    assert _parse_freed_kb("512 KB") == pytest.approx(512)
    assert _parse_freed_kb("2048 B") == pytest.approx(2)


# ─────────────────────────────────────────────────────────────────────────────
# Engine: too few samples → empty
# ─────────────────────────────────────────────────────────────────────────────

def test_analyze_too_few_samples():
    assert analyze(samples=[{"t": 0.0, "cpu_pct": 80}], events=[], meta={}) == []


# ─────────────────────────────────────────────────────────────────────────────
# Engine: all fields absent → no crash, empty findings
# ─────────────────────────────────────────────────────────────────────────────

def test_analyze_bare_samples():
    samples = [{"t": float(i)} for i in range(20)]
    result = analyze(samples=samples, events=[], meta={})
    assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# Severity ordering
# ─────────────────────────────────────────────────────────────────────────────

def test_severity_ordering():
    samples = [
        {"t": float(i), "cpu_pct": 90.0, "threads": 10 + i * 3,
         "rx_b": 100_000.0, "tx_b": 0.0, "fps": 60.0, "jank_pct": 30.0,
         "java_kb": 50_000.0 + i * 3000}
        for i in range(30)
    ]
    findings = analyze(samples=samples, events=[], meta={})
    severities = [f.severity for f in findings]
    order = {"critical": 0, "warning": 1, "info": 2}
    assert severities == sorted(severities, key=lambda s: order[s])


# ─────────────────────────────────────────────────────────────────────────────
# Memory detector: heap growth
# ─────────────────────────────────────────────────────────────────────────────

def test_heap_growth_critical():
    samples = [{"t": float(i), "java_kb": 10_000 + i * 5000} for i in range(20)]
    findings = memory.detect(samples, [], [])
    titles = [f.title for f in findings]
    assert any("heap" in t.lower() for t in titles)
    heap_f = next(f for f in findings if "heap" in f.title.lower())
    assert heap_f.severity in ("critical", "warning")


def test_heap_stable_no_finding():
    samples = [{"t": float(i), "java_kb": 50_000.0 + (i % 3) * 100} for i in range(20)]
    findings = memory.detect(samples, [], [])
    assert not any("heap growth" in f.title.lower() for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# Memory detector: GC pressure from logcat
# ─────────────────────────────────────────────────────────────────────────────

def test_gc_pressure_alloc():
    gc_events = [{"t": float(i), "type": "gc", "reason": "Alloc", "freed_kb": 2048} for i in range(10)]
    samples = [{"t": float(i), "java_kb": 80_000.0} for i in range(20)]
    findings = memory.detect(samples, [], gc_events)
    titles = [f.title for f in findings]
    assert any("alloc" in t.lower() for t in titles)


def test_gc_pressure_explicit():
    gc_events = [{"t": 1.0, "type": "gc", "reason": "Explicit", "freed_kb": 1024}]
    samples = [{"t": float(i), "java_kb": 50_000.0} for i in range(10)]
    findings = memory.detect(samples, [], gc_events)
    assert any("system.gc" in f.title.lower() for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# CPU detector: sustained high CPU
# ─────────────────────────────────────────────────────────────────────────────

def test_sustained_cpu_warning():
    samples = [{"t": float(i), "cpu_pct": 80.0} for i in range(20)]
    findings = cpu.detect(samples, [])
    assert any("cpu" in f.title.lower() for f in findings)


def test_low_cpu_no_finding():
    samples = [{"t": float(i), "cpu_pct": 20.0} for i in range(20)]
    findings = cpu.detect(samples, [])
    assert not any("sustained" in f.title.lower() for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# Jank detector: chronic jank
# ─────────────────────────────────────────────────────────────────────────────

def test_chronic_jank():
    samples = [{"t": float(i), "fps": 45.0, "jank_pct": 30.0} for i in range(20)]
    findings = jank.detect(samples, [])
    assert any("jank" in f.title.lower() for f in findings)


def test_no_jank_no_finding():
    samples = [{"t": float(i), "fps": 60.0, "jank_pct": 2.0} for i in range(20)]
    findings = jank.detect(samples, [])
    assert not findings


# ─────────────────────────────────────────────────────────────────────────────
# Thread detector: thread leak with names
# ─────────────────────────────────────────────────────────────────────────────

def test_thread_leak():
    samples = [{"t": float(i), "threads": 10 + i * 2} for i in range(20)]
    findings = threads.detect(samples, [])
    assert any("thread" in f.title.lower() for f in findings)


def test_thread_leak_with_names():
    samples = [
        {"t": float(i), "threads": 10 + i * 3,
         "thread_names": ["OkHttp Dispatch", "OkHttp Dispatch", "RxComputationTh"]}
        for i in range(20)
    ]
    findings = threads.detect(samples, [])
    assert findings
    f = findings[0]
    assert "OkHttp" in f.description


def test_no_thread_growth_no_finding():
    samples = [{"t": float(i), "threads": 20.0} for i in range(20)]
    findings = threads.detect(samples, [])
    assert not any("leak" in f.title.lower() for f in findings)


# ─────────────────────────────────────────────────────────────────────────────
# Network detector
# ─────────────────────────────────────────────────────────────────────────────

def test_chatty_network():
    samples = [{"t": float(i), "rx_b": 50_000.0, "tx_b": 0.0} for i in range(20)]
    findings = network.detect(samples, [])
    assert any("chatty" in f.title.lower() for f in findings)


def test_large_payload():
    samples = [{"t": 0.0, "rx_b": 2_000_000.0, "tx_b": 0.0}] + \
              [{"t": float(i), "rx_b": 100.0, "tx_b": 0.0} for i in range(1, 20)]
    findings = network.detect(samples, [])
    assert any("large" in f.title.lower() for f in findings)


def test_quiet_network_no_finding():
    samples = [{"t": float(i), "rx_b": 500.0, "tx_b": 100.0} for i in range(20)]
    findings = network.detect(samples, [])
    assert not findings
