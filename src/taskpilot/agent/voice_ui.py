"""The voice prompt's page, kept as strings so it travels with the helper's code (the helper update only ships .py files).

It is a small page the Deskhand helper serves on 127.0.0.1. A browser app window opens it when the shortcut is pressed; it listens
with the browser's own speech recognition, sends the sentence to the helper, and shows the task as it runs: working, a question to
answer with buttons, and the result.
"""

HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deskhand voice</title>
<link rel="stylesheet" href="/voice/voice.css">
</head>
<body>
<main id="card">
  <header class="top">
    <div id="orb" class="orb" data-state="idle" data-talk="0" aria-hidden="true"></div>
    <div class="head"><strong id="state">Starting…</strong><span id="hint" class="hint"></span></div>
    <button id="closeBtn" class="x" type="button" aria-label="Close">&times;</button>
  </header>

  <textarea id="said" rows="2" maxlength="1000" placeholder="Say it, or type it here" aria-label="Your command"></textarea>

  <section id="ask" class="ask" hidden>
    <p id="askText" class="ask-text"></p>
    <div id="choices" class="choices"></div>
    <form id="askForm" class="askform" autocomplete="off" hidden>
      <input id="askAnswer" maxlength="500" placeholder="Something else…" aria-label="Your own answer">
      <button class="go" type="submit" aria-label="Send answer">&rarr;</button>
    </form>
    <div id="approveRow" class="row" hidden>
      <button id="denyBtn" class="ghost" type="button">Deny</button>
      <button id="approveBtn" class="primary" type="button">Approve</button>
    </div>
  </section>

  <section id="result" class="result" hidden><pre id="resultText"></pre></section>

  <footer class="row">
    <button id="micBtn" class="ghost" type="button" aria-pressed="false">Mic</button>
    <span class="sp"></span>
    <button id="stopBtn" class="ghost" type="button" hidden>Stop</button>
    <button id="sendBtn" class="primary" type="button">Send</button>
  </footer>
</main>
<script src="/voice/voice.js"></script>
</body>
</html>
"""

CSS = """
:root { color-scheme: light dark; --bg:#ffffff; --surface:#f3f4f3; --line:#dce1de; --text:#12241d; --muted:#6a7a72; --ink:#12241d; --ink-on:#fafafa;
  --leaf:#1BC237; --mint:#6ED987; --bad:#b4492e; --font: system-ui,"Segoe UI",Roboto,sans-serif; }
@media (prefers-color-scheme: dark) { :root { --bg:#141917; --surface:#1d2420; --line:#2b342f; --text:#e9eeeb; --muted:#97a69d; --ink:#e9eeeb; --ink-on:#0f1311; } }
* { box-sizing: border-box; }
html, body { margin:0; height:100%; background:var(--bg); color:var(--text); font:15px/1.45 var(--font); overflow:hidden; }
#card { height:100%; display:flex; flex-direction:column; gap:.7rem; padding:.9rem 1rem 1rem; }
.top { display:flex; align-items:center; gap:.8rem; }
.head { flex:1; min-width:0; display:grid; line-height:1.25; }
.head strong { font-size:1.05rem; } .hint { color:var(--muted); font-size:.82rem; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.x { background:none; border:0; color:var(--muted); font-size:1.5rem; line-height:1; cursor:pointer; padding:.1rem .4rem; border-radius:8px; }
.x:hover { background:var(--surface); }
button { font:inherit; color:inherit; cursor:pointer; }
:focus-visible { outline:2px solid var(--ink); outline-offset:2px; }
textarea, input { width:100%; background:var(--surface); color:var(--text); border:1px solid var(--line); border-radius:12px; padding:.65rem .8rem; font:inherit; resize:none; }
textarea { flex:none; min-height:3.6rem; font-size:1.05rem; }
.row { display:flex; gap:.6rem; align-items:center; } .sp { flex:1; }
.primary { background:var(--ink); color:var(--ink-on); border:0; border-radius:12px; padding:.55rem 1.1rem; font-weight:600; }
.ghost { background:transparent; border:1px solid var(--line); border-radius:12px; padding:.5rem .9rem; }
.ghost[aria-pressed="true"] { background:var(--ink); color:var(--ink-on); border-color:transparent; }
.ask { display:grid; gap:.5rem; } .ask-text { margin:0; font-weight:600; overflow-wrap:anywhere; }
.choices { display:grid; gap:.4rem; } .choices button { text-align:left; background:var(--bg); border:1px solid var(--line); border-radius:12px; padding:.55rem .8rem; }
.choices button:hover { background:var(--surface); }
.askform { display:flex; gap:.5rem; } .go { background:var(--ink); color:var(--ink-on); border:0; border-radius:12px; width:2.6rem; }
.result { flex:1; min-height:0; overflow:auto; background:var(--surface); border-radius:12px; padding:.6rem .8rem; }
.result pre { margin:0; white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; }
[hidden] { display:none !important; }

/* the orb: still when idle, breathing while it listens, swelling while you speak, shimmering while it works */
.orb { width:44px; height:44px; border-radius:50%; flex:none; background:radial-gradient(circle at 35% 30%, #eafff0 0, var(--mint) 38%, var(--leaf) 72%, #0b7a26 100%);
  box-shadow:0 0 0 0 rgba(27,194,55,.35); transition:transform .15s, filter .3s; }
.orb[data-state="listening"] { animation:breathe 1.8s ease-in-out infinite; }
.orb[data-state="listening"][data-talk="1"] { animation:talk .45s ease-in-out infinite; }
.orb[data-state="working"] { animation:work 1.4s linear infinite; }
.orb[data-state="error"] { filter:hue-rotate(-100deg) saturate(1.2); }
.orb[data-state="done"] { filter:saturate(1.1); }
@keyframes breathe { 0%,100% { transform:scale(1); box-shadow:0 0 0 0 rgba(27,194,55,.30); } 50% { transform:scale(1.08); box-shadow:0 0 0 12px rgba(27,194,55,0); } }
@keyframes talk { 0%,100% { transform:scale(1.04); box-shadow:0 0 0 0 rgba(27,194,55,.5); } 50% { transform:scale(1.22); box-shadow:0 0 0 16px rgba(27,194,55,0); } }
@keyframes work { 0% { transform:scale(1) rotate(0); border-radius:50%; } 50% { transform:scale(.94,1.06) rotate(180deg); border-radius:46% 54% 52% 48%; } 100% { transform:scale(1) rotate(360deg); border-radius:50%; } }
@media (prefers-reduced-motion: reduce) { .orb { animation:none !important; } }
"""

JS = """
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const SECRET = decodeURIComponent((location.hash || '').slice(1));
  const LABEL = { queued: 'Starting', running: 'Working', waiting_for_action: 'Thinking', verifying: 'Checking',
    waiting_for_confirmation: 'Needs your OK', waiting_for_device: 'Waiting for this computer', completed: 'Done',
    failed: 'Could not finish', cancelled: 'Stopped' };
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const SEND_DELAY_MS = 1400;
  let rec = null, listening = false, autoTimer = null, poll = null, tid = null, sent = false, finalText = '', closeTimer = null, shownAsk = '';

  async function api(path, body) {
    const r = await fetch(path, { method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
      headers: { 'X-Deskhand-Local': SECRET, 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || r.statusText);
    return d;
  }
  function setState(state, text, hint) {
    $('orb').dataset.state = state; $('state').textContent = text;
    if (hint !== undefined) $('hint').textContent = hint;
  }
  function cancelAuto() { if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; } }

  // ---------------------------------------------------------------- listening
  function startListening() {
    if (sent) return;
    cancelAuto(); finalText = ''; $('said').value = '';
    if (!SR) { setState('idle', 'Type your command', 'Speech recognition is not available here. Press Enter to send.'); $('said').focus(); return; }
    try { if (rec) rec.abort(); } catch (e) { /* none running */ }
    rec = new SR();
    rec.lang = navigator.language || 'en-US'; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
    rec.onstart = () => { listening = true; $('micBtn').setAttribute('aria-pressed', 'true'); setState('listening', 'Listening…', 'Say what you want done. Enter sends, Esc closes.'); };
    rec.onspeechstart = () => { $('orb').dataset.talk = '1'; };
    rec.onspeechend = () => { $('orb').dataset.talk = '0'; };
    rec.onresult = (e) => {
      let interim = '', fin = '';
      for (let i = e.resultIndex; i < e.results.length; i++) { const r = e.results[i]; if (r.isFinal) fin += r[0].transcript; else interim += r[0].transcript; }
      if (fin) finalText = (finalText + ' ' + fin).trim();
      $('said').value = (finalText + ' ' + interim).trim();
    };
    rec.onerror = (e) => {
      const m = { 'not-allowed': 'The microphone is blocked. Type your command instead.', 'service-not-allowed': 'Speech recognition is not allowed here. Type your command instead.',
        'no-speech': 'I did not hear anything. Press Mic to try again.', 'audio-capture': 'No microphone found. Type your command instead.',
        network: 'Speech recognition could not reach its service. Type your command instead.' }[e.error];
      if (m) setState('idle', 'Not listening', m);
    };
    rec.onend = () => {
      listening = false; $('orb').dataset.talk = '0'; $('micBtn').setAttribute('aria-pressed', 'false');
      if (sent) return;
      if ($('said').value.trim()) {
        setState('listening', 'Sending…', 'Edit the text to change it, Esc to cancel.');
        cancelAuto(); autoTimer = setTimeout(send, SEND_DELAY_MS);
      }
    };
    try { rec.start(); } catch (e) { /* already starting */ }
  }

  // ---------------------------------------------------------------- sending and following the task
  async function send() {
    cancelAuto();
    const goal = $('said').value.trim();
    if (!goal || sent) return;
    sent = true;
    try { if (rec) rec.abort(); } catch (e) { /* fine */ }
    $('said').readOnly = true; $('sendBtn').hidden = true; $('micBtn').hidden = true; $('stopBtn').hidden = false;
    setState('working', 'Sending…', goal.slice(0, 90));
    try {
      const r = await api('/voice/submit', { goal });
      tid = r.task_id;
      poll = setInterval(check, 1000); check();
    } catch (e) { fail(e.message); }
  }
  function fail(message) {
    sent = false; clearInterval(poll); $('said').readOnly = false; $('sendBtn').hidden = false; $('micBtn').hidden = false; $('stopBtn').hidden = true;
    setState('error', 'Could not send', message);
  }
  function showAsk(p) {
    const sig = JSON.stringify(p);
    $('ask').hidden = false;
    if (sig === shownAsk) return;
    shownAsk = sig;
    const choice = p.kind === 'access' || p.kind === 'question';
    $('askText').textContent = p.question || p.reason || 'Deskhand needs your OK.';
    const box = $('choices'); box.textContent = '';
    (p.options || []).forEach((o) => { const b = document.createElement('button'); b.type = 'button'; b.textContent = o.label; b.onclick = () => answer({ choice: o.id }); box.appendChild(b); });
    $('askForm').hidden = !(choice && p.allow_text);
    $('approveRow').hidden = choice;
    setState('working', p.kind === 'question' ? 'Deskhand has a question' : 'Needs your OK', '');
  }
  async function answer(body) { try { await api('/voice/answer', { tid, ...body }); $('ask').hidden = true; shownAsk = ''; } catch (e) { $('hint').textContent = e.message; } }
  async function check() {
    try {
      const t = await api('/voice/status?tid=' + encodeURIComponent(tid));
      if (t.pending_approval) { showAsk(t.pending_approval); return; }
      $('ask').hidden = true; shownAsk = '';
      if (t.terminal) {
        clearInterval(poll); $('stopBtn').hidden = true;
        const ok = t.status === 'completed';
        setState(ok ? 'done' : 'error', ok ? 'Done' : (LABEL[t.status] || 'Stopped'), t.goal.slice(0, 90));
        const text = t.result || t.error || '';
        $('result').hidden = !text; $('resultText').textContent = text;
        closeTimer = setTimeout(() => window.close(), ok && text.length < 200 ? 9000 : 25000);   // linger long enough to read it
      } else {
        setState('working', LABEL[t.status] || 'Working', t.goal.slice(0, 90));
      }
    } catch (e) { /* the helper may be restarting: keep trying */ }
  }

  // ---------------------------------------------------------------- the controls
  $('sendBtn').onclick = send;
  $('micBtn').onclick = () => { if (listening) { try { rec.stop(); } catch (e) { /* ok */ } } else startListening(); };
  $('stopBtn').onclick = async () => { try { await api('/voice/cancel', { tid }); } catch (e) { /* already over */ } };
  $('closeBtn').onclick = () => window.close();
  $('approveBtn').onclick = async () => { try { await api('/voice/approve', { tid, approved: true }); $('ask').hidden = true; shownAsk = ''; } catch (e) { $('hint').textContent = e.message; } };
  $('denyBtn').onclick = async () => { try { await api('/voice/approve', { tid, approved: false }); $('ask').hidden = true; shownAsk = ''; } catch (e) { $('hint').textContent = e.message; } };
  $('askForm').addEventListener('submit', (e) => { e.preventDefault(); const v = $('askAnswer').value.trim(); if (v) { answer({ text: v }); $('askAnswer').value = ''; } });
  $('said').addEventListener('input', () => { cancelAuto(); if (!sent) setState('idle', 'Ready', 'Press Enter to send, Esc to close.'); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { if (sent && !$('stopBtn').hidden) return; window.close(); }
    else if (e.key === 'Enter' && !e.shiftKey && document.activeElement && document.activeElement.id === 'said') { e.preventDefault(); send(); }
  });
  // pressing the shortcut again brings this window forward: listen again
  window.addEventListener('focus', () => { if (!sent && !listening) startListening(); if (closeTimer) { clearTimeout(closeTimer); closeTimer = null; } });
  window.focus(); startListening();
})();
"""
