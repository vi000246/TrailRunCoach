# SP-518 共同查法（每個子 agent 都照這份）

背景：TrailRunCoach（越野跑／路跑教練 app，給所有跑者用，不是只給擁有者）裡有很多「推估」常數。
使用者要求：「照教練共識查出更正確的設定，找得到就不要個人化了。」
這是研究單：**只查，不改 repo、不 commit、不 push、不安裝套件、不讀 .env 或密鑰**。
repo（唯讀）：/Users/logan/Projects/TrailRunCoach-wt-integrate-1012a

## 查法（跟 SP-512 第二輪一樣）

1. 先讀 repo 既有研究文件裡已經查過的（`docs/research/` 下，主要是 `periodization-cross-sport.md`、
   `back-to-back-and-long-day.md`、`ctl-ramp-calibration.md`、`specific-phase-progression.md`、
   `interval-adaptation.md`、`interval-prescription.md`、`runner-progression.md`、`rest-day-placement.md`、
   `coach-schools-zones-periodization.md`、`zones-and-thresholds.md`、`unsourced-rules.md`、
   `estimated-constants-inventory.md`）——已驗證過的來源可以直接沿用（標「沿用 <文件>§」），不用重查；
   但要盡量補到**更多**來源。
2. 用 WebSearch／WebFetch（先用 ToolSearch "select:WebSearch,WebFetch" 載入）找盡量多的教練與教科書來源：
   - 跑步與越野跑：Jack Daniels（Daniels' Running Formula）、Pete Pfitzinger（Advanced Marathoning、Faster Road Racing）、
     Brad Hudson（Run Faster）、Steve Magness（Science of Running）、Jason Koop（Training Essentials for Ultrarunning）、
     Matt Fitzgerald（80/20 Running）、Hal Higdon、Greg McMillan、David Roche（The Happy Runner、SWAP）、
     Jack Daniels/Hanson（Hansons Marathon Method）、Ben Rosario 等；
   - 耐力體系：Joe Friel（Training Bible、joefrieltraining.com）、Uphill Athlete／Scott Johnston／Steve House
     （Training for the Uphill Athlete、uphillathlete.com）、Carmichael／CTS（trainright.com）；
   - Runner's World、TrainingPeaks 的教練文章、iRunFar、Strava、Runna、TrainerRoad 等平台文章；
   - **有研究（統合分析、系統性回顧、實驗）就優先列研究**（PubMed／Europe PMC：Europe PMC REST API
     `https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=...&format=json&resultType=core` 可讀摘要；PubMed 網頁常抓不到）。
3. 每個來源記：**作者、出處（書名＋頁碼／章節，或網址）、原文數字與它的基準（例如「占週里程」vs「占週時間」、
   「前一週」vs「最重那週」）、驗證狀態**：
   - 「已驗證」＝這次 WebFetch 讀到原文那句話（引出原句，英文原文短引即可）；
   - 「未驗證（摘要）」＝只看到搜尋摘要；
   - 「二手」＝別人轉述（說明是誰轉述）。
   不要編造頁碼或引文；讀不到就照實標。
4. 換算成程式的基準（例：教練說「長跑 ≤ 週里程 30 %」，程式用「週分鐘」→ 說明換算方式並標「推估」）。
5. 統計：每個常數列出所有來源的數字 → **眾數／中位數／範圍**，以及「驗證過幾個」。
6. 結論：建議「改成多少／維持／查不到」，以及改了對課表的影響方向（量多或少、哪種週）。
   量測誤差類（例如間歇判定容差）教練多半沒數字——查不到就照實說「查不到」，不要硬湊。

## 產出

- 把完整結果寫到 `/private/tmp/claude-501/-Users-logan-Projects-TrailRunCoach/a3e58917-0a28-42f8-91b0-021b2d8e3ed3/scratchpad/sp518/<你的組名>.md`（繁體中文）。
  格式：每個常數一節：程式位置與現值 → 來源表（作者｜出處｜原文數字與基準｜驗證）→ 統計 → 換算 → 建議與影響。
- 最後的回報訊息：給這份檔案的路徑＋一張精簡總表（常數｜現值｜共識（眾數／範圍）｜來源數（驗證幾個）｜建議）。
- 不要寫到 scratchpad 以外的地方。
