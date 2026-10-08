# SRS: Coros Sync — 非官方 API 自動下載 FIT

## Metadata
- **Source PRDs**:
  - `docs/prd/wko5-training-ai.prd.md` — Milestone 4（initial: Coros 非官方 API）
  - `docs/prd/wko5-trail-multipage-sync-coach.prd.md` — Milestone 3（統一同步頁 + 已載入資料盤點 + TP 接 UI）
- **Source Linear Issue**: N/A — standalone
- **Owner**: maintainer
- **Status**: IMPLEMENTED（M3 delta 進行中）
- **Generated**: 2026-05-15
- **Last updated**: 2026-10-08

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|---|---|---|---|
| 排課「負荷」用 RPE 填之後，PMC 用哪個負荷 | 手錶記錄的負荷；RPE 換算只用在排課目標 | 用 RPE 修正活動的 TSS | 使用者 2026-10-04 決定負荷以手錶數據為準（SP-57） |
| 匯入時要不要算、存每筆指標與 COROS 帳號 FTP | 不算、不存（`workout_metrics`、`mmp_cache`、`ftp_w`）；帳號 LTHR 照寫 | 照舊寫入；或連 LTHR 一起拿掉 | 沒有任何程式讀那些值，圖表與 PMC 都由 FIT Dataset 算；LTHR 是冷啟動的先驗（使用者要求，commit `65bad11d`） |

## Open Questions

- [x] 「負荷」的 RPE 改成每一檔各自換算：預設用 TSS 的定義（IF² × 100／小時），資料夠了逐檔擬合、往預設收縮（SP-57）——已做 2026-10-08，見「『負荷』用 RPE 填：每一檔的每小時 TSS」
- [ ] 同步效能：同步時不卡住課表載入（SP-362，In Progress）——同步側已做：失敗清單、cursor 照常推進、完整檢查／每週檢查、跑後自評回填移到背景；課表側（推送移出寫入鎖、課表先顯示上一份）另做
- [ ] COROS 活動列表回應有沒有總筆數：沒驗證，所以每週檢查沒有做逐月筆數比對（SP-362 A4）
- [x] 備份拿掉 `sync_state`（token 與封存的密碼）（SP-355）——已做，見「備份（SP-355）」
- [ ] Garmin 同步，用 `garminconnect`，資料來源順序 COROS → Garmin → TrainingPeaks（SP-91，Todo）——尚未實作
- [ ] FIT 改用內容 sha256 命名的不可變物件，會改到 `fit/<source>/` 的路徑規則（SP-308，Todo）——尚未實作
- [ ] 手動上傳 FIT（選檔、選資料夾），接上後端去重（SP-323，Todo）——尚未實作
- [ ] 其他同步來源：透過第三方平台、intervals.icu、COROS 官方 MCP 取代帳密登入、匯入帳號匯出檔、Apple Health、把同步拆到 private repo（SP-343 調查 Todo；SP-344、345、346、348、349，Backlog）——尚未實作

## Change History

| Date | Source PRD | Feature SRS | Summary |
|------|------------|-------------|---------|
| 2026-05-15 | `wko5-training-ai.prd.md` | (initial) | Created — Coros 非官方 API 自動下載 FIT，已驗證 1088 筆 |
| 2026-06-13 | `wko5-trail-multipage-sync-coach.prd.md` | `docs/srs/coros-sync-unified-sync-page-data-inventory.srs.md` | 統一同步頁：TP 下載接上 UI、新增 `/sync/inventory` 盤點端點、CorosPage→SyncPage |
| 2026-09-30 | code-sync | — | 同步強化：增量 cursor、錯誤不推進 cursor、失敗 rollback、跨來源去重（`duplicate_of`）、本地日期（`start_time_utc`）、token 以 Fernet 加密。TP 改走網站登入 / WKO5-client OAuth，檔案改用 `details` + `rawfiledata` 下載 |
| 2026-09-30 | code-sync | — | 設定頁「資料同步」區塊、每來源互斥鎖（409 `SYNC_BUSY`）、每日排程（lifespan task）、`POST /sync/auto` + `autosync.js`、每來源獨立 FIT 資料夾與遷移腳本、刪除單一來源檔案、`FitFolderDataset` 與 `/sync/compare` |
| 2026-09-30 | code-sync | N/A | 掃描改走 `fit/<source>/` 並標 source + provider id、`FitFolderDataset` 時區取 `athlete.timezone`、`charts.map.basemap` / `charts.map.overlays` 設定鍵、COROS 課表推送改指向 overview.spec.md；路徑改寫成使用者資料夾相對形式 |
| 2026-10-01 | feat/auto-replan | N/A | 同步結束時，若這次下載 ≥ 1 筆活動（狀態 ok／partial），`runner.stream` 會呼叫 `plan_auto.after_sync`，在背景 task 裡用自己的 DB session 自動調整課表並推送（`docs/spec/plan-auto.spec.md`）。失敗不影響同步結果 |
| 2026-10-01 | bugfix | N/A | `FitFolderDataset` 前置修正：越野分類讀 app DB（含覆寫、跨來源重複）、sub_sport 後備；門檻／體重改成計畫 → `athlete_settings` → as-of 估算，WKO5 athlete 檔改為選用（`charts.fit_settings_from_wko5`）；`source_stamp` 含 DB 簽章 |
| 2026-10-01 | bugfix | user request (COROS vs TP back-test) | 每筆活動的功率來源（`stryd` / `watch` / `none`，`backend/engine/power_source.py`）；手錶推估功率預設不進功率模型、不算功率 TSS（設定 `power.accept_watch_power`，預設 false）；記錄 COROS 與 TP 檔案集合的差異（TP 獨有的一筆垃圾功率檔、TP 缺一段時期的 Stryd 跑步） |
| 2026-10-01 | feature | user request (bad activity files) | 壞掉的活動檔（忘了停錶騎車／開車、功率不可能）整筆排除：`FitFolderDataset`／`Dataset` 不放進 `ds.workouts`、`cptest.bad_files`；覆寫 `activity_tags.exclusion`，設定 `activities.exclude_bad`（預設 true），併入 `source_stamp`（見 workouts.spec.md） |
| 2026-10-02 | feat/primary-source-2 | user request (主要資料來源 follow-ups) | 以開始時間認活動（`backend/engine/activity_key.py`：同檔、去掉 `coros/`／`tp/` 前綴的同檔，否則 ±3 分內最近的開始時間；WKO5 檔名帶開始時間）：背負重量、獨攀標記、山名註記（新紀錄附 `start`，舊的就地沿用，不改鍵）、活動標籤／當作間歇／RPE、活動天氣與路線索引對應、課表完成紀錄的 `done_by.index`（依 `start` 改指目前資料來源的 index，`plan_store.load`／`_plan_rows`／`plan_match.assign`／月曆）。圖表來源讀取時遷移：存的是舊的 `coros` 且沒在新 UI 選過（`charts.data_source.chosen`）→ 視為 `synced`，不寫 DB。`cptest.curves`／`scan` 跟主要來源（`secondary_duplicates`，同 merge 規則；TP 檔名也讀）。實測（DB 與快取複本）：三筆課表完成紀錄的 index 都改指到合併資料裡的同一活動；10/10 標籤、2/2 山名找得到；cptest 跳過 807 個 TP 重複檔 |
| 2026-10-02 | feat/primary-source | user request (主要資料來源) | `sync.primary_source` 改為 自動（預設）／COROS／TrainingPeaks（`backend/sync/primary.py`；自動＝最新活動日最新者，同日看上次同步是否完整、近 90 天筆數，再 COROS）。新圖表來源 `synced`（`charts.data_source` 預設）：兩個 FIT 資料夾合併，同一活動（開始時間 ±2 分，同 dedup）只用主要來源的檔，另一來源只補主要來源沒有的活動；同來源重複檔留樣本數多的；不逐值互補（「算不出來就不要補了」）。`MultiFitStore` 沿用各資料夾既有快取。自動同步（開網站／每日）只同步主要來源，另一個要開 `sync.secondary.auto`（進階）；主要來源停用或登出時改同步另一個。自動模式同步下載後重建 dedup。活動編輯頁顯示來源 badge（`origin`）。實測（唯讀，快取複本）：主要 COROS → 1068 筆（COROS 799、TP 補 269，多為較早年份）；主要 TP → 1068 筆 |
| 2026-10-01 | perf/dataset-load | user request (login / token) | 登入一次：某 region 發了 token 後不再登入其他 region；同時兩個登入回 409 `COROS_LOGIN_BUSY`（COROS 只認最後一次登入）。資料 server 偵測順序：上次偵測到的 → US → 登入 server → 其餘；全部探測失敗（2026-10-01：dataset 建置卡住 event loop，探測全部逾時）時沿用上次的，否則 US。「記住密碼」（預設關）：密碼以 `secrets.seal` 存 `sync_state.coros_password_sealed`／`tp_password_sealed`，token 過期或 result 1019 時自動登入一次、重試一次，取消勾選或登出即刪除（`docs/secrets-and-keys.md`）。同步時 FIT 解析改在 thread，同步下載到新檔後背景重建圖表 Dataset |
| 2026-10-01 | bugfix | user request (charts on COROS) | 圖表分析在 COROS 來源：FIT `vam` 與登山標籤、Stryd-only PD 擬合的圖表 CP（計畫測試之前）、閾值配速推估（CP × 速度／功率比）、區間表來源與日期、越野／爬坡課表看功率、訓練量週增幅改 4 週平均（見「圖表分析在 COROS 來源」） |
| 2026-09-30 | bugfix | N/A | `charts.data_source` 接上圖表 / 總覽 / 功率計算機的 Dataset 工廠與圖表頁資料來源切換；掃描去重的 COROS id 也限定 athlete；`_sync_ids` 接受 `tp` |
| 2026-10-04 | code-sync | N/A | 一次只用一個資料來源（COROS 或 TP，取代 10-02 的自動／合併 `synced`；`charts.data_source` = `source` / `wko5`）；登入有效性檢查 `session_check` + 「登入已過期」橫幅（`/auth/session-alerts`）；COROS 心率設定（`athlete.coros_profile`）登入與每次同步更新；跑步功率 TSS 改用 Stryd-only PD mFTP；`cached_series` 改磁碟快取；時區自動偵測；新端點（`/auth/{source}/remember`、`/sync/timezone/browser`、`/sync/dedup/rebuild` 等）；demo 模式不掛同步路由；`file:line` 指標全面更新；新增 Domain Model |
| 2026-10-04 | feat/sp-34-35-schedule | SP-34 | `GET /sync/primary`（資料來源＋登入／啟用／忙碌）；SSE 讀取抽成 `static/syncrun.js`，設定頁立即同步與課表頁「從 COROS 抓活動」共用 |
| 2026-10-04 | sp-38-load-step | SP-37／SP-38 | 活動列表的 `trainingLoad`（COROS TL）存進 `workout_files.coros_training_load`（新欄位，同步時新活動寫入、已匯入的補上，不多打 API）；同步後的每人校正一併重擬 TSS → TL 換算（`engine/coros_tl.py`：依 TSS 來源分組、收縮到預設、近期加權、門檻大改前的活動不用、最近 30 天時間序回測不比舊的差才換上）與「負荷」步驟的實跑校正；設定頁顯示換算模型（推估） |
| 2026-10-04 | feature | SP-38 follow-up | 推送「負荷」步驟時，重新擬合讓 TL 變動 < 3（推估）就沿用上次送出的 TL，不標「需更新」、不重推；≥ 3 才換 |
| 2026-10-05 | bugfix | SP-88 | 登入過期（例如在 COROS 網頁版 Training Hub 登入後 1019）時總覽看不到：橫幅只在頁面載入時查一次、早於開網站自動同步；同步撞到 1019 只記在記憶體 5 分鐘，之後連不上 COROS 的重查（`unknown`）就當已登入；失敗的同步被算成「上次同步」（自動同步認為已新鮮、`autosync.js` 顯示「已同步 COROS +0」）。改為：被拒過的登入遇到 `unknown` 仍算過期、同步撞到 1019 也存進 `coros_token_expires`；`last_sync_at` 只算成功的同步，新增 `sync.<src>.last_ok`；`/auth/session-alerts` 多回 `sync`（`problem` expired／logged_out／failed、上次成功同步、最近一次結果）；總覽／課表橫幅改成醒目卡片（重新登入連結、上次成功同步），自動同步結束與分頁回到前景時重讀；`autosync.js` 顯示同步失敗；`/sync/primary` 的 `logged_in` 改走登入檢查並回 `login` |
| 2026-10-05 | sp-57-rpe-load | SP-57 | 同步後的每人校正加一項：手錶記錄的 RPE 對活動實際 TSS 擬合「負荷」RPE 換算係數（`engine/rpe_load.py`，Foster session RPE，log 空間收縮到 0.30、w = n ÷ (n + 10)，留一誤差），存 `rpe.load_model`，設定頁顯示；只用於排課目標，PMC 仍用手錶負荷 |
| 2026-10-06 | feat/coros-rpe-sp231 | SP-231 | 跑後自評：每個新活動多一個唯讀 `POST /activity/detail/query?labelId=&sportType=`（GET 回 result=1001），只讀 `data.sportFeelInfo.feelType`（1 最輕～5 最累，0 沒填；不讀 `sportNote`、語音筆記），存 `workout_files.coros_feel`，換成 `rpe`（1→2、2→4、3→5、4→7、5→10，推估；FIT 自己有 RPE 時 FIT 為準，`rpe_source`）。讀取失敗或 0 不算同步錯誤、不卡 cursor；失敗的在之後同步（最近 4 天、每次 ≤ 10 筆）重試。最近 8 週已匯入的活動在之後的同步各讀一次（每次 ≤ 80 筆、每筆間隔 0.4 秒，不登入、不寫 COROS，完成記在 `sync.coros.rpe_backfill`；token 被拒不算一輪，3 輪後停）。有補到自評時 `complete` 事件帶 `rpe_filled`，觸發自動調整與每人校正 |
| 2026-10-06 | integrate/2026-10-06c | SP-231 follow-up | 依唯讀實測修正：每筆 detail 回整個活動（1.4–3.2 MB、約 1.5–3 秒），回填改每次同步 ≤ 25 筆（`BACKFILL_MAX`），剩下的留給之後的同步；「3 輪後停」只算有讀取失敗的輪（`failed_passes`），只是讀不完不算；沒存 `coros_sport_type` 的 COROS 活動不論運動一律用 sportType 100 讀（COROS 這個查詢不看 sportType）；最近 4 天存成 0（沒填）的活動在重試輪再讀一次（同樣每次 ≤ 10 筆，這次同步剛讀過的不重讀），之後在 COROS app 補填的自評照樣換成 RPE（FIT 有 RPE 時仍以 FIT 為準）並帶 `rpe_filled` |
| 2026-10-07 | feat/sp311-341-data-registry | SP-311, SP-341 | 資料分類登錄表 `backend/data_registry.py`：每張表、每類租戶檔案屬「使用者改的／匯入的／衍生的／機密」哪一類（含去識別化欄位），備份改讀它（行為不變）；`sync_state` 屬機密。`mmp_cache` 加 `version` 欄（程式改了，`get_run_ftp` 重算窗內舊列）；`pmc_cache` 表刪除；`power_source_v1.json` 帶程式版本 |
| 2026-10-08 | chore/drop-unused-lthr-import-metrics | owner request（「既然沒用到，放這幹嘛」） | 拿掉寫了沒人讀的資料：匯入不再算、不再寫 `workout_metrics`（hrTSS／rTSS／NP／TSS／高強度秒數）與 `mmp_cache`（只拿來算那些指標的 runFTP），`WorkoutMetric`／`MmpCache` model、`get_run_ftp`、`settings_on`、`backfill_hr_load.py`、`algorithms/mmp.py` 與 `metrics.py` 只餵它們的函式一併刪除（`backend/files/file_service.py:16`、`:187`）。COROS 登入不再寫 `athlete_settings.ftp_w`、回應不再帶 `ftp_w`／`lthr`（`backend/sync/coros_client.py:234-272`）；**LTHR 照寫**：它是冷啟動的 LTHR 先驗（`fitdataset._coros_lthr_prior`，`backend/engine/wko5expr/fitdataset.py:809`、`:937`），體重照用。不做破壞性遷移：舊 DB 的兩張表與 `ftp_w` 欄原樣留著、沒人讀寫（`data_registry.LEGACY_TABLES`，`backend/data_registry.py:150`；`unclassified_tables` 略過，`:319`）；新 DB 不再建它們。`compare_sources.py` 的同步列不再有 TSS（改看 `/api/v1/sync/compare`） |
| 2026-10-08 | perf/sp362-batch1 | SP-362 | 同步效能第一批。**失敗清單**：單一活動下載／匯入失敗（COROS、TP）寫進新表 `sync_failures`（`backend/sync/failures.py`；`SyncFailure`，`backend/db/models.py:407`；登錄表歸「匯入的」，`backend/data_registry.py:118`），cursor 照常推進；之後的同步只依 id 重試這幾筆（COROS `labelId`＋`sportType` 走 detail/download，`backend/sync/coros_client.py:928`；TP `workoutId`，`backend/sync/tp_client.py:870`），不必從很久以前重新列表（正式機原本每次 7 筆失敗 → 列 819 筆、41 頁、33–54 秒）。最多自動重試 5 次或 7 天（使用者 2026-10-08），之後停止、留在設定 › 進階設定，可按「重試」（`GET /sync/failed`、`POST /sync/failed/{id}/retry`）。沒有 FIT 的手動紀錄記成 `no_file`（COROS：detail/download 回 0000 但沒有 fileUrl，`CorosNoFile`，`backend/sync/coros_client.py:444`），不算失敗、不再問。整次失敗（登入、列表）或失敗清單寫不進去時 cursor 不推進（`backend/sync/coros_client.py:951`、`backend/sync/tp_client.py:897`）。刪除來源檔案時清空該來源的清單。**overlap**：COROS 增量列表從上次同步減 3 天改 14 天（`CURSOR_OVERLAP_DAYS`，`backend/sync/coros_client.py:33`；多天百岳沒帶手機晚上傳）；跑後自評的重試窗固定 4 天（`RETRY_DAYS`，`backend/sync/coros_client.py:503`），不跟著放大。**同步後順序**：圖表 Dataset → 總覽狀態 → 課表輸入 → 自動調整 → 每人校正／自動分類（最低，等自動調整結束；`backend/sync/runner.py:189`、`backend/api/wko5views.py:293`、`backend/engine/calibrate.py:348`、`plan_auto.wait_idle`／`busy`，`backend/engine/plan_auto.py:888`） |
| 2026-10-08 | perf/sp362-batch2-sync | SP-362 A5 | **跑後自評回填移出同步**：重試最近 4 天未讀／沒填的、8 週回填（最多 25 筆 detail、每筆間隔 0.4 秒，最久約 65 秒）不再佔住同步。手動 SSE 與 `run_once` 叫 `runner.stream(feel_in_background=True)`（`backend/api/sync.py:55`、`backend/sync/runner.py:356`），`sync_workouts(feel_passes=False)`（`backend/sync/coros_client.py:841`、`:989`）在 `complete` 帶 `feel_read`（runner 拿掉，不送前端）；結果存好、①～③ 之後 runner 開背景 job（`start_feel_job`／`_feel_job`，`backend/sync/runner.py:297`、`:307`、`:319`；`coros_client.feel_job`，`backend/sync/coros_client.py:696`，用存的 token、不登入）。job 用「會讓路」的方式拿 COROS 忙碌旗標（`hold_yielding`，`backend/sync/runner.py:160`）：`is_busy` 不算它（`:111`），之後的同步／檢查先請它停（`_make_way`，`:171`、`:203`），它在兩次讀取之間停（`fill_feel` 的 `should_stop`，`backend/sync/coros_client.py:583`；停下不算回填一輪，`:628`），最多等 30 秒（`YIELD_WAIT_S`）；刪除來源檔案也先請它讓路（review #3）。job 補到 ≥ 1 筆 → 等自動調整閒下來，再呼叫 `plan_auto.after_sync`／`calibrate.after_sync`（`rpe_filled`，SP-231 行為不變；`_after_feel`，`backend/sync/runner.py:319`）。測試 `backend/tests/test_sync_feel_job.py` |
| 2026-10-08 | perf/sp362-batch2-sync | SP-362 A3／A4 | **完整檢查**（`backend/sync/check.py`）：列出遠端全部活動（COROS 從 `FIRST_SYNC_DAY` 每一頁；TP 從 2010-01-01 每 90 天一次 date-range，未來的課表跳過），依 provider id 跟 DB 比對成三組：遠端有、本地沒有、不在失敗清單 → `missing`（「補下載」只下載這些，走同步自己的 `_fetch_one`，失敗照樣進失敗清單）；本地有、遠端沒有 → `local_only`，**只列出、不刪**（使用者 2026-10-08）；失敗清單 → `failed`（`compare`，`backend/sync/check.py:281`）。全部經 `runner.stream(client=…, remember=False)`（`backend/sync/runner.py:216`、`:250`）：同一個忙碌旗標（不跟同來源的同步並行；自評 job 會讓路）、有下載時跑同步後的 ①～③、不動 cursor、不寫 `last_result`／`last_ok`。結果存設定 `sync.<src>.check`（重新整理頁面還在；`backend/settings/repository.py:76`），跑的時候的進度在記憶體（`progress`）。**每週檢查**：排程 loop 啟動 10 分鐘後每分鐘看一次（`backend/sync/scheduler.py:116`），資料來源（`auto_plan`）上次每週檢查超過 7 天（失敗的隔 1 天）就列最近 60 天、比對，缺的直接下載（最多 30 筆），結果存 `sync.<src>.check_weekly`（`weekly_tick`，`backend/sync/check.py:522`）。COROS 列表回應有沒有總筆數沒有驗證過，所以**沒有做**逐月筆數比對。端點 `GET`／`POST /sync/check`、`POST /sync/check/{source}/fill`（`backend/api/sync.py:197`、`:205`、`:229`）；設定 › 資料同步 › 進階設定的按鈕與每來源一行結果（`loadCheck`，`backend/static/settings.html:1218`）。兩個新設定鍵不匯出（`EXPORT_EXCLUDE`，`backend/engine/debug_view.py:60`）。測試 `backend/tests/test_sync_check.py` |
| 2026-10-08 | perf/sp362-batch2-sync（review） | SP-362 review #1–#8 | **排程 loop 一啟動就死掉**：`import time` 被 `from datetime import time` 蓋掉，loop 第一行（try 外）丟 AttributeError，每日同步、自動備份、每週檢查全停；改 `import time as _time`（`backend/sync/scheduler.py:18`、`:116`），測試直接跑真的 loop 幾輪、確認三個 tick 都被呼叫。**每日同步不再因忙碌整天漏掉**：來源忙碌時不記 `sync.schedule.last_run`，下一分鐘再試；開始後才回 SYNC_BUSY 的也把當天的記號清掉（`_busy_retry`／`_unmark`，`backend/sync/scheduler.py:51`、`:65`、`:77`、`:87`）。**刪除來源檔案**先請自評 job 讓路（`backend/sync/purge.py:42`），不再在頁面顯示閒置時回 409。**完整檢查**：開始時就佔住來源（`runner.reserve`／`release`，`backend/sync/runner.py:121`、`:133`；`backend/sync/check.py:472`）；補下載跳過已變成 no_file／停止重試的 id、中斷時保留已處理的（`backend/sync/check.py:419`）；TP 排了沒做的課另外計數、不下載（`tp_done`，`backend/sync/check.py:185`，欄位名稱未驗證）；每週檢查只在自動同步開著時跑（`backend/sync/check.py:532`）。設定頁：中斷／失敗的結果顯示成失敗、`missing` 超過 500 筆時仍可補下載並提示再檢查、顯示「排了沒做 N」（`backend/static/settings.html:1233`） |
| 2026-10-08 | code-sync（SP-215, SP-67, SP-57） | N/A | 錨點全面重新對齊（10-04 之後 runner、coros_client、file_service、fitdataset、repository、wko5views 都長了）；同步流程圖的匯入不再寫「算指標」；新增「同步計時與 log」（SP-215）、COROS 門檻歷史（SP-67）；新增 Decisions Log（SP-57、`65bad11d`）與 Open Questions（SP-57 逐檔 RPE 換算等待實作的單） |
| 2026-10-08 | fix/backup-user-files-sp355 | SP-355 | **備份帶上使用者改的檔案、拿掉機密**：登錄表裡租戶的「使用者改的」檔案與上傳的 GPX 改成 `backup=ALWAYS`（`backend/data_registry.py:172`–`:198`），備份放進 `files/`、還原放回原位，備份裡沒有的檔案不動（`backend/engine/backup.py:243`、`:683`）；DB 快照清空機密表 `sync_state`、`debug_tokens` 並 VACUUM（`backend/engine/backup.py:158`），還原保留本機的機密表（`:592`、`:611`）；格式升為 2，格式 1 照樣能還原；`SEALED_DB_COLUMNS` 補上兩個封存密碼欄位（`backend/settings/secrets.py:48`）。設定頁備份／還原說明與結果訊息跟著改（`backend/static/settings.html:1604`、`:1617`）。見「備份（SP-355）」 |
| 2026-10-08 | fix/backup-user-files-sp355（安全審查） | SP-355 review H1, H2, M1–M4, L1, L2, L5, L6 | **快照不再碰雲端資料夾**：快照與拿掉機密改在 DB 旁的 `backups/` 做，雲端資料夾只出現做好的 zip；被中斷的殘留由 `clean_stale` 清（`backend/engine/backup.py:290`、`:470`）。**cursor 跟著資料走**：`sync_state` 不再整列清空，只把 `local_fields`（憑證、帳號身分）設 NULL；還原取本機登入、備份的 cursor，且不晚於備份時間（`backend/data_registry.py:142`、`backend/engine/backup.py:817`、`:871`）。**還原改成先驗證全部、合併後一次換掉**：`files/` 逐一驗證（路徑、上限、CRC）並解開，本機資料先併進那份 DB，再唯一一次 `backup()`，檔案預先放好再改名；DB 換過就一定跑 `after_restore`；pre-restore 列在還原清單（`backend/engine/backup.py:720`、`:888`、`:991`，`backend/api/backup.py:227`、`:281`）。**留在本機的資料**：debug token／呼叫紀錄／驗證失敗（IP）表、`backup.*`／`debug.*`／`plan.calendar` 設定不進備份、還原不蓋；分享連結備份但不還原（`backend/data_registry.py:73`、`:197`、`:341`）。上限（單檔 50 MB、合計 1 GB、10,000 個）、沙盒與別的帳號的備份拒絕（manifest 記 `tenant`）、金鑰遺失訊息說明登出重登可自救（`backend/settings/secrets.py:45`）。升級注意：舊格式備份仍含憑證，約 5 週後才會被保留規則刪掉 |
| 2026-10-08 | feat/rpe-per-level-sp57 | SP-57（使用者 2026-10-07） | **「負荷」RPE 換算改成每一檔各自換算**：單一係數 0.30 拿掉；每一檔預設＝TSS 定義 IF² × 100（輕鬆 55、稍累 67、累 77、很累 90、極限 121 TSS／小時；區間出自 Seiler & Kjerland 2006、Coggan 功率等級、跑步功率區，取值推估），每檔滿 3 筆自評活動才擬合、往預設收縮 w ＝ n ÷ (n + 10)，保序回歸確保高一檔不會比低一檔少；COROS 跑後自評（SP-231）照 1–5 → 五檔進同一個擬合（COROS FIT 檔本身沒有 RPE，2026-10-08 再查）；`rpe.load_model` 改存每一檔；設定頁一行列五檔、進階設定每人校正下多一張每檔的表（值、筆數、預設／本人） |
| 2026-10-08 | feat/rpe-per-level-sp57（review） | SP-57 code review | **存著的課跟著 RPE 換算走**：換算變了就重算今天起含 RPE 步驟的課的步驟 TSS 與整堂 `tss`（`sync_sessions`，重擬後與每次 COROS 推送前，設定 `rpe.load_stamp`），課表與手錶一致；沒有自己資料、只為順序被調整的檔標「依相鄰檔調整」；沒擬合的檔讀預設、存保序前的值讓讀取結果和存的一樣；沒有自評活動時清掉舊擬合；收縮與保序權重改用有效樣本數 n_eff（舊的自評算得少）；FIT RPE 6 歸「累」（每檔兩個值）；預設的說明改成通用規則（不是照哪個人調的） |

---

## Summary

實作 Coros 非官方 API 客戶端，讓使用者以 Coros Training Hub 帳密登入，自動下載 .fit 檔案到使用者資料夾的 `.wko5coach/fit/coros/{year}/`（`fit_root`，`backend/sync/storage.py:28`；路徑取自 tenant 的 shared 資料夾，`shared_path`，`backend/tenancy.py:167`），完全不依賴 WKO5 資料夾或 TrainingPeaks 帳號。下載完成後觸發 FIT 解析、指標計算、TSS/PMC 流程。

**已驗證（2026-05-15）**：1088 筆活動匯入成功，PMC 圖表（CTL/ATL/TSB）正常顯示。

---

## Coros 非官方 API（逆向工程已驗證，2026-05-15）

### 社群參考實作

| 專案 | 語言 | 功能 |
|------|------|------|
| `xballoy/coros-api` | TypeScript/NestJS | Bulk FIT export，有日期篩選；`/activity/detail/download` endpoint |
| `cygnusb/coros-mcp` | Python | MCP server，sleep/HRV/activity；`yfheader` 要求已記錄 |
| `CuberL/coros-mcp` | Python | MCP server |
| `rowlando/coros-workout-mcp` | Python | MCP，Claude Desktop 整合 |

### 認證流程（已驗證）

```
POST https://teameuapi.coros.com/account/login
Content-Type: application/json
User-Agent: Mozilla/5.0 ...Chrome/145.0.0.0 Safari/537.36

{
  "account":     "<email>",
  "pwd":         "<md5(password)>",   // 欄位名是 pwd，不是 passwd
  "accountType": 2                    // 2 = email login
}
```

**重要修正（vs 社群部分舊文件）**：
- 欄位名：`"pwd"`（不是 `"passwd"`）
- User-Agent header 必須帶（Chrome UA）

**成功回應**:
```json
{
  "result": "0000",           // 成功判斷用 result=="0000"，不是 apiCode=="1"
  "apiCode": "...",
  "message": "OK",
  "data": {
    "userId":      "<user_id>",
    "accessToken": "<token>",           // 在 data.accessToken，不是 result.accessToken
    "zoneData": {
      "ftp":  230,    // 單位 W，從 Coros profile 自動帶入
      "lthr": 178,    // 心率閾值
      "rhr":  50
    },
    "weight": 70,
    "maxHr":  190,
    "criticalPower": 220
  }
}
```

Token TTL：24 小時（登入回應不含 tokenExpiry，固定 +24h）。

### Region 偵測（關鍵）

Coros 有多個 region server，登入成功的 server 不一定是活動資料的 server：

| Region | Login URL | Data URL |
|--------|-----------|----------|
| EU | `teameuapi.coros.com` | `teamapi.coros.com`（US/global） |
| US | `teamapi.coros.com` | `teamapi.coros.com` |
| CN/Asia | `teamcnapi.coros.com` | `teamcnapi.coros.com` |

**台灣帳號實測**：EU server 登入成功，但 token 對資料 API 有效的是 `teamapi.coros.com`（US server）。

**偵測策略**：登入成功後，依序對「上次偵測到的 server（同一 userId）→ US → 登入 server → 其餘」發一次 `/activity/query` 測試請求，找第一個回傳 `result=="0000"` 的 server，儲存為 `coros_base_url`。全部失敗（逾時、網路）時沿用上次的，否則 US，並在 log 記下每個 server 的原因。登入本身只做一次：一個 region 回 `0000` 之後就不再對其他 region 登入（再登入會讓前一個 token 失效）。

**記住密碼 / 自動重新登入**（2026-10-01）：見 `docs/secrets-and-keys.md`。活動列表或 Training Hub 回 result 1019／1030（Access token is invalid）、或 token 過期時，有存密碼就自動登入一次、重試一次（`relogin`，`backend/sync/coros_client.py:365`）；同步事件流會多一筆 `{"status": "relogin"}`。

**登入有效性檢查**（2026-10-03，`backend/sync/session_check.py`）：存著 token 不代表還登入著（COROS token 約 24 h 失效，或帳號在別處登入就失效）。`check()`（`backend/sync/session_check.py:98`）對每個來源最多每 `CHECK_TTL_S`（300 s）打一次便宜的認證呼叫（COROS：`activity/query` size=1，`probe_token`，`backend/sync/coros_client.py:102`；TP：`users/v3/user`），結果放記憶體快取；連不上伺服器 = `unknown`，頁面仍顯示已登入、60 s 後再查。任何 COROS／TP 呼叫拿到「需重新登入」就 `mark_expired()`（`backend/sync/session_check.py:60`），狀態立即翻成「登入已過期」；有記住密碼時先走自動重新登入，失敗或沒存密碼才算過期。`runner.logged_in`（`backend/sync/runner.py:256`）只讀快取判斷，所以過期的來源不會被自動同步。總覽／課表頁的橫幅 `session_banner.js` 讀 `GET /api/v1/auth/session-alerts`（`backend/api/auth.py:164`），只列「正在用」的登入（資料來源的同步、COROS 課表推送）。

**登入過期／無法同步時一定看得到**（2026-10-05，SP-88）。原因（假 COROS 重現）：(1) 橫幅只在頁面載入時查一次，比開網站自動同步早 1.5 s，自動同步撞到 1019 後頁面不會再讀；(2) 同步撞到 1019 只 `mark_expired`（記憶體，5 分鐘），之後重查若連不上 COROS（`unknown`）就又顯示已登入、橫幅消失、`/sync/primary` 回 `logged_in: true`；(3) 失敗的同步也寫 `last_result.at`，被 `last_sync_at` 算成上次同步：自動同步認為「已新鮮」好幾小時，`autosync.js` 還顯示「已同步 COROS +0」。現在：
- `check()` 遇到 `unknown` 而上一個答案是過期時仍回過期（`UNKNOWN_TTL_S` 後再查），只有登入、檢查 ok 或登出才清掉（`backend/sync/session_check.py:120`）；同步撞到 1019 且自動重新登入不成時，也把 `coros_token_expires` 設成現在（`backend/sync/coros_client.py:876`），重啟後不用連線就知道過期。
- `runner.last_sync_at` 只算 cursor 與 ok／partial 的同步（`backend/sync/runner.py:267`）；成功的同步另存 `sync.<src>.last_ok`（`{at, trigger, downloaded}`，失敗不覆蓋；刪檔時清掉）。
- `GET /auth/session-alerts` 多回 `sync`：`source`、`login`、`enabled`、`problem`（`expired`；`logged_out` 只在曾經同步過時；`failed` = 最近一次同步失敗，認證失敗在登入檢查回 ok 後就不算；同步關閉時一律 `null`）、`last_ok_at`、`last_run`；`expired` 的每一項加 `needs`（`sync`／`push`）（`backend/api/auth.py:184`）。
- `session_banner.js`：資料來源有問題時在 `<main>` 最上方顯示紅框卡片（標題、「重新登入」或「查看同步設定」連結、上次成功同步、最近一次失敗與錯誤碼），課表推送的過期另列一行；文字在 `common.session.*`（zh-TW／en）。`autosync.js` 同步結束後呼叫 `WKO5SessionBanner.refresh()`，失敗時徽章顯示「同步失敗：COROS」不自動消失；分頁回到前景（距上次 ≥ 60 s）也重讀。

**COROS 心率設定**（2026-10-03）：登入回應與每次同步結束後的 `GET /account/query`（唯讀，`refresh_hr_profile`，`backend/sync/coros_client.py:308`）解析出最大心率、安靜心率與三組 COROS 區間表（`zoneData.lthrZone` / `rhrZone` / `maxHrZone`），存在設定 `athlete.coros_profile`（`store_hr_profile`，`backend/sync/coros_client.py:287`；`ACCOUNT_KEY`，`backend/engine/hr_profile.py:59`）。每次值有變也記一筆到 `athlete.coros_profile_history`（`history_add`，`backend/engine/coros_compare.py:69`；呼叫在 `backend/sync/coros_client.py:302-304`），因為 COROS 曾把帳號 LTHR 從 182 改成 152，app 這邊沒有任何紀錄（SP-67）。失敗不影響登入或同步。讀取端（最大／安靜心率解析、圖表與課表的心率區間）在 `backend/engine/hr_profile.py`，不屬本規格。

### 所有 API 呼叫的必要 Headers

```
accessToken: <token>
yfheader: {"userId": "<user_id>"}    // 缺少此 header 會返回 "Access token is invalid"
User-Agent: Mozilla/5.0 ...Chrome/145.0.0.0 Safari/537.36
```

### 活動列表（已驗證）

```
GET https://teamapi.coros.com/activity/query
    ?size=20&pageNumber=1&startDay=YYYYMMDD&endDay=YYYYMMDD
Headers: accessToken + yfheader + User-Agent
```

**回應結構**（關鍵欄位）:
```json
{
  "result": "0000",
  "data": {
    "dataList": [               // 欄位在 data.dataList，不是 result.dataList
      {
        "labelId":   "<label_id>",           // unique activity ID
        "name":      "<城市> 跑步",
        "sportType": 100,        // 100=run（已對 FIT 驗證）, 102=trail run, 104=hike, 105=mountaineering, 200=cycling, 402=strength, 9904=custom（backend/sync/coros_sport.py:23）
        "date":      20260514,   // YYYYMMDD 8位整數，不是 Unix timestamp
        "startTime": 1747282306, // Unix timestamp（不用於日期解析）
        "totalTime": 4691,       // seconds
        "trainingLoad": 87,      // COROS 的訓練負荷 TL（多數活動有；SP-37 探測）→ workout_files.coros_training_load
        "fitUrl":    null        // 多數活動為 null，需用 detail/download
      }
    ]
  }
}
```

**重要修正**：`date` 欄位是 `YYYYMMDD` 8位整數（如 `20260514`），`startTime` 是 Unix timestamp，日期解析應使用 `date` 欄位。

### FIT 檔案下載（已驗證）

**正確 endpoint**（`xballoy/coros-api` 確認）：
```
POST https://teamapi.coros.com/activity/detail/download
    ?labelId=<id>&sportType=<type>&fileType=4
Headers: accessToken + yfheader + User-Agent
```

- `fileType=4` = FIT（0=csv, 1=gpx, 2=kml, 3=tcx, 4=fit）

**錯誤的 endpoint**（舊文件，返回 500）：
- `GET /activity/fit/url` — 此 endpoint 對本帳號無效，返回 Internal Server Error

**回應**：
```json
{
  "result": "0000",
  "data": { "fileUrl": "<presigned_s3_url>" }
}
```

接著 GET presigned URL 取得原始 .fit bytes。

若活動有 `fitUrl` 欄位（非 null），可直接 GET 下載，優先於 `detail/download`。

### Token 刷新
Token 約 24h 過期，需重新 POST `/account/login`。無 refresh token 流程。

---

## Scope

### In Scope
- Coros 帳密登入（MD5 password，`pwd` 欄位）
- Region 自動偵測（EU/US/CN 依序測試）
- 活動列表分頁拉取（含日期篩選）
- .fit 檔案下載到使用者資料夾的 `.wko5coach/fit/coros/{year}/`
- 新活動自動觸發 FIT 解析與匯入（`workout_files` 一列；TSS 等指標由圖表的 FIT Dataset 計算，匯入不存，2026-10-08）
- 重複活動跳過（依 `coros_activity_id` 去重）
- SSE 串流同步進度
- Token 持久化到 DB（`sync_state` 表，`secrets.seal` 加密）
- LTHR／weight 從登入回應自動匯入 `athlete_settings`（帳號 FTP 不存，2026-10-08）；心率設定存 `athlete.coros_profile`
- 登入有效性檢查與「登入已過期」提示
- 「資料來源」二選一（COROS 或 TrainingPeaks，`sync.primary_source`）：只同步、只讀所選來源

### Out of Scope
- WKO5 資料夾讀寫（完全獨立；WKO5 只當對照來源）
- 兩個來源合併或互補（2026-10-02 的合併設計已於同日撤回，見 Change History）
- demo 模式的同步（`build_app(demo=True)` 不掛 sync / auth / scan 路由，`backend/main.py:176`）
- 心跳同步 / WebSocket push
- Coros Training Plans / Structured Workouts 解析（反方向的「把本專案課表推送到 COROS」已實作，見下方指標）
- 多運動員帳號切換

---

## System Context

### Actors

| Actor | Type | Interaction |
|-------|------|-------------|
| 個人運動員 | Human — Browser | 觸發 sync、查看 PMC、設定 FTP |
| Coros Training Hub API | External Service | 提供活動列表 + FIT 下載 URL |
| 本機 FileSystem | Storage | 使用者資料夾的 `.wko5coach/fit/<source>/` 儲存 .fit |

### External Dependencies

| Dependency | Purpose | Failure Mode |
|------------|---------|--------------|
| `teameuapi.coros.com` | 身份驗證 | 降級：手動匯入 .fit |
| `teamapi.coros.com` | 活動列表 + FIT URL | 重試，記錄失敗，其他活動繼續 |
| Coros S3 presigned URL | FIT 檔案下載 | 失敗記錄 error event，繼續下一筆 |
| 現有 `file_service.py` | FIT 解析 + DB 匯入 | 同步失敗，不影響已存 |

---

## Architecture

### 元件結構

```
backend/
├── sync/
│   ├── tp_client.py            # TrainingPeaks 客戶端（網站登入 / WKO5-client OAuth）
│   ├── coros_client.py         # Coros API 客戶端（登入、region 偵測、記住密碼、同步、心率設定）
│   ├── coros_sport.py          # COROS 檔名的運動字（FIT session sport 優先，sportType 後備）
│   ├── runner.py               # 共用 runner：每來源互斥鎖、last_result、同步後掛鉤、auto_plan
│   ├── scheduler.py            # 每日排程（lifespan task）
│   ├── primary.py              # 資料來源（COROS／TP 二選一）與舊設定遷移
│   ├── dedup.py                # 跨來源去重（duplicate_of）、in_use_clause
│   ├── purge.py                # 刪除單一來源檔案、來源統計
│   ├── storage.py              # fit/<source>/ 路徑、confined()
│   ├── session_check.py        # 登入有效性檢查（2026-10-03）
│   ├── http.py                 # 共用 httpx client（測試可換 MockTransport）
│   ├── coros_workouts.py       # 課表推送到 COROS（見 overview.spec.md）
│   └── workout_targets/        # 課表推送 provider 介面
├── api/
│   ├── auth.py                 # /auth/coros/*、/auth/tp/*、/auth/session-alerts
│   └── sync.py                 # /sync/*（SSE 同步、設定、來源、比對、刪除）
├── db/
│   ├── models.py               # 含 coros_* 欄位（已實作）
│   └── database.py             # _migrate_schema() 自動 ALTER TABLE（已實作）
└── files/
    └── file_service.py         # _import_one_file(coros_activity_id=...)（已更新）


backend/static/                 # 同步 UI 實際所在（設定頁等靜態頁）
├── settings.html               # 設定 →「資料同步」：資料來源切換、登入、同步、狀態
├── autosync.js                 # 開網站自動同步（shell.js 載入）
├── session_banner.js           # 「登入已過期」橫幅（總覽、課表）
└── compare.html                # 資料來源比對
```

### FIT 儲存路徑

```
<home>/.wko5coach/
├── wko5coach.db           # SQLite DB
└── fit/                   # backend/sync/storage.py（2026-09-30 起每個來源分開）
    ├── coros/{year}/{coros_id}_{YYYY-MM-DD}_{sport}.fit
    └── tp/{year}/tp_{YYYY_MM_DD}_{workout_id}.fit
```

舊位置是 `.wko5coach/fits/{athlete}/…`（COROS）和 `.wko5coach/fit/athlete_1/…`（TP），都在使用者資料夾底下。用 `python -m backend.scripts.migrate_fit_folders` 遷移：預設 dry run，加 `--apply` 才執行，可重複執行。它會搬移檔案、更新 DB 路徑、刪掉清空的舊資料夾。2026-09-30 在這台機器上實際執行：COROS 17 個、TP 17 個。

所有刪除都經過 `storage.confined()`：路徑先 resolve、拒絕 symlink，超出 `fit/<source>/` 一律拒絕。

### 資料夾掃描（`scan_and_import`）

`scan_and_import`（`backend/files/file_service.py:105`）對 athlete 的 `data_dir` 掃描（原本的 `POST /api/v1/scan` 端點只有 React SPA 在用，2026-10-04 隨 SPA 刪除；現在由 demo 建置等內部流程直接呼叫）：

- `discover_tagged_files`（`backend/files/file_service.py:67`）：資料夾底下若有 `storage.SOURCES`（`backend/sync/storage.py:20`）列的 `coros/`、`tp/` 子資料夾，就逐一走 `<source>/<year>/`，檔案標上 DB source（`coros` / `trainingpeaks`）；同一資料夾的傳統 `<year>/*.wko4|.fit` 版面照舊標 `local`。symlink 跳過。
- `_sync_ids`（`backend/files/file_service.py:50`）：從同步寫出的檔名反推 provider id——COROS `<labelId>_<日期>_<sport>.fit` → `coros_activity_id`，TP `tp_<日期>_<workoutId>.fit` → `tp_workout_id`。`source` 收 DB 名稱（`coros` / `trainingpeaks`）也收資料夾 / API 名稱（`tp`），經 `storage.SOURCES` 對應。
- `_already_imported`（`backend/files/file_service.py:82`）：路徑（原樣與 resolve 後）、同一 athlete 的 `coros_activity_id` 或 `tp_workout_id` 任一已在 DB 就跳過（兩種 provider id 都限定 athlete，`backend/files/file_service.py:93`、`backend/files/file_service.py:98`），所以同步已記錄的檔案不會被掃描重複匯入；可重複執行。
- 每個檔案一個 savepoint（`backend/files/file_service.py:124`），失敗不留半筆資料；回傳 `new` / `skipped` / `errors` / `total` / `new_by_source`。

測試：`backend/tests/test_scan_and_tz.py:31`（per-source 版面、冪等）、`backend/tests/test_scan_and_tz.py:47`（跳過同步已記錄者）、`backend/tests/test_scan_and_tz.py:62`（provider id 去重限定 athlete）、`backend/tests/test_scan_and_tz.py:87`（傳統版面仍為 local）。

### 同步流程

```
POST /api/v1/sync/coros/start?since=YYYY-MM-DD
  ↓
_get_token_and_base(db) → (token, base_url, user_id)
  ↓
_list_page(token, base, user_id, since, end, page) → activities[]
  ↓
for each activity:
  if coros_activity_id in DB → 列表的 trainingLoad 補進 coros_training_load（有變才寫）→ SSE: skipped
  if 在失敗清單且不再自動重試（no_file，或已停止）→ SSE: skipped（reason no_file / retry_stopped）
  _fetch_one（backend/sync/coros_client.py:713）→ _download_fit(token, base, user_id, activity):
    1. try fitUrl (presigned S3, if present)
    2. POST /activity/detail/download?labelId=...&sportType=...&fileType=4
    3. GET presigned URL → bytes
  save to <home>/.wko5coach/fit/coros/{year}/{labelId}_{date}_{sport}.fit
    (sport = coros_sport.sport_token：FIT session sport/sub_sport，讀不到才用 sportType)
  _import_one_file(db, athlete_id, dest, source="coros", coros_activity_id=id)
    → parse FIT, write workout_files（指標不存：TSS 等由圖表的 FIT Dataset 算，2026-10-08）
    → 解析失敗：record_corrupt stub（不再重複下載）
    → 下載／匯入失敗：寫進失敗清單（failures.record_failure，次數 +1）；
      detail/download 回 0000 但沒有 fileUrl（手動紀錄）：第一次算一般失敗（可重試），連續第二次才記成
      no_file（CorosNoFile、record_no_file_answer，backend/sync/failures.py:123；不算錯誤，列表 overlap 之後跳過）
    → 成功：從失敗清單移除
  SSE: downloaded / no_file / error
  ↓
失敗清單裡該重試、這次列表沒碰到的：依 labelId＋sportType 直接重下載（不重新列表；SSE checking 帶 retry: true）
  ↓
更新 sync_state.coros_last_sync_at（增量 cursor，下次從這天減 14 天 overlap 開始）——
  單筆失敗不再擋 cursor；只有整次失敗（登入、列表）提早結束、或失敗清單寫不進去時不推進
refresh_hr_profile（GET /account/query，失敗不影響）
SSE: complete {total_downloaded, total_checked, errors}
  （手動 SSE／run_once：跑後自評的重試與 8 週回填不在這裡跑，complete 帶 feel_read 給 runner；SP-362 A5）
  ↓（runner.stream finally）
寫 sync.<src>.last_result → 有新檔時 localtime.refresh_from_fits
→ ① 有新檔時 wko5views.warm_up（背景：圖表 Dataset → 總覽狀態 → 課表輸入）
→ ② plan_auto.after_sync（自動調整，與暖機共用同一份計算）
→ ③ calibrate.after_sync（每人校正，含 COROS TL 換算重擬；等 ② 結束才開始）
→ ④ COROS：start_feel_job（背景，會讓路地拿著忙碌旗標；補到 ≥ 1 筆自評 → 再呼叫 ②③ 帶 rpe_filled）
  （SP-362，backend/sync/runner.py:241-304）
```

#### 完整檢查與每週檢查（SP-362 A3／A4，`backend/sync/check.py`）

同步只從 cursor 往後列（COROS 減 14 天 overlap，TP 用 workouts/changed），落在 cursor 前面的活動永遠不會被抓到；這兩個檢查把遠端清單跟 DB 依 provider id（COROS `labelId`、TP `workoutId`）比對：

| 組 | 條件 | 處理 |
|---|---|---|
| `missing` | 遠端有、DB 沒有、不在失敗清單 | 完整檢查：列出，按「補下載」只下載這些（`POST /sync/check/{src}/fill`，可帶 `ids`）；每週檢查：直接下載（最多 `WEEKLY_FETCH_MAX` 30 筆） |
| `local_only` | DB 有、遠端沒有（只看清單的日期範圍，頭尾兩天不算） | **只列出，不刪**（使用者 2026-10-08） |
| `failed` | 該來源在失敗清單裡的列 | 列出；重試照失敗清單的規則 |

- **模式**：`full`（設定頁按鈕；COROS 從 `FIRST_SYNC_DAY` 起每一頁，約 41 頁、40～60 秒；TP 從 2010-01-01 起每 90 天一次）、`fill`（補下載上一次完整檢查的 `missing`）、`weekly`（最近 `WEEKLY_DAYS` 60 天，約 3 頁 COROS）。清單跟同步一樣列到伺服器的今天（COROS 的 endDay 給未來日期沒驗證過）；本地日期是今天或之後的列不算 `local_only`。
- **跟同步的關係**：都經 `runner.stream(client=check_stream, remember=False)`：同一個忙碌旗標（同來源的同步、刪除、另一個檢查都不能同時跑；自評 job 會讓路），下載走同步的 `_fetch_one`（同樣的檔名、失敗清單、新活動的自評），有下載時跑同步後的 ①～③；**不動 cursor**，也不寫 `last_result`／`last_ok`（不算一次同步，不影響開網站自動同步的新鮮度）。
- **結果**：`sync.coros.check`／`sync.trainingpeaks.check`（完整檢查與補下載：`{mode, at, status, error, since, until, remote, local, pages, missing[], missing_n, local_only[], local_only_n, failed[], failed_n, secs, filled{at, status, downloaded, errors}}`，清單最多 500／200 筆，筆數是全部）；`sync.<src>.check_weekly`（每週檢查，同樣欄位加 `fetched`、`fetch_errors`）。存在設定裡，重新整理頁面還在；跑的時候的進度（`phase` list／fetch、`pages`、`listed`、`done`／`total`）只在記憶體（`GET /sync/check` 的 `progress`）。
- **每週**：排程 loop（`backend/sync/scheduler.py`）啟動滿 `STARTUP_DELAY_S`（10 分鐘，避開啟動暖機）後每分鐘呼叫 `weekly_tick`：只檢查自動同步會跑的來源（資料來源、啟用、已登入、沒在忙），上次每週檢查超過 7 天（失敗或中斷的隔 1 天）才跑。**沒有自己的開關**（使用者偏好自動、不加選項）：自動同步關掉時（「開啟網站時自動同步」沒勾、也沒設每日同步時間）每週檢查也不跑（`backend/sync/check.py:532`）。
- **TP 排了沒做的課**：date-range 清單也會列出過去排了但沒做的課。有實際資料（`totalTime`、`distance`、`startTime`、`tssActual` 等任一有值）算做過；只有計畫欄位（`totalTimePlanned` 等）或實際欄位都空 → `planned`／`planned_n`，另外計數，不算 `missing`、不給補下載、每週檢查不下載（不會打 details、也不會在失敗清單留假的 no_file）；兩種欄位都沒有的項目照舊當做過（`tp_done`，`backend/sync/check.py:185`）。**這些欄位名稱是 TP workout 物件的慣用名稱，清單端點實際帶哪些沒驗證過**。
- **開始時就佔住來源**：`check.start` 先 `runner.reserve`（`backend/sync/runner.py:121`），同一輪的第二個檢查、或在 task 跑起來前按的同步都會看到忙碌（409）；結束（含還沒跑就取消）時 `release`。
- **過期的清單**：補下載前重讀失敗清單，已經是 `no_file` 或停止自動重試的 id 跳過（交給失敗清單的「重試」），不會被改回 failed；中途中斷時已處理的照樣從 `missing` 拿掉（`_fetch_all`，`backend/sync/check.py:419`）。`missing` 最多存 500 筆：`missing_n` 比較多時補下載鈕照樣出現，並提示補完再檢查一次。
- **沒做**：逐月筆數比對。COROS 列表回應有沒有總筆數（`data` 裡除了 `dataList` 的欄位）在程式、測試、探測腳本裡都沒有記錄，沒驗證過就不依賴。
- **設定頁**：設定 › 資料同步 › 進階設定的「完整檢查（找漏掉的活動）」一次檢查所有可以同步的來源；每來源一行：時間、平台／這裡筆數、缺 N（＋補下載）、只在這裡 N、失敗清單 N、排了沒做 N（id 與日期在 hover）、上次補下載、「上次檢查：…，補了 N 筆」；說明在 ? 的 hover。跑的時候每 2 秒更新進度。狀態不是 ok／partial 的結果（中斷、失敗）一律顯示成失敗與原因（`loadCheck`，`backend/static/settings.html:1233`）。
- 測試：`backend/tests/test_sync_check.py`（假的分頁 COROS、TP；記憶體 DB）。

#### COROS Training Load（SP-37／SP-38，2026-10-04）

- **儲存**：列表項目的 `trainingLoad`（`list_training_load`，`backend/sync/coros_client.py:686`）在匯入時寫進新活動，已匯入的在下次列表掃到時補上或更新（`backend/sync/coros_client.py:907-908`）；只用同步本來就抓的列表，不多打任何 COROS API。欄位 `workout_files.coros_training_load`（`backend/db/models.py:79`，加法遷移 `backend/db/database.py:130`）。
- **重擬**：同步有新活動、或只補了已匯入活動的 TL（`complete` 事件的 `tl_filled`，經 `runner` 的 `last_result`）都會觸發每人校正；`calibrate.calibrate` 跑完 Item 後呼叫 `coros_tl.refit_and_store`（`backend/engine/calibrate.py:264`，`backend/engine/coros_tl.py:755`）：有 TL 的 COROS 活動（以檔名的 labelId 對到圖表 Dataset）× app 的 TSS → 三組（功率 TSS、hrTSS 依 IF、hrTSS 比例，`group_samples`，`backend/engine/coros_tl.py:441`）。
  - 只用最後一次門檻（FTP／LTHR）變動 > 5 % 之後的活動（`since_threshold_change`，`backend/engine/coros_tl.py:464`），近期權重較高（半衰期 120 天，`recency`，`backend/engine/coros_tl.py:478`）。
  - 模型族用 LOO MAE 選；樣本 < 60 時只比 A（比例）／C（冪次），避免二次式在小樣本爆掉（`choose_family`，`backend/engine/coros_tl.py:489`）。
  - 收縮：換算 = w·本人 + (1 − w)·預設，w = n ÷ (n + 30)（`SHRINK_K`，`backend/engine/coros_tl.py:84`）；預設只是先驗（推估）。
  - 時間序回測：最近 30 天當 holdout（`HOLDOUT_DAYS`，`backend/engine/coros_tl.py:90`），新擬合在 holdout 上的 MAE 不比目前存的差才換上；存回測與 LOO 誤差（`refit_group`，`backend/engine/coros_tl.py:516`）。結果存設定 `coros.tl_model`。
  - 實跑校正：推上 COROS 的「負荷」步驟（計畫 TSS、送出的 TL、強度、當時的係數）記在 `coros.tl_load_calib`；那堂課完成且活動的圈數＝推送的步驟數時，那一圈累積的 TSS ÷「未校正模型對送出 TL 的 TSS」是一個樣本（沒有時用 計畫 TSS ÷ 推送時的係數；照計畫跑完不會把係數拉回 1），收縮後（w = n ÷ (n + 5)）的係數在換算前除掉（`refresh_load`／`load_factor`，`backend/engine/coros_tl.py:650`、`backend/engine/coros_tl.py:674`）。
  - 重推門檻：重新擬合讓某個「負荷」步驟的 TL 變動 < 3（`TL_RESEND_MIN`，推估）時，推送沿用上次送出的 TL（同一計畫 TSS／依據／強度，從 `coros.tl_load_calib` 的紀錄讀，`_sent_tl`，`backend/sync/coros_workouts.py:659`；`sent_tl`，`backend/engine/workout_steps.py:1117`），指紋不變、不標「需更新」；≥ 3 才換新值重推（SP-38）。
- **顯示**：`GET /sync/settings` 回 `coros_tl`（`describe`，`backend/api/sync.py:297`），設定頁「課表推送到」下方列出每組的模型、n、權重、回測誤差（推估）。

#### 「負荷」用 RPE 填：每一檔的每小時 TSS（SP-57，2026-10-05；逐檔 2026-10-08）

- **用途**：課表「負荷」步驟可用 RPE 五檔＋分鐘填（overview.spec.md），TSS ＝ 這一檔的每小時 TSS × 時數。**只用在排課目標**：PMC、總覽與護欄的負荷一律是手錶記錄，RPE 不修正任何活動的 TSS（使用者 2026-10-04）。2026-10-07 起每一檔各自換算（單一 Foster 係數 × CR-10 在輕鬆偏低、很累偏高）。
- **預設是通用規則，不是照哪個人調的**：TSS 的定義，每小時 TSS ＝ IF² × 100，IF 是這一檔的強度（`DEFAULT_IF`／`DEFAULT_TSS_H`，`backend/engine/rpe_load.py:96`、`:112`）：輕鬆 0.74 → 55、稍累 0.82 → 67、累 0.88 → 77、很累 0.95 → 90、極限 1.10 → 121。範圍有出處：Seiler & Kjerland 2006 用 session RPE 分三區（≤ 4 在 VT1 以下、5–6 在 VT1–VT2、≥ 7 在 VT2 以上）、Coggan 功率等級附的 CR-10 RPE（L2 56–75 % RPE 2–3、L3 76–90 % RPE 3–4、L4 91–105 % RPE 4–5、L5 106–120 % RPE 6–7）、跑步功率區（輕鬆 65–80 % CP、中等 80–90 %）；範圍裡取哪一點是**推估**。例：很累——session-RPE 評整段主課；7 分是第三區的起點（約 CP），整段 20–60 分鐘撐不住 106–120 % FTP，所以取 CP 略下的 0.95（2026-10-08 確認）。每一檔的理由在 `IF_SRC`（`backend/engine/rpe_load.py:105`），設定頁的標籤滑過去看得到。
- **個人化只來自每個人自己有自評的活動**。重擬與 TL 換算同一個觸發；`calibrate.calibrate` 在 TL 之後呼叫 `rpe_load.refit_and_store`（`backend/engine/calibrate.py:273`，`backend/engine/rpe_load.py:532`），自己的 try，失敗不影響其他校正。樣本＝圖表 Dataset 的活動中有自評的（`workout_files.rpe`，經 `activity_tags.load_recorded`／`recorded_of`，與自動努力程度用的是同一個 RPE；`activity_rows`，`backend/engine/rpe_load.py:506`）：FIT 自己的 RPE（Garmin 檔）或 COROS 跑後自評（SP-231：1–5 → 2／4／5／7／10，正好是五檔；**COROS 的 FIT 檔沒有 RPE**，2026-10-08 再查 2026-09～10 的 FIT、有自評的也一樣，自評來自活動明細，`backend/engine/coros_rpe.py`）。FIT 的 1–10 每檔兩個值：1–2 輕鬆、3–4 稍累、5–6 累（Seiler 二區是 session RPE 5–6）、7–8 很累、9–10 極限，小數四捨五入（`FIT_LEVEL`／`level_of_rpe`，`backend/engine/rpe_load.py:121`、`:135`）；值 ＝ 活動 TSS ÷ 移動小時；10 分鐘～20 小時、每小時 TSS 在 10–250 之外的丟掉（`samples`，`:280`，推估）。
  - 和 TL 一樣只用最後一次門檻變動 > 5 % 之後的活動、近期加權（半衰期 120 天；共用 `coros_tl.since_threshold_change`／`recency`）。
  - 每一檔：本人值 ＝ exp(加權平均 ln 每小時 TSS)；在 log 空間收縮到這一檔的預設，w ＝ n_eff ÷ (n_eff + 10)，n_eff ＝ 近期權重的總和（有效樣本數：舊的自評算得少；`SHRINK_K`，`backend/engine/rpe_load.py:116`）；這一檔不到 3 筆（`MIN_N`，`:117`，推估，算筆數）維持預設（`_fit`，`:295`）。
  - 順序：高一檔不會比低一檔少——收縮後的值做加權保序回歸（log 空間、相鄰違反就合併，權重 n_eff ＋ 10：預設當 10 筆；`monotone`／`_ordered`，`backend/engine/rpe_load.py:144`、`:164`），被合併的檔標 `adjusted`。存的值留著每個有擬合的檔保序前的值（`shrunk`）和 n_eff，讀取時用同樣的保序重算，結果和存的一樣；沒有自己擬合的檔一律讀預設（不讀存的副本）。
  - 沒有任何留下來的自評活動（完全沒有、或門檻大改後都不算）：清掉存的擬合，回到預設。
  - 誤差：留一（含收縮與保序）預測每筆活動的 TSS 的 MAPE／MAE／偏差（`loo`／`refit`，`backend/engine/rpe_load.py:313`、`:327`）。結果存設定 `rpe.load_model`（`{levels: {easy…max: {tss_h, shrunk, n, n_eff, w, personal, adjusted}}, n, loo, fitted_at}`；舊的單一係數 `{factor…}` 讀成預設）。
- **存著的課跟著換算走**：換算變了（第一次推送或重擬時，以及之後每次重擬改到值），`sync_sessions`（`backend/engine/rpe_load.py:456`）把今天起、還有效、含 RPE「負荷」步驟的課重算：步驟的 TSS 和整堂課的 `tss`（只加差額，其他步驟的估算不動），不寫課表變更紀錄，結構（檔、分鐘）照使用者存的。設定 `rpe.load_stamp` 記上次用的換算，一樣就不做事。重擬後（`refit_and_store`）和每次 COROS 推送前（`_rpe_sessions_follow`，`backend/sync/coros_workouts.py:1160`）都會跑，所以課表、週 TSS、護欄和手錶拿到的一致；推送因為步驟 TSS 變了會重送一次（`sent_key` 含步驟 TSS，`TL_RESEND_MIN` 只在 TSS 不變時擋小幅 TL 變動）。
- **顯示**：`GET /sync/settings` 回 `rpe_load`（`describe`，`backend/engine/rpe_load.py:385`；`backend/api/sync.py:361`）：每一檔 {tss_h, if, n, source: fitted／adjusted／default, chip}。設定頁在 TL 換算下方一行列出五檔的每小時 TSS 與「本人 n、留一誤差」或「預設是通用規則 ±30 %」；進階設定「自動估算的參數」（每人校正）下面多一張表，每一檔一列：每小時 TSS、IF、「本人 n=…」／「預設（推估）」／「依相鄰檔調整」（沒有自己的資料、只為了順序被調整）標籤（滑過或點一下看來源）、筆數；表的標題滑過去說明預設是通用規則、個人值只來自自己的自評（`showRpe`，`backend/static/settings.html:1085`；表 `#calib-rpe`，`:507`）。「現在重新估算」之後跟著更新。

主流程在 `sync_workouts`（`backend/sync/coros_client.py:812`）。所有同步入口（手動 SSE、`/sync/auto`、每日排程）都走 `runner.stream`；自動同步只跑「資料來源」那一個（`auto_plan`，`backend/sync/runner.py:288`），另一個來源回 `not_in_use`。

#### 同步計時與 log（SP-215，2026-10-06）

- **計時**：`SyncClock`（`backend/sync/runner.py:47`）用 client 事件之間的間隔，把一次同步的時間分到 `list`（登入、列活動）／`check`（查已匯入的）／`download`／`import`／`finish`（cursor、心率設定）／`after`（stream 結束後的掛鉤）。下載和匯入照 client 事件帶的 `secs.download` 拆開（`backend/sync/coros_client.py:800`，TP 同樣）。
- **存與記**：結果存進 `last_result.secs`（`backend/sync/runner.py:178`），設定頁顯示時間花在哪；每次同步寫一行 log，含各步秒數與最慢的一筆活動 id（`_log_run`，`backend/sync/runner.py:97`）。總時間 ≥ `SLOW_SYNC_S`（120 秒，`backend/applog.py:57`）或同步失敗時記 WARNING，其他記 INFO。
- **不記個人資料**：COROS／TP client 的 log 只寫有沒有 LTHR、體重，不寫數值，也不寫 user id。

---

## Data Model

### `workout_files` 表新增欄位

```sql
ALTER TABLE workout_files ADD COLUMN coros_activity_id TEXT;  -- index, unique per activity
ALTER TABLE workout_files ADD COLUMN coros_sport_type INTEGER;
ALTER TABLE workout_files ADD COLUMN coros_training_load REAL;  -- 列表的 trainingLoad（SP-38），NULL = 沒有
-- source 欄位新增值: 'coros'（原有 'local' | 'trainingpeaks'）
```

### `sync_state` 表新增欄位

```sql
ALTER TABLE sync_state ADD COLUMN coros_access_token TEXT;
ALTER TABLE sync_state ADD COLUMN coros_token_expires DATETIME;
ALTER TABLE sync_state ADD COLUMN coros_last_sync_at  DATETIME;
ALTER TABLE sync_state ADD COLUMN coros_email         TEXT;
ALTER TABLE sync_state ADD COLUMN coros_base_url      TEXT;  -- 偵測到的資料 server URL
ALTER TABLE sync_state ADD COLUMN coros_user_id       TEXT;  -- 用於 yfheader
-- 2026-10-01 「記住密碼」（勾選才有；secrets.seal 加密；取消勾選／登出即 NULL）
ALTER TABLE sync_state ADD COLUMN coros_password_sealed TEXT;
ALTER TABLE sync_state ADD COLUMN tp_username           TEXT;
ALTER TABLE sync_state ADD COLUMN tp_password_sealed    TEXT;
```

`sync_state` 在資料分類登錄表（`backend/data_registry.py`，SP-311）屬「機密」：只留在伺服器，
token／封存密碼欄位不出現在任何 API 回應、匯出或同步路徑（`test_data_registry.py` 檢查）。

### 備份（SP-355，`backend/engine/backup.py`）

設定頁「備份」打包成 `trailruncoach-backup-YYYYMMDD-HHMMSS.zip`（格式 `FORMAT = 2`，`backend/engine/backup.py:81`）：

| zip 裡 | 內容 | 依據 |
|---|---|---|
| `wko5coach.db` | DB 快照（sqlite backup API），**拿掉留在這台電腦的資料**（`strip_secrets`，`backend/engine/backup.py:184`）：`local` 表清空（`debug_tokens`、`debug_audit`、`debug_auth_failures`：debug token 與 IP 紀錄）；`sync_state` 的 `local_fields` 設成 NULL（COROS／TP token、到期時間、封存的密碼、帳號 e-mail／id／username），**列與同步 cursor 留著**；`user_settings` 的 `LOCAL_SETTINGS` 鍵刪掉（`backup.*`、`debug.*`、課表訂閱 `plan.calendar` 的連結 token）；最後 `secure_delete` + `VACUUM`，舊值不留在空頁 | 登錄表 `Table.local`／`local_fields`／`cursor_fields`（`backend/data_registry.py:73`、`:142`–`:157`）、`LOCAL_SETTINGS`（`:341`） |
| `files/<相對路徑>` | 使用者改的檔案：`plan.json`、`engine.json`、`corrections.json`、`annotations.json`、`views/`、`racepower_solo_hikes.json`、`racepower_hike_meta.json`、`racepower_shares/`、`routes/names.json`、上傳的 `event_gpx/`、`template_gpx/`（`_user_files`，`backend/engine/backup.py:300`） | 登錄表 `FILES` 裡 `backup=ALWAYS` 的項目（`backend/data_registry.py:191`–`:218`）；新的使用者檔案只要在登錄表標 `ALWAYS`，備份就會帶上，不用改 backup.py |
| `fit/<source>/<year>/<file>.gz` | 同步下來的 FIT 原檔（勾「同時備份 FIT 原始檔」才有） | `FIT`，`backup=OPT_IN` |
| `manifest.json` | 版本、`tenant`、筆數、`db_sha256`、`fit`、`files {count, bytes}`、`local_removed {tables, columns, settings}` | |

- **雲端資料夾裡只出現做好的 zip**（審查 H1）：快照與拿掉機密都在 DB 旁的 `backups/`（本機、登錄表「機密」類）做
  （`work_dir_for`，`backend/engine/backup.py:290`；API 傳 `_local_dir()`），雲端資料夾只寫隱藏的 `.partial` 再改名。
  被中斷留下的 `.trc-snap-*`／`.trc-restore-*`／`.trc-inspect-*` 與 `.partial`，超過一小時由 `clean_stale`
  清掉（`backend/engine/backup.py:470`）：`prune`（每次備份後，雲端資料夾）、每次備份（本機 `backups/`）、
  App 啟動後第一次自動備份檢查（兩處，`backend/api/backup.py:126`）。
- 機密檔案（`secret.key`、`weather.json`、`tp_client.json`、`backups/`）在登錄表是「機密」，規定 `backup=NEVER`，
  不會被走訪；`plan()` 也拒絕非「使用者改的／匯入的」、非租戶資料夾的項目（`backend/engine/backup.py:255`）。
- 只有資料夾是同一個的租戶能備份／還原（`check_roots`，`backend/engine/backup.py:278`）：示範沙盒（BASE／SHARED 在別處）
  拒絕；manifest 記 `tenant`（`backend/api/backup.py:57`），還原別的帳號的備份會拒絕（沒有 `tenant` 的舊備份照收）。
- 上限（L1）：`files/` 單檔 50 MB、合計 1 GB、最多 10,000 個（`backend/engine/backup.py:97`）；備份時超過就報錯，
  不會做出還原不了的備份。FIT 原檔還原時串流解壓、單檔上限 256 MB。

**還原**（`restore`，`backend/engine/backup.py:991`）：

1. 驗證並解開（`open_backup`／`_extract_files`，`backend/engine/backup.py:720`、`:689`）：manifest、DB（sha256、
   integrity_check），**每個 `files/` 項目**都檢查路徑（只收登錄表 `ALWAYS` 的路徑；`../`、絕對路徑、反斜線、
   機密檔、快取、`.tmp` 一律略過並計數）、大小上限、整個讀過一次（CRC），解到暫存資料夾。zip 損毀
   （`BadZipFile`／`zlib.error`）一律是「備份檔損毀，不能還原」。這一步失敗，什麼都沒動。
2. 把目前的 DB 與檔案另存成 pre-restore（同樣不含本機資料）；設定頁還原清單會列出這些（「還原前自動另存的」），
   可以直接還原回去（`list_pre_restores`、`_source` 收 pre-restore 檔名，`backend/engine/backup.py:464`、
   `backend/api/backup.py:227`）。
3. 把這台電腦的資料併進解開的那份 DB（`merge_local`，`backend/engine/backup.py:888`）：`local` 表整個換成本機的列；
   `sync_state` 依主鍵把 `local_fields`（登入）換成本機的值，備份裡沒有的列整列加入，格式 1 備份帶的憑證一律清掉
   （`_put_local_fields`，`:817`）；`LOCAL_SETTINGS` 換成本機的；備份 schema 較舊時先補表／欄位。最後**所有同步
   cursor 不晚於備份時間**（`_clamp_cursors`，`:871`）。
4. 檔案先複製到目標旁的 `*.restore.tmp`（`_stage_files`，`:931`），再做**唯一一次** sqlite `backup()` 換掉 live DB
   （`_swap_into`，`:919`）——沒有任何時刻 live DB 裡是空的登入。之後把暫存檔改名到位；這步出錯回報「資料庫已還原，
   但…」，登入不受影響。API 只要 DB 換過，`after_restore()` 一定會跑（`finally`，`backend/api/backup.py:281`）。
5. 補回缺的 FIT（`_restore_fit`，`:960`），壞掉的那個略過、計數（`fit_failed`）。

備份裡沒有的檔案不動。**分享連結（`racepower_shares/`）會備份、但從不還原**（登錄表 `restore=False`，
`backend/data_registry.py:197`）：刪掉／撤銷的連結不會被還原叫回來；結果訊息說「分享連結 N 個沒有還原（留在備份檔裡）」。
格式 1（SP-355 之前）照樣能還原：沒有 `files/`，它帶的憑證不採用、cursor 採用。格式 2 給舊版 App 還原會被擋下
（「請先更新 App」）。

**`sync_state` 的決定**（2026-10-07 使用者交給 AI 決定；2026-10-08 依安全審查修正）：憑證與帳號身分不進備份、
還原保留本機的；**同步 cursor 跟著資料走**。理由：封存用的 `secret.key` 不在備份裡，換機後封存的值解不開，帶了沒用；
2026-09-30 以前寫入、之後沒更新的舊列可能是明文，帶進雲端資料夾反而是外洩；同一台還原時登入不該被舊備份蓋掉。
cursor 若留本機的（第一版的做法），還原三週前的備份後，下次增量同步會從本機較新的 cursor 開始，這三週的活動就漏抓；
現在 cursor 用備份的、而且不晚於備份時間，下次同步會把這段補回來（重複的活動由匯入去重）。`sync_failures` 跟著備份走。
`secrets.SEALED_DB_COLUMNS` 補上 `coros_password_sealed`、`tp_password_sealed`（`backend/settings/secrets.py:52`）：
以前只剩記住的密碼是密文時（token 過期／登出後），金鑰遺失會被默默重新產生，那份密碼就再也解不開；明文的舊列不算密文，
`unseal` 照原樣讀。金鑰遺失時的 503 `SECRET_KEY_MISSING` 訊息與設定頁提示加上自救方法：登出 COROS 與 TrainingPeaks
（登出會刪掉該帳號所有封存值）再重新登入，就會產生新金鑰（`RECOVER_WITHOUT_KEY`，`backend/settings/secrets.py:45`）。

**升級注意（L6）**：雲端資料夾裡 SP-355 之前（格式 1）的備份仍含 `sync_state`（token、封存的密碼、帳號 e-mail）與
debug token，要等保留規則（7 天每天＋4 週每週，約 5 週）自然刪掉；想立刻清掉就手動刪除舊的
`trailruncoach-backup-*.zip`（建好一份新格式的備份之後）。

測試：`backend/tests/test_backup.py`（SP-355 段：登錄表驅動的檔案清單、新項目自動帶上、掃描 zip 位元組找不到 token／
密碼／e-mail／debug token／IP／ICS token／機密檔、雲端資料夾從頭到尾沒有 DB 檔、殘留清理、往返（分享連結不還原）、
三週前的備份帶自己的 cursor、cursor 不晚於備份、舊 schema、格式 1、惡意 `files/` 路徑、損毀／超過上限什麼都不動、
換完 DB 後出錯登入仍在、沙盒與別的帳號拒絕、API 一定跑 `after_restore`、pre-restore 可還原）、
`backend/tests/test_data_registry.py`（每個租戶的使用者／上傳檔案都是 `ALWAYS`；機密表與有 IP 欄的表都標成留在本機；
像憑證的設定鍵都在 `LOCAL_SETTINGS`；`restore=False` 只給 `ALWAYS` 的項目）、`backend/tests/test_secrets.py`
（封存密碼欄位、明文舊列、金鑰遺失訊息）。

### `pmc_cache` 刪除（SP-341）；`workout_metrics`／`mmp_cache` 留在舊 DB（2026-10-08）

```sql
DROP TABLE IF EXISTS pmc_cache;                 -- 沒有程式讀寫（data_registry.RETIRED_TABLES）
-- workout_metrics、mmp_cache：不刪（不做破壞性遷移）、不再建、沒人讀寫（data_registry.LEGACY_TABLES）
```

`workout_metrics`（匯入時寫的 hrTSS／rTSS／NP／TSS／高強度秒數）與 `mmp_cache`（只拿來算那些指標的
runFTP；SP-341 曾為它加 `version` 欄）從 2026-10-08 起不再寫，model 也拿掉（`backend/db/models.py:84`）。
圖表、PMC、API 的 TSS 一律來自 FIT Dataset（`Dataset._metrics`），從來不讀這兩張表。舊 DB 裡的列原樣留著；
刪除活動（`sync/purge.py`）不再連帶刪它們的列，留下的孤兒列也沒人讀。`init_db` 不動它們
（`backend/db/database.py:119`），`unclassified_tables` 略過（`backend/data_registry.py:150`、`:319`）。

### `athlete_settings` 表（已有，從 Coros 登入自動填入）

登入成功後自動 upsert（`_store_login`，`backend/sync/coros_client.py:213`；寫入在 `:234-256`）：
- `lthr` ← `data.zoneData.lthr`（不是跑步門檻，是沒有夠硬的跑步可估時的冷啟動先驗，見下）
- `weight_kg` ← `data.weight`
- `effective_date` ← 今日
- `data.zoneData.ftp` 不存（2026-10-08 前寫進 `ftp_w`，沒有任何程式讀；舊 DB 留著這欄、model 不再對應，`backend/db/models.py:28`）

---

## Domain Model

### Bounded Context
- **Context Name**: Activity Sync（活動同步）
- **Domain Layer**: Supporting（外部平台資料的取得與整理；分析與教練邏輯在下游）
- **Parent Module**: TrailRunCoach backend — `backend/sync/`、`backend/api/sync.py`、`backend/api/auth.py`；下游讀者為圖表 Dataset（`backend/engine/wko5expr/`）與計畫（plan-auto.spec.md）

### Ubiquitous Language

| Term | Meaning | Code |
|------|---------|------|
| 來源（Source） | 一個外部活動平台：COROS 或 TrainingPeaks。資料夾／API 名 `coros` / `tp`，DB 名 `coros` / `trainingpeaks` | `backend/sync/storage.py:20`、`backend/sync/primary.py:25` |
| 資料來源（Primary source） | 使用者選的那一個來源，全 app 唯一被讀與自動同步的來源；另一個原封保留 | `sync.primary_source`，`current`，`backend/sync/primary.py:87` |
| 同步（Sync run） | 一次列活動 → 下載 FIT → 匯入的執行，以 SSE 事件回報；每來源同時只能一個 | `runner.stream`，`backend/sync/runner.py:138` |
| 同步忙碌（SYNC_BUSY） | 同一來源已在同步時的拒絕（409） | `SyncBusy`，`backend/sync/runner.py:41` |
| 同步結果（Last result） | 每次同步的 `status`（ok／partial／failed／aborted）、下載數、錯誤數、觸發方式 | `sync.<src>.last_result` |
| 上次成功同步（Last ok） | 最近一次 ok／partial 的同步；失敗的同步不算「同步過」 | `sync.<src>.last_ok`、`runner.last_sync_at` |
| 增量 cursor | 上次同步的時間；COROS 下次從它減 14 天（`CURSOR_OVERLAP_DAYS`，`backend/sync/coros_client.py:33`）開始列，TP 列「之後有修改的」。單筆失敗不擋（進失敗清單）；整次失敗（登入、列表）或失敗清單寫不進去才不推進（SP-362） | `sync_state.coros_last_sync_at` / `last_sync_at` |
| 失敗清單（Failed list） | 同步列到但下載／匯入失敗的活動（`failed`），或沒有 FIT 的手動紀錄（`no_file`）。之後的同步依 id 重試 `failed`，最多 5 次或試滿 7 天（第一次到最後一次嘗試的間隔，第 2 次起才算：一次失敗後一週沒同步仍會再試；`auto_retry`，`backend/sync/failures.py:55`），之後停止、留在設定 › 進階設定可按「重試」（`no_file` 列也可以）；匯入成功即移除，刪除來源檔案時清空。TP 的 `no_file` 在 workouts/changed 再列到時重新檢查（計畫中／還沒上傳的課之後才有檔） | `sync_failures` 表，`backend/sync/failures.py`、`SyncFailure`，`backend/db/models.py:407` |
| 自動同步（Auto sync） | 開網站或每日排程觸發，只對資料來源、已啟用、已登入、閒置且超過門檻小時數者 | `auto_plan`，`backend/sync/runner.py:288` |
| 登入狀態（Session status） | `logged_in` / `expired`（登入已過期）/ `logged_out`；檢查結果另有 `unknown`（連不上，仍顯示已登入） | `backend/sync/session_check.py:45` |
| 記住密碼（Remembered password） | 勾選才存、加密存放的密碼，只用於 token 失效時自動重新登入一次 | `coros_password_sealed` / `tp_password_sealed` |
| 資料 server（Data base URL） | COROS 實際接受 token 的資料 API region，登入時偵測 | `sync_state.coros_base_url` |
| 跨來源重複（Duplicate） | 兩個來源的同一活動（開始時間 ±2 分）；非資料來源那列標 `duplicate_of`，不進總數 | `backend/sync/dedup.py` |
| 損壞檔 stub | 解析不了的 FIT 也記一筆 `file_format="corrupt"`，避免每次重下載 | `record_corrupt`，`backend/files/file_service.py:169` |

### Domain Events

| Event | Raised by | Consumers |
|-------|-----------|-----------|
| 同步完成（`status: complete`，含下載數） | `coros_client.sync_workouts` / `tp_client.sync_workouts` | `runner.stream`：`plan_auto.after_sync`、`localtime.refresh_from_fits`、`calibrate.after_sync`、`wko5views.warm_up` |
| 登入過期（`mark_expired`） | 任何回「需重新登入」的 COROS／TP 呼叫 | `/auth/*/status`、`/auth/session-alerts`、`runner.logged_in` |
| 資料來源切換（PUT `/sync/settings` 帶 `primary_source`） | 設定頁 | `dedup.rebuild`；圖表 Dataset 依 `source_stamp` 重建 |

---

## API Contracts

### 已實作 Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/auth/coros/login` | Coros 登入，自動偵測 region + 儲存帳號 LTHR／體重 |
| GET | `/api/v1/auth/coros/status` | 登入狀態 + email + 最後同步時間 |
| POST | `/api/v1/auth/coros/logout` | 清除 token |
| POST | `/api/v1/sync/coros/start` | 觸發同步（SSE stream） |

（`POST /api/v1/pmc/recompute` 已不存在：TSS 不再存 DB，由 FIT Dataset 即時算。）

### M3 新增 Endpoints（2026-06-13，統一同步頁）

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/sync/start` | 觸發 TrainingPeaks 下載同步（SSE stream，經 `runner.stream` 接 `tp_client.sync_workouts`；沒有 `/sync/tp/start`） |
| POST | `/api/v1/auth/tp/login` | TP 帳密登入取 OAuth token（`tp_client.login_password` 已驗證） |
| GET | `/api/v1/auth/tp/status` | TP 連線狀態 |

### 2026-09-30 新增 Endpoints（設定頁「資料同步」）

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/sync/sources` | 每個來源的登入狀態、是否啟用、是否同步中、上次同步時間與結果、檔案數 / 大小 / 活動期間 |
| GET/PUT | `/api/v1/sync/settings` | 資料來源（`primary_source`：`coros` / `trainingpeaks`）、各來源開關、時區、`daily_sync_time`（每日同步時間）、`auto_on_open`（開網站時自動同步）+ 門檻小時數、`chart_data_source`（`source` = 資料來源 / `wko5`）、`map_basemap` / `map_overlays`（單次活動路線圖的預設底圖與疊加層）、TP OAuth 開關；2026-10 起另有 `exclude_bad_activities`、`use_power`、`accept_watch_power`、`push_provider`、`region_override`、`primary_sport`（欄位對應 `_SETTING_KEYS`，`backend/api/sync.py:227`）。GET 另回傳生效值（`timezone_effective`、`region`、`power_source`、`wko5_available`…）與 secret 來源、金鑰狀態，都只給標籤、不給值。PUT 換資料來源時重建去重 |
| POST | `/api/v1/sync/start`、`/api/v1/sync/coros/start` | 走共用 runner：同一來源已在同步時回 409 `SYNC_BUSY`，結果寫進 `sync.<src>.last_result` |
| GET | `/api/v1/sync/primary` | 2026-10-04：目前資料來源（`source` coros／tp、`label`）、是否登入、是否啟用、是否同步中；不算檔案統計、不載 Dataset。課表頁「從 COROS 抓活動」用（`backend/api/sync.py:88`）。2026-10-05（SP-88）：`logged_in` 改走登入檢查（`session_check.check`，有快取），並回 `login`（ok／expired／unknown／logged_out）；原本只讀快取，快取過期後對已被拒的 token 回 true |
| POST | `/api/v1/sync/auto` | 開網站時呼叫。對「已啟用、已登入、閒置、且超過 N 小時」的來源在背景啟動同步，立刻回傳；新鮮、忙碌或關閉時什麼都不做 |
| DELETE | `/api/v1/sync/{coros\|tp}/files[?date_from&date_to]` | 刪掉該來源的 FIT 與 DB 紀錄，重建去重、重設 cursor、清空該來源的失敗清單（SP-362，`backend/sync/purge.py:95`），並拿該來源的鎖（同步中回 409）。若它正是圖表讀的資料來源且有 WKO5 資料夾，圖表改回 WKO5（`backend/sync/purge.py:106-109`） |
| GET | `/api/v1/sync/failed` | 2026-10-08（SP-362）：失敗清單 `items`（`source`、`provider_id`、`date`、`kind` failed／no_file、`state` retrying／stopped／no_file、`attempts`、`last_error`（遮蔽過、≤ 200 字）、`first_at`、`last_at`）與 `max_attempts`、`max_age_days`（`backend/api/sync.py:167`）。設定 › 資料同步 › 進階設定列出每一列（日期、來源、試了幾次／停止自動重試／沒有 FIT 檔）與「重試」（`loadFailed`，`backend/static/settings.html:1187`） |
| POST | `/api/v1/sync/failed/{id}/retry` | 「重試」：該列 attempts 歸零、7 天重新起算（no_file 也改回 failed），來源可同步（已登入、啟用、閒置）就在背景開始同步（`started`），否則等下次同步（`queued`）（`backend/api/sync.py:176`） |
| GET | `/api/v1/sync/compare?a=&b=&since=` | 兩個資料來源逐筆活動比對：時長、距離、爬升、NP、TSS（含所用 FTP 與來源）。頁面是 `/api/v1/static/compare.html`；沒有 WKO5 資料夾時比 `wko5` 回 404 `NO_WKO5_FOLDER` |
| POST | `/api/v1/auth/tp/login` | 新增 `method` 參數：`auto`（預設）/ `web` / `oauth` |

### 2026-10-02 ~ 10-03 新增 Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/auth/session-alerts` | 正在用的登入（資料來源的同步、COROS 課表推送）中已過期者（`expired`，含 `needs`），以及資料來源能不能同步（`sync`：`problem`、`last_ok_at`、`last_run`，SP-88），給總覽／課表的橫幅（`backend/api/auth.py:165`） |
| PUT | `/api/v1/auth/{coros\|tp}/remember` | 取消勾選「記住密碼」立即刪除已存密碼；勾選本身不存任何東西（密碼只隨登入送出）（`backend/api/auth.py:237`） |
| POST | `/api/v1/auth/tp/logout` | TP 登出 |
| GET | `/api/v1/auth/tp/oauth`、`/api/v1/auth/tp/callback` | TP OAuth 轉址流程（密碼登入的替代） |
| GET | `/api/v1/sync/status` | TP 舊狀態（`source: "tp"`、`authenticated`、`last_sync`、`cursor`）；`authenticated` 只看 TP token，與資料來源（COROS）的登入無關 |
| POST | `/api/v1/sync/timezone/browser` | shell.js 送一次瀏覽器的 Intl 時區，當自動時區的輸入之一（`backend/api/sync.py:341`） |
| POST | `/api/v1/sync/dedup/rebuild` | 手動重建跨來源去重 |
| GET | `/api/v1/sync/tp/settings` | 讀 TP 運動員設定（FTP、體重、LTHR），只回傳、不寫 DB |
| GET | `/api/v1/sync/check` | 每來源：`running`、`progress`、`ready`、上次完整檢查 `last`、上次每週檢查 `weekly`（SP-362，`backend/api/sync.py:197`） |
| POST | `/api/v1/sync/check` | 「完整檢查」：body `{source?}`，沒給就檢查所有能同步的來源；回 `started`／`skipped`（`disabled`／`not_logged_in`／`busy`）；指定的來源忙碌時 409 `SYNC_BUSY`（`:205`） |
| POST | `/api/v1/sync/check/{source}/fill` | 「補下載」上次完整檢查的 `missing`（body `{ids?}`）；沒有可補的 404 `NOTHING_MISSING`，忙碌 409 `SYNC_BUSY`，未登入／停用 409 `NOT_READY`（`:229`） |

`GET /auth/coros/status` 與 `/auth/tp/status` 改回 `status`（`logged_in` / `expired` / `logged_out`）、`expired`、`check`（`ok` / `expired` / `unknown`）、`password_saved`（COROS 另有 `auto_relogin`）；不回 token 或密碼（`_session_fields`，`backend/api/auth.py:129`）。`/sync/sources` 的 `logged_in` 也排除已知過期的登入。

**資料來源二選一**（2026-10-02，`backend/sync/primary.py`）：`sync.primary_source` = `coros` | `trainingpeaks`，所選來源是唯一被讀的——圖表 Dataset 只讀它的資料夾（`current_source`，`backend/engine/wko5expr/datasource.py:78`）、自動同步只同步它（`auto_plan`）、CP 測試／比賽功率掃描只讀它的檔（`unused_folder`，`backend/engine/racepower/cptest.py:137`）、DB 總數只算它的列（`in_use_clause`，`backend/sync/dedup.py:34`）。另一個來源的檔案、DB 列和已存登入原封不動，切回來就能用。舊值（`auto`、未設定）在啟動時（lifespan 呼叫 `primary.migrate`，`backend/main.py:86`）或第一次讀取時改成舊「自動」會選的來源：最新活動日較新者，同日或沒資料選 COROS（`choose_initial`，`backend/sync/primary.py:62`）。活動以開始時間為鍵（`backend/engine/activity_key.py`），所以標籤、RPE、背負、課表配對在切換後仍在。設定頁切換前會先確認。

**每日排程**：`backend/sync/scheduler.py`，在 app lifespan 啟動。每分鐘檢查一次，每個本地日期到了 `daily_sync_time` 之後執行一次，只同步資料來源（`runner.auto_plan`）；來源正忙（完整檢查、補下載、別的同步）或開始後回 SYNC_BUSY 時當天不算跑過，下一分鐘再試（SP-362 review #4）。同一個 loop 也跑每日自動備份與每週檢查。設 `WKO5COACH_NO_SCHEDULER=1` 可關閉（`backend/main.py:104`；demo 模式改跑 demo 自己的 loop）。

**開網站自動同步**：`backend/static/autosync.js`，已由 shell.js 自動載入（`backend/static/shell.js:420`；demo 模式不載入）。也可以在頁面裡放 `<script src="/api/v1/static/autosync.js" defer></script>`。它每個瀏覽器每 10 分鐘最多呼叫一次，狀態顯示在 `#nav-sync-status`（沒有這個元素就在右上角加一個小徽章）。同步失敗時顯示「同步失敗」（不自動消失）並請橫幅重讀（SP-88）；失敗的同步不算「上次同步」，下次開網站會再試。

**圖表資料來源**（`charts.data_source` = `source`（預設，資料來源的資料夾）| `wko5`；舊值 `synced` / `coros` / `tp` 讀成 `source`）：`backend/engine/wko5expr/fitdataset.py` 用 FIT 資料夾建 `FitFolderDataset`，每筆活動的指標用本專案自己的公式計算。`datasource.current_source()` / `source_stamp()` 提供 Dataset 工廠。9 月 17 筆活動實測對照 WKO5（當時門檻取自 WKO5 athlete 檔）：時長、距離相同，NP ±0.5%，TSS ±0.2，爬升 1–4%。
**手動同步的前端共用**（2026-10-04）：`backend/static/syncrun.js` 的 `TRCSync.run(src, {since, onEvent})` 打 SSE start 端點並解析進度（`total`／`checked`／`downloaded`／`errors`／`fatal`／`finished`，409 → `busy`），`TRCSync.primary()` 讀 `/sync/primary`。設定頁「立即同步」（`backend/static/settings.html:1375`）與課表頁「從 COROS 抓活動」共用，各自只負責顯示文字。

**FIT 資料集的前置（2026-10-01，`docs/research/unsourced-rules.md` §0.10 第 0 步）**：app 的資料要從 TP／COROS 來，WKO5 只當對照。

- **越野／路跑**：`sport_of`（`backend/engine/wko5expr/fitdataset.py:122`）先看 app DB 的 `workout_files.trail_classification`（唯讀開啟，`load_classifications`，`backend/engine/wko5expr/fitdataset.py:162`），FIT 的 session sub_sport 只當後備（COROS 的 FIT 沒有 trail sub_sport，原本整批越野都被當路跑，回測越野 n = 0；sub_sport 先前也根本沒被讀進來，`fit_to_channels` 現在帶出 `sub_sport`）。跨來源重複（`duplicate_of`）視為同一筆活動：群組裡任一列的使用者覆寫優先（自己這列 → 主紀錄 → 其他重複列），否則用自己這列的自動值，再退到主紀錄（`classification_for`，`backend/engine/wko5expr/fitdataset.py:248`）。每個來源的資料集仍保留自己的檔案（不因為是重複列就丟掉，否則該來源會少活動）。越野跑同時加上 WKO5 的 `runningtrail` 標籤，因為 thresholds／品質門檻／status／成就只看標籤。
- **門檻與體重**，依序：賽季計畫的 dated 列（`Dataset.setting` / `cp`）→ app DB 的 `athlete_settings`（體重、`run_ftp_w`、閾值配速；`_load_db_settings`，`backend/engine/wko5expr/fitdataset.py:780`）→ 從這些 FIT 估算的 as-of LTHR（`_estimate_settings`，`backend/engine/wko5expr/fitdataset.py:874`：每 30 天一個格點，推估；格點日只用當天以前的跑步，`thresholds.estimate`，每次跑步對照它自己日期的 `racepower.athlete.cp_as_of`，估出的值只套用到格點日以後；只在有 app DB 時自動估算）→ 未設定。跑步 FTP（功率 TSS）**不**用 `cp_as_of` 的估算值補：它的 PD 重擬在第一筆計畫 CP 之前沒有合理性參考，在一位跑者的 COROS 資料上，手錶功率時期的擬合值比計畫 CP 高約 70%，會讓那段時間的功率 TSS 少到約三分之一。**2026-10-03 起**跑步功率 TSS 的 FTP 改為 `tss_ftp`（`backend/engine/wko5expr/fitdataset.py:1179`）＝圖表用的同一個 CP：計畫 CP 測試 → `run_ftp_w` → **只用 Stryd 跑步**的 PD 模型 mFTP（推估，手錶功率永遠不進擬合；見「圖表分析在 COROS 來源」）→ 都沒有時用 rTSS／hrTSS（移動時間 hrTSS ＋ 爬升加成）。標籤寫出來源（`cp_info`），`/workouts` 與來源比對頁顯示所用 FTP。parity 模式與 WKO5 opt-in 仍用 WKO5 規則（`Dataset.tss_ftp`）。測試：`backend/tests/test_run_ftp_tss.py`。實測（2026-10-01，一位跑者同步進行中的 COROS 803 筆）：LTHR 估算只有前後兩段時期有值，中間約 17 個月沒有 LTHR，那段 COROS 跑步在 app 路徑上沒有 TSS。WKO5 athlete 檔只有在設定 `charts.fit_settings_from_wko5 = true`（預設 false，`backend/settings/repository.py:88`）時才讀（`dataset_for_source`，`backend/engine/wko5expr/fitdataset.py:1282`）。各處的來源標籤改走 `Dataset.setting_label`，FIT 資料集不再顯示「WKO5 設定」。
- **`athlete_settings.lthr` / `ftp_w` 不當跑步門檻**：唯一的自動寫入者是 `coros_client.login`（COROS 帳號 `zoneData.lthr` / `.ftp` 與體重，日期 = 登入當天 UTC），沒有記錄是哪個運動；TP 的 `fetch_tp_settings` 只回傳 JSON、不寫 DB。實測時 DB 裡那列是 COROS 登入寫的，不是 TP；它的 LTHR 高於同一天 12′ 全力測試的峰值心率，不可能是現在的跑步 LTHR。`lthr` 留在 `settings_ignored`（`backend/engine/wko5expr/fitdataset.py:805`），**有用到**：沒有夠硬的跑步可以估 LTHR 時，它是冷啟動先驗（`_coros_lthr_prior`，`:809`，由 `_estimate_settings` 在 `:937` 呼叫；有它時 SP-289 的 0.90 × 最大心率先驗不套用，`_apply_lthr_prior` `:842`），所以登入照寫。`ftp_w` 從 2026-10-08 起不寫、不讀（`read_athlete_settings` 不選它，`:197`；舊 DB 留著這欄）。體重照用。
- **快取**：`source_stamp` 多帶 `db_stamp()`（`backend/engine/wko5expr/datasource.py:99`），分類覆寫、去重或 `athlete_settings` 變了，即使 FIT 檔沒變也會重建 Dataset。`FitFolderDataset.cached_series`（`backend/engine/wko5expr/fitdataset.py:1212`）是每個 FIT 檔的磁碟快取（fitcache 資料夾的 `series_<key>.json`），key 含檔案 stamp、修正與當時的門檻，每檔保留幾組門檻版本（估算前／後）。
- 測試：`backend/tests/test_fit_dataset_prereqs.py`（合成 FIT ＋ tmp SQLite，不碰 WKO5 資料夾與真實 DB）。

**時區**：`FitFolderDataset` 把 FIT 的 UTC 起始時間換成運動員當地時間再取日期（`backend/engine/wko5expr/fitdataset.py:545`、`backend/engine/wko5expr/fitdataset.py:599-601`；naive 時間視為 UTC），時區來源與同步一致：`athlete.timezone` 設定 → `WKO5COACH_TZ` → 自動偵測（`athlete.timezone.auto`：同步下載新檔後由最新 FIT 的當地時間偏移決定，瀏覽器 Intl 時區一致時優先、含日光節約；`backend/engine/localtime.py`）→ 系統時區（`athlete_tz`，`backend/engine/wko5expr/datasource.py:92`；`resolve_tz`，`backend/settings/repository.py:492`）。測試：`backend/tests/test_scan_and_tz.py:115`、`backend/tests/test_scan_and_tz.py:124`、`backend/tests/test_scan_and_tz.py:130`、`backend/tests/test_region_time.py`。

**路線圖設定**（`charts.map.basemap` / `charts.map.overlays`，`backend/settings/repository.py:124-125`）：預設底圖 `None` = 依地區（tw `rudy`、intl `osm`，`backend/api/sync.py:278-279`）、無疊加層。底圖限 `MAP_BASEMAPS`、疊加層須為 `MAP_OVERLAYS` 內不重複的清單（`backend/settings/repository.py:248-249`、`backend/settings/repository.py:364-369`），不合法時 `PUT /sync/settings` 回 400。API 欄位在 `backend/api/sync.py:217-218`，對應的設定鍵在 `:236`。地圖本身屬 viewer，見 wko5-engine.spec.md。

**接線**：`_dataset()`（`backend/api/wko5views.py:188`）經 `_dataset_key`（`backend/api/wko5views.py:177`）讀 `current_source()`——資料來源的資料夾（`coros` / `tp`），只有 `charts.data_source = wko5` 且有 WKO5 athlete 檔時才是 `wko5`——並以 `source_stamp()` 當快取 key（`backend/api/wko5views.py:184`）；`_dataset_cfg`（`backend/api/wko5views.py:160`）呼叫 `dataset_for_source(source, ATHLETE_DIR, config)`（`backend/api/wko5views.py:146`）：`coros` / `tp` 建 `FitFolderDataset`（只讀一個資料夾，不合併），`wko5` 照舊是 WKO5 `Dataset`。總覽（`backend/api/overview.py:32`）與功率計算機（`backend/api/racepower.py:55`）都走同一個 `_dataset()`。render cache 把 dataset 的 `source` / `source_stamp` 放進 key（`backend/engine/wko5expr/render_cache.py:116`），圖表請求本身也帶 `source`，所以換來源或同步新檔案都不會拿到舊圖。圖表頁右上角有資料來源切換（`#source-chip` + `sourcechip.js`，`backend/static/wko5_viewer.html:411`），設定頁的說明也改成已生效（`backend/static/settings.html:338`）。實測（2026-09-30，本機資料）：`coros` 17 筆活動，5 個 view 共 186 張圖 0 錯誤；`wko5` 預設的輸出與改動前相同（只少了地圖面板不再使用的 `track`）。

**COROS 課表推送**（`backend/sync/coros_workouts.py`，把本專案的計畫課表依日 / 週 / 期推到 COROS 並記錄在 `coros_plan_push` 表）：屬於計畫功能，規格見 overview.spec.md。

> TP client（`backend/sync/tp_client.py`，649 行）已於 2026-05-15 對 live TP OAuth 驗證：password grant → athlete download → `filedata` 端點回 base64-gzip FIT → decode/inflate。M3 主要是把它接到 UI 並加盤點，不需重新逆向格式。

### Request / Response

**POST /api/v1/auth/coros/login**
```json
// Request
{ "email": "user@example.com", "password": "plaintext_password", "athlete_id": 1, "remember": false }

// Response 200
{
  "authenticated": true,
  "coros_user_id": "<user_id>",
  "email": "user@example.com",
  "region": "eu",
  "data_server": "https://teamapi.coros.com",
  "token_expires": "2026-05-16T02:15:00Z",
  "password_saved": false
}

// Response 409 — 另一個登入進行中（COROS_LOGIN_BUSY）；503 — 本機金鑰缺少（SECRET_KEY_MISSING）

// Response 401
{ "detail": "COROS_LOGIN_FAILED: The login credentials you entered do not match our records." }
```

**GET /api/v1/auth/coros/status**
```json
{
  "authenticated": true,
  "expired": false,
  "status": "logged_in",            // logged_in | expired | logged_out
  "check": "ok",                    // ok | expired | unknown（session_check）
  "email": "user@example.com",
  "token_expires": "2026-05-16T02:15:00Z",
  "last_sync": "2026-05-15T02:10:00Z",
  "password_saved": false,
  "auto_relogin": false
}
```

**POST /api/v1/sync/coros/start** — SSE stream
```
event: sync_progress
data: {"status": "started", "since": "20260514", "until": "20260515"}

event: sync_progress
data: {"status": "checking", "activity_id": "<label_id>", "date": "2026-05-14"}

event: sync_progress
data: {"status": "downloaded", "activity_id": "<label_id>", "file": "..._trail_run.fit", "sport": "trail_run"}

event: sync_progress
data: {"status": "skipped", "activity_id": "...", "reason": "already_imported"}

event: sync_progress
data: {"status": "error", "activity_id": "...", "error": "detail/download error: ..."}

event: sync_progress
data: {"status": "complete", "total_downloaded": 2, "total_checked": 2, "errors": []}
```

---

## TSS 計算

匯入時不算、不存 TSS（2026-10-08 起；以前寫進 `workout_metrics`，用 COROS 帳號 `ftp_w`／90 天
`mmp_cache` 擬合的 runFTP，但沒有任何程式讀）。圖表、PMC、總覽、API 的 TSS 都由 FIT Dataset 即時算
（`Dataset._metrics`，`tss_source` = power／rtss／trainingpeaks／hrtss；跑步功率 TSS 的 FTP 見上方「門檻與體重」
的 `tss_ftp`）。原本這裡描述的 `POST /api/v1/pmc/recompute`、`POST /api/v1/athletes/{id}/recalculate-running-metrics`
早已不存在。

### 功率來源（2026-10-01，`backend/engine/power_source.py`）

同一個 FIT `power` 欄位裝了兩種功率，WKO5／TrainingPeaks 都分不出來（它們讀的是同一份
COROS 上傳的 FIT；抽查的兩筆，兩邊的檔案功率完全相同）：

| 來源 | 判定 |
|---|---|
| `stryd` | 紀錄裡有 Stryd 開發者欄位（Form Power、Air Power、Leg Spring Stiffness，經 COROS 轉寫，`developer_data_id` 是 COROS 的），或 `device_info` 有 Stryd（manufacturer 95） |
| `watch` | 有功率但沒有上述欄位／裝置：手錶從手腕推估（配了 Stryd 的跑者也會有零星幾次沒配對） |
| `none` | 沒有 > 0 的功率 |

把有 Form Power 等欄位的跑步當成 Stryd、沒有的當成手錶推估，是推估（手錶本身不算 form power）。

- `FitChannels.power_source`（`backend/files/fit_to_channels.py`，同時讀 `device_info`）；
  `FitFolderDataset` 載入時記在每筆 workout；WKO5 `.wko4` 由 `Dataset.power_source` 從 channel
  判定（依檔案 stamp 快取在 `power_source_v1.json`，檔內帶程式版本 `dataset.per_workout_code`，
  判定程式改了就重算，SP-341）。`.wko4` 存的是同一個 `power` channel
  加上裝置名稱，沒有來源旗標。
- 設定 `power.accept_watch_power`（預設 false；parity 模式一律讀全部功率，同 WKO5）：
  false 時手錶推估功率不算功率 TSS（改用 rTSS／hrTSS，`metrics.power_tss_blocked`），也不進
  功率模型（`docs/spec/racepower.spec.md`）。心率、配速路徑照常使用這些跑步。設定值併入
  `source_stamp`，切換後 Dataset 會重建。
- API：`GET /api/v1/wko5/workouts` 每筆多 `power_source`、`power_label`（手錶功率未採用時為
  「手錶推估功率（未採用）」）；單次活動卡顯示「功率來源」。

### 壞掉的活動檔（2026-10-01，`backend/engine/bad_activity.py`）

忘了停錶就騎車／開車的「跑步」整筆排除：留在 DB 與活動清單（標「已排除：疑似交通工具／騎車（均速
43 km/h）」），但 `FitFolderDataset` 載入時就不放進 `ds.workouts`（WKO5 `Dataset` 在建 index 前濾掉、
重新編號），所以 PMC／TSS、功率曲線、比賽功率、圖表都讀不到；`cptest.curves`／`scan` 讀的 COROS
檔也一樣濾掉（`cptest.bad_files`）。規則、門檻與來源見 `docs/spec/workouts.spec.md`「Bad activity
files」：均速或持續 60 秒／5 分／20 分的速度超過同時間世界紀錄均速 × 1.15（推估），或平均功率
> 10 W/kg（推估）。使用者覆寫存在 `activity_tags.exclusion`（`keep` / `exclude`），設定
`activities.exclude_bad`（預設 true）；兩者都併入 `source_stamp`。parity 模式不排除。
TP 的那筆垃圾檔（下節）就是這條規則抓的：不再只靠「沒有 Stryd 欄位」擋下（已知限制 7）。

### 圖表分析在 COROS 來源（2026-10-01，fix/charts-coros-source）

資料來源為 COROS、圖表讀資料來源（當時設定值為 `charts.data_source = coros`）後，「周期化訓練」幾張圖空白或數字不對，原因與修正（實測 802 筆，唯讀）：
- **VAM 圖空白**：FIT 資料集沒算 4224 `vam`（全部 NaN），登山／健行也沒有 `hiking` / `mountaineering`
  標籤（圖用 `hastag()` 選）。`workout_fields` 補 `vam = round(climbing / duration · 3600)`（WKO5 定義，
  含停留時間，所以多日百岳的 VAM 很低：例如三天行程約 45 m/h），`TYPE_TAGS` 依運動類型加標籤。
  近一年：越野 20 點、登山 4 點（之前 0／0）。
- **Palladino 區間沒資料**：計畫 CP 從第一次 CP 測試那天才有，之前的跑步沒有 CP（WKO5 設定不再讀、
  `run_ftp_w` 空），近 30 天 18 次跑步只算到測試那一次。`FitFolderDataset.cp`：計畫測試 →
  `run_ftp_w` → **只用 Stryd 跑步**的 PD 模型（`racepower.athlete.pd_model`，90 天窗、每 30 天一格、
  窗內 ≥ 5 次 Stryd 跑步，推估；手錶功率永遠不進來，壞檔已在 `ds.workouts`／`cptest.curves` 外）→ 未設定。
  擬合值大致落在計畫 CP 的 0.77–0.92 倍；窗內只有 1 次跑步的格點（擬合值明顯偏低）被門檻擋下。
  2026-10-03 起跑步功率 TSS 也用這個值（`tss_ftp`，見上方「門檻與體重」）。區間表的秒數改用表上印的門檻（當天的 CP）計算，表頭寫出來源與日期、W′
  （有測才有；PD 擬合時顯示 FRC ≈ W′，推估）。近 30 天：39 999 s，1C+Z2 佔 52%。
- **Friel 配速區間沒資料**：計畫沒有閾值配速欄位，`athlete_settings.threshold_pace_s_per_km` 是空的（TP
  沒寫進 DB；COROS 登入那列的 LTHR／FTP 本來就不用）。`thresholds.estimate_tpace`（推估）：
  先用 CP × 近 90 天 Stryd 路跑的速度／功率比（中位數）＝ CP 對應的平路配速；沒有 Stryd 時才用「心率在
  LTHR ±3% 的最快 20 分鐘」中位數（Friel 30 分鐘測試）。後者在夏天讀得太慢（心率飄移：夏天心率在
  LTHR 時配速明顯變慢），比一場半程路跑賽的實際配速還慢約 17%，所以排第二。實測：例如 CP 220 W、
  速度／功率比 12.6 mm/s/W（40 次）→ 6:01 /km；近 30 天 Z1+Z2 81%。估算值只給區間表，不當 rTSS 的閾值配速。
- **課表建議強度**：新增「山路長天／越野輕鬆」（功率 0.75–0.88 CP、心率 ≤ AeT）與「爬坡重複」（功率
  0.95–1.06 CP）兩列，看功率、心率第二；「長跑」改成路跑。依據與限制寫在 `zones.TERRAIN_NOTE`：心率延遲
  τ ≈ 60 s（Hunt 2015／2019）、Stryd 在 0–8% 坡 ≈ 固定代謝負荷（van Rassel 2026）、Stryd 自己說陡峭技術
  地形不能用單一功率數字（陡的技術下坡不看功率）、陡坡負重健行 UA 仍以心率為主；> 8% 坡套功率區間是推估。
  總覽的「長時間輕鬆（山路）」仍用 `long` 那列（心率）——還沒改。
- **訓練量週增幅太大**：公式（本週 ÷ 上週 − 1，所有運動的移動時間）沒算錯，也沒有重複或壞檔灌水
  （COROS 資料夾近一年沒有來源內重複、排除 1 筆；TP 的重複是另一個來源）。數字大是因為單週比單週：
  某週有三天百岳（經過時間約 49 h），讓那週 +131%、下週 −86%；其他週也有 +30%～+90% 的單週跳動。
  UA 說的是「平均」每週 > 10% 持續約 8 週，所以改成「近 4 週平均」的週變化（沒有活動的週算 0）：
  近 20 週落在 −45%～+44%，最大 +44% 仍是百岳那週、−45% 是它離開 4 週窗的那週。

### COROS 與 TP 資料集差異（2026-10-01 實測，唯讀）

TP 1086 筆（796 筆標 `duplicate_of`），COROS 808 筆。功率回測差異的來源：
- **TP 獨有的一筆「跑步」**：約 17 分鐘 12 km（約 43 km/h）、平均功率約 900 W，手錶錄的，沒有
  Stryd 欄位；COROS 資料夾沒有這筆（推定：在 COROS 端刪除過，TP 留著）。它落在一場半程路跑賽
  前的 90 天窗內，讓 PD 模型擬合失敗，詳見 racepower.spec.md「COROS vs TP」。
- TP 缺 COROS 有的十筆早期 Stryd 跑步（TP 的 Stryd 檔晚約一個月才開始）；另有三筆 TP 獨有的
  短跑。其餘配對到的跑步 mean-max 完全相同。
- （當時）`cptest.curves` / `scan` 只讀檔名有 `YYYY-MM-DD` 的 FIT，TP 的 `tp_YYYY_MM_DD_…` 檔名不會進去，
  所以 TP 回測的 PD 擬合其實也混進了 COROS 的檔案。現況：`_file_date`（`backend/engine/racepower/cptest.py:127`）
  兩種檔名都讀，且只掃資料來源的資料夾、跳過另一個（`unused_folder`，`backend/engine/racepower/cptest.py:137`）。
- 同來源內的重複（coros→coros 9 筆、tp→tp 7 筆）`FitFolderDataset` 不去掉；mean-max 取最大值，
  所以不影響 envelope。

---

## Non-Functional Requirements

| NFR | Target |
|-----|--------|
| Coros API timeout | 30s per list/auth request |
| FIT download timeout | 60s per file |
| 單次活動失敗 | 記錄 error event，繼續下一筆，不中斷 sync |
| Token 儲存 | DB 內以 `secrets.seal`（Fernet，本機金鑰）加密；API 永不回傳 token／密碼 |
| Rate limit | 活動下載無強制延遲，依實測調整 |
| Token 刷新 | 有記住密碼時自動重新登入一次；否則狀態為「登入已過期」，設定頁重新顯示登入表單、總覽／課表顯示橫幅 |
| 登入檢查頻率 | 每來源最多每 300 s 一次認證呼叫（連不上時 60 s 後重試） |

---

## Risks

| Risk | Likelihood | Impact | Mitigation | 現況 |
|------|-----------|--------|------------|------|
| Coros API endpoint 改版 | Medium | High | 追蹤 `xballoy/coros-api` issue | 已偵測到 `/activity/fit/url` 失效，改用 `/activity/detail/download` |
| Region server 變動 | Low | High | 每次登入重新偵測 base URL | 已實作 `_detect_data_base()`（`backend/sync/coros_client.py:127`） |
| `yfheader` 要求改版 | Low | Medium | 動態讀取 userId，易調整 | 已實作 |
| Token TTL 縮短 | Low | Medium | 長時間 sync 時 token 可能 mid-sync 失效 | 發生時記 error，提示重新登入 |
| 運動類型無 FIT | Low | Low | `detail/download` 返回 error，已 graceful skip | 已測試 custom (9904)、strength (402) 均 skip |

---

## 已知限制

1. **Token mid-sync 失效**：長時間 sync（>200 筆）偶見 "Access token is invalid"，原因未知（可能 Coros server-side invalidation）。重新登入後繼續 sync 可恢復；有記住密碼時同步中會自動重新登入一次並重試該頁（`{"status": "relogin"}`），再失敗才標成登入已過期。
2. **無功率資料活動的 TSS**：hiking、球類等活動無 power 也無 HR zones，TSS=0，不計入 PMC。
3. **PMC 起始點（歷史資料缺口）**：完整 Coros 歷史資料現已匯入（667 筆 Coros 活動，321 筆跑步）。但 WKO5 本機 `.wko4` 二進位格式的跑步活動仍無法解析 power/HR channel，貢獻 0 TSS。這導致 ATL 與 WKO5 顯示值有差異——WKO5 能讀取 wko4 跑步功率，我們不能。
4. ~~檔名 sport 標籤不準確~~（2026-09-30 已處理）：檔名的運動字改由 FIT session sport／sub_sport 決定，COROS `sportType` 只當後備（`sport_token`，`backend/sync/coros_sport.py`；`SPORT_NAMES` 現為 `COROS_SPORT_TYPES` 的別名，100 = run）；舊的 `*_cycling.fit` 跑步檔用 `backend/scripts/migrate_coros_sport_names.py` 改名。DB 的 `sport` 欄位一直由 `fit_reader.py` 解析 FIT 決定，不受影響。
5. **損壞的 FIT 檔案**：部分 Coros FIT 檔案無效（e.g., 某個 `<labelId>_<date>_other.fit`，FitParseError: Invalid field size）。已修正：`coros_client.py` 現在對無法解析的 FIT 建立 `file_format="corrupt"` 的 stub DB 記錄，避免每次 sync 重複下載。
6. **Coros 功率尖峰**：跑步功率由 Coros 手錶從加速度計/GPS 估算，偶有短暫尖峰（e.g., 某次有 4 個樣本約達 2 × CP）。對 3-30 分鐘 MMP 的 CP 模型計算（runFTP）無影響，但會污染 1–3 秒 MMP 顯示值。配了 Stryd 之後的跑步功率多半來自 Stryd（見「功率來源」）。
7. ~~垃圾功率檔只靠來源規則擋下~~（2026-10-01 已處理）：TP 的那筆約 900 W 的檔現在被「壞掉的活動檔」規則整筆排除（均速 43 km/h），打開 `power.accept_watch_power` 也不會再進 PD 擬合；除非使用者把它標成「這筆是正常的」。
8. **圖表引擎**（WKO5 clone 的 `meanmax(power)`、`ftp(meanmax(power))` 等）照 WKO5 讀全部功率，不套用來源規則。

---

*Generated: 2026-05-15*
*Last updated: 2026-10-08*
*Status: IMPLEMENTED — M4 + runFTP bug fix + corrupt FIT handling + historical data expansion*
