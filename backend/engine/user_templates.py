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
# SP-639: 「一段穩定上坡」 on a template's training route (algorithms/climbs.detect_climbs; the race
# calculator's are ≥ 80 m, ≥ 3 %): average grade ≥ 5 %, ≥ 30 m up, and a drop of up to
# max(10 m, 5 % of the gain so far) does not end it. The owner's numbers (SP-633 Q5), to be tuned.
CLIMB_MIN_GRADE = 0.05
CLIMB_MIN_GAIN_M = 30.0
CLIMB_DROP_M = 10.0
CLIMB_DROP_FRAC = 0.05
# a repeat whose first work leg climbs at least this (average grade on the route): a hill repeat —
# its recoveries come back down (not below where the repeat started) and the next rep climbs again
# from wherever the recovery stopped (SP-639 Q1-3: a short recovery leaves you part-way down; 推估:
# the usual 登山王 / Koop 上坡 shape; a flatter repeat keeps going forward along the route)
HILL_REP_GRADE = 0.03
# SP-639 Q1-1: a descent this steep or steeper gets no downhill speed bonus on the template's chart
# and 坡段表 (the flat pace or slower; 推估 — the runner's own grade model is a later step)
STEEP_DOWN_GRADE = -0.15
COURSE_V = 1                     # the stored segments' format

ROOT = None                      # fixed folder (tests); None = the tenant's template_gpx/

MINE = N_("我的範本")
SRC = N_("我的範本（自己排的）")
ELEV_NOTE = N_("有 GPX 時橫軸可切換距離或時間。距離段照它的距離放；時間、直到按下計圈、負荷、RPE 段用你這段強度的平地速度，"
               "依坡度換算（Minetti 跑步耗能，跟賽事計算機的平地等效配速同一套；陡下坡 15% 以上不加速），是推估。反覆爬坡（主課在上坡）的休息段畫成往回走下坡，"
               "時間不夠走回坡底時，下一趟從停下的地方再往上爬")
TABLE_NOTE = N_("坡段與爬坡由 GPX 平滑後切出；配速是你在這個強度的平地速度依坡度換算（Minetti，下坡最多快 10%，陡下坡 15% 以上不加速），"
                "沒算技術地形，是推估。功率只在平路和可跑的坡（≤ 8%）當目標")
LEVEL_EASY = N_("輕鬆（Z2）")
LEVEL_MAIN = N_("這個範本的主課強度")
SEG_KIND = {"run_climb": N_("可跑的爬坡"), "steep_climb": N_("陡坡"), "descent": N_("下坡"), "flat": N_("平路")}
CLIMB_LABEL = N_("爬 {m} m · 約 {t}")


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
    sandbox copied from an older base): create_all is idempotent (checkfirst); a table from
    before SP-310 gets its uid column (db/stable_ids.migrate fills the ids at the next init_db)."""
    def mk(sess):
        from sqlalchemy import text
        conn = sess.connection()
        for tbl in (WorkoutTemplateUser.__table__, WorkoutTemplateCat.__table__):
            tbl.create(conn, checkfirst=True)
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(workout_templates_user)"))}
        if "uid" not in cols:
            conn.execute(text("ALTER TABLE workout_templates_user ADD COLUMN uid VARCHAR(32)"))
        if "gpx_course_json" not in cols:          # SP-639
            conn.execute(text("ALTER TABLE workout_templates_user ADD COLUMN gpx_course_json TEXT"))
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
    return {"id": t.id, "uid": t.uid, "name": t.name, "cats": _loads(t.cats_json, []),
            "target_basis": t.target_basis, "steps": _loads(t.steps_json, {}), "note": t.note or "",
            "copied_from": t.copied_from,
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

def climb_rule() -> dict:
    """The climb thresholds in effect (stored with a template's segments: other numbers → recomputed)."""
    return {"min_grade": CLIMB_MIN_GRADE, "min_gain_m": CLIMB_MIN_GAIN_M, "drop_m": CLIMB_DROP_M,
            "drop_frac": CLIMB_DROP_FRAC}


def _climb_kw() -> dict:
    return {"min_grade": CLIMB_MIN_GRADE, "min_gain_m": CLIMB_MIN_GAIN_M,
            "abs_tolerance_m": CLIMB_DROP_M, "rel_tolerance": CLIMB_DROP_FRAC}


def _climb_row(c: dict) -> dict:
    return {"no": c["no"], "start_km": round(c["start_m"] / 1000.0, 3), "end_km": round(c["end_m"] / 1000.0, 3),
            "dist_m": round(c["end_m"] - c["start_m"], 1), "gain_m": round(float(c["gain_m"]), 1),
            "grade": round(float(c["grade"]), 4)}


def climbs_of(xs_m, zs) -> list[dict]:
    """The template rule's climbs (CLIMB_*) of a smoothed profile (xs in m): the race calculator's
    detector (course._climbs → algorithms/climbs.detect_climbs) with the training-route thresholds."""
    from backend.engine.racepower import course as CO
    return [_climb_row(c) for c in CO._climbs(xs_m, zs, **_climb_kw())]


def _course_rows(c: dict) -> dict:
    """What a template keeps of build_course (SP-639): the grade segments (DP + merge, the race
    calculator's 坡段) and the climbs under the template rule; no coordinates."""
    segs = [{"i": r["i"], "start_km": round(r["start_km"], 3), "end_km": round(r["end_km"], 3),
             "dist_m": round(r["dist_m"], 1), "gain_m": round(r["gain_m"], 1), "loss_m": round(r["loss_m"], 1),
             "grade": round(r["grade"], 4), "max_grade": round(r["max_grade"], 4),
             "z_start": round(r["z_start"], 1), "z_end": round(r["z_end"], 1), "cls": r["cls"],
             "climb_no": r.get("climb_no")} for r in c.get("segments") or []]
    return {"v": COURSE_V, "rule": climb_rule(), "segments": segs,
            "climbs": [_climb_row(x) for x in c.get("climbs") or []]}


def parse_profile(data: bytes, filename: str = "") -> dict:
    """{"totals", "profile": {"km", "z"}, "course": {segments, climbs}} of a GPX / FIT course: the
    race calculator's reader and builder (no parser of its own), split by grade (its 坡段) and the
    climbs under the template rule (climb_rule, SP-639). TemplateError on a file that isn't a course."""
    from backend.engine.racepower import gpx as GPX
    try:
        tr = GPX.parse(data, filename)
    except (GPX.GpxError, ValueError) as e:
        raise TemplateError([str(e)])
    return parse_track(tr)


def parse_track(tr) -> dict:
    """parse_profile of an already-read track (racepower/gpx.Track): also 「我的路段」's reference
    effort (engine/route_library.py, SP-640). TemplateError when it can't be a course."""
    from backend.engine.racepower import course as CO
    from backend.engine.racepower import gpx as GPX
    try:
        c = CO.build_course(tr, split="grade", osm_huts=False, climb_kw=_climb_kw())
    except (GPX.GpxError, ValueError) as e:
        raise TemplateError([str(e)])
    p = c.get("profile") or {}
    return {"totals": c["totals"], "name": tr.name or "",
            "profile": {"km": [round(float(x), 4) for x in p.get("km") or []], "z": list(p.get("z") or [])},
            "course": _course_rows(c)}


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
    t.gpx_course_json = json.dumps(got["course"]) if got.get("course") else None
    t.updated_at = _now()
    await db.commit()
    return to_dict(t)


async def delete_gpx(db: AsyncSession, tid, root: Optional[Path] = None) -> dict:
    t = await _get(db, tid)
    if not t.gpx_sha1:
        raise TemplateError([_("這個範本沒有 GPX")], 404)
    _unlink(t.id, root)
    t.gpx_filename = t.gpx_sha1 = t.gpx_profile_json = t.gpx_course_json = None
    t.gpx_km = t.gpx_gain_m = t.gpx_loss_m = None
    t.updated_at = _now()
    await db.commit()
    return to_dict(t)


def read_gpx(tid: int, root: Optional[Path] = None) -> Optional[bytes]:
    try:
        return gzip.decompress(gpx_path(tid, root).read_bytes())
    except (OSError, EOFError, gzip.BadGzipFile):
        return None


def _fresh(c: dict) -> bool:
    return bool(c) and c.get("v") == COURSE_V and c.get("rule") == climb_rule() and "segments" in c


async def course_of(db: AsyncSession, tid, root: Optional[Path] = None) -> dict:
    """The template GPX's segments + climbs (SP-639). Stored at upload; a template uploaded before
    (no segments) or under other climb thresholds gets them recomputed from its stored file and
    kept (its updated_at untouched: nothing the user changed). TemplateError 404 without a GPX."""
    import asyncio

    from sqlalchemy import update as sa_update
    t = await _get(db, tid)
    if not t.gpx_sha1:
        raise TemplateError([_("這個範本沒有 GPX")], 404)
    c = _loads(t.gpx_course_json, {})
    if not _fresh(c):
        data = read_gpx(t.id, root)
        if data is None:
            raise TemplateError([_("這個範本沒有 GPX")], 404)
        c = (await asyncio.to_thread(parse_profile, data, t.gpx_filename or ""))["course"]
        await db.execute(sa_update(WorkoutTemplateUser).where(WorkoutTemplateUser.id == t.id)
                         .values(gpx_course_json=json.dumps(c), updated_at=t.updated_at))
        await db.commit()
    return {**c, "name": t.gpx_filename or "", "route_km": t.gpx_km, "gain_m": t.gpx_gain_m, "loss_m": t.gpx_loss_m}


def _flat_kmh(f: Optional[float], c) -> float:
    """km/h on the flat at ≈ f × CP (workout_steps.speed_kmh), scaled to the athlete's trail EP speed
    when known (the step chart's speed, route_elevation)."""
    from backend.engine import workout_steps as WS
    v, _how = WS.speed_kmh(f, c)
    if c.ep_kmh and c.v_easy:
        v = c.ep_kmh * v / c.v_easy
    return max(0.5, float(v))


def _gf(g: float) -> float:
    """Minetti's running cost at grade g relative to the flat, the 10 % downhill floor: the race
    calculator's GAP curve (planner: gap_pace = pace × this). SP-639 Q1-1 (the user: 陡下坡在山上實際是
    減速的): from STEEP_DOWN_GRADE down no speed bonus — the flat pace, or slower where Minetti's cost
    is above the flat's."""
    from backend.engine.algorithms.minetti import grade_factor
    f = grade_factor(g)
    return max(1.0, f) if g <= STEEP_DOWN_GRADE else f


def _mins(s: float) -> str:
    m = max(1, int(round(s / 60.0)))
    return f"{m}′" if m < 60 else f"{m // 60}h{m % 60:02d}′"


def main_frac(order: list) -> Optional[float]:
    """The template's main-set intensity (× CP): its work steps' resolved fraction, averaged by
    their seconds (a lap-button step counts OPEN_CHART_S); None without one."""
    from backend.engine import workout_steps as WS
    num = den = 0.0
    for o in order:
        if o.get("kind") != "work" or o.get("frac") is None:
            continue
        sec = float(o.get("sec") or 0.0) or float(WS.OPEN_CHART_S)
        num += float(o["frac"]) * sec
        den += sec
    return num / den if den > 0 else None


def course_table(course: dict, c, levels: list) -> dict:
    """The 坡段表 (SP-639): each segment and climb of the template's route with its kind (the race
    calculator's seg_targets.kind_of: flat / runnable climb ≤ 8 % / steep climb / descent) and, per
    intensity level (key, label, f = × CP), the pace, time, GAP and power there: the flat speed at f
    (the step chart's) over the grade's Minetti factor. Power a target on flat / runnable ground
    only, a reference elsewhere (seg_targets.chart_rows' rule). 推估: no technical terrain."""
    from backend.engine.racepower import seg_targets as SGT
    cp = c.cp
    lv, vs = [], {}
    for key, label, f in levels:
        v = _flat_kmh(f, c)
        vs[key] = (v, f)
        lv.append({"key": key, "label": label, "frac": round(float(f), 3), "flat_pace_s_per_km": round(3600.0 / v, 1),
                   "power": round(f * cp) if cp else None})

    def est(dist_m: float, grade: float, kind: str) -> dict:
        out = {}
        for key, (v, f) in vs.items():
            pace = 3600.0 / (v / _gf(grade))
            p = round(f * cp) if cp else None
            ok = kind in ("flat", "run_climb")
            out[key] = {"pace_s_per_km": round(pace, 1), "t_s": round(dist_m / 1000.0 * pace),
                        "gap_s_per_km": round(3600.0 / v, 1), "power": p if ok else None, "power_ref": None if ok else p}
        return out

    segs = []
    for s in course.get("segments") or []:
        k = SGT.kind_of({"grade": s["grade"]})
        segs.append({**s, "kind": k, "kind_label": _(SEG_KIND[k]), "est": est(s["dist_m"], s["grade"], k)})
    last = levels[-1][0] if levels else None
    climbs = []
    for cl in course.get("climbs") or []:
        k = SGT.kind_of({"grade": cl["grade"]})
        e = est(cl["dist_m"], cl["grade"], k)
        climbs.append({**cl, "kind": k, "kind_label": _(SEG_KIND[k]), "est": e,
                       "label": _(CLIMB_LABEL, m=round(cl["gain_m"]), t=_mins(e[last]["t_s"])) if last else ""})
    return {"segments": segs, "climbs": climbs, "levels": lv, "rule": course.get("rule") or climb_rule(),
            "note": _(TABLE_NOTE)}


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


class _Walk:
    """Moving along a route profile (km, z — piecewise linear; km from 0) at a flat speed v (km/h): on
    each piece the horizontal speed is v / _gf(grade in the travel direction). Past the route's end
    the ground is flat (1 : 1); going back stops at `floor` (a hill repeat's bottom) and the rest of
    the step is spent on the flat there."""
    EPS = 1e-9

    def __init__(self, km: list, z: list):
        self.km, self.z, self.end = km, z, km[-1]

    def z_at(self, k: float) -> float:
        return _interp(min(max(k, 0.0), self.end), self.km, self.z)

    def _piece(self, pos: float, sign: int, floor: float) -> tuple[Optional[float], float]:
        """(the next stop or None = open flat, grade in the travel direction)."""
        import bisect
        km, z, e = self.km, self.z, self.EPS
        if sign > 0:
            if pos >= self.end - e:
                return None, 0.0
            j = bisect.bisect_right(km, pos + e)
            dk = km[j] - km[j - 1]
            return km[j], ((z[j] - z[j - 1]) / (dk * 1000.0) if dk > 0 else 0.0)
        if pos <= floor + e:
            return None, 0.0
        if pos > self.end + e:
            return max(self.end, floor), 0.0
        j = bisect.bisect_left(km, pos - e) - 1
        if j < 0:
            return None, 0.0
        dk = km[j + 1] - km[j]
        return max(km[j], floor), (-(z[j + 1] - z[j]) / (dk * 1000.0) if dk > 0 else 0.0)

    def move(self, pos: float, sign: int, v: float, secs: Optional[float] = None, dist: Optional[float] = None,
             floor: float = 0.0) -> tuple:
        """(new pos, seconds, km covered, gain m, loss m, [(km, pos, s) from the step's start at each
        knot passed and the end]) for `secs` seconds or `dist` km."""
        t = d = gain = loss = 0.0
        pts = []
        v = max(0.1, float(v))
        for _i in range(100000):
            lt = None if secs is None else secs - t
            ld = None if dist is None else dist - d
            if (lt is None and ld is None) or (lt is not None and lt <= 1e-6) or (ld is not None and ld <= 1e-9):
                break
            nxt, g = self._piece(pos, sign, floor)
            vh = v / _gf(g)
            piece = float("inf") if nxt is None else abs(nxt - pos)
            take = piece
            if lt is not None:
                take = min(take, vh * lt / 3600.0)
            if ld is not None:
                take = min(take, ld)
            if take <= 0 and nxt is not None:
                pos = nxt                        # a zero-length piece
                continue
            z0 = self.z_at(pos)
            if nxt is not None:
                pos = nxt if take >= piece else pos + sign * take
            elif sign > 0:
                pos += take                      # off the route's end: flat
            dz = self.z_at(pos) - z0
            gain += max(0.0, dz)
            loss += max(0.0, -dz)
            d += take
            t += take / vh * 3600.0
            pts.append((d, pos, t))
        return pos, t, d, gain, loss, pts


def _foot(prof: dict, k0: float, end: float) -> Optional[float]:
    """The route copy's `rep_km` (where the hill repeats start) on the 0-based km, None without one."""
    try:
        v = float(prof.get("rep_km")) - k0
    except (TypeError, ValueError):
        return None
    return v if 0.0 <= v < end else None


def _top(prof: dict, k0: float, end: float) -> Optional[float]:
    """The top of the climb picked for the hill repeats (the route copy's `climb.end_km`) on the 0-based
    km, None without one."""
    try:
        v = float((prof.get("climb") or {}).get("end_km")) - k0
    except (TypeError, ValueError):
        return None
    return min(v, end) if v > 0 else None


PATH_OUT = 600                   # elevation-path points sent with a chart


def route_elevation(steps: dict, c, prof: dict) -> Optional[dict]:
    """The step chart on the route (the race calculator's course profile: real km along the route vs
    elevation), SP-36 / SP-639:
      "x"   km covered where each step of view()["order"] starts, plus the end (len(order) + 1) —
            the distance axis;
      "xt"  the same in seconds (the time axis); "s" each step's seconds;
      "up" / "down"  each step's climb / descent (m); "r" each step's [from, to] km on the route;
      "d", "z", "t"  the elevation path on the distance covered (km, m — None: off the route's end —
            and seconds): a hill repeat draws the climb again each time;
      "z_min", "z_max" (of the drawn path), "route_km", "km" (the farthest point reached), "total_km", "complete",
      "gain_m", "name", "note"; None without a usable profile.

    A distance step covers its own km; a time / 直到按下計圈 / 負荷 / RPE step its seconds at the athlete's
    flat speed for its intensity (workout_steps.speed_kmh, scaled to the trail EP speed when known; a
    rest without a target walks, WALK_KMH; a lap-button step without an estimate the chart's
    OPEN_CHART_S), each piece of the route at that speed over Minetti's grade factor (_gf: the race
    calculator's GAP curve). In a repeat whose first work leg climbs (≥ HILL_REP_GRADE on the route)
    the rests come back down — not below where the repeat started — and the next rep climbs again from
    where the rest stopped (SP-639 Q1-3; a rest too short to get back to the bottom leaves it part-way
    up); other repeats go on forward. All 推估: the real pace on a climb isn't known before the run."""
    from backend.engine import workout_steps as WS
    km, z = list(prof.get("km") or []), list(prof.get("z") or [])
    if len(km) < 2 or len(km) != len(z):
        return None
    k0 = km[0]
    km = [float(k) - k0 for k in km]
    z = [float(v) for v in z]
    w = _Walk(km, z)
    pos = D = T = far = 0.0
    xs, xt, ss, ups, downs, rr = [0.0], [0.0], [], [], [], []
    path = [(0.0, 0.0, 0.0, True)]               # (km covered, route pos, s, keep)
    reps: dict = {}
    # SP-640: the climb picked for the hill repeats (route_library.apply): the first repeat starts at its
    # foot, whatever the warm-up covered (推估: you get there before the first rep)
    foot = _foot(prof, k0, w.end)
    top = _top(prof, k0, w.end) if foot is not None else None
    for row in WS.flat(steps["items"]):
        st = row["st"]
        inner = row["rep"][-1] if row["rep"] else None
        rs = None
        if inner:
            rid, pi, _n = inner
            rs = reps.get(rid)
            if rs is None:
                if foot is not None and not reps:
                    pos = foot
                rs = reps[rid] = {"base": pos, "hill": None, "pass": pi}
            elif pi != rs["pass"]:
                rs["pass"] = pi                  # the next rep climbs from where the recovery stopped (Q1-3)
        r = WS.resolve(st, c)
        s, _e = WS._secs(st, r, c)
        typ = st["dur"]["type"]
        if typ == "open" and not s:
            s = float(WS.OPEN_CHART_S)
        if r.frac is None and st["kind"] == "rest":
            v = WS.WALK_KMH
        else:
            v = _flat_kmh(r.frac if r.frac is not None else WS.NONE_IF.get(st["kind"], c.easy_frac()), c)
        back = bool(rs and rs["hill"] and st["kind"] == "rest")
        start = pos
        if path[-1][1] != pos:
            path.append((D, pos, T, True))
        if typ == "distance":
            pos, used_t, used_d, gn, ls, pts = w.move(pos, -1 if back else 1, v, dist=st["dur"]["value"] / 1000.0,
                                                      floor=rs["base"] if back else 0.0)
        elif (rs is not None and typ == "open" and st["kind"] in ("work", "other") and top is not None
              and pos < top - 1e-6 and not back):
            # the climb picked on 路線 (route_library.apply: 「爬 120 m」 ends with the lap button at the top):
            # up to the top from wherever this rep starts — a rep after a short recovery climbs less (Q1-3)
            pos, used_t, used_d, gn, ls, pts = w.move(pos, 1, v, dist=top - pos)
        elif back and typ == "open" and not st["dur"].get("est"):
            # a 直到按下計圈 recovery of a hill repeat (「走下坡」): you press the lap at the bottom — walked
            # all the way down, however long that takes (SP-639 Q1-3: only a timed one can stop part-way)
            pos, used_t, used_d, gn, ls, pts = w.move(pos, -1, v, dist=max(0.0, pos - rs["base"]), floor=rs["base"])
        else:
            pos, used_t, used_d, gn, ls, pts = w.move(pos, -1 if back else 1, v, secs=max(0.0, s),
                                                      floor=rs["base"] if back else 0.0)
        if rs is not None and rs["hill"] is None and st["kind"] in ("work", "other"):
            a, b = min(start, w.end), min(pos, w.end)
            rs["hill"] = b > a and (w.z_at(b) - w.z_at(a)) / ((b - a) * 1000.0) >= HILL_REP_GRADE
        for i, (pd, pp, pt) in enumerate(pts):
            path.append((D + pd, pp, T + pt, i == len(pts) - 1))
        D += used_d
        T += used_t
        far = max(far, start, pos)
        xs.append(round(D, 3))                   # one bound per step, a zero-length one included
        xt.append(round(T, 1))
        ss.append(round(used_t, 1))
        ups.append(round(gn, 1))
        downs.append(round(ls, 1))
        rr.append([round(start, 3), round(pos, 3)])
    if D <= 0:
        return None
    end = w.end
    pd_, pz, pt_ = [], [], []
    keep = []
    for d_, p_, t_, k_ in path:
        on = p_ <= end + 1e-6
        if not on and pz and pz[-1] is None:
            continue                              # one break per stretch off the route
        pd_.append(d_)
        pz.append(round(w.z_at(p_), 1) if on else None)
        pt_.append(t_)
        keep.append(k_)
    while pz and pz[-1] is None:
        pd_.pop(), pz.pop(), pt_.pop(), keep.pop()
    if len(pd_) < 2:
        return None
    step = max(1, -(-len(pd_) // PATH_OUT))
    idx = [i for i in range(len(pd_)) if i % step == 0 or keep[i] or pz[i] is None or i == len(pd_) - 1]
    complete = far >= end - 1e-6
    drawn = [v for v in pz if v is not None]
    return {"x": xs, "xt": xt, "s": ss, "up": ups, "down": downs, "r": rr,
            "d": [round(pd_[i], 3) for i in idx], "z": [pz[i] for i in idx], "t": [round(pt_[i]) for i in idx],
            # the scale of the drawn part (a hill repeat on a long route uses its first km only)
            "z_min": min(drawn), "z_max": max(drawn), "route_km": prof.get("route_km") or end,
            "km": round(min(far, end), 2), "total_km": round(D, 2), "complete": complete,
            "gain_m": prof.get("gain_m"), "name": prof.get("filename") or prof.get("name") or "", "note": _(ELEV_NOTE)}


# ---------------------------------------------------------------------------
# the 插入範本 menu (workout_steps.templates)
# ---------------------------------------------------------------------------

def row(t: dict, qbasis: Optional[str] = None) -> dict:
    """One 插入範本 row (the shape of workout_templates.row): key user:<id>, its family (強度課)
    and trail kind (越野跑) for the sub-tabs, the 目標用 and the GPX it carries. `qbasis`: a
    強度課's 「自動」 for the 主課強度 filter (workout_steps.quality_auto_basis; None: read now)."""
    from backend.engine import workout_steps as WS
    from backend.engine import workout_templates as WT
    full = (t.get("steps") or {}).get("items") or []
    fam = WT.family_of(full)
    r = {"key": f"user:{t['id']}", "id": t["id"], "label": t["name"], "title": t["name"],
         "src": t.get("note") or _(SRC), "url": "", "src_kind": "mine", "conv": "", "note": t.get("note") or "",
         "items": WT.main_of(full) or full, "full": full, "equiv": None, "family": fam,
         "fam_sub": fam["id"] if fam else None, "trail_sub": WT.trail_type_of(full),
         "descent_only": WT.descent_only(full),
         "purpose": "", "cats": t.get("cats") or [], "target_basis": t.get("target_basis"), "mine": True,
         "gpx": t.get("gpx"), "role": WS.rpe_role(full)}
    r["target_types"] = WS.template_target_types(r, qbasis=qbasis)   # SP-84: the same helper as 插入範本
    return r


def groups(templates: list[dict], qbasis: Optional[str] = None) -> list[dict]:
    """「我的範本」 groups, one per category (and 強度課 / 越野跑 sub-tab) a template is in; a
    強度課 template with no interval family is listed under every 強度課 sub-tab (sub None)."""
    out: dict = {}
    for t in templates:
        r = row(t, qbasis)
        for c in r["cats"]:
            sub = r["fam_sub"] if c == "quality" else r["trail_sub"] if c == "trail" else None
            g = out.setdefault((c, sub), {"group": _(MINE), "title": _(MINE), "cat": c, "sub": sub, "mine": True,
                                          "rows": []})
            g["rows"].append({**r, "sub": sub})
    return list(out.values())
