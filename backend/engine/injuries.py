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

Illness (SP-117, owner 2026-10-05): a 生病 event lives in the same log (category "illness", no
body area), one of two types — no COVID branch (docs/research/detraining.md §6.2):
  cold   輕微感冒: runny / blocked nose, sore throat, no fever. While symptoms last only Z1
         recovery runs (≤ ILL_RUN_MAX_MIN), no Zone 3 / Zone 5, no strides — Kaulback 2023 (IOC
         consensus group review: mild respiratory infection has minimal effect on cardiorespiratory
         endurance), Wallenfels / TrainingPeaks (above the neck: a zone-1 recovery session, coach).
  fever  發燒或全身症狀: fever, chest cough, aches all over, stomach bug. No running until
         FEVER_WAIT_DAYS after the symptoms are gone (Wallenfels: ≥ 1 day after below-the-neck
         symptoms; Watson / TrainingPeaks: 24–48 h symptom-free — coach-level); the first run back
         is at recovery pace (≤ FIRST_RUN_MAX_MIN).
onset_date = the first day of symptoms, resolved_date = the first symptom-free day (症狀消失日).
After that the break (last run → first run back) gets reentry.py's block by its length; an
illness never takes the injury step-up (injury.reentry_step_up: tissue healing — 推估). The
neck check only helps pick the type (Ruuskanen 2024: not scientific, may be partly useful).
Illness events are left out of the injury analysis and never take a pain mark.

Privacy (§5): local DB only; never in a share link, the demo mode
(WKO5COACH_MODE=demo: the API answers 404, the UI hides it).
Not a diagnosis.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from typing import Optional

from backend.i18n import N_, _

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
PAIN = {0: N_("沒痛"), 1: N_("痠"), 2: N_("痛"), 3: N_("中斷")}

ATTACH_DAYS = 28        # 推估: a 痛 mark joins an open event of the same area that began ≤ 28 days before
RECUR_DAYS = 42         # 推估: same area within 42 days after the last one resolved = 復發 (§3.3)
CUSTOM_MAX_LEN = 12
CUSTOM_MAX = 30
NOTE_MAX = 1000
PAIN_MAX = 10
RETURN_RUN_MIN = 20.0   # §1.5: the first run ≥ 20 min with pain ≤ 1 ends the layoff (推估)

# ---- 生病 (SP-117) -------------------------------------------------------------------------
CATEGORIES = {"injury": N_("傷"), "illness": N_("生病")}
ILLNESS = {"cold": N_("輕微感冒"), "fever": N_("發燒或全身症狀")}
ILLNESS_HELP = {"cold": N_("只有流鼻水、鼻塞、喉嚨痛，沒有發燒"),
                "fever": N_("發燒、咳嗽到胸、全身痠痛、腸胃炎")}
FEVER_WAIT_DAYS = 1     # Wallenfels: ≥ 1 day after the symptoms are gone; Watson: 24–48 h (coach-level)
ILL_RUN_MAX_MIN = 45    # 推估: a Z1 recovery run while a cold lasts
FIRST_RUN_MAX_MIN = 30  # 推估: the first run back after a fever, at recovery pace
SRC_COLD = N_("Kaulback 2023（IOC 共識小組系統性回顧：輕微呼吸道感染對心肺耐力影響很小）；"
              "Wallenfels／TrainingPeaks（脖子以上的症狀可以跑心率 1 區的恢復課，教練級）")
SRC_FEVER = N_("Wallenfels（脖子以下的症狀消失後至少等 1 天）；Watson／TrainingPeaks（無症狀 24–48 小時），教練級")

SETTING_PATTERN = "injury.pattern_alerts"   # 「跟受傷前很像」提醒, default off, n ≥ 5 to enable
SETTING_STEP_UP = "injury.reentry_step_up"  # 傷停後恢復期往上一級, default on
SETTING_AREAS = "injury.custom_areas"
PATTERN_MIN_N = 5

# Silbernagel KG, Thomeé R, Eriksson BI, Karlsson J. Am J Sports Med 2007;35(6):897–906
# (Methods, read 2026-10-02): "the pain was allowed to reach level 5 on the visual analog
# scale (VAS) … during the exercise training. The pain after the exercise program was
# allowed to reach 5 on the VAS but should have subsided by the following morning. Pain and
# stiffness in the Achilles tendon were not allowed to increase from week to week."
# Studied in Achilles tendinopathy only: SP-269 shows it for the 跟腱 condition only.
SILBERNAGEL = {"during_max": 5, "after_max": 5, "src": "Silbernagel 2007（阿基里斯腱疼痛監測模型）",
               "text": N_("疼痛監測（Silbernagel 2007）：跑的時候疼痛 ≤ 5/10；跑完 ≤ 5/10 且隔天早上要退回原本的程度；"
                          "疼痛和僵硬不能一週比一週多。原研究只看阿基里斯腱，用在其他部位是推估。")}
DISCLAIMER = N_("這不是醫療診斷。持續或加重的疼痛請看醫師或物理治療師。")

# ---- 傷別 (SP-269; docs/research/injury-graded-return.md §2.3, §4.2, owner §6.1 points 1–2) ----------
# Optional, picked by the user (the app never diagnoses: the texts say 「如果醫師或物理治療師說是…」).
# Each condition belongs to one body area; "other" fits any area. No condition = the general
# return-to-run rules (no longer Silbernagel's 5/10: it was only studied in the Achilles tendon).
CONDITIONS = {"achilles": N_("跟腱"), "plantar_fascia": N_("足底筋膜"), "itb": N_("髂脛束"), "pfp": N_("膝前痛"),
              "other": N_("其他")}
CONDITION_HELP = {"achilles": N_("阿基里斯腱的肌腱病變（腳跟上方的跟腱痛）"),
                  "plantar_fascia": N_("足底筋膜炎（腳跟、足弓痛，早上第一步最痛）"),
                  "itb": N_("髂脛束症候群（膝蓋外側痛）"),
                  "pfp": N_("髕股疼痛（膝蓋前面、膝蓋骨周圍痛）"),
                  "other": N_("醫師沒說，或不是上面這幾種")}
CONDITION_AREA = {"achilles": "achilles", "plantar_fascia": "foot", "itb": "knee", "pfp": "knee", "other": None}
# Esculier JF et al. BMC Musculoskelet Disord 2016 (trial protocol, PMC4702381, read 2026-10-06):
# pain ≤ 2/10 while running, "pain should return to before-training levels within 60 min post training"
ESCULIER = {"during_max": 2, "after_min": 60}
MONITOR = {
    "achilles": SILBERNAGEL["text"],
    "pfp": N_("疼痛監測（如果醫師或物理治療師說是膝前痛）：跑時 ≤ 2/10、跑完 60 分鐘內回到原本的程度"
              "（Esculier 2016 試驗計畫書）。"),
    "plantar_fascia": N_("如果醫師或物理治療師說是足底筋膜炎：沒有找到這種傷專用的疼痛數字，用一般回跑指引——"
                         "不能越跑越痛、不能改變跑姿、隔天不能更痛（Ohio State Wexner 回跑指引，臨床機構）。"),
    "itb": N_("如果醫師或物理治療師說是髂脛束症候群：沒有找到這種傷專用的疼痛數字，用一般回跑指引——"
              "不能越跑越痛、不能改變跑姿、隔天不能更痛（Ohio State Wexner 回跑指引，臨床機構）。"),
    "general": N_("疼痛監測（一般回跑指引）：不能越跑越痛、不能改變跑姿、隔天不能更痛"
                  "（Ohio State Wexner 回跑指引，臨床機構）。"),
}
MONITOR_SRC = {"achilles": "Silbernagel 2007", "pfp": "Esculier 2016", "plantar_fascia": "Ohio State Wexner",
               "itb": "Ohio State Wexner", "general": "Ohio State Wexner"}
# 小腿／脛骨: maybe a bone stress injury — no condition rules there, only 「先給醫師看」 (§4.2)
SHIN_NOTE = N_("小腿／脛骨的痛可能是骨應力傷害（疲勞性骨折）：先給醫師看。app 不給這個部位傷別規則。")


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


def is_illness(ev: Optional[dict]) -> bool:
    return bool(ev) and ev.get("category") == "illness"


def illness_label(kind: Optional[str]) -> str:
    """「生病（輕微感冒）」."""
    return _("生病（{what}）", what=_(ILLNESS.get(kind or "", N_("未指定"))))


def event_label(ev: dict) -> str:
    """The event's name: 「右膝」 for an injury, 「生病（發燒或全身症狀）」 for an illness."""
    return illness_label(ev.get("illness")) if is_illness(ev) else full_label(ev.get("area"), ev.get("side"))


def is_custom(a: Optional[str]) -> bool:
    return bool(a) and a != UNKNOWN and a not in AREAS


def validate_pain(pain, area=None) -> Optional[str]:
    if pain is not None and (isinstance(pain, bool) or not isinstance(pain, int) or pain not in PAIN):
        return "INVALID_PAIN"
    if area is not None and (not isinstance(area, str) or norm_area(area) is None):
        return "INVALID_AREA"
    return None


def validate_score(score) -> Optional[str]:
    """SP-271: the optional 0–10 「跑的時候最痛幾分」."""
    if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= PAIN_MAX):
        return "INVALID_PAIN_SCORE"
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
    for k in ("onset_date", "resolved_date", "walkrun_from"):
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
    if "category" in f and f["category"] not in CATEGORIES:
        return "INVALID_CATEGORY"
    if "illness" in f and f["illness"] is not None and f["illness"] not in ILLNESS:
        return "INVALID_ILLNESS"
    if "condition" in f and f["condition"] is not None and f["condition"] not in CONDITIONS:
        return "INVALID_CONDITION"
    return None


def check_condition(area: Optional[str], condition: Optional[str]) -> Optional[str]:
    """SP-269: a condition other than 「其他」 belongs to one body area (CONDITION_AREA) —
    「CONDITION_AREA_MISMATCH」 when the event's area is another one (e.g. 腳踝 + 膝前痛).
    An unknown area is filled by the caller (condition_area)."""
    if condition in (None, "other"):
        return None
    want = CONDITION_AREA.get(condition)
    if want is None or area in (want, None, UNKNOWN):
        return None
    return "CONDITION_AREA_MISMATCH"


def condition_label(condition: Optional[str]) -> str:
    return _(CONDITIONS[condition]) if condition in CONDITIONS else ""


def monitor(ev: Optional[dict] = None) -> dict:
    """The pain-monitoring text of an event by its condition (SP-269): 跟腱 = Silbernagel 2007 (as
    before), 膝前痛 = Esculier 2016's ≤ 2/10, the rest and no condition = the general return-to-run
    rules (Ohio State Wexner). A 小腿／脛骨 event without a condition says 「先給醫師看」 first.
    {"key", "text", "src", "disclaimer"} — every text keeps the 「這不是醫療診斷」 line."""
    ev = ev or {}
    c = ev.get("condition") if not is_illness(ev) else None
    key = c if c in MONITOR else "general"
    text = _(MONITOR[key])
    if c in (None, "other") and ev.get("area") == "shin_calf":
        text = _(SHIN_NOTE) + text
    return {"key": key, "text": text, "src": MONITOR_SRC[key], "disclaimer": _(DISCLAIMER)}


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
    events = [e for e in events if not is_illness(e)]      # a pain mark never joins a 生病 event
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
    if not area or area == UNKNOWN or onset is None or is_illness(ev):
        return None
    prev = [e for e in events if e.get("id") != ev.get("id") and e.get("area") == area and not is_illness(e)
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
    ill = is_illness(ev)
    return {**{k: ev.get(k) for k in ("id", "area", "side", "kind", "severity", "pain_max", "onset_date", "onset_key",
                                      "onset_file", "status", "resolved_date", "days_missed", "pause_quality",
                                      "recurrence_of", "note", "illness")},
            "category": "illness" if ill else "injury",
            "illness_label": _(ILLNESS[ev["illness"]]) if ill and ev.get("illness") in ILLNESS else "",
            # 傷別 (SP-269): the condition and its pain-monitoring text (illness: none)
            "condition": None if ill else ev.get("condition"),
            "walkrun_from": None if ill else ev.get("walkrun_from"),
            "condition_label": "" if ill else condition_label(ev.get("condition")),
            "monitor": None if ill else monitor(ev)["text"],
            "area_label": area_label(ev.get("area")), "label": event_label(ev),
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
                              "area", "side", "condition", "condition_label", "monitor")}


# ---------------------------------------------------------------------------
# sync read (engine side: the planner, reentry, the analysis)
# ---------------------------------------------------------------------------

EVENT_COLS = ("id", "athlete_id", "area", "side", "kind", "severity", "pain_max", "onset_date", "onset_key",
              "onset_file", "status", "resolved_date", "days_missed", "pause_quality", "recurrence_of", "note",
              "category", "illness", "condition", "walkrun_from")
_memo: dict = {}


def load_events(db_path=None, athlete_id: int = 1) -> list[dict]:
    """Every event (sync, read-only, memoised on the DB file, WAL included:
    db/filestamp.py). [] without the DB / table, and in the demo mode.
    Tests: activity_tags._default_db is patched to None, so this reads
    nothing unless given a path."""
    if demo_mode():
        return []
    from backend.db.filestamp import db_stamp
    from backend.engine import activity_tags as AT
    p = AT._db_path(db_path)
    if p is None or not p.exists():
        return []
    fs = db_stamp(p)
    if fs is None:
        return []
    stamp = (str(p), *fs, athlete_id)
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
                    "area": r.get("pain_area"), "injury_id": r.get("injury_id"), "file": r.get("file"),
                    "score": r.get("pain_score")})
    return out


def foot_log(ds, tag_rows: Optional[list] = None, since: Optional[dt.date] = None) -> list[dict]:
    """Every run / walk of the dataset with its pain mark (SP-271–273): {date, key, cat ("run" | "walk"),
    minutes, pain, score, area, injury_id}, oldest first; pain None = not marked. Runs: sport run (road /
    trail); walks: walking / hiking. `tag_rows`: activity_tags.load() (default: read it)."""
    from backend.engine import activity_tags as AT
    from backend.engine.overview import category, moving_s
    from backend.engine import workout_review as WR
    tags = AT.load() if tag_rows is None else tag_rows
    out = []
    for w in ds.workouts:
        c = category(w)
        cat = "run" if w.sport == "run" else "walk" if c in ("hike", "walk") or w.sport in ("walk", "hike") else None
        if cat is None:
            continue
        d = WR._wdate(w)
        if since is not None and d < since:
            continue
        u = AT.find(tags, w.entry.start, w.entry.file) if tags else None
        u = u or {}
        out.append({"date": d.isoformat(), "key": AT.key_of(w.entry.start) or d.isoformat(), "cat": cat,
                    "minutes": moving_s(w) / 60.0, "pain": u.get("pain"), "score": u.get("pain_score"),
                    "area": u.get("pain_area"), "injury_id": u.get("injury_id")})
    return sorted(out, key=lambda m: m["key"])


# ---------------------------------------------------------------------------
# the planner (plan §4.2)
# ---------------------------------------------------------------------------

def pause_reason(events: list[dict], today: dt.date) -> Optional[str]:
    """`pause_quality` on an open event: 「傷病紀錄 #12 進行中：先不排間歇」 until it is resolved.
    An illness (SP-117) pauses the intervals on its own: while the symptoms last, and after a fever
    through the first run back (illness_rule)."""
    for e in active_on(events, today):
        if e.get("pause_quality") and not is_illness(e):
            return f"傷病紀錄 #{e['id']}（{full_label(e.get('area'), e.get('side'))}）進行中：先不排間歇"
    r = illness_rule(events, today)
    if r is not None:
        return _("傷病紀錄 #{id}（{label}）：{what}", id=r["id"], label=r["label"], what=r["text"])
    return None


def illness_rule(events: list[dict], day: dt.date) -> Optional[dict]:
    """What a 生病 event allows on `day` (SP-117), the strictest when several: {"rule": "off" (no
    run: fever symptoms or the FEVER_WAIT_DAYS after them) | "z1" (cold symptoms: Z1 recovery runs
    only) | "first" (the first day a run is allowed again after a fever: recovery pace), "id",
    "label", "illness", "text", "src"}; None when no illness touches the day. The symptom days are
    [onset, resolved_date) — resolved_date is the first symptom-free day; an open one lasts."""
    best = None
    rank = {"off": 3, "z1": 2, "first": 1}
    for e in events:
        if not is_illness(e):
            continue
        o = _d(e.get("onset_date"))
        if o is None or o > day:
            continue
        r = _d(e.get("resolved_date")) if e.get("status") == "resolved" else None
        fever = e.get("illness") == "fever"
        if r is None or day < r:
            rule = "off" if fever else "z1"
        elif fever and day < r + dt.timedelta(days=FEVER_WAIT_DAYS):
            rule = "off"
        elif fever and day == r + dt.timedelta(days=FEVER_WAIT_DAYS):
            rule = "first"
        else:
            continue
        if best is None or rank[rule] > rank[best["rule"]]:
            text = {"off": _("發燒或全身症狀：先不跑，症狀全部消失後至少 {n} 天（最多 48 小時）再跑", n=FEVER_WAIT_DAYS),
                    "z1": _("輕微感冒有症狀：只排心率 1 區的恢復跑（≤ {m} 分），不排 3 區、5 區、加速跑", m=ILL_RUN_MAX_MIN),
                    "first": _("發燒好了之後的第一次跑：恢復配速、≤ {m} 分；之後照停跑天數排", m=FIRST_RUN_MAX_MIN)}[rule]
            best = {"rule": rule, "id": e.get("id"), "label": event_label(e), "illness": e.get("illness"),
                    "text": text, "src": _(SRC_FEVER if fever else SRC_COLD)}
    return best


# ---- 依傷別迴避課型 (SP-270; injury-graded-return.md §1.2, §2.2, §4.2, §4.5, owner §6.1 points 3–4) -----
# While an event with a condition is open (any severity; the day it resolves the rule is gone — §6.1
# point 3), these session types are not planned. Tags (overview.condition_apply maps them to sessions):
#   downhill   下坡離心課 (engine/downhill.py)        technical  技術地形課 (engine/technical.py)
#   climb      長爬坡反覆 (specific_phase.apply_climb: climbs, then runs down at race grade)
#   steep      陡坡健走 (engine/steep_hill.py)        me         ME 負重爬坡 (specific_phase.apply_me)
#   strides    加速跑／坡道衝刺 on an easy run
# 膝前痛 / 髂脛束: downhill is their main load factor — Esculier 2016 (「avoid downhill running」),
# Fredericson & Wolf 2005 (downhill among the training factors), Van Hooren 2024 (downhill loads the
# patellofemoral joint more); the trail long run becomes flat (§6.1 point 4). The 長爬坡反覆 runs down
# at race grade, so it goes too (推估: the same reason as the downhill session).
# 跟腱: no hill repeats, steep walk or strides — a coach's rule, plausible (uphill: forefoot strike,
# more calf work, Vernillo 2017) but no measuring study found: 推估. The long run keeps its terrain.
# 足底筋膜 / 其他: no session type is avoided. load_guard is untouched: these rules only cut.
CONDITION_AVOID = {"pfp": ("downhill", "technical", "climb"), "itb": ("downhill", "technical", "climb"),
                   "achilles": ("climb", "steep", "me", "strides")}
FLAT_LONG = ("pfp", "itb")
CONDITION_NOTE = {
    "pfp": N_("膝前痛進行中：先不排下坡課（Esculier 2016）；技術地形、長爬坡反覆也先不排，越野長跑改平路"),
    "itb": N_("髂脛束進行中：先不排下坡課（Fredericson 2005）；技術地形、長爬坡反覆也先不排，越野長跑改平路"),
    "achilles": N_("跟腱進行中：先不排爬坡反覆、陡坡健走、加速跑（推估：教練的說法，沒有找到量測研究）；長跑地形不變"),
}


def condition_rule(events: list[dict], day: dt.date) -> Optional[dict]:
    """What the open 傷別 events avoid on `day` (SP-270, the injury twin of illness_rule): {"avoid": set of
    tags (CONDITION_AVOID), "flat_long": bool, "events": [{id, label, condition}], "notes": [week-note
    texts]}; None when no event with an avoiding condition touches the day. An event counts on
    [onset, resolved_date) — from the day it resolves the plan goes back to normal; drafts count."""
    avoid, flat, evs, notes = set(), False, [], []
    for e in events or ():
        c = e.get("condition")
        if is_illness(e) or c not in CONDITION_AVOID:
            continue
        o = _d(e.get("onset_date"))
        if o is None or o > day:
            continue
        r = _d(e.get("resolved_date")) if e.get("status") == "resolved" else None
        if e.get("status") == "resolved" and (r is None or day >= r):
            continue
        avoid |= set(CONDITION_AVOID[c])
        flat = flat or c in FLAT_LONG
        lab = full_label(e.get("area"), e.get("side"))
        evs.append({"id": e.get("id"), "label": lab, "condition": c})
        t = _("{label}・{what}", label=lab, what=_(CONDITION_NOTE[c]))
        if t not in notes:
            notes.append(t)
    if not avoid:
        return None
    return {"avoid": avoid, "flat_long": flat, "events": evs, "notes": notes}


def condition_week(events: list[dict], monday: dt.date, today: Optional[dt.date] = None) -> Optional[dict]:
    """The condition rule a week's session modules ask (downhill / technical / steep_hill week_context):
    the one on the week's first planning day (today inside the current week, else its Monday)."""
    first = max(monday, today) if today is not None and today <= monday + dt.timedelta(days=6) else monday
    return condition_rule(events, first)


def avoids(rule: Optional[dict], tag: str) -> bool:
    return bool(rule) and tag in rule["avoid"]


# ---- 疼痛燈號 (SP-271; injury-graded-return.md §1.1, §2.3, §4.6, owner §6.1 points 6–8) ----------------
# The last marked run of an open injury decides (no daily log: the next run's mark stands in for
# 「隔天」, §6.1 point 7; an unmarked run changes nothing):
#   green   the 0–10 within the condition's limit (跟腱 ≤ 5 Silbernagel 2007, 膝前痛 ≤ 2 Esculier 2016;
#           others: not above the last score) and not ≥ RISE_YELLOW above the last one; tap only: 沒痛 / 痠
#   yellow  over the limit, or ≥ RISE_YELLOW above the last score; tap only: 痛
#   red     中斷, 0–10 ≥ RED_SCORE, two yellows in a row, or the event's severity 重 (停跑)
# Green: plan as usual minus the condition's avoided sessions (SP-270). Yellow (applied on its own, like
# every cut — §6.1 point 6): the rest of the week ≤ last week's actual minutes, no interval, the long
# run × YELLOW_LONG. Red: no run; strength / cross-training stay (「會痛的動作先不做」); the box offers
# 不排課日期 (injury_rest). Illness events take no light (SP-117).
RISE_YELLOW = 2         # 推估: 2 points above the last score
RED_SCORE = 7           # 推估
YELLOW_LONG = 0.75      # 推估: the long run one step shorter
LIMIT = {"achilles": SILBERNAGEL["during_max"], "pfp": ESCULIER["during_max"]}
LIGHTS = {"green": N_("綠燈"), "yellow": N_("黃燈"), "red": N_("紅燈"), "walkrun": N_("走跑階段")}
_LRANK = {"green": 0, "yellow": 1, "walkrun": 2, "red": 3}


def _mark_light(m: dict, prev_score: Optional[int], cond: Optional[str]) -> tuple[str, str]:
    """(color, reason) of one marked run (the two-yellows rule is light()'s)."""
    p, sc = m.get("pain"), m.get("score")
    if p == 3:
        return "red", _("標了「中斷」")
    if sc is not None and sc >= RED_SCORE:
        return "red", _("{s}/10，{n}/10 以上", s=sc, n=RED_SCORE)
    lim = LIMIT.get(cond)
    if sc is not None and (lim is not None or prev_score is not None):
        if lim is not None and sc > lim:
            return "yellow", _("上一次 {s}/10，超過{name}的 {lim}/10", s=sc, name=condition_label(cond), lim=lim)
        if prev_score is not None and sc - prev_score >= RISE_YELLOW:
            return "yellow", _("上一次 {s}/10，比再前一次（{p}/10）高 {d} 分", s=sc, p=prev_score, d=sc - prev_score)
        if lim is None and prev_score is not None and sc > prev_score:
            return "yellow", _("上一次 {s}/10，比再前一次（{p}/10）高", s=sc, p=prev_score)
        return "green", _("上一次 {s}/10", s=sc)
    if p == 2:
        return "yellow", _("標了「痛」")
    return "green", _("標了「{what}」", what=_(PAIN.get(p, "沒痛")))


def event_light(e: dict, marks: list[dict], day: dt.date, since: Optional[dt.date] = None) -> dict:
    """The light of one open injury on `day` (see above). `marks`: foot_log rows (runs count; walks are
    SP-272's); the event's own marks, and unlinked ones of its area or with no area. `since`: only the
    runs from that day (SP-272: after the walk-run start), and then a 重 severity no longer holds it red.
    {"color", "reason", "id", "label", "condition", "date" (the deciding run, None = no run yet)}."""
    o = _d(e.get("onset_date")) or day
    lo = max(o, since) if since else o
    runs = [m for m in marks if m.get("cat", "run") == "run" and m.get("pain") is not None
            and lo.isoformat() <= m["date"] <= day.isoformat()
            and (m.get("injury_id") == e.get("id") or (m.get("injury_id") is None
                                                         and m.get("area") in (None, e.get("area"))))]
    base = {"id": e.get("id"), "label": full_label(e.get("area"), e.get("side")), "condition": e.get("condition")}
    if e.get("severity") == "severe" and since is None:
        return {**base, "color": "red", "reason": _("嚴重度選了「重」（停跑）"), "date": None}
    color, reason, when, prev_score, prev_color = "green", _("還沒有標記疼痛的跑步"), None, None, None
    for m in sorted(runs, key=lambda x: x.get("key") or x["date"]):
        c, why = _mark_light(m, prev_score, e.get("condition"))
        if c == "yellow" and prev_color == "yellow":
            c, why = "red", _("連兩次黃燈（{why}）", why=why)
        color, reason, when, prev_color = c, why, m["date"], c
        if m.get("score") is not None:
            prev_score = m["score"]
    return {**base, "color": color, "reason": reason, "date": when}


# ---- 紅燈之後先走跑交替 (SP-272; injury-graded-return.md §2.4, §4.3, §4.6, owner §6.1 point 5) -----------
# Ohio State Wexner's return-to-running guideline (clinical, coach-level): first walk 30 min without
# pain, then walk / run 4/1 → 3/2 → 2/3 → 1/4 min, 2–3 times each with a rest day between run days,
# then 30 min continuous × 3; 「Do not progress phases if …」 = a 痛 repeats the stage. After red (SP-271):
# a walk / hike ≥ WALK_CHECK_MIN marked 沒痛 / 痠, or the 「可以開始走跑」 button (walkrun_from), starts
# it; an unmarked run counts as no pain and moves on (§6.1 point 5); 中斷 (or ≥ RED_SCORE) goes back to
# red. The stages are not re-entry days: reentry.py starts the Daniels block the day after the last
# continuous 30 (§6.1 point 5). Illness: never. The app doesn't judge whether running may start.
# Owner's decision (SP-273, 2026-10-06): while red, a run marked 沒痛 (its own light green) skips the walk
# check and the stages — straight back to green, the Daniels block starting on that run (episode "skipped").
# 痠 / 痛 / unmarked runs keep it red; once the walk-run started (walk check / button) runs are its sessions.
WALK_CHECK_MIN = 30
WALKRUN = ((4, 1), (3, 2), (2, 3), (1, 4))       # (walk, run) minutes per interval — Wexner
WALKRUN_REPS = 6        # 推估: Wexner 3–6 times per session; 6 × 5 min = 30 min, as long as the walk check
WALKRUN_PER_STAGE = 3   # Wexner: each stage 2–3 times; 3 = the conservative end (推估)
CONT_MIN, CONT_N = 30, 3                         # Wexner: 30 min continuous × 3


def _need(stage: int) -> int:
    return CONT_N if stage >= len(WALKRUN) else WALKRUN_PER_STAGE


def _relevant(m: dict, e: dict) -> bool:
    return m.get("injury_id") == e.get("id") or (m.get("injury_id") is None and m.get("area") in (None, e.get("area")))


def return_state(e: dict, marks: list[dict], day: dt.date) -> Optional[dict]:
    """The return-to-run state of an injury on `day` (SP-272) from its marked runs / walks (foot_log):
    {"phase": "light" (never red, or back after the walk-run / a run marked 沒痛) | "red" (waiting for the
    walk check / button / a run marked 沒痛) | "walkrun", "since" (light: the first day its marks count
    from), "stage" (0–3 walk / run, 4 = continuous 30), "n" (sessions done in the stage), "last_run" (the last walk-run / check day),
    "reason", "episodes": [{red, start, first_run, done, skipped (a 沒痛 run skipped the walk-run)}]};
    None for an illness."""
    if is_illness(e):
        return None
    o = _d(e.get("onset_date")) or day
    items = sorted((m for m in marks if o.isoformat() <= m["date"] <= day.isoformat() and _relevant(m, e)),
                   key=lambda m: m.get("key") or m["date"])
    button = e.get("walkrun_from")
    st = {"phase": "light", "since": None, "stage": 0, "n": 0, "last_run": None, "reason": "", "episodes": []}
    ep: Optional[dict] = None
    prev_score = prev_color = None
    red_key = ""

    def to_red(date: str, key: str, why: str):
        nonlocal ep, red_key
        ep = {"red": date, "start": None, "first_run": None, "done": None}
        st["episodes"].append(ep)
        st.update(phase="red", stage=0, n=0, reason=why)
        red_key = key

    def start(date: str, why_last: Optional[str]):
        ep["start"] = date
        st.update(phase="walkrun", stage=0, n=0, last_run=why_last)

    if e.get("severity") == "severe":
        to_red(o.isoformat(), "", _("嚴重度選了「重」（停跑）"))
    for m in items:
        if st["phase"] == "red" and button and button >= ep["red"] and button <= m["date"]:
            start(button, None)
        if st["phase"] == "light":
            if m.get("cat", "run") != "run" or m.get("pain") is None:
                continue
            c, why = _mark_light(m, prev_score, e.get("condition"))
            if c == "yellow" and prev_color == "yellow":
                c, why = "red", _("連兩次黃燈（{why}）", why=why)
            prev_color = c
            if m.get("score") is not None:
                prev_score = m["score"]
            if c == "red":
                to_red(m["date"], m.get("key") or m["date"], why)
            continue
        if st["phase"] == "red":
            if m.get("cat") == "walk" and (m.get("minutes") or 0) >= WALK_CHECK_MIN and m.get("pain") in (0, 1) \
                    and (m.get("key") or m["date"]) > red_key:
                start(m["date"], m["date"])
            elif m.get("cat", "run") == "run" and m.get("pain") == 0 and (m.get("key") or m["date"]) > red_key \
                    and _mark_light(m, None, e.get("condition"))[0] == "green":
                # owner's decision (SP-273, 2026-10-06, against the conservative default): a run marked
                # 沒痛 goes straight back to green — no walk check, no walk-run; the Daniels block (reentry)
                # starts on this run, like any first run back
                ep.update(start=m["date"], first_run=m["date"], done=m["date"], skipped=True)
                st.update(phase="light", since=m["date"], stage=0, n=0, last_run=m["date"], reason="")
                prev_color, prev_score = "green", m.get("score")
            elif m.get("cat", "run") == "run" and m.get("pain") == 3:
                red_key = m.get("key") or m["date"]
            continue
        # walk-run
        if m.get("cat", "run") != "run":
            continue
        if m.get("pain") == 3 or (m.get("score") is not None and m["score"] >= RED_SCORE):
            to_red(m["date"], m.get("key") or m["date"], _("走跑階段標了「中斷」") if m.get("pain") == 3
                   else _("{s}/10，{n}/10 以上", s=m["score"], n=RED_SCORE))
            continue
        ep["first_run"] = ep["first_run"] or m["date"]
        st["last_run"] = m["date"]
        if m.get("pain") == 2:
            continue                                    # 痛: the next session repeats the stage
        st["n"] += 1
        if st["n"] >= _need(st["stage"]):
            st["stage"] += 1
            st["n"] = 0
            if st["stage"] > len(WALKRUN):
                ep["done"] = m["date"]
                st.update(phase="light", since=(_d(m["date"]) + dt.timedelta(days=1)).isoformat(), stage=0)
                prev_score = prev_color = None
    if st["phase"] == "red" and button and button >= ep["red"] and button <= day.isoformat():
        start(button, None)
    return st


def walkrun_sessions(st: dict) -> list[dict]:
    """The sessions still to do in the walk-run (SP-272), in order: {"stage", "k" (its number in the
    stage, 1-based), "walk", "run", "reps", "minutes"} — walk / run stages, then the continuous 30s."""
    out = []
    for stage in range(st.get("stage", 0), len(WALKRUN) + 1):
        first = st.get("n", 0) if stage == st.get("stage", 0) else 0
        for k in range(first, _need(stage)):
            if stage < len(WALKRUN):
                w, r = WALKRUN[stage]
                out.append({"stage": stage, "k": k + 1, "walk": w, "run": r, "reps": WALKRUN_REPS,
                            "minutes": (w + r) * WALKRUN_REPS})
            else:
                out.append({"stage": stage, "k": k + 1, "walk": 0, "run": CONT_MIN, "reps": 1, "minutes": CONT_MIN})
    return out


def walkrun_steps(x: dict) -> dict:
    """The COROS / editor steps of a walk-run session (engine/workout_steps.py doc): ×reps of walk
    (no target) then easy run; the continuous 30 is one easy step."""
    from backend.engine import workout_steps as WS
    ids = WS._Ids("w")
    easy = {"type": "auto", "intent": "easy"}
    if x["walk"] <= 0:
        items = [WS.step(ids, "work", x["run"] * 60, easy, _("連續輕鬆跑 {m} 分", m=x["run"]))]
    else:
        items = [WS.rep(ids, x["reps"], [WS.step(ids, "rest", x["walk"] * 60, None, _("走路 {m} 分", m=x["walk"])),
                                         WS.step(ids, "work", x["run"] * 60, easy, _("輕鬆跑 {m} 分", m=x["run"]))],
                        True, _("走 {w}／跑 {r}", w=x["walk"], r=x["run"]))]
    return WS.doc(items, origin="template:lib:walkrun")


def light(events: list[dict], marks: list[dict], day: dt.date) -> Optional[dict]:
    """The worst light of the injuries open on `day` (SP-271); None without one (illness: never).
    SP-272: after red the event stays red until the walk check / button, then "walkrun" (the
    walk-run state in "walkrun"); back in "light" after it, only its marks from then count. A run
    marked 沒痛 while red goes straight back to "light" (owner's decision, SP-273)."""
    best = None
    for e in active_on(events or [], day):
        if is_illness(e) or (e.get("status") == "resolved" and _d(e.get("resolved_date")) == day):
            continue
        rs = return_state(e, marks, day)
        base = {"id": e.get("id"), "label": full_label(e.get("area"), e.get("side")), "condition": e.get("condition"),
                "date": None, "walkrun": rs}
        if rs["phase"] == "red":
            r = {**base, "color": "red", "reason": rs["reason"]}
        elif rs["phase"] == "walkrun":
            r = {**base, "color": "walkrun", "reason": _("走跑階段第 {s} 階", s=min(rs["stage"], len(WALKRUN)) + 1)}
        else:
            r = {**event_light(e, marks, day, since=_d(rs["since"]) if rs["since"] else None), "walkrun": rs}
        if best is None or _LRANK[r["color"]] > _LRANK[best["color"]]:
            best = r
    if best is not None:
        best["light_label"] = _(LIGHTS[best["color"]])
    return best


# ---- app 提議「好了」 (SP-273; injury-graded-return.md §2.4, §4.6, owner §6.1 point 8) ----------------------
# Proposed, never automatic: the user confirms (status resolved, resolved_date today — editable later).
# Both conditions:
#   1. the last 7 days' run time ≥ DONE_SHARE × the 4 weeks before the onset (reentry.prev_volume's
#      window, from the day before the onset; runs only) — Ohio State Wexner: back to normal training
#      at 75–80 % of the pre-injury volume (clinical, coach-level);
#   2. the last DONE_RUNS runs all marked 沒痛 / 痠, spanning ≥ DONE_SPAN_DAYS (推估).
# 「還沒」: not asked again for DONE_SNOOZE_DAYS (推估; suggestions.visible).
DONE_SHARE = 0.75
DONE_RUNS = 3
DONE_SPAN_DAYS = 14
DONE_SNOOZE_DAYS = 7


def done_check(e: dict, marks: list[dict], day: dt.date) -> Optional[dict]:
    """Whether an open injury looks healed on `day` (see above): {"id", "label", "pct", "recent_h",
    "pre_h", "runs" (the last DONE_RUNS dates), "span"}; None otherwise (an illness, still red or in the
    walk-run, no pre-injury running)."""
    if is_illness(e) or not is_open(e):
        return None
    o = _d(e.get("onset_date"))
    if o is None or o > day:
        return None
    rs = return_state(e, marks, day)
    if rs is None or rs["phase"] != "light":
        return None
    runs = [m for m in marks if m.get("cat", "run") == "run"]
    pre_a, pre_b = o - dt.timedelta(days=28), o - dt.timedelta(days=1)
    pre_h = sum(m.get("minutes") or 0.0 for m in runs if pre_a.isoformat() <= m["date"] <= pre_b.isoformat()) / 60.0 / 4.0
    if pre_h <= 0:
        return None
    lo = (day - dt.timedelta(days=6)).isoformat()
    recent_h = sum(m.get("minutes") or 0.0 for m in runs if lo <= m["date"] <= day.isoformat()) / 60.0
    if recent_h < DONE_SHARE * pre_h:
        return None
    after = sorted((m for m in runs if o.isoformat() < m["date"] <= day.isoformat()),
                   key=lambda m: m.get("key") or m["date"])[-DONE_RUNS:]
    if len(after) < DONE_RUNS or any(m.get("pain") not in (0, 1) for m in after):
        return None
    span = (_d(after[-1]["date"]) - _d(after[0]["date"])).days
    if span < DONE_SPAN_DAYS:
        return None
    return {"id": e.get("id"), "label": full_label(e.get("area"), e.get("side")),
            "pct": int(round(100 * recent_h / pre_h)), "recent_h": round(recent_h, 1), "pre_h": round(pre_h, 1),
            "runs": [m["date"] for m in after], "span": span}


def week_notes(events: list[dict], monday: dt.date, today: dt.date) -> list[dict]:
    """「右膝進行中（第 5 天）」 for the week plan (src "injury")."""
    sun = monday + dt.timedelta(days=6)
    out = []
    for e in events:
        o = _d(e.get("onset_date"))
        if is_illness(e):
            # SP-117: what the illness allows (the strictest day of the rest of the week)
            days = [max(today, monday) + dt.timedelta(days=k) for k in range((sun - max(today, monday)).days + 1)]
            rs = [r for r in (illness_rule([e], d) for d in days) if r is not None]
            if o is not None and o <= sun and rs:
                r = max(rs, key=lambda x: {"off": 3, "z1": 2, "first": 1}[x["rule"]])
                out.append({"level": "watch", "src": "illness",
                            "text": _("{label}：{what}（{src}）", label=r["label"], what=r["text"], src=r["src"])})
            continue
        if o is None or o > sun or e.get("status") == "resolved":
            continue
        n = (min(today, sun) - o).days + 1
        if n < 1:
            continue
        sev = SEVERITIES.get(e.get("severity"), "")
        out.append({"level": "info", "src": "injury",
                    "text": f"{full_label(e.get('area'), e.get('side'))}進行中（第 {n} 天，{sev}：{SEVERITY_HELP.get(e.get('severity'), '')}）"})
    return out


def overlapping(events: list[dict], a: dt.date, b: dt.date, today: dt.date,
                category: str = "injury") -> Optional[dict]:
    """The event of `category` ("injury" / "illness", SP-117) whose period overlaps [a, b] (a layoff
    caused by it), latest onset first."""
    hit = [e for e in events if (o := _d(e.get("onset_date"))) and o <= b and end_of(e, today) >= a
           and is_illness(e) == (category == "illness")]
    return max(hit, key=lambda e: e["onset_date"]) if hit else None


def honesty_tier(n: int) -> int:
    """§3.4: 0 none, 1 描述 (1–4), 2 + 中位與次數 (5–14), 3 + OR / CI (≥ 15)."""
    return 0 if n <= 0 else 1 if n < 5 else 2 if n < 15 else 3
