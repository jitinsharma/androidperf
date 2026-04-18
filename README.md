# androidperf

A CLI that records Android app runtime performance over ADB — CPU, RAM,
network, FPS/jank, battery, thermal, and screen transitions — draws a live
terminal dashboard while it runs, and drops a self-contained HTML report at
the end.

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
| **Python deps** | Installed automatically: `adbutils`, `typer`, `rich`, `jinja2`, `plotly`, `pandas`. |

`adb` is **not** a Python package — it's a standalone binary. If `androidperf devices` says "command not found", that's the missing piece.

## Install

```bash
pipx install androidperf          # recommended — isolated, globally available
# or
pip install androidperf           # into the current environment
```

From source (until the package is on PyPI, or if you want to hack on it):

```bash
git clone <repo>
cd android-performance

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

# 3. Launch the app and record for 60s at 1s cadence
androidperf record --package com.android.settings --interval 1 --duration 60

# 4. (Optional) regenerate the HTML from a prior run
androidperf report runs/20260418-100000-com_android_settings/samples.json
```

If `--package` is omitted, `record` drops into an interactive prompt: type a
substring, pick a number.

Attach to an already-running app instead of launching it:

```bash
androidperf record --package com.example.app --no-launch
```

Stop early with `Ctrl+C` — the JSON and HTML are still written with whatever
samples were collected. After the session ends, a stat-card panel prints in
the terminal with the same summary numbers that appear in the HTML report.

Each run writes to `runs/<timestamp>-<pkg>/`:

- `samples.json` — raw time-series data, easy to diff or re-plot
- `report.html` — single self-contained file, opens offline

## What gets measured

| Metric | Source |
|---|---|
| CPU % (per-core summed) | `top -n 1 -b -p <pid>` |
| RAM: PSS / Java / Native / Graphics / Code / Stack | `dumpsys meminfo <package>` |
| Network rx/tx (per-tick deltas) | `/proc/net/xt_qtaguid/stats` (falls back to `dumpsys netstats --uid=<uid>` on Android 10+) |
| FPS, jank %, frame p50/p90/p95/p99 | `dumpsys gfxinfo <package>` (reset between ticks) |
| Battery: level, temp, voltage, status | `dumpsys battery` |
| Thermal: status, skin/cpu/gpu/battery °C | `dumpsys thermalservice` |
| Screen transitions (as timeline events) | `dumpsys activity activities` |

CPU is reported as `top`'s raw `%CPU` — i.e., summed across cores. 200% means
the process is using two cores worth of time. This is the same convention
used by Android Studio's CPU profiler.

## HTML report

Each chart gets vertical dashed markers for every screen transition, so jank
spikes line up with user navigation. The report also includes:

- Summary stat cards (averages, peaks, network totals, battery delta).
- A flat "Screen timeline" list of every transition with elapsed time.
- Interactive Plotly charts — zoom, pan, hover tooltips, toggle traces in the
  legend. No network required to view the report; `plotly.js` is inlined.

## Commands

| Command | Purpose |
|---|---|
| `androidperf devices` | List connected ADB devices. |
| `androidperf packages [--filter X] [--serial S] [--limit N]` | List installed packages. |
| `androidperf record --package PKG [...]` | Record a session; write JSON + HTML. |
| `androidperf report SAMPLES_JSON` | Regenerate HTML from an existing run. |
| `androidperf version` | Print installed version. |

## Development

```bash
pytest -q         # parser + report tests (no device required)
ruff check src    # lint
```

Parsers live under `src/androidperf/collectors/` and are tested against
captured command output in `tests/fixtures/`. When you hit a device whose
output differs, add a fixture file + an assertion and the parser can be
updated against it without any device plugged in.

## Layout

```
src/androidperf/
├── cli.py                # Typer entry point
├── device.py             # adb detection, package list, app launch
├── session.py            # polling loop, signal handling, JSON writer
├── summary.py            # shared summary-card computation (HTML + terminal)
├── collectors/           # cpu, memory, network, fps, battery, thermal, activity
├── ui/                   # live.py (Live dashboard) + summary.py (end-of-run panel)
└── report/               # Jinja2 + Plotly → self-contained HTML
```

See [`plan/`](./plan) for architecture and future-direction docs.

## License

MIT.
