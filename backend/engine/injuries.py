"""
傷病紀錄 — injury events and the one-tap pain mark on an activity
(docs/plans/injury-tracking.plan.md §1, owner decisions 2026-10-02).

Two layers:
  * the activity mark (activity_tags.pain / pain_area / injury_id): NULL 沒填,
    0 沒痛, 1 痠, 2 痛 (affects the stride or pace, or still hurts after), 3 痛到中斷;
  * the event (db/models.InjuryEvent): area, side, kind (過度使用 / 急性),
    severity by the training impact — 輕 照練 / 中 減量或改練 / 重 停跑 (the
    OSTRC idea of "affects training", Clarsen 2013; the 3-level mapping is
    推估) — optional 0–10 pain, onset, status draft / active / resolved.

A 痛 / 中斷 mark attaches to an open event of the same area that started
0–ATTACH_DAYS before the activity (推估), else a draft event is created
(`attach`, pure). 痠 never creates an event. Body areas: the nine fixed ones
plus the user's own (stored in user_settings `injury.custom_areas`, reused in
the picker).

Privacy (§5): local DB only; never in a share link, the demo mode
(WKO5COACH_MODE=demo: the API answers 404, the UI hides it) or the AI coach.
Not a diagnosis.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from typing import Optional

AREAS = {"knee": "膝", "shin_calf": "小腿／脛骨", "achilles": "阿基里斯腱", "ankle": "腳踝",
         "foot": "足底／腳跟", "hip": "髖／臀", "thigh": "大腿", "low_back": "下背", "other": "其他"}
UNKNOWN = "unknown"
SIDES = {"left": "左", "right": "右", "both": "兩側"}
NO_SIDE = ("low_back",)
KINDS = {"overuse": "過度使用", "acute": "急性"}
SEVERITIES = {"mild": "輕", "moderate": "中", "severe": "重"}
SEVERITY_HELP = {"mild": "照練", "moderate": "減量或改練", "severe": "停跑"}
SEV_RANK = {"mild": 1, "moderate": 2, "severe": 3}
STATUSES = {"draft": "待補細節", "active": "進行中", "resolved": "好了"}
PAIN = {0: "沒痛", 1: "痠", 2: "痛", 3: "中斷"}

ATTACH_DAYS = 28        # 推估: a 痛 mark joins an open event of the same area that began ≤ 28 days before
RECUR_DAYS = 42         # 推估: same area within 42 days after the last one resolved = 復發 (§3.3)
CUSTOM_MAX_LEN = 12
CUSTOM_MAX = 30
NOTE_MAX = 1000
PAIN_MAX = 10
RETURN_RUN_MIN = 20.0   # §1.5: the first run ≥ 20 min with pain ≤ 1 ends the layoff (推估)

SETTING_PATTERN = "injury.pattern_alerts"   # 「跟受傷前很像」提醒, default off, n ≥ 5 to enable
SETTING_STEP_UP = "injury.reentry_step_up"  # 傷停後恢復期往上一級, default on
SETTING_AREAS = "injury.custom_areas"
PATTERN_MIN_N = 5

# Silbernagel KG, Thomeé R, Eriksson BI, Karlsson J. Am J Sports Med 2007;35(6):897–906
# (Methods, read 2026-10-02): "the pain was allowed to reach level 5 on the visual analog
# scale (VAS) … during the exercise training. The pain after the exercise program was
# allowed to reach 5 on the VAS but should have subsided by the following morning. Pain and
# stiffness in the Achilles tendon were not allowed to increase from week to week."
# Studied in Achilles tendinopathy only: for other areas it is 推估.
SILBERNAGEL = {"during_max": 5, "after_max": 5, "src": "Silbernagel 2007（阿基里斯腱疼痛監測模型）",
               "text": "疼痛監測（Silbernagel 2007）：跑的時候疼痛 ≤ 5/10；跑完 ≤ 5/10 且隔天早上要退回原本的程度；"
                       "疼痛和僵硬不能一週比一週多。原研究只看阿基里斯腱，用在其他部位是推估。"}
DISCLAIMER = "這不是醫療診斷。持續或加重的疼痛請看醫師或物理治療師。"


def demo_mode() -> bool:
    """The demo process (docs/plans/auth-and-demo.plan.md: WKO5COACH_MODE=demo) hides the feature."""
    return os.environ.get("WKO5COACH_MODE", "").strip().lower() == "demo"


# ---------------------------------------------------------------------------
# areas and validation
# ---------------------------------------------------------------------------

def clean_custom(label) -> Optional[str]:
    if not isinstance(label, str):
        return None
    s = " ".join(label.split())
    if not s or len(s) > CUSTOM_MAX_LEN or s == UNKNOWN or s in AREAS or s in AREAS.values():
        return None
    if any(ord(c) < 32 for c in s):
        return None
    return s


def valid_area(a) -> bool:
    return a == UNKNOWN or a in AREAS or clean_custom(a) == a


def norm_area(a) -> Optional[str]:
    """A stored area: a fixed key, UNKNOWN, a built-in label typed as text (→ its key) or a custom label."""
    if a is None:
        return None
    if isinstance(a, str):
        s = " ".join(a.split())
        for k, v in AREAS.items():
            if s == v:
                return k
        if s in AREAS or s == UNKNOWN:
            return s
        return clean_custom(s)
    return None


def area_label(a: Optional[str]) -> str:
    if not a or a == UNKNOWN:
        return "未指定部位"
    return AREAS.get(a, a)


def full_label(area: Optional[str], side: Optional[str]) -> str:
    s = SIDES.get(side or "", "")
    if side == "both":
        s = "雙側"
    return f"{s}{area_label(area)}"


def is_custom(a: Optional[str]) -> bool:
    return bool(a) and a != UNKNOWN and a not in AREAS


def validate_pain(pain, area=None) -> Optional[str]:
    if pain is not None and (isinstance(pain, bool) or not isinstance(pain, int) or pain not in PAIN):
        return "INVALID_PAIN"
    if area is not None and (not isinstance(area, str) or norm_area(area) is None):
        return "INVALID_AREA"
    return None


def _iso(d) -> Optional[str]:
    if d is None:
        return None
    try:
        return dt.date.fromisoformat(str(d)[:10]).isoformat() if len(str(d)) >= 10 else None
    except ValueError:
        return None


def validate_event(f: dict, today: Optional[dt.date] = None) -> Optional[str]:
    """Error code of an event create / patch body (only the keys present are checked)."""
    today = today or dt.date.today()
    if "area" in f and (f["area"] is None or norm_area(f["area"]) is None):
        return "INVALID_AREA"
    if "side" in f and f["side"] is not None and f["side"] not in SIDES:
        return "INVALID_SIDE"
    if "kind" in f and f["kind"] not in KINDS:
        return "INVALID_KIND"
    if "severity" in f and f["severity"] not in SEVERITIES:
        return "INVALID_SEVERITY"
    if "status" in f and f["status"] not in STATUSES:
        return "INVALID_STATUS"
    if "pain_max" in f and f["pain_max"] is not None and (
            isinstance(f["pain_max"], bool) or not isinstance(f["pain_max"], int) or not 0 <= f["pain_max"] <= PAIN_MAX):
        return "INVALID_PAIN_MAX"
    if "days_missed" in f and f["days_missed"] is not None and (
            isinstance(f["days_missed"], bool) or not isinstance(f["days_missed"], int) or not 0 <= f["days_missed"] <= 730):
        return "INVALID_DAYS_MISSED"
    for k in ("onset_date", "resolved_date"):
        if k in f and f[k] is not None:
            v = _iso(f[k])
            if v is None or v != str(f[k])[:10] or v > (today + dt.timedelta(days=1)).isoformat():
                return "INVALID_DATE"
    if "onset_date" in f and f["onset_date"] is None:
        return "INVALID_DATE"
    if "note" in f and f["note"] is not None and (not isinstance(f["note"], str) or len(f["note"]) > NOTE_MAX):
        return "INVALID_NOTE"
    if "pause_quality" in f and not isinstance(f["pause_quality"], bool):
        return "INVALID_PAUSE"
    return None


def check_dates(onset: Optional[str], resolved: Optional[str]) -> Optional[str]:
    if onset and resolved and resolved < onset:
        return "RESOLVED_BEFORE_ONSET"
    return None


# ---------------------------------------------------------------------------
# one-tap mark → event (pure)
# ---------------------------------------------------------------------------

def _d(x) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(x)[:10]) if x else None
    except ValueError:
        return None


def is_open(ev: dict) -> bool:
    return ev.get("status") in ("draft", "active")


def attach(pain: Optional[int], area: Optional[str], side: Optional[str], act_date: dt.date,
           act_key: Optional[str], act_file: Optional[str], current_id: Optional[int],
           events: list[dict], linked: Optional[dict] = None) -> dict:
    """What a pain mark does to the events (plan §1.3). `events`: every event as
    a dict; `linked`: {event id: activities attached to it, this one included}.
    Returns {"injury_id", "create" (fields of a new draft) | None,
    "update" ({id, fields}) | None, "delete" (an orphan draft id) | None}."""
    linked = linked or {}
    by = {e["id"]: e for e in events}
    cur = by.get(current_id) if current_id is not None else None
    out = {"injury_id": None, "create": None, "update": None, "delete": None}
    area = norm_area(area) if area else None
    alone = cur is not None and linked.get(cur["id"], 0) <= 1

    def drop_orphan():
        if cur is not None and cur.get("status") == "draft" and alone:
            out["delete"] = cur["id"]

    if pain is None or pain < 2:
        drop_orphan()
        return out
    sev = "moderate" if pain >= 3 else "mild"
    # the activity's own draft: a changed area / 中斷 updates it instead of opening another
    if cur is not None and cur.get("status") == "draft" and alone and cur.get("onset_key") == act_key:
        upd = {}
        if area and area != cur.get("area"):
            upd["area"] = area
        if side is not None and side != cur.get("side"):
            upd["side"] = side
        if SEV_RANK.get(sev, 1) > SEV_RANK.get(cur.get("severity"), 1):
            upd["severity"] = sev
        if upd:
            out["update"] = {"id": cur["id"], "fields": upd}
        out["injury_id"] = cur["id"]
        return out
    if cur is not None and is_open(cur) and (not area or cur.get("area") in (area, UNKNOWN)):
        out["injury_id"] = cur["id"]
        return out
    lo = act_date - dt.timedelta(days=ATTACH_DAYS)
    cands = [e for e in events if is_open(e) and (od := _d(e.get("onset_date"))) and lo <= od <= act_date
             and (not area or e.get("area") in (area, UNKNOWN))]
    if cands:
        # the same area first, then the latest onset
        best = sorted(cands, key=lambda e: (e.get("area") == area, e.get("onset_date")))[-1]
        if cur is not None and cur["id"] != best["id"]:
            drop_orphan()
        out["injury_id"] = best["id"]
        if area and best.get("area") == UNKNOWN and best.get("status") == "draft":
            out["update"] = {"id": best["id"], "fields": {"area": area}}
        return out
    drop_orphan()
    new = {"area": area or UNKNOWN, "side": side if side in SIDES else None, "kind": "overuse", "severity": sev,
           "onset_date": act_date.isoformat(), "onset_key": act_key, "onset_file": act_file, "status": "draft",
           "note": None, "recurrence_of": None}
    prev = recurrence(new, events)
    if prev:
        new["note"] = prev["note"]
        new["recurrence_of"] = prev["recurrence_of"]
    out["create"] = new
    return out


def recurrence(ev: dict, events: list[dict]) -> Optional[dict]:
    """The last resolved event of the same area before `ev`'s onset: {note,
    recurrence_of (its id when ≤ RECUR_DAYS after it resolved, else None), days}."""
    area, onset = ev.get("area"), _d(ev.get("onset_date"))
    if not area or area == UNKNOWN or onset is None:
        return None
    prev = [e for e in events if e.get("id") != ev.get("id") and e.get("area") == area
            and e.get("status") == "resolved" and _d(e.get("resolved_date")) and _d(e["resolved_date"]) <= onset]
    if not prev:
        return None
    p = max(prev, key=lambda e: e["resolved_date"])
    days = (onset - _d(p["resolved_date"])).days
    return {"note": f"距上次好了 {days} 天、同部位（#{p['id']}）", "days": days,
            "recurrence_of": p["id"] if days <= RECUR_DAYS else None}


# ---------------------------------------------------------------------------
# periods, days off
# ---------------------------------------------------------------------------

def end_of(ev: dict, today: dt.date) -> dt.date:
    """The last day of the event: resolved_date, else today (ongoing)."""
    r = _d(ev.get("resolved_date"))
    if ev.get("status") == "resolved" and r:
        return r
    return max(today, _d(ev.get("onset_date")) or today)


def active_on(events: list[dict], day: dt.date) -> list[dict]:
    """Events open on `day` (drafts included: a 中斷 is real even before the details)."""
    out = []
    for e in events:
        o = _d(e.get("onset_date"))
        if o is None or o > day:
            continue
        r = _d(e.get("resolved_date"))
        if e.get("status") == "resolved" and (r is None or r < day):
            continue
        out.append(e)
    return out


def days_off_auto(ev: dict, runs: list[tuple], today: dt.date) -> Optional[int]:
    """§1.5: days from the onset to the first run ≥ 20 min with pain ≤ 1 (or
    unmarked) after it, minus the athlete's usual rest days (the 4 weeks before
    the onset: share of days without a run × the span; 推估). `runs`:
    (date, minutes, pain) of every run. None while still off with no return."""
    o = _d(ev.get("onset_date"))
    if o is None:
        return None
    back = next((d for d, m, p in sorted(runs) if d > o and (m or 0) >= RETURN_RUN_MIN and (p is None or p <= 1)),
                None)
    end = back or (today if is_open(ev) else None)
    if end is None:
        return None
    span = (end - o).days - 1
    if span <= 0:
        return 0
    before = {d for d, m, p in runs if o - dt.timedelta(days=28) <= d < o}
    rest_share = 1.0 - len(before) / 28.0 if before else 0.0
    return max(0, int(round(span - rest_share * span)))


def event_json(ev: dict, today: dt.date, linked: int = 0, auto_days: Optional[int] = None) -> dict:
    o = _d(ev.get("onset_date")) or today
    end = end_of(ev, today)
    return {**{k: ev.get(k) for k in ("id", "area", "side", "kind", "severity", "pain_max", "onset_date", "onset_key",
                                      "onset_file", "status", "resolved_date", "days_missed", "pause_quality",
                                      "recurrence_of", "note")},
            "area_label": area_label(ev.get("area")), "label": full_label(ev.get("area"), ev.get("side")),
            "severity_label": SEVERITIES.get(ev.get("severity"), ""), "kind_label": KINDS.get(ev.get("kind"), ""),
            "status_label": STATUSES.get(ev.get("status"), ""), "custom_area": is_custom(ev.get("area")),
            "day_n": (end - o).days + 1, "open": is_open(ev), "linked": linked, "days_off_auto": auto_days,
            "days_off": ev.get("days_missed") if ev.get("days_missed") is not None else auto_days}


def summary(ev: Optional[dict], today: dt.date) -> Optional[dict]:
    """The chip on an activity: 「傷病紀錄 #12（進行中，第 5 天）」."""
    if not ev:
        return None
    j = event_json(ev, today)
    return {k: j[k] for k in ("id", "label", "status", "status_label", "severity", "severity_label", "day_n", "open",
                              "area", "side")}


# ---------------------------------------------------------------------------
# sync read (engine side: the planner, reentry, the analysis)
# ---------------------------------------------------------------------------

EVENT_COLS = ("id", "athlete_id", "area", "side", "kind", "severity", "pain_max", "onset_date", "onset_key",
              "onset_file", "status", "resolved_date", "days_missed", "pause_quality", "recurrence_of", "note")
_memo: dict = {}


def load_events(db_path=None, athlete_id: int = 1) -> list[dict]:
    """Every event (sync, read-only, memoised on the DB file). [] without the
    DB / table, and in the demo mode. Tests: activity_tags._default_db is
    patched to None, so this reads nothing unless given a path."""
    if demo_mode():
        return []
    from backend.engine import activity_tags as AT
    p = AT._db_path(db_path)
    if p is None or not p.exists():
        return []
    try:
        st = os.stat(p)
        stamp = (str(p), st.st_mtime_ns, st.st_size, athlete_id)
    except OSError:
        return []
    if _memo.get("stamp") == stamp:
        return _memo["rows"]
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            have = {r[1] for r in con.execute("PRAGMA table_info(injury_events)").fetchall()}
            cols = [c for c in EVENT_COLS if c in have]
            rows = [dict(zip(cols, r)) for r in con.execute(
                f"SELECT {', '.join(cols)} FROM injury_events WHERE athlete_id=? ORDER BY onset_date, id",
                (athlete_id,)).fetchall()] if cols else []
        finally:
            con.close()
    except sqlite3.Error:
        rows = []
    for r in rows:
        r["pause_quality"] = bool(r.get("pause_quality"))
    _memo.update(stamp=stamp, rows=rows)
    return rows


def pain_marks(tag_rows: list[dict]) -> list[dict]:
    """The activity marks with a pain value: {date, key, pain, area, injury_id}."""
    out = []
    for r in tag_rows or []:
        p = r.get("pain")
        if p is None or (r.get("start_local") or "") == "":
            continue
        out.append({"date": r["start_local"][:10], "key": r["start_local"], "pain": int(p),
                    "area": r.get("pain_area"), "injury_id": r.get("injury_id"), "file": r.get("file")})
    return out


# ---------------------------------------------------------------------------
# the planner (plan §4.2)
# ---------------------------------------------------------------------------

def pause_reason(events: list[dict], today: dt.date) -> Optional[str]:
    """`pause_quality` on an open event: 「傷病紀錄 #12 進行中：先不排間歇」 until it is resolved."""
    for e in active_on(events, today):
        if e.get("pause_quality"):
            return f"傷病紀錄 #{e['id']}（{full_label(e.get('area'), e.get('side'))}）進行中：先不排間歇"
    return None


def week_notes(events: list[dict], monday: dt.date, today: dt.date) -> list[dict]:
    """「右膝進行中（第 5 天）」 for the week plan (src "injury")."""
    sun = monday + dt.timedelta(days=6)
    out = []
    for e in events:
        o = _d(e.get("onset_date"))
        if o is None or o > sun or e.get("status") == "resolved":
            continue
        n = (min(today, sun) - o).days + 1
        if n < 1:
            continue
        sev = SEVERITIES.get(e.get("severity"), "")
        out.append({"level": "info", "src": "injury",
                    "text": f"{full_label(e.get('area'), e.get('side'))}進行中（第 {n} 天，{sev}：{SEVERITY_HELP.get(e.get('severity'), '')}）"})
    return out


def overlapping(events: list[dict], a: dt.date, b: dt.date, today: dt.date) -> Optional[dict]:
    """The event whose period overlaps [a, b] (a layoff caused by an injury), latest onset first."""
    hit = [e for e in events if (o := _d(e.get("onset_date"))) and o <= b and end_of(e, today) >= a]
    return max(hit, key=lambda e: e["onset_date"]) if hit else None


def honesty_tier(n: int) -> int:
    """§3.4: 0 none, 1 描述 (1–4), 2 + 中位與次數 (5–14), 3 + OR / CI (≥ 15)."""
    return 0 if n <= 0 else 1 if n < 5 else 2 if n < 15 else 3
