# WKO5 per-workout metric algorithms

WKO5 5.0.587 (Windows). These are reverse-engineered from `WKO5.exe` and verified against the "Entire Workout" range fields that WKO5 stores in the athlete index (`<Name>.wko5athlete` → 3201/3202/4202). The sample data is 1098 workouts from one real athlete account. Thresholds on this PC are defaults: runthr/bikethr 160, runtpace 4.66, run/bike FTP 250.

The reference implementations live outside the repo, in the session scratchpad `re\` folder: `metrics_hr.py`, `metrics_pace.py`, `metrics_elev.py`, `metrics_time.py` and `metrics_summary.py`. Port them into the repo with tests.

Status legend:
- **VERIFIED n/N**: reproduces the stored value to ≤1e-9 relative (or exactly) on n of N workouts that have the field.
- **DISASSEMBLY-ONLY**: read from the code but not reproduced.

## Shared conventions

- **Channel deltas** (`0x5b7ce0`): `delta(x, i) = x[i] − x[i−1]`. For i = 0 the channel *base value* is used; that is wko4 channel field 119, which is 0 for `elapsedtime` and usually invalid otherwise. The result is invalid if either operand is invalid. `dt_i = delta(elapsedtime, i)`.
- **Almost-equal** (`0x4a59a0(a, b, 10)`): `|a−b| < |a+b|·DBL_EPSILON·10`, or `|a−b| < DBL_MIN`. Against 0 this is effectively *exactly zero*. It is **not** an absolute tolerance: float residue left in a running sum counts as non-zero.
- **Invalid** = `DBL_MAX` (1.7976931348623157e308), shown as `None`/NaN in our readers.
- **Channel storage quantizes.** A value written into a WKO5 channel is rounded to 1/scale, half away from zero, where scale is wko4 packed-block field 114 (e.g. 10 for elevation). This matters for `_elevation`.
- **Range average** is WKO5's time-weighted average (`backend/files/wko4_file.range_average`). Each sample holds over (t[i−1], t[i]], clipped to the range; invalid samples are skipped, and cadence also skips zeros.
- **Halves** (pwhr/pahr/4250): (0, L/2] and (L/2, L], with L = range length (field 4206).

## hrTSS (4235) and hrIF (4236): VERIFIED 1030/1030

Source: `calculateHeartrateTss` @0x657300. The zone table is at 0x86cdd0…0x86c7b0.

```
for each sample i with valid heart rate hr (invalid samples skipped entirely):
    dt = t[i] - t[i-1]                        (first: t[0] - 0)
    k  = first level with hr >= factor_k * LTHR
    hrTSS += rate_k * dt ;  T += dt
levels (factor of LTHR -> TSS per hour; rate = TSS/h / 3600):
    1.06→140  1.03→120  1.00→100  0.94→80  0.89→70  0.855→60  0.82→50
    0.82·2/3→40  0.82/3→30  0→20
hrTSS invalid if > 5000 or < 0
hrIF = sqrt(0.6 · hrTSS / (T/60)^1.025)        (inverse of the rTSS formula)
```

LTHR is the sport-group threshold HR setting on the workout day (runthr / bikethr / …). Every setting here is 160, so the sport → setting mapping is not independently discriminated.

## `_ragpace` channel, NGP (4230), rTSS duration (4249): VERIFIED

Verification counts:
- NGP 568/582 at 1e-9, and 582/582 within 2.2e-6.
- 4249: 578/582. Two of the misses are workouts where WKO5 did not store 4249.

Source: `_ragpace` @0x65b8e0 and `calculateRunPaceMetrics` @0x6565e0 (Run sport family only).

`_ragpace` is produced on its **own 1 s grid** from `elapsedtime`, `_elevation` and `speed`:

```
tprev = 0, eprev = invalid; two windows W_grade, W_pace (deque of (w, v))
window push(w, v): total += w; if v valid: wv += w, sv += w·v
                   while total - front.w >= 30: pop front (undo its sums)
window mean: sv / wv, invalid if wv is exactly 0
for each input sample i (t_i, E_i = _elevation[i], S_i = speed[i]):
    cnt = 0; tg = min(t_i, tprev + 1)
    slope = 0; eg = E_i
    if eprev and E_i valid: slope = (E_i - eprev)/(t_i - tprev); eg = eprev + slope
    loop while t_i >= tg:
        dt = tg - tprev; tprev = tg
        dist = grade = invalid
        if S_i and E_i valid:
            eprev = eg; dist = S_i·dt/3.6 (m); rise = slope
            if dist >= rise and dist²-rise² >= 0:
                horiz = sqrt(dist² - rise²)
                if horiz >= 0.01 and horiz > rise: grade = rise/horiz
        else:
            eprev = invalid; if cnt >= 30: tg = t_i
        W_grade.push(dt, grade); g = W_grade.mean()
        val = invalid
        if dist and g valid: v = dist·60/dt (m/min); adj = (0.19v + 0.9vg)/0.19; val = adj^4
        W_pace.push(dt, val); m = |W_pace.mean()|
        emit (tg, m^0.25 if m valid and m != 0 else invalid)
        if eg valid and slope != 0:          # advance interpolated elevation, never past E_i
            if (slope > 0 and eg+slope >= E_i) or (slope < 0 and not E_i < eg+slope): slope = E_i - eg
            eg += slope
        if tg ≈ t_i: break
        tg = min(tg + 1, t_i); if t_i - tg < 0.001: tg = t_i
        cnt += 1
    tprev = tg
```

The derived metrics are computed on the `_ragpace` grid, with dt from its own grid times and the first dt taken from 0:
- **NGP (min/km)** = 1000 / (Σ dt·v / Σ dt) over valid `_ragpace` values.
- **rTSS duration (4249)** = Σ dt over valid `_ragpace` values.
- rTSS itself is `(4249/60)^1.025 · IF² / 60 · 100`, with IF = runtpace / NGP. This was verified by the TSS agent.

The remaining ≤2e-6 differences are floating-point accumulation in the window sums, which cancel after pops.

## `_elevation` channel: VERIFIED 628/628 bit-exact

Source: @0x65c670, dispatched from 0x65d3c0. The derivation-cache key `_elevationelevation` equals the elevation channel hash on 628/628, so it is computed from the stored `elevation`.

```
requires n > 2 and at least one valid elevation
X = copy of elevation (writes quantized to the channel scale, 0.1 m)
for tau in (6.0, 1.5):                        # tau *= 0.25 after each pass
    F[0] = first valid X;  F[i] = X[i] invalid ? F[i-1] : (F[i-1] + a·X[i])/(1+a),  a = (t[i]-t[i-1])/tau
    B[n-1] = last valid X; B[i] = X[i] invalid ? B[i+1] : (B[i+1] + a·X[i])/(1+a),  a = (t[i+1]-t[i])/tau
    X[i] = quant((F[i] + B[i]) / 2)
d0 = firstvalid(E) - firstvalid(X);  d1 = lastvalid(E) - lastvalid(X)
_elevation[i] = E[i] invalid ? invalid : quant(X[i] + d0 + (d1-d0)·i/(n-1))
```

## Altitude metrics: VERIFIED

Source: `calculateAltitudeMetrics` @0x654ee0. Computed from `_elevation` (se) and `elapseddistance`.

| field | name | definition | status |
|---|---|---|---|
| 4227 | elevationchange | net = lastvalid(se) − firstvalid(se) | 628/628 |
| 4223 | climbing | up = Σ positive steps between consecutive valid values; `max(net, up)` if net > 0 | 628/628 |
| 4225 | descending | down = Σ \|negative steps\|; `max(−net, down)` if net < 0 | 628/628 |
| 4224 | vam | `round(climbing / L · 3600)` (whole m/h), L = field 4206 | 628/628 |
| 4226 | grade | `net / √(D² − net²)`, D = (lastvalid(dist) − firstvalid(dist))·1000, only if D ≠ 0 and \|D\| > \|net\| | 620/620 |

## Time and distance metrics: VERIFIED

Source: `calculateTimeAndDistanceMetrics` @0x654370.

| field | name | definition | status |
|---|---|---|---|
| 4213 | movingduration | Σ dt where speed valid and speed > threshold. Threshold by sport family: **Run and Walk** 1.609344498 km/h (1 mph), Bike 3.218688996 (2 mph), otherwise 0. Without a speed channel: Σ dt where distance changed. | 789/790 (swim miss) |
| 4214 | pedalingduration | Σ dt where cadence valid and ≠ 0 | 680/680 |
| 4215 | duration | Σ dt where any data channel is valid. This includes the `@` developer channels and excludes `elapsedtime` / `elapseddistance`. | 1075/1075 |
| 4216 | begindistance | d[0] − delta(d, 0), i.e. the channel base value | 780/780 |
| 4217 | distance | d[last] − begindistance (km) | 780/780 |
| 4206 | range length | Entire Workout: last elapsed time | (range record) |

## Power and HR metrics: VERIFIED

| field | name | definition | status |
|---|---|---|---|
| 4219 | np | see `docs/wko5-internals/formulas.md` (`_rapower`) | 359/359 (TSS agent) |
| 4248 | tssduration | emitted `_rapower` seconds | 359/359 (TSS agent) |
| 4218 | work (J) | Σ power·dt | 359/359 |
| 4222 | vi | NP / avg(power), zeros included | 359/359 |
| 4247 | ef | NP / avg(heartrate) | 359/359 |
| 4228 | pwhr | e_k = avg(power, half k)/avg(hr, half k); `(e1−e2)/e1`, unrounded | 358/359 |
| 4229 | pahr | e_k = avg(speed, half k)·1000/60 / avg(hr, half k); `round((e1−e2)/e1, 4)` | 547/548 |
| 4250 | (unnamed) | mean `_ragpace` (m/min)·60/1000 / avg(speed, **first half**). WKO5 reuses the first-half speed; this is reproduced as-is. | 534/548 |
| 4251 | (unnamed, pace EF) | `round(mean _ragpace / avg(heartrate), 2)` | 561/561 |

The pwhr/pahr misses are one workout whose stored value is exactly 1.0. The 4250 misses are the same files that carry the NGP float noise.

## Remaining unknowns

- Swim: the movingduration special case (stroke data) and swim TSS were not investigated. There is only 1 swim workout.
- The hrTSS LTHR sport mapping for non-run/bike groups is inferred, because all thresholds are equal on this PC.
- NGP float noise at ≤2.2e-6 relative. It likely comes from sum/pop ordering or the CRT `pow` in the window; it is not structural.
