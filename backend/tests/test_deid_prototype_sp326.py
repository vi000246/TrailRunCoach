"""The de-identification prototype (SP-326, docs/research/deidentified-analytics.md §14,
§10 item 9): GPS trimming, time shift, account links, k threshold, source allow-list,
consent layers, withdrawal. Synthetic only: the demo athlete's generated FITs and
hand-made tracks — never a real tenant."""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import re

import numpy as np
import pytest

from backend.scripts import deid_prototype as D

KEY = b"test-pool-key"
HOME = (24.1000, 120.7000)
M_LAT = 1 / 111_320.0                   # degrees of latitude per metre


@pytest.fixture(scope="module")
def demo():
    return D.demo_tracks(weeks=4)


def _export(donor, tracks, seed=1, **kw):
    links = D.LinkTable()
    ex = D.export_donor(donor, tracks, links, pool_key=KEY, rng=np.random.default_rng(seed), **kw)
    return ex, links


def _rows_json(ex) -> str:
    return json.dumps({"s": ex.summaries, "p": ex.splits, "d": ex.daily, "a": ex.params}, ensure_ascii=False,
                      default=str)


def _track(points, start, *, source="manual", step_m=3.0, speed=3.0, account=None, is_race=False):
    """A track through `points` (lat, lon) at `speed` m/s, one sample per `step_m`."""
    pts = np.asarray(points, dtype=float)
    seg = D.haversine_m(pts[:-1, 0], pts[:-1, 1], pts[1:, 0], pts[1:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    d = np.arange(0.0, cum[-1], step_m)
    n = len(d)
    return D.Track(t=d / speed, lat=np.interp(d, cum, pts[:, 0]), lon=np.interp(d, cum, pts[:, 1]), dist=d,
                   speed=np.full(n, speed), ele=100 + 0.02 * d, hr=np.full(n, 140.0), power=np.full(n, 200.0),
                   cadence=np.full(n, 170.0), temp=np.full(n, 22.0), start_utc=start, source=source,
                   account=dict(account or {}), is_race=is_race)


def _north(origin, km):
    return [origin, (origin[0] + km * 1000 * M_LAT, origin[1])]


def _donor(**kw):
    base = dict(user_id="u-1", sex="female", age=34, lthr=170.0, cp_w=230.0, weight_kg=55.0, consent_b=True)
    return D.Donor(**{**base, **kw})


def _utc(y, m, d, h=22):
    return dt.datetime(y, m, d, h, 0, tzinfo=dt.timezone.utc)


# ---------------------------------------------------------------------------
# GPS
# ---------------------------------------------------------------------------

def test_gps_start_and_end_trimmed_by_at_least_1km(demo):
    donor, tracks, _ = demo
    rng = np.random.default_rng(3)
    checked = 0
    for tr in tracks:
        first, last = D._first_last_fix(tr)
        keep = D.keep_mask(tr, [], D.trim_radii(rng))
        if not keep.any():
            continue
        assert D.haversine_m(first[0], first[1], tr.lat[keep], tr.lon[keep]).min() > D.TRIM_BASE_M
        assert D.haversine_m(last[0], last[1], tr.lat[keep], tr.lon[keep]).min() > D.TRIM_BASE_M
        checked += 1
    assert checked >= 3                                     # the trail runs and the hikes keep a middle


def test_trim_radius_is_random_per_activity():
    rng = np.random.default_rng(5)
    radii = [D.trim_radii(rng) for _ in range(200)]
    starts = [r["start"] for r in radii]
    assert min(starts) >= 1000 and max(starts) <= 1500
    assert len({round(x) for x in starts}) > 100                              # not one fixed circle
    assert all(2000 <= r["place"] <= 2500 for r in radii)
    assert any(abs(r["start"] - r["end"]) > 50 for r in radii)                # each end its own draw
    # the same straight run cut twice: the first kept point lies at a different distance
    tr = _track(_north(HOME, 6), _utc(2026, 5, 1))
    cut = []
    for seed in (1, 2):
        keep = D.keep_mask(tr, [], D.trim_radii(np.random.default_rng(seed)))
        cut.append(float(tr.dist[np.flatnonzero(keep)[0]]))
    assert abs(cut[0] - cut[1]) > 10


def test_frequent_place_cut_2km_wherever_the_track_passes():
    # three runs from home within 90 days make home a frequent place ...
    ends = [(dt.date(2026, 5, 1) + dt.timedelta(days=d), HOME[0] + 0.0005 * i, HOME[1])
            for i, d in enumerate((0, 20, 40))]
    places = D.frequent_places(ends)
    assert len(places) == 1 and D.haversine_m(HOME[0], HOME[1], *places[0]) < 100
    # ... and a run that starts 5 km away and passes home mid-run is cut around home too
    far = (HOME[0] - 5000 * M_LAT, HOME[1])
    path = [far, HOME, (HOME[0] + 5000 * M_LAT, HOME[1]), (HOME[0] + 5000 * M_LAT, HOME[1] + 0.06)]
    tr = _track(path, _utc(2026, 6, 1))
    keep = D.keep_mask(tr, places, D.trim_radii(np.random.default_rng(0)))
    assert keep.any()
    assert D.haversine_m(*places[0], tr.lat[keep], tr.lon[keep]).min() > D.PLACE_TRIM_M
    # the circle is centred on the endpoints' mean, at most PLACE_RADIUS_M from the true home
    assert D.haversine_m(HOME[0], HOME[1], tr.lat[keep], tr.lon[keep]).min() > D.PLACE_TRIM_M - D.PLACE_RADIUS_M
    # without the place, the mid-run pass by home would have been kept
    keep0 = D.keep_mask(tr, [], D.trim_radii(np.random.default_rng(0)))
    assert D.haversine_m(HOME[0], HOME[1], tr.lat[keep0], tr.lon[keep0]).min() < 100


def test_frequent_place_needs_3_inside_90_days():
    spread = [(dt.date(2026, 1, 1) + dt.timedelta(days=100 * i), *HOME) for i in range(3)]
    assert D.frequent_places(spread) == []
    two = [(dt.date(2026, 1, 1), *HOME), (dt.date(2026, 1, 2), *HOME)]
    assert D.frequent_places(two) == []


def test_pool_rows_hold_no_coordinates(demo):
    donor, tracks, _ = demo
    ex, _ = _export(donor, tracks)
    assert ex.summaries and ex.splits and ex.daily
    assert set(ex.splits[0]) == {"grade", "grade_sd", "ele_band_m", "speed_ms", "pct_lthr", "w_per_kg", "cadence",
                                 "temp_c", "decile", "sex", "age_band"}
    coords = np.array([c for tr in tracks for fix in D._first_last_fix(tr) for c in fix])
    for rows in (ex.summaries, ex.splits, ex.daily, [ex.params]):
        for r in rows:
            assert not {"lat", "lon", "latitude", "longitude"} & set(r)
            for v in r.values():                            # no number within ~200 m of a start / end
                assert not (isinstance(v, float) and np.abs(coords - v).min() < 0.002), v


def test_grid_cells_need_k_distinct_people():
    cell = {(1, 2): [3.0] * 50}
    assert D.aggregate_grid([cell] * 4) == []                         # 4 people, many samples: hidden
    out = D.aggregate_grid([cell] * 5)
    assert out == [{"cell": [1, 2], "n_people": 5, "n_samples": 250, "median_speed_ms": 3.0}]
    assert D.aggregate_grid([cell] * 9, "public") == []               # other users see >= 10 only
    assert len(D.aggregate_grid([cell] * 10, "public")) == 1


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------

DATE_RE = re.compile(r"(19|20)\d{2}[-/]?\d{2}[-/]?\d{2}")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "星期", "週")


def test_no_absolute_date_or_weekday_in_the_pool(demo):
    donor, tracks, _ = demo
    ex, _ = _export(donor, tracks)
    txt = _rows_json(ex)
    assert not DATE_RE.search(txt)
    assert not any(w in txt.lower() for w in WEEKDAYS)
    for r in ex.summaries + ex.daily:
        assert not {"date", "start", "start_utc", "weekday", "workout_date", "imported_at"} & set(r)
    assert {s["time_of_day"] for s in ex.summaries} <= {"dawn", "day", "evening", "night"}
    assert {s["season"] for s in ex.summaries} <= {"winter", "spring", "summer", "autumn"}


def test_relative_days_keep_the_gaps_and_day0_is_shifted(demo):
    donor, tracks, _ = demo
    ex, links = _export(donor, tracks)
    local = sorted(tr.local_start(donor.tz_offset_h).date() for tr in tracks)
    rel = sorted(s["rel_day"] for s in ex.summaries)
    assert [d - rel[0] for d in rel] == [(d - local[0]).days for d in local]
    assert 0 <= rel[0] <= D.DAY0_JITTER_DAYS
    assert links.get(donor.user_id)["day0"] == local[0] - dt.timedelta(days=rel[0])
    # day 0's shift is drawn per donor: not always the same offset
    offs = {_export(donor, tracks[:1], seed=s)[0].summaries[0]["rel_day"] for s in range(40)}
    assert len(offs) > 5 and offs <= set(range(D.DAY0_JITTER_DAYS + 1))


def test_race_has_no_name_or_date_and_coarse_bins():
    acc = {"race_name": "示範越野 50K", "platform_activity_id": "r-1"}
    tr = _track(_north(HOME, 23.4), _utc(2026, 4, 11), account=acc, is_race=True)
    ex, _ = _export(_donor(), [tr])
    s = ex.summaries[0]
    assert s["is_race"] and s["distance_km"] == 20.0 and s["ascent_m"] % 500 == 0
    assert "示範越野" not in _rows_json(ex)


# ---------------------------------------------------------------------------
# account links
# ---------------------------------------------------------------------------

def _digests(v: str) -> set[str]:
    b = v.encode()
    return {h(b).hexdigest()[:n] for h in (hashlib.sha256, hashlib.sha1, hashlib.md5) for n in (16, 32)}


def test_account_links_never_reach_the_pool(demo):
    donor, tracks, _ = demo
    ex, links = _export(donor, tracks)
    txt = _rows_json(ex)
    secrets_ = {donor.user_id}
    for tr in tracks:
        secrets_ |= {str(v) for v in tr.account.values()}
    secrets_ |= {"demo", ".fit", "fit/coros"}
    for v in secrets_:
        assert v not in txt, v
        for h in _digests(v):
            assert h not in txt, v
    keys = {k for r in ex.summaries + ex.daily for k in r}
    assert not keys & {"email", "google_sub", "coros_user_id", "tp_username", "file_path", "coros_activity_id",
                       "tp_workout_id", "user_id", "name", "title", "note"}


def test_donor_id_is_random_not_derived():
    tr = _track(_north(HOME, 5), _utc(2026, 5, 1))
    d = _donor(user_id="alice@example.invalid")
    ex1, l1 = _export(d, [tr])
    ex2, l2 = _export(d, [tr])
    a, b = ex1.summaries[0]["donor_id"], ex2.summaries[0]["donor_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", a) and a != b              # 128 random bits, new for a new link
    assert a not in _digests(d.user_id)
    # one link table: the same donor id every time (needed to withdraw)
    l1.link(d.user_id, dt.date(2026, 5, 1), np.random.default_rng(9))
    assert l1.get(d.user_id)["donor_id"] == a


def test_dedup_key_needs_the_secret_key():
    k1 = D.dedup_key(b"k1", "coros-partner", "476123")
    assert k1 == D.dedup_key(b"k1", "coros-partner", "476123")
    assert k1 != D.dedup_key(b"k2", "coros-partner", "476123")
    assert k1 not in _digests("476123") | _digests("coros-partner:476123")


def test_splits_carry_no_donor_or_activity_id(demo):
    donor, tracks, _ = demo
    ex, _ = _export(donor, tracks)
    txt = json.dumps(ex.splits)
    for s in ex.summaries:
        assert s["donor_id"] not in txt and s["activity_key"] not in txt


# ---------------------------------------------------------------------------
# k threshold on the A layer
# ---------------------------------------------------------------------------

def _p(sex, band, v=3.0):
    return {"sex": sex, "age_band": band, "easy_flat_speed_ms": v, "uphill_vam_mh": 600.0, "drift_pct": 3.0}


def test_params_k_internal_5_public_10():
    assert D.aggregate_params([_p("male", "30-39")] * 4) == []
    out = D.aggregate_params([_p("male", "30-39", v) for v in (2.8, 2.9, 3.0, 3.1, 3.2)])
    assert out[0]["n"] == 5 and out[0]["easy_flat_speed_ms"]["median"] == 3.0
    assert D.aggregate_params([_p("male", "30-39")] * 9, "public") == []
    assert D.aggregate_params([_p("male", "30-39")] * 10, "public")[0]["n"] == 10


def test_small_age_bands_merge_upwards():
    rows = [_p("male", "30-39")] * 6 + [_p("male", "50-59")] * 3 + [_p("male", "60+")] * 2
    out = D.aggregate_params(rows)
    assert {(r["age_band"], r["n"]) for r in out} == {("30-39", 6), ("50+", 5)}


def test_sex_below_k_gives_only_the_all_people_row():
    rows = [_p("male", "30-39")] * 6 + [_p("female", "30-39")] * 3
    out = D.aggregate_params(rows)
    assert [(r["sex"], r["age_band"], r["n"]) for r in out] == [("all", "all", 9)]   # no "male" row to subtract


def test_a_field_with_fewer_than_k_values_is_hidden():
    rows = [_p("male", "30-39") for _ in range(6)]
    for r in rows[:2]:
        r["drift_pct"] = None
    rows[2]["drift_pct"] = None                          # 3 values left
    out = D.aggregate_params(rows)
    assert out[0]["drift_pct"] is None and out[0]["easy_flat_speed_ms"] is not None


# ---------------------------------------------------------------------------
# source allow-list (§8; §11 Q10 not decided yet: a replaceable table)
# ---------------------------------------------------------------------------

def _with_source(tracks, source):
    return [dataclasses.replace(t, source=source) for t in tracks]


@pytest.mark.parametrize("source", ["strava", "strava-export", "intervals:STRAVA", "trainingpeaks", "unknown-x"])
def test_refused_sources_never_reach_the_pool(demo, source):
    donor, tracks, _ = demo
    ex, links = _export(donor, _with_source(tracks[:6], source))
    assert ex.summaries == ex.splits == ex.daily == [] and ex.params is None and ex.cells == {}
    assert ex.excluded == {source: 6} and links.rows == {}


def test_coros_mcp_feeds_a_only_until_coros_answers(demo):
    donor, tracks, _ = demo
    ex, _ = _export(donor, _with_source(tracks, "coros-mcp"))
    assert ex.summaries == ex.splits == ex.daily == []
    assert ex.params is not None and ex.params["sex"] == "male"


def test_garmin_source_needs_attribution(demo):
    donor, tracks, _ = demo
    ex, _ = _export(donor, _with_source(tracks[:3], "intervals:GARMIN_CONNECT"))
    assert ex.summaries and ex.attributions == {D.GARMIN_ATTRIBUTION}
    ex2, _ = _export(donor, _with_source(tracks[:3], "manual"))
    assert ex2.attributions == set()


def test_unofficial_coros_sync_only_for_the_owner(demo):
    donor, tracks, _ = demo
    t = _with_source(tracks[:3], "coros-unofficial")
    assert _export(donor, t)[0].summaries == []
    assert _export(dataclasses.replace(donor, owner=True), t)[0].summaries


def test_source_policy_is_configurable(demo):
    donor, tracks, _ = demo
    assert D.SOURCE_RULES_PENDING                        # the user has not answered §11 Q10
    rules = {**D.DEFAULT_SOURCE_RULES, "coros-mcp": D.SourceRule(True, True)}
    ex, _ = _export(donor, _with_source(tracks[:3], "coros-mcp"), policy=D.SourcePolicy(rules))
    assert len(ex.summaries) == 3
    ex2, _ = _export(donor, tracks[:3], policy=D.SourcePolicy({}))       # an empty list refuses all
    assert ex2.summaries == [] and ex2.params is None


# ---------------------------------------------------------------------------
# consent layers, daily load, withdrawal, retention
# ---------------------------------------------------------------------------

def test_without_b_consent_only_a_parameters(demo):
    donor, tracks, _ = demo
    ex, links = _export(dataclasses.replace(donor, consent_b=False), tracks)
    assert ex.summaries == ex.splits == ex.daily == [] and links.rows == {}
    assert ex.params is not None and set(ex.params) == {"sex", "age_band", "easy_flat_speed_ms", "uphill_vam_mh",
                                                        "drift_pct"}
    ex0, _ = _export(dataclasses.replace(donor, consent_a=False, consent_b=False), tracks)
    assert ex0.params is None and ex0.cells == {} and ex0.summaries == []


def test_daily_load_every_day_and_injury_breaks_of_7_days():
    trs = [_track(_north(HOME, 5), _utc(2026, 5, d)) for d in (1, 3, 20)]
    d = _donor(injuries=[(dt.date(2026, 5, 6), dt.date(2026, 5, 14)), (dt.date(2026, 5, 16), dt.date(2026, 5, 18))])
    ex, links = _export(d, trs)
    day0 = links.get(d.user_id)["day0"]
    rel = {r["rel_day"]: r for r in ex.daily}
    assert len(ex.daily) == 20                            # 2 May (local) .. 21 May, rest days as zero rows
    assert rel[(dt.date(2026, 5, 10) - day0).days]["injury_off"]           # inside the 9-day break
    assert not rel[(dt.date(2026, 5, 17) - day0).days]["injury_off"]       # the 3-day break is too short
    assert sum(r["tss"] > 0 for r in ex.daily) == 3


def test_withdraw_deletes_linked_rows_and_the_link_keeps_splits(demo):
    donor, tracks, _ = demo
    other = dataclasses.replace(donor, user_id="someone-else")
    links = D.LinkTable()
    pool = D.Pool()
    for i, dn in enumerate((donor, other)):
        pool.add(D.export_donor(dn, tracks[:4], links, pool_key=KEY + bytes([i]), rng=np.random.default_rng(i)))
    n_splits = len(pool.splits)
    gone = D.withdraw(pool, links, donor.user_id)
    assert gone["summaries"] == 4 and gone["daily"] > 0
    assert links.get(donor.user_id) is None and links.get(other.user_id) is not None
    assert {s["donor_id"] for s in pool.summaries} == {links.get(other.user_id)["donor_id"]}
    assert len(pool.splits) == n_splits                  # no id: kept (the terms say so, §7.3)


def test_pool_add_drops_duplicate_activities():
    tr = _track(_north(HOME, 5), _utc(2026, 5, 1), account={"platform_activity_id": "476123"})
    links = D.LinkTable()
    pool = D.Pool()
    for _ in range(2):
        pool.add(D.export_donor(_donor(), [tr], links, pool_key=KEY, rng=np.random.default_rng(0)))
    assert len(pool.summaries) == 1


def test_retention_3_years_uses_the_link_table():
    trs = [_track(_north(HOME, 5), _utc(y, 5, 1)) for y in (2022, 2025)]
    links = D.LinkTable()
    pool = D.Pool()
    pool.add(D.export_donor(_donor(), trs, links, pool_key=KEY, rng=np.random.default_rng(0)))
    n_daily = len(pool.daily)
    n = D.expire(pool, links, today=dt.date(2026, 10, 13))
    assert len(pool.summaries) == 1 and n == 1 + n_daily - len(pool.daily)
    day0 = links.get("u-1")["day0"]
    cut = dt.date(2026, 10, 13) - dt.timedelta(days=round(365.25 * 3))
    assert pool.daily and all(day0 + dt.timedelta(days=r["rel_day"]) >= cut for r in pool.daily)
    assert "rel_day" not in pool.splits[0] if pool.splits else True      # splits have no day to expire by
