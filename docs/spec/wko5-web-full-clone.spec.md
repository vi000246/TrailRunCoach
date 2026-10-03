# SRS: Personal Training AI — Full Algorithm Engine + Custom Dashboard + Coros Sync

## Metadata
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md` (partial context)
- **Source Linear Issue**: N/A — standalone
- **Owner**: maintainer
- **Status**: DRAFT
- **Generated**: 2026-05-14
- **Updated**: 2026-05-15 — 改為 Coros-first，獨立 fits 資料夾，移除 WKO5 目錄依賴
- **Supersedes**: `docs/spec/wko5-milestone1-fit-mmp.spec.md` (Milestone 1 becomes a sub-component)
- **See also**: `docs/spec/wko5-coros-sync.spec.md` (Coros sync 詳細 SRS)

---

## Summary

設計一個本機執行的 Web 應用，完整實作訓練分析功能：讀取 `~/.wko5coach/fits/{Athlete}/{YEAR}/` 獨立目錄下的 `.fit` 檔案，在 Python FastAPI 後端執行逆向取得的 PKExpressionParser 算法（MMP、FTP、CTL/ATL/TSB、iLevels 等），並在 React 前端提供可自訂 Dashboard。Coros 非官方 API 自動同步 FIT 檔案（詳見 `wko5-coros-sync.spec.md`）。本系統完全獨立於 WKO5 和 TrainingPeaks，兩者可各自運作互不干擾。

---

## 逆向工程發現（架構輸入）

### 資料儲存目錄結構（獨立，不依賴 WKO5）

```
~/.wko5coach/
├── wko5coach.db              # SQLite DB
└── fits/                     # 獨立 FIT 儲存（Coros sync 下載位置）
    └── {AthleteDir}/         # e.g., Athlete
        └── {YEAR}/           # e.g., 2026
            └── {coros_id}_{YYYY-MM-DD}_{sport}.fit
```

**注意**: `~/WKO5/` 目錄繼續由 WKO5 app 獨立管理，本系統不讀寫該目錄。
**現有資料**: 手動匯入的 .fit 已在 DB 中，後續由 Coros sync 自動補充。

### WKO4 Binary Format
- Magic header: `wko4` (4 bytes)
- 後續使用 Protocol Buffer-style VarInt 編碼
- 包含：sport name、ISO 8601 timestamp、sensor channels（二進制壓縮）
- GoldenCheetah 有 `WkoRideFile` 開源解析器可作為參考

### TrainingPeaks API（可選功能，非主要資料源，2026-05-15）

**主要資料源已改為 Coros 非官方 API**（詳見 `wko5-coros-sync.spec.md`）。TP API 保留為 Milestone 6 可選整合。

#### 重要發現（從 PowerKitOSX binary 提取）

#### OAuth 認證流程

**密碼授權（初次登入）** — 完整 body 字串取自 binary 字串常數：
```
POST https://oauth.trainingpeaks.com/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=password&username={u}&password={p}&scope=fitness+baseactivity+users+metrics+software+groundcontrol
```
重要：**password grant 不含 client_id / client_secret**（binary 中 `&client_id=` 只出現在 refresh flow）。
Scope 使用 `+` 作為分隔符（literal，不是 %20-encoded 空格）。

**Token 刷新** — binary 中的完整 format string：
```
grant_type=refresh_token&refresh_token={t}&client_id=WKO5&client_secret=
```
client_secret 為空字串（`=` 後無值）。

**Premium 限制** — binary 字串：`"Download is allowed only from premium and coach accounts."`
只有 premium 或 coach 帳號可以下載 FIT 檔。

#### REST Endpoints（format strings from binary）

```
https://tpapi.trainingpeaks.com/                                           ← Base URL
users/v3/user                                                               ← 取得用戶資料 + athletes
fitness/v1/athletes/{id}/settings                                           ← FTP, weight, LTHR
fitness/v2/athletes/{id}/workouts/changed?date={YYYY-MM-DD}&searchDirection=After&pageSize={n}&page={n}
fitness/v6/athletes/{id}/workouts/{wid}/detaildata                          ← 含 workoutDeviceFileInfos
fitness/v6/athletes/{id}/workouts/{wid}/filedata/{fileName}                 ← FIT 下載（base64+gzip）
metrics/v2/athletes/{id}/timedmetrics/{YYYY-MM-DD}/{YYYY-MM-DD}
software/v1/trial | activate | deactivate | activations/validate | activations
groundcontrol/v1/elevations
```

#### JSON 回應結構（從 binary string literals 確認）

**users/v3/user**：
```json
{ "user": { "userId": int, "userName": str, "userType": str,
            "athletes": [{ "id": int }], "premium": bool,
            "firstName": str, "lastName": str } }
```

**workouts/changed**（workoutDay / startTime / workoutId）：
```json
[{ "workoutId": 12345, "workoutDay": "2026-05-14", "startTime": "..." }]
```

**workouts/{wid}/detaildata**（關鍵欄位：`workoutDeviceFileInfos` 非 `files`）：
```json
{ "workoutId": int, "athleteId": int,
  "workoutDeviceFileInfos": [{ "fileName": "Device.fit", "fileSystemId": int }] }
```

**workouts/{wid}/filedata/{fileName}**（base64-of-gzip，需 inflate）：
```json
{ "data": "<base64-encoded-gzip-compressed-FIT-bytes>" }
```
解碼流程（對應 binary symbol `b64decode` + `Error inflating device file %s`）：
`JSON.data` → base64 decode → zlib/gzip inflate → raw `.fit` bytes

### 完整算法目錄（從 PKExpressionParser nm 提取，~100 個函式）

| 類別 | 函式 |
|------|------|
| **MMP** | meanmax, meanmaxfrom, meanmaxto, meanmaxcurve, meanmaxvalue, meanmaxcurvegaps, meanmaxvaluegaps, optimizedMeanMaxPower |
| **Power Model** | ftp, ftpe, ftpcurve, ftp_rolling, frc, frce, frccurve, frc_rolling, pmax, pmaxe, pmax_rolling, pdcurve, pdcurve2, pdprofile |
| **PMC** | tl (CTL/ATL EMA), tl2, tl3, tau1, tau2, tss, shift, ewma |
| **Training Levels** | levelcount, levelto, levelfrom, levelname, isef |
| **VO₂max** | vo2max, vo2max_rolling |
| **Physiology** | fibertype, fibertype_rolling, phenotype, tte, tte_rolling |
| **Math** | abs, acos, asin, atan, atan2, ceil, clamp, cos, cosh, cot, csc, deg2rad, floor, frac, gaussian, log, log10, power, rad2deg, round, sec, sign, sin, sinh, sqrt, tan, tanh, trunc |
| **Statistical** | avg, count, cumsum, delta, edges, filter, first, greatest, last, least, length, limit, max, merge, min, noinvalid, notequal, nozero, range, resample, rmsd, slr, slrb, slrm, slrrsq, sort, stddev, sum, sumsqr, unique, variance, pvariance, pstddev |
| **Date/Time** | day, dayofweek, formatdate, month, monthval, startofmonth, startofweek, startofyear, week, weekval, year, yearval |
| **Lookup** | athlete, athleterange, lookup, metric, sport, string, symbol, toMetric, english |
| **Control** | assign, cross, iffn, isef, isvalid |

### Training Level Systems
| Class | Method |
|-------|--------|
| PKCogganClassicPowerLevels | 7 zones by % FTP |
| PKCogganOptimizedPowerLevels | iLevels (personalized MMP-based) |
| PKCogganHeartRateLevels | HR zones |
| PKCTSPowerLevels | Carmichael zones |
| PKRSTPowerLevels | RST zones |
| PKPZIPaceLevels | Pace zones (PZI) |
| PKFrielPaceLevels | Friel pace zones |

---

## System Context

### Scope & Boundaries

- **In scope**:
  - FIT file parser (現有 `src/fit_parser.py` 升級)
  - 完整 PKExpressionParser 算法引擎（Python 實作）
  - SQLite 計算快取（取代 JSON，支援 1000+ 筆高效查詢）
  - FastAPI REST API（本機 127.0.0.1:8000）
  - React + TypeScript 前端（可自訂 Dashboard）
  - Coros 非官方 API 同步（詳見 `wko5-coros-sync.spec.md`）
  - 所有 7 種 Training Level 系統
  - 獨立 FIT 儲存 `~/.wko5coach/fits/`

- **Out of scope**:
  - 多用戶帳號系統
  - 雲端部署（本機 / Docker 運行）
  - WKO5 UI 精確複製（設計自由）
  - WKO4 binary parser（不讀 ~/WKO5/）
  - `.wko5athlete` / `.wko5home` 格式解析
  - 訓練計劃（Workout Builder）
  - TrainingPeaks 作為主要資料源（可選 Milestone 6）
  - 公開 API

### Actors

| Actor | Type | Interaction |
|-------|------|-------------|
| 個人運動員 | Human — Browser | 查看 Dashboard、設定 widgets、觸發 Coros 同步 |
| Coros Training Hub API | External Service | 提供 FIT 檔 + 活動 metadata（詳見 coros-sync.spec.md）|
| TrainingPeaks API | External Service（可選）| Milestone 6：補充歷史資料 |

### External Dependencies

| Dependency | Purpose | Failure Mode |
|------------|---------|--------------|
| `teamcnapi.coros.com` | 身份驗證 + FIT 下載 | 降級：手動匯入 .fit |
| Coros S3 presigned URL | FIT 實際下載 | 重試 3 次，記錄失敗 |
| `tpapi.trainingpeaks.com` | 可選歷史同步（Milestone 6）| 功能不可用，不影響核心 |

---

## Architecture

### High-Level Diagram

```
Browser (React + TypeScript)
│  Dashboard Builder — Drag & Drop Widgets
│  Chart components: Recharts / D3
│  State: React Query + Zustand
└── HTTP/REST ──────────────────────────────────────
                                                    │
                         FastAPI 127.0.0.1:8000     │
                         ┌──────────────────────┐   │
                         │  API Layer           │   │
                         │  /api/v1/*           │   │
                         ├──────────────────────┤   │
                         │  Algorithm Engine    │   │
                         │  ExpressionParser    │   │
                         │  + 100 WKO5 fns      │   │
                         ├──────────────────────┤   │
                         │  Computation Cache   │   │
                         │  SQLite (SQLAlchemy)  │   │
                         ├──────────────────────┤   │
                         │  File Service        │   │
                         │  .fit reader         │   │
                         ├──────────────────────┤   │
                         │  Coros Sync Service  │   │
                         │  unofficial API      │   │
                         └──────────┬───────────┘
                                    │
              ~/.wko5coach/fits/{Athlete}/{YEAR}/
              └── *.fit   (Coros sync 下載或手動匯入)
```

### Components

| Component | Responsibility | Interface |
|-----------|---------------|-----------|
| `api/` | FastAPI routes、request validation | HTTP REST `/api/v1/*` |
| `engine/expr_parser.py` | WKO5 expression string 執行引擎 | `evaluate(expr, context) → PKVector` |
| `engine/algorithms/` | 100 個算法模組（按類別分檔） | Python functions, imported by expr_parser |
| `engine/training_levels.py` | 7 種 zone 計算系統 | `calculate_levels(system, ftp, mmp) → Levels` |
| `db/models.py` | SQLAlchemy ORM | SQLite via aiosqlite |
| `db/cache.py` | 計算結果快取讀寫 | `get_cached / invalidate` |
| `files/fit_reader.py` | .fit parser (升級現有) | `parse_fit(path) → RawWorkout` |
| `files/file_service.py` | 目錄掃描、檔案路由 | `scan_directory() → list[WorkoutFile]` |
| `sync/coros_client.py` | Coros 非官方 API 客戶端 | `sync_workouts(since) → AsyncIterator[SyncResult]` |
| `sync/tp_client.py` | TrainingPeaks OAuth2（可選 M6）| `sync_workouts(since) → AsyncIterator[SyncResult]` |
| `frontend/` | React 18 + Vite + TypeScript | SPA on port 5173 (dev) / served by FastAPI (prod) |

### Data Flow

**讀取流程**（Workout Detail）:
```
Browser → GET /api/v1/workouts/{id}/metrics?expr=meanmax(power)
→ api: lookup cache → miss
→ engine: evaluate expr with workout raw data
→ algorithms/mmp.py: compute_mmp()
→ db: save cache entry
→ response: {curve: {1: 850, 5: 620, ...}}
```

**同步流程**（Coros Sync，主要）:
```
Browser → POST /api/v1/sync/coros/start?since=YYYY-MM-DD
→ coros_client: POST /account/login (MD5 password)
→ coros_client: GET /activity/query (paged)
→ for each new activity:
    GET <fitUrl>  (presigned S3 URL)
    save to ~/.wko5coach/fits/{Athlete}/{YEAR}/{id}_{date}_{sport}.fit
    file_service: register new file (source="coros")
    trigger background compute task
→ SSE stream: sync progress updates → Browser
```

### Sequence Diagram — Dashboard Load

```
Browser          API              DB              FileService      Engine
  │                │               │                  │               │
  │ GET /dashboard │               │                  │               │
  │────────────────>               │                  │               │
  │                │ load config   │                  │               │
  │                │──────────────>│                  │               │
  │                │               │ widget list      │               │
  │                │<──────────────│                  │               │
  │                │ for each widget expr:            │               │
  │                │ check cache   │                  │               │
  │                │──────────────>│                  │               │
  │                │  cache miss   │                  │               │
  │                │<──────────────│                  │               │
  │                │               │  scan files     │               │
  │                │───────────────────────────────> │               │
  │                │               │  raw data        │               │
  │                │<─────────────────────────────── │               │
  │                │               │                  │  evaluate     │
  │                │───────────────────────────────────────────────> │
  │                │               │                  │  result       │
  │                │<─────────────────────────────────────────────── │
  │                │  save cache   │                  │               │
  │                │──────────────>│                  │               │
  │  dashboard JSON│               │                  │               │
  │<───────────────│               │                  │               │
```

---

## Data Model

### Entities

| Entity | Owner | Lifecycle |
|--------|-------|-----------|
| `Athlete` | DB | 手動建立 or TP 同步建立 |
| `WorkoutFile` | DB + FileSystem | 匯入/同步時建立 |
| `WorkoutMetrics` | DB (cache) | 計算後快取，檔案刪除時清除 |
| `MmpCache` | DB (cache) | 計算後快取，按 workout_id 索引 |
| `SyncState` | DB | TP 同步狀態（last sync time、token） |
| `DashboardConfig` | DB (JSON column) | 使用者儲存、可多份 |
| `AthleteSettings` | DB | FTP、weight、DOB 等 |

### Schema

```sql
-- SQLite via SQLAlchemy

CREATE TABLE athletes (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    coros_user_id TEXT,        -- Coros userId（登入後取得）
    tp_athlete_id INTEGER,     -- optional，Milestone 6 TP 整合用
    data_dir      TEXT NOT NULL,  -- ~/.wko5coach/fits/Athlete
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE athlete_settings (
    id          INTEGER PRIMARY KEY,
    athlete_id  INTEGER REFERENCES athletes(id),
    effective_date DATE NOT NULL,
    ftp_w       REAL,
    weight_kg   REAL,
    dob         DATE,
    lthr        INTEGER,
    PRIMARY KEY (athlete_id, effective_date)
);

CREATE TABLE workout_files (
    id          INTEGER PRIMARY KEY,
    athlete_id  INTEGER REFERENCES athletes(id),
    file_path   TEXT UNIQUE NOT NULL,  -- absolute path
    file_format TEXT NOT NULL,         -- 'wko4' | 'fit'
    workout_date DATE NOT NULL,
    sport       TEXT,
    duration_s  REAL,
    source      TEXT,                  -- 'local' | 'coros' | 'trainingpeaks'
    coros_activity_id TEXT,            -- Coros labelId，去重用
    tp_workout_id INTEGER,             -- optional，Milestone 6
    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE workout_metrics (
    workout_id  INTEGER REFERENCES workout_files(id),
    metric_key  TEXT NOT NULL,         -- 'avg_power' | 'np' | 'tss' etc
    value       REAL,
    computed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workout_id, metric_key)
);

CREATE TABLE mmp_cache (
    workout_id  INTEGER REFERENCES workout_files(id),
    channel     TEXT NOT NULL,         -- 'power' | 'heartrate' etc
    duration_s  INTEGER NOT NULL,
    value       REAL,
    PRIMARY KEY (workout_id, channel, duration_s)
);

CREATE TABLE pmc_cache (
    athlete_id  INTEGER REFERENCES athletes(id),
    date        DATE NOT NULL,
    ctl         REAL,
    atl         REAL,
    tsb         REAL,
    ramp_rate   REAL,
    tss         REAL,
    PRIMARY KEY (athlete_id, date)
);

CREATE TABLE sync_state (
    athlete_id  INTEGER PRIMARY KEY REFERENCES athletes(id),
    -- Coros sync（主要）
    coros_access_token  TEXT,
    coros_token_expires DATETIME,
    coros_email         TEXT,
    coros_last_sync_at  DATETIME,
    -- TrainingPeaks sync（可選 Milestone 6）
    tp_access_token  TEXT,
    tp_refresh_token TEXT,
    tp_token_expires DATETIME,
    tp_last_sync_at  DATETIME,
    last_sync_cursor TEXT   -- last activity date synced
);

CREATE TABLE dashboard_configs (
    id          INTEGER PRIMARY KEY,
    athlete_id  INTEGER REFERENCES athletes(id),
    name        TEXT NOT NULL,
    is_default  BOOLEAN DEFAULT FALSE,
    layout_json TEXT NOT NULL,   -- JSON: widget positions + expressions
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Dashboard Config JSON Schema

```json
{
  "version": 1,
  "name": "My Dashboard",
  "widgets": [
    {
      "id": "w1",
      "type": "mmp_curve",
      "position": {"col": 0, "row": 0, "w": 6, "h": 4},
      "config": {
        "expr": "meanmax(power)",
        "date_range": "last_90_days",
        "compare_dates": ["2025-01-01", "2024-01-01"]
      }
    },
    {
      "id": "w2",
      "type": "pmc_chart",
      "position": {"col": 6, "row": 0, "w": 6, "h": 4},
      "config": {
        "show_ctl": true,
        "show_atl": true,
        "show_tsb": true,
        "date_range": "ytd"
      }
    },
    {
      "id": "w3",
      "type": "time_series",
      "position": {"col": 0, "row": 4, "w": 12, "h": 3},
      "config": {
        "expr": "power",
        "workout_id": "auto_last"
      }
    }
  ]
}
```

### Migration Strategy
- **Forward**: `alembic upgrade head` — 單次建立所有表
- **Backward**: `alembic downgrade base` — 清除 DB（不影響 .fit 檔案）
- **Backfill**: 首次啟動執行 `scan_and_import` — 掃描 `~/.wko5coach/fits/` 目錄，批次計算所有指標並填入 SQLite
- **Independence**: WKO5 app 繼續讀寫 `~/WKO5/`；本系統完全不接觸該目錄，各自獨立

---

## Algorithm Engine Design

### Expression Evaluator Architecture

```
Expression string: "meanmax(power)"
    ↓
Tokenizer → Parser → AST
    ↓
Evaluator.evaluate(node, context: EvalContext)
    ├── context.workout_id → load channels from DB/file
    ├── context.athlete_id → load athlete settings
    ├── context.date_range → filter records
    └── dispatch to algorithms/{category}.py
```

### EvalContext

```python
@dataclass
class EvalContext:
    athlete_id: int
    workout_id: Optional[int]       # None = multi-workout mode
    date_range: Optional[DateRange]
    channels: dict[str, np.ndarray] # loaded on demand
    ftp_w: Optional[float]          # from athlete_settings
    units: str                       # "metric" | "imperial"
```

### Algorithm Implementation Priority

| Phase | Algorithms | Notes |
|-------|-----------|-------|
| **P1** (已完成) | meanmax, NP, TSS, IF | `src/mmp.py`, `src/metrics.py` |
| **P2** (Web MVP) | ftp/frc/pmax, CTL/ATL/TSB, tl/ewma/shift, levelcount/levelto/levelfrom | PMC + zones |
| **P3** | vo2max, phenotype, fibertype, tte | Physiological metrics |
| **P4** | pdcurve/pdprofile, PD model solve methods | Power Duration model |
| **P5** | Full expression parser (iffn, assign, range, sport, etc.) | Custom chart expressions |

---

## API Contracts

### Endpoints

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| GET | `/api/v1/athletes` | List athletes | none |
| GET | `/api/v1/athletes/{id}/settings` | FTP, weight history | none |
| PUT | `/api/v1/athletes/{id}/settings` | Set FTP/weight | none |
| GET | `/api/v1/workouts` | Paginated list with filters | none |
| GET | `/api/v1/workouts/{id}` | Workout detail + metrics | none |
| GET | `/api/v1/workouts/{id}/channels` | Raw channel data (time series) | none |
| GET | `/api/v1/workouts/{id}/mmp` | MMP curve for this workout | none |
| GET | `/api/v1/workouts/{id}/levels` | Time in zones (iLevels / HR) | none |
| GET | `/api/v1/workouts/{id}/tis` | Training Impact Score (aerobic/anaerobic) | none |
| GET | `/api/v1/workouts/{id}/summary` | Avg/Max per channel + NP/TSS/IF | none |
| POST | `/api/v1/expr/evaluate` | Evaluate WKO5 expression | none |
| GET | `/api/v1/pmc` | CTL/ATL/TSB time series | none |
| GET | `/api/v1/dashboard/{id}` | Load dashboard config | none |
| PUT | `/api/v1/dashboard/{id}` | Save dashboard config | none |
| POST | `/api/v1/sync/coros/start` | Trigger Coros sync（SSE） | none |
| POST | `/api/v1/auth/coros/login` | Coros 登入 | none |
| GET | `/api/v1/auth/coros/status` | Coros 登入狀態 | none |
| POST | `/api/v1/sync/start` | Trigger TP sync（可選 M6） | none |
| GET | `/api/v1/auth/tp/login` | TP OAuth redirect（可選 M6）| none |
| POST | `/api/v1/scan` | Re-scan ~/.wko5coach/fits/ | none |

### Key Response Shapes

```json
// GET /api/v1/workouts?page=1&per_page=20&sport=cycling&date_from=2026-01-01
{
  "total": 1011,
  "page": 1,
  "per_page": 20,
  "items": [
    {
      "id": 42,
      "date": "2026-05-14",
      "sport": "cycling",
      "duration_s": 3612,
      "metrics": {
        "avg_power_w": 210,
        "normalized_power_w": 235,
        "tss": 98.5,
        "intensity_factor": 0.94
      },
      "file_format": "fit",
      "source": "trainingpeaks"
    }
  ]
}

// GET /api/v1/workouts/{id}/mmp
{
  "workout_id": 42,
  "channel": "power",
  "curve": {
    "1": 850, "5": 620, "10": 520, "30": 400,
    "60": 320, "300": 255, "600": 240, "1200": 230,
    "1800": 225, "3600": 218
  },
  "cached": true
}

// POST /api/v1/expr/evaluate
// Request:
{ "expr": "ftp(meanmax(power))", "athlete_id": 1, "date_range": {"days": 90} }
// Response:
{ "result": 248.5, "unit": "W", "computed_at": "2026-05-14T10:00:00" }

// GET /api/v1/pmc?athlete_id=1&from=2025-01-01&to=2026-05-14
{
  "series": [
    { "date": "2026-05-14", "ctl": 72.3, "atl": 68.1, "tsb": 4.2, "tss": 95.0 }
  ]
}

// GET /api/v1/sync/status  (SSE)
event: sync_progress
data: {"status": "downloading", "current": 5, "total": 23, "workout_date": "2026-05-10"}

event: sync_complete
data: {"new_workouts": 23, "errors": 0, "duration_s": 12.4}
```

### Error Codes

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `WORKOUT_NOT_FOUND` | 404 | workout_id 不存在 |
| `EXPR_PARSE_ERROR` | 400 | expression 語法錯誤 |
| `EXPR_EVAL_ERROR` | 422 | expression 執行失敗（型別不符等） |
| `TP_AUTH_REQUIRED` | 401 | TrainingPeaks 未授權，需重新登入 |
| `TP_API_ERROR` | 502 | TP API 回應錯誤 |
| `FILE_PARSE_ERROR` | 422 | .wko4/.fit 檔案損壞 |
| `NO_POWER_DATA` | 204 | 該訓練無功率資料 |

---

## Non-Functional Requirements

| Category | Target | Measurement | How Achieved |
|----------|--------|-------------|--------------|
| **算法精確度** | MMP ≤ ±1% vs WKO5 | 1011 份歷史資料對比 | numpy vectorized（Milestone 1 已驗證） |
| **Dashboard 載入** | p95 < 500ms | Browser DevTools | SQLite 快取 + 索引；避免 expression re-evaluate |
| **批次掃描** | 1011 workouts < 5min | `time python scan.py` | asyncio 並行，max_workers=4 |
| **TP 同步速度** | 100 workouts < 30s | sync log | async download + streaming progress |
| **本機安全** | TP token 不洩漏 | code review | token 僅存 SQLite，不出現在 JS bundle 或 URL |
| **WKO5 共存** | 不修改 .wko4 | filesystem audit | 只讀 .wko4；.fit 寫入同目錄無衝突 |

---

## Technology Choices

| Concern | Choice | Alternatives | Rationale |
|---------|--------|--------------|-----------|
| Backend framework | FastAPI (Python 3.12) | Flask, Django | async support; 型別系統 + pydantic; 現有 src/ 直接用 |
| Database | SQLite + SQLAlchemy + Alembic | JSON files, PostgreSQL | 1011+ 筆需 indexed query; 單用戶不需 server |
| Async driver | aiosqlite | sqlite3 (sync) | FastAPI async routes; 不阻塞 event loop |
| Frontend | React 18 + TypeScript + Vite | Vue, Svelte | 最多生態; recharts/D3 圖表庫齊全 |
| Charts | Recharts + D3 (complex) | Highcharts, Chart.js | Recharts for standard; D3 for MMP scatter |
| State management | React Query + Zustand | Redux | Query: server state cache; Zustand: UI state |
| Dashboard layout | react-grid-layout | CSS Grid 手刻 | Drag-and-drop resize 已有 production 實作 |
| .wko4 parsing | Python port of GoldenCheetah WkoRideFile.cpp | Reverse engineering from scratch | GoldenCheetah MIT license; saves weeks |
| HTTP client (TP) | httpx (async) | requests | FastAPI 生態; async-native |
| Expression parser | 手刻 recursive descent parser | pyparsing, lark | PKExpressionParser 精確語義；外部庫不匹配 |

---

## Integration Points

### TrainingPeaks OAuth2 Flow（已實作 — ROPC + 自動刷新）

WKO5 使用 **ROPC (Resource Owner Password Credentials)** flow，不做 redirect：

```
User → POST /api/v1/auth/tp/login { username, password, athlete_id }
     → backend: POST https://oauth.trainingpeaks.com/oauth/token
           body: grant_type=password&username={}&password={}
                 &scope=fitness+baseactivity+users+metrics+software+groundcontrol
           (no client_id — matches WKO5 binary exactly)
     → GET https://tpapi.trainingpeaks.com/users/v3/user (Bearer token)
     → extract user.athletes[0].id → store as tp_athlete_id
     → persist {access_token, refresh_token, expires_at} to sync_state (SQLite)
     → return { authenticated, tp_athlete_id, athletes, user_type, premium, can_download }
```

Token 刷新（每次 API 呼叫前自動執行，提前 5 分鐘刷新）：
```
POST https://oauth.trainingpeaks.com/oauth/token
body: grant_type=refresh_token&refresh_token={t}&client_id=WKO5&client_secret=
```

**Premium 限制**：download 需要 premium 或 coach 帳號，`can_download` 欄位表示。

### 獨立儲存（與 WKO5 完全分離）

- 本系統：`~/.wko5coach/fits/{Name}/{YEAR}/{coros_id}_{YYYY-MM-DD}_{sport}.fit`
- WKO5：`~/WKO5/{Name}/{YEAR}/...`（完全獨立，本系統不接觸）
- 兩個工具各自獨立管理自己的資料目錄，不共享檔案

---

## Codebase Patterns to Follow

| Pattern | Where to Find | Why Follow |
|---------|---------------|-----------|
| MMP sliding window | `src/mmp.py:69-100` | Milestone 1 已驗證 ±1% |
| NP calculation | `src/metrics.py:16-45` | 30s rolling^4，已測試 |
| FIT parser + resample | `src/fit_parser.py:130-185` | 1s uniform grid，Coros 相容 |
| JSON storage pattern | `src/storage.py` | 升級為 SQLite 時的介面參考 |
| CLI pipeline | `src/importer.py` | background compute job 參考 |

---

## Risks & Trade-offs

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `.wko4` 解析不完整（壓縮 channels 格式不公開） | H | H | 優先策略：重新從 TP 下載 FIT 檔；.wko4 僅作 metadata 來源 |
| TrainingPeaks API 未公開，隨時可能改變 | M | H | 版本固定 v2/v6；抽象 TP client 層，容易替換 |
| 1011 份歷史資料初次批次計算時間過長 | M | M | 進度條 SSE + background task；使用者可繼續使用已算完的部分 |
| Expression parser 語義與 WKO5 不一致（edge cases） | M | M | 逐一用 WKO5 驗證結果；先 MMP/FTP/TSS，再逐步擴充 |
| React Grid Layout 複雜 widget 拖放在 Safari 問題 | L | L | fallback: 固定 layout template |

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| DB 從 JSON 升級至 SQLite | SQLite | 繼續用 JSON files | 1011 workouts 需要 date-range queries、CTL 時間序列；JSON 需全讀 |
| .wko4 策略 | GoldenCheetah 參考 + TP FIT 優先 | 完整逆向 .wko4 | 節省時間；TP FIT 品質更好（含 GPS、sensor data）|
| Frontend SPA | React + Vite | 伺服器端渲染 | 本機工具；無 SEO 需求；dashboard 互動性高 |
| 算法引擎 | Python 手刻 | 直接呼叫 PowerKitOSX.framework | 可測試、可移植、無授權問題 |
| OAuth 在 backend | FastAPI server-side | Frontend PKCE | TP token 不暴露在 browser；本機 localhost callback 可行 |
| Dashboard 配置 | JSON column in SQLite | 獨立 config 檔案 | 與 workout 資料同庫，方便 backup |

---

## Project Structure (Target)

```
WKO5reverse/
├── backend/
│   ├── main.py                    # FastAPI app entry
│   ├── api/
│   │   ├── workouts.py
│   │   ├── pmc.py
│   │   ├── dashboard.py
│   │   ├── sync.py
│   │   └── auth.py
│   ├── engine/
│   │   ├── expr_parser.py         # recursive descent parser
│   │   ├── algorithms/
│   │   │   ├── mmp.py             # ports from src/mmp.py
│   │   │   ├── metrics.py         # ports from src/metrics.py
│   │   │   ├── pmc.py             # CTL/ATL/TSB
│   │   │   ├── power_model.py     # FTP/FRC/Pmax/PD model
│   │   │   ├── training_levels.py # 7 zone systems
│   │   │   ├── vo2max.py
│   │   │   └── math_stats.py      # abs/sin/cos/avg/sum etc
│   │   └── eval_context.py
│   ├── db/
│   │   ├── models.py              # SQLAlchemy models
│   │   ├── database.py            # engine + session
│   │   └── migrations/            # Alembic
│   ├── files/
│   │   ├── file_service.py        # directory scanner
│   │   ├── fit_reader.py          # ports src/fit_parser.py
│   │   └── wko4_reader.py         # GoldenCheetah-referenced
│   └── sync/
│       └── tp_client.py           # TP OAuth + download
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── widgets/           # MmpCurveWidget, PmcWidget, etc
│   │   │   └── DashboardGrid.tsx
│   │   ├── pages/
│   │   │   ├── Dashboard.tsx
│   │   │   ├── WorkoutList.tsx
│   │   │   └── WorkoutDetail.tsx
│   │   ├── api/                   # React Query hooks
│   │   └── store/                 # Zustand
│   └── vite.config.ts
├── src/                           # Milestone 1 (保留，被 backend/ 引用)
├── docs/
│   ├── prd/
│   └── spec/
└── requirements.txt
```

---

## Open Questions

- [ ] `.wko4` 中是否包含完整 1-second power channel，還是只有 lap/summary data？（影響是否可以不下載 FIT）
- [x] ~~TrainingPeaks `fitness/v6` 的 FIT filedata endpoint 需要特殊 scope 嗎？~~ **已解決**：scope=`fitness+baseactivity+users+metrics+software+groundcontrol`（從 binary 逆向確認）；filedata 回應為 `{"data": "<base64-gzip>"}` 格式
- [x] ~~OAuth 的 client_id 和 grant_type 細節？~~ **已解決**：password grant **不含** client_id；refresh grant 用 `client_id=WKO5&client_secret=`
- [x] ~~filedata endpoint 回傳原始 bytes 還是編碼格式？~~ **已解決**：JSON `{"data": base64(gzip(fit))}` — 需 base64 decode 再 zlib inflate
- [x] ~~users/v3/user 回應結構？~~ **已解決**：`{"user": {"userId", "userType", "athletes":[{"id"}], "premium"}}`
- [x] ~~`workoutDeviceFileInfos` vs `files`？~~ **已解決**：binary 用 `workoutDeviceFileInfos`（含 `fileName` + `fileSystemId`）
- [ ] iLevels（PKCogganOptimizedPowerLevels）的 Dmax 計算是否在 GoldenCheetah 有開源實作？
- [ ] WKO5 的 `sport()` expression function 如何解析多運動類型 athlete（cycling+running）？
- [ ] `react-grid-layout` 的 breakpoint 在小螢幕（13" MacBook）是否需要調整 col count？
- [ ] TP premium 帳號驗證：若帳號不是 premium/coach，`can_download: false`，前端應顯示升級提示

## TP Sync 已知行為（從逆向 + 實作確認，2026-05-15）

| 項目 | 確認值 |
|------|--------|
| OAuth URL | `https://oauth.trainingpeaks.com/oauth/token` |
| API Base | `https://tpapi.trainingpeaks.com/` |
| Password grant client_id | **無**（password grant 不含此欄位）|
| Refresh grant client_id | `WKO5` |
| Refresh grant client_secret | `""` (空字串) |
| Scope（literal）| `fitness+baseactivity+users+metrics+software+groundcontrol` |
| User endpoint | `users/v3/user` |
| Athletes list | `user.athletes[*].id` |
| Workouts list | `fitness/v2/.../workouts/changed?...&searchDirection=After&pageSize=20` |
| Detail JSON key | `workoutDeviceFileInfos[*].fileName`（非 `files`）|
| File download format | JSON `{"data": base64(gzip(fit_bytes))}` |
| HTTP library | libcurl + libz（macOS system）|
| Premium gate | Binary 字串：`"Download is allowed only from premium and coach accounts."` |

---

## Activity Detail 頁面規格

> **Source**: `~/WKO5/Views/Workout/WKO5 Workout View.wko5chart`（逆向分析取得，2026-05-15）
> **位置**: URL `/workouts/{id}` — 與 Workout List 頁面獨立的頁面區塊

### 頁面佈局

```
┌────────────────────────────────────────────────────────┐
│  [← 返回]  2026-05-14  Cycling  3:00:12  210W NP 235W │
│  TSS 98.5  IF 0.94  Distance 80.2km                    │
├──────────────────────────┬─────────────────────────────┤
│  Power Time Series       │  Workout MMP Curve          │
│  (raw + 30s smoothed)    │  (今日 vs 90日最佳)          │
├──────────────────────────┼─────────────────────────────┤
│  Heart Rate Time Series  │  Time in iLevels            │
├──────────────────────────┼─────────────────────────────┤
│  Cadence Time Series     │  Time in HR Zones           │
├──────────────────────────┼─────────────────────────────┤
│  Speed / Pace            │  Aerobic TIS / Anaerobic TIS│
├──────────────────────────┴─────────────────────────────┤
│  Elevation over Distance (gradient color-coded)        │
└────────────────────────────────────────────────────────┘
```

### Chart 規格（對應 WKO5 Workout View.wko5chart）

#### 必要圖表（Must）

| Chart | WKO5 表達式 | 說明 |
|-------|------------|------|
| Power 時間序列 | `power` | 原始功率（1s）+ 30s smoothed 疊加 |
| Workout MMP | `meanmax(power)` | 當次 MMP vs 90 日最佳 MMP |
| Summary Stats | `avg(power)`, `max(power)`, NP, TSS, IF | 頁面頂端 header bar |

#### 應有圖表（Should）

| Chart | WKO5 表達式 | 說明 |
|-------|------------|------|
| Heart Rate 時間序列 | `heartrate` | 原始 HR |
| Avg HR vs Max HR | `{avg(heartrate),max(heartrate)}` | Summary 統計 |
| Cadence 時間序列 | `cadence` | 原始踏頻 |
| Avg Cadence vs Max | `{avg(nozero(cadence)),max(cadence)}` | 排除零值平均 |
| Time in iLevels | `levelcount(...)` per level | 每個 iLevel 的時間分佈（bar chart） |
| Time in HR Zones | `levelcount(...)` per HR zone | HR Zone 時間分佈 |
| Elevation over Distance | `elevation` vs `distance` | 坡度 gradient 上色（0–16% = 不同顏色） |
| Aerobic TIS | `Aerobic Training Impact Score` | 有氧訓練影響分數 |
| Anaerobic TIS | `Anaerobic Training Impact Score` | 無氧訓練影響分數 |

#### 可選圖表（Could，sport=run 時顯示）

| Chart | WKO5 表達式 | 說明 |
|-------|------------|------|
| Running Dynamics | `groundcontacttime`, `verticaloscillation`, `striderating` | 跑步動態（需 Coros Stryd 或 Garmin Running Dynamics）|
| Running Effectiveness | RE Form/Hill/Wind Effect | 跑步效率分析（需額外感測器）|
| Hilly Run Summary | Palladino algorithm | 爬坡/下坡功率分佈 |

### API Endpoints（Activity Detail 專用）

```
GET /api/v1/workouts/{id}/channels
→ { "power": [280,285,...], "heartrate": [142,...], "cadence": [90,...],
    "speed": [8.2,...], "elevation": [120,...], "distance": [0,8,...],
    "timestamps": [...], "duration_s": 10812 }

GET /api/v1/workouts/{id}/mmp
→ { "current": {1:850, 5:620, 30:485, 300:360, 1800:280, 3600:255},
    "best_90d": {1:920, 5:680, ...} }

GET /api/v1/workouts/{id}/summary
→ { "avg_power": 210, "max_power": 920, "np": 235, "tss": 98.5, "if": 0.94,
    "avg_hr": 155, "max_hr": 182, "avg_cadence": 88, "distance_km": 80.2,
    "duration_s": 10812, "elevation_gain_m": 650 }

GET /api/v1/workouts/{id}/levels
→ { "ilevels": [{"level":1,"name":"Recovery","seconds":420,"pct":3.9}, ...],
    "hr_zones": [{"zone":1,"name":"Z1","seconds":600,"pct":5.6}, ...] }

GET /api/v1/workouts/{id}/tis
→ { "aerobic_tis": 3.2, "anaerobic_tis": 1.8,
    "aerobic_label": "Aerobic", "anaerobic_label": "Anaerobic TIS" }
```

### 前端技術規格

| 元件 | Library | 說明 |
|------|---------|------|
| 時間序列圖 | Recharts `<ComposedChart>` | 支援多 series 疊加、brush 縮放 |
| MMP Curve | Recharts `<LineChart>` | log-scale X 軸（1s–3600s）|
| Time in Zones | Recharts `<BarChart>` horizontal | 每個 zone 的時間 + % |
| Elevation over Distance | Recharts area chart + gradient fill | 依坡度上色 |
| TIS | 簡單數字 + badge | 不需圖表 |

### 表達式實作優先順序（對應 Algorithm Engine P2）

以下表達式需在後端 `engine/algorithms/` 實作後，Activity Detail 才能完整運作：

| 表達式 | Phase | 用途 |
|--------|-------|------|
| `meanmax(power)` | P1 ✅ | Workout MMP |
| `avg(power)`, `max(power)` | P1 ✅ | Summary stats |
| `nozero(cadence)` | P1 ✅ | 踏頻排零值 |
| `levelcount(...)` | P2 | Time in Zones |
| `tss(power,ftp)` | P2 | Training Stress Score |
| `isef(...)` | P2 | 指數平滑（smoothed power） |
| Aerobic/Anaerobic TIS | P3 | 訓練影響分數（需 ewma + zone logic） |

