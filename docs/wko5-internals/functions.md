# WKO5 expression functions — exact semantics (WKO5.exe 5.0.587)

Scope: the general-purpose expression functions. The power-duration model (pdcurve, ftp,
frc, pmax, tte, vo2max, stamina, phenotype, pdprofile, ftpcurve, frccurve, target*, TIS)
is covered in `formulas.md`. Per-workout metrics (hrTSS, NGP, VAM, EF, …) are in
`workout-metrics.md`.

**Status legend**
- **VERIFIED**: reproduced bit-exact against a value WKO5 itself computed and stored.
- **DISASSEMBLY**: read from the machine code; not yet compared against a WKO5 number.
- **DOC**: taken from the WKO5 Expression Reference only; not yet checked in the code.
- **PROVISIONAL**: neither source pins it down; the evaluator implements the most plausible
  reading and says so in a code comment. Needs WKO5.exe or a WKO5 on-screen number.

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

Evaluator: the period name, any per-workout key (`startofweek(date)`, `startofmonth(date)`,
`trunc(date)`, …) and `weekval` / `monthval` / `yearval` keys all work for
`sum/avg/count/max/min(values, groupby)`. Period-number keys (weekval …) are plotted at the
period's first day. `max(values, groupby)` / `min(values, groupby)` group when both
arguments are dated or listed sets or the second is a period name; two sample series, or a
set and a number, stay elementwise (the disassembly says max/2 is elementwise, the Reference
says groupby — PROVISIONAL split). Two plain sets (e.g. `max({hr…}, {pace…})`) give one
(key, aggregate) point per key.

### length — DOC

All values, zeros and na included: `length({3,6,0,9,na,12}) = 6`.

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

Evaluator output shapes: `bin(v, size)` → an (x, y) set, x = bin start, empty bins between
min and max included; `bin(v, {cuts})` → a list of n+1 weights; cut items that are pairs
`("name", cut)` label the bins, and an na cut (e.g. `levelto(mm, 8)`, the open-ended Pmax
level) is skipped as a cut but keeps its label, so 9 labelled cuts give 9 bins;
`bin(v, "levels")` → one (level name, weight) pair per level. At the athlete level the bins
of every workout in the range are added up.

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
| 0 | Recovery | 0 | .85 |
| 1 | Aerobic | .85 | .90 |
| 2 | Tempo | .90 | .95 |
| 3 | Sub-Threshold | .95 | 1.00 |
| 4 | Super-Threshold | 1.00 | 1.03 |
| 5 | Aerobic Capacity | 1.03 | 1.06 |
| 6 | Anaerobic Capacity | 1.06 | ∞ |

VERIFIED against WKO5's own "Friel Heart Rate Zones for Running" table at THR 160
(2026-09-29): 1 0–134, 2 136–142, 3 144–150, 4 152–158, 5a 160–163, 5b 165–170, 5c 170+.
WKO5 shows Friel's whole-percent bands (≤84, 85–89, 90–94, 95–99, 100–102, 103–106,
>106 %) rounded to bpm, hence the 1–2 bpm gaps. An earlier reading of this table
(.82/.89/.94) was wrong. The "Classic Heart Rate Zones" table (AR 0–110, E 110–134,
TE 134–152, TH 152–170, VM 170+) matches the classichr fractions above.

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

**ctspower / rstpower** — DISASSEMBLY: **no levels** in 5.0.587. They share the builder
0x64d1e0 (vtable slot 9 of `PKCTSPowerLevels` 0x8526cc and `PKRSTPowerLevels` 0x852878).
It reads the `power` threshold into `[this+0x28]`, destroys every 0x80-byte level in the
vector and sets end = begin. Nothing is added. The constructors (0x64d180, 0x64d0c0 /
0x64d230 = clone) only set an empty vector, and slot 11 (the level count) is 0x457000,
`xor eax,eax; ret`, while classicpower's is 0x64c810, `mov eax,6`. Only the display names
("CTS Power Levels", "RST Sport Power Levels", slot 10) exist. They are placeholder systems.
Evaluator: empty tables, so `levelcount(...) = 0` and every `levelname/from/to` is na / "".

### levelfrom / levelto / levelname / levelcount (0x6eaa00, 0x6ec250, …)

- `levelfrom(sys, i)` returns `from_i` and `levelto(sys, i)` returns `to_i` of the table
  above, for the current workout date.
- The `meanmax(power)` overloads return iLevels bounds.

Evaluator: iLevels indices 0–8 = Recovery, Endurance, Tempo, Sweetspot, FTP, FRC/FTP, FRC,
Pmax/FRC, Pmax (the order the user's "Time in iLevels" / "Power Histogram with iLevels"
charts use), bounds from formulas.md §6.9. The open-ended top of any table is na (it is
DBL_MAX = invalid in WKO5). T = the threshold of the workout's sport on its date; at the
athlete level the Run setting at the range end (PROVISIONAL). `usachr` level names are
PROVISIONAL ("Level 1"…); `ctspower` / `rstpower` raise "unsupported".
The Friel HR zones chart (`round({0,0.85,…}*@thr)` + `string()`) reproduces WKO5's own
table exactly at THR 160 (0–134, 136–142, 144–150, 152–158, 160–163, 165–170, 170+).

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

`delta({3,5,7,11,13}) = {na, 2, 2, 4, 2}`. The first element is na. Evaluator: x[i] − x[i−1]
in the set's natural order (samples, workouts by date, days, list items); na if either is na.

### cumsum(x) — DOC (0x6d45e0)

Running total of valid values: `cumsum({1..10}) = {1,3,6,…,55}`. Evaluator: an na position
stays na and does not reset the total (PROVISIONAL).

### rev, sort, sortd, sortx, sortxd, xx, yx — DOC

- `rev`: reverse order.
- `sort` / `sortd`: sort by value, ascending / descending (na last).
- `sortx` / `sortxd`: sort pairs by x, ascending / descending.
- `xx(pairs)`: (X,Y) → (X,X). `yx(pairs)`: (X,Y) → (Y,X), so `li(yx(pdcurve(mm)), watts)`
  is the duration at which the model reaches `watts`.

Evaluator: dated and timed sets keep each value's x, so `rev`/`sort` of them change nothing
that is plotted (WKO5 shows the order only in reports). `xx(curve)` now returns the curve
(x, x) and `yx(curve)` the swapped curve; before they returned the bare x / y arrays.

### filter(x, kernel, sides), isef(factor, length), gaussian(sigma, length) — DISASSEMBLY

Fully decoded. No WKO5 cache holds a result of any of the three (checked every
`Cache5\*.wko5cache`; `Views\Charts.wko5cache` holds chart configs only), so none is
VERIFIED. The evaluator follows the machine code, including the order of summation.

**filter(x, kernel, sides)** (0x6dcf10; loop 0x6dd4bb…0x6dd638)

```
K = count(kernel); n = count(x)
off = (K−1)/2 (C integer division)  if sides == 2.0      # 0x86c168
      K−1                           otherwise           # sides 3 is causal too
for i in 0…n−1:
    acc = na; wsum = 0
    for m in 0…K−1:
        s = i − off + m
        if 0 <= s < n and x[s] valid:
            wsum = k[m] + wsum
            acc  = k[m]·x[s]           if acc is na
                   k[m]·x[s] + acc     otherwise
    if not almost_equal(wsum, 0):  acc = acc / wsum      # 0x4a59a0, 10 ULP
    out[i] = acc                                         # na if no valid sample
```

- The kernel is used **as listed**. k[0] is the oldest sample, and in the causal case
  k[K−1] is the current one. It is not mirrored.
- With sides = 2 and an even K the window leans forward: `filter(rgrade,{1,10},2)`
  (the user's grade bands) is (1·x[i] + 10·x[i+1]) / 11.
- The edges and na samples are handled by renormalising over the weights that were
  actually used.
- An na sample still gets an output if its window holds a valid one.
- A weight sum ≈ 0 returns the undivided sum.
- It is per sample, not time-weighted. The output keeps x's x-values and units.
- `sides` is only checked for being one value ("Expected 1 or 2 sides in third argument.").
- String data is returned unchanged (`0x5b6ea0`: type 2).

**isef(factor, length)** (0x6e7590)

- factor < 0 or ≥ 1 raises the error "Smoothing factor must be greater than or equal to
  0.005 and less than 1.0."
- factor ≤ 0.005 returns the identity kernel `{1}` (`0x60a780(1.0)`).
- length must be 1…1000 ("Length must be between 1 and 1000."). It is rounded with
  `floor(L+0.5)` (0x4a5a60) and made odd: N = L+1 if L is even. It defaults to 1.
- The kernel is `w_i = exp(|i − N/2| · ln(1 − factor))`, i = 0…N−1, i.e. (1−f)^|j| for
  j = −N/2…N/2.
- It is then divided by its sum, accumulated in order as `s = w + s`.
- It is symmetric and centred, so it is meant for sides = 2.

**gaussian(sigma, length)** (0x6e20a0)

- sigma < 0 or > 100 raises "Sigma must be between 1 and 100.".
- sigma ≤ 1, or a sigma that is not a single value, returns `{1}`.
- length is handled as in isef (1…1000, rounded, made odd).
- c = 1 / (sqrt(2π)·σ), with 2π = 6.283185307179586. `w_i = exp(((−1.0·j)·j) / ((σ+σ)·σ)) · c`
  for j = i − N/2.
- It is then divided by the in-order sum. The Gaussian's own normalisation c cancels out,
  apart from rounding.
- The 0.9 in the earlier notes belongs to the neighbouring function at 0x6e25e0, not to
  gaussian.

In the user's VO2max charts, `filter(power, isef(1/L, L), 2)` with odd L (121, 31, 9) is a
centred exponential smoother with decay (1−1/L)^|j| over ±L/2 samples.

### dfrc(power, frc, ftp) — DISASSEMBLY (0x6d7ae0; loop 0x6d82f0…0x6d8489)

The FRC balance in **kJ** per sample (output units `KJ`, x `HHMMSS`). This is WKO5's W′bal.

Arguments:
- power must carry WATTS units.
- frc must be a single valid value in KJ (or NONE units). frc·1000 must be in (0, 50000] J.
- ftp must be a single valid value in WATTS (or NONE units), 10…600.
- Otherwise it raises "Invalid FRC." / "Invalid FRC value." / "Invalid FTP value." / "Expecting power in watts.".

```
D = 0 (depletion, J)   R = 0 (recovered, J)   t = 0 (time since the effort, s)
for each sample i:
    dt = x[i] − x[i−1]            # 0x5b7ce0; x[−1] = the range begin
    if dt is na or dt < 0.001: skip (no output point)
    p = power[i]; na → 0
    if p > ftp:
        left = D − R;  t = 0;  R = 0
        D = (p − ftp)·dt + (left if left > 0 else 0)
    else:
        t += dt
        R = (1 − e^(t/−300))·(D·0.7) + (1 − e^(t/−25))·(D·0.3)
    out[i] = ((frc·1000 − D) + R) / 1000
```

- Recovery is **bi-exponential**: 30 % of the depleted energy returns with τ = 25 s and
  70 % with τ = 300 s. The clock restarts when an effort ends.
- The next effort starts from what is still missing (D − R).
- The first sample starts at full FRC.
- Nothing clamps the output. A hard effort can take it below 0.
- In the user's chart it is called as `dfrc(runpower, athleterange(date-89, date,
  frc(meanmax(runpower))), athleterange(…, ftp(…)))`. The frc argument is in kJ, as
  `frc()` reports it.
- Evaluator: `_dfrc`, dt = the `deltatime` channel. It is **not VERIFIED**, because no
  WKO5 cache holds a dfrc result.
- CRT `exp` (`_libm_sse2_exp_precise`) and Python's `math.exp` may differ in the last
  ULP.

### li(pairs, x) — DISASSEMBLY (0x6ed370)

1. Find the bracketing points (x0,y0) and (x1,y1).
2. Compute `y0·(1−f) + y1·f`, where `f = (x−x0)/(x1−x0)`.
3. Clamp the result to [min(y0,y1), max(y0,y1)].
4. If x almost-equals an existing x (10 ULP), return that point's y.

### lookup(pairs, x) — DOC (0x6efea0)

- Returns the y of the **last point with X ≤ x**, a step lookup.
- X must be numeric (error "X value must be numeric.").
- Example: `lookup(weight, enddate)` gives the weight in effect at enddate.

Evaluator: a dated setting (`runthr`, `runftp`, `weight`, …) as the first argument is read
as its (date, value) history, i.e. the setting in effect on x (before the first entry, the
earliest value, as for settings everywhere). For other sets, lookup before the first x is na.
`li` / `lookup` accept any set (curve, workouts by date, samples by elapsed time) and a
number, list or set of x values.

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

Evaluator (DOC — each reproduces the Reference's worked example exactly):
- `weekval(d) = (d + 8) / 7` → weekval(2015-11-08) = 5993.857, trunc → Monday 2015-11-02.
- `monthval(d) = (year−1901)·12 + (month−1) + (day−1)/days_in_month` → 1378.0667 for 2015-11-03.
- `yearval(d) = (year−1901) + (day_of_year−1)/days_in_year` → 114.852 for 2015-11-08.
- `startofmonth`, `startofyear`, `week` (ISO-8601), `month`, `year`, `day`, `dayofweek`
  (days after Monday), `date(y, m, d)`.
- `date(x)` of a week/month value does not convert units (the evaluator has no unit types).

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

Evaluator: `slrm/slrb/slrrsq` of sample data at the athlete level give one number per
workout; `slr` of dated values is drawn as a daily line from xmin to xmax (same line),
of samples as one value per sample, of other sets as the two-point curve. The slope of
dated values is per day.

### greatest(values, n), least(values, n), first, last — DOC (0x6e4230, 0x6e8890)

- Return the n greatest (or least) values as a set, keeping each value's x (date).
- n must be ≥ 0 (error "Count of elements must be 0 or greater.").
- `sortd(greatest(tss, 5))` gives the 5 biggest TSS.
- In the PMC chart, `greatest(meanmax(power,300),5)` marks the 5 best 5-minute efforts.
- Lists keep their original order: `greatest({5,7,3,2,4,9,8},3) = {7,9,8}`,
  `least(…,3) = {3,2,4}`, `first(…,3) = {5,7,3}`, `last(…,3) = {4,9,8}`.
- Evaluator: athlete-level sets are limited to the chart range first.

### unique, round, trunc, noinvalid, nozero, clamp, sign, string — DOC

- `round(x, places)`: places must be in −7…7, and counts powers of ten **left** of the point:
  `round(pi,-1) = 3.1`, `round(pi,-3) = 3.142`, `round(1234.567,2) = 1200`.
  - **DISASSEMBLY** (round/1 0x6fe4f0, round/2 0x6feaa0, core 0x4a5bb0):
    - m = 10^−places is taken from a literal table {1e7 … 1, 0.1 … 1e-7} (0x4a5be8).
    - x > 0 → `floor(x·m + 0.5) / m`, otherwise `ceil(x·m − 0.5) / m`. Halves round
      **away from zero**, and the scaling is x·0.01 then /0.01, not x/100.
    - A result within DBL_MIN of 0 becomes +0, so `round(-0.4) = +0`.
    - na stays na.
    - places is itself rounded half away from zero (0x4a59f0) before the −7…7 check
      ("Places must be between -7 and 7.").
  - No cached value isolates it: in the Cache5 entry using `round(10*rngp)/10` WKO5's
    x-values are min/**mi** (the athlete's pace unit; the evaluator's `rngp` is min/km, see
    §7b `rngp`) and never an exact half after the unit change, and the TIS caches
    `round(@score)` never hit an exact half.
- `nozero(x)`: turns 0 into na.
- `noinvalid(x)`: drops na values: `noinvalid({13,0,7,na,0,21}) = {13,0,7,0,21}`. Sample
  series keep their length in the evaluator (na = no point) so they stay aligned with time.
- `clamp`: the Reference documents `clamp(min, max, values)`, but WKO5's own built-in TIS and
  stamina expressions call `clamp(values, min, max)`. The evaluator uses the built-in order
  and falls back to the Reference order when the first reading has min > max.
- `sign`: 1 / −1 / 0.
- `string(values)`: numbers → strings for `+` concatenation (`string(39)+"x"+string(23)`).
  Formatting PROVISIONAL: integers without decimals, else up to 10 significant digits; na → "".

## 7b. Other functions and identifiers the evaluator now supports

| name | semantics | status |
|---|---|---|
| `na`, `e`, `g`, `pi` | constants: na, 2.718281828…, 9.80665 m/s², 3.14159… | DISASSEMBLY: identifier resolver 0x71ad70 compares the name `g` (0x71afe3) and loads the double 9.80665 @0x86c220 (0x71b013, unit METERSPERSECOND); `e` (0x71b07a) loads @0x86c180 = 2.718281828459045 |
| `a in b` | 1 where a's value occurs in b's values, else 0 | PROVISIONAL (not in the Reference) |
| `begintime`, `endtime` | selected range of a workout in elapsed s (0 and the last elapsedtime; the workoutrange window inside workoutrange) | DOC; the Cache5 entry `workoutrange(begintime,if(sport="run",endtime,0),…rngp…)` matches (see `rngp`). Summary-level resolver 0x4f9402 / 0x4f9442 reads them from a range object ([edi+0x270] vcall +0x20) — not decoded further |
| `title` | athlete index 3213 (the workout type, "Trail Running", unless renamed; e.g. "Mountaineering"); "" → falls back to the sport type | DISASSEMBLY: summary resolver 0x4f93e6 reads `title` at +0x144 (0x4f95da), which the index serializer 0x4fb517 writes as 3213 (0x4fb520). In a .wko4 the title is info 4020; the workout getter 0x553760 falls back to 4006, then 4005 (sport group) |
| `description` / `desc` | athlete index 3206 (gunzipped if it starts 1f 8b); .wko4 info 4003 | DISASSEMBLY: +0x15c via 0x4f4990 → 0x4d7400 (gunzip); written by 0x4fb55b through 0x4f8c70 (gzip when shorter). Data: workout 2025-06-21 has 3206 = 4003 = "雪主單攻" with title "Mountaineering", which is what the Season View chart `if(has(title,"Mountaineering") and climbing >1500, description)` labels |
| `notes` | athlete index 3207 (gunzipped); in a .wko4 a list 4700 {4701 …} that the workout-level resolver joins as `X + "\n" + text + "\n"` per note (0x7201a1…0x72028f) | DISASSEMBLY (+0x174 via 0x4f49c0); empty for every workout in the data set, so not checked against data |
| `code` | athlete index 3210 | DISASSEMBLY (+0x1a4, 0x4f95c2); no chart uses it |
| `sftp` | the `bikeftp` setting, for every sport | DISASSEMBLY: settings resolver 0x71dba0 rewrites `sftp` to lower("Bike") + "ftp" (0x71dbe9…0x71dc20) |
| `tisaerobic`, `tisanaerobic`, `stamina` | the built-in expression strings of formulas.md §6.10 / §6.12, evaluated in their own variable scope; TIS once per workout (na without a power channel) | VERIFIED strings, model DISASSEMBLY |
| `sport(x).athleterange(a, b, e)` | athleterange limited to sport group x | from the TIS built-ins |
| `workoutrange(a, b, e)` at athlete level | e once per workout: a number per workout, or the (x, y) sets of all workouts pooled | PROVISIONAL |
| `ftp/frc/pmax/vo2max/tte(mm, lookback)` | one value per day of the range: model fitted to the envelope of the previous `lookback` days; fits failing the validity gate are na | DOC; every day fitted |
| `ftpcurve(mm)`, `frccurve(mm)` | the model's aerobic / anaerobic component over the curve's durations (their sum is `pdcurve`) | DOC + model DISASSEMBLY |
| `targetname/targetduration/targetpower(i, mm)` | formulas.md §6.9b; i may be a list (`{5:0:-1}`) | DISASSEMBLY |
| `s`, `dmax`, `tau1`, `tau2` | model parameters (formulas.md §6.6) | DISASSEMBLY |
| `ecpower` | WKO5's channel expression (below): P = ewma(power,25) / f(h), f(h) = −6.74e−9 h² − 2.74e−5 h + 0.997 (h = elevation, m); if P > sftp: (P − sftp)·f(h) + sftp. Bike and Run workouts only | DISASSEMBLY (string @0x860d18, pushed at 0x724bcc); not VERIFIED — no cached value |
| `fmax` | "Maximum Force", N: `(metric(weight)*g)*(pi/2)*((60*1000/cadence/2-stancetime)/stancetime+1)` with stancetime in **ms** (Morin et al. 2005). Run only | DISASSEMBLY (string @0x860e88, 0x7251a1; unit N, LBSFORCE for English units); not VERIFIED — no cached value |
| `kleg` | leg stiffness, kN/m (string at 0x725058, stancetime ms, height cm, leg length 0.53·height). Run only | DISASSEMBLY; no chart uses it |
| `rngp` | `1000/_ragpace` (0x724e46; Run only): min/km at each sample's time | VERIFIED: Cache5 `workoutrange(begintime,if(sport="run",endtime,0),@Pace:=round(10*rngp)/10,…,xx(avg(@HR,if(@Pace<=20"min/km",@Pace))))` — the evaluator's pace bins × 1.609344 equal WKO5's bins exactly in 15/15 road runs (WKO5 hands rngp to expressions in the athlete's pace unit, min/mi here; units PACEKM/PACEMI chosen at 0x724f4b). WKO5 adds one more (na-key) bin per workout (§8 #21) |

**Derived channels (0x7242ac).** WKO5 creates these channels per workout from expression strings, each
for the listed sport groups only: `gprleft` `power*(1-balance)/effectivenessleft`, `gprright`
`power*balance/effectivenessright`, `gpaleft` `gprleft-power*(1-balance)`, `gparight`
`gprright-power*balance`, `kileft` / `kiright` `effectiveness…/smoothness…` (Bike); `ecpower`
(Bike, Run); `rgrade` `filter(metric(_elevation-shift(_elevation,1)) / sqrt((metric(elapseddistance-shift(elapsedDistance,1))*1000)^2-metric(_elevation-shift(_elevation,1))^2), gaussian(3,17), 2)`
(all sports, PERCENT); `rngp` `1000/_ragpace`, `kleg`, `fmax` (Run). **Units:** WKO5 evaluates
identifiers in display units — `stancetime` in ms, `verticaloscillation` in cm, `height` in cm,
pace in the athlete's pace unit (the Palladino report's own note: "built on the basis of English
units … Kleg reports high"; the fmax/kleg strings divide stancetime by 1000 and height by 100, and
the user's charts divide `metric(verticaloscillation)` by 100 to get metres). **The evaluator
follows this for every expression** (`EXPR_UNIT_SCALE` / `SETTING_UNIT_SCALE`): the .wko4 files
store stancetime in s and verticaloscillation in m, the height setting is in m, and they are
scaled on read to ms / cm / cm. The channel strings are therefore parsed verbatim (the earlier
`CHANNEL_EXPR_UNITS` name rewriting is gone), and the MILLISECONDS / CM axes need no display
scale. Pace stays min/km (see `rngp`).

**rgrade, step by step** (`Evaluator._rgrade`; `RGRADE_EXPR` is the string above):
- dElev = `_elevation[i] − _elevation[i−1]` in m. `_elevation` is WKO5's smoothed elevation
  channel; when a file has none (FIT files imported by this app) it is recomputed from
  `elevation` with WKO5's smoothing (`algorithms/wko5_elevation.py`, VERIFIED bit-exact).
- dRun = `(elapseddistance[i] − elapseddistance[i−1])·1000` in m (elapseddistance is km).
- g = dElev / sqrt(dRun² − dElev²): rise over the **horizontal** run, not over distance.
- g is na for the first sample (`shift(x,1)` has no predecessor), where either channel is na,
  and where dRun < |dElev| (sqrt of a negative: e.g. standing, dRun = 0, while the barometer
  drifts). |dElev| = dRun would be x/0, which is na (the evaluator's x/0 rule, PROVISIONAL).
- **One guard the string does not have** (PROVISIONAL): a horizontal run below 1 cm is na.
  Distance is stored in 1 cm steps, so a smaller run can only be the float residue of
  dRun = |dElev|. On workout 1094, 0.5 m against 0.500000000000167 m gave g = 1.2·10⁶ and
  would have made the trail run's average grade 37 000 %.
- rgrade = `filter(g, gaussian(3,17), 2)`: a centred 17-sample Gaussian (σ = 3 samples, ±8),
  renormalised over the valid samples, so single na samples are bridged by their neighbours.
  A sample is na only when its whole ±8 window is.
- It is per sample, not per metre or per second, and not VERIFIED (no cached rgrade result).
  A file's own `rgrade` channel would win.

Channels the athlete's recent .wko4 files contain (last 60 workouts, count): elapsedtime 60,
heartrate 60, @activity_type 60, speed 59, elapseddistance 59, elevation / latitude / longitude /
_elevation 58, power 55, cadence 55, @vertical_ratio 55, stancetime 55, verticaloscillation 55,
@effort_pace 54, @step_length 54, @form_power 53, @impact_loading_rate 53,
@leg_spring_stiffness 53, @air_power 52. None records `fmax`; WKO5 derives it.
| `ln`, `log`, `floor`, `frac`, `length`, `unique`, `first`, `last`, `least` | as named | DOC |
| `{lo:hi:step}` in a list | expands to lo, lo+step, … hi | DOC (a set bound is reduced to its max/min, PROVISIONAL) |
| `(xs, ys)` with a list or curve side | an (x, y) set; string x values give labelled pairs | DOC |
| `(, set)` | drops the x values; a single value is a plain number | DOC ("( ,Y) with no X value") |

---

## 8. Differences vs `backend/engine/wko5expr/evaluator.py`

| # | Area | Evaluator today | WKO5 | Impact |
|---|---|---|---|---|
| 1 | `_meanmax` | Integer k-window cumsum over samples, NaN→0 | Continuous d-second window with fractional end samples, 98% valid rule, 1.05 grid (§1) | Wrong on any non-1 s data, gaps or invalid samples. MMP, PD-curve and TIS charts are all affected. Port `fn_meanmax_exact.py`. |
| 2 | `meanmax(x)` curve | Not supported | Grid §1.1 | Needed by pdcurve / levels / MMP charts |
| 3 | `count` | **Fixed**: valid non-zero values (grouped counts too) | Counts valid **non-zero** values | OK |
| 4 | `startofweek` | Hard-coded Monday | Uses the user's first-day-of-week preference | Weekly charts, `@lastWeekEP`, `本周…` gauges |
| 4b | `weekval` / `monthval` / `yearval` | **Fixed**: fractional period numbers (§6, Reference examples exact); `date()` and groupby map them back to the period's first day | Period values with WEEK / MONTH / YEAR units | OK; `date()` conversion read off the expression, not from units |
| 5 | `greatest` / `least` / `first` / `last` | **Implemented** (§7) | The n greatest values (with dates) | OK |
| 6 | `avg` over athlete-level sets | Plain mean | Plain mean | OK |
| 7 | `avg` over samples | Weights = `diff(t, prepend=0)` | Same, but `prev` starts at the range begin | OK for whole workouts; differs for sub-ranges starting mid-workout |
| 8 | `ewma` | Same recurrence; k = 1 when c = 0 | Error when c ≈ 0 | Negligible |
| 9 | `round` | **Fixed**: x·m then /m with WKO5's m table, halves away from zero, −0 → +0, places rounded (§7) | DISASSEMBLY 0x4a5bb0 | OK |
| 10 | `bin`, `lookup`, `li`, `levelfrom/levelto/levelname/levelcount`, `stddev` family, `slr*`, `cumsum`, `delta`, `rev`, `sort*`, `xx`, `yx`, `string`, `in`, groupby `sum/count/avg/max/min` | **Implemented** (§2–§7b) | §3–§7 | OK |
| 10b | `filter`, `isef`, `gaussian` | **Fixed**: WKO5's loop and kernels (§5), in-order sums | DISASSEMBLY 0x6dcf10 / 0x6e7590 / 0x6e20a0 | OK; not VERIFIED (no cached result). VO2max marking charts still limited by #19 |
| 11 | `_rolling_time_avg` | Still present | Replaced by `_rapower` (NP) | Dead code |
| 12 | `shift` | Lag (lists too) | Lag, plus the one pre-range value at position k−1 | First day of a TSB series |
| 13 | `rgrade` | **Fixed**: WKO5's channel string (§7b "rgrade, step by step"), plus a PROVISIONAL na for horizontal runs < 1 cm (float residue); `_elevation` recomputed from `elevation` when a file lacks it | Channel string (0x724cfd): `filter(Δ_elevation / sqrt((Δelapseddistance·1000)² − Δ_elevation²), gaussian(3,17), 2)`, per-sample rise over horizontal run, Gaussian-smoothed | Grade-coloured charts. Trail run 1094: average 2.9 % → 1.8 %, median 0.9 % → 0.4 %, 99th percentile 66 % → 52 %; road run 1097 unchanged on average (0.02–0.04 %), wider tails (±20–30 %). Not VERIFIED |
| 14 | athlete-level sets | **Changed**: reductions (`max(tss)`, `greatest`, `stddev`…), `meanmax(x)` envelopes and per-workout sample aggregates cover the chart (RHE) range unless an `athleterange` is given; only `tl()` integrates the whole history | WKO5 works on the selected range | "(Range)" charts and range PD curves now use the range; before they used all history |
| 15 | `max/min(a, b)` | Groupby for two dated / listed sets or a period name, elementwise otherwise (PROVISIONAL split) | Reference: groupby; disassembly: elementwise max/2 | Sample-vs-sample `max(x, y)` stays elementwise |
| 16 | `dfrc(power, frc, ftp)` | **Implemented** (§5): kJ balance, bi-exponential recovery 0.3/τ25 + 0.7/τ300 | DISASSEMBLY 0x6d7ae0 | "dFRC Run" series; not VERIFIED (no cached result) |
| 17 | `fmax`, `kleg`, `ecpower`, text fields | **Fixed**: WKO5's channel strings (§7b), parsed verbatim now that stancetime / height evaluate in ms / cm (#20), `sftp` = bikeftp, title/description/notes/code = index 3213/3206/3207/3210 | DISASSEMBLY 0x7242ac, 0x71dba0, 0x4f93e6 / 0x4fb517 | "Impact Gs", "ElevCP"/"Elev CF" rows of the Palladino / Hilly Run reports; the Mountaineering description labels |
| 18 | `ctspower`, `rstpower` levels | **Fixed**: empty tables, `levelcount` = 0 | Builder 0x64d1e0 clears the table; level count 0 (§4) | none of the user's charts |
| 20 | display units | **Fixed**: stancetime evaluates in ms, verticaloscillation in cm, the height setting in cm, in every expression (§7b); MILLISECONDS / CM axes lost their ×1000 / ×100 display scale. Pace stays min/km | Expressions see display units (stancetime ms, height cm, pace per the athlete's unit preference) | The user's formulas now read as written. Road run 1097: Palladino "Flight Phase" 0.999 → −1.5 % (GCT 448 ms average against a 420 ms step: the average includes walking samples with GCT up to 700 ms), "Pwr-GCT" none → 0.392 W/ms. Trail run 1094 Hilly Run Summary: "Coggan Osc Pwr %" 0.4 % → 42 %, "LSS/kg/GCT" 169 → 0.169 |
| 20b | x/0 on sample series | **Fixed**: na, as for single values (before: ±inf, which made `avg(power/stancetime)` inf whenever a sample had GCT 0) | Not decoded | PROVISIONAL |
| 21 | groupby with na keys | na keys are dropped | Cache5 shows one extra group with an na x per workout (`avg(@HR, if(@Pace<=20, @Pace))`) | One extra (na) bin in RHE pace/HR scatter charts |
| 19 | XY-set model | Only curves, lists, pairs and per-workout / daily sets carry x; sample sets keep their x implicitly (elapsedtime) and are never re-ordered or shortened | Every WKO5 set is (x, y) pairs | The VO2max interval-marking charts (`{@ZeroBeginning, filter(...), @ZeroEnd}`, `lookup` over transition sets) evaluate but will not match WKO5 |

---

## 9. Open items

1. `bin` with levels on pace channels: check whether values are compared as pace or
   speed, and the index order of the pace systems (evaluator: fastest level = index 0).
2. `week` numbering is ISO-8601 per the Reference; `weekval`'s offset follows the Monday
   week start of the Reference example — check with a non-Monday first-day preference.
3. ~~Kernel shape and normalisation of `filter` / `isef` / `gaussian`~~ — decoded (§5).
   Still to do: VERIFY against a WKO5 number. Open a workout chart with e.g.
   `filter(power, isef(1/9, 9), 2)` in WKO5 so that it is cached (or read a value off the
   screen), then compare it with the evaluator.
4. ~~The `ctspower` / `rstpower` tables~~ — they have no levels in 5.0.587 (§4).
5. ~~The rounding mode of `round`~~ — halves away from zero, x·m/m (§7).
6. ~~`dfrc(power, frc, ftp)`~~ — decoded (§5); VERIFY the same way as item 3, using the
   "dFRC Run" chart's dFRC value at one time point.
   ~~`fmax`~~ — a derived Run channel, decoded (§7b). VERIFY: open workout 1097 (2026-09-24
   road run) in WKO5 with the Palladino Run Summary Report and read "Impact Gs" (evaluator
   1.557; 1.539 for 1094). Also read "Flight Phase" (evaluator −1.5 %), "GCT" (448 ms) and
   "Pwr-GCT" (0.392) there to confirm the ms convention (§8 #20), and the "Avg Grade" of 1094's
   Hilly Run Summary (evaluator 1.83 %) to check rgrade (§8 #13).
7. ~~`ecpower`~~ — WKO5's expression string, decoded (§7b). VERIFY: read "ElevCP" /
   "Elev CF" of the same report (evaluator: 1097 150.16 W / 0.023 %, 1094 128.73 W /
   0.60 %); a high-altitude workout (e.g. the 2025-06-21 Mountaineering day, but it has no
   power) would test f(h) better.
8. ~~Which index field is `description` / `notes`~~ — decoded (§7b): 3206 / 3207, title 3213.
   Only `description` is backed by data (one workout). To check `notes`: add a note to any
   workout in WKO5, save, and re-read index field 3207 (and .wko4 record 4700).
9. `in` has no Reference entry; membership is the only plausible reading.
10. Sample-level `avg(x, groupby)` is a plain mean per key; WKO5 may time-weight it.
11. `max/min(a, b)`: groupby (Reference) vs elementwise (disassembly note) for two sample
    series — look at 0x6f0c90's use of the `link` name.
12. The `(expr)"unit"` casts appear to be unit tags only; the parser ignores them.
