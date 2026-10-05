# 課表和導航路線一起上 COROS 手錶（SP-92）

> 調查日期：2026-10-05。只做調查，沒有改程式，沒有用你的帳號呼叫任何 COROS API。只讀了程式碼和公開網頁。
> 標記：**已驗證**＝這次讀到原文（COROS 支援中心文章是用它公開的 Help Center JSON 讀全文，GitHub 專案是讀原始碼）；**摘要**＝只讀到別人的轉述或擷取工具的摘要，沒有逐字核對；**推估**＝我的延伸，沒有來源直接寫；**未找到來源**＝找過，沒有找到任何文件。
> 「要實機確認」＝推估裡最好在手錶上試一次的項目，清單在 §5。

## 摘要

1. **手錶可以同時跑課表和導航。** COROS 2024 年 4 月的更新加了「Navigation during Run and Trail Run structured workouts — Now you can do both!」：先打開當天的課表，按 Start 前往下捲到 Navigation 選路線。只能在 Run 和 Trail Run 模式用。（已驗證）
2. **所以你要的效果現在就做得到，而且 app 不用改。** app 已經會把「賽事計算機 → 匯出至課表」的比賽課排在比賽日推到手錶（每段一步，步驟名稱像「→ 補給站 2 · 約 1:35 · 爬 640 m」，目標是功率或心率）。你只要另外把比賽的 GPX 匯入 COROS app、同步到手錶一次。比賽當天打開 Trail Run，手錶會問要不要跑今天的課表，接著選 Navigation 和路線，再按 Start。
3. **app 沒辦法幫你「推送」路線。** 我們用的非官方 Training Hub API 只有課表和行事曆的端點，找不到上傳路線的端點。COROS 官方的 MCP 明寫「No GPX route import/export」。能上傳路線的只有 Partner API（`route/push`），要用公司名義申請，個人 app 不符合。（已驗證）
4. **COROS 自己的「Pace Strategy（配速策略）」是另一條路，但不能拿來放我們的目標。** 它只能在手機 app 裡建立和編輯，任何 API 都沒有它。目標只有配速，沒有功率和心率。我也沒找到它能不能和課表同時跑的說明。（已驗證／未找到來源）
5. **建議：** 先用方案 (a)「課表照推＋GPX 手動匯入一次」，現在就能用。如果要讓 app 多做一點，最值得做的是「下載手錶用 GPX」：把賽事計算機裡的補給站寫成 GPX 航點，名字和課表步驟一致。這樣手錶的航點頁和課表步驟會對得起來。工作量約半天到一天，風險低。不建議逆向找路線上傳 API，也不建議做 FIT course。

## 1. COROS 手錶能做什麼

### 1.1 課表＋導航同時跑

| 說法 | 標記 | 來源 |
|---|---|---|
| 「Navigation during Run and Trail Run structured workouts. Now you can do both! Open your workout for the day in the activity menu, and before pressing Start, scroll down to Navigation to select a route. You can receive deviate course alerts and turn alerts in addition to your regular workout and activity alerts. This feature is only available in Run and Trail Run modes.」 | 已驗證 | [April 2024 Feature Update Highlights](https://support.coros.com/hc/en-us/articles/25903806327828-April-2024-Feature-Update-Highlights) |
| 排在某一天的課表「只能在那天跑」：打開運動模式時，手錶會提示要不要跑今天的課表。Run 的課表可以用 Run、Track Run、Trail Run、Indoor Run 模式跑。 | 已驗證 | [How to Follow Structured Workouts](https://support.coros.com/hc/en-us/articles/360044426251-How-to-Follow-Structured-Workouts-on-COROS-Watches) |
| 跑課表時，手錶會多一個「目前階段」的主頁面，自己設定的資料頁要轉錶冠才看得到。 | 已驗證 | 同上 |
| 導航時會多一個地圖頁。有航點的路線還會多一頁，顯示下一個航點的名稱、剩下的距離和爬升。 | 已驗證 | [Using Navigation Features](https://support.coros.com/hc/en-us/articles/360039841072-Using-Navigation-Features)、[Using Pins and Waypoints](https://support.coros.com/hc/en-us/articles/360055691511-Using-Pins-and-Waypoints) |
| 活動開始後也能中途載入路線（長按 Back → Navigation → 選路線），手錶會自己跳到你在路線上的位置。也能在跑步中從手機同步一條新路線過去。 | 已驗證 | [Using Navigation Features](https://support.coros.com/hc/en-us/articles/360039841072-Using-Navigation-Features)、[Route Syncing to Watch During Activity](https://support.coros.com/hc/en-us/articles/6504037499284-Route-Syncing-to-Watch-During-Activity) |
| 跑課表中途載入路線也可以。 | 推估（要實機確認） | 上面兩篇沒有排除課表中的活動 |
| 哪些機型有這功能：2024 年 4 月的更新文章沒有列機型。有導航的機型是 PACE 3／PACE Pro／PACE 4、NOMAD、APEX 42/46 mm／APEX Pro／APEX 2／APEX 2 Pro／APEX 4、VERTIX／VERTIX 2／2S。PACE 2 有兩篇文章說法不一樣：一篇說 Run 模式有 Nav Settings，另一篇說「The PACE 2 does not support route navigation」。 | 已驗證（機型清單）；PACE 2 有矛盾 | [Run vs Indoor Run vs Trail Run](https://support.coros.com/hc/en-us/articles/4409122319892-Run-vs-Indoor-Run-vs-Trail-Run-Explained)、[Using Strava Route Sync](https://support.coros.com/hc/en-us/articles/4408407031828-Using-Strava-Route-Sync) |
| 韌體版本：各機型的韌體說明沒有寫「課表＋導航」這一條。APEX 2 Pro 的 V3.0408.0（2024-05-10）和 2024 年 4 月更新文章裡的其他項目（Outdoor Climb 的新 GPS 演算法）一樣，所以這一版應該就是那次更新。KIPRUN GPS 900（COROS 代工的迪卡儂手錶）在 V3.0508.0（2024-08-14）寫了「Use a structured workout with navigation during your Trail Runs」。 | 推估（版本對應）；KIPRUN 那句已驗證 | [APEX 2 Pro Release Notes](https://support.coros.com/hc/en-us/articles/20087492454932-COROS-APEX-2-Pro-Release-Notes)、[KIPRUN GPS 900 Release Notes](https://support.coros.com/hc/en-us/articles/20087889222036-KIPRUN-GPS-900-Release-Notes) |

結論：2024 年中以後更新過韌體、而且有導航功能的 COROS 手錶，都可以在 Trail Run 模式同時跑課表和路線。（推估，依據是上表的已驗證項目）

### 1.2 載入路線後手錶會顯示什麼

| 功能 | 內容 | 標記 | 來源 |
|---|---|---|---|
| 地圖頁 | 你在路線上的即時位置，錶冠可以縮放 | 已驗證 | [Using Navigation Features](https://support.coros.com/hc/en-us/articles/360039841072-Using-Navigation-Features) |
| 偏離路線警示 | 偏離 20 m 超過 10 秒就提醒，回不來就每分鐘提醒一次 | 已驗證 | 同上 |
| 轉彎提示（Turn by Turn） | 只有 Run 和 Trail Run 模式有 | 已驗證 | 同上 |
| 高度圖 | 2026 年 1 月起，導航中的高度頁只顯示「剩下的爬升」 | 已驗證 | [January 2026 Feature Update](https://support.coros.com/hc/en-us/articles/45216666318484-January-2026-Feature-Update-Highlights) |
| 航點頁 | 下一個航點的名稱、剩下的距離和爬升 | 已驗證 | [Using Pins and Waypoints](https://support.coros.com/hc/en-us/articles/360055691511-Using-Pins-and-Waypoints) |
| Hill Alerts（爬坡提示，類似 Garmin ClimbPro） | 坡開始時提示長度、爬升、平均坡度；坡上有「Hill Progress」頁顯示剩下距離、剩下爬升、目前坡度；坡結束也會提示。**只在導航中有效**。越野路線只標平均坡度 ≥ 3 % 而且夠長的坡。2026-03-31 的 V3.1508.0 加入。 | 已驗證 | [Hill Alerts](https://support.coros.com/hc/en-us/articles/47116651977364-Hill-Alerts)、[PACE 3 Release Notes](https://support.coros.com/hc/en-us/articles/20087694119828-COROS-PACE-3-Release-Notes) |
| Hill Alerts 在「課表＋導航」時也會出現 | 文件只說「只在導航中有效」，沒有說課表中會關掉 | 推估（要實機確認） | — |
| 離開路線時爬坡頁會馬上消失 | DC Rainmaker 實測 | 摘要 | [DC Rainmaker 2026-03](https://www.dcrainmaker.com/2026/03/software-strategy-guidance.html) |
| 電量 | 導航和地圖比較耗電。超長距離建議平常停在一般資料頁，要看路線時再切過去 | 已驗證 | [Battery Conservation Tips for Ultras](https://support.coros.com/hc/en-us/articles/11608550508436-Battery-Conservation-Tips-for-Ultras) |

### 1.3 COROS Pace Strategy（配速策略）

| 說法 | 標記 | 來源 |
|---|---|---|
| 在 COROS **手機 app** 的 Profile → Pace Strategy 建立。可以依「距離」或「路線」建立；越野賽建議用路線，要從 COROS 路線庫選一條。 | 已驗證 | [Pace Strategy](https://support.coros.com/hc/en-us/articles/47115766323348-Pace-Strategy) |
| COROS 依路線高度和你的跑力自動算完賽時間和每段目標配速。可以拉桿調總時間，也可以「Expand and Edit」改單一段的目標時間或配速。路線上有開了提醒的航點時，可以輸入每個補給站要停多久。 | 已驗證 | 同上 |
| 手錶上：在 Run、Trail Run、Hike 模式往下捲到 Pace Strategy 選一個。跑步時會多幾個資料欄位：這一段的目標配速、預估完賽時間和剩下距離、比目標快或慢多少，路線版還有「到下一個航點的剩餘時間、距離、爬升」。接近航點時會倒數，到了會顯示這段快或慢、計畫停留時間。 | 已驗證 | 同上 |
| 目標只有**配速**（坡地用 Effort Pace），沒有功率或心率目標。 | 已驗證（文件只寫配速）；「沒有功率」是推估，因為文件沒提 | 同上、[March 2026 Feature Update](https://support.coros.com/hc/en-us/articles/47305529232148-March-2026-Feature-Update-Highlights) |
| COROS 自己說 50 km 以上的預測比較不準，建議依自己的經驗調整。 | 已驗證 | [Pace Strategy](https://support.coros.com/hc/en-us/articles/47115766323348-Pace-Strategy) |
| 2026-03-31 的 V3.1508.0 加入：PACE 3／PACE 4／PACE Pro、APEX 2／2 Pro／APEX 4、VERTIX 2／2S、NOMAD。DURA 只有 Hill Alerts。 | 已驗證 | [March 2026 Feature Update](https://coros.com/stories/coros-metrics/c/march-2026)、各機型 Release Notes |
| 用路線建立的 Pace Strategy 跑步時也會導航（地圖、轉彎提示） | 摘要（只有 DC Rainmaker 文章底下一則讀者留言這樣說） | [DC Rainmaker 2026-03](https://www.dcrainmaker.com/2026/03/software-strategy-guidance.html) |
| Pace Strategy 能不能和課表同時跑 | 未找到來源。它和課表都在按 Start 前的選單裡選，我推估是二選一（要實機確認） | — |
| Training Hub 網頁版能不能建立 Pace Strategy；有沒有 API 能寫入 | 未找到來源。支援文章只講手機 app；Partner API 和官方 MCP 的功能清單都沒有它 | [Partner API Access](https://support.coros.com/hc/en-us/articles/53181766856724-Partner-API-Access)、[Build on COROS MCP](https://support.coros.com/hc/en-us/articles/53181619102996-Build-on-COROS-MCP) |

### 1.4 路線怎麼進到 COROS

| 管道 | 說明 | 標記 | 來源 |
|---|---|---|---|
| 手機開 GPX | 從網站下載 GPX，在手機上用 COROS app 開啟 → Save → Sync with your device。iOS 也能從 Files、AirDrop、Email 開 | 已驗證 | [Downloading and Using Routes](https://support.coros.com/hc/en-us/articles/24181489692436-Downloading-and-Using-Routes)、[Troubleshooting GPX Route Imports](https://support.coros.com/hc/en-us/articles/360040243352-Troubleshooting-GPX-Route-Imports-for-Breadcrumb-Navigation) |
| 在 COROS app 的 Explore 頁畫路線 | 可以加航點、開航點提醒 | 已驗證 | [Explore Page](https://support.coros.com/hc/en-us/articles/15285167205268-Explore-Page) |
| Strava／Komoot 自動同步 | 帳號連動後，Strava 收藏的路線會出現在 COROS 路線庫 | 已驗證 | [Using Strava Route Sync](https://support.coros.com/hc/en-us/articles/4408407031828-Using-Strava-Route-Sync) |
| Training Hub 網頁 | 手冊和運動員教學只寫了匯入「活動」（Import Data），沒寫匯入路線 | 已驗證（手冊沒寫）；「網頁版不能匯入路線」是推估 | [Training Hub Manual](https://support.coros.com/hc/en-us/articles/4412176269844-COROS-Training-Hub-Manual)、[Training Hub Athlete Tutorial](https://support.coros.com/hc/en-us/articles/4412383468180-COROS-Training-Hub-Athlete-Tutorial) |
| Partner API `route/push` | GPX／KML，最大 50 MB。要「Established platform with demonstrated user base」和「Registered company」才能申請 | 已驗證 | [Partner API Access](https://support.coros.com/hc/en-us/articles/53181766856724-Partner-API-Access) |
| 官方 COROS MCP（OAuth，自助申請） | 能讀資料、寫課表和訓練計畫，但明寫「No GPX route import/export」 | 已驗證 | [Build on COROS MCP](https://support.coros.com/hc/en-us/articles/53181619102996-Build-on-COROS-MCP) |
| 非官方 Training Hub API | 社群專案都沒有路線上傳。CorosLink（2026-10 還在更新）留了一個 `uploadRouteToCorosAccount` 空函式，註解寫「the actual COROS route/course upload endpoint + payload … is undocumented」，目前直接丟錯誤，請使用者自己到 COROS app 匯入 | 已驗證（讀原始碼） | [JunAkerBuilds/CorosLink `electron/trainingHubService.ts`](https://github.com/JunAkerBuilds/CorosLink/blob/main/electron/trainingHubService.ts)、[cygnusb/coros-mcp `coros_api.py`](https://github.com/cygnusb/coros-mcp/blob/main/coros_mcp/coros_api.py) |

### 1.5 檔案格式和航點

- 支援「standard GPX」。GPX 沒有高度時，COROS 會顯示沒有高度資料，要先補高度再匯入。（已驗證：[Troubleshooting GPX Route Imports](https://support.coros.com/hc/en-us/articles/360040243352-Troubleshooting-GPX-Route-Imports-for-Breadcrumb-Navigation)、[No Elevation Data After Importing GPX Route](https://support.coros.com/hc/en-us/articles/15405567227028-No-Elevation-Data-After-Importing-GPX-Route)）
- 匯入的路線如果已經有航點，COROS app 會顯示，也可以編輯或刪除。（已驗證：[Using Pins and Waypoints](https://support.coros.com/hc/en-us/articles/360055691511-Using-Pins-and-Waypoints)）
- GPX 匯入的航點會不會自動打開「Waypoint Alert」：未找到來源。Pace Strategy 要用到航點時要求「with waypoint alerts enabled」，所以可能要在 COROS app 裡手動打開。（推估，要實機確認）
- FIT course（含 course point）能不能匯入 COROS app：未找到來源。支援文章只寫 GPX，Partner API 只寫 GPX／KML。

## 2. app 現在怎麼做

### 2.1 已經用到的 COROS 端點（非官方 Training Hub API）

全部在 `backend/sync/coros_client.py`（同步活動）和 `backend/sync/coros_workouts.py`（推課表）：

| 端點 | 用途 | 位置 |
|---|---|---|
| `POST /account/login`、`GET /account/query` | 登入、讀帳號（含 LTHR 等） | `coros_client.py:190`、`:306` |
| `GET /activity/query`、`POST /activity/detail/download` | 活動清單、下載 FIT | `coros_client.py:86`、`:453` |
| `POST /training/program/add`／`detail`／`query`／`delete` | 建立、讀、找、刪課表 | `coros_workouts.py:715–739` |
| `GET /training/schedule/query`、`POST /training/schedule/update` | 把課表排上某一天、拿掉 | `coros_workouts.py:741–785` |

- **沒有任何路線、航點、Pace Strategy 相關的端點。**（已驗證：整個 `backend/` 搜尋 route／course／gpx，COROS 程式裡都沒有）
- 課表 payload（`build_program`，`coros_workouts.py`）裡沒有可以掛路線的欄位：program 只有名稱、說明、步驟、時間、距離。社群專案的 program 欄位也沒有。（已驗證：讀到的欄位；「COROS 沒有這種欄位」是推估）

### 2.2 比賽課怎麼推到手錶

- 賽事計算機的「匯出至課表」（`POST /api/v1/racepower/export/plan`，`backend/api/racepower.py:740`）把計畫變成比賽日的一堂課，存進課表，再由課表本身的推送送到 COROS。
- 步驟由 `backend/engine/racepower/watch_export.py` 的 `steps_for` 產生。越野和百岳預設用「lap」模式：每一步是「直到按下計圈」，步驟名稱寫下一個地標，例如「→ 補給站 2 · 約 1:35 · 爬 640 m」。地標是補給站、坡頂、坡底、每天的終點，最多 25 步（`LAP_MAX`）。目標：可以跑的段用功率 ±3 %；陡坡或走路段用心率上限；下坡不設目標（控制、安全）。
- 這個設計本來就是因為越野路上 GPS 距離會飄，用「到地標按 lap」取代距離步驟。這和導航的「航點頁」是互補的：航點頁告訴你離補給站 2 還有多遠、還要爬多少，到了按 lap 進下一步。（推估）

### 2.3 app 裡已經有的 GPX

- 賽季計畫的每場賽事可以存一份 GPX（`backend/engine/event_gpx.py`），而且已經可以下載原檔：`GET /api/v1/plan/events/{eid}/gpx/file`（`backend/api/plan.py:248`）。
- `backend/engine/racepower/gpx.py:182` 的 `write_gpx` 可以把 Track 寫回 GPX 1.1，包含高度和 `<wpt>` 航點。
- 讀 GPX 時會留下航點名稱，`fuel.stops_from_wpts`（`backend/engine/racepower/fuel.py:105`）會把看起來像補給站的航點變成補給站列；`course.track_distance`（`backend/engine/racepower/course.py:66`）算累積距離。所以要反過來「把補給站的 km 換成座標、寫成航點」，材料都有了。
- 課表範本也可以掛一條訓練路線 GPX（`backend/engine/user_templates.py`），目前只拿來畫圖，沒有送到手錶。

## 3. 方案比較

| 方案 | 怎麼做 | 手錶上會看到什麼 | app 要做的事 | 工作量 | 風險 |
|---|---|---|---|---|---|
| **(a) 課表照推＋GPX 手動匯入** | app 照現在推比賽課。你把賽事 GPX（主辦單位給的，或 app 下載的）用 COROS app 開一次、同步到手錶。比賽當天：Trail Run → 跑今天的課表 → Navigation 選路線 → Start | 課表階段頁（這一步的功率或心率目標、步驟名「→ 補給站 2 · 約 1:35 · 爬 640 m」）＋地圖頁＋偏離警示＋轉彎提示＋高度頁＋航點頁（GPX 有航點時）＋ Hill Alerts（推估） | 不用改。可選：在匯出結果加一段操作說明 | 0；說明文字約 1 小時 | 低。唯一手動步驟是匯入 GPX，一場比賽做一次 |
| **(a+) 同上，但 app 產生「手錶用 GPX」** | 新增「下載手錶用 GPX」：原路線＋賽事計算機的補給站寫成 `<wpt>`，名稱和課表步驟一樣（例如「補給站 2」「CP3 神木」）；沒有高度的點補上平滑後的高度 | 同 (a)，而且航點頁的名字和課表步驟的名字一致；路線一定有高度 | 一個 API（用 `write_gpx`＋累積距離把 km 換成座標）、賽事計算機和匯出對話框加一顆按鈕、測試 | 約半天到 1 天 | 低。要實機確認匯入的航點有沒有自動開提醒（§5） |
| **(b) app 直接上傳路線** | 用 API 把 GPX 送進 COROS 路線庫 | 同 (a)，省掉手動匯入 | 非官方 API：找不到端點，要自己抓 COROS 手機 app 的封包逆向；官方 MCP 不支援；Partner API 要公司申請 | 大（而且不一定找得到） | 高：未公開端點會變；逆向要拿你的帳號試；Partner API 個人不符資格 |
| **(c) COROS Pace Strategy** | 在 COROS app 用同一條路線建立 Pace Strategy，把每段時間、補給站停留時間改成賽事計算機的數字。app 可以印一張「抄寫表」 | Pace Strategy 頁：這段目標配速、比計畫快或慢、預估完賽、到下一個航點的時間距離爬升、航點倒數；可能也有導航（摘要） | 沒有 API，只能手抄。app 可以加一張每個補給站的「到達時間、停留時間」表 | 抄寫表約半天；手抄每場 10–20 分鐘 | 中：只有配速沒有功率和心率；COROS 的分段不一定和我們一樣；應該不能和課表同時跑（未找到來源） |
| **(d) FIT course＋course point** | app 產生 FIT 格式的路線檔，course point 標補給站和坡頂 | 不確定。COROS 文件沒寫支援 FIT 路線 | 要加 FIT 寫入套件（`fitdecode` 只能讀） | 1–2 天 | 高：COROS 可能不收；這比較像 Garmin 的做法 |

補充：Garmin 那邊有官方 Courses API（`docs/research/garmin.md`），也能同時跑課表和 course。等 Garmin 推送真正開放後，(b)／(d) 在 Garmin 上才值得重新評估。（推估）

## 4. 建議

1. **現在就用 (a)。** 不用等程式。比賽前一週：
   - 賽事計算機 →「匯出至課表」，確認比賽課在比賽那天，推到手錶。
   - 把賽事 GPX 傳到手機，用 COROS app 開啟 → Save → Sync with your device。
   - 在 COROS app 打開這條路線，看航點有沒有出現；要用航點提醒的話，到編輯頁把 Waypoint Alert 打開。
   - 練一次：找一天把一堂普通課表和一條路線一起開，確認畫面和按鍵（§5）。
2. **想讓 app 幫忙，就做 (a+)。** 最大的好處是「課表步驟名」和「手錶航點名」一致，而且補給站位置用的是你在賽事計算機裡校正過的那一份。工作量小，不碰 COROS API。
3. **(c) 先不做。** 它和我們的功率／心率目標是兩套，而且多半不能和課表同時跑。如果哪天你想改用 COROS 的配速策略，再做抄寫表。
4. **(b)、(d) 不做。** (b) 沒有可用的公開端點；(d) COROS 不一定支援。

## 5. 要實機確認的事

| 項目 | 為什麼要確認 |
|---|---|
| 排在比賽日的課表，在 Trail Run 模式能不能在 Start 前選 Navigation | 2024 年 4 月的文章寫的是「Open your workout for the day」，應該可以，但沒試過我們推的課表 |
| 開著導航時，按 Back/Lap 鍵會不會照常進下一步 | lap 模式每一步都靠按 lap。文件只說在地圖頁按錶冠會變縮放，沒提 lap 鍵 |
| 課表＋導航時 Hill Alerts 會不會出現 | 文件只說「導航中有效」 |
| GPX 匯入的航點有沒有自動開提醒、手錶的航點頁顯示的中文名稱會不會被截斷 | 未找到來源 |
| 50 km 以上全程開導航的耗電 | 官方建議平常停在資料頁，需要時才切到地圖 |
| Pace Strategy 和課表能不能同時選 | 未找到來源 |

## 6. 未找到來源

- 課表 program 裡有沒有欄位可以掛路線或航點。
- Training Hub 網頁版有沒有匯入路線的功能，以及對應的 API 端點。
- Pace Strategy 有沒有任何 API（官方或非官方）。
- Pace Strategy 能不能和課表同時跑。
- COROS 能不能匯入 FIT 格式的路線（course point）。
- GPX 匯入的航點預設有沒有開 Waypoint Alert。

## 來源

- COROS 支援中心：[April 2024 Feature Update Highlights](https://support.coros.com/hc/en-us/articles/25903806327828-April-2024-Feature-Update-Highlights)、[How to Follow Structured Workouts](https://support.coros.com/hc/en-us/articles/360044426251-How-to-Follow-Structured-Workouts-on-COROS-Watches)、[Using Navigation Features](https://support.coros.com/hc/en-us/articles/360039841072-Using-Navigation-Features)、[Downloading and Using Routes](https://support.coros.com/hc/en-us/articles/24181489692436-Downloading-and-Using-Routes)、[Route Syncing to Watch During Activity](https://support.coros.com/hc/en-us/articles/6504037499284-Route-Syncing-to-Watch-During-Activity)、[Using Pins and Waypoints](https://support.coros.com/hc/en-us/articles/360055691511-Using-Pins-and-Waypoints)、[Explore Page](https://support.coros.com/hc/en-us/articles/15285167205268-Explore-Page)、[Hill Alerts](https://support.coros.com/hc/en-us/articles/47116651977364-Hill-Alerts)、[Pace Strategy](https://support.coros.com/hc/en-us/articles/47115766323348-Pace-Strategy)、[March 2026 Feature Update Highlights](https://support.coros.com/hc/en-us/articles/47305529232148-March-2026-Feature-Update-Highlights)、[January 2026 Feature Update Highlights](https://support.coros.com/hc/en-us/articles/45216666318484-January-2026-Feature-Update-Highlights)、[October 2026 Feature Update Overview](https://support.coros.com/hc/en-us/articles/53791318993044-October-2026-Feature-Update-Overview)、[Partner API Access](https://support.coros.com/hc/en-us/articles/53181766856724-Partner-API-Access)、[Build on COROS MCP](https://support.coros.com/hc/en-us/articles/53181619102996-Build-on-COROS-MCP)、[Troubleshooting GPX Route Imports](https://support.coros.com/hc/en-us/articles/360040243352-Troubleshooting-GPX-Route-Imports-for-Breadcrumb-Navigation)、[No Elevation Data After Importing GPX Route](https://support.coros.com/hc/en-us/articles/15405567227028-No-Elevation-Data-After-Importing-GPX-Route)、[Using Strava Route Sync](https://support.coros.com/hc/en-us/articles/4408407031828-Using-Strava-Route-Sync)、[Run vs Indoor Run vs Trail Run](https://support.coros.com/hc/en-us/articles/4409122319892-Run-vs-Indoor-Run-vs-Trail-Run-Explained)、[Battery Conservation Tips for Ultras](https://support.coros.com/hc/en-us/articles/11608550508436-Battery-Conservation-Tips-for-Ultras)、[Training Hub Manual](https://support.coros.com/hc/en-us/articles/4412176269844-COROS-Training-Hub-Manual)、[Training Hub Athlete Tutorial](https://support.coros.com/hc/en-us/articles/4412383468180-COROS-Training-Hub-Athlete-Tutorial)、[APEX 2 Pro Release Notes](https://support.coros.com/hc/en-us/articles/20087492454932-COROS-APEX-2-Pro-Release-Notes)、[PACE 3 Release Notes](https://support.coros.com/hc/en-us/articles/20087694119828-COROS-PACE-3-Release-Notes)、[KIPRUN GPS 900 Release Notes](https://support.coros.com/hc/en-us/articles/20087889222036-KIPRUN-GPS-900-Release-Notes)
- COROS 官網：[March 2026 Feature Update](https://coros.com/stories/coros-metrics/c/march-2026)
- 評測：[DC Rainmaker — COROS Spring 2026 Software Update](https://www.dcrainmaker.com/2026/03/software-strategy-guidance.html)
- 社群原始碼：[JunAkerBuilds/CorosLink](https://github.com/JunAkerBuilds/CorosLink)、[cygnusb/coros-mcp](https://github.com/cygnusb/coros-mcp)
