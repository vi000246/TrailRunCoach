# 編輯器「插入範本」的課表來源

程式：`backend/engine/workout_templates.py`（`TEMPLATES`）、`backend/engine/workout_steps.py` 的 `templates()`
（把這份清單、間歇庫 `interval_library` 的階梯課表和 app 自己的 CP 測試分到四類）。
測試：`backend/tests/test_workout_templates.py`。它檢查每一份範本都有暖身、出處和網址，而且每一步都用自己的目標，不管整堂的目標依據設成什麼都一樣。

## 1. 分類

| 類型（課表對話框） | 範本分類 |
|---|---|
| 輕鬆跑、長時間 | 輕鬆跑 |
| 強度課 | 強度課 → 三區／四區／五區 |
| 測試 | 測試 |
| 越野跑（舊的「健行／登山」，kind 仍是 `hike`） | 越野跑 |

**三區／四區／五區**用的是 Palladino 的跑步功率區（使用者規則：Palladino 的區間是跑步用的，Coggan／Friel 的功率區是自行車用的）：

- 三區 = 88–101% CP，Palladino 3A＋3B
- 四區 = 101–106% CP，Palladino 4（Supra-threshold）
- 五區 = ≥ 106% CP，Palladino 5 以上

這三段也是編輯器區段圖的第 3／4／5 個顏色。每一份範本放在哪一區，是依它主課的強度在 `Template.sub` 裡指定的。
來源不是用功率寫的課（心率、配速），先換算成約當的 % CP 再分區，所以這部分是推估。

## 2. 目標：每一步都用來源自己的依據

課表對話框沒有整堂的「目標用：自動／心率／功率」了，每一步在結構裡自己決定用什麼目標。範本的每一步都照來源原本寫的依據：

| 依據 | 範本 | 寫法 |
|---|---|---|
| 功率（% CP） | Palladino、Stryd 測試、Rønnestad（原研究是自行車功率，跑步的 % CP 是推估） | `pw(lo, hi)` |
| 心率 | Friel（% LTHR，原生）；Uphill Athlete（≤ AeT，原生）；Pfitzinger、Seiler、Daniels E、徐國峰 E（% HRmax ÷ 0.9 → % LTHR，推估）；Koop、登山王（RPE → % LTHR，推估）；越野跑的課 | `hr(lo, hi)`、`AET` |
| 配速（× 閾值配速） | Daniels T／I／R、Canova、Billat、Pfitzinger 5K 配速、徐國峰 E 配速飄移 | `pace(lo, hi)` |

- 用心率或配速的課，暖身和緩和是 ≤ AeT。
- 全力測試段、衝刺和走路恢復段不設目標。
- 換算的部分都寫在 `conv` 欄位，也標成推估：
  - 閾值配速是 app 的 T 配速（`thresholds.estimate_tpace`）。
  - 馬拉松配速 ≈ T × 1.06，5K ≈ T × 0.93–0.96，I ≈ T × 0.92–0.95，R ≈ T × 0.85–0.89，vVO2max ≈ T × 0.86–0.90，E ≈ T × 1.18–1.29（Friel 2 區配速）。
- 推到 COROS 時，配速段不設目標：COROS 的配速單位還沒驗證，配速會寫在步驟名稱裡，預覽也會標出來。
- 系統排的課（derive 出來、標「自動」的段）還是照 `target_policy` 決定：路跑輕鬆跑和長跑用功率、心率 ≤ AeT 當上限；越野看心率；間歇看功率。

Friel 跑步心率區（% LTHR）：Z1 < 85、Z2 85–89、Z3 90–94、Z4 95–99、Z5a 100–102、Z5b 103–106、Z5c > 106
（[TrainingPeaks](https://www.trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones/)）。

## 3. 範本與出處

### 輕鬆跑

| key | 名稱 | 出處 |
|---|---|---|
| pal_ez | Palladino EZ 輕鬆跑＋3×10″ 加速（24′ ≤ 80% CP、3×10″/1:50、5′） | Steve Palladino，Stryd 訓練計畫（Stryd app 內建課表；使用者 notes 筆記「palladino課表整理/Easy」）· [Stryd Help](https://help.stryd.com/en/articles/7065214-stryd-training-plans-by-steve-palladino) |
| pfitz_recovery | 恢復跑 30′（< 76% HRmax） | Pfitzinger & Douglas《Advanced Marathoning》3rd ed. 2019；[webinar](https://www.slideshare.net/slideshow/marathon-training-webinar/11191347) |
| pfitz_ga | 有氧耐力跑（70–81% HRmax） | 同上 |
| pfitz_long | 長跑（74–84% HRmax） | 同上 |
| daniels_e | Daniels E（65–79% HRmax） | Jack Daniels《Daniels' Running Formula》3rd ed. 2013；[整理](https://www.coachray.nz/2023/05/03/jack-daniels-running-intensity/) |
| xu_e90 | 徐國峰 E 強度 90′（第 10、90 分心率飄移 < 10%） | 徐國峰 [部落格 2015-10-14](http://rocky549.blogspot.com/2015/10/e.html)；《全方位的馬拉松科學化訓練》2015 |
| ua_z2 | 有氧基礎 Z2（AeT −10%～AeT） | Uphill Athlete [zones](https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/) |
| seiler_z1 | 兩極化低強度長課（60–72% HRmax） | Seiler 2010, IJSPP 5(3):276–291 |

### 強度課

| key | 名稱 | 分區 | 出處 |
|---|---|---|---|
| pal_hm_tempo | 半馬功率節奏 2×11′ @ 91–96% CP（3′） | 三區 | Palladino／Stryd（筆記「95% ftp Power Tempo」的 Day 3 截圖） |
| pal_near | 近閾值 3×7′ @ 96–102%（3′） | 三區 | Palladino／Stryd（Day 15 Near-Threshold） |
| daniels_cruise | T 巡航 5×6′／1′ | 三區 | Daniels |
| pfitz_lt | 乳酸閾值節奏 25′ | 三區 | Pfitzinger |
| friel_cruise | 巡航 4×8′／2′（Friel 4–5a 區） | 三區 | Joe Friel《The Triathlete's Training Bible》 |
| koop_tempo | TempoRun 3×12′／6′ | 三區 | Jason Koop《Training Essentials for Ultrarunning》；[TrainRight 2025](https://trainright.com/decoding-ultramarathon-interval-workouts/) |
| canova_specific | 專項 5×3 km（1 km 浮動） | 三區 | Arcelli & Canova《Marathon Training – A Scientific Approach》1999；[書評](https://runningwritings.com/2023/06/canova-marathon-book.html) |
| canova_1k | 10×1000 m／2′ | 三區 | 同上 |
| pal_supra | 超閾值 4×4:30 @ 98–104%（2:45） | 四區 | Palladino／Stryd（Day 1 Supra-Threshold） |
| pal_vo2 | 最大有氧功率 4×2:40 @ 101–106%（2:30） | 四區 | Palladino／Stryd（Day 10 VO2max） |
| seiler_4x8 | 4×8′／2′ | 四區 | Seiler et al. 2013, Scand J Med Sci Sports 23:74–83（[PubMed](https://pubmed.ncbi.nlm.nih.gov/21812820/)） |
| pfitz_vo2 | 5×1000 m（5K 配速，93–98% HRmax） | 四區 | Pfitzinger |
| daniels_i | I 間歇 5×3′／3′ | 五區 | Daniels |
| koop_vo2 | RunningIntervals 6×3′／3′ | 五區 | Koop |
| billat_3030 | 30-30 ×16 | 五區 | Billat et al. 2000, Eur J Appl Physiol 81:188–196（[Springer](https://link.springer.com/article/10.1007/s004210050029)） |
| ronnestad_3015 | 30/15 3×13（組間 3′） | 五區 | Rønnestad et al. 2020（[PubMed](https://pubmed.ncbi.nlm.nih.gov/31977120/)）。原研究是自行車，跑步版是推估 |
| daniels_r | R 8×300 m | 五區 | Daniels |

間歇庫原本的階梯課表（T1–T3、T+、V1–V4、30/15）也列在同一區，標「間歇庫（進階階梯）」，
用來判斷這堂算不算進階。

5 區每趟 < 2 分鐘的課（30-30、30/15）在編輯器裡會出現「5 區每趟至少 2 分鐘（徐國峰）」的錯誤，
儲存時要確認才能存。這是 app 原本就有的規則，沒有改。

### 測試

| key | 名稱 | 出處 |
|---|---|---|
| stryd_cp_3_12 | Stryd 內建 CP 測試 3′＋12′（暖身 20′；按圈開始、≥ 30′ 恢復、按圈結束） | Stryd 內建課表庫（Palladino），使用者 notes「Critical Power測試」〈出自Stryd內建的lib〉 |
| pal_test3 / pal_test10 / pal_test20 | Palladino 3／10／20 分鐘全力 | Palladino／Stryd（筆記「palladino課表整理」3、10、20 分鐘測試） |
| stryd_9_3 | 9′＋3′ CP 測試 | Stryd 9/3 protocol；JSSM 2023 驗證（[PMC10499150](https://pmc.ncbi.nlm.nih.gov/articles/PMC10499150/)） |
| friel_lthr30 | 30′ 獨自全力，LTHR＝後 20′ 平均心率 | Joe Friel，TrainingPeaks〈Quick Guide to Setting Zones〉 |
| ua_aet_drift | AeT 心率飄移 60′（3.5–5% 就是 AeT） | Steve House，Uphill Athlete〈[Heart Rate Drift](https://uphillathlete.com/aerobic-training/heart-rate-drift/)〉 |
| xu_e_drift | 徐國峰 90′ E 配速飄移 | 徐國峰 部落格 2015-10-14 |

「按圈結束」的步驟帶 `est`（這份課表寫的最短時間，例：恢復 ≥ 30′），只用來估總時間。
全力段不設目標：Stryd 原本也把這段留空，讓你自己填「模型功率 −1%～+5%」。

Uphill Athlete 要求用胸帶測，但使用者只有手腕光學心率，所以範本的備註寫「只看趨勢」。

### 越野跑

| key | 名稱 | 出處 |
|---|---|---|
| dsw_classic | 登山王經典 4×7′ 上坡 9 成力、走下 | 江晏慶〈如何提升越野跑的爬升能力－登山王課表〉[Garmin 台灣 2021-01-27](https://www.garmin.com/zh-TW/blog/running/the-climbing-ability-of-trail-running/) |
| dsw_endurance | 登山王耐力型 6×快走上坡、慢跑下 | 同上（耐力變化） |
| ua_hill_sprints | 陡坡衝刺 8×10″／3′ 走 | Uphill Athlete《Training for the Uphill Athlete》2019 |
| koop_uphill | 上坡 TempoRun 3×12′ | Koop（間歇盡量在上坡做） |
| long_climb | 長爬坡有氧 90′（≤ AeT） | Uphill Athlete Zone 2；結構是推估 |
| steep_5／10／15 | 陡坡健走 30′，不背包（坡度約 13／13.5／14.5%、3.5 km/h，心率 ≤ AeT），模擬背 5／10／15% 體重 | Pandolf 1977 同代謝率坡度（[DOI](https://doi.org/10.1152/jappl.1977.43.4.577)）；UA trekking 用跑步機坡度替代背包；`loaded-carry-training.md` §1.1、§3.2。坡度是換算的（推估）；取代原本的負重爬坡（負重課已從排課拿掉） |
| downhill_ecc | 下坡離心預適應 25′（−10～−15%） | Assumpção et al. 2020 Sci Rep（[PMC7606541](https://pmc.ncbi.nlm.nih.gov/articles/PMC7606541/)）；Bontemps et al. 2020 Sports Med（[PMC7674385](https://pmc.ncbi.nlm.nih.gov/articles/PMC7674385/)）；Koop〈[downhill](https://trainright.com/downhill-running-training-go-faster-hurt-less/)〉 |

## 4. Stryd 課表庫能不能直接用

**結論：不能，也沒有接。** 範本只照公開或使用者筆記裡的結構重寫，並寫上出處。

- Stryd Library 有 300 多份功率課表，Palladino 的訓練計畫也在裡面
  （[help](https://help.stryd.com/en/articles/8928220-the-stryd-library)、[2025 更新](https://blog.stryd.com/2025/07/29/explore-the-newly-updated-stryd-workout-library-more-variety-more-workouts-more-power-in-every-run/)）。
- 只有 Stryd 會員能用，入口是 Stryd app 和 PowerCenter（2024-12 起）。網路上沒有列出步驟的公開頁面。
- 沒有官方的公開 API。2021 年客服回覆「沒有開放 API，也沒有計畫」。
- 第三方 client（例：`ethanopp/fitly` 的 `strydAPI.py`）是用使用者自己的帳號密碼打私有端點，違反「不用帳密」的原則，也有違反服務條款的風險。
- [ToS](https://club.stryd.com/tos) 是沒填完的範本，只寫了內容版權歸公司。所以這邊不複製 Stryd 的課表文字，只重寫結構並附出處。

本文件用到的 Stryd／Palladino 課表，都來自使用者自己的 notes 筆記（Stryd app 的截圖和抄錄），
以及 Stryd 公開的 help 頁面。

## 5. 總時間怎麼估（`workout_steps.speed_kmh`、`_secs`）

- **全部是時間段**：總時間就是各段加總。課表對話框的「分鐘」欄位鎖住、由步驟算出來；點這個欄位會打開結構。
- **有距離段**：用你自己的兩個速度當錨點，依該段目標強度（≈ % CP；心率段用 Friel→Palladino 換成 % CP）在兩點之間線性內插。
  兩個錨點是：
  - 輕鬆路跑速度（`equivalence` 的 `v_flat`：近期 ≤ AeT 的輕鬆路跑速度中位數），對到 78% CP。
  - 閾值配速（`thresholds.estimate_tpace`），對到 100% CP。

  平路上跑步功率大約跟速度成正比（Stryd），所以用線性內插。只有一個錨點時就照比例縮放；兩個都沒有時用 6:00/km。這一段的換算都是推估。
- **越野跑**：先算努力距離 EP = km × (1 + 爬升 m/km ÷ 100)，再用你的越野 EP 速度
  （`equivalence` 的 trail `ep_kmh`）乘上同樣的強度比例。爬升比例取同負荷面板設定的值，沒有就用目標賽事的。
- **按圈段**：有 `est` 就照 `est` 算，沒有就不算進總時間（顯示「＋按圈」）。
- 只要有估的部分，「分鐘」欄就顯示「約（分鐘）」，旁邊的 ? 會說明怎麼估的。
  原本同負荷面板那行「努力距離 EP …；你的登山紀錄不夠，還不能回測」也移到這個 ? 裡。
