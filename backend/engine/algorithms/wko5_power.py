"""
WKO5 `_rapower` (30 s rolling power on a 1 s grid), Normalized Power and
tssduration — reconstructed from WKO5.exe 5.0.587 (@0x65b391) and VERIFIED
bit-exact against WKO5's stored NP (field 4219) and tssduration (field 4248)
on 359/359 power workouts (2026-09-29).

Samples: each sample i holds its value over (t[i-1], t[i]] (first from
`start`). For each output second tcur = 1, 2, 3, ... the trailing 30 s window
is filled from the sample covering tcur backwards; if the valid coverage is
>= 27 s the average divides by valid coverage, else by total coverage.
Seconds with (almost) no valid coverage are skipped.
"""
from __future__ import annotations

from typing import Optional, Sequence

WINDOW = 30.0


def rapower(t: Sequence[float], v: Sequence[Optional[float]], start: float = 0.0,
            window: float = WINDOW) -> list[tuple[float, float]]:
    """[(tcur, 30 s average)] for each emitted second."""
    n = len(t)
    dts = [t[i] - (t[i - 1] if i > 0 else start) for i in range(n)]
    out: list[tuple[float, float]] = []
    tcur = 0.0
    edi = 0
    while True:
        tcur = round(tcur + 1.0)
        while edi < n and not (tcur <= t[edi]):
            edi += 1
        if edi >= n:
            break
        remaining, covered, validcov, s = window, 0.0, 0.0, 0.0
        i = edi
        while i >= 0:
            w = min(remaining, min(tcur - (t[i] - dts[i]), dts[i]))
            covered += w
            if v[i] is not None:
                validcov += w
                s += w * v[i]
            remaining -= w
            i -= 1
            if not remaining > 0.0:
                break
        if validcov < 0.001:
            continue
        div = validcov if validcov >= 27.0 else covered
        out.append((tcur, s / div))
    return out


def normalized_power(t: Sequence[float], v: Sequence[Optional[float]]) -> tuple[Optional[float], float]:
    """(NP, tssduration seconds)."""
    ra = rapower(t, v)
    if not ra:
        return None, 0.0
    vals4 = [a ** 4 for _, a in ra]
    return (sum(vals4) / len(vals4)) ** 0.25, float(len(ra))


def power_tss(np_: Optional[float], tssduration: float, ftp: Optional[float]) -> Optional[float]:
    """TSS = NP^2 * tssduration / (FTP^2 * 36)  (= hours * IF^2 * 100)."""
    if np_ is None or not ftp or tssduration <= 0:
        return None
    return np_ * np_ * tssduration / (ftp * ftp * 36.0)
