import { describe, expect, it, vi } from 'vitest';
import { KEYS, storage } from '../lib/storage';
import { useSession } from './session';
import { applyTheme, effectiveTheme, readTheme, useTheme } from './theme';

describe('session store', () => {
  it('keeps the token under the original app\'s key, so both apps share one sign-in', () => {
    useSession.getState().setToken('abc');
    expect(useSession.getState().token).toBe('abc');
    expect(localStorage.getItem('pilot.token')).toBe('abc');
  });

  it('signing out forgets the token and can leave a message for the sign-in screen', () => {
    useSession.getState().setToken('abc');
    useSession.getState().signOut('You were signed out');
    expect(useSession.getState().token).toBeNull();
    expect(useSession.getState().notice).toBe('You were signed out');
    expect(localStorage.getItem(KEYS.token)).toBeNull();
    useSession.getState().clearNotice();
    expect(useSession.getState().notice).toBe('');
  });

  it('signing in again clears an old message', () => {
    useSession.getState().signOut('old news');
    useSession.getState().setToken('fresh');
    expect(useSession.getState().notice).toBe('');
  });
});

describe('storage', () => {
  it('never throws when the browser blocks storage', () => {
    const boom = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const setBoom = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const rmBoom = vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(storage.get('x')).toBeNull();
    expect(() => storage.set('x', '1')).not.toThrow();
    expect(() => storage.remove('x')).not.toThrow();
    boom.mockRestore();
    setBoom.mockRestore();
    rmBoom.mockRestore();
  });
});

describe('theme store', () => {
  it('forces light or dark on the page, and "auto" hands control back to the system', () => {
    useTheme.getState().setMode('dark');
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    useTheme.getState().setMode('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    useTheme.getState().setMode('auto');
    expect(document.documentElement.hasAttribute('data-theme')).toBe(false);
  });

  it('remembers the choice in the shared settings without wiping the other settings', () => {
    localStorage.setItem(KEYS.settings, JSON.stringify({ auto: true, speak: true, notify: false, theme: 'auto' }));
    useTheme.getState().setMode('dark');
    expect(JSON.parse(localStorage.getItem(KEYS.settings) ?? '{}')).toEqual({ auto: true, speak: true, notify: false, theme: 'dark' });
    expect(readTheme()).toBe('dark');
  });

  it('copes with missing or damaged settings', () => {
    expect(readTheme()).toBe('auto');
    localStorage.setItem(KEYS.settings, '{not json');
    expect(readTheme()).toBe('auto');
    localStorage.setItem(KEYS.settings, '[1,2]');
    expect(readTheme()).toBe('auto');
    localStorage.setItem(KEYS.settings, JSON.stringify({ theme: 'purple' }));
    expect(readTheme()).toBe('auto');
  });

  it('toggle flips whatever is showing, and works from "auto" too', () => {
    applyTheme('light');
    useTheme.getState().toggle();
    expect(effectiveTheme()).toBe('dark');
    useTheme.getState().toggle();
    expect(effectiveTheme()).toBe('light');
    useTheme.getState().setMode('auto');
    const shown = effectiveTheme();
    useTheme.getState().toggle();
    expect(effectiveTheme()).not.toBe(shown);
  });
});
