"""SP-884 「問 AI」 on the calculator's result and on a 課表 session card sends the numbers and the
judgments, not only the card's text: askai.js fires askai:describe on the card just before it reads it,
racepower.html / schedule.html fill data-ai-facts / data-ai-rules from their current data.

In a real browser on the demo athlete (owner mode, no API key). The model is never called: POST
/api/v1/ai/ask is answered by the test (page.route), which records what the dialog sent.
Run: ./e2e.sh -k sp884 (see conftest.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="Playwright not installed (requirements-dev.txt)")
from playwright.sync_api import expect  # noqa: E402

from backend.tests.e2e import _server  # noqa: E402
from backend.tests.e2e.test_askai_sp786_e2e import _mock_ask  # noqa: E402
from backend.tests.e2e.test_racepower_effort_sp697_e2e import _calc, _event  # noqa: E402

pytestmark = pytest.mark.e2e


def _ask_first_preset(page):
    page.locator(".askai-fab").click()
    dlg = page.locator(".askai-dlg")
    expect(dlg).to_be_visible()
    dlg.locator("[data-askai-presets] button").first.click()
    expect(dlg.locator(".askai-a")).to_have_count(1)
    return dlg


def test_the_calculator_result_sends_its_segments_and_rules(app):
    page, _ = app
    sent = _mock_ask(page)
    _calc(page, _event(page))
    page.locator("#results h2").first.click()           # the result is the card asked about
    dlg = _ask_first_preset(page)
    expect(dlg.locator("[data-askai-ctx-t]")).to_contain_text("預估結果")
    f = sent[-1]["focus"]
    assert f["kind"] == "calc"
    facts = {x["label"]: x["value"] for x in f["facts"]}
    assert facts["predicted moving time"].count(":") == 2 and facts["distance"].endswith(" km")
    assert "average power" in facts and "effort" in facts
    segs = [x for x in f["facts"] if x["label"].startswith("segment ")]
    n = int(facts["segments"].split(";")[0])
    assert segs and len(segs) == min(n, 40 - (len(f["facts"]) - len(segs)))     # the longest ones when they don't fit
    s0 = segs[0]["value"]
    assert "%" in s0 and "↑" in s0 and "split " in s0 and any(w in s0 for w in ("walk", "run"))
    assert len(f["facts"]) <= 40 and len(f.get("rules") or []) <= 20
    assert all(set(r) == {"text", "source"} and r["text"] for r in f.get("rules") or [])
    # the segments card describes all of its segments (no summary numbers)
    seg = page.evaluate("() => AskAI.focusFrom(document.getElementById('profile-sec'))")
    assert seg["kind"] == "calc" and seg["facts"][0]["label"] == "segments"
    assert all(x["label"].startswith("segment") for x in seg["facts"])


def test_a_session_card_sends_its_numbers_and_judgments(app):
    page, _ = app
    sent = _mock_ask(page)
    page.goto(_server.PAGES["schedule"])
    chip = page.locator("#cal .chip.sc[data-uid]").first
    expect(chip).to_be_visible(timeout=30_000)
    uid = chip.get_attribute("data-uid")
    chip.click()                                          # opens the session dialog; the card is the last clicked
    page.keyboard.press("Escape")
    expect(page.locator("#sd")).not_to_be_visible()
    _ask_first_preset(page)
    f = sent[-1]["focus"]
    assert f["kind"] == "session"
    facts = {x["label"]: x["value"] for x in f["facts"]}
    s = page.evaluate("(uid) => D.sessions.find((x) => x.uid === uid)", uid)
    assert facts["day"] == s["day"] and facts["type"]
    if s.get("minutes"):
        assert facts["planned minutes"] == str(s["minutes"])
    assert "planned load" in facts or s.get("tss_est") is None and s.get("tss") is None
    # a done session also sends what was run and its verdict as a rule (the first done one on the page)
    done = page.evaluate("""() => { const s = D.sessions.find((x) => x.state === 'done' && x.done_by && x.compliance);
                                    if (!s) return null;
                                    const el = document.querySelector(`#cal .chip.sc[data-uid="${CSS.escape(s.uid)}"]`);
                                    return el ? AskAI.focusFrom(el) : null; }""")
    if done:
        dl = {x["label"] for x in done["facts"]}
        assert {"what was run", "completion numbers"} <= dl and done.get("rules"), done
