// AnythingLLM Console service worker: shell only, never caches API or session traffic.
const SHELL = "alc-shell-v1";
const FILES = ["/", "/support.js", "/manifest.webmanifest", "/icon-192.png", "/icon-512.png"];
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES).catch(() => null)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ui/")) return;
  const isNav = e.request.mode === "navigate";
  e.respondWith(
    fetch(e.request).then((res) => {
      // Only cache real successes: caching 404/500 shells poisoned the offline copy.
      if (res.ok && res.type === "basic") {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(e.request, copy)).catch(() => null);
      }
      return res;
    }).catch(() => caches.match(e.request).then((hit) => {
      // Falling back to "/" for a script or image request served HTML in its place.
      if (hit) return hit;
      if (isNav) return caches.match("/");
      return Response.error();
    }))
  );
});
