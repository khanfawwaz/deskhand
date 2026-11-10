/* global console, process, URL */
// A tiny fake Deskhand backend + static host for looking at the Connect and Settings dialogs for real.
//   node serve.mjs <dist folder>        (port 5182)
// Control from a script: POST /__state {devices?, extension_url?} changes the data; POST /__push sends the devices
// to every open event stream (what the real server does when a computer connects).
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const dist = path.resolve(process.argv[2] ?? 'dist');
const PORT = 5182;
const BASE = '/app/';
const state = {
  devices: [],
  extension_url: '',
  prefs: [{ id: 'p1', text: 'when I say calming music, play lofi', created_at: 1 }],
  skills: [
    { id: 's1', title: 'YouTube', runs: { ok: 7, failed: 1 } },
    { id: 's2', title: 'Netflix', runs: { ok: 2 } },
  ],
};
const streams = new Set();
let cmdN = 0;
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.webmanifest': 'application/manifest+json' };

const json = (res, data, status = 200) => {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(data));
};
const body = (req) => new Promise((ok) => { let s = ''; req.on('data', (c) => (s += c)); req.on('end', () => ok(s ? JSON.parse(s) : {})); });
const pushDevices = () => streams.forEach((r) => r.write(`event: devices\ndata: ${JSON.stringify(state.devices)}\n\n`));

http
  .createServer(async (req, res) => {
    const url = new URL(req.url, 'http://x');
    const p = url.pathname;
    if (p.startsWith('/__state')) { Object.assign(state, await body(req)); return json(res, state); }
    if (p === '/__push') { pushDevices(); return json(res, { ok: 1 }); }
    if (p === '/v1/events') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
      res.write(': hello\n\n');
      streams.add(res);
      req.on('close', () => streams.delete(res));
      return;
    }
    if (p.startsWith('/v1/')) {
      const m = req.method;
      if (p === '/v1/me') return json(res, { client_id: 'c1', name: 'Alex', workspace: { id: 'w', name: 'Alex home' }, devices: state.devices, extension_url: state.extension_url });
      if (p === '/v1/add-device') return json(res, { code: 'X' + ++cmdN, expires_in: 900, command: `powershell -NoProfile -Command "iwr https://deskhand.example/install/${cmdN}.ps1 | iex"` });
      if (p === '/v1/workspace') return json(res, { id: 'w', name: 'Alex home', max_tasks_per_day: 50, tasks_24h: 3 });
      if (p === '/v1/members') return json(res, [{ client_id: 'c1', name: 'This phone', last_seen: 1, you: true }, { client_id: 'c2', name: 'Laptop browser', last_seen: 1, you: false }]);
      if (p === '/v1/usage') return json(res, { tasks_24h: 3, max_tasks_per_day: 50, model_calls_today: 1234, tokens_today: 56789, token_limit: 200000, queue: { waiting: 2, running: 1 } });
      if (p === '/v1/preferences') return json(res, state.prefs);
      if (p === '/v1/skills') return json(res, state.skills);
      if (p.startsWith('/v1/skills/') && m === 'GET') return json(res, { id: 's1', title: 'YouTube', runs: { ok: 7 }, text: 'Search box: press / first.\nPause with k.' });
      if (p === '/v1/pair-code') return json(res, { code: 'K7QD-2MPX', expires_in: 600 });
      if (p === '/v1/export') return json(res, { tasks: [] });
      if (p === '/v1/tasks') return json(res, []);
      return json(res, {});
    }
    // static app with SPA fallback
    let rel = p.startsWith(BASE) ? p.slice(BASE.length) : '';
    let file = path.join(dist, rel);
    if (!rel || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(dist, 'index.html');
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] ?? 'application/octet-stream' });
    fs.createReadStream(file).pipe(res);
  })
  .listen(PORT, () => console.log(`fake backend on http://localhost:${PORT}${BASE}`));
