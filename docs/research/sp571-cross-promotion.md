# TrailRunCoach 與疊石 Cairn 互相推廣（SP-571）

> 調查日期：2026-10-14。只做調查與文案草稿，沒有改任何產品程式。
> 讀過的程式：TrailRunCoach `docs/sp571-cross-promo` 分支（基於 `0816a6a3`）；Cairn（repo `inReachLiveTrack`，`6d1e750`，唯讀）。
> 相關單：SP-538（Cairn 正式接口，含「沒有對方帳號時的開通小卡」草稿）、SP-329（SSO）、SP-328（金流）、SP-330（方案）、SP-389（賽事資料庫）、SP-124（免登入功能）、SP-570（計算機時間回寫 Cairn，另一份研究，這裡不重複）。
> 標記：**程式**＝讀 repo 確認；**出處**＝網頁原文，網址在文末；**推估**＝我的推論。

---

## 0. 結論

- **一句話：兩個產品互推，最有效的不是廣告，而是「在對方剛好需要的那一步，多一個按鈕」——同一個 Google 帳號一鍵開通、資料自動帶過去、可以關、一個畫面最多一張。**
- 兩邊的使用者是**同一群人的兩個時間點**：TrailRunCoach 管「出發前幾週到幾個月」（練、排課、算比賽與多日行程要走多久），Cairn 管「出發前幾天到下山」（行程時間軸、地圖、留守分享、揪團）。重疊的是**越野跑者跑百岳／多日縱走**和**常爬百岳、想練體能的山友**。
- 兩邊已經有接點（**程式**）：
  - TrailRunCoach → Cairn：計算機與賽季計畫可貼 Cairn 分享連結匯入多日行程（SP-537）；計算機天氣區有「在疊石 Cairn 看這條路線 ↗」連結（SP-553）。
  - Cairn → TrailRunCoach：**沒有任何入口**（Cairn repo 搜不到 TrailRunCoach 字樣）。
  - 帳號：兩邊都用 Google OIDC，`sub` 可以對應同一個人（SP-329 拍板）；TrailRunCoach 已有 `link:*` 跨專案 scope（`backend/membership.py`）。
- **Cairn 的商業化 PRD 明文不做「訓練計劃／完整飲食規劃」**（**程式**：`.claude/PRPs/prd/inreach-livetrack-commercialization.prd.md`「What We're NOT Building」）——這正是 TrailRunCoach 的強項，兩邊互補、不搶，互推不會自己打自己。
- **建議先做 3 件**（細節 §5）：
  1. **情境開通小卡（兩邊各一張，SP-538 那條）**：只在「多日行程／GPX」那一步出現、可關、同 Google 帳號一鍵開通、開通後資料直接帶過去。
  2. **Cairn 路線頁「用你的體能算這條路線」入口 → TrailRunCoach 計算機（GPX 帶入）**：Cairn 唯一缺的方向，也是 Cairn 使用者最可能想要 TrailRunCoach 的時刻。
  3. **兩邊各記 4 個事件，量「看到 → 按 → 開通 → 30 天後還在用」**：Cairn 已有現成的埋點管線，TrailRunCoach 要補最小的一張計數表。
- **先不做**：捆綁方案（兩邊金流都還沒上線，SP-328）、社群經營與聯合內容（人力成本，等有使用者）、賽事資料庫入口（SP-389 本身還在 Backlog）、Cairn 揪團報名者能力值（牽涉別人的資料與同意，第二波）。
- **最大的限制**：TrailRunCoach 跑在 NAS、有使用者上限與候補名單（**程式**：`WKO5COACH_MAX_USERS`、`store.waitlist()`），Cairn 在 Cloudflare 上沒有這個限制。**Cairn → TrailRunCoach 的導流量要可控**，額滿時小卡要改成「加入候補」，不能讓人按了才撞牆。

---

## 1. 兩邊的使用者與重疊

### 1.1 從程式看兩個產品實際有什麼

| | TrailRunCoach | 疊石 Cairn |
|---|---|---|
| 定位（自述） | 「給越野跑、百岳、路跑的訓練教練」（README） | 「登山人的百寶箱——路線分析、行程規劃，與留在山下的那個人」（`WelcomePage.svelte` 的 lede） |
| 核心功能 | 讀手錶資料（COROS／TP／FIT）→ 訓練負荷、自動排課推 COROS、間歇與心率飄移、**賽事計算機（GPX＋天氣＋體能 → 配速、完賽時間、補給）**、週期規劃、多日行程分日（SP-522／535／537） | GPX 路線管理、POI 與過夜點、高度圖（坡度／鋪面）、3D 地形、**行程規劃與時刻推估（Munter／Tobler、用走過的一趟校準腳程）**、筆記、離線 PWA、**留守分享（inReach 即時軌跡）**、**揪團報名（LINE）** |
| 使用頻率（推估） | 週期性、高頻：每次練完同步、每週看課表 | 事件型、低頻但很深：每趟出發前規劃、出發中留守分享、揪團那幾週 |
| 註冊 | Google 登入＋同意條款頁；有使用者上限，額滿進候補（SP-58） | Google 登入「即自動完成免費註冊」（WelcomePage）；有訪客示範路線 |
| 方案 | 免費／訂閱 NT$99 月、NT$890 年（SP-321、SP-330；金流未上線） | 訪客／免費／輕度 NT$30／重度 NT$300 掛牌（`plan-limits.ts`；Polar，金流未全面上線） |
| 刻意不做 | （沒有地圖分享、留守、揪團） | 「訓練計劃／完整飲食規劃」（商業化 PRD） |

### 1.2 誰在重疊

- **A. 跑百岳／多日縱走的越野跑者**：在 TrailRunCoach 練、算比賽；百岳行程規劃與留守分享是 Cairn。重疊最大，也是擁有者本人的樣子。
- **B. 想把體能練起來的百岳山友**：在 Cairn 排行程、揪團；「我跟得上嗎、要走多久」用 Cairn 的 Munter／Tobler 只能算一般人，TrailRunCoach 能用自己手錶的資料算。
- **C. 揪團主辦**：Cairn 的揪團報名者；商業化 PRD 寫過「FIT 檔分析腳程能力篩選」是痛點——這跟 TrailRunCoach 的 `link:participant_ability` scope 是同一件事（第二波，要報名者本人同意，SP-326／329）。
- **不重疊、不要推的人**：Cairn 的**留守家人**（拿分享連結看即時位置的人，`share-view.ts` 的匿名檢視）——他們不爬山，推訓練 app 是噪音；TrailRunCoach 的**純路跑者**——他們不需要地圖行程。兩邊的推廣都要把這兩群排除。

### 1.3 什麼時刻最需要另一邊

| 從哪邊來 | 時刻 | 他在想什麼 | 另一邊能給什麼 |
|---|---|---|---|
| TrailRunCoach | 賽季計畫加一場多日行程／百岳，或計算機上傳多日 GPX | 「每天走到哪、住哪」 | Cairn：時間軸、過夜點、3D 地形與鋪面 |
| TrailRunCoach | 計算機算完多日行程或山徑賽 | 「出發後家人怎麼知道我在哪」 | Cairn：留守分享、即時軌跡 |
| TrailRunCoach | 比賽／行程前一週 | 「天氣、路況」 | Cairn：即時天氣圖層、監視器（已有 SP-553 連結） |
| Cairn | 路線頁設好腳程、看時刻推估 | 「我這種體能真的要走這麼久嗎」 | TrailRunCoach：用自己手錶資料算的時間、配速、補給（SP-570 研究時間回寫） |
| Cairn | 揪團報名截止前／行程前幾週 | 「我體力夠嗎、還有幾週可以練」 | TrailRunCoach：以出發日為目標的課表 |
| Cairn | 下山後上傳走過的軌跡 | 「我這次表現如何」 | TrailRunCoach：活動分析（要 FIT；Cairn 的 GPX 沒有心率，**推估**只能當參考） |

**進來的方向（推估）**：Cairn 的入口比較寬（免費、免條款頁、有訪客示範、揪團與留守分享會把人帶進來），TrailRunCoach 的入口窄（要手錶資料、要同意條款、有人數上限）。所以實際流量多半是 **Cairn → TrailRunCoach**，但 TrailRunCoach 一次只能收少量人——這是設計時最要注意的不對稱。

---

## 2. 交叉推廣的做法與案例

（各案例細節與網址見文末出處；這裡只寫做法與能借什麼。）

**整體觀察**：大公司的姊妹產品幾乎都是「兩個 app 各自獨立＋一個合購方案＋單向帳號連結」，推廣入口放在「需要它的那一頁」，很少用插頁硬推。查不到任何一家公開「互推帶來多少開通」的數字。

### 2.1 Garmin：Connect ↔ Explore ↔ inReach、Connect+

- **主 app 管帳號與裝置，副 app 掛在後面**：inReach 要先在 Garmin Connect 配對，才能跟 Explore 配對［S1］。Explore 至今仍是規劃路線、航點的工具，可同步到裝置［S2］。
- **付費只加新東西，舊的免費功能不鎖**：Connect+（2025-03）官方原話「All existing features and data in Garmin Connect will remain free」，月 US$6.99／年 US$69.99、30 天試用；新增的包括 LiveTrack 開始時傳簡訊通知親友、個人化 LiveTrack 頁［S3］（今天複查）。
- 2026-07-22 Garmin 收購 TrainingPeaks 與 TrainHeroic［S4］（今天複查）——訓練平台與裝置／追蹤的組合正是這兩個產品的形狀，之後值得觀察它們怎麼互推。
- **可借**：Cairn 的「留守」與 TrailRunCoach 的「練與算」各自完整，連結只是加分；未來收費時照 Connect+ 的做法——**已經免費的連動不改成付費**。

### 2.2 Strava：Komoot 路線同步、Runna、The Breakaway、FATMAP

- **路線同步走第三方中樞**：Strava 與 Komoot 都透過 Garmin 的路線 API 推到錶上，使用者在 Garmin Connect 授權一次即可［S5］。
- **Runna 合購（2025 收購後）**：只在 Strava 賣、Runna 裡不賣；US$149.99／年；新使用者 30 天試用；買完收到 email 連結，到 Runna 按「Sync with Strava」解鎖［S6］（今天複查）。官方另一篇提醒：舊的 Runna 訂閱**不會自動取消，會重複扣款**；解除連結只停同步、不停訂閱［S7］。
- **The Breakaway（2025-05）**：功能併入 Strava 訂閱；Strava 說「連結 Strava 的 Breakaway 使用者，上傳量是一般騎士的 2 倍」［S8］（今天複查）。⚠️ 這是相關、不是因果——本來就勤的人才會去連結，量效果時要避開同樣的誤讀（§4）。
- **FATMAP（2024-10 關站）**：部分功能併入 Strava 付費版，使用者要在期限前自己搬資料［S9］。
- **可借**：
  - 推廣入口放在「設定 → 訓練計畫」這種**需要它的地方**，不彈窗。
  - 合購時**結帳只在一邊**，另一邊只做「連結解鎖」；事先處理重複扣款（SP-328 兩邊共用金流時要寫進驗收）。

### 2.3 TrainingPeaks 系列

- **TrainingPeaks Virtual（前 indieVelo，2024 收購）**：用 TrainingPeaks 帳號單一登入；試用期後併入 TrainingPeaks Premium［S10、S11］。
- **Final Surge 不是 Peaksware 的**（官網頁尾是 Final Surge LLC）；它跟 COROS 是合作夥伴（每晚推送未來 4 天課表）［S12］。單上「TrainingPeaks 與 Final Surge」的前提不成立。
- **可借**：「付費版附帶另一個產品」比兩邊各自收費簡單——捆綁時優先考慮「訂 A 送 B 的某一級」，不另做合購價。

### 2.4 Komoot × Bending Spoons（反面教材）

- 2025-03 被收購；收購前（2025-02-27）新使用者同步路線到錶改成收費，之後大裁員、改版漲價［S13、S14］。查不到它跟 Bending Spoons 其他產品互推的例子。
- **可借**：把原本免費的連動改成收費最傷信任。SP-537 的分享連結匯入是免費的，之後也應該保持免費。

### 2.5 其他同公司多產品

- **Outside+**：一份會員包含 Trailforks Pro、Gaia GPS Premium 與媒體內容，兩個 app 裡都能訂，也能單買其中一個［S15、S16］（子代理查到；今天複查兩頁都回 403，**未複查**）。
- **Polar**：Polar Beat 的紀錄進 Polar Flow，再由 Flow 統一轉給合作服務；Flow 一斷開，所有來源的同步一起停［S17］——一個中樞、一個開關。
- **COROS**：手機 app 與 Training Hub 共用同一個帳號裡的課表庫［S18］。
- **Adidas**：Adidas Training 2024-03-31 收掉、只留 Running［S19］——是合併不是互推。
- **可借**：Outside+ 的「兩邊都能買、也能只買一個」；Polar 的「一個開關控制整條資料流」對應到設定頁的「已連結的服務」。

### 2.6 台灣在地

- **運動筆記與健行筆記同屬 H2U 永悅健康**，有跨平台會員福利，但查不到兩個 app 帳號直接互通的說明［S20、S21］。
- **健行筆記（Hikingbook）**有「留守人」功能（出發前把行程分享給親友，逾時沒下山通知親友）［S22］；Cairn 商業化 PRD 也把它當競品（Hikingbook Pro 約 NT$149／月）。
- 越野賽報名平台分散，查不到平台與 app 互推的案例；「登山客」app 查不到資料。
- **可借**：台灣沒有現成的「訓練 × 登山行程」互推範例，兩邊連起來本身就是差異點；留守是 Cairn 的核心，TrailRunCoach 的長距離課與賽前頁可以提「出發後交給 Cairn 留守」。

### 2.7 七種做法對兩個產品的適用度

| 做法 | 用在這裡 | 成本 | 適合現在嗎 |
|---|---|---|---|
| 情境推薦（只在相關時刻出現、可關） | 多日行程／GPX 那一步、Cairn 路線頁的時刻推估旁 | S～M | ✅ 先做 |
| 共用帳號一鍵開通 | 同一個 Google `sub`（SP-329） | M（要 SSO 第一步） | ✅ 跟小卡一起做 |
| 互相嵌入 | Cairn 路線頁顯示 TrailRunCoach 算好的時間；TrailRunCoach 計算機嵌 Cairn 3D 地圖 | M～L | 部分：資料互傳走 SP-538／570，不做 iframe |
| 捆綁方案 | 訂一邊送另一邊的進階 | M（金流、方案） | ⏳ 等兩邊金流上線（SP-328） |
| 聯合內容 | 「某條百岳路線＋怎麼練」的文章、範例行程 | 人力 | ⏳ 有使用者後再做；可先做 2～3 篇給 SEO |
| 社群 | 越野跑社團、登山社團、揪團 | 人力 | ⏳ 擁有者本人在社群發文就是最便宜的一步 |
| 賽事資料庫當入口 | 每場賽事一頁：計算機＋在 Cairn 看路線 | M～L | ⏳ SP-389 還在 Backlog；做的時候兩個按鈕都放 |

---

## 3. 不讓人覺得煩的原則

### 3.1 外部依據

- **NN/g**：不要在使用者還沒拿到價值前、剛登入時、關鍵任務中跳窗；「a modal overlay is not the right approach for advertising your mobile app」，改用容易關掉的橫幅或非 modal 的提示，講清楚好處［S23］（今天複查）。一次只出一個［S24］。
- **Apple 審核規範**：3.2.1(i) 允許在 app 內推廣自己的其他 app，只要 app 本身不只是目錄；4.5.4 用推播做行銷要明確同意、app 內要能退出；5.1.1(v) 要能在 app 內撤銷與社群服務的連結與資料存取［S25］（今天複查原文）。兩邊現在是網頁／PWA，不受審核，但這是業界底線。
- **Google Play**：連到自家其他 app 的入口要看得出是推廣，不能偽裝成系統通知［S26、S27］。
- **Google 帳號連結文件**：用自訂 scope 只分享必要的資料、說清楚資料怎麼用；使用者要能連結也能解除［S28］（今天複查）。
- **台灣個資法第 20 條**：第 2 項「當事人表示拒絕接受行銷時，應即停止利用其個人資料行銷」；第 3 項「非公務機關於首次行銷時，應提供當事人表示拒絕接受行銷之方式」［S29］（今天複查原文）。產品內的情境卡算不算「行銷」有爭議（**推估**：寄 email 推另一個產品一定算），但照最嚴的做：**每張卡都有「不用了」，按了就停**。
- **GDPR**：同集團的公司之間沒有自動共用資料的特權，每次共用都要有自己的法律依據；同意不能當成使用服務的條件［S30、S31］（摘要）。兩個產品是同一個擁有者，但隱私權政策各自寫，**帳號對應與資料互傳要本人同意**——這跟 SP-326 的結論（分開勾選、不綁註冊）一致。

### 3.2 給這兩個產品的規則

1. **只在相關的那一步出現**：TrailRunCoach 只在多日行程／GPX 區；Cairn 只在自己的路線頁的時刻推估旁。總覽、課表、活動、傷病、地圖主畫面、分享檢視都不放。
2. **一個畫面最多一張、不是彈窗**：內嵌卡片，不擋操作；不在登入後第一個畫面出現。
3. **「不用了」永久有效**：記在帳號上（不是瀏覽器），換裝置也記得；設定頁可以重新打開。不做「90 天後再問」。
4. **尊重提示分級**：TrailRunCoach 設成「精簡」的人不顯示（SP-482 的分級；介紹不是會改變做法的提示）。
5. **有帳號就不推廣**：知道對方有帳號（SSO 後）就只剩互連，不再出現介紹。
6. **不寄推廣信、不用推播**：至少第一版不做；要做就照個資法第 20 條，首次就附退訂。
7. **帳號連結要本人按同意**：同一個 Google 帳號**不自動連結**。第一次要拉／推資料時問一次「允許 TrailRunCoach 讀取你在 Cairn 的行程（反之亦然）」，寫進 `consents`（TrailRunCoach 已有 `action` 欄）；設定頁「已連結的服務」可以解除，解除後立刻停止互傳。
8. **不把對方的人當成自己的**：Cairn 的留守家人、揪團報名者（分享檢視、LIFF 匿名報名）一律不推 TrailRunCoach；報名者能力值是第二波，要報名者本人同意。
9. **免費的連動保持免費**：SP-537 分享連結匯入、SP-553 天氣連結不因為收費而鎖起來（2.4 的教訓）。
10. **導流量要配合 TrailRunCoach 的容量**：額滿時明講候補，不讓人按了才撞牆。

---

## 4. 怎麼量效果

### 4.1 要回答的三個問題

1. **轉換**：看到小卡的人裡，有多少按了、多少開通了另一邊？（漏斗：曝光 → 點擊 → 開通 → 第一次用到）
2. **啟用**：開通之後有沒有真的用到那個功能？（TrailRunCoach：算了一次計算機；Cairn：建了一條行程或分享了一次）
3. **留存**：從另一邊來的人，30 天、90 天後還在用的比例，跟自己進來的人比——**這才是互推值不值得做的答案**。

### 4.2 兩邊現在有什麼紀錄（程式）

- **Cairn 已經有現成的埋點**：`app/src/lib/telemetry.ts` → `POST /api/events` → D1 `usage_daily`（使用者 × 功能 × 日）。值域是 `shared/src/schema.ts` 的 `FEATURE_IDS`，已有 `pricing`、`checkout`、`tour-*-start/done` 這類「漏斗」用的 id。限制：
  - 只記功能 id、**沒有 payload**，同一 id 30 分鐘內去重 → 「曝光／點擊／關掉」要各開一個 id（同 `tour-*` 每章兩把的做法）。
  - 匿名使用者記在全站一格 `anon`，量不到個人漏斗，但量得到總數。
- **TrailRunCoach 沒有產品事件紀錄**：
  - `accounts.db` 的 `usage`／`usage_items` 只記免費層配額（計算機次數、圖表次數）。
  - `perf.log` 每個 API 請求一行（路由樣板、狀態、秒數），可以數「某個路由被打幾次」，但不分使用者、保留期短，不適合當漏斗。
  - `users` 表有建立時間；`consents` 記每次同意條款。
- **兩邊都沒有的**：「這個帳號是從另一邊來的」——要在開通時記來源。

### 4.3 最小的做法

- **事件 id（兩邊同名）**：`xp-card-shown`（小卡出現）、`xp-card-click`（按開通／前往）、`xp-card-dismiss`（按不用了）、`xp-linked`（帳號對應完成）。
  - Cairn：加進 `FEATURE_IDS`，前端 `track()` 一行。
  - TrailRunCoach：新增一張 `events_daily(user_id, event, day, n)` 小表（比照 Cairn 的形狀），或先直接在 `usage` 表借用 key（`period` 用日期）。**推估**：借 `usage` 最省事，但它語意是配額，混用會讓 SP-330 的計數難讀，建議另開小表。
- **來源標記**：開通時帶 `?from=cairn`／`?from=trc`，新帳號寫一欄 `signup_source`（兩邊各一欄）。這樣「從另一邊來的人」的留存可以直接用兩邊既有的登入／活動紀錄算。
- **儀表板**：先不做畫面，每月跑一支查詢就好：曝光、點擊率、開通數、開通者 30 天後是否還有活動（TrailRunCoach：有新同步的活動或算過計算機；Cairn：`usage_daily` 有任何功能事件）。
- **業界的說法**：附加率（attach rate，用 A 的人裡也用 B 的比例）沒有通用標準，只能跟自己過去比［S32］；合購對流失有幫助的公開數字只有串流業（Ampere：Disney 合購 12 個月流失 29%、單買 Disney+ 70%）［S33］，運動 app 查不到（Strava／Runna 沒公布）。
- **別誤讀「連結的人比較活躍」**：Strava 說連結 Breakaway 的人上傳量是 2 倍［S8］，但那是本來就勤的人才會去連結。比較留存時要拿「同樣在多日行程那一步、看到小卡但沒開通的人」當對照，不是拿全部使用者。
- **門檻（推估，小樣本）**：每月開通 < 3 人就先別再加入口，改看文案與位置；點擊率高、開通率低 → 開通流程有問題（多半是 TrailRunCoach 的條款頁或額滿）。

---

## 5. 建議先做的 3 件

排序原則：先做**不用等 SSO、成本最小、量得到**的；做完一個月看數字，再決定要不要往捆綁、內容、賽事資料庫走。

### 5.1 情境開通小卡（兩邊各一張；SP-538 那一條拆出來先做）

- **做什麼**：只在「多日行程／GPX」那一步出現一張小卡，介紹另一邊在這一刻能幫什麼，按一下用同一個 Google 帳號開通，按「不用了」就記住不再出。文案見 §6。
- **分兩段**：
  - **現在（SSO 前）**：TrailRunCoach 不知道對方有沒有 Cairn 帳號，小卡對所有符合情境的人顯示，按鈕是「打開疊石 Cairn ↗」（Cairn 用 Google 登入就自動免費註冊，**程式**：WelcomePage 的 login-note）。成本最小。
  - **SSO 第一步之後（SP-329）**：兩邊用內部 API 問「這個 Google `sub` 在你那邊有沒有帳號、有沒有連結」，已經有帳號的人小卡換成互相連結（「在 Cairn 打開這趟 ↗」「推到 Cairn」，SP-538），不再推廣。
- **為什麼先做**：兩邊都已經有「多日行程」這個交會點（SP-537 匯入、SP-553 連結），只差一張卡；成本 S；完全不影響主要功能。
- **工作量（推估）**：TrailRunCoach 0.5～1 天（兩個位置、「不用了」記住、設定頁重開）；Cairn 0.5～1 天（一個位置、記住關閉）。SSO 後切換成互連：兩邊各再 0.5 天（不含 SP-329 本身）。

### 5.2 Cairn 路線頁「用你的體能算這條路線」→ TrailRunCoach 計算機

- **做什麼**：Cairn 路線頁、時刻推估（腳程設定）旁一個入口；按下去開 TrailRunCoach 計算機，**路線已經帶入**。
  - **現在就能做**：TrailRunCoach 已經能用 Cairn 分享連結匯入（`POST /api/v1/racepower/course/cairn`，**程式**）。Cairn 只要把分享連結帶在網址上（例如計算機網址加 `?cairn=<分享連結>`），TrailRunCoach 計算機讀到就自動匯入。路線還沒有分享連結時，Cairn 先問一句「要產生分享連結才能帶過去」——**不默默替使用者開分享**（分享連結等於把路線公開給拿到網址的人）。
  - **SSO 之後**：改走 SP-538 的內部 API（用 `sub` 列行程），不需要分享連結。
- **沒有 TrailRunCoach 帳號的人**：走 TrailRunCoach 的登入＋條款頁，完成後**回到計算機、路線照樣帶入**（`next` 參數，**程式**：`login.html` 已支援）；額滿時進候補並說清楚（§6.2）。
- **不建議給「免登入算一次」**（要拍板，§7 Q1）：Cairn 已經用 Munter／Tobler 給了一個一般人的時間，TrailRunCoach 沒有使用者的手錶資料時算出來的也是一般人的時間，**沒有多出價值**，卻吃 NAS 算力（NAS 比本機慢 8～9.5 倍），也違反 SP-124「沒登入的人不觸發伺服器計算」的決定。
- **為什麼先做**：這是現在**唯一完全缺**的方向（Cairn → TrailRunCoach 沒有入口），而 §1.3 推估流量主要在這個方向。
- **工作量（推估）**：Cairn 0.5～1 天；TrailRunCoach 0.5 天（讀網址參數、登入後接著匯入、額滿說明）。

### 5.3 兩邊各記 4 個事件＋註冊來源

- **做什麼**：§4.3 的 `xp-card-shown`／`xp-card-click`／`xp-card-dismiss`／`xp-linked`，加上新帳號的 `signup_source`（`cairn`／`trc`／空白）。每月跑一次查詢看漏斗與 30 天留存。
- **為什麼先做**：沒有數字就無法判斷 5.1、5.2 值不值得繼續加碼；Cairn 已有管線、成本極低。
- **工作量（推估）**：Cairn 0.25～0.5 天（`FEATURE_IDS` 加 4 個、`users` 加一欄）；TrailRunCoach 1 天（一張小表、記錄函式、`users` 加一欄、一支查詢腳本）。

### 5.4 順手做（XS，不算在 3 件裡）

- 兩邊的「關於」與操作手冊各加一段介紹對方（Cairn：`AboutSheet.svelte`；TrailRunCoach：`manual.html`）。這是**使用者主動查閱**的位置，不打擾。Cairn 慣例要求改功能時同步檢查關於頁與新手導覽（Cairn `CLAUDE.md` 第 102 行附近）。
- 擁有者在越野跑社團、登山社團發一篇「我怎麼用這兩個工具準備一趟百岳」，連到兩邊。不寫程式，最便宜。

### 5.5 建議開的單

**TrailRunCoach**

| 標題 | 做什麼 | 要達成什麼 | 大小 |
|---|---|---|---|
| Cairn 開通小卡（計算機與賽季計畫的多日行程區） | 多日 GPX／行程那一步顯示小卡，「不用了」記住，設定頁可重開；SSO 前是「打開 Cairn」，SSO 後換成互連 | 需要排行程與留守分享的人知道有 Cairn，不打擾其他人 | S（0.5～1 天） |
| 計算機接收 Cairn 帶來的路線 | 網址帶 Cairn 分享連結就自動匯入；沒登入走登入＋條款後回來接著匯入；額滿說明候補 | Cairn 使用者一按就看到自己這條路線，不用手動貼連結 | S（0.5 天） |
| 跨產品事件與註冊來源 | 一張事件計數小表、4 個事件、`users.signup_source`、每月漏斗與留存查詢 | 量得到互推帶來多少人、留不留得住 | S（1 天） |
| 已連結的服務（設定頁） | 顯示「已連結疊石 Cairn」、連結時間、可以解除；連結那一刻寫進 `consents`（`action = 'link_cairn'`） | 帳號對應有本人同意、可以撤回 | S（0.5～1 天；併 SP-329／538 也可以） |
| 手冊加「疊石 Cairn」一節 | 手冊與關於頁介紹 Cairn 能做什麼、怎麼帶路線過去 | 主動查閱的人找得到 | XS |

**疊石 Cairn**

| 標題 | 做什麼 | 要達成什麼 | 大小 |
|---|---|---|---|
| 路線頁「用你的體能算這條路線」小卡 | 時刻推估旁的入口，帶路線到 TrailRunCoach 計算機；沒有分享連結時先問；可關；分享檢視與留守家人看不到 | 想知道「我自己要走多久」的人有路可走 | S（0.5～1 天） |
| 跨產品事件與註冊來源 | `FEATURE_IDS` 加 4 個 `xp-*`、`users.signup_source` | 同上，Cairn 那半邊的漏斗 | XS（0.25～0.5 天） |
| 帳號狀態內部 API（併 SP-329） | 給 TrailRunCoach 問「這個 `sub` 有沒有 Cairn 帳號、有沒有同意連結」 | 有帳號的人不再看到推廣，只看到互連 | S（0.5 天） |
| TrailRunCoach 帶來的新使用者落地 | `?from=trc` 登入後直接打開那趟（SP-538 推過去的行程），第一次顯示一行「這趟從 TrailRunCoach 帶過來」 | 開通後第一眼就看到自己的東西，不是空白首頁 | S（0.5 天；併 SP-538 推的那半） |
| 關於頁與新手導覽提 TrailRunCoach | `AboutSheet` 一段＋導覽最後一章一句 | 主動查閱的人找得到 | XS |

**不開（第二波，寫在這裡備查）**：捆綁方案（等 SP-328 兩邊金流）；揪團主辦看報名者能力值（要報名者同意，SP-326／329）；賽事資料庫的兩邊按鈕（隨 SP-389）；聯合內容文章（有使用者後）。

---

## 6. 沒有對方帳號時，畫面怎麼顯示（文案草稿）

> 規則：**一個畫面最多一張**；只出現在下面列的位置；有「不用了」（記住，設定頁可再打開）；兩邊都有帳號時只剩互相連結，不推廣。文案不寫個人名字與網域。

### 6.1 TrailRunCoach 使用者沒有 Cairn 帳號

**位置 1：賽季計畫 → 新增／編輯賽事 → GPX 區**（只在多日行程：天數 ≥ 2，或附了 GPX 且勾了分日）。放在「或貼疊石 Cairn 分享連結」那一列下面。

> **把這趟排進疊石 Cairn**
> 每天走到哪、住哪裡排成時間軸，看 3D 地形與鋪面；出發後把即時位置分享給家人。用同一個 Google 帳號就能開通，免費。
> 〔用 Google 帳號開通 Cairn〕　〔不用了〕
> <small>開通後會先問你要不要讓兩邊互傳這趟的路線與過夜點；之後可以在設定解除。</small>

**位置 2：賽事計算機 → 分日預覽算完之後**（同樣只在多日）。比位置 1 短：

> 要排每天的時間軸、分享即時位置給家人？〔在疊石 Cairn 開這趟（免費）〕　〔不用了〕

- **SSO 前的版本**：按鈕改成「打開疊石 Cairn ↗」，小字改成「Cairn 用 Google 帳號登入就自動免費註冊」。
- **按了之後**：SSO 前 → 新分頁開 Cairn 首頁；SSO 後 → 開 Cairn 的 Google 登入，完成後回到原頁，SP-538 的行程下拉選單有東西、「推到 Cairn」可以按。
- **「不用了」**：兩個位置一起記住（同一個關閉），設定 →「提示多寡」下面多一行「疊石 Cairn 的介紹：已關閉〔重新顯示〕」。設成「精簡」的人不顯示（比照 SP-482 提示分級：這是介紹，不是會改變做法的提示）。
- **不放的位置**：總覽、課表、活動分析、傷病（跟行程無關，放了就是廣告）。
- 計算機天氣區既有的「在疊石 Cairn 看這條路線的行程與即時天氣圖層 ↗」（SP-553）保留，不算推廣卡。

### 6.2 Cairn 使用者沒有 TrailRunCoach 帳號

**位置：自己的路線頁 → 行程規劃（腳程設定／時刻推估）旁**。只在登入者看自己的路線時；**分享檢視（留守家人、揪團報名者）看不到**，訪客示範路線第一版也不放。

> **這是一般人的腳程**
> 用 TrailRunCoach 讀你手錶的紀錄，算你自己走這條要多久、爬升段怎麼配速、每天要吃多少。
> 〔用 TrailRunCoach 算這條〕　〔不用了〕
> <small>用同一個 Google 帳號登入，第一次要同意 TrailRunCoach 的條款。需要手錶紀錄（COROS、TrainingPeaks 或 FIT 檔）才算得比這裡準。</small>

- **按了之後**：新分頁開 TrailRunCoach 計算機，路線帶入（§5.2）。沒登入 → 登入＋條款 → 回計算機接著匯入。
- **TrailRunCoach 落地的第一句**（已有 SP-537 文案「已從 Cairn 匯入『{name}』：{days} 天。」，新使用者再加一句）：
  > 還沒有手錶紀錄：先用你填的體能估；同步 COROS 或匯入 FIT 檔之後會更準。
- **TrailRunCoach 額滿時**：註冊頁已經會把人放進候補（SP-58）。落地頁要說清楚，不要讓人以為壞掉：
  > TrailRunCoach 目前人數已滿，你已經排進候補名單。這條路線在 Cairn 的時刻推估照樣可以用。
- **路線沒有分享連結時（SSO 前）**：按鈕先跳一句「要帶過去，需要先產生這條路線的分享連結（拿到連結的人看得到這條路線）。〔產生並前往〕〔取消〕」。
- **「不用了」**：記在帳號設定（不是 localStorage，換裝置也記得）；設定頁一行「TrailRunCoach 的介紹：已關閉〔重新顯示〕」。

### 6.3 兩邊都有帳號

- 不再有推廣卡，只剩互相連結：TrailRunCoach 那邊是 SP-538 的「從 Cairn 選行程」「推到 Cairn」「在 Cairn 打開這趟 ↗」；Cairn 那邊是一行小字連結「在 TrailRunCoach 算 ↗」，SP-570 做完之後換成「時間來自 TrailRunCoach（{日期}算的）」。
- **有帳號但還沒同意連結**：第一次要拉／推資料時才問一次（§3、§7 Q4），不在進頁面時跳。

---

## 7. 要拍板的問題

- **Q1 Cairn 使用者沒有 TrailRunCoach 帳號，按「用 TrailRunCoach 算這條」時給什麼？**
  - A. 先登入＋同意條款才算（同一個 Google 帳號；額滿進候補）
  - B. 免登入先算一次通用版（吃 NAS 算力，要限流）
  - C. 先導到示範跑者的結果
  - **建議 A**：Cairn 已經給了一般人的時間，B 算出來的也是一般人的時間，沒有多出價值；B 違反 SP-124「沒登入不觸發伺服器計算」，也和 SP-538 草稿「先給免登入能看的結果」互相矛盾——**選 A 的話要改 SP-538 那一句**。
- **Q2 開通小卡要不要在 SSO（SP-329）之前先上？**
  - A. 先上 SSO 前的版本（不知道對方有沒有帳號，符合情境的人都看到，可關）
  - B. 等 SSO 做完一次到位
  - **建議 A**：成本 0.5～1 天，先拿到數字；SSO 後只換按鈕與判斷。
- **Q3 TrailRunCoach 額滿時，Cairn 的小卡怎麼辦？**
  - A. 照樣顯示，按下去在 TrailRunCoach 進候補並說清楚
  - B. Cairn 問 TrailRunCoach「現在開放註冊嗎」，額滿就把按鈕改成「加入候補」
  - C. 額滿時 Cairn 不顯示
  - **建議 A**（現在流量小，成本最低）；每月開通數超過名額時再做 B（要一支公開、有快取的註冊狀態端點）。
- **Q4 同一個 Google 帳號，兩邊要不要自動連結？**
  - A. 自動連結，不問
  - B. 第一次要拉／推資料時問一次，設定頁可解除
  - C. 註冊時就打勾
  - **建議 B**：兩邊隱私權政策各自寫；SP-326 已經定了「同意不綁註冊、分開勾」；B 只在真的要用時多一個按鈕。
- **Q5 捆綁方案什麼時候談？**
  - A. 兩邊金流都上線、各有付費使用者之後再議
  - B. 現在就定（例如訂 TrailRunCoach 送 Cairn 輕度）
  - **建議 A**：SP-328 兩邊都還沒收到錢；先用 §4 的數字看重疊的人多不多。到時候照 Runna 的做法：結帳只在一邊、另一邊連結解鎖、防重複扣款。

（小卡「不用了」之後要不要再出現、要不要尊重 TrailRunCoach 的提示分級，我直接照 §3.2 建議寫了；不同意再說。）

---

## 出處

「今天複查」＝2026-10-14 我親自打開原文核對過；其他是同日子代理搜尋讀到的頁面，我沒有逐一再開。

- S1 Garmin Explore iOS Quick Reference Guide（inReach 先配對 Connect）：https://files.inreach.garmin.com/web/files/Explore_App_iOS_Quick_Reference_Guide.pdf
- S2 Garmin GPSMAP 86 說明（Explore 的路線與航點）：https://www8.garmin.com/manuals/webhelp/gpsmap86/EN-US/GUID-870D246F-E11E-4E62-A970-62EB9147A1DD.html
- S3 Garmin 新聞稿，Connect+（今天複查）：https://www.garmin.com/en-US/newsroom/press-release/wearables-health/elevate-your-health-and-fitness-goals-with-garmin-connect/
- S4 Garmin 新聞稿，收購 TrainingPeaks 與 TrainHeroic，2026-07-22（今天複查）：https://www.garmin.com/it-IT/newsroom/acquisizione-trainingpeaks-trainheroic/
- S5 the5krunner，Garmin 路線同步給 Strava 與 Komoot：https://the5krunner.com/2020/05/14/garmins-new-strava-route-sync-komoot-too/
- S6 Runna 說明，Strava + Runna 合購（今天複查）：https://support.runna.com/en/articles/11626438-strava-runna-subscription-guide
- S7 Runna 說明，連結帳號與合購：https://support.runna.com/en/articles/11668568-how-to-connect-your-strava-runna-accounts-with-the-joint-subscription
- S8 BikeRadar，Strava 收購 The Breakaway（今天複查）：https://www.bikeradar.com/news/strava-buys-the-breakaway
- S9 TechCrunch，Strava 關閉 FATMAP：https://techcrunch.com/2024/06/26/strava-to-shutter-3d-mapping-platform-fatmap-18-months-after-acquisition
- S10 Peaksware，TrainingPeaks 收購 indieVelo：https://peaksware.com/trainingpeaks-acquires-indievelo-launches-trainingpeaks-virtual/
- S11 DC Rainmaker，TrainingPeaks Virtual 併入訂閱：https://dcrainmaker.com/2025/02/trainingpeaks-announces-subscribers.html
- S12 Final Surge 部落格，同步課表到 COROS：https://blog.finalsurge.com/sync-planned-structured-workouts-to-coros/
- S13 DC Rainmaker，Komoot 被收購：https://dcrainmaker.com/2025/03/komoot-acquired-history-says-this-wont-end-well.html
- S14 BikeRadar，Komoot 2025 改版：https://www.bikeradar.com/news/komoot-redesign-2025
- S15 Trailforks，Outside+（今天 403，未複查）：https://www.trailforks.com/pro/outsideplus/
- S16 Outside 說明，會員方案（今天 403，未複查）：https://help.outsideonline.com/hc/en-us/articles/32337675985303-What-are-the-Outside-membership-options
- S17 Polar 說明，Flow 同步到 Strava：https://support.polar.com/en/support/how_can_i_automatically_sync_my_data_from_polar_flow_to_strava
- S18 COROS 說明，app 與 Training Hub 的課表：https://support.coros.com/hc/en-us/articles/38199938082836-Download-Workouts-and-Training-Plans-from-the-COROS-App
- S19 Wikipedia，Adidas Runtastic：https://en.wikipedia.org/wiki/Adidas_Runtastic
- S20 Yourator，運動筆記公司頁：https://www.yourator.co/companies/biji
- S21 運動筆記文章（H2U）：https://edh.tw/articles/N8PPQc5
- S22 App Store，健行筆記 Hikingbook：https://apps.apple.com/tw/app/hikingbook-%E6%8E%A2%E7%B4%A2%E5%B1%B1%E6%9E%97%E7%9A%84%E6%9C%80%E4%BD%B3%E5%9A%AE%E5%B0%8E/id1067838748
- S23 NN/g，Popups: 10 Problematic Trends and Alternatives（今天複查）：https://www.nngroup.com/articles/popups/
- S24 NN/g，Overlay Overload：https://www.nngroup.com/articles/overlay-overload/
- S25 Apple App Review Guidelines 3.2.1、4.5.4、5.1.1(v)（今天複查）：https://developer.apple.com/app-store/review/guidelines/
- S26 Android Developers Blog，常見政策問題：https://android-developers.googleblog.com/2020/10/developer-tips-and-guides-common-policy.html
- S27 Google Play 政策說明：https://support.google.com/googleplay/android-developer/answer/9857753
- S28 Google Account Linking（今天複查）：https://developers.google.com/identity/account-linking
- S29 全國法規資料庫，個人資料保護法第 20 條（今天複查）：https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=I0050021&flno=20
- S30 Heuking，集團內資料存取（GDPR）：https://www.heuking.de/en/news-events/newsletter-articles/detail/data-access-within-the-company-group-are-companies-permitted-to-access-the-it-systems-of-parent-sister-or-subsidiary-companies.html
- S31 Usercentrics，EDPB 同意指引摘要：https://usercentrics.com/knowledge-hub/edpb-guidelines-for-consent/
- S32 Ordway，產品附加率（attach rate）：https://ordwaylabs.com/blog/product-attach-rates-saas-companies/
- S33 NewscastStudio 引 Ampere，串流合購與流失：https://www.newscaststudio.com/2024/07/08/streaming-bundles-may-curb-cancel-and-resubscribe-behavior-ampere-notes

repo 內的依據（程式）：
- TrailRunCoach：`README.md`；`backend/membership.py`（`link:*` scope）；`backend/accounts/store.py`（`usage`、`consents`、`waitlist`）；`backend/applog.py`（perf.log）；`backend/engine/cairn.py`、`backend/api/racepower.py`（SP-537、SP-553）；`backend/static/{racepower,plan,login}.html`；`backend/static/hints.js`（SP-482 分級）。
- Cairn：`CLAUDE.md`；`.claude/PRPs/prd/inreach-livetrack-commercialization.prd.md`；`v2/app/src/ui/WelcomePage.svelte`；`v2/app/src/lib/{telemetry,pricing,plan-limits,share-view,recruit-cta,guest,pace-calibrate}.ts`；`v2/server/src/routes/events.ts`；`v2/shared/src/schema.ts`（`FEATURE_IDS`）。
