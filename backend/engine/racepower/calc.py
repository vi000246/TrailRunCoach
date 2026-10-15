"""
The race calculator's computations (POST /predict, /plan, /course/event/{id})
on an athlete context — the API's (api/racepower.py: the context reads the
Dataset / DB / files).

No FastAPI, no database here: everything the athlete's data decides comes from
`ctx` (see `Context`), failures raise `CalcError(status, detail)` (the API turns it
into an HTTPException with the same status and detail).
"""
from __future__ import annotations

import dataclasses
import math
from typing import Literal, Optional, Protocol

from pydantic import BaseModel, Field

from backend.engine import hints as H
from backend.engine.algorithms.effort import SIMPLE_FORMULAS
from backend.engine.racepower import env as ENV
from backend.engine.racepower import predict as PR
from backend.engine.racepower import re as RE
from backend.engine.racepower import riegel as R
from backend.engine.zones import zones_json
from backend.i18n import _

DEFAULT_K = -0.07


class CalcError(Exception):
    """An answer the API sends as HTTP `status` with `detail` (HTTPException)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class Context(Protocol):
    """What the computations read about the athlete (api/racepower.LiveContext)."""

    def inputs(self) -> dict: ...                       # athlete.derive() for today
    def grade_models(self) -> dict: ...                 # RE(g), hike speed, walking capacity, moving rows
    def flags(self) -> tuple[dict, bool]: ...           # backtest.flags()
    def heat_status(self, date: Optional[str]) -> dict: ...
    def hrc_test(self) -> Optional[dict]: ...
    def trail_hr(self) -> Optional[dict]: ...
    def hr_basis(self) -> Optional[dict]: ...           # max / rest HR, COROS account, 課表心率區間 (zonebar)
    def body(self) -> Optional[dict]: ...               # inputs()["body"], filled in
    def event(self, eid: str): ...                      # the plan event (.name, .days, .date); CalcError 404
    def event_track(self, eid: str): ...                # (course_id, Track, row) of its stored GPX, or None
    def event_splits(self, row: dict, days: int) -> list[float]: ...
    def event_meta(self, row: dict) -> Optional[dict]: ...
    def course_track(self, course_id: str): ...         # an uploaded Track still cached, or None


# ---------------------------------------------------------------------------
# request bodies
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
    re_unit: Optional[Literal["per_kg", "per_w"]] = None     # SP-444: the typed RE's unit (default per kg)
    weight: Optional[float] = None
    eph: Optional[float] = None
    effort_formula: str = "fitted_run"
    env_from: Optional[EnvIn] = None
    env_to: Optional[EnvIn] = None
    prior: Optional[PriorIn] = None


class EventCourseIn(BaseModel):
    split: Optional[Literal["grade", "km", "none"]] = None
    sigma_m: Optional[float] = None
    eps_m: Optional[float] = None
    min_len_m: Optional[float] = None
    flat_pct: Optional[float] = None
    official_gain_m: Optional[float] = None


class CourseRef(BaseModel):
    course_id: Optional[str] = None
    event_id: Optional[str] = None          # the course is a plan event's stored GPX: reloaded after a restart
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
    # aid-station editor (fuel.STOP_TYPES); the old 「km:分」 text has neither; sleep = a 連續 race's
    # sleep point (SP-114), its minutes in the ETA like any stop
    type: Optional[Literal["water", "aid", "big", "medical", "self", "sleep"]] = None
    name: Optional[str] = Field(None, max_length=40)


class HourIn(BaseModel):
    t: str                                  # local clock 'YYYY-MM-DDTHH:MM' (UTC+8), as /weather returns it
    temp_c: float
    rh_pct: Optional[float] = None
    dew_c: Optional[float] = None


class RainIn(BaseModel):
    """One /weather `rain` row (SP-249): a span in local clock time."""
    start: str
    end: str
    pop_pct: Optional[float] = None
    mm: Optional[float] = None


class WindIn(BaseModel):
    """One /weather `wind` row (SP-251): local clock time, km/h."""
    t: str
    temp_c: Optional[float] = None
    wind_kmh: Optional[float] = None
    gust_kmh: Optional[float] = None
    apparent_c: Optional[float] = None


class SunIn(BaseModel):
    """One /weather `sun` row (SP-254): local 'YYYY-MM-DDTHH:MM'."""
    date: str
    sunrise: str
    sunset: str
    src: Optional[str] = None


class WeatherPointIn(BaseModel):
    """SP-553: one /weather `points` row — a section of the course and its own forecast rows."""
    i: Optional[int] = None
    from_km: float
    to_km: float
    elevation_m: Optional[float] = None     # the height its rows refer to
    hourly: Optional[list[HourIn]] = None
    rain: Optional[list[RainIn]] = None
    wind: Optional[list[WindIn]] = None


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
    # "skiba" (cycling τ) is still accepted from old saved pages and mapped to "wko5" in the planner
    wbal: Optional[Literal["wko5", "skiba", "skiba_run"]] = None
    # per-segment heat: the /weather hourly rows of the event (CWA 3-day or
    # Open-Meteo); none, or no date / start time → the single To value
    hourly: Optional[list[HourIn]] = None
    hourly_heat: bool = True
    # SP-257: the /weather provider the To temperature / RH were filled from (the page sends it while
    # the athlete has not typed their own). "climatology": To is its daytime 06–18 mean, and a race
    # run outside those hours gets the mean over its hours on course instead (climatology_to)
    to_provider: Optional[str] = Field(None, max_length=32)
    # SP-249: the /weather `rain` rows → a reminder on trail / 百岳 plans; never changes the time
    rain: Optional[list[RainIn]] = None
    # SP-251: the /weather `wind` rows → wind chill and the 冷風 reminder; never changes the time
    wind: Optional[list[WindIn]] = None
    # SP-254: the /weather `sun` rows (else NOAA at the GPX start) → segments marked 夜間; the night
    # slowdown % (0 / 5 / 10 / 15, default 0, 推估) lengthens only their dark part
    sun: Optional[list[SunIn]] = None
    # SP-553: the /weather points along a long course (≤ weather.ROUTE_MAX_POINTS): each segment's heat,
    # rain, wind and 失溫 use the rows of the section it is in, read at its own clock
    weather_points: Optional[list[WeatherPointIn]] = Field(None, max_length=12)
    night_slow_pct: float = Field(0.0, ge=0.0, le=15.0)
    # heat acclimation (heat-acclimation.md §5.5): {"mode": auto|none|partial|acclimatised|custom, "s"}
    heat_acclimatisation: Optional[dict] = None
    # 百岳 capacity (baiyue-from-running.md §6.1)
    trip_kind: Optional[Literal["group", "solo"]] = None
    hr_band: Optional[Literal["aet", "cap"]] = None
    pack_kg_by_day: list[float] = []
    heat_ref_alt_m: Optional[float] = None  # the elevation the race-day temperature refers to
    # 路況 (SP-250): the race-day trail surface; used only when the model has the dry / wet split
    surface: Optional[Literal["dry", "wet"]] = None
    # SP-244: the plan event is marked 會用登山杖 → a hint on steep segments (seg_targets.pole_hint);
    # text only, no number changes
    poles: bool = False
    # SP-252: 百岳 積雪 (hike.SNOW) — × on the climbing legs; none / None = the snow-free time
    snow: Optional[Literal["none", "trodden", "untrodden"]] = None
    peak: Optional[str] = Field(None, max_length=40)   # the page's 地點 (山名): the 玉山 snow-season line
    # SP-578: the group's timetable (the Cairn import's points, when 「這是團的時刻表」 is ticked) → the
    # 跟團對照 (`group`), laid over the plan; never changes its numbers
    group_schedule: Optional[dict] = None
    # SP-580: a multi-day 百岳 trip's start time per day ('HH:MM', day 1 first; empty = start_time) —
    # the clock, the night marks and the cold / wind points of each day run from its own start
    day_starts: list[Optional[str]] = Field([], max_length=20)


class ExportIn(PlanIn):
    push: bool = False
    name: Optional[str] = None
    event_id: Optional[str] = None          # the plan event: one 課表 session per event (re-export overwrites it)
    # lap = open steps ended with the lap button (trail / 百岳 default: watch GPS drifts on trails);
    # distance = distance steps (road default)
    step_mode: Optional[Literal["lap", "distance"]] = None
    # 匯出至課表: overwrite an earlier export the user changed on the 課表 page (after the page asked)
    overwrite: bool = False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _src(value, source, **extra):
    return {"value": value, "source": source, **extra}


def py(o):
    """numpy scalars → Python, recursively; non-finite floats → None (FastAPI cannot encode them)."""
    import numpy as np
    if isinstance(o, dict):
        return {k: py(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [py(v) for v in o]
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def race_day(date: Optional[str]):
    """The race day of a body's `date` ('YYYY-MM-DD…'), None when missing or not a date."""
    import datetime as dt
    if not date:
        return None
    try:
        return dt.date.fromisoformat(str(date)[:10])
    except ValueError:
        return None


def course_opts(c: dict) -> dict:
    out = {}
    for k in ("sigma_m", "eps_m", "min_len_m", "flat_pct", "official_gain_m"):
        v = c.get(k)
        if v is not None and v != "":
            out[k] = float(v)
    if c.get("split") in ("grade", "km", "none"):
        out["split"] = c["split"]
    return out


def build(track, opts: dict) -> dict:
    from backend.engine.racepower import course as CO
    try:
        return CO.build_course(track, **opts)
    except ValueError as e:
        raise CalcError(400, str(e))


# ---------------------------------------------------------------------------
# v1: POST /predict
# ---------------------------------------------------------------------------

def predict(ctx: Context, body: PredictIn) -> dict:
    d = ctx.inputs()
    used, warnings = {}, []
    weight = body.weight or d["weight"]["value"]
    used["weight"] = _src(weight, _("手動") if body.weight else d["weight"]["source"])

    # environment
    tc = d["training_conditions"]
    frm = {k: (getattr(body.env_from, k) if body.env_from and getattr(body.env_from, k) is not None
               else tc.get(k)) for k in ("altitude_m", "temp_c", "rh_pct")}
    to = body.env_to.model_dump() if body.env_to else {}
    env = ENV.multiplier(frm, to)
    m = env["M"]

    if body.type == "baiyue":
        return predict_baiyue(ctx, body, d, weight, env, used, warnings)
    # SP-444: the derived RE is per kg with a body weight in the settings; without one it is speed ÷
    # watts (`re.unit`, RE.re_mass) and the power ↔ speed step divides by 1 — a weight typed into
    # 「你的數字」 then only gives the W/kg (never a default weight)
    re_unit = (d.get("re") or {}).get("unit") or RE.PER_KG

    # CP
    srcs = {s["id"]: s for s in d["cp"]["sources"]}
    sid = body.cp_source if body.cp_source in srcs else d["cp"]["default"]
    if body.cp:
        cp, cp_src = body.cp, _("手動")
    elif sid:
        cp, cp_src = srcs[sid]["cp"], srcs[sid]["label"]
    else:
        raise CalcError(400, _("沒有 CP：請手動輸入"))
    used["cp"] = _src(cp, cp_src, id="manual" if body.cp else sid)
    acts = d["cp"]["activities"] or {}
    s_ = {} if body.cp else (srcs.get(sid) or {})
    w_src = _("手動") if body.w_prime else (s_.get("short_label") if s_.get("w_prime") and s_.get("short_label") else
                                        _("來源自帶") if s_.get("w_prime") else _("活動擬合"))
    w_prime = body.w_prime or s_.get("w_prime") or acts.get("w_prime")
    used["w_prime"] = _src(w_prime, w_src)
    tte = body.tte or s_.get("tte") or d["tte"]["value"]
    used["tte"] = _src(tte, _("手動") if body.tte else (_("PD 模型重算的 TTE") if s_.get("fit") or s_.get("base") == "pdmodel"
                                                    else _("來源自帶") if s_.get("tte") else d["tte"]["source"]))
    # the short-range (F2) CP of a two-anchor source (PD model mFTP + test CP)
    used["cp2"] = _src(s_.get("cp2"), s_.get("short_label"))
    if d["cp"].get("lower_bound_message") and not body.cp:
        warnings.append(H.tag("RC16", d["cp"]["lower_bound_message"]))

    # effort distance & RE
    trail = body.type == "trail"
    fx = body.effort_formula if body.effort_formula in SIMPLE_FORMULAS else "fitted_run"
    from backend.engine.algorithms.effort import divisor_of
    divisor = divisor_of(fx) if trail else None
    d_eff_km = RE.effort_km(body.distance_km, body.gain_m, divisor) if trail else body.distance_km
    race_cvi = RE.cvi(body.gain_m, body.distance_km)
    if body.re:
        re_v, re_src = body.re, _("手動")
        typed_unit = body.re_unit or RE.PER_KG
        if typed_unit != re_unit:
            if not weight or weight <= 0:
                # a per-kg RE typed in, no weight anywhere to bring it to speed ÷ watts
                raise CalcError(400, _("手動填的 RE 是每公斤，沒有體重換算不了；請填體重，"
                                       "或不要手動填 RE（沒有體重時用你的速度／瓦）"))
            re_v = re_v / weight if re_unit == RE.PER_W else re_v * weight
    elif trail:
        s = d["re"]["trail"].get(fx)
        if not s:
            raise CalcError(400, _("沒有越野 RE：請手動輸入"))
        re_v, re_src = s["median"], _("你的越野跑 RE 中位數（{n} 次，effort km = km + 爬升/{div:g}）", n=s['n'], div=divisor)
    else:
        s = d["re"]["road"]
        if not s:
            raise CalcError(400, _("沒有路跑 RE：請手動輸入"))
        base_cvi = (d["re"]["road_cvi"] or {}).get("median") or 0.0
        adj = RE.cvi_adjust(base_cvi, race_cvi or 0.0)
        re_v = RE.with_cvi(s["median"], adj, re_unit)
        re_src = _("你的平路 RE 中位數（{n} 次）", n=s['n']) + (
            (_("，CVI 調整 {pct:+.0f} %（推估）", pct=adj * 100) if re_unit == RE.PER_W else
             _("，CVI 調整 {adj:+.2f}", adj=adj)) if adj else "")
    if re_unit == RE.PER_W:
        re_src += _("（速度／瓦：沒有體重）")
    used["re"] = _src(re_v, re_src, unit=re_unit)

    # Riegel k
    target_m = d_eff_km * 1000.0
    prior = body.prior.model_dump() if body.prior else None
    if prior is None and d.get("auto_prior"):
        a = d["auto_prior"]
        prior = {"distance_km": a["km"], "time_s": a["time_s"], "power": a["avg_power"],
                 "label": _("{label}（自動：一年內心率判定為比賽強度的標準距離跑步）", label=a['label'])}
    elif prior is None:
        warnings.append(H.tag("RC1", _("沒有比賽強度的標準距離紀錄可當查表依據：k 用預設 −0.07（≈ Stryd 比賽功率表）")))
    tk = R.table_k(target_m, prior["distance_km"] * 1000.0, prior["time_s"]) if prior else None
    pr = d.get("riegel") or {}
    ksrc = body.k_source or ("manual" if body.k is not None else None)
    if ksrc == "manual" and body.k is not None:
        k, k_label = body.k, _("手動")
    elif ksrc == "personal" and pr.get("k") is not None:
        k, k_label = pr["k"], _("個人擬合")
    elif ksrc == "table" and tk and tk.get("k") is not None:
        k, k_label = tk["k"], _("查表")
    elif ksrc is None and pr.get("valid"):
        k, k_label, ksrc = pr["k"], _("個人擬合"), "personal"
    elif tk and tk.get("k") is not None:
        k, k_label, ksrc = tk["k"], _("查表"), "table"
        if body.k_source is None and pr.get("k") is not None and not pr.get("valid"):
            warnings.append(H.tag("RC1", _("個人 Riegel k 不可靠（{why}），改用查表 k", why="；".join(pr.get("invalid_reasons") or []))))
    else:
        k, k_label, ksrc = DEFAULT_K, _("預設 −0.07"), "manual"
    used["k"] = _src(k, k_label, kind=ksrc)
    if not body.cp:
        # never predict below a power the athlete already held: the bound for THIS k
        from backend.engine.racepower.athlete import enforce_lower_bound
        cp_eff, lb_k = enforce_lower_bound(d, cp, w_prime, tte, k, (used.get("cp2") or {}).get("value"))
        if cp_eff > cp:
            warnings.append(H.tag("RC16", _("k {k:+.2f} 下，CP {cp:.0f} W 撐不住你 {min:.0f} 分鐘 {p:.0f} W 的紀錄："
                              "提高到 {cp_eff:.0f} W", k=k, cp=cp, min=lb_k['t_s'] / 60, p=lb_k['p'], cp_eff=cp_eff)))
            cp = cp_eff
            used["cp"] = _src(cp, _("{src}；依 k {k:+.2f} 提高到下限", src=used['cp']['source'], k=k), id=used["cp"].get("id"))
    if tk and tk.get("warning"):
        warnings.append(H.tag("RC1", _("查表 k：") + tk["warning"]))

    res = PR.predict_run(distance_km=body.distance_km, cp=cp, tte=tte, k=k, re=re_v, weight=weight, m=m,
                         gain_m=body.gain_m, effort_divisor=divisor, target_time_s=body.target_time_s,
                         w_prime=w_prime, longest_effort_s=pr.get("longest_s"),
                         road_re=(d["re"]["road"] or {}).get("median"),
                         # the CVI that road RE was measured at (its own flat runs),
                         # not the all-run training CVI — §1.3 adjusts from there
                         train_cvi=(d["re"]["road_cvi"] or {}).get("median"), re_unit=re_unit)
    if trail:
        res["ep_itra_per_h"] = RE.effort_km(body.distance_km, body.gain_m, 100.0) / (res["time_s"] / 3600.0)
        res["effort_km_itra"] = RE.effort_km(body.distance_km, body.gain_m, 100.0)
        warnings.append(H.tag("RC1", _("爬坡功率上限 110 % 是經驗法則（非研究結論）；下坡讓功率自然掉下來")))
    if race_cvi is not None and not trail and race_cvi >= 25:
        warnings.append(H.tag("RC1", _("路線 CVI {cvi:.0f}（丘陵），已用 CVI 調整 RE；起伏很大的路線請改用「越野」", cvi=race_cvi)))

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


def predict_baiyue(ctx: Context, body: PredictIn, d: dict, weight: float, env: dict, used: dict, warnings: list):
    from backend.engine.racepower import hike as HK
    h = d["hiking"]
    if body.eph:
        eph, src = body.eph, _("手動")
    elif h.get("eph"):
        eph = h["eph"]["median"]
        src = _("你自己走的登山日 EP/h 中位數（{n} 天，爬升 ≥ 600 m 的日子權重 3 倍）", n=h['eph']['n'])
    else:
        cap = None
        try:
            cap = ctx.grade_models().get("walk_capacity")
        except Exception:                   # noqa: BLE001
            cap = None
        if cap is not None:
            from backend.engine.racepower import capacity as CAP
            # at the v1 reference pack: predict_baiyue then applies its own
            # pack factor (W + hist)/(W + pack) and the altitude M
            eph = CAP.course_eph(cap, body.distance_km, body.gain_m, body.loss_m, body.hist_pack_kg)
            src = _("推估：你的步行能力模型在這條路線的 EP/h（越野走路窗 + 百岳心率窗，AeT；{note}）",
                    note=h.get("note") or _("百岳多為跟團"))
            warnings.append(H.tag("RC1", _("整趟時間是推估：{note}；用你的步行能力模型（待回測）", note=h.get("note") or "")))
        else:
            eph = HK.tobler_eph(body.distance_km, body.gain_m, body.loss_m)
            src = _("推估：Tobler 步行函數在這條路線的 EP/h（{note}）", note=h.get("note") or _("百岳多為跟團"))
            if not d["weight"]["value"]:
                # SP-414: the capacity model needs the settings weight (it is in W/kg)
                warnings.append(H.tag("RC1", _("整趟時間是推估：{note}；沒有體重，步行能力模型算不出，用 Tobler 步行函數",
                                  note=h.get("note") or "")))
            else:
                warnings.append(H.tag("RC1", _("整趟時間是推估：{note}；沒有跑步資料可建能力模型，用 Tobler 步行函數",
                                  note=h.get("note") or "")))
    used["eph"] = _src(eph, src)
    days = max(1, body.days or 1)
    from backend.engine.racepower import capacity as _cap
    if weight and weight > 0:
        default_pack = _cap.pack_default(weight, days)
        pack = body.pack_kg if body.pack_kg is not None else default_pack
        used["pack_kg"] = _src(pack, _("手動") if body.pack_kg is not None else _("預設背負 {pack}", pack=_cap.pack_default_text(weight)))
    else:
        # SP-414: no body weight — the pack factor (W + 5 kg)/(W + pack) is 1, no default pack, no energy
        pack = body.pack_kg
        used["pack_kg"] = _src(pack, _("手動（沒有體重：不影響速度）") if pack is not None
                               else _("沒有體重：背包預設算不出"))
        warnings.append(H.tag("RC28", _("沒有體重：背負係數取 1（速度不依背包重量調整），熱量、飲水算不出；"
                          "請到 設定 → 個人資料 填寫體重"), cls=1))
    used["hist_pack_kg"] = _src(body.hist_pack_kg, _("假設：過去登山日多為輕裝（約 5 kg）"))
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
        warnings.append(H.tag("RC8", _("沒有每日行程：距離與爬升平均分配到每一天；實際行程請逐日輸入")))
    return {"type": "baiyue", "used": used, "env": env, "result": res, "tasks": {},
            "biggest_day": big, "zones": [], "warnings": res.pop("warnings") + warnings}


# ---------------------------------------------------------------------------
# v2: courses and plans
# ---------------------------------------------------------------------------

def event_course(ctx: Context, eid: str, body: Optional[EventCourseIn] = None) -> dict:
    """POST /course/event/{eid}: the course of the GPX stored with a plan event."""
    from backend.engine.racepower import fuel as FU
    e = ctx.event(eid)
    got = ctx.event_track(eid)
    if got is None:
        raise CalcError(404, _("這場賽事沒有 GPX"))
    cid, track, row = got
    c = build(track, course_opts((body or EventCourseIn()).model_dump()))
    sug = FU.stops_from_wpts(c.get("wpts") or [], c["totals"]["km"])
    splits = ctx.event_splits(row, e.days or 1) if (e.days or 1) > 1 else []
    from backend.engine.racepower import overnight as OV
    return py(respond({"course_id": cid, "event_id": eid, "name": track.name or row.get("filename"), **c,
                       "stop_suggestions": sug, "day_splits_km": splits, "gpx": ctx.event_meta(row),
                       "overnights": OV.find(track),        # SP-534: asked about on the page, never applied here
                       "schedule": event_schedule(ctx, row, c["totals"]["km"])}))


def event_schedule(ctx: Context, row: dict, km: float) -> Optional[dict]:
    """SP-577: the Cairn timetable kept with the event's GPX as the import showed it (legs, the group's
    moving time, the answer to 「這是團的時刻表」); None without one."""
    from backend.engine.racepower import group as GR
    sc = ((row or {}).get("source") or {}).get("schedule")
    if not sc:
        return None
    try:
        rows = (ctx.grade_models() or {}).get("moving_rows_group") or []
    except Exception:                       # noqa: BLE001
        rows = []
    return GR.for_import(sc, km, rows)


def resolve_course(ctx: Context, body: PlanIn) -> dict:
    from backend.engine.racepower import course as CO
    c = body.course
    if c and (c.course_id or c.event_id):
        track = ctx.course_track(c.course_id) if c.course_id else None
        if track is None and c.event_id:
            got = ctx.event_track(c.event_id)
            track = got[1] if got else None
        if track is None:
            raise CalcError(410, _("路線已過期（伺服器重啟過），請重新上傳 GPX"))
        return {**build(track, course_opts(c.model_dump())), "name": track.name}
    man = (c.manual if c and c.manual else None) or {}
    km = float(man.get("km") or body.distance_km or 0)
    if km <= 0:
        raise CalcError(400, _("需要距離或 GPX 路線"))
    gain = float(man.get("gain") if man.get("gain") is not None else body.gain_m or 0)
    loss = man.get("loss") if man.get("loss") is not None else body.loss_m
    split = man.get("split") or (c.split if c and c.split else "none")
    alt = body.env_to.altitude_m if body.env_to else None
    return CO.manual_course(km, gain, loss, "km" if split == "km" else "none", alt)


def v1_for(ctx: Context, body: PlanIn, course: dict) -> dict:
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
        # SP-533: the trip's days stay what they are (make_plan has made the cuts fit them)
        days = range(1, len(body.day_splits_km or []) + 2)
        data["days"] = len(days)
        data["day_plan"] = [{"km": sum(p["dist_m"] for p in pieces if p["day"] == n) / 1000.0,
                             "gain_m": sum(p["gain_m"] for p in pieces if p["day"] == n),
                             "loss_m": sum(p["loss_m"] for p in pieces if p["day"] == n)} for n in days]
    if body.mode != "time":
        data["target_time_s"] = None
    return predict(ctx, PredictIn(**data))


CLIM_TO_PASSES = 3                # SP-257: re-solves with the on-course To (the ETAs move by seconds: 1 in practice)
CLIM_TO_TOL_C = 0.05              # … until the To read from the new ETAs moves less than this


def course_ground(ctx: Context, body: PlanIn) -> Optional[dict]:
    """SP-544: the course's stored surface (engine/course_surface.py, SP-543) with the user's marks,
    when TRC_TECH_DIRT_ONLY is on; None otherwise (the technicality on the whole course, as before)."""
    from backend.engine import course_surface as CS
    from backend.engine.racepower import surface as SF
    if not SF.tech_dirt_only() or not body.course:
        return None
    cid = body.course.course_id
    if not cid and body.course.event_id:
        got = ctx.event_track(body.course.event_id)
        cid = got[0] if got else None
    try:
        row = CS.get(cid) if cid else None
    except CS.CourseSurfaceError:
        return None
    if not row or not row.get("profile"):
        return None
    return SF.effective(row["profile"], row.get("overrides"), row.get("manual"))


def solve(ctx: Context, body: PlanIn, course: dict) -> dict:
    """The plan itself on a resolved course: v1 (the baseline / cross-check), the planner
    (plan_hike / plan_run, with the goal against the model in time / power mode) and the
    night marks; make_plan adds what is read from it (fuel, targets, reminders)."""
    from backend.engine.racepower import planner as PL
    v1 = v1_for(ctx, body, course)
    validated, effort_ok = ctx.flags()
    from backend.engine.racepower import night as NI
    stops_l = [x.model_dump() for x in body.stops]
    sun = [x.model_dump() for x in body.sun] if body.sun else \
        NI.course_sun(course, body.date, body.days or 1) if course.get("source") == "gpx" else None

    def night(p: dict, mode: str) -> dict:
        return NI.apply(p, date=body.date, start_time=body.start_time, stops=stops_l, sun=sun,
                        slow_pct=body.night_slow_pct, mode=mode)
    gm = ctx.grade_models()
    opts = body.model_dump()
    opts["locks"] = [x.model_dump() for x in body.locks]
    opts["stops"] = [x.model_dump() for x in body.stops]
    opts["hourly"] = [x.model_dump() for x in body.hourly or []]
    if body.type == "trail" and course.get("source") == "gpx":
        opts["ground"] = course_ground(ctx, body)
    if body.heat_acclimatisation:
        opts["heat_status"] = {**ctx.heat_status(body.date), "hrc_test": ctx.hrc_test()}
    if body.type == "baiyue" and body.heat_ref_alt_m is None and (body.env_to is None or body.env_to.temp_c is None):
        # no race-day temperature: env.resolve copied the training one, which
        # belongs to the training altitude — lapse from there, not from the peak
        opts["heat_ref_alt_m"] = v1["env"]["from"]["altitude_m"]
    try:
        if body.type == "baiyue":
            opts["moving_rows"] = gm.get("moving_rows") or []
            opts["moving_rows_group"] = gm.get("moving_rows_group") or []
            out = night(PL.plan_hike(v1=v1, course=course, hike_speed=gm["hike_speed"], inp=ctx.inputs(), opts=opts,
                                     validated=validated, capacity=gm.get("walk_capacity")), body.mode)
        else:
            gre = gm["grade_re"]
            if body.type == "road":
                # RE(0) is the CVI-adjusted road RE v1 uses
                gre = gre.with_flat(v1["used"]["re"]["value"]) if hasattr(gre, "with_flat") else \
                    dataclasses.replace(gre, re_flat=v1["used"]["re"]["value"])
            inp = ctx.inputs()
            cpd = inp.get("cp") or {}
            capacity = {"spread": cpd.get("spread"), "lower_bound": cpd.get("lower_bound"),
                        "message": cpd.get("lower_bound_message"),
                        "lthr": (inp.get("aet") or {}).get("lthr"), "aet": (inp.get("aet") or {}).get("aet"),
                        # SP-493: the 「LTHR」 cap under the 課表心率區間 in effect (zonebar.threshold_hr)
                        "thr": _thr(ctx, (inp.get("aet") or {}).get("lthr"))}
            th = ctx.trail_hr() if body.type == "trail" else None

            def run(o: dict, v: dict) -> dict:
                return night(PL.plan_run(v1=v, course=course, grade_re=gre, opts=o, validated=validated,
                                         effort_validated=effort_ok, longest_s=(inp.get("riegel") or {}).get("longest_s"),
                                         capacity=capacity, trail_hr=th), o.get("mode") or "auto")
            out = run(opts, v1)
            if body.mode in ("time", "power"):
                # the goal against the model's own prediction (auto, 100 %), same course and conditions
                from backend.engine.racepower import goal as GOAL
                ref = body.model_copy(update={"mode": "auto", "effort_target": 1.0})
                model = run({**opts, "mode": "auto", "effort_target": 1.0}, v1_for(ctx, ref, course))
                out["goal"] = GOAL.check(out["summary"]["time_s"], model["summary"]["time_s"], body.mode)
                out["goal"].update(model_power=model["summary"]["power"],
                                   model_pace_s_per_km=model["summary"]["pace_s_per_km"])
    except ValueError as e:
        raise CalcError(400, str(e))
    out.update(used=v1["used"], env=v1["env"], v1=v1, course_source=course.get("source"),
               course_id=body.course.course_id if body.course else None, course_name=course.get("name"))
    return out


def trip_days(body: PlanIn, course: dict) -> tuple[PlanIn, Optional[dict]]:
    """SP-533: a 百岳 GPX trip's day ends fit its days — the days the page sends are never rewritten
    by the split points (an n-day trip uses its points only when there are exactly n − 1; else
    equal km per day, 推估, with a warning). (body with days / day_splits_km set, the warning).
    A caller that sends split points without `days` (the API before SP-533) gets one day more
    than its points."""
    from backend.engine.racepower import course as CO
    given = [x for x in body.day_splits_km or []]
    days = body.days if "days" in body.model_fields_set or not given else len(given) + 1
    km = course["totals"]["km"]
    cuts, src = CO.day_cuts(given, km, days)
    warn = None
    n = max(1, int(days or 1))
    if src == "equal" and given:
        warn = H.tag("RC8", _("分日點 {k} 個，跟 {n} 天對不上（要 {m} 個）：先每天平均分距離（推估）。"
                             "改天數，或清除分日點重新點", k=len(given), n=n, m=n - 1))
    elif src == "equal":
        warn = H.tag("RC8", _("還沒有分日點：{n} 天先每天平均分距離（推估），攻頂日可能抓錯。"
                             "在剖面上點每天的終點，或點營地航點候選", n=n))
    return body.model_copy(update={"days": n, "day_splits_km": cuts}), warn


def make_plan(ctx: Context, body: PlanIn) -> dict:
    course = resolve_course(ctx, body)
    split_warn = None
    if body.type == "baiyue" and course.get("source") == "gpx":
        body, split_warn = trip_days(body, course)
    out = solve(ctx, body, course)
    if split_warn:
        out["warnings"].append(split_warn)
    # SP-257: no forecast — the climatology's daytime To widened to the hours on course, the plan
    # solved again with it (its ETAs, read again, give the To; a daytime race keeps its plan)
    base, clim, nxt = body, None, climatology_to(body, out)
    for _n in range(CLIM_TO_PASSES):
        if nxt is None or (clim is not None and abs(nxt["temp_c"] - clim["temp_c"]) < CLIM_TO_TOL_C):
            break
        clim = nxt
        body = base.model_copy(update={"env_to": base.env_to.model_copy(
            update={"temp_c": clim["temp_c"], "rh_pct": clim["rh_pct"]})})
        out = solve(ctx, body, course)
        nxt = climatology_to(base, out)
    if clim is not None and nxt is not None:
        # the To the plan was solved with; the hours on course of that plan
        clim = {**clim, "hours": nxt["hours"], "night_h": nxt["night_h"]}
    out["to_clim"] = clim
    if clim:
        out["warnings"].append(H.tag("RC29", _("沒有預報：比賽日溫度用賽道上 {h:.1f} 小時的氣候值 {t:.1f} °C／濕度 {rh:.0f} %"
                                 "（其中 {n:.1f} 小時在 06:00–18:00 以外，用同一時段的歷史平均；"
                                 "白天平均 {day:.1f} °C，推估）", h=clim["hours"], t=clim["temp_c"],
                                 rh=clim["rh_pct"], n=clim["night_h"], day=clim["day_temp_c"])))
    out["fuel"] = fuel(ctx, body, out)
    from backend.engine.racepower import night as NI
    from backend.engine.racepower import seg_targets as ST
    aet_d = ctx.inputs().get("aet") or {}
    thr = _thr(ctx, aet_d.get("lthr"))
    out["seg_targets"] = ST.plan_targets(out, aet=aet_d.get("aet"), lthr=aet_d.get("lthr"), thr=thr)
    # the main chart / table: pace, power and HR target per segment, null where not valid
    out["chart_rows"] = ST.chart_rows(out, aet=aet_d.get("aet"), lthr=aet_d.get("lthr"), poles=body.poles, thr=thr)
    if body.type != "baiyue":
        # the absolute-intensity bar's heart-rate version (SP-118; zonebar.py)
        from backend.engine.racepower import zonebar as ZB
        hb = getattr(ctx, "hr_basis", None)
        out["hr_bar"] = ZB.hr_bar(out, aet_d.get("lthr"), hb() if hb else None)
    # 「匯出至課表」 is one race-day session: a multi-day 百岳 trip is not exported (the reason, else None)
    from backend.engine.racepower import watch_export as WE
    out["export_block"] = WE.multi_day(out, body.start_time, body.days)
    out["rain"] = rain_reminder(body, out)
    out["cold_wind"], out["hypothermia"] = cold_reminders(body, out)
    # SP-305: the outdoor reminders of this race merged into one line for the attention box
    from backend.engine.racepower import cold as CD
    out["attention"] = CD.attention(out["cold_wind"], out["hypothermia"], NI.hint(out.get("night"), body.type))
    snow_season(body, course, out)
    from backend.engine.racepower import fuel as FU
    # SP-255: the sleep points' default minutes by the race hours and the 「凌晨小睡」 line
    out["sleep"] = FU.sleep_info(out, [x.model_dump() for x in body.stops], body.start_time)
    if course.get("source") == "gpx":
        out["stop_suggestions"] = FU.stops_from_wpts(course.get("wpts") or [], course["totals"]["km"])
    return out


def rain_reminder(body: PlanIn, out: dict) -> Optional[dict]:
    """SP-249: the forecast's rain over the race window (start → finish incl.
    stops; without a start time or on a multi-day trip, 06–18 of each day) on
    road, trail and 百岳 plans (owner 2026-10-06); None without rain data.
    Display only — the plan is already computed."""
    secs = _sections(body)
    if not body.rain and not any(x.get("rain") for x in secs):
        return None
    from backend.engine.racepower import weather as WX
    s = out.get("summary") or {}
    dur = s.get("time_total_s") or s.get("clock_s") or ((s.get("time_s") or 0) + (s.get("stops_s") or 0)) or None
    days = len(out.get("days") or []) or body.days or 1
    win = WX.race_window(body.date, body.start_time, days, dur)
    rows = [r.model_dump() for r in body.rain or []]
    kind = "road" if body.type == "road" else "trail"
    if secs:
        # SP-553: each section's rain over the hours the plan is in that section
        from backend.engine.racepower import cold as CD
        pts = CD.points(out, body.date, body.start_time, [x.model_dump() for x in body.stops], days)
        if pts and pts[0].get("start_km") is not None:
            r = WX.rain_alert(WX.section_rain_rows(secs, pts, rows), win, kind)
            if r is not None:
                r["points"] = len(secs)
            return r
    return WX.rain_alert(rows, win, kind)


def _sections(body: PlanIn) -> list[dict]:
    """SP-553: body.weather_points as dicts ([] for the one-point weather)."""
    return [x.model_dump() for x in body.weather_points or []]


def cold_reminders(body: PlanIn, out: dict) -> tuple[Optional[dict], Optional[dict]]:
    """(cold_wind, hypothermia), cold.py. SP-251: wind chill / gusts at each segment's clock from
    the /weather `wind` rows on road, trail and 百岳 plans; None without wind data. SP-253: the
    失溫風險 checklist on trail / 百岳 (lowest temperature ≤ 5 °C and rain or wind chill ≤ −5 °C);
    None without race-day rain or wind data. Display only — call after rain_reminder."""
    secs = _sections(body)
    if not body.wind and not any(x.get("wind") for x in secs) and out.get("rain") is None:
        return None, None
    from backend.engine.racepower import cold as CD
    from backend.engine.racepower import weather as WX
    days = len(out.get("days") or []) or body.days or 1
    pts = CD.points(out, body.date, body.start_time, [x.model_dump() for x in body.stops], days)
    wind = [r.model_dump() for r in body.wind or []]
    hours = [r.model_dump() for r in body.hourly or []]
    # SP-553: each segment reads the wind / temperature rows of its own section (and their height)
    local = WX.local_rows(secs, wind, hours, body.heat_ref_alt_m) \
        if secs and pts and pts[0].get("start_km") is not None else None
    cw = CD.cold_wind(pts, wind, body.heat_ref_alt_m, body.date, local=local) if wind or local else None
    return cw, CD.hypothermia(pts, cw, out.get("rain"), body.type, wind, hours, body.heat_ref_alt_m, local=local)


def climatology_to(body: PlanIn, out: dict) -> Optional[dict]:
    """SP-257 (docs/research/night-and-sleep.md §3 G5 / §4.2 單 4): with no forecast the page's To
    is the climatology's daytime (06:00–17:59) mean; weather.climatology_to widens it to the hours
    this plan is on course (the daytime hours keep it, the others take the climatology's own hour
    from the /weather hourly rows). On course: road / trail from the start to the predicted finish
    (moving + non-moving time + aid stops); 百岳 each day from the start time to its last segment's
    end (cold.points: moving time ÷ the moving ratio + stops, as the ETAs). None — To unchanged —
    unless To came from the climatology (`to_provider`) with its hourly rows, the plan has a start
    time and segments, and part of it is outside 06–18."""
    if body.to_provider != "climatology" or not body.hourly or body.env_to is None \
            or body.env_to.temp_c is None or body.env_to.rh_pct is None:
        return None
    import datetime as dt
    from backend.engine.racepower import cold as CD
    from backend.engine.racepower import weather as WX
    segs = out.get("segments") or []
    t0 = CD._start(body.date, body.start_time)
    if t0 is None or not segs:
        return None
    if out.get("type") == "baiyue":
        days = len(out.get("days") or []) or body.days or 1
        pts = CD.points(out, body.date, body.start_time, [x.model_dump() for x in body.stops], days)
        by_day: dict[int, list[dict]] = {}
        for s, p in zip(segs, pts):
            by_day.setdefault(int(s.get("day") or 1), []).append(p)
        wins = [(min(p["begin"] for p in ps), max(p["end"] for p in ps)) for _d, ps in sorted(by_day.items())]
    else:
        s = out.get("summary") or {}
        on = (s.get("time_total_s") or s.get("time_s") or 0.0) + (s.get("stops_s") or 0.0)
        wins = [(t0, t0 + dt.timedelta(seconds=float(on)))]
    return WX.climatology_to([r.model_dump() for r in body.hourly], wins, body.env_to.temp_c, body.env_to.rh_pct)


def snow_season(body: PlanIn, course: dict, out: dict) -> None:
    """SP-252: a 百岳 with 積雪 picked on a 玉山 route (the 地點 field, else the course's name) gets the
    雪季措施 line (hike.YUSHAN_SNOW_URL) in summary.snow.season — only when snow was picked (owner
    2026-10-06), never from the date alone. Display only."""
    from backend.engine.racepower import hike as HK
    sn = (out.get("summary") or {}).get("snow")
    if body.type != "baiyue" or not sn:
        return
    if any(HK.is_yushan(n) for n in (body.peak, course.get("name"))):
        sn["season"] = {"url": HK.YUSHAN_SNOW_URL, "single_day": len(out.get("days") or []) <= 1}


def _thr(ctx, lthr: Optional[float]) -> Optional[tuple]:
    """zonebar.threshold_hr with the context's 課表心率區間 (hr_basis); None without a basis / LTHR."""
    from backend.engine.racepower import zonebar as ZB
    hb = getattr(ctx, "hr_basis", None)
    try:
        b = hb() if hb else None
    except Exception:                       # noqa: BLE001 — the LTHR cap, as before
        b = None
    if not b or not lthr:
        return None
    v = ZB.threshold_hr(lthr, b)
    return v if v[0] else None


def fuel(ctx: Context, body: PlanIn, out: dict) -> dict:
    """The 補給 card (engine/racepower/fuel.py) on the predicted segments;
    adds kcal / carbohydrate / water / sodium / fuel_action to each one."""
    from backend.engine.racepower import fuel as FU
    weight = out["used"]["weight"]["value"]
    if not weight or weight <= 0:
        # SP-414: every number of the card needs the body weight (Pandolf / Minetti / REE / g per kg)
        return {"unavailable": _("沒有體重：熱量、碳水、水、鈉的補給建議算不出（都要用體重）；"
                                 "請到 設定 → 個人資料 填寫體重"), "category": "hike" if body.type == "baiyue" else None}
    inp = ctx.inputs()
    hr = None
    th = (out.get("summary") or {}).get("trail_hr")
    lthr = (inp.get("aet") or {}).get("lthr")
    if th and th.get("x") and lthr:
        # the race HR the trail model predicts (x_star = the measured level, before the heat shift)
        hr = (th.get("x_star") or th["x"]) * lthr
    return FU.plan_fuel(out, weight=out["used"]["weight"]["value"], stops=[x.model_dump() for x in body.stops],
                        start_time=body.start_time, hr_bpm=hr, body=ctx.body())


def respond(out: dict) -> dict:
    """SP-482: the response's warnings as hint objects (engine/hints.py: {id, cls, prio, smart, text, detail,
    show, ack, fp}) — the result's, the course's and the 補給 card's. The engine keeps strings (tagged with
    their id: every string check still works); an untagged / old string becomes id RC, shown as before."""
    if not isinstance(out, dict):
        return out
    out = dict(out)
    for k in ("warnings", "course_warnings"):
        if isinstance(out.get(k), list):
            out[k] = H.warnings(out[k])
    if isinstance(out.get("fuel"), dict) and isinstance(out["fuel"].get("warnings"), list):
        out["fuel"] = {**out["fuel"], "warnings": H.warnings(out["fuel"]["warnings"])}
    return out


def predict_response(ctx: Context, body: PredictIn) -> dict:
    """POST /predict's response (v1)."""
    return py(respond(predict(ctx, body)))


def plan(ctx: Context, body: PlanIn) -> dict:
    """POST /plan's response."""
    return py(respond(make_plan(ctx, body)))
