"""介紹頁 (SP-622): what a stranger sees at "/" before signing in (main._home; signed in, "/" stays the
overview). static/intro.html with i18n/<locale>/intro.json; no one's data, nothing computed.

The page's 「先試試看」 button (login.try_link) goes to the public demo when TRC_DEMO_URL is set, else to
the built-in race calculator example (/share/<share.EXAMPLE_ID>, a fictional runner's 50K): the page links
to the example by default and the server says, per request, when the demo is there instead
(window.TRC_TRY). Its screenshots are the manual's (backend/manual/shots.json, names intro-*): taken on
the demo athlete in both languages, light and dark."""
from __future__ import annotations

import json

from backend.i18n.pages import PageResponse, render_page

PAGE = "intro"


def page() -> PageResponse:
    from backend.accounts import login as LG
    link = json.dumps(LG.try_link()).replace("<", "\\u003c")
    return render_page(PAGE, fill={"try": f"<script>window.TRC_TRY = {link};</script>"})
