# cairn-curve v1：給疊石 Cairn 的時間曲線

SP-653（研究：`docs/analysis/sp570-cairn-itinerary-times.md`）。TrailRunCoach（TRC）輸出、Cairn 照這份實作（Cairn 單 SP-656）。
程式：`backend/engine/racepower/cairn_curve.py`；測試：`backend/tests/test_cairn_curve_sp653.py`。

## 這是什麼

一次賽事計算的**腳程**：沿路線的「里程 → 累積移動秒數」。Cairn 把它當腳程模型 `paceMinutes` 的第五種填法 `curve`：
一段路（from → to）的移動分鐘 ＝ `(T(to) − T(from)) / 60`，`T` 是對 `points` 的分段線性內插。

- **只有腳程，沒有時刻**：抵達時刻仍由 Cairn 的推估鏈算（每天出發時刻、每點停留、過夜）。外部來源不寫 `poi_visits.eta`。
- **不含**座標、停留（每點休息）、TRC 的任何 id 或 token。
- 夜間減速與熱已經算在曲線裡（TRC 依計算時的出發時刻算），那個時刻寫在 `start_times`。

## 怎麼拿

| 來源 | 說明 |
|---|---|
| `GET /api/v1/racepower/saved/{event_id}/cairn-curve` | 賽事存過的計算＋賽事存的 GPX。`?download=1` 加 `Content-Disposition`。錯誤：`404 NO_RESULT`；`409 NO_GPX`／`GPX_CHANGED`／`GPX_MISMATCH`／`NO_TRACK`／`NO_SEGMENTS`；`400 BAD_ID`。`detail = {code, message}` |
| 公開分享快照 `GET /share/{id}/data` 的 `cairn_curve` | 建立分享時勾「附上給疊石 Cairn 的時間曲線」（`include_cairn_curve: true`）才有，**預設沒有** |
| 推到 Cairn（SP-655，`backend/accounts/cairn_push.py`） | 賽事頁按「推到 Cairn」，曲線放進 Cairn 行程的 `pace_config = {model: {kind: "curve", curve}}`。曲線原樣送（Cairn passthrough），見 `docs/plans/cairn-link.md` 第 4.3 節 |
| 程式介面（SP-654 的 link API 用） | `cairn_curve.for_saved(event_id) -> dict`，失敗丟 `cairn_curve.CurveError`（`.code`、`.status`）。要在該使用者的 tenant 裡呼叫 |

曲線只能用**賽季計畫賽事存的 GPX** 算（要原始檔才有 hash）。計算用的 GPX 跟賽事目前存的不是同一份（sha1 對不上）→ `GPX_CHANGED`，重算再拿。

## 欄位

| 欄位 | 型別 | 說明 |
|---|---|---|
| `v` | `1` | 版本。Cairn 不認得的版本 → 拒絕 |
| `source` | `"trailruncoach"` | |
| `gpx_sha256` | hex 字串 | GPX **原始檔位元組**的 SHA-256 |
| `total_m` | number | 路線總長，**照 Cairn 的量法**：檔案裡每個 `<trkpt>`（依文件順序、不含 `<rtept>`）相鄰兩點 haversine（R = 6371008.8 m）相加 |
| `points` | `[[m, cum_moving_s], …]` | 第一點 `[0, 0]`；之後每個 TRC 分段終點一點；`m` 嚴格遞增、最後一點 `m = total_m`；`cum_moving_s` 不遞減。`m` 是 TRC 的里程按比例換到 `total_m` 上；小數 1 位 |
| `day_splits_km` | number[] | 每天的分界（第 2 天起點、第 3 天起點…），同一套里程（km）。單日行程是 `[]` |
| `start_times` | (`"HH:MM"` \| null)[] | TRC 計算時每天的出發時刻（第 1 天在前）；單日只有一個；`null` = 計算時沒填時刻（曲線不含依時刻的影響） |
| `fallback` | `{kind:"munter-split", upEph, downEph}` | 退回用的 Cairn 腳程，直接是 Cairn 的 `munter-split` 參數（見下） |
| `fallback_check` | object | 只供顯示：`trc_s`、`fallback_s`、`total_err_pct`、每天的 `{day, trc_s, fallback_s, err_pct}`、`rmse_s`（每段誤差）、`clamped`（參數被夾進 0.5–40） |
| `rest_hint` | `{basis, days:[{day, rest_s, range_s}]}` \| null | **建議休息**，只提示、Cairn 不寫入。`basis`：`nonmoving:<user\|stations\|rate>`（越野的非移動時間預估：SP-721 起依計畫的停留類型 `kind`（`race` 比賽／`solo` 自主長程）、停留比例隨移動時間增加且 2.5 小時封頂，`range_s` = p25–p75；`kind` 是選填欄位）、`nonmoving:hike`（百岳，SP-732 起：每天用停留模型的健行類預估，`kind` = `hike_group` 跟團／`hike_solo` 自己走／`hike`，`range_s` = 每天的 p25–p75）或 `moving_ratio`（百岳、沒有停留資料時：每天時鐘時間 − 移動時間）。沒有預估時 `null`，Cairn 照常運作 |
| `computed_at` | `"YYYY-MM-DDTHH:MM:SS"` \| null | 計算存檔時刻（伺服器當地牆鐘，不帶時區） |
| `model` | 字串 | 例 `trail/trail_hr`、`baiyue/capacity`，只供顯示 |

Cairn 驗證時**忽略不認得的欄位**（之後的版本可能加欄位而不升 `v`）。

## fallback 怎麼算（TRC 端）

照抄 Cairn 的 `rangeStats`（`v2/app/src/lib/elevation.ts`）與 `smoothElevations`（`v2/shared/src/geo.ts`）：

1. 讀所有 `<trkpt>`；海拔沿里程 ±50 m 移動平均（整條平滑一次；缺 `<ele>` 的點保持缺值）。
2. 每個 TRC 分段的起訖里程用 Cairn 的 `indexAtKm` 找最近點，段統計 = 逐步 haversine、平滑海拔的升降、依 Δh 正／負／零分上坡、下坡、持平三桶水平距離（缺海拔的步算持平）。相鄰段的索引首尾相接，所以各段相加 = 全程。
3. 解 `munter-split`：時間對 1/upEph 是齊次的，所以先搜 `r = downEph / upEph`（0.2–10，對數格點再細化），每個 `r` 用「全程 Cairn 時間 = TRC 全程移動時間」定出 upEph，取各段誤差平方和最小的 `r`；分不出來時取 Cairn 課本比例 2.5。結果夾進 Cairn 表單範圍 0.5–40，四捨五入到 3 位。

因此用 `fallback` 走全程，時間與 TRC 移動時間差 ≤ 1 %（測試；未夾值時實際約 0.01 %）。每天、每段仍有誤差（兩個參數表達不了日內疲勞、海拔、背包、夜間），印在 `fallback_check`。

TRC 的 EP/h（`km + 爬升/100`）**不能**直接填 Cairn 的 EPH（Cairn 下降也計），所以只給這組反解值。

## Cairn 端的驗證規則

匯入或使用曲線時，依序：

1. **結構**：`v === 1`、`source === "trailruncoach"`、`points` 至少 2 點、第一點 `[0, 0]`、`m` 嚴格遞增、`cum_moving_s` 有限且不遞減、`fallback.kind === "munter-split"` 且兩個 EPH 是正數。任一不合 → **拒絕整份**（不是退回 fallback）。
2. **同一份 GPX（hash）**：行程目前 GPX 原始檔的 SHA-256 ≠ `gpx_sha256` → 這條路線**不用曲線**，全部走 `fallback`，畫面標出「GPX 換過了，用的是 TRC 的等效腳程」。
3. **總長 1 %**：Cairn 自己量的總長 `L`（同上量法）與 `total_m` 差超過 1 %（`|L − total_m| / total_m > 0.01`）→ 同 2，走 `fallback`。在 1 % 內時查表前把里程正規化：`m' = km × 1000 × total_m / L`。
4. **方向**：一段路 `fromM > toM`（沿軌跡倒著走，往返路線的回程）→ 這一段走 `fallback`（用 Cairn 照常的 `directedSegmentStats` 算 munter-split），並標出原因。曲線只描述順著軌跡走的時間。
5. **範圍**：`fromM`、`toM` 夾在 `[0, total_m]`；整段落在範圍外 → 走 `fallback`。
6. 每段移動分鐘 = `(T(toM) − T(fromM)) / 60`，其餘（停留、出發時刻、5 分進位、跨午夜）照 Cairn 現行推估鏈。
7. **出發時刻提醒**：Cairn 某天的出發時刻與 `start_times[day-1]`（缺就用最後一個）差超過 1 小時 → 提示「時間是照 TRC 的 HH:MM 出發算的，可以回 TRC 重算」；`null` 不提示。
8. **分日點提醒**：Cairn 的過夜點里程與 `day_splits_km` 差超過 1 km → 提示（TRC 的日內疲勞會在錯的地方歸零）。
9. `rest_hint` 只顯示，**不寫入** `rest_minutes`。
10. 配速表（`paceReadout`）在 `curve` 下印 `fallback` 的表，註明「TRC 曲線的等效腳程」。

## 範例

一條 12 km、兩天的百岳行程（測試用的合成路線）：

```json
{
  "v": 1,
  "source": "trailruncoach",
  "gpx_sha256": "508c73a60cc02897ff9445b37f1c6ada7856aec530359a21d373c99eb20cfaf1",
  "total_m": 12000.0,
  "day_splits_km": [6.0],
  "start_times": ["05:00", "04:30"],
  "points": [[0, 0], [2000.0, 3000.0], [4000.0, 6600.0], [6000.0, 10600.0],
             [8000.0, 13100.0], [10000.0, 15100.0], [12000.0, 17500.0]],
  "fallback": {"kind": "munter-split", "upEph": 5.5, "downEph": 6.386},
  "fallback_check": {
    "trc_s": 17500, "fallback_s": 17501, "total_err_pct": 0.01,
    "days": [{"day": 1, "trc_s": 10600, "fallback_s": 10430, "err_pct": -1.6},
             {"day": 2, "trc_s": 6900, "fallback_s": 7071, "err_pct": 2.5}],
    "rmse_s": 335.3, "clamped": false
  },
  "rest_hint": {"basis": "moving_ratio",
                "days": [{"day": 1, "rest_s": 2650, "range_s": null},
                         {"day": 2, "rest_s": 1725, "range_s": null}]},
  "computed_at": "2026-10-15T20:10:00",
  "model": "baiyue/capacity"
}
```

例：Cairn 第 1 天從 0 走到 3 km：`T(3000) = 3000 + (6600 − 3000) × 0.5 = 4800 s` → 80 分鐘移動；加上 Cairn 每點自己的停留。

## Golden 值

TRC 的 Cairn 公式實作跟 Cairn 本身比對過：用 Cairn 的 `directedSegmentStats` ＋ `paceMinutes`（2026-10-15 的 `v2`）對一條 80 點、含缺海拔的固定軌跡印出段統計與分鐘數，寫在測試 `GOLDEN`；TRC 算出的差 < 1e-6（要求 ±1 分）。Cairn 改了公式要重產這組值。
