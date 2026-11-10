// Screenshots of the REAL extension popup (extension/popup.html + popup.js, with a stand-in for chrome.* APIs) for the store images.
//   node capture-popup-shots.mjs
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const ext = path.resolve(here, '..', 'extension');
const dir = path.join(here, 'capture', 'popup-shots');
const out = path.join(here, 'public', 'shots');
fs.rmSync(dir, { recursive: true, force: true });
fs.cpSync(ext, dir, { recursive: true });
const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find((p) => fs.existsSync(p));
const browser = await puppeteer.launch({ executablePath: chrome, headless: true, userDataDir: fs.mkdtempSync(path.join(process.env.TEMP || '.', 'dh-pop-')), args: ['--no-first-run'] });

async function shot(state, paired, name, height = 624) {
  const html = fs.readFileSync(path.join(dir, 'popup.html'), 'utf8').replace('<script src="popup.js"></script>',
    `<script>window.chrome={runtime:{sendMessage:async()=>({state:{status:${JSON.stringify(state)}}})},storage:{local:{get:async()=>({token:${paired ? "'t'" : 'undefined'}})}}};</script><script src="popup.js"></script>`);
  fs.writeFileSync(path.join(dir, 'view.html'), html);
  const p = await browser.newPage();
  await p.setViewport({ width: 720, height: Math.max(660, height + 40), deviceScaleFactor: 1 });
  await p.goto(pathToFileURL(path.join(dir, 'view.html')).href);
  await p.evaluate(() => { document.body.style.zoom = '2'; });
  await new Promise((r) => setTimeout(r, 700));
  await p.screenshot({ path: path.join(out, name + '.png'), clip: { x: 0, y: 0, width: 640, height } });
  await p.close();
  console.log('saved', name);
}
await shot('searching', false, 'popup-1-crop', 712);      // taller: this state also has the 'Get the Deskhand app' button
await shot('connected', true, 'popup-connected-crop');
await browser.close();
