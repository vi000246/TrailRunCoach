"""
WKO5 default power-duration model — reconstructed from WKO5.exe 5.0.587
(fitter @0x674c40, solver @0x6724c0, model @0x671e10, tte solve @0x674490,
phenotype @0x671fa0). Full spec: docs/wko5-internals/formulas.md §6.

STATUS: DISASSEMBLY-ONLY — WKO5 stores no PD results on disk, so this has not
yet been compared with WKO5's on-screen mFTP / FRC / Pmax / TTE. Verify
against the WKO5 PD chart before trusting absolute numbers.

    P(t) = FRC/t * (1 - e^(-t/tau1)) + (FTP + [t > TTE] * D * ln(t/TTE)) * (1 - e^(-t/tau2))
"""
from __future__ import annotations

import math
from typing import Optional

NAMES = ["FRC", "tau1", "FTP", "tau2", "TTE", "D"]
LOWER = [1.0, 1.0, 1.0, 15.0, 1000.0, -1000.0]
UPPER = [1e15, 1e15, 1e15, 45.0, 3600.0, 0.0]
MAX_DURATION = 29576.0


def model(p, t: float) -> float:
    FRC, t1, FTP, t2, TTE, D = p
    a = 1.0 - math.exp(-t / t2)
    b = 1.0 - math.exp(-t / t1)
    v = FRC / t * b + FTP * a
    if t > TTE:
        v += D * math.log(t / TTE) * a
    return v


def aerobic(p, t: float) -> float:
    FRC, t1, FTP, t2, TTE, D = p
    a = 1.0 - math.exp(-t / t2)
    return (FTP + (D * math.log(t / TTE) if t > TTE else 0.0)) * a


def anaerobic(p, t: float) -> float:
    FRC, t1 = p[0], p[1]
    return FRC / t * (1.0 - math.exp(-t / t1))


def _jac(p, t):
    FRC, t1, FTP, t2, TTE, D = p
    e1, e2 = math.exp(-t / t1), math.exp(-t / t2)
    a = 1.0 - e2
    over = t > TTE
    ln = math.log(t / TTE) if over else 0.0
    return [
        (1.0 - e1) / t,
        -FRC * e1 / (t1 * t1),
        a,
        -(FTP + (D * ln if over else 0.0)) * t * e2 / (t2 * t2),
        (-D * a / TTE) if over else 0.0,
        (ln * a) if over else 0.0,
    ]


def _sse(p, xs, ys):
    return sum((y - model(p, x)) ** 2 for x, y in zip(xs, ys) if x <= MAX_DURATION)


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-300:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def _gauss_newton(p, xs, ys, free, max_iter=500, tol=1e-5):
    """Bounded Gauss-Newton, unweighted SSE, step x0.125 while SSE increases."""
    p = list(p)
    idx = [i for i in range(6) if free[i]]
    cur = _sse(p, xs, ys)
    for _ in range(max_iter):
        JtJ = [[0.0] * len(idx) for _ in idx]
        Jtr = [0.0] * len(idx)
        for x, y in zip(xs, ys):
            if x > MAX_DURATION:
                continue
            r = y - model(p, x)
            j = _jac(p, x)
            for a_, ia in enumerate(idx):
                Jtr[a_] += j[ia] * r
                for b_, ib in enumerate(idx):
                    JtJ[a_][b_] += j[ia] * j[ib]
        d = _solve(JtJ, Jtr)
        if d is None:
            return None
        step = d
        for _k in range(60):
            q = list(p)
            for a_, ia in enumerate(idx):
                q[ia] = min(UPPER[ia], max(LOWER[ia], p[ia] + step[a_]))
            new = _sse(q, xs, ys)
            if new <= cur:
                break
            step = [s * 0.125 for s in step]
        delta = [q[ia] - p[ia] for ia in idx]
        p, cur = q, new
        if all(abs(v) < tol for v in delta):
            break
    return p, cur


def fit(points) -> Optional[dict]:
    """Fit the WKO5 model to a mean-max curve [(duration_s, power_w)] ascending.
    Returns {FRC (J), tau1, FTP, tau2, TTE, D, Pmax, SSE, tte, valid, vo2max,
    phenotype} or None when WKO5 would not fit (needs MMP out to >= 2400 s)."""
    pts = [(float(t), float(v)) for t, v in points if v is not None]
    while len(pts) > 1 and (pts[-1][0] > MAX_DURATION or not pts[-1][1] > 0):
        pts.pop()
    if len(pts) < 5 or pts[-1][0] < 2400:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    sel = [y for x, y in pts if 3 <= x <= 5 and y > 0]
    pmax = sum(sel) / len(sel) if sel else ys[0]
    sel = [y for x, y in pts if 900 <= x <= 1200 and y > 0]
    ftp0 = sum(sel) / len(sel) if sel else 250.0
    tau1 = 15.0
    thr = pmax - (pmax - ftp0) * 0.333
    for x, y in pts:
        if thr > y:
            tau1 = x / 3.0
            break
    p = [pmax * tau1, tau1, ftp0, 25.0, max(min(xs[-1] - 300.0, 3600.0), 1800.0), -50.0]
    n1 = sum(1 for x in xs if x <= min(xs[-1], 2400.0))
    r = _gauss_newton(p, xs[:n1], ys[:n1], [1, 1, 1, 0, 0, 0])
    if r is None:
        return None
    p, _ = r
    floor = 0.0
    for x, y in pts:
        if 1200 <= x <= 3600:
            floor = max(floor, y - anaerobic(p, x))
    p[2] = max(p[2], floor)
    r = _gauss_newton(p, xs, ys, [0, 0, 0, 1, 1, 1])
    if r is None:
        return None
    p, s = r
    for lim in (1800.0, 3600.0):
        if (lim == 1800.0 and p[4] < 1800.0) or (lim == 3600.0 and p[4] > 3600.0):
            p[4] = lim
            r = _gauss_newton(p, xs, ys, [0, 0, 0, 1, 0, 1])
            if r is None:
                return None
            p, s = r
    res = dict(zip(NAMES, p))
    res["Pmax"] = pmax
    res["SSE"] = s
    res["tte"] = tte_solve(p, p[2])
    res["valid"] = (0 < pmax < 3000 and 0 < p[0] <= 80000 and 0 < p[1] < 90 and 0 < p[2] < 600
                    and 0 < p[3] < 300 and 1800 <= p[4] <= 3600)
    res["vo2max"] = (p[0] / 589.0 + p[2]) / 84.5 + 0.656
    res["phenotype"] = phenotype(pmax, p[0], p[2])
    res["params"] = p
    return res


def tte_solve(p, target: float) -> float:
    """Duration where the model falls to `target` (log10 bisection)."""
    t = p[4] * 2.0
    lo = 1.0
    for _ in range(10):
        if model(p, t) - target <= 0:
            break
        lo = t
        t += t
    hi = t
    for _ in range(25):
        mid = 10 ** ((math.log10(lo) + math.log10(hi)) * 0.5)
        if model(p, mid) - target > 0:
            lo = mid
        else:
            hi = mid
        if int(hi) - int(lo) <= 0:
            break
    return hi


def phenotype(pmax, frc, ftp) -> Optional[str]:
    if not ftp or not pmax:
        return None
    x, y, z = pmax / ftp, frc / pmax, frc / ftp
    scores = {
        "All-rounder": 60.973 * x + 1.404 * y - 6.653 * x * x - 0.081 * z - 0.03 * y * y - 150.73,
        "Pursuiter": 11.454 * x + 1.047 * y - 1.635 * x * x + 0.022 * z - 0.018 * y * y - 39.772,
        "Sprinter": 81.122 * x - 1.181 * y - 8.521 * x * x + 0.758 * z - 0.091 * y * y - 213.943,
        "Time-Trialer": 19.044 * x + 1.253 * y - 2.786 * x * x - 0.043 * z - 0.034 * y * y - 43.392,
    }
    return max(scores, key=scores.get)
