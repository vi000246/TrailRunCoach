# SuperPower Calculator → 賽事功率頁（race-power page）

Date: 2026-09-30. Source workbook: `C:\Users\<user>\Downloads\TOOL_ SuperPower Calculator - 副本.xlsx`
(Google Sheets export, Revision 4.2, Palladino/Stryd community tool). The workbook is a **blank
template**: apart from the default environment chain and the lookup tables it has no cached
values to test against. Raw extraction (cell dump incl. array formulas, named ranges and data
validations): `…\b352b1cb-…\scratchpad\dump2.txt`, table extractors `grid.py`, `tasks.py`.

This document is the spec for `backend/engine/racepower/` + `backend/api/racepower.py` +
`backend/static/racepower.html`. It has three parts: (1) what the workbook computes, (2) which of
its tasks this project ports, with the nine ambiguities decided, (3) how the method is extended to
trail races and 百岳, which the workbook does not handle.

Notation: W = body mass (kg), P = power (W), CP = critical power / FTP (W), TTE = time to
exhaustion at CP (s), k = Riegel exponent (negative), RE = running effectiveness
(speed m/s ÷ W/kg), M = environment multiplier.

---

## 1. What the workbook computes

### 1.1 Environment multiplier M (`v4 Calcs` rows 204–238)

- Defaults: a blank side (From/To) copies the other side; both blank → 200 m, 12 °C, 70 %.
- Air pressure (torr):
  `p = 101325·((T+273.15)/((T+273.15) − 0.0065·alt))^(9.80665·0.0289644/(8.31432·−0.0065)) · 0.00750062`
- Altitude factor: `A(p) = (−174.1448622 + 1.0899959p − 1.5119e−3·p² + 0.72674e−6·p³)/100`
- Dew point (Magnus, a = 6.1121, b = 18.678, c = 257.14, d = 234.5):
  `ita = ln(RH/100 · exp((b − T/d)·T/(c+T)))`, `dewF = (c·ita/(b − ita))·1.8 + 32`
- Heat penalty (Hadley): `x = dewF + (1.8T + 32)`; if x > 100:
  `H = 0.001341x² − 0.249517x + 11.699986` (%), else 0.
- `pct = −(A_from − A_to) − (H_to − H_from)/100`, `M = 1 + pct`. `P_to = P_from · M`.
- Cached, testable values for the defaults (200 m, 12 °C, 70 %): torr **741.965324**,
  A **0.9911949118**, dewF **44.04739671** (via ita 0.4738293568, dew °C 14.02406404 → these
  are intermediate cells B231/B233), heat = FALSE (0), **M = 1**.
- Rows 229–230 (Skiba altitude model, cached 0.9964848) are computed but never used. Not ported.

### 1.2 Riegel / RE tasks

| # | Task | Formula |
|---|---|---|
| 7 | FTP from a prior race | `CP = P_prior·(TTE/T_prior)^k` |
| 8 | Race power from FTP + target time | `P = CP·(T/TTE)^k` |
| 9 | Race power from prior race + target time | `P = P_prior·(T/T_prior)^k` |
| 10 | Race power from prior race + target distance | workbook: `P = P_prior·(D/D_prior)^k` (see decision D1) |
| 11 | Scenarios: Riegel + RE (iterative) | t₀ = 3600; repeat 6×: `P = CP·M·(t/TTE)^k`, `t = D/(RE·P/W)` |
| 12 | % FTP improvement needed | `CP_req = P_req·(TTE/T)^k`, `(CP_req − CP)/CP`, P_req from task 14, no M |
| 14 | Race power from distance, time, RE | `P = (D/T)/RE · W` |
| 15 | Race time from distance, power, RE | `T = D/(RE · P/W · M)` |
| 17 | Scenarios: CP + W′ + RE | `E = D·W/RE`, `t = (E − W′)/CP`, `P = W′/t + CP` (≤ 5–10 km only) |

Final outputs are multiplied by M (except task 12). `%Pt` output unit = fraction of the known
power.

### 1.3 RE/CVI adjustment (task 18, `RE (CVI)` sheet)

- CVI = climbing ft / distance mi. Clamp to [−25, 101]. Categories: Downhill −25..−1,
  Flat 0–25, Slightly Hilly 26–35, Somewhat Hilly 36–50, Moderately Hilly 51–75,
  Very Hilly 76–100, Extremely Hilly 101+.
- `adj = (prior_category_index − target_category_index) × 0.01` (+0.01 per category that the
  training was hillier than the race). `RE_used = RE_in + adj`.

### 1.4 Riegel lookup (task 16, `Riegels` sheet)

- Distance category = largest threshold ≤ distance among 4850 / 9700 / 20465.1 / 40929.15 m
  (0.97 × 5k/10k/half/marathon). Above 1.03 × standard → "Non-std", no result.
- Row = first time band of the *prior* distance whose upper bound is > prior time; value =
  "most likely" k (column K). Range = likely ± 0.01, clamped to [−0.12, −0.03] at the ends.
- Most-likely k by band (fastest → slowest):
  - target marathon: −.06 −.07 −.08 −.09 −.09 −.10 −.10 −.11 −.12 −.12
  - target half:     −.05 −.06 −.07 −.08 −.08 −.09 −.09 −.10 −.10 −.10
  - target 10k:      −.04 −.05 −.06 −.07 −.08 −.08 −.09 −.09 −.09 −.10 −.10
  - target 5k:       −.04 −.05 −.06 −.06 −.07 −.07 −.08 −.08 −.09 −.09 −.09
- Band upper bounds per (target, prior) pair: read them from `dump2.txt` / `grid.py`
  (example, marathon target with a 10k prior: 29:50, 34:10, 38:20, 42:40, 46:50, 51:10, 55:25,
  59:40, 1:04:00, 4:16:00).

### 1.5 CP from efforts (tasks 6 / 20) and RWC rating (task 19)

- OLS `LINEST(work = P·t, t)` over up to 10 efforts: slope = CP, intercept = W′ (J), R² from
  LINEST stats. Validity checks: ≥ 1 effort ≤ 6:00 and ≥ 1 ≥ 15:00; longest − shortest ≥ 6 min;
  dates within 14 days; warn if longest > 30 min; power must strictly fall as duration rises.
- RWC rating bands (inclusive, rounded):

  | Gender / meter | Unit | Too Low | Low | Medium | High | Too High |
  |---|---|---|---|---|---|---|
  | Male, non-Wind | J/kg | ≤61 | 62–79 | 80–136 | 137–154 | ≥155 |
  | Female, non-Wind | J/kg | ≤73 | 74–84 | 85–119 | 120–130 | ≥131 |
  | Male, Wind | J/kg | ≤91 | 92–109 | 110–166 | 167–184 | ≥185 |
  | Female, Wind | J/kg | ≤103 | 104–114 | 115–149 | 150–160 | ≥161 |
  | Male, non-Wind | kJ | ≤4.28 | 4.29–5.63 | 5.64–9.82 | 9.83–11.17 | ≥11.18 |
  | Female, non-Wind | kJ | ≤3.97 | 3.98–4.65 | 4.66–6.78 | 6.79–7.46 | ≥7.47 |
  | Male, Wind | kJ | ≤6.28 | 6.29–7.63 | 7.64–11.82 | 11.83–13.17 | ≥13.18 |
  | Female, Wind | kJ | ≤5.97 | 5.98–6.65 | 6.66–8.78 | 8.79–9.46 | ≥9.47 |

  "Too High" → CP may be under-estimated; "Too Low" → over-estimated.

### 1.6 Riegel from activities (task 5)

`LINEST(ln P, ln t)` over the user's efforts; slope = k.

### 1.7 Zones (task 13)

P³ lower bounds (fraction of CP): 1A .50, 1B .65, 1C .75, 2 .801, 3A .88, 3B .95, 4 1.011,
5 1.06, 6 1.161, 7 1.501. These are the same bands as `backend/engine/zones.py`
(`PALLADINO_POWER_ZONES`, exclusive upper bounds 0.80/1.01/1.16/1.50) written as inclusive
lower bounds. Reuse `zones_json`.

### 1.8 Worked example (inputs chosen by us, numbers computed with numpy — use as tests)

W = 70, CP 285, TTE 3000, k −0.07, RE 1.0.

- M, 0 m/15 °C/50 % → 1500 m/25 °C/70 %: torr 760.0003 → 638.1478; A 1.00000017 → 0.94599896;
  dewF 40.397 → 66.307; x_to = 66.307 + 77 = 143.3 → H_to 3.48248 %; **M = 0.911174**.
- CP test, 180 s @ 345 W and 720 s @ 300 W: **CP 285, W′ 10800 J** (154.3 J/kg, "High").
- 3-point fit 180 s @ 400, 360 s @ 360, 1200 s @ 318: CP 302.429, W′ 18991.09, R² 0.999905;
  ln-ln k = −0.118937.
- Task 8 (CP 300, 3:00:00): multiplier 0.914237 → 274.271 W; × M → 249.909 W.
- Task 7: 280 W for 1:30:00 → CP 291.761 W.
- Task 14: marathon in 3 h → 273.486 W. Task 12: CP_req 299.141 → −0.286 %.
- Task 15: 250 W → 11814.6 s. Task 11 (CP 300, marathon): converges to 10766.75 s, 274.330 W.
- Task 17 (5k, CP 300, W′ 15 kJ): t 1116.67 s → 313.433 W.
- RE lookup: prior CVI 30, target CVI 80 → −0.03.

---

## 2. What this project ports

| # | Task | Port? | How |
|---|---|---|---|
| 1 | Environment adjust | **yes** | core `env.py`; applies to every prediction |
| 3 | Per-activity metrics (RE, CVI, LSS/kg, Pa %, Form-power %) | **yes** | computed from the activity's own channels (Stryd `@air_power`, `@form_power`, `@leg_spring_stiffness`); shown in the "activities used" tables |
| 5 | Riegel from activities | **yes — main source of k** | ln-ln fit on the athlete's mean-max power envelope (§2.2) |
| 6 / 20 | CP from test / from activities | **yes** | test = the dated CP row in the season plan; activities = best efforts from the mean-max envelope (§2.1) |
| 7 | FTP from a prior race | yes | pick an activity as the prior race |
| 8 | Race power from CP + target time | **yes — core** | |
| 9 | Race power from prior race + time | yes | |
| 10 | Race power from prior race + distance | yes | exact form, D1 |
| 11 | Riegel + RE scenario | **yes — core predictor** | solved to convergence (D10), not 6 iterations |
| 12 | % CP improvement needed | **yes** | "to hit the target time you need CP ≈ …" |
| 13 | Zones | yes | reuse `zones_json` |
| 14 / 15 | power ↔ time via RE | **yes** | |
| 16 | Riegel lookup | yes, as cross-check | only 5k–marathon; ultra = out of table (warn) |
| 17 | CP + W′ + RE | limited | only for road targets ≤ 10 km; hidden otherwise |
| 18 | RE/CVI | yes, cross-check for trail | the personal trail RE (§3.1) is primary |
| 19 | RWC rating | **yes** | on the activity-derived CP/W′ |
| 2, 4 | (disabled in workbook) | no | |

### 2.1 CP / W′ / TTE sources (the page shows all, user picks; default in bold)

1. **Season-plan CP test** (`Plan.threshold_on("cp")`), when present and ≤ 90 days old.
2. **Activities fit (task 20)**: the athlete's mean-max power envelope over the last 90 days
   (runs only; WKO5 Cache5 curves via `Evaluator` `meanmax(runpower)`), sampled at
   180, 300, 420, 600, 720, 900, 1200 s (only durations present). OLS work-vs-time. Report CP,
   W′, R², the efforts used (date + activity of each best), the validity checks of §1.5
   (dates-within-14-days becomes a warning, since an envelope mixes days). Default when no
   recent test.
3. WKO5 modelled FTP (`pd_snapshot` mftp, with its TTE and FRC) — shown for comparison.

TTE: WKO5 `pd_snapshot` tte when available, else 3000 s (workbook default), editable.

### 2.2 Personal Riegel exponent (task 5, answers "can Riegel be computed for me?")

- Fit `ln P = a + k·ln t` on the mean-max envelope of runs over the last 365 days, for
  durations from max(TTE, 1200 s) to the longest duration with data, using the WKO5 1.05
  duration grid points. Return k, R², n, duration span, and the lookup-table k (§1.4) for a
  chosen prior race as cross-check.
- Warn when the target duration is more than 1.5× the longest fitted duration
  ("extrapolated beyond your longest effort").
- Durations beyond ~2–3 h are rarely maximal in training, so the fitted k tends to be too
  negative for long races; show both "personal" and "table" k and let the user choose. Ultra
  races: table k is clamped at −0.12 (the table's slowest value) with a warning.

### 2.3 Decisions on the workbook's ambiguities

| # | Workbook behaviour | Decision | Why |
|---|---|---|---|
| D1 | Task 10 applies k to the **distance** ratio | Use the time-consistent form: with P ∝ t^k and D ∝ t·P (RE constant), `P2 = P1·(D2/D1)^(k/(1+k))` | Same Riegel model as tasks 8/9; the workbook form is an approximation (k −0.07 → −0.0753) |
| D2 | Task 5 divides **time** by M | Normalise **power** instead: `P_ref = P_act · M(activity → reference)`; time untouched. Implemented for both the CP and the Riegel envelopes, altitude term only (the activity's median elevation → the training reference altitude; per-activity T / RH are not stored, so the heat term cancels) | M is a power multiplier everywhere else |
| D3 | Hadley term is 0 when °F + dew °F ≤ 100 | Keep (it is Hadley's rule), but clamp the quadratic at ≥ 0 | Faithful; avoids a small negative/positive step at the edge |
| D4 | Task 1 "weight in target conditions" `W·Pt/Prc_adj` | Not ported | No physical meaning; task 1 shows Pt, Pa, Prc adjusted |
| D5 | Riegel band uses strict `>` | Upper bound inclusive (`≤`) | A time exactly on the edge belongs to that band |
| D6 | CP fit excludes the FTP row, Riegel fit includes it | Neither fit ever includes a modelled point | Fits are on measured efforts only |
| D7 | Task 6 adjusts CP but not W′ | Keep: only CP is scaled by M | W′ is largely unaffected by moderate hypoxia (it is an anaerobic capacity) |
| D8 | Google Sheets QUERY | n/a (ported as code) | |
| D9 | Activities range mis-exported | n/a (we read activities directly) | |
| D10 | Task 11 stops after 6 iterations | Solve to convergence (|Δt| < 0.5 s, bisection fallback) | 6 iterations are already within 0.3 s; exact is simpler to test |

---

## 3. Extending to trail races and 百岳

The workbook is road-only: hills enter only as ±0.01 RE per CVI category; the lookup table
stops at the marathon; "Ultra" only widens input validation. This project extends it as follows.

### 3.1 Trail race (running gait, power meter): effort distance + personal trail RE

- **Effort distance** of the race: `D_eff = km + gain_m / X` with X from
  `backend/engine/algorithms/effort.py` `SIMPLE_FORMULAS`. Default `fitted_run` (X = 153,
  least squares on this athlete's runs against Minetti); option `itra` (X = 100, the
  Taiwan/ITRA convention, for comparison with race listings).
- **Personal trail RE**: over the athlete's trail runs with power in the last 365 days
  (tag `runningtrail`, climbing ≥ 150 m, moving ≥ 45 min):
  `RE_trail = (D_eff_m / moving_s) / (avg_power / W)` using the same X. Median and IQR; list the
  runs. This absorbs technical terrain and power-hiking, which CVI cannot.
- Cross-check (workbook method): road RE (median over flat road runs, CVI < 25) + CVI
  adjustment (§1.3) from the CVI of the runs that RE was measured on (the median CVI of
  those same flat road runs) to the race CVI. (An earlier draft said "the training CVI";
  that would pair an RE with a CVI it wasn't measured at.)
- **Prediction** (task 11 with D_eff and RE_trail): solve
  `P(T) = CP·M·(T/TTE)^k`, `T = D_eff/(RE_trail · P(T)/W)`. Output T, average power, % CP,
  real average pace (min/km on the true distance), effort pace (min per effort-km), EP/h.
- Climb guidance **[ours, heuristic]**: on climbs keep power ≤ 110 % of the target average;
  let descents fall. Label it as a heuristic.

### 3.2 百岳 (walking gait, multi-day, usually no power)

Hiking has no running power in this data set, so the model is speed-based:

- Route effort `EP = km + gain_m/100` (健行筆記 / ITRA convention, so the numbers match what the
  user sees on 健行筆記) and Yamamoto's course constant
  `CC = 1.8·h + 0.3·km + 10·gain_km + 0.6·loss_km` for energy.
- **Personal hiking EP/h**: over past hikes/mountaineering days (tags `hiking`,
  `mountaineering`; per calendar day for multi-day trips, as `achievements.py` splits them):
  `EP/h = EP_day / moving_h`. Median and IQR, weighted toward days with ≥ 600 m gain.
- **Prediction**: `moving_h = EP_route / (EP/h_personal · M_env · pack_factor)` per day.
  - `M_env` = §1.1 from the athlete's training conditions to the peak's conditions. For
    walking at sub-maximal effort this is a conservative proxy (it assumes speed scales like
    sustainable power).
  - `pack_factor = (W + L_hist)/(W + L_target)`, L_hist default 5 kg (day pack), L_target
    user input (default 12 kg for 2–3 days). Label as an assumption (Yamamoto: energy ∝
    body + pack).
  - Multi-day: the user enters per-day km/gain/loss if known; otherwise the route is split
    evenly across days.
- Outputs per day: moving time, EP/h needed, energy `CC·(W + L)` kcal, water
  `0.7–0.8 × kcal` ml (Yamamoto), HR cap = AeT (from the season plan / estimate).
- Readiness: compare with the athlete's biggest past day (EP, gain, moving h).

### 3.3 Environment inputs (From = training, To = race)

- **From (training conditions)** default: median elevation of the athlete's last 90 days of
  power runs, and the mean temperature / RH over those activities from the Open-Meteo
  archive at the median start location (one request). Fallback 100 m / 25 °C / 75 %.
- **To (race day)**: provider chain, first success wins, every value user-overridable:
  1. CWA 中央氣象署 open data (needs 授權碼; mountain forecast product when the date is inside
     its horizon; location matched by 百岳 name).
  2. Open-Meteo forecast (no key; lat/lon/elevation; ≤ 16 days).
  3. Open-Meteo archive climatology: same calendar window (±7 days) in the last 5 years at the
     location, daytime hours, lapse-rate-corrected to the peak elevation (−6.5 °C/km).
  4. Manual entry (always available; also the fallback when offline).
- The page shows which provider supplied the values and when; the numbers are editable.
- Location: event name → `backend/data/baiyue.json` peak (name, elevation, lat, lon); or pick a
  peak; or type lat/lon/elevation. For trail races: type or pick the highest point / start.
