"""
預設背負, personalised (SP-740, owner 2026-10-19): the pack a hike is taken to carry when none
was entered — the calculator's trip without a 背負, and every past hike without a recorded one
in the walking-capacity fit (athlete.walk_capacity_inputs) — one rule for both, so the fit and
the prediction never disagree.

Per kind (單日 / 多日), in this order:
  1. 手動: 設定 → 進階設定 → 單日／多日登山預設背負 (advanced_params, calibrate items
     pack_default_single_kg / pack_default_multi_kg; blank = 自動)
  2. 自己的: the median of the packs recorded on the activity card / the activity list
     (「這次背多少」, racepower_hike_meta.json) of that kind's hikes, once there are
     MIN_TRIPS of them (推估: below 3 one odd trip would set it)
  3. 通用: capacity.PACK_DEFAULT_SINGLE 4 kg / PACK_DEFAULT_MULTI 9 kg (SP-738)

Not done (owner): inferring the pack from the climbing speed — it cannot be told apart from
the terrain, the fatigue and the wrist HR error.

A record is a 多日 trip when it spans more than one calendar day (`days`, written with the
pack by athlete.set_hike_meta; the same calendar-day split as athlete._day_slices). Records
of a non-hike activity (`hike` false: a run with a vest) never count; older records without
the two fields count as single-day hikes.
"""
from __future__ import annotations

import statistics
from pathlib import Path
from typing import Optional

MIN_TRIPS = 3                     # 推估 (SP-740): recorded trips of a kind before their median is used
MANUAL = {"single": "pack_default_single_kg", "multi": "pack_default_multi_kg"}
KIND_ZH = {"single": "單日", "multi": "多日"}

_MEMO: dict = {}


def kind_of(days) -> str:
    return "multi" if int(days or 1) > 1 else "single"


def _fallback(kind: str) -> float:
    from backend.engine.racepower.capacity import PACK_DEFAULT_MULTI, PACK_DEFAULT_SINGLE
    return PACK_DEFAULT_MULTI if kind == "multi" else PACK_DEFAULT_SINGLE


def recorded(meta=None) -> dict:
    """{"single": [kg…], "multi": [kg…]}: the recorded packs of hikes, by kind."""
    out: dict = {"single": [], "multi": []}
    if meta is None:
        meta = _meta_memo()
    for rec in dict(meta).values():
        if not isinstance(rec, dict) or rec.get("pack_kg") is None or rec.get("hike") is False:
            continue
        try:
            out[kind_of(rec.get("days"))].append(float(rec["pack_kg"]))
        except (TypeError, ValueError):
            continue
    return out


def _meta_memo() -> dict:
    """athlete.hike_meta(), re-read only when the file changes (the fit asks once per trip)."""
    from backend.engine.racepower import athlete as A
    p = Path(A._hike_meta_path())
    try:
        st = p.stat()
        stamp = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = (str(p), None, None)
    hit = _MEMO.get("meta")
    if hit and hit[0] == stamp:
        return hit[1]
    meta = A.hike_meta(p)
    _MEMO["meta"] = (stamp, meta)
    return meta


def manual(kind: str, user_id: int = 1) -> Optional[float]:
    """The 進階設定 value of `kind`, or None (自動)."""
    try:
        from backend.engine import calibrate as CAL
        e = CAL.entry(MANUAL[kind], user_id)
        return float(e["value"]) if e.get("source") == "user" else None
    except Exception:                       # noqa: BLE001 — no settings store: 自動
        return None


def resolve(days=1, meta=None, manual_kg: Optional[float] = ..., user_id: int = 1) -> dict:
    """The 預設背負 of a `days`-day trip: {kind, kg, source (manual | own | default), n (recorded
    trips of the kind), median (theirs, or None), text (「5 kg（你記過的 5 趟單日，中位數）」),
    note (one sentence: where the number comes from)}. `manual_kg` ... = read the setting."""
    kind = kind_of(days)
    zh = KIND_ZH[kind]
    packs = recorded(meta)[kind]
    n = len(packs)
    med = round(statistics.median(packs), 1) if packs else None
    m = manual(kind, user_id) if manual_kg is ... else manual_kg
    if m is not None:
        kg, src = float(m), "manual"
        text = f"{kg:g} kg（{zh}，進階設定）"
        note = f"{zh}預設背負 {kg:g} kg：你在進階設定手動指定"
    elif n >= MIN_TRIPS:
        kg, src = float(med), "own"
        text = f"{kg:g} kg（你記過的 {n} 趟{zh}，中位數）"
        note = f"你記過的 {n} 趟{zh}，中位數 {kg:g} kg"
    else:
        kg, src = _fallback(kind), "default"
        text = f"{kg:g} kg（{zh}）"
        note = (f"{zh}通用值 {kg:g} kg（推估）；你記過的{zh}背負 {n} 趟，滿 {MIN_TRIPS} 趟改用自己的中位數")
    short = {"manual": "預設背負，進階設定", "own": f"預設背負：你記過的 {n} 趟{zh}，中位數",
             "default": "預設背負"}[src]
    return {"kind": kind, "kg": kg, "source": src, "n": n, "median": med, "text": text, "note": note,
            "short": short}


def both(meta=None, user_id: int = 1) -> dict:
    """{"single": resolve(1), "multi": resolve(2)} — the settings page and the activity list."""
    if meta is None:
        meta = _meta_memo()
    return {"single": resolve(1, meta, user_id=user_id), "multi": resolve(2, meta, user_id=user_id)}


def rule_text(meta=None) -> str:
    """Both kinds in a few words: 「單日 4 kg、多日 9 kg」 while both are the 通用值."""
    b = both(meta)
    parts = []
    for k in ("single", "multi"):
        r = b[k]
        if r["source"] == "default":
            parts.append(f"{KIND_ZH[k]} {r['kg']:g} kg")
        elif r["source"] == "own":
            parts.append(f"{KIND_ZH[k]} {r['kg']:g} kg（你記過的 {r['n']} 趟，中位數）")
        else:
            parts.append(f"{KIND_ZH[k]} {r['kg']:g} kg（進階設定）")
    return "、".join(parts)


def forget() -> None:
    _MEMO.clear()
