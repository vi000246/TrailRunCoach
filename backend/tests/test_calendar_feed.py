"""
課表訂閱 (engine/calendar_feed.py, api/calendar_feed.py): the stored plan as an
RFC 5545 feed — validity (CRLF, ≤ 75-octet lines, required properties), the
secret token (404, regenerate), the window, the done marker, edits / moves /
deletes following through (same UID, higher SEQUENCE), the deep link, and that
the demo instance mounts none of it.
"""
import datetime as dt
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import calendar_feed as A
from backend.engine import calendar_feed as CF
from backend.engine import plan_store as PS
from backend.tests.test_coros_workouts import make_db, run

TODAY = dt.date(2026, 10, 4)


def sess(uid, day, title="輕鬆跑", kind="easy", minutes=45, state="active", **kw):
    return {"uid": uid, "week_start": PS.R.monday_of(day or "2026-10-05"), "gen_key": None, "day": day, "kind": kind,
            "title": title, "minutes": minutes, "target": kw.pop("target", ""), "detail": kw.pop("detail", ""),
            "source": "", "tss": kw.pop("tss", 30.0), "origin": "custom", "edited": False, "provisional": False,
            "state": state, "done_by": kw.pop("done_by", None), "note": None, **kw}


# ---------------------------------------------------------------------------
# a minimal RFC 5545 reader (unfold, split properties) for the checks
# ---------------------------------------------------------------------------

def parse(text: str) -> tuple[dict, list[dict]]:
    assert text.endswith("\r\n")
    raw = text.split("\r\n")[:-1]
    for ln in raw:
        assert len(ln.encode("utf-8")) <= 75, ln
        assert "\n" not in ln and "\r" not in ln
    lines: list[str] = []
    for ln in raw:
        if ln.startswith(" "):
            lines[-1] += ln[1:]
        else:
            lines.append(ln)
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-1] == "END:VCALENDAR"
    cal, events, cur = {}, [], None
    for ln in lines[1:-1]:
        name, _, value = ln.partition(":")
        if ln == "BEGIN:VEVENT":
            cur = {}
        elif ln == "END:VEVENT":
            events.append(cur)
            cur = None
        else:
            (cur if cur is not None else cal)[name] = value
    return cal, events


def unescape(v: str) -> str:
    return v.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------

def test_fold_and_escape():
    long = "SUMMARY:" + "閾值 3×10 分，暖身 15 分；緩和 10 分\\" * 6
    folded = CF.fold(long)
    parts = folded.split("\r\n")
    assert all(len(p.encode()) <= 75 for p in parts)
    assert parts[0] + "".join(p[1:] for p in parts[1:]) == long          # nothing lost, no char split
    assert CF.escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"


def test_build_is_valid_and_has_the_required_properties():
    long_detail = "休 2–3 分鐘；暖身 15 分、緩和 10 分，" * 8
    rows = [(sess("u1", "2026-10-05", "閾值 3×10 分", "quality", 60, detail=long_detail,
                  target="功率 280–300 W", distance_km=10.0, climb_m=120,
                  steps={"items": [{"kind": "warm", "dur": {"type": "time", "value": 900}}]}),
             dt.datetime(2026, 10, 1, 8, 0, 0))]
    cal, ev = parse(CF.build(rows, TODAY, "https://trc.example.org", now=dt.datetime(2026, 10, 4, 1, 2, 3)))
    assert cal["VERSION"] == "2.0" and cal["PRODID"] and cal["METHOD"] == "PUBLISH"
    assert cal["X-WR-CALNAME"] and cal["REFRESH-INTERVAL;VALUE=DURATION"] == "PT1H"
    assert cal["X-PUBLISHED-TTL"] == "PT1H"
    e = ev[0]
    assert e["UID"] == "u1@trailruncoach"
    assert e["DTSTAMP"] == "20261004T010203Z"
    assert e["DTSTART;VALUE=DATE"] == "20261005" and e["DTEND;VALUE=DATE"] == "20261006"
    assert e["LAST-MODIFIED"] == "20261001T080000Z" and int(e["SEQUENCE"]) > 0
    assert unescape(e["SUMMARY"]) == "閾值 3×10 分"
    d = unescape(e["DESCRIPTION"])
    assert "60 分" in d and "TSS 30" in d and "功率 280–300 W" in d and "10.0 km" in d and "爬升 120 m" in d
    assert "暖身" in d and long_detail.strip() in d


def test_window_states_and_kinds():
    rows = [(sess("old", "2026-09-19"), None), (sess("past", "2026-09-20"), None),
            (sess("far", "2026-11-30"), None), (sess("last", "2026-11-29"), None),
            (sess("del", "2026-10-06", state="deleted"), None), (sess("sup", "2026-10-07", state="superseded"), None),
            (sess("note", "2026-10-08", kind="notice", title="課表待確認"), None),
            (sess("miss", "2026-10-01", state="missed"), None), (sess("noday", None), None)]
    _cal, ev = parse(CF.build(rows, TODAY, None))
    assert sorted(e["UID"].split("@")[0] for e in ev) == ["last", "miss", "past"]
    miss = next(e for e in ev if e["UID"].startswith("miss"))
    assert unescape(miss["SUMMARY"]).startswith("✗ ")
    assert all("URL" not in e for e in ev)          # no base, no link


def test_done_marker_and_actuals():
    act = {"index": 3, "date": "2026-10-02", "moving_s": 2580, "tss": 41.6}
    _cal, ev = parse(CF.build([(sess("d", "2026-10-02", state="done", done_by=act), None)], TODAY, None))
    assert unescape(ev[0]["SUMMARY"]) == "✓ 輕鬆跑"
    assert "實際：43 分 · TSS 42" in unescape(ev[0]["DESCRIPTION"])


def test_deep_link_opens_the_session():
    _cal, ev = parse(CF.build([(sess("abc123", "2026-10-06"), None)], TODAY, "https://trc.example.org/"))
    url = ev[0]["URL"]
    u = urlsplit(url)
    assert f"{u.scheme}://{u.netloc}{u.path}" == "https://trc.example.org/api/v1/overview/plan/schedule/page"
    assert parse_qs(u.query) == {"day": ["2026-10-06"], "uid": ["abc123"]}
    assert url in unescape(ev[0]["DESCRIPTION"])


def test_token_and_setting_validation():
    t = CF.new_token()
    assert CF.TOKEN_RE.fullmatch(t)
    assert CF.token_ok(t, {"token": t}) and not CF.token_ok(t + "x", {"token": t})
    assert not CF.token_ok(t, None) and not CF.token_ok("", {"token": t})
    CF.validate_setting({"token": t, "origin": "https://a.example:8443"})
    for bad in ({"token": "short"}, {"token": t, "origin": "https://a.example/path"}, "x",
                {"token": t, "origin": "javascript:alert(1)"}):
        with pytest.raises(ValueError):
            CF.validate_setting(bad)
    assert CF.clean_origin("https://u:p@a.example") is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class Env:
    def __init__(self, monkeypatch):
        from backend.db.database import get_db
        from backend.engine import localtime
        self.db = run(make_db(logged_in=False))
        monkeypatch.setattr(localtime, "today_local", lambda *a, **k: TODAY)
        monkeypatch.delenv(A.ENV_PUBLIC_URL, raising=False)

        async def fake_db():
            yield self.db
        app = FastAPI()
        app.include_router(A.router)
        app.include_router(A.feed_router)
        app.dependency_overrides[get_db] = fake_db
        self.c = TestClient(app)

    def save(self, rows):
        run(PS.save(self.db, rows))

    def feed(self, path):
        r = self.c.get(path)
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/calendar")
        return parse(r.text)[1]


def test_token_regenerate_and_disable(monkeypatch):
    env = Env(monkeypatch)
    assert env.c.get("/api/v1/plan/calendar").json()["enabled"] is False
    assert env.c.get("/share/calendar/" + "x" * 32 + ".ics").status_code == 404
    first = env.c.post("/api/v1/plan/calendar/token", json={"origin": "https://trc.example.org"}).json()
    assert first["enabled"] and first["url"] == "https://trc.example.org" + first["path"]
    assert env.c.get(first["path"]).status_code == 200
    assert env.c.get(first["path"][:-6] + "zz.ics").status_code == 404          # wrong token: 404, not 401
    second = env.c.post("/api/v1/plan/calendar/token", json={}).json()
    assert second["path"] != first["path"]
    assert env.c.get(first["path"]).status_code == 404                          # the old address is dead
    assert env.c.get(second["path"]).status_code == 200
    assert env.c.get("/api/v1/plan/calendar").json()["path"] == second["path"]
    env.c.delete("/api/v1/plan/calendar/token")
    assert env.c.get(second["path"]).status_code == 404


def test_feed_follows_edits_moves_and_deletes(monkeypatch):
    env = Env(monkeypatch)
    env.save([sess("a1", "2026-10-06", "輕鬆跑"), sess("b2", "2026-10-07", "LSD", "long", 120),
              sess("c3", "2026-10-02", state="done")])
    path = env.c.post("/api/v1/plan/calendar/token", json={"origin": "https://trc.example.org"}).json()["path"]
    ev = {e["UID"]: e for e in env.feed(path)}
    assert set(ev) == {"a1@trailruncoach", "b2@trailruncoach", "c3@trailruncoach"}
    assert unescape(ev["c3@trailruncoach"]["SUMMARY"]).startswith("✓ ")
    a0 = ev["a1@trailruncoach"]
    assert "uid=a1" in a0["URL"]

    # saving the same plan again changes nothing (save() rewrites every row)
    env.save([dict(s) for s in run(PS.load(env.db))])
    assert {e["UID"]: e["SEQUENCE"] for e in env.feed(path)} == {k: v["SEQUENCE"] for k, v in ev.items()}

    # edit: same UID, later LAST-MODIFIED, higher SEQUENCE
    run(PS.edit(env.db, "a1", {"title": "輕鬆跑＋快步", "minutes": 50}, "2026-10-04"))
    a1 = next(e for e in env.feed(path) if e["UID"] == "a1@trailruncoach")
    assert unescape(a1["SUMMARY"]) == "輕鬆跑＋快步"
    assert int(a1["SEQUENCE"]) > int(a0["SEQUENCE"]) and a1["LAST-MODIFIED"] >= a0["LAST-MODIFIED"]

    # move to another day (and week): same UID, new DTSTART, higher SEQUENCE again
    run(PS.edit(env.db, "a1", {"day": "2026-10-13"}, "2026-10-04"))
    a2 = next(e for e in env.feed(path) if e["UID"] == "a1@trailruncoach")
    assert a2["DTSTART;VALUE=DATE"] == "20261013" and int(a2["SEQUENCE"]) > int(a1["SEQUENCE"])
    assert sum(e["UID"] == "a1@trailruncoach" for e in env.feed(path)) == 1

    # delete: gone from the feed
    run(PS.delete(env.db, "b2", today="2026-10-04"))
    assert "b2@trailruncoach" not in {e["UID"] for e in env.feed(path)}


def test_links_prefer_public_url_then_stored_origin(monkeypatch):
    env = Env(monkeypatch)
    env.save([sess("a1", "2026-10-06")])
    path = env.c.post("/api/v1/plan/calendar/token", json={"origin": "https://seen.example"}).json()["path"]
    assert env.feed(path)[0]["URL"].startswith("https://seen.example/")
    monkeypatch.setenv(A.ENV_PUBLIC_URL, "https://public.example/")
    assert env.feed(path)[0]["URL"].startswith("https://public.example/")
    monkeypatch.delenv(A.ENV_PUBLIC_URL)
    path = env.c.post("/api/v1/plan/calendar/token", json={}).json()["path"]
    assert env.feed(path)[0]["URL"].startswith("http://testserver/")


def test_forwarded_headers_only_from_a_trusted_proxy(monkeypatch):
    from starlette.requests import Request

    def req(peer):
        return Request({"type": "http", "method": "GET", "path": "/", "scheme": "http", "query_string": b"",
                        "server": ("internal", 8000), "client": (peer, 1234),
                        "headers": [(b"host", b"internal:8000"), (b"x-forwarded-proto", b"https"),
                                    (b"x-forwarded-host", b"trc.example.org")]})
    monkeypatch.setenv("WKO5COACH_TRUSTED_PROXIES", "127.0.0.1/32")
    assert A.request_origin(req("127.0.0.1")) == "https://trc.example.org"
    assert A.request_origin(req("203.0.113.9")) == "http://internal:8000"


def test_demo_instance_mounts_no_feed():
    """The demo builds no feed of its athlete and can't reach the owner's (neither router)."""
    from backend.main import build_app

    def routers(app):
        return {id(getattr(r, "original_router", None)) for r in app.routes}
    mine = {id(A.router), id(A.feed_router)}
    assert mine <= routers(build_app(demo=False))
    assert not mine & routers(build_app(demo=True))
