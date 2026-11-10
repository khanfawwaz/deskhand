import { create } from 'zustand';
import { KEYS, storage } from '../lib/storage';

interface SessionState {
  /** The sign-in token of this device (null = signed out). Kept in localStorage under the original app's key. */
  token: string | null;
  /** A short message to show on the sign-in screen after being signed out ("You were signed out..."). */
  notice: string;
  setToken: (token: string) => void;
  signOut: (notice?: string) => void;
  clearNotice: () => void;
}

export const useSession = create<SessionState>((set) => ({
  token: storage.get(KEYS.token),
  notice: '',
  setToken: (token) => {
    storage.set(KEYS.token, token);
    set({ token, notice: '' });
  },
  signOut: (notice = '') => {
    storage.remove(KEYS.token);
    set({ token: null, notice });
  },
  clearNotice: () => set({ notice: '' }),
}));
