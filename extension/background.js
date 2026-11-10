// Deskhand: connects OUT to the Deskhand app on this computer (127.0.0.1 only) and runs its commands on browser tabs.
// Nothing here reads cookies or passwords, and nothing is sent anywhere except to the Deskhand app on this computer.
const PORT = 9223;
let ws = null;
let attached = new Set();
let state = { status: "searching", detail: "" };   // searching | pairing | connected | denied

function setState(status, detail = "") {
  state = { status, detail };
  chrome.storage.session.set({ state }).catch(() => {});
  const badge = { connected: ["", "#74c9a0"], pairing: ["…", "#e3b07a"], denied: ["!", "#e0795c"], searching: ["", "#999"] }[status];
  chrome.action.setBadgeText({ text: badge[0] }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ color: badge[1] }).catch(() => {});
}

async function getToken() { return (await chrome.storage.local.get("token")).token || ""; }

async function connect(pair = false) {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  const token = await getToken();
  if (!token && !pair) { setState("searching", "not paired"); return; }
  const q = token ? `token=${encodeURIComponent(token)}` : "pair=1";
  try { ws = new WebSocket(`ws://127.0.0.1:${PORT}/?${q}`); } catch (e) { return; }
  if (!token) setState("pairing", "waiting for you to approve it in Deskhand");
  ws.onopen = () => ws.send(JSON.stringify({ hello: "deskhand-extension", version: chrome.runtime.getManifest().version }));
  ws.onmessage = async (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (m.paired) { await chrome.storage.local.set({ token: m.paired }); setState("connected"); return; }
    if (m.denied) { setState("denied", m.denied); return; }
    if (m.welcome) { setState("connected"); return; }
    let out;
    try { out = { id: m.id, result: await handle(m) }; } catch (e) { out = { id: m.id, error: String((e && e.message) || e) }; }
    try { ws.send(JSON.stringify(out)); } catch (e) {}
  };
  ws.onclose = () => {
    ws = null;
    for (const t of attached) chrome.debugger.detach({ tabId: t }).catch(() => {});      // let go of every tab: nothing stays attached
    attached.clear();
    if (state.status === "connected") setState("searching", "Deskhand app not running");
  };
  ws.onerror = () => {};
}

async function attach(tabId) {
  if (attached.has(tabId)) return;
  try {
    await chrome.debugger.attach({ tabId }, "1.3");
  } catch (e) {
    // still attached from an earlier connection (the app restarted): that is ours, carry on. Anything else is a real error.
    if (!/already attached/i.test(String((e && e.message) || e))) throw e;
  }
  attached.add(tabId);
  // the page behaves as if it had focus even though Deskhand's window sits behind the person's own windows
  try { await chrome.debugger.sendCommand({ tabId }, "Emulation.setFocusEmulationEnabled", { enabled: true }); } catch (e) {}
  try { await chrome.debugger.sendCommand({ tabId }, "Page.enable", {}); } catch (e) {}
}

// Deskhand works in its own browser window, so it never touches the tabs the person is using and never takes focus.
async function agentTab(url) {
  const saved = (await chrome.storage.session.get("agentWindow")).agentWindow;
  if (saved) {
    try {
      const w = await chrome.windows.get(saved, { populate: true });
      const tab = (w.tabs || [])[0];
      if (tab) { await chrome.tabs.update(tab.id, { url }); return tab.id; }
    } catch (e) { /* the person closed it: make a new one */ }
  }
  const w = await chrome.windows.create({ url, focused: false, state: "normal", width: 1100, height: 780, type: "normal" });
  await chrome.storage.session.set({ agentWindow: w.id });
  return w.tabs[0].id;
}
chrome.debugger.onDetach.addListener((src) => { if (src.tabId) attached.delete(src.tabId); });

async function handle(m) {
  if (m.cmd === "tabs") {
    const tabs = await chrome.tabs.query({});
    return tabs.map(t => ({ id: t.id, url: t.url || "", title: t.title || "", active: t.active, windowId: t.windowId,
                            lastAccessed: t.lastAccessed || 0 }));
  }
  if (m.cmd === "openAgent") { return { id: await agentTab(m.url) }; }   // opens or reuses Deskhand's own window, unfocused
  if (m.cmd === "cdp") {
    await attach(m.tabId);
    return await chrome.debugger.sendCommand({ tabId: m.tabId }, m.method, m.params || {});
  }
  throw new Error("unknown command " + m.cmd);
}

// popup <-> background
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === "status") { reply({ state, connected: !!ws && ws.readyState === WebSocket.OPEN }); return; }
  if (msg.type === "pair") { ws && ws.close(); ws = null; chrome.storage.local.remove("token").then(() => { connect(true); reply({ ok: true }); }); return true; }
  if (msg.type === "forget") { ws && ws.close(); ws = null; chrome.storage.local.remove("token").then(() => { setState("searching", "not paired"); reply({ ok: true }); }); return true; }
});

chrome.alarms.create("keepalive", { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener(() => connect(false));
chrome.runtime.onStartup.addListener(() => connect(false));
chrome.runtime.onInstalled.addListener(() => connect(false));
connect(false);
setInterval(() => connect(false), 5000);
