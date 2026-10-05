# 讀不到的文獻：要想辦法取得的論文與書（SP-113）

> 掃描日期：2026-10-05。基準 commit `12678bf`。只整理清單，沒有改程式。
> 對應的單：SP-113。取得後的核對工作也記在那張單上。

## 摘要

1. `docs/research/` 裡有 **83 個**來源因為付費牆、403、驗證碼、只讀到摘要或原書沒核對，內容沒讀到。依「會不會改到正在做的單或程式常數」分三級：P1 **9 個**、P2 **31 個**、P3 **43 個**。
2. P1 幾乎都是**書**：Allen & Coggan、Daniels、Pfitzinger、Koop、山本正嘉（3 本）。買電子書最快。論文只有 Bellenger 2016 和 JSAMS 2025 的跑步 τ。
3. 最急的三件：
   - Allen & Coggan 的「最後一趟掉幅」表：決定 `quality_gate.LAST_FADE` 要不要依每趟長度改（部落格和 CTS 轉述的數字不一樣）。
   - Daniels 表 9.1：`reentry.py` 的 FVDOT-2 42 天是 0.994 還是 0.944。
   - Pfitzinger 的 B 賽前後天數：SP-95 的 5 天小減量、賽後 3／5 天恢復，目前只有二手轉述。
4. 另外有十幾個**網站**回 403（COROS 支援、TrainingPeaks help、Strava 工程部落格等），不是論文，換瀏覽器開可能就讀得到，列在 §17。

## 1. 怎麼掃的、怎麼用

**掃描方法**

- 用 grep 掃 `docs/research/*.md` 全部 49 份文件，關鍵字：「付費牆」「paywall」「403」「402」「驗證碼」「captcha」「Get Access」「要登入」「會員」「非開放」「原書未核對」「原書未讀」「原文未讀」「未讀」「讀不到」「只讀摘要」「只讀到摘要」「只有摘要」「搜尋摘要」「二手」「cookie」「Cloudflare」。
- 每個命中都回原文件看上下文，確認是「讀不到」而不是「讀到了但結果是否定的」。
- 以 SP-113 單上的表為起點，逐項對回文件；單上漏掉的補進來（標「新增」）。

**收錄範圍**

- 收：明寫付費牆、403、驗證碼、未讀、原書未核對的；以及**只讀到摘要、而且有一個具體數字或規則因此沒拿到**的。
- 不收：只讀摘要但結論已經夠用、沒有缺數字的（例如 `detraining.md` §8 第 8 點說「所有同儕審查數字都取自摘要」，那些不逐篇列）。
- 不收：已經在另一份文件讀到全文的（例如長野縣《登山 Safety Book》，`mountaineering-physiology-scholars.md` 已讀到 PDF 全文）。

**怎麼用**

1. 照優先級取得（取得方式見 §2 的代號）。
2. 檔案放進專案的 `files/`，檔名註記對應的研究文件，例如 `files/allen-coggan-3e_interval-adaptation.pdf`。
3. 請 agent 核對：讀原文 → 更新研究文件（把「原書未核對」「付費牆，未讀」改成「已驗證」或修正數字）→ 如果數字變了，更新受影響的單和程式註解。
4. 確定取不到的，在研究文件該處標「確定取不到（日期、試過的管道）」，並在 SP-113 勾掉。

## 2. 優先級與取得方式

**優先級**

| 級 | 意思 |
|---|---|
| **P1** | 會改到正在做的單的門檻，或程式裡的常數 |
| **P2** | 會補強依據，但規則大致已定；或只影響提示文字 |
| **P3** | 歷史出處、已有 2 個以上二手來源核對、或沒有拿來用 |

**各級數量**

| 級 | 數量 | 其中書 | 其中論文 | 其中雜誌／網路文章 |
|---|---|---|---|---|
| P1 | 9 | 7 | 2 | 0 |
| P2 | 31 | 1 | 28 | 2 |
| P3 | 43 | 5 | 29 | 9 |
| 合計 | **83** | 13 | 59 | 11 |

（「論文」含期刊論文、學會發表、技術報告；RQ 的 6 篇文章算 1 組。）

**取得方式代號**（下面每一項只寫代號和特別要注意的地方）

| 代號 | 做法 |
|---|---|
| **A 開放版** | 先查 Unpaywall（unpaywall.org，貼 DOI）、Europe PMC、Google Scholar 的「所有版本」。很多付費期刊有作者自存的 accepted manuscript |
| **B 作者** | ResearchGate 按「Request full-text」，或寫信給通訊作者。通常幾天內會回 |
| **C 圖書館** | 國家圖書館（辦閱覽證後可用遠距電子資料庫）；台大等大學圖書館的館際合作（NDDS 文獻傳遞，按篇付費） |
| **D 買書** | 英文書：Kindle、Google Play 圖書、Kobo。中文書：Readmoo、博客來電子書（博客來網頁我們這邊 403，但瀏覽器開得了）。日文書：Kindle 日本、honto、Amazon.co.jp |
| **E 網頁** | 我們的抓取工具被擋，不代表瀏覽器打不開。換瀏覽器、換網路環境，或試 Wayback Machine |
| **F 日本期刊** | 先查 J-STAGE、CiNii Research 有沒有公開；沒有就用国立国会図書館（NDL）的遠隔複写 |

---

## 3. `interval-adaptation.md`：間歇判斷

依賴的單與程式：SP-110 間歇疲勞保險：心率恢復變快但功率沒到時不進階；`quality_gate.py` 的 `LAST_FADE`（`:732`，現在一律 0.05）與 `interval_outcome`；`interval_eval.py` 的 W′bal 顯示。

### 3.1 Allen H, Coggan AR, McGregor SL.《Training and Racing with a Power Meter》第 3 版. VeloPress, 2019 —— **P1**

- 連結：書，文件未附 ISBN。
- 缺什麼：原書未核對。只讀到 Allen 2015 部落格〈Power Up: Increasing Repeatability and Peak Power〉全文，和 CTS（Rutberg 2025）的轉述（§3.6.3，L389–390；§6 L790）。
- 依賴的說法：最後一趟容許掉幅依每趟長度定、以第 3 趟為基準：≤ 2 分 10 %、3 分 8 %、5 分 5 %、10 分 4 %、20 分以上 3 %（§4.3，L574–576，建議改 `LAST_FADE`）。CTS 引同一本書寫「掉超過 15 % 就停」，和部落格的表不一致。要看書裡到底是哪一個、表在哪一章。
- 取得：D（英文電子書）。

### 3.2 Bellenger CR, Fuller JT, Thomson RL, et al. Monitoring athletic training status through autonomic heart rate regulation: a systematic review and meta-analysis. *Sports Med* 2016;46(10):1461–1486 —— **P1**

- 連結：doi:10.1007/s40279-016-0484-2
- 缺什麼：付費牆，Europe PMC 標非開放，只讀到摘要（§3.1 補查 L201；參考文獻 L705）。
- 依賴的說法：適應時 HRR 上升 SMD 0.63、過量訓練時也上升 SMD 0.46，「also occur in response to overreaching」（L212）。這是 SP-110「疲勞保險」的兩個主要依據之一（另一個是 Aubry 2015，已讀全文）。要看分組分析：哪些條件下兩者分得開、HR acceleration 的效果量。
- 取得：A → B（作者在 University of South Australia）→ C。

### 3.3 〈Development of a running-specific recovery time constant for validly assessing D′ balance during exhaustive intermittent running〉. *J Sci Med Sport* 2025 —— **P1**

- 連結：文件未附 DOI，作者也沒寫；從上下文看是 Bellenger CR、Bartram JC 那一組人（Bellenger 2025 IJSPP 的後續）。
- 缺什麼：付費牆，403（§3.6.2 表 L372；L788）。
- 依賴的說法：跑步專用的 W′／D′ 恢復 τ。現在 `interval_eval.py` 用 Vassallo 2020 的跑步 τ，Bellenger 2025 說 D′bal 判不出力竭是因為 τ 不準。這篇決定 W′bal 能不能從「只顯示」變成間歇判斷的依據。
- 取得：A → B。拿到後先查 DOI 補進參考文獻。

### 3.4 徐國峰《跑者都該懂的跑步關鍵數據》 —— **P2**

- 連結：書，文件未附出版資訊（L69、L669）。
- 缺什麼：原書未核對，只找到電子書販售頁。
- 依賴的說法：使用者筆記裡「60 秒心率回到有氧閾值就加趟」的文字出處（§1.1）。補查後的結論是「不要因為心率回得快就加趟」，所以原書只會確認筆記，不太會改規則。
- 取得：使用者筆記是從這本書來的，**手邊可能就有**。否則 D（Readmoo／博客來）。

### 3.5 徐國峰 RQ 文章兩篇 —— **P2**

| 文章 | 連結 | 缺什麼 | 依賴的說法 |
|---|---|---|---|
| 〈間歇訓練的「恢復秒數」〉 | https://www.runningquotient.com/article/single/107 | 403，未讀（L65、L662） | 搜尋摘要有「原本 1 分鐘回到 130 bpm，後來 50 秒」和「比較時條件要一致」，因查無原頁沒採用 |
| 〈利用訓練指數來找出間歇該練幾趟比較適合自己〉 | https://www.runningquotient.com/article/single/18 | 403，未讀（L68、L663） | 筆記裡「趟數上限看 TSS」那段 |

- 取得：E（瀏覽器登入 RQ 會員）。RQ 全站文章頁對我們都回 403，可能要會員。

### 3.6 〈Durability, Fatigability, Repeatability and Resilience〉. *J Appl Physiol* 2025 —— **P2**

- 連結：doi:10.1152/japplphysiol.00343.2025
- 缺什麼：403，未讀（L789）。
- 依賴的說法：耐久度、重複能力的定義與量法。影響間歇判斷的用詞和耐久度圖表的定義，不改門檻。
- 取得：A（APS 期刊常有 12 個月後開放）→ B。

### 3.7 P3：歷史出處、只有摘要的實驗

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 取得 |
|---|---|---|---|---|
| Reindell H, Roskamm H. Ein Beitrag zu den physiologischen Grundlagen des Intervalltrainings unter besonderer Berücksichtigung des Kreislaufes. *Schweiz Z Sportmed* 1959;7:1–8 | 文件未附 DOI | 原文未核對（L245） | 「心率回到 120 再跑」的一手出處；Billat 2001 只說他們最早描述間歇，沒寫 120 | C（德文舊期刊，可能要館際合作） |
| Reindell H, Roskamm H, Gerschler W.《Das Intervalltraining》. München: Barth, 1962 | 書 | 原書未核對（L203、L676） | 同上；「90 秒內回到 120–125」只有 Magness 部落格 | C（二手書或德國圖書館） |
| Seiler S, Hetlelid KJ. 2005. *Med Sci Sports Exerc* | doi:10.1249/01.mss.0000177560.18014.d8 | 只有摘要（L202） | 6×4 分以組休 2 分最好（L279、L487） | A → B |
| Buchheit M, Laursen PB. 2013. HIIT Part I／II. *Sports Med* | doi:10.1007/s40279-013-0029-x；doi:10.1007/s40279-013-0066-5 | 只有摘要（L202、L283） | 具體工休比建議沒讀到，**未驗證** | A → C |
| Le Meur Y, et al. 2017. *IJSPP* | doi:10.1123/ijspp.2015-0675 | 只有摘要 | f-OR 組 HRR 快 16±7 bpm（L214） | A → B |
| Mann TN, et al. 2014. *Eur J Appl Physiol* | doi:10.1007/s00421-014-2907-9 | 只有摘要 | 前一段強度越高 HRR60 越快（L216） | A → B |

## 4. `detraining.md`：停訓復跑

依賴的單與程式：`reentry.py`（FVDOT 表 `:25–30`、`:65`；生病分支）；SP-117 生病停跑記進傷病紀錄，分輕微感冒和發燒兩類復跑；SP-98、SP-109（Daniels 的轉換期規則，見 §5）。

### 4.1 Daniels J.《Daniels' Running Formula》（第 3 或第 4 版；中譯《丹尼爾斯博士跑步方程式》） —— **P1**

- 連結：書，文件未附版次。使用者筆記有中文版表 9.2 的附圖（L554），表 9.1 沒有。
- 缺什麼：原書未核對。表 9.1 只從 VDOT O2 部落格（2018）抄來（§4.2，L159–173）。
- 依賴的說法：
  - 表 9.1 的 42 天 FVDOT-2：網頁寫 0.994，前後是 0.955／0.934，應是 0.944 的筆誤（L173、§8 L480）。`reentry.py:28` 的註解也寫了這件事。要對書確認，並查書裡「有交叉訓練」的定義。
  - 轉換期與休賽季：「Daniels 認為計畫性休息最少 2 週、最多約 6 週」只出現在搜尋摘要，查無出處（`periodization-cross-sport.md` §4.7.1 L319、L321）。影響 SP-109 A 賽是超馬時放寬轉換期上限。
  - `specific-phase-progression.md` L65：M／T／I 配速的單堂上限只讀到讀書筆記。
- 取得：中文版**可能手邊就有**（筆記有表 9.2 附圖）。否則 D。

### 4.2 Schwellnus M, Adami PE, Bougault V, et al. IOC consensus statement on acute respiratory illness in athletes part 1: acute respiratory infections. *Br J Sports Med* 2022;56(19) —— **P2**

- 連結：doi:10.1136/bjsports-2022-105759；PMID 35863871
- 缺什麼：BJSM 403、Europe PMC 非開放；奧會官網的開放 PDF 試 4 次都逾時；casem-acmse.org 轉存回 520；academia.edu 403（§6.2 L290、§8 L485、§9 L546）。
- 依賴的說法：生病後回到運動的分階表。網路流傳的「5 階段、< 70 % HRmax 起步、脖子以下 ≥ 10 天、脖子以上 ≥ 5 天、無症狀 3 天」只在搜尋摘要，**查無出處、不採用**。`reentry.py` 的生病分支和 SP-117 要靠這張表。
- 取得：E —— **奧會官網 stillmed.olympics.com 的 PDF 是開放下載**，只是我們這邊逾時，換網路環境用瀏覽器開應該就有。

### 4.3 Salman D, Vishnubala D, Le Feuvre P, et al. Returning to physical activity after covid-19. *BMJ* 2021;372:m4721 —— **P2**

- 連結：doi:10.1136/bmj.m4721
- 缺什麼：付費牆（bmj.com 403、Europe PMC 非開放）（L320、L547）。
- 依賴的說法：一般民眾版的生病後分階。和 4.2 一起決定 SP-117 的發燒類復跑。
- 取得：A（BMJ 的 COVID 文章很多後來免費開放）→ C。

### 4.4 Current return to sports recommendations after non-severe COVID-19 from an exercise immunology perspective: a scoping review —— **P2**

- 連結：ScienceDirect pii S0949328X23002326（文件未附 DOI 與作者）。
- 缺什麼：403，未讀（L486、L549）。
- 依賴的說法：搜尋摘要裡那組「5 階段……」數字都出自這篇。讀到就知道能不能採用。
- 取得：A → C。拿到後補作者、期刊、DOI。

### 4.5 Serrano N, Dupont-Versteegden EE, Murach KA. Muscle memory theory: a critical evaluation. *J Physiol* 2025;603:4705–4711 —— **P2**

- 連結：doi:10.1113/jp289597
- 缺什麼：Europe PMC 沒有摘要，未讀（L548）。
- 依賴的說法：「再練比首練快」。目前只有 Pilotto 2025 和 Houston 1979 的摘要，結論是「沒有比較快」，`TARGETS_AFTER_DAYS` 14 天保留（L384）。
- 取得：A（J Physiol 常有開放版）→ B。

### 4.6 P3

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 取得 |
|---|---|---|---|---|
| Schwellnus M, et al. IOC consensus part 2: non-infective acute respiratory illness. *Br J Sports Med* 2022 | doi:10.1136/bjsports-2022-105567 | 只讀摘要（L533） | 非感染性呼吸道問題的回場；app 目前沒用 | E（同 4.2，奧會官網） |
| Pilotto AM, Turner DC, Mazzolari R, et al. *Am J Physiol Cell Physiol* 2025;328:C258–C272 | doi:10.1152/ajpcell.00423.2024 | 只讀摘要（L499、L537） | 再練 VO2max 進步和首練一樣 | A |
| Houston ME, Bentzen H, Larsen H. *Acta Physiol Scand* 1979;105:163–170 | doi:10.1111/j.1748-1716.1979.tb06328.x | 只讀摘要（L499、L538） | 停 15 天、練 15 天後耐力仍差 9 % | C |

另外：Pfitzinger 的中斷規則和 pfitzinger.com 的骨折回跑計畫，見 §5.1。

## 5. `periodization-cross-sport.md`：周期化

依賴的單與程式：SP-95 B、C 賽事要影響當週排課（`planning.py` `MINI_TAPER_DAYS`、`B_RECOVERY_DAYS`）；SP-96 減量期依賽事調整長度、保留跑步次數、加上賽週規則；SP-98 賽後恢復期的量改用賽前水準，結束後逐步回量；SP-102 沒有 A 賽事時的維持模式要怎麼排；SP-109 A 賽是超馬時放寬轉換期上限；SP-104 周期相關的推估常數補上來源。

### 5.1 Pfitzinger P, Douglas S.《Advanced Marathoning》（Human Kinetics）；中譯《進階馬拉松全書》（堡壘文化） —— **P1**

- 連結：書。中譯本書摘 [459] https://vocus.cc/article/637f14b9fd8978000141ef4c（已讀，只有減量）；二手轉述 [437]、書評 [451]。
- 缺什麼：原書未核對。博客來、誠品 403；運動視界的書摘（sportsv.net 98878、127853）403；pfitzinger.com 的骨折回跑計畫連線被拒、Wayback 也沒有（`detraining.md` L265–266）。
- 依賴的說法：
  - B 賽前 5 天不做間歇、4 天不做節奏跑和長跑；賽後約 5 天恢復 —— 二手轉述 [437]，未註明書名（§4.8.1 L386）。SP-95 的 5 天小減量、半馬到 30 km 賽後 5 天都靠這條。
  - 書末的 5 週回跑計畫，內容未讀（§4.6.1 L256、§4.7.1 L301）。影響 SP-98 的回量期。
  - 中斷後的具體天數規則（`detraining.md` §4.8、§8 第 4 點）。
  - 減量部分已由中譯本書摘補上（比賽週 −60 %），不用再查。
  - `specific-phase-progression.md` L64：MP 長跑進度（16 英里含 8 → 18 含 10 → 18 含 14），只有第三方整理。
- 取得：D。中文版 Readmoo／博客來電子書；英文版 Kindle。

### 5.2 Koop J.《Training Essentials for Ultrarunning》. VeloPress —— **P1**

- 連結：書，文件未附版次。
- 缺什麼：原書未讀。現在都從 Koop 在 CTS（trainright.com）的文章轉引（[211][419][434][439][440]），書只讀到書評（`trail-terrain-and-climb-sessions.md` L51、L184）。
- 依賴的說法：
  - 超馬減量 2–3 週、最後一週 < 20 %（[419]，SP-96 決定不採用，但要確認書裡是否一樣）。
  - 超馬轉換期：整段 2–4 週以上不跑、只做交叉訓練（[439]，SP-103、SP-109）。「Koop 建議轉換期 3–4 個月」只在搜尋摘要，查無出處（L319）。
  - 100 英里賽後第 2–3 週只跑恢復配速（[434]，SP-98）。
  - 爬坡課：間歇約 80 % 在上坡、下坡練得少（`trail-terrain-and-climb-sessions.md` §2）；`workout-templates.md` 的 `koop_tempo`、`tech_easy` 範本。
  - 背靠背長天（`b2b.py` 的 Koop 規則：最後 2–3 週不硬塞、總量不加）。
- 取得：D。

### 5.3 Aubry A, et al. Functional overreaching: the key to peak performance during the taper? *Med Sci Sports Exerc* 2014 —— **P2（新增）**

- 連結：[279] https://pubmed.ncbi.nlm.nih.gov/25134000（文件未附 DOI）
- 缺什麼：PubMed 頁只回 cookie 提示；Europe PMC 只拿到摘要的結論句（L406）。
- 依賴的說法：「減量後 2 週內出現 60–83 % 的最佳表現」（§6.2 SP-90 列）；沒拿到「巔峰出現在第幾天」。影響兩場 A 賽太近的提示和 SP-102 的巔峰維持長度。
- 取得：A → B（Aubry 和 Le Meur 是 INSEP 那組，ResearchGate 常有）。

### 5.4 P3：中文教科書、雜誌文章、只有摘要的研究

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 取得 |
|---|---|---|---|---|
| 田麥久《運動訓練學》 | 書 | 原書未核對；知乎、百度百科整理頁 403（§4.10.3 L470） | 各期比例。現在只有考研心智圖的節點名稱，沒有數字；只當背景 | D 或 C（大陸教科書，台灣大學體育系圖書館可能有） |
| 徐國峰《KFCS跑力提升訓練系統》（臉譜）第八章 | 書 | 原書未核對（L460） | 各期週數；出版社書摘沒有分期細節 | D |
| 徐國峰 RQ 文章 6 篇：article 32、61、141、192；〈面臨重要比賽前該如何減少訓練量？〉〈全馬週期化訓練數據總分析〉 | runningquotient.com | 403（L461、L522） | 減量比例（搜尋摘要「賽前三週減 20–25 %、兩週 40 %、比賽週 60 %」查無出處）、各期週數 | E（RQ 會員） |
| 《運動訓練法》p.262（作者未記） | 書 | 原書未核對，只有使用者筆記（L443） | 肌力分期：三鐵準備期 100／比賽期 50／過渡期 20；過渡期不超過 4 週 | 使用者手邊應有（筆記引了頁碼）。可能和 SP-85 的 PDF 是同一本，待確認 |
| Chen, Hsieh, Ho, Lin, Lin. Two weeks of detraining reduces cardiopulmonary function and muscular fitness in endurance athletes. *Eur J Sport Sci* 2022 | [448] doi:10.1080/17461391.2021.1880647；PMID 33517866 | 只讀摘要，摘要沒給百分比（L305） | 2 週停訓各項掉多少 | A → B（台灣作者，寫信容易） |
| Wells, Hoffmann, Bruce, Kremer, Dwyer. Training load and intensity in triathlon … 95 age-group triathletes. *Front Sports Act Living* 2026 | [450] doi:10.3389/fspor.2026.1798702 | 只讀摘要（L307） | 休賽季約為專項期的 61 %（時間） | **Frontiers 是開放期刊**，直接讀全文 |
| Roche D.〈Should You Change How You Think About Tapers For Long Races?〉（Trail Runner） | 文件未附網址 | 要登入 Outside 帳號（L229） | 超馬減量 | E（Outside+ 會員） |
| Roche D.〈How Long Should My Long Runs Be?〉（Outside Run） | 文件未附網址 | 同上（L229） | 超馬最長一次長跑 | E |
| Roche D.〈Is Multiple 100-Mile Races In A Single Season Too Much?〉（Trail Runner） | 文件未附網址 | 同上（L405） | 一季多場百英里 | E |
| 〈Messed Up Your Marathon? What to Know Before Running Another Right Away〉（Outside Run） | 文件未附網址 | 同上（L405） | 失利後多久再比 | E |
| 〈The Rules of Recovery〉. *Washington Post* 1998 | 文件未附網址 | 403（L320） | 賽後恢復 | E 或 C（報紙資料庫） |

單上 #16 列的周期化模型論文（Gonzalez-Ravé 2022 反向周期化系統性回顧 PMC9023617、Stone 2021 PubMed 34132223、Issurin 2008、Talsnes 越野滑雪 PMC10694351、IJSPP 2025 划船系統性回顧），在基準 commit 的 `docs/research/` 裡**找不到**這些編號，沒有列入計數。Issurin 只有 [248]〈New horizons for the methodology and physiology of training periodization〉（*Sports Med*，PubMed 20199119）被引用，文件沒有標它讀不到。可能是 SP-94 調查時的中間紀錄。結論（不做區塊／反向周期化）已由 [4][6][7][76] 支持（§2）。

## 6. `race-feasibility.md`：賽事可行性

依賴的單：SP-112 可行性判定改用 EP 週量、週量不判超出、新增跨級檢查；SP-105 賽事可行性判定；SP-107 #4d。

### 6.1 山本正嘉的三本書 —— **P1**

| 書 | 出版資訊 | 缺什麼 |
|---|---|---|
| 《登山の運動生理学とトレーニング学》 | 2016，出版社未核對 | 原書未核對（`race-feasibility.md` L224；`mountaineering-physiology-scholars.md` L236） |
| 《登山と身体の科学：運動生理学から見た合理的な登山術》 | `mountaineering-physiology-scholars.md` 寫山と溪谷社 2024；`baiyue-technical-terrain.md` L232 寫講談社ブルーバックス。**兩份文件不一致**，買之前先查 | 原書未核對 |
| 《最新！登山の科学》 | 2019，出版社未核對 | 原書未核對 |

- 依賴的說法：
  - 「登高能力テスト」：用自己的配速連續爬 1 小時能爬多少公尺，健行 385 m/h、無雪期登山 475 m/h……（§2.5 表）。目前讀的是長野縣《高年登山者の傾向と対策》裡山本的投稿，書裡可能有更完整的表和年齡別數字。
  - コース定数的公式與「訓練要達到目標コース定数的幾成」（還是沒找到這種比例）。
  - 百岳爬升速度的預設值（SP-112 的跨級檢查、SP-114 多日百岳）。
- 取得：D（Kindle 日本、honto、Amazon.co.jp）。三本裡 2016 那本最學術，先買它。

### 6.2 P2：超馬完賽因子

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 取得 |
|---|---|---|---|---|
| Martinez-Navarro I, et al. 2018. *J Sports Sci*（65 km 山地超馬） | 文件未附 DOI 和題名 | 搜尋結果標「Get Access」，付費牆，未讀（L233） | 山地超馬完賽／DNF 的預測因子 | A → B（西班牙 Valencia 大學那組） |
| Wegelin JA, Hoffman MD. Variables associated with odds of finishing and finish time in a 161-km ultramarathon. *Eur J Appl Physiol* 2011 | 文件未附 DOI；摘要經 Europe PMC 讀到 | PMC 全文頁要驗證碼（L104、L231） | 第一次出賽、年份越近完賽機會越高；38 歲以上逐年降低、天氣越熱越低。沒拿到勝算比 | E（PMC 換瀏覽器過驗證碼）→ A |
| Knechtle B, et al. 2009. *Percept Mot Skills*（Deutschlandlauf 多日超馬） | https://pubmed.ncbi.nlm.nih.gov/19831091/ | 非開放，只有摘要（L232） | 24 人 14 人 DNF；訓練量和完賽無關 | B（Knechtle 在 ResearchGate 很活躍）|

### 6.3 P3

| 文獻 | 連結 | 缺什麼 | 取得 |
|---|---|---|---|
| 現代ビジネス，山本正嘉的文章第 2 頁以後 | https://gendai.media/articles/-/130295 | 只讀到第 1 頁，看不出要不要登入（L169、L234） | E |

## 7. `mountaineering-physiology-scholars.md`：登山生理

依賴的單：SP-100 高海拔賽事與百岳行程前的高度適應提醒；SP-112；SP-114 多日百岳的訓練與評估跟越野跑分開；SP-115 登山爬坡的心率上限改用 75 % HRmax（萩原・山本）；負重訓練（`loaded-carry-training.md`）。

### 7.1 P2：高山病指引與研究（SP-100）

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 取得 |
|---|---|---|---|---|
| Luks AM, Auerbach PS, Freer L, et al. WMS clinical practice guidelines for the prevention and treatment of acute altitude illness: 2019 update. *Wilderness Environ Med* 2019;30(4S):S3–S18 | doi:10.1016/j.wem.2019.04.006 | 非開放，只讀摘要（L175、L230） | 高山病預防的主要臨床指引。現在用 CDC 版的數字（CDC 就是引 WMS），所以只是補一手出處 | A（WMS 指引常有開放 PDF）→ C |
| Luks AM, Beidleman BA, Freer L, et al. WMS … 2024 update. *Wilderness Environ Med* 2024 | doi:10.1016/j.wem.2023.05.013 | 同上（L231） | 同上，較新版 | A → C |
| Shen TC, Lin MC, Lin CL, Lin WH, Chuang BK. Acute mountain sickness on Jade Mountain: results from the real-world practice (2018–2019). *J Formos Med Assoc* 2024;123(11):1161–1166 | doi:10.1016/j.jfma.2024.01.030 | 非開放，只讀摘要；勝算比沒拿到（L232） | 台灣玉山高山症的危險因子 | B（台灣作者，寫信）→ C（台灣醫學院圖書館一定有） |
| Schneider M, Bernasch D, Weymann J, Holle R, Bärtsch P. Acute mountain sickness: influence of susceptibility, preexposure, and ascent rate. *Med Sci Sports Exerc* 2002;34(12) | doi:10.1097/00005768-200212000-00005 | 只讀摘要（L234） | 行前高度暴露的定義與效果（SP-100 的「出發前 14 天、2,750 m 以上過 2 晚」） | A → C |
| Burtscher M. Endurance performance of the elderly mountaineer: requirements, limitations, testing, and training. *Wien Klin Wochenschr* 2004;116:703–714 | doi:10.1007/s00508-004-0258-y | 只讀摘要（L163、L233） | 每小時上 300 m 需要 18–22 ml/kg/min、1.2–1.5 W/kg。SP-112 背包爬坡所需功率 | B（Burtscher 在 ResearchGate）→ C |

### 7.2 P2：日本《登山医学》等期刊

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 取得 |
|---|---|---|---|---|
| 中原玲緒奈・萩原正大・山本正嘉. 登山のエネルギー消費量推定式の作成 ―歩行時間, 歩行距離, 体重, ザック重量との関係から―. 登山医学 2006;26:115–121 | https://jglobal.jst.go.jp/detail?JGLOBAL_ID=200902212957954269 | 沒讀到（L223、L237；`effort-distance-formulas.md` L149–152、L527） | コース定数 `1.8h + 0.3km + 10·up + 0.6·down` 的原始論文：受試者、擬合誤差。`effort-distance-formulas.md` 的 Yamamoto 列靠它 | F |
| 山本正嘉 2001. 登山の疲労とその防止. 疲労と休養の科学 16:19–24 | 文件未附連結 | 沒讀（L222）。**新增** | 「75 % HRmax 以下或 RPE ≤ 13」的原始出處；萩原 2011 引用它。SP-115 的上限就是這個數字 | F |
| 山本・西谷 2010（登山医学） | 文件未附完整書目 | 原文未讀，只有安藤 2020、吉塚 2023 兩份轉引（L54、L237） | 膝伸展肌力 ÷ 體重：快要 0.85、同速 0.70、< 0.65 容易出問題 | F |
| 宮崎等 2018（登山医学） | 文件未附完整書目 | 二手（安藤 2020 轉引）（L55、L237） | 只靠登山練腿力要「每月 3–4 次」；完備程度的「近 4 週登山次數」 | F |
| 前大等 2013 | 文件未附完整書目 | 二手（吉塚 2023 轉引）（L57、L237） | 下坡走 40 分，股四頭肌肌力降近 30 %（SP-111） | F |
| 笹子悠歩, 山本正嘉. 登山を想定した体力トレーニングのためのポイント表（試案）の作成. スポーツトレーニング科学 2021;22:33–36 | 文件未附連結 | 點數表只讀到轉載（L236、L261） | 登山體力的點數換算 | F。注意：單上寫成「書」，其實是期刊論文 |

### 7.3 P3：吉塚一典的報告與學會發表（KAKEN 成果清單）

| 文獻 | 缺什麼 | 取得 |
|---|---|---|
| 吉塚一典. 登山愛好者の月間登下降距離に関する事例報告. 佐世保工業高等専門学校研究報告 2021;57:25–27 | 未讀（L257） | F（高專紀要通常放在機構典藏，可能免費） |
| 吉塚一典, 濵田臣二, 大山泰史. 週7～15kmのランやウォークが登山体力および心身の健康意識に及ぼす影響. 同上 2021;57:28–33 | 未讀（L258） | F |
| 吉塚一典, 濵田臣二, 大山泰史, 森崎太一. ランニングを活用した登山トレーニングの検討. 第35回日本トレーニング科学会大会, 2022 | 學會發表，未讀（L259） | B（寫信給作者） |
| 吉塚一典, 濱田臣二, 大山泰史. 登山に必要な体力と事前トレーニングを数値化する試み（ランニングのポイント換算案の提案）. 第43回日本登山医学会, 2023 | 學會發表，未讀（L260） | B |

另外不是付費牆、但要人工處理的：萩原・山本 2011 的**表 2**（垂直速度 × 總重 → VO2、METs）在開放 PDF 裡是圖片，沒擷取到數字（L36、L220）。打開 J-STAGE 的 PDF 人工抄表即可，不算在清單裡。

## 8. `downhill-recovery.md`：下坡損傷與賽後恢復

依賴的單：SP-111 賽事大小改用預估時間、EP 判斷，修只看距離的地方（第二階段：恢復指標、升級天數）。

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 級 | 取得 |
|---|---|---|---|---|---|
| Morin JB, Tomazin K, Edouard P, Millet GY. Changes in running mechanics and spring-mass behavior induced by a mountain ultra-marathon race. *J Biomech* 2011 | [D18] doi:10.1016/j.jbiomech.2011.01.028 | 只讀摘要（L175） | 超馬後腿部剛性和步頻**上升**；第二階段「剛性看偏離、不看下降」 | P2 | A → B（Millet 在 ResearchGate） |
| Degache F, Guex K, Fourchet F, et al. Changes in running mechanics and spring-mass behaviour induced by a 5-hour hilly running bout. *J Sports Sci* 2013 | [D19] doi:10.1080/02640414.2012.729136 | 只讀摘要（L175） | 同上 | P2 | A → B |
| Chen TC, Nosaka K, Wu CC. Effects of a 30-min running performed daily after downhill running on recovery of muscle function and running economy. *J Sci Med Sport* 2008 | [D2] doi:10.1016/j.jsams.2007.02.015 | 只讀摘要（L34、L174） | 下坡跑後 RE 下降 7 天；之後每天跑 30 分不影響恢復 | P2 | B（台灣作者陳忠慶，寫信容易） |
| Hoffman MD, et al. Increasing creatine kinase concentrations at the 161-km Western States Endurance Run. 2012 | 文件未附期刊與 DOI | 未讀（ScienceDirect／ResearchGate）（L176） | 百英里賽後 CK 的時間進程；SP-111 升級天數 | P2 | B（ResearchGate 頁已存在，按 Request） |

## 9. `fueling-and-energy.md`：補給

依賴的程式：`racepower/fuel.py`；`racepower.html:1384` 的來源文字（「ACSM 2007（Sawka）：脫水不超過體重 2 %」）。

### 9.1 Sawka MN, Burke LM, Eichner ER, et al. American College of Sports Medicine position stand: exercise and fluid replacement. *Med Sci Sports Exerc* 2007;39(2):377–390 —— **P2**

- 連結：doi:10.1249/mss.0b013e31802ca597
- 缺什麼：付費牆，只讀摘要（§4.1 L232–236）。
- 依賴的說法：「賽前 4 h 喝 5–7 ml/kg」「飲料含鈉 20–30 mEq/L」常被引用，但是全文內容，**未驗證**。如果補給計算器要顯示這兩個數字，要先讀到。
- 取得：A（ACSM 立場聲明多半有免費 PDF）→ C。

## 10. `coaching-dashboards-mountain.md`：教練儀表板

依賴的單：SP-100（高度適應劑量）。

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 級 | 取得 |
|---|---|---|---|---|---|
| Garvican-Lewis LA, et al. 2016. *J Appl Physiol*（"kilometer hours" 低氧劑量的 letter） | https://journals.physiology.org/doi/full/10.1152/japplphysiol.00556.2016 | 403（L45、L458） | `km·h = (海拔 m / 1000) × 小時`；每 +100 km·h 血紅素質量約 +0.4 %。現在只從 PMC7714921 和搜尋摘要二手核對 | P2 | A（APS 期刊 12 個月後免費）→ E |
| Casado A, et al. 2022. *IJSPP* 17(6):820（菁英長跑選手的強度分配系統性回顧） | https://journals.humankinetics.com/view/journals/ijspp/17/6/article-p820.xml | 403，只有搜尋摘要（L43、L361） | 高水準長跑選手以金字塔分配最常見 | P3 | A → B |

## 11. `effort-distance-formulas.md`：努力距離公式

中原ら 2006 見 §7.2。

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 級 | 取得 |
|---|---|---|---|---|---|
| Scarf P. Route choice in mountain navigation, Naismith's rule, and the equivalence of distance and climb. *J Sports Sci* 2007;25(6):719–726 | doi:10.1080/02640410600874906 | Taylor & Francis 403、ResearchGate PDF 403、Salford 典藏連結失效（L22、L245、L526） | 爬升 : 水平 = 1 : 7.92（男性與健行 1:8、女性 1:10）。目前從搜尋摘要和 Wikipedia | P3 | B（Salford 大學，寫信）→ A |
| Pühringer R, et al. 2022. *High Alt Med Biol* | 文件未附 DOI | 403（L388） | 「1,500 m 以上每 1,000 m 掉 5.0–11.6 %」只在搜尋摘要，**未驗證** | P3 | A → C |
| Fulco CS, et al. 1998. USARIEM 回顧 | https://pubmed.ncbi.nlm.nih.gov/9715971/ | PubMed 要 cookie，摘要經搜尋摘錄（L383–386） | VO2max 從 580 m 開始下降 | P3 | E（瀏覽器開 PubMed） |

## 12. `racepower-v2.md`：賽事計算器公式

依賴的程式：`racepower/` 的 W′bal、背負、海拔換算。三篇主要公式已用 2 個以上開放來源二手核對（§3C），所以都是 P3。

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 取得 |
|---|---|---|---|---|
| Skiba PF, Chidnok W, Vanhatalo A, Jones AM. Modeling the expenditure and reconstitution of work capacity above critical power. *Med Sci Sports Exerc* 2012;44:1526–1532 | doi:10.1249/mss.0b013e3182517a80；PMID 22382171 | 只讀摘要（§3C L426、L1309） | W′bal 的 τ 式；已經 5 篇開放論文＋GoldenCheetah 核對 | A → B |
| Pandolf KB, Givoni B, Goldman RF. Predicting energy expenditure with loads while standing or walking very slowly. *J Appl Physiol* 1977;43:577–581 | doi:10.1152/jappl.1977.43.4.577 | 全文 403（L1296–1297） | 背負公式；原式已二手核對，**原始的速度與坡度適用範圍**還沒確認（L1222） | A（APS 舊文多已免費）→ C |
| Bassett DR, Kyle CR, Passfield L, Broker JP, Burke ER. Comparing cycling world hour records, 1967–1996: modeling with empirical data. *Med Sci Sports Exerc* 1999;31:1665–1676 | doi:10.1097/00005768-199911000-00025 | 全文 402（L1236–1239） | 海拔 km 多項式；已由 TrainingPeaks、Simmons 2014 二手核對 | C |
| Santee WR, et al. 2003. USARIEM 技術報告 | DTIC ADA415788 | DTIC 403（L593） | 下坡背負修正係數，目前**只有單一來源**（Weyand 2021 表 1），所以下坡仍用 v1 的線性係數（L982） | E（DTIC 換網路環境；公開報告） |
| Péronnet F, Thibault G, Cousineau DL. A theoretical analysis of the effect of altitude on running performance. *J Appl Physiol* 1991;70:399–404 | doi:10.1152/jappl.1991.70.1.399；PMID 2010398 | 文件沒標付費牆，但列為「待取得 PDF」（L1222–1224）。**新增** | `env.py` torr 多項式的出處，目前單一來源 | A（APS 舊文多已免費） |

## 13. `baiyue-technical-terrain.md`：百岳技術地形

依賴的單：SP-114（百岳評估）；技術地形與平衡練習。

| 文獻 | 連結 | 缺什麼 | 依賴的說法 | 級 | 取得 |
|---|---|---|---|---|---|
| Springer BA, Marin R, Cyhan T, et al. Normative values for the unipedal stance test with eyes open and closed. *J Geriatr Phys Ther* 2007 | doi:10.1519/00139143-200704000-00003 | 只讀摘要，**常模數字沒讀到**（L175、L225、L229）。**新增** | 單腳閉眼站的各年齡常模（18–39、40–49 … 80+）。要顯示「你的成績在同齡的哪裡」就需要這張表 | P2 | A（這篇常被引，網路上有全文）→ C |
| Kümmel J, Kramer A, Giboin LS, Gruber M. Specificity of balance training in healthy individuals: a systematic review and meta-analysis. *Sports Med* 2016 | doi:10.1007/s40279-016-0515-z | 只讀摘要（L231） | 平衡訓練只轉移到練過的任務；結論已夠用 | P3 | A |
| Martin J, Kearney J, Nestrowitz S, et al. Effects of load carriage on measures of postural sway in healthy, young adults. *Appl Ergon* 2023 | doi:10.1016/j.apergo.2022.103893 | 只讀摘要（L230） | 背包對平衡的影響 | P3 | A → C |

`baiyue-technical-terrain.md` L233 說長野縣《登山 Safety Book》原書未核對，但 `mountaineering-physiology-scholars.md` 後來讀到了 PDF 全文（L37），所以不列。該文件可以順手把那行改掉。

## 14. 其他研究文件（P3 為主）

| 研究文件 | 文獻 | 連結 | 缺什麼 | 依賴的說法 | 級 | 取得 |
|---|---|---|---|---|---|---|
| `heat-acclimation.md` | Roberts WO, et al. 2023. ACSM expert consensus statement on exertional heat illness. *Curr Sports Med Rep* 22:134–149 | doi:10.1249/JSR.0000000000001058 | 只讀摘要（L296、L670）。**新增** | app 的熱傷害安全症狀清單標 [待驗證]，要對全文逐條 | P2 | A（Curr Sports Med Rep 的 ACSM 共識通常免費） |
| `back-to-back-and-long-day.md` | Roche D, Roche M. Back-to-Back Long Runs and Workouts（Trail Runner） | https://run.outsideonline.com/training/workouts/back-back-long-runs-workouts-next-level-training-done-right/ | 付費牆，只有搜尋摘要（L146、L364） | 一次 40–50 英里改成兩天；第 1 天速度、第 2 天爬坡 | P3 | E（Outside+ 會員） |
| `trail-terrain-and-climb-sessions.md` | Johnston S. 爬坡文章（Trail Runner Magazine） | 文件未附網址與題名 | 付費牆（L184） | 爬坡課、肌耐力課；已有 iRunFar 2025 訪談代替 | P3 | E |
| `specific-phase-progression.md` | Arcelli E, Canova R.《Marathon Training – A Scientific Approach》1999 | 書；書評 https://runningwritings.com/2023/06/canova-marathon-book.html | 原書未讀，只讀 John Davis 書評（L60） | 專項期賽前 6–8 週、30–35 km @ 97–100 % MP；SP-75 專項期內部進階 | P3 | D（可能已絕版，二手書） |
| `plan-backtest-feasibility.md` | Hellard P, Avalos M, Lacoste L, et al. Assessing the limitations of the Banister model in monitoring training. *J Sports Sci* 2006;24:509–520 | doi:10.1080/02640410500244697 | 只讀摘要（L177） | Banister 參數的信賴區間太寬；結論已夠用 | P3 | A |
| `plan-backtest-feasibility.md` | Hopkins WG, Schabort EJ, Hawley JA. Reliability of power in physical performance tests. *Sports Med* 2001;31:211–234 | doi:10.2165/00007256-200131030-00005 | 只讀摘要（L177） | 定功率測試典型誤差 CV 0.9–2.0 % | P3 | A |
| `baiyue-from-running.md` | Levine L, et al. 1982. *Ergonomics* 25:393 | 文件未附 DOI | 只讀到題名，沒用（L239） | — | P3 | C |
| `baiyue-from-running.md` | Campbell MJ, et al. 2019. *Applied Geography* 106:93–107 | doi:10.1016/j.apgeog.2019.03.008 | 內容未讀，沒用（L816） | 日後可當 Tobler 之外的坡度-速度對照 | P3 | A |

## 15. 依單整理：拿到哪一份會動到哪張單

| 單 | 要先拿的（P1 → P2） |
|---|---|
| SP-110 間歇疲勞保險 | Bellenger 2016（§3.2） |
| 間歇判斷（`LAST_FADE`，尚未開單） | Allen & Coggan（§3.1）；JSAMS 2025 τ（§3.3） |
| SP-95 B、C 賽事 | Pfitzinger（§5.1） |
| SP-96 減量期 | Koop（§5.2） |
| SP-98 賽後恢復與回量 | Pfitzinger 5 週回跑計畫（§5.1）；Koop（§5.2） |
| SP-109 超馬轉換期上限 | Daniels（§4.1）；Koop（§5.2） |
| SP-112 可行性判定 | 山本三本書（§6.1）；Burtscher 2004（§7.1）；超馬完賽因子三篇（§6.2） |
| SP-100 高度適應 | WMS 2019／2024、Schneider 2002、Shen 2024（§7.1）；Garvican-Lewis 2016（§10） |
| SP-111 下坡恢復第二階段 | Morin、Degache、Chen 2008、Hoffman 2012（§8）；前大 2013（§7.2） |
| SP-115 登山心率上限 | 山本 2001（§7.2） |
| SP-117 生病復跑 | IOC 2022 part 1（§4.2，奧會官網 PDF 開放）；Salman 2021、scoping review（§4.3、§4.4） |
| `reentry.py` FVDOT | Daniels 表 9.1（§4.1） |
| 補給計算器 | Sawka 2007（§9.1） |

## 16. 可能手邊就有、或很容易拿到的

先試這些，成本最低：

- 徐國峰《跑者都該懂的跑步關鍵數據》、Daniels 中文版、《運動訓練法》：使用者筆記引過頁碼或表號，書可能在手邊。
- IOC 2022 共識：奧會官網 PDF 是開放的，換網路環境開。
- Wells 2026（*Front Sports Act Living*）：開放期刊，直接讀。
- APS 期刊（*J Appl Physiol*：Pandolf 1977、Péronnet 1991、Garvican-Lewis 2016、Durability 2025）：舊文多半已免費，用瀏覽器開。
- Chen 2008、Chen 2022、Shen 2024：台灣作者，寫信要最快。

## 17. 不是論文、回 403 的網站

這些不是文獻，只是我們的抓取工具被擋。用瀏覽器開可能就讀得到，不計入上面的 83 個。

| 網站 | 研究文件 | 缺的內容 |
|---|---|---|
| COROS 支援：〈April 2023 Update Common Questions〉〈Setting Resting and Max Heart Rate Data〉 | `coros-threshold-unification.md` L104、L180 | 「LTHR 只能自動調整」「可以編輯預設區間」只看到搜尋摘要 |
| COROS 支援：Hill Alerts、Fitness Metrics | `competitor-charts.md` L130、L374 | 爬坡段分色規則 |
| TrainingPeaks help 全站（EF／Pa:HR、Dashboard Charts、Performance Insights、達成度顏色、Low rTSS and Trail Running） | `competitor-charts.md` L23、L370；`coaching-dashboards-mountain.md` L44；`estimated-constants-inventory.md` L256；`uphill-athlete-mountain-metrics.md` L395 | 達成度綠燈 80–120 %（`OVER_TSS` ±20 % 的依據）目前只有第三方整理 |
| Strava 工程部落格〈An improved GAP model〉（medium.com） | `racepower-v2.md` L45、L1380；`competitor-charts.md` L371；`uphill-athlete-mountain-metrics.md` L259 | GAP 下坡加成上限約 10 % |
| ITRA FAQ、Performance Index、資料使用條款 | `competitor-charts.md` L373；`effort-distance-formulas.md` L525；`public-datasets.md` L291 | ITRA 原文措辭；研究者例外條款**未驗證** |
| YAMAP 說明頁 | `effort-distance-formulas.md` L25、L528 | YAMAP 的時間來源 |
| Garmin Connect Terms of Use（一般使用者版） | `garmin.md` L120、L365 | 自動化存取條款 |
| Uphill Athlete 論壇〈How to schedule the recovery week〉 | `estimated-constants-inventory.md` L236 | 恢復週減 40–60 %，只看到搜尋摘要 |
| Runalyze、SportTracks、Nolio 的 API 條款 | `data-hubs.md` L159–162 | 商業條件**未驗證** |
| Freetrail 訓練計畫（付費內容） | `coaching-dashboards-mountain.md` L148 | 計畫內容 |
| Stryd 課表（會員限定） | `workout-templates.md` L158 | 沒有公開步驟頁 |
| pfitzinger.com 骨折回跑計畫 | `detraining.md` L265 | 連線被拒、Wayback 沒有 |
| 中文站：博客來、誠品、運動視界書摘、知乎、百度百科、陝西省學生體育協會、登山補給站、那丘 | `detraining.md` L266；`periodization-cross-sport.md` L522 | 《進階馬拉松全書》書摘、田麥久整理、百岳體能文章 |

## 18. 限制

- 行號以基準 commit `12678bf` 為準，之後可能位移；找不到時用文件內的章節號或關鍵字搜尋。
- 「新增」是 SP-113 單上沒有、這次掃到的。單上的 #16 在 `docs/research/` 找不到，沒有計入（§5.4）。
- 書目資料以研究文件寫的為準。文件沒寫的期刊卷期、DOI 都標「文件未附」，沒有自己補；取得原文後請一併補齊。
- 優先級是依 2026-10-05 的開單狀況排的。單關掉或改方向後，優先級要重排。
