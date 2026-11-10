import { describe, expect, it } from 'vitest';
import { angDiff, N, nearestIndex, OrbSim, pointAt, R, REACH, SNAP, ZONE } from './orbSim';

const run = (sim: OrbSim, frames: number) => {
  let snaps = 0;
  for (let i = 0; i < frames; i++) if (sim.step()) snaps++;
  return snaps;
};
const still = () => new OrbSim(() => 0.5);

describe('orb geometry', () => {
  it('measures the angle between an edge point and a direction, the short way round', () => {
    expect(angDiff(0, 0)).toBeCloseTo(0);
    expect(angDiff(N / 2, 0)).toBeCloseTo(Math.PI);
    expect(angDiff(N / 4, 0)).toBeCloseTo(Math.PI / 2);
    expect(angDiff(0, Math.PI * 2 - 0.1)).toBeCloseTo(0.1); // wraps
  });

  it('finds the edge point that faces the cursor', () => {
    expect(nearestIndex(1, 0)).toBe(0);
    expect(nearestIndex(0, 1)).toBe(Math.round(N / 4));
    expect(nearestIndex(-1, 0)).toBe(N / 2);
    expect(nearestIndex(1, -0.0001)).toBe(0); // just under a full turn is still point 0
  });
});

describe('the drop', () => {
  it('rests as a circle: breathing only moves the edge a hair', () => {
    const sim = still();
    run(sim, 300);
    expect(sim.maxStretch()).toBeLessThan(1);
  });

  it('stretches the nearest edge toward a cursor in the halo, and leaves the far side alone', () => {
    const sim = still();
    sim.move(pointAt(R + 30, 0)); // to the right, inside the halo
    run(sim, 200);
    const near = sim.h[nearestIndex(1, 0)]!;
    const far = sim.h[nearestIndex(-1, 0)]!;
    expect(near).toBeGreaterThan(8);
    expect(Math.abs(far)).toBeLessThan(1.5);
    expect(sim.maxStretch()).toBeLessThan(SNAP); // hovering can never snap
  });

  it('ignores a cursor outside the halo', () => {
    const sim = still();
    sim.move(pointAt(ZONE + 40, 0));
    run(sim, 120);
    expect(sim.maxStretch()).toBeLessThan(1);
  });

  it('only grabs when pressed inside the halo', () => {
    const sim = still();
    expect(sim.press(pointAt(ZONE + 1, 0))).toBe(false);
    expect(sim.held).toBe(false);
    expect(sim.press(pointAt(R, 0))).toBe(true);
    expect(sim.held).toBe(true);
  });

  it('stretches further while held, without snapping on a short pull', () => {
    const sim = still();
    sim.press(pointAt(R + 10, 0));
    sim.move(pointAt(R + 60, 0));
    expect(run(sim, 300)).toBe(0);
    expect(sim.h[nearestIndex(1, 0)]!).toBeGreaterThan(50);
    expect(sim.maxStretch()).toBeLessThan(SNAP);
  });

  it('snaps exactly once when pulled far, and stays let-go until the button is released', () => {
    const sim = still();
    sim.press(pointAt(R + 10, 0));
    sim.move(pointAt(R + 220, -60));
    expect(run(sim, 400)).toBe(1);
    expect(sim.loose).toBe(true);
    expect(run(sim, 400)).toBe(0); // still holding: no second snap
    sim.release();
    expect(sim.armed).toBe(true);
    sim.press(pointAt(R + 10, 0));
    sim.move(pointAt(R + 220, 0));
    expect(run(sim, 400)).toBe(1); // a new pull can snap again
  });

  it('never stretches past its reach, and settles back after the pointer leaves', () => {
    const sim = still();
    sim.press(pointAt(R, 0));
    sim.move(pointAt(900, 0));
    for (let i = 0; i < 400; i++) {
      sim.step();
      expect(sim.maxStretch()).toBeLessThanOrEqual(REACH + 12);
    }
    sim.rest();
    expect(sim.cursor).toBeNull();
    expect(sim.held).toBe(false);
    run(sim, 600);
    expect(sim.maxStretch()).toBeLessThan(2);
  });

  it('dents and ripples when poked inside', () => {
    const sim = still();
    sim.move(pointAt(R * 0.5, 0), 20);
    expect(sim.v[nearestIndex(1, 0)]!).toBeLessThan(0);
  });
});
