# What coaches of mountain, ultra and trail athletes actually chart: research notes for a non-WKO5 dashboard

Date: 2026-09-29. Scope: web research only. No code changes.

Companion to `docs/research/uphill-athlete-mountain-metrics.md` (hereafter **UA-notes**). That
file covers UA's TSS fudge factors, the AeT drift test, the ADS 10% rule, VAM, grade models,
Minetti 2002, EP/EPH and load for long days. This file does not repeat those. It points to
the relevant UA-notes section instead.

The target user (the example runner) runs on roads and trails, hikes 百岳 (often multi-day,
with a pack) and cycles some of the time. The questions this file answers:

- Which charts should I look at?
- Which ones measure my ability and my progress?
- What do coaches of mountain athletes put on their dashboards?

## How to read this

- Every factual claim has a URL. Claims are tagged as follows:
  - **[validated]**: peer-reviewed research with a measured outcome.
  - **[coach practice]**: what a named coach says they do. This is experience, not evidence.
  - **[vendor]**: a feature description from a product site. Treat it as marketing until shown otherwise.
  - **[ours]**: our own proposal or inference. No source stands behind it.
- Sources we could not read are listed as such. We have not quoted a search snippet as if it were the source. Where a claim rests only on a search-engine extract, it is marked `(search extract)`.
- Each proposed chart has a spec block:
  - **Q**: the question the chart answers.
  - **X / Y**: the axes.
  - **Metric**: how the plotted value is defined.
  - **Data**: the inputs it needs.
  - **Source**: the coach or paper behind it.
  - **Impl**:
    - **(a)** we can build it from what we have: per-second time, HR, speed, elevation, distance, cadence, power when present, the Minetti cost model (`backend/engine/algorithms/minetti.py`), moving-time hrTSS, equivalent flat distance and the WKO5 PD model.
    - **(b)** it needs data we do not have.

### Sources that failed to load (not quoted as sources)

| URL | Result |
|---|---|
| https://www.trailrunnermag.com/training/trail-tips-training/kilian-jornet-training-data/ | 302 to an Outside login. A Yahoo syndicated copy was read instead (§1.5) |
| https://run.outsideonline.com/trail/jack-kuenzle-coached-caleb-olson-to-western-states-win/ | 302 to an Outside login. Only the search extract was available |
| https://pubmed.ncbi.nlm.nih.gov/33886100/, /40442924, /27543663 | PubMed cookie wall, no abstract returned |
| https://link.springer.com/article/10.1007/s40279-021-01459-0 (Maunder 2021) | Redirect to Springer auth |
| https://journals.humankinetics.com/view/journals/ijspp/17/6/article-p820.xml (Casado 2022) | HTTP 403 |
| https://help.trainingpeaks.com/hc/en-us/articles/204071724 (EF / Pa:HR help) | HTTP 403 |
| https://journals.physiology.org/doi/full/10.1152/japplphysiol.00556.2016 (Garvican-Lewis, "kilometer hours") | HTTP 403 |
| https://freetrail.com/plans/12-week-100-mile-training-plan/ | Loads, but the plan content is behind the membership wall |
| Mateo-March 2025 reliability PDF (fisiologiadelejercicio.com) | Binary PDF that we could not render. The PubMed-indexed summary came from a search extract |

---

## 1. What mountain, ultra and trail coaches actually track

### 1.1 Uphill Athlete (Steve House's team) and Evoke Endurance (Scott Johnston)

The UA/Evoke load and threshold framework is covered in UA-notes §1–§2. The additions below
come from UA's 2026 "Are you actually getting fitter?" field-test article:
https://uphillathlete.com/aerobic-training/are-you-actually-getting-fitter-simple-field-tests-for-mountain-athletes/

**What UA emphasises [coach practice]:**

- **HR drift test every 4–6 weeks.** 60 min at a constant speed and grade: 2–3% for runners, 10–15% for hikers. Drift should be under 5% between halves. "Match the test modality to the way you actually train."
- **AnT field test.** 30 min for new athletes, 45–60 min for experienced ones. Average HR excluding warm-up is AnT. Done rested.
- **The 10% rule**, AnT/AeT (UA-notes §2(b)).
- **Benchmark segments.** At least 15–20 min of sustained effort, repeated at AeT HR: "If you are covering the same segment faster at the same heart rate, your aerobic capacity has improved."
- **Resting HR and HRV as recovery signals, "not performance metrics".** Uses RMSSD. "Do not react to single-day readings… What matters is the trend over three to seven days."

**What UA says is limited or misleading for mountain athletes [coach practice]:**

- **GAP / NGP.** "Neither accounts for differences in surface type" and both "underestimate the total stress of downhill running". Use them "as a directional signal rather than a precise measurement".
- **Running power.** "Power values change significantly when you shift from running to walking". It suits athletes on runnable terrain. "For mountaineers and alpinists who are primarily hiking steeply under load, heart rate and ventilatory awareness remain the more practical and reliable tools."
- **Lab VO2max and metabolic-efficiency tests.** VO2max is "nice to have, but not need to have… knowing your VO2 max value doesn't directly tell you how to structure your training next week". Their motto is "The training itself is the test" (https://uphillathlete.com/aerobic-training/should-you-test/).
- **CTL as a readiness predictor.** Johnston has stepped back from CTL predictions (UA-notes §2(b)).

**Efficiency factor and decoupling.** UA uses EF, "pace or power divided by heart rate, in a
steady aerobic effort", comparing "identical zone-two workouts spaced eight weeks apart". A
flat EF over two cycles means the base phase has plateaued. Decoupling under 5% is "the
working benchmark for build-phase aerobic efforts"
(https://uphillathlete.com/aerobic-training/durability-training-efficiency-factor-and-decoupling-for-endurance-athletes/).

**Mountaineering readiness [coach practice]:**

- "At least one mountain climbing workout per week where you ascend 4,000 vertical feet in one day, with a backpack of approximately the same weight" as the climb (for Rainier).
- "The longest duration workout should make up 50% of the total weekly aerobic training volume."
- Pack load progresses from 0% of bodyweight through week 4 up to 15% by week 10, with a caution against more than 25% on long hikes.
- Source: https://uphillathlete.com/mountaineering/training-for-mountaineering/.

### 1.2 Jason Koop (CTS, *Training Essentials for Ultrarunning*)

**Tracks [coach practice]:**

- **Volume by time, not miles.** "Adaptation is driven by the amount of time you are exposed to a particular intensity, not by the mileage you have run" (https://trainright.com/revolutionize-your-run-training/).
- **Vertical per mile matched to the race, not total vertical.** He targets weekly aggregation within 10% of the race's ft/mile. Western States is about 417 ft/mile of elevation change (https://trainright.com/data-worth-tracking-ultrarunners/).
- **Post-workout subjective feedback**, "by far the most important" metric (same page).
- **ATL/CTL in TrainingPeaks** for "a big-picture view of historical training", plus NGP/GAP, Strava/WKO5 segments and NGP comparisons between intervals as fitness checks (https://trainright.com/ultramarathon-runner-toolkit-monitoring-training/).
- **Intensity budget.** High intensity is "maybe 20% of training sessions and only about 10% of total training hours" (https://trainright.com/hierarchy-ultramarathon-training-needs-jason-koop/).

**Says is flawed or not useful [coach practice]:**

- **Mileage** as a volume measure (revolutionize page).
- **HR as the intensity prescription.** "Prescribing trail run training by heart rate should not be the fallback", because heat, altitude and dehydration distort it. He prefers RPE (same page), and repeats "ultrarunners should embrace RPE" even over running power (https://trainright.com/what-ultrarunners-should-learn-from-cyclists-triathletes-and-mountain-bikers/).
- **Total vertical.** "There is no dose response related to amount of vertical gain or loss. Two thousand, 4000 or 8000 feet of vertical are all similarly meaningless to your body" (revolutionize page). This is a strong claim. We found no study behind it, so it counts as coach opinion.
- **Low-dose cross-training.** "A couple of hours of low intensity cross training, in the context of a training schedule that is ~10-15 hours per week, is simply not enough stress to be meaningful" (https://trainright.com/double-day-training-ultrarunner/).
- **Arbitrary long-run percentages.** He has coached successful athletes whose long runs were "as little as 20% and as much as 80%" of race distance. A long run must be at least 4 h to practise nutrition (https://trainright.com/longest-run-ultramarathon-training/).

**Priority order.** Koop ranks specificity (terrain, elevation profile) and altitude near the
top of his pyramid. That puts them *after* volume, rest, nutrition, intensity and gut
training (hierarchy page).

### 1.3 David Roche / SWAP

Published material on what SWAP puts on a dashboard is thin. What we could verify:

- A former athlete describes SWAP coaching as done through a Google Sheet, with no analysis of workout files (https://www.letsrun.com/forum/flat_read.php?thread=13195957&page=4, a forum anecdote, search extract). This is weak evidence.
- Roche's own training emphasis is running economy, aerobic development and progressive overload, plus strides and shorter long runs with some intensity. On outcomes he says: "I don't give a sh*t. If you show up prepared… the results will fall into place" (https://andrewskurka.com/david-roche-interview-ultra-road-marathon-training/).
- On vert: for a mountainous 50k, "make sure you're getting those types of vert ratios on your weekend runs" (Trail Runner 50k plan, https://www.trailrunnermag.com/training/trail-tips-training/an-advanced-50k-training-plan-for-trail-runners/, search extract; the page redirects to login). This is the same ratio idea as Koop's.
- On HR: he uses arm-based HR to calibrate perceived effort on easy and steady runs, calling it "imperfect" (https://x.com/MountainRoche/status/1845897118617424315, search extract).

**Takeaway:** SWAP is an RPE-and-consistency shop. It gives no evidence for or against any chart.

### 1.4 Jack Kuenzle

- The only fetchable detail is a search extract of the Outside article: he "caps the effort at the athlete's first lactate threshold" on base work, and Caleb Olson's Western States build never reached "hero volume" (https://run.outsideonline.com/trail/jack-kuenzle-coached-caleb-olson-to-western-states-win/, login wall).
- Implication **[ours]**: the chart that matters for this style is **time-in-zone relative to LT1/AeT**, which shows whether easy days stayed easy.

### 1.5 Kilian Jornet

From a syndicated copy of Trail Runner's 2022 data piece
(https://www.yahoo.com/news/eight-takeaways-kilian-jornet-2022-175715501.html):

- **Volume:** 1000+ h per year across running, skiing and cycling, about 20 h in most weeks.
- **Intensity distribution (5-zone):** Z1 58%, Z2 19%, Z3 16%, Z4 4%, Z5 3%.
- **Running volume in race blocks:** 150–200 km per week.
- He says "There's no such a thing as the magical session".

In 2019 he cut weekly vert and added flat km, yet "the uphill performance have been improving
a lot" (https://runningmagazine.ca/sections/training/what-you-can-learn-from-kilian-jornets-2019-training-log/).
That is an n=1 counterexample to "more vert = better climber".

His COROS profile says he trains "based on feel, and use[s] my heart rate to see if I have a
cardiac response" (https://coros.com/stories/more-than-splits/c/kilian-jornet-training).
**[vendor]**: COROS is his sponsor.

**Takeaway [ours]:** Jornet's public dashboard is **weekly hours by sport + weekly vert + zone
distribution**, with multi-sport hours added together.

### 1.6 Dylan Bowman / Freetrail

- We found nothing published about the metrics Freetrail coaches chart. Plan content is behind the paywall (https://freetrail.com/plans/12-week-100-mile-training-plan/).
- **Not usable as a source.**

### 1.7 Taiwanese and Japanese practice

- **江晏慶 (Garmin Taiwan blog, 2020).** Periodises by phase. Treats total climb and descent as a key reference. Warns that "越野跑的疲勞不容易透過當下心率來界定" ("trail-running fatigue is hard to judge from HR in the moment"): on descents HR stays low while the quads are failing. Suggests a 10–15% weekly step-down as a safe adjustment (https://www.garmin.com/zh-TW/blog/running/trail-run/). **[coach practice / vendor blog]**
- **RACE ON 鋭速運動醫學, 百岳練習生 series.** A data-driven readiness check for 百岳 (https://www.raceon.com.tw/zh-TW/blogs/news/199997) **[coach practice]**:
  - Pick a local hill with similar km and gain. The example is 雪山: about 6.5 km, 1010 m gain, about 155 m of climb per km.
  - Carry the planned pack and compare your time with the 上河 map time. Faster means ready. Slower means train more.
  - For multi-day 縱走, a 60-min continuous loaded walk at 6–10% grade.
  - Pack ratio bands: ≤10% of bodyweight for day trips, 10–20% for two days / one night, 20–30% for 3–7 days.
  - Target intensity: RPE 2–4 / 56–66% HRmax.
  - This is the only published Taiwanese protocol we found. The 上河時間 comparison maps directly onto a chart (§4).
- **Yuta Yamato (理学療法士, rb-rg.jp).** Repeats Koop's three shifts almost word for word: time not distance, RPE not HR, and gain per km rather than total gain (https://rb-rg.jp/blog/41386/). **[coach practice, derivative]**
- **Yamamoto course constant** (Japan): see UA-notes §3(b).

### 1.8 Where the coaches agree and disagree

| Topic | Consensus | Dissent |
|---|---|---|
| Volume unit | **Hours** (Koop, Jornet, UA, Yamato) | Stryd/Runalyze model volume in km or power, but they are road-centric |
| Intensity during sessions | RPE first (Koop, Roche, Jornet, 江晏慶) | UA and Kuenzle anchor zones on AeT/LT1 HR |
| Vertical | Match the race's **gain per km** (Koop, Roche, Yamato) | UA and 百岳 practice use **absolute big-day gain with pack** |
| CTL | Big-picture only (Koop). Not a predictor (Johnston) | Nobody uses TSB targets for mountain events |
| Fitness evidence | Repeated benchmark at fixed HR (UA), segments (Koop), 上河 time (RACE ON) | None. **This is the strongest point of agreement across all sources** |

---

## 2. Ability and fitness assessment: "am I getting fitter?"

### 2.1 Benchmark climb: vertical speed at a fixed heart rate

- **Basis.** UA's benchmark segment at AeT HR (§1.1). Koop's segments (§1.2). Evidence that uphill speed largely decides trail race time: a PMC review summarising performance determinants says final time "is largely determined by the runner's ability to generate and sustain high uphill speed" (https://pmc.ncbi.nlm.nih.gov/articles/PMC12656631/, search extract). In a 65 km / 4000 m ultra, race time correlated with VO2max (r = −0.66) (https://www.tandfonline.com/doi/abs/10.1080/02640414.2017.1374707, search extract).
- **Validation status.** The benchmark method is **[coach practice]**. We found no test-retest reliability study of field VAM-at-HR. Day-to-day HR varies with heat, sleep and caffeine, so treat changes of about 3% or less as noise **[ours]**.

> **Chart A: Benchmark-climb VAM at AeT**
> - **Q:** On my standard climbs, am I climbing faster at the same heart rate?
> - **X:** date. **Y:** VAM (m/h) on segments with HR within ±3 bpm of AeT, one line per named climb. Secondary Y: mean HR.
> - **Metric:** `VAM = Δelev_up / Δt × 3600`. Use a segment with grade ≥ 10% and duration ≥ 15 min (UA). Exclude the first 10 min of the activity (warm-up). **[ours]** on the exact filters.
> - **Data:** time, elevation, distance (for grade), HR, and segment matching by GPS or by a manually tagged climb.
> - **Source:** UA field-test article, Koop toolkit.
> - **Impl:** **(a)**. `trail.segment_climbs` and `compute_vam` exist. Segment matching by route needs lat/lon. If we only store distance and elevation, use manual tagging.

### 2.2 Aerobic threshold, drift and decoupling trend

- **Definitions.**
  - Pa:HR (TrainingPeaks) is the EF of the first half against the second half (https://www.trainingpeaks.com/blog/efficiency-factor-and-decoupling/).
  - The 5% threshold is Friel's convention, adopted by UA and Evoke (UA-notes §2(a)).
- **Validation.**
  - **[validated]** at population level: Smyth et al. 2022 studied 82,303 marathoners using an HR÷speed "cardiac cost" ratio. Decoupling onset was about 25 km on average. It came at 33.4 km in the low-decoupling (faster) group and 19.1 km in the high-decoupling group (summarised in https://www.frontiersin.org/journals/sports-and-active-living/articles/10.3389/fspor.2025.1571498/full).
  - Hunter et al. 2025 reviewed durability methods. Adding decoupling magnitude to marathon prediction improved error from 6.45% to 5.16% (https://pmc.ncbi.nlm.nih.gov/articles/PMC12576026/).
  - The 5% cut-off itself is **not** validated. It is a coaching convention.
- **Caveat.** Heat inflates drift. UA's own Continuous-AeT failure list includes heat and ungraded rolling terrain (UA-notes §2(b)). Taiwan summers make this a first-order confound.

> **Chart B: Decoupling on qualifying steady sessions**
> - **Q:** Is my aerobic base holding for longer?
> - **X:** date. **Y:** decoupling %. Draw a 5% reference line. Colour by sport (run/hike/ride) and size by duration.
> - **Metric:** `(v_eff/HR)_first half ÷ (v_eff/HR)_second half − 1`.
>   - v_eff is Minetti grade-adjusted speed for running.
>   - Use VAM for hiking at grade ≥ 15%.
>   - Use power for cycling.
>   - Qualifying session: ≥ 45 min, low variability in grade and speed.
> - **Data:** HR, speed, grade, power (for rides).
> - **Source:** Friel/TrainingPeaks, UA, Smyth 2022.
> - **Impl:** **(a)**. Note: UA-notes §2(c) flags that the existing `trail.compute_hr_drift` computes HR × speed, not speed/HR, so its sign is inverted. It needs checking before this chart uses it.

### 2.3 Efficiency factor trend

- **Definition.** EF = NGP/HR for running or NP/HR for cycling (TrainingPeaks blog above). UA uses it on "identical zone-two workouts spaced eight weeks apart" (§1.1).
- **Validation.** EF is a **[coach practice]** trend metric. The underlying HR–speed relationship is what Runalyze's "Effective VO2max" also exploits: it estimates a VO2max-like value per run from HR against pace, optionally elevation-corrected, with a personal correction factor taken from a race (https://blog.runalyze.com/tutorial/runalyze-understanding-the-calculations/). **[vendor]**, not validated for trail.

> **Chart C: Efficiency factor, Z2 only**
> - **Q:** At easy effort, am I moving more per heartbeat?
> - **X:** date. **Y:** EF, with a rolling 28-day median. Show separate series for road run, trail run, hike and ride. **Never pool the sports** (see §5).
> - **Metric:**
>   - Road: m/min per bpm.
>   - Trail: Minetti metabolic power (W/kg) per bpm, i.e. "metabolic EF" **[ours]**. This uses the cost model so that steep ground is comparable.
>   - Ride: NP/HR.
>   - Include only moving time with HR in Z1–Z2.
> - **Data:** HR, speed, grade, power.
> - **Source:** UA, Friel. The metabolic variant is ours.
> - **Impl:** **(a)**

### 2.4 Climbing economy

- **Evidence.**
  - Level running economy does not predict steep uphill economy: Lemire 2021 (UA-notes §3).
  - In trail races, running economy often fails to predict performance while VO2max does (https://pubmed.ncbi.nlm.nih.gov/36754060/, search extract). An exception: Ehrström's 27 km model included economy at 0% and 10% slopes (R² = 0.98) (https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2019.01306/full, search extract).
- **Field feasibility.** True economy needs VO2 **(b)**. A proxy is **HR cost per vertical metre** at a given VAM, i.e. heartbeats per 100 m of climb **[ours]**. It is confounded by fitness, heat and fatigue, so it is only meaningful on benchmark climbs.

> **Chart D: Heartbeats per 100 m of climb (benchmark climbs)**
> - **Q:** Does each metre of climbing cost me fewer heartbeats?
> - **X:** date. **Y:** `Σ(HR/60 × dt) / Δelev × 100`. Plot only matched climbs at a similar VAM (±10%).
> - **Data:** HR, time, elevation.
> - **Source:** derived from UA's benchmark idea. **[ours]**, not validated.
> - **Impl:** **(a)**

### 2.5 Durability and fatigue resistance

**Definitions [validated literature]:**

- Maunder et al. 2021 define durability as "time of onset and magnitude of deterioration in physiological-profiling characteristics over time during prolonged exercise" (Sports Med 51:1619–1628; https://link.springer.com/article/10.1007/s40279-021-01459-0, auth wall, so the definition comes via search extract. It is also restated in https://pmc.ncbi.nlm.nih.gov/articles/PMC12576026/).
- Jones 2024 calls it the "fourth dimension" of endurance performance (https://physoc.onlinelibrary.wiley.com/doi/10.1113/JP284205).

**Published protocols:**

| Protocol | Measure | Key result | Source |
|---|---|---|---|
| van Erp, Sanders & Lamberts 2021 (pro cycling, field) | MMP at durations after 0…50 kJ/kg of accumulated work | After 50 kJ/kg, successful climbers lost about 4% of 20-min power and less successful ones about 8% | MSSE 53(9):1903; citation and numbers via https://aerisperformance.substack.com/p/durability-the-science-and-practical (secondary) |
| Mateo-March 2022 (pro cycling, field) | Record power profile at 5 s–20 min, fresh and after 20–60 kJ/kg total or 2–6 kJ/kg above CP | Losses up to −53.8% at 5 s after 60 kJ/kg, and −13.6% at 20 min after 6 kJ/kg above CP | https://journals.humankinetics.com/view/journals/ijspp/17/6/article-p926.xml (search extract) |
| Mateo-March 2025 (reliability) | Two best MMPs per duration per kJ/kg bin across a season | Declines clear after 20 kJ/kg. ICC > 0.90, but lowest repeatability at the highest workloads | https://pubmed.ncbi.nlm.nih.gov/40373793/ (search extract) |
| Spragg 2024 (pro cycling, lab) | Fresh vs post-≈2000 kJ, low-intensity vs high-intensity prior work | Similar kJ (1985 vs 1878) but a much larger drop after the high-intensity work. "Total work is not sufficient to quantify prior work when assessing durability" | https://pmc.ncbi.nlm.nih.gov/articles/PMC11235642/ |
| Hunter 2025 (marathoners, lab) | sLT before and after 90 min at LT speed | sLT fell 12.8 → 12.1 km/h. The % change correlated with marathon time (r = 0.68). Running economy did not change significantly | https://pmc.ncbi.nlm.nih.gov/articles/PMC12547624/ |
| 3-min all-out test (runners) | Critical speed after 1 h at 85% CS | CS −6%, D′ −68% | https://pubmed.ncbi.nlm.nih.gov/40132597/ (search extract) |

**Methodological warnings.** Hunter et al. 2025
(https://pmc.ncbi.nlm.nih.gov/articles/PMC12576026/):

- Standardise prior work to intensity domains, not to percentages.
- Duration matters: CP was unchanged after 80 min of heavy-domain work but fell about 10% after 120 min.
- Carbohydrate intake changes durability markers.
- Field data carry tactics, pacing and environment noise.

**Practical chart form.** Intervals.icu already ships a "power curve after X kJ" (https://forum.intervals.icu/t/fatigue-resistance/4396) **[vendor]**. It uses absolute kJ, and the developer notes kJ/kg is "a bit tricky" because weight changes. Users are asking for kJ/kg (https://forum.intervals.icu/t/power-curve-after-kj-kg/93688).

**Running and hiking translation [ours, unvalidated].** Running has no kJ meter, so use
**accumulated Minetti metabolic work (kJ/kg)** as the fatigue axis. Across climbs and
descents it is more consistent than elapsed time. Spragg's result means we should also show
**work above AeT (or above CP)** as a second axis. Total work alone misleads.

> **Chart E: Durability curve (fresh vs fatigued mean-max)**
> - **Q:** How much of my climbing speed or power do I keep late in a long day?
> - **X:** duration (log, 1–60 min). **Y:** best value. One curve each for fresh (0 kJ/kg), after 10, after 20 and after 30 kJ/kg, over a rolling 90 days.
>   - Cycling: power.
>   - Trail and hike: VAM.
>   - Road: grade-adjusted speed.
> - **Metric:** mean-max computed only on samples where cumulative work (cycling: mechanical kJ/kg; foot: Minetti metabolic kJ/kg) is past the threshold.
> - **Data:** power or (speed, grade), bodyweight.
> - **Source:** van Erp 2021, Mateo-March 2022/2025, the Intervals.icu feature. The foot-sport translation is ours.
> - **Impl:** **(a)**. The mean-max machinery exists (`wko5_meanmax.py`, `mmp.py`).

> **Chart F: Durability index trend**
> - **Q:** Is my fatigue resistance improving?
> - **X:** date (per 6-week block). **Y:** `MMP_20min(after 20 kJ/kg) ÷ MMP_20min(fresh)` as a %. Use VAM for foot sports.
> - **Source / Impl:** as Chart E. **(a)**. Needs enough long efforts per block, so show "insufficient data" instead of a number when there are too few.

> **Chart G: Late-day decoupling onset**
> - **Q:** On long days, when does my HR start drifting away from output?
> - **X:** elapsed hours (or cumulative kJ/kg). **Y:** rolling 20-min `v_eff/HR` normalised to the first hour. Overlay the last 5 long days.
> - **Metric:** onset is the first time the ratio falls below 95% of the first hour and stays there **[ours]**, modelled on Smyth's onset/magnitude framing.
> - **Data:** HR, speed, grade. Temperature is desirable (see §4.4).
> - **Impl:** **(a)**. The heat confound needs temperature. Many devices record it, but we should check whether our ingest keeps it.

### 2.6 Repeatability of a benchmark

- Among our sources, only the Mateo-March 2025 reliability work (field MMP by kJ bin, ICC > 0.9) quantifies repeatability, and it covers cycling. No foot-sport equivalent was found.
- **[ours]**: show the benchmark's **test-to-test spread**, e.g. a band of ±1 SD over the last 5 attempts, so that a change can be judged against noise. Never report a 1% change as progress.

---

## 3. Load and periodisation charts for ultra and mountain

### 3.1 Weekly hours and weekly vertical as the primary volume metrics

- **Hours:**
  - Koop: "volume is most effectively tracked by time" (https://trainright.com/data-worth-tracking-ultrarunners/).
  - Jornet reports volume in hours (§1.5).
  - UA progresses aerobic *volume* weekly (UA-notes §2(b)).
- **Vertical:** Koop (ratio), UA and 百岳 (absolute big day with pack), Jornet (weekly m).
- **Distance** is secondary. Koop explicitly demotes it.

> **Chart H: Weekly volume, stacked by sport**
> - **Q:** How much did I train, and where did it go?
> - **X:** ISO week. **Y (bars):** moving hours stacked by sport (road run, trail run, hike, ride, strength). **Y2 (line):** weekly D+ (m). Optional thin line: Minetti-based equivalent flat distance.
> - **Data:** moving time, sport, elevation gain.
> - **Source:** Koop, Jornet, UA.
> - **Impl:** **(a)**

### 3.2 Ramp rules for volume and vertical

- **Volume:**
  - UA says sustained growth over 10% leads to trouble "in roughly eight weeks". UA also uses a 3–5% per week expedition ramp (UA-notes §2(b)).
  - 江晏慶 uses a 10–15% step-down in recovery weeks (§1.7).
  - Both are **[coach practice]**.
- **Vertical ramp:** we found **no published ramp rule specific to vertical**. The closest are Koop's "match the race ft/mile within 10%" (a *target*, not a ramp) and UA's big-day guidance.
- **Evidence on ratio-based ramp metrics.** Impellizzeri et al. 2020 argue the acute:chronic workload ratio suffers from mathematical coupling and has "no evidence supporting the use of ACWR in training-load-management systems or for training recommendations aimed at reducing injury risk" (https://journals.humankinetics.com/view/journals/ijspp/15/6/article-p907.xml, search extract; Human Kinetics blocks fetching). **So do not present a ramp-rate chart as injury prediction.**

> **Chart I: Ramp monitor, hours and descent**
> - **Q:** Am I adding load faster than coaches consider absorbable?
> - **X:** week. **Y:** % change of the 4-week rolling mean of hours, and of **D−** (descent). Shade bands at +10% sustained ≥ 3 weeks. Label them "UA heuristic", not "risk".
> - **Why D−:** muscle damage in trail running is driven by eccentric downhill work (https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12846201/, search extract). 江晏慶's example is quads failing on descents with low HR. **[ours]** to chart descent separately.
> - **Impl:** **(a)**

### 3.3 The big day and back-to-back long days

- **Koop:** back-to-backs "concentrate a large amount of training load in a short period of time" (https://trainright.com/block-training-ultrarunning-ultramarathon/, search extract). A long run needs at least 4 h for nutrition practice (longest-run page).
- **UA:**
  - Mountaineering: the longest session is about 50% of weekly aerobic volume, plus a weekly 4,000 ft day with the climb's pack weight (§1.1).
  - Expedition fitness makes "back-to-back tough days possible" (https://mountaintrip.com/relentlessly-uphill-training-for-expedition-style-mountaineering/, search extract, guide service).
- **百岳:** RACE ON's 60-min loaded test and 上河-time comparison (§1.7).

> **Chart J: Big-day ladder**
> - **Q:** How close have my longest days (and 2–3 day blocks) come to the target?
> - **X:** date. **Y:** for each day, moving hours (bar) and D+ (dot). Highlight 2- and 3-day rolling sums as outlined bars. Horizontal lines at the target event's day-1 and total demand.
> - **Data:** moving time, gain, sport. Pack weight (for 百岳) is **(b)** unless logged manually.
> - **Source:** Koop, UA, RACE ON.
> - **Impl:** **(a)** without pack weight. **(b)** for the pack-weighted variant.

### 3.4 Time-in-zone distribution

- **Research [validated, descriptive]:**
  - Stöggl & Sperlich 2015 review: most retrospective studies show **pyramidal** distributions, with 70–95% in Z1, 2–22% in Z2 and 2–11% in Z3. Some world-class athletes show polarised phases. Prospective trials favoured polarised distributions over 6 weeks to 5 months (https://pmc.ncbi.nlm.nih.gov/articles/PMC4621419/).
  - A 2023 synthesis of 175 elite TIDs: 51% pyramidal, 37% polarised, median 85/7/6 (Z1/Z2/Z3) (https://pmc.ncbi.nlm.nih.gov/articles/PMC10641476/).
  - Casado 2022 found pyramidal most common in highly trained and elite distance runners (IJSPP 17(6):820, 403 to our fetcher, search extract).
  - An elite trail runner over 4 years averaged 75/18/7 (https://www.academia.edu/125955956/, search extract).
  - Jornet's 2022 5-zone split is 58/19/16/4/3 (§1.5).
- **The quantification caveat matters more than the model.**
  - The session-goal method yields about 17.6% less Z1 than time-in-zone.
  - Power-based zones give 11.8% more Z1 than HR-based zones (https://pmc.ncbi.nlm.nih.gov/articles/PMC10641476/).
  - For hiking, HR lag and low HR on descents inflate Z1.
  - **Label which method a chart uses.**

> **Chart K: Intensity distribution per 4-week block**
> - **Q:** Are my easy days easy, and where does my hard work go?
> - **X:** 4-week block. **Y:** 100% stacked bars for Z1 (<AeT), Z2 (AeT–AnT) and Z3 (>AnT) by moving time. Toggle HR-based vs session-goal (from workout tags). Filter by sport.
> - **Data:** HR, AeT/AnT per sport.
> - **Source:** Stöggl & Sperlich, Casado, Kuenzle's LT1 cap, UA zones.
> - **Impl:** **(a)**, with per-sport AeT/AnT stored as dated settings.

### 3.5 Load curve (CTL/ATL) for mountain use

- **Koop:** use it for the big picture (§1.2).
- **UA:** hrTSS plus vertical fudge factors. CTL benchmarks are "historical observations, not predictive targets" (UA-notes §2(b)).
- **Friel on multi-sport:** see §5.

> **Chart L: Mountain-adjusted fitness/fatigue**
> - **Q:** Big picture: am I building or holding, and am I carrying fatigue?
> - **X:** date. **Y:** per-sport CTL lines plus one **all-sport ATL** line (Friel, §5). Load is moving-time hrTSS plus UA vertical/pack adjustments (UA-notes §1).
> - **Impl:** **(a)** (pack adjustment **(b)** unless logged). Do not show a "ready" TSB band. Nobody publishes one for mountain events.

### 3.6 Taper visualisation

- **[validated]:**
  - Bosquet 2007 meta-analysis: about 2-week taper, volume cut 41–60% exponentially, intensity and frequency maintained (https://pubmed.ncbi.nlm.nih.gov/17762369/, search extract).
  - 2023 PLOS One meta-analysis: taper ≤ 21 days works. A 41–60% volume reduction is best. Maintaining intensity (SMD −0.55) and frequency matters. **Trail and ultra events are barely represented** (https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0282838).
- **Koop** ranks the taper at the top of his pyramid, i.e. the least important lever (§1.2).

> **Chart M: Taper tracker**
> - **Q:** Is my taper cutting volume without cutting intensity?
> - **X:** days to race (−28…0). **Y:** daily hours as % of the pre-taper 4-week mean (target band 40–59% of the pre-taper level, i.e. a 41–60% cut), plus Z3 minutes per week as a separate line that should stay roughly flat.
> - **Impl:** **(a)**. Label the band as road/cycling evidence.

---

## 4. Race-specific readiness

### 4.1 "Does my training match the demands?"

- **Koop:** match the race's vertical **per mile/km** within 10% weekly, and reverse-engineer from climb and descent steepness and length (https://trainright.com/data-worth-tracking-ultrarunners/, https://trainright.com/hierarchy-ultramarathon-training-needs-jason-koop/).
- **Roche** and **Yamato** say the same (§1.3, §1.7).

> **Chart N: Course-demand match**
> - **Q:** Does my recent training look like my race?
> - **Axes:** a radar or grouped bars, target race vs the last 4 / 8 weeks, on these dimensions:
>   - (1) D+ per km
>   - (2) D− per km
>   - (3) % time at grade > 15%
>   - (4) % time at grade < −10%
>   - (5) longest single day as % of race time estimate
>   - (6) longest 2-day block
>   - (7) % moving time hiking vs running
>   - (8) time above 2000 m
> - **Data:** race GPX (elevation profile) and training streams.
> - **Source:** Koop's ft/mile and specificity. The multi-dimension layout is ours.
> - **Impl:** **(a)**, given a race GPX importer. Hike vs run classification can use cadence and speed at grade.

### 4.2 Longest session vs race demands

> **Chart O: Longest session vs race**
> - **Q:** Have I rehearsed enough of the race's duration and climbing?
> - **X:** weeks to race. **Y:** rolling max single-day moving hours and D+, as % of the race estimate (race time from our EFD/Minetti model or the ITRA-style EP pace).
> - **Reference:** Koop gives no fixed %; successful athletes ranged from 20% to 80% of distance. At least 4 h for fuelling practice (https://trainright.com/longest-run-ultramarathon-training/). So show the value, not a pass/fail.
> - **Impl:** **(a)**

### 4.3 Nutrition and fuelling rate

- **[validated]:**
  - A 2020 randomised trial (PMC7400827) studied 26 elite runners in a mountain marathon (≈3980 m D+). At 120 g/h vs 90 g/h and 60 g/h they found lower internal load (TRIMP 315 vs 371 / 400) and better next-day neuromuscular and high-intensity recovery (https://pmc.ncbi.nlm.nih.gov/articles/PMC7400827/). Small groups (n = 6–7).
  - A 2-week gut-training protocol (Costa et al.; 90 g/h during a 3 h run) is described only in a search extract. We did not locate or read the primary paper, so treat it as unverified. A separate observational study links GI complaints to self-reported intake across (ultra)marathon distances (https://pmc.ncbi.nlm.nih.gov/articles/PMC6628076/, search extract).
- **Koop:** GI distress is "the leading cause of DNFs in ultramarathon events", and gut training is its own priority tier (hierarchy page).

> **Chart P: Fuelling log**
> - **Q:** Am I practising race-rate carbohydrate on long days, and does GI tolerance improve?
> - **X:** date (long sessions ≥ 2.5 h). **Y:** g carbohydrate per hour (bar), with GI score 0–10 (dot). Reference lines at 60 / 90 / 120 g/h.
> - **Data:** manual intake log and GI rating. **(b)**: we have no intake data.

### 4.4 Heat acclimatisation

- **[validated]:**
  - Meta-analysis: after heat acclimation, core temperature is about 0.31 °C lower and **HR about 12 bpm lower**. Responses decay at about **2.5% per day**. 10-day protocols gave larger power gains than 5-day ones (https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2019.01448/full).
  - HR reduction and plasma-volume expansion appear within 3–6 days (Périard 2015, https://onlinelibrary.wiley.com/doi/10.1111/sms.12408, search extract).
- Relevance: Taiwan's summer lowlands vs cool 3000 m ridges. Heat also confounds Charts B, C and G.

> **Chart Q: Heat exposure and HR-at-pace**
> - **Q:** Am I heat-acclimatised (or de-acclimatised) before a hot race, and is heat distorting my fitness charts?
> - **X:** date. **Y1 (bars):** daily minutes exercising with ambient ≥ 28 °C. **Y2 (line):** HR at a fixed easy grade-adjusted speed. Overlay a decay model for heat-acclimation state at 2.5%/day **[ours applied to a validated decay rate]**.
> - **Data:** temperature, from the device sensor (biased by body heat) or from a weather lookup by GPS and time. **(b)** unless ingest keeps temperature or GPS.

### 4.5 Altitude acclimatisation

- **[validated concept]:** Garvican-Lewis et al. 2016 proposed "kilometer hours" as hypoxic dose: `km·h = (altitude_m / 1000) × hours`. Each +100 km·h is associated with about +0.4% haemoglobin mass (definition and slope via https://pmc.ncbi.nlm.nih.gov/articles/PMC7714921/ and search extracts. The primary JAP letter https://journals.physiology.org/doi/full/10.1152/japplphysiol.00556.2016 returned 403). The model was built for living/training at about 1600–3000 m over weeks. It is **not** validated for 2–4 day 百岳 trips.
- **Koop** ranks altitude training near the top of his pyramid, i.e. a marginal lever (hierarchy page).

> **Chart R: Altitude exposure**
> - **Q:** How much time have I spent high recently, before a 3000 m+ objective?
> - **X:** week. **Y:** km·h from activity elevation × duration (activity time only; sleeping altitude on multi-day trips needs a manual entry). Also: HR at easy pace on days above 2500 m vs below.
> - **Data:** elevation, time. Sleep altitude **(b)**.
> - **Impl:** **(a)** for activity-time exposure. Label it "exposure", not "acclimatisation".

---

## 5. Multi-sport athletes: cycling + running + hiking on one screen

**Evidence:**

- **[validated]** A 2026 meta-analysis found no significant difference in running performance between cycling-substituted and run-only training (g = 0.02). There were small non-significant trends toward specificity in VO2max measured on the matching ergometer (https://pmc.ncbi.nlm.nih.gov/articles/PMC13243379/).
- **[survey, n = 55]** Practitioners judge substitution direction-dependent and non-linear. Replacing a run with cycling needs about 1.76× the duration. Replacing a ride with running needs about 0.54×. Low-intensity work transfers better than threshold or VO2max work (https://www.frontiersin.org/journals/sports-and-active-living/articles/10.3389/fspor.2026.1930728/full). These are **perceptions, not measurements**.
- **Koop:** a couple of hours of easy cross-training is "not enough stress to be meaningful" for ultra fitness (§1.2).
- **Friel's rule:** per-sport PMCs, because "the fitness benefits are not equal across the board… A combined chart is simply too general". Fatigue is the exception: "You can do a hard bike ride today and be tired tomorrow no matter what that day's activity is". So use **one all-sport fatigue (ATL) chart** (https://joefrieltraining.com/the-weightlifting-pmc-part-1/).
- **Tooling:**
  - Intervals.icu lumps all sports into one Fitness/Load line by default. Per-sport views are a long-standing feature request (https://forum.intervals.icu/t/fitness-ctl-fatigue-by-sport/4624, https://forum.intervals.icu/t/separate-bike-run-fitness-charts/14284). The developer's workaround is custom Load charts filtered by activity type.
  - Jornet's public numbers add hours across sports (§1.5).

**What this means for the overview [ours]:**

1. **Hours, stacked by sport** is the only volume unit that adds up honestly across sports (Chart H).
2. **Fitness (CTL) is per sport.** Show it as small multiples or overlaid lines, never summed (Friel).
3. **Fatigue (ATL) is all-sport**, in one line (Friel).
4. **Foot-sport CTL** may reasonably combine road run, trail run and hike, because they share the same eccentric and impact loading. Cycling stays separate. That grouping is our judgement.
5. **Transfer factors** (e.g. 0.54×) are survey opinions. Do not bake them into a hidden "combined fitness" number.
6. Mechanical load differs by sport too. Descent (D−) accrues only from foot sports. Show it on the foot-sport side (Chart I).

> **Chart S: Per-sport fitness small multiples + one fatigue line**
> - **Q:** Which sport is my fitness in, and am I tired overall?
> - **X:** date. **Panels:** Foot (run + trail + hike) CTL and Cycling CTL, each with its own y-scale. The bottom panel shows all-sport ATL.
> - **Source:** Friel. Implemented in Intervals.icu only through custom filtered charts.
> - **Impl:** **(a)**

---

## 6. Overview vs sport-specific dashboards: what existing tools ship

### 6.1 Intervals.icu (open, free; the best reference)

Main chart set **[vendor pages]**:

- **Fitness / Fatigue / Form chart (PMC)** with power, HR, pace or RPE load models and configurable CTL/ATL time constants (https://www.intervals.icu/features/fitness-chart/).
- **Power curve:** season comparisons, W/kg, MAP, eFTP / Morton 3P / Monod-Scherrer models (https://www.intervals.icu/features/power-curve/).
- **HR duration curves, pace curves and fatigued variants** (https://www.intervals.icu/, search extract).
- **Fatigue-resistance curves:** power curve after X kJ (https://forum.intervals.icu/t/fatigue-resistance/4396).
- **Activity analysis:** zone distribution, 42-day curve context, **decoupling** and a "Power/HR Z2" metric, **Seiler decoupling** (60 s moving averages, % of reserve), HR zone time, cumulative HR, intervals table, compare activities, map and weather, heatmaps (https://www.intervals.icu/features/analyze/).
- **Track:** activity totals by sport (distance, time, elevation, zone time), wellness (HRV, sleep, weight…), **70+ metric custom charts**, custom formulas, totals tables, period compare, gear, **routes that auto-recognise repeated routes and track progress over time**, custom zones on any stream (https://www.intervals.icu/features/track/).

**Useful for mountain sport:**

- Totals by sport with elevation.
- Route recognition, which is the closest thing to a benchmark-climb chart.
- Decoupling.
- Fatigued curves.
- Custom zones on any stream (which makes VAM zones possible).
- Per-sport custom load charts.

**Less useful:**

- Power rankings (cycling peer comparison).
- A single lumped PMC.

### 6.2 GoldenCheetah

Trends chart types (https://github.com/GoldenCheetah/GoldenCheetah/wiki/UG_ChartTypes_Trends):

- **Overview**: a configurable tile dashboard.
- **Metric Trends**, with curve types Best, Estimate, **PMC** (user-defined stress metric, so it can run on any input), **Banister** performance modelling, Performance, Formula and Measure.
- **Collection Tree Map.**
- **Critical Mean Maximal** (MMP with CP models).
- **Distribution.**

**Useful for mountain sport:** the user-definable PMC input (which could run on Minetti work) and Banister modelling. Otherwise it is cycling-centric.

### 6.3 Runalyze

Metrics and charts (https://blog.runalyze.com/tutorial/runalyze-understanding-the-calculations/,
https://runalyze.com/help/article/marathon-shape) **[vendor]**:

- **Effective VO2max** from HR against pace, optionally elevation-corrected.
- **Marathon Shape**: a proprietary blend of 6 months of weekly km (2/3) and long runs (1/3). Runalyze describes it as "an invention of Runalyze".
- TRIMP-based ATL/CTL/TSB.
- **Monotony** (flag above 2.0) and **Training Strain**.
- Race prognosis.

**Useful for mountain sport:** Monotony and Strain are Foster-style load-variability measures that work on any load unit. Effective VO2max is a named EF variant.

**Not useful:** Marathon Shape. It is km-based and road-calibrated, and has not been validated.

### 6.4 Stryd PowerCenter

Named views (https://blog.stryd.com/2020/02/11/introducing-the-new-powercenter/) **[vendor]**:

- Power Duration Curve (two-window compare).
- Critical Power history.
- Running Stress Balance.
- My Training (stress load vs 42-day average).
- Fitness View.
- Muscle Power (10 s).
- **Fatigue Resistance View** ("how long you can maintain your goal race effort").
- **Endurance View** (longest continuous activity).
- Training Distribution.
- Lap analysis.

Stryd claims CP "determines your race time from 5K to Marathon" (https://www.stryd.com/features). That is marketing, and for road distances only.

UA says running power is unreliable once you switch to hiking (§1.1). **Stryd is useful for the road-run tab only.** The *layout ideas* are worth imitating: Fatigue Resistance and Endurance views, and the two-window curve compare.

### 6.5 Overview vs sport tab: recommended split [ours, drawing on the above]

- **Overview**, which answers "am I training enough, easy enough, and is it working?":
  - Hours by sport (H)
  - Per-sport CTL + all-sport ATL (S)
  - Intensity distribution (K)
  - Ramp monitor (I)
  - One benchmark-progress tile (A)
  - Race readiness summary (N/O) when a target race is set
- **Sport tabs** hold the curves, EF, decoupling and durability, because those metrics are only comparable within a mode (Friel, and UA's "match the test modality").

---

## 7. Prioritised chart list

Priority ranking is ours. It weighs (1) how much coaches converge on the chart, (2) evidence
strength and (3) buildability. **(a)** means buildable with current data (per-second time,
HR, speed, elevation, distance, cadence, power when present, Minetti, moving-time hrTSS,
EFD, WKO5 PD model). **(b)** means it needs data we do not have.

### OVERVIEW

| # | Chart | Question | Evidence | Impl |
|---|---|---|---|---|
| 1 | **H. Weekly hours stacked by sport + weekly D+** | How much, where? | Koop, Jornet, UA [coach consensus] | (a) |
| 2 | **S. Per-sport CTL (foot / cycling) + one all-sport ATL** | Fitness per sport, fatigue overall | Friel [coach]; cross-training meta-analysis [validated] | (a) |
| 3 | **K. Intensity distribution per 4-week block (AeT/AnT 3-zone)** | Are easy days easy? | Stöggl & Sperlich, 2023 synthesis [validated descriptive] | (a) |
| 4 | **I. Ramp monitor, hours and D− (4-week rolling %)** | Am I ramping too fast? | UA 10% heuristic [coach]; ACWR critique means no injury claim | (a) |
| 5 | **N. Course-demand match (target race)** | Does training resemble the race? | Koop ft/mile [coach] | (a), with a race GPX importer |
| 6 | Heat exposure + HR-at-pace (Q) | Acclimatised? Is heat biasing charts? | Heat-acclimation meta-analysis [validated] | (b), needs temperature or GPS weather |

### RUNNING (road)

| # | Chart | Question | Evidence | Impl |
|---|---|---|---|---|
| 7 | **C. EF trend, Z2 only (m/min per bpm)** | Faster per heartbeat at easy effort? | UA, Friel [coach]; Runalyze eVO2max [vendor] | (a) |
| 8 | **B. Decoupling on qualifying steady runs** | Aerobic durability? | Smyth 2022 [validated population]; 5% cut-off [coach] | (a), after fixing the `compute_hr_drift` sign (UA-notes §2(c)) |
| 9 | Pace/GAP mean-max curve, two-window compare | Where did the speed-duration curve move? | Stryd/Intervals.icu layout [vendor]; CS model | (a) |
| 10 | M. Taper tracker | Cutting volume, keeping intensity? | Bosquet 2007, PLOS 2023 [validated, road/cycling] | (a) |

### TRAIL (running on mountain terrain)

| # | Chart | Question | Evidence | Impl |
|---|---|---|---|---|
| 11 | **A. Benchmark-climb VAM at AeT HR** | Climbing faster at the same HR? | UA, Koop segments [coach consensus] | (a); manual climb tagging if we have no GPS |
| 12 | **E. Durability curve: VAM mean-max fresh vs after 10/20/30 kJ/kg Minetti work** | How much climbing speed survives late in the day? | van Erp, Mateo-March [validated, cycling]; foot translation [ours] | (a) |
| 13 | **G. Late-day decoupling onset (long days)** | When does drift start? | Smyth onset/magnitude [validated]; Hunter 2025 | (a); heat confound (b) |
| 14 | O. Longest session vs race (hours, D+) | Rehearsed enough? | Koop [coach] | (a) |
| 15 | D. Heartbeats per 100 m climb (benchmark climbs) | Cheaper climbing? | [ours], unvalidated | (a) |
| 16 | P. Fuelling log (g/h, GI score) | Gut trained to race rate? | PMC7400827 trail-marathon RCT [validated, small n]; Koop | (b), needs an intake log |

### 百岳-HIKING (multi-day, loaded)

| # | Chart | Question | Evidence | Impl |
|---|---|---|---|---|
| 17 | **J. Big-day ladder with 2–3 day rolling blocks vs trip demand** | Ready for day 1 and for the whole traverse? | UA mountaineering, RACE ON [coach] | (a); pack-weighted version (b) |
| 18 | **Actual vs 上河 map time on test hikes (ratio)** | Faster or slower than the standard time? | RACE ON 百岳練習生 [coach] | (b), needs the 上河 reference time per route (manual entry) |
| 19 | VAM at AeT on steep (≥15%) hiking segments (hiking variant of A) | Hiking climb fitness | UA [coach] | (a); pack weight (b) |
| 20 | R. Altitude exposure (km·h, activity time) | How much time high before a 3000 m trip? | Garvican-Lewis 2016 [validated for camps, not for short trips] | (a) activity-only; sleep altitude (b) |

### CYCLING

| # | Chart | Question | Evidence | Impl |
|---|---|---|---|---|
| 21 | **Fatigued power curve (after 10/20/30 kJ/kg) + durability index (F)** | Durability on the bike | van Erp 2021, Mateo-March 2022/2025, Spragg 2024 [validated] | (a), with power |
| 22 | EF (NP/HR) trend + decoupling, Z2 rides | Aerobic base on the bike | Friel [coach] | (a) |
| 23 | WKO5 PD model / mean-max two-window compare | Where did the power curve move? | Existing WKO5 PD model | (a), with power |

### Deliberately left out, and why

- **Single combined CTL across all sports.** Friel says it is "too general". The Intervals.icu forum shows users repeatedly asking to split it (§5).
- **TSB "race-ready" band.** No mountain source publishes one. UA/Evoke and Koop do not use TSB targets (UA-notes §2(b), §1.2).
- **ACWR injury-risk gauge.** It is mathematically coupled and there is no causal evidence for it (Impellizzeri 2020, §3.2).
- **Running power, CP race predictor on trail/hike.** UA calls power unreliable when hiking, and Stryd's claims are for 5K–marathon on roads (§1.1, §6.4).
- **Marathon Shape.** Proprietary, km-based and not validated (§6.3).
- **Total weekly vert as a fitness score.** Koop says total vert has no dose response. Jornet improved uphill while cutting vert. Show vert as **volume context** and as a **ratio against the race**, not as a score (§1.2, §1.5).
- **Watch VO2max / lab VO2max trend as a headline.** UA: "doesn't directly tell you how to structure your training next week" (§1.1).
