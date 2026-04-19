# Changelog

All notable changes to this project are documented in this file. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
