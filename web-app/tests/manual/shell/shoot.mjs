// Takes the screenshots used to eyeball the shell (orb, theme flip, legal pages, phone drawer, palette, Chrome tip).
//   npm run build && node tests/manual/shell/shoot.mjs <output folder>
// It runs the built app against the same fake server the end-to-end tests use.
import fs from 'node:fs';
import path from 'node:path';
import { chromium } from '@playwright/test';
import { createFakeBackend, TOKEN } from '../../e2e/fakeBackend.mjs';

const out = path.resolve(process.argv[2] || 'shots');
fs.mkdirSync(out, { recursive: true });
const server = createFakeBackend();
await new Promise((r) => server.listen(4180, '127.0.0.1', r));
const origin = 'http://127.0.0.1:4180';
const BASE = origin + '/app-next/';
const chrome = 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const browser = await chromium.launch(fs.existsSync(chrome) ? { executablePath: chrome } : {});

async function open(viewport, scheme) {
  const ctx = await browser.newContext({ viewport, colorScheme: scheme, serviceWorkers: 'block' });
  await ctx.addInitScript((t) => localStorage.setItem('pilot.token', t), TOKEN);
  return { ctx, page: await ctx.newPage() };
}
const shot = (page, name) => page.screenshot({ path: path.join(out, name + '.png') });
await fetch(origin + '/__test/reset');
await fetch(origin + '/__test/connect', { method: 'POST', body: JSON.stringify({ name: 'Alex-Laptop' }) });

for (const scheme of ['light', 'dark']) {
  const { ctx, page } = await open({ width: 1200, height: 800 }, scheme);
  await page.goto(BASE);
  await page.getByTestId('orb').waitFor();
  await shot(page, `home-${scheme}`);
  // orb mid-stretch: hold on the drop and pull part of the way
  const box = await page.getByTestId('orb').boundingBox();
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.move(cx + 20, cy);
  await page.mouse.down();
  for (let i = 1; i <= 9; i++) await page.mouse.move(cx + 20 + i * 12, cy - i * 3, { steps: 2 });
  await page.waitForTimeout(450);
  await shot(page, `orb-pull-${scheme}`);
  for (let i = 10; i <= 24; i++) await page.mouse.move(cx + 20 + i * 14, cy - i * 3, { steps: 2 });
  await page.waitForTimeout(500);
  await page.mouse.up();
  await page.waitForTimeout(500);
  await shot(page, `orb-after-flip-from-${scheme}`);
  await page.keyboard.press('Control+k');
  await page.waitForTimeout(300);
  await shot(page, `palette-${scheme}`);
  await page.keyboard.press('Escape');
  await page.goto(BASE + 'privacy');
  await page.getByRole('heading', { level: 1 }).waitFor();
  await shot(page, `privacy-${scheme}`);
  await ctx.close();
}
for (const scheme of ['light', 'dark']) {
  const { ctx, page } = await open({ width: 390, height: 800 }, scheme);
  await page.goto(BASE);
  await page.getByTestId('orb').waitFor();
  await shot(page, `phone-home-${scheme}`);
  await page.getByRole('button', { name: 'Open the menu' }).click();
  await page.waitForTimeout(400);
  await shot(page, `phone-drawer-${scheme}`);
  await page.goto(BASE + 'terms');
  await page.getByRole('heading', { level: 1 }).waitFor();
  await shot(page, `phone-terms-${scheme}`);
  await ctx.close();
}
await browser.close();
server.close();
console.log('screenshots in', out);
