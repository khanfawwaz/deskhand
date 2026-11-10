// Takes the walkthrough's screenshots from the REAL Deskhand web app (src/taskpilot/web) running against a small fake backend,
// so no account, no private data and no real computer is involved. Run:  node capture.mjs
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, '..', 'src', 'taskpilot', 'web');
const h = path.join(here, 'capture', 'h');
const out = path.join(here, 'public', 'shots');
fs.mkdirSync(out, { recursive: true });
fs.cpSync(web, h, { recursive: true });
fs.writeFileSync(path.join(h, 'sw.js'), '// off while capturing\n');

const MOCK = `<script>
if (!window.__ANON) { try { localStorage.setItem('pilot.token', 'demo'); } catch (e) {} }
const _enc = new TextEncoder();
let _ctl = null;
window.__emit = (ev, obj) => { if (_ctl) _ctl.enqueue(_enc.encode('event: ' + ev + '\\ndata: ' + JSON.stringify(obj) + '\\n\\n')); };
window.fetch = async (url) => {
  const u = String(url);
  const json = (o) => new Response(JSON.stringify(o), { status: 200, headers: { 'Content-Type': 'application/json' } });
  if (u.includes('/v1/auth/config')) return json({ email_login: true, signup: 'open' });
  if (u.includes('/v1/me')) return json({ devices: [], name: 'Alex', email: 'alex@example.com', workspace: { name: 'Alex' }, client_id: 'c1' });
  if (u.includes('/v1/add-device')) return json({ command: 'powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://deskhand.example/agent/install.ps1?code=K7QM-4TXD | iex"' });
  if (u.includes('/v1/events')) return new Response(new ReadableStream({ start(c) { _ctl = c; } }), { status: 200 });
  if (u.includes('/v1/workspace')) return json({ name: 'Alex', tasks_24h: 3, max_tasks_per_day: 30 });
  if (u.includes('/v1/members')) return json([{ client_id: 'c1', name: 'Chrome on Windows', you: true }, { client_id: 'c2', name: 'Alex\\'s phone', you: false }]);
  if (u.includes('/v1/usage')) return json({ tasks_24h: 3, max_tasks_per_day: 30, model_calls_today: 41, tokens_today: 128400, queue: {} });
  if (u.includes('/v1/skills')) return json([]);
  return json([]);
};
</script>`;

let html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
html = html.replace('<body>', '<body>' + MOCK);
fs.writeFileSync(path.join(h, 'index.html'), html);
const url = pathToFileURL(path.join(h, 'index.html')).href;

const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find((p) => fs.existsSync(p));
const browser = await puppeteer.launch({ executablePath: chrome, headless: true, userDataDir: fs.mkdtempSync(path.join(process.env.TEMP || '.', 'dh-cap-')), args: ['--no-first-run'] });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => Math.floor(Date.now() / 1000);

async function newPage({ anon = false, theme = 'light' } = {}) {
  const p = await browser.newPage();
  await p.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1.5 });
  await p.evaluateOnNewDocument((anon, theme) => {
    if (anon) window.__ANON = true;
    try { localStorage.setItem('pilot.settings', JSON.stringify({ auto: true, speak: false, notify: false, theme })); } catch (e) {}
  }, anon, theme);
  await p.goto(url);
  await sleep(1200);
  return p;
}
const shot = async (p, name) => { await p.screenshot({ path: path.join(out, name + '.png') }); console.log('saved', name); };
const emit = (p, ev, obj) => p.evaluate((ev, obj) => window.__emit(ev, obj), ev, obj);

// 1. sign-in
let p = await newPage({ anon: true });
await shot(p, '01-signin');
await p.close();

// 2..: signed in, no computer yet -> the connect pop-up opens by itself
p = await newPage();
await shot(p, '02-connect');
await emit(p, 'devices', [{ device_id: 'd1', name: 'Alex-Laptop', online: true }]);
await sleep(2600);
await shot(p, '03-welcome');

await p.evaluate(() => { document.getElementById('toast').hidden = true; });       // the "connected" note belongs to the previous scene
await p.click('#targetBtn'); await sleep(400);
await shot(p, '04-picker');
await p.click('#targetBtn'); await sleep(200);

const t0 = { id: 't1', goal: 'Open YouTube and play the latest video from NetworkChuck', created_at: now() - 38, updated_at: now(), terminal: false, status: 'running' };
await emit(p, 'task', { ...t0, steps: [
  { seq: 1, tool: 'web.open', status: 'completed', note: '' },
  { seq: 2, tool: 'web.search', status: 'completed', note: '' },
  { seq: 3, tool: 'web.links', status: 'completed', note: '' },
  { seq: 4, tool: 'web.click', status: 'dispatched', note: '' }] });
await sleep(800);
await p.evaluate(() => document.querySelector('#history button')?.click()); await sleep(500);
await p.evaluate(() => { const d = document.getElementById('stepsBox'); if (d) d.open = true; document.getElementById('toast').hidden = true; });
await shot(p, '05-running');

await emit(p, 'task', { ...t0, status: 'waiting_for_confirmation', steps: [
  { seq: 1, tool: 'web.open', status: 'completed', note: '' }, { seq: 2, tool: 'web.search', status: 'completed', note: '' }],
  pending_approval: { kind: 'question', question: 'Which profile should I use on Netflix?', options: [{ id: 'a', label: 'Alex' }, { id: 'b', label: 'Kids' }, { id: 'c', label: 'Guest' }], allow_text: true } });
await sleep(700);
await shot(p, '06-question');

await emit(p, 'task', { ...t0, status: 'completed', terminal: true, updated_at: now(), steps: [
  { seq: 1, tool: 'web.open', status: 'completed', note: '' }, { seq: 2, tool: 'web.search', status: 'completed', note: '' },
  { seq: 3, tool: 'web.links', status: 'completed', note: '' }, { seq: 4, tool: 'web.click', status: 'completed', note: '' }, { seq: 5, tool: 'web.url', status: 'completed', note: '' }],
  result: 'Playing now: "I Built a Home Lab in a Weekend" by NetworkChuck. The address bar shows youtube.com/watch.' });
await sleep(700);
await shot(p, '07-done');

await p.click('#menuBtn'); await sleep(900);
await shot(p, '08-settings');
await p.evaluate(() => { const m = document.querySelector('#menu .menu'); document.getElementById('menu').scrollTo(0, 99999); });
await sleep(400);
await shot(p, '09-settings-bottom');
await p.keyboard.press('Escape'); await sleep(300);
await p.evaluate(() => { document.getElementById('newChatBtn').click(); }); await sleep(600);
await p.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark')); await sleep(1400);
await shot(p, '10-dark-welcome');

await browser.close();
console.log('done');
