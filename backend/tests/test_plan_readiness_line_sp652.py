"""SP-652: the 完備程度 row of the 賽事評估 card on the season-plan page is one sentence from the 賽前完備程度
board (race_readiness, SP-650) and a link to that card in 圖表分析 (專項期; 減量期 once the taper started).

The sentence (race_readiness.line) on synthetic boards; the API (GET /api/v1/overview/feasibility →
ready_line) on the demo athlete; the card (feasibility.js) under node in zh-TW and en."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from backend.engine import race_readiness as RD
from backend.tests import demo_fixtures as F

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")
SOURCE_WORDS = ("Koop", "Uphill", "Friel", "Pfitzinger", "Bosquet", "Seiler", "Mujika", "Daniels", "Magness")


def _stage(sid, name, done, total, unlocked=True):
    ok = RD.passed(done, total)
    label = "照進度" if ok else f"還差 {RD.need(done, total)} 項"
    return {"id": sid, "name": name, "done": done, "total": total, "passed": ok, "need": RD.need(done, total),
            "unlocked": unlocked, "state": "lock" if not unlocked else "passed" if ok else "open", "label": label}


def _board(stages, today="2026-10-16", taper_from="2026-11-20", preview=False, text="到上週為止該達成 3 個、做到 2 個"):
    return {"race": {"id": "r1", "name": "Race"}, "today": today, "taper_from": taper_from, "preview": preview,
            "stages": stages, "text": text}


# ---- the sentence ---------------------------------------------------------------------------------------

def test_line_names_the_first_open_unlocked_stage():
    b = _board([_stage("volume", "專項量", 3, 4), _stage("validation", "表現驗證", 0, 2),
                _stage("race_state", "比賽狀態", 0, 0, unlocked=False)])
    ln = RD.line(b)
    assert ln["text"] == "賽前準備：第 2 段〈表現驗證〉0/2（還差 2 項）", ln
    assert ln["phase"] == "build" and ln["race_id"] == "r1"


def test_line_on_track_and_all_passed_keeps_the_last_unlocked_stage():
    b = _board([_stage("volume", "專項量", 4, 4), _stage("validation", "表現驗證", 2, 2),
                _stage("race_state", "比賽狀態", 0, 0, unlocked=False)])
    assert RD.line(b)["text"] == "賽前準備：第 2 段〈表現驗證〉2/2（照進度）"


def test_line_in_the_taper_links_the_taper_page():
    # SP-677: in the taper the sentence is the 減量期 card's (race_readiness.taper)
    t = {"kind": "taper", "race": {"id": "r1"}, "pre": False, "text": "賽前 6 天", "ok": 3,
         "checks": [{}, {}, {}, {}]}
    ln = RD.line(t)
    assert ln["phase"] == "taper" and ln["text"] == "減量期：賽前 6 天，4 項檢查 3 項 OK", ln


def test_preview_and_no_a_race_take_the_engines_text():
    pv = _board([_stage("volume", "專項量", 0, 0, unlocked=False)], preview=True, text="專項期從 11/2 開始")
    assert RD.line(pv)["text"] == "專項期從 11/2 開始"
    none = RD.board(None, dt.date(2026, 10, 16), [])
    ln = RD.line(none)
    assert ln["text"] == none["text"] and ln["race_id"] is None


def test_line_in_english():
    from backend.i18n import use_locale
    b = _board([_stage("volume", "專項量", 1, 4), _stage("validation", "表現驗證", 0, 0, unlocked=False)])
    with use_locale("en"):
        b["stages"][0]["name"] = "Race-specific volume"
        b["stages"][0]["label"] = "2 more to go"
        t = RD.line(b)["text"]
    assert t.startswith("Race prep: stage 1") and "1/4" in t and "2 more to go" in t, t
    assert not any("一" <= ch <= "鿿" for ch in t), t


# ---- the API on the demo athlete ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from backend.demo import build as B
    root = tmp_path_factory.mktemp("demo-sp652")
    B.build(root, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    return root


@pytest.fixture
def demo(built, monkeypatch):
    F.demo_env(monkeypatch, built)
    real_connect = socket.socket.connect

    def _no_net(self, addr, *a, **k):
        if isinstance(addr, tuple) and addr and str(addr[0]) in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, addr, *a, **k)
        raise OSError("network disabled in tests")
    monkeypatch.setattr(socket.socket, "connect", _no_net)
    from backend.api import wko5views
    wko5views._dataset_cfg.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False) as c:
        c.get("/demo")
        yield c
    wko5views._dataset_cfg.cache_clear()


def test_api_gives_the_line_and_a_link_to_the_readiness_card(demo):
    r = demo.get("/api/v1/overview/feasibility")
    assert r.status_code == 200, r.text[:300]
    ln = r.json().get("ready_line")
    assert ln and ln["text"], r.json().keys()
    q = parse_qs(urlparse(ln["href"]).query)
    v = next(x for x in demo.get("/api/v1/app/views").json() if x["name"] == q["view"][0])
    d = v["dashboards"][int(q["dash"][0])]
    want = {"build": "race-readiness", "taper": "race-readiness-taper"}[ln["phase"]]
    assert d["charts"][int(q["chart"][0])]["id"] == want, (ln, d.get("id"))
    assert not any(w in ln["text"] for w in SOURCE_WORDS)


def test_api_for_one_event_skips_the_board(demo, monkeypatch):
    """The race calculator asks for one event: no readiness line there (and no board to compute)."""
    from backend.engine import race_readiness as R
    monkeypatch.setattr(R, "compute", lambda *a, **k: (_ for _ in ()).throw(AssertionError("computed")))
    evs = demo.get("/api/v1/plan").json().get("events") or []
    if not evs:
        pytest.skip("demo plan without events")
    r = demo.get("/api/v1/overview/feasibility", params={"event_id": evs[0]["id"]})
    assert r.status_code == 200 and "ready_line" not in r.json()


# ---- the card under node --------------------------------------------------------------------------------

def _render(races, locale="zh-TW"):
    js = r"""
const vm = require("vm"), fs = require("fs");
const dir = process.argv[1], loc = process.argv[3];
const cat = JSON.parse(fs.readFileSync(`${dir}/i18n/${loc}/common.json`, "utf8"));
const ctx = { DATA: JSON.parse(process.argv[2]), console };
ctx.window = ctx;
const el = () => ({ appendChild() {}, set textContent(v) {} });
ctx.document = { head: el(), createElement: el, getElementById: () => null };
ctx.I18N = { t: (k, p) => String(cat[k.replace(/^common\./, "")] ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m)) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(`${dir}/feasibility.js`, "utf8"), ctx);
vm.runInContext("OUT = DATA.map((r) => Feas.html(r, { level: 'standard', acks: [] }));", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), json.dumps(races, ensure_ascii=False), locale],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


RACE = {"event_id": "r1", "name": "Race", "date": "2026-12-01", "priority": "A", "days_to": 46, "level": "ok",
        "label": "來得及", "checks": [], "suggestions": [],
        "readiness": {"level": "short", "label": "不足", "checks": [{"level": "short", "label": "不足", "text": "最長 3 h"}],
                      "src": ["Koop 2016"]}}


@needs_node
@pytest.mark.parametrize("loc,link", [("zh-TW", "→ 看專項期"), ("en", "→ See the build phase")])
def test_card_shows_one_sentence_and_the_link(loc, link):
    line = {"race_id": "r1", "text": "賽前準備：第 1 段〈專項量〉1/4（還差 2 項）", "phase": "build",
            "href": "/api/v1/app/viewer?view=x&dash=3&chart=0"}
    h = _render([{**RACE, "ready_line": line}], loc)[0]
    assert line["text"] in h and 'href="/api/v1/app/viewer?view=x&amp;dash=3&amp;chart=0"' in h
    assert link in h, h
    assert "不足" not in h and "最長 3 h" not in h and "Koop" not in h   # the old row, its checks and sources


@needs_node
def test_card_in_the_taper_links_the_taper():
    line = {"race_id": "r1", "text": "x", "phase": "taper", "href": "/v"}
    assert "→ 看減量期" in _render([{**RACE, "ready_line": line}])[0]


@needs_node
def test_card_without_a_line_keeps_the_old_row():
    h = _render([RACE])[0]
    assert "不足" in h


def test_plan_page_hands_the_line_to_its_race():
    page = (STATIC / "plan.html").read_text("utf-8")
    assert "r.ready_line" in page and "ready_line:" in page
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "common.json").read_text("utf-8"))
        assert {"feas.ready_go_build", "feas.ready_go_taper"} <= set(cat)
