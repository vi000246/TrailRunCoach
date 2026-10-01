"""
Stand-alone Garmin Connect probe (unofficial `garminconnect` library). NOT
wired into the app: nothing here is imported by backend/api or backend/sync.
Research + usage: docs/research/garmin.md (§6 「使用者測試步驟」).

    .venv\\Scripts\\python.exe -m backend.scripts.garmin_probe --preview      # offline: print the test workout JSON
    .venv\\Scripts\\python.exe -m backend.scripts.garmin_probe                # read-only
    .venv\\Scripts\\python.exe -m backend.scripts.garmin_probe --push-test --date 2026-10-03
    .venv\\Scripts\\python.exe -m backend.scripts.garmin_probe --cleanup
    .venv\\Scripts\\python.exe -m backend.scripts.garmin_probe --forget

Needs `pip install garminconnect==0.3.17` in the venv (not in requirements.txt).

Credentials: email via input(), password via getpass(), MFA code via input().
The password is never stored. The session token (garminconnect's DI token
JSON) is cached only as ~/.wko5coach/garmin/session.enc, sealed with the
app's Fernet key (backend/settings/secrets.py); --no-cache keeps it in
memory only. The token is handed to the library as an inline JSON string,
never as a path, so the library never writes a plaintext token file.

Read-only by default. --push-test creates ONE workout named TEST_NAME and
schedules it; --cleanup removes only that workout (id recorded in
probe_state.json AND name == TEST_NAME). Every write asks y/N first.

The offline part (session steps -> Garmin workout JSON) is garmin_workout()
and is unit-tested in backend/tests/test_garmin_probe.py.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any, Optional

from backend.sync.coros_workouts import (EX_COOLDOWN, EX_REST, EX_TRAIN, EX_WARMUP, Repeat,
                                         Step, StepLike, Thresholds, session_steps,
                                         workout_name)

GARMIN_DIR = Path.home() / ".wko5coach" / "garmin"
SESSION_FILE = GARMIN_DIR / "session.enc"
PROBE_DIR = GARMIN_DIR / "probe"
STATE_FILE = GARMIN_DIR / "probe_state.json"

TEST_NAME = "TrailRunCoach 測試課表（可刪除）"
PACKAGE_HINT = 'pip install "garminconnect==0.3.17"'

# ---------------------------------------------------------------------------
# offline: Step / Repeat (coros_workouts.session_steps) -> Garmin workout JSON
# IDs from garminconnect 0.3.17 workout.py ("from /workout-service/workout/types")
# ---------------------------------------------------------------------------

SPORT_RUNNING = {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1}

STEP_TYPE = {
    EX_WARMUP: {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
    EX_COOLDOWN: {"stepTypeId": 2, "stepTypeKey": "cooldown", "displayOrder": 2},
    EX_TRAIN: {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
    EX_REST: {"stepTypeId": 4, "stepTypeKey": "recovery", "displayOrder": 4},
}
STEP_REPEAT = {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6}

END_LAP = {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1, "displayable": True}
END_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": True}
END_ITER = {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": False}

TARGET_NONE = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
TARGET_POWER = {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone", "displayOrder": 1}
TARGET_HR = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 1}


def _executable(st: Step, order: int, power_targets: bool) -> dict:
    notes = [st.name] if st.name else []
    d: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": dict(STEP_TYPE[st.kind]),
        "endCondition": dict(END_TIME if st.seconds else END_LAP),
        "endConditionValue": float(st.seconds) if st.seconds else None,
        "targetType": dict(TARGET_NONE),
    }
    if st.intensity:
        typ, lo, hi = st.intensity
        if typ == "hr":
            d["targetType"] = dict(TARGET_HR)
            d["targetValueOne"], d["targetValueTwo"] = float(lo), float(hi)
        elif typ == "power":
            if power_targets:
                d["targetType"] = dict(TARGET_POWER)
                d["targetValueOne"], d["targetValueTwo"] = float(lo), float(hi)
            else:
                # app CP (COROS / WKO5 power) is not Garmin wrist / Stryd power: note only
                notes.append(f"目標功率 {int(lo)}–{int(hi)} W")
    if notes:
        d["description"] = "；".join(notes)
    return {k: v for k, v in d.items() if v is not None}


def garmin_steps(steps: list[StepLike], power_targets: bool = False) -> tuple[list[dict], int]:
    """Garmin workoutSteps for `steps` and the estimated duration (s).
    stepOrder is unique across the segment, nested steps included (depth-first)."""
    order = 0
    total = 0
    out: list[dict] = []
    for st in steps:
        order += 1
        if isinstance(st, Repeat):
            group_order = order
            children = []
            for c in st.steps:
                if isinstance(c, Repeat):
                    raise ValueError("nested repeats are not produced by session_steps")
                order += 1
                children.append(_executable(c, order, power_targets))
            out.append({
                "type": "RepeatGroupDTO",
                "stepOrder": group_order,
                "stepType": dict(STEP_REPEAT),
                "numberOfIterations": int(st.sets),
                "smartRepeat": False,
                "endCondition": dict(END_ITER),
                "endConditionValue": float(st.sets),
                "workoutSteps": children,
            })
            total += st.sets * sum(c.seconds for c in st.steps)
        else:
            out.append(_executable(st, order, power_targets))
            total += st.seconds
    return out, total


def build_workout(name: str, steps: list[StepLike], description: str = "",
                  power_targets: bool = False) -> dict:
    ws, total = garmin_steps(steps, power_targets)
    w = {
        "workoutName": name,
        "sportType": dict(SPORT_RUNNING),
        "estimatedDurationInSecs": int(total),
        "author": {},
        "workoutSegments": [{"segmentOrder": 1, "sportType": dict(SPORT_RUNNING), "workoutSteps": ws}],
    }
    if description:
        w["description"] = description[:500]
    return w


def garmin_workout(session: dict, thresholds: Optional[dict], power_targets: bool = False) -> dict:
    """Garmin workout JSON for one plan session (raises coros_workouts.Unsupported)."""
    th = Thresholds.of(thresholds)
    steps = session_steps(session, th)
    return build_workout(workout_name(session), steps, session.get("detail") or "", power_targets)


def test_workout(with_power: bool = False) -> dict:
    """The one workout --push-test creates: short, obviously a test."""
    steps: list[StepLike] = [
        Step(EX_WARMUP, 5 * 60, ("hr", 110, 140), "測試：暖身，心率 110–140"),
        Repeat(2, [Step(EX_TRAIN, 60, None, "測試：1 分"), Step(EX_REST, 60, None, "測試：恢復")], "2×1 分"),
    ]
    if with_power:
        steps.append(Step(EX_TRAIN, 3 * 60, ("power", 200, 230), "測試：功率 200–230 W"))
    steps.append(Step(EX_COOLDOWN, 5 * 60, None, "測試：緩和"))
    return build_workout(TEST_NAME, steps, "TrailRunCoach garmin_probe 建立的測試課表，可直接刪除。",
                         power_targets=with_power)


# ---------------------------------------------------------------------------
# token cache (Fernet-sealed, never plaintext)
# ---------------------------------------------------------------------------

def load_cached_tokens() -> Optional[str]:
    if not SESSION_FILE.exists():
        return None
    from backend.settings import secrets
    try:
        return secrets.unseal(SESSION_FILE.read_text("ascii").strip())
    except secrets.SecretError as e:
        print(f"  （token 快取解不開，改用帳密登入：{e}）")
        return None


def save_tokens(token_json: str) -> None:
    from backend.settings import secrets
    sealed = secrets.seal(token_json)
    if not sealed or not sealed.startswith(secrets.PREFIX):
        raise RuntimeError("seal() did not encrypt the token; refusing to write it")
    GARMIN_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(sealed, "ascii")
    try:
        os.chmod(SESSION_FILE, 0o600)
    except OSError:
        pass


def forget_tokens() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()
        print(f"已刪除 {SESSION_FILE}")
    else:
        print("沒有 token 快取")


# ---------------------------------------------------------------------------
# network (user runs this; never in tests)
# ---------------------------------------------------------------------------

def confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def _import_garmin():
    try:
        import garminconnect
    except ImportError:
        sys.exit(f"找不到 garminconnect 套件。請先在 venv 安裝：{PACKAGE_HINT}")
    return garminconnect


def _use_cffi_api(api) -> None:
    """Workaround from python-garminconnect issue #444 (403 on every API call)."""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        sys.exit("--cffi-api 需要 curl_cffi（garminconnect 會一併安裝）")
    api.client._api_session = cffi_requests.Session(impersonate="chrome")


def login(use_cache: bool, cffi_api: bool):
    gc = _import_garmin()
    if os.environ.pop("GARMINTOKENS", None):
        print("  （已忽略環境變數 GARMINTOKENS，避免套件寫出明文 token 檔）")
    verify = not cffi_api
    cached = load_cached_tokens() if use_cache else None
    if cached:
        api = gc.Garmin(verify_login=verify)
        if cffi_api:
            _use_cffi_api(api)
        try:
            api.login(tokenstore=cached)        # inline JSON string: nothing written to disk
            print("用加密快取的 token 登入成功")
            return api
        except gc.GarminConnectTooManyRequestsError:
            sys.exit("Garmin 回 429（登入限流）。請隔幾小時再試，不要連續重試。")
        except Exception as e:
            print(f"  快取的 token 不能用（{type(e).__name__}），改用帳密登入")
    email = input("Garmin 帳號 email：").strip()
    password = getpass.getpass("Garmin 密碼（不會顯示、不會儲存）：")
    api = gc.Garmin(email=email, password=password, verify_login=verify,
                    prompt_mfa=lambda: input("MFA 驗證碼：").strip())
    del password
    if cffi_api:
        _use_cffi_api(api)
    try:
        api.login()
    except gc.GarminConnectTooManyRequestsError:
        sys.exit("Garmin 回 429（登入限流）。請隔幾小時再試，不要連續重試。")
    except gc.GarminConnectAuthenticationError as e:
        sys.exit(f"登入失敗（帳密或 MFA）：{e}")
    print("登入成功")
    return api


def persist(api, use_cache: bool) -> None:
    if not use_cache:
        print("  （--no-cache：token 只在記憶體，程式結束就丟掉）")
        return
    save_tokens(api.client.dumps())
    print(f"  token 已加密存到 {SESSION_FILE}")


def _hint_403(e: Exception) -> None:
    if "403" in str(e):
        print("  提示：登入成功但 API 回 403，可加 --cffi-api 再試（python-garminconnect #444）")


def list_activities(api, limit: int) -> list[dict]:
    acts = api.get_activities(0, limit) or []
    print(f"\n最近 {len(acts)} 筆活動：")
    for a in acts:
        typ = (a.get("activityType") or {}).get("typeKey")
        km = (a.get("distance") or 0) / 1000
        mins = (a.get("duration") or 0) / 60
        print(f"  {a.get('activityId')}  {a.get('startTimeLocal')}  {typ:<16}  "
              f"{km:6.2f} km  {mins:6.1f} 分  {a.get('activityName')}")
    return acts


def download_original(api, activity_id: str) -> Path:
    gc = _import_garmin()
    data = api.download_activity(activity_id, dl_fmt=gc.Garmin.ActivityDownloadFormat.ORIGINAL)
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        out = PROBE_DIR / f"garmin_{activity_id}.bin"
        out.write_bytes(data)
        print(f"  回傳不是 ZIP（{len(data)} bytes），原樣存成 {out}")
        return out
    saved = None
    for info in zf.infolist():
        name = Path(info.filename).name
        if not name:
            continue
        out = PROBE_DIR / f"garmin_{activity_id}_{name}"
        out.write_bytes(zf.read(info))
        print(f"  已下載 {out}（{info.file_size} bytes）")
        saved = saved or out
    return saved or PROBE_DIR


def _months(n: int = 2) -> list[tuple[int, int]]:
    d = dt.date.today().replace(day=1)
    out = []
    for _ in range(n):
        out.append((d.year, d.month))
        d = (d + dt.timedelta(days=32)).replace(day=1)
    return out


def scheduled_items(api, months=None) -> list[dict]:
    items = []
    for y, m in months or _months():
        cal = api.get_scheduled_workouts(y, m) or {}
        items += [i for i in cal.get("calendarItems") or [] if i.get("itemType") == "workout"]
    return items


def list_scheduled(api) -> None:
    items = scheduled_items(api)
    print(f"\n本月與下月排程的課表：{len(items)} 筆")
    for i in items:
        print(f"  {i.get('date')}  id={i.get('id')}  workoutId={i.get('workoutId')}  {i.get('title')}")


def daily(api, day: str) -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    calls = {"hrv": api.get_hrv_data, "sleep": api.get_sleep_data,
             "training_status": api.get_training_status, "training_readiness": api.get_training_readiness}
    print(f"\n{day} 的每日指標：")
    for key, fn in calls.items():
        try:
            data = fn(day)
        except Exception as e:
            print(f"  {key}: 失敗 {type(e).__name__}: {e}")
            continue
        out = PROBE_DIR / f"daily_{day}_{key}.json"
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        keys = list(data.keys())[:8] if isinstance(data, dict) else f"list[{len(data or [])}]"
        print(f"  {key}: 已存 {out.name}；欄位 {keys}")


def push_test(api, day: str, with_power: bool) -> None:
    if dt.date.fromisoformat(day) < dt.date.today():
        sys.exit("--date 不能是過去的日期")
    w = test_workout(with_power)
    print(f"\n將建立 1 個課表「{TEST_NAME}」，排在 {day}。")
    if not confirm("確定要寫入 Garmin Connect？"):
        print("已取消")
        return
    res = api.upload_workout(w)
    wid = (res or {}).get("workoutId")
    state = {"workout_id": wid, "name": TEST_NAME, "date": day, "upload_response_keys": sorted((res or {}).keys())}
    GARMIN_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    if not wid:
        print(f"  上傳回應裡沒有 workoutId：{res}")
        return
    print(f"  已建立 workoutId={wid}")
    if not confirm(f"要把它排上 {day} 的行事曆？"):
        print("  只建立、沒排程。之後可用 --cleanup 刪除。")
        return
    sched = api.schedule_workout(wid, day)
    state["schedule_response"] = sched
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    print(f"  已排程：{sched}")
    print("  請讓手錶同步，然後到「訓練 → 課表」或當天行事曆確認。測完執行 --cleanup。")


def _schedule_ids(state: dict, items: list[dict]) -> list:
    ids = []
    sched = state.get("schedule_response")
    if isinstance(sched, dict):
        for k in ("workoutScheduleId", "scheduleId", "id"):
            if sched.get(k):
                ids.append(sched[k])
                break
    for i in items:
        if str(i.get("workoutId")) == str(state["workout_id"]) and i.get("title") == TEST_NAME and i.get("id"):
            ids.append(i["id"])
    return list(dict.fromkeys(ids))


def cleanup(api) -> None:
    if not STATE_FILE.exists():
        print("沒有 probe_state.json：probe 沒建立過測試課表，什麼都不刪")
        return
    state = json.loads(STATE_FILE.read_text("utf-8"))
    wid = state.get("workout_id")
    if not wid:
        print("probe_state.json 沒有 workout_id，什麼都不刪")
        return
    w = api.get_workout_by_id(wid)
    if (w or {}).get("workoutName") != TEST_NAME:
        print(f"workoutId={wid} 的名稱不是「{TEST_NAME}」（是 {(w or {}).get('workoutName')!r}），不刪")
        return
    day = state.get("date")
    months = [tuple(map(int, day.split("-")[:2]))] if day else None
    for sid in _schedule_ids(state, scheduled_items(api, months)):
        if confirm(f"取消排程 id={sid}？"):
            try:
                api.unschedule_workout(sid)
                print(f"  已取消排程 {sid}")
            except Exception as e:
                print(f"  取消排程 {sid} 失敗：{type(e).__name__}: {e}")
    if confirm(f"刪除課表 workoutId={wid}「{TEST_NAME}」？"):
        api.delete_workout(wid)
        STATE_FILE.unlink()
        print("  已刪除，probe_state.json 也已移除")
        left = [i for i in scheduled_items(api, months) if str(i.get("workoutId")) == str(wid)]
        if left:
            print(f"  注意：行事曆上還看得到 {len(left)} 筆，請到 Garmin Connect 手動刪：{left}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Garmin Connect probe（非官方 garminconnect；見 docs/research/garmin.md §6）")
    p.add_argument("--preview", action="store_true", help="離線印出測試課表 JSON，不登入")
    p.add_argument("--limit", type=int, default=5, help="列出幾筆活動（預設 5）")
    p.add_argument("--activity-id", help="要下載原始 FIT 的活動 id（預設最新一筆）")
    p.add_argument("--no-download", action="store_true", help="不下載 FIT")
    p.add_argument("--daily", metavar="YYYY-MM-DD", help="另外讀這天的 HRV / 睡眠 / Training Status / Readiness")
    p.add_argument("--push-test", action="store_true", help="建立並排程一個測試課表（會先問 y/N）")
    p.add_argument("--date", help="--push-test 的日期（預設明天）")
    p.add_argument("--with-power", action="store_true", help="--push-test 多加一段功率目標")
    p.add_argument("--cleanup", action="store_true", help="刪除 --push-test 建立的課表（會先問 y/N）")
    p.add_argument("--forget", action="store_true", help="刪除加密的 token 快取")
    p.add_argument("--no-cache", action="store_true", help="token 只放記憶體，不寫加密快取")
    p.add_argument("--cffi-api", action="store_true", help="資料 API 改用 curl_cffi（#444：登入成功但 API 都 403 時）")
    a = p.parse_args(argv)

    if a.preview:
        print(json.dumps(test_workout(a.with_power), ensure_ascii=False, indent=2))
        return 0
    if a.forget:
        forget_tokens()
        return 0
    if a.limit < 1 or a.limit > 50:
        sys.exit("--limit 請用 1–50（probe 只做少量讀取）")

    use_cache = not a.no_cache
    api = login(use_cache, a.cffi_api)
    try:
        if a.cleanup:
            cleanup(api)
        elif a.push_test:
            push_test(api, a.date or (dt.date.today() + dt.timedelta(days=1)).isoformat(), a.with_power)
        else:
            acts = list_activities(api, a.limit)
            if not a.no_download:
                aid = a.activity_id or (acts[0]["activityId"] if acts else None)
                if aid:
                    print(f"\n下載活動 {aid} 的原始 FIT：")
                    download_original(api, str(aid))
            list_scheduled(api)
            if a.daily:
                daily(api, a.daily)
    except Exception as e:
        print(f"\n失敗：{type(e).__name__}: {e}")
        _hint_403(e)
        return 1
    finally:
        try:
            persist(api, use_cache)
        except Exception as e:
            print(f"  token 沒有存下來：{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
