"""
進階 C 類 (generalize-athlete plan B8): numbers that are right for most
runners and that nobody needs to fit — shown under 設定 → 進階設定 → 自動估算的參數
with a manual override (engine/calibrate.py items with manual_only, no fit).

  heat_partial_hadley   the Hadley sum below which a minute adds no heat
                        dose (engine/heat.PARTIAL_HADLEY 130; 150 = the
                        Hadley table's hot threshold stays fixed). T4
  pack_daily_drop_kg    food eaten per day of a multi-day trip
                        (racepower/capacity.PACK_DAILY_DROP 0.7 kg). P4
  z3_unlock_weeks, z3_unlock_runs_per_week, z3_unlock_max_gap_days, z3_relock_days
                        the Zone 3 unlock rule (quality_gate.Z3_WEEKS_NEED 4,
                        Z3_RUNS_PER_WEEK 3, Z3_MAX_GAP_DAYS 7, Z3_RELOCK_DAYS 21;
                        SP-295) — read through z3_rule()
"""
from __future__ import annotations

from backend.engine import calibrate as CAL
from backend.i18n import N_


def _none(ds=None, today=None):
    return None


CAL.register(CAL.Item(
    name="heat_partial_hadley", label=N_("熱適應開始累積的熱指數"), unit="Hadley", default=130.0,
    default_src=N_("推估（以台北夏天傍晚設定；150 以上算滿劑量）"), k=1, min_n=10 ** 9, fit=_none,
    bounds=(100.0, 149.0), digits=0, manual_only=True,
    help=N_("熱適應指數：每分鐘的熱劑量在這個 Hadley 以下是 0，到 150 是滿劑量。")))
CAL.register(CAL.Item(
    name="pack_daily_drop_kg", label=N_("多日行程每天吃掉的糧食"), unit="kg", default=0.7,
    default_src=N_("推估"), k=1, min_n=10 ** 9, fit=_none,
    bounds=(0.0, 2.0), digits=1, manual_only=True,
    help=N_("多日行程第 2 天起，背包每天輕這麼多（糧食）。")))


def partial_hadley() -> float:
    return CAL.value("heat_partial_hadley")


def pack_daily_drop() -> float:
    return CAL.value("pack_daily_drop_kg")


# ---- 3 區解鎖 (SP-295; estimated-constants-inventory.md §3.2, §4.2, §5.5) -------------------------
# The Zone 3 gate's consistency path (quality_gate.z3_gate / z3_consistency): N complete weeks with
# ≥ M runs each and no stretch of ≥ G days without running; a break of ≥ R days re-locks. A rule the
# owner set on 2026-10-04 — no outside source for 4 / 3 / 7 and no outcome that could fit them, so
# C 類: the defaults stay (= quality_gate.Z3_*), the user may change them in 設定 → 進階設定.
# The bounds are 推估 (a sane range, not a recommendation); whole numbers only.
Z3_RULE = {"weeks": "z3_unlock_weeks", "runs": "z3_unlock_runs_per_week",
           "gap": "z3_unlock_max_gap_days", "relock": "z3_relock_days"}

CAL.register(CAL.Item(
    name="z3_unlock_weeks", label=N_("3 區解鎖：連續幾週規律訓練"), unit=N_("週"), default=4.0,
    default_src=N_("推估（app 的預設規則，沒有外部來源）"), k=1, min_n=10 ** 9, fit=_none,
    bounds=(1.0, 16.0), digits=0, manual_only=True, integer=True,
    help=N_("3 區（有氧間歇／節奏跑）的解鎖條件之一：連續這麼多個完整週，每週都跑到下面的次數、"
            "中間沒有太久沒跑。改了以後課表會照新的數字重算。")))
CAL.register(CAL.Item(
    name="z3_unlock_runs_per_week", label=N_("3 區解鎖：每週至少跑幾次"), unit=N_("次／週"), default=3.0,
    default_src=N_("推估（app 的預設規則，沒有外部來源）"), k=1, min_n=10 ** 9, fit=_none,
    bounds=(1.0, 7.0), digits=0, manual_only=True, integer=True,
    help=N_("那幾週裡，每一週都要跑到這個次數才算規律訓練。")))
CAL.register(CAL.Item(
    name="z3_unlock_max_gap_days", label=N_("3 區解鎖：最長幾天不跑就不算連續"), unit=N_("天"), default=7.0,
    default_src=N_("推估（app 的預設規則，沒有外部來源）"), k=1, min_n=10 ** 9, fit=_none,
    bounds=(1.0, 21.0), digits=0, manual_only=True, integer=True,
    help=N_("那幾週裡，連續這麼多天沒跑，就要重新累積（賽後恢復期、轉換期的日子不算）。")))
CAL.register(CAL.Item(
    name="z3_relock_days", label=N_("3 區重新上鎖：停跑幾天"), unit=N_("天"), default=21.0,
    default_src=N_("推估（Coyle 1984：停練 21 天最大攝氧量掉約 7%）"), k=1, min_n=10 ** 9, fit=_none,
    bounds=(7.0, 120.0), digits=0, manual_only=True, integer=True,
    help=N_("停跑這麼多天，3 區重新上鎖，只算停跑之後的紀錄（賽後恢復期、轉換期的日子不算）。")))


def z3_rule(user_id: int = 1) -> dict:
    """The Zone 3 unlock rule in effect: {"weeks", "runs", "gap", "relock"} (whole numbers) and
    "manual" (any of them set by hand → the texts say 手動, else 預設)."""
    out: dict = {"manual": False}
    for k, name in Z3_RULE.items():
        e = CAL.entry(name, user_id)
        out[k] = int(round(float(e["value"])))
        out["manual"] = out["manual"] or e.get("source") == "user"
    return out


def z3_rule_stamp(user_id: int = 1) -> tuple:
    """For the plan / status cache keys: a changed rule recomputes the gate and the plan."""
    r = z3_rule(user_id)
    return tuple(r[k] for k in Z3_RULE)
