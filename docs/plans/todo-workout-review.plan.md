# 單次活動判讀（workout mode 分頁 + 判讀卡）— 設計

> Status: 設計完成，待使用者確認後實作（2026-09-30）
> 使用者需求：「基礎期要看心率飄移，如果穩定了就能加間歇，然後要看間歇有沒有練到有氧閾值」；另外要一個跑姿／膝蓋負荷的參考分頁（落地衝擊、剛性）。

## 結論
- 大部分**不必改 viewer**：
  - custom view 已經支援 `kind:"workout"`（`customviews.py:74-76`）。workout 圖表佔多數的 view 會自動歸到「單次活動」模式（`wko5_viewer.html:340-344`），所以分頁寫成新檔 `views/workout.json`。
  - 判讀卡是新的 chart kind `"review"`，對外回報為 `workout`（`wko5views.py` 的 `_panel_kind`）。卡片回傳 `draw()` 本來就會畫的 JSON：`value` 字串、`values` 表格、`empty` 說明。
- **expression 做不到的部分**都放進新檔 `backend/engine/workout_review.py`：分組偵測、爬坡段、8–12 週基準（median ± IQR）、課表類型判定。
- 門檻線要寫成 `(,aethr)`、`(,lthr)`、`(,0.95*cp)`：沒有 x 的 PairV 才會畫成水平線。

## 分頁（6 個，依重要性排序）

| # | 分頁 | 主題 | 什麼時候適用 |
|---|---|---|---|
| 0 | 本次重點 | 判讀卡（依課表類型自動選）＋ 3–4 張關鍵圖；測試課的結果也在這頁 | 所有活動；肌力、騎車只有這頁 |
| 1 | 有氧／心率飄移 | 有沒有壓在 AeT 以下、Pa:HR 飄移 | 輕鬆跑、長跑、AeT 測試；基礎期最重要 |
| 2 | 間歇 | 每組有沒有達標、有沒有掉、恢復時心率有沒有降 | 品質課、CP 測試 |
| 3 | 爬坡與地形 | 爬坡段、VAM 對心率、坡度對心率／功率、下坡 | 越野、登山；專項期最重要 |
| 4 | 配速與耐久 | 後段有沒有掉、移動與停留時間、補給 | 長跑、越野、登山 |
| 5 | 跑姿與膝蓋負荷（參考） | 衝擊與剛性在跑步中的漂移、下坡佔比、和自己基準比較 | 有 Stryd 的跑步 |

## 各分頁的圖

第一張圖都是判讀卡（`kind:"review"` 加 `section`）。

- **本次重點**：
  - 心率＋功率，加 AeT、LTHR、CP 門檻線
  - AeT／LTHR 三區時間分布
  - 測試課：CP 3'/12' 結果（`(p12·720 − p3·180)/540`，W′）
  - AeT 測試：飄移測試結果（`threshold_estimate.steady_drift`）
- **有氧**：
  - 心率 vs AeT
  - 滾動 EF（`ewma(speed,300)/ewma(heartrate,300)`）
  - Pa:HR 前後半比較（不含前 10 分鐘）
  - 每公里心率
  - 沿用 WKO5「Heart Rate vs. Power by % of work」「PWHR 心飄移」
- **間歇**：
  - 每組表（後端 `detect_efforts`）：時長、功率、%CP、心率、休息 60 秒後心率降幅
  - 功率＋目標帶
  - 沿用 dFRC Run、Today's MMP vs 90 Day Best、Marking VO2max Intervals
- **爬坡**：
  - 沿用 Elevation over Distance Colored Gradient
  - 爬坡段表（`climbs.detect_climbs`）：VAM、每 100 m 爬升的心跳數
  - 坡度對心率／功率
  - 各心率下的 VAM
  - 坡度分組表（`panels.workout.grade_bins`）
  - 沿用 VAM Analysis、Hilly Run Summary、Heart Rate vs. Power by grade、Elevation Corrected Power
- **耐久**：
  - 耐久曲線（`panels.workout.durability`）
  - 每 10% 距離的配速／心率
  - 移動與停留時間
  - 補給點：目前沒有資料來源，顯示「沒有補給紀錄」
- **跑姿與膝蓋（參考）**：
  - 沿用「LSS and LIS- Stryd」「Running Dynamics」「LSS飄移率 by坡度」「步頻 vs 垂直比」
  - 前 ⅓ 對後 ⅓ 的漂移：ILR、LSS、kleg、GCT、cadence
  - 衝擊對坡度（ILR、Impact Gs = fmax/(體重·g)）
  - 陡下坡（坡度 < −10%）的時間、距離、衝擊量佔比
  - 基準比對表：同類課表 8–12 週的 median ± IQR；樣本少於 5 次不比較
  - 左右不對稱：目前 80 筆都沒有 balance 通道，出現時才顯示
  - 沒有 Stryd 的活動：只顯示 cadence、GCT、VO、kleg、Impact Gs，並註明無法判讀衝擊與剛性
  - 卡片上一律寫「參考」

## 判讀規則（寫法比照 status.py 的 if/else）

**判定課表類型**，依序：
1. 周期：用活動當天的周期（`phase_on(plan, 活動日期)`）
2. 肌力、騎車、走路：用 `category()` 判斷
3. test_cp：plan 同一天有 CP 測試紀錄，或標題含 CP／測試，或偵測到 3' 和 12' 兩組全力段
4. test_aet：同一天有 AeT 紀錄，或偵測到符合條件、≥ 55 分鐘的穩定跑
5. quality：達到 `HARD_SESSION_S`
6. long：移動時間 ≥ 75 分鐘，或 ≥ 當週長跑目標 × 0.8
7. easy：以上都不是
- 地形（trail／hike）另外由 tags 判斷

**判讀規則**（每頁最多 3 行）：
- 輕鬆跑、長跑：
  - 心率超過 AeT+3 的時間 > 10% → 下次放慢
  - 飄移 < 5% → 有氧基礎穩（顯示連續次數）；連續 3 次 → 可以加間歇
  - 飄移 5–10% → 暫時不加間歇
  - 飄移 > 10% → 有氧基礎不足
  - 有坡、有停頓的活動，飄移數字不採用
- 間歇：
  - N 組中幾組落在目標帶內
  - 最後一組比第一組低 > 5% → 組數減 1 或多休
  - 休息 60 秒心率降幅 < 20 bpm → 休息拉長
  - 功率有到、心率在 AeT–LTHR 之間 → 屬於閾值下
- 測試：
  - CP 和目前值差 > 3% → 建議更新
  - AeT 測試：飄移 < 5% → 前半段心率可以設成 AeT
- 越野：
  - 每 100 m 爬升的心跳數比近 8 週低 > 5% → 爬坡經濟性變好
  - 最後 20% 耐久 < 90% → 補給、配速要調整
- 跑姿（參考）：
  - 後段 ILR 升幅超出自己的 IQR，且陡下坡 > 30% → 留意膝蓋，下坡放慢或縮步
  - LSS 下降同時 GCT 上升 → 疲勞跡象

## 連到進度決策
- `status.py` `i_drift`：
  - 篩選條件改成：路跑、≥ 40 分鐘、平均心率 ≤ AeT+3
  - 加上連續次數 `extra.streak` / `streak_ok`（≥ 3 次）
- `overview.py` `week_plan`：
  - 基礎期的 `allow_quality` 必須 `streak_ok` 為真
  - 第一次開放時先排「閾值下 3×8 分」
  - 上次間歇判讀為「掉了」→ 組數減 1
- `PHASE_FOCUS["base"]`、`i_drift.action`：
  - 達標：寫「飄移已連續 3 次 < 5%，本週可以加一次閾值下間歇」
  - 未達標：寫「還差 N 次」，這會出現在總覽的「還缺什麼」
- `i_testing`：最近一次 CP 測試的差距 > 3%、但 plan 還沒更新 → 狀態改為 watch，行動寫「套用這次的 CP」
- 可選：新增 `i_knee`，近 4 週出現 ≥ 2 次參考旗標就提醒（info 級）

## 實作順序
1. `backend/engine/workout_review.py`（新增）＋ `backend/tests/test_workout_review.py`
2. `customviews.py`：kind 白名單加 `review`，保留 `section`
3. `backend/api/wko5views.py`：`_panel_kind` 加 review → workout、`needs_workout`、`_render` 分支、`GET /workouts/{i}/review`
   - **注意**：這個檔案目前是圖表掃描 agent 在改，要等它 commit 之後再動
4. `views/workout.json`（新增）
   - 先驗證 `bin(heartrate,{aethr,lthr})` 能不能用變數當切點
5. `status.py`：`i_drift` 加連續次數，`i_testing` 讀 CP 差距
6. `overview.py`：`allow_quality` 和間歇選擇
7. 可選：viewer 讀 `suggested_dashboard` 後自動跳到建議的分頁（約 5 行），要等 viewer 沒有其他人在改時再做
