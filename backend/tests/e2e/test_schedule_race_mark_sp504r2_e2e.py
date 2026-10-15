"""SP-504 R2: on the 課表, in a real browser on the demo athlete —
* the A race's day has the mark 「A 賽：X」 like the overview (SP-517), and no 賽事 phase label beside it (the
  mark says it; the phase colour stays), week / month / list views, English 「A race: X」;
* the hike on a 百岳 trip's day (the demo's C trip 「百岳 3 日（訓練行程）」) has the hike icon, not the race flag;
  a trail race's run keeps the flag;
* an A 百岳 trip's mark is 「A 行程：X」 (the demo has none: raceT on a synthetic row).

Run: ./e2e.sh -k sp504r2 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

SCHEDULE = _server.PAGES["schedule"]
A_RACE = "示範山徑越野 50K"
TRIP = "百岳 3 日（訓練行程）"


# the first icon tile of an HTML string, as the browser serialises it (a script then the expression after it)
NORM = """(() => { const norm = (h) => { const d = document.createElement("div"); d.innerHTML = h;
  return d.querySelector(".sc-ic").outerHTML; }; return """
NORM_END = "; })()"


def _ready(page, q=""):
    open_page(page, SCHEDULE + q)
    page.wait_for_selector("#cal .cal, #cal .agenda", timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


def _race(page, pri: str, back: int = 0, ahead: int = 110) -> dict:   # (the calendar: ≤ 120 days)
    r = page.evaluate("""async ([p, back, ahead]) => { const t = D.today;
      const b = await (await fetch(`${PAPI}/calendar?start=${addDays(t, -back)}&end=${addDays(t, ahead)}`)).json();
      return (b.races || []).find((x) => x.priority === p); }""", [pri, back, ahead])
    assert r, pri
    return r


def _show(page, day: str, view: str):
    page.evaluate("([d, v]) => { S.view = v; S.anchor = d; S.sel = d; load(); }", [day, view])
    page.wait_for_function("(d) => (D.races || []).some((r) => r.start <= d && d <= r.end)", arg=day, timeout=60_000)
    page.wait_for_selector("#calwrap:not(.loading)", timeout=60_000)


@pytest.mark.parametrize("view", ["week", "month"])
def test_the_a_race_day_has_the_mark_and_no_phase_label(app, view):
    page, _ = app
    _ready(page, "?lang=zh-TW")
    r = _race(page, "A")
    assert r["name"] == A_RACE and r["kind"] == "race", r
    _show(page, r["start"], view)
    cell = page.locator(f'.cell[data-day="{r["start"]}"]')
    mark = cell.locator(".rcm")
    expect(mark).to_have_count(1)
    expect(mark).to_have_text(f"A 賽：{A_RACE}")
    assert mark.get_attribute("title").startswith(f"A 賽「{A_RACE}」：這季的目標賽事"), mark.get_attribute("title")
    # the 賽事 phase label is not repeated beside the mark (the day is still in the 賽事 phase)
    assert page.evaluate("(d) => phaseOf(d).kind", r["start"]) == "event"
    expect(cell.locator(".phl")).to_have_count(0)
    assert "賽事" not in cell.inner_text()
    # no session card on the A race's day (SP-492 / SP-504), the day's tooltip names the race
    expect(cell.locator(".chip:not(.act)")).to_have_count(0)
    assert f"A 賽：{A_RACE}" in cell.get_attribute("title")


def test_the_a_race_list_view_and_english(app):
    page, _ = app
    page.set_viewport_size({"width": 390, "height": 844})       # the narrow (list) view
    _ready(page, "?lang=zh-TW")
    r = _race(page, "A")
    _show(page, r["start"], "week")
    expect(page.locator(f'.ag-day[data-day="{r["start"]}"] .rcm')).to_have_text(f"A 賽：{A_RACE}")
    page.set_viewport_size({"width": 1400, "height": 900})
    _ready(page, "?lang=en")
    _show(page, r["start"], "week")
    mark = page.locator(f'.cell[data-day="{r["start"]}"] .rcm')
    expect(mark).to_have_text(f"A race: {A_RACE}")
    assert mark.get_attribute("title").startswith(f"A race “{A_RACE}”: the season's goal race")


def test_an_a_trip_mark_says_trip(app):
    page, _ = app
    for lang, mark, tip in [("zh-TW", "A 行程：X", "A 行程「X」：這季的目標行程"), ("en", "A trip: X", "A trip “X”: the season's goal trip")]:
        _ready(page, f"?lang={lang}")
        got = page.evaluate("""() => { const r = { kind: "baiyue", priority: "A", name: "X" };
          return [raceT(r, "race.mark", { p: "A", name: "X" }), raceTip(r)]; }""")
        assert got[0] == mark and got[1].startswith(tip), got


def test_the_trip_activity_has_the_hike_icon(app):
    page, _ = app
    _ready(page, "?lang=zh-TW")
    r = _race(page, "C", back=60, ahead=0)
    assert r["kind"] == "baiyue" and r["name"] == TRIP, r
    _show(page, r["start"], "week")
    hike, flag = page.evaluate(NORM + '[norm(SessChip.icon("hike")), norm(SessChip.icon("race"))]' + NORM_END)
    assert hike != flag
    chips = page.locator(".chip.act.race")
    assert chips.count() >= 1
    for i in range(chips.count()):
        assert chips.nth(i).locator(".sc-ic").evaluate("(e) => e.outerHTML") == hike
    # a trail race's run keeps the race flag (a synthetic activity row through actHtml)
    run = page.evaluate(NORM + """norm(actHtml({ index: 999, date: D.today, moving_s: 3600, tss: 80, distance_km: 10,
        race: { id: "x", name: "Y", priority: "B", kind: "race" } }))""" + NORM_END)
    assert run == flag


def test_the_overview_trip_activity_has_the_hike_icon(app):
    """The overview's 「本週」 (SP-517) writes the race-day activity with the same icons."""
    page, _ = app
    open_page(page, _server.PAGES["overview"] + "?lang=zh-TW")
    page.wait_for_selector("#wk-days .dc", timeout=60_000)
    got = page.evaluate(NORM + """[...["baiyue", "race"].map((kind) => norm(extraHtml({ index: 999, date: "2026-01-01",
        moving_s: 3600, tss: 80, race: { id: "x", name: "Y", priority: "C", kind } }))),
      norm(SessChip.icon("hike")), norm(SessChip.icon("race"))]""" + NORM_END)
    assert got[0] == got[2] and got[1] == got[3] and got[0] != got[1], got
