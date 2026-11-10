/* Deskhand service worker. It makes the app installable and lets its shell open without a network.
   Rules:
   - The list of files to keep (PRECACHE) is filled in at build time. Their names carry a content hash, so they never change.
   - Pages (index.html, and any address the router handles) are fetched from the NETWORK FIRST, so a new release shows up
     on the next load. The saved copy is only used when the network is down: the app is never stale after a deploy.
   - Files in assets/ are saved the first time and reused (their name changes when their content does).
   - It NEVER touches the API (/v1, /agent): tasks, tokens and results always come live. It only sees addresses under its own scope.
   - Old caches are deleted when a new version takes over, including the original app's (it used the same /app/sw.js). */
const VERSION = '__VERSION__';
const CACHE = 'deskhand-app-' + VERSION;
const PRECACHE = __PRECACHE__;

const scope = new URL(self.registration.scope);
const shell = new URL('index.html', scope).href;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll([shell, ...PRECACHE.map((p) => new URL(p, scope).href)]))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))) // every older cache, the original app's too
      .then(() => self.clients.claim()),
  );
});

async function networkFirst(request, fallbackKey) {
  const cache = await caches.open(CACHE);
  try {
    const fresh = await fetch(request, { cache: 'no-cache' });
    if (fresh.ok) cache.put(fallbackKey || request, fresh.clone());
    return fresh;
  } catch (err) {
    const hit = await cache.match(fallbackKey || request);
    if (hit) return hit;
    throw err;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const fresh = await fetch(request);
  if (fresh.ok) cache.put(request, fresh.clone());
  return fresh;
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== 'GET' || url.origin !== scope.origin || !url.href.startsWith(scope.href)) return; // not ours: the browser handles it
  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, shell)); // any page address is the same app shell
  } else if (url.pathname.startsWith(scope.pathname + 'assets/')) {
    event.respondWith(cacheFirst(request));
  } else if (url.pathname !== scope.pathname + 'sw.js') {
    event.respondWith(networkFirst(request));
  }
});
