"""SP-975: the 介紹頁's 賽事周期 — the fictional runner's season as the app's own phase planner cuts it.

- the season is frozen by scripts/intro_cycle.py from engine/planning.auto_phases (not drawn by hand): the file
  is exactly what the planner gives today, in whole days from today (no calendar date), 「今天」 in the 50K's
  專項期;
- GET /api/v1/intro/cycle serves it, public, the known keys only; 404 without the file;
- the section is one feature like the others (text + a card), right after 「課表與賽事周期」, its rule numbers
  are the planner's, its sources are on the 關於 page (the 賽事周期 methods), every word in both languages.
The browser side (the drawing, phone width): e2e/test_intro_sp975_e2e.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, sign_up, started,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "backend" / "static"
LOCALES = ("zh-TW", "en")


def _page() -> str:
    return (STATIC / "intro.html").read_text("utf-8")


def _cat(loc: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8"))


# ---------------------------------------------------------------------------- the frozen season

def test_the_frozen_season_is_the_planners_output():
    from backend.api import intro as I
    from backend.scripts import intro_cycle as IC
    assert I.CYCLE_FILE == IC.OUT
    assert IC.OUT.read_text("utf-8") == IC.dumps(IC.build()), "run python -m backend.scripts.intro_cycle"
    assert IC.main(["--check"]) == 0


def test_the_season_is_cut_by_the_planners_rules():
    from backend.engine import planning as P
    from backend.scripts import intro_cycle as IC
    d = json.loads(IC.OUT.read_text("utf-8"))
    ph = d["phases"]
    # contiguous, covering the window
    assert ph[0]["start"] == d["begin"] and ph[-1]["end"] == d["end"]
    assert all(a["end"] + 1 == b["start"] for a, b in zip(ph, ph[1:]))
    race = next(e for e in d["events"] if e["id"] == "trail50k")
    by = {(p["kind"], p["event"]): p for p in ph}
    spec, taper = by[("specific", "trail50k")], by[("taper", "trail50k")]
    assert taper["end"] == race["start"] - 1 and taper["end"] - taper["start"] + 1 == P.TAPER_DAYS
    assert spec["end"] - spec["start"] + 1 == 7 * P.SPECIFIC_WEEKS
    rec = by[("recovery", "trail50k")]
    assert rec["start"] == race["end"] + 1 and rec["end"] - rec["start"] + 1 == P.REC_BY_SIZE[P.ULTRA][0]
    assert rec["size"] == "ultra"
    tr = by[("transition", "trail50k")]
    assert tr["end"] - tr["start"] + 1 == 7 * P.TRANSITION_WEEKS
    # 今天 in the 50K's 專項期, about four weeks out (the 課表 calendar's 「比賽前的最後 4 週」)
    assert spec["start"] <= 0 <= spec["end"] and 21 <= race["start"] <= 35
    # the B races: a mini-taper of MINI_TAPER_DAYS before, never a phase of their own
    b = [e for e in d["events"] if e["priority"] == "B"]
    assert len(b) == 2 and not any(p["event"] in {e["id"] for e in b} for p in ph)
    for e in b:
        mt = next(w for w in d["b_windows"] if w["event"] == e["id"] and w["kind"] == "mini_taper")
        assert mt["end"] == e["start"] - 1 and mt["end"] - mt["start"] + 1 == P.MINI_TAPER_DAYS
    # the earlier A 百岳 trip: its phase is a 行程, a 7-day taper (BAIYUE_TAPER_DAYS)
    assert by[("event", "baiyue")].get("trip") is True
    t = by[("taper", "baiyue")]
    assert t["end"] - t["start"] + 1 == P.BAIYUE_TAPER_DAYS
    text = IC.OUT.read_text("utf-8")
    assert re.search(r"\d{4}-\d{2}-\d{2}", text) is None                  # no calendar date


# ---------------------------------------------------------------------------- the API

def test_cycle_api_is_public_and_only_known_keys(c):
    from backend.api import intro as I
    assert "/api/v1/intro/cycle" in I.PUBLIC_PATHS
    r = c.get("/api/v1/intro/cycle")
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"begin", "end", "phases", "events", "b_windows"}
    raw = json.loads(I.CYCLE_FILE.read_text("utf-8"))
    assert len(d["phases"]) == len(raw["phases"]) and len(d["events"]) == len(raw["events"])
    assert all(set(p) <= {"kind", "start", "end", "event", "trip", "size"} for p in d["phases"])
    assert all(set(e) == {"id", "priority", "start", "end"} for e in d["events"])
    assert re.search(r"[一-鿿]", r.text) is None and "http" not in r.text
    assert "public" in r.headers.get("cache-control", "")


def test_cycle_api_404_without_its_file_or_with_a_broken_one(c, monkeypatch, tmp_path):
    from backend.api import intro as I
    monkeypatch.setattr(I, "CYCLE_FILE", tmp_path / "none.json")
    assert c.get("/api/v1/intro/cycle").status_code == 404
    bad = json.loads((ROOT / "backend" / "demo" / "intro_cycle.json").read_text("utf-8"))
    bad["phases"] = [p for p in bad["phases"] if not p["start"] <= 0 <= p["end"]]     # nothing for 今天
    (tmp_path / "bad.json").write_text(json.dumps(bad), "utf-8")
    monkeypatch.setattr(I, "CYCLE_FILE", tmp_path / "bad.json")
    assert I.cycle() is None


# ---------------------------------------------------------------------------- the page

def test_the_section_is_a_feature_after_the_schedule():
    html = _page()
    plan, cyc, race = html.index('id="plan"'), html.index('id="cycle"'), html.index('id="race"')
    assert plan < cyc < race
    sec = html[cyc:html.index("</section>", cyc)]
    assert 'class="feature"' in sec and '<div class="text">' in sec and 'class="cy reveal"' in sec
    assert sec.count("<li ") == 4                                           # 3–4 short points, like the others
    assert 'href="/methods#cycle"' in sec                                   # the sources: the 方法 page's card (SP-973)
    # no 「現在」 cards, no long rule text on the page
    for gone in ('class="cards"', "plan.tl.auto", "目前區間用的門檻"):
        assert gone not in sec, gone
    script = "".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert 'API + "cycle"' in script and "drawCycle" in script


def test_the_points_say_the_planners_numbers():
    from backend.engine import planning as P
    for loc in LOCALES:
        cat = _cat(loc)
        nums = [int(x) for x in re.findall(r"\d+", cat["cyc.li.back"])]
        assert nums == [P.TAPER_DAYS, P.SPECIFIC_WEEKS], (loc, nums)
        assert [int(x) for x in re.findall(r"\d+", cat["cyc.li.b"])] == [P.MINI_TAPER_DAYS], loc
        assert "1–2" in cat["cyc.li.recover"] and P.REC_BY_SIZE[P.SHORT][0] == 7 and P.REC_BY_SIZE[P.ULTRA][0] == 14
    # the old page said a 強化期 the app does not have (planning.PHASES)
    assert "強化" not in _cat("zh-TW")["plan.lead"] and "強化期" not in P.PHASES.values()


def test_the_named_sources_are_on_the_about_page():
    from backend.api import about as A
    cites = " ".join(A.SOURCES[s].cite for m, srcs in A.METHODS if m in ("phases", "postrace") for s in srcs)
    # the names the section says (江晏慶: cited as Chiang YC — the English page has no Chinese)
    as_cited = {"江晏慶": "Chiang YC", "Yen-Ching Chiang": "Chiang YC"}
    names = r"Uphill Athlete|Pfitzinger|Canova|Daniels|江晏慶|Yen-Ching Chiang|Bosquet|Wang|Johnston|Torrence|Millet"
    said = set()
    for loc in LOCALES:
        cat = _cat(loc)
        for k in ("cyc.li.basis", "cyc.tip.specific", "cyc.tip.taper", "cyc.tip.recovery"):
            said |= set(re.findall(names, cat[k]))
    assert {"Uphill Athlete", "Pfitzinger", "Daniels", "Bosquet", "Wang", "Johnston"} <= said
    for name in said:
        assert as_cited.get(name, name) in cites, name


def test_every_cycle_word_is_in_both_languages():
    zh, en = _cat("zh-TW"), _cat("en")
    ck = {k for k in zh if k.startswith("cyc.")}
    assert ck == {k for k in en if k.startswith("cyc.")}
    html = _page()
    used = set(re.findall(r'"intro\.(cyc\.[\w.]+)"', html)) | set(re.findall(r'data-i18n="intro\.(cyc\.[\w.]+)"', html))
    used = {k for k in used if not k.endswith(".")}       # L.t("intro.cyc.ph." + kind): the families below
    assert used and used <= ck, used - ck
    for fam in ("ph", "tip", "ev", "evs", "size"):
        assert f'"intro.cyc.{fam}."' in html, fam
    from backend.api import intro as I
    for k in I.CYCLE_PHASES:
        assert f"cyc.ph.{k}" in ck, k
    for k in I.CYCLE_EVENTS:
        assert f"cyc.ev.{k}" in ck and f"cyc.evs.{k}" in ck, k
    for k in I.CYCLE_SIZES:
        assert f"cyc.size.{k}" in ck, k
    assert not re.search(r"[一-鿿]", json.dumps({k: en[k] for k in ck}, ensure_ascii=False))


def test_the_page_opens_with_the_section(c):
    r = c.get("/", headers=HTML)
    assert r.status_code == 200 and 'id="cycle"' in r.text
