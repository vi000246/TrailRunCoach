"""
課表 page: GET /api/v1/overview/plan/calendar (month range over the stored plan)
and the page route. Mocked COROS, hand-built inputs (see test_plan_store.Env).
"""
from datetime import date

import pytest

from backend.api import plan_sessions
from backend.sync import coros_workouts as CW
from backend.tests.test_plan_store import API, ACT_929, Env


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


PHASES = [{"kind": "base", "label": "基礎期", "start": "2026-09-01", "end": "2026-10-11"},
          {"kind": "specific", "label": "專項期", "start": "2026-10-12", "end": "2026-11-15"}]
ACT_915 = {"index": 3, "date": "2026-09-15", "category": "trail", "category_label": "越野跑",
           "moving_s": 5400, "tss": 90}


def _extras(monkeypatch, calls=None):
    def fake(start, end):
        if calls is not None:
            calls.append((start, end))
        acts = [a for a in (ACT_915, ACT_929) if start <= a["date"] <= end]
        return {"activities": acts, "phases": [p for p in PHASES if p["end"] >= start and p["start"] <= end]}
    monkeypatch.setattr(plan_sessions, "_range_extras", fake)


def test_calendar_month_grid(monkeypatch):
    calls = []
    _extras(monkeypatch, calls)
    with Env(monkeypatch) as e:
        r = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-11-08")     # 6-week month grid
        assert r.status_code == 200
        b = r.json()
        assert calls == [("2026-09-28", "2026-11-08")]
        days = {s["day"] for s in b["sessions"]}
        assert {"2026-10-01", "2026-10-04", "2026-10-06", "2026-10-18"} <= days
        assert all("2026-09-28" <= d <= "2026-11-08" for d in days)
        done = [s for s in b["sessions"] if s["state"] == "done"]
        assert done and done[0]["done_by"]["index"] == ACT_929["index"]      # ✓ on the matched session
        assert [a["index"] for a in b["activities"]] == [11]
        assert [p["kind"] for p in b["phases"]] == ["base", "specific"]
        # one summary row per Monday, with the phase of the week
        w = b["week_rows"]
        assert [x["start"] for x in w] == ["2026-09-28", "2026-10-05", "2026-10-12", "2026-10-19",
                                           "2026-10-26", "2026-11-02"]
        assert w[0]["phase_label"] == "基礎期" and w[2]["phase_label"] == "專項期"
        assert w[0]["done_hours"] == pytest.approx(2400 / 3600) and w[0]["done_tss"] == 30
        # planned time excludes strength: 40 + 60 + 45 + 120 min
        assert w[0]["planned_hours"] == pytest.approx(265 / 60)
        assert w[2]["provisional"] is True and w[1]["provisional"] is False
        # dialog prefill and COROS state
        assert set(b["targets"]) >= {"easy", "long", "quality"} and "bpm" in b["targets"]["easy"]
        assert b["coros"]["authenticated"] is True and b["coros"]["last_pushed_at"] is None
        assert b["coros"]["not_pushed"] >= 5 and b["today"] == "2026-09-30"


def test_calendar_after_push_counts_outdated(monkeypatch):
    _extras(monkeypatch)
    with Env(monkeypatch) as e:
        e.c.post(f"{API}/push-coros?scope=week")
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").json()
        assert b["coros"]["last_pushed_at"] and b["coros"]["pushed"] == 3 and b["coros"]["outdated"] == 0
        long = next(s for s in b["sessions"] if s["day"] == "2026-10-04")
        e.c.patch(f"{API}/sessions/{long['uid']}", json={"minutes": 100})
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").json()
        assert b["coros"]["outdated"] == 1
        assert next(s for s in b["sessions"] if s["uid"] == long["uid"])["coros"]["status"] == "outdated"


def test_calendar_past_month_shows_history(monkeypatch):
    _extras(monkeypatch)
    with Env(monkeypatch) as e:
        b = e.c.get(f"{API}/calendar?start=2026-08-31&end=2026-10-11").json()
        assert [a["index"] for a in b["activities"]] == [3, 11]
        w = {x["start"]: x for x in b["week_rows"]}
        assert w["2026-09-14"]["done_tss"] == 90 and w["2026-09-14"]["planned_hours"] == 0


def test_calendar_not_logged_in(monkeypatch):
    _extras(monkeypatch)
    with Env(monkeypatch) as e:
        from backend.db.models import SyncState
        from backend.tests.test_coros_workouts import run
        run(e.db.execute(SyncState.__table__.delete()))
        run(e.db.commit())
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").json()
        assert b["coros"]["authenticated"] is False


def test_calendar_bad_ranges(monkeypatch):
    _extras(monkeypatch)
    with Env(monkeypatch) as e:
        assert e.c.get(f"{API}/calendar?start=2026-10-04&end=2026-09-28").status_code == 400
        assert e.c.get(f"{API}/calendar?start=nope&end=2026-09-28").status_code == 400
        assert e.c.get(f"{API}/calendar?start=2026-01-01&end=2026-12-31").status_code == 400
        assert e.c.get(f"{API}/calendar?start=2026-09-28").status_code == 422


def test_tss_rates_follow_the_generator():
    ss = [{"origin": "auto", "edited": False, "kind": "long", "minutes": 120, "tss": 132.0},
          {"origin": "auto", "edited": False, "kind": "quality", "minutes": 60, "tss": 70.0},
          {"origin": "auto", "edited": True, "kind": "easy", "minutes": 60, "tss": 999.0},     # edited: ignored
          {"origin": "custom", "edited": True, "kind": "hike", "minutes": 60, "tss": 0.0}]
    r = plan_sessions.tss_rates({"road": 52.0, "hike": 44.0, "strength": 24.0}, ss)
    assert r["long"] == 66.0 and r["quality"] == 70.0                 # the plan's own rates
    assert r["easy"] == 52.0 and r["hike"] == 44.0 and r["strength"] == 24.0 and r["test"] == 75.0
    assert plan_sessions.est_tss({"kind": "hike", "minutes": 90, "tss": 0.0}, r) == pytest.approx(66.0)
    assert plan_sessions.est_tss({"kind": "hike", "minutes": 90, "tss": 10.0}, r) == 10.0


def test_calendar_tss_estimates_and_saved_tss(monkeypatch):
    _extras(monkeypatch)
    with Env(monkeypatch) as e:
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").json()
        q = next(s for s in b["sessions"] if s["day"] == "2026-10-01")
        assert q["tss_est"] == pytest.approx(48.0)                      # the stored plan TSS
        assert b["tss_rates"]["quality"] == pytest.approx(48.0) and b["tss_rates"]["test"] == 75.0
        # a custom session without a TSS gets an estimate; one saved from the dialog keeps its value
        e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "easy", "minutes": 60})
        e.c.post(f"{API}/sessions", json={"day": "2026-10-03", "kind": "hike", "minutes": 60, "tss": 41.5})
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").json()
        cust = {s["kind"]: s for s in b["sessions"] if s["day"] == "2026-10-03"}
        assert cust["easy"]["tss"] == 0 and cust["easy"]["tss_est"] == pytest.approx(b["tss_rates"]["easy"])
        assert cust["hike"]["tss"] == 41.5 and cust["hike"]["tss_est"] == 41.5
        assert e.c.patch(f"{API}/sessions/{q['uid']}", json={"minutes": 30, "tss": 24}).json()["tss"] == 24
        assert e.c.patch(f"{API}/sessions/{q['uid']}", json={"tss": "x"}).status_code == 400
        assert e.c.patch(f"{API}/sessions/{q['uid']}", json={"tss": -1}).status_code == 400
        w = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").json()["week_rows"][0]
        assert w["planned_tss"] == pytest.approx(sum(s["tss_est"] for s in
                                                     e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-04").json()["sessions"]))


def test_session_compliance_levels():
    from backend.engine import compliance as C

    def done(kind="easy", minutes=60, tss=50.0, moving_s=3600, a_tss=50.0, cat="road"):
        return {"state": "done", "kind": kind, "minutes": minutes, "tss": tss,
                "done_by": {"moving_s": moving_s, "tss": a_tss, "category": cat}}
    assert C.session_compliance(done())["level"] == "green"
    assert C.session_compliance(done(a_tss=59.0))["level"] == "green"                 # +18 %
    c = C.session_compliance(done(moving_s=3600 * 0.7, a_tss=45.0))                   # time −30 % decides
    assert (c["level"], c["pct"], c["duration_pct"], c["tss_pct"]) == ("yellow", 90, 70, 90)
    assert C.session_compliance(done(a_tss=80.0))["level"] == "red"                   # +60 %
    w = C.session_compliance(done(cat="bike"))
    assert w["level"] == "red" and w["wrong_type"] and w["label"] == "類型不符"
    assert C.session_compliance(done(kind="long", cat="hike"))["level"] == "green"    # a mountain day counts
    assert C.session_compliance(done(tss=0.0), planned_tss=50.0)["tss_pct"] == 100    # estimate as the plan
    assert C.session_compliance({"state": "missed"})["level"] == "missed"
    assert C.session_compliance({"state": "active"}) is None
    assert C.week_compliance(100.0, 86.0, 5, 4)["pct"] == 86
    assert C.week_compliance(0.0, 0.0, 2.0, 1.0) == {"pct": 50, "level": "yellow"}   # no TSS: time


def test_calendar_compliance(monkeypatch):
    _extras(monkeypatch)
    with Env(monkeypatch) as e:
        b = e.c.get(f"{API}/calendar?start=2026-09-28&end=2026-10-11").json()
        d = next(s for s in b["sessions"] if s["state"] == "done")
        # planned 40 min / TSS 32, done 40 min / TSS 30
        assert d["compliance"]["level"] == "green" and d["compliance"]["tss_pct"] == 94
        assert all(s["compliance"] is None for s in b["sessions"] if s["state"] == "active")
        w0, w1 = b["week_rows"]
        assert w0["compliance"] == {"pct": 94, "level": "green"}        # 9/28–9/30: TSS 30 of 32 planned
        assert w1["compliance"] is None                                  # not started yet
        assert b["compliance_levels"] == {"green": 0.2, "yellow": 0.5}


def test_schedule_page_served(monkeypatch):
    with Env(monkeypatch) as e:
        r = e.c.get(f"{API}/schedule/page")
        assert r.status_code == 200 and 'data-page="schedule"' in r.text


def test_schedule_page_pull_button_and_status_legend(monkeypatch):
    """抓活動 (資料來源 → 這裡, shared syncrun.js) is a separate button from 推送到手錶;
    推送狀態 is a watch glyph, ✓ stays 完成, the legend has both groups."""
    import json
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "static"
    with Env(monkeypatch) as e:
        page = e.c.get(f"{API}/schedule/page").text
    assert 'id="pull-btn"' in page and 'id="pull-login"' in page and "syncrun.js" in page
    assert "TRCSync.run(PRI.source" in page and "TRCSync.primary()" in page and "autoPlanRefresh" in page
    assert 'data-i18n="schedule.push.btn">推送到手錶<' in page and "同步到 COROS" not in page
    assert 'tt("legend.done_h")' in page and 'tt("legend.watch_h")' in page
    assert 'ok: "已推送到 COROS"' not in page and "<circle cx=\"8\" cy=\"8\" r=\"7\"" not in page   # no green ✓ disc
    settings = (static / "settings.html").read_text("utf-8")
    assert "syncrun.js" in settings and "TRCSync.run(src" in settings and "getReader" not in settings
    js = (static / "syncrun.js").read_text("utf-8")
    assert "window.TRCSync" in js and "/primary" in js and "r.status === 409" in js
    for loc in ("zh-TW", "en"):
        cat = json.loads((static / "i18n" / loc / "schedule.json").read_text("utf-8"))
        assert all(cat.get(k) for k in ("pull.btn", "push.btn", "legend.done_h", "legend.watch_h", "sy.pushed"))
