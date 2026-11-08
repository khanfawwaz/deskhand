/* Pure helpers for the Deskhand app (no DOM): easy to test with `node --test`. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PilotLogic = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const norm = (s) => String(s || '').toLowerCase().replace(/[^\p{L}\p{N}\s']/gu, ' ').replace(/\s+/g, ' ').trim();

  const APPROVE = new Set(['approve', 'approved', 'yes', 'yeah', 'yep', 'go ahead', 'do it', 'confirm', 'send it',
    'ok', 'okay', 'sure', 'allow', 'allow it', 'yes please', 'approve it']);
  const DENY = new Set(['deny', 'denied', 'no', 'nope', 'dont', "don't", 'do not', 'reject', 'not now', 'stop',
    'cancel', 'no thanks', 'dont do it', "don't do it", 'decline']);
  const CANCEL = new Set(['stop', 'cancel', 'cancel that', 'stop that', 'abort', 'never mind', 'nevermind', 'stop it']);
  const ESTOP = new Set(['emergency stop', 'stop everything', 'kill switch', 'panic', 'emergency']);

  /** What did the user mean? ctx = {awaitingApproval, taskRunning}. Safety words always win. */
  function interpretVoice(text, ctx) {
    ctx = ctx || {};
    const n = norm(text);
    if (!n) return { type: 'ignore', reason: 'I did not catch that.' };
    if (ESTOP.has(n)) return { type: 'estop' };
    if (ctx.awaitingApproval) {
      if (APPROVE.has(n)) return { type: 'approve' };
      if (DENY.has(n)) return { type: 'deny' };
    }
    if (ctx.taskRunning && CANCEL.has(n)) return { type: 'cancel' };
    if (APPROVE.has(n) || DENY.has(n)) return { type: 'ignore', reason: 'Nothing is waiting for an answer.' };
    return { type: 'task', text: tidyGoal(text) };
  }

  function tidyGoal(text) {
    const t = String(text || '').replace(/\s+/g, ' ').trim();
    return t ? t.charAt(0).toUpperCase() + t.slice(1) : '';
  }

  // [what happened, what Pilot is about to do]
  const VERBS = {
    'browser.open_url': ['Opened a page', 'open a page'], 'browser.open_private': ['Opened a private page', 'open a private page'],
    'ui.snapshot': ['Looked at the page', 'look at the page'], 'ui.click': ['Clicked', 'click'], 'ui.type': ['Typed', 'type'],
    'ui.read': ['Read a value', 'read a value'], 'ui.read_page': ['Read the page', 'read the page'],
    'ui.wait': ['Waited for the page', 'wait for the page'], 'screen.zoom': ['Zoomed in', 'zoom in'],
    'capture_screen': ['Looked at the screen', 'look at the screen'], 'windows.focus': ['Brought a window forward', 'bring a window forward'],
    'windows.list': ['Checked open windows', 'check open windows'], 'get_active_window': ['Checked the active window', 'check the active window'],
    'launch_application': ['Opened an app', 'open an app'], 'click': ['Clicked', 'click'], 'double_click': ['Double-clicked', 'double-click'],
    'type_text': ['Typed', 'type'], 'press_key': ['Pressed a key', 'press a key'], 'hotkey': ['Pressed a shortcut', 'press a shortcut'],
    'scroll': ['Scrolled', 'scroll'], 'wait': ['Waited', 'wait'], 'run_command': ['Ran a command', 'run a command'],
    'files.read': ['Read a file', 'read a file'], 'files.write': ['Wrote a file', 'write a file'], 'files.list': ['Listed a folder', 'list a folder'],
    'clipboard.set': ['Copied text', 'copy text'], 'clipboard.get': ['Read the clipboard', 'read the clipboard'],
    'invalid': ['Rethought a step', 'rethink a step'],
  };
  const PENDING = new Set(['pending_confirmation']);
  const WORKING = new Set(['dispatched', 'proposed']);

  /** One human line + a state for a step {seq, tool, status, note}. */
  function describeStep(step) {
    const isRecipe = step.tool.startsWith('recipe.');
    const [past, now] = isRecipe ? ['Ran saved routine ' + step.tool.slice(7), 'run saved routine ' + step.tool.slice(7)]
      : (VERBS[step.tool] || [step.tool, step.tool]);
    if (PENDING.has(step.status)) return { text: 'Waiting for your OK to ' + now, state: 'working', note: '' };
    if (step.status === 'cancelled') return { text: 'Did not ' + now + ' (not approved)', state: 'blocked', note: '' };
    if (WORKING.has(step.status)) return { text: now.charAt(0).toUpperCase() + now.slice(1) + '…', state: 'working', note: '' };
    const state = step.status === 'denied' ? 'blocked' : (step.status === 'failed' || step.status === 'uncertain') ? 'problem' : 'ok';
    return { text: past, state, note: step.note || '' };
  }

  const LABELS = {
    queued: 'Waiting to start', running: 'Working', waiting_for_action: 'Thinking', verifying: 'Checking',
    waiting_for_confirmation: 'Needs your OK', waiting_for_device: 'Waiting for the laptop', completed: 'Done',
    failed: 'Could not finish', cancelled: 'Stopped',
  };
  const statusLabel = (status) => LABELS[status] || status;
  const statusTone = (status) => ({ completed: 'good', failed: 'bad', cancelled: 'muted', waiting_for_confirmation: 'warn',
    waiting_for_device: 'warn' }[status] || 'busy');

  function elapsedText(startSec, endSec) {
    const s = Math.max(0, Math.round((endSec || Date.now() / 1000) - startSec));
    return s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + (s % 60) + 's';
  }

  /** Plain-English line for an approval card: "Pilot wants to click "Send". This may be irreversible (...)". */
  function approvalText(p) {
    if (!p) return '';
    if (p.question) return p.question + (p.reason ? ' (' + p.reason + ')' : '');
    const args = (p.params && p.params.args) || p.params || {};
    const target = args.name || args.url || args.title || args.key || '';
    const verb = (VERBS[p.tool] || [0, p.tool || 'do something'])[1];
    const reason = String(p.reason || '').replace(/^'[^']*'\s*/, 'This ').replace(/^policy requires approval for .*/, '');
    return 'Deskhand wants to ' + verb + (target ? ' "' + target + '"' : '') + '.' + (reason ? ' ' + reason.charAt(0).toUpperCase() + reason.slice(1) + '.' : '');
  }

  /** Server errors look like "cannot_proceed: <reason>": show only the reason. */
  const friendlyError = (e) => String(e || '').replace(/^(cannot_proceed|need_more_information|need_confirmation):\s*/, '');

  const normalizeCode = (s) => String(s || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  const prettyCode = (s) => { const c = normalizeCode(s).slice(0, 8); return c.length > 4 ? c.slice(0, 4) + '-' + c.slice(4) : c; };

  return { norm, interpretVoice, tidyGoal, describeStep, statusLabel, statusTone, elapsedText, approvalText,
    normalizeCode, prettyCode, friendlyError };
});
