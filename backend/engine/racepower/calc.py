"""
The race calculator's computations (POST /predict, /plan, /course/event/{id}, /export/csv)
on an athlete context — shared by the API (api/racepower.py: the context reads the
Dataset / DB / files) and the static demo, which runs this module in the browser
with Pyodide on a context exported as JSON (backend/demo/static_racepower.py).

No FastAPI, no database here: everything the athlete's data decides comes from
`ctx` (see `Context`), failures raise `CalcError(status, detail)` (the API turns it
into an HTTPException with the same status and detail).
"""
from __future__ import annotations

import dataclasses
import math
from typing import Literal, Optional, Protocol

from pydantic import BaseModel, Field

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
    """What the computations read about the athlete (api/racepower.LiveContext,
    demo/static_racepower.StaticContext)."""

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
    # heat acclimation (heat-acclimation.md §5.5): {"mode": auto|none|partial|acclimatised|custom, "s"}
    heat_acclimatisation: Optional[dict] = None
    # 百岳 capacity (baiyue-from-running.md §6.1)
    trip_kind: Optional[Literal["group", "solo"]] = None
    hr_band: Optional[Literal["aet", "cap"]] = None
    pack_kg_by_day: list[float] = []
    heat_ref_alt_m: Optional[float] = None  # the elevation the race-day temperature refers to
    # SP-244: the plan event is marked 會用登山杖 → a hint on steep segments (seg_targets.pole_hint);
    # text only, no number changes
    poles: bool = False


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
        warnings.append(d["cp"]["lower_bound_message"])

    # effort distance & RE
    trail = body.type == "trail"
    fx = body.effort_formula if body.effort_formula in SIMPLE_FORMULAS else "fitted_run"
    from backend.engine.algorithms.effort import divisor_of
    divisor = divisor_of(fx) if trail else None
    d_eff_km = RE.effort_km(body.distance_km, body.gain_m, divisor) if trail else body.distance_km
    race_cvi = RE.cvi(body.gain_m, body.distance_km)
    if body.re:
        re_v, re_src = body.re, _("手動")
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
        re_v = s["median"] + adj
        re_src = _("你的平路 RE 中位數（{n} 次）", n=s['n']) + (_("，CVI 調整 {adj:+.2f}", adj=adj) if adj else "")
    used["re"] = _src(re_v, re_src)

    # Riegel k
    target_m = d_eff_km * 1000.0
    prior = body.prior.model_dump() if body.prior else None
    if prior is None and d.get("auto_prior"):
        a = d["auto_prior"]
        prior = {"distance_km": a["km"], "time_s": a["time_s"], "power": a["avg_power"],
                 "label": _("{label}（自動：一年內心率判定為比賽強度的標準距離跑步）", label=a['label'])}
    elif prior is None:
        warnings.append(_("沒有比賽強度的標準距離紀錄可當查表依據：k 用預設 −0.07（≈ Stryd 比賽功率表）"))
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
            warnings.append(_("個人 Riegel k 不可靠（{why}），改用查表 k", why="；".join(pr.get("invalid_reasons") or [])))
    else:
        k, k_label, ksrc = DEFAULT_K, _("預設 −0.07"), "manual"
    used["k"] = _src(k, k_label, kind=ksrc)
    if not body.cp:
        # never predict below a power the athlete already held: the bound for THIS k
        from backend.engine.racepower.athlete import enforce_lower_bound
        cp_eff, lb_k = enforce_lower_bound(d, cp, w_prime, tte, k, (used.get("cp2") or {}).get("value"))
        if cp_eff > cp:
            warnings.append(_("k {k:+.2f} 下，CP {cp:.0f} W 撐不住你 {min:.0f} 分鐘 {p:.0f} W 的紀錄："
                              "提高到 {cp_eff:.0f} W", k=k, cp=cp, min=lb_k['t_s'] / 60, p=lb_k['p'], cp_eff=cp_eff))
            cp = cp_eff
            used["cp"] = _src(cp, _("{src}；依 k {k:+.2f} 提高到下限", src=used['cp']['source'], k=k), id=used["cp"].get("id"))
    if tk and tk.get("warning"):
        warnings.append(_("查表 k：") + tk["warning"])

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
        warnings.append(_("爬坡功率上限 110 % 是經驗法則（非研究結論）；下坡讓功率自然掉下來"))
    if race_cvi is not None and not trail and race_cvi >= 25:
        warnings.append(_("路線 CVI {cvi:.0f}（丘陵），已用 CVI 調整 RE；起伏很大的路線請改用「越野」", cvi=race_cvi))

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
            warnings.append(_("整趟時間是推估：{note}；用你的步行能力模型（待回測）", note=h.get("note") or ""))
        else:
            eph = HK.tobler_eph(body.distance_km, body.gain_m, body.loss_m)
            src = _("推估：Tobler 步行函數在這條路線的 EP/h（{note}）", note=h.get("note") or _("百岳多為跟團"))
            warnings.append(_("整趟時間是推估：{note}；沒有跑步資料可建能力模型，用 Tobler 步行函數", note=h.get("note") or ""))
    used["eph"] = _src(eph, src)
    days = max(1, body.days or 1)
    from backend.engine.racepower import capacity as _cap
    default_pack = _cap.pack_default(weight, days)
    pack = body.pack_kg if body.pack_kg is not None else default_pack
    used["pack_kg"] = _src(pack, _("手動") if body.pack_kg is not None else _("預設背負 {pack}", pack=_cap.pack_default_text(weight)))
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
        warnings.append(_("沒有每日行程：距離與爬升平均分配到每一天；實際行程請逐日輸入"))
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
    return py({"course_id": cid, "event_id": eid, "name": track.name or row.get("filename"), **c,
               "stop_suggestions": sug, "day_splits_km": splits, "gpx": ctx.event_meta(row)})


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
        days = sorted({p["day"] for p in pieces})
        data["days"] = len(days)
        data["day_plan"] = [{"km": sum(p["dist_m"] for p in pieces if p["day"] == n) / 1000.0,
                             "gain_m": sum(p["gain_m"] for p in pieces if p["day"] == n),
                             "loss_m": sum(p["loss_m"] for p in pieces if p["day"] == n)} for n in days]
    if body.mode != "time":
        data["target_time_s"] = None
    return predict(ctx, PredictIn(**data))


def make_plan(ctx: Context, body: PlanIn) -> dict:
    from backend.engine.racepower import planner as PL
    course = resolve_course(ctx, body)
    if body.type == "baiyue" and course.get("source") == "gpx" and not body.day_splits_km and (body.days or 1) > 1:
        # a multi-day trip without split points: cut the course into equal-km days
        km = course["totals"]["km"]
        body = body.model_copy(update={"day_splits_km": [km * i / body.days for i in range(1, body.days)]})
    v1 = v1_for(ctx, body, course)
    validated, effort_ok = ctx.flags()
    gm = ctx.grade_models()
    opts = body.model_dump()
    opts["locks"] = [x.model_dump() for x in body.locks]
    opts["stops"] = [x.model_dump() for x in body.stops]
    opts["hourly"] = [x.model_dump() for x in body.hourly or []]
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
            out = PL.plan_hike(v1=v1, course=course, hike_speed=gm["hike_speed"], inp=ctx.inputs(), opts=opts,
                               validated=validated, capacity=gm.get("walk_capacity"))
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
                        "lthr": (inp.get("aet") or {}).get("lthr"), "aet": (inp.get("aet") or {}).get("aet")}
            th = ctx.trail_hr() if body.type == "trail" else None

            def run(o: dict, v: dict) -> dict:
                return PL.plan_run(v1=v, course=course, grade_re=gre, opts=o, validated=validated,
                                   effort_validated=effort_ok, longest_s=(inp.get("riegel") or {}).get("longest_s"),
                                   capacity=capacity, trail_hr=th)
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
    out["fuel"] = fuel(ctx, body, out)
    from backend.engine.racepower import seg_targets as ST
    aet_d = ctx.inputs().get("aet") or {}
    out["seg_targets"] = ST.plan_targets(out, aet=aet_d.get("aet"), lthr=aet_d.get("lthr"))
    # the main chart / table: pace, power and HR target per segment, null where not valid
    out["chart_rows"] = ST.chart_rows(out, aet=aet_d.get("aet"), lthr=aet_d.get("lthr"), poles=body.poles)
    if body.type != "baiyue":
        # the absolute-intensity bar's heart-rate version (SP-118; zonebar.py)
        from backend.engine.racepower import zonebar as ZB
        hb = getattr(ctx, "hr_basis", None)
        out["hr_bar"] = ZB.hr_bar(out, aet_d.get("lthr"), hb() if hb else None)
    # 「匯出至課表」 is one race-day session: a multi-day 百岳 trip is not exported (the reason, else None)
    from backend.engine.racepower import watch_export as WE
    out["export_block"] = WE.multi_day(out, body.start_time, body.days)
    if course.get("source") == "gpx":
        from backend.engine.racepower import fuel as FU
        out["stop_suggestions"] = FU.stops_from_wpts(course.get("wpts") or [], course["totals"]["km"])
    return out


def fuel(ctx: Context, body: PlanIn, out: dict) -> dict:
    """The 補給 card (engine/racepower/fuel.py) on the predicted segments;
    adds kcal / carbohydrate / water / sodium / fuel_action to each one."""
    from backend.engine.racepower import fuel as FU
    inp = ctx.inputs()
    hr = None
    th = (out.get("summary") or {}).get("trail_hr")
    lthr = (inp.get("aet") or {}).get("lthr")
    if th and th.get("x") and lthr:
        # the race HR the trail model predicts (x_star = the measured level, before the heat shift)
        hr = (th.get("x_star") or th["x"]) * lthr
    return FU.plan_fuel(out, weight=out["used"]["weight"]["value"], stops=[x.model_dump() for x in body.stops],
                        start_time=body.start_time, hr_bpm=hr, body=ctx.body())


def plan(ctx: Context, body: PlanIn) -> dict:
    """POST /plan's response."""
    return py(make_plan(ctx, body))


def export_csv(ctx: Context, body: ExportIn) -> tuple[str, str]:
    """POST /export/csv: (the CSV text, the file name)."""
    from backend.engine.racepower import csvplan as CSV
    p = py(make_plan(ctx, body))
    fname = CSV.filename(p, body.name, body.date)
    label = body.name or p.get("course_name") or f"{CSV.TYPE_LABEL.get(p['type'], '')} {p['summary']['km']:.1f} km"
    text = CSV.plan_csv(p, name=label, date=body.date, start_time=body.start_time,
                        stops=[x.model_dump() for x in body.stops],
                        acclimatisation=body.acclimatisation or ("unacclimatised" if body.type == "baiyue"
                                                                 else "acclimatised"))
    return text, fname
