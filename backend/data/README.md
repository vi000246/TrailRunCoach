# backend/data

App 內建的靜態資料。每一份都寫明出處；關於頁（`backend/api/about.py` 的 `DATA`）列同樣的來源。

| 檔案 | 內容 | 出處 |
|---|---|---|
| `baiyue.json` | 台灣百岳 100 座、小百岳 103 座的名稱、海拔、座標 | 見下 |
| `osm_huts_tw/` | 台灣山屋、避難小屋與高海拔營地 | OpenStreetMap，ODbL 1.0（見 `osm_huts_tw/README.md`） |

## `baiyue.json`：百岳與小百岳

**出處**：百岳與小百岳點位：地圖產生器（happyman，twmap.happyman.idv.tw）；百岳名單：中華民國山岳協會。

- 點位（名稱、海拔、經緯度）取自 happyman「地圖產生器」的點位 API
  `https://twmap.happyman.idv.tw/gen/api/pointdata.php?id=ALL`（`mt100` 位元 1 = 百岳、2 = 小百岳）。
- 百岳「選哪 100 座」是 1970 年代林文安與中華民國山岳協會（Chinese Taipei Mountaineering Association）規劃的名單。
- 上游沒有寫授權或使用條款。山名、海拔、座標是事實資料；這裡保留出處，關於頁的資料來源清單也列了這一條。
- 用在：成就頁的登頂偵測（`engine/achievements.py`）、路線的山名（`engine/routes.py`）、
  賽事計算機的天氣地點（`engine/racepower/weather.py`）、`GET /peaks`（`api/racepower.py`）。

**怎麼產生**：`backend/scripts/build_baiyue.py <點位快照檔>`——讀 `[name, ele_m, lng, lat, flags]` 的列，
只留百岳與小百岳，依海拔排 `rank`（1 = 玉山），檢查剛好 100 座百岳。

**格式**：

```jsonc
{
  "source": "百岳與小百岳點位：…；百岳名單：…",   // 出處（上面那一行）
  "rows": "peak list rows [name, ele_m, lng, lat, flags] …",  // 產生時讀的列格式
  "count": {"baiyue": 100, "xiaobaiyue": 103},
  "peaks": [
    {"name": "玉山", "elevation_m": 3952, "lat": 23.47, "lon": 120.95728,
     "baiyue": true, "xiaobaiyue": false, "rank": 1}       // rank 只有百岳有
  ]
}
```
