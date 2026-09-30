"""
Race-power API — the 賽事功率 page (docs/research/superpower-calculator.md).

    GET  /inputs        athlete-derived inputs (CP / W′ / TTE sources, RE, k, EP/h, conditions)
    GET  /peaks         百岳 / 小百岳 list (backend/data/baiyue.json)
    GET  /weather       race-day conditions via CWA → Open-Meteo → climatology → manual
    GET  /weather/key   CWA key status (masked);  POST /weather/key  save it
    POST /predict       road / trail / 百岳 prediction; every input overridable
    GET  /page          the HTML page
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
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
    w_prime = body.w_prime or (srcs.get(sid) or {}).get("w_prime") or acts.get("w_prime")
    used["w_prime"] = _src(w_prime, "手動" if body.w_prime else "活動擬合")
    tte = body.tte or d["tte"]["value"]
    used["tte"] = _src(tte, "手動" if body.tte else d["tte"]["source"])

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
                 "label": f"{a['label']}（自動：一年內最快的標準距離跑步，未必是比賽）"}
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
    h = d["hiking"]
    if body.eph:
        eph, src = body.eph, "手動"
    elif h.get("eph"):
        eph = h["eph"]["median"]
        src = f"你的登山日 EP/h 中位數（{h['eph']['n']} 天，爬升 ≥ 600 m 的日子權重 3 倍）"
    else:
        raise HTTPException(400, "沒有登山紀錄可推 EP/h：請手動輸入")
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
