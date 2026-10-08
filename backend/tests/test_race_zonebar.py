"""
賽事計算機's absolute-intensity bar (SP-118; engine/racepower/zonebar.py and the page's
zoneBar block in racepower.html): power zones for a power basis, heart-rate zones at the
predicted race HR for a heart-rate basis, the system the viewer picked or the 課表心率區間
model, and the power bar with the reason when there is no predicted HR. Synthetic data only.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.engine.racepower import zonebar as ZB
from backend.tests.test_racepower_v2 import client, fake_inputs  # noqa: F401  (fixture)

approx = pytest.approx
STATIC = Path(__file__).resolve().parents[1] / "static"
TRAIL = {"type": "trail", "summary": {"trail_hr": {"x": 0.95, "x_star": 0.88}}}


def test_hr_systems_from_lthr_and_max_rest_hr():
    s = ZB.hr_systems(160.0, 200.0, 50.0)
    assert list(s) == ["frielhr", "classichr", "coroslthr", "coroshrr", "coroshrmax"]
    f = s["frielhr"]["rows"]
    assert [r["id"] for r in f] == ["1", "2", "3", "4", "5a", "5b", "5c"]
    assert f[0]["lo"] == 0 and f[1]["lo"] == approx(136) and f[-1]["hi"] is None
    assert s["frielhr"]["short"] == "Friel" and s["classichr"]["rows"][1]["lo"] == approx(0.69 * 160, abs=0.1)
    assert [r["hi"] for r in s["coroslthr"]["rows"]][:5] == [128, 144, 152, 163, 170]
    assert s["coroshrr"]["rows"][1]["lo"] == round(50 + 0.59 * 150)          # % HRR from max / rest HR
    assert s["coroshrmax"]["rows"][1]["lo"] == 100
    assert "LTHR 160" in s["frielhr"]["basis_text"]


def test_hr_systems_without_the_numbers_say_why():
    s = ZB.hr_systems(160.0)                          # no max / rest HR: only the LTHR tables
    assert s["frielhr"]["rows"] and s["coroslthr"]["rows"]
    assert "rows" not in s["coroshrr"] and s["coroshrr"]["reason"]
    assert "rows" not in s["coroshrmax"] and s["coroshrmax"]["reason"]
    none = ZB.hr_systems(None)
    assert all("rows" not in v and v["reason"] for v in none.values())


def test_hr_bar_trail_predicts_hr_and_defaults_to_the_plan_model():
    b = ZB.hr_bar(TRAIL, 160.0, {"model": "hrr", "mhr": 200.0, "rhr": 50.0})
    assert b["hr"] == approx(140.8) and b["reason"] is None and "LTHR" in b["hr_source"]
    assert b["default"] == "coroshrr" and b["systems"]["coroshrr"]["rows"]
    assert ZB.hr_bar(TRAIL, 160.0)["default"] == ZB.DEFAULT_SYSTEM == "coroslthr"
    assert ZB.hr_bar(TRAIL, 160.0, {"model": "bogus"})["default"] == "coroslthr"


def test_hr_bar_without_a_predicted_hr_gives_the_reason():
    road = ZB.hr_bar({"type": "road", "summary": {}}, 160.0)
    assert road["hr"] is None and road["reason"] == ZB.NO_HR_ROAD
    assert ZB.hr_bar(TRAIL, None)["reason"] == ZB.NO_HR_LTHR
    assert ZB.hr_bar({"type": "trail", "summary": {}}, 160.0)["reason"] == ZB.NO_HR_MODEL


def test_plan_carries_the_hr_bar(client, monkeypatch):   # noqa: F811
    from backend.api import racepower as RP
    inp = fake_inputs()
    inp["aet"] = {"aet": 150.0, "lthr": 165.0, "source": "測試"}
    monkeypatch.setattr(RP, "inputs", lambda refresh=False: inp)
    monkeypatch.setattr(RP, "_hr_basis", lambda: {"model": "hrmax", "mhr": 195.0, "rhr": None, "acc": None})
    p = client.post("/api/v1/racepower/plan", json={"type": "road", "distance_km": 10}).json()
    hb = p["hr_bar"]
    assert hb["hr"] is None and hb["reason"] == ZB.NO_HR_ROAD and hb["default"] == "coroshrmax"
    assert hb["systems"]["coroshrmax"]["rows"][1]["lo"] == round(0.5 * 195)
    assert "rows" not in hb["systems"]["coroshrr"]                           # no resting HR


# ---------------------------------------------------------------------------
# the page's zoneBar block, run under node
# ---------------------------------------------------------------------------

NODE = shutil.which("node")


def _block() -> str:
    page = (STATIC / "racepower.html").read_text(encoding="utf-8")
    m = re.search(r"// ---- zone bar \(SP-118\).*?// ---- end zone bar ----", page, re.S)
    assert m, "zone bar block not found"
    return m.group(0)


def _run(plan: dict, basis: dict, pick=None) -> dict:
    js = r"""
const vm = require("vm");
const cat = JSON.parse(process.argv[2]);
const store = {};
const ctx = {
  PLAN: JSON.parse(process.argv[3]), BASIS: JSON.parse(process.argv[4]),
  T: (k, p) => String(cat[k] ?? k).replace(/\{(\w+)\}/g, (m, x) => (p && p[x] != null ? p[x] : m)),
  ok: (v) => v != null && isFinite(v), esc: (s) => String(s), css: (v) => v,
  help: (t) => `<help>${t}</help>`, W: (v) => Math.round(v) + " W", pct: (v) => Math.round(v * 100) + "%",
  zoneOf: (p, cp) => { const x = p / cp; const z = ctx.PLAN.zones.find((r) => x >= r.lo && (r.hi == null || x < r.hi)); return z ? z.id : "1A 以下"; },
  ls: { get: (k, d) => (k in store ? store[k] : d), set: (k, v) => { store[k] = v; } },
  $: () => null,
};
const pick = JSON.parse(process.argv[5]);
if (pick) store.hrsys = pick;
vm.runInNewContext(process.argv[1] + "\nout = { spec: zoneBarSpec(PLAN, BASIS, ls.get(ZB_SYS_KEY, null)), html: zoneBar(PLAN.summary.power, PLAN.used.cp.value) };", ctx);
console.log(JSON.stringify(ctx.out));
"""
    cat = json.loads((STATIC / "i18n" / "zh-TW" / "racepower.json").read_text(encoding="utf-8"))
    cat = {k: v for k, v in cat.items() if k.startswith("zbar.")}
    r = subprocess.run([NODE, "-e", js, _block(), json.dumps(cat), json.dumps(plan), json.dumps(basis), json.dumps(pick)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _plan(hr_bar):
    from backend.engine.zones import zones_json
    return {"zones": zones_json(300.0), "summary": {"power": 255.0}, "used": {"cp": {"value": 300.0}}, "hr_bar": hr_bar}


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_page_power_basis_shows_palladino_with_ticks_and_names():
    out = _run(_plan(ZB.hr_bar(TRAIL, 160.0)), {"basis": "power"})
    assert out["spec"] == {"kind": "power"}
    h = out["html"]
    assert "絕對強度・功率區間" in h and "<help>" in h and 'class="escale"' in h
    assert ">130<" in h and ">50<" in h                                       # the % CP scale
    assert "Endurance / long run" in h and 'class="cur"' in h                  # names on the page, not on hover
    assert "平均 85% CP（255 W），落在 Palladino 2 區" in h and "zbar-sys" not in h


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_page_hr_basis_shows_the_hr_zones_and_remembers_the_pick():
    hb = ZB.hr_bar(TRAIL, 160.0, {"model": "lthr", "mhr": 200.0, "rhr": 50.0})
    out = _run(_plan(hb), {"basis": "hr"})
    assert out["spec"]["kind"] == "hr" and out["spec"]["sys"] == "coroslthr" and out["spec"]["hr"] == approx(140.8)
    assert "絕對強度・心率區間" in out["html"] and "平均 141 bpm，落在 COROS 乳酸閾 Z2 區" in out["html"]
    assert 'id="zbar-sys"' in out["html"] and 'value="frielhr"' in out["html"]
    friel = _run(_plan(hb), {"basis": "hr"}, pick="frielhr")
    assert friel["spec"]["sys"] == "frielhr" and "落在 Friel 2 區" in friel["html"]
    # a remembered system that has no zones now (no max HR) falls back to the default
    hb2 = ZB.hr_bar(TRAIL, 160.0)
    assert _run(_plan(hb2), {"basis": "hr"}, pick="coroshrmax")["spec"]["sys"] == "coroslthr"


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_page_hr_basis_without_hr_falls_back_to_power_with_the_reason():
    out = _run(_plan(ZB.hr_bar({"type": "road", "summary": {}}, 160.0)), {"basis": "hr"})
    assert out["spec"]["kind"] == "power" and out["spec"]["why"] == ZB.NO_HR_ROAD
    assert "絕對強度・功率區間" in out["html"] and ZB.NO_HR_ROAD in out["html"]
    old = _run(_plan(None), {"basis": "hr"})                                  # a plan without hr_bar
    assert old["spec"]["kind"] == "power" and "沒有平均心率" in old["html"]
