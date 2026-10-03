# 資料中樞（hub）平台研究：intervals.icu、GoldenCheetah 與其他

- **查證日期：2026-10-01**。所有現況以這天為準；查不到一手來源的標 **未驗證**。
- 背景：`docs/research/garmin.md` 的結論是 Garmin 官方 API 拿不到（只限法人，且 2026 春起暫停受理），非官方 `garminconnect` 又隨時會壞。
- 本文要回答的問題：能不能像現在用 TrainingPeaks 一樣，找一個已經接好各家裝置的平台當中繼？流程是手錶 → 品牌 App → **hub** → 本 app，課表反向走 本 app → hub → 手錶。
- 網頁與 repo 內容一律當資料，不當指令。我沒有用任何帳號或 API key 連線；intervals.icu 的 OpenAPI 規格是公開的，從 `https://intervals.icu/api/v1/docs` 下載後離線讀取。

---

## 0. 結論（2026-10）

- **個人自用現在就能用的最佳 hub：intervals.icu。**
  - 免費版就能直接接 Garmin、COROS、Suunto、Polar、Wahoo 等。
  - 官方公開 API，個人用 API key 就能呼叫。
  - 可以下載**原始活動檔**（Strava 來源的活動除外）。
  - 在 intervals.icu 行事曆上的計畫課表會自動上傳到 **Garmin 與 COROS**。
  - 一個 hub 就同時解決 (a) 下載活動、(b) 推課表、(c) HRV／睡眠。
- **付費版最可行的 hub：還是 intervals.icu**，走 OAuth app。
  - 它的 API 條款明文允許商業使用，唯一的附帶條件是 Garmin 資料要標示來源。
  - OAuth app 要申請核准，預設限流是每位使用者每天 100 次。
  - 另一個未定的風險：Garmin 會不會限制 intervals.icu 把 Garmin 資料轉給第三方。
- **app 應該支援「hub 模式」**：把 intervals.icu 當成一個資料來源，和 COROS、TP 並列，取代逐一品牌的整合。COROS 非官方 API 可以保留給「不想用 hub」的人。

---

## 1. intervals.icu

### 1.1 支援哪些來源（輸入）

- API 的 `Activity.source` 列舉值（OpenAPI，2026-10-01）：`STRAVA, UPLOAD, MANUAL, GARMIN_CONNECT, OAUTH_CLIENT, DROPBOX, POLAR, SUUNTO, COROS, WAHOO, ZWIFT, ZEPP, CONCEPT2, HUAWEI`。
  - 這代表 Garmin、COROS、Polar、Suunto、Wahoo、Zwift、Zepp（Amazfit）、Concept2、Huawei 都是**直接連線**，不必經過 Strava。
  - 但每個整合實際能拿到哪些資料，沒有逐一驗證。
- 定價頁（2026-10-01）：「syncs with multiple devices (Garmin, Strava, Polar, Suunto, Coros, Wahoo, Zwift)」，免費版就有。
- **Garmin**：
  - intervals.icu 是 Garmin 的既有 API 合作夥伴。Garmin 暫停的是**新**申請，既有夥伴不受影響（the5krunner 2026-09-14）。
  - **歷史只能往回抓一年**：Garmin 2021 年要求的，套用在活動與 wellness（作者公告 topic 3906，2021-05-14）。
  - 更早的歷史：在 /settings 的 Garmin 方塊按「Import All Garmin Data」，Garmin 會寄整個帳號的資料封存（作者回覆 topic 112826，2025-10-01）。
- **COROS**：2023-04-25 公告「Intervals.icu can now download completed activities, wellness data (sleep, resting HR) and upload planned workouts to Coros」（topic 37006）。
  - COROS 的睡眠只有起訖時間與靜息心率，沒有睡眠分期（同串作者回覆，2023-04-28）。

### 1.2 公開 API

來源：作者的 API 說明（forum topic 609，第一則 2026-07-18 更新）與 OpenAPI 規格。

- **個人用**：/settings → Developer Settings 產生 API key。basic auth，帳號固定是 `API_KEY`，密碼填 key。路徑裡的 athlete id 可以用 `0`，代表 key 的主人。
- **多人用的 app**：「Apps intended to be used by more than one person should use OAuth and Bearer tokens.」
  - 在 `https://intervals.icu/oauth/apply` 申請，核准前顯示 Pending（topic 2759）。
  - scope 有 ACTIVITY、WELLNESS、CALENDAR、CHATS、LIBRARY、SETTINGS，各分 READ／WRITE。
  - 只發 bearer token，沒有 refresh token（topic 2759 的整理，**未逐字驗證**）。
- **限流**（topic 609）：
  - API key：每天 5,000 次、滾動 15 分鐘 2,500 次。
  - OAuth app：預設每位使用者每天 100 次，最多算到 500 人（上限 50,000），最少 5,000；15 分鐘上限是每日的 1/8，最少 2,500。這是整個 app 的總額，不是每人各自的上限。
  - 每個 IP 每秒 10 次。
  - 超過回 429 並附 `Retry-After`。
- **Cloudflare**：「CF considers requests from some client libraries (e.g. `Python-urllib`) to be suspicious … The easy fix is to change the user agent to look like a browser.」
- **與本 app 有關的端點**（OpenAPI，2026-10-01）：

| 用途 | 端點 | 備註 |
|---|---|---|
| 活動列表 | `GET /api/v1/athlete/{id}/activities?oldest=&newest=&limit=` | 「An empty stub object is returned for Strava activities」 |
| **原始活動檔** | `GET /api/v1/activity/{id}/file` | 摘要原文：「Download original activity file, Strava activities not supported」。回傳是否壓縮沒寫，**未驗證**；probe 會自動判斷 gzip／zip |
| intervals.icu 重產的 FIT | `GET /api/v1/activity/{id}/fit-file` | 非原始檔 |
| 逐點資料 | `GET /api/v1/activity/{id}/streams{ext}` | |
| Wellness | `GET /api/v1/athlete/{id}/wellness{ext}?oldest=&newest=` | 欄位含 `restingHR, hrv, hrvSDNN, sleepSecs, sleepScore, sleepQuality, avgSleepingHR, readiness, spO2, respiration, vo2max, steps, weight` 等。沒有 Garmin 的 Training Status。OpenAPI 路徑要求 `{ext}`；probe 呼叫的是不帶副檔名的 `/wellness`（回 JSON），這個寫法**未驗證**，錯的話 probe 會顯示 404 |
| 計畫課表（行事曆） | `GET /api/v1/athlete/{id}/events{format}?oldest=&newest=&category=WORKOUT` | |
| 建立／更新課表（upsert） | `POST /api/v1/athlete/{id}/events/bulk?upsert=true` | 用 `external_id` 對應自己的主鍵（topic 63624，2024-12-04）。內容可以是 intervals.icu 的文字格式（`description`），或 base64 的 `.fit`／`.zwo`／`.mrc`／`.erg` |
| 刪除課表 | `DELETE /api/v1/athlete/{id}/events/{eventId}`、`PUT .../events/bulk-delete` | |
| 上傳活動 | `POST /api/v1/athlete/{id}/activities`（fit／tcx／gpx／zip／gz） | |

### 1.3 計畫課表能不能到手錶上（課表推送）

- **Garmin**：/settings 勾「Upload planned workouts」，會跳到 Garmin Connect 授權。之後「Intervals.icu will upload your next week of planned workouts … Workouts are automatically updated whenever you make changes to your plan」（topic 1521，2020-08-19）。
- **COROS**：勾選後「the next week of workouts are uploaded. You may need to add the "Intervals.icu Training Plan" to your calendar in the Coros app」（topic 37006）。
- 已知問題：Garmin 同一步驟同時有配速和心率目標時，只會送出一種（topic 67330 的標題與搜尋摘要，**未逐字驗證**）。
- 所以 **本 app → intervals.icu 行事曆 → Garmin／COROS 手錶** 這條路是可行的。它取代：
  - `coros_workouts.py` 的非官方 Training Hub 推送；
  - `garmin.md` 裡非官方的 `upload_workout`。
- 文字格式（「Workout Builder Syntax Quick Guide」，topic 123701，2026-08-10）：
  - 一步一行，`- [時間或距離] [目標]`。
  - 重複用 `Main Set 4x` 或單獨一行 `5x`，前後要空一行；**不支援巢狀重複**。
  - 心率只有 `% HR`、`% LTHR`、`Z2 HR` 三種寫法，**沒有絕對 bpm**。所以 app 的 bpm 要換成 % LTHR，前提是 intervals.icu 裡設的 LTHR 和 app 一致。
  - 功率可以寫絕對瓦數 `220w`／`200-240w`。
  - 步驟前面的文字是提示語（cue）；提示語裡有數字可能被當成時間解析，所以 probe 只用「暖身／主課／恢復／緩和」。
- 以下三點 probe 會實測，目前**未驗證**：
  1. 沒有目標的步驟（`- 恢復 3m`）能不能被接受；
  2. event `type: "Run"` 是不是 Garmin／COROS 上傳需要的類型；
  3. 中文提示語能不能正常解析。
- 對應實作：`backend/scripts/intervals_probe.py` 的 `workout_text()`／`session_event()`，一樣沿用 `coros_workouts.session_steps`；離線測試在 `backend/tests/test_intervals_probe.py`。
- 功率的校正問題（同 `garmin.md` §3.3）：
  - app 的 CP 來自 COROS 功率，推到 **COROS 手錶**時瓦數是可比的。
  - 推到 Garmin 手錶時，Garmin 腕式功率或 Stryd 和 COROS 功率不同。
  - `power_targets=False` 可以只送心率。

### 1.4 費用

- 定價頁（2026-10-01）：Free「$0 forever」；Supporter「$4 /month」。
- 裝置同步與上傳課表在免費版就有。Supporter 多的是天氣分析、年度計畫、完整 Strava 歷史、自訂區間、CSV streams 上傳、團隊等。
- **休眠規則（重要）**：作者 2025-10-01 回覆：「Your account was set to DORMANT because you hadn't been seen on Intervals.icu for 90 days and are not a supporter. Intervals.icu does not process files for dormant accounts」。
  - 免費帳號要每 90 天上網站一次，或者付 Supporter。
  - **只用 API 呼叫算不算「seen」，未驗證**。
  - 對 hub 模式來說，Supporter 的 $4/月等於「保證持續收資料」的成本。

### 1.5 條款（API Terms and Conditions，Effective Date: October 23, 2025，topic 114087 原文）

- §1：「We grant you a non-exclusive, worldwide, royalty-free, perpetual license to access and use the API for any lawful purpose, **including commercial use**. Except for the Garmin attribution requirements in 1.1, you may integrate, modify, distribute, and sublicense outputs derived from the API without restriction or attribution.」
- §1.1：「if your application displays information derived from Garmin-sourced data, you must display attribution to Garmin in the form and manner required by Garmin's brand guidelines.」
  - 作者在同串補充：wellness 很難判斷來源，intervals.icu 自己是一律標「Charts may include data from Garmin devices」。
  - 活動可以用 `device_name` 判斷是不是 Garmin。
- §2：「Do not abuse the API.」
- §4：「We may suspend or terminate access if you violate these Terms, with 7 days' notice where possible.」
- §7：條款改動會以 email 提前 30 天通知。
- §8：準據法是南非。
- 對付費版的意義：
  - intervals.icu 這一層允許商業使用。
  - 要做 Garmin 的來源標示（Garmin API Brand Guidelines，2025-07）。
  - Garmin 的 Developer Program Agreement §5.2.o 限制 licensee 把 API 功能或資料「make available … to any third party」（見 `garmin.md` §1.3）。intervals.icu 再把 Garmin 資料經自己的 API 交給我們，是否得到 Garmin 同意，從公開資料看不出來，**未驗證**。intervals.icu 條款裡的 Garmin 標示條款，暗示它與 Garmin 有協調過。

### 1.6 可靠度

- 單人開發（作者 David），但長期穩定營運：API 自 2020 年就有，2026-07 仍在更新文件。
- 接 Garmin、COROS 都走官方 partner API，不受 2026-03 Garmin 非官方登入失效的影響。
- 風險：
  - 單一廠商；
  - 免費帳號 90 天休眠；
  - Garmin 只給一年歷史；
  - Cloudflare 對非瀏覽器 User-Agent 的挑戰。

---

## 2. GoldenCheetah（開源桌面軟體，GPL-2.0）

- 狀態（GitHub，2026-10-01）：最新正式版 **v3.8（2026-09-20）**；master 最後 push 2026-09-26；授權 **GPL-2.0**。
- **cloud services**，`src/Cloud/` 目錄（2026-10-01 的 master）：
  - Strava、Dropbox、PolarFlow、SportTracks、Nolio、Xert、Tredict、RideWithGPS、Withings、CyclingAnalytics、Selfloops、SixCycle、SportsPlusHealth、TrainingsTageBuch、Azum。
  - 另有 Google 行事曆 CalDAV、GoldenCheetah OpenData、CloudDB。
  - **TrainingPeaks、Today's Plan 已移到 `deprecated/`**：`TPDownload.cpp`、`TodaysPlan.cpp` 等。
  - master 裡**找不到 intervals.icu 的 cloud service 檔案**；可能在別的分支或用別的方式整合，**未驗證**。
- **怎麼處理 API secret**（`src/Core/Secrets.h`）：
  - 原文：「Because these are registered to the GoldenCheetah project they are not made public. If you want to build with these services enabled you will need to request a token from the provider and add a line to your gcconfig.pri file」。
  - 原文：「This file is modified by the AppVeyor build scripts to replace the __XXXX_SECRET__ token with the secret gem held within the AppVeyor build environment.」
  - 也就是：**原始碼只放佔位字串，官方 build 在 CI 注入 secret**。client ID 有些直接寫在原始碼，例如 `GC_STRAVA_CLIENT_ID "83"`、`GC_SPORTTRACKS_CLIENT_ID "goldencheetah"`；secret 一律是 `__GC_…_SECRET__`。
  - 新的整合改用 **PKCE 不需要 secret**：「Tredict (PKCE, no client secret needed)」，另有 `OAuthPKCE.cpp`。
- **可以學的**：
  - 和本專案 `tp_client.enc` 的問題一樣：桌面 app 的 secret 只要隨執行檔發出去，就能被抽出來。GoldenCheetah 的做法只是「不放在原始碼」。官方 binary 裡仍有 secret，只是靠 CI 注入。
  - 對本專案的啟示：
    - 能用 PKCE（public client）的服務，就不要 secret；
    - 一定要 secret 的，走 plan §10 的付費版 server 代為交換 token。
- **GPL 影響**：
  - 直接複製 GoldenCheetah 的 C++ 程式碼（或翻譯成 Python 的衍生作品）到本專案，本專案發行時就必須以 GPL 相容授權釋出原始碼。
  - plan §7.6 的授權還沒定。**只參考「它接了哪些服務、用什麼流程」不構成衍生作品**；不要複製程式碼。

---

## 3. 其他 hub（各一行，2026-10-01）

| 平台 | 輸入 | 個人 API／商業 API | 原始 FIT | 推課表到 Garmin／COROS | 費用 | 條款重點 | 可靠度 |
|---|---|---|---|---|---|---|---|
| **Strava** | 幾乎所有品牌 | 個人可建 app，新 app 只能連 1 人，自助升到 10 人，再多要審核（rate-limits 頁）。據 tryterra（2026-09-06）2026-06 起 Standard 開發者要付月費。官方 rate-limits 頁沒有提到費用，所以「要付費」這件事和金額都**未驗證** | **無**，只有 streams（API reference 沒有活動原始檔端點） | 否（Strava 沒有結構化課表推送） | 一般使用者免費／訂閱 | API Agreement（Effective June 1, 2026）：「Strava Data provided by a specific user can only be displayed or disclosed in your Developer Application to that user」；「You may not create applications that compete with or replicate Strava functionality」。AI 禁令：2024-11-15 公告加入「explicitly prohibit … artificial intelligence models」，目前在 API Policy；原文我沒讀到（API Policy 頁 404），**未驗證** | 高，但條款越來越緊；本 app 有 AI 功能，**不適合** |
| **Runalyze** | Garmin、COROS（官方 API）等 | **Personal API**：帳號設定產生 token，要設到期日，header `token:`；免費版是基本端點，Supporter／Premium 可讀更多；「Currently, there is no rate limit」（help「personal-api」）。商業 API 叫 Third Party API，條件**未驗證** | 有：「original fit file」匯出端點（changelog 搜尋摘要，**未驗證**） | 未驗證 | 免費／Supporter／Premium | 未讀條款，**未驗證** | 中（德國小團隊） |
| **SportTracks** | Garmin、COROS 等 | 寄信 api@sporttracks.mobi 取得 OAuth 憑證（api/doc 頁的搜尋摘要） | 未驗證 | Garmin：有（blog「Garmin Training Integration」）；COROS：有活動同步，課表推送**未驗證** | 訂閱 | 未讀，**未驗證** | 中 |
| **Final Surge** | Garmin、COROS 等 | 找不到公開的開發者 API，**未驗證** | 未驗證 | 有：Garmin（每晚推接下來 4 天）、COROS（官方 blog 與 the5krunner 2024-09） | 免費（教練付費） | — | 中；沒有 API，不能當 hub |
| **Nolio** | Garmin、Suunto、Polar、COROS、Strava、Zwift、Wahoo 等 | 有 OAuth 2.0 API，「A quick form and you're good to go」（developers 頁），能讀寫課表與指標。商業條件**未驗證** | 未驗證 | 有：「Structured workouts created in Nolio are automatically sent to compatible devices like Garmin, Coros or Suunto」（connectors 頁摘要） | 免費／付費 | 未讀，**未驗證** | 中（法國）；可當 intervals.icu 的備案，值得之後細查 |
| **TrainingPeaks** | 幾乎所有品牌 | 官方 Partner API：「at this time access to the API is not available for personal use」（官方 blog，2026-09-12 更新）。只給商業 fitness app 與裝置廠；有報導說目前不收新夥伴（搜尋摘要，**未驗證**） | Partner API 有，個人拿不到 | 有（TP 本身會推 Garmin／COROS） | 訂閱 | 見 plan §2.1：WKO5 client secret 不得用於他人 | 高，但個人與小團隊拿不到 API |
| **Apple Health／Google Health Connect** | 手機上的各家 App 寫入 | 只能在**手機上的 app** 讀，沒有雲端 API | 否，只有運動紀錄與樣本，沒有 FIT | 否 | 免費 | 平台審核規範（未細查） | 本 app 是桌面版，**不適用**；除非日後做手機 App |

---

## 4. 建議

### 4.1 個人自用（現在）

1. 在 intervals.icu 建帳號：
   - 連 **COROS**（勾 Upload planned workouts）；
   - 連 **Garmin**（勾 Upload planned workouts，並按 Import All Garmin Data 補一年以上的歷史）。
2. 用 `backend/scripts/intervals_probe.py` 實測（§5）：
   - 原始檔下載對 COROS 與 Garmin 來源的活動是否都拿得到 `.fit`；
   - 推一個測試課表，看會不會出現在 Garmin／COROS 手錶上；
   - % LTHR 換算後的心率範圍對不對。
3. 實測通過再做 hub 模式（§4.3）。之後：
   - COROS 非官方 Training Hub API 降為備案；
   - Garmin 非官方 `garminconnect` 不必做。
4. 考慮付 Supporter（$4/月），避免 90 天休眠後停止收資料。

### 4.2 付費版（之後）

- intervals.icu OAuth app 是目前**唯一**同時具備這四項條件的路線：
  1. 條款明文允許商業使用；
  2. 個人開發者能申請；
  3. 有原始 FIT；
  4. 能推課表到 Garmin＋COROS。
- 要先做的事：
  - 申請 OAuth app，問清楚核准條件和限流。100 次／人／天，對「每天同步數筆活動＋推一週課表」應該夠用：列表 1 次、每筆 FIT 1 次、課表 bulk 1 次。
  - 寫信問 intervals.icu：付費第三方 app 轉用 Garmin 來源資料，除了來源標示以外有沒有其他限制（§1.5 的未驗證點）。
  - 做 Garmin 來源標示。活動用 `device_name` 判斷；wellness 學 intervals.icu 統一標示。
- 這等於要求付費使用者也有 intervals.icu 帳號，免費或 Supporter 都可以。好處是：
  - 我們不必保管 Garmin 或 COROS 的帳密；
  - 不受 Garmin 暫停申請影響；
  - 不必申請 COROS Partner API。
- 風險：
  - 依賴單一小廠商；
  - intervals.icu 對 OAuth app 的核准標準未公開。

### 4.3 app 要不要支援「hub 模式」：要

- 設計，沿用 plan §9.2 的「每來源」架構：
  - `storage.SOURCES` 加 `"intervals": "intervals"`；
  - 新增 `backend/sync/intervals_client.py`：
    - API key（個人）或 OAuth bearer（付費版）；
    - 列活動 → `GET /activity/{id}/file` 取原始 FIT；
    - Strava 來源的活動跳過，因為拿不到檔案；
  - 檔案放 `~/.wko5coach/fit/intervals/`。
- 去重：現有的「開始時間 ±2 分鐘」可以直接用。intervals.icu 的活動同時有 `source`（COROS／GARMIN_CONNECT）和 `external_id`，可以再加強對應。
- 課表推送：新增 `intervals_workouts.py`：
  - 沿用 `coros_workouts.session_steps`，換成 `workout_text()`；
  - 用 `external_id = trc-<session uid>` 做 upsert，不需要像 `coros_plan_push` 那樣存對方的 id；
  - 刪除只動 `external_id` 是 `trc-` 開頭的。
- 每日指標：wellness 端點可以補 HRV、睡眠、靜息心率，給 readiness 類的判讀用。
- API key 用 `secrets.seal()` 存在 DB，和 COROS token 同一套做法。

---

## 5. 使用者測試步驟（intervals.icu）

不需要安裝新套件，`httpx` 已經在 `requirements.txt`。

1. 在 https://intervals.icu 登入，到 /settings：
   - 連好 Garmin 和／或 COROS，Upload planned workouts 先**不要**勾，第 4 步才需要；
   - 在頁面底部的 Developer Settings 產生 API key。
2. 離線預覽測試課表（不連網）：
   ```powershell
   .venv\Scripts\python.exe -m backend.scripts.intervals_probe --preview --date 2026-10-03
   ```
3. 唯讀測試，會用不顯示的方式問 API key，key 只放記憶體：
   ```powershell
   .venv\Scripts\python.exe -m backend.scripts.intervals_probe
   #   選項：--limit 10、--days 60、--activity-id i12345678、--no-download
   ```
   - 會印出 athlete id 和最近的活動（每筆含 `source`、`device_name`、`file_type`）。
   - 會下載最新一筆的原始檔到 `~/.wko5coach/intervals/probe/`。
   - 會印出最近 7 天的 wellness 和未來 14 天的計畫課表。
   - 請確認 COROS 來源和 Garmin 來源的活動都下載得到 `.fit`。用 `--activity-id` 指定不同來源的活動各試一筆。
4. 寫入測試，會先問 y/N：
   ```powershell
   .venv\Scripts\python.exe -m backend.scripts.intervals_probe --push-test --date 2026-10-03 --lthr 你的LTHR
   ```
   - 會建立「TrailRunCoach 測試課表（可刪除）」：暖身 5 分 70–80% LTHR → 2×（1 分＋1 分恢復）→ 緩和 5 分。
   - 確認 intervals.icu 行事曆上看得到它，probe 會印出解析出幾個步驟。
   - 想測手錶，就到 /settings 勾 Garmin／COROS 的 Upload planned workouts，等同步後看手錶上的步驟與心率範圍。
5. 清除：
   ```powershell
   .venv\Scripts\python.exe -m backend.scripts.intervals_probe --cleanup --date 2026-10-03
   ```
   只刪名稱是測試課表、`external_id = trailruncoach-probe-test` 的那一筆。
6. 回報：
   - 每步的輸出，可以遮掉名字；
   - 兩種來源的 `file_type` 與下載結果；
   - 手錶上看到的課表；
   - 剩餘額度那一行。

---

## 6. 重新驗證清單

1. intervals.icu API 說明（topic 609）的限流數字、OAuth 申請流程（topic 2759）。
2. API Terms and Conditions（topic 114087）有沒有改版，特別是商業使用和 Garmin 標示。
3. `GET /activity/{id}/file` 對 Garmin、COROS 來源是否都回原始 FIT，以及壓縮格式。
4. Upload planned workouts 對 Garmin 與 COROS 的支援範圍（天數、目標類型、重複、提示語）。
5. 90 天休眠規則；API 呼叫算不算「seen」。
6. Garmin 對 intervals.icu 轉出 Garmin 資料的限制（Garmin API Brand Guidelines、Developer Agreement）。
7. Workout Builder 文字格式有沒有新增絕對 bpm 的寫法。
8. GoldenCheetah master 的 `src/Cloud/` 有沒有新增 intervals.icu；`Secrets.h` 的做法。
9. Strava API Policy 的 AI 條款原文、Standard 開發者月費金額。
10. Runalyze Personal API 的原始 FIT 端點；Nolio API 的商業條件與 FIT 下載。

---

## 7. 來源（2026-10-01 讀取）

- intervals.icu：
  - API 說明：https://forum.intervals.icu/t/api-access-to-intervals-icu/609
  - OAuth：https://forum.intervals.icu/t/intervals-icu-oauth-support/2759
  - API 條款：https://forum.intervals.icu/t/intervals-icu-api-terms-and-conditions/114087
  - 上傳計畫課表：https://forum.intervals.icu/t/uploading-planned-workouts-to-intervals-icu/63624
  - 課表文字語法：https://forum.intervals.icu/t/workout-builder-syntax-quick-guide/123701
  - Garmin 課表上傳：https://forum.intervals.icu/t/upload-planned-workouts-to-garmin-connect/1521
  - COROS 支援：https://forum.intervals.icu/t/coros-support-added/37006
  - Garmin 歷史一年：https://forum.intervals.icu/t/garmin-connect-history-now-only-goes-back-one-year-max/3906
  - 休眠規則：https://forum.intervals.icu/t/solved-suspended-account-no-activity-processed-ans-acc-set-dormant-if-90days-not-visit-intervals-icu-is-not-supporter/112826
  - 配速＋心率同步問題：https://forum.intervals.icu/t/syncing-both-pace-and-heart-rate-to-garmin-connect-workout/67330
  - 定價：https://www.intervals.icu/pricing/
  - OpenAPI：https://intervals.icu/api/v1/docs ，RapiDoc：https://intervals.icu/api-docs.html
- GoldenCheetah：
  - repo：https://github.com/GoldenCheetah/GoldenCheetah
  - `src/Core/Secrets.h`、`src/Cloud/`、`deprecated/`
- Strava：
  - API Agreement：https://www.strava.com/legal/api
  - 限流：https://developers.strava.com/docs/rate-limits/
  - API reference：https://developers.strava.com/docs/reference/
  - 2026 變更：https://tryterra.co/blog/strava-api-changes-2026
- Runalyze：https://runalyze.com/help/article/personal-api
- Nolio：
  - https://www.nolio.io/en/developers/
  - https://www.nolio.io/en/connectors/
- SportTracks：https://sporttracks.mobi/api/doc
- Final Surge：
  - https://blog.finalsurge.com/now-sync-planned-structured-workouts-to-garmin-connect/
  - https://the5krunner.com/2024/09/12/coros-final-surge-sync/
- TrainingPeaks：https://www.trainingpeaks.com/blog/an-update-on-trainingpeaks-partner-api/
- Garmin 暫停受理：https://the5krunner.com/2026/09/14/garmin-developer-api-access-paused/
- Health Connect：https://www.androidcentral.com/wearables/garmin/heres-everything-garmin-will-and-wont-share-with-google-health-connect
