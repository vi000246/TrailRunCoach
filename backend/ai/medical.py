"""Words that make an AI 問答 question a medical one (SP-785: 醫療相關一律提醒就醫). Data, not UI text
(backend/i18n/allow_cjk.txt lists this file): matched against the question, in any language.
The answer then carries `medical: true` and the dialog shows its fixed see-a-doctor line, whatever
the model wrote."""
from __future__ import annotations

import re

WORDS_ZH = ("痛", "疼", "受傷", "傷病", "扭傷", "拉傷", "骨折", "發炎", "腫", "麻", "抽筋", "胸悶", "頭暈",
            "暈眩", "心悸", "喘不過", "發燒", "生病", "感冒", "醫生", "就醫", "看醫", "吃藥", "藥", "復健", "血")
WORDS_EN = ("pain", "hurt", "injur", "ache", "sore", "sprain", "strain", "fracture", "swell", "numb", "cramp",
            "chest", "dizz", "faint", "palpitat", "breathless", "fever", "sick", "doctor", "medic",
            "physio", "blood")

_RE = re.compile("|".join(re.escape(w) for w in WORDS_ZH + WORDS_EN), re.IGNORECASE)


def is_medical(text: str) -> bool:
    return bool(text and _RE.search(text + " "))
