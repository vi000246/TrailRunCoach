"""
The static demo's structure editor (backend/demo/static_shim.js `Steps`, a JS port of
POST /steps/check and /steps/derive on backend/demo/static_steps.py's data) against
the real endpoints (api/plan_sessions.py) on the same synthetic athlete: every
template structure, hand-made structures that hit each rule, three threshold sets.
Synthetic data only; the JS runs under node.
"""
import asyncio
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api import plan_sessions as PSAPI
from backend.demo import static_steps as SS
from backend.engine import plan_prefs as PP
from backend.engine import target_policy as TP
from backend.engine import workout_steps as WS

NODE = shutil.which("node")
SHIM = Path(__file__).resolve().parents[1] / "demo" / "static_shim.js"

SCENARIOS = {
    "full": ({"cp": 260.0, "lthr": 168.0, "aet": 150.0, "cp_source": "測試", "lthr_source": "手動", "aet_source": "AeT 測試"},
             239.0, {"v_easy": 10.5, "v_easy_src": "近 8 週", "ep_kmh": 7.0},
             PP.Prefs(cap_weekday=50, cap_long=150, cap_mode="hard"), True),
    "no_tpace": ({"cp": 260.0, "lthr": 168.0, "aet": None}, None, {}, PP.Prefs(), True),
    "hr_only": ({"cp": None, "lthr": 170.0, "aet": 152.0}, 250.0, {"v_easy": 9.0, "v_easy_src": "x", "ep_kmh": None},
                PP.Prefs(target_basis="hr", cap_weekday=40), False),
}

SESSIONS = {
    "easy": {"kind": "easy", "title": "輕鬆跑", "minutes": 45, "target": "", "detail": "", "day": "2026-10-06"},
    "quality": {"kind": "quality", "title": "VO2max 5×2 分", "minutes": 60, "target": "", "detail": "",
                "day": "2026-10-07", "variant_key": "v1a"},
    "quality_hr": {"kind": "quality", "title": "閾值 3×8 分", "minutes": 55, "target": "", "detail": "",
                   "day": "2026-10-08", "variant_key": "t2a", "target_basis": "hr"},
    "trail_long": {"kind": "long", "title": "山路長跑", "minutes": 150, "target": "", "detail": "", "day": "2026-10-10",
                   "terrain": "trail", "distance_km": 20, "climb_m": 1200},
    "hike": {"kind": "hike", "title": "越野跑", "minutes": 120, "target": "", "detail": "", "day": "2026-10-11",
             "climb_per_km": 80},
    "test": {"kind": "test", "title": "CP 測試 20 分全力", "minutes": 40, "target": "", "detail": "", "day": "2026-10-09",
             "protocol": "quick"},
}
CAT_SESSION = {"easy": "easy", "quality": "quality", "test": "test", "trail": "hike"}

T = lambda **k: {"type": "auto", **k}                       # noqa: E731
CUSTOM = {
    "mixed": [
        {"id": "a", "kind": "warm", "dur": {"type": "distance", "value": 2000}, "target": T(intent="easy")},
        {"id": "b", "kind": "repeat", "times": 3, "last_rest": False, "items": [
            {"id": "c", "kind": "work", "dur": {"type": "distance", "value": 800},
             "target": {"type": "pace", "mode": "pct", "lo": 0.95, "hi": 0.98}},
            {"id": "d", "kind": "rest", "dur": {"type": "distance", "value": 400}, "target": T(intent="open")},
            {"id": "e", "kind": "repeat", "times": 2, "items": [
                {"id": "f", "kind": "work", "dur": {"type": "time", "value": 60}, "target": {"type": "power", "mode": "zone", "zone": "5"}},
                {"id": "g", "kind": "rest", "dur": {"type": "time", "value": 90}, "target": {"type": "none"}}]}]},
        {"id": "h", "kind": "work", "dur": {"type": "time", "value": 600}, "target": {"type": "hr", "mode": "zone", "zone": "aet"}},
        {"id": "i", "kind": "work", "dur": {"type": "time", "value": 300}, "target": {"type": "hr", "mode": "pct", "lo": 0.95, "hi": 1.0}},
        {"id": "j", "kind": "work", "dur": {"type": "time", "value": 240}, "target": {"type": "power", "mode": "abs", "lo": 300, "hi": 280}},
        {"id": "k", "kind": "other", "dur": {"type": "open", "est": 120}, "target": {"type": "pace", "mode": "abs", "lo": 250, "hi": 240}},
        {"id": "l", "kind": "work", "dur": {"type": "distance", "value": 3000}, "target": {"type": "pace", "mode": "zone", "zone": "3"}},
        {"id": "m", "kind": "cool", "dur": {"type": "open"}, "target": {"type": "hr", "mode": "abs", "lo": 130, "hi": 150}}],
    "z5_rules": [
        {"id": "w", "kind": "warm", "dur": {"type": "time", "value": 900}, "target": T(intent="easy")},
        {"id": "r", "kind": "repeat", "times": 6, "items": [
            {"id": "x", "kind": "work", "dur": {"type": "time", "value": 90}, "target": T(intent="band", lo=1.06, hi=1.12, cls="Z5")},
            {"id": "y", "kind": "rest", "dur": {"type": "time", "value": 200}, "target": T(intent="open")}]},
        {"id": "z", "kind": "cool", "dur": {"type": "time", "value": 600}, "target": T(intent="easy")}],
    "z3_short": [
        {"kind": "repeat", "times": 5, "items": [
            {"kind": "work", "dur": {"type": "time", "value": 150}, "target": T(intent="band", lo=0.9, hi=0.95, cls="Z3sub", hr=[150, 160])},
            {"kind": "rest", "dur": {"type": "time", "value": "60"}, "target": T(intent="open")}]}],
    "long_and_dupes": [
        {"id": "a", "kind": "work", "dur": {"type": "time", "value": 18000}, "target": T(intent="easy", plo=0.75, phi=0.8)},
        {"id": "a", "kind": "work", "dur": {"type": "time", "value": 1800},
         "target": {"type": "pace", "mode": "pct", "lo": 1.04, "hi": 1.08, "hrp": [0.88, 0.95]}},
        {"id": "", "kind": "rest", "dur": {"type": "time", "value": 300}, "note": "  走路  "}],
    "many_steps": [{"kind": "repeat", "times": 30, "items": [
        {"kind": "work", "dur": {"type": "time", "value": 30}, "target": T(intent="band", lo=1.1, hi=1.2, cls="Z5")},
        {"kind": "rest", "dur": {"type": "time", "value": 30}}], "last_rest": False}],
    "power_pct_hr_abs": [
        {"kind": "work", "dur": {"type": "time", "value": 1200}, "target": {"type": "power", "mode": "pct", "lo": 0.88, "hi": 0.92}},
        {"kind": "work", "dur": {"type": "time", "value": 600}, "target": {"type": "hr", "mode": "abs", "lo": 160, "hi": 172}},
        {"kind": "work", "dur": {"type": "time", "value": 600}, "target": {"type": "hr", "mode": "zone", "zone": "5b"}}],
    # 「負荷」 (SP-38): TSS on main-set steps, COROS TL in the preview (default conversion, 推估)
    "load": [
        {"id": "w", "kind": "warm", "dur": {"type": "time", "value": 600}, "target": T(intent="easy")},
        {"id": "p", "kind": "work", "dur": {"type": "load", "value": 60}, "target": {"type": "power", "mode": "pct", "lo": 0.88, "hi": 0.92}},
        {"id": "r", "kind": "repeat", "times": 3, "items": [
            {"id": "h", "kind": "work", "dur": {"type": "load", "value": "12.5"}, "target": {"type": "hr", "mode": "pct", "lo": 0.95, "hi": 1.0}},
            {"id": "x", "kind": "rest", "dur": {"type": "time", "value": 120}, "target": T(intent="open")}]},
        {"id": "n", "kind": "work", "dur": {"type": "load", "value": 20}, "target": {"type": "none"}}],
}
BAD = [{"items": [{"kind": "x"}]}, {"items": []}, {"nope": 1},
       {"items": [{"kind": "repeat", "times": 0, "items": [{"kind": "work", "dur": {"type": "time", "value": 2}}]}]},
       {"items": [{"kind": "work", "dur": {"type": "lap"}, "target": {"type": "power", "mode": "zone", "zone": "9"}}]},
       {"items": [{"kind": "work", "dur": {"type": "time", "value": "abc"}, "target": {"type": "hr", "mode": "pct", "lo": 2, "hi": None}}]},
       {"items": [{"kind": "warm", "dur": {"type": "load", "value": 40}}, {"kind": "work", "dur": {"type": "load", "value": 900}}]}]

DERIVE = [
    {"kind": "easy", "title": "輕鬆跑", "minutes": 45},
    {"kind": "easy", "title": "輕鬆跑＋加速跑 6×20 秒", "minutes": 50},
    {"kind": "easy", "title": "上坡衝刺 8×10 秒", "minutes": 45},
    {"kind": "easy", "title": "熱適應跑", "minutes": 40},
    {"kind": "long", "title": "長跑＋馬拉松配速 30 分", "minutes": 120, "detail": "目標配速 4:50/km"},
    {"kind": "long", "title": "長跑＋馬拉松配速 40 分", "minutes": 130},
    {"kind": "long", "title": "長跑", "minutes": 100},
    {"kind": "mountain", "title": "山路長天", "minutes": 240},
    {"kind": "hike", "title": "越野跑", "minutes": 90, "terrain": "trail"},
    {"kind": "notice", "title": "待確認", "minutes": 0},
    {"kind": "strength", "title": "肌力", "minutes": 35},
    {"kind": "easy", "title": "輕鬆跑", "minutes": 0},
    # quality / test: the export's derive map (build(sessions=...))
    {"kind": "quality", "title": "VO2max 5×2 分", "minutes": 60, "variant_key": "v1a", "day": "2026-10-07"},
    {"kind": "quality", "title": "閾值 4×6 分", "minutes": 60, "detail": "暖身 15 分，休 2 分", "target": "心率 155–162 bpm"},
    {"kind": "test", "title": "CP 測試 20 分全力", "minutes": 40, "protocol": "quick"},
    {"kind": "test", "title": "AeT 飄移測試 60 分", "minutes": 80, "protocol": "aet",
     "target": "固定功率 190 W", "detail": "暖身 10 分，測試 60 分，緩和 10 分"},
]


def _cases():
    out = []
    for g in WS.templates()["groups"]:
        sess = SESSIONS[CAT_SESSION.get(g["cat"], "easy")]
        for r in g["rows"]:
            out.append({**sess, "steps": {"origin": "user", "items": r.get("full") or r["items"]}})
    for name, items in CUSTOM.items():
        for sk in ("easy", "quality", "quality_hr", "trail_long", "test"):
            out.append({**SESSIONS[sk], "steps": {"origin": "user", "items": items}})
    out += [{**SESSIONS["easy"], "steps": b} for b in BAD]
    return out


def _cmp(a, b, path="$"):
    if isinstance(a, float) or isinstance(b, float):
        assert a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9), f"{path}: {a!r} != {b!r}"
    elif isinstance(a, dict) and isinstance(b, dict):
        assert set(a) == set(b), f"{path}: keys {sorted(set(a) ^ set(b))}"
        for k in a:
            _cmp(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), f"{path}: {len(a)} != {len(b)} items\n{a}\n{b}"
        for i, (x, y) in enumerate(zip(a, b)):
            _cmp(x, y, f"{path}[{i}]")
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


NODE_SCRIPT = r"""
const fs = require("fs");
const S = require(%(shim)s).Steps;
const D = JSON.parse(fs.readFileSync(%(data)s, "utf-8"));
const C = JSON.parse(fs.readFileSync(%(cases)s, "utf-8"));
const run = (fn, b) => { try { return { status: 200, body: fn(b, null, D) }; }
  catch (e) { if (e instanceof S.StepsError) return { status: 400, errors: e.errors }; throw e; } };
process.stdout.write(JSON.stringify({ check: C.check.map((b) => run(S.check, b)), derive: C.derive.map((b) => run(S.derive, b)),
  recs: [S.recsKey("easy", "2026-10-06", "road", "45"), S.recsKey("hike", "2026-10-06", "trail", null)] }));
"""


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_js_port_matches_the_endpoints(scenario, monkeypatch, tmp_path):
    th, tpace, speeds, prefs, power_ok = SCENARIOS[scenario]

    async def inputs(*a, **k):
        return {"thresholds": dict(th)}
    monkeypatch.setattr(PSAPI, "_inputs", inputs)
    monkeypatch.setattr(PSAPI, "_tpace", lambda: tpace)
    monkeypatch.setattr(PSAPI, "_speeds", lambda: dict(speeds))
    monkeypatch.setattr(PP, "load", lambda *a, **k: prefs)
    monkeypatch.setattr(TP, "_auto_power_ok", lambda: power_ok)

    cases = _cases()
    data = asyncio.run(SS.collect([d for d in DERIVE if d["kind"] in ("quality", "test")]))
    assert data["derive"] and data["th"]["tpace"] == tpace

    async def py(fn, body):
        try:
            return {"status": 200, "body": json.loads(json.dumps(await fn(dict(body), db=None), ensure_ascii=False))}
        except HTTPException as e:
            return {"status": e.status_code, "errors": e.detail["errors"]}

    async def expected():
        return ([await py(PSAPI.steps_check, b) for b in cases], [await py(PSAPI.steps_derive, b) for b in DERIVE])
    want_check, want_derive = asyncio.run(expected())

    (tmp_path / "d.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    (tmp_path / "c.json").write_text(json.dumps({"check": cases, "derive": DERIVE}, ensure_ascii=False), "utf-8")
    script = NODE_SCRIPT % {"shim": json.dumps(str(SHIM)), "data": json.dumps(str(tmp_path / "d.json")),
                            "cases": json.dumps(str(tmp_path / "c.json"))}
    r = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout)
    assert len(got["check"]) == len(want_check) and len(got["derive"]) == len(want_derive)
    assert got["recs"] == [SS.recs_key("easy", "2026-10-06", "road", 45), SS.recs_key("hike", "2026-10-06", "trail")]
    assert sum(w["status"] == 400 for w in want_check) == len(BAD)
    ok = [w["body"] for w in want_check if w["status"] == 200]
    assert any(b["equiv"] for b in ok) and any(i["level"] == "err" for b in ok for i in b["issues"])
    assert any(o["est"] for b in ok for o in b["order"]) and any(l.get("group") for b in ok for l in b["watch"]["lines"])
    for i, (w, g) in enumerate(zip(want_check, got["check"])):
        _cmp(w, g, f"check[{i}] {json.dumps(cases[i], ensure_ascii=False)[:120]}")
    for i, (w, g) in enumerate(zip(want_derive, got["derive"])):
        _cmp(w, g, f"derive[{i}] {DERIVE[i]['title']}")


def test_derive_sig_reads_the_derive_fields():
    a = {"kind": "easy", "title": "x", "minutes": 45.0, "variant_adj": {"b": 1, "a": 2}, "heat": True, "day": "2026-10-01"}
    b = {**a, "minutes": "45", "day": "2026-11-01", "variant_adj": {"a": 2, "b": 1}}
    assert SS.derive_sig(a) == SS.derive_sig(b)
    assert SS.derive_sig(a) != SS.derive_sig({**a, "title": "y"})
