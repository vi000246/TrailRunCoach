"""總覽待辦的「測試」列 (SP-503): SP-483 (R15) keeps the overview's 測試 待辦 row out and points the 測試 card to the
suggestion box while the box has the test; once every test row of the box was 「不要」 / ✕ the row comes back and
the card keeps its own verdict and links. GET /suggestions says which rows 「不要」 / ✕ hid (`declined`);
suggestions.js passes them on ("suggestions:loaded" {rows, declined}, "suggestions:dismissed" {id, type, action}).
Synthetic data only; the node parts are skipped without node. The page itself is the browser test
(e2e/test_overview_test_row_sp503_e2e.py)."""
import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import suggestions as SG
from backend.tests.test_overview_hints_sp483 import ACTIONS, _run, needs_node

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")

TEST = {"id": "test:cp:2026-10-12", "type": "test"}
BASE = {"id": "baseline:aet:2026-10-12", "type": "baseline"}
ZONE = {"id": "zone:thr_check:hrmax", "type": "zone_test"}
B2B = {"id": "b2b:2026-10-12", "type": "b2b"}


# ---- the backend: which rows 「不要」 / ✕ hid -----------------------------------------------------------

def test_declined_lists_the_rows_hidden_by_no_or_close():
    rows = [TEST, BASE, ZONE, B2B, {"id": "injury_done:7", "type": "injury_done"}]
    now = dt.datetime(2026, 10, 14, 9)
    dis = {TEST["id"]: {"action": "declined", "at": "2026-10-14T08:00:00", "week": "2026-10-12"},
           ZONE["id"]: {"action": "dismissed", "at": "2026-10-14T08:00:00"},
           B2B["id"]: {"action": "accepted", "at": "2026-10-14T08:00:00", "week": "2026-10-12"},   # 排入: not 不要
           "injury_done:7": {"action": "declined", "at": "2026-09-01T08:00:00"},                    # snooze over: shown
           "test:aet:2026-10-12": {"action": "declined", "at": "2026-10-14T08:00:00"}}             # not computed now
    vis = SG.visible(rows, dis, now)
    # SP-515: 「不要」 on the AeT test holds the AeT test of any week, the 基線測試 too
    assert [r["id"] for r in vis] == ["injury_done:7"]
    assert SG.declined(rows, vis, dis) == [{"id": TEST["id"], "type": "test", "action": "declined"},
                                           {"id": BASE["id"], "type": "baseline", "action": "declined"},
                                           {"id": ZONE["id"], "type": "zone_test", "action": "dismissed"}]
    assert SG.declined(rows, rows, {}) == []


def test_api_suggestions_say_what_no_hid(monkeypatch):
    from backend.engine import plan_prefs as PP
    from backend.tests.test_plan_store import API, Env
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs())       # never the real settings
    with Env(monkeypatch) as e:
        e.inp["cur"]["test_suggestions"] = [{"kind": "cp", "protocol": "quick", "title": "CP 測試", "minutes": 40,
                                             "reason": "門檻過期", "replaces_long": False,
                                             "session": {"kind": "test", "title": "CP 測試 3 分＋12 分",
                                                         "minutes": 40, "protocol": "quick"}}]
        e.c.get(f"{API}/sessions")
        r = e.c.get(f"{API}/suggestions").json()
        sid = r["suggestions"][0]["id"]
        assert [s["id"] for s in r["suggestions"]] == [sid] and sid.startswith("test:cp:") and r["declined"] == []
        e.c.post(f"{API}/suggestions/dismiss", json={"id": sid, "action": "declined"})
        r = e.c.get(f"{API}/suggestions").json()
        assert r["suggestions"] == [] and r["count"] == 0
        assert r["declined"] == [{"id": sid, "type": "test", "action": "declined"}]


# ---- overview.html's SP-483 block: OVH.testOut and the 待辦 ------------------------------------------

@needs_node
def test_test_out_only_when_every_test_row_of_the_box_was_declined():
    cases = {
        "not loaded": None,
        "box has the test": {"rows": [TEST], "declined": []},
        "one declined, another still there": {"rows": [ZONE], "declined": [TEST]},
        "no test ever": {"rows": [B2B], "declined": [B2B]},
        "declined": {"rows": [B2B], "declined": [TEST]},
        "closed with ✕": {"rows": [], "declined": [{**BASE, "action": "dismissed"}]},
    }
    got = _run("Object.fromEntries(Object.entries(DATA.c).map(([k, v]) => [k, OVH.testOut(v)]))", c=cases)
    assert got == {"not loaded": False, "box has the test": False, "one declined, another still there": False,
                   "no test ever": False, "declined": True, "closed with ✕": True}


@needs_node
def test_the_test_row_is_back_in_every_level_once_declined():
    titles = lambda xs: [x["title"] for x in xs]
    for lv in ("full", "standard", "lean"):
        kept, back = _run("[OVH.todos(DATA.a, DATA.lv, {testOut: false}), OVH.todos(DATA.a, DATA.lv, {testOut: true})]",
                          a=ACTIONS, lv=lv)
        assert not any(t.startswith("測試") for t in titles(kept["shown"] + kept["detail"])), lv      # SP-483 as is
        assert "測試：建議：做最大心率測試" in titles(back["shown"]), lv                                  # SP-503
        # everything else stays where SP-483 put it
        assert [t for t in titles(back["shown"]) if not t.startswith("測試")] == titles(kept["shown"]), lv
        assert back["detail"] == kept["detail"], lv


# ---- suggestions.js passes 「不要」 on ------------------------------------------------------------------

JS = r"""
const vm = require("vm"), fs = require("fs");
const events = [], listeners = {};
const el = () => new Proxy({}, { get: (t, k) => (k in t ? t[k] : k === "then" ? undefined : () => el()),
                                 set: (t, k, v) => { t[k] = v; return true; } });
// the box's element: its click handler is what we call
const box = { classList: { toggle() {} }, setAttribute() {}, querySelector: () => null,
  addEventListener: (t, f) => { listeners[t] = f; } };
const w = { dispatchEvent: (e) => events.push({ type: e.type, detail: e.detail }), addEventListener() {},
  matchMedia: () => ({ matches: false }) };
const answer = { suggestions: [{ id: "test:cp:2026-10-12", type: "test", title: "CP", pick: "day", options: [] }],
                 declined: [{ id: "zone:x", type: "zone_test", action: "dismissed" }] };
const ctx = { window: w, CustomEvent: function (t, o) { this.type = t; this.detail = (o || {}).detail; },
  setTimeout() {}, localStorage: { getItem: () => "0", setItem() {} }, matchMedia: w.matchMedia,
  document: { body: null, head: el(), createElement: (t) => (t === "aside" ? box : el()), addEventListener() {} },
  fetch: (u, o) => Promise.resolve({ ok: true, json: () => Promise.resolve(o && o.method === "POST" ? {} : answer) }) };
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
(async () => {
  await w.AppSuggestions.refresh(false);
  await new Promise((r) => setImmediate(r));
  const item = { dataset: { id: "test:cp:2026-10-12" }, querySelectorAll: () => [], querySelector: () => null };
  const btn = { dataset: { act: "decline" }, closest: (s) => (s === ".sg-item" ? item : null) };
  await listeners.click({ target: { closest: () => btn } });
  console.log(JSON.stringify(events.filter((e) => e.type !== "suggestions:ready")));
})();
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_box_says_what_was_declined():
    r = subprocess.run([NODE, "-e", JS, str(STATIC / "suggestions.js")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    loaded, dismissed = json.loads(r.stdout)
    assert loaded["type"] == "suggestions:loaded"
    assert [x["id"] for x in loaded["detail"]["rows"]] == ["test:cp:2026-10-12"]
    assert loaded["detail"]["declined"] == [{"id": "zone:x", "type": "zone_test", "action": "dismissed"}]
    assert dismissed == {"type": "suggestions:dismissed",
                         "detail": {"id": "test:cp:2026-10-12", "action": "declined", "type": "test"}}


def test_the_overview_listens():
    """renderTodo / renderInd read OVH.testOut(SUGG); SUGG follows the box's two events."""
    html = (STATIC / "overview.html").read_text("utf-8")
    assert 'addEventListener("suggestions:loaded"' in html and 'addEventListener("suggestions:dismissed"' in html
    todo = html[html.index("function renderTodo()"):html.index("function renderInd()")]
    ind = html[html.index("function renderInd()"):html.index("// 熱適應 card")]
    assert "testOut: OVH.testOut(SUGG)" in todo and "testOut: OVH.testOut(SUGG)" in ind
    assert '(i.level === "watch" || i.level === "bad") && !ctx.testOut' in ind
