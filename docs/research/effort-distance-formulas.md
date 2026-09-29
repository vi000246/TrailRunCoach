# Effort-distance formulas for Taiwanese high-mountain hiking and trail running: research notes

Date: 2026-09-29. Scope: web research and provenance only. No code changes.

Why this exists: we need one "equivalent flat distance" (effort distance) figure that works
for 百岳 days (long, very steep, 3,000 m and higher, loaded, often multi-day, slow) and
for Taiwanese trail races (faster, moderate grades). The main session is testing candidates
against Minetti-derived metabolic cost on the user's GPS data. This document covers what each
formula is, where it comes from, and what it leaves out.

How to read it:

- Each claim has a URL. **[measured]** means the number comes from a physiological or
  statistical study. **[rule of thumb]** means convention, a guidebook, or an organiser's
  choice. **[derived here]** means we computed it from cited numbers; no source states it.
- **Fetch problems (read these before quoting anything below):**
  - The `itra.run` FAQ and news pages (`/FAQ/Organizers`, `/FAQ/ItraScore`,
    `/Runners/Performance`, `/News/Article?...`) came back **empty** to our fetcher. They are
    rendered client-side. `web.archive.org` is blocked for our fetcher. Where ITRA's own
    wording matters we fall back to the UTMB page, which did load, or we mark the claim
    `[search extract only]`.
  - Scarf's paper (Taylor & Francis, ResearchGate PDF) returned **HTTP 403**, and the Salford
    repository link now redirects to the repository home page. Scarf's figures below come from
    Wikipedia and the search-engine abstract, and are marked that way.
  - The YAMAP help page returned **HTTP 403**. Minetti 2002 on journals.physiology.org returned
    403, but a full-text PDF mirror loaded and we quote from that.

---

## 1. ITRA km-effort (and UTMB's use of it)

**Expression.** `km_effort = distance_km + gain_m / 100`

- UTMB's official page says so directly: "KM-Effort = d + (h/100) Distance (d) plus Elevation
  in meters (h) divided by 100 equals KM-Effort", with the worked example 50 km + 2,500 m = 75.
  UTMB also uses km-effort to sort races into its four categories (20K / 50K / 100K / 100M) and
  says the categories "classify races exceeding 20km-effort".
  https://utmb.world/sports-system
- Finishers.com describes it as "adding the distance in kilometers to one hundredth of the
  positive elevation gain (100 mD+ = 1 km-effort)".
  https://www.finishers.com/en/articles/itra-points-everything-you-need-to-know-about-how-it-works-and-how-to-earn-them
- **Units:** km plus m/100. The result is in "km-effort".
- **Provenance:** an organiser's convention **[rule of thumb]**. We found no published
  physiological derivation for the 1:100 ratio. [derived here] It does happen to match the
  running energy data (see §4e): per Minetti, 100 m of climb costs about the same as 1.4 km of
  flat running.
- **Descent:** not included. Only positive gain counts (UTMB formula above).
- **Technicality, terrain, weather:** not in the formula. A search-engine extract of ITRA's
  FAQ says technicality and conditions "are not objectively measurable". According to that
  extract, ITRA handles them statistically instead: it compares the same runners' results
  against their results on courses with similar km-effort, which turns course difficulty into
  an adjustment to the race score. `[search extract only]`, because the page itself rendered
  empty: https://itra.run/FAQ/ItraScore
- **Race categories:** a third-party site gives the ITRA size classes as XXS < 25, XS 25–44,
  S 45–74, M 75–114, L 115–154, XL 155–209, XXL ≥ 210 km-effort. We could not verify these on
  itra.run: https://findracepace.com/en/glossary/itra-distance-categories (via search extract).
- **ITRA "Mountain Level":** this is separate from km-effort. The descriptions we found
  **disagree**:
  - A search extract of ITRA's runner FAQ says it runs **0–12** and uses the gain/distance
    ratio, **average race altitude**, and the longest single ascent. `[search extract only]`
    https://itra.run/FAQ/Runner
  - A race-organiser page paraphrasing ITRA says **1–14**, based on "altitude (minimum, maximum
    and mean), to the gradient percent and to the longest uphill".
    https://mmctrail.no/about_itra
  - ITRA publishes no formula. **This is the only system in this document that looks at
    altitude at all**, and it is a descriptive label, not an effort-distance.
- **ITRA "Finisher Level":** "the lowest ITRA Performance Index that a runner needs in order
  to finish the race (within the cut-off time limit)" (https://mmctrail.no/about_itra). It is
  derived from past results, not from a formula.
- **UTMB Index:** a performance score computed per category from results within the last 24
  to 36 months. The UTMB page mentions no terrain or technicality adjustment.
  https://utmb.world/sports-system
- **What it ignores:** descent, altitude, terrain, pack weight, speed or gait.

## 2. Swiss Leistungskilometer and Swiss hiking-time rules

The label "Swiss formula" covers **three different things**, and the sources do not agree:

### 2a. Leistungskilometer (BASPO version)

`Lkm = horizontal_km + ascent_m / 100 + steep_descent_m / 150`, where descent counts only on
**steep** sections (average gradient > 20 %). German Wikipedia cites the BASPO (Bundesamt für
Sport, Magglingen) training booklet *Lagersport – Trekking – Unterwegssein*. Its definition: "ein
Maß zur Abschätzung des Energie- und Zeitaufwands … nicht mit der tatsächlichen Wegdistanz
identisch".
https://de.wikipedia.org/wiki/Leistungskilometer

- Worked example (secondary source): 15 km, +900 m, −1,100 m at 22 % → 15 + 9 + 7.3 =
  31.3 Lkm. The same source uses 10–15 min per Lkm depending on fitness and load.
  https://www.ich-geh-wandern.de/leistungskilometer-beim-wandern (via search extract)
- **[rule of thumb].** We found no measured basis for either divisor (/100 or /150). The
  Wikipedia article gives no date of origin.

### 2b. Leistungskilometer: other variants

- swissclassic (an event organiser) uses `km + gain/100`, with no descent term. For "starkem
  Gefälle" it only suggests adding extra time, with no formula.
  https://www.sclassic.ch/definition-der-leistungskilometer/
- lauftipps.ch sells a calculator under the same name, but it computes something different:
  +5.8 % time per percentage point of average uphill grade and −2.1 % per point downhill. It
  calls this "eine Annäherung" and cites no study.
  https://lauftipps.ch/tools/leistungskilometer-berechnen/
- So "Leistungskilometer" has **no single authoritative definition**. Only the BASPO version
  handles descent, and it counts steep descent only.

### 2c. Schweizer Wanderwege / SAC signpost times

- The official signpost time is a **15th-degree polynomial in slope** times path length, with
  a flat base speed of 4.2 km/h. Above 40 % slope a constant replaces the polynomial, so time
  grows linearly with gradient. Gerhard Weber (Bundesamt für Landestopografie) and his son
  Stephan Weber derived it in the 1980s, and it has been used nationwide since 2006. Cantonal
  field staff check it by walking the routes and adjust with "Erfahrungswerte".
  https://www.sac-cas.ch/de/die-alpen/die-praktische-wanderzeitformel-25135/ ,
  https://www.schweizer-wanderwege.ch/de/wissen/signalisation/wanderzeit
- The simple rule Schweizer Wanderwege gives the public: ¼ h per flat km (4 km/h) + ¼ h per
  100 m of ascent + ¼ h per **200 m of descent**. Written as an effort distance, that is
  `km + up/100 + down/200`. The official page says surface type and fitness are deliberately
  not considered, and breaks are excluded.
  https://www.schweizer-wanderwege.ch/de/wissen/signalisation/wanderzeit
- **[rule of thumb / empirically tuned]**. None of these includes altitude.

### 2d. DIN 33466 (German / Austrian Alpine-club signposts)

For comparison: 4 km/h horizontal, 300 m/h up, 500 m/h down. Compute the horizontal time and
the vertical time, then take the larger plus half the smaller.
https://www.wanderndeluxe.de/en/wanderzeit-berechnen-strecke-hoehenmeter/ (secondary source;
we did not read the DIN text itself). **[rule of thumb]**

## 3. Japanese コース定数 / ルート定数 (Yamamoto Masayoshi, 山本正嘉, 鹿屋体育大学)

**Expression:**

```
コース定数 = 1.8 × 行動時間(h) + 0.3 × 歩行距離(km) + 10.0 × 登り累積標高差(km) + 0.6 × 下り累積標高差(km)
行動中のエネルギー消費量(kcal) ≈ コース定数 × (体重 kg + ザック重量 kg)
```

Sources: Yama-kei (the guidebook publisher)
https://www.yamakei-online.com/yama-ya/detail.php?id=363 ; YAMAREKO
https://www.yamareco.com/guide/faq/stamina_index/ ; NTT West Biz Clip interview
https://business.ntt-west.co.jp/bizclip/articles/bcl00020-045.html

- **Units:** the coefficients are in kcal per kg of (body + pack): per hour, per horizontal
  km, per km of ascent, per km of descent. Note that gain and loss are in **km**, not m.
- **Derivation [measured]:** Yamamoto's group measured energy use with portable respiratory
  gas analysers while subjects climbed, descended and walked on the flat, then fitted the
  equation to that data: "携帯型の呼吸代謝測定値を用いて、人が山に登るとき、下るとき、平たんな道を
  歩くときの消費エネルギーを実際に測定し…この式を導き出しました"
  (https://business.ntt-west.co.jp/bizclip/articles/bcl00020-045.html). Primary paper: 中原玲緒奈・
  萩原正大・山本正嘉「登山のエネルギー消費量推定式の作成 ―歩行時間, 歩行距離, 体重, ザック重量との
  関係から―」『登山医学』26:115–121, 2006
  (https://jglobal.jst.go.jp/detail?JGLOBAL_ID=200902212957954269). We could not get the
  paper's full text, so subject numbers and fit statistics are **not verified**.
- **Yamamoto's own summary** (鹿屋 NIFS research sheet, PDF text extracted locally):
  - Bands: ~10 beginner, ~20 general, ~30 strong hikers, >40 "１日で歩くことは厳しいので１泊以上".
  - Multiplying by body weight + pack gives kcal. Reading that kcal figure as ml gives the
    estimated dehydration ("kcalという単位をmlに読み換えると…脱水量も推定できます").
  - Since 2016 the constant is printed in Yama-kei's 『分県登山ガイド』. Nagano (from 2014),
    Niigata, Yamanashi, Shizuoka and Gifu use it for their prefectural gradings.
  - Across the 日本百名山 it ranges from 9 (美ヶ原) to 105 (黒岳・鷲羽岳).

  https://www.nifs-k.ac.jp/images/property/researchers_pdf/2-4yamamoto.pdf
- **Water:** Yama-kei recommends replacing 70–80 % of the estimated loss.
  https://www.yamakei-online.com/yama-ya/detail.php?id=363
- **Prefectural use (信州 山のグレーディング):**
  - Nagano's mountain centre computes the constant from the **standard course time in
    『山と高原地図』 (昭文社)**, with distance and gain measured in カシミール3D, cites 中原ら
    2006, and maps the constant to 体力度 1–10.
  - Its example: a 体力度 6 route uses about three times the energy of a 体力度 2 route.
  - It also has a separate A–E 技術的難易度 scale, and says C–E routes need fitness for about
    8 METs versus about 7 for A–B.
  - It rates dry-season, good-weather conditions only.

  http://sangakusogocenter.com/topics/docs/grading.pdf
- **Where the time term comes from:** 山と高原地図 course times are set for experienced hikers
  aged 40–60, in parties of 2–5, in fine summer weather, carrying hut-based gear, with no
  breaks. https://yamachizu.jp/article/9861
- **YAMAREKO** only shows 体力度 when a GPS log with timestamps exists (no hand-drawn tracks),
  so its time term is the user's actual moving time.
  https://www.yamareco.com/guide/faq/stamina_index/ . YAMAP's help page returned 403, so we
  have not verified which time YAMAP uses.
- **Validity range and what it ignores:**
  - It was fitted on Japanese mountain walking with a pack, not running.
  - It has **no altitude term** and **no terrain or technicality term**. Nagano handles
    technicality on a separate axis.
  - Because the 1.8 × h term uses time, a slower walker gets a **higher constant on the same
    route**. It is only route-intrinsic when everyone uses the standard map time.
- **Cross-check against Minetti [derived here]:**
  - 10.0 kcal/kg per km of ascent = 41.8 J/kg per vertical metre. Minetti's walking cost at
    +45 % (17.33 J/kg/m of path ÷ sin(atan 0.45) ≈ 0.41) comes to about 42 J/kg per vertical
    metre. Minetti also reports uphill efficiency of 0.22–0.24, which implies about 41–45
    J/kg/m. **The Yamamoto ascent coefficient agrees with the lab data.**
  - The descent coefficient (0.6, i.e. 6 % of the ascent coefficient) is small. Most of the
    downhill cost is carried by the time term instead.

## 4. Naismith family, Scarf, Tobler (time-based rules)

### 4a. Naismith (1892)

"Allow one hour for every 3 miles (5 km) forward, plus an additional hour for every 2,000 feet
(600 m) of ascent". No descent term. https://en.wikipedia.org/wiki/Naismith's_rule . grough adds
that Naismith deliberately left descent out.
https://www.grough.co.uk/magazine/2009/07/13/just-a-minute-mr-naismith-can-that-be-right
**[rule of thumb].** As an effort distance: `km + gain_m / 120` [derived here, from 5 km ≡ 600 m].

### 4b. Aitken (1977)

3 mph on paths and roads, 2.5 mph on other terrain, plus 1 h per 2,000 ft. The off-path factor
works out to about +20 % time. https://en.wikipedia.org/wiki/Naismith's_rule **[rule of thumb]**

### 4c. Langmuir (Mountaincraft and Leadership, 1984)

- Descent: on gentle downhill of **5–12°**, subtract 10 min per 300 m of descent. On
  **> 12°**, add 10 min per 300 m of descent.
- Group pace: "4 km/h + 1 h / 450 m of ascent".

Sources: https://en.wikipedia.org/wiki/Naismith's_rule and
https://www.grough.co.uk/magazine/2009/07/13/just-a-minute-mr-naismith-can-that-be-right .

**[rule of thumb].** The grough author timed his own descents of Pen-y-ghent and got about
4 km/h on the steep sections and 6 km/h on the gentle ones. He argues that standard descent
allowances are too optimistic. That is a **single-person anecdote**, not a study.

### 4d. Tranter's corrections

- A lookup table that rescales the Naismith time by fitness. Fitness is defined as the minutes
  needed to climb 1,000 ft (300 m) over ½ mile (800 m).
  https://en.wikipedia.org/wiki/Naismith's_rule
- Example: at the 15-minute fitness level an 8 h Naismith estimate becomes about 5.5 h; at
  30 minutes it becomes about 12.5 h. For rough terrain or bad conditions, drop one or more
  fitness levels. https://www.mudandroutes.com/tranters-corrections/ (secondary; via search
  extract)
- **[rule of thumb].** It is a time multiplier, not an effort distance.

### 4e. Scarf's equivalence

- Scarf fitted UK fell-running **record times** (Wikipedia: 300 races from the 1994 calendar)
  and found an equivalence of **α = 7.92**: 1 unit of climb ≡ 7.92 units of horizontal
  distance, rounded to 1:8. The recommendation is **1:8 for men and walkers, 1:10 for women**.
- Effort distance: `km + 8 × gain_km`, i.e. 100 m ≡ 0.8 km (1:10 gives 1.0 km).
- Paper: P. Scarf, "Route choice in mountain navigation, Naismith's rule, and the equivalence
  of distance and climb", *J. Sports Sci.* 25(6):719–726 (2007). An earlier 1998 note is
  mentioned on Wikipedia.
- Sources: https://en.wikipedia.org/wiki/Naismith's_rule ; journal page
  https://www.tandfonline.com/doi/full/10.1080/02640410600874906 (**403 to our fetcher**; the
  ratios come from the search-engine abstract and Wikipedia).
- **[measured], but statistically, on time.** It uses record performances by elite fell
  runners. That says nothing directly about energy cost or slow loaded hiking.

### 4f. Tobler's hiking function (1993)

`W = 6 · exp(−3.5 · |S + 0.05|)` km/h, where S = dh/dx (the tangent of the slope).

- It peaks at 6 km/h on a −5 % grade (about −2.86°) and gives 5 km/h on the flat.
- For off-path travel, multiply by **3/5**.
- It was fitted to empirical data from Eduard Imhof (*Gelände und Karte*, 1950).
- Source: Tobler, *Three presentations on geographical analysis and modeling*, NCGIA Technical
  Report, 1993. https://en.wikipedia.org/wiki/Tobler's_hiking_function
- **[measured, indirectly]:** it is fitted to Imhof's tabulated walking times, not to new
  experiments.
- The effort distance comes from integrating `dt = ds / W(S)`. It includes descent through the
  |S + 0.05| term, but it treats steep descent as symmetric with steep ascent, which the
  energy data do not support (§4g). We did not verify later refits (Irmischer & Clarke;
  Goodchild's "Beyond Tobler's Hiking Function",
  https://onlinelibrary.wiley.com/doi/abs/10.1111/gean.12253).

### 4g. The energy baseline: Minetti 2002

This is the metabolic reference the main session is using. Quotes are from the full-text PDF
mirror at http://runscribe.com/wp-content/uploads/power/Minetti2002.pdf (the official page
https://journals.physiology.org/doi/full/10.1152/japplphysiol.01177.2001 returned 403).

- **Setup:** 10 runners on a treadmill, gradients −0.45 to +0.45.
- **Walking:** minimum Cw is 1.64 J/kg/m on the flat at 1.0 m/s, 17.33 at +0.45, a low of
  0.81 at −0.10, and 3.46 at −0.45.
- **Running:** Cr is 3.40 J/kg/m on the flat (independent of speed), 18.93 at +0.45, a low of
  1.73 at −0.20, and 3.92 at −0.45.
- **Fitted curves:**
  - `Cw(i) = 280.5i⁵ − 58.7i⁴ − 76.8i³ + 51.9i² + 19.6i + 2.5`
  - `Cr(i) = 155.4i⁵ − 30.4i⁴ − 43.3i³ + 46.3i² + 19.5i + 3.6` (R² = 0.999)
- **Efficiency:** above +0.15 it is 0.243 (walking) and 0.218 (running). Below −0.15 it is
  −1.215 and −1.062. "The optimum gradients for mountain paths approximated 0.20–0.30 for both
  gaits."
- **Validity:** the fits are valid only on −0.45…+0.45. Beyond about −0.5 they drift toward
  unphysical values.
- **The key point for choosing an equivalence ratio [derived here]:** the "right" climb-to-flat
  ratio depends on **gait**, because flat running costs about twice flat walking while
  vertical cost is similar.
  - Walking: 100 m of climb ≈ 42 J/kg/m × 100 ÷ 1.64 J/kg/m ≈ **2.6 km of flat walking**.
  - Running: ≈ 46 × 100 ÷ 3.40 ≈ **1.4 km of flat running**.
  - The time-based rules (Naismith, Scarf, ITRA) put 100 m at about 0.8–1.0 km. Measured
    against *energy*, they **underweight climbing for hikers**.

## 5. Taiwan-specific practice

### 5a. 健行筆記 (hiking.biji.co): EP 耗力指數

- EP is ITRA's km-effort under another name: "EP（Effort Point，耗力指數）＝ 距離（km）＋ 爬升（m）/100".
  It is presented as a trail-running import.
- It is used for pacing through **EpH** (EP per hour). At EpH 3, an EP-21 route takes about
  7 h.
- The article itself lists what EP leaves out:
  - "不計算下坡" (it does not count descent), including impact on the joints;
  - technical terrain (loose scree, wet mud, roots);
  - route-finding time;
  - altitude: above 3,000 m performance drops.
- It recommends a 20 % time buffer.

https://hiking.biji.co/index.php?act=info&id=24918&q=news

- The 高山單攻 (single-day high-peak) route table:
  - columns: EP, distance, gain, **loss**, ascent time, EP/hr;
  - rationale: "在平地前進一公里所耗費的體力約等於垂直爬升100公尺";
  - loss is listed but not used in EP.

  https://hiking.biji.co/index.php?q=review&act=info&review_id=21521
- Other Taiwanese sources repeat the same EP and EP/H. 山林知事村 lists local terrain factors
  qualitatively: stone and wooden stairs, mud, roots and gullies, rope sections, overgrown
  trail.
  https://forestplus.twmountain.com/%E5%91%8A%E5%88%A5%E8%B6%8A%E7%B4%9A%E6%89%93%E6%80%AA-%E5%BE%9E%E4%B8%80%E6%A2%9D%E7%99%BB%E5%B1%B1%E8%B7%AF%E7%B7%9A%E7%9A%84%E6%99%82%E9%96%93%E8%A6%8F%E5%8A%83%E8%AB%87%E8%B5%B7/
- 蔡日興 (interviewed in 太報) cites ITRA's 100 m ≈ 1 km and Yamamoto's formula. He calls
  Yamamoto's formula hard to do in your head and treats psychological factors as
  unquantifiable. https://www.taisounds.com/news/content/96/26394
- **So in practice, Taiwan uses km + gain/100 with no descent, altitude or terrain term.**
  **[rule of thumb]**

### 5b. Official gradings (台灣山林悠遊網 / 國家公園)

- The 國家公園步道系統分級 (national-park trail grading, 內政部營建署, announced 2021-10-07)
  has levels 0–6, defined by **days** and qualitative terrain and weather risk. Examples:
  level 3 is about 1–3 days; level 4 is about 3–5 days, or under 3 days with difficult
  terrain; level 6 is snow/ice or trackless routes. It has **no distance, gain or altitude
  thresholds**. https://recreation.forest.gov.tw/News/News?id=20211007003
- The older 百岳 A/B/C/C+ grading works the same way:
  - A: 1–3 days;
  - B: 4–5 days, or 1–3 days with dangerous terrain;
  - C: 5+ days;
  - C+: needs rappelling or rock technique;
  - D/E: snow season.

  https://hiking.biji.co/index.php?q=news&act=info&id=4870
- 玉山國家公園 describes the 玉山 system as 21.8–61.2 km, 2–5 days, entirely above 3,000 m,
  with no gradient figures. https://www.ysnp.gov.tw/Trail/7fa5c242-df1a-4a8e-bcab-32dc55b1f7b6
- **Unlike Japan's prefectural gradings, no Taiwanese official grading computes an energy or
  effort number.**

### 5c. Trail-race organisers

- Taiwanese races publish distance, gain, ITRA points and cut-offs. We found no race using its
  own formula. https://www.lightliterlife.tw/2026_taiwan_trailrun_race_database/ (the page was
  truncated for our fetcher; the race-by-race numbers were not visible).
- TNF100 Taiwan: 60 K = 3 ITRA points, 42 K = 2 ITRA points (42 K has > 1,600 m gain).
  `[search extract only]` https://hiking.biji.co/index.php?act=info&id=24792&q=news
- 福爾摩沙古道: 10/18/40/75/104 K, with 2,400–5,700 m gain from 40 K up (104 K has 5,700 m).
  `[search extract only]` https://nspp.mofa.gov.tw/nspp/news.php?unit=406&post=244666
- 環花東: we found no primary source for its grading.
- **All of them use km-effort, i.e. ITRA.**

### 5d. 百岳 terrain

- We found **no published quantitative terrain multipliers for 黑森林 / 箭竹 / 碎石** from any
  Taiwanese source. The only numeric terrain factors anywhere come from US military
  load-carriage research (§7).
- For 箭竹 (dwarf bamboo) on an overgrown trail, the closest published analogues are "light
  brush" (1.2) or "heavy brush" (1.5). For 碎石 (scree), "loose sand" (2.1) is an upper-bound
  analogue. **These mappings are our guess, not a source's.**

## 6. Altitude

- **VO2max falls with altitude [measured]:**
  - Wehrlin & Hallén 2006 (*Eur J Appl Physiol* 96:404–412): 8 endurance-trained athletes in
    a hypobaric chamber at 300–2,800 m. VO2max "declined linearly … corresponding to a 6.3 %
    decrease per 1,000 m (range 4.6–7.5 %)".
  - Time to exhaustion at a fixed speed fell 14.3–14.5 % per 1,000 m.
  - The decline had **already started between 300 and 800 m**.
  - Their compilation of earlier studies gives **7.7 % per 1,000 m**.

  Full-text mirror:
  https://www.gasteiner-heilstollen.com/fileadmin/user_upload/PDFs/Wissenschaftliche-Unterlagen/2006_Linear_decrease_in_VO2max_and_performance_with_inc.pdf
- **Check on the "6–8 % per 1,000 m above 1,500 m" claim:**
  - The **magnitude is supported**: 6.3 % measured, 7.7 % pooled.
  - The **"above 1,500 m" threshold is contradicted** for trained athletes. Wehrlin & Hallén
    found decreases from 300 to 800 m. Fulco et al. 1998 (USARIEM review) report that VO2max
    is reduced "beginning at an altitude of 580 m".
    https://pubmed.ncbi.nlm.nih.gov/9715971/ (the abstract text reached us via search extract;
    PubMed itself needed cookies).
  - A figure of "5.0–11.6 % per 1,000 m above 1,500 m in normal trained subjects" circulates in
    search extracts of Pühringer et al. 2022 (*High Alt Med Biol*), but that page returned
    **403**, so it is **unverified**.
- **Energy cost does not rise at altitude:** in the same study, **submaximal VO2 at a fixed
  speed "did not change with altitude"**, while heart rate went up (133 → 150 bpm at
  2,800 m) and SpO2 went down (Wehrlin & Hallén, above).
  [derived here] So altitude does *not* raise the energy cost per metre, and it should **not**
  be a multiplier on effort distance (kcal). It lowers **capacity**: the same pace is a
  higher %VO2max, so it inflates time, HR-based load and fatigue.
  - Extrapolating 6.3 %/1,000 m from about 300 m to 3,500 m gives roughly 20 % lower VO2max,
    so the same absolute pace means roughly 25 % higher relative intensity. This goes
    **beyond the 2,800 m tested range** and ignores acclimatisation.
- **Formulas that include altitude:**
  - None of the effort-distance formulas above has an altitude term: km-effort, Leistungskm,
    Wanderwege/DIN, Yamamoto, Naismith family, Tobler.
  - Only ITRA's Mountain Level uses altitude, and it is a label with no public formula (§1).
  - 健行筆記 mentions the 3,000 m performance drop only as prose (§5a).

## 7. Terrain and technicality factors

- **Pandolf equation with Soule & Goldman terrain coefficients [measured, walking with a
  load]:**
  - `M = 1.5W + 2.0(W+L)(L/W)² + η(W+L)(1.5V² + 0.35VG)`
  - η values:

    | Surface | η |
    |---|---|
    | blacktop or treadmill | 1.0 |
    | dirt road | 1.1 |
    | light brush | 1.2 |
    | heavy brush | 1.5 |
    | swampy bog | 1.8 |
    | loose sand | 2.1 |
    | soft snow 15 / 25 / 35 cm | 2.5 / 3.3 / 4.1 |

  - Sources: https://en.wikipedia.org/wiki/Pandolf_equation ; Soule & Goldman,
    *J Appl Physiol* 32:706–708 (1972), https://pubmed.ncbi.nlm.nih.gov/5038861/ .
  - Subjects were young military men walking on the flat. η multiplies only the
    speed/grade term, not the standing cost.
  - A later review proposes refinements.
    https://jhp-ojs-tamucc.tdl.org/jhp/index.php/JHP/article/view/67
- **Uneven surfaces [measured]:**
  - Walking on a surface with up to 2.5 cm of unevenness at 1.0 m/s raised net metabolic cost
    **+28 %** (Voloshina et al. 2013). https://pmc.ncbi.nlm.nih.gov/articles/PMC4236228/
  - Running on the same kind of surface at 2.3 m/s raised it only **+5 %** (Voloshina & Ferris
    2015). https://journals.biologists.com/jeb/article/218/5/711/14624/Biomechanics-and-energetics-of-running-on-uneven
  - The absolute increases were similar for walking and running, so the *percentage* penalty
    is much larger for slow walking. That matters for 百岳 hiking versus racing.
  - A 2.5 cm lab surface is far milder than real 碎石 or root-and-rock trail.
- **Time-based terrain factors [rule of thumb]:**
  - Tobler off-path ×3/5 speed, i.e. +67 % time (§4f).
  - Aitken 2.5 vs 3 mph, i.e. +20 % time (§4b).
  - Tranter: drop one or more fitness levels (§4d).
- **ITRA:** no terrain multiplier. Any terrain effect enters only through its statistical,
  results-based race scoring `[search extract only]` (§1).
- **Minetti's subjects** were on a smooth treadmill. His downhill running cost was about 40 %
  below earlier data from sedentary subjects, which shows descent cost depends heavily on
  skill. http://runscribe.com/wp-content/uploads/power/Minetti2002.pdf

---

## 8. Comparison table

| Formula | Expression (effort-distance form) | Inputs | Descent | Altitude | Terrain | Basis |
|---|---|---|---|---|---|---|
| ITRA / UTMB km-effort, 健行筆記 EP | `km + up_m/100` | dist, gain | ignored | no (Mountain Level label only) | no (statistical race scoring only) | rule of thumb |
| Naismith | `km + up_m/120` [derived] | dist, gain | ignored | no | no | rule of thumb |
| Scarf | `km + 8·up_km` (men and walkers), `+10·up_km` (women) | dist, gain | ignored | no | no | measured on fell-race record *times* |
| Langmuir | Naismith ± 10 min per 300 m of descent (−, 5–12°; +, > 12°) | + descent split by slope | slope-dependent sign | no | no | rule of thumb |
| Aitken / Tranter | time multipliers (terrain, fitness) | + fitness test | no / no | no | coarse | rule of thumb |
| Leistungskm (BASPO) | `km + up/100 + steep_down/150` (> 20 % only) | dist, gain, steep loss | steep only | no | no | rule of thumb |
| Schweizer Wanderwege simple rule | `km + up/100 + down/200` [derived from ¼ h rules] | dist, gain, loss | all descent, ½ weight | no | explicitly excluded | rule of thumb |
| Schweizer Wanderwege official | 15th-degree slope polynomial × length, 4.2 km/h flat | per-segment slope | yes, via polynomial | no | excluded | empirical, field-checked |
| DIN 33466 | max(h, v) + ½ min(h, v); 4 km/h, 300 m/h up, 500 m/h down | dist, gain, loss | yes | no | no | rule of thumb |
| Yamamoto コース定数 | `1.8h + 0.3km + 10·up_km + 0.6·down_km` (kcal/kg) | time, dist, gain, loss (+ body + pack for kcal) | yes, small weight (6 % of ascent) | no | no (separate A–E technical scale in prefectural gradings) | measured (portable gas analysis, 中原ら 2006) |
| Tobler | `∫ ds / (6·e^(−3.5|S+0.05|))` | per-segment slope | yes, symmetric | no | off-path ×3/5 speed | fitted to Imhof's data |
| Minetti Cw / Cr | `∫ C(i) ds` (J/kg) | per-segment slope, gait | yes, measured | no | no (smooth treadmill) | measured |
| Pandolf + η | metabolic rate with terrain factor | mass, load, speed, grade, surface | Pandolf itself has none (a separate downhill correction exists) | no | yes, η 1.0–4.1 | measured (military) |

Where the numbers disagree:

- **Descent weight relative to ascent:**
  - 0: ITRA, Naismith, Scarf
  - 6 %: Yamamoto coefficient only, not counting the extra time
  - 50 %: Schweizer Wanderwege simple rule
  - 60 %: DIN ratio of vertical speeds
  - 67 %, steep sections only: BASPO
  - either sign: Langmuir
- **Climb-to-flat ratio:**
  - Time-based rules: 100 m ≈ 0.8–1.0 km.
  - Energy measurements [derived]: 100 m ≈ 1.4 km for running, ≈ 2.6 km for walking.
  - Yamamoto without its time term: 1.0 / 0.3 ≈ 3.3 km per 100 m. With the time term the
    figure falls, and it depends on the assumed speed.

## 9. What is plausible for 百岳 versus trail racing

These are our recommendations, not sourced claims.

**百岳 days (walking gait, steep ± 20–45 %, 3,000–3,952 m, loaded, 8–14 h, multi-day):**

- **Yamamoto コース定数 (the best-founded option for this use)**
  - It is the only formula here fitted by **measured energy** on **loaded mountain walking**.
  - It **includes descent**.
  - It converts to kcal with body + pack weight, which matters for 3–5-day loads.
  - Its ascent coefficient matches Minetti's walking data.
  - Weaknesses:
    - The time term makes it pace-dependent. For planning, use standard or map time; for
      logging GPS days, use actual moving time, as YAMAREKO does.
    - No altitude or terrain term.
    - It was calibrated on Japanese trails, not Taiwanese 碎石 or 箭竹.
- **A Minetti-Cw integral is the physiological reference.** A simple distance formula should
  be judged by how closely it reproduces this integral. A simple formula that fits it will
  weight climbing far more than ITRA does: roughly `km + up_m/40 … /50` for walking, plus some
  descent term [derived].
- **km-effort / EP will understate steep 百岳 climbs compared with flat approach walking**, and
  it misses descent completely. That is a problem on routes with long continuous descents. (We did
  not source per-route descent figures; check them against the user's GPS files.) It is still the lingua franca in Taiwan, so it is worth *displaying* for
  comparison with 健行筆記 numbers.
- **Altitude:** keep it **out** of the effort distance, because energy per metre does not
  change (Wehrlin & Hallén). Put it into **intensity and time**: expect lower pace or higher HR
  for the same effort distance, on the order of 6–8 % lower VO2max per 1,000 m [measured up to
  2,800 m; extrapolated above that].
- **Terrain:** no Taiwan-specific measured factors exist. If we add one, label it a
  user-adjustable heuristic. Candidates are Pandolf η 1.2–1.5 for overgrown or 箭竹 trail and
  about 1.28 for walking on uneven ground (Voloshina 2013).

**Trail racing (running gait, moderate grades, 20–100 km):**

- **km-effort is acceptable.** It is the ITRA/UTMB/Taiwan-race standard, so it is directly
  comparable with race listings. Its 1:100 ratio sits close to the running-energy equivalence
  of about 1:140 [derived]. Its known gaps are descent (quad damage on long downhills) and
  terrain. If we want a descent-aware variant, a Minetti-Cr integral is the principled one.
  BASPO's `steep_down/150` is the only published simple add-on, and it is a rule of thumb.
- The terrain penalty in percent is small for running (+5 % on mildly uneven ground) and
  large for walking (+28 %). A hybrid race with long power-hiking sections sits in between.

**Open items we could not verify:**

- ITRA's own FAQ wording (the page renders client-side).
- The full text of Scarf 2007 (403).
- The full text of 中原ら 2006 (subjects, fit error).
- YAMAP's time source (403).
- Race-by-race ITRA data for 環花東 and 福爾摩沙古道.
