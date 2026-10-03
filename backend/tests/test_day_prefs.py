"""偏好的星期 per session type (engine/plan_prefs.py): placement, every conflict type, the fallback."""
import datetime as dt

import pytest

from backend.engine import plan_prefs as PP
from backend.settings import repository as SR

MON = dt.date(2026, 10, 5)
WEEK = [MON + dt.timedelta(days=i) for i in range(7)]


def _ss(*extra):
    base = [{"id": "long", "kind": "long", "title": "LSD", "minutes": 120, "day": None},
            {"id": "quality", "kind": "quality", "title": "閾值 3×8 分", "minutes": 45, "day": None},
            {"id": "easy1", "kind": "easy", "title": "輕鬆跑＋坡道衝刺 8×10 秒", "minutes": 45, "day": None},
            {"id": "easy2", "kind": "easy", "title": "輕鬆跑", "minutes": 45, "day": None}]
    return base + list(extra)


def _day(ss, sid):
    return dt.date.fromisoformat(next(s for s in ss if s["id"] == sid)["day"]).weekday()


def test_round_trip_and_validation():
    p = PP.from_body({"pref_days": {"quality": [0, 2], "strides": [4]}, "long_day": "thu"})
    assert p.pref_of("quality") == (0, 2) and p.pref_of("strides") == (4,) and p.pref_of("cp_test") == ()
    assert PP.long_weekday(p, 5) == 3 and p.active
    assert PP.from_settings(p.settings()) == p
    SR.validate("plan.prefs.pref_days", {"quality": [1]})
    for bad in ({"long": [1]}, {"quality": [1, 1]}, {"quality": [7]}, {"quality": [1, 2, 3]}):
        with pytest.raises(ValueError):
            SR.validate("plan.prefs.pref_days", bad)
    SR.validate("plan.prefs.long_day", "thu")


def test_the_preferred_days_are_used_first():
    p = PP.Prefs(long_day="sat", pref_days=(("quality", (0,)), ("strides", (3,))))
    ss = _ss()
    PP.place(ss, WEEK, PP.long_weekday(p, 5), p, notes=[])
    assert _day(ss, "long") == 5 and _day(ss, "quality") == 0 and _day(ss, "easy1") == 3


def test_a_done_hard_day_keeps_the_interval_48h_away():
    # a 高強度長跑 / unplanned Z5 run on Monday (overview passes it as hard_done): no interval on Tue
    p = PP.Prefs(long_day="sat")
    ss = _ss()
    PP.place(ss, WEEK[1:], 5, p, notes=[], hard_done=[MON])
    assert _day(ss, "quality") not in (0, 1)
    ss2 = _ss()
    PP.place(ss2, WEEK[1:], 5, p, notes=[])
    assert _day(ss2, "quality") == 1            # without it: Tuesday (QUALITY_ORDER)


def test_second_choice_then_the_default_rules():
    # Monday is not a training day here: the second choice (Wednesday)
    days = (False, True, True, True, True, True, True)
    p = PP.Prefs(days=days, long_day="sat", pref_days=(("quality", (0, 2)),))
    ss = _ss()
    PP.place(ss, WEEK, 5, p, notes=[])
    assert _day(ss, "quality") == 2
    assert any(c["code"].startswith("not_allowed") for c in PP.day_conflicts(p))


@pytest.mark.parametrize("prefs, code, words", [
    (PP.Prefs(long_day="thu", pref_days=(("quality", (4,)),)), "after_long", "長跑（週四）的隔天"),
    (PP.Prefs(long_day="thu", pref_days=(("quality", (2,)),)), "gap48", "間歇和 LSD 只隔 1 天（建議 ≥ 2 天，台灣教練）：要改到週二嗎？"),
    (PP.Prefs(long_day="sun", pref_days=(("quality", (0,)),)), "after_long", "隔天"),
    (PP.Prefs(long_day="sat", quality=2, pref_days=(("quality", (1, 2)),)), "z5_twice", "只隔 1 天"),
    (PP.Prefs(long_day="thu", cap_weekday=45), "cap_long", "平日上限只有 45 分，LSD 放不下"),
    (PP.Prefs(long_day="sat", pref_days=(("aet_test", (6,)),)), "aet_weekday", "只排平日"),
    (PP.Prefs(long_day="sat", pref_days=(("aet_test", (4,)),)), "aet_gap", "太近"),
])
def test_every_conflict_is_named_with_its_rule_and_action(prefs, code, words):
    cs = PP.day_conflicts(prefs)
    c = next(c for c in cs if c["code"] == code)
    assert words in c["text"] and c["rule"] and c["source"] and c["action"] and not c["keep"]


def test_a_conflict_moves_the_session_unless_kept():
    # LSD Thursday + intervals Friday (the example): moved, with a week note naming the rule
    p = PP.Prefs(long_day="thu", pref_days=(("quality", (4,)),))
    ss, notes = _ss(), []
    PP.place(ss, WEEK, 3, p, notes=notes)
    q = _day(ss, "quality")
    assert q != 4 and PP._gap(q, 3) >= 2
    assert any("長跑（週四）的隔天" in n["text"] and "改排別天" in n["text"] for n in notes)
    kept = PP.Prefs(long_day="thu", pref_days=(("quality", (4,)),), pref_keep=("after_long",))
    ss = _ss()
    PP.place(ss, WEEK, 3, kept, notes=[])
    assert _day(ss, "quality") == 4                                   # 照我的偏好
    assert PP.day_conflicts(kept)[0]["keep"]


def test_one_session_type_per_weekday():
    p = PP.Prefs(long_day="sat", pref_days=(("quality", (5, 1)), ("strides", (1, 3)), ("cp_test", (3,))))
    assert PP.day_owners(p)[0] == ("long", 5)
    # priority: long, then PREF_KINDS order (quality, aet_test, cp_test, strides)
    assert PP.overlaps(p) == [{"kind": "quality", "wd": 5, "owner": "long"},
                              {"kind": "strides", "wd": 1, "owner": "quality"},
                              {"kind": "strides", "wd": 3, "owner": "cp_test"}]
    with pytest.raises(ValueError, match="週六已給LSD"):
        PP.check(p)
    # stored before the rule: the first type keeps the day, the later ones lose it (and are named)
    fixed, dropped = PP.drop_overlaps(p)
    assert fixed.pref_of("quality") == (1,) and fixed.pref_of("strides") == () and fixed.pref_of("cp_test") == (3,)
    assert fixed.long_day == "sat" and PP.overlaps(fixed) == [] and len(dropped) == 3
    PP.check(fixed)
    ok = PP.Prefs(long_day="sat", pref_days=(("quality", (1, 3)),))
    assert PP.drop_overlaps(ok) == (ok, []) and PP.overlaps(PP.Prefs(long_day="auto", pref_days=(("quality", (5,)),))) == []


def test_api_prefs_migrate_overlaps_and_check_unsaved(monkeypatch):
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_coros_workouts import run
    from backend.tests.test_plan_store import API, Env
    with Env(monkeypatch) as e:
        async def store():
            repo = SettingsRepository(e.db)
            await repo.set("plan.prefs.long_day", "sun")
            await repo.set("plan.prefs.pref_days", {"quality": [6, 2], "strides": [2]})
            await e.db.commit()
        run(store())
        got = e.c.get(f"{API}/prefs").json()
        assert got["prefs"]["long_day"] == "sun" and got["prefs"]["pref_days"] == {"quality": [2]}
        assert {(d["kind"], d["wd"], d["owner"]) for d in got["pref_dropped"]} == {("quality", 6, "long"), ("strides", 2, "quality")}
        bad = e.c.put(f"{API}/prefs", json={"long_day": "sun", "pref_days": {"quality": [6]}})
        assert bad.status_code == 400 and "週日已給LSD" in bad.text
        r = e.c.post(f"{API}/prefs/conflicts", json={"long_day": "thu", "pref_days": {"quality": [4]}}).json()
        assert [c["code"] for c in r["day_conflicts"]] == ["after_long"] and r["overlaps"] == []
        assert e.c.get(f"{API}/prefs").json()["prefs"]["long_day"] == "sun"      # nothing stored


def test_a_preferred_day_on_a_blackout_says_so():
    p = PP.Prefs(pref_days=(("quality", (1,)),))
    ns = PP.blocked_pref_notes(p, MON, {"2026-10-06": "出差"})
    assert ns and "週二（10/6）是不排課日期" in ns[0]["text"]
    ss = _ss()
    PP.place(ss, [d for d in WEEK if d.isoformat() != "2026-10-06"], 5, p, notes=[])
    assert _day(ss, "quality") != 1
