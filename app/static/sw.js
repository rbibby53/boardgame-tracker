/* Service worker: makes the app installable and lets you open "Log a play" offline.
   Only static assets, the log-a-play page and the offline page are cached. */
const VERSION = "__ASSET_VERSION__";
const STATIC = `gn-static-${VERSION}`;
const PAGES = `gn-pages-${VERSION}`;
const PRECACHE = [
  "/offline",
  "/static/app.css?v=" + VERSION,
  "/static/app.js?v=" + VERSION,
  "/static/play-form.js?v=" + VERSION,
  "/static/vendor/chart.umd.min.js?v=" + VERSION,
  "/static/favicon.svg",
  "/static/icons/icon-192.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(STATIC).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== STATIC && k !== PAGES).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (e) => {
  if (e.data === "clear") e.waitUntil(caches.keys().then((ks) => Promise.all(ks.map((k) => caches.delete(k)))));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;

  if (url.pathname.startsWith("/static/")) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res.ok) { const copy = res.clone(); caches.open(STATIC).then((c) => c.put(req, copy)); }
      return res;
    })));
    return;
  }

  if (req.mode === "navigate") {
    const cacheable = url.pathname === "/plays/new";
    e.respondWith(
      fetch(req)
        .then((res) => {
          // Only cache a real page (not a redirect to sign-in).
          if (cacheable && res.ok && !res.redirected) {
            const copy = res.clone();
            caches.open(PAGES).then((c) => c.put("/plays/new", copy));
          }
          return res;
        })
        .catch(async () => (cacheable && (await caches.match("/plays/new"))) || caches.match("/offline"))
    );
  }
});
