"""SP-60: the four legal documents (服務條款、隱私權政策、健康風險聲明、第三方串接免責) — drafts for the
lawyer — their version and effective date, and the public pages /legal and /legal/<doc> (the
privacy policy's address for Google's OAuth consent screen, SP-387).

The texts themselves are not tested for wording; these checks keep the registry honest:
  - every document has a version and an effective date (a real date once DRAFT is off, and then
    no 【待定…】 / 【營運者名稱】 placeholder may be left in any of them)
  - the digests stay pinned to terms.VERSION (test_login_sp58 already; here the set as a whole)
  - each document still covers the items SP-60's 「範圍」 lists (a removed section fails)
  - signed out, in sign-in mode, the legal pages open — exactly those paths, nothing next to them;
    owner and demo modes answer them 404 as they did before (no new behaviour there)."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.tests.test_login_sp58 import (  # noqa: F401 — fixtures used by name
    HTML, _clean_env, _client, app, c, csrf, env, location, new_terms, sign_up, started,
)

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DOC_IDS = ("terms", "privacy", "health", "third_party")


# ---------------------------------------------------------------------------- the registry

def test_every_document_has_a_version_and_an_effective_date():
    from backend.accounts import terms
    for d in terms.DOCUMENTS:
        m = terms.meta(d)
        assert re.fullmatch(r"\d+\.\d+(-draft)?", m["version"]), (d.id, m)
        assert DATE.fullmatch(m["updated"]), (d.id, m)
        eff = m["effective"]
        if terms.DRAFT:
            # a draft has no effective date yet: a placeholder that says when it gets one
            assert DATE.fullmatch(eff) or (eff.startswith("【") and eff.endswith("】")), (d.id, eff)
            assert m["version"].endswith("-draft"), d.id
        else:
            assert DATE.fullmatch(eff), (d.id, eff)


def test_no_placeholder_is_left_once_the_texts_are_final():
    """DRAFT off (the lawyer's version) = nothing left to fill in: no 【待定…】, no operator
    placeholder, no draft banner."""
    from backend.accounts import terms
    left = {d.id: terms.PLACEHOLDER.findall(terms.body(d)) for d in terms.DOCUMENTS}
    if terms.DRAFT:
        assert all(left.values()), left            # the draft says what is still open
        for d in terms.DOCUMENTS:
            assert terms.DRAFT_MARK in terms.body(d), d.id
    else:
        assert not any(left.values()), left
        assert not any(terms.DRAFT_MARK in terms.body(d) for d in terms.DOCUMENTS)


def test_the_set_version_names_the_draft_and_its_digests_are_pinned():
    from backend.accounts import terms
    assert terms.digests() == terms.PINNED[terms.VERSION]
    assert terms.VERSION.endswith("-draft") == terms.DRAFT
    assert terms.VERSION != "2026-10-12-draft"           # SP-58's placeholders were replaced


def test_public_documents_carry_their_version_effective_date_and_address():
    from backend.accounts import terms
    pub = terms.public()
    assert [d["id"] for d in pub["documents"]] == list(DOC_IDS)
    for d in pub["documents"]:
        m = terms.meta(next(x for x in terms.DOCUMENTS if x.id == d["id"]))
        assert d["version"] == m["version"] and d["effective"] == m["effective"] and d["updated"] == m["updated"]
        assert d["url"] == f"/legal/{d['id']}"
        assert not d["body"].startswith("---")             # the front matter is not the text
        assert d["body"].strip()


@pytest.mark.parametrize("doc,needles", [
    # SP-60 R2: no plans, prices, payment or refunds (test_no_commercial_clause_until_there_is_a_plan)
    ("terms", ["目前免費", "收費之前", "停權", "刪除帳號", "責任限制", "中華民國",
               "資料遺失", "【營運者名稱】", "【聯絡 email】"]),
    ("privacy", ["【營運者名稱】", "【聯絡 email】", "蒐集目的", "心率", "GPS", "裝置",
                 "1 年", "3 年", "COROS", "Garmin", "intervals.icu", "AI", "跨使用者", "去識別化",
                 "下載", "刪除", "撤回", "管理員", "Cookie", "__Host-trc_session", "__Host-trc_csrf",
                 "【待定：SP-326 調查結論與律師審閱】", "特種個資", "GDPR"]),
    ("health", ["醫療建議", "醫師", "風險", "停止", "演算法", "估計"]),
    ("third_party", ["COROS", "Garmin", "garminconnect", "非官方", "鎖", "停權", "自行承擔", "intervals.icu",
                     "金鑰"]),
])
def test_each_document_covers_the_items_sp60_lists(doc, needles):
    from backend.accounts import terms
    text = terms.body(next(d for d in terms.DOCUMENTS if d.id == doc))
    missing = [n for n in needles if n not in text]
    assert not missing, (doc, missing)


# SP-60 R2 (owner, 2026-10-14): the commercial plan is not ready, so the documents carry no
# commercial clause; the clauses taken out wait in docs/legal/commercial-terms-draft.md
COMMERCIAL = ("NT$", "新台幣", "訂閱", "自動續訂", "續訂", "付款", "扣款", "退款", "金流", "發票", "價格",
              "免費方案", "訂閱方案", "計費")
COMMERCIAL_DRAFT = Path(__file__).resolve().parents[2] / "docs" / "legal" / "commercial-terms-draft.md"


def test_no_commercial_clause_until_there_is_a_plan():
    from backend.accounts import terms
    for d in terms.DOCUMENTS:
        t = terms.body(d)
        found = [w for w in COMMERCIAL if w in t]
        assert not found, (d.id, found)
    # what stays: it is free now, and a charge comes only after a notice and a new consent
    t = terms.body(terms.by_id("terms"))
    assert "## 4. 費用" in t and "目前免費" in t and "請你閱讀並同意" in t
    assert "目前免費" in terms.body(terms.by_id("privacy"))


def test_the_commercial_clauses_are_kept_as_a_draft_for_later():
    """Not served (docs/ is not in the image) and not part of the consent; kept for the day there
    is a commercial plan."""
    from backend.accounts import terms
    text = COMMERCIAL_DRAFT.read_text("utf-8")
    for n in ("NT$99", "NT$890", "自動續訂", "取消與退款", "消費者保護法第 19 條", "金流商", "電子發票",
              "付款資料", "付款紀錄", "12 個月內實際支付"):
        assert n in text, n
    assert "commercial-terms-draft" not in "".join(terms.body(d) for d in terms.DOCUMENTS)
    assert not (terms.LEGAL_DIR / "commercial-terms-draft.md").exists()


def test_third_party_has_a_trademark_section_in_our_own_words():
    """SP-496 (SP-391's follow-up): TrainingPeaks' registered names belong to TrainingPeaks, LLC;
    the service names its own metrics (SP-391 / SP-497 words) and mentions the others only where
    it compares, with the notice."""
    from backend.accounts import terms
    t = terms.body(terms.by_id("third_party"))
    assert "## 2. 商標" in t
    sec = t.split("## 2. 商標", 1)[1].split("\n## ", 1)[0]
    for n in ("TrainingPeaks, LLC", "TSS®", "Training Stress Score®", "NP®", "Normalized Power®", "IF®",
              "Intensity Factor®", "註冊商標", "訓練負荷（Training load）", "強度比（Intensity）",
              "加權功率（Weighted power）", "長期訓練負荷（Chronic load）", "短期負荷（Acute load）",
              "負荷平衡（Load balance）", "對照", "商標聲明", "授權或背書"):
        assert n in sec, n
    # the trademarks appear with their ® only, never as our own label; nothing of the old app name
    import re
    assert not re.search(r"(?<![A-Za-z])(TSS|NP|IF)(?![A-Za-z®])", sec)
    for d in terms.DOCUMENTS:
        assert "WKO" not in terms.body(d), d.id


def test_the_documents_never_name_rq_or_its_people():
    """SP-60: written from the structure only, not RQ's text (copyright; it names RQ's company)."""
    from backend.accounts import terms
    for d in terms.DOCUMENTS:
        t = terms.body(d)
        for name in ("永動科技", "RQ", "徐國峰"):
            assert name not in t, (d.id, name)


def test_to_html_escapes_everything_and_knows_only_a_few_marks():
    from backend.accounts import terms
    h = terms.to_html("## 標題 <b>\n\n段落 <script>alert(1)</script> & 【待定：x】\n\n- 一\n- 二\n\n1. 甲\n2. 乙\n\n> 注意")
    assert "<script>" not in h and "&lt;script&gt;" in h and "&amp;" in h
    assert "<h2>標題 &lt;b&gt;</h2>" in h
    assert "<ul><li>一</li><li>二</li></ul>" in h and "<ol><li>甲</li><li>乙</li></ol>" in h
    assert "<mark>【待定：x】</mark>" in h and "<blockquote>" in h


# ---------------------------------------------------------------------------- the public pages

def test_is_public_names_exactly_the_legal_pages():
    from backend.accounts import login as LG
    for p in ("/legal", "/legal/terms", "/legal/privacy", "/legal/health", "/legal/third_party"):
        assert LG.is_public(p), p
    for p in ("/legal/", "/legal/nope", "/legal/privacy/", "/legal/privacy/x", "/legal/privacy.md", "/legalx",
              "/legal/privacyx", "/legal/../api/v1/plan", "/api/v1/legal/privacy", "/Legal/privacy"):
        assert not LG.is_public(p), p


def test_signed_out_the_legal_pages_open(c):
    from backend.accounts import terms
    for d in terms.DOCUMENTS:
        r = c.get(f"/legal/{d.id}", headers=HTML)
        assert r.status_code == 200, (d.id, r.status_code, r.text[:200])
        assert "text/html" in r.headers["content-type"]
        m = terms.meta(d)
        assert m["version"] in r.text and m["effective"] in r.text
        assert "<h2>" in r.text                                    # the text itself, rendered on the server
        assert 'name="robots" content="noindex"' in r.text if terms.DRAFT else True
    r = c.get("/legal/privacy")                                    # a crawler (no Accept: text/html) too
    assert r.status_code == 200 and "Cookie" in r.text and "__Host-trc_session" in r.text
    assert r.headers["cache-control"] == "private, no-store"       # the page inlines the session
    idx = c.get("/legal")
    assert idx.status_code == 200
    for i in DOC_IDS:
        assert f'href="/legal/{i}"' in idx.text


def test_next_to_the_legal_pages_everything_stays_closed(c):
    for p in ("/legal/nope", "/legal/privacy/x", "/legal/privacy.md", "/legalx"):
        r = c.get(p)
        assert r.status_code == 401 and r.json()["code"] == "LOGIN_REQUIRED", (p, r.status_code)
    for p in ("/legal/%2e%2e/api/v1/plan", "/legal/privacy%2f..%2f..%2fapi", "/legal//privacy"):
        assert c.get(p).status_code == 400, p
    assert c.get("/api/v1/plan").status_code == 401
    assert location(c.get("/overview", headers=HTML)) == "/login?next=%2Foverview"
    assert c.get("/", headers=HTML).status_code == 200      # "/" is the introduction since SP-622
    # a write to a legal page is no way in either
    assert c.post("/legal/privacy", headers={"X-TRC-CSRF": csrf(c)}).status_code == 405


def test_the_legal_pages_open_for_a_user_the_new_terms_stopped(c, monkeypatch):
    """Signed in but new terms not accepted yet: the documents stay readable (the gate sends
    every other page to /signup)."""
    sign_up(c, "g-alice", "alice@example.com")
    new_terms(monkeypatch)
    assert location(c.get("/api/v1/overview/page", headers=HTML)).startswith("/signup")
    assert c.get("/legal/terms", headers=HTML).status_code == 200


def test_the_legal_page_frame_switches_language_the_text_stays_chinese(c):
    r = c.get("/legal/privacy?lang=en", headers=HTML)
    assert r.status_code == 200 and '<html lang="en">' in r.text
    assert "Privacy" in r.text                                    # the frame's title, translated
    assert "隱私權政策" in r.text                                   # the text: Chinese until SP-78
    zh = c.get("/legal/privacy?lang=zh-TW", headers=HTML)
    assert '<html lang="zh-TW">' in zh.text


def test_the_sign_in_and_terms_pages_link_the_public_documents(c):
    r = c.get("/login")
    for i in DOC_IDS:
        assert f'href="/legal/{i}"' in r.text, i


def test_the_legal_catalogs_are_in_both_languages():
    import json
    from backend.i18n.pages import CATALOG_DIR
    zh = json.loads((CATALOG_DIR / "zh-TW" / "legal.json").read_text("utf-8"))
    en = json.loads((CATALOG_DIR / "en" / "legal.json").read_text("utf-8"))
    assert set(zh) == set(en)


def test_owner_mode_has_no_legal_pages_as_before(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    from backend.main import build_app
    with _client(build_app(demo=False)) as cl:
        for p in ("/legal", "/legal/privacy"):
            assert cl.get(p, headers=HTML).status_code == 404, p
        assert cl.get("/api/v1/plan").status_code == 200


def test_the_demo_has_no_legal_pages(monkeypatch, tmp_path):
    from backend.tests import demo_fixtures as F
    root = tmp_path / "demo-root"
    F.demo_env(monkeypatch, root)
    F.make_base(root)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    with TestClient(build_app(demo=True), raise_server_exceptions=False, follow_redirects=False) as cl:
        assert cl.get("/legal/privacy", headers=HTML).status_code == 404
        assert cl.get("/api/v1/plan").status_code == 200
