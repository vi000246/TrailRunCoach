"""
SP-956 的量測，可重跑：按「當作間歇判讀」後，間歇頁六張卡（views/workout.json 的「間歇」）重算要多久。
只讀示範跑者的資料（backend.demo.build 產生），複製到暫存資料夾再跑，從不碰 ~/.wko5coach、~/WKO5 或網路。

    python -m backend.demo.build --root /tmp/trc-demo
    BASE=/tmp/trc-demo/base/$(cat /tmp/trc-demo/base/current)
    python -m backend.scripts.perf_sp956 --base $BASE --work /tmp/trc-sp956 [--workout N] [--out r.json]

子行程裡（app 設定與正式環境一樣）先照圖表頁的樣子把那筆活動的間歇頁開一次（`?stale=1`，等背景畫完），
再對它 PATCH /api/v1/app/activities 加上標籤（跟按鈕送的一樣），然後照 SP-917 之後按鈕的重畫
（`load({ stale: false })`）：六張卡、四條同時（charts.html 的四個 worker），記每張卡的牆鐘與
頁面全部畫完的時間。另外：

  profile   同一筆活動、標記後直接呼叫 interval_eval.card() 一次（cProfile），列最花時間的函式
  serial    同一個流程但一次只問一張卡（沒有並行時每張卡自己的時間）

活動：--workout 給 index，沒給就挑最長的一筆跑步。回應的 sha1 也記下來——改前改後跑同一支、sha1 一樣，
就是六張卡的內容完全一樣。NAS 推估 = 本機 × 8.5（docs/research/cache-tiering.md）。
"""
from __future__ import annotations

import argparse
import cProfile
import hashlib
import io
import json
import os
import pstats
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
VIEW = "單次活動判讀"
DASH = 2                        # views/workout.json: 間歇
N_CARDS = 6
WORKERS = 4                     # charts.html load(): four workers
FLAG = "當作間歇"
NAS_RATIO = 8.5


def _child(a) -> int:
    import logging
    logging.basicConfig(level=logging.WARNING)
    from urllib.parse import quote
    from fastapi.testclient import TestClient
    from backend.main import build_app

    app = build_app(demo=False)
    out: dict = {"loadavg_start": os.getloadavg()}
    with TestClient(app) as c:
        def get(path):
            r = c.get(path)
            if r.status_code >= 400:
                raise RuntimeError(f"{path}: {r.status_code} {r.text[:200]}")
            return r

        rows = get("/api/v1/app/workouts").json()
        rows = rows if isinstance(rows, list) else rows.get("workouts") or []
        from backend.api.wko5views import _dataset
        ds = _dataset()
        runs = [w for w in ds.workouts if w.sport == "run"]
        if a.workout is not None:
            w = next(x for x in ds.workouts if x.idx == a.workout)
        else:
            w = max(runs, key=lambda x: x.metrics.get("duration") or 0)     # the longest run
        row = next((r for r in rows if r.get("index") == w.idx), {})
        base = f"/api/v1/app/views/{quote(VIEW)}/dashboards/{DASH}/charts/{{}}?workout={w.idx}"

        # the page as the user saw it before the button (stale=1, the background drawings done)
        for i in range(N_CARDS):
            get(base.format(i) + "&stale=1")
        for i in range(N_CARDS):
            get(base.format(i))
        before = get(base.format(0)).json()
        out["workout"] = {"index": w.idx, "start": row.get("start"), "name": row.get("name"),
                          "duration_min": round((w.metrics.get("duration") or 0) / 60, 1),
                          "n_runs": len(runs), "state_before": {k: before.get(k) for k in ("badge",) if k in before},
                          "offer": bool(before.get("action"))}

        from backend.engine import activity_tags as AT
        r = c.patch("/api/v1/app/activities", json={"items": [{"key": AT.key_of(w.entry.start),
                                                                "file": getattr(w.entry, "file", None)}],
                                                     "add_tags": [FLAG]})
        assert r.status_code == 200, r.text
        from backend.engine import interval_eval as IE
        assert IE.flagged(w), "the mark did not land"

        if a.mode == "profile":
            pr = cProfile.Profile()
            t0 = time.perf_counter()
            pr.enable()
            e = IE.card(ds, w)
            pr.disable()
            out["card_s"] = round(time.perf_counter() - t0, 3)
            out["card_ok"] = e.get("ok")
            out["n_peers"] = len(e.get("peers") or [])
            s = io.StringIO()
            pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(40)
            out["profile"] = s.getvalue()
        else:
            times: dict = {}
            digest: dict = {}
            lock = threading.Lock()

            def one(i):
                t = time.perf_counter()
                body = get(base.format(i)).content
                with lock:
                    times[i] = round(time.perf_counter() - t, 3)
                    digest[i] = hashlib.sha1(body).hexdigest()[:12]
            t0 = time.perf_counter()
            with ThreadPoolExecutor(1 if a.mode == "serial" else WORKERS) as ex:
                list(ex.map(one, range(N_CARDS)))
            out["page_s"] = round(time.perf_counter() - t0, 3)
            out["cards_s"] = [times[i] for i in range(N_CARDS)]
            out["sha1"] = [digest[i] for i in range(N_CARDS)]
            after = get(base.format(0)).json()
            out["badge_after"] = after.get("badge")
    out["loadavg_end"] = os.getloadavg()
    print("@@" + json.dumps(out, default=str), flush=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="SP-956：「當作間歇判讀」後間歇頁的重算時間")
    ap.add_argument("--base", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--workout", type=int)
    ap.add_argument("--mode", choices=("page", "serial", "profile"), default="page")
    ap.add_argument("--out")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a.child:
        return _child(a)
    from backend.scripts.perf_sp394 import _copy_base, _env
    work = Path(a.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    home = _copy_base(Path(a.base).resolve(), work / f"home-{a.mode}")
    env = _env(home, {"WKO5COACH_NO_WARMUP": "1"})
    cmd = [sys.executable, "-m", "backend.scripts.perf_sp956", "--child", "--base", a.base, "--work", str(work),
           "--mode", a.mode] + (["--workout", str(a.workout)] if a.workout is not None else [])
    p = subprocess.run(cmd, env=env, cwd=str(REPO), stdout=subprocess.PIPE, text=True)
    line = next((x for x in p.stdout.splitlines() if x.startswith("@@")), None)
    if p.returncode or line is None:
        print(p.stdout[-3000:])
        return p.returncode or 1
    res = json.loads(line[2:])
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False), "utf-8")
    print(f"load {res['loadavg_start']} -> {res['loadavg_end']}")
    print("workout", json.dumps(res["workout"], ensure_ascii=False))
    if a.mode == "profile":
        print(f"card() {res['card_s']} s (NAS ≈ {res['card_s'] * NAS_RATIO:.1f} s), ok={res['card_ok']}, "
              f"peers={res['n_peers']}")
        print(res["profile"])
    else:
        print(f"page {res['page_s']} s (NAS ≈ {res['page_s'] * NAS_RATIO:.1f} s)  cards {res['cards_s']}")
        print(f"sha1 {res['sha1']}")
        print(f"badge after {res['badge_after']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
