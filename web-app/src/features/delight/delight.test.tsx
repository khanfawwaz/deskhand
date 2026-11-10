import { act, render, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CommandPalette } from './CommandPalette';
import { DeviceOrbit } from './DeviceOrbit';
import { filterActions, matchesHotkey, MAX_PODS, orbitPositions } from './orbit';
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
  it('collapses a long list into a "+N" pod and sends a spark only to chosen computers', () => {
    const many = Array.from({ length: 9 }, (_, i) => ({ device_id: 'd' + i, name: 'PC ' + i, online: true }));
    const { container, rerender } = render(<DeviceOrbit devices={many} selected={['d0', 'd2']} onToggle={() => {}} onConnect={() => {}}><i>orb</i></DeviceOrbit>);
    expect(screen.getAllByRole('button')).toHaveLength(MAX_PODS - 1);
    expect(screen.getByText('+4')).toBeInTheDocument();
    expect(container.querySelectorAll('[class*="spark"]')).toHaveLength(0);
    rerender(<DeviceOrbit devices={many} selected={['d0', 'd2']} pulse={1} mood="working" onToggle={() => {}} onConnect={() => {}}><i>orb</i></DeviceOrbit>);
    expect(container.querySelectorAll('[class*="spark"]')).toHaveLength(2);
    expect(screen.getByRole('group')).toHaveAttribute('data-mood', 'working');
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
