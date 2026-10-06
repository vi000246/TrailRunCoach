# LTHR 不可信時，飄移測試和輕鬆跑測試要用什麼強度（SP-208）

> 調查日期：2026-10-06。只做調查，沒有改程式。
> 標記：**已驗證（全文）**＝這次讀到原文；**已驗證（摘要）**＝讀到 Europe PMC 的摘要；**教練級**＝教練或平台的做法；**推估**＝我的延伸；**未找到來源**＝查了沒找到。網頁經擷取工具摘錄後閱讀，引號內是它回報的原文。
> 既有文件已查過的不重查：`aerobic-base-readiness.md` §6（飄移測試流程）、`zones-and-thresholds.md` §2.3、§2.6（LTHR 為什麼測不準、可信度檢查）、`drift-algorithm.md`。

## 摘要

1. **徐國峰的 90 分鐘測試本來就不需要 LTHR。** 他的流程是「用 E 配速跑 90 分鐘」，E 配速從 RQ 跑力（比賽成績）查，心率是量出來的結果，不是目標 [L1]。所以「LTHR 可信度低」對這個測試本身沒有影響。
2. **問題出在 app 把它改成了心率上限。** 課表的主課寫「心率 1 區（≤ AeT）」，推到 COROS 的主課是心率區間，判定「有沒有全程 1 區」也用 AeT。沒有實測 AeT 時，AeT＝0.89 × LTHR，所以整堂課都綁在不可信的 LTHR 上。這有兩個後果：
   - 照手錶的心率提醒放慢，心率就飄不上去，**測出來的飄移會偏小**（假的通過）。
   - LTHR 估低時，用正確的 E 配速跑也可能被判「不是全程 1 區」，測試不算數。
3. **LTHR 不可信時，強度可以這樣訂（依可靠度）：**
   - 最好：**E 配速**，從最近一場比賽成績查（RQ 跑力或 Daniels VDOT 表）[L1][L2]。
   - 有 Stryd 和實測 CP：**固定功率 75–80 % CP**（Palladino 1C，app 已經這樣用在 UA 測試）。
   - 都沒有：**講話測試**，能講完整句子的最快配速 [L3][L4]；自覺強度約「輕鬆」（Borg 6–20 量表 11 左右 ≈ 第一乳酸閾值 [L5]）。
   - 不要用：180 − 年齡（沒找到驗證研究）、% 最大心率公式（個人誤差大）。
4. **UA／Evoke 的 AeT 測試起點不用準。** 測完會告訴你下次 +5 或 −5 bpm，「It may take several attempts」[L6]。起點用講話測試的配速或固定功率就好。
5. **提醒會一直出現，是因為來源是自動估算就一律「低」，只有做 30 分鐘 LTHR 測試才會消失。** Friel 的 30 分鐘測試不需要先知道 LTHR [L7]。手動輸入一個數字會變成「中」、提醒消失，但不會比較準。
6. 建議開 4 張單（§5），最優先的是「90 分鐘測試改用配速或功率當目標、不設心率上限」和「飄移判讀檢查配速有沒有維持」。

## 1. 現況

### 1.1 提醒從哪裡來、為什麼一直出現

| 步驟 | 內容 | 程式 |
|---|---|---|
| 來源判斷 | LTHR 的來源是自動估算、手錶、WKO5 預設 → 「LTHR 來源是自動估算，不是測試：一開始就是低信心」 | `backend/engine/threshold_confidence.py:125`、`:293-299` |
| 信心 | 來源在低信心清單裡，就是「低」，不管其他訊號 | `threshold_confidence.py:479-485` |
| 警示文字 | 「LTHR 可信度低：心率目標可能不準（{第一個訊號}）」 | `threshold_confidence.py:600-601` |
| 顯示在哪 | 課表編輯器：只要有任何一步用心率目標，就顯示這個警示 | `backend/api/plan_sessions.py:101`、`:120-126`；`backend/static/workout_editor.js:179-183`、`:567`、`:878` |
| 測試建議 | 只有來源這一個訊號時，「做 LTHR 測試」的建議是低優先，不會讓測試指標變黃；但編輯器的警示照樣出現 | `threshold_confidence.py:581-599` |
| 怎麼消失 | 做 Friel 30 分鐘測試並套用（來源變成測試 → 高）；或手動輸入（來源變成手動 → 中，`MID_SOURCES`） | `threshold_confidence.py:126`、`:632-660`（測試結果）、`:677-685`（套用） |

- 所以「一直出現」是設計：來源是估算，就一直低，直到測過。
- 手動輸入能讓提醒消失，但數字沒有比較準。這是個小漏洞。

### 1.2 徐國峰 90 分鐘測試在 app 裡長什麼樣

| 部分 | 內容 | 程式 |
|---|---|---|
| 課表文字 | 目標：「配速固定在 E 配速，不要調」＋「心率 1 區（≤ AeT {數字}）」 | `backend/engine/aet_test.py:549-552` |
| 推到 COROS | 暖身＋主課 80 分，主課的強度是**心率區間**＝輕鬆跑上限（`easy_hr`），步驟名稱「固定 E 配速，不要調（心率 1 區）」 | `backend/sync/coros_workouts.py:345-352`、`:161-171` |
| 課表編輯器 | 主課的目標是「自動・輕鬆」，解析成心率 ≤ 輕鬆跑上限 | `backend/engine/workout_steps.py:445-454`、`:227-239` |
| 輕鬆跑上限 | 課表心率區間的 Z2 上緣；有**實測** AeT 才用 AeT；沒有時是 0.89 × LTHR 或 COROS 乳酸閾 Z2 上緣（80–90 % LTHR） | `backend/engine/hr_profile.py:264-300`、`zones.py:478` |
| 分析（測試頁） | 第 10 分和第 90 分的心率（各 ±1 分鐘平均），(B − A) ÷ A；平路、停 ≤ 30 秒 | `aet_test.py:292-330`、`quality_gate.py:400-416` |
| 分析（間歇門檻） | 同上，再加「心率全程 1 區：平均 ≤ AeT + 3、超過的時間 ≤ 10 %」 | `backend/engine/base_check.py:176-180`、`:208-246` |

**app 沒有的東西**：

- **沒有 E 配速。** 整個 repo 沒有 VDOT 或 RQ 跑力的計算（只有停訓用的 FVDOT 係數，`reentry.py:74-77`）。課表寫「E 配速」，但沒有給數字。
- **沒有檢查配速有沒有維持。** `xu_drift_of` 只看心率（`quality_gate.py:400-416`），`xu_run` 檢查平路、停頓、1 區、溫度，沒有比較第 10 分和第 90 分的配速或功率（`base_check.py:208-246`）。
- 有閾值配速的推估（CP 或 LTHR 推出來，`thresholds.py:236-262`），但沒有用在 90 分鐘測試。

### 1.3 UA／Evoke 的 AeT 飄移測試

- 主課是**固定功率** 0.75 × CP（±3 %），心率只當起點參考（`aet_test.py:76`、`:486-487`；`coros_workouts.py:356-365`）。
- 起點心率＝自動估算的 AeT，沒有時 0.89 × LTHR − 5（`aet_test.py:75`、`:479-483`）。這裡也綁 LTHR，但只影響暖身的心率上限。
- 判讀照 UA 的三段，結果偏高或偏低就建議下次 ±5 bpm（`aet_test.py:137-153`、`:395-404`）。

## 2. 來源

### 2.1 徐國峰 90 分鐘測試的強度怎麼訂

| 說法 | 來源 | 標記 |
|---|---|---|
| 「必須知道自己的E配速 (可使用RQ查詢)」；「用此配速跑到第10分鐘的心率為A」；「需要持續跑90分，盡量維持在E配速(中途一定要喝水，但每次補給不能停超過30秒)」；「(B-A)÷A×100%」；「全程的平坦的路段」 | 徐國峰部落格〈如何知道自己的有氧基礎是否穩固？RQ自動幫你算〉，2016-12-08 [L1] | 已驗證（全文） |
| 讀書心得版本：「當天氣溫必須是攝氏25度以下」；「必須知道自己的E配速（可用本書的配速區間表或RunningQuotient來取得）」；標題寫「測試在區間1的情況跑90分鐘」 | 使用者筆記〈跑者都該懂的跑步數據，讀書心得〉 | 已驗證（筆記原文） |
| RQ 跑力和 Daniels VDOT 都是用**比賽成績**推出配速區間，不是用心率 | 使用者筆記（「這個公式是根據跑步方程式這本書寫的公式」）；VDOT 計算頁 [L2] | 已驗證（筆記）；VDOT 頁是二手 |
| Daniels 的 E 強度約 59–74 % VO2max、65–79 % 最大心率 | 二手整理（coachray.nz 等）[L2] | 未驗證（沒有讀原書） |

讀完的判斷：

- 徐國峰的流程裡**固定的是配速**，心率是讓它飄的。「區間 1」指的是 E 配速這個強度，不是叫你把心率壓在某個數字以下。
- 所以這個測試要的強度輸入是「一場比賽成績」，不是 LTHR。

### 2.2 其他飄移測試：固定什麼、起點怎麼選

| 測試 | 固定什麼 | 起點怎麼選 | 來源 | 標記 |
|---|---|---|---|---|
| UA 心率飄移 | 「Don't slow down or speed up」；「keep the conditions (exertion level, pace, incline) unchanged」 | 「you should be able to carry on a conversation in full sentences」；結果 < 3.5 % 下次 +5 bpm，> 5 % 下次降低；「It may take several attempts」 | Uphill Athlete [L6] | 已驗證（全文），教練級 |
| Evoke | 固定速度；或固定心率、看配速掉多少 | 暖身不超過鼻呼吸的配速；第 10 分鐘心率已高 10 下還在升就停 | `aerobic-base-readiness.md` §6.2 | 既有文件已驗證 |
| Friel decoupling | 在 AeT 附近穩定跑 1–2 小時，看前後半的 Pa:HR 或 Pw:HR | 要先知道 AeT | `aerobic-base-readiness.md` §6.2 | 既有文件已驗證 |

重點：所有版本都是**固定輸出（配速、速度或功率），讓心率飄**；Evoke 的另一種做法是固定心率、看配速掉多少，那時看的是**配速**，不是心率。沒有一個版本是「心率封頂、只看心率」。

### 2.3 不靠 LTHR 的強度錨點

| 錨點 | 證據 | 來源 | 標記 |
|---|---|---|---|
| 比賽成績推的 E 配速 | RQ、VDOT 都從比賽成績推；不需要任何心率值 | [L1][L2] | 教練級 |
| 功率 % CP | Palladino 1C「EZ aerobic」75–80 % CP、Z2「Endurance / long run」80–88 % CP | `backend/engine/zones.py:17-29`（Palladino 2017 表） | 教練級，既有程式已引用 |
| 講話測試 | 抽血和訓練讓 VT 改變後，「changes in the exercise intensity at VT and at the last positive stage of the TT matched each other」 | Foster 2008 [L3] | 已驗證（摘要） |
| | 久坐成人：訓練強度設在漸增測試「最後一個能舒服講話的階段」的下一階，心率、RPE 才合適 | Foster 2009 [L4] | 已驗證（摘要），14 人 |
| | 「+」階段約 82 ± 7 % 最大心率，和乳酸閾值的關係比 VT 強 | Persinger 2004（`aerobic-base-readiness.md` §6.2） | 既有文件已驗證 |
| 自覺強度 | 2,560 人：乳酸閾值（LT）時 RPE 10.8 ± 1.8，個人無氧閾值 13.6 ± 1.8（Borg 6–20） | Scherr 2013 [L5] | 已驗證（摘要） |
| 180 − 年齡（MAF） | 沒找到對照實測 AeT 的驗證研究 | — | **未找到來源**；`detraining.md` L239 已說它是公式估算 |
| % 最大心率 | 同樣 % 最大心率，乳酸閾值可以落在 60–90 % 最大心率（Iannetta 2020） | `backend/engine/hr_profile.py:79-81` | 既有程式已引用 |
| 手錶估的 LTHR | 平均誤差 9–11 bpm（COROS 8.9） | Lu 2025（`threshold_confidence.py:132`） | 既有程式已引用 |

讀完的判斷：

- **比賽成績和 CP 測試**是最好的錨點：一個是跑出來的速度，一個是測出來的功率，都不經過心率。
- **講話測試**有研究支持它跟 VT 一起移動 [L3]，而且不用任何裝置；缺點是每個人「舒服講話」的判斷不同，要用「完整句子」而不是「勉強講幾個字」。
- RPE 約 11 只是平均值，±1.8 的個人差異約等於一整個區間，只能當輔助（推估）。

### 2.4 要讓 LTHR 變可信

| 說法 | 來源 | 標記 |
|---|---|---|
| 「Do a 30-minute time trial all by yourself」；「at 10 minutes into the test, click the lap button … average heart rate was for the last 20 minutes」；不需要先知道任何心率值 | Friel〈Quick Guide to Setting Zones〉[L7] | 已驗證（全文），教練級 |
| 「the more times you do this test the more accurate your LTHR is likely to become as you will learn to pace yourself better」 | 同上 | 已驗證（全文） |
| 同一位作者的另一頁寫的是 20 分鐘、平均 −5 %（那是自行車的 FTHR） | Friel 個人網站 [L8] | 已驗證（全文）；跑步用 30 分鐘版 |
| 涼天、戴胸帶、不要用一般長跑代替 | `zones-and-thresholds.md` §2.3 | 既有文件已驗證 |

app 已經有這個測試（`threshold_confidence.TEST_TEMPLATE` 的 `lib:friel_lthr30`，`:616`），測完會算候選值、按「套用」才寫入。

## 3. 落差

| # | 來源 | app | 落差 |
|---|---|---|---|
| 1 | 徐國峰：固定 E 配速，心率自己飄 | 課表和 COROS 主課是心率上限 | **方向相反**：照心率上限跑，飄移會被壓低 |
| 2 | E 配速從比賽成績查 | 沒有 E 配速的數字 | **缺** |
| 3 | 測試裡強度的定義是配速 | 「全程 1 區」用 AeT＝0.89 × LTHR 判定（`base_check.py:176-180`） | **綁到不可信的 LTHR**：LTHR 估低時，正確的 E 配速會被判不合格 |
| 4 | 所有飄移測試都要固定輸出 | 90 分鐘測試的分析沒有檢查配速或功率有沒有維持 | **缺**：放慢造成的「假通過」抓不到 |
| 5 | UA：起點用講話測試，結果會自己修正 | 起點心率 0.89 × LTHR − 5 | 小落差：只影響暖身上限 |
| 6 | 提醒應該只出現在會受 LTHR 影響的地方 | 只要有心率目標就出現，包括不需要 LTHR 的 90 分鐘測試 | 提醒太廣 |
| 7 | 手動輸入不會讓數字變準 | 手動輸入 → 中信心 → 提醒消失 | 小漏洞 |

## 4. 結論與建議

### 4.1 給使用者現在就能用的做法（不用等改程式）

**徐國峰 90 分鐘測試：**

1. 用最近一場比賽（5 K～半馬，涼天、全力）到 RQ 跑力或 VDOT 計算器查 E 配速。
2. 有 Stryd 的話，可以改用固定功率約 75–80 % CP（CP 要是實測的）。兩個都有時，選比較新的那個。
3. 都沒有：前 10 分鐘找「能講完整句子」的最快配速，之後就固定這個配速。
4. 平路、25 °C 以下、補給每次停 ≤ 30 秒。**不要理會手錶的心率上限提醒**，配速不要因為心率升高而放慢，否則測出來的飄移會偏小。
5. 跑完 app 的分析可能因為「心率不是全程 1 區」不算數（落差 3）。這是 app 的問題，不是測試失敗；看「第 10 分 → 第 90 分」的數字自己判讀即可。

**UA／Evoke 的 AeT 測試（輕鬆跑強度的測試）：**

1. 起點用講話測試的配速或 75 % CP 的功率。
2. 照結果調整：< 3.5 % 下次起點 +5 bpm，> 5 % 下次 −5 bpm，3.5–5 % 的前半心率就是 AeT。可能要測 2–3 次。
3. 測出 AeT 並套用後，輕鬆跑上限就不再是 0.89 × LTHR，而是實測值（`hr_profile.py:287-289`）。之後的 90 分鐘測試也會用它。

**讓 LTHR 提醒消失：** 選一個涼天，戴胸帶做 Friel 30 分鐘獨跑，套用結果。

### 4.2 給 app 的方向

- 90 分鐘測試的主課改成**配速或功率目標，心率不設上限**；E 配速的來源依序是：使用者輸入的比賽成績 → 實測 CP 的 75–80 % → 不設目標、寫講話測試。
- 測試課（90 分鐘、UA、Evoke、Friel 30 分鐘）都**不顯示 LTHR 可信度警示**：它們本來就不靠 LTHR，或就是在測 LTHR。
- 90 分鐘測試的判讀**不用 1 區條件**（使用者照配速跑），改加**輸出維持**檢查：第 80–90 分的配速（有功率用功率）不能比第 10–20 分慢超過 5 %（門檻推估，借 UA 的 5 %）。
- 手動輸入的 LTHR 維持「中」，但警示文字改成說明「手動輸入沒有驗證」。

## 5. 建議開的單

### 單 1：90 分鐘測試用配速或功率當目標，不設心率上限（P1）

- 範圍：`aet_test.session`（xu90）、`coros_workouts._aet_test_steps`、`workout_steps._aet_test` 的主課目標改成配速（有 E 配速時）或功率 75–80 % CP（有實測 CP 時）；都沒有時不設目標，說明寫講話測試。暖身照舊用輕鬆跑上限。
- 驗收條件草案：
  - 有 E 配速時，推到 COROS 的主課是配速區間（± 3 %，推估），不是心率。
  - 只有實測 CP 時，主課是功率 75–80 % CP。
  - 都沒有時，主課沒有強度目標，步驟名稱寫「能講完整句子的配速，固定不要調」。
  - 這堂課在編輯器不顯示 LTHR 可信度警示（測試課都一樣，包括 Friel 30 分鐘）。
  - 課表文字不再出現「心率 1 區（≤ AeT …）」。
- 要你決定：E 配速從哪裡來（見單 3）；功率區間用 75–80 % 還是 80–88 % CP（Palladino 1C 或 Z2）。

### 單 2：90 分鐘測試的判讀檢查配速有沒有維持，不再用 1 區條件（P1）

- 範圍：`base_check.xu_run` 和 `aet_test.analyze_xu` 加輸出維持檢查；排進課表的 90 分鐘測試不套 `_z1`；被動判讀（一般長跑）保留 `_z1`。
- 驗收條件草案：
  - 第 80–90 分平均配速（或功率）比第 10–20 分慢 > 5 % → 不採用，原因寫「後段放慢了 N %：飄移會偏小，下次配速固定」。
  - 排進課表的測試，心率超過輕鬆跑上限也照算。
  - 一般長跑仍要符合 1 區才算數（現有行為，測試鎖住）。
  - 沒有速度也沒有功率時，照現在只看心率，並在判讀卡寫「沒辦法確認配速有沒有維持」。
- 要你決定：5 % 的門檻（推估）可不可以；一般長跑要不要也加這條。

### 單 3：從比賽成績算 E 配速（P2）

- 範圍：設定頁輸入一場比賽（距離、時間、日期），用 Daniels VDOT 公式算 E 配速範圍；也可以從活動裡被認成比賽的那幾場自動帶入（`threshold_confidence.RACE_WORDS` 已經有認比賽的規則）。越野賽不算（爬升會讓成績失真）。
- 驗收條件草案：
  - 輸入 10 K 45:00，E 配速和 VDOT 計算器（[L2]）一致（±5 秒/km）。
  - 比賽超過 180 天顯示「舊了」（推估）。
  - 只用在 90 分鐘測試和說明，不改其他課表（先不動區間）。
- 要你決定：要不要做（也可以只寫說明，請使用者自己去 RQ 查）；自動帶入的比賽要不要使用者確認。

### 單 4：LTHR 可信度提醒的範圍與文字（P2）

- 範圍：`threshold_confidence.warn_of` 和 `workout_editor.js`：只在「心率目標是從 LTHR 算出來」的步驟顯示（例如 3 區、閾值、輕鬆跑上限沒有實測 AeT 時）；測試課不顯示；只有「來源是估算」這一個原因時，文字改成可行動的：「LTHR 是估算的；輕鬆跑可以改用講話測試的配速，做一次 30 分鐘測試後這個提醒會消失」。手動輸入的 LTHR 顯示「手動輸入，沒有驗證」的小字。
- 驗收條件草案：
  - 實測 AeT 存在時，只有輕鬆跑的課不顯示警示（輕鬆跑上限是實測 AeT）。
  - 測試課（90 分鐘、UA、Evoke、Friel 30 分鐘、最大心率）不顯示。
  - 間歇課照舊顯示。
  - 文字附「為什麼」和「怎麼讓它消失」。
- 要你決定：手動輸入要不要維持「中」（讓提醒消失），還是也當「低」。

## 6. 要你決定的事

1. 90 分鐘測試的強度來源順序：比賽成績的 E 配速 → 功率 → 講話測試（建議）。
2. 要不要在 app 裡算 E 配速（單 3），還是只寫說明請你去 RQ 查。
3. 「後段放慢 > 5 % 不算數」的門檻。
4. 手動輸入的 LTHR 要不要讓提醒消失。
5. 最直接的解法：要不要先找一個涼天做 Friel 30 分鐘測試。做了之後提醒就會消失，輕鬆跑上限也會跟著更準。

### 6.1 已決定的事（使用者，2026-10-06：「照你講的做，先存起來」）

照本文的建議定案，之後開工照這裡做：

1. 90 分鐘測試的強度來源順序：使用者輸入的比賽成績算出的 E 配速 → 實測 CP 的 75–80 %（Palladino 1C）→ 不設目標、寫講話測試。主課不設心率上限（單 1）。
2. app 自己算 E 配速（單 3，P2）：使用者輸入比賽成績；從活動自動認出的比賽要使用者確認後才用；越野賽不算。
3. 「第 80–90 分比第 10–20 分慢 > 5 % 不採用」照用（推估）。先只套在排進課表的 90 分鐘測試，一般長跑維持現有的 1 區條件（單 2）。
4. 手動輸入的 LTHR 維持「中」信心（提醒會消失），但顯示「手動輸入，沒有驗證」的小字（單 4）。
5. 測試課（90 分鐘、UA、Evoke、Friel 30 分鐘、最大心率）都不顯示 LTHR 可信度警示（單 1、單 4）。
6. 使用者自己的待辦：找一個 25 °C 以下的日子，戴胸帶做一次 Friel 30 分鐘測試並套用。

開工順序建議：單 1 和單 2 一起做（同一堂課的課表與判讀），再做單 4，最後單 3。

## 7. 限制

- 徐國峰的 90 分鐘測試和 UA 的測試都是教練級，長度和門檻都沒有同儕審查的驗證（`aerobic-base-readiness.md` §6.2 已寫）。
- Daniels E 強度的百分比只讀到二手整理，沒有讀原書。
- 講話測試的研究多是實驗室漸增測試、樣本小（Foster 2009 只有 14 位久坐成人），沒有越野跑者。
- 「心率封頂會讓飄移偏小」是從定義推的（推估），沒有找到量化研究。
- 沒有讀使用者的實際資料，也沒有確認目前的 LTHR 數字；`zones-and-thresholds.md` §1.6 之前估計範例跑者的 LTHR 可能偏低。

## 8. 讀不到的來源

| 來源 | 為什麼讀不到 | 會影響什麼 |
|---|---|---|
| Daniels J. *Daniels' Running Formula*（E 強度定義、VDOT 表） | 書，沒有原文 | E 強度的 % VO2max、% 最大心率 |
| RQ 跑力的 E 配速算法 | 網站需要登入，沒有公開公式 | 徐國峰原版用的 E 配速和 VDOT 版本差多少 |
| Foster 2008、2009 全文 | 付費 | 講話測試的實際階段和心率 |

## 參考

- [L1] 徐國峰。如何知道自己的有氧基礎是否穩固？RQ自動幫你算。2016-12-08。http://rocky549.blogspot.com/2016/12/rq.html （2026-10-06 讀取）
- [L2] Jack Daniels VDOT training paces（二手計算頁）。https://runningtimecalculator.com/en/training-paces.html ；Daniels 強度表的二手整理：https://www.coachray.nz/2023/05/03/jack-daniels-running-intensity/ （2026-10-06 讀取）
- [L3] Foster C, et al. The talk test as a marker of exercise training intensity. *J Cardiopulm Rehabil Prev* 2008. doi:10.1097/01.hcr.0000311504.41775.78. PMID 18277826.
- [L4] Foster C, et al. Translation of submaximal exercise test responses to exercise prescription using the Talk Test. *J Strength Cond Res* 2009. doi:10.1519/jsc.0b013e3181c02bce. PMID 19972627.
- [L5] Scherr J, Wolfarth B, Christle JW, Pressler A, Wagenpfeil S, Halle M. Associations between Borg's rating of perceived exertion and physiological measures of exercise intensity. *Eur J Appl Physiol* 2013. doi:10.1007/s00421-012-2421-x. PMID 22615009.
- [L6] Uphill Athlete. Heart Rate Drift Test. https://uphillathlete.com/aerobic-training/heart-rate-drift/ （2026-10-06 讀取）
- [L7] Joe Friel. Joe Friel's Quick Guide to Setting Zones. TrainingPeaks. http://www.trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones （2026-10-06 讀取）
- [L8] Joe Friel. A Quick Guide to Setting Zones. https://joefrieltraining.com/a-quick-guide-to-setting-zones/ （2026-10-06 讀取；20 分鐘、−5 % 的 FTHR 版本）
- 既有文件：`aerobic-base-readiness.md` §1.1、§6；`zones-and-thresholds.md` §1.6、§2.3、§2.6；`detraining.md` L239；`back-to-back-and-long-day.md`（徐國峰 90 分鐘的引用）。
- 使用者筆記（Obsidian，唯讀）：`300 Sport/60 🏃 有氧訓練/跑者都該懂的跑步數據，讀書心得.md`、`300 Sport/70 ⏳ 周期化訓練/400 爆發力、敏捷性、專項耐力周期.md`。
