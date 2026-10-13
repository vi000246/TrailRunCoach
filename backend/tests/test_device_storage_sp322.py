"""這台裝置的離線資料 (SP-322): 設定 › 資料同步 shows what the offline copy (SP-314) keeps on this
device — the space used (navigator.storage.estimate) against the fixed 20 MB budget, whether each
tier P0 / P1 / P2 is kept (from the service worker's own status, AppOffline.status()), whether the
storage is persistent (navigator.storage.persisted, a button to ask), when it was last refreshed,
and on an iPhone / iPad browser tab (not the home-screen app) the 加到主畫面 hint. A browser without
these APIs shows 無法取得 and the page keeps working.

static/device_storage.js runs under node (vm) with a fake navigator.storage / AppOffline and the
zh-TW settings catalog; the rest reads the repo's page and catalogs. Synthetic only; the node
tests are skipped without node."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")

MB = 1024 * 1024
AT = 1792224000000            # 2026-10-17T08:00:00Z → 16:00 in Taipei (TZ below)

RUN = r"""
const vm = require("vm"), fs = require("fs");
const ctx = { console, setTimeout, Promise };
ctx.window = ctx;
const cat = JSON.parse(fs.readFileSync(process.argv[1] + "/i18n/zh-TW/settings.json", "utf8"));
ctx.I18N = { t: (k, p) => String(cat[k.replace(/^settings\./, "")] ?? k).replace(/\{(\w+)\}/g, (m, n) => (p && p[n] != null ? p[n] : m)) };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1] + "/device_storage.js", "utf8"), ctx);
const D = ctx.DeviceStorage;
const t = ctx.I18N.t;
// a fake navigator.storage: each method a value, a function, or missing
function storage(o) {
  const s = {};
  if ("estimate" in o) s.estimate = typeof o.estimate === "function" ? o.estimate : async () => o.estimate;
  if ("persisted" in o) s.persisted = typeof o.persisted === "function" ? o.persisted : async () => o.persisted;
  if ("persist" in o) s.persist = typeof o.persist === "function" ? o.persist : async () => o.persist;
  return s;
}
const offline = (st) => ({ status: async () => (typeof st === "function" ? st() : st) });
let OUT;
(async () => {
__BODY__
})().then(() => console.log(JSON.stringify(OUT === undefined ? null : OUT)), (e) => { console.error(e && e.stack || e); process.exit(1); });
"""


def run(body: str):
    js = RUN.replace("__BODY__", body)
    r = subprocess.run([NODE, "-e", js, str(STATIC)], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, env={**os.environ, "TZ": "Asia/Taipei"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _status(**kw):
    """What the worker answers (offline_core.js status()): signed in as u7, every tier kept."""
    s = {"user": "u7", "version": "v1", "budget": 20 * MB,
         "bytes": {"app": int(1.5 * MB), "p0": int(1.2 * MB), "p1": int(0.8 * MB), "p2": 0},
         "total": int(3.5 * MB),
         "tiers": {"p0": {"at": AT, "ok": 30, "failed": 0}, "p1": {"at": AT - 3600e3, "ok": 4, "failed": 0},
                   "p2": {"at": AT - 7200e3, "ok": 0, "failed": 0}},
         "present": {"p0": True, "p1": True, "p2": True}}
    s.update(kw)
    return s


# ---------------------------------------------------------------------------- usage, tiers, time

@needs_node
def test_usage_against_the_20_mb_budget_and_each_tier():
    out = run(f"""
const data = await D.read({{ storage: storage({{ estimate: {{ usage: {int(4.2 * MB)}, quota: 2e10 }}, persisted: false, persist: false }}),
  offline: offline({json.dumps(_status())}) }});
const m = D.view(data, {{ a2hs: false }});
OUT = {{ m, html: D.html(m, t) }};
""")
    m = out["m"]
    assert m["budgetMb"] == 20
    assert m["usage"] == {"state": "ok", "mb": "4.2", "pct": 21, "copyMb": "3.5"}
    assert [(x["tier"], x["state"], x["mb"]) for x in m["tiers"]] == [
        ("p0", "ok", "1.2"), ("p1", "ok", "0.8"), ("p2", "empty", "0.0")]
    assert m["updated"] == {"state": "ok", "at": AT}                    # the newest tier's refill
    assert m["persisted"] == "no" and m["canAsk"] is True
    h = out["html"]
    assert "4.2 MB" in h and "20 MB" in h and "3.5 MB" in h
    for txt in ("P0", "P1", "P2", "已存好", "目前沒有要存的資料", "2026-10-17 16:00", "要求持久保存"):
        assert txt in h, txt
    assert "無法取得" not in h
    assert "加到主畫面" not in h                                        # not an iPhone tab


@needs_node
def test_the_bar_is_capped_and_a_used_space_over_the_budget_says_so():
    out = run(f"""
const m = D.view(await D.read({{ storage: storage({{ estimate: {{ usage: {25 * MB}, quota: 2e10 }}, persisted: true }}),
  offline: offline({json.dumps(_status())}) }}), {{}});
OUT = {{ usage: m.usage, html: D.html(m, t) }};
""")
    assert out["usage"]["pct"] == 100 and out["usage"]["over"] is True and out["usage"]["mb"] == "25.0"
    assert "超過" in out["html"]


@needs_node
@pytest.mark.parametrize("tier,patch,state,text", [
    ("p1", {"tiers.p1": {"at": AT, "ok": 3, "failed": 1}}, "partial", "1 項沒存到"),
    ("p1", {"tiers.p1": {"at": AT, "ok": 0, "failed": 4}}, "failed", "沒存到"),
    ("p1", {"bytes.p1": 0}, "evicted", "空間不夠"),                  # kept, then pushed out over the budget
    ("p1", {"present.p1": False}, "none", "還沒存"),                 # the browser deleted the tier's cache
    ("p2", {"tiers.p2": None}, "none", "還沒存"),                    # never refilled yet
])
def test_tier_states(tier, patch, state, text):
    st = _status()
    for path, v in patch.items():
        a, b = path.split(".")
        if v is None:
            st[a].pop(b)
        else:
            st[a][b] = v
    out = run(f"""
const m = D.view(await D.read({{ storage: storage({{ estimate: {{ usage: 1 }}, persisted: true }}), offline: offline({json.dumps(st)}) }}), {{}});
OUT = {{ tiers: m.tiers, html: D.html(m, t) }};
""")
    row = next(x for x in out["tiers"] if x["tier"] == tier)
    assert row["state"] == state, row
    assert text in out["html"]


@needs_node
def test_nobody_signed_in_or_never_refilled():
    out = run(f"""
const nobody = D.view(await D.read({{ storage: storage({{ estimate: {{ usage: 0 }}, persisted: false }}),
  offline: offline({{ user: null, budget: {20 * MB}, bytes: {{ app: 0, p0: 0, p1: 0, p2: 0 }}, total: 0, tiers: {{}}, present: {{}} }}) }}), {{}});
const fresh = D.view(await D.read({{ storage: storage({{ estimate: {{ usage: 0 }}, persisted: false }}),
  offline: offline({{ user: "u7", budget: {20 * MB}, bytes: {{ app: 0, p0: 0, p1: 0, p2: 0 }}, total: 0, tiers: {{}}, present: {{}} }}) }}), {{}});
OUT = {{ nobody: [nobody.tiers.map((x) => x.state), nobody.updated], fresh: [fresh.tiers.map((x) => x.state), fresh.updated],
  html: D.html(fresh, t) }};
""")
    assert out["nobody"] == [["none", "none", "none"], {"state": "never"}]
    assert out["fresh"] == [["none", "none", "none"], {"state": "never"}]
    assert "還沒存過" in out["html"]


# ---------------------------------------------------------------------------- persistence

@needs_node
def test_persistent_storage_shown_and_asked_for():
    out = run(r"""
const yes = D.view(await D.read({ storage: storage({ estimate: { usage: 1 }, persisted: true, persist: true }), offline: null }), {});
// not yet: the button asks; the browser says yes → shown as persistent, no button
let granted = false;
const s1 = storage({ estimate: { usage: 1 }, persisted: async () => granted, persist: async () => { granted = true; return true; } });
const before = D.view(await D.read({ storage: s1, offline: null }), {});
const asked = await D.ask(s1);
const after = D.view(await D.read({ storage: s1, offline: null }), {});
// the browser says no (Chrome / Safari decide by themselves)
const s2 = storage({ estimate: { usage: 1 }, persisted: false, persist: false });
const denied = await D.ask(s2);
// persist() missing or throwing: nothing to ask
const s3 = storage({ estimate: { usage: 1 }, persisted: false });
const noAsk = D.view(await D.read({ storage: s3, offline: null }), {});
const thrown = await D.ask(storage({ persist: async () => { throw new Error("blocked"); } }));
OUT = { yes: [yes.persisted, yes.canAsk], before: [before.persisted, before.canAsk], asked, after: [after.persisted, after.canAsk],
  denied, noAsk: [noAsk.persisted, noAsk.canAsk], thrown,
  htmlYes: D.html(yes, t), htmlNo: D.html(before, t), htmlDenied: D.html(before, t, { asked: denied }),
  htmlGranted: D.html(after, t, { asked: asked }) };
""")
    assert out["yes"] == ["yes", False]
    assert out["before"] == ["no", True]
    assert out["asked"] == "granted" and out["after"] == ["yes", False]
    assert out["denied"] == "denied"
    assert out["noAsk"] == ["no", False]
    assert out["thrown"] == "na"
    assert "要求持久保存" not in out["htmlYes"] and "data-ds-persist" not in out["htmlYes"]
    assert "data-ds-persist" in out["htmlNo"]
    assert "瀏覽器沒有同意" in out["htmlDenied"]
    assert "已取得持久保存" in out["htmlGranted"]


# ---------------------------------------------------------------------------- no API

@needs_node
def test_no_api_shows_unavailable_and_never_throws():
    out = run(r"""
const shapes = {
  noStorage: { storage: undefined, offline: undefined },
  emptyStorage: { storage: {}, offline: { } },
  rejecting: { storage: storage({ estimate: async () => { throw new Error("x"); }, persisted: async () => { throw new Error("y"); } }),
               offline: offline(() => { throw new Error("z"); }) },
  throwingSync: { storage: storage({ estimate: () => { throw new Error("x"); }, persisted: () => { throw new Error("y"); } }),
                  offline: { status: () => { throw new Error("z"); } } },
  garbage: { storage: storage({ estimate: { usage: "lots" }, persisted: "maybe" }), offline: offline("nope") },
  noWorker: { storage: storage({ estimate: { usage: 1048576 }, persisted: true }), offline: offline(null) },
};
OUT = {};
for (const [k, src] of Object.entries(shapes)) {
  const m = D.view(await D.read(src), {});
  OUT[k] = { usage: m.usage.state, persisted: m.persisted, canAsk: m.canAsk, tiers: m.tiers.map((x) => x.state),
             updated: m.updated.state, html: D.html(m, t) };
}
OUT.bareRead = D.view(await D.read(), {}).usage.state;
""")
    for k in ("noStorage", "emptyStorage", "rejecting", "throwingSync", "garbage"):
        o = out[k]
        assert o["usage"] == "na" and o["persisted"] == "na" and o["canAsk"] is False, k
        assert o["tiers"] == ["na", "na", "na"] and o["updated"] == "na", k
        assert o["html"].count("無法取得") >= 5, k                 # usage, P0, P1, P2, persistent, last refill
        assert "20 MB" in o["html"], k                             # the budget is fixed: always shown
    nw = out["noWorker"]                                           # storage works, the worker doesn't answer
    assert nw["usage"] == "ok" and nw["persisted"] == "yes" and nw["tiers"] == ["na", "na", "na"] and nw["updated"] == "na"
    assert out["bareRead"] == "na"


# ---------------------------------------------------------------------------- 加到主畫面

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
IPAD = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"
MAC = IPAD
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36"


@needs_node
@pytest.mark.parametrize("env,want", [
    ({"ua": IPHONE, "platform": "iPhone", "maxTouchPoints": 5}, True),                       # Safari tab
    ({"ua": IPHONE, "platform": "iPhone", "maxTouchPoints": 5, "standalone": True}, False),  # home-screen app (Safari's flag)
    ({"ua": IPHONE, "platform": "iPhone", "maxTouchPoints": 5, "displayStandalone": True}, False),  # display-mode: standalone
    ({"ua": IPAD, "platform": "MacIntel", "maxTouchPoints": 5}, True),                       # iPadOS asks as a Mac, with touch
    ({"ua": MAC, "platform": "MacIntel", "maxTouchPoints": 0}, False),                       # a Mac: no 7-day hint
    ({"ua": ANDROID, "platform": "Linux armv8l", "maxTouchPoints": 5}, False),
    ({}, False),
])
def test_the_home_screen_hint_only_on_an_iphone_tab(env, want):
    out = run(f"""
const e = {json.dumps(env)};
const m = D.view(await D.read({{ storage: storage({{ estimate: {{ usage: 1 }}, persisted: false }}), offline: null }}), {{ a2hs: D.isIosTab(e) }});
OUT = {{ hint: D.isIosTab(e), html: D.html(m, t) }};
""")
    assert out["hint"] is want
    assert ("加到主畫面" in out["html"]) is want
    if want:
        assert "7 天" in out["html"]


@needs_node
def test_the_environment_read_from_the_window():
    out = run(f"""
const win = (ua, platform, mtp, standalone, dm) => ({{ navigator: {{ userAgent: ua, platform, maxTouchPoints: mtp, standalone }},
  matchMedia: (q) => ({{ matches: dm && q === "(display-mode: standalone)" }}) }});
OUT = [D.isIosTab(D.envOf(win({json.dumps(IPHONE)}, "iPhone", 5, false, false))),
       D.isIosTab(D.envOf(win({json.dumps(IPHONE)}, "iPhone", 5, true, false))),
       D.isIosTab(D.envOf(win({json.dumps(IPHONE)}, "iPhone", 5, undefined, true))),
       D.isIosTab(D.envOf({{}}))];
""")
    assert out == [True, False, False, False]


# ---------------------------------------------------------------------------- the page

def test_the_settings_page_mounts_it_on_the_data_tab():
    page = (STATIC / "settings.html").read_text("utf-8")
    assert '<script src="/api/v1/static/device_storage.js"></script>' in page
    main = page[page.index("<main>"):page.index("</main>")]
    sec = main[main.index('<section id="device">'):]
    sec = sec[:sec.index("</section>")]
    assert 'data-i18n="settings.device.title"' in sec and 'id="ds-body"' in sec
    tabs = page[page.index("const TABS = {"):]
    tabs = tabs[:tabs.index("};")]
    assert re.search(r'data: \[[^\]]*"device"', tabs)
    assert 'DeviceStorage.mount($("ds-body")' in page


def test_the_texts_are_in_both_languages_with_the_same_placeholders():
    zh = json.loads((STATIC / "i18n" / "zh-TW" / "settings.json").read_text("utf-8"))
    en = json.loads((STATIC / "i18n" / "en" / "settings.json").read_text("utf-8"))
    keys = [k for k in zh if k.startswith("device.")]
    assert len(keys) >= 20
    js = (STATIC / "device_storage.js").read_text("utf-8")
    for k in keys:
        assert en.get(k), k
        assert set(re.findall(r"\{(\w+)\}", zh[k])) == set(re.findall(r"\{(\w+)\}", en[k])), k
        assert not re.search(r"[一-鿿]", en[k]), k
    # every key the script asks for exists
    for k in set(re.findall(r'\bT\("([\w.]+)"', js)):
        assert "device." + k in zh, k
    assert not re.search(r"[一-鿿]", re.sub(r"//[^\n]*|/\*.*?\*/", "", js, flags=re.S))   # texts only in the catalogs


@needs_node
def test_the_budget_is_the_offline_copys():
    r = subprocess.run([NODE, "-e", "const vm=require('vm'),fs=require('fs');const c={};c.window=c;vm.createContext(c);"
                        "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),c);"
                        "console.log(JSON.stringify([c.DeviceStorage.BUDGET, require(process.argv[2]).BUDGET]))",
                        str(STATIC / "device_storage.js"), str(STATIC / "offline_core.js")],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == [20 * MB, 20 * MB]
