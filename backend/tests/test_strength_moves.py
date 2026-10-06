"""
SP-191 肌力動作自己挑 (engine/strength_moves.py): the options per movement type come from
strength-session-design.md §3 (with easier / harder versions and the equipment); the phase still
decides the types and the sets × reps (strength_plan), the athlete's pick decides the move, the
default when nothing is picked (the texts are the SP-119 / SP-120 ones); a missing pull-up bar /
band swaps the move for one that doesn't need it; the balance block's moves can be picked too; the
choice is a 課表偏好 (stored, validated, through the API). Synthetic.
"""
import datetime as dt
from datetime import date

import pytest

from backend.engine import balance_plan as BP
from backend.engine import plan_prefs as PP
from backend.engine import projection as PJ
from backend.engine import strength_moves as SM
from backend.engine import strength_plan as STP
from backend.i18n import use_locale
from backend.settings import repository as SR
from backend.tests.test_b2b import _phases
from backend.tests.test_plan_store import API, Env
from backend.tests.test_strength_plan import _ev, _strength, _week


def _ctx(stage, moves=None, lack=None, race_kind="race"):
    ch = {"moves": moves or {}, "lack": lack or []}
    return {"active": True, "stage": stage, "race_kind": race_kind, "race": "x", "choice": ch}


# ---------------------------------------------------------------------------
# the option lists
# ---------------------------------------------------------------------------

def test_every_type_has_options_with_level_equipment_and_a_gear_free_move():
    assert [t.key for t in SM.TYPES] == ["knee", "ecc", "pull", "grip", "core", "glute",
                                         "stance", "reach", "landing", "calf"]
    for t in SM.TYPES:
        assert len(t.moves) >= 2 and len({m.key for m in t.moves}) == len(t.moves)
        assert t.moves[0].level == 0                               # the default is the reference
        for m in t.moves:
            assert m.name and m.gear and m.who and m.grade in (SM.COACH, SM.EST)
            assert m.needs in (None, *SM.EQUIPMENT)
        # whatever is missing, there is a move left
        assert any(m.needs is None for m in t.moves), t.key
    assert any(m.level < 0 for t in SM.TYPES for m in t.moves) and any(m.level > 0 for t in SM.TYPES for m in t.moves)
    # §3.6: no Russian twist; the defaults are SP-119's moves
    assert all(m.key != "russian_twist" for t in SM.TYPES for m in t.moves)
    assert [SM.BY[k].moves[0].key for k in ("knee", "ecc", "pull", "grip", "core")] == \
        ["split_squat", "step_down", "pull_up", "farmer_carry", "plank"]


def test_options_payload_for_the_page():
    o = SM.options()
    assert {e["key"] for e in o["equipment"]} == {"bar", "band"}
    knee = next(t for t in o["types"] if t["key"] == "knee")
    assert knee["default"] == "split_squat" and knee["group"] == "strength" and knee["tip"]
    assert {"key", "name", "level", "needs", "gear", "who", "grade"} <= set(knee["moves"][0])
    assert {t["group"] for t in o["types"]} == {"strength", "balance"}
    with use_locale("en"):
        o = SM.options()
        assert next(t for t in o["types"] if t["key"] == "pull")["moves"][1]["name"] == "Band row"


# ---------------------------------------------------------------------------
# resolve: default, picked, swapped for a missing equipment
# ---------------------------------------------------------------------------

def test_resolve_defaults_picks_and_equipment_swaps():
    r = SM.resolve(None)
    assert all(p.why == "default" and p.move is SM.BY[k].moves[0] for k, p in r.items())
    r = SM.resolve({"moves": {"knee": "bulgarian"}})
    assert r["knee"].why == "picked" and r["knee"].move.key == "bulgarian"
    # no bar: pull-ups -> band rows; neither bar nor band -> inverted rows (the owner's example)
    r = SM.resolve({"lack": ["bar"]})
    assert r["pull"].why == "swap" and r["pull"].move.key == "band_row" and r["pull"].wanted.key == "pull_up"
    assert SM.resolve({"lack": ["bar", "band"]})["pull"].move.key == "inverted_row"
    # a picked move that needs the missing bar gives way too; the default when it needs none
    r = SM.resolve({"moves": {"core": "hanging_leg_raise", "grip": "dead_hang"}, "lack": ["bar"]})
    assert r["core"].move.key == "plank" and r["core"].why == "swap"
    assert r["grip"].move.key == "farmer_carry" and r["grip"].wanted.key == "dead_hang"
    # no band: the clamshell (a mini band) -> side-lying leg raise
    assert SM.resolve({"lack": ["band"]})["glute"].move.key == "side_leg_raise"


def test_clean_drops_defaults_and_unknowns_strict_refuses():
    assert SM.clean_moves({"knee": "split_squat", "pull": "band_row"}) == (("pull", "band_row"),)
    assert SM.clean_moves({"knee": "nope", "x": "y"}) == ()                # stored: dropped
    with pytest.raises(ValueError):
        SM.clean_moves({"knee": "nope"}, strict=True)
    with pytest.raises(ValueError):
        SM.clean_moves(["knee"], strict=True)
    assert SM.clean_gear(["band", "bar", "bar"]) == ("band", "bar")
    with pytest.raises(ValueError):
        SM.clean_gear(["rope"], strict=True)


# ---------------------------------------------------------------------------
# strength_plan: the phase decides the types and the dose, the athlete the moves
# ---------------------------------------------------------------------------

def test_no_choice_keeps_the_sp119_texts():
    for st in ("aa", "max", "maint"):
        a = STP.session({"active": True, "stage": st, "race_kind": "race", "race": "x"})
        b = STP.session(_ctx(st))
        assert a == b and "你挑的" not in a["detail"] and "換上的動作" not in a["source"]
    assert STP.session({"active": True, "stage": "max", "race_kind": "race", "race": "x"})["title"] == \
        "肌力（分腿蹲＋離心下階＋引體向上）"


def test_picked_moves_replace_the_defaults_dose_by_phase():
    mv = {"knee": "bulgarian", "ecc": "rear_raised_lunge", "pull": "inverted_row", "core": "dead_bug"}
    mx = STP.session(_ctx("max", mv))
    assert mx["title"] == "肌力（後腳抬高蹲＋後腳抬高前弓步＋反式划船）" and mx["minutes"] == 35
    d = mx["detail"]
    # 最大肌力's dose on the picked moves; the defaults' names are gone
    assert "後腳抬高蹲 3–4 組 × 3–6 下" in d and "後腳抬高前弓步 3 組 × 8–12 下／腳" in d
    assert "反式划船 3 組（留 2 下）" in d and "最後死蟲式 2 組 × 8–12 下" in d
    assert "分腿蹲" not in d.replace("後腳抬高", "") and "引體向上" not in d and "棒式" not in d
    assert "這堂用你挑的動作：後腳抬高蹲、後腳抬高前弓步、反式划船、死蟲式" in d
    assert "後腳抬高蹲（教練級）" in mx["source"] and "反式划船（推估）" in mx["source"]
    # 解剖適應: the circuit's 12–15 reps; 高踏階 stays a station; holds keep their own wording
    aa = STP.session(_ctx("aa", {"knee": "iso_split_squat"}))
    assert "12–15 下" in aa["detail"] and "分腿蹲底部停住 30 秒／腳" in aa["detail"] and "高踏階" in aa["detail"]
    # 維持: 2 sets, the types SP-119 asks for
    mt = STP.session(_ctx("maint", {"ecc": "kettlebell_swing"}))
    assert mt["title"] == "肌力維持（高踏階＋壺鈴擺盪＋引體向上）" and "壺鈴擺盪 8–10 下" in mt["detail"]
    assert mt["minutes"] == 25


def test_the_phase_still_decides_the_types():
    mv = {"grip": "dead_hang", "glute": "monster_walk"}
    # 最大肌力 trains no grip / glute: a pick there changes nothing and says nothing
    assert STP.session(_ctx("max", mv)) == STP.session(_ctx("max"))
    # a 百岳 week with ME: no eccentric type — the picked eccentric move isn't added back
    me = [{"id": "me", "title": "肌耐力（ME）"}]
    s = STP.session(_ctx("maint", {"ecc": "box_lunge", "grip": "dead_hang"}, race_kind="baiyue"), me)
    assert "箱上原地前弓步" not in s["title"] and "這堂不做箱上原地前弓步" in s["detail"]
    assert "懸吊 2 × 20–40 秒" in s["detail"]


def test_no_bar_swaps_pull_ups_and_says_why():
    s = STP.session(_ctx("max", lack=["bar"]))
    assert s["title"] == "肌力（分腿蹲＋離心下階＋彈力帶划船）"
    assert "引體向上" not in s["title"] and "沒有單槓：引體向上改成彈力帶划船" in s["detail"]
    assert "彈力帶划船（推估）" in s["source"]
    aa = STP.session(_ctx("aa", lack=["bar", "band"]))
    assert "反式划船" in aa["detail"] and "引體向上（做不到" not in aa["detail"]
    # 棒式 30 秒或懸吊抬腿 is the plank's own AA wording: no bar needed for the plank itself
    assert "棒式" in aa["detail"]


def test_old_session_only_gets_the_line():
    s = STP.session({"active": False, "choice": {"moves": {"glute": "monster_walk"}, "lack": []}})
    assert s["title"] == "肌力（下肢單腳＋核心）" and s["minutes"] == 35 and "這堂用你挑的動作：怪獸走路" in s["detail"]
    assert STP.session({"active": False}) == {**STP.DEFAULT, "source": None}


def test_english_titles_follow_the_picks():
    with use_locale("en"):
        s = STP.session(_ctx("max", {"knee": "goblet_squat"}, ["bar"]))
    assert s["title"] == "Strength (Goblet squat + Eccentric step-down + Band row)"
    assert "no pull-up bar: Pull-up becomes Band row" in s["detail"]


# ---------------------------------------------------------------------------
# balance block
# ---------------------------------------------------------------------------

def test_balance_block_uses_the_picks_and_keeps_the_dose():
    base = {"active": True, "race_start": "2027-01-16"}
    assert BP.block(base) == BP.block({**base, "choice": {"moves": {}, "lack": ["bar"]}})
    ch = {"moves": {"stance": "soft", "landing": "step_stop", "calf": "toe_raise"}, "lack": []}
    t = BP.block({**base, "choice": ch})
    assert "2 組 × 20–40 秒／腳" in t and "軟墊上單腳站" in t and "往前跨一步，落地停住 2 秒" in t
    assert "靠牆提腳尖慢放" in t and "單腳站、另一腳往前、側、後伸出去點地" in t   # reach: the default
    assert "小跳" not in t.split("這堂用你挑的動作")[0] and "單腳提踵" not in t
    assert "這堂用你挑的動作：軟墊上單腳站、前跨步落地、提腳尖" in t
    s = BP.session({**base, "choice": ch}, "2027-01-05")
    assert "換上的動作：" in s["source"] and "Schiftan 2015" in s["source"]


# ---------------------------------------------------------------------------
# the 課表偏好: stored, validated, read by week_plan and the projection
# ---------------------------------------------------------------------------

def test_prefs_round_trip_validation_and_not_shaping():
    p = PP.Prefs(strength_moves=(("knee", "bulgarian"), ("pull", "band_row")), strength_no_gear=("bar",))
    assert not p.active                                      # the week's shape is untouched
    s = p.settings()
    assert s["plan.prefs.strength_moves"] == {"knee": "bulgarian", "pull": "band_row"}
    assert s["plan.prefs.strength_no_gear"] == ["bar"]
    for k, v in s.items():
        SR.validate(k, v)
    assert PP.from_settings(s) == p
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.strength_moves", {"knee": "nope"})
    with pytest.raises(ValueError):
        SR.validate("plan.prefs.strength_no_gear", ["rope"])
    with pytest.raises(ValueError):
        PP.from_body({"strength_moves": {"pull": "nope"}})
    # a stored move that no longer exists is dropped, not an error
    assert PP.from_settings({"plan.prefs.strength_moves": {"pull": "gone"}}).strength_moves == ()
    assert STP.week_context([], [], date(2026, 10, 5), "base", p)["choice"] == \
        {"moves": {"knee": "bulgarian", "pull": "band_row"}, "lack": ["bar"]}


def test_week_plan_and_projection_use_the_picks():
    pr = PP.Prefs(strength_moves=(("knee", "bulgarian"),), strength_no_gear=("bar",))
    plan, wp = _week([_ev("2027-01-02")], prefs=pr)
    ss = _strength(wp["sessions"])
    assert wp["phase"] == "base" and ss
    assert all(s["title"] == "肌力（後腳抬高蹲＋離心下階＋彈力帶划船）" for s in ss)
    weeks = PJ.project_weeks(wp, _phases(plan, date(2026, 9, 30)), date(2026, 11, 22), prefs=pr)
    by = {w["start"]: w for w in weeks}
    assert _strength(by["2026-10-05"]["sessions"])[0]["title"] == "肌力（後腳抬高蹲＋離心下階＋彈力帶划船）"
    sp = _strength(by["2026-10-26"]["sessions"])
    assert by["2026-10-26"]["phase"] == "specific" and sp[0]["title"] == "肌力維持（高踏階＋離心下階＋彈力帶划船）"


def test_week_plan_balance_block_uses_the_picks():
    pr = PP.Prefs(strength_moves=(("stance", "head_turn"),))
    _plan, wp = _week([_ev("2027-01-02")], prefs=pr)
    st = [s for s in wp["sessions"] if s["kind"] == "strength"]
    assert st and all("單腳站轉頭" in s["detail"] and s["minutes"] == 35 + BP.MINUTES for s in st)


def test_api_prefs_offer_and_store_the_moves(monkeypatch):
    with Env(monkeypatch) as e:
        got = e.c.get(f"{API}/prefs").json()
        assert got["prefs"]["strength_moves"] == {} and got["prefs"]["strength_no_gear"] == []
        assert {t["key"] for t in got["strength_options"]["types"]} >= {"knee", "pull", "stance"}
        assert e.c.put(f"{API}/prefs", json={"strength_moves": {"pull": "nope"}}).status_code == 400
        r = e.c.put(f"{API}/prefs", json={"strength_moves": {"pull": "inverted_row", "knee": "split_squat"},
                                          "strength_no_gear": ["band"]})
        assert r.status_code == 200 and r.json()["active"] is False
        got = e.c.get(f"{API}/prefs").json()["prefs"]
        assert got["strength_moves"] == {"pull": "inverted_row"} and got["strength_no_gear"] == ["band"]
