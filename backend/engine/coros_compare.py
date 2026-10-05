"""
「app 和手錶數值對照」 (SP-67; docs/research/coros-threshold-unification.md): the app's
LTHR / max HR / resting HR / HR zones beside the COROS account's own, a reminder when
they are more than ALERT_BPM apart, and the account's values over time.

Owner decisions (2026-10-05):
  * the app is the one reference. Pushed workouts carry absolute bpm, so the watch's own
    thresholds never move a pushed target (research §2.3: COROS keeps the bpm and
    recomputes only its percent field with its own LTHR) — nothing is written back to
    COROS, and pushed workouts are not pushed again
  * more than 5 bpm apart → tell the user to change the value in the COROS app by hand,
    and where
  * keep the account's values of every sync (HISTORY_KEY); the card shows the changes
  * NOT done: restarting the TL ↔ TSS fit (engine/coros_tl.py) on a COROS threshold change

What differs when the two sides disagree (research §3): the zones the watch and the COROS
app show, COROS's training load, workouts built inside Training Hub in % LTHR. Not the
workouts this app pushes.

What can be changed by hand on the COROS side — COROS support 「Setting Resting and Max
Heart Rate Data」 and 「April 2023 Update Common Questions」, read only as search summaries
(the support site answered 403), so 未驗證: max and resting HR in the COROS app (Profile →
Settings → Heart Rate Zones); the LTHR 「can only be automatically adjusted by the
algorithm」; the zone edges can be edited (whether any edge is accepted: 待實測). The texts
below say so where it matters.

The watch's zones are not read as bpm: the account's lthrZone carries ratios with hr 0
(hr_profile.py), so they are computed as COROS does — the account's ratios × the account's
LTHR / max / resting HR, rounded to whole bpm. Which of its three models the watch shows
(hrZoneType) is not known (research §7), so the card compares the model the 課表 uses.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Optional

from backend.i18n import N_, _

HISTORY_KEY = "athlete.coros_profile_history"   # user_settings: [{at, lthr, max_hr, rest_hr, ratios, hr_zone_type}]
HISTORY_MAX = 400               # entries kept (one per change; the oldest go first)
HISTORY_SHOWN = 12              # entries the card lists
# the gap that raises the reminder: owner 2026-10-05 「差距超過 5 bpm 就提醒」 (推估／教練級 — the
# research proposed 5; for scale, Lu 2025's COROS Pace 3 LTHR is off by 8.93 bpm on average)
ALERT_BPM = 5
TRACKED = ("lthr", "max_hr", "rest_hr", "ratios", "hr_zone_type")

NOTE = N_("推到手錶的課表心率目標直接送 bpm，用的是 app 的數值，不受手錶設定影響。"
          "手錶和 COROS app 上顯示的心率區間、COROS 的訓練負荷用的是手錶自己的數值，"
          "所以兩邊差太多時，同一次跑步在兩邊看到的區間會不一樣。")
WHERE = N_("去哪裡改：COROS app 的「個人頁 → 設定 → 心率區間」（選單名稱可能隨 app 版本不同）")
NAMES = {"max_hr": N_("最大心率"), "rest_hr": N_("靜息心率"), "zones": N_("心率區間")}


def _name(fid: str) -> str:
    return "LTHR" if fid == "lthr" else _(NAMES[fid])


# ---------------------------------------------------------------------------
# the account's values over time (written by sync/coros_client.store_hr_profile)
# ---------------------------------------------------------------------------

def _values(p: Optional[dict]) -> dict:
    """The tracked values of a profile as the settings store returns them (JSON: the ratio
    tuples of hr_profile.parse_account become lists) — what two reads are compared by."""
    return json.loads(json.dumps({k: (p or {}).get(k) for k in TRACKED}, sort_keys=True))


def history_add(history, prof: dict, previous: Optional[dict] = None) -> Optional[list]:
    """The history with this read: `prof` (hr_profile.parse_account + "at") is appended when
    a tracked value differs from the last entry — one entry per change, dated by the sync
    that first saw it (the change itself happened between the sync before and that one).
    An empty history starts with `previous`, the profile stored before the history existed.
    None = nothing to write."""
    rows = [r for r in history if isinstance(r, dict)] if isinstance(history, list) else []
    n = len(rows)
    if not rows and isinstance(previous, dict) and previous.get("at") and any(_values(previous).values()):
        rows.append({"at": previous["at"], **_values(previous)})
    if not rows or _values(rows[-1]) != _values(prof):
        rows.append({"at": prof.get("at"), **_values(prof)})
    return rows[-HISTORY_MAX:] if len(rows) != n else None


def validate(value) -> None:
    """settings/repository.validate for HISTORY_KEY."""
    if value is not None and not (isinstance(value, list) and all(isinstance(r, dict) for r in value)):
        raise ValueError(f"{HISTORY_KEY} must be a list of objects or null")


def _day(at, tz=None) -> Optional[str]:
    """The athlete's local date of a stored UTC read time."""
    try:
        t = dt.datetime.fromisoformat(str(at))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return (t.astimezone(tz) if tz is not None else t).date().isoformat()


def _changes(old: dict, new: dict) -> str:
    """「LTHR 182 → 152、最大心率 202 → 185」 — what differs between two history entries."""
    out = []
    for k in ("lthr", "max_hr", "rest_hr"):
        a, b = old.get(k), new.get(k)
        if a != b:
            out.append(f"{_name(k)} {a if a is not None else '–'} → {b if b is not None else '–'}")
    if _values(old)["ratios"] != _values(new)["ratios"] or old.get("hr_zone_type") != new.get("hr_zone_type"):
        out.append(_("區間設定改過"))
    return _("、").join(out)


def history_view(history, tz=None, limit: int = HISTORY_SHOWN) -> dict:
    """{"rows": newest first [{day, lthr, max_hr, rest_hr, text}], "n", "last_change"}: the
    last `limit` entries, each with what changed since the one before (「開始記錄」 for the
    first); last_change = the newest change's {day, text}, None while nothing has changed."""
    rows = [r for r in history if isinstance(r, dict)] if isinstance(history, list) else []
    out = []
    for i, r in enumerate(rows):
        out.append({"day": _day(r.get("at"), tz), "lthr": r.get("lthr"), "max_hr": r.get("max_hr"),
                    "rest_hr": r.get("rest_hr"), "first": i == 0,
                    "text": _changes(rows[i - 1], r) if i else _("開始記錄")})
    last = next((r for r in reversed(out) if not r["first"]), None)
    return {"rows": out[::-1][:limit], "n": len(out),
            "last_change": {"day": last["day"], "text": last["text"]} if last else None}


# ---------------------------------------------------------------------------
# the comparison
# ---------------------------------------------------------------------------

def _bpm(v) -> Optional[int]:
    try:
        return int(round(float(v))) if v else None
    except (TypeError, ValueError):
        return None


def _row(fid: str, app, source: Optional[str], watch, from_watch: bool = False) -> dict:
    a, w = _bpm(app), _bpm(watch)
    diff = None if a is None or w is None else a - w
    return {"id": fid, "name": _name(fid), "app": a, "app_source": source, "watch": w, "diff": diff,
            "alert": diff is not None and abs(diff) > ALERT_BPM, "from_watch": bool(from_watch)}


def _zones(plan_zones: Optional[dict], acc: dict) -> Optional[dict]:
    """The 課表's zones (hr_profile.plan_hr_zones, the app's values) beside the same COROS
    model with the account's values; `diff` = the zone's upper edge, app − watch."""
    from backend.engine import hr_profile as HP
    if not plan_zones or not plan_zones.get("rows") or plan_zones.get("model") not in HP.PLAN_MODELS:
        return None
    model = plan_zones["model"]
    out = {"model": model, "label": plan_zones.get("short") or _(HP.MODEL_SHORT[model]),
           "edges": [_bpm(r.get("hi")) for r in plan_zones["rows"][:5]]}
    w = HP.zone_rows(model, acc.get("lthr"), acc.get("max_hr"), acc.get("rest_hr"), acc)
    if "reason" in w:
        return {**out, "rows": [], "alert": False,
                "reason": _("COROS 帳號少了這組區間要用的數值，沒辦法比")}
    rows = []
    for a, (zid, name, lo, hi) in zip(plan_zones["rows"], w["rows"]):
        ah, wh = _bpm(a.get("hi")), _bpm(hi)
        diff = None if ah is None or wh is None else ah - wh
        rows.append({"id": zid, "name": name, "app": [_bpm(a.get("lo")), ah], "watch": [_bpm(lo), wh],
                     "diff": diff, "alert": diff is not None and abs(diff) > ALERT_BPM})
    return {**out, "rows": rows, "alert": any(r["alert"] for r in rows)}


def _lthr_edges(lthr: Optional[int], acc: dict) -> str:
    """「128／144／152／163／170」: the app's LTHR-model edges, to type into COROS."""
    from backend.engine import hr_profile as HP
    z = HP.zone_rows("lthr", lthr, acc=acc)
    return _("／").join(f"{r[3]:.0f}" for r in z["rows"][:5]) if "rows" in z else ""


def compare(lthr, lthr_source: Optional[str], mx: Optional[dict], rs: Optional[dict],
            plan_zones: Optional[dict], acc: Optional[dict], history=None, tz=None) -> Optional[dict]:
    """The card (JSON-able), or None without a COROS account read.

    `lthr` / `lthr_source`: the app's LTHR in effect and where it comes from
    (zones.training_targets); `mx` / `rs`: hr_profile.max_hr / rest_hr; `plan_zones`:
    hr_profile.plan_hr_zones (training_targets' "hr_model"); `acc`: hr_profile.account.

    {"alert_bpm", "read_day", "rows": [{id, name, app, app_source, watch, diff, alert,
     from_watch}], "zones": {model, label, edges, rows: [{id, name, app: [lo, hi],
     watch: [lo, hi], diff, alert}], alert} | None, "alert", "summary", "fixes": [text],
     "where", "note", "history": history_view}"""
    if not isinstance(acc, dict):
        return None
    mx, rs = mx or {}, rs or {}
    rows = [_row("lthr", lthr, lthr_source, acc.get("lthr")),
            _row("max_hr", mx.get("value"), mx.get("source"), acc.get("max_hr"), mx.get("kind") == "coros"),
            _row("rest_hr", rs.get("value"), rs.get("source"), acc.get("rest_hr"), rs.get("kind") == "coros")]
    by = {r["id"]: r for r in rows}
    zones = _zones(plan_zones, acc)
    z_alert = bool(zones and zones["alert"])
    fixes = []
    if by["lthr"]["alert"] or (z_alert and zones["model"] == "lthr"):
        edges = _lthr_edges(by["lthr"]["app"], acc)
        fixes.append(_("LTHR：COROS 的 {watch} 不能自己輸入，只會由 COROS 的演算法更新（依 COROS 支援文件，還沒實測）。"
                       "想讓手錶的乳酸閾值區間和 app 一樣，可以在同一頁把區間的五條邊界改成 {edges}"
                       "（COROS 收不收任意邊界還沒實測，填不進去就維持原樣）",
                       watch=by["lthr"]["watch"], edges=edges))
    for fid in ("max_hr", "rest_hr"):
        if by[fid]["alert"]:
            fixes.append(_("{name}：把 COROS 的 {watch} 改成 {app}", name=by[fid]["name"],
                           watch=by[fid]["watch"], app=by[fid]["app"]))
    if z_alert and zones["model"] != "lthr":
        fixes.append(_("心率區間（{model}）：把最大心率和靜息心率改成 app 的數值後，這組區間就會一樣",
                       model=zones["label"]))
    names = [r["name"] for r in rows if r["alert"]] + ([_name("zones")] if z_alert else [])
    summary = (_("{names}差超過 {bpm} bpm，建議到 COROS app 手動改成 app 的數值", names=_("、").join(names), bpm=ALERT_BPM)
               if names else _("兩邊的差距都在 {bpm} bpm 以內，不用改", bpm=ALERT_BPM))
    return {"alert_bpm": ALERT_BPM, "read_day": _day(acc.get("at"), tz), "rows": rows, "zones": zones,
            "alert": bool(names), "summary": summary, "fixes": fixes, "where": _(WHERE), "note": _(NOTE),
            "history": history_view(history, tz)}


def view(tt: Optional[dict], mx: Optional[dict], rs: Optional[dict], acc: Optional[dict],
         user_id: int = 1) -> Optional[dict]:
    """compare() with the stored history and the athlete's time zone (api/plan.hr_profile_view;
    `tt` = zones.training_targets or None before the first run)."""
    if not isinstance(acc, dict):
        return None
    from backend.engine.wko5expr.datasource import athlete_tz, read_setting
    tt = tt or {}
    return compare(tt.get("lthr"), tt.get("lthr_source"), mx, rs, tt.get("hr_model"), acc,
                   read_setting(HISTORY_KEY, None, user_id), athlete_tz(user_id))
