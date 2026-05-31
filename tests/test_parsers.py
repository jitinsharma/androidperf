import struct
import tempfile
from pathlib import Path

from androidperf.collectors.cpu import parse_top
from androidperf.collectors.fps import parse_gfxinfo
from androidperf.collectors.fragments import parse_active_fragment
from androidperf.collectors.hprof_parse import parse_histogram
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


def test_parse_active_fragment_picks_visible_top_and_deepest_child():
    result = parse_active_fragment(_read("dumpsys_activity.txt"))
    # Captured while the user was on the Stocks tab with the SIP listing pager
    # page in focus. Hidden tabs (Credit/Fno/Mf) should be filtered out.
    assert result is not None
    top, _, child = result.partition(" / ")
    assert top == "MainStocksTabFragment"
    assert child  # nested child present


# ---------------------------------------------------------------------------
# hprof parser
# ---------------------------------------------------------------------------

def _make_hprof(id_size: int = 4) -> bytes:
    """Build a minimal valid Android hprof binary for testing."""
    fmt_id = ">I" if id_size == 4 else ">Q"

    def pid(v: int) -> bytes:
        return struct.pack(fmt_id, v)

    def u4(v: int) -> bytes:
        return struct.pack(">I", v)

    def u2(v: int) -> bytes:
        return struct.pack(">H", v)

    def record(tag: int, data: bytes) -> bytes:
        return bytes([tag]) + struct.pack(">II", 0, len(data)) + data

    # UTF8 strings: id -> name
    s1 = record(0x01, pid(1) + b"com/example/MainActivity")
    s2 = record(0x01, pid(2) + b"java/lang/String")
    s3 = record(0x01, pid(3) + b"android/app/Activity")

    # LOAD_CLASS: (class_serial, class_object_id, stack_serial, name_string_id)
    lc1 = record(0x02, u4(1) + pid(101) + u4(0) + pid(1))
    lc2 = record(0x02, u4(2) + pid(102) + u4(0) + pid(2))
    lc3 = record(0x02, u4(3) + pid(103) + u4(0) + pid(3))

    # CLASS_DUMP for class 101 (zero fields — tests the skip path)
    class_dump = (
        pid(101) + u4(0)          # class_id + stack_serial
        + pid(0) * 6              # super, loader, signers, domain, reserved1, reserved2
        + u4(0)                   # instance_size
        + u2(0) + u2(0) + u2(0)  # cp_count=0, sf_count=0, if_count=0
    )

    def instance(obj_id: int, class_id: int, data: bytes = b"") -> bytes:
        return bytes([0x21]) + pid(obj_id) + u4(0) + pid(class_id) + u4(len(data)) + data

    heap = (
        bytes([0x20]) + class_dump  # CLASS_DUMP (exercises _skip_class_dump)
        + instance(1001, 101)       # 3x MainActivity
        + instance(1002, 101)
        + instance(1003, 101)
        + instance(2001, 102, b"\x00" * 8)  # 5x String (with shallow bytes)
        + instance(2002, 102, b"\x00" * 8)
        + instance(2003, 102, b"\x00" * 8)
        + instance(2004, 102, b"\x00" * 8)
        + instance(2005, 102, b"\x00" * 8)
        + instance(3001, 103)       # 1x Activity
    )

    header = b"JAVA PROFILE 1.0.1\x00" + u4(id_size) + struct.pack(">Q", 0)
    return header + s1 + s2 + s3 + lc1 + lc2 + lc3 + record(0x0C, heap)


def test_hprof_parse_counts_instances():
    with tempfile.NamedTemporaryFile(suffix=".hprof", delete=False) as f:
        f.write(_make_hprof(id_size=4))
        path = f.name

    result = parse_histogram(path)
    by_name = {r["class_name"]: r for r in result}

    assert by_name["com.example.MainActivity"]["instances"] == 3
    assert by_name["java.lang.String"]["instances"] == 5
    assert by_name["android.app.Activity"]["instances"] == 1


def test_hprof_parse_shallow_bytes():
    with tempfile.NamedTemporaryFile(suffix=".hprof", delete=False) as f:
        f.write(_make_hprof(id_size=4))
        path = f.name

    result = parse_histogram(path)
    by_name = {r["class_name"]: r for r in result}

    # 5 String instances × 8 bytes each
    assert by_name["java.lang.String"]["shallow_bytes"] == 40
    # MainActivity instances have 0 instance data
    assert by_name["com.example.MainActivity"]["shallow_bytes"] == 0


def test_hprof_parse_sorted_by_instances_desc():
    with tempfile.NamedTemporaryFile(suffix=".hprof", delete=False) as f:
        f.write(_make_hprof(id_size=4))
        path = f.name

    result = parse_histogram(path)
    counts = [r["instances"] for r in result]
    assert counts == sorted(counts, reverse=True)


def test_hprof_parse_normalises_slash_to_dot():
    with tempfile.NamedTemporaryFile(suffix=".hprof", delete=False) as f:
        f.write(_make_hprof(id_size=4))
        path = f.name

    result = parse_histogram(path)
    for row in result:
        assert "/" not in row["class_name"]


def test_hprof_parse_8byte_ids():
    with tempfile.NamedTemporaryFile(suffix=".hprof", delete=False) as f:
        f.write(_make_hprof(id_size=8))
        path = f.name

    result = parse_histogram(path)
    by_name = {r["class_name"]: r for r in result}
    assert by_name["com.example.MainActivity"]["instances"] == 3
    assert by_name["java.lang.String"]["instances"] == 5


def test_parse_active_fragment_returns_none_for_compose_app():
    # No "Fragment{...}" entries — typical of a Compose-only app dump.
    text = "ACTIVITY com.example/.MainActivity\n  mResumed=true\n  ViewRoot:\n"
    assert parse_active_fragment(text) is None
