"""
Per-athlete heat calibration (generalize-athlete plan B4, engine/calibrate.py items).

T1 — hadley_hr_beta: HR per Hadley unit at a given effort.
    Fit: the route efforts of the route index (routes.HOME/index.json, with
    `wx` from a weather-enabled routes build) — OLS with route fixed effects
    (demeaned), effort (power, else speed), moving minutes, time of day and
    β·(Hadley − 120), as backend/scripts/heat_backtest.py does. At least
    MIN_N efforts and a Hadley span of MIN_SPAN; k = K (推估).
    Default β₀ = 0.3 bpm／Hadley (推估): Jenkins 2023's 1 bpm per °C, put in
    Hadley units with temperature and dew point rising together (≈ 0.29;
    with the dew point fixed it would be 0.56). Owner decision 2026-10-02.
    The author's 271 efforts fit 0.224 ± 0.036; shrunk (w = 271/331) that is
    ≈ 0.238 — inside ±1 SE of the old constant.

T3 — humidity_default: the RH (%) to assume when a run has a temperature
    but no humidity (engine/zone_events.RH_DEFAULT): the median RH of the
    athlete's activities with weather (activity_weather.json). ≥ 20
    activities; default 60 % (推估, temperate median).

P5 — home_temp_c / home_rh_pct: the training conditions the race-power
    model starts from when the last 90 days have no weather
    (racepower/athlete.fallback_training): the median temperature / RH of the
    athlete's activities with weather. ≥ 10 activities; defaults the
    reference conditions of racepower/env.py (12 °C / 70 %; altitude 200 m —
    the runs' own median elevation is used whenever there are runs).

Every fit reads only local files (no network); `current(name)` is the value
in effect: a manual value, else the fit (memoised on the source file's
mtime), else the default.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Optional

import numpy as np

from backend.engine import calibrate as CAL

H0 = 120.0
BETA_DEFAULT = 0.3
BETA_DEFAULT_SE = 0.15         # 推估: wide, so the fired-shift threshold stays conservative
BETA_DEFAULT_SRC = ("推估：Jenkins 2023 氣溫每 +1 °C 心率 +1 bpm（騎車），換算成熱指數 Hadley"
                    "（溫度和露點一起升時約 0.29 bpm／Hadley）")
MIN_N = 30
MIN_SPAN = 40.0
K = 60
BOUNDS = (0.0, 1.0)
MIN_MIN = 20.0                  # effort ≥ 20 moving minutes (heat_backtest default)

RH_DEFAULT = 60.0
RH_MIN_N = 20
HOME_MIN_N = 10
HOME_DEFAULTS = {"home_alt_m": 200.0, "home_temp_c": 12.0, "home_rh_pct": 70.0}   # racepower/env.py reference


def _routes_home() -> Path:
    from backend.engine import routes as R
    return Path(R.HOME)


# ---------------------------------------------------------------------------
# T1: Hadley β
# ---------------------------------------------------------------------------

def tod(h: int) -> str:
    return "morning" if h < 11 else "midday" if h < 16 else "evening"


def effort_rows(idx: dict, min_min: float = MIN_MIN) -> list[dict]:
    """Running route efforts with weather, one per (file, route)."""
    out, seen = [], set()
    for kind in ("routes", "segments"):
        for r in idx.get(kind, []) or []:
            for e in r.get("efforts", []) or []:
                wx = e.get("wx")
                mv = e.get("moving_s") or 0
                if e.get("sport") != "run" or not wx or e.get("avg_hr") is None or mv < min_min * 60 \
                        or wx.get("hadley") is None:
                    continue
                key = (e.get("file"), r.get("id"))
                if key in seen:
                    continue
                seen.add(key)
                st = dt.datetime.fromisoformat(e["start"])
                spd = (e.get("dist_km") or 0) / (mv / 3600.0) if mv else None
                out.append({"route": f"{kind}:{r.get('id')}", "file": e.get("file"), "date": st.date(),
                            "hour": st.hour, "hr": float(e["avg_hr"]),
                            "p": float(e["avg_power"]) if e.get("avg_power") else None,
                            "v": spd, "mv": mv / 60.0, "hadley": float(wx["hadley"]), "temp_c": wx.get("temp_c")})
    return out


def ols_fe(rows, cols, group="route"):
    """Demean y and X within groups (groups of ≥ 2), OLS; {"coef", "se", "rss", "n", "k", "groups"}."""
    by = defaultdict(list)
    for i, r in enumerate(rows):
        by[r[group]].append(i)
    keep = [i for g, ix in by.items() if len(ix) >= 2 for i in ix]
    if not keep:
        return {"coef": {}, "se": {}, "rss": 0.0, "n": 0, "k": 0, "groups": 0}
    y = np.array([rows[i]["hr"] for i in keep], float)
    X = np.array([[c(rows[i]) for c in cols.values()] for i in keep], float)
    gid = [rows[i][group] for i in keep]
    for g in set(gid):
        m = np.array([x == g for x in gid])
        y[m] -= y[m].mean()
        X[m] -= X[m].mean(axis=0)
    ok = X.std(axis=0) > 1e-12
    Xk = X[:, ok]
    beta, *_ = np.linalg.lstsq(Xk, y, rcond=None)
    res = y - Xk @ beta
    n, k = len(y), Xk.shape[1] + len(set(gid))
    s2 = (res @ res) / max(1, n - k)
    cov = s2 * np.linalg.pinv(Xk.T @ Xk)
    names = [nm for nm, o in zip(cols, ok) if o]
    return {"coef": dict(zip(names, beta.tolist())), "se": dict(zip(names, np.sqrt(np.diag(cov)).tolist())),
            "rss": float(res @ res), "n": n, "k": k, "groups": len(set(gid))}


def fit_beta_rows(rows: list[dict]) -> Optional[CAL.Fit]:
    """β from effort rows (power basis when most rows have power, else speed);
    None below MIN_N or a Hadley span < MIN_SPAN."""
    pw = [r for r in rows if r.get("p")]
    basis, use = ("p", pw) if len(pw) >= max(MIN_N, len(rows) / 2) else ("v", [r for r in rows if r.get("v")])
    if len(use) < MIN_N:
        return None
    h = [r["hadley"] for r in use]
    if max(h) - min(h) < MIN_SPAN:
        return None
    cols = {basis: (lambda b: lambda r: r[b])(basis), "mv": lambda r: r["mv"],
            "midday": lambda r: 1.0 if tod(r["hour"]) == "midday" else 0.0,
            "evening": lambda r: 1.0 if tod(r["hour"]) == "evening" else 0.0,
            "heat": lambda r: r["hadley"] - H0}
    f = ols_fe(use, cols)
    b, se = f["coef"].get("heat"), f["se"].get("heat")
    if b is None or f["n"] < MIN_N or not math.isfinite(b):
        return None
    return CAL.Fit(float(b), None if se is None else float(se), int(f["n"]))


def _index(root: Optional[Path] = None) -> Optional[dict]:
    p = Path(root or _routes_home()) / "index.json"
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return None


def fit_hadley_beta(ds=None, today: Optional[dt.date] = None, root: Optional[Path] = None) -> Optional[CAL.Fit]:
    idx = _index(root)
    return None if idx is None else fit_beta_rows(effort_rows(idx))


# ---------------------------------------------------------------------------
# T3 / P5: humidity and home conditions
# ---------------------------------------------------------------------------

def _weather_acts(root: Optional[Path] = None) -> list[dict]:
    from backend.engine import route_weather as RW
    doc = RW.load_activity_weather(Path(root or _routes_home()))
    return [v for v in (doc.get("activities") or {}).values() if isinstance(v, dict)]


def _med_fit(vals: list, min_n: int) -> Optional[CAL.Fit]:
    vals = [float(v) for v in vals if v is not None and math.isfinite(float(v))]
    if len(vals) < min_n:
        return None
    m = median(vals)
    sd = float(np.std(vals)) if len(vals) > 1 else 0.0
    return CAL.Fit(m, 1.2533 * sd / math.sqrt(len(vals)), len(vals))      # SE of a median ≈ 1.25·σ/√n


def _field(acts: list[dict], *names):
    out = []
    for a in acts:
        for n in names:
            if a.get(n) is not None:
                out.append(a[n])
                break
    return out


def fit_rh(ds=None, today=None, root=None) -> Optional[CAL.Fit]:
    return _med_fit(_field(_weather_acts(root), "rh_pct", "rh"), RH_MIN_N)


def fit_home(field_names):
    def f(ds=None, today=None, root=None):
        return _med_fit(_field(_weather_acts(root), *field_names), HOME_MIN_N)
    return f


# ---------------------------------------------------------------------------
# registration and the value in effect
# ---------------------------------------------------------------------------

def _register() -> None:
    CAL.register(CAL.Item(
        name="hadley_hr_beta", label="熱 β（心率／熱指數）", unit="bpm／Hadley", default=BETA_DEFAULT,
        default_src=BETA_DEFAULT_SRC, k=K, min_n=MIN_N, fit=fit_hadley_beta, bounds=BOUNDS, digits=3,
        help="熱指數（Hadley = °F 氣溫 + 相對濕度 %）每高 1，同樣強度的心率多幾 bpm；越野心率預測、"
             "同功率心率的變化、爬坡 VAM 都先把心率移到 Hadley 120。"))
    CAL.register(CAL.Item(
        name="humidity_default", label="沒有濕度時假設的濕度", unit="%", default=RH_DEFAULT,
        default_src="推估（溫帶地區的中位數）", k=10, min_n=RH_MIN_N, fit=fit_rh, bounds=(10.0, 100.0), digits=0,
        help="活動有溫度、沒有濕度時（例如只有手錶溫度）用這個濕度算熱指數。本人值 = 有天氣資料的活動濕度中位數。"))
    for name, label, unit, fields_, lo_hi in (
            ("home_temp_c", "平常訓練的氣溫", "°C", ("temp_c",), (-30.0, 45.0)),
            ("home_rh_pct", "平常訓練的濕度", "%", ("rh_pct", "rh"), (5.0, 100.0))):
        CAL.register(CAL.Item(
            name=name, label=label, unit=unit, default=HOME_DEFAULTS[name],
            default_src="racepower/env.py 的參考條件（200 m／12 °C／70 %）", k=5, min_n=HOME_MIN_N,
            fit=fit_home(fields_), bounds=lo_hi, digits=0,
            help="比賽功率從你平常的訓練條件換算到比賽當天；比賽前 90 天沒有天氣資料時用這個。"))


_register()

_MEMO: dict = {}
_TTL_S = 30.0


def _stamp(name: str) -> str:
    root = _routes_home()
    f = root / ("index.json" if name == "hadley_hr_beta" else "activity_weather.json")
    try:
        return f"{f}:{f.stat().st_mtime_ns}"
    except OSError:
        return f"{f}:-"


def current(name: str) -> dict:
    """The entry in effect for a heat item: a manual value (source user), else
    a fit of the local files (shrunk; memoised on the file's mtime), else the
    default. {"value", "se", "n", "source", "personal", "w"}."""
    item = CAL._registry()[name]
    stored = None
    try:
        stored = CAL.stored_entry(name)
    except Exception:                       # noqa: BLE001
        stored = None
    if stored and stored.get("source") == "user" and stored.get("value") is not None:
        return {**stored}
    now = time.monotonic()
    m = _MEMO.get(name)
    if m and now - m[0] < _TTL_S:
        return m[2]
    st = _stamp(name)
    if m and m[1] == st:
        _MEMO[name] = (now, st, m[2])
        return m[2]
    try:
        e = CAL.shrink(item, item.fit(None, None))
    except Exception:                       # noqa: BLE001 — a broken cache file
        e = None
    out = e or CAL.resolve(item, None)
    _MEMO[name] = (now, st, out)
    return out


def clear_memo() -> None:
    _MEMO.clear()


def hr_beta() -> dict:
    """{"beta", "se", "n", "source", "src"} for the Hadley β (T1)."""
    e = current("hadley_hr_beta")
    src = e.get("source")
    if src == "user":
        txt = f"熱 β {e['value']:.3f} bpm／Hadley（你在進階設定手動指定）"
        se = BETA_DEFAULT_SE
    elif src == "fitted":
        txt = (f"熱 β {e['value']:.3f} bpm／Hadley（本人 {e['n']} 段路線 effort 擬合 {e['personal']:.3f}，"
               f"權重 {e['w']:.0%}，其餘用預設 {BETA_DEFAULT}）")
        se = e.get("se") or BETA_DEFAULT_SE
    else:
        txt = f"熱 β {BETA_DEFAULT} bpm／Hadley（{BETA_DEFAULT_SRC}；本人資料不夠擬合）"
        se = BETA_DEFAULT_SE
    return {"beta": float(e["value"]), "se": float(se), "n": int(e.get("n") or 0), "source": src, "src": txt}
