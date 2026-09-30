"""
Audit the units / axes of every chart in every view.

Renders each chart the way the viewer gets it (render_chart, the athlete's own
engine config = parity off), season charts over the last 365 days and workout
charts on a few representative workouts, and records per series: y-axis id,
points, min / median / max, integer-ness, decimals. Then flags:

    imperial         imperial axis id or english() in the expression
    percent-range    PERCENT series outside [-1, 2] (already x100, or not a fraction)
    duration-scale   duration axis whose values look like minutes / hours
    magnitude        values implausible for the unit (W on a kJ axis, km under m, ...)
    axis-mismatch    the series' quantity (distance, IF, tss, ...) isn't the axis unit
    clip             fixed axis min/max hides > 5% of the points
    cadence          running cadence on RPM that is single-leg (x2 for steps/min)
    pace-base        pace series holding s/km or km/h instead of min/km (normalised)
    error            the series fails ("unsupported function — pending" when the
                     evaluator lacks a function; another agent is adding those)

The WKO5 views are audited raw (views/wko5_fixes.json NOT applied), then once
more with the fixes to show what's left. Output: docs/reports/chart-units-audit.md
and a JSON dump next to it (--json).

    python -m backend.scripts.audit_chart_units [--view NAME] [--limit N] [--json PATH]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.api import wko5views as API                       # noqa: E402
from backend.engine.wko5expr import units as U                 # noqa: E402
from backend.engine.wko5expr.chartfixes import apply_fixes, load_fixes, unmatched  # noqa: E402
from backend.engine.wko5expr.customviews import load_custom_views  # noqa: E402
from backend.engine.wko5expr.render import render_chart        # noqa: E402
from backend.engine.wko5expr.render_units import data_ys       # noqa: E402

REPORT = ROOT / "docs" / "reports" / "chart-units-audit.md"

# plausible |median| of a series' stored values per unit id (after the render
# pipeline: pace in min/km outside parity; CM and ms as WKO5 hands them to expressions)
PLAUSIBLE = {
    "WATTS": (0, 2500), "WATTSKG": (0.2, 30), "KJ": (0.5, 30000), "TSS": (0.5, 3000),
    "TSSPERDAY": (0, 400), "BPM": (30, 230), "RPM": (20, 230), "METERS": (0.05, 9000),
    "KM": (0.05, 3000), "KPH": (0.02, 80), "METERSPERSECOND": (0.005, 25),
    "METERSPERHOUR": (0, 4000), "PACEKM": (1.5, 240), "CM": (0.5, 50),
    "MILLISECONDS": (30, 3000), "L/min": (0.3, 10), "mL/min/kg": (5, 100),
    "CELSIUS": (0, 50),
}

# why a finding is left alone: (check, chart regex, series regex) -> reason
LEFT_AS_IS = [
    ("percent-range", r"Daily % of CTL", r"300%",
     "intended: the series only shows days with TSS ≥ 300% of CTL, so fractions ≥ 3 are right"),
]


def left_reason(f: dict):
    for check, chart_re, series_re, why in LEFT_AS_IS:
        if f["check"] == check and re.search(chart_re, f["chart"] or "") and re.search(series_re, f["series"] or ""):
            return why
    return None
MAGNITUDE_HINT = {
    "KM": "values look like metres", "METERS": "values look like km", "CM": "values look like metres?",
    "MILLISECONDS": "values look like seconds?", "KJ": "values look like J or W",
    "WATTS": "not watts", "PERCENT": "not a fraction",
}

# the quantity a bare identifier carries -> unit ids it can sit on
IDENT_UNITS = {
    "if": {"NONE", "CUSTOMIF", "PERCENT", ""}, "tss": {"TSS", "TSSPERDAY", "NONE"},
    "distance": {"KM", "MI", "CUSTOMkm total distance", "CUSTOM公里"},
    "climbing": {"METERS", "FT"}, "descending": {"METERS", "FT"},
    "elevation": {"METERS", "FT"}, "_elevation": {"METERS", "FT"},
    "duration": {"HHMMSS", "HMSLONG", "HMSSHORT", "HHMM", "SECONDS"},
    "movingduration": {"HHMMSS", "HMSLONG", "HMSSHORT", "HHMM", "SECONDS"},
    "power": {"WATTS"}, "runpower": {"WATTS"}, "np": {"WATTS"}, "ecpower": {"WATTS"},
    "heartrate": {"BPM"}, "work": {"KJ"}, "vam": {"METERSPERHOUR"},
    "description": {"NONE", ""}, "title": {"NONE", ""},
}

_IDENT = re.compile(r"[A-Za-z_@][A-Za-z_0-9]*")


def _strip_strings(e: str) -> str:
    return re.sub(r'"[^"]*"', '""', e)


def value_ident(expr: str):
    """Bare identifier the expression returns, e.g. `if(cond, distance)` ->
    distance, `rev(sum(if(sport="run",climbing),"week"))` -> climbing; None
    when the value is computed (arithmetic, function of several things)."""
    e = _strip_strings(expr or "").strip()
    wrappers = ("if", "rev", "sum", "avg", "max", "min", "round", "nozero", "trunc", "slr")
    for _ in range(8):
        m = re.fullmatch(r"(\w+)\((.*)\)", e, re.S)
        if not m or m.group(1).lower() not in wrappers:
            break
        # split top-level args
        depth, args, cur = 0, [], ""
        for ch in m.group(2):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                args.append(cur)
                cur = ""
            else:
                cur += ch
        args.append(cur)
        args = [a.strip() for a in args if a.strip() and a.strip() != '""']
        if not args:
            return None
        # if(cond, value[, else]) -> value; aggregates -> first arg
        e = args[1] if m.group(1).lower() == "if" and len(args) > 1 else args[0]
    return e.lower() if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", e) else None


def _decimals(v: float) -> int:
    s = f"{v:.10f}".rstrip("0")
    return len(s.split(".")[1]) if "." in s else 0


def series_stats(ys: list[float]) -> dict:
    if not ys:
        return {"n": 0}
    return {"n": len(ys), "min": min(ys), "median": statistics.median(ys), "max": max(ys),
            "integers": all(abs(v - round(v)) < 1e-9 for v in ys),
            "max_decimals": max(_decimals(v) for v in ys[:2000])}


def fmt(v, uid="NONE") -> str:
    if v is None:
        return "—"
    return f"{v:.4g}"


def check_series(where: dict, s: dict, orig: dict, st: dict, sport) -> list[dict]:
    out = []
    y0 = orig.get("y_axis") or "NONE"
    y = s.get("y_axis") or "NONE"
    expr = orig.get("expression") or ""
    u = U.unit(y, sport)

    def add(kind, problem, evidence):
        out.append({**where, "series": s.get("name") or s.get("id") or "(unnamed)",
                    "y_axis": y0, "check": kind, "problem": problem, "evidence": evidence})

    data = s.get("data") or {}
    if data.get("kind") == "error":
        msg = data.get("message", "")
        if "unsupported function" in msg:
            add("error", "unsupported function — pending", msg)
        else:
            add("error", "series fails", msg[:160])
        return out
    if U.is_imperial(y0) or U.uses_english(expr):
        add("imperial", f"imperial unit ({y0}{', english()' if U.uses_english(expr) else ''})",
            f"shown as {y} ({U.unit(y).label}) outside parity")
    vi = value_ident(expr)
    if vi and vi in IDENT_UNITS and y0 not in IDENT_UNITS[vi] and y not in IDENT_UNITS[vi]:
        add("axis-mismatch", f"`{vi}` plotted on a {y or 'NONE'} axis",
            f"median {fmt(st.get('median'))}" if st.get("n") else "static check (no data in range)")
    if st.get("n", 0) == 0:
        return out
    med = st["median"]
    nonzero = [v for v in [st["min"], st["median"], st["max"]] if v]
    if u.kind == "percent":
        if st["median"] > 2 or st["median"] < -1:
            add("percent-range", "PERCENT series isn't a fraction (already ×100?)",
                f"min {fmt(st['min'])} / median {fmt(med)} / max {fmt(st['max'])}")
    elif u.kind == "duration":
        if 0 < abs(med) < 10 and st["max"] < 100 and st["n"] > 1:
            add("duration-scale", "duration values look like minutes/hours, not seconds",
                f"median {fmt(med)} max {fmt(st['max'])}")
    rng = PLAUSIBLE.get(u.id if not u.id.startswith("CUSTOM") else "", None)
    spread = re.search(r"\bstddev\s*\(", expr, re.I) is not None   # a spread, not the quantity itself
    if rng and nonzero and not spread and abs(med) > 0 and not (rng[0] <= abs(med) <= rng[1]):
        add("magnitude", f"values implausible for {u.label or y} ({MAGNITUDE_HINT.get(y, 'check unit')})",
            f"median {fmt(med)} (expected {rng[0]}–{rng[1]})")
    if y == "RPM" and sport in ("run", None) and "cadence" in expr.lower() and "*2" not in expr.replace(" ", "") \
            and not orig.get("scale") and 40 < med < 120:
        add("cadence", "single-leg running cadence on a steps/min axis (×2 for spm)", f"median {fmt(med)}")
    um = s.get("unit") or {}
    b = um.get("converted_from") or (um.get("base") if um.get("base") not in (None, "min") else None)
    if b:
        add("pace-base", f"pace series holds {'s/km' if b == 's' else 'km/h'}; normalised to min/km",
            f"expr `{expr[:60]}`")
    return out


def check_axes(where: dict, res: dict) -> list[dict]:
    out = []
    for a in res.get("axes") or []:
        if not a.get("used"):
            continue
        lo, hi = a.get("min"), a.get("max")
        if lo is None and hi is None:
            continue
        pts = [y for s in res["series"] if (s.get("y_axis") or "NONE") == a["id"]
               for y in data_ys(s.get("data") or {}) if (s.get("data") or {}).get("kind") == "points"]
        if not pts:
            continue
        clipped = sum(1 for v in pts if (lo is not None and v < lo) or (hi is not None and v > hi))
        if clipped / len(pts) > 0.05:
            rng_txt = f"≥ {fmt(lo)}" if hi is None else f"≤ {fmt(hi)}" if lo is None else f"{fmt(lo)}–{fmt(hi)}"
            out.append({**where, "series": "(axis)", "y_axis": a["id"], "check": "clip",
                        "problem": f"fixed axis range {rng_txt} clips {clipped / len(pts):.0%} of points",
                        "evidence": f"{clipped}/{len(pts)} points, data {fmt(min(pts))}–{fmt(max(pts))}"})
    return out


def pick_workouts(ds) -> list:
    """A recent road run with power, a trail run, a hike."""
    ws = list(reversed(ds.workouts))
    road = next((w for w in ws if w.sport == "run" and "runningtrail" not in w.tags
                 and w.metrics.get("np")), None)
    trail = next((w for w in ws if w.sport == "run" and "runningtrail" in w.tags), None)
    hike = next((w for w in ws if (w.sport_type or "").lower() in ("hiking", "mountaineering")
                 or "hiking" in w.tags or "mountaineering" in w.tags), None)
    return [w for w in (road, trail, hike) if w is not None]


def audit(views: dict, ds, begin, end, workouts, limit=None, only_view=None, log=print):
    rows, findings = [], []
    n_charts = 0
    for vname, v in views.items():
        if only_view and vname != only_view:
            continue
        if v.get("error"):
            findings.append({"view": vname, "dashboard": "", "chart": "", "series": "", "y_axis": "",
                             "check": "error", "problem": "view file fails", "evidence": v["error"]})
            continue
        for di, d in enumerate(v["dashboards"]):
            for ci, c in enumerate(d["charts"]):
                kind = c.get("kind")
                if kind not in ("athlete", "workout"):
                    continue
                if limit is not None and n_charts >= limit:
                    return rows, findings, n_charts
                n_charts += 1
                targets = [None] if kind == "athlete" else workouts
                for w in targets:
                    t0 = time.perf_counter()
                    sport = w.sport if w is not None else None
                    where = {"view": vname, "dashboard": d["title"], "chart": c.get("title"),
                             "workout": None if w is None else f"{w.entry.start:%Y-%m-%d} {w.sport_type or w.sport}"}
                    try:
                        res = render_chart(c, ds, begin, end, workout=w)
                    except Exception as e:     # a chart that can't even start
                        findings.append({**where, "series": "(chart)", "y_axis": "", "check": "error",
                                         "problem": "chart fails", "evidence": f"{type(e).__name__}: {e}"})
                        continue
                    for s, orig in zip(res["series"], c.get("series", [])):
                        st = series_stats(data_ys(s.get("data") or {}))
                        rows.append({**where, "series": s.get("name"), "y_axis_orig": orig.get("y_axis"),
                                     "y_axis": s.get("y_axis"), "type": s.get("type"),
                                     "data_kind": (s.get("data") or {}).get("kind"),
                                     "x": (s.get("data") or {}).get("x"), **st})
                        findings += check_series(where, s, orig, st, sport)
                    findings += check_axes(where, res)
                    log(f"  {time.perf_counter() - t0:6.1f}s  {vname} / {d['title']} / {c.get('title')}"
                        + (f"  [{where['workout']}]" if w is not None else ""))
    return rows, findings, n_charts


def dedupe(findings: list[dict]) -> list[dict]:
    """One finding per (view, chart, series, check) — workout charts are
    rendered on several workouts; keep the evidence of the first, list which."""
    out: dict = {}
    for f in findings:
        k = (f["view"], f.get("dashboard"), f["chart"], f["series"], f["check"], f["problem"])
        if k in out:
            if f.get("workout") and f["workout"] not in out[k]["workouts"]:
                out[k]["workouts"].append(f["workout"])
            continue
        out[k] = {**f, "workouts": [f["workout"]] if f.get("workout") else []}
    return list(out.values())


def fix_status(f: dict, fixes: list[dict], fixed_left: set) -> str:
    k = (f["view"], f.get("dashboard"), f["chart"], f["series"], f["check"], f["problem"])
    why = left_reason(f)
    if why and not why.startswith("fixed"):
        return "left as is — " + why
    for x in fixes:
        if x["view"] != f["view"] or x["chart"] != f["chart"]:
            continue
        if x.get("dashboard") and x["dashboard"] != f.get("dashboard"):
            continue
        if "axis" in x and f["series"] == "(axis)" and x["axis"] == f["y_axis"]:
            return "fixed: " + x.get("note", "")
        if x.get("series") == f["series"]:
            return "fixed: " + x.get("note", "")
    touched = any(x["view"] == f["view"] and x["chart"] == f["chart"] for x in fixes)
    if touched and k not in fixed_left and f["check"] not in ("imperial", "pace-base", "error"):
        notes = sorted({x.get("note", "") for x in fixes if x["view"] == f["view"] and x["chart"] == f["chart"]})
        return "fixed: " + "；".join(notes[:2])
    if f["check"] == "imperial":
        return "fixed automatically: english() → metric(), imperial id → metric id (non-parity)"
    if f["check"] == "pace-base":
        return "fixed automatically: pace normalised to min/km, shown m:ss /km"
    if f["check"] == "error":
        return "left as is — " + ("evaluator function pending (another agent)" if "pending" in f["problem"]
                                  else "evaluator issue (evaluator.py is out of scope here)")
    return "left as is — " + (why or ("still flagged after fixes" if k in fixed_left else "not a unit problem"))


def write_report(path: Path, summary: dict, findings: list[dict], fixes: list[dict],
                 left_keys: set, notes: dict) -> None:
    by_check = Counter(f["check"] for f in findings)
    lines = [
        "# 圖表單位與座標軸稽核（chart units audit）",
        "",
        f"產生時間：{dt.datetime.now():%Y-%m-%d %H:%M}，由 `backend/scripts/audit_chart_units.py` 產生（可重跑）。",
        "",
        "## 範圍",
        "",
        f"- 引擎設定：parity = {summary['parity']}（使用者目前的模式）",
        f"- 賽季圖表：{summary['begin']} – {summary['end']}（最近 365 天），全部運動",
        f"- 單次活動圖表：{', '.join(summary['workouts'])}",
        f"- 檢查 {summary['charts']} 張圖、{summary['series']} 條 series（workout 圖在每筆活動各算一次）"
        f"，{summary['rendered']} 次 series 渲染，耗時 {summary['seconds']:.0f} 秒",
        "",
        "## 摘要",
        "",
        "| 檢查 | 說明 | 發現數（未修正） | 套用 wko5_fixes 後 |",
        "|---|---|---:|---:|",
    ]
    desc = {
        "imperial": "英制單位 id 或 english()", "percent-range": "PERCENT 不是分數",
        "duration-scale": "時間軸數值像分鐘／小時", "magnitude": "數值量級與單位不符",
        "axis-mismatch": "series 的量不屬於這個軸", "clip": "固定軸範圍裁掉 >5% 資料點",
        "cadence": "單腳步頻放在 spm 軸", "pace-base": "配速存成 s/km 或 km/h",
        "error": "series 執行失敗",
    }
    after = summary["after_by_check"]
    for k in desc:
        lines.append(f"| {k} | {desc[k]} | {by_check.get(k, 0)} | {after.get(k, 0)} |")
    lines += [
        "",
        "「套用後」一欄：imperial / pace-base 由 render_chart 在非 parity 模式自動轉換，"
        "仍會被偵測到（偵測看的是原始定義），所以數字不會歸零；其他欄是 `views/wko5_fixes.json` 修掉後剩下的。",
        "",
        "## 單位政策",
        "",
        *notes["policy"],
        "",
        "## 發現（每列一項）",
        "",
        "| View / 圖表 / series | 問題 | 證據 | 處理 |",
        "|---|---|---|---|",
    ]
    order = list(desc)
    for f in sorted(findings, key=lambda f: (order.index(f["check"]) if f["check"] in order else 99,
                                             f["view"], f["chart"] or "", f["series"] or "")):
        where = f"{f['view']} / {f['chart']} / {f['series']}"
        if f.get("workouts"):
            where += f"（{len(f['workouts'])} 筆活動）"
        ev = str(f["evidence"]).replace("|", "\\|").replace("\n", " ")
        pr = f"`{f['check']}` {f['problem']}".replace("|", "\\|")
        lines.append(f"| {where.replace('|', '/')} | {pr} | {ev} | {fix_status(f, fixes, left_keys).replace('|', '/')} |")
    lines += ["", f"## 已套用的 WKO5 圖表修正（`views/wko5_fixes.json`，{len(fixes)} 項，只在非 parity 模式）", "",
              "| View / 圖表 | 目標 | 修正 |", "|---|---|---|"]
    for x in fixes:
        target = (f"軸 {x['axis']}" if "axis" in x else f"series「{x.get('series', x.get('series_index'))}」")
        what = "移除" if x.get("drop") else ", ".join(
            [f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in (x.get("set") or {}).items()]
            + ([f"×{x['scale']}"] if x.get("scale") is not None else []))
        lines.append(f"| {x['view']} / {x['chart']} | {target} | {what} — {x.get('note', '')} |".replace("\n", " "))
    if notes.get("unmatched"):
        lines += ["", "## wko5_fixes.json 中沒有命中的項目", ""]
        lines += [f"- {x['view']} / {x['chart']} / {x.get('series') or x.get('axis')}" for x in notes["unmatched"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", "utf-8")


POLICY = [
    "- 單位定義集中在 `backend/engine/wko5expr/units.py`：每個 WKO5 id 有顯示標籤、種類（number / duration / pace / percent / date）、"
    "依量級決定的小數位數，以及顯示倍率（PERCENT 存分數 ×100；CM、MILLISECONDS 不需倍率——式子裡 stancetime 已是 ms、verticaloscillation 已是 cm，與 WKO5 相同）。",
    "- 小數：W 0、W/kg 2、kJ 0、TSS 0、TSS/天 ≥10 取 0 否則 1、m 0、km ≥100 取 0 / ≥10 取 1 / 其餘 2、km/h 1、m/s 2、m/h 0、bpm 0、spm 0、% ≥10% 取 0 否則 1、"
    "L/min 2、mL/min/kg 1；NONE 與 CUSTOM<label>：|v| ≥ 100 → 0、≥ 10 → 1、其餘 2。",
    "- 時間（HHMMSS/HMSLONG/HMSSHORT/SECONDS，單位秒）顯示 h:mm:ss 或 m:ss；配速一律 m:ss /km。",
    "- 非 parity 模式：`english(x)` 改成 `metric(x)` 計算（evaluator 內部全是公制），FT/MI/MPH/PACEMI/FAHRENHEIT 改成 m/km/km/h/min/km/°C；"
    "配速 series 若是 s/km 或 km/h（ngp）會換成 min/km。parity 模式保留 WKO5 原本的 id 與數值，只套用合理的小數。",
    "- WKO5 圖表本身的設計錯誤（單位 id 選錯、軸範圍裁掉資料、單腳步頻）寫在 `views/wko5_fixes.json`，只在非 parity 模式套用，"
    "套用過的圖表在檢視器顯示「已修正單位」標籤。",
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--view")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--json")
    ap.add_argument("--report", default=str(REPORT))
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    log = (lambda *x: None) if a.quiet else (lambda *x: print(*x, flush=True))

    t0 = time.perf_counter()
    ds = API._dataset(False)
    end = ds.today
    begin = end - 365
    workouts = pick_workouts(ds)
    raw = {**API._wko5_views_raw(), **load_custom_views()}
    fixes = load_fixes()
    log(f"parity={ds.config.parity} workouts={[w.idx for w in workouts]} fixes={len(fixes)}")

    rows, findings, n_charts = audit(raw, ds, begin, end, workouts, a.limit, a.view, log)
    findings = dedupe(findings)
    fixed_views = apply_fixes(raw, fixes)
    touched = {(f["view"], f["chart"]) for f in fixes}
    # re-audit only the charts the fixes touch, merge with untouched findings
    sub = {}
    for vname, v in fixed_views.items():
        if v.get("error"):
            continue
        dsh = []
        for d in v["dashboards"]:
            dsh.append({**d, "charts": [c for c in d["charts"] if (vname, c.get("title")) in touched]})
        if any(d["charts"] for d in dsh):
            sub[vname] = {**v, "dashboards": dsh}
    log("re-auditing charts with fixes …")
    _, f_after, _ = audit(sub, ds, begin, end, workouts, None, a.view, log)
    f_after = dedupe(f_after)
    after = [f for f in findings if (f["view"], f["chart"]) not in touched] + f_after
    left_keys = {(f["view"], f.get("dashboard"), f["chart"], f["series"], f["check"], f["problem"]) for f in f_after}

    summary = {
        "parity": ds.config.parity, "begin": str(U.format_value(begin, U.unit("DATE"))),
        "end": str(U.format_value(end, U.unit("DATE"))),
        "workouts": [f"#{w.idx} {w.entry.start:%Y-%m-%d} {w.sport_type or w.sport}" for w in workouts],
        "charts": n_charts, "series": len({(r["view"], r["dashboard"], r["chart"], r["series"]) for r in rows}),
        "rendered": len(rows), "seconds": time.perf_counter() - t0,
        "after_by_check": dict(Counter(f["check"] for f in after)),
    }
    write_report(Path(a.report), summary, findings, fixes, left_keys,
                 {"policy": POLICY, "unmatched": unmatched(raw, fixes)})
    if a.json:
        Path(a.json).write_text(json.dumps({"summary": summary, "rows": rows, "findings": findings,
                                            "after": after}, ensure_ascii=False, indent=1, default=str), "utf-8")
    print(json.dumps({k: v for k, v in summary.items()}, ensure_ascii=False, default=str))
    print("findings:", dict(Counter(f["check"] for f in findings)))
    print("after fixes:", summary["after_by_check"])


if __name__ == "__main__":
    main()
