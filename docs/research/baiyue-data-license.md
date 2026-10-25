# 百岳清單 `backend/data/baiyue.json` 的來源與授權（SP-844）

> 調查日期：2026-10-21。只做調查，沒有改程式、沒有改關於頁。分支 `docs/sp808-844-research`（基於 `integrate/2026-10-20b`）。
> 背景：SP-495 關於頁把資料來源列上去了，但百岳清單沒有；公開收費前要說清楚。
> 標記：**程式**＝讀 repo 確認；**出處**＝讀過網頁，網址在 §6；**摘要**＝搜尋引擎摘要或第三方轉述；**推估**＝我的推論。
> **授權判斷是推估，不是法律意見。** 收費上線前若要確定，請問律師或直接取得資料提供者的書面同意。

## 0. 結論

1. **來源鏈**（程式）：happyman 的「地圖產生器」點位 API `twmap.happyman.idv.tw/gen/api/pointdata.php?id=ALL` → 疊石 Cairn（repo `inReachLiveTrack`）的 `v2/app/scripts/fetch-peaks.mjs` 產生 `v2/app/src/lib/peaks-data.ts`（2026-08-08 起）→ TrailRunCoach `backend/scripts/build_baiyue.py` 讀那個檔，只留百岳（100）與小百岳（103）→ `backend/data/baiyue.json`（2026-09-30 `e60ce17b` 加入）。欄位：名稱、海拔、經緯度（5 位小數）、是否百岳／小百岳、海拔排名。
2. **上游沒有寫授權**（出處）：地圖產生器首頁、地圖瀏覽器頁都沒有授權、使用條款或版權聲明；「關於」連結是 `javascript:void(0)`。GitHub `happyman/twmap` 是 GPL-2.0，但那是**程式碼**的授權，不涵蓋點位資料庫。沒有授權＝沒有明示允許，不等於可以自由用。
3. **內容本身大概不受著作權保護（推估）**：山名、海拔、座標是事實（著作權法第 10-1 條只保護表達，不及於「發現」等）；台灣沒有歐盟那種不需原創性的資料庫特別權（摘要）；智慧財產法院也有判決說只是機械式整理事實的資料庫不是編輯著作、投入勞力不等於原創（摘要）。百岳「選哪 100 座」是 1970 年代山岳協會的選擇，或許有編輯著作的空間，但名單本身已是公眾通用的標準清單，風險低（推估）。
4. **真正的風險不是著作權，是「整包抓別人整理的資料」**（推估）：座標與海拔是 happyman 與山友整理過的版本，我們整包複製、而且已經**隨公開鏡像放在公開 GitHub repo**（`public/main` 有 `backend/data/baiyue.json`）。商業使用、沒標出處、沒問過人，在禮貌上和公平交易法「榨取他人努力成果」的角度都不漂亮；實際被追究的機率低（對方是非營利的個人站，不是競爭對手）。
5. **建議：換成 OpenStreetMap 重建，同時在關於頁標出處**（選項 B，§4）。OSM 是 ODbL，app 已經用它做山屋清單（`backend/data/osm_huts_tw/`），關於頁已經有 OSM 條目、署名格式現成；名單（哪 100 座）是公共事實，座標與海拔改從 OSM 取。在換掉之前，關於頁先標「百岳與小百岳點位：地圖產生器（happyman）」。另一條路是寫信問 happyman 同意商用（選項 C），回覆前照標出處。

## 1. 來源鏈（程式）

| 段 | 在哪 | 內容 |
|---|---|---|
| 上游 | `https://twmap.happyman.idv.tw/gen/api/pointdata.php?id=ALL` | 4,200+ 筆點位，`mt100` 位元：1 百岳、2 小百岳、4 百名山；`class`、`type` 分基石、溫泉、駐在所…（`inReachLiveTrack/v2/app/scripts/fetch-peaks.mjs:9-29`、`:36`） |
| Cairn 快照 | `inReachLiveTrack/v2/app/src/lib/peaks-data.ts`（產生檔，`fetch-peaks.mjs` 寫出，`:121-139`） | 百岳 100、小百岳 103、溫泉 112、古道遺跡 832；座標留 5 位小數（`:59-60`）、同座標合併（`:84-112`）。Cairn 的慣例文件也記了上游是 twmap 的 `pointdata.php`（`inReachLiveTrack/docs/conventions-archive.md:116`） |
| TRC 產生腳本 | `backend/scripts/build_baiyue.py` | 讀 `[name, ele_m, lng, lat, flags]`，只留百岳／小百岳，要剛好 100 座百岳，依海拔排序（`build_baiyue.py:1-60`）。2026-10-03 `63537451` 把預設路徑（Windows 那台的 `peak-list/v2/app/src/lib/peaks-data.ts`）拿掉、`source` 改成中性字串 |
| TRC 資料檔 | `backend/data/baiyue.json` | `"source": "peak list rows [name, ele_m, lng, lat, flags] …"`——**沒有寫上游是誰** |
| 公開鏡像 | `public/main` | 有 `backend/data/baiyue.json`（`git ls-tree public/main`，2026-10-21） |

用到的地方（程式）：成就頁的登頂偵測（只用百岳、半徑 1 km，`engine/achievements.py:46`、`:79-89`）；路線的山名標示（`engine/routes.py:78`、`:1525-1535`）；賽事計算機用山名查天氣地點（`engine/racepower/weather.py:134`、`:269`）；`GET /peaks` 回百岳／小百岳清單（`api/racepower.py:5`）。

## 2. 授權查了什麼

| 查了 | 結果 |
|---|---|
| `twmap.happyman.idv.tw` 首頁（出處） | 只有「地圖產生器」「地圖瀏覽器」兩個連結與一張圖，沒有授權或條款 |
| `twmap.happyman.idv.tw/map/`（出處） | 沒有版權、授權、資料來源聲明；「關於」是 `javascript:void(0)` |
| GitHub `happyman/twmap`（出處，`gh api …/license`） | `GPL-2.0`；README 與 `docs/00README.md` 沒提點位資料的來源或條款 |
| 第三方怎麼標 happyman 的東西（摘要） | GPXSee 的地圖設定把 happyman 的 1:25,000 圖磚標「© Academia Sinica」（那是圖磚，不是點位） |
| Cairn 自己有沒有記授權 | 沒有，只記技術原因（沒有 CORS、資料近乎不變） |
| 開放資料（政府資料開放平臺）有沒有百岳座標（摘要） | 沒找到 |
| 維基百科「臺灣百岳」（出處） | 名單由林文安與中華民國山岳協會在 1970 年代規劃、1972 年成立百岳俱樂部；表格有海拔、沒有座標。內容是 CC BY-SA |
| Wikidata `Q10915621`（100 Peaks of Taiwan） | CC0。用 SPARQL 抓連到它的山頭，跟 `baiyue.json` 用名稱比對：100 座只對到 82 座（玉山、雪山、合歡山等 18 座名稱或連結對不上）；對到的座標差中位數 25 m，但 9 座差 > 300 m、最大 1,070 m（登頂偵測半徑才 1 km）。**不能直接拿來換** |

## 3. 能不能商用（推估）

- **著作權**：山名、海拔、座標是事實。著作權法第 10-1 條：保護只及於表達，不及於「思想、程序、製程、系統、操作方法、概念、原理、發現」（出處）。第 7 條編輯著作要「就資料之選擇及編排具有創作性」（出處）。智慧財產法院判決認為用公開資料機械式整理出的資料庫不具原創性，「汗水原則」不適用（摘要，理律新聞稿）。所以座標海拔這種清單大概不受著作權保護。
- **資料庫特別權**：台灣沒有（摘要：智慧局 2000 年前後委託研究過要不要立法保護不具原創性的資料庫，沒查到後來立法）。
- **百岳名單**：「選哪 100 座」可以主張是選擇的創作，但它已經是公眾通用的標準名單（政府、山岳團體、維基都在用），我們用的是「名單上有哪幾座」這個事實，不是誰的表格排版。風險低。小百岳是 2003 年行政院體委會與登山團體選的（出處：維基）。
- **契約**：上游沒有使用條款，所以沒有違約問題；但也沒有授權。
- **不公平競爭**：公平交易法第 25 條有「榨取他人努力成果」的類型（推估：記憶中公平會的處理原則有這一類，這次沒查原文）。對方是非營利的個人站，跟我們不競爭，被主張的機會低。
- **合起來**：法律上大概可以用（推估），但「整包抓一個沒授權的 API、改名後公開散布、拿去收費、不標出處」不是好的做法。標出處是最低限度；要乾淨就換成有授權的來源，或問到同意。

## 4. 選項

| | 做法 | 關於頁怎麼標 | 工作量 | 風險 |
|---|---|---|---|---|
| A | 留著、只補出處：`baiyue.json` 的 `source` 寫上游、`backend/data/` 加 README（比照 `osm_huts_tw/README.md`）、關於頁加一條 | 「百岳與小百岳點位：地圖產生器（happyman，twmap.happyman.idv.tw）點位資料；百岳名單：中華民國山岳協會」 | 小 | 還是沒授權 |
| **B（建議）** | 換成 OSM：名單（100＋103 個名字，公共事實）留著，座標與海拔改從 OSM `natural=peak` 用名字＋鄰近比對取；比照山屋清單的腳本、格式與 README；用現在的檔做對照，差 > 300 m 的逐座人工看 | 沿用關於頁既有的 OSM 條目，描述多寫「百岳與小百岳的座標」 | 中（要寫腳本、逐座確認比對；OSM 海拔標籤不一定有，沒有就查 DEM，跟山屋一樣） | ODbL 要求標示、衍生資料庫也用 ODbL——山屋清單已經這樣做 |
| C | 寫信問 happyman 同意商用，回覆前做 A | 照對方要求 | 小（等回覆） | 可能不回或不同意，到時還是要做 B |
| D | 不改 | 不標 | 無 | 收費後被問到說不清楚 |

不管選哪個，`build_baiyue.py` 的檔頭與資料檔的 `source` 都該寫清楚上游（現在兩邊都只寫「a peak list file」）。

**Cairn 同源**：`inReachLiveTrack` 的 `peaks-data.ts` 是同一個上游，而且收得更多（溫泉 112、古道遺跡 832，那兩類 OSM 不一定有對應）。Cairn 也要商業化（repo 裡有 commercialization 分支），同一個問題在那邊更大。

## 5. 要使用者決定的

1. 選 A／B／C／D？建議 B（換 OSM），換掉之前先做 A 的出處。
2. 要不要開實作單？建議開兩張，都 P3：
   - 關於頁與資料檔補出處（功能，`related` SP-844、SP-495）——馬上可做。
   - 百岳／小百岳座標改從 OSM 重建（功能，`related` SP-844），比照 SP-536 的山屋清單。
3. Cairn（InReach地圖 專案）的 `peaks-data.ts` 同源，要不要在那個專案也開一張？建議開（研究或維護 P3），那邊還有溫泉、古道遺跡兩類要另找來源。

## 6. 出處

- 地圖產生器首頁：https://twmap.happyman.idv.tw/ ；地圖瀏覽器：https://twmap.happyman.idv.tw/map/（2026-10-21 讀過，沒有授權聲明）
- happyman/twmap（GPL-2.0）：https://github.com/happyman/twmap
- GPXSee 地圖設定（happyman 圖磚標 © Academia Sinica，摘要）：https://git.gpxsee.org/root/GPXSee-maps/commit/86780524fe2b9eb04d654c5abc43ff4dad576ffc
- 著作權法（第 7、9、10-1 條）：https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=J0070017
- 理律法律事務所新聞稿（事實資料庫不具原創性的判決，摘要）：https://www.leeandli.com/TW/Newsletters/7448.htm
- 智慧局委託研究（不具原創性資料庫要不要立法，摘要）：https://www.tipo.gov.tw/wSite/public/Attachment/0/f1747970728286.pdf
- 維基百科 臺灣百岳：https://zh.wikipedia.org/zh-tw/臺灣百岳
- Wikidata 100 Peaks of Taiwan：https://www.wikidata.org/wiki/Q10915621 ；授權 CC0：https://www.wikidata.org/wiki/Wikidata:Licensing
- OSM 授權：https://www.openstreetmap.org/copyright ；ODbL 1.0：https://opendatacommons.org/licenses/odbl/1-0/
- repo 內的先例：`backend/data/osm_huts_tw/README.md`（SP-536）、`backend/api/about.py:109-123`（關於頁的資料來源清單）
