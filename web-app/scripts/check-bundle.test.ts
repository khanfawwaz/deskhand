import { describe, expect, it } from 'vitest';
// @ts-expect-error a plain .mjs script with no type declarations
import { firstLoadFiles, gzipSize } from './check-bundle.mjs';

describe('the bundle check', () => {
  it('lists the scripts and styles of the first load, not the rest of the page', () => {
    const html = `<link rel="manifest" href="/app/manifest.webmanifest">
      <script src="/app/theme-init.js"></script><script type="module" crossorigin src="/app/assets/index-1.js"></script>
      <link rel="modulepreload" href="/app/assets/vendor-2.js"><link rel="stylesheet" href="/app/assets/index-3.css">
      <link rel="icon" href="/app/icons/icon-192.png"><script src="https://example.com/x.js"></script>`;
    expect(firstLoadFiles(html)).toEqual(['/app/theme-init.js', '/app/assets/index-1.js', '/app/assets/vendor-2.js', '/app/assets/index-3.css']);
  });
  it('measures gzip size', () => {
    expect(gzipSize(Buffer.from('a'.repeat(10000)))).toBeLessThan(100);
  });
});
