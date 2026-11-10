import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { queryKeys, useMe } from '../../api/queries';
import type { DeviceView, Me, TaskView } from '../../api/types';
import { Toaster } from '../../components/Toaster';
import { useEventStream } from '../../hooks/useEventStream';
import type { StreamStatus } from '../../hooks/useEventStream';
import { groupChats } from '../../lib/chats';
import { useSession } from '../../state/session';
import { useTheme } from '../../state/theme';
import { useUi } from '../../state/ui';
import { SignInPage } from '../auth/SignInPage';
import { Sidebar } from '../chat/Sidebar';
import { ConnectDialog } from '../connect/ConnectDialog';
import { CommandPalette, useHotkey } from '../delight/CommandPalette';
import { SettingsDialog } from '../settings/SettingsDialog';
import styles from './AppLayout.module.css';
import { ExtensionTip } from './ExtensionTip';
import { buildPaletteActions } from './paletteActions';

/** What every screen inside the app can read from the layout. */
export interface ShellContext {
  me: Me;
  stream: StreamStatus;
}

/** The gate and the frame: no token -> sign in; token -> who am I, then sidebar + the chosen screen. */
export function AppLayout() {
  const token = useSession((s) => s.token);
  return token ? <SignedIn /> : <SignInPage />;
}

function SignedIn() {
  const me = useMe();
  const qc = useQueryClient();
  const signOut = useSession((s) => s.signOut);

  // the live feed keeps the list of computers current; task events are handled by the chat feature
  const stream = useEventStream({
    onEvent: (e) => {
      if (e.event === 'devices' && Array.isArray(e.data)) {
        qc.setQueryData<Me>(queryKeys.me, (old) => (old ? { ...old, devices: e.data as DeviceView[] } : old));
      }
    },
    enabled: me.isSuccess,
  });

  if (me.isPending) return <p className={styles.splash}>Loading Deskhand...</p>;
  if (me.isError) {
    return (
      <div className={styles.splash}>
        <p>{me.error.message}</p>
        <button className="ghost" type="button" onClick={() => void me.refetch()}>
          Try again
        </button>{' '}
        <button className="link" type="button" onClick={() => signOut()}>
          Sign out
        </button>
      </div>
    );
  }
  return <Frame me={me.data} stream={stream} />;
}

/** The signed-in frame: preview note, phone top bar, sidebar (a drawer on phones), the screen, dialogs, palette, toasts. */
function Frame({ me, stream }: { me: Me; stream: StreamStatus }) {
  const context: ShellContext = { me, stream };
  const { pathname } = useLocation();
  const drawerOpen = useUi((s) => s.drawerOpen);
  const closeDrawer = useUi((s) => s.closeDrawer);
  const menuRef = useRef<HTMLButtonElement>(null);
  const sideRef = useRef<HTMLDivElement>(null);

  // choosing something in the sidebar (a new address) puts the drawer away
  useEffect(() => {
    useUi.getState().closeDrawer();
  }, [pathname]);

  // open: Escape closes it and focus goes to the first thing inside; closed again: focus goes back to the menu button
  useEffect(() => {
    if (!drawerOpen) return;
    const menu = menuRef.current;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') useUi.getState().closeDrawer();
    };
    window.addEventListener('keydown', onKey);
    sideRef.current?.querySelector<HTMLElement>('a[href], button:not([disabled]), input, [tabindex="0"]')?.focus();
    return () => {
      window.removeEventListener('keydown', onKey);
      if (menu && menu.offsetParent !== null) menu.focus();
    };
  }, [drawerOpen]);

  return (
    <div className={styles.frame}>
      <p className={styles.preview} role="note">
        Preview of the new Deskhand app. The full app is still at <a href="/app/">/app/</a>.
      </p>
      <div className={styles.topbar}>
        <button
          ref={menuRef}
          className="icon-btn"
          type="button"
          aria-label="Open the menu"
          aria-expanded={drawerOpen}
          aria-controls="side-drawer"
          onClick={() => useUi.getState().openDrawer()}
        >
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
            <path d="M4 7h16M4 12h16M4 17h16" />
          </svg>
        </button>
        <span className="logo" aria-hidden="true" />
        <strong>Deskhand</strong>
      </div>
      <div className={styles.shell}>
        <div ref={sideRef} id="side-drawer" className={styles.side} data-open={drawerOpen ? '1' : '0'}>
          <Sidebar me={me} />
        </div>
        {drawerOpen ? <div className={styles.scrim} onClick={closeDrawer} aria-hidden="true" data-testid="drawer-scrim" /> : null}
        <main className={styles.main}>
          <Outlet context={context} />
        </main>
      </div>
      {/* app-wide dialogs: each opens itself from the flags in state/ui.ts */}
      <ExtensionTip me={me} />
      <ConnectDialog />
      <SettingsDialog />
      <PaletteHost />
      <Toaster />
    </div>
  );
}

/** Ctrl+K / Cmd+K opens the command palette from anywhere in the signed-in app. */
function PaletteHost() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const qc = useQueryClient();
  // only READS the chat list the chat feature has loaded (it owns the query); this adds no request of its own
  const tasks = useSyncExternalStore(
    (notify) => qc.getQueryCache().subscribe(notify),
    () => qc.getQueryData<TaskView[]>(['tasks']),
  );
  const chats = groupChats(tasks);

  useHotkey('mod+k', () => {
    if (document.querySelector('dialog[open]')) return; // a dialog is open: the shortcut stays out of its way
    setOpen((o) => !o);
  });

  const actions = buildPaletteActions({
    chats,
    newTask: () => void navigate('/'),
    openChat: (thread) => void navigate(`/chat/${encodeURIComponent(thread)}`),
    openSettings: () => useUi.getState().openSettings(),
    openConnect: () => useUi.getState().openConnect(),
    openPage: (path) => void navigate(path),
    setTheme: (mode) => useTheme.getState().setMode(mode),
  });
  return <CommandPalette open={open} onClose={() => setOpen(false)} actions={actions} />;
}
