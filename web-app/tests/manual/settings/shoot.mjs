/* global console, process, fetch, document, localStorage, setTimeout */
// Screenshots of the Connect and Settings dialogs against serve.mjs (port 5182).
//   node shoot.mjs <output folder>
import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs';
const require = createRequire(path.resolve('../demo-video/package.json'));
const puppeteer = require('puppeteer-core');

const out = path.resolve(process.argv[2] ?? 'shots');
fs.mkdirSync(out, { recursive: true });
const ORIGIN = 'http://localhost:5182';
const post = (p, b = {}) => fetch(ORIGIN + p, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const DEV = { device_id: 'd1', name: 'Office PC', online: true, last_seen: 1, extension: false };

const browser = await puppeteer.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });

async function session(scheme, width, height, fn) {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: 1 });
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: scheme }]);
  await page.evaluateOnNewDocument(() => { localStorage.setItem('pilot.token', 'tok'); });
  page.on('pageerror', (e) => console.log('PAGE ERROR', e.message));
  await fn(page);
  await page.close();
}
const dialogShot = async (page, name) => {
  const el = await page.$('dialog[open]');
  await el.screenshot({ path: path.join(out, name + '.png') });
};

for (const [scheme, width, height] of [['light', 1000, 900], ['dark', 1000, 900], ['light', 390, 800], ['dark', 390, 800]]) {
  const tag = `${scheme}-${width}`;
  // --- connect dialog: waiting, connected, chrome step in its three states
  await post('/__state', { devices: [], extension_url: '' });
  await session(scheme, width, height, async (page) => {
    await page.goto(`${ORIGIN}/app/`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('dialog[open] [data-testid=connect-command]');
    await sleep(500);
    await dialogShot(page, `connect-1-waiting-${tag}`);
    await post('/__state', { devices: [DEV] }); await post('/__push');
    await sleep(500);
    await dialogShot(page, `connect-2-arrived-${tag}`);
    await sleep(1600);
    await dialogShot(page, `connect-3-chrome-soon-${tag}`);
  });
  await post('/__state', { devices: [], extension_url: 'https://chromewebstore.google.com/detail/deskhand/abc' });
  await session(scheme, width, height, async (page) => {
    await page.goto(`${ORIGIN}/app/`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('dialog[open] [data-testid=connect-command]');
    await post('/__state', { devices: [DEV] }); await post('/__push');
    await sleep(2200);
    await dialogShot(page, `connect-4-chrome-link-${tag}`);
    await post('/__state', { devices: [{ ...DEV, extension: true }] }); await post('/__push');
    await sleep(500);
    await dialogShot(page, `connect-5-chrome-connected-${tag}`);
  });
  // --- settings, full height
  await post('/__state', { devices: [DEV, { device_id: 'd2', name: 'Den laptop', online: false, last_seen: 1, extension: true }], extension_url: '' });
  await session(scheme, width, 6000, async (page) => {
    await page.goto(`${ORIGIN}/app/`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('button[aria-label="Settings"], button[aria-label="Workspace and settings"]');
    // open Settings through the store flag exposed by the Sidebar user row, else by the keyboard-free route below
    const opened = await page.evaluate(() => {
      const b = [...document.querySelectorAll('button')].find((x) => /settings/i.test(x.textContent + x.getAttribute('aria-label')));
      if (b) { b.click(); return true; }
      return false;
    });
    if (!opened) console.log('no settings button found in the sidebar yet');
    await page.waitForSelector('dialog[open] h2#settingsTitle', { timeout: 5000 }).catch(() => console.log('settings did not open'));
    await page.evaluate(() => { const d = document.querySelector('dialog[open]'); d.style.maxHeight = 'none'; });
    await sleep(700);
    await dialogShot(page, `settings-${tag}`);
  });
}
await browser.close();
console.log('shots in', out);
