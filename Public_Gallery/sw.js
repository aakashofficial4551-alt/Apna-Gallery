const CACHE_NAME = 'phantx-cache-v1';
const urlsToCache = [
  '/',
  '/static/style.css',
  '/manifest.json'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
  );
});

self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        if (response) return response; // Cache hit
        return fetch(event.request);   // Network request
      })
  );
});
