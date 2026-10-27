"""SP-949: the AeT test's start — the start HR chained to the last test, the fixed power / pace from
the athlete's own HR → power / pace relation on recent flat easy runs (① power / ② ③ pace), the
fallbacks (CP × 0.75, the E pace, the talk test) and the COROS steps. Synthetic data only."""
import datetime as dt

import numpy as np
import pytest

from backend.engine import aet_test as AT
from backend.engine import e_pace as EP
from backend.engine import target_policy as TP
from backend.engine import workout_steps as WS
from backend.sync import coros_workouts as CW
from backend.tests.wko5_fakes import FakeDataset, FakeWorkout

TODAY = dt.date(2026, 10, 27)
TH = {"cp": 195.0, "lthr": 168.0, "aet": 150.0}


def power_of(hr):          # the synthetic athlete: 136 W at 149 bpm, +2 W / bpm
    return 136.0 + 2.0 * (hr - 149.0)


def kmh_of(hr):            # 8:40 /km (6.923 km/h) at 148 bpm, +0.12 km/h / bpm
    return 3600.0 / 520.0 + 0.12 * (hr - 148.0)


def _run(day: dt.date, hr_lo=140.0, hr_hi=152.0, minutes=50, grade=0.0, power=True, elev=True, seed=0):
    """One synthetic road run: HR ramps hr_lo → hr_hi after a 10-min warm-up, power and speed follow
    the athlete's line; `grade` a constant slope (fraction)."""
    rng = np.random.default_rng(seed)
    n = minutes * 60
    t = np.arange(n, dtype=float)
    hr = np.where(t < 600, 120 + (hr_lo - 120) * t / 600, hr_lo + (hr_hi - hr_lo) * (t - 600) / max(1, n - 600))
    hr = hr + 0.6 * np.sin(t / 7.0) + rng.normal(0, 0.3, n)
    v = kmh_of(hr)
    d = np.cumsum(v / 3600.0)
    ch = {"elapsedtime": t.tolist(), "heartrate": hr.tolist(), "speed": v.tolist(), "elapseddistance": d.tolist()}
    if elev:
        ch["elevation"] = (10.0 + d * 1000.0 * grade).tolist()
    if power:
        ch["power"] = (power_of(hr) + rng.normal(0, 1.0, n)).tolist()
    start = dt.datetime.combine(day, dt.time(6, 0))
    return FakeWorkout(start=start, sport="run", metrics={"duration": float(n), "distance": float(d[-1])},
                       channels=ch, title="輕鬆跑")


def _ds(runs):
    return FakeDataset(runs, TODAY)


def _flat_runs(k=4, **kw):
    return [_run(TODAY - dt.timedelta(days=3 + 4 * i), seed=i, **kw) for i in range(k)]


# ---- the start HR chained to the last test --------------------------------------------------------

@pytest.mark.parametrize("band,want", [("below", 154.0), ("at", 149.0), ("above", 144.0)])
def test_start_hr_chained_to_the_last_ua_test(band, want):
    last = {"ok": True, "band": band, "hr1": 149.2, "judge": "ua", "date": "2026-10-26", "hr_ref": False}
    r = AT.chained_start_hr(last, 140.0, 168.0)
    assert r["hr"] == want and r["from"] == "test"
    assert "2026-10-26" in r["why"] and "149" in r["why"]


@pytest.mark.parametrize("last", [None, {"ok": False, "band": None, "hr1": None},
                                  {"ok": True, "band": "below", "hr1": 149.0, "judge": "ua", "hr_ref": True},
                                  {"ok": True, "band": "base_ok", "hr1": 140.0, "judge": "xu"}])
def test_start_hr_without_a_usable_test_is_the_estimate(last):
    r = AT.chained_start_hr(last, 146.0, 168.0)
    assert r == {"hr": 146.0, "why": None, "from": "estimate"}
    assert AT.chained_start_hr(last, None, 160.0)["hr"] == pytest.approx(0.89 * 160 - 5)


# ---- the relation ---------------------------------------------------------------------------------

def test_flat_segments_skip_the_warm_up_and_the_grade():
    w = _run(TODAY)
    ch = w.channels
    seg = AT.flat_segments(ch["elapsedtime"], ch["heartrate"], ch["power"], ch["speed"],
                           ch["elapseddistance"], ch["elevation"])
    assert len(seg["pw"]) == len(seg["sp"]) == 39            # 50 min − 10 warm-up, the last minute's ±50 m edge
    assert min(h for h, _ in seg["pw"]) > 139
    hilly = _run(TODAY, grade=0.03).channels
    assert AT.flat_segments(hilly["elapsedtime"], hilly["heartrate"], hilly["power"], hilly["speed"],
                            hilly["elapseddistance"], hilly["elevation"]) == {"pw": [], "sp": []}
    assert AT.flat_segments(ch["elapsedtime"], ch["heartrate"], ch["power"], ch["speed"],
                            ch["elapseddistance"], None) == {"pw": [], "sp": []}     # no elevation: can't tell


def test_power_fit_at_the_start_hr():
    st = AT.start_target(_ds(_flat_runs()), TODAY, 153.0, "power", cp=195.0, cap=150.0)
    assert st["fit"] and st["basis"] == "power"
    assert st["value"] == pytest.approx(power_of(153.0), abs=2.0)       # ≈ 144 W, not 0.75 × 195 = 146
    assert st["target"] == f"固定功率 {st['value']:.0f} W（±3%）"
    assert st["why"].startswith("固定功率依據：最近 4 次平路輕鬆跑（")
    assert "心率 153 bpm ≈" in st["why"]


def test_pace_fit_at_the_start_hr():
    st = AT.start_target(_ds(_flat_runs()), TODAY, 148.0, "pace", cap=150.0)
    assert st["fit"] and st["basis"] == "pace"
    assert st["value"] == pytest.approx(520.0, abs=4.0)                  # 8:40 /km
    assert st["target"] in ("配速固定 8:39 /km（±3%）", "配速固定 8:40 /km（±3%）", "配速固定 8:41 /km（±3%）")
    assert st["why"].startswith("固定配速依據：最近 4 次平路輕鬆跑（")


@pytest.mark.parametrize("runs,why", [
    (lambda: _flat_runs(k=2), "最近 42 天只有 2 次"),                                    # too few runs
    (lambda: _flat_runs(grade=0.04), "最近 42 天只有 0 次"),                             # hilly: no flat minutes
    (lambda: _flat_runs(power=False), "最近 42 天只有 0 次有 Stryd 功率"),               # no power
    (lambda: [_run(TODAY - dt.timedelta(days=60 + i), seed=i) for i in range(4)], "只有 0 次"),   # too old
])
def test_power_fallback_is_cp_075_labelled_estimate(runs, why):
    st = AT.start_target(_ds(runs()), TODAY, 153.0, "power", cp=195.0, cap=150.0)
    assert not st["fit"]
    assert st["value"] == pytest.approx(146.25)
    assert st["target"] == "固定功率 146 W（±3%）"
    assert why in st["why"] and "先用 CP × 0.75（推估）" in st["why"]


def test_start_hr_far_outside_the_runs_is_not_extrapolated():
    st = AT.start_target(_ds(_flat_runs()), TODAY, 175.0, "power", cp=195.0)
    assert not st["fit"] and "起始心率 175 bpm 在平路輕鬆跑的心率範圍" in st["why"]


def test_hard_runs_are_not_easy_runs():
    st = AT.start_target(_ds(_flat_runs(hr_lo=160.0, hr_hi=170.0)), TODAY, 153.0, "power", cp=195.0, cap=150.0)
    assert not st["fit"]


# ---- the session: ① power, ② / ③ pace -------------------------------------------------------------

def _start(basis="power", fit=True, value=144.0):
    if basis == "power":
        return {"basis": "power", "value": value, "fit": fit, "target": f"固定功率 {value:.0f} W（±3%）",
                "why": "固定功率依據：最近 4 次平路輕鬆跑（10/7–10/24）的心率–功率關係，心率 153 bpm ≈ 144 W"}
    if fit:
        return {"basis": "pace", "value": 520.0, "fit": True, "target": "配速固定 8:40 /km（±3%）",
                "why": "固定配速依據：最近 4 次平路輕鬆跑（10/7–10/24）的心率–配速關係，心率 148 bpm ≈ 8:40 /km"}
    return {"basis": "pace", "value": None, "fit": False, "target": None,
            "why": "固定配速依據：最近 42 天只有 1 次心率可信的平路輕鬆跑（要 3 次）"}


@pytest.mark.parametrize("proto", ["ua40", "ua60", "evoke60"])
def test_power_in_effect_fixed_power_with_its_basis(proto):
    s = AT.session(TH, 153.0, AT.start_power(195.0), None, proto, None, start=_start(),
                   hr_why=AT.chained_start_hr({"ok": True, "band": "below", "hr1": 149.0, "judge": "ua",
                                               "date": "2026-10-26"}, 140.0, 168.0)["why"])
    assert s["target"] == "固定功率 144 W（±3%）；心率從 153 附近開始"
    assert "心率–功率關係" in s["detail"] and "上次 AeT 測試（2026-10-26）" in s["detail"]
    assert "固定功率不要調" in s["detail"]


@pytest.mark.parametrize("proto", ["ua40", "ua60", "evoke60"])
def test_no_power_fixed_pace(proto):
    th = {**TH, "cp": None}
    s = AT.session(th, 148.0, None, None, proto, None, start=_start("pace"))
    assert s["target"] == "配速固定 8:40 /km（±3%）；心率從 148 附近開始"
    assert " W" not in s["target"] and "固定功率" not in s["detail"]
    assert "固定配速不要調" in s["detail"] and "心率–配速關係" in s["detail"]


def test_no_pace_data_falls_back_to_the_e_pace_then_the_talk_test():
    e10k = EP.of_race({"distance_m": 10000, "time_s": 2700, "date": "2026-09-20"}, TODAY)
    s = AT.session({**TH, "cp": None, "e_pace": e10k}, 148.0, None, None, "ua40", None, start=_start("pace", fit=False))
    assert s["target"].startswith("配速固定 5:39–5:59 /km（E 配速")
    assert "最近 42 天只有 1 次" in s["detail"]
    talk = AT.session({**TH, "cp": None}, 148.0, None, None, "ua40", None, start=_start("pace", fit=False))
    assert talk["target"].startswith("能講完整句子的配速，固定不要調")
    assert "固定功率" not in talk["detail"]


def test_xu90_uses_the_fitted_pace_before_the_talk_test():
    s = AT.session({**TH, "cp": None}, 148.0, None, None, "xu90", None, start=_start("pace"))
    assert s["xu_basis"] == "fit" and s["target"].startswith("配速固定 8:40 /km（±3%）")
    e10k = EP.of_race({"distance_m": 10000, "time_s": 2700, "date": "2026-09-20"}, TODAY)
    assert AT.session({**TH, "e_pace": e10k}, 148.0, None, None, "xu90", None,
                      start=_start("pace"))["xu_basis"] == "pace"            # 徐國峰's E pace first


def test_without_start_the_old_behaviour():
    s = AT.session(TH, 140.0, 190.0, None, "ua40")
    assert s["target"] == "固定功率 190 W（±3%）；心率從 140 附近開始"


# ---- the steps: the editor's structure and COROS -------------------------------------------------

def _work(steps):
    return next(st for st in steps if st.kind == CW.EX_TRAIN)


def test_coros_power_step():
    s = AT.session(TH, 153.0, None, None, "ua40", None, start=_start())
    w = _work(CW.session_steps(s, CW.Thresholds.of(TH)))
    assert w.intensity == ("power", 140, 148) and w.name == "固定功率，不要調"


def test_coros_pace_step_is_intensity_type_3():
    th = {**TH, "cp": None}
    s = AT.session(th, 148.0, None, None, "ua40", None, start=_start("pace"))
    steps = CW.session_steps(s, CW.Thresholds.of(th))
    w = _work(steps)
    assert w.intensity == ("pace", 504, 536) and w.name == "固定配速，不要調"
    ex = CW.build_program("x", steps, CW.Thresholds.of(th))
    main = [e for e in ex["exercises"] if e.get("intensityType") == CW.INT_PACE]
    assert main and main[0]["intensityValue"] == 504 and main[0]["intensityValueExtend"] == 536
    assert not [e for e in ex["exercises"] if e.get("intensityType") == CW.INT_POWER]


def test_coros_pace_step_even_with_a_cp_on_file():
    # ② : the CP on file stays in the push's thresholds; the stored pace still wins over CP × 0.75
    s = AT.session({**TH, "cp": None}, 148.0, None, None, "ua60", None, start=_start("pace"))
    assert _work(CW.session_steps(s, CW.Thresholds.of(TH))).intensity[0] == "pace"


def test_coros_talk_test_has_no_target():
    th = {**TH, "cp": None}
    s = AT.session(th, 148.0, None, None, "ua40", None, start=_start("pace", fit=False))
    assert _work(CW.session_steps(s, CW.Thresholds.of(TH))).intensity is None


def test_editor_structure_pace():
    th = {**TH, "cp": None}
    s = AT.session(th, 148.0, None, None, "ua40", None, start=_start("pace"))
    d = WS.derive(s, th)
    work = [st for st in d["items"] if st.get("kind") == "work"]
    assert work and work[0]["target"] == {"type": "pace", "mode": "abs", "lo": 504, "hi": 536}
    assert work[0]["note"] == "固定配速，不要調"


# ---- English (SP-905: stored in zh-TW, read back in the request's language) -----------------------

def test_the_new_texts_read_in_english():
    from backend.engine import stored_text as ST
    from backend.scripts.i18n_extract import CJK
    chain = AT.chained_start_hr({"ok": True, "band": "above", "hr1": 155.0, "judge": "ua", "date": "2026-10-26"},
                                None, None)["why"]
    fb = AT.start_target(_ds(_flat_runs(k=1)), TODAY, 153.0, "power", cp=195.0)
    texts = []
    for start in (_start(), _start("pace"), _start("pace", fit=False), fb):
        for proto in ("ua40", "ua60", "evoke60", "xu90"):
            s = AT.session({**TH, "cp": None if start["basis"] == "pace" else 195.0}, 148.0, None, None, proto,
                           None, start=start, hr_why=chain)
            texts += [s["target"], s["detail"]]
    texts += [TP.target_policy({"kind": "test", "title": "AeT 飄移測試 40 分"}, None, {**TH, "cp": None},
                               power_state={"state": "none"})["why"],
              AT.main_name("配速固定 8:40 /km（±3%）"), AT.main_name("固定功率 144 W（±3%）")]
    left = []
    for t in texts:
        ST._MEMO.clear()
        en = ST.shown(t, "en")
        if CJK.search(en):
            left.append((t, en))
    assert not left


# ---- ① and ③ grade the same session the same (coordinator 2026-10-27) ------------------------------
# A pace step is graded at threshold pace ÷ its pace, a power step at its watts ÷ CP; running power ≈
# proportional to flat speed (Stryd), so with the athlete's threshold pace at their CP the two agree.
# (The e2e demo's ③ card graded its AeT test 中強度: there the threshold pace is the pace at LTHR,
# 4:27, while its CP 260 W sits at ≈ 3:56 by its own power–speed line — the thresholds disagree.)

def _figs(s, th):
    from backend.engine import session_structure as SST
    f = SST.figures(s, th)
    tot = WS.totals(f["steps"], f["ctx"])
    return f["tss"], {k: tot[k] for k in ("easy_s", "mod_s", "hard_s", "z5_s")}


ONE = {"state": "power"}
THREE = {"state": "none"}
CP1, W_AT, PACE_AT = 260.0, 194.0, 316.0                # the demo's fit at 145 bpm: 194 W ≈ 5:16 /km
TPACE_AT_CP = PACE_AT * W_AT / CP1                       # its pace at CP on the same power–speed line


@pytest.mark.parametrize("proto", ["ua40", "ua60", "evoke60"])
def test_the_aet_test_grades_the_same_by_power_or_pace(proto):
    th1 = {"cp": CP1, "lthr": 168.0, "aet": 150.0, "tpace": TPACE_AT_CP, "power_state": ONE}
    th3 = {**th1, "cp": None, "power_state": THREE}
    p = {"basis": "power", "value": W_AT, "fit": True, "target": "固定功率 194 W（±3%）", "why": "x"}
    q = {"basis": "pace", "value": PACE_AT, "fit": True, "target": "配速固定 5:16 /km（±3%）", "why": "y"}
    tss1, z1 = _figs(AT.session(th1, 145.0, None, None, proto, None, start=p), th1)
    tss3, z3 = _figs(AT.session(th3, 145.0, None, None, proto, None, start=q), th3)
    assert z1 == z3 and z1["mod_s"] == z1["hard_s"] == 0         # at / below AeT: easy in both
    assert tss3 == pytest.approx(tss1, rel=0.03)


def test_sp951_flat_intervals_grade_the_same_by_power_or_pace():
    th1 = {"cp": CP1, "lthr": 168.0, "aet": 150.0, "tpace": TPACE_AT_CP, "power_state": ONE}
    th3 = {**th1, "cp": None, "power_state": THREE}
    s = {"kind": "quality", "title": "有氧間歇 3×10 分", "minutes": 60, "target": "", "detail": "暖身 15 分；休 2 分"}
    tss1, z1 = _figs(s, th1)
    tss3, z3 = _figs(s, th3)
    assert z1 == z3 and tss3 == pytest.approx(tss1, rel=0.03)


# ---- the target policy -----------------------------------------------------------------------------

@pytest.mark.parametrize("state,basis", [("power", "power"), ("stale", "pace"), ("none", "pace")])
def test_target_policy_aet_by_power_state(state, basis):
    s = {"kind": "test", "title": "AeT 飄移測試 40 分", "protocol": "aet"}
    pol = TP.target_policy(s, None, {**TH, "cp": TH["cp"] if state == "power" else None},
                           power_state={"state": state})
    assert pol["basis"] == basis
    assert not pol["hr_cap"]
