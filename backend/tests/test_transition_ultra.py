"""
SP-109: after a 超馬級+ A race (planning.event_size, the 14-day recovery's cut) the 轉換期 may be up
to 6 weeks (課表偏好 transition_weeks 5–6); every other A race stays ≤ 4 weeks; the default (3) is
unchanged and the next A race's 專項期 still shortens / skips it. Synthetic plans only.
"""
from datetime import date

from backend.engine import planning as P

B, E = date(2026, 6, 1), date(2027, 8, 1)


def ev(name, date_, **kw):
    return P.Event(id=name, name=name, date=date_, priority="A", **kw)


ULTRA = ev("u", "2026-12-19", distance_km=70, climbing_m=4000, est_hours=12.0)      # recovery 12/20 – 1/2
MARA = ev("m", "2026-12-19", kind="road", distance_km=42.2, est_hours=4.0)          # recovery 12/20 – 12/26


def tr(ph, eid):
    return [p for p in ph if p.kind == "transition" and p.event_id == eid]


def weeks(p):
    return ((P._d(p.end) - P._d(p.start)).days + 1) / 7


def test_cap_by_event_size():
    assert P.transition_weeks_for(ULTRA, 6) == 6 and P.transition_weeks_for(ULTRA, 9) == 6
    assert P.transition_weeks_for(MARA, 6) == 4 and P.transition_weeks_for(MARA, 3) == 3
    assert P.transition_weeks_for(ULTRA, 0) == 0 and P.transition_weeks_for(MARA, None) == 0
    trip = ev("t", "2026-12-19", kind="baiyue", days=2, distance_km=20, climbing_m=1500)
    assert P.transition_weeks_for(trip, 6) == 6                      # multi-day = 超馬級
    assert P.TRANSITION_WEEKS == 3 and P.TRANSITION_WEEKS_RANGE == (0, 6)


def test_ultra_gets_six_weeks_others_four_with_a_note():
    t = tr(P.auto_phases([ULTRA], B, E, 6), "u")[0]
    assert (t.start, weeks(t)) == ("2027-01-03", 6) and not t.note
    t = tr(P.auto_phases([MARA], B, E, 6), "m")[0]
    assert weeks(t) == 4 and "設定的 6 週只用在超馬級以上" in t.note
    # the default is unchanged
    assert weeks(tr(P.auto_phases([ULTRA], B, E), "u")[0]) == 3


def test_next_a_race_still_shortens_or_skips_it():
    # the next race's 專項期 starts 2027-01-23 (4/3 − 14 d − 8 wk): 20 days of the 6 weeks are left
    nxt = ev("n", "2027-04-03", est_hours=4.0)
    t = tr(P.auto_phases([ULTRA, nxt], B, E, 6), "u")[0]
    assert (t.start, t.end) == ("2027-01-03", "2027-01-22") and "縮短為 20 天" in t.note
    close = ev("c", "2027-03-20", est_hours=4.0)                      # 專項期 from 1/9: 6 days → none
    ph = P.auto_phases([ULTRA, close], B, E, 6)
    assert not tr(ph, "u")
