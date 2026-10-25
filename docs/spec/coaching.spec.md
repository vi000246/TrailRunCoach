# Module Spec: coaching (教練與學員：授權、教練看板、代看、教練代改)

> **Last Updated**: 2026-10-24
> **Status**: Active（SP-789～793 已上線；SP-821、SP-823、SP-826、SP-827 在驗收）
> **Domain Layer**: Supporting
> **Source**: SP-56 `## 結論`（C 混合：看板是唯讀彙總、點進學員用代看、預設全擋＋白名單、學員接受才生效、隨時可撤回、先不收費）

## Overview

教練帶學員：學員按「接受」之後，教練可以在**教練看板**一眼看所有學員的彙總，點進一位學員用**代看**看他的總覽、課表、圖表、活動、路線、賽事，並在學員允許的範圍內**改他的課表與訓練設定**。學員隨時可以撤回，撤回從教練的下一個請求就生效。只在登入模式（多人）有；擁有者模式與示範站沒有這些路由（`backend/main.py:262`）。

| 部分 | 檔案 |
|---|---|
| 規則：誰是教練、關係的狀態、讀學員的唯一入口、開關 | `backend/accounts/coaching.py` |
| 資料表與交易（accounts.db） | `backend/accounts/store.py`（`coach_*` 表與函式） |
| 代看：路徑白名單、開關對應的寫入、稽核摘要 | `backend/accounts/viewing.py` |
| 登入閘：代看換租戶、擋路徑、記稽核、標彙總過期 | `backend/tenancy_mw.py`（7b、7c） |
| 教練看板的彙總：算、存、排程 | `backend/coach_board.py` |
| API | `backend/api/coach.py`、`backend/api/coach_board.py`、`backend/api/viewing.py`；`/api/v1/session` 的教練欄位在 `backend/api/session.py` |
| 代看時的方案與次數 | `backend/membership.py` |
| 畫面 | 設定 › 教練（`backend/static/settings.html` `#coach` ＋ `backend/static/coach_settings.js`）、教練看板（`backend/static/coach_board.html`）、全站橫幅與提示（`backend/static/shell.js`）、課表頁代看時的同步提示（`backend/static/schedule.html`）、管理頁設教練（`backend/static/admin.html`） |

## 誰是教練

- 管理員在管理頁把人設成教練（`PUT /api/v1/admin/users/{id}/coach`，`backend/api/admin.py:485`；`set_coach`，`backend/accounts/coaching.py:221`）。目前不收費（SP-56 Q5）；「是不是教練」跟自己的方案（免費／訂閱）分開，存在 `coach_profiles`。`membership` 的 `COACH` 角色與 `coach:read_students` scope 有定義、沒有發給任何人（`backend/membership.py:121-122`），等教練方案（單獨訂閱）那張再接。
- 每位教練一個**教練代碼**：8 碼、不含 0 O 1 I（`CODE_ALPHABET`／`CODE_LEN`，`backend/accounts/coaching.py:55-56`）；輸入時大小寫、空白、`-` 都可以（`norm_code`，`backend/accounts/coaching.py:213`）。代碼不是秘密，只能「申請」，要教練接受。教練可以重設（舊的失效，`reset_code`，`backend/accounts/coaching.py:240`）。
- 取消教練身分 → 他所有進行中的關係立刻結束、邀請連結刪掉（`store.set_coach`，`backend/accounts/store.py:1745`）；再設回教練不會恢復。

## 授權關係（SP-789、SP-793）

狀態：`pending`（學員輸入代碼、等教練接受）→ `active` → `ended`（`ended_by` 記誰結束）。表本身是多對多；同一對同時只能有一條進行中（`coach_links_live` 部分唯一索引，`backend/accounts/store.py:311`）。

| 怎麼開始 | 誰發起 | 生效 |
|---|---|---|
| 教練發**邀請連結**，學員登入後打開、看到教練名字、按「接受」 | 教練 | 學員按接受當下就 `active`（`coach_accept_invite`，`backend/accounts/store.py:1931`） |
| 學員輸入**教練代碼**送出 | 學員 | `pending`，教練在設定 › 教練按接受才 `active`（`coach_request`，`backend/accounts/store.py:1918`；`coach_accept_request`，`backend/accounts/store.py:1949`） |

- **邀請連結**：一位教練同時一條、30 天（`INVITE_TTL_DAYS`，`backend/accounts/coaching.py:54`）、可以給很多學員用；重新產生或停用就讓舊的失效。資料庫只存 token 的 sha256（`put_coach_invite`，`backend/accounts/store.py:1793`）。網址是 `/api/v1/app/settings?coach_token=…#coach`（`invite_url`，`backend/accounts/coaching.py:262`）：token 放 query 才撐得過登入流程，請求日誌會遮掉，頁面一載入就從網址列拿掉（`backend/static/coach_settings.js:23`）。
- **猜代碼**：同一個人 10 分鐘內查不到 10 次後回 429（`CODE_FAILS`，`backend/accounts/coaching.py:67`；`lookup_code`，`backend/accounts/coaching.py:330`）。
- **一位學員一位教練**（第一版）：進行中（pending＋active）最多 `MAX_COACHES_PER_STUDENT` = 1 條（`backend/accounts/coaching.py:53`；`_student_room`，`backend/accounts/store.py:1909`）。要換教練先解除或取消（409 `HAS_COACH`／`REQUEST_PENDING`）。
- **教練的學員上限**（SP-793）：只算 `active`；預設 `DEFAULT_MAX_STUDENTS` = 4（`backend/accounts/coaching.py:51`），管理頁可設單一教練的值（0～1000，空白＝預設；`set_max`，`backend/accounts/coaching.py:234`）。擋在「讓關係生效的那一下」（學員接受邀請、教練接受申請），檢查跟寫入同一個交易（`_coach_room`，`backend/accounts/store.py:1887`）；滿了回 409 `COACH_FULL` 並說明人數（`refused`，`backend/accounts/coaching.py:91`）。調低上限不解除既有學員；pending 不佔名額。
- **結束**：學員撤回或取消申請（`end_as_student`，`backend/accounts/coaching.py:372`）、教練解除或拒絕（`end_as_coach`，`backend/accounts/coaching.py:381`）、管理員取消教練身分。不能當自己的教練（409 `SELF`）。
- **同意紀錄**：關係生效與有效關係結束各寫一列學員的 consents（action `coach_accept`／`coach_end`，client `coach`，記教練 id 與名字、誰發起、誰結束；`_coach_consent`，`backend/accounts/store.py:1716`）；學員改開關寫 `coach_perms`（`backend/accounts/store.py:2002`）。送出、取消、拒絕申請不寫（那時還沒授權）。
- **看得到什麼身分資料**：教練看學員的顯示名稱與 user id（看板與代看的網址用），學員看教練的顯示名稱；彼此都看不到 e-mail；沒名字的顯示「跑者 #id」（`_name`，`backend/accounts/coaching.py:271`）。
- **下載我的資料**有自己的教練檔案、教練、學員（含已結束的，`export_record`，`backend/accounts/coaching.py:394`）；刪帳號時刪掉所有指名那個人的教練資料，學員 consents 裡的教練名字清空、紀錄留著（`backend/accounts/store.py:89`）。

## 讀學員資料的唯一入口

看板、代看、代改讀學員資料**一律**經 `student_of`（`backend/accounts/coaching.py:117`）／`in_student`（`backend/accounts/coaching.py:154`）。每次請求重讀 accounts.db 的 `active_coach_link`（`backend/accounts/store.py:1858`）：關係 active、兩邊帳號沒停用、教練還是教練。不符一律 403 `NOT_YOUR_STUDENT`——不管那個 id 是別人的學員、沒人的、pending、已結束或根本不存在，答案都一樣。不准自己 `tenants.tenant_of(store.get_user(<請求帶來的 id>))`（`test_coaching_sp789.py::test_the_coach_api_never_opens_a_tenant_by_an_id_itself` 擋 `api/coach.py`）。

## 教練看板（SP-790、SP-826、SP-827）

唯讀彙總，一位學員一列。打開看板**不算任何東西**：每位學員一份小 JSON 預先算好，存在**學員自己的租戶** `cache/coach_summary.json`（`FILE`，`backend/coach_board.py:52`；衍生資料，`backend/data_registry.py:303`：不進備份、刪帳號跟著資料夾刪；學員撤回後留著不刪、不再算）。理由：N 位學員各建一次 Dataset 會吃光記憶體預算。

**彙總內容**（`build`，`backend/coach_board.py:239`；每一塊各自失敗就是 `null`，其他照常）：

| 欄位 | 內容 |
|---|---|
| `volume.d7`／`d28` | 含今天往回 7／28 天、所有運動合計：堂數、移動時間、距離、爬升、TSS（總覽的加總，`volume`，`backend/coach_board.py:159`） |
| `load` | 今天的 CTL／ATL／TSB（總覽的 PMC）；首次匯入回填中是 `{pending: true}`（`load`，`backend/coach_board.py:165`） |
| `next_race` | 賽季計畫下一場（進行中的多天賽也算；`next_race`，`backend/coach_board.py:179`） |
| `plan` | 近 28 天（不含今天）done ÷（done＋missed）、本週完成／排的堂數；「待確認」提醒與熱適應被動訓練不算一堂（`SKIP_KINDS`；`plan_completion`，`backend/coach_board.py:200`） |
| `last_activity`、`alerts` | 最新活動日；總覽狀態的紅／黃燈，標題存算的時候的語言（`status_alerts`，`backend/coach_board.py:228`） |

**看板的一列**（`board_row`，`backend/coach_board.py:489`）在打開時加上會隨時間變的：最後同步（學員設定裡各來源 `sync.*.last_ok` 最新一筆，唯讀，`last_sync`，`backend/coach_board.py:428`——同步了沒新活動也是最新的）、賽事倒數、警示換成教練的語言（`title_in`，`backend/coach_board.py:459`）。看板自己的警示（推估門檻，`backend/coach_board.py:57-59`）：7 天沒同步黃、14 天紅、從沒同步黃；有同步但近 7 天沒活動黃；近 28 天至少 4 堂到期且完成不到 60% 黃。`refreshing` = 彙總被標成過期、重算中（SP-826）。

**API 的順序**：紅燈多的在前，再黃燈多的，再照名字（`_order`，`backend/api/coach_board.py:57`）。
**頁面的排序與篩選**（SP-827，`backend/static/coach_board.html:217`）：在頁面上排，API 回答不變。排序三選一：警示多的在前（＝API 順序）、賽事日期近的在前（`in_days` 小到大）、課表完成度低的在前（`d28.rate` 低到高）；沒有值的排最後，同分照警示順序。「只看有紅燈的」＝至少一個 `bad` 警示。選擇存在這台瀏覽器的 localStorage `coach_board.view`，看不懂的值回預設。
**點學員名字**進代看：頁面 POST `open_url`（`/api/v1/coach/view/{id}`，`STUDENT_VIEW_URL`，`backend/coach_board.py:62`），跳到回傳的 `go`（`backend/static/coach_board.html:190`）。看板頁不顯示右下角的建議浮動框（`backend/static/shell.js:1287`）。

### 什麼時候算

| 時機 | 怎麼做 |
|---|---|
| 學員的 Dataset 暖機做完（同步有新活動、部署／啟動、學員改設定） | 暖機最後一步順手算（Dataset 與總覽狀態都在記憶體裡；`after_warm_up`，`backend/coach_board.py:290`，由 `backend/api/wko5views.py:673` 呼叫） |
| 學員（或代改的教練）成功改了賽事、周期或課表（SP-826） | 登入閘在 2xx 寫入後比對 `PLAN_WRITES`（`backend/coach_board.py:81`；`changes_plan`，`backend/coach_board.py:399`），比到就在學員租戶的彙總寫 `stale_at`、排一個 `warm` 工作（`mark_stale`，`backend/coach_board.py:404`；呼叫點 `backend/tenancy_mw.py:597`）；請求本身不算 |
| 排程每分鐘（`tick`，`backend/coach_board.py:357`，由 `backend/sync/scheduler.py:225` 呼叫） | 還沒有彙總的（剛接受教練）、`stale_at` 還在的，排 `warm`；彙總日期是昨天以前、已過學員當地 04:00＋0～59 分（依租戶錯開，`NIGHT_AT`／`night_offset`）的排 `cold`（`due`，`backend/coach_board.py:335`） |

- 都走伺服器的工作佇列，一個租戶同時只有一個（key `("coach-summary", <租戶>)`，`_queue`，`backend/coach_board.py:389`）。
- 重算中又被改：`_store`（`backend/coach_board.py:279`）比對算之前讀到的 `stale_at`，有更新的就保留標記，下一分鐘再算一次。
- 算失敗等 `RETRY_S`（1 小時）再試；學員改了東西就不等（`mark_stale` 清掉失敗紀錄）。
- 只算有有效教練的學員（`coached_student_ids`，`backend/accounts/store.py:1872`；`coached`，`backend/coach_board.py:131`）；排隊時撤回了，工作就不做（`_job`，`backend/coach_board.py:316`）。
- `PLAN_WRITES` 包含：賽事（含 GPX、分段、疊石匯入）、周期、課表的加改刪交換還原連結、刪過期、排測試、接受建議、reconcile、不排課日、休息日、排課偏好、自動調整的立即執行／核准／復原。不含：門檻與心率／功率區間、推送 COROS、範本、不存東西的預覽。

## 代看（SP-791、SP-821）

教練在設定 › 教練 › 我的學員按「查看資料」（`backend/static/coach_settings.js:236`）或在看板點名字 → `POST /api/v1/coach/view/{id}`（`_start`，`backend/api/viewing.py:51`）：在 accounts.db `coach_views` 記「這個 session 在看誰」（以 session token 的 sha256 為鍵，登出、刪帳號就跟著刪；`coach_view_put`，`backend/accounts/store.py:2014`），寫一筆稽核 `view_start`，回 `{go: 總覽}`。

**登入閘每個請求**（`backend/tenancy_mw.py:511`）：

1. `viewing.resolve`（`backend/accounts/viewing.py:179`）重查關係 → `None`（沒在代看）、`ENDED` 或一個 `View`。查不了 accounts.db 就 503，不放行。
2. `ENDED`（撤回、結束、停用、不再是教練）：API 回 403 `COACH_VIEW_ENDED`（頁面收到會重新整理），打開頁面就清掉代看、送回 `/api/v1/app/settings?coach_view=ended#coach`。`/api/v1/session`、`/api/v1/coach/view/stop`、登出照常能用（CSRF 要拿得到、教練要能脫身）。
3. 有 `View`：`viewing.check`（`backend/accounts/viewing.py:209`）**預設全擋**，被擋的頁面導回 `/api/v1/overview/page?coach_view=blocked`、API 回 403 與原因（`refusal`，`backend/accounts/viewing.py:228`）。
4. 通過的請求：租戶換成學員的（caps 只剩開關給的那幾個，`caps_for`，`backend/accounts/viewing.py:175`），登入者仍是教練（`login.current_user`），回答加 `X-TRC-Viewing: 1`。

| 請求 | 規則 |
|---|---|
| 讀（GET／HEAD） | 只開 `READ_OPEN`（`backend/accounts/viewing.py:70`）：總覽、課表、圖表、活動、路線、賽事、賽事計算機、設定的生理數據、手冊；扣掉 `READ_BLOCK`（`backend/accounts/viewing.py:91`）：單檔下載與 zip、傷病紀錄與單次活動的疼痛、天氣金鑰、分享、Cairn 曲線、課表訂閱網址。其他一律 403 `COACH_VIEW_BLOCKED`（帳號、平台連線與密碼、刪除、下載我的資料、付款、疊石 Cairn、管理頁、debug API、備份） |
| 不存東西的寫入 | `CALC`（`backend/accounts/viewing.py:97`：賽事計算機的預測、課表編輯器的預覽與步驟推導等）、停止代看、登出：放行 |
| 學員允許的寫入 | `WRITE_OPEN`（`backend/accounts/viewing.py:104`），各掛一個開關；開關關著 403 `COACH_PERM_OFF`（見下節） |
| 其他寫入 | 403 `COACH_VIEW_READONLY` |

- **新路由預設是關的**：沒列進白名單的路由代看時打不開（`test_coach_view_sp791.py::test_every_read_but_the_open_ones_is_refused_while_viewing`、`::test_every_write_but_the_open_ones_is_refused_while_viewing` 走過 app 的每一條路由）。
- **站主（user 1）的資料不開放代看**：開始時 403 `COACH_VIEW_OWNER`（`backend/api/viewing.py:54`），`resolve` 也當 `ENDED`（站主租戶有站主專屬資源）。
- **方案與次數用教練的**：`membership.member()` 在代看時改用 `coach_member`（`backend/membership.py:243`、`backend/membership.py:272`）；站主或管理員當教練時用訂閱者的範圍，拿不到站主專屬與管理 scope。
- **頁面**：每頁導覽列下一條藍色橫幅「正在看 X 的資料」＋教練能改什麼＋「回到我自己」（POST `/api/v1/coach/view/stop`，寫 `view_end`，回到 `/api/v1/app/settings#coach`；`backend/static/shell.js:295`）；傷病紀錄與管理頁從選單拿掉（`backend/static/shell.js:61`）；開關沒給的控制項照示範站的方式鎖住（`backend/static/shell.js:989`）；被擋的寫入跳提示、`COACH_VIEW_ENDED` 自動重新整理（`backend/static/shell.js:426-432`）；不自動同步、不掃 FIT 資料夾、不送時區、不跑首次設定精靈（`backend/static/shell.js:1265`）；設定頁只剩生理數據分頁（`backend/static/settings.html:262`）。
- **課表頁的 COROS 提示**（SP-821，`VIEWING`，`backend/static/schedule.html:1646`）：代看時不顯示「COROS 未登入 · 到設定頁登入 →」與「抓活動」和它的設定連結（`backend/static/schedule.html:1660`、`backend/static/schedule.html:1876`），狀態列寫「學員的 COROS 未登入，要學員自己登入後才能推送到手錶」（`st.view_login`），推送遇到學員登入過期寫「要學員自己到設定頁重新登入」、沒有連結（`err.view_relogin`，`backend/static/schedule.html:1696`）。學員自己的頁面不變。
- **離線快取**：代看時 service worker 不存、不用教練自己的離線副本回答，帶 `X-TRC-Viewing` 的回答不進快取（`backend/static/offline_core.js:376`、`backend/static/offline_core.js:482`）。

## 教練代改（SP-792）

學員在設定 › 教練 › 我的教練對每條有效關係有兩個開關（`PERMS`，`backend/accounts/coaching.py:60`），存在 `coach_links.perms`。從沒設過的關係＝兩個都開（`PERMS_DEFAULT`；`perms_of`，`backend/accounts/coaching.py:124`）。關掉從教練的下一個請求就生效。

| 開關 | 開放的寫入（`WRITE_OPEN`） | 給代看的 caps（`CAPS`，`backend/accounts/viewing.py:135`） |
|---|---|---|
| `plan` 改課表、賽事與不排課日 | 課表的新增／修改／刪除／對調／改回自動／對應活動、刪過期、排測試、處理建議、reconcile、排課偏好、推到手錶與從手錶移除、不排課日、休息日、賽事（含 GPX、分段）、周期 | `plan.write`、`push`、`upload.gpx` |
| `training` 改門檻與訓練設定 | 門檻紀錄與套用估計、心率設定、功率區間、起始長短期負荷、比賽配速 | `thresholds.write` |

不含：課表範本庫、路線庫、自動調整的設定與核准、賽事計算機存檔、基本資料（體重、性別、年齡）、功率來源。

**修改紀錄**：每筆成功（2xx）的代改，登入閘讀一次請求與回答，摘幾個短欄位（日期、名稱、門檻數值、改了哪些欄位，不存整個請求；`summary`，`backend/accounts/viewing.py:269`）寫進 accounts.db `coach_audit`（`record`，`backend/accounts/viewing.py:294`；`coach_audit_add`，`backend/accounts/store.py:2047`）；開始／結束代看也記。每位學員留最近 500 筆（`COACH_AUDIT_KEEP`，`backend/accounts/store.py:2011`）；教練帳號刪掉時名字拿掉、紀錄留著。學員在設定 › 教練看最近 50 筆（`GET /api/v1/account/coach/edits`；`backend/static/coach_settings.js:106`）；總覽與課表頁導覽列下一行「教練 X 於 … 修改」：最近 14 天內最後一筆修改（不含開始／結束代看；`COACH_EDIT_DAYS`，`backend/api/session.py:125`），可關閉，關掉記在這台瀏覽器（`backend/static/shell.js:358`）。

## 學員申請的提示（SP-823）

教練有學員用代碼送出、還在等接受的申請（學員帳號沒停用；邀請連結不需要教練確認，不算）→ `/api/v1/session` 帶 `coach_requests {n, at}`（`pending_requests`，`backend/accounts/coaching.py:301`；`_coaching_view`，`backend/api/session.py:128`），**每一頁**導覽列下一行「N 位學員想加入，等你確認 · 到設定 › 教練確認 → · 先關掉」（`backend/static/shell.js:379`）。站內提示，不寄信、不推播。「先關掉」記在這台瀏覽器、以最新一筆申請的時間為準：有新申請才再出現。在設定 › 教練處理完最後一筆，同頁的提示馬上拿掉（`backend/static/coach_settings.js:249`）；別的頁要重新整理（不輪詢）。代看中不顯示。

## 資料模型

accounts.db（`backend/accounts/store.py:292-336`，冪等建表）：

| 表 | 內容 |
|---|---|
| `coach_profiles` | 誰是教練：`code`（唯一）、`max_students`（NULL＝預設）、誰給的、何時 |
| `coach_links` | 教練、學員、`status`（pending／active／ended）、`initiated_by`（coach／student）、`perms`（JSON）、建立／接受／結束時間、`ended_by` |
| `coach_invites` | 教練的邀請連結：token sha256、到期；每位教練一列 |
| `coach_views` | 哪個 session 在代看哪位學員（鍵是 session token 的 sha256，跟著 session 刪） |
| `coach_audit` | 教練在學員資料裡做了什麼：學員、教練 id 與當時的名字、時間、`action`、`params`（JSON，幾個短欄位） |

學員租戶：`cache/coach_summary.json`（`v`、`computed_at`、`day`、`reason`、上表欄位、`status_counts`、可能有 `stale_at`）。

## API

全部 `include_in_schema=False`；登入模式才有（否則 404），示範站不掛；寫入照常過 CSRF。回答 `Cache-Control: no-store`。

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/v1/account/coach` | 我的教練（進行中的關係、各自的開關與說明文字）、我是不是教練 |
| POST | `/api/v1/account/coach/code` | `{code}` → 那位教練的名字（404 `NO_CODE`、429） |
| POST | `/api/v1/account/coach/request` | `{code}` → 送出申請（pending） |
| POST | `/api/v1/account/coach/invite` | `{token}` → 邀請連結是誰發的（404 `INVITE_INVALID`） |
| POST | `/api/v1/account/coach/invite/accept` | `{token}` → 接受，立刻 active（409 `HAS_COACH`／`REQUEST_PENDING`／`COACH_FULL`／`SELF`） |
| POST | `/api/v1/account/coach/{link}/end` | 撤回（active）或取消申請（pending） |
| PUT | `/api/v1/account/coach/{link}/perms` | `{plan?, training?}` 改開關（400 `BAD_PERM`） |
| GET | `/api/v1/account/coach/edits` | 教練在我的資料做了什麼，新的在前（`?limit` ≤ 200） |
| GET | `/api/v1/coach` | 我的代碼、邀請連結到期日（不回 token）、學員與申請、上限與已用 |
| POST／DELETE | `/api/v1/coach/invite` | 產生新的邀請連結 `{url, expires_at}`（URL 只在這次回答裡）／停用 |
| POST | `/api/v1/coach/code` | 重設代碼 |
| POST | `/api/v1/coach/requests/{link}/accept` | 接受申請（409 `COACH_FULL`、`HAS_COACH`） |
| POST | `/api/v1/coach/links/{link}/end` | 解除學員或拒絕申請 |
| GET | `/api/v1/coach/students/{id}` | 一位有效學員：名字、何時開始、開關（403 `NOT_YOUR_STUDENT`） |
| GET | `/api/v1/coach/board` | 看板：每位有效學員一列＋`used`、`max_students`、`pending` |
| GET | `/api/v1/coach/students/{id}/summary` | 一位學員的看板列 |
| GET | `/api/v1/coach/board/page` | 看板頁 |
| POST | `/api/v1/coach/view/{id}` | 開始代看 → `{go, student}`（403 `NOT_A_COACH`／`NOT_YOUR_STUDENT`／`COACH_VIEW_OWNER`；代看中不能再開，要先停） |
| POST | `/api/v1/coach/view/stop` | 停止代看 → `{go: 設定 › 教練}` |
| PUT | `/api/v1/admin/users/{id}/coach` | 管理頁：`{coach?, max_students?}`（寫操作紀錄 `user.coach`／`user.coach_max`） |

非教練打教練端 403 `NOT_A_COACH`（`require_coach`，`backend/accounts/coaching.py:197`）。`/api/v1/session` 另帶：`user.coach`（教練的「更多」選單出現「教練看板」，`backend/static/shell.js:210`）、`viewing {student_id, name, perms, back}`（代看中）、`coach_edit`、`coach_requests`。

## Domain Model

### Bounded Context
- **Context Name**: Coaching（教練與學員）
- **Domain Layer**: Supporting（授權與存取控制；訓練分析與課表邏輯在 overview、plan-auto 等模組，這裡只決定誰能讀、能改學員的哪些資料）
- **Parent Module**: N/A — 掛在帳號系統（`backend/accounts/`）與登入閘（`backend/tenancy_mw.py`）上；看板彙總讀 overview.spec.md 的加總與 PMC

### Ubiquitous Language

| Term | Definition | Code |
|------|-----------|------|
| 教練 | 管理員設定、有 `coach_profiles` 一列的帳號；跟自己的方案無關 | `is_coach`，`backend/accounts/coaching.py:193` |
| 關係（link） | 一位教練與一位學員：pending／active／ended | `coach_links` |
| 有效學員 | active 關係、兩邊帳號沒停用、教練還是教練 | `active_coach_link`，`backend/accounts/store.py:1858` |
| 教練代碼 | 學員輸入用來申請的 8 碼，不是秘密 | `coach_profiles.code` |
| 邀請連結 | 教練發出、學員按接受就生效的連結，30 天 | `new_invite`，`backend/accounts/coaching.py:252` |
| 學員上限 | 一位教練最多幾位有效學員（預設 4） | `max_students`，`backend/accounts/coaching.py:204` |
| 教練看板 | 所有有效學員的唯讀彙總表 | `backend/api/coach_board.py` |
| 彙總 | 一位學員預先算好的看板資料，存在學員租戶 | `build`，`backend/coach_board.py:239` |
| 更新中 | 彙總被學員的修改標成過期、重算中 | `stale_at`、`refreshing` |
| 代看 | 教練的 session 用學員的租戶看資料，登入者仍是教練 | `View`，`backend/accounts/viewing.py:143` |
| 開關（權限） | 學員允許教練改的範圍：`plan`、`training` | `PERMS`，`backend/accounts/coaching.py:60` |
| 修改紀錄 | 教練在學員資料裡的動作，學員看得到 | `coach_audit` |

### Domain Events

| Event | Trigger Condition | Consumers |
|---|---|---|
| 關係生效（`coach_accept`） | 學員接受邀請、教練接受申請 | 學員 consents；下一次 `tick` 開始算彙總 |
| 關係結束（`coach_end`） | 學員撤回、教練解除、管理員取消教練 | 學員 consents；代看下一個請求變 `ENDED`；彙總停算 |
| 開關改變（`coach_perms`） | 學員在設定 › 教練切開關 | 學員 consents；代看下一個請求的 `perms`／caps |
| 代看開始／結束（`view_start`／`view_end`） | 教練按查看資料／回到我自己 | `coach_audit` |
| 代改（`coach_audit` 一列） | 代看中一筆 `WRITE_OPEN` 寫入 2xx | 學員的修改紀錄、「教練 X 於 … 修改」那一行 |
| 賽事或課表改了 | 有教練的學員（或代改的教練）一筆 `PLAN_WRITES` 寫入 2xx | `mark_stale` → 彙總重算 |
| Dataset 暖機完成 | 同步、部署、改設定 | `after_warm_up` → 彙總重算 |

## Decisions Log

| 決定 | Rationale |
|---|---|
| C 混合：看板唯讀彙總＋點進去代看，代看預設全擋＋白名單 | 要看全部又能改課表；切換身分（B）要重審每支 API、越權風險最高（SP-56） |
| 兩種邀請都要學員親自按接受 | 代碼不是秘密，不讓陌生人佔掉教練名額（SP-56 Q2、SP-789） |
| 一位學員同時一位教練 | 兩位教練會搶同一份課表（SP-56 Q3） |
| 彙總存在學員租戶、預先算 | 資料留在學員那邊；打開看板不建 N 個 Dataset（SP-790） |
| 讀也預設全擋 | 漏掉的是「該放沒放」而不是「該擋沒擋」；傷病紀錄、單檔下載、課表訂閱網址也擋（SP-791） |
| 站主的資料不開放代看 | 站主租戶有站主專屬資源（SP-791，回應 SP-789 建議另開） |
| 代看的方案與次數算教練的 | 不用學員的額度做教練的事（SP-56 Q5、SP-791） |
| 開關兩個、預設開、推到手錶算在課表 | 學員好懂；SP-56「教練可改、學員可關」（SP-792） |
| 上限只算 active、擋在生效那一下、同一個交易 | 名額給真的在帶的學員；兩人同時按不會超額（SP-793） |
| 代看時課表頁的 COROS 提示改寫而不是藏起 | 教練要知道學員手錶沒連上才能提醒學員（SP-821） |
| 學員申請用站內提示、每頁一行 | 寄信或推播是對外送出，要另外拍板（SP-823） |
| 彙總過期的觸發放在登入閘、用路徑清單 | 課表寫入二十幾支；代改也走同一個閘；測試逼新路由選邊（SP-826） |
| 排序篩選在頁面上做、存 localStorage | 學員數有上限、資料都在頁面上；預設順序跟 API 一致（SP-827） |

## Testing

| 測試 | 涵蓋 |
|---|---|
| `backend/tests/test_coaching_sp789.py` | 邀請、申請、接受、撤回、一位教練、猜代碼、只讀到自己的有效學員、管理員取消教練、停用帳號、刪帳號、靜態檢查 `api/coach.py` 不自己開租戶 |
| `backend/tests/test_coach_limit_sp793.py` | 學員上限、管理頁設定 |
| `backend/tests/test_coach_board_sp790.py` | 彙總內容、何時算、看板權限與排序、警示 |
| `backend/tests/test_coach_board_refresh_sp826.py` | `stale_at`、重算中又改、每支賽季與課表寫入路由都在 `PLAN_WRITES` 的某一邊（`test_every_plan_write_route_is_on_one_side`） |
| `backend/tests/test_coach_view_sp791.py` | 代看的讀寫白名單（走過每一條路由）、撤回、站主、方案、稽核 |
| `backend/tests/test_coach_edits_sp792.py` | 開關、代改的路由、修改紀錄、推到手錶 |
| `backend/tests/test_coach_requests_sp823.py` | `coach_requests` |
| e2e（`backend/tests/e2e/`） | `test_settings_coach_tab_sp789_e2e.py`、`test_coach_board_sp790_e2e.py`、`test_coach_view_sp791_e2e.py`、`test_schedule_coros_login_view_sp821_e2e.py`、`test_coach_requests_sp823_e2e.py`、`test_coach_board_sp826_e2e.py`、`test_coach_board_sort_sp827_e2e.py` |

## Known limits

- 不收費：教練由管理員手動設定；`COACH` 角色與 `coach:read_students` scope 還沒發（教練方案要單獨訂閱，SP-56）。
- 彙總只在賽事、周期、課表改變時馬上重算；改門檻、心率／功率區間後，以及同步後自動調整在背景改了課表時，要等下次暖機或夜間（SP-826 建議另開，未開單）。
- 學員申請的提示跟著頁面載入的 session，不輪詢。
- 看板、代看都只有登入模式；擁有者模式與示範站沒有。

## Open Questions

- [ ] 學員可另外允許教練看傷病紀錄——現在 `READ_BLOCK` 一律擋（SP-822，Todo，尚未實作）
- [ ] 學員也是站主（user 1）時的代看範圍——程式現在一律擋（`COACH_VIEW_OWNER`，SP-791 的決定）；單還開著（SP-824，Todo）
- [ ] 量正式站夜間重算的負載，再決定要不要只在同步過的日子算（SP-828，Todo，尚未實作）
- [ ] 教練代管、沒有自己帳號的學員（SP-794，Backlog，尚未實作）
- [ ] 未登入首頁放教練功能的介紹（SP-825，Todo，尚未實作）

## Change History

| Date | Source | Ticket | Summary |
|------|--------|--------|---------|
| 2026-10-20 | feature | SP-789 | 授權關係：accounts.db `coach_profiles`／`coach_links`／`coach_invites`、`backend/accounts/coaching.py`（規則、`student_of`／`in_student`）、`backend/api/coach.py`、設定 › 教練分頁、管理頁設教練、下載我的資料與刪帳號。commit `4877ae51` |
| 2026-10-20 | feature | SP-793 | 教練學員上限：預設 4、管理頁可設單一教練、擋在生效那一下（同一個交易）。檢查在 `4877ae51`，測試 `f8a01445` |
| 2026-10-20 | feature | SP-790 | 教練看板：`backend/coach_board.py`（學員租戶裡的彙總、暖機後與夜間算）、`backend/api/coach_board.py`、`static/coach_board.html`、「更多」選單入口。commit `5fd383ff` |
| 2026-10-20 | feature | SP-791 | 代看：`coach_views`、`backend/accounts/viewing.py` 讀寫預設全擋、登入閘換租戶、教練的方案與次數、橫幅與回到我自己、離線快取不存、站主不開放；看板名字接上代看（`62654f44`）。commit `183c73bc` |
| 2026-10-20 | feature | SP-792 | 教練代改：兩個開關（`coach_links.perms`）、`WRITE_OPEN`、`coach_audit` 與學員的修改紀錄、總覽與課表的「教練 X 於 … 修改」。commit `183c73bc` |
| 2026-10-24 | fix | SP-821 | 代看時課表頁不顯示 COROS 登入連結與抓活動，狀態列與推送錯誤改寫成要學員自己登入。commit `2b8bdc1d`（當時寫在 overview.spec.md） |
| 2026-10-24 | feature | SP-823 | 教練有學員申請時，`/api/v1/session` 帶 `coach_requests`，每頁一行提示。commit `59449ba5` |
| 2026-10-24 | feature | SP-826 | 改賽事、周期、課表後彙總立即標成更新中並排重算（`PLAN_WRITES`、`mark_stale`、`stale_at`），看板顯示更新中。commit `efeadbd6` |
| 2026-10-24 | feature | SP-827 | 看板排序（警示／賽事日期／課表完成度）與「只看有紅燈的」，在頁面上做、存 localStorage。commit `21affb3b` |
| 2026-10-24 | maintenance | SP-943 | 新增 module spec：照程式反推，整理上面各張單的規則；overview.spec.md 的代看段落改成指到這裡 |
