# 常見越野傷的負荷關聯與分級回跑（SP-204）

> 調查日期：2026-10-06。只做調查，沒有改程式。
> 標記：**已驗證（全文）**＝這次讀到原文；**已驗證（摘要）**＝讀到 Europe PMC／PubMed 的摘要；**教練級**＝臨床機構、物理治療師或教練的做法，沒有對照研究；**推估**＝我的延伸；**未找到來源**＝查了沒找到。網頁經擷取工具摘錄後閱讀，引號內是它回報的原文。
> 既有文件已查過的不重查，直接引用：`detraining.md`（停訓後恢復期）、`downhill-recovery.md`（下坡）、`impact-cadence.md`（步頻）、`baiyue-technical-terrain.md`（越野傷的部位）、`validation-lovdal.md`（護欄不是受傷預測器）、`docs/plans/injury-tracking.plan.md`（傷病紀錄的設計）。
> 這份文件不是醫療建議。app 不診斷，傷別由使用者自己選（多半是醫師或物理治療師說的）。

## 摘要

1. **app 現在不分傷別。** 部位只用在標籤、接事件和傷前分析的排序。回跑只看「停跑幾天」（Daniels 表 9.2），疼痛門檻全部套阿基里斯腱的 Silbernagel 5/10。傷病進行中，下坡課、陡坡、技術地形課照排。
2. **四種傷的負荷因子不一樣，而且方向有時相反。**
   - 膝前痛（髕股）：下坡、速度、跨大步會加重；步頻 +10 % 能降低髕股負荷 [I9][I10]、[D21]。
   - 髂脛束：下坡、週量突然變多、跑道同方向；**慢跑反而比快一點的步伐容易痛** [I7]。
   - 跟腱：最強的因子是「一年內得過」（OR 6.3）[I1]；小腿肌力不足、冷天訓練 [I2]。爬坡加重跟腱是教練的說法，**沒找到直接量測的人體研究**。
   - 足底筋膜：週跑量 > 40 km 的勝算是 6–20 km 的 6 倍 [I5]；高負荷提踵比伸展好 [I4]。
3. **疼痛門檻因傷而異，不是一律 5/10。** 跟腱的研究允許跑時 ≤ 5/10、隔天早上回到原本 [Silbernagel 2007]；膝前痛的試驗用跑時 ≤ 2/10、跑完 60 分鐘內回到原本 [I8]；一般回跑指引用「不能比開始時更痛、不能越跑越痛、不能改變跑姿」[I11]。
4. **重的傷（停跑）要先走跑交替，不是直接 50 % 跑量。** 臨床指引：先能痛也不痛地走 30 分鐘，再從「走 4 分＋跑 1 分」起步，五階段到連續跑 30 分；跑量回到傷前 50–60 % 才加速度和坡，75–80 % 才恢復正常訓練 [I11]。app 的恢復期一開始就是 50 % 的跑量，沒有坡和速度的限制順序。
5. **建議**：傷病紀錄加一個選填的「傷別」，依傷別給疼痛門檻和迴避規則（例如膝前痛、髂脛束不排下坡課；跟腱不排爬坡反覆和加速跑）；停跑的傷先走跑交替，再接現有的恢復期。共 5 張單（§5）。

## 1. 現況：app 怎麼處理傷

### 1.1 傷病紀錄

| 項目 | 現在的做法 | 程式 |
|---|---|---|
| 部位 | 9 個固定部位＋自訂：膝、小腿／脛骨、阿基里斯腱、腳踝、足底／腳跟、髖／臀、大腿、下背、其他 | `backend/engine/injuries.py:48` |
| 類型 | 過度使用、急性 | `injuries.py:53` |
| 嚴重度 | 用對訓練的影響分：輕＝照練、中＝減量或改練、重＝停跑（OSTRC 的想法，分法推估） | `injuries.py:54-55` |
| 活動上的疼痛標記 | 0 沒痛、1 痠、2 痛（影響跑姿或配速，或跑完還在痛）、3 痛到中斷 | `injuries.py:58` |
| 事件上的疼痛 | `pain_max` 0–10，選填，整筆事件一個值 | `injuries.py:197-199` |
| 疼痛監測說明 | 全部部位都顯示 Silbernagel 2007 的文字：跑時 ≤ 5/10、跑完 ≤ 5/10 且隔天早上回到原本、一週不能比一週多。程式自己註明「用在其他部位是推估」 | `injuries.py:85-93` |

- **膝蓋外側（髂脛束）和前側（髕股）沒有分開。** 計畫書當初的理由是拆太細每格只有 0–1 次，分析沒用，所以寫在備註（`docs/plans/injury-tracking.plan.md` §1.4）。
- 每天的疼痛紀錄（`injury_logs`，含休息日和隔天早上）是計畫書的 P4，**還沒做**（repo 裡搜不到 `injury_logs`）。

### 1.2 傷病怎麼影響課表

| 情況 | app 做什麼 | 程式 |
|---|---|---|
| 進行中的傷 | 週課表多一行「右膝進行中（第 5 天，中：減量或改練）」，只是資訊 | `injuries.py:501-524`、`overview.py:1366-1370` |
| 勾了「受傷期間暫停強度課」 | 不排間歇，直到事件結束 | `injuries.py:455-465`、`quality_gate.py:1268` |
| 重（停跑）且進行中 | 建議框問要不要把接下來 7 天設成不排課，按了才寫入 | `suggestions.py:153-170` |
| 停跑 ≥ 6 天 | Daniels 表 9.2 的恢復期：前半 50 %、後半 75 %（依停跑天數分級），期間不排 3 區、5 區；6–13 天的長跑 ≤ 90 分 | `reentry.py:108-170`、`overview.py:1480`、`overview.py:1551-1556` |
| 停跑和傷病重疊 | 恢復期往上一級（停 10 天照 14–28 天排；推估，預設開） | `reentry.py:105`、`reentry.py:128-131` |
| 恢復期內又標「痛」 | 建議框「先維持這週的量，不要往上加」，附 Silbernagel 的文字 | `suggestions.py:171-178` |
| 恢復期內的跑步 | 活動卡提醒「記一下有沒有痛」 | `backend/api/wko5views.py:798-802` |

**沒有的東西**：

- **沒有任何依部位的規則。** 部位只用在標籤、接事件（`injuries.attach`）、復發判斷（`injuries.recurrence`），以及傷前分析的排序（§1.3）。
- **輕、中的傷不會產生恢復期。** 照練或減量不會停跑 ≥ 6 天，所以只有週註記。業餘跑者大多數的過度使用傷屬於這兩級。
- **下坡、陡坡、技術地形課只看恢復期和恢復週，不看傷病。** 三個模組都只在 `mode` 是 `reentry`／`recovery_week` 時不排（`downhill.py:82`、`steep_hill.py:132`、`technical.py:75`）。膝蓋痛、還在照練的時候，下坡離心課照排。
- **恢復期沒有地形順序。** 只限制 3 區和 5 區，回到 75 % 之後就沒有其他限制。
- **生病已經有「依類型」的規則**（SP-117：輕微感冒只排 1 區、發燒停跑，`injuries.py:468-498`、`overview.py:2142-2200`）。傷可以照這個樣子做。
- `detraining.md` §6.2 已經寫明「傷別規則未查」、「受傷：app 不自動排恢復計畫」（L325、L401）。本文就是補這一塊。

### 1.3 傷前分析的部位指標

`injury_exposure.RELEVANT`（`backend/engine/injury_exposure.py:91-100`）決定每個部位先列哪些指標，**全部是推估**：

| 部位 | 先列的指標 |
|---|---|
| 膝 | 下降、陡下坡時間、衝擊量、步頻差 |
| 阿基里斯腱 | 爬升、越野佔比、低強度佔比、步頻差 |
| 小腿／脛骨 | 同阿基里斯腱 |
| 足底 | 跑步距離、週增量、延遲一週的週增量、2 週平均增幅 |
| 髖、下背 | 長跑次數、負重天、連續長天 |

這只影響顯示順序，不影響課表。§2 會對照文獻看這張表哪裡有支持。

### 1.4 通用的負荷護欄

`load_guard.py` 不看傷病：CTL 週增量（相對線）、跑步週增量 > 20 % 擋、單次 > 30 天內最長 110 %（Frandsen 2025）。這些是「別一次加太多」的保守規則，不是受傷預測器（`validation-lovdal.md` §0）。

## 2. 文獻

### 2.1 越野跑最常見的傷

| 說法 | 來源 | 標記 |
|---|---|---|
| 越野跑受傷率 0.7–61.2 次／1,000 小時；下肢最多，「specifically involving blisters of the foot/toe」。風險因子是經驗多、等級高、沒熱身、「no specialised running plan」、在柏油路練、一天練兩次等；**沒有列出下坡量、爬升量** | Viljoen 2022 活的系統性回顧 [I12] | 已驗證（摘要） |
| 越野跑傷害膝最多，其次是腳踝和阿基里斯腱 | Jiang 2024（`baiyue-technical-terrain.md` §1） | 既有文件已驗證（摘要） |
| 髂脛束症候群是跑者第二常見的傷，膝外側痛最常見的原因；發生率 5–14 % | Aderem 2015 [I15]；van der Worp 2012 [I14] | 已驗證（摘要） |
| 髂脛束約佔跑步傷害的 10 % | Sanchez-Alvarado 2024 [I16] | 已驗證（摘要） |

讀完的判斷：越野跑的流行病學研究**沒有把傷別和下坡、爬升量連起來**。部位和負荷的對應只能從各傷自己的研究和生物力學推，大多是推估。

### 2.2 各傷的負荷因子

| 傷 | 跟負荷有關的因子 | 來源 | 標記 |
|---|---|---|---|
| **膝前痛（髕股）** | 下坡增加髕股關節的累積負荷，減少脛骨和跟腱的；步頻增加，所有部位的負荷都下降 | Van Hooren 2024（`downhill-recovery.md` [D21]） | 既有文件已驗證（摘要） |
| | 步頻 +10 %：髕股峰值力 −14 %；步幅 −10 %：每英里累積 −7.5 % | Lenhart 2014、Willson 2014（`impact-cadence.md`） | 既有文件已驗證 |
| | 系統性回顧：股四頭肌無力是風險因子（軍人）；年齡、身高、BMI、Q 角不是；摘要沒有提訓練量 | Neal 2019 [I13] | 已驗證（摘要） |
| | 單次步頻 +10 % 的步態再訓練，4 週和 3 個月時疼痛、功能、骨盆下掉都改善（12 人，無對照組） | Bramah 2019 [I10] | 已驗證（摘要） |
| **髂脛束** | 「excessive running in the same direction on a track, greater-than-normal weekly mileage and downhill running」；臀外側肌無力 | Fredericson & Wolf 2005 [I7]（敘述性回顧） | 已驗證（摘要） |
| | 「faster-paced running is less likely to aggravate ITBS and faster strides are initially recommended over a slower jogging pace」；多數人 6 週內好 | 同上 | 已驗證（摘要） |
| | 病因的證據「limited or conflicting」；不清楚髖外展肌無力是不是主因 | van der Worp 2012 [I14] | 已驗證（摘要） |
| | 治療最常見的是髖外展肌強化 | Sanchez-Alvarado 2024 [I16] | 已驗證（摘要） |
| **跟腱** | 休閒跑者 1/20 會得；最強的因子是「AT in the previous 12 months」OR 6.3；照課表練 OR 1.8 | Lagas 2020 [I1]（前瞻世代） | 已驗證（摘要） |
| | 系統性回顧：以前的下肢肌腱病或骨折、冷天訓練、蹠屈肌力下降、某些步態；過重、足弓、活動量**沒有**關係 | van der Vlist 2019 [I2] | 已驗證（摘要） |
| | 上坡跑步頻高、改用中足或前足著地、肌肉做功多；下坡改用後腳跟著地、以吸收能量為主 | Vernillo 2017 [I17] | 已驗證（摘要；摘要沒有直接講跟腱負荷） |
| | 「上坡讓跟腱負荷變大」的人體量測 | — | **未找到來源**（搜到的是大鼠研究和下坡垂直力，都沒有回答） |
| **足底筋膜** | 一年前瞻世代：週跑量 > 40 km 的勝算是 6–20 km 的 6 倍；站立期踝外翻較大 | 4HAIE 2025 [I5] | 已驗證（摘要） |
| | 高負荷提踵（腳趾下墊毛巾，隔天做）3 個月時比每天伸展好，12 個月沒差 | Rathleff 2015 [I4]（RCT，48 人） | 已驗證（摘要） |
| | JOSPT 臨床指引 2023 版 | Koc 2023 [I6] | 只讀到摘要，內容沒有（付費牆，§8） |

讀完的判斷：

- **膝前痛和髂脛束**：下坡有直接支持（膝前痛是生物力學，髂脛束是回顧文章的訓練因子）。`RELEVANT` 把下降、陡下坡放在膝的第一位，方向對。
- **步頻**：膝前痛有 RCT 和小型前後研究支持（[I8] 的步態組、[I10]）；髂脛束只有「快一點的步伐」這種說法。`RELEVANT` 把步頻差放在膝和跟腱，跟腱這邊沒找到來源。
- **跟腱**：文獻最清楚的是「以前得過」和「小腿肌力」，不是爬升量。`RELEVANT` 把爬升放第一位是推估，沒有找到人體研究支持；可以留著當排序，但不能寫成「爬坡造成跟腱傷」。
- **足底筋膜**：週跑量有世代研究支持，`RELEVANT` 的方向對。

### 2.3 疼痛監測：每種傷的門檻

| 傷 | 跑的時候 | 跑完／隔天 | 來源 | 標記 |
|---|---|---|---|---|
| 跟腱 | ≤ 5/10 | 跑完 ≤ 5/10，隔天早上要退回原本；疼痛和僵硬不能一週比一週多 | Silbernagel 2007（`injuries.py:85-89` 已逐字引用 Methods） | 既有程式已驗證（全文）；RCT 38 人：照這個規則繼續跑，和停跑 6 週的結果一樣 |
| 跟腱（另一個試驗的用法） | 訓練中 ≤ 4/10 | 之後 24 小時 ≤ 4/10；日常活動疼痛 < 2/10 才開始跑 | Griffin 2021 試驗計畫書 [I3] | 已驗證（全文） |
| 膝前痛 | ≤ 2/10 | 「pain should return to before-training levels within 60 min post training」 | Esculier 2016 試驗計畫書 [I8] | 已驗證（全文） |
| 膝前痛（原始模型） | 疼痛監測系統，細節摘要沒寫 | — | Thomeé 1997 [I18] | 已驗證（摘要）；數字沒讀到 |
| 不分傷別 | 不能尖銳痛、不能越跑越痛、不能痛到改變跑姿；完成 6 趟沒有更痛或腫才進下一階 | — | Ohio State Wexner 回跑指引 [I11] | 已驗證（全文），教練級（臨床機構） |
| 不分傷別 | 跑時和之後 48 小時都不痛的距離當基準，再扣 10–20 % | — | Tom Goom（物理治療師）[I19] | 已驗證（全文），教練級 |
| 髂脛束、足底筋膜 | — | — | — | **未找到來源**（沒有找到這兩種傷專用的數字門檻） |

讀完的判斷：

- **肌腱可以容忍比關節多的痛。** 跟腱的研究允許 4–5/10，膝前痛用 2/10，一般指引用「不能更痛」。app 把跟腱的 5/10 套到膝蓋，對膝前痛太寬。
- **「隔天」是共同的檢查點。** 跟腱看隔天早上，膝前痛看 60 分鐘，一般指引看 24–48 小時。app 只有跑步當下的標記（0–3），沒有隔天的紀錄。
- **app 的 0–3 標記和 0–10 門檻對不起來。** 「痛（2）」的定義是「影響跑姿或配速，或跑完還在痛」，已經超過一般指引的「不能改變跑姿」，大約對應 ≥ 4–5/10（推估）。所以用現在的標記沒辦法判斷 2/10 這種門檻。

### 2.4 回跑的流程

| 項目 | 內容 | 來源 | 標記 |
|---|---|---|---|
| 開始跑的前提 | 走 30 分鐘不痛、步態正常；能承受 200–250 次落地（約 1/3 英里）；跳躍練習不更痛不腫 | Ohio State Wexner [I11] | 已驗證（全文），教練級 |
| 走跑階段 | 走 4／跑 1 → 走 3／跑 2 → 走 2／跑 3 → 走 1／跑 4，每次 3–6 趟、每階 2–3 天；最後連續跑 30 分 × 3 天；跑步日之間至少休 1 天；用「舒服」的強度 | 同上 | 已驗證（全文），教練級 |
| 之後加量 | 第 5 階之後每週 +10–30 %；回到傷前週量 50–60 % 才「gradually increase speed and introduce hills」；75–80 % 才回到正常訓練 | 同上 | 已驗證（全文），教練級 |
| 膝前痛的調整 | 「run more often, but to decrease daily distance and running speed as well as to avoid downhill running」；距離先回到原本，最後才加速度和坡 | Esculier 2016 [I8] | 已驗證（全文） |
| 膝前痛：衛教就夠 | 三組（只有衛教、衛教＋運動、衛教＋步態再訓練）改善一樣；「education on symptoms and management of training loads should be included as a primary component」 | Esculier 2018 RCT [I9]（69 人） | 已驗證（摘要） |
| 跟腱：回到運動 | 建議先做至少 3 個月運動治療；恢復可能要一年，回到運動的階段特別容易復發 | Silbernagel 2015 [I20] | 已驗證（摘要）；階段內容在全文，沒讀 |
| 一次只改一件事 | 「Change 1 thing at a time」；跑步日之間休一天 | Tom Goom [I19] | 已驗證（全文），教練級 |

讀完的判斷：

- **app 的恢復期和臨床回跑是兩件事。** Daniels 表 9.2 處理的是體能流失（停練多久、回來跑多少），臨床回跑處理的是組織能承受多少（先走、再走跑、坡和速度最後）。停跑的傷兩件事都要。
- **坡和速度的順序在兩個來源裡一致**：先回到距離，再加速度和坡 [I8][I11]。app 的恢復期只擋 3 區和 5 區，沒有擋坡。
- **傷停往上一級**（`reentry.STEP_UP_MIN`）的方向和臨床流程一致（傷後比單純停練要更慢），但「上一級」這個量仍是推估，沒有來源。

## 3. 落差

| # | 文獻或指引 | app 現在 | 落差 |
|---|---|---|---|
| 1 | 每種傷的負荷因子不同，甚至相反（下坡對膝前痛、髂脛束不好；跟腱的下坡負荷反而較低 [D21]） | 不分傷別；膝外側和前側合在「膝」 | **缺**：沒有傷別欄位 |
| 2 | 膝前痛 ≤ 2/10、跟腱 ≤ 5/10、一般「不能更痛」 | 全部顯示 Silbernagel 5/10 | **不一致**：膝前痛太寬 |
| 3 | 隔天早上（跟腱）、60 分鐘（膝前痛）、24–48 小時（一般） | 只有跑步當下的 0–3 標記；`injury_logs` 沒做 | **缺**：沒辦法判斷「隔天回到原本」 |
| 4 | 膝前痛、髂脛束：避開下坡；坡和速度最後才加 | 傷病進行中照排下坡課、陡坡、技術地形（`downhill.py:82` 等只看恢復期） | **缺** |
| 5 | 停跑的傷先走 30 分不痛，再走跑交替 5 階段 | 恢復期第一天就是傷前 50 % 的連續跑 | **缺**：重（停跑）的傷少了走跑階段 |
| 6 | 回到傷前 50–60 % 才加坡和速度 | 恢復期只擋 3 區、5 區；長爬坡、越野長跑沒限制 | **部分**：3 區、5 區的部分一致 |
| 7 | 照練或減量（輕、中）也要依傷調整訓練（Silbernagel：照跑但照規則；Esculier：多次、短、慢、不下坡） | 輕、中只有週註記 | **缺** |
| 8 | 跟腱的主要因子是以前得過、小腿肌力 | `RELEVANT` 把爬升排第一 | 小落差：排序是推估，不影響課表 |

## 4. 結論與建議

### 4.1 結論

- **可以依傷別給不同的建議，而且大部分有來源。** 但要使用者自己選傷別（app 不診斷），文字一律寫「如果醫師或物理治療師說是 X」。
- **最有把握的兩條**：膝前痛和髂脛束避開下坡（[I7][I8]、[D21]）；疼痛門檻依傷別（§2.3）。
- **最沒把握的一條**：跟腱少排爬坡。教練常這樣說、機制合理（上坡前足著地、小腿做功多，[I17]），但沒找到量測研究。做的話要標推估。

### 4.2 傷別設定表（草案）

傷別是選填的。不選就照現在的做法（不分傷別），只是疼痛門檻改用一般指引的「不能更痛」，不再套跟腱的 5/10。

| 傷別（顯示名稱） | 對應部位 | 進行中與恢復期**不排**的課 | 會調整的 | 疼痛門檻（顯示文字） | 來源 |
|---|---|---|---|---|---|
| 跟腱 | 阿基里斯腱 | 爬坡反覆、陡坡健走、加速跑（strides）、5 區 | 長跑改平路（推估） | 跑時 ≤ 5/10；跑完 ≤ 5/10 且隔天早上回到原本；不能一週比一週痛 | Silbernagel 2007；迴避規則推估 |
| 足底筋膜 | 足底／腳跟 | 不另外迴避課型 | 週量不往上加，直到隔天早上第一步的痛不再變多（推估） | 跑時不比開始時更痛、不越跑越痛、不改變跑姿；隔天不更痛 | 一般指引 [I11]；週量 [I5] |
| 髂脛束 | 膝（外側） | 下坡離心課、技術地形的下坡段；越野長跑改平路 | 不排很慢的恢復跑：輕鬆跑的配速別刻意壓慢（推估，依 [I7]） | 同一般指引 | Fredericson 2005 [I7] |
| 膝前痛 | 膝（前側） | 下坡離心課、技術地形、陡坡健走的下坡段；越野長跑改平路 | 次數多、每次短、配速慢（週量先不變）；提示步頻 +5–10 %（推估） | 跑時 ≤ 2/10；跑完 60 分鐘內回到原本 | Esculier 2016、2018 [I8][I9]；步頻 [I10] |
| 其他／不選 | 任何部位 | 不另外迴避 | — | 同一般指引 | [I11] |

- 「不排」的範圍：事件進行中（不分輕、中、重），加上和它重疊的恢復期。事件結束後就不再限制（推估：文獻是「回到 50–60 % 才加坡」，app 用事件結束當替代點）。
- 小腿／脛骨的痛可能是骨應力傷害，**app 不給傷別規則**，只顯示「先給醫師看」。骨應力傷害的類型是 SP-192 的建議單 2（使用者已決定不開），這裡不重複。

### 4.3 停跑的傷：走跑階段接在恢復期前面

- 條件：事件是重（停跑），而且停跑 ≥ 6 天（會產生恢復期）。
- 第一次回來先排「走 30 分」的確認課；接著照 [I11] 的 4 階走跑，每階 2–3 次、跑步日之間休 1 天；最後連續跑 30 分。
- 走跑階段結束後才接 Daniels 的恢復期（50 % → 75 %），恢復期的長度照現在算。
- 進下一階的條件是使用者在活動上標「沒痛」或「痠」（0–1）。標「痛」就重複這一階（[I11]：「Do not progress phases if …」）。
- 這些都是建議課表，使用者可以改；app 不判斷能不能開始跑，只在說明裡寫 [I11] 的前提。

### 4.4 輕、中的傷：建議，不自動改

- 輕（照練）：只套 §4.2 的「不排」課型和疼痛門檻文字。週量照排。
- 中（減量或改練）：加一個建議框「這週維持上週的量、不往上加」（推估；比照 `suggestions.injury_hold` 的做法），使用者按了才套。

### 4.5 和既有規則的關係

- 照 SP-117 生病的模式做：`injuries.py` 加一個 `condition_rule(events, day)`，回傳當天要迴避的課型和顯示文字；週課表、下坡、陡坡、技術地形模組都問它，跟 `illness_rule` 一樣。
- 不改 `load_guard`：通用護欄照舊，傷別規則只會讓課表更保守，不會放寬。
- `injury_exposure.RELEVANT`：膝可以依傷別拆成前側（下降、陡下坡、步頻差、衝擊量）和外側（下降、週增量、長跑次數）；跟腱的爬升標推估。只影響排序。

## 5. 建議開的單

### 單 1：傷病紀錄加「傷別」與每種傷的疼痛門檻（P1）

- 範圍：事件多一個選填欄位 `condition`（跟腱／足底筋膜／髂脛束／膝前痛／其他），各自對應部位；疼痛監測文字依傷別換（§4.2 最後一欄）；不選時用一般指引的文字，不再預設 Silbernagel。
- 驗收條件草案：
  - 舊事件沒有傷別，照常顯示，文字是一般指引。
  - 選了膝前痛，建議框和活動卡寫「跑時 ≤ 2/10、跑完 60 分鐘內回到原本」，附來源。
  - 選了跟腱，文字和現在一樣（Silbernagel）。
  - 傷別和部位不一致時（例如部位是腳踝、傷別選膝前痛）回錯誤碼。
  - 所有文字保留「這不是醫療診斷」。
- 要你決定：傷別清單要不要只放這四種，還是加脛前疼痛、腿後肌等。

### 單 2：依傷別迴避課型（P1）

- 範圍：`injuries.condition_rule`；下坡離心課、陡坡健走、技術地形、爬坡反覆、加速跑在事件進行中和重疊的恢復期依 §4.2 不排；越野長跑改平路並在週註記寫原因。
- 驗收條件草案：
  - 膝前痛或髂脛束進行中的那幾週，不排下坡課（`downhill.py`）和技術地形（`technical.py`），週註記寫「膝前痛進行中：先不排下坡課（Esculier 2016）」。
  - 跟腱進行中不排爬坡反覆、陡坡健走、加速跑；這條標推估。
  - 不選傷別時，課表和現在完全一樣（測試鎖住）。
  - 事件結束的那一週起恢復原本的排法。
  - 被取消的課，時間交給其他輕鬆跑，週量不變。
- 要你決定：「越野長跑改平路」要不要做（對要比越野賽的人會少很多地形練習）；事件結束就解除，還是要等到恢復期結束。

### 單 3：停跑的傷先走跑交替，再接恢復期（P2）

- 範圍：重（停跑）且停跑 ≥ 6 天的傷，恢復期前面加「走 30 分確認」和 4 階走跑（§4.3）；以活動上的疼痛標記決定是否進下一階。
- 驗收條件草案：
  - 停跑 10 天的膝傷：第一次回來排「走 30 分」，接著每階 2–3 次走跑，跑步日之間有休息日，最後連續跑 30 分 3 次，然後才是 50 %／75 % 的恢復期。
  - 某一次標「痛」，下一次重複同一階，不前進。
  - 生病停跑不套走跑階段（照 SP-117）。
  - 推到 COROS 的走跑課是走、跑交替的步驟（COROS 步驟格式要確認）。
- 要你決定：走跑階段要不要算進恢復期的天數（會讓回到 100 % 變更晚）；使用者沒有標記疼痛時，預設當成「沒痛」照進度走，還是停在原階。

### 單 4：中度傷「本週不加量」的建議框（P3）

- 範圍：中（減量或改練）的事件進行中，建議框「這週維持上週的量」，按了才套用；比照 `injury_hold`。
- 驗收條件草案：沒按之前課表不變；按了之後本週分鐘數 ≤ 上週實際；事件結束後不再出現。
- 要你決定：要不要直接自動套用（降量是安全方向，`plan-auto.spec.md` 的降量本來就自動套）。

### 單 5：每日疼痛紀錄，支援「隔天早上」的檢查（P3）

- 範圍：計畫書 P4 的 `injury_logs`（事件 id、日期、0–10、備註），傷病進行中與恢復期內每天可記一筆；疼痛門檻的檢查改用它（隔天比前一天多 → 提示退一步）。
- 驗收條件草案：休息日也能記；跟腱事件隔天早上比前一天高 ≥ 2 分時，建議框提示「照 Silbernagel：下次減量」；沒有紀錄時不提示。
- 要你決定：值不值得做。你只有跑步時戴錶、平常不記錄，這張單需要每天手動輸入一個數字。

## 6. 要你決定的事

1. 傷別清單：只放跟腱、足底筋膜、髂脛束、膝前痛（建議），還是再加其他。
2. 不選傷別時，疼痛文字要不要從 Silbernagel 5/10 改成一般指引的「不能更痛」（建議改：5/10 只研究過跟腱）。
3. 迴避的期間：事件結束就解除（建議，簡單），還是等恢復期結束。
4. 傷別迴避裡「越野長跑改平路」要不要做。
5. 停跑的傷要不要加走跑階段（單 3），以及要不要算進恢復期天數。
6. 中度傷的「不加量」要建議（建議）還是自動套。
7. 單 5 要不要做（需要每天手動輸入）。

## 7. 限制

- 越野跑的流行病學研究沒有傷別和下坡、爬升量的關係，§2.2 的對應大多來自路跑、生物力學或敘述性回顧。
- 髂脛束的主要來源（Fredericson 2005）是敘述性回顧，病因的系統性回顧說證據有限或矛盾 [I14]。
- 「跟腱少排爬坡」沒有找到人體量測研究，迴避規則標推估。
- 足底筋膜和髂脛束沒有找到專用的疼痛數字門檻，用一般指引。
- 回跑的走跑階段來自一家醫學中心的臨床指引 [I11]，不是對照試驗；階段時間、每階次數沒有研究比較。
- JOSPT 的三份臨床指引（跟腱 2018、足底筋膜 2023、膝前痛 2019）只讀到摘要，內文沒讀（§8）。
- 沒有查脛骨內側壓力症候群、骨應力傷害、腿後肌、腳踝扭傷的回跑。

## 8. 讀不到的來源

| 來源 | 為什麼讀不到 | 會影響什麼 |
|---|---|---|
| Martin RL 等，Achilles Pain, Stiffness, and Muscle Power Deficits: Midportion Achilles Tendinopathy Revision 2018，*JOSPT*，doi:10.2519/jospt.2018.0302 | jospt.org 回 403；Europe PMC 只有一段目的說明 | 跟腱的風險因子分級、疼痛監測的建議等級 |
| Koc TA 等，Heel Pain – Plantar Fasciitis: Revision 2023，*JOSPT*，doi:10.2519/jospt.2023.0303 | 只有目的說明的摘要 | 足底筋膜的風險因子、負荷建議、疼痛門檻 |
| Willy RW 等，Patellofemoral Pain，*JOSPT* 2019，doi:10.2519/jospt.2019.0302 | 只有定義的摘要 | 膝前痛的步態再訓練、活動調整建議等級 |
| Silbernagel KG, Crossley KM, A Proposed Return-to-Sport Program for Patients With Midportion Achilles Tendinopathy, *JOSPT* 2015, doi:10.2519/jospt.2015.5885 | Europe PMC 列出免費 PDF（jospt.org），這次沒讀 | 跟腱回跑的階段與進階條件（單 3 若要分傷別會用到） |
| Thomeé R 1997, *Phys Ther*, doi:10.1093/ptj/77.12.1690 | 摘要沒有疼痛監測的數字 | 膝前痛原始疼痛監測模型的門檻 |
| Esculier 2018 RCT 全文，*BJSM*，doi:10.1136/bjsports-2016-096988 | 付費 | 實際執行時的疼痛規則（這次用的是試驗計畫書 [I8]） |

## 參考

- [I1] Lagas IF, et al. Incidence of Achilles tendinopathy and associated risk factors in recreational runners: a large prospective cohort study. *J Sci Med Sport* 2020. doi:10.1016/j.jsams.2019.12.013. PMID 31892510.
- [I2] van der Vlist AC, et al. Clinical risk factors for Achilles tendinopathy: a systematic review. *Br J Sports Med* 2019. doi:10.1136/bjsports-2018-099991. PMID 30718234.
- [I3] Griffin C, Daniels K, Hill C, Franklyn-Miller A, Morin JB. A criteria-based rehabilitation program for chronic mid-portion Achilles tendinopathy: study protocol for a randomised controlled trial. *BMC Musculoskelet Disord* 2021. doi:10.1186/s12891-021-04553-6. https://pmc.ncbi.nlm.nih.gov/articles/PMC8364697/
- [I4] Rathleff MS, et al. High-load strength training improves outcome in patients with plantar fasciitis: a randomized controlled trial with 12-month follow-up. *Scand J Med Sci Sports* 2015. doi:10.1111/sms.12313. PMID 25145882.
- [I5] Running distance and biomechanical risk factors for plantar fasciitis: a 1-yr prospective 4HAIE cohort study. *Med Sci Sports Exerc* 2025. doi:10.1249/mss.0000000000003617. PMID 39629715.
- [I6] Koc TA, et al. Heel Pain – Plantar Fasciitis: Revision 2023. *J Orthop Sports Phys Ther* 2023. doi:10.2519/jospt.2023.0303. PMID 38037331.
- [I7] Fredericson M, Wolf C. Iliotibial band syndrome in runners: innovations in treatment. *Sports Med* 2005. doi:10.2165/00007256-200535050-00006. PMID 15896092.
- [I8] Esculier JF, Bouyer LJ, Dubois B, Frémont P, Moore L, Roy JS. Effects of rehabilitation approaches for runners with patellofemoral pain: protocol of a randomised clinical trial addressing specific underlying mechanisms. *BMC Musculoskelet Disord* 2016. doi:10.1186/s12891-015-0859-9. PMC4702381.
- [I9] Esculier JF, et al. Is combining gait retraining or an exercise programme with education better than education alone in treating runners with patellofemoral pain? A randomised clinical trial. *Br J Sports Med* 2018;52:659–666. doi:10.1136/bjsports-2016-096988. PMID 28476901.
- [I10] Bramah C, et al. A 10% increase in step rate improves running kinematics and clinical outcomes in runners with patellofemoral pain at 4 weeks and 3 months. *Am J Sports Med* 2019. doi:10.1177/0363546519879693. PMID 31657964.
- [I11] The Ohio State University Wexner Medical Center Sports Medicine. Basic Return to Running Guideline. https://wexnermedical.osu.edu/-/media/files/wexnermedical/patient-care/healthcare-services/sports-medicine/education/medical-professionals/other/basic-return-to-running-guideline.pdf （2026-10-06 讀取）
- [I12] Viljoen C, et al. Trail running injury risk factors: a living systematic review. *Br J Sports Med* 2022;56(10). doi:10.1136/bjsports-2021-104858. PMID 35022162.
- [I13] Neal BS, et al. Risk factors for patellofemoral pain: a systematic review and meta-analysis. *Br J Sports Med* 2019. doi:10.1136/bjsports-2017-098890. PMID 30242107.
- [I14] van der Worp MP, et al. Iliotibial band syndrome in runners: a systematic review. *Sports Med* 2012. doi:10.2165/11635400-000000000-00000. PMID 22994651.
- [I15] Aderem J, Louw QA. Biomechanical risk factors associated with iliotibial band syndrome in runners: a systematic review. *BMC Musculoskelet Disord* 2015. doi:10.1186/s12891-015-0808-7. PMID 26573859.
- [I16] Sanchez-Alvarado A, Bokil C, Cassel M, Engel T. Effects of conservative treatment strategies for iliotibial band syndrome on pain and function in runners: a systematic review. *Front Sports Act Living* 2024. doi:10.3389/fspor.2024.1386456. PMID 39247485.
- [I17] Vernillo G, et al. Biomechanics and physiology of uphill and downhill running. *Sports Med* 2017;47:615–629. doi:10.1007/s40279-016-0605-y. PMID 27501719.
- [I18] Thomeé R. A comprehensive treatment approach for patellofemoral pain syndrome in young women. *Phys Ther* 1997. doi:10.1093/ptj/77.12.1690. PMID 9413448.
- [I19] Tom Goom（物理治療師）. Returning to running after injury. https://www.running-physio.com/returnafterinjury/ （2026-10-06 讀取）
- [I20] Silbernagel KG, Crossley KM. A proposed return-to-sport program for patients with midportion Achilles tendinopathy: rationale and implementation. *J Orthop Sports Phys Ther* 2015. doi:10.2519/jospt.2015.5885. PMID 26390272.
- Silbernagel KG, Thomeé R, Eriksson BI, Karlsson J. Continued sports activity, using a pain-monitoring model, during rehabilitation in patients with Achilles tendinopathy: a randomized controlled study. *Am J Sports Med* 2007;35:897–906. doi:10.1177/0363546506298279. PMID 17307888.
- 既有文件：`detraining.md` §6.2、`downhill-recovery.md` [D21]、`impact-cadence.md`、`baiyue-technical-terrain.md` §1、`validation-lovdal.md`、`docs/plans/injury-tracking.plan.md`。
