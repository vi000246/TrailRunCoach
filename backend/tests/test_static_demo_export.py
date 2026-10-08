"""
The static demo export (backend/demo/export_static.py) and its browser shim
(backend/demo/static_shim.js): the file-name mapping both sides must agree
on, the page rewriting, the parameter spaces the export walks, and the
shim's rules (which writes are kept in the browser, which are refused) and
its schedule overlay. Synthetic data only; the JS checks run under node
(skipped without it).
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.demo import export_static as X

SHIM = Path(X.__file__).with_name("static_shim.js")
NODE = shutil.which("node")


# ---------------------------------------------------------------- keys / file names
def test_fnv64_known_vectors():
    assert X.fnv64("") == "cbf29ce484222325"
    assert X.fnv64("a") == "af63dc4c8601ec8c"
    assert X.fnv64("foobar") == "85944171f73967e8"


def test_data_key_sorts_query_by_name_and_decodes():
    assert X.data_key("/api/v1/x") == "/api/v1/x"
    assert X.data_key("/api/v1/x?b=2&a=1") == "/api/v1/x?a=1&b=2"
    # same-name values keep their order; blank values stay
    assert X.data_key("/api/v1/x?z=&a=2&a=1") == "/api/v1/x?a=2&a=1&z="
    # the path and the values are decoded: encodeURIComponent or not, one key
    a = X.data_key("/api/v1/wko5/views/%E6%88%91%E7%9A%84/dashboards/0/charts/1?sports=run%2Cwalk&begin=2026-01-01")
    b = X.data_key("/api/v1/wko5/views/我的/dashboards/0/charts/1?begin=2026-01-01&sports=run,walk")
    assert a == b == "/api/v1/wko5/views/我的/dashboards/0/charts/1?begin=2026-01-01&sports=run,walk"
    assert X.data_key("/api/v1/x?q=a+b") == "/api/v1/x?q=a b"


def test_data_file_and_post_file_are_stable_and_distinct():
    f = X.data_file("/api/v1/overview/plan/calendar?start=2026-09-28&end=2026-11-01")
    assert f == X.data_file("/api/v1/overview/plan/calendar?end=2026-11-01&start=2026-09-28")
    assert f.endswith(".json") and len(f) == 21
    p1 = X.post_file("POST", "/api/v1/racepower/plan", '{"a":1}')
    p2 = X.post_file("POST", "/api/v1/racepower/plan", '{"a":2}')
    assert p1 != p2 and p1.startswith("p")


def test_page_mapping_and_attribute_rewrite():
    assert X.static_page_for("/demo") == "index.html"
    assert X.static_page_for("/api/v1/overview/page/") == "overview.html"
    assert X.static_page_for("/api/v1/wko5/settings") is None          # not in the demo
    assert X.rewrite_attr_url("/api/v1/static/shell.js") == "static/shell.js"
    assert X.rewrite_attr_url("/api/v1/static/i18n/i18n.js") == "static/i18n/i18n.js"
    assert X.rewrite_attr_url("/api/v1/static/compare.html") is None
    assert X.rewrite_attr_url("/api/v1/wko5/viewer?view=x") == "charts.html?view=x"
    assert X.rewrite_attr_url("/api/v1/achievements/page") == "activities.html#achievements"
    assert X.rewrite_attr_url("/api/v1/wko5/settings#sport") is None


def test_transform_page_injects_the_shim_first_and_the_static_session():
    src = ('<!doctype html><html><head><script>window.TRC_SESSION = {"mode": "demo", "caps": ["upload.gpx"]};</script>'
           '<script src="/api/v1/static/i18n/i18n.js"></script></head><body>'
           '<script src="/api/v1/static/shell.js" data-page="x"></script>'
           '<a href="/api/v1/plan/page">p</a><a href="/api/v1/wko5/settings">s</a></body></html>')
    out = X.transform_page(src, {"snapshot": "2026-10-03"}, X.static_session("b1"))
    head = out[out.index("<head>") + 6:]
    assert head.startswith("<script>window.TRC_STATIC_CFG = ")
    assert head.index("static/trc_static.js") < head.index("i18n.js")
    assert '"static": true' in out and "upload.gpx" not in out
    assert 'src="static/shell.js"' in out and 'href="plan.html"' in out
    assert 'href="/api/v1/wko5/settings"' in out          # left to the shim (「示範版沒有這個頁面」)


def test_compute_posts_and_overlay_writes_are_separate():
    assert X.COMPUTE_POSTS.match("/api/v1/racepower/plan")
    assert X.COMPUTE_POSTS.match("/api/v1/racepower/course/event/e1")
    assert not X.COMPUTE_POSTS.match("/api/v1/racepower/saved/e1")
    assert X.is_overlay_write("PATCH", "/api/v1/overview/plan/sessions/u1")
    assert X.is_overlay_write("POST", "/api/v1/overview/plan/rest-days")
    assert not X.is_overlay_write("PUT", "/api/v1/overview/plan/prefs")
    assert not X.is_overlay_write("POST", "/api/v1/overview/plan/reconcile")


# ---------------------------------------------------------------- parameter spaces
def test_calendar_ranges_match_the_schedule_page():
    rs = X.calendar_ranges(dt.date(2026, 10, 3), months_back=0, months_ahead=0, weeks_back=0, weeks_ahead=0)
    # October 2026: Monday 9/28 .. Sunday 11/1 (the month view); the week of 10/3
    assert ("2026-09-28", "2026-11-01") in rs
    assert ("2026-09-28", "2026-10-04") in rs
    rs = X.calendar_ranges(dt.date(2026, 1, 15), months_back=1, months_ahead=0, weeks_back=0, weeks_ahead=0)
    assert ("2025-12-01", "2026-01-04") in rs           # December 2025 (crosses the year)


def test_viewer_ranges_default_presets_and_both_ytd_forms():
    rs = X.viewer_ranges(dt.date(2026, 10, 3), "2025-10-04T05:25:00")
    assert rs[0] == ("2026-07-06", "2026-10-03")        # the default: 90 天 (end - 89, SP-336)
    assert ("2025-10-04", "2026-10-03") in rs            # 1年 (end - 364)
    assert ("2026-09-27", "2026-10-03") in rs            # 7 天 (end - 6)
    assert ("2026-01-01", "2026-10-03") in rs and ("2025-12-31", "2026-10-03") in rs
    assert ("2025-10-04", "2026-10-03") in rs            # 全部
    assert len(rs) == len(set(rs))


def test_chart_toggles():
    res = {"period_toggle": True, "series": [{"type": "bar"}], "window_toggle": True, "window_choices": [7, 14],
           "basis_toggle": True, "basis_choices": ["pace", "power"],
           "variant_toggle": True, "variant_choices": [{"key": "tss"}, {"key": "pct"}]}
    t = X.chart_toggles(res, "athlete")
    assert ("period", "month") in t and ("window", "14") in t and ("basis", "power") in t and ("variant", "pct") in t
    assert X.chart_toggles({**res, "series": [{"type": "calendar"}]}, "athlete").count(("period", "day")) == 0
    w = X.chart_toggles(res, "workout")
    assert ("period", "day") not in w and ("basis", "pace") in w


def test_pick_activities_keeps_recent_and_special():
    acts = [{"index": i, "start": f"2026-01-{i + 1:02d}", "tss": i, "duration": 100, "activity_type": "training"}
            for i in range(30)]
    acts[2]["activity_type"] = "race"
    acts[3]["excluded"] = {"label": "bad"}
    out = X.pick_activities(acts, 8)
    assert len(out) == 8 and 29 in out and 2 in out and 3 not in out
    assert len(X.pick_activities(acts, 0)) == 29


def test_scrubber_and_leak_check(tmp_path):
    s = X.scrubber([r"C:\Users\someone\proj"])
    assert s(r'{"path": "C:\\Users\\someone\\proj\\views\\a.json"}') == r'{"path": "demo\\views\\a.json"}'
    assert s("C:/Users/someone/proj/x") == "demo/x"
    (tmp_path / "a.json").write_text('{"p": "C:/Users/someone/x"}', "utf-8")
    assert X.check_output(tmp_path, ["someone"]) and not X.check_output(tmp_path, ["nobody"])


def test_prepare_out_refuses_a_foreign_folder(tmp_path):
    (tmp_path / "keep.txt").write_text("x", "utf-8")
    with pytest.raises(SystemExit):
        X.prepare_out(tmp_path)
    out = tmp_path / "site"
    X.prepare_out(out)
    (out / "data" / "old.json").write_text("{}", "utf-8")
    X.prepare_out(out)                                    # our own export: replaced
    assert not (out / "data" / "old.json").exists()


# ---------------------------------------------------------------- the shim (node)
def _node(script: str):
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_and_python_keys_agree():
    urls = ["/api/v1/x?b=2&a=1", "/api/v1/wko5/views/%E6%88%91%E7%9A%84/dashboards/0/charts/1?sports=run%2Cwalk&begin=x",
            "/api/v1/x?q=a+b&z=&a=2&a=1", "/api/v1/overview/plan/calendar?start=2026-09-28&end=2026-11-01"]
    js = _node(f"""
const S = require({json.dumps(str(SHIM))});
const out = {json.dumps(urls)}.map((u) => {{ const x = new URL(u, "http://h"); return [S.dataKey(x.pathname, x.search), S.dataFile(x.pathname, x.search)]; }});
out.push(S.postFile("post", "/api/v1/racepower/plan", "", '{{"a":1}}'));
console.log(JSON.stringify(out));""")
    for u, (k, f) in zip(urls, js):
        assert k == X.data_key(u) and f == X.data_file(u)
    assert js[-1] == X.post_file("POST", "/api/v1/racepower/plan", '{"a":1}')


OVERLAY_SCRIPT = """
const S = require(%(shim)s);
const base = {today: "2026-10-03", blackouts: [], sessions: [
  {uid: "a", day: "2026-10-05", kind: "easy", title: "輕鬆跑", minutes: 60, tss: 40, state: "active"},
  {uid: "b", day: "2026-10-06", kind: "quality", title: "間歇", minutes: 90, tss: 90, state: "active"},
  {uid: "c", day: "2026-10-01", kind: "easy", title: "過去", minutes: 30, tss: 20, state: "done"}],
  week_rows: [{start: "2026-10-05", end: "2026-10-11", planned_hours: 2.5, planned_tss: 130}],
  weeks: [{start: "2026-10-05", hours: 2.5, tss: 130}]};
const ov = S.emptyOverlay("2026-10-03");
const all = () => S.applyCalendar(base, ov, "2026-09-28", "2026-10-18").sessions;
const ctx = {today: "2026-10-03", find: (u) => all().find((s) => s.uid === u) || null,
  weekSessions: (d) => all().filter((s) => S.mondayOf(s.day) === S.mondayOf(d)), blocked: () => false,
  suggestion: (k) => k === "cp" ? {kind: "cp", title: "CP 測試", minutes: 50, tss: 70} : null};
const r = {};
const err = (f) => { try { f(); return null; } catch (e) { return [e.status, e.message]; } };
// move a: the session follows, the week's planned totals don't change (same week)
r.moved = S.opPatch(ov, "a", {day: "2026-10-07"}, ctx).day;
r.afterMove = S.applyCalendar(base, ov, "2026-09-28", "2026-10-18");
// edit b: 30 min shorter, TSS 60 -> the week loses 0.5 h / 30 TSS
S.opPatch(ov, "b", {title: "短間歇", minutes: 60, tss: 60}, ctx);
r.afterEdit = S.applyCalendar(base, ov, "2026-09-28", "2026-10-18");
r.past = err(() => S.opPatch(ov, "a", {day: "2026-10-01"}, ctx));
r.done = err(() => S.opPatch(ov, "c", {day: "2026-10-08"}, ctx));
r.variant = err(() => S.opPatch(ov, "b", {variant_key: "x"}, ctx));
// add + delete it again: nothing left of it
const added = S.opAdd(ov, {day: "2026-10-09", kind: "easy", title: "加跑", minutes: 30, tss: 20}, ctx);
r.added = added.uid;
r.withAdd = S.applyCalendar(base, ov, "2026-09-28", "2026-10-18").week_rows[0];
S.opDelete(ov, added.uid, ctx);
r.addDeleted = Object.keys(ov.ses).includes(added.uid);
// a test from the saved suggestions
r.test = S.opScheduleTest(ov, {kind: "cp", day: "2026-10-10"}, ctx).title;
r.noTest = err(() => S.opScheduleTest(ov, {kind: "zz", day: "2026-10-10"}, ctx));
// rest day on a's new day: a moves to a free day of that week; undo puts it back
r.rest = S.opRestAdd(ov, "2026-10-07", ctx);
r.restCal = S.applyCalendar(base, ov, "2026-09-28", "2026-10-18");
r.restUndo = S.opRestDel(ov, "2026-10-07");
r.aAfterUndo = S.applyCalendar(base, ov, "2026-09-28", "2026-10-18").sessions.find((s) => s.uid === "a").day;
// a range that doesn't hold the session: it isn't shown there
r.otherRange = S.applyCalendar(base, ov, "2026-10-12", "2026-10-18").sessions.map((s) => s.uid);
// rules: which writes the overlay answers
r.rules = [["PATCH", "/api/v1/overview/plan/sessions/x"], ["DELETE", "/api/v1/overview/plan/rest-days/2026-10-07"],
  ["PUT", "/api/v1/overview/plan/prefs"], ["POST", "/api/v1/overview/plan/reconcile"],
  ["PUT", "/api/v1/plan/events"]].map(([m, p]) => !!S.overlayRule(m, p));
r.unchanged = S.applyCalendar(base, S.emptyOverlay("x"), "a", "b") === base;
console.log(JSON.stringify(r));
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_shim_schedule_overlay():
    r = _node(OVERLAY_SCRIPT % {"shim": json.dumps(str(SHIM))})
    assert r["moved"] == "2026-10-07"
    a = next(s for s in r["afterMove"]["sessions"] if s["uid"] == "a")
    assert a["day"] == "2026-10-07" and a["edited"] is True
    assert r["afterMove"]["week_rows"][0]["planned_tss"] == pytest.approx(130)
    wr = r["afterEdit"]["week_rows"][0]
    assert wr["planned_hours"] == pytest.approx(2.0) and wr["planned_tss"] == pytest.approx(100)
    assert r["afterEdit"]["weeks"][0]["tss"] == pytest.approx(100)
    b = next(s for s in r["afterEdit"]["sessions"] if s["uid"] == "b")
    assert b["title"] == "短間歇" and b["tss_est"] == 60
    assert r["past"][0] == 400 and r["done"][0] == 400 and r["variant"][0] == 403
    assert r["added"].startswith("local-")
    assert r["withAdd"]["planned_tss"] == pytest.approx(120)
    assert r["addDeleted"] is False
    assert r["test"] == "CP 測試" and r["noTest"][0] == 403
    assert r["rest"]["changes"][0]["uid"] == "a" and r["rest"]["changes"][0]["to"] != "2026-10-07"
    assert any(b["start"] == "2026-10-07" and b["kind"] == "rest" for b in r["restCal"]["blackouts"])
    assert not any(s["day"] == "2026-10-07" and s["uid"] == "a" for s in r["restCal"]["sessions"])
    assert r["aAfterUndo"] == "2026-10-07"
    assert "a" not in r["otherRange"]
    assert r["rules"] == [True, True, False, False, False]
    assert r["unchanged"] is True
