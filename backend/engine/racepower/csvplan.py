"""
Race plan → CSV (the 「匯出 CSV」 button). Formatting only: every number comes
from the /plan payload (planner.plan_run / plan_hike), nothing is recomputed.

Layout: a header block of `key,value[,source]` rows (course totals, mode,
CP / TTE / k and their sources, strategy, heat mode, date computed), a blank
row, then one row per segment and a total row. The API writes it as UTF-8
with a BOM so Excel opens the Chinese correctly.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
from typing import Optional

TYPE_LABEL = {"road": "路跑", "trail": "越野", "baiyue": "百岳"}
MODE_LABEL = {"time": "目標時間", "power": "目標功率", "auto": "通通幫我算"}
STRATEGY_LABEL = {"even": "均速", "negative": "前慢後快", "positive": "前快後慢"}
ACCL_LABEL = {"acclimatised": "已適應", "partial": "部分適應（推估）", "unacclimatised": "未適應"}
USED_ROWS = (("cp", "CP", "W"), ("cp2", "CP（20 分鐘內）", "W"), ("w_prime", "W′", "J"), ("tte", "TTE", "s"),
             ("k", "Riegel k", ""), ("re", "RE", ""), ("weight", "體重", "kg"), ("eph", "EP/h", ""),
             ("pack_kg", "背負", "kg"), ("aet", "心率上限 AeT", "bpm"))
COLUMNS = ("段", "天", "起點 km", "終點 km", "距離 m", "爬升 m", "下降 m", "坡度 %", "類別",
           "目標功率 W", "% CP", "區間", "配速 /km", "速度 km/h", "分段時間", "累計時間", "ETA（含補給）",
           "M", "溫度 °C", "露點 °C", "熱修正 %", "熱修正時刻", "備註", "標記")


def _r(v, nd: int = 1):
    if v is None:
        return ""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return v
    return round(x, nd) + 0.0 if nd else int(round(x))      # + 0.0: no "-0.0"


def hms(s) -> str:
    if s is None:
        return ""
    s = int(round(float(s)))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def pace(s) -> str:
    if not s:
        return ""
    s = int(round(float(s)))
    return f"{s // 60}:{s % 60:02d}"


def safe_name(name: str) -> str:
    """File-system safe (Windows too); keeps Chinese."""
    n = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (name or "").strip())
    n = re.sub(r"\s+", "_", n).strip("._")
    return n[:80] or "race"


def filename(plan: dict, name: Optional[str], date: Optional[str], today: Optional[dt.date] = None) -> str:
    km = (plan.get("summary") or {}).get("km") or 0.0
    base = name or plan.get("course_name") or f"{TYPE_LABEL.get(plan.get('type'), '')}{km:.1f}km"
    day = (date or "")[:10] or (today or dt.date.today()).isoformat()
    return f"賽事功率_{safe_name(base)}_{day}.csv"


def _speed_kmh(s: dict):
    if s.get("speed_kmh") is not None:
        return s["speed_kmh"]
    return s["speed_ms"] * 3.6 if s.get("speed_ms") else None


def header_rows(plan: dict, *, name: str, date: Optional[str], computed_at: dt.datetime,
                start_time: Optional[str] = None, stops=None, acclimatisation: Optional[str] = None) -> list[list]:
    s = plan["summary"]
    ct = plan.get("course_totals") or {}
    used = plan.get("used") or {}
    rows: list[list] = [["賽事功率 分段計畫"], ["路線", name], ["比賽日期", date or ""],
                        ["類型", TYPE_LABEL.get(plan.get("type"), plan.get("type"))],
                        ["距離 km", _r(s.get("km"), 2)], ["爬升 m", _r(s.get("gain_m"), 0)],
                        ["下降 m", _r(s.get("loss_m"), 0)], ["分段數", len(plan.get("segments") or [])]]
    if ct.get("z_max") is not None:
        rows.append(["最高海拔 m", _r(ct.get("z_max"), 0)])
    mode = MODE_LABEL.get(s.get("mode"), s.get("mode"))
    if s.get("mode") == "auto" and s.get("effort_target"):
        mode += f"（努力目標 {s['effort_target']:.0%}）"
    rows.append(["模式", mode])
    rows.append(["整場方法", "v2 分段加總" if s.get("total_method") == "v2" else "v1（Riegel + RE），分段只負責分配",
                 s.get("badge") or ""])
    if plan.get("type") == "baiyue":
        rows += [["總移動時間", hms(s.get("time_s"))], ["時鐘時間", hms(s.get("clock_s"))],
                 ["速度倍率", _r(s.get("speed_factor"), 3)]]
    else:
        rows += [["預估完賽", hms(s.get("time_s")), s.get("finish_eta") or ""],
                 ["平均功率 W", _r(s.get("power"), 0), f"{s['pct_cp']:.0%} CP" if s.get("pct_cp") else ""],
                 ["平均配速 /km", pace(s.get("pace_s_per_km"))]]
    eff = plan.get("effort") or {}
    if eff.get("label"):
        val = eff.get("f", eff.get("r"))
        rows.append(["努力度", eff["label"], f"{val:.0%}" if val is not None else "", eff.get("badge") or ""])
    rows.append(["環境係數 M（平均）", _r(s.get("M"), 4)])
    for key, label, unit in USED_ROWS:
        u = used.get(key) or {}
        if u.get("value") is None:
            continue
        nd = 3 if key in ("k", "re") else 2 if key == "eph" else 1 if key in ("weight", "pack_kg") else 0
        rows.append([f"{label}{' ' + unit if unit else ''}", _r(u["value"], nd), u.get("source") or ""])
    if plan.get("type") != "baiyue":
        kind = s.get("strategy") or "even"
        st = STRATEGY_LABEL.get(kind, kind)
        if kind != "even" and s.get("strategy_amount") is not None:
            st += f" {s['strategy_amount'] * 100:.1f}%"
        rows.append(["策略", st])
        if s.get("alpha") is not None:
            rows.append(["坡道彈性", f"上坡 +{s['alpha'] * 100:.0f}%（實際 +{(s.get('alpha_used') or 0) * 100:.1f}%）"
                                   f"，下坡 −{(s.get('beta') or 0) * 100:.0f}%"])
    if acclimatisation:
        rows.append(["海拔適應", ACCL_LABEL.get(acclimatisation, acclimatisation)])
    h = s.get("heat") or {}
    if h.get("mode") == "hourly":
        rows.append(["熱修正", f"逐段：依各段 ETA 取逐時預報（{h.get('passes')} 次迭代）", "推估"])
    else:
        env_to = (plan.get("env") or {}).get("to") or {}
        t = env_to.get("temp_c")
        rows.append(["熱修正", f"單一溫度 {t:.1f} °C" if t is not None else "單一溫度", h.get("reason") or ""])
    rows.append(["起跑時間", start_time or ""])
    if stops:
        rows.append(["補給站", "；".join(f"{float(x['km']):g} km {float(x.get('minutes') or 0):g} 分" for x in stops)])
    rows.append(["計算時間", computed_at.strftime("%Y-%m-%d %H:%M")])
    return rows


def segment_row(s: dict, hike: bool) -> list:
    dist = s.get("dist_m")
    return [s.get("i"), s.get("day") or (1 if hike else ""), _r(s.get("start_km"), 2), _r(s.get("end_km"), 2),
            _r(dist, 0), _r(s.get("gain_m"), 0), _r(s.get("loss_m"), 0),
            _r(s["grade"] * 100 if s.get("grade") is not None else None, 1), s.get("cls_label") or "",
            _r(s.get("power"), 0), _r(s["pct_cp"] * 100 if s.get("pct_cp") is not None else None, 0),
            s.get("zone") or "", pace(s.get("pace_s_per_km")), _r(_speed_kmh(s), 2), hms(s.get("t")),
            hms(s.get("cum_s")), s.get("eta") or "", _r(s.get("M", s.get("A")), 4),
            _r(s.get("temp_c"), 1), _r(s.get("dew_c"), 1), _r(s.get("heat_pct"), 2), s.get("heat_clock") or "",
            "、".join(s.get("notes") or ([s["walk"]] if s.get("walk") else [])), s.get("badge") or ""]


def plan_csv(plan: dict, *, name: str, date: Optional[str] = None, computed_at: Optional[dt.datetime] = None,
             start_time: Optional[str] = None, stops=None, acclimatisation: Optional[str] = None) -> str:
    """The CSV text (no BOM; the endpoint adds it with utf-8-sig)."""
    computed_at = computed_at or dt.datetime.now()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    for r in header_rows(plan, name=name, date=date, computed_at=computed_at, start_time=start_time,
                         stops=stops, acclimatisation=acclimatisation):
        w.writerow(r)
    w.writerow([])
    w.writerow(COLUMNS)
    hike = plan.get("type") == "baiyue"
    segs = plan.get("segments") or []
    for s in segs:
        w.writerow(segment_row(s, hike))
    s = plan["summary"]
    if segs:
        tot = ["合計", "", _r(segs[0].get("start_km"), 2), _r(segs[-1].get("end_km"), 2),
               _r(sum(x.get("dist_m") or 0 for x in segs), 0), _r(sum(x.get("gain_m") or 0 for x in segs), 0),
               _r(sum(x.get("loss_m") or 0 for x in segs), 0), "", "", _r(s.get("power"), 0),
               _r(s["pct_cp"] * 100 if s.get("pct_cp") is not None else None, 0), "", pace(s.get("pace_s_per_km")),
               _r(s["km"] / (s["time_s"] / 3600.0), 2) if s.get("time_s") else "", hms(s.get("time_s")),
               hms(s.get("time_s")), s.get("finish_eta") or "", _r(s.get("M"), 4), "", "", "", "", "", ""]
        w.writerow(tot)
    return buf.getvalue()
