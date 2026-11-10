import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { queryKeys } from '../../api/queries';
import type { Me } from '../../api/types';
import { useUi } from '../../state/ui';
import { server } from '../../test/server';
import { DEVICE, makeMe, renderDialogs } from '../settings/testUtils';
import { ARRIVED_PAUSE_MS, CONNECT_LIFE_MS, resetConnectAsked } from './ConnectDialog';

let issued = 0;
function commandRoute() {
  server.use(
    http.post('*/v1/add-device', () => {
      issued += 1;
      return HttpResponse.json({ code: `C${issued}`, expires_in: 900, command: `iwr https://x/install/${issued} | iex` });
    }),
  );
}

beforeEach(() => {
  issued = 0;
  resetConnectAsked();
  commandRoute();
});
afterEach(() => {
  vi.useRealTimers();
});

const settle = () => act(async () => void (await new Promise((r) => setTimeout(r, 60))));
const title = (name: RegExp) => screen.findByRole('heading', { name, hidden: true });

describe('when the dialog opens', () => {
  it('opens by itself when signed in with no computers, and offers itself only once per visit', async () => {
    const user = userEvent.setup();
    renderDialogs(makeMe({ devices: [] }));
    expect(await title(/connect your computer/i)).toBeInTheDocument();
    expect(useUi.getState().connectOpen).toBe(true);
    await user.click(screen.getByRole('button', { name: /not now/i }));
    expect(useUi.getState().connectOpen).toBe(false);
    // a refresh of the same (empty) list must not bring it back
    await act(async () => {
      await new Promise((r) => setTimeout(r, 30));
    });
    expect(useUi.getState().connectOpen).toBe(false);
  });

  it('stays closed when there is already a computer', async () => {
    renderDialogs(makeMe());
    await settle();
    expect(useUi.getState().connectOpen).toBe(false);
    expect(issued).toBe(0);
  });

  it('opens from the Connect a computer button state flag and shows three numbered steps with the command', async () => {
    renderDialogs(makeMe());
    await settle();
    act(() => useUi.getState().openConnect());
    expect(await screen.findByText('iwr https://x/install/1 | iex')).toBeInTheDocument();
    const steps = screen.getAllByRole('listitem').filter((li) => /^[123]/.test(li.textContent ?? ''));
    expect(steps).toHaveLength(3);
    expect(screen.getByText(/copy your install command/i)).toBeInTheDocument();
    expect(screen.getByText(/open powershell on that computer/i)).toBeInTheDocument();
    expect(screen.getByText(/paste it and press enter/i)).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(/waiting for your computer/i);
  });

  it('copies the command and can fetch a new one', async () => {
    const user = userEvent.setup();
    renderDialogs(makeMe({ devices: [] }));
    await screen.findByText('iwr https://x/install/1 | iex');
    await user.click(screen.getByRole('button', { name: /copy command/i }));
    expect(await screen.findByRole('button', { name: /copied\. now paste it into powershell/i })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /get a new command/i }));
    expect(await screen.findByText('iwr https://x/install/2 | iex')).toBeInTheDocument();
  });

  it('shows why a command could not be fetched', async () => {
    server.use(http.post('*/v1/add-device', () => HttpResponse.json({ detail: 'Too many computers.' }, { status: 409 })));
    renderDialogs(makeMe({ devices: [] }));
    expect(await screen.findByText(/could not get a command: too many computers/i)).toBeInTheDocument();
  });
});

describe('the command expires after 15 minutes', () => {
  it('is refreshed a minute early', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'setInterval', 'clearTimeout', 'clearInterval'], shouldAdvanceTime: true });
    renderDialogs(makeMe({ devices: [] }));
    await screen.findByText('iwr https://x/install/1 | iex');
    expect(CONNECT_LIFE_MS).toBe(14 * 60 * 1000);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CONNECT_LIFE_MS - 5_000);
    });
    expect(issued).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6_000);
    });
    expect(await screen.findByText('iwr https://x/install/2 | iex')).toBeInTheDocument();
  });
});

/** What the live feed does: the server's list changes and the cached copy follows (a later refetch agrees). */
function pushDevices(client: ReturnType<typeof renderDialogs>['client'], devices: Me['devices']) {
  const next = { ...(client.getQueryData<Me>(queryKeys.me) ?? makeMe()), devices };
  server.use(http.get('*/v1/me', () => HttpResponse.json(next)));
  act(() => void client.setQueryData<Me>(queryKeys.me, next));
}

describe('a computer arriving', () => {
  async function arrive(client: ReturnType<typeof renderDialogs>['client'], over: Partial<typeof DEVICE> = {}) {
    await screen.findByText('iwr https://x/install/1 | iex');
    pushDevices(client, [{ ...DEVICE, ...over }]);
  }

  it('says it is connected, then moves on to the Chrome step', async () => {
    const { client } = renderDialogs(makeMe({ devices: [], extension_url: '' }));
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });
    await arrive(client);
    expect(await screen.findByText(/office pc is connected/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /get a new command/i })).toBeDisabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARRIVED_PAUSE_MS + 50);
    });
    expect(await title(/use your own chrome/i)).toBeInTheDocument();
    expect(screen.getByText(/install the deskhand chrome extension so deskhand can work in the chrome you already use/i)).toBeInTheDocument();
  });

  it('shows Coming soon while the extension is not published', async () => {
    const { client } = renderDialogs(makeMe({ devices: [], extension_url: '' }));
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });
    await arrive(client);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARRIVED_PAUSE_MS + 50);
    });
    const dlg = (await title(/use your own chrome/i)).closest('dialog')!;
    expect(within(dlg).getByRole('status')).toHaveTextContent(/coming soon/i);
    expect(within(dlg).queryByRole('link', { name: /add to chrome/i })).toBeNull();
  });

  it('links to the store when the server has an extension address, and ticks when the extension connects', async () => {
    const url = 'https://chromewebstore.google.com/detail/deskhand/abc';
    const { client } = renderDialogs(makeMe({ devices: [], extension_url: url }));
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });
    await arrive(client);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARRIVED_PAUSE_MS + 50);
    });
    const link = await screen.findByRole('link', { name: /add to chrome/i, hidden: true });
    expect(link).toHaveAttribute('href', url);
    expect(link).toHaveAttribute('target', '_blank');
    expect(screen.getByText(/waiting for the extension to connect/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /skip for now/i })).toBeInTheDocument();

    pushDevices(client, [{ ...DEVICE, extension: true }]);
    expect(await screen.findByText(/chrome extension connected/i)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /add to chrome/i, hidden: true })).toBeNull();
    expect(screen.queryByRole('button', { name: /skip for now/i })).toBeNull();
    act(() => void screen.getByRole('button', { name: /^done$/i }).click());
    expect(useUi.getState().connectOpen).toBe(false);
  });

  it('never links to a non-https address', async () => {
    const { client } = renderDialogs(makeMe({ devices: [], extension_url: 'javascript:alert(1)' }));
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'], shouldAdvanceTime: true });
    await arrive(client);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ARRIVED_PAUSE_MS + 50);
    });
    expect(await screen.findByText(/coming soon/i)).toBeInTheDocument();
  });
});
