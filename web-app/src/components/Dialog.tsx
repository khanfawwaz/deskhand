import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import styles from './Dialog.module.css';

interface Props {
  open: boolean;
  /** Called when the dialog closes for any reason (a button, the Escape key, a click on the dim area). */
  onClose: () => void;
  /** id of the heading that names the dialog */
  labelledBy: string;
  children: ReactNode;
}

/**
 * A general dialog: a native <dialog> shown as a modal, so the browser traps focus, dims the page and closes on Escape.
 * A click on the dimmed area closes it too. Its contents exist only while it is open, so each opening starts fresh.
 */
export function Dialog({ open, onClose, labelledBy, children }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    const isOpen = d.hasAttribute('open');
    if (open && !isOpen) {
      if (typeof d.showModal === 'function') d.showModal();
      else d.setAttribute('open', '');
    } else if (!open && isOpen) {
      if (typeof d.close === 'function') d.close();
      else d.removeAttribute('open');
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={styles.dialog}
      aria-labelledby={labelledBy}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose(); // the click landed on the backdrop, not on the content
      }}
    >
      {open ? children : null}
    </dialog>
  );
}
