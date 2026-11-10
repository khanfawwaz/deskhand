import { useMatch, useNavigate } from 'react-router-dom';
import { useTasks } from '../../api/chat';
import type { Me } from '../../api/types';
import { chatOf, groupChats, statusLabel, statusTone } from '../../lib/chats';
import { useUi } from '../../state/ui';
import { useChat } from './chatStore';
import { useDeleteChat } from './useDeleteChat';
import { useTaskFeed } from './useTaskFeed';
import styles from './Sidebar.module.css';

const MAX_RECENTS = 25;

const Plus = () => (
  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
    <path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
);
const Sparkle = () => (
  <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
    <path
      d="M12 3l1.8 4.6L18.5 9l-4.7 1.4L12 15l-1.8-4.6L5.5 9l4.7-1.4zM18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinejoin="round"
    />
  </svg>
);
const Trash = () => (
  <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">
    <path
      d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);
const Chevron = () => (
  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
    <path d="M7 10l5 5 5-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);
const Updown = () => (
  <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
    <path d="M8 9l4-4 4 4M8 15l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/**
 * The left column: workspace, New task, the person's computers, Recents (one row per chat, with a status dot and a
 * trash button) and the user row that opens Settings. It also keeps the live task feed running for the whole app.
 */
export function Sidebar({ me }: { me: Me }) {
  useTaskFeed();
  const navigate = useNavigate();
  const { data: tasks } = useTasks();
  const deleteChat = useDeleteChat();
  const open = useMatch('/chat/:id')?.params.id ?? null;
  const openChat = chatOf(tasks, open);
  const chats = groupChats(tasks).slice(0, MAX_RECENTS); // one row per chat, not per message
  const ws = me.workspace.name;
  const go = (to: string) => void navigate(to);

  return (
    <aside className={styles.sidebar} aria-label="Deskhand navigation">
      <button type="button" className={styles.ws} aria-label="Workspace and settings" onClick={() => useUi.getState().openSettings()}>
        <span className="logo" aria-hidden="true" />
        <span className={styles.wsName}>{ws || 'Deskhand'}</span>
        <Chevron />
      </button>

      <nav className={styles.nav}>
        <button
          type="button"
          className={styles.navItem}
          onClick={() => {
            go('/');
            useChat.getState().focusComposer();
          }}
        >
          <Plus />
          New task
        </button>
        <button type="button" className={styles.navItem} onClick={() => useUi.getState().openSettingsAt('skills')}>
          <Sparkle />
          What it has learned
        </button>
      </nav>

      <section className={styles.section} aria-labelledby="compLbl">
        <h3 id="compLbl">Computers</h3>
        <ul className={styles.computers}>
          {me.devices.map((d) => (
            <li key={d.device_id} title={d.online ? 'Online' : 'Offline'}>
              <span className={styles.dot} data-on={d.online ? '1' : '0'} />
              <span className={styles.nm}>{d.name}</span>
            </li>
          ))}
        </ul>
        <button type="button" className={`${styles.navItem} ${styles.small}`} onClick={() => useUi.getState().openConnect()}>
          Add a computer
        </button>
      </section>

      <section className={`${styles.section} ${styles.grow}`} aria-labelledby="recLbl">
        <h3 id="recLbl">Recents</h3>
        <ul className={styles.history}>
          {chats.map((c) => (
            <li key={c.thread} className={styles.chatRow}>
              <button
                type="button"
                className={styles.chatBtn}
                aria-current={openChat?.thread === c.thread ? 'true' : undefined}
                onClick={() => go(`/chat/${c.thread}`)}
              >
                <span className={styles.sdot} data-tone={statusTone(c.latest.status)} title={statusLabel(c.latest.status)} />
                <span className={styles.g}>{c.title}</span>
              </button>
              <button
                type="button"
                className={styles.rowDel}
                aria-label="Delete this chat"
                title="Delete this chat"
                onClick={() => void deleteChat(c.latest.id)}
              >
                <Trash />
              </button>
            </li>
          ))}
        </ul>
        {chats.length === 0 && <p className={`hint ${styles.empty}`}>Tasks you run will show up here.</p>}
      </section>

      <button type="button" className={styles.user} aria-label="Settings" onClick={() => useUi.getState().openSettings()}>
        <span className={styles.avatar} aria-hidden="true">
          {(me.name || 'D').trim().charAt(0).toUpperCase() || 'D'}
        </span>
        <span className={styles.userText}>
          <span className={styles.userName}>{me.name || 'Signed in'}</span>
          <span className={styles.userSub}>{ws ? ws + ' · settings' : 'Settings'}</span>
        </span>
        <Updown />
      </button>
    </aside>
  );
}
