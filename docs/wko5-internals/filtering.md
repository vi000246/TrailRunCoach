# WKO5 sport filtering, RHE state and the PMC snapshot

WKO5.exe 5.0.587 (x86-32). Reverse-engineered 2026-09-29 by RE Agent C.
Reference scripts are in the session scratchpad at `re/filter_*.py`.

Status tags:
- **VERIFIED**: reproduced against numbers WKO5 wrote.
- **DISASSEMBLY-ONLY**: read from machine code, not checked numerically.
- **INFERRED**: supported by evidence but not traced end-to-end.

## TL;DR

The mismatch between our PMC and WKO5's (TSB off by up to ~9, CTL 25.8 vs 17.6) was **not
caused by an RHE sport filter**. WKO5 **uses the TSS that came from TrainingPeaks
(`tssActual`) for a workout when that is available, instead of its own hrTSS**. With the rule below
and **all sports included**, we reproduce WKO5's athlete-bar snapshot to 8.4e-3 and the
user's PMC / Training Readiness screenshots within reading precision (|ΔCTL| 0.32, |ΔTSB| 0.70).

```
tss(workout) =
    power TSS  NP² · tssduration / (FTP² · 36)          if tssduration > 0, NP valid, FTP > 0
    rTSS       (d4249/60)^1.025 · (tpace/NGP)² / 60 · 100   if sport group == Run and pace data valid
    TP tss     .wko4 info field 4038 (TrainingPeaks tssActual)   if present
    hrTSS      index field 4235                          otherwise
```

The dominant case is **2026-08-14 Mountaineering (49 h)**. Our old rule gave it hrTSS **1014**,
while TrainingPeaks' TSS is **65** (`.wko4` info fields 4038 and 4057). That single day
contributed ~8 CTL.

## 1. The stored TSS override (why TP's TSS wins)

**Workout-level `tss` variable getter @0x4f97a1** (DISASSEMBLY-ONLY; behaviour VERIFIED via snapshot):

```
xmm0 = workout[+0x240]                   ; stored per-workout TSS
if xmm0 != DBL_MAX:  return xmm0         ; lahf/test ah,0x44/jp idiom: jump taken when not equal
else: return tss_compute(range=workout+0x270, sport=workout+0x18c,
                         ftp=workout[+0xb8], tpace=workout[+0xc0], override=xmm0)   ; @0x54a8d0
```

**TP sync sets the override @0x6400a5–0x6402b1** (DISASSEMBLY-ONLY):

- It parses `tssActual` from the TP workout JSON and clamps it to [0, 5000] (constant 5000.0 @0x86c488).
- It parses `tssSource`; `0x7fffffff` means the key is missing.
- **If `tssSource != 0`, the value is discarded (set to DBL_MAX).** Only `tssSource == 0`
  keeps TP's TSS as the override. It is then compared with the workout's existing value at
  +0x1d8 and written if it changed, which logs `%f`.

`tssSource` itself is **not persisted** in any file found: every double and enum field of the `.wko4`
info record (4001) and of the athlete index (3202) was checked. So from files alone the best available
approximation is "use the TP TSS (4038) when present, after power and rTSS". The small residual
(8.4e-3 on the snapshot) is consistent with a few workouts whose `tssSource != 0`.
Example: 2026-09-06 strength, where WKO5 used hrTSS 8.53 rather than TP's 9.0.

`.wko4` info fields involved: `4038` / `4057` = TP TSS (identical except for 23 workouts that
have no device data), `4039` / `4056` = TP total time, `4040` / `4055` = TP distance.

## 2. Compute branches (`tss_compute` @0x54a8d0)

DISASSEMBLY-ONLY. The power and rTSS parts are numerically consistent with the snapshot.

1. `range[+0x178]` must be valid and >= 0, and `range[+0x170]` must be valid; otherwise the result is invalid.
2. **Power**: `tssduration > 0`, `FTP > 0`, NP valid → `NP² · tssduration / (FTP² · 36)`.
3. **Run** (the sport group string compares equal to `"Run"`): pace tssduration (4249) > 0,
   both threshold args valid and > 0, NGP valid → `(d/60)^1.025 · IF² / 60 · 100`, with
   `IF` from @0x54a410 (= tpace / NGP).
4. **Swim** (sport group `"Swim"`): distance and moving time valid → cubic speed-based formula (not decoded further).
5. Otherwise it returns `range[+0xf8]`, the stored hrTSS (4235), if valid.

## 3. RHE / sport-filter bar

- The UI widget is `sportfilterwidget` (@0x4a016a). Its help text: *"The sport filter bar picks a
  combination of sports to analyze in the dashboards and charts."* The date-range list is
  separate: *"Pick date ranges to include in the dashboards and charts."*
- Its state is persisted in `WKO4.wko5home`, top-level block **2011** (window / layout state), under the key
  `sportfilter`. **In the user's saved file it is empty**, meaning no sport restriction (INFERRED: empty = all sports).
  The same block stores the selected date-range preset (`exploreworkouts` / `filterwidget` = `Last 30 Days`)
  and the active dashboards (`athletedocument` = `WKO5 Season View\t負荷與恢復`).
- The athlete's sport-group list is athlete file `3001 → 3221` (`Other, Road Bike, Run, Strength,
  Swim, Walk`), read by `PKHomeDocument` @0x56e395. Filtering is by **sport group**, not sport type
  (INFERRED from the list contents; the filter application path wasn't traced).
- No sport filter is needed to reproduce the user's screenshots: *all sports* with the rule in §TL;DR
  matches best (see the table in §5).

## 4. Athlete-bar PMC snapshot

- Stored twice: athlete file **3403** (`atl`, `ctl`, `phenotype`, `ramp`, `tsb`) and `WKO4.wko5home`
  **2100 → 2101 → 2145** (plus `frc`, `mftp` 175.5557, `vo2maxkg` 41.766, each with a unit string).
- Computed in the loop @0x5658ca over a name table (@0x8e5318). For each name it calls
  `athlete->[+0x94](name)`, which returns the built-in variable (`ctl := tl(tss, ctlconstant)` etc.), then `->[+0x10]`
  evaluates it in the **plain athlete context** (no RHE sport filter) and stores the result via @0x573600.
  The athlete-bar UI (@0x5d3220) only reads these cached values and colours them using thresholds at
  `athlete+0x178…0x190`.
- Snapshot time: home `2147` = day 45927 + 13,622,000 ms → **2026-09-29 11:47:02 local**.
  Every `.wko4` was already on disk (last written 10:53:20), so the snapshot is **not** mid-sync.
- The evaluation day is **2026-09-29** (VERIFIED): `ctl_y = ctl·42/41 = 18.046`,
  `atl_y = atl·7/6 = 12.141`, and `ctl_y − atl_y = 5.905`, which equals the stored `tsb`.
- Home `2108 = 1096` workouts vs 1098 in the index. The two extras have index flag `4023 = 2`
  (runs on 2026-08-31 and 2026-09-24). Excluding them does **not** improve the fit, so 4023's meaning is unknown.

## 5. Results

| Rule (tl linear, inputs 0..5000) | Sports | Snapshot err (ctl+atl) | Screenshot mean ΔCTL / ΔTSB |
|---|---|---|---|
| power → rTSS → **TP tss** → hrTSS | **all** | **8.4e-3** | **0.32 / 0.70** |
| power → rTSS → TP tss → hrTSS | no Walk | 0.54 | 0.91 / 0.31 |
| power → rTSS → hrTSS (old) | all | 8.32 | 12.01 / 9.93 |
| power → rTSS → hrTSS (old) | no Walk | 0.60 | 0.98 / 0.31 |
| power → rTSS → hrTSS (old) | Run only | — | 2.76 / 1.39 |

Rejected hypotheses:
- a `tl()` cap other than 5000 (5000.0 confirmed in `tl` @0x70c6ad);
- a duration cutoff for hrTSS;
- `min` / `max` of hrTSS and TP tss;
- gating TP TSS on field 4075;
- removing the 4023-flagged workouts;
- any subset of sport groups or sport types with the old rule (best err 0.60).

## 6. Chart meta field 438

INFERRED, not traced. Field 438 lives in the chart's library-info record together with:
- 427 author (`WKO`, `Steve Palladino`),
- 428 description,
- 994 → 109 title,
- 423 keywords,
- 425 thumbnail,
- 433 / 434 grid size.

Its values (`''`, `All`, `Run`, `Bike`, `All with power`) are the Chart Library's sport category, used
for browsing. It is **not** a workout filter:
- charts tagged `All with power` still filter explicitly in their expressions (`if(sport="run", …)`);
- chart descriptions say they use "the sports and dates selected in the RHE".

Direct code references to tag 438 couldn't be isolated: tags are written through a table, and the immediate
`0x1b6` hits are exception-handler state numbers.

## 7. Implications for backend/engine/wko5expr

1. `dataset._metrics`: insert **TP tss (`.wko4` info 4038)** between rTSS and hrTSS.
   The athlete index doesn't carry it, so it has to be read from each `.wko4` info record.
2. Default the RHE to **all sports**. The viewer's sport checkboxes stay useful as a what-if tool.
3. For future COROS / Garmin imports without TrainingPeaks there is no TP tss. WKO5 would then use hrTSS,
   so numbers will differ from TP-synced WKO5 for such workouts. That is expected, not a bug.
