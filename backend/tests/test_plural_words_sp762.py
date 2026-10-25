"""SP-762: English counts read "1 day", "3 days" everywhere — one mechanism for every language file: a text's
`{n|day|days}` is the word for the `n` parameter (the first form for exactly 1, else the second), read the same
by the server (backend/i18n.plural_words) and the pages (static/i18n/i18n.js). Every en text that had
"{x} days" / weeks / hours / times … uses it (the catalogs, not card by card). Repo files only."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import i18n
from backend.i18n import translate
from backend.scripts import i18n_extract as X

BACKEND = Path(__file__).resolve().parents[1]
STATIC = BACKEND / "static"
NODE = shutil.which("node")
WORDS = r"(days|weeks|hours|times|months|years|runs|sessions|minutes|activities|races|tests|files|nights|reps|trips)"
BARE = re.compile(r"\{(\w+)(?:[:!][^}]*)?\}\s" + WORDS + r"\b")


def test_plural_words_rule():
    pw = i18n.plural_words
    assert pw("{n} {n|day|days} to race", {"n": 1}) == "{n} day to race"
    assert pw("{n} {n|day|days} to race", {"n": 2}) == "{n} days to race"
    assert pw("{n} {n|day|days}", {"n": "1"}) == "{n} day"                 # a pre-formatted count
    assert pw("{h} {h|hour|hours}", {"h": 1.0}) == "{h} hours"             # a measurement, not a count
    assert pw("{a} {a|week|weeks}, {b} {b|run|runs}", {"a": 1, "b": 3}) == "{a} week, {b} runs"
    assert pw("{n} {n|day|days}", {}) == "{n} days"                        # no parameter: the plural


def test_translate_uses_it():
    assert translate("賽前 {n} 天", "en", n=1) == "1 day to race"
    assert translate("賽前 {n} 天", "en", n=5) == "5 days to race"
    assert translate("賽前 {n} 天", "zh-TW", n=1) == "賽前 1 天"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_the_page_t_reads_the_same():
    js = (STATIC / "i18n" / "i18n.js").read_text("utf-8")
    prog = ("global.window = {__I18N__: {locale: 'en', catalog: {'x.d': '{n} {n|day|days} to race', "
            "'x.two': '{a} {a|week|weeks}, {b} {b|run|runs}'}}, localStorage: null};"
            "global.document = {documentElement: {getAttribute: () => 'en'}, cookie: ''};"
            + js +
            ";console.log(JSON.stringify([window.t('x.d', {n: 1}), window.t('x.d', {n: 3}), window.t('x.d', {n: '1'}),"
            "window.t('x.two', {a: 1, b: 2})]))")
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == ["1 day to race", "3 days to race", "1 day to race", "1 week, 2 runs"]


def _en_texts():
    for p in sorted((STATIC / "i18n" / "en").glob("*.json")):
        for k, v in json.loads(p.read_text("utf-8")).items():
            if isinstance(v, str):
                yield f"{p.stem}.{k}", v
    for k, v in json.loads((BACKEND / "i18n" / "locales" / "en.json").read_text("utf-8")).items():
        if isinstance(v, str):
            yield k, v


def test_no_bare_count_word_left_in_english():
    left = [(k, v) for k, v in _en_texts() if BARE.search(i18n.PLURAL_RX.sub("", v))]
    # a word right after a count must be a plural word ({n} {n|day|days}), not a bare "days"
    left = [(k, v) for k, v in left if any(not re.search(r"\{%s\|" % m.group(1), v) for m in BARE.finditer(v))]
    assert left == [], left[:10]


def test_placeholders_still_match_with_plural_words():
    assert X.py_fields("{n} {n|day|days}") == {"n"} and X.js_fields("{n} {n|day|days}") == {"n"}
