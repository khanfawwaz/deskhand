import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const L = createRequire(import.meta.url)('../../src/taskpilot/web/logic.js');

test('voice: approval words only count while something is waiting', () => {
  for (const w of ['approve', 'Yes!', 'go ahead', 'Okay.', 'send it']) {
    assert.deepEqual(L.interpretVoice(w, { awaitingApproval: true }), { type: 'approve' }, w);
  }
  for (const w of ['deny', 'No', "don't do it", 'not now', 'stop']) {
    assert.deepEqual(L.interpretVoice(w, { awaitingApproval: true }), { type: 'deny' }, w);
  }
  const idle = L.interpretVoice('yes', { awaitingApproval: false });
  assert.equal(idle.type, 'ignore');                              // a stray "yes" must never start a task
  assert.equal(L.interpretVoice('no', {}).type, 'ignore');
});

test('voice: stop words cancel, emergency words always win', () => {
  assert.deepEqual(L.interpretVoice('cancel that', { taskRunning: true }), { type: 'cancel' });
  assert.deepEqual(L.interpretVoice('Stop everything!', {}), { type: 'estop' });
  assert.deepEqual(L.interpretVoice('emergency stop', { awaitingApproval: true, taskRunning: true }), { type: 'estop' });
  assert.equal(L.interpretVoice('stop', { taskRunning: true, awaitingApproval: true }).type, 'deny'); // approval card wins
  assert.equal(L.interpretVoice('cancel that', { taskRunning: false }).type, 'task');                 // nothing to cancel: it is text
});

test('voice: anything else becomes a tidy task', () => {
  assert.deepEqual(L.interpretVoice('  open youtube   and play the latest   video ', {}),
    { type: 'task', text: 'Open youtube and play the latest video' });
  assert.equal(L.interpretVoice('', {}).type, 'ignore');
  assert.equal(L.interpretVoice('   ', {}).type, 'ignore');
  // a command that merely CONTAINS an approval word is still a command
  assert.equal(L.interpretVoice('yes please send the report to Sam', { awaitingApproval: true }).type, 'task');
});

test('steps and statuses read like plain English', () => {
  assert.deepEqual(L.describeStep({ tool: 'ui.read_page', status: 'succeeded', note: 'passed' }).text, 'Read the page');
  assert.equal(L.describeStep({ tool: 'ui.click', status: 'failed', note: 'no control' }).state, 'problem');
  assert.equal(L.describeStep({ tool: 'ui.click', status: 'denied', note: 'x' }).state, 'blocked');
  assert.equal(L.describeStep({ tool: 'recipe.calc_add', status: 'succeeded' }).text, 'Ran saved routine calc_add');
  assert.equal(L.describeStep({ tool: 'something.new', status: 'succeeded' }).text, 'something.new');
  const wait = L.describeStep({ tool: 'ui.click', status: 'pending_confirmation', note: 'confirm' });
  assert.deepEqual(wait, { text: 'Waiting for your OK to click', state: 'working', note: '' });   // never shown as already done
  assert.equal(L.describeStep({ tool: 'ui.type', status: 'dispatched' }).text, 'Type…');
  const skipped = L.describeStep({ tool: 'ui.click', status: 'cancelled' });
  assert.deepEqual(skipped, { text: 'Did not click (not approved)', state: 'blocked', note: '' });   // a denied step never looks done
  assert.equal(L.friendlyError('cannot_proceed: You declined, so I stopped.'), 'You declined, so I stopped.');
  assert.equal(L.friendlyError('action limit reached'), 'action limit reached');
  assert.equal(L.friendlyError(null), '');
  assert.equal(L.statusLabel('waiting_for_confirmation'), 'Needs your OK');
  assert.equal(L.statusTone('completed'), 'good');
  assert.equal(L.statusTone('failed'), 'bad');
  assert.equal(L.statusTone('running'), 'busy');
});

test('approval text, elapsed time and pairing codes', () => {
  assert.equal(L.approvalText({ question: 'Run this high-impact task?' }), 'Run this high-impact task?');
  assert.equal(L.approvalText({ tool: 'ui.click', reason: "'Send' may be irreversible (sends, deletes or pays)", params: { args: { name: 'Send' } } }),
    'Deskhand wants to click "Send". This may be irreversible (sends, deletes or pays).');
  assert.equal(L.approvalText({ tool: 'browser.open_url', params: { args: { url: 'https://example.com' } } }),
    'Deskhand wants to open a page "https://example.com".');
  assert.equal(L.approvalText(null), '');
  assert.equal(L.elapsedText(100, 145), '45s');
  assert.equal(L.elapsedText(100, 225), '2m 5s');
  assert.equal(L.prettyCode('abcd efgh'), 'ABCD-EFGH');
  assert.equal(L.prettyCode('abcd'), 'ABCD');
  assert.equal(L.normalizeCode(' ab-cd ef.gh '), 'ABCDEFGH');
});
