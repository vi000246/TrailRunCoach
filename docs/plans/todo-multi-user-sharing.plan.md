# Plan：分享給朋友（多使用者）、中英切換、公開前檢查

- 日期：2026-09-30
- 狀態：**設計，尚未實作**
- 分支基準：`feat/overview-racepower-ui`

## 背景

使用者想把這個 app 分享給跑友，也可能貼到 Palladino 的功率訓練 Facebook 社團。這些人不會自己架伺服器。

使用者的構想：註冊帳號，資料下載到自己電腦，全部維持在免費方案，伺服器成本為零。

本文件只做設計：比較可行架構、列出分享前必須解決的問題、盤點目前程式碼的單人假設，並給出朋友的上手流程、i18n 設計、公開前檢查清單與工作量估計。

---

## 1. 架構選項

### (a) 打包成本機桌面 app

作法：現有 FastAPI 加上靜態頁面，打包成單一安裝檔。

- **打包工具**：PyInstaller 或 Briefcase，或用 Tauri 殼包一個 Python sidecar。
- **執行方式**：啟動後開瀏覽器連 `http://127.0.0.1:<port>`。
- **自動更新**：安裝檔加 update check（GitHub Releases）。
- **帳號**：沒有；資料全在本機 `~/.wko5coach`。

| 面向 | 評估 |
|---|---|
| 成本 | 0（GitHub Releases 免費散布） |
| 非技術使用者的安裝難度 | 低：下載、安裝、開啟。Windows 未簽章會跳 SmartScreen；macOS 要處理 Gatekeeper / notarization，需 Apple Developer，USD 99/年，這是唯一可能的花費 |
| 隱私 | 最佳：活動檔、token 都不離開本機 |
| 現有程式碼可沿用 | ~95%：FastAPI、engine、靜態頁面都不動；只需打包設定、路徑與啟動流程 |
| 缺點 | 換電腦要自己搬資料；沒有跨裝置；手機只能在同網段連 |

### (b) 本機 app ＋ 免費雲端帳號層

作法：(a) 再加一層很薄的雲端帳號服務，例如 Supabase、Firebase 或 Cloudflare D1。

- **雲端只存**：身分、設定備份、加密過的設定匯出，也許再加成就分享連結。
- **活動檔（FIT）** 一律留在本機。

各家免費方案限制（2026-09 查證）：

- **Supabase Free**：
  - 資料庫 500 MB、檔案儲存 1 GB、egress 5 GB/月。
  - Auth 50,000 MAU，最多 2 個專案。
  - **閒置一週會暫停專案**。
  - 來源：https://supabase.com/pricing
- **Firebase Spark**：
  - Firestore 1 GiB 儲存，每日 50K 讀、20K 寫。
  - Auth 50K MAU；Cloud Storage 只有 legacy bucket（5 GB）。
  - 來源：https://firebase.google.com/pricing
- **Cloudflare D1 Free**：
  - 每日讀 500 萬列、寫 10 萬列，總儲存 5 GB。
  - 來源：https://developers.cloudflare.com/d1/platform/pricing/
- **Cloudflare Workers Free**：
  - 每日 100,000 requests，每 request CPU 10 ms、subrequest 50 個。
  - 來源：https://developers.cloudflare.com/workers/platform/limits/

| 面向 | 評估 |
|---|---|
| 成本 | 0。只存設定與分享連結，用量遠低於上述任何一家的免費額度 |
| 安裝難度 | 同 (a)，外加一個「登入 / 註冊」步驟（可選） |
| 隱私 | 好：雲端沒有活動資料；備份可先用使用者自己的金鑰加密再上傳（端對端） |
| 現有程式碼可沿用 | ~90%：多一個 account 模組（OAuth / magic link 登入、備份上傳下載） |
| 風險 | Supabase 閒置暫停：對小眾工具很實際，需要定期 ping 或改用 D1 / Firestore；免費方案條款可能變動 |

### (c) 純瀏覽器 PWA

作法有兩條：Pyodide 在瀏覽器跑 Python，或把 engine 移植成 JS / TS。

- **CORS 問題**：COROS 和 TrainingPeaks 的 API 都不是給第三方網頁用的。
  - COROS team API 是 `teamapi.coros.com` 等；TP 是 `tpapi.trainingpeaks.com` 和 `home.trainingpeaks.com`。
  - 可以預期它們不會對任意 origin 回 `Access-Control-Allow-Origin`。
  - 這點**尚未實測**：要驗證，就從 `http://localhost` 頁面對兩邊發 preflight（OPTIONS），看回應標頭。
  - 若被擋，瀏覽器就無法直接登入或下載。
- **要做成就得有代理**，例如 Cloudflare Worker，但代價很高：
  - 使用者的 **COROS / TP 帳密與 token 都會經過我們的 Worker**。隱私與法律責任落在我們身上，而且等於幫他們把憑證交給第三方。
  - 由我們的伺服器「代表大量使用者」呼叫非官方 API，最容易被偵測成濫用。這違反 TP 條款的風險最高；我們也會成為封鎖目標，一旦被封，所有人一起壞。
  - Workers 免費方案每 request 只有 10 ms CPU，代理本身夠用，但不能在 Worker 裡跑分析。
- **改走手動匯入**：使用者從 COROS / TP 自己匯出 FIT / ZIP，再拖進 PWA。這可行，但失去自動同步。

| 面向 | 評估 |
|---|---|
| 成本 | 0（靜態網站 + 可選的 Worker） |
| 安裝難度 | 最低：開網址即可 |
| 隱私 | 不用代理時很好（資料在瀏覽器 IndexedDB）；用代理就很差 |
| 現有程式碼可沿用 | Pyodide 版 ~60%：numpy 可用，但 SQLAlchemy / aiosqlite、檔案 I/O、同步 client 都要重寫；移植 JS 則 ~20%，engine 要全部重寫 |
| 結論 | 不適合當主要路線；可當「只看、手動匯入」的輕量版 |

### (d) 託管的多租戶伺服器

免費方案限制（2026-09 查證）：

- **Fly.io**：
  - 已經沒有常駐免費額度，只有 7 天或 2 小時機器時間的試用。
  - 之後要綁卡計費：最小的 shared-cpu-1x 256 MB 約 USD 0.2/月起，volume USD 0.15/GB/月。
  - 來源：https://docs.fly.io/about/pricing/ 、https://docs.fly.io/about/free-trial/
- **Render Free**：
  - 閒置 15 分鐘就休眠，喚醒約 1 分鐘，本機檔案會遺失。
  - 每月 750 instance-hours；**不能掛 persistent disk**。
  - 免費 Postgres 1 GB，而且 **30 天後到期**。
  - 來源：https://render.com/docs/free
- **Oracle Cloud Always Free**：
  - Ampere A1 共 4 OCPU / 24 GB 的月額度，實際為 2 OCPU 12 GB；block storage 200 GB；出站 10 TB/月。
  - 7 天 CPU、網路、記憶體使用率都低於 20% 的機器會被回收。
  - 來源：https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

依使用人數，會先壞掉的地方：

| 使用者 | 會先壞的地方 |
|---|---|
| 5 人 | Render：沒有 persistent disk，SQLite 和 FIT 會消失，Postgres 30 天到期，每次打開都要等喚醒 1 分鐘。Oracle：容量夠，但同步都從同一個 IP 打 COROS / TP，已開始有被限流、封鎖的風險；每人的 FIT 大約 1–3 GB/年（本使用者 1000+ 活動約 2–3 GB）。 |
| 20 人 | Oracle 12 GB RAM 還夠，但 WKO5 風格的 Dataset 常駐記憶體約每人數百 MB，要改成按需載入、LRU 淘汰。每日排程同一時間打出 20 個同步，需要排隊。儲存 20–60 GB 仍在 200 GB 內。 |
| 100 人 | 記憶體和 CPU 都不夠（每次重算 PMC、MMP 都很重）；非官方 API 從單一 IP 大量存取幾乎一定被擋；儲存可能超過 200 GB；需要監控、備份、帳號客服、資料外洩責任，已經不是「零成本」。 |

- 成本：理論上可以 0（Oracle），但營運負擔最大；Oracle 也可能回收閒置機器。
- 隱私：最差。我們替所有人保管 COROS / TP token 和活動資料。
- 現有程式碼可沿用：~60%。多租戶改造見 §3。

### 建議

> 2026-09-30 已依 §9（COROS 官方 OAuth、Garmin、FIT 匯入）更新建議與分階段，以 §9.5 為準。

**選 (a)，之後視需要加 (b) 的一小部分；不做 (c)、(d)。**

分階段：

1. **P1：本機桌面版 alpha**。
   - PyInstaller 一個資料夾版本，Windows 優先。
   - 首次啟動引導、預設資料來源改為 FIT 資料夾。
   - 移除所有 WKO5 / 個人路徑依賴，不含 TP OAuth secret。
2. **P2：穩定版**。
   - 自動更新、macOS 版（視 notarization 成本決定）。
   - 手動 FIT / ZIP 匯入。
   - i18n（見 §6），先讓 en 可用。
3. **P3（可選）：帳號層**。
   - 用 Cloudflare D1 + Workers，或 Firebase Auth + Firestore，不會閒置暫停。
   - 只做「設定與門檻的加密備份」和「成就分享卡片連結」。

---

## 2. 分享前必須解決的阻礙

### 2.1 TrainingPeaks OAuth client_secret 不能散布

- 這組 secret 是從 WKO5.exe 解出來的，屬於 TrainingPeaks / WKO5。
- **不論是否加密，都不能交給任何其他使用者。**
  - 給每個使用者各自的 Fernet 金鑰也改變不了這一點：加密只保護「傳輸與保存」，不改變「散布他人憑證」的本質。
  - 何況 app 必須解密才能用，使用者一定拿得到明文。
- 分享版建置和任何公開 repo 都必須排除以下檔案；若 repo 將來公開，還要從 **git 歷史**移除（`git filter-repo`）：
  - `backend/settings/tp_client.enc`
  - `backend/scripts/decode_tp_client_secret.py`
  - `docs/wko5-internals/trainingpeaks-auth.md`
  - `docs/deploy/tp-oauth-client.md` 中涉及 WKO5 client 的段落
- 程式面：分享版的 `lookup_client_creds()` 只保留 `env` / `file` 兩個來源，給自己申請了 TP API 的人用。`sealed` 和 `wko5_exe` 在建置時移除。

其他使用者連 TP 的選項：

1. **TP 網站 cookie 登入**（目前的 web 路徑）：
   - 一般帳號就能用，但這是模擬瀏覽器登入。
   - TP 服務條款禁止自動化存取，有被停權的風險，UI 必須明示。
   - 另外可能遇到 CAPTCHA / MFA。
2. **自己申請 TP API app**：
   - 使用者向 TrainingPeaks 申請 partner / developer 存取，拿自己的 client_id / secret，走 authorization-code flow。
   - 門檻高，只適合少數人。
3. **不接 TP（建議預設）**：
   - 主力用 COROS 同步。
   - 其他平台（Garmin、Strava、TP）用「手動匯入 FIT / ZIP」，例如 TP 的批次匯出或 Garmin 的資料匯出。

### 2.2 COROS 非官方 API

- **條款**：COROS 沒有公開 API；我們用的是 Training Hub 網頁的內部端點。多人使用時，封鎖的是個人帳號或 IP。桌面版每人用自己的 IP，風險比託管版低很多。
- **限流**：現在每次同步是逐頁列表再逐筆下載。要加上：
  - 每請求間隔與退避（429 / 5xx 時指數退避）。
  - 首次同步預設只抓最近 90 天，舊資料按需補抓。
- **優雅降級**：
  - API 改版或被擋時，同步回清楚的錯誤（已有 `COROS_API_ERROR`）。
  - 設定頁提示改用「手動匯入 FIT」。
  - 分析功能完全不依賴同步，已下載的資料照常可用。

### 2.3 對 WKO5 的依賴

大多數朋友沒有 WKO5，所以 `FitFolderDataset` 必須成為預設資料路徑，WKO5 匯入改為選配。

目前需要 WKO5 檔案的功能與替代：

| 功能 / 資料 | 目前來源 | 替代 |
|---|---|---|
| 門檻歷史（runftp、runthr、tpace、weight…） | `.wko5athlete` 的 3404 dated settings（`Dataset.setting`） | 賽事周期頁的 dated thresholds（`plan.json`）與設定頁體重。初值用 `thresholds.estimate`（LTHR / AeT 估計）與 racepower `cp.py`（CP 測試）自動建議 |
| mFTP / 模型 FTP | `pd_snapshot(.wko5athlete)` | 自己的 PD fit（evaluator 的 `ftp(meanmax(power))`，90 天窗）；UI 顯示「估計值」 |
| PMC 常數（CTL 42 / ATL 7） | `.wko5athlete` 3021 / 3022 | 預設 42 / 7，設定頁可改 |
| 圖表視圖 | `.wko5chart`（`WKO5 Season View`、`WKO5 Workout View`）與 `views/wko5_fixes.json` | 只保留 `views/*.json` 自訂視圖（training / periodization），改成中性預設；WKO5 視圖匯入改為選配 |
| 每日 MMP 曲線快取 | WKO5 `Cache5/*.wko5cache` | 自己算，已支援，只是第一次比較慢 |
| TP TSS 覆寫 | `.wko4` info 4038 + `tp_tss.json` | 不需要；用自己的 TSS |
| 對照模式（parity） | 需要 WKO5 資料 | 沒有 WKO5 時隱藏這個選項 |
| 活動標題 / 描述 / 備註 | WKO5 index 文字欄位 | FIT session 的 sport；標題暫用「日期 + 運動」 |
| 功率計算機、總覽的體重與 CP | `Dataset.setting("weight")`、`cp()` | plan 體重 / CP 測試；缺值時顯示空狀態（見 §4） |

### 2.4 每位使用者的 secret

- 每次安裝時產生一把 Fernet 金鑰，存進作業系統的憑證庫：
  - Windows Credential Manager 或 macOS Keychain，透過 `keyring` 套件。
  - 鍵名如 `wko5coach/secret-key`。
- `secrets.py` 的讀取順序改為：環境變數 `WKO5COACH_SECRET_KEY` → keyring → `~/.wko5coach/secret.key`（舊版相容，之後移除）。
- dotfiles（chezmoi）只是這位使用者自己的開發設定，不是產品的一部分；分享版完全不提。
- 金鑰遺失時只影響登入 token，重新登入即可，因為活動檔不加密。UI 要照這個說明。

---

## 3. 目前程式碼的單人假設

| 假設 | 位置（摘錄） | (a) / (b) 本機版 | (d) 託管版 |
|---|---|---|---|
| `athlete_id = 1` / `user_id = 1` 預設 | 後端 53 處：`api/*` 的 `athlete_id: int = 1`、`api/scan.py` 的 `Athlete.id == 1`、`settings.repository.DEFAULT_USER` | 保持不變，一台電腦一位使用者 | 每個 request 都要從 session 取得 user_id；所有查詢加 tenant filter；移除預設值 |
| 單一 SQLite `~/.wko5coach/wko5coach.db` | `db/database.py` | 不變（安裝目錄或 AppData） | 改 Postgres（多寫入者），或每人一個 SQLite 並嚴格隔離 |
| `~/.wko5coach` 下的各種快取與設定 | `Path.home()` 用在 16 個模組：dataset 快取、`engine.json`、`plan.json`、corrections、achievements、racepower weather、render_cache、`storage.FIT_ROOT`… | 集中成一個 `app_data_dir()`（Windows 用 `%APPDATA%\wko5coach`） | 每人一個 namespace：`<root>/<user_id>/…`；快取加容量上限 |
| 全域 in-process 同步鎖 | `sync/runner._BUSY` | 夠用（單一 process） | 要改成 DB / Redis 鎖，鍵為 (user, source) |
| 排程 | `sync/scheduler.loop()` 只看 user 1 | 夠用 | 逐一掃所有使用者，加 queue / worker，錯開時間 |
| Dataset 在記憶體快取 | `api/wko5views._dataset_cfg`（lru_cache 4） | 夠用 | 每人一份，LRU 加記憶體上限，或改成請求時載入 |
| 寫死的個人路徑 | 見下表 | 必須移除 | 必須移除 |

寫死的個人路徑與個人資料（`git grep`）：

| 檔案 | 內容 |
|---|---|
| `backend/api/wko5views.py:42` | `C:\Users\<user>\Projects\TrailRunCoach\WKO5\Athlete` |
| `backend/api/plan.py:22` | 同上 |
| `backend/api/achievements.py:24` | 同上 |
| `backend/scripts/build_baiyue.py:21` | `C:\Users\<user>\Projects\peak-list\v2\app\src\lib\peaks-data.ts` |
| `backend/engine/wko5expr/chartfixes.py:14`、`views/wko5_fixes.json` | 視圖名稱 "WKO5 Workout View"（`wko5_fixes.json` 裡 51 處） |
| `backend/engine/algorithms/validator.py:4`、`backend/files/wko5chart_reader.py:174-175`、`backend/files/wko5_athlete.py:70` | 註解中的個人檔名 |
| 測試：`test_wko5_*.py`、`test_wko4_*.py`、`test_fit_to_channels.py`、`test_wko5chart_reader.py`、`test_wko5expr_parser.py` | golden 測試用的個人資料夾路徑（沒有資料時會自動 skip） |

---

## 4. 朋友的上手流程

1. **安裝**：下載 `WKO5Coach-Setup.exe` → 安裝 → 開始選單「訓練教練」。
2. **首次啟動精靈**（新頁 `/setup`）：
   1. 語言：中文 / English。
   2. 時區：預設系統時區。
   3. 單位：公制 / 英制（英制只在 en 時提供）。
   4. 基本資料：體重、性別、身高（可跳過）。
3. **連接 COROS**：輸入帳密 → 顯示「已登入」。說明帳密不保存、token 加密存在本機。
   - 附非官方 API 聲明（§7）。
   - 另給「沒有 COROS？改用手動匯入 FIT / ZIP」按鈕。
4. **首次同步**：
   - 預設最近 90 天，可改日期。
   - 顯示即時進度（已完成：SSE 進度條）。
   - 完成後提示「已下載 N 筆活動」。
5. **門檻**：
   - 若有 90 天內的跑步功率資料 → 用 PD fit 建議 CP / mFTP；有心率 → 用 `thresholds.estimate` 建議 LTHR / AeT。使用者按「套用」。
   - 若都沒有 → 引導做 CP 測試（racepower 的 3 / 12 分鐘測試說明），或手動輸入。
6. **總覽**：看 PMC、本週建議、訓練狀況。

空狀態：

| 位置 | 情況 | 顯示 |
|---|---|---|
| 總覽 | 沒有任何活動 | 「還沒有活動。」+「連接 COROS」／「匯入 FIT」兩個按鈕 |
| 總覽 | 活動少於 4 週 | PMC 照畫，但標示「資料不足 4 週，CTL 還在累積」；週建議改成基礎建議 |
| 總覽 / 狀態 | 沒有門檻 | TSS 只能用 hrTSS（沒有 LTHR 時為空）。卡片提示「設定心率門檻或做 CP 測試後，負荷才會準」 |
| 圖表 | 沒有功率 | 功率類圖表顯示「這段期間沒有功率資料」，不顯示空白座標 |
| 功率計算機 | 沒有 CP / 體重 | 表單可手填；預設值顯示「—（請先設定）」 |
| 同步 | token 過期 | 設定頁來源卡片顯示「需要重新登入」；自動同步略過並在導覽列顯示提醒 |

---

## 5. 各階段工作量與風險

| 階段 | 內容 | 估計 | 主要風險 |
|---|---|---|---|
| P0 清理 | 移除 WKO5 client secret 相關檔（分享分支 + 若公開則清歷史）、移除個人路徑、中性預設視圖、`app_data_dir()`、keyring 金鑰 | 3–4 天 | 漏網的個人資料（見 §7 清單）；golden 測試的相依 |
| P1 WKO5-free 預設 | FitFolderDataset 設為預設、plan 門檻取代 WKO5 settings、PD fit 取代 mFTP、空狀態、首次啟動精靈、手動 FIT / ZIP 匯入 | 6–8 天 | 沒有 WKO5 對照時數字準確度的信心；自己的 PD fit 在資料少時不穩 |
| P1 打包 | PyInstaller（Windows）、啟動器（找可用 port、開瀏覽器、系統匣）、安裝檔（Inno Setup）、更新檢查 | 3–5 天 | 防毒誤判、SmartScreen；fitdecode / numpy 打包體積（約 150–250 MB） |
| P2 i18n | 見 §6 | 6–9 天 | 引擎文字改成 key + 參數的範圍大；英文專業用語要審 |
| P2 macOS | Briefcase / PyInstaller + notarization | 3–4 天 + USD 99/年 | Apple 簽章流程 |
| P2 同步強化 | 限流、退避、首次 90 天、降級提示 | 2 天 | COROS 改版 |
| P3 帳號層（選配） | D1 + Workers 或 Firebase：登入、加密設定備份、成就分享連結 | 5–7 天 | 免費方案條款變動；帳號與個資責任 |

---

## 6. i18n 設計（中文 / English）

### 6.1 範圍與字串數

以程式計數：字串字面值或文字片段中含中文字者，已排除註解與 docstring，是估計值。

| 檔案 | 約略字串數 |
|---|---|
| `backend/static/racepower.html` | 264 |
| `backend/static/settings.html` | 161 |
| `backend/static/plan.html` | 151 |
| `backend/static/overview.html` | 132 |
| `backend/static/wko5_viewer.html` | 112 |
| `backend/static/achievements.html` | 78 |
| `backend/static/compare.html` | 24 |
| `backend/static/shell.js` | 22 |
| `backend/static/autosync.js` / `sourcechip.js` | 3 / 4 |
| `backend/engine/status.py`（判定、動作、`PHASE_FOCUS`） | 201 |
| `backend/engine/overview.py`（week_plan 標題、細節、備註） | 61 |
| racepower（`api/racepower.py` 34、`engine/racepower/*` 共 86） | 120 |
| `backend/engine/zones.py` | 27 |
| `backend/engine/planning.py`（週期名稱、賽事種類） | 15 |
| achievements（`engine` 6 + `api` 14，含匯出文字） | 20 |
| 自訂視圖 JSON（`views/periodization.json` 88、`views/training.json` 76） | 164 |
| **合計** | **約 1,560** |

重複字很多（單位、按鈕、區間名），去重後估計約 900–1,100 個 key。

### 6.2 訊息目錄

- **格式**：每個語系一個 JSON，放在 `backend/i18n/zh-TW.json`、`backend/i18n/en.json`。
  - 用點分 key：`overview.week.title`、`status.form.fresh.headline`、`racepower.warn.hot`。
  - 參數用 ICU 風格 `{name}`，複數用 `{n, plural, one{…} other{…}}`，en 需要，zh 不需要。
- **zh-TW 是原始語言**：新字串先加進 `zh-TW.json`，en 缺值時退回 zh-TW，畫面上顯示中文而不是 key。
- **前端**：`/api/v1/static/i18n.js` 提供 `t(key, params)`。
  - 載入 `/api/v1/i18n/<locale>.json`，會 cache。
  - HTML 用 `data-i18n="key"` / `data-i18n-attr="placeholder:key"` 屬性，載入時替換。
  - JS 字串改成 `t("…")`。
- **後端產生的文字**（建議做法）：引擎回傳 **message key + 參數**，由前端翻譯。例如 status 指標：

      {"verdict": {"key": "status.form.fresh", "params": {"tsb": 12.3}}}

  - 這樣引擎與語系無關，快取也不必分語系。
  - 過渡期兩種並存：同時回 `text`（zh-TW 成品）與 `msg`（key + 參數），前端有 `msg` 就用它。
  - 必須在伺服器端組成品字串的地方，改收 `Accept-Language` 或 `?lang=`：成就匯出文字、AI 教練 prompt、Excel / CSV 匯出。
- **自訂視圖 JSON**：`title` / `description` / series `name` 改成可以是物件：

      "title": {"zh-TW": "每週爬升", "en": "Weekly climbing"}

  - 字串仍然接受，當作 zh-TW。
  - `customviews.parse_view` 依 locale 取值。
- **WKO5 匯入的圖表名稱**：保留原本英文（WKO5 的名稱就是英文），兩種語系都不翻。
- **Glossary**：總覽用的名詞提示，如 AeT、CP 測試、TSB，移到 `glossary.<term>.title/body`。
- **語系切換**：放在共用導覽列（shell.js）右上角「中 / EN」，存在 `localStorage`。
  - 首次依 `navigator.language` 決定：`zh*` 用 zh-TW，其他用 en。
  - 設定頁也能改，同時存進 settings store（`ui.locale`），讓伺服器端的匯出知道語系。
- **單位**：預設公制；en 提供「英制」切換（`ui.units`）。
  - 距離 mi、爬升 ft、配速 min/mi、溫度 °F。
  - 引擎已有 `render_units`，把它接到這個設定。
- **日期與數字**：用 `Intl.DateTimeFormat` / `Intl.NumberFormat`，依 locale。

### 6.3 檢查

- 新增測試 `test_i18n_catalog.py`：
  - 比對 zh-TW 與 en 的 key 集合：en 缺 key → 警告並列出；zh-TW 缺 key → 失敗，因為 zh-TW 是原始語言。
  - en 值含中文字 → 視為未翻譯，列出。
  - 掃描靜態頁與引擎：`data-i18n` / `t("…")` / `msg.key` 用到的 key 都必須存在。
  - 掃描靜態頁裡**還沒包成 key 的中文文字**，數字只能減少，逐步收斂（ratchet）。

### 6.4 遷移順序

1. 基礎：`i18n.js`、catalog 載入端點、shell.js 語系切換、lint 測試。
2. shell.js、settings、compare、autosync / sourcechip：字串少，驗證流程。
3. overview.html + `status.py` / `overview.py` 改回傳 key + 參數：影響最大的首頁。
4. racepower（264 + 120，最多，而且是 Palladino 社團最會看的頁面）。
5. wko5_viewer、自訂視圖 JSON 多語欄位。
6. plan、achievements（含匯出文字的伺服器端翻譯）。
7. 英文用語審稿：跑步功率術語對齊 Palladino / Stryd 社群的慣用說法。

估計：基礎 1 天；各頁 0.5–2 天；引擎文字 key 化 2–3 天；總計 6–9 天。

---

## 7. 公開分享檢查清單

會發到具名的第三方社群（Palladino 功率訓練社團），所以要比「給幾個朋友」更嚴格。

### 7.1 不能散布的東西

- [ ] TP client_secret：`backend/settings/tp_client.enc`、`backend/scripts/decode_tp_client_secret.py`、`docs/wko5-internals/trainingpeaks-auth.md`、`docs/deploy/tp-oauth-client.md` 的 WKO5 client 段落。
  - 從分享版與公開 repo 移除，**並清除 git 歷史**（`git filter-repo --invert-paths --path …`）。
  - 清完再用 `test_tp_client_sealed.py` 的 leak guard 和 `git log --all -p | grep`（只輸出計數）驗證。
- [ ] `lookup_client_creds()` 的 `sealed` / `wko5_exe` 來源在分享版移除。

### 7.2 個人資料稽核（目前 repo 追蹤中的檔案）

| 路徑 | 內容 | 處理 |
|---|---|---|
| `WKO5 Season View/WKO5 Season View.wko5chart` | 使用者自己的 WKO5 賽季視圖（二進位） | 移除（含歷史） |
| `WKO5 Workout View/WKO5 Workout View.wko5chart` | 使用者自己的 WKO5 單次活動視圖 | 移除（含歷史） |
| `docs/wko5-views/season-view.json`、`workout-view.json` | 上面兩個視圖的 JSON 匯出，含個人自訂的圖表與中文註記 | 移除，或改寫成中性範例 |
| `views/wko5_fixes.json` | 針對 "Athlete's …" 視圖的修正（51 處提到名字） | 移除，或改用中性視圖名 |
| `views/periodization.json`、`views/training.json` | 自訂視圖，內容泛用，但有個人化的目標值（例如賽事爬升密度） | 保留，但檢查數值是否個人化，改成預設 |
| `backend/data/baiyue.json` | 百岳清單，來自 peak-list 的 twmap 資料 | 確認資料來源授權（twmap / 地圖資料）；註明出處 |
| `backend/api/wko5views.py:42`、`backend/api/plan.py:22`、`backend/api/achievements.py:24` | 寫死的 `C:\Users\<user>\…\WKO5\Athlete` | 移除預設值，改 `app_data_dir()` 或設定 |
| `backend/scripts/build_baiyue.py:21` | 寫死的 `C:\Users\<user>\Projects\peak-list\…` | 改成參數 |
| `backend/engine/wko5expr/chartfixes.py:14`、`validator.py:4`、`wko5chart_reader.py:174-175`、`wko5_athlete.py:70` | 註解與範例中的個人檔名 | 改寫成中性範例 |
| 測試（`test_wko5_*`、`test_wko4_*`、`test_fit_to_channels`、`test_wko5chart_reader`、`test_wko5expr_parser`） | 個人資料夾路徑（golden） | 改讀環境變數，沒設定就 skip |
| `docs/research/superpower-calculator.md` | 試算表研究筆記 | 見 7.3 |
| `docs/plans/*`、`docs/reports/*` | 可能含個人活動數字、賽事 | 公開前逐份檢查，或整個不放進公開 repo |
| 本機（不在 repo）：`~/.wko5coach/*`（DB、FIT、`achievements_cache.json`、`plan.json`） | 個人資料 | 打包時絕不包含；安裝檔只含程式 |

### 7.3 歸屬與授權

- [ ] **SuperPower Calculator / Palladino**：racepower 的公式移植自 SuperPower Calculator 試算表，包括環境乘數、Riegel、CP / RWC、RE、賽事預測。zones 的 Palladino 功率區間表也出自他。
  - app 內的「關於 / 方法」頁與 racepower 頁尾要清楚標示出處與原作者。
  - **建議使用者在社團發文前先私訊 Palladino 取得同意**：說明是免費工具、公式來源已標示，並詢問他是否同意用他的名字與區間名稱。
- [ ] WKO5 的公式重建（TIS、stamina、PD model、formulas.md）：是逆向工程的成果。公開時要評估 WKO5 / TrainingPeaks 的條款；說明文件避免附上二進位位址、解碼步驟等可直接用來繞過授權的細節。
- [ ] 第三方研究引用（Minetti 2002、Foster 1998、Uphill Athlete 等）：沿用目前 docs 的引用格式，放進「方法」頁。

### 7.4 商標

- [ ] 名稱不要用「WKO5 Coach」。改成中性名稱，例如「Trail Power Coach」。
- [ ] 文案一律用「相容於 / compatible with WKO5 .wko4 files, COROS, TrainingPeaks」，並加上：「本工具與 TrainingPeaks、WKO5、COROS、Stryd 無任何關係，商標屬於各自的所有者。」
- [ ] 不用對方的 logo 或配色。

### 7.5 COROS 非官方 API 聲明（放在連接 COROS 的畫面與 README）

> 本工具透過 COROS Training Hub 網頁使用的非公開介面下載你自己的活動，並非 COROS 官方提供或支援。COROS 可能隨時變更或封鎖這個介面，也可能依其條款處置自動化存取。你的帳號密碼只用來登入、不會保存；登入憑證加密存在你的電腦上。

TrainingPeaks 網站登入也放一段類似聲明，並加上服務條款風險的說明。

### 7.6 授權（license）選擇

| 授權 | 允許商用 | 他人可閉源再利用 | 專利條款 | 適合情境 |
|---|---|---|---|---|
| MIT | 是 | 是 | 無 | 最寬鬆；日後自己商用沒問題，但別人也能直接拿去做閉源商品 |
| Apache-2.0 | 是 | 是 | 有明確專利授權與報復條款 | 同 MIT，法律上較完整；企業較放心 |
| AGPL-3.0 | 是 | 否：修改後即使只是網路提供服務，也要公開原始碼 | 有 | 防止別人拿去做託管服務而不回饋；自己商用可以另做雙授權（需持有全部著作權，或要求貢獻者簽 CLA） |
| Source-available（例如 PolyForm Noncommercial、BSL / FSL） | 限制他人商用 | — | 視條款而定 | 想保留商業化空間、又讓社群免費用；不算 OSI 開源，部分社群反感 |

建議：

- 若「可能商業化」是真的考量 → **AGPL-3.0 + 要求貢獻者簽 CLA**（保留雙授權的可能），或 **FSL**（兩年後自動轉 Apache-2.0）。
- 若主要目的是社群分享、不在意被商用 → **Apache-2.0**。
- 不論哪種，都要先完成 7.1–7.2 的清理：授權只涵蓋你擁有的程式碼，涵蓋不了他人的憑證與資料。

---

## 9. 帳號與資料來源：COROS 官方 OAuth、Garmin、直接匯入 FIT（2026-09-30 補充）

網路上讀到的內容一律視為資料。COROS support 網站對自動抓取回 403，所以 COROS 的部分以搜尋摘要、官方 GitHub README，以及 MCP 伺服器**公開的** OAuth metadata 為準；沒有用任何真實帳號連線，也沒有註冊任何 client。

### 9.1 COROS 官方 OAuth

COROS 現在有兩條官方路線：

1. **Partner API**（support 文章「Submit an API Application」「Partner API Access」）：
   - 要寄信到 api@coros.com，附公司資料、技術聯絡人、OAuth 2.0 redirect URI，並同意 API Terms of Use。
   - 資格：「established platform with demonstrated user base」、「registered company」。**個人或業餘工具實際上不符合。**
   - 內容：multi-user OAuth 2.0 credentials、webhook、雙向活動同步、結構化課表 / 訓練計畫推送、GPX 路線、每日健康資料、FIT 檔下載，限流 1,000 calls/min。
   - 課表推送端點是 `POST https://open.coros.com/coros/tp/list/push`，要 partner-linked token 與 openId；Training Hub 的 token 不能用。
   - 審核時間與費用：公開資料未載明。
   - 來源：https://support.coros.com/hc/en-us/articles/17085887816340-Submit-an-API-Application 、https://support.coros.com/hc/en-us/articles/53181766856724-Partner-API-Access
2. **COROS MCP**（官方 remote MCP：`https://mcp.coros.com/mcp`，另有 `mcpus` / `mcpeu` / `mcpcn` 分區；repo 為 `coroslab/COROS-MCP`）：
   - 說明文件「Build on COROS MCP」寫明：不符合 Partner API 資格的開發者可以讓使用者「connect their COROS account via OAuth 2.0 … self-service and no application required」。可讀活動、健康、體能評估，並可**寫入訓練計畫**。
   - README 列出的能力：
     - 活動查詢、心率 / 配速 / 爬升 / 步頻、分段。
     - **FIT 檔下載，每 24 小時上限 50 個**。
     - 訓練計畫 4–16 週，可建立、修改、排程 workout。
   - 但 the5krunner 的分析指出，MCP 暴露的是摘要資料，沒有 GPS 細節、逐秒資料、功率、跑步動態。兩邊說法有出入；FIT 下載若存在，逐秒資料就在 FIT 裡。**需要實測確認。**
   - 來源：https://github.com/coroslab/COROS-MCP 、https://support.coros.com/hc/en-us/articles/53181619102996-Build-on-COROS-MCP 、https://the5krunner.com/2026/05/13/coros-mcp-ai-data/
   - **公開的 OAuth metadata**（`https://mcp.coros.com/.well-known/oauth-authorization-server`，2026-09-30 讀取）：
     - issuer `https://mcpus.coros.com`。
     - 有 `registration_endpoint`（`/connect/register`），代表支援 **Dynamic Client Registration**。
     - `code_challenge_methods_supported: ["S256"]`（PKCE）。
     - `token_endpoint_auth_methods_supported` 含 `none`（public client）。
     - grant 有 `authorization_code`、`refresh_token`、`device_code`（device authorization flow）。
     - scopes：`openid mcp.tools offline_access`。

第三方 app 能不能用這條 OAuth？

- **技術上看起來可以**：本機 app 用 DCR 自己註冊成 public client，走 PKCE authorization code（redirect 用 loopback `http://127.0.0.1:<port>/callback`；DCR 是否接受 loopback URI 要實測），或走 **device flow**（顯示代碼讓使用者到 COROS 網頁同意，完全不需要 redirect URI，很適合桌面 app）。拿到 token 後以 MCP（JSON-RPC over HTTP）呼叫 tools，例如列活動、下載 FIT、推課表。
- **條款面**：COROS 文件的定位是「Build on COROS MCP」給開發者用，不是只限 ChatGPT / Claude 這類 AI client。自己的 app 當 MCP client 應屬預期用途，但**必須先讀 COROS API Terms of Use / MCP 條款確認**。重點看：是否允許非 AI 用途、能否快取 FIT、每人 50 FIT/日的限制。
- **限制**：
  - 50 FIT/24h 代表**首次同步大量歷史很慢**：1,000 筆要 20 天。歷史資料應改用 COROS 帳號的「匯出資料」ZIP（§9.3），OAuth 只負責增量。
  - scope 只有 `mcp.tools`，粒度粗。

COROS OAuth 可以當 (b) 的帳號身分嗎？

- 有 `openid` scope，ID token 的 `sub` 可以當穩定的使用者 id。如果只是「本機 app 知道你是誰」，**不需要另建帳號系統**。
- 但若 (b) 要雲端備份或分享連結，雲端那端（Workers / Firebase）要驗證 COROS 簽發的 ID token（JWKS：`/oauth2/jwks`）。可行，不過就把帳號綁死在 COROS：沒有 COROS 的人（Garmin 使用者）就沒有帳號。
- 建議：本機版**不需要帳號**；真的做 P3 時，用 COROS OIDC 當「其中一種」登入方式，再加 email magic link 當後備。

### 9.2 Garmin

官方：**Garmin Connect Developer Program**。

- 可用的 API：
  - Activity API：可取得原始 **.FIT / GPX / TCX**，ping/pull 或 push。
  - Training API：發佈 workout 與訓練計畫到 Garmin Connect 行事曆，再同步到裝置。
  - 另有 Health API、Courses 等。
- 全部 OAuth 2.0 **PKCE**（舊的 OAuth 1.0a 已在遷移）。無授權費，審核回覆約 2 個工作天，整合通常 1–4 週。
- **只限企業或法人**：FAQ 寫「available for enterprise use」「only for business use」。個人申請會被拒；社群也回報申請表一度暫停受理。
- 來源：https://developer.garmin.com/gc-developer-program/program-faq/ 、https://developer.garmin.com/gc-developer-program/activity-api/ 、https://developer.garmin.com/gc-developer-program/training-api/ 、https://developerportal.garmin.com/sites/default/files/OAuth2PKCE_1.pdf 、https://ghurt.org/garmin-api-for-personal-use

非官方：`garminconnect` / `garth`（Python）。

- 2026 年 3 月 Garmin 改了登入流程，加上 Cloudflare TLS fingerprinting，會擋掉行動版 User-Agent。`garth` 的維護者在 2026-03-27 宣布停止維護，新登入全部失效。
- `garminconnect` 0.3 改用自己的登入流程，不再讀 garth token，已支援 MFA。
- 社群的繞法（換 User-Agent、Playwright、`curl_cffi` 偽造 TLS 指紋）都是貓抓老鼠。違反 Garmin 條款，也可能被封帳號。
- 來源：https://github.com/matin/garth/discussions/222 、https://github.com/cyberjunky/python-garminconnect/pull/448

結論：**沒有公司就沒有官方 Garmin API**。非官方路線才剛全面壞過一次，不適合放進給朋友用的版本。Garmin 使用者的預設路徑是**匯出 ZIP / 拖放 FIT（§9.3）**。

若日後取得 Developer Program 資格，要把 Garmin 加成第三個來源，現有的「每來源」設計都能直接套用：

| 元件 | 要做的事 |
|---|---|
| 儲存 | `storage.SOURCES` 加 `"garmin": "garmin"`，檔案放 `~/.wko5coach/fit/garmin/<year>/garmin_<activityId>.fit` |
| 同步 | 新增 `backend/sync/garmin_client.py`：OAuth2 PKCE（loopback redirect）、Activity API 下載（最好用 push / ping，webhook 需要對外 URL；本機版只能 pull） |
| runner / 鎖 | `runner.SOURCES` 加 `"garmin"`；鎖、`last_result`、排程、`/sync/auto` 自動涵蓋 |
| 刪除 | `DELETE /sync/garmin/files` 走同一個 `purge`，路徑限制在 `fit/garmin/` |
| 圖表來源 | `charts.data_source` 加 `garmin`；`dataset_for_source("garmin")` → `FitFolderDataset(fit/garmin)` |
| 去重 | 不用改：開始時間 ±2 分鐘分組、依主要來源選出主紀錄，已支援任意來源 |
| 設定頁 | 多一張來源卡片，沿用 COROS 卡片的元件 |
| 課表推送 | Training API 的 workout 格式和 COROS 不同，在「課表」模型上做 adapter：同一份 planned session，轉成 COROS steps 或 Garmin workout steps |

工作量：取得資格後約 5–8 天。取得資格本身需要公司或法人，這才是最大的瓶頸。

### 9.3 直接匯入 FIT（列為一級路徑）

這是**唯一**不需要任何核准、對所有品牌都有效、也沒有服務條款風險的路徑。所以它應該是預設的上手方式之一，而不只是後備。

- **入口**：
  - 設定頁與首次精靈提供「拖放檔案 / 選擇檔案」，可接單檔或多檔的 `.fit`、`.fit.gz`、`.zip`。
  - 也可選一個「監看資料夾」（例如手錶 USB 掛載點或下載資料夾），app 定期掃描。本機版用 polling，每分鐘一次即可。
- **批次 ZIP 格式**：
  - **Strava 匯出**：`activities.csv` 加 `activities/` 資料夾。多數是 `.fit.gz`，舊活動可能是 `.gpx`、`.tcx`、`.tcx.gz`。
    - 來源：https://epicefforts.com/blogs/strava/strava-bulk-export 、https://takeoutday.org/guides/how-to-export-strava-data
  - **Garmin「Export Your Data」**：`DI_CONNECT/DI-Connect-Uploaded-Files/UploadedFiles_*_Part*.zip`，是巢狀 ZIP。裡面混著活動 FIT 與 monitoring / sleep / weight FIT，要用 FIT `file_id.type == activity` 篩選。郵件可能 48 小時到 30 天才寄到。
    - 來源：https://gadgetbridge.org/basics/topics/garmin/import-garmin-connect/ 、https://cubetrek.com/static/bulkdownload.html
  - **COROS**：App / Training Hub 可逐筆匯出 `.fit` / `.tcx` / `.gpx`；帳號資料匯出的格式要實測。
    - 來源：https://support.coros.com/hc/en-us/articles/360043975752-Exporting-Workout-Data-and-Uploading-to-3rd-Party-Apps
- **處理規則**：
  - 解開巢狀 ZIP；只取活動類 FIT。
  - `.fit.gz` 直接支援（`fit_to_channels` 已會先 gunzip）。
  - `.gpx` / `.tcx` 轉成 FIT-like channels：時間、距離、海拔、HR，TCX 可能還有 power。這需要一個小轉換器，沒有功率的活動仍可算 hrTSS。
  - Strava 的 `activities.csv` 可補活動名稱與運動類型。
- **儲存**：`~/.wko5coach/fit/manual/<year>/`。檔名用 `manual_<start-UTC>_<hash8>.fit`，hash 是檔案內容，同一檔再匯一次會被辨識。
  - 在 DB 裡，`source = "manual"`。
  - `storage.SOURCES` 加 `"manual": "manual"`，刪除、路徑限制、統計都沿用。
- **去重**：
  - 先比內容 hash：完全同一個檔案 → 跳過。
  - 再走現有的開始時間 ±2 分鐘分組：同一活動已從 COROS / TP 同步進來 → 記成重複紀錄，由主要來源設定決定以誰為準；預設讓自動同步的來源優先，手動匯入補缺。
- **圖表來源**：`charts.data_source` 加 `manual`，另加 `all`（全部）。
  - 目前 `FitFolderDataset` 讀單一資料夾。朋友可能「COROS 同步 + 舊 Garmin ZIP 匯入」混用，需要一個 **MergedFitDataset**：讀多個來源資料夾，依 DB 的主紀錄 / 重複標記只取主紀錄的檔案。
  - 這應該是分享版的預設圖表來源。
- **工作量**：拖放與 ZIP（含巢狀、Strava、Garmin）2–3 天；GPX / TCX 轉換 1–2 天；監看資料夾 0.5 天；MergedFitDataset 加 `all` 來源 1–2 天。

### 9.4 哪些選項需要開發者核准

| 帳號 / 資料來源 | 需要申請核准？ | 審核時間（公開資料） | 個人能否取得 | 可以立刻用？ | 備註 |
|---|---|---|---|---|---|
| 直接匯入 FIT / ZIP（任何品牌） | 否 | — | 是 | **是** | 零條款風險；Garmin 匯出可能要等 48 小時–30 天寄出 |
| COROS MCP OAuth（DCR / device flow） | 否（self-service） | — | 是 | **是**（待實測與讀條款） | FIT 50 個/日；官方支援；openid 可當身分 |
| COROS Partner API | 是（寄信 api@coros.com） | 未公開 | 否（要有公司與使用者規模） | 否 | 可推課表（`open.coros.com`）、webhook、1,000 calls/min |
| COROS 非官方 Training Hub API（目前用的） | 否 | — | 是 | 是 | 非官方、條款風險、可能被改或封 |
| Garmin Connect Developer Program | 是 | 約 2 個工作天回覆，整合 1–4 週；曾暫停受理 | 否（限企業或法人） | 否 | Activity API 有原始 FIT；Training API 可推課表 |
| Garmin 非官方（garminconnect） | 否 | — | 是 | 不穩（2026-03 全面壞過） | 違反條款、會被擋 |
| TrainingPeaks 網站登入（目前的 web 路徑） | 否 | — | 是 | 是 | 模擬瀏覽器，條款風險，可能遇到 CAPTCHA / MFA |
| TrainingPeaks 官方 API | 是（partner） | 未查到公開時程 | 通常要公司 | 否 | — |
| TP WKO5 client secret | — | — | **不得使用於他人** | — | 見 §2.1，分享版移除 |
| Strava API | 是（建立 app 即可，但有使用者上限，擴大要審核） | 未在本次查證範圍 | 是 | 部分 | 不提供原始 FIT 下載，只有 streams；以 ZIP 匯入為主 |

### 9.5 更新後的建議與分階段

建議不變：**(a) 本機桌面 app**。資料來源的優先順序改為：

1. **直接匯入 FIT / ZIP**：人人可用、無條款風險、一次補齊歷史。
2. **COROS 官方 MCP OAuth**：取代非官方 Training Hub API 做增量同步與課表推送；實測通過並確認條款後才上線。
3. COROS 非官方 API 降級為「進階 / 實驗」選項，預設關閉。
4. TP 只留網站登入（進階、預設關閉，附條款警告）；Garmin 不做 API，只做匯入。

分階段（取代 §1 建議與 §5 的順序）：

- **P0 清理**（不變）：移除 TP secret 相關檔案（含歷史）、個人資料與路徑、`app_data_dir()`、keyring 金鑰。
- **P1 WKO5-free + 匯入優先**：
  - FitFolderDataset / MergedFitDataset 預設、`manual` 來源、拖放 / ZIP（Strava、Garmin、COROS）、GPX / TCX 轉換。
  - 首次精靈以「匯入 ZIP」為第一步，空狀態、Windows 安裝檔。
  - 約 10–14 天。
- **P1.5 COROS 官方 OAuth**：
  - 實測 DCR + device flow、確認條款與 50 FIT/日限制。
  - 做 `coros_mcp_client.py`：列活動、下載 FIT、推課表。
  - 設定頁的 COROS 卡片改成「用 COROS 帳號授權」按鈕，不再輸入密碼。
  - 約 4–6 天，外加實測。
- **P2**（不變，加一項）：i18n、macOS、同步強化；課表 adapter（COROS MCP，之後可接 Garmin Training API）。
- **P3（選配）**：帳號層。身分優先用 COROS OIDC（openid），加 email magic link 後備；只存加密設定備份與分享連結。
- **不做（除非取得公司或法人資格）**：COROS Partner API、Garmin Developer Program。取得資格後，Garmin 來源約 5–8 天（§9.2 的表）。

風險：

- COROS MCP 的條款若不允許非 AI 用途或快取 FIT，P1.5 就要退回匯入加非官方 API。
- 50 FIT/日對「每天同步」夠用，但對首次歷史不夠，因此要靠匯入補齊。
- 本節的 COROS 細節有一部分來自搜尋摘要，因為 support 網站 403；上線前必須人工逐條確認。

---

## 8. 決策待定

1. 是否接受「分享版不支援 TP 自動同步」：預設只提供 COROS 同步加手動匯入。
2. macOS 版是否值得 USD 99/年。
3. 帳號層（P3）要不要做，以及選 Cloudflare 還是 Firebase。
4. 授權選擇與產品名稱。
5. 是否先聯絡 Palladino 再公開。
