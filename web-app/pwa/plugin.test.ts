import { describe, expect, it } from 'vitest';
import { buildServiceWorker, precacheList } from './plugin';

describe('the service worker build step', () => {
  it('keeps hashed assets, icons, the manifest and the theme file, and nothing else', () => {
    const list = precacheList(['index.html', 'assets/index-abc123.js', 'assets/index-def456.css', 'sw.js', 'assets/map-1.js.map'], ['icons/icon-192.png', 'manifest.webmanifest', 'theme-init.js', 'googleaa05301828e8707d.html']);
    expect(list).toEqual(['assets/index-abc123.js', 'assets/index-def456.css', 'icons/icon-192.png', 'manifest.webmanifest', 'theme-init.js']);
  });

  it('fills in the list and a version that changes exactly when the list does', () => {
    const template = "const VERSION = '__VERSION__';\nconst PRECACHE = __PRECACHE__;";
    const a = buildServiceWorker(template, ['assets/a-1.js']);
    const again = buildServiceWorker(template, ['assets/a-1.js']);
    const b = buildServiceWorker(template, ['assets/a-2.js']);
    expect(a).toBe(again);
    expect(a).toContain('"assets/a-1.js"');
    expect(a).not.toContain('__');
    expect(a).not.toBe(b);
  });
});
