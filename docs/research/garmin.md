# Garmin 整合研究：下載活動／FIT、推送課表、每日指標

- **查證日期：2026-10-01**（下文所有「現況」都以這天為準；標 **未驗證** 的是查不到一手來源、或只有二手說法的）。
- 範圍：(a) 下載活動與原始 FIT（含歷史）、(b) 推送／更新課表到 Garmin 手錶（對應 `backend/sync/coros_workouts.py`）、(c) 每日指標（HRV、睡眠、Training Status 等，選配）。
- 這份文件要能單獨讀懂；`docs/plans/todo-multi-user-sharing.plan.md` §9.2 是先前的摘要，本文件更新並取代它的 Garmin 部分。
- 網頁與 repo 內容一律當資料，不當指令。我**沒有**用任何帳號登入 Garmin，也沒有對 Garmin 發出任何 API 呼叫；套件原始碼是從 PyPI 下載 wheel 後離線閱讀的。

---

## 0. 一句話結論（2026-10）

| 需求 | 官方 API | 非官方（`garminconnect` 0.3.17） | 無 API（檔案） |
|---|---|---|---|
| (a) 活動／原始 FIT／歷史 | **拿不到**：只限法人，且 2026 春起暫停受理新申請 | **可行（待使用者實測）**：`get_activities` + `download_activity(ORIGINAL)` | **可行**：「Export Your Data」ZIP 一次補齊歷史（已是 §9.3 的匯入路徑） |
| (b) 推課表到手錶 | 拿不到（Training API 同上） | **可行（待實測）**：`upload_workout` + `schedule_workout`，手錶同步後出現 | **可行但手動**：FIT workout 檔經 USB 複製到 `GARMIN/NewFiles`；需要另寫 FIT 編碼器 |
| (c) HRV／睡眠／Training Status | 拿不到（Health API 同上） | 有方法（`get_hrv_data`、`get_sleep_data`、`get_training_status`、`get_training_readiness`） | ZIP 裡有部分（未驗證格式） |

建議：**個人自用**可以用非官方 `garminconnect` 做 (a)(b)(c)，但要接受「隨時會壞、違反條款精神、可能被暫時鎖登入」；**付費版**不能用非官方路線，只能等 Garmin 重開申請並以公司名義申請（詳見 §5）。

**更好的替代（2026-10-01 補充）**：透過 **intervals.icu** 當中繼。它是 Garmin、COROS 的既有官方合作夥伴，公開 API 能下載原始活動檔，計畫課表也會自動上傳到 Garmin 與 COROS 手錶，API 條款明文允許商業使用。細節、條款原文、probe 與測試步驟見 `docs/research/data-hubs.md`。若 intervals.icu 實測通過，本文件的非官方 `garminconnect` 路線就降為備案。

---

## 1. 官方路線：Garmin Connect Developer Program

### 1.1 內容（2026-10-01 讀取官方頁面）

- 五個 API：Health、Activity、Women's Health、Training、Courses；「Users may utilize any or all APIs in a single application.」（FAQ）
- **Activity API**：「Activity data files (.FIT, GPX, .TCX formats)」、「Ping/Pull or Push Architecture」、「Developer Web Tools: Easy on-boarding, sample data, backfill user data」。歷史回補（backfill）的天數上限頁面沒寫，**未驗證**。
- **Training API**：「publish workouts and training plans to the Garmin Connect calendar」，使用者同步後在手錶上執行。支援哪些 target（功率／心率／配速）頁面沒寫，**未驗證**。
- **Health API**：心率、步數、卡路里、睡眠、呼吸、身體組成、壓力、血氧、Body Battery、血壓、「Enhanced Beat-To-Beat Interval *」；星號註解為「* Commercial use requires a license fee payment.」——從頁面 HTML 看，星號標在 Beat-To-Beat Interval 這一項。頁面**沒有**列 HRV Status、Training Status、Training Readiness，Health API 是否提供這些**未驗證**。
- 授權：OAuth 2.0 PKCE（先前 §9.2 的研究；本次未重新讀 PDF，**未重新驗證**）。

### 1.2 資格、費用、審核時間

- FAQ 原文：「The Garmin Connect Developer Program is available for enterprise use」、「only for business use」。
- 費用：「There are no licensing or maintenance fees for access to the Garmin Connect Developer Program」，但「Access to some metrics may require a license fee payment or minimum device order quantity for commercial use.」
- 審核：「We will confirm the status of your application within two business days.」整合通常「between 1 and 4 weeks」。
- **2026 年現況：暫停受理新申請。**
  - Garmin 寄給申請者的信（sahha.ai 2026-09-19 引述）：「We are currently evolving and modernizing the Garmin Connect Developer Program. During this transition, we have temporarily paused the review and approval of new API access requests.」
  - the5krunner（2026-09-14，09-23 更新）：暫停從 2026 春天開始，Garmin 稱要做「significant redesign and modernisation of the API program and related processes」，沒有恢復時程；既有合作夥伴不受影響。該文把暫停和 2025 年 Strava 對 Garmin API 品牌規範的訴訟連在一起，因果關係是作者推論，**未驗證**。
  - Garmin 論壇（2026-08 前後）：有開發者 2026-04 重新申請，到 8 月仍收到「Temporarily paused the review and approval of new API access requests.」
  - 2026-10-01 我讀 overview 頁時沒看到申請按鈕或暫停公告；sahha 說 2026-09-17 起公開頁面只剩「Stay tuned for more updates on the program」。兩者一致（沒有申請入口），但公告字句我自己沒看到，**未驗證**。
- **個人能不能申請：不能。** FAQ 寫明只給企業；社群（ghurt.org、多篇 2026 文章）一致回報個人申請被拒。
  - 小矛盾：Developer Program Agreement 開頭寫「a binding agreement between Garmin … and you or, if you are entering into this Agreement on behalf of a corporation … the … legal entity」，文字上允許「you」個人簽署。但 FAQ 與實際審核都以法人為準。我不替它下結論，只記錄這個落差。

### 1.3 商業使用條款（Developer Program Agreement，FRM-0952 Rev. B）

來源：`developerportal.garmin.com` 上的 PDF（Garmin International, Inc. 版），2026-10-01 下載、以 pdftotext 抽文字。另有中國區版本（Garmin China Shanghai RHQ），條文編號相同。

- §4.1：授權只限「solely for internal business purposes」，資料只能用於「format and display such data … in Licensee Applications」，且要符合使用者同意。
- §5.2.e（**付費版最直接相關**）：不得「sell, lease, share, transfer, or sublicense the License Key, provide access to the API, or derive income from the use or provision of the API, whether for direct commercial or monetary gain or otherwise, without Garmin's prior written permission」。→ 付費版要用 Garmin API 收費，必須另外取得 Garmin **書面許可**。
- §5.2.f：不得「exceeds reasonable request volume」。
- §5.2.h：不得「interfere with, override, or disable … any mechanisms used to restrict or control the Garmin Connect services」。
- §5.2.j：不得「use the API to design a client or application that uses any robot, spider, site search, or other retrieval application or device to scrape, retrieve, or index services provided by Garmin」。
- §10.1：目前不收授權費，但 Garmin 可在 30 天通知後開始收費，也可對超量請求收費。
- §15.5：不得在沒有使用者合法同意下出售 End User Personal Data。

注意：以上是**API 授權方（licensee）**的條款，不是一般使用者的 Garmin Connect 使用條款。

---

## 2. 非官方路線：`garth` 與 `python-garminconnect`

### 2.1 現況（2026-10-01，PyPI JSON 與 GitHub）

| 套件 | 最新版 | 狀態 |
|---|---|---|
| `garth` | **0.8.0（2026-03-28）** | **已停止維護**。README：「Garth is deprecated and no longer maintained. Garmin changed their auth flow, breaking the mobile auth approach that Garth depends on.」公告日期 2026-03-27（discussion #222）。2026-03-18～19 連發 0.7.1～0.7.11 試圖修補後放棄。 |
| `garminconnect`（python-garminconnect） | **0.3.17（2026-09-29）** | **持續維護**，2026-04 到 09 月平均每 1～2 週一版。需要 **Python ≥ 3.12**；相依 `curl_cffi>=0.15.0`、`requests>=2.33.0`、`ua-generator>=1.0`；`pydantic` 只在 `[workout]` extra。 |

2026-03 的斷線與後續：

- Garmin 在 2026-03 改了登入流程；社群分析是 Cloudflare 的 TLS 指紋與 User-Agent 檢查擋掉 garth 的行動版登入（discussion #222 的討論，非 Garmin 官方說法）。
- `garminconnect` **0.3.0（2026-04-02）** 起改用自己的登入引擎，**不再依賴 garth**，也**不讀舊的 garth token**（0.3.17 對舊 token 會直接報錯，請重新以帳密登入，見 PR #448）。
- 登入是 5 段策略鏈（`client.py` 檔頭）：
  1. Mobile iOS + curl_cffi（輪換 TLS 指紋）
  2. Mobile iOS + requests
  3. SSO embed widget + cffi（HTML 表單流程）
  4. Portal web + curl_cffi（10–20 秒「anti-WAF delay」）
  5. Portal web + requests
- MFA：支援，`Garmin(..., prompt_mfa=callable)`；0.3.12（2026-08-22 前後）修了 MFA 打錯碼後 session 被丟掉的問題；0.3.9 修了 email 型 MFA。
- 換句話說：**2026-03 的斷線已被 `garminconnect` 0.3.x 繞過**，但靠的是模仿 App／瀏覽器的 TLS 指紋，屬於貓抓老鼠。

### 2.2 目前已知問題（2026-10-01）

- **#444（open，2026-09-28，Windows 11 + Python 3.12）**：登入與 MFA 成功、也拿到 token，但所有資料 API 都回 403。原因是登入用 curl_cffi，資料呼叫卻用一般 `requests`，被 Cloudflare 拒絕。作者回覆「looks environment-specific — are you on a VPN, datacenter or corporate network?」，不打算改預設，因為 curl_cffi 不支援 `files=` 上傳。
  - 回報者的繞法：`verify_login=False`，再把 `api.client._api_session` 換成 `curl_cffi.requests.Session(impersonate="chrome")`。
  - 本專案 probe 的 `--cffi-api` 參數就是這個繞法（預設關閉）。使用者在公司網路測試時特別可能遇到。
- **429（登入被限流）**：
  - #337（2026-03）、#350（2026-04）都有回報；#350 最後發現是裝到很舊的 `curl_cffi`。
  - Garmin 論壇 thread 435087（約 2026-05）：「The block appears to be at the account level, not IP」，且「My account works fine on the web and mobile app」，有人 72 小時以上仍被擋。
  - 是「每個帳號」還是「每個指紋」被限流：#444 的回報者認為是指紋；兩邊說法不同，**未驗證**。
  - 0.3.17 的 PR #449 已把錯誤訊息改成不再一律說是 IP 問題。

### 2.3 支援的端點（讀 0.3.17 wheel 原始碼確認，`garminconnect/__init__.py`）

| 用途 | 方法 | 備註 |
|---|---|---|
| 活動列表 | `get_activities(start=0, limit=20, activitytype=None, activitysubtype=None)` | `limit` 上限 `MAX_ACTIVITY_LIMIT = 1000`；另有 `get_activities_by_date(...)` |
| 原始 FIT | `download_activity(id, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)` | 文件字串：「For "Original" will return the zip file content, up to user to extract it.」→ 要自己解 ZIP 拿 .fit |
| 其他格式 | `ActivityDownloadFormat.TCX / GPX / KML / CSV` | |
| 建立課表 | `upload_workout(workout_json)` → POST `workout-service/workout` | 也有 typed 版 `upload_running_workout(RunningWorkout)`（需 pydantic） |
| 更新課表 | `update_workout(workout_id, json)`（PUT，整份取代，id 不變，行事曆排程保持有效） | 對應 COROS 的「改了就換掉」 |
| 刪除課表 | `delete_workout(workout_id)` | |
| 排上行事曆 | `schedule_workout(workout_id, "YYYY-MM-DD")` → POST `.../schedule/{id}` body `{"date": ...}` | |
| 取消排程 | `unschedule_workout(scheduled_workout_id)` | 參數是「排程 id」，不是 workout id。回傳欄位名稱**未驗證**，probe 會把整份回應存下來 |
| 讀行事曆 | `get_scheduled_workouts(year, month)`（month 1–12，函式內部會減 1） | 回 `calendarItems`，`itemType == "workout"` |
| 直接推到裝置 | `push_workout_to_device(workout_id, device_id=None)` | 走 device message；不排日期 |
| 下載課表 FIT | `download_workout(workout_id)` | 可拿到 Garmin 自己編好的 workout FIT |
| HRV | `get_hrv_data(date)`、`get_hrv_data_range(start, end)` | |
| 睡眠 | `get_sleep_data(date)` | |
| Training Status | `get_training_status(date)` | |
| Training Readiness | `get_training_readiness(date)` | |
| 每日摘要 | `get_user_summary(date)` / `get_stats(date)` | |
| Token | 登入後 `api.client.dumps()` 回 JSON 字串（`di_token`、`di_refresh_token`、`di_client_id`）；`login(tokenstore=<JSON 字串>)` 可直接載入 | 傳**路徑**才會寫 `garmin_tokens.json`（明文，0600）；傳 JSON 字串不寫檔 |

### 2.4 Garmin 對自動化存取的條款

- **一般使用者的 Garmin Connect Terms of Use 全文，2026-10-01 無法取得**：
  - `garmin.com/en-US/legal/terms-of-use/` 是 JS 渲染，抓下來的 HTML 沒有條文。
  - 我試過的幾個 Connect 專屬 URL 都回 404。
  - web.archive.org 這次的工具連不上。
  - 所以**我無法引用使用者條款原文**。「使用者條款禁止自動化存取」這個說法常見於二手文章，本文件標為 **未驗證**。
- 可以引用的是 §1.3 的 Developer Program Agreement：
  - §5.2.j 禁止 robot／spider／scrape。
  - §5.2.h 禁止繞過「mechanisms used to restrict or control the Garmin Connect services」。
  - 這些條文約束的是 API licensee，但也清楚表示 Garmin 對「繞過控制機制、自動抓取」的立場。`garminconnect` 輪換 TLS 指紋來通過 Cloudflare，本質就是在繞過控制機制。
- 套件作者自己的定位（README）：「This library enables developers to programmatically access Garmin Connect data」；沒有任何 Garmin 授權。

### 2.5 對使用者自己帳號的風險

- **目前看到的實際後果**（2026-10-01）是**登入被 429 限流**，論壇回報可達 48～72 小時以上。網頁與 App 登入不受影響。
- **封號案例**：本次沒找到可查證的 Garmin 因使用 `garminconnect` 而停權帳號的案例。這不代表不會發生，**未驗證**。
- 降低風險的做法（probe 都照做）：
  - 只登入一次，之後重用 token：token 有 refresh，不要每次都用帳密登入。
  - 失敗不要重試迴圈。
  - 讀取量小：預設 5 筆活動、只下載 1 個 FIT。
  - 寫入只建一個明確命名的測試課表，測完就刪。
- **帳號一年沒用**：先用瀏覽器登入一次 connect.garmin.com，把可能出現的新版條款同意、隱私設定、MFA 設定等中間畫面處理掉。HTML 表單型的登入策略遇到這類中間頁很可能失敗；這點是推論，**未驗證**。

---

## 3. 課表格式：Garmin workout JSON 與對應

### 3.1 Garmin workout JSON（依 `garminconnect/workout.py` 0.3.17 的常數與模型；常數註明「from /workout-service/workout/types」）

```json
{
  "workoutName": "TRC 閾值 3×10 分 10/1",
  "description": "…",
  "sportType": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
  "estimatedDurationInSecs": 3240,
  "author": {},
  "workoutSegments": [{
    "segmentOrder": 1,
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
    "workoutSteps": [
      {"type": "ExecutableStepDTO", "stepOrder": 1,
       "stepType": {"stepTypeId": 1, "stepTypeKey": "warmup", "displayOrder": 1},
       "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": true},
       "endConditionValue": 900,
       "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 1},
       "targetValueOne": 128, "targetValueTwo": 150},
      {"type": "RepeatGroupDTO", "stepOrder": 2,
       "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
       "numberOfIterations": 3, "smartRepeat": false,
       "endCondition": {"conditionTypeId": 7, "conditionTypeKey": "iterations", "displayOrder": 7, "displayable": false},
       "endConditionValue": 3,
       "workoutSteps": [ {"type": "ExecutableStepDTO", "stepOrder": 3, "...": "..."},
                         {"type": "ExecutableStepDTO", "stepOrder": 4, "...": "..."} ]}
    ]
  }]
}
```

- **Sport type**：
  - running 1、cycling 2、other 3、swimming 4、strength_training 5、cardio_training 6、yoga 7、pilates 8、hiit 9、multi_sport 10、mobility 11。
  - walking 17、hiking 18。
  - **沒有 trail_running 的 workout sport type**（模型裡沒有）。手錶上「越野跑」是活動 profile，跑 running 課表時可以在越野跑 profile 執行；這是一般經驗，**未驗證**。
- **Step type**：warmup 1、cooldown 2、interval 3、recovery 4、rest 5、repeat 6、other 7、main 8。
- **End condition**：
  - lap.button 1、time 2（秒）、distance 3（公尺）、calories 4、power 5、heart_rate 6。
  - iterations 7、fixed_rest 8、fixed_repetition 9、reps 10。
- **Target type**：
  - no.target 1、power.zone 2、cadence 3、heart.rate.zone 4、speed.zone 5、pace.zone 6、grade 7、heart.rate.lap 8、power.lap 9、resistance 15。
  - 自訂範圍：`targetValueOne`／`targetValueTwo` 放下限／上限，`zoneNumber` 留空。
    - 心率：bpm。
    - 功率：watts（模型註解「upper and lower limits in Watts」）。
    - 配速：m/s，「the slower pace is the lower limit」。
    - 步頻：spm。
  - 用使用者自己在 Garmin 設的區間：只填 `zoneNumber`。
  - 可以再加第二目標：`secondaryTargetType` 加 `secondaryTargetValueOne/Two`，0.3.17 才支援（PR #440、#446）。
- **Repeat**：`RepeatGroupDTO` 放 `numberOfIterations` 和子步驟，可以巢狀。
- `stepOrder` 在**整個 segment 內唯一**，包含子步驟（`create_strength_set` 的說明：「Advance the caller's running order counter … so every stepOrder is unique」）。
- `childStepId`：Garmin 網頁版匯出的 JSON 常見這個欄位，但 `garminconnect` 的 helper 不設定它。不設定能不能用，**未驗證**，probe 實測會知道。
- 每步的 `description`（步驟備註）：模型允許額外欄位（`extra="allow"`）。Garmin 會不會在手錶上顯示，**未驗證**。

### 3.2 從 app 的課表對應過去

來源是 `coros_workouts.session_steps(session, Thresholds)`，回傳 `Step` / `Repeat` 列表。COROS 和 Garmin 共用這一層，只換最後的 payload builder。實作在 `backend/scripts/garmin_probe.py` 的 `garmin_workout()` / `garmin_steps()`，有離線測試 `backend/tests/test_garmin_probe.py`。

| app（`coros_workouts`） | Garmin |
|---|---|
| `Step(EX_WARMUP)` | stepType warmup 1 |
| `Step(EX_TRAIN)` | stepType interval 3（`garminconnect` 的 helper 也用 interval；另有 main 8，未採用） |
| `Step(EX_COOLDOWN)` | stepType cooldown 2 |
| `Step(EX_REST)`（間歇之間） | stepType recovery 4（rest 5 在 Garmin 是「站著休息」，用在肌力組間） |
| `seconds > 0` | endCondition time 2，`endConditionValue` = 秒 |
| `seconds == 0`（全力測試、開放步驟） | endCondition lap.button 1 |
| `intensity = ("hr", lo, hi)` | targetType heart.rate.zone 4，`targetValueOne=lo`、`targetValueTwo=hi`（bpm） |
| `intensity = ("power", lo, hi)` | **預設不送功率目標**：改成 no.target，把「目標功率 lo–hi W」寫進步驟 description；`power_targets=True` 才送 power.zone 2，單位 watts。原因見 §3.3 |
| `intensity = None` | no.target 1 |
| `Repeat(sets, steps)` | RepeatGroupDTO，`numberOfIterations=sets`，endCondition iterations 7 |
| `Step.name` | 步驟 `description` |
| session `kind` long／mountain／hike／easy／quality／test／notice | sport running 1。hike 雖然有 hiking 18，但手錶端沒測過，先用 running |
| strength／race／rest／heat_passive | `session_steps` 已經 raise `Unsupported`，直接沿用，不推 |
| 課表名稱 | 沿用 `coros_workouts.workout_name()`：「TRC 標題 月/日」。COROS 限 30 字；Garmin 上限**未驗證**，先沿用 30 字 |

日後正式接入時（不在本次範圍）：

- 照 COROS 的做法加一張 `garmin_plan_push` 表：session key → workoutId、scheduled id、fingerprint。
- 沒變的不動，變了的用 `update_workout`；只刪表裡記錄的課表。

### 3.3 跑步功率在 Garmin 上

- **原生腕式跑步功率**：FR255／265／955／965 等較新機種才有，FR245 Music 等舊機種沒有。這些機種可以在 Garmin Connect 建立 power zone／power range 的跑步課表。來源是 Garmin 論壇 372326 與 FR965 手冊，機種清單以 Garmin 官方為準，**沒有逐一驗證**。
- **Stryd**：Stryd Zones 這個 Connect IQ data field（2023-11 更新）可以執行功率課表，顯示上下限並提醒，但「Stryd does not show power on the native Garmin Workout screen, only on the installed data field」。課表要從 Stryd 帳號同步到 Garmin 行事曆。
- **校正問題（重要）**：
  - app 的 CP 來自 COROS 或 WKO5 的功率資料。Garmin 腕式功率、Stryd、COROS 是三種不同的估算方式，數值不能互換。
  - 把 COROS 的 CP 換算成瓦數推到 Garmin 手錶，目標可能整個偏高或偏低。
  - 所以 Garmin 推送**預設只送心率目標**。功率只寫在備註，等使用者確認同一個功率來源（例如兩邊都用 Stryd）再打開。

---

## 4. 完全不用 API 的備案

### 4.1 FIT workout 檔經 USB 放進手錶

- 做法：手錶接 USB → 打開 `Garmin/NewFiles` → 複製 `.fit` workout → 退出 → 手錶的「訓練 → 課表」就會出現（8020endurance 的教學）。有些機種要放在 `Garmin/Workouts`。
- 限制：
  - **有音樂功能的新款手錶是 MTP 裝置**，macOS 預設不支援，Windows 可以。
  - 每支手錶能存的課表數有上限（例：FR920XT 只能存 25 個）。
  - **只進手錶、不進 Garmin Connect 行事曆**，沒有日期；更新要手動刪掉舊的。
  - 本 repo 目前只有 FIT **解碼器**（fitparse、fitdecode，以及測試用的 `backend/tests/fit_builder.py`），**沒有 workout FIT 編碼器**。要做就得自己寫 `file_id(type=workout)` + `workout` + `workout_step`，或引入第三方編碼套件，候選套件沒評估，**未驗證**。
  - 自訂心率／功率在 FIT 檔裡的編碼方式（offset）這次沒抓到 FIT SDK 原文，實作前要對照 FIT SDK 的 Profile，**未驗證**。
- 實用性：**中**。適合「本週 3～5 堂課一次放進去」的免費離線版，搭配一鍵匯出 .fit；不適合每天自動重排。

### 4.2 Garmin「Export Your Data」補歷史

- 路徑：garmin.com 帳號 → Data Management → Export Your Data → Request Data Export。
- 通常 24～48 小時寄出下載連結，最長可到 30 天；連結**3 天後失效**；準備中的匯出不能再申請第二次（takeoutday.org、fit-pa 等 2026 年的整理文章，非 Garmin 官方頁面）。
- 內容是巢狀 ZIP。活動檔在 `DI_CONNECT/DI-Connect-Uploaded-Files/` 或 `DI-Connect-Fitness-Uploaded-Files/`，兩種資料夾名稱都有人回報，版本差異**未驗證**。裡面混著 monitoring／sleep 等 FIT，要用 `file_id.type == activity` 篩選。
- 實用性：**高**，一次性補齊歷史、零條款風險；已經規劃在 plan §9.3 的 ZIP 匯入。每日 HRV／睡眠等在 ZIP 裡的格式沒看過，**未驗證**。

---

## 5. 建議

### 5.1 個人自用（現在）

- **(a) 活動與 FIT**：
  - 歷史：先申請 Export Your Data，用 §9.3 的 ZIP 匯入補齊。
  - 增量：probe 實測通過後，可以做 `garmin_client.py`（設計見 plan §9.2 的表），用 `garminconnect` 拉最近的活動與 ORIGINAL FIT，放 `~/.wko5coach/fit/garmin/`。
  - 預設關閉，標「非官方、實驗」，跟 COROS 非官方 API 同等級。
- **(b) 推課表**：
  - 實測 `--push-test` 成功（手錶真的收到、心率目標正確）後，再做 `garmin_workouts.py`：沿用 `session_steps` 加 §3.2 的對應，以及 `garmin_plan_push` 去重表。
  - 預設只送心率目標。
  - 不通的話，退回 §4.1 匯出 .fit 手動放進手錶。
- **(c) 每日指標**：選配。先用 `--daily` 看回傳內容，有需要再接。這部分最容易因 Garmin 改版而壞，優先度最低。
- **前提**：接受非官方路線隨時可能失效（2026-03 就全面壞過，garth 因此停止維護），而且 Windows 公司網路可能遇到 #444。

### 5.2 付費版（之後）

- **不能用非官方路線**：
  - 從自架 server 用使用者的帳密或 token 代為登入，正是 Developer Agreement §5.2.h／§5.2.j 禁止的行為。
  - 很多使用者共用同一個 server IP 和 TLS 指紋，會更快觸發 429。
  - 還要保管使用者的 Garmin 帳密或 refresh token，責任太大。
- **官方路線的條件**：
  1. Garmin 重新開放申請（2026-10 仍暫停）；
  2. 以公司或法人申請；
  3. 收費要依 §5.2.e 取得 Garmin **書面許可**；
  4. 若用到 Health API 的 Beat-To-Beat，商業使用要付授權費。
- **建議**：
  - 付費版先只支援 COROS（plan §10.3 的 COROS 商業條款仍待確認）。
  - Garmin 使用者在付費版也走「匯出 ZIP／拖放 FIT」加「匯出 .fit 課表」。
  - 等 Garmin 重開申請、而且有公司以後，再評估 Activity + Training API。
  - 取得資格後的工作量沿用 plan §9.2 的估計（5–8 天）。

---

## 6. 使用者測試步驟

probe 腳本：`backend/scripts/garmin_probe.py`。它是獨立腳本，沒有接進 app；不碰 `~/.wko5coach/wko5coach.db`，不碰 COROS。

### 6.1 事前準備

1. 用瀏覽器登入一次 https://connect.garmin.com ，確認帳號還能用，把新條款、MFA 等畫面處理完。一年沒登入的帳號特別需要這一步。
2. 安裝套件，只裝進本專案的 venv，**不要**加進 `requirements.txt`：
   ```powershell
   .venv\Scripts\python.exe -m pip install "garminconnect==0.3.17"
   ```
   - 需要 Python ≥ 3.12；目前 venv 是 3.12.10。
   - 會一併裝 `curl_cffi>=0.15.0`、`requests>=2.33.0`、`ua-generator>=1.0`。不需要 `[workout]` extra（probe 直接組 JSON，不用 pydantic 模型）。
   - 確認 `curl_cffi` 版本 ≥ 0.15.0：`.venv\Scripts\python.exe -m pip show curl_cffi`。#350 的 429 就是裝到舊版造成的。
3. 確認 `~/.wko5coach/secret.key` 存在（`chezmoi apply` 部署的那把）。probe 用它加密 token 快取。

### 6.2 指令（都在 repo 根目錄執行）

```powershell
# 0) 離線預覽測試課表 JSON（不連網、不登入）
.venv\Scripts\python.exe -m backend.scripts.garmin_probe --preview

# 1) 唯讀：登入（會問 email、密碼、必要時 MFA）、列最近 5 筆活動、下載第 1 筆的原始 FIT、列本月與下月的排程課表
.venv\Scripts\python.exe -m backend.scripts.garmin_probe

#    選項：--limit 10（列 10 筆）、--activity-id 123456789（指定要下載哪一筆）、--no-download（不下載）
#    選項：--daily 2026-09-30（另外讀這天的 HRV / 睡眠 / Training Status / Training Readiness，存成 JSON）
#    選項：--no-cache（token 只放記憶體，不寫加密快取）
#    選項：--cffi-api（遇到「登入成功但每個 API 都 403」時用，見 §2.2 #444）

# 2) 寫入測試：建立一個「TrailRunCoach 測試課表（可刪除）」並排在指定日期（會先問 y/N）
.venv\Scripts\python.exe -m backend.scripts.garmin_probe --push-test --date 2026-10-03

#    手錶同步後，到「訓練 → 課表」或當天的行事曆確認：
#    - 有 4 段：暖身 5 分（心率 110–140）→ 重複 2 次（1 分 + 1 分恢復）→ 緩和 5 分
#    - 心率範圍顯示正確
#    - 步驟備註有沒有出現
#    加 --with-power 會多一段 3 分鐘功率 200–230 W 的步驟（只在有跑步功率的錶上測）

# 3) 清除測試課表：只刪 probe 自己建的那一個（會先問 y/N）
.venv\Scripts\python.exe -m backend.scripts.garmin_probe --cleanup

# 4) 刪除加密的 token 快取
.venv\Scripts\python.exe -m backend.scripts.garmin_probe --forget
```

### 6.3 檔案放哪裡

- `~/.wko5coach/garmin/session.enc`：Fernet 加密的 token（`enc:v1:` 格式，用 `backend/settings/secrets.py`）。**密碼從不寫入任何地方**；登入成功後套件自己也會把記憶體裡的密碼清掉（`self.password = None`）。
- `~/.wko5coach/garmin/probe/`：下載的 FIT、`--daily` 的 JSON。刻意不放在 `~/.wko5coach/fit/`，app 的資料集不會讀到。
- `~/.wko5coach/garmin/probe_state.json`：`--push-test` 建立的 workoutId 與排程回應，供 `--cleanup` 使用。
- 若環境變數 `GARMINTOKENS` 有設，probe 會先移除它，避免套件把明文 token 寫進那個路徑。

### 6.4 回報什麼給我

- 每一步的輸出（可以把 email、activity 名稱遮掉）。
- 失敗時的例外類別與訊息：429？403？MFA？
- 手錶上看到的課表照片或描述：步驟數、心率範圍、備註。
- `probe_state.json` 裡排程回應的欄位名稱，用來確認 `unschedule_workout` 該用哪個 id。

### 6.5 出狀況時

- **429**：停手，至少隔幾小時再試，不要連續重試。網頁與 App 登入不受影響。
- **登入成功但 API 全部 403**：加 `--cffi-api` 再跑一次。公司網路或 VPN 比較容易遇到。
- **MFA 打錯**：0.3.12 起會保留 MFA session，可以重打。

---

## 7. 重新驗證清單（動工前逐項重查）

1. Garmin Connect Developer Program 是否重新開放申請；FAQ 的資格（enterprise／business only）與審核時間有沒有變。
2. Developer Program Agreement 的版本（目前 FRM-0952 Rev. B）與 §4.1、§5.2.e、§5.2.h、§5.2.j、§10.1 是否改寫。
3. Health API 頁面的商業授權費註記；Health API 是否加入 HRV Status／Training Status。
4. 一般使用者版 Garmin Connect Terms of Use 的自動化存取條款（本次取不到全文）。
5. `garminconnect` 最新版與 Python 需求（本次 0.3.17／≥ 3.12）；登入策略鏈有沒有大改。
6. GitHub issue #444（API 403）是否修正，例如改成 opt-in 的 curl_cffi API session；有沒有新的大規模登入失效 issue。
7. `upload_workout`、`schedule_workout`、`unschedule_workout`、`download_activity(ORIGINAL)` 的簽名與回傳格式。
8. `workout.py` 的 sport／step／condition／target ID 是否變動；`childStepId` 是否必要；名稱長度上限。
9. 手錶端：心率範圍、功率（原生／Stryd）、步驟備註的顯示。這要使用者實測。
10. Export Your Data 的 ZIP 結構（資料夾名稱）與寄送時間。
11. FIT workout 編碼：FIT SDK Profile 裡自訂心率／功率的 offset。

---

## 8. 來源（2026-10-01 讀取）

- Garmin Connect Developer Program FAQ：https://developer.garmin.com/gc-developer-program/program-faq/
- Activity API：https://developer.garmin.com/gc-developer-program/activity-api/
- Training API：https://developer.garmin.com/gc-developer-program/training-api/
- Health API：https://developer.garmin.com/gc-developer-program/health-api/
- Developer Program Agreement（FRM-0952 Rev. B）：
  - https://developerportal.garmin.com/sites/default/files/Garmin%20Connect%20Developer%20Program%20Agreement.pdf
  - 中國區版：https://www8.garmin.com/en-US/GARMINCONNECTDEVELOPERPROGRAMAGREEMENT/GARMINCONNECTDEVELOPERPROGRAMAGREEMENT_EN.pdf
- 暫停受理：
  - https://the5krunner.com/2026/09/14/garmin-developer-api-access-paused/
  - https://sahha.ai/blog/garmin-developer-program-paused/
  - https://forums.garmin.com/apps-software/mobile-apps-web/f/garmin-connect-mobile-andriod/441607/garmin-connect-api-access-paused-for-months-what-are-startups-supposed-to-do
- 個人無法申請：https://ghurt.org/garmin-api-for-personal-use
- garth：
  - 停止維護 README：https://github.com/matin/garth
  - 公告：https://github.com/matin/garth/discussions/222
  - PyPI：https://pypi.org/pypi/garth/json
- python-garminconnect：
  - repo：https://github.com/cyberjunky/python-garminconnect
  - PyPI：https://pypi.org/pypi/garminconnect/json
  - 0.3.17 wheel 原始碼：`garminconnect/__init__.py`、`client.py`、`workout.py`
- issues：
  - #444：https://github.com/cyberjunky/python-garminconnect/issues/444
  - #350：https://github.com/cyberjunky/python-garminconnect/issues/350
  - #337：https://github.com/cyberjunky/python-garminconnect/issues/337
  - PR #448、#449（2026-09-29）
- 429 帳號層級的回報：https://forums.garmin.com/developer/fit-sdk/f/discussion/435087/persistent-429-on-api-login-account-blocked-for-48-hours
- 跑步功率課表：
  - https://forums.garmin.com/apps-software/mobile-apps-web/f/garmin-connect-web/372326/how-do-you-create-a-power-based-run-workout-in-garmin
  - https://www8.garmin.com/manuals/webhelp/GUID-0221611A-992D-495E-8DED-1DD448F7A066/EN-US/GUID-D74FC870-3A94-4376-81D5-C9484545EAD9.html
- Stryd：
  - https://blog.stryd.com/2023/11/14/native-power-based-workouts-on-garmin-watch/
  - https://help.stryd.com/en/articles/8594075-stryd-zones-data-fields-and-garmin-watch-setup-with-stryd-and-stryd-duo
- USB 放 FIT 課表：https://www.8020endurance.com/how-to-load-a-fit-file-on-your-garmin-device/
- Export Your Data：
  - https://takeoutday.org/guides/how-to-export-garmin-connect-data
  - https://app.fit-pa.com/articles/how-to-export-garmin-connect-data-fit-files
  - 另見 plan §9.3 的來源
