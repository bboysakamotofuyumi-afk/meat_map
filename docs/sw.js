const CACHE_NAME = "tmm-cache-v1";
const ASSETS = [
  "./map_demo.html",
  "./manifest.webmanifest",
  "./sw.js",
  "./ogp.png",
  "./assets/icons/icon-192.png",
  "./assets/icons/icon-512.png",
  "./assets/pins/steak.png",
  "./assets/pins/yakiniku.png",
  "./assets/pins/churrasco.png",
  "./assets/pins/yakitori.png",
  "./assets/pins/shabushabu.png",
  "./assets/pins/motsuyaki.png",
  "./assets/pins/korea.png",
  "./assets/pins/china.png",
  "./assets/pins/cluster.png",
  "./assets/pins/motsuyaki.png",
  "./output/meatmap.csv"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req).then((res) => {
        const resClone = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(req, resClone)).catch(() => {});
        return res;
      });
    })
  );
});
