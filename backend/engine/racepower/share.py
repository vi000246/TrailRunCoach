"""
Read-only share links for the 賽事計算機 result (「分享」).

A share is a frozen snapshot of one /plan result: the course profile, the
predicted splits, the per-segment targets and the 補給 card — only the
fields the result page shows, through whitelists. It is never recomputed
and holds no reference to the athlete's data, settings or files.

Privacy: body weight and everything that would reveal it (W/kg, the
pre-race load in grams, the breakfast / caffeine amounts) is dropped
unless the athlete ticks 「包含體重」; height / age / sex, the REE, the
model inputs and their sources and the plan's warnings (they quote the
athlete's own records) are always dropped.

Storage: one JSON file per share in ~/.wko5coach/racepower_shares/<id>.json,
id = secrets.token_urlsafe(16) (128 bits, unguessable).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import secrets
from pathlib import Path
from typing import Optional

from backend.engine import hints as H
from backend.engine.racepower import weather as WX
from backend.i18n import N_

SHARES_DIR = None      # fixed folder (tests); None = the tenant's (private) racepower_shares/


def _shares_dir() -> Path:
    from backend import tenancy
    return Path(SHARES_DIR) if SHARES_DIR is not None else tenancy.private_path("racepower_shares")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
EXPIRY_DAYS = (None, 7, 30, 90)
MAX_SHARES = 200
VERSION = 1

SUMMARY_KEYS = ("time_s", "clock_s", "capacity_time_s", "power", "pct_cp", "pace_s_per_km", "km", "gain_m", "loss_m",
                "finish_eta", "stops_s", "mode", "total_method", "badge", "days", "hr_cap", "M", "speed_factor",
                "ep_per_h", "trip_kind", "category", "time_total_s", "time_total_range_s")
SEG_KEYS = ("i", "day", "start_km", "end_km", "dist_m", "gain_m", "loss_m", "grade", "cls", "cls_label", "walk",
            "power", "pct_cp", "zone", "pace_s_per_km", "speed_kmh", "vert_m_per_h", "t", "cum_s", "eta", "temp_c",
            "kcal", "cum_kcal", "cho_g", "water_ml", "na_mg", "fuel_action", "target", "badge")
# SP-784: the rows of the calculator's 分段目標 (seg_targets.chart_rows) the share page draws with the same
# component (static/seg_profile.js). Left out: hr_cap_src (the model's sources), memory (位置記憶: the dates and
# names of the athlete's own runs), the watch export's z_mean / tech_f / gap pace
ROW_KEYS = ("n", "mark", "i", "day", "start_km", "end_km", "dist_m", "gain_m", "loss_m", "grade", "kind", "label", "basis",
            "gait", "pace_s_per_km", "power", "power_band", "power_ref", "hr_ref", "hr_cap", "walk", "t", "cum_s", "eta",
            "temp_c", "fuel", "badge", "pole_hint", "night")
FUEL_DROP = ("body", "body_src", "crosscheck")
WEIGHT_LOADING = ("g_day", "breakfast_g", "caffeine_mg")
DEFAULT_TITLE = N_("賽事計畫")     # a share with no title; stored as the source text, shown through localized()
REE_HINT = "RC28"       # fuel.py's 「基礎代謝缺…」 warning (it quotes the athlete's height / age / sex): never shared


class ShareError(ValueError):
    pass


HEALTH_KEY = re.compile(r"injur|pain|傷病|疼痛", re.I)    # 傷病紀錄 (engine/injuries.py) never leaves the machine


def scrub_health(v):
    """Drop every injury / pain key at any depth — a second fence behind the
    whitelists, so a later change to them can't leak the injury log."""
    if isinstance(v, dict):
        return {k: scrub_health(x) for k, x in v.items() if not (isinstance(k, str) and HEALTH_KEY.search(k))}
    if isinstance(v, list):
        return [scrub_health(x) for x in v]
    return v


def new_id() -> str:
    return secrets.token_urlsafe(16)


def _now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def snapshot(plan: dict, *, title: str, include_weight: bool = False, expires_days: Optional[int] = None,
             request: Optional[dict] = None, now: Optional[dt.datetime] = None,
             cairn_curve: Optional[dict] = None) -> dict:
    """The frozen, whitelisted view of a /plan result. `cairn_curve` (SP-653, cairn_curve.py): the
    時間曲線 for 疊石 Cairn, only when the athlete ticked it — a share has none by default."""
    if expires_days not in EXPIRY_DAYS:
        raise ShareError(f"有效期限只能是 {EXPIRY_DAYS}")
    now = now or _now()
    req = request or {}
    s = plan.get("summary") or {}
    summary = {k: s.get(k) for k in SUMMARY_KEYS if s.get(k) is not None}
    weight = ((plan.get("used") or {}).get("weight") or {}).get("value")
    if include_weight and weight:
        summary["w_per_kg"] = s.get("w_per_kg")
    segs = [{k: x.get(k) for k in SEG_KEYS if x.get(k) is not None} for x in plan.get("segments") or []]
    fuel = {k: v for k, v in (plan.get("fuel") or {}).items() if k not in FUEL_DROP}
    if fuel.get("loading"):
        ld = dict(fuel["loading"])
        if not include_weight:
            for k in WEIGHT_LOADING:
                ld.pop(k, None)
        fuel["loading"] = ld
    if fuel.get("daily"):
        fuel["daily"] = [{k: v for k, v in d.items() if k != "ree"} for d in fuel["daily"]]
    # a hint object (SP-482) or an old plain string: the id or the text decides
    fuel["warnings"] = [w for w in fuel.get("warnings") or []
                        if "基礎代謝" not in H.text(w)
                        and REE_HINT not in ((w.get("id") if isinstance(w, dict) else None), getattr(w, "hint_id", None))]
    eff = plan.get("effort") or {}
    prof = plan.get("profile") or None
    stops = [{k: st.get(k) for k in ("km", "type", "name", "minutes")} for st in req.get("stops") or []]
    env_to = ((plan.get("env") or {}).get("to") or {})
    out = {
        "v": VERSION, "title": (title or "").strip()[:80] or DEFAULT_TITLE,
        "created": now.isoformat(), "expires": (now + dt.timedelta(days=expires_days)).isoformat() if expires_days else None,
        "type": plan.get("type"),
        "inputs": {"date": req.get("date"), "start_time": req.get("start_time"), "stops": stops,
                   "temp_c": env_to.get("temp_c"), "rh_pct": env_to.get("rh_pct"), "altitude_m": env_to.get("altitude_m"),
                   "course_source": plan.get("course_source")},
        "summary": summary,
        "effort": {k: eff.get(k) for k in ("label", "key", "f", "r") if eff.get(k) is not None},
        "cp": ((plan.get("used") or {}).get("cp") or {}).get("value") if plan.get("type") != "baiyue" else None,
        "weight": weight if include_weight else None,
        "segments": segs, "seg_targets": plan.get("seg_targets"),
        "chart_rows": [{k: r.get(k) for k in ROW_KEYS if r.get(k) is not None} for r in plan.get("chart_rows") or []] or None,
        "days": [{k: d.get(k) for k in ("day", "km", "gain_m", "loss_m", "moving_h", "clock_h")} for d in plan.get("days") or []],
        "profile": {"km": prof["km"], "z": prof["z"]} if prof and prof.get("km") else None,
        "fuel": fuel,
    }
    if cairn_curve:
        out["cairn_curve"] = cairn_curve
    # SP-728: the 攀爬段 on the elevation chart and the cross-validation's shares (not how many runs)
    from backend.engine.racepower import climb as CL
    climb = CL.summary(plan.get("climb"))
    if climb:
        out["climb"] = climb
    return scrub_health(out)


def with_rows(snap: dict) -> dict:
    """SP-784: the snapshot with its 分段目標 rows — a share made before SP-784 (and the built-in examples) has
    none: they are derived from its own segments the way the calculator does (seg_targets.chart_rows), the
    race's heart-rate cap taken from the snapshot's targets (or its summary). Nothing of the athlete's is read;
    the stored file is left as it is."""
    if snap.get("chart_rows") or not snap.get("segments"):
        return snap
    from backend.engine.racepower import seg_targets as ST
    caps = [t.get("hr_cap") for t in snap.get("seg_targets") or [] if t.get("hr_cap")]
    s = snap.get("summary") or {}
    cap = caps[0] if caps else s.get("hr_cap")
    plan = {"type": snap.get("type"), "segments": snap["segments"], "fuel": snap.get("fuel") or {},
            "used": {"cp": {"value": snap.get("cp")}}, "summary": {"time_s": s.get("time_s"), "hr_cap": cap}}
    rows = ST.chart_rows(plan, aet=cap, lthr=cap)
    return {**snap, "chart_rows": [{k: r.get(k) for k in ROW_KEYS if r.get(k) is not None} for r in rows] or None}


def localized(snap: dict, loc: Optional[str] = None) -> dict:
    """SP-831: a snapshot frozen in Chinese (the built-in examples) in the reader's language (default: the
    request's) — a copy; the stored file is left as it is, the numbers are not touched. What has a key is
    rebuilt from it (the effort word from effort.key, the 補給 card's carbohydrate line from fuel.category);
    the other texts the share page shows (the title of an example, the 推估 badge, 賽前補碳, the fuel
    schedule's actions, the warnings, the segment targets' names, 補給 and badge) are read back against the
    translation catalog (i18n/reverse.py). A text no msgid matches stays as it is — the athlete's own
    station names, a word renamed since the snapshot was taken. Run before with_rows (the rows it derives
    take the segments' names and the schedule's actions). Chinese asked for: the snapshot itself."""
    from backend import i18n
    from backend.engine.racepower import difficulty as DF
    from backend.engine.racepower import fuel as FU
    from backend.i18n.reverse import from_source
    loc = loc or i18n.current_locale()
    if loc == i18n.DEFAULT_LOCALE or not isinstance(snap, dict):
        return snap
    out = json.loads(json.dumps(snap, ensure_ascii=False))     # a deep copy: the caller's snapshot stays as it is

    def tr(x):
        t = from_source(x, loc)
        return x if t is None else t

    def each(d, *keys):
        if isinstance(d, dict):
            for k in keys:
                if isinstance(d.get(k), str):
                    d[k] = tr(d[k])

    if out.get("title") in EXAMPLE_TITLES + (DEFAULT_TITLE,):     # a title the athlete typed stays as typed
        out["title"] = i18n.translate(out["title"], loc)
    each(out.get("summary"), "badge")
    eff = out.get("effort")
    if isinstance(eff, dict):
        name = {k: n for k, n, _lo in DF.LABELS}.get(eff.get("key"))
        if name:
            eff["label"] = i18n.translate(name, loc)
        else:
            each(eff, "label")
    F = out.get("fuel")
    if isinstance(F, dict):
        rule = FU.CHO_RULES.get(F.get("category"))
        for d in (F, F.get("cho")):
            if isinstance(d, dict) and d.get("label"):
                d["label"] = i18n.translate(rule["label"], loc) if rule else tr(d["label"])
        each(F.get("loading"), "label", "when", "amount", "note")
        for e in F.get("schedule") or []:
            each(e, "action")
        F["warnings"] = [({**w, "text": tr(w.get("text"))} if isinstance(w, dict) else tr(w))
                         for w in F.get("warnings") or []]
    for s in out.get("segments") or []:
        each(s, "cls_label", "fuel_action", "badge")
        each(s.get("target"), "label", "badge")
    for t in out.get("seg_targets") or []:
        each(t, "label", "badge", "fuel_action")
    for r in out.get("chart_rows") or []:
        each(r, "label", "fuel", "badge")
    return out


def _path(sid: str, root: Optional[Path] = None) -> Path:
    if not ID_RE.match(sid or ""):
        raise ShareError("分享連結格式不對")
    return (root or _shares_dir()) / f"{sid}.json"


def save(snap: dict, root: Optional[Path] = None) -> str:
    root = root or _shares_dir()
    root.mkdir(parents=True, exist_ok=True)
    if len(list(root.glob("*.json"))) >= MAX_SHARES:
        raise ShareError(f"分享最多 {MAX_SHARES} 個：先刪掉舊的")
    sid = new_id()
    _path(sid, root).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return sid


def expired(snap: dict, now: Optional[dt.datetime] = None) -> bool:
    e = snap.get("expires")
    return bool(e) and dt.datetime.fromisoformat(e) <= (now or _now())


# the 介紹頁's 「先試算一場比賽」 (SP-622): a built-in share of the demo's fictional runner and 50K, a file in
# the repo (backend/scripts/intro_example.py writes it) — never in a tenant, never anyone's row in accounts.db.
# Its id has a shape new_id() never makes (17 characters; token_urlsafe(16) gives 22).
EXAMPLE_ID = "example-trail-50k"
_DEMO = Path(__file__).resolve().parents[2] / "demo"
EXAMPLE_FILE = _DEMO / "example_share.json"
# SP-637: one example per kind the 介紹頁 switches between (越野／百岳／路跑), the same way: a repo file each,
# ids of 17 and 19 characters (never new_id()'s 22)
EXAMPLES = {
    EXAMPLE_ID: EXAMPLE_FILE,
    "example-baiyue-3day": _DEMO / "example_share_baiyue.json",
    "example-road-half": _DEMO / "example_share_road.json",
}
EXAMPLE_KINDS = {EXAMPLE_ID: "trail", "example-baiyue-3day": "baiyue", "example-road-half": "road"}
# the examples' titles (backend/scripts/intro_example.KINDS), msgids: localized() shows them in the reader's language
EXAMPLE_TITLES = (N_("示範山徑越野 50K（虛構跑者）"), N_("示範百岳 3 日縱走（虛構跑者）"), N_("示範城市半程馬拉松（虛構跑者）"))


def is_example(sid: str) -> bool:
    return sid in EXAMPLES


def example_id(kind: str) -> str:
    """The built-in example of a kind (trail | baiyue | road); KeyError for any other."""
    return {v: k for k, v in EXAMPLE_KINDS.items()}[kind]


def example(sid: str = EXAMPLE_ID) -> Optional[dict]:
    """A built-in example snapshot; None when the file is missing (a share page's 404)."""
    try:
        return json.loads(EXAMPLES[sid].read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError):
        return None


def load(sid: str, root: Optional[Path] = None) -> Optional[dict]:
    if sid in EXAMPLES:
        return example(sid)
    p = _path(sid, root)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def delete(sid: str, root: Optional[Path] = None) -> bool:
    p = _path(sid, root)
    if not p.exists():
        return False
    p.unlink()
    return True


def listing(root: Optional[Path] = None, now: Optional[dt.datetime] = None) -> list[dict]:
    root = root or _shares_dir()
    out = []
    for p in root.glob("*.json") if root.exists() else []:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sm = s.get("summary") or {}
        t = s.get("title")
        if t == DEFAULT_TITLE:                          # SP-831: the untitled share's word in the page's language
            from backend.i18n import _
            t = _(DEFAULT_TITLE)
        out.append({"id": p.stem, "title": t, "type": s.get("type"), "created": s.get("created"),
                    "expires": s.get("expires"), "expired": expired(s, now), "km": sm.get("km"),
                    "date": (s.get("inputs") or {}).get("date"), "weight": s.get("weight") is not None})
    return sorted(out, key=lambda r: r["created"] or "", reverse=True)
