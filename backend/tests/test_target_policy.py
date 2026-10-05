"""目標依據 (engine/target_policy.py): the policy table, the prefs migration and the push mapping."""
import pytest

from backend.engine import interval_library as IL
from backend.engine import plan_prefs as PP
from backend.engine import target_policy as TP
from backend.engine import zones
from backend.settings import repository as SR
from backend.sync import coros_workouts as CW

TH = {"cp": 250.0, "lthr": 165.0, "aet": 145.0}


@pytest.mark.parametrize("s, basis, typ", [
    ({"kind": "easy", "title": "輕鬆跑"}, "power", "easy"),
    ({"kind": "easy", "title": "輕鬆跑", "terrain": "trail"}, "hr", "trail_easy"),
    ({"kind": "long", "title": "LSD"}, "power", "long"),
    ({"kind": "long", "title": "LSD（山路）"}, "hr", "trail_long"),
    ({"kind": "long", "title": "長跑", "terrain": "trail"}, "hr", "trail_long"),
    ({"kind": "hike", "title": "技術地形 40′（RPE 3–4）", "terrain": "trail"}, "hr", "hike"),
    ({"kind": "hike", "title": "登山健行"}, "hr", "walk"),             # SP-115: a walking session
    ({"kind": "easy", "title": "陡坡健走 14%（模擬負重 9 kg）", "terrain": "trail"}, "hr", "walk"),
    ({"kind": "mountain", "title": "山路長天"}, "hr", "trail_long"),
    ({"kind": "quality", "title": "閾值 3×8 分"}, "power", "interval"),
    ({"kind": "quality", "title": "VO2max 5×2 分上坡", "terrain": "trail"}, "power", "hill"),
    ({"kind": "quality", "title": "爬坡間歇 5×4 分"}, "power", "hill"),
    ({"kind": "long", "title": "長爬坡（建議）"}, "none", "climb"),
    ({"kind": "easy", "title": "下坡練習"}, "none", "downhill"),
    ({"kind": "test", "title": "CP 測試 20 分全力", "protocol": "quick"}, "power", "cp_test"),
    ({"kind": "test", "title": "AeT 飄移測試 徐國峰 90 分", "protocol": "aet"}, "hr", "aet_test"),
    ({"kind": "test", "title": "AeT 飄移測試 40 分", "protocol": "aet"}, "power", "aet_test"),
])
def test_the_auto_policy_table(s, basis, typ):
    p = TP.target_policy(s)
    assert (p["basis"], p["type"]) == (basis, typ) and p["source"]
    assert p["hr_cap"] == (basis == "power" and typ in ("interval", "hill", "easy", "long"))


def test_the_pref_and_the_sessions_own_choice():
    iv = {"kind": "quality", "title": "閾值 3×8 分"}
    assert TP.target_policy(iv, PP.Prefs(target_basis="hr"))["basis"] == "hr"
    assert TP.target_policy({"kind": "easy"}, PP.Prefs(target_basis="power"))["basis"] == "power"
    # tests and downhill keep their own rule
    assert TP.target_policy({"kind": "easy", "title": "下坡練習"}, PP.Prefs(target_basis="hr"))["basis"] == "none"
    assert TP.target_policy({"kind": "test", "title": "CP 測試 20 分全力"}, PP.Prefs(target_basis="hr"))["basis"] == "power"
    # the session's own 目標用 wins over the pref
    own = TP.target_policy({**iv, "target_basis": "power"}, PP.Prefs(target_basis="hr"))
    assert own["basis"] == "power" and own["why"].startswith("這次課表你選了")
    # a basis the thresholds can't fill falls back, with the reason
    assert TP.target_policy(iv, None, {"lthr": 165.0})["basis"] == "hr"
    assert TP.target_policy({"kind": "easy"}, None, {"lthr": 165.0})["fallback"]   # power without CP → HR
    assert TP.target_policy({"kind": "easy", "terrain": "trail"}, None, {"cp": 250.0})["fallback"]
    assert zones.WORKOUT_TARGETS[3][0] == "trail" and zones.WORKOUT_TARGETS[3][6] == "心率"


def test_the_old_interval_target_migrates():
    p = PP.from_settings({"plan.prefs.interval_target": "hr"})
    assert p.target_basis == "hr" and TP.pref_basis(p) == "hr"
    assert PP.from_settings({"plan.prefs.interval_target": "power"}).target_basis == "auto"
    assert PP.from_settings({"plan.prefs.target_basis": "power", "plan.prefs.interval_target": "hr"}).target_basis == "power"
    assert PP.from_settings({"plan.prefs.target_basis": "bogus"}).target_basis == "auto"
    assert SR.DEFAULTS["plan.prefs.target_basis"] == "auto"
    SR.validate("plan.prefs.target_basis", "hr")
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.target_basis", "pace")
    assert PP.from_settings(PP.Prefs(target_basis="power").settings()).target_basis == "power"


def test_the_push_uses_the_chosen_basis():
    th = CW.Thresholds.of(TH)
    v = IL.session_for(IL.fit("z3b", None), TH)
    s = {**v, "day": "2026-10-07"}
    work = lambda st: [x for x in st if isinstance(x, CW.Step) and x.kind == CW.EX_TRAIN and x.seconds == 480]
    assert work(CW.session_steps({**s, "basis": "power"}, th))[0].intensity == ("power", 225, 238)
    hr = work(CW.session_steps({**s, "basis": "hr"}, th))[0].intensity
    assert hr[0] == "hr" and hr[2] == 165
    easy = {"kind": "easy", "title": "輕鬆跑", "minutes": 45, "day": "2026-10-07"}
    assert CW.session_steps({**easy, "basis": "hr"}, th)[0].intensity == ("hr", 124, 145)
    assert CW.session_steps({**easy, "basis": "power"}, th)[0].intensity == ("power", 188, 200)
    assert CW.session_steps({**easy, "basis": "none"}, th)[0].intensity is None
    lines = CW.step_lines(CW.session_steps({**s, "basis": "power"}, th))
    assert lines[0].startswith("① 暖身") and any("×3 功率 225–238 W，休息 2 分" in ln for ln in lines)


def test_push_dict_resolves_the_basis_with_the_prefs(monkeypatch):
    from backend.engine import plan_store as PS
    monkeypatch.setattr(PP, "load", lambda user_id=1: PP.Prefs(target_basis="hr"))
    row = {"uid": "u", "week_start": "2026-10-05", "kind": "quality", "title": "閾值 3×8 分", "minutes": 53,
           "state": "active", "day": "2026-10-07"}
    assert PS.push_dict(row)["basis"] == "hr"
    assert PS.push_dict({**row, "target_basis": "power"})["basis"] == "power"
