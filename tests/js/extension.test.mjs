// The Chrome extension's Connect button: with no Deskhand app on the computer it says so and opens the website;
// with the app present it pairs without opening anything; the quiet background retries never open tabs.
// Runs the real extension/background.js against a stand-in for Chrome's APIs.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = fs.readFileSync(path.resolve(here, '..', '..', 'extension', 'background.js'), 'utf8');
const SITE = 'https://deskhand.fawwazkhan.dev/app/';
const plain = (o) => JSON.parse(JSON.stringify(o));   // objects made inside the sandbox have their own prototypes
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));

function boot({ app }) {
  const opened = [], states = [], store = { local: {} };
  let onMessage;
  class FakeSocket {
    constructor(url) {
      this.url = url; this.readyState = 0; FakeSocket.last = this;
      setTimeout(() => {
        if (app) { this.readyState = 1; this.onopen && this.onopen(); }
        else { this.readyState = 3; this.onerror && this.onerror(); this.onclose && this.onclose(); }
      }, 5);
    }
    send() {}
    close() { this.readyState = 3; }
  }
  FakeSocket.OPEN = 1; FakeSocket.CONNECTING = 0;
  const none = () => Promise.resolve();
  const chrome = {
    runtime: { getManifest: () => ({ version: '1.0.1' }), onMessage: { addListener: (f) => { onMessage = f; } }, onStartup: { addListener() {} }, onInstalled: { addListener() {} } },
    storage: {
      local: { get: async () => ({ token: store.local.token }), set: async (o) => Object.assign(store.local, o), remove: async () => { delete store.local.token; } },
      session: { set: async (o) => { states.push(o.state); }, get: async () => ({}) },
    },
    action: { setBadgeText: none, setBadgeBackgroundColor: none },
    tabs: { create: async (o) => { opened.push(o.url); }, query: async () => [], update: async () => ({}) },
    windows: { get: async () => { throw new Error('none'); }, create: async () => ({ id: 1, tabs: [{ id: 1 }] }) },
    debugger: { attach: none, detach: none, sendCommand: async () => ({}), onDetach: { addListener() {} } },
    alarms: { create() {}, onAlarm: { addListener() {} } },
  };
  const unrefTimeout = (f, ms) => { const t = setTimeout(f, ms); if (t.unref) t.unref(); return t; };
  const ctx = vm.createContext({ chrome, WebSocket: FakeSocket, setTimeout: unrefTimeout, clearTimeout, setInterval: () => 0, console, JSON, encodeURIComponent, Promise });
  vm.runInContext(SOURCE, ctx);
  const ask = (msg) => new Promise((resolve) => { onMessage(msg, {}, resolve); });
  return { ctx, opened, states, store, ask, FakeSocket, last: () => states[states.length - 1] };
}

test('Connect with no Deskhand app: says so, and opens the website', async () => {
  const b = boot({ app: false });
  const reply = await b.ask({ type: 'pair' });
  assert.deepEqual(plain(reply), { ok: false, noapp: true });
  assert.deepEqual(b.opened, [SITE]);
  assert.equal(b.last().status, 'noapp');
  assert.match(b.last().detail, /not found/i);
});

test('Connect with the app running: pairs, and opens nothing', async () => {
  const b = boot({ app: true });
  const reply = await b.ask({ type: 'pair' });
  assert.deepEqual(plain(reply), { ok: true, noapp: false });
  assert.equal(b.states.some((s) => s.status === 'pairing'), true);          // waiting for the Yes/No window on the computer
  b.FakeSocket.last.onmessage({ data: JSON.stringify({ paired: 'secret-token' }) });
  await tick();
  assert.equal(b.store.local.token, 'secret-token');
  assert.equal(b.last().status, 'connected');
  assert.deepEqual(b.opened, []);
});

test('Press Connect again after installing the app: it connects this time', async () => {
  const first = boot({ app: false });
  assert.equal((await first.ask({ type: 'pair' })).noapp, true);
  const second = boot({ app: true });                                        // the same press, app now running
  assert.equal((await second.ask({ type: 'pair' })).ok, true);
  assert.deepEqual(second.opened, []);
});

test('quiet background retries never open a tab, even with no app', async () => {
  const b = boot({ app: false });
  b.store.local.token = 'already-paired';
  const found = await b.ctx.connect(false);                                  // what the 5-second timer and the alarm call
  assert.equal(found, false);
  assert.deepEqual(b.opened, []);
});

test('the site address is the same in the background script and the popup', () => {
  const popup = fs.readFileSync(path.resolve(here, '..', '..', 'extension', 'popup.js'), 'utf8');
  assert.ok(popup.includes(SITE) && SOURCE.includes(SITE));
});

test('the extension version was bumped for this change', () => {
  const manifest = JSON.parse(fs.readFileSync(path.resolve(here, '..', '..', 'extension', 'manifest.json'), 'utf8'));
  assert.notEqual(manifest.version, '1.0.0');
});
