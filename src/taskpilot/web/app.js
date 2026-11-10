/* Deskhand controller app. Talks only to this server's /v1 API; never to a target device directly. */
(() => {
  'use strict';
  const L = window.PilotLogic;
  const $ = (id) => document.getElementById(id);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const ls = {
    get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: keep going */ } },
    del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
  };

  const S = {
    token: ls.get('pilot.token'), me: null, devices: [], tasks: new Map(), currentId: null, qa: new Map(),
    targets: new Set(JSON.parse(ls.get('pilot.targets', '[]') || '[]')), listening: false, rec: null, countdown: null, installEvt: null, seenTerminal: new Set(),
    settings: Object.assign({ auto: true, speak: false, notify: false, theme: 'auto' }, JSON.parse(ls.get('pilot.settings', '{}') || '{}')),
  };

  // ------------------------------------------------------------------ theme (Match system / Light / Dark)
  function effectiveTheme() {
    const d = document.documentElement.getAttribute('data-theme');
    return d || (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  }
  function applyTheme(mode) {
    const root = document.documentElement;
    if (mode === 'dark' || mode === 'light') root.setAttribute('data-theme', mode); else root.removeAttribute('data-theme');
    const m = mode === 'dark' || mode === 'light' ? mode : 'auto';
    [['themeAuto', 'auto'], ['themeLight', 'light'], ['themeDark', 'dark']].forEach(([id, k]) => { const b = $(id); if (b) b.setAttribute('aria-pressed', String(k === m)); });
  }
  function setTheme(mode) { S.settings.theme = mode; saveSettings(); applyTheme(mode); }

  // ------------------------------------------------------------------ api
  async function api(path, { method = 'GET', body } = {}) {
    const headers = {};
    if (S.token) headers.Authorization = 'Bearer ' + S.token;
    if (body) headers['Content-Type'] = 'application/json';
    const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    if (r.status === 401 && S.token) { forgetDevice('You were signed out of this device. Please sign in again.'); throw new Error('unauthorized'); }
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = data.detail;
      throw new Error(typeof d === 'string' ? d : (d && d.problems ? d.problems.join('; ') : r.statusText));
    }
    return data;
  }

  // ------------------------------------------------------------------ views
  function show(view) {
    $('pairView').hidden = view !== 'pair';
    $('appView').hidden = view !== 'app';
    $('topbar').hidden = view === 'app';
  }
  function setConn(state) { const el = $('conn'); el.dataset.state = state; el.textContent = state; }

  function forgetDevice(message) {
    S.token = null; ls.del('pilot.token'); S.tasks.clear(); S.currentId = null; S.connectAsked = false;
    $('pairErr').textContent = message || '';
    show('pair');
  }

  async function pair(ev) {
    ev.preventDefault();
    $('pairErr').textContent = '';
    try {
      const r = await api('/v1/pair', { method: 'POST', body: { code: L.normalizeCode($('code').value), name: $('devName').value.trim() } });
      S.token = r.token; ls.set('pilot.token', r.token);
      await start();
    } catch (e) { $('pairErr').textContent = e.message; }
  }

  // ------------------------------------------------------------------ sign in by email
  function thisDeviceName() { return (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || 'My device'; }

  async function finishLogin(r) { S.token = r.token; ls.set('pilot.token', r.token); history.replaceState(null, '', location.pathname); await start(); }

  async function verifyCode(code) {
    $('emailErr').textContent = '';
    try { await finishLogin(await api('/v1/auth/verify', { method: 'POST', body: { code: L.normalizeCode(code), name: thisDeviceName() } })); }
    catch (e) { $('emailCard').hidden = false; $('codeCard').hidden = true; $('emailErr').textContent = e.message; show('pair'); }
  }

  async function sendEmail(ev) {
    if (ev) ev.preventDefault();
    $('emailErr').textContent = '';
    const email = $('email').value.trim();
    try {
      const r = await api('/v1/auth/email', { method: 'POST', body: { email } });
      if (!r.sent) { $('emailErr').textContent = 'This server cannot send email yet. Ask whoever runs it for a pairing code.'; return; }
      $('emailForm').hidden = true; $('verifyForm').hidden = false;
      $('sentNote').textContent = 'We emailed ' + email + '. Tap the link in the email, or type the code from it here.';
      $('vcode').focus();
    } catch (e) { $('emailErr').textContent = e.message; }
  }

  async function setupLogin() {
    $('emailForm').addEventListener('submit', sendEmail);
    $('verifyForm').addEventListener('submit', (e) => { e.preventDefault(); verifyCode($('vcode').value); });
    $('resendBtn').onclick = () => { $('verifyForm').hidden = true; $('emailForm').hidden = false; $('emailErr').textContent = ''; };
    $('haveCodeBtn').onclick = () => { $('emailCard').hidden = true; $('codeCard').hidden = false; };
    let cfg = { email_login: false };
    try { cfg = await api('/v1/auth/config'); } catch { /* old server: pairing code only */ }
    $('emailCard').hidden = !cfg.email_login; $('codeCard').hidden = cfg.email_login;
    $('noEmail').hidden = !!cfg.email_login;
    const fromLink = async () => {                                  // the link in the email: the code rides in the #fragment
      const m = /[#&]code=([A-Za-z0-9-]+)/.exec(location.hash);
      if (m && !S.token) { show('pair'); await verifyCode(m[1]); }
    };
    window.addEventListener('hashchange', fromLink);                // the app was already open when the link was tapped
    await fromLink();
  }

  async function start() {
    try { S.me = await api('/v1/me'); } catch { return; }
    S.extUrl = (S.me && S.me.extension_url) || '';                       // where to get the Chrome extension ('' = not published yet)
    S.devices = S.me.devices; renderDevices();
    show('app');
    $('whoami').textContent = 'Signed in as ' + S.me.name + ' on this device.';
    $('userName').textContent = S.me.name || 'Signed in';
    $('avatar').textContent = (S.me.name || 'D').trim().charAt(0).toUpperCase() || 'D';
    api('/v1/workspace').then((ws) => { $('wsName').textContent = ws.name || 'Deskhand'; $('userSub').textContent = ws.name ? ws.name + ' · settings' : 'Settings'; }).catch(() => { /* older server */ });
    const list = await api('/v1/tasks?limit=60').catch(() => []);       // chats are threads: enough tasks to rebuild them
    list.forEach((t) => { S.tasks.set(t.id, t); if (t.terminal) S.seenTerminal.add(t.id); });
    const active = list.find((t) => !t.terminal);
    if (active) selectTask(active.id); else renderCurrent();
    renderHistory(); renderApproval();
    streamEvents();
  }

  // ------------------------------------------------------------------ devices
  function toast(msg, ms = 5000) {
    const el = $('toast'); el.textContent = msg; el.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => { el.hidden = true; }, ms);
  }

  const EXAMPLES = ['Open Notepad and write a shopping list', 'Open Calculator and add 7 and 5', "What's the latest AI news today?", 'Open YouTube and play something relaxing'];

  function saveTargets() { ls.set('pilot.targets', JSON.stringify([...S.targets])); }

  function deviceName(id) { const d = S.devices.find((x) => x.device_id === id); return d ? d.name : id; }

  function renderTargets() {
    const box = $('targets');
    box.textContent = '';
    S.devices.forEach((d) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'tchip'; b.dataset.online = d.online ? '1' : '0';
      b.setAttribute('aria-pressed', S.targets.has(d.device_id) ? 'true' : 'false');
      const dot = document.createElement('span'); dot.className = 'dot'; dot.dataset.on = d.online ? '1' : '0';
      const nm = document.createElement('span'); nm.textContent = d.name + (d.online ? '' : ' (offline)');
      b.append(dot, nm);
      b.onclick = () => { if (S.targets.has(d.device_id)) S.targets.delete(d.device_id); else S.targets.add(d.device_id); saveTargets(); renderTargets(); };
      box.appendChild(b);
    });
    const chosen = S.devices.filter((d) => S.targets.has(d.device_id));
    const off = chosen.filter((d) => !d.online);
    const note = $('targetNote');
    if (!chosen.length) note.textContent = 'Tap a computer to choose where this runs. Pick several to send the same task to all of them.';
    else if (off.length) note.textContent = off.map((d) => d.name).join(', ') + (off.length > 1 ? ' are' : ' is') + ' offline. On that computer open Deskhand from the Start menu (or press Ctrl+Alt+P) to bring it online.';
    else note.textContent = chosen.length > 1 ? 'Sending to ' + chosen.length + ' computers at once.' : '';
    $('targetLabel').textContent = !chosen.length ? 'Choose a computer' : chosen.length === 1 ? chosen[0].name : chosen.length + ' computers';
  }

  function renderSideComputers() {
    const ul = $('sideComputers'); ul.textContent = '';
    S.devices.forEach((d) => {
      const li = document.createElement('li');
      const dot = document.createElement('span'); dot.className = 'dot'; dot.dataset.on = d.online ? '1' : '0';
      const nm = document.createElement('span'); nm.className = 'nm'; nm.textContent = d.name;
      li.title = d.online ? 'Online' : 'Offline';
      li.append(dot, nm); ul.appendChild(li);
    });
  }

  function renderDevices() {
    const none = !S.devices.length;
    $('setup').hidden = !none; $('composer').hidden = none;
    renderSideComputers();
    if (none) { S.hadDevices = false; renderExt(); autoConnect(); return; }
    connectArrived();
    // forget chosen computers that no longer exist; default to the first online one
    for (const id of [...S.targets]) if (!S.devices.some((d) => d.device_id === id)) S.targets.delete(id);
    if (!S.targets.size) { const pick = S.devices.find((d) => d.online) || S.devices[0]; S.targets.add(pick.device_id); }
    saveTargets(); renderTargets();
    if (S.hadDevices === false) toast(S.devices[0].name + ' is connected. Try a task below.');
    S.hadDevices = true;
    const ex = $('examples');
    if (!ex.children.length) EXAMPLES.forEach((t) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = t; b.onclick = () => { $('goal').value = t; autoGrow(); $('goal').focus(); }; ex.appendChild(b); });
    renderComputers();
    renderExt();
  }

  // ------------------------------------------------------------------ Chrome extension: use the person's own Chrome
  // Without it Deskhand works in a separate browser profile, so the person would sign in to every site again.
  const extUrl = () => (S.extUrl && /^https:\/\//i.test(S.extUrl) ? S.extUrl : '');          // only a plain https link is ever shown
  const bannerDismissed = () => { try { return localStorage.getItem('pilot.extBanner') === '1'; } catch { return false; } };

  function renderExt() {
    const url = extUrl();
    const ul = $('extList');
    if (ul) {                                                                                  // Settings: one line per computer
      ul.textContent = '';
      S.devices.forEach((d) => {
        const li = document.createElement('li');
        const dot = document.createElement('span'); dot.className = 'dot'; dot.dataset.on = d.extension ? '1' : '0';
        const nm = document.createElement('span'); nm.className = 'name'; nm.textContent = d.name;
        const st = document.createElement('span'); st.className = 'sub'; st.textContent = !d.online ? 'computer offline' : d.extension ? 'Connected' : 'Not set up';
        li.append(dot, nm, st); ul.appendChild(li);
      });
    }
    const need = S.devices.some((d) => !d.extension);
    if ($('extSoon')) $('extSoon').hidden = !(need && !url);
    if ($('extLink')) { $('extLink').hidden = !(need && url); if (url) $('extLink').href = url; }
    // the small tip on the home screen
    const tip = S.devices.some((d) => d.online && !d.extension) && !bannerDismissed() && !S.currentId;       // home screen only
    if ($('extBanner')) {
      $('extBanner').hidden = !tip;
      $('extBannerSoon').hidden = !!url;
      $('extBannerLink').hidden = !url; if (url) $('extBannerLink').href = url;
    }
    if ($('connectStep2') && !$('connectStep2').hidden) renderExtStep();
  }

  function renderExtStep() {                                                                   // step 2 of the connect pop-up
    const dev = S.devices.find((d) => d.device_id === connectDevice), on = !!(dev && dev.extension), url = extUrl();
    $('extStepStatus').dataset.state = on ? 'done' : url ? 'waiting' : 'soon';
    $('extStepText').textContent = on ? 'Chrome extension connected' : url ? 'Waiting for the extension to connect…' : 'Coming soon';
    $('extStepLink').hidden = on || !url; if (url) $('extStepLink').href = url;
    $('extSkip').hidden = on;
  }

  // ------------------------------------------------------------------ "Connect your computer" pop-up
  const CONNECT_LIFE_MS = 14 * 60 * 1000;                  // a command works for 15 minutes: get a fresh one a minute before
  let connectTimer = null, connectBase = 0, connectBusy = false, connectStep = 1, connectDevice = '';

  async function fetchConnectCommand() {
    if (connectBusy) return;
    connectBusy = true;
    $('connectErr').textContent = '';
    $('connectCmd').textContent = 'Getting your command…'; $('connectCopy').disabled = true; $('connectCopy').textContent = 'Copy command';
    try {
      const r = await api('/v1/add-device', { method: 'POST' });
      $('connectCmd').textContent = r.command; $('connectCopy').disabled = false;
      clearTimeout(connectTimer);
      connectTimer = setTimeout(() => { if ($('connectDlg').open && !connectBusy) fetchConnectCommand(); }, CONNECT_LIFE_MS);
    } catch (e) {
      $('connectCmd').textContent = ''; $('connectErr').textContent = 'Could not get a command: ' + (e.message || e);
    } finally { connectBusy = false; }
  }

  function openConnect() {
    const dlg = $('connectDlg');
    if (dlg.open) return;
    if ($('menu').open) $('menu').close();
    connectBase = S.devices.length; connectStep = 1; connectDevice = '';
    $('connectTitle').textContent = 'Connect your computer';
    $('connectStep1').hidden = false; $('connectStep2').hidden = true;
    $('connectStatus').dataset.state = 'waiting'; $('connectStatusText').textContent = 'Waiting for your computer…';
    closeSidebar(); dlg.showModal();
    dlg.scrollTop = 0;                                       // the browser focuses the last button: start at the top anyway
    fetchConnectCommand();
  }

  function closeConnect() { clearTimeout(connectTimer); if ($('connectDlg').open) $('connectDlg').close(); }

  // once per visit, right after sign-in, when there is nothing to run tasks on
  function autoConnect() {
    if (S.connectAsked || !S.token) return;
    S.connectAsked = true;
    openConnect();
  }

  // a computer showed up while the pop-up is open: say so, then move on to step 2 (use your own Chrome)
  function connectArrived() {
    if (!$('connectDlg').open) return;
    if (connectStep === 2) { renderExtStep(); return; }
    if (S.devices.length <= connectBase) return;
    const fresh = S.devices[S.devices.length - 1];
    connectDevice = fresh ? fresh.device_id : '';
    connectStep = 2;
    $('connectStatus').dataset.state = 'done';
    $('connectStatusText').textContent = (fresh && fresh.name ? fresh.name : 'Your computer') + ' is connected.';
    clearTimeout(connectTimer);
    setTimeout(() => {
      if (!$('connectDlg').open) return;
      $('connectTitle').textContent = 'Use your own Chrome';
      $('connectStep1').hidden = true; $('connectStep2').hidden = false;
      renderExtStep();
    }, 1300);
  }

  function renderComputers() {
    const ul = $('computers'); if (!ul) return;
    ul.textContent = '';
    if (!S.devices.length) { const li = document.createElement('li'); li.className = 'sub'; li.textContent = 'None yet.'; ul.appendChild(li); return; }
    S.devices.forEach((d) => {
      const li = document.createElement('li');
      const dot = document.createElement('span'); dot.className = 'dot'; dot.dataset.on = d.online ? '1' : '0';
      const nm = document.createElement('span'); nm.className = 'name'; nm.textContent = d.name;
      const st = document.createElement('span'); st.className = 'sub'; st.textContent = d.online ? 'online' : 'offline';
      const rm = document.createElement('button'); rm.type = 'button'; rm.textContent = 'Remove';
      rm.onclick = async () => { if (confirm('Remove ' + d.name + '? Deskhand will stop controlling it.')) { try { await api('/v1/devices/' + d.device_id, { method: 'DELETE' }); S.devices = S.devices.filter((x) => x.device_id !== d.device_id); renderDevices(); } catch (e) { alert(e.message); } } };
      li.append(dot, nm, st, rm); ul.appendChild(li);
    });
  }

  // ------------------------------------------------------------------ live events (server-sent events over fetch)
  let streaming = false;
  async function streamEvents() {
    if (streaming) return;
    streaming = true;
    let backoff = 1000;
    while (S.token) {
      try {
        setConn('connecting');
        const r = await fetch('/v1/events', { headers: { Authorization: 'Bearer ' + S.token } });
        if (r.status === 401) { forgetDevice('This device is no longer paired. Please pair it again.'); break; }
        if (!r.ok || !r.body) throw new Error('stream failed');
        setConn('online'); backoff = 1000;
        const reader = r.body.getReader(), dec = new TextDecoder();
        let buf = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf('\n\n')) >= 0) { handleEvent(buf.slice(0, i)); buf = buf.slice(i + 2); }
        }
      } catch { /* reconnect below */ }
      setConn('offline');
      await sleep(backoff); backoff = Math.min(backoff * 2, 15000);
    }
    streaming = false;
  }

  function handleEvent(chunk) {
    let ev = 'message', data = '';
    chunk.split('\n').forEach((line) => {
      if (line.startsWith('event:')) ev = line.slice(6).trim();
      else if (line.startsWith('data:')) data += line.slice(5).trim();
    });
    if (!data) return;
    let obj; try { obj = JSON.parse(data); } catch { return; }
    if (ev === 'devices') { S.devices = obj; renderDevices(); }
    else if (ev === 'task') onTask(obj);
  }

  function onTask(t) {
    const before = S.tasks.get(t.id);
    S.tasks.set(t.id, t);
    renderHistory();
    if (t.id === S.currentId) renderCurrent();
    renderApproval();
    if (t.pending_approval && !(before && before.pending_approval)) notify('Deskhand needs your OK', L.approvalText(t.pending_approval));
    if (t.terminal && !S.seenTerminal.has(t.id)) {
      S.seenTerminal.add(t.id);
      if (t.id === S.currentId) {
        if (S.settings.speak && t.result) speak(t.result);
        if (document.hidden) notify('Deskhand: ' + L.statusLabel(t.status), (t.result || t.error || t.goal).slice(0, 120));
      }
    }
  }

  // ------------------------------------------------------------------ rendering
  function closeSidebar() { $('appView').classList.remove('open'); $('scrim').hidden = true; $('sideToggle').setAttribute('aria-expanded', 'false'); }
  function openSidebar() { $('appView').classList.add('open'); $('scrim').hidden = false; $('sideToggle').setAttribute('aria-expanded', 'true'); }

  // the chat (thread of tasks) a task belongs to; the open chat shows its LATEST task as the live one
  function chatOf(id) {
    const t = S.tasks.get(id);
    if (!t) return null;
    const key = t.thread || t.id;
    return L.groupChats([...S.tasks.values()]).find((c) => c.thread === key) || null;
  }
  function selectTask(id) { const c = chatOf(id); S.currentId = c ? c.latest.id : id; closeSidebar(); renderCurrent(); renderHistory(); }
  function newTask() { S.currentId = null; closeSidebar(); renderCurrent(); renderHistory(); $('goal').focus(); }

  function scrollToEnd(force) {
    const th = $('thread');
    const near = th.scrollHeight - th.scrollTop - th.clientHeight < 160;
    if (force || near) th.scrollTop = th.scrollHeight;
  }

  function renderCurrent() {
    renderExt();                                               // the tip is for the home screen only
    const t = S.tasks.get(S.currentId);
    $('current').hidden = !t; $('welcome').hidden = !!t;
    const chat = t ? chatOf(t.id) : null;
    $('chatTitle').textContent = t ? (chat ? chat.title : t.goal) : 'New task';      // the chat is named by its first message
    const running = !!t && !t.terminal;
    $('stopBtn').hidden = !running;                              // Stop takes the place of Send while a task is running
    $('sendBtn').hidden = running;
    $('deleteChatBtn').hidden = !t || running;                   // a running chat must be stopped first
    if (!t) { $('earlier').hidden = true; $('earlier').textContent = ''; return; }
    renderEarlier(t, chat);
    $('current').dataset.done = t.terminal ? '1' : '0';
    const chip = $('chip');
    chip.textContent = L.statusLabel(t.status); chip.dataset.tone = L.statusTone(t.status);
    $('curGoal').textContent = t.goal;
    $('elapsed').textContent = (t.terminal ? 'Worked for ' : 'Working for ') + L.elapsedText(t.created_at, t.terminal ? t.updated_at : null);
    const ol = $('steps'); ol.innerHTML = '';
    const steps = t.steps || [];
    steps.forEach((st) => {
      const d = L.describeStep(st);
      const li = document.createElement('li'); li.dataset.state = d.state;
      const span = document.createElement('span'); span.textContent = d.text;
      li.appendChild(span);
      if (d.note && d.state !== 'ok') { const n = document.createElement('span'); n.className = 'note'; n.textContent = d.note; li.appendChild(n); }
      ol.appendChild(li);
    });
    $('stepsBox').hidden = !steps.length;
    $('stepsSummary').textContent = t.terminal ? steps.length + (steps.length === 1 ? ' step' : ' steps') : 'Thinking…';
    renderQA(t);
    $('resultBox').hidden = !t.result;
    $('result').textContent = t.result || '';
    const err = $('errorLine'); err.hidden = !(t.error && t.terminal); err.textContent = L.friendlyError(t.error);
    scrollToEnd();
  }

  // the earlier turns of the open chat, above the live one: what was asked, how it went, and how it ended
  function renderEarlier(t, chat) {
    const box = $('earlier');
    const before = chat ? chat.tasks.filter((x) => x.id !== t.id && x.created_at <= t.created_at) : [];
    box.hidden = !before.length; box.textContent = '';
    S.fetching = S.fetching || new Set();
    before.forEach((p) => {
      if (!p.steps && !S.fetching.has(p.id)) {                    // the list has no steps: fetch them once, then redraw
        S.fetching.add(p.id);
        api('/v1/tasks/' + p.id).then((full) => { S.tasks.set(p.id, full); const cur = S.tasks.get(S.currentId); if (cur) renderEarlier(cur, chatOf(cur.id)); }).catch(() => { /* gone */ });
      }
      const sec = document.createElement('section'); sec.className = 'convo turn'; sec.dataset.done = '1';
      const b = document.createElement('div'); b.className = 'bubble user-bubble';
      const bp = document.createElement('p'); bp.className = 'goal'; bp.textContent = p.goal; b.appendChild(bp);
      const as = document.createElement('div'); as.className = 'assistant';
      const steps = p.steps || [];
      if (steps.length) {
        const det = document.createElement('details'); det.className = 'steps-box';
        const sum = document.createElement('summary'); sum.textContent = steps.length + (steps.length === 1 ? ' step' : ' steps'); det.appendChild(sum);
        const ol = document.createElement('ol'); ol.className = 'steps';
        steps.forEach((st) => {
          const d = L.describeStep(st);
          const li = document.createElement('li'); li.dataset.state = d.state;
          const span = document.createElement('span'); span.textContent = d.text; li.appendChild(span);
          if (d.note && d.state !== 'ok') { const n = document.createElement('span'); n.className = 'note'; n.textContent = d.note; li.appendChild(n); }
          ol.appendChild(li);
        });
        det.appendChild(ol); as.appendChild(det);
      }
      (S.qa.get(p.id) || []).forEach((q) => {
        const qp = document.createElement('p'); qp.className = 'q'; qp.textContent = q.q;
        const a = document.createElement('div'); a.className = 'bubble'; const ap = document.createElement('p'); ap.textContent = q.a; a.appendChild(ap);
        as.append(qp, a);
      });
      if (p.result) {
        const rb = document.createElement('div'); rb.className = 'answer-box';
        const pre = document.createElement('pre'); pre.className = 'result'; pre.textContent = p.result; rb.appendChild(pre); as.appendChild(rb);
      }
      if (p.error && p.terminal) { const er = document.createElement('p'); er.className = 'err'; er.textContent = L.friendlyError(p.error); as.appendChild(er); }
      sec.append(b, as); box.appendChild(sec);
    });
  }

  // questions already answered stay in the conversation, like a chat
  function renderQA(t) {
    const box = $('qa'); box.textContent = '';
    (S.qa.get(t.id) || []).forEach((p) => {
      const q = document.createElement('p'); q.className = 'q'; q.textContent = p.q;
      const a = document.createElement('div'); a.className = 'bubble'; const ap = document.createElement('p'); ap.textContent = p.a; a.appendChild(ap);
      box.append(q, a);
    });
  }

  function renderHistory() {
    const ul = $('history'); ul.innerHTML = '';
    const chats = L.groupChats([...S.tasks.values()]).slice(0, 25);        // one row per chat, not per message
    const cur = S.tasks.get(S.currentId), curKey = cur ? (cur.thread || cur.id) : null;
    $('emptyHistory').hidden = chats.length > 0;
    chats.forEach((c) => {
      const li = document.createElement('li'); li.className = 'chat-row';
      const b = document.createElement('button'); b.type = 'button';
      if (c.thread === curKey) b.setAttribute('aria-current', 'true');
      const dot = document.createElement('span'); dot.className = 'sdot'; dot.dataset.tone = L.statusTone(c.latest.status); dot.title = L.statusLabel(c.latest.status);
      const g = document.createElement('span'); g.className = 'g'; g.textContent = c.title;
      b.append(dot, g); b.onclick = () => selectTask(c.latest.id);
      const del = document.createElement('button'); del.type = 'button'; del.className = 'row-del';
      del.setAttribute('aria-label', 'Delete this chat'); del.title = 'Delete this chat';
      del.appendChild(trashIcon());
      del.onclick = (e) => { e.stopPropagation(); deleteChat(c.latest.id); };
      li.append(b, del); ul.appendChild(li);
    });
  }

  // ------------------------------------------------------------------ deleting chats
  function trashIcon() {                                                    // built from DOM nodes: the app never sets html from strings
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    [['viewBox', '0 0 24 24'], ['width', '15'], ['height', '15'], ['aria-hidden', 'true']].forEach(([k, v]) => svg.setAttribute(k, v));
    const p = document.createElementNS(ns, 'path');
    [['d', 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6'], ['fill', 'none'], ['stroke', 'currentColor'], ['stroke-width', '1.8'],
      ['stroke-linecap', 'round'], ['stroke-linejoin', 'round']].forEach(([k, v]) => p.setAttribute(k, v));
    svg.appendChild(p);
    return svg;
  }

  async function deleteChat(id) {
    if (!confirm('Delete this chat? This cannot be undone.')) return;
    try {
      const r = await api('/v1/chats/' + encodeURIComponent(id), { method: 'DELETE' });
      (r.ids || []).forEach((i) => { S.tasks.delete(i); S.qa.delete(i); S.seenTerminal.delete(i); });
      if (!S.tasks.has(S.currentId)) S.currentId = null;                     // the open chat is gone: back to the start screen
      renderCurrent(); renderHistory(); renderApproval();
      toast('Chat deleted.');
    } catch (e) { alert(e.message || 'Could not delete the chat.'); }
  }

  async function reloadTasks() {
    const list = await api('/v1/tasks?limit=60').catch(() => []);
    S.tasks.clear();
    list.forEach((t) => { S.tasks.set(t.id, t); if (t.terminal) S.seenTerminal.add(t.id); });
    if (!S.tasks.has(S.currentId)) S.currentId = null;
    renderCurrent(); renderHistory(); renderApproval();
  }

  async function deleteAllChats() {
    if (!confirm('Delete all chats? Anything still running is kept. This cannot be undone.')) return;
    try {
      const r = await api('/v1/chats', { method: 'DELETE' });
      await reloadTasks();
      $('dataMsg').textContent = r.deleted ? 'Deleted ' + r.deleted + (r.deleted === 1 ? ' task' : ' tasks') + ' from your chats.' : 'There were no finished chats to delete.';
    } catch (e) { alert(e.message || 'Could not delete the chats.'); }
  }

  function pendingTask() { return [...S.tasks.values()].find((t) => !t.terminal && t.pending_approval) || null; }
  function runningTask() { return [...S.tasks.values()].find((t) => !t.terminal) || null; }

  function renderApproval() {
    const t = pendingTask();
    $('approval').hidden = !t || t.id !== S.currentId;
    if (!t) { S.choiceSig = ''; return; }
    if (t.id !== S.currentId) { selectTask(t.id); return; }           // bring the question into view, like a chat reply
    const pa = t.pending_approval;
    const choice = pa.kind === 'access' || pa.kind === 'question';
    $('approvalText').textContent = L.approvalText(pa);
    $('approvalTitle').textContent = pa.kind === 'question' ? 'Deskhand has a question' : 'Deskhand needs your OK';
    $('approvalBtns').hidden = choice;
    $('choices').hidden = !choice || !(pa.options || []).length;
    $('answerForm').hidden = !(choice && pa.allow_text);
    $('approvalHint').textContent = choice ? (pa.kind === 'access' ? 'You stay in control: Deskhand only touches what you allow.' : '') : 'Say "approve" or "deny", or tap a button.';
    const sig = t.id + '|' + JSON.stringify(pa.options || []) + pa.question;
    if (choice && S.choiceSig !== sig) {                       // rebuild only when the question changes (no flicker while polling)
      S.choiceSig = sig;
      const box = $('choices'); box.textContent = '';
      (pa.options || []).forEach((o, i) => {
        const b = document.createElement('button'); b.type = 'button'; b.textContent = o.label;
        if (i === 0 && pa.kind === 'access' && o.id !== 'no') b.className = 'primary';
        if (o.id === 'no') b.className = 'danger';
        b.onclick = () => answer(t.id, { choice: o.id }, o.label);
        box.appendChild(b);
      });
      $('answerText').value = '';
    }
    scrollToEnd(true);
  }

  async function answer(taskId, body, label) {
    const t = S.tasks.get(taskId);
    const question = t && t.pending_approval ? L.approvalText(t.pending_approval) : '';
    try {
      await api('/v1/tasks/' + taskId + '/answer', { method: 'POST', body });
      const list = S.qa.get(taskId) || []; list.push({ q: question, a: label || body.text || body.choice }); S.qa.set(taskId, list);
      S.choiceSig = ''; if (t) { renderQA(t); }
    } catch (e) { $('voiceLine').textContent = e.message; }
  }

  // ------------------------------------------------------------------ actions
  async function run(text) {
    const goal = text.trim();
    if (!goal) return;
    cancelCountdown();
    const cur = S.tasks.get(S.currentId);
    if (cur && !cur.terminal && !pendingTask()) {                // one thing at a time in a chat: Stop is where Send was
      $('voiceLine').textContent = 'Deskhand is still working. Press Stop, or wait for it to finish.';
      return;
    }
    $('sendBtn').disabled = true;
    try {
      const chosen = S.devices.filter((d) => S.targets.has(d.device_id));
      const live = chosen.filter((d) => d.online), skipped = chosen.filter((d) => !d.online);
      if (!live.length) { $('voiceLine').textContent = chosen.length ? deviceName(chosen[0].device_id) + ' is offline: open Deskhand on it first.' : 'Choose a computer to run this on.'; $('sendBtn').disabled = false; return; }
      let first = null;
      const failed = [];
      const cf = L.continueTarget(cur);                              // a finished chat is continued; "New task" starts a fresh one
      for (const d of live) {
        try {
          const r = await api('/v1/tasks', { method: 'POST', body: { goal, device_id: d.device_id, ...(cf ? { continue_from: cf } : {}) } });
          S.tasks.set(r.task_id, await api('/v1/tasks/' + r.task_id));
          if (!first) first = r.task_id;
        } catch (e) { failed.push(d.name + ': ' + e.message); }
      }
      if (first) { $('goal').value = ''; autoGrow(); $('voiceLine').textContent = ''; S.currentId = first; renderCurrent(); renderHistory(); scrollToEnd(true); }
      const notes = [];
      if (live.length > 1 && first) notes.push('Sent to ' + (live.length - failed.length) + ' computers.');
      if (skipped.length) notes.push('Skipped offline: ' + skipped.map((d) => d.name).join(', ') + '.');
      if (failed.length) notes.push(failed.join(' '));
      if (notes.length) $('voiceLine').textContent = notes.join(' ');
    } catch (e) { $('voiceLine').textContent = e.message; }
    $('sendBtn').disabled = false;
  }

  async function decide(approved) {
    const t = pendingTask(); if (!t) return;
    try { await api(`/v1/tasks/${t.id}/approve`, { method: 'POST', body: { approved } }); } catch (e) { $('voiceLine').textContent = e.message; }
  }
  async function cancelCurrent() {
    const t = S.tasks.get(S.currentId) || runningTask(); if (!t || t.terminal) return;
    try { await api(`/v1/tasks/${t.id}/cancel`, { method: 'POST' }); } catch (e) { $('voiceLine').textContent = e.message; }
  }
  async function emergencyStop() {
    try { await api('/v1/estop', { method: 'POST' }); $('voiceLine').textContent = 'Emergency stop sent: all tasks are being stopped.'; }
    catch (e) { $('voiceLine').textContent = e.message; }
  }

  // ------------------------------------------------------------------ voice (browser speech recognition)
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;

  function setupVoice() {
    if (!SR) { $('micBtn').hidden = true; $('voiceLine').textContent = 'Voice input needs Chrome, Edge or Safari. Typing works everywhere.'; return; }
    const rec = new SR();
    rec.lang = navigator.language || 'en-US'; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
    let finalText = '';
    rec.onstart = () => { S.listening = true; finalText = ''; $('micBtn').setAttribute('aria-pressed', 'true'); $('voiceLine').textContent = 'Listening…'; };
    rec.onresult = (e) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) finalText += r[0].transcript; else interim += r[0].transcript;
      }
      $('voiceLine').textContent = '“' + (finalText + interim).trim() + '”';
    };
    rec.onerror = (e) => {
      const msg = { 'not-allowed': 'Microphone access was blocked. Allow it in the browser to use voice.', 'service-not-allowed': 'Speech recognition is not allowed here (it needs a secure https page).',
        'no-speech': 'I did not hear anything.', 'audio-capture': 'No microphone found.', network: 'Speech recognition could not reach its service.' }[e.error] || 'Voice error: ' + e.error;
      $('voiceLine').textContent = msg;
    };
    rec.onend = () => { S.listening = false; $('micBtn').setAttribute('aria-pressed', 'false'); if (finalText.trim()) handleUtterance(finalText); };
    S.rec = rec;
  }

  function toggleMic() {
    if (!S.rec) return;
    cancelCountdown();
    if (S.listening) { S.rec.stop(); return; }
    try { S.rec.start(); } catch { /* already starting */ }
  }

  function handleUtterance(text) {
    const intent = L.interpretVoice(text, { awaitingApproval: !!pendingTask(), taskRunning: !!runningTask() });
    switch (intent.type) {
      case 'approve': $('voiceLine').textContent = 'Approved.'; decide(true); break;
      case 'deny': $('voiceLine').textContent = 'Denied.'; decide(false); break;
      case 'cancel': $('voiceLine').textContent = 'Stopping the task.'; cancelCurrent(); break;
      case 'estop': emergencyStop(); break;
      case 'ignore': $('voiceLine').textContent = intent.reason; break;
      default:
        $('goal').value = intent.text; autoGrow();
        if (S.settings.auto) startCountdown(intent.text); else $('voiceLine').textContent = 'Check the text, then press the arrow.';
    }
  }

  function startCountdown(text) {
    let n = 2;
    const line = $('voiceLine');
    const draw = () => { line.textContent = `Running in ${n}… `; const b = document.createElement('button'); b.type = 'button'; b.className = 'link'; b.textContent = 'Cancel'; b.onclick = cancelCountdown; line.appendChild(b); };
    draw();
    S.countdown = setInterval(() => { n -= 1; if (n <= 0) { clearInterval(S.countdown); S.countdown = null; run($('goal').value || text); } else draw(); }, 1000);
  }
  function cancelCountdown() { if (S.countdown) { clearInterval(S.countdown); S.countdown = null; $('voiceLine').textContent = 'Cancelled. Edit the text or press the arrow.'; } }

  function speak(text) {
    try { speechSynthesis.cancel(); speechSynthesis.speak(new SpeechSynthesisUtterance(String(text).slice(0, 700))); } catch { /* unsupported */ }
  }

  function notify(title, body) {
    if (!S.settings.notify || !('Notification' in window) || Notification.permission !== 'granted' || !document.hidden) return;
    try { new Notification(title, { body, icon: 'icons/icon-192.png' }); } catch { /* ignore */ }
  }

  // ------------------------------------------------------------------ settings menu + install
  function saveSettings() { ls.set('pilot.settings', JSON.stringify(S.settings)); }

  async function loadSkills() {
    const ul = $('skills'); if (!ul) return;
    ul.textContent = '';
    let list = [];
    try { list = await api('/v1/skills'); } catch { return; }
    if (!list.length) { const li = document.createElement('li'); li.className = 'sub'; li.textContent = 'Nothing yet. It learns as you use it.'; ul.appendChild(li); return; }
    list.forEach((s) => {
      const li = document.createElement('li'); li.style.flexWrap = 'wrap';
      const nm = document.createElement('span'); nm.className = 'name'; nm.textContent = s.title;
      const st = document.createElement('span'); st.className = 'sub'; st.textContent = (s.runs.ok || 0) + ' worked';
      const view = document.createElement('button'); view.type = 'button'; view.textContent = 'View';
      const del = document.createElement('button'); del.type = 'button'; del.textContent = 'Forget';
      const pre = document.createElement('pre'); pre.hidden = true; pre.style.cssText = 'width:100%;white-space:pre-wrap;font-size:.78rem;margin:.3rem 0;color:var(--muted)';
      view.onclick = async () => {
        if (!pre.hidden) { pre.hidden = true; return; }
        try { const d = await api('/v1/skills/' + encodeURIComponent(s.id)); pre.textContent = d.text; pre.hidden = false; } catch (e) { alert(e.message); }
      };
      del.onclick = async () => { if (confirm('Forget what Deskhand learned about ' + s.title + '?')) { try { await api('/v1/skills/' + encodeURIComponent(s.id), { method: 'DELETE' }); } catch { /* gone */ } loadSkills(); } };
      li.append(nm, st, view, del, pre); ul.appendChild(li);
    });
  }

  async function loadPrefs() {                               // what the person asked Deskhand to remember
    const ul = $('prefs'); if (!ul) return;
    try {
      const list = await api('/v1/preferences');
      ul.textContent = ''; $('prefsEmpty').hidden = list.length > 0;
      list.forEach((p) => {
        const li = document.createElement('li');
        const nm = document.createElement('span'); nm.className = 'name'; nm.textContent = p.text;
        const del = document.createElement('button'); del.type = 'button'; del.textContent = 'Delete';
        del.onclick = async () => { try { await api('/v1/preferences/' + encodeURIComponent(p.id), { method: 'DELETE' }); } catch { /* gone */ } loadPrefs(); };
        li.append(nm, del); ul.appendChild(li);
      });
    } catch { ul.textContent = ''; }
  }

  async function loadMembers() {
    const ul = $('members');
    try {
      const ws = await api('/v1/workspace');
      $('wsLine').textContent = 'Workspace: ' + ws.name + ' (' + ws.tasks_24h + ' of ' + ws.max_tasks_per_day + ' tasks used in the last 24 hours)';
      const list = await api('/v1/members');
      ul.textContent = '';
      list.forEach((m) => {
        const li = document.createElement('li');
        const nm = document.createElement('span'); nm.className = 'name'; nm.textContent = m.name + (m.you ? ' (this device)' : '');
        li.appendChild(nm);
        if (!m.you) {
          const b = document.createElement('button'); b.type = 'button'; b.textContent = 'Remove';
          b.onclick = async () => { if (confirm('Remove ' + m.name + '? It will need a new code to come back.')) { try { await api('/v1/members/' + m.client_id, { method: 'DELETE' }); } catch { /* gone */ } loadMembers(); } };
          li.appendChild(b);
        }
        ul.appendChild(li);
      });
    } catch { ul.textContent = ''; }
  }

  async function loadUsage() {
    const el = $('usageLine'); if (!el) return;
    try {
      const u = await api('/v1/usage');
      const n = (x) => Number(x || 0).toLocaleString();
      const parts = ['Tasks: ' + u.tasks_24h + ' of ' + u.max_tasks_per_day + ' in the last 24 hours', 'Model calls: ' + n(u.model_calls_today), 'Tokens: ' + n(u.tokens_today) + (u.token_limit ? ' of ' + n(u.token_limit) : '')];
      let line = parts.join(' · ');
      if (u.queue && u.queue.waiting) line += '. Busy right now: ' + u.queue.waiting + ' waiting in line.';
      if (u.paused_for_budget) line += ' Paused: the service has used up its model budget for today and resets at midnight UTC.';
      el.textContent = line;
    } catch { el.textContent = 'Usage is not available.'; }
  }

  function openMenu(section) {
    const menu = $('menu');
    $('optAuto').checked = S.settings.auto; $('optSpeak').checked = S.settings.speak;
    $('optNotify').checked = S.settings.notify && 'Notification' in window && Notification.permission === 'granted';
    $('addController').hidden = true;
    renderComputers(); loadMembers(); loadSkills(); loadUsage(); loadPrefs();
    closeSidebar(); menu.showModal();
    if (section) { const el = $(section); if (el) { if (el.tagName === 'DETAILS') el.open = true; el.scrollIntoView({ block: 'start' }); } }
  }

  function setupMenu() {
    const menu = $('menu');
    $('menuBtn').onclick = () => openMenu();
    $('wsBtn').onclick = () => openMenu();
    $('skillsBtn').onclick = () => openMenu('skillsSec');
    $('sideAddBtn').onclick = () => openConnect();
    $('optAuto').onchange = (e) => { S.settings.auto = e.target.checked; saveSettings(); };
    $('optSpeak').onchange = (e) => { S.settings.speak = e.target.checked; saveSettings(); };
    $('optNotify').onchange = async (e) => {
      if (e.target.checked && 'Notification' in window) { const p = await Notification.requestPermission(); e.target.checked = p === 'granted'; }
      S.settings.notify = e.target.checked; saveSettings();
    };
    $('addControllerBtn').onclick = async () => {
      try { const r = await api('/v1/pair-code', { method: 'POST' }); $('ctrlCode').textContent = r.code; $('addController').hidden = false; }
      catch (e) { alert('Could not create a code: ' + (e.message || e)); }
    };
    $('getCmdBtn').onclick = openConnect;
    $('addLaptopBtn').onclick = openConnect;
    $('connectLater').onclick = closeConnect;
    $('connectNew').onclick = fetchConnectCommand;
    $('connectCopy').onclick = async () => {
      try { await navigator.clipboard.writeText($('connectCmd').textContent); $('connectCopy').textContent = 'Copied. Now paste it into PowerShell.'; }
      catch { /* the command is selectable: copy it by hand */ }
    };
    $('connectDlg').addEventListener('close', () => clearTimeout(connectTimer));
    $('extDone').onclick = closeConnect;
    $('extSkip').onclick = closeConnect;
    $('extBannerClose').onclick = () => { try { localStorage.setItem('pilot.extBanner', '1'); } catch { /* not saved: it just shows again next time */ } $('extBanner').hidden = true; };
    $('estopBtn').onclick = () => { if (confirm('Stop every running task right now?')) { emergencyStop(); menu.close(); } };
    $('unpairBtn').onclick = async () => { if (confirm('Forget this device? You will need a new pairing code.')) { try { await api('/v1/unpair', { method: 'POST' }); } catch { /* already gone */ } menu.close(); forgetDevice(''); } };
    $('installBtn').onclick = async () => { if (S.installEvt) { S.installEvt.prompt(); S.installEvt = null; $('installBtn').hidden = true; } };
    // "add to home screen" only makes sense on a phone or tablet; on a computer the real app is the Windows helper
    const touchDevice = matchMedia('(pointer: coarse)').matches;
    window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); S.installEvt = e; $('installBtn').hidden = !touchDevice; });
    if (/iphone|ipad/i.test(navigator.userAgent)) $('iosHint').hidden = false;
    setupDataRights();
  }

  // ------------------------------------------------------------------ your data: take a copy, or erase it
  function setupDataRights() {
    const say = (text, bad) => { const el = $('dataMsg'); el.textContent = text; el.className = bad ? 'err' : 'hint'; };
    $('exportBtn').onclick = async () => {
      say('Preparing your data…');
      try {
        const r = await fetch('/v1/export', { headers: { Authorization: 'Bearer ' + S.token } });
        if (r.status === 401) { forgetDevice('You were signed out of this device. Please sign in again.'); return; }
        if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(typeof d.detail === 'string' ? d.detail : 'could not download your data'); }
        const a = document.createElement('a');
        a.href = URL.createObjectURL(await r.blob());
        a.download = 'deskhand-data-' + new Date().toISOString().slice(0, 10) + '.json';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 4000);
        say('Your data was downloaded as a file.');
      } catch (e) { say(e.message || 'could not download your data', true); }
    };

    const dlg = $('deleteDlg'), box = $('deleteConfirm');
    const typed = () => box.value.toLowerCase().split(/\s+/).filter(Boolean).join(' ');       // the server checks the same way
    const typedOk = () => typed() === 'delete my data';
    $('deleteBtn').onclick = () => { box.value = ''; $('deleteGo').disabled = true; $('deleteErr').textContent = ''; dlg.showModal(); box.focus(); };
    box.addEventListener('input', () => { $('deleteGo').disabled = !typedOk(); });
    $('deleteCancel').onclick = () => dlg.close();
    $('deleteForm').addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!typedOk()) return;
      $('deleteGo').disabled = true; $('deleteErr').textContent = '';
      try {
        await api('/v1/delete-account', { method: 'POST', body: { confirm: typed() } });
        dlg.close(); $('menu').close();
        forgetDevice('Your account and data were deleted.');
      } catch (err) {
        if (err.message !== 'unauthorized') { $('deleteErr').textContent = err.message || 'could not delete your data'; $('deleteGo').disabled = false; }
      }
    });
  }

  // ------------------------------------------------------------------ composer
  function autoGrow() {
    const g = $('goal');
    g.style.height = 'auto';
    g.style.height = Math.min(g.scrollHeight, 160) + 'px';
  }

  function setupComposer() {
    $('composer').addEventListener('submit', (e) => { e.preventDefault(); run($('goal').value); });
    $('goal').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); run($('goal').value); }   // Enter sends, Shift+Enter is a new line
    });
    $('goal').addEventListener('input', () => { cancelCountdown(); autoGrow(); });
    $('targetBtn').onclick = () => { const pop = $('targetPop'); pop.hidden = !pop.hidden; $('targetBtn').setAttribute('aria-expanded', String(!pop.hidden)); };
    document.addEventListener('click', (e) => {
      if (!$('targetPop').hidden && !e.target.closest('.target-wrap')) { $('targetPop').hidden = true; $('targetBtn').setAttribute('aria-expanded', 'false'); }
    });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { $('targetPop').hidden = true; closeSidebar(); } });
  }

  // ------------------------------------------------------------------ boot
  function wire() {
    $('pairForm').addEventListener('submit', pair);
    $('code').addEventListener('input', (e) => { e.target.value = L.prettyCode(e.target.value); });
    $('devName').value = thisDeviceName();
    setupComposer();
    $('micBtn').addEventListener('click', toggleMic);
    $('answerForm').addEventListener('submit', (e) => {
      e.preventDefault();
      const t = pendingTask(), v = $('answerText').value.trim();
      if (t && v) answer(t.id, { text: v }, v);
    });
    $('approveBtn').onclick = () => decide(true);
    $('denyBtn').onclick = () => decide(false);
    $('stopBtn').onclick = cancelCurrent;
    $('deleteChatBtn').onclick = () => { if (S.currentId) deleteChat(S.currentId); };
    $('deleteChatsBtn').onclick = deleteAllChats;
    $('newChatBtn').onclick = newTask;
    $('sideToggle').onclick = () => ($('appView').classList.contains('open') ? closeSidebar() : openSidebar());
    $('scrim').onclick = closeSidebar;
    $('copyBtn').onclick = async () => { try { await navigator.clipboard.writeText($('result').textContent); $('copyBtn').textContent = 'Copied'; setTimeout(() => ($('copyBtn').textContent = 'Copy'), 1500); } catch { /* blocked */ } };
    setInterval(() => { const t = S.tasks.get(S.currentId); if (t && !t.terminal) $('elapsed').textContent = 'Working for ' + L.elapsedText(t.created_at, null); }, 1000);
    setupVoice(); setupMenu();
    $('themeAuto').onclick = () => setTheme('auto');
    $('themeLight').onclick = () => setTheme('light');
    $('themeDark').onclick = () => setTheme('dark');
    applyTheme(S.settings.theme);
    window.addEventListener('deskhand:toggle-theme', () => setTheme(effectiveTheme() === 'dark' ? 'light' : 'dark'));   // the orb's easter egg
  }

  wire();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => { /* not installable here: still works */ });
  if (S.token) start(); else { show('pair'); setupLogin(); }
})();
