"""
平衡／腳踝小課 (SP-120) — docs/research/baiyue-technical-terrain.md §3.2, §6.1;
strength-session-design.md §3.5. Applied after a week's sessions are placed (overview.week_plan,
projection.project_weeks), like the heat sessions (engine/heat_plan.py).

Why: the ankle is the first injury of hikers who fall (42.4 %, Faulhaber 2020) and 75 % of the
falls happen descending (Faulhaber 2017); balance / proprioceptive training cuts ankle sprains by
35 % (RR 0.65, Schiftan 2015 meta-analysis), 1 recurrence prevented per 9 athletes after an
8-week home programme (Hupperets 2009, BMJ). Dose: Lesinski 2015 (meta-analysis, healthy young
adults; the outcome is balance performance, not injuries) — 3 a week for 11–12 weeks, 11–15 min,
4 exercises × 2 sets × 21–40 s. Balance transfers only to trained or easier tasks (Kümmel 2016,
Aune 2026), so it gets harder and more mountain-like stage by stage.

When: only while the next A race is a 越野賽 / 百岳 (strength_plan.next_trail_a), in its 轉換期,
基礎期, 專項期 and 減量期 — the race week included (the owner, 2026-10-05: not hit by the SP-86
strength stop; its own kind "balance", not "strength"). Not on the race days, nor after them.
What (all 推估 but the dose):
  * the weeks count from the training cycle's start — its 轉換期 / 基礎期; with an open-ended
    基礎期 (no real start), from the first stored 平衡 session (first_day), else this week:
    weeks 1–3 stage 1, 4–6 stage 2, 7+ stage 3; the 專項期 and the 減量期 stage 4 (a pack of
    5–10 % body weight); 3 a week for the first 12 weeks, then 2 a week to keep it;
  * the last RACE_WEEK_DAYS days before the race: stage 1–2 moves only, nothing new;
  * 12 min (10–15), TSS 0 (no conversion; like a hot bath), on an easy-run day first (after the
    run), else a free day, spread over the week; it never takes a run's minutes;
  * the warm-up is 徐國峰's loaded ankle mobility (stand, weight on the feet, roll in / out —
    跑力提升 8.2.1.3, 教練級).
The user ticks it (no watch activity matches it), never pushed to the watch.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

from backend.engine import strength_plan as STP
from backend.i18n import N_, _

KIND = "balance"
MINUTES = 12                     # Lesinski 2015: 11–15 min
PER_WEEK = 3                     # Lesinski 2015: 3 a week …
BLOCK_WEEKS = 12                 # … for 11–12 weeks
MAINTAIN_PER_WEEK = 2            # 推估: the ticket's 1–2 a week after it
STAGE_WEEKS = 3                  # 推估: stage 1 → 2 → 3 every 3 weeks
RACE_WEEK_DAYS = 7               # 推估: stage 1–2 only in the last 7 days
SPECIFIC_WEEKS = 8               # planning.SPECIFIC_WEEKS (the 專項期 before the taper)
TAPER_DAYS = 14                  # planning.TAPER_DAYS: without a taper phase
PHASES = ("transition", "base", "specific", "taper")
MAIN = ("long", "quality", "test", "hike", "race")

MOVES = {
    1: N_("單腳站（張眼）；單腳站轉頭；單腳提踵慢放；單腳站、另一腳前後擺"),
    2: N_("單腳站閉眼；單腳站在折疊瑜珈墊或枕頭上；單腳站、另一腳往前、側、後三個方向伸出去點地；"
          "前跨步落地停住 2 秒"),
    3: N_("軟面上單腳站閉眼；單腳往前、側、斜小跳，落地停住；從 20 cm 的階往下跳、單腳落地；"
          "單腳硬舉，手往下觸地"),
    4: N_("從前面階段挑 4 個，背包 5–10 % 體重做；也可以接在下坡課或長天之後做 2–3 分（累的時候練）"),
}
NEXT = {1: N_("每個動作都能穩穩站 40 秒，再換下一階（推估）"),
        2: N_("閉眼能站 20 秒以上，再換下一階（推估）"),
        3: N_("落地不晃、膝蓋不往內夾（推估）"),
        4: ""}


def _g(x, k):
    return x.get(k) if isinstance(x, dict) else getattr(x, k, None)


def _d(x) -> Optional[dt.date]:
    if x in (None, ""):
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def _phase_ending(phases, kind: str, end: dt.date) -> Optional[dt.date]:
    """The start of the `kind` phase ending on `end`; None without one."""
    for p in phases or ():
        if _g(p, "kind") == kind and _d(_g(p, "end")) == end:
            return _d(_g(p, "start"))
    return None


def specific_start(phases, race_start: dt.date) -> dt.date:
    """The first day of the race's 專項期: the 專項期 phase before its 減量期 (planned), else
    SPECIFIC_WEEKS weeks before the taper (TAPER_DAYS without one)."""
    one = dt.timedelta(days=1)
    t0 = _phase_ending(phases, "taper", race_start - one) or race_start - dt.timedelta(days=TAPER_DAYS)
    return _phase_ending(phases, "specific", t0 - one) or t0 - dt.timedelta(weeks=SPECIFIC_WEEKS)


def first_day(since: dt.date, athlete_id: int = 1) -> Optional[dt.date]:
    """The first day of a stored 平衡／腳踝 session (active / done / missed) on / after `since` —
    when the app started planning them. Read-only sqlite like heat_data.completed_passive_dates;
    None without the DB or a row."""
    import sqlite3
    from backend.engine.wko5expr import datasource
    db = datasource._db_path()
    if db is None or not db.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT MIN(day) FROM plan_sessions WHERE athlete_id=? AND kind=? AND day >= ? "
                              "AND state IN ('active', 'done', 'missed')",
                              (athlete_id, KIND, since.isoformat())).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return _d(row[0]) if row and row[0] else None


def cycle_start(phases, monday: dt.date, kind: str, race_start: dt.date) -> Optional[dt.date]:
    """The first day of the 轉換期 / 基礎期 run of this training cycle (the one holding `monday`, or
    the one before the race's 專項期), when the phases know it: None when that run starts with the
    phases themselves (the open-ended 基礎期 auto_phases begins with — no real start)."""
    day = monday if kind in ("transition", "base") else specific_start(phases, race_start) - dt.timedelta(days=1)
    b0 = STP.block_start(phases, day)
    first = min((_d(_g(p, "start")) for p in phases or () if _g(p, "start")), default=None)
    return b0 if b0 is not None and first is not None and b0 > first else None


def week_context(events, phases, monday: dt.date, kind: str, start: Optional[dt.date] = None) -> dict:
    """{active, stage, n, weeks_in, start, race, race_start, race_end} of the week: `kind` = the
    week's phase (week_plan's status.kind / the projection's phase_kind). The weeks count from the
    cycle's 轉換期 / 基礎期 start, else from `start` (the first planned session, first_day), else
    from this week."""
    r = STP.next_trail_a(events, monday)
    if not r or kind not in PHASES:
        return {"active": False}
    rs = _d(r["start"])
    b0 = cycle_start(phases, monday, kind, rs) or min(start or monday, monday)
    b0 -= dt.timedelta(days=b0.weekday())        # its week counts as week 1
    weeks_in = max(1, (monday - b0).days // 7 + 1)
    stage = 4 if kind in ("specific", "taper") else min(3, (weeks_in - 1) // STAGE_WEEKS + 1)
    n = PER_WEEK if weeks_in <= BLOCK_WEEKS else MAINTAIN_PER_WEEK
    return {"active": True, "stage": stage, "n": n, "weeks_in": weeks_in, "start": b0.isoformat(),
            "race": r["name"], "race_start": r["start"], "race_end": r["end"]}


def session(stage: int, day: Optional[str], race_week: bool = False, i: int = 0) -> dict:
    """One 平衡／腳踝 session (a plain dict, as week_plan's sessions)."""
    nxt = _(NEXT[stage]) if NEXT.get(stage) else ""
    detail = _("熱身 1–2 分：承重式腳踝活動度——彈性站姿、體重壓在腳上，慢慢做內翻、外翻（徐國峰）。"
               "接著 4 個動作，每個 2 組 × 20–40 秒／腳：{moves}", moves=_(MOVES[stage]))
    if nxt:
        detail += "。" + nxt
    if race_week:
        detail += "。" + _("賽前 7 天：只做熟悉的階段 1–2 動作，不加新的（推估）")
    return {"id": f"balance{i + 1}", "kind": KIND, "title": _("平衡／腳踝 {m} 分（階段 {n}）", m=MINUTES, n=stage),
            "minutes": MINUTES, "target": "", "detail": detail,
            "source": _("Schiftan 2015（平衡訓練讓踝扭傷少 35 %）；Hupperets 2009；Lesinski 2015（劑量，指標是平衡表現）；"
                        "徐國峰（熱身，教練級）；階段和進階條件是推估"),
            "tss": 0.0, "day": day, "done": False, "done_by": None}


def pick_days(sessions: list, days: list, n: int) -> list:
    """Up to `n` of `days` (dates): easy-run / strength days first (after the run), then free days,
    then the rest (the long day last), two days apart when possible."""
    by = {}
    for s in sessions:
        d = _d(_g(s, "day"))
        if d is not None:
            by.setdefault(d, set()).add(_g(s, "kind") if _g(s, "id") != "long" else "long")

    def rank(d):
        ks = by.get(d, set())
        if "long" in ks or "race" in ks:
            return 3
        if ks & set(MAIN):
            return 2
        return 0 if ks & {"easy", "strength"} else 1

    order = sorted(days, key=lambda d: (rank(d), d))
    picked: list = []
    for gap in (2, 1):
        for d in order:
            if len(picked) >= n:
                break
            if d not in picked and all(abs((d - p).days) >= gap for p in picked):
                picked.append(d)
    return sorted(picked)


def apply(sessions: list, ctx: dict, days: list, allowed: Optional[Callable] = None) -> list:
    """Appends the week's 平衡／腳踝 sessions to `sessions` (dicts, already placed) on `days`
    (dates still open: not past, not 不排課; `allowed` = 課表偏好 可練日) — never on / after the race
    start. Returns the new sessions."""
    if not ctx or not ctx.get("active"):
        return []
    rs = _d(ctx["race_start"])
    cand = [d for d in days if d < rs and (allowed is None or allowed(d))]
    out = []
    for i, d in enumerate(pick_days(sessions, cand, int(ctx["n"]))):
        late = 0 < (rs - d).days <= RACE_WEEK_DAYS
        s = session(min(ctx["stage"], 2) if late else ctx["stage"], d.isoformat(), late, i)
        sessions.append(s)
        out.append(s)
    return out
