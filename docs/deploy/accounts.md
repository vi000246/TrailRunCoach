# 多使用者模式：帳號資料庫、擁有者遷移與 Google 登入（SP-384、SP-58）

> 狀態：資料面（SP-384）與 Google 登入（SP-58）都已完成。**登入模式在啟動時決定一次、之後不會變**：
> `WKO5COACH_ACCOUNTS=1` 加上整組登入設定才是登入模式；一個登入設定都沒有就是原本的擁有者模式；
> 設了一部分（或讀不到 secret 檔）就**拒絕啟動**（§5.1）。
> 正式環境要等 SP-387 的前置（固定網域、OAuth client、拿掉 Cloudflare Access）同一次部署才打開，部署後照 §5.6 驗。
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

### 5.1 模式：啟動時決定一次

模式在啟動時（`backend/accounts/login.py` 的 `decide()`）決定、凍結，之後每個請求都只看這個結果，不會再讀環境變數
（secret 檔後來讀不到、env 被改，都不會讓某個請求變回擁有者模式）。`/healthz` 回 `{"ok": true, "mode": "signin" | "owner"}`。

| 設定 | 模式 |
|---|---|
| 一個登入設定都沒有（`WKO5COACH_ACCOUNTS` 沒設、`0`，或只有 `1`） | **owner**：每個請求都是擁有者，跟 SP-58 之前完全一樣；`/login` 等登入路徑回 404。`WKO5COACH_PUBLIC_URL` 不算登入設定——它是全站網址（課表訂閱的連結也用，SP-54），擁有者模式照樣可以設（2026-10-13 使用者決定）；開了登入才必填 |
| `WKO5COACH_ACCOUNTS=1` ＋ §5.2 整組 | **signin**：沒登入只有公開路徑打得開（§5.3），其他 API 回 401、頁面導到 `/login` |
| 示範 instance（`WKO5COACH_MODE=demo`） | 不受影響（不掛登入路由） |
| 其他任何組合 | **拒絕啟動**（`LoginConfigError`，訊息寫明缺什麼）。例如：設了 Google client 但 `WKO5COACH_ACCOUNTS` 沒開；開了但少 secret、少 `WKO5COACH_PUBLIC_URL`；`*_FILE` 讀不到；`WKO5COACH_PUBLIC_URL` 不是 https（localhost 除外）或帶路徑；https 但 `WKO5COACH_TRUSTED_PROXIES` 沒設或還是預設值；擁有者的 Google 帳號認不出來（user 1 還沒綁，也沒設 `WKO5COACH_OWNER_*`）；`WKO5COACH_ACCOUNTS` 不是 on／off 的值 |

「登入設定」指：`GOOGLE_CLIENT_ID[_FILE]`、`GOOGLE_CLIENT_SECRET[_FILE]`、`WKO5COACH_OWNER_GOOGLE_SUB`、`WKO5COACH_OWNER_EMAIL`、`WKO5COACH_PUBLIC_URL`。
**部署前先檢查 NAS 的 `.env`**：如果已經預先放了其中任何一個，但 `WKO5COACH_ACCOUNTS` 還沒開，這個版本會拒絕啟動。

### 5.2 環境變數

| 名字 | 用途 |
|---|---|
| `WKO5COACH_ACCOUNTS=1` | 多使用者開關（SP-384）；也要 `WKO5COACH_SECRET_KEY` |
| `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET` | Google OAuth client（Web application）。只從 env 或 `*_FILE`（Docker secret）讀，見 `deploy/commercial.md` §2、§5 |
| `WKO5COACH_PUBLIC_URL` | **必填**。固定的對外網址，只有 origin，例如 `https://coach.yichlin.com`；redirect URI 是 `<它>/oauth/google/callback`。一定要 https；本機開發可用 `http://localhost:8000` |
| `WKO5COACH_TRUSTED_PROXIES` | https 時**必填**。只填 cloudflared 容器的位址（`/32`），例如 `172.18.0.5/32`。app 只在直接連線端是它時才採用 `CF-Connecting-IP`，登入限流才算得到真正的使用者 IP；填整段 Docker 網段的話，同網段的任何容器都能假造 IP |
| `WKO5COACH_OWNER_GOOGLE_SUB` | 擁有者 Google 帳號的 `sub`（Google 帳號的固定 ID）。**建議用這個**；設了就只認這個 sub，email 不算 |
| `WKO5COACH_OWNER_EMAIL` | 沒有 sub 時用：Google 說已驗證、而且等於這個 email 的帳號是擁有者。只設這個時啟動會記一行 warning 建議改用 sub |
| `WKO5COACH_MAX_USERS` | 註冊上限，預設 100（擁有者與停用的帳號也算）。到上限後新的 Google 帳號只記進候補名單（`waitlist`），不建帳號與租戶 |
| `WKO5COACH_COOKIE_SECURE=0` | 只限 `http://localhost` 開發；`WKO5COACH_PUBLIC_URL` 是 https 時不管它設什麼，登入 cookie 一律 `Secure` |

**app 的埠只給 cloudflared 連**：`8000` 只綁 `127.0.0.1`（`ports: ["127.0.0.1:8000:8000"]`），或乾脆不發布、讓 cloudflared 走同一個
Docker network。repo 的 `docker-compose.yml` 目前是 `"8000:8000"`（所有介面都開），**這張單沒有改它**：NAS 上 cloudflared 怎麼連進來要
擁有者確認後再改，避免把現在的部署弄壞。

### 5.3 公開路徑與 cookie

`/login`、`/signup`、`/oauth/*/callback`、`/api/v1/account/login|signup|logout`、`/api/v1/session`、`/api/v1/static/*`、`/healthz`、
四份法律文件 `/legal`、`/legal/terms|privacy|health|third_party`（SP-60，完整路徑比對），
`/sw.js`、`/manifest.webmanifest`（SP-314 離線快取與加到主畫面；兩個檔都沒有任何人的資料，瀏覽器抓 manifest 時不帶 cookie），
以及 `/share/*`（照舊讀擁有者的分享與課表訂閱，不看 session；`accounts.db` 的 `shares` 表還沒有分享頁在寫，所以還沒辦法對到別的使用者）。
其他路徑都要登入；寫入（非 GET／HEAD）一律檢查 CSRF（`X-TRC-CSRF` 等於 CSRF cookie，跟示範模式同一套）。路徑裡有 `/./`、`/../`、`//`、
反斜線或編碼過的 `/`、`\`、`.`（`%2f`、`%5c`、`%2e`）一律回 400。沒登入的請求沒有租戶（讀資料會失敗），不會變成擁有者。

登入模式的 cookie 在 https 下都用 `__Host-` 前綴（`Secure`、`Path=/`、沒有 `Domain`、`SameSite=Lax`）：
`__Host-trc_session`、`__Host-trc_oauth`、`__Host-trc_signup`、`__Host-trc_csrf`。擁有者模式與示範模式的 cookie 名稱不變（`trc_csrf`、`trc_demo`）。

### 5.4 擁有者第一次登入

1. 照 §2 遷移完（`WKO5COACH_ACCOUNTS=1`、server key 在 env）。
2. 在 `.env` 設 `WKO5COACH_OWNER_GOOGLE_SUB`（或 `WKO5COACH_OWNER_EMAIL=<你的 Gmail>`），加上 Google client、`WKO5COACH_PUBLIC_URL`、`WKO5COACH_TRUSTED_PROXIES`，重新啟動。
3. 打開網站 → 用 Google 登入 → 應該直接回到總覽，看到原本的資料（user 1；擁有者不需要同意條款）。
4. 這一次登入就把這個 Google 帳號綁到 user 1；之後只有這個帳號（同一個 `sub`）是擁有者，env 的 email 改了或別人用同一個 email
   開了新的 Google 帳號，都搶不走 user 1。要換擁有者的 Google 帳號只能手動改 `accounts.db`（`users.google_sub`、`identities`）。
5. **擁有者登入之前，其他人一律不能註冊**（畫面顯示「網站還在設定中」）：env 的 email 打錯字時，擁有者不會被建成一個新的空帳號。

### 5.5 一般使用者

- 第一次用 Google 登入：先到 `/signup` 看四份文件（`backend/legal/`，目前是草稿，待 SP-60），勾選同意才建帳號與自己的租戶，
  之後進到總覽，首次精靈（性別、年齡、體重、跑步經驗問卷）自己會跳出來。
- 同意紀錄：`consents` 表（使用者、條款版本、使用者看到的那幾份文件的 sha256、時間）。頁面送出時要帶回它看到的那組 digest，對不上就要求重看。
- **改條款**：改 `backend/legal/` 的文件時，一定要同時改 `backend/accounts/terms.py` 的 `VERSION`，並把新的 digest 記進 `PINNED`
  （`test_terms_digests_are_pinned_to_the_version` 會擋）。改版後，已登入的人下一個請求就被擋下（API 403 `TERMS_REQUIRED`，
  頁面導到 `/signup`），同意後繼續用；擁有者不受影響。
- Google email 沒驗證的帳號、被停用的帳號（`store.set_disabled`）不能登入。
- Session：只存 token 的 sha256；30 天，使用中每小時最多延長一次，**最長登入後 90 天**一定要重新登入；每次登入換新 token；登出刪掉那一列。
- 限流：每個用戶端（IPv6 以 /64 計）15 分鐘內最多 10 次開始登入；回呼只有通過 state 檢查的才算，同樣 15 分鐘 10 次（亂打回呼不會用掉別人的額度）。

### 5.6 部署後的 smoke test

拿掉 Cloudflare Access 之前與之後各跑一次（從外網）：

```bash
curl -s https://coach.yichlin.com/healthz                                   # {"ok":true,"mode":"signin"}
curl -s -o /dev/null -w '%{http_code}\n' https://coach.yichlin.com/api/v1/plan   # 一定要是 401
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' -H 'Accept: text/html' \
     https://coach.yichlin.com/api/v1/overview/page                          # 302 → /login?next=…
```

`/healthz` 不是 `signin`、或 `/api/v1/plan` 不是 401，就**不要拿掉 Access**，先查 `.env`。

## 6. 還沒做的（別的單）

- 拿掉密碼 proxy（Cloudflare Access）、固定網域、OAuth client 申請：SP-387
- 條款正式內容：SP-60（換掉 `backend/legal/` 的草稿並改 `terms.VERSION`）
- 刪除帳號、下載我的資料：SP-386 已做（設定 › 個人資料 › 帳號）。半途失敗的刪除（帳號停用、原因 `deleting`）或用 e-mail 申請的刪除，在伺服器上跑 `python -m backend.accounts.deletion <user id>`（不會刪 user 1）
- 方案與權限 scope（`user`／`subscriber`／`owner`）：SP-330
- 管理後台：SP-351
