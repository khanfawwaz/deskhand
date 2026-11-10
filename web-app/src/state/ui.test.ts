import { afterEach, describe, expect, it } from 'vitest';
import { useUi } from './ui';

afterEach(() => useUi.setState({ settingsOpen: false, connectOpen: false }));

describe('dialog flags', () => {
  it('opens and closes Settings and Connect', () => {
    useUi.getState().openSettings();
    expect(useUi.getState().settingsOpen).toBe(true);
    useUi.getState().closeSettings();
    expect(useUi.getState().settingsOpen).toBe(false);
    useUi.getState().openConnect();
    expect(useUi.getState().connectOpen).toBe(true);
    useUi.getState().closeConnect();
    expect(useUi.getState().connectOpen).toBe(false);
  });

  it('shows one dialog at a time: Connect from inside Settings closes Settings', () => {
    useUi.getState().openSettings();
    useUi.getState().openConnect();
    expect(useUi.getState()).toMatchObject({ settingsOpen: false, connectOpen: true });
  });
});
