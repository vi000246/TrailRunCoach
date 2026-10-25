"""SP-802: writes through the debug API (engine/debug_write.py, api/debug.py /write/*): a whitelist
(thresholds, events, 課表偏好), preview → apply, a log with what each write replaced, undo; a
write:config token is never a default and lasts ≤ 7 days; sign-in mode opens the POSTs for the owner's
token only. Synthetic data, in-memory DB (debug_fixtures.debug_env), plan.json in tmp_path."""
import pytest

from backend import debug_auth as DA
from backend.engine import debug_write as DW
from backend.engine import planning as P
from backend.tests.debug_fixtures import ADMIN, DBG, bearer, debug_env, enable, make_token

W = f"{DBG}/write"


@pytest.fixture(autouse=True)
def _fresh():
    DW.reset()
    yield
    DW.reset()


def _plan(rows=None, events=None):
    plan = P.Plan.load()
    plan.thresholds = [P.Threshold(**r) for r in (rows or [{"date": "2026-10-05", "lthr": 155.0, "cp": 204.0}])]
    plan.events = [P.Event(**e) for e in (events or [])]
    plan.save()


def _row(date):
    return next(t for t in P.Plan.load().thresholds if t.date == date)


def _writer(e):
    enable(e)
    return make_token(e, ["read:plan", DA.WRITE_SCOPE], days=7, name="claude")


LTHR_166 = [{"target": "threshold", "date": "2026-10-05", "set": {"lthr": 166}}]


# ---------------------------------------------------------------- the token
def test_a_write_token_is_never_a_default_and_lasts_at_most_7_days(monkeypatch, tmp_path):
    assert DA.WRITE_SCOPE not in DA.DEFAULT_SCOPES
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        for days in (0, 30, 90, 365):
            r = e.c.post(f"{ADMIN}/tokens", json={"scopes": [DA.WRITE_SCOPE], "days": days})
            assert r.status_code == 400 and "7 days" in r.text, days
        assert e.c.post(f"{ADMIN}/tokens", json={"scopes": [DA.WRITE_SCOPE], "days": 7}).status_code == 200
        st = e.c.get(ADMIN).json()
        assert st["write_scope"] == DA.WRITE_SCOPE and st["write_max_days"] == 7 and st["writes"] == []


def test_without_write_config_nothing_can_be_written(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        enable(e)
        _plan()
        tok = make_token(e, ["read:plan", "export:config"], days=0)
        for path, body in (("/preview", {"changes": LTHR_166}), ("/apply", {"preview_id": "pv_x"}), ("/undo", {})):
            r = e.c.post(W + path, json=body, headers=bearer(tok))
            assert r.status_code == 403 and r.json()["detail"]["code"] == "SCOPE", path
        assert e.c.get(f"{W}/log", headers=bearer(tok)).status_code == 403
        assert e.c.post(f"{W}/preview", json={"changes": LTHR_166}).status_code == 401      # no token at all
        assert _row("2026-10-05").lthr == 155.0


# ---------------------------------------------------------------- preview → apply → undo
def test_preview_writes_nothing_apply_writes_it_undo_puts_it_back(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan()
        tok = _writer(e)
        r = e.c.post(f"{W}/preview", json={"changes": LTHR_166}, headers=bearer(tok))
        assert r.status_code == 200, r.text
        pv = r.json()
        d = {(x["key"], x["field"]): (x["before"], x["after"]) for x in pv["diff"]}
        assert d[("2026-10-05", "lthr")] == (155.0, 166.0)
        assert d[("2026-10-05", "lthr_method")] == (None, "manual")          # typed by hand
        assert pv["preview_id"] and pv["expires_in_s"] == DW.PREVIEW_TTL_S
        assert _row("2026-10-05").lthr == 155.0                               # a preview writes nothing
        r = e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(tok))
        assert r.status_code == 200, r.text
        assert r.json()["change_id"] == 1
        row = _row("2026-10-05")
        assert row.lthr == 166.0 and row.lthr_method == "manual" and row.cp == 204.0      # the rest untouched
        # once only
        r = e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(tok))
        assert r.status_code == 404 and r.json()["detail"]["code"] == "NO_PREVIEW"
        # the log: the debug API has before / after, the settings page the diff only
        lg = e.c.get(f"{W}/log", headers=bearer(tok)).json()["writes"]
        assert lg[0]["id"] == 1 and lg[0]["by"] == "claude" and lg[0]["before"]["thresholds"][0]["lthr"] == 155.0
        page = e.c.get(ADMIN).json()["writes"]
        assert page[0]["id"] == 1 and "before" not in page[0] and page[0]["diff"]
        # undo
        r = e.c.post(f"{W}/undo", json={}, headers=bearer(tok))
        assert r.status_code == 200 and r.json()["undone"] == 1
        assert _row("2026-10-05").lthr == 155.0 and _row("2026-10-05").lthr_method is None
        page = e.c.get(ADMIN).json()["writes"]
        assert page[0]["kind"] == "undo" and page[0]["undoes"] == 1 and page[1]["undone_at"]
        r = e.c.post(f"{W}/undo", json={}, headers=bearer(tok))
        assert r.status_code == 404 and r.json()["detail"]["code"] == "NOTHING_TO_UNDO"


def test_apply_refuses_when_the_data_changed_after_the_preview(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan()
        tok = _writer(e)
        pv = e.c.post(f"{W}/preview", json={"changes": LTHR_166}, headers=bearer(tok)).json()
        _plan([{"date": "2026-10-05", "lthr": 158.0, "cp": 204.0}])          # changed on the settings page meanwhile
        r = e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(tok))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "CHANGED"
        assert _row("2026-10-05").lthr == 158.0


def test_undo_refuses_when_the_data_changed_after_the_write(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan()
        tok = _writer(e)
        pv = e.c.post(f"{W}/preview", json={"changes": LTHR_166}, headers=bearer(tok)).json()
        assert e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(tok)).status_code == 200
        _plan([{"date": "2026-10-05", "lthr": 170.0, "cp": 204.0}])
        r = e.c.post(f"{W}/undo", json={}, headers=bearer(tok))
        assert r.status_code == 409 and r.json()["detail"]["code"] == "CHANGED"
        assert _row("2026-10-05").lthr == 170.0


def test_another_tokens_preview_cannot_be_applied(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan()
        a = _writer(e)
        b = make_token(e, [DA.WRITE_SCOPE], days=7, name="other")
        pv = e.c.post(f"{W}/preview", json={"changes": LTHR_166}, headers=bearer(a)).json()
        r = e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(b))
        assert r.status_code == 404 and _row("2026-10-05").lthr == 155.0


def test_a_preview_expires(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan()
        tok = _writer(e)
        pv = e.c.post(f"{W}/preview", json={"changes": LTHR_166}, headers=bearer(tok)).json()
        DW._PREVIEWS[pv["preview_id"]]["exp"] = 0.0
        assert e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(tok)).status_code == 404


# ---------------------------------------------------------------- the whitelist
@pytest.mark.parametrize("change, words", [
    ({"target": "settings", "set": {"sync.primary_source": "garmin"}}, "target"),
    ({"target": "threshold", "date": "2026-10-05", "set": {"wprime": 20000}}, "wprime"),
    ({"target": "threshold", "date": "2026-10-05", "set": {"lthr": 300}}, "lthr"),
    ({"target": "threshold", "date": "2026-10-05", "set": {"lthr": "166"}}, "lthr"),
    ({"target": "threshold", "date": "10/05", "set": {"lthr": 166}}, "date"),
    ({"target": "threshold", "date": "2026-10-05", "set": {"lthr": None, "cp": None}}, "delete"),
    ({"target": "event", "id": "ev1", "delete": True}, "GPX"),
    ({"target": "event", "set": {"name": "x", "date": "2026-12-01", "gpx_pending": True}}, "gpx_pending"),
    ({"target": "event", "id": "nope", "set": {"name": "x"}}, "nope"),
    ({"target": "prefs", "set": {"no_such_pref": 1}}, "no_such_pref"),
])
def test_only_the_whitelist_can_be_changed(monkeypatch, tmp_path, change, words):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan(events=[{"id": "ev1", "name": "北插天山", "date": "2026-11-01"}])
        tok = _writer(e)
        r = e.c.post(f"{W}/preview", json={"changes": [change]}, headers=bearer(tok))
        assert r.status_code == 400 and r.json()["detail"]["code"] == "BAD_CHANGE", r.text
        assert words in r.json()["detail"]["message"]


def test_too_many_changes_or_none_is_refused(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan()
        tok = _writer(e)
        for changes in ([], None, LTHR_166 * (DW.MAX_CHANGES + 1)):
            assert e.c.post(f"{W}/preview", json={"changes": changes}, headers=bearer(tok)).status_code == 400


def test_a_threshold_row_can_be_added_and_deleted(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan()
        tok = _writer(e)
        ch = [{"target": "threshold", "date": "2026-09-30", "set": {"lthr": 166, "note": "SP-719"}},
              {"target": "threshold", "date": "2026-10-05", "delete": True}]
        pv = e.c.post(f"{W}/preview", json={"changes": ch}, headers=bearer(tok)).json()
        added = [x for x in pv["diff"] if x["field"] is None]
        assert {x["key"] for x in added} == {"2026-09-30", "2026-10-05"}
        assert e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(tok)).status_code == 200
        [row] = P.Plan.load().thresholds
        assert row.date == "2026-09-30" and row.lthr == 166.0 and row.note == "SP-719"


def test_a_cp_set_here_counts_as_typed_by_hand(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan([{"date": "2026-10-05", "cp": 204.0, "cp_method": "2pt", "wprime": 13100.0}])
        tok = _writer(e)
        ch = [{"target": "threshold", "date": "2026-10-05", "set": {"cp": 212}}]
        pv = e.c.post(f"{W}/preview", json={"changes": ch}, headers=bearer(tok)).json()
        e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(tok))
        row = _row("2026-10-05")
        assert row.cp == 212.0 and row.cp_manual is True and row.cp_method is None


def test_an_event_is_created_and_changed_through_the_pages_checks(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan(events=[{"id": "ev1", "name": "北插天山", "date": "2026-11-01", "kind": "hike", "priority": "B"}])
        tok = _writer(e)
        ch = [{"target": "event", "id": "ev1", "set": {"priority": "A", "pack_kg": 6}},
              {"target": "event", "set": {"name": "合歡北峰", "date": "2026-12-06", "kind": "hike"}}]
        pv = e.c.post(f"{W}/preview", json={"changes": ch}, headers=bearer(tok)).json()
        assert e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(tok)).status_code == 200
        evs = {x.name: x for x in P.Plan.load().events}
        assert evs["北插天山"].priority == "A" and evs["北插天山"].pack_kg == 6.0 and evs["北插天山"].id == "ev1"
        assert evs["合歡北峰"].date == "2026-12-06"
        # the page's own rule (planning.upsert_event): a pack over the limit is refused
        bad = [{"target": "event", "id": "ev1", "set": {"pack_kg": 999}}]
        r = e.c.post(f"{W}/preview", json={"changes": bad}, headers=bearer(tok))
        assert r.status_code == 400 and "kg" in r.json()["detail"]["message"]


def test_prefs_are_merged_and_checked_like_the_page(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan()
        tok = _writer(e)
        ch = [{"target": "prefs", "set": {"runs": 5}}]
        pv = e.c.post(f"{W}/preview", json={"changes": ch}, headers=bearer(tok)).json()
        assert [(x["key"], x["after"]) for x in pv["diff"]] == [("plan.prefs.runs_per_week", 5)]
        assert e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(tok)).status_code == 200
        assert e.c.get("/api/v1/overview/plan/prefs").json()["prefs"]["runs"] == 5
        # the stored key names the same preference; the page's own check still refuses a bad value
        same = [{"target": "prefs", "set": {"plan.prefs.runs_per_week": 5}}]
        assert e.c.post(f"{W}/preview", json={"changes": same}, headers=bearer(tok)).json()["nothing_to_change"]
        bad = [{"target": "prefs", "set": {"runs": 12}}]
        assert e.c.post(f"{W}/preview", json={"changes": bad}, headers=bearer(tok)).status_code == 400


def test_a_change_to_the_same_value_has_no_preview(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan([{"date": "2026-10-05", "lthr": 166.0, "lthr_method": "manual"}])
        tok = _writer(e)
        pv = e.c.post(f"{W}/preview", json={"changes": LTHR_166}, headers=bearer(tok)).json()
        assert pv["nothing_to_change"] is True and pv["preview_id"] is None and pv["diff"] == []


def test_every_write_call_is_audited(monkeypatch, tmp_path):
    with debug_env(monkeypatch, tmp_path) as e:
        _plan()
        tok = _writer(e)
        pv = e.c.post(f"{W}/preview", json={"changes": LTHR_166}, headers=bearer(tok)).json()
        e.c.post(f"{W}/apply", json={"preview_id": pv["preview_id"]}, headers=bearer(tok))
        e.c.post(f"{W}/preview", json={"changes": [{"target": "nope"}]}, headers=bearer(tok))
        rows = e.c.get(ADMIN).json()["audit"]
        got = [(a["method"], a["path"], a["status"]) for a in rows]
        assert ("POST", f"{W}/preview", 200) in got and ("POST", f"{W}/apply", 200) in got
        assert ("POST", f"{W}/preview", 400) in got


# ---------------------------------------------------------------- sign-in mode (SP-406's gate)
from backend.tests.test_login_sp58 import (OWNER_EMAIL, app, browser, c, csrf, env, sign_in,  # noqa: E402,F401
                                           sign_up, started)


def test_signin_mode_the_owners_write_token_opens_only_the_write_posts(app, c):
    DA.reset_limits()
    o = browser(app)
    sign_in(o, "g-owner", OWNER_EMAIL)
    h = {"X-TRC-CSRF": csrf(o)}
    assert o.put(ADMIN, json={"enabled": True}, headers=h).status_code == 200
    r = o.post(f"{ADMIN}/tokens", json={"name": "claude", "scopes": [DA.WRITE_SCOPE], "days": 7}, headers=h)
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    agent = browser(app)                                            # no cookie, no CSRF header
    r = agent.post(f"{W}/preview", json={"changes": [{"target": "prefs", "set": {"runs": 5}}]},
                   headers=bearer(tok))
    assert r.status_code == 200, r.text
    assert r.json()["preview_id"]
    # other writes stay shut: the app's own write endpoints, a POST elsewhere under /api/v1/debug/
    assert agent.put("/api/v1/overview/plan/prefs", json={"runs": 5}, headers=bearer(tok)).status_code == 401
    assert agent.post(f"{DBG}/sync", headers=bearer(tok)).status_code in (401, 403)
    assert agent.post(f"{ADMIN}/tokens", json={}, headers=bearer(tok)).status_code == 401
    DA.reset_limits()
