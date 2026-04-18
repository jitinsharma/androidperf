# Architecture — current state

A local developer tool that runs on the host (laptop/desktop), talks to a
single connected Android device over ADB, records runtime metrics over a
session, and produces two artifacts: a raw `samples.json` and a self-contained
`report.html`. Single-engineer workflow. No backend, no device farm, no
build-tool integration.

---

## Tech stack (what's actually used)

| Concern | Choice | Notes |
|---|---|---|
| Language | **Python 3.11+** | Type hints everywhere; `from __future__ import annotations`. |
| ADB library | **adbutils** | Pure-Python; `adbutils.Device.shell` for every on-device command. |
| CLI framework | **Typer** | Typed options; auto `--help`. |
| Terminal UI | **Rich** (`rich.live.Live` + `Group`) | Live panel during a session, summary panel after. `rich.layout.Layout` was tried first but it force-fills the terminal height, so the UI now uses `Group` + `Table.grid` with fixed-height panels. |
| HTML report | **Jinja2 + Plotly** | `plotly.graph_objects.Figure.to_html(include_plotlyjs="inline")` bundles the JS into the first chart; the rest reuse the loaded global. Result: one standalone file, opens offline. |
| Packaging | **hatchling** | `pyproject.toml`, src layout. |
| Lint | **ruff** | Single tool for lint + import sorting. |
| Tests | **pytest** + fixtures | Parser tests run against captured `dumpsys` strings — no device needed. |

Explicitly rejected: Textual (too heavy), Chart.js (weaker zoom/pan than
Plotly), Node.js (worse ADB library options), vendored `plotly.min.js`
(plotly's `include_plotlyjs="inline"` makes it unnecessary).

---

## Metric collection

All commands go through `adbutils.Device.shell(...)`. The target app's `pid`
and `uid` are resolved once (either via `launch_app` or attach), then reused
throughout the session.

| Metric | Command | Parser | Sample keys |
|---|---|---|---|
| CPU | `top -n 1 -b -p <pid>` | `collectors/cpu.py` — locates `%CPU` column (toybox glues `S[%CPU]`; we split it). | `cpu_pct` (per-core summed; 200% = 2 cores saturated). |
| Memory | `dumpsys meminfo <pkg>` | `collectors/memory.py` — parses "App Summary" rows. | `pss_kb`, `java_kb`, `native_kb`, `gfx_kb`, `code_kb`, `stack_kb` |
| Network | `cat /proc/net/xt_qtaguid/stats` (or `dumpsys netstats detail --uid=<uid>` on Android 10+) | `collectors/network.py` — sums rx/tx for the target UID (tag=0x0, non-loopback). Session stores *deltas* per tick. | `rx_b`, `tx_b` (per-tick), plus `rx_total_b`/`tx_total_b` internally |
| FPS / jank | `dumpsys gfxinfo <pkg>` + reset between samples | `collectors/fps.py` | `fps`, `jank_frames`, `jank_pct`, `p50_ms`, `p90_ms`, `p95_ms`, `p99_ms`, `frames_total` |
| Battery | `dumpsys battery` | `collectors/battery.py` — scales deci-deg→°C, mV→V. | `battery_level_pct`, `battery_temp_c`, `battery_voltage_v`, `battery_status` |
| Thermal | `dumpsys thermalservice` | `collectors/thermal.py` — regex extracts per-sensor `Temperature{…}` lines. | `thermal_status`, `thermal_skin_c`, `thermal_cpu_c`, `thermal_gpu_c`, `thermal_battery_c`, `thermal_usb_c` |
| Foreground activity | `dumpsys activity activities` | `collectors/activity.py` — finds `mResumedActivity` for target package. | `activity` (full name); transitions also go into `events[]`. |

**Per-collector error isolation.** Each collector call in `session.py:run_session`
is wrapped so a single failing metric records `_<name>_error` on that sample
and the loop keeps running. A misbehaving parser never aborts a recording.

**Pre-flight:** `sdk >= 24` is required for framestats. `fps.reset()` fires
once before the first sample to clear baseline.

---

## Source layout

```
src/androidperf/
├── __init__.py           # __version__
├── cli.py                # Typer entry point: devices, packages, record, report
├── device.py             # pick_device, list_packages, launch_app, pid/uid helpers
├── session.py            # polling loop, SIGINT handling, events[], atomic JSON writer
├── summary.py            # build_cards(df) — shared by HTML + terminal summary
├── collectors/
│   ├── activity.py       # + class_short_name helper for UI labels
│   ├── battery.py
│   ├── cpu.py
│   ├── fps.py
│   ├── memory.py
│   ├── network.py
│   └── thermal.py
├── ui/
│   ├── live.py           # Live dashboard (header + status row + 4 metric panels + footer)
│   └── summary.py        # End-of-session stat-card panel
└── report/
    ├── generate.py       # samples.json → plotly figures → Jinja2 render
    └── template.html.j2  # dark-theme HTML shell (header, cards, timeline, charts)

tests/
├── fixtures/             # top.txt, meminfo.txt, xt_qtaguid.txt, netstats.txt, gfxinfo.txt,
│                         # battery.txt, thermal.txt, activities.txt
├── test_parsers.py       # 9 tests: cpu, memory, network, fps parsers
├── test_extras.py        # 12 tests: battery, thermal, activity, summary helpers
└── test_report.py        # 2 tests: end-to-end samples.json → report.html

plan/
├── 01-architecture.md    # this file
├── 02-future-metrics.md  # candidate metrics not yet implemented
└── 03-automation-integrations.md  # Maestro / MCP / agent integration design
```

---

## Data flow

```
cli.record
    │
    ▼
pick_device ─► launch_app (or attach) ─► resolve pid, uid
    │
    ▼
run_session (src/androidperf/session.py)
    │
    ├── every `interval` seconds (monotonic-anchored, no drift):
    │     each collector.sample(...) → dict merged into the sample
    │     check current activity; if changed, append to events[]
    │     Live dashboard updates (sparklines + current values)
    │
    ├── on stop (SIGINT, --duration elapsed):
    │     restore SIGINT handler
    │     print end-of-session summary panel (shared `summary.build_cards`)
    │     write samples.json atomically (tmp + replace)
    │     call report.generate → report.html
    │
    └── return run_dir
```

---

## samples.json shape (current)

```json
{
  "meta": {
    "device": {"serial":"...", "model":"...", "manufacturer":"...", "sdk": 34},
    "package": "com.example.app",
    "pid": 12345,
    "uid": 10234,
    "started_at": "2026-04-18T10:00:00+00:00",
    "ended_at":   "2026-04-18T10:05:00+00:00",
    "interval_s": 1.0,
    "sample_count": 300,
    "event_count":   7
  },
  "samples": [
    {
      "t": 0.000,
      "cpu_pct": 12.3,
      "pss_kb": 204800, "java_kb": 40960, "native_kb": 81920, "gfx_kb": 20480,
      "rx_b": 1024, "tx_b": 512,
      "fps": 58.2, "jank_pct": 3.1, "p95_ms": 18.4,
      "battery_level_pct": 87, "battery_temp_c": 31.0, "battery_status": 2,
      "thermal_status": 2, "thermal_skin_c": 35.2, "thermal_cpu_c": 42.0,
      "activity": "com.example.app/.HomeActivity"
    }
  ],
  "events": [
    {"t": 0.006, "type": "screen",
     "name": "com.example.app/.HomeActivity",
     "short_name": "HomeActivity"}
  ]
}
```

- `cpu_pct` is per-core-summed raw `top` output; on an 8-core device it can
  range 0–800%. Intentional — matches how Android Studio profiler reports it.
- Events are currently only `type: "screen"`, but the shape is generic so
  future additions (crashes, ANRs, flow steps) fit the same array.

---

## Report (`report.html`)

Single self-contained dark-themed HTML. Top down:

1. **Header** — device, serial, SDK, pid/uid, started/ended, interval, sample count.
2. **Summary cards** — computed by `summary.build_cards`: Avg CPU + max, Avg
   PSS + peak, Avg FPS + p95 frame, Avg jank + max, Network rx/tx totals
   (auto-scaled B→KB→MB→GB), Battery start→end delta, Skin temp mean + max.
3. **Screen timeline** — flat list of every screen transition with elapsed time.
4. **Charts** (Plotly, interactive) — CPU, Memory (4 traces), Network (rx/tx),
   FPS (+ jank on y2), Battery (level + temp), Thermal (skin/cpu/gpu + status
   on y2). Every chart has dashed vertical markers + labels at each screen
   transition, packed into up to 3 rows to avoid horizontal overlap.
5. Legend lives **below** each plot; event labels live **above** it so the two
   don't collide.

---

## Terminal dashboard (`androidperf record`)

```
┌── androidperf · com.example.app · Pixel 7 (sdk 34)     samples 42   elapsed 00:41 ──┐
│ screen HomeActivity        battery 87% charging 31°C   thermal moderate skin 35°C   │
├──── CPU ────┬── Memory ──┬── Network ──┬──── FPS ────┐
│   12.5 %    │ Total PSS… │  ↓ rx …     │  fps  58.2  │
│     ▁▂▄     │     ▁▂     │  ↑ tx …     │  jank  3.1  │
│             │            │     ▁▂      │     ▂▄▁     │
└─────────────┴────────────┴─────────────┴─────────────┘
                     Ctrl+C to stop
```

Bottom-of-session: a stat-card panel rendering the same cards as the HTML
summary. Uses `ui.summary.render_summary`, which calls the shared
`summary.build_cards`, so KB→MB scaling etc. is identical in both surfaces.

---

## Verification

End-to-end (needs a device):

```bash
androidperf devices
androidperf packages --filter com.android
androidperf record --package com.android.settings --duration 15
# expect:
#   - Live panel updates ~15 times
#   - ./runs/<timestamp>-com_android_settings/ exists
#     - samples.json with 15 samples + events
#     - report.html opens offline, shows 6 charts
#   - Stat-card panel prints after the Live dashboard closes
```

Ctrl+C mid-session: clean exit, JSON + HTML still written with whatever samples
were collected.

Automated:

```bash
pytest -q   # 22 parser + report tests, no device required
```

Success criteria:
- All metric families render in both surfaces (live + HTML) when supported.
- `androidperf report samples.json` re-renders HTML from an existing run.
- HTML opens offline (no network fetch for plotly.js).
- Pre-existing samples.json files (from older runs without events/battery/etc.)
  still render — fields are all optional.
