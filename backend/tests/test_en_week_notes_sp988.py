"""SP-988: the 減量期 and B race week notes with what the week changed in their last bracket — 「…≤ 90 分
（賽前 7 天內不跑長跑、不排長爬坡反覆）」 — were a msgid with 「（」 + 「、」.join(…) + 「）」 glued on after it,
so an English page showed the whole note in Chinese. They are M_() texts now, the list a parameter
(i18n.M_list): the Chinese is the same as before to the character, English shows English — as an M_()
text (the plan inputs, in memory) and read back from the stored Chinese (engine/stored_text). Synthetic."""
from datetime import date

from backend.engine import overview as O
from backend.engine import planning as P
from backend.engine import post_race as PR
from backend.engine import stored_text as ST
from backend.i18n import M_, M_list, _

CJK = ST.CJK
NAME = "示範山徑越野 50K"


def s(id_, kind, day, minutes=50, **kw):
    return {"id": id_, "kind": kind, "day": day, "minutes": minutes, "title": "x", "tss": 50.0, "done": False,
            "detail": "", "source": "", "terrain": None, **kw}


def tc(sore=False, taper_start="2026-10-03"):
    return {"race": NAME, "id": "e", "start": "2026-10-17", "taper_start": taper_start, "days": 14, "trail": True,
            "sore": sore, "long_days": 14 if sore else 7}


def english(text):
    """The note as an English page shows it: the M_() text, and the same note read from its stored Chinese."""
    a, b = ST.shown(text, "en"), ST.shown(str(text), "en")
    return a, b


def no_chinese_but_the_name(t):
    return not CJK.search(t.replace(NAME, ""))


def test_the_taper_note_with_its_changes_reads_the_same_in_chinese_and_in_english():
    ss = [s("long", "long", "2026-10-12", 120), s("climb", "hill", "2026-10-08", 60)]
    notes = []
    O.taper_rules(ss, tc(), date(2026, 10, 5), notes)
    text = next(n["text"] for n in notes if n["src"] == "taper")
    legacy = _(O.TAPER_NOTE, race=NAME, days=14, full=O.TAPER_FULL_Q_DAYS, long=7, max=O.TAPER_LONG_MAX_MIN) \
        + "（" + "、".join([_("不排長爬坡反覆"), _("賽前 {n} 天內不跑長跑", n=7)]) + "）"
    assert str(text) == legacy
    for t in english(text):
        assert no_chinese_but_the_name(t), t
        assert t.startswith("Taper (14 days before the A race") and NAME in t
        assert t.endswith("(no long climb repeats, no long run in the last 7 days)"), t


def test_the_taper_note_without_changes_is_the_plain_msgid():
    ss = [s("easy1", "easy", "2026-10-08", 40)]
    notes = []
    O.taper_rules(ss, tc(), date(2026, 10, 5), notes)
    text = next(n["text"] for n in notes if n["src"] == "taper")
    assert str(text) == _(O.TAPER_NOTE, race=NAME, days=14, full=O.TAPER_FULL_Q_DAYS, long=7, max=O.TAPER_LONG_MAX_MIN)
    for t in english(text):
        assert no_chinese_but_the_name(t) and t.endswith("≤ 90 min"), t


def test_a_sore_races_long_run_before_the_taper_note_in_english():
    ss = [s("long", "long", "2026-10-04", 120)]
    notes = []
    O.taper_rules(ss, tc(sore=True, taper_start="2026-10-07"), date(2026, 9, 28), notes)
    text = next(n["text"] for n in notes if n["src"] == "taper")
    assert str(text) == f"A 賽事「{NAME}」賽事下坡多，賽前 14 天內不跑長跑：這週的長跑改成輕鬆跑（最後一次長跑在賽前 14 天以前）"
    for t in english(text):
        assert no_chinese_but_the_name(t) and "a race with long descents: no long run within 14 days" in t, t


def test_the_b_race_note_with_its_changes_in_english():
    b = P.Event(id="b", name=NAME, date="2026-10-17", priority="B", distance_km=21, climbing_m=800, est_hours=3.0)
    ss = [s("quality", "quality", "2026-10-12", 60, variant_key="x"), s("long", "long", "2026-10-18", 120)]
    notes = []
    PR.bc_apply(ss, [b], date(2026, 10, 12), notes)
    text = next(n["text"] for n in notes if n["src"] == "race")
    kw = {"race": NAME, "date": "10/17", "a": PR.B_INTERVAL_DAYS, "b": PR.B_TEMPO_LONG_DAYS}
    legacy = _(PR.B_RACE_NOTE, **kw) + "（" + "、".join([_("賽前 {n} 天內不排間歇", n=PR.B_INTERVAL_DAYS), _("不排長跑")]) + "）"
    assert str(text) == legacy
    a, _b = english(text)
    assert no_chinese_but_the_name(a) and a.endswith("; this week: no intervals within 5 days, no long run"), a
    assert no_chinese_but_the_name(_b), _b


def test_a_b_trip_note_without_changes_stays_the_old_msgid():
    b = P.Event(id="b", name=NAME, date="2026-10-17", priority="B", kind="baiyue", days=1, distance_km=15,
                climbing_m=1200, est_hours=6.0)
    notes = []
    PR.bc_apply([s("easy1", "easy", "2026-10-13", 40)], [b], date(2026, 10, 12), notes)
    text = next(n["text"] for n in notes if n["src"] == "race")
    assert str(text) == _(PR.B_TRIP_NOTE, race=NAME, date="10/17", a=PR.B_INTERVAL_DAYS, b=PR.B_TEMPO_LONG_DAYS)
    for t in english(text):
        assert no_chinese_but_the_name(t) and t.startswith("B trip"), t


def test_m_list_joins_in_the_language():
    items = [M_("不排長跑"), M_("賽前 {n} 天內不排間歇", n=5), M_("賽前 {n} 天內不排節奏跑、長跑", n=4)]
    m = M_list(items)
    assert str(m) == "不排長跑、賽前 5 天內不排間歇、賽前 4 天內不排節奏跑、長跑"
    assert ST.shown(m, "en") == "no long run, no intervals within 5 days, no tempo or long run within 4 days"
    assert M_list([]) == "" and M_list([items[0]]) is items[0]
    # the join is no reverse-lookup pattern: a msgid like 「{a}、{b}」 in the catalog made from_source read every
    # 「、」 of a frozen share text as a list (「補水、吃，…」 → "water, eat , …", test_en_leftovers_sp831)
    from backend.i18n import LIST_JOIN, catalog
    assert not ST.CJK.search(LIST_JOIN) and "{a}、{b}" not in catalog("en")
