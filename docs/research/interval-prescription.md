# 間歇處方查證：每趟長度、休息、劑量、圖表指標、同等課表庫

> 狀態：研究＋設計提案（2026-10-01），尚未實作。
> 範圍：
> - Part A：用論文查證 app 現在的 3 區 → 5 區階梯（`backend/engine/quality_gate.py` 的 `Z3`／`Z5`），提出修正版。
> - Part B：功率間歇課後能看哪些圖表和指標。
> - Part C：同等課表庫、輪替、時間上限內選課，以及手動替換。
>
> 不重做的部分：`interval-adaptation.md`（WKO5、狀態機、HRR 文獻）、台灣教練的規則（3 區先於 5 區；5 區每趟 ≥ 2 分、每週最多 2 次、間隔 ≥ 2 天）、`aerobic-base-readiness.md` §4.5（舊階梯）。這裡只引用結論。
>
> 標記方式：
> - **同儕審查**：期刊論文。「摘要」= 只讀了 Europe PMC 摘要；「全文」= 讀了全文。
> - **教練來源**：書、部落格、研討會、你的筆記，另外標出來。
> - **推估**：找不到來源，是我們自己的推論或換算。
> - **未驗證**：有人這樣說，但我沒讀到原文。
>
> 讀到全文的有：
> - Buchheit & Laursen 2013 Part I／II（作者公開的版本，martin-buchheit.net 和 paulogentil.com 的 PDF）
> - Jones & Vanhatalo 2017（PMC5371646）
> - Haugen 2022（PMC8975965）
> - Buchheit 2014（PMC3936188，表 1 從 Europe PMC 全文 XML 核對）
> - Fleckenstein 2025（PMC11743937）
>
> 其他論文只讀了摘要。

---

## 0. 結論先講

1. **5 區那四階（5×2 → 4×3 → 5×3 → 4×4 分）大致有根據。**
   - 每趟 2–4 分符合 Buchheit & Laursen：時間不確定時用 ≥ 2–3 分的固定長度。
   - 總量 10 → 16 分符合下面幾個來源：
     - Buchheit：耐力選手每堂約 10 分在 VO2max。
     - Wen 2019：每堂 ≥ 15 分效果較大。
     - Helgerud 和 Seiler 的 4×4：16 分。
   - 強度 103–112% CP 換算約是 90–95% vVO2max（推估換算，§A1.4），和 Palladino、Stryd 的 VO2max 帶一致。
   - 要修的是細節：
     - 2 分鐘以下的組休要走路或極慢跑（Buchheit：休息 < 2–3 分用被動恢復）。
     - 最後一趟後面的休息不要算進總時間。
2. **3 區那三階強度和組休有根據，但劑量和平日時間上限衝突，會讓階梯卡住。**
   - `session()` 算出的總時間：
     - 3×8 是 55 分。
     - 4×8 是 65 分。
     - 3×10 是 64 分。
   - 平日上限 45／50 分時，`trim_quality` 會先砍暖身緩和，再減組：
     - 4×8 變成 3×8。
     - 3×10 變成 2×10。
   - 減組後標題變了（例如「閾值 3×8 分」），`planned_spec` 依標題判成「不是這一階」（neutral），所以**這一階永遠不會前進**。這是從程式碼推出來的，沒用真實 DB 驗證（§A5.2）。
3. **3 區的趟可能偵測不到**，這也是從程式碼推出來的，還沒用 FIT 驗證。
   - `detect_efforts` 的門檻是 `max(0.85 CP, 1.12 × 全程功率中位數)`。
   - 3 區課的趟數時間佔一半以上，所以中位數約等於趟的功率，門檻會高於趟本身。
   - 結果會是 `bouts = []`，`interval_outcome` 判成「第 1 趟就沒到 → 目標 −5%」。
   - S0 要先用合成 FIT 測（§A5.2）。
4. **`z3b` 的來源標錯了。** Seiler 2013 的 4×8 分是「可忍受的最大強度」，約 90% HRpeak、血乳酸 9.6 mmol/L，屬於嚴重強度區，不是 88–95% CP 的 3 區。3 區的根據應該改成：
   - Haugen 2022 的 threshold intervals
   - Daniels 巡航間歇
   - Palladino near-threshold
   - CTS TempoRun
5. **修正版階梯（§A5.3）：**
   - 3 區（劑量都放得進 45 分）：3×6 → 3×8 → 2×12 分。
   - 5 區：5×2 → 4×3 → 5×3 → 4×4 分，休息方式改掉。
   - 每一階附 2–4 個同等課表（Part C），平日 45 分也放得下。
   - 「3 區做 3 堂達標才開 5 區」仍然是推估。
6. **最值得做的課後指標（Part B）：**
   1. 每趟功率 vs 目標帶與達標率。
   2. 同一份課表跨堂的「同功率下的心率」。Buchheit 2014：運動中心率的雜訊約 3%，是最可靠的體能訊號。
   3. W′bal／dFRC 每趟的最低點，只顯示。
   4. 目標區時間（TIZ）vs 計畫。
   5. 掉速 Sdec（Glaister 2008）。
   6. HRR60 只看趨勢：雜訊約 25%（Buchheit 2014），而且適應和過量訓練時都會變快。
   7. 加 session RPE（Foster 2001）。

---

# Part A：間歇處方的證據

## A1. 依目標：每趟時間與強度

### A1.1 VO2max（「紅區時間」）

| 來源 | 類型 | 重點數字 |
|---|---|---|
| Buchheit & Laursen 2013 Part I（全文） | 同儕審查回顧 | 每堂要在 > 90% VO2max「至少幾分鐘」；耐力選手的目標約 **10 分 T@VO2max**（結論 2b）。<br>測不到「到達 VO2max 所需時間」時，用 **≥ 2–3 分的固定每趟時間**。<br>單趟要 ≥ 95% vVO2max，重複多趟時 ≥ 90% vVO2max 也可以（§3.1.1.1–3.1.1.2）。<br>偏好「4 分 @ 90–95% v/pVO2max」（結論 3）。 |
| Buchheit & Laursen 2013 Part II 表 1（全文） | 同上 | 長間歇：> 2–3 分、≥ 95% vVO2max，6–10×2 分／5–8×3 分／4–6×4 分，預期 T@VO2max > 10 分。<br>組休二選一：≤ 2 分被動，或 ≥ 4–5 分 @ ≤ 60–70% vVO2max。<br>短間歇：≥ 15 s @ 100–120% vVO2max，2–3 組 ≥ 8 分，組間 ≥ 4–5 分。<br>這些數字針對訓練有素的選手。 |
| Seiler & Sjursen 2004（摘要） | 同儕審查 | 24×1、12×2、6×4、4×6 分，工休比 1:1。<br>1 分的趟峰值 VO2 只到 82%；2–6 分的趟到 92%。<br>結論是 **3–5 分最好**。RPE 在每種課都約 17。 |
| Helgerud 2007（摘要） | 同儕審查 | **4×4 分 @ 90–95% HRmax，休 3 分 @ 70% HRmax（主動）**，每週 3 次、8 週。對象是中等訓練程度的人。<br>VO2max 進步比 LSD 和乳酸閾值組多（同總功）。 |
| Seiler 2013（摘要） | 同儕審查 | 休閒自行車手，每週 2 次、7 週，三組都用可忍受的最大強度：<br>- 4×16 分：88% HRpeak、4.9 mmol/L<br>- 4×8 分：90% HRpeak、9.6 mmol/L<br>- 4×4 分：94% HRpeak、13.2 mmol/L<br>4×8 分的 VO2peak 進步 11.4%，4×4 分 5.5%，4×16 分 5.6%。<br>"Accumulating 32 min of work at 90% HR max induces greater adaptive gains than accumulating 16 min of work at ~95% HR max" |
| Wen 2019 統合分析（摘要） | 同儕審查 | 長間歇（≥ 2 分）、高總量（≥ 15 分）、中長期（≥ 4–12 週）效果較大。<br>短間歇（≤ 30 s）、低總量（≤ 5 分）對一般人也有效。 |
| Bacon 2013 統合分析（摘要） | 同儕審查 | 間歇訓練平均讓 VO2max +0.51 L/min；用較長間歇的 9 篇研究約 +0.8–0.9 L/min。<br>納入條件是每週 ≥ 3 天、≥ 10 分高強度、工休比 ≥ 1:1。 |
| Fleckenstein 2025（全文） | 同儕審查 | 高度訓練的中距離跑者，> 90% VO2max 的時間：<br>- 4×3 分 @ 95% vVO2max，休 3 分 @ 50%：328 ± 147 s<br>- 24×30 s @ 100%，休 30 s @ 55%：201 ± 268 s<br>短間歇比較少。 |
| Billat 2000（摘要） | 同儕審查 | 30-30（100%／50% vVO2max）在 VO2max 的時間 7′51″；vΔ50 持續跑只有 2′42″。 |
| Billat 2001（摘要） | 同儕審查 | 中年跑者，平常只練 LSD。15-15 用 90/80% 或 100/70% vVO2max，在 VO2max 約 14 分；110/60% 只有 7 分。<br>這些人的 critical velocity 是 85.6 ± 1.2% vVO2max。 |
| Billat 1999（摘要） | 同儕審查 | 5 趟 @ vVO2max，每趟 50% tlim，1:1 恢復 @ 60%；每週 1 次、4 週，vVO2max 20.5 → 21.1 km/h。<br>改成每週 3 次的 4 週，表現沒有再進步，正腎上腺素上升。<br>tlim @ vVO2max 約 301 s。 |
| Esfarjani & Laursen 2007（摘要） | 同儕審查 | 中等訓練程度的跑者，每週 2 次、10 週：<br>- 8 × 60% Tmax @ vVO2max（1:1）：VO2max +9.1%、vLT +11.7%、3000 m −7.3%<br>- 12×30 s @ 130%，休 4.5 分：VO2max +6.2% |
| Midgley & McNaughton 2006（摘要） | 同儕審查回顧 | 要拉長 T@VO2max：<br>- 工作 90–105% vVO2max；恢復從 50% vVO2max 到乳酸閾值速度。<br>- 工作和恢復都 15–30 s。<br>- 暖身 10–15 分、比乳酸閾值慢 1–2 km/h、暖身完不要停。 |
| Midgley, McNaughton & Jones 2007（摘要） | 同儕審查回顧 | 對訓練有素的長跑者，「科學證據不足以給出有效的訓練建議」。下面所有建議都要帶著這個前提。 |

**教練來源：**
- Koop／CTS RunningIntervals：RPE 10，每趟 2–4 分，總量 12–24 分，工休比 1:1。例："6x3min RI, 3min recovery"（https://trainright.com/decoding-ultramarathon-interval-workouts/）。
- Palladino 最大有氧功率間歇：103–105% CP，個人化 103–109%；每趟 2.5–3 分；工休比 1:1，進階 2:1（`65 ⚡ 功率訓練/功率區間說明與訓練目的 --star.md:132-143`）。
- Stryd 的 "VO2 Max workouts"："104–110%+ of your CP… typically 15 seconds to 3 minutes"（https://help.stryd.com/en/articles/14360092）。
- Daniels：
  - I 強度每趟 3–5 分；從休息開始要 90–120 秒才到 VO2max（`60 🏃 有氧訓練/間歇訓練.md:35-36`）。
  - 每趟不超過 5 分（`70 ⏳ 周期化訓練/越野跑周期化訓練(晏慶、K天王、丹尼爾).md:100`）。
- WKO（研討會筆記，`interval-adaptation.md` §2）：VO2max 3–8 組，總量 12–30 分，組休 1:1；CW 研討會說累積 12–15 分就有效。
- 台灣教練：5 區每趟至少 2 分。

### A1.2 乳酸閾值／3 區

| 來源 | 類型 | 重點數字 |
|---|---|---|
| Haugen 2022（全文） | 同儕審查回顧，整合菁英實務 | Threshold intervals：每趟 3–15 分，約半馬配速或稍快。<br>典型課表："10–12 × 1000 m with 1 min. recovery"、"6–8 × 1500–2000 m with 1–2 min. recovery"。<br>總時間 30–75 分（菁英）。 |
| Casado 2023（摘要） | 同儕審查（描述性） | 挪威式 LGTIT：每週 3–4 堂閾值間歇加 1 堂 VO2max，血乳酸目標 2–4.5 mmol/L，每 1–3 趟量一次。<br>對象是週跑量 150–180 km 的世界級選手，**不能照搬**到平日 40–50 分的休閒跑者，只借用「閾值下、短休息、累積時間」的原則。 |
| Casado 2021（摘要） | 同儕審查 | 菁英跑者前 7 年的表現，最能預測的是輕鬆跑量和 tempo；短間歇也有關，長間歇相關性弱。 |
| Seiler 2013 | 同儕審查 | 4×16 分 @ 88% HRpeak、4.9 mmol/L 的進步和 4×4 分相當（5.6% vs 5.5%）。 |
| Smith & Jones 2001（摘要） | 同儕審查 | 跑步的 critical velocity 14.4 km/h、MLSS 13.8 km/h、乳酸轉折點 13.7 km/h。平均差不多，但個人間的一致性太差，不能互相代替。 |
| Tschakert & Hofmann 2013（摘要） | 同儕審查 | 用閾值（轉折點）模型開強度，比用 %HRmax 或 %VO2max 準。 |
| Meyler 2021（摘要） | 同儕審查回顧 | 以生理閾值開強度，反應比以最大值開更一致。這支持 app 用 %CP 開課表。 |

**教練來源：**
- Daniels：
  - 節奏跑是連續 20 分；「對大部分跑者而言 20 分鐘的 T 配速通常練一次就很夠了」（`丹尼爾的跑步方程式筆記 還有課表.md:33`）。
  - 少於 20 分、分幾趟、中間有休息的叫巡航間歇（:45）。
  - T 不超過週跑量 10%（:75）。
  - 巡航間歇大約每 5 分休 1 分：二手來源（書評網站），**未驗證**。
- Palladino near-threshold：98–100% CP（個人化 99–101%），每趟 7–10 分，工休比 3:1–4:1；進步靠拉長每趟或縮短休息（`功率區間說明與訓練目的 --star.md:50-63`）。
- Palladino 3 區：「約 15% 的訓練時間為 Zone 3（大約 94‑101% FTP/CP）」（`70 ⏳ 周期化訓練/palladino基礎期訓練.md:63`）。
- CTS TempoRun：RPE 8–9，每趟 8–20 分，總量 30–60 分，工休比 2:1，例 "3x12min TR, 6min recovery"。
- Stryd 的區間分法：
  - Threshold zone 90–100% CP。
  - "Cruise Intervals: 94–100% CP"、"Tempo Run: 80–95%"、"Threshold Run: 95–105%"（Stryd Help Center）。

### A1.3 無氧能力（不在基礎期階梯裡）

- Buchheit & Laursen Part I：SIT 是 30 s 全力，休 2–4 分被動。
- Esfarjani 2007：12×30 s @ 130% vVO2max，休 4.5 分。
- Daniels R：工休比 1:2–1:3（`丹尼爾的跑步方程式筆記 還有課表.md:115`）。
- Uphill Athlete 坡衝刺（神經肌肉訓練，不是 5 區）：8–10 s 全力、坡度 ≥ 20%、6–8 趟、休 2–3 分（`aerobic-base-readiness.md` §2.1 已核對）。

這些都不屬於 5 區「每趟 ≥ 2 分」的範圍。app 的基礎期不排。坡衝刺可以放在暖身或輕鬆日（Part C）。

### A1.4 換算到這位選手（CP 204 W）

- **平路速度：** 0.0126 m/s/W（`docs/spec/wko5-coros-sync.spec.md:537-538` 的實測，Stryd 路跑 90 天中位數），CP 對應約 6:29 /km。
  - 「功率和平路速度成正比」是推估，越野和上坡不適用。
- **W′：** app 沒有實測值。下面用 `workout_review.CP_TEST_WPRIME_PRIOR` 的 13.1 kJ（Ruiz-Alias 2025 業餘男子的平均）當例子，屬推估。
- **pVO2max 約 117–122% CP**（推估）：
  - Billat 2001：CV ≈ 85.6% vVO2max，所以 vVO2max ≈ 1.17 × CV。
  - Billat 1999：vVO2max 能撐約 5 分；用兩參數模型算 5 分功率 = CP + W′/300 ≈ 248 W ≈ 122% CP。
  - 由此推得 90% vVO2max ≈ 105–110% CP，95% ≈ 111–116% CP。
  - Stryd 的 CP 不等於實驗室的 CS。Ruiz-Alias 2024 只證明 Stryd 的 CP 模型對 30／60 分功率的預測誤差 ≤ 2.6%。

| %CP | 功率 | 平路配速 | 400 m | 800 m | 1000 m |
|---|---|---|---|---|---|
| 88% | 180 W | 7:22 /km | 2:57 | 5:54 | 7:22 |
| 92% | 188 W | 7:02 /km | 2:49 | 5:38 | 7:03 |
| 95% | 194 W | 6:49 /km | 2:44 | 5:28 | 6:50 |
| 100% | 204 W | 6:29 /km | 2:36 | 5:11 | 6:29 |
| 105% | 214 W | 6:10 /km | 2:28 | 4:56 | 6:11 |
| 110% | 224 W | 5:53 /km | 2:21 | 4:43 | 5:54 |
| 112% | 228 W | 5:47 /km | 2:19 | 4:38 | 5:47 |

**意思：**
- 菁英課表裡的「400 m」，對這位選手大約是 2:20–2:45，等於 5 區的「2 分鐘趟」。
- 「800 m／1000 m」大約 4:40–7:00，已經是 4–6 分的長趟。
- **一律用時間開課表，不用距離。** 依據：
  - Daniels 說以距離為單位的課表對每個人的壓力差很多（`越野跑周期化訓練(晏慶、K天王、丹尼爾).md:115-117`）。
  - Buchheit 建議用時間個人化。

---

## A2. 休息

### A2.1 工休比、主動或被動、組休長度對 T@VO2max 的影響

| 來源 | 重點 |
|---|---|
| Buchheit & Laursen Part I §3.1.1.3（全文） | "passive recovery is therefore recommended when the relief interval is less than 2–3 min in duration"。要用主動恢復的話，組休至少 3–4 分。<br>受過訓練的跑者自選組休，大多選「走路、約 2 分」；休 4 分沒有比休 2 分好。 |
| Seiler & Hetlelid 2005（摘要） | 6×4 分（5% 坡）。休 1 → 2 分時配速 14.4 → 14.7 km/h；休 4 分沒有更好。VO2 以休 2 分最高。<br>"Approximately 120 s of active recovery may provide an appropriate balance"。 |
| Buchheit Part I §3.1.2（全文） | 短間歇（30/30）的組休強度約 70% vVO2max 最好。<br>分組（每 6 趟休 4 分）會讓 T@VO2max 佔總時間的比例變低（Tardieu-Berger 2004：36% vs 58% tlim）。 |
| Dupont 2004、Thevenet 2007／2008、Ben Abderrahman 2013 | 被動恢復讓 tlim 變長；恢復強度 50–67% MAV 時 T@VO2max 差不多，84% 時明顯變少（`interval-adaptation.md` §3.2）。 |
| Chidnok 2013（摘要） | 60 s 工作，被動休 18／30／48 s，能撐的時間 304／516／847 s。休越長，CP 以上能做的總功越多。 |
| Ma 2023 統合分析（摘要，菁英） | 組休 ≥ 2 分、恢復強度 ≤ 40% 的 HIIT 有正向效果。對象是國家級選手，只當旁證。 |
| Buchheit Part I（全文） | 「心率回到某個值才開始下一趟」"this practice is not very relevant"：恢復期的心率不反映耗氧量和肌肉代謝。這和 `interval-adaptation.md` 說心率規則只當煞車一致。 |

**教練來源：**
- Palladino：near-threshold 3:1–4:1，supra-threshold 2:1，MAP 1:1（進階 2:1）。
- Koop：RI 1:1，TR 2:1。
- 你的筆記：「80–90% 最大心率的間歇，組休 3–5 分就能恢復」（`60 🏃 有氧訓練/間歇訓練.md:114`），這是鐵人J帥的說法。

### A2.2 W′ 恢復，和組休該怎麼對 CP／W′ 設

| 研究 | 恢復速度 | 讀到哪裡 |
|---|---|---|
| Skiba 2012（Jones & Vanhatalo 2017 全文引用） | τ = 546·e^(−0.01·D_CP) + 316 s；20 W 恢復約 377 s，中強度 452 s，重強度 580 s。騎車。 | 全文（回顧） |
| Ferguson 2010 | 半衰期約 234 s；2／6／15 分時恢復 37／65／86% | 摘要＋回顧 |
| Caen 2019 | 2／4／6 分恢復 46／51／59%；在 33% CP 恢復比 66% CP 多 | 摘要 |
| **Vassallo 2020（跑步，摘要）** | 平地間歇跑，恢復在 40% vVO2max／中強度／重強度時，**τ = 119 ± 32／190 ± 45／336 ± 77 s**，比 Skiba 的騎車數字快很多 | 摘要 |
| Black 2023 | 微分式 W′bal 會高估恢復（9.8 vs 實測 6.3 kJ） | 摘要 |
| Bourgois 2023 | 重複幾次以後，W′ 恢復會越來越慢 | 摘要 |
| Galán-Rioja 2023（騎車、壯年） | 用 W′bal 模型設計「每堂把 W′ 用完」的間歇，CP 4 週 +5–6%。表示 W′bal 可以拿來設計課表，但只有一篇小型研究 | 摘要 |

**套到這位選手（W′ 13.1 kJ 是推估）。** 組休結束時恢復了多少：

| 組休 | WKO5 dFRC（app `_dfrc`） | 跑步 τ = 119 s（Vassallo 輕恢復） | 跑步 τ = 190 s | Skiba 騎車（走路約 50% CP） |
|---|---|---|---|---|
| 1 分 | 40% | 40% | 27% | 11% |
| 1.5 分 | 47% | 53% | 38% | 16% |
| 2 分 | 53% | 64% | 47% | 21% |
| 3 分 | 62% | 78% | 61% | 30% |
| 4 分 | 69% | 87% | 72% | 37% |

每趟用掉多少 W′：

| 課表 | 用掉 | 佔 W′ |
|---|---|---|
| 5×2 分 @ 109% CP | 2.2 kJ | 17% |
| 4×3 分 @ 107.5% | 2.75 kJ | 21% |
| 4×4 分 @ 105% | 2.45 kJ | 19% |
| 4×4 分 @ 110% | 4.9 kJ | 37% |

**怎麼設組休（推估，依據在括號）：**
1. **5 區的組休用時間定，不用 W′bal 定。** 5 區每趟只用掉 15–40% 的 W′。休 2–3 分在跑步模型下大約回來 50–80%，所以每趟的負擔不會越堆越深。WKO 講者不贊成用 dFRC 定組休（`interval-adaptation.md` §2.3）。
2. **預設值：**
   - 5 區每趟 2 分 → 休 2 分。
   - 每趟 3 分 → 休 2.5–3 分。
   - 每趟 4 分 → 休 3 分（Helgerud）。
   - 恢復方式：休 ≤ 2.5 分用**走路或極慢跑**（Buchheit：休 < 2–3 分用被動恢復），休 3 分用慢跑（Helgerud：70% HRmax）。
3. **3 區（低於 CP）理論上不消耗 W′。** 組休只是讓配速和心理能撐住，所以照 Haugen 和 Palladino 用 1–2 分（3:1 到 5:1）。
4. **W′bal 只當課後檢討。** 想用它當「休息夠不夠」的參考時，用跑步的 τ（Vassallo），不要用 Skiba 的騎車公式。app 的 dFRC（25 s／300 s）介於兩者之間，**沒有跑步驗證**。

---

## A3. 用距離還是用時間；上坡間歇

- **時間比距離好**：理由見 §A1.4。換算表請直接用 §A1.4 那張；推送到 COROS 的步驟本來就是計時的 step。
- **上坡間歇的證據：**
  - Barnes 2013（摘要）：20 名訓練有素的跑者，6 週，5 種強度的上坡間歇。5 km 平均進步 2.0% ± 0.6%，沒有哪個強度最好；強度最高的那組跑步經濟性進步最多（2.4%）。"runners can assume that any form of high-intensity uphill interval training will benefit 5-km time-trial performance"。
  - Ferley 2016（摘要）：10% 坡的跑步機。短趟（30 s @ Vmax，10–14 趟）在 vLT 和坡上耐力的進步，比長趟（約 3 分 @ 68% Vmax，4–6 趟）多。
  - Ferley & Vukovich 2015：10% 坡用 65–85% Vmax 量力竭時間。坡上的速度是平路的 65–85%（摘要只到方法）。
  - Haugen 2022（全文）：坡度 5–10%，每趟約 30 s 到 4 分；"6–8 × 800–1000 m with easy jog back recoveries"。
  - **Buchheit Part I §3.1.1.4（全文）：** Gajer 的研究，菁英跑者 5% 坡 6×500 m 的 T@VO2max 比例只有 27%，平路 44%。所以**上坡版的 5 區要預期攝氧量比較低**，而且趟要 ≥ 2 分才會出現上坡的慢成分。
  - Vernillo 2017（摘要）：上坡的能量成本隨坡度線性增加；下坡在 −20% 最低。
  - Vernillo 2015（摘要）：山地超馬後，下坡的能量成本 +13.1%，平路和上坡不變；作者建議訓練加入下坡。
  - Bontemps 2025（摘要）：10 次下坡跑以後，股四頭肌的痠痛變少（8.7 vs 29.6 mm），神經肌肉疲勞沒變。
- **教練來源：**
  - Koop："uphill if possible… to reach 90% of your VO2max… more consistently"。
  - Daniels：可以把一到兩次 Q 課表移到丘陵路線，但最後幾趟 R 回到平地（`越野跑周期化訓練(晏慶、K天王、丹尼爾).md:88`）；T 強度偏好在平路練，比較好控制配速（:109）。
- **對 app：**
  - 上坡版照樣用 %CP。Stryd 上坡功率的準確度這裡**未驗證**；`zones.TERRAIN_NOTE` 已經寫了心率延遲的限制。
  - 坡度用 4–10%：3 區用 4–6%，5 區用 6–10%（推估，取 Haugen 5–10% 的範圍）。
  - 下坡回程就是組休，時間照實記錄。
  - 下坡的離心負荷是另外的壓力：組休走下坡比跑下坡溫和（推估）。

---

## A4. 每週劑量與進階

### A4.1 先加哪個維度

- **同儕審查裡沒有直接比較「先加組數」和「先加強度」的 RCT。未找到來源。**
- 間接證據：
  - Seiler 2013：較低強度、較多時間（32 分 @ 90% HR）優於較少時間、較高強度。
  - Wen 2019：總量 ≥ 15 分效果較大。
  - Haugen 2022：「evolves… in the form of duration, number of repetitions, running velocity and/or recovery time」，四個維度都有人用，沒有排序。
- 教練來源：
  - WKO（BI／ROLE）：先加組數或總時間，再拉長每趟、縮短休息，最後才加功率。
  - Palladino：三種間歇都靠「延長每組時間、縮短恢復」進步。
  - CW 研討會：VO2max 總量固定在 12–15 分，進步靠每組更用力。
  - 這些已經寫在 `interval-adaptation.md` §2.2。
- **app 採用的順序：總量 → 每趟長度 → 縮短休息 → 功率。** 這是教練共識，屬推估。

### A4.2 每堂目標區時間（TIZ）

| 類型 | 休閒選手的建議值 | 依據 |
|---|---|---|
| 5 區 | 每堂 10–16 分（工作時間），起步 10 分 | Buchheit 約 10 分 T@VO2max；Helgerud 和 Seiler 的 4×4 是 16 分；Wen ≥ 15 分；Koop 12–24 分；WKO 12–15 分（跑步約 11 分，FR:232） |
| 3 區 | 每堂 18–25 分，平日上限 45–50 分以內 | Daniels：T 一次 20 分對大部分跑者就夠，T ≤ 週跑量 10%；Haugen 菁英 30–75 分；CTS 30–60 分；UA：起步約週有氧量的 5%。<br>以週跑量 25–35 km 估，10% 約 17–24 分 @ 6:50 /km（推估，週跑量是假設） |

### A4.3 頻率

- **Lenk 2025**（摘要，休閒跑者，4×4 分，6 週）：每週 1、2、3 次都有進步。2–3 次的效果最大（d > 0.5）；3 次沒有比 2 次明顯更好。
- **Seiler 2013**：每週 2 次。**Helgerud 2007**：每週 3 次。
- **Billat 1999**：每週 1 次 4 週有進步；改成每週 3 次 4 週，表現沒再進步，正腎上腺素上升。
- Seiler & Tønnessen 2009：菁英每週 1–3 堂，3–4 堂會造成 overreaching（`aerobic-base-readiness.md` §2.4）。
- 你的筆記：「若是新手，一週執行一次間歇訓練即可；若稍有基礎了，可考慮一週 2 次」（`60 🏃 有氧訓練/間歇訓練.md:107`）。
- **對 app：**
  - 基礎期維持每週 1 堂品質課（3 區或 5 區）。
  - 5 區開了、而且連 4 週（推估）沒有護欄警告的話，可以變成 1 堂 5 區加 1 堂 3 區，或 2 堂 5 區。
  - 都要遵守台灣教練的規則：5 區每週 ≤ 2 次、間隔 ≥ 2 天。
  - 加到 2 次的根據是 Lenk 2025 和 Seiler 2013 的每週 2 次。

---

## A5. 檢查 app 現在的階梯

### A5.1 每一階

`session()` 的總時間是 `15 + 組數 × (每趟 + 休息) + 10`。

| 階 | app 現在 | 每趟長度 | 組休 | 強度 | 總量 | 總時間（app 算法） | 判定 |
|---|---|---|---|---|---|---|---|
| z3a | 3×8 分 @ 88–95% CP，休 2 分（慢跑） | 有根據（Haugen 3–15 分；Palladino 7–10 分；CTS 8–20 分） | 有根據（4:1，Palladino 3:1–4:1；Haugen 1–2 分） | 有根據：閾值下（Stryd Threshold 90–100%；Palladino Sweet spot 88–94%） | 24 分 | 55 分 | **太長**：平日上限 50 分時會被砍暖身 |
| z3b | 4×8 分 @ 88–95%，休 2 分 | 有根據 | 有根據 | 有根據；**引用 Seiler 2013 是錯的**（那是約 90% HRpeak、9.6 mmol/L 的嚴重強度） | 32 分 | 65 分 | **太長**：上限 45／50 分時被減成 3×8，標題變成 z3a，判成 neutral，**階梯卡住** |
| z3c | 3×10 分 @ 95–101%，休 3 分 | 有根據（Palladino near-threshold 7–10 分） | 有根據（3.3:1） | 有根據（Palladino 3B；Stryd Cruise 94–100%） | 30 分 | 64 分 | **太長**：被減成 2×10（標題不在階梯上，判成 neutral）。也是卡住 |
| z5a | 5×2 分 @ 106–112%，休 2 分（慢跑） | 有根據（台灣教練 ≥ 2 分；Buchheit ≥ 2–3 分；Seiler & Sjursen 2 分已到 92% VO2max） | 長度對；**方式要改成走路或極慢跑**（Buchheit：休 < 2–3 分被動） | 有根據（約 90–95% vVO2max，推估換算） | 10 分 | 45 分 | 有根據；最後一趟後面的休息不要算 |
| z5b | 4×3 分 @ 105–110%，休 3 分 | 有根據（Daniels 3–5 分；Palladino 2.5–3 分；Koop） | 有根據（1:1） | 有根據（Palladino 103–109%；Stryd 104–110%） | 12 分 | 49 分 | 有根據 |
| z5c | 5×3 分 @ 105–110%，休 3 分 | 有根據 | 有根據；進階時可縮到 2.5 分（Palladino、BI） | 有根據 | 15 分 | 55 分 | 上限 45 分要先砍暖身；改成休 2.5 分剛好放得下 |
| z5d | 4×4 分 @ 103–107%，休 3 分 | 有根據（Helgerud；Buchheit 結論 3） | 有根據（Helgerud 3 分 @ 70% HRmax） | 偏低但可以接受：Helgerud 用的是 90–95% HRmax，換算約 103–110% CP（推估）。建議 104–108%，加上心率條件：最後一分鐘 ≥ 90% HRmax | 16 分 | 53 分 | 有根據 |
| 3→5 區門檻 | 3 堂 3 區達標 | — | — | — | — | — | **推估**。台灣教練的規則只說 3 區穩定、恢復正常後，沒有給數字 |
| 5 區之後 | 4×4 和 3×10 交替 | — | — | — | — | — | 3×10 放不進平日上限，改成 Part C 的維持輪替 |

### A5.2 程式行為的問題（從程式碼推出來的，沒用真實 DB 或 FIT 驗證）

1. **時間上限讓階梯卡住。**
   - 流程：`plan_prefs.trim_quality` 依序把暖身砍到 10 分、緩和砍到 5 分，然後減組；改寫標題（`4×8 分` → `3×8 分`）。
   - `plan_sessions.title` 存的是改過的標題。`dose_history` 用 `done_titles()` 讀回來，`planned_spec` 依標題找到 z3a，但這時階梯在 z3b，所以 `neutral = True`，`dose_step` 不前進。
   - 上限 ≤ 54 分時 z3b 永遠過不去；上限 ≤ 63 分時 z3c 也一樣。
   - 修法見 Part C §C5：用 `variant_key` 判定，不用標題；縮量版另外處理。
2. **3 區的趟可能偵測不到。**
   - `count_reps` 只算 10 秒功率 ≥ 95% CP 的段，88–95% CP 的趟抓不到。
   - 抓不到時會改用 `detect_efforts`，門檻是 `max(0.85 CP, 1.12 × 全程中位數)`。3×8 分的課，趟數時間約佔移動時間一半，中位數約等於趟的功率（約 0.92 CP），門檻變成約 1.03 CP，**比趟本身還高**。
   - 抓不到時 `bouts = []`。`interval_outcome` 的 `done < 1`、`miss = 1`，就判成「未適應（目標太高）」，**每次都把目標下修 5%**。
   - 修法：有計畫時用 lap 對 step（`interval-adaptation.md` §4.2 S2）；沒有 lap 時，門檻改用「計畫下限 × 0.95」。
3. **總時間算法把最後一趟後面的休息也算進去。** `15 + reps × (work + rest) + 10` 多算一個休息（2–3 分）。改成 `暖身 + reps × work + (reps − 1) × rest + 緩和`。
4. **組休方式。** detail 寫「休 N 分鐘（慢跑）」。5 區休 ≤ 2.5 分要改寫「走路或極慢跑」，COROS step 的強度也跟著降低。
5. **`z3b` 的來源字串**要把 Seiler 2013 換掉，改成 Haugen 2022 或 Daniels 巡航間歇。

### A5.3 修正版階梯

**符號：**
- 暖身 WU：3 區 12 分，5 區 15 分（§C3）。
- 緩和 CD：5 分。
- 主課 = 組數 × 每趟 + (組數 − 1) × 組休。
- 總時間 = WU + 主課 + CD。

**3 區**（第一個質量課，徐國峰）：

| 階 | key | 課表 | 強度 | 組休 | TIZ | 總時間 | 依據 |
|---|---|---|---|---|---|---|---|
| T1 | z3a | 3×6 分 | 90–95% CP（184–194 W） | 1.5 分慢跑 | 18 分 | 12 + 21 + 5 = **38 分** | Haugen 3–15 分、1–2 分組休；Palladino 3:1–4:1；台灣教練先 3 區 |
| T2 | z3b | 3×8 分 | 90–95% CP | 2 分慢跑 | 24 分 | 12 + 28 + 5 = **45 分** | 同上；Daniels T 約 20 分 |
| T3 | z3c | 2×12 分 | 90–95% CP | 2 分慢跑 | 24 分 | 12 + 26 + 5 = **43 分** | 先拉長每趟（FR:214-218、Palladino）；CTS TempoRun 8–20 分 |

**5 區**（有氧基礎確認、3 堂 3 區達標之後；每週 ≤ 2 次、間隔 ≥ 2 天）：

| 階 | key | 課表 | 強度 | 組休 | TIZ | 總時間 | 依據 |
|---|---|---|---|---|---|---|---|
| V1 | z5a | 5×2 分 | 106–112% CP（216–228 W） | 2 分走路或極慢跑 | 10 分 | 15 + 18 + 5 = **38 分** | 台灣教練 ≥ 2 分；Buchheit 被動 < 2–3 分；約 10 分 T@VO2max |
| V2 | z5b | 4×3 分 | 105–110% CP | 3 分慢跑 | 12 分 | 15 + 21 + 5 = **41 分** | Palladino MAP 1:1；Koop 6×3／3 分 |
| V3 | z5c | 5×3 分 | 105–110% CP | 2.5 分走路或極慢跑 | 15 分 | 15 + 25 + 5 = **45 分** | 加組數＋縮短休息（Palladino）；Wen ≥ 15 分 |
| V4 | z5d | 4×4 分 | 104–108% CP（212–220 W） | 3 分慢跑 | 16 分 | 15 + 25 + 5 = **45 分** | Helgerud 2007；Buchheit 結論 3 |
| 維持 | — | V3、V4、3 區 T+（3×7 分 @ 97–100% CP、休 2 分）三族輪替（Part C） | | | | ≤ 45 分 | 推估 |

**其他：**
- 3 區之後的「T+ 近閾值」（97–100% CP）是 5 區開了以後的維持選項，不是開 5 區的條件。依據是 Palladino near-threshold。
- 心率備用（沒有功率或 Stryd 時）：
  - 3 區用 AeT–LTHR（UA）；LTHR 還是預設值時改用約 85–90% HRmax（Helgerud 乳酸閾值組 85%、Seiler 4×16 分 88%）。
  - 5 區要求最後一分鐘到 90–95% HRmax（Helgerud）。
  - Buchheit Part I：心率在 1–2 分的趟常常來不及上來，所以心率只用來檢查 3 分以上的趟。
- 這份階梯的劑量全部放得進 45 分；50 分上限時多出來的 5 分留給暖身。

---

# Part B：功率間歇能看的圖表與指標

## B1. 各平台提供什麼

| 平台 | 間歇相關的圖表／指標 | 來源 |
|---|---|---|
| **WKO5** | - 每個 range（lap 或間歇）的平均功率、NP、心率、Pw:HR 前後半（field 4250，`docs/wko5-internals/workout-metrics.md`）<br>- dFRC 曲線<br>- Optimized Intervals 每級的目標時間和功率<br>- iLevels 區間時間<br>- Golich 的看法（研討會筆記 AP1:74-158）：本堂 MMP 疊在 PDC 和目標帶上；「當日最佳 5 分」有沒有落在 VO2max 區；累積一定訓練量之後的 MMP（疲勞耐受）<br>- Cloud Library 社群圖表：dFRC recovery、Skiba vs dFRC、Auto Marking VO2max Intervals、HIIT Validation iLevels | `interval-adaptation.md` §2；`研討會Analyzing Interval Training in WKO4 - Part 1.md:74-158` |
| **TrainingPeaks** | - Analyze 360：所有通道疊圖、自訂 lap、底部的 lap 清單<br>- Interval Detection：功率 ≥ Zone 3、持續夠久才算一段<br>- Compliance 顏色（±20% 綠）<br>- EF、Pw:Hr | https://help.trainingpeaks.com/hc/en-us/articles/36837856738957 ；`competitor-charts.md` §1.2 |
| **intervals.icu** | - 每段間歇一列表格，二十多個欄位（平均／NP 功率、心率、步頻、做功、強度、效率等）；**這句是第三方網站寫的，未驗證**<br>- 每段的 W′bal 最低點和用掉的 %（`wbal_min_j`、`wbal_usage_pct`）；**出自第三方 GitHub repo，未驗證**<br>- 課表編輯器的「predicted W′bal」；論壇寫的恢復式是每秒 `deltaW × (W′ − Wbal) / W′`<br>- HRRc：心率超過門檻 > 1 分以後，60 秒內的最大降幅<br>- 每段的 decoupling | https://forum.intervals.icu/t/predicted-wbal-depletion/27198 ；https://forum.intervals.icu/t/heart-rate-recovery-and-fitness-on-activities/117 ；https://jscyclingtraining.com/en/complete-guide-intervals-icu-cyclists/ |
| **GoldenCheetah** | - W′bal（Skiba 積分式和微分式）、W′ Work<br>- W′bal Fatigue Zones：W1 > 75%、W2 50–75%、W3 25–50%、W4 < 25% W′<br>- interval summary 可自選欄位<br>- Aerobic Decoupling、xPower | https://github.com/GoldenCheetah/GoldenCheetah/wiki/UG_Glossary ；`competitor-charts.md` §1.3 |
| **Stryd PowerCenter** | - 可選單一 lap 分析<br>- 計畫課表和 lap 配對（auto-lap 會讓區塊對不上，Stryd 建議結構化課時關掉 auto-lap）<br>- %CP 區間與 run type 分類<br>- Form Power Ratio、RE | https://blog.stryd.com/2020/02/11/introducing-the-new-powercenter/ ；https://help.stryd.com/en/articles/8901356 ；https://help.stryd.com/en/articles/14360092 |

## B2. 哪些指標有研究支持

| 指標 | 用來判斷 | 證據 | 怎麼用 |
|---|---|---|---|
| 每趟功率 vs 目標、達標率 | 有沒有照課表做 | 定義性指標；Golich 看「第幾趟掉出目標」（教練） | 主要判準（現有 `interval_outcome`） |
| 掉速（fade） | 課內疲勞 | Glaister 2008：percent decrement 是最有效、最可靠的算法（衝刺研究）。Girard 2011：掉速和第一趟的功率有關。「掉 > X% 算疲勞」的門檻**未找到來源**；WKO 講者反對拿它當收工規則 | 只顯示 Sdec，不單獨判斷 |
| W′bal／dFRC 最低點 | 這堂挖得多深 | 模型：Skiba 2012；跑步的 τ（Vassallo 2020）；會高估恢復（Black 2023）；Galán-Rioja 2023 用它設計課表 | 只顯示；跑步要標「模型推估」 |
| 高於 CP 的時間 | 嚴重強度區的劑量 | CP 是穩態和非穩態的分界（Jones & Vanhatalo 2017） | 5 區 TIZ 的定義 |
| ≥ 90% HRmax 的時間 | 當作 T@VO2max 的替代 | **證據弱**：Buchheit Part I §2.3 說心率有延遲，1–2 分的趟常常上不去，恢復時心率又會高估負荷 | 只當輔助，標「替代指標」 |
| 每趟心率峰值、逐趟爬升 | 心血管負荷、疲勞 | 爬升的門檻**未找到來源**（只有鐵人J帥的說法） | 只顯示 |
| HRR60（組休 60 秒的心率降幅） | 適應或疲勞 | Buchheit 2014 表 1：雜訊（CV）約 25%，最小有意義變化約 +7%。適應和功能性過量訓練時都會變快（Bellenger 2016、Aubry 2015、Le Meur 2017）。前一趟越強就恢復越慢（Mann 2014）。Lamberts 2010：4 週 HIT 期間 HRR 上升的那組，40 km TT 進步較多 | 只看同一份課表的跨堂趨勢；要搭配功率達標 |
| **同功率下的運動心率（HRex）** | 體能 | **Buchheit 2014 表 1：CV 約 3%，最小有意義變化約 −1%**；"the lower the HR, the fitter"；但心率上升不一定是疲勞 | **最值得做的跨堂指標**（§B3 圖 6、7） |
| 每趟 Pw:HR | 同上 | 由 HRex 推出來的；TrainingPeaks 的 EF（教練） | 跨堂比較同一份課表 |
| 每趟 RE（speed ÷ W/kg） | 經濟性 | Stryd／Runalyze 的分級（教練）；當疲勞判準用的證據**未找到來源** | 只看平路的趟，只顯示 |
| 步頻、觸地時間的變化 | 跑姿疲勞 | Vernillo 2015：超馬後上坡和下坡的觸地時間變長；間歇裡的門檻**未找到來源** | 只顯示（現有 `form_drift` 改成逐趟） |
| session RPE | 內在負荷 | Foster 2001 有效；Seiler & Sjursen 2004 發現配速得當的課 RPE 約 17 | **app 目前沒有**，建議加 0–10 輸入 |

## B3. 用 app 現有資料能做的圖

**資料：**
- Stryd 1 秒功率、心率、速度、步頻、觸地時間（`stancetime`）、垂直振幅、LSS、ILR。
- 推送課表的 lap：每個計時 step 一個 lap，已在 09-30 的 CP 測試核對過。

**符號：** P = 功率；HR = 心率；趟 k 的區間 [a_k, b_k]；計畫帶 [lo, hi]·CP。

| # | 圖 | 公式 | 來源 |
|---|---|---|---|
| 1 | **每趟功率長條＋目標帶**（間歇卡，取代純表格） | `P̄_k = mean(P[a_k:b_k])`；`pct_target_k = P̄_k / (mid(lo, hi)·CP)`；`in_band_k = P̄_k ≥ 0.98·lo·CP`（現有 `IN_BAND_TOL`）；`達標率 = Σin_band / 計畫趟數` | 定義；Golich（教練）；0.98 的容許值是推估 |
| 2 | **掉速 Sdec**（同一張圖的副標） | `Sdec = 100 × (1 − ΣP̄_k / (n × max P̄_k))` | Glaister 2008（原式用時間，這裡改成功率，屬推估） |
| 3 | **W′bal／dFRC 曲線＋每趟最低點** | dFRC：app `_dfrc`（WKO5）。<br>跑步版 W′bal：P > CP 時 `W −= (P − CP)·dt`；否則 `W += (W′ − W)·(1 − e^(−dt/τ))`，τ = 119 s（輕恢復）或 190 s（Vassallo 2020）。<br>每趟顯示 `min W′bal / W′` 和「組休結束時恢復了多少 %」 | WKO5；Skiba 2012；Vassallo 2020；GC 的 Fatigue Zones 色帶（W1–W4） |
| 4 | **目標區時間 vs 計畫** | `TIZ = Σ dt [10 s 平均功率 ∈ [lo·CP, hi·CP·1.05]]`；另外算 `T>CP = Σ dt [P10 ≥ CP]`；顯示 `TIZ / 計畫 TIZ` | Buchheit（T@VO2max 的概念）；TP compliance ±20% |
| 5 | **心率逐趟** | `hr_peak_k = max HR[b_k − 10 : b_k + 15]`（現有）；`hr_end_k = mean HR[b_k − 5 : b_k]`；`HRR60_k = hr_peak_k − HR[b_k + 60]`（現有 `hr_drop60`）；`t_to_AeT_k`；`T≥90%HRmax`（標「替代指標」） | Buchheit Part I；Buchheit 2014；徐國峰的 AeT 規則只當煞車 |
| 6 | **每趟 Pw:HR** | `EF_k = mean P[後 50%] / mean HR[後 50%]`。只取後半段，因為心率有延遲；50% 是推估 | HRex 的邏輯（Buchheit 2014）；TP EF |
| 7 | **同一份課表的跨堂趨勢**（圖表分析頁） | x = 日期；只比相同 `variant_key`（Part C）的課。<br>y1 = 平均 `P̄_k / CP`；y2 = 平均 `hr_end_k`；y3 = HRR60 中位數。<br>灰帶畫 ±3%（HRex 的 CV）和 ±25%（HRR60 的 CV），超出灰帶才標「有變化」 | Buchheit 2014 表 1；Lamberts 2010 |
| 8 | **逐趟跑姿**（只看平路的趟） | 每趟的平均步頻、觸地時間、`RE_k = v̄_k / (P̄_k / kg)`；第一趟到最後一趟的變化 | Stryd／Runalyze（教練）；Vernillo 2015 |
| 9 | **本堂 MMP vs 90 天 PDC＋目標帶** | 本堂 1 s–20 min 的 MMP 疊在 PDC 上，標出計畫的每趟時間和目標帶 | Golich（WKO 研討會 AP1:74-117） |
| 10 | **計畫 vs 實際** | 總時間、TIZ、趟數的 compliance：80–120% 綠、50–150% 橘、其他紅 | TrainingPeaks 的 compliance colors |

**找趟的方法：**
1. 有推送的課表：依順序用 lap 對 step，再用時長 ±5 s 確認（`interval-adaptation.md` §4.2）。
2. 沒有 lap：門檻改用 `0.95 × 計畫下限`，不用 session 中位數（修正 §A5.2-2）。
3. 沒有計畫：才用現在的 `detect_efforts`。

**首推的四張：** 1＋2、4、7、3。
- 6、8、9 是次要的。
- 5 已經有表格，加上 `hr_end` 和 `t_to_AeT` 就好。

---

# Part C：課表變化與同等課表

## C1. 其他教練和系統怎麼排（依目標）

| 目標 | 系統 | 代表課表 | 強度／組休 | 類型 |
|---|---|---|---|---|
| 3 區 | Daniels | 連續 20 分 T；巡航間歇（多趟 T、短休） | T 約能撐 60 分的配速；巡航每 5 分休 1 分（**二手、未驗證**） | 教練（`丹尼爾的跑步方程式筆記 還有課表.md:33,45,75`） |
| 3 區 | Pfitzinger | LT 間歇／tempo | **未驗證**（只有二手網頁） | 教練 |
| 3 區 | 挪威式（雙閾值） | 一天兩堂閾值間歇，例如 10–12×1000 m／休 1 分、6–8×1500–2000 m | 血乳酸 2–4.5 mmol/L | 同儕審查描述（Casado 2023；Haugen 2022）。休閒跑者只取單堂版本 |
| 3 區 | Palladino | near-threshold：7–10 分 × 數組 | 98–100% CP，工休比 3:1–4:1 | 教練（`功率區間說明與訓練目的 --star.md:50-63`） |
| 3 區 | CTS／Koop | TempoRun："3x12min TR, 6min recovery" | RPE 8–9，工休比 2:1 | 教練 |
| 3 區（越野） | Koop、Daniels | 長上坡穩定爬升；Daniels：T 偏好在平路 | — | 教練（`越野跑周期化訓練(晏慶、K天王、丹尼爾).md:109`） |
| 3 區 | Canova | special block：一天兩堂長的半馬到全馬配速段，碳水限制 | 菁英全馬專項 | 教練（Sweat Elite、Runners Connect），**不適用**於這位選手 |
| 5 區 | Helgerud／挪威 4×4 | 4×4 分 @ 90–95% HRmax／休 3 分 @ 70% | — | 同儕審查 |
| 5 區 | Daniels I／H | 每趟 3–5 分；H：「6×3 分鐘 H，每趟中間慢跑 2 分鐘」 | I 約能撐 10–15 分的配速 | 教練（`丹尼爾的跑步方程式筆記 還有課表.md:56`） |
| 5 區 | Pfitzinger | 600–1600 m @ 5K 配速，休 2–4 分 | **未驗證**（二手網頁） | 教練 |
| 5 區 | Palladino MAP | 2.5–3 分 × 數組 | 103–109% CP，1:1 | 教練 |
| 5 區 | Koop RI | 6×3 分／休 3 分，盡量上坡 | RPE 10，1:1 | 教練 |
| 5 區（短） | Billat 30-30、15-15 | 30 s @ 100%／30 s @ 50% vVO2max | — | 同儕審查；**每趟 < 2 分，不符合台灣教練的規則** |
| 5 區（短） | Rønnestad 30/15 | 3 組 × 13 × 30 s／15 s，組間 3 分 | 依自覺努力 | 同儕審查（騎車）；**每趟 < 2 分** |
| 上坡 | Haugen、Barnes | 5–10% 坡，30 s–4 分，慢跑下來 | — | 同儕審查 |
| 上坡 | Uphill Athlete | 坡衝刺 8–10 s，坡度 ≥ 20%，6–8 趟，休 2–3 分（神經肌肉）；ME（負重陡坡，以腿部疲勞為限，不是心肺間歇） | — | 教練 |
| 變化 | Fartlek／金字塔 | 「Pyramid／Fartlek：多變刺激、適應多種節奏」 | — | 你的筆記（`60 🏃 有氧訓練/間歇訓練.md:208`）；Palladino 恢復週 4–6×1 分 fartlek（`palladino基礎期訓練.md:46,86`） |
| 下坡 | — | 下坡技術放在輕鬆日或長跑（不排成間歇） | — | Vernillo 2015、Bontemps 2025（同儕審查）；不納入間歇庫 |
| 台灣教練 | — | 先 3 區；5 區每趟 ≥ 2 分、每週 ≤ 2 次、間隔 ≥ 2 天 | — | 教練經驗 |

## C2. 什麼叫「同等」

**同等的定義**（全部要成立；數字除了徐國峰那條，其他都是推估）：
1. **目標類別相同**：`Z3sub`（88–95% CP）、`Z3near`（97–101% CP）、`Z5`（103–115% CP）三類之一。
2. **TIZ 在這一階目標的 ±15% 以內。** 依據：Seiler 2013 顯示累積時間和強度會一起影響效果；Wen 2019 說總量重要。±15% 是推估。
3. **每趟長度符合類別：**
   - Z5 每趟 ≥ 2 分（徐國峰）。
   - Z3 每趟 ≥ 3 分（Haugen 下限）。
   - 連續 tempo 算 1 趟。
4. **工休比在類別範圍內：**
   - Z5：組休 ≤ 每趟時間，而且 ≤ 3 分（Buchheit 建議工休比 > 1；Palladino 1:1–2:1）。
   - Z3：工休比 3:1 到 6:1（Palladino、Haugen）。
5. **每趟用掉的 W′（只檢查 Z5）**：落在這一階標準課表的 0.7–1.5 倍（推估）。這條是為了排除「同樣時間、但每趟深很多」的版本，例如 4×4 @ 110% 會用掉 37% 的 W′，標準課表只有 19%。

**TSS／負荷只當上限檢查，不當同等的判準。** 理由：
- Buchheit Part II："Contrasting HIT formats that elicit similar (and maximal) cardiorespiratory responses have been associated with distinctly different anaerobic energy contributions"。
- Seiler 2013、Rønnestad 2015／2020：努力程度相同的課，效果不一樣。

**同等的限制，要寫在 UI 的說明裡：**
- **30/15 和 4×4 不同等。**
  - Rønnestad（騎車）：30/15 在 VO2max 或 20 分功率進步比較多。
  - Fleckenstein 2025（跑者）：30 s 間歇 > 90% VO2max 的時間比 4×3 分少。
  - 方向不一致，而且每趟 < 2 分不符合徐國峰。
  - 所以 30/15 列為「非同等」：使用者可以選，算一堂 5 區（頻率照算），但不算進階。
- **上坡版和平路版用同樣的 %CP，算同等，但要提醒：** 上坡時 T@VO2max 的比例比較低（Gajer），而且下坡回程有離心負荷。
- **不同類別一律不同等**：3 區換 5 區、5 區換 3 區都不行。

## C3. 暖身與緩和（標準區塊）

**使用者的條件：** 要先跑約 10 分鐘穿過市區才到河濱，這段就是暖身裡輕鬆的部分。

| 區塊 | 3 區用（WU-T，12 分） | 5 區用（WU-V，15 分） | 依據 |
|---|---|---|---|
| 市區輕鬆跑 | 10 分 @ ≤ 75% CP（Zone 1–2）；最後 2 分漸進到約 85% CP | 同左 | Buchheit 結論 2a：暖身 ≤ 60–70% vVO2max（換算約 ≤ 70–80% CP，推估）。Bishop 2003：暖身要讓攝氧量升起來、又不能疲勞。Burnley 2005：中或重強度的 priming 讓後面的嚴重強度表現 +2–3%（騎車） |
| 動態伸展、跑姿 drill | 可省略 | 2 分（擺腿、高抬腿、小步跑） | Panascì 2025：暖身加 60 s／腿的動態伸展，休閒跑者的跑步經濟性和表現較好（摘要）；McGowan 2015 回顧 |
| 快步跑（strides） | 2×15–20 s，間隔慢跑 40 s（2 分） | 3×15–20 s，間隔慢跑 40 s（3 分） | Daniels ST：「15～20秒的快步跑（不是衝刺）…每趟中間休息45～60秒」（`丹尼爾的跑步方程式筆記 還有課表.md:97`）；Ingham 2013 的對照組用 6×50 m strides |
| 暖身到第一趟之間 | ≤ 1–2 分 | ≤ 1–2 分 | Buchheit 結論 2a："little delay between the warm-up and the start" |
| 緩和 CD | 5 分慢跑或走路；跑回家的話這段就是緩和（10 分） | 同左 | Van Hooren & Peake 2018：主動緩和對大部分恢復指標沒有幫助，所以短一點就好 |

- **下限：** 市區 10 分是物理上一定要跑的，**不能砍**。3 區的 strides 可以省；5 區的 strides 最多減成 1 趟。
- **設定項（新增 `plan.prefs`）：**
  - `warmup_commute_min`：預設 10。
  - `cooldown_min`：預設 5；要跑回家就設 10。
  - 這兩個值直接進總時間的計算。

## C4. 課表庫（CP 204 W；平日上限 45 分）

**符號：**
- 總時間 = WU + 主課 + CD（5 分）。
- 3 區的 WU 是 12 分，5 區是 15 分。
- 「平」= 河濱平路；「坡」= 4–10% 坡、走或慢跑下來。上坡版的組休長度依坡長而定，表上寫的是目標值。
- 心率備用：3 區用 AeT–LTHR（LTHR 是預設值時用 85–90% HRmax）；5 區要求最後一分鐘 ≥ 90% HRmax（Helgerud）。

**T1（Z3sub，TIZ 18 分）**

| key | 課表 | 強度 | 組休 | TIZ | 總時間 | 地形 | 來源 |
|---|---|---|---|---|---|---|---|
| t1a（標準） | 3×6 分 | 90–95% CP（184–194 W） | 1.5 分慢跑 | 18 | 38 | 平 | Haugen；Palladino |
| t1b | 6×3 分 | 92–97% CP | 1 分慢跑 | 18 | 40 | 平 | Haugen（1000 m 約 7 分；這裡改成 3 分短趟）；短趟強度略高屬推估 |
| t1c | 3×6 分上坡 | 90–95% CP | 慢跑下來（約 2 分） | 18 | 39 | 坡 4–6% | Haugen 坡 5–10%；Koop |
| t1d | 連續 20 分 tempo | 88–92% CP | — | 20 | 37 | 平 | Daniels 20 分 T |

**T2（Z3sub，TIZ 24 分）**

| key | 課表 | 強度 | 組休 | TIZ | 總時間 | 地形 | 來源 |
|---|---|---|---|---|---|---|---|
| t2a（標準） | 3×8 分 | 90–95% CP | 2 分慢跑 | 24 | 45 | 平 | Haugen；Palladino |
| t2b | 4×6 分 | 90–95% CP | 1.5 分慢跑 | 24 | 46（上限 45 時 rest 改 1:20） | 平 | Haugen |
| t2c | 5×5 分 | 90–95% CP | 1 分慢跑 | 25 | 46 | 平 | 挪威式短休（Casado 2023；Haugen "1 min recovery"） |
| t2d | 3×8 分上坡 | 90–95% CP | 慢跑下來（約 2 分） | 24 | 45 | 坡 4–6% | Haugen；Koop |

**T3（Z3sub，TIZ 24 分，長趟）**

| key | 課表 | 強度 | 組休 | TIZ | 總時間 | 地形 | 來源 |
|---|---|---|---|---|---|---|---|
| t3a（標準） | 2×12 分 | 90–95% CP | 2 分慢跑 | 24 | 43 | 平 | FR:214-218「先延長時間」；CTS TR |
| t3b | 連續 24 分 tempo | 88–92% CP | — | 24 | 41 | 平 | Daniels 節奏跑 |
| t3c | 2×12 分長上坡 | 90–95% CP | 下坡慢跑 | 24 | 約 45 | 坡（長坡） | Koop／越野專項 |

**T+（Z3near，維持用；5 區開了以後輪替）**

| key | 課表 | 強度 | 組休 | TIZ | 總時間 | 地形 | 來源 |
|---|---|---|---|---|---|---|---|
| tpa | 3×7 分 | 97–100% CP（198–204 W） | 2 分慢跑 | 21 | 42 | 平 | Palladino near-threshold 7–10 分、3:1–4:1 |
| tpb | 4×5 分 | 98–101% CP | 1.5 分慢跑 | 20 | 42 | 平 | Palladino；Stryd Cruise 94–100% |
| tpc | 3×7 分上坡 | 97–100% CP | 下坡慢跑 | 21 | 約 43 | 坡 | 同上 |

**V1（Z5，TIZ 10 分）**

| key | 課表 | 強度 | 組休 | TIZ | 總時間 | 地形 | 來源 |
|---|---|---|---|---|---|---|---|
| v1a（標準） | 5×2 分 | 106–112% CP（216–228 W） | 2 分走路或極慢跑 | 10 | 38 | 平 | 徐國峰；Buchheit |
| v1b | 4×2:30 | 105–110% CP | 2 分走路或極慢跑 | 10 | 36 | 平 | Palladino MAP 2.5–3 分 |
| v1c | 5×2 分上坡 | 106–112% CP | 走或慢跑下來（約 2 分） | 10 | 38 | 坡 6–10% | Haugen；Koop；Barnes |
| v1d | 2-3-3-2 分 | 105–110% CP | 2 分走路或極慢跑 | 10 | 36 | 平 | 你的筆記「Pyramid」（`間歇訓練.md:208`）；結構屬推估 |

**V2（Z5，TIZ 12 分）**

| key | 課表 | 強度 | 組休 | TIZ | 總時間 | 地形 | 來源 |
|---|---|---|---|---|---|---|---|
| v2a（標準） | 4×3 分 | 105–110% CP | 3 分慢跑 | 12 | 41 | 平 | Koop；Palladino |
| v2b | 6×2 分 | 106–112% CP | 2 分走路或極慢跑 | 12 | 42 | 平 | Buchheit 表 1（6–10×2 分） |
| v2c | 4×3 分上坡 | 105–110% CP | 慢跑下來（約 3 分） | 12 | 41 | 坡 6–10% | Koop "uphill if possible" |
| v2d | 3×4 分 | 104–108% CP | 3 分慢跑 | 12 | 38 | 平 | Helgerud 的形式、量少一點 |

**V3（Z5，TIZ 15 分）**

| key | 課表 | 強度 | 組休 | TIZ | 總時間 | 地形 | 來源 |
|---|---|---|---|---|---|---|---|
| v3a（標準） | 5×3 分 | 105–110% CP | 2.5 分走路或極慢跑 | 15 | 45 | 平 | Palladino（縮短休息）；Wen ≥ 15 分 |
| v3b | 2-3-4-3-2 分金字塔 | 105–110% CP（4 分那趟用 104–108%） | 2 分走路或極慢跑 | 14 | 42 | 平 | 你的筆記 Pyramid；結構屬推估 |
| v3c | 6×2:30 | 105–110% CP | 2 分走路或極慢跑 | 15 | 45 | 平 | Palladino |
| v3d | 5×3 分上坡 | 105–110% CP | 慢跑下來（約 2.5 分） | 15 | 45 | 坡 6–10% | Haugen；Koop |

**V4（Z5，TIZ 16 分）**

| key | 課表 | 強度 | 組休 | TIZ | 總時間 | 地形 | 來源 |
|---|---|---|---|---|---|---|---|
| v4a（標準） | 4×4 分 | 104–108% CP（212–220 W） | 3 分慢跑 | 16 | 45 | 平 | Helgerud 2007 |
| v4b | 8×2 分 | 106–112% CP | 1.5 分走路 | 16 | 46（上限 45 時減成 7×2，TIZ 14，在 ±15% 內） | 平 | Buchheit 表 1；工休比 > 1 |
| v4c | 4×4 分上坡 | 104–108% CP | 慢跑下來（約 3 分） | 16 | 45 | 坡 4–6%（長趟用緩坡） | Gajer：上坡要長趟（Buchheit Part I） |

**非同等選項**（可以手動選，但不算進階）：

| key | 課表 | 為什麼不同等 | 總時間 |
|---|---|---|---|
| x3015 | 2 組 × 13 × 30 s／15 s @ 110–120% CP，組間 3 分 | 每趟 < 2 分（徐國峰）；30/15 和長間歇的證據方向不一致（C2） | 15 + 22.5 + 5 ≈ 43 |
| xhill | 坡衝刺 6–8 × 8–10 s 全力、坡度 ≥ 20%、休 2–3 分 | 神經肌肉訓練（UA），不是 5 區；可以加在輕鬆日的尾巴 | 依輕鬆日 |
| r1 | 恢復週 fartlek 4×1 分 @ 98–101% CP | 原本就不算進階（Palladino） | 現有 |

## C5. 排課規則（給實作的規格）

### C5.1 資料結構

```python
@dataclass(frozen=True)
class Variant:
    key: str            # "v3a"
    rung: str           # "z5c" (the ladder step it serves)
    cls: str            # "Z3sub" | "Z3near" | "Z5"
    reps: int
    work_s: int
    rest_s: int
    rest_mode: str      # "walk" | "jog" | "jog_down"
    lo: float           # × CP
    hi: float
    terrain: str        # "flat" | "hill"
    canonical: bool     # the studied protocol (first exposure)
    src: str
    pattern: tuple[int, ...] | None = None   # pyramids: per-rep work_s
LIBRARY: dict[str, tuple[Variant, ...]]      # rung -> variants (C4)
```

- `tiz_s(v)` = Σ 每趟工作秒數。
- `main_s(v)` = Σ 工作 + (n − 1) × rest_s。
- `total_min(v, prefs)` = WU(cls) + main + CD。
  - WU(Z3) = commute + 2；WU(Z5) = commute + 5。
  - commute 預設 10。
- `equivalent(a, b)`：照 C2 的 1–5 條。
- 單元測試：C4 每一列彼此同等；x3015 和 v4a 不同等。

### C5.2 自動選擇（`pick_variant(rung, day, cap, history, prefs)`）

1. **硬條件：**
   - 同一階、同類別。
   - `total_min ≤ cap`（cap 是這天的上限）。
   - 地形可用：平日預設只有「平」，除非 `plan.prefs.terrain_quality = hill` 或這天標成越野。
   - 5 區還要檢查台灣教練的規則：這週 5 區 ≤ 2 次、距上一堂 5 區 ≥ 2 天。
2. **第一次碰到這一階時用 canonical**，也就是有研究的那份課表，讓第一次的結果可以和文獻對照。這是推估。
3. **之後排序**（全部推估）：
   - 近 2 次同一階用過的變體扣分（避免重複）。
   - 上次在這個變體判成「未適應」的扣分。
   - 使用者標「喜歡」的加分、「不喜歡」的排除。
   - 越野目標時，上坡版每 2 堂至少 1 堂（Koop、Daniels：比賽前要到類似地形練）。
4. **維持期**（V4 之後）：在 V3、V4、T+ 三族之間輪替；每週 1 堂 5 區時，T+ 每 3 週 1 次（推估）。
5. 回傳 `(variant, reason)`。reason 寫進 detail，例如「上次做 v3a；這次換 v3c（同等，不影響進階）」。

### C5.3 時間上限放不下的時候

這段取代現在的 `trim_quality` 減組邏輯。

1. 算 `budget = cap − WU − CD`。WU 和 CD 用下限：市區 10 分不能砍；5 區的 strides 最多減到 1 趟。
2. 在同等變體裡找 `main ≤ budget` 的。**有的話就換那一個，不減組。** 進階照常。
3. 都放不下時，產生縮量版：把標準課表的組數往下減。
   - 縮完 TIZ 還有 ≥ 85% 時，**仍算同等**（C2 的 ±15%）。
   - 縮到 < 85% 時是**「縮量版」**：判成「達標」也只算維持，這一階不前進（推估）；判成「未適應」照一般規則處理。
   - 組數下限：Z5 3 趟、Z3 2 趟。
4. 下限也放不下時，**換一天**：同一週還沒排品質課、上限比較大的日子（週末或沒有上限的日子）。要遵守：
   - 長跑前後 48 小時（現有）。
   - 5 區間隔 ≥ 2 天（徐國峰）。
5. 沒有可換的日子：改排上一階的同等課表當維持（不算進階），並在「還缺什麼」顯示：
   > 「平日 45 分放不下 V4（需要 45 分以上的 4×4）：本週改排 V3 的 6×2:30，不算進階。要進階，把平日上限調到 N 分，或把品質課改到週末。」

### C5.4 手動替換（課表頁）

**卡片：**
- 標籤「第 V3 階：5×3 分」加一個「換一個」按鈕。

**抽屜（drawer）分兩區：**
1. **同等（不影響進階）。** 每一列：
   - 標題、結構、功率（W 和 %CP）、心率備用、組休方式、TIZ。
   - 總時間，拆成「暖身 15（市區 10＋河濱 5）· 主課 25 · 緩和 5 ＝ 45 分」。
   - 超過今天上限的列灰掉，寫出原因。
   - 地形圖示、上次做的日期、上次的判定（達標／邊界／未適應）。
   - 依 `pick_variant` 的分數排序；目前選的那一個標出來。
2. **不同等（會影響進階）。** 每一列都寫出後果：
   - 上一階：「維持，不算進階」。
   - 30/15：「算一堂 5 區，不算進階」。
   - 換成 3 區：「這週沒有 5 區」。
   - 縮量版：「達標也不前進」。

**確認之後：**
- 寫入 `plan_sessions`：`variant_key`、`rung_key`、`equiv`（布林）、`swap`（`auto`／`user`／`cap`）、`swap_reason`。
- 推送 COROS 時用變體自己的 step（休息方式 walk 對應低強度 step）。

**課後判定**（改 `dose_history`／`dose_step`）：
- 用 `variant_key` 找規格，**不再用標題比對**（修正 §A5.2-1）。
- 同等：照 `interval_outcome` 判定，達標就 +1 階。
- 縮量版、非同等：判定結果只顯示，不影響階數；但算進 5 區頻率和硬課間隔。
- 舊資料沒有 `variant_key`：照現在的標題邏輯，向下相容。

### C5.5 實作步驟（每一步都能單獨合併）

| 步驟 | 內容 | 測試 |
|---|---|---|
| S0 | 合成 FIT：3×8 分 @ 92% CP。確認 `dose_history` 會不會把 bouts 判成空的（§A5.2-2） | `test_quality_gate.py`：3 區合成串流的判定不能是 `too_high` |
| S1 | `Variant`、`LIBRARY`、`equivalent`、`total_min`（不算最後一趟後的休息） | C4 每一列的總時間；同等矩陣 |
| S2 | `pick_variant` 和 C5.3 的上限規則，取代 `trim_quality` 的減組 | 上限 45／50／無：z3b 不會變成 z3a；放不下時換一天，再不行就維持＋提醒 |
| S3 | `plan_sessions` 加欄位；`dose_step` 依 `variant_key` 判定 | 舊標題相容；縮量版不前進；非同等不前進 |
| S4 | 課表頁的替換抽屜（C5.4） | `test_panels_*`、前端快照 |
| S5 | 間歇卡圖 1–4、7（Part B）；session RPE 輸入 | `test_workout_review.py`：Sdec、TIZ、跑步 W′bal（τ 119／190 s） |
| S6 | `quality_gate.Z3`／`Z5` 換成 §A5.3 的數字；`z3b` 改來源字串；5 區休息改成走路 | `test_quality_gate.py` 的階梯和 detail 文字 |

規格同步：S1–S3 合併後跑 `/prp-spec`，更新 `docs/spec/plan-auto.spec.md` 和 `workout-review.spec.md`。`aerobic-base-readiness.md` §4.5 加一行指向這份文件。

---

## 來源

### 同儕審查（DOI 都是 Europe PMC 實際抓到的）

**間歇處方與 VO2max：**
- Buchheit M, Laursen PB. 2013. Sports Med 43:313–338, doi:10.1007/s40279-013-0029-x（Part I，全文）；43:927–954, doi:10.1007/s40279-013-0066-5（Part II，全文）
- Helgerud J, et al. 2007. MSSE 39:665–671. doi:10.1249/mss.0b013e3180304570
- Seiler S, Jøranson K, Olesen BV, Hetlelid KJ. 2013. Scand J Med Sci Sports 23:74–83. doi:10.1111/j.1600-0838.2011.01351.x
- Seiler S, Sjursen JE. 2004. Scand J Med Sci Sports 14:318–325. doi:10.1046/j.1600-0838.2003.00353.x
- Seiler S, Hetlelid KJ. 2005. MSSE 37:1601–1607. doi:10.1249/01.mss.0000177560.18014.d8
- Wen D, et al. 2019. J Sci Med Sport 22:941–947. doi:10.1016/j.jsams.2019.01.013
- Bacon AP, et al. 2013. PLoS One 8:e73182. doi:10.1371/journal.pone.0073182
- Milanović Z, et al. 2015. Sports Med 45:1469–1481. doi:10.1007/s40279-015-0365-0
- Rosenblat MA, Granata C, Thomas SG. 2022. Sports Med 52:1329–1352. doi:10.1007/s40279-021-01624-5（HIIT 讓血漿量和左心室質量增加；摘要）
- Ma X, et al. 2023. 菁英選手 HIIT 統合分析（Europe PMC 沒給 DOI）
- Midgley AW, McNaughton LR, Wilkinson M. 2006. Sports Med 36:117–132. doi:10.2165/00007256-200636020-00003
- Midgley AW, McNaughton LR. 2006. J Sports Med Phys Fitness 46:1–14（沒有 DOI）
- Midgley AW, McNaughton LR, Jones AM. 2007. Sports Med 37:857–880. doi:10.2165/00007256-200737100-00003
- Tschakert G, Hofmann P. 2013. IJSPP 8:600–610. doi:10.1123/ijspp.8.6.600
- Meyler S, Bottoms L, Muniz-Pumares D. 2021. Exp Physiol 106:1410–1424. doi:10.1113/ep089565
- Laursen PB, Jenkins DG. 2002. Sports Med 32:53–73. doi:10.2165/00007256-200232010-00003
- Billat VL, et al. 1999. MSSE 31:156–163. doi:10.1097/00005768-199901000-00024
- Billat VL, et al. 2000. EJAP 81:188–196. doi:10.1007/s004210050029
- Billat VL, et al. 2001. IJSM 22:201–208. doi:10.1055/s-2001-16389
- Esfarjani F, Laursen PB. 2007. J Sci Med Sport 10:27–35. doi:10.1016/j.jsams.2006.05.014
- Rønnestad BR, et al. 2015. Scand J Med Sci Sports 25:143–151. doi:10.1111/sms.12165
- Rønnestad BR, et al. 2020. Scand J Med Sci Sports 30:849–857. doi:10.1111/sms.13627
- Fleckenstein D, Braunstein H, Walter N. 2025. Front Sports Act Living 6:1507957. doi:10.3389/fspor.2024.1507957（全文）
- Lenk M, et al. 2025. Physiol Rep 13:e70573. doi:10.14814/phy2.70573
- Stöggl T, Sperlich B. 2014. Front Physiol 5:33. doi:10.3389/fphys.2014.00033

**閾值訓練與菁英實務：**
- Haugen T, Sandbakk Ø, Seiler S, Tønnessen E. 2022. Sports Med Open 8:46. doi:10.1186/s40798-022-00438-7（全文）
- Casado A, Foster C, Bakken M, Tjelta LI. 2023. IJERPH 20:3782. doi:10.3390/ijerph20053782
- Casado A, et al. 2021. JSCR 35:2525–2531. doi:10.1519/jsc.0000000000003176
- Smith CG, Jones AM. 2001. EJAP 85:19–26. doi:10.1007/s004210100384
- Ruiz-Alias SA, et al. 2024. JSCR 38:306–310. doi:10.1519/jsc.0000000000004609

**組休與 W′：**
- Jones AM, Vanhatalo A. 2017. Sports Med 47:65–78. doi:10.1007/s40279-017-0688-0（全文）
- Chidnok W, et al. 2013. Am J Physiol Regul Integr Comp Physiol 305:R1085–92. doi:10.1152/ajpregu.00406.2013
- Vassallo C, et al. 2020. EJAP 120:219–230. doi:10.1007/s00421-019-04266-8
- Galán-Rioja MÁ, et al. 2023. Eur J Sport Sci 23:1259–1268. doi:10.1080/17461391.2022.2142675
- Black MI, et al. 2023. MSSE 55:235–244. doi:10.1249/mss.0000000000003039
- Bourgois G, et al. 2023. EJAP 123:2791–2801. doi:10.1007/s00421-023-05268-3
- Dupont G, et al. 2004. MSSE 36:302–308. doi:10.1249/01.mss.0000113477.11431.59
- Tardieu-Berger M, et al. 2004. EJAP 93:145–152. doi:10.1007/s00421-004-1189-z
- 其餘（Skiba 2012、Ferguson 2010、Caen 2019／2021、Thevenet 2007／2008、Ben Abderrahman 2013）：見 `interval-adaptation.md` 的來源

**上坡與下坡：**
- Barnes KR, et al. 2013. IJSPP 8:639–647. doi:10.1123/ijspp.8.6.639
- Ferley DD, Hopper DT, Vukovich MD. 2016. IJSM 37:958–965. doi:10.1055/s-0042-109539
- Ferley DD, Vukovich MD. 2015. JSCR 29:1855–1862. doi:10.1519/jsc.0000000000000834
- Ferley DD, Vukovich MD. 2019. JSCR 33:1354–1361. doi:10.1519/jsc.0000000000001934
- Vernillo G, et al. 2017. Sports Med 47:615–629. doi:10.1007/s40279-016-0605-y
- Vernillo G, et al. 2015. J Sports Sci 33:1998–2005. doi:10.1080/02640414.2015.1022870
- Bontemps B, et al. 2025. Eur J Sport Sci 25:e12240. doi:10.1002/ejsc.12240

**暖身與緩和：**
- Bishop D. 2003. Sports Med 33:483–498. doi:10.2165/00007256-200333070-00002（Warm up II）
- McGowan CJ, et al. 2015. Sports Med 45:1523–1546. doi:10.1007/s40279-015-0376-x
- Burnley M, Doust JH, Jones AM. 2005. MSSE 37:838–845. doi:10.1249/01.mss.0000162617.18250.77
- Ingham SA, et al. 2013. IJSPP 8:77–83. doi:10.1123/ijspp.8.1.77
- Panascì M, et al. 2025. IJSPP 20:99–108. doi:10.1123/ijspp.2023-0468
- Van Hooren B, Peake JM. 2018. Sports Med 48:1575–1595. doi:10.1007/s40279-018-0916-2

**監測指標：**
- Buchheit M. 2014. Front Physiol 5:73. doi:10.3389/fphys.2014.00073（全文表 1）
- Lamberts RP, et al. 2010. Scand J Med Sci Sports 20:449–457. doi:10.1111/j.1600-0838.2009.00977.x
- Glaister M, et al. 2008. JSCR 22:1597–1601. doi:10.1519/jsc.0b013e318181ab80
- Bellenger、Aubry、Le Meur、Mann、Foster、Girard：見 `interval-adaptation.md` 的來源

### 教練與平台來源（非同儕審查）

- 台灣教練：先 3 區；5 區每趟 ≥ 2 分、每週 ≤ 2 次、間隔 ≥ 2 天。
- 你的筆記（`<notes>\300 Sport\`）：
  - `60 🏃 有氧訓練/間歇訓練.md:35-36, 107, 114, 208`
  - `60 🏃 有氧訓練/丹尼爾的跑步方程式筆記 還有課表.md:33, 45, 47, 56, 75, 97, 115`
  - `70 ⏳ 周期化訓練/越野跑周期化訓練(晏慶、K天王、丹尼爾).md:88, 100, 109, 115-117`
  - `65 ⚡ 功率訓練/功率區間說明與訓練目的 --star.md:50-63, 84-102, 132-170`
  - `70 ⏳ 周期化訓練/palladino基礎期訓練.md:46, 63, 86`
  - `65 ⚡ 功率訓練/研討會整理/研討會Analyzing Interval Training in WKO4 - Part 1.md:74-158`
- CTS／Koop：https://trainright.com/decoding-ultramarathon-interval-workouts/
- Uphill Athlete：見 `aerobic-base-readiness.md` §2.1；ME：https://uphillathlete.com/aerobic-training/vertical-beast-mode-what-is-muscular-endurance-why-it-is-important-for-any-alpinist-or-mountaineer-and-how-do-you-train-it/
- Stryd：https://help.stryd.com/en/articles/14360092 ；https://blog.stryd.com/2021/08/04/what-is-critical-power/ ；https://help.stryd.com/en/articles/8901356
- TrainingPeaks：https://help.trainingpeaks.com/hc/en-us/articles/36837856738957
- intervals.icu 論壇：https://forum.intervals.icu/t/predicted-wbal-depletion/27198 ；https://forum.intervals.icu/t/heart-rate-recovery-and-fitness-on-activities/117
- GoldenCheetah：https://github.com/GoldenCheetah/GoldenCheetah/wiki/UG_Glossary
- Canova special block：https://articles.sweatelite.co/renato-canovas-special-block-explained/ ；https://runnersconnect.net/special-block-training/

**未驗證：**
- Daniels 巡航間歇的工休比、I ≤ 週跑量 8%（只有書評網站）。
- Pfitzinger 的課表（只有二手網頁）。
- intervals.icu 間歇表的欄位清單（第三方）。
- Rønnestad 2015 的實際課表（摘要沒寫）。
- Stryd 上坡功率的準確度。
