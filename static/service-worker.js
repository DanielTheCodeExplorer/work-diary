const APP_ASSET_VERSION = "20260718-timezone-init-fix";
const CACHE_NAME = `work-diary-shell-${APP_ASSET_VERSION}`;
const NETWORK_TIMEOUT_MS = 5000;
const APP_SHELL = [
  "/",
  "/login.html",
  `/static/styles.css?v=${APP_ASSET_VERSION}`,
  `/static/app.js?v=${APP_ASSET_VERSION}`,
  `/static/login.js?v=${APP_ASSET_VERSION}`,
  "/manifest.webmanifest",
  "/favicon.svg",
  "/apple-touch-icon.png",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/notification-icon.png",
  "/static/notification-badge.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(APP_SHELL))
  );
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
  const url = new URL(event.request.url);
  if (
    event.request.method !== "GET"
    ||
    url.origin !== self.location.origin
    || url.pathname.startsWith("/api/")
    || url.pathname === "/config.js"
  ) {
    return;
  }

  const isVersionedAsset = url.pathname.startsWith("/static/")
    && url.searchParams.get("v") === APP_ASSET_VERSION;

  if (isVersionedAsset) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetchAndCache(event.request))
    );
    return;
  }

  event.respondWith(networkFirst(event.request));
});

async function fetchWithTimeout(request) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), NETWORK_TIMEOUT_MS);
  try {
    return await fetch(request, { signal: controller.signal });
  } finally {
    clearTimeout(timeout);
  }
}

async function fetchAndCache(request) {
  const response = await fetchWithTimeout(request);
  if (response.ok) {
    const cache = await caches.open(CACHE_NAME);
    await cache.put(request, response.clone());
  }
  return response;
}

async function networkFirst(request) {
  try {
    return await fetchAndCache(request);
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    if (request.mode === "navigate") {
      const shell = await caches.match("/");
      if (shell) return shell;
    }
    return new Response("Work Diary is temporarily unavailable.", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}

self.addEventListener("push", (event) => {
  let payload = {};
  if (event.data) {
    try {
      payload = event.data.json();
    } catch {
      payload = { body: event.data.text() };
    }
  }

  const title = payload.title || "Work Diary";
  const options = {
    body: payload.body || "You have a reminder.",
    icon: payload.icon || "/static/notification-icon.png",
    badge: payload.badge || "/static/notification-badge.png",
    tag: payload.tag || "work-diary-reminder",
    renotify: Boolean(payload.tag),
    data: {
      url: payload.url || "/?view=planner",
      task_id: payload.task_id || "",
    },
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = new URL(event.notification.data?.url || "/?view=planner", self.location.origin).href;

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if (client.url.startsWith(self.location.origin) && "focus" in client) {
          client.navigate(targetUrl);
          return client.focus();
        }
      }
      return self.clients.openWindow(targetUrl);
    })
  );
});
