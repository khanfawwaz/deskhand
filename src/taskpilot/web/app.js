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
    token: ls.get('pilot.token'), me: null, devices: [], tasks: new Map(), currentId: null,
    targets: new Set(JSON.parse(ls.get('pilot.targets', '[]') || '[]')), listening: false, rec: null, countdown: null, installEvt: null, seenTerminal: new Set(),
    settings: Object.assign({ auto: true, speak: false, notify: false }, JSON.parse(ls.get('pilot.settings', '{}') || '{}')),
  };

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
    $('menuBtn').hidden = view !== 'app';
  }
  function setConn(state) { const el = $('conn'); el.dataset.state = state; el.textContent = state; }

  function forgetDevice(message) {
    S.token = null; ls.del('pilot.token'); S.tasks.clear(); S.currentId = null;
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
  function deviceName() { return (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || 'My device'; }

  async function finishLogin(r) { S.token = r.token; ls.set('pilot.token', r.token); history.replaceState(null, '', location.pathname); await start(); }

  async function verifyCode(code) {
    $('emailErr').textContent = '';
    try { await finishLogin(await api('/v1/auth/verify', { method: 'POST', body: { code: L.normalizeCode(code), name: deviceName() } })); }
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
    S.devices = S.me.devices; renderDevices();
    show('app');
    $('whoami').textContent = 'Signed in as ' + S.me.name + ' on this device.';
    const list = await api('/v1/tasks?limit=20').catch(() => []);
    list.forEach((t) => { S.tasks.set(t.id, t); if (t.terminal) S.seenTerminal.add(t.id); });
    const active = list.find((t) => !t.terminal);
    if (active) selectTask(active.id);
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
    if (!chosen.length) note.textContent = 'Tap a computer to choose where this runs. Pick several to send the same command to all of them.';
    else if (off.length) note.textContent = off.map((d) => d.name).join(', ') + (off.length > 1 ? ' are' : ' is') + ' offline. On that computer open Deskhand from the Start menu (or press Ctrl+Alt+P) to bring it online.';
    else note.textContent = chosen.length > 1 ? 'Sending to ' + chosen.length + ' computers at once.' : '';
  }

  function renderDevices() {
    const none = !S.devices.length;
    $('setup').hidden = !none; $('composer').hidden = none;
    if (none) { S.hadDevices = false; return; }
    // forget chosen computers that no longer exist; default to the first online one
    for (const id of [...S.targets]) if (!S.devices.some((d) => d.device_id === id)) S.targets.delete(id);
    if (!S.targets.size) { const pick = S.devices.find((d) => d.online) || S.devices[0]; S.targets.add(pick.device_id); }
    saveTargets(); renderTargets();
    if (S.hadDevices === false) toast(S.devices[0].name + ' is connected. Try a command below.');
    S.hadDevices = true;
    const ex = $('examples');
    if (!ex.children.length) EXAMPLES.forEach((t) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = t; b.onclick = () => { $('goal').value = t; $('goal').focus(); }; ex.appendChild(b); });
    renderComputers();
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
  function selectTask(id) { S.currentId = id; renderCurrent(); }

  function renderCurrent() {
    const t = S.tasks.get(S.currentId);
    $('current').hidden = !t;
    if (!t) return;
    const chip = $('chip');
    chip.textContent = L.statusLabel(t.status); chip.dataset.tone = L.statusTone(t.status);
    $('curGoal').textContent = t.goal;
    $('elapsed').textContent = L.elapsedText(t.created_at, t.terminal ? t.updated_at : null);
    const ol = $('steps'); ol.innerHTML = '';
    (t.steps || []).forEach((st) => {
      const d = L.describeStep(st);
      const li = document.createElement('li'); li.dataset.state = d.state;
      const span = document.createElement('span'); span.textContent = d.text;
      li.appendChild(span);
      if (d.note && d.state !== 'ok') { const n = document.createElement('span'); n.className = 'note'; n.textContent = d.note; li.appendChild(n); }
      ol.appendChild(li);
    });
    $('resultBox').hidden = !t.result;
    $('result').textContent = t.result || '';
    const err = $('errorLine'); err.hidden = !(t.error && t.terminal); err.textContent = L.friendlyError(t.error);
    $('stopBtn').hidden = t.terminal;
  }

  function renderHistory() {
    const ul = $('history'); ul.innerHTML = '';
    const items = [...S.tasks.values()].sort((a, b) => b.created_at - a.created_at).slice(0, 15);
    $('emptyHistory').hidden = items.length > 0;
    items.forEach((t) => {
      const li = document.createElement('li'), b = document.createElement('button'); b.type = 'button';
      const g = document.createElement('span'); g.className = 'g'; g.textContent = t.goal;
      if (S.devices.length > 1 && t.device_id) { const tg = document.createElement('span'); tg.className = 'tag'; tg.textContent = 'on ' + deviceName(t.device_id); g.appendChild(tg); }
      const c = document.createElement('span'); c.className = 'chip'; c.textContent = L.statusLabel(t.status); c.dataset.tone = L.statusTone(t.status);
      b.append(g, c); b.onclick = () => { selectTask(t.id); window.scrollTo({ top: 0, behavior: 'smooth' }); };
      li.appendChild(b); ul.appendChild(li);
    });
  }

  function pendingTask() { return [...S.tasks.values()].find((t) => !t.terminal && t.pending_approval) || null; }
  function runningTask() { return [...S.tasks.values()].find((t) => !t.terminal) || null; }

  function renderApproval() {
    const t = pendingTask();
    $('approval').hidden = !t;
    if (!t) { S.choiceSig = ''; return; }
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
        if (i === 0) b.className = 'primary';
        if (o.id === 'no') b.className = 'danger';
        b.onclick = () => answer(t.id, { choice: o.id });
        box.appendChild(b);
      });
      $('answerText').value = '';
    }
  }

  async function answer(taskId, body) {
    try { await api('/v1/tasks/' + taskId + '/answer', { method: 'POST', body }); }
    catch (e) { $('voiceLine').textContent = e.message; }
  }

  // ------------------------------------------------------------------ actions
  async function run(text) {
    const goal = text.trim();
    if (!goal) return;
    cancelCountdown();
    $('sendBtn').disabled = true;
    try {
      const chosen = S.devices.filter((d) => S.targets.has(d.device_id));
      const live = chosen.filter((d) => d.online), skipped = chosen.filter((d) => !d.online);
      if (!live.length) { $('voiceLine').textContent = chosen.length ? deviceName(chosen[0].device_id) + ' is offline: open Deskhand on it first.' : 'Choose a computer to run this on.'; $('sendBtn').disabled = false; return; }
      let first = null;
      const failed = [];
      for (const d of live) {
        try {
          const r = await api('/v1/tasks', { method: 'POST', body: { goal, device_id: d.device_id } });
          S.tasks.set(r.task_id, await api('/v1/tasks/' + r.task_id));
          if (!first) first = r.task_id;
        } catch (e) { failed.push(d.name + ': ' + e.message); }
      }
      if (first) { $('goal').value = ''; $('voiceLine').textContent = ''; S.currentId = first; renderCurrent(); renderHistory(); }
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
        $('goal').value = intent.text;
        if (S.settings.auto) startCountdown(intent.text); else $('voiceLine').textContent = 'Check the text, then tap Run.';
    }
  }

  function startCountdown(text) {
    let n = 2;
    const line = $('voiceLine');
    const draw = () => { line.textContent = `Running in ${n}… `; const b = document.createElement('button'); b.type = 'button'; b.className = 'link'; b.textContent = 'Cancel'; b.onclick = cancelCountdown; line.appendChild(b); };
    draw();
    S.countdown = setInterval(() => { n -= 1; if (n <= 0) { clearInterval(S.countdown); S.countdown = null; run($('goal').value || text); } else draw(); }, 1000);
  }
  function cancelCountdown() { if (S.countdown) { clearInterval(S.countdown); S.countdown = null; $('voiceLine').textContent = 'Cancelled. Edit the text or tap Run.'; } }

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

  function setupMenu() {
    const menu = $('menu');
    $('menuBtn').onclick = () => { $('optAuto').checked = S.settings.auto; $('optSpeak').checked = S.settings.speak; $('optNotify').checked = S.settings.notify && Notification.permission === 'granted'; $('addController').hidden = true; $('addLaptop').hidden = true; renderComputers(); loadMembers(); loadSkills(); menu.showModal(); };
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
    $('getCmdBtn').onclick = async () => {
      $('setupErr').textContent = '';
      try { const r = await api('/v1/add-device', { method: 'POST' }); $('setupCmdText').textContent = r.command; $('setupCmd').hidden = false; $('getCmdBtn').hidden = true; }
      catch (e) { $('setupErr').textContent = e.message; }
    };
    $('setupCopy').onclick = async () => { try { await navigator.clipboard.writeText($('setupCmdText').textContent); $('setupCopy').textContent = 'Copied. Now paste it into PowerShell.'; } catch { /* select manually */ } };
    $('addLaptopBtn').onclick = async () => {
      try {
        const r = await api('/v1/add-device', { method: 'POST' });
        $('addCmd').textContent = r.command; $('addLaptop').hidden = false; $('copyCmd').textContent = 'Copy';
      } catch (e) { alert('Could not create a code: ' + (e.message || e)); }
    };
    $('copyCmd').onclick = async () => { try { await navigator.clipboard.writeText($('addCmd').textContent); $('copyCmd').textContent = 'Copied'; } catch { /* select manually */ } };
    $('estopBtn').onclick = () => { if (confirm('Stop every running task right now?')) { emergencyStop(); menu.close(); } };
    $('unpairBtn').onclick = async () => { if (confirm('Forget this device? You will need a new pairing code.')) { try { await api('/v1/unpair', { method: 'POST' }); } catch { /* already gone */ } menu.close(); forgetDevice(''); } };
    $('installBtn').onclick = async () => { if (S.installEvt) { S.installEvt.prompt(); S.installEvt = null; $('installBtn').hidden = true; } };
    window.addEventListener('beforeinstallprompt', (e) => { e.preventDefault(); S.installEvt = e; $('installBtn').hidden = false; });
    if (/iphone|ipad/i.test(navigator.userAgent)) $('iosHint').hidden = false;
  }

  // ------------------------------------------------------------------ boot
  function wire() {
    $('pairForm').addEventListener('submit', pair);
    $('code').addEventListener('input', (e) => { e.target.value = L.prettyCode(e.target.value); });
    $('devName').value = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || 'My device';
    $('composer').addEventListener('submit', (e) => { e.preventDefault(); run($('goal').value); });
    $('goal').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run($('goal').value); } });
    $('goal').addEventListener('input', cancelCountdown);
    $('micBtn').addEventListener('click', toggleMic);
    $('answerForm').addEventListener('submit', (e) => {
      e.preventDefault();
      const t = pendingTask(), v = $('answerText').value.trim();
      if (t && v) answer(t.id, { text: v });
    });
    $('approveBtn').onclick = () => decide(true);
    $('denyBtn').onclick = () => decide(false);
    $('stopBtn').onclick = cancelCurrent;
    $('copyBtn').onclick = async () => { try { await navigator.clipboard.writeText($('result').textContent); $('copyBtn').textContent = 'Copied'; setTimeout(() => ($('copyBtn').textContent = 'Copy'), 1500); } catch { /* blocked */ } };
    setInterval(() => { const t = S.tasks.get(S.currentId); if (t && !t.terminal) $('elapsed').textContent = L.elapsedText(t.created_at, null); }, 1000);
    setupVoice(); setupMenu();
  }

  wire();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => { /* not installable here: still works */ });
  if (S.token) start(); else { show('pair'); setupLogin(); }
})();
