// Bundle-size check: the gzip size of what a first visit downloads (the scripts and style sheets index.html loads),
// against a budget. Run after a build:  node scripts/check-bundle.mjs [--budget 200]
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import zlib from 'node:zlib';

const here = path.dirname(fileURLToPath(import.meta.url));

/** The script and style sheet files that index.html loads on the first visit (module scripts, preloads, style links). */
export function firstLoadFiles(html) {
  const found = new Set();
  const tag = /<(script|link)\b[^>]*>/gi;
  for (const m of html.matchAll(tag)) {
    const t = m[0];
    const attr = (name) => new RegExp('(?:^|[\\s<])' + name + '=["\']([^"\']+)["\']', 'i').exec(t)?.[1];
    const target = m[1].toLowerCase() === 'script' ? attr('src') : attr('href');
    const rel = (attr('rel') || '').toLowerCase();
    if (!target || /^https?:/.test(target)) continue;
    if (m[1].toLowerCase() === 'link' && !['stylesheet', 'modulepreload'].includes(rel)) continue;
    if (/\.(js|mjs|css)$/.test(target)) found.add(target);
  }
  return [...found];
}

export const gzipSize = (buf) => zlib.gzipSync(buf, { level: 9 }).length;

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const arg = process.argv.indexOf('--budget');
  const budgetKb = arg > 0 ? Number(process.argv[arg + 1]) : 200;
  const dist = path.resolve(here, '..', 'dist');
  const html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8');
  const base = /<script[^>]*src="([^"]*)\/assets\//.exec(html)?.[1] ?? '';
  let js = 0;
  let css = 0;
  for (const f of firstLoadFiles(html)) {
    const rel = f.startsWith(base) ? f.slice(base.length).replace(/^\//, '') : f.replace(/^\//, '');
    const size = gzipSize(fs.readFileSync(path.join(dist, rel)));
    if (f.endsWith('.css')) css += size;
    else js += size;
    console.log(`${(size / 1024).toFixed(1).padStart(7)} KB gzip  ${rel}`);
  }
  const total = (js + css) / 1024;
  console.log(`first load: ${(js / 1024).toFixed(1)} KB JS + ${(css / 1024).toFixed(1)} KB CSS = ${total.toFixed(1)} KB gzip (budget ${budgetKb} KB)`);
  if (total > budgetKb) {
    console.error('over budget');
    process.exit(1);
  }
}
