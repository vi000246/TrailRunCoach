"""
SP-342 第 1 條的量測，可重跑：同步帶回一筆新活動後，要多久才在活動列表與總覽看到，
各步驟花多少 CPU 秒。只讀示範跑者的資料（backend.demo.build 產生），複製到暫存資料夾再跑，
從不碰 ~/.wko5coach、~/WKO5 或網路。

    python -m backend.demo.build --root /tmp/trc-demo --weeks 156     # 約 780 筆，接近真實會員
    BASE=/tmp/trc-demo/base/$(cat /tmp/trc-demo/base/current)
    python -m backend.scripts.perf_sp342 --base $BASE --work /tmp/trc-sp342 [--reps 3] [--charts 12] [--out r.json]

子行程裡（app 的設定與正式環境一樣，FIT 解析 2 個 process）先把頁面要的東西都暖好
（活動列表、總覽狀態、課表、自動分類、--charts 張近 90 天的圖），之後每一輪：

  1. 最新一筆 FIT 移出來源資料夾，照常重建與暖好（不計）——同步之前的狀態；
  2. 把它放回來（同步帶回一筆新活動；向 COROS 的網路等待不算），依序量：
       dataset   新的 Dataset（WV._dataset：source_stamp 變了 → 建新物件，解析那 1 個 FIT）
       list      GET /app/activities（活動列表看到新的那筆）
       status    總覽狀態重算（OV._status；GET /status 這時先回舊值標「更新中」，新的就是這段）
       inputs    課表輸入（PSA._compute_inputs；GET /calendar 同樣先回舊值）
       charts    --charts 張圖，新物件第一次畫（ds.memo 冷、渲染快取因資料指紋變而失效）
       charts_memo_warm  渲染快取清掉、同一個物件再畫一次（ds.memo 熱）：
                 charts − charts_memo_warm ≈ 圖表裡「ds.memo 重來」的部分，也就是就地更新最多省的
       status_same / inputs_same  同一個物件、只清掉狀態／課表的快取再算一次：就地更新（物件
                 留著、它自己的快取都在）最多能省 status − status_same、inputs − inputs_same
  看到新活動的時間：活動列表 = dataset + list；總覽 = dataset + status。

每步記牆鐘、本行程 CPU（user+sys）與子行程 CPU（FIT 解析的 process pool），NAS 推估 = CPU × 8.5
（docs/research/cache-tiering.md §1.2）。行程 CPU 含所有執行緒：每一步都在佇列清空後才量，背景工作不混進來。
本機數字會被其他程式干擾：輸出記下 load average。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import resource
import shutil
import subprocess
import sys
import time
from pathlib import Path

NAS_RATIO = 8.5
REPO = Path(__file__).resolve().parents[2]


def _cpu() -> tuple[float, float]:
    s = resource.getrusage(resource.RUSAGE_SELF)
    c = resource.getrusage(resource.RUSAGE_CHILDREN)
    return s.ru_utime + s.ru_stime, c.ru_utime + c.ru_stime


def _wait_idle() -> None:
    from backend import jobs
    from backend.api import wko5views as WV
    quiet = 0
    while quiet < 10:
        time.sleep(0.1)
        snap = jobs.snapshot()
        busy = snap["queued"] or snap["running"] or WV._alive(WV._warm_slot())
        quiet = 0 if busy else quiet + 1


def _child(a) -> int:
    import logging
    logging.basicConfig(level=logging.WARNING)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.api import overview as OV
    from backend.api import plan_sessions as PSA
    from backend.api import wko5views as WV
    from backend.engine.wko5expr import render_cache as RC
    from backend.sync import storage

    app = build_app(demo=False)
    today = dt.date.today()
    cal = (f"/api/v1/overview/plan/calendar?start={today - dt.timedelta(days=35)}"
           f"&end={today + dt.timedelta(days=28)}")
    src = max(storage.source_dir("coros").rglob("*.fit"), key=lambda p: p.name)
    held = Path(a.work) / "held-newest.fit"
    out = {"loadavg_start": os.getloadavg(), "newest": src.name, "runs": []}

    with TestClient(app) as c:
        def get(path, **params):
            r = c.get(path, params=params or None)
            if r.status_code >= 400:
                raise RuntimeError(f"{path}: {r.status_code} {r.text[:200]}")
            return r.json()

        charts = []
        for v in get("/api/v1/app/views"):
            for dsh in v.get("dashboards", [])[:2]:
                for ch in dsh.get("charts", []):
                    if len(charts) < a.charts and ch.get("kind") == "athlete":
                        charts.append(f"/api/v1/app/views/{v['name']}/dashboards/{dsh['index']}/charts/{ch['index']}")
        rng = {"begin": str(today - dt.timedelta(days=89)), "end": str(today)}

        def warm_all():
            get("/api/v1/app/activities")
            get("/api/v1/overview/status")
            c.get(cal)
            c.get("/api/v1/app/activities/auto")
            for u in charts:
                get(u, **rng)
            _wait_idle()

        def step(name, fn, row):
            w0, (s0, c0) = time.perf_counter(), _cpu()
            res = fn()
            w1, (s1, c1) = time.perf_counter(), _cpu()
            row[name] = {"wall": round(w1 - w0, 3), "cpu": round(s1 - s0, 3), "cpu_children": round(c1 - c0, 3)}
            return res

        for rep in range(a.reps):
            shutil.move(str(src), str(held))          # before the sync: without the newest
            warm_all()
            n_before = len(get("/api/v1/app/activities")["activities"])
            shutil.move(str(held), str(src))          # the sync brought it back
            row = {"loadavg": os.getloadavg()}
            step("dataset", lambda: WV._dataset(), row)
            RC.CACHE.clear()                          # new data: never drawn before (an earlier
            #                                           run drew this same state)
            lst = step("list", lambda: get("/api/v1/app/activities"), row)
            row["list_new_seen"] = len(lst["activities"]) == n_before + 1
            # GET /status and /calendar answer the previous value marked stale while the new one is
            # computed (SP-394): the time to the new one is the computation itself
            step("status", lambda: OV._status(WV._dataset(), OV.O.day_to_date(WV._dataset().today)), row)
            step("inputs", lambda: PSA._compute_inputs(), row)
            step("charts", lambda: [get(u, **rng) for u in charts], row)
            RC.CACHE.clear()
            step("charts_memo_warm", lambda: [get(u, **rng) for u in charts], row)
            # the same results again on the SAME Dataset object (its own caches warm: ds.memo, the
            # loaded channels, the series): the most an in-place update could save is X − X_same
            with OV._lock:
                OV._status_cache.clear()
            step("status_same", lambda: OV._status(WV._dataset(), OV.O.day_to_date(WV._dataset().today)), row)
            PSA._cache.clear()
            step("inputs_same", lambda: PSA._compute_inputs(), row)
            _wait_idle()
            out["runs"].append(row)
            print(json.dumps(row), file=sys.stderr, flush=True)
    out["loadavg_end"] = os.getloadavg()
    out["charts"] = len(charts)
    print("@@" + json.dumps(out), flush=True)
    return 0


def _saved(runs: list, k: str) -> float:
    xs = sorted(r[k]["cpu"] - r[k + "_same"]["cpu"] for r in runs)
    return xs[len(xs) // 2]


def _summary(res: dict) -> None:
    runs = res["runs"]
    print(f"newest FIT: {res['newest']}; charts: {res['charts']}; load {res['loadavg_start']} -> {res['loadavg_end']}")
    print(f"{'step':18} {'wall s':>14} {'CPU s':>14} {'NAS s (CPU×8.5)':>18}")
    for k in ("dataset", "list", "status", "status_same", "inputs", "inputs_same", "charts", "charts_memo_warm"):
        wall = [r[k]["wall"] for r in runs]
        cpu = [r[k]["cpu"] + r[k]["cpu_children"] for r in runs]
        print(f"{k:18} {min(wall):6.2f}–{max(wall):<6.2f} {min(cpu):6.2f}–{max(cpu):<6.2f} "
              f"{min(cpu) * NAS_RATIO:7.1f}–{max(cpu) * NAS_RATIO:<7.1f}")
    for label, ks in (("活動列表看到", ("dataset", "list")), ("總覽看到", ("dataset", "status"))):
        wall = [sum(r[k]["wall"] for k in ks) for r in runs]
        cpu = [sum(r[k]["cpu"] + r[k]["cpu_children"] for k in ks) for r in runs]
        print(f"{label}: 牆鐘 {min(wall):.2f}–{max(wall):.2f} s，CPU {min(cpu):.2f}–{max(cpu):.2f} s，"
              f"NAS 約 {min(cpu) * NAS_RATIO:.0f}–{max(cpu) * NAS_RATIO:.0f} s")
    memo = [(r["charts"]["cpu"] - r["charts_memo_warm"]["cpu"]) for r in runs]
    print(f"圖表的 ds.memo 部分：CPU {min(memo):.2f}–{max(memo):.2f} s，NAS 約 "
          f"{min(memo) * NAS_RATIO:.0f}–{max(memo) * NAS_RATIO:.0f} s；"
          f"同一個物件最多省：狀態 {_saved(runs, 'status'):.2f} s、課表輸入 {_saved(runs, 'inputs'):.2f} s CPU（中位）；"
          f"新活動出現在列表：{all(r['list_new_seen'] for r in runs)}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SP-342 第 1 條：同步一筆後多久看到")
    ap.add_argument("--base")
    ap.add_argument("--work", required=True)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--charts", type=int, default=12)
    ap.add_argument("--fit-workers", type=int, default=2)
    ap.add_argument("--out")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.child:
        return _child(a)
    from backend.scripts.perf_sp394 import _copy_base, _env
    work = Path(a.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    home = _copy_base(Path(a.base).resolve(), work / "home")
    env = _env(home, {"WKO5COACH_NO_WARMUP": "1", "WKO5COACH_FIT_WORKERS": str(a.fit_workers)})
    cmd = [sys.executable, "-m", "backend.scripts.perf_sp342", "--child", "--work", str(work),
           "--reps", str(a.reps), "--charts", str(a.charts)]
    p = subprocess.run(cmd, env=env, cwd=str(REPO), stdout=subprocess.PIPE, text=True)
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
    if p.returncode or line is None:
        print(p.stdout[-2000:])
        return p.returncode or 1
    res = json.loads(line[2:])
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1), "utf-8")
    _summary(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
