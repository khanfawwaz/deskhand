/** Pure helpers for the interactive layer (no React here, so they are easy to test). */

export interface Point {
  x: number;
  y: number;
}

/** How many computers are drawn as pods before the rest collapse into one "+N" pod. */
export const MAX_PODS = 6;

/**
 * Where each computer sits around the orb: on the upper arc (so nothing covers the heading below), spread evenly.
 * The arc can be an ellipse (rx wider than ry) so a row of names has room, and with four or more computers every
 * second one sits a little further out (stagger), so neighbours never share a height and their names never collide.
 * One computer sits up and to the right, which reads as "next to" the orb rather than balanced on its head.
 */
export function orbitPositions(count: number, rx: number, ry = rx, stagger = 1): Point[] {
  const n = Math.max(0, Math.min(count, MAX_PODS));
  if (n === 0) return [];
  const from = 195, to = 345; // degrees, clockwise from the right-hand side; 270 is straight up
  const angles = n === 1 ? [320] : n === 2 ? [235, 305] : Array.from({ length: n }, (_, i) => from + ((to - from) * i) / (n - 1));
  return angles.map((deg, i) => {
    const a = (deg * Math.PI) / 180;
    const k = n >= 4 && i % 2 === 1 ? stagger : 1;
    return { x: Math.round(Math.cos(a) * rx * k), y: Math.round(Math.sin(a) * ry * k) };
  });
}

/** A computer in the network: where it sits, and what it is tied to (-1: the orb itself, otherwise another computer). */
export interface NetNode extends Point {
  parent: number;
}

/**
 * Every computer, laid out as a small network. The first ringSize sit on the arc around the orb and are tied to it.
 * The rest sit on wider arcs further out, each tied to the nearest computer on the ring inside it, so a long list grows
 * outwards like a network instead of collapsing into a "+N". Every second computer on an outer ring sits a little
 * further out, so names never share a height with their neighbours.
 */
export function networkLayout(count: number, rx: number, ry: number, ringSize: number, outerRx = rx * 1.5, stagger = 1.2, outerSize = ringSize + 2, gap = 0.85): NetNode[] {
  const size = Math.max(1, Math.min(ringSize, MAX_PODS));
  const inner = Math.min(Math.max(0, count), size);
  const lift = inner >= 4 ? stagger : 1;
  const nodes: NetNode[] = orbitPositions(inner, rx, ry, lift).map((p) => ({ ...p, parent: -1 }));
  let left = count - inner;
  let prev = nodes.map((_, i) => i); // the ring inside the one being placed
  for (let ring = 1; left > 0; ring++) {
    const take = Math.min(left, Math.max(1, outerSize));
    // outer rings arch over the top (not down the sides, where the first ring already is), each clearly higher
    const from = 222 + 5 * (ring - 1), to = 318 - 5 * (ring - 1);
    const ory = ry * lift * (1 + gap * ring); // gap: how much higher each ring sits than the one inside
    const placed: number[] = [];
    for (let j = 0; j < take; j++) {
      const deg = take === 1 ? 270 : from + ((to - from) * j) / (take - 1);
      const a = (deg * Math.PI) / 180;
      const k = take >= 3 && j % 2 === 1 ? 1.14 : 1;
      const p = { x: Math.round(Math.cos(a) * outerRx), y: Math.round(Math.sin(a) * ory * k) };
      let parent = prev[0] ?? -1;
      let best = Infinity;
      for (const i of prev) {
        const q = nodes[i]!;
        const d = Math.hypot(q.x - p.x, q.y - p.y);
        if (d < best) [best, parent] = [d, i];
      }
      placed.push(nodes.push({ ...p, parent }) - 1);
    }
    prev = placed;
    left -= take;
  }
  return nodes;
}

/**
 * How far pod number i has wandered from its place at time t (seconds): a slow, gentle drift, each pod with its own speed
 * and rhythm, like nodes in a living network. Always within about 1.4 x amp of the resting place, so pods never leave the stage.
 */
export function driftOffset(i: number, t: number, amp = 8): Point {
  const phase = i * 1.9 + 0.7;
  const x = Math.sin(t / (2.6 + i * 0.5) + phase) + 0.45 * Math.sin(t / (1.7 + i * 0.3) + phase * 2.1);
  const y = Math.cos(t / (3.1 + i * 0.45) + phase * 1.4) + 0.45 * Math.sin(t / (1.9 + i * 0.35) + phase * 0.6);
  return { x: amp * x, y: amp * y };
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
