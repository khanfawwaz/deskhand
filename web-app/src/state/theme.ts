import { create } from 'zustand';
import { KEYS, storage } from '../lib/storage';

export type ThemeMode = 'auto' | 'light' | 'dark';

/** The settings object is shared with the original app (it keeps other fields in it), so only `theme` is ever touched. */
function readSettings(): Record<string, unknown> {
  try {
    const v: unknown = JSON.parse(storage.get(KEYS.settings) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export function readTheme(): ThemeMode {
  const t = readSettings().theme;
  return t === 'light' || t === 'dark' ? t : 'auto';
}

/** Light and dark force the colours; "auto" removes the override and follows the system. */
export function applyTheme(mode: ThemeMode): void {
  const root = document.documentElement;
  if (mode === 'light' || mode === 'dark') root.setAttribute('data-theme', mode);
  else root.removeAttribute('data-theme');
}

export function effectiveTheme(): 'light' | 'dark' {
  const forced = document.documentElement.getAttribute('data-theme');
  if (forced === 'light' || forced === 'dark') return forced;
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

interface ThemeState {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  /** Flip between light and dark whatever the current mode is (used by the orb easter egg later). */
  toggle: () => void;
}

export const useTheme = create<ThemeState>((set) => ({
  mode: readTheme(),
  setMode: (mode) => {
    storage.set(KEYS.settings, JSON.stringify({ ...readSettings(), theme: mode }));
    applyTheme(mode);
    set({ mode });
  },
  toggle: () => useTheme.getState().setMode(effectiveTheme() === 'dark' ? 'light' : 'dark'),
}));

/** Call once at start-up. */
export function initTheme(): void {
  applyTheme(useTheme.getState().mode);
}
