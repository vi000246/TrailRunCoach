"""SP-892: the plans page's comparison has a 團員腳程估算 row (SP-805's quota "participants"): the
free tier's number comes from the same limits the counter enforces (GET /api/v1/billing limits, the
admin's quota.free.participants), a subscription is unlimited; both languages."""
from __future__ import annotations

import json
import re
from pathlib import Path

from backend import membership as MS

STATIC = Path(__file__).resolve().parents[1] / "static"


def _cat(lang: str) -> dict:
    return json.loads((STATIC / "i18n" / lang / "plans.json").read_text("utf-8"))


def test_the_comparison_has_a_participants_row_numbered_by_the_limits():
    # SP-846: the rows are written into the page from membership.PLAN_ROWS (billing/plan_table.py)
    from backend.billing import plan_table as PT
    assert "<!--fill:plan_rows-->" in (STATIC / "plans.html").read_text("utf-8")
    html = PT.rows_html("plans", "zh-TW", limits={})
    row = re.search(r'<tr><td data-i18n="plans\.row\.participants">.*?</tr>', html).group(0)
    assert 'data-limit="free.participants" data-tpl="row.participants.free_n"' in row
    assert 'data-i18n="plans.row.participants.paid"' in row
    # the default the page shows before /api/v1/billing answers is the quota's own default
    q = MS.QUOTAS[MS.FREE]["participants"]
    assert (q.limit, q.period) == (10, "month") and "participants" not in MS.QUOTAS[MS.PAID]
    zh, en = _cat("zh-TW"), _cat("en")
    assert zh["row.participants.free"] == f"每月 {q.limit} 位" and zh["row.participants.free_n"] == "每月 {n} 位"
    assert zh["row.participants.paid"] == "不限" and "團員腳程估算" in zh["row.participants"]
    assert en["row.participants.free"] == f"{q.limit} people a month"
    assert en["row.participants.free_n"] == {"one": "{n} person a month", "other": "{n} people a month"}
    assert en["row.participants.paid"] == "unlimited" and "pace" in en["row.participants"]
    # the same words as 本期用量 (SP-718) and the plan table the docs mirror
    assert zh["use.participants"] in zh["row.participants"]
    assert any(r[0].startswith("團員腳程估算") and r[1] == "每月 10 位" for r in MS.PLAN_TABLE)


def test_the_billing_view_hands_the_page_the_participants_limit():
    lim = MS.plan_limits()
    assert lim["free"]["participants"]["limit"] == 10 and lim["free"]["participants"]["period"] == "month"
    assert "participants" not in lim["subscriber"]
