import { useEffect, useState } from 'react';
import styles from './Thread.module.css';

/** What Deskhand found or did, as text, with a Copy button. */
export function ResultBox({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(t);
  }, [copied]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      /* blocked */
    }
  };
  return (
    <div className={styles.answerBox}>
      <pre className={styles.result}>{text}</pre>
      <div className={styles.resultActions}>
        <button className="link" type="button" onClick={() => void copy()}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
    </div>
  );
}
