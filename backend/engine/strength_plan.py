"""
肌力課依期別換內容 (SP-119) — docs/research/strength-session-design.md §5,
bompa-periodization-strength.md §4.2, baiyue-technical-terrain.md §4.

Only when the next A race is a 越野賽 or a 百岳 (Event.kind race / baiyue); a road / other A race or
none keeps the old session (「肌力（下肢單腳＋核心）」 35 分). The count per week, the placement and
the SP-86 stop (no strength in the A race's 減量期, overview.drop_strength_before_a) are unchanged:
only the title, the text, the minutes and the source change (the app has no strength data, so no
scoring). The stage, by the week's phase (Bompa & Buzzichelli 2015, §2.4 of the bompa doc):

  * 轉換期, 回量期 (SP-98, the reverse taper after it) and the first AA_WEEKS weeks of the 基礎期
    (counted from the 轉換期 / 回量期 before it, else from the 基礎期's start) — 解剖適應 (AA): a
    6-station circuit, 12–15 reps, 1–2 left in the tank;
  * the rest of the 基礎期 — 最大肌力: split squat 3–6 reps × 3–4 sets, a loaded step-down, pull-ups;
  * 專項期 (and a 減量期 day before the stop) — 維持: 2–4 moves, 20–30 min. With a 百岳 A race and an
    ME session in the week (SP-114's 「ME 負重爬坡」, id "me": a heavy pack up the steepest slope, the
    pack emptied for the descent), no step-down here. The ME is added after the template
    (specific_phase.apply_me), so refresh() re-renders the strength sessions once it is in;
  * 恢復期 (after a race) — the old session (not in SP-119's table).

The moves are UA's (教練級); the sets and reps are 推估 (the app has no 1RM: 「留幾下」 instead).
The eccentric step-down is a weekday supplement — it never replaces real downhills (SP-99,
downhill-recovery.md). Shared by overview.week_plan and projection.week_sessions (`session()`).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from backend.i18n import N_, _

TRAIL_KINDS = ("race", "baiyue")          # planning.KINDS 越野賽 / 百岳
AA_WEEKS = 3                              # 推估: Bompa's 2–4 weeks for the experienced (p.177–179)
MINUTES = {"aa": 35, "max": 35, "maint": 25}
# the old session, as before SP-119 (marked only: the stored texts stay as they were)
DEFAULT = {"title": N_("肌力（下肢單腳＋核心）"), "minutes": 35, "detail": N_("膝主導＋臀中肌；安排在輕鬆日或跑完後")}
# an ME session (SP-114 / loaded-carry-training.md §5.3: id "me", 「肌耐力（ME）」), as overview.html reads it
_ME = re.compile(r"(?<![A-Za-z])ME(?![A-Za-z])")


def _g(x, k):
    return x.get(k) if isinstance(x, dict) else getattr(x, k, None)


def _d(x) -> Optional[dt.date]:
    if x in (None, ""):
        return None
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x)[:10])


def a_races(events, since: dt.date) -> list[dict]:
    """The A events ending on / after `since`, first one first: {id, name, kind, start, end} (ISO).
    Event objects or these dicts (week_plan's `a_races`, read back by the projection)."""
    out = []
    for e in events or ():
        if (_g(e, "priority") or "A") != "A":
            continue
        start = _d(_g(e, "start") or _g(e, "date"))
        if start is None:
            continue
        end = _d(_g(e, "end")) or start + dt.timedelta(days=max(1, int(_g(e, "days") or 1)) - 1)
        if end < since:
            continue
        out.append({"id": _g(e, "id"), "name": _g(e, "name"), "kind": _g(e, "kind") or "race",
                    "start": start.isoformat(), "end": end.isoformat()})
    return sorted(out, key=lambda r: r["start"])


def next_trail_a(events, monday: dt.date) -> Optional[dict]:
    """The next A race (a_races' first) when it is a 越野賽 / 百岳, else None."""
    rs = a_races(events, monday)
    return rs[0] if rs and rs[0]["kind"] in TRAIL_KINDS else None


def phase_at(phases, day: dt.date) -> Optional[dict]:
    """{kind, start, end} (dates) of the phase holding `day`; None outside every phase."""
    for p in phases or ():
        s, e = _d(_g(p, "start")), _d(_g(p, "end"))
        if s and e and s <= day <= e:
            return {"kind": _g(p, "kind"), "start": s, "end": e}
    return None


BLOCK_KINDS = ("transition", "rebuild", "base")    # the post-race run up to the next build (SP-98 回量期 too)


def block_start(phases, day: dt.date) -> Optional[dt.date]:
    """The first day of the run of 轉換期 / 回量期 / 基礎期 phases holding `day` (a 基礎期 right after
    a 轉換期 / 回量期 counts from it); None when `day` is in none of them."""
    p = phase_at(phases, day)
    if p is None or p["kind"] not in BLOCK_KINDS:
        return None
    start = p["start"]
    for _i in range(10):                 # walk back over touching 轉換期 / 回量期 / 基礎期 phases
        q = phase_at(phases, start - dt.timedelta(days=1))
        if q is None or q["kind"] not in BLOCK_KINDS:
            break
        start = q["start"]
    return start


def stage(kind: str, phases, monday: dt.date) -> Optional[str]:
    """aa | max | maint for the week's phase `kind` (week_plan's status.kind / the projection's
    phase_kind); None = the old session (恢復期, or no phase)."""
    if kind in ("transition", "rebuild"):
        return "aa"                      # 回量期 (SP-98): still anatomical adaptation before the 基礎期
    if kind == "base":
        b0 = block_start(phases, monday)
        if b0 is not None and (monday - b0).days < AA_WEEKS * 7:
            return "aa"
        return "max"
    if kind in ("specific", "taper", "event"):
        return "maint"
    return None


def has_me(sessions) -> bool:
    """The week has an ME session (SP-114): id 「me…」 or 「ME」 as a word in the title."""
    for s in sessions or ():
        sid, t = str(_g(s, "id") or _g(s, "gen_key") or ""), str(_g(s, "title") or "")
        if sid.startswith("me") or _ME.search(t):
            return True
    return False


def refresh(ss: list, ctx: Optional[dict]) -> None:
    """Re-render the week's not-done strength sessions (dicts, in place) from `ctx` and the sessions
    now in `ss` — after specific_phase.apply_me added SP-114's ME (has_me), which the template
    didn't see yet. TSS scales with the minutes."""
    if not ctx or not ctx.get("active"):
        return
    st = session(ctx, ss)
    for x in ss:
        if x.get("kind") != "strength" or x.get("done") or not str(x.get("id") or "").startswith("strength"):
            continue
        m0 = int(x.get("minutes") or 0)
        x.update(title=st["title"], detail=st["detail"], minutes=st["minutes"],
                 source=st["source"] or x.get("source"),
                 tss=round(float(x.get("tss") or 0.0) * (st["minutes"] / m0 if m0 else 1.0), 1))


def week_context(events, phases, monday: dt.date, kind: str) -> dict:
    """{active, stage, race_kind, race}: active when the next A race is a 越野賽 / 百岳 and the
    phase has a stage."""
    r = next_trail_a(events, monday)
    st = stage(kind, phases, monday) if r else None
    if not r or st is None:
        return {"active": False}
    return {"active": True, "stage": st, "race_kind": r["kind"], "race": r["name"]}


def session(ctx: Optional[dict], others=()) -> dict:
    """{title, minutes, detail, source} of the week's strength session; `others` = the week's
    sessions so far (an ME one drops the step-down from a 百岳 維持 session). The old session when
    `ctx` isn't active (`source` None = the caller's own, SRC_UA)."""
    if not ctx or not ctx.get("active"):
        return {**DEFAULT, "source": None}
    st = ctx["stage"]
    tail = _("安排在輕鬆日或跑完後；組數次數是推估")
    if st == "aa":
        return {"title": _("肌力（基礎循環 6 站）"), "minutes": MINUTES["aa"],
                "detail": _("6 站做 2–3 輪，每站 12–15 下、留 1–2 下不做到力竭，站間休 30–60 秒："
                            "離心下階（箱 15–30 cm，3 秒慢慢往下點地）、分腿蹲、高踏階（箱高約膝高，踩上去站穩再下）、"
                            "引體向上（做不到用彈力帶輔助，或跳上去 3–5 秒慢放）、農夫走路 30–40 m、"
                            "棒式 30 秒或懸吊抬腿") + "；" + tail,
                "source": _("Bompa & Buzzichelli 2015（解剖適應）；動作：Uphill Athlete（教練級）")}
    if st == "max":
        return {"title": _("肌力（分腿蹲＋離心下階＋引體向上）"), "minutes": MINUTES["max"],
                "detail": _("分腿蹲或後腳抬高蹲 3–4 組 × 3–6 下（留 2 下以上，組間休 2–3 分）；"
                            "離心下階 3 組 × 8–12 下／腳，背包 5 → 10 % 體重；引體向上 3 組（留 2 下）；"
                            "最後棒式 2 × 30 秒。離心下階是平日補強，不取代真的下坡") + "；" + tail,
                "source": _("Bompa & Buzzichelli 2015（最大肌力）；山本正嘉（深蹲和下山用的肌肉相近，教練級）；"
                            "動作：Uphill Athlete（教練級）")}
    if ctx.get("race_kind") == "baiyue" and has_me(others):
        return {"title": _("肌力維持（高踏階＋引體向上）"), "minutes": MINUTES["maint"],
                "detail": _("2–3 個動作各 2 組，不做到力竭：高踏階 8 下／腳（可背包）、引體向上（留 2 下）、"
                            "農夫走路 2 × 30–40 m。這週有 ME 負重爬坡，腿的負荷夠了，這堂不做離心下階") + "；" + tail,
                "source": _("Bompa & Buzzichelli 2015（維持：每週至少 1 次、2–4 個動作）；動作：Uphill Athlete（教練級）")}
    return {"title": _("肌力維持（高踏階＋離心下階＋引體向上）"), "minutes": MINUTES["maint"],
            "detail": _("2–4 個動作各 2 組，不做到力竭、不加新動作：高踏階 8 下／腳（可背包）、"
                        "離心下階 8–10 下／腳、引體向上（留 2 下）、棒式 30 秒") + "；" + tail,
            "source": _("Bompa & Buzzichelli 2015（維持：每週至少 1 次、2–4 個動作）；動作：Uphill Athlete（教練級）")}
