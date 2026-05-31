# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-05-31

### Added

- **Insights engine** — post-session analysis pass that surfaces plain-English
  findings with severity (critical / warning / info) and fix recommendations.
  Five detector categories: memory, cpu, jank, threads, network. Findings appear
  as coloured cards in the HTML report and in the web UI done panel.
  - Memory: Java heap growth via linear regression, GC-correlated jank,
    allocation-pressure / explicit / native-alloc GC pressure, Activity leak
    (object count never decreasing after screen transitions), excessive View counts.
  - CPU: sustained high load (>70% in >30% of ticks), thermal throttle jank,
    network-driven CPU spikes.
  - Jank: chronic jank over rendering ticks only, disk-write jank correlation.
  - Threads: thread count growth / possible leak with activity + library
    attribution, thread storm >100 concurrent threads.
  - Network: chatty network (>80% ticks with traffic), large payload spikes.
- **Thread name attribution** — `/proc/<pid>/task/*/status` thread names are
  captured each tick. Thread findings include library labels (OkHttp, RxJava,
  Glide, Room, Coroutines, etc.) derived from name prefixes, and the
  activity/fragment visible when new threads first appeared.
- **`/proc` collectors** (`collectors/procfs.py`) — thread count, voluntary and
  involuntary context switches, disk read/write bytes (per-tick deltas).
- **Object count collector** — `dumpsys meminfo` Objects block parsed for
  Activities, Views, and AppContexts per tick.
- **Logcat GC collector** (`collectors/logcat.py`) — background thread tailing
  `adb logcat -s art` for the full session duration; parses ART GC log lines
  into structured events (reason, freed KB, timestamp). Stored as `gc_events`
  in `samples.json`.
- **Heap dump + hprof parser** — at end of session, `am dumpheap` is triggered
  automatically for debug builds / emulators. A built-in minimal binary hprof
  parser extracts the class histogram (instance count + shallow bytes) without
  any external dependencies. Saved as `heap_histogram.json` and rendered as a
  searchable table in the HTML report. Silently skipped for non-debuggable apps.
- **Web UI** (`androidperf ui`) — FastAPI server with WebSocket live streaming.
  Browser-based interface to configure + start + stop recordings, watch metrics
  update in real time, view past runs, and open HTML reports.
  - `GET /api/devices`, `/api/packages`, `/api/runs`, `/api/runs/{id}/report`,
    `/api/runs/{id}/samples`, `/api/runs/{id}/insights`, `/api/runs/{id}/heap`
  - `WS /ws/record` — start / stream / stop a live recording.
- **`androidperf ui` CLI command** — launches the web server and opens a
  browser tab automatically.
- **Status callback** in `run_session` — `on_status` hook lets the web UI
  display post-session progress ("Capturing heap dump…") before the done panel
  appears, so the UI doesn't appear frozen during the heap capture wait.

### Fixed

- Thread storm finding previously showed inflated thread counts beside library
  names (e.g. "ThreadPoolExecutor (1217)") because occurrences were summed
  across all high-thread polling ticks instead of counted in the single
  peak-thread snapshot. Now reads from the sample where thread count was highest.

## [0.1.2] - 2026-05-06

### Added
- Fragment collector: emits the active top-level visible AndroidX fragment
  (plus deepest visible child) per tick, plus a `fragment` event each time
  the visible fragment changes. Silent on Compose-only or fragment-less apps.
- HTML report screen-timeline swim-lane: a compact Gantt-style strip at the
  top of the report with one row for activities and one for fragments, color
  coded by class name. Hover any segment for full class name + time range.
- Per-chart unified hover now includes `activity:` and `fragment:` lines so
  the screen context at any timestamp is visible alongside metric values.

### Changed
- FPS / jank cards in the summary panel reordered: smoothness signals
  (`Avg jank`, `Frame time p95`) lead; the misleading `Avg FPS` average
  card was removed. The FPS chart was renamed "Render activity (frames/sec)
  & jank" so the trace isn't read as a smoothness metric.
- The legacy text "Screen timeline" list in the HTML report was removed —
  superseded by the swim-lane.
- Live TUI panel renamed FPS → Render; jank %/p95 are now prominent and
  frames/s is shown dimmed below as activity context.

### Fixed
- FPS values were inflated to tens of thousands on Pixel devices because
  the host-side window measurement raced with `dumpsys gfxinfo` latency.
  The collector now derives the window from the dumpsys output itself
  (`Uptime` − `Stats since`), which is the exact interval the device
  counted frames over.
- Frame-time percentiles (p50/p90/p95/p99) are now suppressed when zero
  frames were rendered in the window — previously the device's
  4950 ms-bucket sentinel leaked through.
- Summary `Avg jank` / `Frame time` cards now compute over rendering ticks
  only (`fps > 0`); idle ticks no longer drag the averages toward zero.

### Performance
- Per-tick cost reduced from ~4 s to ~1.3 s on a Pixel 10 Pro running an
  active app: collectors are now fanned out on a `ThreadPoolExecutor`
  (~`max(individual)` per tick instead of `sum(individual)`).
- Memory collector switched to `dumpsys meminfo -s <package>` (short form),
  which skips the per-allocation breakdown that took 3-6 s on apps with
  large heaps. The `App Summary` block we parse is still present.

## [0.1.1] - 2026-04-19

### Fixed
- Live TUI: added one-char inter-column padding to the Memory and Network
  panels so label and value no longer touch when the value is wide (e.g.
  "Total PSS442.8 MB" → "Total PSS 442.8 MB").
- Per-UID network counters on Android 10+ devices (observed on Pixel 9 /
  Android 16): `dumpsys netstats detail` reads cached NetworkStatsService
  buckets and returned identical totals across successive ticks, so rx/tx
  deltas were always zero. The collector now issues `dumpsys netstats
  --poll` before each read to force a fresh flush.

## [0.1.0] - 2026-04-18

### Added
- Initial public release.
- Per-tick collectors: CPU %, RAM (PSS / Java / Native / Graphics / Code /
  Stack), network rx/tx, FPS + jank % + frame p50/p90/p95/p99, battery
  level/temp/voltage/status, thermal status + skin/cpu/gpu/battery temps, and
  screen transitions as timeline events.
- Live Rich terminal dashboard rendered during `androidperf record`.
- Self-contained HTML report (inlined Plotly + screen-transition markers).
- End-of-run stat-card panel printed to the terminal.
- CLI commands: `devices`, `packages`, `record`, `report`, `version`.
