const CACHE_NAME = "criavideo-shell-v39";
const ASSETS = [
  "/video",
  "/video/static/index.html",
  "/video/static/style.css?v=20260404-05",
  "/video/static/app.js?v=20260404-05",
  "/video/static/pwa.js?v=20260404-05",
  "/video/static/icons/login-logo.png?v=20260404-05",
  "/video/static/icons/icon-192.png?v=20260404-05",
  "/video/static/icons/icon-512.png?v=20260404-05",
];

// HTML pages that should use network-first strategy
const HTML_PATHS = ["/video", "/video/static/index.html"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Never cache API requests.
  if (url.pathname.startsWith("/api/")) {
    return;
  }

  // Network-first for HTML pages so updates are seen immediately.
  if (HTML_PATHS.includes(url.pathname) || request.mode === "navigate") {
    event.respondWith(
      fetch(request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          return response;
        })
        .catch(() => caches.match(request).then((c) => c || caches.match("/video/static/index.html")))
    );
    return;
  }

  // Cache-first for static assets.
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) {
        return cached;
      }
      return fetch(request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          return response;
        })
        .catch(() => caches.match("/video/static/index.html"));
    })
  );
});
