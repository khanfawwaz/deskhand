/**
 * Pure helpers for the chat (no React, no network): what a spoken sentence means, how a step reads in plain English,
 * and how tasks group into chats. Ported from the original app's logic.js, with types.
 */
import type { PendingApproval, TaskStep, TaskView } from '../api/types';

/** Lower-case, punctuation removed, single spaces: "Yes!" and " yes " are the same answer. */
export const norm = (s: unknown): string =>
  String(s ?? '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s']/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();

const APPROVE = new Set([
  'approve',
  'approved',
  'yes',
  'yeah',
  'yep',
  'go ahead',
  'do it',
  'confirm',
  'send it',
  'ok',
  'okay',
  'sure',
  'allow',
  'allow it',
  'yes please',
  'approve it',
]);
const DENY = new Set([
  'deny',
  'denied',
  'no',
  'nope',
  'dont',
  "don't",
  'do not',
  'reject',
  'not now',
  'stop',
  'cancel',
  'no thanks',
  'dont do it',
  "don't do it",
  'decline',
]);
const CANCEL = new Set(['stop', 'cancel', 'cancel that', 'stop that', 'abort', 'never mind', 'nevermind', 'stop it']);
const ESTOP = new Set(['emergency stop', 'stop everything', 'kill switch', 'panic', 'emergency']);

export type VoiceIntent =
  | { type: 'approve' }
  | { type: 'deny' }
  | { type: 'cancel' }
  | { type: 'estop' }
  | { type: 'ignore'; reason: string }
  | { type: 'task'; text: string };

export interface VoiceContext {
  awaitingApproval?: boolean;
  taskRunning?: boolean;
}

/** What did the person mean? Safety words always win; a stray "yes" never starts a task. */
export function interpretVoice(text: string, ctx: VoiceContext = {}): VoiceIntent {
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

export function tidyGoal(text: unknown): string {
  const t = String(text ?? '')
    .replace(/\s+/g, ' ')
    .trim();
  return t ? t.charAt(0).toUpperCase() + t.slice(1) : '';
}

/** tool -> [what happened, what Deskhand is about to do] */
const VERBS: Record<string, readonly [string, string]> = {
  'browser.open_url': ['Opened a page', 'open a page'],
  'browser.open_private': ['Opened a private page', 'open a private page'],
  'ui.snapshot': ['Looked at the page', 'look at the page'],
  'ui.click': ['Clicked', 'click'],
  'ui.type': ['Typed', 'type'],
  'ui.read': ['Read a value', 'read a value'],
  'ui.read_page': ['Read the page', 'read the page'],
  'ui.wait': ['Waited for the page', 'wait for the page'],
  'screen.zoom': ['Zoomed in', 'zoom in'],
  capture_screen: ['Looked at the screen', 'look at the screen'],
  'windows.focus': ['Brought a window forward', 'bring a window forward'],
  'windows.list': ['Checked open windows', 'check open windows'],
  get_active_window: ['Checked the active window', 'check the active window'],
  launch_application: ['Opened an app', 'open an app'],
  click: ['Clicked', 'click'],
  double_click: ['Double-clicked', 'double-click'],
  type_text: ['Typed', 'type'],
  press_key: ['Pressed a key', 'press a key'],
  hotkey: ['Pressed a shortcut', 'press a shortcut'],
  scroll: ['Scrolled', 'scroll'],
  wait: ['Waited', 'wait'],
  run_command: ['Ran a command', 'run a command'],
  'files.read': ['Read a file', 'read a file'],
  'files.write': ['Wrote a file', 'write a file'],
  'files.list': ['Listed a folder', 'list a folder'],
  'clipboard.set': ['Copied text', 'copy text'],
  'clipboard.get': ['Read the clipboard', 'read the clipboard'],
  'web.open': ['Opened a web page', 'open a web page'],
  'web.links': ['Looked at the links on the page', 'look at the links on the page'],
  'web.click': ['Clicked on the page', 'click on the page'],
  'web.search': ['Searched the site', 'search the site'],
  'web.url': ['Checked the address', 'check the address'],
  'web.read': ['Read the page', 'read the page'],
  'browser.current_url': ['Checked the address', 'check the address'],
  invalid: ['Rethought a step', 'rethink a step'],
};
const PENDING = new Set(['pending_confirmation']);
const WORKING = new Set(['dispatched', 'proposed']);

export type StepState = 'ok' | 'working' | 'blocked' | 'problem';
export interface StepLine {
  text: string;
  state: StepState;
  note: string;
}

/** One human line and a state for a step. A step that is waiting or was refused never reads as already done. */
export function describeStep(step: TaskStep): StepLine {
  const isRecipe = step.tool.startsWith('recipe.');
  const [past, now] = isRecipe
    ? (['Ran saved routine ' + step.tool.slice(7), 'run saved routine ' + step.tool.slice(7)] as const)
    : (VERBS[step.tool] ?? ([step.tool, step.tool] as const));
  if (PENDING.has(step.status))
    return {
      text: 'Waiting for your OK to ' + now,
      state: 'working',
      note: '',
    };
  if (step.status === 'cancelled')
    return {
      text: 'Did not ' + now + ' (not approved)',
      state: 'blocked',
      note: '',
    };
  if (WORKING.has(step.status))
    return {
      text: now.charAt(0).toUpperCase() + now.slice(1) + '…',
      state: 'working',
      note: '',
    };
  const state: StepState =
    step.status === 'denied' ? 'blocked' : step.status === 'failed' || step.status === 'uncertain' ? 'problem' : 'ok';
  return { text: past, state, note: step.note || '' };
}

const LABELS: Record<string, string> = {
  queued: 'Waiting to start',
  running: 'Working',
  waiting_for_action: 'Thinking',
  verifying: 'Checking',
  waiting_for_confirmation: 'Needs your OK',
  waiting_for_device: 'Waiting for the laptop',
  completed: 'Done',
  failed: 'Could not finish',
  cancelled: 'Stopped',
};
export const statusLabel = (status: string): string => LABELS[status] ?? status;

export type StatusTone = 'good' | 'bad' | 'muted' | 'warn' | 'busy';
const TONES: Record<string, StatusTone> = {
  completed: 'good',
  failed: 'bad',
  cancelled: 'muted',
  waiting_for_confirmation: 'warn',
  waiting_for_device: 'warn',
};
export const statusTone = (status: string): StatusTone => TONES[status] ?? 'busy';

/** "45s" or "2m 5s" between two times given in seconds (the end defaults to now). */
export function elapsedText(startSec: number, endSec?: number | null): string {
  const s = Math.max(0, Math.round((endSec || Date.now() / 1000) - startSec));
  return s < 60 ? s + 's' : Math.floor(s / 60) + 'm ' + (s % 60) + 's';
}

/** The plain-English line on a question or permission card: 'Deskhand wants to click "Send". This may be irreversible.' */
export function approvalText(p: PendingApproval | null | undefined): string {
  if (!p) return '';
  if (p.question) return p.question + (p.reason ? ' (' + p.reason + ')' : '');
  const params = p.params ?? {};
  const args = ((params as { args?: unknown }).args ?? params) as Record<string, unknown>;
  const target = String(args.name || args.url || args.title || args.key || '');
  const verb = (p.tool ? VERBS[p.tool]?.[1] : undefined) ?? p.tool ?? 'do something';
  const reason = String(p.reason || '')
    .replace(/^'[^']*'\s*/, 'This ')
    .replace(/^policy requires approval for .*/, '');
  return (
    'Deskhand wants to ' +
    verb +
    (target ? ' "' + target + '"' : '') +
    '.' +
    (reason ? ' ' + reason.charAt(0).toUpperCase() + reason.slice(1) + '.' : '')
  );
}

/** Server errors look like "cannot_proceed: <reason>": show only the reason. */
export const friendlyError = (e: unknown): string =>
  String(e ?? '').replace(/^(cannot_proceed|need_more_information|need_confirmation):\s*/, '');

/** The part of a task the chat grouping needs (so tests and callers can pass small objects). */
export type ChatTask = Pick<TaskView, 'id' | 'goal' | 'created_at'> & Partial<Omit<TaskView, 'id' | 'goal' | 'created_at'>>;

export interface Chat<T extends ChatTask = TaskView> {
  /** the id of the chat: its first task */
  thread: string;
  /** oldest first */
  tasks: T[];
  root: T;
  latest: T;
  /** a chat is named by its first message */
  title: string;
}

/**
 * Chats are threads of tasks: tasks that continue one another share `thread` (the id of the first one).
 * Returns one entry per chat, newest activity first.
 */
export function groupChats<T extends ChatTask>(tasks: readonly T[] | null | undefined): Chat<T>[] {
  const by = new Map<string, T[]>();
  for (const t of tasks ?? []) {
    const k = t.thread || t.id;
    const list = by.get(k);
    if (list) list.push(t);
    else by.set(k, [t]);
  }
  const chats: Chat<T>[] = [];
  for (const [thread, ts] of by) {
    ts.sort((a, b) => a.created_at - b.created_at);
    const first = ts[0];
    const last = ts[ts.length - 1];
    if (!first || !last) continue;
    const root = ts.find((x) => x.id === thread) ?? first;
    chats.push({ thread, tasks: ts, root, latest: last, title: root.goal });
  }
  return chats.sort((a, b) => b.latest.created_at - a.latest.created_at);
}

/** The chat that contains a task (by any of its task ids). */
export function chatOf<T extends ChatTask>(tasks: readonly T[] | null | undefined, id: string | null | undefined): Chat<T> | null {
  if (!id) return null;
  const t = (tasks ?? []).find((x) => x.id === id);
  if (!t) return null;
  const key = t.thread || t.id;
  return groupChats(tasks).find((c) => c.thread === key) ?? null;
}

/** The task a new message continues: the open chat's latest task, but only once it has finished. */
export const continueTarget = (task: Pick<TaskView, 'id' | 'terminal'> | null | undefined): string | null =>
  task && task.terminal ? task.id : null;

/**
 * Put newer copies of tasks into the list we hold. The list from the server has no steps, a single task and live
 * events do: an update without steps keeps the steps we already have.
 */
export function mergeTasks(old: readonly TaskView[] | undefined, incoming: readonly TaskView[]): TaskView[] {
  const by = new Map<string, TaskView>((old ?? []).map((t) => [t.id, t]));
  for (const t of incoming) {
    const prev = by.get(t.id);
    by.set(t.id, t.steps === undefined && prev?.steps ? { ...t, steps: prev.steps } : t);
  }
  return [...by.values()].sort((a, b) => b.created_at - a.created_at);
}

/** The task that is waiting for the person's answer, if any. */
export const pendingTask = (tasks: readonly TaskView[] | undefined): TaskView | null =>
  (tasks ?? []).find((t) => !t.terminal && t.pending_approval) ?? null;

/** Any task that has not finished. */
export const runningTask = (tasks: readonly TaskView[] | undefined): TaskView | null => (tasks ?? []).find((t) => !t.terminal) ?? null;

/** "3 steps" / "1 step". */
export const stepCount = (n: number): string => n + (n === 1 ? ' step' : ' steps');

/** The list the server just sent replaces ours (tasks deleted elsewhere disappear), but steps we already hold are kept. */
export function replaceTasks(fresh: readonly TaskView[], old: readonly TaskView[] | undefined): TaskView[] {
  const prev = new Map<string, TaskView>((old ?? []).map((t) => [t.id, t]));
  return fresh
    .map((t) => {
      const p = prev.get(t.id);
      return t.steps === undefined && p?.steps ? { ...t, steps: p.steps } : t;
    })
    .sort((a, b) => b.created_at - a.created_at);
}

export type Mood = 'idle' | 'listening' | 'working' | 'done' | 'stuck';

/** What the orb shows: the mic wins; otherwise how the task in front of the person is going. */
export function moodFor(task: Pick<TaskView, 'status' | 'terminal' | 'pending_approval'> | null | undefined, listening = false): Mood {
  if (listening) return 'listening';
  if (!task) return 'idle';
  if (task.pending_approval || task.status === 'waiting_for_device' || task.status === 'failed' || task.status === 'cancelled')
    return 'stuck';
  return task.terminal ? 'done' : 'working';
}
