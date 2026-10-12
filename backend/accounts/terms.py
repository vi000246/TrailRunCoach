"""
The terms a new user accepts at sign-up (SP-58): one version for the whole set, the documents
read from backend/legal/ (shipped in the image with the code; docs/ is not).

    terms.VERSION            bump it when any document changes: everyone is asked again at their
                             next sign-in (store.consent_version != VERSION)
    terms.public()           {version, documents: [{id, title, body, draft}]} for the sign-up page
    terms.digests()          {id: sha256 of the text} recorded with each acceptance

The four documents are SP-60's (服務條款, 隱私權政策, 健康風險聲明, 第三方串接免責). Until SP-60
writes them they are placeholders that say so (「草稿，待 SP-60 定稿」, draft=True). The
cross-user analysis (SP-319) is part of the privacy policy and accepted with the rest: there is
no separate tick.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from backend.i18n import N_, _

LEGAL_DIR = Path(__file__).resolve().parents[1] / "legal"

# the version of the whole set; a change asks every user for their consent again
VERSION = "2026-10-12-draft"
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


def public() -> dict:
    return {"version": VERSION, "draft": DRAFT,
            "documents": [{"id": d.id, "title": _(d.title), "body": _text(d), "draft": DRAFT} for d in DOCUMENTS]}
