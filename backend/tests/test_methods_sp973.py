"""SP-973: the 方法 page (api/methods.py, static/methods.html, static/i18n/<locale>/methods.json).

  - /methods opens signed out (sign-in mode) and in the owner's app, in Chinese and English; next to it
    everything stays closed
  - one card per model: a short face (one or two sentences), and behind it what it works out, where it is
    used, whether it is calibrated, what is an estimate, the formula and the sources
  - every card cites sources from the one list (about.SOURCES) — or, with no literature, says so and is
    marked 推估; the page's 「N 篇文獻・M 個計算方法」 is that list, the same numbers as the 介紹頁's card
  - every anchor the 介紹頁 links (the four 「為什麼不一樣」 cards, each waterfall layer, the 賽事周期) is a
    card or a filter of the page
  - the numbers on the cards are the code's (planning.py, trailhr.py, terrain_*.py, env.py, race_estimate.py…)
  - the 關於 page's heart rate zones say what the code does by default (COROS % LTHR 6 zones, 自動)
Synthetic data only."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.api import about as AB
from backend.api import methods as M
from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, _clean_env, _client, app, c, env, started,
)

STATIC = Path(__file__).resolve().parents[1] / "static"
LOCALES = ("zh-TW", "en")


def _cat(loc: str, ns: str = "methods") -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def _main(html: str) -> str:
    return html.split('<main class="md-main"', 1)[1].split("</main>", 1)[0]


@pytest.fixture
def owner(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        yield cl


# ---------------------------------------------------------------------------- the words

def test_both_languages_have_the_same_keys_and_every_card_its_words():
    zh, en = _cat("zh-TW"), _cat("en")
    assert set(zh) == set(en)
    ids = {m.id for m in M.MODELS}
    assert {k.split(".")[1] for k in zh if k.startswith("m.")} == ids     # no words for a card that is not there
    for loc, cat in (("zh-TW", zh), ("en", en)):
        for m in M.MODELS:
            for f in ("name", "short", "plain", "where", "calib"):
                assert cat.get(f"m.{m.id}.{f}"), (loc, m.id, f)
            assert (f"m.{m.id}.est" in cat) == m.est, (loc, m.id)          # 推估 said where the model has one
            assert (f"m.{m.id}.nosrc" in cat) == (not m.sources), (loc, m.id)
            assert M.formula_lines({f"methods.{k}": v for k, v in cat.items()}, m.id), (loc, m.id)
        for g in M.GROUPS:
            assert cat.get(f"g.{g}") and cat.get(f"filter.{g}")


def test_the_card_face_is_short():
    """SP-973: the owner found the pages wordy — a card's face is one or two short sentences, the rest
    behind it."""
    for m in M.MODELS:
        zh, en = _cat("zh-TW")[f"m.{m.id}.short"], _cat("en")[f"m.{m.id}.short"]
        assert len(zh) <= 32 and zh.count("。") <= 2, (m.id, len(zh))
        assert len(en) <= 110, (m.id, len(en))
        assert len(zh) < len(_cat("zh-TW")[f"m.{m.id}.plain"]), m.id


def test_every_card_cites_the_one_source_list_or_says_it_has_none():
    for m in M.MODELS:
        assert m.group in M.GROUPS and m.calib in M.CALIB, m.id
        for s in m.sources:
            assert s in AB.SOURCES, (m.id, s)
        if not m.sources:
            assert m.est, m.id                                             # no literature → 推估
    # the layers the 介紹頁's waterfall can show and the cards that explain them
    from backend.api import intro as I
    assert set(M.LAYER_CARD) <= set(I.BREAKDOWN_KEYS)
    assert set(M.LAYER_CARD) >= {"flat", "climb", "terrain", "durability", "heat", "altitude"}
    assert M.CYCLE_CARD in M.BY_ID


def test_the_new_sources_are_methods_of_the_about_page_too():
    """Q1-2 (a): what the 方法 page cites is on 關於, so the 介紹頁's count is one list."""
    cited = {s for m in M.MODELS for s in m.sources}
    on_about = {s for _m, srcs in AB.METHODS for s in srcs}
    assert cited <= on_about
    for sid in ("fornasiero", "kerherve", "clark", "bossi", "markovic", "itra", "peronnet", "wehrlin", "bassett"):
        assert sid in AB.SOURCES and AB.SOURCES[sid].url.startswith("https://"), sid


# ---------------------------------------------------------------------------- the numbers are the code's

def test_the_cards_say_what_the_code_does():
    from backend.engine import planning as PL
    from backend.engine import terrain_calib as TC
    from backend.engine import terrain_difficulty as TD
    from backend.engine import aet_test as AT
    from backend.engine.racepower import env as EV
    from backend.engine.racepower import race_estimate as RE
    from backend.engine.racepower import trailhr as TH
    zh = _cat("zh-TW")
    t = lambda mid: " ".join(v for k, v in zh.items() if k.startswith(f"m.{mid}."))  # noqa: E731
    # 賽事周期
    assert f"{PL.TAPER_DAYS} 天" in t("cycle") and f"{PL.SPECIFIC_WEEKS} 週" in t("cycle")
    assert f"賽前 {PL.MINI_TAPER_DAYS} 天" in t("cycle") and f"拉長到 {PL.TAPER_DAYS_RANGE[1]} 天" in t("cycle")
    assert (PL.BAIYUE_TAPER_DAYS, PL.BAIYUE_LONG_TAPER_DAYS) == (7, 10) and "百岳 7 天、更長的百岳 10 天" in t("cycle")
    rec = PL.REC_BY_SIZE
    assert rec[PL.SHORT] == rec[PL.MEDIUM] == (7, 0) and rec[PL.MARATHON] == (7, 1)
    assert rec[PL.ULTRA] == (14, 0) and rec[PL.HUNDRED] == (14, 2)
    assert "短、中 7 天；馬拉松級 7 天＋回量 1 週；超馬級 14 天；100 英里級 14 天＋回量 2 週" in t("recovery")
    assert PL.SIZE_HOURS == (2.0, 4.0, 6.0, 20.0) and PL.SIZE_EP == (21.0, 42.0, 60.0, 160.0)
    assert "2／4／6／20 小時" in t("recovery") and "21／42／60／160" in t("recovery")
    assert PL.DOWNHILL_BUMP == 1.5 and PL.DOWNHILL_WEEKS[0] == 6 and PL.DOWNHILL_FLAT_DAYS == 3
    assert PL.REC_SHARE == 0.40 and PL.REBUILD_SHARES == (0.50, 0.75)
    # 努力距離、地形難度
    assert TC.DEFAULT == 100.0 and TC.MIN_CLIMB_RUNS == 8 and "d = 100" in t("effort") and "至少 8 次" in t("effort")
    from backend.engine import calibrate as CAL
    item = CAL._registry()["climb_divisor_run"]
    assert item.k == 10 and item.bounds == (50.0, 300.0) and "n ÷ (n + 10)" in t("effort") and "50–300 m" in t("effort")
    assert TD.PRIOR == {"normal": 1.0, "technical": 1.15, "scramble": 1.35} and TD.SHRINK_K == 2.0
    assert TD.MULT_RANGE == (1.0, 2.5) and "× 1.15" in t("terrain") and "× 1.35" in t("terrain")
    # 越野心率配速模型、長時間疲勞
    assert TH.XSTAR["anchors"] == ((0.5, 1.00), (3.0, 0.90), (12.0, 0.85))
    assert "0.5 小時 1.00、3 小時 0.90、12 小時 0.85" in t("trail-hr")
    assert TH.TRAILHR["v_floor"] == 0.80 and TH.TRAILHR["delta_prior"] == 0.05
    assert "max(0.80" in t("durability") and "每小時 0.05" in t("durability")
    # the back-test numbers are the 介紹頁's (api/intro.BACKTEST)
    from backend.api import intro as I
    assert f"{I.BACKTEST['durability_with_pct']} %" in t("durability")
    assert f"{I.BACKTEST['durability_without_pct']} %" in t("durability")
    # 熱、海拔: the worked examples are what env.py gives
    assert round(EV.heat_penalty_pct(21.8, 73), 1) == 2.3 and "約 2.3 %" in t("heat")
    assert round(EV.dew_point(21.8, 73)["dew_c"], 1) == 16.7
    assert round(EV.altitude_factor(EV.pressure_torr(412, 21.8)) * 100, 1) == 98.3
    assert round(EV.altitude_factor(EV.pressure_torr(3000, 10)) * 100, 1) == 86.3
    assert "0.001341x² − 0.249517x + 11.699986" in t("heat")
    # AeT 測試
    assert (AT.BAND_LOW, AT.BAND_HIGH) == (0.035, 0.05) and "< 3.5 %" in t("aet") and "> 5 %" in t("aet")
    # Riegel: the exponent the code uses (race_estimate.K), not a typed one
    assert RE.K == -0.07 and "k 預設 −0.07" in t("riegel")
    assert f"{1 / (1 + RE.K):.3f}" == "1.075" and "約 1.075" in t("riegel")
    # 坡度: Minetti's polynomials as grade_model.py carries them
    src = (Path(__file__).resolve().parents[1] / "engine" / "racepower" / "grade_model.py").read_text("utf-8")
    for poly in ("155.4i⁵ − 30.4i⁴ − 43.3i³ + 46.3i² + 19.5i + 3.6", "280.5i⁵ − 58.7i⁴ − 76.8i³ + 51.9i² + 19.6i + 2.5"):
        assert poly in src and poly in t("grade")


# ---------------------------------------------------------------------------- the page

DENY = re.compile(r"WKO|\.md\b|docs/|擁有者|\bowner\b|Obsi[d]ian|yichlin|localhost|127\.0\.0\.1|⟦", re.I)


@pytest.mark.parametrize("loc", LOCALES)
def test_the_page_has_every_card_and_its_detail(loc, owner):
    r = owner.get(f"/methods?lang={loc}", headers=HTML)
    assert r.status_code == 200 and f'<html lang="{loc}">' in r.text
    assert r.headers["cache-control"] == "private, no-cache"
    assert 'data-page="methods"' in r.text
    main = _main(r.text)
    cat = _cat(loc)
    for m in M.MODELS:
        assert f'<a class="md-card" href="#{m.id}" data-id="{m.id}"' in main, m.id
        assert f'<section class="md-d" id="{m.id}"' in main, m.id
        sec = main[main.index(f'<section class="md-d" id="{m.id}"'):]
        sec = sec[:sec.index("</section>")]
        for s in m.sources:
            cite = AB.SOURCES[s].cite.replace("&", "&amp;")
            assert cite in sec, (m.id, s)
        assert ("<dl>" in sec) and cat[f"m.{m.id}.f1.k"] in sec, m.id
    # the filters: the groups, calibrated, estimates
    for f in M.FILTERS:
        assert f'data-f="{f}"' in main, f
    # the one list: the same count as 關於 and the 介紹頁's card
    from backend.api import intro as I
    w = I.why()
    assert w["sources"] == len(AB.SOURCES) and w["methods"] == len(AB.METHODS)
    count = cat["count"].format(n=len(AB.SOURCES), m=len(AB.METHODS))
    assert count in main and 'href="/about#methods"' in main
    # no internal notes; external links open in a new tab without handing the page over
    text = re.sub(r"<[^>]+>", " ", main)
    assert not DENY.findall(text), DENY.findall(text)
    hrefs = re.findall(r'href="([^"]+)"', main)
    for h in hrefs:
        assert h.startswith("https://") or re.fullmatch(r"#[a-z-]+", h) or h == "/about#methods", h
    ext = [h for h in hrefs if h.startswith("https://")]
    assert main.count('target="_blank"') == main.count('rel="noopener"') == len(ext)
    if loc == "en":
        assert not re.search(r"[一-鿿]", text), re.findall(r".{20}[一-鿿].{20}", text)[:3]


def test_signed_out_the_page_opens_and_only_it(c):  # noqa: F811
    for loc in LOCALES:
        r = c.get(f"/methods?lang={loc}", headers=HTML)
        assert r.status_code == 200, (loc, r.status_code, r.text[:200])
        assert '"mode": "signed_out"' in r.text
    assert c.get("/methodsx", headers=HTML).status_code in (302, 401)
    assert c.get("/methods/x", headers=HTML).status_code in (302, 401, 404)
    assert c.get("/api/v1/plan").status_code == 401


def test_the_demo_has_the_page(monkeypatch, tmp_path):
    from backend.tests import demo_fixtures as F
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    F.make_base(root)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False, follow_redirects=False) as cl:
        assert cl.get("/methods", headers=HTML).status_code == 200


def test_no_floating_suggestion_box_on_a_page_for_reading():
    js = (STATIC / "shell.js").read_text("utf-8")
    assert 'me?.dataset.page !== "methods") {' in js


# ---------------------------------------------------------------------------- the 介紹頁's links

def test_the_intro_links_land_on_a_card_or_a_filter():
    html = (STATIC / "intro.html").read_text("utf-8")
    targets = re.findall(r'href="/methods(?:#([a-z-]+))?"', html)
    assert targets
    for t in targets:
        assert t == "" or t in M.BY_ID or t in M.FILTERS, t
    why = html[html.index('id="why"'):html.index("</section>", html.index('id="why"'))]
    for href in ('href="/methods"', 'href="/methods#zones"', 'href="/methods#calibrated"', 'href="/methods#durability"'):
        assert href in why, href                                       # the four cards
    cyc = html[html.index('id="cycle"'):html.index("</section>", html.index('id="cycle"'))]
    assert f'href="/methods#{M.CYCLE_CARD}"' in cyc                     # SP-975's 賽事周期
    # the waterfall: each layer's name links its card, the map is the page's own
    m = re.search(r"const METHOD_CARD = (\{[^}]*\});", html)
    js = dict(re.findall(r'(\w+): "([a-z-]+)"', m.group(1)))
    assert js == M.LAYER_CARD
    assert 'h.href = "/methods#" + METHOD_CARD[key]' in html


def test_the_about_page_says_the_codes_default_zones():
    from backend.engine import hr_profile as HP
    # production's default (conftest pins "lthr" for the other tests, so read the module's own line)
    src = (Path(__file__).resolve().parents[1] / "engine" / "hr_profile.py").read_text("utf-8")
    assert re.search(r"^DEFAULT_SETTING = AUTO\b", src, re.M) and HP.AUTO == "auto"
    assert HP.DEFAULT_RATIOS["lthr"] == (0.80, 0.90, 0.95, 1.02, 1.06)
    zh, en = _cat("zh-TW", "about")["m.hrzones.desc"], _cat("en", "about")["m.hrzones.desc"]
    assert "COROS" in zh and "6 區" in zh and "自動" in zh and "Friel" in zh
    assert "COROS" in en and "six zones" in en and "automatic" in en
    assert "< 80／80–90／90–95／95–102／102–106／> 106" in _cat("zh-TW")["m.zones.f3.v"]
