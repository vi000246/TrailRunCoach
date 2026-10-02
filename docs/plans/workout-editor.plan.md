# Plan: 結構化課表編輯器（區段、目標下拉、推到 COROS 預覽）

> **For agentic workers:** `/prp-implement` → Type=feature → `implementing-features` skill。
> 這份是研究＋設計文件；S0 之前不改 app code。互動 mockup：`docs/plans/workout-editor-mockup.html`（純靜態）。

## Summary

現在一堂課只有三段文字：`title`、`target`（目標）、`detail`（備註）。推到 COROS 時
`backend/sync/coros_workouts.py` 用 regex 把文字「猜」回步驟，**目標類型是寫死在各課別的規則裡，不是看文字**。
所以畫面上寫「功率 … · 心率 …」，手錶上卻是心率。

這份計畫在 session 上加一個結構化的 `steps` 欄位（區段 → 重複區塊 → 每段的時間/距離與目標），
課表頁加一個編輯器：上方是強度隨時間的區段圖，下方是可拖拉、可新增/刪除/複製、
可設 ×N 重複的步驟清單。每段都有「功率 / 心率 / 配速 / 無」下拉選單，數字依目前門檻自動填，
可手動覆寫。推送前先看「推到手錶會長這樣」。

## User Story

身為用 COROS 手錶、山路和平路都跑的跑者，我想在課表頁看到並調整每堂課的區段
（暖身、每趟、休息、緩和），每段選要用功率還是心率，數字自動帶入；
這樣推到手錶的就是我看到的，不會悄悄變成另一種目標。

## Metadata

- **Module**: plan / coros-sync / schedule UI
- **Type**: feature
- **Size**: L
- **Rigor**: standard
- **Depends on**: `feat/interval-library`（S3：`variant_key` 等欄位、`interval_library.steps()`）——要先 merge 才做 S2
- **Research hook**: `docs/research/vo2max-gate-and-trail-metric.md`（寫這份時**還不存在**；見 §3.2.4 的 hook）

---

## 1. 現況：文字目標怎麼變成 COROS 步驟

### 1.1 儲存

- `plan_sessions` 表（`backend/db/models.py:150-183`）：`target`、`detail` 都是 `Text`，沒有結構。
  `protocol` 只給 CP 測試用。
- `backend/engine/plan_store.py:25` `EDITABLE = (day, kind, title, minutes, target, detail, terrain, distance_km, climb_m)`；
  `edit()`（:237）改任一欄就設 `edited=True`（:263）。之後 reconcile 規則 2/3
  （`backend/engine/reconcile.py:17-22`）就不再用產生器的結果覆蓋它。
- `reconcile.FIELDS`（`reconcile.py:44`）是產生器→儲存列複製的欄位。
- `push_dict()`（`plan_store.py:352-357`）把儲存列轉成 coros_workouts 要的 dict，只帶
  `kind / title / minutes / target / detail / source / protocol`。
- `feat/interval-library` 分支（commit `12ab217`）加了 `variant_key / rung_key / equiv / swap / swap_reason /
  variant_reps / variant_blocks / variant_adj`，`push_dict` 也帶上前四個 variant 欄位，
  註解說 COROS 會改從 variant 自己的步驟建（`coros_workouts._variant_steps`，**該分支上還沒寫**）。

### 1.2 課表頁怎麼編輯

`backend/static/schedule.html:434-481` 的 `<dialog id="sd">`：類型、日期、分鐘、地形／同負荷換算，
**目標是一個 200 字的文字框**（:467），備註是 textarea（:469）。提示文字（:472）寫明
「強度課的標題寫成『3×10 分』這種格式，推送到 COROS 才會有間歇結構」，也就是說結構完全靠標題字串。
`tgtHint()`（:1186-1195）會建議「依門檻建議」的文字；`autoText()`（:1146-1152）在地形不是路跑時
把含「功率」的目標整段換成心率文字。日曆頁只有這一頁（overview 頁沒有編輯功能）。

### 1.3 文字 → COROS 步驟（`backend/sync/coros_workouts.py`）

`session_workout()`（:358）→ `session_steps(s, th)`（:233-272）依 `kind` 分派：

| kind | 建構函式 | 目標怎麼決定 | 讀不讀 `target` 文字 |
|---|---|---|---|
| easy | :261-271（含 `N×M 秒` 衝刺 :262-270） | **永遠** `easy_hr()`：心率，上限 AeT | 不讀 |
| easy＋熱適應 | :256-260 | 永遠心率 | 不讀 |
| long / mountain / hike | :254-255 | **永遠** `easy_hr()` 心率 | 不讀 |
| quality | `_quality_steps` :145-168 | 預設功率（`_FRAC` 的 threshold / supra，或 detail 裡的 `lo–hi% CP`）；**只有** target 文字有「心率 X–Y bpm」且**沒有**「功率」兩個字時改心率（:161-164） | 只看這兩個關鍵字 |
| test（CP） | `_test_steps` :171-197 | 全力段**無目標**（open），暖身/休息/緩和心率 | 只讀「休 N 分」 |
| test（AeT） | `_aet_test_steps` :200-230 | UA/Evoke：固定功率 ±3%；徐國峰 90 / Friel：心率 | 讀「固定功率 N W」「心率從 N」 |

`easy_hr()`（:124-131）：上限 = AeT（沒有就 0.89×LTHR），下限 = 0.75×LTHR，至少差 10 bpm。
`power()`（:134-137）沒有 CP 就回 `None` → 那段沒有目標。

#### 為什麼文字寫功率、手錶卻用心率

1. **輕鬆跑／長跑的目標文字本來就同時寫心率和功率**：`overview._targets()`
   （`backend/engine/overview.py:377-391`）對每種課都把兩個數字串起來，例如長跑
   `target=tgt.get("long")`（:652）＝「心率 < 1xx bpm · 功率 A–B W」，輕鬆跑（:692）也一樣。
   但 `session_steps` 對 easy / long / mountain / hike（:254-255、:261-271）**完全不看 target**，
   一律送 `("hr", lo, AeT)`。畫面上的功率只是說明，從來沒有送出去。
2. **山路的規則在三個地方，而且彼此不一致**：
   - `zones.WORKOUT_TARGETS`（`backend/engine/zones.py:253-255`）的「trail」把**功率**標為主要指標；
   - `plan_prefs._terrain_long()`（`backend/engine/plan_prefs.py:302-318`）山路長跑把 target 改成只有心率；
   - 課表頁 `autoText()`（`schedule.html:1150`）非路跑時把含「功率」的目標整段換成心率；
   - 推送端不管上面哪個，一律心率（`coros_workouts.py:255`）。
3. **間歇的選擇靠「有沒有『功率』兩個字」**（`coros_workouts.py:162-163`）：
   - 課表偏好「間歇目標 = 心率」（`plan_prefs.py:397-398`）會把 target 截成只剩心率那段
     → 推心率；
   - Zone 3 間歇（`backend/engine/quality_gate.py:1159-1162`）target 只寫心率 → 推心率；
   - 其他間歇 target 是「功率 … · 心率 …」（:1163-1171）→ 因為含「功率」，推功率；
   - 使用者手改目標文字，只要刪掉「功率」或寫成「心率 150-160bpm」以外的格式，結果就跟著變，
     而且畫面上不會提示。
4. **沒有 CP 時**（`power()` 回 None）間歇主課**沒有目標**，畫面上卻可能寫著「RPE 8（95–101% CP）」。

結論：目標類型是「課別 × 關鍵字 × 有沒有門檻」三個條件決定的，使用者看不到、也選不了。

### 1.4 已經存在的步驟結構

- `coros_workouts.Step(kind, seconds, intensity, name)`、`Repeat(sets, steps, name)`（:93-105）——
  只有**一層**重複；`build_program()`（:304-341）把 Repeat 寫成 `exerciseType 0 / isGroup true / sets N` 的群組，
  子步驟用 `groupId` 掛上去。時間型（targetType 2）或 open（1），沒有距離型。
- 間歇：`_quality_steps` 產生 暖身 → Repeat(N, [主課, 休息]) → 緩和。
- 衝刺：輕鬆段 → Repeat(N, [M 秒衝刺 無目標, 60 秒走下來])。
- CP 測試：`cp_protocols.TABLE`（`backend/engine/cp_protocols.py:65-86`）quick = 暖身 12 → 20 分全力 → 緩和 5；
  standard = 暖身 15 → 12 分全力 → 休 30 → 3 分全力 → 緩和 10。
- AeT 測試：`aet_test.PROTOCOLS`（`backend/engine/aet_test.py:81-102`）xu90 / ua60 / ua40 / evoke60 / friel 的 warm/main/cool 分鐘。
- 間歇庫（`feat/interval-library`：`backend/engine/interval_library.py`）：`Variant`（reps、work_s、rest_s、
  rest_mode、lo/hi ×CP、pattern 金字塔、sets/set_rest_s 30/15）、`blocks()` 暖身分段（市區 10 分、drill、strides）、
  `steps(v, level)` 回傳**攤平的** `[{kind: warm|work|rest|cool, s, lo, hi, text}]`。

---

## 2. 其他平台怎麼做

| 平台 | 結構 | 時長 | 目標 | 重複 | 其他 |
|---|---|---|---|---|---|
| TrainingPeaks Structured Workout Builder | 拖拉區塊：Warm up / Active / Recovery / Cool down / 兩步、三步重複 / Ramp Up / Ramp Down | 時間、距離 | **整堂課選一個主要指標**：% FTP、% 最大心率、% 閾值心率、% 閾值配速、RPE；依運動員門檻自動換算 | 兩步/三步重複；多步重複是使用者許願項目 | Ramp 是分段階梯，不是連續 |
| intervals.icu | 文字語法＋即時圖形，兩邊互相同步 | `10m`、`30s`、`2km`、`500mtr` | `95-105%`（FTP）、`220w`、`Z2`、`95% LTHR`、`Z2 HR`、`60% Pace`、`5:00/km Pace` | `Main Set 5x`；**不支援巢狀重複** | `ramp 50%-75%`、`freeride`、步驟前的文字成為提示 |
| Garmin Connect | 步驟類型：暖身 / 跑 / 恢復 / 休息 / 緩和 / 其他 | 時間、距離、**按圈結束**、卡路里、心率 | 無、配速、心率（區或自訂）、踏頻、速度、功率 | Add a Repeat，次數上限 99 | 一堂最多 50 步；拖曳排序 |
| COROS Training Hub | Warm Up / Training / Rest / Cool Down，加上 Interval Training 群組 | 時間、距離、Training Load、Open；Trail Run 多 Elev. Gain | 見 §3.4 | 群組 × sets | 每段 `name` 顯示在手錶 |
| Stryd（PowerCenter / app） | 依 CP 與功率曲線產生；有 Workout Builder | 時間、距離 | 功率（% CP / 區間） | 有 | 2025-03 起可把 Stryd 結構課同步到 COROS 行事曆 |

來源：TrainingPeaks help「Structured Workout Builder」、intervals.icu 論壇「Workout builder syntax quick guide」、
Garmin 教學文（runningwithrock、withamrc）、COROS 欄位見 §3.4、the5krunner 2025-03 Stryd × COROS。
Stryd 結構課同步到 COROS 之後用的是哪種 COROS 目標欄位：**未驗證**。

**共同模型**：Workout → Steps（warm-up / active / recovery / cool-down / rest）→ Repeat 區塊（多數只支援一層）
→ 每段：時長（time / distance / lap button / open）＋ 目標類型（power / HR / pace / none）＋ 範圍
（絕對值，或門檻的 %，或區間編號）＋ 文字提示。整堂課有備註。
差別在「目標類型是整堂一個（TrainingPeaks）」還是「每段自己選（Garmin、COROS、intervals.icu）」。
我們採**每段可選、整堂有預設**：兩邊的好處都拿到。

---

## 3. 設計

### 3.1 資料模型

`plan_sessions` 新增一欄 `steps TEXT`（JSON，nullable）。`None` = 舊資料或產生器還沒輸出結構。

```jsonc
{
  "v": 1,
  "default_target": "auto",          // auto | power | hr | pace | none —— 整堂課的預設
  "origin": "generated",             // generated | template:<variant_key> | user
  "items": [
    {"id": "a1", "kind": "warm", "dur": {"type": "time", "value": 600},
     "target": {"type": "inherit"},  // 跟整堂預設
     "note": "市區輕鬆跑到河濱"},
    {"id": "a2", "kind": "repeat", "times": 5, "items": [
      {"id": "a3", "kind": "work", "dur": {"type": "time", "value": 120},
       "target": {"type": "power", "mode": "pct", "lo": 1.06, "hi": 1.12}},
      {"id": "a4", "kind": "rest", "dur": {"type": "time", "value": 120},
       "target": {"type": "none"}, "note": "走路或極慢跑"}
    ]},
    {"id": "a5", "kind": "cool", "dur": {"type": "open"}, "target": {"type": "inherit"}}
  ]
}
```

- `kind`：`warm | work | rest | cool | other`，加上容器 `repeat`（`times` 1–99）。
- `dur.type`：`time`（秒）、`distance`（公尺）、`open`（按圈結束）。`climb`（累積爬升）留到 COROS
  越野 sportType 驗證之後（§3.4）。
- `target.type`：`inherit | power | hr | pace | none`；`mode`：
  - `zone`：`{"zone": "3B"}`（power = Palladino 區，hr = Friel 心率區或 `"aet"`＝≤ AeT，pace = Friel 配速區）；
  - `pct`：`lo / hi` 是門檻的分數（power ×CP、hr ×LTHR、pace ×閾值配速）；
  - `abs`：`lo / hi` 絕對值（W、bpm、秒/km）＝使用者覆寫，**門檻變了也不跟著動**。
- **存相對值，顯示絕對值**：`zone / pct` 在讀取時用當下門檻換算（`resolve()`），
  所以 CP 更新後課表自動跟上；fingerprint（`coros_workouts.py:370`）是對換算後的 payload 算的，
  門檻一變就顯示「已過期，需重推」，這是現有機制，不用另外做。
- 巢狀：模型允許 repeat 裡再放 repeat 一層（深度 ≤ 2），但 COROS 端只確定一層，見 §3.4。

**向下相容**

1. `steps` 有值時，`target` / `detail` 改成**由 steps 產生的摘要**（`steps_text()`），
   日曆 chip（`schedule.html:667`）、AI context、`trim_quality`、`done_titles` 照樣讀得到文字；
   標題保留 `N×M 分` 的格式，舊 parser 不會壞。
2. `steps` 是 `None` 時行為與今天完全一樣：`session_steps()` 照舊解析文字。
3. 推送：`session_steps()` 第一行改成「有 `steps` 就用 `steps_to_coros()`」，否則走舊路。

**產生器怎麼接**

| 來源 | 產出 |
|---|---|
| 間歇庫 `interval_library.steps(v, level)` | `from_variant()`：把攤平的 work/rest 壓回 `repeat`（相同 work_s / rest_s 的連續對）；金字塔（pattern）保持攤平；30/15 = repeat(sets, [repeat(reps, [work, rest])]) 再視 COROS 能力攤平；`origin = template:<key>`、`target = pct lo/hi` |
| CP 測試 `cp_protocols.TABLE` | `from_cp_protocol()`：全力段 `target none`、`note "全力、配速平均"`；休息 `hr aet` |
| AeT 測試 `aet_test.PROTOCOLS` | `from_aet_protocol()`：UA/Evoke 主段 `power abs ±3%`；徐國峰 90 / Friel `hr aet` |
| 輕鬆 / 長跑 / 健行 / 熱適應 / 衝刺 | `from_easy()`：一段 `work`（或暖身/主/緩和），`target inherit`，`default_target` 由 §3.2.3 決定 |

**舊資料遷移**：不批次改 DB。
- API 讀到 `steps=None` 的課時，用 `legacy_to_steps(s, th)`（包住現有 `session_steps()`，
  把 `Step/Repeat` 轉成新模型）回傳 `steps_derived` 加 `derived: true`；編輯器顯示它，
  使用者第一次按儲存才寫進 `steps`。
- 未來的課由產生器直接帶 `steps`（reconcile 規則 2 會在下次 reconcile 覆蓋未編輯的 auto 課，
  所以本週之後的 auto 課自然換成新格式）。
- 選配：`backend/scripts/backfill_steps.py --dry-run`，只印差異，不寫 DB。

### 3.2 目標選擇

#### 3.2.1 每段的下拉

`[功率 ▾] [區間 3B ▾ | % 門檻 | 自訂數字]  248–262 W（95–101% CP）`

- 類型：功率 / 心率 / 配速 / 無 / 跟整堂預設。
- 填法：
  - **區間**：功率用 Palladino 1A–7（`zones.PALLADINO_POWER_ZONES`，`zones.py:15-26`），
    心率用 Friel 1–5c（`zones.FRIEL_HR`，:53-61）外加「≤ AeT」，配速用 Friel 配速區（:63-71）；
  - **% 門檻**：兩個數字（% CP、% LTHR、% 閾值配速）；
  - **自訂數字**：直接輸入 W / bpm / 分:秒。
- 不管哪種填法，**右邊永遠顯示換算後的絕對數字**和它落在哪一區。
- 從區間或 % 改成自訂數字時，自動帶入目前的換算值，再讓使用者改。
- 輸入框旁有「↺ 回到建議值」。

#### 3.2.2 自動填數字

`GET /api/v1/overview/plan/targets/zones`（新）回傳目前門檻和三套區間的絕對值：

| 指標 | 門檻來源（現有） | 缺門檻時 |
|---|---|---|
| 功率 | `thresholds.cp`（`overview.py:890`，來自 `zones.training_targets`） | 下拉裡「功率」變灰並寫原因；這段自動退到心率，顯示 ⚠ |
| 心率 | `lthr`、`aet`（同上；LTHR 若仍是 WKO5 預設值就標「預設值」） | 退到「無」 |
| 配速 | `thresholds.estimate_tpace()`（`backend/engine/thresholds.py:227`，推估） | 不提供 |

各課別的建議值沿用 `zones.WORKOUT_TARGETS`（`zones.py:248-262`）和 `interval_library` 的 lo/hi。

#### 3.2.3 整堂預設＋每段覆寫

決定順序（由高到低）：

1. 這段自己的 `target.type`（不是 `inherit`）；
2. 整堂的 `default_target`（不是 `auto`）；例如使用者把這堂設成「山路用心率」；
3. `auto` 規則 `target_policy(session, prefs)`：
   - 地形是 trail / hike → 心率（與現在 `plan_prefs._terrain_long`、`schedule.html:1150` 一致）；
   - quality → 課表偏好 `interval_target`（`plan_prefs.py:118`，power / hr）；
   - easy / long 路跑 → 心率（≤ AeT），與現在推送一致；
   - 全力測試段 → 無；
4. 缺門檻時退一級（power → hr → none），在步驟旁顯示 ⚠ 和原因。

`auto` 的結果在 UI 上明確寫出來，例如「跟整堂預設：心率（山路自動）」，不再暗中決定。

#### 3.2.4 山路指標 hook

`docs/research/vo2max-gate-and-trail-metric.md` 寫這份時還不存在。先把山路規則集中到一個函式：

```python
# backend/engine/workout_steps.py
def target_policy(session: dict, prefs, step_kind: str) -> str:
    """auto → power | hr | pace | none. 山路規則待 vo2max-gate-and-trail-metric.md：
    目前 trail / hike = hr（plan_prefs._terrain_long）。"""
```

研究文件完成後只改這個函式（和 `zones.WORKOUT_TARGETS` 的 trail primary 是否保留「功率」），
其他地方都呼叫它。§1.3 第 2 點的三處不一致也一起收斂到這裡。

### 3.3 編輯器 UI

位置：課表頁的課程對話框（`#sd`）。強度課、測試、長跑預設展開「結構」區；輕鬆跑收合，只有一行摘要。
桌機對話框寬度從 460 px 改成 `min(880px, 96vw)`；手機維持現有的底部抽屜（`schedule.html:371`）。

**(a) 區段圖（主視圖）**

- 水平 = 時間（open 段用固定寬度＋斜線紋理，標「按圈」）；垂直高度 = 強度（換算成 % CP 等效，
  心率段用 Friel 區換算到 Palladino 區的近似高度，推估）；顏色 = 區間。
- 顏色：區間是**有序**的，用單一藍色色階（dataviz 參考色板 sequential blue；淺色模式 Z1 `#86b6ef` → Z5 `#104281`，
  深色模式 `#184f95` → `#9ec5f4`；兩組都用 `validate_palette.js --ordinal` 驗過，全部 PASS）。
  休息 / 無目標 = 中性灰（`--bar`）。高度和顏色重複編碼，所以不靠顏色也看得出強度。
- 重複區塊上方畫括號標「×5」；狀態色（`--bad` 等）只留給驗證警告。
- hover / 點：tooltip 顯示「第 3 趟 · 2:00 · 功率 296–313 W（106–112% CP，Z5）」；點區塊選中下方對應步驟，反之亦然。
- 圖下方一行：總時間、Z3 以上時間、TSS 估計、單日上限。

**(b) 步驟清單**

- 每列：拖曳把手 ⋮⋮、類型（暖身/主課/休息/緩和/其他）、時長（時間 mm:ss / 距離 km / 按圈）、
  目標類型下拉、填法、範圍、換算後絕對值、⋯ 選單（複製、刪除、包成重複、移出重複）。
- 重複區塊是一張卡片：標頭 `× [5] ±`，裡面是子步驟，可以拖進拖出。
- 工具列：＋ 步驟、＋ 重複、**插入範本**、整堂預設目標下拉。
- 插入範本：依間歇庫的 rung 分組（Z3 T1–T3、T+、Z5 V1–V4），每個 variant 顯示結構、總時間、
  是否 canonical / 等效；插入後就是一般步驟，可以再改。也有 CP 測試、AeT 測試、衝刺範本。
- 鍵盤：方向鍵移動選取、Alt+↑/↓ 搬移、Delete 刪除；拖曳之外也有「上移/下移」按鈕（無障礙與手機）。

**(c) 驗證（即時，顯示在圖下方）**

| 規則 | 等級 | 來源 |
|---|---|---|
| Z5（band 中點 ≥ 1.02 CP，`interval_library.CLASS_RANGE`）每趟 ≥ 2 分 | 錯誤（可強制儲存） | 台灣教練，`Z5_MIN_REP_S` |
| Z5 休息 ≤ 最短一趟且 ≤ 3 分 | 警告 | Buchheit；`Z5_MAX_REST_S` |
| Z3 每趟 ≥ 3 分（或連續） | 警告 | Haugen 2022 下緣，`Z3_MIN_REP_S` |
| 總時間 ≤ 平日 / 長天上限（`Prefs.cap_weekday` / `cap_long`；`cap_mode` soft = 警告、hard = 錯誤） | 依 cap_mode | 課表偏好 |
| 選了功率卻沒有 CP（心率、配速同理） | 錯誤 | — |
| lo > hi、功率 < 40% 或 > 200% CP、心率 > 1.1×LTHR | 錯誤 | 推估 |
| 重複深度 2、步驟數 > 50 | 警告「COROS 可能不支援，推送時會攤平」 | 未驗證（§3.4） |
| 結構跟這個 rung 的範本不等效（`interval_library.equivalent`） | 資訊「這堂不算進階」 | §C2 |

- **總時間**：Σ 時間段 × 重複；open 段用 0 並標「不含按圈段」；距離段用目標配速或近 4 週平均配速換算（推估）。
- **TSS 估計**：Σ 秒 × IF² × 100 / 3600，IF = 目標中點 ÷ CP；心率段用 `zones.FRIEL_HR` → 對應 Palladino 區中點；
  休息段 IF 0.6；全力測試段沿用 `cp_protocols.TABLE` 的 TSS（推估，跑步 rTSS 與這個公式的差距未驗證）。
  分鐘欄改成唯讀、由步驟算出（同負荷換算的 `eq-tss` 仍可用在輕鬆/長跑）。

**(d) 手機**

- 區段圖全寬、高 96 px，可橫向捲動不會出現（圖依寬度縮放）。
- 清單每列變成兩行卡片：第一行「類型 · 時長」，第二行「目標 · 絕對值」；點一下開底部抽屜編輯該段。
- 拖曳用把手（touch）＋上下移按鈕；工具列固定在抽屜底部。

### 3.4 COROS 對應

已確認的欄位（現有程式 `coros_workouts.py:22-27, 56-63`，加上 dholliday3/coros-training-mcp 的
`docs/enums/traininghub-live-builder-catalog.json`——那份是自動操作 Training Hub 編輯器、攔截
`training/program/calculate` 草稿 payload 得到的）：

| 我們的模型 | COROS 欄位 | 狀態 |
|---|---|---|
| warm / work / rest / cool | `exerciseType` 1 / 2 / 4 / 3；`overview` sid_run_warm_up / training / rest / cool_down | 已在用 |
| other | 2（Training），名稱寫 note | 推估 |
| repeat ×N（一層） | `exerciseType 0, isGroup true, sets N`，子步驟 `groupId` | 已在用 |
| repeat 巢狀（深度 2） | — | **未驗證**；推送時把外層展開成 N 個內層群組 |
| dur time | `targetType 2`，`targetValue` 秒 | 已在用 |
| dur open | `targetType 1` | 已在用 |
| dur distance | `targetType 5`，`targetValue` 公分 | 欄位已確認（catalog：160934 = 1 英里），本 app 未送過 |
| dur climb | Trail Run 編輯器有「Elev. Gain」 | **未驗證**：程式 sportType 目前送 1（跑步），越野的 program sportType 值不知道 |
| target power | `intensityType 6`，`intensityValue / Extend` = W，`isIntensityPercent false` | 已在用；Run 編輯器的選項只有絕對「Power」，**沒有 % CP**，所以一律送換算後的瓦數 |
| target hr | `intensityType 2`，`hrType 3`（% LTHR），送 bpm＋`intensityPercent`（% × 1000） | 已在用；catalog 另有 hrType 1（% 最大心率）、2（% 儲備心率） |
| target pace | `intensityType 3`，`intensityDisplayUnit`，值的單位 | **未驗證**：程式註解寫 ms/km，catalog 的範例數值（186411–223694、417000–452000）和顯示單位對不起來；S8 先手動送一堂測試課再讀回 detail |
| target pace（坡度修正） | `intensityType 8`「Effort Pace」 | 存在；手錶怎麼算未驗證，不在 v1 |
| cadence | `intensityType 7` | 存在；v1 不開放 |
| none | `intensityType 0` | 已在用 |
| 步驟提示 | `name`（手錶顯示）；整堂 `overview` ≤ 200 字（程式截斷） | name 長度上限未驗證 |

COROS 表示不了的東西（UI 要先講）：

- **ramp（漸進）**：沒有這種步驟。編輯器不提供 ramp；範本裡的「漸進」寫在 note。
- **主目標＋次要上限**（例如「功率 75–88% CP、心率不超過 AeT」）：每段只有一個 `intensityType`，
  上限只能寫在 `name`。推送預覽會標「心率上限只顯示文字，手錶不會提醒」。
- **% CP**：Run 只有絕對瓦數，所以 CP 變了要重推（fingerprint 會自動把它標成過期）。
- **巢狀重複、50 步以上**：未驗證，推送時攤平並在預覽說明。
- 力量課、被動熱適應、比賽、休息：照舊不推（`coros_workouts.py:237-242`）。

**推送預覽「推到手錶會長這樣」**

- `GET /api/v1/overview/plan/sessions/{uid}/coros-preview`：呼叫 `session_workout()`（不打 COROS），
  把 payload 轉成清單：`1 暖身 10:00 心率 118–150`、`2 間歇 ×5：2:00 功率 296–313 W / 2:00 無目標`……
  再附 `lost`：哪些東西在轉換時被丟掉或攤平（ramp、次要上限、巢狀）。
- 對話框裡「推送這天到 COROS」旁邊加「預覽手錶」；推送週的確認視窗（`schedule.html:928` `push()`）每堂課也可展開看。

### 3.5 跟自動調整、進階的關係

- **編輯結構 = 使用者編輯**：PATCH `steps` 走 `plan_store.edit()`，照樣設 `edited=True`
  （`plan_store.py:263`），`steps.origin = "user"`。reconcile 規則 3 保留它；
  如果那週變成休息週，edited 的 quality 仍會被 superseded（現有規則，不改）。
- `adapt.py` 不動 edited 課（`backend/engine/adapt.py:11, 126-128`）。所以規則 D2 / E 想把強度降一級時，
  edited 課不會被改；新增：在該課上顯示建議 chip「系統建議降成 V1（疲勞）：套用 / 忽略」，按套用才改（S7 之後的選配）。
- 門檻改變：`zone / pct` 自動跟著換算（不算使用者編輯）；`abs` 不變。
- **進階判斷讀步驟，不讀標題**：`quality_gate.dose_step` 在 `feat/interval-library` S3 已改成讀 `variant_key`。
  再加一層：
  1. `steps.origin == template:<key>` 且結構沒被改 → 用那個 `variant_key`；
  2. 被改過 → `variant_from_steps(steps)` 建一個臨時 `Variant`（reps、work_s、rest_s、lo/hi），
     用 `interval_library.equivalent(v, canonical(rung))` 判斷；等效 → `equiv=True, swap="user"`；
     不等效 → 這堂照樣顯示評語，但不推動階梯（與 S3 的「縮量版」同規則）；
  3. 找不到 work 段或目標不是功率（例如整堂改成心率）→ 用 `CLASS_RANGE` 的心率對應推估等級，標「推估」。
- `done_titles()`（`plan_store.py:370`）之後可以改讀 steps，但不急：標題仍保留 `N×M 分`。

---

## 4. 實作階段

估時是推估（一人、含測試）。

| 階段 | 內容 | 檔案 / 函式 | 測試 | 估時 |
|---|---|---|---|---|
| **S0** 模型 | 新模組：schema、`validate_steps()`、`resolve(steps, th)`、`steps_text()`、`total_s()`、`tss_estimate()`、`legacy_to_steps(s, th)`（包 `session_steps`）、`steps_to_coros(steps, th) -> list[Step|Repeat]`、`target_policy()` | `backend/engine/workout_steps.py`（新） | `tests/test_workout_steps.py`：round-trip、驗證規則、換算、**每種現有課別 legacy → steps → COROS payload 與現在完全相同**（golden） | 1–1.5 天 |
| **S1** 儲存 | `steps` 欄位＋migration；`to_dict / _fill / EDITABLE / _clean`；`reconcile.FIELDS`；`push_dict` 帶 steps；`session_steps()` 先看 steps | `db/models.py`、`db/database.py:_migrate_schema`、`engine/plan_store.py`、`engine/reconcile.py`、`sync/coros_workouts.py` | `test_plan_store.py`（PATCH steps → edited）、`test_coros_workouts.py`（steps 優先、fingerprint 隨門檻變） | 1 天 |
| **S2** 產生器 | merge `feat/interval-library` 後：`from_variant / from_cp_protocol / from_aet_protocol / from_easy`；`interval_library.session_for`、`cp_protocols.session_for`、`aet_test.session`、`overview` 的 easy/long 都帶 `steps` | `engine/workout_steps.py`、`interval_library.py`、`cp_protocols.py`、`aet_test.py`、`overview.py` | golden：產生器的 steps 推出去與舊路徑相同（除了刻意修正的 §1.3 不一致） | 1–1.5 天 |
| **S3** 目標 | `default_target`；`target_policy` 收斂三處山路規則；`GET /targets/zones`；缺門檻退級＋原因；長跑/輕鬆的 `target` 文字改由 steps 產生（不再寫推不出去的功率） | `workout_steps.py`、`api/plan_sessions.py`、`plan_prefs.py`、`overview._targets` | 各課別×地形×偏好的目標類型表格測試 | 1 天 |
| **S4** API | PATCH `steps`（伺服器端驗證，錯誤回 422 帶清單）；`GET /sessions/{uid}/coros-preview`；`steps_derived` | `api/plan_sessions.py` | API 測試（假 DB，不碰真 DB、不開 8000） | 0.5 天 |
| **S5** UI 唯讀 | 對話框加區段圖＋步驟清單（顯示 `steps` 或 `steps_derived`）＋手錶預覽 | `static/schedule.html`（或拆 `static/workout_editor.js`） | Playwright：開一堂間歇看到 ×N、色塊、tooltip | 1 天 |
| **S6** UI 編輯 | 行內編輯、目標下拉與自動填、新增/刪除/複製、重複卡、拖曳＋上下移、插入範本、即時驗證、總時間/TSS、手機抽屜 | `static/workout_editor.js`、`schedule.html` | Playwright：改 Z5 為 90 秒 → 錯誤；拖曳後順序；手機寬 390 px 截圖 | 2–3 天 |
| **S7** 進階 | `variant_from_steps()`；`dose_step` 對 `origin=user` 的課做等效判斷；adapt 建議 chip（選配） | `quality_gate.py`、`interval_library.py`、`adapt.py` | 改過的等效結構推動階梯；不等效的不推 | 1 天 |
| **S8** 配速 / 距離 | 送一堂 pace＋distance 測試課到 COROS、讀回 detail 確認單位（手動、名稱 `TRC TEST`、用完刪）；確認後開放配速目標與距離段 | `coros_workouts.py` | 單位測試＋一次人工驗證紀錄 | 0.5 天＋人工 |

合計約 9–11 天（推估）。S0–S1 可以在 interval-library merge 前先做；S2 之後依賴它。

## 4.1 實作狀態（2026-10-02，`feat/workout-editor`）

- S0–S7 完成：`engine/workout_steps.py`、`api/plan_sessions.py` 的 `/steps/*`、`static/workout_editor.js`。
  整堂的目標仍是既有的「目標用：自動／心率／功率」（`target_basis`），沒有另外的 `default_target`；
  每段的 `target.type = auto` 依它換算，點某一段的目標可以只改那一段。
- 重複多一個 `last_rest`（最後一趟不休息）：間歇庫的課都是這樣，COROS 群組做不到，推送時攤平成一段一段，
  和原本 `_variant_steps` 送的完全一樣（golden 測試）。
- S8 **沒有做人工驗證**（不呼叫 COROS live）：距離段照 catalog 送 `targetType 5`、公分，預覽標「未驗證」；
  配速目標可以在編輯器設，但推送時那段不設目標（配速寫在步驟名稱），預覽也標出來。等送一堂 `TRC TEST`
  讀回 detail 確認單位後，再把 `workout_steps._one` 的配速接上 `intensityType 3`。

## 4.2 範本、類型、總時間（2026-10-02，`feat/editor-templates`）

- **類型**：課表對話框只剩路跑和越野跑兩種地形；「健行／登山」改名為「越野跑」，kind 還是 `hike`，沒有做資料遷移。
  「被動熱適應」不再出現在類型選單；已經存在的這類課打開時，還是會顯示自己那個選項。
  地形的「登山」選項拿掉了，舊的 `terrain = hike` 會當成越野跑打開。
  沒有動的部分：課表偏好的「長跑地形：登山」選項、排課器產生的「登山健行（長時間）」和負重課，這些還是 kind `hike`。
- **插入範本**：
  - 按鈕放在工具列最前面，改成主按鈕樣式；摺起來的「結構」標題列上也多一個「範本」，可以直接開。
  - 範本依這堂的類型篩選，分成輕鬆跑、強度課（三區／四區／五區）、測試、越野跑。
  - 每一份範本都有小區段圖和出處。預設插入方式是「整份換」。
  - 範本清單、出處和分區規則見 `docs/research/workout-templates.md`。
  - 帶 `hrp`（% LTHR）的功率帶目標：心率模式用 `hrp`，功率模式用 lo/hi × CP。
- **總時間**：
  - 只要有結構，「分鐘」就鎖住（readOnly），數字由步驟加總；點這一格會打開結構。同負荷換算改了時間的話，會改最長的那一段時間。
  - 有距離段或按圈段時，顯示「約（分鐘）」並加一個 ?，說明是用你的輕鬆路跑速度和閾值配速依強度內插；越野跑用 EP 和你的越野 EP 速度；按圈段用 `dur.est`。
  - 原本同負荷面板那行 EP／回測的長說明也移到這個 ? 裡。

## 4.3 每段自己的目標依據（2026-10-02，`feat/editor-templates-2`）

- 課表對話框拿掉整堂的「目標用：自動／心率／功率」，每一段在結構裡自己選用功率、心率或配速。
  - 標「自動」的段（系統排的課 derive 出來的）還是照 `target_policy` 決定。
  - 舊課表存過的 `target_basis` 照樣有效，只是對話框不會再寫進去。
- 範本的每一步都照來源原本的依據（`docs/research/workout-templates.md` §2），不會跟著任何上層設定一起切換。
- 「長跑地形：登山」改成越野跑。存過的 `hike` 會讀成 `trail`，排課器不再產生「登山健行」。
- 負重課改成陡坡健走（`engine/steep_hill.py`）。

## 5. 未決

- COROS 巢狀重複、步驟數上限、`name` 長度、pace 單位、越野 sportType：全部**未驗證**，S8 一起確認。
- 山路主指標（功率還是心率）：等 `docs/research/vo2max-gate-and-trail-metric.md`。
- 心率段在區段圖上的高度換算（Friel 心率區 → Palladino 功率區）是推估，只影響畫面。
- TSS 公式對跑步的誤差未驗證；顯示時加「估」字。
