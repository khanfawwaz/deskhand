import { useQueryClient } from '@tanstack/react-query';
import { Outlet } from 'react-router-dom';
import { queryKeys, useMe } from '../../api/queries';
import type { DeviceView, Me } from '../../api/types';
import { useEventStream } from '../../hooks/useEventStream';
import type { StreamStatus } from '../../hooks/useEventStream';
import { useSession } from '../../state/session';
import { Sidebar } from '../chat/Sidebar';
import { ConnectDialog } from '../connect/ConnectDialog';
import { SettingsDialog } from '../settings/SettingsDialog';
import { SignInPage } from '../auth/SignInPage';
import styles from './AppLayout.module.css';

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
  const context: ShellContext = { me: me.data, stream };
  return (
    <div className={styles.frame}>
      <p className={styles.preview} role="note">
        Preview of the new Deskhand app. The full app is still at <a href="/app/">/app/</a>.
      </p>
      <div className={styles.shell}>
        <Sidebar me={me.data} />
        <main className={styles.main}>
          <Outlet context={context} />
        </main>
      </div>
      {/* app-wide dialogs: each opens itself from the flags in state/ui.ts */}
      <ConnectDialog />
      <SettingsDialog />
    </div>
  );
}
