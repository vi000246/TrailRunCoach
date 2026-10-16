"""SP-677: the 賽前完備程度 card split in two. The 專項期 card (race_readiness.board) only has ① 專項量 and
② 表現驗證 with a calendar to 賽前第 3 週; the 減量期 card (race_readiness.taper) is a countdown and four
checks (no calendar), each a number, a target band, a state, what to change when it is off and a chart.
Each card computes only its own part (compute / compute_taper). Synthetic rows only."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from backend.engine import race_readiness as RD
from backend.tests.test_race_readiness_sp650 import START, act, board, ctl_series, monday, race

ROOT = Path(__file__).resolve().parents[1]
TAPER_FROM = START - dt.timedelta(days=10)          # 12/2 (Wed of 賽前第 2 週)
IN_TAPER = START - dt.timedelta(days=6)             # 12/6: 賽前 6 天, 減量第 5 天
SOURCE_WORDS = ("Koop", "Uphill", "Friel", "Pfitzinger", "Bosquet", "Seiler", "Wang", "Mujika", "Daniels")


def usual_weeks(h=8.0, climb=1500.0):
    """Weeks 10 → 3 at `h` hours and `climb` m a week (two sessions each)."""
    return [act(monday(w) + dt.timedelta(days=d), h / 2, 20.0, climb / 2) for w in range(10, 2, -1) for d in (1, 5)]


def taper(acts, today=IN_TAPER, **kw):
    kw.setdefault("ctl", ctl_series(200, end=today))
    kw.setdefault("race_tsb", {"share": 0.24, "source": "plan"})
    return RD.taper(race(), today, acts, **kw)


def chk(t, cid):
    return next(c for c in t["checks"] if c["id"] == cid)


# ---- 驗收 1: the 專項期 card -----------------------------------------------------------------------------

def test_build_board_has_only_volume_and_validation_to_week_3():
    b = board([act(monday(10), 1.0, 8, 300)])
    assert [s["id"] for s in b["stages"]] == ["volume", "validation"]
    assert [w["week"] for w in b["weeks"]] == list(range(10, 2, -1))
    assert all(w["phase"] == "specific" for w in b["weeks"])
    for s in b["stages"]:
        for it in s["items"]:
            assert set(it["cells"]) <= set(range(10, 2, -1)), it["id"]
    ids = {i["id"] for s in b["stages"] for i in s["items"]}
    assert not ids & {"taper_volume", "climb_early", "ctl_down_days", "race_tsb"}


def test_build_board_takes_no_race_day_projection():
    import inspect
    assert "race_tsb" not in inspect.signature(RD.board).parameters
    assert "taper_days" not in inspect.signature(RD.board).parameters


# ---- 驗收 2: the 減量期 card ---------------------------------------------------------------------------

def test_taper_is_a_countdown_and_four_checks_without_a_calendar():
    t = taper(usual_weeks())
    assert t["kind"] == "taper" and "weeks" not in t and "stages" not in t
    assert t["days_to"] == 6 and t["taper_days"] == 10 and t["taper_day"] == 5
    assert [d["label"] for d in t["countdown"]][-1] == "賽" and len(t["countdown"]) == 11
    assert [d["state"] for d in t["countdown"]][:5] == ["past"] * 4 + ["today"]
    assert [c["id"] for c in t["checks"]] == ["taper_volume", "climb_early", "ctl_down_days", "race_tsb"]
    for c in t["checks"]:
        assert c["show"] and c["target"] and c["state"] in ("ok", "warn", "wait", "lock"), c
        assert c["label"]
        assert c["chart"]["id"], c
        if c["band"] is not None:
            lo, hi = c["band"]
            assert c["scale"][0] <= lo < hi <= c["scale"][1], c


def test_taper_volume_is_the_taper_days_share_of_the_usual():
    # usual 8 h/week; the taper's 5 days so far at 2.8 h → (2.8 / 5) / (8 / 7) = 49 %
    acts = usual_weeks() + [act(TAPER_FROM + dt.timedelta(days=1), 1.4, 10, 100),
                            act(TAPER_FROM + dt.timedelta(days=3), 1.4, 10, 100)]
    c = chk(taper(acts), "taper_volume")
    assert c["value"] == 0.49 and c["state"] == "ok" and c["todo"] is None, c
    acts = usual_weeks() + [act(TAPER_FROM + dt.timedelta(days=d), 1.5, 10, 100) for d in range(4)]
    c = chk(taper(acts), "taper_volume")
    assert c["state"] == "warn" and c["todo"], c        # 6 h in 5 days = 105 % of the usual


def test_climb_early_off_says_what_to_change():
    acts = usual_weeks() + [act(monday(2) + dt.timedelta(days=d), 1.0, 8, 600) for d in range(3)]
    c = chk(taper(acts), "climb_early")
    assert c["state"] == "warn" and c["todo"], c
    c = chk(taper(usual_weeks()), "climb_early")
    assert c["state"] == "ok" and c["todo"] is None


def test_ctl_down_days_and_race_tsb_states():
    falling = ctl_series(200, value=lambda i: 60.0 - max(0, i - 186) * 0.5, end=IN_TAPER)   # down 13 days
    t = taper(usual_weeks(), ctl=falling)
    c = chk(t, "ctl_down_days")
    assert c["value"] == 13 and c["state"] == "warn" and c["todo"], c
    assert chk(taper(usual_weeks()), "ctl_down_days")["state"] == "ok"
    rt = chk(taper(usual_weeks()), "race_tsb")
    assert rt["state"] == "wait" and rt["value"] == 0.24, rt      # from the plan: look at the actual 3 days out
    rt = chk(taper(usual_weeks(), race_tsb={"share": 0.02, "source": "actual"}), "race_tsb")
    assert rt["state"] == "warn" and rt["todo"]
    rt = chk(taper(usual_weeks(), race_tsb={"share": 0.2, "source": "actual"}), "race_tsb")
    assert rt["state"] == "ok"


def test_taper_before_it_starts_is_locked_with_its_targets():
    t = taper(usual_weeks(), today=TAPER_FROM - dt.timedelta(days=3))
    assert t["pre"] is True and "12/2" in t["text"]
    assert all(c["state"] == "lock" for c in t["checks"])


def test_taper_says_the_build_result_in_one_line():
    t = taper(usual_weeks(), build={"stages": [{"name": "專項量", "done": 4, "total": 5, "passed": True},
                                               {"name": "表現驗證", "done": 3, "total": 4, "passed": True}]})
    assert t["build_line"] == "專項量 4/5 ✓、表現驗證 3/4 ✓", t["build_line"]


def test_taper_without_a_race():
    t = RD.taper(None, IN_TAPER, [])
    assert t["race"] is None and t["text"]


def test_taper_text_has_no_sources():
    s = json.dumps(taper(usual_weeks()), ensure_ascii=False)
    assert not any(w in s for w in SOURCE_WORDS)


# ---- 驗收 3: separate computing; the season-plan sentence links the right card --------------------------

def test_line_in_the_taper_is_the_taper_cards():
    t = taper(usual_weeks())
    ln = RD.line(t)
    assert ln["phase"] == "taper" and ln["text"].startswith("減量期：賽前 6 天"), ln
    b = board([act(monday(10), 1.0, 8, 300)])
    assert RD.line(b)["phase"] == "build"


def test_phase_of_the_next_a_race(monkeypatch):
    from backend.engine import specific_phase as SP
    monkeypatch.setattr(SP, "race_day", lambda *a, **k: race())
    plan = type("P", (), {"events": []})()
    assert RD.phase(plan, IN_TAPER) == "taper"
    assert RD.phase(plan, TAPER_FROM - dt.timedelta(days=1)) == "build"


def test_view_charts_name_their_phase():
    v = json.loads((ROOT.parent / "views" / "periodization.json").read_text("utf-8"))
    ch = {c["id"]: c for d in v["dashboards"] for c in d.get("charts") or []}
    assert ch["race-readiness"].get("phase", "build") == "build"
    assert ch["race-readiness-taper"]["phase"] == "taper"
    en = json.loads((ROOT.parent / "views" / "i18n" / "en.json").read_text("utf-8"))["periodization"]["charts"]
    for cid in ("race-readiness", "race-readiness-taper"):
        d = ch[cid]["description"] + en[cid]["description"]
        assert "比賽狀態" not in ch["race-readiness"]["description"]
        assert not any(w in d for w in SOURCE_WORDS)


def test_card_script_draws_the_taper_and_its_strings_exist():
    js = (ROOT / "static" / "readiness.js").read_text("utf-8")
    assert "drawTaper" in js and "rd-chk" in js and "rd-wkb" not in js     # phone: this week only
    keys = set(re.findall(r'T\("([a-z_.]+)"[,)]', js))
    for loc in ("zh-TW", "en"):
        cat = json.loads((ROOT / "static" / "i18n" / loc / "viewer.json").read_text("utf-8"))
        miss = {k for k in keys if "readiness." + k not in cat}
        assert not miss, (loc, miss)
