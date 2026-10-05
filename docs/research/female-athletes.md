# 女性跑者：月經週期、低能量可用性（REDs）與鐵質（SP-192）

> 調查日期：2026-10-05。只做調查，沒有改程式。
> 接續 `periodization-cross-sport.md` 第 560 行「依生理期調整：不做」[278][286][308]，那三篇這次重讀了。
> 標記：**全文**＝讀到原文的相關段落（不一定通讀）；**摘要**＝只讀到摘要原文（Europe PMC）；**二手**＝A 文轉述 B 文，B 沒讀；**搜尋摘要**＝只看到搜尋結果的轉述，等於未驗證；**廠商／機構**＝產品或機構頁面；**推估**＝我的延伸；**未找到來源**＝查了沒有。
> 絕大多數來源只到摘要層級。引號內是原文。參考文獻的作者、年份、期刊、標題已用 Europe PMC 依 DOI 逐筆核對過。本文編號用 [F1]、[F2]…；舊文件的編號照原樣寫 [278]。
> 這份文件不是醫療建議。裡面所有「建議就醫」的門檻都來自共識或指引，app 只能轉述，不能判斷。

## 摘要

1. **依月經週期的「階段」排課：維持不做。** 各階段的表現差異是「trivial」，證據品質被作者自己評為 low [F1]。耐力訓練的試驗現在是 2 篇沒差別 [F27][F28]、1 篇有差別但在誤差邊緣 [F32]。正確的說法是「證據不足且不一致」，不是「已證明沒用」。主流廠商（COROS、Garmin、Apple）也都只做記錄和提醒，沒有一家自己改課表 [F45][F46]。
2. **熱、心率、受傷風險都不需要依階段調整。** 黃體期體溫高 0.3–0.7 °C [F6]，安靜心率高約 2–4 bpm [F15][F16]，但熱天表現變差只有一個 8 人的試驗支持 [F8]；受傷和階段的研究互相矛盾，而且全是球類 [F19]–[F23]。
3. **真正值得做的是「月經有沒有來」，不是「現在是哪個階段」。** 月經變少或停掉是能量不足最早、也最容易自己看到的訊號。月經失調的跑者骨應力傷害是 2.25–4.5 倍 [F24][F66]；Triad 風險分數裡獨立預測骨傷的就是「月經」和「以前的骨傷」兩項 [F69]。英國運動科學院的文件寫得很直接：三個月沒來要就醫、停經是警訊 [F42]。
4. **能量不足（REDs）不罕見，男性也有。** 女性越野跑者的篩檢問卷陽性約一半（49.7 %、55.1 %，線上自填，會高估）[F63][F64]。10–14 天的能量不足就讓計時表現掉 7.8 %，回補 3 天沒有恢復 [F73]。
5. **app 不該算能量可用性，也不該照搬 LEAF-Q 打分數。** IOC 共識自己說 30 kcal 門檻來自 29 位久坐女性的 5 天實驗、不該當臨床門檻 [F53][F55]。LEAF-Q 只適合「排除」，在混合族群有一半以上超標 [F58]，沒有中文驗證版，對休閒越野跑者也沒有驗證。app 能做的是問幾個事實、命中就請使用者找醫師，不打分數、不貼燈號。
6. **鐵質：app 只提醒驗血，不給切點、不給劑量。** 女性運動員約 15–35 % 缺鐵、男性 5–11 % [F79]；台灣育齡女性缺鐵性貧血 13.7 % [F95]。但休閒馬拉松男性有 15 % 是鐵過多 [F88]，所以一定是先驗血再補。百岳這種 2–5 天的行程沒有任何鐵質建議的依據（研究都是 2–4 週的高地營）[F92][F93]。
7. **建議開 6 張實作單**（§9）。最優先的兩張是「月經紀錄＋沒來的提醒」和「傷病紀錄加骨應力傷害」。全部做成選用、預設關、只存本機，照傷病紀錄現有的隱私做法。

## 1. app 現況

### 1.1 性別欄位在哪裡、誰在用

| 項目 | 位置 | 說明 |
|---|---|---|
| 欄位定義 | `backend/engine/planning.py:233`（`PROFILE_FIELDS`）、`:244`（`Plan.profile`） | 只有 `male`／`female`，可以不填 |
| 輸入 | 設定頁 `backend/static/settings.html:112`；首次精靈 `backend/static/setup_wizard.js:49`；API `backend/api/plan.py:464`（`put_profile`） | 精靈會問到體重和性別都有為止（`backend/engine/athlete_profile.py:102`） |
| W′ 先驗 | `backend/engine/cp_protocols.py:43`、`backend/engine/racepower/cptest.py:89` | 女 6.4 ± 2.2 kJ、男 13.1 ± 4.0 kJ；沒填用男性值並標推估 |
| W′ 評等表 | `backend/engine/racepower/cp.py:62` | 男女各一組區間 |
| 熱量估算 | `backend/engine/racepower/fuel.py:143`（Keytel 心率公式）、`:220`（Mifflin-St Jeor 基礎代謝）；預設值 `:80` | 只用在賽事計算機和百岳每日熱量 |
| 分享連結 | `backend/engine/racepower/share.py:11` | 身高、年齡、性別一律不出現 |

### 1.2 排課、閾值、恢復：完全不分性別

在 `overview.py`、`projection.py`、`plan_prefs.py`、`plan_auto.py`、`load_guard.py`、`reentry.py`、`quality_gate.py`、`status.py`、`thresholds.py`、`zones.py`、`heat.py`、`heat_plan.py`、`heat_calib.py`、`injuries.py`、`injury_exposure.py`、`race_feasibility.py` 搜尋 `sex`，都是 0 筆。單上的描述正確。

| 單上列的程式 | 現在怎麼做 | 和這張單的關係 |
|---|---|---|
| `planning.Plan.profile` | 性別、身高、出生年、功率來源 | 沒有月經、避孕、驗血這類健康欄位 |
| `overview.week_plan`（`backend/engine/overview.py:955`） | 週量由 CTL 目標、TSB、連續加量週數決定（`:1029–1058`）；傷病只加一行提示（`:1089–1093`），事件勾了暫停強度課時不排間歇（`backend/engine/injuries.py:402`） | 沒有「今天狀況不好」的輸入。RPE 只用來換算計畫負荷，不會回頭修正負荷（`backend/engine/rpe_load.py:5–7`） |
| `heat`（`backend/engine/heat.py`） | 熱適應狀態由熱暴露分鐘數推算（`:33–44`）；心率的熱修正用每個人自己擬合的係數（`heat.py:70`、`backend/engine/heat_calib.py:273`） | 沒有體溫輸入 |
| `injuries`（`backend/engine/injuries.py`） | 部位 9 種加自訂（`:30`）；類型只有過度使用和急性（`:35`）；只存本機、不進分享連結和示範模式（`:19–21`）；固定一行「這不是醫療診斷」（`:64`）；資料表 `backend/db/models.py:290` | 沒有「骨應力傷害」這個類型，也不會數兩年內第幾次。**隱私做法可以直接沿用** |

### 1.3 其他相關的現況

- **沒有每日生理資料。** 安靜心率只有使用者設定值或 COROS 帳號值（`backend/engine/hr_profile.py:19–21`）；沒有夜間心率、HRV、體溫、睡眠。專案原則也記著擁有者只有跑步時戴錶。所以「用穿戴裝置偵測週期階段」這條路在這個 app 走不通。
- **沒有體重趨勢。** 設定頁只留一筆體重，存檔會覆寫（`backend/static/settings.html:110`）。
- **算不出能量可用性。** app 只算比賽和百岳行程的消耗（`backend/engine/racepower/fuel.py`），沒有日常攝取。
- **補給提醒只在兩種課。** B2B 和專項期的比賽演練有每小時醣量的文字（`backend/engine/b2b.py:590`、`backend/engine/specific_phase.py:486`），一般長跑沒有。
- **狀態指標看得到「同心率的配速」**（EF `backend/engine/status.py:530`、心率飄移 `:592`），但沒有任何地方把「表現停滯、反覆受傷、月經沒來」連在一起。
- **不排課日期**只有一般和休息日兩種（`backend/engine/blackouts.py:43`），沒有「生病」或「身體不適」。
- **既有結論**：`docs/research/periodization-cross-sport.md:560`「依生理期調整｜兩個試驗都沒有效果｜不做」。

## 2. 文獻：月經週期各階段的影響

### 2.1 表現

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 月經期（早濾泡期）表現略差，效果量 trivial，信賴區間跨 0 | 78 篇；「a trivial effect for both endurance- and strength-based outcomes … (ES0.5 = − 0.06 [95% CrI: − 0.16 to 0.04])」 | McNulty 2020 [F1] | 摘要 |
| 證據品質低；不能訂通用指引 | 「The quality of evidence for this review was classified as "low" (42%).」；「general guidelines on exercise performance across the MC cannot be formed; rather, it is recommended that a personalised approach should be taken」 | [F1] | 摘要 |
| 傘狀回顧：各回顧結果高度不一致、方法差 | 「it is premature to conclude that short-term fluctuations in reproductive hormones appreciably influence acute exercise performance」 | Colenso-Semple 2023 [F2] | 全文 |
| 多系統回顧：差異小或不存在 | 「the consensus view is that the impact of the MC and OC use on various aspects of physiology is small or nonexistent」 | D'Souza 2023 [F3] | 摘要 |
| 自覺強度（RPE）沒有階段差異 | 17 篇；「MC phases did not impact RPE (P > .05)」 | Prado 2024 [F18] | 摘要 |
| 40 年的回顧文章彙整：多數認為沒有影響 | 「the evidence and consensus therein appears to be that the effect is small or trivial」 | Hackney 2025 [F34] | 摘要 |

判斷（推估）：方向一致，差異很小。跑步經濟性、最大攝氧量、乳酸閾的分項效果量這次沒有讀到。

### 2.2 體溫與熱

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 黃體期核心體溫高 0.3–0.7 °C | 「0.3°C to 0.7°C higher in the post-ovulatory luteal phase … most evident during sleep or immediately upon waking」 | Baker 2020 [F6] | 摘要 |
| 熱環境運動：黃體期起跑時體溫較高並維持到運動後；流汗率、皮膚溫、運動心率沒有差 | 9 篇、83 人；「No significant differences were present in mean skin temperature, sweat rate, or exercise heart rate across menstrual phases」 | Giersch 2020 [F7] | 摘要 |
| 濕熱下黃體期力竭時間縮短；溫帶沒有差 | 32 °C／60 %，每個環境只有 8 人；「exercise time to fatigue, was significantly reduced during the luteal phase」 | Janse de Jonge 2012 [F8] | 摘要 |
| 較新的兩個試驗（34 °C／60 %）：直腸溫與心率沒有階段差異 | 12 人：「no significant … MC phase effects on rectal temperature or heart rate (p > .05)」；11 名跑者的 10 km：黃體期直腸溫上升幅度反而略小 | Convit 2025 [F9][F10] | 摘要 |
| 女性短期熱適應可能不夠 | 8 名女性耐力運動員：4 天沒有進步，9 天後功率 +8.1 %；回顧：「females may require a greater number of heat acclimation sessions」 | Kirby 2019 [F11]；Wickham 2021 [F12] | 摘要 |

判斷（推估）：
- 體溫偏移是確定的，但「熱天表現因此變差」的證據弱且不一致。**不足以讓熱懲罰依週期調整。**
- 比較有關的是另一件事：app 的熱適應模型用「連續 5 天滿劑量達到 70 %」（`heat.py:36–39`），這個速率來自以男性為主的資料，對女性可能偏快。只有一個 8 人的試驗加一篇回顧，改參數站不住，頂多在說明文字加一句。

### 2.3 心率與 HRV

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 安靜心率第 5 天最低、第 26 天最高，振幅約 2–3 bpm | WHOOP 11,590 人、45,811 個週期；內文寫 2.73 bpm，表格同一格是 2.3 bpm，兩者不一致 | Jasinski 2024 [F15] | 全文；作者是廠商員工 |
| 睡眠時脈搏：黃體中期比月經期高 3.8 bpm | 91 人、274 個週期 | Shilaih 2017 [F16] | 摘要 |
| HRV 週期內差 3–9 % | 16 篇穿戴裝置研究；「differences in time-domain HRV ranging from 3 to 9%」 | de Jager 2026 [F14] | 摘要 |
| 統合分析：濾泡期到黃體期迷走神經活性下降 | 37 篇、1,004 人；「d = -0.39, 95% CI (-0.67, -0.11)」 | Schmalenberger 2019 [F13] | 摘要 |
| 穩定跑的心率黃體期較高，但作者認為沒有臨床意義 | 23 名耐力女性，75 % 最大有氧速度跑 40 分鐘；摘要沒給幾 bpm | Barba-Moreno 2022 [F17] | 摘要 |
| 口服避孕藥使用者幾乎沒有這個起伏 | 安靜心率振幅 0.28 bpm | [F15] | 全文 |

判斷（推估）：
- 2–4 bpm 落在心率區間的寬度和日常波動之內。**心率區間、閾值心率不必依週期修正。**
- 但 app 拿「同配速心率偏高」當疲勞或熱的訊號時（EF、心率飄移），經前那幾天可能多 2–4 bpm 的雜訊。把安靜心率的差套到運動心率是推估。
- 要比較前後兩次飄移測試或閾值測試時，盡量排在週期的同一段。Schmalenberger 對研究者的建議是「control for cycle phase」[F13]，套到個人測試是推估。

### 2.4 受傷風險

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| ACL：統合分析說黃體期風險較低 | 9 篇、2,519 人；「RR, 0.72 [95% confidence interval, 0.56 to 0.89]」；方法異質 | Somerson 2019 [F19] | 摘要 |
| ACL：較早的回顧說排卵前最高 | 17 篇 | Balachandar 2017 [F20] | 摘要 |
| 只收驗證過階段的研究：沒有結論 | 「quality of evidence were low to very low」；「Practitioners should be cautious manipulating their physical preparation, injury mitigation, and screening practises based on current evidence.」 | Dos'Santos 2023 [F21] | 摘要 |
| 足球：一個世代說濾泡晚期肌肉肌腱傷害多 88 %，另一個說經前期較多 | 113 人、156 次傷害；26 人、74 次傷害 | Martin 2021 [F22]；Barlow 2024 [F23] | 摘要 |
| **跑者有證據的是月經失調，不是階段** | 183 名競技女性長距離跑者；一年月經少於 9 次者，骨應力傷害發生率是「2.25 (p = 0.02, 95% CI: 1.14-4.41) times greater」 | Hutson 2021 [F24] | 摘要 |

判斷（推估）：階段和受傷的研究互相矛盾，全是球類的急性傷害，沒有找到跑者過度使用傷害和階段的研究。**app 不該依階段標示受傷風險。**

### 2.5 荷爾蒙避孕藥

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 菁英運動員約一半在用 | 430 人；「49.5% were currently using HCs」 | Martin 2018 [F25] | 摘要 |
| 對表現的影響是 trivial；服藥期和停藥期沒有差 | 42 篇、590 人；「any group-level effect is most likely to be trivial」 | Elliott-Sale 2020 [F26] | 摘要 |
| 用避孕藥的人，月經狀態無法評估 | 「Menstrual cycle status and endogenous sex hormone levels cannot be accurately assessed in athletes who are taking sex hormone-altering medications」 | IOC 2023 共識 [F53] | 全文 |
| 停藥出血不是月經 | 「Oral contraceptives can 'mask' signs of RED-S, as a withdrawal bleed is not a period.」 | UKSI [F42] | 機構，全文 |

判斷（推估）：任何「依階段」或「看月經有沒有來」的功能，對用荷爾蒙避孕的人都不適用。app 至少要先問這一題，對她們不能顯示「正常」。休閒跑者的使用比例沒查到。

## 3. 文獻：依階段排課，還是照症狀調整

### 3.1 依階段排課的試驗

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 耐力：依週期排課沒有比較好 | 14 人、8 週；「Tailoring a polarized training program to the MC did not augment training responses」；作者承認對照組有一大部分剛好也對上了週期 | Kubica 2023 [F27]（＝[286]） | 摘要 |
| 耐力：對照組改成「故意相反」，還是沒差 | 33 人隨機、26 人完成、8 週；「no time × group interaction effect for any parameter」；階段用日曆＋基礎體溫＋排卵試紙，沒有抽血；作者說「may have been underpowered」、結論是「preliminary」 | Kubica 2024 [F28]（＝[308]） | 全文 |
| 耐力的反例：黃體期集中衝刺間歇，最大攝氧量掉 3 % | 分析 25 人；一個週期內 8 堂 6 × 30 秒全力；「group × time interaction: p = 0.027」；作者自己寫 3 % 落在實驗室誤差範圍 | Kissow 2025 [F32] | 全文 |
| 肌力：兩篇說濾泡期集中訓練較好 | 20 人（同一人兩條腿）；59 人 | Sung 2014 [F29]；Wikström-Frisén 2017 [F30] | 摘要 |
| 這兩篇被點名方法有問題 | 「stems from preliminary evidence from two papers」；用基礎體溫判排卵「not recommended」；其中一篇把吃避孕藥的人混在一起分組 | Colenso-Semple 2023 [F2] | 全文 |
| 抽血確認階段後，肌肉蛋白合成沒有階段差異 | 12 人；「no effect of cycle phase or interaction」 | Colenso-Semple 2025 [F4] | 摘要 |
| 目前最大的試驗還沒有結果 | IMPACT：預計 120 人、3 個週期、抽血確認階段；只找到計畫書 | Ekenros 2024 [F33] | 摘要 |

判斷（推估）：
- 耐力是 2 比 1，三篇都小、都短；肌力那邊「有效」的說法站不太住。說法應該是「**證據不足且不一致**」。
- Kissow 的排法（一個階段塞 8 堂全力衝刺）不是一般跑者課表會有的樣子。
- 試驗裡確認階段要用排卵試紙加基礎體溫，還被作者嫌不準。app 只靠日曆回推會更不準。有回顧直接說：假設人人 28 天、第 13 天排卵來排課，「is an arbitrary implementation of biweekly undulating periodization, not menstrual cycle "phase-based training"」[F2]。

### 3.2 症狀

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 經期症狀非常普遍 | 6,812 人（Strava）；情緒變化 90.6 %、疲勞 86.2 %、腹痛 84.2 % | Bruinvels 2021 [F35] | 摘要 |
| 症狀越多，越常缺課或改課 | 症狀指數每多 1 分：「missing or changing training (OR=1.09 (CI 1.08 to 1.10)」 | [F35] | 摘要 |
| 45 % 曾因此放棄預定的訓練；只有 18 % 排課時會考慮週期 | 1,086 名運動員 | Ekenros 2022 [F36] | 全文 |
| 只有 11 % 會和教練談 | 「81% … partly to strongly agreed that female athlete health is considered a taboo topic」 | von Rosen 2022 [F37] | 摘要 |
| 症狀 3 個以上的人，覺得受影響的機率加倍 | 195 名澳洲備戰奧運選手 | McNamara 2022 [F38] | 摘要 |
| 症狀集中在月經頭幾天 | 60 篇、6,380 人；90 % 的研究用回溯式自述 | Taim 2023 [F39] | 摘要 |
| 個人差異和同一人每個週期的差異都大 | 「a highly individualized approach should be taken」 | Santabarbara 2024 [F41] | 摘要 |
| 各研究的數字差很多 | 「Between 2.8 and 100 % of athletes reported their performance being negatively impacted … the main reason was the occurrence of menstrual cycle symptoms.」 | Oester 2024 [F40] | 摘要 |

判斷（推估）：影響訓練的是症狀，不是日曆上的階段；症狀因人、因週期而異。來源一致指向「記錄個人症狀、照當天狀況調整」。數字幾乎都是回溯式自述。

### 3.3 共識與機構怎麼說

| 說法 | 原文 | 來源 | 層級 |
|---|---|---|---|
| 先追蹤，至少三個月 | 「cycles should be tracked and monitored so the impact on an athlete can be better understood」；「monitor for at least three months」 | UKSI [F42] | 機構，全文 |
| app 只能給一般性建議 | 「Apps can be a helpful tool to record the cycle but be aware that they only provide generalised advice that is not targeted at individuals.」 | [F42] | 機構，全文 |
| 什麼時候該看醫師 | 「If periods have not started by 15 years old, if symptoms related to the menstrual cycle are severe, or if three months of periods are missed, female athletes should seek medical advice」 | [F42] | 機構，全文 |
| 停經是警訊 | 「It is never normal for an athlete to stop menstruating」；「No-one should accept that periods disappear with hard training.」 | [F42] | 機構，全文 |
| 追蹤的用途之一是發現能量不足 | 「to recognize challenges related to low energy availability (LEA) … due to their association with menstrual disturbance/dysfunction」 | 足球月經監測回顧 [F43] | 全文 |
| 依週期週期化沒有明確證據 | 「there is currently no clear scientific evidence to justify the time and resources needed to apply so-called period-periodized training strategies …, although there is also no evidence to prove these strategies are ineffective」 | [F43] | 全文 |
| 最低限度的追蹤：記月經第一天和最後一天 | 「Tracking can be achieved by denoting the first and the last day of menstruation on a calendar for each cycle.」 | Elliott-Sale 2021 [F5] | 全文；這是研究方法指引 |
| 月經恢復不等於排卵恢復 | 「restoration of menses alone is not associated with high rates of ovulation … until multiple consecutive normal length menstrual cycles are achieved」 | Triad 聯盟 2025 更新 [F44] | 摘要 |

### 3.4 現有產品

| 產品 | 做法 | 來源 | 層級 |
|---|---|---|---|
| COROS | 2025-10 起有週期追蹤：症狀紀錄、預測未來六個週期、提醒。沒有看到依階段調整訓練的功能。資料會不會進 FIT 或對外介面：查不到 | COROS Nordic 部落格 [F45] | 廠商 |
| COROS | 只有性別設成「女」才看得到 | COROS 支援文章 | 搜尋摘要 |
| Garmin | 記錄、預測，附各階段的教育內容；自己不改課表。有獨立的 Women's Health API，要另外申請 | Garmin 部落格 [F46]、開發者文件 [F47]、intervals.icu 開發者 [F48] | 廠商／官方文件 |
| intervals.icu | 手動輸入，只記錄和顯示 | [F48] | 論壇（開發者本人） |
| Wild.AI | 依階段改課表（Garmin 的合作夥伴） | [F46] | 廠商 |
| WHOOP、Oura、Apple、FitrWoman | WHOOP 依階段調整負荷與睡眠建議值；Oura 用體溫判斷階段；Apple 只記錄預測；FitrWoman 給階段提示 | 各家頁面 | 搜尋摘要 |

判斷（推估）：
- 「只記錄與提醒、不依階段改課」和業界主流一致。
- COROS 的週期資料目前沒有已知的對外管道，app 不能指望同步，要用就得讓使用者自己填。
- COROS 把功能綁在性別。app 建議做成獨立開關，性別沒填的人也能開。

## 4. 文獻：低能量可用性與 REDs

### 4.1 定義與門檻

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 定義；男女都會有 | 「a syndrome of deleterious health and performance outcomes experienced by female and male athletes exposed to low energy availability」 | IOC 2023 共識 [F53] | 全文 |
| 30 kcal 門檻的原始實驗 | 「29 regularly menstruating, habitually sedentary, young women … for 5 d」 | Loucks & Thuma 2003 [F55] | 摘要 |
| 共識自己對門檻的保留 | 「based on elegant but short-term laboratory studies … in a small sample of sedentary females」；「there are risks in setting a definitive clinical threshold of EA」；自由生活下的量測「challenged by a high level of burden」 | [F53] | 全文 |
| 男性的門檻更不清楚 | 「appears to be lower (eg, ~9 to 25 kcal/kg FFM/day)」 | [F53] | 全文 |
| Triad 聯盟也不再用單一門檻 | 「moving away from the concept of an energy-availability threshold」 | [F44] | 全文 |
| 月經狀態比算熱量準 | 「current reproductive function … appears to provide a more objective and accurate marker of optimal energy for health than the more error-prone and time-consuming dietary and training estimation of EA」 | Heikura 2018 [F66] | 摘要 |

判斷（推估）：**app 不該計算能量可用性，也不該顯示 30／45 這種切點。**

### 4.2 徵兆：哪些是自己看得到的

IOC 的評估工具（CAT2）把指標分成主要、次要、潛在三層 [F53]（全文，表 4）。大半要抽血或骨密度檢查。app 問得到的只有這些：

| 徵兆 | 共識裡的定義 | 層級 | 誰適用 |
|---|---|---|---|
| 月經停了 | 主要指標：「absence of 3–11 consecutive menstrual cycles」；12 個以上算嚴重 | 全文 | 有月經、沒用荷爾蒙避孕的人 |
| 月經變稀 | 次要指標：「>35 days between periods for a maximum of 8 periods/year」 | 全文 | 同上 |
| 15 歲還沒來 | 嚴重主要指標 | 全文 | 青少年 |
| 骨應力傷害史 | 主要指標：「≥1 high-risk (femoral neck, sacrum, pelvis) or ≥2 low-risk BSI … within the previous 2 years or absence of ≥6 months from training due to BSI」；次要指標：兩年內 1 次低風險 | 全文 | 所有人 |
| 性慾下降 | 潛在指標：「Reduced or low libido/sex drive (especially in males) and decreased morning erections」 | 全文 | 主要是男性 |
| 恢復變差、表現停滯 | 「self-reported failure to recover between training sessions」；症狀例子含「performance and training plateaus or declines」 | 全文 | 所有人；沒有特異性 |
| 鐵質長期偏低或突然下降 | 潛在指標 | 全文 | 所有人；要驗血 |

何時該就醫，兩個來源的線不同：

- 內分泌學會的功能性下視丘性無月經指引：「menstrual cycle interval persistently exceeds 45 days and/or those who present with amenorrhea for 3 months or more」[F56]（全文，經擷取工具）。
- 英國運動科學院：三個月沒來 [F42]。
- IOC：超過 35 天是次要指標 [F53]。

反覆生病也有一筆資料：有能量不足風險的人，一年因病缺練超過 22 天的機率是 3 倍（OR 3.01）[F67]（摘要）。

### 4.3 問卷

| 工具 | 內容 | 準確度與限制 | 來源 | 層級 |
|---|---|---|---|---|
| LEAF-Q（女） | 25 題，三個分項（受傷、腸胃、月經）；總分 ≥ 8 為有風險 | 開發時 84 位每週練 5 次以上的女性；敏感度 78 %、特異度 90 %；定位是搭配飲食失調篩檢一起用 | Melin 2014 [F57]；切點引自 Dasa 2023 [F59] | 摘要；切點是二手 |
| LEAF-Q 在混合項目 | 75 人，55 % 超標 | 「cannot be used to classify athletes as 'high risk' …, nor can it be used as a surrogate diagnostic tool for LEA given the low specificity」；適合用來排除（陰性預測值 76.5–100 %） | Rogers 2021 [F58] | 摘要 |
| LEAF-Q 在女足 | 60 人 | 「Except for acceptable accuracy in determining menstrual status, all other LEAF-Q components exhibited poor accuracy」 | Dasa 2023 [F59] | 摘要 |
| LEAF-Q 中文版 | — | 沒找到繁體或簡體的翻譯驗證 | — | 未找到來源 |
| LEAM-Q（男） | 題庫 42 題，最後只留性慾一個分項 | 「only low sex drive was able to distinguish between LEA cases and controls」；論文標題自稱「Attempted Validation」 | Lundy 2022 [F60] | 全文 |
| 整體回顧 | 13 份問卷、8 份做過驗證 | 「may be effective in identifying intentional energy restriction but less valuable in identifying inadvertently failure to increase energy intake」 | Sim & Burns 2021 [F61] | 摘要 |

最後一列對越野跑者特別重要：很多人不是刻意少吃，而是練多了沒有跟著多吃，問卷抓不太到這種。

共識對問卷的定位 [F53]（全文）：

- 問卷是第一步，「less sensitive and objective but inexpensive and easy to implement」。
- 「this tool should not be used in isolation nor solely for diagnosis」；「not a substitute for professional clinical diagnosis」。
- 轉介對象：「sports medicine, nutrition, psychology and sports science personnel」。
- 治療的第一步是補回能量：「restoration of optimal EA via non-pharmacological approaches」。
- 身體組成是健康資料：「must be kept confidential … requires athlete informed consent」。

未找到來源：專門談「app 給一般跑者自我篩檢」的立場；「app 顯示體重或熱量數字會不會傷害飲食失調風險者」的研究。

### 4.4 盛行率

| 族群 | 數字 | 來源 | 層級 |
|---|---|---|---|
| 女性越野跑者 276 人（平均 36 歲，每週練 7.7 小時） | LEAF-Q 陽性 55.1 %；有疲勞性骨折史 14.1 % | Hill 2026 [F63] | 全文；線上自填 |
| 18–40 歲越野跑者（男 510、女 1,445） | 女性 49.7 % 有風險；男性 22.3 %（用的不是驗證過的工具）；47.6 % 在 2.5 小時以上的賽事吃不到建議的醣量 | Henninger 2023 [F64] | 全文；摘要與全文的總人數寫法不一致 |
| 100 英里超馬 123 人 | Triad 中度風險：女 61.1 %、男 29.2 %；骨應力傷害史：女 37.5 %、男 20.5 %；骨密度偏低：女 16.7 %、男 30.1 % | Høeg 2022 [F65] | 摘要 |
| 菁英長距離 59 人 | 女性 37 % 停經；男性 40 % 睪固酮偏低 | Heikura 2018 [F66] | 摘要 |
| 愛爾蘭 833 位活躍女性 | 40 % 有風險 | Logue 2019 [F67] | 摘要 |
| 整體運動員 | 直接估算的研究 22–58 %；用替代指標的 14–63 % | Logue 2020 [F68] | 全文 |

判斷（推估）：線上自願填答會高估，但打折之後也不是罕見情況。台灣沒有找到資料。

### 4.5 和骨應力傷害的關係

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 停經或低睪固酮者骨傷約 4.5 倍 | 「bone injuries were ∼4.5-fold more prevalent in amenorrheic … and low testosterone … groups」 | Heikura 2018 [F66] | 摘要；59 人、橫斷面 |
| Triad 中度風險 2.6 倍、高風險 3.8 倍 | 239 位大學女運動員；骨傷有 64 % 來自越野跑選手 | Tenforde 2017 [F69] | 摘要 |
| 獨立預測骨傷的是月經和先前的骨傷 | 「both the oligomenorrhea/amenorrhea score (P = .0069) and the prior stress fracture/reaction score (P = .0315) were identified as independent predictors」 | [F69] | 摘要 |
| 女跑者：高風險者在鬆質骨部位的骨傷是 4.40 倍 | 前瞻、最長 5 年 | Roche 2023 [F71] | 摘要 |
| 男跑者也成立 | 156 人；風險分數每多 1 分，骨傷風險多 37 %；先前骨傷每多 1 分多 57 % | Kraus 2019 [F72] | 摘要 |
| 每週運動 12 小時以上＋骨密度偏低 | 29.7 % 發生骨傷（OR 5.1） | Barrack 2014 [F70] | 摘要；青少年與年輕成人 |

判斷（推估）：風險倍數約 2.3–4.5 倍，男女方向一致。最有用的兩個預測因子正好是 app 問得到的。哪些部位算高風險，以 IOC 表 4 的「femoral neck, sacrum, pelvis」為準 [F53]。

### 4.6 短期能量不足對訓練的影響

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 14 天：20 分鐘計時表現掉 7.8 %，回補 3 天沒回來 | 12 位耐力女性，交叉設計；22 對 52 kcal/kg FFM/day，訓練量照舊 | Caldwell 2024 [F73] | 摘要 |
| 10 天：絕對表現下降，變輕沒有換到相對表現 | 30 位有訓練的女性；25 對 50；「When the performance data were expressed relative to body mass, LEA did not enhance performance.」 | Oxfeldt 2024 [F74] | 摘要 |
| 同一批人：肌肉蛋白合成下降 | 蛋白質給到 2.2 g/kg 瘦體重仍然如此 | Oxfeldt 2023 [F75] | 摘要 |
| 一天總量差不多，但整天處在缺口的時間較長的人，指標較差 | 女 25 人、男 31 人；橫斷面 | Fahrenholtz 2018 [F76]；Torstveit 2018 [F77] | 摘要 |
| 營養介入有效，但要半年後才看得到 | 50 位 LEAF-Q 陽性的耐力女性，16 週課程 | Fahrenholtz 2023 [F78] | 摘要 |

判斷（推估）：足以支持「疑似能量不足時不要再加量」和「變輕不會變快」這兩句話，但不足以給 app 一個「該減多少」的數字。這幾篇都是同一群研究者、年輕有訓練的女性。

## 5. 文獻：鐵質與貧血

### 5.1 多常見、為什麼

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 女性運動員約 15–35 % 缺鐵，男性 5–11 % | 「~ 15-35% athlete cohorts deficient」；「approximately 5-11% of male athlete cohorts」 | Sim 2019 [F79] | 全文（作者稿） |
| 休閒馬拉松跑者 | ferritin < 15：女 28.0 %、男 1.6 % | Mettler 2010 [F88] | 摘要 |
| 台灣育齡女性 | 貧血 19.1 %；缺鐵性貧血 13.7 %（106–109 年國民營養調查） | 衛福部新聞稿 [F95] | 機構（經擷取工具） |
| 經血過多在跑者很常見，很少人就醫 | 倫敦馬拉松現場 1,073 人：36 % 自述經血過多；「Only a minority (22%) had sought medical advice.」；32 % 自述有貧血史 | Bruinvels 2016 [F80] | 摘要；全部是自述 |
| 跑完 3–24 小時 hepcidin 升高，鐵吸收變差 | 8 人；跑後 3、6、24 小時是跑前的 1.7–3.1 倍 | Peeling 2009 [F81] | 摘要 |
| 有月經的人比停經的人更容易缺鐵 | ferritin < 15：26 % 對 15 % | Petkus 2019 [F94] | 摘要 |
| 能量不足和缺鐵的直接關聯還沒有試驗 | 「studies directly linking LEA and ID are lacking」；「Perhaps, low iron stores may be an early indication of LEA」 | [F79] | 全文（作者稿） |

注意倒數第二列：月經停了反而讓鐵質數字變好看。鐵質正常不能排除能量不足。

### 5.2 切點：有三套，沒有共識

| 來源 | 切點 | 層級 |
|---|---|---|
| WHO 2020 [F82] | 健康成人 ferritin < 15 µg/L；有感染或發炎時 < 70。鐵過多的警戒值：有月經的女性 > 150、其他人 > 200 | 全文（執行摘要版） |
| 瑞士運動醫學共識 [F83] | < 15 是空的、15–30 偏低，「a cut-off of 30 mcg/l is appropriate」；高地訓練前 50 | 摘要 |
| 運動員三階段（Peeling 2007，引自 [F79]） | 第 1 階段 ferritin < 35；第 2 階段 < 20 且運鐵蛋白飽和度 < 16 %；第 3 階段 < 12 且血紅素 < 115 g/L | 二手 |

驗血的條件 [F79]（全文，作者稿）：早上、空腹、休息 24 小時以上、前 2–3 天沒有做離心為主的課、沒有生病。超馬後 ferritin 會假性升高，至少持續到賽後第 5 天 [F84]（摘要）。月經期的 ferritin 可能偏低（34.8 對 40.9）[F96]，另一篇說沒有差 [F97]；兩篇都小。

### 5.3 補了有沒有用、多久驗一次

| 說法 | 數字或原文 | 來源 | 層級 |
|---|---|---|---|
| 缺鐵但沒貧血的耐力運動員補鐵，最大攝氧量中等幅度進步 | 17 篇；「Hedges' g=0.610, 95% CI 0.399 to 0.821」 | Burden 2015 [F85] | 摘要 |
| 有效的研究都用 ferritin ≤ 20 當入選條件；整體一半有效一半沒有 | 12 篇、283 人；「The evidence is equivocal」 | Rubeor 2018 [F87] | 摘要 |
| 育齡女性補鐵後，同負荷心率低 4 bpm | 「MD: -4.05 beats per minute; 95% CI: -7.25, -0.85」；只有 3 篇低偏誤風險 | Pasricha 2014 [F86] | 摘要；一般女性，含貧血者 |
| 休閒馬拉松男性 15 % 鐵過多 | 127 人中 19 人；「supplements should only be used if tests of iron status indicate deficiency」 | Mettler 2010 [F88] | 摘要 |
| 沒症狀沒病史一年一次；有風險每季或至少半年 | 「(i) annually for athletes with no symptoms (or history of ID), or (ii) quarterly (or at least biannually) for 'at risk' individuals」 | [F79] | 全文（作者稿） |
| 分層的條件 | 半年：女性、兩年前有過缺鐵、要做高訓練量、一年內要去高地。每季：近兩年缺鐵、經血過多或停經、休息後仍疲勞、訓練做不到、表現無故下降、吃素或限制熱量、有能量不足的跡象、半年內要去高地 | [F79] 圖 1 | 全文（作者稿）；專家意見 |
| 這張圖是給專業人員用的 | 「requires the expertise of trained professionals」 | [F79] | 全文（作者稿） |
| 跑者每日吃和隔日吃，ferritin 增加一樣，隔日腸胃副作用少 | 31 名跑者、8 週 | McCormick 2020 [F90] | 摘要 |
| 早上跑完 30 分鐘內吃，吸收最好 | 16 名跑者 | McCormick 2019 [F89] | 摘要 |

「同負荷心率低 4 bpm」是補鐵前後的差，不代表「心率高 4 bpm 就是缺鐵」。用訓練資料偵測缺鐵的研究：未找到來源。

### 5.4 高海拔

- 所有鐵質建議都是為了 2–4 週的高地訓練營長出血紅素：178 名運動員、平均 21 天，不補鐵的人血紅素量只增加 1.1 %、ferritin 掉 33.2 % [F92]（摘要）。
- 回顧的範圍是「the altitudes typical of elite athlete training (~ 1600-2400 m)」，不是登山海拔 [F93]（摘要）。
- 2–5 天行程前的鐵質建議：未找到來源。
- 判斷（推估）：百岳行程來不及造血，app 不該在百岳前提醒補鐵。本來就缺鐵或貧血的人上高海拔前先確認一下是合理的，但這句沒有來源。

## 6. 隱私

| 說法 | 原文或重點 | 來源 | 層級 |
|---|---|---|---|
| GDPR：健康資料屬特種個資，原則禁止處理，例外之一是明確同意 | 第 9(1) 條「data concerning health … shall be prohibited」；第 9(2)(a) 條「explicit consent」 | [F49] | 官方條文（經擷取工具） |
| 台灣個資法第 6 條 | 「有關病歷、醫療、基因、性生活、健康檢查及犯罪前科之個人資料，不得蒐集、處理或利用。」例外含當事人書面同意 | [F50] | 官方條文 |
| 月經日期算不算第 6 條的範圍 | 沒查到主管機關的解釋 | — | 未找到來源 |
| FTC 對 Flo（2021） | 說不分享，卻把健康資料給了行銷與分析商 | [F51] | 官方新聞稿 |
| FTC 對 Premom（2023） | 經由第三方 SDK 外流；罰款並永久禁止把健康資料給第三方做廣告 | [F52] | 官方新聞稿 |

兩個案子的共同問題是資料離開使用者、流到第三方。app 是自架的，GDPR 和個資法對「個人自架、自己用」多半不適用，這裡是借原則，不是法律意見。對應到這個 app 的原則見 §8.3，全部是推估。

## 7. 落差

| 主題 | 文獻 | app 現在 | 落差 |
|---|---|---|---|
| 依階段排課 | 證據不足且不一致 | 不做 | 沒有落差。既有文件的措辭要改（§8.1） |
| 熱、心率區間、受傷風險依階段調整 | 不支持 | 不做 | 沒有落差 |
| 月經沒來＝能量不足的早期訊號 | 共識、機構文件都明講 | 沒有任何輸入 | **缺** |
| 骨應力傷害 | 兩年內 1 次高風險部位或 2 次以上是主要指標 | 傷病紀錄沒有這個類型，不會數次數 | **缺** |
| 症狀影響訓練 | 很普遍，照當天狀況調整 | 沒有「今天狀況不好」的入口 | 缺（不只女性用得到） |
| 能量不足時不該加量 | 10–14 天就影響表現 | 週量只看 CTL、TSB | 缺；但 app 沒有可靠的偵測方式，只能靠使用者自述 |
| 長課補給 | 近半數越野跑者長賽事吃不夠 | 只有 B2B 和比賽演練提醒 | 小缺 |
| 鐵質 | 女性半年、有風險每季驗一次（專家意見） | 沒有 | 缺（男女都適用） |
| 女性熱適應較慢 | 一個 8 人試驗＋一篇回顧 | 所有人同一個速率 | 證據太弱，只加說明 |
| 測試的比較條件 | 經前安靜心率高 2–4 bpm | 沒有提醒 | 小缺，加一句說明 |

## 8. 結論與建議

### 8.1 不做的事

| 不做 | 理由 | 出處 |
|---|---|---|
| 依週期階段排課或調整強度 | 證據不足且不一致；階段靠日曆回推不準 | [F1][F2][F27][F28][F32] |
| 熱懲罰依階段調整 | 表現受影響只有一個 8 人試驗 | [F7][F8][F9] |
| 心率區間、閾值心率依階段修正 | 2–4 bpm 在日常波動內 | [F15][F16][F17] |
| 依階段標示受傷風險 | 研究互相矛盾，全是球類 | [F19]–[F23] |
| 計算能量可用性、顯示 30／45 切點 | 共識自己不建議；app 沒有攝取資料 | [F53][F55] |
| 照搬 LEAF-Q 25 題並顯示分數 | 特異度低、沒有中文驗證、對休閒越野跑者沒有驗證 | [F58][F59] |
| 顯示 REDs 燈號或寫「你有 REDs」 | CAT2 是給臨床人員的，診斷由醫師做 | [F53][F54] |
| 判讀 ferritin 數值、建議補鐵劑量 | 切點有三套；15 % 的男性跑者鐵過多 | [F82][F83][F88] |
| 百岳行程前提醒補鐵 | 沒有短行程的依據 | [F92][F93] |
| 從 COROS 同步週期資料 | 查不到對外管道 | [F45] |

另外建議改一句既有文件：`periodization-cross-sport.md:560` 的「兩個試驗都沒有效果」改成「證據不足且不一致（2 篇沒差別、1 篇有差別但在誤差邊緣）」。結論「不做」不變。這次沒有動那份文件。

### 8.2 建議做的事，和每個數字的出處

| 建議 | 數字 | 出處 |
|---|---|---|
| 月經紀錄：記每次的第一天；先問有沒有用荷爾蒙避孕 | — | 最低限度的追蹤 [F5]；避孕藥者無法評估 [F53] |
| 距離上次月經超過 35 天：顯示「留意」 | 35 天 | IOC 次要指標 [F53] |
| 超過 90 天：建議就醫 | 3 個月 | IOC 主要指標 [F53]、內分泌學會 [F56]、UKSI [F42]。把「3 個週期」寫成 90 天是**推估** |
| 過去 12 個月少於 9 次：顯示「留意」 | 9 次 | Hutson 2021 的分組 [F24]；IOC 的寫法是一年最多 8 次 [F53]。兩者差一次，取哪個是**推估** |
| 要累積幾個月才開始判斷 | 3 個月 | UKSI「monitor for at least three months」[F42] |
| 傷病紀錄加「骨應力傷害」類型和部位 | 高風險部位：股骨頸、薦骨、骨盆 | IOC 表 4 [F53] |
| 兩年內 1 次高風險部位或 2 次以上：建議評估骨骼與能量狀況 | 2 年、1 次、2 次 | IOC 主要指標 [F53]；男女都適用 [F72] |
| 自我檢查的題目（不打分數） | 月經、骨應力傷害、一年因病缺練多、休息後仍累、表現無故下降、（男）性慾 | IOC 指標 [F53]；因病缺練 22 天 [F67]；性慾 [F60] |
| 命中時的文字：「變輕不會變快；先不要加量；找運動醫學科、婦產科或營養師」 | — | [F73][F74]；轉介對象 [F53] |
| 驗血提醒的間隔 | 一年；女性或高訓練量半年；有風險條件每季 | Sim 2019 圖 1 [F79]（專家意見） |
| 驗血條件的說明 | 早上空腹、休息 24 小時以上、沒生病、比賽後至少隔 5 天 | [F79]；賽後 5 天 [F84]，「至少 5 天」是**推估**（研究只追到第 5 天） |
| 測試比較的說明：前後兩次盡量排在週期的同一段 | — | [F13][F15]；套到個人測試是**推估** |

### 8.3 這類資料怎麼存（推估）

照 `engine/injuries.py` 現有的做法延伸：

1. 預設關閉，使用者自己開。不綁性別欄位。
2. 只存本機資料庫。不進分享連結、示範模式、行事曆訂閱，不推到 COROS，不送給任何第三方。
3. 不寫進 log 和錯誤訊息。
4. 可以整批刪除；關掉功能時問要不要一併刪除。
5. 只收必要的：月經第一天、是否用荷爾蒙避孕、可選的症狀。不收受孕、懷孕這類欄位。
6. 多人使用時，其他帳號看不到。
7. 備份檔會包含這些資料，說明裡要講。
8. 每個畫面固定一行「這不是醫療診斷」。

## 9. 建議開的實作單

| # | 標題 | 優先度 | 大小 | 依賴 |
|---|---|---|---|---|
| 1 | 月經紀錄與「沒來」的提醒（選用、只存本機） | P1 | M | 無 |
| 2 | 傷病紀錄加「骨應力傷害」類型與兩年內次數提醒 | P1 | S | 無 |
| 3 | 能量不足自我檢查卡（男女都有，不打分數） | P2 | M | 1、2 |
| 4 | 驗血提醒（鐵質），不判讀數值 | P2 | S | 無 |
| 5 | 「今天狀況不好」一鍵換課與可選的症狀紀錄 | P3 | M | 和 SP-82 一起看 |
| 6 | 說明文字與文件修正（測試比較條件、女性熱適應、長課補給、既有文件措辭） | P3 | S | 無 |

### 單 1：月經紀錄與「沒來」的提醒

驗收條件草案：

- 設定頁有獨立開關「月經紀錄」，預設關；不看性別欄位。
- 開啟時先問「有沒有使用荷爾蒙避孕（口服、貼片、環、植入、注射、含藥避孕器）」。回答有的人可以記錄，但不顯示任何「正常／留意」的判斷，並說明原因。
- 可以在行事曆上點一天標成「月經第一天」，可以改、可以刪。
- 紀錄未滿 3 個月時只顯示紀錄，不判斷。
- 距離上次第一天超過 35 天：課表頁出現一行「留意」。超過 90 天：出現「建議就醫」的文字，附轉介對象。過去 12 個月少於 9 次：出現「留意」。文字不出現「REDs」的判定，固定附「這不是醫療診斷」。
- 使用者可以標「懷孕、哺乳、已停經」，標了之後不再提醒。
- 隱私照 §8.3：分享快照、示範模式、行事曆訂閱、COROS 推送、log 都不含這些欄位，各有一個測試。
- 可以整批刪除。
- 不影響排課。

### 單 2：傷病紀錄加「骨應力傷害」

驗收條件草案：

- 傷病事件多一個類型「骨應力傷害（疲勞性骨折、骨應力反應）」，可選是否經醫師診斷。
- 部位多三個選項：股骨頸、薦骨、骨盆，標為高風險部位。
- 兩年內有 1 次高風險部位，或任何部位 2 次以上：傷病頁出現一段文字，建議評估骨骼健康與能量狀況，附轉介對象。男女都適用。
- 舊資料不受影響；類型沒填的事件不計入。
- 文字附出處（IOC 2023）和「這不是醫療診斷」。

### 單 3：能量不足自我檢查卡

驗收條件草案：

- 一張選用的卡片，每 3 個月最多提醒一次，可以永久關掉。
- 題目是事實題，不打分數：月經（有開單 1 的人自動帶入）、兩年內骨應力傷害（自動帶入單 2）、過去一年因病沒練的天數、休息後仍然很累、表現無故下降、（選填）性慾明顯下降。
- 任一題命中：顯示固定文字——這些情況常常一起出現、原因之一是吃得不夠；變輕不會變快；先不要加量；可以找誰。不顯示分數、燈號、熱量數字。
- 命中時課表要不要自動停止加量：見 §10 第 3 點。
- 答案只存本機，照 §8.3。

### 單 4：驗血提醒

驗收條件草案：

- 設定頁可以記「上次驗血日期」，預設關。不存檢驗數值（或只當使用者自己的備註，app 不判讀）。
- 提醒間隔：預設一年；使用者勾了「有月經」或「每週訓練量大」改半年；勾了任一風險條件（近兩年缺鐵、經血過多或停經、吃素或限制熱量、休息後仍疲勞、半年內要去 2–4 週的高地營）改每季。間隔旁邊標「專家意見（Sim 2019）」。
- 提醒文字包含驗血條件：早上空腹、休息 24 小時以上、沒生病、比賽後至少隔 5 天。
- 固定一句「先驗血再決定要不要補；不要自己補鐵」，附出處。
- 百岳行程不觸發這個提醒。

### 單 5：「今天狀況不好」一鍵換課

驗收條件草案：

- 課表當天的課有一個「今天狀況不好」的動作：強度課換成輕鬆跑或休息，輕鬆跑換成休息；不補回週量。
- 可以選填原因（經期症狀、生病、沒睡好、其他），只存本機。
- 一週內用了 3 次以上，或連續兩週都有用：出現一行提示，建議看一下恢復和吃的狀況（次數是推估）。
- 不分性別都能用。休息日偏好和拖曳調整在 SP-82，兩張單一起設計。

### 單 6：說明文字與文件修正

驗收條件草案：

- 飄移測試、閾值測試的說明加一句：有月經的人，前後兩次盡量排在週期的同一段；經前幾天安靜心率可能高 2–4 bpm。
- 熱適應說明加一句：女性可能需要較多天（一個 8 人試驗，推估）；不改模型參數。
- 長跑 ≥ 2.5 小時的課，說明帶上現有的每小時醣量文字（門檻 2.5 小時來自 Henninger 2023 的問法 [F64]，套到訓練是推估）。
- `periodization-cross-sport.md:560` 的措辭照 §8.1 修改。

## 10. 需要使用者決定的事

1. **要不要存月經日期。** 這是敏感資料。替代方案是完全不存，只在設定頁放一段固定的衛教文字（三個月沒來要就醫等）。存的好處是 app 能主動提醒；建議存，但照 §8.3。
2. **功能開關要不要獨立於性別。** 建議獨立。COROS 是綁性別的。
3. **自我檢查命中時，課表要不要自動停止加量。** 建議只提醒，另外給一個使用者自己勾的「這幾週先維持量」。自動停的話，等於 app 依一份沒有驗證過的自述做決定。
4. **男性的性慾題要不要放。** 它是男性問卷裡唯一有鑑別力的一題 [F60]，但很私人。建議放成選填、預設收合。
5. **驗血提醒要不要讓使用者記 ferritin 數值。** 記了就會有人期待 app 判讀。建議不記，或只當備註。
6. **單 5 要不要做。** 它不只和女性有關，會動到排課的互動，範圍比其他幾張大。

## 11. 限制

- 絕大多數來源只讀到摘要；標「全文」的也多半只讀了相關段落。
- IOC 2023 共識讀的是公開的 PDF 全文；CAT2 專文 [F54] 只有摘要，燈號怎麼計分沒有讀到。
- 症狀、盛行率的數字幾乎都是回溯式自述或線上自願填答。
- 短期能量不足的試驗都是年輕、有訓練的女性；男性和休閒跑者的對照試驗沒有讀到。
- 休閒越野跑者、台灣跑者的資料很少：台灣只有一般育齡女性的貧血數字，沒有跑者的月經失調或能量不足資料。
- 產品做法有一半只到搜尋摘要；COROS 的支援頁面被擋。
- 沒有查：懷孕與產後回跑、更年期、多囊性卵巢、青少年。單上沒有列，但「App 給所有人用」的原則下遲早會遇到。
- 沒有請醫療專業人員看過。§8、§9 的文字上線前建議找運動醫學科或婦產科醫師確認。

## 12. 讀不到的來源、查了沒找到

**付費牆或被擋，只讀到摘要或沒讀到**

- IOC REDs CAT2 專文（Stellingwerff 2023）[F54]：只有摘要。
- De Souza 2014 Triad 共識：沒讀到原文，累積風險評估的計分因此沒有採用。
- Peeling 2007（運動員缺鐵三階段的原始出處）：只有 Sim 2019 的轉述。
- Clénin 2015 全文、WHO 2020 完整指引（讀的是執行摘要版）。
- Giersch 2020、Janse de Jonge 2012、Wickham 2021、Barba-Moreno 2022、Somerson 2019、Barlow 2024、Martin 2018、Hutson 2021、Kubica 2023、Wikström-Frisén 2017、Kissow 2022、Bruinvels 2021、Taim 2023、Høeg 2022、Heikura 2018、Tenforde 2017、Barrack 2014、Roche 2023、Kraus 2019、Caldwell 2024、Oxfeldt 2023／2024、Melin 2014、Rogers 2021、Burden 2015、Pasricha 2014：非開放，只讀摘要。
- Lei 等 2021 對 McNulty 統合分析的評論與作者回覆：只有書目。
- 骨應力傷害的 2025 國際共識（Hoenig 等，*Br J Sports Med*，DOI 10.1136/bjsports-2024-108616）：摘要沒有列出條文。
- AIS Female Performance & Health Initiative 網頁（404）、COROS 支援文章、WHOOP、TrainingPeaks 說明頁（403）。
- CHI 2024 女性健康 app 隱私評估（DOI 10.1145/3613904.3642521）：只有搜尋摘要，沒有採用。
- 國健署 2017–2020 國民營養調查報告本體：只讀衛福部新聞稿。

**查了沒找到**

- LEAF-Q 的中文翻譯驗證。
- 台灣跑者或運動員的月經失調、能量不足盛行率（只查了英文標題）。
- 跑者過度使用傷害和週期階段的研究。
- 用訓練資料或穿戴裝置偵測缺鐵的研究。
- 2–5 天高海拔行程前的鐵質建議。
- 越野或超馬跑者的缺鐵盛行率。
- IMPACT 試驗（NCT05697263）的結果。
- 針對最大攝氧量、乳酸閾、跑步經濟性的週期階段統合分析（2021 之後）。
- 休閒跑者使用荷爾蒙避孕的比例。
- 「app 給一般跑者自我篩檢 REDs」的專家立場；app 顯示體重或熱量數字對飲食失調風險者的影響。
- COROS 週期資料有沒有進 FIT 或對外介面。

「沒找到」多半只用了一兩種查法，把握不高。

## 參考文獻

**月經週期的生理**

- [F1] McNulty KL, Elliott-Sale KJ, Dolan E, et al. The effects of menstrual cycle phase on exercise performance in eumenorrheic women: a systematic review and meta-analysis. *Sports Med* 2020. https://doi.org/10.1007/s40279-020-01319-3 （＝[278]）
- [F2] Colenso-Semple LM, D'Souza AC, Elliott-Sale KJ, Phillips SM. Current evidence shows no influence of women's menstrual cycle phase on acute strength performance or adaptations to resistance exercise training. *Front Sports Act Living* 2023. https://doi.org/10.3389/fspor.2023.1054542
- [F3] D'Souza AC, Wageh M, Williams JS, et al. Menstrual cycle hormones and oral contraceptives: a multimethod systems physiology-based review. *J Appl Physiol* 2023. https://doi.org/10.1152/japplphysiol.00346.2023
- [F4] Colenso-Semple LM, McKendry J, Lim C, et al. Menstrual cycle phase does not influence muscle protein synthesis or whole-body myofibrillar proteolysis in response to resistance exercise. *J Physiol* 2025. https://doi.org/10.1113/jp287342
- [F5] Elliott-Sale KJ, Minahan CL, de Jonge XAKJ, et al. Methodological considerations for studies in sport and exercise science with women as participants. *Sports Med* 2021. https://doi.org/10.1007/s40279-021-01435-8
- [F6] Baker FC, Siboza F, Fuller A. Temperature regulation in women: effects of the menstrual cycle. *Temperature (Austin)* 2020. https://doi.org/10.1080/23328940.2020.1735927
- [F7] Giersch GEW, Morrissey MC, Katch RK, et al. Menstrual cycle and thermoregulation during exercise in the heat: a systematic review and meta-analysis. *J Sci Med Sport* 2020. https://doi.org/10.1016/j.jsams.2020.05.014
- [F8] Janse de Jonge XA, Thompson MW, Chuter VH, Silk LN, Thom JM. Exercise performance over the menstrual cycle in temperate and hot, humid conditions. *Med Sci Sports Exerc* 2012. https://doi.org/10.1249/mss.0b013e3182656f13
- [F9] Convit L, Orellana L, Périard JD, et al. *Int J Sport Nutr Exerc Metab* 2025. https://doi.org/10.1123/ijsnem.2024-0125
- [F10] Convit L, Périard JD, Carr AJ, et al. *Temperature (Austin)* 2025. https://doi.org/10.1080/23328940.2025.2465023
- [F11] Kirby NV, Lucas SJE, Lucas RAI. Nine-, but not four-days heat acclimation improves self-paced endurance performance in females. *Front Physiol* 2019. https://doi.org/10.3389/fphys.2019.00539
- [F12] Wickham KA, Wallace PJ, Cheung SS. Sex differences in the physiological adaptations to heat acclimation: a state-of-the-art review. *Eur J Appl Physiol* 2021. https://doi.org/10.1007/s00421-020-04550-y
- [F13] Schmalenberger KM, Eisenlohr-Moul TA, Würth L, et al. A systematic review and meta-analysis of within-person changes in cardiac vagal activity across the menstrual cycle. *J Clin Med* 2019. https://doi.org/10.3390/jcm8111946
- [F14] de Jager E, Caulfield B, Angelidi E, MacNamee B, Holden S. *Sports Med* 2026. https://doi.org/10.1007/s40279-025-02388-y
- [F15] Jasinski SR, Presby DM, Grosicki GJ, Capodilupo ER, Lee VH. A novel method for quantifying fluctuations in wearable derived daily cardiovascular parameters across the menstrual cycle. *NPJ Digit Med* 2024. https://doi.org/10.1038/s41746-024-01394-0
- [F16] Shilaih M, Clerck V, Falco L, Kübler F, Leeners B. Pulse rate measurement during sleep using wearable sensors, and its correlation with the menstrual cycle phases. *Sci Rep* 2017. https://doi.org/10.1038/s41598-017-01433-9
- [F17] Barba-Moreno L, Cupeiro R, Romero-Parra N, Janse de Jonge XAK, Peinado AB. Cardiorespiratory responses to endurance exercise over the menstrual cycle and with oral contraceptive use. *J Strength Cond Res* 2022. https://doi.org/10.1519/jsc.0000000000003447
- [F18] Prado RCR, Hackney AC, Silveira R, et al. *J Womens Pelvic Health Phys Ther* 2024. https://pmc.ncbi.nlm.nih.gov/articles/PMC11042688/
- [F19] Somerson JS, Isby IJ, Hagen MS, Kweon CY, Gee AO. The menstrual cycle may affect anterior knee laxity and the rate of anterior cruciate ligament rupture. *JBJS Rev* 2019. https://doi.org/10.2106/jbjs.rvw.18.00198
- [F20] Balachandar V, Marciniak JL, Wall O, Balachandar C. *Muscles Ligaments Tendons J* 2017. https://doi.org/10.11138/mltj/2017.7.1.136
- [F21] Dos'Santos T, Stebbings GK, Morse C, et al. Effects of the menstrual cycle phase on anterior cruciate ligament neuromuscular and biomechanical injury risk surrogates in eumenorrheic and naturally menstruating women. *PLoS One* 2023. https://doi.org/10.1371/journal.pone.0280800
- [F22] Martin D, Timmins K, Cowie C, et al. Injury incidence across the menstrual cycle in international footballers. *Front Sports Act Living* 2021. https://doi.org/10.3389/fspor.2021.616999
- [F23] Barlow A, Blodgett JM, Williams S, Pedlar CR, Bruinvels G. Injury incidence, severity, and type across the menstrual cycle in female footballers. *Med Sci Sports Exerc* 2024. https://doi.org/10.1249/mss.0000000000003391
- [F24] Hutson MJ, O'Donnell E, Petherick E, Brooke-Wavell K, Blagrove RC. Incidence of bone stress injury is greater in competitive female distance runners with menstrual disturbances independent of participation in plyometric training. *J Sports Sci* 2021. https://doi.org/10.1080/02640414.2021.1945184
- [F25] Martin D, Sale C, Cooper SB, Elliott-Sale KJ. Period prevalence and perceived side effects of hormonal contraceptive use and the menstrual cycle in elite athletes. *Int J Sports Physiol Perform* 2018. https://doi.org/10.1123/ijspp.2017-0330
- [F26] Elliott-Sale KJ, McNulty KL, Ansdell P, et al. The effects of oral contraceptives on exercise performance in women: a systematic review and meta-analysis. *Sports Med* 2020. https://doi.org/10.1007/s40279-020-01317-5

**依週期排課、症狀、共識**

- [F27] Kubica C, Ketelhut S, Querciagrossa D, et al. Effects of a training intervention tailored to the menstrual cycle on endurance performance and hemodynamics. *J Sports Med Phys Fitness* 2024（線上先行 2023）. https://doi.org/10.23736/s0022-4707.23.15277-7 （＝[286]）
- [F28] Kubica C, Ketelhut S, Nigg CR. Polarized running training adapted to versus contrary to the menstrual cycle phases has similar effects on endurance performance and cardiovascular parameters. *Eur J Appl Physiol* 2024. https://doi.org/10.1007/s00421-024-05545-9 （＝[308]）
- [F29] Sung E, Han A, Hinrichs T, Vorgerd M, Manchado C, Platen P. Effects of follicular versus luteal phase-based strength training in young women. *Springerplus* 2014. https://doi.org/10.1186/2193-1801-3-668
- [F30] Wikström-Frisén L, Boraxbekk CJ, Henriksson-Larsén K. Effects on power, strength and lean body mass of menstrual/oral contraceptive cycle based resistance training. *J Sports Med Phys Fitness* 2017. https://doi.org/10.23736/s0022-4707.16.05848-5
- [F31] Kissow J, Jacobsen KJ, Gunnarsson TP, Jessen S, Hostrup M. Effects of follicular and luteal phase-based menstrual cycle resistance training on muscle strength and mass. *Sports Med* 2022. https://doi.org/10.1007/s40279-022-01679-y
- [F32] Kissow J, Jacobsen KJ, Jessen S, et al. *Mol Cell Proteomics* 2025. https://doi.org/10.1016/j.mcpro.2025.101053
- [F33] Ekenros L, von Rosen P, Norrbom J, et al. IMPACT 試驗計畫書. *Trials* 2024. https://doi.org/10.1186/s13063-024-07921-4
- [F34] Hackney AC, Hansen M, Melin A. *Scand J Med Sci Sports* 2025. https://doi.org/10.1111/sms.70107
- [F35] Bruinvels G, Goldsmith E, Blagrove R, et al. Prevalence and frequency of menstrual cycle symptoms are associated with availability to train and compete: a study of 6812 exercising women recruited using the Strava exercise app. *Br J Sports Med* 2021. https://doi.org/10.1136/bjsports-2020-102792
- [F36] Ekenros L, von Rosen P, Solli GS, et al. Perceived impact of the menstrual cycle and hormonal contraceptives on physical exercise and performance in 1,086 athletes from 57 sports. *Front Physiol* 2022. https://doi.org/10.3389/fphys.2022.954760
- [F37] von Rosen P, Ekenros L, Solli GS, et al. *Int J Environ Res Public Health* 2022. https://doi.org/10.3390/ijerph191911932
- [F38] McNamara A, Harris R, Minahan C. 'That time of the month' … for the biggest event of your career! *BMJ Open Sport Exerc Med* 2022. https://doi.org/10.1136/bmjsem-2021-001300
- [F39] Taim BC, Ó Catháin C, Renard M, Elliott-Sale KJ, Madigan S, Ní Chéilleachair N. The prevalence of menstrual cycle disorders and menstrual cycle-related symptoms in female athletes: a systematic literature review. *Sports Med* 2023. https://doi.org/10.1007/s40279-023-01871-8
- [F40] Oester C, Norris D, Scott D, Pedlar C, Bruinvels G, Lovell R. *J Sci Med Sport* 2024. https://doi.org/10.1016/j.jsams.2024.02.012
- [F41] Santabarbara KL, Helms ER, Stewart TI, Armour MJ, Harris NK. *J Sports Med Phys Fitness* 2024. https://doi.org/10.23736/s0022-4707.24.15752-0
- [F42] UK Sports Institute. Supporting the Developing Female Athlete. https://uksportsinstitute.co.uk/wp-content/uploads/2021/04/Supporting-the-developing-female-athlete-full-resource.pdf （2026-10-05 讀取）
- [F43] Mikkonen RS, Ihalainen JK, Bruinvels G, et al. Monitoring Menstrual Health in Footballers: Considerations for Tracking Menstrual and Hormonal Contraceptive Cycles in the Field to Support Performance. *Sports Med* 2026. https://doi.org/10.1007/s40279-025-02338-8
- [F44] Williams NI, De Souza MJ, Misra M, et al. 2025 Update to the Female Athlete Triad Coalition Consensus Statement Part 2. *Sports Med* 2026. https://doi.org/10.1007/s40279-025-02332-0

**產品與隱私**

- [F45] COROS Nordic. October 2025 – New Features: Flashlight, Cycle Tracking & Activity Cropping. https://corosnordic.com/blogs/coros-stories/october-2025-feature-update
- [F46] Garmin. Why Train with Your Menstrual Cycle? https://www.garmin.com/en-US/blog/health/why-train-with-your-menstrual-cycle/
- [F47] Garmin Developer Program. Women's Health API. https://developer.garmin.com/gc-developer-program/womens-health-api/
- [F48] intervals.icu 論壇. Import women health data from Garmin. https://forum.intervals.icu/t/import-women-health-data-from-garmin/59800
- [F49] GDPR 第 9 條. https://gdpr-info.eu/art-9-gdpr/
- [F50] 個人資料保護法第 6 條. https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=I0050021&flno=6
- [F51] FTC. Developer of Popular Women's Fertility-Tracking App Settles FTC Allegations (Flo Health), 2021-01-13. https://www.ftc.gov/news-events/news/press-releases/2021/01/developer-popular-womens-fertility-tracking-app-settles-ftc-allegations-it-misled-consumers-about
- [F52] FTC. Ovulation Tracking App Premom Will be Barred from Sharing Health Data for Advertising, 2023-05-17. https://www.ftc.gov/news-events/news/press-releases/2023/05/ovulation-tracking-app-premom-will-be-barred-sharing-health-data-advertising-under-proposed-ftc

**REDs**

- [F53] Mountjoy M, Ackerman KE, Bailey DM, et al. 2023 International Olympic Committee's (IOC) consensus statement on Relative Energy Deficiency in Sport (REDs). *Br J Sports Med* 2023;57:1073–1098. https://doi.org/10.1136/bjsports-2023-106994 ；公開 PDF：https://cdn.dosb.de/Relaunch_2024/Leistungssport/Gesundheitsmanagement/Sporternaehrung/IOC_Consensus_Statement_REDs_2023.pdf
- [F54] Stellingwerff T, Mountjoy M, McCluskey WT, Ackerman KE, Verhagen E, Heikura IA. IOC REDs Clinical Assessment Tool 2 (CAT2). *Br J Sports Med* 2023. https://doi.org/10.1136/bjsports-2023-106914
- [F55] Loucks AB, Thuma JR. Luteinizing hormone pulsatility is disrupted at a threshold of energy availability in regularly menstruating women. *J Clin Endocrinol Metab* 2003. https://doi.org/10.1210/jc.2002-020369
- [F56] Gordon CM, Ackerman KE, Berga SL, et al. Functional hypothalamic amenorrhea: an Endocrine Society clinical practice guideline. *J Clin Endocrinol Metab* 2017;102:1413. https://doi.org/10.1210/jc.2017-00131
- [F57] Melin A, Tornberg AB, Skouby S, et al. The LEAF questionnaire: a screening tool for the identification of female athletes at risk for the female athlete triad. *Br J Sports Med* 2014. https://doi.org/10.1136/bjsports-2013-093240
- [F58] Rogers MA, Drew MK, Appaneal R, et al. The utility of the Low Energy Availability in Females Questionnaire to detect markers consistent with low energy availability-related conditions in a mixed-sport cohort. *Int J Sport Nutr Exerc Metab* 2021. https://doi.org/10.1123/ijsnem.2020-0233
- [F59] Dasa MS, Friborg O, Kristoffersen M, et al. *Sports Med Open* 2023. https://doi.org/10.1186/s40798-023-00605-4
- [F60] Lundy B, Torstveit MK, Stenqvist TB, et al. Screening for low energy availability in male athletes: attempted validation of LEAM-Q. *Nutrients* 2022;14:1873. https://doi.org/10.3390/nu14091873
- [F61] Sim A, Burns SF. Review: questionnaires as measures for low energy availability (LEA) and relative energy deficiency in sport (RED-S) in athletes. *J Eat Disord* 2021;9:41. https://doi.org/10.1186/s40337-021-00396-7
- [F62] Mathisen TF, Ackland T, Burke LM, et al. *Br J Sports Med* 2023. https://doi.org/10.1136/bjsports-2023-106812 （身體組成測量；本文只在 §4.3 的共識引句間接用到）
- [F63] Hill C, Vigne C, Basset P, Scheer V, Baud D. *PLoS One* 2026. https://doi.org/10.1371/journal.pone.0348896
- [F64] Henninger K, Pritchett K, Brooke NK, Dambacher L. Low energy availability, disordered eating, exercise dependence, and fueling strategies in trail runners. *Int J Exerc Sci* 2023. https://doi.org/10.70252/ffdk5934
- [F65] Høeg TB, Olson EM, Skaggs K, et al. Prevalence of female and male athlete triad risk factors in ultramarathon runners. *Clin J Sport Med* 2022. https://doi.org/10.1097/jsm.0000000000000956
- [F66] Heikura IA, Uusitalo ALT, Stellingwerff T, Bergland D, Mero AA, Burke LM. Low energy availability is difficult to assess but outcomes have large impact on bone injury rates in elite distance athletes. *Int J Sport Nutr Exerc Metab* 2018. https://doi.org/10.1123/ijsnem.2017-0313
- [F67] Logue DM, Madigan SM, Heinen M, McDonnell SJ, Delahunt E, Corish CA. Screening for risk of low energy availability in athletic and recreationally active females in Ireland. *Eur J Sport Sci* 2019. https://doi.org/10.1080/17461391.2018.1526973
- [F68] Logue DM, Madigan SM, Melin A, et al. Low energy availability in athletes 2020: an updated narrative review of prevalence, risk, within-day energy balance, knowledge, and impact on sports performance. *Nutrients* 2020. https://doi.org/10.3390/nu12030835
- [F69] Tenforde AS, Carlson JL, Chang A, et al. Association of the Female Athlete Triad risk assessment stratification to the development of bone stress injuries in collegiate athletes. *Am J Sports Med* 2017. https://doi.org/10.1177/0363546516676262
- [F70] Barrack MT, Gibbs JC, De Souza MJ, et al. Higher incidence of bone stress injuries with increasing female athlete triad-related risk factors. *Am J Sports Med* 2014. https://doi.org/10.1177/0363546513520295
- [F71] Roche M, Nattiv A, Sainani K, et al. *Clin J Sport Med* 2023. https://doi.org/10.1097/jsm.0000000000001180
- [F72] Kraus E, Tenforde AS, Nattiv A, et al. Bone stress injuries in male distance runners: higher modified Female Athlete Triad Cumulative Risk Assessment scores predict increased rates of injury. *Br J Sports Med* 2019. https://doi.org/10.1136/bjsports-2018-099861
- [F73] Caldwell HG, Jeppesen JS, Lossius LO, et al. *FASEB J* 2024. https://doi.org/10.1096/fj.202401780r
- [F74] Oxfeldt M, Marsi D, Christensen PM, et al. Low energy availability followed by optimal energy availability does not benefit performance in trained females. *Med Sci Sports Exerc* 2024. https://doi.org/10.1249/mss.0000000000003370
- [F75] Oxfeldt M, Phillips SM, Andersen OE, et al. Low energy availability reduces myofibrillar and sarcoplasmic muscle protein synthesis in trained females. *J Physiol* 2023. https://doi.org/10.1113/jp284967
- [F76] Fahrenholtz IL, Sjödin A, Benardot D, et al. Within-day energy deficiency and reproductive function in female endurance athletes. *Scand J Med Sci Sports* 2018. https://doi.org/10.1111/sms.13030
- [F77] Torstveit MK, Fahrenholtz I, Stenqvist TB, Sylta Ø, Melin A. Within-day energy deficiency and metabolic perturbation in male endurance athletes. *Int J Sport Nutr Exerc Metab* 2018. https://doi.org/10.1123/ijsnem.2017-0337
- [F78] Fahrenholtz IL, Melin AK, Garthe I, et al. *Front Sports Act Living* 2023. https://doi.org/10.3389/fspor.2023.1254210

**鐵質**

- [F79] Sim M, Garvican-Lewis LA, Cox GR, Govus A, McKay AKA, Stellingwerff T, Peeling P. Iron considerations for the athlete: a narrative review. *Eur J Appl Physiol* 2019. https://doi.org/10.1007/s00421-019-04157-y ；作者稿：https://pure.bond.edu.au/ws/files/32879636/AM_Iron_considerations_for_the_athlete.pdf
- [F80] Bruinvels G, Burden R, Brown N, Richards T, Pedlar C. The prevalence and impact of heavy menstrual bleeding (menorrhagia) in elite and non-elite athletes. *PLoS One* 2016. https://doi.org/10.1371/journal.pone.0149881
- [F81] Peeling P, Dawson B, Goodman C, et al. Effects of exercise on hepcidin response and iron metabolism during recovery. *Int J Sport Nutr Exerc Metab* 2009. https://doi.org/10.1123/ijsnem.19.6.583
- [F82] WHO guideline on use of ferritin concentrations to assess iron status in individuals and populations. 2020. https://www.who.int/publications/i/item/9789240000124
- [F83] Clénin G, Cordes M, Huber A, et al. Iron deficiency in sports – definition, influence on performance and therapy. *Swiss Med Wkly* 2015. https://doi.org/10.4414/smw.2015.14196
- [F84] Kaufmann CC, et al. Effect of marathon and ultra-marathon on inflammation and iron homeostasis. *Scand J Med Sci Sports* 2021. https://doi.org/10.1111/sms.13869
- [F85] Burden RJ, Morton K, Richards T, Whyte GP, Pedlar CR. Is iron treatment beneficial in, iron-deficient but non-anaemic (IDNA) endurance athletes? A systematic review and meta-analysis. *Br J Sports Med* 2015. https://doi.org/10.1136/bjsports-2014-093624
- [F86] Pasricha SR, Low M, Thompson J, Farrell A, De-Regil LM. Iron supplementation benefits physical performance in women of reproductive age: a systematic review and meta-analysis. *J Nutr* 2014. https://doi.org/10.3945/jn.113.189589
- [F87] Rubeor A, Goojha C, Manning J, White J. Does iron supplementation improve performance in iron-deficient nonanemic athletes? *Sports Health* 2018. https://doi.org/10.1177/1941738118777488
- [F88] Mettler S, Zimmermann MB. Iron excess in recreational marathon runners. *Eur J Clin Nutr* 2010. https://doi.org/10.1038/ejcn.2010.16
- [F89] McCormick R, Moretti D, McKay AKA, et al. The impact of morning versus afternoon exercise on iron absorption in athletes. *Med Sci Sports Exerc* 2019. https://doi.org/10.1249/mss.0000000000002026
- [F90] McCormick R, Dreyer A, Dawson B, et al. *Int J Sport Nutr Exerc Metab* 2020. https://doi.org/10.1123/ijsnem.2019-0310
- [F91] Stoffel NU, Cercamondi CI, Brittenham G, et al. *Lancet Haematol* 2017. https://doi.org/10.1016/s2352-3026(17)30182-5 （隔日補鐵；對象不是運動員，本文沒有採用它的數字）
- [F92] Govus AD, Garvican-Lewis LA, Abbiss CR, Peeling P, Gore CJ. Pre-altitude serum ferritin levels and daily oral iron supplement dose mediate iron parameter and hemoglobin mass responses to altitude exposure. *PLoS One* 2015. https://doi.org/10.1371/journal.pone.0135120
- [F93] Stellingwerff T, Peeling P, Garvican-Lewis LA, et al. Nutrition and altitude: strategies to enhance adaptation, improve performance and maintain health. *Sports Med* 2019. https://doi.org/10.1007/s40279-019-01159-w
- [F94] Petkus DL, Murray-Kolb LE, Scott SP, Southmayd EA, De Souza MJ. Iron status at opposite ends of the menstrual function spectrum. *J Trace Elem Med Biol* 2019. https://doi.org/10.1016/j.jtemb.2018.10.016
- [F95] 衛生福利部.〈我國約2成育齡婦女有貧血現象 儲鐵4招 準媽咪「孕」籌帷幄〉2023-03-08. https://www.mohw.gov.tw/cp-16-73893-1.html
- [F96] Alfaro-Magallanes VM, et al. *Eur J Appl Physiol* 2022. https://doi.org/10.1007/s00421-022-05048-5
- [F97] McKay AKA, McCormick R, Pearson M, et al. Optimizing iron-deficiency screening for female athletes: do we need to consider the menstrual cycle? *Int J Sport Nutr Exerc Metab* 2026. https://doi.org/10.1123/ijsnem.2025-0257

**舊文件編號**：[278]、[286]、[308] 見 `periodization-cross-sport.md` 參考文獻。
