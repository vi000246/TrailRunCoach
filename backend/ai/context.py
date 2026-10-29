"""What the AI 問答 sends to the model (SP-785; SP-306 Q1-3: summaries only, never the GPS track).

Two parts, both summaries:

* the server's own (`server_summary`): today, the season phase and its goal, the status card's
  rules — each indicator's judgment, its number, why, what to do and the source it rests on
  (engine/status.Indicator, no sparkline, no `extra`) — the to-do list, and the last WEEKS weeks'
  totals (hours, km, climb, load, sessions; engine/overview.summary without its activity list);
* the page's "AI summary" of what is in view (`clean_focus`), the format the front end
  (static/askai.js) fills — docs/spec/ai-qa.spec.md:

      {"kind": "card" | "chart" | "session" | "calc" | "hint" | "page",
       "page": "overview",                  # the shell's page id
       "title": "心率與功率圖（10/18 越野跑）",
       "summary": "the card's visible text",
       "facts": [{"label": "CTL", "value": "52"}],
       "rules": [{"text": "低強度比例 < 80%：偏少", "source": "Seiler 2010"}],
       "sources": [{"title": "手冊：負荷平衡", "url": "/manual/charts#form"}]}

  Unknown keys are dropped; every string is capped; coordinates ("24.12345, 121.12345") and long
  runs of numbers (a stream, a track) are replaced by a marker; a source's url must be a manual
  section. So whatever a page puts there, no track and no position leaves the server.

Every rule and source gets an id (S1, S2 …): the model cites them, and the answer's parts that
cite none are shown as the model's inference (qa.parse_answer).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Any, Optional

log = logging.getLogger(__name__)

KINDS = ("card", "chart", "session", "calc", "hint", "page")
WEEKS = 8
MAX = {"title": 120, "page": 40, "summary": 2500, "label": 80, "value": 160, "rule": 400, "source": 200,
       "facts": 40, "rules": 20, "sources": 8, "question": 500, "history": 6, "history_q": 500, "history_a": 2000}
MAX_FOCUS_BYTES = 12_000
# a position: two decimal degrees side by side (lat, lon / lon lat)
_COORD = re.compile(r"-?\d{1,3}\.\d{4,}\s*[,/;\s]\s*-?\d{1,3}\.\d{4,}")
# a stream / a track: 40+ numbers in a row
_RUN = re.compile(r"(?:-?\d+(?:\.\d+)?[\s,;|]+){40,}-?\d+(?:\.\d+)?")
COORD_MARK = "[location removed]"
RUN_MARK = "[numbers removed]"
_PAGE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_MANUAL = re.compile(r"^/manual/[a-z0-9][a-z0-9-]*(?:#[a-z0-9][a-z0-9-]*)?$")


def clean_text(v: Any, n: int) -> str:
    if v is None:
        return ""
    s = str(v)
    s = _COORD.sub(COORD_MARK, s)
    s = _RUN.sub(RUN_MARK, s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s[:n]


def clean_focus(raw: Any) -> Optional[dict]:
    """The page's AI summary, cleaned (module doc); None when there is none."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind") if raw.get("kind") in KINDS else "card"
    page = str(raw.get("page") or "")
    out: dict = {"kind": kind, "page": page if _PAGE.match(page) else "",
                 "title": clean_text(raw.get("title"), MAX["title"]),
                 "summary": clean_text(raw.get("summary"), MAX["summary"])}
    facts = []
    for f in (raw.get("facts") or [])[:MAX["facts"]] if isinstance(raw.get("facts"), list) else []:
        if isinstance(f, dict) and (f.get("label") or f.get("value")):
            facts.append({"label": clean_text(f.get("label"), MAX["label"]),
                          "value": clean_text(f.get("value"), MAX["value"])})
    rules = []
    for r in (raw.get("rules") or [])[:MAX["rules"]] if isinstance(raw.get("rules"), list) else []:
        if isinstance(r, dict) and r.get("text"):
            rules.append({"text": clean_text(r.get("text"), MAX["rule"]),
                          "source": clean_text(r.get("source"), MAX["source"])})
    sources = []
    for s in (raw.get("sources") or [])[:MAX["sources"]] if isinstance(raw.get("sources"), list) else []:
        if isinstance(s, dict) and s.get("title"):
            url = str(s.get("url") or "")
            sources.append({"title": clean_text(s.get("title"), MAX["title"]),
                            "url": url if _MANUAL.match(url) else None})
    if facts:
        out["facts"] = facts
    if rules:
        out["rules"] = rules
    if sources:
        out["sources"] = sources
    # the whole of it capped too (a page that fills every field to its limit)
    while len(json.dumps(out, ensure_ascii=False).encode()) > MAX_FOCUS_BYTES and out["summary"]:
        out["summary"] = out["summary"][: len(out["summary"]) // 2]
    if not (out["title"] or out["summary"] or facts or rules):
        return None
    return out


def clean_history(raw: Any) -> list:
    """The conversation so far (the dialog's earlier turns), capped: [{q, a}]."""
    out = []
    for h in (raw if isinstance(raw, list) else [])[-MAX["history"]:]:
        if isinstance(h, dict) and h.get("q"):
            out.append({"q": clean_text(h.get("q"), MAX["history_q"]), "a": clean_text(h.get("a"), MAX["history_a"])})
    return out


# ----------------------------------------------------------------------------- the server's part

def _r(v, d=1):
    try:
        return None if v is None else round(float(v), d)
    except (TypeError, ValueError):
        return None


def _status() -> tuple:
    """(the status card's dict, the Dataset) — the overview's own (cached; stale while a new one computes)."""
    from backend.api import overview as OV
    from backend.engine import overview as O
    ds = OV._dataset()
    return OV.status_answer(ds, O.day_to_date(ds.today)), ds


def _weeks(ds) -> list:
    from backend.engine import overview as O
    out = O.summary(ds, "week", O.day_to_date(ds.today), WEEKS, detail=False)
    return [{"week": b.get("start"), "hours": _r((b.get("moving_s") or 0) / 3600), "km": _r(b.get("distance_km")),
             "climb_m": _r(b.get("climbing_m"), 0), "load_tss": _r(b.get("tss"), 0), "sessions": b.get("sessions"),
             "days": b.get("days")} for b in out.get("buckets") or []]


def server_summary() -> dict:
    """{today, phase, headline, rules, todo, weeks} — summaries only; a part that can't be read is left
    out (the public mirror without the calculation core, a first import still building)."""
    out: dict = {"today": dt.date.today().isoformat()}
    try:
        st, ds = _status()
    except Exception as e:                      # noqa: BLE001 — the question still goes, with less
        log.info("AI context: no status (%s)", type(e).__name__)
        return out
    out["today"] = st.get("today") or out["today"]
    ph = st.get("phase") or {}
    if ph:
        out["phase"] = {"label": clean_text(ph.get("label"), 120), "goal": clean_text(ph.get("goal"), 300),
                        "start": ph.get("start"), "end": ph.get("end")}
    if st.get("headline"):
        out["headline"] = clean_text(st.get("headline"), 300)
    rules = []
    for i in st.get("indicators") or []:
        if i.get("level") == "na" and not i.get("text"):
            continue
        rules.append({"title": clean_text(i.get("title"), 80), "level": i.get("level"),
                      "value": clean_text(i.get("text"), 80), "judgment": clean_text(i.get("verdict"), 300),
                      "why": clean_text(i.get("why"), 500), "action": clean_text(i.get("action"), 300),
                      "source": clean_text(i.get("source"), 200)})
    out["rules"] = rules
    out["todo"] = [{"title": clean_text(a.get("title"), 120), "detail": clean_text(a.get("detail"), 300),
                    "source": clean_text(a.get("source"), 200)} for a in (st.get("actions") or [])[:8]]
    try:
        out["weeks"] = _weeks(ds)
    except Exception as e:                      # noqa: BLE001
        log.info("AI context: no weekly totals (%s)", type(e).__name__)
    return out


# ------------------------------------------------------------------------------------- the ids

def number_refs(server: dict, focus: Optional[dict]) -> tuple[dict, list]:
    """(the context sent, its citable refs [{id, kind, title, source, url}]): every server rule, to-do,
    card rule and card source gets an id S1… in that order."""
    refs: list = []

    def ref(kind: str, title: str, source: str = "", url: Optional[str] = None) -> str:
        rid = f"S{len(refs) + 1}"
        refs.append({"id": rid, "kind": kind, "title": title, "source": source, "url": url})
        return rid

    server = json.loads(json.dumps(server))         # a copy: the ids go in
    for r in server.get("rules") or []:
        r["id"] = ref("status", r.get("title") or "", r.get("source") or "")
    for a in server.get("todo") or []:
        a["id"] = ref("todo", a.get("title") or "", a.get("source") or "")
    if focus:
        focus = json.loads(json.dumps(focus))
        for r in focus.get("rules") or []:
            r["id"] = ref("card", r.get("text") or "", r.get("source") or "")
        for s in focus.get("sources") or []:
            s["id"] = ref("manual", s.get("title") or "", "", s.pop("url", None))   # the link stays in the refs: the model never sees a path (SP-1023)
    return {"app": server, "in_view": focus}, refs


def sent_view(ctx: dict) -> dict:
    """What the dialog says was sent (no values): {in_view, rules, weeks}."""
    app = ctx.get("app") or {}
    f = ctx.get("in_view") or {}
    return {"in_view": f.get("title") or None, "kind": f.get("kind"), "rules": len(app.get("rules") or []),
            "weeks": len(app.get("weeks") or []), "todo": len(app.get("todo") or [])}
