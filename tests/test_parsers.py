from pathlib import Path

from androidperf.collectors.cpu import parse_top
from androidperf.collectors.fps import parse_gfxinfo
from androidperf.collectors.memory import parse_meminfo
from androidperf.collectors.network import parse_netstats, parse_xt_qtaguid

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parse_top_extracts_cpu_percent():
    result = parse_top(_read("top.txt"), pid=12345)
    assert result == {"cpu_pct": 12.3}


def test_parse_top_missing_pid_returns_empty():
    result = parse_top(_read("top.txt"), pid=99999)
    assert result == {}


def test_parse_top_missing_header_returns_empty():
    assert parse_top("nothing useful here\n", pid=12345) == {}


def test_parse_meminfo_pulls_app_summary_rows():
    result = parse_meminfo(_read("meminfo.txt"))
    assert result["pss_kb"] == 204800
    assert result["java_kb"] == 40960
    assert result["native_kb"] == 40960
    assert result["gfx_kb"] == 20480
    assert result["code_kb"] == 14336
    assert result["stack_kb"] == 512


def test_parse_meminfo_missing_summary_returns_empty():
    assert parse_meminfo("no summary anywhere\n") == {}


def test_parse_xt_qtaguid_sums_untagged_rows_for_uid():
    result = parse_xt_qtaguid(_read("xt_qtaguid.txt"), uid=10234)
    # wlan0 cnt_set=0 + wlan0 cnt_set=1 + rmnet0. Tagged (0xf...) excluded;
    # other uid excluded; loopback excluded.
    assert result["rx_total_b"] == 5_000_000 + 1_000_000 + 250_000
    assert result["tx_total_b"] == 800_000 + 200_000 + 50_000


def test_parse_xt_qtaguid_returns_zero_for_unknown_uid():
    # File is present but our uid has no rows — zero bytes, not None.
    result = parse_xt_qtaguid(_read("xt_qtaguid.txt"), uid=11111)
    assert result == {"rx_total_b": 0.0, "tx_total_b": 0.0}


def test_parse_xt_qtaguid_returns_none_when_file_missing():
    # adb relays kernel errors on stdout. None signals caller to fall back.
    assert parse_xt_qtaguid("/proc/net/xt_qtaguid/stats: No such file or directory", uid=10234) is None
    assert parse_xt_qtaguid("", uid=10234) is None


def test_parse_netstats_sums_untagged_history_for_uid():
    result = parse_netstats(_read("netstats.txt"), uid=10234)
    # Two history buckets under the uid=10234 tag=0x0 scope.
    assert result["rx_total_b"] == 3_000_000 + 2_000_000
    assert result["tx_total_b"] == 500_000 + 300_000


def test_parse_netstats_ignores_other_uids_and_tagged_rows():
    result = parse_netstats(_read("netstats.txt"), uid=10999)
    assert result["rx_total_b"] == 99999
    assert result["tx_total_b"] == 99999


def test_parse_netstats_empty_input():
    assert parse_netstats("", uid=10234) == {"rx_total_b": 0.0, "tx_total_b": 0.0}


def test_parse_gfxinfo_extracts_summary():
    result = parse_gfxinfo(_read("gfxinfo.txt"))
    assert result["frames_total"] == 120
    assert result["jank_frames"] == 6
    assert result["jank_pct"] == 5.00
    assert result["p50_ms"] == 8
    assert result["p90_ms"] == 12
    assert result["p95_ms"] == 18
    assert result["p99_ms"] == 48


def test_parse_gfxinfo_partial_output():
    text = "Total frames rendered: 42\n"
    result = parse_gfxinfo(text)
    assert result == {"frames_total": 42}
