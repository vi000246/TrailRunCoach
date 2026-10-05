# 間歇適應：怎麼判斷練起來了、下一堂怎麼排

> 狀態：研究＋設計提案（2026-10-01），尚未實作。2026-10-05 補查中文來源、Gerschler 規則、HRR 雜訊（§1.1、§1.2、§3 開頭、§3.1、§4.3）。2026-10-05 補查（功率）：用功率判斷適應、W′bal、同堂掉幅、功率 vs 心率（§0 第 6 點、§3.6、§4.3）。
> 範圍：間歇適應的指標、什麼時候該進階／什麼時候是「還沒準備好」、組休與每趟時間怎麼定，以及 app 怎麼自動排間歇、自動檢討每一堂。
> 標記：每個門檻都標來源；**推估** = 我們自己定的；**未找到來源** = 找過但沒找到；**未驗證** = 有引用但沒讀到原文。

---

## 0. 結論先講

1. **「休息 60 秒內心率掉回有氧閾值＝適應了，可以加趟」只有徐國峰一個來源，而且他的有氧閾值指的是 70% 儲備心率，不是 app 實測的 AeT。**
   - 本文的「HRR」一律指心率恢復（heart-rate recovery）；儲備心率都寫全名。 文獻裡沒有這條規則。心率恢復在研究裡是很雜訊的指標，而且功能性過量訓練時反而會變快（§3.1）。所以它只能當**輔助訊號**，要跟功率達標一起看，不能單獨拿來決定加趟。
   - **2026-10-05 補查：** 徐國峰公開的文字版（RQ 說明文件）只定義「恢復秒數＝掉到 70% 儲備心率的秒數，越短越好」，**沒有 60 秒門檻，也沒有「加趟」**，而且要求同一份課表、同樣休息方式才能比（§1.1）。60 秒和加趟只出現在課程口述。HRR60 的日間誤差約 25%（Buchheit 2014 全文），跟適應或過量訓練造成的變化（6–8 bpm）差不多大，單一堂分不出來（§3.1）。結論不變：只當煞車，不當油門；另外 f-OR 保險要加雜訊門檻、連續兩堂才算（§4.3）。
2. **WKO5 有的是「目標」演算法，不是「進階」演算法。**
   - Optimized Intervals／iLevels 用功率–時間模型算出每一級的**每趟時間＋功率**。
   - 組數、組休比、區間總時間（TIZ）是教練寫死的建議值（Cusick 和 Golich 定的）。
   - dFRC 講者明講「不要拿來開課表」。
   - 心率恢復「有參考，但不是主要依據」。
   - WKO5 的公式裡沒有任何心率恢復或間歇進階的函式（§2）。
3. **app 現在的「最後一組比第一組低 5% → 退一步」，跟它自己引用的 WKO 講者說法相反。** 三場研討會都明講不贊成「掉 X% 就收工」。Golich 的判斷是看**在第幾組掉出目標**：最後一組掉沒關係，第 2–3 組就掉代表課表開錯了。建議改掉（§4.3）。
4. **進階的順序：**
   - 先加組數或區間總時間，加到上限（短間歇約 8 組，VO2max 總量 12–20 分）。
   - 再加長每趟時間，或縮短組休。
   - 最後才加功率。
   - 平日 40–50 分鐘的上限會擋住「加組」，所以要先把上限算進去再選哪個維度（§4.1）。
5. **資料：**
   - 推送到 COROS 的課表，計時的 step 會各切成一個 lap。實測一次推送的 CP 測試，lap 正好是 180.0 s 和 720.0 s。
   - 但 lap 沒有 `wkt_step_index`、`intensity`、`lap_trigger`（已查最近 12 個檔），所以要靠順序和時長對回計畫。
   - 沒有靜息心率，算不出 70% 儲備心率（§4.4）。
6. **2026-10-05 補查（功率）：用功率判斷適應，大方向不變；改一個門檻、加一個提示（§3.6、§4.3）。**
   - 「功率決定、心率只當煞車」維持。沒有任何新來源拿 W′bal 或同一堂的掉幅當加趟的油門。
   - 功率能證明「練起來了」的只有兩種：
     - 照計畫功率把整份課做完（Cusick IYT:725-729；Palladino 的 beta test 也是這樣判斷）。
     - 同一份課、差不多一樣累，功率比上次高（Rønnestad 2015 全文：10 週內課中平均功率 +9±5%，VO2max 同時 +8.7%；沒進步的那組課中功率也沒漲）。
   - **W′bal 在跑步還不準，不拿來定組休、不拿來判斷下一趟能不能做、不拿來判斷加趟。**
     - Skiba 原始 τ 在跑步不建議用（Vassallo 2020 全文）。
     - 訓練有素的跑者做到力竭，課結束時模型還剩 D′ 157±25 m，不是 0（Bellenger 2025 摘要）。
     - 個人 τ 從 103 到 189 秒都有（Welburn 2025 全文，騎車）。
   - **最後一趟的容許掉幅應該看每趟多長。** Allen 的「間歇做到力竭」表：以第 3 趟為基準，3 分鐘的趟掉 8–9% 才停、1–2 分鐘的趟 10–12%、5 分鐘 5–7%（Allen 部落格全文；書**原書未核對**）。app 現在一律 5%，對短趟太嚴。
   - 掉幅分數本身很不穩（Spencer 2006：掉幅 TE 14.9%，總時間 0.7%），不拿來比趨勢。
   - 新增一個「目標可能太低」的提示：同規格連兩堂每一趟都超過目標上限 → 建議重測 CP，不自己加功率（推估）。

---

## 1. 中文教練來源怎麼說

### 1.1 徐國峰的 60 秒規則

- 徐國峰課程影片筆記（間歇運動）：「如果在間歇完成後的休息，60秒內能回到有氧閾值，就代表適應了」
- 同上：「如果適應了就能增加趟數」
- 同上：「趟數的上限是單次的TSS不能超過xxx, 這個就要看WKO5了」
- 影片截圖：
  - 影片：「6.1.2 常見跑步課表的訓練要領｜間歇跑(Interval)」，13:53。
  - 投影片寫：「關鍵指標：恢復秒數」「看每一趟間歇結束後，心率從高點 → 掉到有氧閾值（HRR 70%）所需的時間長短」。
  - **投影片上沒有「60 秒」**。60 秒是從口述記下來的。
  - **投影片上的有氧閾值（寫作「HRR 70%」）＝70% 儲備心率**：靜息心率＋0.7×（最大心率−靜息心率）。
  - 同系列的心率區間說明也用「% HRR」表示儲備心率。

**外部來源（2026-10-05 補查）：**
- RQ 說明文件「如何判斷間歇訓練有進步？」（runningquotient-support.gitbook.io，全文）：
  - 定義：「從一趟間歇結束後，心率下降到儲備心率 70%所花的時間（單位：秒），即為該趟間歇的恢復秒數。」
  - 解讀：「恢復秒數愈短，代表跑者消除乳酸的能力愈強，恢復能力愈好，也是體能進步的象徵之一。」
  - 反向：「當同樣的課表所需恢復時間變長時，就警示著你需要多休息囉！」
  - **沒有 60 秒門檻，沒有「增加趟數」或「適應」的說法。**
- RQ 文章「間歇訓練的「恢復秒數」」（runningquotient.com/article/single/107）：**403，未讀**（當付費牆處理，沒找繞過的方法）。搜尋摘要另有兩點，查無原頁、**不採用**：
  - 例子「原本要 1 分鐘才恢復到 130 bpm，後來 50 秒或更短」。
  - 比較時課表、趟數、暖身、休息長度與方式（站／走／慢跑）、溫濕度、場地都要一樣。
- RQ 文章「利用訓練指數來找出間歇該練幾趟比較適合自己」（article/single/18）：**403，未讀**。標題看起來就是筆記裡「趟數上限看 TSS」那段，內容未核對。
- 《跑者都該懂的跑步關鍵數據》：**原書未核對**（只找到電子書販售頁）。
- 小結：
  - 文字版的用法是**同一份課表前後比**（縱向趨勢），不是單堂的絕對門檻。
  - 「60 秒」和「適應了就加趟」目前只有課程口述（§1.1 筆記）這一個來源。

徐國峰另外的門檻是**開始練間歇**的時機，不是適應：
- 周期化訓練筆記（爆發力、敏捷性、專項耐力周期）：「E配速90分鐘的心率飄移%…確定在10%以下之後就可以開始練間歇」
- app 已經實作成 `xu_drift`。

### 1.2 其他教練的心率規則（都是教練說法）

- 鐵人J帥（間歇訓練整理）：
  - 「下一組比前一組出現心率會很快的升進入LT2」
  - 「速度差很多甚至想跑走，那要嘛就是速度設得太快了，就是組休時間不夠了」
  - 新手「還要降到最大心率的60%以下」才開始下一組。
- 運動訓練法（同一份整理）：「休息時間可以設為恢復到65%…最大心跳率所需的時間」。

**中文網站（2026-10-05 補查，全文，都是教練／作者說法，沒有附研究）：**

| 來源 | 下一趟的心率條件 | 進階或停止 |
|---|---|---|
| 運動筆記〈跑者進化錄：間歇跑 速度的泉源〉（跑者進化特派員，2013-09-12） | 「要讓心跳降到最大心跳的 70%，一但降到 70%，隨即開始進行下一趟」 | 沒有進階條件 |
| Don1Don〈如何執行一個有效的跑步間歇訓練〉（王志袁，2016-05-26） | 「當心率降到 100 以下時，就可以開始進行下一組了（約略是最高心率的 55-60%」；「進階者可以在 65% 以下心率時就能進行下一組了」 | 停止：「跑 3 分鐘，但 4 分鐘後才降到 100 以下心率，這表示體能已經到極限了…就不建議再練了」。進階只說可以縮短組休，沒有量化 |
| JoiiUp〈使用心跳率決定間歇訓練的休息時間好嗎？〉（王順正） | 介紹「休息期的目標心跳率以每分鐘 120次的恢復為目標」 | 作者存疑：「隨著訓練趟次的增加，選定的目標心跳率似乎也應該隨著提高」，引 Seiler & Hetlelid 2005、Seiler & Sjursen 2004 |
| 運動筆記〈間歇訓練怎麼開始？〉（余文彥，2025-05-09） | 沒有心率數字：「休息太短，下一趟會撐不住；休息太長，心率掉太低則會影響訓練效果」 | 「若發現第 3、4 趟無法維持穩定配速，可能表示強度略高或恢復不夠」；進階＝「增加為 45 秒快跑，或縮短走路恢復時間」 |
| IR Sports〈間歇訓練入門〉（2025-05-08） | 沒有心率數字 | 「配速穩不穩定？能跑幾趟？才是進步指標」；「最後一趟比第一趟慢超過10秒」＝跑太多 |

- 重點：
  - 沒有任何一個中文來源有「60 秒內回到某心率＝可以加趟」。
  - 「心率回到 X 再跑」很常見，但 X 各說各話：最大心率 70%、100 bpm、120 bpm、65% 最大心率。用途都是**決定組休多長**，不是判斷適應。
  - Don1Don 的「恢復時間比工作時間還長就停」是課中停止條件，跟 Gerschler 的「90 秒回不來＝跑太快或太長」同一個邏輯（§3.1）。
  - 余文彥的「第 3、4 趟掉速＝強度高或恢復不夠」跟 Golich（IT2:84-86、233-235）一致。

### 1.3 組休比與每趟時間（課表模板整理）

- 間歇通用模板整理：
  - VO2max：3–8 組，組休 1:1～1:2，每趟 2:30–5 分。
  - Supra Threshold：每趟 4:30–5 分，2–6 組。
  - Near Threshold：1–4 組，約 10 分。
  - Power Tempo：20–30 分，組休 3 分。
- Daniels：
  - I 強度每趟 3–5 分。
  - R 強度工休比 1:2～1:3（《丹尼爾博士跑步方程式》整理）。
- Palladino 的跑步工休比（功率區間說明與訓練目的，下稱 ZONES，冒號後是整理稿的行號）：
  - Near-threshold 3:1～5:1（:54）。
  - Supra-threshold 2:1（:102）。
  - MAP 1:1，進階 2:1（:142-143）。
  - 三種都靠「延長每組時間／縮短恢復」進步（:61-63、:110-112、:151-153）。
- 密度原則：間歇訓練設計指南（1:2 以上練爆發、1:1 練 VO2max、2:1 練有氧容量與抗疲勞）。這份是 GPT 整理的，不是一手來源。

---

## 2. WKO5 實際做了什麼

### 2.1 有算的：每趟的時間和功率（Optimized Intervals）

- 反組譯（`docs/wko5-internals/formulas.md` §6.9b）：`targetduration(level)` 和 `targetpower(level)` 從 mFTP、FRC、TTE、τ1 算出六級的目標。

| 級 | 每趟時間 | 功率 |
|---|---|---|
| 0 Extensive Aerobic | TTE | FTP |
| 1 Intensive Aerobic | 0.9625·FRC/(0.0375·FTP) | 1.02·FTP |
| 2 Max Aerobic | 0.1625·FRC/(0.032·FTP) | 1.20·FTP |
| 3–5 Anaerobic / Max | 5·ln2·τ1、3·ln2·τ1、ln2·τ1 | 曲線上該時間的功率 |

- iLevels（§6.9）：FTP 以上每一級都附「建議時間範圍」，邊界是能量系統的交叉點（研討會 ROLE:444-446）。
- **組數、組休比、總時間不是模型算的：**
  - ROLE:398：「這些時間與組數…是我和 Dean Golich 一起設定的建議值，像是休息比例、目標總時數、建議組數」。
  - 例子：VO2max 3–8 組、總時間 12–30 分、組休 1:1（BI:331、BI:360-361、BI:370）；Golich 版 1:1 或 1:2（AP1:53-54）。
  - DFRC:684-685：「它不會告訴你：『你可以做幾組』」。
- 允許下修：ROLE:499「下修 5～10%」、ROLE:555「乘上 95%」。BI:516-519 重複功率預設 0.95，個人 85–95%。

### 2.2 進階規則（研討會的教練說法，不是演算法）

- **先加組數或總時間：**
  - BI:354、BI:357-358：「當他能穩定做到，下一步就要漸進增加組數，或是總時間」。
  - ROLE:509-527 的流程：3 分×5 組 → 覺得輕鬆就 6 組 → 維持幾次 → 3 分鐘的不超過 8 組 → 改成 4 分×4 組。判斷依據是主觀感受。
- **閾值以下先延長時間：** FR:214-218「先延長時間，再談加功率」，例如 2×20 → 1×45。
- **VO2max 總量固定，進步靠每組更用力：**
  - FR:356。
  - CW:309-313：累積 12–15 分就有效，超過 15 分「通常就是在硬撐」。
  - 整理稿的旁註（FR:232）：跑步大約 11 分。
- **大部分進步在前面：** CW:160、CW:169 說前 6–8 次課表就拿到約 90% 的進步；CW:264 說適應在 4–8 週。
- **反對「功率掉 X% 就收工」：**
  - BI:639-644：「第二組間歇下降 5% 就收工…我不會這麼做」。
  - FR:234-235：不贊成「掉4%以上就回家」。
  - IT2:340（Tim Cusick）。
- **什麼時候功率下降才算數（Golich，FRC 課）：**
  - IT2:84-86：「如果 350 瓦是出現在最後一個間歇，沒關係；但如果是第二或第三個，那就代表整體安排出了問題」。
  - IT2:233-235：「恢復時間不夠長，或者強度設得太高」。
- **心率恢復只是輔助：**
  - BI:776-784：「會，但不是主要依據…看間歇後心率下降的速度…核心依據還是功率」。
  - Palladino（PAL:699）：「心率最多只是冗餘，最糟還可能誤導」。

### 2.3 dFRC 和組休

- 引擎已實作 WKO5 的 `dfrc`（`evaluator._dfrc`）：
  - 高於 FTP 時線性消耗。
  - 恢復是雙指數：30% 用 τ = 25 s、70% 用 τ = 300 s。
  - **還沒對 WKO5 的實際輸出驗證過**（`functions.md:507`）。
- 講者的態度：
  - DFRC:168、DFRC:176-177：不拿 dFRC 開課表。
  - DFRC:424-425：還沒準到能決定「下一組該什麼時候做」。
- 拿來事後檢討可以：
  - DFRC:338：8 分鐘 VO2max 間歇至少消耗一半到三分之二。
  - DFRC:560：無氧課要「用到接近 0」。
  - DFRC:793-797：SST 掉很多＝做太用力。
- WKO5 Cloud Library 社群圖表（**不是內建演算法**）：
  - 「dFRC recovery」（Steve Bateman）：恢復時間＝dFRC 最低點到回升 ≥ `@RecoveredPct:=90` % FRC 的秒數。
  - 「Skiba Estimates of Recovery of FRC or W′」（William Renfroe）：比較 Skiba 1/2 和 dFRC。
  - 「Auto Marking VO2max Intervals」（William Renfroe）：自動標出 ≥ 約 2 分、≥ 75% VO2max 的趟。

### 2.4 找過但沒有的

**未找到來源：**
- WKO5 Expression Reference 裡沒有心率恢復、lap 或「間歇重複性」的函式。
- 研討會筆記裡沒有任何「心率 X 秒內降到 Y」的數字。
- 沒有任何依 dFRC 設定組休長度的規則。
- 沒有 WKO5 自己算出來的組數。

---

## 3. 文獻

**2026-10-05 補查：**
- 第一輪（2026-10-01）WebSearch 額度用完，§3 全部從 Europe PMC 抓摘要（標「摘要」），只有 Skiba 的 τ 公式從 Raimundo 2022 全文核對過。
- 這次查了：
  - 中文網路來源（徐國峰 RQ、運動筆記、Don1Don、JoiiUp、IR Sports）→ §1.1、§1.2。
  - Gerschler／Reindell 心率 120 規則的出處 → §3.1。
  - HRR 的測量誤差與最小有意義變化 → §3.1。
  - 開放全文核對：Buchheit 2014（Frontiers 全文 PDF）、Aubry 2015（PMC 全文）、Billat 2001（作者網站 PDF）。
- 還缺：
  - Bellenger 2016：**付費牆，未讀**（Europe PMC 標示非開放取用）。
  - Seiler & Hetlelid 2005、Buchheit & Laursen 2013、Le Meur 2017、Mann 2014：沒有開放全文，仍只有摘要。
  - Reindell & Roskamm 1959、Reindell／Roskamm／Gerschler 1962 原書：**原書未核對**。
  - 徐國峰 RQ 兩篇文章：403，未讀。

### 3.1 心率恢復（HRR）是適應的指標還是疲勞的指標

**兩者都是：適應和功能性過量訓練（f-OR）都會讓 HRR 變快。**

| 研究 | 發現 | 讀到哪裡 |
|---|---|---|
| Bellenger 2016 統合分析 | 正向適應時 HRR 上升 SMD 0.63；過量訓練時也上升 SMD 0.46。作者說 HRR 的上升 "also occur in response to overreaching"。比較能分辨兩者的是 HR acceleration（運動開始時心率上升的速度）。 | 摘要 |
| Aubry 2015 | f-OR 組 HRR 快了 8±5 bpm，而且 HRR 的變化和表現的變化呈**負相關**。全文核對（2026-10-05）：HRR＝力竭測試結束時心率減第 60 秒心率（坐在車上）；f-OR 組 38±11 → 45±11 bpm，急性疲勞組 40±8 → 42±10、對照組 35±10 → 34±12（都 unclear）；ΔHRR 與表現變化 r = −0.46；f-OR 組最高心率同時從 182±5 掉到 176±6 bpm。作者結論：HRR 的變化要搭配訓練階段、主觀疲勞和表現一起解讀。 | 全文（PMC4619310） |
| Le Meur 2017 | f-OR 組在 11 km/h 時 HRR 快 16±7 bpm，對照組 3±5 bpm。 | 摘要 |
| Siegl 2017、Mann 2015 | 超馬後 HRR 變快，但同時 RPE 變高。 | 摘要 |
| Mann 2014 | 前一段強度越高，HRR60 越快。前一段的強度本身就會干擾 HRR。 | 摘要 |
| Lamberts 2010、Capostagno 2021 | 4 週 HIIT 期間 HRR 上升的那一組，表現進步得比較多。 | 摘要 |
| Daanen 2012 回顧 | "HRR was related to training status"，但量測需要標準化。 | 摘要 |
| Buchheit 2014 | 主要靠靜息心率和次最大運動心率，再搭配訓練日誌、問卷和表現測試。全文核對見下方「HRR 的雜訊有多大」。 | 全文（Frontiers PDF） |
| Lamberts 2009（EJAP） | 訓練有素的車手做 4 週高強度訓練：高強度課後的 HRR 進步 7±6 beats，40 km 計時後的 HRR 進步 6±3 beats；HRR 進步與 40 km 成績進步 r = 0.96。 | 摘要 |

**HRR 的雜訊有多大（最小有意義變化，2026-10-05 補查）：**

| 來源 | 數字 | 讀到哪裡 |
|---|---|---|
| Buchheit 2014 表 1 | 運動後 HRR 的 typical error（CV）：HRR60s 約 25%、HRRτ 約 35%；訊噪比 1.3；對正向適應的最小有意義變化約 +7%。對照：運動中心率（HRex）CV 約 3%、最小有意義變化約 −1%、訊噪比 1.6。 | 全文 |
| Buchheit 2014 內文 | 「changes in HRR did not always correlate with performance changes」；HRR 能不能追蹤表現下降「has to be confirmed」。HRex 和 HRR 的 CV 在運動強度越高時略低（引 Lamberts 2004、2011a）。看到 HR 指標明顯變差時，可以馬上調整，或等「2–3 consecutive days/weeks」再決定（引 Brink 2010）；作者建議保守、寧可多喊停。 | 全文 |
| Bosquet 2008（IJSM） | 跑步機最大／次最大運動後被動恢復 5 分鐘：ΔHR 的 ICC 0.43–0.71，SEM 在第 1 分鐘約 20%、到第 5 分鐘約 8%。 | 摘要 |
| Arduini 2011（JSAMS） | 腳踏車 65%／80% 最大心率：第 60 秒的 %SEM 26.7，120 秒 19.5，180 秒 16.3；80% 最大心率比 65% 穩（mean ICC 0.827 vs 0.747）。 | 摘要 |
| Dupuy 2012 | 最大與次最大運動後：HRR 各指標 ICC 0.12–0.87、CV 5–72%；Δ60（結束時心率減 60 秒後心率）屬於最穩的。 | 摘要 |

- 換成 bpm（**推估**，用 Buchheit 的 25% 套 Aubry 的基準值 35–45 bpm）：同一個人、同一份測試，HRR60 日間差 **約 9–11 bpm** 都還在雜訊內。
- 適應（Lamberts 2009：6–7 bpm）和 f-OR（Aubry 2015：8±5 bpm）造成的變化**都跟雜訊一樣大**。單一堂的 HRR 變化分不出是適應、疲勞還是雜訊。
- 搜尋摘要裡的「HRR60s TEM 3 beats、CV 8.4%、最小有意義變化 > 5 bpm」：**查無出處，不採用**（找不到原頁）。
- 上面的研究都是**標準化測試**（固定強度、固定時間、坐著或站著被動恢復）。間歇課中的組休不是標準化條件，前一趟的強度又會改變 HRR（Mann 2014），雜訊只會更大（推估）。

**「60 秒內掉回 AeT／70% 儲備心率就能加趟」：**
- 同儕審查文獻裡**未找到來源**。
- 中文網路來源（2026-10-05 補查，§1.1、§1.2）：
  - 徐國峰公開文字版沒有 60 秒、沒有加趟，只說恢復秒數越短越好、變長就要多休息，而且要同一份課表前後比。
  - 其他中文網站的「心率回到 X 再跑」都是在**決定組休長度**，X 有 70% 最大心率、100 bpm、120 bpm 好幾種，沒有人拿來判斷加趟。
- 經典的 Gerschler／Reindell 規則（2026-10-05 補查，見下）。

**Gerschler／Reindell（2026-10-05 補查）：**
- 一手出處（Billat 2001 全文的參考文獻 2、3；**原書未核對**）：
  - Reindell H, Roskamm H. Ein Beitrag zu den physiologischen Grundlagen des Intervalltrainings unter besonderer Berücksichtigung des Kreislaufes. Schweiz Z Sportmed 1959; 7: 1-8。
  - Reindell H, Roskamm H, Gerschler W. Das Intervalltraining. München: Barth, 1962。
  - Billat 2001 只說間歇訓練由 Reindell 和 Roskamm 首先在期刊上描述，**沒有寫 120 bpm 或 90 秒**。
- 二手來源的數字：

| 來源 | 工作段 | 恢復 | 讀到哪裡 |
|---|---|---|---|
| Rushall 2015（SDSU Swimming Science Bulletin 55，教授的教學講義） | 「approached the maximum heart rate (~180 bpm; HRmax)」 | 「Rest continued until the heart rate declined to ~120 bpm」；休息長短由心率決定，不是時鐘。後面又寫一組課越做越累，回到 120 要的時間「normally would increase」 | 全文 PDF |
| Magness 2016（Science of Running 部落格） | 「stress the heart until 180 beats per minute」 | 「allow it 1 minute and 30 seconds to get back down to 120-125 beats per minute」；超過 90 秒＝「you went to fast or too long on your repeat」；不到 90 秒就在回到那個心率時開始下一趟。趟距多半 100／150／200 m。**沒有附一手出處** | 全文 |
| schwimmlexikon.de（德文游泳辭典） | — | 「120-140 Schläge/min nach „Freiburger Prägung"」；「lohnende Pause」＝不完全恢復的休息。引 Reindell, Rosskamm & Gerschler 1962 | 全文 |
| 王順正（JoiiUp，中文） | — | 休息期目標 120 bpm；作者認為目標心率應隨趟數提高 | 全文 |

- 小結：
  - 「工作到約 180、休到約 120 再跑」有多個二手來源一致。
  - 「90 秒內回到 120–125，回不來＝跑太快或太長」只有 Magness 部落格一個來源，沒附出處：**未驗證**。
  - 德文辭典給的是 120–140 的範圍，不是單一的 120。
  - 這是**決定組休長度**和**課中停止**的規則，不是「適應了就加趟」的規則。
  - 120 bpm 是絕對值，沒有依個人最大心率或閾值調整；Rushall 和王順正都指出越後面的趟，回到同一心率要越久。

**對設計的意思：**
- 心率掉得快不能證明適應了（Bellenger、Aubry）。
- 心率掉得慢可能只是前一趟比較用力（Mann 2014）。
- 所以 60 秒規則只能當**煞車**：功率都達標、但心率沒回到 AeT → 先不加趟。它不能當**油門**：心率回得快 → 就加。
- 2026-10-05 補查後加的：
  - 煞車的後果要輕：只到「邊界＝同一份課表再做一次」，不能升級成未適應或退階。理由是 HRR 單堂雜訊約 25%（Buchheit 2014），跟真正的變化一樣大。
  - 越後面的趟本來就回得越慢（Rushall 2015；王順正引 Seiler）。用「所有趟」算比例會讓長課表比短課表容易踩煞車（推估）。
  - 徐國峰文字版的用法是同一份課表前後比。等同規格的課累積到幾堂，可以改成「這堂的 `t_to_aet` 比上一次同規格慢」當煞車訊號（推估）。
  - Gerschler 的規則是用來定組休，不是判斷適應。app 的組休是固定秒數（推到 COROS），不改。

### 3.2 組休長度與方式

| 研究 | 發現 | 讀到哪裡 |
|---|---|---|
| Seiler & Hetlelid 2005 | 訓練有素的跑者做 6×4 分，組休 1／2／4 分。配速 14.4／14.7／14.7 km/h（休 2 分和 4 分一樣）；VO2 以休 2 分最高（66.2 vs 65.1／64.9 mL/kg/min）。自選組休平均 118±23 秒。結論：4 分鐘的趟，約 2 分鐘主動恢復最好。 | 摘要 |
| Seiler & Sjursen 2004 | 1 分鐘的趟 VO2 只到 82±5%；2–6 分鐘的趟到 92±4%。自選強度時，每趟 3–5 分最好。 | 摘要 |
| Dupont 2004、Thevenet 2007、Tardieu-Berger 2004、Ben Abderrahman 2013 | 被動恢復能撐的時間約是主動恢復的 2 倍。在 ≥ 90% VO2max 的絕對時間差不多。 | 摘要 |
| Thevenet 2008 | 主動恢復強度在最大有氧速度的 50% 或 67% 時，≥ 90% VO2max 的時間差不多；84% 時明顯變少。 | 摘要 |
| Buchheit & Laursen 2013 I/II | 可以調的變數有 9 個：強度、時間、恢復強度、恢復時間、模式、組數、組別、組間恢復的時間和強度。目標是每堂累積「數分鐘」在 ≥ 90% VO2max。具體的工休比建議沒讀到全文，**未驗證**。 | 摘要 |
| Billat 2000 | 30-30（100%／50% vVO2max）累積 VO2max 時間 7′51″，持續跑只有 2′42″。 | 摘要 |
| Rønnestad 2015、2020 | 30/15 短間歇（3 組×13 趟）提升 VO2max／最大有氧功率，比 4×5 分多。 | 摘要 |
| Seiler 2013 | 4×8 分的 VO2peak 進步（11.4%）比 4×4 和 4×16 多。 | 摘要 |

### 3.3 W′ 恢復（組休長度的上限邏輯）

| 研究 | 發現 | 讀到哪裡 |
|---|---|---|
| Skiba 2012 | τ = 546·e^(−0.01·D_CP) + 316 s。只在騎車驗證過。 | Raimundo 2022 全文引用 |
| Ferguson 2010 | W′ 在 2／6／15 分鐘後分別恢復 37／65／86%。 | 摘要 |
| Caen 2019 | 2／4／6 分鐘後恢復 46／51／59%。在 33% CP 恢復比 66% CP 多 9.4%。 | 摘要 |
| Caen 2021、Chorley 2022 | 雙指數：快成分 τ ≈ 11–22 s，慢成分 τ ≈ 256–388 s。WKO5 dFRC 的 25 s／300 s 落在這個範圍。 | 摘要 |
| Bartram 2018 | 菁英車手的實際恢復比 Skiba 預測的快。 | 摘要 |
| Black 2023 | 微分式的 W′bal 會高估恢復（9.8 vs 6.3 kJ）。 | 摘要 |

**結論：**
- 1–2 分鐘的組休只回來一半左右。
- 所以 W′bal 只用來**事後檢討**這堂到底挖得多深，不拿來決定組休。這和 WKO 講者的態度一樣（DFRC:424-425）。

### 3.4 課中疲勞訊號

- **掉速：**
  - Glaister 2008：percent decrement score 是最可靠的掉速算法。
  - Girard 2011：掉速和第一趟的速度成反比。
  - 「掉 > X% 就算疲勞」的門檻：**未找到來源**。
- **RPE：**
  - Foster 2001：session RPE 是有效的負荷指標。
  - Seiler & Sjursen 2004：配速得當的課，RPE 在課中本來就會爬到約 17。
- **HRV：** Kiviniemi 2007、Vesterinen 2016：HRV 在個人正常範圍內才排高強度，結果比固定課表好。app 沒有 HRV 資料。
- **心率逐趟爬升（cardiac drift）：** 門檻**未找到來源**（只有鐵人J帥的教練說法）。

### 3.5 越野：上坡間歇

- Barnes 2013：6 週、5 種強度的上坡間歇，5 km 都進步約 2%；強度最高的那組跑步經濟性最好。
- Ferley 2016：短的上坡趟（30 秒）在乳酸閾和力竭測試的進步，比長的（約 3 分）多。
- Ferley 2013：平路和上坡都進步，摘要說平路進步較多。
- Ferley & Vukovich 2015：10% 坡跑約 68% Vmax，力竭時間等於平路 Vmax。
- Uphill Athlete（教練來源）：有氧不足時加強度會讓進步變慢甚至倒退。這部分 app 已經用 `ua_gap` 處理；它沒有談組間恢復。

### 3.6 用功率判斷適應（2026-10-05 補查（功率））

**範圍：** 用功率（不是心率）判斷「練起來了沒」「下一趟能不能做」「該不該加」。這次用了 14 次 WebSearch，沒撞到額度。

**結論先講：**
- 功率還是主要依據。新來源裡，功率能當**油門**的只有兩種：
  1. 照計畫功率把整份課做完（教練說法）。
  2. 同一份課、差不多一樣累的情況下，功率比上次高（Rønnestad 2015）。
- W′bal／D′bal 在跑步還沒驗證好，只能事後看，不能拿來決定組休或加趟。
- 「最後一趟掉多少還算 OK」要看每趟多長，一律 5% 對短趟太嚴。
- 掉幅分數本身信度很差，不能拿來看長期趨勢。
- 心率裡最好的適應指標是「同功率的心率」，不是心率恢復。

#### 3.6.1 筆記（這次新引用的）

縮寫：CW＝Coaching with WKO4 Art and Science（已有縮寫，這次引用新的行）；IYT＝Individualize Your Training with WKO；TIS＝TIS training impact score；TC1＝Training and Coaching with WKO5 part 1；ACD＝The Art of Coaching with Data；Z2＝Zone 2 Biochemistry；DFRC＝dFRC by wko5（已有縮寫）；DFRCC＝圖表筆記〈dFRC圖表追踨間歇訓練〉；FRCP＝Building FRC and Pmax with WKO5。冒號後是行號。

| 主題 | 筆記原文 | 出處 |
|---|---|---|
| 進階＝做完再加 | 「先設一個你認為可行的次數──比如先做 5 次，太輕鬆就做 6 次，6 次很累就再做 6 次，然後慢慢增加到 7 次、8 次」；「逐步『漸進式增加訓練量（Progression）』，這才是訓練的關鍵」 | IYT:725-732（Cusick）。跟 ROLE:509-527 同一套，判斷依據是主觀感受 |
| 反對「掉 5% 就停」 | 「『當你功率掉 5% 就該結束該組間歇』──其實這種想法可能會讓你錯過最重要的刺激」；第 3–5 趟「無氧電池其實已經部分枯竭…這才是真正對最大有氧能力的訓練刺激」 | IYT:758-771。跟 BI:639-644、FR:234-235 一致，這是第四個講者出處 |
| 用總量回饋調下一堂 | 4×8 分無氧 TIS 到 9 →「這太多了」；「下次我就把間歇縮短、功率調低，或是總組數減少，讓訓練回到理想的影響區間」 | TIS:366-373。另外 TIS:393-394：Stryd 的 TIS「追蹤效果還不錯」，Garmin「功率高估」 |
| 減量時減組數 | 「大致上保持強度不變、減少間歇組數是比較保守穩妥的做法」 | CW:561-562（原本引用 CW:562，這次核對行號） |
| 漸進性 | 「這就是訓練的漸進性（progression）」：現在做的課是為了「之後…能把那些關鍵課表做得更好」 | CW:528-535。講的是周期安排，不是單堂進階規則 |
| 選手需要穩定進步 | 「這位選手對訓練負荷非常敏感，需要穩定的進步（progression）。一旦訓練停滯，他的表現也會立刻停滯」 | ACD:824-825 |
| 同心率下的功率 | 標出 LT1、LT2 心率，「觀察這兩個心率點下的平均功率變化」；「選手體能變好時，他在同樣心率下的輸出功率就會變高」；當作「是否進入下一階段訓練的關鍵依據之一」 | ACD:925-934 |
| 每瓦心率斜率 | 「功率與心率之間的『斜率』…選手越來越 fit，這條斜率會越來越平緩」 | ACD:940-947 |
| 心率回來≠恢復 | 「乳酸在高強度下會持續維持15到20分鐘以上」；「你可能從心率判斷已經恢復，但代謝系統仍處於不穩定狀態」 | Z2:422-437（San Millán）。講的是 Zone 2 課混入強度，不是間歇組休，但支持「心率恢復不能證明可以再做」 |
| 組休的重要性 | 休息的強度與長度「甚至跟主要運動時間一樣重要」；「量化『可重複性』與『恢復速率』」是未來的突破，「恢復速率是整個 dFRC 概念中最棘手的部分」 | TC1:331-374（Cusick） |
| 下一組什麼時候做 | dFRC「不要把它當作『決定下一組開始的唯一依據』」；「使用歷史資料…回顧上次做這堂課時發生了什麼…那時候的功率是多少」 | DFRC:448-460。跟 DFRC:424-425 同一段，這次補上「看上一次同一份課」 |
| 依目的用多少 W′ | VO2max 課「綠色線約掉到一半或2/3就夠了，搭配短休息」；練 RWC「要將RWC耗完，搭配長休息讓RWC恢復」 | DFRCC:18-21（整理自部落格，非一手） |
| 短的無氧趟 | 「休息時間則採取『充分恢復再做下一組』的方式，因為每一組的品質才是關鍵」 | FRCP:751-752 |
| 組休別太長 | 「我們不讓休息時間太長，因為我們希望保留一些乳酸」 | CW:330-331（講的是混合式間歇） |

- `Palladino教練的課表.md`：講測試週、恢復週怎麼接，**沒有**間歇進階或恢復判斷。
- `palladino文章.md`：只是文章目錄。其中「Towards Individualizing Work Interval Power Targets for Runners」的連結這次去讀了公開頁（§3.6.4）。
- `palladino課表整理/VO2max.md`：只有個人紀錄「S5進階到2:50 S6不變 S7改13:40」「一個月排一次，安排在testing 後兩周」，沒有寫判斷依據，不採用。
- 掃過 `65 ⚡ 功率訓練/` 其他筆記：沒有找到「功率掉多少要停／要加」的數字，也沒有用 W′bal 定組休的規則。

#### 3.6.2 W′bal／D′bal：能不能決定組休、下一趟、加趟

| 研究 | 發現 | 讀到哪裡 |
|---|---|---|
| Skiba 2014（IJSPP，騎車野外資料） | 力竭時 W′bal 0.5±1.3 kJ，沒力竭時 3.6±2.0 kJ；ROC 分析說模型「useful for identifying the point at which athletes are in danger of becoming exhausted」 | 摘要 |
| Skiba 2014（MSSE） | 預測與實際 W′ 差 −1.6±1.1 kJ；作者提到可以找出「physiological optimum formulation of work and recovery intervals」 | 摘要 |
| Skiba 2015（EJAP） | W′ 恢復與新模型預測 r = 0.97；第二趟可用的 W′ 跟磷酸肌酸的差值直接相關；磷酸肌酸恢復得比 W′ 快 | 摘要 |
| Skiba & Clarke 2021（IJSPP 回顧） | 不同版本的 W′bal 建立在不同假設上 | 摘要 |
| Vassallo 2020（EJAP，跑步，9 位團隊運動員） | 60 秒跑／30 秒恢復。恢復的 τ：輕度 119±32 s、中度 190±45 s、重度 336±77 s。跑步版模型預測力竭時間 929±94 s，實際 968±117 s。原始 Skiba 模型「not recommended」，會明顯低估。作者說可用來定訓練強度、預測課中撐不住，但還要驗證 | 全文 |
| Bellenger 2025（IJSPP，跑者） | CS／D′ 由 1500–5000 m 成績算。30 堂間歇：力竭的 14 堂結束時 D′bal 157±25 m，沒力竭的 16 堂 200±19 m；能分出兩者（ES 1.01±0.72），但「did not accurately quantify exhaustive sessions as exhaustive」（應該接近 0）。作者歸因於恢復的 τ 不準 | 摘要 |
| JSAMS 2025「running-specific recovery time constant」 | 同一組人做跑步專用的 τ | **付費牆，未讀**（403） |
| Welburn 2025（EJAP，騎車，13 人） | 5 條通用 τ 公式在力竭時都預測不到 0 kJ。個人 τ 103–189 s，和相對 CP（r = −0.69）、LT1（r = −0.58）負相關。結論：要用個人化的 τ | 全文 |
| Chorley & Lamb 2020（EJAP） | 受訓者的 W′ 恢復跟 VO2max 相關 r = 0.81，未受訓者 r = 0.18 | 摘要 |
| Galán-Rioja 2024（J Hum Kinet，騎車） | 用 W′bal 設計的課：4 分鐘恢復能回到 80–100% W′bal；每趟用掉 33–67% W′ | 全文（網頁） |
| Vaccari 2022（EJAP，跑道） | 第一次休息延後（跑到力竭才休）力竭時間 464±67 s，比 3 分鐘就休（388±48 s）、30 秒就休（308±44 s）長；遞減間歇在 ≥ 90% VO2max 的時間 998±129 s，比短間歇 678±116 s、長間歇 673±115 s 多 | 全文（網頁） |
| Anderson 2026（Sports Med 範圍回顧，124 篇） | CS 與 D′bal 是中長距離表現的強決定因子；測試方法不同會影響估計值 | 摘要 |

- 對本題的意思：
  - **決定組休：不行。** 跑步的 τ 還沒有驗證過的版本；騎車的個人差異已經到 103–189 s。用通用 τ 算出來的「休到 X% 再跑」誤差太大。
  - **判斷下一趟能不能做：不行。** Bellenger 2025：做到力竭的跑者，模型還說剩 157 m。講者也說不要當唯一依據（DFRC:448-449）。
  - **判斷加趟：沒有任何來源這樣用。** 能做的是事後看「這堂挖得多深」，而且只跟自己比（Bellenger 2025 能分出力竭和沒力竭，但絕對值不準）。
  - app 的 `interval_eval.battery` 用 Vassallo 2020 的 119／190 s：受試者是團隊運動員，不是耐力跑者，這點要寫進 UI 說明（推估）。

#### 3.6.3 同一堂的功率衰退：幾 % 算有意義

| 來源 | 說法 | 讀到哪裡 |
|---|---|---|
| Allen 2015（Hunter Allen Power Blog「Increasing Repeatability and Peak Power」） | 「Intervals to Exhaustion」：以**第 3 趟**的平均功率為基準，「typically stop … when your power drops off about 5-12%」。依每趟長度：20 分 3–5%、10 分 4–6%、5 分 5–7%、3 分 8–9%、2 分 10–12%、1 分 10–12%、30 秒 12–15% | 全文（教練部落格） |
| Rutberg 2025（CTS 部落格） | 引 Allen & Coggan《Training and Racing with a Power Meter》：以第 3 趟為準，掉超過 15% 就停。分組的課：掉 ≥ 15% 先多休 1 分鐘，下一趟有回來才繼續；閾值課最後 2–3 分鐘撐得很辛苦不算停止理由 | 全文。書**原書未核對**；15% 和部落格的表不一樣 |
| WKO 講者（Golich、Cusick） | 反對「掉 X% 就收工」（BI:639-644、FR:234-235、IT2:340、IYT:758-759）；看**第幾趟**掉出目標（IT2:84-86） | 筆記 |
| Spencer 2006（JSAMS，重複衝刺） | 總衝刺時間 TE 0.7%；掉幅 TE 14.9%（95% CL 10.8–31.3%） | 摘要 |
| Oliver 2009（JSAMS） | 「Mathematical procedures calculating fatigue from small performance drop-offs always result in unreliable measures」 | 摘要 |
| Glaister 2008 | percent decrement score 是幾種算法裡最可靠的（已在 §3.4） | 摘要 |

- 搜尋摘要裡的「7×30 m 掉幅 CV 36.2%」：**查無出處，不採用**。
- 小結：
  - 「掉幅幾 % 算疲勞」沒有研究門檻（§3.4 原本的結論不變）。只有 Allen 的教練表。
  - Allen 的表是**課中停止**規則，而且是「掉到這樣就沒有訓練效果了」，不是「掉到這樣代表沒適應」。
  - Allen 的表跟 Golich 的看法可以接得起來：短的趟後面掉 8–12% 還在正常範圍，所以不能用 5% 判「沒做好」。
  - 基準用第 3 趟，不是第 1 趟：第 1 趟常常衝太快（Girard 2011：掉速和第一趟速度成反比，§3.4）。
  - 掉幅分數的雜訊和它要測的東西一樣大（Spencer 2006），不要拿掉幅的週間變化當適應指標。

#### 3.6.4 用功率判斷進階的教練方法（公開頁面）

| 來源 | 怎麼判斷 | 讀到哪裡 |
|---|---|---|
| Palladino「Individualized Interval Training Power Targets for Runners」Part 3（2022-01） | 目標功率＝FTP/CP ＋ RWC 貢獻的一個固定比例：「the percentage should be applied to the RWC contribution only - then, add that on top of FTP/CP」。只用 %CP 會讓同 CP、不同 RWC 的人刺激不一樣。**沒有**談趟間 RWC 消耗、恢復或掉幅 | 全文（Google Docs 公開頁） |
| 同上 Part 4（2022-02，beta test，18 人） | 判斷目標對不對：整堂都在目標帶內完成＋5 分制主觀感受（1 太輕、3 剛好、5 太難）。5×3:00：18/18 完成，感受 3.2；4×6:00：17/18 完成，感受 3.6。**沒有**掉幅、心率或進階規則 | 全文 |
| Stryd 部落格「The Power of Intervals」（2021-08） | 短趟目標比例高，因為無氧供能（400 m 約 120% CP）；「Targeted interval training is simpler and more precise with power」；短趟心率幫不上忙。**沒有**組休、進階、掉幅規則 | 全文 |
| Stryd 部落格「Top Strategies to Analyze Your Power-Based Workouts」（2023-08） | 只說拿實際功率對目標。**沒有**進階規則 | 全文 |
| Xert「SMART Intervals」（Mastracci，2020-04） | 工作段做到 MPA 掉到某個值才結束；恢復段等 MPA 回到「TP + 數字 ×（PP−TP）」才結束。都由 fitness signature 算。實際功率超過 MPA → 「Breakthrough」→ 更新 signature（Xert 說明頁）。**沒有**寫進階規則 | 全文（騎車，商業軟體說明） |
| Allen／CTS | 見 §3.6.3。CTS：做完整份課「will help you perform your next PI workout without having to add recovery time」 | 全文 |

- 小結：
  - 沒有一家用「這堂的功率曲線」直接決定加趟。都是**目標由模型算（CP＋W′／RWC）**，再看「有沒有照目標做完」。
  - Xert 的 breakthrough 跟 WKO 的模型更新是同一個概念：實際功率超過模型 → 改的是模型（CP），不是課表。
  - Palladino 的判斷是「完成率＋主觀感受」，跟 Cusick（IYT:725-729）一樣。app 沒有 RPE，只剩完成率。

#### 3.6.5 功率 vs 心率當適應指標

| 研究 | 發現 | 讀到哪裡 |
|---|---|---|
| Rønnestad 2015（Scand J Med Sci Sports，騎車） | 兩組都照「maximal sustainable work intensity」自選強度。短間歇組（3×13×30/15 秒）課中平均功率 10 週 +9±5%（P < 0.01），VO2max +8.7±5.0%；長間歇組（4×5 分）課中功率 +2±5%（P = 0.2），VO2max +2.6±5.2%（不顯著）。兩組每 2 週的 RPE 都差不多（17.1–18.2） | 全文（PDF） |
| Swart 2009（JSCR，騎車 21 人） | 8×4 分、80% 峰值功率、休 90 秒，4 週。心率處方和功率處方都進步：峰值功率 +3.5–5.0%、40 km 計時 +2.1–2.3%。作者：「prescribing training based only on power is more effective…was not supported」 | 摘要 |
| ten Haaf 2019（EJSS） | 加強訓練後低／中強度心率降 4.4／5.5 bpm，「unrelated to the change in performance」；f-OR 和急性疲勞分不出來 | 摘要 |
| Dun 2022（Front Cardiovasc Med，心肌梗塞復健 11 人） | 第一堂到最後一堂：高強度段的速度、坡度、功率、%VO2peak 都上升，心率 124→126 bpm、%HRpeak 88→90% 沒變 | 全文（族群差很多，只當旁證） |
| Buchheit 2014（已在 §3.1） | 運動中心率（HRex）CV 約 3%，是心率指標裡最可靠的 | 全文 |

- 小結：
  - **功率在自選強度下會隨適應上升**（Rønnestad 2015）。在照目標跑的課（app 的情況），功率被目標綁住，適應會變成「做完」「同功率心率變低」「感覺變輕」。
  - 心率處方不一定比較差（Swart 2009），但它回答的是「強度有沒有到」，不是「下一堂能不能加」。
  - 同功率的心率是心率裡最好的指標（Buchheit 2014；ACD:931-947），但心率下降本身不一定代表變強（ten Haaf 2019），要跟功率一起看。
  - 心率恢復仍只當煞車（§3.1；Z2:435-437）。

**對設計的意思（2026-10-05 補查（功率））：**
- 不變：
  - 判斷順序「未適應 → 邊界 → 達標」、`first_miss` 看第幾趟掉出目標。
  - W′bal／dFRC 只顯示，不進 `interval_outcome`。也不拿來定組休（§3.3 結論不變，理由更強）。
  - 同功率心率（`interval_eval` 的 HR @ matched power）只顯示，不當油門（ten Haaf 2019）。
- 要改的（細節見 §4.3）：
  1. 最後一趟的容許掉幅依每趟長度（Allen），基準用第 3 趟。
  2. 加一個「目標可能太低」提示，導向重測 CP。

---

## 4. App 設計提案

### 4.0 現況

- `quality_gate.py` 的劑量表 `DOSE`／`AFTER` 是固定階梯：
  - 5×1′ → 6×1′ → 4×3′（上坡）→ 5×3′ → 4×4′，之後 3×8′／4×8′ 交替。
  - 用 8 週內做了幾次（`dose_history`）推進一步。
  - 最後一堂 `fade < −5%` 就退一步（`dose_step`）。
- 專項期只有固定的「爬坡間歇 5×4 分」（`overview.py:606`），沒有進階。
- `workout_review.detect_efforts` 已經算出這些欄位：
  - 每趟的功率、%CP、平均／最高心率。
  - `hr_drop60`：峰值減掉結束後第 60 秒的心率。條件是下一趟在 60 秒後才開始，所以組休 < 60 秒時沒有這個值。
- `count_reps`（10 秒功率 ≥ 95% CP、≥ 40 秒）和 `detect_efforts`（30 秒功率、≥ 60 秒）是兩套偵測，門檻不同。
- `plan_prefs.trim_quality` 遇到平日上限時，會先砍暖身和緩和，然後**減一組**，等於默默把「加組」的進階吃掉。

### 4.1 (a) 開課表：下一堂怎麼排

**課表的資料結構：**
- 把固定的 `DOSE` tuple 改成可調的 `IntervalSpec(type, reps, work_s, rest_s, rest_mode, lo, hi, uphill)`。
- 劑量表只當**起點**；之後的每一堂由 §4.3 的狀態機從上一堂的規格推出來。

**類型跟著周期走：**

| 周期 | 怎麼排 | 來源 |
|---|---|---|
| 基礎期 | 照現有的劑量表與門檻 | aerobic-base-readiness.md §4.5 |
| 專項期 | 把固定的 5×4 改成爬坡 supra／VO2max 階梯：3 分 → 4 分 → 5 分，105–110% CP | Palladino ZONES:89-90、142；Barnes 2013 |
| 減量期 | 強度不變、組數減少 | CW:562；Bosquet（現有） |

- 同一種類型連續 6–8 堂或 4–8 週以後換類型（CW:160、CW:264、AP1:380）。這個換法屬推估。

**每趟時間：**
- 用劑量表的值。
- 另外用 WKO5 的 `targetduration(2)` = 0.1625·W′/(0.032·CP) 交叉檢查。例：W′ 13.1 kJ、CP 220 W → 302 秒，約 5 分。
- `targetpower(2)` = 1.20·FTP 是騎車的設定，跑步不套用（BI:251-252）。跑步強度用 Palladino 的 %CP 帶。

**組休：**

| 類型 | 組休 | 來源 |
|---|---|---|
| 4 分鐘的 VO2max 趟 | 2 分 | Seiler & Hetlelid 2005 |
| 3 分以下 | 1:1 | Palladino MAP；BI:630 |
| 1 分鐘 | 1:2 | 間歇模板整理；Palladino 早期 fartlek |
| 閾值下 | 3:1～4:1 | Palladino ZONES:54 |

- 恢復方式寫進 detail：
  - VO2max 用走或極慢跑。被動恢復能多做幾趟，VO2max 時間不會少（Dupont 2004、Thevenet 2007）；鐵人J帥（JS:24）也是這樣。
  - 閾值下用慢跑。
  - 恢復強度不要超過約 2/3 最大有氧速度（Thevenet 2008）。

**平日時間上限（`cap_weekday`，選填）：**
- 進階之前先算這一堂要幾分鐘：`15 + reps×(work+rest) + 10`。
- 加一組會超過上限 → 改走 §4.3 的第二維度：拉長每趟（總時間不變、組數減少，BI:396-397 允許），或縮短組休。
- `trim_quality` 不能再減到比上一堂完成的組數還少。
- 沒設上限就不受影響。

### 4.2 (b) 檢討：從 FIT 判斷每一趟

**找出每一趟：**
1. **有推送的課表 → 依順序把 FIT 的 lap 對到計畫的 step。**
   - 證據：一次推送的「CP 測試 3 分 + 12 分」有 `coros_plan_push` 紀錄，FIT 的 5 個 lap 依序是暖身／180.0 s／休息／720.0 s／緩和，暖身、休息、緩和都不是整數秒。
     - 計時的全力段剛好等於 step 的長度。
     - 暖身和休息不是整數，表示按 lap 可以提早結束一個 step。
   - 所以：用**順序**對 step；用時長（±5 秒，推估）確認是不是工作段；休息 lap 的實際長度照記錄。
   - 讀 lap 的方法跟 `racepower/cptest._read` 一樣。
   - 1 分鐘的趟會不會也切 lap，還**未驗證**（S0）。
2. **對不到 → 用功率型態偵測。** 把 `count_reps` 和 `detect_efforts` 合成一支 `find_reps(t, power, plan)`：
   - 平滑窗：每趟 < 90 秒用 10 秒，否則 30 秒。
   - 門檻：`0.95 × 計畫下限`。
   - 最短：`0.67 × 計畫的每趟時間`。
   - 這三個數字都屬推估。
   - 沒有計畫時，退回現在的 `detect_efforts`。

**每一趟的指標：**

| 欄位 | 定義 | 來源 |
|---|---|---|
| `power`、`pct_target` | 整趟平均，以及對計畫帶中點的比例 | — |
| `in_band` | `power ≥ 計畫下限 × 0.98` | 推估（容許 2%，接近 ROLE:555 的 95% 下修） |
| `hr_end` | 結束前 5 秒的平均心率 | 推估 |
| `hr_peak` | 結束前 10 秒到結束後 15 秒的最高心率 | 現有 `detect_efforts` |
| `hr_at60`、`drop60` | 結束後第 60 秒的心率，以及 `hr_peak − hr_at60` | 現有；組休 < 60 秒時沒有值 |
| `t_to_aet` | 組休內第一次 ≤ AeT 的秒數 | 徐國峰 |
| `aet60` | `t_to_aet ≤ 60` | 徐國峰的門檻；**AeT 先用 app 的實測 AeT 代替 70% 儲備心率**，有靜息心率時改用 70% 儲備心率 |
| `wbal_min` | 每趟結束時 dFRC 的最低點 | WKO5 `dfrc`（`_dfrc`），只顯示 |
| `hr_creep` | 每一趟 `hr_peak` 比第一趟高多少（同功率） | 只顯示，**未找到來源**門檻 |

**整堂的指標：**
- `fade`：最後一趟比第一趟，沿用現有欄位，但不再單獨用來判斷。
- `first_miss`：第一個不在帶內的是第幾趟。
- `done`：完成趟數 ÷ 計畫趟數。
- `aet60_share`：符合 `aet60` 的趟數比例。
- `rpe`：選填，Foster 2001 的 session RPE 0–10，要在 UI 加輸入。

### 4.3 (c) 進階狀態機

每一堂分成三類。只有**達標**才往前；判斷依據功率為主，心率只當煞車。

**判斷順序：未適應 → 邊界 → 達標。** 前面的條件先成立就停。

| 結果 | 條件 | 下一堂 | 來源 |
|---|---|---|---|
| **未適應** | `done < 1`；或 `first_miss` 落在第 2 趟到倒數第 2 趟 | 先保持組數、組休多 1 分鐘。下一堂還是未適應，就退回劑量表上一步 | IT2:84-86（Golich 說「第二或第三個」；推廣到倒數第 2 趟屬推估）、IT2:233-235；FR:238 |
| **未適應（目標太高）** | `first_miss = 1`（第一趟就沒到） | 目標功率下修 5%，組數不變 | ROLE:499「下修 5～10%」 |
| **邊界** | 有 `hr_at60` 時 `aet60_share < 0.5`；或 RPE ≥ 8；或只有最後一趟沒到、而且掉 > 5%（2026-10-05 補查（功率）：改依每趟長度，見下方） | 同一份課表再做一次 | ROLE:517「維持六組再做幾次」；徐國峰（煞車）；0.5、8、5% 屬推估 |
| **達標** | 以上都不成立：`done = 1`，所有趟都 `in_band`，或只有最後一趟沒到而且掉 ≤ 5% | 進階一個維度（見下） | BI:357-358；IT2:84-86；ROLE:514（「蠻輕鬆的」→ 加組） |

**疲勞保險（f-OR）：**
- 情況：`drop60` 比 8 週中位數快，**但**功率沒達標或 RPE 偏高。
- 處理：當作未適應，並在 status 提示「可能累積疲勞」。
- 來源：Aubry 2015、Bellenger 2016。觸發條件屬推估。
- 限制：`baseline()` 要 ≥ 5 個樣本，FIT 資料夾裡目前一堂間歇都沒有，所以前 5 堂不會觸發。
- **2026-10-05 補查，改了觸發條件：**
  - 原本「比 8 週中位數快」就算，太敏感。HRR60 單堂雜訊約 25%（約 9–11 bpm，§3.1），跟 f-OR 的 +8±5 bpm（Aubry 2015）一樣大。
  - 改成：`drop60` 比 8 週中位數快 **≥ 25%**（Buchheit 2014 的 CV；當門檻屬推估），**而且**連續 2 堂都這樣（Buchheit 2014 引 Brink 2010「2–3 consecutive days/weeks」），**而且**功率沒達標或 RPE 偏高。
  - 只比**同規格**的課（同每趟時間、同組休、同恢復方式）。徐國峰文字版和 Daanen 2012 都要求條件一致。
  - 影響：`quality_gate.py`（S3 的 `interval_outcome`）。這條目前程式裡還沒做，是改提案，不是改既有行為。

**心率煞車（邊界列的 `aet60_share`）的補充（2026-10-05 補查）：**
- 現在的實作（`quality_gate.interval_outcome`、`AET60_MIN_SHARE = 0.5`）已經是「只踩煞車、結果是邊界」，方向跟補查結果一致，**不用改**。
- 建議加兩點（推估）：
  - 只在走路或站著恢復時才算（恢復方式不同，HRR 不能比）。組休 < 60 秒時本來就沒有 `hr_at60`，不用另外處理。
  - 同規格累積 ≥ 3 堂後，改用 `t_to_aet` 跟上一次同規格比；慢超過 25% 才踩煞車。
- 不要因為心率回得快就加趟（油門）。新來源沒有任何一個支持這樣做。

**用功率判斷的修正（2026-10-05 補查（功率），§3.6）：**
- **最後一趟的容許掉幅 `LAST_FADE` 依每趟長度定，不再一律 5%：**
  - 數字取 Allen「Intervals to Exhaustion」表的下限：≤ 2 分 10%、3 分 8%、5 分 5%、10 分 4%、20 分以上 3%；中間的長度取比較短那一級（推估）。
  - 掉幅改成「最後一趟比第 3 趟」；不到 4 趟的課沿用比第 1 趟（Allen 用第 3 趟當基準；4 趟的門檻屬推估）。
  - 來源是教練部落格，書**原書未核對**；CTS 引同一本書寫的是 15%，兩者不一致，所以取部落格表的下限（比較保守）。
  - 影響：`quality_gate.py` 的 `LAST_FADE`、`interval_outcome` 的 `fade`（只影響「只有最後一趟沒到」那一列）；`interval_eval.evaluate` 的掉幅說明文字。`first_miss` 的判斷不動。
  - 改了以後 3 分鐘以下的課比較容易「達標」。這跟 Golich（IT2:84-86）、Cusick（IYT:758-771）的方向一致。
- **新增「目標可能太低」提示（推估）：**
  - 條件：同規格連續 2 堂，每一趟平均功率都 ≥ 目標上限（`hi × CP`）× 1.03。
  - 處理：結果仍照原規則（通常是達標），另外在 status 提示「功率一直超過目標，CP 可能低估了，建議重測」。不自己把目標功率往上加。
  - 理由：Xert 的 breakthrough、WKO 的模型更新、Palladino Part 3 都是「實際功率超過模型 → 改 CP／模型」，目標跟著 CP 走（`session()` 本來就用當週 CP）。Rønnestad 2015：同樣累、功率變高＝真的進步。
  - 3% 借用 Buchheit 2014 HRex 的 CV，只是「超過雜訊」的粗估。
  - 影響：`quality_gate.interval_outcome`（加一個 flag，不改 outcome）、`dose_step`（連續 2 堂的判斷）、`interval_eval` 顯示。
- **W′bal 不進判斷：**
  - `interval_eval.battery`（dFRC＋Skiba，τ 119／190 s）照舊只顯示。UI 說明加一句：跑步的 W′bal 還沒驗證，力竭的跑者模型也可能還顯示剩不少（Bellenger 2025）。
  - 不新增「依 W′bal 定組休」或「W′bal 回到 X% 才開始下一趟」。
- **同功率心率：** 照舊只顯示。不因為它下降就加趟（ten Haaf 2019）。
- **`interval_reps.py`：** 不用改。找趟的方法跟本次補查無關。

**進階的維度順序（達標時）：**
1. **加組數或總時間，加到上限：**
   - 1 分鐘的趟最多 8 組，3 分鐘的最多 8 組（ROLE:521）。
   - VO2max 總時間上限 16 分。這是取 CW:309-313「12–15 分」和 FR:231「15–20 分」的中間，屬推估；整理稿旁註說跑步大約 11 分（FR:232）。
   - 閾值下總時間上限是 TTE 的 150%（TTE:521）。
2. **拉長每趟時間（總時間不變），或縮短組休 30 秒：**
   - 拉長每趟：ROLE:527 的 3 分×5 → 4 分×4。
   - 縮短組休：Palladino ZONES:61-63；TTE:464-467。
   - 組休的下限：VO2max 1:0.5，閾值下 4:1。屬推估。
3. **加功率 +2%：**
   - FR:356：VO2max「每一組更用力」。2% 屬推估。
   - 新的 CP 測試套用後，目標功率自動跟著變（`session()` 本來就用當週的 CP 算）。
- 平日上限擋住第 1 步時，直接跳到第 2 步（§4.1）。
- 恢復週或護欄的 `hold` 照現有規則：不往上加。

**現在的規則要改的地方：**
- `dose_step` 改成「達標才加一步」。現在是每做一次就推進。
- `fade < −5%` 改成上面的 `first_miss` 判斷。
- `interval_lines` 的「休息 60 秒心率降幅 < 20 → 休息拉長」（`HR_DROP_MIN = 20`；`done-workout-review.plan.md:87` 沒標來源，本次也**未找到來源**），改成顯示 `aet60` 與 `t_to_aet`。
  - 2026-10-05 補查：還是**未找到來源**。最接近的是 Don1Don（王志袁）「恢復時間比工作時間長就停」和 Gerschler「90 秒回不到 120 就是跑太快或太長」（**未驗證**），兩者都是看「多久回到某個心率」，不是看「60 秒降了幾下」。

### 4.4 (d) 有的資料 vs 缺的資料

| 項目 | 狀態 |
|---|---|
| 1 秒功率、心率（COROS／Stryd） | 有 |
| CP、W′（3′/12′ 測試）、mFTP／TTE（WKO5 模型） | 有 |
| 實測 AeT（plan threshold 列）、LTHR | 有；LTHR 可能還是 WKO5 預設，`lthr_default` |
| FIT lap（時長、距離、平均功率／心率、最高／最低心率） | 有。一般跑步每 1000 m 自動切 lap（已核對兩次一般跑步的距離） |
| FIT lap 的 `wkt_step_index`、`intensity`、`lap_trigger` | **沒有**（最近 12 個 COROS 檔都沒這些欄位） |
| 推送的課表在 FIT 裡每個 step 一個 lap | **有**：一次推送的 CP 測試，5 個 step 對到 5 個 lap，計時段剛好 180.0／720.0 s。1 分鐘的趟還**未驗證** |
| FIT 裡的 `workout`／`workout_step` 訊息 | **未驗證**：`fit_reader` 沒讀這兩種訊息 |
| 活動對應到計畫的哪一堂 | 有一部分：`_done_by_this`／`scheduled_test` 只處理測試課 |
| 靜息心率（算 70% 儲備心率） | **缺**：只有 racepower 估的 HRmax |
| session RPE | **缺** |
| HRV | **缺** |

### 4.5 (e) 實作計畫（每一步都可以單獨合併）

| 步驟 | 做什麼 | 檔案 | 測試 |
|---|---|---|---|
| **S0** | 不寫程式。推送一堂 5×1′ 到 COROS，跑完後抓 FIT，確認 1 分鐘的趟和 2 分鐘的休息也是各一個 lap。CP 測試已確認計時 step 會切 lap。 | — | — |
| **S1** | 每一趟的指標：`detect_efforts` 加 `hr_end`、`hr_at60`、`t_to_aet`、`aet60`、`wbal_min`（呼叫 `_dfrc`）；`interval_summary` 加 `first_miss`、`aet60_share`；`CACHE_KEY` 升到 v9 | `engine/workout_review.py` | `test_workout_review.py`：用 `fit_builder` 合成的 5×1′ 串流，心率在第 40／70 秒掉到 AeT，檢查 `aet60`；組休 45 秒時沒有 `hr_at60` |
| **S2** | 找出每一趟：新檔 `find_reps(t, power, plan)`，以及 `match_laps(plan_steps, laps)`；把活動連到計畫的品質課（plan_store 的 uid） | 新增 `engine/interval_reps.py`；`files/fit_reader.py`（讀 lap 欄位，有的話再讀 `workout_step`） | 新增 `test_interval_reps.py`：lap 完全對得上、少一趟、沒有 lap 時的功率偵測、1 分鐘的趟 |
| **S3** | 判斷結果：`interval_outcome(reps, plan, aet, rpe)` → 達標／邊界／未適應；`dose_history` 改存 `outcome`；`dose_step` 改成達標才 +1 | `engine/quality_gate.py` | `test_quality_gate.py`：每一列（只有最後一趟掉、第 2 趟掉、心率煞車、f-OR 保險） |
| **S4** | 可調的課表規格：`IntervalSpec` 和 `next_spec(prev, outcome, cap)`；`session()` 由規格產生 title／detail（COROS 的 `_quality_steps` 仍能解析）；`trim_quality` 不減到比上一堂完成的組數少 | `quality_gate.py`、`plan_prefs.py`、`sync/coros_workouts.py` | `test_quality_gate.py`、`test_plan_prefs.py`（上限 45 分時走第 2 維度）、`test_coros_workouts.py`（30 秒組休、走路恢復） |
| **S5** | 專項期爬坡階梯、減量期規則、projection 的預測週 | `overview.py`、`projection.py` | `test_overview.py`、`test_planning.py` |
| **S6** | UI：間歇卡加 `t_to_aet`、`aet60`、`wbal_min` 欄位與結論列；session RPE 輸入；選填的靜息心率設定（有了就改用 70% 儲備心率） | `workout_review._intervals`、static、`settings/repository.py` | `test_panels_workout.py` |

規格同步：S1–S4 合併後跑 `/prp-spec`，更新 `docs/spec/workout-review.spec.md`，並在 `aerobic-base-readiness.md` §4.5 加一行指向這份文件。

---

## 來源

### 中文筆記整理（教練來源的轉錄，不是一手來源）

**徐國峰與其他教練的心率規則：**
- 徐國峰課程影片「6.1.2 常見跑步課表的訓練要領｜間歇跑(Interval)」筆記與截圖
- 徐國峰周期化訓練筆記（爆發力、敏捷性、專項耐力周期）
- 間歇訓練整理（Daniels、鐵人J帥）

**組休比與每趟時間：**
- 間歇訓練設計指南（GPT 整理）
- 間歇通用模板整理
- 《丹尼爾博士跑步方程式》筆記
- Palladino 功率區間說明與訓練目的（ZONES）
- 鐵人J帥文章（JS）

**中文網路來源（2026-10-05 補查）：**
- RQ 說明文件「如何判斷間歇訓練有進步？」 https://runningquotient-support.gitbook.io/rq-3/premiummore/highintenseintervaltraining （全文）
- RQ「間歇訓練的「恢復秒數」」 https://www.runningquotient.com/article/single/107 （403，未讀）
- RQ「利用訓練指數來找出間歇該練幾趟比較適合自己」 https://www.runningquotient.com/article/single/18 （403，未讀）
- 運動筆記〈跑者進化錄：間歇跑 速度的泉源〉2013 https://running.biji.co/index.php?q=news&act=info&id=6597
- 運動筆記〈間歇訓練怎麼開始？〉余文彥 2025 https://running.biji.co/index.php?q=review&act=detail&id=57429
- Don1Don〈如何執行一個有效的跑步間歇訓練〉王志袁 2016 https://www.don1don.com/archives/75622/
- JoiiUp〈使用心跳率決定間歇訓練的休息時間好嗎？〉王順正 https://www.joiiup.com/knowledge/content/778
- IR Sports〈間歇訓練入門〉2025 https://www.irsports.com.tw/w/bd/1429
- 《跑者都該懂的跑步關鍵數據》徐國峰（原書未核對）

**Gerschler／Reindell 歷史（2026-10-05 補查）：**
- Rushall BS. 2015. Swimming Science Bulletin 55: Interval training, HIIT, and USRPT. https://coachsci.sdsu.edu/swim/bullets/55%20ITHIITUSRPT.pdf （全文）
- Magness S. 2016. A brief history of interval training. Science of Running.（部落格，全文）
- schwimmlexikon.de「Intervallmethode」（全文）
- Billat LV. 2001. Interval training for performance: Part I. Sports Med 31(1).（作者網站 publications.billatraining.com PDF，全文）
- Reindell & Roskamm 1959；Reindell, Roskamm & Gerschler 1962（原書未核對）

**WKO 研討會（中文逐字整理，冒號後是行號）：**
- 研討會 Building intervals th WKO5 way（BI）
- Role and Purpose of iLevels and Optimized Intervals（ROLE）
- Interval Training with WKO4 Part 2（IT2，Golich）
- Analyzing Interval Training Part 1（AP1，Golich）
- Coaching with WKO4 Art and Science（CW）
- dFRC by wko5（DFRC）
- Fatigue Resistance Strategy（FR）
- Advanced Running with Power（PAL）
- 另外：Building FTP, TTE, and Stamina with WKO5（TTE）

### WKO5

- `docs/wko5-internals/formulas.md` §6.9、§6.9b（反組譯）
- `docs/wko5-internals/functions.md` §5 dfrc
- `%LOCALAPPDATA%\WKO4\Documentation\WKO5 Expression Reference.html`（targetduration／targetpower／targetname）
- Cloud Library 社群圖表：
  - 「dFRC recovery」（Steve Bateman）
  - 「Skiba Estimates of Recovery of FRC or W」（William Renfroe）
  - 「Auto Marking VO2max Intervals」（William Renfroe）
  - 「HIIT Validation iLevels」

### 文獻（Europe PMC 擷取；DOI 都是實際抓到的）

**心率恢復與過量訓練：**
- Arduini A, et al. 2011. J Sci Med Sport. doi:10.1016/j.jsams.2011.02.012（2026-10-05 補查，摘要）
- Aubry A, et al. 2015. PLoS One. doi:10.1371/journal.pone.0139754（PMC4619310，2026-10-05 讀全文）
- Bellenger CR, et al. 2016. Sports Med. doi:10.1007/s40279-016-0484-2（付費牆，未讀全文）
- Bosquet L, et al. 2008. Int J Sports Med. doi:10.1055/s-2007-965162（2026-10-05 補查，摘要）
- Buchheit M. 2014. Front Physiol. doi:10.3389/fphys.2014.00073（2026-10-05 讀全文）
- Dupuy O, et al. 2012. Clin Physiol Funct Imaging. doi:10.1111/j.1475-097X.2012.01125.x（2026-10-05 補查，摘要）
- Lamberts RP, et al. 2009. EJAP. doi:10.1007/s00421-008-0952-y（2026-10-05 補查，摘要）
- Capostagno B, Lambert MI, Lamberts RP. 2021. JSCR. doi:10.1519/jsc.0000000000003227
- Daanen HA, et al. 2012. IJSPP. doi:10.1123/ijspp.7.3.251
- Lamberts RP, et al. 2010. Scand J Med Sci Sports. doi:10.1111/j.1600-0838.2009.00977.x
- Le Meur Y, et al. 2017. IJSPP. doi:10.1123/ijspp.2015-0675
- Mann TN, et al. 2014. EJAP. doi:10.1007/s00421-014-2907-9
- Mann TN, et al. 2015. JSCR. doi:10.1519/jsc.0000000000001004
- Siegl A, et al. 2017. IJSM. doi:10.1055/s-0043-110226

**間歇的設計、組休與恢復方式：**
- Ben Abderrahman A, et al. 2013. EJAP. doi:10.1007/s00421-012-2556-9
- Billat VL, et al. 2000. EJAP. doi:10.1007/s004210050029
- Buchheit M, Laursen PB. 2013. Sports Med Part I doi:10.1007/s40279-013-0029-x；Part II doi:10.1007/s40279-013-0066-5
- Dupont G, et al. 2004. MSSE. doi:10.1249/01.mss.0000113477.11431.59
- Rønnestad BR, et al. 2015. Scand J Med Sci Sports. doi:10.1111/sms.12165
- Rønnestad BR, et al. 2020. Scand J Med Sci Sports. doi:10.1111/sms.13627
- Seiler S, Sjursen JE. 2004. Scand J Med Sci Sports. doi:10.1046/j.1600-0838.2003.00353.x
- Seiler S, Hetlelid KJ. 2005. MSSE. doi:10.1249/01.mss.0000177560.18014.d8
- Seiler S, et al. 2013. Scand J Med Sci Sports. doi:10.1111/j.1600-0838.2011.01351.x
- Tardieu-Berger M, et al. 2004. EJAP. doi:10.1007/s00421-004-1189-z
- Thevenet D, et al. 2007. EJAP. doi:10.1007/s00421-006-0327-1
- Thevenet D, et al. 2008. J Sports Sci. doi:10.1080/02640410802072697

**W′ 恢復：**
- Bartram JC, et al. 2018. IJSPP. doi:10.1123/ijspp.2017-0034
- Black MI, et al. 2023. MSSE. doi:10.1249/mss.0000000000003039
- Caen K, et al. 2019. MSSE. doi:10.1249/mss.0000000000001968
- Caen K, et al. 2021. MSSE. doi:10.1249/mss.0000000000002673
- Chorley A, et al. 2022. EJAP. doi:10.1007/s00421-021-04874-3
- Ferguson C, et al. 2010. J Appl Physiol. doi:10.1152/japplphysiol.91425.2008
- Raimundo JAG, et al. 2022. Front Physiol. doi:10.3389/fphys.2022.952818（Skiba τ 公式的全文出處）
- Skiba PF, et al. 2012. MSSE. doi:10.1249/mss.0b013e3182517a80

**用功率判斷適應（2026-10-05 補查（功率），§3.6）：**
- Anderson M, et al. 2026. Sports Med. doi:10.1007/s40279-026-02410-x（摘要）
- Bellenger CR, Nitschke M, Bartram JC. 2025. IJSPP 20(5). doi:10.1123/ijspp.2024-0036（摘要）
- Chorley, Lamb. 2020. EJAP. doi:10.1007/s00421-020-04459-6（摘要）
- Dun, et al. 2022. Front Cardiovasc Med. doi:10.3389/fcvm.2021.772815（全文）
- Galán-Rioja, et al. 2024. J Hum Kinet. https://jhk.termedia.pl/Comparison-of-Physiological-Responses-between-a-W-BAL-INT-Training-Model-and-a-Critical,186976,0,2.html（全文網頁）
- Oliver JL. 2009. JSAMS. doi:10.1016/j.jsams.2007.10.010（摘要）
- Rønnestad BR, et al. 2015. Scand J Med Sci Sports 25:143–151. doi:10.1111/sms.12165（2026-10-05 讀全文 PDF）
- Skiba PF, et al. 2014. MSSE. doi:10.1249/mss.0000000000000226（摘要）
- Skiba PF, et al. 2014. IJSPP. doi:10.1123/ijspp.2013-0471（摘要）
- Skiba PF, et al. 2015. EJAP. doi:10.1007/s00421-014-3050-3（摘要）
- Skiba PF, Clarke DC. 2021. IJSPP. doi:10.1123/ijspp.2021-0205（摘要）
- Spencer M, et al. 2006. JSAMS. doi:10.1016/j.jsams.2005.05.001（摘要）
- Swart J, et al. 2009. JSCR. doi:10.1519/jsc.0b013e31818cc5f5（摘要）
- ten Haaf T, et al. 2019. EJSS. doi:10.1080/17461391.2019.1571112（摘要）
- Vaccari, et al. 2022. EJAP. PMC9813124（全文）
- Vassallo, et al. 2020. EJAP. doi:10.1007/s00421-019-04266-8（PMC6969867，全文）
- Welburn, et al. 2025/2026. EJAP 126(2). doi:10.1007/s00421-025-05912-0（PMC12948861，全文）
- 教練與商業來源（非同儕審查，全文）：
  - Allen H. 2015-03-04. Power Up: Increasing Repeatability and Peak Power. https://www.hunterallenpowerblog.com/2015/03/power-up-increasing-repeatability-and-peak-power.html
  - Rutberg J（CTS）. 2025-03-07 更新. Interval Training: Knowing When Enough is Enough. https://trainright.com/weekend-reading-knowing-when-enough-is-enough/
  - Palladino S. 2022. Individualized Interval Training Power Targets for Runners, Part 3、Part 4（Google Docs 公開頁，目錄：palladino文章.md 的「Towards Individualizing Work Interval Power Targets for Runners: A Compendium」連結）
  - Stryd Team. 2021-08-04. The Power of Intervals. https://blog.stryd.com/2021/08/04/the-power-of-intervals/
  - Stryd Team. 2023-08-17. Top Strategies to Analyze Your Power-Based Workouts. https://blog.stryd.com/2023/08/17/top-strategies-to-analyze-your-power-based-workouts/
  - Mastracci A（Xert）. 2020-04-02. Advanced Workout Design using SMART Intervals. https://www.baronbiosys.com/advanced-workout-design-using-smart-intervals/
- WKO 研討會（這次新加的縮寫）：Individualize Your Training with WKO（IYT）、TIS training impact score（TIS）、Training and Coaching with WKO5 part 1（TC1）、The Art of Coaching with Data（ACD）、Zone 2 Biochemistry（Z2）、Building FRC and Pmax with WKO5（FRCP）、圖表筆記〈dFRC圖表追踨間歇訓練〉（DFRCC）

**課中疲勞訊號與 HRV：**
- Foster C, et al. 2001. JSCR. doi:10.1519/1533-4287(2001)015<0109:anatme>2.0.co;2
- Girard O, et al. 2011. Sports Med. doi:10.2165/11590550-000000000-00000
- Glaister M, et al. 2008. JSCR. doi:10.1519/jsc.0b013e318181ab80
- Kiviniemi AM, et al. 2007. EJAP. doi:10.1007/s00421-007-0552-2
- Vesterinen V, et al. 2016. MSSE. doi:10.1249/mss.0000000000000910

**上坡間歇：**
- Barnes KR, et al. 2013. IJSPP. doi:10.1123/ijspp.8.6.639
- Ferley DD, et al. 2013. JSCR. doi:10.1519/JSC.0b013e3182736923
- Ferley DD, Vukovich MD. 2015. JSCR. doi:10.1519/jsc.0000000000000834
- Ferley DD, et al. 2016. IJSM. doi:10.1055/s-0042-109539

**教練來源（非同儕審查）：** Uphill Athlete "Why is my Zone 2 so slow?"；Daniels' Running Formula（**未驗證**，只用中文筆記整理）。

**未驗證或沒有抓到的：**
- Gerschler／Reindell 原書（1959、1962）：原書未核對；「90 秒內回到 120–125」只有 Magness 部落格，未驗證。
- Skiba 2015：2026-10-05 補查（功率）讀了摘要（§3.6.2）。Plews：還沒查。
- 2026-10-05 補查（功率）：
  - JSAMS 2025「Development of a running-specific recovery time constant for validly assessing D′ balance during exhaustive intermittent running」：付費牆，未讀（403）。
  - 「Durability, Fatigability, Repeatability and Resilience」（J Appl Physiol 2025，doi:10.1152/japplphysiol.00343.2025）：403，未讀。
  - Allen & Coggan《Training and Racing with a Power Meter》：原書未核對（只讀到 Allen 部落格和 CTS 的轉述，兩者的數字不同）。
  - 「7×30 m 掉幅 CV 36.2%」：只出現在搜尋摘要，查無出處，不採用。
  - PubMed 頁面（40132598）只回 cookie 頁，改用 Europe PMC 讀摘要。
- 徐國峰 RQ 兩篇文章（article 107、18）：403，未讀。
- Bellenger 2016：付費牆，未讀。
- 「HRR60s TEM 3 beats、CV 8.4%、最小有意義變化 > 5 bpm」：只出現在搜尋摘要，查無出處，不採用。
- （Billat 2001 已在 2026-10-05 讀全文，移出這一列。）
