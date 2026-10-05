"""
主課強度類型 (SP-84; workout_steps.target_types): the 範本 page's and 插入範本's second
filter. The main set's targets (work steps, inside repeats too), mixed ones listed each,
「自動」 bands by the template's 目標用, 「負荷」 end conditions; the field on every
GET /steps/templates row and GET /steps/templates/user row, built-in and user templates.
"""
from backend.engine import workout_steps as WS
from backend.tests.test_plan_store import API, Env

UAPI = f"{API}/steps/templates/user"


def st(kind, dur, target=None):
    if isinstance(dur, int):
        dur = {"type": "time", "value": dur}
    return {"kind": kind, "dur": dur, "target": target or {"type": "none"}}


EASY = {"type": "auto", "intent": "easy"}
BAND = {"type": "auto", "intent": "band", "lo": 0.95, "hi": 1.0, "cls": "Z4"}


def wrap(*work, rest=None):
    """warm-up (HR ≤ AeT) + 4 × (work, rest) + cool-down: only the work counts."""
    return [st("warm", 600, EASY),
            {"kind": "repeat", "times": 4, "items": [*work, st("rest", 120, rest or {"type": "power", "mode": "abs", "lo": 100, "hi": 150})]},
            st("cool", 600, EASY)]


def test_each_target_type():
    cases = [
        ({"type": "power", "mode": "pct", "lo": 1.0, "hi": 1.05}, "power_pct"),
        ({"type": "power", "mode": "zone", "zone": "4"}, "power_pct"),
        ({"type": "power", "mode": "abs", "lo": 250, "hi": 270}, "power_abs"),
        ({"type": "hr", "mode": "pct", "lo": 0.95, "hi": 1.0}, "hr_pct"),
        ({"type": "hr", "mode": "zone", "zone": "aet"}, "hr_zone"),
        ({"type": "hr", "mode": "abs", "lo": 160, "hi": 170}, "hr_abs"),
        ({"type": "pace", "mode": "pct", "lo": 0.98, "hi": 1.02}, "pace"),
        ({"type": "pace", "mode": "abs", "lo": 240, "hi": 250}, "pace"),
        ({"type": "rpe", "lo": 6, "hi": 7}, "rpe"),
        ({"type": "none"}, "none"),
        ({"type": "auto", "intent": "open"}, "none"),
        (BAND, "auto"),
    ]
    for tg, want in cases:
        # the warm-up / cool-down (HR) and the rest (absolute power) never count
        assert WS.target_types(wrap(st("work", 180, tg))) == [want], tg


def test_auto_band_follows_the_basis():
    items = wrap(st("work", 300, BAND))
    assert WS.target_types(items, "power") == ["power_pct"]
    assert WS.target_types(items, "hr") == ["hr_pct"]
    assert WS.target_types(items, None) == ["auto"]
    # an easy step: HR ≤ AeT, unless a power band under 目標用 power
    plo = {"type": "auto", "intent": "easy", "plo": 0.7, "phi": 0.8}
    assert WS.target_types([st("work", 1800, EASY)], "power") == ["hr_zone"]
    assert WS.target_types([st("work", 1800, plo)], "power") == ["power_pct"]
    assert WS.target_types([st("work", 1800, plo)], "hr") == ["hr_zone"]


def test_mixed_main_set_lists_both_and_load():
    items = wrap(st("work", 120, {"type": "power", "mode": "pct", "lo": 1.1, "hi": 1.2}),
                 st("work", {"type": "load", "value": 20}, {"type": "hr", "mode": "pct", "lo": 0.9, "hi": 0.95}))
    assert WS.target_types(items) == ["power_pct", "hr_pct", "load"]


def test_no_work_steps_falls_back():
    # strides: 「其他」 steps with no target; an all-easy run: every step
    strides = [{"kind": "repeat", "times": 4, "items": [st("other", 20, {"type": "auto", "intent": "open"}), st("rest", 40, EASY)]}]
    assert WS.target_types(strides) == ["none"]
    assert WS.target_types([st("warm", 600, EASY), st("cool", 600, {"type": "rpe", "lo": 2, "hi": 3})]) == ["hr_zone", "rpe"]
    assert WS.target_types([]) == []


def test_type_list_in_display_order():
    ids = [x["id"] for x in WS.target_type_list()]
    assert ids == list(WS.TARGET_TYPE_IDS) and all(x["label"] for x in WS.target_type_list())


def test_builtin_rows_carry_target_types():
    t = WS.templates()
    assert [x["id"] for x in t["target_types"]] == list(WS.TARGET_TYPE_IDS)
    rows = {r["key"]: r for g in t["groups"] for r in g["rows"]}
    assert all(set(r["target_types"]) <= set(WS.TARGET_TYPE_IDS) for r in rows.values())
    # library rows by their source's own targets / 目標用; ladder variants follow the session
    lib = [r for r in rows.values() if r["key"].startswith("lib:")]
    assert {x for r in lib for x in r["target_types"]} >= {"hr_pct", "pace", "power_pct", "rpe"}
    assert all(r["target_types"] == ["auto"] for r in rows.values() if r.get("variant"))
    assert rows["strides"]["target_types"] == ["none"]


def test_api_field_built_in_and_user(monkeypatch):
    with Env(monkeypatch) as e:
        pw = {"items": wrap(st("work", 180, {"type": "power", "mode": "abs", "lo": 300, "hi": 320}))}
        band = {"items": wrap(st("work", 480, BAND))}
        a = e.c.post(UAPI, json={"name": "絕對功率", "cats": ["quality"], "steps": pw}).json()
        b = e.c.post(UAPI, json={"name": "自動心率", "cats": ["quality"], "steps": band, "target_basis": "hr"}).json()
        c = e.c.post(UAPI, json={"name": "自動", "cats": ["easy"], "steps": band}).json()
        # the 範本 page's list
        u = e.c.get(UAPI).json()
        assert [x["id"] for x in u["target_types"]] == list(WS.TARGET_TYPE_IDS)
        got = {t["id"]: t["row"]["target_types"] for t in u["templates"]}
        assert got == {a["id"]: ["power_abs"], b["id"]: ["hr_pct"], c["id"]: ["auto"]}
        # 插入範本: the same per row, the built-in rows too
        m = e.c.get(f"{API}/steps/templates").json()
        assert [x["id"] for x in m["target_types"]] == list(WS.TARGET_TYPE_IDS)
        mine = {r["key"]: r["target_types"] for g in m["groups"] if g.get("mine") for r in g["rows"]}
        assert mine == {f"user:{a['id']}": ["power_abs"], f"user:{b['id']}": ["hr_pct"], f"user:{c['id']}": ["auto"]}
        assert all("target_types" in r for g in m["groups"] for r in g["rows"])
        # 複製成我的範本 keeps a library row's type (its 目標用 goes with it)
        lib = next(r for g in m["groups"] if not g.get("mine") for r in g["rows"]
                   if r["key"].startswith("lib:") and r.get("basis") in ("hr", "power"))
        cp = e.c.post(f"{UAPI}/copy", json={"key": lib["key"]}).json()
        row = next(t["row"] for t in e.c.get(UAPI).json()["templates"] if t["id"] == cp["id"])
        assert row["target_types"] == lib["target_types"]
