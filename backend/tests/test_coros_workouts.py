"""
Week plan -> COROS push (backend/sync/coros_workouts.py) against a fake
Training Hub (httpx.MockTransport): payload mapping, idempotency, replace /
remove, error handling. Never touches the network or a real account.
"""
import asyncio
import copy
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Athlete, Base, CorosPlanPush, SyncState
from backend.settings.secrets import seal
from backend.sync import coros_workouts as CW, http

BASE = "https://teameuapi.coros.com"
TH = {"cp": 300.0, "lthr": 170.0, "aet": 150.0}


@pytest.fixture(autouse=True)
def _pin_real_today(monkeypatch):
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 9, 30))


def run(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeHub:
    """Scripted Training Hub: a library of programs and one calendar plan."""

    def __init__(self):
        self.programs: dict[str, dict] = {}
        self.entities: list[dict] = []
        self.next_id = 480000000000000001
        self.max_id_in_plan = 7
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[str, dict] = {}          # path -> response json to return once
        self.status_override: dict[str, int] = {}

    def ok(self, data=None):
        return httpx.Response(200, json={"result": "0000", "message": "OK", "data": data})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append((req.method, path))
        assert req.headers.get("accessToken") == "tok"
        assert json.loads(req.headers["yfheader"]) == {"userId": "u1"}
        if path in self.status_override:
            return httpx.Response(self.status_override.pop(path), text="nope")
        if path in self.fail:
            return httpx.Response(200, json=self.fail.pop(path))
        q = parse_qs(urlparse(str(req.url)).query)
        body = json.loads(req.content) if req.content else None
        if path == "/training/program/add":
            pid = str(self.next_id)
            self.next_id += 1
            self.programs[pid] = {**copy.deepcopy(body), "id": pid, "deleted": 0}
            return self.ok(pid)
        if path == "/training/program/detail":
            p = self.programs.get(q["id"][0])
            return self.ok(p)
        if path == "/training/program/delete":
            for pid in body:
                self.programs[pid]["deleted"] = 1
            return self.ok()
        if path == "/training/program/query":
            return self.ok([p for p in self.programs.values() if not p["deleted"]])
        if path == "/training/schedule/query":
            # live shape: top-level id is the calendar, entity.planId the plan the entry
            # lives in (they differ); happenDay is an int, ids are strings
            a, b = int(q["startDate"][0]), int(q["endDate"][0])
            ents = [e for e in self.entities if a <= e["happenDay"] <= b]
            return self.ok({"id": "cal1", "maxIdInPlan": str(self.max_id_in_plan),
                            "maxPlanProgramId": str(self.max_id_in_plan), "entities": ents,
                            "programs": []})
        if path == "/training/schedule/update":
            assert body["pbVersion"] == 2
            for v in body["versionObjects"]:
                if v["status"] == 1:
                    ent = body["entities"][0]
                    self.max_id_in_plan = max(self.max_id_in_plan, int(v["id"]))
                    self.entities.append({"happenDay": int(ent["happenDay"]), "idInPlan": str(v["id"]),
                                          "planProgramId": str(v["id"]), "planId": "plan9",
                                          "sortNoInSchedule": ent["sortNoInSchedule"], "executeStatus": 0,
                                          "program": body["programs"][0]})
                elif v["status"] == 3:
                    assert v["planId"] == "plan9"
                    self.entities = [e for e in self.entities if e["idInPlan"] != v["id"]]
            return self.ok()
        return httpx.Response(404, json={})

    def live(self):
        return {pid: p for pid, p in self.programs.items() if not p["deleted"]}

    def adds(self):
        return sum(1 for m, p in self.calls if p == "/training/program/add")


async def make_db(logged_in=True):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    s = async_sessionmaker(engine, expire_on_commit=False)()
    s.add(Athlete(id=1, name="tester", data_dir="x"))
    if logged_in:
        s.add(SyncState(athlete_id=1, coros_access_token=seal("tok"), coros_base_url=BASE,
                        coros_user_id="u1",
                        coros_token_expires=datetime.now(timezone.utc) + timedelta(hours=5)))
    await s.commit()
    return s


def sess(id, kind, title, minutes, day, target="", detail="", done=False, source=""):
    return {"id": id, "kind": kind, "title": title, "minutes": minutes, "target": target,
            "detail": detail, "source": source, "tss": 30.0, "day": day, "done": done, "done_by": None}


def plan(sessions, start="2026-09-28", today="2026-09-30", th=TH):
    return {"week": {"start": start, "end": "2026-10-04", "today": today},
            "sessions": sessions, "thresholds": dict(th or {})}


def week1():
    return plan([
        sess("long", "long", "LSD（山路）", 120, "2026-10-03", detail="全程心率壓在 AeT 150 bpm 以下"),
        sess("quality", "quality", "閾值 3×10 分", 60, "2026-10-01", detail="休 2–3 分鐘；暖身 15 分、緩和 10 分",
             source="Palladino 功率區間（3B）"),
        sess("strength1", "strength", "肌力（下肢單腳＋核心）", 35, "2026-10-02"),
        sess("easy1", "easy", "輕鬆跑", 45, "2026-10-02"),
        sess("easy2", "easy", "輕鬆跑", 40, "2026-09-29", done=True),
    ])


# ---------------------------------------------------------------------------
# mapping
# ---------------------------------------------------------------------------

def _flat(payload):
    return payload["exercises"]


def test_road_easy_run_is_power_first():
    # road easy / long: power first (target_policy, 2026-10-02); the HR cap stays a note
    spec = CW.session_workout(sess("easy0", "easy", "輕鬆跑", 45, "2026-10-02"), TH)
    (e,) = _flat(spec.payload)
    assert e["targetValue"] == 45 * 60 and e["intensityType"] == CW.INT_POWER


def test_trail_easy_run_is_hr_capped_at_aet():
    spec = CW.session_workout(sess("easy1", "easy", "越野輕鬆跑", 45, "2026-10-02"), TH)
    ex = _flat(spec.payload)
    assert len(ex) == 1
    e = ex[0]
    assert e["exerciseType"] == CW.EX_TRAIN and e["targetType"] == CW.TARGET_TIME and e["targetValue"] == 45 * 60
    assert e["intensityType"] == CW.INT_HR and e["hrType"] == CW.HR_TYPE_LTHR
    assert e["isIntensityPercent"] is False
    assert e["intensityValueExtend"] == 150                 # AeT cap
    assert e["intensityValue"] == round(0.75 * 170)
    assert e["intensityPercentExtend"] == round(150 / 170 * 100000)
    p = spec.payload
    assert p["sportType"] == CW.SPORT_RUN and p["estimatedTime"] == 45 * 60
    assert p["name"] == "TRC 越野輕鬆跑 10/2"


def test_long_and_hike_time_based_hr():
    spec = CW.session_workout(sess("long", "long", "LSD", 150, "2026-10-03"), TH)
    (e,) = _flat(spec.payload)
    assert e["targetValue"] == 150 * 60 and e["intensityType"] == CW.INT_POWER      # road long: power first
    for kind in ("hike", "mountain"):
        spec = CW.session_workout(sess(kind, kind, "LSD", 150, "2026-10-03"), TH)
        (e,) = _flat(spec.payload)
        assert e["targetValue"] == 150 * 60 and e["intensityType"] == CW.INT_HR
        assert e["intensityValueExtend"] == 150


def test_threshold_intervals_use_cp_power():
    s = sess("quality", "quality", "閾值 3×10 分", 60, "2026-10-01",
             detail="休 2–3 分鐘；暖身 15 分、緩和 10 分")
    ex = _flat(CW.session_workout(s, TH).payload)
    kinds = [(e["exerciseType"], e["isGroup"]) for e in ex]
    assert kinds == [(1, False), (0, True), (2, False), (4, False), (3, False)]
    warm, grp, work, rest, cool = ex
    assert warm["targetValue"] == 15 * 60 and warm["intensityType"] == CW.INT_HR
    assert grp["sets"] == 3 and grp["targetValue"] == 13 * 60
    assert work["groupId"] == str(grp["id"]) and rest["groupId"] == str(grp["id"])
    assert work["targetValue"] == 600 and work["intensityType"] == CW.INT_POWER
    assert (work["intensityValue"], work["intensityValueExtend"]) == (round(0.95 * 300), round(1.01 * 300))
    assert rest["targetValue"] == 180 and rest["intensityType"] == CW.INT_NONE
    assert cool["targetValue"] == 10 * 60
    assert grp["sortNo"] < work["sortNo"] < rest["sortNo"] < cool["sortNo"]
    total = 15 * 60 + 3 * 13 * 60 + 10 * 60
    assert CW.session_workout(s, TH).payload["estimatedTime"] == total


def test_hill_repeats_and_taper_percent():
    s = sess("quality", "quality", "爬坡間歇 5×4 分", 60, "2026-10-01",
             detail="上坡 4 分鐘（6–10% 坡），慢跑或走下來恢復；暖身 15 分、緩和 10 分")
    ex = _flat(CW.session_workout(s, TH).payload)
    work, rest = ex[2], ex[3]
    assert ex[1]["sets"] == 5 and work["targetValue"] == 240 and rest["targetValue"] == 240
    assert (work["intensityValue"], work["intensityValueExtend"]) == (round(1.01 * 300), round(1.06 * 300))
    t = sess("quality", "quality", "短強度 4×3 分", 45, "2026-10-01", detail="保留強度、不累積疲勞（98–102% CP）")
    ex = _flat(CW.session_workout(t, TH).payload)
    assert ex[1]["sets"] == 4
    assert (ex[2]["intensityValue"], ex[2]["intensityValueExtend"]) == (294, 306)


def _cp_sess(protocol):
    from backend.engine import cp_protocols as CPP
    t = CPP.session_for(protocol)
    return {**sess("test", "test", t["title"], t["minutes"], "2026-10-01", target=t["target"], detail=t["detail"]),
            "protocol": protocol}


def test_cp_test_structure_legacy_title_keeps_its_order():
    # a stored pre-protocol row: 3′ first, the all-out bouts now open (no power range)
    s = sess("test", "test", "CP 測試 3 分 + 12 分", 60, "2026-10-01",
             target="兩段都全力、配速平均；中間休 30 分鐘", detail="平路或跑步機，暖身 15 分鐘")
    ex = _flat(CW.session_workout(s, TH).payload)
    assert [e["targetValue"] for e in ex] == [900, 180, 1800, 720, 600]
    assert ex[1]["intensityType"] == CW.INT_NONE and ex[3]["intensityType"] == CW.INT_NONE
    assert ex[1]["name"] == "3 分全力" and ex[3]["name"] == "12 分全力"


def test_cp_test_structure_quick():
    ex = _flat(CW.session_workout(_cp_sess("quick"), TH).payload)
    assert [e["exerciseType"] for e in ex] == [CW.EX_WARMUP, CW.EX_TRAIN, CW.EX_COOLDOWN]
    assert [e["targetValue"] for e in ex] == [12 * 60, 20 * 60, 5 * 60]         # 37 min, as the session says
    work = ex[1]
    assert work["name"] == "20 分全力" and work["intensityType"] == CW.INT_NONE   # open target
    assert work["targetType"] == CW.TARGET_TIME
    assert ex[0]["intensityType"] == CW.INT_HR and ex[2]["intensityType"] == CW.INT_HR
    assert CW.session_workout(_cp_sess("quick"), TH).payload["estimatedTime"] == 37 * 60


def test_cp_test_structure_standard_long_bout_first():
    ex = _flat(CW.session_workout(_cp_sess("standard"), TH).payload)
    assert [e["exerciseType"] for e in ex] == [CW.EX_WARMUP, CW.EX_TRAIN, CW.EX_REST, CW.EX_TRAIN, CW.EX_COOLDOWN]
    assert [e["targetValue"] for e in ex] == [900, 720, 1800, 180, 600]         # 12′ → 30′ → 3′; 70 min
    assert ex[1]["name"] == "12 分全力" and ex[3]["name"] == "3 分全力"
    assert ex[1]["intensityType"] == CW.INT_NONE and ex[3]["intensityType"] == CW.INT_NONE
    assert ex[2]["intensityType"] == CW.INT_HR                                  # rest ≤ AeT
    assert CW.session_workout(_cp_sess("standard"), TH).payload["estimatedTime"] == 70 * 60


def test_cp_test_race_is_not_pushed():
    s = {**sess("test", "test", "CP 測試：5–10 K 比賽或計時跑", 45, "2026-10-01"), "protocol": "race"}
    with pytest.raises(CW.Unsupported, match="比賽"):
        CW.session_workout(s, TH)


def test_push_quick_cp_test_to_the_fake_hub():
    """The quick test through push_sessions against the mocked Training Hub:
    one program, 37 min, the 20′ bout open."""
    db = run(make_db())
    fake = FakeHub()
    res = _push(db, plan([_cp_sess("quick")]), fake)
    assert [r["status"] for r in res["sessions"]] == ["pushed"]
    (e,) = fake.entities
    prog = fake.programs[e["program"]["id"]]
    assert prog["estimatedTime"] == 37 * 60 and prog["name"].startswith("TRC CP 測試 20 分全力")
    work = [x for x in prog["exercises"] if x["exerciseType"] == CW.EX_TRAIN]
    assert len(work) == 1 and work[0]["intensityType"] == CW.INT_NONE and work[0]["targetValue"] == 1200


def test_strides_repeat():
    s = sess("easy1", "easy", "輕鬆跑＋坡道衝刺 8×10 秒", 50, "2026-10-01")
    ex = _flat(CW.session_workout(s, TH).payload)
    assert ex[0]["targetValue"] == 50 * 60 - 8 * 70
    assert ex[1]["isGroup"] and ex[1]["sets"] == 8
    assert ex[2]["targetValue"] == 10 and ex[3]["targetValue"] == 60
    assert CW.session_workout(s, TH).payload["estimatedTime"] == 50 * 60


def test_not_pushed_kinds():
    for s, why in [(sess("strength1", "strength", "肌力", 35, "2026-10-02"), "肌力"),
                   (sess("race", "race", "比賽", 0, "2026-10-04"), "比賽"),
                   (sess("easy1", "easy", "輕鬆跑", 40, "2026-10-01", done=True), "已完成"),
                   (sess("easy3", "easy", "輕鬆跑", 40, None), "排不進去")]:
        with pytest.raises(CW.Unsupported, match=why):
            CW.session_workout(s, TH)
    with pytest.raises(CW.Unsupported, match="已過"):
        CW.session_workout(sess("easy1", "easy", "輕鬆跑", 40, "2026-09-29"), TH, today="2026-09-30")


def test_no_thresholds_means_open_intensity():
    spec = CW.session_workout(sess("quality", "quality", "閾值 3×10 分", 60, "2026-10-01"), {})
    assert all(e["intensityType"] == CW.INT_NONE for e in spec.payload["exercises"])


def test_fingerprint_tracks_content_and_day():
    a = CW.session_workout(sess("easy1", "easy", "輕鬆跑", 45, "2026-10-02"), TH)
    b = CW.session_workout(sess("easy1", "easy", "輕鬆跑", 45, "2026-10-02"), TH)
    c = CW.session_workout(sess("easy1", "easy", "輕鬆跑", 50, "2026-10-02"), TH)
    d = CW.session_workout(sess("easy1", "easy", "輕鬆跑", 45, "2026-10-03"), TH)
    assert a.fingerprint == b.fingerprint
    assert len({a.fingerprint, c.fingerprint, d.fingerprint}) == 3


# ---------------------------------------------------------------------------
# push / idempotency
# ---------------------------------------------------------------------------

# push_week / remove_week / week_status are gone (production pushes stored
# sessions through push_sessions / remove_keys / status_of, api/plan_sessions);
# these helpers drive the same live functions with a hand-built week.

def _today_of(p):
    return max(p["week"].get("today") or "", CW.real_today().isoformat())


def _keyed(p):
    ws = p["week"]["start"]
    return [{**s, "key": f"{ws}/{s['id']}", "week_start": ws} for s in p["sessions"]]


async def _push_plan(db, p, only=None):
    sessions = _keyed(p)
    stale = []
    if only is not None:
        sessions = [s for s in sessions if s["id"] == only]
    else:
        live = {s["key"] for s in sessions}
        stale = [k for k in await CW.all_rows(db) if k not in live]
    return await CW.push_sessions(db, sessions, p.get("thresholds"), _today_of(p), stale_keys=stale)


def _push(db, p, fake, **kw):
    with http.use_transport(httpx.MockTransport(fake)):
        return run(_push_plan(db, p, **kw))


def _status(db, p):
    rows = run(CW.all_rows(db))
    return {s["id"]: CW.status_of(s, p.get("thresholds"), rows.get(s["key"]), _today_of(p))["status"]
            for s in _keyed(p)}


def _remove(db, only=None):
    rows = run(CW.all_rows(db))
    return run(CW.remove_keys(db, [k for k, r in rows.items() if only is None or r.session_id == only]))


def test_push_week_creates_and_schedules():
    db = run(make_db())
    fake = FakeHub()
    res = _push(db, week1(), fake)
    st = {r["id"]: r["status"] for r in res["sessions"]}
    assert st == {"long": "pushed", "quality": "pushed", "easy1": "pushed",
                  "strength1": "skipped", "easy2": "done"}
    assert len(fake.live()) == 3
    days = sorted(e["happenDay"] for e in fake.entities)
    assert days == [20261001, 20261002, 20261003]
    # each scheduled program is the detail of what was added, with its idInPlan
    for e in fake.entities:
        assert e["program"]["idInPlan"] == int(e["idInPlan"])
        assert e["program"]["id"] in fake.programs
    assert len({e["idInPlan"] for e in fake.entities}) == 3
    rows = run(db.execute(select(CorosPlanPush))).scalars().all()
    assert {r.session_key for r in rows} == {"2026-09-28/long", "2026-09-28/quality", "2026-09-28/easy1"}
    assert all(r.status == "pushed" and r.program_id and r.id_in_plan and r.plan_id == "plan9" for r in rows)


def test_push_twice_is_idempotent():
    db = run(make_db())
    fake = FakeHub()
    _push(db, week1(), fake)
    n_calls = len(fake.calls)
    res = _push(db, week1(), fake)
    assert fake.adds() == 3
    assert len(fake.calls) == n_calls                   # nothing sent at all
    assert {r["id"]: r.get("changed") for r in res["sessions"] if r["status"] == "pushed"} == \
        {"long": False, "quality": False, "easy1": False}
    assert len(fake.entities) == 3 and len(fake.live()) == 3


def test_changed_session_is_replaced_not_duplicated():
    db = run(make_db())
    fake = FakeHub()
    _push(db, week1(), fake)
    old = {e["happenDay"]: e["program"]["id"] for e in fake.entities}
    p = week1()
    p["sessions"][3]["day"] = "2026-10-04"              # easy1 moved Fri -> Sun
    p["sessions"][3]["minutes"] = 50
    res = _push(db, p, fake)
    st = {r["id"]: r["status"] for r in res["sessions"]}
    assert st["easy1"] == "updated" and st["long"] == "pushed"
    assert fake.programs[old[20261002]]["deleted"] == 1
    assert sorted(e["happenDay"] for e in fake.entities) == [20261001, 20261003, 20261004]
    assert len(fake.live()) == 3
    row = run(db.execute(select(CorosPlanPush).where(CorosPlanPush.session_id == "easy1"))).scalar_one()
    assert row.day == "2026-10-04"
    assert fake.programs[row.program_id]["estimatedTime"] == 50 * 60


def test_session_dropped_from_plan_is_removed():
    db = run(make_db())
    fake = FakeHub()
    _push(db, week1(), fake)
    p = week1()
    p["sessions"] = [s for s in p["sessions"] if s["id"] != "quality"]
    res = _push(db, p, fake)
    assert [r["id"] for r in res["removed"]] == ["quality"]
    assert res["removed"][0]["status"] == "removed"
    assert 20261001 not in {e["happenDay"] for e in fake.entities}
    assert len(fake.live()) == 2
    keys = {r.session_id for r in run(db.execute(select(CorosPlanPush))).scalars().all()}
    assert keys == {"long", "easy1"}


def test_unplaced_session_is_taken_off_coros():
    db = run(make_db())
    fake = FakeHub()
    _push(db, week1(), fake)
    p = week1()
    p["sessions"][3]["day"] = None
    res = _push(db, p, fake)
    r = next(r for r in res["sessions"] if r["id"] == "easy1")
    assert r["status"] == "removed"
    assert len(fake.live()) == 2 and len(fake.entities) == 2


def test_done_session_keeps_its_coros_entry():
    db = run(make_db())
    fake = FakeHub()
    _push(db, week1(), fake)
    p = week1()
    p["sessions"][3]["done"] = True
    res = _push(db, p, fake)
    assert next(r for r in res["sessions"] if r["id"] == "easy1")["status"] == "done"
    assert len(fake.live()) == 3 and len(fake.entities) == 3


def test_push_one_session_only():
    db = run(make_db())
    fake = FakeHub()
    res = _push(db, week1(), fake, only="long")
    assert [r["id"] for r in res["sessions"]] == ["long"] and res["removed"] == []
    assert len(fake.live()) == 1


def test_user_deleted_it_in_coros_app():
    """Replacing a workout that is already gone from COROS must not get stuck."""
    db = run(make_db())
    fake = FakeHub()
    _push(db, week1(), fake, only="easy1")
    pid = next(iter(fake.live()))
    fake.programs[pid]["deleted"] = 1
    fake.entities.clear()
    p = week1()
    p["sessions"][3]["minutes"] = 30
    res = _push(db, p, fake, only="easy1")
    assert res["sessions"][0]["status"] == "updated"
    assert ("POST", "/training/program/delete") not in fake.calls
    assert len(fake.live()) == 1 and len(fake.entities) == 1


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

def test_add_failure_is_recorded_and_retried():
    db = run(make_db())
    fake = FakeHub()
    fake.fail["/training/program/add"] = {"result": "5001", "message": "intensity not supported"}
    res = _push(db, week1(), fake)
    st = {r["id"]: r for r in res["sessions"]}
    failed = [k for k, r in st.items() if r["status"] == "failed"]
    assert len(failed) == 1 and "intensity not supported" in st[failed[0]]["error"]
    assert sum(1 for r in st.values() if r["status"] == "pushed") == 2
    # status endpoint says failed; the next push retries just that one
    assert _status(db, week1())[failed[0]] == "failed"
    res = _push(db, week1(), fake)
    assert {r["id"]: r["status"] for r in res["sessions"]}[failed[0]] == "pushed"
    assert len(fake.live()) == 3 and len(fake.entities) == 3


def test_schedule_failure_leaves_no_orphan():
    db = run(make_db())
    fake = FakeHub()
    fake.fail["/training/schedule/update"] = {"result": "9999", "message": "server busy"}
    res = _push(db, week1(), fake, only="long")
    assert res["sessions"][0]["status"] == "failed"
    row = run(db.execute(select(CorosPlanPush))).scalar_one()
    first = row.program_id
    assert row.status == "failed" and first in fake.live()     # created, recorded, not scheduled
    res = _push(db, week1(), fake, only="long")
    assert res["sessions"][0]["status"] == "updated"
    assert fake.programs[first]["deleted"] == 1                 # the half-done one is cleaned up
    assert len(fake.live()) == 1 and len(fake.entities) == 1


def test_not_logged_in():
    db = run(make_db(logged_in=False))
    with pytest.raises(CW.CorosAuthError):
        _push(db, week1(), FakeHub())


def test_http_401_is_auth_error_and_recorded():
    db = run(make_db())
    fake = FakeHub()
    fake.status_override["/training/program/add"] = 401
    with pytest.raises(CW.CorosAuthError):
        _push(db, week1(), fake, only="long")


def test_network_error_marks_failed():
    db = run(make_db())

    def boom(req):
        raise httpx.ConnectError("down")
    res = _push(db, week1(), boom, only="long")
    assert res["sessions"][0]["status"] == "failed" and "ConnectError" in res["sessions"][0]["error"]


# ---------------------------------------------------------------------------
# remove + status
# ---------------------------------------------------------------------------

def test_remove_and_status():
    db = run(make_db())
    fake = FakeHub()
    _push(db, week1(), fake)
    assert _status(db, week1()) == {"long": "pushed", "quality": "pushed", "easy1": "pushed",
                                    "strength1": "skipped", "easy2": "done"}
    p = week1()
    p["sessions"][0]["minutes"] = 90
    assert _status(db, p)["long"] == "outdated"
    with http.use_transport(httpx.MockTransport(fake)):
        one = _remove(db, only="quality")
        assert [r["status"] for r in one] == ["removed"]
        rest = _remove(db)
    assert sorted(r["id"] for r in rest) == ["easy1", "long"]
    assert fake.live() == {} and fake.entities == []
    assert run(db.execute(select(CorosPlanPush))).scalars().all() == []
    assert _status(db, week1())["long"] == "not_pushed"


def test_never_touches_workouts_it_did_not_create():
    db = run(make_db())
    fake = FakeHub()
    fake.programs["111"] = {"id": "111", "name": "user's own", "deleted": 0}
    fake.entities.append({"happenDay": 20261001, "idInPlan": "3", "planProgramId": "3",
                          "planId": "plan9", "sortNoInSchedule": 1, "executeStatus": 0})
    _push(db, week1(), fake)
    with http.use_transport(httpx.MockTransport(fake)):
        _remove(db)
    assert fake.programs["111"]["deleted"] == 0
    assert [e["idInPlan"] for e in fake.entities] == ["3"]


# ---------------------------------------------------------------------------
# never undo what the athlete may have done
# ---------------------------------------------------------------------------

def test_past_session_dropped_from_plan_is_kept(monkeypatch):
    """Mid-week the plan regenerates (e.g. the CP test disappears once done):
    a pushed session on a past day must stay on the COROS calendar."""
    db = run(make_db())
    fake = FakeHub()
    _push(db, week1(), fake)                                   # quality on 10/1
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 2))
    p = week1()
    p["week"]["today"] = "2026-10-02"
    p["sessions"] = [s for s in p["sessions"] if s["id"] != "quality"]
    res = _push(db, p, fake)
    assert res["removed"] == [{"id": "quality", "title": "閾值 3×10 分", "day": "2026-10-01",
                               "status": "kept", "reason": "日期已過，保留在 COROS"}]
    assert 20261001 in {e["happenDay"] for e in fake.entities}
    assert len(fake.live()) == 3


def test_executed_entry_is_never_removed_or_replaced():
    db = run(make_db())
    fake = FakeHub()
    _push(db, week1(), fake, only="easy1")
    fake.entities[0]["executeStatus"] = 1                      # done on the watch, not synced here yet
    p = week1()
    p["sessions"][3]["minutes"] = 30
    res = _push(db, p, fake, only="easy1")
    assert res["sessions"][0]["status"] == "done"
    assert fake.adds() == 1 and len(fake.entities) == 1
    with http.use_transport(httpx.MockTransport(fake)):
        out = _remove(db)
    assert out[0]["status"] == "kept"
    assert len(fake.entities) == 1 and len(fake.live()) == 1


def test_stale_data_never_schedules_in_the_past(monkeypatch):
    """week.today is the last day with data; the real date wins when later."""
    monkeypatch.setattr(CW, "real_today", lambda: date(2026, 10, 3))
    db = run(make_db())
    fake = FakeHub()
    res = _push(db, week1(), fake)
    st = {r["id"]: r["status"] for r in res["sessions"]}
    assert st["long"] == "pushed" and st["quality"] == "skipped" and st["easy1"] == "skipped"
    assert [e["happenDay"] for e in fake.entities] == [20261003]


def test_entry_moved_in_the_app_is_still_found():
    db = run(make_db())
    fake = FakeHub()
    _push(db, week1(), fake, only="easy1")
    fake.entities[0]["happenDay"] = 20261004                   # dragged to Sunday in the COROS app
    with http.use_transport(httpx.MockTransport(fake)):
        _remove(db)
    assert fake.entities == [] and fake.live() == {}
