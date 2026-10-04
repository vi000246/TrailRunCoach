"""
SP-37 COROS TL probe (backend/scripts/probe_coros_tl.py) and the TSS -> TL
fit (backend/scripts/fit_tss_tl.py) on synthetic data. The COROS side is a
fake (httpx.MockTransport): no network, no account, no token.
"""
import asyncio
import csv
import datetime as dt
import json
import math
import random
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from backend.scripts import fit_tss_tl as F
from backend.scripts import probe_coros_tl as P
from backend.sync import http

BASE = "https://teamapi.coros.com"


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# probe: field selection
# ---------------------------------------------------------------------------

def test_load_like_names():
    for n in ("trainingLoad", "aerobicEffect", "anaerobicEffect", "tl", "total_training_effect",
              "trainingLoadRatio", "TSS", "recoveryTime"):
        assert P.is_load_like(n), n
    for n in ("labelId", "name", "userId", "startLatitude", "distance", "avgHr", "deviceSn", "loadId"):
        assert not P.is_load_like(n), n


def test_load_fields_nested_numeric_only_no_identity():
    obj = {"labelId": "4711", "name": "Morning run", "trainingLoad": 87, "aerobicEffect": "3.4",
           "summary": {"trainingLoad": 88.0, "userId": 123456789, "loadName": "x", "anaerobicEffect": None},
           "lapList": [{"trainingLoad": 10}, {"trainingLoad": 20}, {"trainingLoad": 30}],
           "123456": {"trainingLoad": 5}, "flag": True, "tlText": "high"}
    got = P.load_fields(obj)
    assert got == {"trainingLoad": 87.0, "aerobicEffect": 3.4, "summary.trainingLoad": 88.0,
                   "lapList[0].trainingLoad": 10.0, "lapList[1].trainingLoad": 20.0}


def test_key_paths_names_only():
    ks = P.key_paths({"a": 1, "b": {"c": "secret", "999": 2}, "l": [{"d": 1}]})
    assert ks == {"a", "b", "b.c", "b.<n>", "l", "l[].d"}
    assert not any("secret" in k for k in ks)


def test_label_and_day_helpers():
    assert P.label_of_file("471234567890_2026-09-01_trail_run.fit") == "471234567890"
    assert P.label_of_file("Morning_run.fit") is None
    assert P.coros_day(20260901) == "2026-09-01"
    assert P.coros_day("2026") is None


def test_zone_seconds_lthr_zones_and_coverage():
    edges = P.zone_edges(170, (0.80, 0.90, 0.95, 1.02, 1.06))
    assert edges == [136, 153, 162, 173, 180]
    hr = [120, 140, 155, 165, 175, 185, None, float("nan")]
    dts = [1, 1, 1, 1, 1, 1, 1, 1]
    zs, cov, avg = P.zone_seconds(hr, dts, edges)
    assert zs == [1, 1, 1, 1, 1, 1]
    assert cov == pytest.approx(6 / 8)
    assert avg == pytest.approx(sum(hr[:6]) / 6)
    # a long gap (pause) counts DT_CAP_S, not its length
    zs, cov, _ = P.zone_seconds([150, 150], [1, 600], edges)
    assert zs[1] == 1 + P.DT_CAP_S


def test_fit_candidates_session_dev_unknown_and_identity_filter():
    msgs = [("file_id", [("serial_number", 3999999999, False), ("unknown_9", 5, False)]),
            ("record", [("heart_rate", 150, False)]), ("record", []),
            ("session", [("total_training_effect", 3.1, False), ("unknown_200", 95, False),
                         ("unknown_201", 1690000000, False), ("total_distance", 10000.0, False),
                         ("Form Power", 60, True), ("sport", "running", False)]),
            ("unknown_312", [("unknown_3", 88, False)]),
            ("lap", [("unknown_1", 1, False)]), ("lap", [("unknown_1", 2, False)])]
    got, counts = P.fit_candidates(msgs)
    assert got == {"fit.session.total_training_effect": 3.1, "fit.session.unknown_200": 95.0,
                   "fit.session.Form Power": 60.0, "fit.unknown_312.unknown_3": 88.0}
    assert counts["record"] == 2 and counts["lap"] == 2


def test_fit_messages_reads_a_synthetic_fit(tmp_path):
    from backend.tests.fit_builder import build_run
    p = tmp_path / "123_2026-01-01_run.fit"
    p.write_bytes(build_run(dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), seconds=30))
    names = [m[0] for m in P.fit_messages(p)]
    assert "session" in names and names.count("record") == 30
    assert P.fit_messages(tmp_path / "missing.fit") == []


def test_exercise_summary_keeps_structure_only():
    prog = {"name": "my secret workout", "exercises": [
        {"name": "warm", "overview": "note", "exerciseType": 1, "targetType": 2, "targetValue": 600,
         "intensityType": 2, "intensityValue": 140, "intensityValueExtend": 150}]}
    s = P.exercise_summary(prog)
    assert json.loads(s) == [{"exerciseType": 1, "targetType": 2, "targetValue": 600, "intensityType": 2,
                              "intensityValue": 140, "intensityValueExtend": 150}]
    assert "secret" not in s and "note" not in s


def test_summarize_first_look_and_fit_match():
    rows = []
    for i in range(12):
        tss = 30 + 10 * i
        rows.append({"tss": tss, "tss_source": "hrtss", "hrtss": tss, "list.trainingLoad": 1.2 * tss,
                     "fit.session.unknown_200": 1.2 * tss + 1, "fit.session.unknown_5": (i * 7) % 3})
    s = P.summarize(rows)
    assert s["tl_columns"] == ["list.trainingLoad"]
    fl = s["first_look"]["list.trainingLoad"]
    assert fl["hrtss"]["n"] == 12 and fl["hrtss"]["r"] == pytest.approx(1.0)
    assert fl["hrtss"]["median_tl_per_tss"] == pytest.approx(1.2)
    assert [m["field"] for m in s["fit_fields_like_tl"]] == ["fit.session.unknown_200"]


def test_write_csv_fixed_then_extra_columns(tmp_path):
    p = tmp_path / "x" / "a.csv"
    head = P.write_csv(p, [{"n": 1, "date": "2026-01-01", "list.trainingLoad": 50, "tss": None}], P.APP_COLS)
    assert head[:len(P.APP_COLS)] == list(P.APP_COLS) and head[-1] == "list.trainingLoad"
    rows = list(csv.DictReader(open(p, encoding="utf-8")))
    assert rows[0]["list.trainingLoad"] == "50" and rows[0]["tss"] == ""


# ---------------------------------------------------------------------------
# probe: COROS reads (fake server)
# ---------------------------------------------------------------------------

class FakeCoros:
    def __init__(self, detail_needs_post=False, invalid=False):
        self.calls = []
        self.detail_needs_post = detail_needs_post
        self.invalid = invalid

    def ok(self, data):
        return httpx.Response(200, json={"result": "0000", "message": "OK", "data": data})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        q = parse_qs(urlparse(str(req.url)).query)
        self.calls.append((req.method, path))
        assert req.headers.get("accessToken") == "tok"
        if self.invalid:
            return httpx.Response(200, json={"result": "1019", "message": "Access token is invalid"})
        if path == "/activity/query":
            page = int(q["pageNumber"][0])
            items = [{"labelId": str(4700 + i), "name": f"Run {i}", "date": 20260901 + i, "sportType": 100,
                      "trainingLoad": 40 + i, "totalTime": 3600, "distance": 10000, "startLat": 25.0}
                     for i in range(3)] if page == 1 else []
            return self.ok({"dataList": items})
        if path == "/activity/detail/query":
            if self.detail_needs_post and req.method != "POST":
                return httpx.Response(200, json={"result": "5001", "message": "method"})
            return self.ok({"summary": {"trainingLoad": 41, "aerobicEffect": 3.0, "name": "x"}})
        if path == "/training/schedule/query":
            return self.ok({"entities": [{"idInPlan": "8", "happenDay": 20260910, "executeStatus": 0},
                                         {"idInPlan": "9", "happenDay": 20260911, "executeStatus": 0}],
                            "programs": [{"idInPlan": "8", "name": "TRC easy", "sportType": 1, "trainingLoad": 0,
                                          "estimatedTime": 3600, "exercises": [{"exerciseType": 2}]},
                                         {"idInPlan": "9", "name": "hand made", "sportType": 1,
                                          "trainingLoad": 77, "estimatedTime": 2700}]})
        if path == "/training/program/detail":
            return self.ok({"id": q["id"][0], "trainingLoad": 55, "sportType": 1, "estimatedTime": 3600})
        if path == "/training/program/query":
            return self.ok([{"name": "lib", "trainingLoad": 66, "sportType": 1}])
        raise AssertionError(f"unexpected {req.method} {path}")


PUSHED = [{"session_key": "s1", "day": "2026-09-10", "program_id": "p1", "id_in_plan": "8",
           "kind": "z2", "minutes": 60, "tss": 50.0}]


def _probe(fake, allow_post=False):
    api = P.ReadOnlyCoros("tok", BASE, "u1", allow_post=allow_post, pause_s=0)
    with http.use_transport(httpx.MockTransport(fake)):
        res = run(P.probe_remote(api, dt.date(2026, 9, 1), dt.date(2026, 9, 30), 2,
                                 dt.date(2026, 9, 10), dt.date(2026, 9, 20), PUSHED))
    return api, res


def test_probe_remote_reads_only_with_get():
    fake = FakeCoros()
    api, res = _probe(fake)
    assert {m for m, _ in fake.calls} == {"GET"}
    assert {p for _, p in fake.calls} <= set(P.READ_GET)
    acts = res["activities"]
    assert acts["4702"]["list.trainingLoad"] == 42 and acts["4702"]["date"] == "2026-09-03"
    assert acts["4702"]["detail.summary.trainingLoad"] == 41          # 2 most recent have detail
    assert "detail.summary.trainingLoad" not in acts["4700"]
    for r in acts.values():
        assert not any("name" in k.lower() or "lat" in k.lower() for k in r)
    pl = {r["origin"]: r for r in res["planned"]}
    assert pl["app_push"]["tl_schedule"] == 0 and pl["app_push"]["tl_detail"] == 55
    assert pl["app_push"]["app_planned_tss"] == 50.0
    assert pl["calendar"]["tl_schedule"] == 77 and pl["calendar"]["date"] == "2026-09-11"
    assert "name" not in json.dumps(res["planned"]).replace("app_", "")
    assert "list_item" in res["inventory"] and "name" in res["inventory"]["list_item"]   # names, never values
    assert "Run 0" not in json.dumps(res)


def test_detail_needs_post_only_with_flag():
    fake = FakeCoros(detail_needs_post=True)
    _, res = _probe(fake)
    assert not any(k.startswith("detail.") for r in res["activities"].values() for k in r)
    assert any("--allow-post-reads" in n for n in res["notes"])
    assert ("POST", "/activity/detail/query") not in fake.calls

    fake = FakeCoros(detail_needs_post=True)
    _, res = _probe(fake, allow_post=True)
    assert res["activities"]["4702"]["detail.summary.trainingLoad"] == 41
    assert {(m, p) for m, p in fake.calls if m == "POST"} == {("POST", "/activity/detail/query"),
                                                              ("POST", "/training/program/query")}
    assert any(r["origin"] == "library" and r["tl_schedule"] == 66 for r in res["planned"])


def test_invalid_token_stops():
    with pytest.raises(P.TokenRejected):
        _probe(FakeCoros(invalid=True))


def test_read_only_client_refuses_writes():
    api = P.ReadOnlyCoros("tok", BASE, "u1", allow_post=True, pause_s=0)
    for m, p in (("POST", "/training/program/add"), ("POST", "/training/schedule/update"),
                 ("POST", "/training/program/delete"), ("GET", "/account/login"), ("DELETE", "/activity/query"),
                 ("POST", "/activity/query")):
        with pytest.raises(PermissionError):
            run(api.read(m, p))
    no_post = P.ReadOnlyCoros("tok", BASE, "u1", pause_s=0)
    with pytest.raises(PermissionError):
        run(no_post.read("POST", "/activity/detail/query"))
    assert "tok" not in repr(api)


def test_dry_run_calls_nothing(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_fit_index", lambda: {})
    assert P.main(["--dry-run", "--out", str(tmp_path / "o")]) == 0
    out = capsys.readouterr().out
    assert "GET  /activity/query" in out and "POST" not in out
    assert not (tmp_path / "o").exists()


# ---------------------------------------------------------------------------
# fit_tss_tl
# ---------------------------------------------------------------------------

def _s(x, y, h=1.0, iff=0.8, zmin=None):
    return {"x": x, "y": y, "h": h, "if": iff, "zmin": zmin}


def test_models_recover_parameters():
    s = [_s(x, 1.3 * x) for x in range(20, 200, 15)]
    assert F.fit("A", s)["a"] == pytest.approx(1.3)
    s = [_s(x, 0.9 * x + 12) for x in range(20, 200, 15)]
    p = F.fit("B", s)
    assert (p["a"], p["b"]) == (pytest.approx(0.9), pytest.approx(12))
    s = [_s(x, 2.0 * x ** 0.85) for x in range(20, 200, 15)]
    p = F.fit("C", s)
    assert (p["a"], p["k"]) == (pytest.approx(2.0), pytest.approx(0.85))
    s = [_s(0, h * (10 + 20 * i + 90 * i * i), h=h, iff=i) for h in (0.5, 1, 2) for i in (0.6, 0.75, 0.9, 1.0)]
    p = F.fit("D", s)
    assert (p["c0"], p["c1"], p["c2"]) == (pytest.approx(10, abs=1e-6), pytest.approx(20, abs=1e-6),
                                           pytest.approx(90, abs=1e-6))
    w = [0.3, 0.8, 1.5, 2.5, 4.0, 0.0]
    rnd = random.Random(1)
    s = []
    for _ in range(25):
        z = [rnd.uniform(0, 40) for _ in range(5)] + [0.0]
        s.append(_s(0, sum(a * b for a, b in zip(w, z)), zmin=z))
    p = F.fit("E", s)
    assert [p[f"w{i}"] for i in range(1, 7)] == [pytest.approx(v, abs=1e-6) for v in w]


def test_nnls_never_negative():
    A = F.np.array([[1.0, 1.0], [2.0, 1.0], [3.0, 1.0], [4.0, 1.0]])
    y = F.np.array([1.0, 3.0, 5.0, 7.0])        # unconstrained: slope 2, intercept -1
    w = F._nnls(A, y)
    assert (w >= 0).all() and w[1] == 0


def test_loo_and_best_model_picks_the_true_form():
    rnd = random.Random(7)
    s = [_s(x, 2.0 * x ** 0.8 * (1 + rnd.uniform(-0.01, 0.01))) for x in range(20, 300, 10)]
    res = F.evaluate(s)
    assert res["C"]["loo"]["mape"] < 0.02
    assert F.best_model(res) == "C"
    assert res["E"]["params"] is None                # no zone minutes
    e = F.errors([(11.0, 10.0), (9.0, 10.0)])
    assert e == {"n": 2, "mae": 1.0, "mape": pytest.approx(0.1), "bias": 0.0}


def test_filters_groups_and_tl_column():
    base = {"tss": "50", "tss_source": "power", "if": "0.8", "hrtss": "60", "hrif": "0.85",
            "moving_s": "3600", "hr_coverage": "0.99", "app_lthr": "170", "coros_lthr": "172",
            "list.trainingLoad": "70", "detail.summary.trainingLoad": ""}
    assert F.keep_row(base, 10, 20, 0.9, None)
    assert not F.keep_row({**base, "moving_s": "300"}, 10, 20, 0.9, None)
    assert not F.keep_row({**base, "moving_s": str(30 * 3600)}, 10, 20, 0.9, None)
    assert not F.keep_row({**base, "hr_coverage": "0.5"}, 10, 20, 0.9, None)
    assert not F.keep_row(base, 10, 20, 0.9, 1)
    assert F.keep_row({**base, "hr_coverage": ""}, 10, 20, 0.9, None)
    assert F.pick_tl_column([base]) == "list.trainingLoad"
    assert F.pick_tl_column([base], "x.y") == "x.y"
    g = F.groups([base], "list.trainingLoad")
    assert set(g) == {"power", "hrtss(all)"}
    assert g["power"][0]["x"] == 50 and g["hrtss(all)"][0]["x"] == 60 and g["hrtss(all)"][0]["if"] == 0.85
    assert g["power"][0]["zmin"] is None


def test_planned_samples():
    rows = [{"tl_schedule": "0", "tl_detail": "55", "app_planned_tss": "50", "estimated_time_s": "3600"},
            {"tl_schedule": "0", "tl_detail": "", "app_planned_tss": "50", "estimated_time_s": "3600"},
            {"tl_schedule": "40", "tl_detail": "", "app_planned_tss": "", "estimated_time_s": "3600"}]
    ps = F.planned_samples(rows)
    assert len(ps) == 1 and ps[0]["y"] == 55 and ps[0]["if"] == pytest.approx(math.sqrt(0.5))


def test_end_to_end_csv(tmp_path, capsys):
    rnd = random.Random(3)
    rows = []
    for i in range(30):
        mins = rnd.uniform(30, 180)
        mix = [rnd.uniform(0, 1) for _ in range(4)]
        z = [mins * f / sum(mix) for f in mix] + [0.0, 0.0]
        tl = sum(w * m for w, m in zip((0.4, 0.9, 1.6, 2.6, 4.0, 5.0), z))
        hrtss = mins / 60 * 60
        rows.append({"n": i + 1, "date": f"2026-0{1 + i % 9}-1{i % 9}", "in_app": 1, "sport": "run",
                     "duration_s": mins * 60, "moving_s": mins * 60, "tss": hrtss, "tss_source": "hrtss",
                     "if": rnd.uniform(0.6, 0.95), "hrtss": hrtss, "hrif": 0.78, "hr_coverage": 0.99,
                     **{f"z{k + 1}_s": z[k] * 60 for k in range(6)}, "list.trainingLoad": round(tl, 3)})
    P.write_csv(tmp_path / "activities.csv", rows, P.APP_COLS)
    P.write_csv(tmp_path / "planned.csv", [{"n": 1, "tl_detail": 60, "app_planned_tss": 50,
                                            "estimated_time_s": 3600}], P.PLANNED_COLS)
    out = tmp_path / "fit.json"
    assert F.main([str(tmp_path / "activities.csv"), "--planned", str(tmp_path / "planned.csv"),
                   "--json", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["tl_column"] == "list.trainingLoad" and res["kept"] == 30
    assert res["groups"]["hrtss"]["best"] == "E"
    assert res["groups"]["hrtss"]["models"]["E"]["loo"]["mae"] < 0.01
    assert res["groups"]["hrtss"]["models"]["D"]["params"] is not None
    assert res["planned"]["n"] == 1
    assert "[hrtss]" in capsys.readouterr().out


def test_no_tl_column(tmp_path, capsys):
    P.write_csv(tmp_path / "a.csv", [{"n": 1, "tss": 50}], P.APP_COLS)
    assert F.main([str(tmp_path / "a.csv")]) == 2
    assert "no COROS TL column" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# probe main(): fake session, fake COROS, fake Dataset
# ---------------------------------------------------------------------------

class _Entry:
    def __init__(self, file):
        self.file = file


class _W:
    def __init__(self, idx, label, day, tss):
        self.idx, self.entry, self.day = idx, _Entry(f"2026/{label}_2026-09-0{idx + 1}_run.fit"), day
        self.sport, self.sport_type = "run", "trail running"
        self.metrics = {"duration": 3600.0, "movingduration": 3500.0, "distance": 10.0, "climbing": 300.0,
                        "tss": tss, "tss_source": "hrtss", "if": 0.8, "hrtss": tss, "hrif": 0.8}


class _DS:
    def __init__(self):
        from backend.engine.wko5expr.dataset import date_to_day
        self.workouts = [_W(i, str(4700 + i), date_to_day(dt.date(2026, 9, 1 + i)), 50.0 + i) for i in range(3)]

    def sport_setting(self, kind, w):
        return 170.0

    def channel(self, idx, name):
        import numpy as np
        return np.full(600, 150.0) if name == "heartrate" else np.ones(600)


def test_main_writes_anonymous_csv(tmp_path, monkeypatch, capsys):
    import backend.api.wko5views as V

    async def fake_session(athlete_id, need_token):
        return ("tok", BASE, "u1"), PUSHED
    monkeypatch.setattr(P, "_session_and_pushes", fake_session)
    monkeypatch.setattr(P, "_fit_index", lambda: {})
    monkeypatch.setattr(P, "PAUSE_S", 0)
    monkeypatch.setattr(V, "_dataset", lambda **kw: _DS())
    fake = FakeCoros()
    out = tmp_path / "o"
    with http.use_transport(httpx.MockTransport(fake)):
        assert P.main(["--out", str(out), "--detail", "1"]) == 0
    assert {m for m, _ in fake.calls} == {"GET"}
    text = (out / "activities.csv").read_text() + (out / "planned.csv").read_text() + (out / "summary.json").read_text()
    for secret in ("tok", "u1", "4700", "4701", "Run 0", "TRC easy", "hand made", "25.0"):
        assert secret not in text, secret
    rows = list(csv.DictReader(open(out / "activities.csv", encoding="utf-8")))
    assert len(rows) == 3 and rows[0]["date"] == "2026-09-01" and rows[0]["in_app"] == "1"
    assert rows[0]["tss"] == "50.0" and rows[0]["list.trainingLoad"] == "40.0"
    assert rows[0]["z2_s"] == "600.0" and rows[0]["hr_coverage"] == "1.0"
    summ = json.loads((out / "summary.json").read_text())
    assert summ["tl_columns"][0] == "list.trainingLoad"
    assert "COROS load-like fields" in capsys.readouterr().out
