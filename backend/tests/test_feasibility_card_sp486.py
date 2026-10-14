"""賽季計畫的賽事評估卡照提示多寡 (SP-486 = docs/research/ui-hint-audit.md §7 T5; §5 R13, §5.2): the pills,
the cutoff, 跳太多 and the × 2 reminder in every level; the EP multiple by R13; 還沒進專項期 (PL15) and Koop's
weekly hours (PL17) under 「詳細」; PL6 only on the card; 精簡 = the pills, the cutoff, 跳太多 and what falls
short. The engine (race_feasibility.races → card_hints) on synthetic events and weeks; the card under node
with the real hints.js / feasibility.js and the zh-TW / en catalogs. Nothing reads the athlete's data."""
import datetime as dt
import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from backend.engine import hints as H
from backend.engine import race_feasibility as F
from backend.engine.planning import Event, Plan

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
LEVELS = ("full", "standard", "lean")
TODAY = date(2026, 10, 5)                                   # a Monday


def _ev(eid, days_out, **kw):
    a = dict(kind="race", priority="A", distance_km=42, climbing_m=2400, est_hours=7.5)
    a.update(kw)
    return Event(eid, f"賽事 {eid}", (TODAY + dt.timedelta(days=days_out)).isoformat(), **a)


def _hist(km, climb, hours, n):
    return [{"monday": (TODAY - dt.timedelta(weeks=n - i)).isoformat(), "km": km, "climb_m": climb, "hours": hours}
            for i in range(n)]


def _acts(*rows):
    return [{"date": d, "km": km, "climb_m": cl, "descent_m": cl, "hours": h, "minutes": h * 60, "idx": i, "foot": True}
            for i, (d, km, cl, h) in enumerate(rows)]


def _fin(moving_h, stop_h=0.5):
    return {"moving_h": moving_h, "stop_h": stop_h, "stop_src": "user", "stop_user_min": stop_h * 60, "short_min": None,
            "n_runs": None, "effort": 1.0}


def _races(monkeypatch, events, best_ep, finish_h, week=(20.0, 600.0, 3.0), long_run=(25, 900, 3.5)):
    km, cl, h = week
    monkeypatch.setattr(F, "weekly_history", lambda ds, today, weeks=4: _hist(km, cl, h, weeks))
    monkeypatch.setattr(F, "activity_rows",
                        lambda ds, today, days=42: _acts((TODAY - dt.timedelta(days=20), *long_run)))
    monkeypatch.setattr(F, "best_day_ep", lambda acts, div=None: {"ep": best_ep, "date": date(2026, 3, 1)})
    fins = dict(finish_h)
    return F.races(Plan(events=events), None, TODAY, predict=lambda e, c=None: [fins[e.id] - 0.5],
                   gpx=lambda e: None, finish=lambda e, c: _fin(fins[e.id] - 0.5))


def typical(monkeypatch):
    """One A race 90 days out: a 42 km / 2400 m trail race (cutoff 10 h, about 8 h), a typical build
    (20 km / 600 m / 3 h weeks — the weekly volume 有點趕), one class up at × 1.65."""
    return _races(monkeypatch, [_ev("a", 90, cutoff_hours=10.0)], best_ep=40.0, finish_h={"a": 8.0})[0]


def hard(monkeypatch):
    """Two races, one best day (EP 45, class S): "x" an 80 km / 4500 m race (EP 125, L — two classes up)
    over its 14 h cutoff; "y" an 80 km / 3000 m race (EP 110, M — one class up at × 2.4)."""
    evs = [_ev("x", 120, distance_km=80, climbing_m=4500, est_hours=16.0, cutoff_hours=14.0),
           _ev("y", 150, distance_km=80, climbing_m=3000, est_hours=14.0, cutoff_hours=24.0)]
    out = _races(monkeypatch, evs, best_ep=45.0, finish_h={"x": 16.0, "y": 14.0}, week=(60.0, 3000.0, 9.0))
    return {r["event_id"]: r for r in out}


# ---- the engine: the card's lines as hint objects --------------------------------------------------------

def test_typical_race_lines_are_the_main_advice_and_the_specific_phase_note(monkeypatch):
    r = typical(monkeypatch)
    by = {c["id"]: c for c in r["checks"]}
    assert r["days_to"] == 90 and by["weekly"]["level"] == "tight" and by["step"]["up"] == 1
    assert by["step"]["x2"] is False and "x2_note" not in r
    ids = [(h["id"], h["cls"], h.get("kind")) for h in r["hints"]]
    assert ids == [("PL9", 1, "advice"), ("PL15", 3, "note")]
    assert r["hints"][0]["text"] == r["suggestions"][0] == "照分段的心率上限跑，補給站少停"
    # the climb advice (a second tight one, class 2) stays under 詳細
    assert "每週多排一些爬坡：長跑挑爬升多的路線" in r["suggestions"][1:]
    assert not any(h["text"].startswith("每週多排一些爬坡") for h in r["hints"])
    # Koop's weekly hours carry their numbers (the 完整 chip); the text is unchanged
    hours = by["hours"]
    assert (hours["need_h"], hours["need"]) == (6.0, 3) and isinstance(hours["run"], int)
    assert next(c for c in r["readiness"]["checks"] if c["id"] == "hours")["need"] == 3
    json.dumps(r, ensure_ascii=False)                       # tagged suggestions are still plain JSON strings


def test_step_over_cutoff_over_and_x2_are_class_1_lines(monkeypatch):
    rs = hard(monkeypatch)
    x, y = rs["x"], rs["y"]
    hx = {h["id"]: h for h in x["hints"]}
    assert hx["PL14"]["cls"] == 1 and "超過關門時間" in hx["PL14"]["text"]
    assert hx["PL10"]["cls"] == 1 and "低一級（M）" in hx["PL10"]["text"]
    assert "PL13" not in hx and x["milestone"]["text"] in x["suggestions"]      # the milestone: 詳細, as before
    hy = {h["id"]: h for h in y["hints"]}
    st = next(c for c in y["checks"] if c["id"] == "step")
    assert st["x2"] is True and st["up"] == 1
    assert hy["PL12"]["cls"] == 1 and hy["PL12"]["mark"] == "⚠"
    assert hy["PL12"]["text"].startswith("級數只高一級，但距離和爬升是兩倍以上") and y["x2_note"] == hy["PL12"]["text"]
    assert "。級數只高一級" in st["text"]                    # the check's own sentence keeps it (詳細)


def test_notes_are_pl6_pl19_pl20_and_fingerprints_carry_the_event():
    r = {"event_id": "e1", "suggestions": [], "gpx_note": "超馬建議上傳 GPX", "split_note": "請補每天的距離和爬升",
         "stretch_note": "連續賽事以兩個睡眠點之間最硬的一段來比", "data_source": {"label": "資料還少", "text": "資料還少：…"},
         "optimistic_note": "這個估法對週跑量少的人偏樂觀", "readiness": {"note": "還沒到賽前 10 週的專項期"}}
    hs = F.card_hints(r)
    assert [h["id"] for h in hs] == ["PL6", "PL6", "PL19", "PL20", "PL20", "PL15"]
    other = F.card_hints({**r, "event_id": "e2"})
    assert hs[0]["fp"] != other[0]["fp"] and hs[0]["text"] == other[0]["text"]
    assert F.card_hints({"skipped": "C 賽當練習，不評估"}) == []
    # an untagged suggestion (an old or new line nobody tagged): PL9, class 1 — shown as before
    plain = F.card_hints({"event_id": "e1", "suggestions": ["一句沒標的建議", "第二句"]})
    assert [(h["id"], h["cls"]) for h in plain] == [("PL9", 1), ("PL9", 1)]


def test_the_new_catalog_rows():
    assert (H.CATALOG["PL19"].cls, H.CATALOG["PL19"].show) == (2, "●▷▷")
    assert (H.CATALOG["PL20"].cls, H.CATALOG["PL20"].show, H.CATALOG["PL20"].prio) == (2, "●●▷", 4)


# ---- the card under node ----------------------------------------------------------------------------------

def _render(races, level, locale="zh-TW", acks=()):
    js = r"""
const vm = require("vm"), fs = require("fs");
const dir = process.argv[1], loc = process.argv[3];
const cat = JSON.parse(fs.readFileSync(`${dir}/i18n/${loc}/common.json`, "utf8"));
const ctx = { DATA: JSON.parse(process.argv[2]), console };
ctx.window = ctx;
const el = () => ({ appendChild() {}, set textContent(v) {} });
ctx.document = { head: el(), createElement: el, getElementById: () => null };
ctx.I18N = { t: (k, p) => {
  let v = cat[k.replace(/^common\./, "")];
  if (v && typeof v === "object") v = p && p.n === 1 ? v.one : v.other;
  return String(v ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m));
} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(`${dir}/hints.js`, "utf8"), ctx);
vm.runInContext(fs.readFileSync(`${dir}/feasibility.js`, "utf8"), ctx);
vm.runInContext("OUT = DATA.races.map((r) => Feas.html(r, DATA.st));", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    data = {"races": races, "st": {"level": level, "acks": list(acks)}}
    r = subprocess.run([NODE, "-e", js, str(STATIC), json.dumps(data, ensure_ascii=False), locale],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _card(html):
    """(what is on the card, what is under 「詳細」) — the folded 「還有 N 則提醒」 list counts as its one line."""
    hidden = "".join(re.findall(r"<details[^>]*>.*?</details>", html, re.S))
    shown = re.sub(r"<details class=\"hint-more\">.*?</details>", '<span data-fi class="fold"></span>', html, flags=re.S)
    shown = re.sub(r"<details[^>]*>.*?</details>", "", shown, flags=re.S)
    return shown, hidden


def _count(html):
    return _card(html)[0].count("data-fi")


@needs_node
def test_acceptance_one_a_race_90_days_out_standard_at_most_8(monkeypatch):
    """驗收 1: one A race, 90 days out, 標準 — ≤ 8 on the card (10 before SP-486, counted the same way: two
    pills, six numbers next to them with 分級, the main advice, 還沒進專項期)."""
    r = typical(monkeypatch)
    std = _render([r], "standard")[0]
    shown, hidden = _card(std)
    assert _count(std) <= 8, shown
    assert _count(std) == 8                                  # 2 pills, 週量, 完賽/關門, 長天, 最大週量, B2B, the advice
    assert "分級" not in shown and "高一級，可以" in hidden    # one class up at × 1.65: 標準 puts it under 詳細
    assert "倍" not in shown                                 # R13: < 2 times — 詳細 only
    assert "還沒到賽前 10 週" not in shown and "還沒到賽前 10 週" in hidden    # PL15
    assert "每週 6 小時" not in shown and "超馬建議" in hidden                 # PL17 Koop
    assert "照分段的心率上限跑" in shown
    full, lean = (_render([r], lv)[0] for lv in ("full", "lean"))
    assert _count(full) == 12 and _count(lean) <= _count(std)     # 完整 adds 分級 × 1.7, Koop ×2, 還沒進專項期
    fs = _card(full)[0]
    assert "還沒到賽前 10 週" in fs and re.search(r"分級 XS → S · 最大單日的 1\.[67] 倍", fs) and "每週 6 小時：連續" in fs
    ls = _card(lean)[0]
    assert "還沒到賽前 10 週" not in lean                     # PL15 ✕ in 精簡
    assert "完賽 8 小時（關門 10）" in ls and "照分段的心率上限跑" in ls
    # 精簡: the weekly 有點趕 stays, B2B / the long day only when short — here both fall short
    assert "一週練量" in ls


@needs_node
@pytest.mark.parametrize("level", LEVELS)
def test_acceptance_step_over_cutoff_over_and_x2_in_every_level(monkeypatch, level):
    """驗收 2: 跳 ≥ 2 級、關門超過、× 2 提醒 on the card in 完整／標準／精簡."""
    rs = hard(monkeypatch)
    x, y = _render([rs["x"], rs["y"]], level)
    xs, ys = _card(x)[0], _card(y)[0]
    assert "分級 S → L" in xs and "完賽 16 小時（關門 14）" in xs              # the numbers
    assert "低一級（M）" in xs and "預估會超過關門時間" in xs                     # the class-1 lines
    assert 'class="hint-must"' in xs and "太難了" in xs
    assert "級數只高一級，但距離和爬升是兩倍以上" in ys                           # PL12
    assert ("最大單日的 2.4 倍" in ys) == (level != "lean")                      # PL11 (R13): ≥ 2 → 標準 too
    assert ("分級 S → M" in ys) == (level != "lean")                             # PL10 ◐: × 2 hides behind one class


@needs_node
def test_lean_keeps_only_what_falls_short(monkeypatch):
    ok = _races(monkeypatch, [_ev("o", 90, cutoff_hours=12.0)], best_ep=60.0, finish_h={"o": 8.0},
                week=(45.0, 2200.0, 7.0), long_run=(38, 2200, 6.0))[0]
    assert next(c for c in ok["checks"] if c["id"] == "weekly")["level"] == "ok"
    lean, std = _render([ok], "lean")[0], _render([ok], "standard")[0]
    ls = _card(lean)[0]
    assert "一週練量" not in ls and "一週練量" in _card(std)[0]                    # ok weekly: 精簡 hides it
    assert "完賽" in ls and ls.count("feas-pill") == 2                           # the pills and the cutoff stay
    assert _count(lean) < _count(std)
    rd = {c["id"]: c["level"] for c in ok["readiness"]["checks"]}
    for cid, word in (("long", "最長一次"), ("b2b", "連續兩天長距離")):
        assert (word in ls) == (rd[cid] in ("tight", "short")), (cid, rd[cid])


@needs_node
def test_got_it_puts_a_reminder_under_detail_and_class_1_stays():
    r = {"event_id": "e1", "name": "N", "date": "2027-01-01", "priority": "A", "days_to": 90, "level": "tight",
         "label": "有點趕", "checks": [], "suggestions": ["照分段的心率上限跑，補給站少停"],
         "gpx_note": "超馬建議上傳 GPX", "x2_note": "級數相同，但距離和爬升是兩倍以上"}
    r["suggestions"] = [H.tag("PL9", r["suggestions"][0])]
    r["hints"] = F.card_hints(r)
    gpx = next(h for h in r["hints"] if h["id"] == "PL6")
    html = _render([r], "standard", acks=[{"id": "PL6", "fp": gpx["fp"]}])[0]
    shown, hidden = _card(html)
    assert "超馬建議上傳 GPX" not in shown and "超馬建議上傳 GPX" in hidden and "再提醒我" in hidden
    assert "兩倍以上" in shown and "照分段的心率上限跑" in shown
    assert "超馬建議上傳 GPX" in _card(_render([r], "standard")[0])[0]           # PL6 ●● in 標準
    assert "超馬建議上傳 GPX" not in _card(_render([r], "lean")[0])[0]           # ▷ in 精簡


@needs_node
def test_the_card_in_english(monkeypatch):
    rs = hard(monkeypatch)
    y = _render([rs["y"]], "standard", locale="en")[0]
    assert "Class S → M · 2.4× your biggest day" in _card(y)[0]
    full = _render([typical(monkeypatch)], "full", locale="en")[0]
    assert "6 h a week: " in _card(full)[0] and "weeks in a row (need 3)" in full
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
        assert {"feas.k_mult", "feas.k_hours"} <= set(cat)


@needs_node
def test_plan_event_list_keeps_pl6_for_c_races_only():
    """PL6 只留評估卡: an upcoming A / B race has the card; a C race (no card) keeps the event-list line."""
    page = (STATIC / "plan.html").read_text("utf-8")
    fn = re.search(r"function evHints\(e\) \{.*?\n\}", page, re.S).group(0)
    js = ("const esc = (s) => String(s); const window = {}; " + fn +
          "; const e = (p) => ({priority: p, day_plan_missing: true, gpx_recommended: true, gpx: null, days_to: 30});"
          " console.log(JSON.stringify(['A', 'B', 'C'].map((p) => evHints(e(p)))));")
    out = json.loads(subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30, check=True).stdout)
    assert out[0] == "" and out[1] == "" and out[2].count("⚠") == 2
    assert '<script src="/api/v1/static/hints.js"></script>' in page and "Feas.html(x, feasHint)" in page
