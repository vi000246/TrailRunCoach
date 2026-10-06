# 交叉訓練怎麼算進負荷與周期：騎車、游泳、健行、滑雪（SP-201）

> 調查日期：2026-10-06。只做調查，沒有改程式。基準 commit `47c5a6e`。
> 接續這幾份文件，已有的不重查：`ctl-ramp-calibration.md` §1–3（各運動的 TSS 怎麼算、健行與單車常常記 0）、`detraining.md` §5.2（停跑時交叉訓練算不算、FVDOT-2）、`periodization-cross-sport.md` §4.7.1（轉換期 Koop「anything but run」[439][440]）與 [363]（Menges 2026）。
> 標記：**已驗證（全文）**／**已驗證（摘要）**／**搜尋摘要**（沒讀到原頁）／**教練級**／**推估**／**未找到來源**。本文編號 [X1]、[X2]…。

## 摘要

1. **app 的 CTL 是所有運動加總，但很多交叉訓練記 0 TSS。** 每筆活動的 hrTSS 要有那個運動自己的 LTHR（`bikethr`、`swimthr`、`skithr`）。只有 FIT 推估時只寫跑步的 LTHR，所以騎車、游泳、滑雪多半是 0；健行和走路會借用跑步 LTHR，有算（`ctl-ramp-calibration.md` §1）。結果是：同樣練 8 小時，有騎車的那幾小時在 CTL 裡看不到。
2. **跑步的週增量只看跑步**（`load_guard.py:98`），這點和研究一致（受傷看的是跑步量）。
3. **騎車可以部分取代跑步的有氧，但不能取代跑步本身。**
   - 統合分析（7 篇、4–10 週）：只騎車和只跑步，最大攝氧量（g = −0.32）和跑步成績（g = 0.02）都沒有顯著差別 [X1]。但研究很舊、很短、很多是沒訓練的人。
   - 把每週 2 次輕鬆跑換成騎車，4 週後 3 km 一樣進步 [X2]。
   - 整段 5 週完全不跑、只騎車或滑步機：最大攝氧量和乳酸閾值保住，但 3 km 慢了 43–48 秒 [X3]（搜尋摘要）。
4. **受傷時最好的替代是水中跑。** 訓練有素的跑者只做水中跑 4 週，5 km 成績不變 [X4]；6 週最大攝氧量、通氣閾值、跑步經濟性都保住 [X5]。超過 6 週沒有研究。
5. **游泳幾乎不轉移到跑步**（鐵人選手的建模研究 [X7]、回顧 [X6]）；滑雪沒有找到對跑步的直接研究。
6. **沒有「各運動 TSS 換算成跑步 TSS」的驗證係數。** 業界做法是每個運動用自己的閾值算 TSS，再分開看或加總看（TrainingPeaks、Friel）[X9][X10]。
7. **建議（§5）**：補上交叉訓練的 TSS（P2）、狀態頁分開顯示「全部 CTL」和「跑步 CTL」（P2）、傷停時的交叉訓練課（P2）、轉換期「只做交叉訓練」選項（P3，SP-109 筆記要的）、停跑判斷把游泳滑雪也算進交叉訓練（P3）。

## 1. app 現在怎麼做

### 1.1 TSS 和 CTL

| 項目 | 現況 | 位置 |
|---|---|---|
| CTL 的輸入 | 每天所有活動的 TSS 加總，不分運動 | `backend/engine/overview.py:281`（`daily_tss`） |
| 每個運動的閾值名稱 | 跑 `run*`、騎 `bike*`、游 `swim*`、划船 `row*`、滑雪 `ski*`；其他（健行、走路）`other*` | `backend/engine/wko5expr/dataset.py:39–40` |
| 走路、健行沒有自己的閾值 | 非 parity 模式借跑步 LTHR，只算移動時間 | `dataset.py:46`、`hr_lthr`（`:608–620`） |
| 肌力 | 0 TSS | `dataset.py:47` |
| 只有 FIT 推估時 | 推估只寫 `runthr`，**騎車、游泳、滑雪沒有 LTHR → 0 TSS** | `ctl-ramp-calibration.md` §1（`fitdataset.py:817`） |
| 賽季計畫有 dated LTHR 時 | 所有運動都用它 | `ctl-ramp-calibration.md` §1（`dataset.py:584`） |

### 1.2 誰讀什麼

| 規則 | 看哪些運動 | 位置 |
|---|---|---|
| CTL ramp 護欄（注意／擋） | 全部（就是 CTL） | `backend/engine/load_guard.py:87–88`、`status.py:292–332` |
| 週跑量增幅（> 20 % 擋） | **只看跑步**（路跑＋越野） | `load_guard.py:96–98` |
| 單次長跑上限的參考 | 跑步和健行（用腳的），騎車不算 | `load_guard.py` 頂部說明（SP-66） |
| 排課的週時數 | 從 CTL 目標換算成時數，再全部排成跑步／健行／肌力 | `overview.py:1279–1283` |
| 停跑後的復跑 | 只看「連續沒跑的天數」；中斷期間 ≥ 一半的天數有 ≥ 45 分的健行、騎車、走路 → 用 FVDOT-2 | `backend/engine/reentry.py:65–66`、`:235–247` |
| 活動分類 | trail／road／hike／bike／strength／walk，其他都是 other（游泳、滑雪、划船、滑步機） | `overview.py:74–89` |
| 轉換期 | 只排輕鬆跑和肌力；說明文字寫「想做交叉訓練可以拿來取代輕鬆跑」 | `overview.py:438–441` |

**所以**：

- 排課永遠只排跑步、健行、肌力，不會排騎車或游泳。
- 交叉訓練如果有 TSS，會進 CTL，讓排課以為體能比較高，下一週就多排一點**跑步**。
- 交叉訓練如果是 0 TSS（常見），CTL 看不到它；這週練得很多的人，ramp 護欄也看不到。
- 停跑判斷裡游泳、滑雪不算交叉訓練（分類成 other）。

## 2. 文獻

### 2.1 騎車、滑步機取代跑步

| 來源 | 對象與做法 | 結果 | 標記 |
|---|---|---|---|
| Menges 等 2026 統合分析 [X1]（舊文件 [363]） | 7 篇（1974–2018），4–10 週；只跑 vs 只騎，或跑＋騎 vs 只跑；未訓練到有訓練 | 跑步機最大攝氧量 g = −0.32（−0.76～0.13）；跑步成績 g = 0.02（−0.62～0.66）；都不顯著。作者提醒研究少、信賴區間寬、方案老 | 已驗證（全文） |
| Paquette 等 2018 [X2]（Menges 收錄） | 高中越野隊，4 週，每週 2 次輕鬆跑換成騎車 | 3,000 m 兩組都進步，沒有組間差別 | 已驗證（Menges 全文轉述） |
| Honea 2012 碩士論文 [X3] | 高中越野選手，賽季後 5 週，全部改滑步機或固定式腳踏車，對照組繼續跑 | 最大攝氧量、乳酸閾值保住；3 km 計時滑步機組慢 47.7 秒、腳踏車組慢 42.7 秒，跑步組快 9.4 秒 | 搜尋摘要（論文伺服器連不上） |
| Koop（CTS）[X8] | 教練 | 每週 10–15 小時的人加幾小時低強度騎車，「simply not enough stress to be meaningful」；多出來的時間拿去跑或休息 | 搜尋摘要；教練級 |
| DeJong Lempke 等 2026（波士頓馬拉松）[X11] | 917 名跑者問卷，觀察性 | 賽前 4 個月交叉訓練越多，成績越好（p < 0.001，已控制經驗） | 已驗證（摘要）；觀察性，不能說因果 |

讀完的判斷：

- **部分取代（每週換 1–2 堂輕鬆跑）**：短期內看不出跑步表現變差 [X1][X2]。
- **全部取代**：有氧指標保得住，但跑步表現會掉 [X3]。原因推估是跑步的肌肉、肌腱、經濟性要靠跑步維持（`detraining.md` §5.2 的「衝擊負荷要重新學」）。
- 這也是為什麼 app 的復跑判斷只看「連續沒跑的天數」是對的：有騎車只減輕體能損失（FVDOT-2），不縮短復跑期。

### 2.2 受傷時：水中跑最好

| 來源 | 對象與做法 | 結果 | 標記 |
|---|---|---|---|
| Bushman 等 1997 [X4] | 11 名訓練有素的跑者（32.5 歲），4 週只做水中跑，每週 5–6 次 | 5 km 計時 1142.7 → 1149.8 秒（p = 0.28），跑步經濟性、乳酸閾值、最大攝氧量都沒變 | 已驗證（摘要） |
| Wilber 等 1996 [X5] | 16 名訓練有素的男性跑者，6 週，水中跑 vs 跑步機，同樣的強度安排（隔天 30 分 90–100 % 與 60 分 70–75 % VO2max，每週 5 天） | 最大攝氧量、通氣閾值、跑步經濟性兩組都沒變；「may serve as an effective training alternative … for up to 6 wk」 | 已驗證（摘要） |
| Mujika & Padilla 2000 | 回顧 | 交叉訓練能維持適應；運動員要用相近的動作型態 | 既有文件已驗證（`detraining.md` §3） |
| McMillan〈Return from Injury: Cross-Training〉[X12] | 教練 | 分階段：先游泳／騎車（不負重）→ 水中跑 → 反重力跑步機、滑步機 → 回到跑步；水中跑要做間歇把心率拉上來；沒有給換算比例 | 已驗證（全文）；教練級 |
| Friel〈9 天週期〉 | 教練 | 「cross training is especially good for senior runners」 | 已驗證（`masters-athletes.md` [M10]） |

讀完的判斷：

- 水中跑**有做強度**（間歇）時，4–6 週能保住跑步表現。研究的人都有照跑步課的強度做，不是只泡在水裡。
- **超過 6 週沒有研究**。
- 研究對象都是平路跑者，沒有越野或下坡的研究。回來時下坡的肌肉適應一定要重新建立（`downhill-recovery.md` §3：完整下坡的保護 9 週就沒了）。

### 2.3 游泳、滑雪、健行

| 來源 | 說法 | 標記 |
|---|---|---|
| Tanaka 1994 回顧 [X6] | 不同運動之間有一些最大攝氧量的轉移；跑步訓練轉移到別的最明顯，游泳最少；專項訓練永遠比較好 | 已驗證（摘要） |
| Millet 等 2002 [X7] | 菁英鐵人 40 週建模：騎車和跑步之間有交叉轉移；游泳高度專項、幾乎不轉移 | 已驗證（摘要） |
| 越野滑雪選手研究 [X13] | 菁英滑雪選手跑步的最大攝氧量比滑輪溜冰高約 3 %；夏季跑步的最大攝氧量有進步，滑輪的沒有 | 搜尋摘要 |
| 滑雪 → 跑步 | **未找到來源**。沒有研究看越野滑雪或登山滑雪對跑步成績的效果 | — |
| 健行、登山 | app 已把健行算進 CTL，並給爬升加成（UA，`ctl-ramp-calibration.md` §1）；百岳本身就是目標，不只是交叉訓練 | 既有 |

### 2.4 TSS 怎麼跨運動算

| 來源 | 說法 | 標記 |
|---|---|---|
| Friel〈Chronic Training Load — So What?〉[X9] | 「triathletes tend to have lower CTLs *per sport* than do single-sport athletes」；CTL 是負荷，也是體能的代理 | 已驗證（全文） |
| Friel〈Specificity of Training〉 | 心肺會受益，但肌肉沒有用同樣方式動，就沒有明顯的肌肉效果 | 搜尋摘要 |
| TrainingPeaks [X10] | 每個運動各自設閾值（騎車功率、跑步配速）；圖表可以依運動過濾，看單一運動的負荷 | 搜尋摘要 |
| 各運動 TSS 換算成「跑步等價」的係數 | **未找到來源**。查到的換算（例如騎車約是跑步代謝的 69 %）都來自工具網站，沒有研究 | 未找到來源 |
| 騎車的心率閾值比跑步低多少 | 這次沒有查到可靠出處 | 未找到來源 |

讀完的判斷：

- **業界沒有「換算成跑步 TSS」的做法**，是每個運動用自己的閾值算，然後「總負荷」和「單一運動負荷」分開看。
- app 的 ramp 護欄看總 CTL 是合理的（全身疲勞），但「跑步體能」要另外看。
- 騎車用跑步的 LTHR 算 hrTSS 會**偏低**（騎車同樣努力心率通常較低，推估，未找到來源），偏低對護欄來說是比較保守的方向（不會多擋）。

## 3. 落差

| # | 項目 | 來源怎麼說 | app 現在 | 判斷 |
|---|---|---|---|---|
| 1 | 交叉訓練的 TSS | 每個運動用自己的閾值算 [X10] | 沒有那個運動的 LTHR 就是 0 | **缺口**：騎車、游泳、滑雪多半看不到 |
| 2 | 跑步體能 vs 總負荷 | 分開看 [X9][X10] | 只有總 CTL；週跑量另外看跑步 | **缺口（小）**：狀態頁沒有「跑步 CTL」 |
| 3 | 交叉訓練能不能取代跑步量 | 部分取代可以 [X1][X2]，全部不行 [X3] | 排課不排交叉訓練；交叉訓練的 TSS 會推高 CTL，讓下週多排跑步 | **風險**：補上 TSS 後，騎很多的人會被排更多跑步。要先做 #2 |
| 4 | 受傷時 | 水中跑 4–6 週保住表現 [X4][X5] | 傷病紀錄只決定能不能跑、復跑怎麼加量（`injuries.py`）；沒有替代課 | **缺口** |
| 5 | 轉換期只做交叉訓練 | Koop「anything but run」2–4 週 [439] | 只有說明文字；SP-109 筆記說沒做 | **缺口**（SP-109 留下的） |
| 6 | 停跑時的交叉訓練判斷 | Daniels FVDOT-2 沒定義量 | 只算健行、騎車、走路；游泳、滑雪、划船不算 | **小缺口** |
| 7 | 週跑量增幅 | 受傷研究量的是跑步 | 只看跑步 | 一致 |
| 8 | 復跑期長度 | 衝擊負荷要重新建立 | 只看沒跑的天數 | 一致 |

## 4. 結論

- **TSS 要補，但要先分開顯示**。只補 TSS 不分開，騎車多的人 CTL 變高，排課會多排跑步，等於用騎車的體能去加跑步量，和「全部取代會掉跑步表現」[X3] 的方向相反。
- **排課的加量依據要看跑步**。建議：CTL ramp 護欄繼續看總 CTL（全身疲勞），但週量目標和「該不該加量」要看跑步的部分。這需要你決定（§5 X-2）。
- **傷停和轉換期要有交叉訓練的課**。證據最好的是水中跑（4–6 週），其次是騎車和滑步機。
- **不做「交叉訓練換算成跑步分鐘」**。沒有驗證過的係數。

## 5. 建議開的單

| # | 標題 | 優先度 | 驗收條件（草案） | 要你決定的事 |
|---|---|---|---|---|
| X-1 | 交叉訓練有 TSS：沒有該運動 LTHR 時借跑步 LTHR | P2 | (a) 騎車、游泳、滑雪、划船、其他有氧在沒有自己的 `*thr` 時，非 parity 模式借 `runthr` 算 hrTSS（和走路、健行同一條路，`dataset.py:46` 的 `HR_FALLBACK_SPORTS` 擴充）；(b) 活動上標「用跑步 LTHR 推估」；(c) 肌力仍是 0；(d) 回歸測試：只有跑步的資料集 CTL 不變；(e) 先做 X-2 或同時上線 | 要不要包含游泳（心率在水中偏低、手錶心率常不準）。建議先只做騎車、滑雪、划船、滑步機，游泳維持 0 |
| X-2 | 狀態頁分開顯示「全部 CTL」和「跑步 CTL」，排課加量看跑步 | P2 | (a) 狀態頁的體能卡片多一行跑步 CTL（只算路跑＋越野＋健行）；(b) CTL ramp 護欄仍看全部；(c) 週量目標（`overview.py:1279–1283` 的 `need_tss`）改用跑步 CTL 的目標，或交叉訓練比例 > 某值時加註；(d) 交叉訓練佔比顯示在週摘要；(e) 有測試：每週騎 5 小時、跑 3 小時的人，下週跑步不會因為騎車而超過 +10 % | 排課加量要完全看跑步 CTL，還是看總 CTL 但交叉訓練打折（沒有係數，打折是推估）。建議完全看跑步 CTL |
| X-3 | 傷停時排交叉訓練課 | P2 | (a) 傷病紀錄是「不能跑」時，課表把原本的跑步課換成交叉訓練課，時間相同；(b) 可選類型：水中跑（預設）、騎車、滑步機、游泳；(c) 強度照原課：輕鬆跑 → 輕鬆，間歇 → 同樣的工休結構、看心率或 RPE；(d) 說明寫「水中跑 4–6 週能保住跑步表現（Bushman 1997、Wilber 1996），超過 6 週沒有研究」；(e) 不推到手錶或推成一般有氧課；(f) 復跑仍照 `reentry.py`，交叉訓練只影響 FVDOT | 交叉訓練課要不要推到 COROS（COROS 有游泳、騎車課表，但水中跑沒有） |
| X-4 | 轉換期「只做交叉訓練」選項 | P3 | (a) 課表偏好的轉換期加一個選項「不跑，只排交叉訓練」；(b) 週時數照轉換期（賽前 4 週的 50 %），全部排成交叉訓練＋肌力；(c) 預設仍是現在的輕鬆跑；(d) 轉換期結束後的回量期照常從跑步開始；(e) 說明引 Koop〈off-season〉[439] | 要不要在超馬後（SP-109 的 6 週）預設開啟。建議不要，維持選項 |
| X-5 | 停跑判斷把游泳、滑雪、划船、滑步機算進交叉訓練 | P3 | (a) `reentry._cross` 的類型加上這幾種（需要 `overview.category` 先能分出來，現在都是 other）；(b) 門檻不變（≥ 一半天數、每次 ≥ 45 分，推估）；(c) 有測試 | 無 |

**不建議開的單**

- 交叉訓練換算成「等價跑步分鐘」來抵週跑量：沒有驗證過的係數（§2.4）。
- 交叉訓練縮短復跑期：研究支持「有氧保得住」，不支持「跑步的肌肉和衝擊適應保得住」[X3]。

## 6. 限制、付費牆、查無出處

**限制**

- 騎車取代跑步的研究多半是 1970–2000 年代、4–10 週、很多是沒訓練的人 [X1]。
- 水中跑的研究只到 6 週、每組 8–11 人。
- 沒有越野跑者、下坡、超馬的交叉訓練研究。
- 沒有滑雪 → 跑步的研究。
- 沒有讀 `injuries.py` 全部，只知道它決定能不能跑與復跑的往上一級（`detraining.md` §6）。

**付費牆、沒讀到原文**

- Honea 2012 論文：學校伺服器連不上，只看到搜尋摘要的數字。
- Paquette 2018（J Strength Cond Res）全文：只看到 Menges 的轉述。
- Tanaka 1994、Millet 2002 全文：只讀到摘要。
- Trail Runner 雜誌〈Cross Training Could Help Your Running〉：要登入。

**查無出處、不採用**

- 「騎車的有氧效果是跑步的 70–90 %」「水中跑 6–8 週保住 VO2max 在 1–3 % 內」「每週 3 次交叉訓練可以無限期維持體能」：都只出現在商業部落格或搜尋摘要，沒有原始研究。
- 「騎車代謝約是跑步的 69 %」的換算：工具網站，沒有出處。

## 參考

- [X1] Menges T, Dindorf C, Dully J, Fröhlich M. Cross-training between running and cycling: effects on VO2max and running performance — a systematic review and meta-analysis. *Front Sports Act Living* 2026. https://doi.org/10.3389/fspor.2026.1843803
- [X2] Paquette MR, Peel SA, Smith RE, Temme M, Dwyer JN. The impact of different cross-training modalities on performance and injury-related variables in high school cross-country runners. *J Strength Cond Res* 2018;32(6):1745–1753.
- [X3] Honea D. The impact of replacing run training with cross-training. Appalachian State University 碩士論文, 2012. https://libres.uncg.edu/ir/asu/f/Honea,%20David_2012_Thesis.pdf （搜尋摘要）
- [X4] Bushman BA, Flynn MG, Andres FF, et al. Effect of 4 wk of deep water run training on running performance. *Med Sci Sports Exerc* 1997;29(5):694–699.
- [X5] Wilber RL, Moffatt RJ, Scott BE, Lee DT, Cucuzzo NA. Influence of water run training on the maintenance of aerobic performance. *Med Sci Sports Exerc* 1996. https://doi.org/10.1097/00005768-199608000-00017
- [X6] Tanaka H. Effects of cross-training. Transfer of training effects on VO2max between cycling, running and swimming. *Sports Med* 1994. https://doi.org/10.2165/00007256-199418050-00005
- [X7] Millet GP, Candau RB, Barbier B, et al. Modelling the transfers of training effects on performance in elite triathletes. *Int J Sports Med* 2002. https://doi.org/10.1055/s-2002-19276
- [X8] Jason Koop. Do's and Don'ts of Double Day Training for Ultrarunners. https://trainright.com/double-day-training-ultrarunner/ （搜尋摘要）
- [X9] Joe Friel. Part 1: Chronic Training Load — So What? https://joefrieltraining.com/part-1-chronic-training-loadso-what/ （2026-10-06 讀取）
- [X10] TrainingPeaks. New iOS Update Adds Charts／Training by Day/Week chart. https://www.trainingpeaks.com/blog/new-ios-update-adds-charts/ （搜尋摘要）
- [X11] DeJong Lempke AF, Ackerman KE, Stellingwerff T, et al. Training volume and training frequency changes associated with Boston Marathon race performance. *Sports Med* 2026. https://doi.org/10.1007/s40279-025-02304-4 （PMID 40913707）
- [X12] Greg McMillan. Return from Injury: Cross-Training. https://www.mcmillanrunning.com/return-from-injury-cross-training/ （2026-10-06 讀取）
- [X13] Elite cross-country skiers do not reach their running VO2max during roller ski skating. https://www.researchgate.net/publication/243972115 （搜尋摘要）
- 舊文件編號：[363][439][440] 見 `periodization-cross-sport.md`；Mujika & Padilla 2000、Daniels FVDOT 見 `detraining.md`。
