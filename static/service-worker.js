const APP_ASSET_VERSION = "20260718-production-hardening";
const CACHE_NAME = `work-diary-shell-${APP_ASSET_VERSION}`;
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
    url.origin !== self.location.origin
    || url.pathname.startsWith("/api/")
    || url.pathname === "/config.js"
  ) {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (event.request.method === "GET" && response.ok) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

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
