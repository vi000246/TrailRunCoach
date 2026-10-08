"""
The debug API's read-only views (SP-371, api/debug.py, docs/debug-api.md).

Everything here READS: no reconcile, no adapt run, no push, no sync. The numbers come from the
code the pages use (overview.activity_row, workout_review.measure / classify, plan_match.compare,
compliance, planning.threshold_row, quality_gate.lthr_info …); this module only collects them
and says where each came from (`source`), when (`as_of`) and, for a judgement, why (`why`).

Safety nets on every answer (api/debug.finish → `scrub`): GPS keys removed unless ?gps=1;
credential-named keys and credential-looking values (a debug token, a Fernet-sealed blob) blanked.
The sources never put a secret in, this is the second line.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

from backend.i18n import N_, _

SCHEMA_VERSION = 1

# GPS: never in an answer without ?gps=1 (SP-319 deidentify; the ticket's 共同規則)
GPS_KEYS = frozenset({"lat", "lon", "lng", "latitude", "longitude", "position_lat", "position_long",
                      "start_lat", "start_lon", "end_lat", "end_lon", "start_position", "end_position",
                      "cells", "footprint", "polyline", "gps"})
# key names that hold credentials (whatever the module): dropped from every answer
_SECRET_KEY = re.compile(r"(password|passwd|secret|cookie|sealed|access_token|refresh_token|api_?key|"
                         r"credential|token_hash|^token$|^pin$)", re.I)
# settings keys never exported: the backup folder, the 課表訂閱 link, the debug switch's internals
EXPORT_EXCLUDE = frozenset({"backup.dir", "backup.last_result", "backup.last_ok", "plan.calendar",
                            "sync.coros.last_result", "sync.trainingpeaks.last_result", "sync.coros.last_ok",
                            "sync.trainingpeaks.last_ok", "sync.coros.rpe_backfill", "sync.schedule.last_run"})
REDACTED = "[redacted]"
EXPORT_NOT_INCLUDED = (N_("密碼"), "COROS / TP token", N_("記住的帳密"), "debug token", N_("備份雲端位置"),
                       N_("分享連結"), N_("同步結果"), N_("活動檔本身（用 /debug/activity 按需拿）"))


# ---------------------------------------------------------------------------
# JSON + scrub
# ---------------------------------------------------------------------------

def jsonable(x: Any, depth: int = 0) -> Any:
    """Plain JSON: numpy scalars / arrays, NaN / inf → None, dates → ISO, dataclasses → dicts."""
    if depth > 40:
        return None
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, (dt.datetime, dt.date)):
        return x.isoformat()
    if isinstance(x, Path):
        return x.name
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return jsonable(dataclasses.asdict(x), depth + 1)
    if isinstance(x, dict):
        return {str(k): jsonable(v, depth + 1) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        return [jsonable(v, depth + 1) for v in x]
    try:
        import numpy as np
        if isinstance(x, np.ndarray):
            return [jsonable(v, depth + 1) for v in x.tolist()]
        if isinstance(x, np.generic):
            return jsonable(x.item(), depth + 1)
    except ImportError:                     # pragma: no cover
        pass
    return str(x)


def _secret_value(v: str) -> bool:
    from backend.debug_auth import TOKEN_PREFIX
    return v.startswith(TOKEN_PREFIX) or v.startswith("gAAAAA")


def scrub(x: Any, gps: bool = False) -> Any:
    """Drop GPS keys (unless `gps`) and credential-named keys; blank credential-looking strings."""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            ks = str(k)
            if not gps and ks.lower() in GPS_KEYS:
                continue
            if _SECRET_KEY.search(ks):
                continue
            out[k] = scrub(v, gps)
        return out
    if isinstance(x, list):
        return [scrub(v, gps) for v in x]
    if isinstance(x, str) and _secret_value(x):
        return REDACTED
    return x


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------

_SHA: Optional[str] = None


def git_sha() -> str:
    """TRC_GIT_SHA (the image build: Dockerfile ARG GIT_SHA), else `git rev-parse` of the
    checkout, else "unknown"."""
    global _SHA
    if _SHA is None:
        v = (os.environ.get("TRC_GIT_SHA") or "").strip()
        if not v or v == "unknown":
            try:
                root = Path(__file__).resolve().parents[2]
                v = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True,
                                   text=True, timeout=3).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                v = ""
        _SHA = v or "unknown"
    return _SHA


def cache_versions() -> dict:
    out: dict = {}
    try:
        from backend.engine import workout_review as WR
        out["workout_review"] = WR.CACHE_KEY + WR.apply_calibration()
    except Exception:                       # noqa: BLE001
        pass
    try:
        from backend.engine.wko5expr import fitcache
        out["fitcache"] = fitcache.versions()
    except Exception:                       # noqa: BLE001
        pass
    return out


def data_generation(ds) -> dict:
    """Which build of the data the answer read: the Dataset's process-unique generation, its
    source and `today`, and how many activities it holds."""
    if ds is None:
        return {"dataset": None}
    from backend import tenancy
    out = {"dataset": tenancy.generation(ds), "source": getattr(ds, "source", None) or "wko5",
           "activities": len(getattr(ds, "workouts", []) or [])}
    try:
        from backend.engine import overview as O
        out["today"] = O.day_to_date(ds.today).isoformat()
    except Exception:                       # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------------------
# activities
# ---------------------------------------------------------------------------

def find_workouts(ds, day: Optional[dt.date] = None, file_name: Optional[str] = None,
                  label: Optional[str] = None) -> list:
    """Dataset workouts of a day, of a file (a workout_files row's file), or whose label /
    title / file contains `label` (case-insensitive)."""
    from backend.engine import overview as O
    if day is not None:
        return O.workouts_between(ds, day, day + dt.timedelta(days=1))
    if file_name:
        return [w for w in ds.workouts if Path(str(w.entry.file)).name == file_name]
    if label:
        from backend.engine.racepower import athlete as A
        q = label.strip().lower()
        out = []
        for w in ds.workouts:
            names = (A.label(w), getattr(w.entry, "title", "") or "", str(w.entry.file))
            if any(q in str(n).lower() for n in names):
                out.append(w)
        return out[:20]
    return []


def _lthr_used(ds, w) -> dict:
    """The LTHR the run's hrTSS was scored on and where it came from."""
    from backend.engine import overview as O
    from backend.engine.planning import threshold_row
    day = O.wdate(w)
    v = None
    try:
        v = ds.hr_lthr(w)
    except Exception:                       # noqa: BLE001
        v = None
    row = None
    try:
        row = threshold_row(ds.plan, "lthr", day)
    except Exception:                       # noqa: BLE001
        row = None
    if row is not None and v is not None and abs(float(row["value"]) - float(v)) < 0.5:
        src, origin = row["label"], "plan"
    else:
        try:
            src = ds.setting_label("runthr")
        except Exception:                   # noqa: BLE001
            src = None
        origin = "dataset"
    return {"value": v, "source": src, "origin": origin, "as_of": day.isoformat(),
            "why": _("hrTSS = 心率時間 × (平均心率 ÷ LTHR)²；LTHR 取當天生效的值（課表門檻列 → 資料來源設定 → 估算）")}


def activity_detail(ds, w, streams: Iterable[str] = (), every: int = 10, gps: bool = False) -> dict:
    """One activity: basic, numbers, the thresholds used, the review (classify + measure)."""
    from backend.engine import overview as O
    from backend.engine import workout_review as WR
    out: dict = {"index": w.idx}
    src = None
    try:
        src = ds.file_origin(w) if hasattr(ds, "file_origin") else (getattr(ds, "source", None) or "wko5")
    except Exception:                       # noqa: BLE001
        src = getattr(ds, "source", None)
    row = O.activity_row(w, ds)
    out["basic"] = {"source": src, "file": Path(str(w.entry.file)).name, "start": w.entry.start.isoformat(),
                    "date": O.wdate(w).isoformat(), "sport": w.sport, "sport_type": w.sport_type,
                    "category": row.get("category"), "category_label": row.get("category_label"),
                    "title": getattr(w.entry, "title", "") or None, "as_of": O.wdate(w).isoformat()}
    try:
        from backend.api import wko5views as WV
        aj = WV._activity_json(ds, w)
        out["basic"]["label"] = aj.get("label")
        out["basic"]["terrain"] = aj.get("terrain")
        out["tags"] = {k: aj.get(k) for k in aj if k not in ("types", "efforts", "file", "workout")}
        out["tags"]["source"] = _("auto + 使用者覆寫（activity_tags）")
    except Exception as e:                  # noqa: BLE001
        out["tags"] = {"error": type(e).__name__}
    m = dict(w.metrics or {})
    out["numbers"] = {"source": src, "metrics": m, "row": {k: v for k, v in row.items() if k != "session"}}
    th: dict = {"lthr": _lthr_used(ds, w)}
    try:
        if hasattr(ds, "cp_info"):
            th["cp"] = ds.cp_info(w)
        ftp, ftp_src = ds.tss_ftp(w)
        th["tss_ftp"] = {"value": ftp, "source": ftp_src,
                         "why": _("功率 TSS 用的 FTP（= 當天生效的 CP）；沒有就改用 rTSS／hrTSS")}
    except Exception as e:                  # noqa: BLE001
        th["cp_error"] = type(e).__name__
    try:
        th["power_source"] = ds.power_source(w)
    except Exception:                       # noqa: BLE001
        pass
    th["ignored"] = list(getattr(ds, "settings_ignored", []) or [])
    out["thresholds_used"] = th
    try:
        meas = WR.measure(ds, w)
        cls = WR.classify(ds, w, meas) if meas else None
        WR._flush(ds)
        out["review"] = {"source": _("workout_review（快取：{key}）", key=WR.CACHE_KEY), "classification": cls,
                         "session": row.get("session"), "measure": meas,
                         "why": _("課表類型／刺激分級：workout_review.classify；飄移：measure.drift（含心率品質）；"
                                  "間歇：measure.intervals／efforts；爬坡段：measure.climbs")}
    except Exception as e:                  # noqa: BLE001
        out["review"] = {"error": type(e).__name__}
    names = [s.strip() for s in streams if s.strip()]
    if names or gps:
        out["streams"] = _streams(ds, w, names, every, gps)
    return out


STREAMS = {"hr": "heartrate", "power": "power", "speed": "speed", "elev": "elevation", "cadence": "cadence",
           "dist": "elapseddistance"}


def _streams(ds, w, names: list, every: int, gps: bool) -> dict:
    import numpy as np
    every = max(1, min(600, int(every or 10)))
    t = ds.channel(w.idx, "elapsedtime")
    if t is None:
        return {"error": _("沒有逐秒資料")}
    step = slice(None, None, every)
    out = {"every_s": every, "t": t[step]}
    for n in names:
        ch = STREAMS.get(n)
        if ch is None:
            out[n] = {"error": f"unknown stream; one of {sorted(STREAMS)}"}
            continue
        v = ds.channel(w.idx, ch)
        if v is None and n == "elev":
            v = ds.channel(w.idx, "_elevation")
        out[n] = None if v is None else np.asarray(v)[step]
    if gps:
        for k in ("latitude", "longitude"):
            v = ds.channel(w.idx, k)
            out[k] = None if v is None else np.asarray(v)[step]
    return out


# ---------------------------------------------------------------------------
# thresholds
# ---------------------------------------------------------------------------

THRESHOLD_FIELDS = (("cp", "CP"), ("lthr", "LTHR"), ("aethr", "AeT"), ("mhr", N_("最大心率")), ("rhr", N_("安靜心率")))
_SOURCE_OF_METHOD = {"estimate": N_("估算"), "friel30": N_("測試"), "test": N_("測試"), "race": N_("比賽"),
                     "lab": N_("測試"), "manual": N_("手動")}


def _row_source(t, name: str) -> tuple[Optional[str], str]:
    """(method, source word) of a plan threshold row's `name`."""
    from backend.engine import planning as PL
    if name in ("lthr", "aethr"):
        m = PL.threshold_method(t, name)
    elif name == "mhr":
        m = t.mhr_method or "manual"
    elif name == "cp":
        m = "manual" if t.cp_manual else (t.cp_method or "test")
    else:
        m = "manual"
    return m, _(_SOURCE_OF_METHOD.get(m) or (N_("測試") if name == "cp" else N_("手動")))


def plan_threshold_history(plan) -> dict:
    """Every dated plan row (設定 › 門檻), per threshold, oldest first."""
    from backend.engine import planning as PL
    out: dict = {}
    for name, _label in THRESHOLD_FIELDS:
        rows = []
        for t in sorted(plan.thresholds, key=lambda t: t.date):
            v = getattr(t, name, None)
            if v is None:
                continue
            m, src = _row_source(t, name)
            r = {"date": t.date[:10], "value": float(v), "method": m,
                 "method_label": PL.METHOD_LABEL.get(m) if m else None, "source": src,
                 "origin": "plan.json thresholds", "note": t.note or ""}
            if name == "cp":
                r.update(cp_method=t.cp_method, wprime=t.wprime, cp_manual=t.cp_manual)
            rows.append(r)
        out[name] = rows
    return out


def thresholds_view(plan, day: dt.date, ds=None, coros_profile=None, coros_history=None,
                    planner: Optional[dict] = None) -> dict:
    """CP, LTHR, AeT, max HR, E pace on `day`: the value in effect, who set it, and the history."""
    from backend.engine.planning import threshold_row
    hist = plan_threshold_history(plan)
    eff: dict = {}
    for name, label in THRESHOLD_FIELDS:
        v = plan.threshold_on(name, day)
        row = threshold_row(plan, name, day) if name in ("lthr", "aethr") else None
        last = next((r for r in reversed(hist[name]) if r["date"] <= day.isoformat()), None)
        eff[name] = {"label": _(label), "value": v, "as_of": last["date"] if last else None,
                     "source": (row or {}).get("label") or ((last or {}).get("source")),
                     "origin": "plan" if v is not None else None,
                     "why": _("課表門檻列裡當天以前最近一筆（planning.Plan.threshold_on）") if v is not None
                     else _("課表門檻列沒有當天以前的值：用資料來源的設定或估算（見 dataset）")}
    out: dict = {"date": day.isoformat(), "in_effect": eff, "history": {"plan": hist}}
    if coros_profile or coros_history:
        out["history"]["coros_account"] = {
            "source": _("COROS 帳號（登入／同步時讀到；跑步課表不一定採用）"), "current": coros_profile,
            "changes": coros_history or []}
    if ds is not None:
        out["dataset"] = _dataset_thresholds(ds, plan, day)
        est = out["dataset"].get("as_of_estimate") or {}
        for name, key in (("lthr", "lthr"), ("aethr", "aet"), ("cp", "cp")):
            if eff[name]["value"] is None and est.get(key) is not None:
                eff[name].update(value=est[key], source=est.get(f"{key}_source"), origin="dataset",
                                 why=_("課表門檻列沒有：用資料集當天的值（racepower.athlete.thresholds_as_of："
                                       "當天以前的估算 → 資料來源的設定 → 推估）"))
    if eff["mhr"]["value"] is None and isinstance(coros_profile, dict) and coros_profile.get("max_hr"):
        eff["mhr"].update(value=coros_profile["max_hr"], source=_("COROS 帳號"), origin="coros",
                          as_of=coros_profile.get("at"),
                          why=_("課表門檻列沒有最大心率：用 COROS 帳號的值（engine/hr_profile.py）"))
    if planner:
        out["used_by"] = {"plan_generator": {k: planner.get(k) for k in (
            "cp", "cp_source", "lthr", "lthr_source", "aet", "aet_source", "aet_measured", "lthr_prior",
            "easy_cap_label", "e_pace", "tpace", "hr_model")},
            "why": _("課表產生器（overview.week_plan）這週用的門檻；目標心率／功率都從這裡來")}
    return out


def _dataset_thresholds(ds, plan, day: dt.date) -> dict:
    from backend.engine.wko5expr.dataset import date_to_day
    out: dict = {"source": getattr(ds, "source", None) or "wko5"}
    try:
        from backend.engine import quality_gate as QG
        out["lthr_info"] = QG.lthr_info(ds, plan, day)
    except Exception as e:                  # noqa: BLE001
        out["lthr_info"] = {"error": type(e).__name__}
    hist: dict = {}
    for name in ("runthr", "runftp", "runmhr", "mhr", "runaethr", "weight", "runtpace"):
        vals = (getattr(ds.athlete, "settings", None) or {}).get(name)
        if not vals:
            continue
        try:
            label = ds.setting_label(name)
        except Exception:                   # noqa: BLE001
            label = None
        hist[name] = {"source": label, "values": [{"date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                                                   "value": v} for d, v in vals if v is not None
                                                  and getattr(d, "year", 2) > 1]}
        try:
            hist[name]["on_date"] = ds.setting(name, date_to_day(day))
        except Exception:                   # noqa: BLE001
            pass
    out["settings"] = hist
    out["ignored"] = list(getattr(ds, "settings_ignored", []) or [])
    try:
        from backend.engine.racepower import athlete as A
        out["as_of_estimate"] = A.thresholds_as_of(ds, day)
    except Exception as e:                  # noqa: BLE001
        out["as_of_estimate"] = {"error": type(e).__name__}
    return out


# ---------------------------------------------------------------------------
# sync + log
# ---------------------------------------------------------------------------

def log_tail(n: int = 200) -> dict:
    """The last `n` lines of the app log (applog.py), each passed through applog.redact again."""
    from backend import applog
    p = applog.log_file()
    if p is None:
        return {"file": None, "lines": [], "why": _("沒有 app.log（WKO5COACH_LOG_FILE=0 或還沒寫過）")}
    lines: list[str] = []
    for f in (p.with_name(p.name + ".1"), p):
        try:
            lines += f.read_text("utf-8", errors="replace").splitlines()
        except OSError:
            continue
    n = max(1, min(2000, int(n)))
    tail = [applog.redact(x) for x in lines[-n:]]
    slow = [x for x in tail if " WARNING " in x or " ERROR " in x]
    return {"file": p.name, "lines": tail, "warnings_errors": slow[-100:],
            "source": _("applog（已去掉 token、e-mail、網址參數、家目錄）")}


# ---------------------------------------------------------------------------
# export config
# ---------------------------------------------------------------------------

BLOCKS = (
    ("profile", N_("基本資料問卷、體重、主要訓練項目"),
     ("athlete.setup.", "athlete.experience", "athlete.primary_sport", "athlete.timezone", "athlete.region",
      "athlete.pmc_start", "athlete.coros_profile")),
    ("data_source", N_("資料來源設定"), ("sync.", "charts.", "power.", "activities.")),
    ("races", N_("比賽成績"), ("athlete.race_results",)),
    ("calendar", N_("不排課日期、休息日、高海拔過夜"), ("plan.blackouts", "altitude.nights")),
    ("plan_prefs", N_("課表偏好"), ("plan.prefs.", "plan.hr_zone_model", "plan.b2b.", "plan.suggestions.",
                               "plan.match.", "plan.push.")),
    ("auto", N_("自動調整設定"), ("plan.auto.",)),
    ("advanced", N_("進階設定與每人校正值（含 Zone 3 解鎖數字）"), ("athlete.calib", "coros.", "rpe.", "racepower.",
                                                            "injury.", "debug.")),
)


def _block_of(key: str) -> str:
    for name, _t, prefixes in BLOCKS:
        if any(key == p or key.startswith(p) for p in prefixes):
            return name
    return "other"


def export_config(settings: dict, plan, injuries: list, tags: list, overrides: list, gpx_events: set,
                  exported_at: Optional[str] = None) -> dict:
    """The athlete's settings as one JSON (schema_version): user_settings keys by block (key names
    as stored), the plan's thresholds / weights / profile / events, the injury log, the
    activities' manual overrides. Never the backup folder, the 課表訂閱 link, the sync results,
    a password, a token; never the activity files."""
    from dataclasses import asdict
    blocks: dict = {name: {"title": _(title), "keys": {}} for name, title, _p in BLOCKS}
    blocks["other"] = {"title": _("其他設定"), "keys": {}}
    for k in sorted(settings):
        if k in EXPORT_EXCLUDE or _SECRET_KEY.search(k):
            continue
        blocks[_block_of(k)]["keys"][k] = settings[k]
    if not blocks["other"]["keys"]:
        blocks.pop("other")
    profile = dict(plan.profile or {})
    blocks["profile"]["plan_profile"] = profile
    blocks["profile"]["weights"] = [asdict(w) for w in sorted(plan.weights, key=lambda w: w.date)]
    blocks["thresholds"] = {"title": _("門檻紀錄（CP／LTHR／AeT／最大心率）"), "origin": "plan.json thresholds",
                            "rows": [asdict(t) for t in sorted(plan.thresholds, key=lambda t: t.date)]}
    blocks["events"] = {"title": _("賽事清單"), "origin": "plan.json events",
                        "rows": [{**asdict(e), "gpx_uploaded": e.id in gpx_events}
                                 for e in sorted(plan.events, key=lambda e: e.date)],
                        "phases": [asdict(p) for p in plan.phases]}
    blocks["injuries"] = {"title": _("傷病紀錄（含生病）"), "origin": "injury_events", "rows": injuries}
    blocks["activity_overrides"] = {"title": _("活動的手動覆寫（類型、努力程度、標籤、疼痛、壞檔排除、地形）"),
                                    "origin": "activity_tags + workout_files", "tags": tags,
                                    "terrain": overrides}
    return {"schema_version": SCHEMA_VERSION,
            "exported_at": exported_at or dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "excluded": [_(x) for x in EXPORT_NOT_INCLUDED],
            "blocks": blocks}
