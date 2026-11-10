import { KEYS, storage } from '../../lib/storage';

/** The three checkboxes of the Preferences box. They live in the same localStorage object as the theme ('pilot.settings'). */
export interface LocalPrefs {
  /** send voice commands automatically after a short countdown */
  auto: boolean;
  /** read results aloud */
  speak: boolean;
  /** show a system notification when Deskhand needs approval */
  notify: boolean;
}

export const PREF_DEFAULTS: LocalPrefs = { auto: true, speak: false, notify: false };

function readAll(): Record<string, unknown> {
  try {
    const v: unknown = JSON.parse(storage.get(KEYS.settings) || '{}');
    return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

export function readPrefs(): LocalPrefs {
  const all = readAll();
  const pick = (k: keyof LocalPrefs) => (typeof all[k] === 'boolean' ? (all[k] as boolean) : PREF_DEFAULTS[k]);
  return { auto: pick('auto'), speak: pick('speak'), notify: pick('notify') };
}

/** Change one preference; every other field of the shared object (theme, anything the original app keeps) is left alone. */
export function writePref(key: keyof LocalPrefs, value: boolean): void {
  storage.set(KEYS.settings, JSON.stringify({ ...readAll(), [key]: value }));
}
