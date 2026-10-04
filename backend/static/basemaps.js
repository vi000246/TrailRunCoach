/* Leaflet maps shared by the single-activity route map (wko5_viewer.html), the routes page
 * (routes.html) and the race calculator's course map (racepower.html): the basemaps /
 * overlays with the settings-page default, and the route drawing (halo + line + start /
 * finish, the hover marker, the nearest-point lookup).
 *
 *   <script src="/api/v1/static/basemaps.js"></script>
 *   const d = await MapLayers.defaults();          // { base, overlays } from /api/v1/sync/settings
 *   MapLayers.attach(map, d, "routes.map");        // layer switch, remembered per browser
 *   MapLayers.attach(map, d, key, { hint: el });   // + offer another basemap when tiles fail
 *   MapLayers.track(group, latlngs, { color });    // or { pieces: [{ segs: [[a, b], …], color }] }
 *   const near = MapLayers.picker(map, latlngs);   // near(e.layerPoint) -> index within 24 px, or -1
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

  // the settings page's default ({map_basemap, map_overlays} of /api/v1/sync/settings)
  const fromSettings = (us) => ({ base: BASE_BY_ID[us?.map_basemap] ? us.map_basemap : "rudy",
    overlays: (us?.map_overlays || []).filter((id) => OVL_BY_ID[id]) });
  let cached = null;
  async function defaults() {
    if (cached) return cached;
    let us = {};
    try { us = await (await fetch("/api/v1/sync/settings")).json(); } catch {}
    cached = fromSettings(us);
    return cached;
  }

  // per-browser switch, kept only while the settings-page default it was made against is unchanged;
  // other keys a page keeps under the same entry (the viewer's route colour) ride along
  function prefs(key, d) {
    let o = null;
    try { o = JSON.parse(localStorage.getItem(key) || "null"); } catch {}
    const same = o && o.against === JSON.stringify(d);
    const { against, ...rest } = o || {};
    return { ...rest, base: same && BASE_BY_ID[o.base] ? o.base : d.base,
      overlays: same && Array.isArray(o.overlays) ? o.overlays.filter((id) => OVL_BY_ID[id]) : d.overlays };
  }
  function savePrefs(key, d, patch) {
    try { localStorage.setItem(key, JSON.stringify({ ...prefs(key, d), ...patch, against: JSON.stringify(d) })); } catch {}
  }

  // opts.hint: an element that offers another basemap when the active one's tiles keep failing
  // (魯地圖's own host often doesn't answer); the map's max zoom follows the active basemap.
  function attach(map, d, key, opts = {}) {
    const p = prefs(key, d), hint = opts.hint;
    const bases = {}, ovls = {};
    let active = null;
    for (const b of BASEMAPS) {
      const layer = L.tileLayer(b.url, { maxZoom: b.maxZoom, attribution: b.attr });
      layer._def = b; layer._err = 0; layer._ok = 0;
      if (hint) {
        layer.on("tileload", () => { layer._ok++; if (layer === active) hint.hidden = true; });
        layer.on("tileerror", () => {
          layer._err++;
          if (layer !== active || layer._ok > 0 || layer._err < 3 || !hint.hidden) return;
          const alt = BASEMAPS.filter((x) => x.id !== b.id && x.id !== "google-terrain").slice(0, 3);
          hint.innerHTML = `「${b.name}」的圖磚載入失敗（主機可能沒有回應），換個底圖：`
            + alt.map((x) => `<button type="button" data-b="${x.id}">${x.name}</button>`).join("");
          hint.hidden = false;
        });
      }
      bases[b.name] = layer;
    }
    for (const o of OVERLAYS) ovls[o.name] = L.tileLayer(o.url, { maxZoom: o.maxZoom, opacity: o.opacity, attribution: o.attr, zIndex: 5 });
    const setBase = (layer) => {
      active = layer; map.setMaxZoom(layer._def.maxZoom); layer._err = layer._ok = 0;
      if (hint) hint.hidden = true;
    };
    if (hint) hint.onclick = (e) => {
      const id = e.target.closest("button[data-b]")?.dataset.b; if (!id) return;
      if (active) map.removeLayer(active);
      bases[BASE_BY_ID[id].name].addTo(map);    // fires baselayerchange through the layer control
    };
    const first = bases[BASE_BY_ID[p.base].name];
    first.addTo(map); setBase(first);
    for (const id of p.overlays) ovls[OVL_BY_ID[id].name].addTo(map);
    L.control.layers(bases, ovls, { collapsed: true }).addTo(map);
    map.on("baselayerchange", (e) => setBase(e.layer));
    const save = () => savePrefs(key, d, {
      base: BASEMAPS.find((b) => map.hasLayer(bases[b.name]))?.id || p.base,
      overlays: OVERLAYS.filter((o) => map.hasLayer(ovls[o.name])).map((o) => o.id) });
    map.on("baselayerchange overlayadd overlayremove", save);
    return { bases, ovls };
  }

  // a route on `group` (a layerGroup, cleared first): a white halo, then either one line in
  // opts.color or opts.pieces = [{ segs: [[a, b], …] (or one path), color }], then the start
  // (green) and finish (red) dots. opts.start / opts.end: their tooltips.
  function track(group, latlngs, opts = {}) {
    group.clearLayers();
    if (latlngs.length < 2) return;
    L.polyline(latlngs, { color: "#fff", weight: 7, opacity: 0.75, interactive: false }).addTo(group);
    if (opts.pieces) {
      for (const pc of opts.pieces) if (pc.segs.length) L.polyline(pc.segs, { color: pc.color, weight: 4, opacity: 0.95, interactive: false }).addTo(group);
    } else {
      L.polyline(latlngs, { color: opts.color || "#2563eb", weight: 4, interactive: false }).addTo(group);
    }
    const dot = (ll, fill, tip) => L.circleMarker(ll, { radius: 6, color: "#fff", weight: 2, fillColor: fill, fillOpacity: 1 })
      .bindTooltip(tip).addTo(group);
    dot(latlngs[0], "#16a34a", opts.start || "起點");
    dot(latlngs[latlngs.length - 1], "#dc2626", opts.end || "終點");
  }

  // the hover marker: a dark dot with a permanent readout above it
  function marker() {
    const m = L.circleMarker([0, 0], { radius: 7, color: "#fff", weight: 2.5, fillColor: "#111827", fillOpacity: 0.9, interactive: false });
    m.bindTooltip("", { permanent: true, direction: "top", offset: [0, -8], className: "hovtip" });
    return m;
  }

  // nearest route point to a layer point (map mousemove e.layerPoint), within `px`; -1 when none.
  // The projected points are cached until the next zoom.
  function picker(map, latlngs, px = 24) {
    let pts = null;
    map.on("zoomend viewreset", () => { pts = null; });
    return (p) => {
      pts ||= latlngs.map((ll) => map.latLngToLayerPoint(ll));
      let best = -1, bd = px * px;
      for (let k = 0; k < pts.length; k++) {
        const dx = pts[k].x - p.x, dy = pts[k].y - p.y, d2 = dx * dx + dy * dy;
        if (d2 < bd) { bd = d2; best = k; }
      }
      return best;
    };
  }

  window.MapLayers = { BASEMAPS, OVERLAYS, BASE_BY_ID, OVL_BY_ID, fromSettings, defaults, prefs, savePrefs, attach, track, marker, picker };
})();
