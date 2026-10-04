"""
The 插入範本 menu's 「推薦」 block (static/workout_editor.js): per category tab, the three
templates that suit this session best, best first, each with a one-line reason. The
generator's own choices are not touched — this only orders the editor's menu.

Inputs (all of them already decided elsewhere):
  * the interval ladder: interval_library.fit() for the athlete's current rung and the
    day's cap — the old 間歇範本 dropdown's ★ 推薦 — is always #1 on 強度課; other
    equivalent variants of the same rung come next (they don't change progression)
  * Zone 5 open or not (quality_gate z5.open): closed → no Zone 5 template (a ladder Z5
    variant, or a template whose family is VO2max 間歇 / 速度) is recommended
  * the template's family (workout_templates.family_of: 有氧間歇 / VO2max 間歇 / 速度)
  * the training phase on that day (base / build / specific / taper / recovery)
  * the time available: the day's cap (課表偏好) and the session's own minutes
  * terrain (road / trail) and the session's own type (輕鬆 / 長跑 / 強度 / 測試 / 越野)

The weights are 推估 (no published ranking); the phase rules follow the general →
specific progression (Koop; Uphill Athlete) and 台灣教練's Zone 3 before Zone 5. EXPLAIN
is the ? text.
"""
from __future__ import annotations

from typing import Optional

from backend.engine import interval_library as IL
from backend.engine import workout_templates as WT

N_RECS = 3
LONG_MIN = 90                  # 推估: a 長跑 template is ≥ 90 min
SHORT_MIN = 45                 # 推估: a short (taper / recovery) template
ADDON_MIN = 15                 # shorter = an add-on (strides, hill sprints): no time-match bonus
DIST_S_PER_KM = 360.0          # distance steps without the athlete's speed: 6:00/km (推估, as workout_steps)
OVER_CAP = -60.0
Z5_CLOSED = -80.0
DROP_BELOW = -40.0             # a row that ends below this is never recommended

PHASE_OF = {"base": "base", "transition": "base", "build": "build", "specific": "specific",
            "taper": "taper", "event": "taper", "recovery": "recovery"}
PHASE_LABEL = {"base": "基礎期", "build": "強化期", "specific": "專項期", "taper": "減量期", "recovery": "恢復期"}

# trail templates by what they train (phase rules below). 技術地形 (SP-62): the base phase mostly
# low RPE (may replace part of the long run), the 專項期 one a week close to the race's terrain
TRAIL_SPECIFIC = {"lib:dsw_classic", "lib:koop_uphill", "lib:long_climb", "lib:downhill_ecc", "lib:steep_10",
                  "lib:steep_15", "lib:tech_hard"}
TRAIL_BASE = {"lib:long_climb", "lib:ua_hill_sprints", "lib:steep_5", "lib:dsw_endurance", "hill_sprints",
              "lib:tech_easy"}
# 主要訓練項目 = 路跑 (engine/primary_sport.py): marathon-specific sessions for the 專項期 (Pfitzinger's
# LT and MP runs, Daniels' T, Canova's specific block) and the long runs of a marathon plan
ROAD_SPECIFIC = {"lib:pfitz_lt", "lib:daniels_cruise", "lib:canova_specific", "lib:pfitz_mp_long"}
ROAD_LONG = {"lib:pfitz_long", "lib:pfitz_mp_long"}
ROAD_NO_TRAIL = -100.0
# near-duplicates: at most one of each family in the 推薦 block
FAMILY = {"lib:steep_5": "steep", "lib:steep_10": "steep", "lib:steep_15": "steep",
          "lib:ua_hill_sprints": "hills", "hill_sprints": "hills", "cp_quick": "cp", "cp_standard": "cp"}

EXPLAIN = ("推薦依這堂課排序（權重是推估）：① 強度課的第一名一定是間歇階梯的下一步（自動排課會選的那份，依你目前這一階和這天的時間上限）；"
           "同一階的同等課表接在後面，換它們不影響進階。② 5 區還沒開放時不推薦 VO2max 間歇和速度課表（台灣教練：先練 3 區）。"
           "③ 階段：基礎期偏有氧間歇和低 RPE 的技術地形、強化期和專項期偏巡航間歇（閾值課在 VO2max 開放後也不停）和賽道的爬升、下坡、技術地形，"
           "減量期偏短的課（Koop；Uphill Athlete 由一般到專項）。"
           "④ 時間：超過這天上限的往後排，接近這堂原本分鐘數的往前。⑤ 地形：越野日偏上坡版，路跑日不推需要找坡的課。"
           "⑥ 類型：長跑日偏 90 分以上的課。⑦ 主要訓練項目是路跑時：不推越野範本，專項期偏馬拉松專項課"
           "（Pfitzinger 乳酸閾值／馬拉松配速長跑、Daniels T、Canova）。其他範本收在下面，照原本的順序。")


def row_minutes(row: dict) -> float:
    """A template's whole length (warm-up to cool-down), minutes; distance steps at
    6:00/km and lap-button steps at their protocol minimum (推估, the menu only)."""
    sec = 0.0

    def walk(items):
        nonlocal sec
        for it in items or []:
            if it.get("kind") == "repeat":
                n = int(it.get("times") or 1)
                for i in range(n):
                    kids = list(it.get("items") or [])
                    if i == n - 1 and it.get("last_rest") is False:
                        while kids and kids[-1].get("kind") == "rest":
                            kids.pop()
                    walk(kids)
                continue
            d = it.get("dur") or {}
            if d.get("type") == "time":
                sec += float(d.get("value") or 0)
            elif d.get("type") == "distance":
                # 6:00/km taken as an easy pace ≈ 1.25 × threshold pace; a pace step scales from it
                t = it.get("target") or {}
                mid = (t["lo"] + t["hi"]) / 2 if t.get("type") == "pace" and t.get("mode") == "pct" else 1.25
                sec += float(d.get("value") or 0) / 1000.0 * DIST_S_PER_KM * mid / 1.25
            else:
                sec += float(d.get("est") or 0)
    walk(row.get("full") or row.get("items"))
    return sec / 60.0


def _is_hill(row: dict, cat: str) -> bool:
    v = IL.get(row.get("key"))
    if v is not None:
        return v.terrain == "hill"
    return cat == "trail"


def _is_z5(row: dict, sub: Optional[str]) -> bool:
    v = IL.get(row.get("key"))
    return (v.cls == "Z5") if v is not None else sub in ("vo2max", "speed")


class _Score:
    def __init__(self):
        self.v = 0.0
        self.plus: list[tuple[float, str]] = []
        self.minus: list[tuple[float, str]] = []

    def add(self, pts: float, why: str):
        self.v += pts
        (self.plus if pts > 0 else self.minus).append((pts, why))

    def reason(self, fallback: str) -> str:
        best = max(self.plus, key=lambda x: x[0])[1] if self.plus else fallback
        worst = min(self.minus, key=lambda x: x[0])[1] if self.minus else ""
        return f"{best}；但{worst}" if worst and self.v > DROP_BELOW else best


def _score(row: dict, cat: str, sub: Optional[str], s: dict) -> _Score:
    sc = _Score()
    key = row.get("key")
    v = IL.get(key)
    mins = row_minutes(row)
    phase, kind, terrain = s["phase"], s["kind"], s["terrain"]
    cap, want = s["cap"], s["minutes"]
    fam = row.get("family") or {}                 # workout_templates.family_of (the tab id = its id)
    sub, fsub = fam.get("id") or sub, fam.get("sub")

    # ① the ladder: the generator's own pick (fit() already fitted it into the day's cap) is
    # always first, whatever the other rules say
    if cat == "quality" and key and key == s["ladder_key"]:
        sc.add(1000, s["ladder_reason"] or "間歇階梯的下一步")
        return sc
    if cat == "quality":
        if v is not None and v.rung == s["rung"] and v.listed_equiv:
            sc.add(40, f"{IL.RUNG_NAME.get(v.rung, v.rung)} 的同等課表：換它不影響進階")
        elif v is not None and not v.listed_equiv:
            sc.add(-25, "非同等：不算進階")
        elif v is not None and s["rung"] and v.rung != s["rung"]:
            sc.add(-10, f"是 {IL.RUNG_NAME.get(v.rung, v.rung)}，不是你目前這一階")
        elif v is None and s["rung"] and sub and sub == _rung_family(s["rung"]):
            sc.add(15, f"和你目前這一階同一類（{WT.FAMILY_LABEL[sub]}）")
    # ② Zone 5
    if cat == "quality" and _is_z5(row, sub):
        if not s["z5_open"]:
            sc.add(Z5_CLOSED, "5 區還沒開放")
        elif phase in ("build", "specific"):
            sc.add(12, f"{PHASE_LABEL[phase]}：5 區已開放")
    # ③ phase
    if cat == "quality":
        if phase == "base" and sub == "aerobic":
            sc.add(15, "基礎期先練有氧間歇（台灣教練：先練 3 區）")
        elif phase in ("build", "specific") and fsub in ("cruise", "supra"):
            sc.add(10, f"{PHASE_LABEL[phase]}：巡航間歇，閾值課不停")
        elif phase in ("taper", "recovery") and mins <= SHORT_MIN + 15:
            sc.add(10, f"{PHASE_LABEL[phase]}：量少")
    elif cat == "trail":
        if phase in ("specific", "build") and key in TRAIL_SPECIFIC:
            sc.add(20, f"{PHASE_LABEL[phase]}：" + ("接近比賽的路況" if key == "lib:tech_hard" else "練賽道的爬升／下坡"))
        elif phase == "base" and key in TRAIL_BASE:
            sc.add(15, "基礎期：低 RPE 技術地形，可取代部分長跑" if key == "lib:tech_easy" else "基礎期：有氧爬坡、腿力")
        if phase == "taper" and key == "lib:downhill_ecc":
            sc.add(-80, "賽前 2 週內不做下坡離心")
    elif cat in ("easy",) and phase in ("taper", "recovery") and mins <= SHORT_MIN + 15:
        sc.add(12, f"{PHASE_LABEL[phase]}：短一點")
    elif cat == "test" and key in ("cp_quick", "cp_standard"):
        sc.add(20, "這個 app 的 CP 測試：做完會更新 CP")
    # ④ time
    if cap is not None and mins > cap + 0.5:
        sc.add(OVER_CAP, f"超過這天上限 {cap:.0f} 分（要 {mins:.0f} 分）")
    elif want and want > 0 and mins >= ADDON_MIN:
        close = 1.0 - abs(mins - want) / max(want, 30.0)
        if close > 0:
            sc.add(15 * close, f"{mins:.0f} 分，剛好是這堂的時間" if abs(mins - want) <= 5
                   else f"{mins:.0f} 分，接近這堂的 {want:.0f} 分")
    # ⑤ terrain
    if terrain == "trail" and _is_hill(row, cat) and cat != "trail":
        sc.add(15, "越野日：上坡版")
    elif terrain == "road" and _is_hill(row, cat) and cat == "quality":
        sc.add(-15, "要找坡（這堂是路跑）")
    # ⑥ the session's own type
    if cat == "easy":
        if kind == "long":
            if mins >= LONG_MIN:
                sc.add(25, f"長跑日：{mins:.0f} 分夠長")
            else:
                sc.add(-10, "長跑日嫌短")
        elif kind == "easy" and mins <= 70 and key != "strides":
            sc.add(8, "輕鬆跑的長度")
    # ⑦ 主要訓練項目 = 路跑
    if s.get("sport") == "road":
        if cat == "trail":
            sc.add(ROAD_NO_TRAIL, "主要訓練項目是路跑")
        elif phase in ("specific", "build") and key in ROAD_SPECIFIC:
            sc.add(20, f"{PHASE_LABEL[phase]}：馬拉松專項")
        if kind == "long" and key in ROAD_LONG:
            sc.add(10, "馬拉松的長跑")
    return sc


def _rung_family(rung: Optional[str]) -> Optional[str]:
    c = IL.canonical(rung) if rung else None
    f = WT.family_of_variant(c) if c is not None else None
    return f["id"] if f else None


def ladder_pick(rung: Optional[str], cap: Optional[float], history=(), prefs=None, gate: Optional[dict] = None,
                track: Optional[str] = None) -> tuple[Optional[str], str]:
    """(variant key, one-line reason) of the old 間歇範本 ★ 推薦: interval_library.fit for
    the athlete's current rung and the day's cap (what the generator schedules). Two tracks
    (SP-31): with `gate`, the rung is the one `track` stands at (the session's own class —
    a Zone 3 session gets the Zone 3 ladder's next step, a Zone 5 one the Zone 5's;
    quality_gate.rung_now), `rung` is then ignored."""
    if gate is not None:
        from backend.engine import quality_gate as QG
        rung = QG.rung_now(gate, track)
    if rung not in IL.LIBRARY:
        return None, ""
    f = IL.fit(rung, cap, history, prefs)
    name = IL.RUNG_NAME.get(f.get("rung") or rung, rung)
    return f["variant"].key, f"間歇階梯的下一步（{name}）：{f['reason']}"


def recommend(tpl: dict, *, kind: str, cap: Optional[float] = None, minutes: Optional[float] = None,
              terrain: str = "road", phase: Optional[str] = None, z5_open: bool = False,
              rung: Optional[str] = None, ladder_key: Optional[str] = None, ladder_reason: str = "",
              sport: str = "trail") -> dict:
    """{"cats": {cat id: [{"key", "reason"}] best first, ≤ 3}, "inputs": {...}, "tip"}
    over workout_steps.templates() output `tpl`."""
    ph = PHASE_OF.get(phase or "", "base")
    s = {"kind": kind or "easy", "cap": cap, "minutes": float(minutes or 0) or None, "terrain": terrain or "road",
         "phase": ph, "z5_open": bool(z5_open), "rung": rung if rung in IL.LIBRARY else None,
         "ladder_key": ladder_key, "ladder_reason": ladder_reason, "sport": sport}
    out = {}
    for c in tpl.get("cats") or []:
        cid = c["id"]
        scored, seen = [], set()
        i = 0
        for g in tpl.get("groups") or []:
            if g.get("cat") != cid:
                continue
            for r in g.get("rows") or []:
                if r.get("key") in seen:
                    continue
                seen.add(r.get("key"))
                sc = _score(r, cid, g.get("sub"), s)
                scored.append((-sc.v, i, r, sc))
                i += 1
        scored.sort(key=lambda x: (x[0], x[1]))
        picks, per_rung = [], {}
        for _neg, _i, r, sc in scored:
            if sc.v <= DROP_BELOW or len(picks) >= N_RECS:
                continue
            fam = FAMILY.get(r.get("key"))
            if fam and fam in per_rung:
                continue
            if fam:
                per_rung[fam] = 1
            v = IL.get(r.get("key"))
            if v is not None:
                if per_rung.get(v.rung, 0) >= 2:
                    continue
                per_rung[v.rung] = per_rung.get(v.rung, 0) + 1
            picks.append({"key": r["key"], "reason": sc.reason(f"{row_minutes(r):.0f} 分")})
        out[cid] = picks
    return {"cats": out, "tip": EXPLAIN,
            "inputs": {"phase": ph, "phase_label": PHASE_LABEL[ph], "z5_open": s["z5_open"], "cap": cap,
                       "minutes": s["minutes"], "terrain": s["terrain"], "kind": s["kind"],
                       "rung": s["rung"], "rung_name": IL.RUNG_NAME.get(s["rung"] or "", None), "sport": sport,
                       "ladder_key": ladder_key}}
