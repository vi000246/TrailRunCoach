"""SP-847: the about page links to the terms of service and the privacy policy (and the other
documents agreed to at sign-up) — in sign-in mode only: the owner's app and the demo have no /legal."""
from __future__ import annotations

import re

from backend.accounts import terms
from backend.tests.test_about_page_sp495 import LOCALES, _cat, owner  # noqa: F401 — fixtures used by name
from backend.tests.test_login_sp58 import HTML, app, c, env, started  # noqa: F401 — fixtures used by name


def _main(html: str) -> str:
    return html.split("<main", 1)[1].split("</main>", 1)[0]


def test_signed_in_mode_the_page_links_to_the_terms_and_privacy_and_they_open(c):  # noqa: F811
    for loc in LOCALES:
        cat = _cat(loc)
        r = c.get(f"/about?lang={loc}", headers=HTML)
        assert r.status_code == 200
        main = _main(r.text)
        assert '<section class="ab-sec" id="legal">' in main and cat["legal.h"] in main
        assert '<a href="#legal">' in main                                     # in the page's contents
        hrefs = re.findall(r'href="(/legal/[^"]+)"', main)
        assert hrefs == [terms.URL_PREFIX + d.id for d in terms.DOCUMENTS], hrefs
        for h in hrefs:                                                     # public: they open signed out
            assert c.get(h, headers=HTML).status_code == 200, h
    en = _main(c.get("/about?lang=en", headers=HTML).text)
    assert ">Terms of Service</a>" in en and ">Privacy Policy</a>" in en
    zh = _main(c.get("/about?lang=zh-TW", headers=HTML).text)
    assert ">服務條款</a>" in zh and ">隱私權政策</a>" in zh


def test_the_owners_app_has_no_legal_links(owner):  # noqa: F811
    for loc in LOCALES:
        main = _main(owner.get(f"/about?lang={loc}", headers=HTML).text)
        assert 'id="legal"' not in main and "/legal" not in main
    assert owner.get("/legal/terms", headers=HTML).status_code == 404


def test_the_demo_has_no_legal_links(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from backend.main import build_app
    from backend.tests import demo_fixtures as F
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    F.make_base(root)
    with TestClient(build_app(demo=True), raise_server_exceptions=False, follow_redirects=False) as cl:
        r = cl.get("/about", headers=HTML)
        assert r.status_code == 200 and "/legal" not in _main(r.text)
