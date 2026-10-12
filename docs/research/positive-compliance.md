# 課表統計改用正向呈現（SP-393）

> 日期：2026-10-12。只有研究與設計，沒有改程式。盤點靠讀程式碼（main `e2d1519`），沒開瀏覽器。
> 使用者原話：「當課表沒達成，統計數字很差的時候會有焦慮感，我不想要有焦慮感……獎賞達成，然後沒達成也沒關係……主要任務是要讓使用者更能享受完成課表的成就感」。
> mockup：`positive-compliance-mockup.html`（三個方案可切、淺／深色、手機寬度）。跟 SP-375（課表小卡 UI）的衝突點在 §7。

## 0. 一句話結論

**給人看的數字只往上加、不帶分母；「沒做」這件事交給自動調整處理、不做成視覺元素；原始達成率搬進收合區給教練模式用。** 推薦方案 **B「本週累積」**（§5）。

## 1. 現況盤點：哪裡會讓人覺得被扣分

| # | 畫面 | 元素 | 傳達什麼 | 扣分感從哪來 | 使用者真的需要？ |
|---|---|---|---|---|---|
| 1 | 統計頁 KPI 1（`compliance.html` :374-379） | **完成率** 72%＋「完成 13 / 18 堂」＋ 四色分段條（綠／橘／橘斜紋／紅） | 到期的課有做的比例 | 有分母；數字只會因「沒做」往下掉；紅色段直接畫出沒做的量 | 教練視角要；跑者日常不需要一個會掉的分數 |
| 2 | KPI 2（:380-381） | **達標率** 55%＋「✓ 10 · 部分 2 · 沒照課表 1」 | 照計畫完成的比例 | 門檻 ±20% 嚴，數字天生偏低；「沒照課表」這個詞像指責 | 不需要作為主數字；做成「照計畫完成 10 堂」就是正向的 |
| 3 | KPI 3（:382-384） | **TSS 完成度** 83%＋ 進度條（黑線＝100%） | 實際 ÷ 計畫負荷 | 「沒做的課算 0」；黑線像及格線 | 要，但把 100% 畫成目標線會變成「沒到」 |
| 4 | KPI 4（:385-386） | **時數** 6.2 / 8.0 h（計畫的 78%） | 實際 vs 計畫時間 | 分數形式 | 「6.2 h」單獨看是正向的；「/ 8.0」是扣分來源 |
| 5 | KPI 5（:387-388） | **連續達標** 0 週（最長 3 週） | 每週 ≥ 80% 的連續週數 | 0 最刺眼；「最長 3 週」提醒你曾經更好 | streak 有效但要能容錯（§3 Duolingo） |
| 6 | 目前周期（:398-407） | 這期完成率、還剩幾堂、TSS 已完成／整期 | 周期進度 | 又一個完成率 | 周期時間進度條（第 12 / 42 天）本身是中性的，留 |
| 7 | 每週完成率折線（`renderTrend`） | 綠點／橘點、80% 虛線 | 每週有沒有達標 | 虛線＝及格線；橘點＝不及格 | 改成「每週完成堂數」柱狀就沒有及格線 |
| 8 | 依課表類型（`renderKinds`，:566-574） | 每類一條四色堆疊條＋完成率 | 哪種課最常沒做 | 紅段＋「沒做」圖例 | 教練視角要；可收合 |
| 9 | 每堂課列表（:579-595） | 狀態 icon ✓／◐／≠／✗、「沒做」篩選、新的在上面 | 每堂課的結果 | ✗ 與「沒做」「沒照課表」字眼；**`.st-missed` 用 `--bad` 紅字**；篩選器把「沒做 3」放在面前 | 需要，但字眼與顏色可中性 |
| 10 | 每週存檔（`plan_history`） | 「完成 5/6 堂（照課表 4）」 | 歷史 | 分數形式 | 留，歷史紀錄本來就是中性的 |
| 11 | 文案（`compliance.json`） | `status.missed`「沒做」、`status.off_plan`「沒照課表」、`kpi.ok`「達標率」、`trend.tip`「橘點 = 沒達到」、`kinds.tip`「哪種課最常沒做或做走樣」 | — | 「沒做」「沒照課表」「走樣」「沒達到」都是否定句，主詞是你 | 改中性字眼（§6 文案表） |
| 12 | 課表頁 chip（`schedule.html` :253-262） | 完成度底色綠黃紅、✓／◐／≠、`.st-missed` 劃線＋紅字「未完成」 | 每堂課結果 | 紅色＋劃線＋紅字三重 | SP-375 已改成色條＋圓圈（§7 討論） |
| 13 | 課表頁週統計（:1166） | 「完成度 83%」cp 色 | 週負荷比 | 紅色週 | 改成「完成 TSS 310」就沒有顏色 |
| 14 | 總覽本週清單（`overview.html` :646-664） | ✓ 角標換色、≠／◐ 前綴、`.missed` 55% 透明＋劃線、非綠才顯示 %（:663） | 本週每堂結果 | 「只有做不好的才顯示 %」＝只標壞的 | SP-375 一起改 |
| 15 | 自動調整（`adapt.py` 規則 E `_red_streak`） | 「連續 2 堂偏離計畫（紅色）」的 ↻ 原因文字 | 系統為什麼動課表 | 「偏離」又是否定句 | 給演算法用的保留；給人看的文字改成「依最近兩堂的狀況調整」 |

**結構性的根：統計頁的五個 KPI 有四個是「÷ 計畫」**，計畫是分母，沒做就往下掉。這跟 TrainingPeaks／intervals.icu 的「compliance」世界觀一致（它們是給教練看的），但使用者要的是跑者視角。

## 2. 焦慮從哪來（有出處）

| 機制 | 說什麼 | 對本案的意涵 | 出處 |
|---|---|---|---|
| 自我決定論：勝任感（competence） | 內在動機靠三個需求：自主、勝任、關聯。回饋若讓人感到「不夠好」會削弱勝任感 | 主數字要讓人看到「我做到了什麼」，不是「我離目標還差多少」 | Deci & Ryan, SDT；[Deci, Koestner & Ryan 1999 meta-analysis](https://depts.washington.edu/techdocs/papers/deciExtrinsicRewardsAndIntrinsicMotivation99.pdf) |
| 損失規避 | 失去的痛 > 得到的樂（約 2 倍） | 「完成率從 85% 掉到 72%」是損失框架；「這週累積 6.2 h」只有得到 | Kahneman & Tversky 1979 Prospect Theory；Duolingo 的 streak 就是用這個（[Duolingo blog](https://blog.duolingo.com/how-duolingo-streak-builds-habit)） |
| What-the-hell effect（破功效應） | 目標一旦被違反且被編碼成「失敗」，後續表現比沒目標還差 | 一堂沒做就「這週算了」。畫面不要把一堂沒做變成整週的失敗（週完成率、streak 歸零都會） | [Cochran & Tesser 1996](https://www.researchgate.net/publication/251808020_When_Goals_Are_Counterproductive_The_Effects_of_Violation_of_a_Behavioral_Goal_on_Subsequent_Performance)；Polivy & Herman 的飲食研究 |
| 給一點「鬆」反而更能堅持 | 目標附帶緊急備用（emergency reserve）的人，失敗後更願意繼續 | streak 要有免死金牌；休息日、生病不算斷 | [Sharif & Shu 2019, OBHDP](https://anderson-review.ucla.edu/wp-content/uploads/2021/03/Sharif-Shu_EmergencyReserveFailure_OBHDP2019.pdf)；Duolingo 兩個 streak freeze 讓 DAU +0.38%（[Duolingo blog](https://blog.duolingo.com/how-duolingo-streak-builds-habit)） |
| 新起點效應（fresh start） | 時間的「新起點」（週一、月初）讓人更願意重新開始 | 週為單位的統計天然給每週一個新起點；不要跨週累計失敗 | [Dai, Milkman & Riis 2015](https://doi.org/10.1177/0956797615605818) |
| 運動員自我慈悲 | 自我慈悲高的運動員 fear of failure、羞愧、反芻較低；介入可以教 | 文案用「常見情況」（common humanity）語氣：「這很常見」「課表已經調好」 | [Mosewich et al. 2011](https://pubmed.ncbi.nlm.nih.gov/21451173/)；[Mosewich 2013 介入研究](https://self-compassion.org/wp-content/uploads/2015/12/Mosewich_intervention.pdf) |
| 外在獎賞削弱內在動機 | 預期的、有形的、依完成給的獎賞使自由選擇下的內在動機下降（d ≈ −0.3～−0.4）；**非預期的、口頭的正向回饋反而提高** | 不做「做完給幣」；做「做完時一句具體的肯定」（非預期、資訊性） | [Deci, Koestner & Ryan 1999](https://depts.washington.edu/techdocs/papers/deciExtrinsicRewardsAndIntrinsicMotivation99.pdf) |
| 社群比較的壓力 | Strava 使用者的主要壓力來源是比較與自我呈現，部分人因此避用 | 本 app 不做社群比較；也不要跟「計畫中的自己」比成輸贏（推估） | [Russell et al. 2023, Strava 研究](https://journals.sagepub.com/doi/10.1177/15588661221148170) |
| 光學心率誤差（本 repo） | 手腕光學心率在間歇／越野時偏差大，TSS 可能被低估 | 「部分」可能是測量誤差，不是人沒做——不確定時不該扣分 | `docs/research/optical-hr-quality.md`（SP-200）；`engine/hr_quality.py` |

## 3. 參考產品

| 產品 | 怎麼獎賞達成 | 怎麼處理沒達成 | 被批評的地方 | 出處 |
|---|---|---|---|---|
| Apple 健身圓環 | 三個圓環**只往上填**，合上就有動畫＋徽章；月挑戰 | watchOS 11：可**暫停圓環**最多 90 天不斷徽章連續、**每天目標可依星期不同**（週末設低） | 加上這兩個功能之前，被批評「生病也要合圓環」、不能休息 | [Apple Support](https://support.apple.com/guide/watch/adjust-your-activity-ring-goals-apd29b30023c/watchos)、[MacRumors](https://forums.macrumors.com/threads/watchos-11-lets-you-schedule-activity-goals-for-each-day-of-the-week.2429523/) |
| Duolingo | streak 數字、里程碑動畫 | **Streak freeze**（最多 2 個）、週末免修；斷掉可用寶石修 | streak 焦慮本身就是被拿來賣的（Sylvi 案例） | [Duolingo blog](https://blog.duolingo.com/how-duolingo-streak-builds-habit)、[trophy.so](https://trophy.so/blog/the-psychology-of-streaks-how-sylvi-weaponized-duolingos-best-feature-against-them) |
| Finch | 小鳥成長、能量；完成目標給「能量」 | **沒做什麼都不會發生**：沒有 streak 懲罰、沒有排行 | 偏可愛風，給成人運動員不一定合 | [Finch review](https://www.cltcounseling.com/all-resources/finch-habit-tracker-app-review) |
| Runna | 完成課的勾、週回顧 | 漏 >3 堂或一週會跳出 **Plan Realignment**；「Not Feeling 100%」工具讓你說原因、課表自動調；文件明說「plan 本來就預期你不會每堂都做」 | — | [Runna 支援](https://support.runna.com/en/articles/15012850-how-and-when-to-skip-a-run-managing-missed-sessions-in-your-training-plan)、[Tom's Guide](https://www.tomsguide.com/wellness/fitness/im-deep-into-marathon-training-fighting-the-flu-and-runnas-new-not-feeling-100-percent-tool-couldnt-be-more-timely) |
| TrainingPeaks | 完成的課上色（綠＝照計畫） | **紅＝沒做**、橘＝差很多；可整個關掉 compliance 顏色 | 教練工具；跑者端常見「整週紅」的抱怨（推估） | [TP help](https://help.trainingpeaks.com/hc/en-us/articles/204861204) |
| intervals.icu | compliance % 欄位 | 另一條色帶、不蓋卡片；沒做的課留灰 | — | [intervals forum](https://forum.intervals.icu/t/compliance-activity-field/47805) |
| Strava | 徽章、PR、kudos、月挑戰 | 沒有「沒達成」概念（沒有課表） | 社群比較造成壓力（Russell 2023） | [Russell et al. 2023](https://journals.sagepub.com/doi/10.1177/15588661221148170) |
| Garmin Connect | 徽章（累積里程、連續週）、挑戰 | 課表沒做只是灰掉 | 徽章太多顯得廉價（推估） | — |
| Nike Run Club | 里程碑、streak、trophy、語音鼓勵 | 無懲罰 | — | — |

共同模式：**好的設計把「獎賞」做成只往上加的量，把「沒做」做成無事發生或系統代勞（Runna 自動調、Apple 暫停）。** 本 app 已經有 Runna 那一塊（`adapt.py` 自動調整＋ ↻ 標記），缺的是把它講成安心訊息、以及把統計數字改成累積量。

## 4. 設計原則

1. **主數字只往上加**：時間、爬升、距離、完成堂數、連續有練的週數。沒有分母、沒有及格線。
2. **「沒做」不是視覺元素**：不紅、不 ✗、不劃線、不排前面；它只存在於「課表已調整」的安心訊息，以及收合區的教練數字。
3. **獎賞＝具體、非預期、資訊性**：「這週最長的一跑 2:40」「爬升比上週多 300 m」「連續 4 週有練」；不做幣、不做排行。
4. **容錯是預設**：連續紀錄以週為單位、休息日不算、暫停不斷。
5. **不確定就不扣**：光學心率品質差的課，完成度不往下判（最多「完成」或「做了」），並在 hover 說「心率品質不穩，依時間判定」。
6. **教練視角收起來**：原始完成率、達標率、依類型堆疊條，收進「進階」收合區（記住開關），第一屏看不到。
7. 承 SP-375：一個維度一個通道；承 SP-370：判定寬鬆。

## 5. 三個方案

| | A 溫和版 | **B 本週累積（推薦）** | C 圓環 |
|---|---|---|---|
| 第一屏主數字 | 保留五個 KPI，改字眼、去紅色、分母變小字 | **四個累積數字**：完成堂數、時間、爬升、連續有練週；一條「這週亮點」 | 一個圓環：本週累積 TSS 填滿到計畫（**只畫到 100%、不畫缺口的顏色**），外圈一顆顆完成的課 |
| 沒達成的呈現 | 「沒做」改「跳過」、灰色 | **不出現**；只有一行「課表已依狀況調整 ↻（看哪幾堂）」 | 圓環沒填滿的部分是底色，沒字 |
| 獎賞 | 無新增 | 「這週亮點」卡：最長／最高／PR／連續週，一次一條 | 合環動畫＋亮點卡 |
| 原始達成率 | 還在第一屏 | 收合區「教練視角」 | 收合區 |
| 連續紀錄 | 連續達標（≥80%）改成「連續有練」 | **連續有練週**（每週 ≥1 堂，休息週／暫停不算斷） | 同 B |
| 改動量 | 小（文案＋CSS） | 中（新 KPI 計算：後端 `_count` 加累積欄位；前端重排） | 中大（圓環元件、動畫） |
| 焦慮消除 | 弱（分母還在） | **強** | 強，但圓環隱含「要合上」（Apple 的老問題） |
| 跟 SP-375 的一致性 | 高 | 高 | 高 |
| 手機 | OK | OK（2×2 格） | OK |

**推薦 B。** 理由：把「扣分」的結構（分母）拿掉而不是遮掉；改動集中在統計頁第一屏＋文案；C 的圓環好看但「合上」本身又是一個會沒達到的目標，Apple 花了十年才加暫停。

### 5.1 B 的第一屏

```
┌ 本週 ──────────────────────────────── ? ┐
│  完成 4 堂     6.2 h     ↑1,240 m   連續 5 週有練 │
│  ● ● ● ●  ○ ○                                  │   ← 做完的課一顆一顆（類型 icon），還沒到的是空圈
│  ✨ 這週最長的一跑：週日 LSD 2:40，比上週多 20 分  │
│  ↻ 課表已依這週的狀況調整（週三的間歇延到週五）   │   ← 只在有調整時出現
└──────────────────────────────────────────┘
期間：4 週 / 12 週 / 本周期            [▸ 教練視角]   ← 收合：完成率、達標率、依類型、每堂課列表
每週：完成堂數柱狀（沒有及格線）＋ 時數柱狀
```

- 「做完的課一顆一顆」：只畫 done（含 partial／off_plan，都是「做了」），missed **不畫**，還沒到的畫空圈；顆數＝本週排的課數。
- 「這週亮點」一次一條，規則排序：PR（distance／climb／長跑時間）> 連續週里程碑（4、8、12…）> 本週最長／最高 > 本週完成堂數。沒亮點就不畫，不湊。
- 「課表已調整」用 `plan_marks` 的 `auto` 資料（已有）；文字是安心句，細節點開。
- 教練視角收合區：現在的五個 KPI、依類型、每堂課列表原樣搬進去，只改文案（§6）。開關記在 `localStorage`。

### 5.2 數字來源（都已有或一行可加）

| 數字 | 來源 |
|---|---|
| 完成堂數 | `compliance.dashboard` `totals.completed`（done+partial+off_plan） |
| 時間、爬升、距離 | `totals.actual_hours` 已有；爬升／距離加到 `_count`（`done_by.climbing_m`／`distance_km`） |
| 連續有練週 | 新 `_active_streak`：每週 `completed ≥ 1`；週全是休息日／暫停（blackout 全週）不算斷 |
| 亮點 | `achievements.py`（已有 PR 機制）＋ 本週 max |
| 課表已調整 | `plan_marks` auto（SP-318） |

## 6. 文案表

| 現在（key） | 改成（zh-TW） | en | 說明 |
|---|---|---|---|
| 完成率 `kpi.rate` | 完成 {n} 堂 | {n} sessions done | 去分母；分母在 hover「本期排了 {due} 堂」 |
| 達標率 `kpi.ok` | 照計畫完成 {n} 堂 | {n} on plan | 教練視角內 |
| TSS 完成度 `kpi.tss` | 負荷 {a}（計畫 {p}） | Load {a} (planned {p}) | 進度條只畫到 100%，不畫目標線 |
| 連續達標 `kpi.streak` | 連續 {n} 週有練 | {n}-week streak | 門檻從 ≥80% 改成 ≥1 堂 |
| 沒做 `status.missed` | **跳過** | Skipped | 中性；hover「這天沒有對應的活動」 |
| 沒照課表 `status.off_plan` | **換了課** | Different session | 事實描述；hover 說換成什麼 |
| 部分 `status.partial` | 部分 | Partly | 留 |
| 完成 `status.done` | 完成 | Done | 留 |
| 待同步 `status.open` | 待同步 | Pending sync | 留 |
| `trend.tip`「橘點 = 沒達到」 | 每週完成幾堂 | Sessions done per week | 沒有及格線 |
| `kinds.tip`「哪種課最常沒做或做走樣」 | 每種課做了幾堂 | Sessions done by type | |
| 課表 chip「未完成」（SP-375 x 圈） | 跳過 | Skipped | §7 |
| adapt 規則 E 原因「連續 2 堂偏離計畫（紅色）」 | 依最近兩堂的狀況調整 | Adjusted after the last two sessions | 給人看的字；演算法名稱不變 |
| 新：安心句 | 課表已依這週的狀況調整，不用補 | Plan adjusted for this week — nothing to make up | 只在 auto mark 存在時 |
| 新：亮點 | 這週最長的一跑：{day} {title} {h}；比上週多 {d} | Longest run this week: … | |
| 新：心率不穩 | 心率品質不穩，依時間判定 | HR quality low — judged by time | hover |

## 7. 與 SP-375 的交集：要一起討論的點

SP-375 第二輪草稿（`schedule-chip-ui.md` §6–11）定了：四種狀態「完成／部分／沒照課表／未完成」、顏色綠／黃／紅／灰、圓圈 check／pie／slash／x、未完成劃線＋灰外框、off_plan 一律紅、紅色 pie（部分但偏 >50%）。SP-393 的方向會動到：

| # | SP-375 的決定 | SP-393 想改成 | 建議 |
|---|---|---|---|
| 1 | 「未完成」＝灰 **x 圈**＋標題**劃線** | x 與劃線都是「失敗」符號。改成**空圈＋灰字、不劃線**（跟「還沒做」只差卡片在過去） | **改**：過去的空圈＋灰字就夠區分；劃線拿掉 |
| 2 | 「沒照課表」＝**紅** slash 圈 | 紅色＝錯。改成**黃** slash（「換了課」是事實不是錯），紅色整個退場 | **改**：紅只留給「手錶推送失敗」這種系統錯誤；完成度不用紅 |
| 3 | 紅色 pie（部分但偏 >50%） | 同上，紅退場 → 黃 pie | **改**：pie 的填滿比例已經說了偏多少，不需要再用紅加強 |
| 4 | 文字「未完成」「沒照課表」 | 「跳過」「換了課」 | **改**（§6） |
| 5 | 左側色條顏色＝完成度（綠／黃／紅） | 綠／黃／（無紅）；跳過的卡沒有色條 | **改**：三色變兩色＋灰 |
| 6 | 統計頁 `.st-off_plan` 改 `--bad` | 不要 | **改**：`--watch` 維持 |
| 7 | `with_plan_check` off_plan → red（後端 level） | 後端 level 可以照 SP-375 改（演算法／教練視角用），**前端畫面不把 red 畫成紅色** | **分開**：level 是資料，顏色是前端；前端 red→`--watch` |
| 8 | 總覽本週清單「非綠才顯示 %」 | 全部不顯示 %（pie 已表達） | 一致 |
| 9 | 課表週統計「完成度 83%」cp 色 | 「完成 TSS 310」無色 | **改** |

**不衝突的**：類型 icon、外框 vs 色條、↻／✎ 徽章、手錶只在需處理時畫、圖例收 `?`。

## 8. 遷移計畫

每步一張功能單，`parent` 指 SP-393；①② 可以併進 SP-375 的實作單。

### ① 文案與顏色（最小改動，先上）

做：`compliance.json`／`schedule.json`／`overview.json` 依 §6 改字；`compliance.html` `.st-missed` 從 `--bad` 改 `--faint`、`.bg-missed` 改灰；每堂課列表預設篩選「全部」且 missed 排最後；adapt 規則 E 的 reason 文字改。
驗收：
- 👆 統計頁 → 任一期間 → 看不到「沒做」「沒照課表」「走樣」「沒達到」；跳過的列是灰字不是紅字。
- 👆 課表頁 → 一堂自動調整過的課 hover → 原因寫「依最近兩堂的狀況調整」。
- ✅ `test_i18n*` 過；`test_adapt.py` 的 reason 字串 assert 更新。

### ② 第一屏換成累積數字（方案 B）

做：後端 `compliance._count` 加 `distance_km`／`climb_m`；`_active_streak`（≥1 堂、休息週不算斷）；`dashboard()` 回 `highlight`（亮點規則 §5.1）。前端 `render()` 第一屏改 2×2 累積卡＋課顆列＋亮點＋安心句；原五 KPI、依類型、每堂課搬進 `<details id="coach">`，狀態記 `localStorage`。
驗收：
- ✅ `test_compliance_dashboard.py`：一週 6 堂做 4 → `completed=4`、`climb_m` 加總對；休息週夾在中間 streak 不斷；全沒做的週斷。
- 👆 統計頁第一屏：只有「完成 n 堂／h／↑m／連續 n 週」四格、課顆列、亮點一條；沒有任何 %。
- 👆 有自動調整的週 → 安心句出現，點開列出哪幾堂；沒有調整 → 不出現。
- 👆 「教練視角」收合打開 → 原來的 KPI 與列表都在，關掉後重新整理仍關著。
- 👆 手機寬度：四格 2×2，課顆列可換行；深色對比 OK。

### ③ 每週圖表去及格線

做：`renderTrend` 改成完成堂數柱狀（去 80% 虛線與綠橘點）；`compW`（課表頁週統計）改「完成 TSS {n}」無色；`kpi.tss` 進度條去黑線。
驗收：
- 👆 統計頁每週圖：柱狀、沒有虛線、沒有橘點。
- 👆 課表頁週統計欄：「完成 TSS 310」，不上色。

### ④ 不確定不扣分（光學心率）

做：`session_compliance` 讀 `done_by.hr_quality`（`engine/hr_quality.py` 已算），品質 `low` 時 TSS 偏差不往下判（只用時間），`comp.uncertain=True`；前端 hover 加「心率品質不穩，依時間判定」。
驗收：
- ✅ `test_compliance*.py`：hr_quality low、TSS 60%、時間 95% → level green、`uncertain` True。
- 👆 一堂光學心率亂跳的間歇 → 卡片是完成，hover 看到那句。

### ⑤ 亮點（可後）

做：`achievements.py` 接到 `dashboard.highlight`（PR > 連續週里程碑 > 本週最長／最高 > 完成堂數，一週一條，沒有就不畫）。里程碑 toast **不做**（使用者 2026-10-12）。
驗收：
- 👆 一週有 PR → 亮點卡寫那個 PR；沒有任何亮點的週 → 不出現亮點卡、不湊字。

## 9. Q1（2026-10-12 全部答完）

| # | 題 | 答 |
|---|---|---|
| 1 | 方案 | **B 本週累積** |
| 2 | SP-375 一起改（§7 #1–5、#9） | 同意；SP-375 文件 §6–11 已改成「紅退場、跳過不劃線、換了課」 |
| 3 | 字眼 | 「跳過」「換了課」 |
| 4 | 連續紀錄 | 連續有練週：每週 ≥1 堂、休息週／暫停不算斷 |
| 5 | 教練數字 | 留在「教練視角」收合區 |
| 6 | 亮點 | 一週一條，沒有就不畫 |
| 7 | 光學心率不穩 | 不往下判，hover 註明 |
| 8 | 里程碑 toast | **不做** |

另：自動調整規則 E 的「連續 2 堂紅」觸發拿掉（SP-375 Q2-8，見 `schedule-chip-ui.md` §9.2），本文 §1 #15 的 reason 文字因此不用改、直接消失。

## 10. 參考

- Deci, Koestner & Ryan 1999, *Psychological Bulletin* 125(6) — https://depts.washington.edu/techdocs/papers/deciExtrinsicRewardsAndIntrinsicMotivation99.pdf
- Cochran & Tesser 1996, When goals are counterproductive — https://www.researchgate.net/publication/251808020
- Sharif & Shu 2019, Nudging persistence after failure through emergency reserves, *OBHDP* — https://anderson-review.ucla.edu/wp-content/uploads/2021/03/Sharif-Shu_EmergencyReserveFailure_OBHDP2019.pdf
- Dai, Milkman & Riis 2015, Put your imperfections behind you, *Psychological Science* — https://doi.org/10.1177/0956797615605818
- Mosewich et al. 2011, Self-compassion: a potential resource for young women athletes — https://pubmed.ncbi.nlm.nih.gov/21451173/
- Mosewich et al. 2013, Applying self-compassion in sport: an intervention — https://self-compassion.org/wp-content/uploads/2015/12/Mosewich_intervention.pdf
- Russell, Potts & Nelson 2023, "If it's not on Strava it didn't happen" — https://journals.sagepub.com/doi/10.1177/15588661221148170
- Duolingo, How the streak builds habit — https://blog.duolingo.com/how-duolingo-streak-builds-habit
- Apple, Adjust your Activity ring goals / Pause rings — https://support.apple.com/guide/watch/adjust-your-activity-ring-goals-apd29b30023c/watchos
- Runna, Managing missed sessions — https://support.runna.com/en/articles/15012850-how-and-when-to-skip-a-run-managing-missed-sessions-in-your-training-plan
- TrainingPeaks compliance colours — https://help.trainingpeaks.com/hc/en-us/articles/204861204
- intervals.icu compliance — https://forum.intervals.icu/t/compliance-activity-field/47805
- repo：`docs/research/schedule-chip-ui.md`（SP-375）、`docs/research/optical-hr-quality.md`（SP-200）、`docs/research/competitor-charts.md`
