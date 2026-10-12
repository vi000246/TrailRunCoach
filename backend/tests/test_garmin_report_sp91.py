"""SP-91 part D: the Garmin trial's event log (sync/events.py, source "garmin") holds every login,
sync result, MFA step, 429 / 403, pause, recovery and breaker with the garminconnect version and
nothing personal; backend/scripts/garmin_trial_report.py answers the trial's questions from it."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from backend.scripts import garmin_trial_report as R
from backend.sync import events as EV, garmin_client as GC, garmin_limits as GL
from backend.tests.fit_builder import build_run
from backend.tests.garmin_fakes import FakeGarminServer, activity, zip_of
from backend.tests.test_sync_e2e import START, collect, make_session, run

EMAIL, PW = "secret-person@example.com", "pw-not-in-the-log"
T0 = datetime(2026, 10, 12, 6, 0).timestamp()
H = 3600.0


def _ev(at, event, **kw):
    return {"at": at, "source": "garmin", "event": event, **kw}


def _log():
    """Three accounts over three days: u2 syncs fine; u3 is locked (429) for 50 h; on day 2 Garmin
    changes something and every sync fails until the package update (0.3.17 -> 0.3.18) 30 h later."""
    return [
        _ev(T0, "login_ok", tenant="u2", lib="0.3.17"),
        _ev(T0 + 60, "sync_ok", tenant="u2", trigger="manual", secs=20.0, downloaded=12, requests_today=14, lib="0.3.17"),
        _ev(T0 + 120, "mfa_required", tenant="u3"),
        _ev(T0 + 130, "mfa_wrong", tenant="u3", tries=1),
        _ev(T0 + 140, "mfa_ok", tenant="u3"),
        _ev(T0 + 141, "login_ok", tenant="u3", lib="0.3.17"),
        _ev(T0 + 200, "http_429", tenant="u3", login=False, lib="0.3.17"),
        _ev(T0 + 200, "account_paused", tenant="u3", reason="HTTP 429", status=429),
        _ev(T0 + 201, "sync_failed", tenant="u3", trigger="manual", secs=3.0, error="GARMIN_RATE_LIMITED", lib="0.3.17"),
        _ev(T0 + 300, "sync_skipped", tenant="u3", code="GARMIN_PAUSED", trigger="manual"),
        _ev(T0 + 24 * H, "sync_ok", tenant="u2", trigger="schedule", secs=8.0, downloaded=1, requests_today=3, lib="0.3.17"),
        _ev(T0 + 26 * H, "list_failed", tenant="u2", error="GarminConnectConnectionError", status=500, lib="0.3.17"),
        _ev(T0 + 26 * H + 1, "sync_failed", tenant="u2", trigger="manual", secs=2.0, error="GARMIN_API_ERROR", lib="0.3.17"),
        _ev(T0 + 27 * H, "login_failed", tenant="u4", kind="error", error="GarminConnectConnectionError", lib="0.3.17"),
        _ev(T0 + 50 * H, "account_recovered", tenant="u3", locked_s=int(50 * H)),
        _ev(T0 + 56 * H, "sync_ok", tenant="u2", trigger="schedule", secs=30.0, downloaded=2, requests_today=5, lib="0.3.18"),
        _ev(T0 + 57 * H, "breaker_tripped", accounts=3, lib="0.3.18"),
    ]


def test_the_report_answers_the_trials_questions(tmp_path):
    p = tmp_path / "sync_events.jsonl"
    rows = _log() + [{"at": T0, "source": "coros", "event": "sync_ok", "tenant": "u2"}]     # other source: out
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", "utf-8")
    evs = R.read_events([p.with_name(p.name + ".1"), p])
    rep = R.report(evs, now=T0 + 60 * H, paused_now={"u5": {"at": T0}})
    s = rep["sync"]
    # 1. success rate and run time
    assert s["runs"] == 5 and s["by_outcome"] == {"ok": 3, "failed": 2} and s["success_rate"] == 0.6
    assert s["run_s"]["median"] == 8.0 and s["run_s"]["max"] == 30.0
    assert s["skipped"] == {"GARMIN_PAUSED": 1} and s["downloaded"] == 15
    # 2. one breakage (not the 429): from the first non-block failure to the next success, fixed by 0.3.18
    assert len(rep["outages"]) == 1
    o = rep["outages"][0]
    assert o["hours"] == 30.0 and o["accounts"] == 2 and o["failures"] == 3
    assert o["lib"] == "0.3.17" and o["fixed_by"] == "0.3.18" and o["package_updated"] is True
    assert [v["version"] for v in rep["versions"]] == ["0.3.17", "0.3.18"]
    # 3. locked accounts vs users, how long the lock lasted
    lk = rep["locks"]
    assert lk["users"] == 2 and lk["locked_accounts"] == 1 and lk["share_locked"] == 0.5
    assert lk["http_429"] == 1 and lk["lock_hours"] == {"n": 1, "median": 50.0, "max": 50.0}
    assert lk["still_paused"] == ["u5"] and len(lk["breaker_trips"]) == 1
    assert (lk["mfa_required"], lk["mfa_wrong"]) == (1, 1) and lk["login_failed"] == {"error": 1}
    # 4. frequency: locked vs not, per day
    fq = rep["frequency"]
    assert fq["locked"]["accounts"] == 1 and fq["not_locked"]["accounts"] == 1
    assert fq["not_locked"]["requests_per_day"]["max"] == 14
    assert [d["blocked"] for d in fq["per_day"]][0] == 1
    txt = R.text(rep)
    for want in ("同步成功率與延遲", "斷線", "更新套件後恢復", "被鎖的帳號", "同步頻率", "0.3.18"):
        assert want in txt


def test_an_outage_still_going_is_reported_open():
    evs = [_ev(T0, "sync_ok", tenant="u2", lib="0.3.17"),
           _ev(T0 + H, "sync_failed", tenant="u2", error="GARMIN_API_ERROR", lib="0.3.17"),
           _ev(T0 + 2 * H, "sync_failed", tenant="u2", error="GARMIN_RATE_LIMITED", lib="0.3.17")]
    rep = R.report(evs, now=T0 + 5 * H)
    assert len(rep["outages"]) == 1 and rep["outages"][0]["ongoing"] is True
    assert rep["outages"][0]["hours"] == 4.0 and rep["outages"][0]["failures"] == 1     # the 429 is no breakage


def test_the_cli_prints_the_report_and_flips_the_switch(tmp_path, capsys):
    p = tmp_path / "ev.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in _log()), "utf-8")
    assert R.main(["--log", str(p)]) == 0
    assert "Garmin 試運行紀錄" in capsys.readouterr().out
    assert R.main(["--log", str(p), "--json", "--since", "2026-10-13"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["from"] == "2026-10-13"
    assert R.main(["--switch", "off"]) == 0 and not GL.enabled()
    assert R.main(["--switch", "on"]) == 0 and GL.enabled()
    assert [e["event"] for e in EV.read("garmin")][-2:] == ["disabled", "enabled"]


def test_a_real_run_logs_results_blocks_and_the_version_but_nothing_personal(tmp_path, monkeypatch):
    from backend.sync import runner
    server = FakeGarminServer(accounts={EMAIL: {"password": PW, "mfa": "424242"}})
    monkeypatch.setattr(GC, "_LIB", server.module())
    monkeypatch.setattr(GL, "lib_version", lambda: "0.3.17")
    server.activities.append(activity(9001, START))
    server.zips[9001] = zip_of(build_run(START))

    async def go():
        s = await make_session(tmp_path)
        assert (await GC.login(EMAIL, PW, s, 1, remember=True))["mfa_required"]
        with pytest.raises(GC.GarminLoginFailed):
            await GC.login_mfa("000000", s, 1)
        await GC.login_mfa("424242", s, 1)
        await collect(runner.stream(s, "garmin", 1, trigger="manual"))
        server.fail["list"] = 429
        await collect(runner.stream(s, "garmin", 1, trigger="manual"))
        await collect(runner.stream(s, "garmin", 1, trigger="manual"))       # paused: skipped
    run(go())
    evs = EV.read("garmin")
    names = [e["event"] for e in evs]
    for want in ("mfa_required", "mfa_wrong", "mfa_ok", "login_ok", "sync_ok", "http_429", "account_paused",
                 "sync_failed", "sync_skipped"):
        assert want in names, want
    ok = next(e for e in evs if e["event"] == "sync_ok")
    assert ok["lib"] == "0.3.17" and ok["downloaded"] == 1 and "secs" in ok and ok["tenant"] == "owner"
    raw = EV.path().read_text("utf-8")
    for bad in (EMAIL, PW, "SECRET", "di-token"):
        assert bad not in raw
    rep = R.report(R.read_events([EV.path()]))
    assert rep["sync"]["by_outcome"] == {"ok": 1, "failed": 1} and rep["locks"]["locked_accounts"] == 1
