"""SP-502: the English overview, 能力頁 (我的訓練 › 能力) and 課表 week view in a real browser on the demo
athlete — none of the Chinese the SP-497 / SP-484 / SP-483 screenshots showed (the overview opened in Chinese
first: the status is cached, the English page must not get the Chinese one) — and the 有氧／無氧訓練效果 tile
two columns wide, its name and line whole and clear of its ?, at desktop and phone width, in both languages.

What is checked is the page's own words (the chrome, headings, the KPI tiles, the indicator names, the week
strip, the power curve's note / text / legend), not the user's data: activity and session names and the
stored plan's session texts stay as they were written. The rest of the overview that is still Chinese (the
5 區 ladders, indicator details, 做了什麼's tiles) is listed on the ticket.

Run: ./e2e.sh -k sp502 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import no_loading, open_page  # noqa: E402

pytestmark = pytest.mark.e2e

OVERVIEW, SCHEDULE, VIEWER = _server.PAGES["overview"], _server.PAGES["schedule"], _server.PAGES["viewer"]

# the shown text of every element matching the selectors that still has Chinese in it
CJK_IN = r"""(sels) => {
  const cjk = /[㐀-鿿　-〿！-｠]/;
  const on = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  const out = [];
  for (const sel of sels) for (const el of document.querySelectorAll(sel)) {
    const s = (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    if (on(el) && cjk.test(s)) out.push(`${sel}: ${s.slice(0, 100)}`);
  }
  return out;
}"""
# the strings the screenshots showed, anywhere on the page
LISTED = ("短期負荷", "長期負荷持平", "維持中", "詳細", "擬合不出", "MMP 曲線", "天新高", "PD 模型曲線", "沒有超越先前的最佳",
          "那週", "調整課表偏好", "實際", "週統計", "目前")


def _listed_on(page) -> list[str]:
    body = page.evaluate("() => document.body.innerText")
    return [s for s in LISTED if s in body]


def test_overview_in_english_after_the_chinese_one(app):
    page, _ = app
    open_page(page, OVERVIEW + "?lang=zh-TW")
    no_loading(page, "#ind")
    assert "長期訓練負荷（Chronic load）" in page.inner_text("#kpis")          # the Chinese status is now cached
    open_page(page, OVERVIEW + "?lang=en")
    no_loading(page, "#kpis")
    no_loading(page, "#ind")
    no_loading(page, "#wk-days")
    left = page.evaluate(CJK_IN, ["#kpis .k", "#kpis .sub .num", "#kpis .st", "#ind .ic .top", "#ind details.more > summary",
                                  "#ind-sub", "#wk-days .dh", "#wk-days .rest", "#wk-days .mn", "section h2 > span[data-i18n]",
                                  "#wk-h .to-sched", "#unit button", "#now", "#todo-sub", "#actions b",
                                  "#week > details.more > summary"])
    assert not left, "Chinese on the English overview:\n  " + "\n  ".join(left)
    assert not _listed_on(page), _listed_on(page)
    assert "acute load" in page.inner_text("#kpis")                         # the 負荷平衡 tile's 「短期負荷 16」


def test_capacity_page_power_curve_in_english(app):
    page, _ = app
    open_page(page, f"{VIEWER}?view=我的訓練&dash=3&lang=en")
    page.wait_for_function("""() => { const cs = document.querySelectorAll('#grid section.card');
                                      return cs.length && ![...cs].some((c) => c.querySelector('.loading')); }""",
                           timeout=120_000)
    page.wait_for_timeout(500)
    curve = page.locator("#grid section.card").first            # 跑步功率曲線 is the dashboard's first chart
    assert "Running power curve" in curve.inner_text()
    left = page.evaluate(CJK_IN, ["#grid section.card:first-of-type"])
    legend = page.evaluate("""() => { const d = document.querySelector('#grid section.card [_echarts_instance_]');
        const o = d && echarts.getInstanceByDom(d)?.getOption(); if (!o) return [];
        return [].concat(o.legend || []).flatMap((l) => l.data || []).map((x) => typeof x === "string" ? x : x?.name)
          .concat((o.series || []).map((s) => s.name)); }""")
    assert legend and not [x for x in legend if x and any("㐀" <= ch <= "鿿" for ch in x)], legend
    assert "New bests, last 7 days" in legend
    assert not left, left
    assert not _listed_on(page), _listed_on(page)


@pytest.mark.parametrize("width", [1400, 390])
def test_schedule_week_view_in_english(app, width):
    page, _ = app
    page.set_viewport_size({"width": width, "height": 900})
    open_page(page, SCHEDULE + "?lang=en")
    page.locator("#cal .cal, #cal .ag-day").first.wait_for()
    if width > 600:
        page.click("#view-seg [data-view=week]")
        page.locator("#cal .cal.week").wait_for()
    page.wait_for_timeout(500)
    left = page.evaluate(CJK_IN, ["#go-today", "#view-seg", "#phase-now", "#cal .cal-head", "#cal .sum .r > span:first-child",
                                  "#cal .sum .tag", "#cal .sum .php", "#cal .phl", "#pnotes .wk-l", "#pnotes [data-open-prefs]",
                                  "#cal .ag-day .d", "#cal .rest"])
    assert not left, f"Chinese on the English 課表 ({width} px):\n  " + "\n  ".join(left)
    # a done session's line: 「實際 …」 -> "Actual: …"
    xs = page.evaluate("() => [...document.querySelectorAll('#cal .c-x')].map((e) => e.textContent.trim())")
    assert not [x for x in xs if x.startswith("實際")], xs
    assert not _listed_on(page), _listed_on(page)


TILE = r"""() => {
  const c = [...document.querySelectorAll('.rv-c')].find((x) => x.classList.contains('w2'));
  if (!c) return null;
  const k = c.querySelector('.k'), kt = c.querySelector('.kt'), q = c.querySelector(':scope > .qtip'), s = c.querySelector('.s');
  const kr = k.getBoundingClientRect(), qr = q.getBoundingClientRect(), one = c.parentElement.querySelector('.rv-c:not(.w2):not(.zn)');
  return {label: kt.textContent, sub: s ? s.textContent : "", labelCut: kt.scrollWidth > kt.clientWidth + 1,
          subCut: s ? s.scrollWidth > s.clientWidth + 1 : false, clear: kr.right <= qr.left + 0.5,
          w: c.getBoundingClientRect().width, one: one ? one.getBoundingClientRect().width : 0,
          row: c.parentElement.getBoundingClientRect().width};
}"""


@pytest.mark.parametrize("lang,width", [("en", 1400), ("en", 390), ("zh-TW", 1400), ("zh-TW", 390)])
def test_tis_tile_is_wide_and_whole(app, lang, width):
    page, _ = app
    rows = page.request.get("/api/v1/wko5/workouts").json()
    rows = rows if isinstance(rows, list) else rows.get("workouts") or []
    w = next(a for a in rows if a.get("power_source"))
    # the demo athlete's PD model can't be fitted (the tile says 算不出): its real line, the longer one, from the answer
    sub = "1–10, not the watch's 0–5" if lang == "en" else "1–10，跟手錶的 0–5 不同"

    def patch(route):
        r = route.fetch()
        j = r.json()
        for c in j.get("cards") or []:
            if c.get("id") == "tis":
                c.update(value="4／2", sub=sub)
        route.fulfill(response=r, json=j)

    page.route("**/dashboards/0/charts/0?*", patch)
    page.set_viewport_size({"width": width, "height": 900})
    open_page(page, f"{VIEWER}?view=單次活動判讀&dash=0&workout={w['index']}&lang={lang}")
    page.locator(".rv-c").first.wait_for(timeout=60_000)
    t = page.evaluate(TILE)
    assert t, "no wide tile"
    assert t["label"] == ("Aerobic / Anaerobic training effect" if lang == "en" else "有氧／無氧訓練效果")
    assert t["sub"] == sub
    assert not t["labelCut"] and not t["subCut"] and t["clear"], t
    if width > 600:
        assert t["w"] > 1.8 * t["one"], t                 # two columns
    else:
        assert t["w"] > 0.95 * t["row"], t                # the whole row on a phone
