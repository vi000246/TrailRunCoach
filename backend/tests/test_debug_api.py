"""
Debug API endpoints (SP-371, backend/api/debug.py): /debug/day agrees with the 課表 page,
/debug/thresholds lists each threshold's history and source, /debug/export/config fills every
block, every endpoint is read-only (the plan / change log / push / settings tables unchanged, no
COROS call) and no answer carries a credential. Synthetic plan inputs, in-memory DB, fake COROS
(test_plan_store.Env through debug_fixtures.debug_env); no dataset (the activity view with a real
FIT dataset is test_debug_integration.py).
"""
import datetime as dt
import json
from datetime import date

import pytest
from sqlalchemy import text

from backend.api import plan_sessions
from backend.db.models import (ActivityTag, EventGpx, InjuryEvent, PlanChangeLog, SyncState, WorkoutFile)
from backend.engine import planning
from backend.settings import secrets
from backend.settings.repository import SettingsRepository
from backend.sync import coros_workouts as CW
from backend.tests.debug_fixtures import ALL_SCOPES, DBG, bearer, debug_env, enable, make_token
from backend.tests.test_plan_match import _week, act
from backend.tests.test_plan_store import API, g, run

READ_ONLY_TABLES = ("plan_sessions", "plan_change_log", "coros_plan_push", "plan_week_snapshots", "user_settings",
                    "activity_tags", "injury_events", "workout_files", "sync_state", "athlete_settings")


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 2))


def _extras(monkeypatch, acts):
    monkeypatch.setattr(plan_sessions, "_range_extras", lambda s, t: {
        "activities": [x for x in acts if s <= x["date"] <= t], "phases": []})


def _snapshot(db) -> dict:
    out = {}
    for t in READ_ONLY_TABLES:
        out[t] = [tuple(r) for r in run(db.execute(text(f'SELECT * FROM "{t}" ORDER BY 1'))).all()]
    return out


def _lsd_day(e, monkeypatch):
    """The 10/1 LSD 50′ (stored on 9/30) and a 43-minute run synced on 10/1, not matched yet."""
    _week(e, [g("long", "long", "LSD", 50, "2026-10-01"), g("easy1", "easy", "輕鬆跑", 45, "2026-10-03")],
          monkeypatch)
    a = act(41, "2026-10-01", 43)
    e.inp["activities"] = [a]
    _extras(monkeypatch, [a])
    return a


# ---------------------------------------------------------------- /debug/day
def test_day_pairs_the_lsd_with_the_43_minute_run_like_the_plan_page(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ALL_SCOPES)
        _lsd_day(e, monkeypatch)
        before = _snapshot(e.db)
        r = e.c.get(f"{DBG}/day?date=2026-10-01", headers=bearer(tok))
        assert r.status_code == 200, r.text
        body = r.json()
        assert _snapshot(e.db) == before                      # the match was computed, not stored
        [s] = body["sessions"]
        assert s["kind"] == "long" and s["minutes"] == 50 and s["activity"]["index"] == 41
        assert s["activity"]["match"] == "day" and s["status"] == "done"
        assert s["compliance"]["duration_pct"] == 86 and s["compliance"]["label"] == "符合計畫"
        assert "配對方式：同一天" in s["why"] and "時間 86%" in s["why"]
        assert body["unmatched_activities"] == []
        assert body["meta"]["schema_version"] == 1 and body["meta"]["app_version"]
        assert "elapsed_ms" in body["meta"] and body["source"]
        # the 課表 page (calendar: it stores the match on this load) shows the same
        cal = e.c.get(f"{API}/calendar?start=2026-10-01&end=2026-10-01").json()
        cs = next(x for x in cal["sessions"] if x["uid"] == s["uid"])
        assert cs["vs"] == s["vs"] and cs["compliance"] == s["compliance"]
        assert cs["done_by"]["index"] == 41


def test_day_says_why_an_activity_stayed_unmatched(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ALL_SCOPES)
        _week(e, [g("long", "long", "LSD", 50, "2026-10-01")], monkeypatch)
        a, b = act(41, "2026-10-01", 43), act(42, "2026-09-30", 30, cat="bike")
        e.inp["activities"] = [a, b]
        _extras(monkeypatch, [a, b])
        body = e.c.get(f"{DBG}/day?date=2026-09-30", headers=bearer(tok)).json()
        assert body["sessions"] == []
        [u] = body["unmatched_activities"]
        assert u["index"] == 42 and "那天沒有排課" in u["why"]


# ---------------------------------------------------------------- /debug/thresholds
def test_thresholds_list_both_lthr_rows_with_their_sources(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ["read:plan"])
        p = planning.Plan(thresholds=[
            planning.Threshold(date="2026-08-01", lthr=155.0, lthr_method="estimate", note="LTHR 自動估算"),
            planning.Threshold(date="2026-09-15", lthr=160.0, lthr_method="manual", note="手動改")])
        p.save(planning.plan_path())
        body = e.c.get(f"{DBG}/thresholds?date=2026-10-01", headers=bearer(tok)).json()
        rows = body["history"]["plan"]["lthr"]
        assert [(r["date"], r["value"], r["method"], r["source"]) for r in rows] == [
            ("2026-08-01", 155.0, "estimate", "估算"), ("2026-09-15", 160.0, "manual", "手動")]
        eff = body["in_effect"]["lthr"]
        assert eff["value"] == 160.0 and eff["as_of"] == "2026-09-15" and eff["source"] == "手動輸入 2026-09-15"
        assert eff["why"]
        # before the manual change: the applied estimate
        body = e.c.get(f"{DBG}/thresholds?date=2026-09-01", headers=bearer(tok)).json()
        assert body["in_effect"]["lthr"]["value"] == 155.0
        assert body["in_effect"]["lthr"]["source"].startswith("自動估算")
        assert body["dataset_error"] and "cp" in body["history"]["plan"]


# ---------------------------------------------------------------- /debug/export/config
def _athlete_data(e, tmp_path):
    p = planning.Plan(profile={"sex": "female", "height_cm": 165},
                      thresholds=[planning.Threshold(date="2026-08-01", lthr=160.0, cp=250.0, aethr=140.0,
                                                     mhr=190.0)],
                      weights=[planning.Weight(date="2026-08-01", kg=55.0)])
    ev = p.upsert_event({"name": "合成越野 50K", "date": "2026-12-01", "priority": "A", "kind": "trail",
                         "distance_km": 50})
    p.save(planning.plan_path())
    repo = SettingsRepository(e.db)
    for k, v in {"athlete.experience": {"runs_per_week": 4, "minutes_per_run": 45, "longest_min": 120,
                                        "can_run_30": True, "at": "2026-09-01T08:00:00"},
                 "athlete.primary_sport": "trail", "sync.primary_source": "coros",
                 "athlete.race_results": [{"date": "2026-05-01", "distance_km": 10.0, "time_s": 2700,
                                           "trail": False, "source": "manual", "confirmed": True}],
                 "plan.blackouts": [{"id": "b1", "start": "2026-11-01", "end": "2026-11-03", "label": "出差"}],
                 "altitude.nights": [{"day": "2026-09-20", "m": 3000}],
                 "plan.prefs.runs_per_week": 5, "plan.auto.push_days": 5,
                 "backup.dir": str(tmp_path / "SENTINEL-BACKUP-DIR"),
                 "plan.calendar": {"token": "SENTINELcalendarFeedToken0123", "origin": None}}.items():
        run(repo.set(k, v))
    e.db.add(InjuryEvent(athlete_id=1, area="knee", onset_date="2026-07-01", status="resolved", note="合成"))
    e.db.add(ActivityTag(athlete_id=1, start_local="2026-09-29T20:00", activity_type="race", effort="all_out",
                         activity_type_overridden=True, effort_overridden=True, pain=1, exclusion="keep"))
    e.db.add(WorkoutFile(athlete_id=1, file_path=str(tmp_path / "fit" / "coros" / "123_2026-09-29_run.fit"),
                         file_format="fit", workout_date=date(2026, 9, 29), trail_classification="trail",
                         classification_overridden=True))
    e.db.add(EventGpx(event_id=ev.id, filename="course.gpx", km=50.0))
    run(e.db.commit())
    return ev


def test_export_config_fills_every_block(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ["export:config"])
        ev = _athlete_data(e, tmp_path)
        r = e.c.get(f"{DBG}/export/config", headers=bearer(tok))
        assert r.status_code == 200
        body = r.json()
        assert body["schema_version"] == 1 and body["exported_at"]
        blocks = body["blocks"]
        for name in ("profile", "data_source", "races", "calendar", "plan_prefs", "auto", "advanced", "thresholds",
                     "events", "injuries", "activity_overrides"):
            b = blocks[name]
            assert any(b.get(k) for k in ("keys", "rows", "tags", "terrain")), name
        assert blocks["profile"]["keys"]["athlete.primary_sport"] == "trail"
        assert blocks["profile"]["plan_profile"]["sex"] == "female" and blocks["profile"]["weights"][0]["kg"] == 55.0
        assert blocks["plan_prefs"]["keys"]["plan.prefs.runs_per_week"] == 5
        assert blocks["auto"]["keys"]["plan.auto.push_days"] == 5
        assert any(k.startswith("athlete.calib.") or k.startswith("debug.") for k in blocks["advanced"]["keys"])
        assert blocks["thresholds"]["rows"][0]["lthr"] == 160.0
        [e1] = blocks["events"]["rows"]
        assert e1["id"] == ev.id and e1["gpx_uploaded"] is True and e1["priority"] == "A"
        assert blocks["injuries"]["rows"][0]["area"] == "knee"
        assert blocks["activity_overrides"]["tags"][0]["effort"] == "all_out"
        assert blocks["activity_overrides"]["terrain"] == [{"file": "123_2026-09-29_run.fit", "date": "2026-09-29",
                                                           "terrain": "trail"}]
        flat = json.dumps(body, ensure_ascii=False)
        assert "backup.dir" not in flat and "plan.calendar" not in flat
        assert "SENTINEL" not in flat and str(tmp_path) not in flat


# ---------------------------------------------------------------- read-only + no secrets
def _every_call(tok):
    return [f"{DBG}{p}" for p in (
        "/activity?date=2026-10-01", "/activity?label=LSD", "/activity?id=1", "/activity?date=2026-10-01&gps=1",
        "/plan", "/plan?from=2026-09-28&to=2026-10-18", "/day?date=2026-10-01", "/day?date=2026-09-29",
        "/thresholds?date=2026-10-01", "/sync?lines=50", "/export/config")]


def test_every_endpoint_is_read_only_and_never_calls_coros(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ALL_SCOPES)
        _lsd_day(e, monkeypatch)
        assert e.c.post(f"{API}/push-coros?scope=week").status_code == 200       # coros_plan_push rows
        e.db.add(PlanChangeLog(athlete_id=1, trigger="sync", status="pending", summary="合成提議", items_json="[]",
                               created_at=dt.datetime(2026, 10, 1, 8)))
        run(e.db.commit())
        _athlete_data(e, tmp_path)
        before, calls = _snapshot(e.db), len(e.fake.calls)
        assert before["coros_plan_push"] and before["plan_sessions"] and before["plan_change_log"]
        for u in _every_call(tok):
            r = e.c.get(u, headers=bearer(tok))
            assert r.status_code == 200, (u, r.text[:300])
        assert _snapshot(e.db) == before
        assert len(e.fake.calls) == calls                       # no COROS request
        plan = e.c.get(f"{DBG}/plan?from=2026-09-28&to=2026-10-04", headers=bearer(tok)).json()
        assert plan["auto"]["pending"]["summary"] == "合成提議"
        assert any(s["kind"] == "long" for s in plan["stored"]["sessions"])
        assert plan["stored"]["push"]["rows_in_range"] and plan["generator"]["weeks"]
        assert "changes" in plan["would_change"] and plan["gates"]["this_week"]["start"] == "2026-09-28"


SECRET_WORDS = ("password", "passwd", "access_token", "refresh_token", "cookie", "sealed", "token_hash", "secret")


def _keys(x, out):
    if isinstance(x, dict):
        for k, v in x.items():
            out.append(str(k).lower())
            _keys(v, out)
    elif isinstance(x, list):
        for v in x:
            _keys(v, out)
    return out


def test_no_answer_carries_a_credential_or_a_position(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        tok = make_token(e, ALL_SCOPES)
        _lsd_day(e, monkeypatch)
        _athlete_data(e, tmp_path)
        plain = {"tp_access_token": "SENTINEL-TP-ACCESS", "tp_refresh_token": "SENTINEL-TP-REFRESH",
                 "tp_web_cookie": "SENTINEL-TP-COOKIE", "coros_password_sealed": "SENTINEL-COROS-PW",
                 "tp_password_sealed": "SENTINEL-TP-PW"}
        sealed = {k: secrets.seal(v) for k, v in plain.items()}
        st = run(e.db.get(SyncState, 1))
        for k, v in sealed.items():
            setattr(st, k, v)
        st.last_sync_cursor = "cursor-1"
        run(e.db.commit())
        tok2 = make_token(e, ["read:sync"], name="other")
        forbidden = list(plain.values()) + list(sealed.values()) + [
            tok, tok2, tok[5:], st.coros_access_token, "SENTINEL", str(tmp_path / "SENTINEL-BACKUP-DIR")]
        from backend import debug_auth as DA
        forbidden += [DA.hash_token(tok), DA.hash_token(tok2)]
        for u in _every_call(tok):
            r = e.c.get(u, headers=bearer(tok))
            assert r.status_code == 200, u
            for f in forbidden:
                assert f not in r.text, (u, f[:24])
            keys = _keys(r.json(), [])
            assert not [k for k in keys if any(w in k for w in SECRET_WORDS)], u
            assert "backup.dir" not in keys and "plan.calendar" not in keys
            if "gps=1" not in u:
                assert not {"lat", "lon", "latitude", "longitude"} & set(keys), u
        sync = e.c.get(f"{DBG}/sync", headers=bearer(tok)).json()
        assert sync["state"]["tp_cursor"] == "cursor-1"


def test_scrub_drops_positions_and_credentials():
    from backend.engine import debug_view as DV
    x = {"a": 1, "lat": 25.0, "nested": [{"longitude": 121.0, "password": "p", "ok": "trcd_abc", "z": "gAAAAAxx"}],
         "coros_access_token": "t", "tokens": 3}
    assert DV.scrub(x) == {"a": 1, "nested": [{"ok": "[redacted]", "z": "[redacted]"}], "tokens": 3}
    assert DV.scrub(x, gps=True)["lat"] == 25.0
    import numpy as np
    assert DV.jsonable({"v": np.float64(float("nan")), "a": np.arange(2), "d": date(2026, 1, 2)}) == \
        {"v": None, "a": [0, 1], "d": "2026-01-02"}


def test_the_cli_builds_the_calls_and_sends_only_the_bearer_header(monkeypatch, capsys):
    from backend.scripts import debug_fetch as F
    seen = []
    monkeypatch.setattr(F, "fetch", lambda url, token, timeout=120.0: seen.append((url, token)) or {"ok": 1})
    monkeypatch.setenv("TRC_DEBUG_URL", "https://x.example/")
    monkeypatch.setenv("TRC_DEBUG_TOKEN", "trcd_test")
    assert F.main(["day", "2026-10-05"]) == 0
    assert F.main(["activity", "--date", "2026-10-05", "--streams", "hr,speed", "--every", "30", "--gps"]) == 0
    assert F.main(["plan", "--from", "2026-09-29", "--to", "2026-10-12"]) == 0
    assert F.main(["export"]) == 0
    assert [u for u, _t in seen] == [
        "https://x.example/api/v1/debug/day?date=2026-10-05",
        "https://x.example/api/v1/debug/activity?date=2026-10-05&streams=hr%2Cspeed&every=30&gps=1",
        "https://x.example/api/v1/debug/plan?from=2026-09-29&to=2026-10-12",
        "https://x.example/api/v1/debug/export/config"]
    assert {t for _u, t in seen} == {"trcd_test"} and '"ok": 1' in capsys.readouterr().out
    monkeypatch.delenv("TRC_DEBUG_TOKEN")
    assert F.main(["sync"]) == 2
