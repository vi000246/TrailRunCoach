"""
課表結構是唯一來源 (SP-437): a run session's time and TSS come from its structure (the steps the
watch gets: the stored `steps`, else workout_steps.derive from the text / variant), not from the
template's own numbers.

Why: the generator wrote `minutes` / `tss` from its templates (a fixed 60 min, minutes × TSS / h)
while the push and the editor derive the steps from the text — the two drifted apart (SP-411: a card
said 60 min, the watch got 85; the text intervals' 「最後一趟後也休息」 added 1–5 min). Every number
the plan uses (the week's hours, the planned TSS, the CTL projection that sizes the next weeks) now
follows the structure.

TSS (the owner 2026-10-13, Q1 A): the easy part of a structure — warm-up, cool-down, rests and the
steps at easy intensity (workout_steps.rate_easy) — counts at THIS runner's own TSS per hour of that
session (`easy_rate`: the generator's rate for its category, SP-302's genuinely-easy runs for the easy
runs); the steps with an intensity keep IF² × 100 per hour (workout_steps.totals). A session whose
structure is all easy keeps the TSS per hour the generator gave it, so its card TSS is minutes × that
rate exactly as before.

Applied to every run session the generator makes (overview.week_plan, projection.project_weeks, the
suggested tests, adapt's rewrites: plan_store.reconcile_with_adapt) — idempotent, so running it on its
own output changes nothing. Not to: strength, rest, race, notices, passive heat, a done session, a
structure with a lap-button or distance step (its time is the athlete's: the card keeps its own).
"""
from __future__ import annotations

import re
from typing import Optional

from backend.i18n import N_, _

EASY_KINDS = ("easy", "long", "hike", "mountain")
RUN_KINDS = EASY_KINDS + ("quality", "test")


def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _trail(s: dict) -> bool:
    t = s.get("title") or ""
    return s.get("terrain") in ("trail", "hike") or s.get("kind") in ("hike", "mountain") \
        or "山路" in t or "越野" in t


def kind_rate(s: dict, rates: Optional[dict]) -> float:
    """The runner's TSS per hour for an easy step of this session (the generator's category rates,
    overview._tss_per_hour / tss_per_category): the easy runs SP-302's easy rate (on trails its
    trail one), the long run the road / trail median, 越野跑 the hike one, a quality session's or a
    test's warm-up / cool-down / rests the easy-run rate."""
    from backend.engine.overview import TSS_PER_HOUR_DEFAULT, easy_est_rate
    r = {**TSS_PER_HOUR_DEFAULT, **{k: v for k, v in (rates or {}).items() if _f(v)}}
    easy = _f(r.get("easy")) or easy_est_rate()
    k = s.get("kind")
    if k == "easy":
        return float((_f(r.get("easy_trail")) or easy) if _trail(s) else easy)
    if k == "long":
        return float(r["trail"] if _trail(s) else r["road"])
    if k == "mountain":
        return float(r["trail"])
    if k == "hike":
        return float(r["hike"])
    return float(easy)


def _prefs(prefs):
    """課表偏好 as target_policy reads it (attributes): the plan inputs carry it as a dict."""
    if isinstance(prefs, dict):
        from types import SimpleNamespace
        return SimpleNamespace(**prefs)
    return prefs


def structure_of(s: dict, th: Optional[dict] = None) -> Optional[dict]:
    """The session's structure: its stored steps, else workout_steps.derive; None = none."""
    from backend.engine import workout_steps as WS
    st = s.get("steps")
    if isinstance(st, dict) and st.get("items"):
        return st
    try:
        return WS.derive(s, th)
    except Exception:                       # noqa: BLE001 — an unreadable text: the card keeps its numbers
        return None


def figures(s: dict, th: Optional[dict] = None, rates: Optional[dict] = None, prefs=None,
            steps: Optional[dict] = None) -> Optional[dict]:
    """{"sec", "minutes", "tss", "rate", "easy_s", "summary"} of a run session from its structure
    (`steps`, default structure_of), the TSS with the easy steps at `easy_rate`; None when it has no
    structure or one whose time isn't known (a lap-button / distance step)."""
    from backend.engine import workout_steps as WS
    if s.get("kind") not in RUN_KINDS:
        return None
    st = steps if steps is not None else structure_of(s, th)
    if not st or not st.get("items"):
        return None
    c = WS.session_ctx(s, th, _prefs(prefs))
    tot = WS.totals(st, c)
    if tot["open"] or tot["est"] or tot["sec"] <= 0:
        return None
    rate = easy_rate(s, rates, tot)
    minutes = max(1, int(round(tot["sec"] / 60.0)))
    if _all_easy_own(s, tot) and minutes == s.get("minutes"):
        tss = float(s["tss"])           # the generator's own rate, the same minutes: its TSS as it was
    else:
        tss = round(tot["tss_hard"] + tot["easy_s"] * rate / 3600.0, 1)
    return {"sec": tot["sec"], "minutes": minutes, "tss": tss,
            "rate": rate, "easy_s": tot["easy_s"], "summary": card_summary(st, c, s, th), "ctx": c, "steps": st}


def card_summary(st: dict, c, s: Optional[dict] = None, th: Optional[dict] = None) -> str:
    """The card's target text from the structure, in the cards' own words (it must not change what the target
    means): each main step's target as workout_steps.steps_text says it — power with its % CP, an HR band
    as plain bpm (the editor's 「% LTHR」 note is left out: the band may come from a 課表心率區間 / 儲備心率
    model, not from LTHR), 「（≤ 輕鬆跑上限）」 on the easy-run cap — plus on a power session with an HR cap
    (target_policy hr_cap) the HR its steps carry (an interval band's 「心率 a–b bpm」, an easy step's
    「心率 ≤ cap」). A walking session (target_policy.is_walk, SP-115) says its HR as the uphill cap the
    generator wrote (hr_profile.walk_cap_hr: 「心率 ≤ 爬坡上限 X bpm … 或 RPE ≤ 13，先到的為準」) — the watch
    gets that cap (hr_profile.walk_band) and the RPE rule in its step name."""
    from backend.engine import workout_steps as WS
    walk = False
    if s is not None:
        from backend.engine.target_policy import is_walk
        walk = is_walk(s)
    parts = _parts(st, c, skip_hr=walk)
    if walk:
        from backend.engine import hr_profile as HP
        parts.append(HP.walk_cap_hr(c.walk, c.aet, bool((th or {}).get("aet_measured"))))
        return " · ".join(parts)[:200]
    return _hr_part(st, c, " · ".join(parts)[:200])


# 「功率 X（sub）」 / 「功率 X」 … = f"{WS.TYPE_LABEL[type]} {text}（{sub}）" as msgids (SP-905)
_PART = {"power": (N_("功率 {text}（{sub}）"), N_("功率 {text}")), "hr": (N_("心率 {text}（{sub}）"), N_("心率 {text}")),
         "pace": (N_("配速 {text}（{sub}）"), N_("配速 {text}")), "auto": (N_("自動 {text}（{sub}）"), N_("自動 {text}"))}


def _parts(st: dict, c, skip_hr: bool = False) -> list[str]:
    """workout_steps.steps_text's parts without the HR band's 「% LTHR」 note (and without HR at all)."""
    from backend.engine import workout_steps as WS
    rows = WS.flat(st.get("items") or [])
    work = [r["st"] for r in rows if r["st"]["kind"] == "work"] or [r["st"] for r in rows][:1]
    out = []
    for x in work:
        r = WS.resolve(x, c)
        if r.type == "none" or (skip_hr and r.type == "hr"):
            continue
        sub = r.sub.split(" · ")[0] if r.sub else ""
        if r.type == "hr" and not sub.startswith("≤"):
            sub = ""
        # SP-905: one msgid per type (a session's 目標 is shown in the page's language, engine/stored_text)
        pair = _PART.get(r.type)
        rc = re.fullmatch(r"(爬升|下降) (\S+) m", sub) if r.type == "rpe" and sub else None
        if rc is not None:           # 「RPE 3–5（下降 100 m）」 (workout_steps.climb_text's zh-TW part)
            t = (_("RPE {text}（爬升 {m} m）", text=r.text, m=rc.group(2)) if rc.group(1) == "爬升" else
                 _("RPE {text}（下降 {m} m）", text=r.text, m=rc.group(2)))
        elif pair is None:
            t = f"{WS.TYPE_LABEL[r.type]} {r.text}" + (f"（{sub}）" if sub else "")
        else:
            t = _(pair[0], text=r.text, sub=sub) if sub else _(pair[1], text=r.text)
        if t not in out:
            out.append(t)
    return out[:3]


def _hr_part(st: dict, c, out: str) -> str:
    from backend.engine import workout_steps as WS
    if "心率" in out:
        return out
    part = ""
    for r in WS.flat(st.get("items") or []):
        x = r["st"]
        tg = x.get("target") or {}
        if x.get("kind") != "work":
            continue
        if tg.get("intent") == "band" and c.basis == "pace" and c.tpace and not c.hill:
            # SP-951: a flat interval by pace — the HR is its cap note (「心率 ≤ X bpm」); none on a short
            # Zone 5 rep (workout_steps.hr_cap_of)
            cap = WS.hr_cap_of(c, tg, WS._step_secs(x))
            if cap:
                from backend.engine.target_policy import cap_text
                part = cap_text(cap)
                break
            continue
        if tg.get("intent") == "band" and tg.get("hr"):
            part = _("心率 {lo}–{hi} bpm", lo=int(tg['hr'][0]), hi=int(tg['hr'][1]))
            break
        if tg.get("intent") == "easy" and c.hr_cap and c.basis == "power":
            e = WS.easy_hr(c)
            if e:
                part = _("心率 ≤ {hr} bpm", hr=int(e[2]))
                break
    if not part:
        return out
    return f"{out} · {part}" if out else part


def easy_rate(s: dict, rates: Optional[dict], tot: Optional[dict] = None) -> float:
    """The TSS per hour the easy steps count at: a session of the easy kinds whose structure is all
    easy keeps the generator's own rate (its tss ÷ minutes) — so its card TSS doesn't move — else
    kind_rate. `tot`: workout_steps.totals of its structure (all easy = easy_s == sec)."""
    if _all_easy_own(s, tot):
        return float(s["tss"]) / float(s["minutes"]) * 60.0
    return kind_rate(s, rates)


def _all_easy_own(s: dict, tot: Optional[dict]) -> bool:
    """An easy-kind session with its own minutes / TSS whose structure is all easy steps."""
    return bool(s.get("kind") in EASY_KINDS and tot is not None and tot.get("sec")
                and tot.get("easy_s") == tot["sec"] and _f(s.get("minutes")) and _f(s.get("tss")))


def _canon(steps: Optional[dict]):
    """A structure without its step ids (two derivations of the same session compare equal)."""
    def one(x):
        out = {k: v for k, v in x.items() if k != "id"}
        if x.get("kind") == "repeat":
            out["items"] = [one(y) for y in x.get("items") or []]
        return out
    return None if not steps else [one(x) for x in steps.get("items") or []]


def _pushed(s: dict, th: Optional[dict]):
    """What the watch gets for `s` (sync/coros_workouts.session_steps; None = not pushed)."""
    from backend.sync import coros_workouts as CW
    try:
        return CW.session_steps(s, CW.Thresholds.of(th or {}))
    except Exception:                       # noqa: BLE001 — Unsupported, an unreadable text: compared as such
        return None


def summary_target(s: dict, f: dict, th: Optional[dict] = None) -> Optional[str]:
    """The owner 2026-10-13 (Q2 B): a generated session's target text = its structure's summary
    (workout_steps.steps_text, what the editor writes for a saved structure) — only when nothing reads
    the old text into the structure or the push: with the summary as its target the session derives
    the same steps and pushes the same workout (the text paths read an interval's HR, a test's 「休 N 分」
    / 固定功率, the climb's note from it). None = keep the target as it is."""
    new = (f.get("summary") or "").strip()
    if not new or new == (s.get("target") or ""):
        return None
    if isinstance(s.get("steps"), dict) and s["steps"].get("items"):
        return new                          # a stored structure: the text is not read
    from backend.engine import workout_steps as WS
    t = {**s, "target": new}
    try:
        same = _canon(WS.derive(t, th)) == _canon(f["steps"])
    except Exception:                       # noqa: BLE001
        same = False
    if not same or _pushed(t, th) != _pushed(s, th):
        return None
    return new


def apply_one(s: dict, th: Optional[dict] = None, rates: Optional[dict] = None, prefs=None,
              target: bool = True) -> bool:
    """The structure's minutes, TSS and (`target`) target summary on one generated session dict (in
    place); True when it changed."""
    if s.get("done") or s.get("kind") not in RUN_KINDS:
        return False
    f = figures(s, th, rates, prefs)
    if f is None:
        return False
    old = (s.get("minutes"), s.get("tss"), s.get("target"))
    s["minutes"], s["tss"] = f["minutes"], f["tss"]
    if target:
        t = summary_target(s, f, th)
        if t is not None:
            s["target"] = t
    return old != (s["minutes"], s["tss"], s.get("target"))


def apply(sessions: list, th: Optional[dict] = None, rates: Optional[dict] = None, prefs=None,
          target: bool = True) -> int:
    """apply_one over a list of session dicts; the number changed."""
    return sum(1 for s in sessions if isinstance(s, dict) and apply_one(s, th, rates, prefs, target))


FIELDS = ("id", "kind", "title", "minutes", "tss", "target", "detail", "source", "done", "terrain", "protocol",
          "heat", "variant_key", "variant_reps", "variant_blocks", "variant_adj", "steps", "rung_key", "basis",
          "target_basis")


def apply_objs(sessions: list, th: Optional[dict] = None, rates: Optional[dict] = None, prefs=None,
               target: bool = True) -> int:
    """apply on overview.Session objects (week_plan)."""
    n = 0
    for s in sessions:
        d = {k: getattr(s, k, None) for k in FIELDS}
        if apply_one(d, th, rates, prefs, target):
            s.minutes, s.tss, s.target = d["minutes"], d["tss"], d["target"]
            n += 1
    return n


# ---------------------------------------------------------------------------
# the user's own structure (api/plan_sessions._with_steps, _steps_env)
# ---------------------------------------------------------------------------

def edit_rate(s: dict, stored: Optional[dict], th: Optional[dict] = None, rates: Optional[dict] = None,
              prefs=None) -> float:
    """The easy steps' TSS per hour while the user edits `s`'s structure (the editor's 預估 TSS, the
    saved TSS): the stored session's own rate when it is the same kind and its structure is all easy
    (an easy run keeps the generator's rate), else kind_rate."""
    if stored and stored.get("kind") == s.get("kind") and stored.get("kind") in EASY_KINDS:
        from backend.engine import workout_steps as WS
        st = structure_of(stored, th)
        if st and st.get("items"):
            try:
                tot = WS.totals(st, WS.session_ctx(stored, th, _prefs(prefs)))
            except Exception:               # noqa: BLE001 — an unreadable stored structure: the kind's rate
                tot = None
            if _all_easy_own(stored, tot):
                return float(stored["tss"]) / float(stored["minutes"]) * 60.0
    return kind_rate(s, rates)


EDITED = N_("（已改）")                 # a title whose 「N×M」 the new structure doesn't read as (SP-437 Q4)
_NXM = re.compile(r"(\d+)(\s*[×xX]\s*)(\d+)(\s*)(分|秒)")


def _reps(items: list) -> list[tuple[int, int]]:
    """(times, work seconds) of each repeat block holding one timed work step (and its rests)."""
    out = []
    for it in items or []:
        if it.get("kind") != "repeat":
            continue
        kids = it.get("items") or []
        work = [k for k in kids if k.get("kind") in ("work", "other")]
        if len(work) == 1 and all(k.get("kind") in ("work", "other", "rest") for k in kids) \
                and (work[0].get("dur") or {}).get("type") == "time" and work[0]["dur"].get("value"):
            out.append((int(it.get("times") or 0), int(work[0]["dur"]["value"])))
    return out


def retitle(title: str, steps: Optional[dict]) -> str:
    """SP-437 (the owner 2026-10-13, Q4 A): the 「N×M 分／秒」 in a title the user didn't touch follows the
    structure they saved — 「有氧間歇（巡航）3×12 分」 → 「…4×10 分」; a title the structure no longer reads
    as (a pyramid, two different blocks) gets EDITED once. A title with no 「N×M」 stays as it is."""
    title = title or ""
    toks = list(_NXM.finditer(title))
    if not toks or not steps:
        return title
    reps = _reps(steps.get("items") or [])
    out, lost = title, False
    for m in reversed(toks):
        unit = m.group(5)
        cand = sorted({(n, sec) for n, sec in reps
                       if (unit == "分" and sec >= 60 and sec % 60 == 0) or (unit == "秒" and sec < 60)})
        if len(cand) != 1:
            lost = True
            continue
        n, sec = cand[0]
        val = sec // 60 if unit == "分" else sec
        new = f"{n}{m.group(2)}{val}{m.group(4)}{unit}"
        out = out[:m.start()] + new + out[m.end():]
    mark = _(EDITED)
    if lost and not out.endswith(mark):
        out += mark
    return out
