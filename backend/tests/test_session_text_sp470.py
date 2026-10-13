"""SP-470: a session's text says what the session is, never the race rule that made it.

「B 賽事「北插天山」前 4 天內不排節奏跑、長跑：輕鬆跑」 sat in the detail of every session the B race's
mini-taper made an easy run (the 課表 hover, the watch's workout overview, the calendar feed). The race
rules now only change the session; why they did is the week's race / taper / recovery note, which
already said it. Covers: the B race (before, its week, after), the A race's first week after, the A
taper (long run / quality / 長爬坡反覆 made easy) and the balance-only strength session. Synthetic."""
from datetime import date

from backend.engine import balance_plan as BP
from backend.engine import overview as O
from backend.engine import planning as P
from backend.engine import post_race as PR
from backend.tests import test_bc_races as BC
from backend.tests import test_post_race as AP
from backend.tests import test_taper_rules as TR

RACE_WORDS = ("賽事", "賽前", "賽後", "比賽", "減量期", "「")


def _clean(text: str) -> bool:
    return not any(w in (text or "") for w in RACE_WORDS)


def test_b_race_mini_taper_easy_runs_say_nothing_about_the_race():
    b = BC.ev("北插天山", "2026-10-17", **BC.MEDIUM)
    ss = [BC.s("quality", "quality", "2026-10-12", 60, variant_key="x", detail="暖身 15 分；5×3 分，休 3 分；緩和 10 分"),
          BC.s("quality2", "quality", "2026-10-13", 60, title="節奏跑 2×10", detail="節奏跑 2×10 分"),
          BC.s("long", "long", "2026-10-18", 120, detail="平路或緩坡；全程心率壓在 150 以下")]
    notes = []
    out = PR.bc_apply(ss, [b], BC.MON, notes, easy_target="心率 < 154 bpm")
    made = [x for x in out if x["day"] in ("2026-10-12", "2026-10-13", "2026-10-18")]
    assert [x["kind"] for x in made] == ["easy"] * 3
    for x in made:
        assert x["detail"] == "" and x["target"] == "心率 < 154 bpm", x       # the old session's text is gone too
    # the why is in the week's race note, as before
    race = [n["text"] for n in notes if n["src"] == "race"]
    assert race and "北插天山" in race[0] and "不排間歇" in race[0] and "不排節奏跑" in race[0]


def test_b_race_recovery_easy_runs_say_nothing_about_the_race():
    b = BC.ev("北插天山", "2026-10-10", **BC.MEDIUM)                         # Sat; 5 easy days after it
    notes = []
    out = PR.bc_apply([BC.s("quality", "quality", "2026-10-12", 60, detail="5×3 分")], [b], BC.MON, notes)
    assert out[0]["kind"] == "easy" and out[0]["detail"] == ""
    assert any("北插天山" in n["text"] and "只排輕鬆跑" in n["text"] for n in notes)


def test_a_race_first_week_says_the_session_only():
    r = AP.ev(**AP.MARA)
    w = PR.a_windows(P.auto_phases([r], AP.B, AP.E, 0), [r], date(2027, 3, 8))
    notes = []
    out = PR.apply([AP.s("quality", "quality", "2027-03-11", 60)], w, date(2027, 3, 8), notes)
    assert out[0]["kind"] == "easy" and out[0]["detail"] == "輕鬆跑 ≤ 40 分" and _clean(out[0]["detail"])
    assert notes and "第 1 週每次 ≤ 40 分輕鬆跑" in notes[0]["text"]


def test_taper_easy_runs_say_the_session_only():
    ss = [TR.s("long", "long", "2026-10-11", 150, detail="LSD"),               # 6 days out: easy
          TR.s("quality", "quality", "2026-10-16", detail="5×3 分"),           # 1 day out: easy
          TR.s("climb", "easy", "2026-10-06", 70, climb_m=600, detail="長爬坡 2×20 分")]
    notes = []
    O.taper_rules(ss, TR.tc(), date(2026, 10, 5), notes)
    by = {x["day"]: x for x in ss}
    assert by["2026-10-11"]["kind"] == "easy" and by["2026-10-11"]["detail"] == ""
    assert by["2026-10-16"]["kind"] == "easy" and by["2026-10-16"]["detail"] == ""
    assert by["2026-10-06"]["detail"] == "平路或緩坡輕鬆跑"
    assert all(_clean(x["detail"]) for x in ss)
    txt = notes[0]["text"]
    assert notes[0]["src"] == "taper" and "不跑長跑" in txt and "不排強度課" in txt and "不排長爬坡反覆" in txt


def test_balance_session_says_the_session_only():
    d = BP.session({"active": True, "race": "r", "race_start": "2026-10-17"}, "2026-10-14")
    assert "賽前" not in d["detail"] and d["detail"].endswith("平衡照做（負荷低，不累腿）")


def test_english_texts():
    from backend.i18n import _, use_locale
    with use_locale("en"):
        assert _("輕鬆跑 ≤ {max} 分", max=40) == "Easy run ≤ 40 min"
        assert _("平路或緩坡輕鬆跑") == "Easy run on the flat or gentle slopes"
        assert _("平衡照做（負荷低，不累腿）") == "Keep the balance work (a light load, it doesn't tire the legs)"
