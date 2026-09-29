# WKO5 expression functions — exact semantics (WKO5.exe 5.0.587)

Scope: the general-purpose expression functions. The power-duration model (pdcurve, ftp,
frc, pmax, tte, vo2max, stamina, phenotype, pdprofile, ftpcurve, frccurve, target*, TIS)
is covered in `formulas.md`. Per-workout metrics (hrTSS, NGP, VAM, EF, …) are in
`workout-metrics.md`.

**Status legend**
- **VERIFIED**: reproduced bit-exact against a value WKO5 itself computed and stored.
- **DISASSEMBLY**: read from the machine code; not yet compared against a WKO5 number.
- **DOC**: taken from the WKO5 Expression Reference only; not yet checked in the code.

Reference implementations live in the scratchpad (`scratchpad\re\fn_*.py`), not in the repo.

---

## 0. Function registry

The table of expression functions is built at `0x6c5e00…0x6c9150` with this code
pattern:

    push <name>; mov ecx,<slot>; call ctor; mov [ebp-0x20],<impl>; … mov [slot+0x18],<argc>

The implementation address and argument count **follow** each name. The earlier
`registry.json` paired every name with the previous entry's impl and was off by one.
The corrected table (182 entries) is `scratchpad\re\fn_registry.json`. Examples:

| name/argc | impl | name/argc | impl |
|---|---|---|---|
| avg/1 | 0x6cd330 | meanmax/1 | 0x6f2040 |
| avg/(groupby) | 0x6cdae0 | meanmax/2 | 0x6f2bf0 |
| bin/2 | 0x6ce3c0 | min/1, max/1 | 0x6f47e0, 0x6f08d0 |
| count/1 | 0x6d2910 | max/2 (elementwise) | 0x6f0c90 |
| cumsum/1 | 0x6d45e0 | rev/1 | 0x6fd850 |
| delta/1 | 0x6d7510 | round/1, round/2 | 0x6fe4f0, 0x6feaa0 |
| ewma/2 | 0x6db2a0 | shift/2 | 0x700160 |
| filter/3 | 0x6dcf10 | slr/slrb/slrm | 0x701a00 / 0x702240 / 0x702740 |
| gaussian/2 | 0x6e20a0 | startofweek/1 | 0x705910 |
| greatest/2 | 0x6e4230 | stddev | 0x706170 |
| isef/2 | 0x6e7590 | sum/1, sum/2 | 0x707610, 0x7079c0 |
| levelfrom/2, levelto/2 | 0x6eaa00, 0x6ec250 | tl/2, tl/3 | 0x70c0c0, 0x70cac0 |
| li/2 | 0x6ed370 | weekval/1, week/1 | 0x710980, 0x710280 |
| lookup/2 | 0x6efea0 | xx/1, yx/1 | 0x710e90, 0x711d30 |

`athleterange` and `workoutrange` are **not** in this registry. They are special forms
handled by the evaluator itself.

Shared helpers:
- `0x60b840` fetches a named argument.
- `0x5b6c50` gets value *i*; `0x5b6de0` tests whether value *i* is invalid.
- `0x5b7ce0` gets a weight/x value; `0x5b7d90` and `0x5b7e20` append to the output set.
- `0x4a59a0` is `almost_equal(a, b, ulp=10)`, i.e. `|a−b| < |a+b|·ε·10 or |a−b| < DBL_MIN`.
- `0x4a5b30` is the **tolerant floor** (see §3).
- `0x4a5a60` rounds to int: `floor(x+0.5)` for x > 0, else 0.
- CRT: `0x7ab1be` = pow, `0x7ab1dc` = floor.

Invalid ("na") values are stored as `DBL_MAX` (1.7976931348623157e308).

---

## 1. meanmax — VERIFIED

`meanmax(x)` returns the mean-maximal curve; `meanmax(x, d)` returns one point of it.

**VERIFIED** against WKO5's `Cache5` `meanmax(power)` cache: **49,188 / 49,188 curve
points on 359 / 359 power workouts**, bit-exact. Reference: `fn_meanmax_exact.py`.

### 1.1 Duration grid (`0x4bda20`)

```
grid = { int(floor(1.05**i + 0.5)) for i = 0,1,2,…  while d <= T }
     ∪ { 5,10,20,30,40,50,60,120,180,240,300,360,420,480,540,600,900,1200,1800,2400,2700,3600 : ≤ T }
```

T is the last `elapsedtime`. The grid is returned as a sorted set of ints (1, 2, 3, …,
26, 28, 29, 30, 32, 34, 35, …). **VERIFIED** on 359/359 cached grids.

`0x4bdde0` is the same builder with an extra ×100 constant, most likely the distance-axis
grid. DISASSEMBLY only.

### 1.2 Core (`0x5c0230`)

Inputs per sample i:
- `dx[i]`: the x increment; `deltatime` on the time axis
- `w[i]`: the weight; `deltatime`
- `v[i]`: the value; na allowed

The first sample's Δt is measured from t = 0.

For each grid duration d, a **continuous window of exactly d** slides across the
samples, and the samples at the window ends are **pro-rated fractionally**. This is not
a 1 s resample and not an integer rolling mean.

```
phase 1: for each d, add samples from index 0 until acc(dx) >= d; j = last added
         (add w to totw; if v valid: add w to validw, w*v to swv; add dx to acc if dx valid & ≠0)
phase 2: for each start sample k:
   for each d (stop if the smallest d's window reached the end):
     skip samples at the end pointer j with dx ≈ 0 (almost_equal)
     f_end = (acc - d)/dx[j];  f_start = 0
     loop:
        a = (v[k] valid ? w[k] : 0) * f_start;   b = (v[j] valid ? w[j] : 0) * f_end
        validw' = validw - a - b
        div = validw' if validw'/totw > 0.98 else totw        # totw is NOT trimmed
        avg = (swv - v[k]·w[k]·f_start - v[j]·w[j]·f_end) / div   (0 if div <= 0)
        best[d] = max(best[d], avg)
        if f_end == 0: add sample j+1 to the window
        f_end = 0; f_start = (acc - d)/dx[k]  (1.0 if dx[k] <= 0)
        repeat while dx[k] > 0 and d > acc - dx[k] and j < n
     remove sample k (acc -= dx[k]; totw -= w[k]; if v[k] valid: swv -= v·w, validw -= w)
```

Each start sample k is therefore evaluated in two alignments:
1. The window **starts** at k's start; the end sample is trimmed.
2. The window **ends** at the end of each sample j added afterwards; k is trimmed.

Consequences:
- Sparse data works: a single 6 s sample of 153 W is atomic at 1 s, so the 1 s value can
  be *lower* than the 6 s value. The curve is not necessarily monotone.
- Invalid samples inside the window count as time with value 0, unless valid coverage is
  above 98% of the untrimmed weight; then the divisor is the valid weight.

The HHMMSS / KM / MI / DATE branches in the wrapper `0x6f15c0` choose the x axis. The same
core is used with dx = time, distance or date increments; only the time axis is VERIFIED.

---

## 2. Averages, sums, counts

### avg — DISASSEMBLY (0x6cd330), consistent with the VERIFIED range averages

- **Time axis** (x units HHMMSS with an `elapsedtime` channel): time-weighted.
  - For each sample, `dt = t[i] - prev`, where `prev` advances on **every** sample, valid
    or not. `prev` starts at the range's begin time (else `t[0]`).
  - Valid values with `dt >= 0` contribute: `sumw += dt`, `sumwv += dt·v`.
  - Result = `sumwv/sumw`, or na if `sumw = 0`.
  - This is the same rule as the verified `wko4_file.range_average`.
- **Other sets** (athlete-level numbers, lists): the plain mean of valid values.
- `avg()` does **not** drop zeros. The "cadence ignores zeros" rule belongs only to
  WKO5's stored range statistics. Charts write `avg(nozero(cadence))` explicitly.

### sum / min / max — DISASSEMBLY (0x707610, 0x6f47e0, 0x6f08d0)

- These reduce over **valid** values, skipping `0x5b6de0` na values.
- The min/max helpers (`0x5ba860` / `0x5baba0`) use `minsd`/`maxsd` starting from ±DBL_MAX.
  An empty set gives na.
- `max(a, b)` / `min(a, b)` with two arguments (0x6f0c90) are elementwise (they use the
  `y1`/`y2`/`link` names).

### count — DOC + DISASSEMBLY (0x6d2910)

Counts **valid, non-zero** values: `count({3,6,0,9,na,12}) = 4`. The implementation skips
values that are almost-equal (10 ULP) to 0.

### sum / count / avg with a groupby period — DOC (0x7079c0, 0x6cdae0, 0x6d2d30)

`sum(numbers, "day"|"week"|"month"|"year")` buckets values by calendar period. Weeks
follow the first-day-of-week preference (§6).

---

## 3. bin — DISASSEMBLY (0x6ce3c0)

A single implementation handles every overload.

- **`bin(values, binsize)`**
  - `binsize` must be a single non-zero number.
  - `min` and `max` are taken over valid values.
  - `base = floor(min/binsize)·binsize`
  - `nbins = tolfloor((max − base)/binsize) + 1`
  - Each valid v goes to bin `tolfloor((v − base)/binsize)`.
  - The output is pairs `x = base + i·binsize`, `y = accumulated weight`.
- **Weight**: the sample's `deltatime` in seconds, when a deltatime series of the same
  length is available (workout-level channels); otherwise 1, i.e. a count.
- **`bin(values, {c1,…,cn})`**
  - Each valid v goes to the first bin whose cut is greater than v.
  - Values ≥ `cn` go to the extra top bin, giving n+1 bins.
  - Bins are "up to but not equal", as documented.
- **`bin(values, "levelsname")`**: uses the level table in §4. A value falls in level i
  when `from_i <= v < to_i`.
- **`tolfloor(x)`** (0x4a5b30):
  ```
  f = floor(x)
  return f+1 if |x−(f+1)| < 1e−15·|x+f+1| (or < DBL_MIN) else f
  ```
  This protects against 29.999999 → 29.

---

## 4. Training levels — DISASSEMBLY (levels builder = vtable slot 9 of each `PK*Levels` class)

### Name → class map (0x650c00)

| name | class |
|---|---|
| `ilevels`, `cogganoptimized` | PKCogganOptimizedPowerLevels |
| `classicpower`, `cogganclassic` | PKCogganClassicPowerLevels |
| `classichr`, `cogganhr` | PKCogganHeartRateLevels |
| `frielhr` | PKFrielHeartRateLevels |
| `usachr` | PKUSACHeartRateLevels |
| `bcfhr` | PKBCFHeartRateLevels |
| `frielpace` | PKFrielPaceLevels |
| `pzipace` | PKPZIPaceLevels |
| `ctspower` | PKCTSPowerLevels |
| `rstpower` | PKRSTPowerLevels |

### How levels are built

- Each level is created through `0x648ae0(name, from = T·lo, to = T·hi, lo, hi)`.
- The first level starts at 0 and the last one is open-ended (DBL_MAX).
- **T** is the threshold setting for the level system's own sport, as of the workout date
  (`0x507ff0`).
- Index 0 is the lowest level: `levelname("classicpower", 1) = "Endurance"`.

### Level tables (fractions of T)

**classicpower**, T = Bike `ftp` (0x64c960)

| # | name | lo | hi |
|---|---|---|---|
| 0 | Active Recovery | 0 | .56 |
| 1 | Endurance | .56 | .76 |
| 2 | Tempo | .76 | .91 |
| 3 | Threshold | .91 | 1.06 |
| 4 | VO2max | 1.06 | 1.21 |
| 5 | Anaerobic Capacity | 1.21 | ∞ |

**classichr**, T = Bike `thr` (0x64d500)

| # | name | lo | hi |
|---|---|---|---|
| 0 | Active Recovery | 0 | .69 |
| 1 | Endurance | .69 | .84 |
| 2 | Tempo | .84 | .95 |
| 3 | Threshold | .95 | 1.06 |
| 4 | VO2max | 1.06 | ∞ |

**frielhr**, T = `thr` (0x64dc90)

| # | name | lo | hi |
|---|---|---|---|
| 0 | Recovery | 0 | .82 |
| 1 | Aerobic | .82 | .89 |
| 2 | Tempo | .89 | .94 |
| 3 | Sub-Threshold | .94 | 1.00 |
| 4 | Super-Threshold | 1.00 | 1.03 |
| 5 | Aerobic Capacity | 1.03 | 1.06 |
| 6 | Anaerobic Capacity | 1.06 | ∞ |

**usachr**, T = `mhr` (max HR) (0x64e600). The names come from a format string and were
not extracted.

| # | lo | hi |
|---|---|---|
| 0 | 0 | .66 |
| 1 | .66 | .73 |
| 2 | .73 | .84 |
| 3 | .84 | .91 |
| 4 | .91 | ∞ |

**bcfhr**, T = `mhr` (0x64eda0)

| # | name | lo | hi |
|---|---|---|---|
| 0 | Level 1 | 0 | .65 |
| 1 | Level 2 | .65 | .75 |
| 2 | Level 3 | .75 | .82 |
| 3 | Level 4 | .82 | .89 |
| 4 | Level 5 | .89 | .94 |
| 5 | Level 6 | .94 | ∞ |

**frielpace**, T = Run `tpace` (min/km) (0x64f6b0). Fractions of threshold pace (1.29 =
29% slower):

| name | bounds |
|---|---|
| Zone 1 | ≥ 1.29 |
| Zone 2 | 1.14–1.29 |
| Zone 3 | 1.06–1.14 |
| Zone 4 | 1.00–1.06 |
| Zone 5a | .97–1.00 |
| Zone 5b | .90–.97 |
| Zone 5c | < .90 |

**pzipace**, T = `tpace` (0x650070)

| name | bounds |
|---|---|
| Gray | ≥ 1.35 |
| L Aero | 1.22–1.35 |
| M Aero | 1.11–1.22 |
| H Aero | 1.05–1.11 |
| Gray II | 1.00–1.05 |
| Thresh | .97–1.00 |
| Gray III | .91–.97 |
| VO2max | .89–.91 |
| Gray IV | .86–.89 |
| Speed | < .86 |

**Open question for the pace systems:** they are built fastest-first, with the fastest
level open-ended. Whether the values are compared as pace or as speed (1/pace) still has
to be checked against WKO5's Athlete Details level table.

**ilevels**: computed from the PD model. The builder at 0x649de0 uses
`meanmax(bikepower)` over the previous 90 days (`prev90`), then FTP / FRC / Pmax with the
constants 0.5, 0.632 and 1.05. See `formulas.md`.

**ctspower / rstpower**: these share one builder (0x64d1e0), which takes its values from
data, not constants. Not decoded yet.

### levelfrom / levelto / levelname / levelcount (0x6eaa00, 0x6ec250, …)

- `levelfrom(sys, i)` returns `from_i` and `levelto(sys, i)` returns `to_i` of the table
  above, for the current workout date.
- The `meanmax(power)` overloads return iLevels bounds.

---

## 5. Smoothing and series transforms

### ewma(x, c) — DISASSEMBLY (0x6db2a0)

```
k = 1/c                      # error "Constant must be non-zero." if c ≈ 0
v = na
for each sample:
    if x valid:  v = x if v is na else v + (x − v)·k
    emit v (if v valid) at the sample's x
```

- It is per **sample**, not time-weighted.
- An invalid x holds v; leading na values emit nothing.
- On HHMMSS and DATE axes only the x-channel of the output is rebuilt
  (`0x60c000` / `0x60c3e0`).

### shift(x, k) — DISASSEMBLY (0x700160)

- For k > 0, `out[i] = x[i−k]` (a **lag**); leading positions are na.
- Exception: position `k−1` receives the value **preceding the range**, if the set
  carries one (e.g. the day before an athleterange).
- Negative k leads.
- The Reference's line "deltatime = shift(elapsedtime,1) − elapsedtime" contradicts this
  code. `shift(ctl, 1)` is yesterday's CTL, which is what TSB uses.

### delta(x) — DOC (0x6d7510)

`delta({3,5,7,11,13}) = {na, 2, 2, 4, 2}`. The first element is na.

### cumsum(x) — DOC (0x6d45e0)

Running total of valid values.

### rev, sort, sortd, sortx, sortxd, xx, yx — DOC

- `rev`: reverse order.
- `sort` / `sortd`: sort by value, ascending / descending.
- `sortx` / `sortxd`: sort pairs by x, ascending / descending.
- `xx(pairs)` / `yx(pairs)`: return the x values / y values as a plain list.

### filter(x, kernel, sides), isef(factor, length), gaussian(sigma, length) — DISASSEMBLY

- `filter` (0x6dcf10) convolves x with the kernel list. `sides` must be 1 (causal) or 2
  (centred); the constant 2.0 is used for the centred case.
- `isef` (0x6e7590) builds an infinite-symmetric-exponential kernel.
  - length must be 1…1000.
  - Constants 0.005 and 1.0 appear.
- `gaussian` (0x6e20a0) builds a Gaussian kernel.
  - Constants 2π, 0.9 and −1.0 appear.
  - σ must be 1…100 and length 1…1000.

The exact kernel normalisation has not been decoded yet.

### li(pairs, x) — DISASSEMBLY (0x6ed370)

1. Find the bracketing points (x0,y0) and (x1,y1).
2. Compute `y0·(1−f) + y1·f`, where `f = (x−x0)/(x1−x0)`.
3. Clamp the result to [min(y0,y1), max(y0,y1)].
4. If x almost-equals an existing x (10 ULP), return that point's y.

### lookup(pairs, x) — DOC (0x6efea0)

- Returns the y of the **last point with X ≤ x**, a step lookup.
- X must be numeric (error "X value must be numeric.").
- Example: `lookup(weight, enddate)` gives the weight in effect at enddate.

---

## 6. Dates

### startofweek(d) — DISASSEMBLY (0x705910)

- Starting from d, step back one day at a time until
  `weekday(day) == ctx.firstDayOfWeek`.
- The week start is read from `[ctx+0xd8]`, a **user preference**.
- **Differs from the evaluator**, which hard-codes Monday. Ask the user which first day of
  week their WKO5 uses.

### Day numbers (0x4ae680)

- Epoch 1901-01-01 (day 0), computed in 1461-day blocks.
- Same epoch as `backend/files/wko5_athlete.EPOCH`.

### week / weekval / year / yearval / month / monthval / startofmonth / startofyear — DOC (0x710280, 0x710980, …)

- `weekval` produces values with `WEEK` units.
- `week` uses the constants 1.0 and 7.0.
- Exact numbering has not been decoded yet.

---

## 7. Statistics

### stddev(x) — DOC

Sample standard deviation, dividing by n−1: `stddev(1..10) = 3.0276504`.
`pstddev` / `pvariance` are the population versions.

### slrm, slrb, slrrsq, slr — DISASSEMBLY (0x702740, 0x702240, 0x702f40, 0x701a00)

- Use points where both x and y are valid.
- x is the set's own x: `elapsedtime` seconds at workout level, the day number at athlete
  level, or the sample **index** when the set has no x.
- `m = (nΣxy − ΣxΣy)/(nΣx² − (Σx)²)`
- `b = (Σy − mΣx)/n`
- `slr(x)` returns a **two-point line** from (xmin, m·xmin+b) to (xmax, m·xmax+b), with
  xmin/xmax taken over valid points.
- Time units (HHMMSS / SECONDS …) only affect the unit label of the slope.

### greatest(values, n), least(values, n) — DOC (0x6e4230, 0x6e8890)

- Return the n greatest (or least) values as a set, keeping each value's x (date).
- n must be ≥ 0 (error "Count of elements must be 0 or greater.").
- `sortd(greatest(tss, 5))` gives the 5 biggest TSS.
- In the PMC chart, `greatest(meanmax(power,300),5)` marks the 5 best 5-minute efforts.

### unique, round, trunc, noinvalid, nozero, clamp — DOC

- `round(x, places)`: places must be in −7…7.
- `nozero(x)`: turns 0 into na.
- `noinvalid(x)`: drops na values.

---

## 8. Differences vs `backend/engine/wko5expr/evaluator.py`

| # | Area | Evaluator today | WKO5 | Impact |
|---|---|---|---|---|
| 1 | `_meanmax` | Integer k-window cumsum over samples, NaN→0 | Continuous d-second window with fractional end samples, 98% valid rule, 1.05 grid (§1) | Wrong on any non-1 s data, gaps or invalid samples. MMP, PD-curve and TIS charts are all affected. Port `fn_meanmax_exact.py`. |
| 2 | `meanmax(x)` curve | Not supported | Grid §1.1 | Needed by pdcurve / levels / MMP charts |
| 3 | `count` | Counts valid values | Counts valid **non-zero** values | `count(heartrate)`-style series |
| 4 | `startofweek`, `weekval` | Hard-coded Monday; weekval = startofweek | Uses the user's first-day-of-week preference | Weekly charts, `@lastWeekEP`, `本周…` gauges |
| 5 | `greatest` | Returns its input unchanged | The n greatest values (with dates) | PMC "MMP 5 Min (P5)" series |
| 6 | `avg` over athlete-level sets | Plain mean | Plain mean | OK |
| 7 | `avg` over samples | Weights = `diff(t, prepend=0)` | Same, but `prev` starts at the range begin | OK for whole workouts; differs for sub-ranges starting mid-workout |
| 8 | `ewma` | Same recurrence; k = 1 when c = 0 | Error when c ≈ 0 | Negligible |
| 9 | `round` | `np.round` (half-to-even) | Not decoded; places limited to −7…7 | Possible ±1 in the last digit |
| 10 | `bin`, `lookup`, `li`, `levelfrom/levelto/levelname`, `stddev`, `slr*`, `cumsum`, `delta`, `rev`, `sortx`, `xx`, `yx`, `filter`, `isef`, `gaussian`, `string`, `in`, groupby `sum/count/avg` | Not implemented | §3–§7 | Zones, 間歇, 有氧/無氧, Zone & Variation dashboards |
| 11 | `_rolling_time_avg` | Still present | Replaced by `_rapower` (NP) | Dead code |
| 12 | `shift` | Lag | Lag, plus the one pre-range value at position k−1 | First day of a TSB series |
| 13 | `rgrade` | Rise/run over 10 m, PROVISIONAL | Not decoded here (workout-metrics agent) | Grade-coloured charts |

---

## 9. Open items

1. `bin` with levels on pace channels: check whether values are compared as pace or
   speed.
2. Exact numbering and edge cases of `week` / `weekval` / `year`.
3. Kernel normalisation of `filter` / `isef` / `gaussian`.
4. The `ctspower` / `rstpower` tables.
5. The rounding mode of `round`.
6. Groupby buckets for `sum/count/avg(x, period)`.
7. `in`, `string` and the `(expr)"unit"` casts. The casts appear to be unit tags only;
   the parser already ignores them.
