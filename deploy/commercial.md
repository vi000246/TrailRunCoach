# 商業版部署前置（SP-387）

> 這份是開放註冊（SP-390）之前，部署方式要怎麼改的清單。**這張單會一直開到上線前**：
> 有些步驟要登入（SP-58）做好才能測，有些是上線當天才做。
> 規格：`docs/plans/auth-and-demo.plan.md` §3.8、§7、§9。
>
> 標記：👤 擁有者親手做（Claude 做不了，也不該拿到那些值）· 🧑‍💻 程式 · 📅 上線當天。

## 0. 現在的樣子（不動）

- `coach` named tunnel → 密碼 proxy → `:8000`；proxy 只放行 `/share/`。
- 示範主機是另一個 process（`WKO5COACH_MODE=demo`、`:8001`），自己的 tunnel，前面沒有 proxy（§3.8）。

上線前，下面每一步都不能讓擁有者的 `:8000` 少掉密碼那一層，**拿掉 proxy 只在第 3 步、和登入同一次部署**。

## 1. 固定網域與 named tunnel 👤

Google OAuth 的 redirect URI 必須是固定網址；quick tunnel 每次啟動網址都會變，不能用。

1. 決定網域（例如 `<網域>`），交給 Cloudflare 管 DNS。
2. 在 NAS 上（已經有 `cloudflared` 的話沿用）：
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create trc-app                 # 擁有者／商業版主機
   cloudflared tunnel route dns trc-app app.<網域>
   cloudflared tunnel create trc-demo                # 示範主機，維持獨立
   cloudflared tunnel route dns trc-demo demo.<網域>
   ```
3. `~/.cloudflared/config.yml`（示意；憑證檔路徑照 `tunnel create` 的輸出）：
   ```yaml
   tunnel: trc-app
   credentials-file: /home/<you>/.cloudflared/<trc-app-id>.json
   ingress:
     - hostname: app.<網域>
       service: http://localhost:8080        # 上線前：密碼 proxy；上線那天改成 http://localhost:8000
     - service: http_status:404
   ```
   示範主機另一份 config，`service: http://localhost:8001`。
4. 驗收：`https://app.<網域>` 打得開（還是會先問密碼）；`https://demo.<網域>` 打開是示範資料，讀不到真實資料（§3.8 的三層保證）。

## 2. Google OAuth client 👤

只用 `openid email profile` 這三個 scope，不需要 Google 審核。

1. Google Cloud Console → 建一個專案 → **APIs & Services › OAuth consent screen**：
   - User type：External；App name；User support email；Developer contact email。
   - **Privacy policy URL**：SP-60 的隱私權政策網址（文件要先有；沒有就先不要 Publish）。
   - Scopes：`openid`、`.../auth/userinfo.email`、`.../auth/userinfo.profile`。
2. **Credentials › Create credentials › OAuth client ID**：Web application。
   - Authorized redirect URIs：`https://app.<網域>/oauth/google/callback`；開發時另加 `http://localhost:8000/oauth/google/callback`（SP-58 開發用，不擋這張）。
3. client id 與 secret **只放 NAS 的 env 或 Docker secret**（第 5 節），不交給 Claude、不 commit、不貼到 issue 或對話裡。

## 3. 拿掉密碼 proxy 📅（和 SP-58 登入上線同一次部署）

- app 自己做登入之後，前面那層密碼會擋住 OAuth callback 和公開路徑（§7）。
- **不能提早拿**：app 的登入還沒上線就拿掉，等於把擁有者的資料公開。
- 做法：tunnel 的 `service` 從 proxy 改指 `http://localhost:8000`，重啟 cloudflared；proxy 容器停掉。
- 驗收（上線當天）：沒登入時只有第 4 節的白名單路徑打得開，其他回 401 或導到登入頁。

## 4. 公開路徑白名單 🧑‍💻（SP-58 實作，這裡只記清單）

登入攔截的中介層屬於 SP-58，**不在這張先寫**（SP-387 的範圍只到部署）。沒登入可以開的路徑：

| 路徑 | 為什麼 |
|---|---|
| `/login`、`/signup` | 登入與註冊畫面 |
| `/oauth/*/callback` | Google 回呼 |
| `/api/v1/session` | 頁面殼要知道「現在是誰」（CSRF token 也從這裡拿） |
| `/share/*`（含 `/share/calendar/<token>.ics`） | 公開分享與課表訂閱（靠長 token，§SP-232） |
| `/api/v1/static/*` | 靜態檔 |
| `/healthz` | 監控 |

其他路徑都要登入。測試要等 SP-58。

## 5. 密鑰：只走 env 或 Docker secret 🧑‍💻✅

程式只從環境讀這些值（`backend/settings/env_secrets.py`），每一個都可以改用 `<名字>_FILE` 指向一個檔（Docker / compose secrets 掛在 `/run/secrets/`）：

| 名字 | 用途 | 單 |
|---|---|---|
| `WKO5COACH_SECRET_KEY` | 封存所有同步憑證的 server key；多使用者模式必填 | SP-384 |
| `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET` | Google 登入 | SP-58 |
| `PAYMENT_MERCHANT_ID`、`PAYMENT_HASH_KEY`、`PAYMENT_HASH_IV` | 金流（名字待 SP-328 選定金流商後確認） | SP-328 |
| `TP_CLIENT_ID`、`TP_CLIENT_SECRET` | TP 的 WKO5 client（只限擁有者） | — |
| `CWA_API_KEY` | 中央氣象署授權碼（只限擁有者） | — |
| `TRC_DEBUG_PIN` | Debug API 的 PIN | SP-371 |

- **log 裡不印**：每一行 log（app.log、perf.log、`docker logs`）都經過 `applog.redact()`；除了原本的樣式規則（key=value、Bearer、長字串、email），現在也會把上表中**目前有設定的值**整段換成 `***`，不管它長什麼樣（`backend/tests/test_env_secrets_sp387.py`）。
- **不進任何租戶資料夾**：多使用者模式下 server key 只吃 env（`ServerKeyRequired`，SP-384）；擁有者的 `~/.wko5coach/secret.key` 要搬進 env，步驟在 `docs/deploy/accounts.md` §2。
- compose 範例（NAS，repo 外的 `.env` 不 commit）：
  ```yaml
  services:
    wko5coach:
      env_file: /volume1/trc/.env            # WKO5COACH_ACCOUNTS、GOOGLE_CLIENT_ID …
      environment:
        - WKO5COACH_SECRET_KEY_FILE=/run/secrets/trc_secret_key
        - GOOGLE_CLIENT_SECRET_FILE=/run/secrets/google_client_secret
      secrets: [trc_secret_key, google_client_secret]
  secrets:
    trc_secret_key:
      file: /volume1/trc/secrets/trc_secret_key          # chmod 600，不在任何租戶資料夾裡
    google_client_secret:
      file: /volume1/trc/secrets/google_client_secret
  ```
- 驗收：`docker logs wko5coach 2>&1 | grep -F "<secret 的前 8 個字>"` 與 `grep -rF` app.log 都找不到。

## 5b. repo 轉 private 之後，NAS 用唯讀 deploy key 拉 repo 👤（SP-350）

1. NAS 上產生一把只給這個 repo 用的 key（不設密碼，因為部署腳本要自動拉）：
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/trc_deploy -C "nas-deploy trailruncoach" -N ""
   cat ~/.ssh/trc_deploy.pub
   ```
2. GitHub → repo → **Settings → Deploy keys → Add deploy key**：貼上 `.pub`，**不要勾** Allow write access。
3. NAS 的 `~/.ssh/config`：
   ```
   Host github-trc
     HostName github.com
     User git
     IdentityFile ~/.ssh/trc_deploy
     IdentitiesOnly yes
   ```
4. 把 NAS 上那份 clone 的 remote 換掉：`git remote set-url origin git@github-trc:vi000246/TrailRunCoach.git`，`git fetch` 成功。
5. 重新部署一次（`./deploy.sh` 或 compose `up --build -d`），確認版本號（Debug API 的 `TRC_GIT_SHA`）是新的。
6. 其他會拉這個 repo 的地方：
   - 其他電腦的 clone：用自己的 GitHub 帳號登入（private repo 你本人有權限），不用 deploy key。
   - vault 的 `repo-docs/` 鏡像：從本機 clone 讀，不經 GitHub，不受影響。
   - Claude 的雲端 session：靠 GitHub App 授權，private 後照常（App 要裝在這個 repo 上）。
   - GitHub Actions：目前沒有 workflow；之後加的話 private repo 每月有免費分鐘數。

## 6. 部署的營運規則

- **動到引擎模組或升判讀版本的部署，集中在每週一次深夜**（SP-338）。這類部署會讓所有租戶的衍生快取失效、要重算；集中在深夜一次，重算排進 SP-338 的全域佇列，最近 7 天有登入或同步的租戶先補。
- 只改畫面、文案、設定頁的部署不受限，隨時可以上。
- 判斷「有沒有動到引擎」：diff 碰到 `backend/engine/**`、`fitcache` 的版本號、`CACHE_VERSION`、`INDEX_VERSION`、`ALGO_VERSION` 任一個就算。

## 7. NAS 備份 🧑‍💻✅（整台伺服器，SP-384 之後）

多使用者之後，每日備份要涵蓋 `accounts.db` 與所有 `users/<id>/`（接 SP-355）。擁有者自己的備份（設定 › 備份）刻意**不含**別人的資料（`data_registry`：`accounts.db`、`users/**`、`site.json` 是伺服器自己的檔案，`backup=never`），所以另有一支整台伺服器的備份：`backend/accounts/server_backup.py`。

- 👤 設 `WKO5COACH_SERVER_BACKUP_DIR`（NAS 上、伺服器資料夾以外的共用資料夾，例如 `/volume1/trc-backups`；放在伺服器資料夾裡面會被拒絕）。要連 FIT 原檔一起備份再加 `WKO5COACH_SERVER_BACKUP_FIT=1`。
- 多使用者模式下，排程每天跑一次（和擁有者的每日備份同一個迴圈）；也可以手動：`python -m backend.accounts.server_backup`。
- 每次一個資料夾 `server-YYYYMMDD-HHMMSS/`：`accounts.db` 快照、`site.json`、每位使用者一個 SP-355 格式的 zip（DB 不含同步憑證，加上使用者自己的檔案）、`manifest.json`（人數、大小、失敗的使用者）。保留最近 7 次。
- 某一位使用者備份失敗不影響其他人，記在 manifest。
- 測試：`backend/tests/test_server_backup_sp387.py`。
- 👆 上線前在 NAS 實際跑一次 `python -m backend.accounts.server_backup`，打開其中一個使用者的 zip 確認有 `wko5coach.db` 和 `files/`。

## 完成條件對照

| 完成條件 | 狀態 |
|---|---|
| 固定網域可以連到 app；示範主機在另一個 hostname，讀不到真實資料 | 👤 第 1 節 |
| Google OAuth client 建好、redirect 指向固定網域、同意畫面有隱私權政策網址 | 👤 第 2 節（隱私權政策等 SP-60） |
| 登入上線那次部署同時拿掉密碼 proxy；沒登入只有白名單打得開 | 📅 第 3、4 節（等 SP-58） |
| 所有密鑰只在 env 或 secret，`docker logs` 與 app log 都搜不到 | ✅ 第 5 節 |
| `deploy/` 文件寫明步驟與「引擎部署每週一次深夜」 | ✅ 本文件 |
| 每日備份包含 `accounts.db` 與所有 user 租戶目錄 | ✅ 第 7 節（👆 NAS 實跑一次） |
