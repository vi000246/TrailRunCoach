# 單次活動：路線地圖＋跨圖同步 hover — 待辦

> Status: 已實作（2026-09-30，feat/workout-map）。Leaflet 地圖取代原本沒有底圖的 ECharts 軌跡；
> 後端 `GET /api/v1/wko5/workouts/{i}/samples` 給同一步長的 t / d / lat / lng / elev / hr / power / grade；
> 預設底圖、疊圖存在 user_settings（`charts.map.basemap` / `charts.map.overlays`，經 `/api/v1/sync/settings`）。

## 使用者需求
- 圖表分析的「單次活動」模式加一張路線地圖，用 GPS 軌跡畫出這次的路線。
- 滑鼠停在任何一張圖上時：
  - 地圖上標出當下的位置；
  - 其他 x 軸是時間（或距離）的圖表，同一個時間點也一起顯示 hover 標記和數值。
- 目的：對照同一時刻的紀錄，例如「這個功率時心率多少、爬升多少」「跑到哪一段時的心率和功率」。

## 設計重點（尚待確認）
- **地圖**：Leaflet（cdnjs）＋ OpenStreetMap 圖磚，或台灣魯地圖／經建版圖磚。
  - 軌跡來自 FIT 的 lat/long 通道。
  - 可以依心率、功率或坡度幫路線著色。
  - 在地圖上 hover，其他圖表也要反向同步。
- **同步 hover**：
  - 用 ECharts `echarts.connect(group)` 或自訂事件匯流排，把各圖的 axisPointer 對到同一個 x 值。
  - 各圖的 x 可能是時間或距離，要先換算成同一個樣本索引。
  - x 軸不是時間或距離的圖（散點、區間表）不參與同步。
- **放大檢視**：放大的單張圖也要參與同步，地圖可以一起放大。
- **底圖可切換，預設圖層在設定頁選**（使用者 2026-09-30 追加）。照 另一個專案
  `v2/app/src/map/basemaps.ts` 的清單：
  | id | 名稱 | 圖磚網址 | maxzoom | 備註 |
  |---|---|---|---|---|
  | rudy | 魯地圖 | `https://tile.happyman.idv.tw/map/rudy/{z}/{x}/{y}.png` | 18 | 官方主機常不回應，另一個專案 走自己的 `/api/tiles` 代理；這裡沒有代理，失敗時要提示換底圖 |
  | google-terrain | Google 地形 | `https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}` | 15 | 非官方端點，可能隨時失效；z16 以上等高線會消失，所以 maxzoom 設 15 |
  | nlsc-emap | NLSC 電子地圖 | `https://wmts.nlsc.gov.tw/wmts/EMAP/default/GoogleMapsCompatible/{z}/{y}/{x}` | 19 | 注意是 `{z}/{y}/{x}` |
  | nlsc-photo | 正射影像 | `https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/{z}/{y}/{x}` | 19 | 同上 |
  | osm | OSM | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | 19 | |
  - 疊圖（可開關）：
    - 等高線 `https://tile.happyman.idv.tw/map/moi_osm/{z}/{x}/{y}.png`（不透明度 0.8）
    - Google 道路 `lyrs=h`（0.9）
    - NLSC 道路 `EMAP2` `{z}/{y}/{x}`（0.9）
  - 設定頁：新增「預設底圖」和「預設疊圖」，存在設定裡；地圖上也能臨時切換。
  - 地圖上要標示各圖資的來源（attribution）。
- **要改的檔案**：
  - `backend/static/wko5_viewer.html`
  - workout 圖表 JSON 需要附上樣本的時間、距離、經緯度對照表，由後端提供，可能放在 `wko5views.py` 的 workout 端點。
