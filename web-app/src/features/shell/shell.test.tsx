import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSession } from '../../state/session';
import { useTheme } from '../../state/theme';
import { useUi } from '../../state/ui';
import { openStream, renderApp } from '../../test/render';
import { server } from '../../test/server';
import { TIP_KEY } from './ExtensionTip';
import { buildPaletteActions } from './paletteActions';

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
});
beforeEach(() => useUi.setState({ drawerOpen: false, settingsOpen: false, connectOpen: false }));

const ME = {
  client_id: 'c1',
  name: 'Alex',
  workspace: { id: 'main', name: 'Alex Home' },
  devices: [{ device_id: 'd1', name: 'Alex-Laptop', online: true, last_seen: 1, extension: false }],
  extension_url: '',
};

function signedIn(me: object = ME) {
  useSession.setState({ token: 'tok' });
  server.use(
    http.get('*/v1/me', () => HttpResponse.json(me)),
    http.get('*/v1/events', () => openStream().response), // a fresh stream for every connection
    http.get('*/v1/tasks', () => HttpResponse.json([])),
  );
}

describe('buildPaletteActions', () => {
  const noop = () => undefined;
  const deps = { chats: [{ thread: 't1', title: 'Find flights' }], newTask: noop, openChat: vi.fn(), openSettings: noop, openConnect: noop, openPage: vi.fn(), setTheme: vi.fn() };
  it('offers the asked actions, one per recent chat, and nothing that deletes', () => {
    const labels = buildPaletteActions(deps).map((a) => a.label);
    expect(labels).toEqual(
      expect.arrayContaining(['New task', 'Open settings', 'Connect a computer', 'Switch to light theme', 'Switch to dark theme', 'Switch to system theme', 'Find flights', 'Open privacy policy', 'Open terms of use']),
    );
    expect(labels.join(' ')).not.toMatch(/delete/i);
  });
  it('runs the right thing', () => {
    const a = buildPaletteActions(deps);
    a.find((x) => x.id === 'chat-t1')!.run();
    a.find((x) => x.id === 'theme-dark')!.run();
    a.find((x) => x.id === 'terms')!.run();
    expect(deps.openChat).toHaveBeenCalledWith('t1');
    expect(deps.setTheme).toHaveBeenCalledWith('dark');
    expect(deps.openPage).toHaveBeenCalledWith('/terms');
  });
});

describe('the command palette in the app', () => {
  it('opens with Ctrl+K, filters, runs an action with Enter and closes with Escape', async () => {
    signedIn();
    renderApp('/');
    await screen.findByRole('heading', { name: /what should i do/i });
    expect(screen.queryByRole('combobox')).toBeNull();
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    const box = await screen.findByRole('combobox');
    await userEvent.setup().type(box, 'dark{Enter}');
    expect(useTheme.getState().mode).toBe('dark');
    expect(screen.queryByRole('combobox')).toBeNull();
    fireEvent.keyDown(window, { key: 'k', metaKey: true });
    await screen.findByRole('combobox');
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' });
    expect(screen.queryByRole('combobox')).toBeNull();
  });

  it('goes to the privacy page from the palette', async () => {
    signedIn();
    const { router } = renderApp('/');
    await screen.findByRole('heading', { name: /what should i do/i });
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    await userEvent.setup().type(await screen.findByRole('combobox'), 'privacy{Enter}');
    await waitFor(() => expect(router.state.location.pathname).toBe('/privacy'));
  });
});

describe('the Chrome tip', () => {
  const tipName = { name: /use your own chrome/i };

  it('shows when a computer is online without the extension, and stays dismissed', async () => {
    signedIn();
    const { unmount } = renderApp('/');
    const tip = await screen.findByRole('complementary', tipName);
    expect(within(tip).getByText(/coming soon/i)).toBeInTheDocument();
    await userEvent.setup().click(within(tip).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('complementary', tipName)).toBeNull();
    expect(JSON.parse(localStorage.getItem(TIP_KEY) as string).hidden).toEqual(['d1']);
    unmount();
    renderApp('/');
    await screen.findByRole('heading', { name: /what should i do/i });
    expect(screen.queryByRole('complementary', tipName)).toBeNull();
  });

  it('has a link once the extension is published', async () => {
    signedIn({ ...ME, extension_url: 'https://chromewebstore.example/deskhand' });
    renderApp('/');
    expect(await screen.findByRole('link', { name: 'Add to Chrome' })).toHaveAttribute('href', 'https://chromewebstore.example/deskhand');
  });

  it('is not shown when the extension is connected', async () => {
    signedIn({ ...ME, devices: [{ ...ME.devices[0]!, extension: true }] });
    renderApp('/');
    await screen.findByRole('heading', { name: /what should i do/i });
    expect(screen.queryByRole('complementary', tipName)).toBeNull();
  });

  it('still shows when the browser refuses storage', async () => {
    signedIn();
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    renderApp('/');
    expect(await screen.findByRole('complementary', tipName)).toBeInTheDocument();
    vi.restoreAllMocks();
  });
});

describe('the phone drawer', () => {
  it('opens from the menu button, closes with Escape and with the scrim', async () => {
    signedIn();
    renderApp('/');
    const menu = await screen.findByRole('button', { name: /open the menu/i });
    expect(menu).toHaveAttribute('aria-expanded', 'false');
    await userEvent.setup().click(menu);
    expect(menu).toHaveAttribute('aria-expanded', 'true');
    expect(useUi.getState().drawerOpen).toBe(true);
    fireEvent.keyDown(window, { key: 'Escape' });
    await waitFor(() => expect(useUi.getState().drawerOpen).toBe(false));
    act(() => useUi.getState().openDrawer());
    await userEvent.setup().click(await screen.findByTestId('drawer-scrim'));
    expect(useUi.getState().drawerOpen).toBe(false);
  });

  it('puts itself away when the address changes', async () => {
    signedIn();
    const { router } = renderApp('/');
    await screen.findByRole('button', { name: /open the menu/i });
    act(() => useUi.getState().openDrawer());
    await act(async () => void (await router.navigate('/chat/abc')));
    expect(useUi.getState().drawerOpen).toBe(false);
  });
});
