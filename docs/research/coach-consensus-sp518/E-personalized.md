# SP-518 E-personalized：已做個人化（SP-69／SP-511）的常數，查教練共識

日期：2026-10-14。範圍：只查，不改 repo。repo 唯讀：`/Users/logan/Projects/TrailRunCoach-wt-integrate-1012a`。

驗證標記：**已驗證**＝這次 WebFetch 讀到原文那句（引原句）；**已驗證（摘要）**＝讀到期刊摘要原句；**未驗證（摘要）**＝只看到搜尋摘要；**二手**＝別人轉述（寫明誰）；**沿用 <文件>§**＝repo 研究文件已驗證，這次沒重讀。

判斷原則（使用者）：找得到教練共識就用共識、拿掉個人化。這組常數多半是「量測誤差容忍」，教練通常不給數字；查不到就寫查不到。

---

## 總表

| # | 常數 | 現值（個人化範圍） | 共識（眾數／中位數／範圍） | 來源數（驗證幾個） | 建議 |
|---|---|---|---|---|---|
| 1a | `IN_BAND_TOL` | 0.98（0.95–0.99） | 教練沒有「帶內容差」的數字。最接近：TrainerRoad「降 1–5 % 還在目的內」→ 0.95–0.99；Stryd 不給容差（＝1.00）。量測雜訊：Stryd 再測 CV ≤ 4.3 %、CP 估計 SEE 約 3 % | 8（驗證 6） | **查不到、維持現狀**（預設 0.98＋個人化） |
| 1b | `LAST_FADE` | 0.05（0.03–0.10） | 依每趟長度：Allen ≤ 2 分 10–12 %、3 分 8–9 %、5 分 5–7 %、10 分 4–6 %、20 分 3–5 %（取下限）；CTS 引同書 15 %；WKO 講者反對固定 % | 4（驗證 2、沿用 2） | **改用共識（Allen 依長度表）、拿掉個人化**（信心中低：只有一位教練的表） |
| 1c | `TIZ_GOAL` | 0.85（0.75–0.90） | **0.80**（TrainingPeaks、intervals.icu、TrainerPlan 綠燈都是 80–120 %） | 4（驗證 1、二手 1、摘要 2） | **改用共識 0.80、拿掉個人化** |
| 1d | `TARGET_DOWN` | 0.95（沒有個人化） | 5 %（Golich 5–10 %；TrainerRoad 課中降 1–5 %、先降 2–3 %） | 3（驗證 2、沿用 1） | 維持 0.95（已是共識範圍） |
| 2 | `TEST_AGE_DAYS` | 56 天（56–168 天） | 眾數＝中位數 **56 天**（「每 4–8 週」「每 6–8 週」的上緣）；範圍 3 週–6 個月（6 個月只有 UA 的 AeT 測試） | 10（驗證 6） | **改用共識 56 天、拿掉個人化** |
| 3 | 輕鬆跑心率餘裕 AeT +3 | 3 bpm（3–6） | 教練：**0**（UA、MAF、Seiler 都把 AeT／VT1 當上限）；Friel AeT 課 ±2 bpm（二手）。+3 是量測雜訊：Lamberts 2009 日間 3 ± 1 bpm（最穩的強度）、Buchheit 2014 HRex CV 約 3 %（≈ 4 bpm） | 7（驗證 4） | **查不到教練的「餘裕」數字、維持現狀**（預設 3＋個人化） |
| 4 | `EASY_F` | 0.78（0.65–0.85） | 8 套系統的輕鬆跑中心值：中位數 **0.79**，範圍 0.68–0.83；上限眾數 0.80 | 9（驗證 4） | **共識當預設（0.78 不用改）、保留個人化** |
| 5 | `RPE_FRAC` | 表 × 0.85–1.15 | 錨點「CP ≈ RPE 8」是眾數（UA、CTS、TrainerRoad 騎車；TrainerRoad 跑步是 7）；低端各家差 1–2 級 | 9（驗證 8） | **共識當預設（表不用改）、保留個人化** |

---

## 1. 間歇達標

### 1.0 程式位置與現值

| 常數 | 位置 | 現值 | 個人化（`engine/interval_calib.py`） |
|---|---|---|---|
| `IN_BAND_TOL` | `backend/engine/quality_gate.py:755` | 0.98：一趟平均功率 ≥ 計畫下限 × 0.98 算帶內 | 1 − 本人相鄰兩次 CP 測試變動中位數的一半；0.95–0.99；k = 5 |
| `TARGET_DOWN` | `quality_gate.py:756` | 0.95：第一趟就沒到 → 目標 −5 % | 沒有 |
| `LAST_FADE` | `quality_gate.py:758` | 0.05：只有最後一趟沒到、掉 ≤ 5 % 仍算達標（全部長度一個數） | 本人「前 n−1 趟都在帶內」那些課的最後一趟掉幅 P90；0.03–0.10；k = 20 |
| `TIZ_GOAL` | `interval_eval.py:66`、`quality_gate.py:759` | 0.85：區內時間 ≥ 計畫 85 % | 本人「每趟都在帶內」那些課的 TIZ ÷ 計畫 P10；0.75–0.90；k = 20 |

注意：`interval-adaptation.md` §4.3（2026-10-05 補查）已建議 `LAST_FADE` 改成依每趟長度（Allen 表下限、基準用第 3 趟），但程式現在仍是一律 0.05（`quality_gate.py:758`、`interval_calib.DEFAULT_FADE`）。

### 1.1 來源表

**平台「達標／完成」怎麼判（對應 `TIZ_GOAL`、`IN_BAND_TOL`）**

| 作者／平台 | 出處 | 原文數字與基準 | 驗證 |
|---|---|---|---|
| TrainingPeaks | help.trainingpeaks.com/hc/en-us/articles/204861204（403 讀不到）；MyProCoach 說明頁逐字轉載 support.myprocoach.net/hc/en-us/articles/360040273691 | 綠「Within ±20% of the plan (completed as expected)」；黃「50–79% or 121–150% of planned values」；橘「More than 50% above or below plan」。基準：**整堂的 TSS／時間／距離**（依設定的優先順序），不是區內時間 | 二手（MyProCoach 逐字轉載；TP 原頁 403） |
| intervals.icu | forum.intervals.icu（多篇） | compliance＝實際 load ÷ 計畫 load（沒有 load 時用時間）；80–120 % 綠、50–150 % 橘、其他紅 | 未驗證（摘要，論壇） |
| TrainerPlan | trainerplan.co/help（compliance colors） | 綠 80–120 %、黃 50–79／121–150 %、橘 < 50 或 > 150 % | 未驗證（摘要） |
| Stryd | blog.stryd.com/2020/10/09/closer-look-learn-from-every-workout/ | 每段平均功率對目標帶：「A green bar means you were in the power range with the correct average power for your segment」；「A gray bar means you were under the power range」。**沒有寫容差**（＝帶下限 × 1.00） | 已驗證 |
| Stryd Recap | help.stryd.com/en/articles/15281059 | Great／Tolerable「Close to the plan with minor deviations」／Growth Opportunity「Significant drift from the target」；**沒有數字** | 已驗證 |
| TrainerRoad | trainerroad.com/blog/when-is-it-ok-to-bailout-of-a-workout/ | 「Decreases in intensity in the range of 1-5% should be employed」，「anything below that can start to detract from the purpose of the workout」；例子：計畫 100 % FTP 卻只能做 94 %「might be a little too far from the workout's intended goals」；閾值課「unable to maintain power above 95% of FTP」才算失去效果；VO2 課看第 2–4 趟，低於 110 % FTP（計畫更高時）才調整 | 已驗證 |
| TrainerRoad | trainerroad.com/blog/how-to-save-your-workout-from-failure/ | 「To start, lower it 2-3%」 | 已驗證 |
| Golich（WKO 研討會） | 筆記 ROLE:499、ROLE:555 | 第一趟就不到 → 下修 5～10 %；接近 95 % 下修 | 沿用 `interval-adaptation.md` §4.2–4.3 |
| Palladino Part 4 | Google Docs 公開頁（2022-02） | 判斷目標對不對＝整堂都在目標帶內完成＋5 分制主觀感受；**沒有容差 %** | 沿用 `interval-adaptation.md` §3.6.4 |

**量測誤差（`IN_BAND_TOL` 的 2 % 本來就是在吸收這個）**

| 研究 | 出處 | 數字 | 驗證 |
|---|---|---|---|
| Cerezuela-Espejo 2021 | *Eur J Sport Sci*，DOI 10.1080/17461391.2020.1748117 | 5 種跑步功率計再測，Stryd：「SEM ≤ 12.5 W, CV ≤ 4.3%, ICC ≥ 0.980」 | 已驗證（摘要） |
| （Stryd vs RunScribe，2021，*Proc IMechE Part P*） | journals.sagepub.com/doi/pdf/10.1177/1754337120984644 | 跑步機，Stryd CV 0.32 ± 0.29 % | 未驗證（摘要） |
| Galbraith（臨界速度單次到場三趟） | — | CS 再測 CV 0.4 %、D′ 13 % | 沿用 `cp-test-protocols.md` §3（表第 7 列） |
| 20 分 TT 推 CP | — | SEE 約 3 %（CP 200 → ± 6 W） | 沿用 `cp-test-protocols.md` §0 #5、`power-zone-width.md` §2.4 |
| Maunder 2022（騎車 3 分全力） | *Eur J Appl Physiol*，DOI 10.1007/s00421-022-05020-3 | end-test power「coefficient of variation, 4.5% [95% confidence limits, 3.7, 5.5%]」 | 已驗證（摘要） |
| Malcata & Hopkins 2014 | *Sports Med* 44:1763，DOI 10.1007/s40279-014-0239-x | 菁英比賽成績的個人內變異：「Times in sprint and endurance sports also have relatively low variability (0.6-1.4%)」 | 已驗證（摘要） |
| Buchheit 2014 | — | 運動中心率 HRex CV 約 3 % | 沿用 `interval-adaptation.md` §3.1 |

**最後一趟掉幅（`LAST_FADE`）**

| 來源 | 出處 | 數字與基準 | 驗證 |
|---|---|---|---|
| Hunter Allen | Power Blog「Increasing Repeatability and Peak Power」 | 「Intervals to Exhaustion」以**第 3 趟**為基準，掉 5–12 % 就停；依每趟長度：20 分 3–5 %、10 分 4–6 %、5 分 5–7 %、3 分 8–9 %、2 分 10–12 %、1 分 10–12 %、30 秒 12–15 % | 沿用 `interval-adaptation.md` §3.6.3（全文） |
| Rutberg／CTS（引 Allen & Coggan 書） | trainright.com/weekend-reading-knowing-when-enough-is-enough/ | 第 3 趟為基準，掉 > 15 % 停；分組課掉 ≥ 15 % 先多休 1 分 | 沿用 §3.6.3（這次搜尋摘要也一致）；書原文未核對 |
| TrainerRoad 論壇讀者（轉述同書） | trainerroad.com/forum（max repeatable efforts） | 5 分的趟：比第 3 趟低 5–7 % 停 | 二手（論壇讀者轉述書） |
| WKO 講者（Golich、Cusick） | 筆記 IT2:84-86、IYT:758-759 | 反對「掉 X % 就收工」；看第幾趟掉出帶；**最後一趟掉沒關係** | 沿用 §3.6.3 |
| Daniels | — | 「最後一趟還能跑同速」之類的明確規則：**查不到原文**（搜尋只有第三方 VDOT 工具「final reps fade → reduce reps next time」） | 查不到 |
| Spencer 2006、Oliver 2009 | — | 掉幅分數本身很不可靠（TE 14.9 %） | 沿用 §3.6.3 |

### 1.2 統計

- **`TIZ_GOAL`**：3 個平台的「完成」綠燈下限都是 **80 %**（眾數＝中位數＝0.80，範圍 0.80–0.80）。驗證：TP 二手逐字轉載 1、論壇／說明摘要 2；Stryd 不給數字。
- **`IN_BAND_TOL`**：沒有教練直接給「帶下限的容差」。可換算的：TrainerRoad「降 1–5 % 還有效」→ 0.95–0.99；TrainerRoad 例子「94 % 算太遠」→ > 0.94；Stryd 綠條 → 1.00；Golich 95 % → 0.95。中位數約 0.97，範圍 0.94–1.00。量測面：功率計再測 CV 0.3–4.3 %、CP 估計約 3 %、比賽成績本身 0.6–1.4 %。
- **`LAST_FADE`**：只有 Allen 一張依長度的表（＋CTS 15 %、論壇轉述 5–7 %）。3–5 分鐘的趟（app 階梯的主力）下限 5–8 %；2 分以下 10 %；10 分以上 3–4 %。一個數字代表全部長度沒有來源支持。
- **`TARGET_DOWN`**：Golich 5–10 %、TrainerRoad 1–5 %（課中）／先降 2–3 %。中位數約 5 %。

### 1.3 換算

- TP／intervals.icu 的 80 % 是「整堂 TSS／時間 ÷ 計畫」，app 的 TIZ 是「目標帶內時間 ÷ 計畫」——後者更嚴（暖身、爬升到目標的時間不算），所以照抄 0.80 只會比平台略寬鬆、不會更嚴。推估。
- TrainerRoad 的 % 是 % FTP（騎車），app 是 % CP 的帶下限；只借「降幾 % 還在目的內」這個量級。推估。

### 1.4 建議與影響

- **`TIZ_GOAL` → 改用共識 0.80、拿掉個人化。** 理由：三個平台都用 80–120 % 當「照計畫完成」，數字一致；本人 P10 擬合要 ≥ 20 堂，而且是在用「預設判準選出來的課」上擬合，循環性高。影響：TIZ 80–85 % 的課從「未達」變「達標」，階梯稍微容易往前（只影響間歇週）。
- **`IN_BAND_TOL` → 查不到教練共識、維持現狀（預設 0.98＋個人化）。** 理由：教練給的是「目標設在哪」，不是「多少誤差算在帶內」；這個數本質是 CP 估計誤差的容忍，用本人 CP 測試間的變動估計本來就是量測誤差的正確做法。TrainerRoad 的 1–5 % 和 Stryd 再測 CV ≤ 4.3 % 都支持 0.95–0.99 這個範圍，現在的範圍不用改。
- **`LAST_FADE` → 改用共識（Allen 依長度表，取下限：≤ 2 分 10 %、3 分 8 %、5 分 5 %、10 分 4 %、≥ 20 分 3 %；基準第 3 趟）、拿掉個人化。** 理由：唯一有數字的教練來源說掉幅隨每趟長度變，一個 P90 擬合把不同長度的課混在一起，表達不出這件事；`interval-adaptation.md` §4.3 已經建議這樣改但還沒實作。信心：中低（一位教練、部落格；CTS 引同書是 15 %，取下限比較保守）。影響：2–3 分鐘的課比較容易「達標」、10 分以上的課稍嚴；只影響「只有最後一趟沒到」那一列。若不想拿掉，替代方案：保留個人化但改成「本人 P90 ÷ 依長度的 Allen 值」的一個係數。
- **`TARGET_DOWN` → 維持 0.95**（已在 Golich 5–10 %、TrainerRoad 1–5 % 的交界）。

---

## 2. 閾值多久提示重測：`TEST_AGE_DAYS`

### 2.0 程式位置與現值

- `backend/engine/threshold_confidence.py:97` `TEST_AGE_DAYS = 56`：上次 LTHR 測試超過 56 天 → 「測試」指標提示（hint，不改課表）。
- 個人化：`engine/threshold_calib.py`（`lthr_test_age_days`）：本人同類測試間 LTHR 每 30 天動幾 bpm → 動 5 bpm 要幾天；8–24 週；≥ 2 對；k = 4。

### 2.1 來源表

| 作者／平台 | 出處 | 原文數字與基準 | 驗證 |
|---|---|---|---|
| Joe Friel | joefrieltraining.com/the-30-minute-test-is-easy-really/ | 「So I have athletes do the 30-minute test about every 4 to 8 weeks.」（LTHR 30 分測試） | 已驗證（另見 `cold-start.md` [C14]） |
| Joe Friel | Training Bible | LTHR 在 Base、Build 期初各測一次（"early in the Base and Build periods"） | 沿用 `zones-and-thresholds.md` §2.5 |
| TrainingPeaks（教練部落格） | trainingpeaks.com/coach-blog/give-your-data-a-spring-cleaning/ | 「Coaches should conduct formal zone tests every six-to-eight weeks and no more than four-to-six times per year.」；進度檢查課「at least once every three-to-four weeks」 | 已驗證 |
| TrainingPeaks（教練部落格） | trainingpeaks.com/coach-blog/a-zone-setting-and-testing-deep-dive/ | 沒有週數：「The performance ability and experience level of the athlete will define the appropriate frequency」 | 已驗證（無數字） |
| Greg McMillan | mcmillanrunning.com/wp-content/uploads/2016/08/UsingMcMillanCalculatorResults.pdf | 「re-calculate your paces (usually every 4-8 weeks) to keep your fitness advancing」（比賽或覺得變強時） | 已驗證（PDF 轉文字） |
| RunnersConnect | runnersconnect.net/how-to-calculate-your-lactate-threshold/ | 「Retest every 6 to 8 weeks while you're in a build phase.」 | 已驗證 |
| 80/20 Endurance（教練 David Warden 回覆） | 8020endurance.com/when-and-how-to-test-zones/ | 「The run plans have RT (run tempo) workouts scheduled every recovery week, which is every 3rd or 4th week.」「Those RT workouts are designed to confirm or re-establish your zones.」 | 已驗證（不是 Fitzgerald 本人） |
| Stryd | help.stryd.com/en/articles/6879862 | 「We use approximately 90 days worth of runs to determine your CP.」；「每 90 天做最大努力測試」只在搜尋摘要 | 視窗已驗證；90 天測試建議未驗證（摘要） |
| Uphill Athlete | 〈Heart Rate Drift〉 | AeT 飄移測試「every 4-6 months」 | 沿用 `aerobic-base-readiness.md` §8 |
| Uphill Athlete | 〈Zone 2 Heart Rate Training〉 | 測試值「holds for six to ten weeks」 | 沿用 `zones-and-thresholds.md` §2.5 |
| Jack Daniels | DRF（Goodreads 讀者劃線） | 「When increasing the training stress, always stay at a chosen degree of stress for 4 to 6 weeks before making changes.」（講訓練壓力，不是 VDOT 重測）；第三方 VDOT 工具寫「每 4–6 週重算」 | 二手（Goodreads 讀者劃線；第三方工具） |
| Dave Scott | davescottinc.com（LT run） | 賽季初每 3 週、比賽季每 4–6 週 | 未驗證（摘要） |
| Garmin／COROS | 說明頁 | 只有自動偵測與指引測試，**沒有建議頻率** | 查不到 |

研究：

| 研究 | 數字 | 驗證 |
|---|---|---|
| Coyle 1986、Houmard 1992 | 停訓 2–4 週：次大心率 +11 bpm、血量 −9 % | 沿用 `detraining.md:47-49` |
| Londeree 1997（*MSSE* 29:837，DOI 10.1097/00005768-199706000-00016） | 閾值訓練效果的統合分析：久坐者 ES 2.32、已訓練者 ES 0.3–1.1——**有訓練的人閾值變得慢** | 已驗證（摘要） |
| 「閾值心率（bpm）訓練幾週變多少」 | Europe PMC 兩次查詢，**找不到**報告 LT 心率隨訓練週數變化的摘要（Sulowska-Daszyk 2024 只報速度） | 查不到 |
| 固定週期重測的直接證據 | 沒有 | 沿用 `zones-and-thresholds.md` §2.5 |

### 2.2 統計（取每個來源「最久多久」的上緣，單位天）

Friel 56、TP 56、McMillan 56、RunnersConnect 56、80/20 28、Daniels 42、Dave Scott 42、UA（6–10 週）70、Stryd 90、UA AeT 180。

- 眾數 **56**（4 個，其中 4 個都已驗證）；中位數 **56**；範圍 28–180。
- 驗證過：Friel、TP、McMillan、RunnersConnect、80/20、Stryd 視窗、UA × 2（沿用）＝ 8 個；二手／摘要 2 個。
- 只有 UA 的 AeT（另一種測試）和 Stryd 的模型視窗超過 8 週。**沒有任何 LTHR 測試的教練來源支持 12–24 週**，也就是現在個人化範圍（56–168 天）的整段都在共識之外或上緣。

### 2.3 換算

教練說的是「重測／重算配速」，基準多半是**配速／功率**（McMillan、Daniels、Stryd）；app 的提示是 **LTHR（bpm）**。LTHR 的 bpm 隨訓練變得比配速慢（Londeree：有訓練的人效果量小），這是個人化想抓的東西——但沒有研究給出「多慢」，個人化用的 5 bpm 門檻與 28–365 天配對窗也都是推估。

### 2.4 建議與影響

**改用共識 56 天、拿掉個人化。** 理由：
1. 教練數字很一致（4–8 週、6–8 週；上緣都是 8 週），驗證 6 個以上。
2. 個人化範圍 8–24 週整段落在共識上緣以外；只會把提示往後延，而延長沒有任何 LTHR 來源支持。
3. 這只是 hint；真正的重測觸發本來就是事件（新 CP、停跑 ≥ 4 週、熱校正後偏移、換季，`zones-and-thresholds.md` §2.5），固定週期本來就沒有直接證據。
4. 個人化要 3 次同類測試才啟動，多數使用者永遠用不到。

影響：預設值不變（56），只是不會再因為本人閾值穩定而延到 12–24 週；提示頻率對多數人不變。

---

## 3. 輕鬆跑心率餘裕：AeT +3 bpm

### 3.0 程式位置與現值

- `backend/engine/quality_gate.py:110` `FRIEL_HR_BAND = (-5.0, 3.0)`（「在 AeT」＝ AeT −5 … +3；+3 個人化經 `friel_band`）。
- `backend/engine/threshold_calib.py` `DEFAULT_MARGIN = 3.0`、`MARGIN_BOUNDS = (3.0, 6.0)`：本人 AeT 聚合估計的標準誤，下限 3（Lamberts & Lambert 2009）；≥ 6 個飄移點；k = 6。
- `workout_review.AET_MARGIN` 3（分類、圖表）不個人化。

### 3.1 來源表

**教練：輕鬆跑心率的上限離 AeT 多遠**

| 作者／平台 | 出處 | 原文數字與基準 | 驗證 |
|---|---|---|---|
| Uphill Athlete | uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/ | Zone 1「AeT-20% to AeT-10%」、Zone 2「AeT-10% to AeT」；「Most of your weekly endurance training should be genuinely easy aerobic work in Zone 1 and Zone 2」。**上限＝AeT，沒有往上的餘裕**；頁面沒有任何 bpm 容差 | 已驗證 |
| Phil Maffetone（MAF） | philmaffetone.com/180-formula/ | 「the ideal maximum aerobic heart rate in which to base all aerobic training」；「When exceeded, this number indicates a rapid transition towards anaerobic work.」——**上限，不加餘裕**；「MAF −10 的訓練範圍」這頁沒有寫 | 已驗證（−10 範圍查不到） |
| Seiler 三區 | — | Zone 1 ＝ VT1／LT1 以下全部 | 沿用 `coach-schools-zones-periodization.md` Finding 1、`power-zone-width.md` §2.2 |
| Fitzgerald 80/20 | — | 低強度＝Zone 1–2（81–90 % LTHR 上緣），VT1 在 Zone X（Z2 之上）——**上限在 VT1 之下** | 沿用 `coach-schools-zones-periodization.md` 表 2a [20][40] |
| Joe Friel | Training Bible 5th ed.（TrainerRoad 論壇讀者轉述） | AeT 課：目標 LTHR − 30 bpm，「+/- 2bpm」；AeT「20 to 40 bpm below」LTHR | 二手（論壇讀者轉述書） |
| Friel Zone 2 | trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones/ | 跑步心率 Zone 2「85% to 89% of LTHR」（寬 4 % LTHR ≈ 6–7 bpm） | 已驗證 |

**研究：心率本身的日間雜訊**

| 研究 | 出處 | 數字 | 驗證 |
|---|---|---|---|
| Lamberts & Lambert 2009 | *J Strength Cond Res* 23:1005，DOI 10.1519/jsc.0b013e3181a2dcdc | 「The lowest variation in heart rate was found at the end of the last stage of the test in the 85-90% group (3 +/- 1 b·min)」；61–98 % HRmax 都量了，**變異隨強度上升而變小**——輕鬆跑（約 70–80 % HRmax）的日間變異比 3 bpm 大 | 已驗證（摘要） |
| Buchheit 2014 | — | 運動中心率 HRex CV 約 3 %（心率指標裡最可靠）；140 bpm × 3 % ≈ 4 bpm | 沿用 `interval-adaptation.md` §3.1（全文） |
| Achten & Jeukendrup 2003 | *Sports Med*，DOI 10.2165/00007256-200333070-00004 | 摘要只說「There appears to be a small day-to-day variability in HR」；「2–4 bpm」**摘要裡沒有**，全文未讀 | 已驗證（摘要，無數字） |

### 3.2 統計

- 教練給的「往上餘裕」：UA 0、MAF 0、Seiler 0、80/20 < 0（上限在 VT1 之下）→ **眾數 0**；Friel ±2 是「AeT 課怎麼守住」的執行容差（二手）。
- 雜訊：Lamberts 3 ± 1 bpm（最穩的 85–90 % HRmax；輕鬆強度更大）、Buchheit CV 3 %（≈ 4 bpm）。中位數約 3–4 bpm。
- 驗證：UA、MAF、Friel Zone 2、Lamberts 4 個；沿用 3 個；二手 1 個。

### 3.3 換算

app 的 +3 不是「教練允許超過 AeT 3 下」，而是「AeT 本身和當天心率都有雜訊，3 下以內不判偏強」。教練的上限（AeT）＋量測雜訊（3–4 bpm）＝ app 現在的做法。個人化用的是**本人 AeT 估計的標準誤**——這也是量測誤差，教練不會給這種數。

### 3.4 建議與影響

**查不到教練的「餘裕」數字、維持現狀（預設 3、保留個人化 3–6）。** 理由：
- 教練的共識是「AeT 是上限、0 餘裕」；照抄成 0 會讓單純的心率雜訊被判「偏強」，觸發不必要的調整。
- +3 是量測容差，Lamberts 2009 顯示輕鬆強度的日間變異比 3 bpm 還大，所以 3 當下限、本人 AeT 估得越不準就放越寬（到 6），方向正確。
- 小補充（不是建議改值）：說明文字可以寫「教練的上限是 AeT 本身；+3 是心率每天自然會差的幅度」，避免讓人以為教練允許超過。

---

## 4. 輕鬆跑強度：`EASY_F = 0.78`

### 4.0 程式位置與現值

- `backend/engine/workout_steps.py:1358` `EASY_F = 0.78`：輕鬆跑 ≈ 0.78 × CP。用途：(1) `speed_kmh` 的錨點——本人輕鬆跑速度當作 0.78 CP，再和閾值配速（100 % CP）之間線性內插，決定每個距離步驟的時間；(2) 計畫 TSS。
- 個人化：`engine/intensity_calib.py`（`easy_run_if`）：近 26 週平路輕鬆跑（≤ AeT + 3）的 IF 中位數——和輕鬆跑速度是**同一批跑步**；≥ 10 次；0.65–0.85；k = 10。

### 4.1 來源表

| 作者／系統 | 出處 | 原文數字與基準 | 換成 % CP（推估） | 驗證 |
|---|---|---|---|---|
| Stryd（Run Type） | help.stryd.com/en/articles/14360092 | 「Recovery Run / Easy Run: relaxed, conversational efforts at 55–80% of your CP」；暖身、緩和也是 55–80 % | 55–80，中心 67.5 | 已驗證 |
| Stryd（5 區） | 說明中心（CP 252 → Zone 1 164–202 W） | Zone 1 Easy 65–80 % CP；「輕鬆跑約 190 ± 5 W」（≈ 75 % CP） | 65–80，中心 72.5；例子 75 | 沿用 `power-zone-width.md` §2.2（轉述）；這次搜尋摘要也是 164–202 W |
| Steve Palladino（10 區） | 〈Running Power Zones〉 | 1B 65–75、1C 75–80（「輕鬆有氧跑」）；Stryd 內建 EZ 課 ≤ 80 % CP；「大多數跑者的輕鬆跑約 75–80 % FTP」 | 1C 中心 77.5；上限 80 | 區間沿用 `coach-schools-zones-periodization.md` 表 2b [23]；「75–80 %」未驗證（摘要），這次也找不到原句 |
| Fitzgerald 80/20（功率） | 80/20 區間表 | Z1 50–76、Z2 76–88 % rFTP；Foundation run 在 Z2 | Z2 中心 82（rFTP ≈ CP，推估） | 沿用 `coach-schools…` 表 2b [20] |
| Fitzgerald 80/20（配速） | 同上 | 速度 Z1 60–76、Z2 76–87 % 閾值速度 | Z2 中心 81.5 | 沿用同上 |
| Joe Friel（配速） | trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones/ | 「Zone 2 114% to 129% of FTP (78-88%)」（括號是速度 %）；Zone 1「Slower than 129% of FTP (78%)」 | Z2 78–88，中心 83 | 已驗證 |
| Jack Daniels（E） | VDOT 公式（Daniels–Gilbert） | E＝59–74 % VO2max（rundida FAQ「59-74% of VDOT intensity」） | 用 Daniels–Gilbert 速度–VO2 式換算：E ≈ 72.5–87 % T 速度（T＝88 % VDOT），中心約 80（VDOT 40／50／60 都一樣） | 59–74 % 未驗證（第三方摘要）；換算是本次計算 |
| Pfitzinger（GA） | Advanced Marathoning（論壇讀者用書算） | General aerobic 比馬拉松配速慢 15–25 % | 速度 80–87 % MP；MP ≈ 94 % T（`coach-schools` 表 2b Daniels）→ 75–82 % T，中心 78.5 | 二手（runningahead 論壇讀者轉述） |
| Uphill Athlete | — | 輕鬆＝AeT 以下；AeT 約 80 % CP（這位跑者推估） | 上限 ≈ 80 | 沿用 `power-zone-width.md` §2.3 |
| Coggan／Jim Vance 跑步功率區間 | — | Coggan 只有騎車（L2 56–75 % FTP）；Vance《Run with Power》區間表**這次讀不到原表**，網路上的「Zone 2 75–85 %」「80–90 %」都不是 Vance 原文 | — | 查不到 |
| McMillan | 計算器說明 PDF | 只給 Endurance 區的配速範圍，沒有 % 閾值 | — | 查不到 % |

### 4.2 統計（各系統輕鬆跑的**中心值**，% CP，推估換算）

Stryd Run Type 67.5、Stryd Zone 1 72.5、Palladino 1C 77.5、Pfitzinger GA 78.5、Daniels E 80、80/20 配速 Z2 81.5、80/20 功率 Z2 82、Friel Z2 83。

- 中位數 **0.79**（78.5 與 80 之間）；範圍 0.68–0.83；上限的眾數 **0.80**（Stryd、Palladino、UA）。
- 來源 11 個（可用數字 9 個），驗證 4 個（Stryd Run Type、Friel、沿用的 Stryd Zone 1、Palladino 區間），其餘二手／摘要／本次計算。
- 各家帶寬 12–25 %，輕鬆跑的中心從 0.68 到 0.83 都有人用——**沒有「每個人的輕鬆跑都在 0.78」這種共識**。

### 4.3 換算

- % 閾值速度 → % CP：跑步功率在平路約和速度成正比（Stryd；`workout_steps.speed_kmh` 本來就這樣假設），閾值配速 ≈ CP 速度。所以 % T 速度 ≈ % CP，誤差幾 %（推估）。
- 程式裡 `EASY_F` 的角色不是「建議跑多快」，而是「**本人實際輕鬆跑的速度等於幾 % CP**」：速度取自本人跑步、% CP 若用全體預設，兩個錨點來源不一致。

### 4.4 建議與影響

**共識當預設（0.78 不用改）、保留個人化。** 理由：
- 0.78 剛好落在 8 套系統中心值的中位數（0.79）旁邊、上限 0.80 之下，預設沒有理由改。
- 但這個數描述的是「這位跑者的輕鬆跑實際在哪」，各家中心差到 15 %（0.68 vs 0.83）；個人化用的是同一批輕鬆跑的實測 IF，等於讓速度錨點和 % CP 錨點來自同一組資料——這是一致性，不是另訂教練規則。拿掉會讓距離步驟的時間和 TSS 對不準。
- 個人化範圍 0.65–0.85 和各家輕鬆跑帶（Stryd 55/65–80、80/20 Z2 到 88）一致，不用改。

影響：無（維持現狀）。

---

## 5. RPE → % CP 表：`RPE_FRAC`

### 5.0 程式位置與現值

- `backend/engine/workout_steps.py:117` `RPE_FRAC = {1: 0.55, 2: 0.62, 3: 0.70, 4: 0.76, 5: 0.82, 6: 0.88, 7: 0.94, 8: 1.00, 9: 1.05, 10: 1.10}`（Borg CR-10，Foster 2001 session RPE 的字眼）。只決定 RPE 步驟（技術地形／下坡）的圖表高度與計畫 TSS；推到手錶的負荷用預設表。
- 個人化：`engine/intensity_calib.py`（`rpe_if_scale`）：整張表 × 一個係數 0.85–1.15（本人有評 RPE 的活動，IF 中位數 ÷ 表值，加權平均）；每級 ≥ 3 次；k = 10。

### 5.1 來源表

**教練／平台的 RPE 對強度**

| 作者／平台 | 出處 | 原文 | 換成 % CP（推估） | 驗證 |
|---|---|---|---|---|
| Uphill Athlete（1–10） | uphillathlete.com/…/uphill-athlete-training-zones-heart-rate-calculator/ | Zone 1「RPE: 2-4」；Zone 2「5-6, moderate for those with high AeT, easy for those with low AeT」；Zone 3（AeT–LT）「RPE: 7-8 Medium to comfortably hard」；Zone 4（LT–max）「RPE: 9-10 Hard, max sustainable」 | AeT（≈ 0.80）在 6/7 之間；LT（≈ 0.95–1.00）在 8/9 之間 | 已驗證 |
| Koop／CTS（1–10） | trainright.com/decoding-ultramarathon-interval-workouts/ | Recovery「4-5」、Endurance「5-6」、Steady State「7」、Tempo「8-9」（表上標 Lactate Threshold）、Running Intervals「10」 | LT ≈ RPE 8.5；VO2 間歇 10 ≈ 1.05–1.15 | 已驗證 |
| TrainerRoad 跑步 RPE（說明以 Fitzgerald 的量表為主） | support.trainerroad.com/hc/en-us/articles/360021324932 | RPE 2「Recovery」、3–4「Endurance」、5「Marathon」、6「Half Marathon」、7「10k」、8「5k」、9「Mile」、10「Sprints」 | 用 Stryd 比賽功率表（10K ≈ CP）：5 ≈ 0.90、6 ≈ 0.95、7 ≈ 1.00、8 ≈ 1.04、9 ≈ 1.16 | 已驗證 |
| TrainerRoad 騎車 RPE | 同上 | RPE 8「Very Hard」「Threshold」；例子：RPE 8 ＝ 95–105 % FTP、RPE 4–7 ≈ 55 % 到 88–94 % FTP | CP ≈ RPE 8 | 已驗證 |
| Stryd 比賽功率 | help.stryd.com/en/articles/6879547 | % 10K 功率：1.6 km 116、3 km 109.4、5 km 103.8、10 km 100、21.1 km 94.6、42.2 km 89.9 | 用來換算上一列（10K ≈ CP 是 Stryd 的說法，推估） | 已驗證 |
| Koop | 書（CTS 摘要） | 「RPE × 10 ≈ % VO2max」 | — | 未驗證（摘要；CTS 兩篇文章都沒有這句） |

**研究（多是遞增測試的短階段 RPE，不是整堂 session RPE）**

| 研究 | 出處 | 數字 | 換成 CR-10（推估） | 驗證 |
|---|---|---|---|---|
| Scherr 2013（2,560 人） | *Eur J Appl Physiol*，DOI 10.1007/s00421-012-2421-x | 「lactate threshold (LT) and individual anaerobic threshold corresponded to an RPE of 10.8 ± 1.8 and 13.6 ± 1.8」（Borg 6–20）；3、4 mmol 為 12.8、14.1 | LT1 ≈ CR-10 2–3；IAT ≈ 4–5 | 已驗證（摘要） |
| Irving 2006 | *MSSE*，DOI 10.1249/01.mss.0000227322.61964.d2 | 從 LT 到峰值，OMNI 0–10「ranged from … 3.1 to 8.2」、Borg 10.1–16.9 | LT1 ≈ 3 | 已驗證（摘要） |
| Rynders 2011 | *Eur J Appl Physiol*，DOI 10.1007/s00421-010-1821-z | 「RPE LT (10.4 ± 2.0)」（Borg 6–20） | LT1 ≈ 2–3 | 已驗證（摘要） |
| Seiler & Kjerland 2006 | *Scand J Med Sci Sports*，DOI 10.1111/j.1600-0838.2004.00418.x | session RPE 和心率區間的分布幾乎一樣：心率 75／8／17 %，session RPE 76／6／18 %。**各區的 RPE 切點摘要裡沒有**（常見的「≤ 4／5–6／≥ 7」查不到原文） | — | 已驗證（摘要）；切點查不到 |
| Ieno 2020（菁英公開水域游泳） | *J Funct Morphol Kinesiol* 5:21，DOI 10.3390/jfmk5010021 | session RPE 斷點對 LT1／LT2：「Z1 ≤3, Z2 between 4 and 6, Z3 ≥ 7」（量表沒寫） | LT1 在 3/4 之間，LT2 在 6/7 之間 | 已驗證（摘要） |
| Foster 2001 | *J Strength Cond Res* 15:109 | CR-10 字眼：3 中等、5 吃力、7 很累、10 極限 | — | 沿用程式註解（這次 Europe PMC 沒抓到摘要） |

### 5.2 統計

**錨點「哪一級 ≈ CP」**：UA 8.5、CTS 8.5、TrainerRoad 騎車 8、TrainerRoad 跑步 7、Ieno（LT2）6.5、Scherr（IAT，遞增測試）≈ 4.5。
- 教練／平台 4 個：中位數 8.25、眾數 8–8.5、範圍 7–8.5 → **和表上「RPE 8 ＝ 1.00」一致**。
- 研究把 LT2 放得低（4.5–6.5），因為量的是遞增測試 3 分鐘階段或游泳；session RPE 是整堂、持續做時會高很多，所以不拿來換算課表步驟（推估）。

**輕鬆端「≈ 0.78 CP 是幾級」**：UA 5–6、CTS 5–6（Endurance）、TrainerRoad 3–4、研究 LT1 ≈ 3（Irving、Ieno、Scherr）。表上 0.78 約在 4.3 級。各家差 **1–2 級**。

逐級對照（% CP，推估換算）：

| RPE | app 表 | UA | CTS | TrainerRoad／Fitzgerald |
|---|---|---|---|---|
| 3 | 0.70 | 0.65–0.75（Z1） | — | ≈ 0.72（Endurance） |
| 4 | 0.76 | 0.65–0.75（Z1） | 0.65–0.75（Recovery） | ≈ 0.78（Endurance） |
| 5 | 0.82 | 0.75–0.80（Z2） | 0.75–0.82（Recovery／Endurance） | 0.90（馬拉松） |
| 6 | 0.88 | 0.75–0.80（Z2） | 0.75–0.82（Endurance） | 0.95（半馬） |
| 7 | 0.94 | 0.80–0.95（Z3） | 0.85–0.92（Steady State） | 1.00（10K） |
| 8 | 1.00 | 0.80–0.95（Z3 上緣） | 0.95–1.00（Tempo） | 1.04（5K） |
| 9 | 1.05 | ≥ 0.95（Z4） | 0.95–1.00（Tempo） | 1.16（1 英里） |
| 10 | 1.10 | ≥ 0.95（Z4） | 1.05–1.15（VO2 間歇） | > 1.2（衝刺） |

app 的表在 UA／CTS（同一級給較低 % CP）和 TrainerRoad／Fitzgerald（同一級給較高 % CP）中間；兩邊各差約 1 級、≈ 6–10 % CP。

### 5.3 換算

- 教練的 RPE 多半掛在區間或比賽距離，不是 % CP。換算用：AeT ≈ 0.80 CP、LT ≈ 0.95–1.00 CP（`power-zone-width.md` §2.3、`coach-schools…` 表 2b）；比賽距離用 Stryd 的 % 10K 功率表，假設 10K ≈ CP。全部推估。
- Borg 6–20 → CR-10 沒有線性換法，研究列只取大概位置。

### 5.4 建議與影響

**共識當預設（表不用改）、保留個人化。** 理由：
- 錨點「RPE 8 ≈ CP」是教練／平台的眾數；表的形狀落在 UA／CTS 與 TrainerRoad 之間。沒有一套比 app 的表更有共識。
- 各家同一級差 1–2 級（≈ 6–12 % CP），個人對 RPE 的用法差異更大；個人化的單一係數 0.85–1.15 剛好涵蓋 UA／CTS 到 TrainerRoad 的差距，而且只保留表的形狀、不重排順序。教練之間沒有共識可以取代這個「本人怎麼用 RPE」的校正。

影響：無（維持現狀）。

---

## 限制

- TrainingPeaks 說明中心 403，compliance 色階是讀 MyProCoach 逐字轉載的版本。
- Daniels、Fitzgerald、Pfitzinger、Vance、Allen & Coggan 的書都沒讀到原頁；相關數字標二手或沿用。
- Palladino「大多數跑者輕鬆跑 75–80 % FTP」這次仍找不到原句。
- 「閾值心率（bpm）隨訓練幾週變多少」找不到研究，只有 Londeree 1997 的效果量（有訓練者小）與停訓研究。
- RPE 研究多是遞增測試的短階段，和 session RPE、課表步驟的 RPE 不是同一件事。

## 來源（這次新讀的）

- TrainingPeaks compliance（MyProCoach 轉載）：https://support.myprocoach.net/hc/en-us/articles/360040273691
- Stryd planned vs completed：https://blog.stryd.com/2020/10/09/closer-look-learn-from-every-workout/
- Stryd Recap：https://help.stryd.com/en/articles/15281059-stryd-s-recap-plain-language-summary-of-how-your-run-actually-went
- TrainerRoad bailout：https://www.trainerroad.com/blog/when-is-it-ok-to-bailout-of-a-workout/
- TrainerRoad save your workout：https://www.trainerroad.com/blog/how-to-save-your-workout-from-failure/
- TrainerRoad RPE key：https://support.trainerroad.com/hc/en-us/articles/360021324932-RPE-Key-Explained
- intervals.icu compliance（論壇摘要）：https://forum.intervals.icu/t/planned-workout-compliance/98855
- Friel 30 分測試：https://joefrieltraining.com/the-30-minute-test-is-easy-really/
- TrainingPeaks 教練部落格：https://www.trainingpeaks.com/coach-blog/give-your-data-a-spring-cleaning/ 、https://www.trainingpeaks.com/coach-blog/a-zone-setting-and-testing-deep-dive/
- Friel 區間（TrainingPeaks）：https://www.trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones/
- McMillan 計算器說明 PDF：https://www.mcmillanrunning.com/wp-content/uploads/2016/08/UsingMcMillanCalculatorResults.pdf
- RunnersConnect：https://runnersconnect.net/how-to-calculate-your-lactate-threshold/
- 80/20 Endurance：https://www.8020endurance.com/when-and-how-to-test-zones/
- Stryd：https://help.stryd.com/en/articles/6879862-my-first-few-runs-with-stryd 、https://help.stryd.com/en/articles/14360092-run-type-filter-run-type-by-category 、https://help.stryd.com/en/articles/6879547-race-power-calculator
- Uphill Athlete 區間：https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/
- MAF 180：https://philmaffetone.com/180-formula/
- CTS：https://trainright.com/decoding-ultramarathon-interval-workouts/
- Daniels（Goodreads 讀者劃線）：https://www.goodreads.com/notes/20762026-daniels-running-formula/77147819-adrian-gonzalez?page=2
- Europe PMC 摘要：Cerezuela-Espejo 2021（10.1080/17461391.2020.1748117）、Maunder 2022（10.1007/s00421-022-05020-3）、Malcata & Hopkins 2014（10.1007/s40279-014-0239-x）、Londeree 1997（10.1097/00005768-199706000-00016）、Lamberts & Lambert 2009（10.1519/jsc.0b013e3181a2dcdc）、Achten & Jeukendrup 2003（10.2165/00007256-200333070-00004）、Scherr 2013（10.1007/s00421-012-2421-x）、Irving 2006（10.1249/01.mss.0000227322.61964.d2）、Rynders 2011（10.1007/s00421-010-1821-z）、Seiler & Kjerland 2006（10.1111/j.1600-0838.2004.00418.x）、Ieno 2020（10.3390/jfmk5010021）
