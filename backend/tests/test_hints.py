"""提示分類與提示多寡 (SP-482; docs/research/ui-hint-audit.md §4–§5, docs/spec/ui-hints.spec.md):
engine/hints.py (the catalog, the hint object, week notes and calculator warnings, the automatic level,
「知道了」), the settings keys, the profile API and the calculator responses. Synthetic data only; the DB
in memory. The page-side filter (static/hints.js) is test_hints_js.py."""
from __future__ import annotations

import asyncio
import copy
import datetime as dt
import itertools
import pickle
import re
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.engine import hints as H

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "docs" / "research" / "ui-hint-audit.md"
SPEC = ROOT / "docs" / "spec" / "ui-hints.spec.md"
TODAY = dt.date(2026, 10, 13)


def _run(c):
    return asyncio.new_event_loop().run_until_complete(c)


async def _session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)()


# ---- the catalog = the audit's inventory, written down in docs/spec ------------------------------

def _audit_ids() -> list[str]:
    sec = AUDIT.read_text(encoding="utf-8").split("## 1. 盤點與分級")[1].split("## 2.")[0]
    out = []
    for line in sec.splitlines():
        m = re.match(r"\|\s*([A-Z]{2}\d+[a-z′]*)\s*\|", line)
        if m and line.count("|") >= 9:
            out.append(m.group(1))
    return out


def test_every_audit_id_has_a_class_three_displays_and_a_priority():
    ids = _audit_ids()
    assert len(ids) > 140 and "OV8b" in ids and "OV8a′" in ids and "RC3" in ids
    missing = [i for i in ids if i not in H.CATALOG]
    assert not missing, missing
    for e in H.CATALOG.values():
        assert e.cls in (1, 2, 3) and e.prio in range(1, 7)
        assert len(e.show) == 3 and set(e.show) <= set(H.SYMBOLS), e
    # SP-251 冷風 / SP-253 失溫 (implemented after the audit) are class 1 (§7 T1's note)
    for hid in ("RC3", "RC3w", "RC3h"):
        assert H.CATALOG[hid].cls == 1 and H.CATALOG[hid].show == "●●●" and H.CATALOG[hid].prio == 1


def test_the_spec_lists_every_id_with_the_catalogs_class_and_displays():
    rows = {}
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 9 and cells[0] in H.CATALOG:
            rows[cells[0]] = cells
    assert set(rows) == set(H.CATALOG)
    for hid, c in rows.items():
        e = H.CATALOG[hid]
        assert c[2].startswith(str(e.cls)), hid
        assert (c[3], c[4], c[5]) == tuple(e.show), hid
        assert c[6].startswith(str(e.prio)), hid
    assert "SP-482" in SPEC.read_text(encoding="utf-8").split("## Change History")[1]


def test_the_catalog_follows_the_audit_rows():
    """A sample of the audit's 類 / 完標精 columns, read from the doc itself."""
    sec = AUDIT.read_text(encoding="utf-8").split("## 1. 盤點與分級")[1].split("## 2.")[0]
    checked = 0
    for line in sec.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 8 or cells[0] not in H.CATALOG:
            continue
        cls_col, show_col = cells[5], cells[6]
        if re.fullmatch(r"[123]", cls_col):
            assert H.CATALOG[cells[0]].cls == int(cls_col), cells[0]
        m = re.match(r"^([●◐▷✕]{3})", show_col)
        if m and re.fullmatch(r"[123]", cls_col):
            assert H.CATALOG[cells[0]].show == m.group(1), cells[0]
            checked += 1
    assert checked > 100


# ---- the hint object ---------------------------------------------------------------------------

def test_make_carries_every_field():
    h = H.make("OV8b", "B 賽「X」落在 A 賽的減量期內", fp="abc")
    assert {k: h[k] for k in ("id", "cls", "prio", "smart", "text", "detail", "show", "ack", "fp")} == {
        "id": "OV8b", "cls": 1, "prio": 3, "smart": True, "text": "B 賽「X」落在 A 賽的減量期內", "detail": None,
        "show": "●●●", "ack": True, "fp": "abc"}
    assert H.make("OV8a", "疼痛紅燈")["ack"] is False                 # a class-1 row without the flag
    assert H.make("OV8d", "解釋")["ack"] is True                       # every class 2 / 3 hint
    # a class-1 case of a class-2 row is shown in every level
    low = H.make("OV6", "LTHR 可信度低", cls=1)
    assert low["cls"] == 1 and low["show"] == "●●●" and low["ack"] is False
    assert H.make("OV6", "可信度中")["show"] == "●◐◐"
    # the default fingerprint follows the condition's text
    assert H.make("OV8d", "a")["fp"] == H.make("OV8d", "a")["fp"] != H.make("OV8d", "b")["fp"]
    with pytest.raises(KeyError):
        H.make("ZZ9", "x")
    with pytest.raises(ValueError):
        H.make("OV8d", "x", cls=4)
    with pytest.raises(ValueError):
        H.make("OV8d", "x", prio=7)


def test_week_notes_info_watch_bad_get_their_class_and_priority():
    n = lambda **k: H.note({"text": "x：y", **k})
    red = n(level="bad", src="injury_light")
    assert (red["id"], red["cls"], red["prio"], red["level"], red["src"]) == ("OV8a", 1, 1, "bad", "injury_light")
    assert (n(level="info", src="injury")["id"], n(level="info", src="injury")["cls"]) == ("OV8a′", 2)
    b = n(level="watch", src="race", fp="f1")
    assert (b["id"], b["cls"], b["prio"], b["ack"], b["fp"]) == ("OV8b", 1, 3, True, "f1")
    assert n(level="info", src="race")["id"] == "OV8d"              # B / C race week shaping explains the plan
    assert (n(level="watch", src="intensity")["id"], n(level="watch", src="intensity")["cls"]) == ("OV8e", 1)
    assert (n(level="info", src="cold_start")["id"], n(level="info", src="cold_start")["show"]) == ("OV8f", "●◐✕")
    assert (n(level="info", src="easy_tss")["id"], n(level="info", src="easy_tss")["cls"]) == ("OV8i", 3)
    assert n(level="info", src="carb_load")["id"] == "OV8c"
    # no src: watch / bad = the copied indicator line (OV8k, class 3); info = a 課表偏好 line
    assert (n(level="watch")["id"], n(level="watch")["cls"], n(level="watch")["show"]) == ("OV8k", 3, "✕✕✕")
    assert n(level="info")["id"] == "OV8g"
    # an unknown src: the catch-all OV8, its class / priority from the level
    assert (n(level="bad", src="new")["cls"], n(level="bad", src="new")["prio"]) == (1, 4)
    assert (n(level="watch", src="new")["cls"], n(level="watch", src="new")["prio"]) == (2, 5)
    assert (n(level="info", src="new")["cls"], n(level="info", src="new")["prio"]) == (2, 6)
    # the week_start of the 課表's notes and other keys travel along; an already tagged note keeps its id
    assert n(level="info", src="prefs", week_start="2026-10-12")["week_start"] == "2026-10-12"
    assert H.note(H.note({"level": "watch", "src": "race", "text": "t"}))["id"] == "OV8b"
    assert H.notes(None) == []


def test_tagged_warnings_stay_strings_and_become_objects():
    w = H.tag("RC20", "預估脫水約 3.0% 體重")
    assert isinstance(w, str) and "脫水" in w and w == "預估脫水約 3.0% 體重"       # old string checks still work
    assert copy.deepcopy(w).hint_id == "RC20" and pickle.loads(pickle.dumps(w)).hint_id == "RC20"
    assert H.tag("RC20", w) is w
    with pytest.raises(KeyError):
        H.tag("RC99", "x")
    out = H.warnings([w, H.tag("RC28", "沒有體重", cls=1), "舊的存檔句子", {"id": "RC6", "text": "目標太快"},
                      {"text": "沒有 id 的物件"}, "舊的存檔句子"])
    assert [(h["id"], h["cls"]) for h in out] == [("RC20", 1), ("RC28", 1), ("RC", 2), ("RC6", 1), ("RC", 2)]
    assert out[2]["show"] == "●●●" and out[1]["show"] == "●●●"       # untagged: shown as before
    assert [H.text(x) for x in out[:3]] == ["預估脫水約 3.0% 體重", "沒有體重", "舊的存檔句子"]
    assert H.text("plain") == "plain" and H.text(None) == ""
    assert H.warning(out[0]) == out[0]                                 # an object stays as it is


def test_calc_respond_converts_every_warning_list():
    from backend.engine.racepower import calc as CALC
    out = CALC.respond({"warnings": [H.tag("RC14", "撐不住")], "course_warnings": ["點距中位數 80 m"],
                        "fuel": {"warnings": [H.tag("RC21", "沒有輸入補給站")], "kcal": 1}, "x": 1})
    assert out["warnings"][0]["id"] == "RC14" and out["warnings"][0]["cls"] == 1
    assert out["course_warnings"][0]["id"] == "RC" and out["fuel"]["warnings"][0]["id"] == "RC21"
    assert out["fuel"]["kcal"] == 1 and out["x"] == 1
    assert CALC.respond({"fuel": {"unavailable": "沒有體重"}}) == {"fuel": {"unavailable": "沒有體重"}}


def test_share_snapshot_reads_objects_and_old_strings():
    from backend.engine.racepower import share as SH
    p = {"type": "road", "summary": {"km": 10.0, "time_s": 2400.0}, "segments": [],
         "fuel": {"warnings": [H.make("RC28", "基礎代謝缺身高：用假設值"), "基礎代謝缺年齡", H.make("RC20", "預估脫水約 3%")]}}
    snap = SH.snapshot(p, title="x")
    assert [H.text(w) for w in snap["fuel"]["warnings"]] == ["預估脫水約 3%"]


def test_b_race_warnings_carry_a_condition_fingerprint():
    """R7: 「知道了」 on the B-race warning holds while the same races stay on the same days."""
    from backend.engine import planning as P
    from backend.engine import post_race as PRC

    def ev(name, date_, pri="B", **kw):
        return P.Event(id=name, name=name, date=date_, priority=pri, **kw)
    a = ev("a", "2026-11-07", "A", kind="road", distance_km=42.2, est_hours=4.5)
    b = ev("b", "2026-10-17", distance_km=21, climbing_m=800, est_hours=3.0)
    mon = dt.date(2026, 10, 12)
    one = PRC.b_hints([a, b], mon)
    assert one and all(n["src"] == "race" and n.get("fp") for n in one)
    assert [n["fp"] for n in PRC.b_hints([a, b], mon)] == [n["fp"] for n in one]       # same races, same fp
    moved = PRC.b_hints([a, ev("b", "2026-10-18", distance_km=21, climbing_m=800, est_hours=3.0)], mon)
    assert moved and {n["fp"] for n in moved}.isdisjoint({n["fp"] for n in one})       # moved: shows again
    longer = PRC.b_hints([a, ev("b", "2026-10-17", distance_km=30, climbing_m=800, est_hours=3.0)], mon)
    assert {n["fp"] for n in longer}.isdisjoint({n["fp"] for n in one})                 # another distance
    tagged = H.notes(one)
    assert all(h["id"] == "OV8b" and h["cls"] == 1 and h["ack"] and h["fp"] == n["fp"] for h, n in zip(tagged, one))


# ---- 提示多寡: the automatic level (§4.2) ---------------------------------------------------------

EXP_NEW = {"runs_per_week": 2, "minutes_per_run": 30, "longest_min": 40, "can_run_30": False, "at": "x"}
EXP_RUNNER = {"runs_per_week": 5, "minutes_per_run": 50, "longest_min": 120, "can_run_30": True, "at": "x"}


@pytest.mark.parametrize("hist,exp,want", [
    (False, None, "full"),                                   # no data, never answered
    (False, EXP_NEW, "full"),
    (False, {"can_run_30": False, "at": "x"}, "full"),
    (False, {"runs_per_week": 2, "at": "x"}, "full"),
    (False, {"longest_min": 45, "at": "x"}, "full"),
    (False, EXP_RUNNER, "standard"),                         # new to the app, not to running
    (False, {"at": "x"}, "standard"),                        # answered, every question skipped
    (True, None, "standard"),                                # the owner: history
    (True, EXP_NEW, "standard"),
    (None, None, "standard"),                                # the data could not be read
])
def test_auto_level(hist, exp, want):
    assert H.auto_level(hist, exp)[0] == want


def test_auto_never_picks_lean():
    answers = [None, {"at": "x"}, EXP_NEW, EXP_RUNNER]
    for hist, exp in itertools.product((True, False, None), answers):
        st = H.resolve(None, hist, exp)
        assert st["level"] in ("full", "standard") and st["auto"] and st["chosen"] is None
    for lv in H.LEVELS:                                       # a choice wins, the automatic one is still said
        st = H.resolve(lv, False, None)
        assert (st["level"], st["chosen"], st["auto"], st["auto_level"]) == (lv, lv, False, "full")
    assert H.resolve("bogus", True, None)["auto"] is True


def test_the_one_time_lean_question():
    race = [{"date": "2026-04-01", "distance_km": 21.1, "time_s": 6000, "confirmed": True}]
    assert H.suggest_lean(True, race, 130, TODAY)
    assert not H.suggest_lean(False, race, 130, TODAY)                                   # no history
    assert not H.suggest_lean(True, race, 100, TODAY)                                    # no ≥ 2 h session
    assert not H.suggest_lean(True, [{**race[0], "date": "2025-09-01"}], 130, TODAY)     # race > 1 year ago
    assert not H.suggest_lean(True, [{**race[0], "confirmed": False}], 130, TODAY)
    assert not H.suggest_lean(True, race, 130, TODAY, acks=[{"id": H.LEAN_ASK, "fp": "1"}])   # 「不要」


def test_settings_keys_are_declared_and_validated():
    from backend.settings.repository import DEFAULTS, validate
    assert DEFAULTS[H.LEVEL_KEY] is None and DEFAULTS[H.ACK_KEY] == []
    for ok in (None, "full", "standard", "lean"):
        validate(H.LEVEL_KEY, ok)
    for bad in ("auto", "", 1, "Lean"):
        with pytest.raises(ValueError):
            validate(H.LEVEL_KEY, bad)
    validate(H.ACK_KEY, [{"id": "OV8b", "fp": "abc", "at": "2026-10-13T00:00:00+00:00"}])
    for bad in (None, {}, [{"id": "OV8b"}], [{"id": "", "fp": "a"}], [{"id": "OV8b", "fp": "a", "x": 1}],
                [{"id": "OV8b", "fp": "a"}, {"id": "OV8b", "fp": "a"}],
                [{"id": f"OV{i}", "fp": "a"} for i in range(H.ACK_MAX + 1)]):
        with pytest.raises(ValueError):
            validate(H.ACK_KEY, bad)


def test_with_ack_adds_moves_undoes_and_caps():
    now = dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)
    a = H.with_ack([], "OV8b", "f1", now)
    assert a == [{"id": "OV8b", "fp": "f1", "at": "2026-10-13T00:00:00+00:00"}]
    assert H.acked({"id": "OV8b", "fp": "f1"}, a) and not H.acked({"id": "OV8b", "fp": "f2"}, a)
    a = H.with_ack(a, "OV8d", "x", now)
    assert [x["id"] for x in H.with_ack(a, "OV8b", "f1", now)] == ["OV8d", "OV8b"]       # moved, not doubled
    assert H.with_ack(a, "OV8b", "f1", now, undo=True) == [a[1]]
    many = [{"id": "OV8d", "fp": str(i), "at": None} for i in range(H.ACK_MAX)]
    capped = H.with_ack(many, "OV8b", "new", now)
    assert len(capped) == H.ACK_MAX and capped[0]["fp"] == "1" and capped[-1]["fp"] == "new"
    H.validate_acks(capped)


# ---- the profile API -----------------------------------------------------------------------------

def _api(monkeypatch, hist=True, longest=130.0, exp=None, races=()):
    from backend.api import plan as PA
    from backend.engine import experience as EX
    from backend.engine import race_results as RR
    from backend.engine.wko5expr import datasource as DS
    store: dict = {}
    monkeypatch.setattr(DS, "read_setting", lambda k, d=None, *a: store.get(k, d))
    monkeypatch.setattr(PA, "_hint_history", lambda: (hist, longest))
    monkeypatch.setattr(PA, "today_local", lambda: TODAY)
    monkeypatch.setattr(EX, "load", lambda *a: exp)
    monkeypatch.setattr(RR, "load", lambda *a: list(races))
    return PA, store


def test_api_automatic_then_chosen_then_back_to_automatic(monkeypatch):
    from backend.settings.repository import SettingsRepository
    PA, store = _api(monkeypatch, hist=False, exp=None)
    g = PA.get_hint_level()
    assert (g["level"], g["auto"], g["reason"], g["suggest_lean"], g["cap"]) == ("full", True, "new_runner", False, 3)
    assert g["levels"] == ["full", "standard", "lean"] and g["acks"] == []

    async def go():
        s = await _session()
        repo = SettingsRepository(s, 1)
        out = []
        for lv in ("full", "standard", "lean"):                 # 三段都可以選
            out.append(await PA.put_hint_level(PA.HintLevelIn(level=lv), db=s))
            store[H.LEVEL_KEY] = await repo.get(H.LEVEL_KEY)
            out.append(PA.get_hint_level())
        out.append(await PA.put_hint_level(PA.HintLevelIn(level=None), db=s))     # 改回自動
        store[H.LEVEL_KEY] = await repo.get(H.LEVEL_KEY)
        out.append(PA.get_hint_level())
        with pytest.raises(HTTPException) as ei:
            await PA.put_hint_level(PA.HintLevelIn(level="auto"), db=s)
        assert ei.value.status_code == 400
        return out
    r = _run(go())
    assert [(x["level"], x["auto"]) for x in r[:6]] == [("full", False)] * 2 + [("standard", False)] * 2 + \
        [("lean", False)] * 2
    assert (r[6]["level"], r[6]["auto"], r[6]["chosen"]) == ("full", True, None) == (r[7]["level"], r[7]["auto"], r[7]["chosen"])


def test_api_owner_is_standard_and_asked_once_about_lean(monkeypatch):
    from backend.settings.repository import SettingsRepository
    race = [{"date": "2026-04-01", "distance_km": 21.1, "time_s": 6000, "confirmed": True}]
    PA, store = _api(monkeypatch, hist=True, longest=150.0, exp=None, races=race)
    g = PA.get_hint_level()
    assert (g["level"], g["auto"], g["suggest_lean"]) == ("standard", True, True)

    async def go():
        s = await _session()
        r = await PA.post_hint_ack(PA.HintAckIn(id=H.LEAN_ASK, fp="1"), db=s)     # 「不要」
        store[H.ACK_KEY] = await SettingsRepository(s, 1).get(H.ACK_KEY)
        return r
    r = _run(go())
    assert r["acks"] == [{"id": H.LEAN_ASK, "fp": "1"}]
    assert PA.get_hint_level()["suggest_lean"] is False and PA.get_hint_level()["level"] == "standard"
    store[H.LEVEL_KEY] = "full"                                          # a chosen level is never re-asked
    store[H.ACK_KEY] = []
    assert PA.get_hint_level()["suggest_lean"] is False


def test_api_ack_and_undo(monkeypatch):
    from backend.settings.repository import SettingsRepository
    PA, store = _api(monkeypatch)

    async def go():
        s = await _session()
        a = await PA.post_hint_ack(PA.HintAckIn(id="OV8b", fp="f1"), db=s)
        b = await PA.post_hint_ack(PA.HintAckIn(id="OV8d", fp="f2"), db=s)
        stored = await SettingsRepository(s, 1).get(H.ACK_KEY)
        c = await PA.post_hint_ack(PA.HintAckIn(id="OV8b", fp="f1", undo=True), db=s)
        for bad in (PA.HintAckIn(id="ZZ1", fp="x"), PA.HintAckIn(id="OV8b", fp=""), PA.HintAckIn(id="OV8b", fp="x" * 65)):
            with pytest.raises(HTTPException) as ei:
                await PA.post_hint_ack(bad, db=s)
            assert ei.value.status_code == 400
        return a, b, stored, c
    a, b, stored, c = _run(go())
    assert a["acks"] == [{"id": "OV8b", "fp": "f1"}]
    assert b["acks"] == [{"id": "OV8b", "fp": "f1"}, {"id": "OV8d", "fp": "f2"}]
    assert all(x.get("at") for x in stored)
    assert c["acks"] == [{"id": "OV8d", "fp": "f2"}]


def test_api_history_from_the_dataset(monkeypatch):
    """_hint_history: has_history and the longest foot session of the last 12 weeks; None without data."""
    from backend.api import plan as PA
    from backend.api import wko5views as WV
    from backend.tests.test_experience_survey import _foot, _weeks
    monkeypatch.setattr(PA, "today_local", lambda: TODAY)
    from backend.tests.wko5_fakes import FakeDataset
    mon = TODAY - dt.timedelta(days=TODAY.weekday())
    ws = _weeks(5, 3, mon) + [_foot(TODAY - dt.timedelta(days=20), minutes=135)]
    monkeypatch.setattr(WV, "_dataset", lambda *a, **k: FakeDataset(ws, TODAY))
    hist, longest = PA._hint_history()
    assert hist is True and longest == pytest.approx(135.0)

    def boom(*a, **k):
        raise FileNotFoundError("no data")
    monkeypatch.setattr(WV, "_dataset", boom)
    assert PA._hint_history() == (None, None)
