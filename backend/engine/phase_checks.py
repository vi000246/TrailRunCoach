"""SP-678: the check cards of the 轉換期 and 基礎期 pages, and the 強度課表解鎖條件 card that replaces the
5 區開放流程 (z5flow) panel on the 基礎期 page. Same shape as the 減量期 card (race_readiness.taper): a big
line, 這幾天要做, check cards {id, name, value, show, target, band, scale, state, label, todo, chart}.

  transition()  恢復檢查 after the last A race: 「賽後第 N 天」, the post-race weeks (恢復期 / 轉換期 / 回量期 as
                planned) up to the 基礎期, four checks (rest, easy only, volume back slowly, injuries) and the
                conditions for starting the 基礎期.
  base()        基礎檢查: 強度分配 (low / middle / high HR time, a donut over the last 8 weeks and one 100 %
                stacked bar a week), 每週增量 (chronic load % a week, load_guard.GOAL["base"]), 恢復週節奏 (the
                planner's 3:1, overview.weeks_since_recovery).
  unlock()      強度課表解鎖條件: two tiles (3 區, 5 區), each with its unlock checks and when it re-locks —
                the rules are quality_gate's (z3_gate, z5_card, guard, Z3_RELOCK_DAYS, a_race_rebase), this
                only says them with 「還有幾天／差多少」.
The thresholds marked 推估 are this card's own; the rest are the engine's (named where they are read)."""
from __future__ import annotations

import datetime as dt
import math
import statistics
from typing import Iterable, Optional

from backend.i18n import N_, _

STATE = {"ok": N_("做到"), "warn": N_("留意"), "wait": N_("還沒有資料"), "lock": N_("還沒開始")}

# ---- 轉換期 -------------------------------------------------------------------------------------------
# rest days without running after an A race by planning.event_size (short, medium, marathon, ultra,
# hundred): 推估 — the planner's 恢復期 has no run on its first REC_NO_RUN_DAYS (2) days; longer races rest
# longer (the 14-day 恢復期 sizes get 5 / 7)
REST_DAYS = (2, 2, 3, 5, 7)
EASY_SHARE = 0.90                # 推估: in the post-race weeks ≥ 90 % of the HR time under AeT (period_zones'
                                 # base-phase target) — no intensity session
MIN_HR_S = 600                   # < 10 min of HR time: no share
TRANSITION_SCAN_DAYS = 120       # an A race further back has no 恢復檢查

# ---- 基礎期 -------------------------------------------------------------------------------------------
WEEKS = 8
EASY_MIN = 0.80                  # 推估 (SP-678): easy ≥ 80 %, middle 5–10 %, high 10–15 % of the HR time
MID_BAND = (0.05, 0.10)
HIGH_BAND = (0.10, 0.15)
MID_RISE = 3                     # 推估: the middle share up 3 weeks in a row (4 weeks) → say how to change it
RAMP_BAND = (0.02, 0.05)         # SP-678 (owner 2026-10-16): chronic load +2–5 % a week, the range runners hold for
                                 # weeks to months (the 2 % floor 推估); the goal GOAL["base"] (4 %); over
                                 # load_guard.watch_line (max(WATCH_MIN points, WATCH_PCT × chronic load)) 注意
RECOVERY_EVERY = 3               # overview's 3:1 (SRC_31): after 3 build weeks a recovery week

CHART = {"rest": "weekly-hours", "easy_only": "intensity-distribution", "volume": "weekly-hours",
         "intensity": "intensity-distribution", "ramp": "ramp-rate", "recovery": "weekly-hours",
         "z3_base": "long-easy-drift", "low_share": "intensity-distribution"}


def _d(x) -> Optional[dt.date]:
    if x is None or isinstance(x, dt.date):
        return x
    try:
        return dt.date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def _md(d: dt.date) -> str:
    return f"{d.month}/{d.day}"


def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f} %"


def _monday(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def _check(cid: str, name: str, value, show: str, target: str, state: str, *, band=None, scale=(0, 1),
           todo=None, label=None, span=None, **extra) -> dict:
    out = {"id": cid, "name": name, "value": value, "show": show, "target": target,
           "band": list(band) if band else None, "scale": list(scale), "state": state,
           "label": label or _(STATE[state]), "todo": todo if state == "warn" else None,
           "chart": {"id": CHART.get(cid), **(span or {})} if CHART.get(cid) else None}
    out.update(extra)
    return out


def _injury(injuries: Iterable, today: dt.date) -> tuple[list[dict], str]:
    from backend.engine import injuries as INJ
    inj = INJ.active_on(list(injuries or ()), today)
    if not inj:
        return [], _("沒有進行中的傷病")
    return inj, _("進行中：{x}", x=_("、").join(INJ.full_label(e.get("area"), e.get("side")) for e in inj))


# ---------------------------------------------------------------------------------------------------
# 轉換期
# ---------------------------------------------------------------------------------------------------
def transition(race: Optional[dict], today: dt.date, acts: list[dict], *, spans: list[dict], base_from: Optional[dt.date],
               pre_hours: Optional[float], hr: Optional[dict] = None, injuries: Iterable = ()) -> dict:
    """The 恢復檢查 of `race` ({"name", "end", "size"}: the last A race that ended before today; None = none).
    `acts`: {date, hours, run} since the race; `spans`: the planned post-race phases [{kind, start, end}]
    (恢復期 / 轉換期 / 回量期) in order; `base_from`: the 基礎期's first day after them; `pre_hours`: the
    weekly hours of the 4 weeks before its taper (planning.pre_race_mondays); `hr`: HR seconds since the
    race {"low", "all"} (low = under AeT)."""
    from backend.engine import planning as PL
    base = {"kind": "transition", "race": None, "checks": [], "this_week": [], "strip": [], "conds": []}
    if race is None:
        return {**base, "text": _("最近沒有比完的 A 賽事：比完 A 賽之後，這裡會看賽後恢復")}
    end = _d(race["end"])
    day_n = (today - end).days
    if base_from is not None and today >= base_from:
        return {**base, "race": {"name": race["name"], "end": end.isoformat()}, "done": True,
                "text": _("「{name}」的賽後恢復在 {d} 結束了：現在看 ② 基礎期頁", name=race["name"], d=_md(base_from))}
    first = end + dt.timedelta(days=1)
    last = (base_from - dt.timedelta(days=1)) if base_from else first + dt.timedelta(days=6)
    n_weeks = max(1, math.ceil(((last - first).days + 1) / 7))
    wk = min(n_weeks, (today - first).days // 7 + 1)
    strip = [{"label": _("第{n}週", n=i + 1), "state": "past" if i + 1 < wk else "today" if i + 1 == wk else "next"}
             for i in range(n_weeks)]
    if base_from:
        strip.append({"label": _("→ 基礎期 {d}", d=_md(base_from)), "state": "base"})
    span_now = next((s for s in spans if _d(s["start"]) <= today <= _d(s["end"])), None)
    kind_now = span_now["kind"] if span_now else "transition"
    after = [a for a in acts if first <= _d(a["date"]) <= today]
    checks = []
    span = {"begin": (end - dt.timedelta(days=56)).isoformat(), "end": today.isoformat()}

    # ① rest: days without a run after the race
    size = int(race.get("size") or 0)
    need = REST_DAYS[min(max(size, 0), len(REST_DAYS) - 1)]
    runs = sorted(_d(a["date"]) for a in after if a.get("run"))
    size_t = _(PL.SIZE_LABEL[PL.SIZES[size]])
    tgt = _("賽後完全休息或只走路；比賽越長休越久（{s}：至少 {n} 天）", s=size_t, n=need)
    if runs:
        rested = (runs[0] - end).days - 1
        st = "ok" if rested >= need else "warn"
        checks.append(_check("rest", _("先休息"), rested, _("{n} 天", n=rested), tgt, st, span=span,
                             label=_("太早跑") if st == "warn" else None,
                             todo=_("這幾天跑短一點、慢一點；腿還痠就再休一天")))
    else:
        rested = day_n
        st = "ok" if rested >= need else "wait"
        checks.append(_check("rest", _("先休息"), rested, _("{n} 天", n=rested), tgt, st, span=span,
                             label=_("休息中") if st == "wait" else None))
    rest_ok = st == "ok"

    # ② easy only: HR time under AeT since the race
    share = (hr["low"] / hr["all"]) if hr and hr.get("all", 0) >= MIN_HR_S else None
    st = "wait" if share is None else "ok" if share >= EASY_SHARE - 1e-9 else "warn"
    checks.append(_check("easy_only", _("只跑輕鬆"), share, _pct(share) if share is not None else "≥ " + _pct(EASY_SHARE),
                         _("賽後的心率時間 ≥ {p} 在 AeT 以下（不做強度課）", p=_pct(EASY_SHARE)), st,
                         band=(EASY_SHARE, 1.0), scale=(0.5, 1.0), span=span,
                         label=_("有強度") if st == "warn" else None,
                         todo=_("剩下的跑步都放慢到 AeT 以下，強度課等基礎期再排")))
    easy_ok = st != "warn"

    # ③ volume back slowly: this week's hours ÷ the usual before the race, ≤ the phase's share
    mon = _monday(today)
    cap = _week_share(kind_now, span_now, mon)
    v = None
    if pre_hours:
        v = round(sum(a["hours"] for a in acts if mon <= _d(a["date"]) <= today and _d(a["date"]) > end) / pre_hours, 2)
    st = "wait" if v is None else "warn" if v > cap + 1e-9 else "ok"
    phase_t = _(PL.PHASES.get(kind_now, kind_now))
    checks.append(_check("volume", _("量慢慢回來"), v, _pct(v) if v is not None else "≤ " + _pct(cap),
                         (_("這週是賽前平常量（{h} 小時／週）的幾 %；{p}這週 ≤ {c}", h=f"{pre_hours:.1f}", p=phase_t, c=_pct(cap))
                          if pre_hours else _("賽前沒有平常量的紀錄，算不出比例")), st,
                         band=(0, cap), scale=(0, max(1.0, (v or 0) + 0.1)), span=span,
                         label=_("偏多") if st == "warn" else _("沒有資料") if v is None else None,
                         todo=_("這週剩下的課縮短一點")))
    vol_ok = st != "warn"

    # ④ soreness and injuries
    inj, inj_t = _injury(injuries, today)
    if inj:
        e = inj[0]
        from backend.engine import injuries as INJ
        o = _d(e.get("onset_date"))
        checks.append(_check("injury", _("痠痛與傷病"), len(inj), INJ.full_label(e.get("area"), e.get("side")),
                             _("傷病紀錄：第 {n} 天", n=(today - o).days + 1) if o else _("傷病紀錄"), "warn",
                             label=_("留意"), todo=_("沒好之前不要回到基礎期的強度")))
    else:
        checks.append(_check("injury", _("痠痛與傷病"), 0, _("沒有"), _("傷病紀錄沒有進行中的"), "ok"))
    inj_ok = not inj

    this_week = []
    if pre_hours:
        this_week.append(_("只跑輕鬆，這週最多 {h} 小時", h=f"{pre_hours * cap:.1f}"))
    for c in checks:
        if c["todo"]:
            this_week.append(c["todo"])
    conds = [{"text": _("休息做到"), "ok": rest_ok}, {"text": _("只跑輕鬆"), "ok": easy_ok},
             {"text": _("量在 {p} 以內", p=_pct(cap)), "ok": vol_ok}, {"text": _("沒有痠痛"), "ok": inj_ok}]
    return {**base, "race": {"name": race["name"], "end": end.isoformat()}, "today": today.isoformat(),
            "day_after": day_n, "weeks": n_weeks, "week": wk, "strip": strip,
            "base_from": base_from.isoformat() if base_from else None, "phase": kind_now, "checks": checks,
            "this_week": this_week[:3], "conds": conds, "ready": all(c["ok"] for c in conds),
            "injury": inj_t, "injury_on": bool(inj),
            "text": _("賽後第 {n} 天", n=day_n)}


def _week_share(kind: str, span: Optional[dict], monday: dt.date) -> float:
    """The planner's share of the pre-race level for this week: 恢復期 REC_SHARE, 轉換期 TRANSITION_SHARE,
    回量期 rebuild_share (50 → 75 %)."""
    from backend.engine import overview as O
    from backend.engine import planning as PL
    if kind == "recovery":
        return PL.REC_SHARE
    if kind == "rebuild" and span is not None:
        return PL.rebuild_share(span, monday)[0]
    return O.TRANSITION_SHARE


# ---------------------------------------------------------------------------------------------------
# 基礎期
# ---------------------------------------------------------------------------------------------------
def base(today: dt.date, weeks: list[dict], ctl: list, *, phase: Optional[dict] = None, injuries: Iterable = (),
         skip: Iterable = ()) -> dict:
    """The 基礎檢查. `weeks`: the last WEEKS weeks oldest first, this week last: {monday, hours, low, mid,
    high} (HR seconds under AeT / AeT–LTHR / ≥ LTHR); `ctl`: [(date, chronic load)] (≥ WEEKS × 7 + 7 days);
    `phase`: {kind, start, end, next_start (the next phase's first day), next_kind}; `skip`: Mondays of
    weeks that touch a 減量期 / race / post-race phase (load_guard.skip_mondays: recovery-like)."""
    from backend.engine import load_guard as LG
    from backend.engine import overview as O
    skip = {_d(m) for m in skip}
    mon = _monday(today)
    # recovery-like weeks (overview.weeks_since_recovery's rule) among the complete ones
    hrs = [w["hours"] for w in weeks]
    rec = []
    for i, w in enumerate(weeks):
        prev = [h for h in hrs[max(0, i - 3):i] if h > 0]
        rec.append(w["monday"] in skip or bool(prev) and w["monday"] != mon
                   and w["hours"] <= O.RECOVERY_DROP * statistics.mean(prev))
    span8 = {"begin": weeks[0]["monday"].isoformat() if weeks else today.isoformat(), "end": today.isoformat()}
    checks = []

    # ① intensity distribution
    tot = [w["low"] + w["mid"] + w["high"] for w in weeks]
    allt = sum(tot)
    bars = []
    for w, t, r in zip(weeks, tot, rec):
        sh = (w["low"] / t, w["mid"] / t, w["high"] / t) if t >= MIN_HR_S else None
        bars.append({"monday": w["monday"].isoformat(), "shares": [round(x, 3) for x in sh] if sh else None,
                     "recovery": bool(r), "now": w["monday"] == mon})
    if allt >= MIN_HR_S:
        lo, mid, hi = (sum(w["low"] for w in weeks) / allt, sum(w["mid"] for w in weeks) / allt,
                       sum(w["high"] for w in weeks) / allt)
        donut = [round(lo, 3), round(mid, 3), round(hi, 3)]
    else:
        lo = mid = hi = None
        donut = None
    mids = [b["shares"][1] for b in bars if b["shares"] and not b["recovery"]]
    rise = 0
    for a, b in zip(mids, mids[1:]):
        rise = rise + 1 if b > a + 1e-9 else 0
    if lo is None:
        st, lab, todo = "wait", None, None
    elif lo < EASY_MIN - 1e-9 or mid > MID_BAND[1] + 1e-9:
        st = "warn"
        lab = _("中間偏多") if mid > MID_BAND[1] + 1e-9 else _("輕鬆偏少")
        todo = (_("中間區連 {n} 週變多：輕鬆跑放慢，強度課照排", n=rise + 1) if rise >= MID_RISE else
                _("輕鬆跑放慢到 AeT 以下，強度課照排"))
    else:
        st, lab, todo = "ok", _("照計畫"), None
    if st == "ok" and hi is not None and hi > HIGH_BAND[1] + 1e-9:
        st, lab, todo = "warn", _("高強度偏多"), _("強度課每週最多 1–2 堂，其他都跑輕鬆")
    targets = [{"key": "low", "name": _("輕鬆"), "target": _("目標 ≥ {p}", p=f"{EASY_MIN * 100:.0f}")},
               {"key": "mid", "name": _("中間"), "target": _("目標 {a}–{b}", a=f"{MID_BAND[0] * 100:.0f}", b=f"{MID_BAND[1] * 100:.0f}")},
               {"key": "high", "name": _("高強度"), "target": _("目標 {a}–{b}", a=f"{HIGH_BAND[0] * 100:.0f}", b=f"{HIGH_BAND[1] * 100:.0f}")}]
    checks.append(_check("intensity", _("強度分配"), lo, _pct(lo) if lo is not None else "—",
                         _("近 {n} 週的心率時間：輕鬆＝AeT 以下、中間＝AeT 到 LTHR、高強度＝LTHR 以上", n=len(weeks)), st,
                         label=lab, todo=todo, span=span8, donut=donut, bars=bars, targets=targets, mid_rise=rise))

    # ② weekly increase of chronic load (% a week; the planner's goal load_guard.GOAL["base"])
    cmap = {d: v for d, v in ctl}
    goal_pct = LG.GOAL["base"][0]

    def ramp_on(d: dt.date) -> Optional[tuple[float, float]]:
        a, b = cmap.get(d - dt.timedelta(days=7)), cmap.get(d)
        if a is None or b is None or a <= 0:
            return None
        return (b - a) / a, a
    lo_b, hi_b = RAMP_BAND

    def watch_pct(a: float) -> float:
        # SP-678: load_guard's 注意 line, max(WATCH_MIN points, WATCH_PCT × chronic load), as a % of chronic load
        return LG.watch_line(a) / a

    def zone(pct: float, a: float) -> str:
        top_a = watch_pct(a)
        return "low" if pct < lo_b - 1e-9 else "ok" if pct <= hi_b + 1e-9 else "fast" if pct < top_a - 1e-9 else "high"
    rows = []
    for w, r in zip(weeks, rec):
        d = min(today, w["monday"] + dt.timedelta(days=6))
        x = ramp_on(d)
        pct = round(x[0], 4) if x else None
        rows.append({"monday": w["monday"].isoformat(), "pct": pct, "pts": round(x[0] * x[1], 1) if x else None,
                     "recovery": bool(r), "zone": None if r or pct is None else zone(x[0], x[1]),
                     "now": w["monday"] == mon})
    now = ramp_on(today)
    v = now[0] if now else None
    z = zone(v, now[1]) if v is not None else None
    top = round(watch_pct(now[1]), 4) if now else LG.WATCH_PCT
    if v is None:
        st, lab, todo = "wait", None, None
    elif rec[-1]:
        st, lab, todo = "ok", _("恢復週"), None
    elif z == "high":
        st, lab, todo = "warn", _("注意"), _("這週先不加量，強度課只排閾值以下")
    elif z == "fast":
        st, lab, todo = "warn", _("偏快"), _("下週加量少一點，回到每週 +{a}–{b}", a=_pct(lo_b), b=_pct(hi_b))
    elif z == "low":
        st, lab, todo = "warn", _("沒在進步"), _("量慢慢加：每週長期負荷 +{p} 左右", p=_pct(goal_pct))
    else:
        st, lab, todo = "ok", _("照計畫"), None
    out_n = sum(1 for x in rows if x["zone"] not in (None, "ok"))
    show = (_("{p}（{d} 點／週）", p=f"{v * 100:+.1f} %", d=f"{v * now[1]:+.1f}") if v is not None else "—")
    checks.append(_check("ramp", _("每週增量"), v, show,
                         _("長期負荷每週增加多少（占長期負荷的 %）。綠色帶 {lo}–{hi}，目標 {g}；超過 {w} 注意（紅色帶）",
                           lo=_pct(lo_b), hi=_pct(hi_b), g=_pct(goal_pct), w=_pct(top)), st,
                         band=RAMP_BAND, scale=(-0.1, 0.15), label=lab, todo=todo, span=span8, weeks=rows,
                         goal=goal_pct, watch=top, line=True,
                         note=(_("近 {n} 週都在綠色帶內；恢復週（灰）不算", n=len(rows)) if not out_n else
                               _("近 {n} 週有 {k} 週在綠色帶外；恢復週（灰）不算", n=len(rows), k=out_n))))

    # ③ recovery-week rhythm (3:1)
    done = [r for r, w in zip(rec, weeks) if w["monday"] != mon]
    since = 0
    for r in reversed(done):
        if r:
            break
        since += 1
    last_rec = next((w["monday"] for r, w in zip(reversed(rec), reversed(weeks)) if r and w["monday"] != mon), None)
    nxt = mon + dt.timedelta(weeks=max(0, RECOVERY_EVERY - since))
    if since >= O.RECOVERY_MAX_GAP:
        st, lab, todo = "warn", _("太久沒恢復"), _("這週改成恢復週（量 {p}）", p=_pct(O.RECOVERY_WEEK_SHARE))
    else:
        st, lab, todo = "ok", _("照計畫"), None
    checks.append(_check("recovery", _("恢復週節奏"), since + 1, _("第 {n} 週", n=since + 1),
                         _("每練 {n} 週排 1 週恢復週（量 {p}）", n=RECOVERY_EVERY, p=_pct(O.RECOVERY_WEEK_SHARE))
                         + (_("；上次 {d}", d=_md(last_rec)) if last_rec else ""), st, label=lab, todo=todo,
                         span=span8, next=nxt.isoformat(),
                         note=_("這週是恢復週") if nxt == mon else _("下一次恢復週：{d} 那週", d=_md(nxt))))

    inj, inj_t = _injury(injuries, today)
    this_week = [c["todo"] for c in checks if c.get("todo")]
    cur = weeks[-1]["hours"] if weeks else 0.0
    last = weeks[-2]["hours"] if len(weeks) > 1 else None
    this_week.append(_("這週到目前 {h} 小時", h=f"{cur:.1f}") +
                     (_("（上週 {h} 小時）", h=f"{last:.1f}") if last is not None else ""))
    out = {"kind": "base", "today": today.isoformat(), "checks": checks, "this_week": this_week[:3],
           "injury": inj_t, "injury_on": bool(inj)}
    if phase and phase.get("kind") == "base":
        s, e = _d(phase["start"]), _d(phase["end"])
        n = max(1, math.ceil(((e - _monday(s)).days + 1) / 7)) if e else None
        out["text"] = (_("基礎期第 {i} 週 / {n} 週", i=(mon - _monday(s)).days // 7 + 1, n=n) if n
                       else _("基礎期第 {i} 週", i=(mon - _monday(s)).days // 7 + 1))
        ns = _d(phase.get("next_start"))
        if ns and phase.get("next_kind") == "specific":
            out["next"] = _("專項期 {d} 開始", d=ns.isoformat())
            out["next_from"] = ns.isoformat()
    else:
        out["text"] = _("近 {n} 週", n=len(weeks))
    return out


# ---------------------------------------------------------------------------------------------------
# 強度課表解鎖條件
# ---------------------------------------------------------------------------------------------------
def unlock(card: dict, gate: dict, today: dt.date, *, low_share: Optional[float] = None,
           last_run: Optional[dt.date] = None, next_a: Optional[dict] = None, rule: Optional[dict] = None) -> dict:
    """The two tiles from quality_gate's own results: `card` = z5_card(gate), `gate` = evaluate()'s result
    (gate["z3"] = z3_gate), `low_share`: the 4-week HR time under AeT (status.i_intensity), `last_run`: the
    last run day, `next_a`: {"name", "start"} of the next A race, `rule`: quality_gate.z3_rule()."""
    from backend.engine import quality_gate as QG
    from backend.engine import reentry as RE
    rule = rule or {"weeks": QG.Z3_WEEKS_NEED, "runs": QG.Z3_RUNS_PER_WEEK, "gap": QG.Z3_MAX_GAP_DAYS,
                    "relock": QG.Z3_RELOCK_DAYS}
    z3g = gate.get("z3") if isinstance(gate.get("z3"), dict) else (card.get("z3_gate") or {})
    zt = card.get("z5_gate") or {}
    rebase = gate.get("rebase") or None
    z3_open = bool(z3g.get("open"))
    z5_open = bool(zt.get("open"))

    # ---- 3 區
    tests = {t.get("key"): t for t in z3g.get("tests") or ()}
    # after an A race (a_race_rebase) only evidence after it counts: a test passed before is not ticked
    counts = z3_open or not z3g.get("rebase")
    paths = [
        {"key": "weeks", "text": _("連續 {w} 週，每週跑 ≥ {r} 次、沒有 ≥ {g} 天沒跑", w=rule["weeks"], r=rule["runs"], g=rule["gap"]),
         "ok": counts and bool((tests.get("weeks") or {}).get("ok")),
         "now": _("{n}/{w} 週", n=min(int(z3g.get("weeks") or 0), rule["weeks"]), w=rule["weeks"])},
        {"key": "xu90", "text": _("90 分鐘平路輕鬆跑，心率飄移 < 10 %"),
         "ok": counts and bool((tests.get("xu90") or {}).get("ok"))},
        {"key": "ua_gap", "text": _("實測 AeT，和 LTHR 差 ≤ 10 %"), "ok": counts and bool((tests.get("ua_gap") or {}).get("ok"))},
    ]
    z3_checks = [_check("z3_base", _("有氧打穩（三選一）"), None,
                        _("做到") if z3_open else _("還沒"),
                        _("有氧基礎夠了才開 3 區：下面任一項做到就解鎖")
                        + (_("（{x}：只算 {d} 之後的）", x=rebase["text"], d=_md(_d(rebase["from"])))
                           if rebase and not z3_open else ""), "ok" if z3_open else "warn",
                        label=_("做到") if z3_open else _("還沒"), paths=paths,
                        todo=_("照課表規律跑；或排一次 90 分鐘平路輕鬆跑測試"))]
    if z3_open:
        z3_checks.append(_check("keep", _("之後怎麼保持"), low_share, _pct(low_share) if low_share is not None else "—",
                                _("輕鬆跑盡量壓在 AeT 以下；跑太多 AeT 以上那週 5 區先不排、3 區照排"),
                                "ok" if low_share is None or low_share >= QG.LOW_SHARE_MIN else "warn",
                                label=_("照計畫") if low_share is None or low_share >= QG.LOW_SHARE_MIN else _("偏快"),
                                todo=_("輕鬆跑放慢到 AeT 以下")))
    relock3 = []
    left = None
    if last_run is not None:
        until = last_run + dt.timedelta(days=int(rule["relock"]))
        left = max(0, (until - today).days)
        relock3.append({"key": "break", "title": _("連續 {n} 天沒跑步 → 3 區重新上鎖（中斷 {a}–{b} 天要先跑一段回復期）",
                                                   n=rule["relock"], a=RE.MIN_BREAK, b=int(rule["relock"]) - 1),
                        "now": _("最後一次跑步 {d}・{u} 前要再跑，還有 {n} 天", d=_md(last_run), u=_md(until), n=left),
                        "days_left": left, "of": int(rule["relock"])})
    relock3.append({"key": "a_race", "title": _("A 賽之後 → 3 區、5 區都要用賽後的 E 配速、CP、LTHR 重新確認一次"),
                    "now": (_("現在：{x}，重新確認前只排輕鬆跑、長跑和加速跑", x=rebase["text"]) if rebase else
                            _("下一場 A 賽 {d}・賽後重新確認前，只排輕鬆跑、長跑和加速跑", d=_md(_d(next_a["start"])))
                            if next_a else _("還沒有下一場 A 賽"))})
    z3 = {"open": z3_open, "path": z3g.get("path_label") or "", "since": z3g.get("since"),
          "head": (_("3 區已解鎖") if z3_open else _("解鎖 3 區：還差 1 項")),
          "body": (_("節奏跑可以排了") if z3_open else _("先把有氧基礎打穩")),
          "checks": z3_checks, "relock": relock3 if z3_open else [],
          "relock_tag": (_("目前安全・還有 {n} 天", n=left) if left is not None else _("點開看")),
          "relock_warn": left is not None and left <= 7}

    # ---- 5 區
    zc = card.get("z3") or {}
    b = card.get("base") or {}
    z3_ok, aet_ok = bool(zt.get("z3_ok")), bool(zt.get("aet_ok"))
    z5_checks = [
        _check("z3_first", _("先做幾堂 3 區"), int(zc.get("done") or 0),
               _("{d}/{n} 堂", d=int(zc.get("done") or 0), n=int(zc.get("need") or QG.Z5_Z3_NEED)),
               _("近 {w} 週做過 ≥ {n} 堂 3 區，才輪到 5 區", w=QG.Z5_Z3_DAYS // 7, n=QG.Z5_Z3_NEED),
               "ok" if z3_ok else "warn", label=None if z3_ok else _("還差"),
               todo=_("先排 3 區（節奏跑）") if z3_open else _("先解鎖 3 區")),
    ]
    tests5 = b.get("tests") or []
    ta = (gate.get("z5") or {}).get("aet_tested") or {}
    lt = gate.get("lthr") or {}
    aet_show = "—"
    if ta.get("value") and lt.get("value") and lt.get("measured") and not lt.get("default"):
        g = QG.ua_gap(float(ta["value"]), float(lt["value"]))
        if g is not None:
            aet_show = f"{g * 100:.0f} %"
    elif ta.get("value"):
        aet_show = f"{float(ta['value']):.0f} bpm"
    z5_checks.append(_check("aet_test", _("AeT 測試"), None, "✓" if aet_ok else aet_show,
                            _("實測 AeT 和 LTHR 差 ≤ 10 %（或 AeT 附近跑 60 分鐘飄移 < 5 %）"),
                            "ok" if aet_ok else "warn", label=None if aet_ok else (_("還沒做") if not ta else _("還差")),
                            todo=(_("排一次：平路 AeT 附近跑 60 分鐘") if not ta else
                                  _("重測 30 分鐘 LTHR") if (lt.get("invalid") or not lt.get("measured")) else
                                  _("繼續有氧基礎，之後重測 AeT")),
                            tests=[{"ok": t.get("ok"), "key": t.get("key")} for t in tests5],
                            action=None if aet_ok else QG.schedule_action("test", "aet", QG.AET_TEST_PROTOCOL)["href"]))
    floor = QG.LOW_SHARE_MIN
    ls_ok = low_share is None or low_share >= floor
    z5_checks.append(_check("low_share", _("輕鬆跑壓在 AeT 以下"), low_share,
                            _pct(low_share) if low_share is not None else "—",
                            _("近 4 週心率在 AeT 以下的時間；每週檢查，低於 {f} 那週 5 區先不排（基礎期目標 90 %）", f=_pct(floor)),
                            "ok" if ls_ok else "warn", band=(floor, 1.0), scale=(0.5, 1.0),
                            label=_("每週檢查") if ls_ok and low_share is not None else None,
                            todo=_("輕鬆跑放慢到 AeT 以下，下週再看"), weekly=True))
    missing = int(not z3_ok) + int(not aet_ok)
    inv = (lt.get("invalid") or {}).get("text")
    relock5 = [
        {"key": "low_share", "title": _("輕鬆跑跑太快：近 4 週 AeT 以下 < {f} → 那週 5 區先不排（壓回來就恢復）", f=_pct(floor)),
         "now": (_("現在 {p}，還差 {d} 會碰到", p=_pct(low_share), d=_pct(max(0.0, low_share - floor)))
                 if low_share is not None and ls_ok else
                 _("現在 {p}：這週 5 區先不排", p=_pct(low_share)) if low_share is not None else "")},
        {"key": "lthr", "title": _("測驗失效：LTHR 重測或失效、AeT 跟著變 → 要重做一次 AeT 測試"),
         "now": _("現在：{x}", x=inv) if inv else _("現在沒有失效")},
        {"key": "z3", "title": _("3 區重新上鎖（連續 {n} 天沒跑）→ 5 區也跟著鎖", n=rule["relock"]),
         "now": relock3[0]["now"] if relock3 and relock3[0]["key"] == "break" else ""},
        {"key": "a_race", "title": _("A 賽之後 → 要用賽後的數字重新確認"), "now": relock3[-1]["now"]},
    ]
    state5 = card.get("state")
    head5 = (_("5 區已解鎖") if z5_open else _("恢復期：先只跑輕鬆") if state5 == "reentry" else
             _("暫停：要重新確認") if state5 == "paused" else _("解鎖 5 區：還差 {n} 項", n=missing))
    todo5 = [c["todo"] for c in z5_checks if c["todo"] and c["id"] != "low_share"]
    z5 = {"open": z5_open, "missing": missing, "head": head5,
          "body": _("高強度間歇可以排了") if z5_open else "", "chips": todo5, "checks": z5_checks, "relock": relock5}
    if not z3_open:
        z3["chips"] = [z3_checks[0]["todo"]]
    else:
        z3["chips"] = []
    tiles = [{"zone": 3, "open": z3_open, "name": _("節奏跑"),
              "sub": (_("{d} 解鎖", d=_md(_d(z3g["since"]))) if z3_open and _d(z3g.get("since")) else
                      _("已解鎖") if z3_open else _("還差 1 項"))},
             {"zone": 5, "open": z5_open, "name": _("高強度間歇"),
              "sub": _("已解鎖") if z5_open else _("還差 {n} 項", n=missing)}]
    sel = 3 if not z3_open else 5 if not z5_open else 3
    return {"kind": "unlock", "today": today.isoformat(), "tiles": tiles, "z3": z3, "z5": z5, "selected": sel}


# ---------------------------------------------------------------------------------------------------
# the Dataset side
# ---------------------------------------------------------------------------------------------------
def _safe(f, default):
    try:
        return f()
    except Exception:                       # noqa: BLE001 — a missing input = that check without data
        return default


def _rows(ds, lo: dt.date, today: dt.date) -> list[dict]:
    from backend.engine import overview as O
    out = []
    for w in O.workouts_between(ds, lo, today + dt.timedelta(days=1)):
        c = O.category(w)
        if c not in O.ENDURANCE:
            continue
        out.append({"date": O.wdate(w), "hours": O.moving_s(w) / 3600.0, "run": w.sport == "run", "idx": w.idx})
    return out


def hr_split(st, lo: dt.date, today: dt.date) -> dict:
    """{date: [low, mid, high]} HR seconds (under AeT / AeT–LTHR / ≥ LTHR) of the workouts from `lo`."""
    from backend.engine import overview as O
    n = max(0, (today - lo).days)
    exprs = ("heartrate > 0 and heartrate < aethr", "heartrate >= aethr and heartrate < lthr", "heartrate >= lthr")
    vals = [st.ws(f"athleterange(today-{n}, today, sum(if({x}, deltatime)))") for x in exprs]
    out: dict = {}
    for k, v in enumerate(vals):
        for idx, s in v.items():
            d = O.wdate(st.ds.workouts[idx])
            out.setdefault(d, [0.0, 0.0, 0.0])[k] += s
    return out


def last_a_race(plan, today: dt.date) -> tuple:
    """(the last A event that ended before today within TRANSITION_SCAN_DAYS, its post-race phases
    [{kind, start, end}], the 基礎期's first day after them)."""
    from backend.engine import planning as PL
    evs = [e for e in plan.events if getattr(e, "priority", "A") == "A" and e.end < today
           and (today - e.end).days <= TRANSITION_SCAN_DAYS]
    if not evs:
        return None, [], None
    ev = max(evs, key=lambda e: e.end)
    one = dt.timedelta(days=1)
    ps = PL.phases(plan, ev.end - dt.timedelta(days=400), ev.end + dt.timedelta(days=TRANSITION_SCAN_DAYS))
    spans, d = [], ev.end + one
    for p in sorted((p for p in ps if p.kind in PL.POST_RACE_KINDS and _d(p.end) > ev.end), key=lambda p: _d(p.start)):
        if _d(p.start) > d:
            break
        spans.append({"kind": p.kind, "start": p.start, "end": p.end})
        d = _d(p.end) + one
    return ev, spans, (d if spans else None)


def compute_transition(plan, ds, today: dt.date) -> dict:
    from backend.engine import injuries as INJ
    from backend.engine import planning as PL
    from backend.engine.status import Status
    ev, spans, base_from = last_a_race(plan, today)
    if ev is None:
        return transition(None, today, [], spans=[], base_from=None, pre_hours=None)
    ps = PL.phases(plan, ev.end - dt.timedelta(days=400), ev.end + dt.timedelta(days=7))
    mons = PL.pre_race_mondays(ps, ev.end + dt.timedelta(days=1))
    lo = min([ev.end] + mons)
    rows = _safe(lambda: _rows(ds, lo, today), [])
    pre = None
    if mons:
        pre = sum(r["hours"] for r in rows if mons[0] <= _d(r["date"]) < mons[-1] + dt.timedelta(days=7)) / len(mons)
    st = Status(ds, plan, today)
    hs = _safe(lambda: hr_split(st, ev.end + dt.timedelta(days=1), today), {})
    hr = {"low": sum(v[0] for d, v in hs.items() if d > ev.end), "all": sum(sum(v) for d, v in hs.items() if d > ev.end)}
    race = {"name": ev.name, "end": ev.end, "size": _safe(lambda: PL.event_size(ev), 0)}
    return transition(race, today, [r for r in rows if _d(r["date"]) > ev.end], spans=spans, base_from=base_from,
                      pre_hours=pre or None, hr=hr, injuries=_safe(INJ.load_events, []))


def compute_base(plan, ds, today: dt.date) -> dict:
    from backend.engine import injuries as INJ
    from backend.engine import load_guard as LG
    from backend.engine import planning as PL
    from backend.engine.status import Status
    from backend.engine.wko5expr.dataset import date_to_day, day_to_date
    mon = _monday(today)
    mons = [mon - dt.timedelta(weeks=k) for k in range(WEEKS - 1, -1, -1)]
    st = Status(ds, plan, today)
    rows = _safe(lambda: _rows(ds, mons[0], today), [])
    hs = _safe(lambda: hr_split(st, mons[0], today), {})
    weeks = []
    for m in mons:
        e = m + dt.timedelta(days=6)
        z = [sum(v[k] for d, v in hs.items() if m <= d <= e) for k in range(3)]
        weeks.append({"monday": m, "hours": sum(r["hours"] for r in rows if m <= _d(r["date"]) <= e),
                      "low": z[0], "mid": z[1], "high": z[2]})
    ctl_s, _atl, _tsb = st.ev.pmc()
    days = range(int(date_to_day(mons[0] - dt.timedelta(days=8))), int(date_to_day(today)) + 1)
    ctl = [(day_to_date(d), v) for d in days for v in (ctl_s.at(d),) if v is not None and v == v]
    ph = None
    p = _safe(lambda: PL.phase_on(plan, today), None)
    if p is not None:
        nxt = _safe(lambda: PL.phase_on(plan, _d(p.end) + dt.timedelta(days=1)), None) if p.end else None
        ph = {"kind": p.kind, "start": p.start, "end": p.end,
              "next_start": nxt.start if nxt else None, "next_kind": nxt.kind if nxt else None}
    skip = _safe(lambda: LG.skip_mondays(plan, mons), set())
    return base(today, weeks, ctl, phase=ph, injuries=_safe(INJ.load_events, []), skip=skip)


def compute_unlock(ds, plan, today: dt.date, st=None) -> dict:
    """unlock() on the status gate (the object the planner and the 總覽 card read)."""
    from backend.engine import quality_gate as QG
    from backend.engine import race_readiness as RD
    if st is None:
        from backend.api.overview import _status
        st = _status(ds, today)
    gate = next((i.extra for i in st.indicators if i.id == "gate"), None) or {}
    card = QG.z5_card(gate, today)
    ie = next((i.extra for i in st.indicators if i.id == "intensity"), None) or {}
    rd = _safe(lambda: QG.run_days(ds, today), [])
    rc, _e = _safe(lambda: RD._next_race(plan, today), (None, None))
    return unlock(card, gate, today, low_share=ie.get("low_share"), last_run=rd[-1] if rd else None,
                  next_a={"name": rc["name"], "start": rc["start"]} if rc else None, rule=_safe(QG.z3_rule, None))
