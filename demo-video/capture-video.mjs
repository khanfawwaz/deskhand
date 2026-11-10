// Records short screen clips of the REAL Deskhand web app (src/taskpilot/web) while a script drives it like a person would:
// a visible cursor glides and clicks, text is typed, the bubble is pulled, a question is answered, Settings is opened.
// The backend is a small fake (no account, no private data). Output: public/clips/*.mp4 and clips.json (their lengths).
//   node capture-video.mjs
import puppeteer from 'puppeteer-core';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, '..', 'src', 'taskpilot', 'web');
const h = path.join(here, 'capture', 'h');
const outDir = path.join(here, 'public', 'clips');
const work = path.join(here, 'capture', 'frames');
const ffmpeg = path.join(here, 'node_modules', '@remotion', 'compositor-win32-x64-msvc', 'ffmpeg.exe');
fs.mkdirSync(outDir, { recursive: true });
fs.cpSync(web, h, { recursive: true });
fs.writeFileSync(path.join(h, 'sw.js'), '// off while capturing\n');

const MOCK = `<script>
if (!window.__ANON) { try { localStorage.setItem('pilot.token', 'demo'); } catch (e) {} }
const _enc = new TextEncoder();
let _ctl = null;
window.__emit = (ev, obj) => { if (_ctl) _ctl.enqueue(_enc.encode('event: ' + ev + '\\ndata: ' + JSON.stringify(obj) + '\\n\\n')); };
window.__task = null; window.__answered = null;
window.fetch = async (url, opts = {}) => {
  const u = String(url), m = (opts.method || 'GET').toUpperCase();
  const body = opts.body ? JSON.parse(opts.body) : {};
  const json = (o) => new Response(JSON.stringify(o), { status: 200, headers: { 'Content-Type': 'application/json' } });
  if (u.includes('/v1/auth/config')) return json({ email_login: true, signup: 'open' });
  if (u.includes('/v1/auth/email')) return json({ sent: true });
  if (u.includes('/v1/me')) return json({ devices: [], name: 'Alex', email: 'alex@example.com', workspace: { name: 'Alex' }, client_id: 'c1' });
  if (u.includes('/v1/add-device')) return json({ command: 'powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://deskhand.example/agent/install.ps1?code=K7QM-4TXD | iex"' });
  if (u.includes('/v1/events')) return new Response(new ReadableStream({ start(c) { _ctl = c; } }), { status: 200 });
  if (u.includes('/v1/workspace')) return json({ name: 'Alex', tasks_24h: 3, max_tasks_per_day: 30 });
  if (u.includes('/v1/members')) return json([{ client_id: 'c1', name: 'Chrome on Windows', you: true }, { client_id: 'c2', name: 'Alex\\'s phone', you: false }]);
  if (u.includes('/v1/usage')) return json({ tasks_24h: 3, max_tasks_per_day: 30, model_calls_today: 41, tokens_today: 128400, queue: {} });
  if (u.includes('/answer')) { window.__answered = body; return json({}); }
  if (m === 'POST' && u.endsWith('/v1/tasks')) {
    const now = Math.floor(Date.now() / 1000);
    window.__task = { id: 't1', goal: body.goal, created_at: now, updated_at: now, terminal: false, status: 'running', steps: [] };
    return json({ task_id: 't1' });
  }
  if (u.includes('/v1/tasks/t1')) return json(window.__task);
  return json([]);
};
// a visible cursor (the browser's own is not in a screen recording), drawn in the top layer so it stays above dialogs
const mk = () => {
  const c = document.createElement('div');
  c.setAttribute('popover', 'manual');
  c.style.cssText = 'position:fixed;inset:auto;left:0;top:0;margin:0;padding:0;border:0;background:none;overflow:visible;pointer-events:none;width:0;height:0;transform:translate(-100px,-100px)';
  c.innerHTML = '<svg width="30" height="30" viewBox="0 0 24 24" style="position:absolute;left:0;top:0;filter:drop-shadow(0 2px 3px rgba(0,0,0,.45))"><path d="M4 2l15 9-6.5 1.6L9 19z" fill="#fff" stroke="#12241d" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  document.documentElement.appendChild(c);
  try { c.showPopover(); } catch (e) {}
  const ring = document.createElement('div');
  ring.setAttribute('popover', 'manual');
  ring.style.cssText = 'position:fixed;inset:auto;left:0;top:0;margin:0;padding:0;border:0;background:none;overflow:visible;pointer-events:none;width:0;height:0';
  document.documentElement.appendChild(ring);
  try { ring.showPopover(); } catch (e) {}
  window.__mouse = { x: 700, y: 60 };
  const place = (x, y) => { window.__mouse = { x, y }; c.style.transform = 'translate(' + x + 'px,' + y + 'px)'; };
  const lift = () => { try { c.hidePopover(); c.showPopover(); ring.hidePopover(); ring.showPopover(); } catch (e) {} };
  document.addEventListener('mousemove', (e) => { place(e.clientX, e.clientY); }, true);
  document.addEventListener('mousedown', (e) => {
    lift();
    const d = document.createElement('div');
    d.style.cssText = 'position:fixed;left:' + (e.clientX - 22) + 'px;top:' + (e.clientY - 22) + 'px;width:44px;height:44px;border-radius:50%;border:4px solid rgba(27,194,55,.85);transform:scale(.3);opacity:1;transition:transform .45s ease-out,opacity .45s ease-out;pointer-events:none';
    ring.appendChild(d);
    requestAnimationFrame(() => { d.style.transform = 'scale(1.3)'; d.style.opacity = '0'; });
    setTimeout(() => d.remove(), 600);
  }, true);
  new MutationObserver(lift).observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ['open'] });
  place(700, 60);
};
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mk); else mk();
</script>`;

let html = fs.readFileSync(path.join(web, 'index.html'), 'utf8');
html = html.replace('<body>', '<body>' + MOCK);
fs.writeFileSync(path.join(h, 'index.html'), html);
const url = pathToFileURL(path.join(h, 'index.html')).href;

const chrome = ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find((p) => fs.existsSync(p));
const browser = await puppeteer.launch({ executablePath: chrome, headless: true, userDataDir: fs.mkdtempSync(path.join(process.env.TEMP || '.', 'dh-vid-')), args: ['--no-first-run'] });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const now = () => Math.floor(Date.now() / 1000);
const ease = (k) => (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);
const clips = {};

async function newPage({ anon = false } = {}) {
  const p = await browser.newPage();
  await p.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  await p.evaluateOnNewDocument((anon) => {
    if (anon) window.__ANON = true;
    try { localStorage.setItem('pilot.settings', JSON.stringify({ auto: true, speak: false, notify: false, theme: 'light' })); } catch (e) {}
  }, anon);
  await p.goto(url);
  await sleep(900);
  return p;
}

async function glide(p, x, y, ms = 700) {
  const from = await p.evaluate(() => window.__mouse || { x: 700, y: 60 });
  const n = Math.max(8, Math.round(ms / 16));
  for (let i = 1; i <= n; i++) {
    const k = ease(i / n);
    await p.mouse.move(from.x + (x - from.x) * k, from.y + (y - from.y) * k);
    await sleep(ms / n);
  }
}
const centerOf = async (p, sel) => { const el = await p.$(sel); const b = await el.boundingBox(); return { x: b.x + b.width / 2, y: b.y + b.height / 2 }; };
async function moveTo(p, sel, ms = 700) { const c = await centerOf(p, sel); await glide(p, c.x, c.y, ms); return c; }
async function clickOn(p, sel, ms = 700) { await moveTo(p, sel, ms); await sleep(120); await p.mouse.down(); await sleep(70); await p.mouse.up(); }
const emit = (p, ev, obj) => p.evaluate((ev, obj) => window.__emit(ev, obj), ev, obj);
const hideToast = (p) => p.evaluate(() => { document.getElementById('toast').hidden = true; });

// record whatever `fn` does into public/clips/<name>.mp4 (frames come from Chrome's screencast, timed exactly)
async function record(p, name, fn, size = { w: 1440, h: 900 }) {
  const dir = path.join(work, name);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  const cdp = await p.createCDPSession();
  const frames = [];
  cdp.on('Page.screencastFrame', async ({ data, metadata, sessionId }) => {
    frames.push({ t: metadata.timestamp, data });
    try { await cdp.send('Page.screencastFrameAck', { sessionId }); } catch (e) { /* stopped */ }
  });
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, maxWidth: size.w, maxHeight: size.h, everyNthFrame: 1 });
  await sleep(150);
  await fn();
  await sleep(150);
  await cdp.send('Page.stopScreencast');
  await cdp.detach();
  if (frames.length < 2) throw new Error('no frames for ' + name);
  let list = '';
  frames.forEach((f, i) => {
    const file = path.join(dir, String(i).padStart(5, '0') + '.jpg');
    fs.writeFileSync(file, Buffer.from(f.data, 'base64'));
    const dur = i < frames.length - 1 ? Math.max(0.001, frames[i + 1].t - f.t) : 0.12;
    list += `file '${file.replace(/\\/g, '/')}'\nduration ${dur.toFixed(4)}\n`;
  });
  list += `file '${path.join(dir, String(frames.length - 1).padStart(5, '0') + '.jpg').replace(/\\/g, '/')}'\n`;
  fs.writeFileSync(path.join(dir, 'list.txt'), list);
  const out = path.join(outDir, name + '.mp4');
  execFileSync(ffmpeg, ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', path.join(dir, 'list.txt'), '-r', '30', '-pix_fmt', 'yuv420p', '-c:v', 'libx264', '-crf', '19', '-preset', 'veryfast', '-movflags', '+faststart', out]);
  const seconds = frames[frames.length - 1].t - frames[0].t + 0.12;
  clips[name] = Number(seconds.toFixed(2));
  console.log('clip', name.padEnd(10), seconds.toFixed(1) + 's', frames.length + ' frames');
}

// ------------------------------------------------------------------ A: sign in
let p = await newPage({ anon: true });
await record(p, 'a-signin', async () => {
  await sleep(700);
  await clickOn(p, '#email', 800);
  await p.keyboard.type('alex@example.com', { delay: 55 });
  await sleep(350);
  await clickOn(p, '#emailForm button[type="submit"]', 700);
  await sleep(1700);
});
await p.close();

// ------------------------------------------------------------------ B: connect a computer; C: home, the bubble, typing; D: a task
p = await newPage();
await record(p, 'b-connect', async () => {
  await sleep(1300);
  await clickOn(p, '#connectCopy', 900);
  await sleep(150);
  await p.evaluate(() => { const b = document.getElementById('connectCopy'); if (b.textContent === 'Copy command') b.textContent = 'Copied. Now paste it into PowerShell.'; });
  await sleep(1500);
  await emit(p, 'devices', [{ device_id: 'd1', name: 'Alex-Laptop', online: true }]);
  await sleep(2600);
});

await record(p, 'c-home', async () => {
  await hideToast(p);
  const o = await p.evaluate(() => { const r = document.getElementById('orb').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  await glide(p, o.x + 260, o.y + 200, 500);
  await glide(p, o.x + 120, o.y - 60, 800);                      // hover: the nearest edge stretches toward the cursor
  await glide(p, o.x - 90, o.y + 70, 800);
  for (let k = 0; k < 2; k++) {                                   // grab and stretch far: the theme flips (twice: dark, then light again)
    await glide(p, o.x + 100, o.y - 20, 500);
    await p.mouse.down();
    await glide(p, o.x + 340, o.y - 120, 1100);
    await sleep(500);
    await p.mouse.up();
    await sleep(900);
  }
  await clickOn(p, '#goal', 800);
  await p.keyboard.type('Write my meeting notes in Notepad and save them', { delay: 38 });
  await sleep(500);
});

await record(p, 'd-task', async () => {
  await p.keyboard.press('Enter');
  await sleep(900);
  await p.evaluate(() => { const d = document.getElementById('stepsBox'); if (d) d.open = true; });
  const t0 = () => ({ id: 't1', goal: 'Write my meeting notes in Notepad and save them', created_at: now() - 4, updated_at: now(), terminal: false, status: 'running' });
  const steps = [];
  for (const tool of ['launch_application', 'windows.focus', 'ui.type', 'hotkey']) {
    steps.push({ seq: steps.length + 1, tool, status: 'dispatched', note: '' });
    await emit(p, 'task', { ...t0(), steps: steps.map((s) => ({ ...s })) });
    await sleep(750);
    steps[steps.length - 1].status = 'completed';
    await emit(p, 'task', { ...t0(), steps: steps.map((s) => ({ ...s })) });
    await sleep(250);
  }
  await emit(p, 'task', { ...t0(), status: 'waiting_for_confirmation', steps: steps.map((s) => ({ ...s })),
    pending_approval: { kind: 'question', question: 'What should I call the file?', options: [{ id: 'a', label: 'meeting-notes.txt' }, { id: 'b', label: 'notes-today.txt' }], allow_text: true } });
  await sleep(1500);
  await clickOn(p, '#choices button:first-child', 900);
  await sleep(600);
  steps.push({ seq: steps.length + 1, tool: 'files.write', status: 'completed', note: '' });
  await emit(p, 'task', { ...t0(), status: 'completed', terminal: true, steps: steps.map((s) => ({ ...s })), result: 'Done. Saved meeting-notes.txt in your Deskhand folder.' });
  await sleep(2400);
});
await p.close();

// ------------------------------------------------------------------ E: choose the computer
p = await newPage();
await emit(p, 'devices', [{ device_id: 'd1', name: 'Alex-Laptop', online: true }, { device_id: 'd2', name: 'Office-PC', online: true }]);
await sleep(1200);
await p.evaluate(() => { document.getElementById('connectDlg')?.open && document.getElementById('connectDlg').close(); });
await hideToast(p);
await record(p, 'e-picker', async () => {
  await sleep(500);
  await clickOn(p, '#targetBtn', 900);
  await sleep(900);
  const chips = await p.$$('#targets .tchip');
  const b = await chips[1].boundingBox();
  await glide(p, b.x + b.width / 2, b.y + b.height / 2, 700);
  await sleep(100); await p.mouse.down(); await sleep(70); await p.mouse.up();
  await sleep(1800);
});
await p.close();

// ------------------------------------------------------------------ F: settings
p = await newPage();
await emit(p, 'devices', [{ device_id: 'd1', name: 'Alex-Laptop', online: true }]);
await sleep(1200);
await p.evaluate(() => { document.getElementById('connectDlg')?.open && document.getElementById('connectDlg').close(); });
await hideToast(p);
await record(p, 'f-settings', async () => {
  await sleep(500);
  await clickOn(p, '#menuBtn', 900);
  await sleep(1300);
  await clickOn(p, '#themeDark', 900);                           // the whole page turns dark
  await sleep(1300);
  await clickOn(p, '#themeLight', 700);
  await sleep(900);
  const m = await p.evaluate(() => { const r = document.querySelector('#menu').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
  await glide(p, m.x, m.y, 500);
  for (let i = 0; i < 7; i++) { await p.mouse.wheel({ deltaY: 130 }); await sleep(160); }
  await sleep(1500);
});
await p.close();

// ------------------------------------------------------------------ G: a web task, the kind the Chrome extension carries out
p = await newPage();
await emit(p, 'devices', [{ device_id: 'd1', name: 'Alex-Laptop', online: true }]);
await sleep(1200);
await p.evaluate(() => { document.getElementById('connectDlg')?.open && document.getElementById('connectDlg').close(); });
await hideToast(p);
await record(p, 'g-webtask', async () => {
  await sleep(400);
  await clickOn(p, '#goal', 800);
  await p.keyboard.type('Open YouTube and play the latest video from NetworkChuck', { delay: 32 });
  await sleep(400);
  await p.keyboard.press('Enter');
  await sleep(800);
  await p.evaluate(() => { const d = document.getElementById('stepsBox'); if (d) d.open = true; });
  const goal = 'Open YouTube and play the latest video from NetworkChuck';
  const t0 = () => ({ id: 't1', goal, created_at: now() - 4, updated_at: now(), terminal: false, status: 'running' });
  const steps = [];
  for (const tool of ['web.open', 'web.search', 'web.links', 'web.click', 'web.url']) {
    steps.push({ seq: steps.length + 1, tool, status: 'dispatched', note: '' });
    await emit(p, 'task', { ...t0(), steps: steps.map((s) => ({ ...s })) });
    await sleep(700);
    steps[steps.length - 1].status = 'completed';
    await emit(p, 'task', { ...t0(), steps: steps.map((s) => ({ ...s })) });
    await sleep(200);
  }
  await emit(p, 'task', { ...t0(), status: 'completed', terminal: true, steps: steps.map((s) => ({ ...s })), result: 'Playing now: the latest video from NetworkChuck. The address bar shows youtube.com/watch.' });
  await sleep(2200);
});
await p.close();

// ------------------------------------------------------------------ H: the REAL extension popup (extension/popup.html + popup.js), with a stand-in for Chrome's extension API
const popDir = path.join(here, 'capture', 'popup');
fs.rmSync(popDir, { recursive: true, force: true });
fs.cpSync(path.resolve(here, '..', 'extension'), popDir, { recursive: true });
const CURSOR = '<script>' + MOCK.slice(MOCK.indexOf('// a visible cursor'));
const popHtml = (st, paired) => fs.readFileSync(path.join(popDir, 'popup.html'), 'utf8').replace('<script src="popup.js"></script>',
  `<script>window.__st=${JSON.stringify(st)};window.__paired=${paired};
window.chrome={runtime:{sendMessage:async(m)=>{if(m.type==='status')return{state:{status:window.__st}};
 if(m.type==='pair'){window.__st='pairing';window.__paired=false;return{ok:true}}
 if(m.type==='forget'){window.__st='searching';window.__paired=false;return{ok:true}}}},
 storage:{local:{get:async()=>({token:window.__paired?'t':undefined})}}};</script>${CURSOR}<script src="popup.js"></script>`);
async function popupPage(st, paired) {
  fs.writeFileSync(path.join(popDir, 'popup-view.html'), popHtml(st, paired));
  const pg = await browser.newPage();
  await pg.setViewport({ width: 720, height: 660, deviceScaleFactor: 1 });
  await pg.goto(pathToFileURL(path.join(popDir, 'popup-view.html')).href);
  await pg.evaluate(() => { document.body.style.zoom = '2'; });      // draw the popup at double size so it stays sharp on screen
  await sleep(900);
  return pg;
}
const POP = { w: 720, h: 660 };
p = await popupPage('searching', false);                          // before connecting: "Not connected", then press Connect
await record(p, 'h-popup1', async () => {
  await sleep(1300);
  await clickOn(p, '#pair', 800);
  await sleep(2600);
}, POP);
await p.close();
p = await popupPage('pairing', false);                            // approved on the computer: the popup turns to "Connected"
await record(p, 'h-popup2', async () => {
  await sleep(900);
  await p.evaluate(() => { window.__st = 'connected'; window.__paired = true; });
  await sleep(2300);
  await moveTo(p, '#forget', 900);
  await sleep(1300);
}, POP);
await p.close();

await browser.close();
fs.writeFileSync(path.join(here, 'clips.json'), JSON.stringify(clips, null, 2));
console.log('done', clips);
