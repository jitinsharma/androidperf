# Automation integrations — design exploration

Status: **not implemented**. This doc captures direction for when/if automation
becomes the priority. No code changes proposed right now.

---

## The problem

Today `androidperf record` is a blocking foreground process, stopped only by
`SIGINT` or `--duration`. That shape is fine for a human driving it from a
terminal, but it blocks three automation flavors that would be valuable:

1. **Deterministic UI test frameworks** (Maestro being the obvious target) —
   "run this flow, capture perf during the flow, merge step boundaries into
   the timeline".
2. **LLM-driven mobile agents** (auto-mobile, Mobile-Agent-v2, AppAgent, etc.)
   — "agent drives the device; it should be able to start/stop recordings and
   tag what it just did so perf anomalies are traceable to actions".
3. **Claude / other agent hosts via MCP or skills** — "a Claude skill +
   tool-server so any agent host can record perf without knowing the internals".

These three *look* different but share one root requirement: the recording
session has to be **controllable from outside its own process**.

---

## The shared enabler (if/when we build this)

A "session daemon" pattern built around a per-session state directory. No
network socket, no RPC protocol — a filesystem layout is the API.

```
~/.androidperf/sessions/<session_id>/
  pid               # of the background recorder process
  meta.json         # started_at, package, interval, device info
  events.ndjson     # append-only; any caller can write a line
  latest.json       # rewritten each tick with the most recent sample
  samples.json      # created on stop
  report.html       # created on stop
```

New CLI surface:

| Command | Purpose |
|---|---|
| `androidperf record --detach --session-id <id>` | Start a background session, return immediately |
| `androidperf mark <id> --event "…" [--meta '{…}']` | Append an event line (any caller) |
| `androidperf status <id>` | Print live summary (elapsed, counts, latest values) |
| `androidperf stop <id>` | SIGTERM the recorder + wait for JSON/HTML flush |

### Why files instead of a socket/RPC
- `events.ndjson` as append-only gives every caller (shell script, Maestro
  `runScript`, Python SDK, MCP tool) the same trivial write interface.
- The session loop reads unread lines each tick and stamps them with the
  current elapsed time from `started_at` → no clock-sync problem.
- No protocol to design, document, or version. `echo ... >> events.ndjson`
  works from anywhere.

### Known costs
- Pidfile / lockfile / crash cleanup / zombie sessions. Unavoidable price of
  daemonization.
- Need a `prune` or TTL mechanism for orphaned session dirs.

---

## Integration paths built on the daemon

### 1. Maestro wrapper (highest external leverage)

Shape:

```
androidperf flow --package com.example.app --maestro-flow flows/login.yaml \
                 --output-dir ./runs/
```

Internally:
1. Start session in detached mode → get `<id>`.
2. Fork `maestro test flows/login.yaml --format=junit --output .junit.xml`.
3. Translate Maestro step boundaries into `androidperf mark` calls, either by:
   - reading Maestro's JSON/JUnit output incrementally, or
   - parsing stdout lines like `Running: tapOn "Login"`.
4. On Maestro exit → `stop <id>` → render report.

Report additions:
- Flow-step annotations on every chart (same mechanism as screen transitions).
- Pass/fail banner + summary card.
- Optional: interleaved Maestro screenshots with chart sections.

**Easier v1 alternative:** `androidperf correlate samples.json maestro.xml -o merged.html`
— post-hoc merge. No process supervision. Good "deal with Maestro version drift
later" escape hatch.

**Version fragility:** Maestro's output format has changed across releases.
Either pin a supported range, or stay at flow-level (start/end) event
granularity to avoid brittle step parsing.

### 2. LLM-driven mobile agents (auto-mobile, AppAgent, Mobile-Agent-v2)

Two integration shapes — ship both, they're cheap once the daemon exists.

**Python SDK** (for agents that import Python modules directly):

```python
from androidperf import Session

with Session(package="com.example.app") as perf:
    perf.mark("plan:login_flow")
    agent.tap("login_button")
    perf.mark("action:tap_login")
    ...
# perf.run_dir has samples.json + report.html
```

Context-manager is a thin wrapper over `record --detach` + `mark` + `stop`.

**MCP server** (for any MCP-capable host — Claude, Cursor, Continue):

```
androidperf mcp   # stdio MCP server
```

Tools exposed:

| Tool | Signature | Returns |
|---|---|---|
| `perf_start` | `package: str, interval?: float, duration?: float` | `session_id` |
| `perf_mark`  | `session_id, event: str, meta?: dict` | `ok` |
| `perf_sample`| `session_id` | latest sample dict |
| `perf_status`| `session_id` | elapsed, counts, device |
| `perf_stop`  | `session_id` | `{run_dir, report_html}` |
| `perf_summary`| `session_id or run_dir` | min/max/mean/p95 per metric |

MCP is the portable bet — one server, many agent hosts. Hand-rolling per-host
tool schemas is cheaper today if you only care about one agent, but locks you
in.

### 3. Claude skill file (the "when" layer)

A short markdown skill under `~/.claude/skills/androidperf.md` (or as a plugin)
that tells the LLM *when* to reach for this tool:

- user asks about CPU / RAM / FPS / jank / battery / thermal of a running
  Android app
- user wants before/after perf numbers for a change
- during automated testing (Maestro, LLM agents)

Skill + MCP are complementary: **skill = when, MCP = how**. Not exclusive.

---

## What this tool will *not* try to be

- **Macrobenchmark / AndroidX Benchmark** replacement — those are the right
  answer for Gradle-integrated CI perf gates with statistical rigor. This tool
  stays in the ADB-only, language-agnostic, any-installed-APK niche. A README
  note can point users to macrobenchmark when they outgrow this.
- **A device farm / cloud runner.** Local-only by design.

---

## Sequencing (if/when picked up)

1. **Session daemon refactor** (Phase 1) — enabler for everything else. No
   external framework knowledge yet.
2. **Maestro wrapper** (Phase 2) — highest external leverage; Maestro users
   are already running "execute flow, then measure" by hand.
3. **Python SDK + MCP server + Claude skill** (Phase 3) — unlocks agent
   integrations in one shot.

Each phase is independently shippable. Not starting any of this now.
