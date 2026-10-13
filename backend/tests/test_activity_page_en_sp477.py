"""SP-477: 圖表分析 › 單次活動 in English (?lang=en) — the page's own words and the review
cards it draws (workout_review / interval_eval) come out in English, not Chinese.

The browser check of the whole page (every dashboard on the demo athlete) is
backend/tests/e2e/test_activity_page_en_sp477.py; these are the fast parts: the viewer's
catalog, the Chinese literals the SP-438 screenshot showed, and the review JSON in `en`.
Synthetic data only (no WKO5 folder, no real home)."""
import datetime as dt
import re

from backend.engine import interval_eval as IE
from backend.engine import workout_review as R
from backend.i18n import pages as P
from backend.i18n import use_locale
from backend.scripts import i18n_extract as X
from backend.tests.test_climb_profile import SETTINGS as TRAIL_SETTINGS
from backend.tests.test_climb_profile import _trail
from backend.tests.test_workout_review import SETTINGS, _run
from backend.tests.wko5_fakes import FakeDataset

TODAY = dt.date(2026, 9, 30)
# Chinese characters and full-width punctuation (「（：」 …)
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")


def _no_cjk(texts, where: str) -> None:
    bad = [t for t in texts if isinstance(t, str) and CJK.search(t)]
    assert not bad, f"{where}: still Chinese: {bad}"


# ---- the viewer page (static/wko5_viewer.html + its helpers) -----------------

def test_viewer_catalog_is_english_and_keeps_plurals():
    cat = P.page_catalog(P.namespaces_for("wko5_viewer"), "en")
    zh = X.frontend_catalogs()["zh-TW"]["viewer"]
    missing = [k for k in zh if f"viewer.{k}" not in cat]
    assert not missing
    vals = [x for k, v in cat.items() if k.startswith("viewer.") and not k.startswith("viewer.lang.")
            for x in (v.values() if isinstance(v, dict) else [v])]
    _no_cjk(vals, "viewer catalog (en)")
    # a plural reaches the page as {"one", "other"} (it used to be dropped -> the Chinese fallback)
    assert cat["viewer.n_charts"] == {"one": "{n} chart", "other": "{n} charts"}
    assert cat["viewer.n_acts"]["other"] == "{n} activities"
    # markup replaced server side takes the plural's "other"
    assert P.apply_catalog('<b data-i18n="viewer.n_charts">x</b>', cat) == '<b data-i18n="viewer.n_charts">{n} charts</b>'


# what the SP-438 acceptance screenshot (and the SP-477 DOM scan) still showed in Chinese
SHOT_LITERALS = ("疼痛？", "我的圖表", "張圖", "放大這張圖", "⤢ 放大", "圖例", "路線顏色", "單色", "區間模型",
                 "這一頁看什麼", "左右拖曳選一段", "清除選取", "整趟", "移動配速", "平均心率", "最高心率", "平均功率",
                 "心率飄移（前後半）", "筆活動", "選日期", "TSS 來源", "達標（在綠框裡）", "W′ 剩多少",
                 "詳細數字（", "這次（不到", "平常的中間 50%", "越右越快", "趨勢（直線迴歸）", "平均 ± 1 標準差",
                 "第幾趟", "平均功率（W）", "這次中位", "重複路段", "這次沒有經過重複的路段", "路線 →", "貢獻者",
                 "魯地圖</a>", "個建議", "關掉這個建議")


def test_single_activity_page_has_no_unwrapped_shot_literals():
    files = {"backend/static/wko5_viewer.html": X.scan_html(X.ROOT / "backend/static/wko5_viewer.html")}
    for js in ("segments_card.js", "basemaps.js", "suggestions.js"):
        files[f"backend/static/{js}"] = X.scan_js(X.ROOT / "backend/static" / js)
    for path, fs in files.items():
        left = [(ln, s) for ln, s in fs.unwrapped if any(lit in s for lit in SHOT_LITERALS)]
        assert not left, f"{path}: {left}"


def test_views_api_gives_the_views_english_name(monkeypatch):
    from backend.api import wko5views as W
    monkeypatch.setattr(W, "_wko5_views", lambda parity=True: {})
    with use_locale("en"):
        v = next(x for x in W.list_views() if x["name"] == "單次活動判讀")
    assert v["label"] == "Activity review"            # the crumb / directory text; `name` stays the id
    with use_locale("zh-TW"):
        v = next(x for x in W.list_views() if x["name"] == "單次活動判讀")
    assert "label" not in v


# ---- the review cards (workout_review) in English -----------------------------

def _texts(card: dict) -> list:
    out = [card.get(k) for k in ("label", "text", "sub", "unit", "value")]
    for z in card.get("zones") or []:
        out += [z.get("label"), z.get("name")]
    return out


def test_summary_cards_in_english_easy_run():
    ds = FakeDataset([_run(TODAY, minutes=52, hr=150.0, power=200.0)], TODAY, settings=SETTINGS)
    with use_locale("en"):
        r = R.review(ds, ds.workouts[0], "summary")
    C = {c.get("id"): c for c in r["cards"]}
    assert C["type"]["text"] == "Moderate run" and C["terrain"]["text"] == "Road"     # avg HR > AeT+3
    assert C["intensity"]["label"] == "Intensity" and C["intensity"]["sub"] == "Too high: slow down next time"
    _no_cjk([t for c in r["cards"] for t in _texts(c)], "summary cards")
    # the source language is unchanged
    with use_locale("zh-TW"):
        C = {c.get("id"): c for c in R.review(ds, ds.workouts[0], "summary")["cards"]}
    assert C["intensity"]["label"] == "強度" and C["type"]["text"] == "中強度跑"


def test_summary_cards_in_english_trail_run_climbs():
    ds = FakeDataset([_trail(TODAY)], TODAY, settings=TRAIL_SETTINGS)
    with use_locale("en"):
        r = R.review(ds, ds.workouts[0], "summary")
    C = {c.get("id"): c for c in r["cards"]}
    assert C["climbs"]["label"] == "Climbs" and C["climbs"]["unit"] in ("Segment", "Segments")
    assert C["climbs"]["sub"].startswith("200 m in all")
    assert C["terrain"]["text"] == "Trail"
    _no_cjk([t for c in r["cards"] for t in _texts(c)], "trail summary cards")


def test_tables_and_rows_in_english():
    trail = FakeDataset([_trail(TODAY)], TODAY, settings=TRAIL_SETTINGS)
    with use_locale("en"):
        g = R.review(trail, trail.workouts[0], "grades")
        d = R.review(trail, trail.workouts[0], "durability")
        p = R.review(trail, trail.workouts[0], "pacing")
    _no_cjk([s["name"] for s in g["series"]], "坡度分組 table")
    assert [s["name"] for s in g["series"]][:3] == ["Grade", "Time", "Share"]
    _no_cjk([s["name"] for s in d["series"]] + [s["data"].get("value") for s in d["series"]], "耐久判讀 rows")
    _no_cjk([s["name"] for s in p["series"]], "每 10% 距離 table")


def test_interval_verdict_offer_in_english():
    ds = FakeDataset([_run(TODAY, minutes=52, hr=140.0, power=200.0)], TODAY, settings=SETTINGS)
    with use_locale("en"):
        r = R.review(ds, ds.workouts[0], "interval_verdict")
    assert not r.get("hide")             # an easy run with power and a CP: the 「not an interval session」 card
    assert r["badge"]["text"] == "Not an interval session" and r["badge"]["sub"] == "Easy run"
    _no_cjk([s["name"] for s in r["series"]] + [r.get("empty")], "間歇判讀 rows")
    assert "Easy run" in r["series"][0]["data"]["value"]


def test_interval_card_memo_is_per_language():
    # interval_eval.card_cached memoises in ds.memo: one entry per locale, so an English reader never gets
    # the Chinese text a Chinese reader made first (and the other way round)
    ds = FakeDataset([_run(TODAY, minutes=52, hr=140.0, power=200.0)], TODAY, settings=SETTINGS)
    w = ds.workouts[0]
    with use_locale("zh-TW"):
        zh = IE.card_cached(ds, w)
    with use_locale("en"):
        en = IE.card_cached(ds, w)
    assert zh["why"] == "這次不是間歇課（輕鬆跑，課表也沒有對應的間歇）"
    assert en["why"] == "Not an interval session (Easy run, and the plan has no matching intervals)"
    with use_locale("zh-TW"):
        assert IE.card_cached(ds, w)["why"] == zh["why"]          # the Chinese entry is still there
    assert len([k for k in ds.memo if isinstance(k, tuple) and k and k[0] == "interval_card"]) == 2
