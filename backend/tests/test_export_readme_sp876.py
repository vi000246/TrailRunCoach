"""SP-876: the README of 下載我的資料 says what account.json holds, including SP-765's sign-up
source and the 疊石 Cairn card's daily counts (both languages)."""
from __future__ import annotations

from backend import i18n
from backend.accounts import export as X
from backend.accounts import xp


def test_the_readme_explains_the_signup_source_and_the_card_counts():
    for loc, words in (("zh-TW", ("註冊來源", "互推小卡")), ("en", ("where you signed up from", "Cairn card"))):
        with i18n.use_locale(loc):
            text = X._readme().decode("utf-8")
        line = next(ln for ln in text.splitlines() if ln.startswith("account.json"))
        for w in words + ("signup_source", "cross_product_events"):
            assert w in line, (loc, w)
        for src in xp.SOURCES:                      # every value signup_source can hold
            assert src in line, (loc, src)
        for ev in xp.EVENTS:                        # every event the counts are kept for
            assert ev in line, (loc, ev)
        if loc == "en":
            assert not any("一" <= ch <= "鿿" for ch in text), "the English README has no Chinese"
