import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import styles from './dialog.module.css';

interface Props {
  open: boolean;
  /** Called when the dialog closes for any reason (a button, the Escape key). */
  onClose: () => void;
  /** id of the heading that names the dialog */
  labelledBy: string;
  children: ReactNode;
}

/**
 * A native <dialog> shown as a modal: the browser traps focus, dims the page and closes on Escape.
 * Its contents exist only while it is open, so each opening starts fresh and nothing loads in the background.
 */
export function Modal({ open, onClose, labelledBy, children }: Props) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    const isOpen = d.hasAttribute('open');
    if (open && !isOpen) {
      if (typeof d.showModal === 'function') d.showModal();
      else d.setAttribute('open', ''); // a browser (or test DOM) without modal dialogs: still shown, just not modal
      d.scrollTop = 0; // the browser focuses a button further down: start at the top anyway
    } else if (!open && isOpen) {
      if (typeof d.close === 'function') d.close();
      else d.removeAttribute('open');
    }
  }, [open]);

  return (
    <dialog ref={ref} className={styles.dialog} aria-labelledby={labelledBy} onClose={onClose}>
      {open ? children : null}
    </dialog>
  );
}
