"""SP-954: 圖表分析 shows power by THAT day's power state (owner 2026-10-27, SP-950 Q2-3).

On the demo athlete (Stryd; a CP test about three weeks ago, the races long ago — so the runs before the
test had a stale CP, and a few runs carry watch power only):

  * a run on a fresh-CP day: the 心率與功率 card has the power line, the average / weighted power tiles and the
    CP reference line; the 有氧 page has 功率區間時間; no 「當時 CP」 note;
  * a run on a stale-CP day: the note 「當時 CP 已 N 天沒測」, the power line and tiles stay, no CP line, no
    功率區間時間;
  * a watch-power run: heart rate only (the card is 心率, no power tiles), no note;
  * a charts range across both: the drift chart on power blanks the stale days' points and shades them
    (`power_spans` 「CP 過期」), the page draws without errors;
  * no power meter (③, the power state mocked as the API gives it): no CP test in the 課表 dialog, no 「功率」
    目標用 and no power library template on the 範本 page, no CP-test glossary term on 總覽 — and all of it
    back with a meter.

Run: ./e2e.sh -k power_display_sp954 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

VIEWER = _server.PAGES["viewer"]
VIEW = "單次活動判讀"
HIGHLIGHTS, AEROBIC = 0, 1                 # views/workout.json: 本次重點, 有氧／心率飄移


def _rows(page) -> list[dict]:
    r = page.request.get("/api/v1/app/workouts?begin=2000-01-01&end=2100-01-01")
    assert r.ok
    return r.json()


def _pick(rows, mode: str) -> dict:
    hit = [a for a in rows if (a.get("pw") or {}).get("mode") == mode and a.get("kind") == "road"]
    assert hit, f"the demo athlete has no road run with power view {mode!r}"
    return hit[0]


def _open(page, a: dict, dash: int) -> dict:
    open_page(page, f"{VIEWER}?view={VIEW}&dash={dash}&workout={a['index']}")
    done = """() => { const g = document.querySelector('#grid'), cs = g.querySelectorAll(':scope > section.card');
                      return g.dataset.page != null && cs.length && ![...cs].some((c) => c.querySelector('.loading')); }"""
    page.wait_for_function(done, timeout=120_000)
    page.wait_for_timeout(500)
    return page.evaluate("""() => {
      const note = document.querySelector('#pw-note');
      const cards = [...document.querySelectorAll('#grid > section.card')];
      const hp = cards.find((c) => c.querySelector('.ac-stats'));
      return { note: note && !note.hidden ? note.textContent : null,
               titles: cards.map((c) => c.querySelector('h2')?.firstChild?.textContent?.trim()),
               hp: hp ? { title: hp.querySelector('h2')?.firstChild?.textContent?.trim(),
                          stats: hp.querySelector('.ac-stats').innerText, legend: hp.querySelector('.ac-leg')?.innerText } : null };
    }""")


def test_a_fresh_cp_day_shows_full_power(app):
    page, _ = app
    a = _pick(_rows(page), "full")
    st = _open(page, a, HIGHLIGHTS)
    assert st["note"] is None, st
    assert st["hp"] and st["hp"]["title"].startswith("心率與功率"), st
    assert "平均功率" in st["hp"]["stats"] and "加權功率" in st["hp"]["stats"], st
    assert "AeT／LTHR／CP" in st["hp"]["legend"], st
    assert "功率區間時間" in _open(page, a, AEROBIC)["titles"]


def test_a_stale_cp_day_keeps_the_power_line_but_no_percent_cp(app):
    page, _ = app
    a = _pick(_rows(page), "stale")
    st = _open(page, a, HIGHLIGHTS)
    n = a["pw"]["days"]
    assert st["note"] and f"當時 CP 已 {n} 天沒測" in st["note"], st
    assert st["hp"]["title"].startswith("心率與功率") and "平均功率" in st["hp"]["stats"] and "加權功率" in st["hp"]["stats"], st
    assert "CP" not in st["hp"]["legend"], st                       # AeT／LTHR only: no CP reference line
    aer = _open(page, a, AEROBIC)
    assert "功率區間時間" not in aer["titles"] and "心率區間時間" in aer["titles"], aer
    # the server agrees: the power-zones card is off that activity (power_display.day_power_only)
    views = page.request.get("/api/v1/app/views").json()
    single = next(v for v in views if v["name"] == VIEW)
    pz = next(c for c in single["dashboards"][AEROBIC]["charts"] if c.get("pwday"))
    r = page.request.get(f"/api/v1/app/views/{VIEW}/dashboards/{AEROBIC}/charts/{pz['index']}?workout={a['index']}")
    assert r.ok and r.json().get("hide") is True


def test_a_watch_power_run_is_heart_rate_only(app):
    page, _ = app
    a = _pick(_rows(page), "none")
    st = _open(page, a, HIGHLIGHTS)
    assert st["note"] is None, st
    assert st["hp"]["title"].startswith("心率") and "功率" not in st["hp"]["title"] and "平均功率" not in st["hp"]["stats"], st


def test_a_charts_range_blanks_power_on_the_stale_days(app):
    page, _ = app
    rows = _rows(page)
    fresh, stale = _pick(rows, "full"), _pick(rows, "stale")
    begin, end = rows[-1]["start"][:10], rows[0]["start"][:10]
    views = page.request.get("/api/v1/app/views").json()
    found = None
    for v in views:
        for d in v.get("dashboards") or []:
            for c in d["charts"]:
                if c.get("power_basis") and c.get("kind") == "athlete":
                    found = found or (v, d, c)
    assert found, "no chart with a 配速／功率 toggle"
    v, d, c = found
    url = f"/api/v1/app/views/{v['name']}/dashboards/{d['index']}/charts/{c['index']}?begin={begin}&end={end}&basis=power"
    res = page.request.get(url).json()
    spans = res.get("power_spans") or []
    assert any(s["kind"] == "stale" and s["label"] == "CP 過期" and s["from"] <= stale["start"][:10] <= s["to"]
               for s in spans), spans
    assert not any(s["from"] <= fresh["start"][:10] <= s["to"] for s in spans), spans
    pw = [s for s in res["series"] if s["data"].get("kind") == "points" and "power" in (s.get("expression") or "")]
    for s in pw:
        for x, y in s["data"]["points"]:
            if any(sp["from"] <= x[:10] <= sp["to"] for sp in spans):
                assert y is None, (x, y)
    # the page draws that chart over the range on power without an error (shaded stretches on the plot)
    open_page(page, f"{VIEWER}?view={v['name']}&dash={d['index']}&begin={begin}&end={end}")
    page.evaluate("(k) => { const p = JSON.parse(localStorage.getItem('charts.basis') || '{}'); p[k] = 'power'; "
                  "localStorage.setItem('charts.basis', JSON.stringify(p)); }", f"{v['name']}|{d['index']}|{c['index']}")
    open_page(page, f"{VIEWER}?view={v['name']}&dash={d['index']}&begin={begin}&end={end}")
    page.wait_for_function("""() => { const cs = document.querySelectorAll('#grid > section.card');
        return cs.length && ![...cs].some((c) => c.querySelector('.loading')); }""", timeout=120_000)


def _meter(page, meter: bool) -> None:
    """The power state as GET /plan/power-state and /sync/settings give it with / without a power meter."""
    import json as _j

    def ps(route):
        body = route.fetch().json()
        route.fulfill(json={**body, "meter": meter, "state": body.get("state") if meter else "none",
                            "power": bool(body.get("power")) and meter})

    def us(route):
        body = route.fetch().json()
        route.fulfill(body=_j.dumps({**body, "power_meter": meter, **({} if meter else {"use_power": False})}),
                      content_type="application/json")
    page.route("**/api/v1/plan/power-state", ps)
    page.route("**/api/v1/sync/settings", us)


@pytest.fixture
def app_nosw(e2e_browser, e2e_server, request):
    """conftest's `app` with service workers blocked: once the app's service worker controls a page, its fetches
    no longer pass page.route and the power-state mock would miss (as test_interval_flag_sp917_e2e)."""
    from backend.tests.e2e import _browser
    ctx = e2e_browser.new_context(base_url=e2e_server.url, viewport={"width": 1400, "height": 900},
                                  locale="zh-TW", timezone_id="Asia/Taipei", service_workers="block")
    ctx.set_default_timeout(20_000)
    _browser.blank_tiles(ctx, e2e_server.url)
    page = ctx.new_page()
    watch = _browser.Watch(page, e2e_server.url)
    try:
        yield page, watch
        problems = watch.problems()
        if problems:
            pytest.fail(f"{request.node.name}: browser errors\n  " + "\n  ".join(problems[:20]))
    finally:
        ctx.close()


@pytest.mark.parametrize("meter", [False, True])
def test_no_power_meter_hides_cp_tests_and_power_options(app_nosw, meter):
    page, _ = app_nosw
    _meter(page, meter)
    # 課表: the new-session dialog's 「哪一種測試」
    open_page(page, _server.PAGES["schedule"])
    page.wait_for_function("() => typeof D !== 'undefined' && D && typeof S !== 'undefined' && 'noMeter' in S",
                           timeout=60_000)
    page.evaluate("() => openDlg(null, D.today)")
    cp_hidden = page.evaluate("() => document.querySelector('#sd-tkind option[value=\"cp\"]').hidden")
    assert cp_hidden is (not meter)
    # 範本: 目標用 and the library
    open_page(page, _server.PAGES["templates"])
    page.wait_for_function("() => document.querySelectorAll('#tm-items li').length > 0", timeout=60_000)
    lib = page.evaluate("() => [...document.querySelectorAll('#tm-items button[data-key]')].map((b) => b.dataset.key)")
    keys = [k.split(":")[-1] for k in lib]
    assert any(k.startswith("pal_") or k.startswith("stryd_") for k in keys) is meter, keys
    page.click("#tm-new")
    hidden = page.evaluate("() => document.querySelector('#tm-basis button[data-b=\"power\"]').hidden")
    assert hidden is (not meter)
    # 總覽: the glossary
    open_page(page, _server.PAGES["overview"])
    page.wait_for_function("() => typeof GLOSS !== 'undefined'", timeout=60_000)
    page.wait_for_timeout(1500)
    g = page.evaluate("() => ({ cp: 'CP 測試' in GLOSS, aet: GLOSS['AeT'] })")
    assert g["cp"] is meter and (("固定配速跑 40 分鐘" in g["aet"]) is (not meter)), g
