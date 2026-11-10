import { act, render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CommandPalette } from './CommandPalette';
import { DeviceOrbit } from './DeviceOrbit';
import { driftOffset, filterActions, matchesHotkey, MAX_PODS, networkLayout, orbitPositions } from './orbit';
import { useTypewriter } from './useTypewriter';

describe('orbitPositions', () => {
  it('puts every computer above the orb, at the asked distance', () => {
    for (const n of [1, 2, 3, 6]) {
      const pts = orbitPositions(n, 160);
      expect(pts).toHaveLength(n);
      for (const p of pts) {
        expect(p.y).toBeLessThan(0); // above the centre, never over the heading below
        expect(Math.round(Math.hypot(p.x, p.y))).toBeGreaterThan(157);
        expect(Math.round(Math.hypot(p.x, p.y))).toBeLessThan(163);
      }
    }
  });
  it('spreads several computers left to right and never draws more than the cap', () => {
    const xs = orbitPositions(4, 160).map((p) => p.x);
    expect([...xs].sort((a, b) => a - b)).toEqual(xs);
    expect(orbitPositions(40, 160)).toHaveLength(MAX_PODS);
    expect(orbitPositions(0, 160)).toEqual([]);
  });
});

describe('orbitPositions on a wide, staggered arc', () => {
  it('gives every name room: neighbours are far apart, or at clearly different heights', () => {
    const pts = orbitPositions(6, 230, 140, 1.2);
    for (let i = 1; i < pts.length; i++) {
      const a = pts[i - 1]!, b = pts[i]!;
      expect(Math.abs(a.x - b.x) > 125 || Math.abs(a.y - b.y) > 40).toBe(true);
    }
  });
});

describe('filterActions', () => {
  const actions = [
    { label: 'Open settings', keywords: 'preferences account' },
    { label: 'Switch to dark theme', keywords: 'appearance night' },
    { label: 'New task', keywords: 'chat start' },
    { label: 'Settle the theme question' },
  ];
  it('returns everything for an empty query, in order', () => expect(filterActions('  ', actions)).toEqual(actions));
  it('needs every typed word, looks in keywords too, and ranks a label that starts with the query first', () => {
    expect(filterActions('theme', actions).map((a) => a.label)).toEqual(['Switch to dark theme', 'Settle the theme question']);
    expect(filterActions('night', actions).map((a) => a.label)).toEqual(['Switch to dark theme']);
    expect(filterActions('set', actions).map((a) => a.label)).toEqual(['Settle the theme question', 'Open settings']);
    expect(filterActions('dark settings', actions)).toEqual([]);
  });
});

describe('matchesHotkey', () => {
  const ev = (o: Partial<KeyboardEvent>) => ({ key: 'k', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...o });
  it('treats Ctrl and Cmd as the same "mod" and refuses extra modifiers', () => {
    expect(matchesHotkey(ev({ ctrlKey: true }), 'mod+k')).toBe(true);
    expect(matchesHotkey(ev({ metaKey: true, key: 'K' }), 'mod+k')).toBe(true);
    expect(matchesHotkey(ev({}), 'mod+k')).toBe(false);
    expect(matchesHotkey(ev({ ctrlKey: true, shiftKey: true }), 'mod+k')).toBe(false);
  });
});

describe('DeviceOrbit', () => {
  const devices = [
    { device_id: 'a', name: 'Laptop', online: true },
    { device_id: 'b', name: 'Office PC', online: false },
  ];
  it('shows each computer as a pod that says whether it is online and chosen, and toggles on press', async () => {
    const onToggle = vi.fn();
    render(<DeviceOrbit devices={devices} selected={['a']} onToggle={onToggle} onConnect={() => {}}><i>orb</i></DeviceOrbit>);
    const laptop = screen.getByRole('button', { name: 'Laptop, online, chosen' });
    const office = screen.getByRole('button', { name: 'Office PC, offline' });
    expect(laptop).toHaveAttribute('aria-pressed', 'true');
    expect(office).toHaveAttribute('aria-pressed', 'false');
    await userEvent.click(office);
    expect(onToggle).toHaveBeenCalledWith('b');
    expect(screen.getByRole('group', { name: 'Your computers' })).toHaveAttribute('data-mood', 'idle');
  });
  it('with no computer shows one pod that starts connecting one', async () => {
    const onConnect = vi.fn();
    render(<DeviceOrbit devices={[]} selected={[]} onToggle={() => {}} onConnect={onConnect}><i>orb</i></DeviceOrbit>);
    await userEvent.click(screen.getByRole('button', { name: 'Connect a computer' }));
    expect(onConnect).toHaveBeenCalledOnce();
  });
  it('draws every computer: extra ones grow outwards, each tied to a computer on the ring inside', () => {
    const many = Array.from({ length: 9 }, (_, i) => ({ device_id: 'd' + i, name: 'PC ' + i, online: true }));
    const { container, rerender } = render(<DeviceOrbit devices={many} selected={['d0', 'd7']} onToggle={() => {}} onConnect={() => {}}><i>orb</i></DeviceOrbit>);
    expect(screen.getAllByRole('button')).toHaveLength(9);
    expect(screen.queryByText(/^\+\d/)).toBeNull();
    expect(container.querySelectorAll('[class*="spark"]')).toHaveLength(0);
    rerender(<DeviceOrbit devices={many} selected={['d0', 'd7']} pulse={1} mood="working" onToggle={() => {}} onConnect={() => {}}><i>orb</i></DeviceOrbit>);
    expect(container.querySelectorAll('[class*="spark"]')).toHaveLength(2);
    expect(screen.getByRole('group')).toHaveAttribute('data-mood', 'working');
  });
});

describe('networkLayout', () => {
  it('puts the first ring on the orb and ties each extra computer to its nearest one on the ring inside', () => {
    const nodes = networkLayout(9, 230, 140, 6);
    expect(nodes).toHaveLength(9);
    expect(nodes.slice(0, 6).every((p) => p.parent === -1)).toBe(true);
    for (const p of nodes.slice(6)) {
      expect(p.parent).toBeGreaterThanOrEqual(0);
      expect(p.parent).toBeLessThan(6);
      expect(p.y).toBeLessThan(0);
      expect(Math.hypot(p.x, p.y)).toBeGreaterThan(Math.hypot(nodes[p.parent]!.x, nodes[p.parent]!.y));
    }
    expect(networkLayout(3, 200, 140, 6).every((p) => p.parent === -1)).toBe(true);
    expect(networkLayout(30, 200, 140, 4)).toHaveLength(30); // never a "+N": a third ring if it must
  });
});

describe('CommandPalette', () => {
  it('filters as you type, moves with the arrows, runs with Enter and closes', async () => {
    const a = vi.fn(), b = vi.fn(), onClose = vi.fn();
    const actions = [
      { id: 'new', label: 'New task', run: a },
      { id: 'dark', label: 'Switch to dark theme', hint: 'Appearance', run: b },
      { id: 'set', label: 'Open settings', run: vi.fn() },
    ];
    render(<CommandPalette open onClose={onClose} actions={actions} />);
    const box = screen.getByRole('combobox');
    expect(box).toHaveFocus();
    expect(screen.getAllByRole('option')).toHaveLength(3);
    await userEvent.keyboard('{ArrowDown}');
    expect(screen.getByRole('option', { name: /Switch to dark theme/ })).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{Enter}');
    expect(b).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
    await userEvent.type(box, 'zzz');
    expect(screen.getByText(/Nothing matches/)).toBeInTheDocument();
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(a).not.toHaveBeenCalled();
  });
  it('renders nothing while closed', () => {
    const { container } = render(<CommandPalette open={false} onClose={() => {}} actions={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('useTypewriter', () => {
  afterEach(() => vi.useRealTimers());
  it('types an example letter by letter and goes quiet when switched off', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ on }) => useTypewriter(['Open Notepad', 'Play lofi'], on, { typeMs: 10, holdMs: 50, eraseMs: 5 }), { initialProps: { on: true } });
    expect(result.current).toBe('');
    act(() => void vi.advanceTimersByTime(600 + 10 * 3));
    expect(result.current).toBe('Open');
    act(() => void vi.advanceTimersByTime(10 * 8 + 20)); // the other 8 letters, then it holds the full line for a moment
    expect(result.current).toBe('Open Notepad');
    rerender({ on: false });
    expect(result.current).toBe('');
  });
});

describe('driftOffset', () => {
  it('is gentle: never far from the resting place, and different for every computer', () => {
    for (let i = 0; i < 6; i++) {
      for (let t = 0; t < 120; t += 0.5) {
        const o = driftOffset(i, t);
        expect(Math.abs(o.x)).toBeLessThan(16 * 1.46 + 0.1);
        expect(Math.abs(o.y)).toBeLessThan(16 * 1.46 + 0.1);
      }
    }
    expect(driftOffset(0, 12)).not.toEqual(driftOffset(1, 12));
    expect(driftOffset(2, 5)).toEqual(driftOffset(2, 5)); // same moment, same place: nothing random
  });
  it('really moves, slowly: a second later it is close by, a few seconds later it has wandered', () => {
    const a = driftOffset(1, 10), b = driftOffset(1, 11), c = driftOffset(1, 18);
    expect(Math.hypot(a.x - b.x, a.y - b.y)).toBeLessThan(12);
    expect(Math.hypot(a.x - c.x, a.y - c.y)).toBeGreaterThan(3);
  });
});

describe('DeviceOrbit drifting', () => {
  const devices = [{ device_id: 'a', name: 'Laptop', online: true }, { device_id: 'b', name: 'Office PC', online: true }];
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('the computers wander slowly and the line to the orb follows each one', () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance'] });
    const { container } = render(<DeviceOrbit devices={devices} selected={['a']} onToggle={() => {}} onConnect={() => {}}><i>orb</i></DeviceOrbit>);
    const pod = screen.getByRole('button', { name: /Laptop/ });
    const line = container.querySelector('line') as SVGLineElement;
    const before = line.getAttribute('x2');
    act(() => void vi.advanceTimersByTime(3000));
    expect(pod.style.getPropertyValue('--dx')).toMatch(/px$/);
    expect(line.getAttribute('x2')).not.toBe(before);
    const dx = parseFloat(pod.style.getPropertyValue('--dx'));
    const wireMove = Math.abs(parseFloat(line.getAttribute('x2') as string) - parseFloat(before as string));
    expect(Math.abs(dx)).toBeLessThan(26);
    expect(wireMove).toBeLessThan(40);
  });

  it('people who ask for less motion get computers that stay where they are', () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance'] });
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
    render(<DeviceOrbit devices={devices} selected={[]} onToggle={() => {}} onConnect={() => {}}><i>orb</i></DeviceOrbit>);
    const pod = screen.getByRole('button', { name: /Laptop/ });
    act(() => void vi.advanceTimersByTime(3000));
    expect(pod.style.getPropertyValue('--dx')).toBe('');
  });
});
