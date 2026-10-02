"""Real-data half of backend/tests/test_cp_protocols.py (moved out of the default run: it reads
the athlete's WKO5 folder). Opt-in: see backend/tests/realdata/README.md."""
import copy
from dataclasses import replace
import pytest
from backend.engine import cp_protocols as CPP
from backend.engine import plan_prefs as PP


@pytest.mark.golden
@pytest.mark.parametrize("proto", CPP.PROTOCOLS)
def test_week_plan_builds_the_test_by_protocol(proto):
    from backend.settings.paths import athlete_dir
    if not any(athlete_dir().glob("*.wko5athlete")):
        pytest.skip("no WKO5 athlete folder")
    from backend.api.overview import _dataset, _status
    from backend.engine import overview as O
    from backend.engine import status as ST
    ds = _dataset()
    today = O.day_to_date(ds.today)
    st = _status(ds, today)
    st2 = copy.copy(st)
    st2.kind = "base"
    st2.goals = {**st.goals, "days_to_next_a": None}
    act = f"{CPP.NOTE_RACE}（課表偏好：用比賽）" if proto == "race" else "排一次 CP 測試"
    # CP overdue: the level and i_testing's cp_due (week_plan schedules the CP test on
    # cp_due since the 間歇門檻 change — a missing AeT / LTHR alone doesn't)
    st2.indicators = [replace(i, level=ST.BAD, action=act, extra={**i.extra, "cp_due": True}) if i.id == "testing"
                      else i for i in st.indicators]
    wp = O.week_plan(ds, st2, today, prefs=PP.Prefs(cp_test_protocol=proto))
    if wp["mode"] == "recovery_week":
        pytest.skip("a recovery week has no test")
    # CP tests only: the AeT test (its own reason and protocol, engine/aet_test.py) may be due too
    # a due test is suggested (test_suggestions), never put into the plan (the user, 2026-10-01)
    assert not [s for s in wp["sessions"] if s["kind"] == "test"]
    tests = [x for x in wp["test_suggestions"] if x["kind"] == "cp"]
    if proto == "race":
        assert not tests
        assert any(CPP.NOTE_RACE in n["text"] for n in wp["notes"])            # the note instead
    else:
        (t,) = tests
        assert t["protocol"] == proto and t["minutes"] == CPP.TABLE[proto]["minutes"]
        assert t["title"] == CPP.TABLE[proto]["title"]
    # within 10 days of an A race: no test
    st3 = copy.copy(st2)
    st3.goals = {**st.goals, "days_to_next_a": 5}
    assert not [s for s in O.week_plan(ds, st3, today, prefs=PP.Prefs(cp_test_protocol=proto))["sessions"]
                if s["kind"] == "test"]
