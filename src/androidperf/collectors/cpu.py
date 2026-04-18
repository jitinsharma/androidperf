"""CPU usage via `top -n 1 -b -p <pid>`.

Android ships toybox's top, whose column order depends on the device. We
parse the header line to locate %CPU rather than relying on fixed offsets.
"""

from __future__ import annotations

from adbutils import AdbDevice


def parse_top(output: str, pid: int) -> dict[str, float]:
    """Extract %CPU for the given pid. Returns {'cpu_pct': float} or {} if not found."""
    lines = output.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if "PID" in line and "%CPU" in line:
            header_idx = i
            break
    if header_idx is None:
        return {}

    # Toybox's top glues the state and CPU columns together in the header
    # (e.g. `S[%CPU]`) even though they appear as two whitespace-separated
    # values in data rows. Split that token before indexing.
    raw_header = lines[header_idx].replace("S[%CPU]", "S %CPU")
    header = raw_header.split()
    try:
        pid_col = header.index("PID")
        cpu_col = header.index("%CPU")
    except ValueError:
        return {}

    pid_str = str(pid)
    for row in lines[header_idx + 1 :]:
        cols = row.split()
        if len(cols) <= max(pid_col, cpu_col):
            continue
        if cols[pid_col] != pid_str:
            continue
        raw = cols[cpu_col].rstrip("%")
        try:
            return {"cpu_pct": float(raw)}
        except ValueError:
            return {}
    return {}


def sample(device: AdbDevice, *, pid: int, **_: object) -> dict[str, float]:
    out = device.shell(f"top -n 1 -b -p {pid}")
    return parse_top(out, pid)
