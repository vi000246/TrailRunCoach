from dataclasses import dataclass
from typing import Optional


@dataclass
class PowerZone:
    number: int
    name: str
    low_pct_ftp: float
    high_pct_ftp: float
    low_w: Optional[float] = None
    high_w: Optional[float] = None


COGGAN_CLASSIC_ZONES = [
    PowerZone(1, "Active Recovery", 0.0, 0.55),
    PowerZone(2, "Endurance", 0.55, 0.75),
    PowerZone(3, "Tempo", 0.75, 0.90),
    PowerZone(4, "Lactate Threshold", 0.90, 1.05),
    PowerZone(5, "VO2max", 1.05, 1.20),
    PowerZone(6, "Anaerobic Capacity", 1.20, 1.50),
    PowerZone(7, "Neuromuscular Power", 1.50, 9999.0),
]


def coggan_classic_zones(ftp_w: float) -> list[PowerZone]:
    zones = []
    for z in COGGAN_CLASSIC_ZONES:
        z2 = PowerZone(
            z.number, z.name, z.low_pct_ftp, z.high_pct_ftp,
            round(z.low_pct_ftp * ftp_w),
            round(z.high_pct_ftp * ftp_w) if z.high_pct_ftp < 9999 else None,
        )
        zones.append(z2)
    return zones


def ftp_from_mmp(mmp_curve: dict[int, float]) -> Optional[float]:
    """ftp(meanmax(power)) — peak 60-min average power."""
    if 3600 in mmp_curve and mmp_curve[3600] > 0:
        return round(mmp_curve[3600], 1)
    if 1200 in mmp_curve and mmp_curve[1200] > 0:
        return round(mmp_curve[1200] * 0.95, 1)
    return None
