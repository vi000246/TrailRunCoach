"""SP-748: the Chinese the English overview showed while the suggested test was held by 稍後提醒 (SP-514) — the 測試
card's own value, reasons and action, the 5 區 card's rows (the Zone 3 gate's three ways in, the ladders, their
sources) — plus what SP-559 / SP-574 merged in: the Chronic load card's ? source, the Friel / Classic HR zone names
(English in the Chinese page), the settings page's lone ? under 資料同步 and its 功率 section.

Chinese is unchanged. Synthetic data only."""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from backend.engine import plan_prefs as PP
from backend.engine import quality_gate as QG
from backend.i18n import use_locale
from backend.tests.test_z5_gate_viz import BASE, GOOD_BY, TODAY, _ds, _easy, _xu_workout

STATIC = Path(__file__).resolve().parents[1] / "static"
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")
# names of people, races and places stay as written; nothing of the kind is in these synthetic cards


def _cjk(texts) -> list:
    return [t for t in texts if isinstance(t, str) and CJK.search(t)]


def _status():
    from backend.engine.status import Status
    from backend.tests.test_quality_gate import TODAY as QT, _daily, _ds as qds, _plan
    plan = _plan(lthr=165, day="2026-09-01")
    return Status(qds(_daily(), plan), plan, QT, prefs=PP.Prefs()).compute().to_dict()


# ---- the 測試 card -----------------------------------------------------------------------------------------

def test_testing_card_in_english():
    with use_locale("en"):
        st = _status()
    t = next(i for i in st["indicators"] if i["id"] == "testing")
    assert _cjk([t["title"], t["text"], t["verdict"], t["why"], t["action"], t["source"]]) == [], t
    with use_locale("zh-TW"):
        zh = next(i for i in _status()["indicators"] if i["id"] == "testing")
    assert zh["source"] == "教練常見做法（推估）" and zh["why"].startswith("CP 沒測過；LTHR 手動輸入 2026-09-01")


def test_testing_card_words():
    from backend.i18n import translate as T
    assert T("{name} {n} 天前", "en", name="CP", n=1) == "CP 1 day ago"
    assert T("{name} {n} 天前", "en", name="CP", n=12) == "CP 12 days ago"
    assert T("{name} {n} 天前", "zh-TW", name="CP", n=12) == "CP 12 天前"
    assert T("建議（不會自動排課）：{tests}", "en", tests="max-HR test") == "Suggested (not scheduled automatically): max-HR test"


def test_chronic_load_source_in_english():
    with use_locale("en"):
        st = _status()
    f = next(i for i in st["indicators"] if i["id"] == "fitness")
    assert f["source"].startswith("Friel: a chronic load ramp") and not CJK.search(f["source"])


# ---- the 5 區 card ------------------------------------------------------------------------------------------

def _flow_texts(c: dict) -> list:
    out = [c["label"], c["mode_label"]]
    for tr in c["flow"]["tracks"]:
        out += [tr["title"], tr["here"]["next"], tr["here"]["also"]]
        for s in tr["stages"]:
            out += [s["title"], s.get("sub"), s.get("tip"), s.get("unlocks"), s.get("any_label"), s.get("note")]
            for it in (s.get("items") or []) + (s.get("any") or []):
                out += [it["text"], it["value"], it["todo"], it["tip"]]
    return out


def test_z5_card_in_english():
    ds = _ds(_easy(range(2, 40, 2)) + [_xu_workout(TODAY - dt.timedelta(days=10))])
    with use_locale("en"):
        g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
        c = QG.z5_card(g, TODAY)
    left = _cjk(_flow_texts(c))
    assert left == [], left
    z3 = next(t for t in c["flow"]["tracks"] if t["key"] == "z3")
    ladder = [it["text"] for it in z3["stages"][1]["items"]]
    assert ladder[0] == "Aerobic intervals 2×15 min", ladder
    # Chinese unchanged
    g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    zh = QG.z5_card(g, TODAY)
    z3 = next(t for t in zh["flow"]["tracks"] if t["key"] == "z3")
    assert z3["stages"][1]["items"][0]["text"] == "有氧間歇 2×15 分" and zh["label"] == "未確認"


def test_z3_gate_rows_in_english_without_a_test():
    ds = _ds(_easy(range(2, 40, 2)))
    with use_locale("en"):
        g = QG.evaluate(ds, ds.plan, TODAY, PP.Prefs(), GOOD_BY, BASE)
    rows = {t["key"]: t for t in g["z3"]["tests"]}
    assert rows["xu90"]["label"] == "Kuo-Feng Hsu's 90-min test: drift < 10%"
    assert rows["xu90"]["value"] == "— (not done yet: no run of ≥ 90 min in six months)"
    assert _cjk([x for t in rows.values() for x in (t["label"], t["value"], t["need"])]) == []


# ---- the HR zone names ---------------------------------------------------------------------------------------

def test_hr_zone_names_follow_the_language(tmp_path):
    from backend.engine.zones import SYSTEMS
    from backend.i18n import translate as T
    friel = [n for _z, n, _lo, _hi in SYSTEMS["frielhr"]["zones"]]
    classic = [n for _z, n, _lo, _hi in SYSTEMS["classichr"]["zones"]]
    assert friel == ["恢復", "有氧", "節奏", "次閾值", "超閾值", "有氧能力", "無氧能力"]
    assert [T(n, "en") for n in friel][:4] == ["Recovery", "Aerobic", "Tempo", "SubThreshold"]
    assert [T(n, "en") for n in classic] == ["Active Recovery", "Endurance", "Tempo", "Threshold", "VO2max"]


# ---- the settings page ---------------------------------------------------------------------------------------

def _settings() -> str:
    return (STATIC / "settings.html").read_text("utf-8")


def test_settings_basis_tab_is_translated():
    """SP-955: the 功率 section (使用功率, 手錶推估功率) is gone; its place is 訓練依據 (#basis), all of it through
    the catalogs, its script's settings.basis.* keys in both languages."""
    html = _settings()
    assert '<section id="power">' not in html
    sec = html[html.index('<section id="basis">'):html.index('<section id="sync">')]
    from backend.scripts import i18n_extract as X
    left = [m for _l, m in X.scan_html(STATIC / "x.html", sec).unwrapped]
    assert left == [], left
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "settings.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "settings.json").read_text("utf-8"))
    keys = {k for k in re.findall(r'settings\.(basis\.[\w.]+)', html) if not k.endswith(("_", "."))}
    assert {"basis.equip_h", "basis.now_h", "basis.st_sport", "basis.manual"} <= keys
    keys |= {f"basis.st_{s}" for s in ("power", "stale", "none")} | {f"basis.aetp_{k}" for k in ("auto", "xu90", "ua60", "ua40", "evoke60", "friel")}
    assert all(zh.get(k) and en.get(k) and not CJK.search(en[k]) for k in keys), [k for k in keys if not (zh.get(k) and en.get(k))]


def test_power_source_how_is_translated():
    from backend.engine import athlete_profile as AP
    from backend.i18n import translate as T
    s, how = AP.effective_power_source({"power_source": "stryd"}, None)
    assert (s, how) == ("stryd", "設定") and T(how, "en") == "your setting"


def test_settings_tl_rpe_rows_keep_their_question_mark_beside_them():
    html = _settings()
    assert '<div class="row" hidden><span class="meta" id="s-tl"></span>' in html
    assert '<div class="row" hidden><span class="meta" id="s-rpe"></span>' in html
    assert "#s-tl, #s-rpe { flex: 1 1 0; min-width: 0; }" in html
    assert html.count("el.parentElement.hidden") == 2
