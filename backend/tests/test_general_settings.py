"""一般設定＋首次精靈 (generalize-athlete plan B2): engine/athlete_profile.py,
the profile API (birth year, power source, setup), 使用功率 auto, 推課表到手錶
only with COROS, the sex / pack defaults. Synthetic data only; plan.json in tmp."""
from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import athlete_profile as AP
from backend.engine import planning as PL

TODAY = dt.date(2026, 10, 1)


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)()


@pytest.fixture
def plan_file(tmp_path, monkeypatch):
    p = tmp_path / "plan.json"
    monkeypatch.setattr(PL.Plan.load.__func__, "__defaults__", (p,))
    monkeypatch.setattr(PL.Plan.save, "__defaults__", (p,))
    from backend.api import plan as PA
    monkeypatch.setattr(PA, "ATHLETE_DIR", tmp_path / "no-wko5")
    monkeypatch.setattr(PA, "_notify", lambda thresholds: None)
    PA._wko5_profile.cache_clear()
    yield p
    PA._wko5_profile.cache_clear()


# ---- athlete_profile ------------------------------------------------------------

def test_age_birth_year_and_legacy_meter():
    assert AP.age({"birth_year": 1986}, TODAY) == 40 and AP.age({}, TODAY) is None
    assert AP.birth_year_ok(None) and AP.birth_year_ok(1986, TODAY)
    assert not AP.birth_year_ok(1900, TODAY) and not AP.birth_year_ok(TODAY.year - 5, TODAY)
    assert AP.profile_power_source({"power_source": "none", "power_meter": "stryd"}) == "none"
    assert AP.profile_power_source({"power_meter": "coros"}) == "watch"
    assert AP.profile_power_source({"power_meter": "stryd"}) == "stryd"
    assert AP.profile_power_source({}) is None


def _ds(labels):
    from backend.engine.wko5expr.dataset import date_to_day
    ws = [SimpleNamespace(sport="run", day=date_to_day(TODAY - dt.timedelta(days=i + 1)), idx=i, lab=l)
          for i, l in enumerate(labels)]
    return SimpleNamespace(workouts=ws, memo={}, power_source=lambda w: w.lab)


def test_detect_power_source():
    assert AP.detect_power_source(_ds(["stryd"] * 5 + ["none"] * 9), TODAY)["source"] == "stryd"
    assert AP.detect_power_source(_ds(["watch"] * 6 + ["stryd"] * 4), TODAY)["source"] == "watch"
    d = AP.detect_power_source(_ds(["none"] * 20 + ["watch"] * 2), TODAY)
    assert d["source"] == "none" and d["counts"] == {"stryd": 0, "watch": 2, "none": 20}
    assert AP.effective_power_source({}, _ds(["stryd"] * 5), TODAY) == ("stryd", "自動偵測")
    assert AP.effective_power_source({"power_source": "none"}, None) == ("none", "設定")


def test_use_power_follows_the_owner_decision():
    assert AP.use_power("stryd", False) is True
    assert AP.use_power("watch", False) is False        # no Stryd -> heart rate by default
    assert AP.use_power("watch", True) is True          # 進階: watch power accepted
    assert AP.use_power("none", True) is False
    # SP-211: weight, sex and age (the birth year) — the wizard asks until all three are known
    assert AP.setup_needed(None, "male", 1990) and AP.setup_needed(60.0, None, 1990)
    assert AP.setup_needed(60.0, "female", None) and not AP.setup_needed(60.0, "female", 1990)
    assert AP.setup_missing(None, None, None) == ["weight", "sex", "age"]
    assert AP.setup_missing(60.0, "other", 1990) == ["sex"]


# ---- profile API ------------------------------------------------------------------

def test_profile_api_birth_year_power_source_and_setup(plan_file):
    from fastapi import HTTPException
    from backend.api import plan as PA
    g = PA.get_profile()
    assert g["setup"]["needed"] is True and g["effective"]["weight"] is None
    assert g["effective"]["power_source"] is None and "power_source" in g["options"]
    r = PA.put_profile(PA.ProfileIn(weights=[PA.WeightIn(date="2026-09-01", kg=58.0)], sex="female",
                                    height_cm=163, birth_year=1990, power_source="none"))
    e = r["effective"]
    assert e["sex"] == "female" and e["birth_year"] == 1990 and e["age"] == dt.date.today().year - 1990
    assert e["power_source"] == "none" and r["setup"]["needed"] is False
    with pytest.raises(HTTPException):
        PA.put_profile(PA.ProfileIn(birth_year=1850))
    with pytest.raises(HTTPException):
        PA.put_profile(PA.ProfileIn(power_source="garmin"))
    # the legacy field still reads
    PA.put_profile(PA.ProfileIn(power_meter="coros"))
    assert PA.get_profile()["effective"]["power_source"] == "watch"


def test_setup_done_is_stored():
    from backend.api import plan as PA
    from backend.settings.repository import SettingsRepository

    async def go():
        s = await _session()
        assert (await PA.setup_done(PA.SetupIn(done=True), db=s)) == {"done": True, "later": False}
        assert await SettingsRepository(s, 1).get(AP.SETUP_DONE_KEY) is True
    _run(go())


def test_use_power_auto_in_sync_settings(monkeypatch, plan_file):
    from backend.api import sync as SY
    from backend.settings.repository import SettingsRepository
    PL.Plan(profile={"power_source": "watch"}).save()

    async def go():
        s = await _session()
        repo = SettingsRepository(s, 1)
        out = await SY._sync_settings(repo)
        assert out["use_power_stored"] is None and out["use_power"] is False and out["power_source"] == "watch"
        await repo.set("power.accept_watch_power", True)
        assert (await SY._sync_settings(repo))["use_power"] is True
        await repo.set("charts.power.enabled", False)                  # an explicit choice wins
        assert (await SY._sync_settings(repo))["use_power"] is False
        PL.Plan(profile={"power_source": "stryd"}).save()
        await repo.set("charts.power.enabled", None)
        assert (await SY._sync_settings(repo))["use_power"] is True
    _run(go())


# ---- 推課表到手錶 only with COROS --------------------------------------------------

def test_auto_push_follows_coros_login():
    from backend.db.models import SyncState
    from backend.engine import plan_auto as PA

    async def go():
        s = await _session()
        cfg = await PA.settings(s)
        assert cfg["coros_logged_in"] is False and cfg["push"] is False and cfg["notify"] == "overview"
        s.add(SyncState(athlete_id=1, coros_access_token="sealed"))
        await s.commit()
        cfg = await PA.settings(s)
        assert cfg["coros_logged_in"] is True and cfg["push"] is True and cfg["notify"] == "watch"
        from backend.settings.repository import SettingsRepository
        await SettingsRepository(s, 1).set("plan.auto.push", False)
        assert (await PA.settings(s))["push"] is False                  # an explicit choice wins
    _run(go())


# ---- sex and pack defaults ---------------------------------------------------------

def test_sex_or_default():
    from backend.engine import cp_protocols as CPP
    mk = lambda prof, wk=None: SimpleNamespace(plan=SimpleNamespace(profile=prof),
                                               athlete=SimpleNamespace(root={3001: {3017: wk}} if wk else {}))
    assert CPP.sex_or_default(mk({"sex": "female"})) == ("female", "設定頁")
    assert CPP.sex_or_default(mk({}, "female")) == ("female", "WKO5")
    assert CPP.sex_or_default(mk({})) == ("male", "預設（男，推估）")


def test_pack_default_follows_body_weight():
    from backend.engine.racepower import capacity as CAP
    from backend.engine import steep_hill as SH
    assert CAP.pack_default(68.0) == 9.0                 # 68 kg: the old 9 kg default
    assert CAP.pack_default(52.0) == 7.0                 # 6.76 -> 7.0
    assert CAP.pack_default(None, 3) == CAP.PACK_DEFAULT_MULTI
    assert "13 %" in CAP.pack_default_text(60.0) and "9 kg" in CAP.pack_default_text(None)
    assert SH.trip_kg({"days": 2}, 52.0) == 7.0 and SH.trip_kg({"pack_kg": 11}, 52.0) == 11.0


def test_body_profile_age_from_birth_year():
    from backend.engine.racepower import athlete as A
    ds = SimpleNamespace(plan=SimpleNamespace(profile={"birth_year": 1980, "sex": "female"}), athlete=None)
    b = A.body_profile(ds, TODAY)
    assert b["age"] == 46 and b["age_src"].startswith("設定頁") and b["sex"] == "female"


# ---- 首次精靈: 性別、年齡、體重 (SP-211) --------------------------------------------------

def test_age_is_asked_and_stored_as_the_birth_year(plan_file):
    from fastapi import HTTPException
    from backend.api import plan as PA
    from backend.engine.localtime import today_local
    g = PA.get_profile()
    assert g["setup"]["missing"] == ["weight", "sex", "age"]
    r = PA.put_profile(PA.ProfileIn(weights=[PA.WeightIn(date="2026-09-01", kg=61.0)], sex="male"))
    assert r["setup"]["needed"] is True and r["setup"]["missing"] == ["age"]      # age still missing
    r = PA.put_profile(PA.ProfileIn(weights=[PA.WeightIn(date="2026-09-01", kg=61.0)], sex="male", age=37))
    year = today_local().year
    assert r["profile"]["birth_year"] == year - 37 and r["effective"]["age"] == 37
    assert r["setup"]["needed"] is False and r["setup"]["missing"] == []
    # a birth year given wins over an age
    r = PA.put_profile(PA.ProfileIn(sex="male", birth_year=1980, age=20))
    assert r["profile"]["birth_year"] == 1980
    for bad in (9, 101):
        with pytest.raises(HTTPException) as ei:
            PA.put_profile(PA.ProfileIn(age=bad))
        assert ei.value.status_code == 400
    assert AP.birth_year_of_age(40, TODAY) == 1986 and AP.age({"birth_year": 1986}, TODAY) == 40
    assert AP.age_ok(None) and AP.age_ok(10) and not AP.age_ok(True) and not AP.age_ok(101)


def test_wizard_requires_sex_age_weight_and_explains_them():
    import json
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "static"
    js = (static / "setup_wizard.js").read_text("utf-8")
    for name in ("sex", "age", "kg"):
        assert f'name="{name}"' in js
    assert 'name="by"' not in js and "birth_year" not in js.split("const body")[1]   # the age is sent
    for k in ("need_sex", "need_age", "need_weight"):
        assert f'err("{k}"' in js
    keys = set(__import__("re").findall(r'T\("(\w+)"', js))
    for loc in ("zh-TW", "en"):
        cat = json.loads((static / "i18n" / loc / "common.json").read_text("utf-8"))
        missing = [k for k in keys if not cat.get(f"setup.{k}")]
        assert not missing, (loc, missing)
    assert {"sex_why", "age_why", "weight_why"} <= keys                 # what each is used for, not a hover
    s = (static / "settings.html").read_text("utf-8")
    assert 'id="p-age"' in s and 'id="p-birth"' not in s and "age: $(\"p-age\").value" in s


def test_later_skips_the_wizard_for_a_week_then_reminds():
    now = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc)
    assert AP.setup_remind(True, None, now) and not AP.setup_remind(False, None, now)
    assert not AP.setup_remind(True, (now - dt.timedelta(days=6)).isoformat(), now)
    assert AP.setup_remind(True, (now - dt.timedelta(days=AP.REMIND_DAYS)).isoformat(), now)
    assert AP.setup_remind(True, "garbage", now)


def test_setup_later_is_stored_and_the_old_done_no_longer_silences(plan_file, monkeypatch):
    from backend.api import plan as PA
    from backend.engine.wko5expr import datasource as DS
    from backend.settings.repository import SettingsRepository
    store = {AP.SETUP_DONE_KEY: True}                    # dismissed before SP-211
    monkeypatch.setattr(PA, "read_setting", lambda k, d=None, *a: store.get(k, d), raising=False)
    monkeypatch.setattr(DS, "read_setting", lambda k, d=None, *a: store.get(k, d))
    g = PA.get_profile()["setup"]
    assert g["needed"] and g["remind"] and g["remind_days"] == AP.REMIND_DAYS      # asked again

    async def go():
        s = await _session()
        r = await PA.setup_done(PA.SetupIn(done=False, later=True), db=s)
        assert r == {"done": False, "later": True}
        return await SettingsRepository(s, 1).get(AP.SETUP_LATER_KEY)
    store[AP.SETUP_LATER_KEY] = _run(go())
    assert not PA.get_profile()["setup"]["remind"]       # a week of quiet


def test_wizard_later_button_snoozes():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "static" / "setup_wizard.js").read_text("utf-8")
    assert "prof.setup.remind" in js and "done(true)" in js and "later: !!later" in js
