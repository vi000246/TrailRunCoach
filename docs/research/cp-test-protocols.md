# 跑步 CP 測試流程：文獻、WKO5 的做法、App 裡怎麼選

Date: 2026-09-30. 範圍：研究與設計，**不改程式碼**。相關既有文件：
`docs/wko5-internals/formulas.md` §6（PD 模型）、`docs/research/superpower-calculator.md` §1.5（CP 擬合、RWC）、
`docs/research/racepower-v2.md` §2.1、§3A（F1–F4）。

標記沿用 `racepower-v2.md` §0.1：**[確立]** 同儕審查直接支持；**[廠商]** Stryd 等廠商文件；
**[外插]** 公式確立但用在量測範圍外；**[自組]** 我們自己組合；**[無來源]** 沒找到文獻。
二進位位址一律是 WKO5.exe 5.0.587 的 VA；二進位只當資料讀。

---

## 0. 結論先講

1. **今天的測試**：3 分段 217 W < 12 分段 221 W，兩點模型無效（W′ 為負）。現有程式還會把最好的 3 分窗取在
   12 分段裡面，算出無意義的「CP 219 W」，而且把這次判成 easy（§1）。可用的是單段估計：
   **CP ≈ 204 W（201–211 W）**，標「參考」。
2. **WKO5 沒有 CP 測試功能**（字串掃描全是負結果，§2.1）。使用者記得的是社群圖表「Best Times for Informal Testing Run」：
   找 PD 曲線上相對模型最弱的三個時長，叫你去補（§2.2）。mFTP / FRC / TTE 由 90 天包絡線的有界 Gauss–Newton
   擬合決定，15–20 分與 ≥ 40 分的點決定 mFTP（§2.3）。
3. **WKO5 的 195 W 已重現**：我們的 PD 模型 port 在 WKO5 資料 ∪ 今天的檔上得到 196.1 W；只用 WKO5 資料得到 175.7 W，
   和 pd_snapshot 的 175.6 W 相差 0.1 W（§1B.2）。mFTP（30–60 分）比 3–12 分的兩點 CP 低 5–10 % 是文獻上預期的
   （§1B.1）。**賽事功率：長距離（F1）用 mFTP + TTE，短距離（F2）用兩點 CP + W′**（§1B.3）。
4. **Stryd 官方的兩段測試是 9 分 + 3 分**，不是 3′/12′；3′/12′ 在跑步沒有驗證研究（§3.0）。
5. **建議三選一**（§4）：`quick` 20 分全力（約 37 分，有跑者驗證、SEE 約 3 %）、`standard` 12 分 + 休 30 + 3 分
   （約 70 分，有 W′；**休息不能縮**）、`race` 5–10 K 比賽。嫌長就換 quick，不要縮短恢復。
6. **App 設計**（§5）：`plan.prefs.cp_test_protocol`；session 帶 `protocol` 欄位；偵測先認課表（done_by）再猜；
   全力段不要設 COROS 功率上限；新增「套用這次的 CP」API，日期寫測試那天，並存 W′。

---

## 1. 今天這次測試（2026-09-30）發生了什麼

| 段 | 時間 | 功率 | 心率 |
|---|---|---|---|
| 暖身 | 8.9 分 | — | — |
| 3 分「全力」 | 3:00 | 217 W | 平均 141、最高 146 |
| 恢復 | 16.5 分（課表寫 30 分） | — | — |
| 12 分全力 | 12:00 | 221 W（最後 1 分 245 W） | 155 → 171 |
| 緩和 | 8 分 | — | — |
| 合計 | 48 分 | | |

用目前的程式跑一次（`workout_review.measure` / `classify`，COROS 來源，腳本在 scratchpad，未進 repo）：

- `cp_test()` 取「整個檔案裡最好的 180 s」與「最好的 720 s」。最好的 3 分是 **230.5 W，落在 12 分那段裡面**，
  所以 `separate = False`。算出來 `CP 219 W、W′ 2.1 kJ`：這個數字沒有意義（兩個窗重疊）。
- `looks_like_cp_test()` 因為 `separate = False` 回 False；活動標題是空的、賽季計畫當天沒有門檻列
  → `classify()` 判成 **easy**，不是 `test_cp`。但 `cp_lines()` 仍會印「CP 219 W，差 +24.8 %：建議更新」。
  **這是現有偵測的兩個洞**：（a）窗可以重疊；（b）不看課表，只猜標題與功率型態。
- 用圈數（lap）算：`CP = (221·720 − 217·180)/540 = 222.3 W`，`W′ = (217 − 222.3)·180 = −0.96 kJ`。
  W′ 為負 → 兩點模型無效。

**為什麼 3 分那段不是全力**（**[自組]** 判讀：文獻沒有驗證過的心率／配速判準，見 §3.4）：

- 3 分全力的功率一般明顯高於 12 分（兩點模型要求 P3 > P12；典型 W′ 下 P3 ≈ P12 + W′·(1/180 − 1/720)）。
  217 W < 221 W，本身就違反模型。
- 心率：3 分段最高 146，12 分段到 171。3 分全力的最後心率通常接近 12 分全力的最後心率（都接近最大心率的 90 % 以上）；
  差 25 bpm 表示 3 分段只跑到閾值附近。
- 12 分段最後 1 分 245 W，比該段平均高 11 %：前 11 分有保留，所以 **12 分的 221 W 本身也只是下限**。

**敏感度**（純代數，不需來源）：`CP = (P12·720 − P3·180)/540`，所以 ∂CP/∂P3 = −1/3、∂CP/∂P12 = +4/3。

- 3 分少跑 10 W → CP **高估** 約 3 W（W′ 則嚴重低估）。
- 12 分少跑 10 W → CP **低估** 約 13 W。
- 恢復不足時，第二段會帶著 W′ 缺口跑。3′/12′ 的第二段正好是 12 分，P12 偏低 → CP 被低估最多。
  → **縮短恢復比「3 分不夠力」更傷 CP**；而且若要縮短恢復，**先長後短**（12′ 再 3′，同 Stryd 9/3 的順序）
  讓殘留疲勞落在對 CP 最不敏感的 3 分段（§3.3）。

**今天能拿到的可靠數字**（單段 + W′ 先驗，§3.4；**[自組]**，沒有文獻驗證過這個做法）：
`CP ≈ P12 − W′/720`，P12 = 221.9 W（檔案的 720 s mean-max）。12 分段 W′ 每差 ±4 kJ，CP 差 ±5.6 W。

| W′ 假設 | 來源 | CP |
|---|---|---|
| 7.6 kJ | RWC「中等」帶內（`racepower/cp.py` RWC_BANDS，男、無風 5.63–9.82 kJ） | 211 W |
| 9.8 kJ | RWC「中等」上限 | 208 W |
| 13.1 kJ | Stryd 9/3 測試的業餘男性平均 13.1 ± 4.0 kJ（Ruiz-Alias 2025，n = 19）**[確立]** | 204 W |
| 15 kJ | 同一分布的偏高端 | 201 W |

→ **CP 約 201–211 W**，而且因為 12 分段有保留，真值偏向區間上緣或更高。

---

## 1B. 為什麼 WKO5 顯示 mFTP 195 W，而我們估 CP 201–211 W

### 1B.1 兩個數字量的不是同一件事

- **WKO5 mFTP**：PD 模型 `P(t) = FRC/t·(1 − e^(−t/τ1)) + (FTP + [t > TTE]·D·ln(t/TTE))·(1 − e^(−t/τ2))`
  的有氧平台高度（`formulas.md` §6.1，model @0x671e10）。**TTE 是 P(t) 降回 FTP 的時間**（@0x674490），
  被夾在 1800–3600 s（validity gate @0x6722a0）。也就是「約 30–60 分鐘能撐住的功率」。
  Stryd 的 CP 定義也是這個範圍：約 30–70 分鐘可持續的功率 **[廠商]**
  （https://help.stryd.com/en/articles/8955821-stryd-race-calculations-faq）。
- **兩點 CP（3′/12′）**：`W = CP·t + W′` 的斜率，只用 3–12 分鐘的點。它是 heavy / severe 的分界。文獻的幅度：
  - CP 功率下能撐的時間「不超過約 20–30 分鐘」（Jones 2019）**[確立]**；用哪些預測段會改變它：34 vs 43 分
    （Jenkins 1998）。
  - 預測段越短，CP 越高：最高強度組 201 W vs 最低強度組 164 W，約 +23 %（Bishop 1998）；
    全部 < 10 分鐘的預測段會「明顯高估」，兩段 12–20 分鐘則和標準一致（Mattioni Maturana 2018）**[確立]**。
  - CP 高於最大乳酸穩定態：MLSS 233 W、CP 253 W（+8.6 %）（Mattioni Maturana 2016）；跑步 CS 16.4 vs MLSS 15.2 km/h
    （+8 %，Nixon 2021）**[確立]**。
  - CP 高於 FTP：自行車 +7 ± 13 W（Karsten 2020）、+16 W（McGrath 2021）；跑步（Stryd）CP ≈ 30 分鐘功率，
    比 60 分鐘功率高 14 W（約 5 %，Ñancupil-Andrade 2024）**[確立]**。

所以同一個人，**3′/12′ 的 CP 比 mFTP 高 5–10 % 是預期的**，不是誰算錯。今天 CP 201–211 W vs mFTP 195 W
（+3 % 到 +8 %）正落在這個範圍。

### 1B.2 重現 195 W（已完成，數值吻合）

用 repo 的 PD 模型 port（`backend/engine/algorithms/wko5_pdmodel.py`，照 fitter @0x674c40、solver @0x6724c0）
和 evaluator 的 `meanmax(runpower)`（`functions.md` §1，已 bit-exact 驗證），腳本在 scratchpad：

| 90 天包絡線（到 2026-09-30） | mFTP | FRC | TTE | 3′ / 5′ / 20′ / 40′ MMP |
|---|---|---|---|---|
| 只有這台的 WKO5 資料（到約 9/22） | **175.7 W** | 5.30 kJ | 1896 s | 201 / 200 / 179 / 167 |
| 對照：這台的 `pd_snapshot` | 175.6 W | 5.3 kJ | 1897 s | — |
| WKO5 資料 ∪ 今天的 COROS 檔 | **196.1 W** | 4.92 kJ | 1871 s | 231 / 228 / 185 / 167 |
| 對照：使用者那台 WKO5 今天 | 195 W | — | — | — |

- 第一列和 WKO5 自己存的快照差 0.1 W、1 s：**這是 PD 模型 port 第一次有數值驗證**（`formulas.md` §6 原本標
  DISASSEMBLY-ONLY）。
- 第三列和使用者那台 WKO5 差約 1 W（兩邊的 90 天資料集不完全相同，例如 TP 同步的活動）→ **模型確認**。
- 同一條包絡線上的其他擬合：
  - 3–12 分的點做兩點 / 線性 CP：**221.5 W、W′ 1.7 kJ**（因為 3′ 點在 12′ 那段裡面，曲線太平 → W′ 太小、CP 太高，
    跟 §1 的問題相同）。
  - 3–20 分（`racepower/cp.py` 的 CP_DURATIONS）：**176.5 W、W′ 17.5 kJ**（20 分的點來自舊的、非全力的跑步，
    把斜率壓低）。
  - 模型本身在 12 分鐘的預測是 203 W，實際 MMP 是 222 W：模型用 FRC 4.9 kJ + τ1 很小，**沒辦法同時貼合
    3–12 分的平台和 20 分以後的陡降**。原因是 20–40 分的 MMP（185 / 167 W）不是全力值。

**解讀**：mFTP 195 W 是被「最近 90 天沒有一次 20–40 分鐘的全力」拉低的。今天的 12 分全力把它從 176 拉到 196，
但 20 分以上的點仍是訓練強度。等有一次 20–30 分鐘的全力（或一場 5–10 K 比賽），mFTP 會往 CP 靠近。

### 1B.3 賽事功率該用哪個數字

`racepower-v2.md` §3A 的 F1–F3：

- **F1（長，T ≥ TTE）**：`P = CP·M·(T/TTE)^k`。這裡的「CP」定義上是 **TTE 時的功率**，也就是 mFTP 的語意。
  用 3′/12′ 的兩點 CP 當 F1 的錨點，會把全馬／超馬功率高估（高估幅度 ≈ 兩者差距，今天約 +5–8 %）。
- **F2（短，T ≤ 1200 s）**：`P = CP + W′/T`。這裡要用**同一組**兩點 CP / W′（兩個參數必須成對，不能拿 mFTP 配
  兩點 W′）。
- **建議**（自組）：
  1. F1 錨點 = **WKO5 mFTP + TTE**（或我們自己同一 port 算出來的值），不用兩點 CP。
  2. F2 = **兩點 CP + W′**（測試或包絡線 3–12 分的擬合），只用在 ≤ 20 分鐘。
  3. F3 在 F2(1200) 與 F1(TTE) 之間內插，本來就是為了接起兩個模型（已有）。
  4. 賽季計畫的 CP 門檻列同時存 `cp`（兩點）與 `wprime`；頁面標清楚「CP（3–12 分鐘模型）」與「mFTP（30–60 分鐘）」。
  5. 兩者差距 > 12 % 時提示：「20–40 分鐘的最佳功率不是全力值，mFTP 偏低；排一次 20 分鐘全力或 5–10 K 比賽」。

---

## 2. WKO5 怎麼決定 mFTP / FRC / TTE（反組譯）

### 2.1 有沒有「CP 測試」功能：**沒有**

字串掃描（ASCII 與 UTF-16，整個 WKO5.exe，關鍵字 informal / test / critical power / CP / W' / Skiba / Monod /
Morton / 3 min / 20 min / 12 min / all out / time to exhaust / Stryd / Palladino / protocol）只有這些命中，**全都不是測試流程**：

| VA | 字串 | 是什麼 |
|---|---|---|
| 0x847328 | `10 - All Out` | 主觀感受（RPE）下拉選單的一項；前後是 `9 - Extremely Difficult`、`Hungry`、`Very Hungry` |
| 0x8466f4 | `Time to Exhaustion` | 指標名稱清單（前後是 `Phenotype`、`TSS Duration`、`Stamina`） |
| 0x868ab4 | `FIT decode error: Protocol version` | FIT 解碼錯誤訊息 |
| 0x8264f0 | `…registration_protocol_win.cc` | crashpad 原始檔路徑 |

沒有 `Skiba`、沒有 3/12/20 分鐘的測試字串。

### 2.2 使用者記得的「測試」：圖表 Best Times for Informal Testing Run

`docs/wko5-views/season-view.json` 約 4769 行；作者 William Renfroe、Tim Cusick、Steve Palladino；說明：
「Displays points on the runpower MMP curve for the last 90 days that are below the power-duration curve and marks
the points with the greatest percentage deviation」。它是圖表庫的內容，不在執行檔裡。做法：

- 三個區段：短 ≤ 30 s、中 30–900 s、長 900–3600 s。
- 每段找 `(pdcurve − meanmax)/pdcurve` 最大的時長，例如
  `xx(greatest(if(xx(meanmax(runpower))>=900&&xx(meanmax(runpower))<=3600,(pdcurve(…)−meanmax(…))/pdcurve(…)),1))`
  → 那就是「你的曲線相對模型最弱的時長」＝該去測的時長；目標功率 = `li(pdcurve(…), 該時長)`。
- 另一個系列 `Rx Power Points` 用 `targetduration/targetpower({5:0:-1}, …)`（`formulas.md` §6.9b，
  @0x7096c0 / @0x70a340，跳躍表 @0x709ef4 / @0x70aa9c），是依 iLevel 的處方間歇，不是測試。

**所以 WKO5 的「測試方法」是「非正式測試」：不規定固定流程，而是叫你去補 PD 曲線上最弱的三個時長**
（短衝刺、30 s–15 分、15–60 分），模型再從 90 天包絡線重新擬合。

### 2.3 擬合：哪些時長決定哪個參數

全部出自 `formulas.md` §6.2–6.6（反組譯），並已在 §1B.2 用 pd_snapshot 做了數值驗證：

| 步驟（fit entry @0x6741e0、WKO5 fitter @0x674c40） | 用到的包絡線點 |
|---|---|
| 前提：包絡線最長時長 **≥ 2400 s**，且 ≥ 5 點；> 29576 s 的尾端丟掉 | 一次 ≥ 40 分鐘的跑步 |
| Pmax = 3–5 s MMP 平均（之後不再擬合） | 最大衝刺 |
| FTP 初值 = 900–1200 s MMP 平均（沒有就 250 W） | 15–20 分鐘 |
| τ1 初值 = 第一個低於 `Pmax − 0.333·(Pmax − FTP0)` 的時長 / 3 | 約 10–60 s 的點 |
| 擬合 #1：t ≤ min(最長, 2400 s) 的所有點，自由參數 {FRC, τ1, FTP} | 1 s–40 分 |
| FTP 下限：`FTP = max(FTP, max_{1200≤t≤3600}(MMP(t) − anaerobic(t)))` | 20–60 分 |
| 擬合 #2：全部點，自由參數 {τ2, TTE, D}；TTE 夾在 1800–3600 s | 30 分以上的衰退 |
| 單調檢查：模型不能比它的最小值高 10 W 以上 | — |

- 方法：**有界 Gauss–Newton**（@0x6724c0），殘差不加權 `r_i = y_i − P(t_i)`，解析 Jacobian，
  步長不降就 ×0.125 重試，最多 500 次，|Δ| < 1e−5 停；之後用共變異數給每個參數的標準誤（`ftpe`、`frce`…）。
- 驗證閘（@0x6722a0）：0 < FRC ≤ 80 kJ、0 < τ1 < 90、0 < FTP < 600、1800 ≤ TTE ≤ 3600，否則無模型。
- **TTE** = P(t) 降回 FTP 的 t（@0x674490：從 2·TTE_param 往上加倍，再在 log10 空間二分 25 次）。

**穩定擬合的最少努力**（從上表推，自組）：90 天內要有 ①一次最大衝刺（3–5 s）、②10–60 s 的全力、
③3–5 分鐘全力、④15–20 分鐘全力、⑤一次 ≥ 40 分鐘的跑步（有強度更好）。
**因為包絡線是不加權最小平方，而 1–600 s 的點遠多於 600–3600 s 的點**（網格 1.05ⁱ），
缺 ④ 時 mFTP 會被 20–40 分的訓練值往下拉（§1B.2 就是這種情況）。

---

## 3. 文獻：跑步 CP 測試流程（依總時間排序）

### 3.0 取用說明

- 這個 session 的 WebSearch 額度已經用完，文獻改用 Europe PMC REST API（依 DOI 取摘要）、Stryd 說明頁、
  兩篇開放全文查。每筆標註查到的深度：**[全文]** 讀過全文；**[摘要]** 依 DOI 取得完整摘要（數字可靠）；
  **[摘要列表]** 只看到搜尋列表裡的摘要（數字暫定）；**[Stryd]** 抓到 Stryd 頁面；**未驗證** 沒查到。
- 參考標準（reference）是**多天、每天一次力竭（TTE）或計時（TT）**，再用 2 參數模型擬合。
- 兩段的算法：功-時模型 `W = CP·t + W′` 和 1/t 模型 `P = W′/t + CP` 在兩點時答案一樣：
  `CP = (P₂t₂ − P₁t₁)/(t₂ − t₁)`、`W′ = (P₁ − CP)·t₁`（P₁ 是短段）。例：9 分 300 W、3 分 360 W →
  CP = (300·540 − 360·180)/360 = 270 W、W′ = 16.2 kJ。

**重要修正**：Stryd 目前的說明頁寫的兩段測試是 **9 分 + 3 分**（先長後短），**不是 3′/12′**。
3′/12′ 在跑步沒有找到驗證研究，Stryd 或 Palladino 的 3′/12′ 說法也沒找到 **未驗證**。
3′/12′ 只有自行車的驗證（Simpson & Kordi 2017）。

### 3.1 流程總表（依總時間排序）

總時間含暖身；第 4–10 列的時間是我們用「暖身 15–20 分 + 各段 + 休息」算的。

| # | 流程 | 步驟 | 總時間 | 休息 | 算法 | 與參考標準的偏差 | 跑者驗證（n、誤差） | Stryd 戶外可行性 | 出處 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Stryd「估計 CP」 | 17 分輕鬆跑，含兩次 60 秒加速；專有演算法 | 約 17 分 | — | 專有；Stryd 說不需全力 | 沒有資料；Stryd 自己說兩週測試組「最準」 | 無 | 最容易 | Stryd 8258035 [Stryd] |
| 2 | 3 分鐘全力測試（3MT） | 暖身 → 3 分鐘從頭全力到底；最後 30 秒平均 = CP/CS，其上的功 = W′/D′ | 約 20–25 分 | — | EP = 最後 30 s 平均；WEP = EP 以上的功 | 自行車：EP 高估 CP（統合分析 g = 0.41，20 篇、N = 284 [摘要列表]）；等速模式 +31–37 W、SEE 6.6–7.1 % [摘要]；Vanhatalo 2007 線性模式 EP ≈ CP、SE 約 6 W [摘要]。跑步：CS 沒有被高估，但 D′ 被低估 | Broxterman 2013，n = 7，田徑場：ES 13.4 vs CS 13.3 km/h，D′ 141 vs 204 m，7 人中 5 人低估 [摘要]。Pettitt 2012，n = 14 女性（GPS）：預測 1600 m CV 1.7 %、5000 m 2.1 %，800 m 不準 [摘要]。Hunter 2023，n = 23，足部感測器：3MT 305 W、TT 290 W、訓練資料 281 W，無顯著差，W′ 一致性差 [摘要] | 戶外很難執行「從頭全力」的配速；要平路、要有全力經驗。Lipková 2025 綜論：適合習慣全力的跑者 [全文] | Burnley 2006；Vanhatalo 2007；Broxterman 2013；Pettitt 2012；Hunter 2023；Lipková 2025 |
| 3 | 20 分鐘計時 × 0.95 | 暖身 → 20 分全力 → 緩和 | 約 35–45 分 | — | CP ≈ 0.95·P20（跑步 FTP ≈ 0.90·P20） | 跑步（Stryd）：只有 20 分 TT 估 CP 可接受（SEE 6.67 W）；CP ≈ 30 分鐘功率，比 60 分鐘功率高 14 W。自行車 CP − FTP 7 ± 13 W，LoA −19 到 +33 W | Ñancupil-Andrade 2024，n = 15 高訓練跑者 [摘要] | 容易：一段持續全力 | Ñancupil-Andrade 2024；Karsten 2020；Borszcz 2018 |
| 4 | **Stryd 9/3**（兩段） | 10 分輕鬆 + 活動度 + 3 × 100 m 加速（或 10 分輕鬆 + 2–3 × 1 分快、休 2 分）→ 9 分全力 → 休 30 分 → 3 分全力 | 約 60–65 分 | 30 分（Olaya-Cuartero 用活動式休息） | 兩點 / 1/t | 兩點 vs 三點：CP 278 vs 270 W（p = .041，d = 0.75），CS 沒差 [摘要]。先 9 分：9 分段 +4.9 W，CP 偏差 < 5 %、W′ < 10 % [摘要]。同日 vs 分日沒差（p = .32）[摘要]。田徑場 vs 跑步機不可互換（CP SEE > 5 %、W′ > 10 %）[摘要] | Ruiz-Alias 2022，n = 15：CP ≈ RCP（88.7 vs 86.8 % VO2max）[摘要]。Ruiz-Alias 2025，n = 19 業餘：CP − VT2 偏差 3.5 W、18/19 在 5 % 內；W′ 女 6.4 ± 2.2、男 13.1 ± 4.0 kJ [全文]。Olaya-Cuartero 2023，n = 9：半馬以 97.3 % CP 完成，r = .88 [全文]。地形：田徑場／路／碎石 CP 相同，碎石 W′ 較低，n = 20 [摘要列表] | Stryd 的標準手動測試；Stryd 說手動輸入「比估計 CP 更容易出錯」[Stryd]。兩段都 < 10 分，理論上有高估 CP 的風險（§1B.1） | Ruiz-Alias 2022 / 2024 / 2025；van Rassel 2024；Olaya-Cuartero 2023；Jaén-Carrillo 2026；Stryd 8718390 |
| 5 | 3′/12′（兩段，自行車驗證） | 同一次做 3 分與 12 分全力 | 約 60–70 分 | 同一次（摘要沒寫休息多久） | 兩點 | 自行車，n = 8：兩段 vs 三段 CP 283 vs 282 W、W′ 18.7 vs 18.3 kJ（先熟悉 2 次）[摘要] | **沒有跑步資料**；Stryd／Palladino 3′/12′ **未驗證** | 12 分段比 9 分段長，短段偏差較小（Mattioni Maturana 2018） | Simpson & Kordi 2017 |
| 6 | Stryd 6/3 圈 | 400 m 跑道 6 圈、3 圈全力 | 約 60 分（估） | **未驗證** | 距離-時間兩點 | 無資料 | 無 | Stryd 稱為「測 CP 衝擊最小的方式」[Stryd] | Stryd 6879345、8718390 |
| 7 | 單次到場 3 趟（Galbraith） | 3600、2400、1200 m | 約 90–100 分（休 30）或約 2 h（休 60） | 30 或 60 分 | 距離-時間線性 | CS vs 24 h 間隔的跑步機 TTE：沒差，r = .89（30 分）/ .82（60 分），SEE 0.14 m/s；D′ 無相關（r = .13 / .33），SEE 88 m [摘要] | n = 10 男性跑者；重測 CV：CS 0.4 %、D′ 13 % [摘要] | 好；用 GPS／Stryd 配速；沒研究功率 | Galbraith 2014 |
| 8 | 單次 3 段 TT、休 30 分（Triska / Karsten，自行車） | 12、7、3 分（Karsten）或 10、5、2 分（Triska） | 約 100–110 分 | 30 分 | 1/t 或功-時 | Triska 2021，n = 9：30 分 vs > 24 h，CP、W′ d < 0.1 [摘要]。Karsten 2014 戶外，n ≈ 14：CP SEE 4.5 %、LoA −26 到 +29 W、CV 2.4 % [摘要] | 只有自行車 | 可以照做；沒有跑步驗證 | Triska 2021；Karsten 2014 |
| 9 | 單次 3 段 TT、休 60 分（Triska 2017） | 12、7、3 分 | 約 2.5 h | 60 分被動 | 1/t | 熟悉後信度：CP CV 2.6 %、W′ CV 8.2 % [摘要列表] | 自行車，n = 10 | 太長 | Triska 2017 |
| 10 | 分日 TT（近似參考標準） | 3–4 趟 1200–4400 m，或 3/7/12 分，分不同天 | 3–4 天 × 約 40 分 | ≥ 24–48 h | 2 參數雙曲線／線性 | 跑步（Stryd），n = 10：CS 對應功率 271 W vs CP3TT 270 W，ICC .98 [摘要]。Kranenburg 1996，n = 9 菁英：CS 293 m/min = 10 km 比賽速度，r = .92 [摘要] | 同左 | 最準；負擔最大 | van Rassel 2024；Kranenburg 1996 |
| 11 | 比賽 / 訓練資料（Stryd Auto-CP） | 約 90 天 PDC 的最佳值，舊的降權。Stryd 建議的測試組：10 s、2、10、20 分（或 5 K），「最多 5 次跑步」，每 90 天重測 | 0 額外 | — | Stryd 專有；文獻用 3/7/12 分最佳段做線性 | 跑步：訓練資料 CP 281 W vs TT 290 W，CP r = .916，W′ 差 [摘要]。自行車 3/7/12 分訓練最佳段：CP SEE 5.2 %、LoA −34 到 +44 W [摘要]。自行車濾過的比賽 MMP：與測試差 5–7 W，r = .87–.92，n = 13 [摘要列表] | Hunter 2023，n = 23 [摘要]；Smyth & Muniz-Pumares 2020（> 25,000 人，400/800/5000 m 最佳）：全馬預測誤差 7.67 %，全馬以 84.8 ± 13.6 % CS 完成 [摘要] | 內建；準不準取決於紀錄裡有沒有真正的全力 | Stryd 6879345、6879351；Hunter 2023；Karsten 2014；Spragg 2023；Smyth 2020 |

補充：比賽當測試時的換算（**[廠商]**，`racepower-v2.md` §2.1.1 已核對）：Stryd 表以 10 K 功率為 100 %，
5 K 103.8 %、半馬 94.6 %；Stryd 說 CP 約是 30–70 分鐘的功率。業餘跑者 10 K 多在 40–60 分，所以
**10 K 平均功率 ≈ CP**（Kranenburg 1996：菁英 CS = 10 km 速度 **[確立]**），5 K 平均 ≈ 1.04 × CP。
半馬 ≈ 0.97 × CP（Olaya-Cuartero 2023 **[確立]**，n = 9）。

### 3.2 休息要多久

- **30 分鐘對 CP 夠，對 W′ 不夠穩**：
  - Karsten 2016（自行車，n = 9，30 分 / 3 h / 24 h）：休 30 分時 CP 預測誤差 3.7 %（LoA −27 到 +22 W），
    **W′ 誤差 32.9 %** [摘要]。
  - Triska 2021：30 分 vs > 24 h，CP、W′ 都沒差，VO2 動力學、NIRS 也沒差 [摘要]。
  - Galbraith 2014（跑步）：30 與 60 分對 CS 都有效，對 D′ 無效 [摘要]。
  - Ruiz-Alias 2024（跑步、Stryd、休 30 分）：同次與分日沒差；先 9 分只讓 9 分段稍高 [摘要]。
- **W′ 恢復動力學**：力竭後兩相恢復，時間常數約 11 s 與 256 s（單相擬合約 104 s）；W′bal 模型（τ ≈ 524 s）在
  前 5 分鐘低估恢復（Caen 2021 [摘要列表]）。反覆力竭後慢相變慢：377 → 549 s（Chorley 2022 [摘要列表]）。
  Caen 2019：短休息後 W′bal **低估**實際恢復 [摘要列表]；Black 2023 則是 W′bal **高估**恢復
  （實際 6.3 kJ vs 預測 9.8 / 16.9 kJ）[摘要列表]。
- **推論（自組）**：30 分鐘是慢相時間常數的 4–5 倍以上，對 CP 夠。**今天的 16.5 分鐘**約是慢相（256–549 s）的
  2–4 倍：W′ 大概恢復 85–98 %，但沒有任何研究驗證過 < 30 分的單次兩段測試；加上 ∂CP/∂P12 = 4/3，
  第二段少 3 % 就讓 CP 低 4 %。**不建議把恢復縮短到 30 分以下**；嫌長就換流程（§4），不要壓縮休息。
- 其他：熟悉 2 次後信度明顯改善 [摘要]；前面先做過 severe 運動會降低 W′ 但不改變 CP
  （Parker Simpson 2012 [摘要列表]）。

### 3.3 為什麼「先短後長」和 Stryd 的「先長後短」都可以

- Stryd 9/3 先做 9 分；Ruiz-Alias 2024 發現順序只讓 9 分段 +4.9 W、CP 偏差 < 5 % [摘要]。
- 3′/12′ 先做 3 分（使用者筆記）。以 §1 的敏感度：殘留疲勞落在 12 分段時，CP 受影響最大
  （∂CP/∂P12 = 4/3），落在 3 分段時最小（1/3）。所以若採「先長後短」（12′ 再 3′），恢復不足的後果主要是
  W′ 偏低、CP 略高，比較不傷 CP。**[自組]** 這是代數推論，沒有跑步研究比較過 3/12 的順序。

### 3.4 不完美測試的分析

- **文獻現況**：沒找到驗證過的「用心率或配速判定非全力」規則，也沒找到「單段 + W′ 先驗」的驗證研究。
  以下是 Stryd 的做法與我們的推論。
- **Stryd「CP Accuracy Flow」[Stryd]**（13730600）：看每次跑步在 CP 以上的時間、PDC 疲勞與使用者回報的 RPE；
  RPE 9–10 時期待「強證據」在 CP 以上。沒公布數字門檻。
- **W′ 先驗**（已驗證，Stryd 9/3）：業餘女 6.4 ± 2.2 kJ、男 13.1 ± 4.0 kJ（Ruiz-Alias 2025 [全文]）；
  田徑場高於跑步機、碎石低於田徑場／路 [摘要列表]。單段時 `CP = P − W′/t`；12 分段 W′ ±4 kJ → CP ±5.6 W；
  20 分段 → ±3.3 W（越長的單段越不依賴先驗）。
- **一致性檢查**（文獻）：CP 應落在 10 分與 20 分功率之間（Ruiz-Alias 2023 IJSM [摘要列表]）；
  ≈ 30 分功率、≈ 0.95 × 20 分功率（Ñancupil-Andrade 2024 [摘要]）；半馬功率 ≈ 97 % CP（Olaya-Cuartero 2023 [全文]）。
- **非全力判讀（自組，要用使用者自己的資料校正）**：P3 ≤ P12（違反模型）；3 分段最高心率遠低於 12 分段
  （今天差 25 bpm）；長段最後 1 分明顯高於平均（今天 +11 %，前面有保留）。
- **歷史包絡線當下限**：訓練／比賽的 3/7/12 分最佳段估 CP，SEE 約 5 %（Karsten 2014 [摘要]）；
  濾過的比賽 MMP 比固定時長好（Spragg 2023 [摘要列表]）。包絡線來自非全力紀錄時只會低估，所以可以當下限。
- **模型與時長**：跑步（Stryd）功-時與 1/t 模型的最短有效組合是 3–30 分（Ruiz-Alias 2023 EJAP [摘要列表]）；
  3 參數模型高估 TTE [摘要列表]。

---

## 4. 建議的三個選項

| 選項 | 流程 | 總時間 | 準確度（CP） | W′ | 依據 | 取捨 |
|---|---|---|---|---|---|---|
| **快速** `quick` | 暖身 12 分（含 3 趟 20 秒加速）→ **20 分全力** → 緩和 5 分 | **約 37 分** | SEE 6.67 W（約 3 %），CP ≈ 0.95 × P20 | 不測；用先驗 | Ñancupil-Andrade 2024（跑步、Stryd、n = 15）**[確立]** | 最短的「有跑者驗證」流程；同時補上 WKO5 mFTP 最缺的 15–20 分點（§2.3），mFTP 會跟著變準。缺點：20 分全力很痛；沒有 W′ |
| **標準** `standard` | 暖身 15 分 → **12 分全力** → 休 30 分（走或極慢跑）→ **3 分全力** → 緩和 10 分 | **約 70 分** | 兩點；9/3 版本 CP 與 VT2 差 3.5 W、18/19 人在 5 % 內 | 有（但 30 分休息時 W′ 誤差可到 33 %） | 9/3：Ruiz-Alias 2022/2025、Olaya-Cuartero 2023 **[確立]**；3/12：只有自行車（Simpson & Kordi 2017）；12 分比 9 分少一點短段高估（Mattioni Maturana 2018）**[外插]** | 拿到 CP 和 W′（賽事功率 F2 需要 W′）。時間最長；**休息不能縮**（§3.2） |
| **用比賽** `race` | 5–10 K 比賽或計時跑（平路） | 0 額外（比賽本身 40–60 分） | 10 K ≈ CP（Kranenburg 1996 **[確立]**，菁英）；5 K ≈ 1.04 × CP、半馬 ≈ 0.97 × CP（Stryd 表 **[廠商]**、Olaya-Cuartero 2023） | 不測 | 同左 | 不多花時間、動機最強；但路線、天氣、配速策略會影響；越野賽不適用 |

**建議預設**：`standard` 保留為預設（重現現在的行為，也給 W′）；**使用者嫌長時選 `quick`**，而不是把標準流程的
休息縮短——今天的 16.5 分休息 + 非全力的 3 分，得到的資訊其實和一次 12 分全力差不多（§1），卻花了 48 分。

**不推薦的**：3MT（最短，約 20–25 分，但戶外 Stryd 很難做到「從頭全力」的配速，自行車會高估 CP，跑步 D′ 低估
**[確立]**）；Stryd 估計 CP（沒有驗證資料）；單次 3 趟（90 分以上）。

**今天這次怎麼用**：12 分段當「單段 + W′ 先驗」→ CP 約 204 W（W′ 13.1 kJ），區間 201–211 W，標「參考」。
下一次選 `quick` 做 20 分全力，可以同時驗證這個數字並把 mFTP 拉到正確位置。

---

## 5. App 設計

### 5.1 偏好鍵 `plan.prefs.cp_test_protocol`

| 值 | 顯示名稱 | 預設 |
|---|---|---|
| `quick` | 快速：20 分全力（約 37 分鐘） | |
| `standard` | 標準：12 分 + 休 30 分 + 3 分（約 70 分鐘） | ✓（現在的行為；順序改成先長後短，§3.3） |
| `race` | 用比賽：5–10 K 比賽或計時跑 | |

- `backend/settings/repository.py`：DEFAULTS 加 `"plan.prefs.cp_test_protocol": "standard"`，
  ENUMS 加 `("quick", "standard", "race")`。
- `backend/engine/plan_prefs.py`：`KEY_FIELDS` 加 `"plan.prefs.cp_test_protocol": "cp_test_protocol"`，
  `Prefs.cp_test_protocol: str = "standard"`。預設值讓 `Prefs().active` 仍為 False，沒開過面板的人課表不變。
- 「課表偏好」面板（`backend/static/plan.html` 的偏好區）加一個三選一；每個選項下面一行寫時間與準確度的取捨（§4 表）。

### 5.2 週課表：每個流程怎麼產生 session

`overview.week_plan`（overview.py 541–545）改成查表，不再寫死：

```python
CP_PROTOCOLS = {   # engine/cp_protocols.py（新檔）
  "quick":    dict(title="CP 測試 20 分全力", minutes=37, tss=45,
                   target="20 分鐘全力、配速平均；最後 2 分可以加速",
                   detail="暖身 12 分（含 3 趟 20 秒加速）；20 分全力；緩和 5 分。平路或田徑場"),
  "standard": dict(title="CP 測試 12 分 + 3 分", minutes=70, tss=65,
                   target="兩段都全力、配速平均；中間休 30 分鐘（不要縮短）",
                   detail="暖身 15 分；12 分全力；休 30 分（走或極慢跑）；3 分全力；緩和 10 分。平路或田徑場"),
  "race":     dict(title="CP 測試：5–10 K 比賽或計時跑", minutes=45, tss=60,
                   target="全力比賽；平路、少轉彎",
                   detail="暖身 15 分；5 K 或 10 K 全力；緩和 10 分"),
}
```

- minutes = 暖身 + 各段 + 恢復 + 緩和（standard：15 + 3 + 30 + 12 + 10 = 70；現在寫 60 但 COROS 步驟其實是 70，
  順便修正）。
- TSS 估計（自組，`TSS = Σ 分鐘 × IF² × 100/60`；暖身 IF 0.70、恢復 0.55、緩和 0.65、3 分 1.20、12 分 1.05、
  20 分 1.00）：quick ≈ (12·0.49 + 20·1.00 + 5·0.42)·1.667 ≈ **47 → 45**；standard ≈ (15·0.49 + 3·1.44 + 30·0.30 + 12·1.10 + 10·0.42)·1.667
  ≈ **64 → 65–70**；race 5 K（約 20 分 IF 1.05）+ 25 分 ≈ **55–60**；10 K（約 42 分 IF 1.0）+ 25 分 ≈ **85**。
- `race`：只有在 `goals` 裡 21 天內有一場 B / C 賽（或使用者手動）時，把那場比賽標成測試；
  **不自己排一場比賽**。沒有比賽時退回 `quick`，並寫一條 note。
- `plan_prefs.py` `NOTE_TEST`（71 行）與 341 行的上限豁免：文字改成依流程（`quick` 37 分通常不會超過上限，不需豁免）。
- `plan_store.DEFAULT_TITLES["test"]`（23 行）改成依偏好。
- `status.py` 590 行的 act 文字改成依偏好（「排一次 CP 測試（{流程名稱}）」）。

**protocol 存在哪裡**：選項 A（建議）——在 session 上加欄位 `protocol`（`db/models.py` PlanSession 加一欄
`protocol: Optional[str]`，一個 migration；`reconcile.FIELDS` 加 `"protocol"`，讓改期／重新產生時帶著走；
`plan_store._to_dict/_from_dict` 讀寫）。選項 B——只靠標題（`coros_workouts._test_steps` 已經在解析標題），
不用 migration，但使用者改標題就壞。A 的成本是一次 migration 與 reconcile 的一個欄位；換到的是偵測與計算不再猜。

### 5.3 COROS 步驟（`backend/sync/coros_workouts.py::_test_steps`）

改成依 `s["protocol"]`（沒有就從標題推：有「+」→ standard、「20 分全力」→ quick、「比賽」→ race；
舊標題「3 分 + 12 分」照原順序產生，相容已存的 session）：

| 流程 | 步驟 |
|---|---|
| quick | 暖身 12 分（HR ≤ AeT）→ 訓練 20 分「20 分全力」**不設功率範圍（開放）** → 緩和 5 分 |
| standard | 暖身 15 分 → 訓練 12 分「12 分全力」開放 → 休息 30 分（HR ≤ AeT）→ 訓練 3 分「3 分全力」開放 → 緩和 10 分 |
| race | 不推（`Unsupported("比賽不推")`），同 `kind == "race"` |

- **全力段不要設功率範圍**。現在 3 分段目標是 `power(th, 1.10, 1.30)`：用舊 CP（176 W）算就是 194–229 W，
  手錶到上限會叫，等於叫人不要超過 229 W——今天 3 分只跑 217 W 可能就是這樣。全力段改成開放目標，
  名稱寫「全力」；要給參考就放在說明文字（「預估 ≥ {1.15·CP} W」）。
- 緩和從寫死 10 分改成讀 detail。
- 測試：`backend/tests/test_coros_workouts.py::test_cp_test_structure` 改成三個流程各一個。

### 5.4 事後偵測：先認課表，再猜

現在：`overview.week_plan` 用「≥ 10 分鐘在門檻以上」把 test session 標完成（616–617 行，
`hard ≥ HARD_SESSION_S`）；`workout_review.classify` 另外用標題 regex、當天門檻列、功率型態猜 `test_cp`。
兩者不相通，今天的測試就被判成 easy。

新順序（`workout_review.classify` / `session_type`）：

1. **課表對應**：找 stored plan session，`kind == "test"`、`state == "done"`、`done_by.index == w.idx`
   → `test_cp`，並拿 `session.protocol`。（`plan_store` 讀 DB；`classify` 需要一個
   `plan_sessions_by_activity(ds)` 的快取查表，放在 `plan_store.py`。）
2. 同一天有未完成的 test session，而且這次活動有「≥ 3 分鐘 ≥ 1.05 × 舊 CP」的段 → 也算，protocol 取 session 的。
3. 之後才是現在的規則（當天門檻列、標題、`looks_like_cp_test`）。

`week_plan` 的完成判定（616–617 行）對 `kind == "test"` 改成：同一天、耐力類、而且
「≥ 10 分鐘 ≥ 0.95 CP」**或**「最好的 720 s ≥ 0.98 CP」（quick 的 20 分全力與 standard 的 12 分段都會滿足；
`hard` 用舊 CP 算，新 CP 高時也會滿足）。

### 5.5 每種流程怎麼算

新函式 `workout_review.cp_result(t, power, hr, protocol, prior)`，取代 `cp_test()`：

**找段**：不再在整個檔案找最好的 180 s / 720 s。改成：

- 有 lap 且 lap 長度在目標 ±10 % 內 → 用 lap（使用者按 lap 或 COROS 課表自動分段）。
- 沒有 lap → 找**不重疊**的窗：standard 先找最好的 720 s，再在它**之後**（舊標題「3 分 + 12 分」則在之前）、
  至少隔 10 分鐘的地方找最好的 180 s。`separate` 變成硬條件。quick 找最好的 1200 s。

**算**：

| 流程 | 公式 | 輸出 |
|---|---|---|
| standard | `CP = (P12·720 − P3·180)/540`、`W′ = (P3 − CP)·180`（§3.0 兩點式） | CP、W′、`method="2pt"` |
| quick | 主：`CP = 0.95·P20`（Ñancupil-Andrade 2024）；交叉檢查：`CP = P20 − W′prior/1200` | CP、兩者差、`method="tt20"` |
| race | `CP = P_race / f`：10 K f = 1.00、5 K f = 1.04、半馬 f = 0.97（§3.1 補充）；其他距離用 Riegel D1 反推（`racepower/riegel.py`） | CP、`method="race"` |
| standard 失敗時的退回 | `CP = P12 − W′prior/720` | CP、區間、`method="1pt_prior"`、品質「參考」 |

**W′ 先驗**（依序）：①90 天內最近一次**有效**兩點測試的 W′；②Ruiz-Alias 2025 的業餘平均（男 13.1 ± 4.0、
女 6.4 ± 2.2 kJ），用 ±1 SD 當區間；③（交叉）RWC 帶（`cp.py` RWC_BANDS）。90 天包絡線的線性擬合 W′
**不當先驗**：今天的例子顯示它會因為非全力的 20 分點而偏到 17.5 kJ（§1B.2）。
輸出同時給 W′ 帶兩端算出的 CP 區間。

**品質檢查**（每項不過就降一級，最後標「可信 / 參考 / 不採用」）：

| 檢查 | 規則 | 依據 |
|---|---|---|
| 模型一致 | P3 > P12，W′ 在 Ruiz-Alias 2025 平均 ± 2 SD 內 | 兩點模型的前提；§3.4 |
| 短段心率 | 3 分段最高心率 ≥ 12 分段最高心率 − 8 bpm | **[自組]**，§3.4；門檻要用使用者資料校正 |
| 長段配速 | 最後 1 分功率 ≤ 該段平均 × 1.08（太大的衝刺 = 前面有保留） | **[自組]**，§3.4 |
| 恢復 | standard：兩段間隔 ≥ 25 分 | §3.2 |
| 一致性 | quick：`0.95·P20` 與 `P20 − W′prior/1200` 差 ≤ 3 % | §3.4 |
| 包絡線下限 | 結果 CP ≥ max(0.95 × 90 天 20 分 MMP, 90 天 30 分 MMP) − 2 % | §3.4（包絡線只會低估；CP ≈ 0.95·P20 ≈ 30 分功率） |

- standard 失敗（例如今天：P3 < P12）→ **自動退回單段算法**（只用 12 分段 + W′ 先驗），標「參考」，
  並寫清楚原因（「3 分段心率最高 146，比 12 分段低 25 bpm，不是全力」）。
- 結果永遠 ≥ 包絡線下限；低於下限時顯示下限並標「測試偏低，可能沒有全力」。

`cp_lines()`、`_cp()`（1239–1247 行，含「這次不是 CP 測試（3 分＋12 分全力）」字串）、`latest_cp_test()`（848 行）改用
`cp_result`，輸出多帶 `method`、`quality`、`cp_range`、`reasons`。

### 5.6 「套用這次的 CP」

現在沒有按鈕：`status.py` 611 行只叫使用者去「賽事周期」頁手動填。

- 新 API：`POST /api/plan/thresholds/apply-cp`，body `{activity_index, cp, wprime?, note}`，
  仿 `api/plan.py::apply_estimate`（282–300 行），**但門檻列的日期是測試那天**（不是今天），
  這樣 `i_testing` 的「幾天前」與 90 天新鮮度都對。
- `planning.Threshold` 加 `wprime: Optional[float]`（`THRESHOLD_FIELDS` 加上），賽事功率 F2 用它（§1B.3）。
- note 自動寫「{流程}；{method}；品質 {quality}」。
- 按鈕位置：活動檢討的 CP 區塊（`_cp` 的輸出多一個 `action`）與總覽「測試」指標（`status.py` extra.cp_test）。
  品質「不採用」時不給按鈕；「參考」時按鈕文字是「套用下限 {cp_low} W」。
- 套用後 `_notify(True)` 重建 dataset（區間、TSS 會變）。

### 5.7 時機規則（現行，已核對程式碼）

| 規則 | 位置 | 值 |
|---|---|---|
| 門檻多久算過期 | `status.py` 51 行 `TEST_DAYS_WATCH, TEST_DAYS_BAD = 42, 90` | 42 天「watch」、90 天「bad」（筆記：每 4–6 週） |
| 什麼時候排測試 | `overview.py` 522 行 `test_due = lvl("testing") in ("bad","watch") and (days_to is None or days_to > 10)` | 門檻 watch/bad，且離下一場 A 賽 > 10 天 |
| 只在哪種週 | `overview.py` 528、541 行 | base / specific 週、不是恢復週；測試取代當週的品質課 |
| 賽前窗口 | `status.py` 591–595 行 | 賽前 10–21 天是測試的好時機；< 10 天「不要測，賽後再測」 |
| 品質課 0 次 | `plan_prefs.py` 330 行 | 偏好每週 0 次品質課 → CP 測試也不排 |
| 前端說明 | `static/plan.html` 173 行 | 「每 4–6 週或賽前 10–21 天」 |

依據：這些是使用者的筆記（`SRC_NOTES`），**不是文獻**；文獻沒有定出測試頻率，4–6 週是教練慣例 **[經驗法則]**。
建議新增：

- `race` 流程：B / C 賽可以在 A 賽前 10 天以外的任何時候當測試。
- 3 天內有長距離（≥ 90 分）或品質課時不排測試（疲勞會讓 CP 低估）——**[自組]**，要不要加請使用者決定。

### 5.8 要改的檔案與函式

| 檔案 | 函式 / 位置 | 改什麼 |
|---|---|---|
| `backend/settings/repository.py` | DEFAULTS、ENUMS | 新鍵 `plan.prefs.cp_test_protocol` |
| `backend/engine/plan_prefs.py` | `KEY_FIELDS`、`Prefs`、`NOTE_TEST`、`shape()` 341 行 | 新欄位；依流程的 note 與上限豁免 |
| `backend/engine/cp_protocols.py`（新） | `CP_PROTOCOLS`、`session_for(protocol, th)` | 流程表（標題、分鐘、TSS、步驟） |
| `backend/engine/overview.py` | `week_plan` 522、541–545、616–617 行 | 依偏好產生 test session；完成判定 |
| `backend/engine/projection.py` | `_place` 169–175 行（排序已有 test） | 投影週若排測試也用同一張表（目前投影週不排測試，不變） |
| `backend/engine/plan_store.py` | `DEFAULT_TITLES`、存取、`plan_sessions_by_activity` | protocol 欄位；給 classify 的查表 |
| `backend/db/models.py` | `PlanSession` | `protocol` 欄位（migration） |
| `backend/engine/reconcile.py` | `FIELDS` | 加 `protocol` |
| `backend/sync/coros_workouts.py` | `_test_steps` | 三種流程；全力段開放目標；緩和讀 detail |
| `backend/engine/workout_review.py` | `cp_test`→`cp_result`、`looks_like_cp_test`、`session_type`/`classify`、`latest_cp_test`、`cp_lines`、`_cp` | 不重疊的段、課表優先、W′ 先驗、品質檢查 |
| `backend/engine/status.py` | `i_testing` 590、611 行 | 依流程的文字；套用按鈕的資料 |
| `backend/engine/planning.py` | `Threshold` | `wprime` |
| `backend/api/plan.py` | 新 `apply_cp` | 套用 CP（測試日期） |
| `backend/engine/racepower/athlete.py` | 290–317 行 sources / `default_cp` | F1 用 mFTP+TTE、F2 用 CP+W′（§1B.3） |
| `backend/static/plan.html` 173、`overview.html` 222、`schedule.html` 491、`racepower.html` 905 | 說明文字 | 依流程；說明 CP 與 mFTP 的差別 |
| 測試 | `test_coros_workouts.py::test_cp_test_structure`、`test_workout_review.py::test_cp_test_formula_and_detection`、`test_plan_prefs.py::test_cp_test_is_exempt_from_the_cap_with_a_note`、`test_plan_store.py` 546、561 行 | 三流程；今天這筆當回歸案例（P3 < P12 → 退回單段） |

---

## 6. 參考文獻

標註：[全文] / [摘要] / [摘要列表] / [Stryd]，意義見 §3.0。

**3 分鐘全力測試**
- Burnley M, Doust JH, Vanhatalo A. 2006. MSSE. https://doi.org/10.1249/01.mss.0000232024.06114.a6 [摘要]
- Vanhatalo A, Doust JH, Burnley M. 2007. MSSE. https://doi.org/10.1249/mss.0b013e31802dd3e6 [摘要]
- Broxterman RM et al. 2013. Respir Physiol Neurobiol. https://doi.org/10.1016/j.resp.2012.08.024 [摘要]
- Pettitt RW, Jamnick N, Clark IE. 2012. Int J Sports Med. https://doi.org/10.1055/s-0031-1299749 [摘要]
- Wright J et al. 2017. IJSM. https://doi.org/10.1055/s-0043-102944 [摘要]
- Nicolò A et al. 2017. IJSPP. https://doi.org/10.1123/ijspp.2016-0111 [摘要]
- Karsten B et al. 2014. IJSM（3MT 等速）. https://doi.org/10.1055/s-0033-1349093 [摘要]
- Quittmann OJ, Piehl MA. 2026. EJAP. https://doi.org/10.1007/s00421-026-06356-w [摘要列表]
- Bergstrom HC et al. 2013. J Sports Sci. https://doi.org/10.1080/02640414.2012.738925 [摘要列表]

**兩段 / 多段、單次到場**
- Ruiz-Alias SA et al. 2022. IJSPP. https://doi.org/10.1123/ijspp.2022-0069 [摘要]
- Ruiz-Alias SA et al. 2023. IJSM. https://doi.org/10.1055/a-2069-2192 [摘要列表]
- Ruiz-Alias SA et al. 2023. EJAP. https://doi.org/10.1007/s00421-023-05243-y [摘要列表]
- Ruiz-Alias SA et al. 2024. IJSM. https://doi.org/10.1055/a-2201-7081 [摘要]
- Ruiz-Alias SA et al. 2024. JSCR. https://doi.org/10.1519/jsc.0000000000004609 [摘要列表]
- Ruiz-Alias SA et al. 2024. EJSS. https://doi.org/10.1002/ejsc.12210 [摘要列表]
- Ruiz-Alias SA et al. 2025. EJSS. https://doi.org/10.1002/ejsc.12254（PMC11770271）[全文]
- Olaya-Cuartero J et al. 2023. JSSM. https://doi.org/10.52082/jssm.2023.526 [全文]
- van Rassel CR et al. 2024. IJSPP. https://doi.org/10.1123/ijspp.2023-0260 [摘要]
- Jaén-Carrillo D et al. 2026. EJAP. https://doi.org/10.1007/s00421-025-05840-z [摘要列表]
- Simpson LP, Kordi M. 2017. IJSPP. https://doi.org/10.1123/ijspp.2016-0371 [摘要]
- Kordi M et al. 2019. EJSS. https://doi.org/10.1080/17461391.2018.1495768 [摘要列表]
- Galbraith A et al. 2014. IJSPP. https://doi.org/10.1123/ijspp.2013-0507 [摘要]
- Triska C et al. 2017. PLoS One. https://doi.org/10.1371/journal.pone.0189776 [摘要列表]
- Triska C et al. 2021. MSSE. https://doi.org/10.1249/mss.0000000000002477 [摘要]
- Karsten B et al. 2014. IJSM（實驗室 vs 戶外）. https://doi.org/10.1055/s-0033-1349844 [摘要]
- Karsten B et al. 2015. EJAP. https://doi.org/10.1007/s00421-014-3001-z [摘要]
- Karsten B et al. 2017. J Sports Sci（休息 30 分 / 3 h / 24 h）. https://doi.org/10.1080/02640414.2016.1215500 [摘要]
- Karsten B et al. 2018. IJSPP. https://doi.org/10.1123/ijspp.2016-0761 [摘要列表]
- Kranenburg KJ, Smith DJ. 1996. MSSE. https://doi.org/10.1097/00005768-199605000-00013 [摘要]
- Nimmerichter A et al. 2017. JSCR. https://doi.org/10.1519/jsc.0000000000001529 [摘要列表]
- Muniz-Pumares D et al. 2019. JSCR（方法綜論）. https://doi.org/10.1519/jsc.0000000000002977 [摘要列表]
- Lipková L et al. 2025. Front Sports Act Living. https://doi.org/10.3389/fspor.2025.1520914 [全文]
- Anderson M et al. 2026. Sports Med. https://doi.org/10.1007/s40279-026-02410-x [摘要列表]

**20 分 / FTP、訓練資料、比賽**
- Ñancupil-Andrade AA et al. 2024. IJSM. https://doi.org/10.1055/a-2155-6813 [摘要]
- Karsten B et al. 2020. Front Physiol. https://doi.org/10.3389/fphys.2020.613151 [摘要列表]
- Borszcz FK et al. 2018. IJSM. https://doi.org/10.1055/s-0044-101546 [摘要]
- McGrath E et al. 2021. IJES. https://doi.org/10.70252/isyh9512 [摘要列表]
- Hunter B, Ledger A, Muniz-Pumares D. 2023. IJSPP. https://doi.org/10.1123/ijspp.2023-0276 [摘要]（感測器廠牌摘要未寫）
- Smyth B, Muniz-Pumares D. 2020. MSSE. https://doi.org/10.1249/mss.0000000000002412 [摘要]
- Spragg J, Leo P, Swart J. 2023. J Sports Sci. https://doi.org/10.1080/02640414.2023.2254574 [摘要列表]

**CP 與較長可持續功率、W′ 恢復**
- Bishop D, Jenkins DG, Howard A. 1998. IJSM. https://doi.org/10.1055/s-2007-971894 [摘要列表]
- Jenkins D, Kretek K, Bishop D. 1998. JSAMS. https://doi.org/10.1016/s1440-2440(09)60004-9 [摘要列表]
- Mattioni Maturana F et al. 2016. APNM. https://doi.org/10.1139/apnm-2016-0248 [摘要]
- Mattioni Maturana F et al. 2018. JSAMS. https://doi.org/10.1016/j.jsams.2017.11.015 [摘要]
- Jones AM et al. 2010. MSSE. https://doi.org/10.1249/mss.0b013e3181d9cf7f [摘要列表]
- Jones AM et al. 2019. Physiol Rep. https://doi.org/10.14814/phy2.14098 [摘要]
- Nixon RJ et al. 2021. EJAP. https://doi.org/10.1007/s00421-021-04780-8 [摘要]
- Skiba PF et al. 2012. MSSE. https://doi.org/10.1249/mss.0b013e3182517a80 [摘要列表]
- Caen K et al. 2019. MSSE. https://doi.org/10.1249/MSS.0000000000001968 [摘要列表]
- Caen K et al. 2021. MSSE. https://doi.org/10.1249/mss.0000000000002673 [摘要列表]
- Chorley A et al. 2022. EJAP. https://doi.org/10.1007/s00421-021-04874-3 [摘要列表]
- Black MI et al. 2023. MSSE. https://doi.org/10.1249/mss.0000000000003039 [摘要列表]
- Parker Simpson L et al. 2012. EJAP. https://doi.org/10.1007/s00421-011-2214-7 [摘要列表]

**Stryd [Stryd]**
- https://help.stryd.com/en/articles/6879345-critical-power-definition
- https://help.stryd.com/en/articles/8258035-estimated-critical-power
- https://help.stryd.com/en/articles/8718390-manually-enter-critical-power
- https://help.stryd.com/en/articles/6879351-power-duration-curve-pdc
- https://help.stryd.com/en/articles/13730600-cp-accuracy-flow-for-adaptive-training
- https://help.stryd.com/en/articles/8955821-stryd-race-calculations-faq
- https://help.stryd.com/en/articles/6879547-race-power-calculator

**未驗證（本文不當依據）**：Stryd / Palladino 的 3′/12′ 流程；Palladino《Power Training for Runners》的建議；
Stryd 6/3 圈的休息時間；Galbraith 2011 田徑場測試；Skiba 2012 的 τ 公式原文。
