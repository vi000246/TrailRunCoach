"""SP-805: 腳程估算 — one recorded hike + a route → that person's time on the route (engine/racepower/
pace_estimate.py), for 疊石 Cairn's 揪團報名 (POST /api/v1/link/cairn/pace, SP-734) and the public 試算頁
(/share/pace, api/pace.py; signed in also 「比你」, POST /api/v1/pace/me).

The tracks are synthetic: a climb and a descent walked at a multiple of Tobler's hiking function, so the
expected speed of each is known. Nothing of the uploaded files may be kept: the tests look for a marker
written into the GPX in every file under the test's folders after each call."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.engine.racepower import gpx as GPX
from backend.engine.racepower import pace_estimate as PE
from backend.engine.racepower.grade_model import tobler_kmh

MARKER = "SP805-MARKER-do-not-keep"


def make_gpx(km_up=5.0, gain=800.0, km_down=5.0, factor=1.0, stop_s=600.0, timed=True, z0=1000.0, step=10.0,
             night_s=0.0, name=MARKER) -> bytes:
    """Up `gain` m over km_up, down again over km_down, each 10 m at factor × Tobler; a `stop_s` stop at
    the top's halfway point and, with `night_s`, a night that long at the top."""
    lat, lon, ele, tt = [], [], [], []
    t = x = 0.0
    n_up, n_dn = int(km_up * 1000 / step), int(km_down * 1000 / step)
    z = z0
    for i in range(n_up + n_dn + 1):
        g = gain / (km_up * 1000) if i < n_up else -gain / (km_down * 1000)
        lat.append(24.0 + x / 111320.0)
        lon.append(121.0)
        ele.append(z)
        tt.append(t)
        pause = (stop_s if i == n_up // 2 else 0.0) + (night_s if i == n_up else 0.0)
        if pause:
            for k in range(1, int(pause / 30) + 1):
                lat.append(24.0 + x / 111320.0)
                lon.append(121.0)
                ele.append(z)
                tt.append(t + 30 * k)
            t += pause
        v = tobler_kmh(g) / 3.6 * factor
        x += step
        z += g * step
        t += step / v
    return GPX.write_gpx(GPX.Track(lat, lon, ele, tt if timed else None), name=name).encode()


def moving_s_of(factor=1.0, km_up=5.0, gain=800.0, km_down=5.0) -> float:
    up = km_up * 1000 / (tobler_kmh(gain / (km_up * 1000)) / 3.6 * factor)
    dn = km_down * 1000 / (tobler_kmh(-gain / (km_down * 1000)) / 3.6 * factor)
    return up + dn


ROUTE = make_gpx(timed=False, name="route")


def est(factor=1.0, pack_kg=4.0, trip_kind="solo", route=ROUTE, route_pack=4.0, days=None, **kw):
    ab = PE.track_ability(make_gpx(factor=factor, **kw), "t.gpx", pack_kg, trip_kind)
    return PE.estimate(ab, PE.route_of(route, "r.gpx", days), route_pack)


# ---------------------------------------------------------------------------- the model

def test_the_tracks_own_route_comes_back_at_its_moving_time():
    for f in (0.7, 1.0, 1.3):
        e = est(f)
        assert e["moving_s"] == pytest.approx(moving_s_of(f), rel=0.05), f
        lo, hi = e["moving_range_s"]
        assert lo < e["moving_s"] < hi


def test_a_slower_hiker_reads_slower_by_about_the_same_share():
    slow, fast = est(0.8), est(1.2)
    c = PE.compare(slow, fast)
    assert c["moving"] == pytest.approx(1.5, rel=0.06)
    assert c["parts"]["up"] == pytest.approx(1.5, rel=0.08) and c["parts"]["down"] == pytest.approx(1.5, rel=0.08)
    assert c["pct"] == PE.pct_of(c["moving"]) > 40
    # against the common ruler: the slow one slower than the fast one, both as a time ratio
    assert slow["general"]["ratio"] > fast["general"]["ratio"]
    assert slow["general"]["pct"] == PE.pct_of(slow["general"]["ratio"])


def test_one_track_is_not_pulled_to_the_prior_level():
    """The level over all windows is the person's (LeveledSpeed): a track at 0.6 × Tobler is not read
    as near Tobler, as the bare per-bin shrinkage would on a single track."""
    e = est(0.6)
    assert e["moving_s"] == pytest.approx(moving_s_of(0.6), rel=0.06)
    assert e["moving_s"] > 1.4 * moving_s_of(1.0)


def test_the_pack_on_the_track_and_on_the_route():
    base = est(1.0, pack_kg=4.0, route_pack=4.0)["moving_s"]
    assert est(1.0, pack_kg=4.0, route_pack=14.0)["moving_s"] > base * 1.1           # heavier this time: slower
    assert est(1.0, pack_kg=14.0, route_pack=4.0)["moving_s"] < base * 0.9           # carried more then: faster now
    # no route pack: the 單日 / 多日 default (capacity.PACK_DEFAULT_*)
    assert est(1.0, route_pack=None)["route"]["pack_kg"] == 4.0
    assert est(1.0, route_pack=None, days=3)["route"]["pack_kg"] == 9.0


def test_a_higher_route_is_slower():
    high = make_gpx(timed=False, z0=3200.0, name="high")
    assert est(1.0, route=high)["moving_s"] > est(1.0)["moving_s"] * 1.03


def test_a_group_track_leans_on_the_prior_and_widens_the_range():
    solo, group = est(0.7, trip_kind="solo"), est(0.7, trip_kind="group")
    w = lambda e: e["moving_range_s"][1] - e["moving_range_s"][0]   # noqa: E731
    assert w(group) / group["moving_s"] > w(solo) / solo["moving_s"]
    assert group["basis"]["trip_kind"] == "group" and solo["basis"]["trip_kind"] == "solo"


def test_stops_are_per_day_a_night_is_not_a_stop_and_one_day_leans_on_the_prior():
    from backend.engine.racepower import nonmoving as NM
    ab = PE.track_ability(make_gpx(stop_s=600, night_s=10 * 3600), "t.gpx", 8.0, "group")
    assert ab.basis["days"] == 2
    assert ab.basis["stopped_s"] < 3600                     # the 10 h night left out
    one = PE.track_ability(make_gpx(stop_s=0), "t.gpx")
    assert one.stop["n"] == 1 and one.stop["prior"] == NM.NONMOVING["prior"]["hike"]
    e = PE.estimate(one, PE.route_of(ROUTE))
    assert 0 < e["stop"]["share"] <= NM.NONMOVING["share_max"]
    lo, hi = e["stop"]["range_s"]
    assert lo <= e["stop"]["total_s"] <= hi
    assert e["total_s"] == pytest.approx(e["moving_s"] + e["stop"]["total_s"])
    assert e["total_range_s"][0] < e["total_s"] < e["total_range_s"][1]


def test_the_answer_has_what_cairn_shows():
    """SP-734's mock: the total (median and range), 上坡 / 平路 / 下坡 relative values, the stops, the basis."""
    e = est(1.0)
    for k in ("total_s", "total_range_s", "moving_s", "moving_range_s", "stop", "general", "parts", "basis",
              "route", "speeds", "model"):
        assert k in e, k
    assert set(e["parts"]) == {"up", "flat", "down"}
    assert e["parts"]["up"]["ratio"] and e["parts"]["down"]["ratio"] and e["parts"]["up"]["vam_m_h"] > 0
    assert e["basis"]["kind"] == "track" and e["basis"]["text"] == "依一趟紀錄估計"
    assert e["basis"]["km"] == pytest.approx(10.0, rel=0.02) and e["basis"]["gain_m"] == pytest.approx(800, rel=0.05)
    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
    assert not {"lat", "lon", "time", "start", "date", "name"} & set(keys(e))          # nothing of where or when


def test_files_that_cannot_be_used():
    cases = [
        (make_gpx(timed=False), "NO_TIME"),
        (make_gpx(km_up=0.3, km_down=0.3, gain=30), "TOO_SHORT"),
        (b"<gpx><trk><trkseg></trkseg></trk></gpx>", "BAD_FILE"),
        (b"not a gpx", "BAD_FILE"),
    ]
    for data, code in cases:
        with pytest.raises(PE.EstimateError) as ei:
            PE.track_ability(data, "x.gpx")
        assert ei.value.code == code, (code, str(ei.value))
    with pytest.raises(PE.EstimateError) as ei:
        PE.route_of(make_gpx(km_up=0.2, km_down=0.2, gain=10, timed=False))
    assert ei.value.code == "TOO_SHORT"


def test_the_account_uses_the_calculators_walking_curve():
    from backend.engine.racepower import grade_model as GM
    with pytest.raises(PE.EstimateError) as ei:
        PE.account_ability(None, None)
    assert ei.value.code == "NO_DATA"
    hs = GM.fit_hike_speed([{"g": g / 100, "v": tobler_kmh(g / 100) / 3.6 * 0.9, "z": 800.0, "a": i}
                            for i, g in enumerate(range(-20, 22, 2)) for _ in range(40)])
    ab = PE.account_ability(hs, None, "group", 5)
    assert ab.basis["kind"] == "account" and ab.basis["days"] == 5
    e = PE.estimate(ab, PE.route_of(ROUTE), 4.0)
    assert e["moving_s"] == pytest.approx(moving_s_of(0.9), rel=0.06)


def test_the_common_ruler_is_munter_with_a_descent_cost():
    p = {"dist_m": 1000.0, "up": 100.0, "down": 0.0}
    assert PE.general_s(p) == pytest.approx((1 + 1) / 4 * 3600)
    assert PE.general_s({"dist_m": 1000.0, "up": 0.0, "down": 200.0}) == pytest.approx((1 + 1) / 4 * 3600)


# ---------------------------------------------------------------------------- the page

def test_the_page_never_names_the_public_formula():
    """SP-734 Q1-3: the ruler behind 「比一般登山客」 is not on the page (the person does not know Munter)."""
    root = Path(PE.__file__).resolve().parents[2] / "static"
    texts = [(root / "pace.html").read_text("utf-8")] + [(root / "i18n" / loc / "pace.json").read_text("utf-8")
                                                          for loc in ("zh-TW", "en")]
    for t in texts:
        for word in ("Munter", "Naismith", "Tobler", "Langmuir", "公式"):
            assert word not in t, word


@pytest.mark.parametrize("loc", ["zh-TW", "en"])
def test_the_page_loads_nothing_from_behind_the_gate(loc):
    """Cloudflare Access lets only /share/* through (SP-698): i18n.js inline, no /api/v1/static/."""
    import re
    from backend.i18n import pages as P
    html = P.render("pace", loc, debug=False)
    srcs = re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", re.sub(r"<script>.*?</script>", "", html, flags=re.S))
    assert srcs == []
    assert "/api/v1/static/" not in re.sub(r"<script>.*?</script>", "", html, flags=re.S)
    assert "window.I18N = {" in html
    if loc == "en":
        markup = re.sub(r"<script>.*?</script>", "", html, flags=re.S)
        body = markup[markup.index("<body>"):]
        assert not re.search(r"[一-鿿]", body), "Chinese left in the English page"


# ---------------------------------------------------------------------------- the routes (sign-in mode)

from backend.tests.test_link_sp329 import (  # noqa: E402,F401 — fixtures used by name
    SECRET, _limits, _link, _server, _token, _uid, app, browser, c, cairn, csrf, env, sign_up, started,
)


@pytest.fixture(autouse=True)
def _pace_limits():
    from backend.api import pace as PA
    PA.reset_limits()
    yield
    PA.reset_limits()


def _files(track=None, route=ROUTE, **fields):
    files = {"track": ("hike.gpx", track if track is not None else make_gpx(factor=0.9), "application/gpx+xml"),
             "route": ("route.gpx", route, "application/gpx+xml")}
    data = {"pack_kg": "6", "trip_kind": "group", **fields}
    return files, data


def _kept(root: Path) -> list[str]:
    """Files under `root` holding the marker (the uploaded GPX's name)."""
    out = []
    for p in root.rglob("*"):
        if p.is_file():
            try:
                if MARKER.encode() in p.read_bytes():
                    out.append(str(p))
            except OSError:
                pass
    return out


def test_a_visitor_who_is_not_signed_in_gets_the_estimate_and_nothing_is_kept(c, app, tmp_path):
    from backend.api import racepower as RP
    pub = browser(app)
    page = pub.get("/share/pace")
    assert page.status_code == 200 and 'id="f"' in page.text and "no-store" in page.headers["cache-control"]
    before = dict(RP._courses)
    files, data = _files()
    r = pub.post("/share/pace/estimate", files=files, data=data)             # no session, no CSRF header
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_s"] > body["moving_s"] > 0 and body["basis"]["trip_kind"] == "group"
    assert body["general"]["pct"] == PE.pct_of(body["general"]["ratio"])
    assert "no-store" in r.headers["cache-control"]
    assert _kept(tmp_path) == []                                              # no file, no log, no DB row
    assert dict(RP._courses) == before                                        # not the calculator's course cache


def test_the_visitors_route_has_no_tenant(c, app, monkeypatch):
    from backend import tenancy
    from backend.api import pace as PA
    seen = []
    real = PA.estimate_from_track

    def spy(up):
        seen.append(tenancy.current())
        return real(up)
    monkeypatch.setattr(PA, "estimate_from_track", spy)
    files, data = _files()
    assert browser(app).post("/share/pace/estimate", files=files, data=data).status_code == 200
    assert seen and seen[0].id == tenancy.anonymous().id


def test_the_visitors_route_is_limited(c, app, monkeypatch):
    from backend.api import pace as PA
    from backend.security import ratelimit as RL
    pub = browser(app, "198.51.100.40")
    monkeypatch.setattr(PA, "PER_IP", RL.Buckets(rate=1, per=3600, burst=2))
    files, data = _files()
    assert pub.post("/share/pace/estimate", files=files, data=data).status_code == 200
    assert pub.post("/share/pace/estimate", files=files, data=data).status_code == 200
    r = pub.post("/share/pace/estimate", files=files, data=data)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "RATE_LIMITED" and r.headers["retry-after"]
    assert browser(app, "198.51.100.41").post("/share/pace/estimate", files=files, data=data).status_code == 200


def test_a_file_too_large_is_refused_before_it_is_read(c, app, monkeypatch):
    from backend.api import pace as PA
    monkeypatch.setattr(PA, "MAX_FILE", 20_000)
    monkeypatch.setattr(PA, "MAX_BODY", 50_000)
    files, data = _files(track=make_gpx(step=2.0))
    r = browser(app).post("/share/pace/estimate", files=files, data=data)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "TOO_LARGE"


def test_bad_inputs_answer_400(c, app):
    pub = browser(app)
    files, data = _files(track=make_gpx(timed=False))
    r = pub.post("/share/pace/estimate", files=files, data=data)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "NO_TIME"
    files, data = _files(pack_kg="99")
    assert pub.post("/share/pace/estimate", files=files, data=data).status_code == 400
    files, data = _files(trip_kind="team")
    assert pub.post("/share/pace/estimate", files=files, data=data).status_code == 400
    r = pub.post("/share/pace/estimate", files={"route": ("r.gpx", ROUTE, "application/gpx+xml")})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "FILE_REQUIRED"


def _fake_account(monkeypatch, factor=1.0, n=40):
    from backend.api import racepower as RP
    from backend.engine.racepower import grade_model as GM
    hs = GM.fit_hike_speed([{"g": g / 100, "v": tobler_kmh(g / 100) / 3.6 * factor, "z": 800.0, "a": i}
                            for i, g in enumerate(range(-20, 22, 2)) for _ in range(n)]) if n else None
    monkeypatch.setattr(RP, "_grade_models", lambda: {"hike_speed": hs, "hike_basis": {"solo_hikes": 3}})
    monkeypatch.setattr(RP, "_trail_hr", lambda: None)


def test_signed_in_the_page_also_compares_with_you_and_counts_the_calculator_once(c, app, monkeypatch):
    from backend.accounts import store
    _fake_account(monkeypatch, factor=1.0)
    sign_up(c, "g-a", "alice@example.com")
    tok = csrf(c)
    files = {"route": ("route.gpx", ROUTE, "application/gpx+xml")}
    assert browser(app).post("/api/v1/pace/me", files=files).status_code == 401           # signed out
    assert c.post("/api/v1/pace/me", files=files).status_code == 403                      # no CSRF header
    r = c.post("/api/v1/pace/me", files=files, headers={"X-TRC-CSRF": tok})
    assert r.status_code == 200, r.text
    mine = r.json()
    assert mine["basis"]["kind"] == "account" and mine["moving_s"] == pytest.approx(moving_s_of(1.0), rel=0.06)
    files2, data = _files(track=make_gpx(factor=0.8))
    theirs = c.post("/share/pace/estimate", files=files2, data=data).json()
    assert PE.compare(theirs, mine)["moving"] == pytest.approx(1.25, rel=0.08)
    # the same route the same day: the calculator counted once (SP-713)
    assert c.post("/api/v1/pace/me", files=files, headers={"X-TRC-CSRF": tok}).status_code == 200
    import datetime as dt
    from backend import membership as MS
    month = MS._period_key("month", dt.datetime.now(dt.timezone.utc))
    assert store.usage_get(_uid("g-a"), "calc", month) == 1


def test_signed_in_without_hikes_says_so(c, app, monkeypatch):
    _fake_account(monkeypatch, n=0)
    sign_up(c, "g-a", "alice@example.com")
    r = c.post("/api/v1/pace/me", files={"route": ("r.gpx", ROUTE, "application/gpx+xml")},
               headers={"X-TRC-CSRF": csrf(c)})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "NO_DATA"


# ---------------------------------------------------------------------------- Cairn (server to server)

PACE_URL = "/api/v1/link/cairn/pace"


def _cairn_post(srv, tok, track=None, consent="true", **fields):
    files, data = _files(track=track, **fields)
    if consent is not None:
        data["consent"] = consent
    return srv.post(PACE_URL, files=files, data=data, headers={"Authorization": f"Bearer {tok}"})


def test_cairn_estimates_a_participant_with_their_consent_on_the_organisers_link(c, app, cairn, tmp_path):
    sign_up(c, "g-a", "alice@example.com")
    assert _link(c).status_code == 200
    with _server(app) as srv:
        tok = _token(srv, "g-a").json()["token"]
        r = _cairn_post(srv, tok, consent=None)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "CONSENT_REQUIRED"
        assert r.json()["detail"]["scope"] == "link:participant_ability"
        r = _cairn_post(srv, tok)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_range_s"][0] < body["total_s"] < body["total_range_s"][1]
        assert set(body["parts"]) == {"up", "flat", "down"} and body["basis"]["kind"] == "track"
        assert body["quota"] == {"used": 1, "limit": 10}
        assert "no-store" in r.headers["cache-control"]
        # only POST, only with a service token
        assert srv.get(PACE_URL, headers={"Authorization": f"Bearer {tok}"}).status_code == 401
        files, data = _files()
        assert srv.post(PACE_URL, files=files, data={**data, "consent": "true"}).status_code == 401
    assert _kept(tmp_path) == []                       # neither file kept (a digest of the track counts the quota)


def test_cairn_needs_a_linked_organiser(c, app, cairn):
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    with _server(app) as srv:
        tok = _token(srv, "g-a").json()["token"]
        _link(c, False)
        assert _cairn_post(srv, tok).status_code == 401          # unlinking deleted the token
        assert _cairn_post(srv, "trc_" + "x" * 40).status_code == 401


def test_the_free_plan_counts_participants_a_month_and_the_admin_sets_the_number(c, app, cairn):
    from backend import site_config as SC
    from backend.accounts import store
    assert "quota.free.participants" in SC.ITEMS and SC.ITEMS["quota.free.participants"].default == 10
    SC.set_values({"quota.free.participants": 2}, actor=1)
    sign_up(c, "g-a", "alice@example.com")
    _link(c)
    with _server(app) as srv:
        tok = _token(srv, "g-a").json()["token"]
        a, b, d = make_gpx(factor=0.8), make_gpx(factor=0.9), make_gpx(factor=1.1)
        assert _cairn_post(srv, tok, track=a).json()["quota"]["used"] == 1
        assert _cairn_post(srv, tok, track=a).json()["quota"]["used"] == 1       # the same 報名者 again: free
        assert _cairn_post(srv, tok, track=b).json()["quota"]["used"] == 2
        r = _cairn_post(srv, tok, track=d)
        assert r.status_code == 429 and r.json()["detail"]["code"] == "QUOTA_EXCEEDED"
        assert r.json()["detail"]["quota"] == "participants" and r.json()["detail"]["upgrade"] is True
        # a file that cannot be used counts nothing
        assert _cairn_post(srv, tok, track=make_gpx(timed=False)).status_code == 400
        # a subscription: no limit
        store.set_plan(_uid("g-a"), "subscriber", "2099-01-01T00:00:00+00:00")
        r = _cairn_post(srv, tok, track=d)
        assert r.status_code == 200 and r.json()["quota"]["limit"] is None


def test_the_admins_extra_uses_do_not_raise_the_participants(c, app):
    """本月額外次數 (SP-713) are uses of the calculator; the participants quota counts people."""
    import datetime as dt
    from backend import membership as MS
    from backend.accounts import store
    sign_up(c, "g-a", "alice@example.com")
    uid = _uid("g-a")
    store.quota_extra_set(uid, MS.month_key(), 5)
    m = MS.Member(MS.USER, MS.FREE, uid)
    now = dt.datetime.now(dt.timezone.utc)
    assert MS.quota_for("participants", m, now).limit == 10 and MS.quota_for("calc", m, now).limit == 15
    assert "extra" not in MS.usage("participants", now, m)


def test_no_token_ever_carries_the_participant_scope():
    from backend.accounts import links as LK
    assert "link:participant_ability" not in LK.CAIRN.scopes and "link:participant_ability" not in LK.FEATURES
    assert LK.POST_ROUTES[PACE_URL] == "link:participant_ability"
    assert LK.CONSENT_PER_REQUEST["link:participant_ability"] == "link:account"
    assert PACE_URL not in LK.ROUTES


def test_the_quota_reads_in_the_plans_and_settings_pages():
    import json
    root = Path(PE.__file__).resolve().parents[2] / "static" / "i18n"
    for loc in ("zh-TW", "en"):
        plans = json.loads((root / loc / "plans.json").read_text("utf-8"))
        settings = json.loads((root / loc / "settings.json").read_text("utf-8"))
        admin = json.loads((root / loc / "admin.json").read_text("utf-8"))
        assert plans.get("use.participants") and settings.get("plan_sum.participants")
        assert admin.get("item.quota.free.participants") and admin.get("item.quota.free.participants.help")
