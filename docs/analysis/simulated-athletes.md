# 模擬學員：用公開資料或合成資料做出多位測試學員（SP-125）

2026-10-05，分支 `docs/sim-athletes-sp125`（從 `12678bf` 開出）。只做調查和提案，**沒有改程式**。

單據：SP-125「新增模擬學員，測試用」。需求原文：research 文件提到有公開的跑者訓練資料，想拿來測試教練功能，可以看到各學員的 dashboard。這張單 blocks SP-56「教練能看到學員的跑步資料」。

標記：**已驗證** = 2026-10-05 實際打開官方頁面或 API 確認；**推估** = 沒有直接證據，是從程式或既有文件推出來的。

---

## 0. 結論先講

1. **能合法拿來做「看得到 dashboard 的模擬學員」的公開資料只有一份：GoldenCheetah OpenData（CC0）。** 它有逐秒的心率、功率、步頻、海拔，但**沒有 GPS**、不是 FIT 檔（是 CSV），資料年份停在 2007–2020，而且 96% 是男性、多數是路跑。其他資料不是只有彙總（沒有逐秒資料，畫不出 dashboard），就是授權不允許（FitRec 只限學術、PhysioNet 只限研究、爬蟲資料的上傳者沒有權利授權）。
2. **app 現在沒有任何多學員或教練畫面**，也沒有「一個人看別人資料」的權限概念。多租戶只有三種：owner（自架的本人）、demo base（共用的示範跑者）、demo sandbox（每位訪客的寫入副本）。`user` 只是保留字，沒有實作。
3. **把一個資料夾的 FIT 變成一位可看的學員，程式已經會做**：demo 的建置流程（`backend/demo/build.py`）就是「產生 FIT → 寫 `plan.json` → `scan_and_import` 匯入 DB → 預熱圖表」。模擬學員只要換掉「產生 FIT」這一步。
4. **最大的限制是記憶體**：同一個行程最多同時留 2 個 Dataset（`DATASETS_MAX = 2`，`backend/api/wko5views.py:82`）。切到第 3 位學員就會丟掉一個，切回來要重建。放在本人那台 3 GB 上限的 NAS 容器裡，會把本人的 Dataset 也擠掉。**建議另開一個獨立的「測試實例」**，不要和本人的 app 共用行程。
5. 建議順序（推估）：先做 (c) 合成學員 3–5 位＋(b) 最小的切換頁，約 **5–7 個工作天**，就能試教練畫面；之後如果要「真實的雜亂資料」，再加 (a) GoldenCheetah 轉檔，約 **4–5 個工作天**。

---

## 1. 研究文件提到的公開資料集

來源：`docs/research/public-datasets.md`（2026-10-01 調查）、`validation-goldencheetah.md`、`validation-lovdal.md`。授權在 2026-10-05 重新查了一次官方頁面或 API。

「能不能當模擬學員」的判斷標準：要有**每次活動的逐秒資料**（至少心率＋速度或距離，最好有海拔和功率），而且**授權允許再散布**（測試實例如果放上網，等於散布資料）。

### 1.1 總表

| 資料集 | 內容 | 大小 | 授權 | 下載 | 海拔／越野 | 能當模擬學員？ |
|---|---|---|---|---|---|---|
| **GoldenCheetah OpenData** | 每人一個 zip：一個 JSON（每次活動的運動種類、日期、GC 算好的指標、體重）＋每次活動一個 CSV `secs,km,power,hr,cad,alt`，**逐秒**。沒有 GPS、沒有溫度、不是 FIT | 6,614 個運動員 zip（已驗證，OSF API）；總共 114.8 GB（`validation-goldencheetah.md` §1.1，2026-10-02 量的）；每人 0.3 KB～40 MB 以上 | **CC0 1.0**（已驗證，OSF API license 物件）。可商用、可再散布、不用署名 | https://osf.io/6hfpz/ （DOI 10.17605/OSF.IO/6HFPZ） | 有逐秒海拔；沒有越野標記。之前抽的 358 位跑者、32,617 次跑步裡，每公里爬升 ≥ 20 m（app 的越野門檻）的有 6,120 次，約 19% | **可以，唯一一份** |
| Lövdal 2021（荷蘭競技跑者） | 每日彙總：總 km、Z3-4 km、Z5 km、衝刺 km、肌力、交叉訓練時數、主觀疲勞，加受傷標記。**沒有逐秒資料** | 74 人 × 7 年；4 個檔共約 28.7 MB（已驗證，DataverseNL API：13.5 MB＋15.2 MB＋README＋notebook） | **CC0 1.0**（已驗證，DataverseNL API） | https://doi.org/10.34894/UWU9PV | 沒有 | 不能直接用。但可以借它的「每天跑多少、強度多少」當合成學員的訓練節奏（見 §4.3） |
| 4TU 多感測器跑步 | 12 人 × 3 次（耐力跑、間歇、5K），IMU＋心率帶＋GPS 錶；格式 CSV、GPX（已驗證，DataCite） | 單一 zip 7.7 GB（`public-datasets.md`；今天 4TU 網站維護中，沒有重查） | **CC BY 4.0**（已驗證，DataCite）。要署名 | https://doi.org/10.4121/efa64223-ba51-48fa-91be-53f0e48460b4 | GPX 可能有海拔（推估，沒下載確認） | 不能。每人只有 3 次，沒有訓練歷史，dashboard 幾乎是空的 |
| PMData（Simula） | Fitbit 手腕心率、每次運動的彙總、sRPE、主觀狀態、受傷回報 | 16 人 × 5 個月，約 1.4 GB | **CC BY 4.0**（已驗證，頁面 Terms of use） | https://datasets.simula.no/pmdata/ | 沒有海拔串流 | 不能。沒有每次活動的逐秒距離或海拔，而且多數不是跑者 |
| Kerhervé 2016（GNW 越野超馬） | 只有各坡度類別的相對速度彙總表 | 62 KB＋1.5 KB＋兩份 PDF（已驗證，Figshare API） | **CC BY 4.0**（已驗證） | https://doi.org/10.6084/m9.figshare.3369790.v1 | 只有彙總 | 不能 |
| Trinity 漸增負荷測試 | 實驗室測試的心率、攝氧量、乳酸 | `data.zip` 6.7 MB（已驗證，Zenodo API） | **CC BY 4.0**（已驗證） | https://doi.org/10.5281/zenodo.6325735 | 沒有 | 不能。可以拿來設定合成學員的閾值組合（LTHR、最大心率的比例）比較像真人 |
| PhysioNet 跑步機最大測試 | 逐口氣心率、攝氧量 | CSV 6.3 MB | PhysioNet Contributor Review Health Data License 1.5.0（已驗證，頁面），**只限科學研究、不得再分享** | https://doi.org/10.13026/7ezk-j442 | 沒有 | 不能（授權） |
| MMASH（PhysioNet） | 24 小時 RR 間期、睡眠 | — | ODbL（未驗證） | https://physionet.org/content/mmash/1.0.0/ | 沒有 | 不能，沒有跑步 |
| FitRec／Endomondo（UCSD） | 每次訓練 500 點（不等距）的心率、速度、海拔、經緯度 | 約 1,104 人、25 萬次訓練 | 原文 "for academic use only. Please do not redistribute them or use for commercial purposes."（已驗證，UCSD 頁面）。Kaggle 鏡像標的 Apache 2.0 無效 | https://cseweb.ucsd.edu/~jmcauley/datasets/fitrec.html | 有海拔和 GPS | **不能（授權）**。內容其實最適合，可惜禁止再散布 |
| Ultra-marathon 大型成績集（Kaggle） | 只有比賽成績，沒有訓練 | 1.62 GB（已驗證，Kaggle API） | Kaggle 標 CC0（已驗證標示）；原始網站權利未驗證 | https://www.kaggle.com/datasets/aiaiaidavid/the-big-dataset-of-ultra-marathon-running | 沒有 | 不能。頂多參考它設定學員的目標賽事和完賽時間 |
| UTMB world race data（Kaggle） | 比賽成績，爬自 UTMB 網站 | 187 MB | Kaggle 標 MIT，但來源是爬蟲 | https://www.kaggle.com/datasets/mgpoirot/utmb-world-race-daa | 有距離、爬升 | 不能 |
| Afonseca 2022（36,412 人每日跑量） | 每天的距離和時間 | 14 個檔共 536 MB（已驗證，Figshare API） | Figshare 標 CC BY 4.0（已驗證），但資料爬自運動社群網站 | https://doi.org/10.6084/m9.figshare.16620238 | 沒有 | 不能（來源） |
| Boston Marathon（Kaggle） | 成績，含姓名 | — | Unknown | https://www.kaggle.com/datasets/rojour/boston-results | 沒有 | 不能 |
| Matarmaa 2025 單人戶外運動（Kaggle `jarnomatarmaa/sportdata-mts-5`） | 單一業餘者 228 次活動，但每個訊號已經**標準化、重取樣成 69 點** | 4.7 MB（已驗證，Kaggle API） | EU ODP Legal Notice（已驗證，Kaggle API） | https://www.kaggle.com/datasets/jarnomatarmaa/sportdata-mts-5 | 有海拔（標準化過） | 不能。69 點畫不出任何圖 |

另外 2026-10-05 用網路搜尋找「公開、可再散布、逐秒、越野跑 FIT／GPX」的資料集，**沒有找到新的**。這和 `public-datasets.md` §5 的結論一致。

### 1.2 GoldenCheetah 的實際樣本

2026-10-05 下載了 2 個小 zip（0.8 MB、6.9 MB）到 scratch，沒有放進 repo：

- 結構和 `validation-goldencheetah.md` §1.2 說的一樣：`{uuid}.json`（`VERSION`、`ATHLETE`、`RIDES[]`，每筆有 `date`、`sport`、`data`、`METRICS`）＋每次活動一個 CSV。
- 兩位剛好都是騎車的人（40 次 Bike、124 次 Bike＋13 次 ski）。一位只有功率，一位只有心率和海拔。
- 這也說明：**直接隨機挑 GC 運動員，大部分不是跑者**。要先用 `gc_fetch.py scan` 只讀 zip 的目錄和 JSON（不下載整包）挑人。之前的掃描是 1,750 個 zip、讀了 2.18 GB 的 metadata，挑出 358 位跑者。

### 1.3 GoldenCheetah 當模擬學員的缺點

| 缺點 | 影響 | 怎麼補 |
|---|---|---|
| 沒有 GPS | 地圖、路線相關的畫面是空的 | 用 demo 的虛構區域（`backend/demo/athlete.py` 的 `AREAS`）畫一條假軌跡，或乾脆不給座標。越野分類只看每公里爬升（`backend/engine/algorithms/classify.py:11`，≥ 20 m/km），不需要 GPS |
| 不是 FIT | app 只吃 FIT | 用 demo 已有的 FIT 寫入器 `backend/demo/fitwrite.py:222` 的 `encode_activity` 轉檔 |
| 年份是 2007–2020 | dashboard 的「本週」「最近 42 天」會是空的 | 整段日期往後平移，讓最後一次活動落在今天。平移天數取 7 的倍數，星期幾不變 |
| `sport` 欄不可靠（空白、各國語言的 Strava 標題） | 會把騎車當跑步 | 沿用 `validation-goldencheetah.md` §1.3 的篩法：移動中位速度 5–22 km/h、功率中位 < 500 W |
| 不知道功率計是 Stryd 還是手錶 | 功率 TSS 的算法不同 | 一律當「手錶功率」（不寫 Stryd 的 device_info），TSS 走心率。比較保守 |
| 沒有閾值（LTHR、CP） | 區間和 TSS 要閾值 | app 本來就會從資料推估 LTHR（`backend/engine/thresholds.py`）；`plan.json` 的閾值列可以留空，或建置時跑一次推估再寫入 |
| 沒有比賽標記 | 沒有目標賽事，週期化畫面是空的 | 依每人的訓練量和爬升「虛構」一場目標賽事寫進 `plan.json`，名稱標明「模擬」 |
| 96% 男性、多為路跑 | 學員組合不夠多樣 | 優先挑越野比例高的人；女性和百岳型學員用 (c) 合成補 |

---

## 2. app 現在有什麼

以下由子代理逐檔讀程式整理，關鍵行我自己抽查過（`DATASETS_MAX`、`athlete.py`、`tenancy.py`、`sandbox.py`、`build.py`、`generate.py`）。

### 2.1 多租戶

- **用什麼分辨是誰的資料**：只有 cookie。owner 模式（自架的正常 app）每個請求都是同一位 owner，middleware 不做事（`backend/tenancy_mw.py:215-217`）。demo 模式（`WKO5COACH_MODE=demo`）看 `trc_demo` cookie 找訪客的 sandbox，沒有 cookie 就讀共用的 demo base（`tenancy_mw.py:247-248`）。
- **資料放在哪**：`Tenant(id, kind, root, shared, caps, wko5_dir)`（`backend/tenancy.py:53-67`）。
  - `root` 放可寫的：`wko5coach.db`、`plan.json`。
  - `shared` 放大的、幾乎唯讀的：`fit/coros/<年>/*.fit`、FIT 解析快取 `cache/fit`、圖表快取 `cache/render`。
  - owner 的 `root == shared == $WKO5COACH_HOME`（預設 `~/.wko5coach`）。
- **種類**：`owner`、`demo_base`、`demo_sandbox`、`user`。`user` 只是常數，標「later」，**沒有任何地方建立它**（`tenancy.py:13-20`）。
- **沒有租戶清單**。只有 sandbox 能建立和列出（`backend/demo/sandbox.py:136-167`、`:207-211`）。
- **一個安裝只有一位運動員**：`athlete_id` 從環境變數 `WKO5COACH_ATHLETE_ID` 讀，預設 1（`backend/db/current.py:21`）。
- **沒有角色**：只有能力集合 `ALL_CAPS` 和 `DEMO_CAPS = {plan.write, upload.gpx}`（`tenancy.py:42-47`）。帳號系統（`accounts.db`、`users.role owner|user`）只在 `docs/plans/auth-and-demo.plan.md:427-433` 規劃，沒有 coach 角色。

### 2.2 demo 示範跑者怎麼產生

- 體能數字全部是 `backend/demo/athlete.py` 的模組常數：36 歲男性、172 cm、62 kg、CP 255 W（歷史 240 → 255 → 260）、LTHR 168、AeT 150、最大心率 188、安靜心率 50。
- `generate(root, seed, anchor, weeks, small)`（`backend/demo/generate.py:263`）：用固定亂數種子產生 52 週、約 300 次活動，每次一個合成 FIT（`root/fit/coros/<年>/demo_NNNN.fit`），還有 `plan.json`、賽事 GPX、manifest。完全不讀系統時鐘，同樣參數產生同樣結果。
- 逐秒訊號（`backend/demo/signals.py`）：功率目標（CP 的比例）→ Minetti 能量成本算速度 → 手腕光學心率模型 → 氣壓海拔 → GPS 抖動 → 溫度 → Stryd 開發者欄位。
- 季節（`backend/demo/season.py`）：賽事是寫死的（半馬、兩次百岳暖身、百岳三天、移地訓練、50K A 賽、貓空 17K B 賽）；週時數依週期；還有 10 天生病空窗、約 12% 缺課。
- **要做出「不同的學員」目前不容易**：能改的參數只有 `seed`、`anchor`、`weeks`、`small`（`build.py` CLI）。體重、CP、心率、訓練量、爬升率、賽事組合都寫在模組常數，要先抽成一個 profile 物件。好消息是 `generate()` 已經接受任意 `root`，多位學員就是多個資料夾。
- **建置**：`python -m backend.demo.build --root <demo-root>`（`build.py:66`）。在子行程裡以 owner 模式對暫存資料夾跑一遍 app：匯入 FIT（`scan_and_import`，`build.py:195`）、上傳賽事 GPX、預熱所有圖表，最後原子切換 `base/current`。HF 的 demo 映像在 `docker build` 時建一次，要「幾分鐘」。
- **sandbox**：每位訪客一份，第一次寫入時複製 DB 和 `plan.json`（每份 < 3 MB），FIT 和快取共用 base 的。24 小時過期，最多 300 份、總共 1 GiB（`sandbox.py:30-32`）。

`plan.json` 的形狀（`backend/engine/planning.py`）：

```
{ events[]:     id, name, date, kind (race|baiyue|road|other), priority A/B/C, days,
                distance_km, climbing_m, est_hours, cutoff_hours, pack_kg, heat, note ...
  phases[]:     kind, start, end, event_id, note
  thresholds[]: date, lthr, aethr, mhr, rhr, cp, wprime, *_method, note
  weights[]:    date, kg
  profile{}:    sex, height_cm, power_source/power_meter, birth_year }
```

### 2.3 教練／多學員畫面

**完全沒有。**

- 後端和 `backend/static/` 裡沒有任何 roster、切換學員、coach、學員的程式。
- 舊計畫明白延後：「Multi-athlete switching UI — foundation only, Athlete hardcoded」（`docs/plans/done-core-wko5-web-full-clone.plan.md:210`）。
- 唯一和「教練」有關的程式是 TrainingPeaks 登入時解析 `isCoach` 和 `athletes[]`（`backend/sync/tp_client.py:422-434`），沒有拿來看別人的資料。
- 文件裡和教練、商業化有關的：
  - `auth-and-demo.plan.md:576`：「教練看多位學員」這類跨使用者查詢是改用 Postgres 的理由之一，但「目前沒有需求」。現在的設計是一人一個 SQLite＋一個資料夾，跨學員彙總就是逐個打開。
  - `todo-multi-user-sharing.plan.md` §10（2026-10-01 決定）：桌面版免費、只能離線；自動同步和 OAuth 是付費功能，跑在自架 server；**「不做互相分享」**（`:725`）。
  - 沒有任何文件提到「教練方案」。

### 2.4 FIT／GPX 的匯入路徑

- **沒有上傳活動檔的 API。** 現有的 `UploadFile` 只收 GPX 路線（賽事路線 `PUT /plan/events/{eid}/gpx`、racepower 路線、課表範本 GPX）。`import/files`、`import/zip` 只在計畫裡（`auth-and-demo.plan.md:455-458`）。
- **活動只吃 FIT（`.fit`、`.fit.gz`），不吃 GPX。**
- **把 FIT 丟進資料夾，一半會自動生效**：
  - 圖表的 Dataset 會自己掃 `<shared>/fit/<主要來源>/**/*.fit`（`backend/engine/wko5expr/fitdataset.py:533-537`），檔案數量或時間一變就重建。
  - 但 PMC、越野分類、去重、課表比對要 DB 裡的 `workout_files` 和指標，這要跑 `scan_and_import(db, athlete_id, dir)`（`backend/files/file_service.py:104-134`）。這個函式沒有 API，只有 demo 建置在用。
  - 所以做法就是 demo 建置做的那一套：放進 `fit/coros/<年>/`，跑 `scan_and_import`，再預熱。
- **引擎讀的 FIT 欄位**（`backend/files/fit_to_channels.py:60-71`）：`timestamp`、`heart_rate`、`cadence`、`speed`／`enhanced_speed`、`altitude`／`enhanced_altitude`、`power`、`temperature`、`position_lat`／`position_long`、`distance`，以及 session 的 `sport`／`sub_sport`、Stryd 的 `device_info`。GC 的 CSV 有其中的 時間、距離、心率、步頻、海拔、功率，缺 GPS 和溫度。

### 2.5 可以直接沿用的外部資料腳本

- `backend/scripts/validation/gc_fetch.py`：`list`（列出 OSF 上所有 zip，存 md5／sha256）、`scan`（用 HTTP Range 只讀 zip 目錄和 JSON，挑出「≥ 5 次 ≥ 40 分鐘有心率的跑步」的人）、`download`（下載整包並驗證雜湊，有總量上限）。輸出在 repo 外（預設 `~/Datasets/goldencheetah`，這台 Mac 上目前沒有；之前的結果在 Windows 那台）。
- `backend/scripts/validation/gc_drift.py` 的 `parse_csv`（`:84-116`）已經會把 GC 的 CSV 轉成 numpy 陣列、從距離差分算速度。
- 缺的只有「GC CSV＋JSON → FIT」這一段，寫入器可以用 `demo/fitwrite.encode_activity`。

### 2.6 記憶體與磁碟

- **Dataset 快取**：`DATASETS_MAX = 2`，全部模式加起來最多 2 個，用 LRU 淘汰（`backend/api/wko5views.py:82-127`）。key 含 `shared` 路徑，所以**每位學員各佔一格**。每個 Dataset「圖表快取填滿後有數百 MB」（`:90-91`）。commit `b4a15cb` 量到 300 次合成跑步的 RSS 從 248 MB 降到 168 MB。
- **其他快取**：圖表快取磁碟上限 300 MB（**每個 `shared` 一份**，`render_cache.py:45`）、記憶體 32 MB；同時開啟的 FIT 通道檔 12 個。
- **容器上限**：NAS 那台的 3 GB 只出現在 `Dockerfile` 的註解和 commit `b4a15cb`（"NAS container sat at its 3 GB limit"）；NAS 的 compose 檔不在 repo。HF 的 demo 容器是 1.5 GB（`deploy/hf/Dockerfile:6`）。
- **對多位學員的影響**：同時只能留 2 位學員的 Dataset。看第 3 位會丟掉一位，切回來要重建。磁碟上的 FIT 解析快取會讓重建快一些，但還是要幾秒到幾十秒（推估）。

---

## 3. 選項比較

| | (a) GoldenCheetah 轉檔 | (b) 最小的教練切換頁 | (c) 合成學員（現有 demo 產生器） |
|---|---|---|---|
| 做什麼 | 把 GC 跑者轉成 FIT＋`plan.json`，每人一個資料夾 | 列出模擬學員、點一下切換、看原本的 dashboard | 把 demo 的常數抽成 profile，用不同參數產生多位學員 |
| 資料像不像真人 | 像（真實的心率雜訊、不規律的訓練、缺課） | — | 不太像：訊號是 app 自己的模型產生的，拿來測演算法有循環論證的問題 |
| 越野／百岳 | 只有「爬升比例高」的路跑者；沒有百岳多日 | — | 完整（越野、百岳多日、背負、賽事） |
| GPS／地圖 | 沒有（要假造） | — | 有（虛構路線） |
| 授權 | CC0，可公開 | — | 自己產生，可公開 |
| 工作量（推估） | 4–5 天 | 2–3 天 | 2–3 天 |
| 依賴 | 需要 (b) 才看得到多位 | 需要 (a) 或 (c) 提供學員 | 需要 (b) 才看得到多位 |

---

## 4. 提案

### 4.1 共同架構：獨立的「測試實例」

不要把模擬學員塞進本人那台 app。理由：

1. 本人的 app 是 owner 模式，一個行程只有一位運動員（`athlete_id` 寫死 1、cookie 不分人）。要支援多位，得改 tenancy。
2. Dataset 只能留 2 個。在本人那台切學員，會把本人的 Dataset 擠掉，回到自己的 dashboard 要重建。
3. NAS 容器已經頂到 3 GB 過（commit `b4a15cb`）。

建議做法（推估）：新增一個模式，例如 `WKO5COACH_MODE=lab`，結構照抄 demo：

```
<lab-root>/
  athletes/
    sim-50k-m35/        ← 一位學員 = 一個資料夾，結構和 owner 的 ~/.wko5coach 一樣
      wko5coach.db
      plan.json
      fit/coros/<年>/*.fit
      cache/{fit,render}/
      sim_manifest.json ← 來源（合成 seed／GC uuid）、平移天數、虛構的賽事說明
    gc-3f9a.../
  ...
```

- 每位學員是一個 tenant：`root = shared = athletes/<id>`，能力用唯讀或 `DEMO_CAPS`。
- 每個請求用 cookie（例如 `trc_athlete`）決定是哪一位。middleware 已經有 demo 的 cookie 流程可以照抄。
- `athlete_id` 仍然是 1：每位學員有自己的 DB，裡面都是 athlete 1，不用動那 53 處寫死的地方。
- 執行地點：先在 Mac 本機跑；要放 NAS 就另開一個容器，記憶體上限 1.5 GB（和 HF demo 一樣），不影響本人的容器。

### 4.2 (a) GoldenCheetah → 模擬學員轉檔腳本

新增 `backend/demo/sim_gc.py`（或 `backend/scripts/sim/`），步驟：

1. **挑人**：沿用 `gc_fetch.py scan` 的結果（`scan.jsonl`）。挑選條件（推估）：最近一年 ≥ 100 次跑步、心率覆蓋 ≥ 80%、每公里爬升 ≥ 20 m 的跑步比例高的排前面；有功率的優先挑 1–2 位。先挑 5 位。
2. **下載**：`gc_fetch.py download`，只抓挑中的人，總量控制在 1 GB 以內，放 repo 外。
3. **轉檔**（每次活動）：
   - CSV → `secs, km, power, hr, cad, alt`；距離差分算速度（沿用 `gc_drift.parse_csv`）。
   - 用 `validation-goldencheetah.md` §1.3 的規則判斷是不是跑步；不是跑步的跳過，或照 `sport` 寫成單車（只當訓練量）。
   - 開始時間：JSON 的 `date`（UTC），用 `gc_fetch.match_csvs` 對上 CSV。
   - **日期平移**：整個人的資料往後平移 N 週，讓最後一次活動落在建置當天。
   - GPS：給一條沿 `AREAS` 的虛構軌跡，或全部留空（要先確認空座標不會讓哪個畫面報錯，**未驗證**）。
   - 溫度：留空。
   - `sub_sport`：每公里爬升 ≥ 20 m 寫 trail，其他 generic（app 匯入時會自己用 DB 的分類，這只是備用）。
   - 用 `fitwrite.encode_activity` 寫 FIT，`stryd=False`。
4. **plan.json**：體重取 JSON 的 `athlete_weight`；閾值留空讓 app 推估，或建置時呼叫 `thresholds.estimate` 寫一列；依最近 8 週的週量和爬升虛構一場 A 賽事（例如 8–12 週後的 25K／50K），名稱加「（模擬）」。
5. **匯入和預熱**：重用 `demo/build.py` 的 `_stage` 後半段（設定、`scan_and_import`、`relocate_paths`、`warm_base`），改成接受「已經有 FIT 的資料夾」。
6. **測試**：用一個小的假 GC zip（幾次活動）當 fixture，測轉檔欄位、日期平移、跑步判斷、`plan.json` 形狀。

| 工作 | 天數（推估） |
|---|---|
| 挑人＋下載（大多沿用 `gc_fetch`） | 0.5 |
| CSV → FIT 轉檔、日期平移、跑步判斷 | 1.5 |
| `plan.json`（體重、閾值、虛構賽事） | 0.5–1 |
| 接上 `build.py` 的匯入與預熱 | 0.5–1 |
| 測試 | 1 |
| **小計** | **4–5** |

### 4.3 (c) 合成學員（不靠外部資料的備案）

把 `backend/demo/athlete.py` 的常數和 `season.py` 裡的訓練量、爬升率、賽事抽成一個 `Profile` dataclass，`generate(root, seed, anchor, weeks, profile=...)` 接收它。預設 profile 就是現在的示範跑者，demo 的輸出完全不變（用現有 demo 測試的雜湊確認）。

建議先做 4 種（推估）：

| id | 樣貌 | 主要差異 |
|---|---|---|
| `sim-baiyue-f45` | 45 歲女性，百岳為主 | 體重 55 kg、CP 低、週 5–7 小時、背負多日、A 賽是百岳縱走 |
| `sim-50k-m35` | 35 歲男性，越野 50K | 接近現在的示範跑者，換 seed |
| `sim-road2trail-m28` | 28 歲男性，路跑轉越野新手 | 平路多、爬升率低、第一場 25K、週量起伏大 |
| `sim-100k-m50` | 50 歲男性，100K 超馬 | 週 10–14 小時、長距離背對背、CP 平穩或下滑 |

可選的加強：用 **Lövdal（CC0）** 的每日 km 和強度 km 當某位學員的「訓練節奏」，訊號仍然是合成的。這樣週量的起伏、缺課、受傷空窗是真人的樣子。限制：Lövdal 是菁英中長跑、單位是 km，而且只有沒受傷的那幾段能接成連續序列（`validation-lovdal.md` §1.1）。

| 工作 | 天數（推估） |
|---|---|
| 抽出 `Profile`，demo 輸出不變 | 1–1.5 |
| 4 個 profile 和各自的賽事 | 0.5–1 |
| 建置指令（一次建多位到 `<lab-root>/athletes/`） | 0.5 |
| （可選）Lövdal 節奏 | +1 |
| **小計** | **2–3**（含 Lövdal 3–4） |

### 4.4 (b) 最小的教練切換頁

- `lab` 模式的 tenant 解析：cookie `trc_athlete` → `athletes/<id>`；沒有 cookie 就顯示清單頁。
- `GET /api/v1/lab/athletes`：列出每位學員。**只讀 DB 和 `plan.json`**，不建 Dataset（避免一次載入 N 位）：名字、來源（合成／GC）、最近一次活動日期、近 7／28 天時數和 TSS、下一場賽事和倒數天數。
- `POST /api/v1/lab/switch`：設 cookie，前端跳到原本的 dashboard。
- 前端：一個清單頁＋頁首一個「目前學員：XXX ▾」的切換選單。
- 所有學員唯讀（或像 demo 一樣用 sandbox），避免測試時改壞。
- 測試：tenant 解析、cookie 檢查（只能是清單裡的 id，防路徑穿越）、清單 API。

| 工作 | 天數（推估） |
|---|---|
| `lab` 模式和 tenant 解析 | 0.5–1 |
| 清單 API（只讀 DB） | 0.5 |
| 清單頁＋切換選單＋i18n | 0.5–1 |
| 測試 | 0.5 |
| **小計** | **2–3** |

**和 SP-56、商業化的重疊**：

- 重疊的部分：清單頁的欄位（誰該注意、最近練了什麼、離比賽多久）、切換選單、「以學員的資料畫原本的 dashboard」，這些 SP-56 都會用到。
- 不重疊的部分：SP-56 真正要的是「帳號＋教練角色＋學員同意分享＋權限」。這次的 lab 模式沒有帳號、沒有同意流程，不能直接變成產品功能。
- 和已做的決定衝突：`todo-multi-user-sharing.plan.md` §10 寫了「不做互相分享」。教練看學員本質上就是分享。SP-56 要做的話，要先改這個決定。
- 架構：一人一個 SQLite＋資料夾的設計，教練清單頁要逐個打開 N 個 DB。10–20 位學員沒問題（推估），再多就是 `auth-and-demo.plan.md:576` 說的要換 Postgres 的時候。
- 記憶體：教練一次只看一位的 dashboard，所以 `DATASETS_MAX = 2` 夠用；但「全部學員的 PMC 疊在一起」之類的畫面不能靠 Dataset，要另外做輕量的彙總表。

### 4.5 建議的順序

1. (c) 合成學員 4 位＋(b) 切換頁，在 Mac 本機跑：**約 5–7 天**（推估）。先確認教練畫面要長什麼樣。
2. 如果要看「真實雜亂的資料」下 dashboard 的表現，再加 (a) 轉 5 位 GC 跑者：**約 4–5 天**（推估）。
3. 要給別人看的話，另開 NAS 容器或放 HF；兩種資料來源都可以公開（GC 是 CC0，合成是自己的）。

---

## 5. 記憶體和磁碟估計（全部推估）

**每位學員的磁碟**

| 項目 | 合成學員（照 demo） | GC 學員（一年） |
|---|---|---|
| FIT | 60–100 MB（約 300 次，含 GPS 和 Stryd 欄位；`auth-and-demo.plan.md:236`、`deploy/static/README.md:66`） | 10–30 MB（一年約 100–250 次、中位 61 分鐘；沒有 GPS，每秒約 20–26 bytes，一小時約 70–95 KB） |
| FIT 解析快取（npz） | 約 FIT 的 1/3 | 同左 |
| 圖表快取 | 預熱後數十 MB，上限 300 MB | 同左 |
| DB＋`plan.json` | < 5 MB | < 5 MB |
| **合計** | **約 150–200 MB** | **約 50–100 MB** |

10 位學員約 1–2 GB。最壞情況（每位的圖表快取都長到上限）約 4 GB。NAS 放得下，但要加上 lab 根目錄的總量監控，或把 lab 的圖表快取上限調小。

另外，GC 的原始 zip 下載量控制在 1 GB 以內，放在 repo 和 NAS app 資料夾以外，轉完可以刪。

**記憶體**

- 一位學員的 Dataset：150–300 MB（commit `b4a15cb` 量到 300 次合成跑步 168 MB；GC 學員活動較少，應該更小）。
- 同時最多 2 個 Dataset，lab 行程大約 600 MB–1 GB。和 HF demo 一樣設 1.5 GB 上限應該夠。
- **不要放進本人那台 3 GB 的 NAS 容器**：會和本人的 Dataset 搶那 2 格，而且那台已經頂到上限過。
- 建置時（轉檔＋匯入＋預熱）是另一個子行程，峰值可能到 1 GB 以上（推估），建議在 Mac 上建好再複製到 NAS。

---

## 6. 要請你決定的事

1. **目的是哪一個？** 只是想試「教練畫面長什麼樣」→ 合成學員就夠。想看 app 在「別人真實的雜亂資料」下會不會出錯 → 需要 GC。
2. **學員要哪些樣貌、幾位？** 建議 4 位合成（§4.3 的表）＋最多 5 位 GC。要不要指定女性、年長、百岳、100K 等組合？
3. **在哪裡跑？** 只在 Mac 本機、另開 NAS 容器、還是放上 HF 給別人看？NAS 還有沒有 1.5 GB 左右的記憶體空間給第二個容器？
4. **可以接受 GC 的日期被平移嗎？** 2016 年的跑步會顯示成 2026 年。GC 沒有 GPS，地圖是假的或空的，可以嗎？
5. **切換頁是「測試用、可丟的」還是 SP-56 的第一步？** 如果是後者，要先處理「不做互相分享」那個決定，以及帳號、同意流程。
6. **學員要唯讀，還是可以改計畫（像 demo 的 sandbox）？** 試教練改課表的流程需要可寫。
7. **虛構賽事**：GC 學員沒有比賽，可以由腳本依訓練量自動編一場嗎？名稱都會加「（模擬）」。

---

## 7. 來源（2026-10-05 查證）

授權：
- GoldenCheetah OpenData：https://osf.io/6hfpz/ ；OSF API `https://api.osf.io/v2/nodes/6hfpz/?embed=license` → "CC0 1.0 Universal"
- Lövdal 2021：DataverseNL API `datasets/:persistentId/?persistentId=doi:10.34894/UWU9PV` → "CC0-1.0"
- 4TU 多感測器：DataCite API `dois/10.4121/efa64223-ba51-48fa-91be-53f0e48460b4` → "cc-by-4.0"，格式 ".csv; .gpx; .py"
- PMData：https://datasets.simula.no/pmdata/ ，"Terms of use … CC BY 4.0"
- Kerhervé 2016：Figshare API `articles/3369790` → "CC BY 4.0"
- Trinity：Zenodo API `records/6325735` → "cc-by-4.0"，`data.zip` 6,656,694 bytes
- PhysioNet Treadmill：https://physionet.org/content/treadmill-exercise-cardioresp/1.0.1/ → "PhysioNet Contributor Review Health Data License 1.5.0"
- FitRec：https://cseweb.ucsd.edu/~jmcauley/datasets/fitrec.html → "for academic use only. Please do not redistribute them or use for commercial purposes."
- Ultra-marathon（Kaggle API）→ "CC0: Public Domain"，1,617,635,293 bytes
- Afonseca 2022：Figshare API `articles/16620238` → "CC BY 4.0"，14 個檔
- Matarmaa sportdata-mts-5（Kaggle API）→ "EU ODP Legal Notice"

repo 內：
- `docs/research/public-datasets.md`、`validation-goldencheetah.md`、`validation-lovdal.md`
- `docs/plans/auth-and-demo.plan.md`、`docs/plans/todo-multi-user-sharing.plan.md`
- `backend/tenancy.py`、`backend/tenancy_mw.py`、`backend/demo/{athlete,generate,season,signals,fitwrite,build,sandbox,instance}.py`
- `backend/api/wko5views.py`、`backend/engine/wko5expr/fitdataset.py`、`backend/files/file_service.py`、`backend/files/fit_to_channels.py`、`backend/engine/algorithms/classify.py`
- `backend/scripts/validation/gc_fetch.py`、`gc_drift.py`
