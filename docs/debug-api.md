# Debug API（給 AI agent 用）

SP-371。一組**唯讀**的 HTTPS API，讓 AI agent 不用進主機就能拿到「正式環境現在的真相」：某一筆活動、某段期間的課表、某一天的配對與完成度、當天生效的門檻、同步狀態，以及運動員設定的匯出。

- 程式：`backend/api/debug.py`（端點）、`backend/debug_auth.py`（認證、稽核、流量限制）、`backend/engine/debug_view.py`（組資料、去掉秘密和 GPS）
- 設定頁：設定 › 進階設定 › **Debug API**（`backend/static/debug_api.js`）
- 測試：`backend/tests/test_debug_auth.py`、`test_debug_api.py`、`test_debug_integration.py`

---

## 一分鐘上手

```bash
BASE=https://<正式網址>          # 經 Cloudflare Tunnel 的 HTTPS 位址
TOKEN=trcd_xxxxxxxx...           # 使用者在設定頁產生、交給你的 token

# 某一天：課表、活動、配對、完成度和原因（查「這天為什麼被標成 X」只要這一支）
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/debug/day?date=2026-10-05"

# 某一天的活動（完整數字、門檻來源、判讀、配對結果）
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/debug/activity?date=2026-10-05"

# 一段期間的課表（預設本週）
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/debug/plan?from=2026-09-29&to=2026-10-12"

# 當天生效的門檻和每一筆的來源
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/debug/thresholds?date=2026-10-05"

# 同步結果、失敗清單、最近的 log
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/debug/sync?lines=200"

# 匯出運動員設定（token 要有 export:config）
curl -s -H "Authorization: Bearer $TOKEN" "$BASE/api/v1/debug/export/config"
```

回應都是 JSON（UTF-8，`Cache-Control: no-store`）。

不想自己組 HTTP 的話，用 CLI（`backend/scripts/debug_fetch.py`，只送 GET）：

```bash
export TRC_DEBUG_URL=https://<正式網址> TRC_DEBUG_TOKEN=trcd_...
python -m backend.scripts.debug_fetch day 2026-10-05
python -m backend.scripts.debug_fetch activity --date 2026-10-05 --streams hr,speed --every 30
python -m backend.scripts.debug_fetch plan --from 2026-09-29 --to 2026-10-12
python -m backend.scripts.debug_fetch export -o config.json
```

---

## 認證模型

| 項目 | 規則 |
|---|---|
| 預設 | **關閉**。沒在設定 › 進階打開時，`/api/v1/debug/*` 一律回 `404 {"detail": "Not Found"}`（跟不存在的網址一樣，不洩漏端點存在） |
| PIN | 伺服器環境變數 `TRC_DEBUG_PIN`（至少 6 碼）。**沒設 PIN 就不能打開**，打開了也一律 404。PIN 只在伺服器的 `.env`，不在 repo、不在資料庫 |
| 怎麼拿 token | 使用者在設定頁按「產生 token」→ 輸入 PIN → 選名稱、範圍、有效期 → token **只顯示這一次** |
| PIN 錯誤 | 固定時間比較（兩邊先 SHA-256 再 `hmac.compare_digest`）；**連續錯 5 次鎖 15 分鐘**（整台伺服器，鎖住時正確的 PIN 也拒絕） |
| 怎麼帶 | 只認 `Authorization: Bearer trcd_…`。**網頁登入的 cookie 一律不讀**：被 XSS／CSRF 帶走的 session 叫不動這些端點，token 外流也碰不到一般功能 |
| 儲存 | 資料庫（`debug_tokens`）只存 token 的 SHA-256 和前 4 碼（`trcd_AbCd…`，用來分辨）。token 是 256 位元亂數，資料庫外流也還原不出來 |
| 範圍（scope） | `read:activity`、`read:plan`、`read:sync`、`export:config`。預設勾前三個，`export:config` 要自己勾 |
| 有效期 | 1／7／30／90 天，預設 30；到期自動失效 |
| 撤銷 | 設定頁可以撤銷單一 token 或「全部撤銷」；撤銷後立刻 401 |
| 綁定使用者 | token 綁定產生它的 tenant（`tenancy.current().id`），只看得到自己的資料；別的 tenant 的 token 在這裡是「不認識的 token」 |
| 流量限制 | 每個 token 每分鐘 60 次，超過回 `429`（`Retry-After: 60`）。同一個來源 IP 10 分鐘內認證失敗超過 10 次 → 封鎖 10 分鐘（`429 BLOCKED`，連正確的 token 也擋）。沿用 `backend/security/ratelimit.py` 的 token bucket（同 `tenancy_mw`） |
| 稽核 | 每次呼叫（通過 404 那一關之後，含 401／403／429）記一筆：時間、token 名稱、端點、參數、來源 IP、狀態、回應大小、耗時；保留最新 1000 筆，設定頁看最近 100 筆。token **第一次從某個 IP 使用**時，那筆和 token 列會標「⚠ 新 IP」 |
| demo 模式 | 一律關閉：demo instance 根本不掛這些路由 |
| HTTPS | 正式網址經 Cloudflare Tunnel，本來就是 HTTPS |

### 錯誤碼

| 狀態 | `detail` | 意思 |
|---|---|---|
| 404 | `"Not Found"` | 沒打開、沒設 PIN、demo 模式，或網址不存在 |
| 401 | `{"code": "TOKEN_MISSING"}` | 沒帶 `Authorization: Bearer trcd_…`（cookie 不算） |
| 401 | `{"code": "TOKEN_INVALID"}` | 不認識的 token（打錯、別的 tenant 的） |
| 401 | `{"code": "TOKEN_REVOKED"}` / `{"code": "TOKEN_EXPIRED"}` | 已撤銷／已過期 |
| 403 | `{"code": "SCOPE"}` | token 沒有這個端點需要的範圍 |
| 429 | `{"code": "RATE_LIMITED"}` / `{"code": "BLOCKED"}` | 每分鐘上限／來源 IP 被暫時封鎖 |
| 400 | 文字訊息 | 參數不對（例如日期格式） |

---

## 共同規則

- **全部唯讀**。課表相關的端點**不呼叫** `plan_sessions.sessions()`（那支會拿寫入鎖、reconcile、記週存檔），而是在記憶體裡算一樣的東西：配對用 `plan_store.match_only`，「下次開頁會改什麼」用 `plan_store.reconcile_with_adapt`，都不寫回。只會寫 `debug_tokens`（最後使用時間／IP）和 `debug_audit`。**不會呼叫 COROS／TP**。
- **不回傳秘密**：密碼、COROS／TP token、記住的帳密（Fernet 加密的密碼）、debug token、備份資料夾、課表訂閱網址一律不出現。最後一道防線 `debug_view.scrub` 會再把名稱像憑證的 key（password、cookie、sealed、access_token…）整個拿掉，把看起來像 token 的值換成 `[redacted]`。
- **GPS 預設不給**：沒帶 `?gps=1` 時，lat／lon／latitude／longitude 之類的 key 全部拿掉。
- 每個區塊盡量附 `source`（資料從哪來）、`as_of`（哪一天的值）；重要的判斷附一行 `why`（用了哪條規則、哪個門檻）。
- 文字跟著請求的語言（`Accept-Language`／`?lang=`），預設繁體中文。

### `meta`（每個回應都有）

| 欄位 | 意思 |
|---|---|
| `app_version` | 伺服器程式的 git commit（`TRC_GIT_SHA`，建 image 時 `--build-arg GIT_SHA=$(git rev-parse --short HEAD)`；沒有就讀 checkout；都沒有是 `unknown`） |
| `schema_version` | 這份 API 的格式版本（目前 1） |
| `endpoint` / `query` | 這次呼叫的路徑和參數 |
| `token_name` | 用的是哪個 token（名稱，不是 token 本身） |
| `generated_at` | 產生時間（UTC） |
| `data_generation` | 讀的是哪一版資料：`dataset`（這個 process 裡資料集的世代號，同步後重建就會變）、`source`（coros／trainingpeaks／wko5）、`activities`（活動數）、`today` |
| `cache_versions` | 用到的快取 key 版本：`workout_review`（判讀快取）、`fitcache`（FIT 解析各欄位的版本） |
| `elapsed_ms` | 計算耗時 |

---

## 端點

### `GET /api/v1/debug/activity` — 範圍 `read:activity`

指定方式**擇一**：`?date=YYYY-MM-DD`（列出當天全部）、`?id=<workout_files.id>`（DB id）、`?label=<文字>`（標籤／標題／檔名包含這段文字，最多 20 筆）。
選用：`&streams=hr,power,speed,elev,cadence,dist&every=10`（每 10 秒取一點的降採樣資料），`&gps=1`（加上 `latitude`／`longitude`）。

回應：`{"activities": [...], "count", "lookup"}`，每一筆：

| 區塊 | 內容 |
|---|---|
| `index` | 資料集裡的活動編號（課表的 `done_by.index`、`/debug/day` 的 `activity.index` 都是它） |
| `basic` | `source`（coros／tp／wko5）、`file`（檔名）、`start`、`date`、`sport`、`sport_type`、`category`（road／trail／hike／bike…）、`label`、`terrain`（地形：`value`、能不能改、是不是使用者覆寫） |
| `tags` | 活動類型／努力程度／標籤／疼痛／壞檔排除：`*_auto`（自動值和 `*_reason`）、`*_overridden`（使用者有沒有改）、目前生效值 |
| `numbers` | `metrics`：資料集算的全部數字（時間、距離、爬升、`tss`、`hrtss`、`rtss`…）；`row`：課表頁用的那一列（`moving_s`、`tss`、`hard_s` 閾值以上秒數…） |
| `thresholds_used` | 算這些數字用的門檻：`lthr`（hrTSS 用的 LTHR、`source` 例如「手動輸入 2026-09-15」「推估（最大心率的 90 %）」「來自手錶…」、`origin` plan／dataset）、`cp`（CP 和來源）、`tss_ftp`（功率 TSS 用的 FTP）、`power_source`、`ignored`（被忽略的值，例如 COROS 登入時寫進來的 LTHR，附 `why`） |
| `review` | `classification`：課表類型（`type`／`type_label`）、刺激分級（`stimulus`、`stim` 含 Zone 3 秒數、等效 T@VO2max 和 `why`）；`measure`：`drift`（飄移，含心率品質是否只當參考）、`intervals`／`efforts`（間歇判讀）、`climbs`（爬坡段）、`zones`、`hard_s`… |
| `plan` | 配對：`matched`、`session`（配到的那一堂，格式同 `/debug/day` 的一列）、`why`（為什麼這樣配／為什麼沒配到） |
| `streams` | 只在要求時：`every_s`、`t`、各通道陣列；`gps=1` 才有 `latitude`／`longitude` |

### `GET /api/v1/debug/day?date=YYYY-MM-DD` — 範圍 `read:plan` + `read:activity`

把當天的課表和活動擺在一起，跟課表頁（`plan_sessions.calendar` → `_decorate`）**同一套計算**：

| 欄位 | 意思 |
|---|---|
| `sessions[]` | 當天每一堂課：`uid`、`kind`（easy／long／quality／test／hike／strength…）、`title`、`minutes`、`tss`／`tss_est`、`gen_key`、`origin`（auto／custom）、`edited`、`state`（active／done／missed）、`activity`（配到的活動：`index`、`moving_s`、`tss`、`hard_s`、`session` 分類、`match`＝day 同一天／plan 同一週課表自動對到／manual 手動） |
| `sessions[].vs` | `plan_match.compare`：`planned`／`actual`（hard／easy）、`grade`、`intensity_pct`、`off_plan`（沒照課表）、`short`（強度不足／偏強）、`text`／`short_text`（原因）、`wrong_sport`、`terrain_off`、`match_label` |
| `sessions[].compliance` | `compliance.with_plan_check(session_compliance(...))`：`level`（green／yellow／red／missed）、`label`（符合計畫／有點偏離／偏離計畫／沒照課表／強度不足／未完成）、`pct`、`duration_pct`、`tss_pct`、`intensity_pct`、`off_text` |
| `sessions[].status` | 達成率頁的分類：done／partial／off_plan／missed／open |
| `sessions[].why` | 一行說明：配對方式、完成度、時間 %、TSS %、原因、等級的規則 |
| `unmatched_activities[]` | 沒配到任何一堂課的活動和 `why`（那天沒排課／那堂已配給別的活動／類型不符／使用者解除過配對） |
| `would_change` | 下一次開課表頁或同步時 reconcile／自動調整會改什麼（只算不寫）：`changes`、`adapt` |

### `GET /api/v1/debug/plan?from=&to=` — 範圍 `read:plan`

預設本週（週一到週日），最多 120 天。

| 區塊 | 內容 |
|---|---|
| `range` | `from`、`to`、`today` |
| `stored.sessions[]` | 已存的課表（含刪除／取代的標記 `deleted`、`stored_state`）：kind、gen_key、variant_key／rung_key、edited、origin、配到的活動（`done_by`）、TSS、目標、`coros`（推送狀態：推到 COROS 的 key、時間、錯誤） |
| `stored.push` | `provider`、`rows_in_range`（`coros_plan_push` 的原始紀錄） |
| `generator.weeks` | 產生器這次會怎麼排（`plan_store.gen_weeks`） |
| `would_change` | 跟已存課表的差異（reconcile 會改什麼）和自動調整（`adapt`）的調整與原因，只算不寫 |
| `auto` | `settings`（`plan.auto.*`，含 `plan.auto.state`：stamp、階段、CP）、`pending`（待確認的提議）、`log`（區間內的 `plan_change_log`） |
| `gates` | 本週的 `quality_gate`（Zone 3／Zone 5 狀態、`guard` 的阻擋原因）、`why`、`notes`（週備註）、後面幾週的 `weeks[]`、`plan_notes`、`zone`（重測建議）、`tests` |
| `thresholds` | 產生器這週用的門檻（目標心率／功率都從這裡來） |

### `GET /api/v1/debug/thresholds?date=YYYY-MM-DD` — 範圍 `read:plan` 或 `read:activity`

當天生效的 CP、LTHR、AeT、最大心率、安靜心率，每一個都附完整歷史和來源。

| 區塊 | 內容 |
|---|---|
| `in_effect.<cp\|lthr\|aethr\|mhr\|rhr>` | `value`、`as_of`（那筆的日期）、`source`（例如「手動輸入 2026-09-15」「自動估算（已套用 2026-08-01）」）、`origin`（plan＝課表門檻列／dataset＝資料集的設定或估算／coros＝COROS 帳號）、`why` |
| `history.plan.<name>[]` | 課表門檻列（設定 › 門檻，plan.json）每一筆：`date`、`value`、`method`（estimate／friel30／test／race／lab／manual）、`source`（估算／測試／比賽／手動）、`note`；CP 另有 `cp_method`、`wprime`、`cp_manual` |
| `history.coros_account` | COROS 帳號的心率設定（目前值和每次變動），附「跑步課表不一定採用」 |
| `dataset` | 資料集那邊：`lthr_info`（`quality_gate.lthr_info`）、`settings`（各設定的日期歷史和 `source`，例如「推估（最大心率的 90 %）」）、`ignored`（被忽略的值和原因）、`as_of_estimate`（`racepower.athlete.thresholds_as_of`：當天以前的資料算出的 LTHR／AeT／CP 和來源） |
| `used_by.plan_generator` | 課表產生器這週實際採用的值和來源（`cp_source`、`lthr_source`、`aet_source`、`easy_cap_label`、`e_pace`、`hr_model`） |
| `e_pace_source` | E 配速的來源（比賽成績清單） |

### `GET /api/v1/debug/sync?lines=200` — 範圍 `read:sync`

| 區塊 | 內容 |
|---|---|
| `settings` | `sync.*`：資料來源、各來源開關、`last_result`／`last_ok`（每次同步的結果：時間、觸發方式、下載數、錯誤）、排程、開站自動同步 |
| `state` | `sync_state` 的時間和 cursor：`coros_last_sync_at`、`tp_last_sync_at`、`tp_cursor`、`*_login_expires`（**不含任何 token**） |
| `failures` | 最後一次同步有錯的來源和錯誤清單 |
| `log` | app log（`applog`，`~/.wko5coach/logs/app.log` 和上一個輪替檔）的最後 N 行（最多 2000），每一行再過一次 `applog.redact`（去掉 token、e-mail、網址參數、家目錄）；`warnings_errors` 是其中 WARNING／ERROR 的行（慢請求、5xx、例外） |

### `GET /api/v1/debug/export/config` — 範圍 `export:config`

運動員設定匯出成一份 JSON：`schema_version`、`exported_at`、`excluded`（沒匯出的東西）、`blocks`。`keys` 底下的 key 名稱跟 `user_settings` 的 key 一致，可以直接在本機重現。

| block | 內容 |
|---|---|
| `profile` | 基本資料問卷（`athlete.experience`、`athlete.setup.*`）、主要訓練項目、時區、地區、COROS 帳號心率；`plan_profile`（性別、身高、功率計）、`weights`（體重紀錄） |
| `data_source` | `sync.*`、`charts.*`、`power.*`、`activities.*`（不含同步結果） |
| `races` | 比賽成績清單（`athlete.race_results`） |
| `calendar` | 不排課日期和休息日（`plan.blackouts`）、高海拔過夜（`altitude.nights`） |
| `plan_prefs` | 課表偏好（`plan.prefs.*`：可練日、強度課次數、轉換期週數、測試方式…）、心率區間模型、B2B、建議 |
| `auto` | 自動調整設定（`plan.auto.*`） |
| `advanced` | 進階設定與每人校正值（`athlete.calib.*`，含 Zone 3 解鎖數字）、COROS TL／RPE 模型、傷病設定 |
| `thresholds` | 門檻紀錄（plan.json 每一列：日期、各門檻、方法、備註） |
| `events` | 賽事清單（A／B／C、日期、距離、爬升、`gpx_uploaded`），以及手動週期 |
| `injuries` | 傷病紀錄（含生病） |
| `activity_overrides` | 活動的手動覆寫：`tags`（類型、努力程度、標籤、疼痛、壞檔排除）、`terrain`（地形覆寫：檔名、日期） |

**不含**：密碼、COROS／TP token、記住的帳密、debug token、備份資料夾、課表訂閱網址、分享連結、同步結果、活動檔本身（要活動資料用 `/debug/activity`）。正式環境不提供匯入。

---

## 範例：「這一天為什麼被標成沒照課表？」

1. `GET /api/v1/debug/day?date=2026-10-05`
2. 找 `sessions[]` 裡那一堂：
   - `activity` 是 `null` → 沒配到活動。看 `state`：`missed` 是過了那天、資料也涵蓋那天卻沒有活動；再看 `unmatched_activities[]` 的 `why`（類型不符？那堂已配給別的活動？使用者解除過配對？）。
   - 有 `activity` → 看 `compliance.label` 和 `vs`：
     - `vs.off_plan: true` + `vs.text`（例如「沒照課表：排強度課，實際跑輕鬆」）：強度不到這堂課需要的 50 %，或是別的運動。`vs.intensity_pct`、`vs.hard_min`／`vs.need_min` 是依據。
     - `vs.short: true`：強度 50–80 %（強度不足）或輕鬆跑偏強，算「部分」，不是沒照課表。
     - 只有 `compliance.level` 是 red／yellow：時間或 TSS 偏離（`duration_pct`、`tss_pct`，±20 % 內算符合）。
3. 強度怎麼判的：`GET /api/v1/debug/activity?id=…` 或 `?date=…`，看 `review.classification`（課表類型、`stim` 的 Zone 3 秒數和等效 T@VO2max）和 `thresholds_used`（用的是哪個 LTHR／CP、從哪來）。門檻有疑問再看 `GET /api/v1/debug/thresholds?date=…`。
4. 課表頁顯示的跟 API 不一樣？看 `would_change`（下次開頁會改什麼）和 `meta.data_generation`（資料集是不是還沒重建）。

---

## 部署（NAS）

1. 在 NAS 的 `.env`（不進 repo）加 `TRC_DEBUG_PIN=<自己選的，至少 6 碼>`，重啟容器。
2. 建 image 時帶上版本：`docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD) .`（回應的 `meta.app_version`）。
3. 設定 › 進階設定 › Debug API → 打開 → 產生 token（輸入 PIN）→ 複製交給 agent。
4. 用完就撤銷；token 外流就按「全部撤銷」。
