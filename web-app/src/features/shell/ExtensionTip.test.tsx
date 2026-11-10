import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import type { DeviceView } from '../../api/types';
import { ExtensionTip, extensionNudge, TIP_KEY } from './ExtensionTip';

const pc = (extension: boolean, online = true): DeviceView => ({ device_id: 'd1', name: 'Home PC', online, last_seen: 1, extension });
const me = (devices: DeviceView[]) => ({ devices, extension_url: 'https://chromewebstore.google.com/detail/deskhand' });

describe('extensionNudge', () => {
  it('asks to install on an online computer without it, and says "lost" when it had it before', () => {
    expect(extensionNudge([pc(false)], { seen: [], hidden: [] })?.kind).toBe('install');
    expect(extensionNudge([pc(false)], { seen: ['d1'], hidden: [] })?.kind).toBe('lost');
    expect(extensionNudge([pc(true)], { seen: [], hidden: [] })).toBeNull();
    expect(extensionNudge([pc(false, false)], { seen: [], hidden: [] })).toBeNull(); // offline: nothing to say yet
    expect(extensionNudge([pc(false)], { seen: [], hidden: ['d1'] })).toBeNull();
  });
});

describe('ExtensionTip', () => {
  beforeEach(() => localStorage.clear());

  it('comes back when the extension disconnects, even after it was closed', async () => {
    const { rerender } = render(<ExtensionTip me={me([pc(false)])} />);
    expect(screen.getByRole('complementary', { name: 'Use your own Chrome' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Add to Chrome' })).toHaveAttribute('href', expect.stringMatching(/^https:/));
    await userEvent.click(screen.getByRole('button', { name: 'Maybe later' }));
    expect(screen.queryByRole('complementary')).toBeNull();

    rerender(<ExtensionTip me={me([pc(true)])} />); // installed and connected
    expect(screen.queryByRole('complementary')).toBeNull();
    rerender(<ExtensionTip me={me([pc(false)])} />); // ...then it dropped
    expect(screen.getByRole('complementary', { name: 'Chrome extension disconnected' })).toHaveTextContent(/Home PC/);
    expect(JSON.parse(localStorage.getItem(TIP_KEY) as string).seen).toEqual(['d1']);
  });

  it('remembers across visits that a computer had the extension', () => {
    localStorage.setItem(TIP_KEY, JSON.stringify({ seen: ['d1'], hidden: [] }));
    render(<ExtensionTip me={me([pc(false)])} />);
    expect(screen.getByRole('complementary', { name: 'Chrome extension disconnected' })).toBeInTheDocument();
  });

  it('survives junk in storage', () => {
    localStorage.setItem(TIP_KEY, '{not json');
    render(<ExtensionTip me={me([pc(false)])} />);
    expect(screen.getByRole('complementary', { name: 'Use your own Chrome' })).toBeInTheDocument();
  });
});
