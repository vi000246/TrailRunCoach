"""
SP-968 的量測，可重跑：課表統計（GET /api/v1/overview/plan/compliance）一次請求的回應時間與讀設定的
次數，4 週與長區間（371 天，API 的上限）。只讀示範跑者的資料（backend.demo.build 產生），複製到暫存
資料夾再跑，從不碰 ~/.wko5coach、~/WKO5 或網路。

    python -m backend.demo.build --root /tmp/trc-demo                 # 52 週示範跑者
    BASE=/tmp/trc-demo/base/$(cat /tmp/trc-demo/base/current)
    python -m backend.scripts.perf_sp968 --base $BASE --work /tmp/trc-sp968 [--reps 15] [--out r.json]

子行程裡（app 設定與正式環境一樣）先開一次課表與統計頁把快取暖好（課表輸入、區間的活動與周期），
之後兩個區間輪流各打 --reps 次，記牆鐘（中位數與最小值）、這次請求讀設定的次數
（datasource._setting_row：每次一條新的 sqlite 連線），以及回應的 sha1——改前改後跑同一支，
sha1 一樣就是統計結果完全一樣。量的是快取已熱的「切換日期區間」，不是第一次打開。
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API = "/api/v1/overview/plan/compliance"


def _child(a) -> int:
    import logging
    logging.basicConfig(level=logging.WARNING)
    from fastapi.testclient import TestClient
    from backend.main import build_app
    from backend.engine.wko5expr import datasource as DS

    reads = []
    real = DS._setting_row

    def counted(db, key, user_id):
        reads.append(key)
        return real(db, key, user_id)
    DS._setting_row = counted

    app = build_app(demo=False)
    out = {"loadavg_start": os.getloadavg(), "ranges": {}}
    with TestClient(app) as c:
        def get(path):
            r = c.get(path)
            if r.status_code >= 400:
                raise RuntimeError(f"{path}: {r.status_code} {r.text[:200]}")
            return r

        today = dt.date.today()
        get(f"/api/v1/overview/plan/calendar?start={today - dt.timedelta(days=35)}"     # the inputs and
            f"&end={today + dt.timedelta(days=28)}")                                      # sessions, warm
        ranges = {"4w": (today - dt.timedelta(days=27), today),
                  "371d": (today - dt.timedelta(days=370), today)}
        urls = {k: f"{API}?start={x}&end={y}" for k, (x, y) in ranges.items()}
        for u in urls.values():                                  # warm: the range extras, the history
            get(u)
            get(u)
        times = {k: [] for k in urls}
        nread = {k: [] for k in urls}
        keys = {}
        digest = {k: set() for k in urls}
        for _ in range(a.reps):
            for k, u in urls.items():
                reads.clear()
                t0 = time.perf_counter()
                r = get(u)
                times[k].append(time.perf_counter() - t0)
                nread[k].append(len(reads))
                keys[k] = collections.Counter(reads).most_common(5)
                digest[k].add(hashlib.sha1(r.content).hexdigest())
        for k, (x, y) in ranges.items():
            out["ranges"][k] = {"start": str(x), "end": str(y),
                                "median_ms": round(statistics.median(times[k]) * 1000, 1),
                                "min_ms": round(min(times[k]) * 1000, 1),
                                "reads": sorted(set(nread[k])), "most_read": keys[k],
                                "sha1": sorted(digest[k])}
            if a.dump:
                Path(a.dump).mkdir(parents=True, exist_ok=True)
                (Path(a.dump) / f"{k}.json").write_bytes(get(urls[k]).content)
    out["loadavg_end"] = os.getloadavg()
    print("@@" + json.dumps(out), flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SP-968：課表統計 API 的回應時間與讀設定次數")
    ap.add_argument("--base")
    ap.add_argument("--work", required=True)
    ap.add_argument("--reps", type=int, default=15)
    ap.add_argument("--dump", help="also write each range's response here (to diff before / after)")
    ap.add_argument("--out")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.child:
        return _child(a)
    from backend.scripts.perf_sp394 import _copy_base, _env
    work = Path(a.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    home = _copy_base(Path(a.base).resolve(), work / "home")
    env = _env(home, {"WKO5COACH_NO_WARMUP": "1"})
    cmd = [sys.executable, "-m", "backend.scripts.perf_sp968", "--child", "--work", str(work),
           "--reps", str(a.reps)] + (["--dump", str(Path(a.dump).resolve())] if a.dump else [])
    p = subprocess.run(cmd, env=env, cwd=str(REPO), stdout=subprocess.PIPE, text=True)
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
    if p.returncode or line is None:
        print(p.stdout[-2000:])
        return p.returncode or 1
    res = json.loads(line[2:])
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1), "utf-8")
    print(f"load {res['loadavg_start']} -> {res['loadavg_end']}")
    for k, r in res["ranges"].items():
        print(f"{k:5} {r['start']}..{r['end']}  median {r['median_ms']:7.1f} ms  min {r['min_ms']:7.1f} ms  "
              f"settings reads {r['reads']}  sha1 {r['sha1']}")
        print(f"      most read: {r['most_read']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
