import { create } from 'zustand';
import { storage } from '../../lib/storage';

/** One answered question: what Deskhand asked and what the person said. Kept for the visit, like the chat on screen. */
export interface QA {
  q: string;
  a: string;
}

const TARGETS_KEY = 'pilot.targets'; // the same key the original app uses

function readTargets(): string[] {
  try {
    const v: unknown = JSON.parse(storage.get(TARGETS_KEY) || '[]');
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

interface ChatState {
  /** answered questions by task id */
  qa: Record<string, QA[]>;
  addQa: (taskId: string, item: QA) => void;
  forget: (taskIds: string[]) => void;
  /** the computers the next task runs on (shared by the composer and the orbiting pods) */
  targets: string[];
  toggleTarget: (id: string) => void;
  /** drop computers that no longer exist; with nothing chosen pick the first online one */
  syncTargets: (devices: { device_id: string; online: boolean }[]) => void;
  /** bumped to put the cursor in the message box (New task) */
  composerFocus: number;
  focusComposer: () => void;
  /** the chat the page opened on was chosen already (a running task) */
  bootDone: boolean;
  setBootDone: () => void;
}

export const useChat = create<ChatState>((set, get) => ({
  qa: {},
  addQa: (taskId, item) =>
    set((s) => ({
      qa: { ...s.qa, [taskId]: [...(s.qa[taskId] ?? []), item] },
    })),
  forget: (ids) =>
    set((s) => {
      const qa = { ...s.qa };
      ids.forEach((i) => delete qa[i]);
      return { qa };
    }),
  targets: readTargets(),
  toggleTarget: (id) => {
    const cur = get().targets;
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    storage.set(TARGETS_KEY, JSON.stringify(next));
    set({ targets: next });
  },
  syncTargets: (devices) => {
    if (!devices.length) return;
    let next = get().targets.filter((id) => devices.some((d) => d.device_id === id));
    if (!next.length) {
      const pick = devices.find((d) => d.online) ?? devices[0];
      next = pick ? [pick.device_id] : [];
    }
    const cur = get().targets;
    if (next.length === cur.length && next.every((x, i) => x === cur[i])) return;
    storage.set(TARGETS_KEY, JSON.stringify(next));
    set({ targets: next });
  },
  composerFocus: 0,
  focusComposer: () => set((s) => ({ composerFocus: s.composerFocus + 1 })),
  bootDone: false,
  setBootDone: () => set({ bootDone: true }),
}));
