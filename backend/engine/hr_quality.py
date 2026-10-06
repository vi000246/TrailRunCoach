"""
Per-run optical HR quality: the ONE place that cleans a run's HR and flags the
stretches that are not to be trusted (SP-265; docs/research/optical-hr-quality.md
§1.1, §1.3, §2 point 1, §3.2 單 1).

Everything is on a 1-s grid (`to_grid`): samples interpolated linearly, gaps
longer than GAP_S not bridged (NaN).

Cleaning (`clean`) — what every max-HR rule removes before it takes its own
peak (thresholds.estimate_mhr 5 s, threshold_confidence 60 / 120 s,
session_stimulus.hr_peak 60 s, racepower.maximal 120 s cumulative):

  range   < 30 or > 220 bpm
  spike   a rise ≥ 15 bpm within 3 s, dropped until HR is back within 15 bpm
          of the level before it — when that happens within 30 s.
  step_up a rise that does not come back within 30 s while moving (a `step`
          below, up): its plateau is dropped until HR is back within 15 bpm of
          the level before it (HR doesn't jump 15 bpm in 3 s while running; the
          old thresholds rule). After a stop, in the first 10 min, after a
          dropout, or without speed it is a level change and kept.
  lock    cadence lock (Bent 2020, npj Digit Med 3:18, 'signal crossover'):
          HR within 3 bpm of the cadence (spm) over a 60-s window AND
          following its changes (correlation ≥ 0.8 with ≥ 1.5 spm of cadence
          variation) — HR merely close to the cadence is common on easy runs
          and is not a lock (vo2max-session-detection.md §2.5)

Flags (`assess`) — marked only, never removed from the data, never
interpolated (SP-266 / SP-267 read them):

  dropout     HR missing ≥ 10 s while the watch kept recording
  flat        the same HR value ≥ 60 s while moving
  step        a moving level jump: a rise or fall ≥ 15 bpm within 3 s that is
              not back within 30 s, after minute 10, with no stop (speed
              < 0.5 m/s for ≥ 5 s) in the 60 s before and no dropout in the
              10 s before. A jump after a stop is left alone: HR really rises
              ~10+ bpm within seconds at the start of exercise (phase I,
              PMC8505324 — abstract only)
  high_start  ≥ 30 s of the first 10 min above (the 95th percentile after
              minute 10) + 10 bpm, runs ≥ 30 min only

Every number here is 推估 (the research doc's own definitions, §1.3) unless a
source is named; the range / spike / lock numbers are the ones the four max-HR
rules used before (they differed: 1 s vs 3 s, 220 vs 225 — unified here).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

# ---- cleaning ---------------------------------------------------------------
GAP_S = 5.0                 # 推估: a recording gap > 5 s is not bridged (both old rules)
HR_ABS_MIN = 30.0           # 推估: below = no contact
HR_ABS_MAX = 220.0          # 推估: above any usual adult maximum (Tanaka 2001: 208 − 0.7 × age, SD ≈ 10);
                            # thresholds used 220, threshold_confidence 225 — 220 kept
SPIKE_JUMP_BPM = 15.0       # 推估: a real effort climbs a few bpm per second at most
SPIKE_WINDOW_S = 3          # 推估: the rise may be spread over 3 s (threshold_confidence's rule; thresholds used 1 s)
SPIKE_MAX_S = 30            # 推估: not back within 30 s = a level change, not a spike
# cadence lock (Bent 2020 'signal crossover'; the numbers are 推估, backtest in
# vo2max-session-detection.md §2.5: HR close to the cadence alone matched 144 / 188 runs)
LOCK_TOL = 3.0              # bpm vs spm
LOCK_WIN = 60               # s
LOCK_CORR = 0.8
LOCK_CAD_SD = 1.5           # spm: the cadence has to move for the correlation to mean anything
LOCK_COVER = 0.8            # share of the window with both HR and cadence

# ---- flags (all 推估: optical-hr-quality.md §1.3) ----------------------------
DROPOUT_S = 10              # HR missing ≥ 10 s while recording
FLAT_S = 60                 # the same value ≥ 60 s …
FLAT_MOVING = 0.8           # … with ≥ 80 % of it moving
STOP_KMH = 1.8              # 0.5 m/s: stopped
STEP_JUMP_BPM = 15.0        # the spike rule's size …
STEP_WINDOW_S = 3           # … and window
STEP_HOLD_S = 30            # not back within 30 s
STEP_AFTER_S = 600          # only after minute 10 (the start has its own patterns)
STEP_STOP_LOOK_S = 60       # no stop in the 60 s before …
STEP_STOP_S = 5             # … (≥ 5 s below STOP_KMH)
STEP_DROP_LOOK_S = 10       # and no dropout in the 10 s before
HIGH_START_S = 600          # the first 10 min …
HIGH_START_MIN_RUN_S = 1800  # … of runs ≥ 30 min
HIGH_START_OVER = 10.0      # above the later 95th percentile + 10 bpm …
HIGH_START_NEED_S = 30      # … for ≥ 30 s
HIGH_START_PCT = 95

KINDS = ("range", "spike", "lock", "dropout", "flat", "step", "high_start")
CLEANED = ("range", "spike", "step_up", "lock")  # what `clean` removes ("step_up": an up-step's plateau)
SUSPECT = ("spike", "step", "flat", "lock")      # SP-266: the drift window's 「可疑心率秒數」


def _arr(x) -> np.ndarray:
    return np.asarray([np.nan if v is None else v for v in x], float)


def to_grid(t, x, g: Optional[np.ndarray] = None, max_gap: float = GAP_S,
            positive: bool = True) -> Optional[tuple]:
    """(grid, x on it): linear interpolation, NaN inside gaps > `max_gap`.
    Without `g` the grid runs from floor(first valid t) to floor(last valid t)
    by 1 s (the old clean_hr / peak_sustained_hr grid); with `g` NaN outside
    the samples. `positive`: values ≤ 0 are no data (HR, speed is not)."""
    if t is None or x is None:
        return None
    t, x = _arr(t), _arr(x)
    n = min(len(t), len(x))
    t, x = t[:n], x[:n]
    ok = np.isfinite(t) & np.isfinite(x)
    if positive:
        ok &= x > 0
    if ok.sum() < 2:
        return None
    tt, xx = t[ok], x[ok]
    order = np.argsort(tt, kind="stable")
    tt, xx = tt[order], xx[order]
    if g is None:
        g = np.arange(np.floor(tt[0]), np.floor(tt[-1]) + 1.0)
        y = np.interp(g, tt, xx)
    else:
        g = np.asarray(g, float)
        y = np.interp(g, tt, xx, left=np.nan, right=np.nan)
    j = np.clip(np.searchsorted(tt, g), 1, len(tt) - 1)
    gap = (tt[j] - tt[j - 1]) > max_gap
    y[gap & (g > tt[j - 1]) & (g < tt[j])] = np.nan
    return g, y


def spikes(y: np.ndarray) -> np.ndarray:
    """Samples of a spike (module doc): a rise ≥ SPIKE_JUMP_BPM within
    SPIKE_WINDOW_S seconds, until HR is back near the level before it (≤ SPIKE_MAX_S)."""
    y = np.asarray(y, float)
    n = len(y)
    out = np.zeros(n, dtype=bool)
    if n < 2:
        return out
    w = SPIKE_WINDOW_S
    pad = np.concatenate([np.full(w, np.nan), y])
    with np.errstate(all="ignore"):
        win = np.lib.stride_tricks.sliding_window_view(pad[:-1], w)
        fin = np.isfinite(win)
        prevmin = np.where(fin.any(axis=1), np.min(np.where(fin, win, np.inf), axis=1), np.nan)
        cand = np.where(np.isfinite(y) & np.isfinite(prevmin) & (y - prevmin >= SPIKE_JUMP_BPM))[0]
    yy = y.copy()
    done = -1
    for i in cand:
        if i <= done or not np.isfinite(yy[i]):
            continue
        prev = yy[max(0, i - w):i]
        prev = prev[np.isfinite(prev)]
        if not prev.size or yy[i] - prev.min() < SPIKE_JUMP_BPM:
            continue
        base = prev.min()
        k = i
        while k < n and k - i < SPIKE_MAX_S and (not np.isfinite(yy[k]) or yy[k] > base + SPIKE_JUMP_BPM):
            k += 1
        if k - i < SPIKE_MAX_S:                 # back near the level: a spike (else a level change, kept)
            out[i:k] = np.isfinite(yy[i:k])
            yy[i:k] = np.nan
        done = k
    return out


def cadence_lock(hr: Optional[np.ndarray], cad_spm: Optional[np.ndarray]) -> np.ndarray:
    """Samples where the optical HR follows the cadence (module doc)."""
    if hr is None or cad_spm is None:
        return np.zeros(0 if hr is None else len(hr), dtype=bool)
    n = len(hr)
    out = np.zeros(n, dtype=bool)
    step = LOCK_WIN // 2
    for a in range(0, max(1, n - LOCK_WIN + 1), step):
        h, c = hr[a:a + LOCK_WIN], cad_spm[a:a + LOCK_WIN]
        ok = np.isfinite(h) & np.isfinite(c)
        if ok.sum() < LOCK_WIN * LOCK_COVER:
            continue
        h, c = h[ok], c[ok]
        if abs(float(np.mean(h - c))) > LOCK_TOL or float(np.std(c)) < LOCK_CAD_SD or float(np.std(h)) < 1e-6:
            continue
        if float(np.corrcoef(h, c)[0, 1]) >= LOCK_CORR:
            out[a:a + LOCK_WIN] = True
    return out


def _cad_on(t, cadence_spm, g: np.ndarray) -> Optional[np.ndarray]:
    """Cadence (spm) on the HR grid; None when there is too little of it."""
    if cadence_spm is None:
        return None
    c = to_grid(t, cadence_spm, g, max_gap=GAP_S)
    if c is None or np.isfinite(c[1]).sum() < 2:
        return None
    return c[1]


@dataclass
class Quality:
    """A run's HR on the 1-s grid `g` (`hr`: gaps NaN, nothing removed), the
    per-sample masks of KINDS and the event runs [(a, b)] of each."""
    g: np.ndarray
    hr: np.ndarray
    masks: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)

    def cleaned(self) -> np.ndarray:
        """HR with range / spike / up-step / lock samples NaN (what `clean` returns)."""
        y = self.hr.copy()
        for k in CLEANED:
            m = self.masks.get(k)
            if m is not None:
                y[m] = np.nan
        return y


def runs_of(mask: np.ndarray) -> list[tuple[int, int]]:
    m = np.asarray(mask, dtype=bool)
    e = np.diff(np.concatenate([[0], m.astype(int), [0]]))
    return [(int(a), int(b)) for a, b in zip(np.where(e == 1)[0], np.where(e == -1)[0])]


def clean(t, hr, cadence_spm=None, grid: Optional[np.ndarray] = None,
          speed_kmh=None) -> Optional[tuple]:
    """(grid seconds, HR) with NaN where the HR is not to be trusted: gaps > 5 s,
    out of range, spikes, the plateau after a moving up-step (until HR is back
    within 15 bpm of the level before the jump) and cadence lock
    (`cadence_spm` in steps / min). None without HR. `grid`: put it on that
    1-s grid (NaN outside the samples). `speed_kmh`: needed for the up-steps
    (a jump after a stop is a real restart and is kept; without speed no jump
    is dropped)."""
    q = assess(t, hr, cadence_spm, speed_kmh, grid)
    if q is None:
        return None
    return q.g, q.cleaned()


def held_peak(y, hold_s: int) -> Optional[float]:
    """The highest HR held for ≥ `hold_s` seconds (max of the rolling minimum over
    fully valid windows)."""
    if y is None or len(y) < hold_s:
        return None
    win = np.lib.stride_tricks.sliding_window_view(np.asarray(y, float), hold_s)
    full = np.isfinite(win).all(axis=1)
    if not full.any():
        return None
    return float(np.min(win[full], axis=1).max())


# ---------------------------------------------------------------------------
# flags
# ---------------------------------------------------------------------------

def _recording(t, g: np.ndarray) -> np.ndarray:
    """Grid seconds the watch was recording (any sample within GAP_S)."""
    tt = _arr(t)
    tt = np.sort(tt[np.isfinite(tt)])
    if not tt.size:
        return np.zeros(len(g), dtype=bool)
    j = np.clip(np.searchsorted(tt, g), 1, max(1, len(tt) - 1))
    lo = tt[np.clip(j - 1, 0, len(tt) - 1)]
    hi = tt[np.clip(j, 0, len(tt) - 1)]
    near = np.minimum(np.abs(g - lo), np.abs(hi - g)) <= GAP_S
    return near & (g >= tt[0] - 0.5) & (g <= tt[-1] + 0.5)


def dropouts(h: np.ndarray, recording: np.ndarray) -> np.ndarray:
    miss = ~np.isfinite(h) & recording
    out = np.zeros(len(h), dtype=bool)
    for a, b in runs_of(miss):
        if b - a >= DROPOUT_S:
            out[a:b] = True
    return out


def flatline(h: np.ndarray, moving: np.ndarray) -> np.ndarray:
    n = len(h)
    same = np.zeros(n, dtype=bool)
    if n > 1:
        same[1:] = np.isfinite(h[1:]) & (h[1:] == h[:-1])
    out = np.zeros(n, dtype=bool)
    for a, b in runs_of(same):
        a0 = max(0, a - 1)                      # the first sample of the run of equal values
        if b - a0 >= FLAT_S and moving[a0:b].mean() >= FLAT_MOVING:
            out[a0:b] = True
    return out


def steps(h: np.ndarray, rel: np.ndarray, stopped: Optional[np.ndarray], drop: np.ndarray) -> tuple:
    """(mask, [(a, b)], up) of moving level jumps (module doc). `mask` (the
    flag) covers the jump and the 30 s it held; `up` (what `clean` drops) is
    an up-step's plateau, from the jump until HR is back within 15 bpm of the
    level before it (the end of the run when it never is) — HR doesn't jump
    15 bpm in 3 s while running, so the higher side is the error (the old
    thresholds.peak_sustained_hr rule, now only while moving)."""
    n = len(h)
    out = np.zeros(n, dtype=bool)
    up_m = np.zeros(n, dtype=bool)
    ev = []
    w = STEP_WINDOW_S
    if n <= w:
        return out, ev, up_m
    # candidates (vectorised): a ≥ 15 bpm change against the previous 3 s
    pad = np.concatenate([np.full(w, np.nan), h])
    with np.errstate(all="ignore"):
        win = np.lib.stride_tricks.sliding_window_view(pad[:-1], w)
        fin = np.isfinite(win)
        pmin = np.where(fin.any(axis=1), np.min(np.where(fin, win, np.inf), axis=1), np.nan)
        pmax = np.where(fin.any(axis=1), np.max(np.where(fin, win, -np.inf), axis=1), np.nan)
        cand = np.where(np.isfinite(h) & (rel >= STEP_AFTER_S) &
                        ((h - pmin >= STEP_JUMP_BPM) | (pmax - h >= STEP_JUMP_BPM)))[0]
    done = -1
    for i in cand:
        if i < done or i < w:
            continue
        prev = h[i - w:i]
        prev = prev[np.isfinite(prev)]
        if not prev.size or i + STEP_HOLD_S > n:
            continue
        nxt = h[i:i + STEP_HOLD_S]
        if not np.isfinite(nxt).any():
            continue
        up = h[i] - prev.min() >= STEP_JUMP_BPM and np.nanmin(nxt) > prev.min() + STEP_JUMP_BPM
        dn = prev.max() - h[i] >= STEP_JUMP_BPM and np.nanmax(nxt) < prev.max() - STEP_JUMP_BPM
        if not (up or dn):
            continue
        a0 = max(0, i - STEP_STOP_LOOK_S)
        if stopped is not None and stopped[a0:i].sum() >= STEP_STOP_S:
            continue
        if drop[max(0, i - STEP_DROP_LOOK_S):i].any():
            continue
        a, b = max(0, i - w), min(n, i + STEP_HOLD_S)
        out[a:b] = True
        ev.append((a, b))
        done = b
        if up:
            base = prev.min()
            back = np.where(np.isfinite(h[i:]) & (h[i:] <= base + STEP_JUMP_BPM))[0]
            k = i + int(back[0]) if back.size else n
            up_m[i:k] = True
            done = max(done, k)
    return out, ev, up_m


def high_start(h: np.ndarray, rel: np.ndarray, moving: np.ndarray) -> np.ndarray:
    out = np.zeros(len(h), dtype=bool)
    if not len(rel) or rel[-1] < HIGH_START_MIN_RUN_S:
        return out
    late = moving & (rel >= HIGH_START_S) & np.isfinite(h)
    if late.sum() < HIGH_START_S:
        return out
    lim = float(np.percentile(h[late], HIGH_START_PCT)) + HIGH_START_OVER
    early = moving & (rel < HIGH_START_S) & np.isfinite(h) & (h > lim)
    if early.sum() >= HIGH_START_NEED_S:
        out = early
    return out


def assess(t, hr, cadence_spm=None, speed_kmh=None, grid: Optional[np.ndarray] = None) -> Optional[Quality]:
    """Every mask of KINDS (and "step_up", the plateaus `clean` drops) on the
    run's 1-s grid (`grid` to share another one). `speed_kmh` decides moving /
    stopped; without it nothing is a step (a restart after a stop can't be told
    from a moving jump, so the data is kept) and everything counts as moving
    for `flat` / `high_start`. `cadence_spm`: steps / min."""
    c = to_grid(t, hr, grid)
    if c is None:
        return None
    g, h = c
    tt = _arr(t)
    rel = g - (float(np.nanmin(tt)) if np.isfinite(tt).any() else g[0])
    v = to_grid(t, speed_kmh, g, max_gap=30.0, positive=False) if speed_kmh is not None else None
    if v is not None:
        moving = np.nan_to_num(v[1]) > STOP_KMH
        stopped = ~moving
    else:
        moving = np.ones(len(g), dtype=bool)
        stopped = None
    y = h.copy()
    rng = np.isfinite(y) & ((y < HR_ABS_MIN) | (y > HR_ABS_MAX))
    y[rng] = np.nan
    sp = spikes(y)
    y[sp] = np.nan
    drop = dropouts(h, _recording(t, g))
    if stopped is not None:
        st, ev, up = steps(y, rel, stopped, drop)
    else:                                   # no speed: a restart can't be told from a moving jump — none flagged
        st, ev, up = np.zeros(len(g), dtype=bool), [], np.zeros(len(g), dtype=bool)
    y[up] = np.nan
    lock = np.zeros(len(g), dtype=bool)
    c1 = _cad_on(t, cadence_spm, g)
    if c1 is not None:
        lock = cadence_lock(y, c1) & np.isfinite(y)
    q = Quality(g=g, hr=h, masks={"range": rng, "spike": sp, "lock": lock, "dropout": drop,
                                  "flat": flatline(h, moving), "step": st, "step_up": up,
                                  "high_start": high_start(h, rel, moving)})
    for k in KINDS:
        q.events[k] = ev if k == "step" else runs_of(q.masks[k])
    return q


def summary(q: Optional[Quality], window: Optional[np.ndarray] = None) -> dict:
    """Seconds and events of each kind inside `window` (a grid mask; None = the
    whole run): {"<kind>_s", "<kind>_n", "suspect_s" (the union of SUSPECT),
    "window_s", "share"}. An event counts when it starts in the window."""
    out = {f"{k}_{x}": 0 for k in KINDS for x in ("s", "n")}
    out.update(suspect_s=0, window_s=0, share=0.0)
    if q is None:
        return out
    n = len(q.g)
    win = np.ones(n, dtype=bool) if window is None else np.asarray(window, dtype=bool)[:n]
    sus = np.zeros(n, dtype=bool)
    for k in KINDS:
        m = q.masks.get(k)
        if m is None:
            continue
        out[f"{k}_s"] = int((m & win).sum())
        out[f"{k}_n"] = sum(1 for a, _b in q.events.get(k, []) if a < n and win[a])
        if k in SUSPECT:
            sus |= m
    out["suspect_s"] = int((sus & win).sum())
    out["window_s"] = int(win.sum())
    out["share"] = out["suspect_s"] / out["window_s"] if out["window_s"] else 0.0
    return out
