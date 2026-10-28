"""SP-810: the 間歇門檻 in English — the overview's gate card (quality_gate.indicator: verdict, details,
the two Zone 5 unlock routes, source), the 強度課表解鎖條件 card's texts (z3 / z5 gates, base_check's Zone 5
states), the 課表偏好 selector's hover (option_texts); the suggestion box (the race simulation of
specific_phase, the max-HR / LTHR test of threshold_confidence, the day notes); the season view's CUSTOM
count unit (wko5expr/units). Chinese is unchanged. Synthetic data only."""
from __future__ import annotations

import datetime as dt
import re

import pytest

from backend.engine import quality_gate as QG
from backend.i18n import translate as T
from backend.i18n import use_locale
from backend.scripts import i18n_extract as X
from backend.scripts.i18n_extract import CJK
from backend.tests import test_quality_gate as TQ

FILES = ("backend/engine/quality_gate.py", "backend/engine/base_check.py", "backend/engine/specific_phase.py",
         "backend/engine/wko5expr/units.py", "backend/engine/threshold_confidence.py")
# literals that are data, not text: the ladder's stored titles, the stored notes the code reads, a regex,
# the WKO5 CUSTOM unit names matched as keys
DATA = {"VO2max 間歇 4×4 分", "爬坡間歇 4×3 分", "短間歇 5×1 分", "短間歇 6×1 分", "間歇 5×3 分", "閾值 3×10 分",
        "閾值 4×8 分", "閾值下 3×8 分", "閾值下 4×8 分", "恢復週 fartlek 4×1 分", "Zone 3 間歇", "3 區（有氧間歇）",
        "5 區（VO2max 間歇）", "自動估算", "飄移測試", "推估", "3 區還沒解鎖：", "Zone 3 間歇 {…}×{…} 分", "心率",
        "次", "公里", "賽事計算器預測的完賽時間", "平路或緩坡", "挑每公里爬升", "有山路就走山路", "全程心率壓在",
        "VO2max 間歇", "上坡", "賽事模擬｜{…}", " 第 {…} 天"}
NAME = re.compile(r"“[^”]*”|\"[^\"]*\"")          # a race's name (the user's own) inside a text


def _left(*texts) -> list:
    return [t for t in texts if isinstance(t, str) and CJK.search(NAME.sub("", t))]


def test_the_files_wrap_their_texts_and_every_msgid_has_english():
    scans = X.scan_repo()
    en = X.load_json(X.LOCALES / "en.json", {})
    for f in FILES:
        left = [(ln, s) for ln, s in scans[f].unwrapped if s not in DATA and not s.startswith("飄移測試|")]
        assert left == [], (f, left[:5])
        missing = sorted({m for _l, m in scans[f].msgids if not en.get(m)})
        assert not missing, f"{f}: no en for {missing[:5]}"


# ---- the gate card (status.i_gate) ----------------------------------------------------------------------

CASES = {  # name: _gate kwargs (test_quality_gate's synthetic athlete)
    "guardrails": {},
    "ua_open": {"plan": TQ._plan(aethr=150, lthr=160)},
    "ua_shut": {"plan": TQ._plan(aethr=140, lthr=165)},
    "z3_closed": {"z3_weeks": 1},
    "friel_missing": {"mode": "friel_drift"},
    "xu_missing": {"mode": "xu_drift"},
    "plateau": {"mode": "plateau"},
    "none": {"mode": "none"},
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_gate_card_in_english(name):
    with use_locale("en"):
        g = TQ._gate(**CASES[name])
        card = QG.indicator(g)
        z5 = QG.z5_card(g, TQ.TODAY)
    assert _left(*(card[k] for k in ("text", "verdict", "why", "action", "source"))) == [], card
    zt = [z5.get("text"), z5.get("reason"), z5.get("label"), (z5.get("z5_gate") or {}).get("text"),
          (z5.get("z3_gate") or {}).get("text"), (z5.get("z3_gate") or {}).get("reason"), (z5.get("z3") or {}).get("src"),
          (z5.get("next") or {}).get("text")]
    zt += [t.get(k) for t in (z5.get("base") or {}).get("tests") or [] for k in ("label", "value", "src")]
    zt += [t.get(k) for t in (z5.get("z3_gate") or {}).get("tests") or [] for k in ("label", "value", "src")]
    assert _left(*zt) == [], zt


def test_the_gate_card_chinese_unchanged():
    g = TQ._gate()
    card = QG.indicator(g)
    assert card["text"] == "護欄" and card["why"].startswith("模式：自動（不設門檻）；AeT 沒有實測（用 0.89×LTHR 估）")
    assert card["verdict"].startswith("沒有 AeT 實測：照 80/20 原則每週 1 次間歇（")
    assert card["source"] == "Seiler 2010；Seiler & Tønnessen 2009（整個週期都有少量高強度，每週 1–3 次）"
    shut = QG.indicator(TQ._gate(z3_weeks=1))
    assert shut["text"] == "3 區未開" and shut["action"].startswith("規律跑（每週 ≥ 3 次、別連續 7 天沒跑）")
    z3 = TQ._gate(z3_weeks=1)["z3"]
    assert z3["reason"].startswith("3 區還沒解鎖：連續 1/4 週每週跑 ≥ 3 次") and z3["text"] == f"Zone 3：未解鎖（{z3['why']}）"


def test_the_zone_3_text_is_not_said_twice_in_english():
    with use_locale("en"):
        z3 = TQ._gate(z3_weeks=1)["z3"]
    assert z3["reason"].startswith("Zone 3 not unlocked yet: 1/4 weeks in a row")
    assert z3["text"].startswith("Zone 3: not unlocked (1/4 weeks") and "not unlocked yet" not in z3["text"]


def test_the_selector_hover_in_english():
    with use_locale("en"):
        tips = QG.option_texts()
    for m, v in tips.items():
        assert not CJK.search(v["tip"]), (m, v["tip"][:200])
        assert "\nSource: " in v["tip"] and "\n\nWhat to do: " in v["tip"]
    assert "4 weeks of steady training in a row (≥ 3 runs every week" in tips["auto"]["tip"]
    zh = QG.option_texts()
    assert zh["auto"]["label"] == "自動" and zh["auto"]["tip"].startswith("自動\n來源：台灣教練、徐國峰部落格")
    assert "連續 4 週規律訓練（每週 ≥ 3 次、沒有 ≥ 7 天沒跑；" in zh["auto"]["tip"]


def test_base_check_zone_5_states():
    assert T("Zone 5：暫停（{why}）", "en", why="x") == "Zone 5: paused (x)"
    assert T("恢復期後先完成 {need} 堂 3 區（已 {n} 堂；台灣教練：先 3 區後 5 區，堂數推估）", "en", need=1, n=0) == \
        "After the Recovery period, 1 Zone 3 session first (0 done; a Taiwanese coach: Zone 3 before Zone 5, the number an estimate)"
    assert T("已經 {n} 天沒跑（≥ {relock} 天要重新累積；{tag}）", "en", n=1, relock=21, tag="t") == \
        "no run for 1 day (≥ 21 days starts the count again; t)"


# ---- the suggestion box -------------------------------------------------------------------------------

def test_the_race_simulation_reads_back_in_english():
    from backend.engine import specific_phase as SP
    from backend.engine import stored_text as ST
    race = {"id": 1, "name": "測試越野 50K", "start": "2026-11-30", "kind": "race", "days": 1, "hours": 7.5, "km": 50.0,
            "climb_m": 2600.0, "descent_m": 2600.0, "cc": 100.0, "goal": 100.0,
            "day": {"hours": 7.5, "km": 50.0, "climb_m": 2600.0, "descent_m": 2600.0}, "features": None}
    info = {"race": race, "active": True, "sport": "trail"}
    sg = SP.sim_suggestion(info, dt.date(2026, 10, 19), 120.0, aet=145.0)           # built in zh-TW (the inputs)
    assert sg is not None and sg["help"].startswith("Koop、Uphill Athlete：賽前在像比賽的地形演練裝備、補給和配速，問題在比賽前就發現。")
    assert sg["help"].endswith("選一天按「排入」才會進課表；不排也不影響其他課。時間點與目標比例為推估。")
    for k in ("title", "help", "reason"):
        shown = ST.shown(sg[k], "en")
        assert not CJK.search(shown.replace(race["name"], "")), (k, shown)
    with use_locale("en"):
        opts = SP.sim_day_options(sg, dt.date(2026, 10, 19))
    assert opts and {o["note"] for o in opts} <= {"weekend", "weekday"} and opts[0]["note"] == "weekend"
    assert {o["note"] for o in SP.sim_day_options(sg, dt.date(2026, 10, 19))} <= {"週末", "平日"}


def test_the_max_hr_test_conditions_read_back_in_english():
    from backend.engine import stored_text as ST
    from backend.engine import threshold_confidence as TC
    from backend.i18n import M_
    notes = [T("前 {h} 小時不要有長跑、強度課或比賽", "zh-TW", h=TC.HARD_GAP_H)]
    c = M_("{test}：{how}", test=M_(TC.TEST_TITLE["hrmax"]), how="；".join(notes))
    assert c == "最大心率測試：前 48 小時不要有長跑、強度課或比賽"
    assert ST.shown(c, "en") == "max-HR test: no long run, quality session or race in the 48 hours before"
    # the row's title, built again at read time (the inputs keep the zh-TW one)
    assert TC.test_title(["hrmax"]) == "建議：做最大心率測試" and TC.test_title(["tt30"]) == "建議：做 LTHR 測試（30 分鐘獨跑）"
    with use_locale("en"):
        assert TC.test_title(["hrmax", "tt30"]) == "Suggested: max-HR test and LTHR test (30-min solo run)"
    # a date read back whole (「10/19」, not 「10 /19」)
    assert ST.shown("賽前第 6、4 週（10/19、11/2 那兩週）", "en") == "6 and 4 weeks before the race (the weeks of 10/19, 11/2)"


def test_the_season_view_count_unit():
    from backend.engine.wko5expr import units as U
    with use_locale("en"):
        assert U.meta("CUSTOM次")["label"] == "times" and U.meta("CUSTOMminutes")["label"] == "min"
        assert U.meta("DATE")["label"] == "date"
    assert U.meta("CUSTOM次")["label"] == "次" and U.meta("CUSTOMminutes")["label"] == "分鐘"


# the demo athlete and its test client: test_en_overview_sp810's fixtures
from backend.tests.test_en_overview_sp810 import built, client  # noqa: E402,F401


def test_the_box_and_the_gate_card_in_english(client):
    box = client.get("/api/v1/overview/plan/suggestions?lang=en").json()
    texts = []
    for r in box["suggestions"]:
        name = r.get("event") or "\0"                      # the race's name, the user's own, stays as written
        texts += [(r.get(k) or "").replace(name, "") for k in ("title", "reason", "help", "src")]
        texts += [o.get("note") for o in r.get("options") or []]
        texts += [t.get("label") for t in r.get("tests") or []]
    assert _left(*texts) == [], _left(*texts)
    st = client.get("/api/v1/overview/status?lang=en").json()
    gate = next(i for i in st["indicators"] if i["id"] == "gate")
    assert _left(*(gate[k] for k in ("title", "text", "verdict", "why", "action", "source"))) == []
    zh = client.get("/api/v1/overview/plan/suggestions?lang=zh-TW").json()
    assert all(CJK.search(r["title"]) for r in zh["suggestions"] if r.get("title"))
