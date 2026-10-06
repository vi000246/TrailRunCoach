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

Rules (thresholds: source or 推估):
  A. missed easy run -> its make-up is dropped (the generator would re-place it
     on a later day). Seiler「easy days easy」; not making it up is 推估.
  B. missed quality / test -> kept on the generator's new day only when it is
     ≥ 2 days from the long run and every other hard day (plan_prefs.place()'s
     48-h rule); else moved to a free day that keeps the spacing; else
     cancelled. The next week repeats the same dose step: quality_gate's dose
     step only counts sessions actually done (no code needed here).
  C. missed long run -> kept / moved to a free day of the same week that is not
     next to a quality / test day; else cancelled. Never carried into next week
     (reconcile never moves a session across weeks).
  D. easy run done too hard: the session stays done. Detected by
     overhard() (unsourced-rules.md §B5): the heart-rate condition needs BOTH
     avg HR > AeT + 3 bpm (workout_review.AET_MARGIN) AND time above AeT+3
     > 10 % (workout_review.OVER_AET_SHARE) — summer easy runs
     sit high on HR alone (heat), so one HR rule fired too often; or avg power
     > 80 % CP (zones z2 upper bound, Palladino 1C); or TSS > planned + 20 %
     (TrainingPeaks compliance green band, engine/compliance.py). Power / TSS
     either one alone; the combination is 推估. Then:
       1. the actual TSS counts (plan_store.plan_summary uses done_by.tss;
          CTL/ATL already come from the real data);
       2. a hard session < 2 days after it moves later in the week if the 48-h
          spacing allows, else steps down one dose step, else becomes an easy
          run (推估);
       3. the remaining easy runs this week lose the excess TSS (actual −
          planned), each ≥ 20 min, else the last easy is dropped (推估); the
          long run and the quality session are never trimmed for this;
       4. a note on that day:「輕鬆跑偏強（…）：已調整之後的課表」.
     Self-rating trigger (SP-231, rule "rpe_hard"; docs/research/readiness-signals.md §4.1):
     an easy run or long run the athlete rated Hard or more after the run (COROS's
     post-run rating ≥ 4, engine/coros_rpe.py; a FIT's own RPE ≥ 7 the same) -> step 2 for
     the next hard session (quality / test) < 48 h after it: moved to a free day ≥ 48 h
     after the run, else one step down. Nothing else (no trim, the long run stays, the hard
     session's done-check unchanged). 推估: no controlled trial (research §2.4). Runs after E
     and leaves alone any session another rule already changed (E's removal / step-down
     wins); ctx `rpe_rule` False = off (課表偏好 › 自動調整).
  E. fatigue guard: two red-compliance sessions in a row (engine/compliance.py)
     -> the quality steps down to the recovery fartlek and easy minutes × 0.8;
     TSB < −30 (Friel / TrainingPeaks, coach; when week_plan has not already
     made it a recovery week) or a CTL ramp at load_guard's BLOCK line
     (min(10, max(5, 15 % of CTL 7 days ago)); SP-63) -> the quality is removed
     and easy minutes × 0.8. The 20 % cut is 推估. Mapping (SP-63): this rule
     used 8/week = the old block line (since 2026-10-01; 7 before that), so it
     takes the new block line, not the watch line. TSB < −30 itself is
     week_plan's existing recovery-week rule and the ramp block is quality_gate's
     existing block: those are not repeated when they already acted.
     Exception (engine/b2b.py, user-approved): during a planned B2B week and
     the easy days after one, TSB < −30 alone is the expected drop and does
     nothing (an adjustment "note" logs why); the red streak and the ramp
     still act — they are signs beyond the expected drop.
"""
from __future__ import annotations

import copy
import datetime as dt
from typing import Optional

from backend.engine import load_guard as LG
from backend.engine.hr_profile import easy_cap_label
from backend.i18n import _, fmt

HARD = ("quality", "test")
SIDE = ("strength", "heat_passive", "notice")

# D. easy run done too hard (unsourced-rules.md §B5): HR = both conditions together; power / TSS either alone (推估)
OVER_HR_BPM = 3.0          # workout_review.AET_MARGIN: "easy" = avg HR ≤ AeT + 3 (the default; per athlete: SP-69)
OVER_SHARE = 0.10          # workout_review.OVER_AET_SHARE: > 10 % of the time above AeT + 3
EASY_POWER_CAP = 0.80      # zones.py z2 upper bound (Palladino 1C: 75–80 % CP)
OVER_TSS = 0.20            # compliance.COMPLIANCE["green"] (TrainingPeaks ±20 %)
MIN_EASY_MIN = 20          # 推估: a trimmed easy run is never shorter than this
SPACING_DAYS = 2           # plan_prefs.place(): 48 h between hard days / the long run
# E. fatigue guard
TSB_FLOOR = -30.0          # week_plan(): TSB < −30 -> recovery week
FATIGUE_CUT = 0.80         # 推估: easy minutes × 0.8
RED_STREAK = 2             # 推估: two red sessions in a row

SRC_SEILER = "Seiler：easy days easy；不補課屬推估"
SRC_SPACING = "硬課之間隔 ≥ 2 天：台灣教練（5 區一週最多 2 次、間隔至少 2 天）"
SRC_OVER = ("平均心率 > AeT+3 且 > 10% 時間超過（兩條都要）、z2 上限 80% CP（Palladino）、"
            "TrainingPeaks ±20%；組合方式推估")
SRC_FATIGUE = ("CTL ramp 到擋線（min(10, max(5, CTL 的 15%))，Friel 5–8／10 換算，推估）；"
               "TSB < −30（Friel／TrainingPeaks）；連兩堂紅色、減 20% 推估")
SRC_B2B = "Johnston（UA）B2B 後「three or four light days」；B2B 造成的 TSB 下降不觸發減量為推估"
SRC_RPE = "跑後自評：Nuuttila 2021（心率恢復了、自覺費力仍偏高）；門檻 Hard 是使用者決定；規則與 1–5 級換算推估"
RPE_RULE = "rpe_hard"


def _b2b_exempt(info: Optional[dict], today: str) -> Optional[str]:
    from backend.engine import b2b as B2B
    return B2B.fatigue_exempt(info, today)


def wd(day: str) -> str:
    return fmt.weekday(day)                     # 週一 / Mon (backend/i18n/fmt.py)


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
    over, tot = r.get("over_aet_s"), r.get("hr_s") or 0
    # the average-HR margin: OVER_HR_BPM or the athlete's own (threshold_calib.easy_margin, SP-69 —
    # api/plan_sessions puts it in the review row); the time share stays the activity's AeT+3 seconds
    margin = float(r.get("aet_margin") or OVER_HR_BPM)
    hi_avg = bool(aet and hr and hr > aet + margin)
    hi_share = over is not None and tot > 0 and over / tot > OVER_SHARE
    if hi_avg and hi_share:                     # B5: both, not either
        m = f"{margin:.0f}" if abs(margin - round(margin)) < 0.05 else f"{margin:.1f}"
        basis = r.get("aet_margin_basis")
        return (f"平均心率 {hr:.0f} > AeT+{m}（{aet + margin:.0f}"
                + (_("；餘裕 {m} bpm，{basis}", m=m, basis=basis) if basis else "") + "），"
                f"且超過的時間 {over / tot * 100:.0f}% > {OVER_SHARE * 100:.0f}%")
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
        # done hard days of this week from the activities (workout_review.HARD_TYPES: Z5 / Z3 /
        # 高強度長跑 / CP test), planned or not — an unplanned hard run spaces the rest too
        self.hard_done = [d for d in (ctx.get("hard_days") or []) if str(d)[:10] >= self.ws]
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
        out += [d for d in self.hard_done if d not in out]
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


def _prev_row(title: str) -> Optional[tuple]:
    """The ladder row one step below the rung titled `title` (interval_library.PREV_RUNG: within
    its track; A1 / V1 → T3), None when there is none."""
    from backend.engine import interval_library as IL
    from backend.engine import quality_gate as QG
    title = IL.renamed(title)                  # a stored pre-SP-79 title (「閾值 3×8 分」)
    s = next((s for s in QG.LADDER if s[1] == title), None)
    prev = IL.PREV_RUNG.get(s[0]) if s is not None else None
    return next((r for r in QG.LADDER if r[0] == prev), None) if prev else None


def _downgrade(g: dict, th: dict) -> str:
    """One step down its track (interval_library.PREV_RUNG), else an easy run. Returns what it became."""
    from backend.engine import interval_library as IL
    from backend.engine import quality_gate as QG
    rung = g.get("rung_key") if g.get("kind") == "quality" else None
    if rung in IL.PREV_RUNG:
        # a library variant: the rung before's standard session, as maintenance (not progress)
        prev = IL.PREV_RUNG[rung]
        f = IL.fit(prev, g.get("minutes") or None)
        s = IL.session_for({**f, "equiv": False, "progress": False,
                            "reason": f"自動調整：降一階到 {IL.RUNG_NAME[prev]}（不算進階）"}, th or {}, swap="auto")
        g.update({k: s.get(k) for k in ("title", "minutes", "target", "detail", "tss", "variant_key", "rung_key",
                                        "equiv", "swap", "swap_reason", "variant_reps", "variant_blocks", "variant_adj")})
        return s["title"]
    p = _prev_row(g.get("title") or "") if g.get("kind") == "quality" and not g.get("variant_key") else None
    if p is not None:
        s = QG.session(p, th or {})
        g.update({k: s[k] for k in ("title", "minutes", "target", "detail", "tss")})
        return s["title"]
    rate = 50.0 / 60.0
    m = min(int(g.get("minutes") or 45), 45)
    g.update(kind="easy", title="輕鬆跑", minutes=m, target="", detail=f"心率不超過{easy_cap_label(th or {})}（原本的強度課改成輕鬆跑）",
             tss=round(m * rate, 1), protocol=None, **NO_VARIANT)
    return "輕鬆跑"


NO_VARIANT = {k: None for k in ("variant_key", "rung_key", "equiv", "swap", "swap_reason", "variant_reps",
                                "variant_blocks", "variant_adj")}


def _to_recovery(g: dict, th: dict) -> str:
    from backend.engine import quality_gate as QG
    s = QG.session(QG.RECOVERY, th or {})
    g.update({k: s[k] for k in ("title", "minutes", "target", "detail", "tss")}, **NO_VARIANT)
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
                     f"{wd(was)}長跑沒跑；本週沒有不靠著強度課的空日：取消，不移到下週", SRC_SPACING + "；不跨週屬推估")
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


def _rated_label(r: dict) -> str:
    """「Hard」 (COROS's word) or 「RPE 8」 (a FIT's own RPE)."""
    from backend.engine import coros_rpe as CR
    sr = CR.self_rating(r) or {}
    return sr.get("label") or f"RPE {float(r.get('rpe') or 0):g}"


def _rpe_hard(wk: _Week, rated: dict, th: dict, out: list) -> None:
    """Rule D's self-rating trigger (module doc). `rated`: activity index -> {rpe, source,
    coros_feel} of this week's runs (api/plan_sessions._adapt_ctx)."""
    from backend.engine import coros_rpe as CR
    if not rated:
        return
    touched = {a.get("gen_key") for a in out if a.get("action") != "note"}
    done = [g for g in wk.gens() if g.get("done") and (g.get("kind") in ("easy", "long") or g.get("id") == "long")
            and isinstance(g.get("done_by"), dict)]
    seen = {(g.get("done_by") or {}).get("index") for g in done}
    for s in wk.st:      # the user's own easy / long sessions that are done
        if s["state"] == "done" and s["kind"] in ("easy", "long") and isinstance(s.get("done_by"), dict) \
                and s["done_by"].get("index") not in seen:
            done.append({"id": s.get("gen_key") or s["uid"], "kind": s["kind"], "day": s["day"], "done": True,
                         "done_by": s["done_by"], "title": s.get("title")})
    for g in sorted(done, key=lambda g: g.get("day") or ""):
        a = g["done_by"]
        r = rated.get(a.get("index")) or rated.get(str(a.get("index")))
        if not r or r.get("rpe") is None or float(r["rpe"]) < CR.HARD_RPE:
            continue
        day = a.get("date") or g["day"]
        what = _("長跑") if (g.get("kind") == "long" or g.get("id") == "long") else _("輕鬆跑")
        label = _rated_label(r)
        nxt = sorted([h for h in wk.gens() if h.get("kind") in HARD and wk.free(h) and h.get("day")
                      and h.get("id") not in touched and 0 <= (_d(h["day"]) - _d(day)).days < SPACING_DAYS],
                     key=lambda h: h["day"])
        for h in nxt[:1]:
            pick = next((d for d in wk.open_days(skip=h) if _hard_ok(wk, h, d, extra_after=day)), None)
            was = h["day"]
            touched.add(h.get("id"))
            if pick:
                h["day"] = pick
                _adj(out, RPE_RULE, wk, h, "moved",
                     _("{was} {title}延到{to}：{day}{what}自評 {label}，間隔不到 48 小時",
                       was=wd(was), title=h.get("title") or "", to=wd(pick), day=wd(day), what=what, label=label),
                     SRC_RPE + "；" + SRC_SPACING, before_day=was, activity=a.get("index"))
            else:
                old = h.get("title")
                became = _downgrade(h, th)
                _adj(out, RPE_RULE, wk, h, "downgraded",
                     _("{was} {old}改成{became}：{day}{what}自評 {label}，本週沒有隔 48 小時的空日",
                       was=wd(was), old=old or "", became=became, day=wd(day), what=what, label=label),
                     SRC_RPE, before_title=old, activity=a.get("index"))


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
    tsb, ramp, base = load.get("tsb"), load.get("ramp"), load.get("ramp_base")
    rest_week = ctx.get("mode") in ("recovery_week", "recovery", "taper", "event", "transition", "rebuild", "reentry")
    why, remove = None, False
    tsb_hit = tsb is not None and tsb < TSB_FLOOR and not rest_week
    # a planned B2B (engine/b2b.py): its TSB drop is expected — only the ramp / red streak still act
    b2b_why = _b2b_exempt(ctx.get("b2b"), wk.today) if tsb_hit else None
    if b2b_why:
        _adj(out, "b2b", wk, {"day": wk.today}, "note",
             f"TSB {tsb:+.0f} < {TSB_FLOOR:.0f}，但{b2b_why}：這是預期中的下降，不減量（推估）", SRC_B2B)
    if tsb_hit and not b2b_why:
        why, remove = f"TSB {tsb:+.0f} < {TSB_FLOOR:.0f}", True
    elif LG.ramp_level(ramp, base) == LG.BLOCK and ctx.get("mode") != "reentry":
        # the re-entry block's 50 → 75 → 100 % steps are planned, not overload (detraining.md §6.5, 推估)
        why, remove = LG.ramp_text(ramp, base, LG.BLOCK), True
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
    week), load {tsb, ramp, ramp_base (CTL 7 days ago)}, reviews {activity index: review metrics},
    rpe {activity index: {rpe, source, coros_feel}} and rpe_rule (bool, default on): SP-231.
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
    if ctx.get("rpe_rule", True):
        _rpe_hard(wk, ctx.get("rpe") or {}, th, out)
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
