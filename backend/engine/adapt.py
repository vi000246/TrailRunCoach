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
  D. easy run done too hard (SP-301, the user 2026-10-06): two tiers; the session stays done
     and its actual TSS counts (plan_store.plan_summary uses done_by.tss; CTL/ATL already
     come from the real data).
       偏強 — label only, overhard(): avg power > 80 % CP (zones z2 upper bound, Palladino 1C)
         or TSS > planned + 20 % (TrainingPeaks compliance green band, engine/compliance.py;
         the plan's easy rate is the easy-only one since SP-302). A run without power reads its
         average HR instead of the power line: above the easy-run cap + 3 bpm (SP-565, owner
         2026-10-14: one 「輕鬆」 — hr_profile.easy_cap_for / over_easy, the 課表's cap = the Z2
         top of the zones in effect, a measured AeT when there is one, + HR's day-to-day spread;
         the activity review's 「輕鬆」 is the same line). It was avg HR > 94 % LTHR from
         2026-10-06 (SP-301) and AeT + 3 before. With power, HR is not used at all. The done session gets
         「輕鬆跑偏強（…）：只標示，課表不變」; no session changes (no move, no step-down, no
         trimmed easy runs).
       太強 — schedule change, too_hard(): the session classifier (workout_review.classify,
         session_stimulus.verdict) puts the run in a hard class — Zone 3 (threshold) or Zone 5,
         高強度長跑, CP test (= workout_review.HARD_TYPES). It counts as a hard session: the
         next free hard session < 2 days later moves later in the week if the 48-h spacing
         allows, else steps down one dose step, else becomes an easy run (rule B's spacing,
         SRC_SPACING); the reason names the run (「週四 有氧間歇…延到週六：週三輕鬆跑跑成閾值課，
         間隔不到 48 小時」). week_plan already keeps the remaining hard sessions 48 h from a done
         hard run (hard_done): when the generator moved one off its stored day for this run,
         that move gets the same reason. A session another rule changed, a locked (edited /
         custom / done) one, is never touched; within 14 days of an A race the change waits
         for approval (plan_auto.classify, rule "too_hard"); 復原 as every run.
       The two tiers and 「太強 = the classifier's hard class」 are 推估 (Seiler「easy days
       easy」is the principle). The old step 3 (the excess TSS off the remaining easy runs) is
       gone: 偏強 changes nothing, 太強 is handled as a hard session.
     Self-rating trigger (SP-231, rule "rpe_hard"; docs/research/readiness-signals.md §4.1):
     an easy run or long run the athlete rated Hard or more after the run (COROS's
     post-run rating ≥ 4, engine/coros_rpe.py; a FIT's own RPE ≥ 7 the same) -> the 太強
     step for the next hard session (quality / test) < 48 h after it: moved to a free day ≥ 48 h
     after the run, else one step down. Nothing else (no trim, the long run stays, the hard
     session's done-check unchanged). 推估: no controlled trial (research §2.4). Runs after E
     and D and leaves alone any session another rule already changed (E's removal / step-down
     wins) and a run 太強 already acted on (one adjustment per session when both fire); ctx
     `rpe_rule` False = off (課表偏好 › 自動調整).
  E. fatigue guard, on load and intensity, not the completion colour (SP-398, the user
     2026-10-12: 「疲勞保護要看是不是有高強度或 TSS 過高，跟完成度沒關係」 — the old trigger
     two red-compliance sessions in a row is gone: it read too little, a bike ride for a run …
     as fatigue):
     TSB < −30 % of CTL (Friel / TrainingPeaks, coach, as a share since SP-670; when week_plan has not already
     made it a recovery week) or a CTL ramp at load_guard's BLOCK line
     (min(10, max(5, 15 % of CTL 7 days ago)); SP-63) -> the quality is removed
     and easy minutes × 0.8.
     「連續 2 堂輕鬆課跑太強」 (SP-404, the user 2026-10-12; rule D and rpe_hard act on
     one run, nothing read the accumulation): this week's done easy runs and long runs — the
     ones rule D / rpe_hard judge, the generator's and the user's own — by date; the last
     OVERHARD_STREAK (2) both too hard -> the quality steps down to the recovery fartlek and easy
     minutes × 0.8 (no session is cancelled: that stays for TSB / ramp, which win when they
     fire too — one change per session). Too hard = rule D's 太強 (the classifier's hard class)
     or 偏強 (power > 80 % CP, TSS > planned + 20 %, HR > the easy cap + 3 without power) on an easy
     run, or a self-rating ≥ Hard (rpe_hard, when `rpe_rule` is on) on an easy or long run.
     With low optical-HR quality (done_by.hr_quality "low", SP-402's — as compliance's `uncertain`) a 偏強
     read from HR alone does not count. Hard sessions, strength and skipped sessions in
     between neither count nor reset it; a normal easy / long run in between resets it.
     The 2 runs and the trigger itself are 推估 (no controlled trial).
     The 20 % cut is 推估. Mapping (SP-63): this rule
     used 8/week = the old block line (since 2026-10-01; 7 before that), so it
     takes the new block line, not the watch line. TSB < −30 % itself is
     week_plan's existing recovery-week rule and the ramp block is quality_gate's
     existing block: those are not repeated when they already acted.
     Exception (engine/b2b.py, user-approved): during a planned B2B week and
     the easy days after one, TSB < −30 % alone is the expected drop and does
     nothing (an adjustment "note" logs why); the ramp and the too-hard
     streak still act — signs beyond the expected drop.
"""
from __future__ import annotations

import copy
import datetime as dt
from typing import Optional

from backend.engine import load_guard as LG
from backend.engine import hr_profile as HP
from backend.engine.hr_profile import easy_cap_label
from backend.i18n import N_, _, fmt

HARD = ("quality", "test")
SIDE = ("strength", "heat_passive", "notice")

# D. easy run done too hard (SP-301): 偏強 = power or TSS, either alone (label only); 太強 = the
# session classifier's hard class (moves / steps down the next hard session). Two tiers: 推估
EASY_POWER_CAP = 0.80      # zones.py z2 upper bound (Palladino 1C: 75–80 % CP)
OVER_TSS = 0.20            # compliance.COMPLIANCE["green"] (TrainingPeaks ±20 %)
TOO_HARD_TYPES = ("quality", "hard_long", "test_cp")    # = workout_review.HARD_TYPES (Zone 3 / Zone 5 …)
TOO_HARD_LABEL = {"z3": N_("閾值課"), "z5": N_("VO2max 課"), "hard_long": N_("高強度長跑"), "test_cp": N_("CP 測試")}
MIN_EASY_MIN = 20          # 推估: a trimmed easy run is never shorter than this (rule E)
SPACING_DAYS = 2           # plan_prefs.place(): 48 h between hard days / the long run
# E. fatigue guard
TSB_FLOOR = LG.TSB_DEEP    # week_plan(): TSB < −30 % of CTL -> recovery week (SP-670; was −30 points)
FATIGUE_CUT = 0.80         # 推估: easy minutes × 0.8
OVERHARD_STREAK = 2        # 推估 (SP-404): the last two done easy / long runs both too hard

# SP-905: an adjustment's 出處 is shown in the page's language (engine/stored_text): msgids, or parts
# joined by 「；」 that are msgids
SRC_SEILER_PARTS = (N_("Seiler：easy days easy"), N_("不補課屬推估"))
SRC_SEILER = "；".join(SRC_SEILER_PARTS)
SRC_SPACING = N_("硬課之間隔 ≥ 2 天：台灣教練（5 區一週最多 2 次、間隔至少 2 天）")
SRC_OVER = N_("平均功率 > 80% CP（z2 上限，Palladino）或訓練負荷（Training load）> 計畫 +20%（TrainingPeaks 達成度綠燈帶），"
              "任一項；沒有功率時改看平均心率 > 輕鬆跑上限 +3（跟課表、活動判讀同一個上限：課表心率區間的 Z2 上緣，"
              "有實測 AeT 時用實測值；+3 是心率每天自然的落差）；"
              "只標示、不動課表屬推估（使用者決定 2026-10-06）")
SRC_TOO_HARD = N_("課別分類（Zone 3 閾值或更強）跑成強度課，就當成一堂強度課：Seiler「easy days easy」；"
                  "用課別分類當「太強」的界線屬推估")
# (SP-905: SRC_TOO_HARD's parts, for the text it starts: each part is read on its own)
SRC_TOO_HARD_PARTS = (N_("課別分類（Zone 3 閾值或更強）跑成強度課，就當成一堂強度課：Seiler「easy days easy」"),
                      N_("用課別分類當「太強」的界線屬推估"))
OVERHARD_RULE = "overhard"
SRC_FATIGUE_PARTS = (N_("長期負荷週增量（Chronic load ramp）到擋線（min(10, max(5, 長期負荷的 15%))，Friel 5–8／10 換算，推估）"),
                     N_("負荷平衡（Load balance）< −30（Friel／TrainingPeaks）"), N_("連續 2 堂輕鬆課跑太強、減 20% 推估"))
SRC_FATIGUE = "；".join(SRC_FATIGUE_PARTS)
SRC_B2B_PARTS = (N_("Johnston（UA）B2B 後「three or four light days」"),
                 N_("B2B 造成的負荷平衡（Load balance）下降不觸發減量為推估"))
SRC_B2B = "；".join(SRC_B2B_PARTS)
SRC_RPE_PARTS = (N_("跑後自評：Nuuttila 2021（心率恢復了、自覺費力仍偏高）"), N_("門檻 Hard 是使用者決定"),
                 N_("規則與 1–5 級換算推估"))
SRC_RPE = "；".join(SRC_RPE_PARTS)


def _src(*parts) -> str:
    """Sources joined by 「；」, each part in the request's language (the generator: zh-TW, as stored)."""
    return "；".join(_(p) for p in parts)
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


def overhard(planned_tss: Optional[float], r: Optional[dict], hr: bool = True) -> Optional[str]:
    """Why a done easy run was 偏強 (rule D's label-only tier), or None. `r`: {avg_power, cp,
    avg_hr, easy_cap, tss} (api/plan_sessions._adapt_ctx: workout_review.measure's row). SP-301: avg
    power > 80 % CP or TSS > planned + 20 %, either one; a run without power (no avg power or no CP)
    reads avg HR > the easy-run cap + 3 instead of the power line (SP-565: hr_profile.over_easy, the
    課表's and the review's 「輕鬆」; no time-share condition). With power, HR is not used at all.
    `hr` False: the HR line is not read (rule E's streak on a run with low HR quality, SP-404)."""
    if not r:
        return None
    p, cp = r.get("avg_power"), r.get("cp")
    if p and cp:
        if p > EASY_POWER_CAP * cp:
            return _("平均功率 {p:.0f} W > {pct:.0f}% CP（{w:.0f} W）", p=p, pct=EASY_POWER_CAP * 100,
                     w=EASY_POWER_CAP * cp)
    elif hr:
        ahr, cap = r.get("avg_hr"), _cap_bpm(r.get("easy_cap"))
        if HP.over_easy(ahr, cap):
            return _("平均心率 {hr:.0f} > 輕鬆跑上限 {cap:.0f}+3（沒有功率才看心率）", hr=ahr, cap=cap)
    t = r.get("tss")
    if t and planned_tss and t > planned_tss * (1 + OVER_TSS):
        return _("負荷 {t:.0f} > 計畫 {plan:.0f} 的 +{pct:.0f}%", t=t, plan=planned_tss, pct=OVER_TSS * 100)
    return None


def _cap_bpm(cap) -> Optional[float]:
    """The easy cap in bpm: workout_review's easy_cap dict (hr_profile.easy_cap) or a number."""
    v = cap.get("value") if isinstance(cap, dict) else cap
    return float(v) if v else None


def too_hard(r: Optional[dict]) -> Optional[str]:
    """What a done easy run turned into when the session classifier calls it a hard session
    (Zone 3 threshold or harder: rule D's 太強 tier), else None. `r`: the review row's
    session_type / stimulus (api/plan_sessions._adapt_ctx, workout_review.classify)."""
    typ = (r or {}).get("session_type")
    if typ not in TOO_HARD_TYPES:
        return None
    key = typ if typ in ("hard_long", "test_cp") else ("z5" if r.get("stimulus") == "z5" else "z3")
    return _(TOO_HARD_LABEL[key])


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
                            "reason": _("自動調整：降一階到 {rung}（不算進階）", rung=IL.RUNG_NAME[prev])},
                           th or {}, swap="auto")
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
    g.update(kind="easy", title="輕鬆跑", minutes=m, target="",
             detail=_("心率不超過{cap}（原本的強度課改成輕鬆跑）", cap=easy_cap_label(th or {})),
             tss=round(m * rate, 1), protocol=None, **NO_VARIANT)
    _drop_steps(g)
    return "輕鬆跑"


def _drop_steps(g: dict) -> None:
    """A session that became another one keeps no structure of the old (SP-87: the 專項期 技術地形 RPE
    6–7 session is kind quality, so these downgrades reach it — its RPE structure would be pushed)."""
    if g.get("steps"):
        g["steps"] = None


NO_VARIANT = {k: None for k in ("variant_key", "rung_key", "equiv", "swap", "swap_reason", "variant_reps",
                                "variant_blocks", "variant_adj")}


def _to_recovery(g: dict, th: dict) -> str:
    from backend.engine import quality_gate as QG
    s = QG.session(QG.RECOVERY, th or {})
    g.update({k: s[k] for k in ("title", "minutes", "target", "detail", "tss")}, **NO_VARIANT)
    _drop_steps(g)
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
                 _("{day}輕鬆跑沒跑：不補，之後照原本的課表", day=wd(was)), _src(*SRC_SEILER_PARTS))
        elif s["kind"] in HARD and g.get("kind") in HARD:
            q = g["kind"] == "quality"
            name = _("間歇") if q else _("測試")
            if g.get("day") and g["day"] >= wk.first and _hard_ok(wk, g, g["day"]):
                pick = g["day"]
            else:
                pick = next((d for d in wk.open_days(skip=g) if _hard_ok(wk, g, d)), None)
            if pick is None:
                wk.remove(g)
                _adj(out, "missed_quality", wk, {**g, "day": was}, "removed",
                     (_("{day}間歇沒跑；本週剩下的日子都離長跑或其他強度課不到 48 小時：取消，下週重複同一階、不進階",
                        day=wd(was)) if q else
                      _("{day}測試沒跑；本週剩下的日子都離長跑或其他強度課不到 48 小時：取消，下週重複同一階、不進階",
                        day=wd(was))),
                     _(SRC_SPACING))
                continue
            g["day"] = pick
            lg = sorted(wk.long_days(skip=g))
            tail = ("；" + _("離長跑（{day}）仍有 {n} 天", day=wd(lg[0]), n=_gap(pick, lg[0]))) if lg else ""
            _adj(out, "missed_quality", wk, g, "moved", _("{name}延到{to}：{day}沒跑", name=name, to=wd(pick), day=wd(was))
                 + tail, _(SRC_SPACING), before_day=was)
        elif s["kind"] == "long" and g.get("id") == "long":
            if g.get("day") and g["day"] >= wk.first and _long_ok(wk, g, g["day"]):
                pick = g["day"]
            else:
                pick = next((d for d in wk.open_days(skip=g) if _long_ok(wk, g, d)), None)
            if pick is None:
                wk.remove(g)
                _adj(out, "missed_long", wk, {**g, "day": was}, "removed",
                     _("{day}長跑沒跑；本週沒有不靠著強度課的空日：取消，不移到下週", day=wd(was)),
                     _src(SRC_SPACING, N_("不跨週屬推估")))
                continue
            g["day"] = pick
            _adj(out, "missed_long", wk, g, "moved", _("長跑延到{to}：{day}沒跑；不和強度課相鄰", to=wd(pick), day=wd(was)),
                 _(SRC_SPACING), before_day=was)


def _done_easy(wk: _Week) -> list[dict]:
    """This week's done easy sessions with their activity (generated and the user's own)."""
    done = [g for g in wk.gens() if g.get("done") and g.get("kind") == "easy" and isinstance(g.get("done_by"), dict)]
    seen = {(g.get("done_by") or {}).get("index") for g in done}
    for s in wk.st:      # the user's own easy sessions that are done
        if s["state"] == "done" and s["kind"] == "easy" and isinstance(s.get("done_by"), dict) \
                and s["done_by"].get("index") not in seen:
            done.append({"id": s.get("gen_key") or s["uid"], "kind": "easy", "day": s["day"], "tss": s.get("tss"),
                         "done": True, "done_by": s["done_by"], "title": s.get("title")})
    return sorted(done, key=lambda g: g.get("day") or "")


def _as_hard(wk: _Week, day: str, label: str, th: dict, out: list, touched: set, act) -> bool:
    """Rule D 太強: the run on `day` counts as a hard session — the next free hard session < 48 h
    after it moves to a free day ≥ 48 h away, else steps down (rule D′ / B's spacing). When the
    generator already moved it off its stored day (week_plan's hard_done), that move gets the
    reason. True when a session was adjusted."""
    src = _src(*SRC_TOO_HARD_PARTS, SRC_SPACING)
    nxt = sorted([h for h in wk.gens() if h.get("kind") in HARD and wk.free(h) and h.get("day")
                  and h.get("id") not in touched and 0 <= (_d(h["day"]) - _d(day)).days < SPACING_DAYS],
                 key=lambda h: h["day"])
    for h in nxt[:1]:
        pick = next((d for d in wk.open_days(skip=h) if _hard_ok(wk, h, d, extra_after=day)), None)
        was = h["day"]
        touched.add(h.get("id"))
        if pick:
            h["day"] = pick
            _adj(out, OVERHARD_RULE, wk, h, "moved",
                 _("{was} {title}延到{to}：{day}輕鬆跑跑成{label}，間隔不到 48 小時",
                   was=wd(was), title=h.get("title") or "", to=wd(pick), day=wd(day), label=label),
                 src, before_day=was, activity=act)
        else:
            old = h.get("title")
            became = _downgrade(h, th)
            _adj(out, OVERHARD_RULE, wk, h, "downgraded",
                 _("{was} {old}改成{became}：{day}輕鬆跑跑成{label}，本週沒有隔 48 小時的空日",
                   was=wd(was), old=old or "", became=became, day=wd(day), label=label),
                 src, before_title=old, activity=act)
        return True
    # week_plan keeps the remaining hard sessions 48 h from a done hard run (hard_done): the
    # generator may have moved it already — give that move this reason (reconcile's change)
    stored = {s["gen_key"]: s for s in wk.st if s.get("gen_key") and s["state"] == "active"
              and s.get("origin") == "auto" and not s.get("edited") and s["kind"] in HARD and s.get("day")}
    for h in sorted(wk.gens(), key=lambda h: h.get("day") or ""):
        s = stored.get(h.get("id"))
        if s is None or h.get("kind") not in HARD or not wk.free(h) or h.get("id") in touched \
                or not h.get("day") or h["day"] == s["day"]:
            continue
        if 0 <= (_d(s["day"]) - _d(day)).days < SPACING_DAYS <= (_d(h["day"]) - _d(day)).days:
            touched.add(h["id"])
            _adj(out, OVERHARD_RULE, wk, h, "moved",
                 _("{was} {title}延到{to}：{day}輕鬆跑跑成{label}，間隔不到 48 小時",
                   was=wd(s["day"]), title=h.get("title") or "", to=wd(h["day"]), day=wd(day), label=label),
                 src, before_day=s["day"], activity=act)
            return True
    return False


def _overhard(wk: _Week, reviews: dict, th: dict, out: list, notes: dict) -> None:
    """Rule D (module doc, SP-301): 太強 → handled as a hard session; else 偏強 → a label only."""
    touched = {a.get("gen_key") for a in out if a.get("action") != "note"}
    for g in _done_easy(wk):
        a = g["done_by"]
        idx = a.get("index")
        r = reviews.get(idx) or reviews.get(str(idx))
        rr = dict(r or {})
        if rr.get("tss") is None:
            rr["tss"] = a.get("tss")              # the activity row's real TSS
        day = a.get("date") or g["day"]
        label = too_hard(rr)
        if label:
            acted = _as_hard(wk, day, label, th, out, touched, idx)
            what = _("已調整之後的強度課") if acted else _("48 小時內沒有強度課，課表不用動")
            notes[idx] = _("輕鬆跑跑成強度課（{label}）：{what}", label=label, what=what)
            _adj(out, OVERHARD_RULE, wk, {**g, "day": day}, "note",
                 _("{day}輕鬆跑跑成強度課（{label}）：{what}", day=wd(day), label=label, what=what),
                 _(SRC_TOO_HARD), activity=idx)
            continue
        why = overhard(g.get("tss"), rr)
        if why:
            notes[idx] = _("輕鬆跑偏強（{why}）：只標示，課表不變", why=why)
            _adj(out, OVERHARD_RULE, wk, {**g, "day": day}, "note",
                 _("{day}輕鬆跑偏強（{why}）：只標示，課表不變", day=wd(day), why=why), _(SRC_OVER), activity=idx)


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
    # a run rule D's 太強 already acted on: one adjustment, not two (SP-301)
    acted = {str(a.get("activity")) for a in out if a.get("rule") == OVERHARD_RULE and a.get("action") != "note"}
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
        if not r or r.get("rpe") is None or float(r["rpe"]) < CR.HARD_RPE or str(a.get("index")) in acted:
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
                     _src(*SRC_RPE_PARTS, SRC_SPACING), before_day=was, activity=a.get("index"))
            else:
                old = h.get("title")
                became = _downgrade(h, th)
                _adj(out, RPE_RULE, wk, h, "downgraded",
                     _("{was} {old}改成{became}：{day}{what}自評 {label}，本週沒有隔 48 小時的空日",
                       was=wd(was), old=old or "", became=became, day=wd(day), what=what, label=label),
                     _src(*SRC_RPE_PARTS), before_title=old, activity=a.get("index"))


def _hr_low(*rows) -> bool:
    """Low optical-HR quality on the run: SP-402's `hr_quality` "low" (workout_review.run_hr_quality,
    carried by overview.activity_row into done_by — the field compliance.session_compliance reads
    for `uncertain`), on done_by or the review row."""
    return any((r or {}).get("hr_quality") == "low" for r in rows)


def _done_runs(wk: _Week) -> list[dict]:
    """This week's done easy runs and long runs with their activity (the generator's and the user's
    own), oldest first: the runs rule D / rpe_hard judge."""
    done = [g for g in wk.gens() if g.get("done") and (g.get("kind") in ("easy", "long") or g.get("id") == "long")
            and isinstance(g.get("done_by"), dict)]
    seen = {(g.get("done_by") or {}).get("index") for g in done}
    for s in wk.st:      # the user's own easy / long sessions that are done
        if s["state"] == "done" and s["kind"] in ("easy", "long") and isinstance(s.get("done_by"), dict) \
                and s["done_by"].get("index") not in seen:
            done.append({"id": s.get("gen_key") or s["uid"], "kind": s["kind"], "day": s["day"], "done": True,
                         "tss": s.get("tss"), "done_by": s["done_by"], "title": s.get("title")})
    return sorted(done, key=lambda g: (g["done_by"].get("date") or g.get("day") or ""))


def _too_hard_streak(wk: _Week, ctx: dict) -> Optional[list[str]]:
    """Rule E's streak (SP-404, module doc): the days of the last OVERHARD_STREAK done easy / long
    runs when every one was too hard, else None."""
    from backend.engine import coros_rpe as CR
    reviews, rated = ctx.get("reviews") or {}, (ctx.get("rpe") or {}) if ctx.get("rpe_rule", True) else {}
    last = _done_runs(wk)[-OVERHARD_STREAK:]
    if len(last) < OVERHARD_STREAK:
        return None
    for g in last:
        a = g["done_by"]
        idx = a.get("index")
        r = reviews.get(idx) or reviews.get(str(idx))
        rr = dict(r or {})
        if rr.get("tss") is None:
            rr["tss"] = a.get("tss")
        easy = g.get("kind") == "easy" and g.get("id") != "long"
        hard = easy and bool(too_hard(rr) or overhard(g.get("tss"), rr, hr=not _hr_low(a, rr)))
        rt = rated.get(idx) or rated.get(str(idx))
        if not hard and not (rt and rt.get("rpe") is not None and float(rt["rpe"]) >= CR.HARD_RPE):
            return None
    return [g["done_by"].get("date") or g["day"] for g in last]


def _fatigue(wk: _Week, stored: list[dict], ctx: dict, th: dict, out: list) -> None:
    load = ctx.get("load") or {}
    tsb, ramp, base = load.get("tsb"), load.get("ramp"), load.get("ramp_base")
    ctl = load.get("ctl")                       # the CTL the TSB is taken from: the line is a share (SP-670)
    rest_week = ctx.get("mode") in ("recovery_week", "recovery", "taper", "event", "transition", "rebuild", "reentry")
    why, remove = None, False
    tsb_hit = LG.tsb_below(tsb, ctl, TSB_FLOOR) and not rest_week
    # a planned B2B (engine/b2b.py): its TSB drop is expected — only the ramp / too-hard streak still act
    b2b_why = _b2b_exempt(ctx.get("b2b"), wk.today) if tsb_hit else None
    if b2b_why:
        _adj(out, "b2b", wk, {"day": wk.today}, "note",
             _("負荷平衡（Load balance）{tsb:+.0f}＝{pct} < −30%，但{why}：這是預期中的下降，不減量（推估）",
               tsb=tsb, pct=LG.tsb_pct_text(tsb, ctl), why=b2b_why),
             _src(*SRC_B2B_PARTS))
    if tsb_hit and not b2b_why:
        why, remove = _("負荷平衡（Load balance）{tsb:+.0f}＝{pct} < −30%", tsb=tsb, pct=LG.tsb_pct_text(tsb, ctl)), True
    elif LG.ramp_level(ramp, base) == LG.BLOCK and ctx.get("mode") != "reentry":
        # the re-entry block's 50 → 75 → 100 % steps are planned, not overload (detraining.md §6.5, 推估)
        why, remove = LG.ramp_text(ramp, base, LG.BLOCK), True
    elif (days := _too_hard_streak(wk, ctx)):
        # SP-404: the quality steps down (not removed), easy minutes × 0.8
        why = _("連續 {n} 堂輕鬆課跑太強（{days}）", n=len(days), days=_("、").join(wd(d) for d in days))
    if not why:
        return
    for q in [g for g in wk.gens() if g.get("kind") == "quality" and wk.free(g) and g.get("day") and g["day"] >= wk.today]:
        if remove:
            wk.remove(q)
            # msgids (SP-909 R1): the weekday before the title is read back as one of the seven (stored_text)
            _adj(out, "fatigue", wk, q, "removed",
                 _("{day}{title}取消：{why}，本週減量", day=wd(q["day"]), title=q["title"], why=why),
                 _src(*SRC_FATIGUE_PARTS))
        else:
            old = q["title"]
            became = _to_recovery(q, th)
            _adj(out, "fatigue", wk, q, "downgraded",
                 _("{day}{old}改成{became}：{why}，本週減量", day=wd(q["day"]), old=old, became=became, why=why),
                 _src(*SRC_FATIGUE_PARTS), before_title=old)
    for g in [g for g in wk.gens() if g.get("kind") == "easy" and wk.free(g) and g.get("day") and g["day"] >= wk.today]:
        before = g.get("minutes") or 0
        m = max(MIN_EASY_MIN, _r5(before * FATIGUE_CUT))
        if m < before:
            _set_minutes(g, m)
            _adj(out, "fatigue", wk, g, "trimmed",
                 _("{day}輕鬆跑 {before}→{m} 分：{why}，輕鬆量減 {pct}%", day=wd(g['day']), before=before, m=m, why=why,
                   pct=round((1 - FATIGUE_CUT) * 100)), _src(*SRC_FATIGUE_PARTS), before_minutes=before)


def adapt(gen_weeks: list[dict], stored: list[dict], ctx: dict) -> tuple[list[dict], list[dict], dict]:
    """(adjusted gen_weeks, adjustments, notes). `ctx`: today, first_free (first
    day sessions can go on), blocked, allowed_days, thresholds, mode (this
    week), load {tsb, ctl (the CTL the TSB is taken from), ramp, ramp_base (CTL 7 days ago)}, reviews {activity index: review metrics},
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
    _fatigue(wk, stored, ctx, th, out)
    # D after E: a session E removed / stepped down is left alone (one adjustment per session)
    _overhard(wk, ctx.get("reviews") or {}, th, out, notes)
    if ctx.get("rpe_rule", True):
        _rpe_hard(wk, ctx.get("rpe") or {}, th, out)
    return weeks, out, notes


# the notes rule D writes on the done session (a stale one is cleared): 偏強 / 太強, in either language
NOTE_PREFIX = "輕鬆跑偏強"
NOTE_TOO_HARD = "輕鬆跑跑成強度課"


def _note_prefixes() -> tuple:
    from backend.i18n import translate
    out = []
    for p in (NOTE_PREFIX, NOTE_TOO_HARD):
        out += [p, translate(p, "en")]
    return tuple(x for x in out if x)


def note_kind(note: Optional[str]) -> Optional[str]:
    """Which rule D note a done session carries: "over" (偏強, label only) / "too_hard" (太強, the next hard
    session was looked at), in either language; None for any other note (SP-484: the card's dot / ⚠)."""
    from backend.i18n import translate
    n = note or ""
    if not n:
        return None
    if n.startswith(tuple(x for x in (NOTE_TOO_HARD, translate(NOTE_TOO_HARD, "en")) if x)):
        return "too_hard"
    if n.startswith(tuple(x for x in (NOTE_PREFIX, translate(NOTE_PREFIX, "en")) if x)):
        return "over"
    return None


# R3 (docs/research/ui-hint-audit.md §5, SP-484): one 偏強 changes nothing (SP-301) — it is a dot on the card;
# a habit is worth a week note: 2 of the last 3 done easy runs, or 2 within 7 days, 偏強 or 太強 by rule D.
# The 3 / 2 / 7 are the audit's (推估); runs older than STREAK_LOOKBACK days no longer count (推估).
STREAK_LAST = 3
STREAK_MIN = 2
STREAK_DAYS = 7
STREAK_LOOKBACK = 14


def overhard_streak(sessions: list[dict], reviews: Optional[dict], today: str) -> Optional[dict]:
    """R3's 「輕鬆跑連續偏強」: {"n", "of", "days"} when STREAK_MIN of the last STREAK_LAST done easy runs
    (within STREAK_LOOKBACK days, today included) — or STREAK_MIN of those within STREAK_DAYS — were too hard
    by rule D (太強: the session class; 偏強: power / TSS / HR, overhard()), else None. A run is judged from
    its review row (`reviews`, api/plan_sessions._adapt_ctx, by activity index) like _overhard(); one without
    a row by its stored rule D note (note_kind). `n` / `of` = the too-hard runs / the runs counted,
    `days` = the too-hard runs' days (the hint's condition)."""
    reviews = reviews or {}
    t = _d(today)
    lo = (t - dt.timedelta(days=STREAK_LOOKBACK - 1)).isoformat()
    runs = []
    for s in sessions or ():
        a = s.get("done_by")
        if s.get("state") != "done" or s.get("kind") != "easy" or not isinstance(a, dict):
            continue
        day = a.get("date") or s.get("day") or ""
        if not (lo <= day <= today):
            continue
        idx = a.get("index")
        r = reviews.get(idx) if idx in reviews else reviews.get(str(idx))
        if r is not None:
            rr = dict(r)
            if rr.get("tss") is None:
                rr["tss"] = a.get("tss")
            hard = bool(too_hard(rr) or overhard(s.get("tss"), rr))
        else:
            hard = note_kind(s.get("note")) is not None
        runs.append((day, a.get("start") or "", hard))
    runs.sort()
    week_lo = (t - dt.timedelta(days=STREAK_DAYS - 1)).isoformat()
    for pick in (runs[-STREAK_LAST:], [r for r in runs if r[0] >= week_lo]):
        hard = [r[0] for r in pick if r[2]]
        if len(hard) >= STREAK_MIN:
            return {"n": len(hard), "of": len(pick), "days": hard}
    return None


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
        elif (s.get("note") or "").startswith(_note_prefixes()):
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
