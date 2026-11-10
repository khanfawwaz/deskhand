// A small fake Deskhand server for the end-to-end tests. It serves the BUILT app (web-app/dist) at the app base and
// speaks just enough of the real API (the shapes of src/taskpilot/client_api.py) for the four flows:
//   sign in by emailed code, connect a computer, a task with a question then a follow-up in the same chat, delete a chat.
// Everything lives in memory. The tests steer it with three extra routes under /__test (reset, connect a computer, state).
import fs from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const BASE = '/app/';
export const TEST_CODE = 'ABCD-EFGH'; // the code "emailed" to anyone
export const TOKEN = 'e2e-token';

const here = path.dirname(fileURLToPath(import.meta.url));
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.webmanifest': 'application/manifest+json', '.json': 'application/json', '.svg': 'image/svg+xml' };

export function createFakeBackend(dist = path.resolve(here, '..', '..', 'dist')) {
  /** @type {{devices: any[], tasks: any[], streams: Set<import('node:http').ServerResponse>, seq: number}} */
  let state;
  /** how many times the server was asked for each address (the service-worker test reads this) */
  const hits = new Map();
  const reset = () => {
    for (const s of state?.streams ?? []) s.end();
    state = { devices: [], tasks: [], streams: new Set(), seq: 0 };
  };
  reset();

  const now = () => Math.floor(Date.now() / 1000);
  const send = (event, data) => {
    for (const s of state.streams) s.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  };
  const view = (t) => ({ ...t, terminal: ['completed', 'failed', 'cancelled'].includes(t.status) });
  const push = (t) => {
    t.updated_at = now();
    send('task', view(t));
  };
  const me = () => ({ client_id: 'c1', name: 'Alex', workspace: { id: 'main', name: 'Alex Home' }, devices: state.devices, extension_url: '' });
  const json = (res, status, body) => {
    res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify(body));
  };
  const readBody = (req) =>
    new Promise((resolve) => {
      const chunks = [];
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => {
        try {
          resolve(JSON.parse(Buffer.concat(chunks).toString() || '{}'));
        } catch {
          resolve({});
        }
      });
    });

  /** A task that mentions "which" or "question" asks something first; anything else just finishes. */
  function runTask(t) {
    setTimeout(() => {
      if (/which|question/i.test(t.goal) && !t.answered) {
        t.status = 'waiting_for_action';
        t.pending_approval = { kind: 'question', question: 'Which one should I use?', options: [{ id: 'blue', label: 'The blue one' }, { id: 'red', label: 'The red one' }], allow_text: true, tool: null, reason: null, params: null };
      } else {
        t.status = 'completed';
        t.pending_approval = null;
        t.result = `Done: ${t.goal}` + (t.answered ? ` (you chose ${t.answered})` : '');
        t.steps = [...t.steps, { seq: t.steps.length + 1, tool: 'ui.click', status: 'ok', note: null }];
      }
      push(t);
    }, 350);
  }

  async function api(req, res, url) {
    const authed = req.headers.authorization === `Bearer ${TOKEN}`;
    const p = url.pathname;
    if (p === '/v1/auth/config' && req.method === 'GET') return json(res, 200, { email_login: true, extension_url: '' });
    if (p === '/v1/auth/email') return json(res, 200, { sent: true });
    if (p === '/v1/auth/verify') {
      const body = await readBody(req);
      if (String(body.code || '').replace(/[^a-z0-9]/gi, '').toUpperCase() !== TEST_CODE.replace('-', '')) return json(res, 401, { detail: 'That code is not right or has expired.' });
      return json(res, 200, { client_id: 'c1', token: TOKEN, name: 'Alex', workspace: 'main', new_workspace: false });
    }
    if (!authed) return json(res, 401, { detail: 'not signed in' });

    if (p === '/v1/me') return json(res, 200, me());
    if (p === '/v1/events') {
      res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', Connection: 'keep-alive' });
      res.write(`event: devices\ndata: ${JSON.stringify(state.devices)}\n\n`);
      state.streams.add(res);
      req.on('close', () => state.streams.delete(res));
      return undefined;
    }
    if (p === '/v1/add-device' && req.method === 'POST') return json(res, 200, { code: 'JOIN-1234', expires_in: 900, command: 'irm https://deskhand.example/install | iex  # e2e' });
    if (p === '/v1/tasks' && req.method === 'GET') return json(res, 200, [...state.tasks].reverse().map((t) => view({ ...t, steps: undefined })));
    if (p === '/v1/tasks' && req.method === 'POST') {
      const body = await readBody(req);
      const prev = state.tasks.find((t) => t.id === body.continue_from);
      const id = `t${++state.seq}`;
      const t = { id, goal: body.goal, device_id: body.device_id || state.devices[0]?.device_id, status: 'running', created_at: now() + state.seq / 100, updated_at: now(), result: null, error: null, pending_approval: null, continue_from: prev?.id ?? null, thread: prev ? prev.thread : id, steps: [{ seq: 1, tool: 'browser.open', status: 'ok', note: null }] };
      state.tasks.push(t);
      push(t);
      runTask(t);
      return json(res, 200, { task_id: id, device_id: t.device_id });
    }
    const one = /^\/v1\/tasks\/([^/]+)(?:\/(answer|approve|cancel))?$/.exec(p);
    if (one) {
      const t = state.tasks.find((x) => x.id === decodeURIComponent(one[1]));
      if (!t) return json(res, 404, { detail: 'no such task' });
      if (!one[2]) return json(res, 200, view(t));
      if (one[2] === 'answer') {
        const body = await readBody(req);
        t.answered = body.choice || body.text || 'something';
        t.status = 'running';
        t.pending_approval = null;
        push(t);
        runTask(t);
        return json(res, 200, { answered: true });
      }
      if (one[2] === 'cancel') {
        t.status = 'cancelled';
        t.pending_approval = null;
        push(t);
      }
      return json(res, 200, { ok: true });
    }
    const chat = /^\/v1\/chats\/([^/]+)$/.exec(p);
    if (chat && req.method === 'DELETE') {
      const key = decodeURIComponent(chat[1]);
      const hit = state.tasks.find((t) => t.id === key);
      const thread = hit?.thread ?? key;
      const ids = state.tasks.filter((t) => t.thread === thread).map((t) => t.id);
      state.tasks = state.tasks.filter((t) => t.thread !== thread);
      return json(res, 200, { deleted: ids.length, ids });
    }
    if (p === '/v1/preferences') return json(res, 200, []);
    if (p === '/v1/skills') return json(res, 200, []);
    if (p === '/v1/members') return json(res, 200, []);
    if (p === '/v1/usage') return json(res, 200, { tasks_24h: 0, max_tasks_per_day: 100 });
    return json(res, 404, { detail: 'not part of the fake server: ' + p });
  }

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url ?? '/', 'http://localhost');
    if (!url.pathname.startsWith('/__test')) hits.set(url.pathname, (hits.get(url.pathname) ?? 0) + 1);
    try {
      if (url.pathname === '/__test/hits') return json(res, 200, Object.fromEntries(hits));
      if (url.pathname === '/__test/reset') {
        reset();
        return json(res, 200, { ok: true });
      }
      if (url.pathname === '/__test/connect') {
        // a computer "arrives": it shows up in the list and the live feed says so
        const body = await readBody(req);
        state.devices = [...state.devices, { device_id: `d${state.devices.length + 1}`, name: body.name || 'Alex-Laptop', online: true, last_seen: now(), extension: !!body.extension }];
        send('devices', state.devices);
        return json(res, 200, state.devices);
      }
      if (url.pathname === '/__test/state') return json(res, 200, { devices: state.devices, tasks: state.tasks.length });
      if (url.pathname.startsWith('/v1/')) return await api(req, res, url);
      if (url.pathname === '/privacy' || url.pathname === '/terms') {
        res.writeHead(302, { Location: BASE.slice(0, -1) + url.pathname });
        return res.end();
      }
      if (url.pathname === BASE.slice(0, -1)) {
        res.writeHead(308, { Location: BASE });
        return res.end();
      }
      if (url.pathname.startsWith(BASE)) {
        const rel = decodeURIComponent(url.pathname.slice(BASE.length));
        const file = path.resolve(dist, rel);
        const inside = file === dist || file.startsWith(dist + path.sep);
        if (rel && inside && fs.existsSync(file) && fs.statSync(file).isFile()) {
          res.writeHead(200, { 'Content-Type': TYPES[path.extname(file)] || 'application/octet-stream', 'Cache-Control': rel.startsWith('assets/') ? 'public, max-age=31536000, immutable' : 'no-cache' });
          return fs.createReadStream(file).pipe(res);
        }
        if (path.extname(rel)) return json(res, 404, { detail: 'missing' });
        res.writeHead(200, { 'Content-Type': TYPES['.html'], 'Cache-Control': 'no-cache' });
        return fs.createReadStream(path.join(dist, 'index.html')).pipe(res);
      }
      return json(res, 404, { detail: 'not found' });
    } catch (e) {
      return json(res, 500, { detail: String(e) });
    }
  });
  return server;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.E2E_PORT || 4173);
  createFakeBackend().listen(port, '127.0.0.1', () => console.log(`fake Deskhand on http://127.0.0.1:${port}${BASE}`));
}
