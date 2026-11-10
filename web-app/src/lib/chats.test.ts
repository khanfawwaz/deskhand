import { describe, expect, it } from 'vitest';
import type { TaskView } from '../api/types';
import {
  approvalText,
  chatOf,
  continueTarget,
  describeStep,
  elapsedText,
  friendlyError,
  groupChats,
  interpretVoice,
  mergeTasks,
  moodFor,
  pendingTask,
  replaceTasks,
  runningTask,
  statusLabel,
  statusTone,
  stepCount,
} from './chats';

// every case of the original tests/js/logic.test.mjs, plus the new helpers

describe('voice', () => {
  it('approval words only count while something is waiting', () => {
    for (const w of ['approve', 'Yes!', 'go ahead', 'Okay.', 'send it']) {
      expect(interpretVoice(w, { awaitingApproval: true }), w).toEqual({
        type: 'approve',
      });
    }
    for (const w of ['deny', 'No', "don't do it", 'not now', 'stop']) {
      expect(interpretVoice(w, { awaitingApproval: true }), w).toEqual({
        type: 'deny',
      });
    }
    expect(interpretVoice('yes', { awaitingApproval: false }).type).toBe('ignore'); // a stray "yes" must never start a task
    expect(interpretVoice('no', {}).type).toBe('ignore');
  });

  it('stop words cancel, emergency words always win', () => {
    expect(interpretVoice('cancel that', { taskRunning: true })).toEqual({
      type: 'cancel',
    });
    expect(interpretVoice('Stop everything!', {})).toEqual({ type: 'estop' });
    expect(
      interpretVoice('emergency stop', {
        awaitingApproval: true,
        taskRunning: true,
      }),
    ).toEqual({ type: 'estop' });
    expect(interpretVoice('stop', { taskRunning: true, awaitingApproval: true }).type).toBe('deny'); // the question wins
    expect(interpretVoice('cancel that', { taskRunning: false }).type).toBe('task'); // nothing to cancel: it is text
  });

  it('anything else becomes a tidy task', () => {
    expect(interpretVoice('  open youtube   and play the latest   video ', {})).toEqual({
      type: 'task',
      text: 'Open youtube and play the latest video',
    });
    expect(interpretVoice('', {}).type).toBe('ignore');
    expect(interpretVoice('   ', {}).type).toBe('ignore');
    // a command that merely CONTAINS an approval word is still a command
    expect(
      interpretVoice('yes please send the report to Sam', {
        awaitingApproval: true,
      }).type,
    ).toBe('task');
  });
});

describe('steps and statuses read like plain English', () => {
  it('describes steps', () => {
    expect(
      describeStep({
        tool: 'ui.read_page',
        status: 'succeeded',
        note: 'passed',
      }).text,
    ).toBe('Read the page');
    expect(describeStep({ tool: 'ui.click', status: 'failed', note: 'no control' }).state).toBe('problem');
    expect(describeStep({ tool: 'ui.click', status: 'denied', note: 'x' }).state).toBe('blocked');
    expect(describeStep({ tool: 'recipe.calc_add', status: 'succeeded' }).text).toBe('Ran saved routine calc_add');
    expect(describeStep({ tool: 'something.new', status: 'succeeded' }).text).toBe('something.new');
    // never shown as already done
    expect(
      describeStep({
        tool: 'ui.click',
        status: 'pending_confirmation',
        note: 'confirm',
      }),
    ).toEqual({
      text: 'Waiting for your OK to click',
      state: 'working',
      note: '',
    });
    expect(describeStep({ tool: 'ui.type', status: 'dispatched' }).text).toBe('Type…');
    // a denied step never looks done
    expect(describeStep({ tool: 'ui.click', status: 'cancelled' })).toEqual({
      text: 'Did not click (not approved)',
      state: 'blocked',
      note: '',
    });
    expect(describeStep({ tool: 'web.search', status: 'succeeded' }).text).toBe('Searched the site');
    expect(describeStep({ tool: 'ui.click', status: 'uncertain', note: null }).note).toBe('');
  });

  it('cleans errors and labels statuses', () => {
    expect(friendlyError('cannot_proceed: You declined, so I stopped.')).toBe('You declined, so I stopped.');
    expect(friendlyError('action limit reached')).toBe('action limit reached');
    expect(friendlyError(null)).toBe('');
    expect(statusLabel('waiting_for_confirmation')).toBe('Needs your OK');
    expect(statusLabel('something_new')).toBe('something_new');
    expect(statusTone('completed')).toBe('good');
    expect(statusTone('failed')).toBe('bad');
    expect(statusTone('running')).toBe('busy');
    expect(stepCount(1)).toBe('1 step');
    expect(stepCount(4)).toBe('4 steps');
  });
});

describe('approval text and elapsed time', () => {
  it('reads like a sentence', () => {
    expect(approvalText({ question: 'Run this high-impact task?' })).toBe('Run this high-impact task?');
    expect(
      approvalText({
        tool: 'ui.click',
        reason: "'Send' may be irreversible (sends, deletes or pays)",
        params: { args: { name: 'Send' } },
      }),
    ).toBe('Deskhand wants to click "Send". This may be irreversible (sends, deletes or pays).');
    expect(
      approvalText({
        tool: 'browser.open_url',
        params: { args: { url: 'https://example.com' } },
      }),
    ).toBe('Deskhand wants to open a page "https://example.com".');
    expect(approvalText({ question: 'Which profile?', reason: 'two were found' })).toBe('Which profile? (two were found)');
    expect(approvalText({ tool: 'brand.new_tool' })).toBe('Deskhand wants to brand.new_tool.');
    expect(approvalText({})).toBe('Deskhand wants to do something.');
    expect(approvalText(null)).toBe('');
  });

  it('formats elapsed time', () => {
    expect(elapsedText(100, 145)).toBe('45s');
    expect(elapsedText(100, 225)).toBe('2m 5s');
    expect(elapsedText(200, 100)).toBe('0s'); // clocks can disagree: never negative
  });
});

const t = (over: Partial<TaskView> & { id: string }): TaskView => ({
  goal: 'goal ' + over.id,
  device_id: 'd1',
  status: 'completed',
  created_at: 1,
  updated_at: 1,
  result: null,
  error: null,
  terminal: true,
  pending_approval: null,
  ...over,
});

describe('chats', () => {
  it('follow-ups share a thread, the row is titled by the first message, newest chat first', () => {
    const tasks = [
      {
        id: 'a',
        thread: 'a',
        goal: 'open youtube',
        created_at: 10,
        status: 'completed',
        terminal: true,
      },
      {
        id: 'b',
        thread: 'a',
        goal: 'now close it',
        created_at: 20,
        status: 'failed',
        terminal: true,
      },
      {
        id: 'c',
        thread: 'c',
        goal: 'write a note',
        created_at: 15,
        status: 'completed',
        terminal: true,
      },
      {
        id: 'd',
        goal: 'old task without thread info',
        created_at: 5,
        status: 'completed',
        terminal: true,
      },
    ];
    const chats = groupChats(tasks);
    expect(chats.map((c) => c.thread)).toEqual(['a', 'c', 'd']); // by latest activity
    expect(chats[0]?.title).toBe('open youtube'); // first message, not the latest
    expect(chats[0]?.latest.id).toBe('b'); // the dot shows the latest task
    expect(chats[0]?.tasks.map((x) => x.id)).toEqual(['a', 'b']); // oldest first
    expect(chats[2]?.root.id).toBe('d'); // tasks from before threads are their own chat
    expect(groupChats([])).toEqual([]);
    expect(groupChats(undefined)).toEqual([]);
  });

  it('finds the chat of any of its tasks', () => {
    const tasks = [t({ id: 'a', thread: 'a', created_at: 1 }), t({ id: 'b', thread: 'a', created_at: 2 }), t({ id: 'c', thread: 'c' })];
    expect(chatOf(tasks, 'b')?.thread).toBe('a');
    expect(chatOf(tasks, 'a')?.latest.id).toBe('b');
    expect(chatOf(tasks, 'nope')).toBeNull();
    expect(chatOf(tasks, null)).toBeNull();
    expect(chatOf(undefined, 'a')).toBeNull();
  });

  it('a chat whose first task is gone is still shown, titled by its oldest task', () => {
    const chats = groupChats([
      t({ id: 'b', thread: 'a', goal: 'second', created_at: 2 }),
      t({ id: 'c', thread: 'a', goal: 'third', created_at: 3 }),
    ]);
    expect(chats).toHaveLength(1);
    expect(chats[0]?.title).toBe('second');
  });

  it('a message continues the open chat only after its latest task has finished', () => {
    expect(continueTarget({ id: 'x', terminal: true })).toBe('x');
    expect(continueTarget({ id: 'x', terminal: false })).toBeNull(); // still running: behaviour stays as before
    expect(continueTarget(null)).toBeNull(); // no chat open: a new one
  });

  it('merging keeps the steps we already have when an update has none', () => {
    const steps = [{ tool: 'ui.click', status: 'succeeded' }];
    const old = [t({ id: 'a', created_at: 1, steps }), t({ id: 'b', created_at: 2 })];
    const merged = mergeTasks(old, [t({ id: 'a', created_at: 1, status: 'failed' }), t({ id: 'c', created_at: 3 })]);
    expect(merged.map((x) => x.id)).toEqual(['c', 'b', 'a']); // newest first
    expect(merged.find((x) => x.id === 'a')).toMatchObject({
      status: 'failed',
      steps,
    });
    const fresh = mergeTasks(merged, [t({ id: 'a', created_at: 1, steps: [] })]);
    expect(fresh.find((x) => x.id === 'a')?.steps).toEqual([]); // an update WITH steps replaces them
    expect(mergeTasks(undefined, [t({ id: 'z' })])).toHaveLength(1);
  });

  it('finds the task that waits for an answer and any unfinished task', () => {
    const waiting = t({
      id: 'w',
      terminal: false,
      status: 'waiting_for_confirmation',
      pending_approval: { question: 'Ok?' },
    });
    const busy = t({ id: 'r', terminal: false, status: 'running' });
    expect(pendingTask([t({ id: 'a' }), busy, waiting])?.id).toBe('w');
    expect(pendingTask([busy])).toBeNull();
    expect(runningTask([t({ id: 'a' }), busy])?.id).toBe('r');
    expect(runningTask([t({ id: 'a' })])).toBeNull();
    expect(runningTask(undefined)).toBeNull();
  });
});

describe('refreshing the list and the orb mood', () => {
  const t = (id: string, over: Partial<TaskView> = {}): TaskView => ({
    id,
    goal: id,
    device_id: 'd',
    status: 'completed',
    created_at: 1,
    updated_at: 1,
    result: null,
    error: null,
    terminal: true,
    pending_approval: null,
    ...over,
  });

  it('a fresh list replaces ours (deleted tasks vanish) but keeps the steps we already hold', () => {
    const old = [t('a', { steps: [{ tool: 'wait', status: 'completed' }] }), t('gone')];
    const out = replaceTasks([t('a', { created_at: 5 }), t('b', { created_at: 9 })], old);
    expect(out.map((x) => x.id)).toEqual(['b', 'a']);
    expect(out.find((x) => x.id === 'a')?.steps).toHaveLength(1);
    expect(out.find((x) => x.id === 'b')?.steps).toBeUndefined();
  });

  it('mood: the mic wins, then how the task in front of the person is going', () => {
    expect(moodFor(null)).toBe('idle');
    expect(moodFor(null, true)).toBe('listening');
    expect(moodFor(t('a', { status: 'running', terminal: false }), true)).toBe('listening');
    expect(moodFor(t('a', { status: 'running', terminal: false }))).toBe('working');
    expect(moodFor(t('a'))).toBe('done');
    expect(moodFor(t('a', { status: 'failed' }))).toBe('stuck');
    expect(
      moodFor(
        t('a', {
          status: 'waiting_for_confirmation',
          terminal: false,
          pending_approval: { question: 'q' },
        }),
      ),
    ).toBe('stuck');
  });
});
