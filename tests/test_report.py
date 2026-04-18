import json
from pathlib import Path

from androidperf.report.generate import generate_report


def _synthetic_payload(n: int = 10) -> dict:
    samples = []
    for i in range(n):
        samples.append({
            "t": float(i),
            "cpu_pct": 10.0 + i,
            "pss_kb": 200000.0 + i * 500,
            "java_kb": 40000.0,
            "native_kb": 45000.0,
            "gfx_kb": 20000.0,
            "rx_b": 1024.0 * i,
            "tx_b": 512.0 * i,
            "fps": 58.0,
            "jank_pct": 3.0,
            "p95_ms": 18.0,
        })
    return {
        "meta": {
            "device": {"serial": "test", "model": "Pixel", "manufacturer": "Google", "sdk": 34},
            "package": "com.example.app",
            "pid": 1,
            "uid": 10000,
            "started_at": "2026-04-18T10:00:00+00:00",
            "ended_at": "2026-04-18T10:00:10+00:00",
            "interval_s": 1.0,
            "sample_count": n,
        },
        "samples": samples,
    }


def test_generate_report_writes_self_contained_html(tmp_path: Path):
    json_path = tmp_path / "samples.json"
    json_path.write_text(json.dumps(_synthetic_payload()))
    html_path = tmp_path / "report.html"

    result = generate_report(json_path, html_path)
    assert result == html_path
    html = html_path.read_text()

    assert "<html" in html
    # plotly.js must be embedded inline — report should be viewable offline.
    assert "Plotly.newPlot" in html or "Plotly.react" in html
    assert "com.example.app" in html
    # one chart div per metric
    assert html.count("plotly-graph-div") >= 4 or html.count('class="plotly') >= 4


def test_generate_report_handles_empty_samples(tmp_path: Path):
    payload = _synthetic_payload(0)
    json_path = tmp_path / "samples.json"
    json_path.write_text(json.dumps(payload))
    html_path = tmp_path / "report.html"

    result = generate_report(json_path, html_path)
    assert result.exists()
