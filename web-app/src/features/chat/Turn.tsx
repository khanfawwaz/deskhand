import { useEffect, useState, type ReactNode } from 'react';
import type { TaskView } from '../../api/types';
import { elapsedText, friendlyError, statusLabel, statusTone, stepCount } from '../../lib/chats';
import type { QA } from './chatStore';
import { ResultBox } from './ResultBox';
import { StepsBox } from './Steps';
import styles from './Thread.module.css';

/** Re-render once a second while `active` (for the "Working for 12s" timer). */
function useTick(active: boolean): void {
  const [, setN] = useState(0);
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setN((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
}

interface Props {
  task: TaskView;
  /** the newest turn of the chat: it shows the status line and takes the question card */
  live?: boolean;
  /** questions already answered in this turn */
  qa: readonly QA[];
  /** the question card, for the live turn */
  children?: ReactNode;
}

/** One message and what happened because of it: the goal bubble, status, steps, answered questions, the result or a friendly error. */
export function Turn({ task, live = false, qa, children }: Props) {
  useTick(live && !task.terminal);
  const steps = task.steps;
  return (
    <section
      className={styles.convo}
      data-done={task.terminal ? '1' : '0'}
      data-live={live ? '1' : '0'}
      aria-live={live ? 'polite' : undefined}
    >
      <div className={`${styles.bubble} ${styles.userBubble}`}>
        <p className={styles.goal}>{task.goal}</p>
      </div>
      <div className={styles.assistant}>
        {live && (
          <div className={styles.statusLine}>
            {!task.terminal && <span className={styles.spin} aria-hidden="true" />}
            <span className={styles.elapsed}>
              {(task.terminal ? 'Worked for ' : 'Working for ') + elapsedText(task.created_at, task.terminal ? task.updated_at : null)}
            </span>
            <span className={styles.chip} data-tone={statusTone(task.status)}>
              {statusLabel(task.status)}
            </span>
          </div>
        )}
        <StepsBox steps={steps} summary={task.terminal ? stepCount(steps?.length ?? 0) : 'Thinking…'} />
        {qa.map((p, i) => (
          <div key={i} className={styles.qa}>
            <p className={styles.q}>{p.q}</p>
            <div className={styles.bubble}>
              <p>{p.a}</p>
            </div>
          </div>
        ))}
        {children}
        {task.result ? <ResultBox text={task.result} /> : null}
        {task.error && task.terminal ? <p className={styles.err}>{friendlyError(task.error)}</p> : null}
      </div>
    </section>
  );
}
