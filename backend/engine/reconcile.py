"""
Reconcile the stored (editable) plan with what actually happened and with a
freshly generated plan. Pure functions over plain dicts, so the rules are easy
to test; storage lives in backend/engine/plan_store.py.

A stored session:
  uid, week_start, gen_key (the generator's id: long / quality / easy1 …, None
  for custom), day, kind, title, minutes, target, detail, source, tss,
  origin ('auto' | 'custom'), edited (bool), provisional (bool),
  state ('active' | 'done' | 'missed' | 'deleted' | 'superseded'), done_by, note

Rules:
  1. active sessions on days up to today that match an activity (the
     generator's own match for auto sessions, else same day + same kind of
     activity) become done; the rest on past days become missed (kept as
     history, leave the active plan).
  2. per generated week, unedited auto sessions from today on are replaced by
     the regenerated ones (same gen_key → changed, gone → removed, new → added).
  3. edited and custom sessions are kept. An edited long / quality / test is
     superseded when the regenerated week is a rest week (recovery / taper /
     event) that no longer has it. Deleted auto sessions stay deleted
     (tombstones block their gen_key for that week).
  4. an auto session that lands on the same day as a kept edited / custom
     session is moved to a free day of that week, or dropped.
  5. unedited auto sessions after the horizon are removed.
  6. 不排課日期 (`blocked`, engine/blackouts.py): nothing active stays on a
     blocked day from today on. Unedited auto sessions follow the regenerated
     week (which never uses a blocked day) or move to the nearest free day
     (blackouts.move_to), else are dropped. Edited / custom sessions are a
     `conflict` change (「在不排課日期內」, with move_to): only the user's
     decision (`decisions[uid]` = move / delete) changes them; without one
     they are left as they are.
"""
from __future__ import annotations

import copy
import datetime as dt
import uuid
from typing import Callable, Optional

ENDURANCE = {"road", "trail", "hike", "bike"}
REST_MODES = {"recovery_week", "recovery", "taper", "event", "transition", "reentry"}
HARD_KINDS = {"long", "quality", "test"}
FIELDS = ("day", "kind", "title", "minutes", "target", "detail", "source", "tss", "terrain", "distance_km",
          "climb_m", "protocol",
          # the interval library variant (engine/interval_library.py): regenerated with the session
          "variant_key", "rung_key", "equiv", "swap", "swap_reason", "variant_reps", "variant_blocks", "variant_adj",
          # a structure (engine/workout_steps.py): only the user's edits store one, so an
          # unedited auto row gets the generator's (None = derived from the text)
          "steps")
SHOWN = ("day", "kind", "title", "minutes", "target", "detail", "terrain")


def new_uid() -> str:
    return uuid.uuid4().hex[:12]


def monday_of(day: str) -> str:
    d = dt.date.fromisoformat(day)
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def session_from_gen(g: dict, week_start: str, provisional: bool, uid: Optional[str] = None) -> dict:
    return {"uid": uid or new_uid(), "week_start": week_start, "gen_key": g["id"], "origin": "auto",
            "edited": False, "provisional": provisional, "state": "done" if g.get("done") else "active",
            "done_by": g.get("done_by"), "note": None,
            **{k: g.get(k) for k in FIELDS}}


# never hold a main day (heat_passive: engine/heat_plan.py; notice: the 課表待確認
# reminder pushed to the watch, engine/plan_auto.py — never done / missed / load)
SIDE_KINDS = ("strength", "heat_passive", "notice")
NOTICE = "notice"


def _matches(s: dict, a: dict) -> bool:
    if s["kind"] in ("heat_passive", NOTICE):
        return False                              # a bath / sauna has no FIT: the user ticks it
    if s["kind"] == "strength":
        return a.get("category") == "strength"
    return a.get("category") in ENDURANCE


def _change(action: str, s: dict, reason: str = "", before: Optional[dict] = None) -> dict:
    c = {"action": action, "uid": s["uid"], "day": s.get("day"), "title": s.get("title"),
         "kind": s.get("kind"), "minutes": s.get("minutes"), "origin": s.get("origin"),
         "edited": s.get("edited")}
    if reason:
        c["reason"] = reason
    if before:
        c["before"] = before
    return c


def reconcile(stored: list[dict], gen_weeks: list[dict], activities: list[dict], today: str,
              horizon_end: Optional[str] = None, uid_fn: Callable[[], str] = new_uid,
              covered: Optional[str] = None, blocked: Optional[dict] = None,
              allowed_days: Optional[list] = None,
              decisions: Optional[dict] = None) -> tuple[list[dict], list[dict]]:
    """(new stored list, changes). `gen_weeks`: [{start, mode, provisional, sessions}],
    the current week from week_plan() (with done flags) then projected weeks.
    `covered`: last day the synced data is known to cover; a past session is
    only called missed up to that day (a late sync must not turn a done
    workout into a missed one). Missed rows are re-checked every time.
    `blocked`: ISO day -> label of the 不排課日期; `allowed_days`: 7 bools
    (課表偏好 可練日) for moving off them; `decisions`: {uid: 'move' | 'delete'}
    for edited / custom sessions on a blocked day (rule 6)."""
    blocked = blocked or {}
    allowed = (lambda d: bool(allowed_days[d.weekday()])) if allowed_days else None
    out = [copy.deepcopy(s) for s in stored]
    changes: list[dict] = []
    used = {s["done_by"]["index"] for s in out
            if s["state"] == "done" and isinstance(s.get("done_by"), dict) and "index" in s["done_by"]}
    gen_done = {(w["start"], g["id"]): g for w in gen_weeks for g in w["sessions"] if g.get("done")}

    # ---- 1. done / missed ------------------------------------------------
    for s in sorted([s for s in out if s["state"] in ("active", "missed") and s["kind"] != NOTICE],
                    key=lambda s: s.get("day") or "9999"):
        g = gen_done.get((s["week_start"], s["gen_key"])) if s.get("gen_key") else None
        if g is not None and (not g.get("done_by") or g["done_by"].get("index") not in used):
            s["state"], s["done_by"] = "done", g.get("done_by")
            if g.get("day"):
                s["day"] = g["day"]
            if g.get("done_by"):
                used.add(g["done_by"].get("index"))
            changes.append(_change("done", s))
            continue
        if s.get("day") and s["day"] <= today:
            a = next((a for a in activities if a.get("date") == s["day"] and a.get("index") not in used
                      and _matches(s, a)), None)
            if a is not None:
                s["state"], s["done_by"] = "done", a
                used.add(a.get("index"))
                changes.append(_change("done", s))
                continue
        if (s["state"] == "active" and s.get("day") and s["day"] < today
                and (covered is None or s["day"] <= covered)):
            s["state"] = "missed"
            changes.append(_change("missed", s, "沒有對應的活動"))

    # ---- 2–4. regenerate each generated week -------------------------------
    for w in gen_weeks:
        ws, prov = w["start"], bool(w.get("provisional"))
        olds = [s for s in out if s["week_start"] == ws]
        gens = {g["id"]: g for g in w["sessions"]}
        consumed: set[str] = set()
        block = {s["gen_key"] for s in olds if s.get("gen_key") and
                 (s["state"] in ("deleted", "superseded", "done") or (s["state"] == "active" and s["edited"]))}
        for s in olds:
            if s["state"] != "active" or s["origin"] != "auto" or s["edited"]:
                continue
            if s.get("day") and s["day"] < today:
                continue
            g = gens.get(s["gen_key"])
            consumed.add(s["gen_key"])
            if g is None or g.get("done") or not g.get("day") or g["day"] < today:
                out.remove(s)
                changes.append(_change("removed", s, "重新計算後不需要"))
                continue
            before = {k: s.get(k) for k in SHOWN if s.get(k) != g.get(k)}
            for k in FIELDS:
                s[k] = g.get(k)
            s["provisional"] = prov
            if before:
                changes.append(_change("changed", s, before=before))
        for s in olds:
            if (s["state"] == "active" and s["origin"] == "auto" and s["edited"] and s.get("day")
                    and s["day"] >= today and s["kind"] in HARD_KINDS
                    and w.get("mode") in REST_MODES and s["gen_key"] not in gens):
                s["state"] = "superseded"
                changes.append(_change("removed", s, "這週改成恢復／減量，你改過的這堂課和它衝突"))
        for gid, g in gens.items():
            if gid in consumed or gid in block:
                continue
            if g.get("done"):
                if not any(s.get("gen_key") == gid for s in olds):
                    out.append(session_from_gen(g, ws, prov, uid_fn()))
                continue
            if not g.get("day") or g["day"] < today:
                continue
            s = session_from_gen(g, ws, prov, uid_fn())
            out.append(s)
            changes.append(_change("added", s))
        if blocked:
            _clear_blackouts(out, ws, today, changes, blocked, allowed, decisions or {})
        _resolve_collisions(out, ws, today, changes, blocked)

    # ---- 6. 不排課日期 in weeks that weren't regenerated ----------------------
    if blocked:
        done_ws = {w["start"] for w in gen_weeks}
        for ws in sorted({s["week_start"] for s in out if s["state"] == "active" and s.get("day")
                          and s["day"] >= today and s["day"] in blocked} - done_ws):
            _clear_blackouts(out, ws, today, changes, blocked, allowed, decisions or {})
            _resolve_collisions(out, ws, today, changes, blocked)
        _blackout_reasons(changes, blocked)

    # ---- 5. beyond the horizon -------------------------------------------
    if horizon_end:
        for s in list(out):
            if (s["state"] == "active" and s["origin"] == "auto" and not s["edited"]
                    and s.get("day") and s["day"] > horizon_end):
                out.remove(s)
                changes.append(_change("removed", s, "超出排程範圍"))
    return out, changes


def _why(label: str) -> str:
    return "在不排課日期內" + (f"（{label}）" if label else "")


def _md(day: str) -> str:
    d = dt.date.fromisoformat(day)
    return f"{d.month}/{d.day}"


def _prio(s: dict) -> int:
    """The long session first, then other long days (hike), hard, easy, strength."""
    if s.get("gen_key") == "long" or s["kind"] == "long":
        return 0
    return {"hike": 1, "quality": 2, "test": 2, "easy": 3, "strength": 4}.get(s["kind"], 3)


def _clear_blackouts(out: list[dict], ws: str, today: str, changes: list[dict], blocked: dict,
                     allowed, decisions: dict) -> None:
    """Rule 6 for one week: active sessions on a blocked day from today on."""
    from backend.engine.blackouts import move_to
    week = [s for s in out if s["week_start"] == ws and s.get("day")]
    hits = sorted([s for s in week if s["state"] == "active" and s["day"] >= today and s["day"] in blocked
                   and s["kind"] != NOTICE],
                  key=lambda s: (_prio(s), s["day"]))
    held: list[dict] = []                          # proposed moves still waiting for a decision
    for s in hits:
        label = blocked[s["day"]]
        why = _why(label)
        live = [x for x in week if x in out] + held
        tgt = move_to(s, live, blocked, today, allowed)
        prev = s["day"]
        if s["origin"] == "auto" and not s["edited"]:
            if tgt:
                s["day"] = tgt
                _merge_change(changes, s, {"day": prev}, f"{why}，移到 {_md(tgt)}")
            else:
                out.remove(s)
                _drop_change(changes, s, f"{why}，本週沒有空的日子")
            continue
        choice = decisions.get(s["uid"])
        conflict = {"label": label, "move_to": tgt, "day": prev}
        if choice == "delete":
            if s["origin"] == "auto":
                s["state"] = "deleted"             # tombstone: not regenerated
            else:
                out.remove(s)
            changes.append({**_change("removed", s, f"{why}，依你的選擇刪除"), "conflict": {**conflict, "choice": "delete"}})
        elif choice == "move" and tgt:
            s["day"] = tgt
            changes.append({**_change("changed", s, f"{why}，依你的選擇移到 {_md(tgt)}", {"day": prev}),
                            "conflict": {**conflict, "choice": "move"}})
        else:                                      # no decision: shown, left as it is
            if tgt:
                held.append({**s, "uid": s["uid"] + ":held", "day": tgt})
            changes.append({**_change("conflict", s, why + ("" if tgt else "，本週沒有空的日子可以移")),
                            "conflict": {**conflict, "choice": None}})


def _drop_change(changes: list[dict], s: dict, reason: str) -> None:
    for c in list(changes):
        if c["uid"] == s["uid"] and c["action"] == "added":
            changes.remove(c)                      # never existed: nothing to report
            return
    for c in changes:
        if c["uid"] == s["uid"] and c["action"] == "changed":
            c.update(action="removed", reason=reason)
            return
    changes.append(_change("removed", s, reason))


def _blackout_reasons(changes: list[dict], blocked: dict) -> None:
    """Regenerated sessions that left a blocked day say so."""
    for c in changes:
        if c.get("conflict") or c["action"] not in ("changed", "removed"):
            continue
        old = (c.get("before") or {}).get("day") or (c["day"] if c["action"] == "removed" else None)
        if old and old in blocked and "不排課" not in (c.get("reason") or ""):
            tail = f"，移到 {_md(c['day'])}" if c["action"] == "changed" and c.get("day") else "，本週排不下"
            c["reason"] = _why(blocked[old]) + tail


def _resolve_collisions(out: list[dict], ws: str, today: str, changes: list[dict],
                        blocked: Optional[dict] = None) -> None:
    week = [s for s in out if s["week_start"] == ws and s["state"] == "active" and s.get("day")]
    kept = [s for s in week if (s["origin"] == "custom" or s["edited"]) and s["kind"] not in SIDE_KINDS]
    kept_days = {s["day"] for s in kept}
    autos = [s for s in week if s["origin"] == "auto" and not s["edited"] and s["kind"] not in SIDE_KINDS
             and s["day"] >= today]
    main_days = {s["day"] for s in week if s["kind"] not in SIDE_KINDS}
    start = dt.date.fromisoformat(ws)
    for s in autos:
        if s["day"] not in kept_days:
            continue
        free = [d for d in ((start + dt.timedelta(days=i)).isoformat() for i in range(7))
                if d >= today and d not in main_days and d not in (blocked or {})]
        prev = s["day"]
        if free:
            s["day"] = free[0]
            main_days.add(free[0])
            _merge_change(changes, s, {"day": prev}, "和你安排的課同一天，移到空的一天")
        else:
            out.remove(s)
            changes.append(_change("removed", s, "和你安排的課同一天，本週沒有空的日子"))


def _merge_change(changes: list[dict], s: dict, before: dict, reason: str) -> None:
    for c in changes:
        if c["uid"] == s["uid"] and c["action"] in ("added", "changed"):
            c["day"] = s["day"]
            c.setdefault("before", {}).update({k: v for k, v in before.items() if c["action"] == "changed"})
            c["reason"] = reason
            return
    changes.append(_change("changed", s, reason, before))


def by_day(changes: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for c in changes:
        out.setdefault(c.get("day") or "—", []).append(c)
    return dict(sorted(out.items()))
