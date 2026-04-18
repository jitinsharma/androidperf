# Future metrics — candidates for follow-up work

Captured already: CPU, Memory (PSS/Java/Native/Gfx/Code/Stack), Network (rx/tx
deltas), FPS/jank/frame-time percentiles, Battery (level/temp/voltage/status),
Thermal (skin/cpu/gpu/battery + status), Activity transitions.

The ideas below are not yet implemented. Ordered by expected value-for-effort.

---

## Cheap to add per-tick

These piggy-back on a single extra `adb shell` per sample (or on a file we
already read), so the cost is negligible.

### Thread count
- **Source:** `cat /proc/<pid>/status` → `Threads:` line
- **Why:** Steady upward drift is a thread-leak smoke signal.
- **Sample keys:** `threads`

### Disk I/O bytes (read/write)
- **Source:** `cat /proc/<pid>/io` → `read_bytes`, `write_bytes`
- **Why:** Often the hidden cause of main-thread jank. Storing deltas (like
  network) gives per-tick I/O pressure.
- **Sample keys:** `disk_read_b`, `disk_write_b`

### Context switches (voluntary / non-voluntary)
- **Source:** `/proc/<pid>/status` → `voluntary_ctxt_switches`,
  `nonvoluntary_ctxt_switches`
- **Why:** Non-voluntary spikes = scheduling pressure / CPU contention.
- **Sample keys:** `csw_vol`, `csw_nonvol` (store deltas per tick).

### Activities / Views / ViewRootImpl counts
- **Source:** `dumpsys meminfo <pkg>` → "Objects" section
  (already a command we run; just parse more of it).
- **Why:** Classic Android leak detector. A View or Activity count that never
  goes down after screen transitions = leak.
- **Sample keys:** `activities_n`, `views_n`, `view_roots_n`, `app_contexts_n`

### Device-wide MemAvailable
- **Source:** `cat /proc/meminfo` → `MemAvailable`
- **Why:** Correlate app slowdowns with whole-system memory pressure.
- **Sample keys:** `sys_mem_avail_kb`

---

## One-shot (record once in `meta`, not time-series)

### Startup time (cold / warm)
- **Source:** `am start -W -n <pkg>/<main-activity>` returns `ThisTime`,
  `TotalTime`, `WaitTime`.
- **Why:** Probably the single most-asked-about app metric. We already launch
  the app in `device.launch_app` — parsing `-W` output gives it for free.
- **Meta keys (new):** `startup.this_time_ms`, `startup.total_time_ms`,
  `startup.wait_time_ms`, `startup.mode` (cold/warm)
- **UX:** Show on the summary cards row of the HTML report and in the terminal
  header.

---

## Events (emit alongside screen transitions)

### ANRs / crashes
- **Source:** `adb logcat -b crash --pid=<pid>` (background reader) and/or
  `adb logcat *:E` filtered by pid.
- **Why:** Marks exactly where things went wrong on the timeline. Would become
  additional entries in the `events[]` array with `type: "crash"` / `"anr"` /
  `"error"`, and render as red vertical bars on every Plotly chart.
- **Implementation note:** Needs a lightweight background thread reading
  logcat so the polling loop isn't blocked.

---

## Medium effort, higher value

### GC activity
- **Source:** `dumpsys gfxinfo <pkg>` already surfaces some; richer signal from
  tailing logcat `art:` tag (`"Background concurrent copying GC freed..."`)
  or `dumpsys meminfo --checkin <pkg>`.
- **Why:** Pause times + GC frequency explain memory-driven jank better than
  raw PSS.

### Binder IPC rate
- **Source:** `/proc/<pid>/binder` (if readable) or `dumpsys binder_proc <pid>`
- **Why:** Catches chatty IPC (often an unnoticed battery/perf drain).
- **Sample keys:** `binder_txns` (delta), `binder_in_flight`

### CPU frequency (per big/little cluster)
- **Source:** `/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq` +
  `cpuinfo_max_freq`
- **Why:** Thermal throttling becomes visible as frequency caps, independent
  of the skin-temp reading. Also catches governor-based slowdowns.
- **Sample keys:** `cpu_freq_mhz_min`, `cpu_freq_mhz_max`, `cpu_freq_mhz_avg`

### Coulomb-counter battery drain
- **Source:** `/sys/class/power_supply/battery/current_now` (µA, signed)
- **Why:** Much finer-grained than the 1% `battery_level_pct` — lets short
  recordings show a meaningful drain number.
- **Sample keys:** `battery_current_ua`

---

## Probably not worth the effort (yet)

- **Input latency / touch events** — `dumpsys input` is noisy; value is low
  without a synthetic input driver.
- **Cellular / WiFi signal** — interesting but rarely the cause of app perf
  issues developers debug locally.
- **GPU memory / surface count** — `dumpsys SurfaceFlinger` is dense to parse
  and mostly duplicates what gfxinfo tells us.
- **Screen brightness / power draw estimate** — derivable from batterystats
  but only meaningful over very long sessions.

---

## Sequencing suggestion

If someone picks this up, a reasonable order:

1. **Startup time** — one-shot, trivial, biggest user-visible win.
2. **Threads + disk I/O + context switches + MemAvailable** — one `/proc`
   pass each tick, one extension to the existing session loop, one new
   collector file each (~40 LOC apiece with fixtures).
3. **Activities / Views counts** — zero extra ADB calls (already run meminfo);
   parse the "Objects" block in `collectors/memory.py`.
4. **Logcat-based events (ANR / crash / error)** — introduces the first
   background thread; gate behind a `--watch-logcat` flag initially.
5. **GC + Binder + CPU freq + coulomb counter** — specialist signals, add on
   demand.
