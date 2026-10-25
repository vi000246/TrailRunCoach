"""SP-877: the monthly 互推漏斗 (SP-765's backend/scripts/xp_funnel.py) on the admin page's 營運報表 tab,
computed when it is asked for — the site's owner sees last month's funnel without running the script
on the NAS. Admins only; counts only."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from backend.scripts import xp_funnel as XF
from backend.tests.test_admin_sp351 import F, P, _fresh_site, client  # noqa: F401 — fixtures
from backend.tests.test_membership_sp330 import server  # noqa: F401 — fixtures

URL = "/api/v1/admin/xp-funnel"


def _last_month(today: dt.date) -> dt.date:
    return today.replace(day=1) - dt.timedelta(days=1)


def test_the_admin_sees_last_months_funnel_without_running_the_script(client, server):  # noqa: F811
    today = dt.datetime.now(XF.TZ).date()
    last = _last_month(today)
    day = last.replace(day=5).isoformat()
    a = server.create_user(email="a@x.tw")
    b = server.create_user(email="b@x.tw")
    server.set_signup_source(b.id, "cairn")
    for uid, ev in ((a.id, XF.SHOWN), (a.id, XF.SHOWN), (a.id, XF.CLICK), (a.id, XF.LINKED), (b.id, XF.SHOWN)):
        server.xp_add(uid, ev, day, 200)
    r = client.get(URL)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["accounts"] is True and d["last_month"] == last.strftime("%Y-%m") and d["retain_days"] == 30
    row = next(m for m in d["months"] if m["month"] == d["last_month"])
    assert row["funnel"] == {"saw": 2, "shown_times": 3, "clicked": 1, "dismissed": 0, "linked": 1,
                             "click_rate": 0.5, "link_rate": 0.5}
    assert row["retained_30d"]["linked"]["n"] == 1 and row["retained_30d"]["control"]["n"] == 1
    # the same numbers as the script on the same accounts.db
    want = {m["month"]: m for m in XF.report(XF.read(server.path()), today)}
    assert row == want[row["month"]]
    # newest first
    assert [m["month"] for m in d["months"]] == sorted((m["month"] for m in d["months"]), reverse=True)
    assert "a@x.tw" not in r.text and "b@x.tw" not in r.text              # counts only


def test_no_events_yet_is_an_empty_list(client):  # noqa: F811
    d = client.get(URL).json()
    assert d["accounts"] is True and d["months"] == []


def test_only_an_admin_sees_the_funnel(client):  # noqa: F811
    for h in (F, P):
        r = client.get(URL, headers=h)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "SCOPE_REQUIRED"


def test_the_ops_tab_shows_it_in_both_languages():
    root = Path(__file__).resolve().parents[1] / "static"
    html = (root / "admin.html").read_text("utf-8")
    assert 'id="xpf"' in html and 'api("/xp-funnel")' in html and "showFunnel();" in html
    keys = ("xpf.h", "xpf.lead", "xpf.none", "xpf.no_last", "xpf.last", "xpf.times", "xpf.new_cell",
            "xpf.waiting", "xpf.note") + tuple("xpf.col." + c for c in (
                "month", "saw", "clicked", "dismissed", "linked", "new", "k_linked", "k_cairn", "k_share", "k_control"))
    zh = json.loads((root / "i18n" / "zh-TW" / "admin.json").read_text("utf-8"))
    en = json.loads((root / "i18n" / "en" / "admin.json").read_text("utf-8"))
    for k in keys:
        assert zh.get(k) and en.get(k) and zh[k] != en[k], k
