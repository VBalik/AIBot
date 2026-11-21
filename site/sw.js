/* Дайджест ИИ — service worker: чтение без сети.
 *
 * Кэши:
 *   shell-*  — сама страница, иконки, манифест (обновляется при каждой версии)
 *   data     — feed.json (сначала сеть, без сети — сохранённая копия) и переводы full/*.json (зашифрованы)
 *   fonts    — шрифт Onest (лежит рядом со страницей, файлы неизменны)
 *   images   — иллюстрации из переводов (в том числе непрозрачные ответы чужих CDN)
 *
 * Страница присылает сообщение {type: "cache-images", urls: [...]}: SW докачивает иллюстрации в фоне,
 * чтобы они были доступны офлайн, даже если статью ещё не открывали.
 */
const VERSION = "v4";
const SHELL = `shell-${VERSION}`;
const DATA = "data";
const FONTS = "fonts";
const IMAGES = "images";
const MAX_IMAGES = 800;
const SHELL_FILES = [
  "./", "index.html", "favicon.svg", "manifest.webmanifest",
  "icons/app-192.png", "icons/app-512.png", "icons/app-maskable-512.png", "icons/app-180.png",
];
const FONT_FILES = ["fonts/onest-cyrillic.woff2", "fonts/onest-latin.woff2"];

self.addEventListener("install", (event) => {
  event.waitUntil(Promise.all([
    caches.open(SHELL).then((c) => c.addAll(SHELL_FILES)),
    caches.open(FONTS).then((c) => c.addAll(FONT_FILES)),
  ]).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith("shell-") && key !== SHELL) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

const noQuery = (url) => { const u = new URL(url); u.search = ""; return u.toString(); };
const isFeed = (url) => /\/feed\.json$/.test(url.pathname);
const isFull = (url) => /\/full\/[^/]+\.json$/.test(url.pathname);
const isFont = (url) => url.origin === self.location.origin && /\/fonts\/[^/]+\.woff2$/.test(url.pathname);

async function networkFirst(request, cacheName, key, timeoutMs) {
  const cache = await caches.open(cacheName);
  const ctrl = new AbortController();
  const timer = timeoutMs ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
  try {
    const resp = await fetch(request, { signal: ctrl.signal });
    if (resp.ok) await cache.put(key, resp.clone());
    return resp;
  } catch (err) {
    const cached = await cache.match(key);
    if (cached) return cached;
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function cacheFirst(request, cacheName, key) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(key);
  if (cached) return cached;
  const resp = await fetch(request);
  if (resp.ok || resp.type === "opaque") {
    await cache.put(key, resp.clone());
    if (cacheName === IMAGES) trimImages();
  }
  return resp;
}

let trimming = false;
async function trimImages() {
  if (trimming) return;
  trimming = true;
  try {
    const cache = await caches.open(IMAGES);
    const keys = await cache.keys();
    for (let i = 0; i < keys.length - MAX_IMAGES; i++) await cache.delete(keys[i]);  // самые старые
  } finally {
    trimming = false;
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  if (isFeed(url)) {
    event.respondWith(networkFirst(req, DATA, noQuery(req.url), 8000));
  } else if (isFull(url)) {
    event.respondWith(cacheFirst(req, DATA, noQuery(req.url)));
  } else if (isFont(url)) {
    event.respondWith(cacheFirst(req, FONTS, noQuery(req.url)));
  } else if (url.origin === self.location.origin) {
    // страница: сначала сеть (чтобы обновления приходили сразу), без сети — сохранённая копия
    const key = req.mode === "navigate" ? new URL("index.html", self.registration.scope).toString() : noQuery(req.url);
    event.respondWith(networkFirst(req, SHELL, key, 5000).catch(async () =>
      (await caches.match(key)) || (await caches.match(new URL("./", self.registration.scope).toString())) || Response.error()));
  } else if (req.destination === "image") {
    event.respondWith(cacheFirst(req, IMAGES, req.url).catch(() => Response.error()));
  }
});

// Фоновая докачка иллюстраций по списку от страницы
self.addEventListener("message", (event) => {
  const msg = event.data || {};
  if (msg.type !== "cache-images" || !Array.isArray(msg.urls)) return;
  event.waitUntil((async () => {
    const cache = await caches.open(IMAGES);
    const queue = msg.urls.filter((u) => /^https:\/\//.test(u));
    let saved = 0;
    async function worker() {
      while (queue.length) {
        const u = queue.shift();
        if (await cache.match(u)) { saved++; continue; }
        try {
          const resp = await fetch(new Request(u, { mode: "no-cors", referrerPolicy: "no-referrer" }));
          if (resp.ok || resp.type === "opaque") { await cache.put(u, resp); saved++; }
        } catch { /* нет сети или картинка недоступна — попробуем в следующий раз */ }
      }
    }
    await Promise.all([worker(), worker(), worker(), worker()]);
    await trimImages();
    if (event.source) event.source.postMessage({ type: "images-cached", saved, total: msg.urls.length });
  })());
});
