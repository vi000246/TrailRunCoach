"""建議框「稍後提醒」 (SP-507): every row (but injury_done, whose 「還沒」 already is one) gets 稍後提醒 — the row leaves
the box for 7 days and comes back if it is still computed, unless a test it suggests was done meanwhile; 「不要」 stays
as it was. Stored in `plan.suggestions.dismissed` as {action: snoozed, at, until, key, tests}; the key holds a weekly
id's later weeks and the 基線測試 of the same kind. While snoozed a test is still the box's: the overview's testOut
(SP-503) stays false. Synthetic data only; the node parts are skipped without node. The page is the browser test
(e2e/test_suggestions_snooze_sp507_e2e.py)."""
import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import suggestions as SG
from backend.tests.test_overview_hints_sp483 import _run, needs_node

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")

W1, W2 = "2026-10-12", "2026-10-19"
NOW = dt.datetime(2026, 10, 14, 9)                       # a Wednesday
TEST = {"id": f"test:cp:{W1}", "type": "test", "kind": "cp"}
ZONE = {"id": "zone:thr_check:hrmax", "type": "zone_test", "tests": [],
        "links": [{"test": "hrmax", "href": "/x", "label": "安排課表：最大心率測試"}]}
ALT = {"id": "altitude:e1:2026-10-30:3200:", "type": "altitude"}


def at(days: float) -> dt.datetime:
    return NOW + dt.timedelta(days=days)


def snooze(rows, sid, now=NOW, dis=None):
    row = next(r for r in rows if r["id"] == sid)
    return SG.record(dis or {}, sid, SG.SNOOZED, now, tests=SG.test_kinds(row))


def ids(rows):
    return [r["id"] for r in rows]


# ---- engine/suggestions.py -------------------------------------------------------------------------------

def test_snooze_is_stored_with_its_day_key_and_tests():
    dis = snooze([TEST, ZONE], TEST["id"])
    assert dis == {TEST["id"]: {"action": "snoozed", "at": "2026-10-14T09:00:00", "until": "2026-10-21",
                                "key": "test:cp", "tests": ["cp"]}}
    assert SG.record({}, ZONE["id"], "snoozed", NOW, tests=SG.test_kinds(ZONE))[ZONE["id"]]["tests"] == ["hrmax"]
    assert [SG.snooze_key(x) for x in ("test:aet:2026-10-12", "baseline:cp:2026-10-19", "b2b:2026-10-12",
                                       "zone:thr_check:hrmax")] == ["test:aet", "test:cp", "b2b", "zone:thr_check:hrmax"]
    assert SG.test_kinds({"type": "baseline", "kind": "aet"}) == ["aet"]
    assert SG.test_kinds({"type": "zone_test", "tests": [{"key": "cp"}, {"key": "aet"}],
                          "links": [{"test": "tt30"}]}) == ["aet", "cp", "tt30"]
    assert SG.test_kinds(ALT) == [] and SG.test_kinds({"type": "b2b"}) == []


def test_snoozed_row_leaves_the_box_and_comes_back_after_7_days():
    rows = [TEST, ZONE, ALT]
    for sid in ids(rows):
        dis = snooze(rows, sid)
        others = [x for x in ids(rows) if x != sid]
        assert ids(SG.visible(rows, dis, at(0))) == others, sid                    # gone at once
        assert ids(SG.visible(rows, dis, at(6.5))) == others, sid                  # 10/20 21:00: still away
        assert ids(SG.visible(rows, dis, at(7) - dt.timedelta(hours=9))) == ids(rows), sid   # 10/21 00:00: back


def test_a_snooze_holds_the_next_weeks_row_and_the_baseline_of_the_same_test():
    dis = snooze([TEST], TEST["id"])
    nxt = [{"id": f"test:cp:{W2}", "type": "test", "kind": "cp"}, {"id": f"baseline:cp:{W2}", "type": "baseline", "kind": "cp"},
           {"id": f"test:aet:{W2}", "type": "test", "kind": "aet"}]
    assert ids(SG.visible(nxt, dis, at(5))) == [f"test:aet:{W2}"]                  # Monday 10/19: still snoozed
    assert ids(SG.visible(nxt, dis, at(7))) == ids(nxt)
    # prune keeps it until its day even when this week's id is no longer computed, then while the test is computed
    assert SG.prune(dis, nxt, "2026-10-19") == dis
    assert SG.prune(dis, [], "2026-10-20") == dis
    assert SG.prune(dis, [], "2026-10-21") == {}
    assert SG.prune(dis, nxt, "2026-11-30") == dis


def test_a_test_done_meanwhile_keeps_it_away():
    dis = snooze([TEST, ZONE], TEST["id"])
    dis = snooze([TEST, ZONE], ZONE["id"], dis=dis)
    done = lambda kinds, since: bool({"cp", "hrmax"} & set(kinds)) and since == "2026-10-14"
    assert ids(SG.visible([TEST, ZONE], dis, at(8), done)) == []
    assert ids(SG.visible([TEST, ZONE], dis, at(8), lambda k, s: False)) == ids([TEST, ZONE])
    # another test done (AeT) does not count for the CP snooze
    assert ids(SG.visible([TEST], dis, at(8), lambda k, s: "aet" in k)) == [TEST["id"]]


def test_tested_reads_sessions_the_review_and_measured_thresholds():
    since = "2026-10-14"
    cp_done = {"kind": "test", "state": "done", "day": "2026-10-17", "title": "CP 測試 3 分＋12 分", "protocol": "standard"}
    aet_done = {"kind": "test", "state": "done", "day": "2026-10-17", "title": "AeT 測試", "protocol": "aet"}
    assert SG.tested(["cp"], since, [cp_done], None, set())
    assert not SG.tested(["aet"], since, [cp_done], None, set())
    assert SG.tested(["aet"], since, [aet_done], None, set())
    assert not SG.tested(["cp"], since, [{**cp_done, "day": "2026-10-13"}], None, set())       # before the snooze
    assert not SG.tested(["cp"], since, [{**cp_done, "state": "active"}], None, set())         # only planned
    assert SG.tested(["cp"], since, [], {"cp": {"date": "2026-10-16", "ok": True}, "aet": None}, set())
    assert not SG.tested(["cp"], since, [], {"cp": {"date": "2026-10-16", "ok": False}}, set())  # did not count
    assert SG.tested(["hrmax"], since, [], None, {"hrmax"})
    assert not SG.tested([], since, [cp_done], None, {"cp"})                                     # not a test row


def test_no_stays_for_good_and_snoozed_is_not_declined():
    rows = [TEST, ZONE, ALT]
    dis = SG.record(snooze(rows, TEST["id"]), ZONE["id"], "declined", NOW)
    vis = SG.visible(rows, dis, at(30))
    assert ids(vis) == [TEST["id"], ALT["id"]]                                    # 不要: still away a month later
    vis = SG.visible(rows, dis, at(1))
    assert SG.declined(rows, vis, dis) == [{"id": ZONE["id"], "type": "zone_test", "action": "declined"}]
    assert SG.snoozed(rows, vis, dis) == [{"id": TEST["id"], "type": "test", "until": "2026-10-21"}]
    # 不要 after the snooze is over replaces it
    dis = SG.record(dis, TEST["id"], "declined", at(8))
    assert SG.snoozed(rows, SG.visible(rows, dis, at(9)), dis) == [] and TEST["id"] not in ids(SG.visible(rows, dis, at(9)))


# ---- the API (api/plan_sessions.py) ----------------------------------------------------------------------

CP_SUG = {"kind": "cp", "protocol": "quick", "title": "CP 測試", "minutes": 40, "reason": "門檻過期", "replaces_long": False,
          "session": {"kind": "test", "title": "CP 測試 3 分＋12 分", "minutes": 40, "protocol": "quick"}}


def _age(e, sid, days):
    """Move the stored snooze `days` back in time (as if it was pressed that long ago)."""
    from backend.settings.repository import SettingsRepository
    from backend.tests.test_plan_store import run
    dis = run(SettingsRepository(e.db).get(SG.KEY))
    v = dis[sid]
    back = lambda s: (dt.datetime.fromisoformat(s) - dt.timedelta(days=days)).isoformat(timespec="seconds")
    dis[sid] = {**v, "at": back(v["at"]), "until": back(v["until"] + "T00:00:00")[:10]}
    run(SettingsRepository(e.db).set(SG.KEY, dis))
    run(e.db.commit())
    return dis[sid]


def test_api_snooze_then_back_then_tested(monkeypatch):
    from backend.engine import plan_prefs as PP
    from backend.tests.test_plan_store import API, Env
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs())       # never the real settings
    with Env(monkeypatch) as e:
        e.inp["cur"]["test_suggestions"] = [CP_SUG]
        e.c.get(f"{API}/sessions")
        sid = e.c.get(f"{API}/suggestions").json()["suggestions"][0]["id"]
        assert sid.startswith("test:cp:")
        r = e.c.post(f"{API}/suggestions/dismiss", json={"id": sid, "action": "snoozed"})
        assert r.status_code == 200
        until = (dt.date.today() + dt.timedelta(days=7)).isoformat()
        assert r.json() == {"id": sid, "action": "snoozed", "today": r.json()["today"], "until": until}
        got = e.c.get(f"{API}/suggestions").json()
        assert got["suggestions"] == [] and got["declined"] == []
        assert got["snoozed"] == [{"id": sid, "type": "test", "until": until}]
        # R1: the 課表's 排入測試 ▸ still offers it (排入 itself: test_suggestions_snooze_r1_sp507.py)
        assert [r["id"] for r in got["snoozed_tests"]] == [sid]
        day = got["snoozed_tests"][0]["options"][0]["day"]
        assert [(o["id"], o["ok"]) for o in e.c.get(f"{API}/test-options?day={day}").json()["options"]] == [(sid, True)]
        # 7 days later: back in the box
        _age(e, sid, 7)
        got = e.c.get(f"{API}/suggestions").json()
        assert [s["id"] for s in got["suggestions"]] == [sid] and got["snoozed"] == []
        # snoozed again, then the CP test is done meanwhile (the review found it): 7 days later it stays away
        e.c.post(f"{API}/suggestions/dismiss", json={"id": sid, "action": "snoozed"})
        e.inp["tests"] = {"cp": {"date": dt.date.today().isoformat(), "method": "2pt", "protocol": "standard", "ok": True},
                          "aet": None}
        _age(e, sid, 8)
        e.inp["tests"]["cp"]["date"] = (dt.date.today() - dt.timedelta(days=3)).isoformat()   # after the snooze
        got = e.c.get(f"{API}/suggestions").json()
        assert sid not in [s["id"] for s in got["suggestions"]]
        assert got["snoozed"] == [{"id": sid, "type": "test", "until": (dt.date.today() - dt.timedelta(days=1)).isoformat()}]
        # a suggestion that is not in the box cannot be snoozed
        assert e.c.post(f"{API}/suggestions/dismiss", json={"id": "zone:nope", "action": "snoozed"}).status_code == 400
        assert e.c.post(f"{API}/suggestions/dismiss", json={"id": sid, "action": "later"}).status_code == 400


# ---- the box (static/suggestions.js) and the overview (OVH.testOut) ------------------------------------

JS = r"""
const vm = require("vm"), fs = require("fs");
const events = [], posts = [], listeners = {};
const el = () => new Proxy({}, { get: (t, k) => (k in t ? t[k] : k === "then" ? undefined : () => el()),
                                 set: (t, k, v) => { t[k] = v; return true; } });
let html = "";
const box = { classList: { toggle() {} }, setAttribute() {}, querySelector: () => null,
  addEventListener: (t, f) => { listeners[t] = f; } };
Object.defineProperty(box, "innerHTML", { get: () => html, set: (v) => { html = v; } });
const w = { dispatchEvent: (e) => events.push({ type: e.type, detail: e.detail }), addEventListener() {},
  matchMedia: () => ({ matches: false }) };
const answer = { suggestions: JSON.parse(process.argv[2]), declined: [], snoozed: [{ id: "zone:y", type: "zone_test", until: "2026-10-20" }] };
const ctx = { window: w, CustomEvent: function (t, o) { this.type = t; this.detail = (o || {}).detail; },
  setTimeout() {}, localStorage: { getItem: () => "0", setItem() {} }, matchMedia: w.matchMedia,
  document: { body: null, head: el(), createElement: (t) => (t === "aside" ? box : el()), addEventListener() {} },
  fetch: (u, o) => { if (o && o.method === "POST") posts.push(JSON.parse(o.body));
    return Promise.resolve({ ok: true, json: () => Promise.resolve(o && o.method === "POST" ? { until: "2026-10-21" } : answer) }); } };
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
(async () => {
  await w.AppSuggestions.refresh(false);
  await new Promise((r) => setImmediate(r));
  const rendered = html;
  const act = { outerHTML: "" };
  const item = { dataset: { id: "test:cp:2026-10-12" }, querySelectorAll: () => [act], querySelector: (s) => (s === ".sg-act" ? act : null),
    insertAdjacentHTML() {} };
  const btn = { dataset: { act: "snooze" }, closest: (s) => (s === ".sg-item" ? item : null) };
  await listeners.click({ target: { closest: () => btn } });
  console.log(JSON.stringify({ rendered, posts, after: act.outerHTML, events: events.filter((e) => e.type !== "suggestions:ready") }));
})();
"""

ROWS = [{"id": "test:cp:2026-10-12", "type": "test", "title": "CP", "pick": "day", "options": [{"day": "2026-10-16"}]},
        {"id": "zone_update:cp:2026-10-01", "type": "zone_update", "title": "區間已更新", "pick": None},
        {"id": "zone:thr_check:hrmax", "type": "zone_test", "title": "HR", "pick": None, "tests": [],
         "links": [{"href": "/x", "label": "安排課表：最大心率測試", "test": "hrmax"}]},
        {"id": "injury_done:7", "type": "injury_done", "title": "好了？", "pick": "confirm", "decline_label": "還沒"}]


def _box():
    r = subprocess.run([NODE, "-e", JS, str(STATIC / "suggestions.js"), json.dumps(ROWS)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _items(html: str) -> dict:
    parts = html.split('<div class="sg-item" data-id="')[1:]
    return {p.split('"', 1)[0]: p for p in parts}


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_every_row_but_injury_done_has_remind_me_later():
    out = _box()
    items = _items(out["rendered"])
    assert set(items) == {r["id"] for r in ROWS}
    for sid, h in items.items():
        assert (h.count('data-act="snooze"') == 1) == (sid != "injury_done:7"), sid
    assert 'data-act="decline"' in items["test:cp:2026-10-12"]                     # 不要 is still there
    assert "snooze_tip_test" in items["test:cp:2026-10-12"] and "snooze_tip_test" in items["zone:thr_check:hrmax"]
    assert '"snooze_tip"' in items["zone_update:cp:2026-10-01"]                     # (keys: no catalog under node)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_snooze_posts_snoozed_and_tells_the_page():
    out = _box()
    assert out["posts"] == [{"id": "test:cp:2026-10-12", "action": "snoozed"}]
    loaded, snoozed = out["events"]
    assert loaded["detail"]["snoozed"] == [{"id": "zone:y", "type": "zone_test", "until": "2026-10-20"}]
    assert snoozed == {"type": "suggestions:snoozed", "detail": {"id": "test:cp:2026-10-12", "type": "test", "until": "2026-10-21"}}
    assert "sg-ok" in out["after"] and "snoozed" in out["after"]                    # the 「好，10/21 再提醒你」 line


@needs_node
def test_a_snoozed_test_is_still_the_boxs():
    T, B = {"id": "test:cp:2026-10-12", "type": "test"}, {"id": "baseline:aet:2026-10-12", "type": "baseline"}
    cases = {"snoozed, other declined": {"rows": [], "declined": [T], "snoozed": [B]},
             "snoozed only": {"rows": [], "declined": [], "snoozed": [T]},
             "declined (SP-503)": {"rows": [], "declined": [T], "snoozed": []},
             "no snoozed key (old)": {"rows": [], "declined": [T]}}
    got = _run("Object.fromEntries(Object.entries(DATA.c).map(([k, v]) => [k, OVH.testOut(v)]))", c=cases)
    assert got == {"snoozed, other declined": False, "snoozed only": False, "declined (SP-503)": True,
                   "no snoozed key (old)": True}


def test_the_overview_follows_a_snooze():
    html = (STATIC / "overview.html").read_text("utf-8")
    assert 'addEventListener("suggestions:snoozed"' in html
    assert "snoozed: (e.detail || {}).snoozed || []" in html


def test_strings_in_both_languages():
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
        for k in ("sugg.snooze", "sugg.snooze_tip", "sugg.snooze_tip_test", "sugg.snoozed"):
            assert cat.get(k), (loc, k)
        assert "{day}" in cat["sugg.snoozed"]
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "common.json").read_text("utf-8"))
    assert zh["sugg.snooze"] == "稍後提醒" and zh["sugg.decline"] == "不要"
