"""
Power model: FTP estimation from MMP curve.
WKO5 symbols: ftppower, frcpower, pmaxpower
"""
from typing import Optional


def ftp_estimate(mmp_curve: dict[int, float]) -> Optional[float]:
    """
    Best estimate of FTP from MMP curve.
    Primary: MMP[3600] (60-min power)
    Fallback: MMP[1200] * 0.95 (20-min × 0.95)
    """
    if 3600 in mmp_curve and mmp_curve[3600] > 0:
        return round(mmp_curve[3600], 1)
    if 1200 in mmp_curve and mmp_curve[1200] > 0:
        return round(mmp_curve[1200] * 0.95, 1)
    return None


def pmax_estimate(mmp_curve: dict[int, float]) -> Optional[float]:
    """Pmax: peak 1-second power."""
    return round(mmp_curve[1], 1) if 1 in mmp_curve and mmp_curve[1] > 0 else None


def frc_estimate(mmp_curve: dict[int, float], ftp_w: float) -> Optional[float]:
    """
    FRC (Functional Reserve Capacity) — anaerobic work capacity above FTP.
    Approximate: integrate (MMP[d] - ftp) * d for d in short durations.
    Simplified W' estimate from 2-parameter critical power model.
    """
    if ftp_w <= 0:
        return None
    # Use MMP[120] as proxy: FRC ≈ (MMP[120] - ftp) * 120
    if 120 in mmp_curve and mmp_curve[120] > ftp_w:
        return round((mmp_curve[120] - ftp_w) * 120, 0)
    return None
