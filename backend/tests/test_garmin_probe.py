"""Offline tests for backend/scripts/garmin_probe.py: session steps -> Garmin
workout JSON, and the sealed token cache. No network, no garminconnect import."""
import sys

import pytest

from backend.scripts import garmin_probe as GP
from backend.sync import coros_workouts as CW

TH = {"cp": 300, "lthr": 170, "aet": 150}


def sess(kind, title, minutes, detail="", target="", source="", day="2026-10-01"):
    return {"id": kind, "kind": kind, "title": title, "minutes": minutes, "target": target,
            "detail": detail, "source": source, "tss": 30.0, "day": day, "done": False}


def steps_of(w):
    (seg,) = w["workoutSegments"]
    assert seg["segmentOrder"] == 1 and seg["sportType"]["sportTypeKey"] == "running"
    return seg["workoutSteps"]


def all_orders(steps):
    out = []
    for s in steps:
        out.append(s["stepOrder"])
        out += all_orders(s.get("workoutSteps", []))
    return out


def test_module_does_not_import_garminconnect():
    assert "garminconnect" not in sys.modules


def test_easy_run_is_one_hr_step():
    w = GP.garmin_workout(sess("easy", "越野輕鬆跑", 45), TH)
    assert w["workoutName"] == "TRC 越野輕鬆跑 10/1"
    assert w["sportType"]["sportTypeId"] == 1 and w["estimatedDurationInSecs"] == 45 * 60
    (s,) = steps_of(w)
    assert s["type"] == "ExecutableStepDTO" and s["stepOrder"] == 1
    assert s["stepType"]["stepTypeKey"] == "interval"
    assert s["endCondition"]["conditionTypeKey"] == "time" and s["endConditionValue"] == 45 * 60
    assert s["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert (s["targetValueOne"], s["targetValueTwo"]) == (round(0.75 * 170), 150)
    assert "zoneNumber" not in s


def test_threshold_intervals_repeat_group_and_unique_orders():
    s = sess("quality", "閾值 3×10 分", 60, detail="休 2–3 分鐘；暖身 15 分、緩和 10 分")
    w = GP.garmin_workout(s, TH)
    warm, grp, cool = steps_of(w)
    assert warm["stepType"]["stepTypeKey"] == "warmup" and warm["endConditionValue"] == 15 * 60
    assert cool["stepType"]["stepTypeKey"] == "cooldown" and cool["endConditionValue"] == 10 * 60
    assert grp["type"] == "RepeatGroupDTO" and grp["stepType"]["stepTypeKey"] == "repeat"
    assert grp["numberOfIterations"] == 3 and grp["endConditionValue"] == 3.0
    assert grp["endCondition"]["conditionTypeKey"] == "iterations"
    work, rest = grp["workoutSteps"]
    assert work["stepType"]["stepTypeKey"] == "interval" and work["endConditionValue"] == 600
    assert rest["stepType"]["stepTypeKey"] == "recovery" and rest["endConditionValue"] == 180
    assert all_orders(steps_of(w)) == [1, 2, 3, 4, 5]
    assert w["estimatedDurationInSecs"] == 15 * 60 + 3 * 13 * 60 + 10 * 60
    assert w["description"].startswith("休 2–3 分鐘")


def test_power_is_a_note_by_default_and_a_target_when_asked():
    s = sess("quality", "閾值 3×10 分", 60, detail="休 2–3 分鐘；暖身 15 分、緩和 10 分")
    lo, hi = round(0.95 * 300), round(1.01 * 300)
    work = steps_of(GP.garmin_workout(s, TH))[1]["workoutSteps"][0]
    assert work["targetType"]["workoutTargetTypeKey"] == "no.target"
    assert "targetValueOne" not in work and f"目標功率 {lo}–{hi} W" in work["description"]
    work = steps_of(GP.garmin_workout(s, TH, power_targets=True))[1]["workoutSteps"][0]
    assert work["targetType"]["workoutTargetTypeKey"] == "power.zone"
    assert (work["targetValueOne"], work["targetValueTwo"]) == (lo, hi)


def test_open_all_out_bout_ends_with_lap_button():
    steps = [CW.Step(CW.EX_TRAIN, 0, None, "全力")]
    (s,) = GP.garmin_steps(steps)[0]
    assert s["endCondition"]["conditionTypeKey"] == "lap.button"
    assert "endConditionValue" not in s and s["targetType"]["workoutTargetTypeKey"] == "no.target"


def test_cp_test_and_strides():
    w = GP.garmin_workout(sess("test", "CP 測試 12 分 + 3 分", 60), TH)
    kinds = [s["stepType"]["stepTypeKey"] for s in steps_of(w)]
    assert kinds == ["warmup", "interval", "recovery", "interval", "cooldown"]
    w = GP.garmin_workout(sess("easy", "輕鬆跑＋6×20 秒衝刺", 40), TH)
    base, grp = steps_of(w)
    assert grp["numberOfIterations"] == 6
    assert [c["endConditionValue"] for c in grp["workoutSteps"]] == [20, 60]
    assert all_orders(steps_of(w)) == [1, 2, 3, 4]


def test_no_thresholds_means_no_target():
    (s,) = steps_of(GP.garmin_workout(sess("long", "長跑", 90), {}))
    assert s["targetType"]["workoutTargetTypeKey"] == "no.target"


@pytest.mark.parametrize("kind", ["strength", "race", "rest", "heat_passive"])
def test_unsupported_kinds_are_not_pushed(kind):
    with pytest.raises(CW.Unsupported):
        GP.garmin_workout(sess(kind, "x", 30), TH)


def test_the_push_test_workout_is_clearly_named_and_small():
    w = GP.test_workout()
    assert w["workoutName"] == GP.TEST_NAME == "TrailRunCoach 測試課表（可刪除）"
    steps = steps_of(w)
    assert [s["stepType"]["stepTypeKey"] for s in steps] == ["warmup", "repeat", "cooldown"]
    assert steps[0]["targetType"]["workoutTargetTypeKey"] == "heart.rate.zone"
    assert w["estimatedDurationInSecs"] == 14 * 60
    wp = GP.test_workout(with_power=True)
    power = [s for s in steps_of(wp)
             if s.get("targetType", {}).get("workoutTargetTypeKey") == "power.zone"]
    assert len(power) == 1 and (power[0]["targetValueOne"], power[0]["targetValueTwo"]) == (200, 230)


def test_token_cache_is_sealed(tmp_path, monkeypatch):
    # conftest's autouse fixture sets a throwaway WKO5COACH_SECRET_KEY
    from backend.settings import secrets
    monkeypatch.setattr(GP, "GARMIN_DIR", tmp_path / "garmin")
    monkeypatch.setattr(GP, "SESSION_FILE", tmp_path / "garmin" / "session.enc")
    tok = '{"di_token": "abc", "di_refresh_token": "def", "di_client_id": "x"}'
    GP.save_tokens(tok)
    raw = GP.SESSION_FILE.read_text("ascii")
    # quoted: a bare "abc" turns up in ~1 of 800 random base64 tokens; '"' never does
    assert raw.startswith(secrets.PREFIX) and '"abc"' not in raw and '"def"' not in raw
    assert GP.load_cached_tokens() == tok
    GP.forget_tokens()
    assert not GP.SESSION_FILE.exists() and GP.load_cached_tokens() is None


def test_preview_runs_offline(capsys):
    assert GP.main(["--preview"]) == 0
    out = capsys.readouterr().out
    assert GP.TEST_NAME in out and "RepeatGroupDTO" in out
    assert "garminconnect" not in sys.modules
