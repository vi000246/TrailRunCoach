"""SP-922: the race calculator's 準確度 tab in English — the tab buttons, its heading, 重新回測 and the loading line
(server side, data-i18n), and everything renderBacktest writes itself (headers, titles, notes, intensity and
category names, the state line, each race's columns) through the racepower catalog's bt.* keys. The stored
back-test's own texts (pass / fail reasons, notes, a row's error) stay in the language it ran in. Chinese unchanged.

Reads repo files only (the page, the catalogs)."""
from __future__ import annotations

import json
import re

from backend.i18n import pages as P
from backend.scripts import i18n_extract as X

CJK = re.compile(r"[㐀-鿿　-〿！-｠]")
STATIC = X.ROOT / "backend" / "static"
START, END = "// ---------------- 準確度", "// ---------------- events: saved inputs"


def _src() -> str:
    return (STATIC / "racepower.html").read_text("utf-8")


def _cat(loc: str) -> dict:
    return json.loads((STATIC / "i18n" / loc / "racepower.json").read_text("utf-8"))


def _code() -> str:
    """The 準確度 script (groupFactorHtml, renderBacktest, 重新回測) without its comments."""
    s = _src()
    block = s[s.index(START):s.index(END)]
    out = []
    for line in block.splitlines():
        if line.lstrip().startswith("//"):
            continue
        out.append(re.sub(r"\s//\s.*$", "", line))
    return "\n".join(out)


def test_the_tab_and_its_heading_are_english():
    page = P.apply_catalog(_src(), P.page_catalog(P.namespaces_for("racepower"), "en"))
    tabs = re.search(r'<div class="seg tabs" id="tabs">(.*?)</div>', page).group(1)
    assert re.findall(r">([^<]+)</button>", tabs) == ["Calculate", "Accuracy"]
    sec = re.search(r'<section id="acc-sec"[^>]*>(.*?)</section>', page, re.S).group(1)
    text = re.sub(r"<[^>]+>", " ", sec)
    assert not CJK.search(text), text
    assert "Run the back-test again" in text and "Loading…" in text


def test_the_tab_script_writes_no_chinese_of_its_own():
    code = _code()
    bad = [ln.strip() for ln in code.splitlines() if CJK.search(ln)]
    assert not bad, bad


def test_every_key_the_tab_uses_is_in_both_catalogs():
    code = _code()
    keys = set(re.findall(r'"((?:bt|sg)\.[a-z_.]+)"', code))          # TR("bt.x") and TR(c ? "bt.y" : "bt.z")
    keys |= {f"bt.cat.{c}" for c in ("road", "trail")} | {f"bt.row.{c}" for c in ("road", "trail", "hike", "test")}
    keys |= {f"bt.cls.{c}" for c in ("easy", "steady", "race", "all")} | {"bt.grp.running", "bt.grp.walking"}
    keys |= {"bt.tab", "bt.sub", "bt.run", "bt.loading"}
    assert len(keys) > 60
    zh, en = _cat("zh-TW"), _cat("en")
    missing = sorted(k for k in keys if k not in zh or k not in en)
    assert not missing, missing
    bad = sorted(k for k in keys if CJK.search(en[k]))
    assert not bad, [(k, en[k]) for k in bad]
    # the same placeholders in both languages ({n|run|runs} is the English plural word of n)
    ph = lambda s: sorted(set(re.findall(r"\{(\w+)(?:\|[^}]*)?\}", s)))      # noqa: E731
    assert [k for k in keys if ph(zh[k]) != ph(en[k])] == []


def test_chinese_keeps_the_words_it_had():
    zh = _cat("zh-TW")
    # the SP-867 screenshot's headers and the old literals, word for word
    assert zh["bt.h.abs"] == "|誤差| 中位" and zh["bt.h.bias"] == "偏差（中位）"
    assert zh["bt.cap.title"] == "比賽預測回測（能力模型）" and zh["bt.ter.title"] == "地形模型回測（給實際功率，看各坡度的速度）"
    assert zh["bt.queued"].replace("{n}", "2") == "排隊中：前面還有 2 個回測，輪到時會自動開始"
    assert zh["bt.grp.walking"].replace("{p}", "50%") == "走為主（≥ 50% 時間步頻 < 130 spm）"
    assert [zh[f"bt.cls.{k}"] for k in ("easy", "steady", "race")] == ["輕鬆", "穩定", "比賽強度"]
    page = P.apply_catalog(_src(), P.page_catalog(P.namespaces_for("racepower"), "zh-TW"))
    assert '>準確度</button>' in page and '>重新回測</button>' in page
