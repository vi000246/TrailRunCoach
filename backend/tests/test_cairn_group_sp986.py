"""
SP-986 was: Cairn's 「這是跟團的時刻表」 (its SP-582, `gpx_files.group_schedule`) carried by a share link
ticked the import's 「這是團的時間表」 and kept the trip as the group's.

Cairn retired that mark (Cairn SP-582 R1): its share meta no longer has `group_schedule`, and TRC no longer
reads it (Cairn SP-1454). These pin the retirement — a stray `group_schedule` from an old Cairn changes
nothing — and that TRC's own signals and its own 「這是團的時間表」 answer are as before.

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


def test_cairns_old_mark_changes_no_signal():
    """`group_schedule` / `groupSchedule` from an old Cairn: no `marked`, and the suggestion is what the data says."""
    est = _estimates()
    for meta in ({"group_schedule": True}, {"groupSchedule": True}):
        g = sig(est, uploaded_at="2026-09-01", **meta)
        assert "marked" not in g and g["suggest"] is False, meta
        old = sig(GROUP, uploaded_at="2026-07-30 08:00:00", **meta)
        assert old["legacy"] is True and old["suggest"] is False, meta   # legacy: not ticked by itself, as before SP-986
    assert not hasattr(CA, "marked_group")


def test_the_data_signals_are_unchanged():
    """TRC's own guess (SP-577): times typed in a day tick it on a new route; a legacy route is not ticked by itself."""
    assert sig(GROUP, uploaded_at="2026-09-01")["suggest"] is True
    assert sig(GROUP, uploaded_at="2026-07-30 08:00:00")["suggest"] is False
    assert sig(_estimates(), uploaded_at="2026-09-01")["suggest"] is False


def test_an_import_with_cairns_old_mark_is_not_kept_as_the_groups(cairn, plan_client, store):  # noqa: F811
    from backend.accounts import cairn_push as CP
    cairn.pois = _estimates()
    cairn.meta = {**cairn.meta, "uploaded_at": "2026-09-01 08:00:00", "group_schedule": True}
    r = plan_client.post(URL, json={"url": cairn.link})
    assert r.status_code == 200 and TOKEN not in r.text
    sc = r.json()["cairn"]["schedule"]
    assert sc["group"] is False and "marked" not in sc["signals"]
    src = EG.get("jm")["source"]
    assert "group" not in src and "marked" not in src["schedule"]["signals"]
    assert CP.origin("jm")["group"] is False


def test_trcs_own_answer_still_makes_it_the_groups(cairn, plan_client, store):  # noqa: F811
    """The person ticks 「這是團的時間表」 here (SP-575 Q1): read only, a push makes a trip of our own — as before."""
    from backend.accounts import cairn_push as CP
    cairn.pois = _estimates()
    assert plan_client.post(URL, json={"url": cairn.link}).status_code == 200
    assert plan_client.put("/api/v1/plan/events/jm/gpx/group", json={"group": True}).status_code == 200
    assert EG.get("jm")["source"]["schedule"]["group"] is True
    assert CP.origin("jm")["group"] is True and CP.target("jm") == ("new", None)


def test_a_trip_kept_as_the_groups_before_stays_the_groups(cairn, plan_client, store):  # noqa: F811
    """A trip imported while Cairn still marked it has `source.group`: it stays read only (nothing writes it any more)."""
    from backend.accounts import cairn_push as CP
    assert plan_client.post(URL, json={"url": cairn.link}).status_code == 200
    EG.set_source("jm", group=True)
    assert CP.origin("jm")["group"] is True and CP.target("jm") == ("new", None)


def test_the_page_no_longer_mentions_cairns_mark():
    html = (ROOT / "backend" / "static" / "races.html").read_text("utf-8")
    assert "grp.sig_marked" not in html and "g.marked" not in html
    for lang in ("zh-TW", "en"):
        strings = json.loads((ROOT / "backend" / "static" / "i18n" / lang / "races.json").read_text("utf-8"))
        assert "grp.sig_marked" not in strings, lang
