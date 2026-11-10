// Take screenshots of the chat against the fake backend (server.mjs must be running on 5181).
//   node tests/manual/chat/shots.mjs <output folder>
import puppeteer from '../../../../demo-video/node_modules/puppeteer-core/lib/esm/puppeteer/puppeteer-core.js';
import path from 'node:path';
import fs from 'node:fs';

const OUT = path.resolve(process.argv[2] ?? 'shots');
fs.mkdirSync(OUT, { recursive: true });
const ORIGIN = 'http://127.0.0.1:5181';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
  args: ['--no-sandbox'],
});

async function shot(name, { mode = 'idle', url = '/', theme = 'light', width = 1280, height = 820, act } = {}) {
  await fetch(`${ORIGIN}/__mode/${mode}`);
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  page.on('pageerror', (e) => console.log('PAGE ERROR', name, e.message));
  page.on('console', (m) => m.type() === 'error' && console.log('console.error', name, m.text()));
  await page.setViewport({
    width,
    height,
    deviceScaleFactor: width < 600 ? 2 : 1,
  });
  await page.evaluateOnNewDocument((th) => {
    localStorage.setItem('pilot.token', 'tok');
    localStorage.setItem('pilot.settings', JSON.stringify({ theme: th }));
  }, theme);
  await page.goto(ORIGIN + '/app-next' + url, { waitUntil: 'networkidle2' });
  await sleep(1200);
  if (act) await act(page);
  await sleep(500);
  const file = path.join(OUT, `${name}-${theme}-${width}.png`);
  await page.screenshot({ path: file });
  console.log('saved', file);
  await ctx.close();
}

const clickText = (page, text, sel = 'button') =>
  page.evaluate(
    (t, s) => {
      const b = [...document.querySelectorAll(s)].find((x) => x.textContent.trim().startsWith(t));
      b?.click();
      return !!b;
    },
    text,
    sel,
  );

for (const theme of ['light', 'dark']) {
  for (const width of [1280, 390]) {
    const size = { theme, width, height: width < 600 ? 780 : 820 };
    await shot('1-welcome', { ...size });
    await shot('2-running', { ...size, mode: 'running', url: '/chat/r1' });
    await shot('3-question', { ...size, mode: 'question', url: '/chat/r1' });
    if (theme === 'light') await shot('4-access', { ...size, mode: 'access', url: '/chat/r1' });
    await shot('5-finished-followup', {
      ...size,
      url: '/chat/f1',
      act: async (p) => {
        await p.type('#goal', 'Also add tea');
      },
    });
    if (width > 600)
      await shot('6-sidebar-trash', {
        ...size,
        url: '/chat/f1',
        act: async (p) => {
          await p.hover('li:has(button[aria-label="Delete this chat"])');
        },
      });
  }
}
// pick a computer on the orb, then the picker open
await shot('7-picker', {
  url: '/',
  act: async (p) => {
    await clickText(p, 'Office-PC');
    await clickText(p, 'Alex-Laptop', 'button[aria-pressed]');
    await p.click('button[aria-label="Choose which computer"]');
  },
});
await browser.close();
