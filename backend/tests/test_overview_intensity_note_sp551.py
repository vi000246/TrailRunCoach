"""SP-551: the overview's low-intensity week note (OV8e) said 「強度分配是 bad」 (an internal level name), sat in a red
frame (static/hints.js `.hint-must` around the class-1 notes) with a white click frame on its row, and opened onto
its own text again (overview.html noteHtml put the note's text in the body). Now: plain words in zh-TW and en, a
`warn_detail` the row opens onto (engine/quality_gate.low_share_detail → the note's `detail`), a note without a
detail is a plain row that cannot open, no extra frame around the class-1 group, focus only for the keyboard.
Synthetic data only; the node part is skipped without node."""
import json
import re
import shutil
import subprocess
import types
from pathlib import Path

import pytest

from backend.engine import quality_gate as QG
from backend.i18n import use_locale

STATIC = Path(__file__).resolve().parents[1] / "static"
PAGE = STATIC / "overview.html"
NODE = shutil.which("node")
LEVEL_WORDS = re.compile(r"\b(bad|good|watch)\b")
CJK = re.compile(r"[一-鿿]")


def _spec(tested: bool):
    from backend.tests.test_quality_gate import _gate
    g = _gate(phase=types.SimpleNamespace(kind="specific", start="2026-11-01", end="2026-11-30"))
    return {**g, "levels": {"intensity": "bad"}, "aet": {**(g.get("aet") or {}), "tested": tested}}


@pytest.mark.parametrize("tested", [True, False])
def test_specific_phase_note_is_plain_words_with_a_detail(tested):
    d = QG.week_decision(_spec(tested), "specific", "specific")
    assert d["allow"] and d["warn"].startswith("輕鬆跑心率偏高：")
    assert d["warn_detail"] and d["warn_detail"] != d["warn"]
    for s in (d["warn"], d["warn_detail"]):
        assert not LEVEL_WORDS.search(s), s
    # the detail says why and what it means for Zone 3 / Zone 5; an estimated AeT says so
    assert "AeT 以上" in d["warn_detail"] and "65%" in d["warn_detail"] and "3 區" in d["warn_detail"]
    assert ("估計值" in d["warn_detail"]) is (not tested)
    with use_locale("en"):
        e = QG.week_decision(_spec(tested), "specific", "specific")
    for s in (e["warn"], e["warn_detail"]):
        assert not CJK.search(s) and not re.search(r"\bis bad\b", s), s
    assert e["warn"].startswith("Easy-run HR is high: ")


@pytest.mark.parametrize("tested", [True, False])
def test_base_phase_guard_note_has_its_detail(tested):
    g = QG.guard(low_share=0.68, aet=150.0, aet_tested=tested)
    assert g["warn"].startswith("輕鬆跑心率偏高：低強度只有 68%")
    assert "68%" in g["warn_detail"] and "75%" in g["warn_detail"] and "150 bpm" in g["warn_detail"]
    assert ("估計值" in g["warn_detail"]) is (not tested) and ("AeT 是估計值" in g["warn"]) is (not tested)
    assert g["block"] is tested                         # a tested AeT keeps the Zone 5 floor (SP-39)
    with use_locale("en"):
        e = QG.guard(low_share=0.68, aet=150.0, aet_tested=tested)
    assert not CJK.search(e["warn"]) and not CJK.search(e["warn_detail"])
    # the power share: its own words, the same detail
    p = QG.guard(low_share=0.9, power_low_share=0.7, aet=150.0, aet_tested=tested)
    assert p["warn"].startswith("輕鬆跑功率偏高：跑步功率 < 80% CP 只有 70%") and "80% CP" in p["warn_detail"]
    # a projected week keeps the warning and its detail with the intensity block (week_decision, not first)
    if tested:
        gate = {**_spec(True), "kind": "base", "levels": {}, "guard": g, "state": "unlocked"}
        d = QG.week_decision(gate, "base", "base", first=False)
        assert d.get("warn_detail") == (g["warn_detail"] if d.get("warn") else "")


def test_the_overview_note_carries_the_detail():
    from backend.tests.test_overview import BOTH, _two_track_week
    from backend.engine import plan_prefs as PP
    g = QG.guard(low_share=0.6, aet=150.0, aet_tested=True)
    wp = _two_track_week({**BOTH, "guard": g}, PP.Prefs(quality=2))
    note = next(n for n in wp["notes"] if n.get("src") == "intensity")
    assert note["text"] == g["warn"] and note["detail"] == g["warn_detail"]


def test_no_level_name_in_the_gates_words():
    # drift and the non-base verdict said 「是 bad」／「不是 bad」 too
    src = (Path(QG.__file__)).read_text(encoding="utf-8")
    strings = re.findall(r'"([^"\n]*[一-鿿][^"\n]*)"', src)
    bad = [s for s in strings if LEVEL_WORDS.search(s)]
    assert bad == []
    d = QG.week_decision({**_spec(True), "levels": {"drift": "bad"}}, "specific", "specific")
    assert not d["allow"] and "心率飄移偏大" in d.get("z3_note", "") + d.get("note", "")


# ---- overview.html: the note row and its look -----------------------------------------------------------

def _note_html():
    page = PAGE.read_text(encoding="utf-8")
    m = re.search(r"const noteHtml = \(n\) => \{.*?\n\};\n", page, re.S)
    assert m, "noteHtml not found"
    return m.group(0)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_note_row_opens_only_onto_a_detail():
    js = r"""
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const gl = esc, I = (l) => `<i data-l="${l}"></i>`;
""" + _note_html() + r"""
const N = JSON.parse(process.argv[1]);
console.log(JSON.stringify(N.map(noteHtml)));
"""
    notes = [{"level": "watch", "text": "輕鬆跑心率偏高：AeT 是估計值，只是提醒，3 區、5 區照排", "detail": "近 4 週…為什麼"},
             {"level": "info", "text": "本週沒排 3 區：還沒解鎖", "detail": None},
             {"level": "watch", "text": "Easy-run HR is high: just a reminder", "detail": "Why: …"}]
    r = subprocess.run([NODE, "-e", js, json.dumps(notes)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    with_d, plain, en = json.loads(r.stdout)
    assert with_d.startswith('<details class="al watch">') and "<b>輕鬆跑心率偏高</b>" in with_d
    body = re.search(r'<div class="body">(.*?)</div></details>', with_d).group(1)
    assert body == "近 4 週…為什麼"                          # the detail, not the title again
    assert "<details" not in plain and plain.startswith('<div class="al info"><div class="al-row">')
    assert "<b>本週沒排 3 區</b>" in plain and "還沒解鎖" in plain
    assert "<b>Easy-run HR is high</b>" in en and '<span class="s">just a reminder</span>' in en


def test_no_red_frame_and_keyboard_only_focus():
    css = PAGE.read_text(encoding="utf-8")
    assert re.search(r"#wk-notes \.hint-must \{[^}]*border: 0", css)
    assert "details.al > summary:focus { outline: none; }" in css
    assert re.search(r"details\.al > summary:focus-visible \{ outline: 2px solid var\(--accent\)", css)
