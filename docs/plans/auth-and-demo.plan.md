# Plan：線上示範模式（先做）＋ 登入與多使用者（之後）

- 日期：2026-10-02
- 狀態：**設計，尚未實作**。
- 分支基準：`feat/overview-racepower-ui`
- 相關：`todo-multi-user-sharing.plan.md`。§10 決定做桌面 app；本文件講的是**擁有者自架的網頁版**（Cloudflare tunnel）。兩者的關係見 §0.2。

## 背景

使用者想要一個**示範頁**，讓沒登入的人用虛構資料試用三個功能：排課表、預估賽事功率、規劃週期。這樣就能把連結貼到網路上。之後再加登入，方式要有彈性：註冊帳號或 Google 登入。COROS 登入以後才做，因為 COROS 沒有公開 OAuth。

2026-10-02 擁有者補充：**先做示範模式**，登入、註冊、Google 之後再做。所以本文件的重點放在示範模式（§3），要詳細到可以直接照著實作；登入和多使用者（§4–§7）只寫到不會擋路的程度。重點是：**示範沙盒和登入後的每人資料，要用同一套隔離機制**（§2）。

---

## 0. 決策摘要

### 0.1 建議

| 問題 | 建議 | 理由（出自程式碼） |
|---|---|---|
| 示範模式跑在哪裡 | **另開一個 process**（建議用 Docker 容器）：自己的資料根目錄、自己的 hostname，前面不放密碼 proxy | 每個頁面都直接打 `/api/v1/...` 的絕對路徑（`shell.js`、各 `*.html`）。如果在同一台主機上放行 `/demo/`，proxy 分不出 `/api/v1/plan` 這個請求是示範頁發的還是擁有者發的；要放行就等於公開擁有者的 API。另開 process 的話，就算程式有 bug 也讀不到 `~/.wko5coach` |
| 每位訪客的沙盒 | **寫入時才複製**：只讀的請求一律讀共用的示範資料；第一次寫入才建沙盒（小 SQLite 加幾個 JSON），並發 cookie；24 小時後刪除 | 只看不改的爬蟲和訪客不會建任何檔案。沙盒每份不到 3 MB。Dataset 很大（幾百 MB，`wko5views._dataset_cfg`），不能每人一份，所以所有人共用一份 |
| 隔離機制 | **Tenant context**：用 `contextvars` 記住「這個請求屬於哪份資料」，所有路徑和 DB 都透過它取得。示範沙盒是一種 tenant，之後登入的使用者也是 | 現在有 20 多個模組在 import 時就把 `Path.home()/".wko5coach"` 寫死成常數（§1）。全部改成「從 tenant 取得」之後，示範和登入都靠這一處 |
| 多使用者的 DB | **每個 tenant 一個 SQLite 檔**加一個目錄，目錄結構和現在的 `~/.wko5coach` 完全相同。不在每張表加 `user_id` 欄 | 見 §2.2。最大的理由：53 處 `athlete_id = 1` 和 `DEFAULT_USER = 1` 都不用改，因為在自己的 DB 裡永遠是 1 號；而且一半以上的狀態根本不在 DB 裡（`plan.json`、FIT、快取） |
| 擁有者資料的遷移 | 擁有者就是 user #1，tenant 目錄**原地指向現在的 `~/.wko5coach`**，不搬也不複製 | 不搬就不會遺失；遷移前先備份 |
| 示範模式能改的範圍 | 課表、賽事與週期（事件、階段）、課表偏好、賽事功率計算都能改。**會改到圖表資料的設定一律唯讀**，例如門檻、體重、排除活動、校正、引擎設定、資料來源 | 共用的 Dataset 是用示範基準資料建出來的；讓訪客改門檻，就得幫他重建一份 Dataset |
| 登入（之後） | email＋密碼（argon2id）＋ Google OIDC（authlib），provider 介面預留 COROS、Strava、Garmin；session 存在伺服器端，用 httpOnly cookie，CSRF 用 double-submit | §4 |

### 0.2 和 `todo-multi-user-sharing.plan.md` §10 的關係

- **示範模式**和 §10 不衝突。示範資料是虛構的，不碰任何第三方 API，也不保管任何人的資料。它也可以拿來介紹桌面版。
- **登入加多使用者**（§4–§7）等於重新打開 §1 (d)「託管多租戶伺服器」，只是規模小。§1 (d) 和 §10.3 列的問題全都還在：
  - 記憶體：每人一份 Dataset。
  - 從同一個 IP 打非官方 API。
  - 替別人保管 token 的責任。
  - TP 的 WKO5 client 不能給別人用（§2.1）。
- 所以第二階段要不要做、做給誰用（例如只開放邀請制的幾位朋友），由擁有者決定（§9 的第 1 項）。示範模式先做，不必等這個決定。

---

## 1. 現況：單人假設的盤點（2026-10-02 讀程式碼）

### 1.1 寫死在 import 時的路徑

這些常數都在 import 時算好，所以同一個 process 只能服務一份資料。

| 模組 | 常數 | 內容 |
|---|---|---|
| `backend/db/database.py` | `DB_PATH`、`engine`、`AsyncSessionLocal` | `~/.wko5coach/wko5coach.db`，一個全域 engine |
| `backend/engine/wko5expr/datasource.py` | `_db_path()` | 同步唯讀讀 `user_settings`；`current_source()` 用它 |
| `backend/engine/activity_tags.py:113`、`plan_store.py:439/527`、`heat_data.py:101`、`fitdataset.py:130` | 各自 import `DB_PATH` | 在 worker thread 裡用 `sqlite3` 唯讀開 DB |
| `backend/engine/planning.py:28` | `PLAN_PATH` | `plan.json`：事件、週期、門檻 |
| `backend/engine/racepower/weather.py:31` | `HOME`（`WX.HOME`） | `racepower/athlete.py`、`backtest.py`、`share.py`、`cptest`、`fitdataset.py:369` 都從這裡取根目錄 |
| `backend/engine/wko5expr/dataset.py:42` | `_CACHE_DIR` | `tp_tss.json`、`moving_hrtss.json`、`power_source_v1.json`、`bad_activity_v1.json`、`series_*.json` |
| `backend/engine/wko5expr/config.py:22` | `CONFIG_PATH` | `engine.json` |
| `backend/engine/wko5expr/corrections.py:27` | `CORRECTIONS_PATH` | `corrections.json` |
| `backend/engine/wko5expr/customviews.py:47` | `USER_VIEWS` | `views/` |
| `backend/engine/wko5expr/fitcache.py:70` | `root()` | `cache/fit`；可用 env 覆寫 |
| `backend/engine/wko5expr/render_cache.py:36` | `CACHE_DIR` | `cache/render` |
| `backend/engine/achievements.py:30-31` | `CACHE_PATH`、`ANNOTATIONS_PATH` | 成就快取、註記 |
| `backend/engine/routes.py:64` | `HOME` | `routes/`；可用 env 覆寫 |
| `backend/sync/storage.py:19` | `FIT_ROOT` | `fit/<source>/<year>/` |
| `backend/settings/secrets.py:29` | `KEY_FILE` | `secret.key`（Fernet） |
| `backend/sync/tp_client.py:70` | `TP_CLIENT_FILE` | 擁有者專用 |
| `backend/sync/coros_client.py:40` | `LEGACY_COROS_FITS_ROOT` | 舊版路徑 |
| `backend/settings/paths.py`、`api/wko5views.py:48`、`api/plan.py:21`、`api/achievements.py:23`、`api/athletes.py:14`、`files/file_service.py:19` | `athlete_dir()`、`ATHLETE_DIR`、`WKO5_ROOT` | 沒設定 env 時會**自動去找 `~/WKO5`**。示範 process 如果不擋，會讀到擁有者的 WKO5 資料夾（§3.1 的啟動檢查） |

`backend/tests/conftest.py` 為了不碰真實資料，已經 monkeypatch 了 9 個這類常數。改成 tenant 之後，一個 fixture 就能取代全部。

### 1.2 Process 內的全域狀態

| 位置 | 狀態 | 多 tenant 時的問題 |
|---|---|---|
| `api/wko5views.py:57` `_dataset_cfg`（lru 4）、`_FLIGHT`、`_LIVE`、`_WARM` | Dataset 快取、single-flight 鎖、背景暖機 thread | key 沒有 tenant。`plan_changed()` 會把 `ds.plan = Plan.load()` **寫進共用的 Dataset**，示範時 A 訪客的週期會被 B 看到 |
| `api/overview.py:40` `_status_cache` | 只留 1 筆，key 是 `(id(ds), today, plan mtime, prefs, tests)` | 只有 1 筆，訪客一多就互相擠掉，每次都重算（Status 是慢的那段）。key 也沒有 tenant |
| `engine/wko5expr/buildstate.py:84` `_STATES` | 以 source 為 key 的建置進度 | 要加 tenant 或 base |
| `api/plan.py:36/206` `_wko5_settings`、`_wko5_profile`；`api/achievements.py:38` `_dataset` | lru 1，讀 WKO5 檔 | **沒有 WKO5 資料夾時會丟 `StopIteration`**（`next(ATHLETE_DIR.glob(...))`），示範模式下週期頁會壞 |
| `api/routes.py:54`、`api/achievements.py:42` | 直接用 WKO5 的 `Dataset(ATHLETE_DIR)` | 沒有 WKO5 就沒有資料 |
| `engine/plan_store.py:411-481` `_TEST_CACHE` 等 | 以 db_path 為 key | 只要 db_path 依 tenant 分開就不用改 |
| `sync/runner.py:30` `_BUSY`、`sync/coros_client.py:128/263`、`tp_client.py:565` | 同步鎖、登入鎖，以 source 或 athlete_id 為 key | 多使用者時改成 `(tenant, source)`。示範模式不掛載這些 router |
| `sync/scheduler.loop()` | 只看 user 1 | 多使用者時要逐一掃 tenant |
| `engine/plan_auto.py:72` `_TASKS` | 同步後自動重排並推 COROS | 示範模式關閉 |
| `engine/loaded_carry.py:775`、`engine/workout_review.py:1644` | 小快取 | key 加 tenant |

### 1.3 已經有、可以沿用的

- `user_settings` 表已經有 `user_id`，`SettingsRepository` 一律帶 id（`settings/repository.py`）。
- 公開分享 `/share/<id>` 已經獨立成 `share_router`（`api/racepower.py:989`），回應帶 `no-store`、`noindex`、`no-referrer`。tunnel 的密碼 proxy 已經放行 `/share/`。
- 每個頁面第一個載入的都是 `shell.js`，所以橫幅、功能開關、CSRF header 只要加在這一支。
- `backend/tests/fit_builder.py` 已經能寫出 fitparse 讀得懂的跑步 FIT，包括 Stryd developer fields 和 device_info，可以拿來當示範產生器的基礎。
- `dataset_for_source(..., today=...)` 可以指定「今天」。
- **命名衝突**：`/api/v1/auth/*` 已經被 COROS／TP 連線占用（`api/auth.py`）。App 自己的登入改放 `/api/v1/account/*`，callback 放 `/oauth/<provider>/callback`。

---

## 2. 共用機制：Tenant context（示範和登入都靠它）

### 2.1 介面

新模組 `backend/tenancy.py`：

```python
@dataclass(frozen=True)
class Tenant:
    id: str                 # "owner" | "u<user_id>" | "demo-base" | "demo-<hash>"
    kind: str               # owner | user | demo_base | demo_sandbox
    root: Path              # 這個 tenant 自己可寫的目錄（DB、plan.json …）
    shared: Path            # 大檔與快取的目錄：FIT、cache/、dataset 快取。owner／user 時 == root
    caps: frozenset[str]    # 能做什麼，見 §3.5
    wko5_dir: Path | None   # 只有 owner 有；其他人一律 None

_CURRENT: ContextVar[Tenant]
def current() -> Tenant          # 沒設定時 = owner tenant（CLI、scripts、現有測試照常）
def use(t: Tenant) -> ContextManager   # 暫時切換（建 Dataset、janitor、scheduler 用）
def private_path(name) -> Path   # root / name
def shared_path(name) -> Path    # shared / name
def db_path() -> Path            # root / "wko5coach.db"
```

- **Owner tenant 的根目錄**：env `WKO5COACH_HOME`；沒設定就是 `~/.wko5coach`，和現在一樣。
- **Request 綁定**：用一個純 ASGI middleware（`backend/tenancy_mw.py`），**不要用 `BaseHTTPMiddleware`**。它解析 cookie 得到 tenant，再 `_CURRENT.set()`。
  - 示範模式：沙盒 cookie → 沙盒 tenant；沒有 cookie → `demo_base`。
  - 之後登入：session cookie → user tenant。
  - 目前的單人模式：一律 owner。
- **DB**：`database.get_db()` 依 `db_path()` 從 engine 池取 engine。池用 LRU，上限 32，淘汰時 `dispose()`。第一次開某個檔案時跑 `init_db()`（`_migrate_schema` 本來就是冪等的）。直接用 `AsyncSessionLocal` 的地方（`wko5views.py:516/601/758`、`plan_auto.py:759`、`scheduler.py`）改成 `database.session_for(current())`。

### 2.2 每人一個 SQLite，還是加 `user_id` 欄？→ **每人一個 SQLite 檔＋目錄**

| | 每 tenant 一個目錄（SQLite＋JSON＋FIT） | 共用 DB、每表加 `user_id` |
|---|---|---|
| DB 以外的狀態 | 一樣放在 tenant 目錄，**同一套機制** | `plan.json`、`engine.json`、corrections、views、FIT、6 種快取還是要另外按人分目錄，等於兩套機制 |
| 現有的 53 處 `athlete_id = 1` | 不用改 | 全部要改成從 session 取，漏一處就是資料外洩 |
| 同步唯讀的 `sqlite3` 讀取（5 個模組） | 只換路徑 | 每個 SQL 都要加 `WHERE user_id = ?` |
| 刪除帳號、匯出、備份 | 刪除或打包一個目錄 | 逐表刪，還要另外刪檔案 |
| 擁有者遷移 | user #1 指向現有目錄，零搬移 | 要寫資料遷移 |
| SQLite 寫入鎖 | 每人各自一把 | 所有人共用一把（`database` 的部署文件已經提過 DB locked） |
| 缺點 | 跨使用者的統計要逐檔讀（目前沒有這種需求）；schema 升級在每個檔案第一次開啟時執行；engine 要做池 | — |

帳號本身（users、identities、sessions、分享索引）放在一個獨立的 `accounts.db`（§4.3）。

### 2.3 路徑分類：可寫的放 `root`，大的唯讀資料放 `shared`

| 檔案 | 分類 | 示範沙盒裡 |
|---|---|---|
| `wko5coach.db` | private | 建沙盒時從 base 複製（SQLite backup API） |
| `plan.json` | private | 複製 |
| `racepower_hike_meta.json`、`racepower_solo_hikes.json` | private | 複製（不存在就算了） |
| `corrections.json`、`engine.json`、`annotations.json`、`views/` | private | 示範模式唯讀，不複製，從 base 讀 |
| `racepower_shares/` | private | 示範模式關閉 |
| `fit/`、`cache/fit`、`cache/render` | shared | 共用 base |
| `_CACHE_DIR` 底下的 dataset 快取、`racepower_training_env.json`、`racepower_cptests.json`、`racepower_power_source.json`、`racepower_bad_activity.json`、`racepower_backtest.json`、`routes/`、`achievements_cache.json` | shared | 共用 base |
| `secret.key`、`tp_client.json`、`weather.json`（CWA 授權碼） | **server／owner 專用** | 不存在；示範模式不讀 |

### 2.4 要改的全域狀態（實作清單）

1. §1.1 的每個常數改成函式（`tenancy.private_path(...)` 或 `shared_path(...)`），呼叫端改成呼叫函式。可以一個模組一個 commit。
2. `paths.athlete_dir()` 改為回傳 `current().wko5_dir`。非 owner 一律 `None`，呼叫端要處理 `None`，見第 7 項。
3. `wko5views._dataset_key` 加 `current().shared`。示範時所有沙盒都對應到同一個 base，所以共用一份 Dataset。
4. **`ds.plan` 不再由 `plan_changed()` 寫進 Dataset**。改成 `Dataset.plan` property，回傳目前 tenant 的 `Plan.load()`，以 `(tenant.id, plan.json mtime)` memo。`racepower/athlete.py`、`overview` 和 `b2b`、`loaded_carry` 的 `status.plan` 都會自動讀到各自的週期。
5. Dataset 建置一律 `with tenancy.use(base_of(current()))`：建圖表資料時讀的 DB 和門檻都來自 base，不會因為某個沙盒觸發建置就讀到沙盒的資料。
6. `overview._status_cache` 改成 LRU 64，key 加 `tenant.id`。
7. 「沒有 WKO5 也能跑」的防護（和 sharing plan 的 §2.3、§9.5 P1 是同一件事，這裡只做最小範圍）：
   - `api/plan.py` 的 `_wko5_settings` 和 `_wko5_profile`：沒有 athlete 檔就回 `{}`。
   - `api/achievements.py` 和 `api/routes.py`：`wko5_dir is None` 時改用 `_dataset()`（目前的資料來源）。不能用時，該頁回「這個資料來源沒有提供」。
8. Thread 和 process：
   - `asyncio.create_task` 和 anyio 的 `to_thread` 會自動帶上 contextvar。
   - **`threading.Thread` 不會帶**。`wko5views.warm_up` 的 thread 要用 `contextvars.copy_context().run` 包起來，或在 thread 裡 `tenancy.use(t)`。
   - `fitcache` 的 process pool 本來就是傳完整路徑，不用改。
9. `buildstate._STATES`、`loaded_carry._PLANNED_CACHE`、`workout_review._WX_CACHE`：key 加 `tenant.shared` 或 `tenant.id`。
10. `conftest.py`：加一個 autouse fixture，把 `WKO5COACH_HOME` 設成 `tmp_path`。現有的 monkeypatch 可以逐步拿掉。

---

## 3. 示範模式（第一階段，詳細）

### 3.1 架構

```
訪客 ──https──> demo.<網域>  ──cloudflared──> 127.0.0.1:8001  示範 process（WKO5COACH_MODE=demo）
                                                            資料：<demo-root>/ 只有這個目錄
擁有者 ─https──> coach.<網域> ──cloudflared──> 密碼 proxy ──> 127.0.0.1:8000  現在的 app（不動）
                                              └ 放行 /share/（現有）
```

- **同一份程式碼**，用 `WKO5COACH_MODE=demo` 啟動。
- **建議跑在 Docker 容器 `trailcoach-demo`**：
  - 只掛 `<demo-root>`，不掛 `~/.wko5coach` 和 `~/WKO5`。這樣從檔案系統層就讀不到擁有者的資料。
  - 用非 root 使用者執行，程式碼目錄唯讀。
  - **`--cpus 1 --memory 1.5g`**：示範流量不能把整台主機拖慢。
  - 也可以放到 NAS 上。
- **示範 process 的啟動檢查**：任一項不符合就拒絕啟動。
  1. `WKO5COACH_HOME` 必須有設定，而且 resolve 後不能是 `~/.wko5coach`，也不能在它底下。
  2. `athlete_dir()` 在示範模式一律回 `None`；就算 env 有設 `WKO5_ATHLETE_DIR` 也忽略並警告。
  3. `<demo-root>/base/current` 必須存在，也就是示範資料已經產生好。
  4. 下列 router **不掛載**：`sync`、`auth`（COROS／TP 連線）、`scan`、`athletes`、`ai`、`plan_auto`。
  5. `secrets.SEALED_FILES` 清空：示範模式不會解開 repo 裡的 `tp_client.enc`。
- **不採用的做法**：在擁有者主機的 proxy 放行 `/demo/`。理由見 §0.1。另外 `frontend/dist`（如果存在）掛在 `/`，示範模式改成 `/` 導向 `/demo`。

目錄：

```
<demo-root>/
  base/
    current                  # 一行字：目前用的 base 目錄名，例如 2026-09-28
    2026-09-28/              # 一份完整的示範 tenant（結構同 ~/.wko5coach）
      wko5coach.db  plan.json  fit/coros/<year>/*.fit  cache/…  demo_manifest.json
  sandboxes/
    <sha256(cookie)[:32]>/   # 每位訪客：wko5coach.db  plan.json  meta.json
```

### 3.2 合成範例跑者（產生器）

新套件 `backend/demo/`：

| 檔案 | 內容 |
|---|---|
| `fitwrite.py` | 正式版的 FIT writer，從 `tests/fit_builder.py` 搬上來並擴充：position lat/long、altitude、cadence、lap、session totals、sport／sub_sport（跑步、越野、健行／登山）、Stryd developer fields、device_info。`tests/fit_builder.py` 改成 import 它，現有測試不變 |
| `athlete.py` | 虛構跑者的固定設定：「範例跑者」，40 歲，175 cm，70 kg，CP 220 W（Stryd），LTHR 160，AeT 142，HRmax 185，跑步效率 RE 約 1.0。**全部是虛構的，和擁有者的數值無關**，不從真實資料推出 |
| `season.py` | 一年的週期與課表：<br>・一年 3 個目標：路跑半馬（約第 18 週）、越野 50K（約第 40 週）、**百岳 3 日**（約第 30 週，單日爬升 1200–1600 m，海拔 2800–3900 m）。另有兩次 2 日的百岳暖身行程<br>・每週 4–6 次：輕鬆跑、長跑、閾值或間歇、坡道反覆、越野長跑、健行<br>・每 4 週一週恢復；有一段 10 天的感冒空窗，和幾次漏練，看起來比較真實<br>・用 `engine/planning.py` 的格式寫出事件、階段，和 3 筆有日期的門檻（CP 隨訓練從 210 W 升到 225 W） |
| `course.py` | 產生路線：路跑是起伏小的環線；越野是 8–25% 的爬坡加技術下坡；百岳是長爬坡加高海拔。GPS 軌跡放在一個固定的虛構區域，**不使用任何真實活動的軌跡** |
| `signals.py` | 產生逐秒資料：<br>・速度：目標配速加 AR(1) 雜訊，爬坡依 Minetti 成本降速（`engine/algorithms/minetti.py`）<br>・功率：依同一個成本模型換算成 Stryd 風格的功率，加入 3–5% 雜訊<br>・心率：**手腕光學心率**（沒有胸帶），也就是強度的一階延遲（τ 30–60 s）、心率飄移、高溫時加成，再加上光學誤差：起跑後 2–4 分鐘偏低、偶爾鎖到步頻（170–180 bpm 突波）、短暫掉訊號<br>・步頻；海拔加氣壓計雜訊<br>・約 10% 的跑步只有手錶功率，沒有 Stryd，用來展示 `power.accept_watch_power` 的差別 |
| `build.py` | CLI：`python -m backend.demo.build --root <demo-root> --seed 20261002 --anchor 2026-09-28 [--weeks 52]` |

`build.py` 的步驟（在 `tenancy.use(新 base)` 裡執行，**從頭到尾不碰 `~/.wko5coach`**）：

1. 用 `numpy.random.Generator(PCG64(seed))` 產生所有隨機數，不讀系統時間。所有日期都從 `anchor` 往回推。
2. 把 FIT 寫進 `fit/coros/<year>/demo_<n>.fit`，跟同步下來的檔案同一個位置，所以 `FitFolderDataset` 直接讀得到。
3. `init_db()`，再用同步匯入時的同一段程式寫入 `workout_files`，包括 trail classification 和 start_time_utc。`user_settings` 寫入：`charts.data_source = "coros"`、`athlete.timezone = "Asia/Taipei"`、`sync.*.enabled = false`、`sync.auto_on_open.enabled = false`。
4. 寫入 `plan.json`。接著用排課引擎（`engine/plan_store`、`overview.week_plan`）排好接下來 3 週的 `plan_sessions`，讓課表頁一打開就有內容。
5. **暖機**：建一次 Dataset、overview status，還有賽事功率的 athlete 模型。這會把 `cache/` 和各種 dataset 快取都先寫好，第一個訪客就不必等。
6. 寫入 `demo_manifest.json`：seed、anchor、產生器版本、活動數、所有 FIT 的 sha256。最後更新 `base/current`。

**示範資料的「今天」**：anchor 是建置那週的週一。示範 process 每週一凌晨重建一次 base：寫到新目錄，再原子地切換 `current`。這樣最後一筆活動永遠是最近幾天的，PMC 也不會越來越舊。切換後，舊 base 的沙盒會在下一個請求時重設，並顯示「示範資料已更新」。舊 base 保留一份，之後刪除。

規模估計：約 300 筆活動，平均 1 小時，1 Hz。FIT 總共約 60–90 MB。建置時間主要花在暖機，參考 `fitcache` 的 process pool，應該在幾分鐘內，實作時再量。

### 3.3 每位訪客的沙盒

| 項目 | 規則 |
|---|---|
| 建立時機 | **第一次寫入時**（非 GET 而且在寫入白名單內）。GET 一律讀 `demo_base`，不建任何東西 |
| cookie | `trc_demo` = `secrets.token_urlsafe(32)`；屬性 `HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=86400`。本機 http 測試時用 `WKO5COACH_COOKIE_SECURE=0` |
| 目錄名 | `sha256(cookie)[:32]`。從目錄名看不出 cookie |
| 內容 | 用 SQLite backup API 複製 base 的 `wko5coach.db`，再複製 `plan.json` 和 §2.3 標成 private 的 JSON。`meta.json`：`{created, last_seen, base, writes}` |
| 有效期 | **建立後 24 小時**，不延長。橫幅顯示剩餘時間。到期後的請求：刪除目錄、清掉 cookie、回到 base |
| 重設 | `POST /api/v1/demo/reset`：刪除目前沙盒，下一次寫入時重建 |
| 清理 | 示範 process 內的 janitor 每 10 分鐘執行一次：刪除到期沙盒，以及 `base` 已經不是 `current` 的沙盒。同時檢查總量上限（§3.7） |
| tenant | `Tenant(kind="demo_sandbox", root=<sandbox>, shared=<base>, caps=DEMO_CAPS, wko5_dir=None)` |
| 隱私 | 沙盒裡只有虛構資料，加上訪客自己上傳的 GPX，到期就刪。伺服器不記 IP，只在記憶體裡做限流計數 |

### 3.4 頁面與路由

| 頁面（`shell.js` 的 id） | 示範模式 | 說明 |
|---|---|---|
| `/demo`（新的 landing） | ✓ | 一句話介紹，加三張卡片：課表、賽事功率、週期，各連到對應的頁面，再加「進入示範」。說明一律放在 ? hover（記憶 `ui-concise`） |
| 總覽 `home` | ✓ 唯讀 | PMC、狀況、本週課表 |
| **課表 `schedule`** | ✓ **可寫** | 新增、編輯、刪除、拖曳課程；建議測試；步驟預覽；課表偏好；不排課日期；等效課設計。「同步 COROS」停用 |
| 圖表分析 `charts` | ✓ 唯讀 | 所有 GET；排除活動、校正、引擎設定、單筆活動編輯都鎖住 |
| 活動編輯 `activity` | ✓ 唯讀 | 只能看，不能改 |
| 路線 `routes` | ✓ 唯讀 | 不能重建、不能改名。依賴 §2.4 第 7 項 |
| **賽事周期 `plan`** | ✓ **可寫** | 事件、階段可以改；門檻和個人資料（體重）唯讀，並提示「示範模式的門檻是固定的」 |
| **賽事功率 `racepower`** | ✓ **可算** | 預估、課程（GPX 上傳有上限）、配速計畫、補給、匯出 CSV 都能用。另外內建 3 條示範賽道：路跑半馬、越野 50K、百岳 3 日，不用上傳也能試。表單可以手改 CP 或體重來試算，只影響這次計算。天氣只用 Open-Meteo（有快取，§3.7）；分享、匯出到 COROS、回測、CWA 授權碼都停用 |
| 成就 `achievements` | v1 隱藏 | 目前只讀 WKO5（`api/achievements.py:42`）。等 §2.4 第 7 項做完再開放 |
| 設定 `settings` | 隱藏 | 導覽列不顯示；直接打網址會看到唯讀頁 |
| `/share/<id>` | 停用 | 示範模式不建立分享 |

### 3.5 功能開關（caps）與後端強制

`caps` 是後端的判斷依據，前端只是跟著顯示。

| cap | owner | 示範沙盒 | 示範 base（沒有 cookie） |
|---|---|---|---|
| `plan.write`（事件、階段、課表、偏好） | ✓ | ✓ | 第一次寫入時會建沙盒，所以實際上一樣可以 |
| `thresholds.write`、`profile.write`、`dataset.write`（排除、校正、config、分類、活動編輯、routes 重建、pmc recompute） | ✓ | — | — |
| `sync`、`connect`、`push`、`upload.fit`、`ai`、`share`、`weather.key`、`backtest` | ✓ | — | — |
| `upload.gpx`（賽事功率課程） | ✓ | ✓（有上限） | ✓ |

後端強制分三層：

1. **不掛載**：§3.1 第 4 項列的 router 根本不存在，示範 host 上一律 404。這也包括 `GET /api/v1/auth/tp/callback` 這種有副作用的 GET。
2. **寫入白名單**（middleware）：示範模式裡，非 GET／HEAD 的請求只有符合下表的才放行，其他一律回 `403 {"code": "DEMO_DISABLED"}`。**預設拒絕**，之後新增的路由不會意外開放。

   | 方法與路徑 | 對應 |
   |---|---|
   | `POST/PATCH/DELETE /api/v1/overview/plan/sessions[/…]` | 課表 |
   | `POST /api/v1/overview/plan/test-suggestions/schedule`、`/steps-preview`、`/reconcile`、`/blackouts/preview`、`/equivalence/design` | 課表 |
   | `PUT /api/v1/overview/plan/prefs`、`/blackouts` | 課表偏好 |
   | `PUT /api/v1/plan/events`、`DELETE /api/v1/plan/events/{id}`、`PUT /api/v1/plan/phases` | 週期 |
   | `POST /api/v1/racepower/predict`、`/course`、`/plan`、`/export/csv`、`/hike-meta`、`/solo-hikes` | 賽事功率 |
   | `POST /api/v1/expr/evaluate` | 圖表計算（唯讀語意，有限流） |
   | `POST /api/v1/demo/reset` | 沙盒 |

   特別注意：`plan_sessions.py` 的 `push-coros`（POST、DELETE）、`racepower` 的 `share`、`export/coros`、`backtest/run`、`weather/key`，以及 `plan.py` 的 `thresholds`、`profile`、`apply-*`，**都不在白名單裡**。
3. **資料量上限**：在白名單 handler 前面檢查，見 §3.7。

示範模式下，`plan.py` 的 `_notify()` 不呼叫 `plan_auto.after_thresholds()`（反正門檻也改不了）。`autosync.js` 呼叫的 `/sync/auto` 是 404，前端看到 `mode=demo` 就不呼叫。

### 3.6 前端：session 端點、橫幅、CTA、CSRF

- **新端點 `GET /api/v1/session`**，之後登入時沿用：

      {"mode": "demo", "caps": ["plan.write", "upload.gpx"],
       "demo": {"sandbox": true, "expires_at": "…", "base": "2026-09-28"},
       "user": null}

  owner 模式回 `{"mode": "owner", "caps": [...全部], "user": null}`。
- **`shell.js`**：
  1. 啟動時抓 `/api/v1/session`，結果快取在 `window.TRC_SESSION`。
  2. `mode == "demo"` 時，在導覽列上方插入固定的橫幅：「**示範資料**　這是虛構的跑者，你的修改只存在這個瀏覽器，{剩餘時間} 後清除。」右邊兩顆按鈕：「重設示範」和 CTA。CTA 在登入上線前是「想用自己的資料？」，連到擁有者決定的頁面（§9 第 6 項）；上線後改成「建立帳號」。
  3. 導覽列依 caps 隱藏「設定」和「成就」。
  4. 頁面上的按鈕加 `data-cap="sync"` 這類屬性，`shell.js` 對缺少的 cap 統一 `disabled`，hover 顯示「示範模式不能同步」。各頁只要標屬性，不用自己寫判斷。
  5. **CSRF**：`shell.js` 包住 `window.fetch`。同源的非 GET 請求自動帶上 `X-TRC-CSRF` header，值等於 cookie `trc_csrf`（非 httpOnly，第一次 GET `/api/v1/session` 時發）。後端 middleware 比對兩者，不符就 403。這是 double-submit 加 SameSite=Lax，示範和之後的登入共用同一套。
- 403 `DEMO_DISABLED` 統一顯示成 toast：「示範模式不提供這個功能」。

### 3.7 濫用限制

單一 uvicorn worker（Dockerfile 已經固定），所以用 process 內的 token bucket（新模組 `backend/security/ratelimit.py`，約 60 行，不加套件）。

- **client IP 的取法**：只有在直接連線端是可信的 proxy 時（loopback 或 `WKO5COACH_TRUSTED_PROXIES`，也就是 cloudflared 或 Docker bridge），才讀 `CF-Connecting-IP`；其他情況用 `request.client.host`。

| 對象 | 限制 | 超過時 |
|---|---|---|
| 建立沙盒 | 每個 IP 每小時 3 個、每天 20 個 | 429；繼續用 base 唯讀 |
| 同時存在的沙盒 | 全域 300 個，`sandboxes/` 總大小 ≤ 1 GB | 淘汰最舊的沙盒 |
| 沙盒的寫入次數 | 每分鐘 60 次、每天 2,000 次 | 429 |
| 沙盒的資料量 | `plan_sessions` ≤ 400 筆、事件 ≤ 20、階段 ≤ 30；文字欄位 ≤ 200 字 | 400 |
| 重運算（賽事功率的 `/plan`、`/predict`、`/course`、`expr/evaluate`、沒命中快取的圖表、overview status） | 全域同時最多 3 個（semaphore），每個 IP 每分鐘 20 次 | 排隊最多 10 秒，之後 503 |
| 上傳 GPX | ≤ 5 MB、≤ 50,000 點、≤ 300 km；只放在記憶體或沙盒裡 | 413／400 |
| request body | 全域 ≤ 6 MB（middleware） | 413 |
| 對外呼叫（Open-Meteo） | 只透過磁碟快取；全域每天 ≤ 200 次 | 改用季節氣候值或手動輸入 |
| Cloudflare 端（建議） | 示範 hostname 開 Bot Fight Mode，再加一條 WAF rate-limit 規則（免費方案的額度以 Cloudflare 文件為準） | — |

其他：

- API 回應帶 `X-Robots-Tag: noindex`。`robots.txt` 只允許 `/demo`。
- 示範模式的 log 不記 cookie 和 IP。

### 3.8 部署：tunnel 與密碼 proxy

**擁有者主機：完全不動。**

- `coach` tunnel → 密碼 proxy → `:8000`。
- proxy 照舊只放行 `/share/`。

**示範主機：**

- 第二條 tunnel，直接連 `:8001`，前面**沒有** proxy。
  - **試用期**：可以先用 quick tunnel（`cloudflared tunnel --url http://localhost:8001`）。但每次啟動網址都會變，貼出去的連結會失效。
  - **正式**：要穩定的連結，就要在擁有者的網域上開 **named tunnel**，例如 `demo.<網域>`。已經有網域和 Cloudflare Tunnel 的話可以沿用。
- 示範 process 的環境變數：

      WKO5COACH_MODE=demo
      WKO5COACH_HOME=<demo-root>
      WKO5COACH_NO_SCHEDULER=1        # 不跑同步排程；示範的 janitor 與每週重建另有開關
      WKO5COACH_TRUSTED_PROXIES=127.0.0.1,172.16.0.0/12
      uvicorn backend.main:app --port 8001 --workers 1

- `docker-compose.yml` 加 `trailcoach-demo` service：只掛 `<demo-root>`；不帶 `HOME` 對應，也不掛 `~/WKO5`。
- 安全上的保證來自三層：
  1. 示範 process 讀不到擁有者的資料：容器沒掛載，加上 §3.1 的啟動檢查。
  2. 示範主機和擁有者主機是不同的 hostname，各自有各自的 tunnel。
  3. 擁有者主機的 proxy 設定完全不變。

**實作者不能碰的東西**：port 8000 上的 process、`~/.wko5coach`，以及任何密碼。示範的開發和測試一律用 `:8001` 或 TestClient，搭配 tmp 目錄。

### 3.9 測試

所有測試都不讀 WKO5 資料夾和真實 DB：用 `tmp_path` 當 `WKO5COACH_HOME`，用產生器的小規模模式產生資料。

| 測試檔 | 驗證內容 |
|---|---|
| `test_demo_generator.py` | 1. **決定性**：同樣的 seed 和 anchor 跑兩次，`demo_manifest` 的 sha256 完全相同；換 seed 就不同。<br>2. **合理性**（用 `--weeks 8` 加完整一年的統計摘要，不寫檔）：每週時數的分布、恢復週、≥90% 的跑步有 Stryd 欄位、心率在 90–195 之間、百岳行程單日爬升 ≥ 1000 m、事件和階段的日期順序正確。<br>3. `fit_to_channels` 讀得懂每一個產生的檔案；`FitFolderDataset` 建得起來，PMC 不為空。<br>4. 輸出裡不含任何擁有者的字串（擁有者的名字、帳號、真實路徑） |
| `test_tenancy.py` | 1. 預設是 owner tenant，`WKO5COACH_HOME` 生效。<br>2. 在 `use()` 裡，§1.1 的每個路徑函式都換到新根目錄（參數化列出所有模組）。<br>3. warm-up 的 thread 也拿得到 tenant。<br>4. 示範模式的啟動檢查：根目錄等於或位在 `~/.wko5coach` 底下時拒絕啟動；`athlete_dir()` 是 `None` |
| `test_demo_sandbox.py` | 1. 沒有 cookie 的 GET 不會建目錄。<br>2. 第一次寫入會發 cookie、建目錄。<br>3. **A 和 B 兩個 client 互相看不到對方的事件和課表**。<br>4. 寫完之後 base 目錄的 hash 沒變。<br>5. 重設。<br>6. 用假時鐘測 24 小時到期和 janitor。<br>7. base 切換後沙盒會重設。<br>8. 總量上限與淘汰 |
| `test_demo_routes.py` | 1. **列舉 `app.routes`**：示範模式下，每一個非 GET 路由要嘛在白名單裡，要嘛回 403。新增路由卻沒分類就會失敗。<br>2. 不掛載的 router 回 404。<br>3. 缺少或不符的 CSRF header 回 403。<br>4. 門檻、push-coros、share 回 403 |
| `test_ratelimit.py` | 用假時鐘測 token bucket；只有可信 proxy 才讀 `CF-Connecting-IP`；GPX 和 body 的大小上限 |
| `test_session_endpoint.py` | owner 和 demo 兩種模式的回應內容 |
| 手動或 e2e（可選，Playwright） | 每頁都有橫幅；`data-cap` 標記的按鈕是 disabled；三個主要流程各走一次 |

---

## 4. 登入與帳號（第二階段，簡述）

### 4.1 範圍

- **email＋密碼**：
  - 雜湊用 `argon2-cffi`（argon2id，使用套件預設參數）。
  - 密碼至少 10 個字元。
  - email 驗證和忘記密碼要先有寄信服務，留到之後做（§9 第 4 項）。在那之前，忘記密碼由擁有者手動重設。
- **Google OIDC**：
  - 用 `authlib` 的 Starlette client。scope 是 `openid email profile`，帶 state、nonce 和 PKCE。
  - `GOOGLE_CLIENT_ID` 和 `GOOGLE_CLIENT_SECRET` **只從 env 讀**，不 commit，也不放進 tenant 目錄。
  - redirect 是 `https://<固定網域>/oauth/google/callback`。
- **provider 介面**（`backend/account/providers/`）：

      class IdentityProvider(Protocol):
          id: str; display_name: str
          def authorize_url(self, state, nonce, code_verifier) -> str
          async def handle_callback(self, request) -> ExternalIdentity  # provider, subject, email, email_verified

  - 依 env 有沒有設定，決定註冊哪些 provider。
  - 預留的位置：
    - `coros`：Partner OAuth，要 COROS 審核。或 COROS MCP 的 OIDC，issuer `mcpus.coros.com`、有 `openid` scope，見 sharing plan §9.1，待實測。
    - `strava`：OAuth2，沒有 OIDC，subject 用 athlete id。
    - `garmin`：限企業申請，§9.2。
  - v1 只有 `password` 和 `google`。
- **帳號連結**：外部身分以 `(provider, subject)` 為唯一鍵。用 email 自動合併帳號，只限 `email_verified = true`，而且要使用者在畫面上確認。

### 4.2 Session、CSRF、限流、刪除帳號

- **Session**：
  - 存在伺服器端：`sessions` 表只存 token 的 sha256。cookie `trc_session` 是 32 bytes 的隨機值，`HttpOnly; Secure; SameSite=Lax`。
  - 30 天，使用中會延長；登入時換新 token。登出就刪掉那一列。
  - 不用 JWT，因為要能立即撤銷。
- **CSRF**：和示範模式同一套（§3.6 的 double-submit 加 `X-TRC-CSRF`）。OAuth callback 另外靠 state 驗證。
- **登入限流**（同一個 `ratelimit.py`）：
  - 每個 IP 15 分鐘內最多 10 次。
  - 同一個帳號連續失敗 5 次，就鎖 15 分鐘，之後指數增加。
  - 錯誤訊息一律寫「帳號或密碼錯誤」。查不到帳號時也做一次假的雜湊，讓回應時間一致。
- **刪除帳號**：
  - 要重新輸入密碼，或重新做一次 OAuth。
  - 刪除 `accounts.db` 裡該使用者的所有列，再刪除 tenant 目錄（裡面包括同步憑證和分享），立即執行。
  - 刪除前提供「下載我的資料」：把 tenant 目錄打包成 zip。
  - 擁有者（user #1）不能從網頁刪除。

### 4.3 `accounts.db`（server 全域，和 tenant 分開）

- `users(id, email UNIQUE NULL, email_verified_at, password_hash NULL, display_name, role owner|user, tenant_dir, created_at)`
- `identities(id, user_id, provider, subject, email, UNIQUE(provider, subject))`
- `sessions(token_hash PK, user_id, created_at, last_seen_at, expires_at)`
- `shares(id PK, user_id)`：公開的 `/share/<id>` 用它找到該使用者的 tenant；快照檔本身仍放在 tenant 目錄
- `invites(code, created_by, used_by, expires_at)`：如果擁有者決定採邀請制

---

## 5. 多使用者資料隔離與擁有者遷移（第二階段，簡述）

- **機制**：§2 的 tenant 機制。user tenant 的 `root` 和 `shared` 都是 `<server-root>/users/<id>/`，`caps` 依角色決定，`wko5_dir` 只有 owner 有。
- **擁有者遷移**，零資料遺失：
  1. 先停掉 `:8000`，把 `~/.wko5coach` 整個複製一份當備份（由擁有者自己操作）。
  2. 建立 `accounts.db`，`users` 第 1 列設 `role=owner`、`tenant_dir=~/.wko5coach`，也就是原地指向，不搬檔案。
  3. 驗證：拿遷移前後的 overview、PMC、課表 API 回應做 diff，應該完全相同。
- **還要改的全域狀態**（§1.2）：
  - Dataset 的 LRU 加上記憶體上限，每人一份，預設最多 3 份同時常駐，見 sharing plan §3。
  - `runner._BUSY` 和各種登入鎖的 key 改成 `(tenant, source)`。
  - `scheduler.loop()` 逐一掃有開同步的 tenant，錯開時間，每次最多 1 個同步。
  - `secrets` 只用一把 server key（env `WKO5COACH_SECRET_KEY`）。每人的 sealed 值存在各自的 DB 裡，key 本身不放進任何 tenant 目錄。
- **只屬於 owner 的東西**：WKO5 資料夾、TP 的 WKO5 client（`tp_client.enc`，不能給別人用，§2.1）、CWA 授權碼、AI 的 API key。其他使用者的 tenant 一律讀不到。

---

## 6. 新使用者怎麼把資料匯進來（第二階段，簡述）

1. **上傳 FIT**（預設，零條款風險）：
   - `POST /api/v1/import/files`：可以一次多個 `.fit` 或 `.fit.gz`。
   - `POST /api/v1/import/zip`：接受 Strava 和 Garmin 的匯出格式，包括巢狀 ZIP。檔案上限 500 MB，邊收邊寫磁碟；壓縮比上限 100、檔案數上限 20,000、巢狀最多 2 層，防止 zip bomb。
   - 存放、去重、`manual` 來源的規則，照 sharing plan §9.3。匯入完重建該 tenant 的 Dataset。
2. **COROS／TP 帳密同步，每人自己選擇開啟**：
   - 沿用現有的 `api/auth.py` 和 `sync/*`，只是改到 tenant 底下。
   - 預設關閉。開啟前顯示 sharing plan §7.5 的聲明。
   - 擁有者可以用 `WKO5COACH_ALLOW_CREDENTIAL_SYNC=0` 全站關閉。
   - **風險**：所有人都從同一台 server 的 IP 打非官方 API，被封鎖的風險比桌面版高（sharing plan §1 (d)、§10.3）；而且我們等於在保管別人的帳密和 token。
   - TP 只能用網站登入，WKO5 client 只限 owner 使用。
3. **COROS 官方 OAuth**：只留 §4.1 的 provider 位置，拿到 Partner 核准或 MCP 實測通過後再做。

---

## 7. 部署：登入上線後

- **App 主機**改成由 app 自己做登入，**拿掉密碼 proxy**，因為兩層密碼會擋掉 OAuth callback 和公開路徑。
- **公開路徑**（app 端的 allowlist，其他路徑都要登入）：
  - `/login`、`/signup`
  - `/oauth/*/callback`
  - `/api/v1/account/login|signup|logout`
  - `/api/v1/session`
  - `/share/*`
  - `/api/v1/static/*`
  - `/healthz`
  - 示範主機維持獨立（§3.8），不受影響。
- **網域**：Google OAuth 的 redirect URI 必須是固定網址，而 quick tunnel 每次啟動網址都會變，所以**一定要用 named tunnel 加自己的網域**。
- **替代方案**（§9 第 3 項）：Cloudflare Access（Zero Trust）可以直接提供 Google 或 email OTP 登入，app 讀 `Cf-Access-Jwt-Assertion` 就能知道是誰，對應到 tenant。幾乎不用寫登入程式。缺點：
  - 沒有「自己註冊帳號密碼」。
  - 免費方案有人數上限，以 Cloudflare 文件為準。
  - 登入體驗綁在 Cloudflare 上。

---

## 8. 分階段

大小：S ≈ 1–2 天，M ≈ 3–5 天，L ≈ 1–2 週。

| 階段 | 內容 | 大小 | 依賴 |
|---|---|---|---|
| **D0a** 不依賴 WKO5 的最小防護 | §2.4 第 7 項：plan、achievements、routes 在沒有 WKO5 時也能跑 | S | — |
| **D0b** Tenant 機制 | `tenancy.py`、ASGI middleware、engine 池、§1.1 常數全部改成函式、`ds.plan` property、status cache、thread 傳遞、conftest fixture。owner 的行為完全不變 | M | — |
| **D1** 示範產生器 | `backend/demo/`：fitwrite、athlete、season、course、signals、build；決定性測試 | M | D0b（build 要在 tenant 裡跑） |
| **D2** 示範 process 與沙盒 | `WKO5COACH_MODE=demo`、啟動檢查、不掛載的 router、寫入白名單、沙盒的生命週期與 janitor、每週重建、`/api/v1/session`、`/api/v1/demo/reset`、CSRF、限流 | M | D0b、D1 |
| **D3** 示範前端 | `shell.js` 的橫幅、CTA、`data-cap`、fetch wrapper；`/demo` landing；3 條示範賽道；各頁標上 `data-cap` | S–M | D2 |
| **D4** 上線示範 | Docker service、第二條 tunnel（先 quick，再 named）、Cloudflare 的 bot／rate 規則、上線檢查清單（從擁有者主機確認示範主機讀不到真實資料） | S | D3 |
| A1 帳號 | `accounts.db`、email＋密碼、session、登入限流、刪除帳號、邀請碼 | M | D0b |
| A2 Google 與 provider 介面 | authlib、provider registry、帳號連結 | S–M | A1、固定網域 |
| A3 多使用者 tenant | user tenant、擁有者原地遷移與 diff 驗證、Dataset LRU 和記憶體上限、拿掉密碼 proxy | M | A1 |
| A4 匯入 | 單檔、多檔、ZIP（Strava、Garmin），`manual` 來源 | M | A3、sharing plan §9.3 |
| A5 每人的同步連線 | tenant 化的同步、排程逐一掃 tenant、全站開關、聲明 | M | A3 |
| A6 COROS 官方 provider | 等 Partner 核准或 MCP 實測 | ? | 外部核准 |

**示範模式最短的上線路徑：D0a → D0b → D1 → D2 → D3 → D4**，約 3–4 週。
- D0b 是**示範和登入共用**的基礎；做完之後，A1–A3 不必再改路徑。
- D1 和 D0b 可以部分平行：產生器的訊號模型和 FIT writer 不依賴 tenant。
- 搬到雲端主機時的儲存工作（C1–C5）見 §10.7，跟著 A3 一起做，示範模式不需要。

---

## 9. 擁有者要決定的事

1. **第二階段（登入加多使用者）要不要做、開放給誰**：
   - 這等於部分推翻 sharing plan §10「桌面 app、各用各的」，改成在自己的機器上替別人保管資料。
   - 選項：只做示範／邀請制給幾位朋友／公開註冊。
   - 建議先做示範，看反應再決定。
2. **示範 process 跑在哪裡**：這台 Windows 開發機的 Docker（要限制 CPU 和記憶體），還是 NAS。
3. **網域與 tunnel**：
   - 示範要穩定的連結，就需要 named tunnel 加子網域，例如 `demo.<網域>`。
   - **Google OAuth 必須有固定網域**，quick tunnel 不行。
   - 登入要自己做，還是改用 Cloudflare Access（§7）。
4. **Google OAuth client 的申請**：
   - 要在 Google Cloud Console 建 OAuth client，設定同意畫面（app 名稱、隱私權政策網址、支援 email），加上 redirect URI。
   - client id 和 secret 由擁有者放進 env，不交給 Claude，也不 commit。
   - 只用 `openid email profile` 的話，不需要 Google 審核；但同意畫面還是要有隱私權政策網址。
   - 另外：要不要接寄信服務（email 驗證、忘記密碼），以及用哪一家。
5. **示範內容**：
   - 虛構跑者的設定，§3.2 是預設值。
   - 3 條示範賽道用什麼：完全虛構，還是用公開賽事的 GPX（要注意授權）。
   - GPS 軌跡放在哪個區域。
6. **CTA 連到哪裡**：登入上線前，「想用自己的資料？」要連到什麼：桌面版下載頁、Google 表單（等候名單），還是 email。
7. **示範的限制數值**：§3.7 的數字是起始值；沙盒保留 24 小時是否可以。
8. **COROS／TP 帳密同步要不要開放給其他使用者**（第二階段）：條款風險和保管責任在擁有者身上。預設建議：只開 FIT 匯入，不開帳密同步。
9. **隱私權政策與服務條款**：開放註冊前必須要有，因為台灣個資法下心率可能屬於健康資料（sharing plan §10.5）。示範模式不收任何個資，可以先上線。

---

## 10. 雲端託管的儲存（之後，開放登入時才做）

前提沿用 §2.2：**每個 tenant 一個目錄、一個 SQLite**。本節回答「搬到雲端主機時，這個目錄要放哪裡」：示範先放 Hugging Face Spaces，之後登入版放 Oracle Cloud 免費 VM，前面接 Cloudflare（Tunnel、R2）。**現在不做**；示範模式（§3）不需要本節的任何東西，理由見 §10.6。

### 10.1 實測：一位使用者有多大（2026-10-02，一位實際使用者的資料目錄，唯讀量測）

| 項目 | 內容 | 大小 |
|---|---|---|
| `wko5coach.db` | `workout_files` 1,895 列、`workout_metrics` 7,481 列、`mmp_cache` 35,830 列；`journal_mode=delete` | **3.3 MB** |
| `fit/coros/` | 809 個 `.fit`，平均 147 KB，沒有 `.fit.gz` | 116.4 MB |
| `fit/tp/` | 1,086 個 `.fit`，平均 122 KB，沒有 `.fit.gz` | 129.2 MB |
| `cache/fit/` | 1,880 個 `.npz`（逐秒通道，float64，`np.savez_compressed`）＋兩個 `index.json`（0.7＋0.9 MB） | 89.2 MB（磁碟佔用 118 MB） |
| `cache/render/` | 圖表 JSON 快取，有容量上限、LRU 淘汰 | 89 MB |
| 整個目錄 | | **536 MB** |

- COROS 和 TP 大多是**同一批活動的兩份**。一般使用者只會接一個來源，所以估計「約 800 筆活動」的使用者：FIT 約 120 MB、npz 約 45 MB、DB 約 3 MB。
- **DB 只佔 1% 不到**，大的是 FIT 原檔和衍生快取。所以「SQLite 夠不夠」不是容量問題，是寫入併發和部署形態的問題（§10.2）；真正要設計的是 FIT 放哪裡（§10.3）。

### 10.2 SQLite 夠不夠

**夠。** 每人一個 3 MB 級的檔案，寫入來源只有「這個人自己的請求」和「這個人的同步」，SQLite 的單一寫入鎖只會鎖到自己。示範沙盒（§3.3）也是同一個模式。

要先補的兩件事（雲端化時做，C3）：

1. **改成 WAL**（`PRAGMA journal_mode=WAL`，加 `busy_timeout`）。目前是 `delete` 模式；Litestream（§10.5）一定要 WAL。要確認 5 個用 `sqlite3` `mode=ro` 開 DB 的模組（§1.1）在 WAL 下還能讀：唯讀連線需要 `-shm` 檔，目錄要可寫或檔案已存在。
2. **`accounts.db` 是唯一全站共用的寫入點**（§4.3）。`sessions.last_seen_at` 不要每個請求都寫，改成最多每 5 分鐘寫一次，避免所有人搶同一把鎖。

**什麼時候才需要 Postgres**（任一項成立才換，換的話是 L）：

| 情況 | 為什麼 SQLite 不行 |
|---|---|
| 要跑**多個 app instance**（水平擴充、藍綠部署、平台自動開多個 replica） | SQLite 是本機檔案，不能被兩台機器同時寫；網路檔案系統上的 SQLite 鎖不可靠 |
| 平台**沒有持久磁碟**又不能接受「開機先還原」 | 每次重啟都要從備份還原，等於把備份當主資料庫 |
| 同一份資料有**大量同時寫入**（例如共用的社群、排行榜） | 單一寫入鎖排隊；目前沒有這種功能 |
| 需要**跨使用者查詢**（全站統計、教練看多位學員） | 每人一檔要逐檔打開；§2.2 已列為缺點，目前沒有需求 |

這幾項在「單機、邀請制幾位朋友」的規模都不會發生。真的要換時，tenant 的 `root` 目錄仍保留給 JSON 和快取，只有 DB 換掉，而且 53 處 `athlete_id = 1` 就得改了（§2.2）。

### 10.3 FIT 原檔：儲存介面＋一律 gzip

#### 介面

新模組 `backend/files/fitstore.py`。key 是 tenant 內的相對路徑，例如 `coros/2026/<labelId>_2026-09-30_run.fit.gz`：

```python
class FitStore(Protocol):
    def put(self, key: str, fit: bytes) -> str        # 一律 gzip 後存；回傳實際 key（.fit.gz）
    def get(self, key: str) -> bytes                  # 回傳解壓後的 FIT bytes
    def delete(self, key: str) -> None                # 取代 purge.py 直接 unlink
    def list(self, prefix: str = "") -> Iterator[FitObj]   # key、size、stamp（mtime 或 ETag）
    def local_path(self, key: str) -> ContextManager[Path] # 仍需要路徑的舊呼叫端用；遠端時下載到暫存檔
```

- `LocalDirStore(root=tenant.shared / "fit")`：現在的行為，`confined()` 的防護搬進來（拒絕 symlink、拒絕 key 跳出 root）。
- `S3Store(bucket, prefix="users/<id>/fit/")`：Cloudflare R2 或任何 S3 相容服務，用 `boto3`。R2 沒有出口流量費，適合「原檔放遠端、需要時才拉」。
- `workout_files.file_path` 目前存**絕對路徑**而且 unique。改成存 key，並寫一次性遷移把絕對路徑轉成 key（和 C2 同一支腳本）。
- `fitcache` 的有效條件目前是 `(size, mtime_ns)`。遠端時改用 `list()` 給的 `stamp`（ETag）。npz 檔名是 `sha1(rel)`，所以 key 改名會讓快取全部失效（見下方遷移腳本的處理）。

#### 要改走介面的呼叫端（2026-10-02 讀程式碼）

| 類別 | 位置 | 現在的做法 | 注意 |
|---|---|---|---|
| 寫入 | `sync/coros_client.py:494-505` | `tmp.write_bytes` 再 `replace` 成 `.fit` | 改成 `store.put`；`fit_session_sport(tmp)` 改讀 bytes |
| 寫入 | `sync/tp_client.py:947-951` | `save_path.write_bytes(raw)`，已先 gunzip | 改成 `store.put`（重新 gzip） |
| 刪除 | `sync/purge.py:39-71` | `confined()` 後 unlink，另外掃資料夾刪孤兒檔 | `store.delete`、`store.list` |
| 改名 | `scripts/migrate_coros_sport_names.py:38-80`、`scripts/migrate_fit_folders.py` | `rglob("*.fit")`、rename、改 `file_path` | 一次性腳本；gzip 之後的檔案它看不到，要改或標記為已完成 |
| 列舉 | `engine/wko5expr/fitdataset.py:511`（`FitFolderDataset`） | `rglob("*.fit")`＋`rglob("*.fit.gz")` | 已支援 gz |
| 列舉 | `engine/wko5expr/datasource.py:109`（`_files_stamp`） | `rglob("*.fit*")` | 已涵蓋 gz；遠端改用 `list()` |
| 列舉 | `engine/wko5expr/fitdataset.py:371`、`engine/racepower/cptest.py:189` | **只有** `rglob("*.fit")` | **gzip 之後會默默漏掉檔案**，必須先修 |
| 列舉 | `files/file_service.py:44`（`discover_workout_files`，scan） | 只收 `.wko4`、`.fit` | 同上；`_sync_ids` 用 `name.split(".")[0]`，gz 沒問題 |
| 列舉 | `sync/storage.py:63`（`folder_stats`） | `rglob("*")` | 改用 `list()` 加總 |
| 讀取（bytes） | `files/fit_to_channels.py:281`（`fit_to_channels`）← `fitcache.parse_file:146`、`cptest.py:219/266`、`scripts/scan_bad_activities.py` | 看 gzip magic 自動解壓 | 已支援 gz；只要改成從 `store.get` 拿 bytes |
| 讀取（bytes） | `engine/interval_reps.py:49-57`（`_fit_laps`）、`scripts/backfill_rpe.py:34` | 看 magic 或副檔名解壓 | 已支援 gz |
| 讀取（路徑） | `files/fit_reader.py:69`（`parse_fit`：`fitparse.FitFile(path)`、`fitdecode.FitReader(path)`）← `api/workouts.py:248/271/321/374`、`api/athletes.py:204`、`files/file_service.py`（匯入）、`sync/coros_sport.py:79`、`engine/racepower/cptest.py:135`、`scripts/backfill_start_time.py`、`scripts/compare_sources.py` | 直接開路徑，**不支援 gz** | 改成 `parse_fit_bytes(store.get(key))`（fitparse 和 fitdecode 都吃 file-like）。這是 gzip 前**一定要先做**的一項 |

#### 為什麼一律 gzip（實測）

隨機抽 50 個 FIT（COROS、TP 各 25 個，seed 20261002），**複製到 scratch 的獨立資料夾**再壓縮，量完即刪，沒有碰 `~/.wko5coach`：

| | 大小 | 比例 |
|---|---|---|
| 原檔 | 9.21 MB | 100% |
| gzip -6 | 3.26 MB | **35.4%（2.82 倍）**，每檔 6.4 ms |
| gzip -9 | 3.26 MB | 35.4%，沒有比 -6 好 |

- 換算 §10.1 那位使用者的 1,895 個檔案：245.6 MB → 約 **87 MB**；一般單一來源的使用者約 120 MB → 約 43 MB。
- 解壓成本相對於 fitdecode 解析（純 Python，幾百個檔要幾分鐘，`fitcache.py` 開頭的說明）可以忽略。
- 用 gzip 而不是 zstd：標準函式庫就有，`fit_to_channels` 已經在用，TP 下載本來就是 gzip。

#### 一次性遷移腳本 `backend/scripts/gzip_fits.py`（C2）

前提：C1 已經上線（所有讀取端都支援 gz），而且擁有者已停掉 `:8000` 並備份 `~/.wko5coach`（同 §5 的作法，由擁有者操作）。

1. 預設 **dry-run**：列出要轉的檔案數、預估省下的空間，什麼都不改；`--apply` 才動手。
2. 只處理 `storage.confined()` 通過的檔案（每個來源資料夾裡、非 symlink）。
3. 每個檔案：讀原檔 → `gzip -6` 寫到 `<name>.fit.gz.tmp` → **解壓後和原檔逐 byte 比對、記 sha256** → fsync → `os.replace` 成 `.fit.gz`。
4. 同一個 transaction 裡更新 `workout_files.file_path`（每 100 筆 commit 一次）；DB 更新成功後才刪原檔。中途中斷再跑一次會從斷點繼續（已有 `.fit.gz` 而且內容相符的就跳過）。
5. **保住 fitcache**：解析結果只取決於解壓後的內容，所以腳本順便把 `index.json` 的 entry 從舊 rel 搬到新 rel、更新 size 和 mtime，並把 npz 改名成 `sha1(新 rel)`。不做的話下次啟動要重新解析全部 1,880 個檔案（冷快取，幾分鐘）。
6. 結束時印出前後大小、筆數、失敗清單。同時把 sync 的寫入端改成直接存 `.fit.gz`（C1 已做），之後不會再產生新的 `.fit`。

測試：`tmp_path` 加 `tests/fit_builder.py` 產生的檔案；驗證 dry-run 不改任何東西、內容 round-trip、`file_path` 更新、中斷後重跑、symlink 和資料夾外的檔案不動、fitcache 不重新解析。

### 10.4 逐秒衍生快取：維持 npz，不換 Parquet

同樣抽 50 個 `cache/fit` 的 npz（共 131,514 秒的資料）複製到 scratch 轉檔比較（暫存的 venv 裡裝 `pyarrow` 25.0.1，量完即刪）：

| 格式 | 大小 | 相對 npz |
|---|---|---|
| 現在：`np.savez_compressed`（float64，NaN 表示沒資料） | 2.56 MB | 100% |
| Parquet＋zstd 3，float64 | 3.28 MB | 128% |
| Parquet＋zstd 3，float32 | 2.58 MB | 101% |
| Parquet＋zstd 9，float32＋byte_stream_split | 2.56 MB | 100% |

- **結論：不換。** Parquet 最好也只打平，還要多一個安裝後約 90 MB 的 `pyarrow` 依賴（容器變大、冷啟動變慢）。Parquet 的強項（欄位裁切、跨檔查詢）在這裡用不到：每次都是整檔讀一個活動的所有通道。
- 這份快取**可以從 FIT 重建**，所以不需要備份，也不需要放物件儲存。雲端主機上放在本機磁碟（或 HF 的暫存磁碟）就好；遺失的代價只是一次冷啟動。
- 真的要省空間時，比較有效的是 float32 的 npz（大約減半），但會改到數值精度，要先跑圖表一致性測試（記憶 `wko5-coach-port-goal`），目前不值得。

### 10.5 備份：Litestream → Cloudflare R2

| 對象 | 作法 |
|---|---|
| 每個 tenant 的 `wko5coach.db`、`accounts.db` | **Litestream** 持續複寫到 R2（`s3://<bucket>/litestream/<tenant>/`）。Litestream v0.5 起會自動辨識 `*.r2.cloudflarestorage.com` 端點，並把並行上傳設為 2（R2 的限制） |
| `plan.json`、`engine.json`、`corrections.json`、`views/` 等小 JSON | 每天一次打包上傳（每人幾十 KB） |
| FIT 原檔 | 用 `S3Store` 時本身就在 R2；用 `LocalDirStore` 時每天同步新增的檔案（只傳新的） |
| `cache/fit`、`cache/render` | **不備份**，可重建 |

R2 免費額度（每月；以 Cloudflare 文件為準）：**10 GB 儲存、100 萬次 Class A（寫入／列舉）、1,000 萬次 Class B（讀取）、出口流量免費**。換算：

- 儲存：每位單一來源使用者的 gzip FIT 約 43 MB，DB 約 3 MB → 10 GB 約可放 **200 位**。
- **寫入次數才是限制**：Litestream 有變更時每個同步間隔就上傳一次。如果一直有寫入，1 秒一次就是一個月 260 萬次，超過免費額度。所以 `sync-interval` 設 **30–60 秒**，另外 snapshot 每天一次、保留 7 天。沒有變更的 DB 不會上傳。
- 多個 tenant DB：Litestream 設定檔要列出每一個 DB。要確認 v0.5 的目錄監看設定是否可用；不行的話由 app 在建 tenant 時重寫設定檔並重啟 Litestream。

**還原演練**（上線前做一次，之後每季一次，寫成 `docs/deploy/restore-drill.md`）：

1. 在另一個空目錄（或另一台機器）跑 `litestream restore -o <tmp>/wko5coach.db s3://…/<tenant>`，也試一次 `-timestamp`（還原到某個時間點）。
2. `PRAGMA integrity_check` 必須是 `ok`。
3. 用還原出來的目錄起一個 `:8001` 的 process（不碰 `:8000`），拿 overview、PMC、課表 API 的回應跟正式機 diff，應該相同（同 §5 第 3 步）。
4. 記錄花了多久、資料落後多少秒。
5. 開機流程用 `litestream restore -if-db-not-exists -if-replica-exists`，再用 `litestream replicate -exec "uvicorn …"` 啟動 app，讓暫存磁碟的平台也能用（§10.6）。

### 10.6 各平台怎麼放

| 平台 | 磁碟 | DB | FIT 原檔 | 逐秒快取 | 備份 | 適合 |
|---|---|---|---|---|---|---|
| **Hugging Face Spaces**（CPU basic：2 vCPU、16 GB RAM，免費） | 50 GB **暫存**，重啟或暫停就清空；持久儲存要另外付費（20 GB 每月 5 美元起）或掛 Storage Bucket | 示範：不需要持久。登入版：開機 `litestream restore`，執行中持續複寫 | 示範：開機時用產生器建好（或打包進 image）。登入版：`S3Store`（R2） | 本機暫存磁碟，重啟後重建 | Litestream → R2 | **示範模式**：資料是虛構的、每週重建（§3.2），沙盒 24 小時就刪，本來就不需要持久。不建議放登入版：每次重啟都要還原 DB、重建快取，而且平台可能同時開兩個 instance |
| **Oracle Cloud 免費 VM**（Always Free Ampere A1，最多 4 OCPU／24 GB RAM、200 GB 區塊儲存；閒置可能被回收，以 Oracle 文件為準） | 持久的區塊儲存 | 本機 SQLite（WAL），每人一檔 | `LocalDirStore` 就夠；容量不夠再換 `S3Store` | 本機 | Litestream → R2（異地），另加區塊儲存的定期快照 | **登入版的首選**：和現在的本機架構最接近，前面接 Cloudflare Tunnel（§7） |
| **Cloudflare D1＋R2** | 沒有本機磁碟（Workers） | D1：每個 DB 免費方案 500 MB、付費方案 **10 GB**；天生就是「每人一個 DB」的模型 | R2 | 要另外放（R2 或 KV） | D1 內建 Time Travel（免費 7 天） | **不建議**：引擎是 Python＋numpy＋fitdecode，解析要用 process pool 跑幾分鐘，不能放進 Workers。要用就等於重寫後端。Cloudflare 的角色維持在 Tunnel、R2、WAF |

大小上限都不是問題：DB 3.3 MB 離 D1 的 10 GB、HF 的 50 GB 都很遠。真正決定平台的是**磁碟會不會消失**和**會不會同時有兩個 instance**（§10.2）。

### 10.7 分階段

**現在不做。** 示範模式放 HF 不需要這些；等第二階段開放登入、要離開這台 Windows 機器時才做。大小的定義同 §8。

| 階段 | 內容 | 大小 | 依賴 |
|---|---|---|---|
| **C1** FIT 儲存介面 | `fitstore.py`（`LocalDirStore`）、`parse_fit_bytes`、§10.3 表中所有呼叫端改走介面、只認 `.fit` 的 4 處修好、sync 改成直接存 `.fit.gz`、`file_path` 改存 key | M | D0b（root 來自 tenant） |
| **C2** gzip 遷移腳本 | `scripts/gzip_fits.py`：dry-run、驗證、可續跑、保住 fitcache；擁有者停機備份後執行 | S | C1 |
| **C3** WAL＋Litestream | WAL 與唯讀連線檢查、`accounts.db` 寫入節流、Litestream 設定（R2、30–60 秒）、小 JSON 每日打包、還原演練文件與第一次演練 | S | A1 |
| **C4** `S3Store`（R2） | boto3 實作、`fitcache` 改用 ETag、本機暫存與快取重建 | M | C1 |
| **C5** 上 Oracle VM | 部署、Tunnel、區塊儲存快照、上線前做一次還原演練 | S–M | C3、A3 |
| （必要時）Postgres | 只有 §10.2 表中任一情況成立才做 | L | — |

建議順序：**C1 → C2 → C3 → C5**；C4 等單機磁碟不夠或要上沒有持久磁碟的平台時才做。
