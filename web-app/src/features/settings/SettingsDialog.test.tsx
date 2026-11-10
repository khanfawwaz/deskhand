import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useSession } from '../../state/session';
import { useTheme } from '../../state/theme';
import { useUi } from '../../state/ui';
import { server } from '../../test/server';
import { resetConnectAsked } from '../connect/ConnectDialog';
import { setInstallEventForTests } from './installPrompt';
import { DEVICE, makeMe, renderDialogs } from './testUtils';

const calls: string[] = [];
const record = (method: string, path: string) => calls.push(`${method} ${path}`);

/** The read routes every box needs; individual tests override what they care about. */
function backend(over: { skills?: unknown[]; prefs?: unknown[] } = {}) {
  server.use(
    http.get('*/v1/workspace', () => HttpResponse.json({ id: 'w', name: 'Alex home', max_tasks_per_day: 50, tasks_24h: 3 })),
    http.get('*/v1/members', () =>
      HttpResponse.json([
        { client_id: 'c1', name: 'This phone', last_seen: 1, you: true },
        { client_id: 'c2', name: 'Laptop browser', last_seen: 1, you: false },
      ]),
    ),
    http.get('*/v1/usage', () =>
      HttpResponse.json({ tasks_24h: 3, max_tasks_per_day: 50, model_calls_today: 1234, tokens_today: 5678, token_limit: 100000, queue: { waiting: 2, running: 1 } }),
    ),
    http.get('*/v1/preferences', () => HttpResponse.json(over.prefs ?? [{ id: 'p1', text: 'when I say calming music, play lofi', created_at: 1 }])),
    http.get('*/v1/skills', () => HttpResponse.json(over.skills ?? [{ id: 's1', title: 'YouTube', runs: { ok: 4 } }])),
  );
}

async function openSettings(me = makeMe()) {
  backend();
  const view = renderDialogs(me);
  await act(async () => {
    await new Promise((r) => setTimeout(r, 50)); // let /v1/me arrive
  });
  act(() => useUi.getState().openSettings());
  await screen.findByRole('heading', { name: 'Settings', hidden: true });
  return view;
}

beforeEach(() => {
  calls.length = 0;
  resetConnectAsked();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});
afterEach(() => {
  vi.restoreAllMocks();
  setInstallEventForTests(null);
});

describe('every box loads', () => {
  it('shows computers, extension status, devices, usage, saved preferences and learned skills', async () => {
    await openSettings(makeMe({ devices: [DEVICE, { ...DEVICE, device_id: 'd2', name: 'Den PC', online: false }] }));
    const computers = screen.getByRole('region', { name: 'Your computers' });
    expect(within(computers).getByText('Office PC')).toBeInTheDocument();
    expect(within(computers).getByText('online')).toBeInTheDocument();
    expect(within(computers).getByText('offline')).toBeInTheDocument();

    const ext = screen.getByRole('region', { name: 'Chrome extension' });
    expect(within(ext).getByText('Not set up')).toBeInTheDocument();
    expect(within(ext).getByText('computer offline')).toBeInTheDocument();
    expect(within(ext).getByText('Coming soon')).toBeInTheDocument();

    expect(await screen.findByText(/workspace: alex home \(3 of 50 tasks used/i)).toBeInTheDocument();
    expect(await screen.findByText('This phone (this device)')).toBeInTheDocument();
    const n = (x: number) => x.toLocaleString();
    await waitFor(() =>
      expect(screen.getByTestId('usage-line')).toHaveTextContent(
        `Tasks: 3 of 50 in the last 24 hours · Model calls: ${n(1234)} · Tokens: ${n(5678)} of ${n(100000)}. Busy right now: 2 waiting in line.`,
      ),
    );
    expect(await screen.findByText('when I say calming music, play lofi')).toBeInTheDocument();
    expect(await screen.findByText('YouTube')).toBeInTheDocument();
    expect(screen.getByText('4 worked')).toBeInTheDocument();
    expect(screen.getByText(/signed in as alex on this device/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /privacy policy/i })).toHaveAttribute('href', '/privacy');
    expect(screen.getByRole('link', { name: /terms/i })).toHaveAttribute('href', '/terms');
  });

  it('shows the store link in the extension box when the server has one', async () => {
    await openSettings(makeMe({ extension_url: 'https://chromewebstore.google.com/detail/x' }));
    const link = screen.getByRole('link', { name: /add to chrome/i });
    expect(link).toHaveAttribute('href', 'https://chromewebstore.google.com/detail/x');
    expect(screen.queryByText('Coming soon')).toBeNull();
  });

  it('says so when usage cannot be loaded and when nothing is saved yet', async () => {
    backend({ prefs: [], skills: [] });
    server.use(http.get('*/v1/usage', () => HttpResponse.json({ detail: 'x' }, { status: 500 })));
    renderDialogs();
    await act(async () => void (await new Promise((r) => setTimeout(r, 50))));
    act(() => useUi.getState().openSettings());
    expect(await screen.findByText('Usage is not available.')).toBeInTheDocument();
    expect(await screen.findByText('None yet.', { selector: 'p' })).toBeInTheDocument();
    expect(await screen.findByText(/nothing yet\. it learns as you use it/i)).toBeInTheDocument();
  });

  it('opens the skills box when asked for it', async () => {
    backend();
    renderDialogs();
    await act(async () => void (await new Promise((r) => setTimeout(r, 50))));
    act(() => useUi.getState().openSettingsAt('skills'));
    await screen.findByText('YouTube');
    expect(document.getElementById('set-skills')).toHaveAttribute('open');
  });
});

describe('computers and other devices', () => {
  it('removes a computer after confirming, and not when declined', async () => {
    const user = userEvent.setup();
    await openSettings(makeMe({ devices: [DEVICE, { ...DEVICE, device_id: 'd2', name: 'Den PC' }] }));
    server.use(http.get('*/v1/me', () => HttpResponse.json(makeMe({ devices: [{ ...DEVICE, device_id: 'd2', name: 'Den PC' }] }))), http.delete('*/v1/devices/d1', () => (record('DELETE', 'devices/d1'), HttpResponse.json({ revoked: 'd1' }))));
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false);
    await user.click(screen.getByRole('button', { name: 'Remove Office PC' }));
    expect(calls).toEqual([]);
    await user.click(screen.getByRole('button', { name: 'Remove Office PC' }));
    await waitFor(() => expect(calls).toEqual(['DELETE devices/d1']));
    expect(window.confirm).toHaveBeenLastCalledWith('Remove Office PC? Deskhand will stop controlling it.');
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Your computers' })).queryByText('Office PC')).toBeNull());
  });

  it('Connect a computer closes settings and opens the connect dialog', async () => {
    const user = userEvent.setup();
    server.use(http.post('*/v1/add-device', () => HttpResponse.json({ code: 'x', expires_in: 900, command: 'cmd' })));
    await openSettings();
    await user.click(screen.getByRole('button', { name: /connect a computer/i }));
    expect(useUi.getState().connectOpen).toBe(true);
    expect(useUi.getState().settingsOpen).toBe(false);
  });

  it('removes another device but never offers to remove this one, and makes a one-time code', async () => {
    const user = userEvent.setup();
    await openSettings();
    server.use(
      http.delete('*/v1/members/c2', () => (record('DELETE', 'members/c2'), HttpResponse.json({ revoked: 'c2' }))),
      http.post('*/v1/pair-code', () => HttpResponse.json({ code: 'ABCD-1234', expires_in: 600 })),
    );
    await screen.findByText('Laptop browser');
    expect(screen.queryByRole('button', { name: 'Remove This phone' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Remove Laptop browser' }));
    await waitFor(() => expect(calls).toEqual(['DELETE members/c2']));
    await user.click(screen.getByRole('button', { name: /get a code for another device/i }));
    expect(await screen.findByTestId('pair-code')).toHaveTextContent('ABCD-1234');
    expect(screen.getByText(/works once, for 10 minutes/i)).toBeInTheDocument();
  });
});

describe('appearance and this-device preferences', () => {
  it('the theme buttons go through the theme store and are remembered', async () => {
    const user = userEvent.setup();
    await openSettings();
    await user.click(screen.getByRole('button', { name: 'Dark' }));
    expect(useTheme.getState().mode).toBe('dark');
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark');
    expect(screen.getByRole('button', { name: 'Dark' })).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', { name: 'Match system' }));
    expect(document.documentElement).not.toHaveAttribute('data-theme');
    expect(JSON.parse(localStorage.getItem('pilot.settings') ?? '{}').theme).toBe('auto');
  });

  it('the checkboxes are stored under pilot.settings keys auto, speak and notify', async () => {
    const user = userEvent.setup();
    localStorage.setItem('pilot.settings', JSON.stringify({ theme: 'light', other: 1 }));
    await openSettings();
    const auto = screen.getByRole('checkbox', { name: /send voice commands automatically/i });
    const speak = screen.getByRole('checkbox', { name: /read results aloud/i });
    expect(auto).toBeChecked();
    expect(speak).not.toBeChecked();
    await user.click(auto);
    await user.click(speak);
    expect(JSON.parse(localStorage.getItem('pilot.settings') ?? '{}')).toMatchObject({ theme: 'light', other: 1, auto: false, speak: true });
  });

  it('asks the browser for notification permission and keeps the box ticked only when it is granted', async () => {
    const user = userEvent.setup();
    const request = vi.fn().mockResolvedValueOnce('denied').mockResolvedValueOnce('granted');
    vi.stubGlobal('Notification', Object.assign(function Notification() {}, { permission: 'default', requestPermission: request }));
    await openSettings();
    const box = screen.getByRole('checkbox', { name: /alert me when deskhand needs approval/i });
    await user.click(box);
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect(box).not.toBeChecked();
    await user.click(box);
    await waitFor(() => expect(box).toBeChecked());
    expect(JSON.parse(localStorage.getItem('pilot.settings') ?? '{}').notify).toBe(true);
    vi.unstubAllGlobals();
  });

  it('offers the home-screen install only on a touch device once the browser has offered it', async () => {
    const user = userEvent.setup();
    const prompt = vi.fn().mockResolvedValue(undefined);
    const offer = Object.assign(new Event('beforeinstallprompt'), { prompt });
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    await openSettings();
    act(() => setInstallEventForTests(offer));
    expect(screen.queryByRole('button', { name: /home screen/i })).toBeNull(); // a computer: no button

    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
    act(() => setInstallEventForTests(null));
    act(() => setInstallEventForTests(offer));
    await user.click(await screen.findByRole('button', { name: /add deskhand to this device's home screen/i }));
    expect(prompt).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: /home screen/i })).toBeNull();
    vi.unstubAllGlobals();
  });
});

describe('saved preferences and learned skills', () => {
  it('deletes a saved preference with DELETE /v1/preferences/{id}', async () => {
    const user = userEvent.setup();
    await openSettings();
    server.use(http.delete('*/v1/preferences/p1', () => (record('DELETE', 'preferences/p1'), HttpResponse.json({ forgotten: 'p1' }))));
    await user.click(await screen.findByRole('button', { name: /delete preference: when i say calming music/i }));
    await waitFor(() => expect(calls).toEqual(['DELETE preferences/p1']));
  });

  it('views the notes of a skill and forgets it with DELETE /v1/skills/{id}', async () => {
    const user = userEvent.setup();
    await openSettings();
    server.use(
      http.get('*/v1/skills/s1', () => HttpResponse.json({ id: 's1', title: 'YouTube', runs: { ok: 4 }, text: 'Press k to pause.' })),
      http.delete('*/v1/skills/s1', () => (record('DELETE', 'skills/s1'), HttpResponse.json({ forgotten: 's1' }))),
    );
    await user.click(await screen.findByRole('button', { name: 'View notes about YouTube' }));
    expect(await screen.findByText('Press k to pause.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'View notes about YouTube' }));
    expect(screen.queryByText('Press k to pause.')).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Forget YouTube' }));
    await waitFor(() => expect(calls).toEqual(['DELETE skills/s1']));
    expect(window.confirm).toHaveBeenCalledWith('Forget what Deskhand learned about YouTube?');
  });
});

describe('your data', () => {
  it('downloads the export with the auth header as a dated file', async () => {
    const user = userEvent.setup();
    await openSettings();
    let auth: string | null = null;
    server.use(
      http.get('*/v1/export', ({ request }) => {
        auth = request.headers.get('authorization');
        return HttpResponse.json({ tasks: [] });
      }),
    );
    const created = vi.fn(() => 'blob:x');
    URL.createObjectURL = created;
    URL.revokeObjectURL = vi.fn();
    let name = '';
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      name = this.download;
    });
    await user.click(screen.getByRole('button', { name: /download my data/i }));
    expect(await screen.findByText(/your data was downloaded as a file/i)).toBeInTheDocument();
    expect(auth).toBe('Bearer tok');
    expect(created).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalled();
    expect(name).toMatch(/^deskhand-data-\d{4}-\d{2}-\d{2}\.json$/);
  });

  it('says why a download failed', async () => {
    const user = userEvent.setup();
    await openSettings();
    server.use(http.get('*/v1/export', () => HttpResponse.json({ detail: 'Export is busy.' }, { status: 429 })));
    await user.click(screen.getByRole('button', { name: /download my data/i }));
    expect(await screen.findByText('Export is busy.')).toBeInTheDocument();
  });

  it('deletes all chats with DELETE /v1/chats after confirming', async () => {
    const user = userEvent.setup();
    await openSettings();
    server.use(http.delete('*/v1/chats', () => (record('DELETE', 'chats'), HttpResponse.json({ deleted: 3, chats: 2 }))));
    await user.click(screen.getByRole('button', { name: /delete all chats/i }));
    await waitFor(() => expect(calls).toEqual(['DELETE chats']));
    expect(await screen.findByText(/deleted 3 tasks from your chats/i)).toBeInTheDocument();
  });

  describe('delete my account', () => {
    it('blocks a wrong phrase, accepts the right one in any case and spacing, then signs out', async () => {
      const user = userEvent.setup();
      let body: unknown;
      server.use(http.post('*/v1/delete-account', async ({ request }) => ((body = await request.json()), HttpResponse.json({ deleted: true }))));
      await openSettings();
      await user.click(screen.getByRole('button', { name: /delete my account and data/i }));
      const input = await screen.findByLabelText(/type delete my data to confirm/i);
      const go = screen.getByRole('button', { name: /delete everything/i });
      expect(go).toBeDisabled();
      await user.type(input, 'delete my dat');
      expect(go).toBeDisabled();
      await user.click(go);
      expect(body).toBeUndefined();
      await user.type(input, 'a');
      await user.clear(input);
      await user.type(input, '  Delete   MY data ');
      expect(go).toBeEnabled();
      await user.click(go);
      await waitFor(() => expect(body).toEqual({ confirm: 'delete my data' }));
      await waitFor(() => expect(useSession.getState().token).toBeNull());
      expect(useSession.getState().notice).toMatch(/your account and data were deleted/i);
      expect(useUi.getState().settingsOpen).toBe(false);
    });

    it('shows the server message and stays signed in when the delete fails; Cancel closes it', async () => {
      const user = userEvent.setup();
      server.use(http.post('*/v1/delete-account', () => HttpResponse.json({ detail: 'Try again later.' }, { status: 500 })));
      await openSettings();
      await user.click(screen.getByRole('button', { name: /delete my account and data/i }));
      await user.type(await screen.findByLabelText(/type delete my data/i), 'delete my data');
      await user.click(screen.getByRole('button', { name: /delete everything/i }));
      expect(await screen.findByText('Try again later.')).toBeInTheDocument();
      expect(useSession.getState().token).toBe('tok');
      await user.click(screen.getByRole('button', { name: /^cancel$/i }));
      await waitFor(() => expect(screen.queryByLabelText(/type delete my data/i)).toBeNull());
    });
  });
});

describe('account and safety', () => {
  it('emergency stop asks first, then calls /v1/estop', async () => {
    const user = userEvent.setup();
    await openSettings();
    server.use(http.post('*/v1/estop', () => (record('POST', 'estop'), HttpResponse.json({ stopped: true }))));
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false);
    await user.click(screen.getByRole('button', { name: /emergency stop/i }));
    expect(calls).toEqual([]);
    await user.click(screen.getByRole('button', { name: /emergency stop/i }));
    await waitFor(() => expect(calls).toEqual(['POST estop']));
    expect(window.confirm).toHaveBeenLastCalledWith('Stop every running task right now?');
    expect(await screen.findByText(/emergency stop sent/i)).toBeInTheDocument();
  });

  it('sign out tells the server and forgets the token', async () => {
    const user = userEvent.setup();
    await openSettings();
    server.use(http.post('*/v1/unpair', () => (record('POST', 'unpair'), HttpResponse.json({ unpaired: true }))));
    await user.click(screen.getByRole('button', { name: /sign out of this device/i }));
    await waitFor(() => expect(calls).toEqual(['POST unpair']));
    await waitFor(() => expect(useSession.getState().token).toBeNull());
  });

  it('Done closes the dialog', async () => {
    const user = userEvent.setup();
    await openSettings();
    await user.click(screen.getByRole('button', { name: /^done$/i }));
    expect(useUi.getState().settingsOpen).toBe(false);
  });
});
