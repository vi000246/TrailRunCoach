"""SP-856: the 總覽 熱適應 card for a hot A/B race within 30 days with a race-day S < 75 % is 黃 (watch),
not 紅 (bad) — since SP-569 there is no 熱適應課 to do, only the race-day reminder. With and without the
per-activity weather; no hot race → as before (S 0 = info)."""
import pytest

from backend.engine import heat_data as HD
from backend.engine import plan_prefs as PP
from backend.tests.test_heat_auto_sp569 import _hot_a


def _card(monkeypatch, weather: bool, race: bool):
    from backend.engine.status import Status
    from backend.tests.test_b2b import _history, _plan_with
    from backend.tests.test_quality_gate import TODAY
    if weather:
        monkeypatch.setattr(HD, "exposures", lambda root=None: ([], {"missing": False}))
    ds = _history(TODAY)
    plan = _plan_with("2027-06-01", 1, TODAY)
    plan.events = [_hot_a("2026-10-18")] if race else []
    ds.plan = plan
    return Status(ds, plan, TODAY, prefs=PP.Prefs()).i_heat()


@pytest.mark.parametrize("weather", [True, False])
def test_a_hot_race_with_a_low_race_day_s_is_watch_not_bad(monkeypatch, weather):
    ind = _card(monkeypatch, weather, race=True)
    assert ind.level == "watch", (ind.level, ind.verdict)
    assert "比賽當天配速放慢" in ind.action and "賽事計算機" in ind.action


def test_no_hot_race_is_unchanged(monkeypatch):
    ind = _card(monkeypatch, True, race=False)
    assert ind.level == "info" and not ind.action
