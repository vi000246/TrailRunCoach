"""static/hints.js under node (SP-482; docs/research/ui-hint-audit.md §4.4, §5.2): the five-step filter in
the three levels × the three classes, the cap of 3 with 「還有 N 則提醒」, the order, 「知道了」 and a changed
condition, old plain strings, and the HTML in zh-TW / en. Synthetic hints only (shaped like
engine/hints.make's, built from its catalog); skipped without node."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine import hints as H

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _run(expr: str, locale: str = "zh-TW", demo: bool = False, **data):
    js = r"""
const vm = require("vm"), fs = require("fs");
const ctx = { DATA: JSON.parse(process.argv[3]), console };
ctx.window = ctx;
if (process.argv[5] === "1") ctx.TRC_SESSION = { mode: "demo" };
const cat = JSON.parse(fs.readFileSync(process.argv[1] + "/i18n/" + process.argv[4] + "/common.json", "utf8"));
ctx.I18N = { t: (k, p) => {
  let v = cat[k.replace(/^common\./, "")];
  if (v && typeof v === "object") v = p && p.n === 1 ? v.one : v.other;
  return String(v ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m));
} };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1] + "/hints.js", "utf8"), ctx);
vm.runInContext("OUT = (" + process.argv[2] + ");", ctx);
console.log(JSON.stringify(ctx.OUT));
"""
    r = subprocess.run([NODE, "-e", js, str(STATIC), expr, json.dumps(data), locale, "1" if demo else "0"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _f(hints, level="standard", acks=(), cap=3):
    out = _run("(() => { const r = Hints.filter(DATA.h, {level: DATA.level, acks: DATA.acks, cap: DATA.cap});"
               " const ids = (a) => a.map((x) => x.text); return {must: ids(r.must), shown: ids(r.shown),"
               " more: ids(r.more), detail: ids(r.detail), hidden: ids(r.hidden)}; })()",
               h=hints, level=level, acks=list(acks), cap=cap)
    return out


def _bucket(out, text):
    return next(k for k, v in out.items() if text in v)


# one hint of each display a row can have, per class: (id of a catalog row with that class and symbols)
ROWS = {
    1: ["OV8a", "GL4", "RC14"],                     # ●●●
    2: ["OV5", "OV4", "OV1", "GL7", "OV8d"],        # ●◐▷ · ●▷▷ · ●◐◐ · ▷▷▷ · ●▷✕
    3: ["OV3", "OV8i", "AN8", "AN13"],              # ●▷✕ · ●▷✕ · ✕✕✕ · ●●●
}
WANT = {"●": "shown", "◐": "shown", "▷": "detail", "✕": "hidden"}


@pytest.mark.parametrize("level", H.LEVELS)
def test_three_levels_by_three_classes(level):
    hs = [H.make(hid, hid) for c in (1, 2, 3) for hid in ROWS[c]]
    out = _f(hs, level, cap=99)
    i = H.LEVELS.index(level)
    for c in (1, 2, 3):
        for hid in ROWS[c]:
            want = "must" if c == 1 else WANT[H.CATALOG[hid].show[i]]
            assert _bucket(out, hid) == want, (level, hid)


def test_class_1_shows_in_every_level_whatever_its_symbols_and_is_never_capped():
    # OV8c 碳水負荷週 (●●▷) and RC2 降雨 (◐◐◐, condition not met) are class 1: every level
    hs = [H.make("OV8c", "carb"), H.make("RC2", "rain", smart=False), H.make("OV6", "low", cls=1)] + \
        [H.make("OV8a", f"red{i}") for i in range(4)] + [H.make("OV1", f"r{i}") for i in range(5)]
    for level in H.LEVELS:
        out = _f(hs, level)
        assert set(out["must"]) == {"carb", "rain", "low", "red0", "red1", "red2", "red3"}, level
        assert len(out["shown"]) == 3 and len(out["more"]) == 2, level   # the cap is for reminders only


def test_more_than_three_reminders_show_three_and_fold_the_rest():
    hs = [H.make("OV1", f"r{i}") for i in range(5)] + [H.make("OV8a", "red")]
    out = _f(hs, "standard")
    assert out["shown"] == ["r0", "r1", "r2"] and out["more"] == ["r3", "r4"] and out["must"] == ["red"]
    assert _f(hs[:3], "standard")["more"] == []
    assert _f(hs, "standard", cap=2)["shown"] == ["r0", "r1"]


def test_the_order_priority_then_newer_then_as_given():
    hs = [H.make("OV1", "other6"),                                  # prio 6
          H.make("OV8d", "plan2", prio=2),
          H.make("GL5", "todo5"),                                   # prio 5
          {**H.make("OV1", "old6"), "week_start": "2026-10-05"},
          {**H.make("OV1", "new6"), "week_start": "2026-10-12"},
          H.make("OV8a′", "inj1")]                                   # prio 1 (●▷▷: full only)
    out = _f(hs, "full", cap=99)
    assert out["shown"] == ["inj1", "plan2", "todo5", "new6", "old6", "other6"]
    must = _f([H.make("RC14", "race3"), H.make("GL1", "data4"), H.make("OV8a", "safety1"), H.make("GL4", "plan2")])
    assert must["must"] == ["safety1", "plan2", "race3", "data4"]


def test_smart_condition_decides_a_half_circle():
    hs = [H.make("OV1", "on"), H.make("OV1", "off", smart=False), H.make("OV4", "always")]
    std = _f(hs, "standard")
    assert std["shown"] == ["on"] and set(std["detail"]) == {"off", "always"}
    full = _f(hs, "full")                                          # ● in 完整: the condition does not matter
    assert full["shown"] == ["on", "off", "always"]


def test_got_it_puts_away_until_the_condition_changes():
    a = H.make("OV1", "same week", fp="w41")
    out = _f([a], "standard", acks=[{"id": "OV1", "fp": "w41"}])
    assert out["detail"] == ["same week"] and out["shown"] == []
    changed = H.make("OV1", "next week", fp="w42")                 # the condition changed: back again
    assert _f([changed], "standard", acks=[{"id": "OV1", "fp": "w41"}])["shown"] == ["next week"]
    # a class-1 hint ignores 「知道了」 unless its row allows it (OV8b, the B-race warning, R7)
    red = H.make("OV8a", "red")
    assert _f([red], acks=[{"id": "OV8a", "fp": red["fp"]}])["must"] == ["red"]
    b = H.make("OV8b", "B race", fp="b1")
    assert _f([b], acks=[{"id": "OV8b", "fp": "b1"}])["detail"] == ["B race"]
    assert _f([H.make("OV8b", "B race moved", fp="b2")], acks=[{"id": "OV8b", "fp": "b1"}])["must"] == ["B race moved"]


def test_old_plain_strings_show_as_before():
    out = _f(["舊的警告一", "舊的警告二"], "lean")
    assert out["shown"] == ["舊的警告一", "舊的警告二"]
    n = _run("Hints.norm('x')")
    assert (n["cls"], n["show"], n["ack"], n["id"]) == (2, "●●●", False, None)
    assert _run("Hints.text({text: 'a'}) + Hints.text('b') + Hints.text(null)") == "ab"


def test_the_html_box_cap_line_link_and_got_it_buttons():
    hs = [H.make("OV8b", "B 賽太近", fp="b1"), H.make("OV8a", "疼痛紅燈")] + [H.make("OV1", f"提醒{i}") for i in range(5)]
    r = _run("(() => { const r = Hints.render(DATA.h, {level: 'standard', acks: []}); return r; })()", h=hs)
    html = r["html"]
    assert html.count('class="hint-must"') == 1 and html.index("B 賽太近") < html.index("提醒0")
    assert "還有 2 則提醒 ▸" in html and "提示太多？" in html and "/api/v1/wko5/settings#hint-level" in html
    assert html.count('data-hint-ack="OV8b"') == 1 and 'data-hint-ack="OV8a"' not in html   # only ackable ones
    assert html.count("知道了") == 1 + 5                                     # OV8b + every reminder
    assert r["counts"] == {"must": 2, "shown": 3, "more": 2, "detail": 0, "hidden": 0}
    acked = _run("Hints.render(DATA.h, {level: 'standard', acks: [{id: 'OV8b', fp: 'b1'}]})", h=hs[:1])
    assert acked["html"] == "" and "B 賽太近" in acked["detailHtml"] and "再提醒我" in acked["detailHtml"]
    assert 'data-hint-undo="1"' in acked["detailHtml"]
    few = _run("Hints.render(DATA.h, {level: 'standard'}).html", h=hs[2:4])
    assert "還有" not in few and "提示太多" not in few                       # no fold line, no link
    # the demo has no settings page: no 「提示太多？」, no 「知道了」 (it can't save)
    demo = _run("Hints.render(DATA.h, {level: 'standard'}).html", demo=True, h=hs)
    assert "還有 2 則提醒" in demo and "提示太多" not in demo and "data-hint-ack" not in demo


def test_the_html_in_english():
    hs = [H.make("OV1", f"r{i}") for i in range(4)]
    en = _run("Hints.render(DATA.h, {level: 'standard'}).html", locale="en", h=hs)
    assert "More reminders: 1 ▸" in en and "Too many tips?" in en and "Got it" in en
    # a plain string: the page's catalog keeps strings only (i18n/pages.py), no plural objects
    assert isinstance(json.loads((STATIC / "i18n" / "en" / "common.json").read_text("utf-8"))["hints.more"], str)


def test_text_is_escaped():
    out = _run("Hints.render(DATA.h, {level: 'full'}).html", h=[H.make("OV1", "<b>x</b>")])
    assert "<b>x</b>" not in out and "&lt;b&gt;x&lt;/b&gt;" in out
