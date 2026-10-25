"""SP-477: 圖表分析 › 單次活動 in English — every dashboard of 單次活動判讀, for a trail run, an
interval session and a CP test of the demo athlete, opened with ?lang=en: no Chinese left in what
the page shows (text on screen, the chart legends / axes / series names, the chrome's aria-labels).

Allowed: the activities' own names (the user's data, never translated) and the language switch's
「中文」 (a language is written in itself). Hover texts (`data-tip`, `title`) are not checked here: some
of the review's long explanations are still Chinese (listed on the ticket).

Run: ./e2e.sh -k activity_page_en (see conftest.py)."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e._browser import open_page  # noqa: E402

pytestmark = pytest.mark.e2e

VIEWER = _server.PAGES["viewer"]
CJK = re.compile(r"[㐀-鿿　-〿！-｠]")

# visible text nodes, the chart legends / axis / series names, the aria-labels of the page chrome
SCAN = r"""() => {
  const cjk = /[㐀-鿿　-〿！-｠]/, out = [];
  const shown = (el) => el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = w.nextNode());) {
    const p = n.parentElement, s = n.textContent.trim();
    if (!p || ["SCRIPT", "STYLE", "TEMPLATE", "OPTION"].includes(p.tagName) || p.closest("#sugbox, .sugbox")) continue;
    if (s && cjk.test(s) && shown(p)) out.push(s);
  }
  for (const el of document.querySelectorAll("#grid [aria-label], #crumb [aria-label], .tb2 [aria-label]"))
    if (cjk.test(el.getAttribute("aria-label")) && shown(el)) out.push("@aria-label " + el.getAttribute("aria-label"));
  if (window.echarts) document.querySelectorAll("[_echarts_instance_]").forEach((d) => {
    const o = echarts.getInstanceByDom(d)?.getOption(); if (!o) return;
    const names = [].concat(o.legend || []).flatMap((l) => l.data || [])
      .concat([].concat(o.xAxis || [], o.yAxis || []).map((a) => a.name))
      .concat((o.series || []).map((s) => s.name));
    for (const x of names) { const s = typeof x === "string" ? x : x?.name; if (s && cjk.test(s)) out.push("@chart " + s); }
  });
  return [...new Set(out)];
}"""


def _workouts(page) -> list[dict]:
    r = page.request.get("/api/v1/app/workouts")
    assert r.ok
    j = r.json()
    rows = j if isinstance(j, list) else j.get("workouts") or []
    assert rows, "the e2e athlete has no activities"
    return rows


def _pick(rows: list[dict]) -> dict[str, dict]:
    """A trail run (爬坡與地形), an interval session (間歇) and a CP test from the demo athlete."""
    trail = next(a for a in rows if "trail" in str(a.get("sport_type") or a.get("kind") or ""))
    iv = next(a for a in rows if (a.get("plan") or {}).get("kind") == "quality" or "間歇" in str(a.get("name")))
    cp = next((a for a in rows if "CP" in str(a.get("name"))), None)
    return {k: v for k, v in (("trail", trail), ("intervals", iv), ("cp", cp)) if v}


def _loaded(page, timeout: float = 120_000) -> None:
    page.wait_for_function(
        """() => { const cs = document.querySelectorAll('#grid section.card');
                   // a page with no chart for this activity (爬坡與地形 of a road run) says so instead
                   if (!cs.length) return !!document.querySelector('#grid > .empty:not([data-i18n])');
                   return ![...cs].some((c) => c.querySelector('.loading')); }""",
        timeout=timeout)


def _segments_built(page, idx: int, timeout_s: float = 120) -> list:
    """The 重複路段 card (static/segments_card.js, on 本次重點) lists the route index's segments this activity
    passed; the first request starts building the index and the card says 「still being built」 until it is
    there. Wait for it, so the card's segment names are checked whatever ran before (the compare page builds
    it too)."""
    import time
    t0 = time.time()
    while True:
        r = page.request.get(f"/api/v1/app/workouts/{idx}/segments")
        assert r.ok, r.status
        if r.json().get("built"):
            return r.json()["segments"]
        assert time.time() - t0 < timeout_s, "the route index was not built"
        page.wait_for_timeout(500)


def test_single_activity_page_is_english(app):
    page, _ = app
    rows = _workouts(page)
    names = {str(a.get("name") or "") for a in rows} | {str(a.get("sport_type") or "") for a in rows}
    picked = _pick(rows)
    assert {"trail", "intervals"} <= set(picked)
    # the demo trail run passes repeated segments: automatic names, written in Chinese on the server
    assert _segments_built(page, picked["trail"]["index"])
    left = {}
    for what, a in picked.items():
        for dash in range(7):                        # views/workout.json's 7 dashboards (閾值: SP-801)
            open_page(page, f"{VIEWER}?view=單次活動判讀&dash={dash}&workout={a['index']}&lang=en")
            _loaded(page)
            page.wait_for_timeout(800)                   # the cards drawn after their data (maps, segments)
            found = [s for s in page.evaluate(SCAN)
                     if s != "中文" and not any(n and n in s for n in names)]
            if found:
                left[f"{what} dash {dash}"] = found
    assert not left, "Chinese on the English single-activity page:\n" + "\n".join(
        f"  {k}: {v}" for k, v in left.items())
