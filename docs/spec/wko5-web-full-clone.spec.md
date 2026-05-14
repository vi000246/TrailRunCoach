# SRS: WKO5 Web Clone — Full Algorithm Engine + Custom Dashboard + TrainingPeaks Sync

## Metadata
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md` (partial context)
- **Source Linear Issue**: N/A — standalone
- **Owner**: vi000246
- **Status**: DRAFT
- **Generated**: 2026-05-14
- **Supersedes**: `docs/spec/wko5-milestone1-fit-mmp.spec.md` (Milestone 1 becomes a sub-component)

---

## Summary

設計一個本機執行的 Web 應用，完整複製 WKO5 的訓練分析功能：讀取 `~/WKO5/{Athlete}/{YEAR}/` 目錄下的 `.wko4` 和 `.fit` 檔案，在 Python FastAPI 後端執行所有逆向取得的 PKExpressionParser 算法（MMP、FTP、CTL/ATL/TSB、iLevels 等），並在 React 前端提供可自訂 Dashboard。TrainingPeaks OAuth2 整合自動同步 FIT 檔案到現有 WKO5 目錄。

---

## 逆向工程發現（架構輸入）

### WKO5 目錄結構
```
~/WKO5/
├── WKO4.wko5home                        # home database
├── {AthleteDir}/                        # e.g., Athlete
│   ├── {Name}.wko5athlete               # athlete profile (binary)
│   ├── {Name}.wko5athlete.1             # backup
│   ├── {YEAR}/                          # e.g., 2022
│   │   └── {Name}_{YYYY}_{MM}_{DD}_{HH}_{MM}.wko4   # workout (binary)
│   └── Cache5/                          # WKO5 computed cache
├── Chart History/Charts.wko5cache
├── Smart Segments/Smart Segments.wko5cache
└── Views/Charts.wko5cache
```

**現有資料**: Athlete 運動員，1,011 份 `.wko4` 檔案（2020–2026）

### WKO4 Binary Format
- Magic header: `wko4` (4 bytes)
- 後續使用 Protocol Buffer-style VarInt 編碼
- 包含：sport name、ISO 8601 timestamp、sensor channels（二進制壓縮）
- GoldenCheetah 有 `WkoRideFile` 開源解析器可作為參考

### TrainingPeaks API（從 PowerKitOSX strings 提取）
- OAuth token: `https://oauth.trainingpeaks.com/oauth/token`
- Base: `https://tpapi.trainingpeaks.com/`
- `GET fitness/v1/athletes/{id}/settings`
- `GET fitness/v2/athletes/{id}/workouts/changed?date={date}&searchDirection=After&pageSize={n}&page={n}`
- `GET fitness/v6/athletes/{id}/workouts/{id}/detaildata`
- `GET fitness/v6/athletes/{id}/workouts/{id}/filedata/{filename}` ← FIT 檔下載
- `GET metrics/v2/athletes/{id}/timedmetrics/{from}/{to}`

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
  - WKO4 binary parser (GoldenCheetah 參考實作)
  - FIT file parser (現有 `src/fit_parser.py` 升級)
  - 完整 PKExpressionParser 算法引擎（Python 實作）
  - SQLite 計算快取（取代 JSON，支援 1011+ 筆高效查詢）
  - FastAPI REST API（本機 127.0.0.1:8000）
  - React + TypeScript 前端（可自訂 Dashboard）
  - TrainingPeaks OAuth2 + FIT 檔自動同步
  - 所有 7 種 Training Level 系統

- **Out of scope**:
  - 多用戶帳號系統
  - 雲端部署（本機運行）
  - WKO5 UI 精確複製（設計自由）
  - `.wko5athlete` / `.wko5home` 格式解析（使用 TP API 替代運動員設定）
  - 訓練計劃（Workout Builder）
  - Garmin Connect / Coros 直接整合（透過 TP 取得）
  - 公開 API

### Actors

| Actor | Type | Interaction |
|-------|------|-------------|
| 個人運動員 | Human — Browser | 查看 Dashboard、設定 widgets、觸發 TP 同步 |
| TrainingPeaks API | External Service | 提供 FIT 檔 + 運動員 metadata |
| WKO5 App | Coexisting App | 同一目錄讀寫 .wko4（只讀共存，不修改） |

### External Dependencies

| Dependency | Purpose | Failure Mode |
|------------|---------|--------------|
| TrainingPeaks OAuth | 身份驗證 + FIT 下載 | 降級：手動匯入 .fit |
| `tpapi.trainingpeaks.com` | 工作資料同步 | 降級：讀本地快取 |
| GoldenCheetah WkoRideFile | .wko4 解析參考 | fallback：僅讀 .fit |

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
                         │  .wko4 + .fit reader │   │
                         ├──────────────────────┤   │
                         │  TP Sync Service     │   │
                         │  OAuth2 + download   │   │
                         └──────────┬───────────┘
                                    │
              ~/WKO5/{Athlete}/{YEAR}/
              ├── *.wko4  (WKO5 files — read-only)
              └── *.fit   (新下載 — 我們寫入)
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
| `files/wko4_reader.py` | .wko4 binary parser | `parse_wko4(path) → RawWorkout` |
| `files/fit_reader.py` | .fit parser (升級現有) | `parse_fit(path) → RawWorkout` |
| `files/file_service.py` | 目錄掃描、檔案路由 | `scan_directory() → list[WorkoutFile]` |
| `sync/tp_client.py` | TrainingPeaks OAuth2 + REST | `sync_workouts(since) → list[SyncResult]` |
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

**同步流程**（TP Sync）:
```
Browser → POST /api/v1/sync/trainingpeaks
→ tp_client: GET fitness/v2/athletes/{id}/workouts/changed
→ for each new workout:
    GET fitness/v6/athletes/{id}/workouts/{wid}/filedata/{fn}
    save to ~/WKO5/{Athlete}/{YEAR}/{Name}_{datetime}.fit
    file_service: register new file
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
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    tp_athlete_id INTEGER,
    data_dir    TEXT NOT NULL,  -- ~/WKO5/Athlete
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
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
    source      TEXT,                  -- 'local' | 'trainingpeaks'
    tp_workout_id INTEGER,
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
    tp_access_token  TEXT,
    tp_refresh_token TEXT,
    tp_token_expires DATETIME,
    last_sync_at     DATETIME,
    last_sync_cursor TEXT   -- last workout date synced
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
- **Backward**: `alembic downgrade base` — 清除 DB（不影響 .wko4/.fit 檔案）
- **Backfill**: 首次啟動執行 `scan_and_import` — 掃描 ~/WKO5 目錄，批次計算所有指標並填入 SQLite
- **Coexistence**: WKO5 app 繼續讀寫 .wko4；本系統只讀 .wko4，只寫 .fit

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
| GET | `/api/v1/workouts/{id}/channels` | Raw channel data | none |
| GET | `/api/v1/workouts/{id}/mmp` | MMP curve | none |
| GET | `/api/v1/workouts/{id}/levels` | Training zones | none |
| POST | `/api/v1/expr/evaluate` | Evaluate WKO5 expression | none |
| GET | `/api/v1/pmc` | CTL/ATL/TSB time series | none |
| GET | `/api/v1/dashboard/{id}` | Load dashboard config | none |
| PUT | `/api/v1/dashboard/{id}` | Save dashboard config | none |
| POST | `/api/v1/sync/start` | Trigger TP sync | none |
| GET | `/api/v1/sync/status` | SSE stream of sync progress | none |
| GET | `/api/v1/auth/tp/login` | Redirect to TP OAuth | none |
| GET | `/api/v1/auth/tp/callback` | OAuth callback | none |
| POST | `/api/v1/scan` | Re-scan ~/WKO5 directory | none |

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

### TrainingPeaks OAuth2 Flow

```
User → GET /api/v1/auth/tp/login
     → redirect to https://oauth.trainingpeaks.com/oauth/authorize
           ?client_id={}&response_type=code&scope=ATHLETE_READ WORKOUT_READ
     → TP login page
     → redirect to /api/v1/auth/tp/callback?code={}
     → POST https://oauth.trainingpeaks.com/oauth/token
           {grant_type: authorization_code, code: {}, redirect_uri: {}}
     → store {access_token, refresh_token, expires_in} in sync_state table
     → redirect to frontend /?sync=authenticated
```

Token 自動刷新：每次 TP API 呼叫前檢查 `tp_token_expires`，過期則使用 `refresh_token` 取新 token。

### WKO5 File Coexistence

- 本系統：`~/WKO5/{Name}/{YEAR}/{Name}_{YYYY}_{MM}_{DD}_{HH}_{MM}.fit`（TP 下載）
- WKO5：`~/WKO5/{Name}/{YEAR}/{Name}_{YYYY}_{MM}_{DD}_{HH}_{MM}.wko4`（既有）
- 命名規則相容，同目錄無衝突；WKO5 不識別 .fit 副檔名，會忽略

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
- [ ] TrainingPeaks `fitness/v6` 的 FIT filedata endpoint 需要特殊 scope 嗎？（需實際測試 OAuth）
- [ ] iLevels（PKCogganOptimizedPowerLevels）的 Dmax 計算是否在 GoldenCheetah 有開源實作？
- [ ] WKO5 的 `sport()` expression function 如何解析多運動類型 athlete（cycling+running）？
- [ ] `react-grid-layout` 的 breakpoint 在小螢幕（13" MacBook）是否需要調整 col count？
