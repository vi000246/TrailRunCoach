# WKO5 internals — formulas

Reverse-engineered from `WKO5.exe` 5.0.587 (x86-32) for personal interoperability,
plus verification against WKO5's own stored numbers.

Status legend:
- **VERIFIED**: reproduced WKO5-stored numbers exactly (the count of files/values is noted).
- **DISASSEMBLY-ONLY**: read from machine code, not yet checked numerically.

Addresses are VAs in WKO5.exe 5.0.587 (image base 0x400000). Helper scripts live in the
session scratchpad under `re/`: `wk.py` (disasm/xrefs), `registry.py` (function registry),
`rapower.py` (NP reference implementation), `verify_*.py`.

---

## 0. Conventions

- **Invalid / no data** = `DBL_MAX` (1.7976931348623157e308). Every formula checks for it with
  `ucomisd x, DBL_MAX; lahf; test ah,0x44; jnp` (i.e. "if x == DBL_MAX").
- **Sample hold**: a sample at time t[i] holds its value over (t[i-1], t[i]]; the first sample
  holds from the range start (0). The same rule drives range averages (see `backend/files/wko4_file.py`).
- **Sport group** strings compared in formulas: `"Run"`, `"Bike"`, `"Swim"` (athlete index field 3209).

---

## 1. Range metric fields (athlete index / .wko4 range records)

Source: the range serializer @0x54af00 (`movsd xmm0,[ebx+OFF]; push TAG`) joined with the
range-variable getter @0x4f8f40 (`mov edx,<name global>` → `[edi+0x270+OFF]`). **Mapping from DISASSEMBLY;**
distance/climbing/vam/descending/elevationchange/work were also confirmed numerically.

| field | variable | notes |
|---|---|---|
| 4206 | range length s | 4205 = range start s |
| 4213 | `movingduration` | |
| 4214 | `pedalingduration` | |
| 4248 | `tssduration` (power) | VERIFIED: count of `_rapower` seconds (§3) |
| 4215 | `duration` | |
| 4216 | `begindistance` | |
| 4217 | `distance` (km) | VERIFIED (780/780) |
| 4218 | `work` (J) | power × dt with sample-hold |
| 4219 | `np` | VERIFIED 359/359 (§3) |
| 4222 | `vi` | NP / avg power |
| 4247 | `ef` | NP / avg HR |
| 4223 | `climbing` | VERIFIED (628/628), sum of positive deltas of `_elevation` |
| 4224 | `vam` | climbing / hours |
| 4225 | `descending` | |
| 4226 | `grade` | |
| 4227 | `elevationchange` | VERIFIED (628/628) |
| 4228 | `pwhr` | |
| 4229 | `pahr` | |
| 4230 | `ngp` (Run, min/km) | returned by the ngp function when there is no TSS override |
| 4235 | `hrTSS` | returned by `tss` as the last fallback |
| 4236 | `hrIF` | returned by `if` as the last fallback |
| 4249 | pace `tssduration` | used by rTSS; getter falls back to it when there is no power |
| 4250, 4251 | ? | not yet identified |

`if` (id 4220) and `tss` (id 4221) are **not stored**; they are computed on demand because they
depend on thresholds. Workout FTP at the time of the workout = athlete index field **3010**.

---

## 2. `tss` — Training Stress Score (function @0x54a8d0) — DISASSEMBLY-ONLY (NP/tssduration inputs VERIFIED)

```
if tssduration(4248) > 0 and NP(4219) valid and FTP > 0:        # power
    TSS = NP^2 * tssduration / (FTP^2 * 36)                      # = hours * IF^2 * 100
elif sportgroup == "Run" and d=4249 > 0 and IF_pace valid:       # rTSS
    TSS = (d/60)^1.025 * IF_pace^2 / 60 * 100
elif sportgroup == "Swim": ...                                    # sTSS, cubic (not decoded yet)
else:
    TSS = hrTSS (field 4235)
```
Constants: 36.0 @0x86c2d0, 1.025 @0x86c088, 60.0 @0x86c330, 100.0 @0x86c390.

## 2b. `if` — Intensity Factor (function @0x54a410) — DISASSEMBLY-ONLY
```
power (Bike/Run with tssduration & FTP): IF = NP / FTP
Run without power:                       IF = runtpace / NGP        # both min/km; NGP = field 4230
Swim:                                    speed based (distance*1000/duration vs 16.667/tpace)
else:                                    IF = hrIF (field 4236)
```
NGP function @0x54a290: if a user TSS override is present (workout +0x240), NGP is derived back from
it: `IF = sqrt(TSS*0.6 / (duration/60)^1.025)`, `NGP = tpace / IF`; otherwise NGP = field 4230.

---

## 3. `np` / `tssduration` / `_rapower` / `_rapower4` — VERIFIED (359/359 power workouts, exact)

Builder @0x65b391, consumer in calculatePowerMetrics @0x655750.

```
_rapower (1 s channel):
tcur = 0; edi = 0
loop:
    tcur = round(tcur + 1)
    advance edi to the first sample with t[edi] >= tcur; stop if none
    remaining = 30; covered = validcov = sum = 0
    for i = edi down to 0 while remaining > 0:
        w = min(remaining, min(tcur, t[i]) - t[i-1])     # t[-1] = range start
        covered += w
        if v[i] valid: validcov += w; sum += w * v[i]
        remaining -= w
    if validcov < 0.001: emit nothing for this second
    avg = sum / (validcov if validcov >= 27 else covered)
    emit (tcur, avg); _rapower4 = avg^4
    (if tcur - last_emit >= 1.01, an invalid sample is written at tcur-1: a gap marker)

NP          = mean(_rapower4 over emitted seconds) ^ 0.25
tssduration = number of emitted seconds × 1.0 s
```
Reference implementation: scratchpad `re/rapower.py`.

---

## 4. `tl(numbers, constant)` — training load (function @0x70c0c0) — DISASSEMBLY-ONLY

```
k = 1 / constant
v = 0                                   # starts at 0 on the first input day
for each calendar day d from the first input day:
    x = sum of that day's inputs that are valid and 0 <= x <= 5000   (others ignored)
    v = v + (x - v) * k                 # LINEAR, not 1-exp(-1/c)
    output v for day d
days before the first input day → 0
```
Defaults: `ctlconstant` = 42, `atlconstant` = 7 (athlete profile fields 3022 / 3021).
`tsb` = shift(ctl - atl, 1) per the Expression Reference.

**Caution:** the athlete PMC snapshot (record 3403: ctl 17.616, atl 10.406) was written mid-sync
and does not match any day of the full dataset. With the rules above on the full data,
CTL(2026-09-29) = 25.82 and ATL = 10.52. Verify against WKO5's on-screen numbers instead.

---

## 5. Expression function registry

`registry.py` parses the registry @0x6c5000–0x6ca000 (219 rows). **Pairing rule: registry name[k]
belongs to impl[k+1]** (verified: `sumsqr` → params[15], `ftp` → params[6], ...). The corrected map is
`re/fn_registry.json` (maintained by the functions agent), e.g. tl/2=0x70c0c0, meanmax/1=0x6f2040,
pdcurve/1=0x6f81f0, ftp/1=0x6e0b20, frc/1=0x6ded40, pmax/1=0x6fa5f0, tte/1=0x70d940, vo2max/1=0x70f800.

Scope split: per-workout metrics → `workout-metrics.md`; general function semantics (incl. the exact
`meanmax` curve generation) → `functions.md`; **this file owns TSS/NP/tl (above) and the
power-duration model family (below).**

---

## 6. Power-duration (PD) model — DISASSEMBLY-ONLY (not yet numerically verified)

### 6.1 Model function P(t) (@0x671e10; also inlined in the fitters)
Parameters (model object offsets): FRC `+0x30` (J), τ1 `+0x38` (s), FTP `+0x40` (W), τ2 `+0x48` (s),
TTE `+0x50` (s, decay breakpoint), D `+0x58` (decay coefficient, ≤ 0), Pmax `+0x28`.
```
anaerobic(t) = FRC/t · (1 − e^(−t/τ1))                                  (@0x671ed0)
aerobic(t)   = (FTP + [t > TTE]·D·ln(t/TTE)) · (1 − e^(−t/τ2))          (@0x671f20)
P(t)         = anaerobic(t) + aerobic(t)
```

### 6.2 Fit entry (@0x6741e0)
- Input: meanmax curve as (duration s, power W) points (map order = ascending duration).
- **Requires the curve's longest duration ≥ 2400 s**, otherwise no model.
- Preference `pdmodel`: if `"wko4"` → WKO4 fitter @0x6747c0, **otherwise (default) → WKO5 fitter @0x674c40**.
- Then `validate` (@0x6722a0) and `tte_solve` (@0x674490, target = FTP) → model `+0x68`.

### 6.3 WKO5 default fitter (@0x674c40)
```
drop tail points while duration > 29576 s or power <= 0; need >= 5 points
Pmax  = mean(power where 3 <= t <= 5 and power > 0)   else power[0]        # NOT refit later
FTP0  = mean(power where 900 <= t <= 1200 and power > 0) else 250
tau1  = 15; thr = Pmax − (Pmax − FTP0)·0.333
        if some point has power < thr: tau1 = t_first_such / 3
tau2  = 25
FRC0  = Pmax · tau1
TTE0  = clamp(t_last − 300, 1800, 3600)
D0    = −50
fit #1: points with t <= min(t_last, 2400); free = {FRC, tau1, FTP}        (flags 01 01 01 00 00 00)
        if SSE result invalid → no model
FTP floor: FTP = max(FTP, max over points 1200<=t<=3600 of (y − anaerobic(t)))
fit #2: all points; free = {tau2, TTE, D}                                   (flags 00 00 00 01 01 01)
if TTE < 1800: TTE = 1800, refit #2 with TTE fixed
if TTE > 3600: TTE = 3600, refit #2 with TTE fixed
monotonic check: walking points, model P must not rise > 10 W above its running minimum
```
The WKO4 fitter (@0x6747c0, only when preference `pdmodel` = "wko4") is the same except: no 29576 s
tail trim / 5-point minimum, no FTP-floor step, fit #2 frees {FRC, τ1, FTP, TTE, D} with τ2 fixed
(flags 01 01 01 00 01 01), and the monotonic tolerance is 1 W.

### 6.4 Solver (@0x6724c0) — bounded Gauss–Newton
```
params p = [FRC, tau1, FTP, tau2, TTE, D]
lower    = [1,   1,    1,   15,   1000, −1000]
upper    = [1e15,1e15, 1e15, 45,  3600, 0]
residual r_i = y_i − P(t_i)   (unweighted; SSE over points with t <= 29576)
Jacobian (analytic):
  dP/dFRC  = (1 − e^(−t/τ1)) / t
  dP/dτ1   = −FRC · e^(−t/τ1) / τ1²
  dP/dFTP  = 1 − e^(−t/τ2)
  dP/dτ2   = −(FTP + [t>TTE]·D·ln(t/TTE)) · t · e^(−t/τ2) / τ2²
  dP/dTTE  = −[t>TTE] · D · (1 − e^(−t/τ2)) / TTE
  dP/dD    = [t>TTE] · ln(t/TTE) · (1 − e^(−t/τ2))
loop (max 500 iterations):
  Δ = solve normal equations for the free params (matrix helpers @0x675590/0x675500/0x676310)
  p_new = clamp(p + Δ, lower, upper); if SSE(p_new) > SSE(p): Δ *= 0.125 and retry
  stop when every |Δ_j| < 1e-5
afterwards: covariance-based standard errors for each param (→ params[1,3,5,7,9,11,13])
```

### 6.5 Validity gate (@0x6722a0)
All params finite and ≠ DBL_MAX, and 0 < Pmax < 3000, 0 < FRC ≤ 80000, 0 < τ1 < 90, 0 < FTP < 600,
0 < τ2 < 300, 1800 ≤ TTE ≤ 3600. Otherwise the model is invalid (functions return invalid).

### 6.6 `params` vector (built @0x60c69e) and accessors
| idx | value | accessor |
|---|---|---|
| 0 | Pmax (mean MMP 3–5 s) | `pmax` |
| 1 | Pmax error | `pmaxe` |
| 2 | FRC (J) | `frc` returns kJ (÷1000) |
| 3 | FRC error | `frce` |
| 4 | τ1 | `tau1` |
| 5 | τ1 error | `tau1e` |
| 6 | mFTP | `ftp` |
| 7 | FTP error | `ftpe` |
| 8 | τ2 | `tau2` |
| 9 | τ2 error | `tau2e` |
| 10 | model TTE param (+0x50) | `dmax` |
| 11 | its error | `dmaxe` |
| 12 | D (decay) | `s` |
| 13 | D error | `se` |
| 14 | phenotype code | `phenotype` |
| 15 | SSE | `sumsqr` |
| 16 | **TTE** = t where P(t) = FTP (bisection @0x674490) | `tte` |

`tte` solve (@0x674490): start t = 2·TTE_param, double t up to 10× while P(t) > FTP, then 25 bisection
steps in log10 space (`mid = 10^((log10 lo + log10 hi)/2)`), stopping when the integer interval collapses.

### 6.7 `vo2max` (@0x70f800)
`VO2max = (FRC_J / 589 + mFTP) / 84.5 + 0.656`  (units as returned by WKO5; likely L/min — to check).

### 6.8 `phenotype` (@0x671fa0 → names @0x672200)
x = Pmax/FTP, y = FRC/Pmax (s), z = FRC/FTP. Score each class, return the argmax:
```
1 All-rounder : 60.973x + 1.404y − 6.653x² − 0.081z − 0.03y²  − 150.73
4 Pursuiter   : 11.454x + 1.047y − 1.635x² + 0.022z − 0.018y² − 39.772
2 Sprinter    : 81.122x − 1.181y − 8.521x² + 0.758z − 0.091y² − 213.943
3 Time-Trialer: 19.044x + 1.253y − 2.786x² − 0.043z − 0.034y² − 43.392
```
(scores start at −DBL_MAX; ties keep the earlier class; 0 = none if FTP/Pmax are ~0.)

### 6.9 iLevels (@0x649de0, also @0x64b440) — individualized training levels
Durations from the model: tA = ln2·τ1, tB = 3·ln2·τ1, tC = t where aerobic(t)/P(t) = 0.632 (log10 bisection
in [1, TTE]), tD = t where P(t) = 1.05·FTP.
```
7  Pmax       : durations [1, tA],  power >= P(tA)
7a Pmax/FRC   : [tA, tB],           P(tB)..P(tA)
6  FRC        : [tB, tC],           P(tC)..P(tB)
5  FRC/FTP    : [tC, tD],           1.05·FTP..P(tC)
4  FTP        : 0.95–1.05 × FTP
4a Sweetspot  : 0.88–0.95 × FTP
3  Tempo      : 0.76–0.88 × FTP
2  Endurance  : 0.56–0.76 × FTP
1  (Recovery) : < 0.56 × FTP
```
Level boundary details (which end is inclusive) are DISASSEMBLY-ONLY.

### 6.9b Optimized interval targets: `targetname` / `targetduration` / `targetpower` — DISASSEMBLY-ONLY
Level index (Expression Reference): 0 Extensive Aerobic (FTP), 1 Intensive Aerobic (FTP),
2 Max Aerobic (VO2max Intensive), 3 Extensive Anaerobic (FRC), 4 Intensive Anaerobic (FRC), 5 Max.
Inputs: tte (params[16]), mFTP, FRC (J), τ1 from the same meanmax curve.
```
targetduration (@0x7096c0, jump table @0x709ef4):
  0: tte
  1: 0.9625 · FRC_J / (0.0375 · FTP)
  2: 0.1625 · FRC_J / (0.032  · FTP)
  3: 5 · ln2 · τ1
  4: 3 · ln2 · τ1
  5:     ln2 · τ1
targetpower (@0x70a340, jump table @0x70aa9c):
  0: FTP · 1.00
  1: FTP · 1.02
  2: FTP · 1.20
  3,4,5: P_model(targetduration(level))    (pdcurve evaluated/looked up at that duration)
```

### 6.9c `ftp/frc/pmax/tte/vo2max/fibertype(mm, lookback)`; `ftpcurve` / `frccurve`
Per the Expression Reference the two-argument forms build a meanmax curve for every date in the range
from the previous `lookback` days, fit the model (§6.3) per day and return the daily parameter. Same model
code (@0x6741e0 via the daily builders @0x649de0/@0x64b440). DISASSEMBLY-ONLY; per-day caching details not
decoded. The evaluator fits every day (a fit is ~10 ms; days whose window holds the same workouts share one
fit) and returns na where the fit fails the validity gate (§6.5).

`ftpcurve(mm)` / `frccurve(mm)` are **not** daily: the Reference defines them as "a power-duration curve
… showing only the FTP / FRC component", i.e. `aerobic(t)` and `anaerobic(t)` of §6.1 over the curve's
durations (their sum is `pdcurve`). The user's charts use them that way (`ftpcurve(mm)*xx(ftpcurve(mm))`
= aerobic kJ per duration).

### 6.9d `pdcurve(standardindex, gender)` / `pdprofile(meanmaxcurve)` — TODO
Standards: Novice, Novice 2, Fair, Moderate, Good, Very good, Excellent, Exceptional, World class
(strings @0x84e1c4…@0x84e208), gender "male"/"female". The standard parameter tables were not located yet
(not the classic Coggan power-profile values).

### 6.10 Chart-level TIS formulas (from WKO5's default chart expressions, cached in Cache5)
```
Aerobic TIS:
workoutrange(begintime,endtime,@lookback:=90,
  @ftp:=sport(sport).athleterange(date-@lookback+1,date,ftp(meanmax(power))),
  @height:=@ftp*1.3,@width:=0.105,@center:=@ftp*1,@ewmapower:=ewma(power,18),
  @weighting:=-(@width*(@ewmapower-@center))^2+@height,@weighting:=if(@weighting>0,@weighting,0),
  @weightedwork:=@ewmapower*@weighting*deltatime/1000,@score:=sum(@weightedwork)/(@ftp*3.6)/85,
  if(count(@ewmapower)>0,clamp(round(@score)+1,1,10)))
Anaerobic TIS:
workoutrange(begintime,endtime,@lookback:=90,@weighting:=1.379,
  @threshold:=sport(sport).athleterange(date-@lookback+1,date,ftp(meanmax(power)))*.85,
  @frc:=sport(sport).athleterange(date-@lookback+1,date,frc(meanmax(power))),
  @ewmapower:=ewma(power,18),@powerabove:=if(@ewmapower>@threshold,@ewmapower-@threshold,0),
  @weightedpowerabove:=@powerabove^@weighting,@work:=(@weightedpowerabove*deltatime/1000),
  @score:=sum(@work)/@frc/3.6,if(count(@ewmapower)>0,clamp(round(@score)+1,1,10)))
```
**VERIFIED as built-ins:** WKO5.exe defines the variables `tisaerobic` / `tisanaerobic` as exactly these
expressions (string literals @0x721945 (403 chars) / @0x7218e0 (449 chars), evaluated per workout range).

### 6.12 Built-in variables defined as expressions (string literals in WKO5.exe)
Extracted with `re/builtin_exprs.py` (pattern `push LEN; push STR` in the variable evaluator):
```
stamina      := clamp(1+(s(meanmax(_rapower4)^.25)*(1+ln(3600/Dmax(meanmax(_rapower4)^0.25))))
                      /ftp(meanmax(_rapower4)^0.25),0,100)                                  @0x71d01c
atl          := tl(tss,atlconstant)                                                         @0x71dd61
ctl          := tl(tss,ctlconstant)                                                         @0x71dd83
tisaerobic   := (Aerobic TIS expression above)                                              @0x721945
tisanaerobic := (Anaerobic TIS expression above)                                            @0x7218e0
```
So **stamina** fits the PD model on the *normalized* MMP curve `meanmax(_rapower4)^0.25` (MMNP), and is
`1 + D·(1 + ln(3600/TTE_param)) / mFTP` (D = params[12] `s`, TTE_param = params[10] `dmax`), clamped to
[0, 100] (a fraction, displayed as %). Note `Dmax`/`s` are the expression names of params[10]/[12].

Derived *channels* are expression strings too, created per workout by 0x7242ac for given sport
groups (`builtin_exprs.py` misses them: they live outside its address window):
```
ecpower (Bike, Run) @0x860d18  if(ewma(power,25)/F<=sftp, ewma(power,25)/F, (ewma(power,25)/F-sftp)*F+sftp)
                               F = -0.00000000674*(metric(elevation))^2-0.0000274*(metric(elevation))+.997
fmax    (Run)       @0x860e88  (metric(weight)*g)*(pi/2)*((60*1000/cadence/2-stancetime)/stancetime+1)
kleg    (Run)       0x725058   Fmax / (leg compression), leg = metric(height)/100*0.53
rngp    (Run)       0x724e46   1000/_ragpace
rgrade  (all)       0x724cfd   filter(dElev / sqrt((dDist*1000)^2 - dElev^2), gaussian(3,17), 2)
```
`sftp` resolves to `bikeftp` (0x71dba0). Full strings and units: functions.md §7b.

### 6.11 Handoff note for the functions agent (meanmax)
Observed vs Cache5 `meanmax(power)`: windows are built from whole consecutive samples (a sample
that holds 6 s only counts toward durations >= 6 s; smart-recording file 2023_09_03_16_35: 1 s best
147 W while a 6 s-held 153 W first appears at x=6), and averages divide by valid time. Details are
owned by `functions.md`.
