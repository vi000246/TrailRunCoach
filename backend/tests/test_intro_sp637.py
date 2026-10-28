"""SP-637: the 介紹頁 made interactive.

- the small calculator's public API (GET /api/v1/intro/estimate): signed out, only the built-in fictional
  runner (no runner parameter is read), the distance / climb in a fixed range per kind, rounded to a grid
  and cached (one computation per grid point), one per-IP limit (the real client behind a trusted proxy),
  the answer just the finish time and a few totals — no model parameter, no intermediate value;
- the examples (越野／百岳／路跑) the page switches between: three repo files, fixed ids, a fictional runner,
  no tenant; served trimmed (GET /api/v1/intro/example/<kind>): profile, legs (pace, heart rate, fuel),
  never a source note;
- exactly these paths are public; every other private path stays behind sign-in;
- the page: no Stryd anywhere, the FAQ asks about a power meter; no formula in its script; the
  replaced screenshots gone from it and from the shot list.
- R1 (2026-10-17, 「課表才是主角，計算機是配角」): the hero is the 課表 calendar (GET /api/v1/intro/plan: the
  fictional runner's frozen weeks before the 50K, no date), the 課表 section's 新增一堂課 → 選範本 → 看結構 (GET
  /api/v1/intro/templates: library templates as step lengths and levels, no target number, no source), the
  small calculator moved into the race section after it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, OWNER_EMAIL, app, browser, c, csrf, env, location, sign_in, sign_up, started,
)

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "backend" / "static"
SHOTS = ROOT / "backend" / "scripts" / "intro_shots.json"
LOCALES = ("zh-TW", "en")
KINDS = ("trail", "baiyue", "road")
EST = "/api/v1/intro/estimate"


@pytest.fixture(autouse=True)
def _fresh():
    from backend.api import intro as I
    I.reset()
    yield
    I.reset()


# ---------------------------------------------------------------------------- the estimate API

def test_estimate_signed_out_gives_a_finish_time_and_nothing_else(c):
    r = c.get(EST, params={"kind": "trail", "km": 50, "gain": 2800})
    assert r.status_code == 200, r.text
    d = r.json()
    assert set(d) == {"kind", "km", "gain_m", "time_s", "pace_s_per_km", "fictional"}
    assert d["kind"] == "trail" and d["km"] == 50 and d["gain_m"] == 2800 and d["fictional"] is True
    assert 3 * 3600 < d["time_s"] < 12 * 3600 and d["time_s"] % 60 == 0
    assert d["pace_s_per_km"] == pytest.approx(d["time_s"] / 50, abs=1)
    assert "public" in r.headers.get("cache-control", "")


@pytest.mark.parametrize("kind,km,gain", [("road", 21, 50), ("baiyue", 38, 3800), ("trail", 20, 900)])
def test_each_kind_answers(c, kind, km, gain):
    d = c.get(EST, params={"kind": kind, "km": km, "gain": gain}).json()
    assert d["kind"] == kind and d["time_s"] > 0


def test_the_answer_names_no_model_parameter(c):
    from backend.api import intro as I
    runner = json.loads(I.RUNNER_FILE.read_text("utf-8"))
    for kind, km, gain in (("trail", 50, 2800), ("road", 21, 50), ("baiyue", 38, 3800)):
        text = c.get(EST, params={"kind": kind, "km": km, "gain": gain}).text.lower()
        for w in ("cp", "w_prime", "tte", "riegel", "re", "eph", "power", "watt", "weight", "used", "zones",
                  "warnings", "scenarios", "task", "effort", "aet", "env", "pack"):
            assert f'"{w}' not in text, (kind, w)
    assert runner["cp"] and runner["re"]                # the runner has them; the answer never shows them


# SP-969 Q2-3 (the user, 2026-10-28): the small calculator and the profile left the page, so "the calculator next
# to the profile reads the example's finish time" has nothing to compare any more — that test went with them (the
# trail example is now run at assumed race settings, SP-969 Q2: 95 % effort, 技術路段多, fatigue; the total-only
# estimate of the same km / climb cannot read the same)


def test_more_climb_takes_longer_and_more_distance_too(c):
    t = lambda km, g: c.get(EST, params={"kind": "trail", "km": km, "gain": g}).json()["time_s"]  # noqa: E731
    assert t(50, 3000) > t(50, 1000) and t(60, 2000) > t(40, 2000)


def test_no_runner_parameter_is_read(c):
    base = c.get(EST, params={"kind": "trail", "km": 50, "gain": 2800}).json()
    from backend.api import intro as I
    I.reset()
    other = c.get(EST, params={"kind": "trail", "km": 50, "gain": 2800, "cp": 400, "weight": 40, "re": 1.5,
                               "k": 0, "tte": 9000, "eph": 9, "pack": 1, "athlete": "1", "user": "2"}).json()
    assert other == base


@pytest.mark.parametrize("params", [
    {"kind": "trail", "km": 0, "gain": 0}, {"kind": "trail", "km": 300, "gain": 2000},
    {"kind": "trail", "km": 50, "gain": 20000}, {"kind": "trail", "km": 50, "gain": -100},
    {"kind": "road", "km": 100, "gain": 50}, {"kind": "road", "km": 21, "gain": 3000},
    {"kind": "baiyue", "km": 500, "gain": 3000}, {"kind": "ultra", "km": 50, "gain": 100},
    {"kind": "trail", "km": "nan", "gain": 100}, {"kind": "trail", "km": "inf", "gain": 100},
    {"kind": "trail", "gain": 100}, {"kind": "trail", "km": "x", "gain": 100},
])
def test_inputs_out_of_range_are_refused(c, params):
    assert c.get(EST, params=params).status_code == 422


def test_limits_are_sane():
    from backend.api import intro as I
    assert set(I.LIMITS) == set(KINDS)
    for kind, L in I.LIMITS.items():
        assert 0 < L["km"][0] < L["km"][1] <= 120 and 0 <= L["gain"][0] < L["gain"][1] <= 8000, kind
        assert L["km_step"] >= 0.5 and L["gain_step"] >= 10, kind
    assert I.LIMITS["road"]["km"][1] >= 42 and I.LIMITS["road"]["gain"][1] <= 1000


def test_inputs_are_rounded_to_a_grid_and_each_point_is_computed_once(c, monkeypatch):
    from backend.api import intro as I
    from backend.engine.racepower import calc as CALC
    I.ratio("trail")                                    # the example's own (once per kind)
    calls = []
    real = CALC.predict
    monkeypatch.setattr(CALC, "predict", lambda ctx, body: calls.append((body.distance_km, body.gain_m)) or real(ctx, body))
    a = c.get(EST, params={"kind": "trail", "km": 50.3, "gain": 2790}).json()
    b = c.get(EST, params={"kind": "trail", "km": 49.8, "gain": 2810}).json()
    again = c.get(EST, params={"kind": "trail", "km": 50, "gain": 2800}).json()
    assert a == b == again and a["km"] == 50 and a["gain_m"] == 2800
    assert calls == [(50.0, 2800.0)]
    assert I.cache_size() == 1


def test_the_runner_is_the_frozen_fictional_one(c, monkeypatch):
    # never a tenant's: the context reads backend/demo/intro_runner.json only
    from backend import tenancy
    from backend.api import racepower as RP
    monkeypatch.setattr(RP, "inputs", lambda *a, **k: pytest.fail("read the live athlete"))
    monkeypatch.setattr(tenancy, "current", lambda: pytest.fail("read a tenant"), raising=False)
    assert c.get(EST, params={"kind": "road", "km": 21, "gain": 50}).status_code == 200


def test_per_ip_limit(c, monkeypatch):
    from backend.api import intro as I
    from backend.security import ratelimit as RL
    t = [1000.0]
    monkeypatch.setattr(RL, "clock", lambda: t[0])
    codes = [c.get(EST, params={"kind": "road", "km": 21, "gain": 50}).status_code for _ in range(I.BURST + 3)]
    assert codes[:I.BURST] == [200] * I.BURST and codes[-1] == 429
    r = c.get(EST, params={"kind": "road", "km": 21, "gain": 50})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    t[0] += 60                                           # refills
    assert c.get(EST, params={"kind": "road", "km": 21, "gain": 50}).status_code == 200


def _scope(peer: str, **headers) -> dict:
    return {"type": "http", "client": (peer, 1234),
            "headers": [(k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()]}


def test_the_limit_counts_the_real_client_behind_a_trusted_proxy(monkeypatch):
    from backend.api import intro as I
    monkeypatch.setenv("WKO5COACH_TRUSTED_PROXIES", "172.18.0.0/16")
    # behind our proxy (cloudflared on the Docker bridge): the visitor's address
    assert I.client_key(_scope("172.18.0.5", cf_connecting_ip="203.0.113.7")) == "203.0.113.7"
    assert I.client_key(_scope("172.18.0.5", x_forwarded_for="198.51.100.1, 203.0.113.9")) == "203.0.113.9"
    # anyone else's header is ignored: a stranger can't pick a fresh key per request
    assert I.client_key(_scope("198.51.100.20", cf_connecting_ip="203.0.113.7")) == "198.51.100.20"
    # an IPv6 client by its /64
    assert I.client_key(_scope("2001:db8:1:2::5")) == I.client_key(_scope("2001:db8:1:2::99"))


def test_distinct_clients_have_their_own_budget(monkeypatch):
    from backend.api import intro as I
    monkeypatch.setenv("WKO5COACH_TRUSTED_PROXIES", "172.18.0.0/16")
    a = _scope("172.18.0.5", cf_connecting_ip="203.0.113.7")
    b = _scope("172.18.0.5", cf_connecting_ip="203.0.113.8")
    assert all(I.allow(a) for _ in range(I.BURST)) and not I.allow(a)
    assert I.allow(b)


def test_cache_misses_have_a_site_wide_ceiling(c, monkeypatch):
    from backend.api import intro as I
    monkeypatch.setattr(I, "MISS", type(I.MISS)(rate=2, per=60, burst=2))
    monkeypatch.setattr(I, "client_key", lambda scope: f"ip-{len(I._CACHE)}")    # a new client each time
    codes = [c.get(EST, params={"kind": "trail", "km": km, "gain": 1000}).status_code for km in (20, 21, 22)]
    assert codes == [200, 200, 503]
    assert c.get(EST, params={"kind": "trail", "km": 20, "gain": 1000}).status_code == 200      # cached: no compute


def test_fails_gracefully_without_the_core_or_the_runner(c, monkeypatch):
    # the public mirror (SP-500): a stub core raises CoreNotIncluded — a 503, never a 500, nothing cached
    from backend.api import intro as I
    from backend.engine.core_stub import CoreNotIncluded
    from backend.engine.racepower import calc as CALC

    def boom(*a, **k):
        raise CoreNotIncluded("racepower")
    monkeypatch.setattr(CALC, "predict", boom)
    r = c.get(EST, params={"kind": "trail", "km": 30, "gain": 1500})
    assert r.status_code == 503 and I.cache_size() == 0
    monkeypatch.undo()
    monkeypatch.setattr(I, "RUNNER_FILE", I.RUNNER_FILE.with_name("missing.json"))
    I.reset()
    assert c.get(EST, params={"kind": "trail", "km": 30, "gain": 1500}).status_code == 503


def test_one_computation_is_quick():
    import time

    from backend.api import intro as I
    I.reset()
    t0 = time.perf_counter()
    for kind, km, gain in (("trail", 50, 2800), ("road", 21, 50), ("baiyue", 38, 3800)):
        I.estimate(kind, km, gain)
    assert (time.perf_counter() - t0) / 3 < 0.5          # well under a second here; ×8.5 on the NAS


# ---------------------------------------------------------------------------- public paths

def test_exactly_the_intro_api_paths_are_public(c):
    from backend.accounts import login as LG
    from backend.api import intro as I
    for p in ("/api/v1/intro/estimate", "/api/v1/intro/example/trail", "/api/v1/intro/example/baiyue",
              "/api/v1/intro/example/road", "/api/v1/intro/templates", "/api/v1/intro/plan"):   # R1: the 課表 demo
        assert LG.is_public(p), p
    assert {p for p in LG.PUBLIC_PATHS if p.startswith("/api/v1/intro")} == set(I.PUBLIC_PATHS)
    for p in ("/api/v1/intro", "/api/v1/intro/", "/api/v1/intro/example", "/api/v1/intro/example/x",
              "/api/v1/intro/estimatex", "/api/v1/intro/plan/x", "/api/v1/intro/templatesx",
              "/api/v1/overview/plan/calendar", "/api/v1/overview/plan/steps/templates",
              "/api/v1/racepower/predict", "/api/v1/racepower/inputs"):
        assert not LG.is_public(p), p
    # still behind sign-in, signed out
    assert c.get("/api/v1/racepower/inputs").status_code == 401
    assert c.post("/api/v1/racepower/predict", json={"distance_km": 10}).status_code in (401, 403)
    assert c.get("/api/v1/plan").status_code == 401
    assert location(c.get("/api/v1/overview/page", headers=HTML)).startswith("/login?next=")


# ---------------------------------------------------------------------------- the three examples

def test_three_examples_fixed_ids_fictional_no_tenant(c, monkeypatch):
    from backend import tenancy_mw as TM
    from backend.accounts import store
    from backend.engine.racepower import share as SH
    looked = []
    monkeypatch.setattr(store, "share_owner", lambda sid: looked.append(sid))
    assert set(SH.EXAMPLE_KINDS.values()) == set(KINDS) and set(SH.EXAMPLES) == set(SH.EXAMPLE_KINDS)
    for sid, kind in SH.EXAMPLE_KINDS.items():
        assert SH.ID_RE.match(sid) and len(sid) != len(SH.new_id()), sid
        assert TM._share_tenant("/share/" + sid + "/data").kind == "anonymous"
        snap = c.get("/share/" + sid + "/data").json()
        assert snap["example"] is True and "虛構" in snap["title"] and snap["expires"] is None, sid
        assert snap["type"] == kind and snap["segments"] and snap["profile"]["km"], sid
        assert snap["weight"] is None and "w_per_kg" not in snap["summary"], sid
        assert set(snap) - {"example"} <= set(SH.snapshot({}, title="x")), sid
    assert looked == []
    assert SH.load(SH.EXAMPLE_ID) == SH.example()


def test_example_api_is_trimmed_and_has_no_source_note(c):
    for kind in KINDS:
        r = c.get(f"/api/v1/intro/example/{kind}")
        assert r.status_code == 200, kind
        d = r.json()
        # SP-1013: + stops (km and a type code), which the try card rings on the profile
        assert set(d) == {"kind", "summary", "profile", "legs", "days", "stops", "calc"}, kind
        assert all(set(x) == {"km", "type"} for x in d["stops"]), kind
        assert d["kind"] == kind and len(d["profile"]) <= 300 and d["legs"], kind
        assert {x["mode"] for x in d["legs"]} <= {"run", "walk", "mix"}, kind
        for leg in d["legs"]:
            assert set(leg) <= {"n", "a", "b", "cls", "mode", "pace_s_per_km", "t_s", "cum_s", "hr_cap", "cho_g",
                                "water_ml", "na_mg", "gain_m", "loss_m", "grade", "day"}, (kind, leg)
        text = r.text
        assert "Stryd" not in text and "src" not in text and "power" not in text, kind
        assert re.search(r"[一-鿿]", text) is None, kind     # numbers only: the page words them
        L = d["calc"]
        assert L["km"][0] <= d["calc"]["default"]["km"] <= L["km"][1], kind
        assert d["calc"]["result"]["time_s"] > 0
    assert c.get("/api/v1/intro/example/ultra").status_code == 401        # not one of the public paths


def test_example_api_reads_no_tenant(c, monkeypatch):
    from backend.api import racepower as RP
    monkeypatch.setattr(RP, "inputs", lambda *a, **k: pytest.fail("read the live athlete"))
    assert c.get("/api/v1/intro/example/trail").status_code == 200


# ---------------------------------------------------------------------------- the page

def _page() -> str:
    return (STATIC / "intro.html").read_text("utf-8")


def test_no_stryd_on_the_page_and_the_faq_asks_about_a_power_meter():
    assert "stryd" not in _page().lower()
    for loc in LOCALES:
        cat = json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8"))
        assert not [k for k, v in cat.items() if "stryd" in str(v).lower()], loc
    assert json.loads((STATIC / "i18n" / "zh-TW" / "intro.json").read_text("utf-8"))["faq.power.q"] == "沒有功率計可以用嗎？"
    assert json.loads((STATIC / "i18n" / "en" / "intro.json").read_text("utf-8"))["faq.power.q"] == "No power meter — can I use it?"


def test_the_page_script_computes_no_finish_time():
    # SP-500: the core stays on the server — the page asks /api/v1/intro/estimate and only formats the answer
    html = _page()
    script = "".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    # (SP-969 Q2-3: the small calculator — the page's /estimate call — left the page; the API stays)
    assert 'API = "/api/v1/intro/"' in script and '"example/"' in script
    for w in ("Math.pow", "Math.exp", "Math.log", "**", "riegel", "Riegel", "tobler", "eph", "cp *", "cp*"):
        assert w not in script, w


def test_the_hero_is_the_schedule_calendar_and_the_calculator_supports_the_race_section():
    # R1 (2026-10-17): 「課表才是主角，計算機是配角」 — the hero's right side is the 課表 calendar, the 課表
    # section (新增一堂課 → 選範本 → 看結構) comes before the race section, the small calculator lives there
    html = _page()
    hero = html[html.index('class="hero"'):html.index("</section>")]
    assert 'id="cal"' in hero and 'id="cal-weeks"' in hero and 'id="cal-detail"' in hero
    assert 'id="calc"' not in hero and 'type="range"' not in hero and "data-shot" not in hero
    assert html.index('id="plan"') < html.index('id="race"')
    plan = html[html.index('id="plan"'):html.index('id="race"')]
    assert 'id="tpl"' in plan and 'id="tpl-cats"' in plan and 'id="tpl-list"' in plan and 'id="tpl-view"' in plan
    assert "data-shot" not in plan
    race = html[html.index('id="race"'):html.index('id="read"')]
    # SP-969 Q2-3: the race section is the waterfall now — the small calculator and the profile left the page
    assert 'id="wf"' in race and 'id="calc"' not in race and 'id="profile"' not in race and "data-shot" not in race
    shots = {s["name"] for s in json.loads(SHOTS.read_text("utf-8"))["shots"]}
    assert "intro-race" not in shots and "intro-legs" not in shots and "intro-schedule" not in shots
    assert "intro-status" in shots
    script = "".join(re.findall(r"<script>(.*?)</script>", html, re.S))
    assert '"plan"' in script and '"templates"' in script


def test_every_word_the_schedule_demo_uses_is_in_both_languages():
    from backend.api import intro as I
    d, t = I.plan(), I.templates()
    for loc in LOCALES:
        cat = json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8"))
        need = {"cal.h", "cal.sub", "cal.hint", "cal.later", "cal.later_note", "cal.weeks", "cal.week_1", "cal.race_week",
                "cal.hours", "cal.rest", "cal.rest_chip", "cal.race_a", "cal.race_a_chip", "cal.race_a_note", "cal.race_b",
                "cal.race_b_chip", "cal.race_b_note", "cal.strength_note", "tpl.h", "tpl.total", "step.repeat"}
        need |= {f"cal.dow.{i}" for i in range(7)} | {f"cal.dow_long.{i}" for i in range(7)}
        need |= {f"lv.{i}" for i in range(6)} | {f"step.{k}" for k in ("warm", "work", "rest", "cool", "other")}
        need |= {f"cal.phase.{w['phase']}" for w in d["weeks"] if w["phase"]}
        for w in d["weeks"]:
            for day in w["days"]:
                for s in day["sessions"]:
                    need |= {f"cal.kind.{s['kind']}", f"cal.kind.{s['kind']}_long"}
        for c in t["cats"]:
            need.add(f"tpl.cat.{c['id']}")
            for row in c["templates"]:
                need |= {f"tpl.t.{row['key']}.name", f"tpl.t.{row['key']}.purpose"}
        missing = sorted(k for k in need if not cat.get(k))
        assert not missing, (loc, missing)
        assert not [k for k in need if "stryd" in str(cat.get(k)).lower()], loc


# ---------------------------------------------------------------------------- R1: the 課表 demo API

def test_the_level_of_a_step_follows_its_own_target_scale():
    from backend.api import intro as I
    assert I.level({"type": "power", "mode": "pct", "lo": 0.65, "hi": 0.8}, "work") == 1
    assert I.level({"type": "power", "mode": "pct", "lo": 1.01, "hi": 1.06}, "work") == 4
    assert I.level({"type": "hr", "mode": "pct", "lo": 0.90, "hi": 0.94}, "work") == 3      # Friel Z3
    assert I.level({"type": "hr", "mode": "pct", "lo": 0.91, "hi": 1.0}, "work") == 4       # Friel Z4 (95–99 %)
    assert I.level({"type": "hr", "mode": "zone", "zone": "aet"}, "work") == 2
    assert I.level({"type": "pace", "mode": "pct", "lo": 0.99, "hi": 1.01}, "work") == 4      # T pace: threshold
    assert I.level({"type": "pace", "mode": "pct", "lo": 1.15, "hi": 1.25}, "work") == 1
    assert I.level({"type": "rpe", "lo": 9, "hi": 10}, "work") == 5
    assert I.level({"type": "auto", "intent": "easy"}, "cool") == 1
    assert I.level({"type": "auto", "intent": "open"}, "work") is None       # an all-out / open bout: no level
    assert I.level({"type": "auto", "intent": "open"}, "rest") == 1


def test_a_structure_keeps_repeats_and_drops_every_target_number():
    from backend.api import intro as I
    from backend.engine import workout_templates as WT
    st = I.structure(WT.items_of(WT.BY_KEY["daniels_cruise"]))
    rep = next(x for x in st if "x" in x)
    assert rep["x"] == 5 and [y["k"] for y in rep["items"]] == ["work", "rest"] and rep["items"][0]["lv"] == 4
    assert rep["items"][0]["s"] == 360
    for x in [y for x in st for y in (x.get("items") or [x])]:
        assert set(x) == {"k", "s", "lv", "n"}, x
        assert x["s"] > 0 and (x["lv"] is None or 1 <= x["lv"] <= 5)


def test_templates_api_is_public_the_library_only_and_names_no_target_or_source(c, monkeypatch):
    from backend.api import intro as I
    from backend.engine import user_templates as UT
    monkeypatch.setattr(UT, "list_all", lambda *a, **k: pytest.fail("read someone's templates"))
    r = c.get("/api/v1/intro/templates")
    assert r.status_code == 200
    d = r.json()
    assert [x["id"] for x in d["cats"]] == list(I.TEMPLATE_KEYS)
    for cat in d["cats"]:
        assert [t["key"] for t in cat["templates"]] == list(I.TEMPLATE_KEYS[cat["id"]])
        for t in cat["templates"]:
            assert set(t) == {"key", "steps"} and t["steps"], t["key"]
    text = r.text
    for w in ("Stryd", "Palladino", "http", "src", "CP", "LTHR", "lo\"", "hi\"", "target"):
        assert w not in text, w


def test_plan_api_is_the_frozen_fictional_calendar_without_a_date(c, monkeypatch):
    from backend.api import plan_sessions as PSA
    monkeypatch.setattr(PSA, "_inputs", lambda *a, **k: pytest.fail("read the live athlete"))
    r = c.get("/api/v1/intro/plan")
    assert r.status_code == 200
    d = r.json()
    assert [w["before"] for w in d["weeks"]] == [3, 2, 1, 0]
    assert all(len(w["days"]) == 7 for w in d["weeks"])
    races = [(wi, day["dow"], day["race"]) for wi, w in enumerate(d["weeks"]) for day in w["days"] if day["race"]]
    assert (3, 5, {"priority": "A", "main": True}) in races              # the 50K on the race week's Saturday
    sessions = [s for w in d["weeks"] for day in w["days"] for s in day["sessions"]]
    assert len(sessions) >= 8 and any(s["steps"] for s in sessions)
    assert {s["kind"] for s in sessions} >= {"easy", "climb", "long"}
    # the days after the plan's horizon are marked, and they hold no session
    later = [day for w in d["weeks"] for day in w["days"] if not day["planned"]]
    assert later and not any(day["sessions"] for day in later)
    assert re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}", r.text) is None
    assert re.search(r"\d{4}-\d{2}-\d{2}", (Path(__file__).resolve().parents[1] / "demo" / "intro_plan.json").read_text("utf-8")) is None


def test_plan_api_without_its_file_is_a_404(c, monkeypatch, tmp_path):
    from backend.api import intro as I
    monkeypatch.setattr(I, "PLAN_FILE", tmp_path / "none.json")
    assert c.get("/api/v1/intro/plan").status_code == 404


def test_the_switch_has_the_three_kinds_and_their_words():
    html = _page()
    for kind in KINDS:
        assert f'data-kind="{kind}"' in html, kind
    for loc in LOCALES:
        cat = json.loads((STATIC / "i18n" / loc / "intro.json").read_text("utf-8"))
        for kind in KINDS:
            for k in (f"kind.{kind}.h1", f"kind.{kind}.lead", f"kind.{kind}.profile"):
                assert cat.get(k), (loc, k)


def test_motion_respects_reduced_motion():
    html = _page()
    assert "prefers-reduced-motion" in html
    assert re.search(r"matchMedia\(\s*['\"]\(prefers-reduced-motion: reduce\)['\"]\s*\)", html)


# ---------------------------------------------------------------------------- no calendar date in the examples

def test_the_examples_carry_no_calendar_date():
    # SP-622 Q2-3: a fixed race day reads as a race already run once it passed — 「賽前 N 週」 instead
    from backend.engine.racepower import share as SH
    for sid, path in SH.EXAMPLES.items():
        text = path.read_text("utf-8")
        assert re.search(r"\d{4}-\d{2}-\d{2}|\d{4}/\d{1,2}/\d{1,2}", text) is None, sid
        snap = json.loads(text)
        assert snap["created"] is None and snap["inputs"].get("date") is None, sid
        w = snap["inputs"].get("weeks_before")
        assert w is None or (isinstance(w, int) and w > 0), sid


def test_undated_turns_the_race_day_into_weeks_before():
    import datetime as dt

    from backend.scripts import intro_example as IE
    snap = {"created": "2026-10-15T13:00:00", "inputs": {"date": "2026-11-14", "start_time": "06:00"},
            "fuel": {"schedule": [{"t": "2026-11-14T07:30", "x": "on 2026-11-14"}]}}
    out = IE.undated(snap, dt.date(2026, 10, 15))
    assert out["created"] is None and out["inputs"]["date"] is None and out["inputs"]["weeks_before"] == 4
    assert out["inputs"]["start_time"] == "06:00" and out["fuel"]["schedule"][0]["t"] == "07:30"
    assert "2026" not in json.dumps(out)
    assert IE.undated({"inputs": {"date": "2026-01-01"}}, dt.date(2026, 10, 15))["inputs"]["weeks_before"] is None


def test_the_share_page_shows_weeks_before_for_an_example():
    html = (STATIC / "share.html").read_text("utf-8")
    # SP-460: the words are the share catalog's (share.weeks_before: 「賽前 {n} 週」)
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "share.json").read_text("utf-8"))["weeks_before"]
    assert "I.weeks_before" in html and 'JT("weeks_before"' in html and "S.created &&" in html
    assert all("賽前" in v for v in (zh.values() if isinstance(zh, dict) else [zh]))


def test_the_try_link_follows_the_kind_to_an_existing_example():
    from backend.engine.racepower import share as SH
    html = _page()
    m = re.search(r"const SHARE = (\{[^}]*\});", html)
    assert m
    ids = dict(re.findall(r'(\w+): "([a-z0-9-]+)"', m.group(1)))
    assert ids == {v: k for k, v in SH.EXAMPLE_KINDS.items()}
