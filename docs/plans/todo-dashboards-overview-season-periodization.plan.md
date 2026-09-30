# Plan: 總覽 / 賽季回顧 / 週期化訓練 三個儀表板

Date: 2026-09-30. Branch: `feat/tis-dashboard`（worktree `wko5_coach-tis`）。

Scope 由使用者決定：
- 不做儀表板編輯（沒有編輯按鈕、拖拉、CRUD）。
- 做三個規劃好的儀表板 + 一組分析圖。
- 不做 HRV 和安靜心率。
- 不做路線完賽時間預測、功率預測、天氣。main tree 的 SuperPower Calculator / 賽事功率頁（`backend/engine/racepower/`、`backend/api/racepower.py`、`racepower.html`）已經在做這些。儀表板需要賽事預測時，等它 commit 後連結或呼叫它的 API，這裡不重做。

## 架構：沿用 custom view，特殊圖表用 panel

每張圖表都走現有的 custom view 管線：`views/*.json` → `backend/api/wko5views.py` → `backend/static/wko5_viewer.html`。

- 用 WKO5 運算式就能算的圖，直接寫成 series expression（`athleterange`、`tisaerobic`…）。
- 運算式寫不出來的圖（逐次爬坡散佈、坡度分箱表、區間分析、預測、建議）改用 `kind: "panel"`，由 `panel: "<name>"` 指到 `backend/engine/panels/` 裡的 Python 函式。它的回傳格式跟 render.py 的 series JSON 一樣（points / hline / band / values）。另外多兩種 series 格式：`table`（欄 + 列）和 `text`（建議文字）。這樣 viewer 只需要再學畫 table、gauge、scatter。
- 這個做法跟 main tree 已有的 `kind: "zones"` / `"targets"` 同一套。

不做新的資料模型：
- 週期、賽事、門檻都讀 `backend/engine/planning.py` 的 `Plan`（`~/.wko5coach/plan.json`）。
- 狀態讀 `backend/engine/status.py`。

## 共用的 engine 模組（每個都附 unit test）

| 模組 | 內容 |
|---|---|
| `panels/loadfocus.py` | 4 週負荷焦點。以下「每秒」都指每一個取樣點，dt = 1 s。有功率時，每秒負荷 = (P/FTP)² × dt/36（跟 TSS 同量綱）。依 P < 0.85 FTP / 0.85–1.05 / > 1.05 分成 低有氧 / 高有氧 / 無氧，這三條線就是 WKO5 Energy System Impact 的分界。沒有功率時改用 HR：< 0.89 LTHR / 0.89–1.0 / > 1.0，負荷用 hrTSS 依時間比例分配。目標範圍依當下週期決定（基礎期：低 60–80%、高 15–30%、無氧 0–10%；專項期：低 50–70%、高 20–35%、無氧 5–15%；減量期同專項期）。**[ours] heuristic**，文件會寫清楚。 |
| `panels/fatigue.py` | COROS 式疲勞帶：ATL(7d) / CTL(42d) × 100。≥150 過量、100–149 最佳化、80–99 維持、<80 恢復。另算 Foster monotony = 週日均負荷 / 日負荷標準差，strain = 週負荷 × monotony。 |
| `panels/recommend.py` | 「下一課練什麼」：先看疲勞帶（≥150 → 休息或輕鬆），再依 load-focus 缺口，最後看週期；每條理由都列出依據的數字。 |
| `panels/workout.py` | 單次活動：時間區間分布（HR / 功率 / GAP）、坡度分箱表（每個坡度桶的配速 / 功率 / HR / 步頻 / 時間；WKO5 workout-view「坡度」那張的分箱）、耐力曲線（滾動 10 分 GAP/HR 或 功率/HR 對累積 kJ 或時間）、TIS 儀表。 |
| `panels/trends.py` | EF + 解耦趨勢（>60 分輕鬆跑，5% 參考線）、門檻趨勢（CP/mFTP、LTHR、門檻配速）、MMP/PD 曲線今昔對比。 |
| `panels/trail.py` | 每次爬坡的 VAM vs 坡度散佈（`climbs.detect_climbs`）+ 90 天擬合線；每週下坡量與下坡負荷（坡度 < -10% 的時間 / 距離 / 下降）。 |
| `panels/effortpace.py` | 個人化坡度配速：擬合自己「坡度 → 在某 HR 下的配速」曲線。L，最後做。 |

## 儀表板 1：總覽（view `總覽`）

目的：現在狀況如何、下一課該練什麼。

| 位置 | 圖 | 資料來源 |
|---|---|---|
| 1 列 | 建議（text panel） | `recommend.py`（status + loadfocus + fatigue） |
| 2 列左 | PMC + COROS 疲勞帶 | CTL/ATL/TSB 運算式；疲勞帶 panel |
| 2 列右 | 最近一次活動 TIS 有氧 / 無氧（gauge 1–10，6 門檻） | `tisaerobic` / `tisanaerobic` builtin |
| 3 列左 | 4 週負荷焦點 vs 目標範圍 | `loadfocus.py` |
| 3 列右 | 每週爬升 / 下降負荷 | `sum(climbing)` 運算式 + `trail.py` 下坡量 |

## 儀表板 2：賽季回顧（view `賽季回顧`）

目的：現在跟過去比有沒有進步。

| 圖 | 資料來源 |
|---|---|
| MMP/PD 曲線：最近 90 天 vs 前 90 天 vs 去年同期 | `meanmax(power)` 運算式（三個 athleterange） |
| 門檻趨勢（CP / mFTP、LTHR、門檻配速） | PD fit 運算式 + `thresholds.py` / plan thresholds |
| EF + 解耦趨勢（輕鬆跑 > 60 分，5% 線） | `trends.py` |
| VAM vs 坡度（每段爬坡）+ 90 天擬合 | `trail.py` |
| Chronic / Acute TIS Load | WKO5 season-view 同名圖（`tl(tisaerobic…)`） |
| Aerobic Impulse / Anaerobic Impulse | WKO5 season-view 同名圖 |

## 儀表板 3：週期化訓練（在 `周期化訓練` view 新增一個 dashboard）

目的：朝目標賽事的階段安排與執行。

| 圖 | 資料來源 |
|---|---|
| 階段時間軸（base / specific / taper / event / recovery）+ 目標賽事 | `planning.phases` / `goals` |
| 計畫 vs 實際週負荷 + 週增幅 | 實際值取 daily TSS；計畫值依階段推算（基礎期每週 +5–8%、專項期持平或微增、減量期 -40–60%）。這是 **[ours]** 推算；如果 main tree 已經有 weekplan 的計畫負荷，就改用那個 |
| Monotony / Strain（Foster） | `fatigue.py` |
| 每個 block 的負荷焦點分布 | `loadfocus.py`（依 phase 切段） |

## 單次活動（workout view `訓練效果-越野`）

時間區間分布、坡度分箱表、耐力曲線、Energy System Impact - Run 散佈、TIS 儀表。

## 驗證

- 每個 panel 模組都有 pytest，用 `wko5_fakes` 建造合成 workout，預期值手算得出。
- TIS 跟 golden test 的 WKO5 值比對（如果 golden 有）。
- 在 viewer 實際載入三個 view，截圖。

## 順序

1. 本文件
2. rebase 到 main（TIS 那邊）
3. panel 基礎（kind 註冊 + viewer 畫 table / gauge / scatter / text）
4. fatigue、loadfocus、recommend → 總覽
5. workout panels
6. trends、trail → 賽季回顧
7. 週期化（賽事預測直接連到 racepower 頁）
8. effortpace
