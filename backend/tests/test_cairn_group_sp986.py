"""
SP-986: Cairn's 「這是跟團的時刻表」 (its SP-582, `gpx_files.group_schedule`) as a share link carries it.

`GET /api/gpx/<id>/meta?share=` answers `group_schedule: boolean` (snake_case). The owner ticking it is
said by a person, so the import's 「這是團的時間表」 starts ticked whatever the other signals say — even
on a route older than Cairn's 「who wrote each time」 (legacy) — and the trip is kept as the group's, so
a push never writes it back (SP-575 Q1, SP-816's origin). false / missing says nothing either way.

The fake Cairn of test_cairn_import_sp537 (127.0.0.1): nothing here reaches the real Cairn.
"""
import json
from pathlib import Path

from backend.engine import cairn as CA
from backend.engine import event_gpx as EG
from backend.tests.test_cairn_import_sp537 import (TOKEN, cairn, plan_client, store)  # noqa: F401
from backend.tests.test_group_itinerary_sp576 import GROUP

ROOT = Path(__file__).resolve().parents[2]
URL = "/api/v1/plan/events/jm/gpx/cairn"


def sig(pois, **meta):
    return CA.signals(CA.schedule_visits(pois), meta)


def _estimates():
    """GROUP with only each day's start typed: Cairn's own pace model wrote the rest (no signal)."""
    return [{**x, "visits": [{**v, "etaSource": "user" if v["kind"] == "depart" else "estimate"} for v in x["visits"]]}
            for x in GROUP]


def test_the_mark_in_cairn_ticks_it_whatever_the_other_signals_say():
    est = _estimates()
    assert sig(est, uploaded_at="2026-09-01")["suggest"] is False
    g = sig(est, uploaded_at="2026-09-01", group_schedule=True)
    assert g["marked"] is True and g["suggest"] is True
    # an older route: who wrote the times can't be told — but the owner said so
    old = sig(GROUP, uploaded_at="2026-07-30 08:00:00", group_schedule=True)
    assert old["legacy"] is True and old["marked"] is True and old["suggest"] is True
    # SP-582 Q1 first wrote it groupSchedule: read as well
    assert sig(est, groupSchedule=True)["marked"] is True


def test_not_marked_or_an_older_cairn_says_nothing():
    est = _estimates()
    for meta in ({}, {"group_schedule": False}, {"group_schedule": 1}, {"group_schedule": "true"}):
        g = sig(est, uploaded_at="2026-09-01", **meta)
        assert g["marked"] is False and g["suggest"] is False, meta
    # false does not untick what the times say
    assert sig(GROUP, uploaded_at="2026-09-01", group_schedule=False)["suggest"] is True


def test_the_import_starts_ticked_and_keeps_the_trip_as_the_groups(cairn, plan_client, store):  # noqa: F811
    from backend.accounts import cairn_push as CP
    cairn.pois = _estimates()
    cairn.meta = {**cairn.meta, "uploaded_at": "2026-07-01 08:00:00", "group_schedule": True}
    r = plan_client.post(URL, json={"url": cairn.link})
    assert r.status_code == 200 and TOKEN not in r.text
    sc = r.json()["cairn"]["schedule"]
    assert sc["group"] is True and sc["signals"]["marked"] is True and sc["signals"]["legacy"] is True
    src = EG.get("jm")["source"]
    assert src["group"] is True and src["schedule"]["group"] is True and src["schedule"]["signals"]["marked"] is True
    # SP-816: a push makes a trip of our own, never onto the group's (Cairn would answer 409)
    assert CP.origin("jm")["group"] is True and CP.target("jm") == ("new", None)
    # the person unticks the comparison here: the trip is still the group's in Cairn
    assert plan_client.put("/api/v1/plan/events/jm/gpx/group", json={"group": False}).status_code == 200
    assert EG.get("jm")["source"]["schedule"]["group"] is False and CP.origin("jm")["group"] is True


def test_a_marked_trip_without_times_is_still_the_groups(cairn, plan_client, store):  # noqa: F811
    from backend.accounts import cairn_push as CP
    cairn.meta = {**cairn.meta, "group_schedule": True}                     # SP-537's trip: nights, no times
    j = plan_client.post(URL, json={"url": cairn.link}).json()
    assert j["cairn"]["schedule"] is None and j["event_days"] == 3
    assert EG.get("jm")["source"]["group"] is True and CP.origin("jm")["group"] is True


def test_an_unmarked_trip_is_not_the_groups(cairn, plan_client, store):  # noqa: F811
    from backend.accounts import cairn_push as CP
    cairn.meta = {**cairn.meta, "group_schedule": False}
    assert plan_client.post(URL, json={"url": cairn.link}).status_code == 200
    assert "group" not in EG.get("jm")["source"] and CP.origin("jm")["group"] is False


def test_the_page_says_why_it_is_ticked():
    html = (ROOT / "backend" / "static" / "races.html").read_text("utf-8")
    assert 'if (g.marked) out.push(T("grp.sig_marked"))' in html
    # the guesses against it are not said when the owner marked it
    assert "if (g.legacy && !g.marked)" in html and "else if (!g.marked && g.n_estimate > g.n_user)" in html
    zh = json.loads((ROOT / "backend" / "static" / "i18n" / "zh-TW" / "races.json").read_text("utf-8"))
    en = json.loads((ROOT / "backend" / "static" / "i18n" / "en" / "races.json").read_text("utf-8"))
    assert "這是跟團的時刻表" in zh["grp.sig_marked"] and en["grp.sig_marked"] and en["grp.sig_marked"] != zh["grp.sig_marked"]
