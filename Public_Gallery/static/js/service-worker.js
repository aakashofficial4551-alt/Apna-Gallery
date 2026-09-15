const CACHE_NAME = 'apna-gallery-v1';

self.addEventListener('install', (e) => {
  console.log('[Service Worker] Install');
});

self.addEventListener('fetch', (e) => {
  // Pass through all requests directly to the network.
  // This satisfies the PWA install requirement for Android/Chrome.
  e.respondWith(fetch(e.request));
});
