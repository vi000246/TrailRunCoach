"""徐國峰 90-minute test: pace or power, never an HR cap (SP-274;
lthr-low-confidence-testing.md §6.1 第 1、5 點). Synthetic data only."""
import pytest

from backend.engine import aet_test as AT
from backend.engine import e_pace as EP
from backend.engine import threshold_confidence as TC
from backend.engine import workout_steps as WS
from backend.i18n import use_locale
from backend.sync import coros_workouts as CW

import datetime as dt

TODAY = dt.date(2026, 10, 6)
BASE = {"cp": 250.0, "lthr": 165.0, "aet": 145.0}
E10K = EP.of_race({"distance_m": 10000, "time_s": 2700, "date": "2026-09-01"}, TODAY)   # E 5:32–6:06


def _th(**k):
    return {**BASE, **k}


def _sources():
    return {"pace": _th(e_pace=E10K, cp_measured=True),           # E pace first, even with a tested CP
            "power": _th(cp_measured=True),
            "talk": _th(cp_measured=False)}                       # a CP that isn't tested doesn't count


def _work(steps):
    return next(st for st in steps if st.kind == CW.EX_TRAIN)


@pytest.mark.parametrize("basis", ["pace", "power", "talk"])
def test_session_text(basis):
    s = AT.session(_sources()[basis], 140.0, 190.0, None, "xu90")
    assert s["xu_basis"] == basis
    assert "心率 1 區" not in s["target"] and "≤ AeT" not in s["target"]
    assert "不設心率上限" in s["target"]
    want = {"pace": "配速固定 5:39–5:59 /km（E 配速 5:49 ±3%，推估），不要調",
            "power": "功率固定 188–200 W（75–80% CP，Palladino 1C），不要調",
            "talk": "能講完整句子的配速，固定不要調"}[basis]
    assert s["target"].startswith(want)
    if basis == "pace":
        assert "E 配速 5:32–6:06 /km（10 K 45:00，VDOT 45.3）" in s["detail"]
    if basis == "talk":
        assert "講話測試" in s["detail"]


@pytest.mark.parametrize("basis", ["pace", "power", "talk"])
def test_coros_steps(basis):
    th = _sources()[basis]
    s = AT.session(th, 140.0, 190.0, None, "xu90")
    steps = CW.session_steps(s, CW.Thresholds.of(th))
    assert steps[0].intensity == CW.easy_hr(CW.Thresholds.of(th))            # warm-up: the easy-run cap
    w = _work(steps)
    assert w.seconds == 80 * 60
    if basis == "pace":
        assert w.intensity == ("pace", 339, 359) and w.name == "固定 E 配速，不要調"
        ex = CW.build_program("x", steps, CW.Thresholds.of(th))
        main = [e for e in ex["exercises"] if e.get("intensityType") == CW.INT_PACE]
        assert main and (main[0]["intensityValue"], main[0]["intensityValueExtend"]) == (339, 359)
    elif basis == "power":
        assert w.intensity == ("power", 188, 200) and w.name == "固定功率 75–80% CP，不要調"
    else:
        assert w.intensity is None and w.name == "能講完整句子的配速，固定不要調"


@pytest.mark.parametrize("basis", ["pace", "power", "talk"])
def test_editor_steps(basis):
    th = _sources()[basis]
    s = AT.session(th, 140.0, 190.0, None, "xu90")
    d = WS.derive(s, th)
    warm, work = d["items"][0], d["items"][1]
    assert warm["target"] == WS.EASY
    if basis == "pace":
        assert work["target"] == {"type": "pace", "mode": "abs", "lo": 339, "hi": 359}
        r = WS.resolve(work, WS.Ctx.of(th))
        assert r.type == "pace" and r.text == "5:39–5:59 /km"
    elif basis == "power":
        assert work["target"] == {"type": "power", "mode": "abs", "lo": 188, "hi": 200}
    else:
        assert work["target"] == WS.OPEN and work["note"] == "能講完整句子的配速，固定不要調"
        assert WS.resolve(work, WS.Ctx.of(th)).type == "none"


def test_an_old_row_with_the_hr_text_has_no_hr_cap_any_more():
    old = {"kind": "test", "title": "AeT 飄移測試 徐國峰 90 分", "minutes": 90, "protocol": "aet",
           "target": "配速固定在 E 配速，不要調；心率 1 區（≤ AeT 145）", "detail": "暖身 10 分，接著測試 80 分"}
    assert _work(CW.session_steps(old, CW.Thresholds.of(BASE))).intensity is None


def test_stale_race_still_used_with_the_note():
    old = EP.of_race({"distance_m": 10000, "time_s": 2700, "date": "2026-01-01"}, TODAY)
    s = AT.session(_th(e_pace=old), 140.0, 190.0, None, "xu90")
    assert s["xu_basis"] == "pace" and "舊了" in s["detail"]


def test_english():
    with use_locale("en"):
        s = AT.session(_sources()["pace"], 140.0, 190.0, None, "xu90")
        assert s["target"].startswith("Hold pace at 5:39–5:59 /km (E pace 5:49 ±3%, estimate), don't adjust")
        steps = CW.session_steps(s, CW.Thresholds.of(BASE))
        assert _work(steps).intensity == ("pace", 339, 359)                   # the numbers parse in English too
        assert _work(steps).name == "Hold E pace, don't adjust"
        assert AT.session(_sources()["talk"], None, None, None, "xu90")["target"].startswith(
            "A pace at which you can speak in full sentences")


# ---- no LTHR badge on test sessions (§6.1 第 5 點) -----------------------------------------

WARN = {"lthr": {"low": True, "text": "LTHR 可信度低：心率目標可能不準（x）"}}
HR_ITEMS = [{"kind": "work", "target": {"type": "hr", "mode": "pct", "lo": 0.95, "hi": 1.0}}]


@pytest.mark.parametrize("s", [
    AT.session(BASE, 140.0, 190.0, None, "xu90"), AT.session(BASE, 140.0, 190.0, None, "ua60"),
    AT.session(BASE, 140.0, 190.0, None, "evoke60"),
    {"kind": "test", "title": "Friel 30′ 閾值心率測試"}, {"kind": "test", "title": "最大心率測試：3 趟上坡、最後一趟全力"},
    {"kind": "quality", "title": "LTHR 測試（30 分鐘獨跑）"}])
def test_test_sessions_have_no_lthr_badge(s):
    assert TC.is_test_session(s)
    assert TC.session_warn(WARN, s, HR_ITEMS) is None


def test_an_interval_session_keeps_the_badge():
    assert TC.session_warn(WARN, {"kind": "quality", "title": "閾值 3×8 分"}, HR_ITEMS) == WARN
