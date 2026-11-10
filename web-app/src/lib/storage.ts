/** localStorage that never throws (private windows, blocked storage, previews): reads come back empty, writes are dropped. */
export const KEYS = {
  token: 'pilot.token', // the same keys the original app uses, so people stay signed in across both
  settings: 'pilot.settings',
} as const;

export const storage = {
  get(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string): void {
    try {
      localStorage.setItem(key, value);
    } catch {
      /* storage unavailable: the app still works, it just forgets */
    }
  },
  remove(key: string): void {
    try {
      localStorage.removeItem(key);
    } catch {
      /* nothing to do */
    }
  },
};
