"""
Stand-alone intervals.icu probe (official public API, personal API key).
NOT wired into the app. Research + usage: docs/research/data-hubs.md
(「使用者測試步驟」).

    .venv\\Scripts\\python.exe -m backend.scripts.intervals_probe --preview     # offline: print the test event
    .venv\\Scripts\\python.exe -m backend.scripts.intervals_probe               # read-only
    .venv\\Scripts\\python.exe -m backend.scripts.intervals_probe --push-test --date 2026-10-03
    .venv\\Scripts\\python.exe -m backend.scripts.intervals_probe --cleanup --date 2026-10-03

Auth: the API key is asked with getpass() and kept in memory only; it is
never written anywhere. Basic auth, username "API_KEY" (intervals.icu forum
post 609). Uses httpx, already in requirements.txt.

Read-only by default: profile, recent activities (with source / device),
one original activity file (GET /activity/{id}/file), wellness, upcoming
planned workouts. --push-test creates ONE planned workout named TEST_NAME
(external_id EXTERNAL_ID) via POST /events/bulk?upsert=true; --cleanup
deletes only an event with that name AND external_id. Every write asks y/N.

The offline part (session steps -> intervals.icu workout text) is
workout_text() / session_event(), unit-tested in
backend/tests/test_intervals_probe.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import gzip
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Optional

from backend.sync.coros_workouts import (EX_COOLDOWN, EX_REST, EX_TRAIN, EX_WARMUP, Repeat,
                                         Step, StepLike, Thresholds, session_steps,
                                         workout_name)

BASE = "https://intervals.icu/api/v1"
PROBE_DIR = Path.home() / ".wko5coach" / "intervals" / "probe"
TEST_NAME = "TrailRunCoach 測試課表（可刪除）"
EXTERNAL_ID = "trailruncoach-probe-test"
# forum post 609: Cloudflare may challenge non-browser user agents
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TrailRunCoach-probe/0.1"

# cue words only: digits in a cue could be read as a duration by the parser
CUE = {EX_WARMUP: "暖身", EX_TRAIN: "主課", EX_COOLDOWN: "緩和", EX_REST: "恢復"}


# ---------------------------------------------------------------------------
# offline: Step / Repeat (coros_workouts.session_steps) -> intervals.icu text
# syntax: forum "Workout Builder Syntax Quick Guide" (topic 123701, 2026-08-10)
# ---------------------------------------------------------------------------

def _duration(seconds: int) -> str:
    if seconds <= 0:
        raise ValueError("open (lap button) steps have no verified intervals.icu syntax")
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return "".join(f"{v}{u}" for v, u in ((h, "h"), (m, "m"), (s, "s")) if v) or "0s"


def _target(st: Step, th: Thresholds, power_targets: bool) -> str:
    if not st.intensity:
        return ""
    typ, lo, hi = st.intensity
    if typ == "hr":
        # no absolute-bpm syntax in the guide: % of LTHR (intervals.icu must hold the same LTHR)
        if not th.lthr:
            return ""
        a, b = round(lo / th.lthr * 100), round(hi / th.lthr * 100)
        return f"{a}-{b}% LTHR" if a != b else f"{a}% LTHR"
    if typ == "power" and power_targets:
        return f"{int(lo)}-{int(hi)}w" if int(lo) != int(hi) else f"{int(lo)}w"
    return ""


def _line(st: Step, th: Thresholds, power_targets: bool) -> str:
    parts = ["-", CUE[st.kind], _duration(st.seconds), _target(st, th, power_targets)]
    return " ".join(p for p in parts if p)


def workout_text(steps: list[StepLike], th: Thresholds, power_targets: bool = True) -> str:
    """Native intervals.icu workout text. Repeats are `Nx` blocks with a blank
    line before and after (nested repeats are not supported by intervals.icu)."""
    lines: list[str] = []
    for st in steps:
        if isinstance(st, Repeat):
            if any(isinstance(c, Repeat) for c in st.steps):
                raise ValueError("nested repeats are not supported by intervals.icu")
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"Main Set {int(st.sets)}x")
            lines += [_line(c, th, power_targets) for c in st.steps]
            lines.append("")
        else:
            lines.append(_line(st, th, power_targets))
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def event(day: str, name: str, text: str, external_id: str, typ: str = "Run") -> dict:
    return {"category": "WORKOUT", "start_date_local": f"{day}T00:00:00", "type": typ,
            "name": name, "description": text, "external_id": external_id}


def session_event(session: dict, thresholds: Optional[dict], power_targets: bool = True) -> dict:
    """intervals.icu planned-workout event for one plan session
    (raises coros_workouts.Unsupported like the COROS push)."""
    th = Thresholds.of(thresholds)
    text = workout_text(session_steps(session, th), th, power_targets)
    return event(session["day"], workout_name(session), text, f"trc-{session['id']}")


def test_event(day: str, lthr: float = 170.0) -> dict:
    th = Thresholds(lthr=lthr)
    steps: list[StepLike] = [
        Step(EX_WARMUP, 5 * 60, ("hr", round(0.70 * lthr), round(0.80 * lthr))),
        Repeat(2, [Step(EX_TRAIN, 60, None), Step(EX_REST, 60, None)]),
        Step(EX_COOLDOWN, 5 * 60, None),
    ]
    return event(day, TEST_NAME, workout_text(steps, th), EXTERNAL_ID)


def decode_file(data: bytes) -> tuple[bytes, str]:
    """Original activity file -> (bytes, extension). Handles gzip and zip."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if names:
                data = zf.read(names[0])
    if len(data) >= 12 and data[8:12] == b".FIT":
        return data, ".fit"
    head = data[:200].lower()
    if b"<gpx" in head:
        return data, ".gpx"
    if b"trainingcenterdatabase" in head:
        return data, ".tcx"
    return data, ".bin"


# ---------------------------------------------------------------------------
# network (user runs this; never in tests)
# ---------------------------------------------------------------------------

def confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


class Api:
    def __init__(self, key: str):
        import httpx
        self.c = httpx.Client(base_url=BASE, auth=("API_KEY", key), timeout=30,
                              headers={"User-Agent": USER_AGENT, "Accept": "application/json"})

    def _check(self, r):
        if r.status_code == 401 or r.status_code == 403:
            sys.exit(f"HTTP {r.status_code}：API key 不對，或被 Cloudflare 擋下")
        if r.status_code == 429:
            sys.exit(f"HTTP 429（超過限流）。Retry-After: {r.headers.get('Retry-After')} 秒")
        r.raise_for_status()
        rl = r.headers.get("X-RateLimit-Remaining")
        if rl:
            self.remaining = rl
        return r

    def get(self, path, **params):
        return self._check(self.c.get(path, params=params or None))

    def post(self, path, body, **params):
        return self._check(self.c.post(path, json=body, params=params or None))

    def delete(self, path):
        return self._check(self.c.delete(path))


def show_profile(api: Api) -> None:
    p = api.get("/athlete/0/profile").json()
    a = p.get("athlete", p) if isinstance(p, dict) else {}
    print(f"athlete id={a.get('id')}  name={a.get('name')}")


def list_activities(api: Api, limit: int, days: int) -> list[dict]:
    oldest = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    acts = api.get("/athlete/0/activities", oldest=oldest, limit=limit).json() or []
    print(f"\n最近 {days} 天內的活動（最多 {limit} 筆）：{len(acts)} 筆")
    for a in acts:
        km = (a.get("distance") or 0) / 1000
        print(f"  {a.get('id')}  {a.get('start_date_local')}  {a.get('type')!s:<10}  {km:6.2f} km  "
              f"source={a.get('source')}  device={a.get('device_name')}  file={a.get('file_type')}  "
              f"{a.get('name')}")
    return acts


def download_original(api: Api, activity_id: str) -> Path:
    r = api.get(f"/activity/{activity_id}/file")
    data, ext = decode_file(r.content)
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    out = PROBE_DIR / f"intervals_{activity_id}{ext}"
    out.write_bytes(data)
    print(f"  已下載 {out}（{len(data)} bytes，Content-Type {r.headers.get('Content-Type')}）")
    return out


def show_wellness(api: Api, days: int) -> None:
    newest = dt.date.today()
    rows = api.get("/athlete/0/wellness", oldest=(newest - dt.timedelta(days=days)).isoformat(),
                   newest=newest.isoformat()).json() or []
    print(f"\n最近 {days} 天的 wellness：{len(rows)} 筆")
    for w in rows[-7:]:
        print(f"  {w.get('id')}  restingHR={w.get('restingHR')}  hrv={w.get('hrv')}  "
              f"sleepSecs={w.get('sleepSecs')}  sleepScore={w.get('sleepScore')}  readiness={w.get('readiness')}")


def upcoming(api: Api, days: int = 14) -> list[dict]:
    a = dt.date.today()
    evs = api.get("/athlete/0/events", oldest=a.isoformat(),
                  newest=(a + dt.timedelta(days=days)).isoformat(), category="WORKOUT").json() or []
    print(f"\n未來 {days} 天的計畫課表：{len(evs)} 筆")
    for e in evs:
        print(f"  {e.get('start_date_local', '')[:10]}  id={e.get('id')}  ext={e.get('external_id')}  "
              f"{e.get('type')}  {e.get('name')}")
    return evs


def push_test(api: Api, day: str, lthr: float) -> None:
    if dt.date.fromisoformat(day) < dt.date.today():
        sys.exit("--date 不能是過去的日期")
    ev = test_event(day, lthr)
    print(f"\n將建立 1 個計畫課表「{TEST_NAME}」在 {day}：\n{ev['description']}\n")
    if not confirm("確定要寫入 intervals.icu？"):
        print("已取消")
        return
    res = api.post("/athlete/0/events/bulk", [ev], upsert="true").json()
    for e in res or []:
        doc = e.get("workout_doc") or {}
        print(f"  已建立 id={e.get('id')}  解析出 {len(doc.get('steps') or [])} 個步驟  "
              f"duration={doc.get('duration')}")
    print("  若 /settings 的 Garmin / COROS 方塊有勾「Upload planned workouts」，等同步後到手錶確認。"
          "測完執行 --cleanup --date 同一天。")


def cleanup(api: Api, day: str) -> None:
    evs = api.get("/athlete/0/events", oldest=day, newest=day, category="WORKOUT").json() or []
    mine = [e for e in evs if e.get("name") == TEST_NAME and e.get("external_id") == EXTERNAL_ID]
    if not mine:
        print(f"{day} 沒有 probe 建立的測試課表，什麼都不刪")
        return
    for e in mine:
        if confirm(f"刪除 {day} 的 event id={e['id']}「{TEST_NAME}」？"):
            api.delete(f"/athlete/0/events/{e['id']}")
            print(f"  已刪除 {e['id']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="intervals.icu probe（官方 API、個人 API key；見 docs/research/data-hubs.md）")
    p.add_argument("--preview", action="store_true", help="離線印出測試課表 event JSON，不連網")
    p.add_argument("--limit", type=int, default=5, help="列出幾筆活動（預設 5）")
    p.add_argument("--days", type=int, default=30, help="活動與 wellness 往回幾天（預設 30）")
    p.add_argument("--activity-id", help="要下載原始檔的活動 id（預設最新一筆）")
    p.add_argument("--no-download", action="store_true")
    p.add_argument("--push-test", action="store_true", help="建立一個測試計畫課表（會先問 y/N）")
    p.add_argument("--cleanup", action="store_true", help="刪除測試計畫課表（需 --date）")
    p.add_argument("--date", help="--push-test / --cleanup 的日期（預設明天）")
    p.add_argument("--lthr", type=float, default=170.0, help="測試課表暖身心率用的 LTHR（預設 170）")
    a = p.parse_args(argv)
    day = a.date or (dt.date.today() + dt.timedelta(days=1)).isoformat()

    if a.preview:
        print(json.dumps(test_event(day, a.lthr), ensure_ascii=False, indent=2))
        return 0
    if not 1 <= a.limit <= 50 or not 1 <= a.days <= 365:
        sys.exit("--limit 請用 1–50、--days 請用 1–365（probe 只做少量讀取）")

    key = getpass.getpass("intervals.icu API key（/settings → Developer Settings；不會顯示、不會儲存）：").strip()
    if not key:
        sys.exit("沒有輸入 API key")
    api = Api(key)
    del key
    try:
        if a.cleanup:
            cleanup(api, day)
        elif a.push_test:
            push_test(api, day, a.lthr)
        else:
            show_profile(api)
            acts = list_activities(api, a.limit, a.days)
            if not a.no_download:
                aid = a.activity_id or (acts[0]["id"] if acts else None)
                if aid:
                    print(f"\n下載活動 {aid} 的原始檔：")
                    download_original(api, str(aid))
            show_wellness(api, min(a.days, 14))
            upcoming(api)
        rem = getattr(api, "remaining", None)
        if rem:
            print(f"\n剩餘額度（15 分鐘, 每日）：{rem}")
    except Exception as e:
        print(f"\n失敗：{type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
