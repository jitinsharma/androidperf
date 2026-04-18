from pathlib import Path

from androidperf.collectors.activity import class_short_name, parse_resumed_activity
from androidperf.summary import fmt_bytes_from_kb
from androidperf.collectors.battery import battery_status_name, parse_battery
from androidperf.collectors.thermal import parse_thermal, thermal_status_name

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_parse_battery_extracts_all_fields():
    result = parse_battery(_read("battery.txt"))
    assert result["battery_level_pct"] == 87
    assert result["battery_temp_c"] == 31.0  # 310 deci-deg → 31.0 C
    assert result["battery_voltage_v"] == 4.35  # 4350 mV → 4.35 V
    assert result["battery_status"] == 2  # charging


def test_battery_status_name_maps_codes():
    assert battery_status_name(2.0) == "charging"
    assert battery_status_name(3.0) == "discharging"
    assert battery_status_name(None) == "—"
    assert battery_status_name(99.0).startswith("code_")


def test_parse_battery_ignores_missing_fields():
    result = parse_battery("nothing to see")
    assert result == {}


def test_parse_thermal_extracts_status_and_temps():
    result = parse_thermal(_read("thermal.txt"))
    assert result["thermal_status"] == 2  # moderate
    assert result["thermal_skin_c"] == 35.2
    assert result["thermal_cpu_c"] == 42.0
    assert result["thermal_gpu_c"] == 40.1
    assert result["thermal_battery_c"] == 30.5


def test_thermal_status_name_maps_codes():
    assert thermal_status_name(0.0) == "none"
    assert thermal_status_name(3.0) == "severe"
    assert thermal_status_name(None) == "—"


def test_parse_thermal_handles_empty_input():
    assert parse_thermal("") == {}


def test_parse_resumed_activity_finds_target_package():
    out = _read("activities.txt")
    assert parse_resumed_activity(out, "com.example.app") == "com.example.app/.HomeActivity"


def test_parse_resumed_activity_ignores_other_packages():
    out = _read("activities.txt")
    # Settings is in the activity list but not as mResumedActivity for its line.
    # We only match mResumedActivity lines scoped to our package.
    assert parse_resumed_activity(out, "com.other.app") is None


def test_parse_resumed_activity_handles_empty():
    assert parse_resumed_activity("", "com.example.app") is None


def test_fmt_bytes_from_kb_scales_units():
    assert fmt_bytes_from_kb(0.5) == "0.5 KB"
    assert fmt_bytes_from_kb(500) == "500.0 KB"
    assert fmt_bytes_from_kb(1024) == "1.0 MB"
    assert fmt_bytes_from_kb(2048) == "2.0 MB"
    assert fmt_bytes_from_kb(1024 * 1024) == "1.00 GB"
    assert fmt_bytes_from_kb(5 * 1024 * 1024) == "5.00 GB"


def test_class_short_name_strips_package_and_path():
    assert class_short_name("com.example.app/.MainActivity") == "MainActivity"
    assert class_short_name("com.foo/.a.b.c.HomeActivity") == "HomeActivity"
    assert class_short_name("com.foo/com.foo.ui.SettingsActivity") == "SettingsActivity"
    assert class_short_name("BareClass") == "BareClass"
