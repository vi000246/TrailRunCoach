# 伺服器重啟後第一次打開活動，判讀卡為什麼慢（SP-759）

> 調查日期：2026-10-19。只做調查與量測，沒有改正式程式。分支 `worktree-agent-ad5be21d65d6cde8c`（main `d5c8f503` 之後）。
> 相關：`device-side-charts.md` §0 第 7 點、§7 單 E（SP-325，本單的來源）；`cache-tiering.md`（SP-320，NAS 倍率 × 8.5）。
> 標記：**已驗證**＝讀程式或實測；**推估**＝由量測換算或判斷。
> 資料：示範跑者（`backend.demo.build --weeks 52`，257 筆活動），每個情境複製一份、開新的 process，沒有讀 NAS、`.env`、金鑰。本機 Apple M5 Pro，量測時 load average 4–6（同時有其他程式），「NAS」＝本機秒數 × 8.5。

---

## 0. 結論

1. **主因是一個 bug：程式碼雜湊每次開 process 都不一樣，所以重啟後磁碟上的快取全部對不上。**（已驗證）
   - 磁碟快取（判讀用的逐筆量測 `series.db`、圖表的渲染快取）的 key 含「算這個值的程式碼雜湊」（`engine/codehash.py`）。
   - 雜湊把函式的**預設值**用 `repr` 算進去（`backend/engine/codehash.py:285`、`:121`）。預設值是函式時，`repr` 是 `<function stored_course at 0x10ce…>`，**記憶體位址每個 process 都不同**。
   - 例：`race_refs.course_of(e, gpx=stored_course)`（`backend/engine/panels/race_refs.py:97`）。判讀卡的量測（`workout_review.measure`）、門檻信心的逐跑紀錄（`threshold_confidence.gather_runs`）、TIS 內建函式、還有判讀卡與「心率與功率」等圖的渲染快取簽章，程式碼都會走到它，所以**每次重啟都換一個雜湊**。
   - 同樣的預設值在 `backend/engine` 有 28 個函式（`docs/research/first-open-cards/unstable_defaults.py` 列出），例如 `drift_bars.apply`、`race_refs.apply`、`capacity.fit_walk_capacity`、`pacing.RunModel.__init__`、`weather.*`。
   - 證據：同一份資料連續啟動 3 次，`series.db` 裡 `workout_review_v23`、`thr_conf_run_v2` 每次都多一組新的 `code`（建置時 1 組＋3 次重啟各 1 組）；兩個 process 比對雜湊組成，只差 `race_refs.course_of` 一項。圖表渲染快取：第二次啟動前一次打開過的卡，key 的資料指紋相同、程式簽章不同，全部重算。
2. **重啟後第一次打開一筆活動要等多久（本機，示範資料）：**「重點數字」3.7–4.1 秒（NAS 推估 31–35 秒）；第二筆活動的「跑姿」兩張 1.1 + 1.6 秒（NAS 約 23 秒）。**之前打開過的活動也一樣慢**，因為渲染快取也對不上。
3. **雜湊改成穩定之後（只在量測 process 裡試，沒有改程式）：**
   - 打開過的活動：重啟後直接用渲染快取，每張 0.005–0.12 秒；「重點數字」剩 1.2 秒，這是第一次算圖表程式簽章，正式站的暖機（`_warm_chart_code`，`backend/api/wko5views.py:375`）本來就會先算。
   - 沒打開過的活動：「重點數字」3.8 → 2.3 秒；「跑姿」1.1 / 1.6 → 0.2 / 0.15 秒。
4. **修完之後還剩的（約 1–2 秒本機、9–17 秒 NAS，推估）：**
   - 每個 process 第一次算快取的程式碼雜湊：約 0.6–1.2 秒（`codehash.closure`，用 `dis` 走過 1,000 多個函式），暖機沒有先算。
   - 門檻信心第一次要讀全部 243 筆跑步的溫度（`zone_events.run_heat` → `watch_temp`，`backend/engine/zone_events.py:367`、`:208`）：約 0.4–0.8 秒，每個 process 一次，沒有存磁碟。
5. **建議**（§5）：先修雜湊（Bug，S）；再把上面兩項放進暖機（S）。「背景預先算最新一筆活動的判讀卡」先不做，修完在 NAS 量過再決定（§6 Q3）。

---

## 1. 慢在哪：重點數字卡的呼叫路徑（已驗證，cProfile）

重啟後第一次打開最新一筆跑步（越野跑），「重點數字」在 `_render` 裡 4.2 秒（profile 下）：

| 花的時間 | 在做什麼 | 位置 |
|---|---|---|
| 2.7 秒 | 門檻信心：近一年 243 筆跑步各算一次摘要（心率品質、溫度…），`cached_series` 全部沒命中 | `threshold_confidence.py:876`（`gather_runs`）、`:895-896`；由 `workout_review.py:1838`（`_with_easy`）→ `:1813`（`easy_cap_of`）→ `hr_profile.py:514` → `:430` → `:199` → `threshold_confidence.py:710` → `:1079` 走到 |
| 1.15 秒 | 算快取的程式碼雜湊（`codehash.closure`，用 `dis` 讀 bytecode） | `codehash.py:228`、`:159` |
| 0.8 秒 | 讀全部跑步的溫度 | `zone_events.py:367`、`:208` |
| 其餘 | 同類型過去 26 週的活動逐筆量測（`measure`），也沒命中 | `workout_review.py:3688`（`_pool`）、`:2197`（`measure`） |

「跑姿」三張卡（`_form_pool`，`workout_review.py:4092`）用的也是 `_pool` → `measure`。第一筆活動的跑姿卡快（0.2 秒），是因為「重點數字」剛把同一批過去活動量完；換一筆不同類型的活動（第二筆是路跑）就要重量一次（1.1、1.6 秒）。

## 2. 為什麼重啟後快取對不上（已驗證）

- `series.db` 一列的 key 是 `(key, 檔案, code, sig)`，`code` 是 `series_code(compute)`（`backend/engine/wko5expr/dataset.py:921`、`:936`）＝`codehash.callable_hash`。
- `callable_hash` 走過 compute 能碰到的所有函式，每個函式的雜湊包含 `_stable(fn.__defaults__)`（`codehash.py:285`）。`_stable` 對集合與 dict 有排好順序，其他東西直接 `repr`（`codehash.py:121`）。
- 函式、lambda、`object()` 哨兵的 `repr` 帶記憶體位址，每個 process 不同。所以只要走到一個這樣的預設值，整個雜湊就每次重啟都變。
- 圖表渲染快取的 key 也含程式簽章（`wko5views.py:967` → `chartscope.py:719-734`，同樣用 `code_hash`），在請求裡算的簽章也被同一批函式污染（量到走進來的有 `race_refs.course_of`、`race_refs.apply`、`drift_bars.apply`、`capacity.fit_walk_capacity`、`pacing.RunModel.__init__`、`weather.fetch_activities_conditions`、`plan_sessions.plan_rates`）。
- 沒受影響的（兩個 process 一樣）：活動自動分類、門檻估算、PD 擬合、逐筆的峰值／曲線／功率來源／壞活動（`activity_auto._branch_code`、`fitdataset.estimate_code`、`pd_code`、`dataset.per_workout_code`）。
- 附帶影響：`series.db` 每個檔案每個 key 只留 4 列（`dataset.py:901`），每次重啟都寫一列新的、擠掉舊的，**正常的變體（例如估算門檻前後兩種）也會被擠掉**；寫入量也白白多一倍。
- TIS 內建函式的快取 key 名稱本身就含雜湊（`evaluator.py:912`、`:919`），所以每次重啟會多一個新的 key，舊的永遠用不到（`series.db` 裡 `builtin_tisaerobic_*` 有 4 個不同名字）。

## 3. 量測數字（已驗證，示範資料）

挑最新兩筆有心率的跑步：#256（越野跑，10/19）、#255（路跑，10/18）。依頁面順序打「重點」分頁 6 張，再打「跑姿與膝蓋」分頁 4 張。每格是牆鐘秒數（本機），括號是 NAS 推估。

| 情境 | #256 重點數字 | #256 跑姿（前後半／坡度） | #255 重點數字 | #255 跑姿（前後半／坡度） |
|---|---|---|---|---|
| 重啟（快取都在磁碟上，渲染快取清空＝沒打開過） | 3.7–4.1（31–35） | 0.20／0.25 | 0.04 | 1.1／1.6（9／14） |
| 重啟，渲染快取保留（**打開過的活動**） | 3.9（33） | 0.21／0.26 | 0.05 | 1.2／1.8 |
| 部署改到量測的程式（`series.db` 清空） | 6.2（53） | 0.20／0.24 | 0.10 | 1.1／1.6 |
| 雜湊穩定（實驗），沒打開過 | 2.3（19） | 0.12／0.03 | 0.02 | 0.20／0.15 |
| 雜湊穩定（實驗），打開過 | 1.2（10）＊ | 0.006／0.006 | 0.006 | 0.005／0.005 |

＊ 這 1.2 秒是第一次算圖表程式簽章；量測關掉了暖機（`WKO5COACH_NO_WARMUP=1`），正式站暖機會先算掉（`wko5views.py:375`、`:597`）。

- 同一份資料連續重啟 3 次，三次幾乎一樣（3.69／3.68／3.73 秒），證明不是「第一次建快取」，是每次都重算。
- 其他卡（心率與功率 0.1 秒、路線難度 0.12 秒、無氧儲備 0.1 秒）重啟後也重算，但本來就快。
- 跟 SP-325 的數字差異：SP-325 量到「跑姿 7.15 秒、重點 3.34 秒」是在另一份示範資料、另一個時間點；這次「重點」接近，「跑姿」第一筆快很多，因為這次「重點」先把同一批過去活動量完了。結論方向一致。

## 4. 「背景預先算」和「存到磁碟」值不值得（推估）

- **存到磁碟**：本來就有（`series.db`、渲染快取），只是 bug 讓它重啟後用不到。修 bug 就是這條路，最便宜。
- **背景預先算**：要預先算什麼？
  - 程式碼雜湊與門檻信心的 `gather_runs`：跟活動無關，每個 process 一次，本機約 1–2 秒（NAS 9–17 秒）。放進暖機的低優先度那段，成本小、效果確定。
  - 某筆活動的判讀卡：每筆 NAS 8–14 秒（SP-325 §2.1）。只對最新一筆、而且只在同步後做，1,000 位會員一天約 600 筆 × 10 秒 ≈ 1.7 CPU 小時（推估）；大部分人不一定會打開。修完 bug 後，打開過的活動重啟後就不用重算，要不要再預先算沒打開過的，等 NAS 實測再決定。

## 5. 建議開的單

| # | 標題 | 做什麼 | 要達成什麼 | 大小 |
|---|---|---|---|---|
| A | Bug：快取的程式碼雜湊每次重啟都變 | `codehash._stable` 遇到函式／類別／方法用「模組.名稱」，其他帶位址的物件用型別名稱；加測試：兩個 process 算同一個 compute 的雜湊要一樣（subprocess），並掃 `backend.engine` 不准再有「repr 帶位址」的預設值進雜湊 | 重啟後打開過的活動直接用渲染快取；沒打開過的「重點數字」本機 3.8 → 2.3 秒 | S |
| B | 暖機先算快取雜湊與門檻信心 | 暖機的低優先度那段：對判讀用的幾個 `cached_series` compute 先算 `series_code`；呼叫一次 `threshold_confidence.gather_runs`（或把 `run_heat` 的結果也存進 `series.db`） | 重啟後第一張判讀卡再少約 1–2 秒（NAS 9–17 秒） | S |
| C | （先不開，看 Q3）同步後預先算最新一筆活動的判讀卡 | 同步完成、最新一筆是跑步時，背景算它的 19 張判讀卡（`prerender` 佇列、最低優先度） | 剛同步完打開最新活動不用等 | S–M |

修 A 時要注意：雜湊一改，**所有**受影響的快取在部署那次會全部重算一次（跟現在每次重啟一樣，之後就不會了）。

## 6. 要使用者決定的問題

1. 開 A（Bug，修雜湊）？建議：開，P2；這是重啟、部署後「每次都慢」的主因，也讓 `series.db` 每次重啟白寫一輪。
2. 開 B（暖機先算）？建議：開，P3；A 修完再做，量得出差多少。
3. C（同步後預先算最新活動的判讀卡）現在要不要做？建議：先不做；A、B 上線後在 NAS 量一次重啟後第一次打開的時間，還是超過 10 秒再開。

## 7. 實作後（SP-773、SP-774，2026-10-20）

使用者 Q1 答：A、B 開單並實作，C 先不做。A＝SP-773（`codehash._stable` 不再用帶位址的 repr；當預設值的函式會被走進去算），B＝SP-774（暖機低優先度那段的 `wko5views._warm_review`）。

同一份示範資料（`--weeks 52`，2026-10-20 重建，256 筆；最新兩筆是路跑 #255、#254），`perf_sp759.py --warm`（先跑暖機本來就有的 `_warm_chart_code`，有 `_warm_review` 也先跑，各自另外計時）。本機秒數，括號 NAS 推估 ×8.5。load average 3–4。

| 情境 | #255 重點數字 | #255 跑姿（前後半／坡度） | 打開過的活動（重點／跑姿） |
|---|---|---|---|
| 修之前（main `c3829526` 的 codehash） | 1.73（15） | 1.04／1.55（9／13） | 1.73／1.04／1.55（跟沒打開過一樣） |
| A 之後 | 0.54（4.6） | 0.19／0.15 | 0.03／0.006 |
| A＋B 之後 | 0.06（0.5） | 0.19／0.14 | 同上 |
| A＋B，`series.db` 清空（部署那次） | 0.05 | 1.05／1.52 | — |

- B 的暖機步驟本身：快取都在時 0.47 秒（NAS 約 4 秒），`series.db` 清空時 4.3 秒（NAS 約 37 秒，重算近一年每筆跑步的摘要）；都在背景、低優先度。
- 這次「重點數字」修之前只有 1.73 秒（§3 是 3.7 秒）：§3 量測沒有跑暖機（圖表程式簽章 1.2 秒算在卡上），而且 SP-753 之後跑步溫度改存磁碟。
- A 部署那次所有受影響的快取（量測、門檻信心、TIS、圖表渲染）會用新的雜湊重算一次，之後重啟就直接用。

---

## 附錄：量測方法

- 腳本在 `docs/research/first-open-cards/`：
  - `perf_sp759.py`：複製示範資料、開新的 process（TestClient），先建 Dataset（另外計時，正式站由暖機做），清空渲染快取，依頁面順序打兩筆活動的卡。`--times N` 同一份資料連續啟動 N 次；`--keep-render` 保留渲染快取；`--scenario nostore` 先刪 `series.db`；`--profile` 在 worker thread 裡對 `_render` 做 cProfile；`--stable-defaults` 只在量測 process 裡把 `codehash._stable` 換成「函式用名稱」的版本（實驗，不是正式程式）；`--warm`（SP-774）先跑暖機的 `_warm_chart_code` 和（有的話）`_warm_review`，另外計時。
  - `unstable_defaults.py`：掃 `backend` 每個函式，列出預設值 `repr` 帶記憶體位址的。
- 重跑：

  ```
  python -m backend.demo.build --root <demo> --weeks 52
  python docs/research/first-open-cards/perf_sp759.py --base <demo>/base/<日期-seed> --work <dir> --times 3
  python docs/research/first-open-cards/perf_sp759.py --base … --work <dir2> --times 2 --keep-render
  python docs/research/first-open-cards/perf_sp759.py --base … --work <dir3> --times 2 --keep-render --stable-defaults
  python docs/research/first-open-cards/perf_sp759.py --base … --work <dir4> --scenario nostore
  python docs/research/first-open-cards/unstable_defaults.py
  ```

- 比對兩個 process 的雜湊組成、渲染快取 key 的各部分（請求、資料指紋、程式簽章）是用暫存腳本做的（monkeypatch `chart_key`、`codehash.closure` 記錄），結果寫在 §0、§2。
- 本機負載 load average 4–6，數字會被干擾；同一情境重跑差 ±5%。
