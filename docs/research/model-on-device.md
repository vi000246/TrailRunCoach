# 手機瀏覽器跑計算模型要多久（SP-760）

- **量測日期：2026-10-20。** 桌面 Chromium＋CPU 降速模擬手機，**不是真手機**（真手機見 §8）。
- **標記方式**（同 `fit-on-device.md`）：
  - **已驗證**：這次實際量到，或在本 repo 程式碼讀到（附 `檔案:行`）。
  - **推估**：我的推論或粗估。
  - **未找到來源**：查不到。
- **資料**：全部是示範運動員（`python -m backend.demo.build`，seed 20261002，anchor 2026-10-03）一年 260 筆活動。沒有讀擁有者的資料，沒有連 NAS 或正式站，沒有讀 `.env` 或金鑰。
- **接在誰後面**：SP-313（解析 FIT 的速度，`local-first-sync.md` §7.1）、SP-319（FIT 存在使用者裝置，`fit-on-device.md`）。SP-313 問答 Q1 第 2 題已定：裝置端解析 FIT 用 Garmin JS SDK，Pyodide 只在要跑 Python 模型時才載入。這份只回答「Python 模型在手機瀏覽器跑要多久、能不能跑」。

---

## 0. 結論先講

1. **跑得起來，答案也一樣。** 後端原本的 Python（131 個模組、約 6.4 萬行）不改一行，就能在 Pyodide 314.0.7（Python 3.14.2）裡跑出單次活動判讀（`workout_review`）的六張卡片，和 Mac 上 CPython 的結果逐欄位比對 **0 處不同**（18 張卡片，誤差容許 1e-9）。需要的套件 Pyodide 都有（numpy、SQLAlchemy、sqlite3），只有 `fitdecode` 要自己打包（純 Python，可以）。
2. **但「單次活動判讀」其實不是單筆的工作。** 它要先建好整個運動員的 Dataset：門檻估算、每筆活動的 mean-max 曲線（260 筆全算）、拿這筆跟過去同類課比較。這些在伺服器上是存在硬碟的快取，**到了瀏覽器全部用不上**（§6.1：快取的版本號是用 Python 位元組碼算的，伺服器 3.11 跟 Pyodide 3.14 算出來不一樣），所以裝置第一次要自己從頭算。
3. **第一次打開要很久。** 打開到看到第一筆活動的判讀：桌面 Chrome 約 1 分鐘，模擬中階手機（CPU 慢 4 倍）約 4.5 分鐘，模擬較慢的手機（慢 6 倍）約 6.5–7.5 分鐘。FIT 還沒解析過（用 `fitdecode`）的話，模擬中階手機要 10.5 分鐘。這是示範資料一年 260 筆；擁有者 3 年約 800 筆，建 Dataset 那段大約再乘 3（推估）。Pyodide 比同一台 Mac 上的 CPython 慢 2.6–3.5 倍。
4. **記憶體是最大的問題。** 判讀一筆一小時的跑步，wasm 記憶體就長到約 460 MB，判讀三筆到 846 MB，瀏覽器分頁行程峰值 1.1–1.3 GB；wasm 記憶體只會長不會縮。iPhone 分頁能用多少記憶體官方沒公布（`fit-on-device.md` §5），1.3 GB 很可能會被系統砍掉（推估）。
5. **裝置有了自己的快取之後快很多，但還是不快**（§4.3）：同一台裝置第二次打開（沒有新活動），看到第一筆判讀：桌面約 9 秒、模擬中階手機約 25 秒、較慢手機約 38 秒；記憶體一樣高（判讀一筆 wasm 就到約 460 MB）。前提是那份快取沒被瀏覽器清掉（iPhone Safari 沒加到主畫面時 7 天沒開會被刪，SP-319）。
6. **另一個代價：計算核心會整包送到每個使用者的裝置。** 跑到的 131 個模組裡有 24 個（512 KB 原始碼）在公開鏡像的核心排除清單上（`deploy/public-mirror/exclude.txt`）。放進瀏覽器就等於公開。
7. **建議：模型繼續在伺服器跑，裝置只放伺服器算好的結果（SP-314 的離線唯讀快取）。** 現在不值得投資「手機跑 Python 模型」。如果之後真的要裝置算（例如離線也要看新活動的判讀），要先做兩件事：判讀改成「伺服器送運動員背景資料、裝置只算這一筆」，以及快取版本號改成不綁 Python 版本。細節與選項見 §7。

---

## 1. 要跑的到底是什麼

| 項目 | 說明 | 標記 |
|---|---|---|
| 入口 | `GET /workouts/{i}/review` → `_dataset()` 拿整個運動員的 Dataset → 六個 SECTIONS 各呼叫一次 `workout_review.review(ds, w, section)`（`backend/api/wko5views.py:1521-1543`、`backend/engine/workout_review.py:228`、`:2893`） | 已驗證 |
| Dataset 從哪來 | COROS 等 FIT 資料夾走 `dataset_for_source` → `FitFolderDataset`（`backend/engine/wko5expr/fitdataset.py:1596-1611`）。建的時候要估門檻（`_estimate_settings`，`fitdataset.py:1102`），裡面的 PD 模型要讀每筆活動的 mean-max 曲線（`dataset.py:1070` 的 `_workout_curves` → `wko5_meanmax.py:62` `_fast_meanmax`）| 已驗證（程式碼＋cProfile） |
| 判讀要讀別的活動 | 第一次判讀會拿這筆跟以前同類的課比較：間歇的同類課（`interval_eval.py:697` `peers`）、門檻信心要整理所有跑步（`threshold_confidence.py:898` `gather_runs`）、刺激分數讀運動員範圍的數值（`workout_review.py:3077` `tis_scores`）| 已驗證（cProfile） |
| 這次怎麼跑 | 不經過 FastAPI：直接呼叫 `dataset_for_source(...)`（hot-first 關掉，全部活動）再呼叫 `review`。量測腳本 `docs/research/model-bench/model_run.py`，CPython 和 Pyodide 跑同一個檔 | 已驗證 |

**cProfile（Mac CPython，FIT 已解析、其他快取都沒有）**：建 Dataset 13.4 秒裡有 12.1 秒在算 260 筆活動的 mean-max 曲線；第一筆判讀 10 秒裡，同類課比較（`interval_eval.peers`）4.1 秒、HR 區間與門檻信心（`hr_profile.easy_cap_for` → `threshold_confidence.check`）3.1 秒、`tis_scores` 2.7 秒。**真正只跟這一筆有關的計算很少**：第二次打開同一筆（記憶體裡都有了）只要 0.2–0.5 秒。

---

## 2. 依賴：哪些在 Pyodide 跑不了

### 2.1 會用到哪些程式

`docs/research/model-bench/import_closure.py` 靜態追 `import`：

| 範圍 | backend 模組 | 原始碼 | 第三方套件 | 標記 |
|---|---|---|---|---|
| 只算檔案最上層的 import（import 當下就要） | 32 | 997 KB | 只有 numpy | 已驗證 |
| 加上函式裡面的 import（上限，幾乎是整個 backend） | 319 | 7.0 MB | numpy、SQLAlchemy、FastAPI、Starlette、pydantic、httpx、anyio、fitdecode、fitparse、cryptography、authlib、garminconnect… | 已驗證 |
| **實際跑一次用到的**（CPython 跑完看 `sys.modules`） | **131** | **3.1 MB，約 6.4 萬行** | numpy、SQLAlchemy、fitdecode，以及 FastAPI 一整套（見下面） | 已驗證 |

為什麼會碰到 FastAPI、httpx 這些網頁伺服器的套件（`model_run.py --who` 記錄是誰 import 的）：

- **快取版本號的計算**：`engine/codehash.py` 為了算「這個快取是哪一版程式算的」，會沿著函式讀到的名字去 `import_module`（`codehash.py:179-196`），一路帶進 `backend.membership`（檔頭就 `from fastapi import HTTPException`，`membership.py:54`）、`backend.accounts.google`（httpx）等。import 失敗會被吞掉（`codehash.py:188-189`），所以**沒有這些套件也不會出錯**。
- **讀設定**：`datasource._db_path` 透過 `backend.db.database` 拿 DB 路徑，那個檔頭 import SQLAlchemy（`database.py:14-15`）。**這裡要小心**：import 失敗會被吞掉、回傳 `None`（`datasource.py:28-33`），結果是「讀不到設定、全部用預設值」，**不會報錯但答案會變**（推估：這次有載 SQLAlchemy，沒有實際跑過缺它的情況）。
- **FIT 快取**：`fitcache` 解析 FIT 用 `fitdecode`（`fitcache.py:107`）。

### 2.2 Pyodide 有沒有

Pyodide 314.0.7 的套件清單（`pyodide-lock.json`，Python 3.14.2，357 個套件）＋在瀏覽器實測（`bench-worker.js` 的 `probe` 與 `caps`）：

| 東西 | Pyodide 314.0.7 | 這條路要不要 | 標記 |
|---|---|---|---|
| numpy | 有，2.4.6（和 venv 同版） | 要 | 已驗證 |
| SQLAlchemy | 有，2.0.48；`sqlalchemy.ext.asyncio` import 成功 | 要（讀設定，見上） | 已驗證 |
| sqlite3 | 標準庫裡就有，能開 DB 查詢 | 要 | 已驗證 |
| greenlet | **沒有** | 不要：只有非同步 DB 引擎真的連線時才用；這條路是同步讀 sqlite | 已驗證（清單）；用不到是這次跑成功證實 |
| fitdecode | **沒有**，但它是純 Python，打包成 zip 解開就能用（SP-313 已證實） | FIT 沒解析過時要 | 已驗證 |
| FastAPI、pydantic、Starlette、httpx、anyio | 有 | 不要：不載也能跑，結果一樣（§6.1 的雜湊也一樣） | 已驗證 |
| 開 thread、開 process pool | 不行，見 §2.3 | `fitcache` 會先試 process pool，失敗就改在同一條執行緒解析（`fitcache.py:524-531`） | 已驗證 |
| 背景工作（`jobs`、`backfill`、排程） | — | 不要：直接呼叫 `dataset_for_source`、hot-first 關掉就不會碰到 | 已驗證 |

### 2.3 Pyodide 做不到的事（實測）

在 Pyodide 裡直接試（`bench-worker.js` 的 `caps`，每一組都一樣）：

| 試什麼 | 結果 | 後端哪裡會用到 | 標記 |
|---|---|---|---|
| 開一條 thread（`threading.Thread(...).start()`） | `RuntimeError: can't start new thread` | 伺服器的背景工作（`jobs`、`backfill`、排程）。這條路沒有用到；`threading.Lock` 照常可用 | 已驗證 |
| 開 process pool（`ProcessPoolExecutor`） | `NotImplementedError: This Python build lacks multiprocessing.synchronize…` | `fitcache` 平行解析 FIT（`fitcache.py:560-569`）。失敗會被接住、改成一個一個解析（`fitcache.py:524-531`），cold 那組就是這樣跑完的 | 已驗證 |
| sqlite3 開 DB、查詢 | 可以 | 讀設定（`datasource.read_setting`） | 已驗證 |

**結論：模型依賴裡沒有 Pyodide 跑不了的東西。** 跑不了的（開 thread、開 process）不在單次判讀這條路上，或已經有退路。只有一個要記住的陷阱：缺 SQLAlchemy 時不會報錯、而是靜靜地用預設設定（§2.1）。

---

## 3. 下載量與啟動時間

**下載**（第一次開，Chromium 的 `performance` resource 紀錄，壓縮後／解壓後）：

| 東西 | 壓縮後 | 說明 | 標記 |
|---|---|---|---|
| Pyodide 本體（`pyodide.asm.wasm`、`python_stdlib.zip`、`pyodide.asm.mjs`、lock 檔） | 6.1 MB | 和 SP-313 一樣 | 已驗證 |
| numpy 2.4.6 | 2.8 MB | | 已驗證 |
| SQLAlchemy 2.0.48＋typing-extensions | 1.9 MB | 只為了讀設定；改成直接用 sqlite3 就能省掉（推估） | 已驗證 |
| **Pyodide＋套件合計** | **10.7 MB**（解壓後 17.7 MB） | | 已驗證 |
| 後端程式 `backend.zip`（全部 .py 與資料檔，不含 tests、static） | 3.3 MB（解壓後 9.9 MB） | | 已驗證 |
| 只放跑到的 131 個模組 `backend-runtime.zip` | 1.4 MB（解壓後 3.1 MB） | | 已驗證 |
| `fitdecode.zip` | 0.1 MB | | 已驗證 |
| **合計** | **約 12–14 MB** | 10 Mbps 行動網路約 10–12 秒（推估）；之後瀏覽器會快取 | 已驗證（量）／推估（網速） |

資料夾（FIT、DB、快取）這次是用 zip 從本機送進去的，不算進下載量：真的做時資料應該已經在裝置上（OPFS），或由伺服器同步。

**啟動時間**（第一次開；每個設定量了 2–3 次（warm／fit／cold 各一次），寫範圍）：

| 設定 | 載 Pyodide | 載 numpy、SQLAlchemy | 解開程式 | import 模型 | 算快取版本號 |
|---|---|---|---|---|---|
| Mac CPython（對照） | — | — | — | 0.04 秒 | 0.5 秒 |
| 桌面 Chrome（worker） | 1.4 秒 | 0.6 秒 | 0.2–0.3 秒 | 0.25–0.3 秒 | 1.3–1.4 秒 |
| 模擬中階手機（慢 4 倍） | 3.5–3.8 秒 | 0.9–1.1 秒 | 0.7–1.2 秒 | 1.0–1.3 秒 | 5.2–5.7 秒 |
| 模擬較慢手機（慢 6 倍） | 5.3–5.5 秒 | 1.2–1.3 秒 | 1.8–1.9 秒 | 1.8–2.0 秒 | 8.6–8.8 秒 |

- 桌面約 3.9 秒、模擬中階手機約 12 秒、較慢手機約 19 秒才開始算。光是「算快取版本號」（`codehash`：把會用到的函式的位元組碼全部讀一遍）在模擬手機上就要 5–9 秒。
- Pyodide 不能用 CPython 事先編好的 `.pyc`，每次都從原始碼編譯（推估：Pyodide 版本不同，`.pyc` 不能共用；這次沒試把 Pyodide 自己編的 `.pyc` 存起來）。

---

## 4. 跑模型要多久

### 4.1 怎麼量

- **測試頁**：`docs/research/model-bench/`（`index.html`＋module worker `bench-worker.js`，沿用 `fit-parse-bench/` 的伺服器、模擬設定和記憶體抽樣）。可以重跑，步驟見 §8 與各檔開頭的說明。
- **資料夾三種狀態**（`make_bundle.py`）：
  - **warm**：伺服器跑過一次後的資料夾，所有快取都在（模擬「伺服器把快取交給裝置」）。
  - **fit**：只有 FIT 和每個 FIT 解析好的逐秒資料（`cache/fit/<home>/index.json`＋`ch/*.npz`），沒有門檻估算和其他快取（模擬「裝置自己解析過 FIT」）。
  - **cold**：只有 FIT，裝置要自己用 `fitdecode` 全部解析。
  - **steady**：同一個瀏覽器先跑一次 warm，把跑完的資料夾（含 Pyodide 自己寫的快取）存進 OPFS，再開新頁面量（模擬「同一台裝置第二次打開」）。
- **挑的活動**（近 90 天、照逐秒筆數）：中位數 `demo_0254.fit`（跑步 3,720 秒，輕鬆跑）、第 90 百分位 `demo_0259.fit`（跑步 8,922 秒，有氧門檻測試）、最長 `demo_0249.fit`（健行 29,077 秒）。依序判讀。每筆判讀兩次：第一次（「首次」）＋馬上再一次（「再開」，記憶體裡都有了）。示範資料近 90 天沒有間歇課，所以沒有間歇那一類。
- **環境**：Mac（Apple M5 Pro，18 核心），Playwright 1.63 的 Chromium 153 headless。模擬手機＝Pixel 7 螢幕和 user agent＋DevTools CPU 降速 4 倍／6 倍；**降速對 worker 沒有作用，所以模擬手機那幾組在主執行緒跑**（同 SP-313）。`chromium-desktop-main` 是沒降速、在主執行緒跑的對照，數字和 worker 差不多。Playwright 的 Firefox 版本對不上、WebKit 沒裝，**沒有 Safari 引擎的數字**。
- **CPython 對照**：`run_cpython.py`，同樣三種狀態，各跑兩次。
- **結果正確性**：`compare.py` 把每張卡片的每個欄位跟 CPython 比，**全部 0 處不同**（容許誤差 1e-9；Pyodide 桌面 warm／cold／steady、模擬 4 倍 cold／steady、模擬 6 倍 fit／steady 都比過，每組 18 張卡片）。

### 4.2 第一次打開（裝置上沒有自己的快取）

「建 Dataset」＝門檻估算＋每筆活動的曲線；「第一筆」＝第一筆活動的六張卡片（含要讀其他活動的那些，§1）；「之後每筆」＝後面兩筆活動的首次判讀；「再開」＝同一筆第二次。

| 設定 | 資料夾 | 建 Dataset | 第一筆 | 之後每筆 | 再開 | 打開到看到第一筆（含啟動） |
|---|---|---|---|---|---|---|
| Mac CPython | warm | 0.07 秒 | 1.2 秒 | 0.5–0.6 秒 | 0.2–0.4 秒 | 約 1.8 秒 |
| Mac CPython | fit | 11.7 秒 | 7.4 秒 | 1.1–2.3 秒 | 0.2–0.6 秒 | 約 20 秒 |
| Mac CPython | cold | 18.6 秒 | 7.0 秒 | 1.0–2.3 秒 | 0.2–0.5 秒 | 約 26 秒（process pool 平行解析） |
| 桌面 Chrome（worker） | warm | 52.8 秒 | 22.8 秒 | 2.7–6.8 秒 | 0.4–1.1 秒 | 約 1.3 分 |
| 桌面 Chrome（worker） | fit | 41.4 秒 | 19.3 秒 | 2.6–6.5 秒 | 0.4–1.0 秒 | 約 1.1 分 |
| 桌面 Chrome（worker） | cold | 2.1 分 | 19.1 秒 | 2.6–6.5 秒 | 0.4–1.0 秒 | 約 2.5 分 |
| 桌面 Chrome（主執行緒） | warm／fit | 40–41 秒 | 18–19 秒 | 2.2–6.5 秒 | 0.2–1.0 秒 | 約 1.1 分 |
| 模擬中階手機（4 倍） | warm | 2.9 分 | 92 秒 | 10–28 秒 | 1.2–4.1 秒 | 約 4.6 分 |
| 模擬中階手機（4 倍） | fit | 2.8 分 | 79 秒 | 10–27 秒 | 1.1–4.1 秒 | 約 4.3 分 |
| 模擬中階手機（4 倍） | cold | 8.9 分 | 79 秒 | 10–27 秒 | 1.0–4.0 秒 | 約 10.5 分 |
| 模擬較慢手機（6 倍） | warm | 4.3 分 | 3.1 分 | 22–44 秒 | 1.7–6.9 秒 | 約 7.7 分 |
| 模擬較慢手機（6 倍） | fit | 4.2 分 | 1.9 分 | 14–40 秒 | 1.6–6.0 秒 | 約 6.4 分 |

- **warm 和 fit 在瀏覽器裡幾乎一樣**（warm 還略慢，是雜訊）：伺服器的快取到了 Pyodide 一個都用不上（§6.1）。只有「FIT 解析好的逐秒資料」那層用得上，因為它的版本號是用原始碼文字算的（`fitcache.py:90-99`、`:115`），不是位元組碼。
- **Pyodide 比 Mac CPython 慢約 2.6–3.5 倍**（建 Dataset 41 秒 vs 11.7 秒；第一筆 19 秒 vs 7.4 秒）。SP-313 量的解析 FIT 是 1.7–1.9 倍；這裡比較慢，推估是因為 mean-max 這類 Python 迴圈比較多（`wko5_meanmax.py:116` `window_avg` 一個函式就佔 5 秒）。
- cold 比 fit 多出來的就是用 `fitdecode` 解析 260 個 FIT：桌面約 80 秒、模擬中階手機約 6 分鐘，跟 SP-313 的量測一致。照 SP-313 的定案，裝置端解析應該改用 Garmin JS SDK（快約 40 倍），那這段可以縮到幾秒；但解析結果要轉成後端的逐秒格式（npz），這次沒做（推估）。
- 擁有者有 3 年約 800 筆活動，是示範資料的 3 倍。建 Dataset 主要時間跟活動數成正比（每筆都要算曲線），**擁有者大約要再乘 3**（推估）。

### 4.3 同一台裝置第二次打開（裝置有自己的快取）

先在同一個瀏覽器跑一次（約 1.5 分鐘），把跑完的資料夾——連同 Pyodide 自己寫的快取——存進 OPFS（約 60 MB），再開新頁面從 OPFS 讀回來量。Pyodide 和套件從瀏覽器的 HTTP 快取拿（沒有網路傳輸）。三筆活動在第一次那頁都判讀過。

| 設定 | 啟動到可以算（載 Pyodide＋套件＋解開＋import＋版本號） | 建 Dataset | 第一筆 | 之後每筆 | 再開 | 打開到看到第一筆 |
|---|---|---|---|---|---|---|
| 桌面 Chrome（worker） | 4.1 秒 | 0.3 秒 | 4.1 秒 | 2.0 秒 | 0.6–1.6 秒 | **約 9 秒** |
| 桌面 Chrome（主執行緒） | 2.8 秒 | 0.2 秒 | 2.2 秒 | 1.0–1.2 秒 | 0.3–1.0 秒 | 約 5 秒 |
| 模擬中階手機（4 倍） | 13.5 秒 | 0.9 秒 | 10.5 秒 | 4.6–5.1 秒 | 1.2–4.2 秒 | **約 25 秒** |
| 模擬較慢手機（6 倍） | 20.2 秒 | 1.4 秒 | 16.3 秒 | 7.3–8.0 秒 | 2.0–6.6 秒 | **約 38 秒** |

- 自己的快取有用：建 Dataset 從幾分鐘變成 1 秒內。
- 但**第一筆判讀還是要 10–16 秒**（模擬手機），因為它仍然要讀其他活動的逐秒資料來比較（§1、§6.2），那些不在快取裡、每次開頁都要重讀。
- 啟動本身（Pyodide＋算快取版本號）在模擬手機上就要 13–20 秒，其中版本號 6–9 秒。
- 這是「沒有新活動」的情況。來了一筆新活動時，伺服器現在的做法是整份重建（`fit-on-device.md` §9.5、SP-320），裝置上也一樣，會回到 §4.2 的量級，除非 SP-320 的增量重算做好（推估）。
- 前提是 OPFS 沒被清掉。iPhone Safari 沒加到主畫面時 7 天沒開會被刪（SP-319），被刪就回到 §4.2。

---

## 5. 記憶體

| 設定 | 資料夾 | 峰值 | 標記 |
|---|---|---|---|
| Mac CPython | warm | 最大 RSS 201 MB | 已驗證 |
| Mac CPython | fit／cold | 最大 RSS 423 MB | 已驗證 |
| 桌面 Chrome 與模擬手機（全部組合） | warm／fit／cold | **wasm 記憶體 846 MB**；分頁 renderer 行程 RSS 峰值 1.24–1.34 GB（開頁後、開跑前約 104 MB） | 已驗證 |
| 桌面與模擬手機，steady（裝置自己的快取） | — | **wasm 記憶體一樣 846 MB**；renderer RSS 峰值 1.12–1.13 GB | 已驗證 |

**記憶體是在哪一步長上去的**（wasm 記憶體大小，`model_run.py` 的 `mem`；Pyodide 的 wasm 記憶體是一段一段長的，所以數字會落在幾個固定值上）：

| 階段 | warm（第一次） | steady（第二次打開） |
|---|---|---|
| 開始（Pyodide＋numpy＋SQLAlchemy 載好） | 43 MB | 43 MB |
| import 模型 | 51 MB | 51 MB |
| 算快取版本號 | 89 MB | 89 MB |
| 建 Dataset | 129 MB | 89 MB |
| 判讀第一筆（中位數，跑步 1 小時） | **464 MB** | **464 MB** |
| 判讀第二筆（第 90 百分位，2.5 小時） | 845 MB | 749 MB |
| 判讀第三筆（最長，健行 8 小時） | 845 MB | 845 MB |

- **建 Dataset 本身不吃記憶體，是判讀吃的**：光判讀一筆一小時的跑步，wasm 記憶體就到 464 MB，因為判讀要把拿來比較的其他活動的逐秒資料讀成 Python list（每筆每個欄位約 32 bytes，`fitcache.py:759-763` 的說明；同時最多開 12 個檔，`fitcache.py:768`）。
- 有沒有自己的快取（warm vs steady），判讀時的記憶體都一樣。
- **WebAssembly 的記憶體只能長、不能縮**（規格上沒有縮小的操作；摘要），所以峰值就是這一頁之後一直佔著的量。這次每個階段量到的值也都沒變小（已驗證）。
- 1.3 GB 裡另外一部分是資料夾本身（解開放在 Pyodide 的記憶體檔案系統，FIT＋逐秒資料約 140 MB）和送進去的 zip。
- iPhone 分頁的記憶體上限官方沒公布（未找到來源）。**判讀一筆就要約 460 MB、三筆到 850 MB 的 wasm 記憶體，在手機上很可能跑不完**，尤其是較舊的手機（推估）；這要真手機才知道（§8）。
- 桌面的降速模擬只降 CPU，不會模擬手機的記憶體上限，所以這次的模擬手機都跑完了，不代表真手機跑得完。

---

## 6. 為什麼在瀏覽器裡這麼慢

### 6.1 伺服器的快取到了 Pyodide 全部失效

伺服器的大部分硬碟快取（每筆活動的曲線、門檻估算、series 快取…）用「算出這個值的程式碼」的雜湊當版本號：`engine/codehash.py` 把會用到的函式的**位元組碼**（`co_code`、名稱、常數）雜湊起來（`codehash.py:157-167`），程式一改快取就自動失效（SP-320、SP-341）。位元組碼每個 Python 小版本都不一樣，伺服器是 CPython 3.11，Pyodide 314.0.7 是 3.14.2：

| 快取 | Mac CPython 3.11 | Pyodide（Python 3.14.2） |
|---|---|---|
| `estimate`（門檻估算） | `fca4ef6bb2e8e967` | `b833d72452862396` |
| `workout_curves`（每筆曲線） | `7be69abe15663547` | `ded5c620fc8c3b04` |
| `channel_peaks` | `b468a831a308d4d5` | `51813891e1b47e78` |

（`model_run.py` 的 `code_hashes`；Pyodide 有沒有載 FastAPI 一套，雜湊都一樣——差別只在 Python 版本。已驗證。）

所以就算伺服器把整個資料夾連快取一起給裝置，裝置還是要全部重算。要讓兩邊共用快取，得讓伺服器和 Pyodide 用同一個 Python 小版本，或改成不靠位元組碼的版本號（例如原始碼文字，像 `fitcache._h` 那樣，`fitcache.py:90-99`）。後者會讓「改了註解也要重算」回來，那是 SP-320 ① 刻意拿掉的（`codehash.py:3-6`）。

### 6.2 「單筆判讀」要整個運動員的歷史

§1 的 cProfile：建 Dataset 九成時間在算全部活動的曲線；第一筆判讀八成時間在讀其他活動（同類課比較、門檻信心、刺激分數）。只看這一筆的部分不到 1 秒（CPython）。

### 6.3 Pyodide 本身慢約 3 倍

同樣的程式、同樣的資料，Pyodide 比 CPython 慢 2.6–3.5 倍（§4.2）；手機 CPU 再慢 4–6 倍。兩個乘起來，Mac 上 20 秒的工作在中階手機上要 4–5 分鐘。

---

## 7. 對 SP-319 的意義：選項

| 選項 | 做法 | 第一次打開（模擬中階手機） | 之後 | 代價 | 標記 |
|---|---|---|---|---|---|
| **A. 模型留伺服器（現在的做法）** | 伺服器算好判讀，裝置只存結果（SP-314 的離線唯讀快取） | 跟看網頁一樣 | 一樣 | 沒有網路時看不到「新活動」的判讀（同步之前本來也沒有新活動的 FIT） | 推估 |
| B. 整套 Python 搬到瀏覽器 | 這次量的做法 | 約 4.5 分（擁有者資料量再 ×3），記憶體 1.1–1.3 GB | 沒有新活動時約 25 秒看到第一筆；有新活動時回到第一次的量級（SP-320 做好之前） | 下載 12–14 MB；手機記憶體可能撐不住；計算核心整包公開；瀏覽器清掉資料就要重算 | 已驗證（模擬）；手機撐不撐得住待真機 |
| C. 伺服器送「運動員背景」，裝置只算這一筆 | 把判讀拆成兩段：門檻、同類課摘要、刺激分數的歷史由伺服器算好送來；裝置只對新活動跑跟它自己有關的部分 | 推估在中階手機上幾秒（CPython 只看這一筆 < 1 秒 × Pyodide 3 倍 × 手機 4 倍） | 同左 | 要重構 `workout_review`（4,837 行）把「歷史」與「這一筆」分開；核心一樣會送到裝置 | 推估 |
| D. 改寫成 JS／TS | 判讀改用 JS 在裝置算 | 快 | 快 | 兩份模型要一起維護，會越差越遠（SP-307 §7） | 推估 |

**建議 A。** 理由：
- 裝置端跑模型要解決的問題（沒網路時看新活動的判讀）其實不存在：新活動的 FIT 本來就是伺服器從 COROS 抓下來的（`fit-on-device.md` §1、§4.2），沒網路時裝置也拿不到新的 FIT。
- SP-319 已經算過：把 FIT 搬到裝置幾乎省不到錢，真正的成本是伺服器運算（每個新活動整份重建）——那是 SP-320（快取分級與增量重算）要解的，不是把運算搬到手機。
- B 在手機上第一次要好幾分鐘、記憶體可能撐不住，而且會把計算核心公開。
- 真的要做離線判讀時再考慮 C，並先解決 §6.1 的快取版本號。

---

## 8. 真手機（要請使用者跑）

和 SP-313 的真機測試一起跑（SP-313 問答 Q2）。步驟：

1. Mac 和手機連同一個 Wi-Fi。在 repo 先建示範資料與測試檔（只需要一次）：
   ```
   .venv/bin/python -m backend.demo.build --root /tmp/trc-demo --anchor 2026-10-03 --no-warm
   .venv/bin/python docs/research/model-bench/make_bundle.py --demo /tmp/trc-demo/base/2026-10-03-20261002
   ```
2. 啟動測試頁：`.venv/bin/python docs/research/model-bench/serve.py`（http 就可以量時間；要測「第二次打開」要加 `--https`，手機會跳一次憑證警告，處理方式同 SP-313）。
3. 手機開終端機印的 `http://<Mac 的 IP>:8766/`，設定用預設（numpy＋SQLAlchemy、整個 backend、warm、Web Worker），按「開始」。螢幕保持亮著，模擬中階手機要 5 分鐘左右。要測第二次打開：第一次勾「跑完把資料夾存到 OPFS」，跑完重新整理，資料夾選「opfs」再按一次。
4. 跑完按「複製結果」貼回單上；分頁中途重新整理或當掉，就把當掉前看到的進度文字貼回來——**當掉本身就是答案**（記憶體不夠）。

要看的數字：`run.dataset_s`、`run.reviews.*.cold.total_s`、是否當掉。

---

## 9. 要使用者決定的事與建議另開

- 要不要接受 §7 的建議 A（模型留伺服器，現在不做手機跑 Python 模型）。
- 真手機要不要測（照 §8，和 SP-313 一起）。如果選 A，真手機的數字只是參考，不影響決定。
- 建議另開（不在這張做）：
  - 讀設定改成直接用 `sqlite3`、不經 SQLAlchemy：缺 SQLAlchemy 時現在會靜靜地用預設值（§2.1），而且裝置端可以少下載 1.9 MB。只有選 B 或 C 才需要。
  - 快取版本號不綁 Python 版本（§6.1）：伺服器升級 Python 時，現在也會讓全部快取失效、整份重算——**這點跟手機無關，伺服器升級 Python 版本時就會碰到**（推估：同一個機制）。

---

## 附錄：檔案與重跑

| 檔案 | 做什麼 |
|---|---|
| `docs/research/model-bench/import_closure.py` | 靜態追 import（§2.1） |
| `docs/research/model-bench/model_run.py` | CPython 與 Pyodide 共用的量測：建 Dataset、判讀、記錄模組與快取版本號 |
| `docs/research/model-bench/make_bundle.py` | 產生 `fixtures/`（後端 zip、`fitdecode.zip`、三種資料夾） |
| `docs/research/model-bench/run_cpython.py` | CPython 對照 |
| `docs/research/model-bench/index.html`、`bench-worker.js` | 測試頁與 Pyodide worker |
| `docs/research/model-bench/run_playwright.py` | 桌面 Chromium 與模擬手機（沿用 `fit-parse-bench/run_playwright.py` 的設定與記憶體抽樣） |
| `docs/research/model-bench/compare.py` | 卡片逐欄比對 |
| `docs/research/model-bench/summarize.py` | 把 `results/` 整理成表 |

`fixtures/`、`results/` 不進 git（`.gitignore`）。Pyodide 套件清單：`https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide-lock.json`。
