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
    # the ladder variants are 強度課 with 「自動」 bands: % CP (SP-84, owner 2026-10-07)
    assert all(r["target_types"] == ["power_pct"] for r in rows.values() if r.get("variant"))
    assert rows["strides"]["target_types"] == ["none"]


def test_quality_auto_counts_as_pct_cp():
    """Owner 2026-10-07: a 強度課's 「自動」 main set is % CP in the filter (間歇／爬坡 run on power,
    target_policy.AUTO) — one function for both lists; an explicit 目標用 still wins; other categories
    keep 「自動」."""
    items = wrap(st("work", 300, BAND))
    assert WS.target_types(items, None, quality=True) == ["power_pct"]
    assert WS.target_types(items, "hr", quality=True) == ["hr_pct"]
    assert WS.target_types(items, None, quality=False) == ["auto"]
    # the one helper both lists use: a row's category / cats decide
    assert WS.template_target_types({"items": items, "cats": ["quality"]}) == ["power_pct"]
    assert WS.template_target_types({"items": items, "cats": ["easy"]}) == ["auto"]
    assert WS.template_target_types({"items": items}, "quality") == ["power_pct"]
    assert WS.template_target_types({"items": items, "target_basis": "hr", "cats": ["quality"]}) == ["hr_pct"]
    # an easy-intent warm-up / a 「其他」-only main set don't change
    assert WS.target_types([st("work", 1800, EASY)], None, quality=True) == ["hr_zone"]


def test_api_field_built_in_and_user(monkeypatch):
    with Env(monkeypatch) as e:
        pw = {"items": wrap(st("work", 180, {"type": "power", "mode": "abs", "lo": 300, "hi": 320}))}
        band = {"items": wrap(st("work", 480, BAND))}
        a = e.c.post(UAPI, json={"name": "絕對功率", "cats": ["quality"], "steps": pw}).json()
        b = e.c.post(UAPI, json={"name": "自動心率", "cats": ["quality"], "steps": band, "target_basis": "hr"}).json()
        c = e.c.post(UAPI, json={"name": "自動", "cats": ["easy"], "steps": band}).json()
        d = e.c.post(UAPI, json={"name": "強度自動", "cats": ["quality"], "steps": band}).json()
        # the 範本 page's list
        u = e.c.get(UAPI).json()
        assert [x["id"] for x in u["target_types"]] == list(WS.TARGET_TYPE_IDS)
        got = {t["id"]: t["row"]["target_types"] for t in u["templates"]}
        assert got == {a["id"]: ["power_abs"], b["id"]: ["hr_pct"], c["id"]: ["auto"], d["id"]: ["power_pct"]}
        # 插入範本: the same per row, the built-in rows too
        m = e.c.get(f"{API}/steps/templates").json()
        assert [x["id"] for x in m["target_types"]] == list(WS.TARGET_TYPE_IDS)
        mine = {r["key"]: r["target_types"] for g in m["groups"] if g.get("mine") for r in g["rows"]}
        assert mine == {f"user:{a['id']}": ["power_abs"], f"user:{b['id']}": ["hr_pct"], f"user:{c['id']}": ["auto"],
                        f"user:{d['id']}": ["power_pct"]}
        assert all("target_types" in r for g in m["groups"] for r in g["rows"])
        # 複製成我的範本 keeps a library row's type (its 目標用 goes with it)
        lib = next(r for g in m["groups"] if not g.get("mine") for r in g["rows"]
                   if r["key"].startswith("lib:") and r.get("basis") in ("hr", "power"))
        cp = e.c.post(f"{UAPI}/copy", json={"key": lib["key"]}).json()
        row = next(t["row"] for t in e.c.get(UAPI).json()["templates"] if t["id"] == cp["id"])
        assert row["target_types"] == lib["target_types"]


# ---------------------------------------------------------------------------
# a 強度課's 「自動」 follows what the planner runs it on (owner 2026-10-13, SP-84): the same
# target_policy.target_policy call — 課表偏好 hr, or a non-Stryd power source → % LTHR
# ---------------------------------------------------------------------------

def _source(monkeypatch, src):
    from backend.engine import planning as PL
    monkeypatch.setattr(PL.Plan, "load", classmethod(lambda cls, path=None: PL.Plan(profile={"power_source": src})))


def test_quality_auto_follows_the_plan_basis(monkeypatch):
    from backend.engine import plan_prefs as PP
    from backend.engine import target_policy as TP
    _source(monkeypatch, "stryd")
    # the filter's basis is the planner's own call for a 強度課 (間歇 / 爬坡 alike)
    for p in (PP.Prefs(), PP.Prefs(target_basis="hr"), PP.Prefs(target_basis="power"), PP.Prefs(interval_target="hr")):
        for s in ({"kind": "quality", "title": "Z5 間歇 5×3 分"}, {"kind": "quality", "title": "爬坡重複 6×2 分"}):
            assert WS.quality_auto_basis(p) == TP.target_policy(s, p)["basis"]
    assert WS.quality_auto_basis(PP.Prefs()) == "power"
    assert WS.quality_auto_basis(PP.Prefs(target_basis="hr")) == "hr"
    assert WS.quality_auto_basis(PP.Prefs(interval_target="hr")) == "hr"          # the old 間歇目標 hr
    assert WS.quality_auto_basis(PP.Prefs(target_basis="power")) == "power"
    _source(monkeypatch, "none")                                                   # power not from a Stryd
    assert WS.quality_auto_basis(PP.Prefs()) == "hr"
    assert WS.quality_auto_basis(PP.Prefs(target_basis="power")) == "power"       # an explicit 功率 still wins
    items = wrap(st("work", 300, BAND))
    assert WS.target_types(items, None, quality=True, qbasis="hr") == ["hr_pct"]
    assert WS.target_types(items, None, quality=True, qbasis="power") == ["power_pct"]
    # the template's own 目標用 wins; other categories keep 「自動」
    assert WS.target_types(items, "power", quality=True, qbasis="hr") == ["power_pct"]
    assert WS.template_target_types({"items": items, "target_basis": "power", "cats": ["quality"]}, qbasis="hr") \
        == ["power_pct"]
    assert WS.template_target_types({"items": items, "cats": ["easy"]}, qbasis="hr") == ["auto"]
    # no qbasis given: 課表偏好 read now (plan_prefs.load), like the planner
    monkeypatch.setattr(PP, "load", lambda *a, **k: PP.Prefs(target_basis="hr"))
    assert WS.template_target_types({"items": items, "cats": ["quality"]}) == ["hr_pct"]
    _source(monkeypatch, "stryd")
    monkeypatch.setattr(PP, "load", lambda *a, **k: PP.Prefs())
    assert WS.template_target_types({"items": items, "cats": ["quality"]}) == ["power_pct"]


def _both_lists(e):
    """{user:<id>: types} of the 範本 page's list and of 插入範本's 我的範本 rows, plus the ladder variants'."""
    page = {f"user:{t['id']}": t["row"]["target_types"] for t in e.c.get(UAPI).json()["templates"]}
    m = e.c.get(f"{API}/steps/templates").json()
    mine = {r["key"]: r["target_types"] for g in m["groups"] if g.get("mine") for r in g["rows"]}
    variants = {tuple(r["target_types"]) for g in m["groups"] for r in g["rows"] if r.get("variant")}
    return page, mine, variants


def test_api_hr_basis_both_lists_agree(monkeypatch):
    from backend.engine import plan_prefs as PP
    _source(monkeypatch, "stryd")
    # 課表偏好 as the planner reads it (plan_prefs.load: its own read-only sqlite, not the test's db)
    prefs = {"p": PP.Prefs()}
    monkeypatch.setattr(PP, "load", lambda *a, **k: prefs["p"])
    with Env(monkeypatch) as e:
        band = {"items": wrap(st("work", 480, BAND))}
        q = e.c.post(UAPI, json={"name": "強度自動", "cats": ["quality"], "steps": band}).json()
        qp = e.c.post(UAPI, json={"name": "強度功率", "cats": ["quality"], "steps": band, "target_basis": "power"}).json()
        qh = e.c.post(UAPI, json={"name": "強度心率", "cats": ["quality"], "steps": band, "target_basis": "hr"}).json()
        ez = e.c.post(UAPI, json={"name": "輕鬆自動", "cats": ["easy"], "steps": band}).json()
        ids = [f"user:{t['id']}" for t in (q, qp, qh, ez)]

        def check(want_q):
            page, mine, variants = _both_lists(e)
            assert page == mine                                   # 範本 page = 插入範本
            assert [page[k] for k in ids] == [[want_q], ["power_pct"], ["hr_pct"], ["auto"]]
            assert variants == {(want_q,)}                        # the built-in ladder's 「自動」 too
        check("power_pct")                                        # 課表偏好 自動, a Stryd: % CP as before
        prefs["p"] = PP.Prefs(target_basis="hr")
        check("hr_pct")                                           # 課表偏好 心率: % LTHR
        prefs["p"] = PP.Prefs()
        check("power_pct")
        _source(monkeypatch, "none")                              # 自動, power not from a Stryd: HR
        check("hr_pct")
        prefs["p"] = PP.Prefs(target_basis="power")
        check("power_pct")                                        # 課表偏好 功率
