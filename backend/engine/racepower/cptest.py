"""
Formal CP tests (3′ / 12′, the Stryd / Palladino protocol the athlete uses)
found in the synced FIT files (~/.wko5coach/fit/**), for the race-power
capacity model and back-test. A detected test is only a SUGGESTION: it is
never written into the season plan's thresholds; the user applies it.

Per bout the power is the mean-max over the bout's own duration from the
1-s records (the lap average when there are no records).

Non-maximal bout (a bout that was not all-out):
  * the workbook's "falling" rule (cp.validity): the shorter bout must have
    the higher power — a 3′ at or below the 12′ power was not maximal;
  * 自組: its peak HR is ≥ HR_GAP_BPM below the other bout's peak. Evidence:
    the 2026-09-30 test, 3′ 217 W peak 146 bpm vs 12′ 221 W peak 171 bpm.
With both bouts maximal: the 2-parameter model through the two points
(work = CP·t + W′, Jones & Vanhatalo 2017). With one maximal bout: the
single-bout estimate CP = P − W′/t with a W′ prior = Ruiz-Alias et al. 2025's
amateur Stryd 9/3 mean (men 13.1 ± 4.0 kJ, women 6.4 ± 2.2 kJ; the range is
± 1 SD, a higher W′ gives the lower CP), the workbook's RWC band beside it.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Optional

import numpy as np

from backend.engine.racepower import cp as CP

SHORT_S = (150.0, 210.0)       # 3′ ± 30 s
LONG_S = (660.0, 780.0)        # 12′ ± 60 s
BOUT_MIN_RATIO = 1.3           # 自組: a bout is ≥ 1.3 × the median power of the other laps
HR_GAP_BPM = 10.0              # 自組, see the module docstring
FRESH_DAYS = 90                # as PLAN_CP_MAX_AGE_DAYS
CACHE_NAME = "racepower_cptests.json"
_KEY_VERSION = 2


def _mean_max(p: np.ndarray, n: int) -> Optional[float]:
    p = np.nan_to_num(np.asarray(p, float))
    if n <= 0 or len(p) < n:
        return None
    c = np.cumsum(np.concatenate([[0.0], p]))
    return float((c[n:] - c[:-n]).max() / n)


def detect(laps: list[dict]) -> Optional[dict]:
    """laps = [{"t" (s), "p" (avg W), "hr_max", "pw" (the lap's 1-s power,
    optional)}]. Returns the bouts and their maximality, or None when the
    laps are not a 3′/12′ test."""
    ls = [l for l in laps if l.get("t") and l.get("p")]
    if len(ls) < 3:
        return None
    short = [l for l in ls if SHORT_S[0] <= l["t"] <= SHORT_S[1]]
    long_ = [l for l in ls if LONG_S[0] <= l["t"] <= LONG_S[1]]
    if not short or not long_:
        return None
    a = max(short, key=lambda l: l["p"])
    b = max(long_, key=lambda l: l["p"])
    rest = [l["p"] for l in ls if l is not a and l is not b]
    base = float(np.median(rest)) if rest else 0.0
    if not base or a["p"] < BOUT_MIN_RATIO * base or b["p"] < BOUT_MIN_RATIO * base:
        return None
    bouts = []
    for l, nominal in ((a, 180), (b, 720)):
        pw = l.get("pw")
        # mean-max inside the lap only (a 3′ window inside the 12′ bout must not count)
        mm = _mean_max(pw, min(int(round(l["t"])), len(pw))) if pw is not None and len(pw) else None
        bouts.append({"nominal_s": nominal, "t": float(round(l["t"])), "p_lap": float(l["p"]),
                      "p": mm if mm and mm >= l["p"] * 0.97 else float(l["p"]), "hr_max": l.get("hr_max"),
                      "maximal": True, "why": ""})
    s, lo = bouts
    if s["p"] <= lo["p"]:
        s.update(maximal=False, why=f"3′ {s['p']:.0f} W 不高於 12′ {lo['p']:.0f} W（功率應隨時間遞減）")
    if s.get("hr_max") and lo.get("hr_max"):
        gap = lo["hr_max"] - s["hr_max"]
        if gap >= HR_GAP_BPM:
            s.update(maximal=False, why=(s["why"] + "；" if s["why"] else "") + f"最高心率 {s['hr_max']:.0f} 比 12′ 的 {lo['hr_max']:.0f} 低 {gap:.0f} bpm")
        elif -gap >= HR_GAP_BPM:
            lo.update(maximal=False, why=f"最高心率 {lo['hr_max']:.0f} 比 3′ 的 {s['hr_max']:.0f} 低 {-gap:.0f} bpm")
    return {"bouts": bouts}


# Ruiz-Alias et al. 2025 (EJSS, PMC11770271, full text): amateur runners'
# Stryd 9/3 two-point W′, men 13.1 ± 4.0 kJ, women 6.4 ± 2.2 kJ (n = 19).
W_PRIME_PRIOR_KJ = {"male": (13.1, 4.0), "female": (6.4, 2.2)}


def w_prime_prior(weight: float, sex: str = "male", wind: bool = False) -> dict:
    """W′ prior for a single-bout estimate: Ruiz-Alias 2025's amateur mean,
    the range ± 1 SD; the workbook's RWC "Medium" band is reported beside it
    as a cross-check."""
    sex = "female" if str(sex).lower().startswith("f") else "male"
    m, sd = W_PRIME_PRIOR_KJ[sex]
    bands = CP.RWC_BANDS[(sex, bool(wind), "jkg")]
    return {"lo": (m - sd) * 1000.0, "hi": (m + sd) * 1000.0, "mid": m * 1000.0,
            "source": f"Ruiz-Alias 2025 業餘{'男' if sex == 'male' else '女'}性 Stryd 9/3 測試 W′ {m} ± {sd} kJ",
            "rwc_band_j": [bands[1] * weight, bands[2] * weight]}


def estimate(test: dict, weight: float, sex: str = "male", wind: bool = False) -> dict:
    """CP / W′ from the detected bouts (see the module docstring)."""
    mx = [b for b in test["bouts"] if b["maximal"]]
    if len(mx) == 2:
        f = CP.fit_cp([(b["t"], b["p"]) for b in mx])
        return {"method": "two_point", "cp": f["cp"], "w_prime": f["w_prime"], "cp_range": [f["cp"], f["cp"]],
                "label": "3′ / 12′ 兩點（CP 模型）"}
    if not mx:
        return {"method": None, "cp": None, "w_prime": None, "cp_range": None, "label": "兩段都不是全力"}
    b = mx[0]
    pr = w_prime_prior(weight, sex, wind)
    cp = b["p"] - pr["mid"] / b["t"]
    return {"method": "single_bout", "cp": cp, "w_prime": pr["mid"],
            "cp_range": [b["p"] - pr["hi"] / b["t"], b["p"] - pr["lo"] / b["t"]], "w_prime_prior": pr,
            "bout_s": b["t"], "bout_p": b["p"],
            "label": f"只有 {b['nominal_s'] // 60:.0f}′ 是全力：CP = P − W′/t，W′ 用先驗"}


# ---------------------------------------------------------------------------
# the FIT folder
# ---------------------------------------------------------------------------

def _file_date(p: Path) -> Optional[dt.date]:
    import re
    m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
    try:
        return dt.date.fromisoformat(m.group(1)) if m else None
    except ValueError:
        return None


def _read(p: Path) -> Optional[dict]:
    from backend.files.fit_reader import parse_fit
    try:
        r = parse_fit(str(p))
    except Exception:                       # noqa: BLE001
        return None
    sport = str(r.session.get("sport") or r.sport or "").lower()
    if "run" not in sport:
        return None
    laps = []
    ts = np.asarray(r.time_s, float) if len(r.time_s) else None
    for l in r.laps:
        row = {"t": l.get("total_timer_time"), "p": l.get("avg_power"), "hr_max": l.get("max_heart_rate"),
               "hr_avg": l.get("avg_heart_rate")}
        st = l.get("start_time")
        if r.has_power and ts is not None and st is not None and r.start_time is not None and row["t"]:
            a = (st - r.start_time).total_seconds()
            m = (ts >= ts[0] + a) & (ts < ts[0] + a + row["t"])
            row["pw"] = np.asarray(r.power_w, float)[m[:len(r.power_w)]] if m.any() else None
        laps.append(row)
    t = detect(laps)
    curve = mean_max_curve(np.asarray(r.power_w, float)) if r.has_power else None
    return {"test": t, "date": (r.start_time.date().isoformat() if r.start_time else None), "file": p.name,
            "sport": sport, "curve": curve,
            "laps": [{k: v for k, v in l.items() if k != "pw"} for l in laps] if t else None}


def mean_max_curve(p: np.ndarray, grid=None) -> Optional[list]:
    """[[t], [p]] mean-max of 1-s power on the racepower envelope grid."""
    from backend.engine.racepower.athlete import GRID
    p = np.nan_to_num(np.asarray(p, float))
    if len(p) < 10:
        return None
    c = np.cumsum(np.concatenate([[0.0], p]))
    xs, ys = [], []
    for g in grid or GRID:
        n = int(g)
        if n > len(p):
            break
        xs.append(float(n))
        ys.append(round(float((c[n:] - c[:-n]).max() / n), 2))
    return [xs, ys]


def _files(home: Path, since: dt.date, until: dt.date) -> list[dict]:
    """Every running FIT file in home/fit/** dated since…until, cached per file."""
    cache_p = home / CACHE_NAME
    try:
        cache = json.loads(cache_p.read_text("utf-8"))
        if cache.get("v") != _KEY_VERSION:
            cache = {"v": _KEY_VERSION, "files": {}}
    except (OSError, ValueError):
        cache = {"v": _KEY_VERSION, "files": {}}
    out, dirty = [], False
    root = home / "fit"
    for p in sorted(root.rglob("*.fit")) if root.exists() else []:
        d = _file_date(p)
        if d is None or not (since <= d <= until):
            continue
        st = p.stat()
        key = str(p.relative_to(root))
        stamp = [st.st_size, int(st.st_mtime)]
        hit = cache["files"].get(key)
        if not hit or hit[0] != stamp:
            hit = [stamp, _read(p)]
            cache["files"][key] = hit
            dirty = True
        if hit[1] and hit[1].get("date") and since.isoformat() <= hit[1]["date"] <= until.isoformat():
            out.append({**hit[1], "path": key})
    if dirty:
        try:
            cache_p.parent.mkdir(parents=True, exist_ok=True)
            cache_p.write_text(json.dumps(cache, ensure_ascii=False, default=float), "utf-8")
        except OSError:
            pass
    return sorted(out, key=lambda x: x["date"])


POWER_CACHE_NAME = "racepower_power_source.json"   # {path: [size, mtime, stryd|watch|none]}


def _classify_file(p: Path) -> str:
    from backend.engine import power_source as PS
    from backend.files.fit_to_channels import fit_to_channels
    try:
        return fit_to_channels(p.read_bytes()).power_source
    except Exception:                       # noqa: BLE001
        return PS.NONE


def power_sources(home: Path, paths: list[str]) -> dict[str, str]:
    """{path (relative to home/fit): stryd / watch / none} of the given FIT
    files (backend/engine/power_source.py), cached per file stamp in its own
    file (the curve cache's version is left alone)."""
    cache_p = home / POWER_CACHE_NAME
    try:
        cache = json.loads(cache_p.read_text("utf-8"))
    except (OSError, ValueError):
        cache = {}
    root, out, dirty = home / "fit", {}, False
    for key in paths:
        p = root / key
        try:
            st = p.stat()
        except OSError:
            continue
        stamp = [st.st_size, int(st.st_mtime)]
        hit = cache.get(key)
        if not hit or hit[:2] != stamp:
            hit = stamp + [_classify_file(p)]
            cache[key] = hit
            dirty = True
        out[key] = hit[2]
    if dirty:
        try:
            cache_p.parent.mkdir(parents=True, exist_ok=True)
            cache_p.write_text(json.dumps(cache), "utf-8")
        except OSError:
            pass
    return out


def _usable(home: Path, files: list[dict], accept_watch: bool) -> list[dict]:
    """Drop the files whose power is watch-estimated (unless accepted)."""
    from backend.engine import power_source as PS
    if accept_watch or not files:
        return files
    src = power_sources(home, [f["path"] for f in files])
    return [f for f in files if PS.usable(src.get(f["path"]), False)]


def scan(home: Path, since: dt.date, until: dt.date, accept_watch: bool = True) -> list[dict]:
    """Every 3′/12′ test in the FIT folder dated since…until (one per date).
    `accept_watch` False: a test recorded with watch-estimated power is no test."""
    out, seen = [], set()
    files = [f for f in _files(home, since, until) if f.get("test")]
    for f in _usable(home, files, accept_watch):
        if f.get("test") and f["date"] not in seen:
            seen.add(f["date"])
            out.append({**f["test"], "date": f["date"], "file": f["file"], "sport": f["sport"],
                        "laps": f.get("laps"), "path": f["path"]})
    return out


def curves(home: Path, since: dt.date, until: dt.date, accept_watch: bool = True) -> list[dict]:
    """Mean-max curves of the synced running FIT files (COROS / TP), so a
    session WKO5 has not imported yet (today's test) still enters the
    envelope. Duplicates of WKO5 activities are harmless: the envelope is a
    maximum. Only file names with a YYYY-MM-DD date are read (`_file_date`):
    the COROS files, not TP's tp_YYYY_MM_DD_… names. `accept_watch` False:
    watch-estimated power is left out."""
    files = [f for f in _files(home, since, until) if f.get("curve")]
    return [{"date": f["date"], "file": f["file"], "path": f["path"], "xs": f["curve"][0], "ys": f["curve"][1]}
            for f in _usable(home, files, accept_watch)]
