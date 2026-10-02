"""
進階 C 類 (generalize-athlete plan B8): numbers that are right for most
runners and that nobody needs to fit — shown under 設定 → 進階設定 → 自動估算的參數
with a manual override (engine/calibrate.py items with manual_only, no fit).

  heat_partial_hadley   the Hadley sum below which a minute adds no heat
                        dose (engine/heat.PARTIAL_HADLEY 130; 150 = the
                        Hadley table's hot threshold stays fixed). T4
  pack_daily_drop_kg    food eaten per day of a multi-day trip
                        (racepower/capacity.PACK_DAILY_DROP 0.7 kg). P4
"""
from __future__ import annotations

from backend.engine import calibrate as CAL


def _none(ds=None, today=None):
    return None


CAL.register(CAL.Item(
    name="heat_partial_hadley", label="熱適應開始累積的熱指數", unit="Hadley", default=130.0,
    default_src="推估（以台北夏天傍晚設定；150 以上算滿劑量）", k=1, min_n=10 ** 9, fit=_none,
    bounds=(100.0, 149.0), digits=0, manual_only=True,
    help="熱適應指數：每分鐘的熱劑量在這個 Hadley 以下是 0，到 150 是滿劑量。"))
CAL.register(CAL.Item(
    name="pack_daily_drop_kg", label="多日行程每天吃掉的糧食", unit="kg", default=0.7,
    default_src="推估", k=1, min_n=10 ** 9, fit=_none,
    bounds=(0.0, 2.0), digits=1, manual_only=True,
    help="多日行程第 2 天起，背包每天輕這麼多（糧食）。"))


def partial_hadley() -> float:
    return CAL.value("heat_partial_hadley")


def pack_daily_drop() -> float:
    return CAL.value("pack_daily_drop_kg")
