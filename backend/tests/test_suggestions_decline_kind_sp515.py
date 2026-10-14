"""SP-515: 「不要」 on a test row of the suggestion box (`test:<kind>:<week>`, `baseline:<kind>:<week>`) holds that test
in every later week (by its kind, decline_key = snooze_key: a 基線測試 and the weekly test of the same kind are one
test); prune keeps it. The settings page's 「不再建議的測試」 lists them (GET …/suggestions/declined-tests) and 恢復
undoes one (POST …/suggestions/restore). ✕ and 「不要」 on a B2B still hold their week only. SP-503 stays: a test held
from an earlier week is in GET /suggestions' `declined`, so the overview's testOut gives the 待辦 test row back.
Synthetic data only; the node parts are skipped without node. The pages are the browser test
(e2e/test_suggestions_decline_kind_sp515_e2e.py)."""
import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import suggestions as SG
from backend.tests.test_overview_hints_sp483 import _run, needs_node
from backend.tests.test_overview_test_row_sp503 import ACTIONS
from backend.tests.test_suggestions_snooze_sp507 import CP_SUG

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
W1, W2, W3 = "2026-10-12", "2026-10-19", "2026-10-26"
NOW = dt.datetime(2026, 10, 14, 9)


def row(sid, type_, **kw):
    return {"id": sid, "type": type_, **kw}


def ids(rows):
    return [r["id"] for r in rows]


def week2():
    return [row(f"test:cp:{W2}", "test", kind="cp"), row(f"baseline:cp:{W2}", "baseline", kind="cp"),
            row(f"test:aet:{W2}", "test", kind="aet"), row(f"b2b:{W2}", "b2b"), row("zone:thr_check:hrmax", "zone_test")]


# ---- engine/suggestions.py -------------------------------------------------------------------------------

def test_no_on_a_test_holds_that_test_in_later_weeks():
    dis = SG.record({}, f"test:cp:{W1}", "declined", NOW, W1)
    later = NOW + dt.timedelta(days=60)
    assert ids(SG.visible(week2(), dis, later)) == [f"test:aet:{W2}", f"b2b:{W2}", "zone:thr_check:hrmax"]
    # 「不要」 on the 基線測試 holds the weekly test of that kind as well
    dis = SG.record({}, f"baseline:aet:{W1}", "declined", NOW, W1)
    assert f"test:aet:{W2}" not in ids(SG.visible(week2(), dis, later))
    assert [SG.decline_key(x) for x in (f"test:cp:{W1}", f"baseline:aet:{W1}", f"b2b:{W1}", "zone:x")] == \
        ["test:cp", "test:aet", None, None]


def test_close_and_no_on_a_b2b_still_hold_their_week_only():
    dis = SG.record({}, f"test:cp:{W1}", "dismissed", NOW, W1)                 # ✕
    dis = SG.record(dis, f"b2b:{W1}", "declined", NOW, W1)
    dis = SG.record(dis, f"test:aet:{W1}", "accepted", NOW, W1)                # 排入
    assert ids(SG.visible(week2(), dis, NOW)) == ids(week2())
    assert SG.prune(dis, week2(), W2) == {}                                    # last week's: gone as before


def test_prune_keeps_a_no_on_a_test_until_restored():
    dis = SG.record({}, f"test:cp:{W1}", "declined", NOW, W1)
    assert SG.prune(dis, [], "2027-03-01") == dis
    assert SG.restore(dis, "test:cp") == {}
    assert SG.restore(dis, "test:aet") == dis


def test_declined_lists_a_later_weeks_row_of_a_test_said_no_to():
    """SP-503: the overview takes the test back (待辦 test row) from `declined` — so a later week's row counts."""
    dis = SG.record({}, f"test:cp:{W1}", "declined", NOW, W1)
    rows = week2()
    vis = SG.visible(rows, dis, NOW)
    assert SG.declined(rows, vis, dis) == [{"id": f"test:cp:{W2}", "type": "test", "action": "declined"},
                                           {"id": f"baseline:cp:{W2}", "type": "baseline", "action": "declined"}]


def test_declined_tests_one_per_test_with_the_latest_day():
    dis = SG.record({}, f"test:cp:{W1}", "declined", NOW, W1)
    dis = SG.record(dis, f"baseline:cp:{W2}", "declined", NOW + dt.timedelta(days=7), W2)
    dis = SG.record(dis, f"test:aet:{W1}", "dismissed", NOW, W1)               # ✕: not listed
    dis = SG.record(dis, f"b2b:{W1}", "declined", NOW, W1)                     # not a test
    assert SG.declined_tests(dis) == [{"key": "test:cp", "kind": "cp", "at": "2026-10-21"}]
    assert SG.restore(dis, "test:cp") == {k: v for k, v in dis.items() if "cp" not in k}


# ---- the API (api/plan_sessions.py) ----------------------------------------------------------------------

def test_api_no_holds_next_week_lists_it_and_restore_brings_it_back(monkeypatch):
    from backend.engine import plan_prefs as PP
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_plan_store import API, Env, run
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs())       # never the real settings
    with Env(monkeypatch) as e:
        e.inp["cur"]["test_suggestions"] = [CP_SUG]
        e.c.get(f"{API}/sessions")
        sid = e.c.get(f"{API}/suggestions").json()["suggestions"][0]["id"]
        assert sid.startswith("test:cp:")
        mon = dt.date.fromisoformat(sid.rsplit(":", 1)[1])
        prev, nxt = (mon - dt.timedelta(days=7)).isoformat(), (mon + dt.timedelta(days=7)).isoformat()
        assert e.c.get(f"{API}/suggestions/declined-tests").json() == {"tests": []}
        # 「不要」 pressed a week earlier (last week's id), stored as before SP-515 ({action, at, week})
        run(SettingsRepository(e.db).set(SG.KEY, {f"test:cp:{prev}": {"action": "declined", "at": f"{prev}T08:00:00",
                                                                       "week": prev}}))
        run(e.db.commit())
        got = e.c.get(f"{API}/suggestions").json()
        assert got["suggestions"] == []                                                  # this week's: held too
        assert got["declined"] == [{"id": sid, "type": "test", "action": "declined"}]    # SP-503: the overview's testOut
        assert e.c.get(f"{API}/test-suggestions").json()["suggestions"] == []
        assert e.c.get(f"{API}/suggestions").json()["declined"] == got["declined"]       # prune kept it
        assert e.c.get(f"{API}/suggestions/declined-tests").json() == {"tests": [{"key": "test:cp", "kind": "cp",
                                                                                 "at": prev}]}
        # 恢復
        assert e.c.post(f"{API}/suggestions/restore", json={"key": "test:aet"}).status_code == 400
        r = e.c.post(f"{API}/suggestions/restore", json={"key": "test:cp"})
        assert r.status_code == 200 and r.json() == {"key": "test:cp", "tests": []}
        got = e.c.get(f"{API}/suggestions").json()
        assert ids(got["suggestions"]) == [sid] and got["declined"] == []
        # 「不要」 from the box now: listed, and held in a later week (the stored id is this week's)
        e.c.post(f"{API}/suggestions/dismiss", json={"id": sid, "action": "declined"})
        assert [t["key"] for t in e.c.get(f"{API}/suggestions/declined-tests").json()["tests"]] == ["test:cp"]
        dis = run(SettingsRepository(e.db).get(SG.KEY))
        assert SG.visible([row(f"test:cp:{nxt}", "test"), row(f"baseline:cp:{nxt}", "baseline")], dis) == []


# ---- the overview (SP-503 OVH.testOut / todos) and the box (static/suggestions.js) ----------------------

@needs_node
def test_the_overview_gives_the_test_row_back_for_a_later_week():
    sg = {"rows": [], "declined": [{"id": f"test:cp:{W2}", "type": "test", "action": "declined"}], "snoozed": []}
    out, todo = _run("[OVH.testOut(DATA.sg), OVH.todos(DATA.a, 'standard', {testOut: OVH.testOut(DATA.sg)})]", sg=sg, a=ACTIONS)
    assert out is True
    assert "測試：建議：做最大心率測試" in [x["title"] for x in todo["shown"]]


JS = r"""
const vm = require("vm"), fs = require("fs");
let html = "";
const el = () => new Proxy({}, { get: (t, k) => (k in t ? t[k] : k === "then" ? undefined : () => el()),
                                 set: (t, k, v) => { t[k] = v; return true; } });
const box = { classList: { toggle() {} }, setAttribute() {}, querySelector: () => null, addEventListener() {} };
Object.defineProperty(box, "innerHTML", { get: () => html, set: (v) => { html = v; } });
const w = { dispatchEvent() {}, addEventListener() {}, matchMedia: () => ({ matches: false }) };
const ctx = { window: w, CustomEvent: function (t) { this.type = t; }, setTimeout() {}, matchMedia: w.matchMedia,
  localStorage: { getItem: () => "0", setItem() {} },
  document: { body: null, head: el(), createElement: (t) => (t === "aside" ? box : el()), addEventListener() {} },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve({ suggestions: JSON.parse(process.argv[2]) }) }) };
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
(async () => { await w.AppSuggestions.refresh(false); console.log(JSON.stringify(html)); })();
"""

ROWS = [{"id": f"test:cp:{W1}", "type": "test", "title": "CP", "pick": "day", "options": [{"day": "2026-10-16"}]},
        {"id": f"baseline:aet:{W1}", "type": "baseline", "title": "AeT", "pick": "day", "options": []},
        {"id": f"b2b:{W1}", "type": "b2b", "title": "B2B", "pick": "pair", "options": []}]


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_box_says_no_holds_a_test_for_good():
    r = subprocess.run([NODE, "-e", JS, str(STATIC / "suggestions.js"), json.dumps(ROWS)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    items = {p.split('"', 1)[0]: p for p in json.loads(r.stdout).split('<div class="sg-item" data-id="')[1:]}
    tip = 'data-act="decline" title="decline_tip_test"'                  # (keys: no catalog under node)
    assert tip in items[f"test:cp:{W1}"] and tip in items[f"baseline:aet:{W1}"]
    assert 'data-act="decline">' in items[f"b2b:{W1}"]                    # a B2B's 不要: its week, no tip


def test_settings_lists_them_with_restore():
    html = (STATIC / "settings.html").read_text("utf-8")
    hint = html[html.index('<div id="hint-level">'):html.index('<section id="mode">')]
    assert '<div id="sugg-declined" hidden>' in hint and 'id="sugg-declined-list"' in hint
    js = html[html.index("// ---- 不再建議的測試 (SP-515"):]
    assert "`${SUGG_API}/declined-tests`" in js and "`${SUGG_API}/restore`" in js and "loadDeclined();" in js
    assert 'data-restore="${esc(x.key)}"' in js


def test_strings_in_both_languages():
    keys = ("title", "intro", "kind_cp", "kind_aet", "since", "restore", "restored")
    for loc in ("zh-TW", "en"):
        st = json.loads((STATIC / "i18n" / loc / "settings.json").read_text("utf-8"))
        assert all(st.get(f"sugg_declined.{k}") for k in keys), loc
        assert "{day}" in st["sugg_declined.since"] and "{test}" in st["sugg_declined.restored"]
        cm = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
        assert cm.get("sugg.decline_tip_test"), loc
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "settings.json").read_text("utf-8"))
    assert zh["sugg_declined.title"] == "不再建議的測試" and zh["sugg_declined.restore"] == "恢復"
