# androidperf

A CLI + web UI that records Android app runtime performance over ADB — CPU,
RAM, network, FPS/jank, battery, thermal, threads, disk I/O, GC events, and
screen transitions — draws a live terminal dashboard while it runs, drops a
self-contained HTML report at the end, and automatically surfaces plain-English
**insights** with fix recommendations.

```
┌── androidperf · com.example.app · Pixel 7 (sdk 34)     samples 42  elapsed 00:41 ──┐
│ screen HomeActivity     battery 87% charging 31°C      thermal moderate skin 35°C  │
├──── CPU ────┬── Memory ─────────┬──── Network ────┬──── FPS ────┐
│             │                   │                 │             │
│   19.2 %    │  Total PSS 199 MB │   ↓ rx 5 KB/s   │  fps  58.2  │
│    ▁▂▄▆█    │  Java       39 MB │   ↑ tx 2 KB/s   │  jank 3.1 % │
│             │  Native     44 MB │                 │  p95    18  │
│ per-process │  Graphics   20 MB │       ▁▂        │     █▆▄▂▁   │
│     CPU     │         ▁▂        │       ▁▂        │             │
└─────────────┴───────────────────┴─────────────────┴─────────────┘
                             Ctrl+C to stop
```

## Requirements

| | |
|---|---|
| **Python** | 3.11 or newer |
| **adb** | Android Platform Tools on `$PATH`. macOS: `brew install --cask android-platform-tools`. Debian/Ubuntu: `sudo apt install adb`. Windows/other: <https://developer.android.com/tools/releases/platform-tools>. Verify with `adb version`. |
| **Android device** | Physical device or emulator running **Android 7.0 (SDK 24) or newer** — `dumpsys gfxinfo framestats` needs SDK 24+. |
| **USB debugging** | Enabled in Developer Options, and the host authorized (`adb devices` shows the serial as `device`, not `unauthorized`). |
| **Python deps** | Installed automatically: `adbutils`, `typer`, `rich`, `jinja2`, `plotly`, `pandas`, `fastapi`, `uvicorn`. |

`adb` is **not** a Python package — it's a standalone binary. If `androidperf devices` says "command not found", that's the missing piece.

## Install

```bash
pipx install androidperf          # recommended — isolated, globally available
# or
pip install androidperf
```

From source:

```bash
git clone https://github.com/jitinsharma/androidperf.git
cd androidperf

# Option A — globally available, isolated venv managed by pipx:
pipx install .
pipx install --editable .          # if you plan to edit the code

# Option B — a regular venv with the dev extras (pytest, ruff):
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quickstart

```bash
# 1. See connected devices
androidperf devices

# 2. Find the package you want to profile
androidperf packages --filter com.android

# 3. Record interactively (prompts for device + package)
androidperf record

# 4. Record a specific package for 60 s at 1 s cadence
androidperf record --package com.android.settings --interval 1 --duration 60

# 5. Launch the web UI (configure + record + view reports in a browser)
androidperf ui
```

If `--package` is omitted, `record` drops into an interactive prompt: type a
substring, pick a number.

Attach to an already-running app instead of launching it:

```bash
androidperf record --package com.example.app --no-launch
```

Stop early with `Ctrl+C` — the JSON and HTML are still written with whatever
samples were collected. After the session, a stat-card panel prints in the
terminal, and the HTML report opens the run directory.

Each run writes to `runs/<timestamp>-<pkg>/`:

- `samples.json` — raw time-series data including GC events
- `report.html` — single self-contained file, opens offline
- `heap_histogram.json` — class instance counts (debug builds / emulators only)
- `heap.hprof` — raw heap dump (kept for re-analysis; delete to save space)

## What gets measured

| Metric | Source |
|---|---|
| CPU % (per-core summed) | `top -n 1 -b -p <pid>` |
| RAM: PSS / Java / Native / Graphics / Code / Stack | `dumpsys meminfo <package>` |
| Object counts: Activities, Views, AppContexts | `dumpsys meminfo <package>` |
| Network rx/tx (per-tick deltas) | `/proc/net/xt_qtaguid/stats` → `dumpsys netstats` on Android 10+ |
| Frame rendering rate, jank %, frame p50/p90/p95/p99 | `dumpsys gfxinfo <package>` |
| Battery: level, temp, voltage, status | `dumpsys battery` |
| Thermal: status, skin/cpu/gpu/battery °C | `dumpsys thermalservice` |
| Thread count (per-process, instantaneous) | `/proc/<pid>/status` |
| Thread names (library attribution) | `/proc/<pid>/task/*/status` |
| Context switches, disk I/O (per-tick deltas) | `/proc/<pid>/io` |
| GC events: reason, freed KB, timestamp | `logcat -s art` (background tail) |
| Activity transitions | `dumpsys activity activities` |
| Active fragment (top-level + deepest visible child) | `dumpsys activity <component>` — AndroidX FragmentManager apps only |
| Heap histogram: class → instance count + shallow bytes | `am dumpheap` + built-in hprof parser (debug builds / emulators only) |

CPU is reported as `top`'s raw `%CPU` — i.e., summed across cores. 200% means
the process is using two cores worth of time.

A note on FPS: Android UI rendering is **demand-driven**. The app submits a
frame only when something on screen changes (animation tick, scroll, view
invalidation), so `frames/sec` goes to zero on a static screen — that's
correct, not a bug. `androidperf` reports `jank %` and `p95 frame time` as
the primary smoothness metrics.

## Insights

After each session, `androidperf` runs an analysis pass over the collected
time-series and surfaces findings with plain-English descriptions. Findings
appear as severity-coloured cards in the HTML report and in the web UI done
panel.

| Category | What's detected |
|---|---|
| **memory** | Rapid Java heap growth (linear regression), GC-correlated jank, allocation-pressure GCs, `Explicit` / `NativeAlloc` GCs, Activity leak (obj count never decreasing), excessive View counts |
| **cpu** | Sustained high CPU (>70% in >30% of ticks), thermal throttle jank, network-driven CPU spikes |
| **jank** | Chronic jank (>10% mean over rendering ticks), disk-write jank correlation |
| **threads** | Thread count growth / leak (with activity + library attribution), thread storm >100 concurrent |
| **network** | Chatty network (>80% ticks with traffic), large payload spikes (>1 MB) |

Thread findings include library attribution from thread names — e.g.
"+12 threads during [NetworkFragment]: OkHttp (8), arch_disk_io (4)" — by
diffing the `/proc` thread-name set between consecutive samples.

## Heap histogram

For **debug builds and emulators**, `androidperf` automatically captures a
heap dump at the end of each session using `am dumpheap`, parses the hprof
binary with a built-in minimal parser (no external dependencies), and includes
a searchable class histogram in the HTML report. App-package classes are
highlighted.

The histogram shows instance counts and shallow bytes at the moment the
session ends. It is most useful when an Activity-leak finding fires — it tells
you *which* Activity subclasses are over-retained.

On **release builds** and stock Android 10+ devices, `am dumpheap` requires
the app to be debuggable; the heap step is silently skipped and the rest of the
report is unaffected.

## HTML report

The report opens with a compact **screen-timeline swim-lane** (Activity + Fragment rows) showing coloured segments across the session. Below that:

- **Insights** — severity-coloured finding cards (critical / warning / info).
- **Heap histogram** — searchable table of classes by instance count (when available).
- **Metric charts** — interactive Plotly charts with unified hover showing the active activity + fragment at each timestamp.
- **Summary stat cards** — averages, peaks, network totals, battery delta.

No network required to view the report; `plotly.js` is inlined.

## Web UI

```bash
androidperf ui          # default port 8421
androidperf ui --port 9000
```

Opens a browser at `http://localhost:8421`. Select a device and package,
configure the recording, press Start, watch metrics update in real time, then
press Stop. The done panel shows top findings and links to the full HTML report.
Past runs are listed in the sidebar.

REST + WebSocket API:

| Endpoint | Description |
|---|---|
| `GET /api/devices` | List connected ADB devices |
| `GET /api/packages?serial=S&filter=F` | List installed packages |
| `GET /api/runs` | List past runs |
| `GET /api/runs/{id}/report` | Serve the pre-generated HTML report |
| `GET /api/runs/{id}/samples` | Raw samples.json payload |
| `GET /api/runs/{id}/insights` | Insights findings as JSON |
| `GET /api/runs/{id}/heap` | Heap histogram as JSON |
| `WS  /ws/record` | Start / stream / stop a live recording |

## Commands

| Command | Purpose |
|---|---|
| `androidperf devices` | List connected ADB devices. |
| `androidperf packages [--filter X] [--serial S] [--limit N]` | List installed packages. |
| `androidperf record --package PKG [...]` | Record a session; write JSON + HTML. |
| `androidperf report SAMPLES_JSON` | Regenerate HTML from an existing run. |
| `androidperf ui [--port N] [--runs-dir DIR]` | Launch the web UI. |
| `androidperf version` | Print installed version. |

## Development

```bash
.venv/bin/pytest -q     # 60 parser + insight + report tests (no device required)
ruff check src          # lint
```

Parsers live under `src/androidperf/collectors/` and are tested against
captured command output in `tests/fixtures/`. When you hit a device whose
output differs, add a fixture file + an assertion and the parser can be
updated without any device plugged in.

## Layout

```
src/androidperf/
├── cli.py                  # Typer entry point
├── device.py               # adb detection, package list, app launch
├── session.py              # polling loop, signal handling, JSON + hprof writer
├── summary.py              # shared summary-card computation (HTML + terminal)
├── collectors/
│   ├── cpu.py  memory.py  network.py  fps.py
│   ├── battery.py  thermal.py  activity.py  fragments.py
│   ├── procfs.py           # thread count, context switches, disk I/O, thread names
│   ├── logcat.py           # background ART GC event tail
│   ├── heapdump.py         # am dumpheap capture + adb pull
│   └── hprof_parse.py      # minimal hprof binary parser → class histogram
├── insights/
│   ├── engine.py           # analyze() entry point
│   ├── models.py           # Finding dataclass
│   └── detectors/          # memory, cpu, jank, threads, network
├── ui/                     # live.py (Live dashboard) + summary.py (end-of-run panel)
├── report/                 # Jinja2 + Plotly → self-contained HTML
└── server/                 # FastAPI app, WebSocket streaming, static web UI
```

See [`plan/`](./plan) for architecture and future-direction docs.

## License

MIT.
