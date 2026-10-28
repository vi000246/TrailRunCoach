"""
CP-test protocols — the athlete picks one (課表偏好 `plan.prefs.cp_test_protocol`);
design: docs/research/cp-test-protocols.md §4–§5.

quick     20 min all-out. About 37 min in all. Default.
standard  12 min all-out, 30 min easy, 3 min all-out. Long bout first, as
          Stryd's 9/3 (Ruiz-Alias et al. 2024). About 70 min.
race      a 5–10 K race or time trial instead of a test session (15–70 min
          efforts). No session is scheduled.

This module holds the session table (week_plan / plan_store / COROS use it)
and the per-protocol reading of a test (workout_review calls it):

measure_bouts(t, power, hr)
    JSON-safe numbers from one activity (memoised with the review measure):
    the non-overlapping 12′ / 3′ windows, the best 20′, the best race-like
    window, each bout's HR peak and last-minute power.
result(bouts, protocol, …)
    {protocol, method "pd_model", cp, wprime None, cp_range, pd, checks,
    reasons, bouts, …}: the test's CP is WKO5's mFTP fitted on the 90-day
    mean-max envelope up to the test day, the test in it.

SP-449 (the user 2026-10-13, 「不要擅自決定我做的 CP test 不算」) judged a test the way WKO5
builds its mFTP (docs/research/wko5-mftp-logic.md): WKO5 never drops an effort, it fits its PD
model to the upper edge of the 90-day mean-max curve. SP-468 (the user 2026-10-13, 「兩點算法…
刪掉，還有待套用這個也刪掉，完全比照 WKO5 用自動計算的」): a CP test is only an effort on that
curve — no two-point / single-bout / 0.95 × P20 / Riegel CP, nothing to apply (no 「已偵測、待
套用」, no apply-cp endpoint); the 課表's CP follows the same fit by itself
(fitdataset.stryd_cp_fit, changing only by ≥ fitdataset.CP_HOLD_W). The checks (HR, pacing,
recovery — 推估, no study validated them, §3.4) only describe whether the effort was all-out.
"""
from __future__ import annotations

import re
from typing import Optional

import numpy as np

from backend.i18n import M_, N_, _

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


def sex_or_default(ds) -> tuple[str, str]:
    """(sex, where it came from) for the models that need one: 設定頁 / WKO5,
    else the men's value labelled 「預設（男，推估）」 (未填性別). The where is an M_() text (SP-990: the
    calculator's 你的數據 card, a cached answer shown in the request's language)."""
    prof = getattr(getattr(ds, "plan", None), "profile", None) or {}
    s = athlete_sex(ds)
    if s is None:
        return "male", M_("預設（男，推估）")
    return s, M_("設定頁") if prof.get("sex") == s else M_("對照資料夾")


def wprime_prior(sex: Optional[str]) -> tuple[float, float, str]:
    """(W′ J, SD J, label) of the single-bout prior for this sex. Without a
    sex on file the men's value is used (the long-standing default) and the
    label says so."""
    key = sex if sex in WPRIME_PRIOR else "male"
    w, sd = WPRIME_PRIOR[key]
    who = f"業餘{SEX_LABEL[key]}性" if sex in WPRIME_PRIOR else "未填性別，用男性值"
    return w, sd, f"無氧容量先驗 {w / 1000:.1f} kJ（Ruiz-Alias 2025，{who}；推估）"
RIEGEL_K = -0.07                   # the best race-like window (measure_bouts): racepower/riegel.py
RACE_ANCHOR_S = 1800.0             # CP ≈ 30-min power
RACE_MIN_S, RACE_MAX_S = 15 * 60, 70 * 60

# effort checks (推估 unless noted): they only describe the effort, the CP is WKO5's mFTP (SP-468)
HR_GAP_BPM = 8.0                   # 推估: 3′ peak HR ≥ 12′ peak HR − 8 bpm
HR_BELOW_LTHR_BPM = 5.0            # 推估: a ≥ 12′ all-out bout peaks above LTHR − 5 bpm
LAST_MIN_RATIO = 1.08              # 推估: last-minute power ≤ 1.08 × the bout average
RECOVERY_MIN_S = 25 * 60           # §3.2: 30 min between bouts; < 25 min flagged
GAP_S = 600                        # the 3′ window starts ≥ 10 min from the 12′ bout
HR_LAG_S = 15                      # HR peaks a few seconds after the bout

# how a plan row's CP was measured (planning.Threshold.cp_method: the rows 「套用這次的 CP」 wrote
# before SP-468 — kept for those rows' labels); a CP test's own result is always "pd_model"
METHOD_LABEL = {"2pt": "兩點（12 分＋3 分）", "1pt_prior": "單段＋無氧容量先驗", "tt20": "20 分全力 × 0.95",
                "race": "比賽換算（Riegel）",
                "pd_model": N_("PD 模型擬合（近 90 天 mean-max 上緣，含這次測試）")}
# a CP test without a PD fit (fewer than fitdataset.CP_FIT_MIN_RUNS Stryd runs in 90 days, or no
# valid fit): no CP — no formula stands in for WKO5's model (SP-468)
NO_FIT = N_("算不出模型閾值功率（Modelled threshold power）：測試那天以前 90 天的 Stryd 跑步不到 5 次，"
            "或 PD 模型擬合不成立（不用公式代替）")

TABLE = {
    "quick": dict(
        label=N_("快速：20 分全力"), hint="；".join((N_("約 37 分"), N_("準確度約 ±3%（SEE 6.7 W），不測無氧容量"))),
        title="CP 測試 20 分全力", minutes=37, tss=45.0,
        steps="暖身 12 分 → 20 分全力 → 緩和 5 分", warm=12, bouts=(20,), rest=0, cool=5),
    "standard": dict(
        label=N_("標準：12 分＋休 30 分＋3 分"), hint="；".join((N_("約 70 分"), N_("兩點 CP＋無氧容量，休息不能縮短"))),
        title="CP 測試 12 分 + 3 分", minutes=70, tss=65.0,
        steps="暖身 15 分 → 12 分全力 → 休 30 分 → 3 分全力 → 緩和 10 分", warm=15, bouts=(12, 3), rest=30,
        cool=10),
    "race": dict(
        label=N_("用比賽：5–10 K 比賽或計時跑"), hint="；".join((N_("不另外排"), N_("用 Riegel 換算（外插），不測無氧容量"))),
        title="CP 測試：5–10 K 比賽或計時跑", minutes=0, tss=0.0,
        steps="", warm=0, bouts=(), rest=0, cool=0),
}
NOTE_RACE = N_("用 5–10 K 比賽或計時跑代替 CP 測試")


def norm(protocol: Optional[str]) -> str:
    return protocol if protocol in PROTOCOLS else DEFAULT


def _texts(p: str) -> dict:
    """The session's 目標 / 說明 / 出處 (SP-905: msgids joined with 「；」「。」, so an English page reads
    them back; the 「暖身 N 分」「緩和 N 分」 the step builders parse stay)."""
    t = TABLE[p]
    if p == "quick":
        return {"target": _("20 分鐘全力、配速平均") + "；" + _("最後 2 分可以加速"),
                "detail": "；".join((_("暖身 {n} 分（含 3 趟 20 秒加速）", n=t["warm"]), _("{n} 分全力", n=t["bouts"][0]),
                                    _("緩和 {n} 分", n=t["cool"]))) + "。" + _("平路或田徑場"),
                "source": _("Ñancupil-Andrade 2024：跑步 CP ≈ 0.95 × 20 分全力功率")}
    return {"target": _("兩段都全力、配速平均") + "；" + _("中間休 {n} 分鐘（不要縮短）", n=t["rest"]),
            "detail": "；".join((_("暖身 {n} 分", n=t["warm"]), _("{n} 分全力", n=t["bouts"][0]),
                                _("休 {n} 分（走或極慢跑）", n=t["rest"]), _("{n} 分全力", n=t["bouts"][1]),
                                _("緩和 {n} 分", n=t["cool"]))) + "。" + _("平路或田徑場"),
            "source": _("Stryd 9/3 兩段測試（Ruiz-Alias 2022/2025），先長後短") + "；" + _("每 4–6 週")}


def session_for(protocol: Optional[str]) -> Optional[dict]:
    """The week plan's CP-test session (overview.Session fields); None for
    `race` (nothing is scheduled; the testing indicator says NOTE_RACE)."""
    p = norm(protocol)
    if p == "race":
        return None
    t = TABLE[p]
    return {"id": "test", "kind": "test", "title": t["title"], "minutes": t["minutes"], **_texts(p),
            "tss": t["tss"], "protocol": p}


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
    return M_("CP 測試的流程固定（{steps}），不受單次時間上限", steps=t["steps"])


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
    return {"id": cid, "ok": bool(ok), "text": text + ("（推估門檻）" if own and not ok else ""), "own": own}


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
                  (f"到 LTHR {lthr:.0f} 附近" if ok else
                   f"比 LTHR {lthr:.0f} 低 {lthr - b['hr_peak']:.0f} bpm：不像全力（或 LTHR 估高了）"))


def result(bouts: Optional[dict], protocol: Optional[str], lthr: Optional[float] = None,
           sex: Optional[str] = None, pd=None) -> Optional[dict]:
    """The test's bouts (measure_bouts() output) and its CP: WKO5's mFTP fitted on the 90-day
    mean-max envelope up to the test day, the test in it — method "pd_model" for every protocol
    (SP-468, the user 2026-10-13: 「完全比照 WKO5 用自動計算的」; no two-point, single-bout,
    0.95 × P20 or Riegel CP any more). `pd`: a callable (or a dict) giving that fit
    {mftp, frc, date, runs} or None (racepower.athlete.stryd_pd, the 課表's own fit); without it
    `cp` is None and a reason says so. The checks only describe the effort (was it all-out); they
    change nothing — WKO5 never drops an effort. None when the activity has no bout of the
    protocol. `sex` is kept for the callers' signature."""
    if not bouts:
        return None
    p = norm(protocol)
    checks: list[dict] = []
    if p == "standard":
        s = bouts.get("standard")
        if not s:
            return None
        L, S = s["long"], s["short"]
        p12, p3 = L["power"], S["power"]
        checks.append(_check("curve", p3 > p12, f"3 分 {p3:.0f} W 高於 12 分 {p12:.0f} W" if p3 > p12 else
                             f"3 分 {p3:.0f} W 沒有高於 12 分 {p12:.0f} W：3 分這點由 12 分段裡最好的 3 分鐘決定"
                             "（曲線取每個時長的最大值）", own=False))
        if S.get("hr_peak") is not None and L.get("hr_peak") is not None:
            gap = L["hr_peak"] - S["hr_peak"]
            hr_ok = gap <= HR_GAP_BPM
            checks.append(_check("hr", hr_ok, f"3 分段最高心率 {S['hr_peak']:.0f}，" +
                                 (f"和 12 分段 {L['hr_peak']:.0f} 相近" if hr_ok else
                                  f"比 12 分段 {L['hr_peak']:.0f} 低 {gap:.0f} bpm：3 分段可能沒全力")))
        pc = _pacing(L, "12 分段")
        if pc:
            checks.append(pc)
        rec_ok = s["gap_s"] >= RECOVERY_MIN_S
        checks.append(_check("recovery", rec_ok, f"兩段間隔 {s['gap_s'] / 60:.0f} 分" +
                             ("" if rec_ok else "（< 25 分，課表是 30 分）：第二段帶著疲勞"), own=False))
        hl = _hr_vs_lthr(L, "12 分段", lthr)
        extra = {"p3": p3, "p12": p12, "bouts": [S, L] if not s["long_first"] else [L, S]}
    elif p == "quick":
        b = bouts.get("quick")
        if not b:
            return None
        pc = _pacing(b, "20 分段")
        if pc:
            checks.append(pc)
        hl = _hr_vs_lthr(b, "20 分段", lthr)
        extra = {"p20": b["power"], "bouts": [b]}
    else:
        b = bouts.get("race")
        if not b or not (RACE_MIN_S <= b["duration_s"] <= RACE_MAX_S):
            return None
        hl = _hr_vs_lthr(b, "比賽段", lthr)
        extra = {"race_s": b["duration_s"], "race_power": b["power"], "bouts": [b]}
    if hl is not None:
        checks.append(hl)
    fit = pd() if callable(pd) else pd
    mftp = float(fit["mftp"]) if fit and fit.get("mftp") else None
    reasons = [c["text"] for c in checks if not c["ok"]]
    if mftp is None:
        reasons.append(NO_FIT)
    return {"protocol": p, "protocol_label": TABLE[p]["label"], "method": "pd_model", "cp": mftp, "wprime": None,
            "cp_range": [mftp, mftp] if mftp is not None else None, "pd": fit if mftp is not None else None,
            "checks": checks, "reasons": reasons, **extra}
