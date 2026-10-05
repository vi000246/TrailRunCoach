# 百岳（多日登山）怎麼練：門檻、周期、課表（SP-114）

> 調查日期：2026-10-05。只做調查，沒有改程式。
> 標記：**已驗證**＝這次讀到原文（網頁經擷取工具摘要後閱讀；PDF 是轉成文字後直接讀）；**只有摘要**＝只看到論文摘要或搜尋摘要；**推估**＝我的延伸。
> 來源等級：教練（Uphill Athlete／Evoke 本人）／嚮導公司（RMI、吉力馬札羅健行公司）／同儕審查／官方（國家公園、長野縣）／台灣教練／日本（山本正嘉）。
> **範圍限制：只用登山、健行、高山攀登的來源。** 越野跑和超馬的來源（Big Vert Ultra、Koop、CTS、iRunFar、江晏慶的越野周期）只在「現在 app 用了什麼」時提到，不拿來當百岳的依據。
> 接續 `race-feasibility.md`（SP-105）。相關：`uphill-athlete-mountain-metrics.md`、`loaded-carry-training.md`、`back-to-back-and-long-day.md`、`baiyue-from-running.md`。

## 摘要

1. **「每週量練到最難那天的 90 %」不適用百岳。** 這條來自 UA 的越野跑計畫〈Big Vert Ultra〉。UA 的登山文章和登山計畫**完全沒有**「週量 = 攻頂日的幾 %」這種規則。登山用的是另外三把尺：
   - 每週**時數**（依起始體能 5／7／8–10 小時起跳，16 週計畫練到 11 小時）；
   - **最長的一課 ＝ 每週有氧時間的 50 %**；
   - **攻頂日模擬**：行程前 8 週起，每週至少一次「一天爬完攻頂日的爬升（100 %），背和行程差不多重的背包」。
   都是**已驗證**（UA〈Training for Mountaineering〉PDF 原文）。
2. **長天的門檻也要改。** app 現在的「長天練到最難那天難度（コース定数）的 80 %、上限 6 小時」，依據是圖表的目標帶、江晏慶（越野）和 iRunFar（越野）。UA 登山的標準是**爬升 100 %、帶背包**，沒有打折，也沒有要求練到同樣的時數（在平地、睡飽、吃飽時走，本來就比較快）。以百岳來說，這通常比現在的門檻嚴格（例子在 §1.4，推估）。
3. **肌耐力（ME）是登山的「高強度課」，不是爬坡間歇。** UA 登山計畫的進度：ME 的爬升從「目標行程最大那天爬升的 50 %」、背包 15–40 % 體重開始，最後幾次要「比最大那天爬得更多、背得更重」。每週一次，做完要有 3 天輕鬆，共 6–10 次、6–8 週（已驗證）。
4. **背包要練，但訓練用的有氧長天不要背到行程最重。** UA：前 4 週（16 堂有氧課）不背，之後 5 % → 10 % → 15 % 體重，有氧課最多 20 %（PDF）或 25 %（網頁版）；攻頂日模擬背「和攻頂日差不多」的重量。這和 app 現在「不背包、改走陡坡」的做法不同（`steep_hill.py`），要你決定（§5）。
5. **減量：2–3 天的行程，UA 說一週就夠**，量減到最硬那週的一半，練的內容要像目標行程。Evoke：1–2 週、量減 50 %、最後兩週停 ME。app 現在是 14 天（已驗證）。
6. **時間不夠：UA 說 8 週是最低限度，沒有有效的 2 週或 4 週計畫。** 剩 4–8 週時，各家只說「把階段壓縮」（RMI）或「有 8 週就用 8 週」（UA）。登山來源另外給了一條很實用的路：**換一條低一級的路線、或多排一天**（雪霸國家公園分級：B 級要先有 A 級經驗；長野縣：同一級爬 2–3 次再升級，都已驗證）。
7. **下坡的準備，最好的證據是登山研究本身。** Maeo、山本等 2017：背 10 % 體重、−28 % 坡下坡走 5 分鐘，一週後再做 40 分鐘下坡，力量下降少 49 %、痠痛少 66 %（已驗證）。登山杖也減少下坡後的肌肉損傷（Howatson 2011，只有摘要）。
8. **給 app 的建議（§4）：百岳和越野跑分開判。** 百岳看「時數」「攻頂日模擬做了幾次」「帶背包的爬升速度（山本的表）」「有沒有爬過同一級的路線」；不看「週公里數 ÷ 最難那天」。

## 1. 第 1 題：app 現在的門檻適不適用百岳

### 1.1 app 現在怎麼判（程式唯讀）

| 項目 | 現在的規則 | 依據（文件裡寫的） | 是登山來源嗎 |
|---|---|---|---|
| 週量（可行性、完備程度） | 高峰週的距離和爬升各自 ÷ 最難那天，取低的；多日行程一律算「長的賽事」，≥ 90 % 才算夠，< 50 % 判「太難」（`race_feasibility.py:59–61, 108–112, 267–272, 422–427`） | UA〈Big Vert Ultra Marathon〉 | **不是**，是越野跑計畫 |
| 長天（完備程度） | 最近 6 週最硬一次的コース定数 ÷（最難那天的コース定数 × min(1, 6 h ÷ 那天時數)），≥ 80 % 夠、≥ 70 % 差一點（`race_feasibility.py:345–347, 407–418`） | 圖表目標帶；江晏慶「抓七成」；iRunFar 5–6 小時 | **不是**（江晏慶、iRunFar 都是越野跑） |
| 長天上限 | 6 小時（`specific_phase.TRAIL_LONG_MAX_MIN = 360`） | iRunFar 100 英里 5–6 小時（SP-106） | **不是** |
| 超馬週時數 | Koop，只給 `race`／`other`，百岳不跑（`koop_need`） | Koop | 不適用百岳，程式也沒用 |
| 撤退時間 | 預估到山頂的時間 vs 撤退時間 | 你的規則 | 百岳專用，保留 |
| 專項期 | 賽前第 10–3 週；減量 14 天（`planning.py:44–45`） | 越野通則 | 部分不同（§2） |
| 陡坡健走 | 專項期每週一次 40–50 分、不背包、用 Pandolf 換成更陡的坡（`steep_hill.py`） | Pandolf、Ludlow & Weyand、UA trekking | 部分是 |

### 1.2 UA 登山怎麼定「練多少」（已驗證）

讀的是 UA 創辦人 Steve House 和 Scott Johnston 合寫的〈Training for Mountaineering〉（RMI 網站放的 PDF，27 頁，轉成文字後逐段讀）和網頁版（2021-03-14）。

| 項目 | UA 原文 | 換成數字 |
|---|---|---|
| 起始週時數 | 「Un-trained people with a higher BMI, typically start with 5 hours … lean-looking … 7 hours … Fit, active people who can jog 10 miles … 8-10 hours per week」，含肌力課 | 5／7／8–10 小時，分 6 天 |
| 週結構 | 「training 6 days a week, four of those days are aerobic training, 2 are strength training, and one is a full day off」 | 4 次有氧＋2 次肌力＋1 天全休 |
| 最長的一課 | 「the longest duration workout should make up 50% of the total weekly aerobic training volume」 | 長天 ≈ 每週有氧時間的一半 |
| 週量進度 | 「we progress training load for 3 weeks in a row. Then we prescribe an easy week that is typically 50% of the training volume (time) of the most difficult week」；「Typically these occur every third week」 | 3 週加、1 週減到 50 %（app 是 65 %） |
| **攻頂日模擬** | 「Summit day on Mount Rainier … is about 4,000 vertical feet. 8 weeks out from your climb you should be doing at least one workout per week where you ascend 4,000 vertical feet in one day, with a backpack of approximately the same weight which you'll carry on Rainier, typically about 25 pounds.」並說明這是在低海拔、睡飽、吃飽、鞋子乾的情況，「still only an approximately of the real Rainier climbing day」 | **行程前 8 週起，每週一次：攻頂日爬升 100 %＋攻頂日背包** |
| ME 起點 | 「Vertical gain: 50% of the vertical gain of your biggest day on your goal climb. Weight in pack: 15-40% of body weight is typical. The point is to have the rate of climb be limited by your leg strength, not by your breathing.」 | ME 從最大那天爬升的 50 % 開始 |
| ME 終點 | 「The progression goal is for your final workouts to encompass more vertical and more weight than the biggest day of your upcoming objective.」（UA ME 文章，2016-11-27） | ME 最後幾次 > 最大那天的爬升和背包 |
| ME 進度 | 「progress either the weight or the total vertical in a stairstep fashion, but not both at the same time」 | 一次只加一樣 |
| 有氧課背包上限 | PDF：「tops out at 20% of body weight, and this is only for the most fit」；網頁版：「tops out at 25% of body weight」；「We do not recommend trying to build up to the actual pack weight of your heavy carries」 | 有氧長天 ≤ 20–25 % 體重；不必練到行程最重的那包 |
| 最少幾週 | 「The minimum effective training plan is 8 weeks. … there is no two or four week training plan that is effective」 | 8 週 |

- https://assets.rmiguides.com/_includes/_documents/attachments/Training_for_Mountaineering_UphillAthlete.pdf
- https://uphillathlete.com/mountaineering/training-for-mountaineering/
- https://uphillathlete.com/aerobic-training/vertical-beast-mode-what-is-muscular-endurance-why-it-is-important-for-any-alpinist-or-mountaineer-and-how-do-you-train-it/

**UA 登山沒有的東西（查過，沒有）：**

- 沒有「每週距離或爬升 = 攻頂日的幾 %」。登山頁只用時數，再加上「攻頂日模擬」和 ME 這兩種**單次課**的爬升目標。
- 沒有「長天上限幾小時」。Rainier 的模擬是看爬升，不看時間。
- 〈How Fit Do You Need to Be to Climb Everest?〉（2025-04-20，已驗證）提到的「每週平均約 4,700 英尺、最高 17,000 英尺」是**一位聖母峰學員的紀錄**，不是規則；原文也說 CTL 這類數字是「historical observations, not predictive targets」。

### 1.3 其他登山來源

| 來源 | 說法 | 等級 |
|---|---|---|
| UA〈Training for Trekking and Hiking〉（Steve House，2025-05-20） | 起始每週 5–8 小時，「demanding treks」練到 10–12 小時以上；最長的一課約佔每週時間 50 %；背包 1–4 週不背、5–6 週 5 %、7–8 週 10 %、9–10 週 15–20 %，上限「20% of your body weight or up to the maximum weight you will carry on your trek」；訓練目標包括「recover well from back-to-back days on the trail」 | 教練，已驗證 |
| UA 16 週登山計畫說明頁 | 起始「over 6 hours of required training per week」，最後 11 小時；入門條件「hike one hour every day for a week without notable fatigue」；階段：轉換期、基礎期、登山專項期、減量期 | 教練，已驗證 |
| UA Podcast〈Training for Mountaineering: Base Training〉（2023-02-13） | ME 從 10 %（約 15 % 也可以）體重、約 1 小時開始；約每 7 天一次，強度高了可以拉到 10 天；Rainier（攻頂日 4,000 英尺）的例子：「2000 vertical feet with like 20% of body weight」；ME 也可以連兩天（週六約 90 分＋週日約 2.5 小時） | 教練，已驗證（逐字稿的擷取） |
| RMI〈Mountaineering Training: Using Benchmarks〉（2023-10-22） | Rainier 的目標配速「about 1000 vertical feet per hour」（約 300 m/h），背約 20 磅；到出發時能維持 1,500–2,000 英尺／小時（約 450–600 m/h）代表體能很好 | 嚮導公司，已驗證 |
| RMI〈How to Train for Mount Rainier〉（Kristian Whittaker，2026-03-26） | 16 週、4 階段；每週 4–7 小時起，模擬週到 15 小時以上；第 12、14 週排連兩天的長健行；最長健行練到 7–8 小時；背包練到「approaches 40 pounds on your biggest days」；背水上山、山頂倒掉、輕裝下山；第 15 週量減約 50 %，第 16 週只處理後勤和休息；時間比 16 週短就「compress … accordingly」 | 嚮導公司，已驗證 |
| Ian Taylor Trekking〈Training for Kilimanjaro〉（2026-10-04 更新） | 最後階段「gain 600m … in 1 hour 30/40 minutes with a weighted backpack. You should be able to do this on back to back days」；長健行從 3 小時練到 8 小時，每週一次；出發前一週減量；需要 4–8 個月 | 嚮導公司，已驗證 |
| 雪霸國家公園管理處 百岳分級（健行筆記轉載，2015-08-03） | A：一般健行，1–3 天，不需經驗；B：中級縱走，4–5 天，或 1–3 天但有危險地形，**需完成 A 級**；C：5 天以上，需完成 B 級；C+：垂降攀岩，需完成 C 級 | 官方（轉載），已驗證 |
| 長野縣山岳總合中心〈信州 山のグレーディング〉說明（2014-06-26，PDF） | 「体力度をステップアップしたい方は、日頃から全身持久力を高める早歩きや坂道歩きなどのトレーニングをすると同時に、同じランクの山を２，３回経験してから次のランクに進むように」；体力度用山本正嘉的ルート定数算，是**整條路線**的值，大的路線分幾天走 | 官方，已驗證 |
| 山本正嘉（YAMAP 專訪） | 心肺：「月間累計2000mの登山を、週1回・標高差500mの山に4回に分散」；肌力：深蹲、腹肌各 15 下 × 5 組，每週 2–3 次 | 日本，已驗證（專訪的擷取，沒有日期） |
| 山本正嘉 2015（`race-feasibility.md` §2.5） | 背 10 % 體重、1 小時能爬 430 m（7 METs）就有日本阿爾卑斯無雪期登山的基礎體力；350 m 只夠健行 | 日本，已驗證（前一份文件） |
| 楊礎豪〈百岳練習生〉（台灣登山體能訓練師，2025-02-21、2025-07-30） | 8 週、每週 3 天（耐力 30–40 分、肌力、假日登山測驗或 40–60 分耐力）；第 4、8 週減量；入門 A 級百岳的判斷：背實際裝備走類似路線，**能符合上河地圖時間**就有基礎體能；3 天以上縱走背包盡量 < 體重 30 % | 台灣教練，已驗證 |

- https://uphillathlete.com/trekking/training-for-trekking-and-hiking/
- https://uphillathlete.com/training-plans/16-week-mountaineering-training-plan-rpe/
- https://uphillathlete.com/podcast/training-for-mountaineering-base-training/
- https://uphillathlete.com/mountaineering/fit-to-climb-everest/
- https://www.rmiguides.com/blog/2023/10/22/mountaineering_training_using_benchmarks
- https://www.rmiguides.com/rmi-knowledge-hub/how-to-train-for-mount-rainier/
- https://iantaylortrekking.com/blog/training-for-kilimanjaro-the-best-information/
- https://hiking.biji.co/index.php?q=news&act=info&id=4870
- https://www.sangakusogocenter.com/topics/docs/201406grading.pdf
- https://sp.yamap.com/casio-heartrate/interview01/
- https://www.raceon.com.tw/zh-TW/blogs/news/182126
- https://www.raceon.com.tw/zh-TW/blogs/news/199997

### 1.4 合不合理：逐條對照

| app 的門檻 | 登山來源怎麼說 | 判斷 |
|---|---|---|
| 週量 ≥ 最難那天的 90 %（距離、爬升取低） | 登山沒有這條；週量用**時數**看 | **不適用**。來源是越野跑計畫。而且距離對百岳意義不大：大小霸 65 km 裡有很長的林道，距離被林道灌大，爬升才是難的地方（推估） |
| 週量 < 50 % 判「太難」 | 登山沒有這條 | **不適用**。SP-105 補查已經建議拿掉「超出」，登山來源也沒有支持它的 |
| 長天 ≥ 最難那天コース定数的 80 %、上限 6 小時 | UA：一天爬完攻頂日爬升的 100 %，背攻頂日背包，行程前 8 週起每週一次 | **太寬，而且量錯東西**。見下面的例子 |
| 長天用コース定数比 | コース定数是山本用來估**能量消耗**和路線分級的；長野縣用它分級時是**整條路線**。它沒有被用來當「訓練夠不夠」的比例 | 可以拿來顯示，不適合當門檻（推估）。另外 app 算訓練那次的コース定数時用的是**實際時數**，走得快的人分數反而低 |
| 減量 14 天 | UA：2–3 天的行程一週就夠；Evoke 1–2 週 | 對百岳偏長（§2.4） |
| 恢復週 65 % | UA：最硬那週的 50 % | 不同，影響不大（推估） |

**例子（數字是假設，只用來說明門檻差多少）：** 最難那天 10 小時、25 km、上升 1,500 m、下降 1,200 m。

- コース定数 = 1.8 × 10 + 0.3 × 25 + 10 × 1.5 + 0.6 × 1.2 = 41.2。
- 現在的長天門檻：41.2 × 0.6（6 h ÷ 10 h）× 0.8 = **19.8**。
- 一次 4 小時、12 km、上下 1,000 m、**不背包**的郊山：1.8 × 4 + 0.3 × 12 + 10 × 1 + 0.6 × 1 = 21.4 → 現在判「夠」。
- UA 登山的標準：一天爬 **1,500 m、背攻頂日的包**。上面那次只有 67 % 的爬升，沒背包 → 不夠。
- 時間上做得到嗎（推估）：背 10 % 體重、山本的 430 m/h，1,500 m 約 3.5 小時上，加下山約 5.5–6 小時。所以「長天上限 6 小時」大致可以保留，要改的是**目標**：從「コース定数 × 80 %」改成「最難那天的爬升 × 100 %、帶背包」。

### 1.5 百岳該用的數字

| 指標 | 數字 | 等級 |
|---|---|---|
| 攻頂日模擬（主要） | 行程前 8 週起，每週至少一次：一天爬升 ≥ 最難那天的爬升（100 %），背包 ≈ 攻頂日要背的重量 | UA，已驗證 |
| 每週有氧時數 | 長天 ≈ 每週有氧時間的 50 % → 每週有氧 ≈ 2 × 攻頂日模擬的時數 | UA 的 50 %，已驗證；「反推週時數」是推估 |
| 起始週時數 | 5／7／8–10 小時（依起始體能）；16 週計畫最後 11 小時；困難的健行 10–12 小時以上 | UA，已驗證 |
| 每週爬升 | **沒有登山來源給比例**。只要每週做了攻頂日模擬，週爬升自然 ≥ 最難那天的 100 %（比 90 % 還高） | 推估 |
| ME | 爬升從最難那天的 50 % 開始，背 15–40 % 體重；最後幾次 > 最難那天的爬升和背包 | UA，已驗證 |
| 爬升速度 | 背 10 % 體重、自己的配速爬 1 小時：≥ 430 m/h（無雪期登山）；350–430 m/h 差一點 | 山本 2015，已驗證 |
| 經驗 | B 級路線要先完成 A 級；同一級 2–3 次再升級 | 雪霸國家公園、長野縣，已驗證 |
| 最少週數 | 8 週 | UA，已驗證 |

**跟越野跑門檻的差別：**

- 越野看「週量（距離＋爬升）÷ 比賽」；百岳看「時數」和「單次的攻頂日模擬」。
- 越野的長天打折（80 %、6 小時）；百岳的攻頂日模擬是**爬升 100 %**，但不要求時數一樣。
- 百岳要背包；越野不用。
- 百岳的「高強度」是 ME（背重爬陡坡，腿先酸）；越野是爬坡間歇。
- 百岳多一道「路線分級的經驗」檢查，正好對應 SP-105 的「跨級」構想，但用的是國家公園的 A／B／C 分級，不是 ITRA。

## 2. 第 2 題：百岳的周期怎麼排

### 2.1 各階段（UA 登山）

| 階段 | 長度 | 內容 | 來源 |
|---|---|---|---|
| 轉換期 | 24 週計畫的前 8 週 | 一般有氧＋一般肌力；從前一個周期平均週量的 50 % 開始 | UA 計畫說明、〈Making the Most〉，已驗證 |
| 基礎期 | 計畫的大部分 | 4 天 Z1–Z2 有氧（健行、跑步機坡度、踏箱）；2 天肌力＋核心；背包 4 週（16 堂有氧）後才加 | UA PDF，已驗證 |
| 第一次 Z3 | 24 週計畫的第 13 週，那週約 11 小時、Z3 不到 1 小時 | 強度很少 | UA Podcast，已驗證 |
| ME | 平均在第 16 週（24 週計畫）加入，也就是行程前約 8 週；6–10 次、6–8 週；每週一次 | 背重爬陡坡；前提 AeT 在 AnT 的 10 % 以內 | UA，已驗證 |
| 專項（攻頂日模擬） | 行程前 8 週起，每週一次 | 攻頂日爬升 100 %＋攻頂日背包 | UA，已驗證 |
| 減量 | 2–3 天的行程：1 週 | 量減到最硬那週的 50 %；練的東西像目標行程 | UA，已驗證 |

Steve House〈How to Train for Alpinism〉（2026-08-18，已驗證）把順序寫成：先有氧基礎（最長的一段）→ 一般和最大肌力 → ME → 專項強度和技術。沒有給週數。

**跟越野跑的差別（推估，比較 UA 登山和 app 的越野周期）：**

- 越野的專項期加 Z3／Z4 間歇；登山的專項期是 **ME＋攻頂日模擬**，強度課很少。
- 越野的長天不背包；登山從第 5 週起就背，而且有固定的漸進表。
- 登山的減量比較短（2–3 天的行程 1 週）。
- 專項期長度差不多：UA 的 ME 和攻頂日模擬都從行程前約 8 週開始，和 app 的「賽前第 10–3 週」接近。

### 2.2 連續兩天（B2B）

| 來源 | 說法 | 等級 |
|---|---|---|
| UA trekking | 訓練要讓你「recover well from back-to-back days on the trail」；沒有給課表 | 教練，已驗證 |
| UA Podcast | ME 可以連兩天（週六約 90 分、週日約 2.5 小時） | 教練，已驗證 |
| RMI Rainier | 16 週計畫的第 12、14 週（行程前 4 週和 2 週）排連兩天長健行 | 嚮導公司，已驗證 |
| Ian Taylor（吉力馬札羅） | 背包 600 m 爬升在 1:30–1:40 內完成，「on back to back days」 | 嚮導公司，已驗證 |
| Ainslie 等 2002，*J Appl Physiol*（連續 10 天、每天 21 km、上升 1,160 m） | 年輕組（24 歲）維持水分；年長組（56 歲）逐漸脫水，和跳躍、認知速度下降有關；兩組每天約 21 MJ，體重都維持 | 同儕審查，只有摘要 |
| Vieira 等 2015，*PLoS One*（4 天、262 km，熱） | 姿勢穩定度逐日變差，第 3 天最明顯；原因包括疲勞、水泡（第 2 天起）、睡眠 6.5 小時 | 同儕審查，已驗證 |

- https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:10.1152/japplphysiol.01249.2001&format=json&resultType=core
- https://pmc.ncbi.nlm.nih.gov/articles/PMC4406731/

**和 `back-to-back-and-long-day.md` 不一致的地方：** 那份文件的 B2B 規則（第 2 天 = 第 1 天的 0.6–0.7、3 天版本賽前 4–6 週）主要來自 Koop、CTS、COROS，都是越野跑。登山來源只有 RMI 的「行程前 4 週和 2 週各一次」比較具體，而且 RMI 把最後一次排在行程前 2 週，比 app 的「最晚賽前 3 週」晚。登山來源**沒有**給第 2 天的比例。

### 2.3 背包

| 來源 | 進度 | 上限 |
|---|---|---|
| UA 登山 PDF | 1–4 週 0、5–6 週 5 %、7–8 週 10 %、9–10 週 15 %；體重 < 130 磅的人多加 5 % | 有氧課 20 %；不必練到行程最重的那包；超過會變成在練 ME |
| UA 登山網頁版 | 同上 | 25 % |
| UA trekking | 同上，最後 15–20 % | 20 % 或行程背包 |
| UA 攻頂日模擬 | 背攻頂日的重量（Rainier 約 25 磅） | — |
| UA ME | 15–40 % 體重，或約 50 磅、「腿先酸」的重量 | 最後 > 行程最大那天的背包 |
| RMI Rainier | 10–15 → 20–25 → 30–35 磅，最大的日子接近 40 磅 | — |
| 楊礎豪 | 背實際裝備測試 | 3 天以上 < 體重 30 % |

`loaded-carry-training.md` 已經整理過這部分；這次補到的是 PDF 版的「20 %」和「不必練到行程最重」這兩句原文。

### 2.4 下坡

UA 登山文章對下坡著墨很少：ME 用水壺背水，**到頂倒掉、輕裝下山**，保護膝蓋（UA ME 文章、RMI，已驗證）。也就是說，UA 的 ME 刻意**不**練下坡。下坡的準備要看登山研究：

| 研究 | 發現 | 等級 |
|---|---|---|
| **Maeo, Yamamoto, Kanehisa & Nosaka 2017**，*PLoS One* 12:e0173909 | 背 10 % 體重、−28 % 坡、5 km/h 下坡走 40 分。一週前先做 **5 分鐘**同樣的下坡（本身沒有造成損傷）：力量下降 −9.9 % vs −19.2 %（少 49 %）、CK 少 47 %、痠痛 27.8 vs 81.4 mm（少 66 %）。作者建議長時間下坡前一週內，先做短時間、低強度的下坡走 | 同儕審查，已驗證 |
| Howatson 等 2011，*MSSE*（DOI 10.1249/mss.0b013e3181e4b649） | 登山杖讓上坡 RPE 較低，下山後最大力量下降較少，24–48 小時痠痛較低，24 小時 CK 較低 | 同儕審查，只有摘要 |
| Faulhaber 等 2020，*IJERPH* 17:1115（Tyrol，405 位跌倒受傷的健行者） | 受傷者 56 ± 15 歲；主觀疲勞低，只有 5 % 有肌肉痠痛；70 % 視力有問題 | 同儕審查，已驗證（摘要全文） |
| 同團隊另一篇（搜尋摘要） | 約 60 % 的跌倒發生在下山 | 只有摘要 |

- https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0173909
- https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=Howatson%20trekking%20poles%20mountain&format=json&resultType=core
- https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=PMCID:PMC7036860&format=json&resultType=core

讀法（推估）：
- Maeo 2017 是直接測「背包下坡走」的研究，條件（10 % 體重、−28 %）和百岳很接近。第二作者 Yamamoto 是不是山本正嘉，這次沒有核對。
- 「一週前的短下坡就有保護」對時間不夠的人特別有用：剩 1–2 週也做得到。
- Faulhaber 的受傷者自評不累、沒有痠痛，所以「下坡跌倒 = 腿沒力」這個推論**沒有被支持**；它比較像年齡、視力、地形的問題。

### 2.5 肌力與 ME

| 來源 | 說法 | 等級 |
|---|---|---|
| UA 登山 PDF | 每週 2 次肌力（核心＋下肢漸進）；深蹲從不練到超過 2 倍體重；40 歲以上更要重視肌力 | 教練，已驗證 |
| UA ME | 每週一次、6–10 次、6–8 週、做完 3 天輕鬆；30–100 % 坡；前提 AeT 在 AnT 10 % 內 | 教練，已驗證 |
| Evoke（Johnston，2024-11-02） | 「Typically only one ME workout per week」；ME 通常直接接到減量，做太多「right before your climb」容易過度訓練或受傷；最後兩週停 ME | 教練，已驗證 |
| UA 登山 PDF | 室內：跑步機（比樓梯機好，因為要單腳把整個體重抬上去）、踏箱，箱高約小腿骨的 70–75 % | 教練，已驗證 |
| 山本正嘉（現代ビジネス，約 7,000 位中高年登山者的調查） | 有在**慢跑**的人，多數山上的身體問題比較少；只做健走、爬樓梯、騎車、游泳的人「どのトラブルに対しても効果がついていません」；原因是健走強度不夠、爬樓梯量太少 | 日本，已驗證（文章擷取） |

- https://evokeendurance.com/resources/training-for-mountaineering/
- https://gendai.media/articles/-/131148

### 2.6 有氧基礎

- UA 登山：Z1–Z2 為主，AeT 以 HR 漂移測試定（5 % 判準）；基礎期至少 4 堂有氧／週，「Less than this … you will either stay the same or loose aerobic conditioning」（PDF，已驗證）。這和 app 的 AeT、ADS 規則一致（`uphill-athlete-mountain-metrics.md` §2）。
- 山本：中高年一般登山者 92 % 爬升速度 ≥ 400 m/h（約 7 METs）；他認為比較安全的是 6 METs、300 m/h（YAMAP 專訪，已驗證）。
- Niebauer & Burtscher 2021，*IJERPH*（已驗證）：登山時心臟猝死的人，過去比較少做輕中度運動和山上活動；一年 > 2 週的山上活動，風險少 77 %。建議照 WHO／ESC：每週 3–7 天、共 150–300 分中強度。https://pmc.ncbi.nlm.nih.gov/articles/PMC7915124/
- 高山症：體能不能預防高山症（Wu 2015：11–13 歲到 3,886 m，有沒有高山症的體能指數差不多；Honigman 1995：平地活動量沒差別；只有摘要）。玉山排雲 AMS 36 %（`baiyue-from-running.md` §2.4）。所以練得夠不代表不會高山症，app 不要讓人有這種誤會（推估）。

## 3. 第 3、4 題：課表、減量、時間不夠

### 3.1 一週的結構

| 來源 | 一週 |
|---|---|
| UA 登山 | 6 天：4 次 Z1–Z2 有氧（其中 1 次是長天，約週有氧 50 %）、2 次肌力、1 天全休（多半週一）；ME 期間其中一課換成 ME，做完 3 天輕鬆；行程前 8 週起，長天就是攻頂日模擬 |
| UA trekking | 同上：4 有氧、2 肌力、1 休 |
| 楊礎豪（入門 A 級） | 3 天：平日耐力 30–40 分、平日肌力（膝主導、髖主導，3–4 組 × 20 下）、假日登山或 40–60 分耐力 |
| 山本 | 每週一次 500 m 爬升的登山（月 2,000 m）＋每週 2–3 次深蹲、腹肌 |

套到 app 的限制（平日 40–50 分、週末有空；推估）：

| 星期 | 專項期（行程前 8–2 週） |
|---|---|
| 一 | 休 |
| 二 | 有氧 40–50 分（跑步機坡度或輕鬆跑） |
| 三 | 肌力（下肢＋核心） |
| 四 | 有氧 40–50 分，或 ME（取代一課，做完到週六之間只排輕鬆） |
| 五 | 肌力，或休 |
| 六 | **攻頂日模擬**：最難那天的爬升、攻頂日背包，心率 ≤ AeT（爬陡坡時用走的守住） |
| 日 | 有氧 1–2 小時，下坡多一點（第 2 天的情境） |

- 攻頂日模擬和 ME 都是「重課」，同一週都排的話，ME 要在模擬前至少 3 天（UA 的 3 天輕鬆，推估的排法）。
- 每 4 週一個恢復週，量 50 %，不排模擬、不排 ME（UA）。

### 3.2 減量和行前一週

| 來源 | 減量 |
|---|---|
| UA 登山 | 2–3 天的行程：1 週；量減到最硬那週的 50 %；那週：一次 Z1 課佔週有氧 25 %、一次 Z2 課佔 10 %、其他 Z1 或恢復、2 次一般肌力 |
| UA trekking | 最後 1–2 週減量、保持強度 |
| Evoke | 短的目標（Denali）：1–2 週、全部減約 50 %、最後兩週停 ME；長的遠征：幾乎練到出發，前段行程就是減量 |
| RMI | 第 15 週減約 50 %、強度也降；第 16 週只處理後勤和休息 |
| Ian Taylor | 出發前一週減量 |

建議（推估）：
- 2–3 天的百岳：減量 7–10 天；最後一次攻頂日模擬在行程前 10–14 天；最後兩週停 ME；行程前 7 天內一次 Z1 長一點的課（週有氧 25 %）和一次短的 Z2，加一次短下坡走（Maeo 的 5 分鐘保護量）。
- 4 天以上（B／C 級）：照 UA 的 1–2 週。

### 3.3 時間不夠（剩 4–8 週）

| 來源 | 說法 | 等級 |
|---|---|---|
| UA 登山 | 8 週是最低限度；「If you have 8 weeks, use that」；沒有有效的 2 週、4 週計畫 | 教練，已驗證 |
| UA 8 週入門計畫 | 給 1–3 天的登山目標（Mt Baker、科羅拉多 14ers，約 4,000 m）；Rainier 這級要 12 或 16 週 | 教練，已驗證 |
| UA 16 週計畫 | 推薦給「eight weeks out from a one- or two-day mountaineering objective like Mount Rainier」的人（搜尋摘要） | 只有摘要 |
| UA〈Making the Most〉 | 太累就把週量砍 50 %；一週缺 2 堂以上就重做那週 | 教練，已驗證 |
| Evoke 12 週計畫 | 「a chopped-down version of the 24-week plan … for mountaineers who find themselves with only 12 weeks」 | 教練，已驗證 |
| RMI | 比 16 週短就「compress」各階段 | 嚮導公司，已驗證 |
| 雪霸國家公園、長野縣 | B 級要先完成 A 級；同級 2–3 次再升 | 官方，已驗證 |
| Maeo 2017 | 一週前 5 分鐘下坡就有保護 | 同儕審查，已驗證 |

**沒有任何登山來源說「時間不夠時砍哪一段」。** 綜合起來的建議（推估）：

- 剩 6–8 週：直接進專項。每週一次攻頂日模擬，從現在做得到的爬升開始，每次 ≤ +10 %，背包照 UA 表從 5 % 開始；ME 只給 ADS ≤ 10 % 的人，做 6–8 週的完整版會壓到減量，所以最多 4–6 次。
- 剩 4–5 週：不做 ME（UA 要 6–8 週才有效，Evoke 又要最後兩週停）。每週一次攻頂日模擬，加一次下坡預備；減量 7 天。
- 剩 < 4 週：只減量、下坡預備。顯示「練不出來」，建議**改 A 級路線、多排一天、或延期**，而不是建議「降組別」。

## 4. 第 5 題：給 app 的建議

### 4.1 「來不來得及練」（可行性）

| 指標 | 怎麼算 | 可行 | 吃力 | 超出 | 依據 |
|---|---|---|---|---|---|
| **攻頂日爬升（新的主項，取代週量比）** | 照現有每次 ≤ +10 % 的規則，推算到行程前 2 週，最長一天的爬升能到最難那天爬升的幾 % | ≥ 100 % | 70–100 % | — | UA 攻頂日模擬；70 % 是推估 |
| **剩幾週** | 距離出發 | ≥ 8 週 | 4–8 週 | < 4 週：建議換 A 級、多排一天或延期 | UA 8 週；< 4 週是推估 |
| **經驗（新的，硬性候選）** | 過去爬過的最高分級（使用者勾選，或從活動推估） | 同級，或 A → B | — | 跳級（例如沒有 B 級經驗去 C 級） | 雪霸國家公園分級；長野縣 |
| 週時數 | 推算到行程前 2 週的週時數，對照「2 × 攻頂日模擬的時數」 | 達到 | 不到 | — | UA 50 %；反推是推估 |
| 撤退時間 | 不變 | | | | 你的規則 |
| 週距離 ÷ 最難那天 | **百岳拿掉** | | | | 不是登山來源 |

### 4.2 「現在練得夠不夠」（完備程度）

| 指標 | 怎麼算 | 夠 | 差一點 | 還不夠 | 依據 |
|---|---|---|---|---|---|
| **攻頂日模擬次數（取代コース定数 80 %）** | 行程前 8 週內，一天爬升 ≥ 最難那天的 100 %、有背包（≥ 計畫重量 0.8 倍）的次數 | ≥ 3 次（推估） | 1–2 次 | 0 次 | UA「每週一次，從 8 週前起」；次數是推估 |
| **帶背包的爬升速度** | 最近一次背包課，連續上坡 ≥ 1 小時的段落，用背包 % 換算到山本的表 | 背 10 % ≥ 430 m/h（背 20 % ≥ 395 m/h） | 350–430 m/h | < 350 m/h | 山本 2015 |
| 週時數 | 最近 6 週最多的一週 vs 2 × 模擬時數 | 達到 | 達到 75 %（推估） | — | UA |
| B2B | 最近 10 週做過連兩天、第 1 天有背包 | ≥ 1 次 | 0 次 | — | RMI、UA trekking；1 次是推估 |
| 下坡預備 | 行程前 14 天內有沒有帶背包的下坡（≥ 300 m 下降，推估） | 有 | 沒有 | — | Maeo 2017 |
| 週距離、週爬升 ÷ 最難那天 | **百岳拿掉**，保留給越野 | | | | — |

### 4.3 要和越野跑分開處理的地方

| 項目 | 越野 | 百岳 |
|---|---|---|
| 週量門檻 | UA Big Vert 90–100 %（照舊，SP-105 的 EP 版） | 不用；改看時數和攻頂日模擬 |
| 長天目標 | コース定数 80 %、≤ 6 小時 | 爬升 100 %、帶背包；時間 ≤ 6–7 小時通常做得到 |
| 專項期的質量課 | 爬坡間歇 5 × 4 分 | ME（ADS ≤ 10 % 時），否則一般肌力 |
| 背包 | 不用 | UA 漸進表；有氧課 ≤ 20 % 體重 |
| 減量 | 14 天 | 2–3 天行程 7–10 天；4 天以上 1–2 週 |
| 恢復週 | 65 % | UA 是 50 %（可以不改，差異小） |
| 下坡 | 下坡跑（越野文件） | 背包下坡走；行程前一週內短下坡 |
| 經驗檢查 | ITRA／UTMB 跨級（SP-105 構想） | 國家公園 A／B／C 分級 |
| 爬升速度 | 不看 | 山本的表 |

### 4.4 和現有文件、程式不一致的地方

1. `race-feasibility.md` 和 `race_feasibility.py` 把 UA Big Vert（越野）用在百岳；`week_ok_at` 把多日行程當「長的賽事」套 90 %。
2. `LONG_OK = 0.80` 的來源是圖表帶和江晏慶（越野）；`LONG_CAP_H = 6` 的來源是 iRunFar（越野）。UA 登山是爬升 100 %。
3. `back-to-back-and-long-day.md` 的 B2B 比例和時機來自 Koop、CTS、COROS（越野）。登山來源（RMI）把 B2B 排在行程前 4 週和 2 週。
4. `steep_hill.py` 不背包；UA 登山的攻頂日模擬要背攻頂日的包（UA 的 PDF 也說有氧課不必背到行程最重，所以兩者不完全衝突：有氧課用陡坡代替可以，模擬日要背）。
5. 減量 14 天 vs UA 登山 1 週（`loaded-carry-training.md` §2.5 已經提過，還沒改）。
6. `uphill-athlete-mountain-metrics.md` §2(b) 引的「每週 3–5 %」是聖母峰 6–8 個月的備戰，不適合套到 2–3 天的百岳。

## 5. 要你決定的事

1. **百岳拿掉「週量 ÷ 最難那天」，改用「攻頂日爬升」當主項，可以嗎？** 我建議可以。UA 登山沒有週量比，而且百岳的距離常被林道灌大。
2. **完備程度的長天，改成「攻頂日模擬次數（爬升 100 %、有背包）」？** 「≥ 3 次」是推估（UA 說 8 週前起每週一次，理論上 6 次；我取一半）。你要 3 次，還是照 UA 寫 6 次？
3. **攻頂日模擬要不要背包？** 這和 2026-10-02「app 不排負重課」的決定衝突。我建議：平日的陡坡健走維持不背包（UA PDF 也說有氧課不必背到行程重量），**只有週末的攻頂日模擬背**攻頂日的包。
4. **百岳的減量要不要縮成 7–10 天？** UA 說 2–3 天的行程 1 週就夠。
5. **百岳專項期的質量課要不要從爬坡間歇換成 ME？** 前提是 ADS ≤ 10 %，否則維持一般肌力。
6. **加不加「路線分級經驗」檢查？** 需要使用者在百岳上填國家公園分級（A／B／C／C+），以及自己爬過哪一級。

### 決定（2026-10-05）

0. **範圍：** 越野跑和百岳的訓練分開處理，但只限**多日**百岳。單攻百岳使用者會用越野跑分類，照越野跑處理，這張不用管。
1. 多日百岳拿掉「週量 ÷ 最難那天」，改用攻頂日爬升當主項。
2. 完備程度的長天改成攻頂日模擬次數，**3 次**算夠。
3. 攻頂日模擬**一定要背包**。平日的陡坡健走維持不背。
   - 「攻頂日」是整趟行程裡最硬的**一天**，不是整趟。大小霸 3 天共 3500 m，模擬的是最硬那天的爬升（約 1000 m 上下，推估，要等 GPX 分日點確認），不是 3500 m。
4. 多日百岳的減量縮成 7–10 天（照建議）。
5. 多日百岳專項期的質量課從爬坡間歇換成 ME（照建議；前提 ADS ≤ 10 %，否則維持一般肌力）。
6. **不加**路線分級經驗檢查。

## 6. 沒查到的

- **UA 登山計畫的完整週課表**（8、12、16、24 週）：計畫本身要付費，說明頁沒有逐週內容。Evoke 12 週、24 週計畫頁、TrainingPeaks 頁都沒有細節。
- **《Training for the New Alpinism》《Training for the Uphill Athlete》原書**：沒有讀到。這份文件用的是同兩位作者的 UA PDF 和網頁。
- **UA 的「vertical progression」專文**：沒找到登山版的週爬升進度表。論壇上有使用者自己的 12 週負重爬坡表（800 m → 2,700 m），不是 UA 的規則，沒有採用。
- **UA 登山的下坡專文**：沒有找到。UA 論壇〈Downhill Athlete: pain in quads〉回 404。UA 談下坡跑的文章是越野，沒有採用。
- **山本正嘉「登山の体力は登山で鍛える」「用目標山的幾成難度來練」**：這兩句的原文都**沒查到**。找到的只有「月 2,000 m、分 4 次 500 m」和「同一級 2–3 次再升」（長野縣）。《登山の運動生理学とトレーニング学》原書沒有讀到。
- **山本調查的「4 大トラブル」比例**：現代ビジネス的文章有圖，但文字沒寫數字。
- **台灣官方（國家公園、登山協會）的訓練量建議**：只找到分級和「需完成 A 級」的經驗規則。健行筆記〈我是新手，我想去雪山〉提到「背預期重量（10 公斤以上）在類似地形訓練」，只有搜尋摘要，沒有讀原文。
- **多日健行或登山的訓練介入研究**（例如「做 B2B vs 不做」）：沒找到。
- **登山意外和訓練量的關係**：只有猝死（Niebauer & Burtscher）和跌倒受傷者的描述（Faulhaber），沒有研究比較「訓練多寡 → 有沒有出事」。
- **大小霸每天的實際分段**：這份文件沒有查，例子的數字是假設的。
