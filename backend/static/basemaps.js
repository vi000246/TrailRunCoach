/* Basemaps and overlays for Leaflet maps, and the settings-page default.
 *
 * The same definitions the single-activity route map uses (wko5_viewer.html,
 * "route map" section). The viewer still carries its own copy; point it at
 * this file when that page is next edited so there is one list.
 *
 *   <script src="/api/v1/static/basemaps.js"></script>
 *   const d = await MapLayers.defaults();          // { base, overlays } from /api/v1/sync/settings
 *   MapLayers.attach(map, d, "routes.map");        // layer switch, remembered per browser
 */
(() => {
  const NLSC_ATTR = '© <a href="https://maps.nlsc.gov.tw/" target="_blank" rel="noopener">內政部國土測繪中心</a>';
  const BASEMAPS = [
    { id: "rudy", name: "魯地圖", url: "https://tile.happyman.idv.tw/map/rudy/{z}/{x}/{y}.png", maxZoom: 18,
      attr: '<a href="https://rudy.basecamp.tw/" target="_blank" rel="noopener">魯地圖</a> · © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> 貢獻者' },
    { id: "google-terrain", name: "Google 地形", url: "https://mt1.google.com/vt/lyrs=p&x={x}&y={y}&z={z}", maxZoom: 15,
      attr: "地圖資料 © Google" },
    { id: "nlsc-emap", name: "NLSC 電子地圖", url: "https://wmts.nlsc.gov.tw/wmts/EMAP/default/GoogleMapsCompatible/{z}/{y}/{x}",
      maxZoom: 19, attr: NLSC_ATTR },
    { id: "nlsc-photo", name: "正射影像", url: "https://wmts.nlsc.gov.tw/wmts/PHOTO2/default/GoogleMapsCompatible/{z}/{y}/{x}",
      maxZoom: 19, attr: NLSC_ATTR },
    { id: "osm", name: "OSM", url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png", maxZoom: 19,
      attr: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> 貢獻者' },
  ];
  const OVERLAYS = [
    { id: "contour", name: "等高線", url: "https://tile.happyman.idv.tw/map/moi_osm/{z}/{x}/{y}.png", opacity: 0.8, maxZoom: 18,
      attr: '等高線：<a href="https://rudy.basecamp.tw/" target="_blank" rel="noopener">魯地圖</a>' },
    { id: "google-roads", name: "Google 道路", url: "https://mt1.google.com/vt/lyrs=h&x={x}&y={y}&z={z}", opacity: 0.9, maxZoom: 19,
      attr: "道路 © Google" },
    { id: "nlsc-roads", name: "NLSC 道路", url: "https://wmts.nlsc.gov.tw/wmts/EMAP2/default/GoogleMapsCompatible/{z}/{y}/{x}",
      opacity: 0.9, maxZoom: 19, attr: NLSC_ATTR },
  ];
  const BASE_BY_ID = Object.fromEntries(BASEMAPS.map((b) => [b.id, b]));
  const OVL_BY_ID = Object.fromEntries(OVERLAYS.map((o) => [o.id, o]));

  let cached = null;
  async function defaults() {
    if (cached) return cached;
    let us = {};
    try { us = await (await fetch("/api/v1/sync/settings")).json(); } catch {}
    cached = { base: BASE_BY_ID[us.map_basemap] ? us.map_basemap : "rudy",
      overlays: (us.map_overlays || []).filter((id) => OVL_BY_ID[id]) };
    return cached;
  }

  // per-browser switch, kept only while the settings-page default it was made against is unchanged
  function prefs(key, d) {
    let o = null;
    try { o = JSON.parse(localStorage.getItem(key) || "null"); } catch {}
    const same = o && o.against === JSON.stringify(d);
    return { base: same && BASE_BY_ID[o.base] ? o.base : d.base,
      overlays: same && Array.isArray(o.overlays) ? o.overlays.filter((id) => OVL_BY_ID[id]) : d.overlays };
  }
  function savePrefs(key, d, p) {
    try { localStorage.setItem(key, JSON.stringify({ ...p, against: JSON.stringify(d) })); } catch {}
  }

  function attach(map, d, key) {
    const p = prefs(key, d);
    const bases = {}, ovls = {};
    for (const b of BASEMAPS) bases[b.name] = L.tileLayer(b.url, { maxZoom: b.maxZoom, attribution: b.attr });
    for (const o of OVERLAYS) ovls[o.name] = L.tileLayer(o.url, { maxZoom: o.maxZoom, opacity: o.opacity, attribution: o.attr, zIndex: 5 });
    bases[BASE_BY_ID[p.base].name].addTo(map);
    for (const id of p.overlays) ovls[OVL_BY_ID[id].name].addTo(map);
    L.control.layers(bases, ovls, { collapsed: true }).addTo(map);
    const save = () => savePrefs(key, d, {
      base: BASEMAPS.find((b) => map.hasLayer(bases[b.name]))?.id || p.base,
      overlays: OVERLAYS.filter((o) => map.hasLayer(ovls[o.name])).map((o) => o.id) });
    map.on("baselayerchange overlayadd overlayremove", save);
  }

  window.MapLayers = { BASEMAPS, OVERLAYS, defaults, attach };
})();
