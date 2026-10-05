# 爬坡何時改走：坡度與速度的跑走切換點（SP-197）

> 調查日期：2026-10-05。只做調查，沒有改程式，也沒有讀任何人的活動資料。
> 標記：**已驗證**＝這次讀到原文全文、原始網頁或原始碼；**摘要**＝只讀到論文摘要；**推估**＝我的換算或延伸；**未驗證**＝只看到搜尋摘要；**未找到來源**。
> 網頁都經擷取工具摘錄後閱讀，引號內是它回報的原文。配速、坡度百分比、爬升速度（VAM）的換算都是我算的。
> 本文編號用 [W1]、[W2]…。

## 摘要

1. **切換點不是一個坡度，是「坡度 × 速度」。** 每個坡度都有一個切換速度：比它慢，走路比較省；比它快，跑步比較省。坡越陡，切換速度越低 [W1][W2][W4]。
2. **換成爬升速度來看最好懂。** 同一個人爬得越慢，就該在越緩的坡改走。照實驗室的數字換算（推估）：每小時爬 500 m 的人約 8 % 就該走，700 m 約 11 %，900 m 約 15 %，1,400 m 約 28 %（§3.2）。
3. **app 現在的「15 % 走跑皆可、28 % 建議快走」只對爬得很快的人成立**（約 900 和 1,400 m/h）。每小時爬 400–700 m 的人，切換點落在 6–11 %（推估）。
4. **app 有三套互不相干的「算不算走」判斷**（固定坡度標籤、個人多數步態、8 % 以上一律「陡坡（走）」），都只看坡度、不看速度，彼此會矛盾（§1）。
5. **切換點附近選錯步態的代價不大。** 在切換速度上兩種步態成本相等；即使在遠離切換點的陡坡（≥ 15.8°、每小時爬 1,260 m），走路也只省 8.45 % [W5]，所以切換點附近的差距更小（推估）。它應該是一段「走跑皆可」的區間，不是一條硬線。這張單的價值主要在指示一致、能告訴使用者一個自己的數字，對完賽時間的影響預期不大。
6. **沒有找到**用比賽的 GPS 或步頻資料統計「選手在幾 % 改走」的研究。場地研究只確認了方向（坡越陡切換速度越低）和偵測方法（步頻呈雙峰）[W10]。
7. 建議五張實作單（§6），其中四件事要你決定（§7）。

## 1. app 現況（已驗證，讀過程式）

### 1.1 賽事計算機：三套判斷

| # | 判斷 | 門檻 | 用在哪裡 | 程式 |
|---|---|---|---|---|
| A | 固定坡度標籤 | ≥ 15 %「走跑皆可」、≥ 28 %「建議快走」 | 分段的文字標籤，不進時間計算 | `backend/engine/racepower/course.py:35-36`、`course.py:216-223`；出處註解 `course.py:13-17`；寫進備註 `planner.py:597-598` |
| B | 個人多數步態 | 每 2 % 坡度箱，個人的 100 m 視窗有一半以上是走的（該箱 ≥ 10 窗） | 決定這個坡度用走路曲線還是跑步曲線算速度；備註「走（你在這個坡度多半走）」 | `grade_model.py:149`、`grade_model.py:212-214`、`grade_model.py:237-238`、`planner.py:605-606` |
| C | 目標類型 | 坡度 > 8 % 一律標「陡坡（走）」，目標改成心率上限＋VAM | 分段目標、主圖、手錶匯出 | `seg_targets.py:24`、`seg_targets.py:30`、`seg_targets.py:42-50`、`seg_targets.py:93-94` |

- 「走」的定義：步頻 < 130 spm（`workout_review.py:1179`、`racepower/intensity.py:65`）。一個 100 m 視窗有一半以上的移動時間低於這個步頻，就算走路窗（`grade_model.py:278-279`、`grade_model.py:416`）。130 這個數字是推估。
- 步態在算速度之前就決定了，和功率無關：`pacing.py:97-98` 先呼叫 `model.re(g)`（裡面用 B 選曲線），再乘功率得到速度。所以同一個坡度，不管是輕鬆跑還是全力比賽，步態都一樣。
- B 用的是一年內全部訓練的多數步態，不分強度。
- C 的 8 % 來自 Stryd 功率的有效範圍（van Rassel 2026，`seg_targets.py:6-9`），不是跑走研究。
- 補給熱量用 A 判斷走路：≥ 15 % 就改用 Minetti 走路成本，「走跑皆可」也算走（`racepower/fuel.py:164-169`）。
- 越野心率配速模型用 effort km 和心率，不分步態（`racepower/trailhr.py:1-31`，只讀了模組說明）。在驗證通過之前，越野的總時間來自這類整場模型，分段模型只負責分配（`planner.py:8-15`）。

三套會互相矛盾的例子：一段 10 % 的坡，A 沒有任何標籤，C 標「陡坡（走）」，B 看個人資料可能是跑也可能是走。

### 1.2 課表：只有文字，沒有數字

| 課 | 現在怎麼說 | 程式 |
|---|---|---|
| 山路輕鬆跑、長跑 | 「有山路就走山路，陡坡用走的」 | `overview.py:1251`、`projection.py:182`、`plan_prefs.py:419` |
| B2B 第 1 天 | 「爬坡用走的守住上限」 | `b2b.py:600` |
| 長爬坡反覆（專項期，要有賽道 GPX） | 「用比賽的走／跑方式」，沒說是哪一種 | `specific_phase.py:409-410` |
| 陡坡健走（百岳或多日行程前） | 跑步機 12–15 %、3.5 km/h 用走的。坡度是用 Pandolf 模擬背包算出來的，和切換點無關 | `steep_hill.py:39-41`、`steep_hill.py:194` |
| 登山王耐力、長爬坡有氧（範本，不自動排） | 「快走上坡」、「跑走混合」 | `workout_templates.py:388-390`、`workout_templates.py:405` |

- 沒有任何一堂課告訴使用者「幾 % 以上走」或「慢到多少就走」。
- 程式註解記載了一位跑者的實況：≥ 8 %、持續 ≥ 8 分鐘的爬坡幾乎全是走的，跑步比例中位數 1 %（`climb_vam.py:26-32`）。這和 §3.2 的換算一致，但只是一個人。

## 2. 既有研究文件已經有的（引用，不重查）

| 來源 | 說法 | 在哪份文件 |
|---|---|---|
| Giovanelli 等 2016 [W5] | 垂直速度固定 0.35 m/s（1,260 m/h）：9.4°（16.6 %）走和跑差不多；≥ 15.8°（28.3 %）走路平均省 8.45 % | `baiyue-from-running.md` §2.1、`racepower-v2.md` §2.4（摘要） |
| Ortiz 等 2017 [W4] | 30° 坡：0.3–0.7 m/s 走路較省，0.8 m/s 相同 | 同上；這次重讀摘要確認 |
| Minetti 等 1994 [W3] | 人自己改跑的速度，比能量相等的速度低 0.5–0.9 km/h | `baiyue-from-running.md` §2.1；這次重讀摘要確認 |
| Minetti 等 2002 | 走路、跑步的坡度成本多項式，已實作 | `algorithms/minetti.py`、`racepower-v2.md` §2.4 |
| Koop／CTS [W20] | 4–15 % 的坡，跑到每英里 18–19 分或更慢就改快走 | `trail-terrain-and-climb-sessions.md` §2.3；這次重讀原文確認 |
| 既有結論 | 「15–20 % 改走」沒有單一來源；15 %／28 % 只是標籤 | `baiyue-from-running.md` §2.1、`racepower-v2.md` §2.4 |

既有文件缺的是「速度」這一半：只有 30° 一個坡度有切換速度（Ortiz）。這次補上 0–15° 的數字。

## 3. 文獻與資料

### 3.1 實驗室：每個坡度的切換速度

**Brill & Kram 2021 [W1]（已驗證，全文）**。10 名高水準男性越野跑者，跑步機，0°、5°、10°、15°。量三種切換速度：

- 自選（PTS）：逐步加減速 0.1 m/s，走轉跑和跑轉走兩個速度的平均。
- 能量最省（EOTS）：走和跑的代謝功率相等的速度。
- 心率最低（HROTS）：走和跑的心率相等的速度。

| 坡度 | 自選 PTS | 能量最省 EOTS | 心率最低 HROTS | 換算成 VAM（PTS–EOTS） |
|---|---|---|---|---|
| 0°（0 %） | 1.95 m/s（8:33 /km） | 2.14 m/s（7:47 /km） | 2.08 m/s | — |
| 5°（8.7 %） | 1.78 m/s（9:22 /km） | 1.99 m/s（8:23 /km） | 1.92 m/s | 560–620 m/h |
| 10°（17.6 %） | 1.62 m/s（10:17 /km） | 1.78 m/s（9:22 /km） | 1.69 m/s | 1,010–1,110 m/h |
| 15°（26.8 %） | 1.47 m/s（11:20 /km） | 1.51 m/s（11:02 /km） | 1.48 m/s | 1,370–1,410 m/h |

- 每個坡度都一樣：慢的時候走路省，快的時候跑步省；坡越陡，切換速度越低。
- 人自己選的切換速度比能量最省的**低**（0–10° 有顯著差異），也就是人會在「走路其實還比較省」的速度就開始跑。到 15° 兩者沒有差別。
- 作者的結論：「EOTS is not accurately predicted by heart rate」。但從平均值看，心率切換點落在自選和能量最省之間，和能量最省差 0.03–0.09 m/s。
- 作者列的限制：戴著面罩不能用手撐膝蓋（戶外陡坡常見的走法）；只有高水準跑者；15° 有 3 人做不完較快的速度，數字是外推的。

**其他研究（都只讀到摘要）**

| 研究 | 對象與方法 | 結果 | 等級 |
|---|---|---|---|
| Finiel 等 2026 [W2] | 17 名耐力跑者；垂直速度固定 800 m/h，改變坡度與帶速 | 自選切換在 1.76 m/s、7.2° 坡（12.6 %）。能量成本的交叉點和自選切換沒有顯著差異；心率、通氣量的交叉點有顯著差異 | 摘要 |
| Ortiz 等 2017 [W4] | 11 名有經驗的跑者；30°（57.7 %） | 0.3–0.7 m/s 走路較省，0.8 m/s 相同（換算 VAM 1,440 m/h）。「most VK racers should walk rather than run」 | 摘要 |
| Minetti 等 1994 [W3] | 5 人；多個坡度 | 自選切換速度在所有坡度都比能量相等速度低 0.5–0.9 km/h；在自選切換速度，跑一步和走一步的成本一樣 | 摘要（250 字截斷） |
| Rotstein 等 2005 [W6] | 19 名年輕男性，跑者與非跑者；平地 | 自選切換：非跑者 7.23、跑者 7.42 km/h，沒有顯著差異。能量最省：8.02、7.90 km/h。兩者「are not dependent on aerobic capacity or training status」 | 摘要 |
| Whiting 等 2020 [W7] | 30°、1.0 m/s，走和跑比較 | 跑的步頻快 40 %、觸地時間短 40 %；比目魚肌每步的肌電量少 36 %。作者推測：選手在陡坡交替走跑，是在平衡能量效率和比目魚肌疲勞 | 摘要 |
| Baker 等 2025 [W9] | 平地、戶外、800 或 2,400 m，限定時間 | 平均速度 1.9–3.0 m/s 之間，人會用「走跑混合」，比例逐漸變化；這和能量最省的預測一致 | 摘要（預印本，未經同儕審查） |
| Lagos-Hausheer 等 2026 [W8] | 22 名男性跑者；在切換速度附近反覆加減速 | 代謝成本比估計值高 4.7 %，作者認為切換步態本身有額外成本 | 未驗證（搜尋摘要，原文 403） |

這幾篇互相對得上（推估，我的核對）：

- 把 [W1] 的自選切換速度對坡度畫直線，得到 PTS ≈ 1.945 − 0.032 × 坡角（度），四個點的誤差都在 0.005 m/s 內。套到 7.2° 是 1.72 m/s，[W2] 獨立量到 1.76 m/s。
- [W5] 在 9.4° 用 0.35 m/s 垂直速度，帶速是 2.14 m/s，高於該坡度的能量最省切換速度（約 1.8 m/s），所以走路沒有比較省；在 15.8° 帶速 1.29 m/s，低於切換速度（約 1.47 m/s），所以走路省。和 [W1] 一致。
- 15° 之後接到 [W4] 的 30°、0.8 m/s，換算的 VAM 都在 1,370–1,440 m/h 之間。也就是在很陡的坡上，切換點接近一個固定的爬升速度。

### 3.2 換成「你爬多快，就在幾 % 改走」（推估）

同一個爬升速度下，坡越陡、前進速度越慢。把 §3.1 的切換速度換成 VAM，再反過來查：

| 這段坡的爬升速度 | 比這個坡度陡，走路比較省（照自選 PTS） | 照能量最省 EOTS |
|---|---|---|
| 400 m/h | 約 6 % | 約 5.5 % |
| 500 m/h | 約 8 % | 約 7 % |
| 600 m/h | 約 9.5 % | 約 8.5 % |
| 700 m/h | 約 11 % | 約 10 % |
| 800 m/h | 約 13 % | 約 11.5 % |
| 900 m/h | 約 15 % | 約 13.5 % |
| 1,000 m/h | 約 17.5 % | 約 15.5 % |
| 1,200 m/h | 約 22 % | 約 20 % |
| 1,400 m/h | 約 28 % | 約 26.5 % |

- 算法：0–15° 用 [W1] 的四個點直線內插，15–30° 直線接到 [W4] 的 0.8 m/s。**整張表是推估**：受試者是高水準男性跑者、在跑步機上、沒有疲勞、沒有登山杖。
- 讀法：長賽裡每小時爬 500–700 m 的人，8–11 % 以上走路就比較省。每小時爬 1,400 m 是菁英的速度，28 % 才需要走。
- 這也說明了 app 的 15 %／28 % 是怎麼來的：Giovanelli 2016 用的垂直速度是 1,260 m/h，那是垂直公里賽選手的速度。
- 反過來看同一張表：坡度 8 % 的切換配速約 9:17 /km，15 % 約 10:00 /km，25 % 約 11:07 /km（PTS）。

### 3.3 場地：實際上在哪裡改走

| 研究 | 對象與方法 | 結果 | 等級 |
|---|---|---|---|
| Sanchez 2024 [W10] | 14 名越野跑者，戶外上坡，GPS 錶＋慣性感測器 | 自選切換速度和心率最低切換速度都隨坡度下降；步頻呈雙峰分布、步幅是單峰。作者認為步態選擇不只看能量效率 | 摘要（全文 403，沒有拿到各坡度的數字） |
| Zimmermann 等 2022 [W11] | 14 名越野跑者，戶外 375 m、平均 22.3 % 的坡，全力跑一趟、全力走一趟 | 跑比較快到頂（p = 0.009）；最大攝氧量、心率、乳酸沒有顯著差異，通氣量跑的時候較高。受試者自己偏好跑的佔 57 %、走的 36 % | 已驗證（全文） |
| Genitrini 等 2024 [W12] | 9.1 km、爬升 420 m 的比賽，全身慣性感測器 | 用觸地比例 > 50 % 判定走路。走超過 10 % 步數的人被排除，沒有報告在幾 % 改走。後段上坡速度下降、步幅變短、步頻不變 | 已驗證（全文） |
| Genitrini 等 2022 [W13] | 4 條賽道、16 場超馬的分段成績 | 成績好的選手下坡的相對速度較高、**上坡的相對速度較低** | 摘要 |

- **未找到來源**：用比賽的 GPS 或步頻資料統計「選手在幾 % 或多慢時改走」的研究。這次搜尋沒有找到。
- [W11] 提醒一件事：短距離全力衝一段 22 % 的坡，跑比較快，心肺代價差不多。§3.2 的表是「同樣速度下哪個省」，適用在要省力的長時間；短而全力的坡不適用（推估）。
- [W13] 支持「上坡不必硬跑」的方向，但它沒有量步態（推估的解讀）。

### 3.4 教練的說法

| 來源 | 說法 | 等級 |
|---|---|---|
| Koop／CTS [W20] | 「if you are running on any normal climb (4 to 15 percent grade) around 18- to 19-min/mile or slower, it's in your best interest to drop to a power-hike」。理由：同速度下跑的心率較高；走的時候可以順便吃東西 | 已驗證（2025-03-07 更新版） |
| Henninger／Freetrail 2022 [W21]（[W5] 的共同作者） | 坡度 ≥ 15° 建議用走的，尤其是慢於切換速度時；約 2 m/s 以下走路較省。登山杖：6° 以下沒有代謝上的好處，20° 以上自覺強度和垂直成本較低 | 已驗證 |
| Trail Runner Magazine [W22] | 50、100 英里的後段，多數選手 8–10 % 以上就用走的；15 % 左右是短距離賽事走或跑的分界 | 未驗證（搜尋摘要，登入牆） |
| Uphill Athlete | 輕鬆日在陡坡要守住心率，可以改用走的；以走路為主的人，有氧閾值測試用 15 % 坡走路做 | 未驗證（搜尋摘要）；心率上限的做法見 `back-to-back-and-long-day.md` |

Koop 的規則換算是 1.41–1.49 m/s（11:11–11:48 /km）。同樣坡度的實驗室自選切換速度是 1.67–1.87 m/s。也就是說 Koop 的規則比實驗室的跑者**更晚**才改走；照 Koop 做不會走太早（推估）。

### 3.5 走路的能力和登山杖

| 研究 | 結果 | 等級 |
|---|---|---|
| Martinez-Navarro 等 2026 [W14]，36 人，106 km 越野賽 | 上坡走路經濟性能解釋 58 % 的成績差異，但最大攝氧量一項就解釋 79 %。「Uphill walking economy is not an independent performance factor」 | 摘要 |
| Vernillo 等 2016 [W15]，19 人，330 km／爬升 24,000 m | 賽後上坡走路成本 −11.5 %（5 km/h、+20 %），上坡跑步成本 −7.2 %、−7.0 %。沒有對照組 | 摘要 |
| Giovanelli 等 2019 [W16]，14 人，10.1°–38.9° | 用登山杖，垂直成本在 25.4°、29.8°、35.5° 較低；自覺強度在多數坡度較低 | 摘要 |
| Giovanelli 等 2022 [W17]，1.3 km／爬升 433 m 的山徑 | 全力時用杖較快（18:51 對 19:19）；80 % 強度時生理和力學指標都沒有差別 | 摘要 |
| Giovanelli 等 2023 [W18]、2026 [W19] | 用杖時腳的受力較小；戶外全力快 2.5 %；31 km 模擬賽後，不用杖的走路成本高 2.5 %（18.6°） | 摘要 |
| Giovanelli 等 2022 [W23]，6 人 | 同樣的陡坡用杖走，戶外的垂直成本比跑步機高（53.7 對 49.6 J/kg/m） | 摘要 |

- 沒有研究證明「專門練走路」比「一般訓練」更能提升越野成績。支持的只有間接證據：走和跑在陡坡是不同的動作、用的肌肉比例不同 [W7]；走路經濟性和成績有關但不獨立 [W14]；大量走之後走路成本下降 [W15]；教練的專項原則（`trail-terrain-and-climb-sessions.md` §2.1）。
- **未找到來源**：登山杖會把切換點移動多少。方向上，杖讓陡坡的走路更有利（推估）。

## 4. 落差：app 和文獻差在哪裡

| # | app 現在 | 文獻 | 影響 |
|---|---|---|---|
| 1 | 只看坡度 | 看坡度和速度。同一個坡度，快的人跑、慢的人走 [W1][W2][W4] | 標籤對多數人偏晚：15 % 才提示，但爬 500–700 m/h 的人 8–11 % 就該走 |
| 2 | 三套門檻（8 %、15／28 %、個人多數步態） | 一條隨坡度下降的切換速度曲線 | 同一段坡在不同畫面說法不同（§1.1 的例子） |
| 3 | 個人步態不分強度 | 切換由速度決定，所以強度高、速度快時會在更陡的坡才走 | 比賽和輕鬆跑被當成同一種步態；長賽後段變慢、該多走的效果也看不到 |
| 4 | 硬切：一個坡度箱不是走就是跑 | 切換點附近兩種步態成本接近；人會混用 [W1][W9] | 應該有一段「走跑皆可」，而且依速度決定，不是固定的 15–28 % |
| 5 | 課表只寫「陡坡用走的」 | 可以給個人的數字（§3.2） | 使用者不知道多陡算陡 |
| 6 | 長爬坡反覆寫「用比賽的走／跑方式」 | 計算機其實算得出那段坡比賽時該走還是跑 | 資訊在 app 裡但沒接起來 |
| 7 | 補給熱量在 ≥ 15 % 一律用走路成本 | 同第 1 點 | 快的人在 15–25 % 其實在跑，慢的人在 8–15 % 其實在走；熱量各差一些 |

不算落差的：

- 「步頻 < 130 spm 算走」的做法有場地資料支持（步頻雙峰 [W10]；陡坡上跑的步頻比走快 40 % [W7]）。130 這個數字本身仍是推估。
- 輕鬆日用心率上限決定要不要走（`b2b.py:600` 等）和 Koop、Uphill Athlete 一致，不用改。
- 陡坡健走（`steep_hill.py`）是模擬背包，本來就全程走，和切換點無關。

## 5. 結論與建議

### 5.1 切換速度曲線（給所有人的預設值）

| 坡度 | 低於這個速度：走 | 兩者之間：走跑皆可 | 高於這個速度：跑 | 出處 |
|---|---|---|---|---|
| 0–15°（0–26.8 %） | PTS = 1.945 − 0.032 × 坡角（度）m/s | PTS 到 EOTS | EOTS：2.14、1.99、1.78、1.51 m/s（0、5、10、15°），中間直線內插 | [W1] 的實測值；直線和內插是推估 |
| 15–30°（26.8–57.7 %） | 從 15° 的值直線降到 30° 的 0.80 m/s | 幾乎沒有（兩條線重合） | 同左 | [W1]、[W4]；中間沒有實測，推估 |
| > 30° | 垂直速度 0.4 m/s（1,440 m/h）換算的帶速 | — | 同左 | [W4] 的單點外推；[W5] 在 39° 以內走路都較省。推估 |

- 只用在**上坡，坡度 ≥ 3 %**（`seg_targets.py:24` 現有的「爬坡」下限）。平路和下坡不動。3 % 是推估。
- 為什麼用兩條線：低於 PTS 時，「人自己會選」和「能量較省」都指向走；高於 EOTS 都指向跑；中間是人偏好跑、但走略省的區間，差距小 [W1]。
- 這條曲線來自高水準跑者。平地的切換速度和訓練程度無關 [W6]，這點讓它比較能套到一般人；上坡沒有一般人的資料，所以要有 §5.2 的個人校正。

### 5.2 個人校正

| 做法 | 內容 | 依據 |
|---|---|---|
| 個人切換速度 | 每個坡度箱裡，用自己的 100 m 視窗找「一半是跑、一半是走」的速度。該箱走路窗和跑步窗各 ≥ 10 個才算 | 視窗和步態欄位已經有（`grade_model.py:375-448`）；10 沿用 `VMAX_MIN_N`。推估 |
| 一個參數的平移 | 各箱「個人 − 預設」取中位數，當成整條曲線的平移量 Δ；用 n/(n+30) 收縮到 0；限制在 ±0.4 m/s | 和 app 其他個人模型同一個做法（先驗來自文獻，資料只調整它）。30、±0.4 都是推估 |
| 資料不夠時 | 用預設曲線，畫面寫明「預設值，還沒有你的資料」 | — |
| 心率交叉點 | 不拿來決定切換點，最多當參考顯示 | [W1]：心率預測能量最省切換點不準；[W2]：心率交叉點和自選切換有顯著差異 |
| 步頻門檻 | 檢查個人爬坡時的步頻分布是不是雙峰、130 spm 是不是落在谷底 | [W10]；做法是推估 |

個人校正用的是每個人自己的資料，沒有把任何一個人的數字寫死。

### 5.3 計算機怎麼改

1. **一個函式回答「這段是走、走跑皆可、還是跑」**，輸入坡度和預估速度。A、C 和補給都改用它。
   - A 的 15 %／28 % 常數拿掉。
   - C 的 8 % 只保留原本的意思（超過 8 % 不用功率當目標），標籤從「陡坡（走）」改成依函式顯示「陡坡（走）」或「陡坡（跑）」。
2. **時間模型**（`GaitRE.re`）改成依預估速度選曲線：先用跑步曲線算速度，低於切換速度就改用走路曲線。
   - 這樣強度高時自動多跑，長賽後段變慢時自動多走，不用另外加疲勞參數（推估）。
   - 這一步會改預測時間，要過回測：越野的分段誤差不能比現在的「多數步態」差（`backtest.py:259` 已經有 gait／no_gait 的比較）。沒有變好就只改標籤，時間模型維持 B。
3. 預期影響（推估）：切換點附近兩種步態成本只差幾個百分點，所以完賽時間的變化應該小；變化大的是標籤和分段目標的一致性。

### 5.4 課表怎麼改

1. **長爬坡反覆**（專項期）：把「用比賽的走／跑方式」換成計算機的答案，例如「這段坡比賽時預估每小時爬 620 m、坡度 18 %：用快走」。這堂課本來就要有賽道 GPX。
2. **山路輕鬆跑、長跑**：規則不變（心率到上限就走）。另外加一句個人提示：「照你輕鬆心率的爬升速度（約 N m/h），坡度超過約 X % 用走的比較省」。N 來自既有的爬坡 VAM 統計（`climb_vam.py`），X 查 §5.1 的曲線。沒有資料就不顯示數字。
3. **爬坡課要不要依切換點分成「跑的課」和「走的課」**：
   - 計算機已經分了（可跑的坡給功率，陡坡給心率＋VAM），§5.3 讓它分得更準。
   - 訓練課不建議現在新增課種。「專門練走」的證據只有間接的（§3.5）。先把現有的課寫清楚用哪一種步態。
   - 可以考慮的下一步（要你決定，§7）：比賽預估有一半以上的爬坡時間在走時，專項期每週一堂爬坡課明確指定快走（範本「登山王耐力 6×快走上坡」已經有，現在不自動排）。「一半」是推估。

### 5.5 不建議做的

- 不把切換點套到平路。平地慢跑的人照曲線會被叫去走，這不是輕鬆跑的目的。
- 不替登山杖另設曲線：沒有數字（§3.5）。
- 不用心率交叉點當個人切換點（§5.2）。

## 6. 建議開的實作單

| # | 標題 | 驗收條件草案 | 優先度 |
|---|---|---|---|
| 1 | 跑走判斷統一成一個「坡度 × 速度」函式（標籤、分段目標、補給） | ① 一個純函式輸入坡度與速度，回傳走／走跑皆可／跑。② 單元測試重現 [W1] 的 8 個數字、[W2] 的 1.76 m/s@7.2°（誤差 ≤ 0.05 m/s）、[W4] 的 0.8 m/s@30°。③ `course.walk_label`、`seg_targets._walked`、`fuel.run_energy` 都改用它，15／28 % 常數移除。④ 坡度 > 8 % 但判定為跑的分段不再顯示「走」。⑤ 預測時間不變（這張只改標籤）。⑥ 每個常數註明出處或「推估」 | P2 |
| 2 | 課表文字給出步態和個人數字 | ① 長爬坡反覆的說明寫出該段坡比賽時的步態與預估 VAM。② 山路輕鬆跑、長跑在有個人爬坡 VAM 時顯示「約 X % 以上用走的比較省」，沒有資料時不顯示數字。③ 心率上限的規則不變。④ 任何使用者都能算，沒有寫死的個人數值 | P2 |
| 3 | 個人切換速度校正 | ① 從個人的 100 m 視窗算平移量 Δ，n/(n+30) 收縮，限制 ±0.4 m/s。② 「坡度 RE 曲線」區塊顯示預設曲線、個人曲線和各箱樣本數。③ 資料不足時用預設值並標明。④ 單元測試：沒有樣本時等於預設；樣本很多時趨近個人值 | P3 |
| 4 | 計算機的時間模型改用速度選步態 | ① `GaitRE` 依預估速度選走路或跑步曲線。② 越野回測的分段誤差不比現行「多數步態」差，否則不合併、維持現狀。③ 同一條賽道，目標強度提高時走路的分段數不增加（測試）。④ 依賴 #1；有 #3 時用個人曲線 | P3 |
| 5 | 步頻門檻檢查 | ① 診斷頁顯示個人爬坡步頻的分布與 130 spm 的位置。② 不是雙峰或門檻不在谷底時提示。③ 先不自動改門檻 | P4 |

待決定後才開：走路比例高的比賽，專項期自動排一堂快走爬坡課（§5.4 第 3 點）。

## 7. 要你決定的事

1. **「走」的界線用哪一條？** 我建議三段式（低於 PTS 走、PTS–EOTS 走跑皆可、高於 EOTS 跑）。另一個選擇是只用一條線：用 EOTS 會比較早叫人走（能量最省），用 PTS 比較晚（貼近人的習慣）。
2. **輕鬆跑要不要顯示個人的「約 X % 以上用走的」？** 我建議顯示。它是提示，不是規則；規則仍然是心率上限。
3. **走路比例高的比賽，要不要自動排快走爬坡課？** 證據弱，我建議先不排，等 #1、#2 上線後再看。
4. **預設曲線來自高水準男性跑者，一般人直接套用可以嗎？** 我建議可以，但畫面要標「預設值」，並盡快做 #3 的個人校正。

## 8. 限制

- 實驗室的數字都來自跑步機、新鮮狀態、幾分鐘的測試、訓練有素的跑者（[W1] 全是男性）。技術路面、疲勞、背包、登山杖、一般跑者的上坡切換速度都沒有資料。
- §3.2 和 §5.1 的曲線在 15°–30° 之間只靠兩篇不同研究的端點連起來。
- 技術路面可能改變結論：不平路面上走路的成本增加得比跑步多（`racepower-v2.md` §2.4 引 Voloshina 2013／2015，平地資料），方向上對跑步有利，但沒有上坡的研究（推估）。
- 沒有用任何人的活動資料驗算。§5.2 的個人校正、§5.3 的回測都要在實作時用資料確認。
- 這次只讀了 `racepower`、`steep_hill.py`、`specific_phase.py` 的相關段落和各課的說明文字，沒有讀前端。

## 9. 讀不到的來源

- Sanchez 2024 [W10] 全文（commons.nmu.edu 回 403）：只讀到摘要，沒有各坡度的切換速度。
- Lagos-Hausheer 等 2026 [W8]（royalsocietypublishing.org 回 403）：只看到搜尋摘要。
- Finiel 等 2026 [W2] 全文（Springer 要登入）：只讀到摘要。
- Trail Runner Magazine〈When (and How) to Power Hike〉[W22]、Outside〈Yes, Walking Is Sometimes Faster than Running Uphill〉、〈The Ultimate Guide to Uphill Trail Running〉（登入牆）：沒有讀到。
- Genitrini 等 2022 [W13] 全文（PMC 出現驗證碼）：只讀到摘要。
- Minetti 等 1994 [W3] 全文：只讀到截斷的摘要，測了哪些坡度沒有確認。
- Giovanelli 等 2016 [W5]：沿用既有文件的摘要，這次沒有重讀。

## 參考文獻

- [W1] Brill JW, Kram R. Does the preferred walk–run transition speed on steep inclines minimize energetic cost, heart rate or neither? *J Exp Biol* 2021;224(3):jeb233056. https://doi.org/10.1242/jeb.233056
- [W2] Finiel L, Carron S, Margot C, Luc V, Malatesta D, Borrani F. Energetic cost of locomotion closely aligns with the preferred uphill walk-run transition at constant vertical speed in runners. *Eur J Appl Physiol* 2026. https://doi.org/10.1007/s00421-026-06363-x
- [W3] Minetti AE, Ardigò LP, Saibene F. The transition between walking and running in humans: metabolic and mechanical aspects at different gradients. *Acta Physiol Scand* 1994;150:315–323. https://doi.org/10.1111/j.1748-1716.1994.tb09692.x
- [W4] Ortiz ALR, Giovanelli N, Kram R. The metabolic costs of walking and running up a 30-degree incline: implications for vertical kilometer foot races. *Eur J Appl Physiol* 2017. https://doi.org/10.1007/s00421-017-3677-y
- [W5] Giovanelli N, Ortiz AL, Henninger K, Kram R. Energetics of vertical kilometer foot races; is steeper cheaper? *J Appl Physiol* 2016;120:370–375. https://doi.org/10.1152/japplphysiol.00546.2015
- [W6] Rotstein A, Inbar O, Berginsky T, Meckel Y. Preferred transition speed between walking and running: effects of training status. *Med Sci Sports Exerc* 2005. https://doi.org/10.1249/01.mss.0000177217.12977.2f
- [W7] Whiting CS, Allen SP, Brill JW, Kram R. Steep (30°) uphill walking vs. running: COM movements, stride kinematics, and leg muscle excitations. *Eur J Appl Physiol* 2020. https://doi.org/10.1007/s00421-020-04437-y
- [W8] Lagos-Hausheer L, Minetti AE, Pavei G, Bona RL, Biancardi CM. Energetics of human locomotion near the walk–run transition speed. *R Soc Open Sci* 2026;13(6):251967. https://royalsocietypublishing.org/rsos/article/13/6/251967/482230/Energetics-of-human-locomotion-near-the-walk-run
- [W9] Baker NS, Long L, Srinivasan M. Overground gait transitions are not sharp but involve gradually changing walk-run mixtures even over long distances. arXiv:2501.00720（預印本，2025）. https://arxiv.org/abs/2501.00720
- [W10] Sanchez R. Analysing gait transition dynamics in uphill trail running from wearable devices. *ISBS Proceedings Archive* 2024;42(1):104. https://commons.nmu.edu/isbs/vol42/iss1/104/
- [W11] Zimmermann P 等. The energetic costs of uphill locomotion in trail running: physiological consequences due to uphill locomotion pattern—a feasibility study. *Life (Basel)* 2022. https://pmc.ncbi.nlm.nih.gov/articles/PMC9787284/
- [W12] Genitrini M, Fritz J, Stöggl T, Schwameder H. Spatiotemporal parameters and kinematics differ between race stages in trail running—a field study. *Front Sports Act Living* 2024. https://pmc.ncbi.nlm.nih.gov/articles/PMC11228266/
- [W13] Genitrini M, Fritz J, Zimmermann G, Schwameder H. Downhill sections are crucial for performance in trail running ultramarathons—a pacing strategy analysis. *J Funct Morphol Kinesiol* 2022. https://doi.org/10.3390/jfmk7040103
- [W14] Martinez-Navarro I, Vicente-Mampel J, López-Grueso R, Collado-Boira E, Hernando C. Uphill walking economy and maximal oxygen consumption in trail runners: relationship with ultra-trail performance. *Int J Sports Med* 2026. https://doi.org/10.1055/a-2813-3109
- [W15] Vernillo G, Savoldelli A, Skafidas S, 等. An extreme mountain ultra-marathon decreases the cost of uphill walking and running. *Front Physiol* 2016. https://doi.org/10.3389/fphys.2016.00530
- [W16] Giovanelli N, Sulli M, Kram R, Lazzer S. Do poles save energy during steep uphill walking? *Eur J Appl Physiol* 2019. https://doi.org/10.1007/s00421-019-04145-2
- [W17] Giovanelli N, Mari L, Patini A, Lazzer S. Pole walking is faster but not cheaper during steep uphill walking. *Int J Sports Physiol Perform* 2022. https://doi.org/10.1123/ijspp.2021-0274
- [W18] Giovanelli N, Pellegrini B, Bortolan L, Mari L, Schena F, Lazzer S. Do poles really "save the legs" during uphill pole walking at different intensities? *Eur J Appl Physiol* 2023. https://doi.org/10.1007/s00421-023-05254-9
- [W19] Giovanelli N, Mari L, Pellegrini B, 等. The impact of pole use on vertical cost of transport and foot force during uphill treadmill walking before and after a simulated trail running competition. *Eur J Appl Physiol* 2026. https://doi.org/10.1007/s00421-025-05881-4
- [W20] Koop J. Should You Run or Hike That Hill? CTS，2025-03-07 更新. https://trainright.com/run-walk-hill/ （2026-10-05 讀取）
- [W21] Henninger K. Climb the Mountain: When to Run, Hike, or Pole. Freetrail，2022-08-17. https://freetrail.com/climb-the-mountain-when-to-run-hike-or-pole/ （2026-10-05 讀取）
- [W22] When (and How) to Power Hike. *Trail Runner Magazine*. https://www.trailrunnermag.com/training/trail-tips-training/run-faster-by-power-hiking/ （未讀到，登入牆）
- [W23] Giovanelli N, Mari L, Patini A, Lazzer S. Energetics and mechanics of steep treadmill versus overground pole walking: a pilot study. *Int J Sports Physiol Perform* 2022. https://doi.org/10.1123/ijspp.2021-0252
- 既有文件：`baiyue-from-running.md` §2.1、`racepower-v2.md` §2.4、`trail-terrain-and-climb-sessions.md` §2.1 與 §2.3、`back-to-back-and-long-day.md`、`uphill-athlete-mountain-metrics.md` §4。
