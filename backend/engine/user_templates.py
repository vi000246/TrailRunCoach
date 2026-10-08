"""
The user's own 課表範本 (SP-36; the 範本 page static/templates.html, the editor's 插入範本
and 儲存成範本). Tables workout_templates_user / workout_template_cats (models.py).

A template is a step structure (engine/workout_steps.py) stored as written: relative
targets (% CP / % LTHR / zones / 自動 / RPE / no target, 「直到按下計圈」 durations) are
resolved when it is used, with that day's thresholds; absolute W / bpm / pace stay as
they are. It also keeps
  * several categories: the built-in ids (workout_templates.cats: easy / quality / test /
    trail — 強度課 and 越野跑 rows go to their sub-tab by family_of / trail_type_of, computed
    on read) and the user's own (c<id>, workout_template_cats: add / rename / delete);
  * the 目標用 it was made for (target_basis hr / power; None = 自動 by the session type):
    applying it sets the session's target_basis;
  * an optional training-route GPX: parsed with the race calculator's reader and builder
    (racepower/gpx.parse, racepower/course.build_course — the same smoothing as an event's
    GPX, engine/event_gpx.py), the file gzipped to <HOME>/template_gpx/<id>.gz (per
    tenant), the elevation profile cached in the row. With one the step chart switches to
    the route's distance axis (km, as the race calculator's course profile) with the profile
    drawn as a light background: route_elevation() places each step by its distance, a time /
    直到按下計圈 / 負荷 / RPE step by the athlete's estimated speed for it (推估).

A session made from a template carries the template's id in its steps (`tpl`) and, once
saved, its own copy of the profile (`route`, route_copy(): the ≤ PROFILE_OUT-point
downsample; both kept by workout_steps.normalize), so its chart keeps the profile when the
template or its GPX is deleted later.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import WorkoutTemplateCat, WorkoutTemplateUser
from backend.i18n import N_, _

NAME_MAX = 40
CAT_MAX = 20                     # a custom category's label
MAX_CATS = 8                     # categories of one template
MAX_TEMPLATES = 200
MAX_CUSTOM_CATS = 30
NOTE_MAX = 200
BUILTIN = ("easy", "quality", "test", "trail")
BASES = ("hr", "power")
PROFILE_OUT = 400                # profile points sent with a chart (the stored one: ≤ course.PROFILE_MAX)
EP_CLIMB_M = 100.0               # effort distance: km + climb / 100 (equivalence trail EP, as workout_steps._secs)

ROOT = None                      # fixed folder (tests); None = the tenant's template_gpx/

MINE = N_("我的範本")
SRC = N_("我的範本（自己排的）")
ELEV_NOTE = N_("有 GPX 時橫軸是路線距離（km）：距離段照它的距離放，時間、直到按下計圈、負荷、RPE 段依你每段的預估速度換算成距離（越野用努力距離：km＋爬升÷100），是推估")


class TemplateError(ValueError):
    def __init__(self, errors: list[str], status: int = 400):
        super().__init__("；".join(errors))
        self.errors = errors
        self.status = status


def _root() -> Path:
    if ROOT is not None:
        return Path(ROOT)
    from backend import tenancy
    return tenancy.private_path("template_gpx")


def gpx_path(tid: int, root: Optional[Path] = None) -> Path:
    return Path(root or _root()) / f"{int(tid)}.gz"


def _now() -> dt.datetime:
    return dt.datetime.utcnow().replace(microsecond=0)


def _loads(raw, default):
    try:
        v = json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


# ---------------------------------------------------------------------------
# categories
# ---------------------------------------------------------------------------

async def _ensure(db: AsyncSession) -> None:
    """The two tables, in a DB made before them that init_db hasn't migrated yet (a demo
    sandbox copied from an older base): create_all is idempotent (checkfirst)."""
    def mk(sess):
        conn = sess.connection()
        for tbl in (WorkoutTemplateUser.__table__, WorkoutTemplateCat.__table__):
            tbl.create(conn, checkfirst=True)
    await db.run_sync(mk)


def cat_id(c: WorkoutTemplateCat) -> str:
    return f"c{c.id}"


async def custom_cats(db: AsyncSession) -> list[dict]:
    await _ensure(db)
    rows = (await db.execute(select(WorkoutTemplateCat).order_by(WorkoutTemplateCat.id))).scalars().all()
    return [{"id": cat_id(c), "label": c.label, "custom": True} for c in rows]


def _label(v) -> str:
    s = " ".join(str(v or "").split())
    if not s:
        raise TemplateError([_("分類名稱不能空白")])
    if len(s) > CAT_MAX:
        raise TemplateError([_("分類名稱最多 {n} 字", n=CAT_MAX)])
    return s


async def add_cat(db: AsyncSession, label) -> dict:
    s = _label(label)
    have = await custom_cats(db)
    if len(have) >= MAX_CUSTOM_CATS:
        raise TemplateError([_("自訂分類最多 {n} 個", n=MAX_CUSTOM_CATS)])
    if any(c["label"] == s for c in have):
        raise TemplateError([_("已經有這個分類")])
    c = WorkoutTemplateCat(label=s, created_at=_now())
    db.add(c)
    await db.commit()
    return {"id": cat_id(c), "label": c.label, "custom": True}


def _cat_pk(cid: str) -> int:
    try:
        return int(str(cid)[1:]) if str(cid).startswith("c") else -1
    except ValueError:
        return -1


async def rename_cat(db: AsyncSession, cid: str, label) -> dict:
    s = _label(label)
    c = await db.get(WorkoutTemplateCat, _cat_pk(cid))
    if c is None:
        raise TemplateError([_("找不到這個分類")], 404)
    if any(x["label"] == s and x["id"] != cid for x in await custom_cats(db)):
        raise TemplateError([_("已經有這個分類")])
    c.label = s
    await db.commit()
    return {"id": cid, "label": s, "custom": True}


async def delete_cat(db: AsyncSession, cid: str) -> dict:
    """Remove a custom category; templates keep their other categories (one left with none
    gets nothing: it still shows on the 範本 page under 全部)."""
    c = await db.get(WorkoutTemplateCat, _cat_pk(cid))
    if c is None:
        raise TemplateError([_("找不到這個分類")], 404)
    await db.delete(c)
    n = 0
    for t in (await db.execute(select(WorkoutTemplateUser))).scalars().all():
        cats = _loads(t.cats_json, [])
        if cid in cats:
            t.cats_json = json.dumps([x for x in cats if x != cid])
            n += 1
    await db.commit()
    return {"removed": cid, "templates": n}


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------

def clean(data: dict, custom_ids: set, partial: bool = False) -> dict:
    """The stored fields of a create / update body, or TemplateError with every problem."""
    from backend.engine import workout_steps as WS
    errs: list[str] = []
    out: dict = {}
    if not partial or "name" in data:
        name = " ".join(str(data.get("name") or "").split())
        if not name:
            errs.append(_("範本名稱不能空白"))
        elif len(name) > NAME_MAX:
            errs.append(_("範本名稱最多 {n} 字", n=NAME_MAX))
        out["name"] = name
    if not partial or "cats" in data:
        cats = data.get("cats") or []
        if not isinstance(cats, list):
            cats = [cats]
        cats = list(dict.fromkeys(str(c) for c in cats))
        bad = [c for c in cats if c not in BUILTIN and c not in custom_ids]
        if bad:
            errs.append(_("沒有這個分類：{c}", c="、".join(bad)))
        if len(cats) > MAX_CATS:
            errs.append(_("一個範本最多 {n} 個分類", n=MAX_CATS))
        out["cats"] = cats
    if not partial or "steps" in data:
        try:
            st = WS.normalize(data.get("steps"))
            st.pop("tpl", None)
            st.pop("route", None)
            st["origin"] = "user"
            out["steps"] = st
        except WS.StepsError as e:
            errs += e.errors
    if "target_basis" in data:
        b = data.get("target_basis")
        b = None if b in (None, "", "auto") else b
        if b is not None and b not in BASES:
            errs.append(_("目標用要是 自動／心率／功率"))
        out["target_basis"] = b
    if "note" in data:
        out["note"] = str(data.get("note") or "").strip()[:NOTE_MAX] or None
    if errs:
        raise TemplateError(list(dict.fromkeys(errs)))
    return out


def to_dict(t: WorkoutTemplateUser) -> dict:
    return {"id": t.id, "name": t.name, "cats": _loads(t.cats_json, []), "target_basis": t.target_basis,
            "steps": _loads(t.steps_json, {}), "note": t.note or "", "copied_from": t.copied_from,
            "gpx": gpx_meta(t),
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None}


def gpx_meta(t: WorkoutTemplateUser) -> Optional[dict]:
    if not t.gpx_sha1:
        return None
    return {"filename": t.gpx_filename, "km": t.gpx_km, "gain_m": t.gpx_gain_m, "loss_m": t.gpx_loss_m}


async def list_all(db: AsyncSession) -> list[dict]:
    await _ensure(db)
    rows = (await db.execute(select(WorkoutTemplateUser).order_by(WorkoutTemplateUser.id))).scalars().all()
    return [to_dict(t) for t in rows]


async def _get(db: AsyncSession, tid) -> WorkoutTemplateUser:
    await _ensure(db)
    try:
        t = await db.get(WorkoutTemplateUser, int(tid))
    except (TypeError, ValueError):
        t = None
    if t is None:
        raise TemplateError([_("找不到這個範本")], 404)
    return t


async def get(db: AsyncSession, tid) -> dict:
    return to_dict(await _get(db, tid))


async def _custom_ids(db: AsyncSession) -> set:
    return {c["id"] for c in await custom_cats(db)}


async def create(db: AsyncSession, data: dict, copied_from: Optional[str] = None) -> dict:
    f = clean(data, await _custom_ids(db))      # (custom_cats: the tables exist from here)
    n = len((await db.execute(select(WorkoutTemplateUser.id))).all())
    if n >= MAX_TEMPLATES:
        raise TemplateError([_("範本最多 {n} 個", n=MAX_TEMPLATES)])
    now = _now()
    t = WorkoutTemplateUser(name=f["name"], cats_json=json.dumps(f["cats"]), target_basis=f.get("target_basis"),
                            steps_json=json.dumps(f["steps"], ensure_ascii=False), note=f.get("note"),
                            copied_from=(copied_from or None) and str(copied_from)[:40], created_at=now, updated_at=now)
    db.add(t)
    await db.commit()
    return to_dict(t)


async def update(db: AsyncSession, tid, patch: dict) -> dict:
    t = await _get(db, tid)
    f = clean(patch, await _custom_ids(db), partial=True)
    if "name" in f:
        t.name = f["name"]
    if "cats" in f:
        t.cats_json = json.dumps(f["cats"])
    if "steps" in f:
        t.steps_json = json.dumps(f["steps"], ensure_ascii=False)
    if "target_basis" in f:
        t.target_basis = f["target_basis"]
    if "note" in f:
        t.note = f["note"]
    t.updated_at = _now()
    await db.commit()
    return to_dict(t)


async def delete(db: AsyncSession, tid, root: Optional[Path] = None) -> dict:
    t = await _get(db, tid)
    tid = t.id
    await db.delete(t)
    await db.commit()
    _unlink(tid, root)
    return {"removed": tid}


def _unlink(tid: int, root: Optional[Path] = None) -> None:
    try:
        gpx_path(tid, root).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# the training-route GPX
# ---------------------------------------------------------------------------

def parse_profile(data: bytes, filename: str = "") -> dict:
    """{"totals", "profile": {"km", "z"}} of a GPX / FIT course: the race calculator's reader
    and builder (no parser of its own). TemplateError on a file that isn't a course."""
    from backend.engine.racepower import course as CO
    from backend.engine.racepower import gpx as GPX
    try:
        tr = GPX.parse(data, filename)
        c = CO.build_course(tr, split="none")
    except (GPX.GpxError, ValueError) as e:
        raise TemplateError([str(e)])
    p = c.get("profile") or {}
    return {"totals": c["totals"], "name": tr.name or "",
            "profile": {"km": [round(float(x), 4) for x in p.get("km") or []], "z": list(p.get("z") or [])}}


async def save_gpx(db: AsyncSession, tid, data: bytes, filename: str = "", root: Optional[Path] = None,
                   parsed: Optional[dict] = None) -> dict:
    """Upload or replace the template's GPX (a broken file is refused before anything is
    written); `parsed` = parse_profile(data) when the caller already did it (off the loop)."""
    t = await _get(db, tid)
    got = parsed or parse_profile(data, filename)
    tot = got["totals"]
    gz = gzip.compress(data, compresslevel=9, mtime=0)
    f = gpx_path(t.id, root)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_bytes(gz)
    os.replace(tmp, f)
    t.gpx_filename = (filename or got["name"] or "route.gpx")[:200]
    t.gpx_sha1 = hashlib.sha1(data).hexdigest()
    t.gpx_km, t.gpx_gain_m, t.gpx_loss_m = float(tot["km"]), float(tot["gain_m"]), float(tot["loss_m"])
    t.gpx_profile_json = json.dumps(got["profile"])
    t.updated_at = _now()
    await db.commit()
    return to_dict(t)


async def delete_gpx(db: AsyncSession, tid, root: Optional[Path] = None) -> dict:
    t = await _get(db, tid)
    if not t.gpx_sha1:
        raise TemplateError([_("這個範本沒有 GPX")], 404)
    _unlink(t.id, root)
    t.gpx_filename = t.gpx_sha1 = t.gpx_profile_json = None
    t.gpx_km = t.gpx_gain_m = t.gpx_loss_m = None
    t.updated_at = _now()
    await db.commit()
    return to_dict(t)


def read_gpx(tid: int, root: Optional[Path] = None) -> Optional[bytes]:
    try:
        return gzip.decompress(gpx_path(tid, root).read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile):
        return None


async def profile_of(db: AsyncSession, tid) -> Optional[dict]:
    """The cached profile + totals of a template's GPX, None when it has none (or is gone)."""
    await _ensure(db)
    try:
        t = await db.get(WorkoutTemplateUser, int(tid))
    except (TypeError, ValueError):
        return None
    if t is None or not t.gpx_profile_json:
        return None
    p = _loads(t.gpx_profile_json, {})
    if not p.get("km"):
        return None
    return {"km": p["km"], "z": p["z"], "route_km": t.gpx_km, "gain_m": t.gpx_gain_m, "name": t.name,
            "filename": t.gpx_filename, "id": t.id}


def route_copy(prof: Optional[dict]) -> Optional[dict]:
    """A session's own copy of a template's route profile (steps `route`): the profile
    downsampled to ≤ PROFILE_OUT points (the ends kept), its length, climb and file name."""
    if not prof:
        return None
    km, z = list(prof.get("km") or []), list(prof.get("z") or [])
    if len(km) < 2 or len(km) != len(z):
        return None
    step = max(1, -(-len(km) // PROFILE_OUT))
    idx = list(range(0, len(km), step))
    if idx[-1] != len(km) - 1:
        if len(idx) < PROFILE_OUT:
            idx.append(len(km) - 1)
        else:
            idx[-1] = len(km) - 1
    return {"km": [round(float(km[i]), 3) for i in idx], "z": [round(float(z[i]), 1) for i in idx],
            "route_km": prof.get("route_km"), "gain_m": prof.get("gain_m"),
            "name": prof.get("filename") or prof.get("name") or ""}


def _interp(x: float, xs: list, ys: list) -> float:
    """Linear interpolation on increasing xs (held flat outside)."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        m = (lo + hi) // 2
        if xs[m] <= x:
            lo = m
        else:
            hi = m
    a, b = xs[lo], xs[hi]
    return ys[lo] if b == a else ys[lo] + (ys[hi] - ys[lo]) * (x - a) / (b - a)


def route_elevation(steps: dict, c, prof: dict) -> Optional[dict]:
    """The step chart on the route's distance axis (the race calculator's course profile:
    real km along the route vs elevation), {"x": [km] — where each step of view()["order"]
    starts, plus the end (len(order) + 1) —, "d": [km], "z": [m] (the profile up to where
    the workout ends), "z_min", "z_max", "route_km", "km" (covered), "total_km", "complete",
    "gain_m", "name", "note"} or None.

    A distance step covers its own km of the route; a time / 直到按下計圈 / 負荷 / RPE step
    covers its effort distance (EP = km + climb / 100) at the athlete's speed for the step's
    intensity (workout_steps.speed_kmh, scaled to the trail EP speed when known; a rest
    without a target walks, WALK_KMH; a lap-button step without an estimate the chart's
    OPEN_CHART_S), turned back into km through the route's climb. Past the route's end the
    axis goes on 1 : 1 (flat). All 推估: the real pace on a climb isn't known before the run."""
    from backend.engine import workout_steps as WS
    km, z = list(prof.get("km") or []), list(prof.get("z") or [])
    if len(km) < 2 or len(km) != len(z):
        return None
    k0 = km[0]
    km = [k - k0 for k in km]
    ep, climb = [km[0]], 0.0
    for i in range(1, len(km)):
        climb += max(0.0, z[i] - z[i - 1])
        ep.append(km[i] + climb / EP_CLIMB_M)

    def to_km(e: float) -> float:
        return _interp(e, ep, km) if e <= ep[-1] else km[-1] + (e - ep[-1])

    def to_ep(k: float) -> float:
        return _interp(k, km, ep) if k <= km[-1] else ep[-1] + (k - km[-1])

    xs, K = [0.0], 0.0
    for row in WS.flat(steps["items"]):
        st = row["st"]
        r = WS.resolve(st, c)
        s, _e = WS._secs(st, r, c)
        if st["dur"]["type"] == "open" and not s:
            s = float(WS.OPEN_CHART_S)
        if st["dur"]["type"] == "distance":
            K += st["dur"]["value"] / 1000.0
        elif s > 0:
            if r.frac is None and st["kind"] == "rest":
                v = WS.WALK_KMH
            else:
                f = r.frac if r.frac is not None else WS.NONE_IF.get(st["kind"], WS.EASY_F)
                v, _how = WS.speed_kmh(f, c)
                if c.ep_kmh and c.v_easy:
                    v = c.ep_kmh * v / c.v_easy
            K = to_km(to_ep(K) + v * s / 3600.0)
        xs.append(round(K, 3))                # one bound per step, a zero-length one included
    if K <= 0:
        return None
    pts_d, pts_z = [], []
    for i in range(len(km)):
        if km[i] > K:
            break
        pts_d.append(km[i])
        pts_z.append(z[i])
    complete = K >= km[-1]
    if not complete:                          # the workout ends mid-route: the last point at its end
        pts_d.append(K)
        pts_z.append(round(_interp(K, km, z), 1))
    if len(pts_d) < 2:
        return None
    step = max(1, -(-len(pts_d) // PROFILE_OUT))
    idx = list(range(0, len(pts_d), step))
    if idx[-1] != len(pts_d) - 1:
        idx.append(len(pts_d) - 1)
    return {"x": xs, "d": [round(pts_d[i], 3) for i in idx], "z": [pts_z[i] for i in idx],
            "z_min": min(z), "z_max": max(z), "route_km": prof.get("route_km") or km[-1],
            "km": round(min(K, km[-1]), 2), "total_km": round(K, 2), "complete": complete,
            "gain_m": prof.get("gain_m"), "name": prof.get("filename") or prof.get("name") or "", "note": _(ELEV_NOTE)}


# ---------------------------------------------------------------------------
# the 插入範本 menu (workout_steps.templates)
# ---------------------------------------------------------------------------

def row(t: dict) -> dict:
    """One 插入範本 row (the shape of workout_templates.row): key user:<id>, its family (強度課)
    and trail kind (越野跑) for the sub-tabs, the 目標用 and the GPX it carries."""
    from backend.engine import workout_steps as WS
    from backend.engine import workout_templates as WT
    full = (t.get("steps") or {}).get("items") or []
    fam = WT.family_of(full)
    r = {"key": f"user:{t['id']}", "id": t["id"], "label": t["name"], "title": t["name"],
         "src": t.get("note") or _(SRC), "url": "", "src_kind": "mine", "conv": "", "note": t.get("note") or "",
         "items": WT.main_of(full) or full, "full": full, "equiv": None, "family": fam,
         "fam_sub": fam["id"] if fam else None, "trail_sub": WT.trail_type_of(full),
         "purpose": "", "cats": t.get("cats") or [], "target_basis": t.get("target_basis"), "mine": True,
         "gpx": t.get("gpx"), "role": WS.rpe_role(full)}
    r["target_types"] = WS.template_target_types(r)          # SP-84: the same helper as 插入範本
    return r


def groups(templates: list[dict]) -> list[dict]:
    """「我的範本」 groups, one per category (and 強度課 / 越野跑 sub-tab) a template is in; a
    強度課 template with no interval family is listed under every 強度課 sub-tab (sub None)."""
    out: dict = {}
    for t in templates:
        r = row(t)
        for c in r["cats"]:
            sub = r["fam_sub"] if c == "quality" else r["trail_sub"] if c == "trail" else None
            g = out.setdefault((c, sub), {"group": _(MINE), "title": _(MINE), "cat": c, "sub": sub, "mine": True,
                                          "rows": []})
            g["rows"].append({**r, "sub": sub})
    return list(out.values())
