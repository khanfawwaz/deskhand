// Copies the words of the privacy policy and the terms from the server's pages (src/taskpilot/web/*.html, the one source
// of the text) into the React app (src/features/legal/content/*.html). Run after editing either page:
//   node scripts/sync-legal.mjs          (a unit test fails when the copies are out of step)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const source = path.resolve(here, '..', '..', 'src', 'taskpilot', 'web');
const target = path.resolve(here, '..', 'src', 'features', 'legal', 'content');

/** The inside of <main>...</main>, tidied the same way every time. */
export function mainOf(html) {
  const m = /<main[^>]*>([\s\S]*?)<\/main>/i.exec(html);
  if (!m) throw new Error('no <main> in the page');
  return m[1].replace(/\r\n/g, '\n').trim() + '\n';
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  fs.mkdirSync(target, { recursive: true });
  for (const name of ['privacy', 'terms']) {
    const body = mainOf(fs.readFileSync(path.join(source, name + '.html'), 'utf8'));
    fs.writeFileSync(path.join(target, name + '.html'), body);
    console.log('synced', name, body.length, 'characters');
  }
}
