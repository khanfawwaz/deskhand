import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Plugin } from 'vite';

/** Files that make up the app shell and never need the network once saved. Everything is relative to the app base. */
export function precacheList(emitted: string[], publicFiles: string[]): string[] {
  const keep = (f: string) => /^assets\/.+\.(js|css|woff2?|png|svg)$/.test(f) || f === 'manifest.webmanifest' || f === 'theme-init.js' || /^icons\//.test(f);
  return [...new Set([...emitted, ...publicFiles])].filter(keep).sort();
}

/** The service worker source with its file list and version filled in. The version is a hash of the list, so it changes
 *  exactly when a hashed file does, and the browser then installs the new worker by itself. */
export function buildServiceWorker(template: string, files: string[]): string {
  const version = createHash('sha256').update(files.join('\n')).digest('hex').slice(0, 12);
  return template.replace('__VERSION__', version).replace('__PRECACHE__', JSON.stringify(files, null, 2));
}

const HERE = path.dirname(fileURLToPath(import.meta.url));

function walk(dir: string, prefix = ''): string[] {
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => (e.isDirectory() ? walk(path.join(dir, e.name), prefix + e.name + '/') : [prefix + e.name]));
}

/** Adds sw.js to the build output (production builds only; the dev server has no service worker). */
export function serviceWorkerPlugin(): Plugin {
  const template = path.resolve(HERE, 'sw.template.js');
  return {
    name: 'deskhand-service-worker',
    apply: 'build',
    generateBundle(_options, bundle) {
      const publicDir = path.resolve(HERE, '..', 'public');
      const files = precacheList(Object.keys(bundle), walk(publicDir));
      this.emitFile({ type: 'asset', fileName: 'sw.js', source: buildServiceWorker(fs.readFileSync(template, 'utf8'), files) });
    },
  };
}
