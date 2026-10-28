"""SP-810: 圖表分析 in English — the single-activity page's hover texts (the session-type stimulus, the AeT
drift reasons, the threshold sources, the zone models' sources), the season (trend) mode's own words
(區間時數 by period / by week, the 訓練目標 card, the day calendar, the gauges) and the capability page's
climbing VAM:HR card (static/climb_vam.js, panels/climb_vam.py). Chinese is unchanged.

Synthetic data only (fit_builder runs in tmp_path, hand-made dicts): no WKO5 folder, no real home."""
from __future__ import annotations

import datetime as dt
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.engine import drift_agg as DA
from backend.engine import session_stimulus as SS
from backend.engine import zone_events as ZE
from backend.engine import zones as Z
from backend.engine.planning import Plan, Threshold
from backend.engine.wko5expr.dataset import date_to_day
from backend.i18n import use_locale
from backend.tests.test_charts_coros_source import _ds

STATIC = Path(__file__).resolve().parents[1] / "static"
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")
TODAY = dt.date(2026, 9, 30)
UTC = timezone.utc

FILES = ("backend/static/charts.html", "backend/static/painpicker.js", "backend/static/climb_vam.js",
         "backend/static/sourcechip.js", "backend/static/segments_card.js", "backend/static/seg_profile.js",
         "backend/static/readiness.js", "backend/engine/panels/activity_charts.py", "backend/engine/climb_vam.py",
         "backend/engine/panels/climb_vam.py", "backend/engine/drift_agg.py", "backend/engine/session_stimulus.py",
         "backend/engine/zones.py", "backend/engine/zone_events.py")


def _strings(x) -> list:
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    if isinstance(x, list):
        return [s for v in x for s in _strings(v)]
    return [x] if isinstance(x, str) else []


def _cjk(x) -> list:
    return [s for s in _strings(x) if CJK.search(s)]


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


# ---- the page and its scripts -------------------------------------------------------------------------

def test_the_files_have_no_unwrapped_chinese():
    from backend.scripts import i18n_extract as X
    scans = X.scan_repo()
    for f in FILES:
        assert scans[f].unwrapped == [], (f, scans[f].unwrapped[:5])


def test_the_new_keys_have_english_and_chinese_unchanged():
    zh, en = _cat("zh-TW", "viewer"), _cat("en", "viewer")
    keys = [k for k in zh if k.split(".")[0] in ("pz", "tg", "cal", "g") or k in ("paren", "enum_sep")]
    assert len(keys) > 60 and all(en.get(k) and not CJK.search(json.dumps(en[k], ensure_ascii=False)) for k in keys)
    assert zh["pz.thr_same"] == "門檻：{v}（{n} 個來源，見下方）" and zh["cal.no"] == "{d}：沒練"
    assert en["pz.days"] == " ({n} {n|day|days})" and en["paren"] == " ({x})"
    czh, cen = _cat("zh-TW", "common"), _cat("en", "common")
    mine = [k for k in czh if k.split(".")[0] in ("climb", "painpicker", "srcchip")]
    assert len(mine) > 50 and all(cen.get(k) and not CJK.search(cen[k]) for k in mine)
    assert czh["painpicker.level.3"] == "中斷" and cen["painpicker.level.3"] == "Stopped"
    assert czh["climb.sum"] == "<b>{n}</b> 段（走 {w}、跑 {r}）・<b>{a}</b> 次"


# code, not shown: the bundled views' names used as ids, a regex on the server's msgid, ECharts' zh-TW month format
_CODE = ('const CUSTOM_ORDER = ["我的訓練", "周期化訓練"];', '"{M}月"')


def test_the_english_chart_page_shows_no_chinese():
    from backend.i18n.pages import render
    html = render("charts", "en")
    body = re.sub(r"<script>window.__I18N__.*?</script>|<style>.*?</style>|<!--.*?-->", "", html, flags=re.S)
    body = re.sub(r"(?m)//[^\n]*$", "", body)
    for c in _CODE:
        assert c in body
        body = body.replace(c, "")
    assert CJK.findall(body) == []
    zh = render("charts", "zh-TW")
    assert '<title data-i18n="viewer.page_title">圖表</title>' in zh


def _node():
    import shutil
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node not installed")
def test_the_season_texts_read_in_english_and_chinese():
    """viewer.pz.* / cal.* through the page's own t() (i18n.js): plurals and brackets."""
    import subprocess
    js = (STATIC / "i18n" / "i18n.js").read_text("utf-8")
    out = {}
    for loc in ("en", "zh-TW"):
        cat = {f"viewer.{k}": v for k, v in _cat(loc, "viewer").items()}
        prog = ("global.window = {__I18N__: {locale: %s, catalog: %s}, localStorage: null};"
                "global.document = {documentElement: {getAttribute: () => %s}, cookie: ''};" % (
                    json.dumps(loc), json.dumps(cat, ensure_ascii=False), json.dumps(loc))
                + js + ";console.log(JSON.stringify([window.t('viewer.pz.days', {n: 1}), window.t('viewer.pz.days', {n: 28}),"
                "window.t('viewer.pz.thr_same', {v: '160 bpm', n: 3}), window.t('viewer.pz.used', {used: 4, n: 5}),"
                "window.t('viewer.cal.sum', {a: 1, b: 3, c: 9, d: '0.7'})]))")
        r = subprocess.run([_node(), "-e", prog], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        out[loc] = json.loads(r.stdout)
    assert out["en"][:4] == [" (1 day)", " (28 days)", "Threshold: 160 bpm (3 sources, see below)", "4/5 activities"]
    assert out["zh-TW"][:4] == ["（1 天）", "（28 天）", "門檻：160 bpm（3 個來源，見下方）", "4／5 次活動"]
    assert out["zh-TW"][4] == "本週 <b>1</b> 次 · 近 4 週 <b>3</b> 次 · 這段期間 <b>9</b> 次（平均每週 0.7 次）"


# ---- the single activity's hover texts ------------------------------------------------------------------

def test_the_stimulus_verdict_and_sources_in_english():
    stim = {"t_vo2_power_s": 300.0, "z3_s": 900.0}
    with use_locale("en"):
        v = SS.verdict(stim, "road")
        assert v["why"] == "Equivalent T@VO2max 5.0 min ≥ 4 min"
        assert SS.verdict({"z3_s": 700.0}, "road")["why"].startswith("Zone 3 12 min ≥ 10 min; equivalent")
        assert SS.verdict({}, "road", easy_hr=True)["why"] == "Average HR ≤ easy-run cap + 3"
        srcs = [SS.SRC[k] for k in ("t_vo2", "z5_min", "bouts", "lag", "hr", "z3", "trail", "hrpeak")]
    assert _cjk(srcs) == [] and "Baiyue" in srcs[6]
    # Chinese unchanged
    assert SS.verdict(stim, "road")["why"] == "等效 T@VO2max 5.0 分 ≥ 4 分"
    assert SS.SRC["z3"].startswith("Zone 3：功率 ≥ 88% CP")


def test_the_aet_drift_texts_in_english():
    agg = {"mean": 0.031, "se": 0.018, "n": 6}
    with use_locale("en"):
        assert DA.text(agg) == "3.1% ± 1.8 pp (average of 6 runs)"
        assert DA.bound_label(152) == "AeT ≥ 152 bpm (lower bound, estimate)"
        assert DA.beta_text({"src": "user", "beta": 0.8}) == "β 0.80 bpm/°C (set by you in Advanced settings)"
        b = {"src": "personal", "beta": 0.85, "n": 24, "personal": 0.7, "w": 0.55}
        note = DA.heat_note([{"band": "warm", "heat_bpm": 1.5}], b)
        assert note.startswith("Includes 1 run at 25–28 °C") and _cjk([note, DA.beta_text(b)]) == []
    assert DA.text(agg) == "3.1% ± 1.8 pp（6 次平均）"
    assert DA.bound_label(152) == "AeT ≥ 152 bpm（下限，推估）"


def test_the_zone_models_sources_in_english():
    from backend.engine.panels import activity_charts as AC
    with use_locale("en"):
        srcs = [AC._model_source(m) for k in ("hr", "power") for m in AC.MODELS[k]]
        assert AC._model_source(next(m for m in AC.MODELS["hr"] if m["id"] == "rqhrr")).endswith(
            "; T zone 84–88% HRR, see also runningquotient.com/article/single/52")
    assert _cjk(srcs) == []
    seiler = next(m for m in AC.MODELS["hr"] if m["id"] == "seiler3")
    assert AC._model_source(seiler).startswith("Seiler 三區模型")


def test_the_aet_error_badge_in_english():
    with use_locale("en"):
        tips = [Z.aet_uncertainty(k, se)["tip"] for k, se in (("regression", 4.2), ("estimate", None), ("friel", None))]
    assert _cjk(tips) == [] and tips[2].startswith("Estimated error about ±16 bpm. 0.89 × LTHR is the Friel")
    assert "0.89 × LTHR 是 Friel 區間的慣例" in Z.aet_uncertainty("friel")["tip"]


# ---- the zones / targets cards (single activity and season) ------------------------------------------------

def test_the_zone_table_and_targets_in_english(tmp_path):
    plan = Plan(thresholds=[Threshold(date="2026-09-01", cp=200.0, lthr=160.0)])
    runs = [dict(start=datetime(2026, 9, d, 0, tzinfo=UTC), seconds=2400, hr=150, power=190, stryd=True)
            for d in (5, 12, 19)]
    ds = _ds(tmp_path, runs, plan)
    end = int(date_to_day(TODAY))
    with use_locale("en"):
        z = Z.zone_table(ds, "frielhr", end)
        p = Z.zone_table(ds, "palladino", end)
        tt = Z.shown_targets(Z.training_targets(ds, end))
        choices = Z.hr_choices()
    assert z["title"] == "Friel heart-rate zones (running)" and p["threshold_source"] == "your test 2026-09-01"
    assert _cjk([z["title"], z["threshold_source"], p["title"], choices]) == []
    rows = {r["id"]: r for r in tt["rows"]}
    assert rows["trail"]["primary"] == "Heart rate" and rows["downhill"]["primary"] == "Feel"
    texts = [tt[k] for k in ("cp_source", "lthr_source", "aet_source", "terrain_note")]
    texts += [r[k] for r in tt["rows"] for k in ("name", "primary", "example", "source")]
    assert _cjk(texts) == [], _cjk(texts)
    # the code-facing result stays in the source language (overview compares 「心率」)
    raw = Z.training_targets(ds, end)
    assert {r["id"]: r for r in raw["rows"]}["trail"]["primary"] == "心率"
    assert Z.zone_table(ds, "frielhr", end)["title"] == "Friel 心率區間（跑步）"


# ---- the retest suggestions (zone_events) ------------------------------------------------------------------

def test_the_retest_suggestion_texts_in_english():
    sh = {"heat": {"beta": 0.22, "beta_se": 0.04, "beta_source": "fitted", "confidence": "medium",
                   "recent_sources": {"route_weather": 5, "watch": 1}}}
    with use_locale("en"):
        sg = ZE._suggestion("break", ["cp", "tt30", "aet"], "t", "x", TODAY)
        note = ZE.heat_note(sh)
        plan = Plan(thresholds=[Threshold(date="2026-09-25", cp=210.0)])
        ev = ZE.applied_events(plan, TODAY)
        r = ZE.hr_shift([], TODAY)
    assert _cjk(sg["conditions"] + [sg["caveat"], sg["source"], note]) == []
    assert sg["conditions"][0].startswith("CP test: < 25 °C")
    assert note.startswith("Heat-index corrected (β 0.22 ± 0.04 bpm/Hadley, fitted to your data; weather: ")
    assert ev and ev[0]["text"] == "CP 210 W (Test 2026-09-25): zones recalculated from 2026-09-25"
    assert r["reason"] and _cjk(r["reason"]) == []          # (its heat source is engine/heat_calib's)
    # Chinese unchanged
    assert ZE._suggestion("x", ["aet"], "t", "x", TODAY)["source"] == "門檻在事件後重測：CP 變化、停跑、季節轉換（Friel；推估）"
    assert ZE.applied_events(plan, TODAY)[0]["text"] == "CP 210 W（測試 2026-09-25）：從 2026-09-25 起區間已重算"


# ---- the climbing VAM:HR card ---------------------------------------------------------------------------

def test_the_climb_card_in_english_and_its_mode_code():
    from backend.engine import climb_vam as CV
    from backend.engine.panels import climb_vam as PCV
    from backend.tests.wko5_fakes import FakeDataset
    ds = FakeDataset([], TODAY)
    with use_locale("en"):
        out = PCV.compute(ds, 0.0, float(date_to_day(TODAY)), {}, route_index={"routes": []}, weather={}, names={})
        rev = PCV.route_map({"routes": [{"id": "r1", "name": "Wulai", "members": ["a", "b"],
                                         "dirs": {"b": "reverse"}}]}, {})[1]
    assert out["empty"] == "No trail runs or hikes in this period"
    assert rev["r1~rev"] == "Wulai (reverse)"
    # the stored word stays (disk-cached series); code reads the key
    assert CV.MODE_WORD == {"run": "跑", "walk": "走"}
    assert [CV.mode_key({"run_share": x}) for x in (None, 0.2, 0.5)] == [None, "walk", "run"]
    assert PCV.compute(ds, 0.0, float(date_to_day(TODAY)), {}, route_index={"routes": []}, weather={},
                       names={})["empty"] == "這段期間沒有越野跑或登山健行"


# ---- the trend charts' names and descriptions (views/i18n/en.json) ------------------------------------------

def test_the_bundled_trend_charts_in_english():
    """Series names (the banded charts' bands too), variant labels and descriptions, the reference line and the
    power note of views/training.json and views/periodization.json (the axes' unit ids are wko5expr/units')."""
    from backend.engine.wko5expr.viewi18n import sidecar, translate_view
    root = Path(__file__).resolve().parents[2] / "views"
    for stem in ("training", "periodization"):
        v = json.loads((root / f"{stem}.json").read_text("utf-8"))
        t = translate_view(v, sidecar("en")[stem])
        left = []
        for d in t["dashboards"]:
            left += [d["title"], d.get("description") or ""]
            for c in d["charts"]:
                left += [c.get("title") or "", c.get("description") or ""]
                left += [s.get("name") or "" for s in c.get("series", [])]
                for var in c.get("variants", []) or []:
                    left += [var.get("label") or "", var.get("description") or ""]
                    left += [s.get("name") or "" for s in var.get("series", [])]
                left += [((c.get("zoned") or {}).get("ref") or {}).get("label") or "",
                         (c.get("basis") or {}).get("power_note") or ""]
        assert _cjk(left) == [], (stem, _cjk(left)[:5])
    # the view file itself stays Chinese
    tr = json.loads((root / "training.json").read_text("utf-8"))
    assert any(s.get("name") == "安全 < 5" for d in tr["dashboards"] for c in d["charts"] for s in c.get("series", []))
