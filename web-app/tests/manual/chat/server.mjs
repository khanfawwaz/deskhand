// A tiny fake Deskhand backend for looking at the chat in a real browser (not part of the test suite).
//   node tests/manual/chat/server.mjs <dist folder>      -> http://127.0.0.1:5181/app/
// Switch what is on screen with  GET /__mode/idle | running | question | access | confirm
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const DIST = path.resolve(process.argv[2] ?? 'dist');
const PORT = 5181;
const BASE = '/app';
const now = () => Math.floor(Date.now() / 1000);

const devices = [
  {
    device_id: 'd1',
    name: 'Alex-Laptop',
    online: true,
    last_seen: now(),
    extension: true,
  },
  {
    device_id: 'd2',
    name: 'Office-PC',
    online: true,
    last_seen: now(),
    extension: false,
  },
  {
    device_id: 'd3',
    name: 'Old-Tablet',
    online: false,
    last_seen: now() - 9000,
    extension: false,
  },
];

const t = (id, goal, over = {}) => ({
  id,
  goal,
  device_id: 'd1',
  status: 'completed',
  created_at: now() - 3000,
  updated_at: now() - 2960,
  result: null,
  error: null,
  terminal: true,
  pending_approval: null,
  thread: id,
  continue_from: null,
  ...over,
});
const steps = (...s) =>
  s.map((x, i) => ({
    seq: i,
    tool: x[0],
    status: x[1] ?? 'completed',
    note: x[2] ?? null,
  }));

const finished = [
  t('f1', 'Open Notepad and write a shopping list', {
    created_at: now() - 3600,
    updated_at: now() - 3570,
    result: 'Opened Notepad and typed:\n- milk\n- eggs\n- bread\n- coffee',
    steps: steps(['launch_application'], ['type_text'], ['capture_screen']),
  }),
  t('f2', 'Now add bananas to the list', {
    created_at: now() - 3400,
    updated_at: now() - 3380,
    thread: 'f1',
    continue_from: 'f1',
    result: 'Added "bananas" to the end of the list.',
    steps: steps(['press_key'], ['type_text']),
  }),
  t('x1', "What's the latest AI news today?", {
    created_at: now() - 1800,
    updated_at: now() - 1790,
    status: 'failed',
    error: 'cannot_proceed: Old-Tablet could not open a browser.',
    steps: steps(['browser.open_url', 'failed', 'No browser window appeared.']),
  }),
];

const live = {
  running: t('r1', 'Open YouTube and play something relaxing', {
    status: 'running',
    terminal: false,
    created_at: now() - 42,
    updated_at: now() - 3,
    steps: steps(['browser.open_url'], ['ui.snapshot'], ['ui.click', 'dispatched']),
  }),
  question: t('r1', 'Book a table for two on Friday', {
    status: 'waiting_for_confirmation',
    terminal: false,
    created_at: now() - 95,
    updated_at: now() - 5,
    steps: steps(['web.open'], ['web.search']),
    pending_approval: {
      kind: 'question',
      question: 'Which restaurant do you prefer?',
      allow_text: true,
      options: [
        { id: 'a', label: 'Osteria Roma (Italian, 4.7)' },
        { id: 'b', label: 'Sakura House (Japanese, 4.6)' },
        { id: 'c', label: 'The Green Fork (Vegetarian, 4.5)' },
      ],
    },
  }),
  access: t('r1', 'Play my Discover Weekly on Spotify', {
    status: 'waiting_for_confirmation',
    terminal: false,
    created_at: now() - 30,
    updated_at: now() - 2,
    steps: steps(['launch_application']),
    pending_approval: {
      kind: 'access',
      question: 'Deskhand wants to open Spotify on Alex-Laptop.',
      options: [
        { id: 'once', label: 'Allow this time' },
        { id: 'always', label: 'Always allow on this computer' },
        { id: 'no', label: 'Not now' },
      ],
    },
  }),
  confirm: t('r1', 'Send the draft to Sam', {
    status: 'waiting_for_confirmation',
    terminal: false,
    created_at: now() - 30,
    updated_at: now() - 2,
    steps: steps(['ui.click', 'pending_confirmation']),
    pending_approval: {
      tool: 'ui.click',
      params: { name: 'Send' },
      reason: 'This may be irreversible',
      kind: 'confirm',
    },
  }),
};

let mode = 'idle';
let extra = []; // tasks started from the page
const all = () => [...extra, ...(live[mode] ? [live[mode]] : []), ...finished];
const taskById = (id) => all().find((x) => x.id === id);
const sse = new Set();
const push = (event, data) => sse.forEach((r) => r.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));

const json = (res, code, body) => {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
};
const readBody = (req) =>
  new Promise((ok) => {
    let s = '';
    req.on('data', (c) => (s += c));
    req.on('end', () => ok(s ? JSON.parse(s) : {}));
  });
const TYPES = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.json': 'application/json',
};

http
  .createServer(async (req, res) => {
    const url = new URL(req.url, 'http://x');
    const p = url.pathname;
    if (p.startsWith('/__mode/')) {
      mode = p.slice(8);
      extra = [];
      return json(res, 200, { mode });
    }
    if (p === '/v1/events') {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
      });
      res.write('event: devices\ndata: ' + JSON.stringify(devices) + '\n\n');
      sse.add(res);
      req.on('close', () => sse.delete(res));
      return;
    }
    if (p === '/v1/me')
      return json(res, 200, {
        client_id: 'c1',
        name: 'Alex Rivera',
        workspace: { id: 'w', name: 'Alex Home' },
        devices,
        extension_url: '',
      });
    if (p === '/v1/auth/config') return json(res, 200, { email_login: true });
    if (p === '/v1/add-device')
      return json(res, 200, {
        code: 'ABCD-1234',
        expires_in: 900,
        command: 'irm https://example.test/install | iex',
      });
    if (p === '/v1/tasks' && req.method === 'GET')
      return json(
        res,
        200,
        all().map((x) => ({ ...x, steps: undefined })),
      );
    if (p === '/v1/tasks' && req.method === 'POST') {
      const b = await readBody(req);
      const cf = b.continue_from ? taskById(b.continue_from) : null;
      const id = 'n' + (extra.length + 1);
      const task = t(id, b.goal, {
        device_id: b.device_id,
        status: 'running',
        terminal: false,
        created_at: now(),
        updated_at: now(),
        thread: cf ? cf.thread : id,
        continue_from: cf ? cf.id : null,
        steps: [],
      });
      extra.unshift(task);
      setTimeout(() => {
        task.steps = steps(['launch_application']);
        task.updated_at = now();
        push('task', task);
      }, 900);
      setTimeout(() => {
        Object.assign(task, {
          status: 'completed',
          terminal: true,
          updated_at: now(),
          result: 'Done: ' + b.goal,
          steps: steps(['launch_application'], ['type_text']),
        });
        push('task', task);
      }, 3500);
      return json(res, 200, { task_id: id, device_id: b.device_id });
    }
    let m = /^\/v1\/tasks\/([^/]+)(?:\/(answer|approve|cancel))?$/.exec(p);
    if (m) {
      const task = taskById(m[1]);
      if (!task) return json(res, 404, { detail: 'unknown task' });
      if (!m[2]) return json(res, 200, task);
      if (m[2] === 'cancel') {
        Object.assign(task, {
          status: 'cancelled',
          terminal: true,
          updated_at: now(),
        });
        push('task', task);
      } else {
        Object.assign(task, {
          status: 'running',
          pending_approval: null,
          updated_at: now(),
        });
        push('task', task);
      }
      return json(res, 200, { ok: true, answered: true });
    }
    m = /^\/v1\/chats\/([^/]+)$/.exec(p);
    if (m && req.method === 'DELETE') {
      const task = taskById(m[1]);
      if (!task) return json(res, 404, { detail: 'unknown chat' });
      const ids = all()
        .filter((x) => x.thread === task.thread)
        .map((x) => x.id);
      if (all().some((x) => ids.includes(x.id) && !x.terminal))
        return json(res, 409, {
          detail: 'This chat is still running. Press Stop first, then delete it.',
        });
      for (const list of [finished, extra]) for (let i = list.length - 1; i >= 0; i--) if (ids.includes(list[i].id)) list.splice(i, 1);
      return json(res, 200, { deleted: ids.length, ids });
    }
    if (p.startsWith('/v1/')) return json(res, 404, { detail: 'not in the fake' });

    // static files under /app/, with the single-page fallback
    if (!p.startsWith(BASE)) {
      res.writeHead(302, { Location: BASE + '/' });
      return res.end();
    }
    let rel = p.slice(BASE.length).replace(/^\//, '');
    let file = path.join(DIST, rel);
    if (!rel || !fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, 'index.html');
    res.writeHead(200, {
      'Content-Type': TYPES[path.extname(file)] ?? 'application/octet-stream',
      'Cache-Control': 'no-store',
    });
    fs.createReadStream(file).pipe(res);
  })
  .listen(PORT, '127.0.0.1', () => console.log(`fake backend on http://127.0.0.1:${PORT}${BASE}/ serving ${DIST}`));
