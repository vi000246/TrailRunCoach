"""
The terms a new user accepts at sign-up (SP-58): one version for the whole set, the documents
read from backend/legal/ (shipped in the image with the code; docs/ is not).

    terms.VERSION            bump it when any document changes, and pin the new texts' digests in
                             PINNED: everyone is asked again (signed in or at the next sign-in)
    terms.PINNED             {version: {id: sha256}}: a document edited without a new VERSION fails
                             test_terms_digests_are_pinned_to_the_version (the people who accepted
                             that version agreed to another text)
    terms.public()           {version, digest, documents: [{id, title, body, draft, version,
                             effective, updated, url}]} for the page
    terms.digests()          {id: sha256 of the text}; shown_digest() one hash over the set: the page
                             sends back the one it showed, and that set is what a consent records

The four documents are SP-60's (服務條款, 隱私權政策, 健康風險聲明, 第三方串接免責): drafts for the
lawyer (DRAFT = True: the sign-up page and /legal say so, each text opens with DRAFT_MARK, the set
VERSION ends in -draft). Each file starts with a front matter (ASCII keys, between two "---"
lines): `version` (0.1-draft), `updated` (a date) and `effective` (a date; a 【待定…】 placeholder
while a draft). The digest covers the whole file, front matter included. Going live = the lawyer's
texts, DRAFT = False, real effective dates, no placeholder left (test_legal_sp60 checks).

The cross-user analysis (SP-319) is part of the privacy policy and accepted with the rest: there is
no separate tick (the owner, 2026-10-12; SP-326 still open, the policy marks it 【待定】).

    terms.meta(doc)          {version, effective, updated} of one document
    terms.body(doc)          its text without the front matter (Markdown: ##, ###, -, 1., >)
    terms.to_html(text)      that text as HTML, every character escaped first (the /legal pages)
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.i18n import N_, _

LEGAL_DIR = Path(__file__).resolve().parents[1] / "legal"

# the version of the whole set; a change asks every user for their consent again
VERSION = "2026-10-15-draft"
PINNED = {
    # SP-58's placeholders (kept: a consent recorded under that version names those texts)
    "2026-10-12-draft": {
        "terms": "13d89e737cd750614520f3a7b5c46b1399a06f4948e6d3257e7e9f25bb420ed0",
        "privacy": "567bc933a5d9dc6c1ee3629560aa1f5fe7c2ad849ce3c1c5e11dfe2726088d77",
        "health": "75ed72e634432ba70b4581c2915635894f069752308376c281430225a7a084e0",
        "third_party": "9d64d8901d2d436f4d858e5f828d45e48208997babdd9672b2a49e56b5b065fb",
    },
    "2026-10-13-draft": {
        "terms": "63ee3149f124a2dfce1a3b386786687ba1567209bbf13f47446809c4b608f886",
        "privacy": "72a02af45ab42ce44337a7a71f3325e4643faee5e1c014c74026e0def0887e3f",
        "health": "95cd126a9b7975508df89dbb2ff342c1b0e7ebe75826f4b4df282fef5b5a44c7",
        "third_party": "d4fdf17b4d6869f3018aac04317658797ae651b6f20b42a1f36f17f689a84b61",
    },
    # SP-60 R2: the commercial clauses (plans, prices, payment, auto-renewal, refunds) taken out
    # until there is a commercial plan; kept for later in docs/legal/commercial-terms-draft.md.
    # SP-496: third_party gains the trademark section (same version: neither was released alone)
    "2026-10-15-draft": {
        "terms": "1ec9ae82b3da23f4fbcff389afdea10f9271cb57de8054e5c07bf29128623537",
        "privacy": "cc64a7b5411d9d74ad53e4e7ca3ea16ee1da05b3866a114923dece68882058f1",
        "health": "95cd126a9b7975508df89dbb2ff342c1b0e7ebe75826f4b4df282fef5b5a44c7",
        "third_party": "8e7e6c1f587c56140a92f4e2caec1646f465e3e92e3d246680b989a20e98f9cc",
    },
}
DRAFT = True          # SP-60's drafts, not reviewed by a lawyer yet: the sign-up page and /legal mark them
# the first line of every draft's text (the banner a reader sees first)
DRAFT_MARK = "【草稿】"            # (backend/i18n/allow_cjk.txt: it is the documents' own text)
# what is still to be filled in or decided: 【待定：…】, 【營運者名稱】, 【聯絡 email】 …
PLACEHOLDER = re.compile("\u3010[^\u3010\u3011\n]*\u3011")
URL_PREFIX = "/legal/"


@dataclass(frozen=True)
class Doc:
    id: str
    title: str          # msgid (translated when shown)
    file: str


DOCUMENTS = (
    Doc("terms", N_("服務條款"), "terms.md"),
    Doc("privacy", N_("隱私權政策"), "privacy.md"),
    Doc("health", N_("健康風險聲明"), "health.md"),
    Doc("third_party", N_("第三方串接免責"), "third_party.md"),
)


def _text(d: Doc) -> str:
    return (LEGAL_DIR / d.file).read_text("utf-8")


def _split(text: str) -> tuple[dict, str]:
    """(front matter, the rest) of a document's file."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        k, sep, v = line.partition(":")
        if sep:
            meta[k.strip()] = v.strip()
    return meta, text[end + 5:].lstrip("\n")


def meta(d: Doc) -> dict:
    m = _split(_text(d))[0]
    return {"version": m.get("version", ""), "effective": m.get("effective", ""), "updated": m.get("updated", "")}


def body(d: Doc) -> str:
    return _split(_text(d))[1]


def by_id(doc_id: str) -> Optional[Doc]:
    return next((d for d in DOCUMENTS if d.id == doc_id), None)


def _inline(s: str) -> str:
    """One line of text: escaped, then the placeholders marked."""
    return PLACEHOLDER.sub(lambda m: f"<mark>{m.group(0)}</mark>", html.escape(s, quote=False))


_OL = re.compile(r"\d+\.\s+")


def to_html(text: str) -> str:
    """The documents' few marks as HTML: ## / ### headings, - and 1. lists, > notes, blank-line
    paragraphs. Everything is escaped first (nothing in the text becomes markup of its own)."""
    out: list[str] = []
    para: list[str] = []
    lst: Optional[str] = None
    quote: list[str] = []

    def flush() -> None:
        nonlocal lst
        if para:
            out.append("<p>" + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()
        if lst:
            out.append(f"</{lst}>")
            lst = None
        if quote:
            out.append("<blockquote><p>" + "<br>".join(_inline(x) for x in quote) + "</p></blockquote>")
            quote.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith("### ") or line.startswith("## "):
            flush()
            tag = "h3" if line.startswith("### ") else "h2"
            out.append(f"<{tag}>{_inline(line.split(' ', 1)[1].strip())}</{tag}>")
            continue
        kind = "ul" if line.startswith("- ") else "ol" if _OL.match(line) else None
        if kind:
            if para or quote or (lst and lst != kind):
                flush()
            if lst is None:
                out.append(f"<{kind}>")
                lst = kind
            item = line[2:] if kind == "ul" else _OL.sub("", line, count=1)
            out.append(f"<li>{_inline(item.strip())}</li>")
            continue
        if line.startswith(">"):
            if para or lst:
                flush()
            quote.append(line[1:].strip())
            continue
        if lst or quote:
            flush()
        para.append(line)
    flush()
    return "".join(out)


def digests() -> dict:
    return {d.id: hashlib.sha256(_text(d).encode("utf-8")).hexdigest() for d in DOCUMENTS}


def _set_digest(ds: dict) -> str:
    return hashlib.sha256(json.dumps(ds, sort_keys=True).encode("utf-8")).hexdigest()


def shown_digest() -> str:
    """One sha256 over the documents as served now (what the sign-up page shows and sends back)."""
    return _set_digest(digests())


def public() -> dict:
    ds = digests()
    return {"version": VERSION, "draft": DRAFT, "digest": _set_digest(ds),
            "documents": [{"id": d.id, "title": _(d.title), "body": body(d), "draft": DRAFT, **meta(d),
                           "url": URL_PREFIX + d.id} for d in DOCUMENTS]}


def accepted(version: str, digest: str) -> Optional[dict]:
    """The documents' digests a consent records, when the page accepted the current version and
    the very texts it showed (its `digest`); None when either changed meanwhile (the page shows
    the new ones and asks again)."""
    ds = digests()
    if version != VERSION or not isinstance(digest, str) or digest != _set_digest(ds):
        return None
    return ds
