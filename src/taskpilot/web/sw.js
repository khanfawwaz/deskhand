/* Deskhand service worker: makes the app installable and its shell load instantly/offline.
   It NEVER caches the API: tasks, tokens and results always come live from the server. */
const VERSION = 'deskhand-shell-v7';
const SHELL = ['./', 'index.html', 'app.js', 'logic.js', 'style.css', 'manifest.webmanifest',
  'icons/icon-192.png', 'icons/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || !url.pathname.startsWith('/app')) return; // API: network only
  e.respondWith(
    fetch(e.request).then((r) => {                       // network first, so updates arrive; cache as a fallback
      if (r.ok) { const copy = r.clone(); caches.open(VERSION).then((c) => c.put(e.request, copy)); }
      return r;
    }).catch(() => caches.match(e.request).then((hit) => hit || caches.match('index.html')))
  );
});
