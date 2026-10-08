"""
Which activity executed which planned session (reconcile rule 1), and how the
run compared with the plan. Pure functions over plain dicts.

COROS link (checked 2026-10-02 on the stored data, read-only): the activity FIT
COROS returns has no workout / workout_step message, no wkt_step_index on the
laps and no plan / program id; the activity list rows the sync reads
(labelId, sportType, date, fitUrl) carry none either. The only "done" signal is
on the COROS calendar entry itself (schedule/query entity `executeStatus`,
sync/coros_workouts.find_entry), which needs a live call and still does not
say which activity did it. So matching is ours: the day + sport first.

Matching, one activity <-> one session:
  0. kept: sessions already done (a manual link, or an earlier match). Two
     sessions on the same activity (old data) keep only the better pair; the
     other one is re-matched.
  1. same day, the planned sport (compliance.accepted: easy / long / quality /
     test = a run, hike = hike / trail / walk, strength = strength; a walking
     session — 陡坡健走, 登山 / 健行 … — also a hike / walk / trail run): the pairs
     with the lowest cost (duration closest to the plan, terrain, intensity,
     the generator's own pick) first — so two runs on a day each take the
     closest session.
  2. long / quality / test only: the generator's own week-wide match (week_plan:
     the long done a day early, the intervals moved), when that activity is
     still free; the session moves to the activity's day. An easy run on a day
     with nothing planned stays a separate item (no session is pulled over).
  3. same day, any other endurance activity (planned a run, rode a bike): still
     linked, shown as 類型不符.
  An activity the user unlinked (`unlinked`) is never auto-matched again; the
  user can link it by hand (plan_store.link).

Planned vs actual intensity is graded, not pass / fail (SP-216: a session run
at 99 % of its intensity was 「沒照課表」). dose() = the run's quality work over
what the session needs; the bands are TrainingPeaks' compliance colours that
compliance.py already uses for time / TSS (±20 % / 50 %), applied to time at
intensity as interval-prescription.md #4 / #10 propose (TIZ ÷ planned TIZ):
  planned hard: ≥ 80 % done · 50–80 % 強度不足 (partial) · < 50 % ran easy (沒照課表)
  planned easy: a quality dose up to 150 % 偏強 (partial) · beyond: ran hard (沒照課表)
SP-370: with time and TSS both within ±20 % (compliance.time_tss_level green) the
「ran easy / ran hard」 and another foot sport are 部分 (short, `soft`) with the reason
(「時間和負荷都對，但跑成強度課」); 沒照課表 stays when time or TSS is also > 20 % off, or the
sport is not on foot. intervals.icu / TrainingPeaks rate completion by load / time; the
combination is 推估. A long run is planned easy like an easy run (planned_intensity), so the
150 % line and this rule apply to it the same.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.engine import compliance as C
from backend.i18n import _

ENDURANCE = {"road", "trail", "hike", "bike"}
NEVER = ("heat_passive", "notice")          # a bath / sauna / the 課表待確認 reminder: the user ticks it
HARD = ("quality", "test")
HARD_MIN_S = 600                            # overview.HARD_SESSION_S: ≥ 10 min at/above threshold
EASY_KINDS = ("easy", "long", "hike")
MATCH_LABEL = {"manual": "手動配對", "day": "同一天", "plan": "同一週（課表自動對到）"}
# intensity bands (SP-216): TrainingPeaks' compliance colours (green 80–120 %, yellow 50–79 % /
# 121–150 %; competitor-charts.md §1.2), here on the quality dose — 推估 for intensity
INTENSITY_DONE = 0.80            # planned hard: ≥ 80 % of the dose it needs = done
INTENSITY_OFF = 0.50             # < 50 % = ran easy (沒照課表); 50–80 % = 強度不足
EASY_OVER_OFF = 1.50             # planned easy: a quality dose ≥ 150 % = ran hard (沒照課表); below = 偏強
HARD_TYPES = ("quality", "hard_long", "test_cp")   # the session classifier's hard classes


def _f(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def sport_ok(s: dict, a: dict) -> bool:
    """The activity is the planned kind of sport."""
    cat = a.get("category")
    if s.get("kind") in NEVER:
        return False
    ok = C.accepted(s)                      # a walking session also takes a hike / walk (SP-370)
    return cat in (ENDURANCE if ok is None else ok)


def can_match(s: dict, a: dict) -> bool:
    """Any automatic match at all (pass 3 allows another endurance sport)."""
    if s.get("kind") in NEVER:
        return False
    if s.get("kind") == "strength":
        return a.get("category") == "strength"
    return a.get("category") in ENDURANCE or sport_ok(s, a)


def hard_need(s: dict) -> float:
    try:
        from backend.engine import quality_gate as QG
        return QG.hard_need(s.get("title") or "", HARD_MIN_S, s.get("variant_key"), s.get("variant_reps"))
    except Exception:                       # noqa: BLE001 — a bad title never breaks matching
        return HARD_MIN_S


def is_aet(s: dict) -> bool:
    from backend.engine.aet_test import is_aet_session
    return is_aet_session(s)


def actual_intensity(a: dict, need: float = HARD_MIN_S) -> Optional[str]:
    """'hard' (≥ need seconds at/above threshold — the generator's own done
    rule for a quality session) / 'easy' / None (not measured). With the session
    classifier's row (`session`, overview.activity_row(w, ds)) its class decides:
    Z5 / Z3 / 高強度長跑 / CP test = hard, the rest easy (vo2max-session-detection.md)."""
    t = (a.get("session") or {}).get("type")
    if t:
        return "hard" if t in HARD_TYPES else "easy"
    h = _f(a.get("hard_s"))
    if h is None:
        return None
    return "hard" if h >= need else "easy"


def dose(a: dict, need: float = HARD_MIN_S) -> Optional[dict]:
    """How much quality work the run holds against what a quality session needs:
    {ratio, by, done_s, need_s}. With the session classifier's row (as actual_intensity:
    it decides first) the better of Zone 3 time ÷ 10 min and equivalent T@VO2max ÷ 4 min —
    its own bars (session_stimulus.Z3_NEED_S / Z5_MIN_S, vo2max-session-detection.md §3.5) —
    and at least 1 when it classed the run hard; without one (or without its numbers) the
    seconds at threshold ÷ `need` (the generator's done rule). None: nothing measured."""
    ses = a.get("session") or {}
    t = ses.get("type")
    if t:
        from backend.engine import session_stimulus as SS
        cands = [(v / q, by, v, q) for by, v, q in (("z3", _f(ses.get("z3_s")), SS.Z3_NEED_S),
                                                    ("z5", _f(ses.get("t_vo2_eq_s")), SS.Z5_MIN_S)) if v is not None]
        if cands or t in HARD_TYPES:
            r, by, v, q = max(cands) if cands else (0.0, "class", None, None)
            if t in HARD_TYPES and r < 1.0:
                r, by = 1.0, "class"
            return {"ratio": r, "by": by, "done_s": v, "need_s": q}
    h = _f(a.get("hard_s"))
    if h is None or not need or need <= 0:
        return None
    return {"ratio": h / need, "by": "hard", "done_s": h, "need_s": need}


def graded(s: dict, a: dict) -> tuple[Optional[str], Optional[str], Optional[dict]]:
    """(planned, actual, dose): actual = hard / short (強度不足) / easy for a planned
    hard session, easy / over (偏強) / hard for a planned easy one, None when not measured."""
    p = planned_intensity(s)
    if p is None:
        return None, None, None
    if p == "hard":
        d = dose(a, hard_need(s))
        if d is None:
            return p, None, None
        r = d["ratio"]
        return p, ("hard" if r >= INTENSITY_DONE else "short" if r >= INTENSITY_OFF else "easy"), d
    got = actual_intensity(a, HARD_MIN_S)
    if got != "hard":
        return p, got, None
    if (a.get("session") or {}).get("type") == "test_cp":
        return p, "hard", None              # a CP test on an easy day: ran hard, never 「偏強」
    d = dose(a, HARD_MIN_S)
    return p, ("over" if d is not None and d["ratio"] < EASY_OVER_OFF else "hard"), d


def _dose_text(d: Optional[dict]) -> str:
    """「閾值以上 7／10 分」: the measure the grade came from."""
    if not d or d.get("done_s") is None or not d.get("need_s"):
        return ""
    a, b = d["done_s"] / 60.0, d["need_s"] / 60.0
    if d["by"] == "z3":
        return _("Zone 3 {a:.0f}／{b:.0f} 分", a=a, b=b)
    if d["by"] == "z5":
        return _("等效 T@VO2max {a:.1f}／{b:.0f} 分", a=a, b=b)
    return _("閾值以上 {a:.0f}／{b:.0f} 分", a=a, b=b)


def planned_intensity(s: dict) -> Optional[str]:
    k = s.get("kind")
    if k == "test" and is_aet(s):
        return "easy"                       # the AeT test is a steady run below AeT
    if k in HARD:
        return "hard"
    if k in ("easy", "long"):
        return "easy"
    return None


def cost(s: dict, a: dict) -> float:
    """Lower = a better pair (only compared between pairs of one day)."""
    c = 0.0 if sport_ok(s, a) else 2.0
    mins = float(s.get("minutes") or 0)
    mov = _f(a.get("moving_s"))
    if mins > 0 and mov is not None:
        c += min(2.0, abs(mov / 60.0 / mins - 1.0))
    ter, cat = s.get("terrain"), a.get("category")
    if ter in ("road", "trail") and cat in ("road", "trail") and ter != cat:
        c += 0.3
    p, got, _d = graded(s, a)
    if got in ("hard", "easy") and got != p:          # 強度不足 / 偏強 still fit the session
        c += 0.6
    return c


def key_session(s: dict) -> bool:
    """Long / quality / test: the generator's own week-wide match (long ≥ 80 % of
    the planned time, quality / test its seconds at threshold) is evidence enough
    that it was done on another day. An easy run on an unplanned day stays its
    own item."""
    return s.get("kind") in ("long", "quality", "test") or s.get("gen_key") == "long"


def _done_index(s: dict):
    d = s.get("done_by")
    return d.get("index") if s.get("state") == "done" and isinstance(d, dict) else None


def dedupe(out: list[dict], acts_by_index: dict, today: str, covered: Optional[str], changes: list) -> None:
    """Old rows can hold one activity twice (a CP test and an easy run on one
    run): the manual link, else the same-day / lower-cost pair stays done; the
    rest go back to open (re-matched below, else missed)."""
    groups: dict = {}
    for s in out:
        i = _done_index(s)
        if i is not None:
            groups.setdefault(i, []).append(s)
    for i, ss in groups.items():
        if len(ss) < 2:
            continue
        a = acts_by_index.get(i) or ss[0]["done_by"]

        def rank(s):
            manual = (s["done_by"] or {}).get("match") == "manual"
            return (0 if manual else 1, 0 if s.get("day") == a.get("date") else 1, cost(s, a))
        keep, *rest = sorted(ss, key=rank)
        for s in rest:
            s["state"], s["done_by"] = "active", None
            changes.append({"action": "unmatched", "uid": s["uid"], "day": s.get("day"), "title": s.get("title"),
                            "kind": s.get("kind"), "reason": f"同一筆活動已算給「{keep.get('title')}」"})


def assign(out: list[dict], activities: list[dict], today: str, gen_done: dict,
           unlinked: Optional[set] = None, covered: Optional[str] = None) -> list[dict]:
    """Rule 1 in place on `out` (stored sessions). `gen_done`: {(week_start,
    gen_key): generated session with done / done_by} (week_plan's own match).
    Returns the changes (done / missed / unmatched)."""
    unlinked = set(unlinked or ())
    changes: list[dict] = []
    # stored done_by indexes -> these activities' indexes by start (activity_key.py:
    # a source switch / 同步資料 / a late-synced older run renumbers the dataset)
    from backend.engine import activity_key as AK
    AK.rebase_done_by(out, activities)
    by_index = {a.get("index"): a for a in activities}
    dedupe(out, by_index, today, covered, changes)
    used = {i for i in (_done_index(s) for s in out) if i is not None}
    free = lambda a: a.get("index") not in used and a.get("index") not in unlinked   # noqa: E731
    open_ = [s for s in out if s["state"] in ("active", "missed") and s.get("kind") not in NEVER
             and s.get("day") and s["day"] <= today]
    open_ += [s for s in out if s["state"] == "active" and s.get("kind") not in NEVER
              and s.get("day") and s["day"] > today and s.get("gen_key")
              and (s["week_start"], s["gen_key"]) in gen_done]       # done ahead of its day (pass 2)
    hint = {}
    for s in open_:
        g = gen_done.get((s["week_start"], s.get("gen_key"))) if s.get("gen_key") else None
        if g is not None and isinstance(g.get("done_by"), dict):
            hint[s["uid"]] = g["done_by"]

    def take(s, a, how):
        s["state"] = "done"
        s["done_by"] = {**a, "match": how}
        if how == "plan" and a.get("date"):
            s["day"] = a["date"]
        used.add(a.get("index"))
        changes.append({"action": "done", "uid": s["uid"], "day": s.get("day"), "title": s.get("title"),
                        "kind": s.get("kind"), "minutes": s.get("minutes"), "origin": s.get("origin"),
                        "edited": s.get("edited")})

    def same_day(strict: bool):
        pairs = []
        for s in open_:
            if s["state"] == "done" or s["day"] > today:
                continue
            for a in activities:
                if a.get("date") != s["day"] or not free(a):
                    continue
                if not (sport_ok(s, a) if strict else can_match(s, a)):
                    continue
                c = cost(s, a) - (0.2 if (hint.get(s["uid"]) or {}).get("index") == a.get("index") else 0.0)
                pairs.append((c, s.get("day"), s["uid"], a.get("index"), s, a))
        for c, _, _, _, s, a in sorted(pairs, key=lambda p: p[:4]):
            if s["state"] != "done" and free(a):
                take(s, a, "day")

    same_day(True)
    for s in sorted(open_, key=lambda s: s.get("day") or ""):
        h = hint.get(s["uid"])
        if s["state"] == "done" or h is None or not key_session(s):
            continue
        a = by_index.get(h.get("index")) or h
        if free(a) and can_match(s, a):
            take(s, a, "plan")
    same_day(False)
    for s in open_:
        if (s["state"] == "active" and s["day"] < today and (covered is None or s["day"] <= covered)):
            s["state"] = "missed"
            changes.append({"action": "missed", "uid": s["uid"], "day": s.get("day"), "title": s.get("title"),
                            "kind": s.get("kind"), "minutes": s.get("minutes"), "origin": s.get("origin"),
                            "edited": s.get("edited"), "reason": "沒有對應的活動"})
    return changes


# ---------------------------------------------------------------------------
# planned vs actual (the 課表 page)
# ---------------------------------------------------------------------------

INTENSITY_LABEL = {"hard": "強度", "easy": "輕鬆"}


def compare(s: dict, planned_tss: Optional[float] = None) -> Optional[dict]:
    """A done session vs its activity: {off_plan, short, soft, planned, actual, intensity_pct,
    text, short_text, match}. off_plan (沒照課表): the planned intensity was not run
    (排間歇、跑成輕鬆 — under 50 % of the dose — or the other way) or another sport;
    short (部分): 強度不足 (50–80 %) or 偏強; or (SP-370, `soft` intensity / sport) the
    reversal or another foot sport while time and TSS are both within ±20 % (`planned_tss`:
    the estimate the page uses, as compliance.session_compliance). `actual` stays hard / easy
    (short reads hard, it was intensity; 偏強 reads hard). The time / TSS colour is compliance.py's part."""
    a = s.get("done_by") if s.get("state") == "done" else None
    if not isinstance(a, dict):
        return None
    p, grade, d = graded(s, a)
    got = {"short": "hard", "over": "hard"}.get(grade, grade)
    wrong_sport = bool(a.get("category")) and not sport_ok(s, a) and s.get("kind") not in NEVER
    reversed_ = bool(p) and grade in ("hard", "easy") and grade != p
    # time and TSS on plan: another foot sport / the reversal is 部分, not 沒照課表 (SP-370, 推估)
    on_plan = C.time_tss_level(s, planned_tss) == "green"
    soft = None
    why = []
    if wrong_sport:
        if on_plan and C.foot_swap(s, a.get("category")):
            soft = "sport"
        else:
            why.append(f"排{_kind_label(s)}，實際{a.get('category_label') or a.get('category')}")
    elif reversed_:
        if on_plan:
            soft = "intensity"
        else:
            why.append(f"排{_kind_label(s)}，實際跑{'強度' if grade == 'hard' else '輕鬆'}")
    short = not why and (soft is not None or grade in ("short", "over"))
    pct = None if d is None or p != "hard" else round(min(d["ratio"], 9.99) * 100)
    what = _dose_text(d)
    if soft == "sport":
        short_text = _("時間和負荷都對，但項目不同：排{kind}，實際{act}", kind=_kind_label(s),
                       act=a.get("category_label") or a.get("category"))
    elif soft == "intensity":
        short_text = _("時間和負荷都對，但跑成強度課") if grade == "hard" else _("時間和負荷都對，但跑成輕鬆")
    else:
        short_text = "" if not short else (
            _("強度不足：只做到這堂課的 {pct}%", pct=pct) if grade == "short" else _("輕鬆跑偏強"))
    if short_text and what and soft != "sport":
        short_text += _("（{what}）", what=what)
    ter, cat = s.get("terrain"), a.get("category")
    terrain_off = ter in ("road", "trail") and cat in ("road", "trail") and ter != cat
    hs = _f(a.get("hard_s"))
    return {"off_plan": bool(why), "short": short, "soft": soft, "planned": p, "actual": got, "grade": grade,
            "intensity_pct": pct, "wrong_sport": wrong_sport,
            "terrain_off": terrain_off, "text": "沒照課表：" + why[0] if why else "",
            "short_text": short_text,
            "hard_min": None if hs is None else round(hs / 60.0, 1),
            "need_min": round(hard_need(s) / 60.0, 1) if p == "hard" else None,
            "match": a.get("match") or "day", "match_label": MATCH_LABEL.get(a.get("match") or "day", "")}


def _kind_label(s: dict) -> str:
    from backend.engine.plan_store import KINDS
    return KINDS.get(s.get("kind"), s.get("kind") or "")


def candidates(s: dict, activities: list[dict], used: set, days: int = 1) -> list[dict]:
    """Activities the user can link `s` to: ± `days` around its day, not used by
    another session, endurance / strength as the kind allows (closest first)."""
    if not s.get("day") or s.get("kind") in NEVER:
        return []
    d0 = dt.date.fromisoformat(s["day"])
    out = []
    for a in activities:
        try:
            gap = abs((dt.date.fromisoformat(a.get("date")) - d0).days)
        except (TypeError, ValueError):
            continue
        if gap <= days and a.get("index") not in used and can_match(s, a):
            out.append((gap, cost(s, a), a))
    return [a for _, _, a in sorted(out, key=lambda x: (x[0], x[1]))]
