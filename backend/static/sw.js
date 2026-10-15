/* The service worker (SP-314): an offline, read-only copy of 總覽 / 課表 for the person signed
 * in. Served at /sw.js (backend/api/pwa.py: scope "/", no-cache, __TRC_BUILD__ replaced by a
 * digest of the static files, so a deploy installs a new worker). The rules and the engine are
 * in offline_core.js (tested under node); this file only wires the worker's events to it.
 *
 * Messages from the pages (shell.js):
 *   {type: "session", session}  who the page is for; another person than before: the copy goes
 *   {type: "sync", force?}      refill p0 → p1 → p2 when due (force: p0 now, e.g. back online)
 *   {type: "logout"}            forget every person's copy (shell.js deletes the caches too)
 *   {type: "manual"}            keep the whole manual for offline (its pages, search index, pictures)
 *   {type: "status"}            answered on the message port: whose copy, bytes per tier, which
 *                               tiers' caches are still there (設定 › 這台裝置的離線資料, SP-322)
 */
const VERSION = "__TRC_BUILD__";
importScripts("/api/v1/static/offline_core.js?v=" + VERSION);

const engine = self.TrcOffline.createEngine({
  caches: self.caches,
  fetch: (req, init) => self.fetch(req, init),
  origin: self.location.origin,
  version: VERSION,
});

self.addEventListener("install", (event) => {
  event.waitUntil(engine.install().catch(() => {}).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(engine.activate().catch(() => {}).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (event) => {
  const p = engine.handleFetch(event.request, { waitUntil: (q) => event.waitUntil(q) });
  if (p) event.respondWith(p);
});

self.addEventListener("message", (event) => {
  const d = event.data || {};
  const port = event.ports && event.ports[0];
  const reply = (v) => { if (port) port.postMessage(v); };
  let p = null;
  if (d.type === "session") p = engine.setSession(d.session).then(() => engine.sync({}));
  else if (d.type === "sync") p = engine.sync({ force: !!d.force });
  else if (d.type === "logout") p = engine.clearUser();
  else if (d.type === "status") p = engine.status();
  else if (d.type === "manual") p = engine.warmManual();          // a manual page: keep all of it (SP-558)
  if (p) event.waitUntil(p.then(reply, (e) => reply({ error: String(e) })));
});
