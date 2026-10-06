"""
SP-285 / SP-286 / SP-287 (docs/research/carb-periodization.md §5 C-1…C-3): text-only carbohydrate
hints — the 減量期 week note for the A race's last 1–2 days, the 「課前要吃」 line on 強度課 and
≥ 2 h long runs, the race calculator's loading text. Synthetic.
"""
from datetime import date

from backend.engine import overview as O
from backend.engine import projection as PJ
from backend.engine.planning import Event
from backend.tests.test_b2b import _phases
from backend.tests.test_taper_rules import _week
from backend.tests.test_quality_gate import TODAY            # Wed 2026-09-30

RACE = "2026-10-17"                                         # Sat


def _ev(**kw):
    a = dict(id="e", name="測試賽", date=RACE, kind="race", priority="A", distance_km=30, climbing_m=2000,
             est_hours=5.0)
    a.update(kw)
    return Event(**a)


def _tc(e):
    return {"race": e.name, "id": e.id, "start": e.date, "taper_start": "2026-10-03", "days": 14,
            "trail": e.kind != "road", "sore": False, "long_days": 7}


def _note(e, monday, weight=None):
    return O.carb_load_note(_tc(e), e, weight, date.fromisoformat(monday))


# ---- SP-285: the 減量期 week note --------------------------------------------------------------

def test_long_race_carb_load_with_and_without_weight():
    e = _ev()
    n = _note(e, "2026-10-12", 60.0)
    assert n["level"] == "info" and n["src"] == "carb_load"
    t = n["text"]
    assert "前 1 天（可分 2 天）" in t and "10–12 g/kg" in t and "600–720 g" in t and "60 kg" in t
    assert "低纖、低脂" in t and "總熱量也要跟著多" in t and "1–2 kg" in t and "說明，推估" in t
    assert "Bussau 2002" in t and "ISSN 2019" in t and "Hawley 1997" in t
    t0 = _note(e, "2026-10-12")["text"]
    assert "10–12 g/kg" in t0 and " g）" not in t0 and "kg：" not in t0


def test_only_the_week_of_the_last_one_or_two_days():
    e = _ev()
    assert _note(e, "2026-10-05", 60.0) is None                              # the week before
    # a Tuesday race: day −1 is Monday (race week), day −2 the Sunday before → both weeks
    tue = _ev(date="2026-10-20")
    assert _note(tue, "2026-10-19") and _note(tue, "2026-10-12")
    # a Monday race: both days in the week before, none in the race week
    mon = _ev(date="2026-10-19")
    assert _note(mon, "2026-10-12") and _note(mon, "2026-10-19") is None
    # ≤ 90 min: only the day before counts
    road_tue = _ev(date="2026-10-20", kind="road", distance_km=10, climbing_m=0, est_hours=0.8)
    assert _note(road_tue, "2026-10-19") and _note(road_tue, "2026-10-12") is None


def test_road_10k_has_no_carb_load():
    e = _ev(kind="road", distance_km=10, climbing_m=20, est_hours=0.75)
    for w in (None, 60.0):
        t = _note(e, "2026-10-12", w)["text"]
        assert "10–12" not in t and "負荷" not in t and "可分 2 天" not in t
        assert "正常高碳水" in t and "6 g/kg" in t and "Hawley 1997" in t
    assert "360 g" in _note(e, "2026-10-12", 60.0)["text"]


def test_baiyue_has_no_supercompensation():
    e = _ev(kind="baiyue", days=3, est_hours=None, distance_km=30, climbing_m=2500)
    t = _note(e, "2026-10-12", 60.0)["text"]
    assert "前一晚正常吃" in t and "超補" not in t and "負荷" not in t and "g/kg" not in t
    assert _note(e, "2026-10-05", 60.0) is None


def test_no_note_without_the_race_or_its_time():
    e = _ev()
    assert O.carb_load_note(None, e, 60.0, date(2026, 10, 12)) is None
    assert O.carb_load_note(_tc(e), None, 60.0, date(2026, 10, 12)) is None
    assert O.carb_load_note(_tc(e), _ev(id="other"), 60.0, date(2026, 10, 12)) is None
    assert _note(_ev(est_hours=None, distance_km=None), "2026-10-12") is None


def test_week_plan_and_projection_show_it_in_the_race_week():
    # A race Sat 10/10 (5 h): this week (9/28–10/4) is the taper, the race week (10/5) has days −1, −2
    ds, plan, wp = _week("2026-10-10")
    assert not any(n.get("src") == "carb_load" for n in wp["notes"])
    weeks = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18), events=plan.events, weight=60.0)
    race_wk = next(w for w in weeks if w["start"] == "2026-10-05")
    n = next(n for n in race_wk["notes"] if n.get("src") == "carb_load")
    assert "10–12 g/kg" in n["text"] and "600–720 g" in n["text"]
    # the plan itself is untouched (text only): same sessions / TSS as without the note's weight
    again = PJ.project_weeks(wp, _phases(plan, TODAY), date(2026, 10, 18), events=plan.events)
    a = next(w for w in again if w["start"] == "2026-10-05")
    assert a["sessions"] == race_wk["sessions"] and a["tss"] == race_wk["tss"]
    assert "600–720 g" not in next(n for n in a["notes"] if n.get("src") == "carb_load")["text"]


def test_week_plan_this_week_uses_the_settings_weight():
    ds, plan, wp = _week("2026-10-03")                       # Sat: days −1, −2 in this week
    n = next(n for n in wp["notes"] if n.get("src") == "carb_load")
    w = plan.weight_on(TODAY)
    assert w and f"{w:.0f} kg" in n["text"] and "10–12 g/kg" in n["text"]


# ---- SP-286: 「課前要吃」 on 強度課 and ≥ 2 h long runs (app text only) ----------------------------

PRE = "課前 1–4 小時吃含碳水的一餐或點心，不要空腹做"


def _row(kind, minutes, state="active", **kw):
    return {"uid": f"u-{kind}-{minutes}", "week_start": "2026-10-05", "kind": kind, "title": "x", "minutes": minutes,
            "target": "", "detail": "暖身 15 分", "source": "", "day": "2026-10-07", "state": state, **kw}


def test_pre_meal_on_quality_and_long_runs_only():
    from backend.engine import session_fuel as SF
    for r in (_row("quality", 50), _row("long", 120), _row("mountain", 150)):
        t = SF.pre_meal(r)["text"]
        assert PRE in t and "Aird 2018" in t and "Mata 2019" in t and "Impey 2018" in t and "g/kg" not in t
    for r in (_row("easy", 60), _row("easy", 150), _row("long", 110), _row("strength", 40), _row("rest", 0),
              _row("quality", 50, state="done"), _row("long", 150, state="missed")):
        assert SF.pre_meal(r) is None, r["kind"]


def test_pre_meal_is_on_the_view_but_never_pushed():
    from backend.api import plan_sessions as API
    from backend.engine import plan_store as PS

    class Prov:
        def status_of(self, *a):
            return {"status": "new"}

        def row_view(self, r):
            return {}

    th = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}
    q, e = _row("quality", 50), _row("easy", 45)
    vq = API._view(q, {"thresholds": th}, {}, "2026-10-06", Prov())
    assert PRE in vq["pre_meal"]["text"] and vq["detail"] == "暖身 15 分"
    assert API._view(e, {"thresholds": th}, {}, "2026-10-06", Prov())["pre_meal"] is None
    # the watch gets the same session as before: no pre_meal, the detail unchanged
    p = PS.push_dict(vq)
    assert "pre_meal" not in p and p["detail"] == "暖身 15 分" and p == PS.push_dict(q)
    assert PRE not in str(p)
