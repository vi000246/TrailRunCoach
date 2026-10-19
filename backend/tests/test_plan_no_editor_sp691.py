"""SP-691: the season plan page (賽事周期) has no race editor any more — each target race (百岳 too)
links to 賽事管理, which opens that race's editor (?event=<id>&edit=1); every field the plan page used
to edit (days, 賽制, per-day rows, GPX, Cairn, cutoff / summit, 土徑比, pack, poles) is edited there."""
from __future__ import annotations

import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text("utf-8")


def _cat(loc: str, ns: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / f"{ns}.json").read_text("utf-8"))


def test_the_plan_page_has_no_editor():
    plan = _read("plan.html")
    for gone in ('id="evform"', "data-edit=", "data-del=", 'id="gpxpick"', "data-gpxup", "data-gpxdel",
                 "data-gpxsplit", "data-cairnup", 'call("PUT", "/events"', "DaySplit.open("):
        assert gone not in plan, gone
    # each race: its name and an 編輯 link go to 賽事管理, the editor link with &edit=1
    assert 'const RACES = "/api/v1/app/races/page"' in plan
    assert re.search(r"\?event=\$\{encodeURIComponent\(e\.id\)\}\$\{edit \? \"&edit=1\" : \"\"\}", plan)
    assert "raceHref(e, true)" in plan and "<a href=\"${raceHref(e)}\">${esc(e.name)}</a>" in plan
    assert 'data-i18n="plan.ev.manage"' in plan
    # the phases stay editable here (周期, not a race)
    assert 'call("PUT", "/phases"' in plan


def test_the_race_manager_opens_the_editor_from_the_link_and_saves_every_field():
    races = _read("races.html")
    assert 'q.get("edit") === "1"' in races and "openEditor(row)" in races
    assert 'call("PUT", "/events", body)' in races
    for name in ("name", "date", "kind", "priority", "days", "race_format", "distance_km", "climbing_m", "est_hours",
                 "cutoff_hours", "summit_km", "trail_pct", "pack_kg", "poles", "result", "note"):
        assert re.search(rf'<(input|select|textarea)[^>]*\bname="{name}"', races), name
    # the stored event goes first: what the form doesn't show (heat) is kept
    assert "const body = { ...(S.edit || {}) };" in races
    # the GPX of a race: upload / replace / 分日 / delete in its detail
    for k in ("data-gpxup", "data-gpxsplit", "data-gpxdel", "/gpx/file"):
        assert k in races, k


def test_the_race_manager_page_gets_the_plan_texts():
    from backend.i18n import pages as P
    assert "plan" in P.namespaces_for("races")
    cat = P.page_catalog(P.namespaces_for("races"), "en")
    assert cat["plan.ev.gpx_help"] and cat["races.ed.days"]


def test_the_new_texts_in_both_languages():
    for loc in ("zh-TW", "en"):
        pl, rc = _cat(loc, "plan"), _cat(loc, "races")
        assert all(pl[f"ev.{k}"] for k in ("manage", "edit_in_races", "edit_in_races_tip", "add_in_races")), loc
        assert all(rc[k] for k in ("ed.days", "ed.pack", "ed.pack_tip", "ed.adv", "ed.saved", "gpx.h", "gpx.none",
                                   "gpx.up", "gpx.up_tip", "gpx.replace", "gpx.dl", "gpx.del", "gpx.del_confirm",
                                   "gpx.saved", "gpx.deleted", "gpx.failed")), loc
        assert "ed.more" not in rc                    # 「在賽事周期頁編輯」 is no longer true
    assert "賽事管理" in _cat("zh-TW", "plan")["ev.gpx_nosplit"]
