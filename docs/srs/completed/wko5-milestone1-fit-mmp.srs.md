# SRS: WKO5 Reverse — Milestone 1: FIT 解析 + MMP 計算引擎

> ⛔ **CANCELED（2026-10-04）**：5 月版的設計已作廢——React SPA 與 5 月的舊程式移除，相關功能之後另做新版。本文件只留作歷史紀錄；現行實作以 `docs/spec/` 為準。

## Metadata
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md`
- **Source Linear Issue**: N/A — standalone
- **Owner**: maintainer
- **Status**: CANCELED (2026-10-04)
- **Generated**: 2026-05-14
- **Scope**: Milestone 1 only — FIT parsing + MMP curve + JSON storage; no AI, no FTP/iLevels

---

## Summary

從 WKO5 `PowerKitOSX.framework` 逆向提取的符號與公式字串，確認核心訓練指標算法（MMP、NP、TSS）可在 Python 中完整重現，無需動態載入閉源 framework。本規格定義一個本機 CLI 工具鏈：FIT 檔 → 解析 → 指標計算 → JSON 儲存，以 ±1% 精度對齊 WKO5 輸出為驗收標準。

---

## 逆向工程發現摘要

> 本節記錄靜態分析從 PowerKitOSX.framework (Build 590, arm64+x86_64) 提取的關鍵資訊。

### 已確認的算法公式（從 `strings` 提取）

| 指標 | WKO5 內部公式字串 | 說明 |
|------|-----------------|------|
| **Normalized Power (NP)** | `normalizedpower` / `npower` channel | 30s 滾動平均^4 取平均再開4次方根 |
| **_rapower4** | `_rapower4` | 內部 channel = `(30s_rolling_avg_power)^4` |
| **MMP 曲線** | `meanmax(power)` | 滑動窗口最大平均功率 |
| **MMP with gaps** | `meanmaxgaps` | 含間歇段的 MMP |
| **FTP 估算** | `ftp(meanmax(power))` | MMP 曲線 60 分鐘值 |
| **FRC** | `frc(meanmax(power))*1000.0` | Functional Reserve Capacity (J) |
| **TSS** | `tsspower` channel | `(dur_s × NP × IF) / (FTP × 3600) × 100` |
| **CTL** | `tl(tss, ctlconstant)` | 指數平滑，預設 42 天 |
| **ATL** | `tl(tss, atlconstant)` | 指數平滑，預設 7 天 |
| **TSB** | `shift(tl(tss,ctlconstant)-tl(tss,atlconstant), 1)` | 昨日 CTL − ATL |
| **Ramp Rate** | `(tl(tss,ctlconstant)-shift(tl(tss,ctlconstant),rampconstant))/(rampconstant/7)` | CTL 週增幅 |
| **VO2max score** | `clamp(1+(s(meanmax(_rapower4)^.25)*(1+ln(3600/Dmax(meanmax(_rapower4)^0.25))))/ftp(meanmax(_rapower4)^0.25),0,100)` | 有氧適能指數 |

### 關鍵 C++ 符號（從 `nm` 提取）

| 函式 | 用途 |
|------|------|
| `searchMeanMaximalTimeOrDistancePK8PKVectorS1_S1_bR16PKMeanMaxEntriesP8PKThread` | 核心 MMP 滑動窗口搜尋 |
| `PKPowerDuration::solveMethodKevin1/2` | PD 模型擬合（WKO5 私有） |
| `PKPowerDuration::solveMethodGreg` | PD 模型擬合（經典） |
| `PKPowerDuration::solveMethodLSF` | Least Squares Fit |
| `PKCogganClassicPowerLevels::calculateLevels` | 7 區間功率區間（% FTP） |
| `PKCogganOptimizedPowerLevels::calculateLevels` | iLevels（MMP 曲線個人化，Milestone 3） |
| `triangleThreshold` | 閾值偵測（可能用於 FTP 估算） |

### 資料結構映射

| WKO5 C++ 類別 | Python 等效 | 說明 |
|--------------|------------|------|
| `PKVector` | `np.ndarray` | 時間索引的 double 陣列 |
| `PKDataFrame` | `dict[str, np.ndarray]` | Named channels（power, cadence, hr...） |
| `PKMeanMaxEntries` | `dict[int, float]` | `{duration_s: max_avg_power_w}` |
| `PKFlatTrack` | 暫不實作 | GPS 軌跡，Milestone 1 不需要 |
| `PKAthleteDocument` | `athlete.json` | 運動員設定（FTP, weight） |
| `PKWorkoutDocument` | `workout_{date}.json` | 單次訓練完整資料 |

---

## System Context

### Scope & Boundaries

- **In scope (Milestone 1)**:
  - FIT 檔案解析（Coros / Garmin，含 .fit）
  - 原始 channel 提取：power (W), time (s), heart_rate (bpm), cadence (rpm), distance (m), altitude (m)
  - MMP 曲線計算（1s 到 3600s，含 gaps 模式）
  - Normalized Power (NP) 計算
  - TSS 計算（需手動設定 FTP）
  - JSON 儲存（單次訓練 + 匯總索引）
  - CLI 驗證工具（對比 WKO5 輸出）

- **Out of scope (Milestone 1)**:
  - FTP 自動估算（`ftp(meanmax(power))`，Milestone 3）
  - iLevels / PKCogganOptimizedPowerLevels（Milestone 3）
  - CTL/ATL/TSB（需多筆訓練，Milestone 2+）
  - Coros 自動同步（Milestone 4）
  - AI 對話介面（Milestone 2）
  - GPS/地圖分析

### Actors

| Actor | Type | Interaction |
|-------|------|-------------|
| 個人運動員 | Human — CLI | 手動放入 .fit 檔，執行 import 指令，查看輸出 |

### External Dependencies

| Dependency | Purpose | Failure Mode |
|------------|---------|--------------|
| `fitparse` (python-fitparse) | FIT 二進位解析 | pip 安裝失敗 → 用 `fit_tool` 替代 |
| `numpy` | 向量運算（滑動窗口） | 基礎依賴，不預期失敗 |
| WKO5 (已安裝) | 驗證比對基準 | 不影響生產執行，只用於測試 |

---

## Architecture

### High-Level Diagram

```
.fit file
    │
    ▼
┌─────────────────┐
│  fit_parser.py  │  fitparse → raw channel dict
└────────┬────────┘
         │ raw_data: dict[channel, list[float]]
         ▼
┌─────────────────┐
│  metrics.py     │  NP, TSS, duration, avg_power
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  mmp.py         │  MMP curve: dict[duration_s, max_avg_power]
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  storage.py     │  write workouts/YYYY-MM-DD_*.json
│                 │  update index.json
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  cli.py         │  import / show / validate commands
└─────────────────┘
```

### Components

| Component | Responsibility | Interface |
|-----------|---------------|-----------|
| `fit_parser.py` | 讀取 .fit，輸出 normalized channel dict | `parse_fit(path) -> RawWorkout` |
| `mmp.py` | 滑動窗口 MMP 計算，支援 gaps | `compute_mmp(power: np.ndarray, time: np.ndarray) -> MmpCurve` |
| `metrics.py` | NP, TSS, IF, duration, avg_power | `compute_metrics(raw: RawWorkout, ftp: float) -> WorkoutMetrics` |
| `storage.py` | JSON 讀寫，index 維護 | `save_workout(w: Workout) -> Path` / `load_all() -> list[Workout]` |
| `cli.py` | 使用者入口 | `import <file.fit>` / `show <date>` / `validate <file.fit>` |

### Data Flow

同步、批次。使用者執行 `wko import ride.fit`：
1. `fit_parser` 讀取 binary .fit → `RawWorkout`（channel 字典）
2. `metrics` 計算 NP、TSS、duration → `WorkoutMetrics`
3. `mmp` 用 numpy 滑動窗口計算完整 MMP 曲線 → `MmpCurve`
4. `storage` 組合成 `Workout` → 寫入 `workouts/2026-05-14_ride.json` + 更新 `index.json`

---

## Algorithm Specifications

### A. MMP 曲線計算

**依據**: `searchMeanMaximalTimeOrDistancePK8PKVectorS1_S1_bR16PKMeanMaxEntriesP8PKThread`，GoldenCheetah 開源實作交叉驗證。

**輸入**: `power[t]`（每秒採樣，W）、`time[t]`（秒）
**輸出**: `{duration_s: max_avg_power_w}` — durations: `[1,2,...,10,12,...,60,90,...,3600]`

**算法**:
```
for each target_duration d in DURATIONS:
    best = 0
    for each start index i:
        window_end = first j where time[j] - time[i] >= d
        if window_end exists:
            avg = mean(power[i:window_end])
            best = max(best, avg)
    mmp[d] = best
```

**Gaps 處理**: 靜止段（power == 0 超過 threshold）不計入窗口時間（對應 `meanmaxgaps`）

**性能要求**: 1 小時訓練（3600 個數據點）在 < 2 秒內完成（numpy vectorized）

**驗收標準**: 5 份測試 .fit 檔，與 WKO5 MMP 數值誤差 ≤ ±1%

### B. Normalized Power (NP)

**依據**: WKO5 `normalizedpower` / `_rapower4` channel 命名與 Coggan 公式

**算法**:
```
1. 30s 滾動平均: rolling_avg = rolling_mean(power, window=30)
2. 4次方: pow4 = rolling_avg ** 4
3. 平均: mean_pow4 = mean(pow4[30:])  # 跳過前 30s
4. NP = mean_pow4 ** 0.25
```

### C. TSS 計算

**依據**: `tsspower` 符號，Coggan/Allen 公式

```
IF = NP / FTP
TSS = (duration_s × NP × IF) / (FTP × 3600) × 100
    = (duration_s × NP²) / (FTP² × 3600) × 100
```

**前提**: FTP 需手動設定於 `athlete.json`

---

## Data Model

### Entities

| Entity | Owner | Lifecycle |
|--------|-------|-----------|
| `RawWorkout` | `fit_parser` | 記憶體暫存，解析後即轉換 |
| `Workout` | `storage` | 每次匯入建立，不修改 |
| `MmpCurve` | 嵌入 `Workout` | 與 Workout 同生命週期 |
| `AthleteProfile` | `storage` | 手動建立，手動更新 |
| `WorkoutIndex` | `storage` | 每次匯入更新 |

### Schema

**`workouts/YYYY-MM-DD_HH-MM-SS.json`**
```json
{
  "id": "2026-05-14T08:30:00",
  "source_file": "ride.fit",
  "sport": "cycling",
  "date": "2026-05-14",
  "duration_s": 3612,
  "metrics": {
    "avg_power_w": 210,
    "normalized_power_w": 235,
    "intensity_factor": 0.94,
    "tss": 98.5,
    "avg_hr_bpm": 158,
    "avg_cadence_rpm": 88,
    "distance_m": 42000,
    "elevation_gain_m": 450
  },
  "mmp_curve": {
    "1": 850,
    "5": 620,
    "10": 520,
    "30": 400,
    "60": 320,
    "120": 280,
    "300": 255,
    "600": 240,
    "1200": 230,
    "1800": 225,
    "3600": 218
  },
  "raw_channels": {
    "sample_rate_s": 1,
    "has_power": true,
    "has_hr": true,
    "has_cadence": true,
    "has_gps": false
  }
}
```

**`athlete.json`**
```json
{
  "name": "Example Runner",
  "ftp_w": 250,
  "weight_kg": 70,
  "updated": "2026-05-14"
}
```

**`index.json`**
```json
{
  "workouts": [
    {
      "id": "2026-05-14T08:30:00",
      "date": "2026-05-14",
      "sport": "cycling",
      "duration_s": 3612,
      "tss": 98.5,
      "normalized_power_w": 235,
      "file": "workouts/2026-05-14_08-30-00.json"
    }
  ],
  "last_updated": "2026-05-14T10:00:00"
}
```

### 儲存策略

- **Forward**: 每次 `import` append 新 workout JSON + 更新 index
- **Backward**: 刪除 workout JSON + 從 index 移除對應記錄
- **Backfill**: 不需要（Milestone 1 只處理新匯入）
- **Coexistence**: JSON 格式加版本欄位 `"schema_version": 1` 供後續升級

---

## CLI Contracts

### Commands

| Command | Purpose |
|---------|---------|
| `wko import <file.fit>` | 解析並儲存 FIT 檔 |
| `wko show <date>` | 顯示特定日期訓練摘要 |
| `wko list [--last N]` | 列出最近 N 筆訓練 |
| `wko validate <file.fit>` | 對比 WKO5 輸出（開發用） |
| `wko mmp <date>` | 顯示完整 MMP 曲線 |

### Output Shape

```
$ wko import ride.fit
✓ Parsed: 2026-05-14 08:30, 60:12 min
  Avg Power:  210 W
  NP:         235 W
  TSS:        98.5
  MMP 5min:   380 W | 1min: 520 W | 20min: 260 W
  Saved: workouts/2026-05-14_08-30-00.json

$ wko validate ride.fit
Comparing against WKO5...
  MMP 5min:    380 W vs 381 W  → ✓ (0.26%)
  MMP 20min:   260 W vs 261 W  → ✓ (0.38%)
  NP:          235 W vs 236 W  → ✓ (0.42%)
```

### Error Handling

| Error | Exit Code | Message |
|-------|-----------|---------|
| FIT 檔無 power channel | 0 | `Warning: No power data, MMP/NP/TSS skipped` |
| FIT 檔損壞 | 1 | `Error: Invalid FIT file: {reason}` |
| FTP 未設定 | 0 | `Warning: FTP not set in athlete.json, TSS skipped` |
| 重複匯入 | 0 | `Info: Already imported, skipping` |

---

## Non-Functional Requirements

| Category | Target | Measurement | How Achieved |
|----------|--------|-------------|--------------|
| **算法準確度** | MMP ≤ ±1% vs WKO5 | 5 份 .fit 對照測試 | Numpy 滑動窗口，無浮點截斷 |
| **計算效能** | 1h 訓練 < 2s | 本機 `time wko import` | Numpy vectorized，非 Python for-loop |
| **FIT 相容性** | Coros + Garmin .fit | 各解析測試集 | `fitparse` 支援 FIT Protocol 2.0 |
| **可重現性** | 同一檔案結果不變 | 多次執行對比 | Pure function，無側效應 |

---

## Technology Choices

| Concern | Choice | Alternatives | Rationale |
|---------|--------|--------------|-----------|
| FIT 解析 | `python-fitparse` | `fit_tool`, Garmin SDK | 最多 stars，pure Python，Coros 已知可用 |
| 向量運算 | `numpy` | pure Python lists | MMP 滑動窗口需要 vectorized ops 達到性能要求 |
| 儲存格式 | JSON | SQLite, CSV | 最簡單，人眼可讀，AI 問答直接餵入（Milestone 2 準備） |
| CLI 框架 | `click` | `argparse`, `typer` | 最少依賴，decorator 語法清晰 |
| Python 版本 | 3.11+ | 3.9, 3.10 | `match` 語法，`tomllib` 內建 |

---

## Codebase Patterns to Follow

> 此為 greenfield 專案，以下為建立時要遵守的慣例。

| Pattern | Reference | Why |
|---------|-----------|-----|
| Pure function computation | WKO5 `searchMeanMaximalTimeOrDistance` 無全域狀態 | 方便單元測試與驗證 |
| Channel-named data | WKO5 `PKDataFrame` (named PKVectors) | 對應 FIT message 欄位命名 |
| Separated parse/compute | WKO5 `PKImportFiles.cpp` vs `PKExpressionParser.cpp` | 解析與計算解耦 |

---

## Risks & Trade-offs

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| MMP 滑動窗口邊界處理與 WKO5 不同（gaps 定義不一致） | H | H | 對照 GoldenCheetah 開源實作；先用 no-gaps 版本驗證 |
| Coros .fit 格式有私有 developer fields | M | L | 忽略未知 fields，只取標準 channels |
| WKO5 的 MMP 使用次秒精度（10Hz）而一般 .fit 是 1Hz | M | M | 先實作 1Hz，記錄誤差範圍，未來可重採樣 |
| FTP 手動設定不精確影響 TSS 計算 | M | L | Milestone 1 範圍：TSS 計算正確即可，FTP 準確度是 Milestone 3 |
| `fitparse` 對損壞的 .fit 拋出異常而非降級 | L | L | try/catch + graceful error message |

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| 算法來源 | strings/nm 提取公式 + GoldenCheetah 交叉驗證 | 純動態 hook (Frida) | 靜態分析已足夠重現，不需安裝複雜工具 |
| 不呼叫 framework 動態 | Python 重寫 | ctypes 呼叫 PowerKitOSX | 避免授權問題；Python 版本可測試、可移植 |
| JSON 儲存 | 扁平 JSON | SQLite | Milestone 2 AI 問答可直接傳入；無 schema migration 負擔 |
| MMP 範圍 | 1s–3600s，非均勻取樣 | 全部 1s 步長 | 節省空間；1/5/10/30/60/120/300/600/1200/1800/3600 與 WKO5 顯示值對齊 |
| Milestone 1 不含 FTP 估算 | 手動設定 FTP | 自動 `ftp(meanmax(power))` | 確保 MMP 算法本身正確後再疊加 FTP 估算層 |

---

## Open Questions

- [ ] Coros .fit 的 `timestamp` channel 是 local time 還是 UTC？（影響日期分組）
- [ ] WKO5 MMP 是否先做 1s 重採樣再計算，或允許不規則採樣間隔？（影響 ±1% 目標）
- [ ] `meanmaxgaps` 的 gaps threshold 是幾秒靜止？（WKO5 strings 未洩漏此值，需測試確認）
- [ ] 是否需支援 run power（`runpower`）或只做 cycling？（FIT messages 不同）

---

## Next Step

```
/prp-plan docs/srs/completed/wko5-milestone1-fit-mmp.srs.md
```

計劃層將繼承上述算法規格，分解為：
- Step 1: 專案結構 + `athlete.json` 初始化
- Step 2: `fit_parser.py` + `fitparse` 整合
- Step 3: `mmp.py` numpy 滑動窗口實作
- Step 4: `metrics.py` NP + TSS 計算
- Step 5: `storage.py` JSON 讀寫
- Step 6: `cli.py` import/show/validate 指令
- Step 7: 5 份 .fit 對照 WKO5 驗收測試
