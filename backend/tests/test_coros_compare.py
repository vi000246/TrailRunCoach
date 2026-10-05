"""「app 和手錶數值對照」 and the COROS account's values over time (engine/coros_compare.py,
sync/coros_client.store_hr_profile, api/plan.hr_profile_view; SP-67): the 5 bpm reminder,
what the card tells the user to change and where, the zones of the 課表's model, one history
entry per change. Synthetic data only — an in-memory DB, no COROS call."""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Athlete, Base
from backend.engine import coros_compare as CC
from backend.engine import hr_profile as HP
from backend.engine.planning import Plan, Threshold
from backend.i18n import use_locale
from backend.scripts.i18n_extract import has_cjk
from backend.settings.repository import SettingsRepository
from backend.sync import coros_client
from backend.tests.test_hr_profile import ACCOUNT_DATA, TODAY, _ds, _hr_run

TAIPEI = dt.timezone(dt.timedelta(hours=8))


def _account(lthr=152, max_hr=185, rest_hr=53, at="2026-10-04T12:54:00+00:00"):
    """The parsed COROS account as the settings store returns it (JSON: lists, with "at")."""
    data = copy.deepcopy(ACCOUNT_DATA)
    data["zoneData"].update(lthr=lthr, maxHr=max_hr, rhr=rest_hr)
    data.update(maxHr=max_hr, rhr=rest_hr)
    return json.loads(json.dumps({**HP.parse_account(data), "at": at}))


def _card(lthr=160, mhr=189, rhr=None, acc=None, model="lthr", history=None):
    """The card for an app LTHR / manual max HR (None = the watch's) / manual rest HR."""
    acc = acc or _account()
    mx = ({"value": float(mhr), "kind": "manual", "source": "你的設定 2026-10-04"} if mhr
          else {"value": float(acc["max_hr"]), "kind": "coros", "source": "來自手錶（COROS 帳號）"})
    rs = ({"value": float(rhr), "kind": "manual", "source": "你的設定 2026-10-04"} if rhr
          else {"value": float(acc["rest_hr"]), "kind": "coros", "source": "來自手錶（COROS 帳號）"})
    hrz = HP.plan_hr_zones(lthr, None, False, mx["value"], rs["value"], model, acc)
    return CC.compare(lthr, "自動估算", mx, rs, hrz, acc, history, TAIPEI)


def _by(card):
    return {r["id"]: r for r in card["rows"]}


# ---------------------------------------------------------------------------
# the reminder
# ---------------------------------------------------------------------------

def test_the_owners_numbers_lthr_and_zones_are_flagged_max_hr_is_not():
    # SP-67: app LTHR 160 (自動估算) / max 189 (設定) against the watch's 152 / 185 / 53
    c = _card()
    r = _by(c)
    assert (r["lthr"]["app"], r["lthr"]["watch"], r["lthr"]["diff"], r["lthr"]["alert"]) == (160, 152, 8, True)
    assert (r["max_hr"]["diff"], r["max_hr"]["alert"]) == (4, False)            # 189 vs 185
    assert r["rest_hr"]["from_watch"] and r["rest_hr"]["diff"] == 0 and not r["rest_hr"]["alert"]
    assert r["lthr"]["app_source"] == "自動估算"
    # the zones of the 課表's model, the watch's computed as COROS does (ratios × its own LTHR)
    z = c["zones"]
    assert z["model"] == "lthr" and z["alert"]
    assert [x["app"][1] for x in z["rows"][:5]] == [128, 144, 152, 163, 170]
    assert [x["watch"][1] for x in z["rows"][:5]] == [122, 137, 144, 155, 161]
    assert [x["diff"] for x in z["rows"]] == [6, 7, 8, 8, 9, None]
    assert c["alert"] and c["alert_bpm"] == 5
    assert c["summary"] == "LTHR、心率區間差超過 5 bpm，建議到 COROS app 手動改成 app 的數值"
    # what to do: the LTHR can't be typed in, so the app's five edges; and where
    assert len(c["fixes"]) == 1 and "128／144／152／163／170" in c["fixes"][0] and "152" in c["fixes"][0]
    assert "還沒實測" in c["fixes"][0]
    assert "個人頁 → 設定 → 心率區間" in c["where"]
    assert "直接送 bpm" in c["note"] and c["read_day"] == "2026-10-04"


def test_exactly_5_bpm_is_quiet_6_is_not():
    # owner 2026-10-05: 「差距超過 5 bpm 就提醒」
    r = _by(_card(lthr=157, mhr=190))
    assert (r["lthr"]["diff"], r["lthr"]["alert"]) == (5, False)
    assert (r["max_hr"]["diff"], r["max_hr"]["alert"]) == (5, False)
    r = _by(_card(lthr=146, mhr=191))
    assert (r["lthr"]["diff"], r["lthr"]["alert"]) == (-6, True)               # the watch higher counts too
    assert (r["max_hr"]["diff"], r["max_hr"]["alert"]) == (6, True)


def test_within_5_says_nothing_to_change():
    c = _card(lthr=154, mhr=None)
    assert not c["alert"] and c["fixes"] == [] and not c["zones"]["alert"]
    assert c["summary"] == "兩邊的差距都在 5 bpm 以內，不用改"
    assert _by(c)["max_hr"]["from_watch"] and _by(c)["max_hr"]["diff"] == 0


def test_max_and_rest_hr_say_the_number_to_type_in():
    c = _card(lthr=152, mhr=196, rhr=45, acc=_account(max_hr=185, rest_hr=53))
    assert [r["id"] for r in c["rows"] if r["alert"]] == ["max_hr", "rest_hr"]
    assert c["fixes"] == ["最大心率：把 COROS 的 185 改成 196", "靜息心率：把 COROS 的 53 改成 45"]
    assert c["summary"].startswith("最大心率、靜息心率差超過 5 bpm")


def test_hrr_model_compares_the_watchs_hrr_zones():
    # 課表心率區間 = 儲備心率: the app's 189 / 53 against the watch's 202 / 53
    c = _card(lthr=152, mhr=189, acc=_account(max_hr=202), model="hrr")
    z = c["zones"]
    assert z["model"] == "hrr" and z["alert"]
    assert [x["watch"][1] for x in z["rows"][:5]] == [141, 163, 178, 184, 195]   # = the account's rhrZone
    assert [x["app"][1] for x in z["rows"][:2]] == [133, 154]                    # research §3: Z2 133–154
    assert any("把 COROS 的 202 改成 189" in f for f in c["fixes"])
    assert any(f.startswith("心率區間（COROS 儲備心率）") for f in c["fixes"])
    assert not any(f.startswith("LTHR") for f in c["fixes"])


def test_missing_values_are_shown_not_flagged():
    acc = _account()
    acc["lthr"] = None                                   # an account without an LTHR
    c = CC.compare(160, "自動估算", {"value": None}, {"value": None},
                   HP.plan_hr_zones(160, None, False, None, None, "lthr", acc), acc)
    r = _by(c)
    assert r["lthr"]["watch"] is None and r["lthr"]["diff"] is None and not r["lthr"]["alert"]
    assert r["max_hr"]["app"] is None and not r["max_hr"]["alert"]
    assert c["zones"]["rows"] == [] and "沒辦法比" in c["zones"]["reason"] and not c["alert"]
    # no LTHR in the app yet (no runs): no zones at all
    c = CC.compare(None, None, None, None, None, acc)
    assert c["zones"] is None and _by(c)["lthr"]["app"] is None and not c["alert"]


def test_no_coros_account_no_card():
    assert CC.compare(160, "自動估算", {"value": 189.0}, {"value": 53.0}, None, None) is None
    assert CC.view(None, {}, {}, None) is None


def test_english_card_has_no_chinese():
    with use_locale("en"):
        c = _card(history=[_account(lthr=182, max_hr=202, at="2026-06-13T01:00:00+00:00"), _account()])
    texts = [c["summary"], c["where"], c["note"], *c["fixes"], c["zones"]["label"],
             *(r["name"] for r in c["rows"]), *(r["text"] for r in c["history"]["rows"])]
    assert all(t and not has_cjk(t) for t in texts), [t for t in texts if has_cjk(t)]
    assert c["summary"].startswith("LTHR, HR zone: more than 5 bpm apart")     # 「心率區間」 = the catalog's "HR zone"
    assert "128 / 144 / 152 / 163 / 170" in c["fixes"][0]
    assert c["history"]["rows"][0]["text"] == "LTHR 182 → 152, Max HR 202 → 185"


# ---------------------------------------------------------------------------
# the account's values over time
# ---------------------------------------------------------------------------

def test_history_gets_one_entry_per_change():
    a = {**HP.parse_account(ACCOUNT_DATA), "at": "2026-10-03T01:00:00+00:00"}      # tuples, as parsed
    h = CC.history_add(None, a)
    assert [(r["at"], r["lthr"], r["max_hr"], r["rest_hr"]) for r in h] == [("2026-10-03T01:00:00+00:00", 182, 202, 53)]
    assert set(h[0]) == {"at", *CC.TRACKED}
    h = json.loads(json.dumps(h))                                                  # as stored
    # the same values read again (a later sync): nothing to write
    assert CC.history_add(h, {**a, "at": "2026-10-03T09:00:00+00:00"}) is None
    b = _account(lthr=152, max_hr=185)
    h2 = CC.history_add(h, b)
    assert [r["lthr"] for r in h2] == [182, 152] and h2[-1]["at"] == b["at"]
    # an edited zone table is a change too
    c = copy.deepcopy(b)
    c["ratios"]["lthr"][0] = 0.82
    c["at"] = "2026-10-06T00:00:00+00:00"
    assert len(CC.history_add(h2, c)) == 3
    # only the newest HISTORY_MAX are kept
    many = [{**b, "lthr": 100 + i} for i in range(CC.HISTORY_MAX)]
    out = CC.history_add(many, b)
    assert len(out) == CC.HISTORY_MAX and out[0]["lthr"] == 101 and out[-1]["lthr"] == 152


def test_history_starts_with_the_profile_stored_before_it_existed():
    old, new = _account(lthr=182, max_hr=202, at="2026-10-03T01:00:00+00:00"), _account()
    h = CC.history_add(None, new, old)
    assert [(r["at"][:10], r["lthr"]) for r in h] == [("2026-10-03", 182), ("2026-10-04", 152)]
    assert len(CC.history_add(None, {**old, "at": "2026-10-05T00:00:00+00:00"}, old)) == 1   # unchanged: one entry
    assert len(CC.history_add(None, new, {})) == 1                                          # first login ever


def test_history_view_is_newest_first_with_what_changed():
    hist = [_account(lthr=182, max_hr=202, at="2026-06-13T01:00:00+00:00"),
            _account(lthr=152, max_hr=185, at="2026-10-04T16:30:00+00:00"),               # 10/5 00:30 in Taipei
            {**_account(at="2026-10-08T02:00:00+00:00"), "hr_zone_type": 1}]
    v = CC.history_view(hist, TAIPEI)
    assert v["n"] == 3
    assert [(r["day"], r["lthr"], r["max_hr"], r["rest_hr"]) for r in v["rows"]] == [
        ("2026-10-08", 152, 185, 53), ("2026-10-05", 152, 185, 53), ("2026-06-13", 182, 202, 53)]
    assert [r["text"] for r in v["rows"]] == ["區間設定改過", "LTHR 182 → 152、最大心率 202 → 185", "開始記錄"]
    assert v["last_change"] == {"day": "2026-10-08", "text": "區間設定改過"}
    one = CC.history_view(hist[:1], TAIPEI)
    assert one["last_change"] is None and one["rows"][0]["text"] == "開始記錄"
    assert CC.history_view(None) == {"rows": [], "n": 0, "last_change": None}
    assert len(CC.history_view([_account(lthr=100 + i) for i in range(30)])["rows"]) == CC.HISTORY_SHOWN


def test_history_setting_is_validated():
    CC.validate(None)
    CC.validate([{"at": "x"}])
    for bad in ({}, "x", [1]):
        with pytest.raises(ValueError):
            CC.validate(bad)


# ---------------------------------------------------------------------------
# the sync writes it; the settings page reads it
# ---------------------------------------------------------------------------

def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    s = async_sessionmaker(engine, expire_on_commit=False)()
    s.add(Athlete(id=1, name="tester", data_dir="x"))
    await s.commit()
    return s


def test_every_sync_reads_the_account_and_a_change_adds_an_entry():
    async def go():
        db = await _db()
        repo = SettingsRepository(db, 1)
        assert await repo.get(CC.HISTORY_KEY) is None
        await coros_client.store_hr_profile(db, 1, ACCOUNT_DATA)
        await db.commit()
        first = await repo.get(CC.HISTORY_KEY)
        await coros_client.store_hr_profile(db, 1, ACCOUNT_DATA)                 # the next sync: same values
        await db.commit()
        same = await repo.get(CC.HISTORY_KEY)
        changed = copy.deepcopy(ACCOUNT_DATA)
        changed["zoneData"].update(lthr=152, maxHr=185)
        await coros_client.store_hr_profile(db, 1, changed)
        await db.commit()
        assert await coros_client.store_hr_profile(db, 1, {"zoneData": {}}) is None   # no HR part: untouched
        return first, same, await repo.get(CC.HISTORY_KEY), await repo.get(HP.ACCOUNT_KEY)
    first, same, hist, acc = run(go())
    assert len(first) == 1 and same == first
    assert [(r["lthr"], r["max_hr"], r["rest_hr"]) for r in hist] == [(182, 202, 53), (152, 185, 53)]
    assert acc["lthr"] == 152 and hist[-1]["at"] == acc["at"]                     # the latest stays where it was


def test_an_account_stored_before_the_history_seeds_it():
    async def go():
        db = await _db()
        repo = SettingsRepository(db, 1)
        await repo.set(HP.ACCOUNT_KEY, _account(lthr=182, max_hr=202, at="2026-10-03T01:00:00+00:00"))
        changed = copy.deepcopy(ACCOUNT_DATA)
        changed["zoneData"].update(lthr=152, maxHr=185)
        await coros_client.store_hr_profile(db, 1, changed)
        await db.commit()
        return await repo.get(CC.HISTORY_KEY)
    hist = run(go())
    assert [(r["at"][:10], r["lthr"]) for r in hist][0] == ("2026-10-03", 182) and hist[1]["lthr"] == 152


def test_settings_page_gets_the_card_with_the_app_values(monkeypatch):
    from backend.api import plan as PL
    from backend.engine.wko5expr import datasource
    runs = [_hr_run(TODAY - dt.timedelta(days=i * 10), p) for i, p in enumerate((180.0, 186.0, 188.0))]
    ds = _ds(runs, Plan(thresholds=[Threshold("2026-09-20", mhr=189)]), lthr=160.0)
    ds.aethr = lambda w: None
    ds.cp = lambda w: None
    ds.settings_from = "app"
    ds.setting_label = lambda name, default="": "自動估算"
    acc = _account()
    hist = [_account(lthr=182, max_hr=202, at="2026-06-13T01:00:00+00:00"), acc]
    monkeypatch.setattr(HP, "account", lambda user_id=1: acc)
    monkeypatch.setattr(datasource, "read_setting",
                        lambda key, default=None, user_id=1: hist if key == CC.HISTORY_KEY else default)
    c = PL.hr_profile_view(ds, TODAY)["compare"]
    r = _by(c)
    assert (r["lthr"]["app"], r["lthr"]["app_source"], r["lthr"]["watch"], r["lthr"]["alert"]) == (160, "自動估算", 152, True)
    assert (r["max_hr"]["app"], r["max_hr"]["watch"], r["max_hr"]["alert"]) == (189, 185, False)
    assert r["max_hr"]["app_source"] == "你的設定 2026-09-20"
    assert r["rest_hr"]["from_watch"] and c["zones"]["model"] == "lthr" and c["zones"]["alert"]
    assert c["history"]["n"] == 2 and "LTHR 182 → 152" in c["history"]["last_change"]["text"]
    # not connected to COROS: no card
    monkeypatch.setattr(HP, "account", lambda user_id=1: None)
    assert PL.hr_profile_view(ds, TODAY)["compare"] is None
