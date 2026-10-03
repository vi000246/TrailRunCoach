"""
Running power zones — Steve Palladino's published table (Palladino Power
Project zone document, 2017; unchanged in his 2019 training-intensity
review), as fractions of CP / FTP. Palladino calls them "descriptive, not
prescriptive".

  https://docs.google.com/document/d/e/2PACX-1vTHqzlWwp2Dp6f1cMlS45PycEf-hCAjy61KXG7fRoR2e4mxDyWH6gXo5ZnIvj5b9cTBWBcj9kcfJHel/pub

"Near threshold" is not a zone name in that table; it sits at the top of 3B.
Palladino's three-zone summary (used for intensity distribution) is
low < 80% CP, moderate 80–95%, high ≥ 95%.
"""
from __future__ import annotations

PALLADINO_POWER_ZONES = [
    # (id, name, lo, hi)  — fractions of CP; hi is exclusive, None = open
    ("1A", "Post-interval recovery", 0.50, 0.65),
    ("1B", "EZ warm-up", 0.65, 0.75),
    ("1C", "EZ aerobic", 0.75, 0.80),
    ("2", "Endurance / long run", 0.80, 0.88),
    ("3A", "Threshold stimulus – extensive", 0.88, 0.95),
    ("3B", "Threshold stimulus – intensive", 0.95, 1.01),
    ("4", "Supra-threshold", 1.01, 1.06),
    ("5", "Maximal aerobic power", 1.06, 1.16),
    ("6", "Anaerobic power", 1.16, 1.50),
    ("7", "Sprint / maximal power", 1.50, None),
]

PALLADINO_3ZONE = {"low": 0.80, "high": 0.95}

# The app's running power zones are Palladino's everywhere (owner 2026-10-02): the editor's
# 三區 / 四區 / 五區 split, interval_library's classes, the ladder's Zone 3 / Zone 5, the weekly
# Zone 5 slot and the session classifier all read these edges (no Coggan / cycling sets).
Z3_LO = 0.88          # 3A: threshold stimulus from here
Z4_LO = 1.01          # 4: supra-threshold (above CP)
Z5_LO = 1.06          # 5: maximal aerobic power (VO2max)


def palladino_rows(open_hi: float = 99.0) -> list[tuple]:
    """[(id, name, lo, hi)] for time-in-zone tables: the first zone from 0, the last
    open zone up to `open_hi` (so every sample falls in one zone)."""
    rows = []
    for i, (zid, name, lo, hi) in enumerate(PALLADINO_POWER_ZONES):
        rows.append((zid, name, 0.0 if i == 0 else lo, open_hi if hi is None else hi))
    return rows

SOURCE = "Palladino Power Project zone table (2017/2019)"


# ---------------------------------------------------------------------------
# WKO5's own level systems for running (docs/wko5-internals/functions.md §4),
# as (id, name, lo, hi) fractions of the threshold. Checked against WKO5's
# zone-table charts at LTHR 160 / threshold pace 4.66 min/km:
#   Classic HR: AR 0–110, E 110–134, TE 134–152, TH 152–170, VM 170+
#   Friel HR:   1 0–134, 2 136–142, 3 144–150, 4 152–158, 5a 160–163,
#               5b 165–170, 5c 170+
# Friel's bands are whole percents with a 1% gap shown between them
# (Z1 ≤ 84%, Z2 85–89%, …); here each band runs up to the next one's start
# so every sample falls in exactly one zone.
# ---------------------------------------------------------------------------

CLASSIC_HR = [
    ("AR", "Active Recovery", 0.0, 0.69),
    ("E", "Endurance", 0.69, 0.84),
    ("TE", "Tempo", 0.84, 0.95),
    ("TH", "Threshold", 0.95, 1.06),
    ("VM", "VO2max", 1.06, None),
]
FRIEL_HR = [
    ("1", "Recovery", 0.0, 0.85),
    ("2", "Aerobic", 0.85, 0.90),
    ("3", "Tempo", 0.90, 0.95),
    ("4", "SubThreshold", 0.95, 1.00),
    ("5a", "SuperThreshold", 1.00, 1.03),
    ("5b", "Aerobic Capacity", 1.03, 1.06),
    ("5c", "Anaerobic Capacity", 1.06, None),
]
# pace: fractions of threshold PACE (min/km); >1 = slower. Listed easy → hard.
FRIEL_PACE = [
    ("1", "Zone 1", 1.29, None),
    ("2", "Zone 2", 1.14, 1.29),
    ("3", "Zone 3", 1.06, 1.14),
    ("4", "Zone 4", 1.00, 1.06),
    ("5a", "Zone 5a", 0.97, 1.00),
    ("5b", "Zone 5b", 0.90, 0.97),
    ("5c", "Zone 5c", None, 0.90),
]

def _coros_table(kind: str) -> list[tuple]:
    """The COROS model's default edges as (id, name, lo, hi) fractions (hr_profile)."""
    from backend.engine import hr_profile as HP
    r = HP.DEFAULT_RATIOS[kind]
    lo, hi = (0.0,) + r, r + (None,)
    return [(HP.ZONE_IDS[i], HP.ZONE_NAMES[i], lo[i], hi[i]) for i in range(6)]


SYSTEMS = {
    "classichr": {"title": "Classic 心率區間（跑步）", "unit": "bpm", "basis": "lthr", "zones": CLASSIC_HR},
    "frielhr": {"title": "Friel 心率區間（跑步）", "unit": "bpm", "basis": "lthr", "zones": FRIEL_HR},
    # the watch's COROS models (engine/hr_profile.py, 2026-10-03); hrr / hrmax rows are
    # computed in bpm from the max / resting HR in effect (zone_table)
    "coroslthr": {"title": "COROS 乳酸閾心率區間（跑步）", "unit": "bpm", "basis": "lthr", "coros": "lthr",
                  "zones": _coros_table("lthr")},
    "coroshrr": {"title": "COROS 儲備心率區間（跑步）", "unit": "bpm", "basis": "hrr", "coros": "hrr",
                 "zones": _coros_table("hrr")},
    "coroshrmax": {"title": "COROS 最大心率區間（跑步）", "unit": "bpm", "basis": "hrmax", "coros": "hrmax",
                   "zones": _coros_table("hrmax")},
    "frielpace": {"title": "Friel 配速區間（跑步）", "unit": "min/km", "basis": "tpace", "zones": FRIEL_PACE},
    "palladino": {"title": "Palladino 功率區間（跑步）", "unit": "W", "basis": "cp", "zones": PALLADINO_POWER_ZONES},
}


# ---------------------------------------------------------------------------
# More zone tables for the single-activity time-in-zone charts
# (panels/activity_charts.py). Same shape: (id, name, lo, hi) fractions of
# the basis, hi exclusive, None = open.
# ---------------------------------------------------------------------------

# Stryd's five running-power zones, % CP (Stryd 「Power Zones」: Easy 65–80,
# Moderate 80–90, Threshold 90–100, Interval 100–115, Repetition 115–130).
# Below 65 % is folded into zone 1 and above 130 % into zone 5 so every
# second has a zone.
STRYD_ZONES = [
    ("1", "Easy", 0.0, 0.80),
    ("2", "Moderate", 0.80, 0.90),
    ("3", "Threshold", 0.90, 1.00),
    ("4", "Interval", 1.00, 1.15),
    ("5", "Repetition", 1.15, None),
]
# No %HRmax zones (user decision 2026-10-01): a fixed % of HRmax puts LT
# anywhere from 60 to 90 % HRmax and MLSS at 75–97 % (Iannetta et al. 2020,
# MSSE 52:466; docs/research/zones-and-thresholds.md §2.1, §3.1). HR zones
# are Friel % LTHR, power zones Palladino % CP; HRmax is only a data check.
# → The user re-added %HRmax zones 2026-10-03 as a CHOICE (COROS's max-HR model,
# engine/hr_profile.py): the chart default stays Friel and the limitation above is
# in that model's 來源 / ? text.
# 徐國峰 RQ 跑力 heart-rate-reserve zones (% HRR): T = 84–88 % HRR
# (runningquotient.com/article/single/52). Verified 2026-10-03: the same edges are
# COROS's HRR model (the account's rhrZone 59/74/84/88/95; the user's COROS app shows
# Z2 141–163 at max 202 / rest 53), so this IS the COROS HRR table.
RQ_HRR_ZONES = [
    ("R", "恢復", 0.0, 0.59),
    ("E", "輕鬆 E", 0.59, 0.74),
    ("M", "馬拉松 M", 0.74, 0.84),
    ("T", "閾值 T", 0.84, 0.88),
    ("A", "無氧閾值 A", 0.88, 0.95),
    ("I", "間歇 I", 0.95, None),
]


def zone_of(power: float, cp: float) -> str | None:
    if not cp or power is None or power <= 0:
        return None
    x = power / cp
    if x < PALLADINO_POWER_ZONES[0][2]:
        return "1A"          # below 50% still counts as recovery
    for zid, _, lo, hi in PALLADINO_POWER_ZONES:
        if x >= lo and (hi is None or x < hi):
            return zid
    return None


def _in_zone_expr(system: str, lo, hi, threshold: Optional[str] = None) -> str:
    """Per-workout expression: seconds of this workout inside the zone.
    `threshold`: the threshold operand — None = each workout's own dated
    value (runtpace / cp / lthr), or a number (zone_table: the threshold its
    rows show, so the time in a row matches that row's boundaries)."""
    if system == "frielpace":
        # pace min/km = 60 / speed(km/h); slower pace = lower speed.
        # pace ≥ lo·tpace  ⇔  speed ≤ 60/(lo·tpace)
        tpace = threshold or "runtpace"
        conds = ["speed > 0"]
        if lo is not None:
            conds.append(f"speed <= 60/({lo}*{tpace})")
        if hi is not None:
            conds.append(f"speed > 60/({hi}*{tpace})")
    else:
        ch, basis = ("runpower", "cp") if system == "palladino" else ("heartrate", "lthr")
        basis = threshold or basis
        conds = [f"{ch} > 0"]
        if lo:
            conds.append(f"{ch} >= {lo}*{basis}")
        if hi is not None:
            conds.append(f"{ch} < {hi}*{basis}")
    return f"sum(if({' and '.join(conds)}, deltatime))"


def _on_day(w, end_day: int):
    """The reference run moved to `end_day`, so its thresholds are those in
    effect that day: a plan test dated after the last run but on or before
    `end_day` applies (since Plan.threshold_on stopped applying tests
    backwards, 2026-10-01, the last run's own date would miss a test done
    on a day WKO5 has no run for, e.g. a CP test entered in the plan)."""
    import dataclasses
    if w is None or not dataclasses.is_dataclass(w):
        return w
    return dataclasses.replace(w, day=max(float(w.day), float(end_day)))


def threshold_info(ds, basis: str, ref, end_day: int) -> dict:
    """{"value", "source", "date", "wprime", "wprime_source"} of the zone
    basis (cp / lthr / tpace) in effect for the reference run, with where it
    comes from: the plan's dated test, the dataset's dated setting (WKO5 /
    athlete_settings / the FIT dataset's as-of estimates) or, for CP before
    the first plan test on a COROS / TP source, the Stryd-only PD fit
    (FitFolderDataset.cp_info); for pace without a setting, the estimate
    thresholds.estimate_tpace (推估)."""
    from backend.files.wko5_athlete import day_to_date
    out = {"value": None, "source": None, "date": None, "wprime": None, "wprime_source": None}
    if ref is None:
        return out
    day = day_to_date(ref.day)
    if basis == "cp":
        f = getattr(ds, "cp_info", None)
        if f is not None:
            i = f(ref)
            return {**out, **i, "value": i.get("cp")}
        rows = sorted((t for t in ds.plan.thresholds if t.cp is not None and t.date[:10] <= day.isoformat()),
                      key=lambda t: t.date)
        if rows:
            return {**out, "value": float(rows[-1].cp), "source": f"你的測試 {rows[-1].date[:10]}",
                    "date": rows[-1].date[:10], "wprime": rows[-1].wprime,
                    "wprime_source": "測試（兩點法）" if rows[-1].wprime else None}
        return {**out, "value": ds.cp(ref), "source": "WKO5 mFTP" if ds.settings_from == "wko5" else None}
    if basis == "lthr":
        # an applied estimate is labelled as one, not 「你的測試」 (zones-and-thresholds.md §3.4 change 1)
        from backend.engine.planning import threshold_row
        r = threshold_row(ds.plan, "lthr", day)
        if r is not None:
            return {**out, "value": r["value"], "source": r["label"], "date": r["date"],
                    "method": r["method"], "measured": r["measured"]}
        return {**out, "value": ds.sport_setting("thr", ref), "source": ds.setting_label("runthr", "WKO5 設定")}
    # threshold pace: a dated setting, else the estimate
    v = ds.sport_setting("tpace", ref)
    if v is not None:
        return {**out, "value": v, "source": ds.setting_label("runtpace", "WKO5 設定")}
    from backend.engine.thresholds import estimate_tpace
    est = estimate_tpace(ds, day)
    return {**out, "value": est.get("value"), "source": est.get("reason"),
            "date": day.isoformat() if est.get("value") else None}


def _no_data_reason(basis: str, T, n_runs: int, days: int) -> str:
    name = {"cp": "CP", "lthr": "LTHR", "tpace": "閾值配速"}[basis]
    if not n_runs:
        return f"最近 {days} 天沒有跑步"
    if T is None:
        return f"沒有 {name}，區間算不出來"
    return {"cp": f"最近 {days} 天的跑步沒有可用的功率（手錶推估功率不採用）",
            "lthr": f"最近 {days} 天的跑步沒有心率", "tpace": f"最近 {days} 天的跑步沒有速度"}[basis]


def zone_table(ds, system: str, end_day: int, days: int = 30) -> dict:
    """WKO5-style zone table: boundaries at the threshold in effect on
    `end_day`, plus time in each zone over the last `days` days of runs."""
    import math
    from backend.engine.wko5expr.evaluator import Evaluator, WS
    spec = SYSTEMS[system]
    runs = [w for w in ds.workouts if w.sport == "run" and end_day - days < math.floor(w.day) <= end_day]
    ref = runs[-1] if runs else next((w for w in reversed(ds.workouts) if w.sport == "run"), None)
    ref = _on_day(ref, end_day)
    if spec["basis"] in ("hrr", "hrmax"):
        return _zone_table_bpm(ds, system, spec, end_day, days, runs)
    basis = {"lthr": lambda w: ds.sport_setting("thr", w), "cp": ds.cp,
             "tpace": lambda w: ds.sport_setting("tpace", w)}[spec["basis"]]
    T = basis(ref) if ref else None
    # WKO5 dates never-entered settings 1980-01-01; the plan's tests override
    import datetime as _dt
    from backend.files.wko5_athlete import day_to_date
    setting = {"lthr": "runthr", "tpace": "runtpace"}.get(spec["basis"])
    plan_field = {"lthr": "lthr", "cp": "cp"}.get(spec["basis"])
    hist = ds.athlete.settings.get(setting) or [] if setting else []
    overridden = ref is not None and plan_field and ds.plan.threshold_on(plan_field, day_to_date(ref.day)) is not None
    is_default = bool(hist) and not overridden and all(d == _dt.date(1980, 1, 1) for d, _ in hist)
    info = threshold_info(ds, spec["basis"], ref, end_day)
    if spec["basis"] == "tpace" and T is None and info.get("value"):
        # no threshold-pace setting: the estimate (thresholds.estimate_tpace)
        T = info["value"]
    # the time in each zone is counted against the threshold the rows show
    # (T, in effect on end_day), not each run's own dated value: with a
    # recent CP test, runs before it had no CP and the table read 0 s
    # (FIT source), or a different CP than the boundaries printed in the row
    op = None if T is None else f"{float(T):.4f}"
    ev = Evaluator(ds, end_day - days + 1, end_day, sports={"run"})
    rows, total = [], 0.0
    for zid, name, lo, hi in spec["zones"]:
        r = ev.evaluate(f"athleterange({end_day - days + 1}, {end_day}, {_in_zone_expr(system, lo, hi, op)})")
        secs = sum(float(v) for v in r.values() if v == v) if isinstance(r, WS) else 0.0
        total += secs
        rows.append({"id": zid, "name": name, "lo": lo, "hi": hi, "seconds": secs,
                     "from": None if (lo is None or T is None) else lo * T,
                     "to": None if (hi is None or T is None) else hi * T})
    for r in rows:
        r["share"] = r["seconds"] / total if total else None
    return {"system": system, "title": spec["title"], "unit": spec["unit"], "basis": spec["basis"],
            "threshold": T, "threshold_is_default": is_default, "days": days, "runs": len(runs), "total_seconds": total, "rows": rows,
            "threshold_source": info.get("source"), "threshold_date": info.get("date"),
            "wprime": info.get("wprime"), "wprime_source": info.get("wprime_source"),
            "no_data_reason": None if total else _no_data_reason(spec["basis"], T, len(runs), days),
            "source": SOURCE if system == "palladino" else _coros_source(spec) or "WKO5 level tables",
            "choices": hr_choices() if spec["unit"] == "bpm" else None}


HR_SYSTEMS = ("frielhr", "classichr", "coroslthr", "coroshrr", "coroshrmax")


def hr_choices() -> list[dict]:
    """The HR zone tables a 「心率區間」 card can switch between (the viewer remembers the choice)."""
    return [{"id": k, "title": SYSTEMS[k]["title"]} for k in HR_SYSTEMS]


def _coros_source(spec: dict) -> Optional[str]:
    if not spec.get("coros"):
        return None
    from backend.engine import hr_profile as HP
    return HP.SOURCE[spec["coros"]]


def _zone_table_bpm(ds, system: str, spec: dict, end_day: int, days: int, runs: list) -> dict:
    """zone_table for the COROS % HRR / % HRmax models: edges in bpm from the max /
    resting HR in effect on end_day (engine/hr_profile.py), or the reason there are none."""
    from backend.engine import hr_profile as HP
    from backend.engine.wko5expr.evaluator import Evaluator, WS
    from backend.files.wko5_athlete import day_to_date
    kind = spec["coros"]
    acc = HP.account()
    day = day_to_date(end_day)
    mx = HP.max_hr(ds, day, acc)
    rs = HP.rest_hr(ds, day, acc) if kind == "hrr" else {}
    z = HP.zone_rows(kind, None, mx.get("value"), rs.get("value"), acc)
    rat = HP.ratios(kind, acc)
    fr_lo, fr_hi = (0.0,) + tuple(rat), tuple(rat) + (None,)
    srcs = "；".join(s for s in ((f"最大心率：{mx['source']}" if mx.get("source") else ""),
                                (f"靜息心率：{rs['source']}" if rs.get("source") else "")) if s)
    T = None
    if "rows" in z:
        T = mx["value"] - rs["value"] if kind == "hrr" else mx["value"]
    ev = Evaluator(ds, end_day - days + 1, end_day, sports={"run"})
    rows, total = [], 0.0
    for i, (zid, name, lo, hi) in enumerate(z.get("rows") or _blank_rows(kind)):
        secs = 0.0
        if "rows" in z:
            r = ev.evaluate(f"athleterange({end_day - days + 1}, {end_day}, "
                            f"{_in_zone_expr('hr', lo or None, hi, '1')})")
            secs = sum(float(v) for v in r.values() if v == v) if isinstance(r, WS) else 0.0
        total += secs
        rows.append({"id": zid, "name": name, "lo": fr_lo[i], "hi": fr_hi[i], "seconds": secs,
                     "from": None if "rows" not in z else lo, "to": None if "rows" not in z else hi})
    for r in rows:
        r["share"] = r["seconds"] / total if total else None
    reason = z.get("reason") if "reason" in z else (None if total else _no_data_reason("lthr", T, len(runs), days))
    return {"system": system, "title": spec["title"], "unit": spec["unit"], "basis": spec["basis"],
            "threshold": T, "threshold_is_default": False, "days": days, "runs": len(runs), "total_seconds": total,
            "rows": rows, "threshold_source": (z.get("basis_text", "") + (f"；{srcs}" if srcs else "")) or None,
            "threshold_date": None, "wprime": None, "wprime_source": None,
            "estimate": mx.get("kind") == "estimate", "no_data_reason": reason,
            "source": _coros_source(spec), "choices": hr_choices()}


def _blank_rows(kind: str) -> list[tuple]:
    return [(i, n, None, None) for i, n, _lo, _hi in _coros_table(kind)]


# What to run by, per workout type. Power from Palladino's table; HR caps from
# the athlete's AeT / Friel HR bands. For intervals HR lags too much to steer
# by (it keeps climbing through a 5-minute rep), so power is primary there.
#
# Trail long days: HR first (user decision 2026-10-01, after
# docs/research/vo2max-gate-and-trail-metric.md: only ~9 % of trail time is on
# Stryd-validated 3–8 % grades). Long climbs: suggestions only, no enforced
# target. Downhill practice: by feel. Hill repeats: power first, HR second.
# (The paragraph below is the rationale for hill repeats.) HR answers a change of effort with a
# ~60 s lag (τ 55–70 s, Hunt 2015/2019, Wang & Hunt 2021;
# docs/research/drift-algorithm.md §2), so on a 1–3-minute climb it is still
# rising when the climb ends, while a steady Stryd power on 0–8 % grades is a
# steady metabolic load (van Rassel et al. 2026; docs/research/racepower-v2.md
# §2.4). Limits, both from the same docs: Stryd's own trail guidance — "in
# technical terrain and face steep terrain, you can no longer use a single
# power number" (help.stryd.com 6879554) — so on steep technical descents
# power is ignored; and for steep hiking under load Uphill Athlete keeps HR as
# the practical tool (docs/research/coaching-dashboards-mountain.md §1.1). The
# power bands themselves are Palladino's (% CP); applying them uphill above
# ~8 % grade is 推估 (Stryd is validated to ~8 %).
TERRAIN_NOTE = ("山路長天、越野輕鬆看心率（≤ 輕鬆跑上限）；越野只有約 9% 時間在 Stryd 驗證過的 3–8% 坡，功率只當參考。"
                "爬坡重複（3–8% 坡）看功率（心率約慢 1 分鐘才反應，Hunt 2015；van Rassel 2026），心率當上限檢查。"
                "長爬坡不設強制目標，能跑的坡參考功率、陡坡參考心率＋VAM。陡的技術下坡不看功率（Stryd），照感覺與安全。"
                "")
WORKOUT_TARGETS = [
    # (id, name, power lo, power hi, hr lo (×LTHR or "aet"), hr hi, primary, example, source)
    ("recovery", "恢復跑", None, 0.75, None, 0.85, "心率", "20–40 分鐘，隔天有強度課時", "Palladino 1A–1B；Friel Z1"),
    ("z2", "輕鬆跑（Zone 2）", 0.75, 0.80, None, "aet", "心率", "大部分的跑步；心率不超過輕鬆跑上限", "Palladino 1C；Uphill Athlete AeT"),
    ("long", "長跑（路跑）", 0.80, 0.88, None, "aet", "心率", "60 分鐘以上；心率壓在輕鬆跑上限以下", "Palladino Z2；Uphill Athlete"),
    # primary = engine/target_policy.py (vo2max-gate-and-trail-metric.md §2.5: long trail days by HR)
    ("trail", "山路長天 / 越野輕鬆", 0.75, 0.88, None, "aet", "心率",
     "心率不超過輕鬆跑上限（可以走）；功率只當參考，陡坡、下坡不看功率",
     "Uphill Athlete（AeT 心率）；越野只有約 9% 時間在 Stryd 驗證的 3–8% 坡（依活動資料統計）"),
    ("climb", "長爬坡（自由練，建議值）", 0.88, 1.00, 0.90, 1.00, "建議",
     "不設強制目標：能跑的坡參考功率，陡到要走的坡參考心率＋VAM（每小時爬升）；自己決定練法",
     "功率 Palladino Z2–3B（% CP）、心率 Friel Z3–Z4；陡坡用心率＋VAM（推估）"),
    ("downhill", "下坡練習", None, None, None, None, "體感",
     "控制下降公尺數，練技術與步頻；不看功率也不看心率（離心負荷，量要慢慢加）",
     "Uphill Athlete；下降量的上限是推估"),
    ("hill", "爬坡重複", 0.95, 1.06, 0.95, 1.03, "功率",
     "4–6×4 分鐘上坡，走或慢跑下來恢復；看 30 秒平均功率，心率只當參考（短坡上還沒升上來）",
     "Palladino 3B–Z4（% CP）；心率延遲 Hunt 2015；坡度 > 8% 為推估"),
    ("threshold", "閾值（Near threshold）", 0.95, 1.01, 0.95, 1.00, "功率", "3×10 分鐘 或 2×15 分鐘，休 2–3 分鐘", "Palladino 3B；Friel Z4"),
    ("supra", "Supra threshold", 1.01, 1.06, 1.00, 1.03, "功率", "4–6×5 分鐘，休 2:45", "Palladino Z4；Friel Z5a"),
    ("vo2", "VO2max", 1.06, 1.16, 1.03, 1.06, "功率", "5×3 分鐘，休 3 分鐘；FTP 紮實後、賽前 4–6 週", "Palladino Z5；Friel Z5b"),
]


# ---- the 「± N bpm」 badge on an AeT that is an estimate, not a test --------------------
# docs/research/zones-and-thresholds.md §2.1–2.2: the ±16 bpm in the research is NOT the
# drift / regression method's error — it is Micheli 2025's 95 % limits of agreement of the
# HR at CP vs the HR at MLSS (−15.84 … +17.05 bpm, PeerJ 13:e19060). No validated error
# exists for an AeT from 0.89 × LTHR or from drift; ±16 is borrowed as the field-estimate
# magnitude (推估). A regression estimate with a standard error shows ±2·SE instead, never
# below the ~3 bpm day-to-day HR variation (Lamberts & Lambert 2009).
AET_PM_BORROWED = 16                 # Micheli 2025 LoA magnitude, borrowed (推估)
AET_PM_FLOOR = 3                     # Lamberts & Lambert 2009: day-to-day HR 3 ± 1 bpm
_PM_SRC = ("±16 bpm 借用 Micheli 2025（PeerJ 13:e19060）：CP 時的心率和 MLSS 心率的 95% 一致性界限"
           " −15.8～+17.1 bpm。AeT 本身沒有驗證過的誤差，所以是推估")


def aet_uncertainty(kind: Optional[str], se: Optional[float] = None) -> Optional[dict]:
    """{"pm": N bpm, "tip"} for an AeT estimate; None for a measured AeT (kind
    None / "measured"). kind: "regression" (drift regression with its SE),
    "estimate" (an estimate whose SE isn't kept), "friel" (0.89 × LTHR)."""
    if kind in (None, "measured"):
        return None
    if kind == "regression" and se is not None and se > 0:
        n = max(AET_PM_FLOOR, int(round(2 * se)))
        return {"pm": n, "tip": f"推估的誤差約 ±{n} bpm：多次穩定跑的飄移回歸，95% 範圍 ≈ 2 × 標準誤（{se:.1f}）；"
                                f"至少 ±{AET_PM_FLOOR}（每天的心率本來就差約 3 bpm，Lamberts 2009）。"
                                "單次飄移的標準誤可能被低估 2–3 倍（Golden Cheetah 驗證），實際可能更大。"
                                "做一次 AeT 測試最準。"}
    head = ("0.89 × LTHR 是 Friel 區間的慣例，不是量出來的 AeT。" if kind == "friel" else
            "這是自動估算，不是測試。")
    return {"pm": AET_PM_BORROWED, "tip": f"推估的誤差約 ±{AET_PM_BORROWED} bpm。{head}{_PM_SRC}。做一次 AeT 測試最準。"}


def training_targets(ds, end_day: int, lthr_est=None, aet_est=None, aet_below=None) -> dict:
    """Suggested HR / power ranges per workout type. Uses the thresholds in
    effect; when LTHR is still WKO5's untouched default and an estimate
    exists, uses the estimate and says so. HR ranges follow the 課表心率區間
    setting (engine/hr_profile.plan_hr_zones: recovery Z1, easy / long / trail
    Z2 — capped by a MEASURED AeT, climb Z3–Z4, hill / threshold Z4, supra /
    VO2 Z5). `aet_below`: thresholds.estimate's 「highest HR of the runs with
    drift < 5 %」, shown as a reference when AeT falls back to 0.89 × LTHR."""
    import datetime as dt
    import math
    runs = [w for w in ds.workouts if w.sport == "run" and math.floor(w.day) <= end_day]
    ref = _on_day(runs[-1], end_day) if runs else None
    cp = ds.cp(ref) if ref else None
    lthr = ds.sport_setting("thr", ref) if ref else None
    hist = ds.athlete.settings.get("runthr") or []
    from backend.engine.planning import threshold_row
    from backend.files.wko5_athlete import day_to_date
    # where each value comes from, said as it is: a test, or an applied estimate
    # (zones-and-thresholds.md §3.4 change 1 — an LTHR that was an applied estimate shown as 「你的測試」)
    lr = threshold_row(ds.plan, "lthr", day_to_date(ref.day)) if ref is not None else None
    planned = lr is not None
    lthr_src = lr["label"] if planned else ds.setting_label("runthr", "WKO5 設定")
    lthr_measured = bool(lr and lr["measured"])
    if not planned and hist and all(d == dt.date(1980, 1, 1) for d, _ in hist) and lthr_est:
        lthr, lthr_src = float(lthr_est), "自動估算（尚未套用）"
    ar = threshold_row(ds.plan, "aethr", day_to_date(ref.day)) if ref is not None else None
    aet_measured = bool(ar and ar["measured"])
    if ar is not None:
        # the easy cap: a measured AeT; an applied estimate is used (the user approved it) but says so
        aet, aet_src = ds.aethr(ref), ar["label"] + ("" if aet_measured else "（推估）")
    elif aet_est:
        aet, aet_src = float(aet_est), "自動估算（尚未套用）"
    else:
        aet, aet_src = (None if lthr is None else 0.89 * lthr), "0.89 × LTHR（Friel Z2 上限，推估）"
        if aet_below:
            # the AeT estimate failed: the highest HR of the runs that drifted < 5 % is a hint
            # that the easy cap may sit higher than 0.89 × LTHR (text only, the cap is unchanged)
            aet_src += f"；參考：飄移 < 5% 的跑步最高心率 {aet_below:.0f} bpm"
    # 課表心率區間 (engine/hr_profile.py, 設定): the workout HR targets in the chosen COROS model;
    # the WKO5 cross-check mode keeps the old Friel fractions
    hrz = None
    if lthr is not None and not getattr(getattr(ds, "config", None), "parity", False):
        from backend.engine import hr_profile as HP
        try:
            hrz = HP.plan_hr_zones_for(ds, day_to_date(ref.day), lthr, aet, aet_measured)
        except Exception:                   # noqa: BLE001 — a dataset without a plan (tests)
            hrz = None
    rows = []
    for tid, name, plo, phi, hlo, hhi, primary, example, src in WORKOUT_TARGETS:
        def hr(x):
            if x is None or lthr is None:
                return None
            return aet if x == "aet" else x * lthr
        h = [hr(hlo), hr(hhi)]
        if hrz and tid in hrz["targets"]:
            h = list(hrz["easy"]) if tid in ("z2", "long", "trail") else list(hrz["targets"][tid])
            if tid in ("z2", "long", "trail"):
                h[0] = None                         # the easy rows are a cap (≤), as before
        rows.append({"id": tid, "name": name, "primary": primary, "example": example, "source": src,
                     "power": [None if plo is None or not cp else plo * cp, None if phi is None or not cp else phi * cp],
                     "power_pct": [plo, phi], "hr": h})
    ci = threshold_info(ds, "cp", ref, end_day)
    return {"cp": cp, "cp_source": ci.get("source") or ("WKO5 mFTP" if ds.settings_from == "wko5"
                                                        else ds.setting_label("runftp")),
            "cp_date": ci.get("date"), "terrain_note": TERRAIN_NOTE,
            "lthr": lthr, "lthr_source": lthr_src, "lthr_measured": lthr_measured,
            "aet": aet, "aet_source": aet_src, "aet_measured": aet_measured, "aet_below": aet_below,
            "aet_pm": None if aet is None else aet_uncertainty(
                "measured" if aet_measured else "estimate" if (ar is not None or aet_est) else "friel"),
            # the easy-run HR cap of the 課表: a measured AeT, else the model's Z2 top
            "easy_cap": None if hrz is None else {"value": float(hrz["easy"][1]), "source": hrz["easy_source"]},
            "hr_model": hrz,
            "hr_zones": hrz["label"] if hrz else "Friel % LTHR", "power_zones": "Palladino % CP", "rows": rows}


def zones_json(cp: float | None) -> list[dict]:
    return [{"id": z, "name": n, "lo": lo, "hi": hi,
             "watts": None if not cp else [round(lo * cp), None if hi is None else round(hi * cp)]}
            for z, n, lo, hi in PALLADINO_POWER_ZONES]
