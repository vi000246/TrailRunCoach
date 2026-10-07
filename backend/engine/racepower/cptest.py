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
  * 推估: its peak HR is ≥ HR_GAP_BPM below the other bout's peak. Evidence:
    one runner's test, a 3′ just below the 12′ power with a peak HR ~25 bpm lower.
With both bouts maximal: the 2-parameter model through the two points
(work = CP·t + W′, Jones & Vanhatalo 2017). With one maximal bout: the
single-bout estimate CP = P − W′/t with a W′ prior = Ruiz-Alias et al. 2025's
amateur Stryd 9/3 mean (men 13.1 ± 4.0 kJ, women 6.4 ± 2.2 kJ; the range is
± 1 SD, a higher W′ gives the lower CP), the workbook's RWC band beside it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np

from backend.engine.racepower import cp as CP

SHORT_S = (150.0, 210.0)       # 3′ ± 30 s
LONG_S = (660.0, 780.0)        # 12′ ± 60 s
BOUT_MIN_RATIO = 1.3           # 推估: a bout is ≥ 1.3 × the median power of the other laps
HR_GAP_BPM = 10.0              # 推估, see the module docstring
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
    """The date in a synced file name: COROS …_YYYY-MM-DD_….fit, TP tp_YYYY_MM_DD_….fit."""
    import re
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", p.name) or re.match(r"tp_(\d{4})_(\d{2})_(\d{2})_", p.name)
    try:
        return dt.date(*(int(x) for x in m.groups())) if m else None
    except ValueError:
        return None


def unused_folder() -> str:
    """The synced folder NOT in use ("coros" / "tp"): only the 資料來源's
    files are scanned (backend/sync/primary.py), like the charts."""
    from backend.engine.wko5expr.datasource import read_setting
    from backend.sync import primary as P
    return P.other_folder(read_setting(P.SETTING_KEY, None))


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


def _primary_files(home: Path, since: dt.date, until: dt.date) -> list[dict]:
    """_files of the 資料來源 only (the other synced folder is left out)."""
    return _files(home, since, until, skip_folder=unused_folder())


_LIST_MEMO: dict = {}
POOL_MIN_READS = 12            # fewer unread files than this: read inline (no pool start-up)


def _dir_sig(dirs: list[str], root: Path) -> Optional[tuple]:
    """mtimes of the listed folders (a file added / removed / renamed changes
    its folder's) and of the FIT dataset cache indexes (rewritten when a
    rebuild sees a changed file)."""
    from backend.engine.wko5expr import fitcache
    out = []
    try:
        for d in dirs:
            out.append(os.stat(d).st_mtime_ns)
    except OSError:
        return None
    for f in ("coros", "tp"):
        try:
            out.append(fitcache.index_path_of(root / f).stat().st_mtime_ns)
        except OSError:
            out.append(None)
    return tuple(out)


def _listing(root: Path) -> list[tuple]:
    """[(path, key, date, stamp)] of every *.fit under root (sorted). A PD
    refit per day (the as-of LTHR estimates, the back-test) calls _files
    hundreds of times; each call used to walk and stat the whole folder.
    Reused while no folder changed (_dir_sig)."""
    from backend.engine.wko5expr import fitcache
    m = _LIST_MEMO.get(str(root))
    if m is not None and _dir_sig(m[0], root) == m[1]:
        return m[2]
    rows, dirs = [], []
    if root.exists():
        stack = [str(root)]
        while stack:
            d = stack.pop()
            dirs.append(d)
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.is_dir():
                            stack.append(e.path)
                        elif (e.name.lower() if os.name == "nt" else e.name).endswith(".fit"):
                            p = Path(e.path)
                            fd = _file_date(p)
                            if fd is None:
                                continue
                            try:
                                st = e.stat()
                            except OSError:
                                continue
                            rows.append((p, str(p.relative_to(root)), fd, fitcache.stamp_s(p, st)))
            except OSError:
                continue
    rows.sort(key=lambda r: r[0])
    _LIST_MEMO[str(root)] = (dirs, _dir_sig(dirs, root), rows)
    return rows


_CACHE_MEMO: dict = {}


def _load_cache(cache_p: Path) -> dict:
    """racepower_cptests.json, parsed once per file version (it is MBs)."""
    try:
        mt = cache_p.stat().st_mtime_ns
    except OSError:
        mt = None
    m = _CACHE_MEMO.get(str(cache_p))
    if m is not None and m[0] == mt and mt is not None:
        return m[1]
    try:
        cache = json.loads(cache_p.read_text("utf-8"))
        if cache.get("v") != _KEY_VERSION:
            cache = {"v": _KEY_VERSION, "files": {}}
    except (OSError, ValueError):
        cache = {"v": _KEY_VERSION, "files": {}}
    _CACHE_MEMO[str(cache_p)] = (mt, cache)
    return cache


def _prefetch_one(args: tuple) -> tuple:
    """(_read, raw bad-file entry, power source) of one FIT file — what
    _files, bad_files and power_sources would each parse it for, in one
    pool task. `need_fc` False: the FIT dataset cache already has the bad /
    power fields (no second parser run)."""
    path, need_fc = args
    p = Path(path)
    bad = pw = None
    if need_fc:
        from backend.engine import bad_activity as BA
        from backend.engine.wko5expr import fitcache
        from backend.files.fit_to_channels import fit_to_channels
        try:
            fc = fit_to_channels(p.read_bytes())
            st = fc.start_time
            if st is not None and st.tzinfo is None:
                st = st.replace(tzinfo=dt.timezone.utc)
            bad = [st.isoformat() if st is not None else None, fitcache.group_of(fc.sport, fc.sub_sport),
                   BA.features(fc.elapsedtime, fc.channels.get("elapseddistance"), fc.channels.get("power"))]
            pw = fc.power_source
        except Exception:                   # noqa: BLE001 — as _bad_entry / _classify_file
            bad, pw = ["", None, None], None
    return _read(p), bad, pw


def _pool_map(fn, items: list) -> list:
    """fn over items in a process pool like the dataset's FIT parse
    (fitcache: spawn, at most _pool_size workers), inline when few or when
    the pool fails."""
    from backend.engine.wko5expr import fitcache
    n = fitcache._pool_size(len(items)) if len(items) >= POOL_MIN_READS else 0
    if n > 1:
        try:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=n, mp_context=mp.get_context("spawn"),
                                     initializer=fitcache._worker_init) as ex:
                return list(ex.map(fn, items, chunksize=4))
        except Exception:                   # noqa: BLE001 — a pool failure: read inline
            pass
    return [fn(x) for x in items]


def _prefetch(home: Path, rows: list, files: dict) -> None:
    """A cold folder (a new user, a restored copy): read EVERY unread file
    now, in the pool, and fill the bad-file and power-source caches from the
    same pass. The as-of estimates walk the history one 90-day window at a
    time, so file by file inline this was ~1900 single-threaded parses (2-3
    parsers each) interleaved with rewriting the MB-sized caches."""
    from backend.engine import power_source as PS
    from backend.engine.wko5expr import fitcache
    bad_p, pw_p = home / BAD_CACHE_NAME, home / POWER_CACHE_NAME
    bad_v, pw_v = bad_cache_code(), power_cache_code()
    bad_c, pw_c = _load_json(bad_p, bad_v), _load_json(pw_p, pw_v)
    jobs = []
    for r in rows:
        e = _fit_entry(home, r[1])
        have = bool(e and (e.get("bad") or [None])[0] == fitcache.versions()["bad"]
                    and (e.get("power") or [None])[0] == fitcache.versions()["power"])
        fresh = (bad_c.get(r[1]) or [None, None])[:2] == r[3] and (pw_c.get(r[1]) or [None, None])[:2] == r[3]
        jobs.append((str(r[0]), not (have or fresh)))
    res = _pool_map(_prefetch_one, jobs)
    bad_dirty = pw_dirty = False
    for r, (v, bad, pw) in zip(rows, res):
        files[r[1]] = [r[3], v]
        if bad is not None:
            if bad[0] == "":                    # unreadable: what _bad_entry / _classify_file record
                bad_c[r[1]], pw_c[r[1]] = r[3] + [None, None, None], r[3] + [PS.NONE]
            else:
                start = dt.datetime.fromisoformat(bad[0]) if bad[0] else None
                bad_c[r[1]] = r[3] + [_local_start(start), bad[1], bad[2]]
                pw_c[r[1]] = r[3] + [pw]
            bad_dirty = pw_dirty = True
    if bad_dirty:
        _save_json(bad_p, bad_c, bad_v)
    if pw_dirty:
        _save_json(pw_p, pw_c, pw_v)


def _files(home: Path, since: dt.date, until: dt.date, skip_folder: Optional[str] = None) -> list[dict]:
    """Every running FIT file in home/fit/** dated since…until, cached per file;
    `skip_folder` = a top folder of home/fit ("tp") left out unread."""
    cache_p = home / CACHE_NAME
    cache = _load_cache(cache_p)
    files = cache["files"]
    root = home / "fit"
    listing = [r for r in _listing(root) if not (skip_folder and Path(r[1]).parts[0] == skip_folder)]

    def unread(r):
        return not (files.get(r[1]) and files[r[1]][0] == r[3])
    rows = [r for r in listing if since <= r[2] <= until]
    todo = [r for r in rows if unread(r)]
    if todo:
        cold = [r for r in listing if unread(r)]
        if len(cold) >= POOL_MIN_READS:
            _prefetch(home, cold, files)          # the whole folder at once, in the pool
        else:
            for r in todo:
                files[r[1]] = [r[3], _read(r[0])]
        try:
            cache_p.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(cache, ensure_ascii=False, default=float)
            cache_p.write_text(text, "utf-8")
            cache = json.loads(text)        # what a later call reads back (lists, plain floats)
            files = cache["files"]
            _CACHE_MEMO[str(cache_p)] = (cache_p.stat().st_mtime_ns, cache)
        except OSError:
            pass
    lo, hi = since.isoformat(), until.isoformat()
    out = []
    for r in rows:
        v = files[r[1]][1]
        if v and v.get("date") and lo <= v["date"] <= hi:
            out.append({**v, "path": r[1]})
    return sorted(out, key=lambda x: x["date"])


POWER_CACHE_NAME = "racepower_power_source.json"   # {"v", "files": {path: [size, mtime, stryd|watch|none]}}
# SP-341: bump when the power-source / bad-file caches change through code the versions below miss
FILE_CACHE_V = 1

_JSON_MEMO: dict = {}


def power_cache_code() -> str:
    """The version racepower_power_source.json is written with (SP-341): the FIT parse and
    power-source versions of the dataset cache field it mirrors (fitcache.versions) +
    FILE_CACHE_V. A file of another version (or the older unversioned form) is recomputed."""
    from backend.engine.wko5expr import fitcache
    v = fitcache.versions()
    return f"{FILE_CACHE_V}:{v['parse']}:{v['power']}"


def bad_cache_code() -> str:
    """The version of racepower_bad_activity.json (SP-341): the FIT parse and bad-file feature
    versions (fitcache.versions), the sport-group code (engine/codehash.py) + FILE_CACHE_V."""
    from backend.engine.codehash import code_hash
    from backend.engine.wko5expr import fitcache
    v = fitcache.versions()
    return f"{FILE_CACHE_V}:{v['parse']}:{v['bad']}:{code_hash(fitcache.group_of)[:10]}"


def _load_json(p: Path, version: str) -> dict:
    """The entries of a versioned per-file JSON cache, parsed once per file version; {} when
    the file was written with another version (SP-341)."""
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return {}
    m = _JSON_MEMO.get(str(p))
    if m is not None and m[0] == mt:
        doc = m[1]
    else:
        try:
            doc = json.loads(p.read_text("utf-8"))
        except (OSError, ValueError):
            doc = {}
        _JSON_MEMO[str(p)] = (mt, doc)
    files = doc.get("files") if isinstance(doc, dict) and doc.get("v") == version else None
    return files if isinstance(files, dict) else {}


def _save_json(p: Path, files: dict, version: str) -> None:
    """Atomic: a temporary file renamed over the cache (an interrupted write keeps the old one)."""
    doc = {"v": version, "files": files}
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(doc), "utf-8")
        os.replace(tmp, p)
        _JSON_MEMO[str(p)] = (p.stat().st_mtime_ns, json.loads(json.dumps(doc)))
    except OSError:
        pass
    finally:
        tmp.unlink(missing_ok=True)


_STORES: dict = {}


def _fit_entry(home: Path, key: str) -> Optional[dict]:
    """The FIT dataset cache's entry (fitcache.FitStore) of home/fit/<key>
    when it is current: the dataset build already parsed the file, so its
    power source / bad-file features need no second parse."""
    from backend.engine.wko5expr import fitcache
    head, _, rest = key.replace("\\", "/").partition("/")
    if head not in ("coros", "tp") or not rest:
        return None
    d = Path(home) / "fit" / head
    try:
        mt = fitcache.index_path_of(d).stat().st_mtime_ns
    except OSError:
        return None
    m = _STORES.get(str(d))
    if m is None or m[0] != mt:
        m = _STORES[str(d)] = (mt, fitcache.FitStore(d))
    try:
        return m[1].entry(d / rest)
    except (OSError, ValueError):
        return None


def _classify_file(p: Path, home: Optional[Path] = None, key: Optional[str] = None) -> str:
    from backend.engine import power_source as PS
    from backend.engine.wko5expr import fitcache
    from backend.files.fit_to_channels import fit_to_channels
    e = _fit_entry(home, key) if home is not None and key else None
    slot = (e or {}).get("power")
    if slot and slot[0] == fitcache.versions()["power"]:
        return slot[1]                      # the same fit_to_channels(...).power_source, from the dataset parse
    try:
        return fit_to_channels(p.read_bytes()).power_source
    except Exception:                       # noqa: BLE001
        return PS.NONE


def power_sources(home: Path, paths: list[str]) -> dict[str, str]:
    """{path (relative to home/fit): stryd / watch / none} of the given FIT
    files (backend/engine/power_source.py), cached per file stamp in its own
    file (the curve cache's version is left alone), with its code version (SP-341)."""
    from backend.engine.wko5expr import fitcache
    cache_p, version = home / POWER_CACHE_NAME, power_cache_code()
    cache = _load_json(cache_p, version)
    root, out, dirty = home / "fit", {}, False
    for key in paths:
        p = root / key
        try:
            st = p.stat()
        except OSError:
            continue
        stamp = fitcache.stamp_s(p, st)
        hit = cache.get(key)
        if not hit or hit[:2] != stamp:
            hit = stamp + [_classify_file(p, home, key)]
            cache[key] = hit
            dirty = True
        out[key] = hit[2]
    if dirty:
        _save_json(cache_p, cache, version)
    return out


BAD_CACHE_NAME = "racepower_bad_activity.json"   # {"v", "files": {path: [size, mtime, local start, group, features]}}


def _local_start(start):
    from backend.engine.wko5expr.datasource import athlete_tz
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt.timezone.utc)
    return start.astimezone(athlete_tz()).replace(tzinfo=None).isoformat()


def _bad_entry(p: Path, home: Optional[Path] = None, key: Optional[str] = None) -> list:
    """[local start 'YYYY-MM-DDTHH:MM:SS', sport group, bad_activity.features]."""
    from backend.engine import bad_activity as BA
    from backend.engine.wko5expr import fitcache
    from backend.engine.wko5expr.datasource import athlete_tz
    from backend.engine.wko5expr.fitdataset import sport_of
    from backend.files.fit_to_channels import fit_to_channels
    e = _fit_entry(home, key) if home is not None and key else None
    slot = (e or {}).get("bad")
    m = (e or {}).get("meta") or {}
    if slot and slot[0] == fitcache.versions()["bad"] and m.get("start") and "" in slot[1]:
        # the dataset parse computed the same features from the same channels
        try:
            start = dt.datetime.fromisoformat(m["start"])
        except ValueError:
            start = None
        if start is not None:
            return [_local_start(start), fitcache.group_of(m.get("sport"), m.get("sub_sport")), slot[1][""]]
    try:
        fc = fit_to_channels(p.read_bytes())
    except Exception:                       # noqa: BLE001
        return [None, None, None]
    start = fc.start_time
    if start is not None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=dt.timezone.utc)
        start = start.astimezone(athlete_tz()).replace(tzinfo=None).isoformat()
    sport_raw, sub = (fc.sport or "", getattr(fc, "sub_sport", None))
    if isinstance(sport_raw, str) and "/" in sport_raw:
        sport_raw, sub = sport_raw.split("/", 1)
    group = sport_of(sport_raw, sub)[0]
    return [start, group, BA.features(fc.elapsedtime, fc.channels.get("elapseddistance"),
                                      fc.channels.get("power"))]


def bad_files(home: Path, paths: list[str], enabled: Optional[bool] = None,
              tags: Optional[list] = None) -> dict[str, str]:
    """{path: reason} of the given FIT files that are bad activity files
    (backend/engine/bad_activity.py: a car / bike segment, impossible power),
    with the user's keep / exclude overrides (activity_tags, matched by the
    file's local start) and the setting activities.exclude_bad. The features
    are cached per file stamp in their own file, with its code version (SP-341)."""
    from backend.engine import activity_tags as AT
    from backend.engine import bad_activity as BA
    from backend.engine.wko5expr import fitcache
    enabled = BA.read_setting(True) if enabled is None else enabled
    rows = AT.load() if tags is None else tags
    if not enabled and not any(AT.user_exclusion(r) for r in rows):
        return {}
    cache_p, version = home / BAD_CACHE_NAME, bad_cache_code()
    cache = _load_json(cache_p, version)
    root, out, dirty = home / "fit", {}, False
    for key in paths:
        p = root / key
        try:
            st = p.stat()
        except OSError:
            continue
        stamp = fitcache.stamp_s(p, st)
        hit = cache.get(key)
        if not hit or hit[:2] != stamp:
            hit = stamp + _bad_entry(p, home, key)
            cache[key] = hit
            dirty = True
        start, group, feats = hit[2], hit[3], hit[4]
        when = dt.datetime.fromisoformat(start) if start else None
        ov = AT.user_exclusion(AT.find(rows, when, None)) if rows and when else None
        ex = BA.decide(BA.judge(feats, group or ""), ov, enabled)
        if ex:
            out[key] = ex["reason"]
    if dirty:
        _save_json(cache_p, cache, version)
    return out


def _usable(home: Path, files: list[dict], accept_watch: bool) -> list[dict]:
    """Drop bad activity files (bad_files) and the files whose power is
    watch-estimated (unless accepted)."""
    from backend.engine import power_source as PS
    if not files:
        return files
    bad = bad_files(home, [f["path"] for f in files])
    files = [f for f in files if f["path"] not in bad]
    if accept_watch or not files:
        return files
    src = power_sources(home, [f["path"] for f in files])
    return [f for f in files if PS.usable(src.get(f["path"]), False)]


def scan(home: Path, since: dt.date, until: dt.date, accept_watch: bool = True) -> list[dict]:
    """Every 3′/12′ test in the FIT folder dated since…until (one per date).
    `accept_watch` False: a test recorded with watch-estimated power is no test."""
    out, seen = [], set()
    files = [f for f in _primary_files(home, since, until) if f.get("test")]
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
    maximum. Only the 資料來源's folder is read, like the charts.
    `accept_watch` False:
    watch-estimated power is left out."""
    files = [f for f in _primary_files(home, since, until) if f.get("curve")]
    return [{"date": f["date"], "file": f["file"], "path": f["path"], "xs": f["curve"][0], "ys": f["curve"][1]}
            for f in _usable(home, files, accept_watch)]
