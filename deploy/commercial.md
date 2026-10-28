# 商業版部署前置（SP-387）

> 這份是開放註冊（SP-390）之前，部署方式要怎麼改的清單。**這張單會一直開到上線前**：
> 有些步驟要登入（SP-58）做好才能測，有些是上線當天才做。
> 規格：`docs/plans/auth-and-demo.plan.md` §3.8、§7、§9。
>
> 標記：👤 擁有者親手做（Claude 做不了，也不該拿到那些值）· 🧑‍💻 程式 · 📅 上線當天。

## 0. 現在的樣子（不動）

- `coach.yichlin.com` 已經走 Cloudflare **named tunnel** 到 NAS 的 `:8000`（SP-65，2026-10-05 設好）。
- 外網進來先經過 **Cloudflare Access**（Zero Trust）的登入頁；`/share` 另外設了 Bypass。這就是 auth plan 裡說的「密碼 proxy」。
- 示範主機還沒有自己的 hostname；沒有線上的公開示範（GitHub Pages 的靜態示範 2026-10-14 下線、程式刪除，SP-500）。
- 部署：image 在 Mac build、傳到 NAS `docker load`，NAS 不 build、不連 GitHub（`docs/deploy/wko5coach.deploy.md`「正式環境：NAS」）。

上線前，下面每一步都不能讓 `coach.yichlin.com` 少掉 Access 那一層，**拿掉 Access 只在第 3 步、和登入同一次部署**。

## 1. 網域與 tunnel ✅（已經有，不用另外準備）

- Google OAuth 的 redirect 用 `https://coach.yichlin.com/oauth/google/callback`（固定網址，tunnel 是 named tunnel，不是每次會變的 quick tunnel）。
- 示範主機要上線時，在同一條 tunnel 加一個 public hostname（Cloudflare Zero Trust › Networks › Tunnels › 這條 tunnel › Public Hostname），例如
  伺服器版示範（`TRC_MODE=demo`）：`try.yichlin.com` → `http://localhost:8001`，前面**不**加 Access（§3.8）。
- 驗收：`https://coach.yichlin.com` 照舊先問 Access；示範主機打得開、讀不到真實資料（§3.8 的三層保證）。

## 2. Google OAuth client 👤

只用 `openid email profile` 這三個 scope，不需要 Google 審核。

1. Google Cloud Console → 建一個專案 → **APIs & Services › OAuth consent screen**：
   - User type：External；App name；User support email；Developer contact email。
   - **Privacy policy URL**：`https://coach.yichlin.com/legal/privacy`（SP-60；沒登入也打得開）。**Terms of service URL** 可以填 `https://coach.yichlin.com/legal/terms`。
   - 文件還是草稿（`backend/accounts/terms.py` 的 `DRAFT = True`，頁面標「草稿，尚待律師審閱」）時先不要 Publish；律師審閱後的版本上線再 Publish。
   - Scopes：`openid`、`.../auth/userinfo.email`、`.../auth/userinfo.profile`。
2. **Credentials › Create credentials › OAuth client ID**：Web application。
   - Authorized redirect URIs：`https://coach.yichlin.com/oauth/google/callback`；開發時另加 `http://localhost:8000/oauth/google/callback`（SP-58 開發用，不擋這張）。
3. client id 與 secret **只放 NAS 的 env 或 Docker secret**（第 5 節），不交給 Claude、不 commit、不貼到 issue 或對話裡。

## 3. 拿掉 Cloudflare Access 📅👤（和 SP-58 登入上線同一次部署）

- app 自己做登入之後，Access 那層會擋住 OAuth callback 和公開路徑（auth plan §7）。
- **不能提早拿**：app 的登入還沒上線就拿掉，等於把擁有者的資料公開。
- 做法：Cloudflare Zero Trust › Access › Applications → `coach.yichlin.com` 那個 application → 刪除（或把 policy 改成 Bypass everyone）；`/share` 的 Bypass 規則一起不需要了。
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
| `/legal`、`/legal/terms`、`/legal/privacy`、`/legal/health`、`/legal/third_party` | 四份法律文件（SP-60）；Google OAuth 同意畫面要填隱私權政策網址。只開這幾個完整路徑，旁邊的路徑（`/legal/x`、`/legal/privacy/…`）照樣要登入 |
| `/sw.js`、`/manifest.webmanifest` | 離線快取的 service worker 與加到主畫面的 manifest（SP-314；不含任何人的資料，完全比對） |

其他路徑都要登入。測試要等 SP-58。

## 5. 密鑰：只走 env 或 Docker secret 🧑‍💻✅

程式只從環境讀這些值（`backend/settings/env_secrets.py`），每一個都可以改用 `<名字>_FILE` 指向一個檔（Docker / compose secrets 掛在 `/run/secrets/`）：

| 名字 | 用途 | 單 |
|---|---|---|
| `TRC_SECRET_KEY` | 封存所有同步憑證的 server key；多使用者模式必填 | SP-384 |
| `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET` | Google 登入 | SP-58 |
| `RESEND_API_KEY` | email 註冊／重設密碼的寄信（Resend）；沒設時信只印在 stderr、不寄出（`docs/deploy/accounts.md` §5.8） | SP-610 |
| `TRC_POLAR_TOKEN`、`TRC_POLAR_WEBHOOK_SECRET` | 訂閱付款：Polar 的 Organization Access Token 與 webhook secret（同一組金流商戶與 inReachLiveTrack 共用；非密鑰的設定 `TRC_POLAR_ENV`、`TRC_POLAR_PRODUCT_MONTH`、`TRC_POLAR_PRODUCT_YEAR` 見 `docs/deploy/billing.md`） | SP-328 |
| `TP_CLIENT_ID`、`TP_CLIENT_SECRET` | TP 的桌面軟體 client（只限擁有者） | — |
| `CWA_API_KEY` | 中央氣象署授權碼（只有擁有者能看、能設；站台用它下載的預報全站共用） | SP-552 |
| `TRC_UPSTREAM_TOKEN`、`TRC_PROXY_PASSWORD` | 本機副本從正式站同步用的 token 與代理密碼（只設在本機，不設在正式站；SP-373 起 `TRC_DEBUG_PIN` 不再讀） | SP-373 |
| `TRC_ANTHROPIC_API_KEY` | AI 問答呼叫 Claude 的 API key（只限擁有者；沒設也可以在設定 › 進階設定 › AI 問答貼上，加密存在擁有者資料夾的 `ai.json`）。非密鑰的設定：`TRC_AI_MODEL`（預設 `claude-sonnet-5-5`）、`TRC_AI_DAILY_LIMIT`（預設 30，每人各自算）、`TRC_AI_MEMBER_KEYS`（預設關；設 `1` 讓所有登入會員用**自己的** key，費用記在他們的 Anthropic 帳號——打開前先等 SP-881 查完資料來源條款，SP-883）。見 `docs/spec/ai-qa.spec.md` | SP-785、SP-883 |

- **log 裡不印**：每一行 log（app.log、perf.log、`docker logs`）都經過 `applog.redact()`；除了原本的樣式規則（key=value、Bearer、長字串、email），現在也會把上表中**目前有設定的值**整段換成 `***`，不管它長什麼樣（`backend/tests/test_env_secrets_sp387.py`）。
- **不進任何租戶資料夾**：多使用者模式下 server key 只吃 env（`ServerKeyRequired`，SP-384）；擁有者的 `~/.trailruncoach/secret.key` 要搬進 env，步驟在 `docs/deploy/accounts.md` §2。
- compose 範例（NAS，repo 外的 `.env` 不 commit）：
  ```yaml
  services:
    trailruncoach:
      env_file: /volume1/trc/.env            # TRC_ACCOUNTS、GOOGLE_CLIENT_ID …
      environment:
        - TRC_SECRET_KEY_FILE=/run/secrets/trc_secret_key
        - GOOGLE_CLIENT_SECRET_FILE=/run/secrets/google_client_secret
      secrets: [trc_secret_key, google_client_secret]
  secrets:
    trc_secret_key:
      file: /volume1/trc/secrets/trc_secret_key          # chmod 600，不在任何租戶資料夾裡
    google_client_secret:
      file: /volume1/trc/secrets/google_client_secret
  ```
- 驗收：`docker logs trailruncoach 2>&1 | grep -F "<secret 的前 8 個字>"` 與 `grep -rF` app.log 都找不到。

## 5b. 開發移到 private repo 之後的部署：不需要 deploy key（SP-350、SP-500）

**結論：NAS 不需要唯讀 deploy key。** 2026-10-05 起 NAS 不再從 GitHub 拉程式、也不在 NAS 上 build：
image 在 Mac 用本機 clone 的 `git archive HEAD` 建（`linux/amd64`），`docker save | gzip | ssh nas docker load`
傳過去，NAS 只跑 `docker-compose up -d`（步驟：`docs/deploy/wko5coach.deploy.md`「正式環境：NAS」）。
整條路上只有 Mac 的 clone 碰 GitHub，而 Mac 用的是擁有者自己的 GitHub 帳號，repo 轉 private 後照樣能 pull。

之前的寫法（NAS 上產生 `~/.ssh/trc_deploy`、GitHub 加 Deploy key、NAS 的 clone 換 remote）**不用做**。
只有**之後改回「在伺服器上 git pull + build」**（例如換一台夠力的主機、或改用雲端 VM 自動部署）時才需要；
那時的做法：在那台主機產生一把不設密碼的 ed25519 key → GitHub repo **Settings → Deploy keys** 加 `.pub`、
**不要勾** Allow write access → 主機的 `~/.ssh/config` 用一個 `Host github-trc` 別名指定這把 key → clone 的
remote 改成 `git@github-trc:vi000246/TrailRunCoach-private.git`，`git fetch` 成功即可。

👤 轉 private 時順手做（不轉也無妨）：

- NAS 的 `/volume1/docker/trailruncoach/src/` 是 2026-10-05 第一次部署時的舊 clone，現在沒有任何步驟用它；
  轉 private 後它的 `git pull` 會失敗，**可以刪掉**。刪之前先確認 NAS 的 `docker-compose.yml` 是 `image: trailruncoach`、
  沒有 `build:`（有 `build: ./src` 的話，日後誰下了 `up --build` 就會在 NAS 上 build、而且讀不到新程式）。

其他會拉這個 repo 的地方——2026-10-14 起分成兩個 repo（SP-500）：**開發在 private repo
`vi000246/TrailRunCoach-private`**；原本的公開 repo `vi000246/TrailRunCoach` 保留，改成不含計算核心的自動鏡像
（`deploy/public-mirror/README.md`；push 到 private 的 `main` 後由 GitHub Action 自動同步）。

| 地方 | 現在 | 要做什麼 |
|---|---|---|
| NAS 正式環境 | 不受影響：image 從 Mac 的 private clone 傳過去，NAS 不連 GitHub | 無（舊 `src/` clone 見上） |
| Mac / 其他電腦的 clone | `origin` 要指 private repo；舊 clone 的 `origin` 還是公開 repo | 照 `deploy/public-mirror/README.md`「另一台電腦」改 remote，並把公開 repo 的 push 關掉 |
| 公開鏡像 | GitHub Action `public-mirror.yml` 在 push 到 private `main` 後自動 `sync` 並用 deploy key 推（SP-532） | 無；Action 壞掉時照 `deploy/public-mirror/README.md`「手動同步」 |
| vault 的 `repo-docs/` 鏡像 | 從本機 clone 讀，不經 GitHub | 無（讀的是 private 的 clone，內容完整） |
| Claude 的雲端 session | 靠 GitHub App 授權，App 要裝在 **private repo** 上 | 開一個雲端 session 試 clone private repo 一次 |
| GitHub Actions | private repo 只有 `public-mirror.yml`（公開鏡像同步） | 無 |
| GitHub Pages 線上示範 | 2026-10-14 `gh-pages` 已刪、Pages 已關；靜態示範的程式也刪了（不再提供） | 無 |
| Hugging Face Space（`deploy/hf/`） | 不連 GitHub（`make_space.py` 從本機 clone 複製） | 見下 |

`deploy/hf/make_space.py` 會把 `backend/`、`views/` 的**原始碼**（含計算核心）從本機 private clone 複製進 Space repo。
Space 是 public 的話，等於把核心公開一份；要放伺服器版示範就把 Space 設成 private（或改用自己的主機，例如
`try.yichlin.com`，§1），或改成從公開鏡像（`public_mirror.py export`）複製——但公開版沒有核心，示範會算不出圖表。

## 6. 部署的營運規則

- **動到引擎模組或升判讀版本的部署，集中在每週一次深夜**（SP-338）。這類部署會讓所有租戶的衍生快取失效、要重算；集中在深夜一次，重算排進 SP-338 的全域佇列，最近 7 天有登入或同步的租戶先補。
- 只改畫面、文案、設定頁的部署不受限，隨時可以上。
- **背景重算的工作行程**（SP-394 第 5 條）：賽事回測和整批的自動分類在另開的 Python 行程裡算（`backend/workproc.py`），不再拖慢同一時間的頁面請求。容器裡因此最多會多兩個行程（每個量到最高約 270 MB：約 110 MB 程式加上該租戶的 Dataset），算在同一個 `mem_limit` 裡，優先權比伺服器低（nice 10）。行程出問題時可設 `TRC_PROCESS_JOBS=0` 退回在伺服器行程裡算（頁面會在回測期間慢 10～20 倍）。
- **10 個租戶同時重建的記憶體**（SP-394 第 6 條，本機量，約 3 年資料的示範跑者複製 10 份）：整個行程樹（含 FIT 解析與工作行程）峰值 1,961 MB（第一次匯入）／1,979 MB（部署改版）；修正前 3,164 MB，超過容器 3 GB。重量：`python -m backend.scripts.perf_sp394 memory --base <示範資料> --work <暫存資料夾>`。NAS 上 Python 的記憶體用量跟本機差不多（不像 CPU 時間要乘 8.5），但沒在 NAS 量過。
- 判斷「有沒有動到引擎」：diff 碰到 `backend/engine/**`、`fitcache` 的版本號、`CACHE_VERSION`、`INDEX_VERSION`、`ALGO_VERSION` 任一個就算。

## 7. NAS 備份 🧑‍💻✅（整台伺服器，SP-384 之後）

多使用者之後，每日備份要涵蓋 `accounts.db` 與所有 `users/<id>/`（接 SP-355）。擁有者自己的備份（設定 › 備份）刻意**不含**別人的資料（`data_registry`：`accounts.db`、`users/**`、`site.json` 是伺服器自己的檔案，`backup=never`），所以另有一支整台伺服器的備份：`backend/accounts/server_backup.py`。

- 👤 設 `TRC_SERVER_BACKUP_DIR`（NAS 上、伺服器資料夾以外的共用資料夾，例如 `/volume1/trc-backups`；放在伺服器資料夾裡面會被拒絕）。要連 FIT 原檔一起備份再加 `TRC_SERVER_BACKUP_FIT=1`。
- 多使用者模式下，排程每天跑一次（和擁有者的每日備份同一個迴圈）；也可以手動：`python -m backend.accounts.server_backup`。
- 每次一個資料夾 `server-YYYYMMDD-HHMMSS/`：`accounts.db` 快照、`site.json`、每位使用者一個 SP-355 格式的 zip（DB 不含同步憑證，加上使用者自己的檔案）、`manifest.json`（人數、大小、失敗的使用者）。保留最近 7 次。
- 某一位使用者備份失敗不影響其他人，記在 manifest。
- 測試：`backend/tests/test_server_backup_sp387.py`。
- 👆 上線前在 NAS 實際跑一次 `python -m backend.accounts.server_backup`，打開其中一個使用者的 zip 確認有 `wko5coach.db` 和 `files/`。

## 完成條件對照

| 完成條件 | 狀態 |
|---|---|
| Google 登入的回呼 `https://coach.yichlin.com/oauth/google/callback` 從外網打得到 | 等 SP-58（網域與 tunnel 已有，第 1 節） |
| Google OAuth client 建好、redirect 指向固定網域、同意畫面有隱私權政策網址 | 👤 第 2 節（隱私權政策等 SP-60） |
| 登入上線那次部署同時拿掉 Cloudflare Access；沒登入只有白名單打得開 | 📅👤 第 3、4 節（等 SP-58） |
| 所有密鑰只在 env 或 secret，`docker logs` 與 app log 都搜不到 | ✅ 第 5 節 |
| `deploy/` 文件寫明步驟與「引擎部署每週一次深夜」 | ✅ 本文件 |
| 每日備份包含 `accounts.db` 與所有 user 租戶目錄 | ✅ 第 7 節（👆 NAS 實跑一次） |
