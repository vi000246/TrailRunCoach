# 手錶平台官方 API、商業使用條款與備案（SP-331）

> 調查日期：2026-10-07。只做調查，沒有改程式。
> 標記：**已驗證**＝今天用 curl GET 抓了一手頁面（或官方 PDF、GitHub 原始檔、Zendesk 公開 JSON），英文引句用腳本逐字比對過（141 句全部吻合）；**已驗證（SP-343）**＝同一天 SP-343 調查已逐字核對過的引句，這裡直接重用；**摘要**＝搜尋摘要、論壇使用者說法或二手轉述；**推估**＝我的推論；**未找到來源**＝查不到，會寫試過哪些方法。
> 規則：沒有登入任何服務、沒有建帳號、沒有用任何金鑰，也沒有發任何寫入請求（COROS MCP 的動態註冊端點是 POST，所以沒呼叫）。網頁內容一律當資料看。
> 原始下載檔放在 session scratchpad 的 `sp331/raw/`，不進 repo。

## 摘要

1. **現在的 COROS 串接在 COROS 使用者條款裡是明文禁止的做法。** COROS Terms of Service（2023-04-23 版，美、歐、英、澳四個地區版本內容一樣）禁止讓第三方用你的帳密登入（§7）、未經書面許可用自動化方式存取（§4(e)）、把服務用於商業目的（§4(a)）、替第三方下載帳號資料（§4(c)）。違反的後果是授權自動終止、帳號可被封鎖、資料可被刪除，而且**使用者要替「別人用他的帳密存取」負賠償責任**（§20(e)）。COROS 給合作夥伴的 API 合約更直接寫「不得要求或保存使用者的 COROS 帳密」（§8.11）。所以商業版不能沿用現在的帳密登入。
2. **COROS 有一條免申請的官方路：COROS MCP。** 「No application or approval required」，每位使用者自己用 OAuth 授權，可以讀活動、下載 FIT（每人每 24 小時 50 個檔）、建課表並排到日期上（今天起 90 天內）。官方列的用途第一項就是「AI coaching apps」。限制：沒有 webhook（要自己輪詢）、**不能刪除或移動已排的課表**（要使用者在 COROS App 自己處理）、MCP 只說受 COROS 使用者條款約束，**沒寫能不能用在收費服務**。要寄信請 COROS 書面確認。
3. **COROS Partner API 的合約與技術文件其實公開得到。** 申請表（飛書）裡連著 COROS API Agreement 和 API Reference V2.1.1（2026-09）的 PDF。合約允許 AI 教練功能、去識別化的內部分析；免費；但締約方要是公司（「duly organized and validly existing」），對外發表群體報告要先取得 COROS 同意。申請表的使用者人數最低一級是「0-150」，有「Personal／Public」「Commercial／Non-Commercial」選項，**小團隊可以送件**，但審核標準與時間沒公開。
4. **Garmin 現在申請不到。** 2026-10-07 官方頁面仍是「Stay tuned for more updates on the program」，申請表從 2026 春天下架，到今天沒有重開日期、沒有排隊名單。就算重開，也只給企業，用 API 收費要 Garmin 書面許可。詳見 §3。
5. **其他平台**：Polar 任何使用者都能自助申請、免費、有 FIT，但不能推課表；Suunto 只給公司或組織，最多兩週審核、免費；Apple Health 沒有雲端 API，要做原生 iOS app；intervals.icu 條款明文允許商業使用，能同時接 Garmin、COROS、Suunto 並推課表。
6. **建議**：商業版以「手動匯入 FIT（SP-323／324）」當所有人的基本路線；COROS 使用者在 COROS 書面確認後改用 **COROS MCP OAuth**；Garmin、Suunto 使用者走 **intervals.icu**；有公司登記與使用者之後再申請 **COROS Partner API**；Garmin 等它重開。現在的 COROS、TrainingPeaks 帳密登入只留給自架或個人使用。
7. **對 SP-321 的影響**：免費層「每晚自動同步一次」在 COROS MCP 的額度內做得到（每人每晚一次列表加幾個 FIT）；訂閱層的「即時同步」在 MCP 下只能做到輪詢（沒有 webhook），「推課表到手錶」做得到但不能自動刪除或改期。COROS 回信前，文案照 SP-321 的決定，不寫自動同步和推手錶。

---

## 1. 現況：程式現在怎麼接

| 功能 | 做法 | 程式 |
|---|---|---|
| COROS 登入 | 帳號加 MD5 密碼 POST 到 Training Hub 的 `/account/login`，三個地區（EU、US、CN）輪流試 | `backend/sync/coros_client.py:1-7`（說明）、`:34-38`（三個主機）、`:182-208`（`_login`） |
| COROS 假裝成瀏覽器 | 固定送 Chrome 的 User-Agent | `coros_client.py:42-45` |
| 記住密碼、自動重登 | 密碼加密存在 `sync_state.coros_password_sealed`，token 過期時自動用密碼再登入 | `coros_client.py:349-358`（`save_password`）、`:366-397`（`relogin`）；`backend/db/models.py:128-135` |
| 台灣帳號的資料主機 | 註解：「a Taiwan account logs in on EU, its data is on teamapi = US」 | `coros_client.py:125-147`（`_detect_data_base`） |
| 同一帳號只能有一個 token | 註解：COROS 只保留最新的 token，第二次登入會讓第一個 token 失效 | `coros_client.py:150-153` |
| 讀活動、下載 FIT | `/activity/query` 分頁列表、`/activity/detail/download` 取 FIT 網址 | `coros_client.py:426-443`、`:445-488` |
| 跑後自評（SP-231） | 每筆活動讀一次 `/activity/detail/query` | `coros_client.py:512-533` |
| 推課表到 COROS | Training Hub 內部端點 `/training/program/add`、`/training/schedule/update`、`/training/program/delete`；計畫變動時會刪掉舊的、補上新的 | `backend/sync/coros_workouts.py:1-40`（端點說明）、`:700-830`（`TrainingHub`）、`:1080-1100`（`push_sessions`） |
| 自動推送的範圍 | 預設推未來 7 天（`plan.auto.push_days`） | `backend/engine/plan_auto.py:467`（`push_window`）、`:677`、`:809` |
| 推課表的 provider 介面 | 設定 `plan.push.provider`，預設 COROS；Garmin、intervals.icu 是不連網的空殼 | `backend/sync/workout_targets/__init__.py:18-25`；`coros.py:18`（`enabled = True`）、`garmin.py:18`、`intervals.py:18`（`enabled = False`） |
| TrainingPeaks | WKO5 的 OAuth client，或網站表單登入後用 `tpapi.trainingpeaks.com` | `backend/sync/tp_client.py:57-58`、`:167-175` |
| 行事曆訂閱 | 計畫課表輸出成 iCalendar，Google 日曆和 iPhone 行事曆可以訂閱；過去 14 天、未來 56 天 | `backend/engine/calendar_feed.py:1-24`、`:37-38`；`backend/api/calendar_feed.py:41-42`；`backend/main.py:165` |
| 每日自動同步 | 每天固定時間跑一次所有已登入的來源 | `backend/sync/scheduler.py:1-10` |

另外兩個背景事實：

- 正式環境在 NAS，**所有使用者共用同一個對外 IP**。現在的做法等於從同一個 IP、用同一個瀏覽器 User-Agent，替很多不同的 COROS 帳號登入。推估：在 COROS 看來很像撞庫或機器人，一旦 IP 被擋，所有使用者一起失效。
- `coros_client.py:150-153` 的註解寫 COROS 一個帳號只保留最新的 token。推估：使用者自己在網頁版 Training Hub 登入時，可能讓 app 的 token 失效，app 再用存的密碼重登，又把使用者的網頁登入踢掉。

---

## 2. COROS

### 2.1 COROS 使用者條款（Terms of Service）

來源：https://coros.com/terms （**已驗證**，2026-10-07）。`coros.com/eu/terms`、`/uk/terms`、`/au/terms` 的內容相同（**已驗證**）；`coros.com/tw/terms` 回 404，台灣沒有另外的版本。App Store 台灣區 COROS app 的開發者是「COROS Wearables Inc.」，和條款的締約方相同（**已驗證**）。COROS MCP 的 README 寫「Use of COROS MCP is subject to the COROS Terms of Service and Privacy Policy.」，連到的就是這份（**已驗證**）。

- 版本：「Last Updated: April 23, 2023」
- §2：「The Service may only be used for the intended purpose for which we make it available.」
- §4 Restrictions（節錄）：
  - 「(a) use the Service or any Content for any commercial purpose;」
  - 「(c) download or copy account information for the benefit of a third party;」
  - 「(e) use any robot, spider, scraper, or other automated means to access the Service for any purpose without our express written permission;」
  - 「(h) bypass any measures we may use to prevent or restrict access to the Service.」
  - 罰則：「Any unauthorized use automatically terminates the permissions and licenses granted to you by us.」
- §7 帳號：「You will not share your account information or username and password with any third party or allow any third party to log on to the Service using your account information.」
- §17 停權：「We may also block your access to the Service if (a) you breach these Terms;」；「If your account is terminated, we may delete data or your Submissions or otherwise disassociate it from you and your account」
- §20 賠償（使用者要賠 COROS 的情形之一）：「(e) any other party's access and use of the Service with your unique username, password, or other appropriate security code.」

**現行做法在條款裡的位置**：

| app 現在做的事 | 對到的條文 | 判斷 |
|---|---|---|
| 使用者把 COROS 帳密交給 TrailRunCoach，伺服器替他登入 | §7「allow any third party to log on」 | 明文禁止（已驗證條文；套用是推估，但很直接） |
| 程式自動登入、列活動、下載 FIT、推課表 | §4(e)「automated means … without our express written permission」 | 明文禁止，除非 COROS 書面許可。個人自用也一樣違反 |
| 向使用者收費 | §4(a)「any commercial purpose」 | 這條約束的是使用者本人；TrailRunCoach 不是締約方，但商業服務建立在使用者違約之上（推估） |
| 替 TrailRunCoach 下載使用者的活動 | §4(c)「download or copy account information for the benefit of a third party」 | 很可能涵蓋（推估） |
| 送 Chrome 的 User-Agent | §4(h)「bypass any measures」 | 看 COROS 有沒有用 User-Agent 擋程式；目前沒有證據（推估） |
| 存使用者的密碼 | COROS API 合約 §8.11「request or store End User COROS account credentials;」（見 §2.3） | 合約約束的是 Partner，不是現在的我們；但清楚表示 COROS 不接受（已驗證條文） |

**罰則的實際後果**：授權自動終止、帳號可被封鎖、資料可被刪除；使用者要負責賠償因別人用他帳密存取造成的損失。條款沒寫 COROS 會對第三方服務提告；TrailRunCoach 不是締約方，可能另外涉及美國法上的未經授權存取或誘使違約，這是推估，不是法律意見。本次**沒找到** COROS 因非官方 API 封號的案例（試過：搜尋「COROS unofficial API banned / blocked」、社群專案 README；只看到社群專案自己警告「Your account could potentially be affected」，**摘要**）。

### 2.2 COROS MCP（免申請的官方路）

**是什麼、誰能用**（**已驗證**，support 文章〈Build on COROS MCP〉，更新於 2026-09-10，https://support.coros.com/hc/en-us/articles/53181619102996 ）：

- 「No application or approval required.」
- 「Implement OAuth 2.0 in your app, and your users can connect their COROS account and grant access to their own data.」
- 官方列的用途：「AI coaching apps that analyze training and prescribe workouts」（另有 training dashboards、training plan generators 等）
- 限制：
  - 「Single-user access only (each user authorizes their own data)」、「All data access is scoped to the authorizing user only (single-user)」：一個 token 只能看授權者本人，教練看學生的模式做不到。
  - 「No webhook push notifications (your app polls the MCP)」
  - 「No two-way activity sync (COROS to your platform requires Partner API)」
- 費用：README FAQ「COROS MCP itself is free of charge.」（**已驗證**，https://github.com/coroslab/COROS-MCP ）
- 條款：README「Use of COROS MCP is subject to the COROS Terms of Service and Privacy Policy.」**沒有另外的開發者條款**（試過：README、README.zh、CHANGELOG、skill 目錄、四篇 support 文章；**未找到來源**）。

**能做什麼**（README 工具清單，**已驗證**；伺服器原始碼沒有公開，repo 只有文件和登入輔助腳本）：

| 類別 | 工具 | 對 TrailRunCoach 的用途 |
|---|---|---|
| 活動 | `querySportRecords`、`getActivityDetail`、`queryActivityLapData`、`downloadActivityFitFiles`、`queryActivityFitFileDownloadUrls` | 取代 `coros_client.py` 的列表與 FIT 下載 |
| 健康 | `querySleepData`、`querySleepHrv`、`queryRestingHeartRate` 等 | 選用；擁有者只在跑步時戴錶，這類資料多半沒有 |
| 訓練 | `createScheduledWorkout`、`updateScheduledWorkout`、`scheduleWorkout`、`createSingleWorkout`、`createTrainingPlan`、`updateTrainingPlan`、`queryTrainingSchedule` 等 | 取代 `coros_workouts.py` 的推課表 |
| 其他 | `queryUserInfo`、`queryDevices`、`queryFitnessAssessmentOverview` | 裝置型號（標示資料來源）、閾值配速 |

**限制（逐條，已驗證）**：

- FIT：「File retrieval and download-URL retrieval share a 50-file allowance per fixed 24-hour window for the connected platform user; later calls do not extend that window.」（README）。support 文章寫「.FIT files, including GPS tracks (capped at 50 file requests per day)」。
- 功率：「Power data is not part of the standard query set, even when a power meter is paired and recording.」（〈Connect Your COROS to AI〉）。推估：FIT 檔裡仍有功率，所以 app 一律下載 FIT 就不受影響。
- 排課日期：「Creating or scheduling a workout accepts dates from today through 90 days from today, inclusive.」；訓練計畫要 4–16 週、起始日在今天起 14 天內（「The start date can be anytime from today to 14 days from now.」）。
- **不能刪除或移動**：「Moving or removing standalone scheduled workouts, or quitting or deleting a plan, still requires the COROS App.」；support 文章：「However, you are not able to delete a plan or workout with AI; this must be done manually in the COROS app.」
- 計畫逐日覆寫：官方 skill 說明「Plan updates are incremental by day, not by individual workout: omitted days stay unchanged, but each submitted day replaces all workouts on that day.」、「A rest entry clears the whole day.」推估：如果 app 把課表做成一份 COROS 訓練計畫，就能用 `updateTrainingPlan` 把某天改成休息來「刪掉」那天的課，等於繞開「不能刪」的限制；要實測。
- 運動種類：新建課表只支援路跑、越野跑、自行車。肌力課不能建（README）。
- 歷史範圍、每分鐘呼叫上限、課表支援哪些強度目標（bpm、% 閾值心率、配速、功率）：**未找到來源**。這些寫在連線後伺服器回傳的工具定義裡（README：「Actual availability and detailed limits follow the refreshed tool definitions returned by your connected server.」），要登入才看得到，本次沒做。

**OAuth 與技術細節**（**已驗證**，2026-10-07 GET `https://mcp.coros.com/.well-known/oauth-authorization-server`、`/openid-configuration`、`/oauth-protected-resource/mcp`，以及 `mcpus`、`mcpeu`、`mcpcn` 三個分區）：

- issuer：從本機 GET `mcp.coros.com` 時回 `https://mcpus.coros.com`。三個分區各有自己的 issuer。README 說 `mcp.coros.com`「automatically switches to the best server based on the actual region of your account」。
- **台灣使用者在哪一區**：推估是 US（`mcpus`）。依據是 repo 註解（台灣帳號的資料在 US 主機）和本機查詢結果；要用台灣帳號實測。
- 端點：`/oauth2/authorize`、`/oauth2/token`、`/oauth2/device_authorization`、`/oauth2/revoke`、`/oauth2/introspect`、`/userinfo`。
- **動態註冊（DCR）**：有，`"registration_endpoint":"https://mcpus.coros.com/connect/register"`。
- PKCE 只支援 S256。token 端點的驗證方式包含 `none`（public client）和 `client_secret_basic` 等。grant 包含 `authorization_code`、`refresh_token`、`device_code`、`client_credentials`、`token-exchange`。
- scope：`"scopes_supported":["openid","mcp.tools","offline_access"]`。`offline_access` 就是 refresh token。
- COROS 官方的登入輔助腳本（`skill/coros_mcp_login_gateway/scripts/coros_mcp_login.py`，2026-09-21 版）是這樣做的：先 DCR 註冊成 public client（`"token_endpoint_auth_method": "none"`，redirect `DEFAULT_REDIRECT_URI = "http://127.0.0.1:43123/callback"`），再走 PKCE 授權碼流程，access token 快到期就用 refresh token 換新的。**已驗證**（原始碼）。
- access token 有效期：腳本在回應沒有 `expires_in` 時預設 3600 秒，實際值**未找到來源**。
- MCP 傳輸：stateless 的 streamable HTTP，不需要 `Mcp-Session-Id`（README FAQ 6、CHANGELOG 2026-06-24，**已驗證**）。

**多使用者的伺服器 app 能不能當 MCP client**：

- 文件寫的就是這個情境：「your users can connect their COROS account」、「Your user clicks "Connect COROS"」（**已驗證**）。
- Claude、ChatGPT 的連接器本來就是伺服器端、多使用者、每人各自 OAuth 的 MCP client，COROS 列它們為「verified platforms」（README：「We currently recommend using verified platforms first, such as ChatGPT, Claude, OpenClaw, Workbuddy, and Hermes.」，**已驗證**）。推估：TrailRunCoach 用同樣方式連，技術上沒有差別。
- 不確定的地方：DCR 接不接受 `https://coach.yichlin.com/...` 這種非 loopback 的 redirect URI。Claude、ChatGPT 能連，表示 DCR 至少接受它們的 HTTPS callback（推估）。要確認就得呼叫註冊端點（POST），本次依規定沒做。

**商業使用**：MCP 文件沒有提到收費服務能不能用。它受使用者條款約束，而使用者條款 §4(a) 禁止「any commercial purpose」、§4(e) 要求自動化存取要有「express written permission」。推估：〈Build on COROS MCP〉這篇官方文章本身就算是對 MCP 這個管道的書面許可，§4(e) 應該沒問題；§4(a) 約束的是使用者本人怎麼用 COROS，不是第三方 app 收不收費，但條款沒寫清楚。**結論：上線前要請 COROS 書面確認**（信件內容見 §9.3）。

### 2.3 COROS Partner API

**申請條件**（**已驗證**，〈Partner API Access〉更新於 2026-09-28，https://support.coros.com/hc/en-us/articles/53181766856724 ）：

- 「Established platform with demonstrated user base」
- 「Registered company with authorized technical representative」
- 「Agreement to COROS API Terms of Use」，文章另寫「Agree to our standard, non-discriminatory API Terms of Use, which include standard security requirements, data privacy compliance, and system rate limits.」
- 同一系列的〈Submit an API Application〉（更新於 2026-09-05）說法比較寬：「Rather than building custom integrations or conducting manual selection reviews, we grant access through our standard OAuth 2.0 API framework to any platform that satisfies our standard security and operational requirements.」〈Connect Your Data〉寫這些做法是「in alignment with EU data protection regulations (including GDPR and the EU Data Act)」。推估：COROS 為了符合歐盟 Data Act，把審核寫成客觀、不歧視的標準；「demonstrated user base」實際要多少人沒寫。
- 流程：「To apply, submit your platform details using the form below and email api@coros.com with your company details, technical contacts, and OAuth 2.0 redirect URIs.」

**申請表內容**（**已驗證**，GET 飛書表單頁 https://coros-teams.feishu.cn/share/base/form/shrcnLqSduZsaNhbvDJTO2x0Vlf ，從頁面內嵌的表單定義讀出題目，沒有填寫或送出）：

- 必填：Company Name、Company Owner Name and Title、Platform / Application Name 與 URL、100 字說明（可能顯示在 COROS 合作夥伴頁）、Primary／Secondary Contact Email、**Privacy Officer Email**、「Authorized Callback Domain (redirect_url)」、若要活動推送服務要填接收端點與狀態檢查網址、需要哪些 API 功能（單向活動同步、雙向同步、「Structured Workouts and Training Plans Sync (from your platform to COROS)」、GPX、每日健康資料、藍牙／ANT+）、「Intended use of data?」、Logo PNG（144、102 px；要推課表再加 120、300 px）。
- **「Total Active Users」選項最低一級是「0-150」**，再來是 150-1,000、1,000-5,000…
- 「Will your platform / application be for commercial or non-commercial use?」、「Will your platform / application be for personal or public use?」（選項有 Personal）
- 要勾同意「COROS API Application Terms」「Do you agree to the terms of the COROS API Agreement? (Linked below)」「DCA Agreement」「API Contract」。
- 推估：有「0-150」和「Personal」選項，代表小規模、甚至個人平台可以送件；但核不核准、多久，表單和文章都沒寫（**未找到來源**；試過：四篇 support 文章、表單、API Reference、Nango 文件、搜尋「COROS API approved / rejected / review time」）。

**COROS API Agreement**（Partner 要簽的合約；表單連到的 Dropbox PDF，PDF 建立日 2026-04-04，15 頁，**已驗證**）。和 TrailRunCoach 有關的條文：

| 主題 | 原文 | 對 TrailRunCoach |
|---|---|---|
| 締約方 | 「[Company legal name], a [jurisdiction and entity type]」；§19.1「(a) it is duly organized and validly existing;」 | 要是依法設立的主體。台灣的獨資商號不是法人，算不算：**未找到來源**；推估要有限公司才穩 |
| 審核 | 「4.2 COROS may approve, reject, condition, limit, suspend, or revoke access in its sole discretion.」 | COROS 可以自行決定 |
| 用途 | 「(c) create athlete facing insights, analytics, recommendations, training features, and performance features for the relevant End User;」 | 教練、分析、課表都在允許範圍 |
| 不算競爭 | 「For clarity, this does not prohibit specialized training, coaching, or analytics applications that complement the COROS Platform;」 | 明文排除我們這類 app |
| AI | 「7.3 Company may use COROS Data for machine learning, algorithmic models, and AI enabled End User features only if:」條件包括「(c) Company does not use COROS Data to train general purpose models for unrelated commercial purposes;」「(d) … cross customer profiling product」 | AI 教練可以；使用者自帶金鑰把資料送到 LLM 供應商，要在通知與同意裡寫清楚（推估） |
| 去識別化 | 「7.4 Company may create and use aggregated or deidentified data derived from COROS Data only if such data:」…「(c) is not sold, licensed, disclosed, or otherwise commercialized as a standalone data product.」 | SP-326 的內部分析可以 |
| 群體報告 | 「8.13 use COROS Data to create population level benchmarking reports or industry reports for external sale or publication without COROS's prior written consent.」 | SP-326 的結果若要**對外發表**，要先寫信給 legal@coros.com 取得同意 |
| 保存 | 「provided that such retention does not exceed ninety (90) days after End User revocation」 | 可以長期保存 FIT，只要使用者還連著 |
| 撤回 | 「Company must automatically and permanently delete or deidentify the relevant Personal Data within twenty-four (24) hours」 | 和上一列的 90 天互相矛盾（推估：個資 24 小時內刪除或去識別化，其他 90 天內） |
| 帳密 | 「8.11 request or store End User COROS account credentials;」（禁止） | 現在的 `coros_password_sealed` 正是這個 |
| 爬取 | 「8.6 scrape, crawl, harvest, or otherwise access any COROS system other than through approved APIs and approved methods;」（禁止） | 現在的 Training Hub 內部端點正是這個 |
| 分享 | 「8.12 disclose COROS Data of one End User to another End User except where the relevant End Users expressly authorize a social or collaborative feature approved by COROS;」 | 分享連結、教練功能若帶活動資料要注意 |
| 安全攸關 | 「8.9 use the API in connection with products or services where failure could reasonably lead to death, bodily injury, or catastrophic damage, unless expressly approved in writing by COROS;」 | 關門判斷、inReach 安全功能算不算，要問（推估：教練 app 一般不算） |
| 標示 | 「clearly and prominently display an attribution statement indicating that the data originates from a COROS device (e.g., "Data provided by COROS" or "Powered by COROS")」，還要標裝置型號；以及 7.5「must be clearly labeled as "for fitness and performance purposes only"」 | 圖表、AI 回覆旁要標來源和型號 |
| 宣傳 | 「14.3 Company shall not issue any press release or public statement referencing COROS, the API, or this relationship without COROS's prior written approval.」 | 文案提到 COROS 合作要先問 |
| 費用 | 「17.1 Unless otherwise agreed in writing, access is provided without charge.」；「17.2 COROS may introduce fees, usage based pricing, or premium access tiers upon ninety (90) days' prior written notice.」 | 現在免費 |

推估：這份合約是 Partner API 用的，MCP 沒有要求簽。但它最能代表 COROS 對 AI、去識別化、保存期限的立場，MCP 路線也照這些做最保險。

**技術規格**（COROS API Reference V2.1.1，同一個 Dropbox 資料夾，PDF 檔名「Updated September 2026」，75 頁，**已驗證**）：

- 測試環境 `opentest.coros.com`、正式環境 `open.coros.com`（「Test environment domain is opentest.coros.com.」）。
- OAuth：授權碼 30 分鐘、「The accessToken is valid for 30 day by default.」，可以 refresh。
- 活動查詢：「The maximum date range for one query is 30 days, and the query date is not earlier than three months before the day」——**只能往回查 3 個月**，更早的要靠使用者自己匯出。
- 活動推送：「COROS server checks newly added and previously failed workout data of each user that links with partners every 5 minutes (frequency can be adjusted).」失敗重試兩次，超過 24 小時不再推。
- FIT：`/v2/coros/sport/detail/fit` 取 FIT 下載網址。
- 推課表：`POST /coros/tp/list/push`，「COROS receives the training schedule of the user covering the next 1 year including today.」；修訂紀錄寫一次同步 30 個課表；強度目標有 `PercentOfThresholdHr`、`RangeOfThresholdHr`、`PercentOfThresholdSpeed`、`PercentOfFtp`、`RangeOfFtp`、步頻區間；結束條件有距離、時間、手動（EndManually）；支援 `trailRun`。
- 刪除：`POST /coros/tp/workout/deleteById`，「Only training workouts that have not been executed in today's and future dates can be deleted.」
- 限流：「1000 maximum calls/minute.」
- Nango 的整合文件寫「The Coros API documentation is private, so use the Register Application link above to get access, and the Coros team will share them with you.」（**已驗證**）——但這份 PDF 現在從申請表就能下載。

**有沒有小團隊申請成功**：

- Tredict（德國的訓練平台，部落格作者 Felix Gertz）2020-10 寫「Recently, Coros has started offering its own API to synchronize your activities with other platforms.」，2023-06 接上課表 API，當時「If you delete a planned workout in Tredict, it cannot be removed from the Coros workout plan at the moment.」（**已驗證**文章；Tredict 是不是小團隊：推估是）。這是 2026 年改版前的例子，不代表現在的標準。
- intervals.icu（單人開發）也是 COROS 合作夥伴（data-hubs.md §1.1）。
- 2026 年新規定（「Established platform…」）之後個人或小團隊成功的案例：**未找到來源**（試過：搜尋 COROS API approved／indie／reddit、Zwift 論壇 COROS 整合串、Garmin 論壇）。

### 2.4 COROS 三條路比較

| | 非官方 Training Hub（現在） | COROS MCP | Partner API |
|---|---|---|---|
| 申請 | 不用 | 不用 | 寄信＋飛書表單，COROS 自行決定 |
| 誰能用 | 任何人（但違反條款） | 任何開發者 | 依法設立的公司（推估） |
| 登入方式 | 使用者交出帳密 | 使用者在 COROS 頁面授權（OAuth） | 使用者在 COROS 頁面授權（OAuth） |
| 讀活動與 FIT | 可以，沒有公開上限 | 可以，每人每 24 小時 50 個 FIT | 可以，只能往回 3 個月；1,000 次／分 |
| 新活動通知 | 輪詢 | 輪詢（沒有 webhook） | webhook，約 5 分鐘 |
| 推課表 | 可以，能改能刪 | 可以，今天起 90 天內；**不能刪、不能改期** | 可以，未來 1 年；未執行的可以刪 |
| 費用 | 0 | 0 | 0（可 90 天通知後收費） |
| 收費服務 | 條款禁止 | 沒寫，要書面確認 | 合約允許 |
| AI、去識別化分析 | — | 沒寫 | 有條件允許；對外報告要同意 |
| 風險 | 帳號被封、IP 被擋、隨時壞；使用者要賠償 | COROS 改規則；限制比 Partner 多 | 審核不過；要公司、隱私長、資安計畫 |

---

## 3. Garmin：怎麼申請、好不好申請

`docs/research/garmin.md` §1（2026-10-01）的結論今天仍成立。這節照使用者要求完整重查一次。

### 3.1 從哪裡申請、表單要填什麼

- 入口：Developer Program 頁面 https://developer.garmin.com/gc-developer-program/overview/ 和 FAQ 都寫「please request the Garmin Connect Developer Program」，連到表單 https://www.garmin.com/en-US/forms/GarminConnectDeveloperAccess/ 。
- 2026-10-07 表單頁回 200，但頁面是 JS 渲染，原始 HTML 裡沒有任何表單題目（搜「Company name」0 筆）。論壇使用者 2026-04-19 說「The "Under Construction" page has been blocking new applicants for weeks now.」（**已驗證**論壇原文；是使用者說法）。
- **表單題目**（web.archive.org 2025-12-04 快照，標題「Garmin Connect Developer Program Access Request Form」，**已驗證**；2024-08-22 快照大致相同）：
  - 公司資料：公司名稱、聯絡人、職稱、電話、地址（「Your business address provides us the details necessary to process your request.」）、國家（清單有 Taiwan）、公司網站。
  - 隱私政策連結：「Please provide a link to your privacy statement/policy that informs users of the ways in which you use and disclose their activity data. Please note, this is a legal requirement.」
  - 是否代客戶整合、是否用外包。
  - 「How do you plan to use the Garmin Connect Developer Program?」
  - 「Do you intend to offer the data and/or share it with other platforms or services? *」（2024-08 版沒有，2025-12 版才加）
  - 是否已和 Garmin 或經銷商接觸、是否已整合其他品牌（Fitbit、Polar、Apple）。
  - 「How many end users/members are on your platform? *」
  - 業務類型（Fitness or Outdoor – direct to consumer、Analytics…）與目標客群（General Consumer…）。

### 3.2 核准後的流程與時間

（FAQ，https://developer.garmin.com/gc-developer-program/program-faq/ ，**已驗證**）

1. 「After you request the Garmin Connect Developer Program, we will confirm the status of your application within two business days.」
2. 核准後進 Developer Portal；「In parallel, we will invite you to an integration call to help understand your needs and walk you through the high-level technical details of the Developer Program and its APIs.」
3. 測試：Activity、Health API 是「After approval, you will have access to an evaluation environment to test the Garmin Connect Activity API.」；Training API 是「After approval, you will have access to test your integration against the production environment with throttled access.」
4. 上線前驗證：Activity API 頁列「Developer Web Tools: Easy on-boarding, sample data, backfill user data, and auto-verification of your integration before production」。
5. 「A typical integration takes between 1 and 4 weeks.」
6. 品牌：要照 Garmin API Brand Guidelines 標示（intervals.md §7、SP-343 已驗證）；AI 產出與彙總分析也要標 Garmin（**已驗證（SP-343）**）。

注意：「兩個工作天」是暫停前的說法，現在不適用（§3.4）。

### 3.3 資格

- FAQ：「The Garmin Connect Developer Program is available for enterprise use」；「There are no licensing or maintenance fees for access to the Garmin Connect Developer Program, but it is only for business use.」（**已驗證**）
- 合約授權範圍：「solely for internal business purposes」（Developer Program Agreement FRM-0952 Rev. B，**已驗證**）。
- **台灣的行號（獨資）或有限公司算不算**：**未找到來源**。試過：FAQ、表單快照、Agreement 全文、搜尋「Garmin developer program sole proprietor / individual business」。表單只問公司名稱、地址、網站、隱私政策、使用者數，沒有要求公司登記證明。推估：有統一編號的行號加公司網域、隱私政策，形式上能填；但 Garmin 實際以法人為準的可能性高（garmin.md §1.2 的社群說法）。
- 個人被拒（**摘要**，論壇使用者說法，互相矛盾）：
  - 「I can't think of a single solo dev that was ever given access.」（jim_m_58，2026 年）
  - 「there are absolutely tonnes of solo devs who have been granted access to the training api in the last 12 months alone (up to the point where Garmin shut access in march).」（另一使用者，2026 年）
  - 「My application was rejected with specific requirements to address.」（新創，2025-09 申請被退件、要求補件）

### 3.4 現況：暫停受理

- 2026-10-07 Overview、FAQ、Activity、Training、Health API 各頁頂端都是「Stay tuned for more updates on the program」（**已驗證**）。沒有恢復受理的字樣。
- Garmin 團隊在論壇的說法（Terra 部落格 2026-09-07 轉引）：「The application form for new API access requests is currently unavailable while we complete updates to the Garmin Connect Developer Program. During this transition, new access requests are temporarily paused.」（**已驗證**為 Terra 的轉引；Garmin 原帖本次沒找到，**摘要**）
- 2026-08 申請者收到的回覆：「Temporarily paused the review and approval of new API access requests.」（論壇引述，**已驗證**論壇原文）
- **暫停期間能不能送件、會不會排隊**：**未找到來源**。表單已下架；論壇有人寄 connect-support@developer.garmin.com 沒有回音（2026-04 的帖子）；2026-08 的帖子問「Are existing applications queued?」沒有官方回答。
- 2026 年暫停後成功申請的案例：**未找到來源**（試過：Garmin 論壇三個相關討論串、Terra、the5krunner、搜尋）。
- 原因：Garmin 說是「significant redesign and modernisation」（the5krunner，garmin.md §1.2，**摘要**）；論壇有人猜跟 TrainingPeaks 或 AI 申請暴增有關（**摘要**，臆測）。

### 3.5 費用與條件

- 不收授權費，但「Access to some metrics may require a license fee payment or minimum device order quantity for commercial use.」（FAQ，**已驗證**）。Health API 頁的星號標在 Beat-To-Beat Interval：「* Commercial use requires a license fee payment.」（**已驗證**）
- 收費要書面許可：Agreement §5.2.e 不得「sell, lease, share, transfer, or sublicense the License Key, provide access to the API, or derive income from the use or provision of the API, whether for direct commercial or monetary gain or otherwise, without Garmin's prior written permission;」（**已驗證**）。
- **公開的商業授權流程**：**未找到來源**。FAQ、Agreement、各 API 頁都沒有寫怎麼申請 §5.2.e 的許可。推估：在核准後的 integration call 裡談。
- 未來收費：Agreement §10.1 允許 Garmin 30 天通知後開始收費（garmin.md §1.3）。

### 3.6 拿到之後能做什麼

- **Activity API**：「Full Activity Details Access: Activity data files (.FIT, GPX, .TCX formats) available for complete activity details」；Ping/Pull 或 Push 架構（**已驗證**）。回補幾天：官方規格不公開（**未找到來源**）；聚合商的說法不一：Terra 5 年、Spike 90 天、Open Wearables 連線前 30 天、intervals.icu 1 年（**摘要**，SP-343）。
- **Training API**：「The Training API allows you to publish workouts and training plans to the Garmin Connect calendar.」使用者同步手錶後就出現（**已驗證**）。支援哪些目標、一堂最多幾步：官方規格只在核准後的入口提供（**未找到來源**）；Terra 轉述「Up to 2 targets per step (cycling/swimming)」「HR and power trigger completions」（**摘要**）；`workout_targets/garmin.py` 的「一堂最多 50 步」來自非官方套件（garmin.md §3.1）。
- **Health API**：全天心率、睡眠、壓力、Body Battery 等；擁有者只在跑步時戴錶，用處不大。
- **Courses API**：推路線到手錶（越野跑有用，但 COROS 那邊沒有對應）。

### 3.7 不用 Developer Program 的 Garmin 官方替代

- **Connect IQ（自己做手錶 app）**：
  - SDK 免費：FAQ「Learn more about Connect IQ and start using our free SDK.」（**已驗證**）；Connect IQ 和 Developer Program 是兩個獨立計畫，不需要後者。
  - 能從我們的伺服器抓當天課表，在**自己的 app 裡**執行：Stryd、Final Surge 都有這樣的 Connect IQ app（**摘要**，搜尋摘要）。
  - **不能寫進手錶原生的課表清單**：開發者在論壇問「Is there any supported way for a third-party Connect IQ app (or its iOS/Android companion app) to write a workout into the device's native Workouts list」（**已驗證**問題原文），回覆者說要用 Developer Program 的 API（**摘要**）。
  - 要過 Connect IQ Store 審核；審核時間與規範頁是 JS 渲染，本次沒讀到（**未找到來源**）。
  - 推估：要另學 Monkey C、為每種錶型測畫面，至少數週工作量，跑步時使用者看到的是我們自己的畫面，不是 Garmin 原生課表畫面。
- **Garmin Health SDK**：「Available to Garmin Health enterprise partners」、「Commercial Use: License Fee or Minimum Device Order Quantity」（**已驗證**）。要企業身分加授權費，不適合。
- **FIT 課表檔經 USB 放進手錶**：見 garmin.md §4.1。
- 非官方 `garminconnect`：Garmin 使用條款（「Effective Date: April 1, 2026」，今天仍是這版）禁止「accesses, copies, or scrapes content from the Site through any means not purposely made available through the Site」（**已驗證**），商業版不能用。

### 3.8 結論：好不好申請

**現在申請不了，重開後也不好申請。** 表單從 2026 春天下架，到 2026-10-07 沒有重開日期，也沒有排隊名單。重開後的條件：企業身分、公司網站、隱私政策、使用者數、用途說明；用 API 收費還要另外取得 Garmin 書面許可，流程不公開。個人或小團隊過去有人被拒、也有人說拿得到，說法互相矛盾。Garmin 使用者短期只能走 intervals.icu 或手動匯入；Connect IQ app 可行但工作量大，也進不了原生課表。

---

## 4. 其他平台

### 4.1 Polar AccessLink

- 誰能申請：「Any registered Polar Flow user can create API client to AccessLink by filling application details at」（https://www.polar.com/accesslink-api/ ，**已驗證**）。自助、免審核（推估）。
- FIT：有；但「Only Exercises uploaded to Flow in the last 30 days are returned.」且只有授權後的新活動（**已驗證（SP-343）**）。
- 推課表：沒有（端點清單沒有課表寫入，**已驗證（SP-343）**）。
- 條款（Polar API Agreement，「Latest update: 22 August 2025」，**已驗證**）：
  - 費用：「At the moment Polar offers the use and activation of Polar API's free of charge.」
  - 商業：授權用於「proprietary application or services development」，不禁止收費；但「You may not use, or permit others use the Licensed Materials in creating a service similar to or competing with Polar Ecosystem」。
  - AI、外部傳輸：「ensuring that no Data is distributed to external sources without the explicit Member permission」——送到 LLM 供應商要使用者明確同意（推估）。
  - 標示：「shall credit Polar Ecosystem as the source of Data when displaying any Data, or any data derived from Data in Your application or service」
  - 終止後：「You shall destroy and delete all copies of the Licensed Materials and Data.」
  - 跨使用者分析：沒有明文；推估受上面「external sources」與使用者同意約束。

### 4.2 Suunto

- 誰能申請：「We currently don't offer the API access for personal use.」；「We can provide access to companies/organizations that are building tools/apps/services for commercial & non-commercial usage.」（https://apizone.suunto.com/ 、/faq ，**已驗證**）
- 審核：「We are reviewing the applicants based on for example the fit to our brand, interest from our customers and illustration of the innovation mindset」；「There is maximum two week waiting period.」（**已驗證**）
- 費用：「We do not charge from the use of the API.」（**已驗證**）
- 推課表：「You can push the workouts in FIT file format for the users account.」——指的是上傳活動檔，不是課表（**已驗證（SP-343）**）；intervals.icu 能推到 Suunto（SuuntoPlus Guides）。
- API Agreement：申請時才給（「If you select Suunto Cloud API you will be provided the API agreement.」，**已驗證**）。AI、跨使用者分析的條款**未找到來源**（試過：apizone 首頁、FAQ、how-to、partner 頁、搜尋「Suunto API Agreement」）。
- 申請狀態有矛盾：官網仍收申請，Terra 的整合表寫「Applications no longer open」（**已驗證（SP-343）**兩邊）。

### 4.3 Apple Health／Apple Watch

（詳見 SP-343 的 apple-hc 調查與 SP-344）

- 沒有雲端 API，網頁 app 讀不到；推課表要原生 iOS app 用 WorkoutKit（**已驗證（SP-343）**）。
- App Store 審查指南 5.1.2(i)：「You must clearly disclose where personal data will be shared with third parties, including with third-party AI, and obtain explicit permission before doing so.」（**已驗證**）
- 5.1.2(vi)：HealthKit 資料「may not be used for marketing, advertising or use-based data mining, including by third parties.」（**已驗證**）。去識別化的跨使用者分析是否算「use-based data mining」有解讀空間（推估，見 SP-343）。
- 開發者授權協議：HealthKit 資料只能用於「providing health, motion, and/or fitness services」（**已驗證（SP-343）**）。
- 不寫原生 app 的替代：Apple Watch → Intervals.icu Companion 或 HealthFit → intervals.icu → TrailRunCoach（**已驗證（SP-343）**）。

### 4.4 intervals.icu（中介）

（詳見 data-hubs.md §1 與 SP-343 的 intervals 重查）

- OAuth app 線上申請（2026-08-28 起），核准標準沒公開；預設每位使用者每天 100 次。
- API 條款：「We grant you a non-exclusive, worldwide, royalty-free, perpetual license to access and use the API for any lawful purpose, including commercial use.」唯一附帶條件是 Garmin 來源要標示（**已驗證（SP-343）**）。
- 有 webhook（`ACTIVITY_UPLOADED` 等）；原始檔是 gzip 壓縮；COROS 來源只能補 3 個月歷史（COROS API 的限制）。
- 推課表到 Garmin、COROS、Suunto、Wahoo；心率只能寫 % LTHR；在 intervals.icu 刪掉的課表不會從 COROS 刪除（使用者回報，**摘要**）。
- 風險：免費帳號 90 天沒上網站會休眠（Supporter US$4／月可避免）；作者在做官方 MCP，之後可能自己做 AI 功能。

### 4.5 TrainingPeaks、Strava（不適合，簡列）

- TrainingPeaks Partner API 從 2022 年起暫停收新夥伴，條款限快取 7 天、不得競爭；現在的 `tp_client.py` 帳密登入違反 TP 使用條款多條（**已驗證（SP-343）**，見 SP-343 的 TP 調查）。
- Strava API Policy §5.3 禁止把資料用在任何 AI 用途，§5.4 禁止去識別化的跨使用者分析（data-hubs.md §3.1、**已驗證（SP-343）**）。

---

## 5. 總表

| 平台 | 官方 API | 誰能申請 | 申請流程與時間 | 費用 | 讀活動／原始 FIT | 推課表 | 限流與 webhook | 商業使用 | AI／跨使用者 | 現行非官方用法的條款定位 | 風險 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **COROS MCP** | OAuth＋MCP 工具 | 免申請 | 自己實作 OAuth（DCR＋PKCE），立即可用 | 免費 | 有／FIT 每人每 24 小時 50 個 | 有，今天起 90 天；不能刪、不能改期 | 沒有 webhook，要輪詢；其他上限未公開 | 沒寫；受使用者條款約束，要書面確認 | 官方列「AI coaching apps」；跨使用者沒寫 | — | COROS 改規則；推課表的限制 |
| **COROS Partner API** | OAuth＋REST | 依法設立的公司；表單有 0-150 人、Personal 選項 | 寄 api@coros.com＋飛書表單；審核時間未公開 | 免費（可 90 天通知收費） | 有，只能往回 3 個月 | 有，未來 1 年，可刪未執行的 | 1,000 次／分；webhook 約 5 分鐘 | 合約允許 | AI 有條件允許；去識別化內部可，對外報告要同意 | — | 審核不過；資安、隱私義務 |
| **COROS 非官方（現在）** | Training Hub 內部端點 | — | — | 0 | 有 | 有，能改能刪 | 未公開 | 使用者條款禁止 §4(a) | — | **違反** ToS §4(a)(c)(e)、§7；API 合約 §8.6、§8.11 也禁止 | 帳號被封、IP 被擋、隨時壞、使用者要賠償 |
| **Garmin Developer Program** | Activity、Training、Health、Courses API | 只限企業 | 表單**下架暫停**（2026 春起）；以前 2 個工作天回覆、整合 1–4 週 | 免授權費；部分指標要付費；收費要書面許可 | 有，FIT／GPX／TCX；回補天數未公開 | 有（Training API） | Ping/Pull 或 Push；限流未公開 | 要 Garmin 書面許可（§5.2.e） | AI、彙總分析要標 Garmin | 沒用（stub）；非官方套件違反使用條款 | 申請不到 |
| **Suunto** | Cloud API | 公司或組織，不給個人 | 申請表，最多兩週 | 免費 | 有 FIT | 公開文件沒有（intervals.icu 可推） | workout webhook | 允許（commercial & non-commercial） | 合約申請時才給，未找到 | 沒用 | 申請狀態說法矛盾 |
| **Polar AccessLink** | REST | 任何 Polar Flow 使用者 | 自助建立 client | 免費 | 有，只有授權後、近 30 天 | 沒有 | 有 webhook | 允許，不得做競品 | 外部傳輸要使用者明確同意；要標 Polar | 沒用 | 台灣使用者少（推估） |
| **Apple Health** | 只有裝置上的 HealthKit | Apple 開發者（年費 US$99） | 要原生 iOS app 過審 | US$99／年 | 沒有 FIT，只有樣本 | WorkoutKit，要原生 app | 無雲端 | 允許 | 給第三方 AI 要明確同意；禁止 data mining | 沒用 | 要另做 iOS app |
| **intervals.icu** | REST＋OAuth | 開發者線上申請，核准標準未公開 | 線上表單 | 免費（使用者可能要 Supporter） | 有原始檔（Strava 來源除外） | 有（Garmin、COROS、Suunto、Wahoo） | 每人每天 100 次；有 webhook | 條款明文允許 | 沒限制；Garmin 來源要標示 | 沒用 | 單一小廠商；使用者要多一個帳號 |
| **TrainingPeaks** | Partner API | 公司；**暫停收新夥伴** | — | 免費 | 沒有 FIT | 有 | 未設硬上限 | 不得競爭、快取 7 天 | — | `tp_client.py` 違反 TP 使用條款 | 不適合 |
| **Strava** | REST | 要 Strava 訂閱 | 自助，超過 10 人要審核 | 訂閱費 | 沒有 FIT | 沒有 | 有 | 禁止 AI | §5.3 禁 AI、§5.4 禁跨使用者 | 沒用 | 不適合 |

---

## 6. 沒有官方 API 時怎麼推課表

| 做法 | COROS | Garmin | 其他 | 實用性 |
|---|---|---|---|---|
| **行事曆訂閱**（已經有，`calendar_feed.py`） | 只進手機行事曆，不進手錶 | 同左 | 所有人 | 低成本、零條款風險；使用者要自己在錶上建課 |
| **匯出 FIT 課表檔** | Training Hub 只有「Manually upload activity, support .fit and .zip file.」——是活動，不是課表；**沒找到匯入課表檔的功能**（試過：Training Hub 手冊、COROS 匯入相關 support 文章 6 篇、搜尋）。Training Hub 只能「Share a Structured Workout via link.」，COROS 對 COROS | USB 放進 `GARMIN/NewFiles` 可以（garmin.md §4.1）；Garmin Connect 沒有匯入課表檔的功能（**摘要**） | Suunto 不支援匯入課表檔（未查） | Garmin 中等（要寫 FIT 課表編碼器、macOS 要 MTP 工具）；COROS 不可行 |
| **經 intervals.icu 推** | 可以，推未來 7 天；刪掉的不會同步刪除 | 可以，推未來一週 | Suunto、Wahoo 可以 | 高；要使用者有 intervals.icu 帳號 |
| **COROS MCP** | 可以（今天起 90 天），不能刪或改期 | — | — | 高，要 COROS 書面確認商業使用 |
| **Connect IQ app** | — | 只能在自己的 app 裡跑課表 | — | 低（工作量大） |

推估：COROS 使用者沒有任何「檔案」方式能把課表放進手錶，只能走 API（MCP、Partner、intervals.icu）或手動在 COROS App 建。

---

## 7. 落差

1. **商業版不能用帳密登入。** COROS 使用者條款 §7、§4(e)，API 合約 §8.11；TrainingPeaks 也一樣（SP-343）。`coros_client.py` 的登入、密碼保存、自動重登，`tp_client.py` 的網站登入，商業版都要拿掉或關掉。
2. **推課表的模型要改。** 現在的推送會刪掉計畫裡已經不存在、或錯過的課（`coros_workouts.py:1080-1100`、`plan_auto.py:483-485`）。MCP 不能刪也不能改期，要改成三選一：只推很近的天數（例如明後天）、改用 COROS 訓練計畫逐日覆寫、或把刪除留給使用者手動。
3. **沒有 webhook。** MCP 只能輪詢。夜間一次很省；「即時」只能做到「打開網頁時同步＋定時輪詢」。
4. **歷史資料。** MCP 每人每 24 小時 50 個 FIT；Partner API 只能往回 3 個月。舊資料要靠 COROS 的「Export Data」（「You will be prompted to choose a file format (either .FIT or .TCX) and enter an email address to receive the exported files.」，**已驗證**）再用 SP-323 匯入。
5. **資料來源標示與同意。** 要在圖表、AI 回覆旁標「Data provided by COROS」與錶型（API 合約 §14.5），Garmin、Polar 來源也要標；同意畫面要列出資料類別、用途、保存期限、AI 用途、怎麼撤回與刪除（API 合約 §9.1）。這些和 SP-326、SP-60 的條款一起寫。
6. **撤回後刪除。** 使用者在 COROS 撤銷授權後，個資 24 小時內刪除或去識別化（API 合約 §9.5）。現在沒有這個流程。
7. **Garmin 使用者沒有自動路線**，只能 intervals.icu 或手動。

---

## 8. 對商業模式的影響（SP-321：NT$99／月、NT$890／年；免費層每晚自動同步一次；訂閱層即時同步＋推手錶）

| 結果 | 免費層 | 訂閱層 | 文案可以寫 | 文案不能寫 |
|---|---|---|---|---|
| **A. 拿到 COROS Partner API** | COROS：webhook 收到就存，每晚一次重算 | COROS：收到 webhook 立即重算（約 5 分鐘）；推課表到 COROS 手錶，改了自動更新、刪除 | 「支援 COROS 自動同步、推課表到 COROS 手錶」（照 COROS 品牌規範；新聞稿、公開聲明要 COROS 書面同意，§14.3） | 「COROS 官方合作夥伴」（§14.2 不得暗示超過互通的關係，除非 COROS 同意） |
| **B. 只有 COROS MCP**（書面確認可商用） | COROS：每晚輪詢一次（列表＋幾個 FIT，遠低於 50／日） | COROS：打開網頁時同步＋定時輪詢（間隔看 COROS 回覆的限流）；推課表到手錶，但改期、刪除要到 COROS App 手動，或改用訓練計畫逐日覆寫 | 「用 COROS 帳號授權連結，自動匯入活動」「推課表到 COROS 手錶（改期或刪除請在 COROS App 操作）」 | 「即時同步」（沒有 webhook）；「官方合作」 |
| **C. 只有中介平台**（intervals.icu） | 每晚同步一次（或 webhook） | webhook 近即時；經 intervals.icu 推課表到 Garmin、COROS、Suunto、Wahoo | 「透過 intervals.icu 同步 Garmin、COROS、Suunto…」；Garmin 來源要標示 | 「直接連 Garmin／COROS」 |
| **D. 只有手動匯入** | 手動匯入 FIT（SP-323）、桌面 Chrome 自動匯入資料夾（SP-324）；行事曆訂閱 | 同左；訂閱改賣回測、全歷史、計算機無限、AI、3 年保留（SP-321 筆記的 99 方案） | 「匯入 FIT、選資料夾一鍵匯入」「課表訂閱到 Google／iPhone 行事曆」 | 「自動同步」「推手錶」 |

推估：最可能的實際狀態是 **B＋C＋D 並存**——COROS 使用者走 MCP、Garmin 使用者走 intervals.icu、所有人都能手動匯入。這時「推手錶」只在 COROS（MCP，有限制）和 intervals.icu 路線成立，訂閱層的主要賣點仍應是分析功能，推手錶當附加。成本面：MCP、intervals.icu、Partner API 都免費；SP-343 查過的商用聚合商最便宜 US$300／月，超出預算，不考慮。

---

## 9. 結論與後續

### 9.1 建議的串接方式

1. **所有人的基本路線：手動匯入 FIT**（SP-323，再加 SP-324）。不需要任何核准、沒有條款風險，也是舊資料的唯一入口（COROS Export Data、Garmin Export Your Data）。
2. **COROS 使用者：COROS MCP OAuth**，取代 `coros_client.py` 與 `coros_workouts.py` 的帳密登入。前提是 COROS 書面確認可以用在收費服務。新增一個 `coros_mcp` 來源與 workout provider（沿用 `workout_targets` 介面），推課表的語意改成「只新增或覆寫，不刪除」。
3. **Garmin、Suunto、Wahoo、Apple Watch 使用者：intervals.icu OAuth**（data-hubs.md §4.3 的 hub 模式）。
4. **有公司登記、上線並有使用者之後：申請 COROS Partner API**，拿到 webhook、刪除課表、未來 1 年的課表推送。
5. **Garmin Developer Program：等重開**，不投入 Connect IQ（工作量大、進不了原生課表）。
6. **現在的 COROS、TrainingPeaks 帳密登入**：只留給自架或個人版，商業版關掉；連接畫面照 plan §7.5 的聲明。

### 9.2 時程

| 時間 | 做什麼 | 依賴 |
|---|---|---|
| 本週 | 寄信給 COROS（§9.3）；擁有者自己把 COROS MCP 接到 Claude Desktop，用自己的帳號看工具清單與 `createScheduledWorkout` 的參數（強度目標、單位）、`querySportRecords` 能查多久以前 | 無 |
| 第 1–2 週 | 實作 SP-323 手動匯入；申請 intervals.icu OAuth app；寄信給 intervals.icu（data-hubs.md、intervals 重查列的問題） | 無 |
| 第 2–4 週 | COROS 沒回就再寄一次並副本 support@coros.com；回覆允許的話，在分支做 COROS MCP 原型（DCR 是否接受 `https://coach.yichlin.com` 的 redirect、FIT 下載、推一堂測試課） | COROS 回覆 |
| 上線前 | 商業版移除帳密登入；隱私政策、同意畫面、撤回後刪除流程、資料來源標示（§7 第 5、6 點）；文案照 §8 | 上面各項 |
| 上線後、有公司登記 | 送 COROS Partner API 申請（表單的使用者數選「0-150」） | 公司登記、隱私長 email、Logo |
| Garmin 重開時 | 用公司名義申請 Activity＋Training API；在申請表的用途欄寫清楚 AI 教練、不轉售資料 | 公司登記、隱私政策 |

### 9.3 要寄的信

**給 COROS（api@coros.com）**，主旨例：「Commercial use of COROS MCP OAuth by a small training app (Taiwan), and Partner API eligibility」。要問：

1. 我們是台灣的越野跑訓練分析網站，訂閱制收費（約 US$3／月），伺服器自架。能否用 COROS MCP 的 OAuth（每位使用者自己授權）在伺服器端當 MCP client，用在**收費服務**？MCP 的開發者適用哪份條款——只有 Terms of Service（其 §4(a) 禁止 commercial purpose、§4(e) 要求自動化存取取得 express written permission），還是 COROS API Agreement？請書面確認。
2. 使用者連線期間，能否長期保存 FIT 與衍生指標（數年）？使用者撤銷授權後要多久內刪除？
3. 使用者自帶 LLM 金鑰，把他的 COROS 資料送到他選的 AI 供應商產生教練建議，是否允許？
4. 去識別化的跨使用者分析，只在 app 內部使用、不對外發表，是否允許？
5. 除了每人每 24 小時 50 個 FIT，還有哪些上限（每分鐘、每天的工具呼叫；以 client 還是使用者計）？`querySportRecords` 與 FIT 能查多久以前？
6. DCR 是否接受 `https://` 網域的 redirect URI？token 有效期？台灣帳號在哪個分區？
7. MCP 之後會不會支援刪除或移動已排的課表？`createScheduledWorkout` 支援哪些強度目標（bpm、% 閾值心率、配速、功率）？
8. Partner API：上線初期使用者少於 150 人、公司型態是〔有限公司／行號，待使用者決定〕，是否符合資格？審核大約多久？
9. 能否在 app 裡用「Connect with COROS」按鈕和 COROS logo？
10. 我們有關門時間、天候等安全提醒功能；API Agreement §8.9 的「failure could reasonably lead to death, bodily injury」是否涵蓋？

**給 intervals.icu（support@intervals.icu）**：付費 app 的 OAuth 核准條件、API 呼叫算不算「seen」（90 天休眠）、超過 500 人的額度、Garmin 來源轉出的限制（照 intervals 重查的清單）。

**Garmin**：現在沒有可寄的管道（表單下架，論壇回報 connect-support@developer.garmin.com 沒有回音）；只追蹤 developer.garmin.com。

**Suunto、Polar**：Garmin 和 COROS 有結論後再說。Polar 是自助申請，需要時隨時能接。

### 9.4 建議的後續單（使用者決定後才開）

- COROS MCP 來源與 workout provider（含 DCR、token 加密保存、撤回後刪除）。
- 商業版移除 COROS、TrainingPeaks 帳密登入（設定開關：自架版保留）。
- intervals.icu hub 模式（data-hubs.md §4.3）。
- 資料來源標示（COROS 錶型、Garmin、Polar）與同意畫面（和 SP-326、SP-60、SP-58 合併）。

---

## 10. 要使用者決定的問題

1. 商業版是否完全停用 COROS 與 TrainingPeaks 的帳密登入，只留給自架或個人版？
2. 要不要登記公司（有限公司或行號）？COROS Partner API、Garmin、Suunto 都要公司；COROS 合約要求「duly organized and validly existing」。
3. 寄給 COROS 的信由誰寄、用哪個 email、公司名稱怎麼寫？要不要現在就寄？
4. COROS 回信前，免費層的「每晚自動同步」要不要先用 MCP 上線？還是等書面確認？（建議等）
5. MCP 推課表不能刪除與改期，要選哪一種：只推明後兩天、改用 COROS 訓練計畫逐日覆寫、還是推了就由使用者在 COROS App 自己刪？
6. Garmin 使用者走 intervals.icu 當主要路線，可以接受嗎？（使用者要多一個帳號；免費帳號 90 天沒上站會休眠，Supporter US$4／月）
7. Garmin 重開前，要不要做 Connect IQ app？（建議不做）
8. SP-326 的跨使用者分析只做內部、不對外發表？COROS 合約 §8.13 規定對外的群體報告要先取得同意。
9. 訂閱層的賣點在 COROS 回信前改以分析功能為主（SP-321 的 99 方案），推手錶當附加？

---

## 11. 重新驗證清單（動工前逐項重查）

1. COROS Terms of Service 的「Last Updated」是否仍是 2023-04-23；§4、§7 有沒有改；有沒有新增 MCP 或開發者專用條款。
2. 〈Build on COROS MCP〉是否仍寫「No application or approval required」；README 的 50 FIT／24 小時、90 天排課、不能刪除或移動，有沒有改。
3. COROS MCP 的 OAuth metadata（DCR、scope、grant），以及實測 DCR 接不接受 HTTPS redirect、token 有效期、台灣帳號的分區。
4. COROS 對商業使用的書面回覆（§9.3）。
5. 飛書申請表的題目（使用者數級距、Personal 選項）、API Agreement 與 API Reference 的版本（目前 PDF 建立日 2026-04-04、V2.1.1 2026-09）。
6. Partner API 的歷史 3 個月限制、課表推送範圍（1 年、一次 30 個）、刪除規則。
7. Garmin Developer Program 是否重開（各頁頂端的「Stay tuned」）、表單題目、FAQ 的資格與時間；Agreement 版本（FRM-0952 Rev. B）與 §5.2.e。
8. Garmin 使用條款版本（目前 2026-04-01）。
9. Suunto 申請狀態（官網 vs Terra）、Polar API Agreement 版本（2025-08-22）。
10. intervals.icu API 條款、OAuth 核准方式、休眠規則（data-hubs.md §6）。
11. 擁有者用自己的帳號實測 MCP：工具定義裡的強度目標、`querySportRecords` 能查多久以前、FIT 的 50 個額度怎麼算。

---

## 12. 參考（2026-10-07 讀取）

COROS：

- Terms of Service（Last Updated: April 23, 2023）：https://coros.com/terms ；地區版：https://coros.com/eu/terms 、https://coros.com/uk/terms 、https://coros.com/au/terms
- Privacy Policy：https://coros.com/privacy
- Build on COROS MCP：https://support.coros.com/hc/en-us/articles/53181619102996 （以 Zendesk 公開 JSON `https://support.coros.com/api/v2/help_center/en-us/articles/53181619102996.json` 讀取，下同）
- Connect Your COROS to AI：https://support.coros.com/hc/en-us/articles/50841795180948
- Connect Your Data：https://support.coros.com/hc/en-us/articles/53181260265492
- Partner API Access：https://support.coros.com/hc/en-us/articles/53181766856724
- Submit an API Application：https://support.coros.com/hc/en-us/articles/17085887816340
- Requesting a Bulk Export of COROS Data：https://support.coros.com/hc/en-us/articles/25002333092500
- COROS Training Hub Manual：https://support.coros.com/hc/en-us/articles/4412176269844
- 匯入相關：https://support.coros.com/hc/en-us/articles/7708736140948 、/23909104424084 、/26879034494612 、/4408620041620 、/24288945322388
- COROS MCP（README、CHANGELOG、skill）：https://github.com/coroslab/COROS-MCP （raw：README.md、CHANGELOG.md、skill/coros_mcp_login_gateway/SKILL.md、scripts/coros_mcp_login.py）
- MCP OAuth metadata：https://mcp.coros.com/.well-known/oauth-authorization-server 、/.well-known/openid-configuration 、/.well-known/oauth-protected-resource/mcp ；分區 mcpus、mcpeu、mcpcn 同路徑
- Partner API 申請表（飛書）：https://coros-teams.feishu.cn/share/base/form/shrcnLqSduZsaNhbvDJTO2x0Vlf
- COROS API Agreement（表單連結的 PDF）：https://www.dropbox.com/scl/fi/pqrgalut8k63gr8aslp3i/COROS-API-Agreement.pdf?rlkey=h8qlr5dmokd2f8orfjchey8xk&dl=0
- COROS API Reference V2.1.1（表單連結的資料夾）：https://www.dropbox.com/scl/fo/6ps1297tn9pfo7qmcb0o8/AItfHWAW8t-jZ0NIrAaT0hg?rlkey=kbq4zmu47j9c3c6qu7b96z39f&dl=0
- COROS Official MCP 更新頁：https://www.coros.com/stories/coros-metrics/c/mcp-testing
- App Store 台灣區 COROS：https://apps.apple.com/tw/app/coros/id1277625343
- Nango COROS 文件：https://nango.dev/docs/integrations/all/coros.md
- Tredict：https://www.tredict.com/blog/coros_api_integration/ （2020-10-09）、https://tredict.com/blog/coros_training_sync （2023-06-14）
- Zwift 論壇 COROS 整合串：https://forums.zwift.com/t/coros-integration/604861

Garmin：

- Overview、FAQ、各 API：https://developer.garmin.com/gc-developer-program/overview/ 、/program-faq/ 、/activity-api/ 、/training-api/ 、/health-api/ 、/courses-api/
- 申請表：https://www.garmin.com/en-US/forms/GarminConnectDeveloperAccess/ ；快照：https://web.archive.org/web/20251204045725/https://www.garmin.com/en-US/forms/GarminConnectDeveloperAccess/ 、https://web.archive.org/web/20240822120027/https://www.garmin.com/en-US/forms/GarminConnectDeveloperAccess/
- Developer Program Agreement（FRM-0952 Rev. B）：https://developerportal.garmin.com/sites/default/files/Garmin%20Connect%20Developer%20Program%20Agreement.pdf
- Terms of Use（Effective Date: April 1, 2026）：https://www.garmin.com/en-US/legal/terms-of-use/
- 論壇：https://forums.garmin.com/developer/connect-iq/f/discussion/434761/apply-for-connect-developer-program-access-request-form-down （含 RSS）、/441459/connect-developer-program-what-should-a-solo-developer-do-while-applications-are-paused 、/440200/connect-developer-program-activity-api-access-no-response-is-the-program-still-paused 、https://forums.garmin.com/apps-software/mobile-apps-web/f/garmin-connect-mobile-andriod/441607/garmin-connect-api-access-paused-for-months-what-are-startups-supposed-to-do 、https://forums.garmin.com/developer/connect-iq/f/discussion/443199/can-a-3rd-party-app-inject-a-structured-workout-into-the-native-workouts-list
- Terra 部落格（2026-09-07）：https://tryterra.co/blog/garmin-connect-developer-program-pause
- the5krunner（2026-09-14，今天 403，見 garmin.md）：https://the5krunner.com/2026/09/14/garmin-developer-api-access-paused/
- Health SDK：https://developer.garmin.com/health-sdk/overview/
- Connect IQ：https://developer.garmin.com/connect-iq/overview/ ；Stryd 課表 app：https://blog.stryd.com/2020/04/02/stryd-connect-iq-power-based-workouts-app/ （摘要）

其他：

- Polar：https://www.polar.com/accesslink-api/ 、https://www.polar.com/en/legal/polar-api-agreement
- Suunto：https://apizone.suunto.com/ 、https://apizone.suunto.com/faq 、https://www.suunto.com/welcomepartners
- Apple：https://developer.apple.com/app-store/review/guidelines/ 、https://developer.apple.com/support/terms/apple-developer-program-license-agreement/
- Terra 課表推送相容性：https://docs.tryterra.co/planned-workouts-api/provider-compatibility.md

Repo 內既有文件：`docs/research/garmin.md`、`docs/research/data-hubs.md`、`docs/research/coros-navigation-with-workouts.md`、`docs/research/coros-threshold-unification.md`、`docs/plans/todo-multi-user-sharing.plan.md` §2.2、§7.5、§9、§10.3；SP-343 的調查（聚合商與品牌 API、intervals.icu 重查、TrainingPeaks 與 Final Surge、Strava 等、Apple Health 與 Health Connect）。
