"""
SP-699 (the calculator's side): races are managed in 賽事管理 only.

  * 賽事計算機 has no Cairn import (with a race picked or without), no 「存到『X』」, no 「推到 Cairn」; it keeps
    the race picker, the day ends clicked on the profile (saved back to the race) and a link to 賽事管理
    next to the picker;
  * 「這是登山社／團排好的時間表」 is asked in 賽事管理's editor popup with the Cairn import (the why in the ?);
    the calculator only says what its 跟團對照 uses, read from the race's GPX, and a first look at such a
    race takes the group's time and day starts as the import did (SP-576 / 580).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend.tests.test_cairn_import_sp537 import (cairn, calc_client, plan_client, store)  # noqa: F401
from backend.tests.test_group_itinerary_sp576 import GROUP

STATIC = Path(__file__).resolve().parents[1] / "static"


def _calc() -> str:
    return (STATIC / "racepower.html").read_text("utf-8")


def _races() -> str:
    return (STATIC / "races.html").read_text("utf-8")


def test_the_calculator_keeps_the_picker_the_day_ends_and_a_link_to_the_race_manager():
    html = _calc()
    # the link sits next to the race picker (not far off in the header)
    target = html[html.index('<section id="target-sec"'):html.index('<div class="trip bf" id="trip-box"')]
    after = target[target.index('<select id="event"'):]
    assert re.match(r'<select id="event"[^>]*></select></label>\s*<!--.*?-->\s*<a id="to-races" href="/api/v1/app/races/page"',
                    after, re.S)
    header = html[html.index("<header>"):html.index("</header>")]
    assert 'id="to-races"' not in header
    # nothing in it changes a race but the day ends
    for gone in ('id="gpx-attach"', "attachGpx", "LAST_FILE", 'id="cairn-url"', 'id="cairn-go"', "importCairn",
                 "/course/cairn", "/gpx/cairn", 'id="cairn-push"', 'id="cp-dlg"', "cpushRefresh",
                 'id="grp-ask"', "function setGroup", "/gpx/group", "/api/v1/plan/events/${encodeURIComponent(eid)}/gpx`, { method: \"POST\""):
        assert gone not in html, gone
    assert "function saveEventSplits" in html and "/gpx/splits" in html
    for loc in ("zh-TW", "en"):
        cat = json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))
        assert cat["to_races"] and cat["to_races_tip"] and cat["course.event_tip"]
        assert not [k for k in cat if k.startswith("cpush.")]
        assert not {"cairn.label", "cairn.go", "grp.ask", "grp.ask_sub"} & set(cat)


def test_a_first_look_at_a_group_race_takes_its_timetable():
    html = _calc()
    choose = html[html.index("async function chooseEvent"):html.index("async function addPastEvent")]
    assert "if (await loadEventGpx(e.id)) applyGroupDefaults();" in choose
    fn = html[html.index("function applyGroupDefaults"):html.index("// ---------------- the plan event's stored GPX")]
    assert "if (!sc?.group" in fn and "schedPrefill(sc)" in fn and 'S.trip = "group"' in fn
    note = html[html.index("function renderGroupNote"):html.index("function applyGroupDefaults")]
    assert 'T("grp.note_on")' in note and 'T("grp.note_off")' in note and '$("to-races").href' in note
    assert "COURSE?.event_id" in note                                  # only a race's timetable, read only
    # the 跟團對照 still goes with the plan when the race's timetable is the group's
    assert "if (S.csrc === \"gpx\" && COURSE?.group?.group) body.group_schedule" in html


def test_the_race_popup_asks_with_the_import_in_plain_words():
    html = _races()
    assert 'id="ed-grp"' in html
    js = html.split("<script>", 1)[1]
    imp = js[js.index("async function importCairnNow"):js.index("function groupWhy")]
    assert "renderGroupAsk();" in imp
    opener = js[js.index("async function openEditor"):js.index("async function saveEvent")]
    assert "renderGroupAsk();" in opener
    ask = js[js.index("function renderGroupAsk"):js.index("async function setGroup")]
    assert "evGpx?.source?.schedule" in ask
    assert 'class="help"' in ask and 'T("grp.tip", { why })' in ask              # the why only in the ?
    assert 'T("grp.sub", { days, n: pts.length })' in ask
    st = js[js.index("async function setGroup"):js.index('$("ci-go").onclick')]
    assert "/gpx/group" in st and '"PUT"' in st and "box.checked = !on" in st     # a failure unticks it again
    zh = json.loads((STATIC / "i18n/zh-TW/races.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n/en/races.json").read_text("utf-8"))
    assert zh["grp.ask"] == "這是登山社／團排好的時間表：幫我比對跟不跟得上"
    assert set(k for k in zh if k.startswith("grp.")) == set(k for k in en if k.startswith("grp."))


def test_the_answer_set_in_the_race_manager_is_what_the_calculator_reads(cairn, calc_client, plan_client):  # noqa: F811
    cairn.pois = GROUP
    cairn.meta = {**cairn.meta, "uploaded_at": "2026-09-01 08:00:00"}
    j = plan_client.post("/api/v1/plan/events/jm/gpx/cairn", json={"url": cairn.link}).json()
    assert j["gpx"]["source"]["schedule"]["group"] is True                # the popup reads e.gpx.source.schedule
    r = plan_client.put("/api/v1/plan/events/jm/gpx/group", json={"group": False})
    assert r.status_code == 200 and r.json()["gpx"]["source"]["schedule"]["group"] is False
    ev = next(e for e in plan_client.get("/api/v1/plan").json()["events"] if e["id"] == "jm")
    assert ev["gpx"]["source"]["schedule"]["group"] is False
    c = plan_client.post("/api/v1/racepower/course/event/jm", json={}).json()
    assert c["schedule"]["group"] is False and c["schedule"]["legs"]
    plan_client.put("/api/v1/plan/events/jm/gpx/group", json={"group": True})
    assert plan_client.post("/api/v1/racepower/course/event/jm", json={}).json()["schedule"]["group"] is True
