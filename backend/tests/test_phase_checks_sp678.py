"""SP-678: the 轉換期 恢復檢查 card, the 基礎期 基礎檢查 card and the 強度課表解鎖條件 card that replaces the
5 區開放流程 (z5flow) panel on the 基礎期 page (engine/phase_checks.py, static/readiness.js). Synthetic
rows for the cards; the view, the API on the demo athlete and the viewer's strings at the end."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pytest

from backend.engine import phase_checks as PC
from backend.engine import quality_gate as QG

ROOT = Path(__file__).resolve().parents[1]
TODAY = dt.date(2026, 11, 23)                       # a Monday
RACE_END = dt.date(2026, 11, 14)                    # 賽後第 9 天
SOURCE_WORDS = ("Koop", "Uphill", "Friel", "Pfitzinger", "Bosquet", "Seiler", "Hickson", "Mujika", "Daniels",
                "研討會", "（UA", "推估", "Coyle", "Canova", "Higdon")


def chk(card, cid):
    return next(c for c in card["checks"] if c["id"] == cid)


# ---- 驗收 1: the 轉換期 card -----------------------------------------------------------------------------

SPANS = [{"kind": "recovery", "start": "2026-11-15", "end": "2026-11-21"},
         {"kind": "transition", "start": "2026-11-22", "end": "2026-12-12"}]
BASE_FROM = dt.date(2026, 12, 13)


def tr(acts=(), hr=None, injuries=(), size=2, today=TODAY, pre=8.0):
    return PC.transition({"name": "山徑 50K", "end": RACE_END, "size": size}, today, list(acts), spans=SPANS,
                         base_from=BASE_FROM, pre_hours=pre, hr=hr, injuries=injuries)


def run(d, h=0.5):
    return {"date": d, "hours": h, "run": True}


def test_transition_says_the_day_after_the_race_its_weeks_and_the_base_start():
    t = tr()
    assert t["text"] == "賽後第 9 天" and t["day_after"] == 9
    assert t["weeks"] == 4 and t["week"] == 2                          # 11/15 → 12/12, today in week 2
    assert [s["state"] for s in t["strip"]] == ["past", "today", "next", "next", "base"]
    assert t["strip"][-1]["label"] == "→ 基礎期 12/13"
    assert [c["id"] for c in t["checks"]] == ["rest", "easy_only", "volume", "injury"]
    for c in t["checks"]:
        assert c["show"] and c["target"] and c["label"] and c["state"] in ("ok", "warn", "wait", "lock"), c
    assert [c["text"] for c in t["conds"]][0] == "休息做到"


def test_rest_days_grow_with_the_race_and_an_early_run_is_flagged():
    # a 馬拉松級 race: ≥ 3 days; first run on day 4 → 3 days rested
    c = chk(tr([run(RACE_END + dt.timedelta(days=4))], size=2), "rest")
    assert c["value"] == 3 and c["state"] == "ok" and "至少 3 天" in c["target"]
    # a 100 英里級 race: ≥ 7 days; the same run is too early, with what to do
    c = chk(tr([run(RACE_END + dt.timedelta(days=4))], size=4), "rest")
    assert c["state"] == "warn" and c["todo"] and c["label"] == "太早跑"
    assert PC.REST_DAYS == (2, 2, 3, 5, 7)


def test_easy_only_and_volume_back_slowly():
    c = chk(tr(hr={"low": 3600 * 0.95, "all": 3600}), "easy_only")
    assert c["value"] == pytest.approx(0.95) and c["state"] == "ok" and c["band"] == [0.90, 1.0]
    c = chk(tr(hr={"low": 1800, "all": 3600}), "easy_only")
    assert c["state"] == "warn" and c["todo"]
    # this week (Mon 11/23, 轉換期: 50 % of 8 h) — 6.2 h done = 78 % > 60 % → 偏多
    c = chk(tr([run(TODAY, 6.2)]), "volume")
    assert c["value"] == 0.78 and c["state"] == "warn" and c["label"] == "偏多" and c["todo"]
    assert c["band"] == [0, 0.5]
    assert chk(tr([run(TODAY, 3.0)]), "volume")["state"] == "ok"


def test_injury_card_reads_the_injury_log_and_the_conditions_to_start_the_base():
    ev = {"onset_date": "2026-11-19", "status": "active", "area": "knee", "side": "left"}
    t = tr(injuries=[ev])
    c = chk(t, "injury")
    assert c["state"] == "warn" and c["show"] == "左膝" and "第 5 天" in c["target"]
    assert t["ready"] is False and {"text": "沒有痠痛", "ok": False} in t["conds"]


def test_transition_without_a_recent_a_race_explains_itself():
    t = PC.transition(None, TODAY, [], spans=[], base_from=None, pre_hours=None)
    assert t["race"] is None and t["checks"] == [] and "A 賽" in t["text"]


# ---- 驗收 2: the 基礎期 card ------------------------------------------------------------------------------

def weeks(mid=(0.10, 0.12, 0.14, 0.04, 0.10, 0.15, 0.17, 0.18), hours=(6, 6.3, 6.6, 4.0, 6.5, 6.8, 7.0, 3.0)):
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    out = []
    for k, (m, h) in enumerate(zip(mid, hours)):
        t = 3600 * h
        out.append({"monday": mon - dt.timedelta(weeks=7 - k), "hours": h, "low": t * (0.9 - m), "mid": t * m,
                    "high": t * 0.10})
    return out


def ctl_rising(pct=0.036, start=40.0, days=70):
    d0 = TODAY - dt.timedelta(days=days)
    return [(d0 + dt.timedelta(days=i), start * (1 + pct) ** (i / 7)) for i in range(days + 1)]


def test_base_intensity_is_a_donut_and_eight_stacked_weeks_with_targets():
    b = PC.base(TODAY, weeks(), ctl_rising())
    assert [c["id"] for c in b["checks"]] == ["intensity", "ramp", "recovery"]
    c = chk(b, "intensity")
    assert len(c["donut"]) == 3 and sum(c["donut"]) == pytest.approx(1.0, abs=0.01)
    assert len(c["bars"]) == 8 and all(sum(x["shares"]) == pytest.approx(1.0, abs=0.01) for x in c["bars"])
    assert c["bars"][3]["recovery"] and c["bars"][-1]["now"]
    assert [t["target"] for t in c["targets"]] == ["目標 ≥ 80", "目標 5–10", "目標 10–15"]
    # middle up 4 weeks in a row (recovery week left out) → say how to change it
    assert c["state"] == "warn" and c["label"] == "中間偏多" and "連 4 週" in c["todo"]
    assert c["chart"]["id"] == "intensity-distribution"


def test_base_weekly_increase_is_chronic_load_percent_in_the_2_to_5_band():
    from backend.engine import load_guard as LG
    b = PC.base(TODAY, weeks(), ctl_rising(0.036))
    c = chk(b, "ramp")
    assert c["value"] == pytest.approx(0.036, abs=0.002) and c["state"] == "ok" and c["label"] == "照計畫"
    assert c["band"] == [0.02, 0.05] and c["goal"] == 0.04 and c["watch"] == LG.WATCH_PCT == 0.10 and c["line"]
    assert all(w in c["target"] for w in ("2 %", "5 %", "4 %", "10 %"))
    assert re.fullmatch(r"\+3\.\d %（\+\d\.\d 點／週）", c["show"]), c["show"]
    assert len(c["weeks"]) == 8 and c["chart"]["id"] == "ramp-rate"
    rec = [w for w in c["weeks"] if w["recovery"]]
    assert rec and all(w["zone"] is None for w in rec)                            # grey, not judged
    assert all(w["zone"] == "ok" and w["pts"] for w in c["weeks"] if not w["recovery"] and w["pct"] is not None)
    for pct, zone, state, label in ((0.07, "fast", "warn", "偏快"), (0.12, "high", "warn", "注意"),
                                    (0.01, "low", "warn", "沒在進步")):
        c = chk(PC.base(TODAY, weeks(), ctl_rising(pct)), "ramp")
        assert (c["weeks"][-1]["zone"], c["state"], c["label"]) == (zone, state, label) and c["todo"], pct


def test_base_recovery_rhythm_three_to_one_and_the_next_one():
    c = chk(PC.base(TODAY, weeks(), ctl_rising()), "recovery")
    # recovery week 4 weeks ago; 3 build weeks since → this week is the recovery week
    assert c["show"] == "第 4 週" and c["next"] == TODAY.isoformat() and "70 %" in c["target"]
    c = chk(PC.base(TODAY, weeks(hours=(6, 6.3, 6.6, 6.8, 7.0, 4.0, 6.5, 2.0)), ctl_rising()), "recovery")
    assert c["show"] == "第 2 週" and c["next"] == (TODAY + dt.timedelta(weeks=2)).isoformat()


def test_base_card_header_and_this_week():
    ph = {"kind": "base", "start": "2026-10-26", "end": "2027-01-31", "next_start": "2027-02-01", "next_kind": "specific"}
    b = PC.base(TODAY, weeks(), ctl_rising(), phase=ph)
    assert b["text"] == "基礎期第 5 週 / 14 週" and b["next"] == "專項期 2027-02-01 開始"
    assert b["this_week"] and len(b["this_week"]) <= 3


# ---- 驗收 3: 強度課表解鎖條件 (replaces z5flow) --------------------------------------------------------------

def gate(z3_open=True, aet_ok=False, z3_ok=True, lthr_invalid=None):
    tests = [{"key": "weeks", "ok": z3_open}, {"key": "xu90", "ok": None}, {"key": "ua_gap", "ok": None}]
    return {"z3": {"open": z3_open, "path_label": "x", "since": "2026-10-20" if z3_open else None, "weeks": 4,
                   "tests": tests},
            "z5": {"aet_tested": None}, "lthr": {"value": 165, "measured": True, "invalid": lthr_invalid},
            "rebase": None}


def card(aet_ok=False, z3_ok=True, state="unconfirmed"):
    return {"state": state, "z5_gate": {"open": aet_ok and z3_ok, "aet_ok": aet_ok, "z3_ok": z3_ok},
            "z3": {"done": 2 if z3_ok else 1, "need": 2}, "base": {"tests": [{"key": "aet_ua_gap", "ok": None}]}}


def test_two_tiles_and_the_one_not_unlocked_is_selected():
    u = PC.unlock(card(), gate(), TODAY, low_share=0.92, last_run=TODAY - dt.timedelta(days=1),
                  next_a={"name": "A", "start": "2027-04-10"})
    assert [t["zone"] for t in u["tiles"]] == [3, 5]
    assert u["tiles"][0]["open"] and u["tiles"][0]["sub"] == "10/20 解鎖"
    assert not u["tiles"][1]["open"] and u["tiles"][1]["sub"] == "還差 1 項"
    assert u["selected"] == 5 and u["z5"]["head"] == "解鎖 5 區：還差 1 項"
    assert [c["id"] for c in u["z5"]["checks"]] == ["z3_first", "aet_test", "low_share"]
    assert chk(u["z5"], "aet_test")["state"] == "warn" and u["z5"]["chips"] == ["排一次：平路 AeT 附近跑 60 分鐘"]
    u = PC.unlock(card(), gate(z3_open=False), TODAY)
    assert u["selected"] == 3 and u["z3"]["head"].startswith("解鎖 3 區")


def test_after_an_a_race_a_zone3_test_passed_before_is_not_ticked():
    g = gate(z3_open=False)
    g["z3"]["tests"][0]["ok"] = True                    # the consistency weeks were met before the race
    g["z3"]["rebase"] = True
    g["rebase"] = {"text": "A 賽「山徑 50K」後重新打底", "from": "2026-11-15"}
    u = PC.unlock(card(), g, TODAY)
    c = chk(u["z3"], "z3_base")
    assert not any(p["ok"] for p in c["paths"]) and "只算 11/15 之後的" in c["target"]


def test_relock_says_days_left_and_how_far_from_the_floor():
    u = PC.unlock(card(), gate(), TODAY, low_share=0.92, last_run=TODAY - dt.timedelta(days=1),
                  next_a={"name": "A", "start": "2027-04-10"})
    b = u["z3"]["relock"][0]
    assert b["key"] == "break" and b["days_left"] == QG.Z3_RELOCK_DAYS - 1 == 20
    assert "還有 20 天" in b["now"] and "12/13 前要再跑" in b["now"]
    assert u["z3"]["relock_tag"] == "目前安全・還有 20 天"
    assert "4/10" in u["z3"]["relock"][-1]["now"]
    ls = next(r for r in u["z5"]["relock"] if r["key"] == "low_share")
    assert "現在 92 %" in ls["now"] and "還差 17 %" in ls["now"]      # floor 75 % (QG.LOW_SHARE_MIN)
    lt = next(r for r in u["z5"]["relock"] if r["key"] == "lthr")
    assert lt["now"] == "現在沒有失效"
    u = PC.unlock(card(), gate(lthr_invalid={"text": "LTHR 測完後停跑 30 天（≥ 4 週）"}), TODAY)
    assert "停跑 30 天" in next(r for r in u["z5"]["relock"] if r["key"] == "lthr")["now"]


def test_unlock_reads_the_gate_not_its_own_rules():
    # the engine's numbers: 21 days, 75 %, 2 Zone 3 sessions in 6 weeks
    assert (QG.Z3_RELOCK_DAYS, QG.LOW_SHARE_MIN, QG.Z5_Z3_NEED, QG.Z5_Z3_DAYS) == (21, 0.75, 2, 42)
    u = PC.unlock(card(aet_ok=True, z3_ok=True), gate(), TODAY)
    assert u["tiles"][1]["open"] and u["z5"]["head"] == "5 區已解鎖"


# ---- the view, the API and the viewer -------------------------------------------------------------------

def _view():
    from backend.engine.wko5expr.customviews import REPO_VIEWS, parse_view
    return parse_view(json.loads((REPO_VIEWS / "periodization.json").read_text(encoding="utf-8")))


def test_view_puts_the_cards_on_the_transition_and_base_pages():
    v = _view()
    d = {x["id"]: x for x in v["dashboards"]}
    first = d["transition"]["charts"][0]
    assert first["kind"] == "readiness" and first.get("phase") == "transition" and first["id"] == "recovery-check"
    first = d["base"]["charts"][0]
    assert first["kind"] == "readiness" and first.get("phase") == "base" and first["id"] == "base-check"
    z = next(c for c in d["base"]["charts"] if c["kind"] == "z5gate")
    assert z["title"] == "強度課表解鎖條件"
    en = json.loads((ROOT.parent / "views" / "i18n" / "en.json").read_text(encoding="utf-8"))["periodization"]["charts"]
    for cid in ("recovery-check", "base-check", "z5-gate"):
        c = next(c for x in v["dashboards"] for c in x["charts"] if c.get("id") == cid)
        assert not any(w in c["description"] or w in c["title"] for w in SOURCE_WORDS), cid
        assert en[cid]["title"] and en[cid]["description"]
        assert not any(w in en[cid]["description"] for w in SOURCE_WORDS), cid


def test_viewer_draws_the_new_cards_and_has_their_strings():
    js = (ROOT / "static" / "readiness.js").read_text("utf-8")
    viewer = (ROOT / "static" / "charts.html").read_text("utf-8")
    assert "drawTransition" in js and "drawBase" in js and "function unlock(" in js
    assert "Readiness.unlock" in viewer
    keys = set(re.findall(r'T\("([a-z0-9_.]+)"[,)]', js))
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / "static" / "i18n" / loc / "viewer.json").read_text("utf-8"))
        miss = {k for k in keys if "readiness." + k not in cat}
        assert not miss, (loc, miss)
    assert not any(w in js for w in SOURCE_WORDS)


def test_engine_strings_have_english():
    src = (ROOT / "engine" / "phase_checks.py").read_text("utf-8")
    en = json.loads((ROOT / "i18n" / "locales" / "en.json").read_text("utf-8"))
    ids = set(re.findall(r'\b_\("([^"]+)"', src)) | set(re.findall(r'N_\("([^"]+)"', src))
    miss = {m for m in ids if not en.get(m)}
    assert not miss, sorted(miss)[:10]
    assert not any(w in m for m in ids for w in SOURCE_WORDS)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    from backend.demo import build as B
    root = tmp_path_factory.mktemp("demo-sp678")
    B.build(root, anchor=dt.date.today(), weeks=8, small=True, warm=False)
    return root


@pytest.fixture
def demo(built, monkeypatch):
    from backend.tests import demo_fixtures as F
    import socket
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


def _get(c, did, cid):
    v = next(x for x in c.get("/api/v1/app/views").json() if x["name"] == "周期化訓練")
    di = next(i for i, d in enumerate(v["dashboards"]) if d.get("id") == did)
    ci = next(i for i, ch in enumerate(v["dashboards"][di]["charts"]) if ch.get("id") == cid)
    r = c.get(f"/api/v1/app/views/周期化訓練/dashboards/{di}/charts/{ci}?begin=2026-01-01")
    assert r.status_code == 200, r.text[:500]
    return r.json()


def test_api_draws_the_three_cards_on_the_demo(demo):
    j = _get(demo, "base", "base-check")
    assert j["kind"] == "readiness" and j["phase"] == "base", j
    R = j["readiness"]
    assert [c["id"] for c in R["checks"]] == ["intensity", "ramp", "recovery"]
    assert len(chk(R, "intensity")["bars"]) == 8
    assert "dash=" in (chk(R, "intensity")["chart"].get("href") or ""), chk(R, "intensity")["chart"]
    j = _get(demo, "transition", "recovery-check")
    assert j["phase"] == "transition" and j["readiness"]["kind"] == "transition"
    j = _get(demo, "base", "z5-gate")
    assert j["kind"] == "z5gate" and j["unlock"]["kind"] == "unlock", j
    assert [t["zone"] for t in j["unlock"]["tiles"]] == [3, 5]
