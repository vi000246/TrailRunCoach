"""SP-692: 賽事管理 lists the races in a table like 活動列表 (sortable headers, 編輯 per row); adding and
editing are a popup, which also deletes the race and holds 疊石 Cairn's import / push; the list's 🧮 計算機
label (it did nothing) is gone, the race's link to the calculator stays.
SP-699: a Cairn import from 賽事管理 updates the race itself — its distance, climb and days follow the route."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from backend.engine import planning as P
from backend.engine import event_gpx as EG
from backend.tests.test_cairn_import_sp537 import cairn, store  # noqa: F401 — the fixtures

STATIC = Path(__file__).resolve().parents[1] / "static"


def _html() -> str:
    return (STATIC / "races.html").read_text("utf-8")


def _script() -> str:
    return _html().split("<script>", 1)[1]


def _cat(loc: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / "races.json").read_text("utf-8"))


# ---- SP-692: the table ---------------------------------------------------------------------

def test_the_list_is_a_sortable_table_like_the_activity_list():
    html = _html()
    assert '<table id="tbl">' in html and 'id="thead"' in html and '<tbody id="list">' in html
    js = _script()
    # the columns, each header a sort button (date, name, type, distance, climb, status, finish, result)
    for k in ("date", "name", "kind", "km", "gain", "st", "time", "res"):
        assert re.search(rf'\{{ k: "{k}", cls: "c-{k}', js), k
    assert 'class="sort" data-sort=' in js and "aria-sort" in js
    assert "S.sort = S.sort.k === c.k ? { k: c.k, dir: -S.sort.dir }" in js      # again: the other way
    # 編輯 on each race of the plan, straight into the popup
    assert 'class="edit" data-edit=' in js and "openEditor(row(b.dataset.edit))" in js
    # a phone folds the less needed columns into the line under the name; the select sorts there
    phone = html.split("@media (max-width: 699px)")[-1]
    assert "#tbl .c-kind, #tbl .c-gain, #tbl .c-st, #tbl .c-res { display: none; }" in phone
    assert "#tbl td.c-name .sub { display: block; }" in phone and "#sort { display: inline-block; }" in phone


def test_no_calculator_label_in_the_list_but_the_race_keeps_its_calculator_button():
    js = _script()
    assert "calc.short" not in js and "calc.saved_tip" not in js
    for loc in ("zh-TW", "en"):
        assert "calc.short" not in _cat(loc)
    assert 'href="${esc(d.calc_url)}"><button>🧮' in js                # the race's 用計算機算這場 / 看計算機的計算


def test_the_popup_adds_edits_deletes_and_holds_cairn():
    html = _html()
    ed = html.split('<dialog id="ed"', 1)[1].split("</dialog>", 1)[0]
    assert 'class="edhead"' in ed and 'id="ed-x"' in ed                  # title bar with ×
    assert 'id="ed-save"' in ed and 'id="ed-del"' in ed                  # save / delete in the popup
    # Cairn: the share-link import and the push, in the popup — no separate Cairn dialog or race button
    for el in ('id="ed-cairn"', 'id="evcairn"', 'id="ci-go"', 'id="cp-wrap"', 'id="cp-go"'):
        assert el in ed, el
    assert 'id="cairn-dlg"' not in html and 'id="d-cairn"' not in html and 'id="d-del"' not in html
    js = _script()
    assert '$("ed-del").hidden = !ev;' in js and '$("cp-wrap").hidden = !ev;' in js   # a new race: nothing to delete / push
    assert "delRace({ name: e.name, event_id: e.id })" in js and 'method: "DELETE"' in js
    assert "CPUSH + encodeURIComponent(S.edit.id)" in js
    # the explanations sit behind a ? (hover / focus), not as paragraphs
    assert re.search(r'<span class="help"(?: data-manual="[^"]+")? tabindex="0">\?<span class="tip"><span data-i18n="races.cairn.import_how">', ed)  # SP-842: may link the manual


def test_the_new_texts_in_both_languages():
    for loc in ("zh-TW", "en"):
        c = _cat(loc)
        for k in ("col.date", "col.name", "col.kind", "col.km", "col.gain", "col.st", "col.time", "col.res", "col.act",
                  "edit_label", "add_short", "deleted", "cairn.on_save", "cairn.imported_ev"):
            assert c.get(k), (loc, k)
    assert "距離、爬升、天數" in _cat("zh-TW")["cairn.import_how"]


# ---- SP-699: the race takes the imported route -------------------------------------------------

@pytest.fixture
def client(store, monkeypatch):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import plan as A
    monkeypatch.setattr(A, "_notify", lambda thresholds: None)
    # a race typed before the route existed: 2 days, 18 km, 900 m
    A.put_event(A.EventIn(id="jm", name="嘉明湖", date="2027-03-02", kind="baiyue", days=2, distance_km=18,
                          climbing_m=900, gpx_pending=True))
    app = FastAPI()
    app.include_router(A.router)
    return TestClient(app)


def test_a_cairn_import_puts_the_routes_distance_climb_and_days_on_the_race(cairn, client):  # noqa: F811
    r = client.post("/api/v1/plan/events/jm/gpx/cairn", json={"url": cairn.link})
    assert r.status_code == 200, r.text
    j = r.json()
    row = EG.get("jm")
    e = P.Plan.load().events[0]
    assert e.distance_km == pytest.approx(row["km"], abs=0.01) and e.distance_km == pytest.approx(30, abs=0.2)
    assert e.climbing_m == round(row["gain_m"])                          # the typed 18 km / 900 m are replaced
    assert e.days == 3 and len(e.day_plan) == 3                          # the nights → the days
    assert e.date == "2027-03-02" and e.name == "嘉明湖"                  # the date and name stay
    # the answer carries the race as stored: the popup and the list show it at once
    assert j["event"]["id"] == "jm" and j["event"]["distance_km"] == e.distance_km and j["event"]["days"] == 3


def test_a_plain_gpx_upload_still_keeps_the_typed_totals(cairn, client):  # noqa: F811
    up = client.post("/api/v1/plan/events/jm/gpx", files={"file": ("t.gpx", cairn.gpx)})
    assert up.status_code == 200
    e = P.Plan.load().events[0]
    assert (e.distance_km, e.climbing_m) == (18, 900)                    # SP-533: only empty ones are filled


def test_the_popup_fills_in_what_the_import_changed():
    js = _script()
    assert "S.edit = { ...e, ...r.event, gpx: r.gpx };" in js
    assert "f.distance_km.value = r.event.distance_km" in js and "f.days.value = r.event.days" in js
    assert "loadPlanEvents(); load()" in js                               # the list and the open race reload
