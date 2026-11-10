/**
 * The physics of the Deskhand orb, with no drawing and no browser in it (so it can be tested).
 *
 * The drop's centre never moves. Its edge is N points, each with a height above the resting circle and a speed.
 * A cursor nearby pulls the closest part of the edge toward it; holding the button pulls much further, into a thinning
 * neck; pulled past SNAP the drop lets go once (the caller flips the theme) and re-arms when the button is released.
 */
export const SIZE = 460; // css px of the canvas: room for the glow and a very long stretch
export const R = 58; // radius of the resting drop
export const N = 150; // points around the edge
export const ZONE = R * 2.15; // the soft halo: the gentle pull starts when the cursor is inside it
export const REACH = 160; // the longest a held drop can stretch
export const SNAP = 134; // the stretch you can actually see (px beyond the edge) at which the drop lets go

/** Where the cursor is, measured from the middle of the drop. */
export interface Point {
  dx: number;
  dy: number;
  dist: number;
}

export const pointAt = (dx: number, dy: number): Point => ({ dx, dy, dist: Math.hypot(dx, dy) });

/** How far edge point `i` is, in radians (0..PI), from the direction `ang`. */
export function angDiff(i: number, ang: number): number {
  const full = Math.PI * 2;
  return Math.abs(((((i / N) * full - ang) % full) + Math.PI * 3) % full - Math.PI);
}

/** The edge point that faces the direction (dx, dy). */
export function nearestIndex(dx: number, dy: number): number {
  const a = (Math.atan2(dy, dx) + Math.PI * 2) % (Math.PI * 2);
  return Math.round((a / (Math.PI * 2)) * N) % N;
}

export class OrbSim {
  readonly h = new Float32Array(N); // height of each edge point above the resting circle
  readonly v = new Float32Array(N); // its speed
  readonly tgt = new Float32Array(N); // where the cursor wants it
  t = 0;
  ox = 0; // the highlight leans toward the cursor
  oy = 0;
  wobble = 0;
  cursor: Point | null = null;
  held = false; // button down on the drop
  armed = true; // may snap
  loose = false; // just snapped: let go until released
  private tx = 0;
  private ty = 0;
  private readonly rng: () => number;

  constructor(rng: () => number = Math.random) {
    this.rng = rng;
  }

  /** The cursor moved (speed: px since the last move, used for ripples). */
  move(p: Point, speed = 0): void {
    this.cursor = p;
    this.tx = clamp(p.dx / (R * 2.4), -1, 1);
    this.ty = clamp(p.dy / (R * 2.4), -1, 1);
    const s = Math.min(40, speed);
    this.wobble = Math.min(1, this.wobble + s * 0.008);
    if (!this.held && p.dist < R * 0.95) {
      // poking inside: a dent that ripples
      const ang = Math.atan2(p.dy, p.dx);
      for (let i = 0; i < N; i++) this.v[i]! -= Math.exp(-(angDiff(i, ang) ** 2) / 0.3) * (0.4 + s * 0.05);
    }
  }

  /** Button pressed. Returns true when the press grabbed the drop (it was inside the halo). */
  press(p: Point): boolean {
    if (p.dist >= ZONE) return false;
    this.cursor = p;
    this.held = true;
    this.armed = true;
    this.loose = false;
    for (let i = 0; i < N; i++) this.v[i]! -= 0.8 + this.rng() * 0.4; // a little squish as it is grabbed
    return true;
  }

  release(): void {
    if (!this.held) return;
    this.held = false;
    this.loose = false;
    this.armed = true;
    this.wobble = 1;
  }

  /** The pointer left the page: the drop settles. */
  rest(): void {
    this.cursor = null;
    this.tx = 0;
    this.ty = 0;
    this.release();
  }

  /** Where the edge wants to be. Returns true when the drop has been pulled far enough to let go. */
  private aim(): boolean {
    this.tgt.fill(0);
    const c = this.cursor;
    if (!c || this.loose) return false;
    const gap = c.dist - R; // how far the cursor is from the edge
    let peak: number;
    let sharp: number;
    let snap = false;
    if (this.held) {
      // grabbed: the tip follows the cursor, a thinning neck
      if (c.dist < R * 0.6) return false;
      peak = Math.min(REACH, Math.max(0, gap) * 1.15 + 6);
      sharp = 0.26 - 0.17 * Math.min(1, peak / REACH); // wide at first, a thin neck when stretched far
      let seen = 0;
      for (let i = 0; i < N; i++) if (this.h[i]! > seen) seen = this.h[i]!;
      snap = this.armed && seen >= SNAP; // only once the drop has really stretched that far
    } else {
      // hovering: a gentler pull inside the halo
      if (c.dist >= ZONE || c.dist < R * 0.95) return false;
      const near = 1 - Math.max(0, gap) / (ZONE - R);
      peak = Math.min(46, Math.max(0, gap) + 8) * Math.sqrt(Math.max(0, near));
      sharp = 0.24;
    }
    const ang = Math.atan2(c.dy, c.dx);
    for (let i = 0; i < N; i++) this.tgt[i] = peak * Math.exp(-(angDiff(i, ang) ** 2) / sharp);
    return snap;
  }

  /** One frame. Returns true on the frame the drop snaps (the caller flips the theme). */
  step(): boolean {
    this.t += 0.016;
    const snapped = this.aim();
    if (snapped) {
      this.armed = false;
      this.loose = true;
      for (let i = 0; i < N; i++) this.v[i]! -= 2.5 + this.rng() * 1.5; // let go with a big wobble
      this.wobble = 1;
    }
    const k = this.held ? 0.1 : 0.09; // a soft spring: it lags the hand and overshoots a little
    const damp = this.held ? 0.9 : 0.88;
    const { h, v, tgt, t } = this;
    for (let i = 0; i < N; i++) {
      const l = h[(i + N - 1) % N]!;
      const r = h[(i + 1) % N]!;
      let vi = v[i]!;
      vi += (l + r - 2 * h[i]!) * 0.26; // neighbours pull each other: ripples travel round
      vi += (tgt[i]! - h[i]!) * k; // spring toward where the cursor wants the edge
      vi += Math.sin(t * 1.4 + i * 0.15) * 0.012 + Math.sin(t * 0.8 - i * 0.25) * 0.01; // idle breathing
      v[i] = vi * damp;
    }
    for (let i = 0; i < N; i++) h[i] = clamp(h[i]! + v[i]!, -24, REACH + 12);
    this.ox += (this.tx * 12 - this.ox) * 0.08;
    this.oy += (this.ty * 12 - this.oy) * 0.08;
    this.wobble *= 0.96;
    return snapped;
  }

  /** The furthest the edge currently reaches beyond the resting circle. */
  maxStretch(): number {
    let m = 0;
    for (let i = 0; i < N; i++) if (this.h[i]! > m) m = this.h[i]!;
    return m;
  }
}

function clamp(x: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, x));
}
