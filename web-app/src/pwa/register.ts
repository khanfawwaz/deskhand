/** Registers the service worker, in production builds only (in development a worker would hide your own edits). */
export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || !('serviceWorker' in navigator)) return;
  const base = import.meta.env.BASE_URL;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register(`${base}sw.js`, { scope: base }).catch(() => {
      /* no worker (private window, blocked): the app simply runs without offline support */
    });
  });
}
