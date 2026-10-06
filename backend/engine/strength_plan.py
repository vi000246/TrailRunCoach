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

肌力動作自己挑 (SP-191, engine/strength_moves.py; strength-session-design.md §3, §3.8): the stage
decides which movement types a session trains (TYPES) and the sets × reps (DOSE); the move of each
type is the athlete's 課表偏好 pick, else the default — the one named here before, so with nothing
picked the texts are unchanged. 高踏階 stays a fixed station (STEP). A picked move, or one changed
because its equipment is marked missing, is said at the end of the text and in the source. The old
session names no moves: it only gets that line (its types: OLD_TYPES).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from backend.engine import strength_moves as SM
from backend.i18n import N_, _

TRAIL_KINDS = ("race", "baiyue")          # planning.KINDS 越野賽 / 百岳
AA_WEEKS = 3                              # 推估: Bompa's 2–4 weeks for the experienced (p.177–179)
MINUTES = {"aa": 35, "max": 35, "maint": 25}
# the old session, as before SP-119 (marked only: the stored texts stay as they were)
DEFAULT = {"title": N_("肌力（下肢單腳＋核心）"), "minutes": 35, "detail": N_("膝主導＋臀中肌；安排在輕鬆日或跑完後")}
# an ME session (SP-114 / loaded-carry-training.md §5.3: id "me", 「肌耐力（ME）」), as overview.html reads it
_ME = re.compile(r"(?<![A-Za-z])ME(?![A-Za-z])")
# the movement types (strength_moves.TYPES) each session trains, in the order of its text — SP-119's
# table, not a preference; maint_me = a 百岳 維持 week with an ME session (no step-down)
TYPES = {"aa": ("ecc", "knee", "pull", "grip", "core"), "max": ("knee", "ecc", "pull", "core"),
         "maint": ("ecc", "pull", "core"), "maint_me": ("pull", "grip")}
OLD_TYPES = ("knee", "glute", "core")     # the old session: 「膝主導＋臀中肌」, 下肢單腳＋核心
# 高踏階 (the uphill single-leg push, UA): a fixed station of the circuit and the 維持 session
STEP = {"aa": N_("高踏階（箱高約膝高，踩上去站穩再下）"), "maint": N_("高踏階 8 下／腳（可背包）")}
# 推估: the sets × reps of a plain reps move, by stage and type ({name}); a hold, a carry or a loaded
# move has its own wording (strength_moves.Move.text). AA: the circuit says its 12–15 reps once.
DOSE = {"max": {"knee": N_("{name} 3–4 組 × 3–6 下（留 2 下以上，組間休 2–3 分）"), "ecc": N_("{name} 3 組 × 8–12 下／腳"),
                "pull": N_("{name} 3 組（留 2 下）"), "core": N_("{name} 2 組 × 8–12 下")},
        "maint": {"ecc": N_("{name} 8–10 下／腳"), "pull": N_("{name}（留 2 下）"), "core": N_("{name} 8–12 下")}}


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


def week_context(events, phases, monday: dt.date, kind: str, prefs=None) -> dict:
    """{active, stage, race_kind, race}: active when the next A race is a 越野賽 / 百岳 and the
    phase has a stage. `prefs` (課表偏好): its picked moves / missing equipment ride along as
    `choice` (strength_moves.choice, SP-191) — also when not active, for the old session."""
    r = next_trail_a(events, monday)
    st = stage(kind, phases, monday) if r else None
    ch = SM.choice(prefs)
    own = {"choice": ch} if ch else {}
    if not r or st is None:
        return {"active": False, **own}
    return {"active": True, "stage": st, "race_kind": r["kind"], "race": r["name"], **own}


def _said(picks: dict, st: str, typ: str) -> str:
    """One move as the stage's text says it: its own wording, else the stage's sets × reps."""
    m = picks[typ].move
    if m.text.get(st):
        return _(m.text[st])
    tpl = DOSE.get(st, {}).get(typ)
    return _(tpl, name=_(m.name)) if tpl else _(m.name)


def session(ctx: Optional[dict], others=()) -> dict:
    """{title, minutes, detail, source} of the week's strength session; `others` = the week's
    sessions so far (an ME one drops the step-down from a 百岳 維持 session). The old session when
    `ctx` isn't active (`source` None = the caller's own, SRC_UA). The moves: `ctx["choice"]`
    (SP-191), the defaults without it."""
    picks = SM.resolve((ctx or {}).get("choice"))
    if not ctx or not ctx.get("active"):
        own = SM.notes(picks, OLD_TYPES)
        if not own:
            return {**DEFAULT, "source": None}
        return {**DEFAULT, "detail": "；".join([_(DEFAULT["detail"])] + own), "source": None}
    st = ctx["stage"]
    name = {t: _(p.move.name) for t, p in picks.items()}
    if st == "maint" and ctx.get("race_kind") == "baiyue" and has_me(others):
        st = "maint_me"
    tail = SM.notes(picks, TYPES[st]) + [_("安排在輕鬆日或跑完後；組數次數是推估")]

    def out(title, body, source):
        return {"title": title, "minutes": MINUTES[st[:5]], "detail": "；".join([body] + tail),
                "source": "；".join(x for x in (source, SM.sources(picks, TYPES[st])) if x)}
    if st == "aa":
        stations = [_said(picks, st, "ecc"), _said(picks, st, "knee"), _(STEP["aa"]), _said(picks, st, "pull"),
                    _said(picks, st, "grip"), _said(picks, st, "core")]
        return out(_("肌力（基礎循環 6 站）"),
                   _("6 站做 2–3 輪，每站 12–15 下、留 1–2 下不做到力竭，站間休 30–60 秒：{moves}", moves=_("、").join(stations)),
                   _("Bompa & Buzzichelli 2015（解剖適應）；動作：Uphill Athlete（教練級）"))
    if st == "max":
        return out(_("肌力（{knee}＋{ecc}＋{pull}）", knee=name["knee"], ecc=name["ecc"], pull=name["pull"]),
                   _("{knee}；{ecc}；{pull}；最後{core}。{name}是平日補強，不取代真的下坡", knee=_said(picks, st, "knee"),
                     ecc=_said(picks, st, "ecc"), pull=_said(picks, st, "pull"), core=_said(picks, st, "core"),
                     name=name["ecc"]),
                   _("Bompa & Buzzichelli 2015（最大肌力）；山本正嘉（深蹲和下山用的肌肉相近，教練級）；"
                     "動作：Uphill Athlete（教練級）"))
    src = _("Bompa & Buzzichelli 2015（維持：每週至少 1 次、2–4 個動作）；動作：Uphill Athlete（教練級）")
    if st == "maint_me":
        stations = [_(STEP["maint"]), _said(picks, "maint", "pull"), _said(picks, "maint", "grip")]
        return out(_("肌力維持（高踏階＋{pull}）", pull=name["pull"]),
                   _("2–3 個動作各 2 組，不做到力竭：{moves}。這週有 ME 負重爬坡，腿的負荷夠了，這堂不做{name}",
                     moves=_("、").join(stations), name=name["ecc"]), src)
    stations = [_(STEP["maint"]), _said(picks, st, "ecc"), _said(picks, st, "pull"), _said(picks, st, "core")]
    return out(_("肌力維持（高踏階＋{ecc}＋{pull}）", ecc=name["ecc"], pull=name["pull"]),
               _("2–4 個動作各 2 組，不做到力竭、不加新動作：{moves}", moves=_("、").join(stations)), src)
