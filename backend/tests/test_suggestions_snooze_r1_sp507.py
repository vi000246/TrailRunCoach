"""SP-507 R1: 稍後提醒 hides a test from the suggestion box only — the 課表's 排入測試 ▸ still lists it and 排入 works
(the owner: 「一樣要能排入測試」). 「不要」 still takes it out of 排入測試 ▸ too. GET /suggestions returns the held
tests whole (`snoozed_tests`) for the 課表's context menu (schedule.html sugCount, through suggestions.js
AppSuggestions.load(fresh, true)). Synthetic data only; the node part is skipped without node. The page is the
browser test (e2e/test_suggestions_snooze_r1_sp507_e2e.py)."""
import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import suggestions as SG
from backend.tests.test_suggestions_snooze_sp507 import CP_SUG, TEST, ZONE

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
NOW = dt.datetime(2026, 10, 14, 9)


def test_visible_without_snoozes_keeps_a_held_row_but_not_a_declined_one():
    dis = SG.record({}, TEST["id"], SG.SNOOZED, NOW, tests=["cp"])
    dis = SG.record(dis, ZONE["id"], "declined", NOW)
    assert SG.visible([TEST, ZONE], dis, NOW) == []
    assert SG.visible([TEST, ZONE], dis, NOW, snoozes=False) == [TEST]


def _env(monkeypatch):
    from backend.engine import plan_prefs as PP
    from backend.tests.test_plan_store import Env
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs())       # never the real settings
    return Env(monkeypatch)


def test_api_a_held_test_is_offered_and_scheduled_from_the_schedule(monkeypatch):
    from backend.tests.test_plan_store import API
    with _env(monkeypatch) as e:
        e.inp["cur"]["test_suggestions"] = [CP_SUG]
        e.c.get(f"{API}/sessions")
        first = e.c.get(f"{API}/suggestions").json()
        sid = first["suggestions"][0]["id"]
        assert first["snoozed_tests"] == []
        assert e.c.post(f"{API}/suggestions/dismiss", json={"id": sid, "action": "snoozed"}).status_code == 200
        got = e.c.get(f"{API}/suggestions").json()
        assert got["suggestions"] == []                                           # the box: gone for 7 days
        held = got["snoozed_tests"]
        assert [r["id"] for r in held] == [sid] and held[0]["type"] == "test" and held[0]["options"]
        day = held[0]["options"][0]["day"]
        opts = e.c.get(f"{API}/test-options?day={day}").json()["options"]
        assert [(o["id"], o["kind"], o["ok"]) for o in opts] == [(sid, "cp", True)] and opts[0]["templates"]
        # 排入 from 排入測試 ▸
        r = e.c.post(f"{API}/suggestions/accept", json={"id": sid, "day": day, "template": opts[0]["templates"][0]["key"]})
        assert r.status_code == 200, r.text
        s = r.json()["sessions"][0]
        assert s["kind"] == "test" and s["day"] == day
        after = e.c.get(f"{API}/suggestions").json()
        assert after["suggestions"] == [] and after["snoozed_tests"] == []        # scheduled: nothing to suggest
        assert e.c.get(f"{API}/test-options?day={day}").json()["options"] == []


def test_api_no_still_takes_it_out_of_the_schedule_menu(monkeypatch):
    from backend.tests.test_plan_store import API
    with _env(monkeypatch) as e:
        e.inp["cur"]["test_suggestions"] = [CP_SUG]
        e.c.get(f"{API}/sessions")
        row = e.c.get(f"{API}/suggestions").json()["suggestions"][0]
        day = row["options"][0]["day"]
        e.c.post(f"{API}/suggestions/dismiss", json={"id": row["id"], "action": "declined"})
        got = e.c.get(f"{API}/suggestions").json()
        assert got["snoozed_tests"] == [] and e.c.get(f"{API}/test-options?day={day}").json()["options"] == []
        assert e.c.post(f"{API}/suggestions/accept", json={"id": row["id"], "day": day}).status_code == 400


JS = r"""
const vm = require("vm"), fs = require("fs");
const calls = [];
const el = () => new Proxy({}, { get: (t, k) => (k in t ? t[k] : k === "then" ? undefined : () => el()),
                                 set: (t, k, v) => { t[k] = v; return true; } });
const w = { dispatchEvent() {}, addEventListener() {} };
const answer = { suggestions: [{ id: "b2b:2026-10-12", type: "b2b" }], snoozed_tests: [{ id: "test:cp:2026-10-12", type: "test" }] };
const ctx = { window: w, CustomEvent: function (t) { this.type = t; }, setTimeout() {},
  document: { body: null, head: el(), createElement: el, addEventListener() {} },
  fetch: (u) => { calls.push(u); return Promise.resolve({ ok: true, json: () => Promise.resolve(answer) }); } };
vm.runInNewContext(fs.readFileSync(process.argv[1], "utf8"), ctx);
(async () => {
  const A = w.AppSuggestions;
  const [rows, all] = await Promise.all([A.load(false), A.load(false, true)]);
  console.log(JSON.stringify({ calls: calls.length, rows: rows.map((r) => r.id), held: all.snoozed_tests.map((r) => r.id) }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_box_shares_its_whole_answer_with_the_schedule():
    r = subprocess.run([NODE, "-e", JS, str(STATIC / "suggestions.js")], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {"calls": 1, "rows": ["b2b:2026-10-12"], "held": ["test:cp:2026-10-12"]}


def test_the_schedule_menu_counts_the_held_tests():
    html = (STATIC / "schedule.html").read_text(encoding="utf-8")
    body = html[html.index("async function sugCount"):]
    body = body[:body.index("const sugFits")]
    assert "(j.suggestions || []).concat(j.snoozed_tests || [])" in body
    assert 'CTX.sug = (rows || []).filter((x) => ["test", "baseline", "zone_test"].includes(x.type));' in body
