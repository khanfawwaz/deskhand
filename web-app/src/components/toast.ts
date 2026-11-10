import { create } from 'zustand';

export interface ToastItem {
  id: number;
  message: string;
}

interface ToastState {
  items: ToastItem[];
  show: (message: string, ms?: number) => void;
  dismiss: (id: number) => void;
}

let next = 1;
const timers = new Map<number, ReturnType<typeof setTimeout>>();

/** Short messages at the bottom of the screen ("Chat deleted."). Anywhere: `toast('Chat deleted.')`. */
export const useToasts = create<ToastState>((set, get) => ({
  items: [],
  show: (message, ms = 5000) => {
    const id = next++;
    set((s) => ({ items: [...s.items.slice(-2), { id, message }] })); // at most three at once
    timers.set(id, setTimeout(() => get().dismiss(id), ms));
  },
  dismiss: (id) => {
    clearTimeout(timers.get(id));
    timers.delete(id);
    set((s) => ({ items: s.items.filter((t) => t.id !== id) }));
  },
}));

export const toast = (message: string, ms?: number) => useToasts.getState().show(message, ms);
