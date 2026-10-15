"""
SP-394 第 5、6 項的量測，可重跑。只讀示範跑者的資料（backend.demo.build 產生），每次都複製到
一個暫存資料夾再跑，從不碰 ~/.wko5coach、~/WKO5 或網路。

    # 示範資料（已經有就跳過；--weeks 156 是約 3 年、比較接近真實會員的活動數）
    python -m backend.demo.build --root /tmp/trc-demo
    BASE=/tmp/trc-demo/base/$(cat /tmp/trc-demo/base/current)

    # 第 5 項：背景跑整份工作時，便宜請求的延遲（伺服器在子行程，量測端在這個行程）
    python -m backend.scripts.perf_sp394 latency --base $BASE --work /tmp/trc-perf \
        [--job classify --job backtest --job rebuild] [--samples 40] [--switch-ms 5] [--out lat.json]

    # 第 6 項：10 個租戶同時重建的峰值記憶體（整個 process 樹的 RSS，每 0.1 秒取樣）
    python -m backend.scripts.perf_sp394 memory --base $BASE --work /tmp/trc-perf \
        [--tenants 10] [--cold fit|derived] [--fit-workers 2] [--holders] [--out mem.json]

第 5 項 (latency): 伺服器是 uvicorn + backend.main 的 app（擁有者模式，資料是示範跑者的複本），
跟正式環境一樣一個 process。先把快取暖好，然後輪流打便宜請求（/healthz、活動列表
GET /api/v1/app/activities、課表頁 GET /api/v1/overview/plan/calendar、總覽狀態
GET /api/v1/overview/status），沒有背景工作時量一輪，再叫伺服器在全域佇列裡跑一份整份工作、
同時量一輪；工作比取樣先跑完就再跑一次。背景工作：
  classify  自動分類全量（模擬部署改了判讀程式：兩個分支的 code hash 都換掉，每筆活動重算，
            照熱／溫／冷分級，`full`）
  backtest  賽事回測（`backtest` 類別，跟按鈕一樣）
  rebuild   另一個租戶從冷重建整份 Dataset（FIT 解析與估算快取都刪掉：第一次匯入、解析版本
            變更後；`full`，FIT 解析 2 個 process 跟 NAS 一樣）；正在服務的 Dataset 不動，
            所以請求不會在等它，量到的只是搶 CPU／GIL
  bgsync    SP-499 背景同步帶回一筆新活動後伺服器做的事：最新的 FIT 移出／放回來源資料夾，
            warm_up("sync-bg-coros")（`warm`：Dataset、總覽狀態、課表輸入，之後分類、最近畫過的
            圖重畫），等佇列清空；同步本身向 COROS 的請求是等網路、不吃 CPU，不算。暖機時先照
            圖表頁的樣子畫 12 張近 90 天的圖（?stale=1），讓預算有東西可畫
  bgsync_other  同上，但是另一個租戶（--other 的複本）的背景同步，請求是擁有者的：一個人的
            背景同步對其他人的影響
報 p50／p95、比值，與 NAS 推估（× NAS_RATIO：本機 CPU 秒 × 8.5，docs/research/cache-tiering.md）。

第 6 項 (memory): 一個子行程裡做出 N 個租戶（tenancy USER，root = 示範資料的複本），同時：
每個租戶一個部署後暖機（wko5views.warm_up("deploy")：Dataset → 總覽狀態 → 課表輸入，之後
校正、自動分類全量）＋ 一個「打開頁面」的請求（_dataset → 總覽狀態 → 活動列表 → 自動分類的
狀態）。--cold fit：FIT 解析快取也刪掉（新使用者第一次匯入：先算近 90 天、背景補較舊的）；
--cold derived：只刪衍生快取（部署改版：估算、series、自動分類、總覽狀態）。量測端每 0.1 秒
用 ps 取子行程與它的子孫（FIT 解析的 process pool）的 RSS 總和，報峰值；子行程最後也報
getrusage 的 ru_maxrss。--fit-workers 預設 2（NAS 4 核：fitcache._pool_size = min(8, cpu − 2)）。
約每分鐘印一行進度：行程樹 RSS、Dataset 快取留著幾個、實際還活著幾個、佇列；--holders 另外列出
快取已丟掉卻還活著的 Dataset 被誰拿著（gc 與各執行緒的區域變數，會拖慢）。

本機數字會被同時在跑的其他程式干擾：多跑幾次、記下 load average（輸出裡有）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

NAS_RATIO = 8.5          # NAS ÷ 本機 CPU 秒（2026-10-07 實測，docs/research/cache-tiering.md）
REPO = Path(__file__).resolve().parents[2]

# what a version change drops (第 6 項 --cold derived; latency's rebuild drops only the estimates)
ESTIMATE_FILES = ("estimate.json", "estimate_grid.json", "pd_mftp.json")
DERIVED_IN_FIT_CACHE = ESTIMATE_FILES + ("series.db", "series.db-wal", "series.db-shm", "activity_auto.json")
DERIVED_AT_ROOT = ("workout_curves.json", "channel_peaks.json", "achievements_cache.json",
                   "cache/overview_status.json", "cache/render")


# ---------------------------------------------------------------------------------- helpers

def _py() -> str:
    return sys.executable


def _env(home: Path, extra: dict | None = None) -> dict:
    """The child's environment: the app on `home` only (backend.demo.build.child_env: no WKO5
    folder, no scheduler, no backup, no TP client), plus `extra`."""
    from backend.demo.build import child_env
    env = child_env(home)
    env.pop("WKO5COACH_NO_WARMUP", None)
    env["PYTHONPATH"] = str(REPO)
    env.update(extra or {})
    return env


def _copy_base(base: Path, dest: Path) -> Path:
    from backend.demo.build import _refuse_owner, relocate_paths
    _refuse_owner(dest)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(base, dest, ignore=shutil.ignore_patterns("logs"))
    relocate_paths(dest)
    return dest


def _fit_cache_dirs(root: Path) -> list[Path]:
    d = root / "cache" / "fit"
    return [p for p in d.iterdir() if p.is_dir()] if d.is_dir() else []


def _rm(p: Path) -> None:
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    else:
        try:
            p.unlink()
        except OSError:
            pass


def drop_caches(root: Path, cold: str) -> None:
    """`derived`: what a version change recomputes; `fit`: everything, the FIT parses too (a first
    import); `estimates`: the threshold estimates only."""
    if cold == "fit":
        _rm(root / "cache")
        for f in DERIVED_AT_ROOT:
            _rm(root / f)
        return
    names = ESTIMATE_FILES if cold == "estimates" else DERIVED_IN_FIT_CACHE
    for d in _fit_cache_dirs(root):
        for f in names:
            _rm(d / f)
    if cold == "derived":
        for f in DERIVED_AT_ROOT:
            _rm(root / f)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _env_note() -> dict:
    try:
        la = os.getloadavg()
    except OSError:
        la = None
    return {"when": dt.datetime.now().isoformat(timespec="seconds"), "python": sys.version.split()[0],
            "cpus": os.cpu_count(), "loadavg": la, "platform": sys.platform}


def _emit(**kw) -> None:
    print("@@" + json.dumps(kw), flush=True)


# ---------------------------------------------------------------------------------- 第 5 項: server

def _serve(a) -> int:
    """The child: the app on 127.0.0.1:port; one background job at a time on command (stdin)."""
    import logging
    import uvicorn
    if a.switch_ms:
        sys.setswitchinterval(a.switch_ms / 1000.0)
    logging.basicConfig(level=logging.WARNING)
    from backend.main import build_app
    app = build_app(demo=False)
    n = {"run": 0}

    def classify() -> None:
        from backend.api import activity_auto as AA
        from backend.api import wko5views as WV
        ds = WV._dataset()
        n["run"] += 1
        # a deploy that changed both branches' code: every activity is recomputed, in age tiers
        AA._CODE["branches"] = {"run": f"perf-run-{n['run']}", "other": f"perf-other-{n['run']}"}
        job = AA.job_for(ds, priority="deploy_hot")
        job.join(3600)

    def backtest() -> None:
        from backend.api import wko5views as WV
        from backend.api import racepower as RP
        from backend.engine.racepower import backtest as BT
        BT.start_background(WV._dataset, run=RP._backtest_runner())     # as POST /backtest/run
        time.sleep(0.2)
        while True:
            st = BT.state()
            if not st.get("running") and not st.get("queued"):
                return
            time.sleep(0.1)

    def rebuild() -> None:
        # another tenant's whole Dataset from cold (its FIT parses and estimates dropped: a first
        # import, or a re-parse after a parse-version change) — built beside the one being served,
        # which stays in the cache: the requests never wait for it, they only share the CPU / GIL
        from backend import jobs
        from backend import tenancy
        from backend.api import wko5views as WV
        other = Path(a.other)
        drop_caches(other, "fit")
        t = tenancy.Tenant(id="u-perf", kind=tenancy.USER, root=other, shared=other, caps=tenancy.USER_CAPS)
        with tenancy.use(t):
            key = WV._dataset_key()
            jobs.call(lambda: WV._build_dataset(*key, hot_first=False), cls="full", priority="warm",
                      label="perf rebuild (another tenant)")

    held: dict = {}

    def bgsync_other() -> None:
        # SP-499: the same, for another tenant (a user whose watch had a new activity) while the
        # requests are the owner's: what one person's background sync costs everyone else
        from backend import tenancy
        other = Path(a.other)
        t = tenancy.Tenant(id="u-perf", kind=tenancy.USER, root=other, shared=other, caps=tenancy.USER_CAPS)
        with tenancy.use(t):
            bgsync(key="other")

    def bgsync(key: str = "owner") -> None:
        # SP-499: what the server does after a background sync that brought one new activity
        # (runner.after_import → wko5views.warm_up("sync-bg-coros") at `warm`: the Dataset with the
        # new FIT, the overview status, the 課表's inputs; then the classification and the charts
        # drawn lately, api/prerender.py), waited for until the queue is empty. The sync's own COROS
        # requests are network waits (no CPU) and are left out. The newest FIT leaves the source
        # folder and comes back on alternate runs: one activity changed each time.
        from backend import jobs
        from backend.api import wko5views as WV
        from backend.sync import storage
        os.environ.pop("WKO5COACH_NO_WARMUP", None)
        if key not in held:
            src = max(storage.source_dir("coros").rglob("*.fit"), key=lambda p: p.name)
            held[key] = (src, Path(a.other).parent / f"held-newest-{key}.fit")
        src, out = held[key]
        if src.exists():
            shutil.move(str(src), str(out))
        else:
            shutil.move(str(out), str(src))
        WV.warm_up("sync-bg-coros")
        quiet = 0
        while quiet < 15:                         # 1.5 s with nothing queued, running or warming
            time.sleep(0.1)
            snap = jobs.snapshot()
            busy = snap["queued"] or snap["running"] or WV._alive(WV._warm_slot())
            quiet = 0 if busy else quiet + 1

    work = {"classify": classify, "backtest": backtest, "rebuild": rebuild, "bgsync": bgsync,
            "bgsync_other": bgsync_other}

    def control() -> None:
        for line in sys.stdin:
            cmd = line.strip()
            if cmd == "quit":
                os._exit(0)
            fn = work.get(cmd)
            if fn is None:
                continue
            t0, c0 = time.time(), time.process_time()
            _emit(ev="start", job=cmd, t=t0)
            err = None
            try:
                fn()
            except Exception as e:           # noqa: BLE001
                err = f"{type(e).__name__}: {e}"[:300]
            _emit(ev="done", job=cmd, t0=t0, t=time.time(), cpu=time.process_time() - c0, error=err)
    threading.Thread(target=control, daemon=True).start()
    cfg = uvicorn.Config(app, host="127.0.0.1", port=a.port, log_level="warning", lifespan="on")
    uvicorn.Server(cfg).run()
    return 0


class _Child:
    def __init__(self, cmd: list[str], env: dict, log: Path):
        self.log = open(log, "w", encoding="utf-8")      # the child's log lines (the app's INFO)
        self.p = subprocess.Popen(cmd, env=env, cwd=str(REPO), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self.log, text=True, bufsize=1)
        self.events: list[dict] = []
        self._lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.p.stdout:
            if line.startswith("@@"):
                with self._lock:
                    self.events.append(json.loads(line[2:]))

    def send(self, cmd: str) -> None:
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()

    def evs(self) -> list[dict]:
        with self._lock:
            return list(self.events)

    def stop(self) -> None:
        try:
            self.send("quit")
            self.p.wait(10)
        except Exception:                    # noqa: BLE001
            self.p.kill()


def _endpoints() -> dict:
    today = dt.date.today()
    return {
        "healthz": "/healthz",
        "activities": "/api/v1/app/activities",
        "schedule": f"/api/v1/overview/plan/calendar?start={today - dt.timedelta(days=35)}"
                    f"&end={today + dt.timedelta(days=28)}",
        "status": "/api/v1/overview/status",
    }


def latency(a) -> int:
    import httpx
    base = Path(a.base).resolve()
    work = Path(a.work).resolve()
    home = _copy_base(base, work / "latency-home")
    other = _copy_base(base, work / "latency-other")
    port = _free_port()
    env = _env(home, {"WKO5COACH_NO_WARMUP": "1", "WKO5COACH_FIT_WORKERS": str(a.fit_workers),
                      "WKO5COACH_PROCESS_JOBS": "0" if a.no_workers else "1"})
    cmd = [_py(), "-m", "backend.scripts.perf_sp394", "_serve", "--port", str(port), "--other", str(other)] + \
          (["--switch-ms", str(a.switch_ms)] if a.switch_ms else [])
    child = _Child(cmd, env, work / "latency-server.log")
    print(f"server log: {work / 'latency-server.log'}", flush=True)
    url = f"http://127.0.0.1:{port}"
    eps = _endpoints()
    out = {"env": _env_note(), "base": str(base), "switch_ms": a.switch_ms, "workers": not a.no_workers,
           "nas_ratio": NAS_RATIO, "jobs": {}}
    print(f"worker processes: {'off' if a.no_workers else 'on'}", flush=True)
    try:
        with httpx.Client(base_url=url, timeout=600) as c:
            for _ in range(600):
                try:
                    if c.get("/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.2)
            t0 = time.time()
            for _ in range(3):                    # warm: Dataset, status, the 課表 inputs, auto values
                for name, path in eps.items():
                    r = c.get(path)
                    if r.status_code >= 400:
                        print(f"  ! warm {name}: {r.status_code} {r.text[:200]}", flush=True)
                c.get("/api/v1/app/activities/auto")
            if "bgsync" in (a.job or []):
                # SP-499: charts drawn as the viewer asks (90 days to today, ?stale=1), so the
                # background pre-render has slots to redraw
                drawn = 0
                today = dt.date.today()
                chart_urls = []
                for v in c.get("/api/v1/app/views").json():
                    for dsh in v.get("dashboards", [])[:2]:
                        for ch in dsh.get("charts", []):
                            if drawn >= 12 or ch.get("kind") not in ("athlete",):
                                continue
                            r = c.get(f"/api/v1/app/views/{v['name']}/dashboards/{dsh['index']}/charts/{ch['index']}",
                                      params={"begin": str(today - dt.timedelta(days=89)), "end": str(today),
                                              "stale": "1"})
                            drawn += r.status_code == 200
                            if r.status_code == 200:
                                chart_urls.append(r.url)
                    if drawn >= 12:
                        break
                print(f"charts drawn for the pre-render: {drawn}", flush=True)
            print(f"warm: {time.time() - t0:.1f} s", flush=True)

            def sample(rounds: int, until=None) -> list[dict]:
                rows = []
                i = 0
                while (until is None and i < rounds) or (until is not None and not until()):
                    for name, path in eps.items():
                        s = time.time()
                        r = c.get(path)
                        e = time.time()
                        rows.append({"ep": name, "s": s, "e": e, "ms": (e - s) * 1000, "code": r.status_code})
                        time.sleep(a.gap)
                    i += 1
                return rows

            for job in a.job:
                idle = sample(a.samples)
                busy, intervals, runs = [], [], 0
                while runs < a.max_repeats:
                    before = len(child.evs())
                    child.send(job)
                    runs += 1

                    def finished(before=before) -> bool:
                        return any(ev["ev"] == "done" for ev in child.evs()[before:])
                    busy += sample(0, until=finished)
                    done = next(ev for ev in child.evs()[before:] if ev["ev"] == "done")
                    intervals.append(done)
                    if done.get("error"):
                        print(f"  ! {job}: {done['error']}", flush=True)
                    n_in = sum(1 for r in busy if any(d["t0"] <= r["s"] and r["e"] <= d["t"] for d in intervals))
                    if n_in >= a.samples * len(eps):
                        break
                inside = [r for r in busy if any(d["t0"] <= r["s"] and r["e"] <= d["t"] for d in intervals)]
                idle_after = sample(max(5, a.samples // 4))
                res = {"runs": [{"secs": round(d["t"] - d["t0"], 2), "cpu": round(d["cpu"], 2),
                                 "error": d.get("error")} for d in intervals], "endpoints": {}}
                for name in eps:
                    xi = [r["ms"] for r in idle + idle_after if r["ep"] == name]
                    xb = [r["ms"] for r in inside if r["ep"] == name]
                    res["endpoints"][name] = {
                        "n_idle": len(xi), "n_busy": len(xb),
                        "idle_p50": _pct(xi, .5), "idle_p95": _pct(xi, .95),
                        "busy_p50": _pct(xb, .5), "busy_p95": _pct(xb, .95)}
                if job == "bgsync":
                    # the charts the page asks for after the last run: drawn ahead (not `stale`)?
                    got = [c.get(u).json() for u in chart_urls]
                    res["charts_after"] = {"asked": len(got), "stale": sum(1 for g in got if g.get("stale"))}
                    print(f"charts asked after the last run: {len(got)}, stale {res['charts_after']['stale']}",
                          flush=True)
                out["jobs"][job] = res
                _print_latency(job, res)
    finally:
        child.stop()
    out["env_end"] = _env_note()
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1), "utf-8")
    return 0


def _print_latency(job: str, res: dict) -> None:
    runs = ", ".join(f"{r['secs']} s" for r in res["runs"])
    print(f"\n== {job}: {len(res['runs'])} run(s) ({runs})", flush=True)
    print(f"{'endpoint':<11} {'n':>7} {'idle p50':>9} {'p95':>7} {'busy p50':>9} {'p95':>7} {'×p50':>6} {'×p95':>6}"
          f" | NAS idle p50/p95   busy p50/p95 (ms)")
    for name, e in res["endpoints"].items():
        r50 = e["busy_p50"] / e["idle_p50"] if e["idle_p50"] else float("nan")
        r95 = e["busy_p95"] / e["idle_p95"] if e["idle_p95"] else float("nan")
        print(f"{name:<11} {e['n_idle']:>3}/{e['n_busy']:<3} {e['idle_p50']:>9.1f} {e['idle_p95']:>7.1f} "
              f"{e['busy_p50']:>9.1f} {e['busy_p95']:>7.1f} {r50:>6.2f} {r95:>6.2f} | "
              f"{e['idle_p50'] * NAS_RATIO:>7.0f}/{e['idle_p95'] * NAS_RATIO:<7.0f} "
              f"{e['busy_p50'] * NAS_RATIO:>7.0f}/{e['busy_p95'] * NAS_RATIO:<7.0f}", flush=True)


# ---------------------------------------------------------------------------------- 第 6 項

def _holders(datasets: list) -> dict:
    """{what holds it: count} over Datasets alive but not in the Dataset cache (--holders): each
    one's referrers, a dict / cell / tuple named by what holds that in turn. A debugging aid."""
    import gc
    import types
    from collections import Counter
    me = sys._getframe()
    count: Counter = Counter()

    def name(r) -> str:
        if isinstance(r, types.FunctionType):
            return f"fn {r.__module__}.{r.__qualname__}"
        if isinstance(r, types.MethodType):
            return f"method {type(r.__self__).__name__}.{r.__func__.__name__}"
        return type(r).__name__

    for d in datasets:
        seen = set()
        for r in gc.get_referrers(d):
            if r is datasets or isinstance(r, types.FrameType) or r is me:
                continue
            what = name(r)
            if isinstance(r, (dict, types.CellType, tuple, list)):
                up = [name(u) for u in gc.get_referrers(r)
                      if u is not datasets and not isinstance(u, types.FrameType)][:3]
                what += " <- " + ", ".join(sorted(set(up)) or ["?"])
            if what not in seen:
                seen.add(what)
                count[what] += 1
    # a running (or waiting) thread's local variables: not found by gc.get_referrers
    want = {id(d) for d in datasets}
    for f in sys._current_frames().values():
        while f is not None:
            if f is not me:
                for k, v in list(f.f_locals.items()):
                    if id(v) in want:
                        count[f"local {k} in {f.f_code.co_name} ({Path(f.f_code.co_filename).name}:{f.f_lineno})"] += 1
            f = f.f_back
    return dict(count.most_common(12))


def _memchild(a) -> int:
    """The child: N user tenants on <server root>/users/<i>; every one's deploy warm-up and a page
    open at once; waits until the queue and every warm-up / back-fill is done."""
    import logging
    import resource
    logging.basicConfig(level=logging.WARNING)
    from backend import jobs, tenancy
    from backend.api import wko5views as WV
    from backend.api import overview as OV
    from backend.api import activity_auto as AA
    root = Path(a.root)
    tenants = [tenancy.Tenant(id=f"u{i}", kind=tenancy.USER, root=root / "users" / str(i),
                              shared=root / "users" / str(i), caps=tenancy.USER_CAPS)
               for i in range(1, a.tenants + 1)]
    # a deploy that changed the classification's code (every tenant recomputes it, in tiers)
    AA._CODE["branches"] = {"run": "perf-run", "other": "perf-other"}
    errors: list[str] = []
    t0 = time.time()
    _emit(ev="start", t=t0)

    def stats() -> None:                     # what holds the memory, every 30 s (the parent prints it)
        while True:
            time.sleep(30)
            try:
                with WV._dataset_cfg._lock:  # not peek(): that would reorder the LRU
                    kept = list(WV._dataset_cfg._d.values())
                snap = jobs.snapshot()
                out = dict(kept=len(kept), live=len(WV._LIVE),
                           est_mb=round(sum(WV.dataset_mb(d) for d in kept)),
                           jobs={"queued": len(snap["queued"]), "running": len(snap["running"])})
                if a.holders:
                    ids = {id(d) for d in kept}
                    out["holders"] = _holders([d for d in list(WV._LIVE) if id(d) not in ids])
                _emit(ev="stats", **out)
            except Exception:                # noqa: BLE001 — a measurement aid only
                pass
    threading.Thread(target=stats, daemon=True).start()

    def page(t) -> None:
        with tenancy.use(t):
            try:
                ds = WV._dataset()
                OV.status()
                WV.activities_list()
                AA.status(ds)
            except Exception as e:           # noqa: BLE001
                errors.append(f"{t.id} page: {type(e).__name__}: {e}"[:300])

    def warm(t) -> None:
        with tenancy.use(t):
            try:
                WV.warm_up("deploy")
            except Exception as e:           # noqa: BLE001
                errors.append(f"{t.id} warm: {type(e).__name__}: {e}"[:300])
    threads = []
    for t in tenants:
        for fn in (warm, page):
            th = threading.Thread(target=fn, args=(t,), daemon=True)
            th.start()
            threads.append(th)
    for th in threads:
        th.join()

    def busy() -> bool:
        snap = jobs.snapshot()
        if snap["queued"] or snap["running"]:
            return True
        with WV._WARM_LOCK:
            return any(WV._alive(w) for w in WV._WARM.values())
    quiet = 0
    while quiet < 30 and time.time() - t0 < a.timeout:
        quiet = 0 if busy() else quiet + 1
        time.sleep(0.1)
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    ru_c = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    div = 1048576 if sys.platform == "darwin" else 1024      # macOS bytes, Linux KB
    kept = WV._dataset_cfg.entries()
    _emit(ev="done", t=time.time(), secs=time.time() - t0, maxrss_mb=ru / div, children_maxrss_mb=ru_c / div,
          datasets_kept=len(kept), est_mb=round(sum(WV.dataset_mb(WV._dataset_cfg.peek(k)) for k in kept
                                                    if WV._dataset_cfg.peek(k) is not None), 1),
          errors=errors[:20], timed_out=time.time() - t0 >= a.timeout)
    return 0


def _tree_rss_mb(pid: int) -> tuple[float, float, int]:
    """(RSS of pid and every descendant, RSS of pid alone, process count) in MB, from ps."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True).stdout
    rows = {}
    kids: dict = {}
    for line in out.splitlines():
        p = line.split()
        if len(p) != 3:
            continue
        a, b, r = int(p[0]), int(p[1]), int(p[2])
        rows[a] = r
        kids.setdefault(b, []).append(a)
    seen, stack = [], [pid]
    while stack:
        x = stack.pop()
        if x in rows:
            seen.append(x)
            stack.extend(kids.get(x, []))
    return sum(rows[x] for x in seen) / 1024, rows.get(pid, 0) / 1024, len(seen)


def memory(a) -> int:
    base = Path(a.base).resolve()
    work = Path(a.work).resolve() / "memory-root"
    if work.exists():
        shutil.rmtree(work)
    (work / "owner").mkdir(parents=True)
    t0 = time.time()
    for i in range(1, a.tenants + 1):
        drop_caches(_copy_base(base, work / "users" / str(i)), a.cold)
    print(f"{a.tenants} tenants copied ({a.cold} cold) in {time.time() - t0:.0f} s", flush=True)
    env = _env(work / "owner", {"WKO5COACH_FIT_WORKERS": str(a.fit_workers),
                                "WKO5COACH_PROCESS_JOBS": "0" if a.no_workers else "1"})
    if a.dataset_mem_mb:
        env["WKO5COACH_DATASET_MEM_MB"] = str(a.dataset_mem_mb)
    cmd = [_py(), "-m", "backend.scripts.perf_sp394", "_memchild", "--root", str(work), "--tenants",
           str(a.tenants), "--timeout", str(a.timeout)] + (["--holders"] if a.holders else [])
    child = _Child(cmd, env, work.parent / "memory-child.log")
    peak = peak_main = 0.0
    peak_n = 0
    trace = []
    while child.p.poll() is None:
        tot, main, n = _tree_rss_mb(child.p.pid)
        if tot > peak:
            peak = tot
            peak_n = n
        peak_main = max(peak_main, main)
        trace.append((round(time.time() - t0, 1), round(tot), round(main), n))
        if len(trace) % 600 == 0:            # about once a minute: an interrupted run still says how far it got
            st = next((ev for ev in reversed(child.evs()) if ev["ev"] == "stats"), {})
            print(f"  {time.time() - t0:.0f} s: tree {tot:.0f} MB ({n} procs), peak so far {peak:.0f} MB; "
                  f"Datasets kept {st.get('kept')} (est {st.get('est_mb')} MB), alive {st.get('live')}, "
                  f"jobs {st.get('jobs')}", flush=True)
            if st.get("holders"):
                print(f"    held by: {st['holders']}", flush=True)
        time.sleep(0.1)
    done = next((ev for ev in child.evs() if ev["ev"] == "done"), {})
    res = {"env": _env_note(), "base": str(base), "tenants": a.tenants, "cold": a.cold, "fit_workers": a.fit_workers,
           "workers": not a.no_workers,
           "peak_tree_mb": round(peak), "peak_tree_procs": peak_n, "peak_main_mb": round(peak_main),
           "child": done, "trace_every_s": trace[::10]}
    print(json.dumps({k: v for k, v in res.items() if k != "trace_every_s"}, ensure_ascii=False, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False), "utf-8")
    if not a.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 0


# ---------------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SP-394 第 5、6 項的量測")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("latency")
    p.add_argument("--base", required=True, help="a demo base (backend.demo.build: <root>/base/<current>)")
    p.add_argument("--work", required=True, help="a scratch folder (the copy of the base goes here)")
    p.add_argument("--job", action="append", choices=("classify", "backtest", "rebuild", "bgsync", "bgsync_other"))
    p.add_argument("--samples", type=int, default=40, help="rounds of every endpoint, idle and busy")
    p.add_argument("--gap", type=float, default=0.05, help="seconds between two requests")
    p.add_argument("--max-repeats", type=int, default=8, help="runs of the job at most to fill the busy samples")
    p.add_argument("--switch-ms", type=float, default=0, help="sys.setswitchinterval in the server (ms; 0: 5 ms)")
    p.add_argument("--fit-workers", type=int, default=2, help="FIT parse processes (NAS: 2)")
    p.add_argument("--no-workers", action="store_true",
                   help="WKO5COACH_PROCESS_JOBS=0: the back-test / classification in the server (before SP-394 第 5 條)")
    p.add_argument("--out")
    p = sub.add_parser("memory")
    p.add_argument("--base", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--tenants", type=int, default=10)
    p.add_argument("--cold", choices=("fit", "derived"), default="fit")
    p.add_argument("--fit-workers", type=int, default=2)
    p.add_argument("--dataset-mem-mb", type=float, help="WKO5COACH_DATASET_MEM_MB for the child")
    p.add_argument("--no-workers", action="store_true", help="WKO5COACH_PROCESS_JOBS=0")
    p.add_argument("--timeout", type=float, default=3600)
    p.add_argument("--keep", action="store_true", help="keep the tenants' folders")
    p.add_argument("--holders", action="store_true",
                   help="every 30 s, what holds the Datasets alive outside the Dataset cache (slow: gc scans)")
    p.add_argument("--out")
    p = sub.add_parser("_serve")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--other", required=True, help="the other tenant's folder (rebuild)")
    p.add_argument("--switch-ms", type=float, default=0)
    p = sub.add_parser("_memchild")
    p.add_argument("--holders", action="store_true")
    p.add_argument("--root", required=True)
    p.add_argument("--tenants", type=int, default=10)
    p.add_argument("--timeout", type=float, default=3600)
    a = ap.parse_args(argv)
    if a.cmd == "latency":
        a.job = a.job or ["classify", "backtest", "rebuild"]
        return latency(a)
    if a.cmd == "memory":
        return memory(a)
    if a.cmd == "_serve":
        return _serve(a)
    return _memchild(a)


if __name__ == "__main__":
    sys.exit(main())
