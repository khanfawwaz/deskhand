import type { ReactNode } from 'react';
import styles from './Banner.module.css';

interface Props {
  title: string;
  children?: ReactNode;
  /** a button or link shown on the right */
  action?: ReactNode;
  onDismiss: () => void;
}

/** A small tip with a close button. The caller decides when it shows and remembers when it was closed. */
export function Banner({ title, children, action, onDismiss }: Props) {
  return (
    <aside className={styles.banner} aria-label={title}>
      <div className={styles.text}>
        <b>{title}</b>
        {children ? <span>{children}</span> : null}
      </div>
      {action}
      <button className="icon-btn" type="button" aria-label="Dismiss" onClick={onDismiss}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
      </button>
    </aside>
  );
}

/** The right-hand label of a banner: a pill (dashed when it is only a label, solid when it is a link). */
export const bannerPill = styles.pill;
export const bannerPillLink = styles.pillLink;
