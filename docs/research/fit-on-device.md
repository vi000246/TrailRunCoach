# FIT 能不能存在使用者自己的裝置、由網頁讀取（SP-319）

- **查證日期：2026-10-07。** 外部說法都回到原始頁面核對過原文（MDN、MDN 的相容性資料 `browser-compat-data`、web.dev、WebKit 部落格與 WebKit 官方頁、Chrome 開發者文件、Apple 開發者文件、Capacitor 文件、npm、Pyodide CDN）。網址列在文末附錄。
- **標記方式：**
  - **已驗證**：我在原始頁面、相容性資料或本 repo 程式碼裡讀到原文。
  - **摘要**：整理自官方或二手文件，但原文說得不夠肯定，或沒有逐字核對。
  - **推估**：我的推論或粗估。
  - **未找到來源**：查不到。
- **這份文件接在 SP-307 後面**：`docs/research/local-first-sync.md`（分支 `docs/actual-sync-sp307`）。下列事情使用者已經決定，本文件不重新決定：
  1. 沒訊號時只要能**看**：做離線唯讀快取，連上網路再同步（SP-314）。
  2. **可以做端對端加密**，但要先確定 FIT 存哪、怎麼同步再決定（就是這張單）。
  3. 網頁和桌面 app 只選一個，**網頁優先**；未來也要手機。
  4. 課表：伺服器 `plan_auto` 產生，**手動改過的永遠優先**。
  5. 舊的「伺服器只轉送、分析在桌面 app」做法**不再維持**。
  6. 運動紀錄**保留 3 年**，每人 FIT 約 60 MB（SP-317）。
- 沒有登入任何外部服務，也沒有呼叫 COROS。擁有者的資料只在 NAS 容器內做唯讀量測（§9.1），文件只放總數與大小；沒有讀 `secret.key`、`.env` 或 token。

---

## 0. 結論先講

1. **技術上做得到，但「只存在使用者裝置」不安全。** 瀏覽器存 60 MB 的 FIT 沒問題，容量夠。問題在 iPhone：用 Safari 開、沒加到主畫面的話，7 天沒點這個網站，資料會被系統整個刪掉；加到主畫面才不受這條限制。再加上手機會換、會壞，所以**伺服器一定要留一份**（明文或加密都可以）。
2. **網頁讀不到使用者手機裡任意資料夾的 FIT。** 桌面 Chrome／Edge 可以記住使用者授權的資料夾，下次自動讀；Android Chrome 132 起有選資料夾的功能；**iPhone 和 Firefox 完全沒有**，只能每次讓使用者手動挑檔上傳。而且本 app 的 FIT 是伺服器從 COROS 下載的，本來就不在使用者的資料夾裡。
3. **把 FIT 搬到裝置幾乎省不到錢，真正的成本是運算。** 擁有者 3 年 FIT gzip 後 47 MB（重度使用者）；1,000 位會員的 FIT 放 Cloudflare R2 每月不到 1 美元，100 位在免費額度內。會卡住伺服器的是資料集整份重建（30 秒）和舊版快取（擁有者一人 44 MB 沒清掉）。假設裝置只能穩定留 20 MB：課表、3 年摘要、近 90 天的圖表資料約 8 MB 就夠好用，加上近 90 天 FIT 合計約 13 MB（§9）。
4. **建議：FIT 主檔留在伺服器，裝置只放分層的快取（SP-314），先不做端對端加密。** FIT 物件異地備份到 R2。 如果之後真的要加密，走本文的**選項 D**：伺服器在匯入當下算好摘要，再把 FIT 原檔和 GPS 用使用者的公鑰封起來，之後伺服器自己打不開；`plan_auto`、推課表到手錶照常全自動。代價是模型升級後的「全部重算」要等使用者打開裝置才能做，而且忘記密碼就拿不回原檔。

---

# 第一部分：現況

## 1. 本 app 現在的 FIT 從哪來、誰在用

| 項目 | 現況 | 標記 |
|---|---|---|
| FIT 從哪來 | 伺服器用使用者的 COROS 帳號（token 與加密封存的密碼存在 `sync_state`）去 COROS 下載，存到 `<shared>/fit/coros/<年>/…`。沒有讓使用者上傳 FIT 的端點：`upload.fit` 權限有定義（`backend/tenancy.py:44`），頁面上也有對應的選擇器（`backend/static/shell.js:290`），但後端沒有接收的程式 | 已驗證（程式碼） |
| 擁有者資料量 | `fit/coros` 812 個、122.7 MB，中位數 93 KB。**全部 gzip 實測 49.4 MB，是原本的 0.40 倍**（SP-307 抽 40 個樣本估 0.36 倍，偏低）。逐秒快取 npz 42.8 MB，DB 2.3 MB 加 WAL 3.7 MB | 已驗證（SP-307 量測；gzip 為 2026-10-07 本單在 NAS 唯讀全量實測） |
| 多租戶的目標量 | 保留 3 年，每人 FIT 約 60 MB，gzip 後約 24 MB。擁有者是重度使用者：近 3 年 641 個 FIT、117.6 MB，gzip 後 47.0 MB | 60 MB 是 SP-317 的設定（推估）；擁有者數字已驗證（本單實測） |
| `plan_auto` 要讀什麼 | 不讀 FIT 本身，讀從 FIT 算出來的東西：這週每堂課的平均心率、功率、TSS、課型（`workout_review.measure`）、CTL 爬升、TSB、門檻（CP、LTHR、AeT）、自評 RPE（`backend/api/plan_sessions.py` 的 `_adapt_ctx`） | 已驗證（程式碼） |
| 推課表到手錶 | 伺服器用 COROS token 呼叫 COROS，把課表內容送過去（`/api/v1/plan/push-coros`、`plan_auto` 的推送） | 已驗證（程式碼） |
| 路線天氣 | 用每段路線的**平均位置與海拔**去查 Open-Meteo 歷史天氣（`backend/engine/route_weather.py`）。也就是伺服器要看得到 GPS | 已驗證（程式碼） |
| 門檻估算、課後評估 | 要讀**逐秒資料**（從 FIT 解析出的 npz） | 已驗證（SP-307 §3.2） |

**重點：** 伺服器自動做的事（調課表、推手錶）只需要「每筆活動的摘要」，但**產生這些摘要**需要逐秒資料。這一點決定了後面各選項能不能保留自動化。

## 2. 瀏覽器有哪些地方可以存檔案

| 方式 | 適合放什麼 | 支援度 | 標記 |
|---|---|---|---|
| **OPFS**（Origin Private File System，網站自己的私有檔案系統） | 檔案型資料：FIT、逐秒資料。web.dev 的建議是「檔案型內容用 OPFS」 | 取得根目錄（`navigator.storage.getDirectory`）：Chrome 86、Android Chrome 109、Firefox 111、Safari 15.2（含 iPhone）。在 Worker 裡同步讀寫（`createSyncAccessHandle`）：Chrome 102、Android Chrome 109、Firefox 111、Safari 15.2。在主執行緒寫檔（`createWritable`）Safari 要到 26 | 已驗證（MDN 相容性資料、web.dev） |
| **IndexedDB** | 結構化的小筆資料：摘要、課表、設定、加密金鑰 | 所有現代瀏覽器 | 已驗證（web.dev：「IndexedDB, the OPFS, and the Cache Storage API are supported in every modern browser」） |
| **Cache API**（Service Worker 的快取） | 網頁本身的檔案、API 回應。SP-314 的離線頁面用這個 | 所有現代瀏覽器 | 已驗證（同上） |
| **File System Access API**（讀寫使用者自己指定的資料夾） | 使用者電腦上的真實資料夾 | 只有 Chromium 系（見 §4） | 已驗證 |
| localStorage | 不適合：同步執行會卡畫面，只能存字串，約 5 MB | 全部 | 已驗證（web.dev） |

- MDN 說明 OPFS「is subject to browser storage quota restrictions, just like any other origin-partitioned storage mechanism」，而且「Clearing storage data for the site deletes the OPFS」。也就是 **OPFS 跟 IndexedDB 一樣會被清**，沒有比較安全。已驗證。
- 瀏覽器也內建 gzip 解壓縮（`DecompressionStream`：Chrome 80、Firefox 113、Safari 16.4），FIT 可以用 gzip 存、讀的時候再解。已驗證（MDN 相容性資料）。

## 3. 容量與被清掉的風險

### 3.1 每個網站能用多少

| 瀏覽器 | 每個網站上限 | 標記 |
|---|---|---|
| Chrome／Edge（含 Android） | 磁碟總容量的 60%，一般模式與持久模式都一樣。整個瀏覽器最多用 80% | 已驗證（MDN） |
| Firefox | 一般模式：10 GiB 或可用空間的一部分，取小的；持久模式：磁碟的 50% | 已驗證（MDN） |
| Safari 17 起（iOS 17、macOS Sonoma） | 瀏覽器 app：磁碟的 60%；內嵌網頁的其他 app（例如用 WKWebView 包的 app）：15%。**加到主畫面或 Dock 的 web app 跟瀏覽器一樣是 60%** | 已驗證（WebKit 2023〈Updates to Storage Policy〉、MDN） |
| Safari 16 以前 | 一開始 1 GiB，超過時問使用者 | 已驗證（MDN） |

**結論：** 每人約 24–47 MB（gzip 後的 3 年 FIT）加上逐秒資料、摘要，大約 50–100 MB。**瀏覽器給的上限不是問題，問題是會不會被清（§3.2）。** 如果假設裝置只能穩定留 20–60 MB，要放什麼見 §9。推估。

### 3.2 什麼情況會被清掉

| 情況 | 誰會發生 | 說明 | 標記 |
|---|---|---|---|
| 空間不夠 | 所有瀏覽器 | 從「最久沒用的網站」開始整個刪。**拿到持久儲存的網站不會被這樣刪** | 已驗證（MDN、WebKit） |
| **7 天沒點就刪** | **只有 Safari**（開啟「防止跨網站追蹤」時，預設開啟） | 原文：ITP「deletes all cookies created in JavaScript and all other script-writeable storage after 7 days of no user interaction with the website」。包含 IndexedDB、Service Worker 與它的快取、localStorage | 已驗證（webkit.org/tracking-prevention） |
| 7 天怎麼算 | Safari | 算的是「**用 Safari 的天數**」，不是日曆天：「after seven days of Safari use without user interaction on the site」 | 已驗證（WebKit 2020 部落格） |
| 加到主畫面的 web app | iPhone、Mac | **不受 7 天限制**：「The first-party domain of home screen web applications is exempt from ITP's 7-day cap on all script-writeable storage」 | 已驗證（webkit.org/tracking-prevention） |
| 使用者自己清 | 所有瀏覽器 | 清瀏覽紀錄或網站資料就沒了，持久儲存也擋不住 | 已驗證（MDN、web.dev） |
| 無痕模式 | 所有瀏覽器 | 關掉就刪 | 已驗證（MDN） |

**兩個容易忽略的細節：**

- **OPFS 有沒有在 Safari 7 天規則裡？** WebKit 2020 年的清單沒有 OPFS（那時 Safari 還沒有 OPFS）；MDN 現在的說法是「its data created from script will be deleted」，WebKit 2023 文章也把 File System 列在同一套儲存政策內。所以**應該當成會被一起刪**。推估。
- **Safari 分頁和主畫面 app 的資料是分開的。** WebKit 原文：「the website data of home screen web applications is kept isolated from Safari」。使用者在 Safari 用過、再加到主畫面，主畫面 app 會從空的開始。已驗證。

### 3.3 持久儲存（`navigator.storage.persist()`）怎麼拿

| 瀏覽器 | 怎麼給 | 標記 |
|---|---|---|
| Chrome／Edge | 不跳視窗，自動判斷：網站使用程度高不高、**有沒有安裝或加書籤**、有沒有給通知權限 | 已驗證（web.dev〈Persistent storage〉，2020 年更新） |
| Firefox | 跳視窗問使用者 | 已驗證（MDN、web.dev） |
| Safari 17 起 | 不跳視窗，自動判斷，例如「whether the website is opened as a Home Screen Web App」 | 已驗證（WebKit 2023） |
| 持久儲存能不能擋 Safari 的 7 天規則 | 官方沒有明講。WebKit 只說持久模式的網站「might be excluded from eviction」 | **未找到來源** |

- Chrome 團隊的研究說資料「very rarely」被瀏覽器自動刪；常來的網站，即使沒拿到持久儲存也很少被刪。已驗證（MDN、web.dev 引用）。
- **實際意思（推估）：**
  - Android Chrome、桌面瀏覽器：常用的話資料很穩，安裝成 PWA 更穩。
  - iPhone Safari 分頁：**不可靠**，一週沒打開就可能全空。
  - iPhone 主畫面 app：沒有 7 天問題，大致可靠；但手機空間不夠時仍可能被清。

## 4. 網頁能不能讀「使用者自己資料夾」裡的 FIT

### 4.1 各平台的支援度

| 平台 | 選檔一次上傳（`<input type=file multiple>`） | 選整個資料夾一次上傳（`webkitdirectory`） | 授權一個資料夾、之後直接讀（`showDirectoryPicker`） | 下次打開還記得授權 | 標記 |
|---|---|---|---|---|---|
| 桌面 Chrome／Edge | 可以 | 可以 | 可以（Chrome 86 起） | 可以（Chrome 122 起：「Allow on every visit」；**安裝成 app 的自動記住**） | 已驗證 |
| Android Chrome | 可以 | 可以（132 起；131 選資料夾會當掉） | 可以（132 起） | 文件沒寫 Android 的狀況 | 已驗證（相容性資料）；記住授權：**未找到來源** |
| 桌面 Firefox | 可以 | 可以 | **不支援** | — | 已驗證 |
| 桌面 Safari | 可以 | 可以 | **不支援** | — | 已驗證 |
| iPhone Safari／主畫面 app | 可以 | 可以（**iOS 18.4 起**，從「檔案」app 挑資料夾） | **不支援** | — | 已驗證（相容性資料、WebKit Safari 18.4 文章） |

- Chrome 文件原文：File System Access API「is supported on most Chromium browsers on Windows, macOS, ChromeOS, Linux, and Android」，Brave 要開旗標。已驗證。
- 沒有持久授權時：「The web app can continue to save changes to the file without prompting until all tabs for its origin are closed」，關掉分頁後下次要重新授權。已驗證（Chrome 文件）。
- 這個 API 在 MDN 標為「Experimental」「not Baseline」，而且一定要**使用者先點一下**才能叫出選擇器（transient user activation）。已驗證。
- **網頁沒辦法在背景自動讀使用者的資料夾**：每次都要使用者打開網頁，持久授權也只是「打開時不用再問」。推估（依上面的限制推得）。

### 4.2 FIT 實際上會在使用者裝置的哪裡

- 本 app 的 FIT 是**伺服器從 COROS 抓的**，使用者的手機或電腦上通常沒有 FIT 檔。COROS 手機 app 會不會把 FIT 放在使用者看得到的資料夾：**未找到來源**。
- 所以「網頁讀使用者資料夾裡的 FIT」只在兩種情況有用（推估）：
  1. 使用者用的不是 COROS（例如自己從別的平台匯出 FIT），要**手動匯入**。
  2. 桌面使用者有一個固定的 FIT 資料夾，希望網頁每次打開時自動匯入新檔（只有 Chrome／Edge 做得到）。
- 兩種都是「**上傳到 app**」的功能，不是「FIT 只放在使用者那邊」的架構。

### 4.3 包成原生 app（例如 Capacitor）會不會比較好

| 項目 | 說明 | 標記 |
|---|---|---|
| 讀寫檔案 | Capacitor 的 Filesystem 外掛可以寫 app 自己的目錄。Android 11 起，`Documents` 只能存取 app 自己建立的檔案 | 已驗證（Capacitor 文件） |
| 被系統清掉 | 原生 app 的檔案不受 Safari 7 天規則影響；但 Safari 對「內嵌網頁的 app」的網頁儲存上限是 15%（如果資料存在 WebView 的 IndexedDB 而不是原生檔案） | 已驗證（WebKit 2023）；整體判斷推估 |
| 背景自動同步 | iOS 的背景工作由系統決定何時跑：「the system doesn't guarantee launching the task at the specified date, but only that it won't begin sooner」。所以**手機 app 也不能保證每天定時同步** | 已驗證（Apple `BGTaskRequest.earliestBeginDate`） |
| 網頁版的背景同步 | `Periodic Background Sync` 只有 Chromium 有（Chrome 80 起），Safari、Firefox 沒有 | 已驗證（相容性資料） |
| 代價 | 要上架 App Store／Play、多一套建置流程，跟「網頁優先」的決定相反 | 推估 |

**結論：** 原生 app 能解決「iPhone 被清資料」，但解決不了「沒開 app 就不會跑」。對「網頁優先」來說，第一步不需要它。推估。

## 5. 在裝置上解析 FIT、跑模型

| 項目 | 說明 | 標記 |
|---|---|---|
| Garmin 官方 JS FIT SDK | npm `@garmin/fitsdk`，最新版 21.218.0（2026-10-06 更新），解壓後約 1.45 MB。README：「requires Node.js v14.0 or higher, or a browser with a compatible JavaScript runtime engine」。授權是 Garmin 自己的 FIT 授權（`SEE LICENSE IN LICENSE.txt`），上線前要讀條款 | 已驗證（npm、GitHub README） |
| Pyodide（瀏覽器裡跑 Python） | 示範版已在用 314.0.7（`backend/demo/static_racepower.py`）。實際下載量：`pyodide.asm.wasm` 9.6 MB、`python_stdlib.zip` 2.5 MB、numpy 套件 3.0 MB，**合計約 15 MB**（未壓縮）。SQLAlchemy 有現成套件；`fitdecode` 不在 Pyodide 套件清單裡，要另外從 PyPI 裝（它是純 Python，應該可以） | 已驗證（CDN 實際下載量測、`pyodide-lock.json`）；`fitdecode` 能裝是推估 |
| 解析速度 | NAS 上 Python 解析：中位數檔 0.65 秒、最大 12.3 秒。800 個檔第一次全量解析，用 Python 估計要 10–20 分鐘；JS SDK 應該比較快，但沒測 | NAS 數字已驗證（SP-307）；手機數字推估，**SP-313 會實測** |
| 平常每天 | 只解析新的那 1–2 個檔，幾秒內完成。前提是快取 key 用 FIT 雜湊（SP-308）、彙總能增量更新（SP-320） | 推估 |
| 整套模型搬到瀏覽器 | 引擎約 8.1 萬行 Python。用 Pyodide 可以沿用，但要拿掉檔案 I/O 與資料庫的部分；改寫成 JS／TS 等於維護兩份模型，會越差越遠 | 推估（SP-307 §7） |
| 手機記憶體 | iPhone 分頁的記憶體上限官方沒公布 | 未找到來源 |

---

# 第二部分：落差

## 6. 什麼一定要在伺服器跑

| 功能 | 為什麼要伺服器 | FIT 不在伺服器（或伺服器打不開）時怎麼辦 | 標記 |
|---|---|---|---|
| **COROS 同步** | 用的是 COROS 帳號登入後的 API，token 和密碼存伺服器；瀏覽器直接呼叫預期會被 CORS 擋（SP-315 還沒實測）；就算不擋，也等於把 COROS 密碼交給網頁 | 只能「伺服器下載 → 交給裝置」。**伺服器在下載當下一定看得到明文 FIT**。要讓伺服器完全看不到，就得讓裝置自己去抓：需要 CORS 開放（未知）或原生 app，而且只有裝置開著時才會同步 | 推估（CORS 部分待 SP-315） |
| **`plan_auto` 自動調課表** | 同步完自動跑，使用者不用開 app | 需要的是**摘要**（§1）。摘要如果伺服器看得到，就照常自動；摘要也加密的話，只能等使用者打開 app 時在裝置上跑 | 已驗證（輸入清單）；做法推估 |
| **推課表到手錶** | 用 COROS token 呼叫 COROS | 推送內容就是課表本身，伺服器一定看得到課表 | 已驗證 |
| **路線天氣** | 用 GPS 位置查 Open-Meteo | 在匯入當下查完、只存結果；或改由裝置查（Open-Meteo 是公開 API，CORS 狀況未查） | 推估 |
| **門檻估算、課後評估** | 要逐秒資料 | 匯入當下在伺服器算一次並存結果；模型改版後要重算時，只能在裝置上做 | 推估 |
| **每月清理（SP-317）** | 排程刪 3 年前的資料 | 伺服器只存密文時，刪「整個物件」照樣做得到（看得到日期這種中繼資料就行），裝置下次同步再刪本機那份 | 推估 |
| **「下載我的全部 FIT」（SP-317）** | 從伺服器打包 | 伺服器存密文時，改成裝置下載密文、在裝置上解開打包 | 推估 |
| **換裝置、多裝置、裝置壞掉** | 需要一份不在使用者手上的副本 | 伺服器一定要留一份（可以是密文） | 推估 |

## 7. FIT 放在使用者裝置時：同步、備份、換裝置

**如果伺服器有一份（明文或密文）：**

1. **新裝置**：登入 → 拿到 `fit_objects` 清單（SP-308，800 個雜湊約 26 KB）→ 只下載需要的。
   - 手機只需要最近 90 天的 FIT（擁有者實測 10.6 MB 原檔、4.7 MB gzip）加上全部摘要。
   - 電腦可以全部拉，一般會員約 24 MB gzip，重度使用者（擁有者）47 MB。
   - 裝置只能留 20–60 MB 時的優先順序見 §9.2。
2. **多裝置**：FIT 是不會改的物件，用雜湊比對就知道缺什麼，不會有衝突。會衝突的只有使用者改的小筆資料，照 SP-307 的變更紀錄與「手動改的優先」規則處理（SP-312）。
3. **裝置壞掉或被瀏覽器清掉**：重新下載就好，等同新裝置。
4. **iPhone Safari 7 天被清**：同上，重新下載。但如果**加密金鑰也存在被清掉的 IndexedDB 裡**，使用者要重新輸入密碼（見 §8）。

**如果伺服器沒有任何副本（選項 C）：**

- 換裝置要從舊裝置傳過去。網頁之間沒有直接傳檔的好方法，實際上就是匯出 zip 再匯入。推估。
- 裝置壞掉、iPhone 被清，**FIT 就沒了**。伺服器上只剩摘要，長期圖表還在，但不能重算逐秒分析。推估。
- SP-317 的「下載我的全部 FIT」只能從裝置匯出。
- **結論：選項 C 跟「多裝置」和「保留 3 年」都衝突。** 推估。

## 8. 端對端加密怎麼搭

### 8.1 最基本的問題：COROS 的資料一定先經過伺服器

只要 COROS 同步在伺服器跑（§6），伺服器在下載當下就看得到 FIT。端對端加密在本 app 能做到的是：**伺服器下載、算完之後，就再也打不開**。不是「伺服器從來沒看過」。這點跟 SP-307 §2.8 提到的 Actual 銀行同步一樣。推估。

### 8.2 建議的加密方式（如果要做）

**用「公鑰封存」**：伺服器只拿得到公鑰，可以封、不能開。這是我依通用做法提出的設計，沒有在其他產品裡逐行查證。推估。

1. 使用者第一次啟用時，裝置產生一組金鑰對（WebCrypto 的 ECDH P-256）。
   - 公鑰給伺服器。
   - 私鑰用使用者的密碼（PBKDF2 導出的金鑰）包起來。包好的私鑰也存一份在伺服器，新裝置輸入密碼就能解開。
   - 另外給一組**救援碼**，讓使用者印下來或存在密碼管理器。
2. 每次 COROS 同步：伺服器下載 FIT → **在記憶體裡算完摘要** → 產生一把隨機的 AES-256-GCM 金鑰加密 FIT → 用使用者的公鑰把這把金鑰包起來 → 只存密文，丟掉明文。
3. 裝置下載密文，用私鑰解開。

**這些瀏覽器都做得到：**
- WebCrypto 的 `encrypt` 與 `deriveKey`：Chrome 37／41、Firefox 34、Safari 7／11 起都有。已驗證（相容性資料）。
- 私鑰可以存成「不可匯出」的 CryptoKey，放在 IndexedDB。推估（WebCrypto 的一般用法，沒逐條查證）。

### 8.3 加密的代價

| 代價 | 說明 | 標記 |
|---|---|---|
| 忘記密碼又沒救援碼 | 加密過的 FIT 救不回來，只剩伺服器上的明文摘要 | 推估（Actual 官方文件也這樣寫，SP-307 §2.7 已驗證） |
| iPhone Safari 被清 | 包好的私鑰跟著被清，要重新輸入密碼才能看逐秒資料；摘要和課表不受影響 | 推估 |
| 模型改版要重算 | 伺服器打不開 FIT，只能等使用者打開裝置，在裝置上重算；或請使用者「暫時解鎖」，讓伺服器重算一次。跟 SP-320 的分級重算要一起設計 | 推估 |
| 裝置算力 | 重算要在手機或電腦上跑；第一次全量可能十幾分鐘（§5） | 推估 |
| 伺服器仍看得到 | 每筆活動的時間、大小、數量；摘要本身（若摘要不加密）；課表 | 推估 |
| 客服與除錯 | 使用者回報「這筆活動分析怪怪的」，開發者看不到原始資料 | 推估 |

### 8.4 哪些東西加密才有意義

- **GPS 軌跡最敏感**：從起點終點看得出住家、上班地點。逐秒心率次之。推估。
- **摘要**（日期、時間長度、距離、TSS、平均心率、課型）敏感度低很多，而且 `plan_auto` 和推手錶需要它。推估。
- **自由文字**（備註、傷病描述）模型用不到，可以單獨加密（SP-307 §6 已提）。推估。

## 9. 假設裝置只能穩定留 20–60 MB：放什麼、伺服器要花多少

使用者補充的前提（2026-10-07）：瀏覽器每人只能可靠地留約 60 MB，甚至更少（20–50 MB，或 iPhone 被清）。目標是**多租戶、伺服器成本盡量低，但不要明顯傷到速度與使用體驗**。

### 9.1 擁有者的資料分層實測

- 2026-10-07 在 NAS 容器內跑唯讀 Python 腳本，按活動日期分層統計。只讀檔案大小、筆數與資料表欄位長度；沒有寫入，沒有讀 `secret.key`、`.env`，`sync_state` 只數列數。已驗證。
- 擁有者是**重度使用者**：近 3 年 641 個 FIT（每天約 0.6 個），平均一個 183 KB。一般會員應該比較少（推估）。

| 項目 | 近 90 天 | 近 1 年 | 近 3 年 | 全部 | 標記 |
|---|---|---|---|---|---|
| FIT 個數 | 54 | 219 | 641 | 812 | 已驗證 |
| FIT 原檔 | 10.6 MB | 39.7 MB | 117.6 MB | 122.7 MB | 已驗證 |
| FIT gzip | **4.7 MB** | **17.2 MB** | **47.0 MB** | 49.4 MB | 已驗證（逐檔 gzip 等級 6） |
| 逐秒快取 npz（全解析度、float64、已壓縮） | 4.8 MB | 17.3 MB | 39.2 MB | 42.8 MB | 已驗證 |
| 「畫圖用」逐秒資料：每 5 秒一點、時間＋4 個通道、float32 | **1.0 MB** | 3.8 MB | 9.1 MB | 9.9 MB | 依實際筆數換算（格式是假設的，推估） |
| `mmp_cache`（最佳成績曲線） | 51 KB | 188 KB | 374 KB | 514 KB | 已驗證（欄位長度加總，不含索引） |
| `workout_metrics`（每筆活動 12 種指標） | 11 KB | 41 KB | 83 KB | 154 KB | 已驗證（同上） |

**不分日期的部分：**

| 項目 | 大小 | 說明 | 標記 |
|---|---|---|---|
| 每筆活動的中繼資料（`cache/fit/…/index.json`） | 0.9 MB | 812 筆，每筆約 1.1 KB（開始時間、運動、各種衍生欄位） | 已驗證 |
| `workout_files` 表 | 288 KB | 1,824 列（含 TP 來源與重複標記） | 已驗證 |
| 課表與設定（`plan_sessions`、`plan.json`、`user_settings`、`race_calc`） | 約 30 KB | | 已驗證 |
| `plan_change_log` | 376 KB | 15 列，每列平均 19 KB（存整份前後課表） | 已驗證 |
| 課後評估快取 `workout_review_v23`（目前版本） | 7.2 MB | 全部活動 | 已驗證 |
| 其他目前版本的彙總快取（racepower 等） | 約 9.2 MB | 伺服器運算的中間結果，裝置用不到 | 已驗證 |
| **舊版本、沒清掉的快取**（`workout_review_v18`、`v19`） | **44.4 MB** | 程式現在用 `v23`（`backend/engine/workout_review.py:96`）。`cache/fit` 的 60.8 MB JSON 裡有 73% 是用不到的舊版 | 已驗證 |
| GPS 軌跡 `routes/tracks` | 12.6 MB（462 個，平均 27 KB） | | 已驗證 |
| 路線天氣 `routes/weather` | 0.8 MB | | 已驗證 |
| 圖表圖片快取 `cache/render` | 4.4 MB | | 已驗證 |
| 根目錄 JSON（`workout_curves`、`racepower_cptests`、成就等） | 2.5 MB | | 已驗證 |
| 網頁程式本身（`backend/static`） | 1.7 MB | 不含從 CDN 載入的函式庫 | 已驗證（repo 量測） |
| Pyodide 執行環境 | 約 15 MB | 只有選擇在裝置上跑 Python 模型時才需要 | 已驗證（§5） |

### 9.2 裝置上的優先順序

依「沒有它會不會明顯變慢、或沒訊號時看不到」排序。大小用擁有者的數字。

| 優先 | 放什麼 | 大小（擁有者） | 為什麼 | 累計 |
|---|---|---|---|---|
| **P0 一定要放** | 網頁程式；今天、這週、接下來 2 週的課表；設定；最近幾筆課表變動；近 90 天每筆活動的摘要（指標、中繼資料、最佳成績、課後評估結果） | 約 2.5–3 MB（程式 1.7 MB；近 90 天摘要約 0.6 MB，評估結果按比例推估） | 沒訊號能看今天的課（SP-314）；首頁、課表頁打開就有東西 | **約 3 MB** |
| **P1 應該放** | 3 年的每筆活動摘要（約 1.2 MB）；每天的 CTL／TSB 序列（3 年約 50 KB，推估）；最佳成績曲線與成就（約 1.4 MB）；近 90 天「畫圖用」逐秒資料（1.0 MB）；近 90 天 GPS 軌跡（約 0.8–1.5 MB，按比例推估） | 約 5 MB | 長期圖表、PMC、最近活動的逐秒圖「秒開」，不用等伺服器 | **約 8 MB** |
| **P2 有空間再放** | 近 90 天 FIT gzip（4.7 MB）；近 1 年「畫圖用」逐秒資料（3.8 MB）；近 1 年 GPS 軌跡（約 3.4 MB，推估） | 約 12 MB | 往前捲一年內的活動也快；有 FIT 才能在裝置上重算 | **約 20 MB** |
| **P3 不放裝置，放伺服器或雲端** | 90 天以前的 FIT（3 年 gzip 47 MB）；全解析度 npz（39 MB）；圖表圖片快取；伺服器的中間彙總快取；舊版快取（應該直接刪） | 90 MB 以上 | 很少看；放裝置會擠掉 P0–P2，而且 iPhone 被清時白下載 | — |
| **需要時再拿或重算** | 打開一筆舊活動：下載那一筆的 FIT gzip（中位數約 40 KB）或伺服器算好的圖表資料 | 每筆幾十 KB | 下載不到 1 秒；JS 解析一筆約 1 秒內（推估，SP-313 實測） | — |

**不同預算放得下什麼（推估）：**

| 裝置預算 | 放得下 | 放不下 |
|---|---|---|
| 20 MB | P0＋P1（約 8 MB）＋近 90 天 FIT（4.7 MB），還留一半空間 | 近 1 年的逐秒資料與軌跡要分批 |
| 50–60 MB | P0–P2（約 20 MB）＋近 1 年 FIT gzip（17 MB），約 37 MB | 3 年 FIT（重度使用者 47 MB）＋ Pyodide（15 MB）會超過 |
| 0（被清空） | iPhone Safari 分頁 7 天沒用被清：打開時先補 P0（約 3 MB，幾秒），再背景補 P1、P2 | — |

### 9.3 被瀏覽器清掉之後怎麼恢復

| 要補回的 | 大小 | 從哪裡下載 | 時間（10 Mbps 約 1.25 MB／秒；山區 2 Mbps） | 要不要重算 | 標記 |
|---|---|---|---|---|---|
| P0 | 約 3 MB | 伺服器 | 約 2 秒；山區約 12 秒 | 不用，伺服器已算好 | 推估 |
| P0＋P1 | 約 8 MB | 伺服器 | 約 7 秒；山區約 30 秒 | 不用 | 推估 |
| P2 | 約 12 MB | 伺服器或物件儲存（例如 R2） | 約 10 秒；背景慢慢補 | 選項 A 不用；選項 D 要先輸入密碼才能解開 FIT | 推估 |
| 新電腦拉 3 年 FIT | 24–47 MB | 物件儲存 | 約 20–40 秒 | 選項 A 不用；B／C 要在裝置上解析全部（§5，可能十幾分鐘） | 推估 |

- 補回的順序：先 P0（畫面能用），再 P1，最後在背景補 P2。推估。
- **選項 A 和 D 被清只是「重新下載」，選項 C 被清就是「資料遺失」。** 這是 §10 選項表裡 iPhone 風險的依據。

### 9.4 什麼真的要伺服器，什麼可以只在裝置

| 東西 | 放哪 | 理由 |
|---|---|---|
| COROS 帳號、同步、推課表 | **伺服器** | §6：帳號、CORS、全自動 |
| 匯入時解析一次、算摘要 | **伺服器** | `plan_auto` 要摘要；每筆約 1 秒，很便宜（見 §9.5） |
| 摘要、課表、使用者改的資料（每人 2–6 MB） | **伺服器** | `plan_auto` 要讀；新裝置要拿；很小 |
| FIT gzip（每人 24–47 MB） | **伺服器或便宜的物件儲存** | 換裝置、模型改版重算、SP-317 匯出都要它。成本很低（§9.5） |
| 全解析度逐秒快取 | 伺服器只留**近 1 年**，更舊的需要時從 FIT 重算 | 每人省約 22 MB（3 年 39.2 MB → 1 年 17.3 MB）；舊活動很少看。跟 SP-320 的分級一起做 |
| 圖表圖片快取（`cache/render`） | 可以改成裝置自己畫 | 裝置有「畫圖用」逐秒資料就能畫；伺服器省空間和 CPU。推估 |
| 舊版快取 | **刪掉** | 擁有者一人就 44 MB；這比把 FIT 搬到裝置省得多 |
| 近期快取、離線課表 | **只在裝置** | SP-314 |

### 9.5 伺服器成本：100 人與 1,000 人

**假設（推估）：** 一般會員 3 年 FIT 60 MB（SP-317），重度會員 118 MB（擁有者）；gzip 0.40 倍；每天 0.6 個新活動，每個 183 KB（擁有者近 3 年平均）。

**儲存量：**

| 類別 | 每人 | 100 人 | 1,000 人 |
|---|---|---|---|
| FIT 原檔 | 60–118 MB | 6–12 GB | 60–118 GB |
| FIT gzip | 24–47 MB | 2.4–4.7 GB | 24–47 GB |
| 摘要 DB（含課表、設定） | 2–6 MB | 0.2–0.6 GB | 2–6 GB |
| 伺服器衍生快取，現在的做法（npz 43＋目前版本彙總 16＋軌跡天氣 13＋圖片 4，不含舊版快取） | 約 77 MB | 約 7.7 GB | 約 77 GB |
| 伺服器衍生快取，只留近 1 年全解析度、圖片改裝置畫（npz 17＋彙總 16＋軌跡天氣 13） | 約 47 MB | 約 4.7 GB | 約 47 GB |

**每月費用（定價 2026-10-07 從官方頁面原文核對）：**

| 放哪 | 公開定價 | 100 人 | 1,000 人 | 標記 |
|---|---|---|---|---|
| **現有 NAS 自架** | 硬體與電費已經在付 | 約 $0 額外費用 | 約 $0（FIT＋DB＋快取總量約 75–130 GB，NAS 放得下） | 推估。限制在 CPU、記憶體、家用上傳頻寬，而且只有一台、沒有異地備份 |
| **Cloudflare R2**（放 FIT gzip） | Standard：每月前 10 GB 免費，之後 $0.015／GB-月；寫入（Class A）每月前 100 萬次免費，之後 $4.50／百萬次；讀取（Class B）每月前 1,000 萬次免費，之後 $0.36／百萬次；**對外流量免費** | **$0**（2.4–4.7 GB 在免費額度內；每月約 1,800 次寫入） | **約 $0.2–0.6**（24–47 GB；每月約 1.8 萬次寫入，免費） | 定價已驗證；費用推估 |
| **Backblaze B2**（放 FIT gzip） | $6.95／TB-月；前 10 GB 免費；對外流量在儲存量 3 倍內免費，超過 $0.01／GB；API 呼叫免費 | $0 | 約 $0.1–0.3 | 定價已驗證；費用推估 |
| 代管資料庫：Neon（Postgres） | Launch 方案：運算 $0.106／CU-小時、儲存 $0.35／GB-月；閒置會自動停（不計運算） | 儲存約 $0.1–0.2；運算看用量，0.25 CU 全天開約 $19 | 儲存約 $0.7–2.1；運算同左或更多 | 定價已驗證；費用推估 |
| 代管資料庫：Cloudflare D1（SQLite） | 要 Workers 付費方案（每月最低 $5），含 5 GB，超過 $0.75／GB-月 | $5 | $5 起 | 定價已驗證；費用推估 |

- 代管資料庫的問題不在錢：本 app 是「每人一個 SQLite＋Python 在旁邊算」。換 Postgres 要大改；D1 只能從 Cloudflare Workers 存取，跟 FastAPI 架構不合。推估。
- 透過 Cloudflare Tunnel 從 NAS 對外的流量有沒有限制或收費：**未找到來源**（目前 homelab 已經在用，沒有額外帳單，推估）。

**流量（推估）：**

| 流量 | 100 人 | 1,000 人 |
|---|---|---|
| COROS 下載 FIT（進來） | 每天約 11 MB，每月約 0.33 GB | 每月約 3.3 GB |
| 裝置每天同步（新摘要＋一個 FIT gzip 約 73 KB） | 每月不到 0.5 GB | 每月約 2–5 GB |
| 裝置被清後補回（每人每月 2 次 × 20 MB，偏高的假設） | 每月約 4 GB | 每月約 40 GB（放 R2 免流量費） |

**運算（推估，用擁有者實測換算）：**

| 工作 | 每次 | 100 人每天 | 1,000 人每天 |
|---|---|---|---|
| 匯入時解析 FIT | 約 1 秒（NAS 中位數 0.65 秒、第 90 百分位 1.76 秒，SP-307 已驗證） | 約 1 分鐘 CPU | 約 10 分鐘 CPU |
| `plan_auto` 一次 | 沒量過（未找到來源） | — | — |
| 資料集**整份**重建 | 30 秒（正式環境 log，SP-320） | 如果每個新活動都整份重建：約 30 分鐘 | 約 5 小時 |
| 部署後快取版本升級，全部租戶重建 | 每人 30 秒 | 約 50 分鐘 | 約 8.3 小時單核心（4 核約 2 小時） |

### 9.6 這一節的結論

1. **把 FIT 從伺服器搬到使用者裝置，幾乎省不到錢。** 1,000 人的 FIT gzip 放 R2 每月不到 1 美元，100 人在免費額度內；但換來的是 iPhone 被清就遺失、換裝置麻煩。推估（定價已驗證）。
2. **真正花錢、會卡住的是運算和記憶體**：資料集整份重建（30 秒）、部署後全部重算、同時重建吃記憶體。SP-320 的增量重算和分級快取，比 FIT 放哪重要得多。推估。
3. **比搬 FIT 更有效的省法**：刪舊版快取（擁有者一人 44 MB）、全解析度逐秒快取只留近 1 年（每人省約 22 MB）、圖表改裝置畫（每人省約 4 MB 加 CPU）。已驗證（大小）／推估（做法）。
4. **建議的存放方式（兼顧成本與體驗）：**
   - **裝置**：P0＋P1 常駐（約 8 MB），空間夠就加 P2（約 20 MB）。永遠不是唯一的一份；被清就從伺服器補回，P0 幾秒內就能用。
   - **伺服器（NAS）**：每人一個 SQLite 放摘要、課表、使用者改的資料（2–6 MB）；COROS 同步、匯入時解析、`plan_auto`、推手錶；全解析度衍生快取只留近 1 年。
   - **FIT 原檔**：gzip、雜湊命名的物件（SP-308），現在放 NAS；**異地備份到 R2**（100 人免費，1,000 人每月不到 1 美元）。NAS 不夠用時，把 `FitStore` 換成 R2 為主。
   - **之後要加密**：FIT 物件改成選項 D 的公鑰封存，存放位置與成本都不變。

---

# 第三部分：結論與後續

## 10. 選項比較

- **A. 伺服器存 FIT（明文，伺服器自己的金鑰做靜態加密）**：裝置只有快取。就是目前加上 SP-308、SP-314 的方向。
- **B. 伺服器存加密的 FIT，裝置解開來算**：伺服器只當保管箱，所有分析在裝置上。
- **C. FIT 只放在裝置，伺服器只存小筆加密摘要**：最接近「資料在使用者手上」。
- **D. 混合**：伺服器在匯入當下算好摘要，然後把 FIT（含 GPS、逐秒資料）用使用者公鑰封存；明文只留摘要，讓 `plan_auto` 和推手錶照常自動。

| | **A 伺服器存 FIT** | **B 伺服器存加密 FIT** | **C 只放裝置** | **D 混合（匯入時算、之後封存）** |
|---|---|---|---|---|
| FIT 在哪 | 伺服器（主檔）＋裝置快取 | 伺服器（密文主檔）＋裝置 | 只有裝置 | 伺服器（密文主檔）＋裝置 |
| 摘要在哪 | 伺服器明文 | 裝置算好，加密後上傳 | 裝置算好，加密後上傳 | 伺服器明文 |
| 離線 | 唯讀快取（SP-314） | 唯讀快取 | 全部離線可用 | 唯讀快取 |
| 端對端加密 | 沒有（只有傳輸與靜態加密） | 有（下載當下除外，§8.1） | 有（但 COROS 同步仍經伺服器，除非裝置自己抓） | **部分**：FIT 和 GPS 有；摘要和課表沒有 |
| COROS 同步全自動 | 是 | 是（伺服器抓完就封） | 只有裝置自己抓或伺服器轉交時 | 是 |
| `plan_auto`、推手錶全自動 | **是** | **否**：要等裝置開著算完上傳 | **否** | **是** |
| 模型改版重算 | 伺服器自己算 | 只能在裝置算 | 只能在裝置算 | 摘要層級伺服器能算；要用逐秒資料的只能在裝置算 |
| 換裝置／裝置壞掉 | 登入就好 | 輸入密碼後重新下載 | **FIT 會遺失** | 輸入密碼後重新下載 |
| iPhone 風險 | 低：快取被清就重新下載 | 中：被清要重新輸入密碼，第一次全量要在手機算 | **高**：被清就等於資料遺失 | 低到中：被清只影響逐秒資料，要重新輸入密碼 |
| 忘記密碼 | 不適用 | 全部 FIT 和分析遺失 | 全部遺失 | 原檔遺失，摘要與長期圖表還在 |
| 工作量 | **S–M**（已開的 SP-308、SP-314） | **XL**：引擎搬到瀏覽器、金鑰管理、`plan_auto` 改成裝置觸發 | **XL**：同 B，再加裝置間傳檔，而且跟保留 3 年、多裝置衝突 | **L**：金鑰管理、匯入時封存、裝置解密與逐秒分析、重算流程 |
| 裝置要多少空間（擁有者，§9.2） | 8 MB 就好用，20 MB 很充裕 | 至少近 1 年 FIT（17 MB）＋ Pyodide（15 MB）＋摘要；要重算 3 年就要 47 MB 以上 | 3 年 FIT 47 MB 全放裝置，**超過 20–50 MB 的預算** | 同 A，另加金鑰（很小） |
| 伺服器每月成本（1,000 人，§9.5） | FIT 放 NAS 約 $0；異地備份 R2 約 $0.2–0.6 | 同 A（密文一樣大） | 只省下 FIT 那 $0.2–0.6，摘要照樣要存 | 同 A |
| 伺服器運算 | 全部在伺服器；靠 SP-320 增量重算控制 | 匯入解析仍在伺服器；分析移到裝置 | 幾乎全移到裝置 | 匯入時算一次；模型改版的逐秒重算移到裝置 |
| 跟使用者決定的關係 | 符合網頁優先、離線唯讀、手動改優先 | 會失去「同步完自動調課表、推手錶」 | 跟多裝置、3 年保留衝突 | 保留自動化，加密範圍較小 |

## 11. 建議

1. **現在：走 A，照 §9.6 的分層存放。** FIT 主檔在伺服器（SP-308 雜湊物件），裝置只放離線快取（SP-314）。
   - 裝置：P0＋P1 常駐（擁有者約 8 MB：課表、摘要放 IndexedDB；近 90 天「畫圖用」逐秒資料與軌跡放 OPFS），空間夠再加 P2（約 20 MB）。
   - 打開時呼叫 `navigator.storage.persist()`。
   - 快取被清時就從伺服器補回（P0 約 3 MB，幾秒內），**不把裝置當成唯一的副本**。
   - FIT gzip 物件異地備份到 Cloudflare R2：100 人在免費額度內，1,000 人每月不到 1 美元。
2. **省成本先從運算和快取下手，不是搬 FIT。** 刪舊版快取（擁有者 44 MB）、全解析度逐秒快取只留近 1 年、資料集增量重算（SP-320）。
3. **iPhone 使用者引導加到主畫面。** 這是避開 Safari 7 天清除的唯一官方方式。設定頁顯示「這台裝置的離線資料：xx MB，是否持久」（用 `navigator.storage.estimate()`、`persisted()`）。
4. **「讀使用者自己的 FIT」改成匯入功能**，不當成儲存架構：
   - 所有瀏覽器（含 iOS 18.4 起）：選檔或選資料夾上傳。
   - 桌面 Chrome／Edge 可以加「記住這個資料夾，每次打開自動匯入新檔」。
   - 匯入的檔案一樣進伺服器的 `FitStore`。
5. **端對端加密：先不做，但保留 D 的路。**
   - SP-308 的雜湊物件、SP-311 的資料分類、SP-320 的分級重算，都要讓「摘要」和「原檔、逐秒資料」分得清楚。之後要加 D 時，只是把原檔那一層換成密文。
   - 不建議 B、C：會失去本 app 的核心（同步完自動調課表、推手錶），或跟多裝置、保留 3 年衝突。
6. **如果使用者主要擔心的是「NAS 或雲端硬碟被偷、備份外流」**，伺服器端的靜態加密就夠了（伺服器保管金鑰），不需要端對端加密。推估。

## 12. 建議開的單

| # | 標題 | 大小 | 依賴 | 說明 |
|---|---|---|---|---|
| 1 | SP-314 補充：離線快取照 P0／P1／P2 分層，處理被清除 | S（併入 SP-314） | SP-314 | 照 §9.2：P0 課表與近 90 天摘要、P1 3 年摘要與近 90 天「畫圖用」逐秒資料、P2 有空間再放；打開時要求持久儲存；偵測快取被清就依 P0→P1→P2 補回；空間不夠（`QuotaExceededError`）時從 P2 開始丟；Safari 分頁和主畫面 app 是兩份獨立資料，登出時兩邊都要清 |
| 2 | 設定頁顯示「這台裝置的離線資料」 | S | 1 | 用量、是否持久、最後更新時間；iPhone Safari 分頁顯示「加到主畫面才不會 7 天被清」提示 |
| 3 | 手動匯入 FIT（選檔、選資料夾上傳） | M | SP-308 | 補上 `upload.fit` 的後端；所有瀏覽器用 `<input type=file multiple>`／`webkitdirectory`；匯入走雜湊去重與 `dedup.py` |
| 4 | 桌面 Chrome／Edge：記住 FIT 資料夾，打開時自動匯入新檔 | S | 3 | 用 `showDirectoryPicker`，把資料夾控制代碼存 IndexedDB；不支援的瀏覽器隱藏這個選項 |
| 5 | SP-313 補充：量 800 個檔全量解析與 OPFS 讀寫 | S（併入 SP-313） | SP-313 | 除了三個代表檔，也量批次 800 個的總時間、記憶體峰值、iPhone 會不會把分頁砍掉；這是 B、D 能不能在手機重算的依據 |
| 6 | 資料分類加上「敏感度」欄位 | S（併入 SP-311） | SP-311 | 標出 GPS、逐秒資料、自由文字屬於「加密時要封存」，摘要屬於「伺服器自動化需要」。之後選 D 時直接照表做 |
| 7 | 清掉舊版本的衍生快取 | S | — | `cache/fit` 的 `series_workout_review_v18`、`v19` 共 44 MB（擁有者一人），程式只用 `v23`。快取版本升級時自動刪舊版；可併入 SP-320 |
| 8 | FIT 物件異地備份到 Cloudflare R2 | S–M | SP-308 | `FitStore` 加 `S3Store`（R2），先當備份、之後可當主檔；伺服器金鑰加密；每月清理（SP-317）時一起刪。100 人免費，1,000 人每月不到 1 美元。要使用者同意開 R2 |
| 9 | 全解析度逐秒快取只留近 1 年，更舊的需要時重算 | M（併入 SP-320） | SP-320 | 每人省約 22 MB；打開舊活動時從 FIT 重算一筆（約 1 秒） |
| 10 | 圖表改由裝置用「畫圖用」逐秒資料自己畫 | M | 1 | 每 5 秒一點、float32；近 90 天約 1 MB。伺服器少存圖片快取、少花 CPU；可以先做活動詳情頁 |
| 11 | 調查：選項 D 的金鑰管理與重算流程 | M（文件＋原型） | 6、SP-320 | **只有使用者決定要端對端加密才開**。公鑰封存、密碼與救援碼、新裝置解鎖、模型改版時的裝置端重算或暫時解鎖 |

## 13. 要請使用者決定的問題

1. **端對端加密想防的是誰？**
   - (a) NAS 或雲端被偷、備份外流 → 伺服器端靜態加密就夠（選項 A）。
   - (b) 連伺服器管理者（你自己）都不能看會員的 GPS 和逐秒資料 → 選項 D。
   - (c) 伺服器從頭到尾都不能看到 FIT → 要放棄伺服器 COROS 同步，改成裝置自己抓，而且只有開著 app 才同步。不建議。
2. **接受選項 D 的代價嗎？** 忘記密碼又沒救援碼時，原檔救不回來（摘要和長期圖表還在）；模型改版後的逐秒重算要等使用者打開 app。
3. **iPhone 使用者可以要求「加到主畫面」嗎？** 不加的話，離線快取一週沒開就可能被清（只影響離線，資料在伺服器不會丟）。
4. **要不要做「手動匯入 FIT」（單 3、4）？** 這是給不用 COROS、或想匯入舊資料的會員用的。
5. **手機 app 的方向**：繼續用 PWA（網頁加到主畫面），還是之後包成原生 app（Capacitor）？原生 app 能避開 Safari 的清除規則，但一樣不能保證背景定時同步，而且要上架。
6. **裝置預算與離線快取要放多少？** 建議以 20 MB 為準（P0–P2，§9.2）；iPhone Safari 分頁要假設隨時可能被清空，打開時先補 P0。要不要讓使用者在設定頁選「省空間（約 8 MB）／一般（約 20 MB）／完整（加近 1 年 FIT，約 40 MB）」？
7. **可以開 Cloudflare R2 當 FIT 異地備份嗎？**（100 人免費，1,000 人每月不到 1 美元；要在 Cloudflare 帳號開 R2，可能需要綁付款方式。）還是先只放 NAS？
8. **舊版快取可以直接刪嗎？** NAS 上 `series_workout_review_v18`、`v19` 共 44 MB，程式已經不讀（單 7）。

---

## 附錄：查證過的來源

**瀏覽器儲存（2026-10-07 下載原文核對）**
- MDN〈Storage quotas and eviction criteria〉 https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria
- MDN〈Origin private file system〉 https://developer.mozilla.org/en-US/docs/Web/API/File_System_API/Origin_private_file_system
- MDN〈Window: showDirectoryPicker()〉 https://developer.mozilla.org/en-US/docs/Web/API/Window/showDirectoryPicker
- MDN browser-compat-data（`api/Window.json`、`StorageManager.json`、`FileSystemFileHandle.json`、`FileSystemSyncAccessHandle.json`、`FileSystemHandle.json`、`HTMLInputElement.json`、`PeriodicSyncManager.json`、`SyncManager.json`、`StorageBucketManager.json`、`SubtleCrypto.json`、`DecompressionStream.json`） https://github.com/mdn/browser-compat-data
- web.dev〈Storage for the web〉（2024-09-23 更新） https://web.dev/articles/storage-for-the-web
- web.dev〈Persistent storage〉（2020-05-12 更新） https://web.dev/articles/persistent-storage
- WebKit〈Updates to Storage Policy〉（2023-08-10） https://webkit.org/blog/14403/updates-to-storage-policy/
- WebKit〈Full Third-Party Cookie Blocking and More〉（2020-03-24） https://webkit.org/blog/10218/full-third-party-cookie-blocking-and-more/
- WebKit〈Tracking Prevention in WebKit〉 https://webkit.org/tracking-prevention/
- WebKit〈WebKit Features in Safari 18.4〉 https://webkit.org/blog/16574/webkit-features-in-safari-18-4/
- WebKit〈Web Push for Web Apps on iOS and iPadOS〉 https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/

**檔案存取**
- Chrome〈The File System Access API〉（2024-08-19 更新） https://developer.chrome.com/docs/capabilities/web-apis/file-system-access
- Chrome〈Persistent permissions for the File System Access API〉（2024-01-09 更新） https://developer.chrome.com/blog/persistent-permissions-for-the-file-system-access-api
- Capacitor〈Filesystem〉 https://capacitorjs.com/docs/apis/filesystem
- Apple〈BGTaskRequest.earliestBeginDate〉 https://developer.apple.com/documentation/backgroundtasks/bgtaskrequest/earliestbegindate

**解析與運算**
- npm `@garmin/fitsdk`（registry 中繼資料） https://registry.npmjs.org/@garmin/fitsdk
- Garmin FIT JavaScript SDK README https://github.com/garmin/fit-javascript-sdk
- Pyodide 314.0.7 CDN（`pyodide.asm.wasm`、`python_stdlib.zip`、`pyodide-lock.json`、numpy 套件，實際下載量測） https://cdn.jsdelivr.net/pyodide/v314.0.7/full/

**TrailRunCoach 程式碼**
- `backend/tenancy.py`（`upload.fit` 權限）、`backend/static/shell.js`
- `backend/engine/plan_auto.py`、`backend/api/plan_sessions.py`（`_adapt_ctx`、`push`）
- `backend/engine/route_weather.py`
- `backend/demo/static_racepower.py`（Pyodide 版本）

**定價（2026-10-07 下載原文核對）**
- Cloudflare R2 Pricing https://developers.cloudflare.com/r2/pricing/
- Backblaze B2 Pricing https://www.backblaze.com/cloud-storage/pricing
- Cloudflare D1 Pricing https://developers.cloudflare.com/d1/platform/pricing/
- Cloudflare Workers Pricing（付費方案每月最低 $5） https://developers.cloudflare.com/workers/platform/pricing/
- Neon Pricing https://neon.com/pricing
- Hetzner、Turso 的定價頁要執行 JavaScript 才有數字，沒有取到原文，所以沒放進比較。

**擁有者資料量測（2026-10-07，NAS 容器內唯讀 Python）**
- `/data/fit` 每個 FIT 的大小與檔名日期、逐檔 gzip 等級 6 的大小。
- `/data/cache/fit/<資料夾>/index.json` 對應到 `ch/*.npz`，依活動日期分層加總；讀 npz 的筆數換算「每 5 秒一點」的大小。
- `cache/fit` 的 JSON 依快取名稱與版本加總；`routes/`、`cache/render`、根目錄 JSON 的大小。
- `wko5coach.db` 以唯讀模式開啟，各表列數與欄位長度加總；`sync_state` 只數列數、不讀內容。沒有寫入，沒有讀 `secret.key`、`.env`。

**前一份研究與單**
- `docs/research/local-first-sync.md`（分支 `docs/actual-sync-sp307`）
- SP-307、SP-308、SP-311、SP-313、SP-314、SP-315、SP-317、SP-320
