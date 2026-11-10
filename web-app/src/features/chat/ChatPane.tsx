import { useOutletContext, useParams } from 'react-router-dom';
import type { ShellContext } from '../shell/AppLayout';
import styles from './ChatPane.module.css';

/**
 * FOUNDATION PLACEHOLDER. The Chat agent owns this folder: the conversation, the composer (Stop replaces Send),
 * question cards, continue-in-the-same-chat, delete chat. The route /chat/:id already hands over the chat id.
 */
export function ChatPane() {
  const { id } = useParams();
  const { stream } = useOutletContext<ShellContext>();
  return (
    <>
      <header className={styles.top}>
        <h2 className={styles.title}>{id ? `Chat ${id}` : 'New task'}</h2>
        <span className={styles.conn} data-state={stream} role="status" aria-live="polite">
          {stream === 'online' ? 'online' : stream === 'connecting' ? 'connecting' : 'offline'}
        </span>
      </header>
      <section className={styles.welcome}>
        <h2>What should I do?</h2>
        <p className="hint">Tell Deskhand a task in your own words. It works on your computer and asks you when it needs to.</p>
      </section>
    </>
  );
}
