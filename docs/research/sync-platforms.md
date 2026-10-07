# 透過第三方平台同步 COROS、Garmin、Apple Watch（SP-343）

> 調查日期：2026-10-07。只做調查，沒有改程式。
> 背景：SP-331 拿不到 COROS／Garmin 官方 API 時的備案。資料流是「手錶 → 品牌 App → 第三方平台 → TrailRunCoach」，課表反向走「TrailRunCoach → 平台 → 手錶」。
> 標記：**已驗證**＝今天讀了一手頁面，引句逐字吻合；**摘要**＝搜尋摘要或二手來源（含論壇非官方人員）；**推估**＝我的推論；**未找到來源**＝查過，寫出試過什麼。
> 方法：
> - 五路平行查證：intervals.icu；TrainingPeaks 與 Final Surge；Strava 等小平台；Apple 與 Health Connect；商用聚合 API。
> - 只用 GET 讀公開頁面，原始 HTML、PDF、Zendesk／Discourse 公開 JSON 存下來後用腳本逐字比對引句（約 500 句）。
> - 最關鍵的 10 句另外人工重抓原頁核對，都吻合。
> - 沒有登入、沒有建帳號、沒有用任何 API key、沒有發寫入請求。網頁內容一律當資料，不當指令。

## 摘要

1. **intervals.icu 仍是唯一能用的中介**。它同時符合四個條件：條款明文允許商業使用；個人開發者申請得到；拿得到活動檔；能把課表推到 Garmin、COROS、Suunto。新發現是 OAuth app **有 webhook**，訂閱層的「即時同步」不必靠輪詢。（已驗證）
2. **代價主要在使用者這端**：
   - 要多一個 intervals.icu 帳號。
   - 免費帳號 90 天沒上網站會休眠、停止收新活動；只透過我們的 API 存取很可能不算「上過網站」（推估）。
   - 經 intervals.icu 能拿的歷史：COROS 只有 3 個月、Garmin 1 年。
   - Garmin 給的 FIT 被 Garmin 過濾過。
   - Garmin 來源的資料（包括 AI 的輸出）都要標示 Garmin。
3. **Nolio 技術上第二完整**：有原始檔下載、有 webhook、能用 API 寫課表，平台再推到 Garmin、COROS、Suunto、Apple Watch。但它**沒有公開的 API 條款**，方案表又把「API access」列為 Premium（6.90 歐元／月）。寫信問清楚之前不能排第一。
4. **其他平台都不能當商業中介**：
   - TrainingPeaks：2022 年起不收新夥伴；條款禁止競爭、資料最多快取 7 天；推課表要使用者付 Premium。
   - Strava：禁止用於 AI、禁止去識別化分析、快取最多 7 天。
   - Final Surge：沒有 API。
   - Runalyze：第三方 app 只能寫入、不能讀取。
   - SportTracks：沒有 FIT，也不能寫課表。
   - Golden Cheetah：桌面軟體，沒有雲端。
   - 商用聚合 API：每月 US$300～598，是預算的 10～160 倍；Terra 接 Garmin 還要自備 Garmin 金鑰。
5. **Apple Health 和 Health Connect 都是手機上的資料庫**，沒有雲端 API，也沒有 FIT。不寫原生 app 的話，Apple Watch 唯一的路是使用者自己裝第三方橋接 app（Intervals Companion 免費，或 HealthFit US$6.99）把資料送進 intervals.icu。
6. **建議的備案順序**：
   1. intervals.icu hub；
   2. 手動匯入加桌面自動匯入（一直保留，也是補歷史的路）；
   3. Nolio（寫信確認後再決定）；
   4. 原生 iOS app（等 Apple Watch 使用者夠多再做）。

   COROS 使用者要不要改走 COROS 官方的 MCP（免申請），等 SP-331 的結論。
7. **能不能批次同步並存回本專案**：
   - 經 intervals.icu 可以：條款沒有保存期限，逐筆下載原始檔，用日期分段批次抓。
   - TrainingPeaks 不行：條款限快取 7 天、不得建資料庫，Partner API 也沒有 FIT。
   - 細節見 §4.8。
8. **推課表**：間歇、心率、配速、功率、距離、重複都能經 intervals.icu 推。會降級的是：
   - 絕對 bpm 改成 % LTHR；
   - 計圈步驟、TL 結束條件改成時間；
   - COROS 上刪不掉的課；
   - COROS 跑後自評與 TL 拿不到。

   細節見 §4.9。

---

## 1. 現況

### 1.1 程式現在怎麼接

| 路徑 | 位置 | 做法 | 商業版能不能用 |
|---|---|---|---|
| COROS 同步與推課表 | `backend/sync/coros_client.py:34`（`teamapi.coros.com`）、`backend/sync/coros_workouts.py` | 使用者帳密登入非官方 API | 待 SP-331（COROS 條款還沒人讀過原文） |
| TrainingPeaks 同步 | `backend/sync/tp_client.py` 檔頭說明 | WKO5 的 OAuth client，或網站表單登入取得 cookie，再打內部 API `tpapi.trainingpeaks.com` | 不能，見 §4.6 |
| Garmin 推課表 | `backend/sync/workout_targets/garmin.py` | stub（`enabled = False`、不連網） | 非官方路線條款禁止（`garmin.md` §2.4） |
| intervals.icu 推課表 | `backend/sync/workout_targets/intervals.py:18`（`enabled = False`）；離線轉換器在 `backend/scripts/intervals_probe.py:86`（`workout_text`）、`:111`（`session_event`） | stub；心率送 % LTHR、沒有「直到按下計圈」與「負荷」結束條件 | 本文件的主要候選 |
| 來源清單 | `backend/sync/storage.py:20` | 只有 `coros`、`tp` | 要加 `intervals`（`data-hubs.md` §4.3 已設計） |
| 去重 | `backend/sync/dedup.py:26` | 開始時間 ±2 分鐘算同一筆 | 可沿用，SP-344 會延伸 |
| 排程 | `backend/sync/scheduler.py` | 每天固定時間跑一次 | 免費層的夜間批次可以沿用 |

### 1.2 已決定的事（SP-321，2026-10-07）

- 價格 NT$99／月、NT$890／年。
- 免費層：每天晚上自動同步一次（前提是 SP-331 允許）。
- 訂閱層：即時同步、推課表到手錶。
- SP-331 有結論前，文案不寫「自動同步、推手錶」。

### 1.3 既有研究

- `docs/research/data-hubs.md`（2026-10-01）已經建議「hub 模式」：把 intervals.icu 當成一個資料來源。
- 本文件重新核對那份文件，補上商業面（條款、費用、會員分級），並擴大比較範圍。和那份文件不一致的地方列在 §4.7。

---

## 2. 比較表

表內沒有標記的都是**已驗證**；推估、摘要、未找到來源會另外標。

### 2.1 手錶支援與 API 能力

| 平台 | COROS | Garmin | Apple Watch | Suunto | Polar | API：誰能用 | 原始 FIT | 經 API 推課表到手錶 |
|---|---|---|---|---|---|---|---|---|
| **intervals.icu** | 官方直連 | 官方直連 | 第三方 iOS app（HealthFit、Intervals Companion） | 官方直連 | 官方直連 | OAuth app 線上申請、人工核准；個人可申請 | 有（gzip）；Garmin 的被過濾；Huawei 是重新產生的 | Garmin、COROS、Suunto、Wahoo、Amazfit；Huawei 一次一堂；Polar 不行；Apple 靠第三方 app |
| **Nolio** | 自動同步 | 自動同步 | 經 Nolio app | 自動同步 | 自動同步 | 表單加人工審核；多人 app 要另外開通 | 有（`file_url`，1 小時有效）；是不是原始檔沒寫（推估是） | 有：Garmin、COROS、Suunto、Apple Watch；Polar 頁沒寫；經 API 建的課表會不會推，**未找到來源** |
| **TrainingPeaks** | 自動同步（不回溯） | 自動同步（可回溯 5 年） | TP iOS app | 自動同步（不回溯） | 自動同步（不回溯） | Partner API 只給公司，**2022 年起不收新夥伴** | 沒有下載端點；只有 Premium 使用者的時間序列 JSON | 有：Garmin 15 天、COROS 7 天、Apple 7 天、Suunto、Polar；未來日期限 Premium |
| **Final Surge** | 雙向 | 雙向 | 雙向 | 雙向 | 只進不出 | **沒有 API**（未找到來源） | — | 第三方寫不進去 |
| **Strava** | 自動同步 | 自動同步 | 自動同步 | 自動同步 | 自動同步 | OAuth；開發者要有 Strava 訂閱；超過 10 人要審核 | 沒有，只有 streams | 不能 |
| **Runalyze** | 自動同步 | 自動同步 | HealthFit | 自動同步 | 自動同步 | 第三方 OAuth **只能寫入**；讀取只給使用者自己的 Personal token（Supporter 以上） | 有，但只限 Personal token | 沒有 |
| **SportTracks** | 自動同步 | 自動同步 | HealthFit、RunGap | 自動同步 | 自動同步 | 寄信取得 OAuth 憑證 | 沒有，JSON 軌跡陣列 | API 只能讀課表，不能寫 |
| **Golden Cheetah** | — | — | — | — | — | 桌面軟體，沒有伺服器 API | 不適用 | 不適用 |
| **Apple Health＋WorkoutKit** | 只寫摘要，清單沒有 Workouts | 摘要（摘要） | 原生 | 未找到來源 | 只寫摘要、沒有路線 | 只在 iPhone 上；要原生 iOS app | 沒有（推估） | 原生 iOS app 用 `WorkoutScheduler` 推到 Apple Watch |
| **Health Connect** | 有寫入（型別是摘要） | 單向、不含功率與步頻（摘要） | — | 未找到來源 | 有，含路線 | 只在 Android 上；要原生 app，加上 Play 健康權限審查 | 沒有（推估） | 有 `PlannedExerciseSessionRecord`，但哪支錶會讀，**未找到來源** |
| **Terra**（聚合） | Terra 代管 | **要自備 Garmin key**（目前申請不到） | 要 Terra iOS SDK | Terra 代管 | Terra 代管 | 付費合約 | 有（Garmin 是原檔；其他來源可能是重建的） | 有（另付 US$99／月），Polar 除外 |
| **其他聚合**（Junction、Spike、ROOK、Thryve、Sahha） | Spike 有；ROOK、Sahha 只經手機；Junction、Thryve 沒有 | Spike、Junction、ROOK、Thryve 用自家授權；Sahha 要自備 | 要 SDK | 部分支援 | 部分支援 | 付費合約 | 沒有，或未找到來源 | 未找到來源；Sahha 明說不支援 |
| **Open Wearables**（開源自架） | 沒有 | 要自備 key | 要 SDK | 要自備 key | 要自備 key | MIT 授權，自己架 | 未找到來源 | 還在「Exploring」階段 |

### 2.2 限制、條款、費用

| 平台 | webhook／輪詢 | 限流 | 能拿多久的歷史 | 商業使用 | AI | 跨使用者分析 | 使用者要付費嗎 | 開發者費用 |
|---|---|---|---|---|---|---|---|---|
| **intervals.icu** | **有 webhook**（重送 8 次後放棄，要每天輪詢補漏）；Strava 來源沒有 | app 總額每天 max(5,000, 100×人數)，500 人封頂；每 IP 每秒 10 次 | Garmin 1 年；COROS 3 個月；Polar 只有新活動；Suunto 全部 | 明文允許，免權利金 | 條款沒限制 | 條款沒限制；Garmin 資料要標示 | 不用；但免費帳號 90 天休眠（Supporter US$4／月可免） | 沒看到收費（未找到來源） |
| **Nolio** | 有 webhook | 每小時 1,000＋50×人數；每天 10,000＋250×人數 | 沒寫上限 | **沒有公開 API 條款**（未找到來源） | 隱私政策歡迎使用者自接 AI | 未找到來源（有研究用 API） | 「API access」列在 Premium 6.90 歐元／月；第三方讀取要不要，**未找到來源** | 未找到來源 |
| **TrainingPeaks** | webhook 還是 Early Access；輪詢每次最多 45 天 | 沒有硬性上限 | 文件沒寫 | 只能對「TP 沒有的功能」收費；**不得競爭** | 條款沒寫；申請表會問 AI 用途 | 要使用者 opt-in；不得建資料庫；快取 ≤ 7 天 | 讀時間序列、推未來課表都要 Premium（US$19.95／月或 US$134.99／年） | 目前免費 |
| **Final Surge** | — | — | Garmin 只回溯 30 天 | 條款只允許「personal, noncommercial use」 | 沒寫 | FS 自己會分享彙總資料 | 運動員免費 | — |
| **Strava** | 有 webhook | 200／15 分、2,000／天 | 沒寫 | 只能對 Strava 沒有的功能收費 | **禁止**（§5.3） | **禁止**（§5.4）；快取 ≤ 7 天（§6.2） | 開發者要有 Strava 訂閱 | 訂閱（金額只有摘要） |
| **Runalyze** | 沒有 | 每小時 40／150（說明頁另寫「沒有上限」，兩處矛盾） | 沒寫 | Personal API 定位是「private and own use」 | 自家 MCP（Premium） | 未找到來源 | 讀取要 Supporter（2.50 歐元／月）以上 | — |
| **SportTracks** | 未找到來源（推估要輪詢） | 未找到來源 | 沒寫 | 沒有公開條款 | 沒有公開條款 | 沒有公開條款 | 推課表要付費版（US$59／年，推估） | 未找到來源 |
| **Apple Health** | 背景喚醒，鎖機時讀不到 | — | 文件沒寫限制 | 只能用於健身服務 | 交給第三方 AI 前要揭露並取得明確同意（5.1.2(i)） | 禁止「use-based data mining」；換用途要再同意 | 不用 | 開發者年費 US$99 |
| **Health Connect** | 背景讀取要另外申請權限；別的 app 寫的路線只能在前景讀 | — | 預設只能讀授權前 30 天 | 限介面上看得到的功能 | 轉給第三方只有安全、法遵、併購三種例外（AI 屬於模糊地帶，推估） | 「aggregated for internal operations」有空間（推估） | 不用 | 註冊費 US$25；個人帳號要先跑 12 人、14 天封閉測試 |
| **Terra** | Garmin 事件驅動（手錶同步後 30～90 分鐘）；COROS 約每 15 分鐘輪詢 | 每月 credits | Garmin 5 年、COROS 3 個月、Polar 30 天 | 只能用在 Terra 書面核准的用途；不能轉售 | 沒有專門條款 | 要寫進核准用途 | 不用 | **US$499／月（年繳 $399）＋課表 $99** |
| **其他聚合** | 都有 webhook | — | 7～90 天 | 不能轉售；ROOK、Thryve、Spike 會保留匿名衍生資料自用 | Sahha：用可識別資料訓練 AI 要使用者另外同意 | Sahha 只允許自家 app 內部分析 | 不用 | Junction $300、Sahha $299～399、ROOK $399、Spike $450 起、Thryve €499（每月） |
| **Open Wearables** | 有 webhook | 跟各廠商 | Garmin 30 天、Polar 365 天、Suunto 不限 | 跟各廠商條款 | 跟各廠商條款 | 跟各廠商條款 | 不用 | $0（自架） |

### 2.3 對照：品牌官方與現行非官方（細節是 SP-331 的範圍）

| 路線 | 誰能用 | FIT | 推課表 | webhook | 條款重點 | 狀態 |
|---|---|---|---|---|---|---|
| COROS 非官方（現況） | 任何人，用帳密 | 有 | 有 | 沒有 | COROS 使用者條款還沒讀（SP-331） | 運作中 |
| COROS MCP（官方） | **免申請**："No application or approval required." | 有，每位使用者每 24 小時 50 個檔 | 有（路跑、越野、自行車課表；4～16 週計畫） | 沒有："No webhook push notifications (your app polls the MCP)" | "Use of COROS MCP is subject to the COROS Terms of Service and Privacy Policy."；官方列的用途含 "AI coaching apps that analyze training and prescribe workouts" | 2026-09 開放；多人收費服務能不能用、要不要錢，**未找到來源** |
| COROS Partner API | "Established platform with demonstrated user base"、註冊公司 | 有 | 有 | 有（約 5 分鐘） | 要同意 API Terms of Use | 寄信申請 |
| Garmin Developer Program | 只給企業 | 有 | 有 | 有 | 收費要 Garmin 書面許可（`garmin.md` §1.3） | 官方頁仍是 "Stay tuned for more updates on the program"，**沒有恢復受理** |
| Polar AccessLink | 任何 Polar Flow 使用者 | 有 | 沒有（推估：端點清單裡沒有任何課表寫入） | 有 | 免權利金；資料給外部（例如 LLM）要使用者明確同意；要標示 Polar | 開放 |
| Suunto API | 只給公司或組織 | 有 | "push workouts" 是上傳 FIT 活動檔，不是課表 | 有（workout） | 免費 | 官網寫可以申請；Terra 表上寫 "Applications no longer open"，兩邊矛盾 |

---

## 3. 各平台重點（原文）

### 3.1 intervals.icu

**輸入與歷史**

- `Activity.source` 列舉值（OpenAPI）：`STRAVA, UPLOAD, MANUAL, GARMIN_CONNECT, OAUTH_CLIENT, DROPBOX, POLAR, SUUNTO, COROS, WAHOO, ZWIFT, ZEPP, CONCEPT2, HUAWEI`。（已驗證，https://intervals.icu/api/v1/docs ）
- Garmin 歷史只有一年："they agreed to allow calls to fetch history going back up to a year."（已驗證，topic 3906）
  - 更早的：設定頁按 "Import All Garmin Data"，Garmin 會寄整個帳號的封存（已驗證，topic 112826 #14）。
- COROS 歷史只有 3 個月，作者轉引 COROS API 文件："The maximum date range for one query is 30 days, and the query date is not earlier than three months before the day"（已驗證，topic 37006 #76；COROS 文件本身沒讀）。
- Polar 只有新活動："Only new activities are downloaded. This is a limitation of the Polar API."（已驗證，topic 5149）
- Suunto 可以全部："If you connect Suunto you should be able to get everything."（已驗證，topic 24678 #2）
- Apple Watch 沒有官方整合。作者原話："you need to have an iOS app running on the phone to get Apple data."（已驗證，topic 67776 #6）。官方原生 app 作者說 "in progress"（2026-09-24，topic 132557 #7），會不會接 HealthKit，**未找到來源**。

**API、OAuth、webhook**

- 多人 app 必須用 OAuth："Apps intended to be used by more than one person should use OAuth and Bearer tokens."（已驗證，topic 609）
- 2026-08-28 起改成線上申請表："You can now apply online, instead of sending an email."；核准前不能跑 OAuth："Please note that you will not be able to do the OAuth flow until it is approved"（已驗證，topic 2759）。核准標準**未找到來源**。
- token 沒有 refresh："it doesn't use refresh tokens, only access tokens."（作者 2021，已驗證）。版主說 token 不會過期（摘要）。一個 token 只能存取授權者本人的資料（作者 2023，已驗證）。
- **webhook**（已驗證，topic 2759、cookbook 80090、前端字串）：
  - 類型有 `ACTIVITY_UPLOADED`、`ACTIVITY_ANALYZED`、`ACTIVITY_UPDATED`、`ACTIVITY_DELETED`、`WELLNESS_UPDATED`、`CALENDAR_UPDATED`、`SPORT_SETTINGS_UPDATED`、`CONNECTED_SERVICE`、`APP_SCOPE_CHANGED` 等。
  - "Note that activity webhooks are not delivered for Strava activities."
  - "It will retry 8 times with exponential backoff of 1, 2, 4, 8 ... 256 seconds." → 約 8.5 分鐘後放棄（推估），所以要每天輪詢補漏。
  - `CALENDAR_UPDATED` 會帶 `oauth_client_id` 和 `external_id`，可以分辨哪些課表是我們推的。
- **限流**（已驗證，topic 609）："The default daily rate limit is 100/user per day up to 500 users (max 50000 requests), with a minimum of 5000."；"The requests per 15 minute limit is 1/8 of the daily limit with a minimum of 2500."；"There is an additional limit of 10 calls per second per IP address."；超過 500 人要寫信給 support@intervals.icu。
- 原始檔：`GET /api/v1/activity/{id}/file`，作者 cookbook："To download the original activity file (fit, gpx or tcx) gzip compressed"（已驗證）。
  - **Garmin 的檔案被過濾**，作者 2026-03-12："Unfortunately Garmin now does some filtering on the files they return via the API. The file is not the same as what you can download manually from Garmin Connect"；2026-03-15 說 Garmin 確認是刻意的（已驗證，topic 124287）。使用者回報少了 Training Effect、VO2max 等 Garmin 自家指標（摘要）。逐秒的心率、配速、GPS、功率應該不受影響（推估）。
  - Huawei："The Huawei API does not support fit file export."（已驗證）
- 活動列表的 `limit` 沒有公開上限；作者建議用 `oldest`／`newest` 每 1～3 個月分段抓（已驗證）。

**課表推到手錶**

- Garmin："Intervals.icu will upload your next week of planned workouts."，改計畫會自動更新（已驗證，topic 1521）。
- COROS：作者："Intervals.icu pushes the next week of workouts to Coros whenever you make changes"（已驗證，topic 37006 #92）。在 intervals.icu 刪掉的課表不會從 COROS 行事曆消失（使用者回報，摘要）。
- Suunto：用 SuuntoPlus Guides，"Step targets using power, heart rate, pace and cadence are supported."（已驗證，topic 9560）
- Polar 不行，作者："uploading planned workouts is still not supported."（已驗證，topic 68263 #3）
- Apple Watch：第三方 Intervals Companion 的 App Store 頁："Convert planned workouts from your Intervals.icu calendar and send them directly to Apple Watch"，並註明 "an independent hobby project, not affiliated with or endorsed by Intervals.icu"（已驗證）。HealthFit 的課表匯入要它的 Fanatical Supporter 方案（第三方開發者說法，已驗證）。
- 文字格式：心率只有 `% HR`、`% LTHR`、`Z2 HR`，**沒有絕對 bpm**；2025-11 起支援絕對配速（已驗證）。

**條款**（API Terms and Conditions，Effective Date: October 23, 2025，今天沒有改版，已驗證，topic 114087）

- §1："We grant you a non-exclusive, worldwide, royalty-free, perpetual license to access and use the API for any lawful purpose, including commercial use."
- §1.1："if your application displays information derived from Garmin-sourced data, you must display attribution to Garmin in the form and manner required by Garmin's brand guidelines."
- §2："Do not abuse the API."；§4："We may suspend or terminate access if you violate these Terms, with 7 days' notice where possible."；§7：改版提前 30 天 email 通知；§8："These Terms are governed by the laws of South Africa"。
- 全文沒有 AI、跨使用者分析、轉售的條文（已驗證）。
- 公司在英國（"Intervals.icu Ltd. … London"），準據法卻是南非，簽約前值得問（推估）。
- 隱私政策："Our Service does not address anyone under the age of 18"（已驗證）→ 走這條路的使用者要滿 18 歲（推估）。

**Garmin 品牌指南**（Garmin Developer API Brand Guidelines，V 6.30.2025，已驗證）

- 下游："All commercial uses of Garmin device-sourced data that is shared, exported or transmitted beyond an application must include a Garmin attribution. This includes sharing data via file formats (such as CSVs or PDFs) or digital interfaces (such as APIs or webhooks)."
- AI 與彙總："All uses of Garmin device-sourced data as an input to analytics, algorithms, machine learning models, artificial intelligence or combined, aggregated or blended with other sources … must include a Garmin attribution."；可接受的範例文字："Insights derived in part from Garmin device-sourced data."
- 所以 Garmin 是「規範」而不是「禁止」partner 把資料交給下游（推估）。Garmin 有沒有另外書面同意 intervals.icu 轉出，仍沒有公開證據。

**休眠**

- 作者："Your account was set to DORMANT because you hadn't been seen on Intervals.icu for 90 days and are not a supporter. Intervals.icu does not process files for dormant accounts"（已驗證，topic 112826）。休眠前 7 天會寄提醒（已驗證，topic 120715）。
- 只透過 API 存取算不算 "seen"：作者沒有明講。2026-08 有教練說 "I pull their data through the API into my own coaching app … several of their accounts went dormant"，作者沒有反駁，只回："If an athlete has a coach on Intervals.icu and the coach is active, then they are not made dormant."（已驗證，topic 130858）→ 推估：API 存取不算。
- OpenAPI 有 `icu_last_seen` 欄位可以監控（已驗證）。

**費用與競爭**

- 定價頁：Free "$0 forever"、Supporter "$4 /month"（已驗證）。約 NT$127，比我們的 NT$99 還貴（推估，匯率 31.795，同 SP-321）。
- 開發者費用：沒找到任何收費、分潤或要求使用者是 Supporter 的說法；條款寫 royalty-free。作者在考慮「用 API 建帳號的付費白牌方案」（已驗證）。
- 作者對 AI 教練 app 的態度是正面的："Try searching for "AI Coach", this is an exciting space!"（已驗證，2026-01-08 新聞）
- 但作者也在做官方 MCP："We are working on an official MCP server. It is at the spec stage currently."（已驗證，2026-09-09）→ 之後它自己的 AI 功能可能和我們重疊（推估）。

### 3.2 Nolio（法國）

- 支援 Garmin、COROS、Suunto、Polar、Apple Watch（經 Nolio app）等 20 多個來源（已驗證，connectors 頁）。
- API：OAuth 2.0；申請 "A quick form and you're good to go."，但 wiki FAQ："We will evaluate each requests"（已驗證）。
  - 個人 app 最多 5 位使用者；"The multi-user application is disabled by default, you need to contact us when you want to enable it after testing with your personal app."（已驗證）
- 檔案：`file_url` 是 "Url to download the .fit or .tcx file associated to the workout, valid only for one hour"（已驗證）；另有 streams。
- 課表：`POST /create/planned/training/`，可帶 `structured_workout`（已驗證）。平台會推到手錶："Structured workouts created in Nolio are automatically sent to compatible devices like Garmin, Coros or Suunto"（已驗證）。經 API 建的課表會不會一樣推過去，**未找到來源**。
- 限流與 webhook：上面 §2.2；"A webhook mechanism is available to avoid querying the API to see if there's new data."（已驗證）
- 條款：Terms of Use（2025-07-21）沒有 API、AI、商業使用的條文；**沒有公開的開發者條款**（未找到來源）。
  - 隱私政策歡迎使用者自接 AI："You can connect an artificial intelligence assistant of your choice (for example Claude or ChatGPT) to your Nolio account"（已驗證）。
  - 資料 "only shared with third parties at your initiative and with your explicit consent"（已驗證）。
- 費用：Athlete "0€ forever!"、Premium "6,90€ /month"；方案表的「API access」只有 Premium 打勾（已驗證）。這是指使用者自己用 API，還是第三方讀取也要，**未找到來源**。如果是後者，每位使用者每月多付的錢比 NT$99 還高（推估）。

### 3.3 TrainingPeaks

- **不收新夥伴**："We are not accepting any new API partners at this time, while we do some maintenance on our API systems."（已驗證，https://api.trainingpeaks.com/request-access ）。web.archive.org 從 2022-01-31 起的快照都是同一句（已驗證）。
- 不給個人："At this time access to the API is not available for personal use."（已驗證，官方 blog，2026-09-12 更新）
- 沒有原始 FIT 下載端點；最接近的 `details` 時間序列要 "Athlete needs to be premium athlete."（已驗證，GitHub PartnersAPI wiki）
- 推未來課表：Basic 使用者 "will result in 403 status code"（已驗證）。
- API 條款（已驗證，同 request-access 頁）：
  - §4a "Compete with TrainingPeaks products and services in any manner, either directly or indirectly."（禁止）
  - §3g "No data shall remain in your cache longer than permitted by the cache header, or if a cache header is not present, seven days."
  - §5e 不得 "build databases, or otherwise create permanent copies of Content"
  - §5c 給其他使用者看要 "explicit opt-in consent from that user"
  - §4a 允許 "charging for the provision of functionality not provided by the TrainingPeaks Platform"
- Premium：US$19.95／月、US$134.99／年（已驗證），約 NT$358～634／月，是我們月費的 3.6～6.4 倍（推估）。
- 商標：TSS、IF、NP 是 TP 的註冊商標，"Any metrics with a trademark symbol Ⓡ were developed and are owned by TrainingPeaks."（已驗證）。repo 的 `views/` 有用到 "TSS"，商業化前要檢查用語（推估）。

### 3.4 Strava

條款是 API Policy（Effective Date: June 1, 2026），原文已驗證：

- §5.3 "You may not use the Strava API Materials or Strava Data, directly or indirectly, in connection with the development, training, evaluation, or operation of any AI Application."
- §5.4 "You may not process or disclose Strava Data—even publicly viewable Strava Data—including in an aggregated, de-identified, or anonymized manner, for the purposes of analytics, analyses, customer insight generation, or product or service improvements."
- §6.2 "You may not retain Strava Data in your cache for longer than seven (7) days."
- §5.5 禁止存進 "Persistent Index"。
- 開發者要有訂閱："A Strava subscription is a prerequisite for creating an app."
- 沒有原始檔端點，也沒有課表端點（已驗證：API reference 全文）。

光是「保存訓練史來算 CTL」就會碰到 §6.2 和 §5.5（推估）。結論和 `data-hubs.md` §3.1 一樣，而且更確定。

### 3.5 Final Surge、Runalyze、SportTracks、Golden Cheetah

- **Final Surge**：找不到任何開發者 API（未找到來源：網站導覽、`/api`、`/developers`、`api.`／`developer.` 子網域、說明中心、條款全文都查過）。條款："solely for your personal, noncommercial use."（已驗證）。它是競品參考：運動員免費，能推課表到 Garmin、COROS、Suunto、Apple Watch、Amazfit（已驗證）。
- **Runalyze**："At the moment, no third-party app has read access. This will remain the case for a while."（已驗證，2025-05-27 官方部落格）。Personal API："Just simpler for private and own use."（已驗證）。
- **SportTracks**：API 只有 JSON 軌跡陣列；"Planned Workouts -Reading planned workouts."，只能讀（已驗證）。沒有公開的 API 條款、限流、webhook 文件（未找到來源）。
- **Golden Cheetah**："GoldenCheetah is a desktop application for cyclists and triathletes and coaches"（已驗證）。OpenData 是 CC0 靜態資料集，收集伺服器已下線（已驗證）。

### 3.6 Apple Health／WorkoutKit 與 Health Connect（細節由 SP-344 處理）

- HealthKit 只在裝置上："The user’s device stores all HealthKit data locally."（已驗證）。iCloud 裡的健康資料是端對端加密，Apple 自己也沒有金鑰（已驗證）。
- Health Connect："Device-centric (on-device)"、"No OAuth required (on-device permissions)"（已驗證，Google 遷移指南 2026-09-10）。
- 推課表到 Apple Watch：要原生 iOS app 用 WorkoutKit："sync scheduled compositions to Apple Watch"（已驗證）。WWDC23 說一次最多 15 筆、使用者看得到前後 7 天（已驗證，2023 年的說法）。
- Health Connect 預設只能讀第一次授權前 30 天："By default, all applications can read data from Health Connect for up to 30 days prior to when any permission was first granted."（已驗證）
- 條款重點：
  - Apple 5.1.2(i)："You must clearly disclose where personal data will be shared with third parties, including with third-party AI, and obtain explicit permission before doing so."
  - Apple 5.1.3(ii)：不得存進 iCloud。
  - Google Play："Data use should be limited to providing or improving the appropriate use case or features visible in the application's user interface."
  - 以上都已驗證。
- 手錶品牌寫進 Apple Health 的大多是摘要：
  - COROS 的勾選清單沒有 Workouts（已驗證）。
  - Polar 只寫摘要與運動中心率、沒有路線（已驗證）。
  - 所以「Garmin／COROS → Apple Health → 我們」品質很差，Apple Health 只適合 **Apple Watch 使用者**（推估）。
- 不寫原生 app 的橋接：
  - **Intervals Companion**（免費、第三方）："Automatically sync workouts recorded on Apple Watch or iPhone to Intervals.icu"（已驗證）。
  - **HealthFit**（US$6.99 買斷）：可上傳到 intervals.icu、TrainingPeaks、Runalyze、Nolio、COROS 等（已驗證）。
  - **Health Auto Export Premium**（US$6.99／年）：可以把 JSON（含路線）POST 到任何 URL，但 "Automations will only run during periods when your device is unlocked."（已驗證）。
  - 這三個 app 產生的 FIT 是依樣本重建的，**不是手錶原始檔**（推估）。

### 3.7 商用聚合 API

- 價格（已驗證）：
  - Terra："Subscriptions start from $399 per month billed annually, or $499 per month billed monthly."；推課表附加 "$99/month"。
  - Junction："Launch is the cheapest plan on the market at $300 a month"。
  - Sahha $299（年繳）／$399（月繳）、ROOK $399、Spike $450 起、Thryve €499。
- 預算是每月 NT$100～1,000（約 US$3～31）。最便宜的 Junction 要約 100 位訂閱會員才付得起，Terra 讀加推要約 190 位，還沒扣金流與稅（推估）。
- Terra 接 Garmin 要自備 key："Garmin requires every company that connects Garmin users to have its own app on the Garmin developer portal"（已驗證）。Garmin 暫停受理期間，新客戶經 Terra 也接不到 Garmin（推估）。
- 能拿原始 FIT、能推課表的只有 Terra（已驗證／未找到來源：其他家文件都沒有）。

---

## 4. 落差：對 TrailRunCoach 的意思

### 4.1 intervals.icu 的額度夠不夠（推估）

用 SP-321 的用量（每位會員每天約 0.6 筆活動）估每位使用者每天的呼叫：

| 情境 | 呼叫 | 每人每天 |
|---|---|---|
| 訂閱：webhook 驅動 | 每筆活動讀摘要＋下載檔案約 2～3 次；每天輪詢補漏 1 次；課表有變就 bulk upsert 1～2 次；wellness 1 次 | 約 5～7 次 |
| 免費：夜間一次 | 列表 1 次＋檔案約 0.6 次＋wellness 1 次 | 約 3 次 |
| 新使用者首次回填 | 每 1～3 個月一次列表，加每筆活動 1 次；一年約 220 筆 | 一次約 225 次 |

- 平常的用量遠低於每人每天 100 次。
- 會碰到上限的是**首次回填**：
  - 100 人同時加入要約 22,500 次，超過 app 每天的總額（100 人時是 10,000）。
  - 回填要排隊、分幾天做，SP-320 的全域佇列可以做這件事。
- COROS 經 intervals.icu 只有 3 個月歷史，更早的要另外補：
  - 使用者匯出 ZIP 後手動匯入（SP-323）；
  - 或走 COROS MCP（每天 50 個檔，一年的量約 5 天補完；是否可用看 SP-331）。

### 4.2 會員分級：免費夜間一次、訂閱即時

- 走 hub 之後，「抓資料」幾乎不花我們的錢：webhook 本來就會來，呼叫次數也夠。
- 真正的成本是**收到之後的運算**：SP-321 估每筆活動約 18 CPU 秒（改善後）。
- 所以兩層的差別要做在「什麼時候處理」，不是「什麼時候抓」（推估）：
  - **免費**：webhook 進來先排進佇列，夜間批次再處理；看得到「已收到，今晚分析」。
  - **訂閱**：收到就處理（即時），課表改了馬上推到 intervals.icu 行事曆，再由它推到手錶。
- 推課表：寫進 intervals.icu 行事曆本身很便宜（每次 1 個呼叫），按 SP-321 的決定留在訂閱層。
- 文案：intervals.icu 這條路確認可行後（§5.2 的信），「透過 intervals.icu 自動同步 Garmin、COROS、Suunto」可以寫，要附 Garmin 標示。SP-321「不寫自動同步」的前提就可以解除（推估，要使用者決定）。

### 4.3 使用者要多做什麼

| 使用者 | 要做的事 | 額外花費 |
|---|---|---|
| COROS／Garmin／Suunto | 建 intervals.icu 帳號 → 連手錶平台 → 勾「Upload planned workouts」→ 在我們這邊用 OAuth 授權 | 0；但每 90 天要上 intervals.icu 一次，否則休眠 |
| Apple Watch | 另外裝 Intervals Companion（免費、第三方 hobby 專案）或 HealthFit | 0～US$6.99；HealthFit 推課表要再付 US$9.99 |
| Polar | 同 COROS，但課表推不過去（Polar API 不支援） | 0 |
| 想免休眠 | 付 intervals.icu Supporter | US$4／月，**比我們的月費還貴** |

- 心率目標只能用 % LTHR，intervals.icu 裡的 LTHR 要和我們一致。由我們幫使用者寫進去還是請使用者自己設，實作時再定（推估）。
- 18 歲以上才能用 intervals.icu（隱私政策）。
- 減少休眠的做法（推估）：
  - 每天讀 `icu_last_seen`，到第 83 天時提醒使用者上站一次；
  - 或問作者「教練帳號豁免」能不能用在商業服務。這等於我們有一個帳號能看、能改所有人的行事曆，作者設想的是真人教練團隊，不先問不要做。

### 4.4 AI 與跨使用者分析（接 SP-326）

| 來源 | AI 教練（使用者自帶金鑰） | 去識別化跨使用者分析 |
|---|---|---|
| intervals.icu | 條款沒限制 | 條款沒限制 |
| 其中 Garmin 來源 | 要標示 Garmin（品牌指南明寫 AI 與彙總都要） | 要標示 Garmin |
| Nolio | 未找到來源 | 未找到來源 |
| TrainingPeaks | 條款沒寫 | 要 opt-in，而且卡在「快取 7 天、不得建資料庫」 |
| Strava | **禁止** | **禁止** |
| Apple Health（經原生 app） | 要事先揭露並取得明確同意 | 只能回饋本 app 的健身功能，換用途要再同意 |
| Health Connect（經原生 app） | 模糊：轉第三方只列三種例外 | 「aggregated for internal operations」有空間 |
| Polar AccessLink | 資料給外部（例如 LLM）要使用者明確同意 | 要標示 Polar |

- 經 intervals.icu 拿到的 Apple Watch 資料，受 HealthKit 條款約束的是橋接 app；我們受 intervals.icu 條款約束。但資料本質上仍是健康資料，同意與揭露照做比較穩（推估）。
- 實際上 Strava 來源的活動在 intervals.icu 只有 stub，本來就拿不到，不會誤用到（已驗證："Strava does not want Intervals.icu to give out any Strava activities via the API so only a stub is returned."）。

### 4.5 資料品質與去重

- 經 intervals.icu 拿到的檔案：
  - Garmin 的被過濾過（沒有 Garmin 自家指標、沒有課表步驟）；
  - Huawei 是重新產生的；
  - Apple 是橋接 app 重建的；
  - COROS 是不是原檔，推估是，要實測。
- 我們的分析用逐秒心率、配速、GPS、功率，被過濾的欄位目前用不到（推估）。
- 同一次運動可能從 COROS 直連、intervals.icu、手動匯入各來一次：
  - `dedup.py` 的 ±2 分鐘可以直接用；
  - intervals.icu 的 `source`、`device_name`、`external_id` 可以讓對應更準。
  - 這部分交給 SP-344。

### 4.6 對照：為什麼不走「伺服器代使用者登入非官方 API」

- Garmin 使用者條款（2026-04-01）明文禁止 "Using any process, whether automated or manual, that accesses, copies, or scrapes content from the Site through any means not purposely made available through the Site"，罰則包括停權使用者帳號、訂閱不退費（已驗證，`garmin.md` §2.4）。
- TrainingPeaks 使用者條款（Last Updated July 28, 2026，已驗證）：
  - §11(e) "you will not allow others to access and use your account;"
  - §23 "Access or attempt to access the Sites or Services by any means other than the interface provided or authorized by us;"
  - §23 "Access the Sites or Services by means of automated process, spiders, bots, or similar device;"
  - §14 違反可以停用帳號。
- 現有 `tp_client.py`（借 WKO5 的 client、網站表單登入、打沒有公開文件的 `tpapi`）符合上面的描述（推估）。只能算擁有者自用，不能當付費方案的資料來源。
- 平台偵測靠的是它自己伺服器看到的東西（登入模式、TLS 指紋、IP），跟我們的程式碼公不公開無關（推估）。
- 商業版的 NAS 是一個 IP 替很多帳號登入，比每人在家用開源套件更容易被認出來（推估）。被停權的是付費會員自己的手錶帳號。
- COROS 的條款定位等 SP-331。

### 4.7 跟 `data-hubs.md`（2026-10-01）不同的地方

| 項目 | data-hubs.md | 今天 |
|---|---|---|
| intervals.icu 是否只能輪詢 | 沒提 webhook | **有 webhook**（§3.1） |
| intervals.icu 原始檔壓縮 | 未驗證 | gzip（已驗證） |
| 沒有 refresh token | 未逐字驗證 | 已驗證（作者 2021） |
| OAuth 申請 | 寄信、等核准 | 2026-08-28 起改線上表單 |
| 定價頁那句 "syncs with multiple devices (…)" | 有 | 措辭改了，今天頁面上找不到原句 |
| Garmin 檔案 | 「原始活動檔」 | 被 Garmin 過濾過（2026-03） |
| COROS 歷史 | 沒寫 | 只有 3 個月 |
| 課表能不能寫絕對配速 | 沒提 | 可以（2025-11） |
| Runalyze 限流 | 引 "Currently, there is no rate limit" | 原句找不到了；changelog 寫每小時 40／150 |
| Runalyze 原始 FIT | 未驗證 | 有，但只限 Personal token；第三方讀不到 |
| Strava | §5.3 AI 禁令 | 另外還有 §5.4、§5.5、§5.8、§6.2 |
| TrainingPeaks | 「有報導說不收新夥伴」（未驗證） | 申請頁原文已驗證，2022 年起就這樣 |
| Nolio | 未細查 | FIT、webhook、課表 API 已驗證；條款與費用待問 |

`data-hubs.md` 沒有改，以本文件為準；之後修那份文件時照這張表改。

### 4.8 能不能批次同步、把活動存回本專案的儲存空間

| | intervals.icu | TrainingPeaks |
|---|---|---|
| 能不能長期存 | **可以**。條款是 "perpetual license … including commercial use"，而且 "you may integrate, modify, distribute, and sublicense outputs derived from the API without restriction"，全文沒有快取或保存期限（已驗證）。Garmin 那層只要求標示（§3.1）。作者 2021 年說過 "Garmin don't have those kind of unpleasant "we can instruct you to delete everything" terms in their API legals."（已驗證，2021 年的說法） | **不行**（就算拿到 Partner API）。§3g 快取最多 7 天；§5e 不得 "build databases, or otherwise create permanent copies of Content"（已驗證） |
| 拿得到什麼檔 | 原始檔 `GET /activity/{id}/file`（gzip，每筆 1 次呼叫）。另一個 `POST /athlete/{id}/download-fit-files` 打包的是 intervals.icu **重新產生**的 FIT，不是原檔（已驗證：OpenAPI 摘要） | Partner API 沒有 FIT 下載端點；只有 Premium 使用者的時間序列 JSON（已驗證） |
| 批次怎麼抓 | 用 `oldest`／`newest` 每 1～3 個月分段列活動，再逐筆下載（作者建議，已驗證） | 每次查詢最多 45 天（已驗證） |
| 限制 | app 每天總額 max(5,000, 100×人數)、15 分鐘 1/8、每 IP 每秒 10 次；首次回填要排隊（§4.1） | 沒有硬性上限，但條款保留限制的權力 |
| 歷史多長 | 已經在 intervals.icu 的都拿得到（推估：它自己存一份）；它能從手錶平台抓的：Garmin 1 年（另有封存匯入）、COROS 3 個月、Polar 只有新的、Suunto 全部 | 文件沒寫；Garmin 連 TP 時可回溯 5 年 |
| 休眠帳號 | 新活動不處理，等於斷流（§3.1） | — |
| 現在能不能用 | 要先申請 OAuth app；個人自用可以先用 API key（`intervals_probe.py` 已經能下載原始檔） | 現有 `tp_client.py` 可以批次下載 FIT 並存進本專案，但違反使用者條款（§4.6），只能擁有者自用 |
| 合規的替代 | — | 使用者在 TP 網站批次匯出："Enter a 12-month or less time frame and click "export" to get a folder of all your workout files from that time period."（已驗證），再用手動匯入（SP-323）。這是使用者自己的資料，存回本專案沒問題（推估：TP 條款 §16 "You retain all rights in and to your User Content."） |

結論：**經 intervals.icu 可以批次同步、長期存在本專案（NAS 或之後的 R2）**。使用者撤銷授權或要求刪除時怎麼處理，照我們自己的隱私政策（SP-326）。TrainingPeaks 只能走使用者自己匯出。

### 4.9 推課表：目前專案的功能，經 intervals.icu 哪些能用

現在只有 COROS 推課表在運作（`workout_targets/coros.py`，非官方 Training Hub）。下表是同樣的功能改走 intervals.icu 會怎樣；最後一欄是 TrainingPeaks（只當對照，Partner API 拿不到）。

| 現有功能（COROS 直連） | 經 intervals.icu | 經 TrainingPeaks（對照） |
|---|---|---|
| 功率目標（絕對瓦數） | **能**：`220w`、`200-240w`（已驗證）。推到 Garmin 時，Garmin 的功率和 COROS 不同（`garmin.md` §3.3） | 能（結構化課表） |
| 心率目標（絕對 bpm） | **不能用 bpm**，只有 `% LTHR`、`% HR`、`Z2 HR`（已驗證）。要讓 intervals.icu 的 LTHR 和我們一樣；OpenAPI 有 `PUT /athlete/{id}/sport-settings/{id}`，欄位含 `lthr`、`max_hr`（已驗證），由我們寫進去應該可行（推估，要 SETTINGS 寫入權限） | 能 |
| 配速目標 | **能**：絕對配速 `5:00/km Pace`（2025-11 起，已驗證） | 能 |
| 重複組（一層 ×N） | **能**；"Nested repeats are not supported."（已驗證），和我們現在一樣 | 能 |
| 距離段 | **能**：`2km`、`500mtr`（已驗證）。現在 stub 寫「不支援」是因為轉換器還沒做（`workout_targets/intervals.py:20`） | 能 |
| 「直到按下計圈」 | **找不到可用語法**（未找到來源）→ 先改成預估時間 | 未查 |
| 「負荷（TL）」結束條件 | **不能**；只有整堂的 `load_target`（已驗證：Event 欄位）→ 改送預估時間（stub 已經這樣做） | 不能（推估） |
| 肌力課 | 能放進 intervals.icu 行事曆；會不會推到 Garmin／COROS 手錶，**未找到來源** | Garmin 不收："Strength, Day Off, and Brick workouts are not supported."；COROS 收（已驗證） |
| 推未來 N 天（`plan.auto.push_days`，預設 7） | intervals.icu 推「下一週」到 Garmin、COROS（已驗證）。設超過 7 天時，多的部分推估到不了手錶 | Garmin 15 天、COROS 7 天、Apple 7 天；未來日期要 Premium |
| 課表改了就換掉（冪等） | **能**：`events/bulk?upsert=true`＋`external_id`；Garmin 那邊 "Workouts are automatically updated whenever you make changes"（已驗證） | 能 |
| 刪掉或錯過的課從手錶移除 | intervals.icu 這端能刪（`bulk-delete` 用 `external_id`，已驗證）；**COROS 行事曆上刪不掉**（使用者回報，摘要）；Garmin 推估會跟著更新 | 未查 |
| 賽事計算機單堂課 | 排在某天：能；只放「課表庫」（`POST /athlete/{id}/workouts`）：能存，但推估不會到手錶 | — |
| 讀回手錶上的課表 | 讀 intervals.icu 行事曆可以；手錶那端的狀態讀不到（推估） | — |
| 讀 COROS 心率設定（LTHR、最大心率） | 讀得到 intervals.icu 自己的 `lthr`、`max_hr`（已驗證：欄位），**不是手錶上的設定**（推估） | — |
| COROS 跑後自評（SP-231，feelType） | **拿不到**（推估）：這個值 "is in neither the FIT file nor the activity list"（`backend/engine/coros_rpe.py` 說明），intervals.icu 抓的是檔案。Activity 雖然有 `feel`、`icu_rpe` 欄位，但那是 intervals.icu 自己的；COROS 的有沒有帶進去，**未找到來源** | — |
| COROS TL（SP-37） | **拿不到**（推估）；只有 intervals.icu 自己算的 `icu_training_load` | — |
| Polar、Apple Watch、Suunto | Polar 不能推；Apple 靠第三方 app；Suunto 用 SuuntoPlus Guides（2026-08 有心率目標不顯示的回報，摘要） | Polar、Suunto、Apple 都能 |

重點：

- 主要的課表內容（間歇、心率、配速、功率、距離、重複）都能推。
- 會失去或降級的有：
  - 絕對 bpm（改送 % LTHR）；
  - 計圈步驟與 TL 結束條件（改送時間）；
  - COROS 上刪不掉的課；
  - COROS 自評與 TL。
- 後兩項只有 COROS 直連拿得到。所以如果 SP-331 確認 COROS MCP 可用，COROS 使用者走 COROS MCP 會保留比較多功能。MCP 是否提供自評與 TL，那份文件會查。

---

## 5. 結論與後續

### 5.1 建議的備案順序（拿不到 COROS／Garmin 官方 API 時）

| 順位 | 路線 | 涵蓋 | 前提 | 用在 |
|---|---|---|---|---|
| 1 | **intervals.icu OAuth（hub 模式）** | 活動＋檔案：Garmin、COROS、Suunto、Polar、Wahoo、Amazfit、Huawei；Apple Watch 經第三方 app。課表：Garmin、COROS、Suunto | 申請核准；§5.2 的信回覆沒有障礙；做 Garmin 標示；處理休眠 | 免費（夜間處理）與訂閱（即時＋推課表）兩層的預設同步 |
| 2 | **手動匯入 FIT／ZIP（SP-323）＋桌面自動匯入（SP-324）** | 所有品牌；Garmin「Export Your Data」、TP 批次匯出、COROS 匯出 | 無 | 一直保留：補歷史、不想多開帳號的人、hub 出問題時的退路 |
| 3 | **Nolio** | 跟 intervals.icu 差不多，多了 Apple Watch 推課表（經它自己的 app） | 寫信確認條款、使用者要不要付 Premium | intervals.icu 被拒或條款改變時的第二個 hub |
| 4 | **原生 iOS app**（HealthKit＋WorkoutKit） | Apple Watch 的活動與推課表 | 開發者年費 US$99；App Store 審查；5.1.2(i) 的 AI 同意 | Apple Watch 使用者夠多時 |
| — | COROS MCP | COROS 的活動、FIT、課表 | SP-331 確認條款允許多人收費服務 | 如果可行，COROS 使用者直接用它，不必經 intervals.icu（比 3 個月歷史好、少一個帳號） |
| 不建議 | TrainingPeaks、Strava、Final Surge、Runalyze、SportTracks、Golden Cheetah、商用聚合 API、Health Connect | — | 條款、費用或技術上不可行（§3） | — |

### 5.2 後續（不在這張單做）

1. **寄信給 support@intervals.icu**（使用者寄）：
   1. 付費第三方 app 的 OAuth 核准條件，有沒有費用或分潤；
   2. 只透過 API 存取算不算 "seen"；教練帳號豁免能不能用在商業服務；
   3. Garmin 來源資料除了標示，還有沒有其他限制；
   4. 活動列表 `limit` 的上限、首次回填的建議做法；
   5. 準據法（南非）和公司（英國）的關係。
2. **寄信給 Nolio**：
   1. 多人 app 用在收費服務的條件與費用；
   2. 使用者需不需要 Premium；
   3. Garmin、COROS 來源的活動與 `file_url` 是否都開放給第三方；
   4. AI 與去識別化跨使用者分析是否允許；
   5. 經 API 建立的課表會不會自動推到手錶。
3. **開實作單（信回來之後）**：
   - `intervals_client.py`（OAuth、webhook 端點、每日輪詢補漏、下載與解壓 FIT）；
   - `storage.SOURCES` 加 `intervals`；
   - 啟用 `workout_targets/intervals.py`：補距離段轉換器；用 sport-settings 把我們的 LTHR 寫進 intervals.icu；計圈步驟改送時間；
   - Garmin 標示（活動用 `device_name`，AI 回覆與圖表統一加註）；
   - `icu_last_seen` 休眠提醒；
   - 首次回填排隊。

   設計沿用 `data-hubs.md` §4.3。
4. **用語檢查**：TSS、IF、NP 是 TrainingPeaks 的註冊商標（§3.3），商業版是否改名或加註。
5. **`data-hubs.md` 照 §4.7 更新。**

---

## 6. 要使用者決定的問題

1. **商業版的預設同步要不要走 intervals.icu？** 代價是使用者要多開一個帳號、每 90 天上站一次（或付 US$4／月）。替代是商業版只給手動匯入，等官方 API。
2. **休眠怎麼處理？** (a) 讀 `icu_last_seen` 提醒使用者；(b) 建議使用者付 Supporter（比我們的月費貴）；(c) 先問作者教練帳號豁免能不能用。建議先 (a)，同時問 (c)。
3. **兩封信要寄嗎？** intervals.icu 與 Nolio 各一封，問題列在 §5.2。要我先擬稿嗎？
4. **Apple Watch 怎麼支援？** 先只靠第三方 app（Intervals Companion 或 HealthFit）經 intervals.icu；還是排進原生 iOS app 的計畫（年費 US$99、要過 App Store 審查）？
5. **會員分級改成「處理時機」來分可以嗎？** 免費：webhook 先排隊、夜間處理；訂閱：即時處理加推課表（§4.2）。
6. **intervals.icu 這條路確認後，文案可以寫「透過 intervals.icu 自動同步」嗎？** SP-321 原本的決定是 SP-331 有結論前不寫。
7. **現行非官方串接**：COROS 帳密登入與 `tp_client.py` 在商業版預設關閉、只留給自架與擁有者自用，對嗎？
8. **TSS 等用語**：要開單檢查 TP 的註冊商標嗎？
9. **走 hub 的 COROS 使用者沒有「跑後自評」與 COROS TL，可以接受嗎？** 還是 COROS 使用者一律等 SP-331 的 COROS MCP（§4.9）？

---

## 7. 重新驗證清單（動工前逐項重查）

1. intervals.icu：API 條款（topic 114087）有沒有改版；限流（topic 609）；OAuth 申請與核准（topic 2759）；webhook 類型與重送；休眠規則；官方 MCP 的進度。
2. intervals.icu 各來源的歷史範圍（COROS 3 個月是轉引 COROS 文件）與原始檔格式；Garmin 過濾的範圍。
3. Garmin API Brand Guidelines 的版本（今天是 V 6.30.2025）。
4. Nolio：API 條款、Premium 與第三方讀取的關係、經 API 建的課表會不會推到手錶。
5. TrainingPeaks：request-access 頁那句「不收新夥伴」是否還在。
6. Strava：API Policy 的版本；2027-06-01 的技術變更。
7. COROS MCP 的條款與費用（SP-331）。
8. Garmin Developer Program 是否恢復受理。
9. 聚合 API 的價格；Terra 的 Garmin 是否仍要自備 key。
10. Intervals Companion、HealthFit 的課表功能與價格；intervals.icu 官方原生 app 會不會接 HealthKit。
11. Suunto API 是否還收新申請（兩邊說法矛盾）。

---

## 參考（全部 2026-10-07 讀取）

- intervals.icu：
  - API 說明：https://forum.intervals.icu/t/api-access-to-intervals-icu/609
  - OAuth：https://forum.intervals.icu/t/intervals-icu-oauth-support/2759
  - Integration Cookbook：https://forum.intervals.icu/t/intervals-icu-api-integration-cookbook/80090
  - API 條款：https://forum.intervals.icu/t/intervals-icu-api-terms-and-conditions/114087
  - OpenAPI：https://intervals.icu/api/v1/docs
  - 定價：https://www.intervals.icu/pricing/ ；隱私政策：https://www.intervals.icu/privacy-policy/
  - 裝置與歷史：topic 1521、3906、37006、9560、24678、5149、68263、65949、107652、120529
  - Garmin 過濾：https://forum.intervals.icu/t/garmin-fit-fields-not-coming-in-possible-garmin-is-filtering-data-prior-to-sending-to-intervals-icu/124287
  - 休眠：topic 112826、120715、130858、5876
  - Apple：topic 67776、25320、5776、112136、124208、132557
  - AI 與 MCP：https://forum.intervals.icu/t/intervals-icu-news-2026-01-08/119193 、https://forum.intervals.icu/t/request-for-official-mcp-support-for-ai-tools-chatgpt-claude/126164
- Garmin：
  - API Brand Guidelines：https://developer.garmin.com/downloads/brand/Garmin-Developer-API-Brand-Guidelines.pdf
  - Developer Program：https://developer.garmin.com/gc-developer-program/overview/ 、https://developer.garmin.com/gc-developer-program/program-faq/
- Nolio：https://www.nolio.io/en/developers/ 、https://www.nolio.io/en/connectors/ 、https://www.nolio.io/en/pricing/ 、https://www.nolio.io/en/terms/ 、https://www.nolio.io/en/data/privacy/ 、https://github.com/NolioApp/NolioAPI-Documentation/wiki
- TrainingPeaks：
  - https://api.trainingpeaks.com/request-access （含 API Terms）
  - https://www.trainingpeaks.com/blog/an-update-on-trainingpeaks-partner-api/
  - https://github.com/TrainingPeaks/PartnersAPI/wiki
  - https://www.trainingpeaks.com/terms-of-use/ （2026-07 PDF）
  - https://www.trainingpeaks.com/pricing/for-athletes/
  - 說明中心文章 204070854、204070864、360041756752、48096960116749、204069974、221307448、204074014
  - https://www.trainingpeaks.com/learn/articles/glossary-of-trainingpeaks-metrics/
- Final Surge：https://www.finalsurge.com/privacy-and-terms-of-use 、https://www.finalsurge.com/pricing 、https://blog.finalsurge.com/
- Strava：https://www.strava.com/legal/api_policy 、https://www.strava.com/legal/api 、https://developers.strava.com/docs/rate-limits/ 、https://developers.strava.com/docs/webhooks/ 、https://developers.strava.com/docs/getting-started/
- Runalyze：https://runalyze.com/doc-api 、https://runalyze.com/help/article/personal-api 、https://blog.runalyze.com/allgemein-en/refactored-api/ 、https://runalyze.com/changelog 、https://runalyze.com/pricing
- SportTracks：https://sporttracks.mobi/api/doc 、https://sporttracks.mobi/partners 、https://sporttracks.mobi/pricing
- Golden Cheetah：https://github.com/GoldenCheetah/GoldenCheetah 、https://github.com/GoldenCheetah/OpenData
- Apple：
  - https://developer.apple.com/documentation/healthkit/protecting-user-privacy
  - https://developer.apple.com/documentation/workoutkit
  - https://developer.apple.com/videos/play/wwdc2023/10016/
  - https://developer.apple.com/app-store/review/guidelines/
  - https://developer.apple.com/support/terms/apple-developer-program-license-agreement/
- Google：
  - https://developer.android.com/health-and-fitness/guides/health-connect/migrate/migration-guide
  - https://developer.android.com/health-and-fitness/guides/health-connect/develop/read-data
  - https://support.google.com/googleplay/android-developer/answer/9888170
- 橋接 app：
  - https://apps.apple.com/us/app/healthfit/id1202650514
  - https://apps.apple.com/us/app/intervals-icu-companion/id6739638454
  - https://apps.apple.com/us/app/health-auto-export-json-csv/id1115567069
- 品牌官方：
  - COROS：https://support.coros.com/hc/en-us/articles/53181619102996-Build-on-COROS-MCP 、https://support.coros.com/hc/en-us/articles/53181766856724-Partner-API-Access
  - Polar：https://www.polar.com/accesslink-api/ 、https://www.polar.com/en/legal/polar-api-agreement
  - Suunto：https://apizone.suunto.com/
- 聚合：https://tryterra.co/pricing 、https://docs.tryterra.co/unified-api/garmin.md 、https://www.junction.com/pricing 、https://sahha.ai/pricing 、https://www.tryrook.io/pricing 、https://www.spikeapi.com/pricing 、https://www.thryve.health/pricing 、https://github.com/the-momentum/open-wearables
- 既有文件：`docs/research/data-hubs.md`、`docs/research/garmin.md`、`docs/plans/todo-multi-user-sharing.plan.md` §9、SP-321 的 `docs/research/membership-cost-tiers.md`（分支 `docs/membership-cost-sp321`）。
