# 賽事計算機的時間回填疊石 Cairn 行程規劃（SP-570）

2026-10-14，分支 `docs/sp570-cairn-times`（從 `integrate/2026-10-12a` 開出）。**只做調查和提案，兩個 repo 的程式都沒改**；inReachLiveTrack（Cairn）全程唯讀。兩邊的 `.env`、金鑰、token 檔都沒讀。

單據：SP-570（本單）；相關 SP-531（Q5 在 2026-10-14 改成「可以回寫 Cairn」）、SP-537（已做：貼 Cairn 分享連結匯入）、SP-538（SSO 之後的雙向正式接口）、SP-329（SSO）、SP-556（沿線天氣服務）；InReach 地圖的 SP-541、SP-554、SP-183、SP-186。

路徑寫法：`TRC/` 指 `/Users/logan/Projects/TrailRunCoach-wt-sp570/`；`CAIRN/` 指 `/Users/logan/Projects/inReachLiveTrack/`。Cairn 只看 `v2/`（v1 已刪）。

標記：**已驗證**＝讀過程式碼確認；**推估**＝從程式或文件推出來，沒有實際跑過。

---

## 0. 結論先講

1. **不要把計算機的「抵達時刻」直接寫進 Cairn 的 `poi_visits.eta`。** 要回寫的是**腳程**：把 TrailRunCoach（以下簡稱 TRC）算出的「沿線累積移動時間」交給 Cairn，當成 Cairn 腳程模型 `paceMinutes` 的**第五種填法**（暫名 `curve`，時間曲線）。抵達時刻仍然由 Cairn 自己的推估鏈算：它負責每天出發時刻、每個點的停留、過夜、5 分進位、跨午夜不寫回。
   - 這樣 Cairn 的**慣例 43**（同一段路只有一個機器算出來的時間）在精神上仍然成立：時間軸、框選統計、推估鏈、配速表全部還是只問 `paceMinutes`，只是 `paceMinutes` 多了一支「照 TRC 曲線查表」的分支。慣例 43 的條文要改一句，改法見 §2.3。
   - 也跟 SP-531 定下的分工一致：**Cairn 管行程與抵達時間，TRC 管生理模型與配速**。TRC 提供配速，不搶 Cairn 的抵達時間。
2. **不能直接把 TRC 的「EP/h」抄進 Cairn 的「EPH」，兩邊定義不同。**
   - TRC：`EP = km + 爬升/100`（`TRC/backend/engine/racepower/predict.py:198`）。
   - Cairn 的 Munter：`EP = km + 上升/100 + 下降/100`（`CAIRN/v2/app/src/lib/pace.ts:115`）。
   - 例：16 km、↑1200、↓1200，TRC EP/h 3.0 ⇒ 移動 9.3 h。照抄 3.0 到 Cairn，Cairn 會算出 13.3 h，**慢 43 %**；正確的 Cairn EPH 是 4.29。
   - 兩邊的平滑也不同，爬升數字本來就對不上。所以 TRC 給的 EPH 必須**用 Cairn 的定義、在 Cairn 的算法上反解**。
3. **建議三步走：**
   - **A（只改 TRC，現在就能做）**：計算機結果多一張「給 Cairn 的腳程」卡，內容是用 Cairn 的公式反解出的上坡／下坡 EPH、Tobler 係數，以及每天的移動時間，使用者照著填進 Cairn。
   - **B（兩邊都改，不用等 SSO）**：TRC 輸出時間曲線（下載 JSON，或放進 TRC 既有的公開分享快照），Cairn 新增 `curve` 腳程，可以上傳檔案或貼 TRC 分享連結匯入。
   - **C（等 SP-329 SSO，併進 SP-538）**：用服務 token 自動推或拉、重新同步。
4. **精度預期（推估）**：
   - A 只剩兩個參數，會失去 TRC 逐段的差異：日內疲勞、海拔、每天背包重、夜間、熱。整條路線的總時間一樣，但每天、每段會有誤差，卡上會把每天的誤差印出來。
   - B 的曲線把 TRC 的逐段時間原樣帶過去。只要 GPX 是同一份，Cairn 任何一段（兩個點之間）的移動時間就等於 TRC 對同一段的預估。
5. **本專案先做的（不用等 SSO、也不用等 Cairn）**：TRC-1「給 Cairn 的腳程」卡 → TRC-2 時間曲線輸出與規格。Cairn 那邊等使用者有空再做 CAIRN-1、CAIRN-2。SSO 之後的工作併進 SP-538。

---

## 1. Cairn 現在怎麼算行程時間（已驗證）

### 1.1 EPH 的定義與公式

- **唯一入口**是 `paceMinutes`（`CAIRN/v2/app/src/lib/pace.ts:134`），它呼叫 `walkMinutes`（`:98-127`）。四種填法：

  ```ts
  case 'tobler':        (toblerUpMin + toblerFlatMin + toblerDownMin) * coef
  case 'tobler-split':  toblerUpMin*up + toblerDownMin*down + toblerFlatMin*(up+down)/2
  case 'munter':        ((km + ascentM/100 + descentM/100) / eph) * 60
  case 'munter-split':  ((distUpM/1000 + ascentM/100) / upEph)*60
                      + ((distDownM/1000 + descentM/100) / downEph)*60
                      + ((distFlatM/1000) / ((upEph+downEph)/2))*60
  ```

  - **EPH**＝每小時走幾個努力點。努力點＝距離 km ＋ 上升 m/100 ＋ **下降 m/100**。下坡也計高度是使用者定案的「B 案」（`pace.ts:51-57`）。
  - `EPH上 = VAM/100`。兩個 EPH 填同一個值時，`munter-split` 恆等於 `munter`（測試 `CAIRN/v2/app/test/pace.test.ts:67`）。
  - Tobler 函式：`6·exp(−3.5·|s+0.05|)` km/h，坡度夾在 ±1（`CAIRN/v2/app/src/lib/elevation.ts:101-104`）。server 端另有一份同樣的式子，算上傳時的 `gpx_files.est_minutes`（`CAIRN/v2/server/src/lib/gpx-stats.ts:41-44`）。
  - 預設是 `{kind:'tobler', coef:1}`（`pace.ts:74`）。表單範圍：coef 0.2–5、EPH 0.5–40（`CAIRN/v2/app/src/lib/pace-form.ts:59-61`）。
  - 另有一個 `gpxEp`＝`km + 爬升/100`（`CAIRN/v2/shared/src/schema.ts:139-143`），只用在路線清單的顯示與篩選，**不參與估時**。
- **每段的輸入** `PaceBasis`（`pace.ts:82-93`），由 `rangeStats` 一個迴圈算出（`elevation.ts:194-296`）：
  - 距離用 haversine，對原始點逐點累加。
  - 升降用平滑後的海拔。
  - `toblerUp/Flat/DownMin`、`distUp/Flat/DownM` 依**行進方向**的 Δh 分桶：Δh>0 算上坡、<0 算下坡、=0 算持平（`elevation.ts:254-267`）。缺 `<ele>` 的段一律算持平。
  - 行程的一段（leg）用 `directedSegmentStats(from,to)`（`elevation.ts:136`）。
- **休息**只有一個入口：每一次經過的 `rest_minutes`。「每小時休 X 分」已移除（`pace.ts:129-133`）。
- **校準**：已經可以上傳自己走過的 GPX，加上手填的移動時間，反解四種填法的參數（`CAIRN/v2/app/src/lib/pace-calibrate.ts:71` `solvePace`；`CAIRN/v2/app/src/ui/PaceCalibrateDialog.svelte:105-112`）。
  - 一個樣本只解得出一個縮放：`munter-split` 的上下比例固定在課本的 4:10（`pace-calibrate.ts:35`），`tobler-split` 上下相等。

### 1.2 推估鏈（休息、過夜、每天出發）

- `estimateEtas`（`CAIRN/v2/app/src/lib/eta-estimate.ts:168-249`）：
  - 每天各起一條鏈，**唯一錨點是每天的出發時刻**。
  - `clock += leg + rest`。時刻以「當天午夜起算的分鐘」表示。
  - 寫回時 `Math.ceil` 到 5 分（`formatEtaMinutes`，`:48-65`）。超過 24:00 標 overflow、不寫回。
- `buildEstimateChain`（`CAIRN/v2/app/src/lib/eta-plan.ts:139`）：營地在鏈上是兩列，本尊（抵達）加一列合成的 `@dep`（隔天出發）。天的切法看資料（有 `day` 照 day，沒有就看「☾ 在此過夜」）。**否決依時數自動切天。**
- 接線：`legMinutes = (fromKm,toKm) => paceMinutes(directedSegmentStats(pts, indexAtKm(fromKm), indexAtKm(toKm)), cfg)`（`CAIRN/v2/app/src/ui/points/PointsPage.svelte:1159-1173`）。
  - 時間軸（`CAIRN/v2/app/src/lib/timeline.ts:211`）、清單逐段統計（`CAIRN/v2/app/src/lib/row-legs.ts:129`）、配速表 `paceReadout`（`pace.ts:275`）、校準也都呼叫同一支。
- 寫回由 `buildEtaPatches` 組（`eta-plan.ts:513-668`）：
  - 出發列寫 `etaSource:'user'`，推算的列寫 `'estimate'`。
  - 覆寫手填時刻時累加 `overwriteUserCount`，**套用前要顯示**。
  - visits 整批取代，逐顆循序寫入。

### 1.3 存在哪裡（D1）

| 欄位 | 內容 | 出處 |
|---|---|---|
| `gpx_files.pace_config` | TEXT JSON `{ model: PaceModel }`，**腳程掛在路線上** | `CAIRN/v2/shared/src/schema.ts:35-67`、`CAIRN/v2/server/schema_v2.sql:71-77` |
| `users.prefs` | 個人預設腳程，順序是帳號 ▸ 本機 ▸ 內建 | `CAIRN/v2/server/src/routes/me.ts:194-208`、`CAIRN/v2/app/src/lib/pace-default.ts` |
| `gpx_files.start_date` | `'YYYY-MM-DD'` | `schema_v2.sql:61-70` |
| `poi_visits` | `day`（整數，0＝出發前日）、`eta`（`'HH:MM'`）、`distance_m`（這一次經過的里程）、`rest_minutes`、`eta_source`（user/estimate）、`is_segment`、`kind`、`sort_order` | `schema_v2.sql:546-578`、`schema.ts:332-370` |

### 1.4 UI 與 API

- **UI**：
  - `CAIRN/v2/app/src/ui/points/PointsPage.svelte`（腳程與草稿）
  - `PaceEstimatePanel.svelte`（`overwriteUserCount` 警告，`:131-134`）
  - `PointList.svelte`（出發時／分下拉、停留分鐘）
  - `CAIRN/v2/app/src/ui/PaceFields.svelte`
  - `PaceCalibrateDialog.svelte`
  - `TimelineSheet.svelte`
- **API**：**沒有「計算」端點，估時全在前端。** 寫入走這幾支：
  - `PATCH /api/gpx/:id`（含 `pace_config`，`CAIRN/v2/server/src/routes/gpx.ts:2019`、`:2062-2064`）
  - `PATCH /api/pois/:id`（visits 整批取代，`CAIRN/v2/server/src/routes/pois.ts:188`）
  - `POST /api/pois`
  - `PUT /api/me/prefs`
- **沒有任何對外公開的 API，也沒有服務對服務的 token。** 匯出只有下載 GPX 原檔（`CAIRN/v2/app/src/ui/Toolbar.svelte:729-757`），**行程（visits、ETA）沒有 CSV/JSON 匯出或匯入**。

---

## 2. Cairn 慣例 43 與回寫的衝突

### 2.1 原文

`CAIRN/CLAUDE.md:149`：

> 43. **同一段路只有一個機器算出來的時間**（`v2/app/src/lib/{pace,pace-form,eta-estimate,eta-plan,pace-prefs}.ts`）。四種填法都是 `paceMinutes` 的分支（`tobler`／`tobler-split`／`munter`／`munter-split`），時間軸 leg、框選統計、推估鏈、配速表、`paceReadout` 全部只問它。單位（分速、VAM）解決「不知道自己 EPH」，不由模型數量解決。…

子條：

- `CAIRN/CLAUDE.md:151`：「**唯一錨點是每天的出發時刻**（使用者定案）…**手填會被覆寫，`overwriteUserCount` 要在套用前顯示**；寫回 `Math.ceil` 到 5 分；跨午夜不寫回。腳程掛路線（`gpx_files.pace_config`）…」
- `CAIRN/CLAUDE.md:150`：否決紀錄，含「依時數自動切天」。

完整版在 `CAIRN/docs/conventions-archive.md:229-247`。關鍵句（`:229`）：

> 推估功能若另外用 Munter，同一段路會在同一個畫面上出現**兩個機器算出來的不同時間**——這個 repo 的慣例一路在殺的正是這種矛盾。所以**四種填法都是 `paceMinutes` 的分支**…

**怎麼落實**：靠前端結構與測試，例如 `row-legs.test.ts:214`「估時的唯一出處是腳程模型（慣例 43）」。**server 對 `eta_source` 沒有任何業務約束**，只負責存。

⚠️ 順帶發現 Cairn 一處過時的註解：`CAIRN/v2/shared/src/schema.ts:309-317` 的 `EtaSourceSchema` 還寫著「只有 `'user'` 的時刻會錨定推估鏈…使用者手打的東西從來不會被蓋掉」。這是 2026-08-12 被推翻的第一版語意，跟現行慣例 43 矛盾（列為 CAIRN-5）。

### 2.2 直接寫 ETA 會碰到什麼

如果 TRC 把每個點的抵達時刻直接寫進 `poi_visits.eta`（假設新增 `eta_source:'trc'`）：

1. **同一段路出現兩個機器時間**：清單上的時刻是 TRC 的，時間軸 leg、框選統計、配速表仍然是 `paceMinutes` 的。這正是慣例 43 要消滅的矛盾。
2. **下次按「時間推估」就整批被蓋掉**：推估鏈只認每天的出發時刻，其他時刻一律當可覆寫。
3. **休息算兩次或漏算**：
   - TRC 百岳的時鐘時間＝移動時間 ÷ `moving_ratio`，休息隱含在裡面（`TRC/backend/engine/racepower/planner.py:966`、`TRC/backend/engine/racepower/hike.py:193-200`）。
   - Cairn 的休息是每個點明寫的 `rest_minutes`。
   - 兩者疊起來會重複，拆開又對不齊。
4. **天與過夜**：TRC 百岳每天都從同一個 `start_time` 起算（`planner.py:966`，`(cum − day_start)/ratio`）；Cairn 每天有自己的出發時刻。直接寫時刻會把 TRC 的出發假設硬塞給 Cairn。
5. **點對不上**：TRC 的分段依坡度切（Douglas–Peucker），不在 Cairn 的興趣點切。要寫 ETA 得先內插到每一次經過的里程，等於在 TRC 端重做一次 Cairn 的推估鏈。

結論：**回寫「腳程」，不回寫「時刻」。**

### 2.3 建議的慣例 43 修訂（給 Cairn 開單用，不在本單改）

在第 43 條第一句後補：

> 第五種填法 `curve`（外部時間曲線，目前唯一來源是 TrailRunCoach）同樣是 `paceMinutes` 的分支，不是平行的第二套數字：它回答「這一段的移動分鐘＝曲線上 T(終點里程) − T(起點里程)」。曲線綁定一份 GPX（`gpx_sha256`、總長）。
> - 倒著走、超出曲線範圍、GPX 換過時，一律退回曲線自帶的 `fallback`（一組 `munter-split` 參數），而且畫面要標出來。
> - 外部來源**只能給腳程，不能寫 `poi_visits.eta`**。抵達時刻仍然只由推估鏈產生，錨點仍然是每天的出發時刻。

同時要補一句：`paceReadout`（各坡度配速表）在 `curve` 下印的是 `fallback` 那組參數的表，並註明「TRC 曲線的等效腳程」。因為曲線本身沒有「某坡度 1 公里」的答案。

---

## 3. TRC 賽事計算機能輸出什麼（已驗證）

- **端點**：前綴 `/api/v1/racepower`（`TRC/backend/api/racepower.py:49`）。
  - 主計算 `POST /plan`（`:827`，扣額度 `MS.use_quota("calc")`）。
  - 上傳路線 `POST /course`（`:354`）。
  - Cairn 匯入 `POST /course/cairn`（`:392`）。
  - 賽事 GPX `POST /course/event/{eid}`（`:441`）。
  - 個人坡度模型 `GET /grade-model`（`:511`）。
  - 存檔 `GET/PUT/DELETE /saved/{eid}`（`:1190-1211`）。
  - 公開分享 `POST /share`、`GET /share/{sid}/data`（`:964`、`:998-1020`）。
- **輸入** `PlanIn`（`TRC/backend/engine/racepower/calc.py:184-237`）：
  - 路線 `course`
  - `start_time`（`"HH:MM"`）、`date`
  - `stops[{km,minutes,type,name}]`
  - `day_splits_km`、`terrain`（百岳逐段地形）
  - `hourly`／`weather_points`（天氣）
  - `night_slow_pct`、`pack_kg_by_day`、`poles`、`snow`
- **每段輸出**：
  - 路跑／越野（`TRC/backend/engine/racepower/planner.py:739-751`）：`start_km, end_km, dist_m, gain_m, loss_m, grade, z_mean, walk, power, pace_s_per_km, gap_pace_s_per_km, vert_m_per_h, t, cum_s, eta`，另有熱與天氣欄位。
  - 百岳（`planner.py:1214-1221`）多了 `day, terrain, f_day, f_time, A（海拔）, H（熱）, pack_kg, sigma, speed_kmh`。
  - **`t`／`cum_s` 是移動時間**。`eta` 才加上停留：路跑／越野是 `cum + stops_before`（`:746`）；百岳是 `(cum − day_start)/ratio + stops_before`（`:966`）。
- **整體與每天**：
  - `summary`：`time_s`（移動）、`finish_eta`、`stops_s`、`time_total_s`、`time_total_range_s`（`TRC/backend/engine/racepower/nonmoving.py` 預估的非移動時間）、百岳的 `clock_s, moving_ratio, eph_personal, band`（`planner.py:1291-1306`）。
  - `days[]`（`:1222-1246`）：每天 `km, gain_m, loss_m, moving_h, clock_h, kcal, water_ml, pack_kg`。
- **補給站與航點的到站時間**：在 `fuel.schedule[]`、`fuel.stations[{km,type,name,t_s}]`（`TRC/backend/engine/racepower/fuel.py:581-595`、`:652-654`、`:694`）。分段**不在航點切**。
- **坡度與地形**：
  - 路跑／越野用個人 RE(g)：每 2 % 一個坡度箱，往 Minetti 2002 的先驗收縮，加上走／跑步態、技術係數（`TRC/backend/engine/racepower/grade_model.py:1-67`、`:192`）。
  - 百岳用 Pandolf 反解速度，加個人 δ(g)、海拔項 A(z)、地形 η、積雪（`TRC/backend/engine/racepower/capacity.py:1-21`）。
- **疲勞**：
  - 越野：個人耐久衰退，只用來分配各段時間（`TRC/backend/engine/racepower/trailhr.py`，`planner.py:612-614`）。
  - 百岳：日內 `f_time = exp(γ·h)`（`capacity.py:421`）；**多日疲勞 `f_day` 固定 1.0**，並發出警告（`capacity.py:424`、`planner.py:1272`）。
- **多日分天**：
  - `day_splits_km` → `course.cut_at`（`TRC/backend/engine/racepower/course.py:595-611`）；分日點要剛好 n−1 個，否則每天平均分（`course.py:461-472`）。
  - 分日點候選：營地航點、OSM 山屋、紀錄中的過夜、Cairn 過夜點（SP-533～537）。
- **夜間與熱**：
  - 夜間減速只作用在黑暗中的比例（`TRC/backend/engine/racepower/night.py:144-200`）。
  - 熱依每段中點的時鐘時間取溫度，迭代收斂（`planner.py:618-668`）。
  - → **TRC 的移動時間會隨出發時刻小幅變動**（§4.4）。
- **個人化的「EPH」**：
  - 百岳：`eph_personal`＝solo 登山日 `(km + 爬升/100)/移動 h` 的中位數，爬升 ≥600 m 的日子權重 3 倍（`TRC/backend/engine/racepower/athlete.py:303-326`、`:1368-1372`）。
  - 越野：個人 effort divisor（一位跑者擬合出 153，ITRA 是 100；`TRC/backend/engine/terrain_calib.py:1-12`）。
  - **兩者都不計下降**，跟 Cairn 的 Munter 不同（§0 第 2 點）。
- **已有的匯出**：
  - `/plan` 的 JSON。
  - 公開分享快照 `GET /share/{sid}/data`：每段已經帶 `day, start_km, end_km, t, cum_s, eta`，不含座標（`TRC/backend/engine/racepower/share.py:41-47`）。
  - 手錶課表（`watch_export.py`）。
  - 賽事 GPX 原檔（`TRC/backend/api/plan.py:423-439`）。
  - **沒有**附時間的 GPX、CSV 或 ICS。
- **跨專案 token 的現況**：
  - `api_tokens` 表、`create_service_token`／`service_token`（`TRC/backend/accounts/store.py:692-725`）、`LINK_SCOPES = link:gpx_sync, link:calc, link:participant_ability`（`TRC/backend/membership.py:82-89`）、`check_service_token`（`:338-352`）都已經寫好。
  - **但沒有任何端點或 middleware 接受 link token**（只有測試在用）。所以 SSO 之後還要補「哪些路由接受 Bearer」那一層。

---

## 4. 兩邊資料怎麼對應

### 4.1 路線與點

- **GPX 是不是同一份**：
  - 從 Cairn 匯入（SP-537）時，TRC 拿到的就是 Cairn 的 GPX 原檔（`GET /api/gpx/:id?share=`，`TRC/backend/engine/cairn.py:107-127`）。TRC 的 `course_id` 是檔案內容的 sha1（`TRC/backend/api/racepower.py:359`）。
  - 反方向（TRC 的賽事 GPX 推到 Cairn）是 SP-538 的範圍。
  - 不管哪個方向，**以檔案的 sha256 認同一份**，曲線帶著 hash。Cairn 換過 GPX（Cairn 慣例 11 會清掉里程）就失效、退回 fallback。
- **里程**：
  - Cairn 對原始點逐點累加 haversine。
  - TRC 也用 haversine，但之後重取樣成每 10 m 一點（`course.py:166-232`）。
  - 同一份檔案的總長應該只差零點幾 %（推估）。曲線帶 `total_m`，Cairn 查表前用 `km × TRC總長/Cairn總長` 正規化；差超過 1 % 就當成不是同一份。
- **點**：
  - Cairn 的點是 `poi_visits`，每一次經過有自己的 `distance_m`，往返路線各算各的。
  - TRC 的分段是坡度段，**不需要對點**。曲線是「里程 → 累積移動時間」的分段線性函式，Cairn 任何兩個里程之間都查得到。
  - 過夜點：SP-537 已經把 Cairn 的過夜點轉成 TRC 的 `day_splits_km`，曲線把這組分日點一起帶回去。Cairn 的過夜點跟它差超過 1 km 時要提醒（日內疲勞會在錯的地方歸零）。

### 4.2 海拔

- **兩邊都只用 GPX 的 `<ele>`，都沒有用 DEM 補值**。DEM 只用在 Cairn 的 3D 地形，以及 TRC 的天氣點高度。
- 平滑方式不同：
  - Cairn：沿里程移動平均，半視窗 50 m，**不加遲滯**（`CAIRN/v2/shared/src/geo.ts:91`、`:123-170`，Cairn 慣例 44）。
  - TRC：每 10 m 重取樣 → 5 點中位數 → Gaussian σ=50 m → **3 m 遲滯**算爬升，並可依官方爬升縮放（`course.py:31`、`:201`、`:406`）。
  - → 同一份 GPX 的爬升數字會不同。差多少沒量，推估 5–15 %；遲滯通常讓 TRC 比較低。
- 影響：
  - **曲線（B）不受影響**：只看里程對時間，不看爬升。
  - **反解 EPH（A）會受影響**：EPH 要在 **Cairn 的平滑與分桶**上反解，否則 Cairn 用自己較高或較低的爬升去乘，就會系統性偏差。所以 TRC-1 要照抄 Cairn 的 `rangeStats`，不能用 TRC 自己的 `gain_m`。

### 4.3 時區

- 兩邊都是**不帶時區的當地牆鐘時間**：
  - Cairn：`eta` 是 `'HH:MM'`、`day` 是整數、`start_date` 是 `'YYYY-MM-DD'`（`CAIRN/v2/shared/src/trip-dates.ts`）。
  - TRC：`start_time` 是 `"HH:MM"`、`date` 是日期，ETA 輸出 `"HH:MM (+n)"`（`planner.py:62-71`）；天氣寫死 UTC+8（`TRC/backend/engine/racepower/weather.py:147`）。
- 曲線只帶「移動秒數」，**不帶時刻**，所以沒有時區問題。附帶的 `start_time` 只用來提醒「TRC 是用幾點出發算的」。

### 4.4 休息、過夜、出發時刻

- 曲線只放**移動時間**（`cum_s`），**不含**停留，也不含 `moving_ratio` 換算。休息在 Cairn 由使用者逐點填。
- TRC 的 `nonmoving.py` 預估的每天非移動時間，可以當成「建議休息」另外顯示，不自動寫入（Q6）。
- 移動時間跟出發時刻的關係很小，但不是零：夜間減速（`night.py`）和熱（`env.py`）依時刻而定。曲線帶上 TRC 算的時候用的每天出發時刻；Cairn 的出發時刻差超過 1 小時就提示「時間是照 TRC 的 06:00 出發算的，可以回 TRC 重算」（Q3）。
- 多日：TRC 每天的移動時間各自累加（`day_start` 歸零的是時鐘，不是 `cum_s`），所以一條全程曲線就夠，Cairn 每天的鏈照樣取差值。

---

## 5. 整合方案

| 方案 | 做法 | 改哪邊 | 前置 | 精度 | 取捨 |
|---|---|---|---|---|---|
| **A 給數字** | TRC 計算機多一張「給 Cairn 的腳程」卡：在 Cairn 的算法上反解 `munter-split`（上／下坡 EPH）與 `tobler-split`（上／下係數），附每天的移動時間與誤差；使用者手動填進 Cairn（或把每天移動時間填進 Cairn 既有的校準視窗） | 只改 TRC | 無 | 總時間對；每天、每段有誤差（失去疲勞、海拔、背包、夜間的逐段差異） | 最快、零耦合、零資料外流；要手動，而且只是近似 |
| **B 時間曲線檔** | TRC 輸出 `cairn-curve` JSON（下載，或在公開分享快照裡勾選附上）；Cairn 新增 `curve` 腳程，可以上傳檔案或貼 TRC 分享連結（Cairn Worker 伺服器端抓，只接受 TRC 網址、不跟轉址、限大小）；匯入前先顯示「每天差多少」，套用後照常跑推估鏈 | 兩邊 | 無（不用 SSO） | 跟 TRC 逐段一致（同一份 GPX） | 慣例 43 要改一句；`PaceBasis` 要多帶起訖里程；曲線存在 `pace_config`（D1 TEXT，200 段約 5 KB） |
| **C SSO 後自動同步** | SP-329 之後，TRC 用 link scope 推「路線＋過夜點＋曲線」到 Cairn 建行程（SP-538 的推）；或由 Cairn 用服務 token 拉 TRC 已存的計算結果；TRC 重算後 Cairn 顯示「有新版，重新同步」 | 兩邊 | SP-329、SP-538 | 同 B | 最順手；要有服務對服務的認證、帳號對應、同意設定；工作量最大 |
| ~~D 直接寫 ETA~~ | TRC 把每點時刻寫進 `poi_visits.eta` | 兩邊 | C 的前置 | 時刻對，但會被下次推估蓋掉 | **不建議**：違反慣例 43、休息與出發時刻對不上（§2.2） |

使用者提的「把 TRC 算出的個人化 EPH／坡度係數給 Cairn 用」就是 **A**，而 B 的 `fallback` 也是同一組數字。

### 5.1 安全與授權

- **A**：沒有資料跨服務流動。
- **B**：
  - 曲線不含座標，只有「里程 → 秒數」，加上 GPX hash 和分日點。但它等於**個人能力資料**（腳程）。
  - 存進 Cairn 的 `pace_config` 之後，**任何持有 Cairn 分享連結的人都讀得到**：`/api/gpx/:id/meta` 會回 `pace_config`（`CAIRN/v2/server/src/middleware/auth.ts:117-126`）。留守的人本來就看得到每點的預估時刻，風險增加有限，但匯入前要寫清楚。
  - TRC 的分享快照預設**不附**曲線，要勾選才放（Q4），分享可以撤銷（`DELETE /shares/{sid}`）。
  - Cairn 端抓 TRC 分享連結：只接受 TRC 的網址（同 SP-537 的 `TRC_CAIRN_ORIGIN` 白名單做法）、不跟轉址、timeout 與大小上限；不存分享 id，只存快照。
- **C**：
  - 服務之間用 SP-329 的服務 token，加上 Google `sub` 對應，**兩邊都不存對方的使用者 token 或密碼**。
  - TRC 要補 middleware：只在白名單路由接受 Bearer，經 `check_service_token` 換成該使用者。
  - 每個使用者要能看到、能撤銷「允許 Cairn 讀我的計算機結果」。
  - TRC 的 `LINK_SCOPES` 現在只有三個，SP-556 的 `link:weather` 和這裡的曲線（可以沿用 `link:calc`）要一起補。

### 5.2 商業化影響

- TRC 的計算扣 `calc` 額度。曲線是 TRC 的核心價值，等於把教練模型的結果外送。建議：
  - **A 免費**：當成兩邊互相導流的入口，配合 SP-571。
  - **B、C 的曲線輸出跟著 TRC 的付費等級走**（Q5）。
- 曲線若含天氣的影響（熱），而那份天氣來自 Open-Meteo 免費版，就有 SP-531 Q1 已記的商用授權問題：外送的是衍生數字，不是原始資料，風險低，但仍在同一個「先不管」的範圍。
- Cairn 方面，`curve` 腳程可以免費（用 TRC 的人本來就要先在 TRC 付費或用額度）。也可以跟 Cairn 自己的進階方案綁，由使用者決定。

### 5.3 建議

**A → B → C。**

- A 現在就能做，只改 TRC，也是 B 的 `fallback`。
- B 讓 Cairn 拿到 TRC 逐段的精度，不用等 SSO。
- C 是 SP-538 的「推」與「重新同步」，等 SP-329。
- D 不做。

---

## 6. 實作 task 清單

### 6.1 TrailRunCoach（照順序）

| # | 標題 | 做什麼 | 驗收 | 依賴 | 大小 | 不用等 SSO |
|---|---|---|---|---|---|---|
| TRC-1 | 計算機「給 Cairn 的腳程」：用 Cairn 的公式反解上／下坡 EPH | 在 TRC 照抄 Cairn 的 `rangeStats`（haversine、50 m 移動平均、依 Δh 分桶、Tobler），以每段 TRC 移動時間做最小平方，解 `munter-split`（upEph、downEph）與 `tobler-split`（up、down）；結果頁一張卡：兩組參數、每天移動時間（給 Cairn 校準視窗用）、每天與整體誤差、複製按鈕；說明「TRC 的 EP/h 不能直接填 Cairn」 | ① 同一份 GPX、同一組參數，TRC 算出的 Cairn 時間與 Cairn `paceMinutes` 一致（用手算的 golden 值，±1 分）；② 反解後整條路線總時間與 TRC 移動時間差 ≤1 %；③ 每天誤差有印出；④ 有 Cairn 匯入來源時卡片放在最上面 | 無 | M（1–1.5 天） | ✅ |
| TRC-2 | 時間曲線輸出（`cairn-curve` v1）與規格文件 | 定義 JSON：`{v:1, source:"trailruncoach", gpx_sha256, total_m, day_splits_km, start_times, points:[[m, cum_moving_s], …]（每個 TRC 段終點）, fallback:{kind:'munter-split', upEph, downEph}, computed_at, model}`；從存過的計算下載（`GET /api/v1/racepower/saved/{eid}/cairn-curve`）與從當次結果下載；公開分享快照可勾選附上（預設不附）；規格寫在 `docs/spec/cairn-curve.md`，Cairn 照它實作 | ① 曲線上任兩點的差＝TRC 同區間各段 `t` 的和（測試）；② 不含座標、不含停留、不含 token；③ 分享快照沒勾就沒有曲線；④ 規格文件有範例檔與 Cairn 端的驗證規則（hash、總長 1 %、倒著走退回） | TRC-1（fallback） | M（1 天） | ✅ |
| TRC-3 | 曲線多帶「建議休息」與出發時刻提醒（可選） | 把 `nonmoving.py` 預估的每天非移動時間放進曲線的 `rest_hint`（只提示，不寫入 Cairn）；寫出計算用的每天出發時刻 | 曲線裡有 `rest_hint` 與 `start_times`；Cairn 不用它也能正常運作 | TRC-2 | S（0.5 天） | ✅ |
| TRC-4 | link token 路由層＋讀取已存計算的服務 API | middleware 只在白名單路由接受 `Authorization: Bearer`，經 `check_service_token` 換成該使用者；`GET /api/v1/link/cairn/curves?gpx_sha256=` 列出這個人對同一份 GPX 存過的計算與曲線；使用者設定可關「允許 Cairn 讀取」；補 `link:weather`（SP-556）到 `LINK_SCOPES` | ① 沒 token／錯 scope／關掉同意 → 401/403；② 只回這個人的資料；③ 有測試 | **SP-329** | M（1–1.5 天） | ❌ |
| TRC-5 | 推到 Cairn：路線＋過夜點＋曲線（SP-538 的「推」） | 賽事頁「推到 Cairn」：呼叫 CAIRN-3 建立或更新行程（GPX、過夜點的 visits、`pace_config = curve`）；記下 Cairn 行程 id，可以再推（更新） | ① 推過去在 Cairn 看得到路線、過夜點、TRC 曲線腳程；② 再推是更新不是新建；③ 不存 Cairn 的使用者 token | SP-329、CAIRN-3、TRC-2 | M（1 天） | ❌ |

順序：**TRC-1 → TRC-2 →（TRC-3）**，這三張現在就能做。TRC-4、TRC-5 等 SP-329，建議併進 SP-538 當子單。

### 6.2 inReachLiveTrack（Cairn）

| # | 標題 | 做什麼 | 驗收 | 依賴 | 大小 | 不用等 SSO |
|---|---|---|---|---|---|---|
| CAIRN-1 | 慣例 43 修訂＋`curve` 腳程 | `PaceModelSchema` 加 `{kind:'curve', …}`（照 TRC-2 規格）；`PaceBasis` 多帶 `fromM`/`toM`（`directedSegmentStats` 已有索引）；`paceMinutes` 的 `curve` 分支用曲線差值，倒著走、超出範圍、hash 或總長不符時退回 `fallback` 並回報原因；`paceReadout` 印 fallback 的表並註明；改 CLAUDE.md 與 conventions-archive 的第 43 條 | ① 各段相加＝曲線全長時間（測試）；② 倒著走、換 GPX 會退回 fallback，而且畫面看得到；③ 時間軸、框選、推估鏈、配速表數字一致（沿用慣例 43 的測試） | TRC-2 規格 | M（1.5–2 天） | ✅ |
| CAIRN-2 | 匯入 TRC 時間曲線（上傳檔案／貼 TRC 分享連結） | 腳程面板多「從 TrailRunCoach 匯入」：上傳 JSON 或貼分享連結（Worker 端抓，只接受 TRC 網址、不跟轉址、限大小）；匯入前比較「目前腳程 vs TRC」每天的移動時間，以及出發時刻差異；套用寫 `pace_config`，之後照常推估（`overwriteUserCount` 照舊） | ① 不是同一份 GPX 時拒絕或明講；② 匯入前看得到每天差異；③ 曲線存 D1、分享頁的留守者看到同樣的時刻；④ 說明「腳程會出現在分享連結」 | CAIRN-1、TRC-2 | M（1–1.5 天） | ✅ |
| CAIRN-3 | TRC 用的內部 API：列行程、讀行程、建立／更新行程 | 服務 token＋Google `sub` 對應；`GET` 某人的行程清單（SP-538 的「拉」）、`POST/PUT` 建立或更新（GPX＋過夜點 visits＋`pace_config`）；寫入只能寫 `pace_config` 與點位，不寫 `eta` | ① 未授權 401；② 只動這個人的行程；③ 不寫 `poi_visits.eta`（慣例 43） | **SP-329** | L（1.5–2 天） | ❌ |
| CAIRN-4 | TRC 曲線重新同步 | 行程記住曲線來源（TRC 的計算 id，不存 token）；SSO 後按鈕「TRC 有新版」→ 經 TRC-4 拉最新曲線 | 重新同步後時間更新、手填的出發時刻不變 | CAIRN-2、TRC-4 | S（0.5–1 天） | ❌ |
| CAIRN-5 | 修 `EtaSourceSchema` 過時註解 | `CAIRN/v2/shared/src/schema.ts:309-317` 改成現行語意（「誰寫的」，手填會被覆寫） | 註解跟慣例 43 一致 | 無 | XS | ✅ |

另外 Cairn 既有單可以順手：**SP-183**（反推時加註海拔、揹重相似才準）可以併進 CAIRN-2 的說明。TRC 曲線本身已經考慮海拔與背包，反推加註在匯入 TRC 時不適用。

---

## 7. 跟既有單的重疊與衝突（不改那些單，只寫建議）

- **SP-531**：
  - Q5 已經改成可以回寫。結論「Cairn 管行程（GPX 主檔、過夜點、抵達時間），TRC 管配速」**仍然成立**，本研究的回寫方式（回寫腳程、不寫時刻）就是照這個分工。
  - 建議在 SP-531 的筆記補一句「回寫的是腳程曲線，抵達時間仍由 Cairn 推估鏈產生（SP-570）」，避免之後被讀成「TRC 寫 ETA」。
- **SP-538**：
  - 描述說「時間也可以回寫，做法照 SP-570」。建議具體化為「推的時候帶 `pace_config = curve`，**不寫 `poi_visits.eta`**」。
  - TRC-4、TRC-5、CAIRN-3、CAIRN-4 都在它的範圍內，建議當 SP-538 的子單（或把 SP-538 拆成「拉：帳號對應＋行程清單＋重新同步」與「推：路線＋過夜點＋曲線」兩張）。
  - 驗收第 4 條「時間照 SP-570 結論」可以改寫成「推過去的行程在 Cairn 用 TRC 曲線算出每點時刻；再推是更新」。
- **SP-537**：
  - 描述寫「只做單向拉（Cairn 慣例 43）」。理由已經過時，但功能本身不用改。
  - 它匯入的過夜點正好是曲線 `day_splits_km` 的來源，兩邊天的切法一致。
- **SP-556（W4 天氣）**：
  - 「Cairn 傳自己的抵達時間，不違反慣例 43」仍然對。
  - 套用 TRC 曲線之後，Cairn 的抵達時間跟 TRC 自己算天氣用的時間會很接近，兩邊天氣提醒不再各說各話。
  - `link:weather` scope 要跟 TRC-4 一起加。
- **SP-541、SP-554（Cairn 天氣依抵達時間）**：寫的「用 Cairn 自己的 ETA、不從 TRC 拿時間」仍然對；有了曲線，那個 ETA 就是 TRC 等級的精度。不衝突。
- **SP-329**：
  - 「兩邊互通的 API 與 scope 寫成文件」要加上 `cairn-curve` 的讀取（沿用 `link:calc`）與 Cairn 端的建立／更新行程（CAIRN-3）。
  - 補一個發現：TRC 的 `check_service_token` 已經寫好，但沒有任何路由接受 Bearer。
- **SP-186（Cairn：依天數自動分段）**：跟 Cairn 慣例 43 的否決紀錄「依時數自動切天」看起來相衝。本研究不碰天的切法（過夜點仍然由使用者標或從 TRC 帶），所以沒有新增衝突。
- **SP-182（分享頁腳程比較）、SP-329 的報名者能力值**：之後可以重用 TRC-1 的「用 Cairn 公式反解 EPH」做跨人比較。目前不在範圍內。

---

## 8. 要使用者拍板的問題

1. **整合路線**：
   - (a) A → B → C（建議）
   - (b) 只做 A
   - (c) 跳過 B，直接等 SSO 做 C
   - (d) 直接寫 ETA（不建議）
2. **Cairn 慣例 43 怎麼改**：
   - (a) 加第五種填法 `curve`，外部來源只給腳程、不寫 `eta`（建議，§2.3 條文）
   - (b) 不改規則，只做 A
   - (c) 允許外部寫 `eta`（不建議）
3. **曲線要不要含依時刻變動的影響（夜間、熱）**：
   - (a) 含，並帶 TRC 用的出發時刻，Cairn 的出發時刻差超過 1 h 就提醒（建議）
   - (b) 不含，用不看時刻的移動時間（比較穩，但少了夜間和熱）
4. **TRC 公開分享快照附曲線**：
   - (a) 預設不附、勾選才附（建議）
   - (b) 預設附
5. **商業化**：
   - (a) A 免費，B 和 C 的曲線輸出跟 TRC 付費等級（建議）
   - (b) 全部免費當導流
   - (c) 全部付費
6. **休息**：
   - (a) Cairn 照舊由使用者逐點填，TRC 只在曲線附「建議休息」提示（建議）
   - (b) TRC 的建議休息自動寫進 Cairn 的 `rest_minutes`
7. **fallback／A 卡片主推哪種填法**：
   - (a) `munter-split`（上／下坡 EPH，建議，Cairn 使用者多半用 EPH 思考）
   - (b) `tobler-split`
   - (c) 兩種都給、不主推

---

## 9. 外部參考

- EPH／努力點的定義與例子（Garmin 台灣部落格，EP＝km＋爬升/100、EPH＝EP/h）：https://www.garmin.com/zh-TW/blog/general/trail-running-watch-setting-3-features-for-smarter-trail-running/
- 「km-effort」與下坡計入的變體（100 m 上升＝1 km、400 m 下降＝1 km）：https://www.passy-mont-blanc.com/en/activites/balades-et-randonnees/tour-des-fiz/le-kilometre-effort/ 、https://www.hikr.org/post30201.html
- Tobler 1993 與 GPS 實測比較（Tobler R² 為負；資料導向模型 RMSE 1.10 對 Tobler 1.24，預印本）：https://arxiv.org/abs/2303.16065
- Minetti et al. 2002（坡度能量成本）：https://iris.unibs.it/retrieve/ddc633e4-3c03-4e2e-e053-3705fe0a4c80/Minetti%20JAP%202002.pdf
- 本 repo 既有整理：`TRC/docs/research/effort-distance-formulas.md`（ITRA km-effort、Munter、各公式出處）。

這些研究的共同結論是：通用公式的個人差異很大，**用個人資料校正**才準。這正是 TRC 能補 Cairn 的地方：Cairn 的校準一次只解得出一個縮放、比例固定，TRC 有逐段的個人坡度模型。
