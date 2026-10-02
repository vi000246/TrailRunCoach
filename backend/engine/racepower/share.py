"""
Read-only share links for the 賽事功率 result (「分享」).

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

from backend.engine.racepower import weather as WX

SHARES_DIR = WX.HOME / "racepower_shares"
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
FUEL_DROP = ("body", "body_src", "crosscheck")
WEIGHT_LOADING = ("g_day", "breakfast_g", "caffeine_mg")


class ShareError(ValueError):
    pass


def new_id() -> str:
    return secrets.token_urlsafe(16)


def _now() -> dt.datetime:
    return dt.datetime.now().replace(microsecond=0)


def snapshot(plan: dict, *, title: str, include_weight: bool = False, expires_days: Optional[int] = None,
             request: Optional[dict] = None, now: Optional[dt.datetime] = None) -> dict:
    """The frozen, whitelisted view of a /plan result."""
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
    fuel["warnings"] = [w for w in fuel.get("warnings") or [] if "基礎代謝" not in w]
    eff = plan.get("effort") or {}
    prof = plan.get("profile") or None
    stops = [{k: st.get(k) for k in ("km", "type", "name", "minutes")} for st in req.get("stops") or []]
    env_to = ((plan.get("env") or {}).get("to") or {})
    return {
        "v": VERSION, "title": (title or "").strip()[:80] or "賽事計畫",
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
        "days": [{k: d.get(k) for k in ("day", "km", "gain_m", "loss_m", "moving_h", "clock_h")} for d in plan.get("days") or []],
        "profile": {"km": prof["km"], "z": prof["z"]} if prof and prof.get("km") else None,
        "fuel": fuel,
    }


def _path(sid: str, root: Optional[Path] = None) -> Path:
    if not ID_RE.match(sid or ""):
        raise ShareError("分享連結格式不對")
    return (root or SHARES_DIR) / f"{sid}.json"


def save(snap: dict, root: Optional[Path] = None) -> str:
    root = root or SHARES_DIR
    root.mkdir(parents=True, exist_ok=True)
    if len(list(root.glob("*.json"))) >= MAX_SHARES:
        raise ShareError(f"分享最多 {MAX_SHARES} 個：先刪掉舊的")
    sid = new_id()
    _path(sid, root).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    return sid


def expired(snap: dict, now: Optional[dt.datetime] = None) -> bool:
    e = snap.get("expires")
    return bool(e) and dt.datetime.fromisoformat(e) <= (now or _now())


def load(sid: str, root: Optional[Path] = None) -> Optional[dict]:
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
    root = root or SHARES_DIR
    out = []
    for p in root.glob("*.json") if root.exists() else []:
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sm = s.get("summary") or {}
        out.append({"id": p.stem, "title": s.get("title"), "type": s.get("type"), "created": s.get("created"),
                    "expires": s.get("expires"), "expired": expired(s, now), "km": sm.get("km"),
                    "date": (s.get("inputs") or {}).get("date"), "weight": s.get("weight") is not None})
    return sorted(out, key=lambda r: r["created"] or "", reverse=True)
