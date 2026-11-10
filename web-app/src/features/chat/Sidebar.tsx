import type { Me } from '../../api/types';
import { useSession } from '../../state/session';
import styles from './Sidebar.module.css';

/**
 * FOUNDATION PLACEHOLDER. The Chat agent owns this folder: Recents grouped into chats, New task, delete chat.
 * The Account + Settings agent adds the Settings entry point (the user row at the bottom).
 */
export function Sidebar({ me }: { me: Me }) {
  const signOut = useSession((s) => s.signOut);
  return (
    <aside className={styles.sidebar} aria-label="Deskhand">
      <div className={styles.workspace}>
        <span className="logo" aria-hidden="true" />
        <strong>{me.workspace.name || 'Deskhand'}</strong>
      </div>

      <section className={styles.section} aria-labelledby="computersLabel">
        <h3 id="computersLabel">Computers</h3>
        {me.devices.length === 0 ? (
          <p className="hint">None connected yet.</p>
        ) : (
          <ul className={styles.list}>
            {me.devices.map((d) => (
              <li key={d.device_id} title={d.online ? 'Online' : 'Offline'}>
                <span className={styles.dot} data-on={d.online ? '1' : '0'} />
                {d.name}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={`${styles.section} ${styles.grow}`} aria-labelledby="recentsLabel">
        <h3 id="recentsLabel">Recents</h3>
        <p className="hint">Tasks you run will show up here.</p>
      </section>

      <div className={styles.user}>
        <span className={styles.avatar} aria-hidden="true">
          {(me.name || 'D').trim().charAt(0).toUpperCase() || 'D'}
        </span>
        <span className={styles.userText}>
          <span className={styles.userName}>{me.name || 'Signed in'}</span>
        </span>
        <button className="link" type="button" onClick={() => signOut()}>
          Sign out
        </button>
      </div>
    </aside>
  );
}
