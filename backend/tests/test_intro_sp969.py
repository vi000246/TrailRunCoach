"""SP-969: the 介紹頁 made to show what sets the calculator apart.

- the trail calculator's moving time taken apart factor by factor (planner.trail_breakdown): every layer
  is the calculator run again with one factor more, so the layers add up to the plan's own time exactly;
  the example's breakdown is frozen beside it (backend/demo/intro_breakdown.json, scripts/intro_example)
  and served as numbers only (GET /api/v1/intro/breakdown, public);
- 「為什麼不一樣」: its numbers are the repo's own (about.SOURCES / METHODS, the calibration registry),
  handed to the page by the server; the back-test line's numbers come from the spec;
- the try buttons open the example in a dialog (same-origin iframe, ×／background／Esc close it, focus back
  to the button, 「在新分頁開啟」), and stay plain links to the demo when TRC_DEMO_URL is set.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest
from pytest import approx

from backend.engine.racepower import course as CO
from backend.engine.racepower import grade_model as GM
from backend.engine.racepower import planner as PL
from backend.engine.racepower import trailhr as TH
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, sign_up, started,
)
from backend.tests.test_racepower_v2 import RE0, fake_v1, synthetic_track

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "backend" / "static"
LOCALES = ("zh-TW", "en")


# ---------------------------------------------------------------------------- the breakdown (engine)

def _model(**kw):
    m = {"kind": "ols", "a": 2.0, "b": 4.0, "c": 6.0, "delta": 0.05, "x_race": 1.0, "x_race_source": "t", "n": 9,
         "xstar": TH.xstar_prior()}
    m.update(kw)
    return m


def _plan(model, alt=100.0, opts=None, **v1kw):
    tr = synthetic_track({"len": 30000, "z": lambda x: 200 + 150 * math.sin(x / 2500.0)})
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 30.0, c["totals"]["gain_m"], alt=alt, **v1kw)
    return PL.plan_run(v1=v1, course=c, grade_re=GM.GradeRE(RE0), effort_validated=False,
                       opts={"mode": "auto", **(opts or {})}, validated={"trail": True}, trail_hr=model,
                       capacity={"lthr": 170.0})


def test_the_layers_add_up_to_the_plans_time():
    p = _plan(_model())
    s = p["summary"]
    bd = s["breakdown"]
    assert s["total_method"] == "trail_hr" and bd
    keys = [x["key"] for x in bd["layers"]]
    assert keys[:4] == ["flat", "climb", "terrain", "durability"] and set(keys[4:6]) == {"heat", "altitude"}
    assert sum(x["t_s"] for x in bd["layers"]) == approx(s["time_s"], abs=1e-6)
    assert bd["layers"][-1]["cum_s"] == approx(s["time_s"], abs=1e-6)


def test_each_layer_is_the_calculator_run_again_without_that_factor():
    m = _model()
    p = _plan(m)
    s, hr = p["summary"], p["summary"]["trail_hr"]
    bd = {x["key"]: x for x in s["breakdown"]["layers"]}
    km, gain = s["km"], s["gain_m"]
    div = TH.effort_divisor()
    # the flat: the same HR model on the distance alone (no climb, no durability, no heat, no altitude)
    flat = TH.predict_race(m, km, m["xstar"], delta=0.0)[0]
    assert bd["flat"]["t_s"] == approx(flat)
    climb = TH.predict_race(m, km + gain / div, m["xstar"], delta=0.0)[0]
    assert bd["climb"]["t_s"] == approx(climb - flat)
    assert bd["terrain"]["t_s"] == approx(0.0, abs=1e-6)              # a 一般 course: × 1
    dur = TH.predict_race(m, km + gain / div, m["xstar"])[0]
    assert bd["durability"]["t_s"] == approx(dur - climb) and bd["durability"]["t_s"] > 0
    assert bd["durability"]["cum_s"] + bd["heat"]["t_s"] == approx(hr["time_s"])    # the HR model's own time
    assert bd["climb"]["t_s"] > 0


def test_no_durability_is_a_zero_layer_and_a_technical_course_a_longer_one():
    p0 = _plan(_model(delta=0.0))
    bd0 = {x["key"]: x for x in p0["summary"]["breakdown"]["layers"]}
    assert bd0["durability"]["t_s"] == approx(0.0, abs=1e-6)
    p1 = _plan(_model(), opts={"terrain_difficulty": "technical"})
    bd1 = {x["key"]: x for x in p1["summary"]["breakdown"]["layers"]}
    assert bd1["terrain"]["t_s"] > 0
    assert sum(x["t_s"] for x in p1["summary"]["breakdown"]["layers"]) == approx(p1["summary"]["time_s"], abs=1e-6)


def test_altitude_is_its_own_layer():
    p = _plan(_model(), alt=100.0)
    lo = {x["key"]: x for x in p["summary"]["breakdown"]["layers"]}
    # the same model with the race 2 km higher than the training: altitude costs far more time
    tr = synthetic_track({"len": 30000, "z": lambda x: 2400 + 150 * math.sin(x / 2500.0)})
    c = CO.build_course(tr)
    v1 = fake_v1("trail", 30.0, c["totals"]["gain_m"], alt=100.0)
    v1["env"]["to"]["altitude_m"] = 2400.0
    hi = PL.plan_run(v1=v1, course=c, grade_re=GM.GradeRE(RE0), effort_validated=False, opts={"mode": "auto"},
                     validated={"trail": True}, trail_hr=_model(), capacity={"lthr": 170.0})
    bd = {x["key"]: x for x in hi["summary"]["breakdown"]["layers"]}
    assert bd["altitude"]["t_s"] > 3 * max(lo["altitude"]["t_s"], 1.0)
    # the layers before it do not see the altitude (the HR model runs in training conditions)
    assert bd["climb"]["cum_s"] == approx(lo["climb"]["cum_s"], rel=0.02)
    assert sum(x["t_s"] for x in hi["summary"]["breakdown"]["layers"]) == approx(hi["summary"]["time_s"], abs=1e-6)


def test_an_assumed_fatigue_replaces_the_athletes_own():
    # Q2-1: PlanIn.assume_delta — the example's what-if δ; the durability layer follows it
    p0 = _plan(_model(delta=0.0))
    p1 = _plan(_model(delta=0.0), opts={"assume_delta": 0.05})
    b0 = {x["key"]: x["t_s"] for x in p0["summary"]["breakdown"]["layers"]}
    b1 = {x["key"]: x["t_s"] for x in p1["summary"]["breakdown"]["layers"]}
    assert b0["durability"] == approx(0.0, abs=1e-6) and b1["durability"] > 60
    assert p1["summary"]["breakdown"]["delta"] == 0.05 and p1["summary"]["time_s"] > p0["summary"]["time_s"]
    assert sum(b1.values()) == approx(p1["summary"]["time_s"], abs=1e-6)
    from backend.engine.racepower import calc as CALC
    with pytest.raises(ValueError):
        CALC.PlanIn(type="trail", assume_delta=0.5)


def test_no_breakdown_without_the_hr_model():
    p = _plan(None)
    assert p["summary"]["breakdown"] is None
    assert PL.trail_breakdown(None, None, 10, 100, 1.0, 1.0, 1.0, 1.0, 1.0) is None


def test_a_later_step_that_moves_the_time_becomes_its_own_layer():
    plan = {"summary": {"time_s": 1100.0, "breakdown": {"layers": [{"key": "flat", "t_s": 1000.0, "cum_s": 1000.0}]}}}
    PL.breakdown_after(plan)
    bd = plan["summary"]["breakdown"]
    assert bd["layers"][-1] == {"key": "night", "t_s": 100.0, "cum_s": 1100.0} and bd["time_s"] == 1100.0
    same = {"summary": {"time_s": 1000.2, "breakdown": {"layers": [{"key": "flat", "t_s": 1000.0, "cum_s": 1000.0}]}}}
    assert len(PL.breakdown_after(same)["summary"]["breakdown"]["layers"]) == 1


# ---------------------------------------------------------------------------- the frozen breakdown and its API

def test_the_frozen_breakdown_adds_up_to_the_frozen_example():
    from backend.api import intro as I
    from backend.engine.racepower import share as SH
    raw = json.loads(I.BREAKDOWN_FILE.read_text("utf-8"))
    snap = SH.example(SH.EXAMPLE_ID)
    assert sum(x["t_s"] for x in raw["layers"]) == approx(snap["summary"]["time_s"], abs=1.0)
    assert raw["km"] == approx(snap["summary"]["km"]) and raw["gain_m"] == approx(snap["summary"]["gain_m"])
    keys = [x["key"] for x in raw["layers"]]
    # the calculator's order (planner.trail_breakdown): flat first, then one factor at a time
    assert keys[:4] == ["flat", "climb", "terrain", "durability"] and {"heat", "altitude"} <= set(keys)
    assert raw["legs"] == len(snap["segments"])
    # Q2-1 (the user, 2026-10-28): the example's assumed settings make the 地形難度 and fatigue layers real
    from backend.scripts import intro_example as IE
    A = IE.ASSUMED["trail"]
    by = {x["key"]: x["t_s"] for x in raw["layers"]}
    assert by["terrain"] > 60 and by["durability"] > 60
    assert raw["assumed"] == {"effort": A["effort_target"], "terrain": A["terrain_difficulty"], "delta": A["assume_delta"],
                              "stops": len(A["stops"]), "stop_min": sum(x["minutes"] for x in A["stops"])}
    # Q2-2: the example's effort and stops are a race's, not all-out with hours of stops
    assert snap["effort"]["f"] == A["effort_target"] and snap["effort"]["key"] != "max"
    assert snap["inputs"]["stops"] and [x["minutes"] for x in snap["inputs"]["stops"]] == [x["minutes"] for x in A["stops"]]
    stop_s = snap["summary"]["time_total_s"] - snap["summary"]["time_s"]
    assert 0 < stop_s < 1.5 * 3600, stop_s
    assert re.search(r"\d{4}-\d{2}-\d{2}", I.BREAKDOWN_FILE.read_text("utf-8")) is None      # no calendar date


def test_breakdown_api_is_public_and_numbers_only(c):
    r = c.get("/api/v1/intro/breakdown")
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"time_s", "layers", "assumed", "km", "gain_m", "divisor", "terrain", "delta", "heat_beta",
                      "temp_c", "rh_pct", "altitude_m", "legs"}
    assert set(d["assumed"]) == {"effort", "terrain", "delta", "stops", "stop_min"}
    assert sum(x["t_s"] for x in d["layers"]) == approx(d["time_s"], abs=1.0)
    assert all(set(x) == {"key", "t_s"} for x in d["layers"])
    assert re.search(r"[一-鿿]", r.text) is None and "src" not in r.text and "http" not in r.text
    assert "public" in r.headers.get("cache-control", "")
    from backend.api import intro as I
    ex = c.get("/api/v1/intro/example/trail").json()
    assert d["time_s"] == approx(ex["summary"]["time_s"], abs=1) and d["legs"] == ex["summary"]["legs"]
    assert "/api/v1/intro/breakdown" in I.PUBLIC_PATHS


def test_breakdown_api_404_without_its_file_or_when_it_no_longer_adds_up(c, monkeypatch, tmp_path):
    from backend.api import intro as I
    monkeypatch.setattr(I, "BREAKDOWN_FILE", tmp_path / "none.json")
    assert c.get("/api/v1/intro/breakdown").status_code == 404
    bad = json.loads((ROOT / "backend" / "demo" / "intro_breakdown.json").read_text("utf-8"))
    bad["layers"][0]["t_s"] += 600                       # an example frozen again without its breakdown
    (tmp_path / "bad.json").write_text(json.dumps(bad), "utf-8")
    monkeypatch.setattr(I, "BREAKDOWN_FILE", tmp_path / "bad.json")
    assert I.breakdown() is None


def test_the_freeze_refuses_a_breakdown_that_does_not_add_up():
    from backend.scripts import intro_example as IE
    snap = {"summary": {"time_s": 1000.0}, "inputs": {"temp_c": 20.0}, "segments": [{}, {}]}
    ok = {"summary": {"breakdown": {"layers": [{"key": "flat", "t_s": 600.0}, {"key": "climb", "t_s": 400.0}],
                                    "km": 10, "terrain": {"level": "normal", "mult": 1.0}}}}
    out = IE.breakdown(ok, snap)
    assert [x["key"] for x in out["layers"]] == ["flat", "climb"] and out["legs"] == 2 and out["temp_c"] == 20.0
    with pytest.raises(SystemExit):
        IE.breakdown({"summary": {"breakdown": {"layers": [{"key": "flat", "t_s": 900.0}]}}}, snap)
    with pytest.raises(SystemExit):
        IE.breakdown({"summary": {}}, snap)


# ---------------------------------------------------------------------------- 為什麼不一樣

def test_why_counts_the_repo_not_a_typed_number():
    from backend.api import about as AB
    from backend.api import intro as I
    from backend.engine import calibrate as CAL
    I.reset()
    w = I.why()
    assert w["sources"] == len(AB.SOURCES) and w["methods"] == len(AB.METHODS)
    assert w["calibrated"] == sum(1 for it in CAL._registry().values() if not it.manual_only) > 0
    # documents: constants with their source (docs/research/coach-schools-zones-periodization.md:9, :361-753)
    doc = (ROOT / "docs" / "research" / "coach-schools-zones-periodization.md").read_text("utf-8")
    assert f"{I.ZONE_SYSTEMS} 套區間系統" in doc and f"[{I.ZONE_REFS}] " in doc and f"[{I.ZONE_REFS + 1}] " not in doc
    spec = (ROOT / "docs" / "spec" / "racepower.spec.md").read_text("utf-8")
    bt = I.BACKTEST
    assert (f"| C prior order (A + B) | {bt['walk_n']} | full {bt['walk_full_pct']} % ≤ prior {bt['walk_prior_pct']} % "
            f"≤ Tobler {bt['walk_formula_pct']} %") in spec
    assert f"| trail HR model, {bt['trail_races']} races, race level | – | – | {bt['trail_pct']} % " in spec
    assert f"{bt['durability_with_pct']} % with it vs {bt['durability_without_pct']} % without" in spec


def test_the_page_carries_the_counts(c):
    from backend.api import intro as I
    html = c.get("/", headers=HTML).text
    m = re.search(r"<script>window\.TRC_WHY = (\{.*?\});</script>", html)
    assert m and json.loads(m.group(1)) == I.why()


def test_without_the_core_the_calibrated_card_has_no_number(monkeypatch):
    from backend.api import intro as I
    from backend.engine import calibrate as CAL

    def stub():
        raise ImportError("the public mirror's stub")
    I.reset()
    monkeypatch.setattr(CAL, "_registry", stub)
    assert I.why()["calibrated"] is None
    I.reset()


# ---------------------------------------------------------------------------- the page

def _page() -> str:
    return (STATIC / "intro.html").read_text("utf-8")


def _script() -> str:
    return "".join(re.findall(r"<script>(.*?)</script>", _page(), re.S))


def test_the_why_section_follows_the_hero():
    html = _page()
    hero_end = html.index("</section>", html.index('class="hero"'))
    why = html.index('id="why"')
    assert hero_end < why < html.index('aria-label="三個重點"') < html.index('id="plan"')
    sec = html[why:html.index("</section>", why)]
    assert sec.count('class="card reveal"') == 4
    for k in ("sources", "zones", "calibrated"):
        assert f'data-why="{k}"' in sec, k


def test_the_try_buttons_are_big_carry_the_result_and_open_a_dialog():
    html = _page()
    hero = html[html.index('class="hero"'):html.index("</section>")]
    race = html[html.index('id="race"'):html.index('id="read"')]
    for part in (hero, race):
        a = re.search(r'<a class="trybtn" data-try data-try-big href="/share/example-trail-50k">.*?</a>', part, re.S)
        assert a and "data-try-sub" in a.group(0) and "data-try-h" in a.group(0)
    # the race section ends with it (「計算機段落底下再一顆」)
    assert race.rindex("data-try-big") > race.index('id="wf"')
    script = _script()
    # the result is the example's own (the example API's summary), not typed into the page
    assert "setTry(k, d)" in script and "s.km" in script and "s.time_s" in script and "s.legs" in script
    # the dialog: × , the background and Esc close it, the focus goes back to the button
    dlg = html[html.index('id="ex-modal"'):html.index("<!--fill:try-->")]
    assert 'role="dialog"' in dlg and 'aria-modal="true"' in dlg and '<iframe id="ex-frame"' in dlg
    assert 'id="ex-close"' in dlg and 'id="ex-new"' in dlg and 'target="_blank"' in dlg
    assert 'e.key === "Escape"' in script and "e.target === modal" in script and "opener.focus()" in script
    assert "inert = true" in script and "inert = false" in script
    # with a modifier key (a new tab) or the public demo, the link is a link
    assert "demoTry" in script and "e.metaKey" in script and "e.ctrlKey" in script


def test_the_waterfall_only_lays_out_the_servers_numbers():
    html = _page()
    race = html[html.index('id="race"'):html.index('id="read"')]
    assert race.index('id="wf-assumed"') < race.index('id="wf"')
    # the back-test line and the Palladino origin line are off the page (user, 2026-10-28)
    assert 'id="bt"' not in race and "intro.race.origin" not in race
    # Q2-3: the old list, the small calculator and the profile are gone from the race section
    for gone in ('id="calc"', 'id="profile"', 'intro.race.li.', 'type="range"'):
        assert gone not in race, gone
    script = _script()
    assert 'API + "breakdown"' in script and "drawWaterfall" in script
    for w in ("Math.pow", "Math.exp", "Math.log", "Math.sqrt", "**", "riegel", "Riegel", "tobler", "153", "x_shift",
              "xstar", "heat_shift"):
        assert w not in script, w


def test_every_new_word_is_in_both_languages():
    cats = {loc: json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8")) for loc in LOCALES}
    script, html = _script(), _page()
    # whole keys only (L.t("intro.cal.phase." + p) is the start of one, checked by SP-637's own test)
    keys = {k for k in re.findall(r'L\.t\("intro\.([\w.]+)"', script) if not k.endswith((".", "_"))}
    names = ("flat", "climb", "terrain", "durability", "heat", "altitude", "memory", "night")
    keys |= {f"wf.{k}" for k in names} | {f"wf.{k}_s" for k in names}
    keys |= {f"try.{k}" for k in ("trail", "baiyue", "road")}
    keys |= set(re.findall(r'data-i18n="intro\.([\w.]+)"', html))
    keys |= {k.split(":")[1].replace("intro.", "") for a in re.findall(r'data-i18n-attr="([^"]+)"', html)
             for k in a.split(";")}
    for loc, cat in cats.items():
        missing = sorted(k for k in keys if k not in cat)
        assert not missing, (loc, missing)


# ---------------------------------------------------------------------------- the example in a frame

def test_the_example_may_be_framed_by_the_same_site(c):
    r = c.get("/share/example-trail-50k", headers=HTML)
    assert r.status_code == 200
    xfo = (r.headers.get("x-frame-options") or "").lower()
    csp = (r.headers.get("content-security-policy") or "").lower()
    assert xfo in ("", "sameorigin") and "frame-ancestors 'none'" not in csp


def test_the_cairn_block_is_last_before_the_footer_and_links_like_the_cross_promotion(c, monkeypatch):
    # 2026-10-27 (使用者聊天追加): the sister project, one quiet block — slogan, what it does, its link
    html = _page()
    sec = html[html.index('id="cairn"'):html.index("</section>", html.index('id="cairn"'))]
    assert html.index('id="faq"') < html.index('class="end"') < html.index('id="cairn"') < html.index("<footer>")
    assert 'id="cairn-link"' in sec and "hidden" in sec.split('id="cairn-link"')[1].split(">")[0]
    cats = {loc: json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8")) for loc in LOCALES}
    # the slogan names the product from its one key (SP-971): {name} = 「基石」 in zh (the Chinese part of
    # 「Trig 基石」), 「Trig」 in en — the e2e test reads the rendered words
    assert cats["zh-TW"]["cairn.h"] == "以{name}備戰，隨疊石回家"
    assert cats["en"]["cairn.h"] == "{name} gets you ready. Cairn gets everyone home."
    assert '$("cairn-h").textContent = L.t("intro.cairn.h", { name })' in _script()
    assert "以基石備戰，隨疊石回家" in html                     # the words before the script (no JS)
    for cat in cats.values():
        assert cat["cairn.what"] and cat["cairn.link"] and cat["cairn.kicker"]
    from backend.api import xp as XP
    page = c.get("/", headers=HTML).text
    m = re.search(r"<script>window\.TRC_CAIRN = (\{.*?\});</script>", page)
    assert m and json.loads(m.group(1)) == {"url": XP.cairn_url()} and XP.cairn_url().startswith("https://")
    assert 'a.href = CA.url; a.hidden = false;' in _script()
    from backend.api import intro as I
    monkeypatch.setattr(XP, "cairn_url", lambda: "http://not-https.example")
    assert I.cairn_url() is None                       # no https address: the block shows no link


def test_in_the_dialog_the_share_pages_sign_up_opens_as_the_whole_page():
    html = (STATIC / "share.html").read_text("utf-8")
    assert 'if (window.top !== window.self) el.target = "_top";' in html
