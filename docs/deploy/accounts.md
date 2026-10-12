# 多使用者模式：帳號資料庫、擁有者遷移與 Google 登入（SP-384、SP-58）

> 狀態：資料面（SP-384）與 Google 登入（SP-58）都已完成。**登入只在 `WKO5COACH_ACCOUNTS=1` 而且設了
> `GOOGLE_CLIENT_ID`＋`GOOGLE_CLIENT_SECRET` 時才啟用**；少任何一個，每個請求都還是擁有者，跟以前一樣（§5.1）。
> 正式環境要等 SP-387 的前置（固定網域、OAuth client、拿掉 Cloudflare Access）同一次部署才打開。
> 規格：`docs/plans/auth-and-demo.plan.md` §2.2、§4、§5、§7。

## 1. 放在哪裡

| 東西 | 位置 | 說明 |
|---|---|---|
| `accounts.db` | `<server-root>/accounts.db` | users、identities、sessions（只存 token 的 sha256）、shares、waitlist、consents（同意的條款版本與時間）、登入中的 state（10 分鐘）與註冊單（30 分鐘） |
| 使用者的資料 | `<server-root>/users/<id>/` | 一個人一個資料夾，結構和 `~/.wko5coach` 一樣（自己的 `wko5coach.db`、`plan.json`、`fit/`、快取） |
| 擁有者的資料 | `~/.wko5coach`（不動） | `users` 第 1 列，`role=owner`，`tenant_dir` 原地指向這裡 |

`<server-root>` 是 `WKO5COACH_SERVER_ROOT`，沒設就是 `WKO5COACH_HOME`（`~/.wko5coach`）。
`accounts.db` 和 `users/` 在資料登錄表（`backend/data_registry.py`）標成伺服器自己的檔案，**不會進擁有者的備份**——那是別人的資料。
整台伺服器的備份（含 `accounts.db` 與所有 `users/`）在 SP-387 第 7 條。

## 2. 擁有者遷移（原地，零搬移）

1. 停掉 app（`docker compose stop`），把 `~/.wko5coach` 整個複製一份當備份。
2. **把 server key 搬進環境變數**：多使用者模式只吃 `WKO5COACH_SECRET_KEY`，不讀任何租戶資料夾裡的 key 檔
   （擁有者的 `~/.wko5coach` 也是一個租戶資料夾）。
   ```bash
   cat ~/.wko5coach/secret.key      # 把這串貼進 NAS 的 .env：WKO5COACH_SECRET_KEY=<那串>
   ```
   沒設就打開多使用者模式，app 啟動時會停下來（`ServerKeyRequired`），不會另外產生一把新的 key。
   同一把 key 繼續解得開既有的 COROS／TP 憑證；確認 app 正常之後，舊的 `secret.key` 檔可以移走。
3. 在 `.env` 加 `WKO5COACH_ACCOUNTS=1`，啟動。啟動時會建 `accounts.db`，`users` 第 1 列是擁有者、指向現有資料夾（重跑不會重建）。
4. 驗證：擁有者登入後，總覽、PMC、課表 API 的回應要和遷移前完全相同
   （自動測試：`backend/tests/test_accounts_sp384.py::test_the_owner_answers_the_same_signed_in_as_before`）。

擁有者登入後仍然是原本的租戶（id `owner`、同一個資料夾、WKO5 資料夾、所有權限），所以快取、課表、同步狀態都不會變。

## 3. 只屬於擁有者的東西

一般使用者的租戶讀不到（`tenancy.OWNER_ONLY_CAPS`；`backend/tests/test_accounts_sp384.py`）：

- WKO5 資料夾（各模組的 `ATHLETE_DIR` 一律經過 `settings/paths.for_tenant`）
- 中央氣象署授權碼（`CWA_API_KEY` 或 `weather.json`）：使用者的天氣改用 Open-Meteo
- TrainingPeaks 的 WKO5 client（`TP_CLIENT_ID`／`tp_client.json`）：TP 條款不准把 developer credentials 給別人
- 整個資料夾的備份與還原（使用者要的是「下載我的資料」，SP-386）
- 擁有者的 AI 金鑰（SP-306 做了之後）

## 4. 每日自動同步

排程每分鐘掃一次所有租戶（擁有者＋每個沒停用的使用者），在各自的資料庫裡讀各自的
`sync.schedule.daily_time`。使用者的時間會固定往後錯開 0～29 分鐘（依 id 算，每天一樣），
擁有者不錯開。每週 60 天檢查也逐租戶跑；補雨量與每日備份只跑擁有者的。

## 5. Google 登入（SP-58）

### 5.1 開關：什麼時候要求登入

| `WKO5COACH_ACCOUNTS` | Google client（ID＋SECRET 都有） | 結果 |
|---|---|---|
| 沒設 | 任何 | 單人模式：每個請求都是擁有者，跟 SP-58 之前完全一樣；`/login` 等登入路徑回 404 |
| `1` | 沒有或只有一半 | 多使用者資料面（SP-384），但**不要求登入**，每個請求還是擁有者；啟動時 log 一行警告 |
| `1` | 有 | **要求登入**：沒登入只有公開路徑打得開（§5.3），其他 API 回 401、頁面導到 `/login` |
| 示範 instance（`WKO5COACH_MODE=demo`） | 任何 | 不受影響（示範模式永遠不是多使用者模式，也不掛登入路由） |

要求登入時，啟動會先確認**擁有者的 Google 帳號**認得出來（user 1 已經綁過，或設了下面其中一個 env）；認不出來就停在啟動
（`LoginConfigError`），不會帶著「擁有者登不進自己資料」的狀態上線。

### 5.2 環境變數

| 名字 | 用途 |
|---|---|
| `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET` | Google OAuth client（Web application）。只從 env 或 `*_FILE`（Docker secret）讀，見 `deploy/commercial.md` §2、§5 |
| `WKO5COACH_PUBLIC_URL` | 固定的對外網址，例如 `https://coach.yichlin.com`；redirect URI 是 `<它>/oauth/google/callback`。沒設就用請求自己的網址（本機開發 `http://localhost:8000` 可以；在 Cloudflare 後面一定要設，否則 app 看到的是 `http://`） |
| `WKO5COACH_OWNER_GOOGLE_SUB` | 擁有者 Google 帳號的 `sub`（Google 帳號的固定 ID）。設了就**只認這個 sub**，email 不算 |
| `WKO5COACH_OWNER_EMAIL` | 沒有 sub 時用：Google 說已驗證、而且等於這個 email 的帳號是擁有者 |
| `WKO5COACH_MAX_USERS` | 註冊上限，預設 100（擁有者與停用的帳號也算）。到上限後新的 Google 帳號只記進候補名單（`waitlist`），不建帳號與租戶 |
| `WKO5COACH_COOKIE_SECURE=0` | 只限本機 http 開發（cookie 不加 `Secure`）；正式環境不要設 |

### 5.3 公開路徑

`/login`、`/signup`、`/oauth/*/callback`、`/api/v1/account/login|signup|logout`、`/api/v1/session`、`/api/v1/static/*`、`/healthz`，
以及 `/share/*`（照舊讀擁有者的分享與課表訂閱，不看 session）。其他路徑都要登入；寫入（非 GET／HEAD）一律檢查 CSRF
（`X-TRC-CSRF` 等於 `trc_csrf` cookie，跟示範模式同一套）。

### 5.4 擁有者第一次登入

1. 照 §2 遷移完（`WKO5COACH_ACCOUNTS=1`、server key 在 env）。
2. 在 `.env` 設 `WKO5COACH_OWNER_EMAIL=<你的 Gmail>`（或 `WKO5COACH_OWNER_GOOGLE_SUB`），加上 Google client 與 `WKO5COACH_PUBLIC_URL`，重新啟動。
3. 打開網站 → 用 Google 登入 → 應該直接回到總覽，看到原本的資料（user 1；擁有者不需要同意條款）。
4. 這一次登入就把這個 Google 帳號綁到 user 1；之後只有這個帳號（同一個 `sub`）是擁有者，env 的 email 改了或別人用同一個 email
   開了新的 Google 帳號，都搶不走 user 1。要換擁有者的 Google 帳號只能手動改 `accounts.db`（`users.google_sub`、`identities`）。

### 5.5 一般使用者

- 第一次用 Google 登入：先到 `/signup` 看四份文件（`backend/legal/`，目前是草稿，待 SP-60），勾選同意才建帳號與自己的租戶，
  之後進到總覽，首次精靈（性別、年齡、體重、跑步經驗問卷）自己會跳出來。
- 同意紀錄：`consents` 表（使用者、條款版本 `backend/accounts/terms.py` 的 `VERSION`、每份文件的 sha256、時間）。
  改了任何一份文件就要改 `VERSION`，所有人下次登入時會被要求重新同意。
- Google email 沒驗證的帳號、被停用的帳號（`store.set_disabled`）不能登入。
- Session：只存 token 的 sha256；30 天，使用中每小時最多延長一次；每次登入換新 token；登出刪掉那一列。
- 限流：同一個 IP 15 分鐘內最多 10 次開始登入、10 次回呼。

## 6. 還沒做的（別的單）

- 拿掉密碼 proxy（Cloudflare Access）、固定網域、OAuth client 申請：SP-387
- 條款正式內容：SP-60（換掉 `backend/legal/` 的草稿並改 `terms.VERSION`）
- 刪除帳號、下載我的資料：SP-386
- 方案與權限 scope（`user`／`subscriber`／`owner`）：SP-330
- 管理後台：SP-351
