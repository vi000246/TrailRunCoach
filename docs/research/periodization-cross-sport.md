# 耐力運動的周期化：各周期怎麼排，以及 TrailRunCoach 還缺什麼

> 研究日期：2026-10-05 · 模式：deep · 只做調查與開單，沒有改程式。
> 引用格式：`[n]` 是外部來源（文末參考文獻）；`docs/research/…` 是本 repo 已查證過的文件，直接沿用、不重做；`backend/…:行號` 是程式位置（HEAD `c320074`）。
> 標記：**已驗證**＝這次讀到原文或摘要；**教練級**＝教練的書或文章，沒有對照試驗；**推估**＝我把別的項目或族群的結論延伸到 app。
> 證據資料夾：`~/Documents/Endurance_Periodization_Research_20261005/`（`sources.jsonl`、`evidence.jsonl`、`notes/cluster_A–F.md`）。

## 摘要

1. **周期的骨架不用改。** 世界級的越野滑雪、划船、自行車、游泳、長跑選手都用同一個形狀：一般準備 → 專項準備 → 減量 → 比賽 → 轉換，從比賽日往回排 [30][96][151]。app 的「基礎 → 專項 8 週 → 減量 14 天 → 賽事 → 恢復 → 轉換」落在各項目的範圍內。
2. **區塊周期化和反向周期化不值得做。** 統合分析裡區塊只有小幅優勢（VO2max SMD 0.40），而且試驗品質低（PEDro 3.7／10）、對象都是受過訓練的選手 [4][35]；條件配得比較好的 12 週試驗沒有差別 [76]。反向和傳統順序的結果相同 [6][7][69]。
3. **有證據的是「骨架＋依回饋調整」。** 休閒跑者用個別化調整的計畫，10 公里進步 6.2 %，固定計畫 2.9 %；沒進步的人 0 % 對 21 % [299]。app 已經是這個方向（自動調整規則 A–E）。
4. **app 最明顯的缺口有四個**，都是「周期裡的規則太粗」：
   - B、C 賽事只有標記，不影響排課。
   - 減量固定 14 天，而且是減次數、不是縮短每堂。
   - 恢復週由歷史時數觸發，可能刪掉專項期最重要的那一次長天。
   - 賽後恢復期的量用「含減量週和比賽週的近 4 週平均」算，基準偏低。
5. **三個 app 完全沒有的東西**：下坡課依賽前倒數安排（保護效果只維持 3–6 週）[350]、高海拔行程前的高度適應提醒 [402]、用 HRV／安靜心率／主觀狀態調整當天的課 [299][41]。
6. **幾個常見說法沒有證據，不建議做**：年長者要 2:1 或 9 天周期（唯一的對照試驗沒發現年齡差異）[312]、依生理期調整課表 [278][308]、每週 10 % 規則 [169]。
7. 開單清單在 §6。研究單是 SP-94，新單 10 張（SP-95–SP-104），另有 4 張既有的單補上這次的證據。

## 1. 範圍與覆蓋率

- 檢索：30 個子題、57 個搜尋任務，加上 1 次補查。共 **419 個來源、1,272 條證據**，來自 119 個網站。
- 既有文件已涵蓋、這次不重做的：跑步各教練學派的分期（`coach-schools-zones-periodization.md`）、專項期逐週進展（`specific-phase-progression.md`）、Bompa 肌力分期（`bompa-periodization-strength.md`）、休息日（`rest-day-placement.md`）、停訓（`detraining.md`）、臨時賽事（`late-race-entry.md`）、背靠背（`back-to-back-and-long-day.md`）。
- **沒有查到的子題**：中文來源（徐國峰、田麥久、台灣教練文章）。搜尋額度在那之前用完，0 個來源。
- **沒查完的子題與原因（之後續查用）**：這個 session 的網路搜尋上限是 200 次（Claude Code 的 `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION`），所有搜尋 agent 共用。額度在第 46 個任務左右用完（2026-10-05 08:44 起），之後的任務只能直接抓已知網址。

  | 子題 | 受影響的任務 | 結果 | 還缺什麼 |
  |---|---|---|---|
  | Q30 中文來源 | `Q30#1`、補查 | 0 個來源 | 徐國峰／KFCS 的四周期、運動筆記與 Don1Don 的周期化文章、田麥久《運動訓練學》的分期、中文越野與百岳體能周期化 |
  | Q19 轉換期與休賽季 | `Q19#2`（0 個）；補查改用 Europe PMC 取得 15 個 | 有來源，但沒有教練端 | Daniels、Pfitzinger、Uphill Athlete 的轉換期；鐵人與跑者的休賽季研究 |
  | Q20 一季多場比賽 | `Q20#1`、`Q20#2`（只靠直接抓頁面，共 7 個） | 偏少 | 巔峰能維持多久、兩場重點賽事相隔 3–8 週怎麼再達峰、帶著訓練跑 B 賽的代價 |
  | Q17 減量 | `Q17#2`（學術角度，只抓到 2 個） | 偏少 | 超馬與越野的減量研究、最後一次長跑的時機、2024 年後的回顧 |
  | Q29 其他 app | 兩個角度都跑完，但廠商文件少 | 偏少 | TrainingPeaks ATP、Garmin、COROS、Runna、Xert 的規則 |

  續查的方法：開新的 session（額度重新計算），或先把 `CLAUDE_CODE_MAX_WEB_SEARCHES_PER_SESSION` 調高，然後說「繼續上次的研究」並指向證據資料夾。資料夾裡的 `RESUME.md` 有同一張表和可直接用的子題文字。
- **來源偏少的子題**：
  - 一季多場比賽：只有 TrainerRoad 的文件和教練文章，沒有試驗。
  - 其他 app 的自動分期：只有 TrainerRoad 寫出具體規則；TrainingPeaks ATP、Garmin、COROS、Runna、Xert 沒有資料。
  - 越野、超馬、登山的減量與賽後恢復：只有教練建議，沒有對照試驗。
- **引文可靠度**：部分引文是抓取工具摘要後的文字，不保證逐字相同。「轉換期」那一題的補查是在搜尋額度用完後，改用 Europe PMC 的 API 和搜尋結果頁找到的，內容多為論文摘要。要引用原文時請回原始頁面核對。

## 2. 周期化模型：哪一種有證據

| 模型 | 做法 | 證據 | 給 app 的結論 |
|---|---|---|---|
| 傳統（Matveyev、Friel） | 先量後強度；準備期佔整個周期的 2/3–3/4 [158]。Friel：Prep 2–4 週、Base 12、Build 8–9、Peak 1–2、Race 1、Transition 1–8 [18] | 挪威世界級教練全部採用傳統模型，再加每週 2–3 個重點日 [96] | 當預設骨架 |
| 區塊（Issurin） | 2–4 週的集中負荷，一次只練少數能力 [248][4] | 統合分析：VO2max SMD 0.40、Wmax 0.28，試驗平均 4.9 週、品質低 [4]。12 週對照試驗沒有差別 [76]。把低強度量集中成一週 160 % 沒有好處，情緒和恢復感變差 [36] | 不做。教練自己也說非進階選手用傳統較好 [15] |
| 反向 | 先強度、後加量 [6] | 鐵人 10 週試驗無差異 [6]；5K 的 12 週試驗無顯著差異，但傳統順序有 87 % 的人進步、反向 63 % [7] | 不做 |
| 波動式 | 一週內大幅變化 [5] | 只找到定義，沒有耐力項目的試驗 | 不做 |

對周期化本身的批評（Kiely、Afonso）：

- 沒有任何研究比較「有周期」和「有變化但沒周期」的訓練；現有研究偏差風險都很高 [20]。
- 相同的計畫在不同人身上結果差很多 [39]。
- 教練調查：71 % 說自己有做周期化，但只有 33 % 相信適應會照固定時程發生，76 % 反對固定的訓練目標 [40]。
- Kiely 的建議是「稀疏的規劃骨架」，內容隨新資訊調整 [39]。

這些批評反對的是固定順序和固定時程，不是反對有結構。一篇整理指出：總負荷相同時，順序影響很小，但有結構勝過沒有結構 [69]（教練文章）。

## 3. 各運動項目實際怎麼排一年

### 3.1 共同點（至少三個項目都有）

| 共同點 | 數字 | 項目與來源 |
|---|---|---|
| 低強度佔絕大多數，全年如此 | 75–95 % | 越野滑雪 90／5／5 [12][27]；划船 95 % [53]；職業自行車 Z1 84–91 % [85]；長跑 ≥ 80 % [151] |
| 每週 2–3 個重點日 | 滑雪選手每 3.0–3.6 天一次高強度 | [12][96][151][166] |
| 重點日不連排，量大和強度高很少同一天 | 只有 5 % 的日子同時量大又有強度 | [12][95] |
| 定期減負荷 | 每 8–9 天一個休息或減量日；每 3–4 週一個輕鬆週（量 −25–35 %） | [95][30] |
| 越接近比賽，專項比例越高 | 專項訓練佔比 48 % → 87 % → 92 % | 越野滑雪與冬季兩項金牌選手 [28] |
| 強度分配往極化移動 | 16 週試驗：前 8 週金字塔、後 8 週極化最好 | 跑步 [239][236]；划船最後 6 週 [25]；游泳 [155] |
| 減量保留強度、只減量 | 統合分析 8–14 天、量 −41–60 %、頻率減 ≤ 20 % | [207][242][249] |
| 肌力集中在準備期，賽季每週 1 次維持 | 停掉後 8 週內明顯退步 | [28][343][407][408] |
| 轉換期減量但不全停 | 全停 VO2max −10.1 %，減量 −4.8 % | 輕艇 [412]；自行車 [409][411] |

### 3.2 不同的地方

- **量的上限**：滑雪選手一年 750–1000 小時 [12]，跑者受衝擊負荷限制，用次數和距離計（每週 11–14 次）[151]。挪威教練把差異歸因於「機械與肌肉負荷」[369]。所以其他項目的時數不能直接搬到跑步（**推估**時要標明）。
- **減量的深度**：游泳可減 60–90 % [246]；奧運金牌滑雪選手只減 4–15 %，11 人裡有 10 人在賽前 48 小時內做了一堂高強度 [116]。常常比賽的項目沒有空間深度減量。
- **世界級長跑的做法**：每個大周期從峰值週量的 40–60 % 開始，用 8–12 週加回去 [151]；到最後 7–10 天才明顯減量 [151]；馬拉松後 7–14 天完全休息或很輕鬆，轉換期 1–2 週 [416]。

### 3.3 越野、超馬、登山

這一塊幾乎沒有對照試驗，以下除了特別註明，都是**教練級**或個案。

- **越野／超馬**：
  - 最後 8–10 週的訓練要像比賽：每公里爬升和下降對齊賽道 [131][175]。
  - 最大的背靠背放在賽前 4–5 週，最後 3 週不做 [190]。
  - 一個區塊最多 6 週就要休息 [211]。
  - 山區超馬的成績預測因子是每週爬升、強度和量 [104]（觀察研究）。
  - 一位世界賽第 11 名的女子選手：平均每週 8 小時 53 分、87 km、3,755 m 爬升；最大週 7,552 m，約為平均的 2 倍 [10]（個案）。
- **登山與多日行程**：
  - 至少準備 8 週，16–24 週較好 [114][115]。
  - 負重前先做至少 4 週不負重 [114][177]；之後每 2 週加體重的 5 %，到 15–20 %，上限 20–25 % [115][114]。
  - 加量 3 週、第 4 週減到約 50 % [114]。
  - 2–3 天的攀登減量 1 週就夠；大目標 10–14 天 [114][162]。
  - 這些和 `loaded-carry-training.md` 一致。
- **下坡的保護效果**（對照試驗，**已驗證**）：
  - 做過一次下坡跑之後，下一次的痠痛和肌肉損傷指標明顯較低。間隔 3 週和 6 週都有效，間隔 9 週就沒有了 [350]；3 週間隔有三個研究重複驗證 [351][352][353]。
  - 平常就做下坡反覆的越野跑者，下坡跑後 CK 較低（d = 0.64）、深蹲力量保留較多（d = 0.87）[357]。
  - 沒有找到劑量反應，也沒有「賽前最後一次該排在哪天」的研究。
- **高海拔**（指引與系統性回顧，**已驗證**）：
  - CDC：出發前 14 天內在 2,750 m 以上過至少 2 晚，越接近出發越好；3,000 m 以上每晚的睡眠高度增加不超過 500 m [402]。
  - 事前累積 7–104 小時的低氧暴露，急性高山症風險下降 12–73 %，暴露越多效果越大；對運動表現沒有幫助 [394]。
  - 回到平地 12 天後適應仍部分保留，2 週內再上山還有效 [400]。
  - 研究多針對 4,000–5,000 m，沒有 3,000–3,950 m 或「一天從登山口上到山屋」的資料。
- **交叉訓練**：
  - 2026 年的統合分析：只跑步和只騎車的組別，跑步表現沒有差別（g = 0.02），但信賴區間很寬，不能當成可以互換 [363]。
  - 鐵人選手的受傷多半跟在「跑步負荷單週增加 > 30 %」之後 [371]，所以除了總負荷，跑步要另外算一條。app 已經這樣做（`load_guard.py:84`）。

## 4. 各周期該怎麼排：證據與 app 現況

### 4.1 基礎期

- **長度**：教練給 12 週上下，範圍 6–16 週 [18][93][164]，沒有試驗比較過。
- **適應的時程**：粒線體約 2 週開始改善，微血管 4 週內，6–8 週趨於穩定；總量越大進步越多 [127][129]。每週不到 3 小時，6 個月內看不到有氧配速進步；5–6 小時要 3–4 個月 [168]（教練級）。
- **量怎麼加**：
  - 「每週 10 %」沒有證據 [169]。
  - Garmin-RUNSAFE 世代研究（5,205 人）：每週里程變化和 ACWR 幾乎不能預測受傷 [121]。
  - 能預測的是**單次**：一次跑步超過前 30 天最長那一次的 10 % 以上，過度使用傷害風險上升；超過 10–30 % 約高 64 %，加倍約高 128 % [311][160][122]。
- **基礎期放多少強度**：菁英在準備期有 78–91 % 是低強度 [25]，但不是零強度（滑雪選手每 3.6 天一次高強度 [12]）。
- **app 現況**：基礎期不分段，由解鎖條件（三區關卡、五區關卡）決定什麼時候加強度。這比固定的「Base 1／2／3」更接近 §2 的結論，**不用改**。

### 4.2 專項期

- **長度**：各項目 6–12 週，核心 6–8 週 [18][130][146][131]。app 固定 8 週，在範圍內。
- **原則**：越接近比賽越像比賽 [18]；每次只在量、每趟長度、休息三者之中擇一進展，不原封不動重複同一堂 [143]（教練級）。
- **強度分配**：前段金字塔、後段極化 [239]。這是 SP-75 的範圍。
- **app 現況**：長天有逐週表（`specific_phase.py:42`），背靠背最後一次在賽前 ≥ 21 天（`b2b.py:31–45`），和 [190] 一致。缺口在恢復週（§4.3）和下坡（§5 缺口 5）。

### 4.3 加量與恢復週

- **3:1 是慣例，不是生理定律**。它最常見 [199][23]；Koop 說它來自方便和傳統，他的上限是 6 週 [211]。沒有任何耐力研究直接比較 3:1 和 2:1 [198]。
- **恢復週減多少**：挪威 −25–35 % [30]；跑者 −20–35 % [194]；登山 −50 % [114]；自行車 −40–60 % [199]。app 是前 3 週平均的 65 %（−35 %），在範圍內，可以補上來源。
- **恢復週保留什麼**：挪威的做法是保留次數和強度，把每堂縮短 [30]。減量的統合分析也是保留強度才有效 [105]（間接證據）。
- **年長者**：教練普遍說要每 3 週恢復一次、硬課間隔 48–72 小時 [78][200]。但唯一的對照試驗沒有發現年長和年輕的訓練有素選手在間歇後恢復有差別 [312]；Friel 自己也說他的年長者建議「沒有研究」[81]。
- **計畫性的超量**：鐵人選手超量 3 週後，23 人裡 11 人進入功能性超量，他們減量後進步反而比「只是累」的人少，感染率 70 % 對 20 % [279]。跑者每週加 10／20／30 % 連 3 週，24 人裡 12 人超量 [283]。結論是不要刻意把人練到表現下降。
- **app 現況**（`backend/engine/overview.py:1027`、`:1056–1057`、`:1236`）：
  - 近 3 個完整週都 ≥ 前一週的 0.95 倍，就把這週改成恢復週。看的是實際時數，不看日曆、不看離比賽幾週。
  - 恢復週沒有長跑；專項期的恢復週也沒有強度課。
  - 所以專項期的恢復週可能剛好落在逐週表的最大長天那一週（賽前第 4 週，90 %），那一週的長天就不見了。

### 4.4 一週內的排法

- 每週 2–3 個重點日，不連排，長課算一個重點日，前一天不排硬間歇 [166][221][151]。
- 休閒跑者每週 2 次高強度間歇和 3 次效果一樣 [373]。
- 一天兩堂閾值課不適合非菁英：每週 48–80 km 的人做了「疲勞多於體能」[154][220]（教練級）。
- **app 現況**：硬課間隔 ≥ 48 小時、每週 1–2 堂強度課，一致。休息日的位置在 SP-82／SP-83。

### 4.5 減量

- Wang 2023 統合分析：量減 41–60 % 效果最大（SMD −0.77）；減 ≤ 40 % 不夠；保留強度才有效；8–14 天效果最大，≤ 7 天和 15–21 天也有效 [105][206][207]。
- Mujika 與 Padilla：頻率減少不超過 20 %；漸進式優於一次減到底 [249]。
- Strava 的馬拉松資料（休閒跑者）：嚴格的 3 週減量比幾乎不減量快 2.6 %（中位數 5 分 32 秒）；64 % 的人減得不確實；女性受益較多 [205]。
- 減量開始後的前 2 週內出現 60–83 % 的最佳表現 [279]。這是找到唯一關於「巔峰維持多久」的數字。
- 短行程：2–3 天的攀登減量 1 週 [114]（教練級）。
- **app 現況**（`planning.py:44`、`overview.py:1058–1065`、`:365–372`）：
  - 固定 14 天；量是近 6 週平均的 50 %，最後 7 天 40 %。量的部分和統合分析一致。
  - 輕鬆跑的次數是「剩下的分鐘 ÷ 50」，所以減量時是**次數變少**，不是每堂變短。和 [249] 相反。
  - 沒有「最後一次長跑」「最後一堂強度課」排在哪天的規則。

### 4.6 賽後恢復

- 馬拉松後：CK 約 144 小時恢復正常，LDH 192 小時，CRP 到 192 小時仍偏高 [253]；跳躍功率第 5 天仍低 12 % [258]；心臟指標 2 週內回到基準 [261]。
- 賽後 48 小時起做 40 分鐘輕鬆跑沒有壞處，第 96 小時的跳躍表現反而較好（d = 0.80）[256]（對照試驗）。
- 山區超馬後 72 小時，代謝物和生物標記還沒回到基準 [331]；賽程越長，發炎指標峰值越高 [255]。
- 世界級馬拉松選手賽後 7–14 天完全休息或很輕鬆 [416]。
- 沒有找到「幾天後可以恢復正常訓練」的對照研究，也沒有反向減量的研究。
- **app 現況**（`planning.py:354`、`overview.py:1066–1068`）：7 天或 14 天（≥ 6 小時的賽事），長度和上面一致。量是「近 4 週平均的 50 %」，但近 4 週包含 2 週減量和比賽週，基準已經偏低。轉換期在 SP-73 修過同一個問題（改用賽前 4 週），恢復期沒有。

### 4.7 轉換期

- 長度：Friel 3–4 週 [3]；世界級跑者 1–2 週，有人完全休息約 4 週 [416]。app 預設 3 週，可設 0–4，一致。
- 減量訓練比全停好：VO2max −4.8 % 對 −10.1 % [412]。
- 轉換期保留一點強度有用（自行車選手的對照試驗）：每週一次在輕鬆騎裡加短衝刺，20 分鐘功率維持住 [410]；進入下一個準備期 6 週後高 7.3 %，對照組 −1.3 % [411]；8 週轉換期保留高強度間歇，下一季更好 [409]。
- 肌力每週 1 次就能維持 [407]。
- **app 現況**：轉換期只有輕鬆跑（每次 ≤ 60 分）和 2 次肌力，加速跑只在基礎期排（`overview.py:1312`）。

### 4.8 多場比賽與沒有比賽

- **A／B／C**（只有廠商文件和教練文章）：TrainerRoad 的規則是 A 賽事完整減量、賽後一週恢復；B 賽事在比賽當週減量；C 賽事不改計畫、當成一堂硬課；多場 A 賽事要相隔 ≥ 12 週 [362]。教練的說法類似 [17][158][233]。
- 沒有找到兩場重點賽事相隔 3–8 週時怎麼再達峰的研究。
- **沒有目標賽事時**：
  - 維持體能的最低劑量：每週 2 次，或量減 33–66 %，只要強度不降，可以維持到 15 週 [234][270]。降低強度就會退步 [268]。
  - 沒有找到休閒選手「維持期怎麼排」的研究。
- **app 現況**：
  - B 賽事只產生標記（`planning.py:455–467`），`week_plan` 和預測都不讀優先級 B；C 賽事只顯示。
  - 沒有 A 賽事時是無限期基礎期（`planning.py:377–378`），規則和一般基礎期相同。

### 4.9 依狀態調整（HRV、安靜心率、主觀感受）

- 統合分析：HRV 導引對表現的優勢很小、不顯著（SMD 0.20），主要好處是變差的人比較少 [41][300]。
- 可用的規則（對照試驗）：
  - 夜間 HRV 落在「4 週滾動平均 ± 0.5 個標準差」之外算異常 [299]。
  - 低於 10 天平均減一個標準差，或連續 2 天下降，就改成低強度或休息 [295]。
  - 先累積 2–4 週基準再開始用 [297][41]。
- 一個休閒跑者的試驗裡，只用壓力問卷的組別比 HRV 組和固定計畫組進步更多（5 公里 −12.8 % 對 −8.3 % 對 −6.0 %）[277]。
- **app 現況**：調整訊號只有 TSB、CTL 增幅、達成率和實際心率／功率；沒有 HRV、安靜心率、睡眠（`adapt.py` 全檔沒有相關欄位）。

## 5. 對照表：app 的周期規劃缺什麼

判定：**一致**＝不用改；**補來源**＝規則不變，可以把「推估」換成出處；**缺口**＝要改；**不做**＝證據不支持。

| # | 項目 | 證據 | app 現況 | 判定 |
|---|---|---|---|---|
| 1 | 周期骨架、從比賽往回排 | [30][96][151] | `planning.auto_phases` | 一致 |
| 2 | 專項期 8 週 | 6–12 週 [18][130][131] | `SPECIFIC_WEEKS = 8` | 補來源 |
| 3 | 基礎期分 Base 1／2／3 | 教練級，沒有試驗 [80] | 用解鎖條件控制 | 不做 |
| 4 | 區塊、反向周期化 | 無優勢 [76][6][7] | 沒有 | 不做 |
| 5 | **B、C 賽事** | B 當週減量、C 當硬課 [362][17] | 只有標記，不影響排課 | **缺口 1** |
| 6 | **減量的長度與次數** | 8–14 天最好、3 週對休閒馬拉松有效、短行程 1 週；次數減 ≤ 20 % [207][205][114][249] | 固定 14 天；次數隨分鐘減少；沒有賽週規則 | **缺口 2** |
| 7 | 減量的量與強度 | −41–60 %、保留強度 [206][105] | 50 % → 40 %，保留一堂 | 一致＋補來源 |
| 8 | **恢復週的位置與內容** | 保留次數和強度、縮短每堂 [30]；最大背靠背在賽前 4–5 週 [190] | 由歷史時數觸發；恢復週沒有長跑；專項期恢復週沒有強度課 | **缺口 3** |
| 9 | 恢復週的量 | −25–35 % [30][194] | 65 % | 補來源 |
| 10 | 3:1 可不可以改 | 沒有試驗比較 [198] | 固定 3 週 | 不急（`bompa-periodization-strength.md` §4.5 已提過做成偏好選項） |
| 11 | **賽後恢復的量** | 7–14 天輕鬆 [416][253]；48 小時起可輕鬆跑 [256] | 長度一致；量的基準含減量週與比賽週 | **缺口 4** |
| 12 | **下坡負荷的時機** | 保護效果 3–6 週有、9 週沒有 [350] | 沒有依賽前倒數排的下坡課；只有背靠背的文字建議和下坡負荷指標 | **缺口 5** |
| 13 | **高海拔行程** | 出發前 14 天內 ≥ 2 晚在 2,750 m 以上 [402] | 沒有 | **缺口 6** |
| 14 | **HRV／安靜心率／主觀狀態** | 變差的人較少 [41][299] | 沒有 | **缺口 7**（先調查資料來源） |
| 15 | **沒有 A 賽事** | 最低劑量 [234]；區塊 ≤ 6 週 [211] | 無限期基礎期 | **缺口 8**（要先設計） |
| 16 | **轉換期的強度** | 每週一次短衝刺有用 [410][411] | 只有輕鬆跑 | **缺口 9**（小） |
| 17 | 單次長跑護欄 | 超過 30 天內最長的 10 % 風險上升 [311] | 1.15 × 近 28 天最長；專項期的 90 分下限會蓋過這個上限（`specific_phase.py:289–302`） | 已有 SP-66，補證據 |
| 18 | 兩場 A 賽事太近 | 相隔 ≥ 12 週 [362] | 第二場可能消失 | 已有 SP-90，補證據 |
| 19 | 專項期強度分配前後段 | 金字塔 → 極化 [239] | 整期固定 | 已有 SP-75，補證據 |
| 20 | 肌力分期與維持 | 準備期 2 次、賽季 1 次；停掉 8 週內退步 [304][407][408] | 全年同一堂；A 賽前 14 天停（SP-86） | 已有 SP-85，補證據。停 14 天在安全範圍內（效果可維持 6–8 週 [342]） |
| 21 | 每週 10 % 規則 | 沒有證據 [169] | 程式已標「推估、沒有證據」；`overview.spec.md` 還寫「UA 10 %」 | 補來源（順便改 spec） |
| 22 | 年長者專用周期 | 教練說法，試驗不支持 [312][81] | 沒有年齡輸入 | 不做 |
| 23 | 依生理期調整 | 兩個試驗都沒有效果 [286][308] | 沒有 | 不做 |
| 24 | 一天兩堂閾值課 | 不適合非菁英 [154] | 沒有 | 不做 |
| 25 | 跑步負荷另外算 | 跑步單週增加 > 30 % 後受傷 [371] | 量的護欄只看跑步，> 20 % 擋強度課 | 一致 |
| 26 | Foster 單調度 | 個人門檻 [266] | `injury_exposure.py:85` 已有 | 一致 |

## 6. 開單清單

### 6.1 新單

| 單 | 標題 | 優先級 | 依據 | 主要程式位置 |
|---|---|---|---|---|
| SP-95 | B、C 賽事要影響當週排課 | P2 | [362][17][158]（廠商與教練級） | `planning.py:455–467`、`overview.py` `week_plan` |
| SP-96 | 減量期：長度依賽事、保留次數、加上賽週規則 | P2 | [207][205][249][114][116] | `planning.py:44`、`overview.py:1058–1065`、`:365–372` |
| SP-97 | 恢復週對齊專項期逐週表，並保留短長跑與短強度 | P2 | [30][105][190][211] | `overview.py:1027`、`:1056–1057`、`:1236`、`specific_phase.py:42` |
| SP-98 | 賽後恢復期的量改用賽前水準，結束後逐步回量 | P3 | [416][253][256][331] | `overview.py:1066–1068`、`planning.py:354` |
| SP-99 | 專項期依賽前倒數安排下坡課 | P2 | [350][351][352][357] | `specific_phase.py`、`b2b.py:607` |
| SP-100 | 高海拔賽事與百岳行程前的高度適應提醒 | P3 | [402][394][400] | 新功能；`planning.Event` 沒有最高海拔欄位 |
| SP-101 | 調查：HRV、安靜心率、主觀狀態當自動調整的訊號 | P3 | [299][41][295][277] | `adapt.py`；要先確認 COROS 同步有哪些欄位 |
| SP-102 | 設計：沒有 A 賽事時的維持模式 | P3 | [234][270][211] | `planning.py:377–378` |
| SP-103 | 轉換期每週保留一次加速跑 | P3 | [410][411][409]（自行車選手，**推估**到跑步） | `overview.py:1312` |
| SP-104 | 周期相關的「推估」常數補上這次找到的來源 | P3 | 見 §5 的「補來源」列 | `overview.py:331–334`、`planning.py:44–51`、`docs/research/estimated-constants-inventory.md`、`docs/spec/overview.spec.md:151` |

各單的做法建議：

**SP-95 B、C 賽事**

- B 賽事：比賽當週的量降低、不排長跑、強度課最晚排在賽前 3 天；賽後 3 天只排輕鬆跑。比賽本身算那一週的重點課。
- C 賽事：取代當週一堂強度課或長跑，其他不變。
- 現有的 5 天小減量與 3 天恢復（`MINI_TAPER_DAYS`、`B_RECOVERY_DAYS`）是沒有來源的數字，可以沿用，標成教練級 [362]。
- 已定案（2026-10-05）：B 賽事當週的量降到平常的 75 %（**推估**）。

**SP-96 減量期**

- 長度（已定案，2026-10-05，使用者要求依研究結果決定）：A 賽事預設 14 天不變，因為統合分析裡 8–14 天效果最大 [207]。2–3 天的百岳行程 7 天 [114]（教練級）。21 天只有休閒馬拉松的觀察性資料支持 [205]，不當預設，只做成路跑馬拉松的課表偏好選項。
- 量與強度：維持現在的 50 % → 40 %，保留強度 [206][105]。
- 次數：減量期的輕鬆跑次數維持減量前的次數（最多少 1 次），把每堂縮短 [249]。
- 賽週規則（**推估**，依 [116][190]）：最後一次長跑在賽前 ≥ 7 天且不超過 90 分；最後一堂強度課在賽前 3–5 天、量減半；賽前 2 天輕鬆跑加幾趟加速。

**SP-97 恢復週**

- 專項期改成「從比賽往回數」決定哪幾週是輕鬆週，和 `FRAC` 表的低點（賽前第 5、3 週）對齊；歷史觸發的 3:1 只在基礎期用。
- TSB < −30 的保護保留。
- 恢復週保留一次縮短的長跑（例如平常的 60–70 %）和一堂短的強度課（專項期比照基礎期的 4×1 分）[30]。
- 加一個上限：連續 6 週沒有恢復週就強制排一次 [211]。

**SP-98 賽後恢復期**

- 量的基準改用減量前 4 個完整週（和轉換期同一個函式 `planning.pre_race_mondays`）。
- 恢復期第 1 週只排 ≤ 40 分的輕鬆跑，從賽後第 3 天起 [256]。
- 恢復期結束後第 1 週不排強度課（**推估**）。
- 之後可以再做：依實際完賽時間調整長度。

**SP-99 下坡課**

- 有明顯下降的 A 賽事（越野、百岳）：專項期內每 2–3 週排一次有結構的下坡段，兩次間隔不超過 3 週 [350]。
- 最後一次排在賽前 2–3 週（**推估**：保護效果至少維持 3 週，而第一次做的損傷最大，不能太靠近比賽）。
- 第一次的量要小，之後 48 小時只排輕鬆課 [352]。
- 先確認 SP-62 的「下坡」課型做到哪裡，SP-99 只管「什麼時候排」。

**SP-100 高度適應**

- 賽事或行程最高點 ≥ 3,000 m 時，在出發前 14 天的週計畫加一則提示：這段期間在 2,750 m 以上過 2 晚 [402]。
- 近 14 天內有一筆最高海拔 ≥ 2,750 m 且過夜的活動，就顯示「已有部分適應」[400]。
- 只做提示，不改課表。要先在 `Event` 加最高海拔（可由 GPX 算）。
- 注意：研究多針對 4,000 m 以上，套到 3,000–3,950 m 是**推估**。

**SP-101 調查 HRV**

- 先查：COROS 同步進來的資料有沒有夜間 HRV、安靜心率、睡眠。
- 有的話，設計規則：4 週基準、低於平均 − 0.5 SD 或連 2 天下降時，把當天的強度課降成輕鬆跑 [299][295]。
- 沒有的話，評估只用一題主觀狀態（1–7 分）[277]。
- 產出是調查文件，不直接實作。

**SP-102 維持模式**

- 要回答的問題：沒有 A 賽事超過 N 週時，計畫該繼續加量、還是進入「維持＋輪替重點」。
- 可用的材料：最低劑量（每週 2 次、保留強度）[234]；每個區塊 ≤ 6 週 [211]；定期測試。
- 產出是設計提案，要你決定方向。

**SP-103 轉換期加速跑**

- 轉換期每週第一堂輕鬆跑加 6–8 趟短加速（沿用基礎期的 `8×10 秒` 坡道版或 `6×20 秒` 平路版）。
- 證據來自自行車選手，套到跑步是**推估**；量很小，風險低。

**SP-104 補來源**

| 常數 | 現在的標記 | 可以加的出處 |
|---|---|---|
| 恢復週 65 % | 沒有來源 | 挪威教練 −25–35 % [30]；跑者 −20–35 % [194] |
| 3:1 | Friel／UA | 再加 [199][114]；註明沒有試驗比較過 [198] |
| 減量 50 % → 40 % | Bosquet 2007 | 加 Wang 2023：−41–60 %、≤ 40 % 不夠、保留強度 [206][105] |
| 專項期 8 週 | Koop、UA（文字） | Friel Build 8–9 週 [18]、Canova 6–8 週 [130]、最後 8–10 週 [131] |
| 背靠背最後一次 ≥ 21 天前 | 推估 | 最大的在賽前 4–5 週、最後 3 週不做 [190] |
| 轉換期減量不全停 | 推估（50 %） | 全停 −10.1 % 對減量 −4.8 % [412] |
| B 賽事小減量 | 沒有來源 | TrainerRoad：比賽當週減量 [362] |
| 每週 +10 % 上限 | 推估 | 系統性回顧：沒有證據 [169]；改 `overview.spec.md:151` 的「UA 10 %」 |
| 賽季肌力每週 1 次 | UA | 13 週每週 1 次可維持 [407] |

### 6.2 既有的單，補上這次的證據

| 單 | 補什麼 |
|---|---|
| SP-66 單次長跑護欄 | RUNSAFE 世代研究：單次超過前 30 天最長的 10 % 以上，風險上升 [311][121][160]。app 的 1.15 倍比這個寬。另外 `specific_phase.py:289–302` 的 90 分下限會蓋過上限，程式註解卻說上限仍然有效 |
| SP-75 專項期內部進階 | 前 8 週金字塔、後 8 週極化的試驗 [239]；進展時改量、每趟長度或休息，不重複同一堂 [143]；專項訓練佔比 48 → 87 → 92 % [28] |
| SP-85 肌力分期 | 統合分析：重負荷（≥ 80 % 1RM）加增強式效果最大 [303][305]；6–20 週改善跑步經濟性 2–8 % [304]；同一天先肌力或隔 ≥ 3 小時 [319][387]。這回答了 `bompa-periodization-strength.md` §7 的兩個待研究項目 |
| SP-90 兩場 A 賽事太近 | 廠商規則：A 賽事相隔 ≥ 12 週 [362]；減量後 2 週內出現 60–83 % 的最佳表現 [279]。建議除了修 bug，相隔 < 12 週時顯示提示 |

## 7. 限制

- 越野、超馬、登山的周期研究幾乎都是教練經驗和個案。§3.3、SP-96、SP-98、SP-99 裡的時間點多是**推估**。
- 菁英選手的資料只能當結構參考。他們每年練 750–1000 小時，恢復能力和休閒選手不同。
- 一季多場比賽和其他 app 的規則，只有 TrainerRoad 一家的文件。
- 中文來源完全沒有查到。
- 部分引文是工具摘要後的文字，沒有逐字核對；統計數字請以原文為準。
- app 現況以 2026-10-05 的 HEAD `c320074` 為準。這天 repo 有多次合併，行號之後可能位移。
- 沒有跑任何回測。缺口 3（恢復週刪掉長天）是從程式邏輯推出來的，沒有在真實資料上重現。

## 參考文獻

只列本文引用到的來源；編號沿用證據資料夾的 `display_numbers.json`，所以不連續。

[3] The Transition Period. https://www.trainingpeaks.com/blog/the-transition-period-by-joe-friel

[4] Block periodization of endurance training - a systematic review and meta-analysis (Mølmen, Øfsteng, Rønnestad). https://www.dovepress.com/block-periodization-of-endurance-training-a-systematic-review-and-meta-peer-reviewed-fulltext-article-OAJSM

[5] Running Periodization Part 3: Block and Undulating Periodization. https://trackandfieldnews.com/track-coach/running-periodization-part-3-block-and-undulating-periodization

[6] Effectiveness of Reverse vs. Traditional Linear Training Periodization in Triathlon. https://pmc.ncbi.nlm.nih.gov/articles/PMC6696421

[7] Running Periodization Part 2: Reverse Linear Periodization. https://trackandfieldnews.com/track-coach/running-periodization-part-2-reverse-linear-periodization

[10] Reverse periodization in ultratrail: The road to the 2023 World Mountain and Trail Running Championships of an elite female ultrarunner. https://ciss-journal.org/article/view/11560

[12] Day-To-Day Endurance Training Periodization of World-Class Cross-Country Skiers (Walther et al., Eur J Sport Sci 2025). https://pmc.ncbi.nlm.nih.gov/articles/PMC12094961

[15] Another Block Periodization Study - Joe Friel. https://joefrieltraining.com/another-block-periodization-study

[17] Joe Friel's Bible for Periodisation. https://www.coachray.nz/2021/10/18/joe-friels-bible-for-periodisation

[18] K.I.S.S. Periodization - Joe Friel. https://joefrieltraining.com/kiss-periodization

[20] A Systematic Review of Meta-Analyses Comparing Periodized and Non-periodized Exercise Programs: Why We Should Go Back to Original Research (Afonso et al.). https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2019.01023/full

[23] Cycling Periodisation Guide: Friel, Lorang, Johnson (2026). https://roadmancycling.com/blog/cycling-periodisation-friel-lorang-johnson

[25] Recent advances in training intensity distribution theory for cyclic endurance sports: theoretical foundations, model comparisons, and periodization characteristics. https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2025.1657892/full

[27] The Training Characteristics of the World's Most Successful Female Cross-Country Skier (Solli, Tonnessen, Sandbakk). https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2017.01069/full

[28] The Road to Gold: Training and Peaking Characteristics in the Year Prior to a Gold Medal Endurance Performance (Tonnessen et al.). https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0101796

[30] The Norwegian Approach to Periodization and Training Management in Endurance Sports. https://www.athletemonitoring.com/2026/01/08/norwegian-approach-to-periodization-and-training-in-endurance-sports

[35] Block periodization of endurance training - a systematic review and meta-analysis (Mølmen et al., OAJSM 2019). https://europepmc.org/article/MED/31802956

[36] No Additional Effects of Block- Compared to Even-Periodization of Low-Intensity Training in Junior Cross-Country Skiers (IJSPP 2026). https://journals.humankinetics.com/view/journals/ijspp/aop/article-10.1123-ijspp.2025-0552/article-10.1123-ijspp.2025-0552.xml

[39] Periodization Theory: Confronting an Inconvenient Truth (Kiely, Sports Med). https://pmc.ncbi.nlm.nih.gov/articles/PMC5856877

[40] Coaches Perceptions of Common Planning Concepts Within Training Theory: An International Survey. https://pmc.ncbi.nlm.nih.gov/articles/PMC10663426

[41] Heart Rate Variability-Guided Training for Enhancing Cardiac-Vagal Modulation, Aerobic Fitness, and Endurance Performance: A Methodological Systematic Review with Meta-Analysis. https://pmc.ncbi.nlm.nih.gov/articles/PMC8507742

[53] Training Methods and Intensity Distribution of Young World Class Rowers (Guellich, Seiler, Emrich 2009 abstract repost). http://arcrsa.blogspot.com/2010/08/training-methods-and-intensity.html

[69] Is Periodization Overrated?. https://www.8020endurance.com/is-periodization-overrated

[76] No Differences Between 12 Weeks of Block- vs. Traditional-Periodized Training in Performance Adaptations in Trained Cyclists (Almquist et al. 2022, Front Physiol). https://pmc.ncbi.nlm.nih.gov/articles/PMC8921659

[78] Training for Masters Runners, Part 2: Block Periodization - TrainingPeaks. https://www.trainingpeaks.com/blog/training-for-masters-runners-part-2-block-periodization

[80] Aging: Customizing the Base Period - Joe Friel. https://joefrieltraining.com/aging-customizing-the-base-period

[81] Aging: Customizing the Peak Period - Joe Friel. https://joefrieltraining.com/aging-customizing-the-peak-period

[85] How Professional Cyclists Train for the Giro d Italia & Tour de France - W/KG. https://www.wattkg.com/how-professional-cyclists-train

[93] Cycling Base Training: Why and How to Build Your Aerobic Base. https://www.trainerroad.com/blog/base-training-for-cyclists-why-and-how-to-build-your-aerobic-base

[95] Day-To-Day Endurance Training Periodization of World-Class Cross-Country Skiers (Eur J Sport Sci 2025). https://europepmc.org/article/MED/40400090

[96] Best-Practice Training Characteristics Within Olympic Endurance Sports as Described by Norwegian World-Class Coaches (Sports Med Open 2025). https://europepmc.org/article/MED/40278987

[104] Predictors of Athlete’s Performance in Ultra-Endurance Mountain Races. https://pmc.ncbi.nlm.nih.gov/articles/PMC7908619

[105] Effects of tapering on performance in endurance athletes: A systematic review and meta-analysis (Wang et al.). https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0282838

[114] Training for Mountaineering. https://uphillathlete.com/mountaineering/training-for-mountaineering

[115] Training for Trekking and Hiking. https://uphillathlete.com/trekking/training-for-trekking-and-hiking

[116] The Road to Gold: Training and Peaking Characteristics in the Year Prior to a Gold Medal Endurance Performance (Tønnessen et al. 2014). https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0101796

[121] The 10 per cent mileage rule isn't what you think, study warns. https://runningmagazine.ca/sections/training/the-10-per-cent-mileage-rule-isnt-what-you-think-study-warns

[122] Base Training Running: How Long It Takes to Build. https://endogusto.com/blog/base-training-running

[127] Volume or Intensity? A Systematic Review and Meta-Regression of How Exercise Influences Mitochondrial Content, Capillarization, and Aerobic Capacity. https://www.gethealthspan.com/research/article/exercise-volume-intensity-systematic-review

[129] Mitochondrial adaptations to endurance training - The Centre for Integrative Sports Nutrition. https://intsportsnutrition.com/articles/mitochondria-adaptations-to-endurance-training

[130] Review and summary of Marathon Training - A Scientific Approach by Renato Canova. https://runningwritings.com/2023/06/canova-marathon-book.html

[131] How to Plan Your 2026 Running Season With Intention. https://vert.run/how-to-plan-your-trail-running-season

[143] Renato Canova's "Special Period" Example Training Sessions - Sweat Elite. https://articles.sweatelite.co/renato-canovas-special-period-example-training-sessions

[146] How to Apply the Principles of Training Specificity When Running. https://lauranorrisrunning.com/principles-training-specificity-running

[151] The Training Characteristics of World-Class Distance Runners: An Integration of Scientific Literature and Results-Proven Practice. https://pmc.ncbi.nlm.nih.gov/articles/PMC8975965

[154] The Norwegian Training Method Simplified: How to Use It at Any Level. https://www.trainingpeaks.com/blog/the-norwegian-training-method-simplified-how-to-use-it-at-any-level

[155] Training periodization for a world-class 400 meters individual medley swimmer (Gonzalez-Rave et al.). https://pmc.ncbi.nlm.nih.gov/articles/PMC9536385

[158] Sports periodization - Wikipedia. https://en.wikipedia.org/wiki/Sports_periodization

[160] Everything we thought about running injury development was wrong, Danish study shows. https://www.eurekalert.org/news-releases/1090184

[162] How Should You Taper Before a Race or Big Objective?. https://uphillathlete.com/trail-running/tapering-for-race-event-what-to-do

[164] Training for Mountaineering: Climbing Specific Period (podcast). https://uphillathlete.com/podcast/training-for-mountaineering-climbing-specific-period

[166] Training Session Models in Endurance Sports: A Norwegian Perspective on Best Practice Recommendations. https://pmc.ncbi.nlm.nih.gov/articles/PMC11560996

[168] Why is my Zone 2 so slow?. https://uphillathlete.com/aerobic-training/aerobic-deficiency-syndrome

[169] Is There Evidence for an Association Between Changes in Training Load and Running-Related Injuries? A Systematic Review. https://pmc.ncbi.nlm.nih.gov/articles/PMC6253751

[175] How CTS Coaches Prepare Athletes for UTMB. https://trainright.com/how-cts-coaches-prepare-athletes-for-utmb

[177] 6 Week Training Plan for Alpine Peak Ascents. https://summit-guides.com/en-eu/blog/6-week-training-plan-for-alpine-peak-ascents

[190] 100 Mile Training Plan: Complete 100-Miler Guide - Vert.run. https://vert.run/100-miles-training-guide

[194] How runners should do cutback weeks in training. https://www.runspirited.com/single-post/how-runners-should-do-cutback-weeks-in-training

[198] Cycling Rest Week Guide - Structure, Volume, Timing (2026). https://roadmancycling.com/blog/cycling-rest-week-guide

[199] Mesocycle Training for Cyclists - The 4-Week Block Explained. https://roadmancycling.com/blog/mesocycle-training-explained-cyclists

[200] The 5+2 Framework: How Masters Athletes Should Structure Their Week. https://uphillathlete.com/aerobic-training/the-5-2-framework-how-masters-athletes-should-structure-their-week

[205] Longer Disciplined Tapers Improve Marathon Performance for Recreational Runners. https://www.frontiersin.org/articles/10.3389/fspor.2021.735220/full

[206] Effects of Tapering on Performance in Endurance Athletes: A Systematic Review and Meta-Analysis. https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0282838

[207] Effects of tapering on performance in endurance athletes: A systematic review and meta-analysis (Wang et al., PLOS ONE 2023). https://pmc.ncbi.nlm.nih.gov/articles/PMC10171681

[211] Debunking 3 Weeks On, 1 Week Off Training Cycles (Jason Koop, CTS). https://trainright.com/3-weeks-on-1-week-off-training-cycles

[220] Double Threshold Training: How to Get the Benefits in One Session. https://runnersconnect.net/double-threshold-training-everyday-runners

[221] Micro Cycle Structure: Weekly Training Planning. https://pushinglimits.club/en/knowledge/micro-cycle-structure

[233] Multi-Sport and Seasonal Planning (Uphill Athlete Podcast). https://uphillathlete.com/podcast/multi-sport-and-seasonal-planning

[234] Maintaining Physical Performance: The Minimal Dose of Exercise Needed to Preserve Endurance and Strength Over Time (Spiering, Mujika, Sharp, Foulis; J Strength Cond Res 35(5):1449-1458). doi:10.1519/JSC.0000000000003964

[236] Polarized training intensity distribution in distance running: A case study of the 2021 Olympic long-distance runner. https://sssj.aearedo.es/index.php/sssj/article/view/polarized-training-intensity-running-olympic-long-distance

[239] Effects of 16 weeks of pyramidal and polarized training intensity distributions in well-trained endurance runners. https://pmc.ncbi.nlm.nih.gov/articles/PMC9299127

[242] Effects of tapering on performance: a meta-analysis (Bosquet et al., Med Sci Sports Exerc 2007). https://europepmc.org/article/MED/17762369

[246] Scientific bases for precompetition tapering strategies (Mujika & Padilla, MSSE 2003). doi:10.1249/01.mss.0000074448.73931.11

[248] New horizons for the methodology and physiology of training periodization (Issurin, Sports Med). https://pubmed.ncbi.nlm.nih.gov/20199119

[249] Scientific bases for precompetition tapering strategies (Mujika & Padilla, MSSE). https://pubmed.ncbi.nlm.nih.gov/12840640

[253] Recovery of Inflammation, Cardiac, and Muscle Damage Biomarkers After Running a Marathon (J Strength Cond Res 2021). doi:10.1519/JSC.0000000000003167

[255] Inflammatory Response to Ultramarathon Running: A Review of IL-6, CRP, and TNF-alpha (IJMS 2025). doi:10.3390/ijms26136317

[256] The week after running a marathon: Effects of running vs elliptical training vs resting on neuromuscular performance and muscle damage recovery (EJSS 2021). doi:10.1080/17461391.2020.1857441

[258] Muscle mechanical characteristics in fatigue and recovery from a marathon race in highly trained runners (EJAP 2007). doi:10.1007/s00421-007-0504-x

[261] Exercise-induced ventricular changes in recreational half-marathon runners compared with marathon/ultramarathon runners. doi:10.1016/j.ijcha.2026.101886

[266] Monitoring training in athletes with reference to overtraining syndrome (Foster, MSSE 1998). https://pubmed.ncbi.nlm.nih.gov/9662690

[268] Reduced training intensities and loss of aerobic power, endurance, and cardiac growth (Hickson et al., J Appl Physiol 1985). https://pubmed.ncbi.nlm.nih.gov/3156841

[270] Reduced training frequencies and maintenance of increased aerobic power (Hickson & Rosenkoetter, MSSE 1981). https://pubmed.ncbi.nlm.nih.gov/7219129

[277] Individually guided training prescription by heart rate variability and self-reported measure of stress tolerance in recreational runners (Figueiredo et al.). doi:10.1080/02640414.2023.2191082

[278] The Effects of Menstrual Cycle Phase on Exercise Performance in Eumenorrheic Women: A Systematic Review and Meta-Analysis. https://pmc.ncbi.nlm.nih.gov/articles/PMC7497427

[279] Functional overreaching: the key to peak performance during the taper? (Aubry et al., MSSE 2014). https://pubmed.ncbi.nlm.nih.gov/25134000

[283] Muscle fiber typology is associated with the incidence of overreaching in response to overload training (Bellinger et al., J Appl Physiol 2020). https://pubmed.ncbi.nlm.nih.gov/32816636

[286] Effects of a training intervention tailored to the menstrual cycle on endurance performance and hemodynamics. https://europepmc.org/article/MED/37800402

[295] Endurance training guided individually by daily heart rate variability measurements (Kiviniemi et al., Eur J Appl Physiol 2007). https://pubmed.ncbi.nlm.nih.gov/17849143

[297] Training Prescription Guided by Heart-Rate Variability in Cycling (Javaloyes et al., IJSPP 2019). https://pubmed.ncbi.nlm.nih.gov/29809080

[299] Individualized Endurance Training Based on Recovery and Training Status in Recreational Runners (Nuuttila et al., MSSE 2022). https://pmc.ncbi.nlm.nih.gov/articles/PMC9473708

[300] Monitoring and adapting endurance training on the basis of heart rate variability monitored by wearable technologies: A systematic review with meta-analysis (Duking et al., J Sci Med Sport 2021). doi:10.1016/j.jsams.2021.04.012

[303] The Effect of Strength Training Methods on Middle-Distance and Long-Distance Runners Athletic Performance: A Systematic Review with Meta-analysis (Llanos-Lagos et al., Sports Med 2024). https://pmc.ncbi.nlm.nih.gov/articles/PMC11258194

[304] Effects of Strength Training on the Physiological Determinants of Middle- and Long-Distance Running Performance: A Systematic Review (Blagrove et al., Sports Med 2018). https://pmc.ncbi.nlm.nih.gov/articles/PMC5889786

[305] Effect of Strength Training Programs in Middle- and Long-Distance Runners Economy at Different Running Speeds: A Systematic Review with Meta-analysis (Llanos-Lagos et al., Sports Med 2024). https://pmc.ncbi.nlm.nih.gov/articles/PMC11052887

[308] Polarized running training adapted to versus contrary to the menstrual cycle phases has similar effects on endurance performance and cardiovascular parameters. https://pmc.ncbi.nlm.nih.gov/articles/PMC11519221

[311] How much running is too much? Identifying high-risk running sessions in a 5200-person cohort study. https://europepmc.org/article/MED/40623829

[312] Performance and Recovery of Well-Trained Younger and Older Athletes during Different HIIT Protocols. https://europepmc.org/article/MED/35050974

[319] The Role of Intra-Session Exercise Sequence in the Interference Effect: A Systematic Review with Meta-Analysis (Eddens et al., Sports Med 2018). https://pmc.ncbi.nlm.nih.gov/articles/PMC5752732

[331] Longitudinal NMR-based Metabolomics Analysis of Male Mountain Ultramarathon Runners: New Perspectives for Athletes Monitoring and Injury Prevention. doi:10.1186/s40798-025-00879-w

[342] Heavy strength training effects on physiological determinants of endurance cyclist performance: a systematic review with meta-analysis (Llanos-Lagos et al., Eur J Appl Physiol). https://pmc.ncbi.nlm.nih.gov/articles/PMC12881108

[343] Strength training among professional UCI road cyclists: Practices, challenges, and rationales (Vikestad et al., PLoS One). https://pmc.ncbi.nlm.nih.gov/articles/PMC12244580

[350] Delayed onset muscle soreness following repeated bouts of downhill running. https://pubmed.ncbi.nlm.nih.gov/4055561

[351] Repeated Bout Effect of Downhill Running on Physiological Markers of Effort and Post Exercise Perception of Soreness in Trained Female Distance Runners. https://pubmed.ncbi.nlm.nih.gov/38921863

[352] Neuromuscular, biomechanical, and energetic adjustments following repeated bouts of downhill running. https://pubmed.ncbi.nlm.nih.gov/34098176

[353] The repeated bout effect influences lower-extremity biomechanics during a 30-min downhill run. https://pubmed.ncbi.nlm.nih.gov/35225166

[357] Downhill Running-Induced Muscle Damage in Trail Runners: An Exploratory Study Regarding Training Background and Running Gait. https://pubmed.ncbi.nlm.nih.gov/41590954

[362] Plan Builder Overview - TrainerRoad Support. https://support.trainerroad.com/hc/en-us/articles/360059419452-Plan-Builder-Overview

[363] Cross-training between running and cycling: effects on VO2max and running performance - a systematic review and meta-analysis (Menges et al., Front Sports Act Living 2026). https://pubmed.ncbi.nlm.nih.gov/42267259

[369] Best-Practice Training Characteristics Within Olympic Endurance Sports as Described by Norwegian World-Class Coaches (Sports Med Open 2025). https://pubmed.ncbi.nlm.nih.gov/40278987

[371] How Do Age-Group Triathlon Coaches Manage Training Load? A Pilot Study (Sports 2024). https://pubmed.ncbi.nlm.nih.gov/39330738

[373] Impact of weekly frequency of high-intensity interval training on cardiorespiratory, metabolic, and performance measures in recreational runners - An exploratory study. https://pmc.ncbi.nlm.nih.gov/articles/PMC12451023

[387] The effects, mechanisms, and influencing factors of concurrent strength and endurance training with different sequences: a semi-systematic review (Feng et al., Front Sports Act Living). https://www.ebi.ac.uk/europepmc/webservices/rest/PMC12885173/fullTextXML

[394] Time requirements of pre-acclimatization at simulated altitude to prevent acute mountain sickness (J Travel Med). https://europepmc.org/article/MED/41609128

[400] Is normobaric hypoxia an effective treatment for sustaining previously acquired altitude acclimatization?. https://europepmc.org/article/MED/28705998

[402] CDC Yellow Book: High-Altitude Travel and Altitude Illness. https://www.cdc.gov/yellow-book/hcp/environmental-hazards-risks/high-altitude-travel-and-altitude-illness.html

[407] In-season strength maintenance training increases well-trained cyclists performance (Ronnestad, Hansen, Raastad, Eur J Appl Physiol 2010). doi:10.1007/s00421-010-1622-4

[408] Impairment of Performance Variables After In-Season Strength-Training Cessation in Elite Cyclists (Ronnestad et al., IJSPP 2016). doi:10.1123/ijspp.2015-0372

[409] HIT maintains performance during the transition period and improves next season performance in well-trained cyclists (Ronnestad, Askestad, Hansen, Eur J Appl Physiol 2014). doi:10.1007/s00421-014-2919-5

[410] Effects of Including Sprints in One Weekly Low-Intensity Training Session During the Transition Period of Elite Cyclists (Almquist et al., Front Physiol 2020). doi:10.3389/fphys.2020.01000

[411] The Inclusion of Sprints in Low-Intensity Sessions During the Transition Period of Elite Cyclists Improves Endurance Performance 6 Weeks Into the Subsequent Preparatory Period (Taylor et al., IJSPP 2021). doi:10.1123/ijspp.2020-0594

[412] Post-season detraining effects on physiological and performance parameters in top-level kayakers: comparison of two recovery strategies (Garcia-Pallares et al., J Sports Sci Med 2009). https://europepmc.org/article/MED/24149605

[416] The Training Characteristics of World-Class Distance Runners: An Integration of Scientific Literature and Results-Proven Practice (Haugen et al., Sports Med Open 2022). doi:10.1186/s40798-022-00438-7
