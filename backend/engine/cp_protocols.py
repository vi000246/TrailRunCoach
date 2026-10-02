"""
CP-test protocols — the athlete picks one (課表偏好 `plan.prefs.cp_test_protocol`);
design: docs/research/cp-test-protocols.md §4–§5.

quick     20 min all-out, CP ≈ 0.95 × P20 (Ñancupil-Andrade et al. 2024, IJSM,
          Stryd runners, SEE 6.67 W). About 37 min in all. Default.
standard  12 min all-out, 30 min easy, 3 min all-out; two-point CP and W′.
          Long bout first, as Stryd's 9/3 (Ruiz-Alias et al. 2024: the order
          moves CP < 5 %), so leftover fatigue lands on the 3′ bout, where
          ∂CP/∂P3 = −1/3 instead of ∂CP/∂P12 = +4/3. About 70 min.
race      a 5–10 K race or time trial instead of a test session:
          CP = P_race · (T_race / 1800)^(−k), k = −0.07 (Riegel, anchored at
          30 min, CP ≈ 30-min power per Ñancupil-Andrade 2024) — an
          extrapolation, only for 15–70 min efforts. No session is scheduled.

This module holds the session table (week_plan / plan_store / COROS use it)
and the per-protocol analysis (workout_review calls it):

measure_bouts(t, power, hr)
    JSON-safe numbers from one activity (memoised with the review measure):
    the non-overlapping 12′ / 3′ windows, the best 20′, the best race-like
    window, each bout's HR peak and last-minute power.
result(bouts, protocol, …)
    {protocol, method, cp, wprime, cp_range, quality, checks, reasons}.
    quality: 可信 / 參考 / 不採用.

Quality checks marked 自組 are our own thresholds — no study validated an HR
or pacing rule for "not all-out" (§3.4); they are to be calibrated on the
athlete's own tests.
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np

PROTOCOLS = ("quick", "standard", "race")
DEFAULT = "quick"

# W′ prior for a single bout: Ruiz-Alias et al. 2025 (EJSS, PMC11770271),
# amateur Stryd 9/3 two-point W′ — men 13.1 ± 4.0 kJ, women 6.4 ± 2.2 kJ
WPRIME_PRIOR = {"male": (13100.0, 4000.0), "female": (6400.0, 2200.0)}
SEX_LABEL = {"male": "男", "female": "女"}


def athlete_sex(ds) -> Optional[str]:
    """"male" / "female" from the settings-page profile (plan.profile), else
    the WKO5 athlete file (3001/3017); None when neither has it."""
    prof = getattr(getattr(ds, "plan", None), "profile", None) or {}
    if prof.get("sex") in WPRIME_PRIOR:
        return prof["sex"]
    try:
        root = ds.athlete.root.get(3001)
        s = root.get(3017) if root is not None else None
    except AttributeError:
        s = None
    return s if s in WPRIME_PRIOR else None


def wprime_prior(sex: Optional[str]) -> tuple[float, float, str]:
    """(W′ J, SD J, label) of the single-bout prior for this sex. Without a
    sex on file the men's value is used (the long-standing default) and the
    label says so."""
    key = sex if sex in WPRIME_PRIOR else "male"
    w, sd = WPRIME_PRIOR[key]
    who = f"業餘{SEX_LABEL[key]}性" if sex in WPRIME_PRIOR else "未填性別，用男性值"
    return w, sd, f"W′ 先驗 {w / 1000:.1f} kJ（Ruiz-Alias 2025，{who}；推估）"
TT20_FACTOR = 0.95                 # CP ≈ 0.95 × P20 (Ñancupil-Andrade 2024)
RIEGEL_K = -0.07                   # racepower/riegel.py; Stryd table 10–42 km implies −0.069
RACE_ANCHOR_S = 1800.0             # CP ≈ 30-min power
RACE_MIN_S, RACE_MAX_S = 15 * 60, 70 * 60
# two-point CP (3–12 min model) runs ≈ 5 % above a 30-min CP (tt20 / race):
# Bishop 1998, Mattioni Maturana 2018 (docs/research/cp-test-protocols.md §1B.1).
# 外插 — to calibrate on the athlete's own tests of both kinds.
CP_2PT_OVER_30MIN = 1.05

# quality checks (自組 unless noted)
HR_GAP_BPM = 8.0                   # 自組: 3′ peak HR ≥ 12′ peak HR − 8 bpm
HR_BELOW_LTHR_BPM = 5.0            # 自組: a ≥ 12′ all-out bout peaks above LTHR − 5 bpm
LAST_MIN_RATIO = 1.08              # 自組: last-minute power ≤ 1.08 × the bout average
RECOVERY_MIN_S = 25 * 60           # §3.2: 30 min between bouts; < 25 min flagged
TT20_CROSS_TOL = 0.03              # 0.95·P20 vs P20 − W′prior/1200 within 3 %
GAP_S = 600                        # the 3′ window starts ≥ 10 min from the 12′ bout
HR_LAG_S = 15                      # HR peaks a few seconds after the bout

METHOD_LABEL = {"2pt": "兩點（12 分＋3 分）", "1pt_prior": "單段＋W′ 先驗", "tt20": "20 分全力 × 0.95",
                "race": "比賽換算（Riegel）"}

TABLE = {
    "quick": dict(
        label="快速：20 分全力", hint="約 37 分；準確度約 ±3%（SEE 6.7 W），不測 W′",
        title="CP 測試 20 分全力", minutes=37, tss=45.0,
        target="20 分鐘全力、配速平均；最後 2 分可以加速",
        detail="暖身 12 分（含 3 趟 20 秒加速）；20 分全力；緩和 5 分。平路或田徑場",
        source="Ñancupil-Andrade 2024：跑步 CP ≈ 0.95 × 20 分全力功率",
        steps="暖身 12 分 → 20 分全力 → 緩和 5 分", warm=12, bouts=(20,), rest=0, cool=5),
    "standard": dict(
        label="標準：12 分＋休 30 分＋3 分", hint="約 70 分；兩點 CP＋W′，休息不能縮短",
        title="CP 測試 12 分 + 3 分", minutes=70, tss=65.0,
        target="兩段都全力、配速平均；中間休 30 分鐘（不要縮短）",
        detail="暖身 15 分；12 分全力；休 30 分（走或極慢跑）；3 分全力；緩和 10 分。平路或田徑場",
        source="Stryd 9/3 兩段測試（Ruiz-Alias 2022/2025），先長後短；每 4–6 週",
        steps="暖身 15 分 → 12 分全力 → 休 30 分 → 3 分全力 → 緩和 10 分", warm=15, bouts=(12, 3), rest=30,
        cool=10),
    "race": dict(
        label="用比賽：5–10 K 比賽或計時跑", hint="不另外排；用 Riegel 換算（外插），不測 W′",
        title="CP 測試：5–10 K 比賽或計時跑", minutes=0, tss=0.0, target="", detail="", source="",
        steps="", warm=0, bouts=(), rest=0, cool=0),
}
NOTE_RACE = "用 5–10 K 比賽或計時跑代替 CP 測試"


def norm(protocol: Optional[str]) -> str:
    return protocol if protocol in PROTOCOLS else DEFAULT


def session_for(protocol: Optional[str]) -> Optional[dict]:
    """The week plan's CP-test session (overview.Session fields); None for
    `race` (nothing is scheduled; the testing indicator says NOTE_RACE)."""
    p = norm(protocol)
    if p == "race":
        return None
    t = TABLE[p]
    return {"id": "test", "kind": "test", "title": t["title"], "minutes": t["minutes"], "target": t["target"],
            "detail": t["detail"], "source": t["source"], "tss": t["tss"], "protocol": p}


def protocol_of(s: Optional[dict]) -> Optional[str]:
    """A stored / generated test session's protocol; legacy rows without the
    field are read from the title (「3 分 + 12 分」 = standard, short bout first)."""
    if not s:
        return None
    p = s.get("protocol")
    if p in PROTOCOLS:
        return p
    title = str(s.get("title") or "")
    if re.search(r"20\s*分", title):
        return "quick"
    if "+" in title or "＋" in title:
        return "standard"
    if re.search(r"比賽|計時|\d+\s*[kK]\b", title):
        return "race"
    return None


def cap_note(protocol: Optional[str]) -> str:
    """plan_prefs.NOTE_TEST: the test is exempt from the per-session cap."""
    t = TABLE[norm(protocol)]
    return f"CP 測試的流程固定（{t['steps']}），不受單次時間上限"


# ---------------------------------------------------------------------------
# measuring the bouts (arrays in, JSON-safe numbers out)
# ---------------------------------------------------------------------------

def _grid(t, x) -> Optional[np.ndarray]:
    """1-s grid from the first sample (gaps interpolated, like _grid1 in
    workout_review but without its > MAX_DT blanking: bouts are continuous)."""
    if x is None:
        return None
    t = np.asarray(t, float)
    x = np.asarray(x, float)
    if len(x) != len(t):
        return None
    ok = np.isfinite(t) & np.isfinite(x)
    if ok.sum() < 2:
        return None
    tt, xx = t[ok], x[ok]
    g = np.arange(tt[0], tt[-1] + 1.0)
    return np.interp(g, tt, xx)


def _roll(p: np.ndarray, n: int) -> Optional[np.ndarray]:
    if p is None or len(p) < n:
        return None
    c = np.cumsum(np.concatenate([[0.0], np.nan_to_num(p)]))
    return (c[n:] - c[:-n]) / n


def _bout(p: np.ndarray, h: Optional[np.ndarray], i: int, n: int) -> dict:
    seg = p[i:i + n]
    out = {"start_s": float(i), "duration_s": float(n), "power": float(np.mean(seg)),
           "last60": float(np.mean(seg[-60:])) if n >= 120 else None, "hr_peak": None}
    if h is not None:
        w = h[i:min(len(h), i + n + HR_LAG_S)]
        w = w[np.isfinite(w) & (w > 0)]
        out["hr_peak"] = float(w.max()) if len(w) else None
    return out


def measure_bouts(t, power, hr=None) -> Optional[dict]:
    """The candidate bouts of each protocol in one activity (None without power)."""
    p = _grid(t, power)
    if p is None or not np.isfinite(p).any() or np.nanmax(p) <= 0:
        return None
    h = _grid(t, hr) if hr is not None else None
    out: dict = {"standard": None, "quick": None, "race": None, "best180": None}
    c180 = _roll(p, 180)
    if c180 is not None:
        out["best180"] = float(c180.max())
    # standard: best 12′, then the best 3′ ≥ GAP_S away (before or after) — never overlapping
    c720 = _roll(p, 720)
    if c720 is not None and c180 is not None:
        i12 = int(np.argmax(c720))
        ok = np.ones(len(c180), bool)
        ok[max(0, i12 - 180 - GAP_S + 1):min(len(c180), i12 + 720 + GAP_S)] = False
        if ok.any():
            i3 = int(np.argmax(np.where(ok, c180, -np.inf)))
            b12, b3 = _bout(p, h, i12, 720), _bout(p, h, i3, 180)
            first, second = (b12, b3) if i12 < i3 else (b3, b12)
            out["standard"] = {"long": b12, "short": b3, "long_first": i12 < i3,
                               "gap_s": float(second["start_s"] - (first["start_s"] + first["duration_s"]))}
    c1200 = _roll(p, 1200)
    if c1200 is not None:
        out["quick"] = _bout(p, h, int(np.argmax(c1200)), 1200)
    # race: the window 15–70 min whose Riegel-converted CP is highest (easy
    # warm-up / cool-down minutes lower the power more than the factor gains)
    best = None
    for n in range(RACE_MIN_S, min(RACE_MAX_S, len(p)) + 1, 60):
        c = _roll(p, n)
        if c is None:
            break
        i = int(np.argmax(c))
        cp = float(c[i]) * (n / RACE_ANCHOR_S) ** (-RIEGEL_K)
        if best is None or cp > best[0]:
            best = (cp, i, n)
    if best is not None:
        out["race"] = _bout(p, h, best[1], best[2])
    return out


# ---------------------------------------------------------------------------
# per-protocol analysis
# ---------------------------------------------------------------------------

def _check(cid: str, ok: bool, text: str, own: bool = True) -> dict:
    return {"id": cid, "ok": bool(ok), "text": text + ("（自組門檻）" if own and not ok else ""), "own": own}


def _pacing(b: dict, name: str) -> Optional[dict]:
    if b.get("last60") is None or not b.get("power"):
        return None
    r = b["last60"] / b["power"]
    return _check("pacing", r <= LAST_MIN_RATIO,
                  f"{name}最後 1 分 {b['last60']:.0f} W，比該段平均 {b['power']:.0f} W 高 {(r - 1) * 100:.0f}%："
                  "前面有保留，這段只是下限" if r > LAST_MIN_RATIO else f"{name}配速平均（最後 1 分 {(r - 1) * 100:+.0f}%）")


def _hr_vs_lthr(b: dict, name: str, lthr: Optional[float]) -> Optional[dict]:
    if not lthr or b.get("hr_peak") is None:
        return None
    ok = b["hr_peak"] >= lthr - HR_BELOW_LTHR_BPM
    return _check("hr", ok, f"{name}最高心率 {b['hr_peak']:.0f}，" +
                  (f"到 LTHR {lthr:.0f} 附近" if ok else f"比 LTHR {lthr:.0f} 低 {lthr - b['hr_peak']:.0f} bpm：不像全力"))


def _quality(checks: list[dict], floor: str = "可信") -> str:
    order = ("可信", "參考", "不採用")
    q = order.index(floor)
    for c in checks:
        if not c["ok"]:
            q = max(q, order.index(c.get("fail", "參考")))
    return order[q]


def _single(b: dict, prior: tuple, n: int) -> tuple[float, list]:
    w, sd = prior
    return b["power"] - w / n, [b["power"] - (w + sd) / n, b["power"] - (w - sd) / n]


def result(bouts: Optional[dict], protocol: Optional[str], lthr: Optional[float] = None,
           sex: Optional[str] = None) -> Optional[dict]:
    """The protocol's CP from measure_bouts() output. None when the activity
    has no bout of that protocol."""
    if not bouts:
        return None
    p = norm(protocol)
    prior = WPRIME_PRIOR.get(sex or "male", WPRIME_PRIOR["male"])
    w0, sd0 = prior
    prior_txt = f"W′ 先驗 {w0 / 1000:.1f} ± {sd0 / 1000:.1f} kJ（Ruiz-Alias 2025）"
    base = {"protocol": p, "protocol_label": TABLE[p]["label"]}
    if p == "standard":
        s = bouts.get("standard")
        if not s:
            return None
        L, S = s["long"], s["short"]
        p12, p3 = L["power"], S["power"]
        checks: list[dict] = []
        cp2 = (p12 * 720 - p3 * 180) / 540.0
        w2 = (p3 - cp2) * 180.0
        model_ok = p3 > p12 and (w0 - 2 * sd0) <= w2 <= (w0 + 2 * sd0)
        checks.append(_check("model", model_ok,
                             f"3 分 {p3:.0f} W > 12 分 {p12:.0f} W、W′ {w2 / 1000:.1f} kJ 在先驗 ± 2 SD 內"
                             if model_ok else
                             (f"3 分 {p3:.0f} W 不高於 12 分 {p12:.0f} W：兩點模型不成立" if p3 <= p12 else
                              f"W′ {w2 / 1000:.1f} kJ 在先驗 {w0 / 1000:.1f} ± {2 * sd0 / 1000:.1f} kJ 之外"), own=False))
        hr_ok = True
        if S.get("hr_peak") is not None and L.get("hr_peak") is not None:
            gap = L["hr_peak"] - S["hr_peak"]
            hr_ok = gap <= HR_GAP_BPM
            checks.append(_check("hr", hr_ok, f"3 分段最高心率 {S['hr_peak']:.0f}，" +
                                 (f"和 12 分段 {L['hr_peak']:.0f} 相近" if hr_ok else
                                  f"比 12 分段 {L['hr_peak']:.0f} 低 {gap:.0f} bpm：不是全力")))
        pc = _pacing(L, "12 分段")
        if pc:
            checks.append(pc)
        rec_ok = s["gap_s"] >= RECOVERY_MIN_S
        checks.append(_check("recovery", rec_ok, f"兩段間隔 {s['gap_s'] / 60:.0f} 分" +
                             ("" if rec_ok else "（< 25 分，課表是 30 分）：第二段帶著疲勞"), own=False))
        if model_ok and hr_ok:
            q = _quality([c for c in checks if c["id"] not in ("model", "hr")])
            return {**base, "method": "2pt", "cp": cp2, "wprime": w2, "cp_range": [cp2, cp2], "p3": p3, "p12": p12,
                    "quality": q, "checks": checks, "reasons": [c["text"] for c in checks if not c["ok"]],
                    "bouts": [S, L] if not s["long_first"] else [L, S]}
        cp, rng = _single(L, prior, 720)
        hl = _hr_vs_lthr(L, "12 分段", lthr)
        if hl is not None:
            hl["fail"] = "不採用"
            checks.append(hl)
        return {**base, "method": "1pt_prior", "cp": cp, "wprime": None, "wprime_prior": w0, "cp_range": rng,
                "p3": p3, "p12": p12, "quality": _quality(checks, "參考"), "checks": checks,
                "reasons": [c["text"] for c in checks if not c["ok"]] + [f"只用 12 分段，{prior_txt}"],
                "bouts": [S, L] if not s["long_first"] else [L, S]}
    if p == "quick":
        b = bouts.get("quick")
        if not b:
            return None
        p20 = b["power"]
        cp = TT20_FACTOR * p20
        alt = p20 - w0 / 1200.0
        checks = []
        d = abs(cp - alt) / cp
        checks.append(_check("consistency", d <= TT20_CROSS_TOL,
                             f"0.95 × P20 = {cp:.0f} W、P20 − W′/1200 = {alt:.0f} W，差 {d * 100:.1f}%"
                             + ("" if d <= TT20_CROSS_TOL else "（> 3%）"), own=False))
        pc = _pacing(b, "20 分段")
        if pc:
            checks.append(pc)
        hl = _hr_vs_lthr(b, "20 分段", lthr)
        if hl is not None:
            hl["fail"] = "不採用"
            checks.append(hl)
        return {**base, "method": "tt20", "cp": cp, "wprime": None, "wprime_prior": w0,
                "cp_range": sorted([cp, alt]), "p20": p20, "quality": _quality(checks), "checks": checks,
                "reasons": [c["text"] for c in checks if not c["ok"]], "bouts": [b]}
    b = bouts.get("race")
    if not b or not (RACE_MIN_S <= b["duration_s"] <= RACE_MAX_S):
        return None
    f = (b["duration_s"] / RACE_ANCHOR_S) ** (-RIEGEL_K)
    cp = b["power"] * f
    checks = []
    hl = _hr_vs_lthr(b, "比賽段", lthr)
    if hl is not None:
        checks.append(hl)
    lo, hi = (b["duration_s"] / RACE_ANCHOR_S) ** (-(RIEGEL_K - 0.02)), (b["duration_s"] / RACE_ANCHOR_S) ** (-(RIEGEL_K + 0.02))
    rng = sorted([b["power"] * lo, b["power"] * hi])
    return {**base, "method": "race", "cp": cp, "wprime": None, "cp_range": rng, "race_s": b["duration_s"],
            "race_power": b["power"], "quality": _quality(checks), "checks": checks,
            "reasons": [c["text"] for c in checks if not c["ok"]] +
            [f"{b['duration_s'] / 60:.0f} 分 {b['power']:.0f} W × {f:.3f}（Riegel k {RIEGEL_K}，以 30 分為錨點；外插）"],
            "bouts": [b]}


# ---------------------------------------------------------------------------
# comparison: only against the previous result of the same method
# ---------------------------------------------------------------------------

def _basis(method: Optional[str]) -> str:
    """Which CP definition a method measures: '2pt' (3–12 min model) or '30min'."""
    return "2pt" if method in (None, "2pt", "1pt_prior") else "30min"


def reference(thresholds, test_date: str, method: str, cp_now: Optional[float] = None) -> dict:
    """What a new test result is compared with.

    1. The latest plan threshold row before `test_date` with a CP of the same
       method (rows without cp_method are legacy 3′/12′ tests: '2pt').
    2. Else the CP in effect, converted to the test's definition when its
       method is known and differs (two-point CP ≈ 1.05 × a 30-min CP, 外插);
       a CP from WKO5 (no plan row) is compared as is.
    {"cp", "date", "method", "same_method", "converted"}; cp None = nothing to compare."""
    rows = sorted([t for t in thresholds or [] if getattr(t, "cp", None) is not None and t.date < test_date],
                  key=lambda t: t.date)
    same = [t for t in rows if (getattr(t, "cp_method", None) or "2pt") == method]
    if same:
        t = same[-1]
        return {"cp": float(t.cp), "date": t.date, "method": getattr(t, "cp_method", None) or "2pt",
                "same_method": True, "converted": False}
    if rows:
        t = rows[-1]
        m = getattr(t, "cp_method", None) or "2pt"
        cp = float(t.cp)
        conv = False
        if _basis(m) != _basis(method):
            cp = cp / CP_2PT_OVER_30MIN if _basis(m) == "2pt" else cp * CP_2PT_OVER_30MIN
            conv = True
        return {"cp": cp, "date": t.date, "method": m, "same_method": False, "converted": conv}
    return {"cp": cp_now, "date": None, "method": None, "same_method": False, "converted": False}


def apply_payload(res: dict, date: str, activity_index: Optional[int] = None) -> Optional[dict]:
    """The body of POST /api/v1/plan/thresholds/apply-cp for one result, or
    None when the result must not be applied (不採用). W′ only when measured
    (two-point): a prior is not the athlete's W′."""
    if not res or res.get("quality") == "不採用" or res.get("cp") is None:
        return None
    cp = round(float(res["cp"]))
    return {"date": date, "cp": cp, "wprime": round(res["wprime"]) if res.get("wprime") is not None else None,
            "cp_method": res["method"], "activity_index": activity_index,
            "note": f"{TABLE[res['protocol']]['label']}；{METHOD_LABEL.get(res['method'], res['method'])}；品質 {res['quality']}",
            "label": f"套用這次的 CP {cp} W" + ("（參考）" if res.get("quality") == "參考" else "")}
