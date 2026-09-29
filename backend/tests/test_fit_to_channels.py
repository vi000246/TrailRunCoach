"""FIT -> WKO5 channel conversion.

Unit tests pin each reverse-engineered rule with synthetic records; the parity
test replays the original FIT embedded in real .wko4 files (field 4049) and
requires WKO5-identical samples. Parity is skipped when no WKO5 athlete folder
is available (set WKO5_ATHLETE_DIR).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.files.fit_to_channels import channels_from_messages, fit_to_channels, _quantize

T0 = datetime(2025, 1, 1, 8, 0, 0, tzinfo=timezone.utc)


def rec(sec, **kw):
    return {"timestamp": T0 + timedelta(seconds=sec), **kw}


def sess(total=None, **kw):
    return [{"start_time": T0, "total_elapsed_time": total, **kw}]


def timer(sec, typ):
    return {"timestamp": T0 + timedelta(seconds=sec), "event": "timer", "event_type": typ}


# --- 1-second recording model ------------------------------------------------

def test_one_second_labels_each_record_at_its_interval_end():
    fc = channels_from_messages([rec(i, heart_rate=100 + i) for i in range(4)], sess())
    assert fc.one_second
    assert fc.elapsedtime == [1.0, 2.0, 3.0, 4.0]
    assert fc.channels["heartrate"] == [100, 101, 102, 103]


def test_one_second_gap_inserts_single_empty_sample():
    recs = [rec(i, heart_rate=100) for i in range(40)] + [rec(i, heart_rate=90) for i in range(60, 100)]
    fc = channels_from_messages(recs, sess())
    i = fc.elapsedtime.index(40.0)
    assert fc.elapsedtime[i:i + 3] == [40.0, 60.0, 61.0]
    assert fc.channels["heartrate"][i:i + 3] == [100, None, 90]


def test_one_second_last_label_clipped_to_total_elapsed():
    recs = [rec(i, heart_rate=100) for i in range(30)]
    fc = channels_from_messages(recs, sess(total=29.4))
    assert fc.elapsedtime[-2:] == [29.0, 29.4]


def test_leading_records_without_data_fields_are_trimmed_and_origin_moves():
    recs = [rec(i, activity_type="generic", distance=0.0) for i in range(5)]
    recs += [rec(i, activity_type="generic", distance=0.0, heart_rate=90) for i in range(5, 40)]
    fc = channels_from_messages(recs, sess(total=39.5))
    assert fc.elapsedtime[0] == 1.0 and fc.channels["heartrate"][0] == 90
    assert len(fc.elapsedtime) == 35
    assert fc.elapsedtime[-1] == 34.5            # total shifted by the 5 s lead-in
    assert "elapseddistance" not in fc.channels  # all-zero distance is dropped


def test_records_defining_fields_with_invalid_values_are_not_trimmed():
    recs = [rec(i, heart_rate=None, temperature=None) for i in range(3)]
    recs += [rec(i, heart_rate=None, temperature=20) for i in range(3, 30)]
    fc = channels_from_messages(recs, sess())
    assert len(fc.elapsedtime) == 30


# --- smart recording model -----------------------------------------------------

def test_smart_recording_labels_own_time_and_drops_t0():
    recs = [rec(0, heart_rate=80), rec(5, heart_rate=90), rec(9, heart_rate=95), rec(15, heart_rate=99)]
    fc = channels_from_messages(recs, sess())
    assert not fc.one_second
    assert fc.elapsedtime == [5.0, 9.0, 15.0]
    assert fc.channels["heartrate"] == [90, 95, 99]


def test_duplicate_timestamps_merge():
    recs = [rec(0, heart_rate=80), rec(4, heart_rate=90), rec(4, position_lat=2 ** 30), rec(8, heart_rate=91)]
    fc = channels_from_messages(recs, sess())
    assert fc.elapsedtime == [4.0, 8.0]
    assert fc.channels["heartrate"][0] == 90 and fc.channels["latitude"][0] == 90.0


# --- timer events --------------------------------------------------------------

def test_records_after_final_timer_stop_are_dropped():
    recs = [rec(0, heart_rate=80), rec(5, heart_rate=90), rec(10, heart_rate=95), rec(20, heart_rate=99)]
    fc = channels_from_messages(recs, sess(), [timer(0, "start"), timer(12, "stop_all")])
    assert fc.elapsedtime == [5.0, 10.0]


def test_smart_record_at_restart_instant_is_dropped():
    recs = [rec(i * 2, heart_rate=100) for i in range(12)]
    fc = channels_from_messages(recs, sess(), [timer(0, "start"), timer(10, "stop_all"), timer(12, "start")])
    assert 12.0 not in fc.elapsedtime and 10.0 in fc.elapsedtime and 14.0 in fc.elapsedtime


def test_repeated_stop_without_start_is_ignored():
    recs = [rec(i * 3, heart_rate=100) for i in range(10)]
    fc = channels_from_messages(recs, sess(), [timer(0, "start"), timer(6, "stop_all"), timer(27, "stop_all")])
    assert fc.elapsedtime[-1] == 27.0


# --- values -------------------------------------------------------------------

def test_units_and_quantization():
    recs = [rec(i, enhanced_speed=2.5555, enhanced_altitude=100.25, cadence=80,
                fractional_cadence=0.5, distance=1000.0 + i) for i in range(3)]
    fc = channels_from_messages(recs, sess())
    assert fc.channels["speed"][0] == 9.2              # 2.5555 m/s * 3.6 -> 0.001 km/h
    assert fc.channels["elevation"][0] == 100.3        # half away from zero
    assert fc.channels["cadence"][0] == 81             # 80.5 rounds up
    assert fc.channels["elapseddistance"] == [0.0, 0.001, 0.002]


def test_distance_ignores_device_drops_but_keeps_later_increments():
    d = [0, 10, 20, 5, 15]
    fc = channels_from_messages([rec(i, distance=float(x)) for i, x in enumerate(d)], sess())
    assert fc.channels["elapseddistance"] == [0.0, 0.01, 0.02, 0.02, 0.03]


def test_speed_derived_from_distance_when_missing():
    # distance is rebased to the first kept sample, so its derived speed is 0
    recs = [rec(0, distance=0.0), rec(6, distance=60.0), rec(12, distance=90.0)]
    fc = channels_from_messages(recs, sess())
    assert fc.channels["speed"] == [0.0, 18.0]


def test_speed_spikes_blanked():
    recs = [rec(i, enhanced_speed=v) for i, v in enumerate([3.0, 45.0, 3.0])]
    fc = channels_from_messages(recs, sess())
    assert fc.channels["speed"] == [10.8, None, 10.8]


def test_at_channels_float_and_activity_type_enum():
    recs = [rec(i, activity_type="running", step_length=1000.0, **{"Effort Pace": 2.0}) for i in range(3)]
    fc = channels_from_messages(recs, sess())
    assert fc.channels["@activity_type"] == [1.0, 1.0, 1.0]
    assert fc.channels["@effort_pace"] == [7.2, 7.2, 7.2]
    assert fc.channels["@step_length"][0] == 1000.0


def test_quantize_half_away_from_zero():
    assert _quantize(80.5, 1) == 81 and _quantize(-2.5, 1) == -3


# --- parity against WKO5 ------------------------------------------------------

ATHLETE_DIR = Path(os.environ.get(
    "WKO5_ATHLETE_DIR", r"C:\Users\<user>\Projects\TrailRunCoach\WKO5\Athlete"))
wko4_files = sorted(ATHLETE_DIR.rglob("*.wko4")) if ATHLETE_DIR.exists() else []
PARITY_STRIDE = int(os.environ.get("WKO5_PARITY_STRIDE", "8"))   # 1 = every file


def _eq(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 1e-6 * max(1.0, abs(b))


@pytest.mark.skipif(not wko4_files, reason="no WKO5 athlete folder available")
def test_parity_with_wko5_imports():
    import warnings
    from backend.files.wko4_file import read_wko4
    warnings.filterwarnings("ignore")
    checked = same_len = 0
    for p in wko4_files[::PARITY_STRIDE]:
        w = read_wko4(p)
        if w.original_type != "fit" or not w.original_bytes or "elapsedtime" not in w.channels:
            continue
        checked += 1
        fc = fit_to_channels(w.original_bytes)
        wt = w.channels["elapsedtime"].values
        if len(fc.elapsedtime) != len(wt):
            continue  # known device oddities (GPSMAP clock, pool swim, table tennis)
        same_len += 1
        assert all(_eq(a, b) for a, b in zip(fc.elapsedtime, wt)), (p.name, "elapsedtime")
        for ch, vals in fc.channels.items():
            if ch == "power" or ch not in w.channels:
                continue  # power: WKO5 blanks repeated spike values (not modelled)
            wv = w.channels[ch].values
            assert all(_eq(a, b) for a, b in zip(vals, wv)), (p.name, ch)
    assert checked > 20
    assert same_len / checked >= 0.95, (same_len, checked)
