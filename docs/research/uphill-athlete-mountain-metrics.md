# Uphill Athlete methodology and mountain-specific metrics: research notes

Date: 2026-09-29. Scope: web research only, no code changes.

Why this exists: WKO5 is built for cycling first and running second. Its grade model
(ACSM, `(0.19v + 0.9vg)/0.19`, reconstructed in `backend/engine/algorithms/wko5_pace.py`)
and its hrTSS (Friel HR levels, reconstructed in `backend/engine/algorithms/wko5_hr.py`)
both break down on steep, long, loaded mountain days. This document collects what
Uphill Athlete (UA), its spin-off Evoke Endurance, and the primary literature say we
could compute instead.

How to read it. Each topic separates:

- **(a) Published formulas**: can be implemented as written and cited.
- **(b) Heuristics / rules of thumb**: coach experience or community convention, not research. Label them that way in the UI.
- **(c) Our recommendation**: what this project should do and why. These are our proposals, not sourced claims.

Source-quality notes:

- The uphillathlete.com **forum** pages returned HTTP 404 to our fetcher (the articles loaded fine). Forum quotes below come from search-engine extracts of those threads. They are marked `[forum, via search extract]`. Re-read them in a browser before quoting them in the product.
- Several UA articles have been rewritten recently (bylines dated June 2026). The protocol numbers below are from the current live versions. Older versions and Evoke Endurance differ in places, and those differences are noted.
- Scott Johnston co-founded UA and left in September 2022 to start Evoke Endurance. He wrote most of the original TSS guidance. Evoke's pages are his current position; UA's pages are Steve House's team's current position.

---

## 1. Uphill Athlete TSS adjustments for mountain days

### 1(a) The published rule: exact wordings from three sources, which do not agree

| Source | Exact wording (vertical) | Exact wording (load) |
|---|---|---|
| UA article "TrainingPeaks Metrics for Mountain Athletes", 2017-04-09 — https://uphillathlete.com/aerobic-training/trainingpeaks-metrics-ctl-tss/ | "Without significant weight: Use the TrainingPeaks hrTSS and add 10 TSS for each 1,000 feet (300 meters) of elevation **gain and loss**." | "Carrying more than 10 percent of body weight: Add an additional 10 TSS per 10 percent of body weight carried, per 1,000 feet (300 meters) of **gain**." |
| UA "Making the Most of Your Uphill Athlete Training Plan" — https://uphillathlete.com/making-the-most-of-your-uphill-athlete-training-plan/ | "For each 1,000 feet (300 meters) of vertical **gain**, we add 10 to the hrTSS." | "For every 10 percent in body weight that is carried, we add another 10 to the TSS per 1,000 feet of vertical." |
| Evoke Endurance (Scott Johnston), 2023-04-20 — https://evokeendurance.com/resources/a-new-and-better-look-at-training-peaks-metric/ | "Add 10TSS to a workout for every 300 meters or 1000 feet of elevation **gain and loss**." | "In addition, add 10TSS for every 10% of body weight carried for every 300 meters or 1000 feet" |

Evoke gives the reason for the fudge factor: heart rate does not reflect the effort or
fatigue of steep uphill hiking, and it drops on descents "at a rate that does not reflect
the fatigue that comes from running downhill"
(https://evokeendurance.com/resources/a-new-and-better-look-at-training-peaks-metric/,
paraphrased from the search extract). Evoke also says outright that these are
coaching-derived fudge factors, not physiology.

### 1(b) Does "gain and loss" mean gain + loss, or just gain?

The published texts are ambiguous. The best disambiguating evidence is UA's
**machine rule** [forum, via search extract]:

> "On a treadmill or stairmaster, discount the elevation gain by 50% before applying the
> fudge factor. For example, if the total elevation gain is 600 m on a treadmill, then only
> consider 300 m," because a machine gives "just an elevation gain and no loss."
> — https://uphillathlete.com/forums/topic/tss-fudge-factor-when-using-machines/ and
> https://uphillathlete.com/forums/topic/adjusting-tss-when-using-stairmaster/

This rule only makes sense if the unit "1,000 ft of gain and loss" means **1,000 ft
up plus the matching 1,000 ft down**, scored 10 TSS in total. If each 1,000 ft of gain
and each 1,000 ft of loss scored 10 separately, a treadmill would just score its gain
and no discount would be needed. So the self-consistent reading, which also reconciles
the "gain only" wording on the training-plan page, is:

```
vert_TSS = 10 × ((gain_m + loss_m) / 2) / 304.8
```

- On a loop (gain ≈ loss) this equals `10 × gain / 304.8`, which is the training-plan page's wording.
- On a treadmill (loss = 0) it gives exactly the 50% discount.

The literal alternative, `10 × (gain + loss) / 304.8`, doubles the adjustment on loops.
One third-party UA calculator takes a single "ascent/descent height" input
(https://pdragun.github.io/uphill-peaks-tss/), which does not settle the question.
**This is our inference, not an explicit UA statement.** Keep the divisor configurable.

Load term. The wording says "per 1,000 feet of **gain**" (no loss). It applies only
when the load is over 10% of bodyweight. The third-party calculator implements it
continuously once over the threshold:
`load_TSS = 10 × (load_kg / bw_kg / 0.10) × gain_ft / 1000`
(https://pdragun.github.io/uphill-peaks-tss/). It is not stated whether the first 10%
counts once the threshold is exceeded, so treat that as ambiguous. A search extract
also shows a stepped simplification (10 TSS/1,000 ft under 10% BW, 20 TSS/1,000 ft over),
but we could not trace it to a primary UA page.

### 1(b) Refinements for very long and multi-day outings

All from forum threads, via search extracts:

- **Keep long easy days as scored.** A user reported two ~11 h days at ~125 bpm giving ~450 hrTSS each (TSB −90, CTL +20) and asked whether to reduce them. Scott Johnston: "I would not un-skew those hrTSS. Long duration low intensity does have a pronounced training effect." — https://uphillathlete.com/forums/topic/hrtss-for-very-long-workouts/
- **Flat per-hour rates for trips** — https://uphillathlete.com/forums/topic/estimating-tss-for-a-16-hour-trip/
  - Hiking, including glacier travel: 40 TSS/h, then add the vertical and pack adjustments.
  - Technical climbing: 50 TSS/h.
  - Scrambling: 30 TSS/h.
  - Do not count long obvious breaks (e.g. half an hour on the summit).
  - For pitched climbing, halve the total time to account for standing at belays (6 h of pitches = 150 TSS).
- **Resting HR still scores.** "TrainingPeaks can score up to 30 hrTSS/hr even when doing nothing." At all-day hiking HR, athletes "can count on an hrTSS of 40/hour … 10 hours = 400 hrTSS." (hrTSS-for-very-long-workouts thread, as above.)
- **Consistency over accuracy.** Compare how you feel after a stair machine with how you feel after a real mountain day, and adjust (stairmaster thread, as above).

No UA or Evoke source we found gives a duration cap, a decay factor, or a
sleep/bivouac rule for multi-day trips beyond "exclude long breaks."

### 1(c) Recommendation

1. Implement `mountain_tss = hrTSS_moving + vert_TSS + load_TSS`.
   - Use the half-sum reading of gain and loss by default.
   - Store the three components separately so the UI can show the breakdown and the user can switch interpretations.
2. Gain and loss must come from the smoothed `_elevation` channel with hysteresis (e.g. only count a change after 3–5 m of reversal). Raw barometric or GPS noise inflates both gain and loss, and the adjustment is linear in them.
3. Pack weight is a per-workout manual field. We have no sensor for it.
4. Do not add the vertical adjustment on top of a power- or pace-based TSS that already models grade. UA's rule is defined against **hrTSS**, which is grade-blind.

---

## 2. UA's broader training framework: the parts that produce numbers

### 2(a) Zones: anchored on AeT and AnT, not LTHR alone

From https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/
and https://uphillathlete.com/aerobic-training/aerobic-anaerobic-threshold-self-assessment/:

| Zone | HR range | Talk test / RPE (UA wording) |
|---|---|---|
| Z1 | AeT −20% to AeT −10% | "walk/jog pace", RPE 2–4 |
| Z2 | AeT −10% to AeT | a breath every 4–5 sentences, RPE 5–6 |
| Z3 | AeT to AnT (LT) | 3–4 words per breath, RPE 7–8 |
| Z4 | AnT to HRmax | "one four-letter word", RPE 9–10 |

TrainingPeaks setup: "Manually enter your heart rate for the top of Zone 2, which is the
Aerobic Threshold (AeT)… Set the top of Zone 1 at 10 percent below that"
(https://uphillathlete.com/making-the-most-of-your-uphill-athlete-training-plan/).
The percentages are percentages of the AeT heart rate.

### 2(a) AeT test: heart-rate drift (Pa:HR) protocol

Current UA version (Steve House, dated 2026-06-08) — https://uphillathlete.com/aerobic-training/heart-rate-drift/

- **Warm-up:** 10–15 min.
- **Test:** 40–60 min at a steady conversational effort. For returning athletes, start "just below your last known Aerobic Threshold."
- **Terrain:** treadmill at 3% for jogging, 10%+ for hiking, or a flat track. "Trails have too many pitch changes."
- **Drift:** first-half versus second-half comparison, worked example `((151/144) − 1) × 100%`. TrainingPeaks' Pa:HR does this automatically.
- **Bands:**
  - 0–3.5%: below AeT. Retest starting 5 bpm higher.
  - 3.5–5%: "You have determined your AeT heart rate… Set that as the top of Zone 2."
  - Over 5%: started above AeT. Retest lower.

Other versions of the protocol:

- Evoke's version is a 60 min constant pace on a treadmill at 2% (runners) or 15% (hikers). AeT is where HR "climbs [no] more than 5% during an hour" (https://evokeendurance.com/resources/our-latest-thinking-on-aerobic-assessment-for-the-mountain-athlete/).
- UA's field-test article uses 60 min, 2–3% for runners and 10–15% for hikers, with a 5% cut-off (https://uphillathlete.com/aerobic-training/are-you-actually-getting-fitter-simple-field-tests-for-mountain-athletes/).
- **Where they disagree:** the duration (40–60 vs 60 min), the hiking grade (10% vs 10–15% vs 15%), and whether 3.5% is a lower band. The 5% upper limit is the one constant.

Other AeT methods UA lists: MAF (180 − age, ±5–10 bpm), blood lactate, gas exchange, and
"Continuous AeT" (https://uphillathlete.com/aerobic-training/aerobic-anaerobic-threshold-self-assessment/).
UA says "we no longer treat nose breathing or the talk test as a way to find your AeT" for
untrained people (same page). Evoke still offers nose breathing as a low-tech check.

### 2(a) AnT test

Go "as hard as you can for between 30 and 60 minutes", in the event-specific mode
(steep uphill hiking for mountaineers). The average HR excluding warm-up is AnT
(https://uphillathlete.com/aerobic-training/aerobic-anaerobic-threshold-self-assessment/,
https://uphillathlete.com/aerobic-training/are-you-actually-getting-fitter-simple-field-tests-for-mountain-athletes/).
Evoke allows 20 min for beginners up to 1 h for elites
(https://evokeendurance.com/resources/our-latest-thinking-on-aerobic-assessment-for-the-mountain-athlete/).

Note for us: this is the classic 30 min TT average, and Friel's LTHR is the last 20 min of a
30 min TT. The two are close but not identical, and our LTHR setting may come from
either.

### 2(b) Aerobic Deficiency Syndrome (ADS): the 10% rule

- "If the spread between your AeT and AnT heart rates is greater than 10 percent, you have Aerobic Deficiency… If the spread is 10 percent or less, you have earned the right to add Zone 3 and Zone 4 work." — https://uphillathlete.com/aerobic-training/when-to-add-intensity-training/
- Worked example on the same page: "Divide your AnT heart rate by your AeT heart rate … AeT 128, AnT 150 → 150 ÷ 128 = 1.17. Your AnT is 17 percent greater than your AeT."
- It can be measured "by heart rate or pace" — https://uphillathlete.com/aerobic-training/aerobic-deficiency-syndrome/ (2018-02-02).
- **The pages use two different formulas.** The zones page says "when your AeT is more than 10 percent below your AnT", which is `1 − AeT/AnT > 0.10`, not `AnT/AeT − 1 > 0.10`.
  - Example: AeT 136, AnT 150 gives 10.3% on the first formula (ADS) and 9.3% on the second (not ADS). The disagreement band is roughly AnT/AeT between 1.10 and 1.111.
  - Recommendation: use UA's worked example (`AnT/AeT − 1`), because it is the only one with numbers, and show the value rather than just a pass/fail flag.
- Status: this is a **coaching heuristic**. We found no peer-reviewed validation of the 10% threshold.

### 2(b) Continuous AeT (UA's automated estimator)

- A four-week moving estimate of AeT from routine training HR vs pace/power drift. The algorithm is not published: "Continuous AeT needs… about four weeks of training with heart rate data before the trend stabilizes" (https://uphillathlete.com/aerobic-training/continuous-aet-how-and-why-it-works/, Steve House, 2026-06-12).
- Reported validation: against 65 hand-scored files, the mean error was 0.1 bpm, but only 62% were within 5 bpm, and outliers were as far as 17 bpm off (search extract of the UA Continuous AeT pages).
- Failure modes UA lists (https://uphillathlete.com/aerobic-training/when-continuous-aet-is-wrong/, 2026-06-30):
  - Heat. An example showed 9–10% drift on easy runs.
  - Rolling terrain without grade correction.
  - Short runs where HR is still catching up. The page's recommended minimum reads as roughly 49+ min.
- The same page says to cross-check against at least three independent methods.

### 2(b) Volume progression rules: what "8-week" actually is

- "Greater than an average of 10 percent progression in volume leads to trouble in roughly eight weeks" — https://uphillathlete.com/tactical-training/transition-period-training-tactical/. This is the "8-week" rule. **We found no UA "8%" rule.** The number is 10% sustained, with trouble appearing at about 8 weeks.
- Start each new cycle at 50% of the previous cycle's average weekly aerobic volume, then "bump up… about 10 percent per week" — https://uphillathlete.com/making-the-most-of-your-uphill-athlete-training-plan/.
- A more conservative figure for expedition builds: "roughly 3 to 5 percent per week," over 6–8 months — https://uphillathlete.com/mountaineering/fit-to-climb-everest/.
- Zone 2 hrTSS rate: "In general, we see about 60 hrTSS/hour for workouts in Zone 2" (training-plan page).

### 2(b) How UA uses CTL/ATL/TSB differently from cyclists

- **CTL is a volume proxy, alongside hours and climb rate.** "We use rates of climb in a similar way that it might be used to indicate a cyclist's power… We also evaluate the total hours spent training" (Johnston, https://www.trainingpeaks.com/blog/low-intensity-training-for-mountaineers-a-qa-with-uphill-athletes-scott-johnston/).
- **Fixed TSS values for non-HR work** (https://uphillathlete.com/aerobic-training/trainingpeaks-metrics-ctl-tss/):
  - Muscular endurance (ME) workouts: 150–200 TSS for slow-twitch-dominant athletes, 100 for fast-twitch.
  - General strength: 50–70 TSS/h.
  - Max strength: 80–90 TSS/h.
  - The training-plan page gives 60 / 80 / 100–150 per workout.
  - The reason: "Heart rate is essentially meaningless as a measure of how hard a strength workout is."
- **CTL benchmarks, as historical observations, not targets** (https://uphillathlete.com/mountaineering/fit-to-climb-everest/):
  - Denali: around 75 for 2 months or more.
  - Everest with oxygen: around 100 for 3 months.
  - Everest without oxygen: 125 or more.
  - The page's own caveat: "These are historical observations, not predictive targets." The 2017 article: "CTL 40 to 50 are not ready for big mountain adventures."
  - Johnston (Evoke, 2023) has since stepped back from CTL predictions: he will "no longer try to predict fitness for people whose training I am unfamiliar with."
- **No TSB rules.** We found no UA-specific TSB target or taper rule. TSB is defined only as CTL − ATL.

### 2(c) Recommendation

- Store AeT and AnT per sport and per date, separately from WKO5's LTHR, and derive UA zones from them.
- Compute `ads_spread = AnT/AeT − 1` and show it as a trend line.
- Build a **drift-test detector**, not only a manual test:
  - Qualifying segments: 40–60 min or longer, after a ≥10 min warm-up, with low variance in grade and speed (or on a treadmill).
  - Decoupling = `(v/HR)_first half / (v/HR)_second half − 1`, where v is a grade-adjusted speed (§4). On steep hiking segments, use vertical speed instead.
  - Apply the 3.5% and 5% bands, and exclude hot days if temperature is available.
- Existing-code observation (no change made): `compute_hr_drift` in `backend/engine/algorithms/trail.py` computes `HR / pace(s/m)` per half. That is HR × speed, not Pa:HR = speed / HR.
  - A constant-HR slowdown therefore produces **negative** "decoupling". UA/TrainingPeaks would show it as positive drift.
  - Worth checking before building on it.
- Add a weekly-volume ramp flag: rolling 4-week average weekly hours growing more than 10% per week, sustained for 8 weeks or more.

---

## 3. Vertical-specific metrics WKO5 does not compute

### 3(a) VAM (vertical ascent speed)

- Definition: `VAM = metres ascended × 60 / minutes` (m/h). The term was coined by Michele Ferrari for cycling (https://en.wikipedia.org/wiki/VAM_(bicycling)).
- Ferrari's cycling-only estimate: `W/kg ≈ VAM / (200 + 10 × grade%)` (same source). It includes bike mass and rolling losses, so **do not use it for foot travel**.
- Physics floor for any mode: `W/kg (lifting only) = VAM × 9.81 / 3600 = VAM / 367`.
- For running or hiking on grades of 20% or more, Minetti's measured vertical cost is about 44–47 J·kg⁻¹ per vertical metre (§4), so `metabolic W/kg ≈ VAM × 45 / 3600 ≈ VAM / 80`. For example, 1,000 m/h is about 12.5 W/kg metabolic. That estimate is ours, derived from Minetti 2002.

### 3(b) Vertical-speed benchmarks and "zones"

- **No published VAM zones.** We found no UA or peer-reviewed VAM zones. UA's approach is personal benchmarks: "Run or hike segments periodically at a standardized heart rate, specifically your AeT heart rate, and track the time" (search extract, https://uphillathlete.com/forums/topic/vertical-ascent-per-hour/). Johnston uses "rates of climb" the way cyclists use power (TrainingPeaks Q&A above).
- **Guide-service rules of thumb:**
  - About 1,000 vertical ft/h (~300 m/h) with a ~20 lb pack is typical on Rainier.
  - The advice is to train until 1,500–2,000 ft/h (450–600 m/h) is comfortable (https://www.rmiguides.com/blog/2023/10/22/mountaineering_training_using_benchmarks).
- **Classical hiking-time rules:**
  - Naismith (1892): 1 h per 5 km plus 1 h per 600 m of ascent.
  - Langmuir descent corrections: −10 min per 300 m on 5–12° descents, +10 min per 300 m on descents steeper than 12°.
  - SAC planning rates: 400 m/h ascent, 800 m/h descent, 4 km/h horizontal.
  - Sources: https://en.wikipedia.org/wiki/Naismith%27s_rule, https://de.wikipedia.org/wiki/Leistungskilometer.

### 3(a) Vertical Kilometre (VK) energetics and pacing

- **Minetti 2002:** the vertical cost of uphill running is at its minimum (44.9 ± 3.8 J·kg⁻¹·m_vert⁻¹) over gradients of 0.20–0.40. The "optimum gradient for mountain paths is close to 0.20–0.30" (text extracted from the paper PDF, https://www.skyrunning.com/wp-content/uploads/2020/05/Scientific-Research.pdf; journal page https://journals.physiology.org/doi/full/10.1152/japplphysiol.01177.2001).
- **Giovanelli et al. 2016:** at a fixed vertical speed of 0.35 m/s, metabolic cost is minimised at inclines of 20.4°–35.0° (about 37–70% grade). This is steeper than Minetti's range (https://journals.physiology.org/doi/full/10.1152/japplphysiol.00546.2015, https://www.researchgate.net/publication/284729239_Energetics_of_vertical_kilometer_foot_races_Is_steeper_cheaper).
- **Ortiz, Giovanelli & Kram 2017:** at 30°, walking is metabolically cheaper than running for the same speed. At 9.4° the difference was not significant. The fastest VK courses are about 30° (https://link.springer.com/article/10.1007/s00421-017-3677-y; summary via search extract).
- **Practical conclusion:** above about 20–25% grade, the cost per vertical metre is roughly flat, and the horizontal component stops mattering. **Vertical speed becomes the natural intensity metric** on steep ground, and GAP-style horizontal pace becomes a poor one. There are no published VK "pacing formulas" beyond this.
- **Kilian Jornet / Tobias Mews:** we found no published formula from either, so neither is used here.

### 3(a/b) Grade-adjusted pace models for steep terrain

| Model | Basis | Range / known problems | Source |
|---|---|---|---|
| ACSM running `VO2 = 0.2v + 0.9vG` (WKO5 uses 0.19) | Linear regression, mostly shallow grades | At +30% the implied cost ratio is 2.42× flat, against Minetti's measured ~3.5×. The vertical term implies about 50% muscular efficiency, against a measured 22–24%, so it **under-counts** steep running cost. For *walking*, ACSM is reported to **overestimate** at 30–40% grade (up to 18%). Mechanics change above ~15–18%. | Our calculation below; walking overestimate summarised at https://www.sciencedirect.com/science/article/pii/S2666337626000326 |
| Minetti 2002 polynomial | Measured VO2, 10 elite mountain runners, −45% to +45% | Downhill speeds it predicts are "far lower than metabolically feasible" in practice, i.e. real runners are much slower (safety, control) | §4 |
| Strava GAP (2017) | HR-equivalence fitted to 240k athletes / 6M runs | Much more credit for downhill running than the old Minetti-based model. Peak benefit near −10%, and no more than ~10% speed benefit downhill. Coefficients not published; reverse-engineered | https://medium.com/strava-engineering/an-improved-gap-model-8b07ae8886c3 (403 to our fetcher; summary via search), https://aaron-schroeder.github.io/reverse-engineering/grade-adjusted-pace.html |
| Ultrapacer | Empirical quadratic `factor = 0.0021 g² + 0.034 g + 1` (g in %) | Empirical | https://educatedguesswork.org/posts/grade-vs-pace/ |
| Lankford et al. 2020 (walking) | Measured, 1–3 mph, −18% to +40% | Most accurate against ACSM, Pandolf, Minetti and LCDA (adj. R² 0.89) | https://pubmed.ncbi.nlm.nih.gov/32656608/ |
| LCDA / Looney et al. 2019 (walking) | Military load-carriage aid | Better than Pandolf, Santee and Minetti in their test. Handles the minimum-cost-then-rising downhill curve | https://www.semanticscholar.org/paper/Estimating-Energy-Expenditure-during-Level,-Uphill,-Looney-Santee/f50e5bc3f5283b768e1543e68b19d0e40c0e000c |
| Pandolf 1977 (+ Santee correction) | Load carriage | Pandolf goes below basal cost on steep downhill. Santee fixes that to about −12% but overcorrects beyond. The original under-predicts modern military loads | https://en.wikipedia.org/wiki/Pandolf_equation, Looney summary above |

Other evidence:

- Lemire et al. 2021: running economy at ±20% does not correlate with level economy on the steep uphill. Downhill cost relates to knee-extensor strength (https://pmc.ncbi.nlm.nih.gov/articles/PMC8281813/). One grade curve does not fit every athlete.
- The "educatedguesswork" analysis found one runner's own data "noticeably slower on the downhills and noticeably faster on the uphills than any of the other models" (https://educatedguesswork.org/posts/grade-vs-pace/). That supports **fitting a personal curve**.

### 3(b) Equivalent flat distance and effort per hour: where EP = km + D+/100 comes from

| Convention | Formula | Descent? | Source |
|---|---|---|---|
| **ITRA km-effort** | `km + D+(m)/100` | No | https://www.finishers.com/en/articles/itra-points-everything-you-need-to-know-about-how-it-works-and-how-to-earn-them ("100 mD+ = 1 km-effort") |
| **Taiwan EP / EPH** (the app's existing charts) | `EP = km + D+/100`, `EPH = EP / hours` | No. The article names this as a blind spot ("不計算下坡", "does not count descent") | 健行筆記 (hiking.biji.co), 2025-08-21: https://hiking.biji.co/index.php?act=info&id=24918&q=news. Described as taken over from trail-running circles, i.e. the ITRA convention |
| Swiss **Leistungskilometer** | `km + D+/100 + (steep descent D−)/150`, where steep means more than 20% | Yes, steep sections only | https://de.wikipedia.org/wiki/Leistungskilometer (cites BASPO). Planning rate: 10–15 min per Leistungskilometer |
| Scarf's equivalence (Naismith) | `x + 7.92 y`, i.e. about 126 m of climb = 1 km | No | https://en.wikipedia.org/wiki/Naismith%27s_rule |
| Yamamoto **course constant** (Japan) | `1.8·h + 0.3·km + 10.0·D+(km) + 0.6·D−(km)`. Energy: `kcal ≈ CC × (bodyweight + pack kg)`. Water loss: the same number in mL | Yes | https://www.yamakei-online.com/yama-ya/detail.php?id=363. Bands: ~10 easy, ~20 average, ~30 strong day-hiker, 40+ needs an overnight. Derived from portable metabolic measurements (https://business.ntt-west.co.jp/bizclip/articles/bcl00020-045.html) |

EPH reference values are anecdotal. The 健行筆記 article gives low-mountain examples
and says EPH drops above 3,000 m (the search extract suggests about 8 falling to 6 or
below). They are not normative.

**Energetic check (ours):**

- With Minetti's running cost, 100 m of climbing costs about 4.5 kJ/kg (45 J/kg/m_vert), and 1 km of flat running costs 3.4–3.6 kJ/kg. So "100 m = 1 km" is close to running energetics.
- For walking, flat cost is about 2.5 kJ/kg per km (Cw at its optimum), so 100 m of climbing is worth about 1.8 flat km. **EP under-weights climbing for hikers.**
- Scarf's 126 m sits in between.

### 3(c) Recommendation

- Add **VAM per climb segment**. `trail.compute_vam` already exists.
- Add a **vertical mean-max curve**: the best gain in 5/10/20/30/60/120 min, which is the vertical analogue of a power-duration curve.
- Add **VAM at AeT**: VAM on segments with grade ≥ 15% and HR in the top of Z2. This operationalises UA's fixed-HR benchmark climb.
- Keep **EP/EPH** exactly as the app's existing charts define them (ITRA convention) for continuity. Add two siblings:
  - `EP_desc`, the Leistungskilometer-style variant with steep descent/150.
  - `EFD_minetti`, an energy-based equivalent flat distance (§4).
- Compute Yamamoto's course constant as an independent energy/water estimate. It is the only published mountain formula here that includes time, distance, gain *and* loss, and it is derived from metabolic measurements.

---

## 4. Minetti et al. 2002: energy cost of walking and running on gradients

Citation: Minetti AE, Moia C, Roi GS, Susta D, Ferretti G. *Energy cost of walking and
running at extreme uphill and downhill slopes.* J Appl Physiol 93:1039–1046, 2002.
PubMed https://pubmed.ncbi.nlm.nih.gov/12183501/. Full text as distributed by the
International Skyrunning Federation (a co-sponsor of the study):
https://www.skyrunning.com/wp-content/uploads/2020/05/Scientific-Research.pdf.
The coefficients below were extracted from that PDF's text and cross-checked against
https://aaron-schroeder.github.io/reverse-engineering/grade-adjusted-pace.html.

### 4(a) The polynomials

`i` = gradient as a decimal (rise / horizontal run, i.e. tan θ). C is in J·kg⁻¹·m⁻¹,
per metre travelled **along the surface**. Both fits have R² = 0.999 and are valid for
−0.45 ≤ i ≤ +0.45.

```
Cr(i) = 155.4 i^5 − 30.4 i^4 − 43.3 i^3 + 46.3 i^2 + 19.5 i + 3.6     (running)
Cw(i) = 280.5 i^5 − 58.7 i^4 − 76.8 i^3 + 51.9 i^2 + 19.6 i + 2.5     (walking, minimum over speeds)
```

Key measured facts (paper text):

- Level Cr = 3.40 ± 0.24 J/kg/m, independent of speed. The polynomial's intercept is 3.6. Use Cr(0) = 3.6 for self-consistent ratios.
- Level minimum Cw = 1.64 ± 0.50 at about 1.0 m/s.
- Minimum cost: Cw 0.81 at −0.10, and Cr 1.73 at −0.20. The minimum is similar for both gaits at −0.10 to −0.20.
- At +0.45: Cw 17.33 and Cr 18.93. At −0.45: Cw 3.46 and Cr 3.92.
- Above +0.15, cost is proportional to slope, with muscular efficiency 0.243 (walking) and 0.218 (running). Below −0.15, efficiency is about −1.2 (negative work).
- The ±0.15 boundary is where the pendulum (walking) and bouncing-ball (running) mechanisms are lost.
- Vertical cost of uphill running is at its minimum, 44.9 J/kg/m_vert, at 0.20–0.40. Downhill minimum is 9.2 at −0.20 to −0.40.
- "The running speeds adopted in downhill competition are far lower than metabolically feasible, mainly because of safety reasons."
- Subjects: 10 elite mountain runners, treadmill, steady state.

Our spot check of the polynomials against the paper's table: Cr(+0.45) = 19.4 (measured
18.93), Cr(−0.20) = 1.80 (1.73), Cw(+0.45) = 17.6 (17.33). The fit is good.

**Comparison with WKO5's ACSM factor** `(0.19 + 0.9 i)/0.19` (our calculation):

| i | −0.30 | −0.20 | −0.10 | 0 | +0.10 | +0.20 | +0.30 | +0.40 |
|---|---|---|---|---|---|---|---|---|
| Minetti Cr/Cr(0) | 0.68 | 0.50 | 0.60 | 1.00 | 1.66 | 2.50 | 3.49 | 4.68 |
| ACSM factor | −0.42 | 0.05 | 0.53 | 1.00 | 1.47 | 1.95 | 2.42 | 2.89 |
| Minetti vertical cost (J/kg/m_vert) | 8.6 | 9.2 | 21.6 | – | 60.0 | 45.9 | 43.8 | 45.4 |

What the table shows:

- ACSM goes negative below about −21%, so it is invalid downhill.
- It is about 20% low at +20% and about 30% low at +30% compared with measured running cost.

### 4(a) Turning it into GAP, metabolic power, and an "effort distance"

Given a per-second horizontal distance increment `dx` and rise `dh` (from smoothed elevation):

```
i        = dh / dx                                  (clamp to [-0.45, 0.45])
ds       = sqrt(dx² + dh²)                          (surface distance, Minetti's basis)
v        = ds / dt
P_met    = C(i) · v                                 [W/kg]   metabolic power
v_eq     = P_met / C(0) = v · C(i)/C(0)             [m/s]    flat-equivalent speed → GAP
EFD      = Σ ds · C(i)/C(0)                         [m]      energy-equivalent flat distance
E        = Σ C(i) · ds · body_mass                  [J]      gross energy above what the paper measured
```

Choice of C:

- Use Cr for running pace equivalence.
- For hiking, Cw is a *lower bound*, because it is the minimum over speeds. At grades over about 0.25, Cw ≈ Cr, so the choice matters little on steep ground.
- Minetti also gives `v_max,i = P_max / Cr(i)` and `v_vert = v · sin(arctan i)` for predicting maximal speed on a slope.

### 4(c) Recommendation

Minetti is the best-grounded **uphill** replacement for ACSM up to ±45%.

- **Downhill, do not use raw Minetti for HR-equivalent pace.** It gives, for example, a 2× "speed credit" at −20%. People do not run that fast downhill, and Strava's HR-fitted model caps the benefit at about 10%.
- Proposed hybrid, `C_hybrid(i)`:
  - For i ≥ 0: Minetti Cr.
  - For i < 0: `max(Cr(i), Cr(0) × f_down(i))`, with `f_down` fitted from the athlete's own HR-vs-speed data. The starting default has a minimum of 0.90 near −10%, rising back to 1.0 at about −25%.
- **Validation:** bin all steady samples by grade and regress HR on `v_eq` (after a lag of about 60 s). The best grade model is the one whose HR/v_eq residual has the least grade dependence. Run this for ACSM, Minetti, hybrid, and a personal polynomial fit.

---

## 5. Load metrics for very long, low-intensity days

### 5(a) What hrTSS does, and why it over-counts multi-day trips

- TrainingPeaks: hrTSS "is based on time in heart rate training zones derived from an athlete's lactate threshold heart rate" and uses "an estimate of the amount of accumulated TSS in an hour, given the level of exertion." It is best for steady-state efforts (https://www.trainingpeaks.com/learn/articles/training-with-tss-vs-hrtss-whats-the-difference/). The ceiling is 100 TSS per hour (https://www.trainingpeaks.com/learn/articles/what-is-tss/).
- **Our reconstruction of WKO5's table** (`wko5_hr.py`, `HR_LEVELS`) makes the mechanism concrete. Every recorded second earns TSS, with no zero level:
  - At least 20 TSS/h at any HR.
  - 30 TSS/h at ≥ 27% of LTHR.
  - 40 TSS/h at ≥ 55% of LTHR.
  - Sleeping HR of about 50 bpm with LTHR about 160 is ≥ 27% of LTHR, so it earns **30 TSS/h**. This matches the UA forum's remark that TrainingPeaks "can score up to 30 hrTSS/hr even when doing nothing."
- A multi-day outing with two nights out (moving time about one seventh of recorded time): hrTSS on all recorded time came out about 3× the moving-time value. Most of the gap is camp and sleep time accruing 20–30 TSS/h.
- **The TrainingPeaks figure for the same file, about a tenth of the moving-time value, is not explained by any documented behaviour we found.**
  - Hypotheses to check in our TrainingPeaks sync data: the TSS type or source field (e.g. rTSS on trail, which TrainingPeaks' own help page "Low rTSS and Trail Running" admits runs low — https://help.trainingpeaks.com/hc/en-us/articles/205229730-Low-rTSS-and-Trail-Running, 403 to our fetcher), a truncated or partial upload, or a manually entered or planned value.
  - Do not treat that figure as ground truth.

### 5(b) Moving-time conventions

- UA forum: exclude "long obvious breaks", halve pitched-climbing time, and use 30/40/50 TSS/h by activity type (§1).
- Johnston: don't reduce genuine long easy days (§1).
- **Together this implies that moving time with HR scoring is right, and stationary time should be removed.** Rescaling or capping the moving part is not supported.
- **Duration caps or decay:** we found **no published or practitioner-standard duration cap or decay** for TSS/TRIMP. Any cap would be our invention.

### 5(a) Banister TRIMP and variants

- **Banister:** `TRIMP = minutes × ΔHR_ratio × 0.64 e^(1.92 × ΔHR_ratio)` for men; the female constant is 1.67 (https://www.veohtu.com/trimp.html).
  - `ΔHR_ratio = (HR − HRrest)/(HRmax − HRrest)`.
  - The primary literature and many summaries give the female form as `0.86 e^(1.67 x)`; the veohtu page shows 0.64. Verify against Banister/Morton before implementing.
  - It is still linear in time. With HR at rest, ΔHR_ratio is 0 and TRIMP is 0, so **TRIMP does not charge sleep**, unlike Friel hrTSS. This is a structural advantage for multi-day files.
- **Edwards TRIMP:** 5 zones at 50–100% of HRmax, weights 1–5. **Lucia TRIMP:** 3 zones bounded by VT1 and VT2 (≈ AeT and AnT), weights 1–3. Both are summarised at https://www.veohtu.com/trimp.html.
  - Lucia's zones map directly onto UA's AeT/AnT framework.
  - Edwards gives 0 below 50% HRmax, so sleep is not charged.
- **Normalisation:** HRSS/hrTSS = session TRIMP / (1 h TRIMP at LT) × 100 (veohtu). A TRIMP can therefore be put on the TSS scale for the PMC.

### 5(a) EPOC-based load (Firstbeat/Garmin) and why it plateaus

- Firstbeat: EPOC "doesn't continue to accrue during lower intensity periods if the body is able to grab some brief recovery and only reflects those moments when the intensity is greater than can be coped with." TRIMP, by contrast, "will still accrue if the heart rate is above resting levels" (https://www.firstbeat.com/en/professional-sports/learning-center/interpreting-training-data/).
- The white paper describes the mechanism. EPOC is predicted recursively from HR-derived %VO2max, respiration rate and time. At low intensity (under about 30–40% VO2max) it stops accumulating after the initial rise. Above about 50% VO2max it accumulates continuously, more steeply with intensity (https://www.firstbeat.com/wp-content/uploads/2015/10/white_paper_epoc.pdf; the thresholds are from the search extract of that paper).
- EPOC is a **state** (recovery debt), not an integral. A 10 h Z1 day therefore ends with a small EPOC. EPOC-based "Training Load" (EPOC per session, summed over 7 days) under-counts exactly the days UA says have "a pronounced training effect."
- The model uses neural-network intensity estimation and is proprietary, so it cannot be reproduced exactly.
- EPOC is the opposite bias to hrTSS. It is useful as a secondary "intensity stress" line, not as the main load number for mountaineers.

### 5(b) Mountaineering-specific guidance found

- UA per-hour rates and break exclusions (§1). Johnston's advice not to un-skew long days (§1).
- Yamamoto's course constant (§3). It includes time, horizontal distance, gain and loss with metabolically derived weights, and is intended for multi-day planning (40+ = needs an overnight). It is not a TSS, but it is a published, mountain-validated energy load.

### 5(c) Recommendation

**Primary load number for mountain days: `mountain_tss`.**

1. Compute hrTSS on **moving samples only**. `wko5_hr.hr_tss(moving=...)` already supports this.
2. Also score samples below the Z1 floor (AeT −20%) at **0 TSS/h**, not 20–30. This removes camp time that the moving mask misses, such as slow shuffling around camp.
3. Add UA's vertical term (half-sum gain/loss, §1) and load term.
4. **Split multi-day activities by calendar day** before feeding the PMC. A file spanning three calendar days must land on 3 days, or ATL/TSB spike on one day and ignore the others. Neither UA nor TrainingPeaks addresses this explicitly. It is our recommendation.
5. No duration cap. There is no source for one, and Johnston argues against reducing long days.

**Secondary lines, shown alongside for comparison:**

- Lucia TRIMP (AeT/AnT bands), normalised to the TSS scale.
- The Yamamoto course constant.
- Minetti energy (kJ) for the day.
- Where these disagree strongly with `mountain_tss`, flag the activity for review.

---

## 6. Prioritised implementation list

Available inputs (per second): elapsedtime, heart rate, speed, elevation, smoothed
`_elevation`, power, cadence, distance. Athlete settings: LTHR, threshold pace, FTP,
weight. **New settings needed:** AeT HR and AnT HR per sport, HRrest, HRmax,
per-workout pack weight, and an optional per-workout "pitched climbing" flag.

| # | Metric | Formula | Inputs | Validation |
|---|---|---|---|---|
| 1 | **hrTSS_moving** (+ sub-Z1 zeroing) | WKO5 Friel levels, summed only where `moving` is true and HR ≥ AeT·0.8. Samples below that floor score 0 | elapsedtime, HR, speed (moving mask), LTHR, AeT | On ordinary workouts without stops, equals WKO5 hrTSS (golden tests). On a multi-day file, falls toward (or below) the moving-time hrTSS and stays ≥ 40 TSS/h × moving hours ± 30% (UA rate) |
| 2 | **Vertical & load adjustment → mountain_tss** | `vert = 10 × ((gain+loss)/2)/304.8`; `load = 10 × (L/BW/0.10) × gain/304.8` if L/BW > 0.10; `mountain_tss = hrTSS_moving + vert + load` | smoothed `_elevation` with 3–5 m hysteresis, weight, pack weight | Hand-computed fixtures. Cross-check against https://pdragun.github.io/uphill-peaks-tss/. Treadmill case = half. Keep the divisor configurable (×2 literal reading) |
| 3 | **Per-day split of multi-day activities** | Split all channels at local midnight and compute each metric per day | elapsedtime, activity start time and time zone | Sum of daily parts = whole-activity value for additive metrics. PMC for a three-calendar-day trip shows 3 days |
| 4 | **Minetti GAP / metabolic power channel** | `i = dh/dx` (clamp ±0.45), `v_eq = v·Cr(i)/Cr(0)`, `P_met = Cr(i)·v`. Hybrid downhill floor (§4c) | speed/distance, smoothed `_elevation`, weight (for W) | Flat: v_eq = v. Reproduce the paper's table (Cr(0.45) ≈ 19.4). Grade-residual test: HR vs v_eq residual flatter than ACSM across grade bins |
| 5 | **Vertical metrics** | VAM per climb (existing `compute_vam`). Vertical mean-max `max Δh over window w`, w ∈ {5,10,20,30,60,120} min. VAM@AeT on segments with grade ≥ 15% and HR in [AeT−10%, AeT] | elapsedtime, smoothed `_elevation`, HR, AeT | Known VK or race results. Stable week-to-week for steady athletes. Monotone non-increasing mean-max curve |
| 6 | **AeT/AnT spread (ADS)** | `AnT/AeT − 1`. Flag when over 0.10. Also report `1 − AeT/AnT` | AeT, AnT settings (dated) | UA example: 128/150 gives 17.2% |
| 7 | **Drift test detector (Pa:HR)** | Qualifying steady segment of 40–60 min or more after a ≥ 10 min warm-up. `drift = (v/HR)_1 / (v/HR)_2 − 1`, with v = v_eq (or VAM when grade ≥ 15%). Bands < 3.5 / 3.5–5 / > 5% | HR, speed, smoothed `_elevation` | UA example: 151/144 gives 4.9%. Treadmill fixed-pace workouts give the same result as TrainingPeaks Pa:HR. Fix or bypass the sign issue in `trail.compute_hr_drift` first |
| 8 | **EP / EPH and siblings** | `EP = km + D+/100`, `EPH = EP/h_moving`. `EP_desc = EP + D−(steep > 20%)/150`. `EFD = Σ ds·Cr(i)/Cr(0)` | distance, smoothed `_elevation`, moving time | EP and EPH match the app's existing charts exactly. EFD ≈ EP on running-grade routes, larger on steep hikes |
| 9 | **Yamamoto course constant & energy** | `CC = 1.8·h + 0.3·km + 10·D+km + 0.6·D−km`. `kcal = CC × (BW + pack)` | moving hours, distance, gain, loss, weight, pack | Worked examples from https://www.yamakei-online.com/yama-ya/detail.php?id=363. Same order of magnitude as Minetti energy |
| 10 | **Lucia TRIMP (TSS-normalised)** | `TRIMP_L = Σ minutes × w`, w = 1 (AeT·0.8 ≤ HR < AeT), 2 (AeT ≤ HR < AnT), 3 (HR ≥ AnT), and 0 below AeT·0.8. The zero band is our addition, so sleep is not charged. `lucia_tss = TRIMP_L / 180 × 100`, so 1 h at AnT = 100 | HR, AeT, AnT | 1 h at AnT = 100. Check Z2 hours against UA's ~60 hrTSS/h rule of thumb. Compare against mountain_tss over 6 months (correlation, outliers) |
| 11 | **Volume ramp flag** | 4-week rolling mean weekly hours. Flag if average weekly growth > 10% for ≥ 8 weeks | daily moving hours | Synthetic ramps |
| 12 | (optional) **Banister TRIMP** | `min × x × 0.64 e^{1.92x}` (verify the female constants) | HR, HRrest, HRmax | Literature examples |

Sequencing rationale:

- Items 1–3 fix the load numbers that are visibly wrong today (multi-day trips), and they feed CTL/ATL/TSB.
- Item 4 underpins items 5, 7 and 8.
- Items 5–7 are the UA-specific fitness diagnostics WKO5 lacks.
- Items 8–12 are cheap additions and comparison lines.

---

## Source index

Uphill Athlete / Evoke Endurance:

- https://uphillathlete.com/aerobic-training/trainingpeaks-metrics-ctl-tss/
- https://uphillathlete.com/making-the-most-of-your-uphill-athlete-training-plan/
- https://evokeendurance.com/resources/a-new-and-better-look-at-training-peaks-metric/
- https://evokeendurance.com/resources/our-latest-thinking-on-aerobic-assessment-for-the-mountain-athlete/
- https://uphillathlete.com/aerobic-training/heart-rate-drift/
- https://uphillathlete.com/aerobic-training/aerobic-anaerobic-threshold-self-assessment/
- https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/
- https://uphillathlete.com/aerobic-training/aerobic-deficiency-syndrome/
- https://uphillathlete.com/aerobic-training/when-to-add-intensity-training/
- https://uphillathlete.com/aerobic-training/are-you-actually-getting-fitter-simple-field-tests-for-mountain-athletes/
- https://uphillathlete.com/aerobic-training/continuous-aet-how-and-why-it-works/
- https://uphillathlete.com/aerobic-training/when-continuous-aet-is-wrong/
- https://uphillathlete.com/tactical-training/transition-period-training-tactical/
- https://uphillathlete.com/mountaineering/fit-to-climb-everest/
- https://www.trainingpeaks.com/blog/low-intensity-training-for-mountaineers-a-qa-with-uphill-athletes-scott-johnston/

UA forum threads (via search extracts; direct fetch returned 404):

- https://uphillathlete.com/forums/topic/hrtss-for-very-long-workouts/
- https://uphillathlete.com/forums/topic/estimating-tss-for-a-16-hour-trip/
- https://uphillathlete.com/forums/topic/tss-fudge-factor-when-using-machines/
- https://uphillathlete.com/forums/topic/adjusting-tss-when-using-stairmaster/
- https://uphillathlete.com/forums/topic/vertical-ascent-per-hour/

Peer-reviewed:

- Minetti et al. 2002: https://pubmed.ncbi.nlm.nih.gov/12183501/ and https://www.skyrunning.com/wp-content/uploads/2020/05/Scientific-Research.pdf
- Giovanelli et al. 2016: https://journals.physiology.org/doi/full/10.1152/japplphysiol.00546.2015
- Ortiz et al. 2017: https://link.springer.com/article/10.1007/s00421-017-3677-y
- Lemire et al. 2021: https://pmc.ncbi.nlm.nih.gov/articles/PMC8281813/
- Lankford et al. 2020: https://pubmed.ncbi.nlm.nih.gov/32656608/
- Looney et al. 2019: https://www.semanticscholar.org/paper/Estimating-Energy-Expenditure-during-Level,-Uphill,-Looney-Santee/f50e5bc3f5283b768e1543e68b19d0e40c0e000c
- Pandolf: https://en.wikipedia.org/wiki/Pandolf_equation

Vendor:

- https://www.trainingpeaks.com/learn/articles/training-with-tss-vs-hrtss-whats-the-difference/
- https://www.trainingpeaks.com/learn/articles/what-is-tss/
- https://www.firstbeat.com/en/professional-sports/learning-center/interpreting-training-data/
- https://www.firstbeat.com/wp-content/uploads/2015/10/white_paper_epoc.pdf
- https://medium.com/strava-engineering/an-improved-gap-model-8b07ae8886c3

Conventions:

- ITRA km-effort: https://www.finishers.com/en/articles/itra-points-everything-you-need-to-know-about-how-it-works-and-how-to-earn-them
- 健行筆記 EP/EPH: https://hiking.biji.co/index.php?act=info&id=24918&q=news
- Leistungskilometer: https://de.wikipedia.org/wiki/Leistungskilometer
- Naismith/Scarf: https://en.wikipedia.org/wiki/Naismith%27s_rule
- Yamamoto course constant: https://www.yamakei-online.com/yama-ya/detail.php?id=363
- VAM: https://en.wikipedia.org/wiki/VAM_(bicycling)
- RMI benchmarks: https://www.rmiguides.com/blog/2023/10/22/mountaineering_training_using_benchmarks

Secondary / analysis:

- https://aaron-schroeder.github.io/reverse-engineering/grade-adjusted-pace.html
- https://educatedguesswork.org/posts/grade-vs-pace/
- https://pdragun.github.io/uphill-peaks-tss/
- https://www.veohtu.com/trimp.html
