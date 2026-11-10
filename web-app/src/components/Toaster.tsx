import { useToasts } from './toast';
import styles from './Toaster.module.css';

/** Mount once. Screen readers hear each message politely (role=status); a click dismisses it. */
export function Toaster() {
  const items = useToasts((s) => s.items);
  const dismiss = useToasts((s) => s.dismiss);
  return (
    <div className={styles.stack} aria-live="polite">
      {items.map((t) => (
        <button key={t.id} type="button" className={styles.toast} onClick={() => dismiss(t.id)} title="Dismiss">
          {t.message}
        </button>
      ))}
    </div>
  );
}
