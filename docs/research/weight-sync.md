# 體重同步：Apple Health、COROS、Garmin、intervals.icu、TrainingPeaks（SP-472）

> 調查日期：2026-10-13。只做調查，沒有改程式。
> 標記：**已驗證**＝今天抓了一手頁面（官方說明中心的 Zendesk 公開 JSON、Apple 開發者文件 JSON、官方 OpenAPI、GitHub 原始檔）並對過原文；**摘要**＝搜尋摘要、論壇使用者說法或二手轉述；**推估**＝我的推論；**未找到來源**＝查不到。
> 規則：沒有登入任何第三方帳號、沒有碰 NAS 與擁有者的資料、沒有讀任何金鑰。

## 摘要

1. **Apple Health 的體重，網頁 app 和我們的伺服器都讀不到。** HealthKit 只存在手機上，沒有雲端 API；手機鎖著的時候連手機上的 app 都讀不到。所以一定要靠**手機上的某個東西主動把體重送過來**：iOS 內建的「捷徑」（免費）、Health Auto Export（付費 app），或手動匯出 `export.xml` 再上傳。
2. **擁有者「Apple Health → COROS → 我們」這條路不通。** COROS App 跟 Apple Health 是單向的，只把 COROS 的資料寫進 Apple Health，清單裡也沒有體重。COROS 帳號裡的體重是**個人資料的設定值**，只有使用者在 COROS App 或 Training Hub 手動改才會變，不是量測紀錄。
3. **app 其實已經拿到 COROS 的體重，只是幾乎用不到。** COROS 登入時會把帳號體重存成當天一筆 `athlete_settings.weight_kg`。可是只要設定頁的體重紀錄（plan.json）有任何一筆，算 W/kg、RE 就只看 plan.json，COROS 的值完全不會用到（擁有者正是這種情況）。現在 COROS 的值只用來在首次精靈預填體重。
4. **Garmin**：Garmin Connect 有真正的體重量測紀錄（Index 體重計或手動輸入）。官方 Health API 的申請從 2026 春天暫停到現在；非官方 `garminconnect` 有 `get_weigh_ins`，但違反 Garmin 使用條款，只能自架、個人使用。Garmin Connect 也**不會**讀 Apple Health。
5. **intervals.icu** 的 wellness 有 `weight` 欄位，可以從 Garmin、Strava、HealthFit、Health Sync（Apple Health）收體重。SP-346 接上之後多拿一個 wellness 讀取權限就有。但 2026-10-08 起論壇說 Garmin 改了合作條款，intervals.icu 可能要在 API 擋掉 Garmin 來源的資料。**TrainingPeaks** 官方明寫 Apple Health 的資料（步數、靜息心率、睡眠）不會同步過去，而且 TP 本來就只給擁有者用。
6. **建議 A（推薦）：做一個「寫入體重」的 API 加個人 token，附一個 iOS 捷徑範本。** 使用者每天一個自動化，或量完體重開一下捷徑，就把 Apple Health 最新的體重送進來。任何體重計、任何手錶平台，只要寫進 Apple Health 就能用；免費；不用申請任何平台的審核。前提是 SP-406（登入模式下登入攔截認得 Bearer token）。同一個 API 也能收 Health Auto Export 的格式（做法 B）。手錶平台的體重（做法 C）當作補充，不當主要來源。

---

## 1. app 現在怎麼存、怎麼用體重

| 項目 | 現況 | 出處 |
|---|---|---|
| 主要的體重紀錄 | plan.json 的 `weights`：一串「日期＋公斤」。設定頁「一般設定」編輯，`PUT /api/v1/plan/profile` **整串取代** | `backend/engine/planning.py:687-703`、`backend/api/plan.py:602-633`（`:626` 整串取代） |
| 某一天的體重 | `Plan.weight_on(day)`：當天或之前最近的一筆；比第一筆還早的日子用第一筆 | `backend/engine/planning.py:706-713` |
| 圖表與模型讀體重的順序 | `Dataset.setting("weight")` 先問 `plan.weight_on`；**只要 plan 有任何一筆，就永遠有值**，不會往下讀 app DB 的 `athlete_settings` | `backend/engine/wko5expr/dataset.py:764-768`；DB 那層在 `backend/engine/wko5expr/fitdataset.py:979-1003` |
| COROS 帳號的體重 | 登入回應的 `weight` 存成當天一筆 `athlete_settings.weight_kg`（跟 LTHR 同一列） | `backend/sync/coros_client.py:238-261` |
| COROS 體重的用途 | 只在 plan 沒有任何體重時，當首次精靈的預填值（`_app_weight`） | `backend/api/plan.py:444-446`、`:476-477`、`:487-504` |
| 每次同步重讀的 COROS 帳號資料 | `GET /account/query` 每次同步都讀，但只解析心率（`parse_account`），沒讀體重 | `backend/sync/coros_client.py:312-321`、`backend/engine/hr_profile.py:120-143` |
| 體重怎麼影響 RE（SP-444、SP-461） | 有體重：每趟用「那一天」的體重算 RE（`run_mass`）；完全沒有體重：RE 改成速度÷瓦 | `backend/engine/racepower/athlete.py:222-232`、`backend/engine/racepower/re.py:31-50` |
| 改體重的代價 | 存檔後 `_notify(True)`：重算門檻、自動課表重推功率目標；plan 的內容（含體重）是估算快取的鍵，改一筆就整批重算 | `backend/api/plan.py:93-99`、`:632`；`backend/engine/wko5expr/fitdataset.py:1180-1181` |
| TrainingPeaks 的帳號設定 | `GET /api/v1/sync/tp/settings` 讀得到 TP 帳號的體重，但只回給前端看，沒有存 | `backend/sync/tp_client.py:1070-1088`、`backend/api/sync.py:573-579` |
| API token | `accounts.db` 有 `api_tokens` 表（sha256），目前只發給 inReachLiveTrack 用的 `link:*` scope；登入攔截不認 Bearer，帶 token 的請求會被 401 | `backend/accounts/store.py:116-123`、`:691-724`；`backend/membership.py:89`、`:338-352`；`backend/tenancy_mw.py:7-22` |

**對使用者問題的意思**：擁有者只有一筆體重紀錄，所以 app 所有地方都用這一筆；就算 COROS 帳號的體重改了、app 也存到了，畫面和計算都不會變。

**效能提醒（推估）**：如果每天自動寫進一筆體重，依現在的設計，每次都會讓估算快取整批失效、重推課表。NAS 比本機慢 8～9.5 倍。所以自動同步不能「收到就寫」：要先存原始量測，只有跟目前生效的體重差到門檻（例如 ≥ 0.5 kg）、或每週一次，才寫進 plan 的體重紀錄。這張單如果實作，要加 `效能` tag。

---

## 2. COROS

### 2.1 拿得到什麼

| 管道 | 有沒有體重 | 性質 | 出處 |
|---|---|---|---|
| 帳密登入的回應（app 現在用的） | 有，`result["weight"]` | 帳號的個人資料設定值 | `backend/sync/coros_client.py:243`（實際欄位值沒看，規則不讀擁有者帳號） |
| `GET /account/query`（每次同步） | 推估也有（同一份帳號資料），程式沒讀 | 設定值 | `coros_client.py:283-286` 的說明只確認了 zoneData；體重**未驗證** |
| FIT 檔 `user_profile`（訊息 3，`weight` 欄位，單位 kg、scale 10） | **沒有**：COROS 的 FIT 不帶 user_profile 訊息 | — | `backend/engine/hr_profile.py:22-23`（「FIT files carry no zone / user_profile messages (checked on COROS FITs)」）；FIT 定義見 fitparse `profile.py`（user_profile mesg_num 3、field 4 `weight`） |
| 官方 COROS MCP（OAuth、免申請） | 有：`queryUserInfo`「Query basic user profile including height, weight, birthday, and gender.」 | 設定值 | https://github.com/coroslab/COROS-MCP （README，**已驗證**）；https://support.coros.com/hc/en-us/articles/53181619102996-Build-on-COROS-MCP |
| COROS Partner API（要申請） | `getUserInfo`、`getDailyData`；欄位裡有沒有體重**未找到來源**（API Reference PDF 在申請表裡，本次沒開） | — | https://support.coros.com/hc/en-us/articles/53181766856724-Partner-API-Access （**已驗證**） |

### 2.2 是「設定值」還是「量測紀錄」

- COROS 自己把體重放在個人資料裡：Training Hub「Personal Information: Change your profile photo, nickname, height/weight, location, units of measurement…」（**已驗證**，https://support.coros.com/hc/en-us/articles/4412383468180-COROS-Training-Hub-Athlete-Tutorial ）。
- 卡路里說明也叫使用者「check your profile settings to ensure that your weight, height, and gender are correct」（**已驗證**，https://support.coros.com/hc/en-us/articles/4406604054932-Measuring-Active-Calories-on-COROS-Watches ）。
- COROS 沒有體重計產品，說明中心搜「weight」也沒有任何體重紀錄、體重趨勢的功能（**已驗證**：Zendesk 搜尋 API，2026-10-13）。
- 結論：**是設定值，沒有歷史**。只有使用者手動改它才會變。

### 2.3 多久更新一次

- app 端：每次 COROS 登入就存一筆（手動登入，或勾了「記住密碼」後 token 過期〔24 小時〕自動重登）。日期是登入那天，不是使用者改體重那天（`coros_client.py:221`、`:244-259`、自動重登 `:369`）。
- COROS 端：使用者手動改才會變（見 2.2）。

### 2.4 Apple Health 改了體重，會不會傳到 COROS 再到我們

**不會。** COROS 官方說明：「In order to receive daily and training data collected from the COROS app to your Apple Health app…」，可選的資料只有 Cycling Distance、Heart Rate、Sleep Analysis、Steps、Swimming Distance、Walking + Running Distance（**已驗證**，2026-10-13 更新，https://support.coros.com/hc/en-us/articles/360041549551-Connecting-Apple-Health-with-COROS-App ）。方向是 COROS → Apple Health，而且沒有體重。

### 2.5 條款

現在的帳密登入，COROS 使用者條款不允許拿來做商業服務（`docs/research/watch-official-apis.md` 摘要第 1 點）。商業版要讀 COROS 體重，走 COROS MCP 的 `queryUserInfo`，但那仍是設定值。

---

## 3. Apple Health

### 3.1 網頁或自架伺服器能不能直接讀

**不能。**

- 「The user's device stores all HealthKit data locally. For security, the device encrypts the HealthKit store when the user locks the device. As a result, your app may not be able to read data from the store when it runs in the background.」（**已驗證**，https://developer.apple.com/documentation/healthkit/protecting-user-privacy ）
- 要讀 HealthKit 就得做原生 iOS app：開發者年費 US$99、App Store 審查、5.1.2 的揭露與同意（`docs/research/sync-platforms.md` §3.6、`docs/research/watch-official-apis.md` §4.3）。

### 3.2 替代做法

| 做法 | 使用者要做什麼 | 限制 | 出處 |
|---|---|---|---|
| **iOS 捷徑（Shortcuts）** | 1. 在 app 設定頁產生一個「只能寫體重」的 token。2. 加入我們提供的捷徑（iCloud 連結）。捷徑內容：「尋找健康樣本」（體重、最新優先、取 N 筆）→「取得 URL 的內容」用 POST 送 JSON，標頭 `Authorization: Bearer <token>`。3. 在「自動化」設每天固定時間執行並設成不用確認；或設「打開／關閉體重計 app 時」執行 | 手機**鎖著時讀不到健康資料**（Apple 文件同上；論壇實例：「There was an error trying to read workout samples from Health: Protected health data is inaccessible」，解鎖時正常）。所以固定時間的自動化可能剛好遇到鎖機而失敗，建議用「打開 app 時」觸發，或時間選在通常在用手機的時候。只有 iPhone 能用 | 自動化不用確認、不會通知：「Some personal automations can run without asking you for confirmation」「The automation will not notify you when it's triggered.」（**已驗證**，https://support.apple.com/guide/shortcuts/create-a-new-personal-automation-apdfbdbd7123/ios ）；鎖機錯誤：https://talk.automators.fm/t/get-workouts-from-health-not-working-when-iphone-locked/13100 （**摘要**，2022 年使用者回報） |
| **Health Auto Export（第三方 app）** | 買 Premium（訂閱或買斷），新增 REST API 自動化：填 URL、加 `Authorization: Bearer <token>`、選指標 `weight_&_body_mass`、選頻率 | 「Automations will only run during periods when your device is unlocked.」；靠背景重新整理，低電量模式或很久沒用會延遲；送的是它自己的 JSON 格式（`name`／`units`／`data[{qty, date}]`，日期 `yyyy-MM-dd HH:mm:ss Z`），我們要寫一個解析器 | REST 設定與限制：https://help.healthyapps.dev/en/health-auto-export/automations/rest-api （**已驗證**）；格式與 `weight_&_body_mass`：https://help.healthyapps.dev/en/health-auto-export/export-format/health-metrics （**已驗證**）；自動匯出要 Premium：App Store 說明（**已驗證**，https://apps.apple.com/app/id1115567069 ）；價格 US$6.99／年見 `sync-platforms.md` §3.6 |
| **匯出 export.xml 上傳** | 健康 App → 摘要 → 頭像 →「輸出所有健康資料」→ 把 zip 傳到 app 的匯入頁 | 手動、每次都是全部資料，檔案可能很大（推估，幾十到幾百 MB）；只適合一次補齊歷史。檔案裡體重的類型是 `HKQuantityTypeIdentifierBodyMass`（推估，Apple 沒有公開 export.xml 格式規格，未找到來源） | 「You can export all of your health and fitness data from Health in XML format… Tap Export All Health Data」（**已驗證**，https://support.apple.com/guide/iphone/share-your-health-data-iph5ede58c3d/ios ） |
| **經 intervals.icu 中轉** | 裝 HealthFit 或 Health Sync，把 Apple Health 的體重送到 intervals.icu，我們再從 intervals.icu 讀（見 §5） | 要等 SP-346；多一個第三方；同時從 Garmin 也拿體重時，intervals.icu 沒定義哪個來源優先（**摘要**，論壇） | https://forum.intervals.icu/t/health-sync-now-supports-intervals-icu/92287 、https://forum.intervals.icu/t/healthfit-sync-for-activities-and-health-metrics/2718 （**摘要**） |
| 原生 iOS app | 裝 app、授權 HealthKit | US$99／年、審查、要另外維護一個 app | `watch-official-apis.md` §4.3 |

**token 的現況**：捷徑和 Health Auto Export 都要帶 Bearer token。repo 有 `api_tokens` 表與 `check_service_token`，但只認 `link:*` scope，而且登入攔截還不認 Bearer（SP-406 已拍板做法 A「攔截也認 Bearer，依 token 的使用者與 scope 放行」，還沒做）。另外登入模式下寫入要帶 CSRF 標頭（`tenancy_mw.py:23`），用 Bearer 的請求要排除在外。正式站現在是擁有者模式，對外有沒有另一層保護（例如 Cloudflare Access）本次沒查（不碰 NAS）。

---

## 4. Garmin

| 管道 | 有沒有體重 | 條件與風險 | 出處 |
|---|---|---|---|
| 官方 Health API | 有：「Data includes heart rate, steps, calories, sleep, respiration, body composition」 | 頁面頂端「Stay tuned for more updates on the program」；申請從 2026 春天暫停；只給企業；收費使用要 Garmin 書面許可 | https://developer.garmin.com/gc-developer-program/health-api/ （**已驗證** 2026-10-13）；`docs/research/watch-official-apis.md` §3.3-3.5 |
| 非官方 `garminconnect`（0.3.17，2026-09-29） | 有：`get_weigh_ins(startdate, enddate)` → `/weight-service/weight/range/...`；`get_body_composition`、`get_daily_weigh_ins` | Garmin 使用條款禁止「accesses, copies, or scrapes content from the Site through any means not purposely made available through the Site」，帳號可能被停權；只能自架、個人使用。repo 的 SP-91 試運行已經用這個套件（`backend/sync/garmin_client.py:1-46`），加讀體重只要多一個請求 | 方法：https://github.com/cyberjunky/python-garminconnect/blob/master/garminconnect/__init__.py （`get_weigh_ins`、`get_body_composition`，**已驗證**）；條款：`docs/research/garmin.md` §2.4 |
| FIT 檔 | 定義上有：`weight_scale`（訊息 30：weight、percent_fat、muscle_mass…）、檔案類型 9 `weight`。這是體重計產生的檔，**跑步的活動檔裡不會有**（推估）；要從 Garmin Connect 匯出或從裝置拿 | 我們沒有管道自動拿到這種檔 | fitparse `profile.py`（`weight_scale` mesg_num 30、`weight` scale 100 kg；file 9 `weight`） |
| Garmin Connect 讀 Apple Health | **不會**：「Garmin Connect pushes data to Apple Health but doesn't currently pull (read) data from an Apple Watch or Apple Health.」 | 所以 Apple Health 的體重進不了 Garmin | https://the5krunner.com/2025/06/20/garmin-to-add-health-connect-support-to-apple/ （**摘要**，二手；Garmin 支援頁本次抓不到） |

---

## 5. intervals.icu 與 TrainingPeaks

### 5.1 intervals.icu（SP-346 計畫接）

- API 有體重：`Wellness` 的 `weight`（`GET /api/v1/athlete/{id}/wellness{ext}?oldest=&newest=`），運動員設定另有 `icu_weight`（**已驗證**，https://intervals.icu/api/v1/docs ，2026-10-13）。
- 體重來源：Garmin 的 wellness 同步、Strava（「Intervals.icu can sync weight from Strava whenever it downloads a new activity」）、HealthFit、Health Sync（Apple Health）（**摘要**，論壇）。Intervals Companion 有沒有送體重，**未找到來源**。
- 要加什麼：SP-346 原本只要活動讀取權限；要體重就多申請 wellness 讀取（SP-346 描述已寫「wellness 視需要再加」）。每天讀一次 wellness 就夠。
- 風險：2026-10-08 論壇：「Garmin has changed its partner terms and David expects Intervals will have to block Garmin-sourced data in its API」；「The clause only governs data that Intervals receives through Garmin's partner API.」（**摘要**，https://forum.intervals.icu/t/garmin-intervals-bridge-your-complete-garmin-fit-files-and-wellness-data-in-intervals-icu-with-or-without-the-official-garmin-integration/132863 ）。Garmin 來的體重可能之後從 API 拿不到；Apple Health 經 HealthFit／Health Sync 送進去的不受影響（推估）。

### 5.2 TrainingPeaks

- 「Please note that Apple Health data (such as daily steps, resting heart rate, or sleep) does not sync with TrainingPeaks at this time.」（**已驗證**，https://help.trainingpeaks.com/hc/en-us/articles/48096960116749-TrainingPeaks-and-Apple-Watch ）
- 體重可以從 Garmin（Index 體重計或 Garmin Connect 手動輸入）同步到 TP：「you can also manually enter your weight in Garmin Connect, which will then sync to TrainingPeaks」（**已驗證**，https://help.trainingpeaks.com/hc/en-us/articles/204070854-How-to-Sync-Garmin-Connect-With-TrainingPeaks ）；也能在 TP 手動記 metric。
- app 已讀得到 TP 帳號設定裡的體重（`tp_client.py:1070-1088`），但沒存。TP 的網站登入只給擁有者用（`backend/membership.py:26-28`：TP terms §11(e)、§23），**不能當所有人的來源**。

---

## 6. 做法比較

| | **A. 體重 API＋iOS 捷徑範本（推薦）** | B. 同一個 API 也收 Health Auto Export | C. 從手錶平台讀（COROS 設定值／Garmin／intervals.icu） |
|---|---|---|---|
| 拿到的是 | Apple Health 的**量測紀錄**（任何體重計、手動輸入、任何會寫進 Apple Health 的 app） | 同 A | COROS：設定值；Garmin：量測紀錄；intervals.icu：看它從哪收 |
| 擁有者能不能解決 | 能（他本來就用 Apple Health 記體重） | 能，但要付 Health Auto Export Premium | COROS 不能（Apple Health 不會進 COROS）；Garmin、intervals.icu 要他另外設定 |
| 對所有使用者 | iPhone 使用者都能用；Android 沒有內建捷徑可讀 Health Connect（推估，要另找 app） | iPhone，且要付費 | COROS MCP 對所有 COROS 使用者可用但只有設定值；Garmin 官方申請不到；intervals.icu 要等 SP-346 核准 |
| 自動程度 | 自動化每天跑，或量完開一下；鎖機時會失敗 | 背景跑，但也只在解鎖期間 | 每次同步順手讀 |
| 工作量（推估） | M：寫入端點（附加、同日去重、記來源、合理範圍檢查）、設定頁發／撤銷 token（scope `weight:write`）、捷徑範本與說明頁、體重變動門檻（避免天天重算）。**前提 SP-406**（Bearer 進得了攔截） | S（做完 A 之後）：多一個格式的解析器 | COROS：S（同步時讀 `/account/query` 的體重，跟最新紀錄差很多時提示）；Garmin 非官方：S（SP-91 試運行多一個請求）；intervals.icu：S（SP-346 做完後） |
| 審核與費用 | 都不用 | 使用者付費；我們不用 | COROS MCP 免申請；Garmin 官方暫停；intervals.icu OAuth 要人工核准 |
| 隱私與條款 | 體重是健康資料，資料從使用者手機主動送來，不經第三方；token 只能寫體重、可撤銷、存雜湊；隱私權政策要寫「接收體重」 | 多一個第三方 app，但它聲明不收資料（App Store 說明） | Garmin 非官方違反條款（只能自架）；COROS 帳密登入不能商用；intervals.icu 有 Garmin 條款變動的風險 |

**推薦 A，B 順手支援，C 只做 COROS 的「提示」不做自動覆蓋。** 理由：

1. 擁有者的資料就在 Apple Health，只有 A／B 碰得到；手錶平台都拿不到 Apple Health 的體重。
2. A 不用任何平台審核，也不違反任何條款，自架版、商業版都能用。
3. 手錶平台的體重多半是設定值（COROS）或申請不到（Garmin 官方），當主要來源不可靠。

---

## 7. 要使用者決定的問題

1. **選哪個做法？** 建議 A（API＋捷徑），B 順手支援；C 只做 COROS 帳號體重變動時的提示。
2. **自動寫入的頻率**：建議原始量測全部存起來，但 plan 的體重紀錄只在「跟目前生效的體重差 ≥ 0.5 kg」或「距上次寫入滿 7 天」時才新增一筆，避免天天整批重算、重推課表（效能）。
3. **來源衝突**：建議設定頁手動輸入的優先；同一天多筆取最後一筆；每筆標來源（Apple Health／COROS／手動）。
4. **COROS 帳號的體重要不要也寫進體重紀錄？** 建議不要自動寫（它是設定值，常常是很久以前填的）。只在它跟最新紀錄差 ≥ 1 kg 時，在設定頁提示「手錶帳號的體重是 X kg，要更新嗎？」。
5. **排程**：A 要等 SP-406（Bearer 認證）。擁有者模式要不要先做一版（只給擁有者、固定一個 token）？建議先做，正式站現在就是擁有者模式；SP-406 做完再開給所有人。
6. **歷史回填**：要不要做 export.xml 匯入？建議不做，捷徑一次送最近 N 筆（例如 90 天）就能補。
