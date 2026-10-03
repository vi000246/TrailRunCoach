"""
不排課日期 (engine/blackouts.py): validation, placement never on a blocked day,
move vs drop, the week's hours and note, the <= 10 % step after a short week,
edited sessions as a preview conflict, the 50-min cap and allowed weekdays,
projection weeks, and a mocked COROS removal through reconcile + push.
No dataset, no network.
"""
from datetime import date

import pytest

from backend.engine import blackouts as BL
from backend.engine import plan_prefs as PP
from backend.engine import plan_store as PS
from backend.engine import projection as P
from backend.engine import reconcile as R
from backend.settings import repository as SR
from backend.tests.test_plan_prefs import RATES, TGT
from backend.tests.test_plan_store import API, PHASES, Env, cur_plan, g, inputs, next_week


def bo(start, end, label="連假出遊", id=None):
    return BL.Blackout(id=id or f"b{start}", start=start, end=end, label=label)


def bmap(*bos):
    return {d: b.label for d, b in BL.blocked(bos).items()}


def main(ss):
    return [s for s in ss if s["kind"] != "strength"]


MON = date(2026, 10, 5)


def week(blocked=(), prefs=None, hours=5.0, notes=None, kind="base", mode="base"):
    return P.week_sessions(MON, kind, mode, hours, 50.0, TGT, 6, 60.0, False, True, 17.5, 150.0, None,
                           prefs=prefs, rates=RATES, notes=notes, blocked=set(blocked))


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    [{"id": "a", "start": "2026-10-04", "end": "2026-10-01", "label": ""}],            # end before start
    [{"id": "a", "start": "2026/10/01", "end": "2026-10-02", "label": ""}],            # format
    [{"id": "a", "start": "2026-10-01", "end": "2026-12-31", "label": ""}],            # > 62 days
    [{"id": "a", "start": "2026-10-01", "end": "2026-10-02", "label": "x" * 31}],      # label
    [{"id": "a", "start": "2026-10-01", "end": "2026-10-03", "label": ""},
     {"id": "b", "start": "2026-10-03", "end": "2026-10-05", "label": ""}],            # overlap
    [{"id": "a", "start": "2026-10-01", "end": "2026-10-01", "label": ""},
     {"id": "a", "start": "2026-10-09", "end": "2026-10-09", "label": ""}],            # duplicate id
    [{"id": "a", "start": "2026-10-01", "end": "2026-10-01", "label": "", "x": 1}],    # unknown field
    "2026-10-01",
])
def test_bad_ranges_are_rejected(value):
    with pytest.raises(ValueError):
        SR.validate(BL.KEY, value)


def test_normalize_fills_ids_sorts_and_allows_past_ranges():
    out = BL.normalize([{"start": "2026-10-09", "end": "2026-10-11", "label": " 連假出遊 "},
                        {"start": "2026-08-01", "label": ""}])                  # past, one day
    assert [r["start"] for r in out] == ["2026-08-01", "2026-10-09"]
    assert out[0]["end"] == "2026-08-01" and out[1]["label"] == "連假出遊" and all(r["id"] for r in out)
    SR.validate(BL.KEY, out)
    assert SR.DEFAULTS[BL.KEY] == []


# ---------------------------------------------------------------------------
# placement
# ---------------------------------------------------------------------------

BLOCK_SETS = [("2026-10-10", "2026-10-11"), ("2026-10-05", "2026-10-07"), ("2026-10-08", "2026-10-11"),
              ("2026-10-06", "2026-10-06"), ("2026-10-05", "2026-10-10")]


@pytest.mark.parametrize("prefs", [None, PP.Prefs(cap_weekday=50), PP.Prefs(runs=4, days=(True, False, True, True, False, True, True)),
                                   PP.Prefs(strength_days=(5, 6))])
@pytest.mark.parametrize("rng", BLOCK_SETS)
def test_placement_never_lands_on_a_blocked_day(prefs, rng):
    days = set(BL.blocked([bo(*rng)]))
    ss = week(days, prefs)
    assert not [s for s in ss if s["day"] in days], [(s["id"], s["day"]) for s in ss]
    placed = [s for s in main(ss) if s["day"]]
    assert len({s["day"] for s in placed}) == len(placed)                 # still one main session a day


def test_long_run_gets_the_last_free_day_before_easy_runs():
    days = set(BL.blocked([bo("2026-10-05", "2026-10-10")]))                # only Sunday left
    ss = week(days)
    placed = [s for s in main(ss) if s["day"]]
    assert [s["id"] for s in placed] == ["long"] and placed[0]["day"] == "2026-10-11"
    assert [s for s in main(ss) if s["kind"] == "easy" and not s["day"]]   # the easy runs are the ones dropped


def test_hard_sessions_stay_apart_when_days_are_blocked():
    days = set(BL.blocked([bo("2026-10-06", "2026-10-07")]))
    ss = week(days)
    hard = sorted(date.fromisoformat(s["day"]) for s in ss if s["kind"] in ("long", "quality") and s["day"])
    assert len(hard) == 2 and (hard[1] - hard[0]).days >= 2


# ---------------------------------------------------------------------------
# move vs drop (stored sessions)
# ---------------------------------------------------------------------------

def S(uid, kind, day, edited=False, origin="auto", state="active", gen_key=None):
    return {"uid": uid, "week_start": R.monday_of(day), "gen_key": gen_key if gen_key is not None else (uid if origin == "auto" else None),
            "day": day, "kind": kind, "title": uid, "minutes": 45, "target": "", "detail": "", "source": "",
            "tss": 30.0, "origin": origin, "edited": edited, "provisional": False, "state": state,
            "done_by": None, "note": None}


def test_move_to_nearest_free_day_and_not_next_to_a_hard_day():
    wk = [S("quality", "quality", "2026-10-06"), S("easy1", "easy", "2026-10-07"),
          S("long", "long", "2026-10-11"), S("easy2", "easy", "2026-10-09")]
    blocked = bmap(bo("2026-10-10", "2026-10-11"))
    long_s = wk[2]
    # nearest free days: 10/8 (next to nothing hard? quality 10/6 is 2 away) — 10/10 blocked
    assert BL.move_to(long_s, wk, blocked, "2026-10-05") == "2026-10-08"
    q = S("q2", "quality", "2026-10-10")
    assert BL.move_to(q, wk + [q], blocked, "2026-10-05") == "2026-10-08"
    # 10/8 taken by the long now: the next quality has nowhere (10/5 is next to 10/6)
    wk2 = wk + [S("x", "easy", "2026-10-08")]
    assert BL.move_to(q, wk2 + [q], blocked, "2026-10-05") is None
    # an easy run goes to the nearest free day even next to a hard one
    e = S("e", "easy", "2026-10-10")
    assert BL.move_to(e, wk + [e], blocked, "2026-10-05") == "2026-10-08"
    # today bounds it, allowed weekdays bound it
    assert BL.move_to(e, wk + [e], blocked, "2026-10-09") is None
    no_thu = lambda d: d.weekday() != 3
    assert BL.move_to(e, wk + [e], blocked, "2026-10-05", no_thu) == "2026-10-05"


def _stored_week():
    base = inputs()
    stored, _ = R.reconcile([], PS.gen_weeks(base), base["activities"], base["today"], base["horizon_end"])
    return base, stored


def test_unedited_auto_follows_the_regenerated_week_and_says_why():
    base, stored = _stored_week()
    blocked = bmap(bo("2026-10-03", "2026-10-04"))
    # the generator (which never uses a blocked day) moved the long to 10/2 and dropped easy1
    cur = cur_plan(sessions=[s for s in cur_plan()["sessions"] if s["id"] != "easy1"])
    for s in cur["sessions"]:
        if s["id"] == "long":
            s["day"] = "2026-10-02"
    new, ch = R.reconcile(stored, PS.gen_weeks({**base, "cur": cur}), base["activities"], base["today"],
                          base["horizon_end"], blocked=blocked)
    assert not [s for s in new if s["state"] == "active" and s["day"] in blocked]
    c = next(c for c in ch if c["title"] == "LSD（山路）")
    assert c["action"] == "changed" and c["before"]["day"] == "2026-10-04"
    assert c["reason"] == "在不排課日期內（連假出遊），移到 10/2"


def test_auto_session_in_a_week_not_regenerated_moves_or_drops():
    ss = [S("long", "long", "2026-10-25"), S("easy1", "easy", "2026-10-24"), S("easy2", "easy", "2026-10-21")]
    blocked = bmap(bo("2026-10-24", "2026-10-25"))
    new, ch = R.reconcile(ss, [], [], "2026-10-20", None, blocked=blocked)
    act = {s["uid"]: s["day"] for s in new if s["state"] == "active"}
    assert act["long"] == "2026-10-23"                       # nearest free day; long first
    assert act["easy1"] == "2026-10-22"
    assert act["easy2"] == "2026-10-21"
    # a full week blocked: dropped
    blocked = bmap(bo("2026-10-19", "2026-10-25"))
    new, ch = R.reconcile(ss, [], [], "2026-10-20", None, blocked=blocked)
    assert not [s for s in new if s["state"] == "active"]
    assert all(c["action"] == "removed" and "本週沒有空的日子" in c["reason"] for c in ch)


def test_edited_session_is_a_conflict_until_the_user_decides():
    ss = [S("long", "long", "2026-10-25", edited=True), S("mine", "hike", "2026-10-24", origin="custom"),
          S("easy2", "easy", "2026-10-21")]
    blocked = bmap(bo("2026-10-24", "2026-10-25"))
    # preview / no decision: shown, nothing touched
    new, ch = R.reconcile(ss, [], [], "2026-10-20", None, blocked=blocked)
    assert {s["uid"]: s["day"] for s in new} == {"long": "2026-10-25", "mine": "2026-10-24", "easy2": "2026-10-21"}
    conf = {c["uid"]: c for c in ch}
    assert conf["long"]["action"] == "conflict" and conf["long"]["reason"].startswith("在不排課日期內")
    assert conf["long"]["conflict"]["move_to"] == "2026-10-23"          # long first, nearest
    # the hike is hard too: not next to the long's proposed 10/23, 10/21 has a run -> 10/20
    assert conf["mine"]["conflict"]["move_to"] == "2026-10-20"
    # the user's choices
    new, ch = R.reconcile(ss, [], [], "2026-10-20", None, blocked=blocked,
                          decisions={"long": "move", "mine": "delete"})
    by = {s["uid"]: s for s in new}
    assert by["long"]["day"] == "2026-10-23" and by["long"]["state"] == "active" and by["long"]["edited"]
    assert "mine" not in by                                             # custom: gone
    assert {c["uid"]: c["action"] for c in ch} == {"long": "changed", "mine": "removed"}
    # an edited auto session deleted leaves a tombstone (not regenerated)
    new, _ = R.reconcile(ss, [], [], "2026-10-20", None, blocked=blocked, decisions={"long": "delete"})
    assert next(s for s in new if s["uid"] == "long")["state"] == "deleted"


def test_conflict_hike_room_when_no_hard_neighbour():
    ss = [S("mine", "hike", "2026-10-24", origin="custom")]
    blocked = bmap(bo("2026-10-24", "2026-10-25"))
    _, ch = R.reconcile(ss, [], [], "2026-10-20", None, blocked=blocked)
    assert ch[0]["conflict"]["move_to"] == "2026-10-23"


def test_allowed_weekdays_bound_the_move():
    ss = [S("easy1", "easy", "2026-10-24")]
    blocked = bmap(bo("2026-10-24", "2026-10-25"))
    days = [True, True, True, False, False, False, False]                  # Mon–Wed only
    new, _ = R.reconcile(ss, [], [], "2026-10-20", None, blocked=blocked, allowed_days=days)
    assert new[0]["day"] == "2026-10-21"


# ---------------------------------------------------------------------------
# week hours, the note, the step after it, projection
# ---------------------------------------------------------------------------

def test_factor_and_note_text():
    m = date(2026, 9, 28)
    b = bmap(bo("2026-09-30", "2026-10-04"))
    bm = BL.blocked([bo("2026-09-30", "2026-10-04")])
    lost = BL.lost_days(bm, m)
    assert len(lost) == 5 and BL.factor(m, lost) == pytest.approx(2 / 7)
    n = BL.week_note(bm, lost, 3.57)
    assert n["text"] == "9/30–10/4 不排課（連假出遊），本週少 3.6 小時" and n["src"] == "blackout"
    # a past blocked day the athlete trained on isn't lost; a rest weekday isn't either
    assert len(BL.lost_days(bm, m, trained={date(2026, 9, 30)})) == 4
    sat_sun_off = lambda d: d.weekday() < 5
    lost2 = BL.lost_days(bm, m, sat_sun_off)
    assert len(lost2) == 3 and BL.factor(m, lost2, sat_sun_off) == pytest.approx(2 / 5)
    assert b                                                              # labels map


def test_projection_weeks_honor_blackouts_hours_note_and_step():
    base = P.project_weeks(cur_plan(), PHASES, date(2026, 11, 1))
    bos = [bo("2026-10-16", "2026-10-18")]                                # Fri–Sun of the week of 10/12
    weeks = P.project_weeks(cur_plan(), PHASES, date(2026, 11, 1), blackouts=bos)
    days = set(BL.blocked(bos))
    assert not [s for w in weeks for s in w["sessions"] if s["day"] in days]
    i = next(i for i, w in enumerate(weeks) if w["start"] == "2026-10-12")
    strip = lambda ws: [{k: v for k, v in w.items() if k != "notes"} for w in ws]
    assert strip(weeks[:i]) == strip(base[:i])                             # earlier weeks untouched
    w = weeks[i]
    assert w["hours"] == pytest.approx(base[i]["hours"] * 4 / 7)
    lost_h = base[i]["hours"] * 3 / 7
    assert any(n["text"] == f"10/16–10/18 不排課（連假出遊），本週少 {lost_h:.1f} 小時" for n in w["notes"])
    assert w["blackout_days"] == sorted(days)
    # the next week: a 3-day break is Daniels' category 1 — back to 100 %, no make-up
    # (detraining.md §6.2); the old ≤ 10 % step_cap is gone (it held the volume down for weeks)
    nxt = weeks[i + 1]
    assert nxt["hours"] == pytest.approx(base[i + 1]["hours"])
    assert nxt["mode"] != "reentry"


def test_projection_first_week_after_a_short_current_week():
    cur = cur_plan(hours=2.0)
    cur["blackout_days"] = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    for h in cur["history"]:
        h["hours"] = 6.0                                                   # no 3:1 recovery week next
    cur["history"][-2]["hours"] = 4.0
    base = P.project_weeks({**cur, "blackout_days": []}, PHASES, date(2026, 10, 11))
    weeks = P.project_weeks(cur, PHASES, date(2026, 10, 11), blackouts=[bo("2026-10-01", "2026-10-04")])
    # 4 days off < 6: no re-entry block and no step cap — the same week as without the blackout
    assert weeks[0]["hours"] == pytest.approx(base[0]["hours"]) and weeks[0]["mode"] != "reentry"
    assert not any("實際只練了" in n["text"] for n in weeks[0]["notes"])


def test_50_min_cap_and_allowed_weekdays_with_blackouts():
    p = PP.Prefs(cap_weekday=50, cap_mode="hard", days=(True, False, True, False, True, True, True))
    bos = [bo("2026-10-10", "2026-10-11")]                                # Sat–Sun off this time
    weeks = P.project_weeks(cur_plan(), PHASES, date(2026, 10, 18), prefs=p, blackouts=bos)
    w = weeks[0]
    days = set(BL.blocked(bos))
    for s in w["sessions"]:
        d = date.fromisoformat(s["day"])
        assert s["day"] not in days and p.allowed(d), s
        if s["kind"] not in ("test", "strength"):
            assert s["minutes"] <= 50, s
    # allowed days 5, two blocked: 3/5 of the volume
    base = P.project_weeks(cur_plan(), PHASES, date(2026, 10, 18), prefs=p)
    assert w["hours"] == pytest.approx(base[0]["hours"] * 3 / 5)
    assert any(n["src"] == "blackout" for n in w["notes"])


# ---------------------------------------------------------------------------
# API: preview, save with decisions, add refused, COROS removal (mocked)
# ---------------------------------------------------------------------------

class BEnv(Env):
    """Env whose generator honours the candidate / stored blackouts like week_plan:
    sessions on a blocked day of this week move to 10/3 (long) or are dropped."""

    def __init__(self, monkeypatch):
        super().__init__(monkeypatch)
        from backend.api import plan_sessions
        self.stored_bl = []
        base = self.inp

        def compute(blackouts=None):
            bl = self.stored_bl if blackouts is None else blackouts
            days = set(BL.blocked(BL.from_list(bl)))
            cur = cur_plan()
            keep = []
            for s in cur["sessions"]:
                if s["day"] in days and not s.get("done"):
                    if s["id"] == "long" and "2026-10-03" not in days:
                        s = {**s, "day": "2026-10-03"}
                    else:
                        continue
                keep.append(s)
            cur["sessions"] = keep
            return {**base, "cur": cur, "blackouts": bl}
        monkeypatch.setattr(plan_sessions, "_compute_inputs", compute)


BL_LIST = [{"start": "2026-10-04", "end": "2026-10-06", "label": "連假出遊"}]


def test_api_preview_saves_nothing_and_put_applies_decisions(monkeypatch):
    with BEnv(monkeypatch) as e:
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        q = next(s for s in ss if s["day"] == "2026-10-06")                   # next week's quality
        assert e.c.patch(f"{API}/sessions/{q['uid']}", json={"minutes": 50}).status_code == 200   # edited
        pv = e.c.post(f"{API}/blackouts/preview", json={"blackouts": BL_LIST})
        assert pv.status_code == 200
        ch = pv.json()["changes"]
        conf = next(c for c in ch if c["uid"] == q["uid"])
        assert conf["action"] == "conflict" and conf["conflict"]["label"] == "連假出遊"
        long_c = next(c for c in ch if c["title"] == "LSD（山路）" and c.get("before"))
        assert long_c["before"]["day"] == "2026-10-04" and "在不排課日期內" in long_c["reason"]
        assert e.c.get(f"{API}/blackouts").json()["blackouts"] == []          # nothing saved
        assert any(s["day"] == "2026-10-04" for s in e.c.get(f"{API}/sessions").json()["sessions"])
        # bad input
        assert e.c.post(f"{API}/blackouts/preview", json={"blackouts": [{"start": "x"}]}).status_code == 400
        assert e.c.put(f"{API}/blackouts", json={"blackouts": BL_LIST, "decisions": {"a": "keep"}}).status_code == 400
        # save with the user's choice for the edited session
        r = e.c.put(f"{API}/blackouts", json={"blackouts": BL_LIST, "decisions": {q["uid"]: "move"}})
        assert r.status_code == 200
        saved = e.c.get(f"{API}/blackouts").json()["blackouts"]
        assert [(b["start"], b["end"], b["label"]) for b in saved] == [("2026-10-04", "2026-10-06", "連假出遊")]
        e.stored_bl = saved
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        days = set(BL.blocked(BL.from_list(saved)))
        assert not [s for s in ss if s["state"] == "active" and s["day"] in days]
        moved = next(s for s in ss if s["uid"] == q["uid"])
        assert moved["day"] == "2026-10-08" and moved["minutes"] == 50        # 10/7 is next to nothing hard? easy there
        # adding onto a blocked day is refused
        r = e.c.post(f"{API}/sessions", json={"day": "2026-10-05", "kind": "easy", "minutes": 30})
        assert r.status_code == 400 and "不排課" in r.json()["detail"]


def test_pushed_sessions_on_a_blackout_day_come_off_coros(monkeypatch):
    with BEnv(monkeypatch) as e:
        e.c.post(f"{API}/push-coros?scope=phase")
        assert {20261004, 20261006} <= {x["happenDay"] for x in e.fake.entities}
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        q = next(s for s in ss if s["day"] == "2026-10-06")
        e.c.patch(f"{API}/sessions/{q['uid']}", json={"minutes": 50})         # edited, no decision given
        e.c.post(f"{API}/push-coros?scope=phase")
        # save the blackout without a decision for the edited one: it stays in the plan (conflict) ...
        r = e.c.put(f"{API}/blackouts", json={"blackouts": BL_LIST})
        assert any(c["action"] == "conflict" and c["uid"] == q["uid"] for c in r.json()["changes"])
        e.stored_bl = r.json()["blackouts"]
        pv = e.c.get(f"{API}/push-coros/preview?scope=phase").json()
        assert pv["blackout_to_remove"] == 1
        assert not [s for s in pv["sessions"] if s["day"] in ("2026-10-04", "2026-10-05", "2026-10-06")]
        n = len(e.fake.calls)
        res = e.c.post(f"{API}/push-coros?scope=phase").json()
        # ... but nothing on a blocked day is left on COROS: the regenerated long (10/4 -> 10/3) is
        # re-sent on its new day, the edited quality on 10/6 is removed
        live_days = {x["happenDay"] for x in e.fake.entities}
        assert not live_days & {20261004, 20261005, 20261006}, live_days
        assert 20261003 in live_days
        assert any(x["status"] == "removed" for x in res["removed"])
        assert len(e.fake.calls) > n
        ss = e.c.get(f"{API}/sessions").json()["sessions"]
        assert next(s for s in ss if s["uid"] == q["uid"])["state"] == "active"   # not silently deleted
