/** Pure helpers for the interactive layer (no React here, so they are easy to test). */

export interface Point {
  x: number;
  y: number;
}

/** How many computers are drawn as pods before the rest collapse into one "+N" pod. */
export const MAX_PODS = 6;

/**
 * Where each computer sits around the orb: on the upper arc (so nothing covers the heading below), spread evenly.
 * One computer sits up and to the right, which reads as "next to" the orb rather than balanced on its head.
 */
export function orbitPositions(count: number, radius: number): Point[] {
  const n = Math.max(0, Math.min(count, MAX_PODS));
  if (n === 0) return [];
  const from = 200, to = 340; // degrees, clockwise from the right-hand side; 270 is straight up
  const angles = n === 1 ? [320] : Array.from({ length: n }, (_, i) => from + ((to - from) * i) / (n - 1));
  return angles.map((deg) => {
    const a = (deg * Math.PI) / 180;
    return { x: Math.round(Math.cos(a) * radius), y: Math.round(Math.sin(a) * radius) };
  });
}

export interface PaletteAction {
  id: string;
  label: string;
  /** shown small on the right: a shortcut or what kind of thing this is */
  hint?: string;
  /** extra words that should also find this action */
  keywords?: string;
  run: () => void;
}

/** Every word typed must appear somewhere; a label that starts with the query comes first, then the original order. */
export function filterActions<T extends Pick<PaletteAction, 'label' | 'keywords'>>(query: string, actions: T[]): T[] {
  const q = query.trim().toLowerCase();
  if (!q) return actions;
  const words = q.split(/\s+/);
  const scored: { a: T; rank: number; i: number }[] = [];
  actions.forEach((a, i) => {
    const hay = (a.label + ' ' + (a.keywords ?? '')).toLowerCase();
    if (!words.every((w) => hay.includes(w))) return;
    const label = a.label.toLowerCase();
    scored.push({ a, i, rank: label.startsWith(q) ? 0 : label.includes(q) ? 1 : 2 });
  });
  return scored.sort((x, y) => x.rank - y.rank || x.i - y.i).map((s) => s.a);
}

/** "mod+k" means Ctrl+K on Windows and Linux, Cmd+K on a Mac. */
export function matchesHotkey(e: Pick<KeyboardEvent, 'key' | 'ctrlKey' | 'metaKey' | 'altKey' | 'shiftKey'>, combo: string): boolean {
  const parts = combo.toLowerCase().split('+');
  const key = parts[parts.length - 1];
  const want = { mod: parts.includes('mod'), alt: parts.includes('alt'), shift: parts.includes('shift') };
  return e.key.toLowerCase() === key && (e.ctrlKey || e.metaKey) === want.mod && e.altKey === want.alt && e.shiftKey === want.shift;
}
