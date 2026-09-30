"""
Achievements API — the trail-run / mountain record, filterable, and exportable
as plain text to paste into a hiking-group sign-up.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from backend.engine.achievements import (
    CLASS_BAIYUE, Annotations, build_achievements, load_peaks, to_row,
)
from backend.engine.wko5expr.config import EngineConfig
from backend.engine.wko5expr.dataset import Dataset
from backend.settings.paths import athlete_dir

ATHLETE_DIR = athlete_dir()

router = APIRouter(prefix="/api/v1/achievements", tags=["achievements"])

SORT_KEYS = {
    "date": lambda r: r["start"],
    "distance": lambda r: r["distance_km"] or 0,
    "climbing": lambda r: r["climbing_m"] or 0,
    "top": lambda r: r["top_m"] or 0,
    "moving": lambda r: r["moving_s"] or 0,
    "efd": lambda r: r["efd_km"] or 0,
    "vam": lambda r: (r["best_climb"] or {}).get("vam_m_per_h") or 0,
}


@lru_cache(maxsize=1)
def _dataset() -> Dataset:
    # distance / climbing / time come from WKO5's index and don't depend on
    # the TSS policy, so the parity config is fine here.
    return Dataset(ATHLETE_DIR, config=EngineConfig())


def _rows(include_hidden: bool = False) -> list[dict]:
    ann = Annotations()
    rows = [to_row(r, ann) for r in build_achievements(_dataset())]
    return rows if include_hidden else [r for r in rows if not r["hidden"]]


def _matches(r: dict, q: str) -> bool:
    hay = " ".join(filter(None, [r["name"], r["auto_name"], r.get("route_name"), r.get("note"),
                                 *(p["name"] for p in r["peaks"])])).lower()
    return all(tok in hay for tok in q.lower().split())


def _filter(rows, kind, mclass, q, min_km, max_km, min_climb, max_climb, min_top,
            date_from, date_to):
    classes = {c.strip() for c in mclass.split(",")} if mclass else None
    out = []
    for r in rows:
        if kind and r["kind"] != kind:
            continue
        if classes and r["mclass"] not in classes:
            continue
        if q and not _matches(r, q):
            continue
        d, c, top = r["distance_km"] or 0, r["climbing_m"] or 0, r["top_m"] or 0
        if (min_km is not None and d < min_km) or (max_km is not None and d > max_km):
            continue
        if (min_climb is not None and c < min_climb) or (max_climb is not None and c > max_climb):
            continue
        if min_top is not None and top < min_top:
            continue
        day = r["start"][:10]
        if (date_from and day < date_from) or (date_to and day > date_to):
            continue
        out.append(r)
    return out


@router.get("")
def list_achievements(kind: Optional[str] = None, mclass: Optional[str] = None,
                      q: Optional[str] = None, min_km: Optional[float] = None,
                      max_km: Optional[float] = None, min_climb: Optional[float] = None,
                      max_climb: Optional[float] = None, min_top: Optional[float] = None,
                      date_from: Optional[str] = None, date_to: Optional[str] = None,
                      sort: str = "date", desc: bool = True, limit: Optional[int] = None,
                      include_hidden: bool = False):
    rows = _filter(_rows(include_hidden), kind, mclass, q, min_km, max_km, min_climb,
                   max_climb, min_top, date_from, date_to)
    key = SORT_KEYS.get(sort)
    if key is None:
        raise HTTPException(400, f"sort must be one of {sorted(SORT_KEYS)}")
    rows.sort(key=key, reverse=desc)
    total = len(rows)
    if limit:
        rows = rows[:limit]
    return {"total": total, "rows": rows, "summary": _summary(rows if not limit else
                                                           _filter(_rows(include_hidden), kind, mclass, q, min_km, max_km,
                                                                   min_climb, max_climb, min_top, date_from, date_to)),
            "peaks_loaded": len(load_peaks())}


def _summary(rows: list[dict]) -> dict:
    peaks = {}
    for r in rows:
        for p in r["peaks"]:
            peaks.setdefault(p["name"], {"name": p["name"], "elevation_m": p["elevation_m"],
                                         "rank": p.get("rank"), "first": r["start"][:10], "times": 0})
            peaks[p["name"]]["times"] += 1
            peaks[p["name"]]["first"] = min(peaks[p["name"]]["first"], r["start"][:10])
    by_class = {}
    for r in rows:
        by_class[r["mclass"]] = by_class.get(r["mclass"], 0) + 1
    return {
        "count": len(rows),
        "by_class": by_class,
        "baiyue_peaks": sorted(peaks.values(), key=lambda p: -(p["elevation_m"] or 0)),
        "total_climb_m": round(sum(r["climbing_m"] or 0 for r in rows)),
        "total_km": round(sum(r["distance_km"] or 0 for r in rows), 1),
        "highest": max(rows, key=lambda r: r["top_m"] or 0)["top_m"] if rows else None,
        "longest_km": max((r["distance_km"] or 0 for r in rows), default=None),
        "biggest_climb_m": max((r["climbing_m"] or 0 for r in rows), default=None),
    }


class RecordAnnotation(BaseModel):
    name: Optional[str] = None
    shang_he_min: Optional[float] = None
    note: Optional[str] = None
    hidden: Optional[bool] = None


@router.put("/records/{rid:path}")
def annotate_record(rid: str, body: RecordAnnotation):
    ann = Annotations()
    return {"id": rid, "annotation": ann.set_record(rid, **body.model_dump())}


class RouteName(BaseModel):
    name: str


@router.put("/routes/{key:path}")
def name_route(key: str, body: RouteName):
    ann = Annotations()
    ann.set_route_name(key, body.name.strip())
    return {"route_key": key, "name": body.name.strip()}


def _hms(s: Optional[float]) -> str:
    if not s:
        return "—"
    s = int(round(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}"


def _period_line(rows: list[dict], date_from: Optional[str], date_to: Optional[str]) -> str:
    """統計區間: the filter's dates when set, otherwise the span the records cover,
    so a reader knows when the totals are from."""
    days = sorted(r["start"][:10] for r in rows if r.get("start"))
    lo = (date_from or "")[:10] or (days[0] if days else None)
    hi = (date_to or "")[:10] or (days[-1] if days else None)
    if not lo and not hi:
        return "統計區間：—（沒有紀錄）"
    whole = "（全部紀錄）" if not date_from and not date_to else ""
    return f"統計區間：{lo or '最早'} ～ {hi or '最近'}{whole}，共 {len(rows)} 筆"


@router.get("/export", response_class=PlainTextResponse)
def export_text(kind: Optional[str] = None, mclass: Optional[str] = None, q: Optional[str] = None,
                min_km: Optional[float] = None, max_km: Optional[float] = None,
                min_climb: Optional[float] = None, max_climb: Optional[float] = None,
                min_top: Optional[float] = None, date_from: Optional[str] = None,
                date_to: Optional[str] = None, sort: str = "climbing", limit: int = 10):
    """Plain text for pasting into a hiking-group sign-up form or chat."""
    all_rows = _filter(_rows(), kind, mclass, q, min_km, max_km, min_climb, max_climb,
                       min_top, date_from, date_to)
    rows = sorted(all_rows, key=SORT_KEYS.get(sort, SORT_KEYS["climbing"]), reverse=True)[:limit]
    s = _summary(all_rows)
    lines = ["【登山 / 越野紀錄】"]
    lines.append(_period_line(all_rows, date_from, date_to))
    peaks = s["baiyue_peaks"]
    if peaks:
        lines.append(f"已登百岳 {len(peaks)} 座：" + "、".join(
            f"{p['name']}({p['elevation_m']})" for p in peaks))
    cls = s["by_class"]
    lines.append("累計：" + "，".join(f"{k} {v} 次" for k, v in cls.items())
                 + f"；總爬升 {s['total_climb_m']:,} m")
    if s["highest"]:
        lines.append(f"最高到達 {round(s['highest'])} m；單次最大爬升 {round(s['biggest_climb_m'] or 0):,} m")
    lines.append("")
    lines.append(f"代表紀錄 Top {len(rows)}：")
    for i, r in enumerate(rows, 1):
        bits = [r["start"][:10], r["name"],
                f"{(r['distance_km'] or 0):.1f} km",
                f"↑{round(r['climbing_m'] or 0):,} m",
                f"移動 {_hms(r['moving_s'])}"]
        if r["days"]:
            bits.append(f"{len(r['days'])} 天")
        if r["shang_he_ratio"]:
            bits.append(f"上河 {r['shang_he_ratio']:.2f}")
        lines.append(f"{i:>2}. " + "｜".join(bits))
    lines.append("")
    lines.append(f"（資料來源：個人 GPS 紀錄，產生於 {dt.date.today().isoformat()}）")
    return "\n".join(lines)


@router.get("/page", include_in_schema=False)
def page():
    return FileResponse(Path(__file__).resolve().parents[1] / "static" / "achievements.html")
