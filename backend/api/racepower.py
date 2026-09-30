"""
Race-power API — the 賽事功率 page (docs/research/superpower-calculator.md).

    GET  /inputs        athlete-derived inputs (CP / W′ / TTE sources, RE, k, EP/h, conditions)
    GET  /peaks         百岳 / 小百岳 list (backend/data/baiyue.json)
    GET  /weather       race-day conditions via CWA → Open-Meteo → climatology → manual
    GET  /weather/key   CWA key status (masked);  POST /weather/key  save it
    POST /predict       road / trail / 百岳 prediction; every input overridable
    GET  /page          the HTML page

v2 (docs/research/racepower-v2.md §10.2):
    POST /course        upload .gpx / .fit → course_id + segments + profile
    POST /plan          three modes on a course; segments, effort bar, cross-checks
    GET  /grade-model   personal RE(g), v_max(g), v_h(g)
    GET  /backtest      stored leave-one-out back-test;  POST /backtest/run  recompute
    POST /export/coros  plan → COROS structured workout (preview, or push=true)
    POST /export/csv    plan → CSV (UTF-8 BOM), header block + one row per segment
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.engine.algorithms.effort import SIMPLE_FORMULAS
from backend.engine.planning import PLAN_PATH
from backend.engine.racepower import env as ENV
from backend.engine.racepower import predict as PR
from backend.engine.racepower import re as RE
from backend.engine.racepower import riegel as R
from backend.engine.racepower import weather as WX
from backend.engine.zones import zones_json

STATIC = Path(__file__).resolve().parents[1] / "static"
router = APIRouter(prefix="/api/v1/racepower", tags=["racepower"])

DEFAULT_K = -0.07
INPUTS_TTL_S = 600.0
_lock = threading.Lock()
_cache: dict = {}


def _dataset():
    from backend.api.wko5views import _dataset as ds
    return ds()


def _plan_stamp() -> float:
    try:
        return PLAN_PATH.stat().st_mtime
    except OSError:
        return 0.0


def inputs(refresh: bool = False) -> dict:
    """derive() for today, memoised per dataset / plan / day for 10 minutes."""
    from backend.engine.racepower.athlete import derive
    ds = _dataset()
    today = dt.date.today()
    key = (id(ds), today, _plan_stamp())
    with _lock:
        hit = _cache.get("inputs")
        if not refresh and hit and hit[0] == key and time.time() - hit[1] < INPUTS_TTL_S:
            return hit[2]
        d = derive(ds, today)
        _cache["inputs"] = (key, time.time(), d)
        return d


@router.get("/inputs")
def get_inputs(refresh: bool = False):
    return inputs(refresh)


@router.get("/peaks")
def peaks(q: Optional[str] = None, baiyue_only: bool = False):
    ps = WX.load_peaks()
    if baiyue_only:
        ps = [p for p in ps if p.get("baiyue")]
    if q:
        ps = [p for p in ps if q in p["name"]]
    return {"count": len(ps), "peaks": ps}


# ---------------------------------------------------------------------------
# weather
# ---------------------------------------------------------------------------

def _event(eid: str):
    from backend.engine.planning import Plan
    for e in Plan.load().events:
        if e.id == eid:
            return e
    raise HTTPException(404, f"no event {eid}")


@router.get("/weather")
def weather(date: Optional[str] = None, days: int = 1, event_id: Optional[str] = None,
            peak: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None,
            elevation: Optional[float] = None, cwa: bool = True):
    name = None
    if event_id:
        e = _event(event_id)
        date = date or e.date
        days = e.days or days
        pk = WX.find_peak(e.name)
        if pk:
            peak = peak or pk["name"]
        name = e.name
    if peak:
        pk = WX.find_peak(peak)
        if pk is None:
            raise HTTPException(404, f"找不到山名 {peak}")
        name = pk["name"]
        lat = pk["lat"] if lat is None else lat
        lon = pk["lon"] if lon is None else lon
        elevation = pk["elevation_m"] if elevation is None else elevation
    if not date:
        raise HTTPException(400, "date is required")
    try:
        d = dt.date.fromisoformat(date[:10])
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")
    r = WX.race_conditions(date=d, days=max(1, days), lat=lat, lon=lon, elevation_m=elevation,
                           name=name, use_cwa=cwa)
    r["peak"] = WX.find_peak(name) if name else None
    return r


class KeyIn(BaseModel):
    key: str = ""


@router.get("/weather/key")
def key_status():
    return WX.key_status()


@router.post("/weather/key")
def save_key(body: KeyIn):
    k = body.key.strip()
    if k and (len(k) < 10 or len(k) > 80 or any(c.isspace() for c in k)):
        raise HTTPException(400, "授權碼格式不對")
    WX.save_key(k)
    return WX.key_status()


# ---------------------------------------------------------------------------
# prediction
# ---------------------------------------------------------------------------

class EnvIn(BaseModel):
    altitude_m: Optional[float] = None
    temp_c: Optional[float] = None
    rh_pct: Optional[float] = None


class DayIn(BaseModel):
    km: float
    gain_m: float = 0.0
    loss_m: Optional[float] = None


class PriorIn(BaseModel):
    distance_km: float
    time_s: float
    power: float
    label: Optional[str] = None


class PredictIn(BaseModel):
    type: Literal["road", "trail", "baiyue"] = "road"
    distance_km: float = Field(gt=0)
    gain_m: float = 0.0
    loss_m: Optional[float] = None
    days: int = 1
    date: Optional[str] = None
    target_time_s: Optional[float] = None
    pack_kg: Optional[float] = None
    hist_pack_kg: float = 5.0
    day_plan: Optional[list[DayIn]] = None
    cp_source: Optional[str] = None
    cp: Optional[float] = None
    w_prime: Optional[float] = None
    tte: Optional[float] = None
    k: Optional[float] = None
    k_source: Optional[Literal["personal", "table", "manual"]] = None
    re: Optional[float] = None
    weight: Optional[float] = None
    eph: Optional[float] = None
    effort_formula: str = "fitted_run"
    env_from: Optional[EnvIn] = None
    env_to: Optional[EnvIn] = None
    prior: Optional[PriorIn] = None


def _src(value, source, **extra):
    return {"value": value, "source": source, **extra}


@router.post("/predict")
def predict(body: PredictIn):
    d = inputs()
    used, warnings = {}, []
    weight = body.weight or d["weight"]["value"]
    used["weight"] = _src(weight, "手動" if body.weight else d["weight"]["source"])

    # environment
    tc = d["training_conditions"]
    frm = {k: (getattr(body.env_from, k) if body.env_from and getattr(body.env_from, k) is not None
               else tc.get(k)) for k in ("altitude_m", "temp_c", "rh_pct")}
    to = body.env_to.model_dump() if body.env_to else {}
    env = ENV.multiplier(frm, to)
    m = env["M"]

    if body.type == "baiyue":
        return _predict_baiyue(body, d, weight, env, used, warnings)

    # CP
    srcs = {s["id"]: s for s in d["cp"]["sources"]}
    sid = body.cp_source if body.cp_source in srcs else d["cp"]["default"]
    if body.cp:
        cp, cp_src = body.cp, "手動"
    elif sid:
        cp, cp_src = srcs[sid]["cp"], srcs[sid]["label"]
    else:
        raise HTTPException(400, "沒有 CP：請手動輸入")
    used["cp"] = _src(cp, cp_src, id="manual" if body.cp else sid)
    acts = d["cp"]["activities"] or {}
    s_ = {} if body.cp else (srcs.get(sid) or {})
    w_src = "手動" if body.w_prime else (s_.get("short_label") if s_.get("w_prime") and s_.get("short_label") else
                                        "來源自帶" if s_.get("w_prime") else "活動擬合")
    w_prime = body.w_prime or s_.get("w_prime") or acts.get("w_prime")
    used["w_prime"] = _src(w_prime, w_src)
    tte = body.tte or s_.get("tte") or d["tte"]["value"]
    used["tte"] = _src(tte, "手動" if body.tte else ("PD 模型重算的 TTE" if s_.get("fit") or s_.get("base") == "pdmodel"
                                                    else "來源自帶" if s_.get("tte") else d["tte"]["source"]))
    # the short-range (F2) CP of a two-anchor source (PD model mFTP + test CP)
    used["cp2"] = _src(s_.get("cp2"), s_.get("short_label"))
    if d["cp"].get("lower_bound_message") and not body.cp:
        warnings.append(d["cp"]["lower_bound_message"])

    # effort distance & RE
    trail = body.type == "trail"
    fx = body.effort_formula if body.effort_formula in SIMPLE_FORMULAS else "fitted_run"
    divisor = SIMPLE_FORMULAS[fx][0] if trail else None
    d_eff_km = RE.effort_km(body.distance_km, body.gain_m, divisor) if trail else body.distance_km
    race_cvi = RE.cvi(body.gain_m, body.distance_km)
    if body.re:
        re_v, re_src = body.re, "手動"
    elif trail:
        s = d["re"]["trail"].get(fx)
        if not s:
            raise HTTPException(400, "沒有越野 RE：請手動輸入")
        re_v, re_src = s["median"], f"你的越野跑 RE 中位數（{s['n']} 次，effort km = km + 爬升/{divisor:g}）"
    else:
        s = d["re"]["road"]
        if not s:
            raise HTTPException(400, "沒有路跑 RE：請手動輸入")
        base_cvi = (d["re"]["road_cvi"] or {}).get("median") or 0.0
        adj = RE.cvi_adjust(base_cvi, race_cvi or 0.0)
        re_v = s["median"] + adj
        re_src = f"你的平路 RE 中位數（{s['n']} 次）" + (f"，CVI 調整 {adj:+.2f}" if adj else "")
    used["re"] = _src(re_v, re_src)

    # Riegel k
    target_m = d_eff_km * 1000.0
    prior = body.prior.model_dump() if body.prior else None
    if prior is None and d.get("auto_prior"):
        a = d["auto_prior"]
        prior = {"distance_km": a["km"], "time_s": a["time_s"], "power": a["avg_power"],
                 "label": f"{a['label']}（自動：一年內心率判定為比賽強度的標準距離跑步）"}
    elif prior is None:
        warnings.append("沒有比賽強度的標準距離紀錄可當查表依據：k 用預設 −0.07（≈ Stryd 比賽功率表）")
    tk = R.table_k(target_m, prior["distance_km"] * 1000.0, prior["time_s"]) if prior else None
    pr = d.get("riegel") or {}
    ksrc = body.k_source or ("manual" if body.k is not None else None)
    if ksrc == "manual" and body.k is not None:
        k, k_label = body.k, "手動"
    elif ksrc == "personal" and pr.get("k") is not None:
        k, k_label = pr["k"], "個人擬合"
    elif ksrc == "table" and tk and tk.get("k") is not None:
        k, k_label = tk["k"], "查表"
    elif ksrc is None and pr.get("valid"):
        k, k_label, ksrc = pr["k"], "個人擬合", "personal"
    elif tk and tk.get("k") is not None:
        k, k_label, ksrc = tk["k"], "查表", "table"
        if body.k_source is None and pr.get("k") is not None and not pr.get("valid"):
            warnings.append("個人 Riegel k 不可靠（" + "；".join(pr.get("invalid_reasons") or []) + "），改用查表 k")
    else:
        k, k_label, ksrc = DEFAULT_K, "預設 −0.07", "manual"
    used["k"] = _src(k, k_label, kind=ksrc)
    if not body.cp:
        # never predict below a power the athlete already held: the bound for THIS k
        from backend.engine.racepower.athlete import enforce_lower_bound
        cp_eff, lb_k = enforce_lower_bound(d, cp, w_prime, tte, k, (used.get("cp2") or {}).get("value"))
        if cp_eff > cp:
            warnings.append(f"k {k:+.2f} 下，CP {cp:.0f} W 撐不住你 {lb_k['t_s'] / 60:.0f} 分鐘 {lb_k['p']:.0f} W 的紀錄："
                            f"提高到 {cp_eff:.0f} W")
            cp = cp_eff
            used["cp"] = _src(cp, f"{used['cp']['source']}；依 k {k:+.2f} 提高到下限", id=used["cp"].get("id"))
    if tk and tk.get("warning"):
        warnings.append("查表 k：" + tk["warning"])

    res = PR.predict_run(distance_km=body.distance_km, cp=cp, tte=tte, k=k, re=re_v, weight=weight, m=m,
                         gain_m=body.gain_m, effort_divisor=divisor, target_time_s=body.target_time_s,
                         w_prime=w_prime, longest_effort_s=pr.get("longest_s"),
                         road_re=(d["re"]["road"] or {}).get("median"),
                         # the CVI that road RE was measured at (its own flat runs),
                         # not the all-run training CVI — §1.3 adjusts from there
                         train_cvi=(d["re"]["road_cvi"] or {}).get("median"))
    if trail:
        res["ep_itra_per_h"] = RE.effort_km(body.distance_km, body.gain_m, 100.0) / (res["time_s"] / 3600.0)
        res["effort_km_itra"] = RE.effort_km(body.distance_km, body.gain_m, 100.0)
        warnings.append("爬坡功率上限 110 % 是經驗法則（非研究結論）；下坡讓功率自然掉下來")
    if race_cvi is not None and not trail and race_cvi >= 25:
        warnings.append(f"路線 CVI {race_cvi:.0f}（丘陵），已用 CVI 調整 RE；起伏很大的路線請改用「越野」")

    tasks = {}
    if prior:
        tasks = {"prior": prior,
                 "task7_cp": R.cp_from_prior(prior["power"], prior["time_s"], tte, k),
                 "task9_power": R.power_from_prior_time(prior["power"], prior["time_s"], res["time_s"], k) * m,
                 "task10": {k_: (v * m if k_ in ("power", "power_workbook") else v)
                            for k_, v in R.power_from_prior_distance(prior["power"], prior["distance_km"],
                                                                     d_eff_km, k).items()},
                 "table": tk}
    return {"type": body.type, "used": used, "env": env, "result": res, "tasks": tasks,
            "zones": zones_json(cp), "warnings": res.pop("warnings") + warnings}


def _predict_baiyue(body: PredictIn, d: dict, weight: float, env: dict, used: dict, warnings: list):
    from backend.engine.racepower import hike as HK
    h = d["hiking"]
    if body.eph:
        eph, src = body.eph, "手動"
    elif h.get("eph"):
        eph = h["eph"]["median"]
        src = f"你自己走的登山日 EP/h 中位數（{h['eph']['n']} 天，爬升 ≥ 600 m 的日子權重 3 倍）"
    else:
        cap = None
        try:
            cap = _grade_models().get("walk_capacity")
        except Exception:                   # noqa: BLE001
            cap = None
        if cap is not None:
            from backend.engine.racepower import capacity as CAP
            # at the v1 reference pack: predict_baiyue then applies its own
            # pack factor (W + hist)/(W + pack) and the altitude M
            eph = CAP.course_eph(cap, body.distance_km, body.gain_m, body.loss_m, body.hist_pack_kg)
            src = ("推估：你的步行能力模型在這條路線的 EP/h（越野走路窗 + 百岳心率窗，AeT；" +
                   (h.get("note") or "百岳多為跟團") + "）")
            warnings.append("整趟時間是推估：" + (h.get("note") or "") + "；用你的步行能力模型（待回測）")
        else:
            eph = HK.tobler_eph(body.distance_km, body.gain_m, body.loss_m)
            src = "推估：Tobler 步行函數在這條路線的 EP/h（" + (h.get("note") or "百岳多為跟團") + "）"
            warnings.append("整趟時間是推估：" + (h.get("note") or "") + "；沒有跑步資料可建能力模型，用 Tobler 步行函數")
    used["eph"] = _src(eph, src)
    days = max(1, body.days or 1)
    pack = body.pack_kg if body.pack_kg is not None else (12.0 if days >= 2 else 6.0)
    used["pack_kg"] = _src(pack, "手動" if body.pack_kg is not None else ("預設 2–3 天 12 kg" if days >= 2 else "預設單日 6 kg"))
    used["hist_pack_kg"] = _src(body.hist_pack_kg, "假設：過去登山日多為輕裝（約 5 kg）")
    plan = PR.split_days(days, body.distance_km, body.gain_m, body.loss_m,
                         [x.model_dump() for x in body.day_plan] if body.day_plan else None)
    aet = d["aet"].get("aet")
    used["aet"] = _src(aet, d["aet"].get("source"))
    big = h.get("biggest")
    res = PR.predict_baiyue(day_plan=plan, eph=eph, weight=weight, m=env["M"], pack_kg=pack,
                            hist_pack_kg=body.hist_pack_kg, aet=aet,
                            target_moving_h=(body.target_time_s / 3600.0) if body.target_time_s else None,
                            biggest_day=big)
    if not body.day_plan and days > 1:
        warnings.append("沒有每日行程：距離與爬升平均分配到每一天；實際行程請逐日輸入")
    return {"type": "baiyue", "used": used, "env": env, "result": res, "tasks": {},
            "biggest_day": big, "zones": [], "warnings": res.pop("warnings") + warnings}


@router.get("/page", include_in_schema=False)
def page():
    return FileResponse(STATIC / "racepower.html")


# ---------------------------------------------------------------------------
# v2: course upload, plan, grade model, back-test, COROS export.
# /predict above is kept unchanged (v1); /plan calls it for the baseline.
# ---------------------------------------------------------------------------

COURSE_CACHE_MAX = 20
_courses: "OrderedDict[str, object]" = OrderedDict()
_courses_lock = threading.Lock()


def _py(o):
    """numpy scalars → Python, recursively (FastAPI cannot encode np.bool_)."""
    import math

    import numpy as np
    if isinstance(o, dict):
        return {k: _py(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_py(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def _course_opts(c: dict) -> dict:
    out = {}
    for k in ("sigma_m", "eps_m", "min_len_m", "flat_pct", "official_gain_m"):
        v = c.get(k)
        if v is not None and v != "":
            out[k] = float(v)
    if c.get("split") in ("grade", "km", "none"):
        out["split"] = c["split"]
    return out


def _build(track, opts: dict) -> dict:
    from backend.engine.racepower import course as CO
    try:
        return CO.build_course(track, **opts)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/course")
async def upload_course(file: UploadFile = File(...), sigma_m: Optional[float] = Form(None),
                        eps_m: Optional[float] = Form(None), min_len_m: Optional[float] = Form(None),
                        flat_pct: Optional[float] = Form(None), split: Optional[str] = Form(None),
                        official_gain_m: Optional[float] = Form(None)):
    """Parse a .gpx / .fit once and cache the Track (LRU 20, by content sha1);
    later plans send only the course_id and re-segment with their options."""
    from backend.engine.racepower import gpx as GPX
    data = await file.read(GPX.MAX_BYTES + 1)
    try:
        track = GPX.parse(data, file.filename or "")
    except GPX.GpxError as e:
        raise HTTPException(400, str(e))
    cid = hashlib.sha1(data).hexdigest()
    with _courses_lock:
        _courses[cid] = track
        _courses.move_to_end(cid)
        while len(_courses) > COURSE_CACHE_MAX:
            _courses.popitem(last=False)
    c = _build(track, _course_opts({"sigma_m": sigma_m, "eps_m": eps_m, "min_len_m": min_len_m,
                                    "flat_pct": flat_pct, "split": split, "official_gain_m": official_gain_m}))
    return _py({"course_id": cid, "name": track.name or file.filename, **c})


def _grade_models() -> dict:
    """GradeRE + HikeSpeed + the hike moving-ratio rows, memoised like /inputs."""
    from backend.engine.achievements import KIND_HIKE, build_achievements
    from backend.engine.racepower import athlete as A
    ds = _dataset()
    today = dt.date.today()
    key = (id(ds), today)
    with _lock:
        hit = _cache.get("grade")
        if hit and hit[0] == key and time.time() - hit[1] < INPUTS_TTL_S:
            return hit[2]
    from backend.engine.racepower import backtest as BT
    road = (inputs()["re"]["road"] or {}).get("median")
    gm = A.grade_models(ds, today, re_flat=road)
    # the race-like class's own RE(g) only when the back-test showed it clearly better
    gm["race_model"] = BT.class_model_flag()
    if gm["race_model"]:
        gm["grade_re"] = A.grade_models(ds, today, re_flat=road, classes=gm.get("classes"),
                                        only_classes={"race"}, hikes=False)["grade_re"]
    solo = A.solo_hikes()
    try:
        # the clock ETA's moving ratio, per trip kind: group hikes rest on the
        # group's schedule, solo ones on the athlete's (baiyue §2.6)
        rows = [(a.id in solo, {"moving_s": a.moving_s, "elapsed_s": a.elapsed_s}) for a in build_achievements(ds)
                if a.kind == KIND_HIKE and not a.days]
        gm["moving_rows"] = [r for s, r in rows if s]
        gm["moving_rows_group"] = [r for s, r in rows if not s]
    except Exception:                       # noqa: BLE001
        gm["moving_rows"], gm["moving_rows_group"] = [], []
    cap = gm.get("walk_capacity")
    hc = (BT.load() or {}).get("hike_capacity") or {}
    if cap is not None and hc.get("sigma_loo"):
        # the back-test's held-out segment log-error SD (segment level: wider
        # than a whole day's, so conservative)
        cap.sigma_loo = float(hc["sigma_loo"])
        cap.sigma_loo_src = f"回測：留一的段時間對數誤差 SD（{hc.get('today')}）"
    with _lock:
        _cache["grade"] = (key, time.time(), gm)
    return gm


@router.get("/grade-model")
def grade_model():
    gm = _grade_models()
    cap = gm.get("walk_capacity")
    return _py({"grade_re": gm["grade_re"].to_json(), "hike_speed": gm["hike_speed"].to_json(),
                "walk_capacity": cap.to_json() if cap is not None else None,
                "hike_hr": gm.get("hike_hr"), "hike_basis": gm.get("hike_basis"), "race_model": gm.get("race_model")})


def heat_status_for(date: Optional[str]) -> dict:
    """engine/heat_data.status for today, projected to `date` (the race day)."""
    from backend.engine import heat_data as HD
    rd = None
    if date:
        try:
            rd = dt.date.fromisoformat(str(date)[:10])
        except ValueError:
            rd = None
    try:
        passive = HD.completed_passive_dates()
    except Exception:                       # noqa: BLE001
        passive = []
    return HD.status(dt.date.today(), rd, passive_dates=passive)


@router.get("/heat-status")
def heat_status(date: Optional[str] = None):
    """The heat-acclimation index S (engine/heat.py): today, its last 120
    days, and the race-day projection for `date` (推估)."""
    return _py(heat_status_for(date))


class HikeMetaIn(BaseModel):
    file: str
    pack_kg: Optional[float] = None


def _hike_rows() -> list[dict]:
    """Every hiking day with its pack (recorded or default) and the solo /
    group suggestion (capacity.classify_day; baiyue-from-running.md §2.6)."""
    from backend.engine.racepower import athlete as A
    from backend.engine.racepower import capacity as CAP
    ds = _dataset()
    today = dt.date.today()
    gm = _grade_models()
    cap = gm.get("walk_capacity")
    meta = A.hike_meta()
    solo = A.solo_hikes()
    hikes = {w.entry.file: w for w in A.hike_workouts(ds, today)}
    days = inputs().get("hiking", {}).get("days") or []
    out = []
    for d in days:
        w = hikes.get(d["file"])
        rec = meta.get(d["file"]) or {}
        default = CAP.PACK_DEFAULT_MULTI if (d.get("days") or 1) > 1 else CAP.PACK_DEFAULT_SINGLE
        sug = None
        if w is not None:
            th = A.thresholds_as_of(ds, w.entry.start.date())
            wins = [x for x in A.hike_samples(ds, [w]) if x["day"] == (d.get("day") or 1)]
            L = CAP.day_pack(rec.get("pack_kg", default), d.get("day") or 1)
            sug = CAP.classify_day(wins, th.get("aet"), cap, L)
        out.append({**{k: d.get(k) for k in ("date", "trip", "name", "day", "days", "km", "gain_m", "moving_h",
                                             "ep_per_h", "file", "peaks")},
                    "solo": d["file"] in solo, "pack_kg": rec.get("pack_kg"), "pack_default": default,
                    "suggest": sug})
    marked = [{"marked": "solo" if r["solo"] else "group", "suggest": (r["suggest"] or {}).get("suggest")}
              for r in out]
    return out, CAP.classifier_validation(marked)


@router.get("/hike-meta")
def get_hike_meta():
    rows, val = _hike_rows()
    return _py({"days": rows, "validation": val,
                "note": "「看起來是自己走」只是建議；確認後才寫進自己走的清單"})


@router.post("/hike-meta")
def post_hike_meta(body: HikeMetaIn):
    from backend.engine.racepower import athlete as A
    try:
        trips = A.set_hike_meta(body.file, body.pack_kg)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with _lock:
        _cache.pop("grade", None)
    return {"trips": trips}


class SoloHikesIn(BaseModel):
    files: list[str] = []


@router.get("/solo-hikes")
def get_solo_hikes():
    """Hikes opted in as solo (the athlete's own pace): only these calibrate
    EP/h, the walking model and the hike back-test."""
    from backend.engine.racepower import athlete as A
    return {"files": sorted(A.solo_hikes()), "note": A.GROUP_HIKE_NOTE}


@router.post("/solo-hikes")
def post_solo_hikes(body: SoloHikesIn):
    from backend.engine.racepower import athlete as A
    s = A.set_solo_hikes(body.files)
    with _lock:
        _cache.pop("inputs", None)
        _cache.pop("grade", None)
    return {"files": sorted(s), "note": A.GROUP_HIKE_NOTE}


class CourseRef(BaseModel):
    course_id: Optional[str] = None
    split: Optional[Literal["grade", "km", "none"]] = None
    sigma_m: Optional[float] = None
    eps_m: Optional[float] = None
    min_len_m: Optional[float] = None
    flat_pct: Optional[float] = None
    official_gain_m: Optional[float] = None
    manual: Optional[dict] = None           # {km, gain, loss, split}


class LockIn(BaseModel):
    seg: int
    power: float


class StopIn(BaseModel):
    km: float
    minutes: float = 0.0


class HourIn(BaseModel):
    t: str                                  # local clock 'YYYY-MM-DDTHH:MM' (UTC+8), as /weather returns it
    temp_c: float
    rh_pct: Optional[float] = None
    dew_c: Optional[float] = None


class PlanIn(PredictIn):
    distance_km: Optional[float] = None
    mode: Literal["time", "power", "auto"] = "auto"
    target_pace_s_per_km: Optional[float] = None
    target_power: Optional[float] = None
    target_pct_cp: Optional[float] = None
    power_is_training: bool = False
    effort_target: float = 1.0
    speed_factor: Optional[float] = None
    course: Optional[CourseRef] = None
    strategy: Optional[dict] = None         # {kind: even|negative|positive, amount}
    hills: Optional[dict] = None            # {up, down}
    acclimatisation: Optional[Literal["acclimatised", "partial", "unacclimatised"]] = None
    locks: list[LockIn] = []
    start_time: Optional[str] = None
    stops: list[StopIn] = []
    day_splits_km: list[float] = []
    terrain: dict = {}
    wbal: Optional[Literal["wko5", "skiba", "skiba_run"]] = None
    # per-segment heat: the /weather hourly rows of the event (CWA 3-day or
    # Open-Meteo); none, or no date / start time → the single To value
    hourly: Optional[list[HourIn]] = None
    hourly_heat: bool = True
    # heat acclimation (heat-acclimation.md §5.5): {"mode": auto|none|partial|acclimatised|custom, "s"}
    heat_acclimatisation: Optional[dict] = None
    # 百岳 capacity (baiyue-from-running.md §6.1)
    trip_kind: Optional[Literal["group", "solo"]] = None
    hr_band: Optional[Literal["aet", "cap"]] = None
    pack_kg_by_day: list[float] = []
    heat_ref_alt_m: Optional[float] = None  # the elevation the race-day temperature refers to


def _resolve_course(body: PlanIn) -> dict:
    from backend.engine.racepower import course as CO
    c = body.course
    if c and c.course_id:
        with _courses_lock:
            track = _courses.get(c.course_id)
        if track is None:
            raise HTTPException(410, "路線已過期（伺服器重啟過），請重新上傳 GPX")
        return {**_build(track, _course_opts(c.model_dump())), "name": track.name}
    man = (c.manual if c and c.manual else None) or {}
    km = float(man.get("km") or body.distance_km or 0)
    if km <= 0:
        raise HTTPException(400, "需要距離或 GPX 路線")
    gain = float(man.get("gain") if man.get("gain") is not None else body.gain_m or 0)
    loss = man.get("loss") if man.get("loss") is not None else body.loss_m
    split = man.get("split") or (c.split if c and c.split else "none")
    alt = body.env_to.altitude_m if body.env_to else None
    return CO.manual_course(km, gain, loss, "km" if split == "km" else "none", alt)


def _v1_for(body: PlanIn, course: dict) -> dict:
    """The v1 /predict response for the same inputs (baseline + cross-check).
    With a GPX course and no race-day altitude, the altitude is the course's
    (百岳: the top, as v1 uses the peak; runs: the distance-weighted mean)."""
    t = course["totals"]
    data = {k: v for k, v in body.model_dump().items() if k in PredictIn.model_fields}
    data.update(distance_km=t["km"], gain_m=t["gain_m"], loss_m=t.get("loss_m"))
    gpx = course.get("source") == "gpx"
    to = dict(data.get("env_to") or {})
    if gpx and to.get("altitude_m") is None:
        segs = course["segments"]
        to["altitude_m"] = t["z_max"] if body.type == "baiyue" else \
            sum(s["z_mean"] * s["dist_m"] for s in segs) / sum(s["dist_m"] for s in segs)
        data["env_to"] = to
    if body.type == "baiyue" and gpx:
        from backend.engine.racepower import course as CO
        pieces = CO.cut_at(course["segments"], body.day_splits_km)
        days = sorted({p["day"] for p in pieces})
        data["days"] = len(days)
        data["day_plan"] = [{"km": sum(p["dist_m"] for p in pieces if p["day"] == n) / 1000.0,
                             "gain_m": sum(p["gain_m"] for p in pieces if p["day"] == n),
                             "loss_m": sum(p["loss_m"] for p in pieces if p["day"] == n)} for n in days]
    if body.mode != "time":
        data["target_time_s"] = None
    return predict(PredictIn(**data))


def make_plan(body: PlanIn) -> dict:
    from backend.engine.racepower import backtest as BT
    from backend.engine.racepower import planner as PL
    course = _resolve_course(body)
    if body.type == "baiyue" and course.get("source") == "gpx" and not body.day_splits_km and (body.days or 1) > 1:
        # a multi-day trip without split points: cut the course into equal-km days
        km = course["totals"]["km"]
        body = body.model_copy(update={"day_splits_km": [km * i / body.days for i in range(1, body.days)]})
    v1 = _v1_for(body, course)
    validated, effort_ok = BT.flags()
    gm = _grade_models()
    opts = body.model_dump()
    opts["locks"] = [x.model_dump() for x in body.locks]
    opts["stops"] = [x.model_dump() for x in body.stops]
    opts["hourly"] = [x.model_dump() for x in body.hourly or []]
    if body.heat_acclimatisation:
        opts["heat_status"] = heat_status_for(body.date)
    if body.type == "baiyue" and body.heat_ref_alt_m is None and (body.env_to is None or body.env_to.temp_c is None):
        # no race-day temperature: env.resolve copied the training one, which
        # belongs to the training altitude — lapse from there, not from the peak
        opts["heat_ref_alt_m"] = v1["env"]["from"]["altitude_m"]
    try:
        if body.type == "baiyue":
            opts["moving_rows"] = gm.get("moving_rows") or []
            opts["moving_rows_group"] = gm.get("moving_rows_group") or []
            out = PL.plan_hike(v1=v1, course=course, hike_speed=gm["hike_speed"], inp=inputs(), opts=opts,
                               validated=validated, capacity=gm.get("walk_capacity"))
        else:
            gre = gm["grade_re"]
            if body.type == "road":
                # RE(0) is the CVI-adjusted road RE v1 uses
                gre = gre.with_flat(v1["used"]["re"]["value"]) if hasattr(gre, "with_flat") else \
                    dataclasses.replace(gre, re_flat=v1["used"]["re"]["value"])
            inp = inputs()
            cpd = inp.get("cp") or {}
            capacity = {"spread": cpd.get("spread"), "lower_bound": cpd.get("lower_bound"),
                        "message": cpd.get("lower_bound_message"),
                        "lthr": (inp.get("aet") or {}).get("lthr"), "aet": (inp.get("aet") or {}).get("aet")}
            out = PL.plan_run(v1=v1, course=course, grade_re=gre, opts=opts, validated=validated,
                              effort_validated=effort_ok, longest_s=(inp.get("riegel") or {}).get("longest_s"),
                              capacity=capacity)
    except ValueError as e:
        raise HTTPException(400, str(e))
    out.update(used=v1["used"], env=v1["env"], v1=v1, course_source=course.get("source"),
               course_id=body.course.course_id if body.course else None, course_name=course.get("name"))
    return out


@router.post("/plan")
def plan(body: PlanIn):
    return _py(make_plan(body))


@router.get("/backtest")
def backtest_result():
    from backend.engine.racepower import backtest as BT
    return {"state": BT.state(), "result": BT.load()}


@router.post("/backtest/run")
def backtest_run():
    from backend.engine.racepower import backtest as BT
    return {"started": BT.start_background(_dataset), "state": BT.state()}


class ExportIn(PlanIn):
    push: bool = False
    name: Optional[str] = None


def coros_payload(body: ExportIn, p: dict) -> dict:
    """Plan → COROS program through the existing mapping (coros_workouts
    Step / build_program): one time-based step per segment, power ± 3 %
    (百岳: heart rate ≤ AeT)."""
    from backend.engine.racepower import planner as PL
    from backend.sync import coros_workouts as CW
    th = CW.Thresholds(cp=(p["used"].get("cp") or {}).get("value"),
                       aet=(p["used"].get("aet") or {}).get("value"))
    if p["type"] == "baiyue":
        hr = CW.easy_hr(th)
        steps = [CW.Step(CW.EX_TRAIN, max(1, int(round(s["t"]))), hr,
                         f"{s['start_km']:.1f}-{s['end_km']:.1f}k {s['cls_label']}") for s in p["segments"]] \
            or [CW.Step(CW.EX_TRAIN, int(p["summary"]["time_s"]), hr, "心率 ≤ AeT")]
    else:
        steps = PL.coros_steps(p["segments"])
    badge = "（推估）" if p["summary"].get("badge") else ""
    name = body.name or f"{CW.NAME_PREFIX} 比賽配速 {p['summary']['km']:.0f}k"
    return CW.build_program(name, steps, th, f"{PL.HINT_30S}；分段目標{badge}")


async def push_to_coros(db, payload: dict, day: Optional[str]) -> dict:
    """Add the workout to the COROS library and, for a date not in the past,
    put it on that day (the existing Training Hub client)."""
    from backend.sync import coros_workouts as CW
    hub = await CW.TrainingHub.from_db(db)
    pid = await hub.add_program(payload)
    out = {"program_id": pid, "scheduled": None}
    if day and day[:10] >= dt.date.today().isoformat():
        detail = await hub.program_detail(pid)
        out["scheduled"] = await hub.schedule(detail, day[:10])
    return out


async def _db():
    from backend.db.database import get_db
    async for s in get_db():
        yield s


@router.post("/export/csv")
def export_csv(body: ExportIn):
    """The /plan output as CSV (UTF-8 with BOM for Excel): a header block
    (course totals, mode, CP / TTE / k sources, strategy, heat, date computed)
    and one row per segment. Same body as /plan; `name` names the file."""
    from urllib.parse import quote

    from fastapi.responses import Response

    from backend.engine.racepower import csvplan as CSV
    p = _py(make_plan(body))
    fname = CSV.filename(p, body.name, body.date)
    label = body.name or p.get("course_name") or f"{CSV.TYPE_LABEL.get(p['type'], '')} {p['summary']['km']:.1f} km"
    text = CSV.plan_csv(p, name=label, date=body.date, start_time=body.start_time,
                        stops=[x.model_dump() for x in body.stops],
                        acclimatisation=body.acclimatisation or ("unacclimatised" if body.type == "baiyue"
                                                                 else "acclimatised"))
    q = quote(fname)
    return Response(content=text.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=\"racepower.csv\"; filename*=UTF-8''{q}",
                             "X-Filename": q})


@router.post("/export/coros")
async def export_coros(body: ExportIn, db=Depends(_db)):
    from backend.sync import coros_workouts as CW
    p = make_plan(body)
    payload = coros_payload(body, p)
    payload = _py(payload)
    out = {"payload": payload, "steps": len(payload["exercises"]), "pushed": None}
    if body.push:
        try:
            out["pushed"] = await push_to_coros(db, payload, body.date)
        except CW.CorosAuthError as e:
            raise HTTPException(401, f"COROS 未登入：{e}")
        except CW.CorosError as e:
            raise HTTPException(502, f"COROS 回應錯誤：{e}")
    return out
