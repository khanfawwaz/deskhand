import styles from './StatusDot.module.css';

interface Props {
  on: boolean;
  /** words for screen readers; the dot alone says nothing to them */
  label?: string;
}

/** A small round light: green when something is online, grey when it is not. */
export function StatusDot({ on, label }: Props) {
  return label ? (
    <span className={styles.dot} data-on={on ? '1' : '0'} role="img" aria-label={label} />
  ) : (
    <span className={styles.dot} data-on={on ? '1' : '0'} aria-hidden="true" />
  );
}
