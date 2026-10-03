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

## 單次活動頁：WKO5 對照 + 自己的儀表板

活動頁分兩個 tab：
- 「WKO5 對照」：原本的 WKO5 Workout view，不改。
- 「分析」：自己的單次活動儀表板。

「分析」tab 的版面依活動類型自動選。類型由 sport / sport_type / GPX 分類決定，可以手動覆寫。版面寫在宣告式設定檔 `views/workout_layouts.json`：類型 → 區塊清單，每個區塊是一個 panel 名稱加參數。之後要調整版面只改這個 JSON，不必改程式碼。

每種版面都要回答三個問題：這課練了什麼、表現得如何、跟自己的能力比起來怎樣。

最上方一律是「計畫 vs 實際」（見下一節），接著依類型：

| 類型 | 區塊 |
|---|---|
| 跑步（路跑 / 跑道） | 摘要列（距離、時間、配速、NP / 平均功率、平均 HR、rTSS / TSS、IF）；TIS 有氧 / 無氧 + 主要效益；區間時間（HR / 功率 / 配速）；圈 / 間歇表（自動偵測努力段）；本次最佳努力 vs PD 曲線（1 / 5 / 20 / 60 分功率與配速佔 90 天最佳的 %）；EF 與解耦 vs 最近輕鬆跑基準；步頻 / 步幅 |
| 越野跑 | 摘要（含爬升 / 下降、GAP / EFD）；TIS + Energy System Impact；區間時間（HR / 功率 / GAP）；坡度分箱表；爬坡清單（每段的坡度、長度、VAM、功率、HR，並對照自己的 VAM-坡度擬合）；下坡表現與下坡負荷（坡度 < -10% 的時間）；耐力曲線（GAP/HR 或 功率/HR 對累積 kJ / 時間，逐小時衰退）；各坡度的跑 / 走比例 |
| 百岳 / 登山 | 摘要（移動 vs 總時間、爬升 / 下降、最高海拔、3000 m 以上時間）；垂直速度（上 / 下 m/h）vs 個人基準，以及 vs 標準時間（GPX metadata 有魯地圖 / 上河標準時間就用，沒有就用 Naismith / Tobler 推估），算出「配速 / 標準」比；HR 區間時間與整天的 HR 飄移；耐力（每小時垂直速度衰退）；休息 / 停留分析；能量消耗估算；到達的山頭（比對百岳 / 中級山清單，沿用 另一個專案 的 twmap snapshot 做法）。沒有功率時不顯示 TIS，改用 HR 負荷並清楚標示 |

三種版面共用同一批 panel 元件。

## 計畫 vs 實際

每一課活動，如果當天有計畫課表，就算出下面這些：
- 預定目的（有氧基礎 / 閾值 / VO2max / 爬坡 / 長距離耐力）vs 實際主要效益（由 TIS + 能量系統比例判定）。
- 計畫 vs 實際的時長、負荷、TSS。
- 目標區間內的時間 %。
- 判定：達成 / 偏輕 / 偏重 / 練錯目標，附一行理由。

單次活動頁：放在最上方。

總覽頁：列出最近 7–14 天的判定，並顯示這些課加總起來跟目前 block 目標（負荷焦點 vs block 目標）的差距。

沒有計畫時，退回用 block 的目標分布判斷。

目前的限制：`planning.py` / `plan.py` 只有賽事、週期、門檻，沒有每日課表，所以要等後面的「課表」階段建好 planned-session 模型才有資料。在那之前一律走退回邏輯。

## 同步強化（優先，現在就做）

範圍限於 main tree 沒動到的檔案：`backend/sync/*`、`backend/files/file_service.py`、`backend/api/sync.py`、`backend/api/auth.py`、`backend/api/pmc.py`、`backend/db/*`，以及新檔。

1. TP：`tp_token_expires` 從 SQLite 讀出來是 naive，跟 aware now 比較會 TypeError。改成跟 coros 一樣視為 UTC。
2. TP：detaildata / filedata 回 401/403/404 時，當成錯誤並且不推進 cursor（原本回 None → 計為 no_file → cursor 前進，那些課就永遠被跳過）。真的沒有檔案（detail 沒有列檔名）才算 no_file。
3. 匯入失敗要 rollback，TP 和 COROS 都要；TP 遇到壞掉的 FIT 也要記 stub；TP 不再吞 TypeError。
4. COROS：用上次同步的日期減 3 天當 cursor，不再每次從 20200101 開始（`since` 參數仍可覆寫）；偵測區域時改用「最近 30 天」的日期範圍；token 過期回傳明確的 `COROS_AUTH_REQUIRED` 事件，不是直接丟例外。
5. `workout_files` 新增兩個欄位：`start_time_utc`，以及依設定時區（`athlete.timezone` 設定 → 環境變數 `WKO5COACH_TZ` → 系統時區）換算的本地 `workout_date`。附 migration，外加一個 backfill 腳本修正舊資料。
6. 跨來源去重：新增欄位 `duplicate_of`。開始時間相差 ±2 分鐘視為同一課。以 `sync.primary_source` 設定的來源為主；沒設定時，以最早匯入的為主。PMC 和 analytics 只算 `duplicate_of IS NULL` 的活動。
7. 指標：沒有功率的跑步也要算 hrTSS / rTSS（原本遇到沒有功率就提早 return）；FTP 改用活動日期當時有效的設定，不是最新一筆；爬升優先採用 FIT session 的 `total_ascent`，跟 COROS / WKO5 一致。
8. `backend/scripts/compare_sources.py`（唯讀）：把 COROS FIT / synced workout_files 跟本機 `.wko4` 以開始時間 ±2 分鐘配對，印出差異：時長、距離、爬升、NP / 平均功率、平均 HR、TSS。
9. 端對端測試，用 httpx MockTransport 假造 HTTP：login → list → download → import → dedup，兩個 client 都測，完全不連真實網路。
10. `docs/deploy/sync-verification-checklist.md`：給使用者的手動驗證清單。

先記錄、暫不改：
- COROS login 會把 profile 的 FTP 寫進 `ftp_w`。這牽涉到 plan / threshold 的檔案，而那些檔案 main tree 正在改。
- TP client 冒用 WKO5 的 client_id / User-Agent，有違反服務條款的風險。之後會放在 opt-in 設定後面，並附警告。
- repo 裡有寫死的個人路徑與個人資料（見「公開 repo」一節）。

## 資料來源與使用者設定（同步強化之後）

- 設定頁可以選主要來源、個別開 / 關每個同步。
- 「來源一致性」報告頁：用 compare_sources 的邏輯，以 API + 頁面呈現，附 CTL 影響。
- 使用者設定模型：`backend/settings/` 的 `SettingsRepository(user_id)`，存在 SQLite `user_settings` 表。secret 用 Fernet 加密存放，金鑰取自 `WKO5COACH_SECRET_KEY`；沒有設定時，在 `~/.wko5coach/secret.key` 產生一把並警告。預設單一使用者（user_id = 1），將來加 auth / 多使用者時不必重寫。
- 存放的內容：COROS 帳號 / token、TP token、CWA 授權碼、athlete 參數、時區、主要來源。
- 設定頁跟著 main tree 的 `settings.html` / `/settings` 路由走，每個連線都能輸入並測試。

## 公開 repo 注意事項（只列出，不改歷史）

- `api/wko5views.py`、`api/plan.py`、`api/achievements.py`、`scripts/build_baiyue.py` 寫死了 `~\Projects\TrailRunCoach\WKO5\Athlete`。
- repo 追蹤了個人的 `WKO5 Season View/*.wko5chart`、`WKO5 Workout View/*.wko5chart`。
- `chartfixes.py` 裡有 "Athlete"。
- token 以明文存在 SQLite（會由上面的使用者設定模型改為加密）。

## 課表（最後一個階段）

1. planned-session 模型：日期、運動、目的、結構化步驟（時長 / 距離 + 功率 / HR / 配速目標）、計畫 TSS、備註、`source = generated | coros | manual`、`external_id`。
2. 規則式課表產生器，先不用 LLM，之後可以外掛：
   - 輸入：CTL / ATL / TSB、疲勞帶、週期 / block、目標賽事、4 週負荷焦點缺口、最近的活動、門檻、每週可訓練時間。
   - 輸出：未來 1–2 週的課。遵守 ramp rate 上限與難 / 易間隔，長跑放週末，越野專項課（坡道反覆、VAM 爬升、下坡練習）依 block 需要安排。每一課都附理由。
3. COROS 課表同步（非官方 Training Hub API）：
   - 推送結構化跑步課表到 COROS 行事曆。
   - 讀回 COROS 行事曆（`/training/schedule/query`），把在 COROS app 排的課也匯入。
   - 刪除 / 更新已推送的課。
   - 全部放在 interface 後面，有 dry-run 模式，測試只用 fixture。
   - 第一次真正推送前停下來，回報等使用者確認。
4. 「課表」獨立頁，入口放在跟設定頁、racepower 頁同一個選單。流程：產生 → 檢視 / 編輯 → 推送到 COROS → 顯示每一課的狀態（已推送 / 已匯入 / 已完成 + 判定）。

## 順序

1. 本文件
2. 同步強化（現在就做，不必等 main tree）
3. rebase 到 main（TIS 那邊 commit 之後）
4. panel 基礎（kind 註冊 + viewer 畫 table / gauge / scatter / text）
5. fatigue、loadfocus、recommend → 總覽（接在 main tree 的 overview.py / overview.html 之上，不另做一份）
6. 單次活動「分析」tab（依類型的宣告式版面）+ 計畫 vs 實際（先走退回邏輯）
7. trends、trail → 賽季回顧
8. 週期化（賽事預測直接連到 racepower 頁）
9. effortpace
10. 使用者設定 / 加密 secret / 來源一致性頁
11. 課表：模型 → 產生器 → COROS 同步（dry-run）→ 頁面
