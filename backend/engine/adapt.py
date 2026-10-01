"""
Adaptive adjustments to the generated plan, from what actually happened
(docs/spec/plan-auto.spec.md). Pure functions over plain dicts.

adapt() runs on the generator's weeks *before* reconcile() (engine/reconcile.py
rule 2 overwrites every unedited auto session with the generator's output, so
an adjustment made on the stored rows would be undone by the next reconcile).
Same inputs -> same adjusted weeks -> reconcile reports no change: the
adjustments are idempotent by construction.

Only the current week is adjusted. Sessions the user edited, added, deleted or
that are done are never touched (their gen_key is "locked").

Rules (thresholds: source or 自組):
  A. missed easy run -> its make-up is dropped (the generator would re-place it
     on a later day). Seiler「easy days easy」; not making it up is 自組.
  B. missed quality / test -> kept on the generator's new day only when it is
     ≥ 2 days from the long run and every other hard day (plan_prefs.place()'s
     48-h rule); else moved to a free day that keeps the spacing; else
     cancelled. The next week repeats the same dose step: quality_gate's dose
     step only counts sessions actually done (no code needed here).
  C. missed long run -> kept / moved to a free day of the same week that is not
     next to a quality / test day; else cancelled. Never carried into next week
     (reconcile never moves a session across weeks).
  D. easy run done too hard: the session stays done. Detected by
     overhard(): avg HR > AeT + 3 bpm (workout_review.AET_MARGIN), or time
     above AeT+3 > 10 % (workout_review.OVER_AET_SHARE), or avg power > 80 %
     CP (zones z2 upper bound, Palladino 1C), or TSS > planned + 20 %
     (TrainingPeaks compliance green band, engine/compliance.py). Using any
     one of the four (OR) is 自組. Then:
       1. the actual TSS counts (plan_store.plan_summary uses done_by.tss;
          CTL/ATL already come from the real data);
       2. a hard session < 2 days after it moves later in the week if the 48-h
          spacing allows, else steps down one dose step, else becomes an easy
          run (自組);
       3. the remaining easy runs this week lose the excess TSS (actual −
          planned), each ≥ 20 min, else the last easy is dropped (自組); the
          long run and the quality session are never trimmed for this;
       4. a note on that day:「輕鬆跑偏強（…）：已調整之後的課表」.
  E. fatigue guard: two red-compliance sessions in a row (engine/compliance.py)
     -> the quality steps down to the recovery fartlek and easy minutes × 0.8;
     TSB < −30 (when week_plan has not already made it a recovery week) or a
     CTL ramp ≥ status.RAMP["short"] (7/week, Palladino) -> the quality is
     removed and easy minutes × 0.8. The 20 % cut is 自組. TSB < −30 itself is
     week_plan's existing recovery-week rule and ramp ≥ 7 is quality_gate's
     existing block: those are not repeated when they already acted.
"""
from __future__ import annotations

import copy
import datetime as dt
from typing import Optional

WEEKDAYS = "一二三四五六日"
HARD = ("quality", "test")
SIDE = ("strength", "heat_passive", "notice")

# D. easy run done too hard (each one alone is enough; the OR is 自組)
OVER_HR_BPM = 3.0          # workout_review.AET_MARGIN: "easy" = avg HR ≤ AeT + 3
OVER_SHARE = 0.10          # workout_review.OVER_AET_SHARE: > 10 % of the time above AeT + 3
EASY_POWER_CAP = 0.80      # zones.py z2 upper bound (Palladino 1C: 75–80 % CP)
OVER_TSS = 0.20            # compliance.COMPLIANCE["green"] (TrainingPeaks ±20 %)
MIN_EASY_MIN = 20          # 自組: a trimmed easy run is never shorter than this
SPACING_DAYS = 2           # plan_prefs.place(): 48 h between hard days / the long run
# E. fatigue guard
TSB_FLOOR = -30.0          # week_plan(): TSB < −30 -> recovery week
RAMP_SHORT = 7.0           # status.RAMP["short"] (Palladino)
FATIGUE_CUT = 0.80         # 自組: easy minutes × 0.8
RED_STREAK = 2             # 自組: two red sessions in a row

SRC_SEILER = "Seiler：easy days easy；不補課屬自組"
SRC_SPACING = "plan_prefs.place() 48 小時間隔"
SRC_OVER = "workout_review AeT+3／>10%、z2 上限 80% CP（Palladino）、TrainingPeaks ±20%；四擇一屬自組"
SRC_FATIGUE = "Palladino CTL ramp ≥ 7；TSB < −30（week_plan）；連兩堂紅色、減 20% 屬自組"


def wd(day: str) -> str:
    return "週" + WEEKDAYS[dt.date.fromisoformat(day).weekday()]


def _d(day: str) -> dt.date:
    return dt.date.fromisoformat(day)


def _gap(a: str, b: str) -> int:
    return abs((_d(a) - _d(b)).days)


def overhard(planned_tss: Optional[float], r: Optional[dict]) -> Optional[str]:
    """Why a done easy run was too hard (Traditional Chinese), or None.
    `r`: {avg_hr, aet, over_aet_s, hr_s, avg_power, cp, tss}."""
    if not r:
        return None
    aet, hr = r.get("aet"), r.get("avg_hr")
    if aet and hr and hr > aet + OVER_HR_BPM:
        return f"平均心率 {hr:.0f} > AeT+{OVER_HR_BPM:.0f}（{aet + OVER_HR_BPM:.0f}）"
    over, tot = r.get("over_aet_s"), r.get("hr_s") or 0
    if over is not None and tot > 0 and over / tot > OVER_SHARE:
        return f"心率超過 AeT+{OVER_HR_BPM:.0f} 的時間 {over / tot * 100:.0f}% > {OVER_SHARE * 100:.0f}%"
    p, cp = r.get("avg_power"), r.get("cp")
    if p and cp and p > EASY_POWER_CAP * cp:
        return f"平均功率 {p:.0f} W > {EASY_POWER_CAP * 100:.0f}% CP（{EASY_POWER_CAP * cp:.0f} W）"
    t = r.get("tss")
    if t and planned_tss and t > planned_tss * (1 + OVER_TSS):
        return f"TSS {t:.0f} > 計畫 {planned_tss:.0f} 的 +{OVER_TSS * 100:.0f}%"
    return None


class _Week:
    """The current generated week plus what the stored plan says about it."""

    def __init__(self, w: dict, stored: list[dict], ctx: dict):
        self.w = w
        self.ws = w["start"]
        self.today = ctx["today"]
        self.blocked = ctx.get("blocked") or {}
        self.allowed = ctx.get("allowed_days")
        self.first = ctx.get("first_free") or self.today
        self.st = [s for s in stored if s.get("week_start") == self.ws]
        self.locked = {s["gen_key"] for s in self.st if s.get("gen_key") and (
            s["state"] in ("deleted", "superseded", "done") or (s["state"] == "active" and s.get("edited")))}
        # kept user sessions (edited / custom) occupy their days
        self.kept = [s for s in self.st if s["state"] == "active" and (s.get("origin") == "custom" or s.get("edited"))
                     and s.get("day") and s["kind"] not in SIDE]

    def gens(self) -> list[dict]:
        return self.w["sessions"]

    def free(self, g: dict) -> bool:
        return not g.get("done") and g["id"] not in self.locked

    def remove(self, g: dict) -> None:
        self.w["sessions"] = [x for x in self.w["sessions"] if x is not g]

    def days(self) -> list[str]:
        a = _d(self.ws)
        return [(a + dt.timedelta(days=i)).isoformat() for i in range(7)]

    def occupied(self, skip=None) -> set:
        out = {g["day"] for g in self.gens() if g is not skip and g.get("day") and g.get("kind") not in SIDE
               and (g.get("done") or g["id"] not in self.locked)}
        out |= {s["day"] for s in self.kept}
        out |= {s["day"] for s in self.st if s["state"] == "done" and s.get("day") and s["kind"] not in SIDE}
        return out

    def hard_days(self, skip=None) -> list[str]:
        out = [g["day"] for g in self.gens() if g is not skip and g.get("kind") in HARD and g.get("day")
               and (g.get("done") or g["id"] not in self.locked)]
        out += [s["day"] for s in self.st if s["kind"] in HARD and s.get("day")
                and (s["state"] == "done" or (s["state"] == "active" and (s.get("edited") or s.get("origin") == "custom")))]
        return out

    def long_days(self, skip=None) -> list[str]:
        out = [g["day"] for g in self.gens() if g is not skip and g.get("id") == "long" and g.get("day")
               and (g.get("done") or g["id"] not in self.locked)]
        out += [s["day"] for s in self.st if s["kind"] == "long" and s.get("day")
                and (s["state"] == "done" or (s["state"] == "active" and (s.get("edited") or s.get("origin") == "custom")))]
        return out

    def open_days(self, skip=None, after: Optional[str] = None) -> list[str]:
        occ = self.occupied(skip)
        return [d for d in self.days() if d >= self.first and (after is None or d > after) and d not in occ
                and d not in self.blocked and (not self.allowed or self.allowed[_d(d).weekday()])]


def _hard_ok(wk: _Week, g: dict, day: str, extra_after: Optional[str] = None) -> bool:
    others = wk.hard_days(skip=g) + wk.long_days(skip=g)
    if extra_after and (_d(day) - _d(extra_after)).days < SPACING_DAYS:
        return False
    return all(_gap(day, x) >= SPACING_DAYS for x in others)


def _long_ok(wk: _Week, g: dict, day: str) -> bool:
    return all(_gap(day, x) >= SPACING_DAYS for x in wk.hard_days(skip=g))


def _adj(out: list, rule: str, wk: _Week, g: dict, action: str, reason: str, src: str, **kw) -> None:
    out.append({"rule": rule, "week_start": wk.ws, "gen_key": g.get("id"), "kind": g.get("kind"),
                "title": g.get("title"), "day": g.get("day"), "action": action, "reason": reason, "src": src, **kw})


def _rate(g: dict) -> float:
    m = g.get("minutes") or 0
    return float(g.get("tss") or 0.0) / m if m > 0 else 0.0


def _set_minutes(g: dict, minutes: int) -> None:
    r = _rate(g)
    g["minutes"] = int(minutes)
    g["tss"] = round(r * minutes, 1)


def _r5(x: float) -> int:
    return int(round(x / 5.0) * 5)


def _dose_index(title: str) -> Optional[int]:
    from backend.engine import quality_gate as QG
    for i, s in enumerate(QG.DOSE):
        if s[1] == title:
            return i
    return None


def _downgrade(g: dict, th: dict) -> str:
    """One dose step down (quality_gate.DOSE), else an easy run. Returns what it became."""
    from backend.engine import quality_gate as QG
    i = _dose_index(g.get("title") or "") if g.get("kind") == "quality" else None
    if i is not None and i > 0:
        s = QG.session(QG.DOSE[i - 1], th or {})
        g.update({k: s[k] for k in ("title", "minutes", "target", "detail", "tss")})
        return s["title"]
    rate = 50.0 / 60.0
    m = min(int(g.get("minutes") or 45), 45)
    g.update(kind="easy", title="輕鬆跑", minutes=m, target="", detail="心率不超過 AeT（原本的強度課改成輕鬆跑）",
             tss=round(m * rate, 1), protocol=None)
    return "輕鬆跑"


def _to_recovery(g: dict, th: dict) -> str:
    from backend.engine import quality_gate as QG
    s = QG.session(QG.RECOVERY, th or {})
    g.update({k: s[k] for k in ("title", "minutes", "target", "detail", "tss")})
    return s["title"]


def _missed(wk: _Week, stored_missed: list[dict], out: list) -> None:
    by = {g["id"]: g for g in wk.gens()}
    for s in sorted(stored_missed, key=lambda s: s.get("day") or ""):
        g = by.get(s.get("gen_key"))
        if g is None or not wk.free(g) or g not in wk.gens():
            continue
        was = s["day"]
        if s["kind"] == "easy" and g.get("kind") == "easy":
            wk.remove(g)
            _adj(out, "missed_easy", wk, {**g, "day": was}, "removed",
                 f"{wd(was)}輕鬆跑沒跑：不補，之後照原本的課表", SRC_SEILER)
        elif s["kind"] in HARD and g.get("kind") in HARD:
            name = "間歇" if g["kind"] == "quality" else "測試"
            if g.get("day") and g["day"] >= wk.first and _hard_ok(wk, g, g["day"]):
                pick = g["day"]
            else:
                pick = next((d for d in wk.open_days(skip=g) if _hard_ok(wk, g, d)), None)
            if pick is None:
                wk.remove(g)
                _adj(out, "missed_quality", wk, {**g, "day": was}, "removed",
                     f"{wd(was)}{name}沒跑；本週剩下的日子都離長跑或其他強度課不到 48 小時：取消，下週重複同一階、不進階",
                     SRC_SPACING)
                continue
            g["day"] = pick
            lg = sorted(wk.long_days(skip=g))
            tail = f"；離長跑（{wd(lg[0])}）仍有 {_gap(pick, lg[0])} 天" if lg else ""
            _adj(out, "missed_quality", wk, g, "moved", f"{name}延到{wd(pick)}：{wd(was)}沒跑{tail}", SRC_SPACING,
                 before_day=was)
        elif s["kind"] == "long" and g.get("id") == "long":
            if g.get("day") and g["day"] >= wk.first and _long_ok(wk, g, g["day"]):
                pick = g["day"]
            else:
                pick = next((d for d in wk.open_days(skip=g) if _long_ok(wk, g, d)), None)
            if pick is None:
                wk.remove(g)
                _adj(out, "missed_long", wk, {**g, "day": was}, "removed",
                     f"{wd(was)}長跑沒跑；本週沒有不靠著強度課的空日：取消，不移到下週", SRC_SPACING + "；不跨週屬自組")
                continue
            g["day"] = pick
            _adj(out, "missed_long", wk, g, "moved", f"長跑延到{wd(pick)}：{wd(was)}沒跑；不和強度課相鄰",
                 SRC_SPACING, before_day=was)


def _trim_easy(wk: _Week, excess: float, out: list, rule: str, why: str, src: str) -> None:
    """Take `excess` TSS off the remaining easy runs (≥ MIN_EASY_MIN each, else drop the last)."""
    while excess > 0.5:
        easy = sorted([g for g in wk.gens() if g.get("kind") == "easy" and wk.free(g) and g.get("day")
                       and g["day"] >= wk.today and g.get("id") != "long" and not g.get("heat")],
                      key=lambda g: g["day"])
        tot = sum(float(g.get("tss") or 0.0) for g in easy)
        if not easy or tot <= 0:
            return
        f = max(0.0, (tot - excess) / tot)
        new_min = [_r5((g.get("minutes") or 0) * f) for g in easy]
        if all(m >= MIN_EASY_MIN for m in new_min):
            for g, m in zip(easy, new_min):
                if m < (g.get("minutes") or 0):
                    before = g["minutes"]
                    _set_minutes(g, m)
                    _adj(out, rule, wk, g, "trimmed", f"{wd(g['day'])}輕鬆跑 {before}→{m} 分：{why}", src,
                         before_minutes=before)
            return
        last = easy[-1]
        wk.remove(last)
        excess -= float(last.get("tss") or 0.0)
        _adj(out, rule, wk, last, "removed", f"{wd(last['day'])}輕鬆跑取消：{why}（縮短後會少於 {MIN_EASY_MIN} 分）", src)


def _overhard(wk: _Week, reviews: dict, th: dict, out: list, notes: dict) -> None:
    done = [g for g in wk.gens() if g.get("done") and g.get("kind") == "easy" and isinstance(g.get("done_by"), dict)]
    seen = {(g.get("done_by") or {}).get("index") for g in done}
    for s in wk.st:      # the user's own easy sessions that are done
        if s["state"] == "done" and s["kind"] == "easy" and isinstance(s.get("done_by"), dict) \
                and s["done_by"].get("index") not in seen:
            done.append({"id": s.get("gen_key") or s["uid"], "kind": "easy", "day": s["day"], "tss": s.get("tss"),
                         "done": True, "done_by": s["done_by"], "title": s.get("title")})
    for g in sorted(done, key=lambda g: g.get("day") or ""):
        a = g["done_by"]
        r = reviews.get(a.get("index")) or reviews.get(str(a.get("index")))
        rr = dict(r or {})
        if rr.get("tss") is None:
            rr["tss"] = a.get("tss")              # the activity row's real TSS
        why = overhard(g.get("tss"), rr)
        if not why:
            continue
        day = a.get("date") or g["day"]
        notes[a.get("index")] = f"輕鬆跑偏強（{why}）：已調整之後的課表"
        _adj(out, "overhard", wk, {**g, "day": day}, "note", f"{wd(day)}輕鬆跑偏強（{why}）：課表不作廢、算完成", SRC_OVER)
        # 2. the next hard session within 48 h
        nxt = sorted([h for h in wk.gens() if h.get("kind") in HARD and wk.free(h) and h.get("day")
                      and 0 <= (_d(h["day"]) - _d(day)).days < SPACING_DAYS], key=lambda h: h["day"])
        for h in nxt[:1]:
            pick = next((d for d in wk.open_days(skip=h) if _hard_ok(wk, h, d, extra_after=day)), None)
            was = h["day"]
            if pick:
                h["day"] = pick
                _adj(out, "overhard", wk, h, "moved",
                     f"{h['title']}從{wd(was)}延到{wd(pick)}：{wd(day)}輕鬆跑偏強，間隔不到 48 小時", SRC_OVER + "；" + SRC_SPACING,
                     before_day=was)
            else:
                old = h.get("title")
                became = _downgrade(h, th)
                _adj(out, "overhard", wk, h, "downgraded",
                     f"{wd(was)}{old}改成{became}：{wd(day)}輕鬆跑偏強，本週沒有隔 48 小時的空日", SRC_OVER, before_title=old)
        # 3. keep the week's TSS: the excess comes off the remaining easy runs
        actual = rr.get("tss")
        excess = (float(actual) - float(g.get("tss") or 0.0)) if actual is not None else 0.0
        if excess > 0:
            _trim_easy(wk, excess, out, "overhard", f"{wd(day)}輕鬆跑多了 {excess:.0f} TSS，維持本週的 TSS 目標", SRC_OVER)


def _red_streak(stored: list[dict], today: str) -> bool:
    from backend.engine import compliance as C
    past = sorted([s for s in stored if s["state"] in ("done", "missed") and s.get("day") and s["day"] <= today
                   and s["kind"] not in SIDE], key=lambda s: s["day"])
    lv = []
    for s in past[-RED_STREAK:]:
        c = C.session_compliance(s)
        lv.append(c and c["level"])
    return len(lv) == RED_STREAK and all(x == "red" for x in lv)


def _fatigue(wk: _Week, stored: list[dict], ctx: dict, th: dict, out: list) -> None:
    load = ctx.get("load") or {}
    tsb, ramp = load.get("tsb"), load.get("ramp")
    rest_week = ctx.get("mode") in ("recovery_week", "recovery", "taper", "event", "transition")
    why, remove = None, False
    if tsb is not None and tsb < TSB_FLOOR and not rest_week:
        why, remove = f"TSB {tsb:+.0f} < {TSB_FLOOR:.0f}", True
    elif ramp is not None and ramp >= RAMP_SHORT:
        why, remove = f"CTL 每週 +{ramp:.1f}（≥ {RAMP_SHORT:.0f}）", True
    elif _red_streak(stored, wk.today):
        why = f"連續 {RED_STREAK} 堂偏離計畫（紅色）"
    if not why:
        return
    for q in [g for g in wk.gens() if g.get("kind") == "quality" and wk.free(g) and g.get("day") and g["day"] >= wk.today]:
        if remove:
            wk.remove(q)
            _adj(out, "fatigue", wk, q, "removed", f"{wd(q['day'])}{q['title']}取消：{why}，本週減量", SRC_FATIGUE)
        else:
            old = q["title"]
            became = _to_recovery(q, th)
            _adj(out, "fatigue", wk, q, "downgraded", f"{wd(q['day'])}{old}改成{became}：{why}，本週減量", SRC_FATIGUE,
                 before_title=old)
    for g in [g for g in wk.gens() if g.get("kind") == "easy" and wk.free(g) and g.get("day") and g["day"] >= wk.today]:
        before = g.get("minutes") or 0
        m = max(MIN_EASY_MIN, _r5(before * FATIGUE_CUT))
        if m < before:
            _set_minutes(g, m)
            _adj(out, "fatigue", wk, g, "trimmed",
                 f"{wd(g['day'])}輕鬆跑 {before}→{m} 分：{why}，輕鬆量減 {round((1 - FATIGUE_CUT) * 100)}%", SRC_FATIGUE,
                 before_minutes=before)


def adapt(gen_weeks: list[dict], stored: list[dict], ctx: dict) -> tuple[list[dict], list[dict], dict]:
    """(adjusted gen_weeks, adjustments, notes). `ctx`: today, first_free (first
    day sessions can go on), blocked, allowed_days, thresholds, mode (this
    week), load {tsb, ramp}, reviews {activity index: review metrics}.
    `notes`: activity index -> the note for the done session it matched."""
    weeks = copy.deepcopy(gen_weeks)
    out: list[dict] = []
    notes: dict = {}
    if not weeks:
        return weeks, out, notes
    wk = _Week(weeks[0], stored, ctx)
    th = ctx.get("thresholds") or {}
    missed = [s for s in wk.st if s["state"] == "missed" and s.get("origin") == "auto" and s.get("gen_key")]
    _missed(wk, missed, out)
    _overhard(wk, ctx.get("reviews") or {}, th, out, notes)
    _fatigue(wk, stored, ctx, th, out)
    return weeks, out, notes


NOTE_PREFIX = "輕鬆跑偏強"


def apply_notes(sessions: list[dict], notes: dict) -> None:
    """Write the over-hard notes on the done sessions (by their activity index);
    clear a stale one. Done rows are never rewritten by reconcile()."""
    keyed = {str(k): v for k, v in notes.items()}
    for s in sessions:
        if s.get("state") != "done":
            continue
        idx = (s.get("done_by") or {}).get("index") if isinstance(s.get("done_by"), dict) else None
        n = keyed.get(str(idx)) if idx is not None else None
        if n:
            s["note"] = n
        elif (s.get("note") or "").startswith(NOTE_PREFIX):
            s["note"] = None


def annotate(changes: list[dict], adjustments: list[dict], sessions: list[dict], stored: list[dict]) -> None:
    """Give reconcile's changes the adjustment's reason (by week + gen_key)."""
    key = {}
    for s in list(stored) + list(sessions):
        if s.get("gen_key"):
            key[s["uid"]] = (s.get("week_start"), s["gen_key"])
    by = {}
    for a in adjustments:
        if a["action"] != "note":
            by.setdefault((a["week_start"], a["gen_key"]), a)
    for c in changes:
        a = by.get(key.get(c["uid"]))
        if a is not None and c["action"] in ("changed", "removed", "added"):
            c["reason"] = a["reason"]
            c["adapt"] = a["rule"]
