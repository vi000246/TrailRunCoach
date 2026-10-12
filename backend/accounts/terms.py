"""
The terms a new user accepts at sign-up (SP-58): one version for the whole set, the documents
read from backend/legal/ (shipped in the image with the code; docs/ is not).

    terms.VERSION            bump it when any document changes, and pin the new texts' digests in
                             PINNED: everyone is asked again (signed in or at the next sign-in)
    terms.PINNED             {version: {id: sha256}}: a document edited without a new VERSION fails
                             test_terms_digests_are_pinned_to_the_version (the people who accepted
                             that version agreed to another text)
    terms.public()           {version, digest, documents: [{id, title, body, draft}]} for the page
    terms.digests()          {id: sha256 of the text}; shown_digest() one hash over the set: the page
                             sends back the one it showed, and that set is what a consent records

The four documents are SP-60's (服務條款, 隱私權政策, 健康風險聲明, 第三方串接免責). Until SP-60
writes them they are placeholders that say so (「草稿，待 SP-60 定稿」, draft=True). The
cross-user analysis (SP-319) is part of the privacy policy and accepted with the rest: there is
no separate tick.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.i18n import N_, _

LEGAL_DIR = Path(__file__).resolve().parents[1] / "legal"

# the version of the whole set; a change asks every user for their consent again
VERSION = "2026-10-12-draft"
PINNED = {
    "2026-10-12-draft": {
        "terms": "13d89e737cd750614520f3a7b5c46b1399a06f4948e6d3257e7e9f25bb420ed0",
        "privacy": "567bc933a5d9dc6c1ee3629560aa1f5fe7c2ad849ce3c1c5e11dfe2726088d77",
        "health": "75ed72e634432ba70b4581c2915635894f069752308376c281430225a7a084e0",
        "third_party": "9d64d8901d2d436f4d858e5f828d45e48208997babdd9672b2a49e56b5b065fb",
    },
}
DRAFT = True          # SP-60 has not written the texts yet: the sign-up page marks them


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
            "documents": [{"id": d.id, "title": _(d.title), "body": _text(d), "draft": DRAFT} for d in DOCUMENTS]}


def accepted(version: str, digest: str) -> Optional[dict]:
    """The documents' digests a consent records, when the page accepted the current version and
    the very texts it showed (its `digest`); None when either changed meanwhile (the page shows
    the new ones and asks again)."""
    ds = digests()
    if version != VERSION or not isinstance(digest, str) or digest != _set_digest(ds):
        return None
    return ds
