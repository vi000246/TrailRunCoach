# 台灣山屋／營地清單（OpenStreetMap 精選）

`huts.json` 是從 OpenStreetMap 抓出來的台灣山屋、避難小屋與高海拔營地（SP-536，研究 SP-522）。
TrailRunCoach 上傳多日行程的 GPX 時，沿線 150 m 內的點會列成「分日點候選」（標「OSM 山屋」／「OSM 營地」，
要點選才會變成切點，不會自己套用）。

這份清單**只有公開的 OSM 資料**，跟使用者資料、教練邏輯分開放，可以直接公開（公開鏡像也包含它）。
其他專案（例如疊石 Cairn 的 SP-550）要用，直接讀這個檔案即可，格式見下。

## 授權：ODbL 1.0

- 資料 © OpenStreetMap contributors，以 [Open Database License 1.0](https://opendatacommons.org/licenses/odbl/1-0/)
  提供：<https://www.openstreetmap.org/copyright>。
- 這份精選清單是 OSM 的**衍生資料庫**，同樣以 ODbL 1.0 提供：任何人都可以使用、修改、再散布，
  條件是標示來源，且衍生的資料庫也要以 ODbL 提供。
- 用到這份資料的畫面要標「© OpenStreetMap contributors」並連到 <https://www.openstreetmap.org/copyright>
  （計算機的候選點旁、地圖標記的說明都有）。
- 海拔有一部分來自 Copernicus GLO-90 DEM（經 Open-Meteo 的 elevation API 取得）：
  © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the
  European Union and ESA.

## 格式 `trc-osm-huts/1`

```jsonc
{
  "format": "trc-osm-huts/1",              // 格式版本；欄位意義改了才換號
  "attribution": "© OpenStreetMap contributors",
  "license": "ODbL-1.0",
  "license_url": "https://www.openstreetmap.org/copyright",
  "source": "OpenStreetMap via the Overpass API (…)",
  "osm_timestamp": "2026-10-14T05:33:48Z", // OSM 資料的時間（Overpass 的 timestamp_osm_base）
  "generated": "…",                        // 產生這個檔的時間（UTC）
  "query": {"bbox": [south, west, north, east], "tourism": ["alpine_hut", "wilderness_hut", "camp_site"]},
  "filter": {"camp_site_min_ele_m": 1500, "ele": "…"},
  "count": {"alpine_hut": 95, "wilderness_hut": 16, "camp_site": 188},
  "dropped": {"camp_site_low": 543, "camp_site_no_ele": 0},
  "huts": [                                 // 依 OSM id 排序，一行一筆
    {"osm": "way/1535803756",               // OSM 的 type/id：https://www.openstreetmap.org/way/1535803756
     "kind": "alpine_hut",                  // alpine_hut 山屋 | wilderness_hut 避難小屋 | camp_site 營地
     "name": "素密達山屋",                  // OSM 的 name（沒有就 name:zh-Hant / name:zh / name:en）；可能是 null
     "name_en": "Sumida Hut",               // 選填：name:en 跟 name 不同時才有
     "lat": 24.42337, "lon": 121.25089,     // WGS84，5 位小數（約 1 m）；way / relation 是中心點
     "ele_m": 3500,                         // 公尺，整數；可能是 null（山屋沒有海拔時）
     "ele_src": "osm"}                      // osm：OSM 的 ele 標籤 | dem：DEM 查的 | null
  ]
}
```

讀的時候只認 `format` 開頭是 `trc-osm-huts/1` 的檔案；不認得就當沒有清單（候選只是方便，不影響計算）。

## 怎麼篩

- 範圍：台灣本島 bbox `21.85, 119.95, 25.35, 122.05`（用 bbox 不用 area：自架的 Overpass 沒開 area）。
- `alpine_hut`、`wilderness_hut` 全部保留（OSM 上這兩個標籤本來就是山屋；少數低海拔的看起來是標錯的涼亭，
  但它們只有在路線經過 150 m 內才會出現、而且只是候選，所以不另外篩）。
- `camp_site` 只留**海拔 ≥ 1500 m** 的。2026-10-14 的資料有 731 個 camp_site，大多是平地、淺山的車露營區：
  - 中位數 662 m；1000–1500 m 之間 97 個，幾乎都是「○○露營區」「○○農場」；
  - 1500 m 以上 188 個，絕大多數是登山營地（○○營地、獵寮、工寮），少數是高山農場的露營區（武陵、福壽山、觀霧）。
    這幾個留著：它們只當候選，路線剛好經過時本來就可能在那裡過夜。
  - 門檻低於 1500 m 的登山營地（例如 1400 多 m 的鞍部營地）會被濾掉；要改門檻，改
    `backend/scripts/build_osm_huts.py` 的 `CAMP_MIN_ELE_M` 再重建。
- 海拔：有 OSM 的 `ele` 標籤就用（842 個裡只有 46 個有，多半是山屋），沒有就查 DEM（Copernicus GLO-90，經 Open-Meteo）。
  兩者都有的點，差距中位數 21 m。

## 更新

在 repo 根目錄跑（只寫這個檔案，不碰使用者資料）：

```sh
# 公開的 Overpass（預設 overpass-api.de）
python -m backend.scripts.build_osm_huts

# 自架的台灣區 Overpass（例如 https://overpass.yichlin.com/api/interpreter，在 Cloudflare Access 後面）
TRC_OVERPASS_URL=https://overpass.yichlin.com/api/interpreter \
TRC_OVERPASS_CF_CLIENT_ID=… TRC_OVERPASS_CF_CLIENT_SECRET=… \
python -m backend.scripts.build_osm_huts

# 用存下來的 Overpass JSON 回應重建；--dry-run 只印數量
python -m backend.scripts.build_osm_huts --from-file overpass.json --dry-run
```

- Overpass 位址：`--url` 或 `TRC_OVERPASS_URL`；Cloudflare Access 的 service token 只從環境變數讀，不會寫進檔案。
- DEM：`--elevation-url` 或 `TRC_ELEVATION_URL`（預設 Open-Meteo）。Open-Meteo 每分鐘 600 個點，
  超過時腳本會等一分鐘再查。
- 重建後看一下 `git diff`（一行一筆，新增／刪除的點一目了然），跑
  `pytest backend/tests/test_osm_huts_sp536.py`。
