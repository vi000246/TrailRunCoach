"""
Cold on race day: wind chill and the 冷風 reminder (SP-251).

docs/research/cold-environment.md §2.3 / §4.2 單 1. Display only — the
predicted time never changes (no study gives a cold slowdown to apply).

  * Each segment's clock span comes from the plan (start time + the time
    before it + the aid stops up to it; 百岳: each day from the start time,
    moving time ÷ the moving ratio, as the planner's ETA). Without a start
    time or without segments (a manual 百岳 course) the race window's hours
    are used instead (weather.race_window: 06–18 of each event day), with no km.
  * The wind (and the forecast's own temperature) at the segment's midpoint
    comes from the /weather `wind` rows (Open-Meteo forecast, CWA mountain
    3-day product), linear between rows. The temperature is moved from the
    weather point (heat_ref_alt_m) to the segment's mean height by
    −0.65 °C / 100 m, as the heat model does; a row without a temperature
    uses the segment's own plan temperature.
  * Wind chill: US National Weather Service, °F / mph,
    35.74 + 0.6215T − 35.75V^0.16 + 0.4275T·V^0.16, only for T ≤ 50 °F
    (10 °C) and V > 3 mph (4.8 km/h) (weather.gov/safety/cold-wind-chill-chart).
  * Reminder: any point with wind chill ≤ −10 °C (Environment and Climate
    Change Canada's 「risk of hypothermia and frostbite if outdoors for long
    periods」 band, owner 2026-10-06) or gusts ≥ 50 km/h (ECCC: wind over
    50 km/h makes frostbite faster; the gust reading of it is 推估). Wind
    chill ≤ −28 °C says 「exposed skin can freeze in 10–30 minutes」 (ECCC).
  * No wind data → None: nothing shown, no error.

SP-305 (docs/research/ui-hint-audit.md): the page shows this as ONE line in
the result's attention box, merged with the other outdoor reminders of the
same race (attention()), the details in a collapsed section.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from backend.i18n import _

WC_MAX_T_C = 10.0                 # NWS: wind chill is defined only at ≤ 50 °F …
WC_MIN_WIND_KMH = 4.8             # … and wind > 3 mph
WC_ALERT_C = -10.0                # ECCC −10 to −27: risk if outdoors for long periods (owner 2026-10-06)
WC_FROSTBITE_C = -28.0            # ECCC −28 to −39: exposed skin can freeze in 10–30 minutes
GUST_ALERT_KMH = 50.0             # ECCC: > 50 km/h speeds frostbite up; applied to gusts (推估, ticket SP-251)
EDGE_H = 1.5                      # a clock this far past the first / last wind row still takes that row
LAPSE_C_PER_M = -0.0065


def wind_chill_c(temp_c: Optional[float], wind_kmh: Optional[float]) -> Optional[float]:
    """NWS wind chill in °C; None outside its range (T > 10 °C or wind ≤ 4.8 km/h) or without data."""
    if temp_c is None or wind_kmh is None or temp_c > WC_MAX_T_C or wind_kmh <= WC_MIN_WIND_KMH:
        return None
    tf = temp_c * 9.0 / 5.0 + 32.0
    v = (wind_kmh / 1.609344) ** 0.16
    wf = 35.74 + 0.6215 * tf - 35.75 * v + 0.4275 * tf * v
    return (wf - 32.0) * 5.0 / 9.0


# ---------------------------------------------------------------------------
# the clock span of each segment
# ---------------------------------------------------------------------------

def _start(date: Optional[str], start_time: Optional[str]) -> Optional[dt.datetime]:
    if not date or not start_time:
        return None
    try:
        d = dt.date.fromisoformat(str(date)[:10])
        h, m = (int(x) for x in str(start_time).split(":")[:2])
        return dt.datetime(d.year, d.month, d.day, h, m)
    except ValueError:
        return None


def _stops_before(stops, km: float) -> float:
    return sum(float(s.get("minutes") or 0) * 60.0 for s in stops or [] if float(s.get("km") or 0) <= km + 1e-6)


def points(plan: dict, date: Optional[str], start_time: Optional[str], stops=None,
           days: int = 1) -> list[dict]:
    """[{i, start_km, end_km, z, temp_c, begin, end}] — the plan's segments with their clock
    spans; without a start time or segments, one point per hour of the race window
    (weather.race_window, km and height None)."""
    segs = plan.get("segments") or []
    t0 = _start(date, start_time)
    if t0 is not None and segs:
        out = []
        if plan.get("type") == "baiyue":
            ratio = float((plan.get("summary") or {}).get("moving_ratio") or 1.0) or 1.0
            day, day_start, prev = None, 0.0, 0.0
            for s in segs:
                n = int(s.get("day") or 1)
                if n != day:
                    day, day_start = n, prev
                base = t0 + dt.timedelta(days=n - 1)
                b = base + dt.timedelta(seconds=(s["cum_s"] - s["t"] - day_start) / ratio
                                        + _stops_before(stops, s["start_km"]))
                out.append({"i": s.get("i"), "start_km": s["start_km"], "end_km": s["end_km"], "z": s.get("z_mean"),
                            "temp_c": s.get("temp_c"), "begin": b, "end": b + dt.timedelta(seconds=s["t"] / ratio)})
                prev = s["cum_s"]
        else:
            for s in segs:
                b = t0 + dt.timedelta(seconds=s["cum_s"] - s["t"] + _stops_before(stops, s["start_km"]))
                out.append({"i": s.get("i"), "start_km": s["start_km"], "end_km": s["end_km"], "z": s.get("z_mean"),
                            "temp_c": s.get("temp_c"), "begin": b, "end": b + dt.timedelta(seconds=s["t"])})
        return out
    from backend.engine.racepower import weather as WX
    sm = plan.get("summary") or {}
    dur = sm.get("time_total_s") or sm.get("clock_s") or ((sm.get("time_s") or 0) + (sm.get("stops_s") or 0)) or None
    wins = WX.race_window(date, start_time, days, dur) or []
    out = []
    for a, b in wins:
        t = a
        while t < b:
            e = min(b, t + dt.timedelta(hours=1))
            out.append({"i": None, "start_km": None, "end_km": None, "z": None, "temp_c": None, "begin": t, "end": e})
            t = e
    return out


def mid(p: dict) -> dt.datetime:
    return p["begin"] + (p["end"] - p["begin"]) / 2


def clock(t: dt.datetime, date: Optional[str]) -> str:
    """'HH:MM', with ' (+n)' on a later day than the race date (as planner._clock)."""
    try:
        d0 = dt.date.fromisoformat(str(date)[:10])
        n = (t.date() - d0).days
    except (TypeError, ValueError):
        n = 0
    return t.strftime("%H:%M") + (f" (+{n})" if n else "")


# ---------------------------------------------------------------------------
# wind at a clock
# ---------------------------------------------------------------------------

KEYS = ("temp_c", "wind_kmh", "gust_kmh")


def wind_at(rows: list[dict], when: dt.datetime) -> Optional[dict]:
    """Wind (and the row temperature) at a local clock time: linear between the two
    bracketing rows (a missing value on one side takes the other); up to EDGE_H past
    either end the edge row; beyond → None."""
    pts = []
    for r in rows or []:
        try:
            pts.append((dt.datetime.fromisoformat(str(r["t"])[:16]), r))
        except (KeyError, TypeError, ValueError):
            continue
    pts = [(t, r) for t, r in sorted(pts, key=lambda x: x[0]) if r.get("wind_kmh") is not None]
    if not pts:
        return None
    edge = dt.timedelta(hours=EDGE_H)
    if when <= pts[0][0]:
        return {k: pts[0][1].get(k) for k in KEYS} if pts[0][0] - when <= edge else None
    if when >= pts[-1][0]:
        return {k: pts[-1][1].get(k) for k in KEYS} if when - pts[-1][0] <= edge else None
    for (t0, a), (t1, b) in zip(pts, pts[1:]):
        if t0 <= when <= t1:
            w = (when - t0).total_seconds() / max(1.0, (t1 - t0).total_seconds())
            out = {}
            for k in KEYS:
                x, y = a.get(k), b.get(k)
                out[k] = x + w * (y - x) if x is not None and y is not None else (x if y is None else y)
            return out
    return None


# ---------------------------------------------------------------------------
# the 冷風 reminder
# ---------------------------------------------------------------------------

def _ranges(hits: list[dict]) -> list[dict]:
    """Consecutive hit points merged into one range (km, clock, the worst values)."""
    out = []
    for h in hits:
        if out and out[-1]["_n"] + 1 == h["_n"]:
            r = out[-1]
            r.update(end_km=h["end_km"], to=h["end"], _n=h["_n"])
        else:
            r = {"start_km": h["start_km"], "end_km": h["end_km"], "from": h["begin"], "to": h["end"],
                 "min_wc_c": None, "max_gust_kmh": None, "min_temp_c": None, "_n": h["_n"]}
            out.append(r)
        for k, v, f in (("min_wc_c", h["wc_c"], min), ("max_gust_kmh", h["gust_kmh"], max),
                        ("min_temp_c", h["temp_c"], min)):
            if v is not None:
                r[k] = v if r[k] is None else f(r[k], v)
    for r in out:
        r.pop("_n")
    return out


def cold_wind(pts: list[dict], rows: Optional[list[dict]], z_ref: Optional[float] = None,
              date: Optional[str] = None) -> Optional[dict]:
    """The SP-251 reminder over the points (points()): {alert, level (frostbite | cold | gust |
    None), min_wc_c, max_gust_kmh, min_temp_c, n, ranges: [{start_km, end_km, from, to,
    min_wc_c, max_gust_kmh, min_temp_c}], per_km}. None when no point has wind data."""
    if not rows or not pts:
        return None
    evald, hits = [], []
    for n, p in enumerate(pts):
        w = wind_at(rows, mid(p))
        if w is None:
            continue
        t = w.get("temp_c")
        if t is not None and z_ref is not None and p.get("z") is not None:
            t = t + LAPSE_C_PER_M * (float(p["z"]) - float(z_ref))
        if t is None:
            t = p.get("temp_c")
        wc = wind_chill_c(t, w.get("wind_kmh"))
        g = w.get("gust_kmh")
        e = {**p, "_n": n, "temp_c": t, "wind_kmh": w.get("wind_kmh"), "gust_kmh": g, "wc_c": wc}
        evald.append(e)
        if (wc is not None and wc <= WC_ALERT_C) or (g is not None and g >= GUST_ALERT_KMH):
            hits.append(e)
    if not evald:
        return None
    wcs = [e["wc_c"] for e in evald if e["wc_c"] is not None]
    gusts = [e["gust_kmh"] for e in evald if e["gust_kmh"] is not None]
    temps = [e["temp_c"] for e in evald if e["temp_c"] is not None]
    min_wc = min(wcs) if wcs else None
    max_g = max(gusts) if gusts else None
    level = None
    if hits:
        hit_wc = [e["wc_c"] for e in hits if e["wc_c"] is not None]
        if hit_wc and min(hit_wc) <= WC_FROSTBITE_C:
            level = "frostbite"
        elif hit_wc and min(hit_wc) <= WC_ALERT_C:
            level = "cold"
        else:
            level = "gust"
    ranges = _ranges(hits)
    for r in ranges:
        r["from"], r["to"] = clock(r["from"], date), clock(r["to"], date)
    return {"alert": bool(hits), "level": level, "min_wc_c": min_wc, "max_gust_kmh": max_g,
            "min_temp_c": min(temps) if temps else None, "n": len(evald), "ranges": ranges,
            "per_km": any(e["start_km"] is not None for e in evald)}


# ---------------------------------------------------------------------------
# one line for the page (SP-305: merged, details collapsed)
# ---------------------------------------------------------------------------

def signed(x: float, nd: int = 0) -> str:
    """−12 with a real minus sign (as the page writes negatives)."""
    return f"{x:.{nd}f}".replace("-", "−")


def _where(r: dict) -> str:
    if r.get("start_km") is not None:
        return _("km {a:.1f}–{b:.1f}（{t0}–{t1}）", a=r["start_km"], b=r["end_km"], t0=r["from"], t1=r["to"])
    return _("{t0}–{t1}", t0=r["from"], t1=r["to"])


def _range_text(r: dict) -> str:
    bits = []
    if r.get("min_wc_c") is not None and r["min_wc_c"] <= WC_ALERT_C:
        bits.append(_("風寒 {wc} °C", wc=signed(r["min_wc_c"])))
    if r.get("max_gust_kmh") is not None and r["max_gust_kmh"] >= GUST_ALERT_KMH:
        bits.append(_("陣風 {g:.0f} km/h", g=r["max_gust_kmh"]))
    return _("{where}{what}", where=_where(r), what=_("、").join(bits))


def attention(cold: Optional[dict] = None) -> Optional[dict]:
    """The outdoor reminders of one plan merged into one line: {kinds, line, gear, details}.
    None when nothing is triggered."""
    kinds, heads, wheres, gear, details = [], [], [], [], []

    def add_gear(*items):
        for g in items:
            if g not in gear:
                gear.append(g)
    if cold and cold.get("alert"):
        kinds.append("cold_wind")
        heads.append(_("冷風"))
        rs = cold["ranges"]
        first = _range_text(rs[0]) + (_("（共 {n} 處）", n=len(rs)) if len(rs) > 1 else "")
        if cold["level"] == "frostbite":
            first += _("：外露皮膚 10–30 分鐘可能凍傷")
        wheres.append(first)
        add_gear(_("防風外套"), _("保暖層"), _("手套帽子"))
        if cold["level"] == "frostbite":
            add_gear(_("遮住臉和手"))
        details += [_("冷風：") + _range_text(r) for r in rs]
        details.append(_("風寒用美國國家氣象局的公式，只在氣溫 ≤ 10 °C、風速 > 4.8 km/h 時算；"
                         "≤ −10 °C 是加拿大環境部「長時間在外有失溫和凍傷風險」那一級，≤ −28 °C 外露皮膚 10–30 分鐘可能凍傷；"
                         "陣風 ≥ 50 km/h 也提醒（推估）。氣溫依各段海拔換算。只是提醒，預估時間不變"))
    if not kinds:
        return None
    line = _("{heads}：{wheres} → 帶{gear}", heads=_("＋").join(heads), wheres=_("；").join(wheres),
             gear=_("、").join(gear))
    return {"kinds": kinds, "line": line, "gear": gear, "details": details}
