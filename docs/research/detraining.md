# 停訓與恢復訓練：停多久會掉多少、怎麼回來、app 怎麼排

- 日期：2026-10-01
- 問題：長假或中斷後要不要「重新練」？Z5（和 Z3）的暫停門檻（目前是佔位值：連續 ≥ 14 天沒跑、Z1 週量 < 確認時的 70 % 持續 2 週、長跑飄移變差）該怎麼定？中斷後的課表長什麼樣子？
- 對象：休閒越野跑者兼百岳登山者，Stryd + COROS，平日 40–50 分、週末越野。
- 標記：
  - **[同儕審查]**：期刊論文，數字取自摘要（Europe PMC）；沒讀全文的都是摘要層級。
  - **[教練]**：書、教練文章、研討會；不是同儕審查。
  - **[你的筆記]**：notes `300 Sport`，以 `路徑:行號` 引用。
  - **推估**：我們自己的數字或推論，沒有外部來源。
  - **未驗證**：查不到原文、只有二手轉述。
- 相關文件：`aerobic-base-readiness.md`（有氧基礎確認）、`interval-adaptation.md`、`docs/spec/plan-auto.spec.md`。

---

## 0. 結論先講

1. **兩週以內掉得很少，掉的主要是「血量／次最大心率」，不是 VO2max。**
   - 跑者停 14 天：VO2max −4 %、力竭時間 −9 %、同速心率 +11 bpm、血漿量 −5 %、檸檬酸合成酶 −25 %，跑步經濟性不變（Houmard 1992）。
   - 停 2–4 週：血量 −9 %、心搏量 −12 %、VO2max −6 %；把血量補回去，心搏量與 VO2max 就回到訓練時的 2–4 % 內（Coyle 1986）。
2. **3–8 週開始傷到有氧基礎本身。** VO2max 21 天 −7 %、56 天後穩定在 −16 %；粒線體酵素半衰期約 12 天，56 天約 −40 %（Coyle 1984、1985）。但多年訓練的人，84 天後酵素仍比久坐者高 50 %、乳酸閾值仍在 75 % VO2max（久坐者 62 %），微血管密度沒掉。**剛練起來（幾個月內）的進步，停 > 4 週會全部歸零**（Mujika & Padilla 2000）。
3. **減量不等於停訓。強度留著，量和頻率可以大砍。**
   - 頻率 6 → 2 天／週、持續 15 週，VO2max 維持（Hickson 1981）。
   - 每次 40 → 13 分，VO2max 維持，但 2 小時以上的長耐力 −10 %（Hickson 1982）。
   - 強度降 1/3，VO2max 就守不住，長耐力 −21 %（Hickson 1985）。
   - 一週只跑一次 35 分高強度、4 週：VO2max 不變，但 75 % VO2max 的力竭時間 −21 %（Madsen 1993）。
   - **所以「量掉了」傷的是長耐力和飄移，也就是 Z5 門檻想保護的那個有氧基礎，不是 VO2max。**
4. **回來的速度：** 至少不比第一次慢。一位多年訓練的鐵人停 12 週、再練 12 週，VO2max 回到原點，但跑步經濟性沒回來（Lepers 2024，n = 1）。肌肉的「耐力記憶」在轉錄層級找不到證據（Lindholm 2016，停 9 個月）。教練經驗是「成熟度」會留 12–18 個月（WKO5 研討會，§4.6）。
5. **最好用的復跑表是 Daniels 表 9.2（你的筆記裡有）：恢復期長度＝停訓長度。** ≤ 5 天照常；6–28 天前半 50 %、後半 75 %；4–8 週分三段 33／50／75 %；> 8 週每 3 週一階 33→50→70→85→100 %。配 VDOT 折減表（14 天 ×0.973、28 天 ×0.931；中斷期間有交叉訓練則 ×0.986、×0.965）。
6. **給 app 的建議（§6）：**
   - 「連續 14 天沒跑」改成**「連續 ≥ 6 天沒跑 → 進入恢復期」**（Daniels 第 2 類的起點）。Z5 在恢復期內一律暫停，恢復期長度＝中斷天數。
   - 「Z1 週量 < 70 % 持續 2 週」改成**「Z1 週量 < 2/3 持續 3 週（不算恢復週／減量週）」**，依據是 Hickson 1982：量砍 1/3 仍守得住長耐力，砍 2/3 守不住。
   - **長跑飄移變差**留著，而且它最敏感：停訓最早出現的就是同速心率上升。
   - 中斷 ≥ 4 週：恢復期結束後要**重新確認有氧基礎**（三種測試選一：90 分飄移、UA 差距、Friel 飄移），並重測 CP。
7. **app 現有的 `blackouts.step_cap()` 跟 Daniels 衝突**：整週不排課、實際 0 h，下週上限只有 0.5 h，之後每週 +10 %，要好幾週才回得去。Daniels 是 50 %→75 %→100 %（§6.6）。

---

## 1. 停訓的時間軸（同儕審查）

### 1.1 依時間點整理

| 停多久 | VO2max | 血量／心搏量 | 粒線體／氧化酵素 | 乳酸閾值 | 跑步經濟性 | 出處 |
|---|---|---|---|---|---|---|
| 10 天內 | （未見顯著） | — | — | — | — | 代謝面：運動中呼吸交換率上升、更依賴醣類，「可能在停訓 10 天內發生」（Mujika & Padilla 2001 MSSE 33:413） |
| 12 天 | — | — | 半衰期約 12 天（CS、SDH） | — | — | Coyle 1984 |
| 14 天 | −4 %（61.6 → 58.7 ml/kg/min） | 血漿量 −5.1 %；同速心率 +11 bpm；HRmax +9 | CS −25.3 % | — | **不變**（75 %、90 % VO2max） | Houmard 1992，12 名長跑者 |
| 14 天 | — | — | CS 41.0 → 30.6（−25 %） | — | — | Houmard 1993，耐力組 n = 12 |
| 2–4 週 | −6 % | 血量 −9 %、血漿量 −12 %、心搏量 −12 %；次最大心率 +11 % | — | — | — | Coyle 1986，8 名耐力訓練者 |
| 21 天 | −7 % | 心搏量下降（初期 VO2max 下降主要來自這裡） | — | — | — | Coyle 1984，n = 7 |
| 56 天 | 穩定在 −16 % | 心搏量已降到與對照組無差 | 約 −40 %（56 天內） | 同強度的心率、通氣、乳酸在前 56 天逐步上升，之後穩定 | — | Coyle 1984、1985 |
| 84 天 | 仍高於從未訓練者（50.8 vs 43.3） | — | 仍比久坐者高 50 %；微血管密度沒掉，仍高 50 % | 75 ± 2 % VO2max（對照 62 ± 3 %） | — | Coyle 1984、1985 |
| 12 週 | 騎車 −9.1 %、跑步 −10.9 % | — | — | — | 能量消耗 **+22 %**（變差） | Lepers 2024，53 歲鐵人，n = 1 |

### 1.2 機制的順序

- **前 2–4 週：中樞（血量 → 心搏量）。** Coyle 1986 把血量用 dextran 補回去，心搏量與 VO2max 就回到訓練時的 2–4 % 內。初期的下降大多是血量造成的。
- **超過 2–4 週：周邊（動靜脈氧差、氧化酵素）。** Coyle 1984：「The initial decline in VO2 max was related to a reduced SV and the later decline to a reduced a-vO2 difference.」Neufer 1989 的綜論也是同樣分段：2–4 週內 VO2max 已顯著下降，之後的下降來自動靜脈氧差。
- 乳酸閾值的一部分很耐久：Coyle 1985 說「persists for a long time (greater than 85 days)」。

### 1.3 已訓練者 vs 剛練起來的人

- Mujika & Padilla 2000（Part II，> 4 週）：「VO2max of athletes declines markedly but remains above control values during long term detraining, whereas recently acquired VO2max gains are completely lost.」
- 肌肉層級：微血管、動靜脈氧差、氧化酵素在運動員身上下降，「are completely reversed in recently trained individuals」。
- Part I（< 4 週）：這些變化在剛練起來的人身上比較溫和。
- **對這位選手的意義（推估）：** 他練了幾年，有一定的「地板」。但最近幾個月才建的有氧基礎（也就是 Z5 門檻剛確認的那一段），停 > 4 週要當作沒了。

### 1.4 更新的綜論

- **Barbieri et al. 2023**（Front Physiol，耐力運動員停訓綜論）：結論與 Mujika 相同。VO2max 隨血量下降、乳酸閾值下降、心室變小；部分減量可以減輕損失。也寫明「There is a dearth of data」。
- **Zhang et al. 2026**（Front Physiol，86 篇 RCT 的統合分析，多數是一般族群與健康結果）：停訓 ≤ 2 個月、2–6 個月、> 6 個月，VO2max 都仍顯著高於訓練前。這是「相對未訓練時」的比較，**不是**相對訓練後的比較，不能拿來說沒有退步。
- 查無專門針對「耐力運動員 VO2max 停訓時間曲線」的統合分析（Europe PMC 2015–2026，檢索詞：detraining／training cessation × meta-analysis／systematic review × VO2max）。

---

## 2. 減量 vs 完全停（同儕審查）

| 研究 | 設計 | 結果 |
|---|---|---|
| Hickson & Rosenkoetter 1981, MSSE 13:13 | 10 週、每天 40 分、每週 6 天，之後改 4 或 2 天／週，強度與時長不變，15 週 | VO2max 兩組都維持在訓練後水準 |
| Hickson et al. 1982, J Appl Physiol 53:225 | 每次 40 分 → 26 或 13 分，強度與頻率不變，15 週 | VO2max 與約 5 分鐘的短耐力兩組都維持；**2 小時以上的長耐力：26 分組維持，13 分組 −10 %（139 → 123 分）** |
| Hickson et al. 1985, J Appl Physiol 58:492 | 強度降 1/3 或 2/3，頻率與時長不變，15 週 | VO2max 守不住（但仍高於訓練前）；長耐力 −21 %（1/3 組）、−30 %（2/3 組）；左心室質量回到原點 |
| Madsen et al. 1993, J Appl Physiol 75:1444 | 9 名耐力運動員，4 週只做每週一次 35 分高強度（原本 6–10 h／週） | VO2max 不變（4.57 vs 4.54 L/min）；75 % VO2max 力竭時間 −21 %（79 → 62 分） |
| Houmard et al. 1996, J Appl Physiol 81:1162 | 頻率減半 2 週 vs 完全停 2 週 | 減半：胰島素敏感度與 GLUT-4 維持；全停：回到久坐水準（代謝指標，非表現） |
| Spiering, Mujika et al. 2021, JSCR 35:1449（敘述性綜論） | — | 一般族群：頻率減到每週 2 次，或量減 33–66 %（每次 13–26 分），只要強度（運動心率）維持，耐力表現可維持到 15 週。「exercise intensity seems to be the key variable」。運動員資料不足 |
| Mujika & Padilla 2000, Part II | 綜論 | 「as long as training intensity is maintained and frequency reduced only moderately. On the other hand, training volume can be markedly reduced」；交叉訓練有用，運動員要用相近的運動型態，中等訓練者用不同型態也有效 |
| Bosquet et al. 2007, MSSE 39:1358（減量期統合分析，27 篇） | — | 最佳減量期：2 週、量指數遞減 41–60 %，強度與頻率不變 |

**重點：** VO2max 對「量」很不敏感，對「強度」很敏感。長耐力（≥ 2 h、75 % VO2max 的持續時間）則對量敏感。Hickson 1982 的 13 分組（量剩 1/3）和 Madsen（量剩約 10 %）都掉了長耐力。Hickson 的 26 分組（量剩 2/3）沒掉。

這直接對應到 Z5 門檻想保護的東西：90 分鐘 Z1 測試、每週 Z1 時間、長跑後段不飄，都是**長耐力**指標。

---

## 3. 回升（retraining）

| 來源 | 內容 | 層級 |
|---|---|---|
| Lepers et al. 2024, Front Physiol 15:1508642 | 53 歲多年鐵人：停 12 週後 VO2max −9 至 −11 %；12 週漸進結構化訓練後幾乎全部回到原點，甚至略高。**跑步經濟性與瘦體重沒回來** | [同儕審查] 個案 n = 1 |
| Joo 2018, PLoS One 13:e0196212 | 半職業足球員停 2 週，Yo-Yo IR2 顯著下降；再練 2 週回到與沒停組無差 | [同儕審查] 足球，非長跑 |
| Lindholm et al. 2016, PLoS Genet 12:e1006294 | 單腿訓練 3 個月，停 9 個月後，練過的腿和沒練過的腿轉錄體無差；「no coherent evidence of an endurance training induced transcriptional skeletal muscle memory」 | [同儕審查] 分子層級；不代表表現層級沒有記憶 |
| Coyle 1984 | 多年訓練者停 84 天，微血管與酵素仍高於久坐者 | 「地板」是回得快的合理解釋（推論） |
| WKO5 研討會（Elements of WKO5 Training Load） | 「成熟度來自於你的訓練歷史，不會那麼快消失」；「關鍵不是你目前的 CTL 降到多低，而是你是否真正痊癒、準備好重新開始訓練」 | [教練]，你的筆記 `65 ⚡ 功率訓練/研討會整理/研討會 Elements of WKO5 Training Load.md:439-449` |
| WKO5 研討會（基礎期） | 「通常你的『訓練成熟度』會維持 12 到 18 個月」；停更久則「回退到『1~3 年前的訓練水準』」，不會讓他完全重頭開始 | [教練]，`70 ⏳ 周期化訓練/wko5研討會 基礎期.md:154-162` |
| WKO5 研討會（Training Load） | 年紀大的人「休息不能太長，一旦恢復期拉太長，重新建立 fitness 的代價就會變得太大」 | [教練]，`…/研討會 Elements of WKO5 Training Load.md:305-306` |
| WKO5 研討會（強化期） | 「也因為剛恢復訓練，運動表現通常會先下降」：重新加量的疲勞會先壓低表現 | [教練]，`70 ⏳ 周期化訓練/wko5研討會 強化期.md:227-228` |

**「再練比第一次快」的證據：** 表現層級只有個案與教練經驗支持（Lepers 2024、WKO5）。分子層級反而否定（Lindholm 2016）。合理的說法是：**多年累積的結構（微血管、部分酵素、心臟、肌腱骨骼適應）留下來了，所以回得快；最近幾個月才加上去的那一層沒有特權。** 這是推論，沒有直接比較「首次 vs 再次」速度的耐力研究（未驗證：Europe PMC 只查到上述 retraining 研究）。

---

## 4. 復跑規範（教練與運動醫學）

### 4.1 Daniels 表 9.2：停練一段時間之後的訓練量調整 [教練]

來源：你的筆記 `60 🏃 有氧訓練/丹尼爾的跑步方程式筆記 還有課表.md:123-124`（「停練後回來訓練的規劃」，附圖 `assets/丹尼爾的跑步方程式筆記-20250315213400605.jpg`，中文版表 9.2）。下表照圖轉錄：

| 類別 | 休息期長度 | 重回訓練後的調整原則 | 前跑力值 % |
|---|---|---|---|
| 1 | 5 天以內 | 5 天的 E 日：不超過之前訓練量的 100 % | 100 % |
| 2 | 6–28 天 | 前 ½ 訓練期：之前量的 50 %；後 ½：75 % | 93.1–99.7 % 或 96.5–99.8 % |
| | 例：6 天 | 前 3 天 50 %；後 3 天 75 % | 99.7–99.8 % |
| | 例：28 天 | 前 14 天 50 %；後 14 天 75 % | 93.1–96.5 % |
| 3 | 4–8 週 | 前 ⅓ 的 E 日 33 %、中 ⅓ 50 %、後 ⅓ 75 % | 84.7–93.1 % 或 92.3–96.5 % |
| | 例：29 天 | 9 天 33 % + 10 天 50 % + 10 天 75 % + 一些快步跑 | 93.0–96.4 % |
| | 例：8 週 | 18 天 33 % + 19 天 50 % + 19 天 75 % + 一些快步跑 | 84.7–92.3 % |
| 4 | 8 週以上 | 3 週 E 日 33 %（≤ 48 km／週）→ 3 週 50 %（≤ 64 km）→ 3 週 70 % + 快步跑（≤ 96 km）→ 3 週 85 % + 快步跑 + R（≤ 120 km）→ 3 週 100 % + 快步跑 + T + R（≤ 144 km） | 80.0–84.7 % 或 90.0–92.3 % |

讀表（後兩點是我們的推論）：
- **恢復期長度＝休息期長度**（第 2、3 類）：6 天 → 3 + 3 天，28 天 → 14 + 14 天，8 週 → 18 + 19 + 19 天。
- 第 2 類只寫 E 日，第 3 類加「一些快步跑」，T 強度（閾值）到第 4 類最後一階才出現。**推論：Daniels 的恢復期內沒有 Q 課（T、I），結束後才照折減後的 VDOT 回到 Q 課。**
- 「之前訓練量」書上沒寫怎麼算；推估用中斷前 4 週的平均週量。

### 4.2 Daniels VDOT 折減（表 9.1）[教練]

表 9.1 不在你的筆記裡。下表取自 VDOT O2 官方部落格「VDOT Adjustments For Time Off From Running」（2018-02）。FVDOT-1：中斷期間沒有交叉訓練；FVDOT-2：有交叉訓練。

| 中斷 | FVDOT-1 | FVDOT-2 |
|---|---|---|
| ≤ 5 天 | 1.000 | 1.000 |
| 6 天 | 0.997 | 0.998 |
| 7 天 | 0.994 | 0.997 |
| 10 天 | 0.985 | 0.992 |
| 14 天 | 0.973 | 0.986 |
| 21 天 | 0.952 | 0.976 |
| 28 天 | 0.931 | 0.965 |
| 35 天 | 0.910 | 0.955 |
| 42 天 | 0.889 | 0.994 ← 網頁原文如此，前後是 0.955 / 0.934，應是 0.944 的筆誤（未驗證，要對書） |
| 49 天 | 0.868 | 0.934 |
| 56 天 | 0.847 | 0.923 |
| 63 天 | 0.826 | 0.913 |
| 70 天 | 0.805 | 0.902 |
| ≥ 72 天 | 0.800 | 0.900 |

- 與表 9.2 的「前跑力值 %」區間吻合（6 天 99.7–99.8、28 天 93.1–96.5、8 週 84.7–92.3），兩份來源一致。
- 跟研究對照：14 天 ×0.973（−2.7 %）與 Houmard 1992 的 VO2max −4 % 同一量級；56 天 ×0.847（−15 %）與 Coyle 1984 的 −16 % 很接近。
- 「交叉訓練」要多少才算 FVDOT-2，網頁沒有定義（未驗證，需查原書）。
- 原文用法：「If you haven't engaged in cross-training, multiply the FVDOT-1 value … If you've done cross-training while away from running, multiply FVDOT-2.」

### 4.3 Friel：Missed Workouts（2010-05-11）[教練]

joefrieltraining.com/missed-workouts/：
- **≤ 3 天：**「Return to training as if nothing happened. Don't try to make up the missed workouts.」
- **4–7 天：** 把這段當作恢復週，目前的訓練區塊少一週（或巔峰期從兩週縮成一週），重排 2–3 堂關鍵課。
- **1–2 週：** 生病且在 Build 期 → 從 Base 3 重新開始；在 Base 期 → 回 Base 1 或 Prep，直到心率與自覺強度和配速、功率對得上。
- **> 2 週：** Build 期 → 回 Base 3；Base 期 → 退一個區塊。
- Friel 談 COVID 的文章（2021-12-21）沒有規則，只說「exercise conservatively and cautiously」，並彙整選手經驗：先 Z1–Z2、先加量再加強度。他自己也說「I wish I had an answer」。

### 4.4 TrainingPeaks（Lance Watson，LifeSport 總教練）[教練]

「How to Get Sick Like a Pro」：
- **頸部檢查：** 症狀在脖子以上（喉嚨痛、流鼻水），通常可以降強度練；在脖子以下（咳嗽、肌肉痠痛、發燒）就停。
- 無症狀 24–48 小時後再開始，「Initial training after that should be easy, aerobic, and short in duration」。
- **2–5 天：**「Your fitness has not significantly changed」。**6–14 天：**「Hit rewind for one to two weeks prior to the onset of the illness」。
- 間歇先「taking them down a training zone」，恢復段稍微動態一點。

### 4.5 TrainerRoad 部落格（無署名）[教練]

- 長假（2–4 週）：退回課表中和中斷一樣長的位置（例：第 5 週停 2 週 → 從第 3 週開始）。
- 生病：前 1–2 週保守。
- 跟 Daniels 的「恢復期＝中斷長度」是同一個思路。

### 4.6 Uphill Athlete [教練]

- 「Aerobic Self-Assessment for Mountain Athletes」：「A clean Heart Rate Drift Test or lab test is still the right way to establish your first baseline, and the right tool to reach for when you return from a layoff or illness and want a fresh, deliberate read.」
  → **中斷後用飄移測試重新讀一次 AeT／有氧基礎**，有 UA 的來源。
- 「Zone 2 Heart Rate Training」：每 6–10 週重測一次；這條針對進步，不是針對中斷。
- 沒找到 UA 對復跑起始量、加量速度的具體數字（未驗證）。

### 4.7 Palladino、WKO5 [教練／你的筆記]

- Palladino 基礎期：「初階選手或長期休息者：3‑6 週，最多 2‑3 個月；有經驗或具備基礎選手：1‑3 週即可」；CTL 每週 +1–3 TSS/day（約 2–5 %），「避免用過時的『10% 升量法則』」。`70 ⏳ 周期化訓練/palladino基礎期訓練.md:48-54`
- WKO5（Training and Coaching part 1）：基準測試「也適用於運動員因傷休息或長時間沒有訓練的情況，例如長達兩個月的訓練中斷後重新開始」。`65 ⚡ 功率訓練/研討會整理/研討會 Training and Coaching with WKO5 part 1.md:169-170`
  → **中斷 ≈ 2 個月要重做 CP／功率曲線基準。**
- WKO5（巔峰期）：無氧功率「是退化最慢的一個項目。你甚至可以在休息四週後，輕鬆地輸出一個不錯的 45 秒功率」。`70 ⏳ 周期化訓練/wko研討會 巔峰期.md:533-537`
  → 中斷後短段功率看起來沒掉，**不代表有氧基礎沒掉**。不能拿短段 MMP 判斷能不能開 Z5。
- WKO5（巔峰期）：傷後回來的選手「有很大的心肺與有氧系統能力，但就是缺少高輸出能力」。`…/wko研討會 巔峰期.md:443-459`（只有強度受限、量沒停的情形）。

### 4.8 Pfitzinger

沒查到 Pfitzinger 本人針對「中斷多久、回到多少」的一手規則。網路上流傳的「50–75 % 起步」「停 3 週要 2 週回來」都是二手整理（marathonhandbook、runnersconnect），沒附 Pfitzinger 原文。**未驗證，不採用。**

### 4.9 徐國峰與台灣教練

**沒有找到徐國峰對停訓或復跑的公開說法**（Daniels 表是中文版書頁）。可以用的是 app 現有的規則：
- 有氧基礎：90 分鐘 Z1 測試（徐國峰部落格 2016-12）；每週 Z1 時間（台灣教練）。
- 入門轉進階先 Z3，穩定、恢復正常後再 Z5；Z5 每週最多 2 次、間隔至少 2 天（台灣教練）。

這些直接當「中斷後重新確認有氧基礎」與「Z3 → Z5 的回歸順序」的來源。

### 4.10 生病後回歸（運動醫學）

| 來源 | 內容 | 層級 |
|---|---|---|
| Elliott et al. 2020, BJSM 54:1174（COVID 漸進回歸資訊圖） | 開始前：日常活動做得到、平地走 500 m 不會過度疲勞或喘；「at least 10 days' rest and be 7 days symptom-free before starting」；過程中任何症狀（含過度疲勞）就退回前一階，至少 24 小時無症狀再往上。監測：安靜心率、RPE、睡眠／壓力／疲勞／痠痛 | [同儕審查期刊的指引]；各階段的時長與心率百分比在圖檔裡，**PMC 文字版沒有，未驗證** |
| Snyders et al. 2022, BJSM 56:223（IOC 共識小組的系統性回顧） | 急性呼吸道疾病：80 % 沒有損失訓練日；平均症狀 7.1 天；回到運動 0–8.5 天 | [同儕審查] |
| IOC 共識 2022 Part 1（感染性）、Part 2（非感染性），Schwellnus et al., BJSM | 都有 return-to-sport 章節 | 具體分階內容**未讀全文、未驗證** |

**健康長假 vs 生病／受傷：**
- 長假：身體沒有額外的發炎或組織損傷，損失只有停訓本身 → 照 Daniels 表、照中斷天數走。
- 生病：在停訓之外還有發炎、心肌風險（發燒、脖子以下的症狀、COVID）。起點是**無症狀**，不是「病好了那天」：TrainingPeaks 24–48 h；COVID 至少 10 天休息、7 天無症狀（Elliott 2020）。先做 Z1 短時間，有症狀就退一階。強度回得比長假慢（Friel：生病 1–2 週回 Base；TrainerRoad：前 1–2 週保守）。
- 受傷：限制來自組織，不是心肺（WKO5 巔峰期筆記的例子）。跑量要依傷別（骨應力傷害等）照醫療的走跑進度，**app 不該自動排**。本文不涵蓋傷別規則（未查）。

---

## 5. 從資料偵測中斷與它的大小

### 5.1 用什麼量

| 指標 | 優點 | 缺點 |
|---|---|---|
| **連續沒跑的天數** | Daniels 表 9.1／9.2 就是用它；直接、可解釋 | 不看交叉訓練 |
| 連續沒有任何耐力活動的天數（跑、越野、登山、騎車 ≥ 30 分，推估） | 對應 FVDOT-2（有交叉訓練） | 「多少交叉訓練才算」Daniels 沒定義 |
| CTL 降幅 | app 已有（42 天常數） | 42 天的平滑讓 1–2 週的中斷看起來很小（7 天全停 CTL 只掉約 15 %：e^(−7/42) ≈ 0.85，推估計算）；而且 CTL 是負荷，不是體能。WKO5 筆記也說「關鍵不是你目前的 CTL 降到多低」 |
| 同功率心率上升／飄移 | 直接量到停訓最先出現的變化（Houmard：同速心率 +11 bpm／14 天；Coyle 1985：前 56 天逐步上升） | 要有一次夠長的平穩跑；溫度會干擾（> 25 °C 不判，見 `heat-acclimation.md`） |

**建議：分類用「連續沒跑的天數」（Daniels），交叉訓練只用來選 FVDOT-1／FVDOT-2；是否重新確認基礎看飄移。** CTL 只用在既有的加量上限（Palladino ramp），不拿來判斷中斷大小。

### 5.2 交叉訓練（登山、騎車）算不算

- Mujika & Padilla 2000：交叉訓練能維持適應；運動員要用相近的型態，中等訓練者用不同型態也有效。
- Daniels：有交叉訓練就用 FVDOT-2（損失約減半），**但表 9.2 的復跑量不分有沒有交叉訓練**。
- 推論：百岳登山是用腳、長時間、低強度，比騎車更「相近」。但它沒有跑步的衝擊，對骨骼、肌腱與跑步經濟性的保護有限；Lepers 2024 顯示經濟性是最慢回來的。
- **推估規則：**
  - 中斷的類別一律用「連續沒跑的天數」（衝擊負荷要重新適應）。
  - 中斷期間若有 ≥ 1/2 的天數做了 ≥ 45 分的登山／騎車／健走（推估門檻），配速與功率目標用 FVDOT-2。
  - 多日百岳行程本身就是大負荷，不是停訓：行程後由既有的 TSB／ramp 規則處理疲勞，類別仍照「沒跑天數」算，但用 FVDOT-2。

---

## 6. 給 app 的建議

### 6.1 Z5（與 Z3）暫停門檻：取代佔位值

| 佔位值 | 建議 | 依據 |
|---|---|---|
| 連續 ≥ 14 天沒跑 → Z5 暫停 | **連續 ≥ 6 天沒跑 → 進入恢復期**，恢復期內 Z3、Z5 都不排（快步跑依 §6.2）。恢復期長度＝中斷天數（≤ 8 週）。1–5 天不觸發 | Daniels 表 9.2：第 2 類從 6 天開始，恢復期內只有 E 日；Friel：≤ 3 天照常、4–7 天當恢復週；TrainingPeaks：2–5 天體能沒有顯著變化、6–14 天倒退 1–2 週 |
| Z1 週量 < 確認時的 70 %，持續 2 週 | **Z1 週時間 < 確認時的 2/3，持續 3 週，只暫停 Z5**（Z3 照排）。計算時排除恢復週、減量週、比賽週；不排課的週改由「連續沒跑」規則處理 | Hickson 1982：量剩 2/3（26/40 分）長耐力守住、剩 1/3 掉 10 %；Spiering 2021：量減 33–66 % 仍可維持（一般族群）；Madsen 1993：量剩約 10 %、4 週，長耐力 −21 %。「3 週」是推估：Hickson 每 5 週量一次，最短的損失證據是 Madsen 的 4 週；2 週會被一個恢復週加一個忙碌週誤觸 |
| 長跑飄移變差 | **保留**，並當作恢復期結束時是否需要重新確認的判斷 | 停訓最先出現的就是同速心率上升（Houmard 1992、Coyle 1985）；UA：中斷後用飄移測試重新讀 |
| （新增）| 中斷 ≥ 4 週：Z5 要等**有氧基礎重新確認**後才開放 | Mujika & Padilla 2000：最近才得到的 VO2max 進步停 > 4 週就全部失去；UA 中斷後重新讀 |

「確認時的 Z1 量」：用確認當週往前 4 週的平均（推估），存成 `base.confirmed_z1_min`。

Z3 暫停只在恢復期內。量下滑（第 2 列）只停 Z5、不停 Z3。依據：Hickson 1985 強度才是維持 VO2max 的關鍵；先 Z3 後 Z5（台灣教練）。

### 6.2 依中斷長度的恢復計畫

「之前的量」＝中斷前 4 週的平均週時間（推估）。「中斷」＝連續沒跑的天數。

| 中斷 | 恢復期 | 起始量 → 加量 | Z3 何時回來 | Z5 何時回來 | 長跑上限 | 有氧基礎要不要重新確認 | 配速／功率目標 |
|---|---|---|---|---|---|---|---|
| **1–5 天** | 無 | 回到 100 %（Daniels 第 1 類：5 天 E 日 ≤ 100 %）；不補課（Friel） | 照排；中斷 ≥ 4 天時，回來後第一個 Q 課不早於第 3 次跑（推估，折衷 Daniels 的 5 天 E 日與 Friel 的 ≤ 3 天照常） | 同 Z3 | 照常 | 不用 | ×1.000 |
| **6–13 天** | ＝中斷天數 | 前半 50 %、後半 75 %（Daniels 第 2 類），之後 100 % | 恢復期結束後的第一週 | 恢復期結束後，**先完成 1 堂 Z3 且非紅色合規**，再排 Z5（台灣教練：先 Z3 後 Z5；推估：1 堂） | 中斷前最長一次 × 該段的 %（50 %／75 %），且 ≤ 90 分（推估） | 不需要正式測試；恢復期後第一次 ≥ 60 分 Z1 跑若飄移比中斷前差 → 走「飄移變差」規則 | FVDOT（0.997–0.985；有交叉訓練 0.998–0.992） |
| **14–28 天** | ＝中斷天數（2–4 週） | 前半 50 %、後半 75 %（Daniels 第 2 類） | 恢復期結束後的第一週，**目標 × FVDOT** | 恢復期結束後 **≥ 2 堂 Z3** 非紅色，**且**恢復期最後一週的長跑飄移不比中斷前差（推估：2 堂；飄移依 UA） | 50 %／75 % × 中斷前最長；恢復期最後一週可到 90 分平路 Z1，兼作 90 分鐘飄移測試（徐國峰部落格） | **要檢查**：恢復期最後一次長跑當飄移檢查（UA）；不過關 → Z5 繼續等，Z3 照排 | FVDOT 0.973–0.931（交叉 0.986–0.965） |
| **29 天–8 週** | ＝中斷天數 | 三段 33 %／50 %／75 %，最後一段可加快步跑（Daniels 第 3 類） | 恢復期結束後 | **有氧基礎重新確認後**（三種測試選一），並 ≥ 2 堂 Z3 | 33／50／75 % × 中斷前最長 | **必須**正式重新確認（Mujika：最近的進步 > 4 週全失；UA）；「確認時 Z1 量」重設為新的 | FVDOT 0.931–0.847（交叉 0.965–0.923）；中斷接近 8 週加排 CP 測試（WKO5：約 2 個月中斷要重做基準） |
| **> 8 週** | 15 週，每 3 週一階 | 33 → 50 → 70（+ 快步跑）→ 85（+ R）→ 100 %（+ T + R）（Daniels 第 4 類；每週 km 上限對這位選手的量不會碰到） | Daniels：第 5 階才回 T；app 對應：第 13 週起 Z3（推估對應） | 當作新的基礎期：品質門檻（`quality_gate`）整個重來 | 照各階 % | **必須**；基礎期長度依 Palladino「長期休息者 3–6 週，最多 2–3 個月」 | FVDOT ≤ 0.847（交叉 ≤ 0.923）；**一定要重測 CP 與 AeT** |

補充：
- **加量上限：** 恢復期內用 Daniels 的 %，不用 10 % 規則。理由：這是「回到之前做過的量」，不是新的負荷。Palladino 也說 10 % 規則已過時。恢復期結束、回到 100 % 之後，才恢復正常的 ramp 規則（CTL ramp ≥ 7 的既有保護一直有效）。
- **生病：** 起點是**無症狀**那一天，不是生病第一天：發燒或脖子以下的症狀 → 無症狀 ≥ 48 h（TrainingPeaks 上限）；COVID → ≥ 10 天休息、7 天無症狀（Elliott 2020）。中斷天數從最後一次跑步算到回來第一次跑。類別照上表，但：
  - 恢復期多一條「有症狀就退回上一段，24 h 無症狀再往上」（Elliott 2020）。
  - 恢復期後第一堂 Z3 降一區（TrainingPeaks：take intervals down a zone）。這是指引的直接套用。
- **受傷：** app 不自動排恢復計畫；請使用者手動排或照醫療的走跑進度。app 只在使用者標記「受傷」時暫停 Z3／Z5（推估）。

### 6.3 恢復期間的強度與區間

- **心率區間（AeT 為基準）在恢復期內繼續有效，而且應該當主要目標。** 停訓後同速、同功率的心率會上升（Houmard 1992：+11 bpm／14 天；Coyle 1986：+11 %）。照心率跑，會自動降速度與功率，對應的是同樣的生理強度。這是推論，符合 TrainingPeaks「stay in the low end of your … heart rate zone, rather than your normal power or speed zone」的方向（該句出自搜尋摘要，原文未逐字核對，未驗證）。
- **功率與配速上限要降：** Z3／Z5 目標 × FVDOT（§4.2），這是 Daniels 的直接用法。功率目標套同一個係數是推估：VDOT 與 CP 都是「能持續的最高有氧輸出」，但兩者的對應沒有驗證。
- **AeT 本身：** 中斷 ≥ 4 週，計畫裡的 AeT 標為過期，要求重測（UA：中斷後重新讀）。中斷 < 4 週照舊。
- Easy 跑「偏強」規則 D（AeT + 3 bpm、80 % CP）在恢復期內照常。功率版會比較容易觸發，因為同心率下功率變低。80 % CP 也乘 FVDOT（推估）。
- **短段功率（≤ 1 分）不要拿來判斷恢復：** WKO5 筆記說無氧功率休息 4 週仍然很好（§4.7）。

### 6.4 計畫中的中斷（不排課日期）vs 臨時中斷（生病）

| | 不排課日期（長假、出國） | 臨時中斷（生病、事忙） |
|---|---|---|
| 何時知道 | 事前 | 事後（同步後看到連續沒跑） |
| 觸發 | 不排課區間 ≥ 6 天 → **事前就排好恢復期**：區間結束後第一天開始，長度＝區間天數 | 每次同步時算「最後一次跑步到今天」的天數；≥ 6 天且今天有新活動（回來了）→ 產生恢復期 |
| 區間內實際有跑 | 以「實際連續沒跑天數」重算，不以區間長度為準（區間內跑了 3 次，就不是停訓） | — |
| 中斷前的減量 | 可以在區間前 1 週做一次 Z5（強度維持效果，Hickson 1985、Madsen 1993）；不額外加量 | — |
| 生病額外規則 | — | 使用者在 app 標記「生病」→ 套 §6.2 的生病補充（無症狀起算、退階規則、Z3 降一區） |

**不排課日期要不要自動觸發恢復期？要，但只在 ≥ 6 天時。** 理由：
- Daniels 的分界是 6 天。
- 1–5 天的連假（台灣常見的 3–5 天）不該讓 Z5 停掉。
- 只自動處理中斷天數，不自動判斷生病。生病要使用者標記，因為 app 分不出「沒跑」和「病了」。

### 6.5 和自動調整（plan-auto）怎麼接

讀 `docs/spec/plan-auto.spec.md`、`backend/engine/adapt.py`（只讀）得到的限制：

1. **adapt 只調當週**，而且在 reconcile 前對產生器的輸出做。恢復期跨好幾週，所以**恢復期要做在產生器（`overview.week_plan` / `projection.project_weeks`）**，不能做成 adapt 規則：
   - 新增 `mode = "reentry"`，帶 `{start, days, segments, fvdot, illness}`。
   - 週量 = 之前的量 × 該段 %。
   - Q 課不排。
   - 長跑上限照 §6.2。
2. **adapt 的 E 規則**（`rest_week` 判斷 `mode in ("recovery_week", "recovery", "taper", "event", "transition")`）要加上 `"reentry"`。否則中斷後 TSB 很高、不會觸發，但若有一次偏強，ramp ≥ 7 會在恢復期加量時被觸發。恢復期本來就是從低量往上，**ramp 規則在恢復期內應該不看**（推估）：Daniels 的 50 %→75 % 跳躍是設計好的，不是過量。
3. **adapt 的 A 規則（沒跑的 easy 不補）** 在恢復期內照常。恢復期內又中斷 ≥ 6 天 → 重新計算恢復期（以新的中斷為準，推估）。
4. **大改動判斷：** 恢復期一開始，週 TSS 會大降。降量是安全方向，會自動套用（spec「Reductions … apply on their own」）。恢復期結束回到 100 % 時，週 TSS 上升可能 > 20 %，會被當成大改動等使用者同意。**建議：** 恢復期本來就排好的升段不算「大改動」，在 `classify()` 用 `mode = reentry` 豁免（推估）。不然每次回升都要按同意。
5. **訓練階段改變：** 中斷 > 8 週 → 基礎期重來，觸發「phase changed」大改動，這是對的（要使用者知道）。
6. **品質門檻（`quality_gate`）：** `LOOKBACK_DAYS = 56`。中斷 3 週回來，8 週窗內還留著中斷前的飄移過關紀錄，會讓 Z5 繼續開。**門檻要以中斷結束日為界：** 中斷 ≥ 4 週，只看中斷之後的紀錄；14–28 天，中斷前的紀錄仍可用，但要加恢復期最後一次長跑的飄移檢查（§6.2）。`AET_FRESH_DAYS`（16 週）也要在中斷 ≥ 4 週時讓 AeT 視同過期。
7. **推送：** 恢復期的課在 7 天推送窗內照常推。生病標記改變課表時，降量會直接套用。

### 6.6 與現有 `blackouts.step_cap()` 的衝突

- 現狀（`backend/engine/blackouts.py:188-190`，`overview.py:513-519`、`projection.py:315-320`）：上週有不排課日，本週上限 = max(1.10 × 上週實際，上週實際 + 0.5 h)。
- 問題：
  - **整週不排課、實際 0 h → 下週上限 0.5 h。** 之後每週約 +10 %，從 0.5 h 回到 4–5 h 要很多週，比 Daniels 慢得多。Daniels 第 2 類是 50 % 起步（例如 4.5 h 的人約 2.25 h／週）。
  - **短的不排課（例如 3 天）** 照 Daniels 是第 1 類，應該回到 100 %。step_cap 卻從那週較少的實際量起算，連續壓低幾週。
  - 「10 % 規則」在 Palladino 筆記裡被稱為過時（`palladino基礎期訓練.md:54`）。
- **建議：** 不排課後的加量改由 §6.2 的恢復期決定（≤ 5 天回 100 %，≥ 6 天照 Daniels 的 %）。`step_cap` 只在恢復期不適用時保留（或移除）。這是程式改動，本文只提出，不改碼。

---

## 7. 推估一覽（沒有外部來源的數字）

| 數字 | 用在 | 理由 |
|---|---|---|
| 「之前的量」＝中斷前 4 週平均 | §6.2 | Daniels 沒定義；4 週與 app 其他規則一致 |
| 1–5 天：第一個 Q 課不早於回來後第 3 次跑 | §6.2 | 折衷 Daniels 5 天 E 日與 Friel ≤ 3 天照常 |
| Z1 量 < 2/3 持續 **3 週** | §6.1 | 2/3 有 Hickson 1982；3 週是避開恢復週＋忙碌週誤觸 |
| 確認時的 Z1 量＝確認前 4 週平均 | §6.1 | — |
| 6–13 天：Z5 前 1 堂 Z3；14 天以上：2 堂 | §6.2 | 順序：台灣教練；堂數是推估 |
| 長跑上限 ≤ 90 分（6–13 天） | §6.2 | 對應徐國峰 90 分檢測長度 |
| 交叉訓練門檻：≥ 1/2 的中斷天數、每次 ≥ 45 分 | §5.2 | Daniels 沒定義 FVDOT-2 的量 |
| 功率目標 × FVDOT；80 % CP 也乘 FVDOT | §6.3 | VDOT 與 CP 的對應未驗證 |
| 恢復期內不看 CTL ramp、升段不算大改動 | §6.5 | 恢復期的升量是設計好的 |
| 恢復期內再中斷 ≥ 6 天 → 重算 | §6.5 | — |
| 受傷標記只暫停 Z3／Z5、不自動排 | §6.2 | 傷別規則未查 |
| > 8 週：第 13 週起 Z3 | §6.2 | Daniels T 在第 5 階的對應 |

---

## 8. 未驗證、待查

1. Daniels 表 9.1 的 42 天 FVDOT-2 = 0.994（網頁）應為 0.944，要對原書。「交叉訓練」的定義也要查原書。
2. Elliott 2020 COVID 資訊圖的各階段時長、心率百分比（只在圖檔裡）。
3. IOC 2022 呼吸道疾病共識的 return-to-sport 分階細節（未讀全文）。
4. Pfitzinger 對中斷的一手規則（找不到）。
5. Uphill Athlete 對復跑起始量、加量速度的具體數字（找不到）。
6. TrainingPeaks「heart rate zone, rather than your normal power or speed zone」原句（來自搜尋摘要）。
7. 「再練比首練快」在耐力表現上的直接比較研究（沒找到）。
8. 所有同儕審查數字都取自摘要，沒有讀全文。

---

## 9. 參考文獻

### 同儕審查

- Mujika I, Padilla S. Detraining: loss of training-induced physiological and performance adaptations. Part I: short term insufficient training stimulus. *Sports Med* 2000;30:79–87. doi:10.2165/00007256-200030020-00002 [摘要]
- Mujika I, Padilla S. Detraining … Part II: long term insufficient training stimulus. *Sports Med* 2000;30:145–154. doi:10.2165/00007256-200030030-00001 [摘要]
- Mujika I, Padilla S. Cardiorespiratory and metabolic characteristics of detraining in humans. *Med Sci Sports Exerc* 2001;33:413–421. doi:10.1097/00005768-200103000-00013 [摘要]
- Mujika I, Padilla S. Muscular characteristics of detraining in humans. *Med Sci Sports Exerc* 2001;33:1297–1303. doi:10.1097/00005768-200108000-00009 [摘要]
- Coyle EF, Martin WH, Sinacore DR, Joyner MJ, Hagberg JM, Holloszy JO. Time course of loss of adaptations after stopping prolonged intense endurance training. *J Appl Physiol* 1984;57:1857–1864. doi:10.1152/jappl.1984.57.6.1857 [摘要]
- Coyle EF, Martin WH, Bloomfield SA, Lowry OH, Holloszy JO. Effects of detraining on responses to submaximal exercise. *J Appl Physiol* 1985;59:853–859. doi:10.1152/jappl.1985.59.3.853 [摘要]
- Coyle EF, Hemmert MK, Coggan AR. Effects of detraining on cardiovascular responses to exercise: role of blood volume. *J Appl Physiol* 1986;60:95–99. doi:10.1152/jappl.1986.60.1.95 [摘要]
- Houmard JA, et al. Effect of short-term training cessation on performance measures in distance runners. *Int J Sports Med* 1992;13:572–576. doi:10.1055/s-2007-1024567 [摘要]
- Houmard JA, et al. Training cessation does not alter GLUT-4 protein levels in human skeletal muscle. *J Appl Physiol* 1993;74:776–781. doi:10.1152/jappl.1993.74.2.776 [摘要]
- Houmard JA, et al. Effect of reduced training and training cessation on insulin action and muscle GLUT-4. *J Appl Physiol* 1996;81:1162–1168. doi:10.1152/jappl.1996.81.3.1162 [摘要]
- Neufer PD. The effect of detraining and reduced training on the physiological adaptations to aerobic exercise training. *Sports Med* 1989;8:302–320. doi:10.2165/00007256-198908050-00004 [摘要]
- Hickson RC, Rosenkoetter MA. Reduced training frequencies and maintenance of increased aerobic power. *Med Sci Sports Exerc* 1981;13:13–16. doi:10.1249/00005768-198101000-00011 [摘要]
- Hickson RC, Kanakis C, Davis JR, Moore AM, Rich S. Reduced training duration effects on aerobic power, endurance, and cardiac growth. *J Appl Physiol* 1982;53:225–229. doi:10.1152/jappl.1982.53.1.225 [摘要]
- Hickson RC, Foster C, Pollock ML, Galassi TM, Rich S. Reduced training intensities and loss of aerobic power, endurance, and cardiac growth. *J Appl Physiol* 1985;58:492–499. doi:10.1152/jappl.1985.58.2.492 [摘要]
- Madsen K, Pedersen PK, Djurhuus MS, Klitgaard NA. Effects of detraining on endurance capacity and metabolic changes during prolonged exhaustive exercise. *J Appl Physiol* 1993;75:1444–1451. doi:10.1152/jappl.1993.75.4.1444 [摘要]
- Spiering BA, Mujika I, Sharp MA, Foulis SA. Maintaining physical performance: the minimal dose of exercise needed to preserve endurance and strength over time. *J Strength Cond Res* 2021;35:1449–1458. doi:10.1519/JSC.0000000000003964 [摘要]
- Bosquet L, Montpetit J, Arvisais D, Mujika I. Effects of tapering on performance: a meta-analysis. *Med Sci Sports Exerc* 2007;39:1358–1365. doi:10.1249/mss.0b013e31806010e0 [摘要]
- Barbieri A, et al. Cardiorespiratory and metabolic consequences of detraining in endurance athletes. *Front Physiol* 2023;14:1334766. doi:10.3389/fphys.2023.1334766 [摘要]
- Zhang H, Zhu Y, Zhao Z, Tan D. The effects of detraining on body composition and cardiometabolic health: a systematic review and meta-analysis of randomized controlled trials. *Front Physiol* 2026;17:1891899. doi:10.3389/fphys.2026.1891899 [摘要]
- Lepers R, et al. Effect of 12 weeks of detraining and retraining on the cardiorespiratory fitness in a competitive master athlete: a case study. *Front Physiol* 2024;15:1508642. doi:10.3389/fphys.2024.1508642 [摘要]
- Joo CH. The effects of short term detraining and retraining on physical fitness in elite soccer players. *PLoS One* 2018;13:e0196212. doi:10.1371/journal.pone.0196212 [摘要]
- Lindholm ME, et al. The impact of endurance training on human skeletal muscle memory, global isoform expression and novel transcripts. *PLoS Genet* 2016;12:e1006294. doi:10.1371/journal.pgen.1006294 [摘要]
- Elliott N, et al. Infographic. Graduated return to play guidance following COVID-19 infection. *Br J Sports Med* 2020;54:1174–1175. doi:10.1136/bjsports-2020-102637 [PMC 文字版]
- Snyders C, Pyne DB, Sewry N, Hull JH, Kaulback K, Schwellnus M. Acute respiratory illness and return to sport: a systematic review and meta-analysis by a subgroup of the IOC consensus on 'acute respiratory illness in the athlete'. *Br J Sports Med* 2022;56:223–231. doi:10.1136/bjsports-2021-104719 [摘要]
- Schwellnus M, et al. IOC consensus statement on acute respiratory illness in athletes part 1: acute respiratory infections. *Br J Sports Med* 2022. doi:10.1136/bjsports-2022-105759 [只讀摘要]
- Schwellnus M, et al. IOC consensus statement on acute respiratory illness in athletes part 2: non-infective acute respiratory illness. *Br J Sports Med* 2022. doi:10.1136/bjsports-2022-105567 [只讀摘要]

### 教練來源

- Daniels J.《丹尼爾斯博士跑步方程式》中文版，表 9.2（你的筆記附圖，`300 Sport/60 🏃 有氧訓練/丹尼爾的跑步方程式筆記 還有課表.md:123-124`）。
- VDOT O2. VDOT Adjustments For Time Off From Running (2018-02). https://news.vdoto2.com/2018/02/vdot-adjustments-time-off/
- Friel J. Missed Workouts (2010-05-11). https://joefrieltraining.com/missed-workouts/
- Friel J. Covid and Return-to-Training Comments (2021-12-21). https://joefrieltraining.com/covid-and-return-to-training-comments/
- Watson L. How to Get Sick Like a Pro. TrainingPeaks. https://www.trainingpeaks.com/blog/how-to-get-sick-like-a-pro/
- TrainerRoad. How to Return from Illness or a Holiday Hiatus. https://www.trainerroad.com/blog/how-to-return-from-illness-or-a-holiday-hiatus/
- Uphill Athlete. Aerobic Self-Assessment for Mountain Athletes. https://uphillathlete.com/aerobic-training/aerobic-anaerobic-threshold-self-assessment/
- Uphill Athlete. Zone 2 Heart Rate Training. https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/
- 台灣教練：先 Z3 後 Z5、每週 Z1 時間。
- WKO5 研討會筆記：`300 Sport/65 ⚡ 功率訓練/研討會整理/研討會 Elements of WKO5 Training Load.md:305-306, 439-449`；`…/研討會 Training and Coaching with WKO5 part 1.md:169-170`；`300 Sport/70 ⏳ 周期化訓練/wko5研討會 基礎期.md:154-162`；`…/wko5研討會 強化期.md:227-228`；`…/wko研討會 巔峰期.md:443-459, 533-537`。
- Palladino 基礎期筆記：`300 Sport/70 ⏳ 周期化訓練/palladino基礎期訓練.md:48-54`。
