"""Per-app network counters.

`/proc/<pid>/net/dev` is **per-netns**, not per-process — on Android every
regular app shares the default netns, so that file reports device-wide
traffic. Real per-app accounting lives keyed by UID:

1. `/proc/net/xt_qtaguid/stats` — present on Android <10 and many emulators.
   Kernel-maintained table of rx/tx bytes per (uid, tag, iface).
2. `dumpsys netstats detail --uid=<uid>` — the supported path on Android 10+
   after qtaguid was removed. NetworkStatsService stores cumulative per-UID
   counters; we sum rb/tb across history buckets.

The session writer computes per-tick deltas, so both sources just need to
return a monotonically-increasing cumulative (rx_total_b, tx_total_b).
"""

from __future__ import annotations

import re

from adbutils import AdbDevice


def parse_xt_qtaguid(output: str, uid: int) -> dict[str, float] | None:
    """Sum rx/tx bytes for `uid` from xt_qtaguid. Returns None if unavailable.

    Row format: `idx iface acct_tag_hex uid_tag_int cnt_set rx_bytes rx_packets
    tx_bytes tx_packets ...`. Tagged rows (tag != 0x0) are subsets of the
    untagged totals, so filter to `0x0` to avoid double-counting.
    """
    stripped = output.strip()
    if not stripped:
        return None
    # adb surfaces errors on stdout: "No such file or directory",
    # "Permission denied", "cat: ...: ...".
    lower = stripped.lower()
    if "no such" in lower or "permission denied" in lower or "cannot open" in lower:
        return None

    uid_str = str(uid)
    rx_total = 0
    tx_total = 0
    matched_any = False
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 8 or parts[0] == "idx":
            continue
        iface, tag, row_uid = parts[1], parts[2], parts[3]
        if iface == "lo" or row_uid != uid_str or tag != "0x0":
            continue
        try:
            rx_total += int(parts[5])
            tx_total += int(parts[7])
            matched_any = True
        except ValueError:
            continue

    # No matching rows could mean either "no traffic yet" or "qtaguid exists
    # but our uid is unknown to it" — return zero rather than None, since the
    # file was readable. The fallback only kicks in when the file is missing.
    if not matched_any and "idx iface" not in output:
        return None
    return {"rx_total_b": float(rx_total), "tx_total_b": float(tx_total)}


_RB_RE = re.compile(r"\brb=(\d+)")
_TB_RE = re.compile(r"\btb=(\d+)")
_UID_SCOPE_RE = re.compile(r"\buid=(\d+)\b.*?\btag=(0x[0-9a-fA-F]+)")


def parse_netstats(output: str, uid: int) -> dict[str, float]:
    """Sum rb/tb across `dumpsys netstats detail` history rows for `uid`.

    Output groups history rows under headers like
    `... uid=10234 set=ALL tag=0x0 ...`. Only untagged (tag=0x0) headers are
    summed; tagged sub-totals would double-count.
    """
    uid_str = str(uid)
    rx_total = 0
    tx_total = 0
    in_scope = False
    for line in output.splitlines():
        scope = _UID_SCOPE_RE.search(line)
        if scope:
            in_scope = scope.group(1) == uid_str and scope.group(2) == "0x0"
            continue
        if not in_scope:
            continue
        if m := _RB_RE.search(line):
            rx_total += int(m.group(1))
        if m := _TB_RE.search(line):
            tx_total += int(m.group(1))
    return {"rx_total_b": float(rx_total), "tx_total_b": float(tx_total)}


def sample(device: AdbDevice, *, uid: int, **_: object) -> dict[str, float]:
    out = device.shell("cat /proc/net/xt_qtaguid/stats")
    parsed = parse_xt_qtaguid(out, uid)
    if parsed is not None:
        return parsed
    out = device.shell(f"dumpsys netstats detail --uid={uid}")
    return parse_netstats(out, uid)
