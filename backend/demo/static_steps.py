"""
The static demo's structure editor (static_shim.js `Steps`): the data its JS port of
POST /steps/check and /steps/derive needs, saved by export_static.py as
data/steps_ctx.json.

The schedule page's editor (static/workout_editor.js) asks the server for every
number it draws (POST /steps/check: resolved targets, the segment chart, totals /
TSS 估, issues, the watch preview) and for the starting structure (POST
/steps/derive). The static site has no server, so static_shim.js answers both with
a port of engine/workout_steps.py view() + engine/target_policy.py; this module
gives it
  - the athlete context api/plan_sessions._steps_env reads (thresholds with the
    threshold pace, the easy / trail EP speeds, 課表偏好's caps and 目標用, the
    power-source rule, the threshold-pace link);
  - the engine's tables (zones, rules, labels, the interval ladder's canonical
    sessions) so the port carries no copy of them;
  - `derive`: WS.derive() of every stored session and of the test templates /
    suggestions, keyed by derive_sig() (the fields derive reads): the port only
    derives the easy / long kinds itself; quality and test structures come from here.

backend/tests/test_static_steps.py runs the real endpoints and the JS port on the
same inputs and compares them.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

# the session fields WS.derive() reads (static_shim.js deriveSig is the same)
DERIVE_SIG_FIELDS = ("kind", "title", "minutes", "target", "detail", "source", "protocol", "variant_key",
                     "variant_reps", "variant_blocks", "variant_adj", "heat", "gen_key")


def _load_table() -> dict:
    from backend.engine import coros_tl as TL
    from backend.engine import rpe_load as RL
    from backend.engine import workout_steps as WS
    from backend.sync import workout_targets as WT
    return {"kinds": list(WS.LOAD_KINDS), "range": list(WS.LOAD_RANGE), "label": WS.LOAD_LABEL,
            "tl": {g: dict(TL.DEFAULTS[g][1]) for g in TL.GROUPS}, "err": TL.DEFAULT_ERR,
            # the editor's 時長類型 dropdown (the demo pushes nowhere: COROS, the default provider)
            "provider": WT.get(WT.DEFAULT).describe(),
            # 「負荷」 by RPE (SP-57, engine/rpe_load.py): the levels and each level's default TSS per hour
            # (IF² × 100, 推估 — the demo has no athlete fit)
            "rpe": {"levels": RL.levels(), "cr10": dict(RL.CR10), "label": dict(RL.LABEL),
                    "min_range": list(RL.MIN_RANGE), "tss_h": dict(RL.DEFAULT_TSS_H), "err": RL.DEFAULT_ERR,
                    "if_range": list(WS.RPE_IF_RANGE)}}


def _sig_val(k: str, v) -> str:
    if v is None or v is False:
        return ""
    if v is True:
        return "1"
    if k == "minutes":
        try:
            return str(int(v or 0))
        except (TypeError, ValueError):
            return "0"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(v)


def derive_sig(s: dict) -> str:
    return "\u001f".join(_sig_val(k, s.get(k)) for k in DERIVE_SIG_FIELDS)


def recs_terrain(kind: Optional[str], terrain: Optional[str]) -> str:
    """The terrain GET /steps/templates/recs ranks for (plan_sessions.steps_template_recs)."""
    return "trail" if kind == "hike" or terrain in ("trail", "hike") else "road"


def recs_key(kind: str, day: str, ter: str, minutes=None) -> str:
    """data.recs key (static_shim.js recsKey): minutes "" = the 推薦 for no particular length."""
    try:
        m = str(int(float(minutes))) if minutes else ""
    except (TypeError, ValueError):
        m = ""
    return f"{kind}|{day}|{ter}|{m}"


def _variant(v) -> dict:
    return {"key": v.key, "rung": v.rung, "cls": v.cls, "reps": v.reps, "work_s": v.work_s, "rest_s": v.rest_s,
            "rest_mode": v.rest_mode, "lo": v.lo, "hi": v.hi, "terrain": v.terrain, "canonical": v.canonical,
            "src_kind": v.src_kind, "pattern": list(v.pattern) if v.pattern else None, "sets": v.sets,
            "set_rest_s": v.set_rest_s, "listed_equiv": v.listed_equiv}


def constants() -> dict:
    """The engine tables the port uses (never edited by hand in the JS)."""
    from backend.engine import aet_test as AT
    from backend.engine import interval_library as IL
    from backend.engine import target_policy as TP
    from backend.engine import workout_steps as WS
    from backend.sync import coros_workouts as CW
    return {
        "ws": {"zones": {k: [list(r) for r in v] for k, v in WS.ZONES.items()},
               "hr_work": {k: list(v) for k, v in WS.HR_WORK.items()},
               "hr_class_band": {k: list(v) for k, v in WS.HR_CLASS_BAND.items()},
               "hr_p": [list(x) for x in WS._HR_P],
               "none_if": WS.NONE_IF, "easy_f": WS.EASY_F, "walk_kmh": WS.WALK_KMH,
               "dist_pace_default": WS.DIST_PACE_DEFAULT, "z5_frac": WS.Z5_FRAC,
               "kind_label": WS.KIND_LABEL, "type_label": WS.TYPE_LABEL, "no_tpace": WS.no_tpace_text(),
               "max_times": WS.MAX_TIMES, "max_depth": WS.MAX_DEPTH, "max_items": WS.MAX_ITEMS,
               "max_note": WS.MAX_NOTE, "coros_max_steps": WS.COROS_MAX_STEPS,
               "rules": {"z5_min_rep_s": WS.Z5_MIN_REP_S, "z3_min_rep_s": WS.Z3_MIN_REP_S,
                         "z5_max_rest_s": WS.Z5_MAX_REST_S, "coros_max_steps": WS.COROS_MAX_STEPS},
               "mp": {"pace": list(WS.MP_PACE), "hr": list(WS.MP_HR), "goal_band": WS.MP_GOAL_BAND,
                      "tail_s": WS.MP_TAIL_S},
               "step_name": {str(k): v for k, v in CW.STEP_NAME.items()},
               "rpe": {"min": WS.RPE_MIN, "max": WS.RPE_MAX, "word": {str(k): v for k, v in WS.RPE_WORD.items()},
                       "frac": {str(k): v for k, v in WS.RPE_FRAC.items()}, "easy_max": WS.RPE_EASY_MAX,
                       "hard_min": WS.RPE_HARD_MIN, "max_climb": WS.MAX_CLIMB_M, "limit": WS.RPE_LIMIT},
               # 「負荷」 steps (SP-38): the default TSS → COROS TL conversion (engine/coros_tl.py, 推估)
               "load": _load_table()},
        "il": {"class_range": {k: list(v) for k, v in IL.CLASS_RANGE.items()}, "rung_name": IL.RUNG_NAME,
               "canonical": {r: _variant(IL.canonical(r)) for r in IL.LIBRARY if IL.canonical(r)},
               "variant_rung": {k: v.rung for k, v in IL.ALL.items()},
               "tiz_tol": IL.TIZ_TOL, "wprime_ratio": list(IL.WPRIME_RATIO), "z3_ratio": list(IL.Z3_RATIO)},
        "tp": {"label": TP.LABEL, "src": TP.SRC, "auto": {k: list(v) for k, v in TP.AUTO.items()}},
        "aet": {"title_re": AT.TITLE_RE.pattern, "protocol": AT.PROTOCOL},
    }


def build(th: dict, prefs, speeds: dict, auto_power_ok: bool, tpace_link: Optional[str],
          sessions: Iterable[dict] = ()) -> dict:
    """data/steps_ctx.json. `th`: the thresholds _steps_env uses (with tpace, s/km);
    `prefs`: plan_prefs.Prefs; `sessions`: the ones whose derive() the port can't do
    itself (stored sessions, test templates / suggestions as the dialog sends them)."""
    from backend.api import plan_sessions as PSAPI
    from backend.engine import target_policy as TP
    from backend.engine import workout_steps as WS
    derive: dict = {}
    for s0 in sessions:
        try:
            s = PSAPI._session_of({}, s0)
            k = derive_sig(s)
            if k not in derive:
                derive[k] = WS.derive(s, th)
        except Exception:                  # noqa: BLE001 — a session the engine can't read: the port says so
            continue
    p = prefs
    return {
        "v": 1,
        "th": {k: th.get(k) for k in ("cp", "lthr", "aet", "tpace", "cp_source", "lthr_source", "aet_source", "thr_warn",
                                       "aet_measured")},
        "tpace_link": tpace_link,
        "speeds": {k: (speeds or {}).get(k) for k in ("v_easy", "v_easy_src", "ep_kmh")},
        "prefs": {"active": bool(p is not None and p.active),
                  "cap_weekday": getattr(p, "cap_weekday", None), "long_cap": getattr(p, "long_cap", None),
                  "cap_mode": getattr(p, "cap_mode", "soft"), "basis": TP.pref_basis(p)},
        "auto_power_ok": bool(auto_power_ok),
        **constants(),
        "derive": derive,
    }


def dialog_tests(calendar: dict, suggestions: Optional[dict]) -> list[dict]:
    """The test sessions the 課表 dialog sends before anything is stored: 新增 › 測試
    (calendar test_templates) and 排入測試 (test-suggestions; static_shim.js opScheduleTest)."""
    out = []
    tt = (calendar or {}).get("test_templates") or {}
    for k, rows in tt.items():
        for t in rows or []:
            if t.get("none"):
                continue
            proto = (t.get("protocol_stored") or "aet") if k == "aet" else t.get("protocol")
            out.append({"kind": "test", "title": t.get("title"), "minutes": t.get("minutes"),
                        "target": t.get("target") or "", "detail": t.get("detail") or "", "protocol": proto})
    sg = suggestions or {}
    for x in sg.get("suggestions") or sg.get("tests") or sg.get("items") or []:
        out.append({"kind": "test", "title": x.get("title"), "minutes": x.get("minutes"), "target": x.get("target") or "",
                    "detail": x.get("detail") or "", "protocol": x.get("protocol") or x.get("kind")})
    return out


async def collect(sessions: Iterable[dict]) -> dict:
    """build() with the running app's context (the same calls as plan_sessions._steps_env)."""
    from starlette.concurrency import run_in_threadpool
    from backend.api import plan_sessions as PSAPI
    from backend.engine import plan_prefs as PP
    from backend.engine import target_policy as TP
    inp = await PSAPI._inputs()
    th = dict(inp.get("thresholds") or {})
    th["tpace"] = await run_in_threadpool(PSAPI._tpace)
    speeds = dict(await run_in_threadpool(PSAPI._speeds))
    prefs = await run_in_threadpool(PP.load)
    ok = await run_in_threadpool(TP._auto_power_ok)
    return build(th, prefs, speeds, ok, PSAPI.tpace_link(), list(sessions))
