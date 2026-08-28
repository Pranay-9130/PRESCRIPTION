/* RxVault service worker — cache app shell only. Clinical records stay in IndexedDB. */
const CACHE_NAME = "rxvault-shell-v1";
const OFFLINE_URL = "/offline";
const PRECACHE = [
  OFFLINE_URL,
  "/static/style.css",
  "/static/js/pwa.js",
  "/static/js/sync-status.js",
  "/static/js/offline-store.js",
  "/static/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") {
    return;
  }

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) {
    return;
  }

  if (url.pathname.startsWith("/api/")) {
    return;
  }

  const isStatic = url.pathname.startsWith("/static/");
  if (isStatic) {
    event.respondWith(cacheFirst(request));
    return;
  }

  event.respondWith(networkFirst(request));
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) {
    return cached;
  }
  try {
    const response = await fetch(request);
    const cache = await caches.open(CACHE_NAME);
    cache.put(request, response.clone());
    return response;
  } catch (err) {
    return cached || Response.error();
  }
}

async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response && response.ok && response.type === "basic") {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) {
      return cached;
    }
    if (request.mode === "navigate") {
      const offline = await caches.match(OFFLINE_URL);
      if (offline) {
        return offline;
      }
    }
    return new Response("Offline", { status: 503, statusText: "Offline" });
  }
}

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});
