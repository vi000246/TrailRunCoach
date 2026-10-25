"""同日配對先給長跑 (SP-814): plan_match's first pass pairs a run with a session planned on its own
day before the generator's week-wide pick (pass 2) is used. A fartlek run on the day the LSD was
planned went to the LSD there — the generator said it was the week's fartlek (SP-649), but pass 2
came too late: the fartlek stayed 「沒有對應的活動」 and the LSD read done.

Now the same-day pass leaves a run for the session the generator gave it to: a long run does not
take, on its own day, a run the generator matched to another open interval / test session of the
week. Pass 2 then links it there; if that fails (the session cannot take it) the lenient same-day
pass may still give it to the long run.

The week is SP-649's (test_quality_gate's flat 8 weeks → recovery week, TODAY = Wed 2026-09-30):
「恢復週 fartlek 4×1 分」 37′ and 「LSD（路跑）」 45′."""
import datetime as dt

from backend.engine import plan_match as PM
from backend.tests import test_week_match_long_first_sp649 as T649

MON = T649.MON
TODAY = T649.TODAY
BASE = {"target": "", "detail": "", "source": "", "origin": "auto", "edited": False,
        "provisional": False, "done_by": None, "note": None, "state": "active"}


def _stored(day_q, day_l, ws_):
    return [{**BASE, "week_start": ws_, "uid": "q", "gen_key": "quality", "day": day_q, "kind": "quality",
             "title": T649.FARTLEK, "minutes": 37, "tss": 30.0},
            {**BASE, "week_start": ws_, "uid": "l", "gen_key": "long", "day": day_l, "kind": "long",
             "title": "LSD（路跑）", "minutes": 45, "tss": 36.0}]


def test_a_fartlek_on_the_lsd_day_goes_to_the_fartlek():
    """The bug: the LSD planned on Monday, the fartlek on Wednesday; the 37′ fartlek run on Monday.
    Pass 1 gave it to the LSD (same day, run sport); now the fartlek takes it (pass 2, 「plan」)."""
    _ds, _wp, by = T649._week(T649._efforts(MON, 37))
    a = by["quality"]["done_by"]
    assert a["date"] == MON.isoformat()
    ws_ = MON.isoformat()
    gen_done = {(ws_, k): by[k] for k in ("quality", "long")}
    out = _stored(TODAY.isoformat(), MON.isoformat(), ws_)
    changes = PM.assign(out, [a], TODAY.isoformat(), gen_done)
    q, lng = out
    assert q["state"] == "done" and q["done_by"]["index"] == a["index"] and q["done_by"]["match"] == "plan"
    assert lng["state"] == "missed"               # Monday is past, nothing else ran: 沒有對應的活動
    assert [c["uid"] for c in changes if c["action"] == "done"] == ["q"]


def test_the_lsd_and_the_fartlek_each_take_their_own_on_the_lsd_day():
    """The fartlek run on the LSD's day and a 50′ LSD the day after: the LSD takes Tuesday's run
    (pass 2, the generator's pick), the fartlek Monday's."""
    lsd = T649._run(MON + dt.timedelta(days=1), minutes=50, power=180.0)
    _ds, _wp, by = T649._week(T649._efforts(MON, 37), lsd)
    aq, al = by["quality"]["done_by"], by["long"]["done_by"]
    assert (aq["date"], al["date"]) == (MON.isoformat(), (MON + dt.timedelta(days=1)).isoformat())
    ws_ = MON.isoformat()
    gen_done = {(ws_, k): by[k] for k in ("quality", "long")}
    out = _stored(TODAY.isoformat(), MON.isoformat(), ws_)
    PM.assign(out, [aq, al], TODAY.isoformat(), gen_done)
    q, lng = out
    assert q["done_by"]["index"] == aq["index"]
    assert lng["state"] == "done" and lng["done_by"]["index"] == al["index"]


def test_without_the_generators_word_the_long_run_still_takes_its_day():
    """No hint that the run is another session's (the generator matched nothing): the same-day pass
    gives it to the LSD as before."""
    _ds, _wp, by = T649._week(T649._efforts(MON, 37))
    a = by["quality"]["done_by"]
    ws_ = MON.isoformat()
    out = _stored(TODAY.isoformat(), MON.isoformat(), ws_)
    PM.assign(out, [a], TODAY.isoformat(), {})
    q, lng = out
    assert lng["state"] == "done" and lng["done_by"]["index"] == a["index"] and lng["done_by"]["match"] == "day"
    assert q["state"] == "active"


def test_a_real_long_run_on_its_day_stays_the_long_run():
    """The generator's pick for the LSD is the run on the LSD's own day: same-day pass, as before."""
    lsd = T649._run(MON, minutes=48, power=180.0)
    _ds, _wp, by = T649._week(lsd)
    al = by["long"]["done_by"]
    ws_ = MON.isoformat()
    gen_done = {(ws_, k): by[k] for k in ("quality", "long")}
    out = _stored(TODAY.isoformat(), MON.isoformat(), ws_)
    PM.assign(out, [al], TODAY.isoformat(), gen_done)
    q, lng = out
    assert lng["state"] == "done" and lng["done_by"]["index"] == al["index"] and lng["done_by"]["match"] == "day"
    assert q["state"] == "active"


def test_an_unlinked_fartlek_does_not_hold_the_run_back():
    """SP-647: the fartlek was unlinked by hand (never auto-matched again). It cannot take the run in
    pass 2, so the hint does not keep the run from the LSD on its day."""
    _ds, _wp, by = T649._week(T649._efforts(MON, 37))
    a = by["quality"]["done_by"]
    ws_ = MON.isoformat()
    gen_done = {(ws_, k): by[k] for k in ("quality", "long")}
    out = _stored(TODAY.isoformat(), MON.isoformat(), ws_)
    PM.assign(out, [a], TODAY.isoformat(), gen_done, unlinked={"q"})
    q, lng = out
    assert lng["state"] == "done" and lng["done_by"]["index"] == a["index"]
    assert q["state"] != "done"
